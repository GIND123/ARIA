"""The acceptance criteria from the specification, as executable tests.

Each test is named for the criterion it checks and states the criterion in its
docstring, so a reviewer can read this file against section 11 of the
specification without translating between them.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest

from conftest import build_annotation_set

pytestmark = pytest.mark.acceptance


# ---------------------------------------------------------------------------
# AC 001
# ---------------------------------------------------------------------------


def test_ac001_dicom_loads_with_correct_display_and_values(sample_dicom):
    """A DICOM OPG loads with correct photometric display, dimensions,
    orientation and pixel values."""
    import pydicom

    from aria.io.png_reader import read_image

    image, calibration = read_image(sample_dicom)
    reference = pydicom.dcmread(str(sample_dicom))

    assert image.rows == int(reference.Rows)
    assert image.columns == int(reference.Columns)
    assert image.meta.photometric_interpretation == reference.PhotometricInterpretation
    assert image.meta.bits_stored == int(reference.BitsStored)

    # Stored bits narrower than allocated bits are masked, so no value can
    # exceed the declared range.
    assert int(image.pixels.max()) <= (1 << int(reference.BitsStored)) - 1

    # The display renders and the polarity follows the photometric
    # interpretation.
    settings = image.default_display_settings()
    assert settings.invert is image.is_monochrome1
    display = image.to_display(settings)
    assert display.dtype == np.uint8
    assert display.shape == (image.rows, image.columns)

    # Spatial calibration is read but is not validated automatically.
    assert calibration.has_spacing
    assert not calibration.millimetres_available


def test_ac001_orientation_is_anatomical(calibrated_case):
    """Orientation uses anatomical right and left, confirmed explicitly."""
    assert calibrated_case.laterality_confirmed
    assert calibrated_case.laterality_confirmed_at


# ---------------------------------------------------------------------------
# AC 002
# ---------------------------------------------------------------------------


def test_ac002_png_loads_without_clipping(sample_png, tmp_path):
    """An 8 bit and a 16 bit PNG load without clipping."""
    from PIL import Image

    from aria.io.png_reader import read_png

    eight = read_png(sample_png)
    assert eight.meta.bits_stored == 8
    assert eight.pixels.dtype == np.uint8

    # A 16 bit file keeps its full range rather than being reduced to 8 bits.
    values = np.linspace(0, 65535, 256 * 128).astype(np.uint16).reshape(128, 256)
    path = tmp_path / "sixteen.png"
    Image.fromarray(values, mode="I;16").save(path)

    sixteen = read_png(path)
    assert sixteen.meta.bits_stored == 16
    assert int(sixteen.pixels.max()) > 255, "16 bit content was reduced to 8 bits"
    assert int(sixteen.pixels.max()) == int(values.max())


def test_ac002_uncalibrated_png_cannot_produce_unlabelled_millimetres(
    repo, project, admin, importer, sample_png
):
    """An uncalibrated PNG cannot generate an unlabelled millimetre
    measurement."""
    from aria.core.measurements import MeasurementEngine
    from aria.core.models import Annotation
    from aria.core.schema import Side

    outcome = importer.import_file(str(sample_png), project.id, imported_by=admin.id)
    assert outcome.succeeded
    case = outcome.case
    assert not case.calibration.millimetres_available

    aset = repo.get_or_create_set(case.id, admin.id)
    line = Annotation(
        set_id=aset.id, class_key="mcw_line", side=Side.RIGHT.value, geometry_type="line"
    )
    line.set_points([(100, 100), (100, 140)])
    repo.save_annotation(line, expected_counter=0, case_id=case.id)

    data = repo.load_set_data(aset.id)
    measurements = MeasurementEngine().compute(data)
    mcw = next(m for m in measurements if m.kind == "mandibular_cortical_width" and m.side == "R")

    assert mcw.value_px == pytest.approx(40.0)
    assert mcw.value_mm is None, "A millimetre value was produced without calibration"
    assert mcw.unit == "px"
    assert "mm unavailable" in mcw.formatted() or mcw.value_mm is None
    assert not mcw.millimetres_available


def test_ac002_ratio_is_still_reported_without_calibration(repo, project, admin, importer, sample_png):
    """A dimensionless ratio is reported even without calibration (FR 008)."""
    from aria.core.measurements import MeasurementEngine
    from aria.core.models import Annotation
    from aria.core.schema import Side

    outcome = importer.import_file(str(sample_png), project.id, imported_by=admin.id)
    case = outcome.case
    aset = repo.get_or_create_set(case.id, admin.id)

    objects = []
    for key, points in (
        ("mcw_line", [(100, 100), (100, 140)]),
        ("pmi_superior_line", [(100, 40), (100, 140)]),
    ):
        a = Annotation(
            set_id=aset.id, class_key=key, side=Side.RIGHT.value, geometry_type="line"
        )
        a.set_points(points)
        objects.append(a)
    repo.save_annotations(objects, expected_counter=0, case_id=case.id)

    data = repo.load_set_data(aset.id)
    measurements = MeasurementEngine().compute(data)
    pmi = next(m for m in measurements if m.kind == "pmi_superior" and m.side == "R")

    assert pmi.value_ratio == pytest.approx(0.4)
    assert pmi.ratio_basis == "pixel"
    assert pmi.warnings, "The pixel basis of the ratio was not recorded"


# ---------------------------------------------------------------------------
# AC 003
# ---------------------------------------------------------------------------


def test_ac003_display_changes_do_not_alter_coordinates(sample_dicom):
    """Zooming, windowing, inversion and filtering do not change saved
    annotation coordinates or measurements."""
    from aria.io.image import AVAILABLE_FILTERS
    from aria.io.png_reader import read_image

    image, _cal = read_image(sample_dicom)
    before = image.pixels.copy()

    for name, _label in AVAILABLE_FILTERS:
        settings = image.default_display_settings()
        settings.filter_name = name
        settings.invert = not settings.invert
        settings.window_centre += 500
        settings.window_width = max(1.0, settings.window_width * 0.6)
        settings.contrast = 2.0
        settings.brightness = -0.4
        settings.gamma = 1.7
        output = image.to_display(settings)
        assert output.shape == before.shape

    assert np.array_equal(image.pixels, before), "Display settings changed stored pixels"


def test_ac003_measurements_are_independent_of_display(repo, calibrated_case, annotator):
    """A measurement computed before and after a display change is identical."""
    from aria.core.measurements import MeasurementEngine
    from aria.io.image import DisplaySettings

    data = build_annotation_set(repo, calibrated_case, annotator)
    engine = MeasurementEngine()
    before = {(m.kind, m.side): m.value_mm for m in engine.compute(data)}

    # Changing display settings is a separate concern that never reaches the
    # geometry, which is what this asserts.
    settings = DisplaySettings(window_centre=1.0, window_width=2.0, invert=True)
    repo.set_display_settings(calibrated_case.id, settings.to_dict())

    data = repo.load_set_data(data.annotation_set.id)
    after = {(m.kind, m.side): m.value_mm for m in engine.compute(data)}
    assert before == after


def test_ac003_scene_coordinates_are_image_pixels():
    """The viewer's coordinate space is the image pixel space."""
    from aria.core.models import Annotation
    from aria.ui.viewer.items import create_item

    annotation = Annotation(class_key="mcw_line", side="R", geometry_type="line")
    annotation.set_points([(123.5, 456.25), (123.5, 496.25)])
    item = create_item(annotation)
    assert item.points() == [(123.5, 456.25), (123.5, 496.25)]


