"""Mask and segmentation export (FR 048, AC 006).

Masks are written as indexed PNG files with an explicit class map, and as COCO
compatible segmentation where the geometry supports it.

Round trip guarantee
--------------------
Acceptance criterion AC 006 requires a mask export to round trip without class
loss or coordinate displacement. Two design choices deliver that:

* the PNG is written as a palette image whose index is the class index, so a
  reader recovers the exact class number rather than inferring it from colour,
  which would break as soon as two classes were given similar colours,
* rasterisation is deterministic and lives in :mod:`aria.core.geometry`, so the
  same vertices always produce the same pixels on any machine.

:func:`verify_round_trip` performs the check, and the test suite runs it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ...version import version_block
from ...core.geometry import (
    box_to_polygon,
    cortical_band_mask,
    polygon_area,
    rasterize_polygon,
    rasterize_polyline,
)
from ...core.schema import GeometryType, Side, get_class
from ...io.fsutil import atomic_write_text

#: Index zero is background in every mask ARIA writes.
BACKGROUND_INDEX = 0

#: Stroke width used when a polyline is rasterised into a mask.
POLYLINE_STROKE_PX = 3.0


def _hex_to_rgb(colour: str) -> tuple:
    c = colour.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def build_class_index(schema=None) -> dict:
    """Assign a stable integer to every class and side combination.

    Stability matters: an index written last month must mean the same thing
    today. The order comes from the schema declaration order, which is fixed in
    source, not from a set or a dictionary iteration.
    """
    from ...core.schema import LABEL_CLASSES

    index: dict = {}
    next_value = 1
    for cls in LABEL_CLASSES:
        if cls.geometry in (
            GeometryType.POINT,
        ):
            continue
        sides = (Side.RIGHT, Side.LEFT) if cls.side_scoped else (Side.MIDLINE,)
        for side in sides:
            index[(cls.key, side.value)] = next_value
            next_value += 1
    return index


def class_map_document(class_index: dict) -> dict:
    """The class map written beside every mask."""
    entries = []
    for (class_key, side), value in sorted(class_index.items(), key=lambda kv: kv[1]):
        try:
            cls = get_class(class_key)
            entries.append(
                {
                    "index": value,
                    "class_key": class_key,
                    "class_display": cls.display_name,
                    "short_code": cls.short_code,
                    "side": side,
                    "side_display": Side(side).display,
                    "colour": cls.colour,
                    "geometry": cls.geometry.value,
                }
            )
        except KeyError:
            continue
    return {
        "background_index": BACKGROUND_INDEX,
        "n_classes": len(entries),
        "encoding": "indexed PNG, palette index equals class index",
        "polyline_stroke_px": POLYLINE_STROKE_PX,
        "versions": version_block(),
        "classes": entries,
        "note": (
            "The palette index carries the class. Colours are for viewing only "
            "and must not be used to recover the class."
        ),
    }


def annotation_polygon(annotation) -> list | None:
    """The polygon that represents an annotation, or None when it has none."""
    pts = annotation.points()
    gt = GeometryType(annotation.geometry_type)
    if gt in (GeometryType.BOX, GeometryType.ROI_RECT) and len(pts) >= 2:
        (x0, y0), (x1, y1) = pts[0], pts[-1]
        return box_to_polygon(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
    if gt in (GeometryType.POLYGON, GeometryType.MASK) and len(pts) >= 3:
        return pts
    return None


def rasterise_case(data, shape: tuple, class_index: dict | None = None) -> tuple:
    """Rasterise every maskable annotation into one indexed label image.

    Where two classes overlap, the one with the higher class index wins, and the
    overlap is reported so a reader knows a pixel was contested rather than
    discovering it silently.
    """
    class_index = class_index or build_class_index()
    rows, cols = shape
    label = np.zeros((rows, cols), dtype=np.uint8)
    written: list = []
    overlaps: list = []

    ordered = sorted(
        data.live_annotations(),
        key=lambda a: class_index.get((a.class_key, a.side), 0),
    )

    for annotation in ordered:
        if not annotation.is_assessable:
            continue
        value = class_index.get((annotation.class_key, annotation.side))
        if value is None:
            continue
        gt = GeometryType(annotation.geometry_type)

        mask = None
        polygon = annotation_polygon(annotation)
        if polygon is not None:
            mask = rasterize_polygon(polygon, shape)
        elif gt is GeometryType.POLYLINE and len(annotation.points()) >= 2:
            mask = rasterize_polyline(annotation.points(), shape, POLYLINE_STROKE_PX)
        if mask is None or not mask.any():
            continue

        collision = np.logical_and(mask, label > 0)
        if collision.any():
            overlaps.append(
                {
                    "class_key": annotation.class_key,
                    "side": annotation.side,
                    "overlap_pixels": int(collision.sum()),
                    "resolution": "higher class index takes the pixel",
                }
            )
        label[mask] = value
        written.append(
            {
                "annotation_id": annotation.id,
                "class_key": annotation.class_key,
                "side": annotation.side,
                "index": value,
                "pixels": int(mask.sum()),
            }
        )

    # Optional derived cortical band, when both borders exist.
    for side in (Side.RIGHT, Side.LEFT):
        peri = data.present("periosteal_border", side)
        endo = data.present("endosteal_border", side)
        if peri and endo and data.present("cortical_mask", side) is None:
            value = class_index.get(("cortical_mask", side.value))
            if value is None:
                continue
            band = cortical_band_mask(peri.points(), endo.points(), shape)
            free = np.logical_and(band, label == 0)
            if free.any():
                label[free] = value
                written.append(
                    {
                        "annotation_id": "",
                        "class_key": "cortical_mask",
                        "side": side.value,
                        "index": value,
                        "pixels": int(free.sum()),
                        "derived": True,
                        "note": "Derived from the periosteal and endosteal borders.",
                    }
                )

    # A single indexed plane can hold only one class per pixel, so a class drawn
    # earlier may end up partly or wholly covered. Reporting only what was drawn
    # would overstate what the file contains, so each entry also carries the
    # number of pixels that survived, and a class covered completely is marked.
    surviving = {int(v): int(c) for v, c in zip(*np.unique(label, return_counts=True))}
    for entry in written:
        drawn = entry.get("pixels", 0)
        visible = surviving.get(entry["index"], 0)
        entry["pixels_drawn"] = drawn
        entry["pixels_visible"] = visible
        entry["pixels"] = visible
        if visible == 0 and drawn > 0:
            entry["fully_occluded"] = True
            entry["note"] = (
                "This class was drawn but is completely covered by a class with a "
                "higher index. Its geometry is in the annotation document."
            )
        elif visible < drawn:
            entry["partly_occluded"] = True

    return label, written, overlaps


def write_indexed_png(path, label: np.ndarray, class_index: dict) -> Path:
    """Write the label image as an indexed PNG with a documented palette."""
    from PIL import Image

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    palette = [0, 0, 0] * 256
    palette[0:3] = [0, 0, 0]
    for (class_key, _side), value in class_index.items():
        if value > 255:
            continue
        try:
            rgb = _hex_to_rgb(get_class(class_key).colour)
        except (KeyError, ValueError):
            rgb = (255, 255, 255)
        palette[value * 3 : value * 3 + 3] = list(rgb)

    image = Image.fromarray(label, mode="P")
    image.putpalette(palette)
    image.save(str(path), format="PNG", optimize=True, bits=8)
    return path


def read_indexed_png(path) -> np.ndarray:
    """Read back an indexed PNG as class indices, not colours."""
    from PIL import Image

    with Image.open(path) as image:
        if image.mode != "P":
            raise ValueError(
                f"{Path(path).name} is not an indexed PNG, so class indices "
                f"cannot be recovered from it."
            )
        return np.array(image, dtype=np.uint8)


def verify_round_trip(path, original: np.ndarray) -> dict:
    """Confirm a written mask reads back identically (AC 006)."""
    try:
        recovered = read_indexed_png(path)
    except (OSError, ValueError) as exc:
        return {"ok": False, "reason": str(exc)}

    if recovered.shape != original.shape:
        return {
            "ok": False,
            "reason": (
                f"The mask read back as {recovered.shape} but was written as "
                f"{original.shape}, so coordinates would be displaced."
            ),
        }
    if not np.array_equal(recovered, original):
        differing = int((recovered != original).sum())
        lost = sorted(set(np.unique(original).tolist()) - set(np.unique(recovered).tolist()))
        return {
            "ok": False,
            "reason": f"{differing} pixels differ after the round trip.",
            "classes_lost": lost,
        }
    return {
        "ok": True,
        "classes_present": sorted(int(v) for v in np.unique(original) if v),
        "shape": list(original.shape),
        "reason": "The mask read back with identical class indices and shape.",
    }


# ---------------------------------------------------------------------------
# COCO compatible segmentation
# ---------------------------------------------------------------------------


def coco_document(case_bundles, class_index: dict | None = None) -> dict:
    """Build a COCO compatible segmentation document.

    Only classes with genuine area are included. A landmark is a point and a
    cortical width line is a line; neither is a segmentation, and inventing a
    box around them would misrepresent what was annotated. Those live in the
    geometry document instead.
    """
    class_index = class_index or build_class_index()
    categories: list = []
    category_ids: dict = {}
    for (class_key, side), value in sorted(class_index.items(), key=lambda kv: kv[1]):
        try:
            cls = get_class(class_key)
        except KeyError:
            continue
        if cls.geometry not in (
            GeometryType.POLYGON, GeometryType.MASK, GeometryType.BOX, GeometryType.ROI_RECT
        ):
            continue
        category_ids[(class_key, side)] = value
        categories.append(
            {
                "id": value,
                "name": f"{cls.short_code}_{side}",
                "supercategory": cls.category.value,
                "class_key": class_key,
                "side": side,
                "display_name": f"{cls.display_name} ({Side(side).display})",
            }
        )

    images: list = []
    annotations: list = []
    annotation_id = 1

    for image_id, (data, *_rest) in enumerate(case_bundles, start=1):
        case = data.case
        images.append(
            {
                "id": image_id,
                "file_name": f"{case.pseudonym}.png",
                "width": case.source.columns,
                "height": case.source.rows,
                "case_pseudonym": case.pseudonym,
                "source_checksum_sha256": case.source.sha256,
            }
        )
        for annotation in data.live_annotations():
            if not annotation.is_assessable:
                continue
            category = category_ids.get((annotation.class_key, annotation.side))
            if category is None:
                continue
            polygon = annotation_polygon(annotation)
            if polygon is None or len(polygon) < 3:
                continue
            flat = [float(v) for p in polygon for v in p]
            xs = [p[0] for p in polygon]
            ys = [p[1] for p in polygon]
            annotations.append(
                {
                    "id": annotation_id,
                    "image_id": image_id,
                    "category_id": category,
                    "segmentation": [flat],
                    "area": float(polygon_area(polygon)),
                    "bbox": [
                        float(min(xs)), float(min(ys)),
                        float(max(xs) - min(xs)), float(max(ys) - min(ys)),
                    ],
                    "iscrowd": 0,
                    "aria_annotation_id": annotation.id,
                    "aria_class_key": annotation.class_key,
                    "aria_side": annotation.side,
                }
            )
            annotation_id += 1

    return {
        "info": {
            "description": "ARIA mandibular radiomorphometric annotations",
            "version": version_block()["schema_version"],
            "coordinate_system": "original image pixels",
            "note": (
                "Point and line classes are not represented here because they "
                "are not segmentations. They are in the geometry document."
            ),
        },
        "licenses": [],
        "images": images,
        "annotations": annotations,
        "categories": categories,
    }


def write_masks(
    directory, data, shape: tuple, class_index: dict | None = None, verify: bool = True
) -> dict:
    """Write the indexed mask and class map for one case."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    class_index = class_index or build_class_index()

    label, written, overlaps = rasterise_case(data, shape, class_index)
    mask_path = directory / "mask.png"
    write_indexed_png(mask_path, label, class_index)

    map_path = directory / "classmap.json"
    document = class_map_document(class_index)
    document["written_classes"] = written
    document["overlaps"] = overlaps
    document["image_shape"] = {"rows": shape[0], "columns": shape[1]}

    result = {
        "mask": mask_path, "classmap": map_path,
        "written": written, "overlaps": overlaps,
    }
    if verify:
        verification = verify_round_trip(mask_path, label)
        document["round_trip_check"] = verification
        result["round_trip"] = verification

    atomic_write_text(map_path, json.dumps(document, indent=2, default=str))
    return result
