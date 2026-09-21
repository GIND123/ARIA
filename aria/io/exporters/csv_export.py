"""Tabular export with a data dictionary (FR 049).

Every file is UTF-8 with one documented variable per column, and every column
appears in ``data_dictionary.csv`` with its type, unit and meaning. A reader who
opens the export without ever seeing the application should be able to work out
what each column is.

Measurements are written twice on purpose:

``measurements_long.csv``
    One row per measure per side. This is the shape statistical software wants.

``measurements_wide.csv``
    One row per case with a column per measure and side. This is the shape
    people actually read.

Both come from the same values, so they cannot disagree.
"""

from __future__ import annotations

import csv
from pathlib import Path

from ...version import version_block
from ...core.measurements import MeasurementEngine, MeasurementKind, measurements_to_rows
from ...core.schema import MCIGrade, ProjectSchema, QualityFlag, Side, get_class

#: The data dictionary. Each entry is variable, table, type, unit, description.
DATA_DICTIONARY = [
    # cases.csv
    ("case_pseudonym", "cases", "text", "", "Pseudonymous case identifier. Never a patient identifier."),
    ("project_name", "cases", "text", "", "Name of the project the case belongs to."),
    ("case_status", "cases", "text", "", "Workflow state: unassigned, assigned, in progress, submitted, returned, accepted or adjudicated."),
    ("split", "cases", "text", "", "Dataset split label assigned by the data manager."),
    ("source_format", "cases", "text", "", "Format of the retained original file: dicom or png."),
    ("source_checksum_sha256", "cases", "text", "", "SHA-256 digest of the retained original file."),
    ("image_rows", "cases", "integer", "pixels", "Image height in pixels."),
    ("image_columns", "cases", "integer", "pixels", "Image width in pixels."),
    ("bits_stored", "cases", "integer", "bits", "Bits of real content per sample."),
    ("photometric_interpretation", "cases", "text", "", "Pixel polarity as declared by the source."),
    ("manufacturer", "cases", "text", "", "Acquisition device manufacturer, when the profile retains it."),
    ("manufacturer_model", "cases", "text", "", "Acquisition device model, when the profile retains it."),
    ("calibration_source", "cases", "text", "", "Where the spatial scale came from."),
    ("calibration_status", "cases", "text", "", "Validation state of the spatial scale."),
    ("row_spacing_mm", "cases", "number", "mm per pixel", "Vertical detector scale before any correction."),
    ("col_spacing_mm", "cases", "number", "mm per pixel", "Horizontal detector scale before any correction."),
    ("effective_row_spacing_mm", "cases", "number", "mm per pixel", "Vertical scale after any approved magnification correction."),
    ("effective_col_spacing_mm", "cases", "number", "mm per pixel", "Horizontal scale after any approved magnification correction."),
    ("correction_factor", "cases", "text", "", "Magnification correction applied, or none."),
    ("millimetres_available", "cases", "boolean", "", "True when millimetre values are produced for this case."),
    ("laterality_source", "cases", "text", "", "Laterality as declared by the source file, if any."),
    ("laterality_confirmed", "cases", "boolean", "", "True when anatomical right and left were explicitly confirmed."),
    ("annotator_pseudonym", "cases", "text", "", "Pseudonym of the annotator who produced the set."),
    ("reviewer_pseudonym", "cases", "text", "", "Pseudonym of the reviewer who accepted or returned the set."),
    ("schema_version", "cases", "text", "", "Version of the label schema the annotations follow."),
    ("annotation_version", "cases", "integer", "", "Revision number of the annotation set."),
    ("n_annotations", "cases", "integer", "", "Number of live annotation objects in the set."),
    ("n_quality_flags", "cases", "integer", "", "Number of quality flags recorded on the case."),
    ("imported_at", "cases", "timestamp", "ISO 8601", "When the case was imported."),
    ("submitted_at", "cases", "timestamp", "ISO 8601", "When the annotation set was submitted."),

    # measurements_long.csv
    ("measure", "measurements_long", "text", "", "Canonical measure name."),
    ("measure_display", "measurements_long", "text", "", "Human readable measure name."),
    ("aliases", "measurements_long", "text", "", "Accepted alternative names, separated by a vertical bar."),
    ("side", "measurements_long", "text", "", "R for right, L for left, NA for a bilateral aggregate."),
    ("value_px", "measurements_long", "number", "pixels", "Value in original image pixels."),
    ("value_mm", "measurements_long", "number", "mm", "Value in millimetres, empty when no validated calibration exists."),
    ("value_ratio", "measurements_long", "number", "dimensionless", "Value for ratio measures such as the panoramic mandibular index."),
    ("unit", "measurements_long", "text", "", "Unit of the reported value: px, mm, ratio or none."),
    ("assessable", "measurements_long", "boolean", "", "False when the side could not be measured."),
    ("unavailable_reason", "measurements_long", "text", "", "Why a value is not available."),
    ("aggregation", "measurements_long", "text", "", "Aggregation rule used, or none for a side specific value."),
    ("sides_used", "measurements_long", "text", "", "Sides that contributed to an aggregate."),
    ("source_annotation_ids", "measurements_long", "text", "", "Identifiers of the annotations the value came from."),
    ("calculation_version", "measurements_long", "text", "", "Version of the calculation rules."),
    ("calibration_source", "measurements_long", "text", "", "Where the spatial scale used for this value came from."),
    ("calibration_status", "measurements_long", "text", "", "Validation state of the scale used for this value."),
    ("calibration_scale", "measurements_long", "text", "", "The row and column spacing applied to this value, as text."),
    ("correction_factor", "measurements_long", "text", "", "Magnification correction applied to this value, or none."),
    ("millimetres_available", "measurements_long", "boolean", "", "True when this value could be expressed in millimetres."),
    ("aliases", "measurements_long", "text", "", "Accepted alternative names for the measure, separated by a vertical bar."),
    ("ratio_basis", "measurements_long", "text", "", "Whether a ratio was taken from millimetre or pixel distances."),
    ("warnings", "measurements_long", "text", "", "Notes attached to the value."),
    ("screening_rules", "measurements_long", "text", "", "Outcome of any configured screening rule. Not a diagnosis."),

    # labels.csv
    ("label_key", "labels", "text", "", "Categorical label key, for example mci_grade."),
    ("label_value", "labels", "text", "", "Assigned value, for example C1, C2, C3, not_assessable or uncertain."),
    ("label_definition", "labels", "text", "", "Definition of the assigned grade."),
    ("rationale", "labels", "text", "", "Annotator note explaining the assignment."),
    ("region_annotation_id", "labels", "text", "", "Identifier of the region the grade was read from."),

    # quality_flags.csv
    ("flag", "quality_flags", "text", "", "Quality flag key."),
    ("flag_display", "quality_flags", "text", "", "Human readable flag name."),
    ("comment", "quality_flags", "text", "", "Free text comment recorded with the flag."),

    # annotations.csv
    ("annotation_id", "annotations", "text", "", "Identifier of the annotation object."),
    ("class_key", "annotations", "text", "", "Label class key."),
    ("class_display", "annotations", "text", "", "Human readable label class name."),
    ("geometry_type", "annotations", "text", "", "point, line, polyline, polygon, box, mask or roi_rect."),
    ("presence", "annotations", "text", "", "present, not_visible, not_assessable, absent_anatomy or uncertain."),
    ("n_points", "annotations", "integer", "", "Number of vertices."),
    ("length_px", "annotations", "number", "pixels", "Path length for line and polyline objects."),
    ("area_px", "annotations", "number", "square pixels", "Enclosed area for polygon and box objects."),
    ("bbox_x", "annotations", "number", "pixels", "Left edge of the bounding box."),
    ("bbox_y", "annotations", "number", "pixels", "Top edge of the bounding box."),
    ("bbox_width", "annotations", "number", "pixels", "Bounding box width."),
    ("bbox_height", "annotations", "number", "pixels", "Bounding box height."),
    ("visibility_score", "annotations", "integer", "0 to 4", "Annotator judgement of how clearly the structure is visible."),
    ("ambiguous", "annotations", "boolean", "", "True when the annotator flagged the object as ambiguous."),
    ("revision", "annotations", "integer", "", "Revision counter for this object."),

    # omissions.csv
    ("state", "omissions", "text", "", "Why the label is absent: an explicit presence state, or not_recorded."),
    ("state_display", "omissions", "text", "", "Human readable form of the absence state."),

    # texture.csv
    ("region_id", "texture", "text", "", "Identifier of the region annotation the features were computed from."),
    ("region_class", "texture", "text", "", "Class of the analysed region."),
    ("roi_x", "texture", "integer", "pixels", "Left edge of the analysed region."),
    ("roi_y", "texture", "integer", "pixels", "Top edge of the analysed region."),
    ("roi_width", "texture", "integer", "pixels", "Width of the analysed region."),
    ("roi_height", "texture", "integer", "pixels", "Height of the analysed region."),
    ("texture_version", "texture", "text", "", "Version of the texture implementations."),
    ("fractal_dimension", "texture", "number", "dimensionless", "Box counting dimension of the skeletonised trabecular pattern."),
    ("fd_r_squared", "texture", "number", "dimensionless", "Goodness of fit of the box counting regression."),
    ("glcm_contrast_mean", "texture", "number", "dimensionless", "Co-occurrence contrast averaged over four directions."),
    ("glcm_entropy_mean", "texture", "number", "dimensionless", "Co-occurrence entropy averaged over four directions."),
]