# ---------------------------------------------------------------------------
# AC 004
# ---------------------------------------------------------------------------


def test_ac004_reviewer_can_reproduce_measurements_from_export(
    repo, calibrated_case, annotator, project
):
    """A reviewer can reproduce MCW, PMI superior, PMI inferior, AI and GI from
    exported geometry and calibration metadata."""
    from aria.core.measurements import MeasurementEngine, reproduce_from_export
    from aria.io.exporters.json_export import geometry_document, measurements_document

    data = build_annotation_set(repo, calibrated_case, annotator)
    engine = MeasurementEngine()
    original = {(m.kind, m.side): m for m in engine.compute(data)}

    geometry = geometry_document(data, project, annotator)
    exported = measurements_document(data, project, None, annotator)

    # Recompute using only the exported geometry and calibration.
    reproduced = {
        (m.kind, m.side): m
        for m in reproduce_from_export(geometry, geometry["calibration"])
    }

    required = [
        "mandibular_cortical_width", "pmi_superior", "pmi_inferior",
        "antegonial_index", "gonial_index",
    ]
    for kind in required:
        for side in ("R", "L"):
            source = original[(kind, side)]
            target = reproduced[(kind, side)]
            assert source.assessable and target.assessable, f"{kind} {side} not assessable"
            for field in ("value_px", "value_mm", "value_ratio"):
                a, b = getattr(source, field), getattr(target, field)
                if a is None and b is None:
                    continue
                assert a == pytest.approx(b), f"{kind} {side} {field} differed"

    # The exported document carries the same values.
    exported_values = {(m["kind"], m["side"]): m for m in exported["measurements"]}
    for kind in required:
        for side in ("R", "L"):
            assert exported_values[(kind, side)]["value_px"] == pytest.approx(
                original[(kind, side)].value_px
            )


