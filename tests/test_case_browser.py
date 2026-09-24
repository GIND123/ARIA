"""Getting from an imported file into the annotation workspace.

This file exists because importing worked and opening did not. The case list
rebuilds every row on a refresh, and a refresh runs as soon as an import
finishes, which left the list with no current row. Open read the current row,
found nothing and returned without a word, so the images appeared to have been
imported into nowhere. Nothing in the database recorded the attempt, because
the attempt never reached the controller.

The tests drive the case browser rather than the controller, because the
controller was never the part that was broken.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def window(paths, repo, admin, project, monkeypatch):
    """A real main window over a real database, on the offscreen platform."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from aria.config import Config
    from aria.security.auth import AuthService

    config = Config(paths)
    config.settings.first_run_completed = True
    config.settings.tour_completed = True
    config.settings.backup_on_launch = False

    app = QApplication.instance() or QApplication([])
    repo.set_actor(admin)
    session = AuthService(repo, config.settings).start_session(admin)

    from aria.ui.main_window import MainWindow

    w = MainWindow(repo, paths, config, session)
    w.controller.set_project(project)
    w.case_browser.reload_projects()
    app.processEvents()
    yield w
    w.controller.close_case()
    w.deleteLater()
    app.processEvents()


def _import(window, admin, *files):
    summary = window.controller.importer().import_files(
        [str(f) for f in files], window.controller.project.id, imported_by=admin.id
    )
    assert summary.imported, summary.summary_line()
    window.case_browser.refresh()
    return summary


class TestOpeningAnImportedCase:
    """An imported case has to be reachable, not merely present."""

    def test_a_refreshed_list_leaves_a_case_selected(
        self, window, admin, sample_dicom, sample_png
    ):
        """The defect: a rebuilt list had no current row for Open to act on."""
        _import(window, admin, sample_dicom, sample_png)

        browser = window.case_browser
        assert browser.tree.topLevelItemCount() == 2
        assert browser.selected_case_id(), (
            "The refreshed list has no current row, so Open has nothing to open"
        )

    def test_open_reaches_the_annotation_workspace(self, window, admin, sample_dicom):
        """Import, then Open, without selecting a row by hand in between."""
        summary = _import(window, admin, sample_dicom)
        case = summary.imported[0].case

        window.case_browser.select_case(case.id)
        window.case_browser.open_button.click()

        assert window.controller.case_data is not None, "The case did not open"
        assert window.controller.case_data.case.id == case.id
        assert window.controller.image is not None, "The pixels were not loaded"
        assert window.module_stack.currentWidget() is window.annotate_panel

    def test_the_open_attempt_is_recorded(self, window, repo, admin, sample_dicom):
        """An opened case leaves an annotation set behind, which is how the
        original report was diagnosed: there was none."""
        summary = _import(window, admin, sample_dicom)
        case = summary.imported[0].case

        assert not repo.list_sets_for_case(case.id)
        window.case_browser.select_case(case.id)
        window.case_browser.open_button.click()
        assert repo.list_sets_for_case(case.id)

    def test_a_selection_survives_a_refresh(self, window, admin, sample_dicom, sample_png):
        _import(window, admin, sample_dicom, sample_png)
        browser = window.case_browser

        browser.tree.setCurrentItem(browser.tree.topLevelItem(1))
        chosen = browser.selected_case_id()
        browser.refresh()

        assert browser.selected_case_id() == chosen, (
            "A refresh moved the selection to another case"
        )

    def test_open_is_disabled_when_there_is_nothing_to_open(self, window):
        """A button that cannot act says so, rather than swallowing the click."""
        browser = window.case_browser
        assert browser.tree.topLevelItemCount() == 0
        assert not browser.open_button.isEnabled()
        assert not browser.open_read_only_button.isEnabled()

    def test_open_with_no_selection_says_so(self, window, admin, sample_dicom):
        """Even forced, the silent path now explains itself."""
        _import(window, admin, sample_dicom)
        messages = []
        window.controller.status_message.connect(lambda m, _t: messages.append(m))

        window.case_browser.tree.setCurrentItem(None)
        window.case_browser._open(False)

        assert window.controller.case_data is None
        assert messages and "select a case" in messages[0].lower()


class TestReadOnlyRolesAreGivenARemedy:
    """A role that cannot annotate is told what to do about it (FR 046)."""

    def test_an_administrator_is_pointed_at_an_annotator_account(
        self, window, repo, admin, annotator, sample_dicom
    ):
        summary = _import(window, admin, sample_dicom)
        window.controller.open_case(summary.imported[0].case.id)

        reason = window.controller.read_only_reason
        assert window.controller.read_only
        assert annotator.username in reason, (
            f"The notice names no account to annotate from: {reason}"
        )

    def test_without_an_annotator_account_the_remedy_is_to_create_one(
        self, window, admin, sample_dicom
    ):
        summary = _import(window, admin, sample_dicom)
        window.controller.open_case(summary.imported[0].case.id)

        reason = window.controller.read_only_reason
        assert "Administration" in reason, reason


class TestFirstRunOffersAnAnnotatorAccount:
    """A one person study needs an account that can annotate (FR 046)."""

    def test_the_wizard_creates_the_annotator_account_when_asked(
        self, paths, repo, monkeypatch
    ):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.core.models import Role
        from aria.ui.dialogs.first_run import FirstRunWizard

        QApplication.instance() or QApplication([])
        config = Config(paths)

        wizard = FirstRunWizard(repo, paths, config, None)
        wizard.account_page.username.setText("solo_admin")
        wizard.account_page.display_name.setText("Solo Researcher")
        wizard.account_page.password.setText("SoloStudy-2026")
        wizard.account_page.confirm.setText("SoloStudy-2026")
        wizard.account_page.also_annotate.setChecked(True)
        wizard.account_page.annotator_username.setText("solo_ann")
        wizard.account_page.annotator_password.setText("SoloAnnotate-2026")
        wizard.account_page.annotator_confirm.setText("SoloAnnotate-2026")
        wizard.project_page.name.setText("Solo project")
        wizard.accept()

        assert wizard.created_user.role == Role.ADMIN
        assert wizard.created_annotator is not None
        assert wizard.created_annotator.role == Role.ANNOTATOR

        from aria.security.auth import Permission, has_permission

        assert has_permission(wizard.created_annotator, Permission.EDIT_ANNOTATIONS)

    def test_the_annotator_account_is_optional(self, paths, repo, monkeypatch):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.ui.dialogs.first_run import FirstRunWizard

        QApplication.instance() or QApplication([])
        wizard = FirstRunWizard(repo, paths, Config(paths), None)
        wizard.account_page.username.setText("team_admin")
        wizard.account_page.display_name.setText("Team Administrator")
        wizard.account_page.password.setText("TeamStudy-2026")
        wizard.account_page.confirm.setText("TeamStudy-2026")
        wizard.project_page.name.setText("Team project")
        wizard.accept()

        assert wizard.created_user is not None
        assert wizard.created_annotator is None
