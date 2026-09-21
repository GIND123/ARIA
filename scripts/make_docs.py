"""Generate the reference documentation from the code.

The colour nomenclature and the data dictionary describe things that live in
`aria.core.schema` and `aria.io.exporters.csv_export`. Writing them by hand
guarantees they drift, so they are generated from the same definitions the
application uses.

    python scripts/make_docs.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DOCS = ROOT / "docs"


def colour_nomenclature() -> str:
    from aria.core.schema import (
        LABEL_CLASSES, LabelCategory, MCI_DEFINITIONS, PALETTE,
        CASE_STATE_GLYPHS, CaseState, Presence, QualityFlag, Side,
    )
    from aria.ui.theme import PALETTE as THEME, contrast_ratio

    lines = [
        "# Colour and label nomenclature",
        "",
        "Written for: annotators learning the scheme, and anyone reading an "
        "ARIA export.",
        "",
        "Generated from `aria/core/schema.py`. Do not edit by hand; run "
        "`python scripts/make_docs.py`.",
        "",
        "## The rule behind the scheme",
        "",
        "Colour is never the only carrier of meaning. Every distinction ARIA "
        "draws is also carried by something that survives a monochrome print, "
        "a projector, or colour vision deficiency:",
        "",
        "* **side** is carried by stroke pattern, solid for right and dashed "
        "for left, and by the side letter in the label chip,",
        "* **class** is carried by a short code drawn beside the geometry,",
        "* **state** is carried by a glyph and a word, not by colour alone.",
        "",
        "Base colours come from the Okabe and Ito qualitative palette, which "
        "stays separable under the common forms of colour vision deficiency.",
        "",
        "## Palette",
        "",
        "| Name | Value | Contrast on the canvas | Used by |",
        "| --- | --- | --- | --- |",
    ]
    for name, value in PALETTE.items():
        ratio = contrast_ratio(value, THEME.canvas)
        users = sorted({c.short_code for c in LABEL_CLASSES if c.colour == value})
        used = ", ".join(f"`{u}`" for u in users) if users else "not used for labels"
        lines.append(
            f"| {name.replace('_', ' ')} | `{value}` | {ratio:.2f}:1 | {used} |"
        )

    lines += [
        "",
        "## Label classes",
        "",
        "Short codes are what appear beside the geometry on the image and in "
        "the object list.",
        "",
    ]

    for category, title in (
        (LabelCategory.LANDMARK, "Landmarks"),
        (LabelCategory.CONTOUR, "Contours"),
        (LabelCategory.INDEX_LINE, "Index lines"),
        (LabelCategory.REGION, "Regions"),
        (LabelCategory.OPTIONAL, "Optional"),
    ):
        classes = [c for c in LABEL_CLASSES if c.category is category]
        if not classes:
            continue
        lines += [
            f"### {title}",
            "",
            "| Code | Label | Geometry | Colour | Sided | Required | Specification |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for c in classes:
            lines.append(
                f"| `{c.short_code}` | {c.display_name} | {c.geometry.value} | "
                f"`{c.colour}` | {'yes' if c.side_scoped else 'no'} | "
                f"{'yes' if c.required_by_default else 'no'} | "
                f"{', '.join(c.requirements) or '—'} |"
            )
        lines.append("")
        for c in classes:
            detail = c.description
            if c.aliases:
                detail += f" Also known as {', '.join(c.aliases)}."
            if c.depends_on:
                detail += f" Depends on {', '.join(c.depends_on)}."
            lines.append(f"* **{c.short_code}**, {c.display_name}. {detail}")
        lines.append("")

    lines += [
        "## Stroke pattern by side",
        "",
        "| Side | Stroke | Chip |",
        "| --- | --- | --- |",
        "| Right | solid | `CODE R` |",
        "| Left | dashed | `CODE L` |",
        "| Midline | solid | `CODE` |",
        "",
        "A locked object is drawn dotted regardless of side. An object recorded "
        "as absent is drawn at reduced opacity, and an ambiguous one carries a "
        "dotted ring.",
        "",
        "## Case states",
        "",
        "| State | Glyph | Meaning |",
        "| --- | --- | --- |",
    ]
    state_meanings = {
        CaseState.UNASSIGNED: "Imported, not yet assigned to anyone",
        CaseState.ASSIGNED: "Assigned to an annotator, not yet started",
        CaseState.IN_PROGRESS: "Being annotated",
        CaseState.SUBMITTED: "Submitted for review",
        CaseState.RETURNED: "Returned by a reviewer for changes",
        CaseState.ACCEPTED: "Accepted by a reviewer",
        CaseState.ADJUDICATED: "Adjudicated between two annotations",
    }
    for state in CaseState:
        lines.append(
            f"| {state.display} | `{CASE_STATE_GLYPHS[state]}` | "
            f"{state_meanings[state]} |"
        )

    lines += [
        "",
        "## Presence states",
        "",
        "Absence is a recorded state with a reason. It is never a coordinate of "
        "zero.",
        "",
        "| State | Meaning |",
        "| --- | --- |",
    ]
    presence_meanings = {
        Presence.PRESENT: "Annotated normally",
        Presence.NOT_VISIBLE: "Not visible on this image",
        Presence.NOT_ASSESSABLE: "Visible but cannot be assessed reliably",
        Presence.ABSENT_ANATOMY: "The structure is not present in this patient",
        Presence.UNCERTAIN: "Not confident enough to record it",
    }
    for presence in Presence:
        lines.append(f"| {presence.display} | {presence_meanings[presence]} |")

    lines += [
        "",
        "## Cortical index grades",
        "",
        "| Grade | Definition |",
        "| --- | --- |",
    ]
    for grade, definition in MCI_DEFINITIONS.items():
        lines.append(f"| {grade.display} | {definition} |")

    lines += [
        "",
        "## Quality flags",
        "",
        "| Flag | Notes |",
        "| --- | --- |",
    ]
    flag_notes = {
        QualityFlag.NOT_VISIBLE: "A required structure is not visible",
        QualityFlag.AMBIGUOUS: "The structure is visible but its boundary is unclear",
        QualityFlag.ANATOMICAL_VARIANT: "Anatomy departs from the usual pattern",
        QualityFlag.ARTEFACT: "Ghost image, metal, or other artefact",
        QualityFlag.CROPPED_ANATOMY: "Required anatomy falls outside the field",
        QualityFlag.POOR_POSITIONING: "Patient positioning affects the measurement",
        QualityFlag.MOTION: "Movement during acquisition",
        QualityFlag.OTHER: "Anything else; requires a comment",
    }
    for flag in QualityFlag:
        lines.append(f"| {flag.display} | {flag_notes[flag]} |")

    lines += [
        "",
        "## Mask export indices",
        "",
        "In an exported mask the palette index carries the class. Colour is for "
        "viewing only and must not be used to recover a class.",
        "",
        "| Index | Class | Side |",
        "| --- | --- | --- |",
        "| 0 | background | |",
    ]
    from aria.io.exporters.mask_export import build_class_index

    index = build_class_index()
    from aria.core.schema import get_class

    for (key, side), value in sorted(index.items(), key=lambda kv: kv[1]):
        try:
            name = get_class(key).display_name
        except KeyError:
            name = key
        lines.append(f"| {value} | {name} | {Side(side).display} |")

    lines += [
        "",
        "Where two classes overlap, the higher index takes the pixel, the "
        "overlap is recorded in `classmap.json`, and a class covered completely "
        "is marked as occluded there rather than silently dropped. Its geometry "
        "is always present in the annotation document.",
        "",
    ]
    return "\n".join(lines)


def data_dictionary() -> str:
    from aria.io.exporters.csv_export import DATA_DICTIONARY
    from aria.version import version_block

    by_table: dict = {}
    for name, table, vtype, unit, description in DATA_DICTIONARY:
        by_table.setdefault(table, []).append((name, vtype, unit, description))

    lines = [
        "# Data dictionary",
        "",
        "Written for: anyone reading an ARIA export without access to the "
        "application.",
        "",
        "Generated from `aria/io/exporters/csv_export.py`. Do not edit by hand; "
        "run `python scripts/make_docs.py`.",
        "",
        "The same content is written as `data_dictionary.csv` inside every "
        "export and every training bundle.",
        "",
        "## Reading the values",
        "",
        "**Units.** An empty `value_mm` means the case had no validated spatial "
        "calibration. Millimetre values are never inferred from an unvalidated "
        "scale. Pixel values and dimensionless ratios are present regardless.",
        "",
        "**Coordinates.** Original image pixels. The origin is the top left "
        "corner of the top left pixel, x increases to the right and y increases "
        "downward. Nothing in the viewer affects them.",
        "",
        "**Sides.** `R` is anatomical right, `L` is anatomical left, `M` is the "
        "midline and `NA` marks a bilateral aggregate.",
        "",
        "**Absence.** A structure that was not annotated appears in "
        "`omissions.csv` with its state and reason. It is never a coordinate of "
        "zero.",
        "",
        "**Screening rules.** A screening rule column records the outcome of a "
        "configurable project rule. It is not a diagnosis.",
        "",
    ]

    titles = {
        "cases": "cases.csv",
        "measurements_long": "measurements_long.csv",
        "labels": "labels.csv",
        "quality_flags": "quality_flags.csv",
        "annotations": "annotations.csv",
        "omissions": "omissions.csv",
        "texture": "texture_features.csv",
    }
    for table, entries in by_table.items():
        lines += [
            f"## {titles.get(table, table)}",
            "",
            "| Variable | Type | Unit | Description |",
            "| --- | --- | --- | --- |",
        ]
        for name, vtype, unit, description in entries:
            lines.append(f"| `{name}` | {vtype} | {unit or '—'} | {description} |")
        lines.append("")

    lines += [
        "## measurements_wide.csv",
        "",
        "One row per case with a column per measure and side, derived from the "
        "same values as `measurements_long.csv`. Column names follow the "
        "pattern `<measure>_<side>` for ratios and `<measure>_<side>_mm` and "
        "`<measure>_<side>_px` for lengths, where side is `r`, `l` or `mean`.",
        "",
        "## Version identifiers",
        "",
        "Recorded with every export so a value can be traced to the rules that "
        "produced it.",
        "",
        "| Identifier | Value in this build |",
        "| --- | --- |",
    ]
    for key, value in version_block().items():
        lines.append(f"| `{key}` | {value} |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    DOCS.mkdir(parents=True, exist_ok=True)

    written = []
    for name, content in (
        ("COLOUR_NOMENCLATURE.md", colour_nomenclature()),
        ("DATA_DICTIONARY.md", data_dictionary()),
    ):
        path = DOCS / name
        path.write_text(content, encoding="utf-8")
        written.append((path, len(content.splitlines())))

    print("Generated documentation:")
    for path, lines in written:
        print(f"  {path.relative_to(ROOT)}  ({lines} lines)")

    from aria.ui.icons import clear_cache

    clear_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