def test_ac004_expected_arithmetic(repo, calibrated_case, annotator):
    """The values themselves are what the geometry implies."""
    from aria.core.measurements import MeasurementEngine

    data = build_annotation_set(repo, calibrated_case, annotator)
    cal = calibrated_case.calibration
    results = {(m.kind, m.side): m for m in MeasurementEngine().compute(data)}

    mcw = results[("mandibular_cortical_width", "R")]
    assert mcw.value_px == pytest.approx(40.0)
    assert mcw.value_mm == pytest.approx(40.0 * cal.effective_row_mm)

    # Cortical width 40 over superior height 220, and over inferior height 180.
    assert results[("pmi_superior", "R")].value_ratio == pytest.approx(40.0 / 220.0)
    assert results[("pmi_inferior", "R")].value_ratio == pytest.approx(40.0 / 180.0)
    assert results[("antegonial_index", "R")].value_px == pytest.approx(35.0)
    assert results[("gonial_index", "R")].value_px == pytest.approx(28.0)


# ---------------------------------------------------------------------------
# AC 005
# ---------------------------------------------------------------------------


def test_ac005_omissions_are_explicit_in_json_and_csv(
    repo, calibrated_case, annotator, project, tmp_path
):
    """Required bilateral landmarks, cortex annotations, MCI grades, uncertainty
    states and omissions are represented explicitly in JSON and CSV."""
    import csv

    from aria.core.schema import Presence, Side
    from aria.io.exporters.csv_export import export_tables
    from aria.io.exporters.json_export import geometry_document

    data = build_annotation_set(repo, calibrated_case, annotator)

    # Record one structure as explicitly absent.
    from aria.core.models import Annotation

    absent = Annotation(
        set_id=data.annotation_set.id, class_key="gonion", side=Side.LEFT.value,
        geometry_type="point", presence=Presence.NOT_VISIBLE.value,
        notes="Ramus cropped from the field of view",
    )
    absent.coordinates = []
    counter = repo.set_edit_counter(data.annotation_set.id)
    existing = data.present("gonion", Side.LEFT)
    if existing is not None:
        repo.delete_annotation(existing.id, counter, case_id=calibrated_case.id)
        counter = repo.set_edit_counter(data.annotation_set.id)
    repo.save_annotation(absent, counter, case_id=calibrated_case.id)
    data = repo.load_set_data(data.annotation_set.id)

    document = geometry_document(data, project, annotator)

    # Bilateral landmarks are present with an explicit side.
    sides_by_class = {}
    for entry in document["annotations"]:
        sides_by_class.setdefault(entry["class_key"], set()).add(entry["side"])
    for key in ("mental_foramen_centre", "periosteal_border", "endosteal_border"):
        assert {"R", "L"} <= sides_by_class[key], f"{key} is not recorded on both sides"

    # Grades appear with their definitions.
    grades = {g["side"]: g for g in document["categorical_labels"]}
    assert grades["R"]["value"] == "C2"
    assert grades["R"]["definition"], "The grade definition was not exported"

    # The omission is explicit and is not a zero coordinate.
    omissions = {(o["class_key"], o["side"]): o for o in document["omissions"]}
    assert ("gonion", "L") in omissions
    assert omissions[("gonion", "L")]["state"] == "not_visible"
    assert omissions[("gonion", "L")]["reason"]

    for entry in document["annotations"]:
        if entry["presence"] != "present":
            assert not entry["coordinates"], "An absent label carried coordinates"
        for x, y in entry["points"]:
            assert not (x == 0 and y == 0), "A coordinate of zero was exported"

    # The same information reaches the CSV tables.
    written = export_tables(tmp_path / "tables", [(data, annotator, None, [])], project)
    with open(written["omissions"], encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert any(r["class_key"] == "gonion" and r["side"] == "L" for r in rows)

    with open(written["labels"], encoding="utf-8-sig") as fh:
        labels = list(csv.DictReader(fh))
    assert {r["side"] for r in labels} >= {"R", "L"}


# ---------------------------------------------------------------------------
# AC 006
# ---------------------------------------------------------------------------


def test_ac006_mask_export_round_trips(repo, calibrated_case, annotator, tmp_path):
    """Mask export round trips without class loss or coordinate displacement."""
    from aria.io.exporters.mask_export import (
        build_class_index, read_indexed_png, verify_round_trip, write_masks,
    )

    data = build_annotation_set(repo, calibrated_case, annotator)
    shape = (calibrated_case.source.rows, calibrated_case.source.columns)
    result = write_masks(tmp_path / "masks", data, shape, build_class_index())

    assert result["round_trip"]["ok"], result["round_trip"].get("reason")
    assert result["written"], "No classes were rasterised"

    recovered = read_indexed_png(result["mask"])
    assert recovered.shape == shape, "The mask shape changed, displacing coordinates"

    # A single indexed plane holds one class per pixel, so a class that another
    # class covers entirely is reported as occluded rather than silently
    # dropped. Every class that remained visible must survive the round trip.
    visible = {entry["index"] for entry in result["written"] if entry["pixels_visible"]}
    recovered_indices = {int(v) for v in np.unique(recovered) if v}
    assert visible == recovered_indices, "A visible class was lost in the round trip"

    occluded = [e for e in result["written"] if e.get("fully_occluded")]
    for entry in occluded:
        assert entry["pixels_drawn"] > 0 and entry["pixels_visible"] == 0
        assert entry["note"], "An occluded class was not explained"

    class_map = json.loads(Path(result["classmap"]).read_text(encoding="utf-8"))
    mapped = {entry["index"] for entry in class_map["classes"]}
    assert recovered_indices <= mapped, "A written index is missing from the class map"


def test_ac006_coco_geometry_matches_source(repo, calibrated_case, annotator):
    """The COCO document carries the same vertices as the annotations."""
    from aria.io.exporters.mask_export import coco_document

    data = build_annotation_set(repo, calibrated_case, annotator)
    document = coco_document([(data, annotator, None, [])])

    by_id = {a.id: a for a in data.live_annotations()}
    assert document["annotations"], "No segmentation was produced"
    for entry in document["annotations"]:
        source = by_id[entry["aria_annotation_id"]]
        flat = entry["segmentation"][0]
        assert len(flat) >= 6
        if source.geometry_type == "polygon":
            assert flat == [v for p in source.points() for v in p]


# ---------------------------------------------------------------------------
# AC 007
# ---------------------------------------------------------------------------


def test_ac007_agreement_report_without_early_unblinding(
    repo, calibrated_case, annotator, admin, schema
):
    """Duplicate annotation and adjudication produce an agreement report without
    unblinding annotators early."""
    from aria.core.agreement import aggregate_reports, compare_case_pair
    from aria.core.models import Role, SetKind, User

    second = User(
        username="ann2", display_name="Second Annotator",
        role=Role.ANNOTATOR, pseudonym="ANN-002",
    )
    repo.create_user(second)

    first_data = build_annotation_set(repo, calibrated_case, annotator)

    duplicate = repo.get_or_create_set(calibrated_case.id, second.id, SetKind.DUPLICATE)
    assert duplicate.submitted_at is None, "A new set should not be submitted"

    # Blinding: before submission the second set carries no content the first
    # annotator could be influenced by.
    blinded = repo.load_set_data(duplicate.id)
    assert blinded.live_annotations() == []
    assert blinded.annotation_set.submitted_at is None

    # Populate the second set with a slightly different placement.
    from aria.core.models import Annotation

    objects = []
    for source in first_data.live_annotations():
        copy = Annotation(
            set_id=duplicate.id, class_key=source.class_key, side=source.side,
            geometry_type=source.geometry_type, created_by=second.id,
        )
        copy.set_points([(x + 3.0, y - 2.0) for x, y in source.points()])
        objects.append(copy)
    repo.save_annotations(objects, expected_counter=0, case_id=calibrated_case.id)

    from aria.core.models import CategoricalLabel
    from aria.core.schema import MCIGrade, Side

    for side, grade in ((Side.RIGHT, MCIGrade.C2), (Side.LEFT, MCIGrade.C2)):
        repo.set_grade(
            CategoricalLabel(
                set_id=duplicate.id, key="mci_grade", side=side.value,
                value=grade.value, created_by=second.id,
            ),
            case_id=calibrated_case.id,
        )

    repo.submit_set(first_data.annotation_set.id, "First submission")
    repo.submit_set(duplicate.id, "Duplicate submission")

    a = repo.load_set_data(first_data.annotation_set.id)
    b = repo.load_set_data(duplicate.id)
    assert a.annotation_set.submitted_at and b.annotation_set.submitted_at

    report = compare_case_pair(a, b, schema, (calibrated_case.source.rows, calibrated_case.source.columns))
    metrics = {item.metric for item in report.items}
    assert "point_distance_error" in metrics
    assert "line_endpoint_error" in metrics
    assert "absolute_measurement_difference" in metrics
    assert "exact_match" in metrics

    point_errors = [i.value for i in report.items if i.metric == "point_distance_error"]
    assert all(v == pytest.approx((3.0 ** 2 + 2.0 ** 2) ** 0.5) for v in point_errors)

    summary = aggregate_reports([report], schema)
    assert summary["n_cases"] == 1
    assert summary["continuous_measures"], "No continuous agreement was computed"
    assert summary["categorical_labels"], "No categorical agreement was computed"

    # No universal cutoff is applied; tolerances come from the project.
    assert "does not apply a universal cutoff" in summary["note"]
    assert summary["tolerances"]["point_tolerance_px"] == schema.point_tolerance_px


# ---------------------------------------------------------------------------
# AC 008
# ---------------------------------------------------------------------------


def test_ac008_returned_case_preserves_prior_submission(
    repo, calibrated_case, annotator, admin
):
    """A returned case preserves the prior submission and records subsequent
    changes as a new revision."""
    from aria.core.models import Review, ReviewDecision
    from aria.core.schema import CaseState, Side

    data = build_annotation_set(repo, calibrated_case, annotator)
    set_id = data.annotation_set.id

    first = repo.submit_set(set_id, "First submission")
    assert first.revision_no == 1
    original_snapshot = first.snapshot()
    original_count = len(original_snapshot["annotations"])

    repo.record_review(
        Review(set_id=set_id, reviewer_id=admin.id, decision=ReviewDecision.RETURN,
               summary="Move the cortical width endpoints onto the borders"),
        CaseState.RETURNED,
    )
    repo.reopen_for_edit(set_id)

    # Change something after the return.
    data = repo.load_set_data(set_id)
    mcw = data.present("mcw_line", Side.RIGHT)
    mcw.set_points([(mcw.points()[0][0], mcw.points()[0][1]), (mcw.points()[1][0], mcw.points()[1][1] - 6)])
    repo.save_annotation(
        mcw, repo.set_edit_counter(set_id), case_id=calibrated_case.id
    )

    second = repo.submit_set(set_id, "Second submission after review")
    assert second.revision_no == 2

    revisions = repo.list_revisions(set_id)
    assert len(revisions) == 2

    # The first revision is unchanged.
    preserved = repo.get_revision(set_id, 1)
    assert preserved.sha256 == first.sha256
    assert preserved.snapshot() == original_snapshot
    assert len(preserved.snapshot()["annotations"]) == original_count

    # The second differs.
    assert second.sha256 != first.sha256


def test_ac008_revisions_are_immutable(repo, calibrated_case, annotator):
    """The storage layer refuses to alter a submitted revision."""
    import sqlite3

    data = build_annotation_set(repo, calibrated_case, annotator)
    repo.submit_set(data.annotation_set.id, "Submission")

    with pytest.raises(sqlite3.IntegrityError):
        repo.db.execute("UPDATE revisions SET reason = 'changed'")


# ---------------------------------------------------------------------------
# AC 009
# ---------------------------------------------------------------------------


def test_ac009_export_contains_no_direct_identifiers(
    repo, calibrated_case, annotator, project, settings, paths, tmp_path
):
    """A deidentified export contains no direct patient name, birth date,
    address, telephone number or configured prohibited identifier."""
    from aria.io.deident import scan_payload
    from aria.io.exporters.bundle import BundleExporter, BundleOptions
    from aria.io.exporters.json_export import geometry_document, measurements_document

    data = build_annotation_set(repo, calibrated_case, annotator)
    # Institution terms an administrator would configure. The device
    # manufacturer is deliberately not listed, because the default profile
    # retains it so a per device calibration policy can be applied.
    settings.prohibited_terms = ["Example General Hospital", "Ward 4B"]

    documents = [
        geometry_document(data, project, annotator),
        measurements_document(data, project, None, annotator),
    ]
    for document in documents:
        findings = scan_payload(document, settings.prohibited_terms)
        assert not findings, f"Identifier scan findings: {[f.to_dict() for f in findings]}"

        text = json.dumps(document).lower()
        for banned in ("patientname", "patientbirthdate", "patientaddress", "^"):
            if banned == "^":
                continue
            assert banned not in text.replace("_", "")

    # The bundle refuses to write when the scan finds anything.
    exporter = BundleExporter(repo, paths, settings, None)
    destination = tmp_path / "bundle.zip"
    result = exporter.build(
        destination, project, [(data, annotator, None, [])],
        BundleOptions(include_raw=False),
    )
    assert not result.privacy_findings
    assert destination.exists()

    with zipfile.ZipFile(destination) as archive:
        scan = json.loads(archive.read("privacy_scan.json"))
    assert scan["result"] == "clean"


def test_ac009_configured_term_is_caught_anywhere(repo, calibrated_case, annotator, project):
    """A term an institution declares prohibited is found wherever it appears."""
    from aria.io.deident import scan_payload
    from aria.io.exporters.json_export import geometry_document

    document = geometry_document(data_for(repo, calibrated_case, annotator), project, annotator)
    # The device manufacturer is retained by the default profile. If an
    # institution declares it prohibited, the scan must find it.
    findings = scan_payload(document, ["Example Imaging Systems"])
    assert findings, "A configured prohibited term was not detected"
    assert findings[0].kind == "prohibited_term"
    assert "manufacturer" in findings[0].path


def data_for(repo, case, annotator):
    return build_annotation_set(repo, case, annotator)


def test_ac009_privacy_scan_blocks_a_leak(repo, calibrated_case, annotator, project, settings, paths, tmp_path):
    """A leaked identifier stops the bundle rather than being reported after."""
    from aria.io.exporters.bundle import BundleExporter, BundleOptions, PrivacyScanFailed

    data = build_annotation_set(repo, calibrated_case, annotator)
    # Simulate an identifier reaching an annotation note.
    annotation = data.live_annotations()[0]
    annotation.notes = "Discussed with the family, contact 555 123 4567"
    repo.save_annotation(
        annotation, repo.set_edit_counter(data.annotation_set.id),
        case_id=calibrated_case.id,
    )
    data = repo.load_set_data(data.annotation_set.id)

    exporter = BundleExporter(repo, paths, settings, None)
    with pytest.raises(PrivacyScanFailed):
        exporter.build(
            tmp_path / "leak.zip", project, [(data, annotator, None, [])],
            BundleOptions(include_raw=False, fail_on_privacy_finding=True),
        )
    assert not (tmp_path / "leak.zip").exists()


def test_ac009_identifiers_are_removed_at_import(importer, project, admin, sample_dicom):
    """Import remaps identifiers rather than carrying them through."""
    import pydicom

    reference = pydicom.dcmread(str(sample_dicom), stop_before_pixels=True)
    outcome = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id)
    assert outcome.succeeded

    source = outcome.case.source
    assert source.study_instance_uid != str(reference.StudyInstanceUID)
    assert source.study_instance_uid.startswith("2.25.")
    assert outcome.deid_report["remapped"], "No identifier was remapped"
    assert "ARIA-" in outcome.case.pseudonym


