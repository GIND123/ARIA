"""One person, one sign in, from import through annotation to export.

Every check here came from the same report: a case was opened and worked on,
and then the next image could not be imported and the finished one could not be
exported. The cause was a role list that split one person's job across three
accounts. An annotator now does the whole job, which is why this file drives an
ordinary annotator account rather than a special one.

The rest of the file covers what that same session has to keep doing: showing
what an import produced even when a case is already open, keeping the side
panels reachable, and putting every labelling gesture on disk as it happens.
"""

from __future__ import annotations

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
    w.resize(1400, 900)
    w.setAttribute(Qt.WA_DontShowOnScreen, True)
    w.show()
    w.controller.set_project(project)
    w.case_browser.reload_projects()
    app.processEvents()
    yield w
    w.controller.close_case()
    w.deleteLater()
    app.processEvents()


class TestOneAccountCoversTheWholeJob:
    """The report: import and export were greyed out while a case was open."""

    def test_import_and_export_are_available_while_a_case_is_open(
        self, window, importer, project, annotator, sample_dicom
    ):
        outcome = importer.import_file(str(sample_dicom), project.id, imported_by=annotator.id)
        window.open_case(outcome.case.id)
        assert window.controller.case_data is not None

        assert window.action_import.isEnabled(), "Import is greyed out with a case open"
        assert window.action_import_folder.isEnabled()
        assert window.action_export.isEnabled(), "Export is greyed out with a case open"
        assert window.action_bundle.isEnabled()

    def test_the_open_case_is_editable_rather_than_read_only(
        self, window, importer, project, annotator, sample_dicom
    ):
        outcome = importer.import_file(str(sample_dicom), project.id, imported_by=annotator.id)
        window.open_case(outcome.case.id)

        assert not window.controller.read_only, window.controller.read_only_reason

    def test_every_module_the_job_needs_is_offered(self, window):
        offered = {
            window.module_combo.itemData(i)
            for i in range(window.module_combo.count())
        }
        assert {"cases", "annotate", "measure", "export"} <= offered


class TestImportingWithACaseAlreadyOpen:
    """Importing a second case used to look as though it had done nothing."""

    def test_the_imported_case_is_shown_and_selected(
        self, window, project, annotator, sample_dicom, sample_png, monkeypatch
    ):
        from PySide6.QtWidgets import QApplication

        first = window.controller.importer().import_file(
            str(sample_dicom), project.id, imported_by=annotator.id
        )
        window.open_case(first.case.id)
        window.set_module("annotate")

        # The import dialog is driven by hand here, because the file chooser in
        # front of it cannot be answered without a person.
        class _Dialog:
            def __init__(self, *a, **k):
                self.summary = None

            def exec(self):
                self.summary = window.controller.importer().import_files(
                    [str(sample_png)], project.id, imported_by=annotator.id
                )
                return 1

        monkeypatch.setattr("aria.ui.dialogs.import_dialog.ImportDialog", _Dialog)
        window._run_import([str(sample_png)])
        QApplication.instance().processEvents()

        assert window.module_stack.currentWidget() is window.case_browser, (
            "The import finished on a module that does not show what arrived"
        )
        assert window.case_browser.selected_case_id(), "Nothing is selected to open"
        assert window.controller.case_data is not None, (
            "The case that was open was closed by an import"
        )
        assert window.controller.case_data.case.id == first.case.id


class TestThePanelsStayReachable:
    """The report: the annotation sidebar appeared, then vanished."""

    def test_a_hidden_module_panel_comes_back_on_restore(self, window):
        window.module_dock.hide()
        assert window.module_dock.isHidden()

        window._recover_unreachable_panels()

        assert not window.module_dock.isHidden(), (
            "A session that starts with the module panel hidden has no case "
            "list and no annotation tools"
        )

    def test_a_panel_floated_off_every_screen_is_brought_back(self, window):
        from PySide6.QtCore import QRect

        window.module_dock.setFloating(True)
        window.module_dock.setGeometry(QRect(-9000, -9000, 300, 400))

        window._recover_unreachable_panels()

        assert not window.module_dock.isFloating(), (
            "The panel is still floating somewhere no screen reaches"
        )
        assert not window.module_dock.isHidden()

    def test_reset_puts_both_panels_back(self, window):
        window.module_dock.hide()
        window.display_dock.hide()
        window.display_dock.setFloating(True)

        window.reset_panel_layout()

        for dock in (window.module_dock, window.display_dock):
            assert not dock.isHidden()
            assert not dock.isFloating()

    def test_a_panel_docked_where_it_was_left_is_not_disturbed(self, window):
        """Recovery is for arrangements that cannot be used, not for every
        arrangement that is not the default."""
        from PySide6.QtCore import Qt

        window.addDockWidget(Qt.RightDockWidgetArea, window.module_dock)
        window._recover_unreachable_panels()

        assert window.dockWidgetArea(window.module_dock) == Qt.RightDockWidgetArea


