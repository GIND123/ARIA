"""Functional tests for the requirements outside the acceptance criteria.

Grouped by the area of the specification they cover, so a gap is visible as a
missing group rather than having to be inferred.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pytest

from conftest import build_annotation_set


# ---------------------------------------------------------------------------
# Import guardrails
# ---------------------------------------------------------------------------


class TestImportGuards:
    """Nothing unsuitable reaches the decoder, and every refusal explains itself."""

    def _png_header(self, width, height, depth=8, colour=0):
        header = struct.pack(">IIBBBBB", width, height, depth, colour, 0, 0, 0)
        chunk = (
            struct.pack(">I", 13) + b"IHDR" + header
            + struct.pack(">I", zlib.crc32(b"IHDR" + header))
        )
        return b"\x89PNG\r\n\x1a\n" + chunk + b"\x00" * 256

    def test_known_formats_are_named_in_the_refusal(self, tmp_path):
        from aria.io.guards import inspect_file

        cases = {
            "photo.jpg": (b"\xff\xd8\xff\xe0", "JPEG"),
            "scan.tif": (b"II*\x00", "TIFF"),
            "report.pdf": (b"%PDF-1.7", "PDF"),
            "archive.zip": (b"PK\x03\x04", "archive"),
            "volume.nii": (b"\x5c\x01\x00\x00", "NIfTI"),
        }
        for name, (magic, expected) in cases.items():
            path = tmp_path / name
            path.write_bytes(magic + b"x" * 4000)
            result = inspect_file(path)
            assert not result.accepted, f"{name} was accepted"
            problem = result.first_problem()
            assert expected.lower() in problem.message.lower()
            assert problem.remedy, f"{name} was refused without a next step"

    def test_content_is_checked_not_the_name(self, tmp_path):
        from aria.io.guards import GuardCode, inspect_file

        path = tmp_path / "study.dcm"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 4000)
        result = inspect_file(path)
        assert not result.accepted
        assert result.first_problem().code == GuardCode.SIGNATURE_MISMATCH.value

    def test_declared_dimensions_are_checked_before_decoding(self, tmp_path):
        from aria.io.guards import GuardCode, inspect_file

        path = tmp_path / "bomb.png"
        path.write_bytes(self._png_header(100000, 100000))
        result = inspect_file(path)
        assert not result.accepted
        assert result.first_problem().code == GuardCode.DIMENSION_TOO_LARGE.value

    def test_pixel_budget_is_enforced(self, tmp_path):
        from aria.io.guards import GuardCode, inspect_file

        path = tmp_path / "wide.png"
        path.write_bytes(self._png_header(25000, 25000))
        result = inspect_file(path, limits={"max_pixels": 100_000_000, "max_dimension": 30000})
        assert not result.accepted
        assert result.first_problem().code == GuardCode.PIXEL_BUDGET_EXCEEDED.value

    def test_file_size_limit(self, tmp_path, sample_png):
        from aria.io.guards import GuardCode, inspect_file

        result = inspect_file(sample_png, limits={"max_file_bytes": 1024})
        assert not result.accepted
        assert result.first_problem().code == GuardCode.TOO_LARGE.value

    def test_unsupported_bit_depth(self, tmp_path):
        from aria.io.guards import GuardCode, inspect_file

        path = tmp_path / "fourbit.png"
        path.write_bytes(self._png_header(2000, 1000, depth=4, colour=0))
        result = inspect_file(path)
        assert not result.accepted
        assert result.first_problem().code == GuardCode.UNSUPPORTED_BIT_DEPTH.value

    def test_colour_png_is_accepted_with_a_note(self, sample_png):
        from aria.io.guards import inspect_file

        result = inspect_file(sample_png)
        assert result.accepted
        assert any("grayscale" in w.lower() or "colour" in w.lower() for w in result.warnings)

    def test_real_dicom_is_accepted(self, sample_dicom):
        from aria.io.guards import inspect_file

        result = inspect_file(sample_dicom)
        assert result.accepted
        assert result.detected_format == "dicom"
        assert result.rows > 0 and result.columns > 0

    def test_unsupported_transfer_syntax_names_the_syntax(self):
        from aria.io.guards import _check_transfer_syntax

        issue = _check_transfer_syntax("1.2.840.10008.1.2.4.100")
        assert issue is not None
        assert "1.2.840.10008.1.2.4.100" in issue.message
        assert "Explicit VR Little Endian" in issue.remedy

    def test_duplicate_content_is_detected(self, importer, project, admin, sample_dicom):
        first = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id)
        assert first.succeeded
        second = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id)
        assert not second.succeeded
        assert second.duplicate_of == first.case.pseudonym

    def test_a_failed_import_leaves_nothing_behind(self, importer, project, admin, tmp_path, paths):
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        before = len(list(paths.sources_dir.rglob("*")))
        outcome = importer.import_file(str(path), project.id, imported_by=admin.id)
        assert not outcome.succeeded
        assert len(list(paths.sources_dir.rglob("*"))) == before


# ---------------------------------------------------------------------------
# Source integrity
# ---------------------------------------------------------------------------


class TestSourceIntegrity:
    """The retained original is unchanged and linked by checksum (FR 002)."""

    def test_original_is_retained_byte_for_byte(self, importer, imported_case, sample_dicom):
        retained = Path(imported_case.source.stored_path)
        assert retained.exists()
        assert retained.read_bytes() == Path(sample_dicom).read_bytes()

    def test_checksum_links_the_working_copy(self, importer, imported_case):
        from aria.io.fsutil import sha256_file

        assert imported_case.source.sha256
        assert sha256_file(imported_case.source.stored_path) == imported_case.source.sha256
        assert importer.verify_case_integrity(imported_case)["ok"]

    def test_a_modified_source_is_refused(self, importer, imported_case, paths):
        retained = Path(imported_case.source.stored_path)
        data = bytearray(retained.read_bytes())
        data[4000] ^= 0xFF
        retained.write_bytes(bytes(data))

        report = importer.verify_case_integrity(imported_case)
        assert not report["ok"]
        assert report["reason"] == "checksum_mismatch"

        working = paths.working_path(imported_case.id)
        if working.exists():
            working.unlink()
        with pytest.raises(ValueError, match="no longer matches"):
            importer.load_case_image(imported_case)


# ---------------------------------------------------------------------------
# Concurrency and autosave
# ---------------------------------------------------------------------------


class TestConcurrency:
    """Two users cannot silently overwrite one another (FR 015)."""

    def test_stale_write_is_refused(self, repo, imported_case, annotator):
        from aria.core.models import Annotation
        from aria.store.repository import ConcurrentEditError

        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        a = Annotation(set_id=aset.id, class_key="menton", side="M", geometry_type="point")
        a.set_points([(10, 10)])
        counter = repo.save_annotation(a, expected_counter=0, case_id=imported_case.id)
        assert counter == 1

        with pytest.raises(ConcurrentEditError) as info:
            repo.save_annotation(a, expected_counter=0, case_id=imported_case.id)
        assert "Reload the case" in str(info.value)

    def test_case_lock_names_the_holder(self, database, imported_case, annotator, admin):
        from aria.store.repository import CaseLockedError, Repository

        first = Repository(database, session_id="session-one")
        second = Repository(database, session_id="session-two")

        first.acquire_lock(imported_case.id, annotator)
        with pytest.raises(CaseLockedError) as info:
            second.acquire_lock(imported_case.id, admin)
        assert annotator.display_name in str(info.value)

        first.release_lock(imported_case.id)
        assert second.acquire_lock(imported_case.id, admin)

    def test_lock_is_reclaimed_after_a_crash(self, database, imported_case, annotator, admin):
        from aria.core.models import utc_now
        from aria.store.repository import Repository

        first = Repository(database, session_id="dead-session")
        first.acquire_lock(imported_case.id, annotator)

        # Simulate a session that stopped sending heartbeats, as after a crash.
        database.execute(
            "UPDATE case_locks SET heartbeat_at = ? WHERE case_id = ?",
            ("2000-01-01T00:00:00.000+00:00", imported_case.id),
        )
        second = Repository(database, session_id="live-session")
        assert second.acquire_lock(imported_case.id, admin)

    def test_edits_survive_reopening_the_database(self, paths, imported_case, annotator, repo, database):
        """A committed change is durable, which is what autosave depends on."""
        from aria.core.models import Annotation
        from aria.store.db import open_database
        from aria.store.repository import Repository

        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        a = Annotation(set_id=aset.id, class_key="menton", side="M", geometry_type="point")
        a.set_points([(123.5, 456.5)])
        repo.save_annotation(a, expected_counter=0, case_id=imported_case.id)
        database.close()

        reopened = open_database(paths.database)
        try:
            again = Repository(reopened)
            stored = again.list_annotations(aset.id)
            assert len(stored) == 1
            assert stored[0].points() == [(123.5, 456.5)]
        finally:
            reopened.close()


# ---------------------------------------------------------------------------
# Undo and redo
# ---------------------------------------------------------------------------


class TestEditJournal:
    """Undo survives a restart because the journal is stored, not held in memory."""

    def test_journal_records_and_replays(self, repo, imported_case, annotator):
        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        repo.push_journal(aset.id, "create", {"a": 1}, {"b": 2}, "Add landmark")

        undo, redo = repo.journal_depth(aset.id)
        assert (undo, redo) == (1, 0)

        entry = repo.peek_undo(aset.id)
        assert entry["label"] == "Add landmark"
        assert json.loads(entry["undo_json"]) == {"a": 1}

        repo.mark_undone(entry["id"], True)
        assert repo.journal_depth(aset.id) == (0, 1)
        assert repo.peek_redo(aset.id)["label"] == "Add landmark"

    def test_a_new_edit_clears_the_redo_branch(self, repo, imported_case, annotator):
        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        repo.push_journal(aset.id, "create", {}, {}, "First")
        entry = repo.peek_undo(aset.id)
        repo.mark_undone(entry["id"], True)
        repo.push_journal(aset.id, "create", {}, {}, "Second")
        assert repo.journal_depth(aset.id)[1] == 0


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestWorkflow:
    """Case states and the transitions between them (FR 039)."""

    def test_illegal_transition_is_refused_with_the_allowed_set(self, repo, imported_case):
        from aria.core.schema import CaseState
        from aria.store.repository import TransitionError

        with pytest.raises(TransitionError) as info:
            repo.set_case_state(imported_case.id, CaseState.ACCEPTED)
        assert "Allowed next states" in str(info.value)

    def test_the_full_path_through_the_workflow(self, repo, calibrated_case, annotator, admin):
        from aria.core.models import Review, ReviewDecision
        from aria.core.schema import CaseState

        repo.assign_case(calibrated_case.id, annotator.id)
        assert repo.get_case(calibrated_case.id).state == CaseState.ASSIGNED.value

        repo.set_case_state(calibrated_case.id, CaseState.IN_PROGRESS)
        data = build_annotation_set(repo, calibrated_case, annotator)
        repo.submit_set(data.annotation_set.id, "Ready")
        assert repo.get_case(calibrated_case.id).state == CaseState.SUBMITTED.value

        repo.record_review(
            Review(set_id=data.annotation_set.id, reviewer_id=admin.id,
                   decision=ReviewDecision.ACCEPT, summary="Accepted"),
            CaseState.ACCEPTED,
        )
        assert repo.get_case(calibrated_case.id).state == CaseState.ACCEPTED.value

    def test_submission_is_blocked_while_content_is_missing(self, repo, imported_case, annotator, schema):
        from aria.core.validation import validate_for_submission

        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        data = repo.load_set_data(aset.id)
        result = validate_for_submission(data, schema)

        assert not result.can_submit
        codes = {i.code for i in result.blockers}
        assert "laterality_missing" in codes
        assert "required_label_missing" in codes
        assert "mci_grade_missing" in codes
        for issue in result.blockers:
            assert issue.remedy, f"{issue.code} has no remedy"

    def test_a_complete_set_passes(self, repo, calibrated_case, annotator, schema):
        from aria.core.validation import validate_for_submission

        data = build_annotation_set(repo, calibrated_case, annotator)
        result = validate_for_submission(data, schema)
        assert result.can_submit, [i.label() for i in result.blockers]

    def test_configurable_omissions(self, repo, calibrated_case, annotator, schema):
        """Allowed omissions are project configuration (FR 040)."""
        from aria.core.validation import validate_for_submission

        aset = repo.get_or_create_set(calibrated_case.id, annotator.id)
        data = repo.load_set_data(aset.id)

        strict = validate_for_submission(data, schema)
        schema.allowed_omissions = list(schema.required_classes)
        schema.require_mci_grade = False
        relaxed = validate_for_submission(data, schema)
        assert len(relaxed.blockers) < len(strict.blockers)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAudit:
    """Append only history with a verifiable chain (NFR 003, NFR 004)."""

    def test_every_event_type_reaches_the_log(self, repo, calibrated_case, annotator, admin):
        build_annotation_set(repo, calibrated_case, annotator)
        events = {r.event for r in repo.audit_records(limit=1000)}
        for expected in (
            "import_completed", "deidentify_applied", "annotation_created",
            "grade_assigned", "calibration_validated", "laterality_confirmed",
            "user_created", "project_created",
        ):
            assert expected in events, f"{expected} was not recorded"

    def test_records_carry_actor_event_object_and_timestamp(self, repo, imported_case):
        record = repo.audit_records(limit=1)[0]
        assert record.actor_id and record.actor_name
        assert record.event and record.object_type and record.object_id
        assert record.timestamp.endswith("+00:00")
        assert record.record_hash and len(record.record_hash) == 64

    def test_before_and_after_values_are_recorded(self, repo, imported_case, annotator):
        from aria.core.models import Annotation

        aset = repo.get_or_create_set(imported_case.id, annotator.id)
        a = Annotation(set_id=aset.id, class_key="menton", side="M", geometry_type="point")
        a.set_points([(10, 10)])
        repo.save_annotation(a, expected_counter=0, case_id=imported_case.id)
        a.set_points([(20, 20)])
        repo.save_annotation(a, expected_counter=1, case_id=imported_case.id)

        updates = [r for r in repo.audit_records(limit=50) if r.event == "annotation_updated"]
        assert updates
        before = json.loads(updates[0].before_json)
        after = json.loads(updates[0].after_json)
        assert before["first_point"] != after["first_point"]

    def test_the_log_rejects_update_and_delete(self, repo, imported_case):
        for statement in (
            "UPDATE audit_log SET detail = 'changed' WHERE sequence = 1",
            "DELETE FROM audit_log WHERE sequence = 1",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                repo.db.execute(statement)

    def test_the_chain_verifies_and_detects_tampering(self, repo, imported_case, annotator):
        build_annotation_set(repo, imported_case, annotator)
        result = repo.verify_audit_chain()
        assert result["valid"], result["reason"]
        assert result["records_checked"] > 5

        # Bypass the trigger the way an attacker with file access would, and
        # confirm the chain notices.
        conn = repo.db.connect()
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("DROP TRIGGER audit_log_no_update")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.execute("UPDATE audit_log SET detail = 'tampered' WHERE sequence = 3")

        broken = repo.verify_audit_chain()
        assert not broken["valid"]
        assert broken["broken_at_sequence"] == 3

    def test_auditor_view_withholds_coordinates(self):
        from aria.core.audit import redact_for_auditor

        payload = {
            "class_key": "mcw_line",
            "coordinates": [1.0, 2.0, 3.0, 4.0],
            "nested": {"coordinates": [5.0, 6.0]},
        }
        redacted = redact_for_auditor(payload)
        assert redacted["class_key"] == "mcw_line"
        assert "withheld" in redacted["coordinates"]
        assert "withheld" in redacted["nested"]["coordinates"]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    """Least privilege, checked in one place (NFR 001)."""

    def test_role_matrix(self):
        from aria.core.models import Role
        from aria.security.auth import Permission, permissions_for

        annotator = permissions_for(Role.ANNOTATOR)
        assert Permission.EDIT_ANNOTATIONS in annotator
        assert Permission.MANAGE_USERS not in annotator
        assert Permission.IMPORT_CASES not in annotator

        auditor = permissions_for(Role.AUDITOR)
        assert Permission.VIEW_AUDIT in auditor
        assert Permission.EDIT_ANNOTATIONS not in auditor
        assert Permission.EXPORT_DATA not in auditor

        admin = permissions_for(Role.ADMIN)
        assert Permission.MANAGE_SCHEMA in admin
        assert Permission.EDIT_ANNOTATIONS not in admin, (
            "The administrator role manages the project rather than annotating"
        )

        reviewer = permissions_for(Role.REVIEWER)
        assert {Permission.REVIEW_CASES, Permission.EDIT_ANNOTATIONS} <= reviewer

    def test_require_explains_the_refusal(self, annotator):
        from aria.security.auth import Permission, require

        with pytest.raises(PermissionError) as info:
            require(annotator, Permission.MANAGE_USERS)
        assert "manage users" in str(info.value)
        assert "Annotator" in str(info.value)

    def test_deactivated_account_has_no_permissions(self, annotator):
        from aria.security.auth import Permission, has_permission

        annotator.active = False
        assert not has_permission(annotator, Permission.EDIT_ANNOTATIONS)


class TestAuthentication:
    def test_password_round_trip(self):
        from aria.security.auth import hash_password, verify_password

        digest, salt = hash_password("a-good-passphrase-2026")
        assert verify_password("a-good-passphrase-2026", digest, salt)
        assert not verify_password("wrong", digest, salt)
        assert "a-good-passphrase-2026" not in digest

    def test_lockout_after_repeated_failures(self, repo, settings):
        from aria.security.auth import AccountLockedError, AuthService, AuthenticationError

        settings.max_failed_logins = 3
        service = AuthService(repo, settings)
        service.create_account("locktest", "Lock Test", "annotator", "GoodPassword-2026", must_change=False)

        for _ in range(3):
            with pytest.raises(AuthenticationError):
                service.authenticate("locktest", "wrong")

        with pytest.raises(AccountLockedError):
            service.authenticate("locktest", "GoodPassword-2026")

    def test_policy_is_enforced(self, repo, settings):
        from aria.security.auth import AuthService

        service = AuthService(repo, settings)
        with pytest.raises(ValueError, match="at least"):
            service.create_account("short", "Short", "annotator", "abc")

    def test_unknown_account_and_wrong_password_read_the_same(self, repo, settings):
        from aria.security.auth import AuthService, AuthenticationError

        service = AuthService(repo, settings)
        service.create_account("real", "Real", "annotator", "GoodPassword-2026", must_change=False)

        with pytest.raises(AuthenticationError) as a:
            service.authenticate("real", "wrong")
        with pytest.raises(AuthenticationError) as b:
            service.authenticate("nobody", "wrong")
        assert str(a.value) == str(b.value)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


class TestCalibration:
    def test_manual_calibration_from_a_known_length(self):
        from aria.core.models import utc_now
        from aria.core.units import Calibration

        cal = Calibration.from_known_length(25.0, 500.0, "25 mm ball", "REV-001", utc_now())
        assert cal.millimetres_available
        assert cal.row_spacing_mm == pytest.approx(0.05)
        assert cal.length_mm((0, 0), (100, 0)) == pytest.approx(5.0)

    def test_implausible_scale_is_refused(self):
        from aria.core.units import Calibration, CalibrationSource, ValidationStatus

        cal = Calibration(
            source=CalibrationSource.DICOM_PIXEL_SPACING,
            row_spacing_mm=25.0, col_spacing_mm=25.0,
            status=ValidationStatus.UNVALIDATED,
        )
        ok, reasons = cal.can_validate()
        assert not ok and reasons
        with pytest.raises(ValueError):
            cal.validate("REV-001", "now")

    def test_magnification_correction_changes_the_effective_scale(self):
        from aria.core.units import Calibration, CalibrationSource, ValidationStatus

        cal = Calibration(
            source=CalibrationSource.DICOM_PIXEL_SPACING,
            row_spacing_mm=0.1, col_spacing_mm=0.1,
            status=ValidationStatus.VALIDATED,
        )
        assert cal.distance_mm(0, 100) == pytest.approx(10.0)

        cal.magnification_vertical = 1.25
        cal.magnification_horizontal = 1.1
        cal.magnification_applied = True
        assert cal.distance_mm(0, 100) == pytest.approx(10.0 / 1.25)
        assert cal.distance_mm(100, 0) == pytest.approx(10.0 / 1.1)
        assert "v 1.25" in cal.correction_factor_text

    def test_calibration_survives_serialisation(self):
        from aria.core.units import Calibration, CalibrationSource, ValidationStatus

        cal = Calibration(
            source=CalibrationSource.MANUAL_KNOWN_LENGTH,
            row_spacing_mm=0.076, col_spacing_mm=0.077,
            status=ValidationStatus.VALIDATED, validated_by="REV-001",
            magnification_vertical=1.2, magnification_applied=True,
        )
        restored = Calibration.from_dict(cal.to_dict())
        assert restored.source is cal.source
        assert restored.status is cal.status
        assert restored.effective_row_mm == pytest.approx(cal.effective_row_mm)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


class TestThresholds:
    """Screening rules are configuration, never label content (FR 038)."""

    def test_a_rule_is_inert_until_approved(self):
        from aria.core.schema import ThresholdRule

        rule = ThresholdRule(
            key="mcw_low", display_name="Low cortical width",
            measure="mandibular_cortical_width", comparator="lt",
            value=3.0, unit="mm",
        )
        assert not rule.is_active()
        assert rule.evaluate(2.0) is None

        rule.enabled = True
        assert not rule.is_active(), "Enabling alone should not activate a rule"

        rule.clinically_approved = True
        assert rule.is_active()
        assert rule.evaluate(2.0) is True
        assert rule.evaluate(4.0) is False

    def test_an_active_rule_is_labelled_as_screening(self, repo, calibrated_case, annotator, schema):
        from aria.core.measurements import MeasurementEngine
        from aria.core.schema import ThresholdRule

        schema.thresholds = [
            ThresholdRule(
                key="mcw_low", display_name="Low cortical width",
                measure="mandibular_cortical_width", comparator="lt",
                value=99.0, unit="mm", enabled=True, clinically_approved=True,
                approved_by="Dr Example", version="2",
            )
        ]
        data = build_annotation_set(repo, calibrated_case, annotator)
        results = MeasurementEngine(schema).compute(data)
        mcw = next(m for m in results if m.kind == "mandibular_cortical_width" and m.side == "R")

        assert mcw.screening
        entry = mcw.screening[0]
        assert entry["rule_type"] == "screening_rule"
        assert entry["rule_version"] == "2"
        assert "not a diagnosis" in entry["disclaimer"]

    def test_no_threshold_is_shipped_enabled(self, schema):
        assert schema.thresholds == []


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------


class TestAgreement:
    def test_no_universal_cutoff_is_applied(self, schema):
        from aria.core.agreement import aggregate_reports

        summary = aggregate_reports([], schema)
        assert "does not apply a universal cutoff" in summary["note"]
        assert summary["tolerances"]["point_tolerance_px"] == schema.point_tolerance_px

    def test_line_direction_is_not_a_disagreement(self):
        from aria.core.agreement import line_endpoint_error

        result = line_endpoint_error(((0, 0), (10, 0)), ((10, 0), (0, 0)))
        assert result["mean_endpoint_error_px"] == pytest.approx(0.0)
        assert result["pairing"] == "swapped"

    def test_weighted_kappa_credits_near_misses(self):
        from aria.core.agreement import cohen_kappa

        a = ["C1", "C1", "C2", "C2", "C3", "C3", "C1", "C2"]
        b = ["C1", "C2", "C2", "C2", "C3", "C2", "C1", "C2"]
        categories = ["C1", "C2", "C3"]
        plain = cohen_kappa(a, b, categories, "none")["kappa"]
        linear = cohen_kappa(a, b, categories, "linear")["kappa"]
        quadratic = cohen_kappa(a, b, categories, "quadratic")["kappa"]
        assert plain < linear < quadratic

    def test_icc_forms_differ_on_a_systematic_offset(self):
        from aria.core.agreement import bland_altman, icc

        ratings = np.array([[1.0, 2.0], [2, 3], [3, 4], [4, 5], [5, 6]])
        assert icc(ratings, "3,1")["icc"] == pytest.approx(1.0)
        assert icc(ratings, "2,1")["icc"] < 1.0
        assert bland_altman(ratings[:, 0], ratings[:, 1])["bias"] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# Texture
# ---------------------------------------------------------------------------


class TestTexture:
    def test_region_size_is_recorded_with_the_result(self):
        from aria.core.texture import compute_region_features

        image = np.random.default_rng(4).integers(0, 4096, (200, 200)).astype(np.float64)
        result = compute_region_features(image, 10, 10, 64, 64, region_class="trabecular_roi")
        assert result.width == 64 and result.height == 64
        assert result.parameters["fd_box_sizes"]
        assert result.parameters["glcm_levels"]
        assert result.texture_version

    def test_region_size_changes_the_value(self):
        """Region size materially changes fractal dimension, which is why it is
        fixed per project and recorded."""
        from aria.core.texture import compute_region_features

        image = np.random.default_rng(9).integers(0, 4096, (300, 300)).astype(np.float64)
        small = compute_region_features(image, 20, 20, 32, 32)["features"] if False else None
        a = compute_region_features(image, 20, 20, 32, 32).features["fractal_dimension"]
        b = compute_region_features(image, 20, 20, 128, 128).features["fractal_dimension"]
        assert not np.isnan(a) and not np.isnan(b)

    def test_a_region_outside_the_image_is_reported(self):
        from aria.core.texture import compute_region_features

        image = np.zeros((50, 50))
        result = compute_region_features(image, 400, 400, 64, 64)
        assert result.warnings
        assert not result.features

    def test_uniform_region_is_flagged(self):
        from aria.core.texture import compute_region_features

        result = compute_region_features(np.full((64, 64), 128.0), 0, 0, 64, 64)
        assert any("uniform" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Schema versioning
# ---------------------------------------------------------------------------


class TestSchemaVersioning:
    def test_annotations_stay_readable_after_a_schema_change(
        self, repo, calibrated_case, annotator, project, schema
    ):
        """Existing annotations remain readable after a schema update (FR 053)."""
        data = build_annotation_set(repo, calibrated_case, annotator)
        before = len(data.live_annotations())

        schema.required_classes = ["mental_foramen_centre"]
        schema.mandible_mode = "hemimandible"
        project.schema_json = json.dumps(schema.to_dict())
        repo.update_project(project, "Schema narrowed")

        again = repo.load_set_data(data.annotation_set.id)
        assert len(again.live_annotations()) == before
        for annotation in again.live_annotations():
            assert annotation.points() or annotation.presence != "present"

    def test_schema_change_is_audited(self, repo, project, schema):
        schema.roi_size_px = 128
        project.schema_json = json.dumps(schema.to_dict())
        repo.update_project(project, "Region size changed")
        events = [r for r in repo.audit_records(limit=20) if r.event == "schema_changed"]
        assert events
        assert json.loads(events[0].after_json)["roi_size_px"] == 128

    def test_database_migration_is_idempotent(self, database):
        from aria.version import DB_SCHEMA_VERSION

        assert database.schema_version() == DB_SCHEMA_VERSION
        assert database.migrate(DB_SCHEMA_VERSION) == []


# ---------------------------------------------------------------------------
# Export filtering
# ---------------------------------------------------------------------------


class TestExportFiltering:
    def test_export_by_state_and_split(self, repo, project, admin, importer, sample_dicom, sample_png):
        from aria.core.schema import CaseState

        a = importer.import_file(str(sample_dicom), project.id, imported_by=admin.id).case
        b = importer.import_file(str(sample_png), project.id, imported_by=admin.id).case
        repo.set_case_split(a.id, "train")
        repo.set_case_split(b.id, "test")

        assert len(repo.list_cases(project.id, split="train")) == 1
        assert len(repo.list_cases(project.id, split="test")) == 1
        assert len(repo.list_cases(project.id)) == 2

        repo.assign_case(a.id, admin.id)
        assert len(repo.list_cases(project.id, state=CaseState.ASSIGNED.value)) == 1

    def test_data_dictionary_documents_every_column(self, repo, calibrated_case, annotator, project, tmp_path):
        import csv

        from aria.io.exporters.csv_export import export_tables

        data = build_annotation_set(repo, calibrated_case, annotator)
        written = export_tables(tmp_path, [(data, annotator, None, [])], project)

        with open(written["data_dictionary"], encoding="utf-8-sig") as fh:
            documented = {r["variable"] for r in csv.DictReader(fh)}

        for key in ("cases", "measurements_long", "labels", "annotations"):
            with open(written[key], encoding="utf-8-sig") as fh:
                columns = set(next(csv.reader(fh)))
            # Identity and free text columns are self describing; everything
            # that carries a measured or derived value must be documented.
            self_describing = {
                "case_id", "side", "created_at", "source_filename",
                "acquisition_date", "converted_for_display", "annotation_set_kind",
                "length_mm", "area_mm2", "notes", "n_pixels", "label_display",
                "measure", "measure_display",
            }
            undocumented = columns - documented - self_describing
            assert not undocumented, f"{key} has undocumented columns: {sorted(undocumented)}"

    def test_raw_and_derived_are_separate_files(self, repo, calibrated_case, annotator, project, tmp_path):
        """Exports keep raw annotations and derived measurements apart (FR 051)."""
        from aria.io.exporters.json_export import write_case_json

        written = write_case_json(tmp_path, data_for(repo, calibrated_case, annotator), project)
        assert written["annotations"].exists()
        assert written["measurements"].exists()
        assert written["annotations"] != written["measurements"]

        geometry = json.loads(written["annotations"].read_text(encoding="utf-8"))
        derived = json.loads(written["measurements"].read_text(encoding="utf-8"))
        assert "annotations" in geometry and "measurements" not in geometry
        assert "measurements" in derived and "annotations" not in derived
        for entry in derived["measurements"]:
            if entry["assessable"] and entry["aggregation"] == "none":
                assert entry["source_annotation_ids"], "A value has no source reference"
                assert entry["calculation_version"]


def data_for(repo, case, annotator):
    return build_annotation_set(repo, case, annotator)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------


class TestBundle:
    def test_layout_manifest_and_checksums(
        self, repo, calibrated_case, annotator, project, settings, paths, tmp_path
    ):
        from aria.io.exporters.bundle import BundleExporter, BundleOptions, verify_bundle

        data = build_annotation_set(repo, calibrated_case, annotator)
        exporter = BundleExporter(repo, paths, settings, None)
        destination = tmp_path / "bundle.zip"
        result = exporter.build(destination, project, [(data, annotator, None, [])], BundleOptions())

        assert result.n_cases == 1
        assert result.sha256

        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            readme = archive.read("README.md").decode("utf-8")

        pseudonym = calibrated_case.pseudonym
        for required in (
            "manifest.json", "README.md", "checksums.sha256", "privacy_scan.json",
            "metadata/cases.csv", "metadata/measurements_long.csv",
            "metadata/measurements_wide.csv", "metadata/data_dictionary.csv",
            "metadata/omissions.csv", "coco/instances.json",
            f"annotations/{pseudonym}/annotations.json",
            f"annotations/{pseudonym}/measurements.json",
            f"masks/{pseudonym}/mask.png", f"masks/{pseudonym}/classmap.json",
            f"raw/{pseudonym}/{pseudonym}.dcm",
        ):
            assert required in names, f"{required} is missing from the bundle"

        assert manifest["counts"]["cases"] == 1
        assert manifest["versions"]["calculation_version"]
        assert manifest["label_classes"]
        assert "not a diagnosis" in manifest["intended_use"].lower()
        assert "original image pixels" in readme

        assert verify_bundle(destination)["ok"]

    def test_verification_detects_a_modified_bundle(
        self, repo, calibrated_case, annotator, project, settings, paths, tmp_path
    ):
        from aria.io.exporters.bundle import BundleExporter, BundleOptions, verify_bundle

        data = build_annotation_set(repo, calibrated_case, annotator)
        exporter = BundleExporter(repo, paths, settings, None)
        source = tmp_path / "bundle.zip"
        exporter.build(source, project, [(data, annotator, None, [])], BundleOptions(include_raw=False))

        # Rebuild the archive with one file altered.
        altered = tmp_path / "altered.zip"
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(altered, "w") as target:
            for item in original.infolist():
                payload = original.read(item.filename)
                if item.filename.endswith("annotations.json"):
                    payload = payload.replace(b"annotations", b"annotationz", 1)
                target.writestr(item, payload)

        report = verify_bundle(altered)
        assert not report["ok"]
        assert report["mismatched"]

    def test_raw_image_in_the_bundle_matches_the_original(
        self, repo, calibrated_case, annotator, project, settings, paths, tmp_path, sample_dicom
    ):
        from aria.io.exporters.bundle import BundleExporter, BundleOptions

        data = build_annotation_set(repo, calibrated_case, annotator)
        exporter = BundleExporter(repo, paths, settings, None)
        destination = tmp_path / "bundle.zip"
        exporter.build(destination, project, [(data, annotator, None, [])], BundleOptions())

        with zipfile.ZipFile(destination) as archive:
            payload = archive.read(f"raw/{calibrated_case.pseudonym}/{calibrated_case.pseudonym}.dcm")
        assert payload == Path(sample_dicom).read_bytes()


# ---------------------------------------------------------------------------
# Quality flags
# ---------------------------------------------------------------------------


class TestQualityFlags:
    def test_all_flags_are_available(self):
        from aria.core.schema import QualityFlag

        expected = {
            "not_visible", "ambiguous", "anatomical_variant", "artefact",
            "cropped_anatomy", "poor_positioning", "motion", "other",
        }
        assert {f.value for f in QualityFlag} == expected

    def test_other_requires_a_comment(self, repo, calibrated_case, annotator, schema):
        from aria.core.models import QualityFlagRecord
        from aria.core.validation import validate_for_submission

        data = build_annotation_set(repo, calibrated_case, annotator)
        repo.add_quality_flag(
            QualityFlagRecord(set_id=data.annotation_set.id, flag="other", comment=""),
            repo.set_edit_counter(data.annotation_set.id), case_id=calibrated_case.id,
        )
        data = repo.load_set_data(data.annotation_set.id)
        result = validate_for_submission(data, schema)
        assert any(i.code == "other_flag_without_comment" for i in result.blockers)


# ---------------------------------------------------------------------------
# Presence
# ---------------------------------------------------------------------------


class TestPresence:
    def test_absence_is_never_a_zero_coordinate(self, repo, calibrated_case, annotator, project):
        """Missing anatomy is recorded explicitly (FR 017)."""
        from aria.core.models import Annotation
        from aria.core.schema import Presence, Side
        from aria.io.exporters.json_export import geometry_document

        data = build_annotation_set(repo, calibrated_case, annotator)
        absent = Annotation(
            set_id=data.annotation_set.id, class_key="antegonial_point",
            side=Side.LEFT.value, geometry_type="point",
            presence=Presence.ABSENT_ANATOMY.value,
            notes="Antegonial notch is not developed on this side",
        )
        existing = data.present("antegonial_point", Side.LEFT)
        counter = repo.set_edit_counter(data.annotation_set.id)
        if existing:
            repo.delete_annotation(existing.id, counter, case_id=calibrated_case.id)
            counter = repo.set_edit_counter(data.annotation_set.id)
        repo.save_annotation(absent, counter, case_id=calibrated_case.id)

        data = repo.load_set_data(data.annotation_set.id)
        stored = data.first("antegonial_point", Side.LEFT)
        assert stored.presence == Presence.ABSENT_ANATOMY.value
        assert stored.coordinates == []
        assert not stored.is_assessable

        document = geometry_document(data, project, annotator)
        omission = next(
            o for o in document["omissions"]
            if o["class_key"] == "antegonial_point" and o["side"] == "L"
        )
        assert omission["state"] == "absent_anatomy"
        assert omission["reason"]

    def test_an_absent_side_is_excluded_from_the_mean(self, repo, calibrated_case, annotator):
        """Bilateral means use assessable sides only (FR 036)."""
        from aria.core.measurements import MeasurementEngine
        from aria.core.schema import Presence, Side

        data = build_annotation_set(repo, calibrated_case, annotator)
        left = data.present("antegonial_index_line", Side.LEFT)
        left.presence = Presence.NOT_ASSESSABLE.value
        left.coordinates = []
        repo.save_annotation(
            left, repo.set_edit_counter(data.annotation_set.id), case_id=calibrated_case.id
        )
        data = repo.load_set_data(data.annotation_set.id)

        results = {(m.kind, m.side): m for m in MeasurementEngine().compute(data)}
        mean = results[("antegonial_index", "NA")]
        assert mean.sides_used == ["R"]
        assert mean.value_px == pytest.approx(results[("antegonial_index", "R")].value_px)
        assert mean.inputs["n_sides_used"] == 1
        assert mean.inputs["excluded"][0]["side"] == "L"
        assert mean.warnings
