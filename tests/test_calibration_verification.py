"""Whether a millimetre reported by ARIA is a millimetre of patient.

A panoramic unit magnifies, vertically and horizontally by different amounts,
and by different amounts across the arch. Pixel spacing in a DICOM header
describes the detector. Deriving a scale from one drawn object and marking it
validated records a number and shows nothing.

The check these tests cover is the step that shows something: measure a second
object whose true size is known, one the scale was not taken from, and report
how far out the answer comes. A calibration that cannot pass that is a
calibration whose millimetres should not be published, and the error belongs in
the export so a reader can decide for themselves.
"""

from __future__ import annotations

import pytest

from aria.core.units import (
    VERIFICATION_TOLERANCE_PERCENT,
    Calibration,
    CalibrationSource,
)

WHO = "ADM-001"
WHEN = "2026-09-30T09:00:00+00:00"


def _calibrated(mm=25.0, px=250.0):
    """A scale of exactly 0.1 mm per pixel, set from a 25 mm object."""
    return Calibration.from_known_length(mm, px, "25 mm ball, right premolar", WHO, WHEN)


class TestACalibrationKnowsWhetherItHasBeenChecked:

    def test_a_fresh_calibration_is_not_verified(self):
        cal = _calibrated()
        assert cal.is_validated, "The scale itself should be usable"
        assert not cal.is_verified, (
            "A calibration nothing has checked reports itself as verified"
        )
        assert not cal.has_verification

    def test_it_says_so_in_words(self):
        assert "not been checked" in _calibrated().verification_line()

    def test_the_summary_line_says_so_too(self):
        """The line shown beside every quantitative result (FR 009)."""
        assert "Not checked" in _calibrated().summary_line()


class TestTheCheckMeasuresWhatItShould:

    def test_an_exact_check_passes(self):
        cal = _calibrated()
        # 100 px at 0.1 mm per pixel is 10 mm, and the object is 10 mm.
        result = cal.verify(10.0, 100.0, 0.0, "10 mm ball", WHO, WHEN)

        assert result["passed"]
        assert cal.is_verified
        assert result["measured_mm"] == pytest.approx(10.0)
        assert result["error_mm"] == pytest.approx(0.0)
        assert result["error_percent"] == pytest.approx(0.0)

    def test_a_check_that_is_out_reports_how_far(self):
        cal = _calibrated()
        # 108 px reads as 10.8 mm, against an object that is really 10 mm.
        result = cal.verify(10.0, 108.0, 0.0, "10 mm ball at the gonion", WHO, WHEN)

        assert not result["passed"]
        assert not cal.is_verified
        assert result["measured_mm"] == pytest.approx(10.8)
        assert result["error_mm"] == pytest.approx(0.8)
        assert result["error_percent"] == pytest.approx(8.0)

    def test_the_sentence_a_person_reads_carries_the_numbers(self):
        cal = _calibrated()
        cal.verify(10.0, 108.0, 0.0, "check", WHO, WHEN)
        line = cal.verification_line()

        assert "10.8" in line and "+0.8" in line and "8.00" in line
        assert "outside" in line

    def test_a_diagonal_check_uses_both_axes(self):
        """A line drawn at an angle is converted per axis, not through one
        averaged scale, which matters when the two differ."""
        cal = Calibration.from_known_lengths(
            10.0, 100.0,     # vertical: 0.1 mm per pixel
            20.0, 100.0,     # horizontal: 0.2 mm per pixel
            "two references", WHO, WHEN,
        )
        # 30 px across and 40 px down is 6 mm horizontally and 4 mm vertically,
        # so the true length is the hypotenuse of those, not of the pixels.
        cal.verify(7.211, 30.0, 40.0, "diagonal check", WHO, WHEN)

        assert cal.verification_measured_mm == pytest.approx(7.2111, abs=1e-3)
        assert cal.is_verified

    @pytest.mark.parametrize("percent", [0.0, 1.0, -1.0, 1.9, -1.9])
    def test_errors_inside_the_tolerance_pass(self, percent):
        cal = _calibrated()
        px = 100.0 * (1.0 + percent / 100.0)
        cal.verify(10.0, px, 0.0, "check", WHO, WHEN)
        assert cal.is_verified, f"{percent} per cent was rejected"

    @pytest.mark.parametrize("percent", [2.5, -2.5, 8.0, -8.0, 25.0])
    def test_errors_outside_the_tolerance_fail(self, percent):
        cal = _calibrated()
        px = 100.0 * (1.0 + percent / 100.0)
        cal.verify(10.0, px, 0.0, "check", WHO, WHEN)
        assert not cal.is_verified, f"{percent} per cent was accepted"

    def test_the_tolerance_is_tight_enough_to_matter(self):
        """Cortical width runs to a few millimetres, so a tolerance loose
        enough to hide a whole millimetre would be worthless."""
        assert VERIFICATION_TOLERANCE_PERCENT <= 5.0


