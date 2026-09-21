# Data dictionary

Written for: anyone reading an ARIA export without access to the application.

Generated from `aria/io/exporters/csv_export.py`. Do not edit by hand; run `python scripts/make_docs.py`.

The same content is written as `data_dictionary.csv` inside every export and every training bundle.

## Reading the values

**Units.** An empty `value_mm` means the case had no validated spatial calibration. Millimetre values are never inferred from an unvalidated scale. Pixel values and dimensionless ratios are present regardless.

**Coordinates.** Original image pixels. The origin is the top left corner of the top left pixel, x increases to the right and y increases downward. Nothing in the viewer affects them.

**Sides.** `R` is anatomical right, `L` is anatomical left, `M` is the midline and `NA` marks a bilateral aggregate.

**Absence.** A structure that was not annotated appears in `omissions.csv` with its state and reason. It is never a coordinate of zero.

**Screening rules.** A screening rule column records the outcome of a configurable project rule. It is not a diagnosis.

## cases.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `case_pseudonym` | text | — | Pseudonymous case identifier. Never a patient identifier. |
| `project_name` | text | — | Name of the project the case belongs to. |
| `case_status` | text | — | Workflow state: unassigned, assigned, in progress, submitted, returned, accepted or adjudicated. |
| `split` | text | — | Dataset split label assigned by the data manager. |
| `source_format` | text | — | Format of the retained original file: dicom or png. |
| `source_checksum_sha256` | text | — | SHA-256 digest of the retained original file. |
| `image_rows` | integer | pixels | Image height in pixels. |
| `image_columns` | integer | pixels | Image width in pixels. |
| `bits_stored` | integer | bits | Bits of real content per sample. |
| `photometric_interpretation` | text | — | Pixel polarity as declared by the source. |
| `manufacturer` | text | — | Acquisition device manufacturer, when the profile retains it. |
| `manufacturer_model` | text | — | Acquisition device model, when the profile retains it. |
| `calibration_source` | text | — | Where the spatial scale came from. |
| `calibration_status` | text | — | Validation state of the spatial scale. |
| `row_spacing_mm` | number | mm per pixel | Vertical detector scale before any correction. |
| `col_spacing_mm` | number | mm per pixel | Horizontal detector scale before any correction. |
| `effective_row_spacing_mm` | number | mm per pixel | Vertical scale after any approved magnification correction. |
| `effective_col_spacing_mm` | number | mm per pixel | Horizontal scale after any approved magnification correction. |
| `correction_factor` | text | — | Magnification correction applied, or none. |
| `millimetres_available` | boolean | — | True when millimetre values are produced for this case. |
| `laterality_source` | text | — | Laterality as declared by the source file, if any. |
| `laterality_confirmed` | boolean | — | True when anatomical right and left were explicitly confirmed. |
| `annotator_pseudonym` | text | — | Pseudonym of the annotator who produced the set. |
| `reviewer_pseudonym` | text | — | Pseudonym of the reviewer who accepted or returned the set. |
| `schema_version` | text | — | Version of the label schema the annotations follow. |
| `annotation_version` | integer | — | Revision number of the annotation set. |
| `n_annotations` | integer | — | Number of live annotation objects in the set. |
| `n_quality_flags` | integer | — | Number of quality flags recorded on the case. |
| `imported_at` | timestamp | ISO 8601 | When the case was imported. |
| `submitted_at` | timestamp | ISO 8601 | When the annotation set was submitted. |

## measurements_long.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `measure` | text | — | Canonical measure name. |
| `measure_display` | text | — | Human readable measure name. |
| `aliases` | text | — | Accepted alternative names, separated by a vertical bar. |
| `side` | text | — | R for right, L for left, NA for a bilateral aggregate. |
| `value_px` | number | pixels | Value in original image pixels. |
| `value_mm` | number | mm | Value in millimetres, empty when no validated calibration exists. |
| `value_ratio` | number | dimensionless | Value for ratio measures such as the panoramic mandibular index. |
| `unit` | text | — | Unit of the reported value: px, mm, ratio or none. |
| `assessable` | boolean | — | False when the side could not be measured. |
| `unavailable_reason` | text | — | Why a value is not available. |
| `aggregation` | text | — | Aggregation rule used, or none for a side specific value. |
| `sides_used` | text | — | Sides that contributed to an aggregate. |
| `source_annotation_ids` | text | — | Identifiers of the annotations the value came from. |
| `calculation_version` | text | — | Version of the calculation rules. |
| `calibration_source` | text | — | Where the spatial scale used for this value came from. |
| `calibration_status` | text | — | Validation state of the scale used for this value. |
| `calibration_scale` | text | — | The row and column spacing applied to this value, as text. |
| `correction_factor` | text | — | Magnification correction applied to this value, or none. |
| `millimetres_available` | boolean | — | True when this value could be expressed in millimetres. |
| `aliases` | text | — | Accepted alternative names for the measure, separated by a vertical bar. |
| `ratio_basis` | text | — | Whether a ratio was taken from millimetre or pixel distances. |
| `warnings` | text | — | Notes attached to the value. |
| `screening_rules` | text | — | Outcome of any configured screening rule. Not a diagnosis. |