def _write_rows(path, fieldnames, rows) -> Path:
    """Write a CSV file as UTF-8 with a byte order mark.

    The mark is included because spreadsheet software on Windows otherwise
    misreads UTF-8 files, and a mangled pseudonym in a shared file causes real
    confusion.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path


def case_row(data, project, annotator=None, reviewer=None) -> dict:
    case = data.case
    cal = case.calibration
    return {
        "case_pseudonym": case.pseudonym,
        "case_id": case.id,
        "project_name": project.name if project else "",
        "case_status": case.state,
        "split": case.split,
        "source_format": case.source.source_format,
        "source_checksum_sha256": case.source.sha256,
        "source_filename": case.source.original_filename,
        "image_rows": case.source.rows,
        "image_columns": case.source.columns,
        "bits_stored": case.source.bits_stored,
        "photometric_interpretation": case.source.photometric_interpretation,
        "converted_for_display": case.source.converted_for_display,
        "manufacturer": case.source.manufacturer,
        "manufacturer_model": case.source.manufacturer_model,
        "acquisition_date": case.source.acquisition_date,
        "calibration_source": cal.source.value,
        "calibration_status": cal.status.value,
        "row_spacing_mm": cal.row_spacing_mm,
        "col_spacing_mm": cal.col_spacing_mm,
        "effective_row_spacing_mm": cal.effective_row_mm,
        "effective_col_spacing_mm": cal.effective_col_mm,
        "correction_factor": cal.correction_factor_text,
        "millimetres_available": cal.millimetres_available,
        "laterality_source": case.source.image_laterality,
        "laterality_confirmed": case.laterality_confirmed,
        "annotator_pseudonym": annotator.pseudonym if annotator else "",
        "reviewer_pseudonym": reviewer.pseudonym if reviewer else "",
        "schema_version": data.annotation_set.schema_version,
        "annotation_version": data.annotation_set.annotation_version,
        "annotation_set_kind": data.annotation_set.kind,
        "n_annotations": len(data.live_annotations()),
        "n_quality_flags": len(data.quality_flags),
        "imported_at": case.imported_at,
        "submitted_at": data.annotation_set.submitted_at or "",
    }


CASE_FIELDS = [
    "case_pseudonym", "case_id", "project_name", "case_status", "split",
    "source_format", "source_checksum_sha256", "source_filename", "image_rows",
    "image_columns", "bits_stored", "photometric_interpretation",
    "converted_for_display", "manufacturer", "manufacturer_model",
    "acquisition_date", "calibration_source", "calibration_status",
    "row_spacing_mm", "col_spacing_mm", "effective_row_spacing_mm",
    "effective_col_spacing_mm", "correction_factor", "millimetres_available",
    "laterality_source", "laterality_confirmed", "annotator_pseudonym",
    "reviewer_pseudonym", "schema_version", "annotation_version",
    "annotation_set_kind", "n_annotations", "n_quality_flags", "imported_at",
    "submitted_at",
]

MEASUREMENT_FIELDS = [
    "case_pseudonym", "measure", "measure_display", "aliases", "side", "value_px",
    "value_mm", "value_ratio", "unit", "assessable", "unavailable_reason",
    "aggregation", "sides_used", "source_annotation_ids", "calculation_version",
    "calibration_source", "calibration_status", "calibration_scale",
    "correction_factor", "millimetres_available", "ratio_basis", "warnings",
    "screening_rules",
]

LABEL_FIELDS = [
    "case_pseudonym", "label_key", "side", "label_value", "label_display",
    "label_definition", "rationale", "region_annotation_id", "annotator_pseudonym",
]

FLAG_FIELDS = ["case_pseudonym", "flag", "flag_display", "side", "comment", "created_at"]

ANNOTATION_FIELDS = [
    "case_pseudonym", "annotation_id", "class_key", "class_display", "side",
    "geometry_type", "presence", "n_points", "length_px", "length_mm", "area_px",
    "area_mm2", "bbox_x", "bbox_y", "bbox_width", "bbox_height",
    "visibility_score", "ambiguous", "revision", "notes",
]

OMISSION_FIELDS = [
    "case_pseudonym", "class_key", "class_display", "side", "state",
    "state_display", "reason",
]


def annotation_rows(data) -> list:
    from ...core.geometry import bounding_box, polygon_area, polyline_length

    cal = data.case.calibration
    rows: list = []
    for a in data.live_annotations():
        pts = a.points()
        try:
            display = get_class(a.class_key).display_name
        except KeyError:
            display = a.class_key
        row = {
            "case_pseudonym": data.case.pseudonym,
            "annotation_id": a.id,
            "class_key": a.class_key,
            "class_display": display,
            "side": a.side,
            "geometry_type": a.geometry_type,
            "presence": a.presence,
            "n_points": len(pts),
            "visibility_score": a.visibility_score,
            "ambiguous": a.ambiguous,
            "revision": a.revision,
            "notes": a.notes,
        }
        if len(pts) >= 2:
            length = polyline_length(pts)
            row["length_px"] = round(length, 4)
            mm = cal.length_mm(pts[0], pts[-1]) if len(pts) == 2 else None
            row["length_mm"] = round(mm, 4) if mm is not None else ""
        if len(pts) >= 3 and a.geometry_type in ("polygon", "mask"):
            area = polygon_area(pts)
            row["area_px"] = round(area, 2)
            area_mm = cal.area_mm2(area)
            row["area_mm2"] = round(area_mm, 4) if area_mm is not None else ""
        if pts:
            x, y, w, h = bounding_box(pts)
            row.update(
                {
                    "bbox_x": round(x, 2), "bbox_y": round(y, 2),
                    "bbox_width": round(w, 2), "bbox_height": round(h, 2),
                }
            )
        rows.append(row)
    return rows


def label_rows(data, annotator=None) -> list:
    rows: list = []
    for c in data.categorical:
        display = definition = ""
        if c.key == "mci_grade":
            try:
                grade = MCIGrade(c.value)
                display, definition = grade.display, grade.definition
            except ValueError:
                display = c.value
        rows.append(
            {
                "case_pseudonym": data.case.pseudonym,
                "label_key": c.key,
                "side": c.side,
                "label_value": c.value,
                "label_display": display,
                "label_definition": definition,
                "rationale": c.rationale,
                "region_annotation_id": c.region_annotation_id or "",
                "annotator_pseudonym": annotator.pseudonym if annotator else "",
            }
        )
    return rows


def flag_rows(data) -> list:
    rows: list = []
    for q in data.quality_flags:
        try:
            display = QualityFlag(q.flag).display
        except ValueError:
            display = q.flag
        rows.append(
            {
                "case_pseudonym": data.case.pseudonym,
                "flag": q.flag, "flag_display": display, "side": q.side,
                "comment": q.comment, "created_at": q.created_at,
            }
        )
    return rows


def wide_measurement_row(data, measurements) -> dict:
    """One row per case, one column per measure and side."""
    row = {"case_pseudonym": data.case.pseudonym, "case_status": data.case.state}
    cal = data.case.calibration
    row["millimetres_available"] = cal.millimetres_available
    row["calibration_status"] = cal.status.value

    for m in measurements:
        side = m.side.lower() if m.side != Side.NONE.value else "mean"
        base = f"{m.kind}_{side}"
        if m.value_ratio is not None:
            row[base] = round(m.value_ratio, 6)
        elif m.value_mm is not None:
            row[f"{base}_mm"] = round(m.value_mm, 4)
            row[f"{base}_px"] = round(m.value_px, 3) if m.value_px is not None else ""
        elif m.value_px is not None:
            row[f"{base}_px"] = round(m.value_px, 3)
        if not m.assessable:
            row[f"{base}_assessable"] = False

    for side in (Side.RIGHT, Side.LEFT):
        label = data.grade("mci_grade", side)
        row[f"mci_grade_{side.value.lower()}"] = label.value if label else ""
    return row


def export_tables(
    directory, case_bundles, project, schema: ProjectSchema | None = None,
) -> dict:
    """Write every tabular file for a selection of cases.

    ``case_bundles`` is a list of ``(CaseData, annotator, reviewer, texture)``.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    schema = schema or ProjectSchema()
    engine = MeasurementEngine(schema)

    cases, long_rows, wide_rows = [], [], []
    labels, flags, annotations, omissions, textures = [], [], [], [], []
    wide_fields: list = ["case_pseudonym", "case_status", "calibration_status",
                         "millimetres_available"]

    from .json_export import omission_record

    for data, annotator, reviewer, texture in case_bundles:
        cases.append(case_row(data, project, annotator, reviewer))

        measurements = engine.compute(data) + engine.grades(data)
        for row in measurements_to_rows(measurements):
            row["case_pseudonym"] = data.case.pseudonym
            long_rows.append(row)

        wide = wide_measurement_row(data, measurements)
        wide_rows.append(wide)
        for key in wide:
            if key not in wide_fields:
                wide_fields.append(key)

        labels.extend(label_rows(data, annotator))
        flags.extend(flag_rows(data))
        annotations.extend(annotation_rows(data))
        for o in omission_record(data):
            omissions.append({"case_pseudonym": data.case.pseudonym, **o})

        for t in texture or []:
            flat = {
                "case_pseudonym": data.case.pseudonym,
                "region_id": t.get("region_id", ""),
                "region_class": t.get("region_class", ""),
                "side": t.get("side", ""),
                "roi_x": t.get("x"), "roi_y": t.get("y"),
                "roi_width": t.get("width"), "roi_height": t.get("height"),
                "n_pixels": t.get("n_pixels"),
                "texture_version": t.get("texture_version", ""),
            }
            for k, v in (t.get("features") or {}).items():
                if isinstance(v, (int, float, str, bool)) or v is None:
                    flat[k] = v
            textures.append(flat)

    written: dict = {}
    written["cases"] = _write_rows(directory / "cases.csv", CASE_FIELDS, cases)
    written["measurements_long"] = _write_rows(
        directory / "measurements_long.csv", MEASUREMENT_FIELDS, long_rows
    )
    written["measurements_wide"] = _write_rows(
        directory / "measurements_wide.csv", wide_fields, wide_rows
    )
    written["labels"] = _write_rows(directory / "labels.csv", LABEL_FIELDS, labels)
    written["quality_flags"] = _write_rows(directory / "quality_flags.csv", FLAG_FIELDS, flags)
    written["annotations"] = _write_rows(
        directory / "annotations.csv", ANNOTATION_FIELDS, annotations
    )
    written["omissions"] = _write_rows(directory / "omissions.csv", OMISSION_FIELDS, omissions)

    if textures:
        fields: list = []
        for t in textures:
            for k in t:
                if k not in fields:
                    fields.append(k)
        written["texture"] = _write_rows(directory / "texture_features.csv", fields, textures)

    written["data_dictionary"] = write_data_dictionary(directory / "data_dictionary.csv")
    return written


def write_data_dictionary(path) -> Path:
    """Write the documented variable list that accompanies every export."""
    rows = [
        {
            "variable": name, "table": table, "type": vtype, "unit": unit,
            "description": description,
        }
        for name, table, vtype, unit, description in DATA_DICTIONARY
    ]
    versions = version_block()
    for key, value in versions.items():
        rows.append(
            {
                "variable": key, "table": "export_metadata", "type": "text", "unit": "",
                "description": f"Version identifier recorded with this export: {value}",
            }
        )
    rows.append(
        {
            "variable": "note_units", "table": "export_metadata", "type": "text", "unit": "",
            "description": (
                "An empty value_mm column means no validated calibration exists "
                "for that case. Millimetre values are never inferred."
            ),
        }
    )
    rows.append(
        {
            "variable": "note_screening", "table": "export_metadata", "type": "text", "unit": "",
            "description": (
                "Screening rule columns record the outcome of a configurable "
                "project rule. They are not a diagnosis."
            ),
        }
    )
    return _write_rows(
        path, ["variable", "table", "type", "unit", "description"], rows
    )