class TestEveryGestureIsOnDiskWhenItEnds:
    """Annotations train models, so a gesture that is not written is lost work.

    Each check reads the database through a second connection opened from
    scratch, which is the closest a test gets to asking what would survive if
    the machine lost power at that moment.
    """

    @pytest.fixture
    def open_case(self, window, project, annotator, sample_dicom):
        outcome = window.controller.importer().import_file(
            str(sample_dicom), project.id, imported_by=annotator.id
        )
        window.open_case(outcome.case.id)
        return outcome.case

    def _independent_read(self, paths, set_id):
        """What another process would see in the file right now."""
        from aria.store.db import open_database
        from aria.store.repository import Repository

        db = open_database(paths.database)
        try:
            return [
                (a.class_key, a.side, a.points())
                for a in Repository(db).list_annotations(set_id)
            ]
        finally:
            db.close()

    def test_a_placed_landmark_is_on_disk_immediately(
        self, window, paths, open_case, annotator
    ):
        from aria.core.models import Annotation

        set_id = window.controller.case_data.annotation_set.id
        annotation = Annotation(
            set_id=set_id, class_key="menton", side="NA",
            geometry_type="point", created_by=annotator.id,
        )
        annotation.set_points([(640.0, 812.0)])
        assert window.controller.add_annotation(annotation)

        assert ("menton", "NA", [(640.0, 812.0)]) in self._independent_read(paths, set_id)

    def test_moving_a_landmark_is_on_disk_when_the_gesture_ends(
        self, window, paths, open_case, annotator
    ):
        from aria.core.models import Annotation

        set_id = window.controller.case_data.annotation_set.id
        annotation = Annotation(
            set_id=set_id, class_key="menton", side="NA",
            geometry_type="point", created_by=annotator.id,
        )
        annotation.set_points([(100.0, 100.0)])
        window.controller.add_annotation(annotation)

        # Mid drag the change is held in the item and is not written, which is
        # what keeps one drag from becoming a thousand database writes.
        window.controller.update_annotation_points(
            annotation.id, [(250.0, 375.0)], commit=False
        )
        on_disk = self._independent_read(paths, set_id)
        assert ("menton", "NA", [(100.0, 100.0)]) in on_disk

        # The gesture ends, and the new position is durable.
        window.controller.update_annotation_points(
            annotation.id, [(250.0, 375.0)], commit=True
        )
        assert ("menton", "NA", [(250.0, 375.0)]) in self._independent_read(paths, set_id)

    def test_a_deletion_is_on_disk_immediately(self, window, paths, open_case, annotator):
        from aria.core.models import Annotation

        set_id = window.controller.case_data.annotation_set.id
        annotation = Annotation(
            set_id=set_id, class_key="menton", side="NA",
            geometry_type="point", created_by=annotator.id,
        )
        annotation.set_points([(1.0, 2.0)])
        window.controller.add_annotation(annotation)
        assert self._independent_read(paths, set_id)

        window.controller.delete_annotation(annotation.id)
        assert not self._independent_read(paths, set_id)

    def test_a_run_of_gestures_is_all_there(self, window, paths, open_case, annotator):
        """A session's worth of landmarks, with the file checked after each one
        rather than once at the end."""
        from aria.core.models import Annotation

        set_id = window.controller.case_data.annotation_set.id
        placements = [
            (key, side)
            for key in (
                "mental_foramen_centre", "mental_foramen_superior",
                "mental_foramen_inferior", "antegonial_point", "gonion",
            )
            for side in ("R", "L")
        ]
        placements.append(("menton", "NA"))

        for index, (key, side) in enumerate(placements):
            annotation = Annotation(
                set_id=set_id, class_key=key, side=side,
                geometry_type="point", created_by=annotator.id,
            )
            annotation.set_points([(float(index * 40 + 30), float(index * 9 + 60))])
            assert window.controller.add_annotation(annotation)

            on_disk = self._independent_read(paths, set_id)
            assert len(on_disk) == index + 1, (
                f"After {index + 1} landmarks the file holds {len(on_disk)}"
            )

        assert len(self._independent_read(paths, set_id)) == len(placements)

    def test_replacing_a_landmark_is_written_through_too(
        self, window, paths, open_case, annotator
    ):
        """A side scoped point holds one object per side, so placing it again
        moves the existing one. That is still a change, and it still has to be
        on disk before the next gesture starts."""
        from aria.core.models import Annotation

        set_id = window.controller.case_data.annotation_set.id
        for position in ((200.0, 300.0), (640.0, 905.0)):
            annotation = Annotation(
                set_id=set_id, class_key="gonion", side="R",
                geometry_type="point", created_by=annotator.id,
            )
            annotation.set_points([position])
            assert window.controller.add_annotation(annotation)

            on_disk = self._independent_read(paths, set_id)
            assert on_disk == [("gonion", "R", [position])], on_disk

    def test_the_interface_reports_the_save(self, window, open_case, annotator):
        """A save nobody can see is a save nobody trusts."""
        from aria.core.models import Annotation

        reported: list = []
        window.controller.saved.connect(reported.append)

        annotation = Annotation(
            set_id=window.controller.case_data.annotation_set.id,
            class_key="menton", side="NA", geometry_type="point",
            created_by=annotator.id,
        )
        annotation.set_points([(5.0, 6.0)])
        window.controller.add_annotation(annotation)

        assert reported, "Nothing told the interface the change had been saved"