## labels.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `label_key` | text | — | Categorical label key, for example mci_grade. |
| `label_value` | text | — | Assigned value, for example C1, C2, C3, not_assessable or uncertain. |
| `label_definition` | text | — | Definition of the assigned grade. |
| `rationale` | text | — | Annotator note explaining the assignment. |
| `region_annotation_id` | text | — | Identifier of the region the grade was read from. |

## quality_flags.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `flag` | text | — | Quality flag key. |
| `flag_display` | text | — | Human readable flag name. |
| `comment` | text | — | Free text comment recorded with the flag. |

## annotations.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `annotation_id` | text | — | Identifier of the annotation object. |
| `class_key` | text | — | Label class key. |
| `class_display` | text | — | Human readable label class name. |
| `geometry_type` | text | — | point, line, polyline, polygon, box, mask or roi_rect. |
| `presence` | text | — | present, not_visible, not_assessable, absent_anatomy or uncertain. |
| `n_points` | integer | — | Number of vertices. |
| `length_px` | number | pixels | Path length for line and polyline objects. |
| `area_px` | number | square pixels | Enclosed area for polygon and box objects. |
| `bbox_x` | number | pixels | Left edge of the bounding box. |
| `bbox_y` | number | pixels | Top edge of the bounding box. |
| `bbox_width` | number | pixels | Bounding box width. |
| `bbox_height` | number | pixels | Bounding box height. |
| `visibility_score` | integer | 0 to 4 | Annotator judgement of how clearly the structure is visible. |
| `ambiguous` | boolean | — | True when the annotator flagged the object as ambiguous. |
| `revision` | integer | — | Revision counter for this object. |

## omissions.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `state` | text | — | Why the label is absent: an explicit presence state, or not_recorded. |
| `state_display` | text | — | Human readable form of the absence state. |

## texture_features.csv

| Variable | Type | Unit | Description |
| --- | --- | --- | --- |
| `region_id` | text | — | Identifier of the region annotation the features were computed from. |
| `region_class` | text | — | Class of the analysed region. |
| `roi_x` | integer | pixels | Left edge of the analysed region. |
| `roi_y` | integer | pixels | Top edge of the analysed region. |
| `roi_width` | integer | pixels | Width of the analysed region. |
| `roi_height` | integer | pixels | Height of the analysed region. |
| `texture_version` | text | — | Version of the texture implementations. |
| `fractal_dimension` | number | dimensionless | Box counting dimension of the skeletonised trabecular pattern. |
| `fd_r_squared` | number | dimensionless | Goodness of fit of the box counting regression. |
| `glcm_contrast_mean` | number | dimensionless | Co-occurrence contrast averaged over four directions. |
| `glcm_entropy_mean` | number | dimensionless | Co-occurrence entropy averaged over four directions. |

## measurements_wide.csv

One row per case with a column per measure and side, derived from the same values as `measurements_long.csv`. Column names follow the pattern `<measure>_<side>` for ratios and `<measure>_<side>_mm` and `<measure>_<side>_px` for lengths, where side is `r`, `l` or `mean`.

## Version identifiers

Recorded with every export so a value can be traced to the rules that produced it.

| Identifier | Value in this build |
| --- | --- |
| `application` | ARIA |
| `application_version` | 1.0.0 |
| `schema_version` | 1.0.0 |
| `calculation_version` | 1.0.0 |
| `texture_version` | 1.0.0 |
| `database_schema_version` | 1 |
| `bundle_format_version` | 1.0.0 |
| `deid_profile_version` | 1.0.0 |