class TestTheCheckRefusesWhatItCannotMeasure:

    def test_a_check_needs_a_scale_to_check(self):
        with pytest.raises(ValueError, match="no calibration"):
            Calibration().verify(10.0, 100.0, 0.0, "check", WHO, WHEN)

    def test_a_check_object_needs_a_real_size(self):
        with pytest.raises(ValueError, match="must be positive"):
            _calibrated().verify(0.0, 100.0, 0.0, "check", WHO, WHEN)

    def test_a_check_line_needs_length(self):
        with pytest.raises(ValueError, match="no length"):
            _calibrated().verify(10.0, 0.0, 0.0, "check", WHO, WHEN)


class TestEachAxisCanHaveItsOwnScale:
    """A panoramic image needs it: the two magnifications are not the same."""

    def test_two_references_give_two_scales(self):
        cal = Calibration.from_known_lengths(
            10.0, 100.0, 20.0, 100.0, "two references", WHO, WHEN
        )
        assert cal.row_spacing_mm == pytest.approx(0.1)
        assert cal.col_spacing_mm == pytest.approx(0.2)
        assert cal.is_anisotropic
        assert cal.source is CalibrationSource.MANUAL_KNOWN_LENGTH

    def test_a_height_and_a_width_convert_differently(self):
        cal = Calibration.from_known_lengths(
            10.0, 100.0, 20.0, 100.0, "two references", WHO, WHEN
        )
        assert cal.distance_mm(0.0, 100.0) == pytest.approx(10.0)
        assert cal.distance_mm(100.0, 0.0) == pytest.approx(20.0)

    def test_one_reference_still_gives_one_scale(self):
        """The simple path is unchanged for anybody who wants it."""
        cal = _calibrated()
        assert cal.row_spacing_mm == pytest.approx(cal.col_spacing_mm)
        assert not cal.is_anisotropic

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_a_reference_of_no_size_is_refused(self, bad):
        with pytest.raises(ValueError):
            Calibration.from_known_lengths(bad, 100.0, 20.0, 100.0, "x", WHO, WHEN)
        with pytest.raises(ValueError):
            Calibration.from_known_lengths(10.0, 100.0, bad, 100.0, "x", WHO, WHEN)


class TestWhereTheScaleWasEstablished:
    """Horizontal magnification moves across the arch, so distance matters."""

    def test_the_reference_position_is_kept(self):
        cal = Calibration.from_known_lengths(
            10.0, 100.0, 20.0, 100.0, "two references", WHO, WHEN,
            centre_px=(1200.0, 800.0),
        )
        assert cal.reference_centre_px == [1200.0, 800.0]
        assert cal.distance_from_reference_px(1500.0) == pytest.approx(300.0)

    def test_no_position_means_no_claim_about_distance(self):
        assert _calibrated().distance_from_reference_px(1500.0) is None


class TestTheCheckSurvivesBeingStored:

    def test_it_round_trips(self):
        cal = _calibrated()
        cal.verify(10.0, 104.0, 0.0, "10 mm ball, left molar", WHO, WHEN)

        back = Calibration.from_dict(cal.to_dict())

        assert back.verification_length_mm == pytest.approx(10.0)
        assert back.verification_description == "10 mm ball, left molar"
        assert back.verified_by == WHO
        assert back.verification_error_percent == pytest.approx(4.0)
        assert not back.is_verified

    def test_the_exported_form_carries_the_numbers_a_reader_needs(self):
        cal = _calibrated()
        cal.verify(10.0, 104.0, 0.0, "check", WHO, WHEN)
        d = cal.to_dict()

        assert d["verified"] is False
        assert d["verification_measured_mm"] == pytest.approx(10.4)
        assert d["verification_error_mm"] == pytest.approx(0.4)
        assert d["verification_error_percent"] == pytest.approx(4.0)
        assert d["verification_tolerance_percent"] == VERIFICATION_TOLERANCE_PERCENT

    def test_an_unchecked_calibration_exports_as_unchecked(self):
        d = _calibrated().to_dict()
        assert d["verified"] is False
        assert d["verification_measured_mm"] is None
        assert d["verification_error_percent"] is None


