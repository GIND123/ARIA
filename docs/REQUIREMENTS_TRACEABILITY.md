# Requirements traceability

Written for: the clinical and implementation teams reviewing ARIA against the
software requirements specification, version 1.0.

Each requirement lists where it is implemented and where it is verified. A
requirement with no test reference is one where verification is a process step
rather than something software can assert about itself, and that is stated.

---

## 3. Supported input and image integrity

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 001 | Import single image DICOM Part 10 and PNG | `io/guards.py`, `io/dicom_reader.py`, `io/png_reader.py` | `test_ac001_*`, `TestImportGuards::test_real_dicom_is_accepted` |
| FR 002 | Retain the original unchanged, link by checksum | `io/importer.py`, `io/fsutil.py::copy_preserving` | `TestSourceIntegrity` (three tests) |
| FR 003 | Read pixel data, photometric, rows, columns, bits stored, rescale, window, transfer syntax, UIDs, spatial calibration | `io/dicom_reader.py::read_dicom` | `test_ac001_dicom_loads_with_correct_display_and_values` |
| FR 004 | Display MONOCHROME1 and MONOCHROME2 correctly; do not alter coordinates on display change | `io/image.py`, `ui/viewer/canvas.py` | `test_ac003_*`, `selftest::check_monochrome1` |
| FR 005 | Deidentify before annotation; keep identifiers out of exports | `io/deident.py`, `io/importer.py` | `test_ac009_*` (three tests) |
| FR 006 | PNG 8 and 16 bit grayscale; colour converted for display only | `io/png_reader.py` | `test_ac002_png_loads_without_clipping` |
| FR 007 | Millimetres only from validated spacing or manual calibration | `core/units.py` | `test_ac002_uncalibrated_png_*`, `TestCalibration` |
| FR 008 | Uncalibrated PNG gives pixels and ratios; millimetres marked unavailable | `core/units.py`, `core/measurements.py` | `test_ac002_ratio_is_still_reported_without_calibration` |
| FR 009 | Show calibration source, value, unit, status and correction beside every result | `ui/panels/measure_panel.py`, `core/units.py::summary_line` | `ui_smoke.py`, screenshot `module_measure.png` |
| FR 010 | Anatomical right and left; confirm when laterality is missing | `core/validation.py`, `ui/controller.py::confirm_laterality` | `TestWorkflow::test_submission_is_blocked_while_content_is_missing` |

## 4. Viewer and annotation workspace

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 011 | Lossless zoom, pan, fit, reset, window and level, contrast, brightness, inversion, original pixel toggle | `ui/viewer/canvas.py`, `io/image.py` | `ui_smoke.py::exercise display controls` |
| FR 012 | Enhancement filters non destructive, settings stored apart from pixels | `io/image.py::apply_filter`, `cases.display_settings_json` | `test_ac003_display_changes_do_not_alter_coordinates` |
| FR 013 | Geometry in original pixel coordinates with side, class, creator, timestamps, revision | `core/models.py::Annotation`, `store/db.py` | `test_ac003_scene_coordinates_are_image_pixels` |
| FR 014 | Point, distance, polyline, polygon, box, brush, eraser, contour edit, copy, undo, redo, hide, lock, delete | `ui/viewer/canvas.py`, `ui/panels/annotate_panel.py` | `ui_smoke.py::select every drawing tool`, `TestEditJournal` |
| FR 015 | Autosave, recover interrupted sessions, prevent silent overwrite | `ui/controller.py`, `store/repository.py`, `store/db.py` | `TestConcurrency` (four tests) |
| FR 016 | Eight quality flags with comments | `core/schema.py::QualityFlag` | `TestQualityFlags` |

