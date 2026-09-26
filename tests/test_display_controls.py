"""The image display controls, and the promise that they change only the view.

Brightness, contrast, magnification and sharpness exist so that a faint
endosteal margin can be found. They would be worth very little if using them
put a measurement in doubt, so the first thing these tests establish is that
the pixels a measurement is taken from are the pixels that arrived, whatever
the sliders are set to.
"""

from __future__ import annotations

import json

import numpy as np
import pytest


@pytest.fixture
def window(paths, repo, annotator, project, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from aria.config import Config
    from aria.security.auth import AuthService

    config = Config(paths)
    config.settings.first_run_completed = True
    config.settings.tour_completed = True
    config.settings.backup_on_launch = False

    app = QApplication.instance() or QApplication([])
    repo.set_actor(annotator)
    session = AuthService(repo, config.settings).start_session(annotator)

    from aria.ui.main_window import MainWindow

    w = MainWindow(repo, paths, config, session)
    # A real viewport, so that fitting to the window produces a real scale
    # rather than the degenerate one an unsized widget reports.
    w.resize(1400, 900)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    w.controller.set_project(project)
    app.processEvents()
    yield w
    w.controller.close_case()
    w.deleteLater()
    app.processEvents()


@pytest.fixture
def open_case(window, importer, project, annotator, sample_dicom):
    from PySide6.QtWidgets import QApplication

    outcome = importer.import_file(str(sample_dicom), project.id, imported_by=annotator.id)
    assert outcome.succeeded, outcome.error_message
    window.open_case(outcome.case.id)
    QApplication.instance().processEvents()
    assert window.controller.image is not None
    return outcome.case


def _drawn(window) -> np.ndarray:
    """The frame as it is drawn on screen right now."""
    return np.asarray(
        window.controller.image.to_display(window.controller.display_settings)
    ).astype(float)


class TestTheControlsChangeTheView:

    def test_brightness_lifts_the_image(self, window, open_case):
        before = _drawn(window).mean()
        window.display_panel.rows["brightness"].slider.setValue(60)
        assert _drawn(window).mean() > before + 10

    def test_contrast_spreads_the_image(self, window, open_case):
        before = _drawn(window).std()
        window.display_panel.rows["contrast"].slider.setValue(260)
        assert _drawn(window).std() > before + 5

    def test_sharpness_drives_the_unsharp_mask(self, window, open_case):
        window.display_panel.rows["sharpness"].slider.setValue(70)
        settings = window.controller.display_settings
        assert settings.filter_name == "unsharp"
        assert settings.filter_strength == pytest.approx(0.70)

    def test_sharpness_at_zero_leaves_no_filter_behind(self, window, open_case):
        window.display_panel.rows["sharpness"].slider.setValue(70)
        window.display_panel.rows["sharpness"].slider.setValue(0)
        assert window.controller.display_settings.filter_name == "none"

    def test_magnification_zooms_the_canvas(self, window, open_case):
        window.display_panel.rows["magnification"].slider.setValue(300)
        assert window.canvas.zoom_factor() == pytest.approx(3.0, abs=0.01)


class TestTheControlsNeverTouchTheData:
    """The property that lets them be used freely (FR 013, AC 003)."""

    def test_stored_pixels_are_untouched_by_every_control(self, window, open_case):
        original = np.array(window.controller.image.pixels, copy=True)

        panel = window.display_panel
        panel.rows["brightness"].slider.setValue(-80)
        panel.rows["contrast"].slider.setValue(380)
        panel.rows["sharpness"].slider.setValue(95)
        panel.rows["magnification"].slider.setValue(600)

        assert np.array_equal(window.controller.image.pixels, original), (
            "A display control altered the pixels a measurement is taken from"
        )

    def test_annotation_coordinates_survive_magnification(self, window, open_case, annotator):
        """Magnifying is a view transform. A point placed before it must sit on
        the same image pixel afterwards."""
        from aria.core.models import Annotation

        annotation = Annotation(
            set_id=window.controller.case_data.annotation_set.id,
            class_key="menton", side="NA", geometry_type="point",
            created_by=annotator.id,
        )
        annotation.set_points([(801.0, 553.0)])
        assert window.controller.add_annotation(annotation)

        window.display_panel.rows["magnification"].slider.setValue(500)
        window.display_panel.rows["brightness"].slider.setValue(70)

        stored = window.repo.get_annotation(annotation.id)
        assert stored.points() == [(801.0, 553.0)]


class TestResetPutsTheImageBack:

    def test_reset_returns_every_control_to_its_starting_value(self, window, open_case):
        panel = window.display_panel
        panel.rows["brightness"].slider.setValue(75)
        panel.rows["contrast"].slider.setValue(300)
        panel.rows["sharpness"].slider.setValue(55)

        window.reset_view()

        assert panel.values()["brightness"] == 0
        assert panel.values()["contrast"] == 100
        assert panel.values()["sharpness"] == 0
        assert window.controller.display_settings.filter_name == "none"

    def test_reset_fits_the_image_to_the_window_again(self, window, open_case):
        panel = window.display_panel
        fitted = panel.values()["magnification"]
        panel.rows["magnification"].slider.setValue(700)
        assert panel.values()["magnification"] == 700

        window.reset_view()
        assert panel.values()["magnification"] == pytest.approx(fitted, abs=2)


class TestThePanelAgreesWithTheImage:
    """Sliders that describe a state the image is not in are worse than none."""

    def test_zooming_elsewhere_moves_the_magnification_slider(self, window, open_case):
        window.canvas.set_zoom(2.5)
        assert window.display_panel.values()["magnification"] == pytest.approx(250, abs=2)

    def test_opening_a_case_shows_that_cases_settings(self, window, open_case):
        window.display_panel.rows["brightness"].slider.setValue(50)
        assert window.controller.display_settings.brightness == pytest.approx(0.5)

        window.controller.close_case()
        window.open_case(open_case.id)

        assert window.display_panel.values()["brightness"] == 50, (
            "The panel does not show the settings the case was left with"
        )

    def test_the_controls_are_unavailable_with_no_case_open(self, window):
        assert not window.display_panel.rows["brightness"].isEnabled()
        assert not window.display_panel.reset_button.isEnabled()

    def test_they_become_available_when_a_case_opens(self, window, open_case):
        assert window.display_panel.rows["brightness"].isEnabled()
        assert window.display_panel.reset_button.isEnabled()