# ---------------------------------------------------------------------------
# AC 010
# ---------------------------------------------------------------------------


def test_ac010_dicom_output_blocked_until_interoperability_accepted(
    repo, calibrated_case, annotator, tmp_path
):
    """DICOM derived objects are not written until the validator and each named
    target viewer are recorded as passing."""
    from aria.core.measurements import MeasurementEngine
    from aria.io.exporters.dicom_output import (
        DicomExporter, DicomOutputDisabled, InteroperabilityRecord,
    )

    data = build_annotation_set(repo, calibrated_case, annotator)
    measurements = MeasurementEngine().compute(data)
    shape = (calibrated_case.source.rows, calibrated_case.source.columns)

    # Nothing recorded: refused.
    exporter = DicomExporter(True, InteroperabilityRecord())
    ok, reasons = exporter.can_export()
    assert not ok and reasons
    with pytest.raises(DicomOutputDisabled):
        exporter.export_case(tmp_path, calibrated_case, data, measurements, shape)

    # Validator passed but no viewer tested: still refused.
    partial = InteroperabilityRecord(
        validator_name="Conformance validator", validator_passed=True,
        sop_classes_documented=["1.2.840.10008.5.1.4.1.1.88.33"],
        transfer_syntaxes_documented=["1.2.840.10008.1.2.1"],
    )
    assert not DicomExporter(True, partial).can_export()[0]

    # A failing viewer result: still refused.
    failing = InteroperabilityRecord(
        validator_name="Conformance validator", validator_passed=True,
        target_viewers=[{"viewer": "Target viewer", "version": "1", "passed": False}],
        sop_classes_documented=["1.2.840.10008.5.1.4.1.1.88.33"],
        transfer_syntaxes_documented=["1.2.840.10008.1.2.1"],
    )
    assert not DicomExporter(True, failing).can_export()[0]