## 5. Mandatory annotation schema

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 017 | Sides annotated independently; missing anatomy explicit, never zero coordinates | `core/schema.py::Presence`, `ui/controller.py::set_presence` | `TestPresence` (two tests), `test_ac005_*` |
| FR 018 | Mental foramen centre, superior, inferior, optional box, visibility score, ambiguity flag | `core/schema.py` classes `mental_foramen_*` | `test_ac005_omissions_are_explicit_in_json_and_csv` |
| FR 019 | Periosteal and endosteal polylines, optional cortical mask | `core/schema.py`, `core/geometry.py::cortical_band_mask` | `test_ac006_mask_export_round_trips` |
| FR 020 | Whole mandible or paired hemimandible by project schema | `core/schema.py::ProjectSchema.mandible_mode` | `TestSchemaVersioning` |
| FR 021 | Bilateral antegonial point, bilateral gonion, midline menton | `core/schema.py` | `selftest::check_schema_integrity` |
| FR 022 | Cortical width line on the mental foramen perpendicular, stored under the canonical names | `core/geometry.py::construct_cortical_width`, `schema.CANONICAL_ALIASES` | `test_ac004_expected_arithmetic`, `selftest::check_geometry` |
| FR 023 | Antegonial index perpendicular to the cortex at the antegonial point | `core/geometry.py::construct_antegonial_thickness` | `ui_smoke.py::construct every index line` |
| FR 024 | Gonial index along the bisector of the ramus and inferior border tangents | `core/geometry.py::construct_gonial_thickness` | `ui_smoke.py::construct every index line` |
| FR 025 | PMI superior height along the cortical width axis | `core/geometry.py::construct_pmi_height` | `test_ac004_*` |
| FR 026 | PMI inferior height along the same axis | `core/geometry.py::construct_pmi_height` | `test_ac004_*` |
| FR 027 | MCI per side on a region distal to the mental foramen, five values | `core/schema.py::MCIGrade`, `mci_region` class | `TestWorkflow`, `test_ac005_*` |
| FR 028 | C1, C2 and C3 definitions | `core/schema.py::MCI_DEFINITIONS` | Definitions exported with every grade, checked in `test_ac005_*` |
| FR 029 | Optional trabecular, crestal, symphysis and user defined labels; trabecular excludes roots, canal, cortex, lesions, artefacts | `core/schema.py`, `core/validation.py::roi_overlaps_cortex` | `TestTexture`, validation warning |
| FR 030 | Texture features computed after annotation as derived features | `core/texture.py`, `ui/main_window.py::compute_texture` | `TestTexture`, `selftest::check_texture` |

## 6. Derived measurements and calculation rules

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 031 | Euclidean pixel distance; millimetres applying row and column spacing separately | `core/units.py::distance_mm` | `selftest::check_units`, `TestCalibration` |
| FR 032 | Cortical width is the calibrated periosteal to endosteal distance | `core/measurements.py::measure_line` | `test_ac004_expected_arithmetic` |
| FR 033 | PMI superior equals cortical width over superior height | `core/measurements.py::measure_ratio` | `test_ac004_expected_arithmetic` |
| FR 034 | PMI inferior equals cortical width over inferior height | `core/measurements.py::measure_ratio` | `test_ac004_expected_arithmetic` |
| FR 035 | AI and GI in pixels, and millimetres only when calibrated | `core/measurements.py` | `test_ac002_*`, `test_ac004_*` |
| FR 036 | Bilateral means from assessable sides only, retaining side values and the rule | `core/measurements.py::bilateral_mean` | `TestPresence::test_an_absent_side_is_excluded_from_the_mean` |
| FR 037 | Every derived value keeps source identifiers and calculation version | `core/measurements.py::Measurement` | `TestExportFiltering::test_raw_and_derived_are_separate_files` |
| FR 038 | Thresholds configurable, versioned, labelled as screening, disabled by default | `core/schema.py::ThresholdRule`, `ui/panels/admin_panel.py` | `TestThresholds` (three tests) |

