"""Reading a number off the image, and choosing what is on it.

Two reports, both about the same thing: being able to see what you are doing.

The ruler drew a line between two clicks and then threw it away. The value
appeared once in the status bar and was gone, so a distance could be measured
but never read back, checked or exported. A measurement that cannot be read
twice is not a measurement.

The second: every object stayed on the image whether or not it was wanted
there. Hiding one was possible, but only by right clicking a row in a list, so
on a radiograph carrying both cortical borders, four index lines, six landmarks
a side and a grading region, there was no practical way to look at one thing.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from aria.core.models import Annotation
from aria.core.schema import MEASUREMENT_CLASSES, Side
from aria.core.units import Calibration

WHO = "ANN-001"
WHEN = "2026-10-01T09:00:00+00:00"


@pytest.fixture
def window(paths, repo, annotator, project, monkeypatch):
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
    session = AuthService(repo, config.settings).start_session(annotator)

    w = MainWindow(repo, paths, config, session)
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
def open_case(window, project, annotator, sample_dicom):
    from PySide6.QtWidgets import QApplication

    outcome = window.controller.importer().import_file(
        str(sample_dicom), project.id, imported_by=annotator.id
    )
    assert outcome.succeeded, outcome.error_message
    window.open_case(outcome.case.id)
    QApplication.instance().processEvents()
    return outcome.case


def _landmarks(window, keys):
    """Place a few objects so there is something to show and hide."""
    set_id = window.controller.case_data.annotation_set.id
    made = []
    for index, (key, side) in enumerate(keys):
        annotation = Annotation(
            set_id=set_id, class_key=key, side=side, geometry_type="point",
        )
        annotation.set_points([(100.0 + index * 25, 500.0)])
        assert window.controller.add_annotation(annotation)
        made.append(annotation.id)
    return made


class TestAMeasurementIsKept:

    def test_the_ruler_leaves_something_behind(self, window, open_case):
        """The defect: the line and its value disappeared on the second click."""
        before = len(window.controller.case_data.live_annotations())

        window._on_ruler(300.0, (100.0, 200.0), (400.0, 200.0))

        after = window.controller.case_data.live_annotations()
        assert len(after) == before + 1
        measurement = [a for a in after if a.class_key == "free_measurement"]
        assert measurement, "The ruler drew a line and kept nothing"
        assert measurement[0].points() == [(100.0, 200.0), (400.0, 200.0)]

    def test_it_survives_closing_and_reopening_the_case(self, window, open_case):
        window._on_ruler(300.0, (100.0, 200.0), (400.0, 200.0))
        window.controller.close_case()
        window.open_case(open_case.id)

        kept = [
            a for a in window.controller.case_data.live_annotations()
            if a.class_key == "free_measurement"
        ]
        assert len(kept) == 1

    def test_its_value_is_shown_in_pixels_without_a_scale(self, window, open_case):
        window._on_ruler(300.0, (100.0, 200.0), (400.0, 200.0))
        measurement = [
            a for a in window.controller.case_data.live_annotations()
            if a.class_key == "free_measurement"
        ][0]

        label = window._measurement_label(measurement)
        assert "300" in label and "px" in label

    def test_its_value_becomes_millimetres_once_calibrated(self, window, open_case):
        window._on_ruler(300.0, (100.0, 200.0), (400.0, 200.0))
        measurement = [
            a for a in window.controller.case_data.live_annotations()
            if a.class_key == "free_measurement"
        ][0]

        window.controller.set_calibration(
            Calibration.from_known_length(30.0, 300.0, "ref", WHO, WHEN), "test"
        )

        assert window._measurement_label(measurement) == "30.00 mm"

    def test_the_long_form_says_whether_the_scale_was_checked(self, window, open_case):
        window._on_ruler(300.0, (100.0, 200.0), (400.0, 200.0))
        measurement = [
            a for a in window.controller.case_data.live_annotations()
            if a.class_key == "free_measurement"
        ][0]

        calibration = Calibration.from_known_length(30.0, 300.0, "ref", WHO, WHEN)
        window.controller.set_calibration(calibration, "test")
        assert "unchecked" in window.controller.measurement_text(measurement)

        calibration.verify(10.0, 100.0, 0.0, "check", WHO, WHEN)
        window.controller.set_calibration(calibration, "checked")
        assert "unchecked" not in window.controller.measurement_text(measurement)

    def test_only_measurements_carry_a_value_on_the_image(self, window, open_case):
        """An anatomical landmark is not a number to be read off."""
        made = _landmarks(window, [("menton", "NA")])
        landmark = next(
            a for a in window.controller.case_data.live_annotations()
            if a.id == made[0]
        )
        assert window._measurement_label(landmark) == ""

    def test_an_area_is_measured_as_an_area(self, window, open_case):
        set_id = window.controller.case_data.annotation_set.id
        region = Annotation(
            set_id=set_id, class_key="free_area", side="NA",
            geometry_type="polygon",
        )
        region.set_points([(0.0, 0.0), (100.0, 0.0), (100.0, 50.0), (0.0, 50.0)])
        assert window.controller.add_annotation(region)

        value = window.controller.measurement_value(region)
        assert value["kind"] == "area"
        assert value["pixels"] == pytest.approx(5000.0)
        assert "²" in window._measurement_label(region)

    def test_an_area_converts_through_both_axes(self, window, open_case):
        """Each axis has its own scale, so an area is not one scale squared."""
        set_id = window.controller.case_data.annotation_set.id
        region = Annotation(
            set_id=set_id, class_key="free_area", side="NA",
            geometry_type="polygon",
        )
        region.set_points([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])
        window.controller.add_annotation(region)

        window.controller.set_calibration(
            Calibration.from_known_lengths(
                10.0, 100.0,    # 0.1 mm per pixel down
                20.0, 100.0,    # 0.2 mm per pixel across
                "two references", WHO, WHEN,
            ),
            "anisotropic",
        )

        value = window.controller.measurement_value(region)
        assert value["millimetres"] == pytest.approx(10000 * 0.1 * 0.2)

    def test_measurements_are_their_own_label_classes(self):
        """So they export, undo and hide like everything else."""
        from aria.core.schema import CLASS_BY_KEY

        for key in MEASUREMENT_CLASSES:
            assert key in CLASS_BY_KEY


class TestChoosingWhatIsOnTheImage:

    def test_every_object_has_its_own_tick(self, window, open_case):
        _landmarks(window, [("menton", "NA"), ("gonion", "R"), ("gonion", "L")])
        panel = window.annotate_panel
        panel.refresh_objects()

        assert panel.object_list.count() == 3
        for i in range(panel.object_list.count()):
            item = panel.object_list.item(i)
            assert item.flags() & Qt.ItemIsUserCheckable
            assert item.checkState() == Qt.Checked

    def test_unticking_takes_the_object_off_the_image(self, window, open_case):
        from PySide6.QtWidgets import QApplication

        made = _landmarks(window, [("menton", "NA"), ("gonion", "R")])
        panel = window.annotate_panel
        panel.refresh_objects()

        target = panel.object_list.item(0).data(Qt.UserRole)
        panel.object_list.item(0).setCheckState(Qt.Unchecked)
        QApplication.instance().processEvents()
        QApplication.instance().processEvents()

        stored = {a.id: a for a in window.controller.case_data.live_annotations()}
        assert stored[target].hidden
        assert not window.canvas._items[target].isVisible()
        assert target in made

    def test_hiding_does_not_delete(self, window, open_case):
        from PySide6.QtWidgets import QApplication

        _landmarks(window, [("menton", "NA")])
        panel = window.annotate_panel
        panel.refresh_objects()
        panel.object_list.item(0).setCheckState(Qt.Unchecked)
        QApplication.instance().processEvents()
        QApplication.instance().processEvents()

        assert len(window.controller.case_data.live_annotations()) == 1

    def test_hide_all_and_show_all(self, window, open_case):
        _landmarks(window, [("menton", "NA"), ("gonion", "R"), ("gonion", "L")])
        panel = window.annotate_panel

        panel.set_all_visible(False)
        assert all(a.hidden for a in window.controller.case_data.live_annotations())

        panel.set_all_visible(True)
        assert not any(a.hidden for a in window.controller.case_data.live_annotations())

    def test_isolating_shows_only_what_is_selected(self, window, open_case):
        _landmarks(window, [("menton", "NA"), ("gonion", "R"), ("gonion", "L")])
        panel = window.annotate_panel
        panel.refresh_objects()
        panel.object_list.item(1).setSelected(True)

        panel.isolate_selected()

        visible = [
            a for a in window.controller.case_data.live_annotations()
            if not a.hidden
        ]
        assert len(visible) == 1

    def test_isolating_nothing_says_so_rather_than_hiding_everything(
        self, window, open_case
    ):
        _landmarks(window, [("menton", "NA"), ("gonion", "R")])
        panel = window.annotate_panel
        panel.refresh_objects()
        panel.object_list.clearSelection()

        messages = []
        window.controller.status_message.connect(lambda m, _t: messages.append(m))
        panel.isolate_selected()

        assert not any(a.hidden for a in window.controller.case_data.live_annotations())
        assert messages and "select" in messages[-1].lower()

    def test_the_tick_reflects_a_state_set_elsewhere(self, window, open_case):
        """Hiding from the context menu has to tick the box too."""
        made = _landmarks(window, [("menton", "NA")])
        window.controller.set_annotation_flags(made[0], hidden=True)
        window.annotate_panel.refresh_objects()

        assert window.annotate_panel.object_list.item(0).checkState() == Qt.Unchecked


class TestTheViewFollowsTheSide:
    """Choosing a side used to change only the label the next object got."""

    def _centre_x(self, window):
        canvas = window.canvas
        return canvas.mapToScene(canvas.viewport().rect().center()).x()

    def test_a_magnified_view_moves_to_the_chosen_side(self, window, open_case):
        window.canvas.set_zoom(2.5)
        window._last_focused_side = None
        columns = window.controller.image.columns

        window.annotate_panel.set_side(Side.RIGHT)
        right = self._centre_x(window)
        window.annotate_panel.set_side(Side.LEFT)
        left = self._centre_x(window)

        assert right < columns * 0.5, "Patient right is the left of the image"
        assert left > columns * 0.5
        assert left > right

    def test_the_midline_is_the_middle(self, window, open_case):
        window.canvas.set_zoom(2.5)
        window._last_focused_side = None
        window.annotate_panel.set_side(Side.MIDLINE)

        assert self._centre_x(window) == pytest.approx(
            window.controller.image.columns * 0.5, rel=0.05
        )

    def test_nothing_moves_when_the_whole_image_is_visible(self, window, open_case):
        """There is nowhere to move to, and jumping would be noise."""
        window.canvas.fit_to_window()
        window.annotate_panel.set_side(Side.RIGHT)
        before = self._centre_x(window)
        window.annotate_panel.set_side(Side.LEFT)

        assert self._centre_x(window) == pytest.approx(before, abs=1.0)

    def test_the_position_within_a_side_is_carried_across(self, window, open_case):
        """Leaving the right antegonial region should arrive at the left one,
        not at the middle of the left side."""
        columns = window.controller.image.columns
        window.canvas.set_zoom(2.5)
        window._last_focused_side = None
        window.annotate_panel.set_side(Side.RIGHT)

        centre_y = window.canvas.mapToScene(
            window.canvas.viewport().rect().center()
        ).y()
        window.canvas.centre_on_scene(columns * 0.28 - 300.0, centre_y)

        window.annotate_panel.set_side(Side.LEFT)

        # Mirrored: 300 further from the midline on the right becomes 300
        # further from the midline on the left.
        assert self._centre_x(window) == pytest.approx(columns * 0.72 + 300.0, abs=2.0)


class TestFinishingASideMovesToTheOther:

    def _required_sided(self, window):
        schema = window.controller.schema
        required = set(schema.required_classes)
        return [
            c for c in schema.active_classes()
            if c.side_scoped and c.key in required
        ]

    def _complete_side(self, window, side):
        from PySide6.QtWidgets import QApplication

        set_id = window.controller.case_data.annotation_set.id
        for index, cls in enumerate(self._required_sided(window)):
            annotation = Annotation(
                set_id=set_id, class_key=cls.key, side=side.value,
                geometry_type=cls.geometry.value,
            )
            points = [(100.0 + index * 5, 500.0), (300.0 + index * 5, 520.0)]
            if cls.geometry.value == "point":
                points = points[:1]
            elif cls.geometry.value in ("polyline", "polygon"):
                points = points + [(500.0, 540.0)]
            annotation.set_points(points)
            window.controller.add_annotation(annotation)
        QApplication.instance().processEvents()
        QApplication.instance().processEvents()

    def test_a_side_is_not_complete_until_it_is(self, window, open_case):
        window.annotate_panel.set_side(Side.RIGHT)
        assert not window.annotate_panel.side_is_complete(Side.RIGHT)

    def test_finishing_the_right_moves_to_the_left(self, window, open_case):
        panel = window.annotate_panel
        panel.set_side(Side.RIGHT)

        self._complete_side(window, Side.RIGHT)

        assert panel.side_is_complete(Side.RIGHT)
        assert panel.active_side is Side.LEFT, (
            "Finishing one side still leaves the annotator to remember the other"
        )

    def test_it_says_that_it_moved(self, window, open_case):
        messages = []
        window.controller.status_message.connect(lambda m, _t: messages.append(m))
        panel = window.annotate_panel
        panel.set_side(Side.RIGHT)

        self._complete_side(window, Side.RIGHT)

        assert any("complete" in m and "left" in m.lower() for m in messages), messages

    def test_it_happens_once_rather_than_on_every_later_edit(self, window, open_case):
        """Editing a finished side should not keep throwing the view across."""
        from PySide6.QtWidgets import QApplication

        panel = window.annotate_panel
        panel.set_side(Side.RIGHT)
        self._complete_side(window, Side.RIGHT)
        assert panel.active_side is Side.LEFT

        panel.set_side(Side.RIGHT)
        _landmarks(window, [("menton", "NA")])
        QApplication.instance().processEvents()
        QApplication.instance().processEvents()

        assert panel.active_side is Side.RIGHT, "It moved the annotator again"