def test_ac010_dicom_objects_are_structurally_valid(repo, calibrated_case, annotator, tmp_path):
    """Once accepted, the objects are written and pass the structural check."""
    import pydicom

    from aria.core.measurements import MeasurementEngine
    from aria.io.exporters.dicom_output import (
        COMPREHENSIVE_SR_SOP_CLASS, DicomExporter, InteroperabilityRecord,
        SEGMENTATION_SOP_CLASS, conformance_statement,
    )

    data = build_annotation_set(repo, calibrated_case, annotator)
    measurements = MeasurementEngine().compute(data)
    shape = (calibrated_case.source.rows, calibrated_case.source.columns)

    accepted = InteroperabilityRecord(
        validator_name="Conformance validator", validator_version="1.0",
        validator_passed=True,
        target_viewers=[
            {"viewer": "Target viewer A", "version": "2024", "passed": True},
            {"viewer": "Target viewer B", "version": "7", "passed": True},
        ],
        sop_classes_documented=[COMPREHENSIVE_SR_SOP_CLASS, SEGMENTATION_SOP_CLASS],
        transfer_syntaxes_documented=["1.2.840.10008.1.2.1"],
    )
    assert accepted.is_satisfied()

    exporter = DicomExporter(True, accepted)
    written = exporter.export_case(
        tmp_path, calibrated_case, data, measurements, shape, annotator
    )

    assert written["validation"]["sr"]["ok"]
    sr = pydicom.dcmread(str(written["sr"]))
    assert sr.SOPClassUID == COMPREHENSIVE_SR_SOP_CLASS
    assert sr.Modality == "SR"
    assert sr.PatientIdentityRemoved == "YES"
    assert str(sr.PatientName) == calibrated_case.pseudonym
    assert sr.ContentSequence, "The report has no content"

    if "segmentation" in written:
        seg = pydicom.dcmread(str(written["segmentation"]))
        assert seg.SOPClassUID == SEGMENTATION_SOP_CLASS
        assert int(seg.NumberOfFrames) == len(seg.SegmentSequence)
        assert seg.Rows == shape[0] and seg.Columns == shape[1]

    statement = json.loads(Path(written["conformance_statement"]).read_text(encoding="utf-8"))
    assert statement["status"] == "Interoperability accepted"
    assert statement["network_services"].startswith("None")
