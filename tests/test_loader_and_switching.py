"""The loader ARIA shows while it opens, and changing who is annotating.

Two things a person meets before any annotation happens. The loader is here
because a startup with nothing on screen is indistinguishable from a startup
that failed, and somebody who cannot tell starts the program a second time.
Switching profile is here because two observers annotating the same study on
one workstation is the ordinary case, not an edge one, and it used to mean
closing the application.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestTheLoaderSaysWhatIsHappening:
    def test_it_starts_empty_and_fills_as_steps_complete(self, qt_app):
        from aria.ui.splash import SplashScreen

        splash = SplashScreen(total_steps=3)
        try:
            assert splash._step == 0
            splash.step("Opening the database")
            splash.step("Checking the installation")
            assert splash._step == 2
            assert splash._message == "Checking the installation"
        finally:
            splash.close()
            splash.deleteLater()

    def test_the_bar_cannot_run_past_its_end(self, qt_app):
        """A step added later must not paint outside the bar."""
        from aria.ui.splash import SplashScreen

        splash = SplashScreen(total_steps=2)
        try:
            for _ in range(6):
                splash.step("Still going")
            assert splash._step == 2
        finally:
            splash.close()
            splash.deleteLater()

    def test_it_paints_without_a_window_manager(self, qt_app):
        """It is painted by hand, so the painting is what can break."""
        from aria.ui.splash import SplashScreen

        splash = SplashScreen(total_steps=4)
        try:
            splash.show()
            splash.step("Backing up before any change")
            picture = splash.grab()
            assert not picture.isNull()
            assert picture.width() > 0 and picture.height() > 0
        finally:
            splash.close()
            splash.deleteLater()


class TestTheMark:
    def test_the_shipped_mark_is_where_the_loader_looks_for_it(self):
        """It is read from a file so it can be replaced without touching code."""
        from aria.ui.splash import LOGO_NAME, resources_dir

        assert (resources_dir() / LOGO_NAME).is_file()

    def test_it_is_square_and_large_enough_to_scale_down(self, qt_app):
        from PySide6.QtGui import QPixmap

        from aria.ui.splash import LOGO_NAME, resources_dir

        pixmap = QPixmap(str(resources_dir() / LOGO_NAME))
        assert not pixmap.isNull()
        assert pixmap.width() == pixmap.height()
        assert pixmap.width() >= 512

    def test_a_build_without_the_file_still_shows_a_mark(self, qt_app, monkeypatch, tmp_path):
        """Falling back to nothing would make a stripped build look broken."""
        import aria.ui.splash as splash_module

        monkeypatch.setattr(splash_module, "resources_dir", lambda: tmp_path)
        mark = splash_module.logo_pixmap(64)

        assert not mark.isNull()


class TestChangingWhoIsAnnotating:
    """File, Switch profile, which used to close ARIA and start it again."""

    def _window(self, paths, repo, user, project, monkeypatch):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.core.profile import ActiveProfile
        from aria.ui.main_window import MainWindow

        config = Config(paths)
        config.settings.first_run_completed = True
        config.settings.tour_completed = True
        config.settings.backup_on_launch = False

        QApplication.instance() or QApplication([])
        repo.set_actor(user)
        window = MainWindow(repo, paths, config, ActiveProfile(user))
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
        window.show()
        window.controller.set_project(project)
        return window

    def _answer_with(self, monkeypatch, profile):
        from PySide6.QtWidgets import QDialog

        from aria.ui.dialogs.profile_dialog import ProfileDialog

        def fake_exec(self):
            self.profile = profile
            return (
                QDialog.DialogCode.Rejected if profile is None
                else QDialog.DialogCode.Accepted
            )

        monkeypatch.setattr(ProfileDialog, "exec", fake_exec)

    def test_the_window_stays_open_and_the_name_changes(
        self, paths, repo, annotator, project, monkeypatch
    ):
        from aria.core.profile import ProfileService

        second = ProfileService(repo).create("Second Observer")
        window = self._window(paths, repo, annotator, project, monkeypatch)
        try:
            self._answer_with(monkeypatch, second)
            window.switch_profile()

            assert window.isVisible()
            assert window.session.user.id == second.id
            assert window.controller.user.id == second.id
            assert second.display_name in window.status_user.text()
        finally:
            window.controller.close_case()
            window.deleteLater()

    def test_cancelling_leaves_the_profile_alone(
        self, paths, repo, annotator, project, monkeypatch
    ):
        window = self._window(paths, repo, annotator, project, monkeypatch)
        try:
            self._answer_with(monkeypatch, None)
            window.switch_profile()

            assert window.session.user.id == annotator.id
        finally:
            window.controller.close_case()
            window.deleteLater()

    def test_the_next_annotation_is_attributed_to_the_new_profile(
        self, paths, repo, annotator, project, importer, sample_dicom, monkeypatch
    ):
        """The point of the whole thing: whose work the rows say they are."""
        from PySide6.QtWidgets import QApplication

        from aria.core.profile import ProfileService

        second = ProfileService(repo).create("Second Observer")
        window = self._window(paths, repo, annotator, project, monkeypatch)
        try:
            outcome = importer.import_file(
                str(sample_dicom), project.id, imported_by=annotator.id
            )
            assert outcome.succeeded, outcome.error_message

            self._answer_with(monkeypatch, second)
            window.switch_profile()

            window.open_case(outcome.case.id)
            QApplication.instance().processEvents()

            assert window.controller.case_data is not None
            assert window.controller.case_data.annotation_set.annotator_id == second.id
        finally:
            window.controller.close_case()
            window.deleteLater()

    def test_the_change_of_hands_is_in_the_history(
        self, paths, repo, annotator, project, monkeypatch
    ):
        """An agreement figure computed afterwards depends on the history
        accounting for a change partway through a session."""
        from aria.core.audit import AuditEvent
        from aria.core.profile import ProfileService

        second = ProfileService(repo).create("Second Observer")
        window = self._window(paths, repo, annotator, project, monkeypatch)
        try:
            self._answer_with(monkeypatch, second)
            window.switch_profile()

            selections = [
                r for r in repo.audit_records(limit=50)
                if r.event == AuditEvent.PROFILE_SELECTED.value
            ]
            assert selections, "The profile change was not recorded"
            assert any(second.id == r.object_id for r in selections)
            # Said plainly, so a reader months later does not mistake a name
            # that was selected for one that was proved.
            assert "not authenticated" in selections[0].detail
        finally:
            window.controller.close_case()
            window.deleteLater()

    def test_switching_to_the_same_profile_changes_nothing(
        self, paths, repo, annotator, project, monkeypatch
    ):
        window = self._window(paths, repo, annotator, project, monkeypatch)
        try:
            before = window.session
            self._answer_with(monkeypatch, annotator)
            window.switch_profile()

            assert window.session is before
        finally:
            window.controller.close_case()
            window.deleteLater()
