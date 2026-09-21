"""Canonical annotation record and geometry export (FR 047, FR 048, FR 051).

Two documents are produced for every case, and they are deliberately separate
(FR 051):

``annotations.json``
    Raw annotation geometry in original image pixel coordinates, together with
    the provenance needed to interpret it.

``measurements.json``
    Derived values, each carrying the identifiers of the annotations it came
    from and the version of the calculation rules.

Keeping them apart is what lets a reviewer recompute the second from the first
and confirm they match, which is acceptance criterion AC 004.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ...version import version_block
from ...core.measurements import MeasurementEngine, MeasurementKind
from ...core.schema import CLASS_BY_KEY, MCIGrade, ProjectSchema, Side, get_class
from ...io.fsutil import atomic_write_text


def _annotator_pseudonym(user) -> str:
    if user is None:
        return ""
    return user.pseudonym or f"USR-{user.id[-6:].upper()}"


def canonical_record(
    data, project, annotator=None, reviewer=None, retain_uids: bool = True
) -> dict:
    """The canonical annotation record for one case (FR 047).

    Identities appear only as pseudonyms. No direct patient identifier is
    written by this function, and the export scanner checks the result.
    """
    case = data.case
    aset = data.annotation_set
    source = case.source

    record = {
        "project_id": case.project_id,
        "project_name": project.name if project else "",
        "case_id": case.id,
        "case_pseudonym": case.pseudonym,
        "source_checksum_sha256": source.sha256,
        "source_format": source.source_format,
        "source_filename": source.original_filename,
        "image_rows": source.rows,
        "image_columns": source.columns,
        "bits_stored": source.bits_stored,
        "bits_allocated": source.bits_allocated,
        "photometric_interpretation": source.photometric_interpretation,
        "samples_per_pixel": source.samples_per_pixel,
        "converted_for_display": source.converted_for_display,
        "original_channel_description": source.original_channel_description,
        "transfer_syntax_uid": source.transfer_syntax_uid,
        "modality": source.modality,
        "manufacturer": source.manufacturer,
        "manufacturer_model": source.manufacturer_model,
        "acquisition_date": source.acquisition_date,
        "calibration": case.calibration.to_dict(),
        "annotator_pseudonym": _annotator_pseudonym(annotator),
        "reviewer_pseudonym": _annotator_pseudonym(reviewer),
        "schema_version": aset.schema_version,
        "annotation_version": aset.annotation_version,
        "annotation_set_id": aset.id,
        "annotation_set_kind": aset.kind,
        "case_status": case.state,
        "split": case.split,
        "laterality": {
            "source_value": source.image_laterality,
            "confirmed": case.laterality_confirmed,
            "confirmed_at": case.laterality_confirmed_at,
            "note": case.laterality_note,
            "convention": "Anatomical right and left",
        },
        "created_at": aset.created_at,
        "updated_at": aset.updated_at,
        "submitted_at": aset.submitted_at,
        "reviewed_at": aset.reviewed_at,
        "versions": version_block(),
        "coordinate_system": {
            "space": "original_image_pixels",
            "origin": "top left corner of the top left pixel",
            "x": "column index, increasing to the right",
            "y": "row index, increasing downward",
            "note": (
                "Coordinates are unaffected by zoom, pan, windowing, inversion "
                "or display filters."
            ),
        },
    }

    if retain_uids:
        record["dicom_uids"] = {
            "sop_instance_uid": source.sop_instance_uid,
            "study_instance_uid": source.study_instance_uid,
            "series_instance_uid": source.series_instance_uid,
            "sop_class_uid": source.sop_class_uid,
            "note": "Identifiers are remapped by the deidentification profile.",
        }
    else:
        record["dicom_uids"] = {"note": "Identifiers were removed at import."}

    return record


def annotation_to_dict(annotation) -> dict:
    """One annotation, with its class definition attached for readability."""
    try:
        cls = get_class(annotation.class_key)
        class_info = {
            "display_name": cls.display_name,
            "short_code": cls.short_code,
            "category": cls.category.value,
            "colour": cls.colour,
            "aliases": list(cls.aliases),
            "requirements": list(cls.requirements),
        }
    except KeyError:
        class_info = {"display_name": annotation.class_key, "note": "Class not in the current schema."}

    return {
        "id": annotation.id,
        "class_key": annotation.class_key,
        "class": class_info,
        "side": annotation.side,
        "side_display": Side(annotation.side).display,
        "geometry_type": annotation.geometry_type,
        "coordinates": list(annotation.coordinates),
        "points": [list(p) for p in annotation.points()],
        "n_points": len(annotation.points()),
        "mask_rle": annotation.mask_rle,
        "mask_bbox": list(annotation.mask_bbox),
        "presence": annotation.presence,
        "properties": dict(annotation.properties),
        "visibility_score": annotation.visibility_score,
        "ambiguous": annotation.ambiguous,
        "locked": annotation.locked,
        "created_at": annotation.created_at,
        "updated_at": annotation.updated_at,
        "revision": annotation.revision,
        "notes": annotation.notes,
    }


def geometry_document(
    data, project, annotator=None, reviewer=None, retain_uids: bool = True
) -> dict:
    """Raw geometry document for one case (FR 048)."""
    doc = canonical_record(data, project, annotator, reviewer, retain_uids)
    doc["annotations"] = [annotation_to_dict(a) for a in data.live_annotations()]
    doc["categorical_labels"] = [
        {
            **asdict(c),
            "value_display": (
                MCIGrade(c.value).display if c.key == "mci_grade" else c.value
            ),
            "definition": (
                MCIGrade(c.value).definition if c.key == "mci_grade" else ""
            ),
        }
        for c in data.categorical
    ]
    doc["quality_flags"] = [asdict(q) for q in data.quality_flags]

    # Explicit record of what is absent and why (FR 017, AC 005).
    doc["omissions"] = omission_record(data)
    return doc


def omission_record(data) -> list:
    """Every required label that is not present, with the reason.

    Missing anatomy is recorded explicitly rather than as a zero coordinate, so
    a reader of the export can tell "not annotated" from "annotated as absent"
    (FR 017, AC 005).
    """
    from ...core.schema import Presence

    out: list = []
    present_keys = {
        (a.class_key, a.side) for a in data.live_annotations() if a.is_assessable
    }
    recorded = {(a.class_key, a.side): a for a in data.live_annotations()}

    for cls in CLASS_BY_KEY.values():
        sides = (Side.RIGHT, Side.LEFT) if cls.side_scoped else (Side.MIDLINE,)
        for side in sides:
            key = (cls.key, side.value)
            if key in present_keys:
                continue
            ann = recorded.get(key)
            if ann is not None:
                out.append(
                    {
                        "class_key": cls.key,
                        "class_display": cls.display_name,
                        "side": side.value,
                        "state": ann.presence,
                        "state_display": Presence(ann.presence).display,
                        "reason": ann.notes,
                        "annotation_id": ann.id,
                    }
                )
            elif cls.required_by_default:
                out.append(
                    {
                        "class_key": cls.key,
                        "class_display": cls.display_name,
                        "side": side.value,
                        "state": "not_recorded",
                        "state_display": "Not recorded",
                        "reason": "",
                        "annotation_id": "",
                    }
                )
    return out


def measurements_document(
    data, project, schema: ProjectSchema | None = None, annotator=None, texture=None
) -> dict:
    """Derived measurement document for one case (FR 051)."""
    schema = schema or ProjectSchema()
    engine = MeasurementEngine(schema)
    measurements = engine.compute(data)
    grades = engine.grades(data)

    doc = {
        "case_id": data.case.id,
        "case_pseudonym": data.case.pseudonym,
        "project_id": data.case.project_id,
        "annotator_pseudonym": _annotator_pseudonym(annotator),
        "annotation_version": data.annotation_set.annotation_version,
        "calibration": data.case.calibration.to_dict(),
        "versions": version_block(),
        "calculation_rules": {
            "distance_px": "Euclidean distance in original image coordinates",
            "distance_mm": (
                "Row and column spacing applied per axis, so unequal spacing "
                "converts correctly"
            ),
            "mcw": "Calibrated distance between the periosteal and endosteal endpoints",
            "pmi_superior": (
                "Cortical width divided by the superior foramen to inferior "
                "border height on the same side"
            ),
            "pmi_inferior": (
                "Cortical width divided by the inferior foramen to inferior "
                "border height on the same side"
            ),
            "bilateral_mean": "Mean over assessable sides only, with the rule recorded",
            "millimetre_availability": (
                "Millimetre values are produced only from a validated calibration"
            ),
        },
        "measurements": [m.to_dict() for m in measurements],
        "grades": [m.to_dict() for m in grades],
        "measurement_names": {
            kind.value: {"display": kind.display, "aliases": list(kind.aliases)}
            for kind in MeasurementKind
        },
    }
    if texture:
        doc["texture_features"] = list(texture)
    return doc


def write_case_json(
    directory, data, project, schema=None, annotator=None, reviewer=None,
    texture=None, retain_uids: bool = True,
) -> dict:
    """Write both documents for one case and return their paths."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    geometry = geometry_document(data, project, annotator, reviewer, retain_uids)
    measurements = measurements_document(data, project, schema, annotator, texture)

    geometry_path = directory / "annotations.json"
    measurements_path = directory / "measurements.json"
    atomic_write_text(geometry_path, json.dumps(geometry, indent=2, default=str))
    atomic_write_text(measurements_path, json.dumps(measurements, indent=2, default=str))
    return {
        "annotations": geometry_path,
        "measurements": measurements_path,
        "geometry_document": geometry,
        "measurements_document": measurements,
    }


def write_project_json(path, documents: list, project, filters: dict | None = None) -> Path:
    """Write one combined document for a whole export selection."""
    payload = {
        "project_id": project.id if project else "",
        "project_name": project.name if project else "",
        "exported_at": __import__("aria.core.models", fromlist=["utc_now"]).utc_now(),
        "versions": version_block(),
        "filters": dict(filters or {}),
        "n_cases": len(documents),
        "cases": documents,
    }
    atomic_write_text(Path(path), json.dumps(payload, indent=2, default=str))
    return Path(path)