## 7. Workflow and quality control

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 039 | Seven case states | `core/schema.py::CaseState`, `STATE_TRANSITIONS` | `TestWorkflow` (two tests) |
| FR 040 | Required labels, allowed omissions, tolerances configurable per project | `core/schema.py::ProjectSchema` | `TestWorkflow::test_configurable_omissions` |
| FR 041 | Submission blocked when mandatory content is incomplete | `core/validation.py` | `TestWorkflow::test_submission_is_blocked_*` |
| FR 042 | Reviewers compare with the original, view differences, comment, accept or return | `ui/panels/review_panel.py` | `ui_smoke.py::review and accept` |
| FR 043 | Blinded duplicate annotation and adjudication | `store/repository.py`, `ui/panels/review_panel.py::build_agreement` | `test_ac007_agreement_report_without_early_unblinding` |
| FR 044 | Point, line, measurement, ICC, Dice, IoU, Cohen and weighted kappa | `core/agreement.py` | `TestAgreement`, `selftest::check_agreement` |
| FR 045 | No universal agreement cutoff; thresholds from the protocol | `core/agreement.py::aggregate_reports` | `TestAgreement::test_no_universal_cutoff_is_applied` |
| FR 046 | Annotators complete a calibration set; results and approval stored | `core/models.py::CalibrationSetResult`, `security/auth.py::production_access_blocked`, `ui/panels/admin_panel.py` | Process step, with the state recorded and shown |

## 8. Data model and export

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| FR 047 | Canonical record with project, pseudonym, checksum, format, UIDs, dimensions, calibration, pseudonyms, versions, status | `io/exporters/json_export.py::canonical_record` | `test_ac005_*`, `test_ac009_*` |
| FR 048 | JSON geometry in pixel coordinates; indexed PNG masks with a class map; COCO where applicable | `io/exporters/json_export.py`, `mask_export.py` | `test_ac006_*` (two tests) |
| FR 049 | UTF-8 CSV, one documented variable per column, with a data dictionary | `io/exporters/csv_export.py` | `TestExportFiltering::test_data_dictionary_documents_every_column` |
| FR 050 | DICOM SR and Segmentation, with conformance statement and interoperability testing before claiming compatibility | `io/exporters/dicom_output.py` | `test_ac010_*` (two tests) |
| FR 051 | Raw annotations and derived measurements exported separately | `io/exporters/json_export.py::write_case_json` | `TestExportFiltering::test_raw_and_derived_are_separate_files` |
| FR 052 | Export by project, split, status, annotator, reviewer and case list, without identifiers | `ui/panels/export_panel.py`, `store/repository.py::list_cases` | `TestExportFiltering::test_export_by_state_and_split` |
| FR 053 | Schema changes versioned; existing annotations stay readable | `store/db.py::MIGRATIONS`, `version.py` | `TestSchemaVersioning` (three tests) |

## 9. Security, privacy and audit

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| NFR 001 | Authenticated accounts, least privilege roles | `security/auth.py` | `TestPermissions`, `TestAuthentication` |
| NFR 002 | Encrypted in transit and at rest | `security/crypto.py`. There is no transit: ARIA has no network layer, which is a stronger statement than encrypting one. At rest uses AES-256-GCM with a key held in the configuration directory, optionally wrapped by a passphrase. | `selftest::check_paths`, compatibility check reports both application and volume encryption |
| NFR 003 | Audit login, import, view, assignment, change, submission, review, adjudication, export and administration | `core/audit.py::AuditEvent`, `store/repository.py::log` | `TestAudit::test_every_event_type_reaches_the_log` |
| NFR 004 | Append only, with actor, event, object, timestamp and before and after values | `store/db.py` triggers, `core/audit.py` hash chain | `TestAudit` (four tests) |
| NFR 005 | Session timeout, password, backup, retention and deletion configurable | `config.py::Settings`, `ui/dialogs/preferences_dialog.py` | `TestAuthentication::test_policy_is_enforced` |
| NFR 006 | No third party transmission unless approved and documented | No network code exists in the application. The macOS entitlements request neither client nor server network access. | Inspection; stated in About, Privacy |

## 10. Performance, reliability and usability