class TestTheExportSaysWhetherTheScaleWasEverChecked:

    def test_the_case_row_carries_the_result(self, repo, calibrated_case, annotator, project):
        from tests.conftest import build_annotation_set
        from aria.io.exporters.csv_export import case_row

        data = build_annotation_set(repo, calibrated_case, annotator)
        cal = data.case.calibration

        # The check object is sized so that it comes out four per cent large
        # under whatever scale this case happens to carry, rather than assuming
        # one. The point of the test is that the error reaches the export, not
        # what the fixture's header said.
        measured_mm = cal.distance_mm(104.0, 0.0)
        true_mm = measured_mm / 1.04
        cal.verify(true_mm, 104.0, 0.0, "10 mm ball", WHO, WHEN)

        row = case_row(data, project, annotator)

        assert row["calibration_verified"] is False
        assert row["verification_object_mm"] == pytest.approx(true_mm)
        assert row["verification_measured_mm"] == pytest.approx(measured_mm)
        assert row["verification_error_percent"] == pytest.approx(4.0)
        assert row["verification_description"] == "10 mm ball"

    def test_every_new_column_is_in_the_data_dictionary(self):
        """An exported number nobody can interpret is not evidence (FR 049)."""
        from aria.io.exporters.csv_export import DATA_DICTIONARY

        documented = {n for n, table, *_ in DATA_DICTIONARY if table == "cases"}
        for column in (
            "calibration_verified", "verification_object_mm",
            "verification_measured_mm", "verification_error_mm",
            "verification_error_percent", "verification_description",
        ):
            assert column in documented, f"{column} is exported but undocumented"

    def test_the_columns_are_written_in_order(self):
        from aria.io.exporters.csv_export import CASE_FIELDS

        for column in (
            "calibration_verified", "verification_object_mm",
            "verification_error_percent",
        ):
            assert column in CASE_FIELDS, f"{column} would be dropped from cases.csv"


class TestWhatTheCalibrationNoticeActuallySays:
    """A report from the clinic: the notice reads as though it stops a
    submission, and points at a module that does not exist.

    Pixel spacing in a header describes the detector, so withholding
    millimetres from it is right. Leaving somebody to infer that their case is
    stuck, and then sending them to look for Calibration when the control is
    under Measure, is not.
    """

    def _issue(self, repo, case, annotator, required):
        from aria.core.schema import ProjectSchema
        from aria.core.validation import validate_for_submission
        from tests.conftest import build_annotation_set

        schema = ProjectSchema()
        schema.require_calibration_for_submission = required
        data = build_annotation_set(repo, case, annotator)
        result = validate_for_submission(data, schema)
        issue = next(
            (i for i in result.issues if i.code == "calibration_unvalidated"), None
        )
        return result, issue

    def test_unvalidated_spacing_does_not_block_an_ordinary_project(
        self, repo, imported_case, annotator
    ):
        """The default. Pixel measurements and ratios are recorded either way."""
        repo.confirm_laterality(imported_case.id, annotator.id, "confirmed")
        result, issue = self._issue(
            repo, repo.get_case(imported_case.id), annotator, required=False
        )

        assert issue is not None, "The fixture no longer reproduces the report"
        assert issue.severity == "warning"
        assert result.can_submit, "An unvalidated header is stopping a submission"

    def test_the_notice_says_the_case_can_still_be_submitted(
        self, repo, imported_case, annotator
    ):
        repo.confirm_laterality(imported_case.id, annotator.id, "confirmed")
        _result, issue = self._issue(
            repo, repo.get_case(imported_case.id), annotator, required=False
        )

        assert "can still be submitted" in issue.message
        assert "PMI" in issue.message, (
            "The notice does not say that the ratios are unaffected"
        )

    def test_the_notice_says_so_when_it_does_block(
        self, repo, imported_case, annotator
    ):
        repo.confirm_laterality(imported_case.id, annotator.id, "confirmed")
        result, issue = self._issue(
            repo, repo.get_case(imported_case.id), annotator, required=True
        )

        assert issue.severity == "blocker"
        assert not result.can_submit
        assert "requires a validated calibration" in issue.message

    def test_the_remedy_names_somewhere_that_exists(
        self, repo, imported_case, annotator
    ):
        """It used to say "Open Calibration", and there is no such module."""
        repo.confirm_laterality(imported_case.id, annotator.id, "confirmed")
        _result, issue = self._issue(
            repo, repo.get_case(imported_case.id), annotator, required=False
        )

        assert "Measure" in issue.remedy
        assert "Validate" in issue.remedy
        assert "Calibrate" in issue.remedy
        assert "Open Calibration" not in issue.remedy

    def test_the_remedy_is_reachable_from_the_menu(self, paths, repo, annotator, project, monkeypatch):
        """Both routes the remedy names have to be real actions."""
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.security.auth import AuthService
        from aria.ui.main_window import MainWindow

        config = Config(paths)
        config.settings.first_run_completed = True
        config.settings.tour_completed = True
        config.settings.backup_on_launch = False
        app = QApplication.instance() or QApplication([])
        repo.set_actor(annotator)
        window = MainWindow(
            repo, paths, config,
            AuthService(repo, config.settings).start_session(annotator),
        )
        try:
            assert window.action_calibrate is not None
            assert window.measure_panel.validate_button is not None
        finally:
            window.deleteLater()
            app.processEvents()