| ID | Requirement | Implementation | Verification |
| --- | --- | --- | --- |
| NFR 007 | Interactive within five seconds for 95 percent of test images | `io/image.py` windowed rendering; working copies avoid re decoding | `platform/system_check.py::check_performance` measures and reports against the target |
| NFR 008 | Immediate feedback on pan, zoom and point placement without changing stored coordinates | `ui/viewer/canvas.py` view transform only | `test_ac003_*` |
| NFR 009 | Autosave after every material change, surviving restart | `ui/controller.py`, `synchronous = FULL` | `TestConcurrency::test_edits_survive_reopening_the_database` |
| NFR 010 | Keyboard shortcuts, high contrast colours, colour independent indicators, scalable text, full tooltips | `ui/theme.py`, `ui/dialogs/shortcuts_dialog.py`, glyph plus text everywhere | `selftest::check_theme_contrast`, `check_schema_integrity` (side distinguishable without colour) |
| NFR 011 | Errors identify the case and the corrective action without exposing protected data | `core/validation.py::Issue.remedy`, `io/guards.py::GuardIssue.remedy` | `TestImportGuards` asserts every refusal carries a remedy |
| NFR 012 | Backup restoration and export reproducibility tested before release | `store/db.py::backup_to`, `io/exporters/bundle.py::verify_bundle` | `TestBundle` (three tests), `test_ac004_*` |

## 11. Acceptance criteria

Every criterion in section 11 is an executable test in
`tests/test_acceptance.py`, named for the criterion. Running
`pytest tests/test_acceptance.py -v` prints them by name.

| Criterion | Test |
| --- | --- |
| AC 001 | `test_ac001_dicom_loads_with_correct_display_and_values`, `test_ac001_orientation_is_anatomical` |
| AC 002 | `test_ac002_png_loads_without_clipping`, `test_ac002_uncalibrated_png_cannot_produce_unlabelled_millimetres`, `test_ac002_ratio_is_still_reported_without_calibration` |
| AC 003 | `test_ac003_display_changes_do_not_alter_coordinates`, `test_ac003_measurements_are_independent_of_display`, `test_ac003_scene_coordinates_are_image_pixels` |
| AC 004 | `test_ac004_reviewer_can_reproduce_measurements_from_export`, `test_ac004_expected_arithmetic` |
| AC 005 | `test_ac005_omissions_are_explicit_in_json_and_csv` |
| AC 006 | `test_ac006_mask_export_round_trips`, `test_ac006_coco_geometry_matches_source` |
| AC 007 | `test_ac007_agreement_report_without_early_unblinding` |
| AC 008 | `test_ac008_returned_case_preserves_prior_submission`, `test_ac008_revisions_are_immutable` |
| AC 009 | `test_ac009_export_contains_no_direct_identifiers`, `test_ac009_configured_term_is_caught_anywhere`, `test_ac009_privacy_scan_blocks_a_leak`, `test_ac009_identifiers_are_removed_at_import` |
| AC 010 | `test_ac010_dicom_output_blocked_until_interoperability_accepted`, `test_ac010_dicom_objects_are_structurally_valid` |

## 12. Out of scope, confirmed

None of the following exist anywhere in the codebase:

* automatic osteoporosis diagnosis,
* treatment recommendation,
* any bone density estimate,
* PACS write back or any network transfer,
* three dimensional or CBCT annotation,
* model training or inference of any kind.

The geometry assists are deterministic calculations from the annotator's own
contours. They are labelled as constructions, they state their method, and they
are editable. There is no model in the product.

## 13. Decisions needing clinical sign off

These are surfaced in Administration, Decisions needing clinical sign off, with
their current state, rather than being left in a configuration file.

| Decision | Where it is set | Current default |
| --- | --- | --- |
| Cortical width naming convention | `schema.CANONICAL_ALIASES`; exported as MCW with CWI and MI as aliases | One canonical record, all three names exported |
| Region placement rules and dimensions | Administration, Label schema, analysis region size | 64 by 64 pixels, recorded with every result |
| Calibration policy per device, and whether magnification correction is permitted | Administration, Label schema, magnification correction | Correction switched off |
| Mandatory labels, not assessable rules, duplicate percentage, agreement thresholds | Administration, Label schema | 14 required labels, 20 percent duplicates, tolerances as shipped |
| Supported DICOM SOP classes, transfer syntaxes, validators and viewers | Administration, DICOM derived output | Documented; output disabled until a validator and each viewer are recorded as passing |
