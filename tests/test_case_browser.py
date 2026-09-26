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
from PySide6.QtCore import Qt


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


class TestNoRoleIsStuckUnableToAnnotate:
    """There is no longer a role that reads a case without being able to
    annotate it (FR 046).

    This replaces a remedy. An administrator used to open every case read only
    and be told to find an annotator account to sign in as. Both roles annotate
    now, so the wall the remedy explained is gone and there is nothing to
    explain.
    """

    def test_an_administrator_can_annotate(self, window, admin, sample_dicom):
        summary = _import(window, admin, sample_dicom)
        window.controller.open_case(summary.imported[0].case.id)

        assert not window.controller.read_only, window.controller.read_only_reason
        assert window.controller.read_only_reason == ""

    def test_an_annotator_can_annotate(self, paths, repo, annotator, project, sample_dicom):
        """Driven through a window of its own, because the account is decided
        by the session rather than set afterwards."""
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
        try:
            w.controller.set_project(project)
            outcome = w.controller.importer().import_file(
                str(sample_dicom), project.id, imported_by=annotator.id
            )
            assert outcome.succeeded, outcome.error_message
            w.controller.open_case(outcome.case.id)

            assert not w.controller.read_only, w.controller.read_only_reason
        finally:
            w.controller.close_case()
            w.deleteLater()
            app.processEvents()

    def test_every_role_can_annotate(self):
        from aria.core.models import Role
        from aria.security.auth import Permission, permissions_for

        for role in Role.ALL:
            assert Permission.EDIT_ANNOTATIONS in permissions_for(role), (
                f"The {role} role cannot annotate"
            )

    def test_a_deactivated_account_is_told_which_account_it_is(
        self, window, repo, admin, sample_dicom
    ):
        """The one way a case still opens read only on account grounds. It
        names the account rather than a role, because the role is not the
        problem any more."""
        summary = _import(window, admin, sample_dicom)
        window.controller.close_case()

        admin.active = False
        window.controller.open_case(summary.imported[0].case.id)

        reason = window.controller.read_only_reason
        assert window.controller.read_only
        assert admin.username in reason, reason
        assert "not active" in reason.lower(), reason


class TestFirstRunGivesOnePersonOneAccount:
    """Setup creates one account, and it can do everything (FR 046).

    It used to ask whether you would also be annotating, and create a second
    account with its own password if you said yes. With an administrator able
    to annotate there is nothing to ask and nothing to issue twice.
    """

    def _wizard(self, paths, repo, monkeypatch):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.ui.dialogs.first_run import FirstRunWizard

        QApplication.instance() or QApplication([])
        wizard = FirstRunWizard(repo, paths, Config(paths), None)
        wizard.account_page.username.setText("solo")
        wizard.account_page.display_name.setText("Solo Researcher")
        wizard.account_page.password.setText("SoloStudy-2026")
        wizard.account_page.confirm.setText("SoloStudy-2026")
        wizard.project_page.name.setText("Solo project")
        return wizard

    def test_the_one_account_can_do_the_whole_job(self, paths, repo, monkeypatch):
        from aria.core.models import Role
        from aria.security.auth import Permission, has_permission

        wizard = self._wizard(paths, repo, monkeypatch)
        wizard.accept()

        user = wizard.created_user
        assert user.role == Role.ADMIN
        for permission in (
            Permission.IMPORT_CASES,
            Permission.EDIT_ANNOTATIONS,
            Permission.SUBMIT_ANNOTATIONS,
            Permission.EXPORT_DATA,
            Permission.CREATE_BUNDLE,
            Permission.MANAGE_USERS,
        ):
            assert has_permission(user, permission), (
                f"The first account cannot {permission.value.replace('_', ' ')}"
            )

    def test_only_one_account_is_created(self, paths, repo, monkeypatch):
        wizard = self._wizard(paths, repo, monkeypatch)
        wizard.accept()

        assert len(repo.list_users()) == 1

    def test_setup_asks_nothing_about_roles(self, paths, repo, monkeypatch):
        """The second password, and the question that led to it, are gone."""
        wizard = self._wizard(paths, repo, monkeypatch)
        page = wizard.account_page

        for gone in (
            "also_annotate", "annotator_username",
            "annotator_password", "annotator_confirm",
        ):
            assert not hasattr(page, gone), f"Setup still asks about {gone}"


class TestTheProjectSelectorFollowsTheOpenProject:
    """The selector names the project whose cases are listed beneath it.

    The list is drawn from the controller. The selector used to be rebuilt from
    its own previous value, so the two could name different projects at once,
    and nothing on screen said which one an imported case had gone into.
    """

    def _second_project(self, repo, admin, schema, name="Later project"):
        import json as _json
        from datetime import datetime, timedelta

        from aria.core.models import Project

        project = Project(
            name=name, created_by=admin.id,
            schema_json=_json.dumps(schema.to_dict()),
        )
        # Stamped a second on from the newest project already stored. Created
        # in the same breath as the fixture it would share a millisecond with
        # it, and then the identifier settles the order rather than the time,
        # which is not the arrangement these tests are describing.
        latest = max((p.created_at for p in repo.list_projects()), default="")
        if latest:
            project.created_at = (
                datetime.fromisoformat(latest) + timedelta(seconds=1)
            ).isoformat(timespec="milliseconds")
        repo.create_project(project)
        return project

    def test_switching_project_in_administration_carries_to_the_selector(
        self, window, repo, admin, project, schema
    ):
        """The reproduction, entirely with the mouse.

        Pick a project row in Administration, which opens it, then walk back to
        Cases. Cases rebuilds its selector on the way in, and the selector used
        to restore the name it held before the trip while the list beneath it
        had already moved to the newly opened project.
        """
        later = self._second_project(repo, admin, schema)
        browser = window.case_browser

        window.set_module("cases")
        assert browser.project_combo.currentData() == project.id

        window.set_module("administration")
        rows = window.admin_panel.project_list
        match = [
            i for i in range(rows.count())
            if rows.item(i).data(Qt.UserRole) == later.id
            or rows.item(i).text() == later.name
        ]
        assert match, "The new project is not listed in Administration"
        rows.setCurrentRow(match[0])
        assert window.controller.project.id == later.id

        window.set_module("cases")

        assert browser.project_combo.currentData() == later.id, (
            "Cases names the project that was open before the trip to "
            "Administration, while the list below it holds another project"
        )

    def test_a_rebuild_cannot_leave_a_stale_name_in_the_selector(
        self, window, repo, admin, project, schema
    ):
        """The selector is on one project and the controller is moved to
        another. A rebuild has to settle on the controller."""
        later = self._second_project(repo, admin, schema)
        browser = window.case_browser
        browser.reload_projects()
        # Chosen the way a person chooses it, which moves the controller too.
        browser.project_combo.setCurrentIndex(
            browser.project_combo.findData(later.id)
        )
        assert browser.project_combo.currentData() == later.id
        assert window.controller.project.id == later.id

        window.controller.set_project(project)
        browser.reload_projects()

        assert browser.project_combo.currentData() == project.id, (
            "The selector kept its own previous value instead of following "
            "the project that is open"
        )

    def test_the_listed_cases_belong_to_the_named_project(
        self, window, repo, admin, project, schema, sample_dicom, sample_png
    ):
        """The check that matters to somebody importing: what the selector
        names is what the list holds."""
        later = self._second_project(repo, admin, schema)
        _import(window, admin, sample_dicom)

        window.controller.set_project(later)
        window.case_browser.reload_projects()

        named = window.case_browser.project_combo.currentData()
        assert named == later.id
        assert window.case_browser.tree.topLevelItemCount() == 0, (
            "The selector names an empty project but the list still shows "
            "cases from another one"
        )

        window.controller.set_project(project)
        window.case_browser.reload_projects()
        assert window.case_browser.project_combo.currentData() == project.id
        assert window.case_browser.tree.topLevelItemCount() == 1

    def test_an_archived_project_does_not_leave_the_two_disagreeing(
        self, window, repo, admin, project, schema
    ):
        """What is open has gone from the list. Both parts have to land on the
        same replacement rather than each choosing its own."""
        later = self._second_project(repo, admin, schema)
        window.controller.set_project(later)
        window.case_browser.reload_projects()

        later.archived = True
        repo.update_project(later, "Archived for this test.")
        window.case_browser.reload_projects()

        assert window.case_browser.project_combo.currentData() == project.id
        assert window.controller.project is not None
        assert window.controller.project.id == project.id, (
            "The selector fell back to a project the controller never opened"
        )

    def test_no_projects_leaves_nothing_named_and_nothing_listed(
        self, paths, repo, admin, monkeypatch
    ):
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
        session = AuthService(repo, config.settings).start_session(admin)

        window = MainWindow(repo, paths, config, session)
        try:
            window.case_browser.reload_projects()
            app.processEvents()

            assert window.case_browser.project_combo.count() == 0
            assert window.case_browser.tree.topLevelItemCount() == 0
            assert not window.case_browser.open_button.isEnabled()
        finally:
            window.deleteLater()
            app.processEvents()

    def test_archiving_the_last_project_leaves_nothing_open(
        self, window, repo, project
    ):
        """The selector has nothing left to name, so nothing may stay open
        behind it."""
        window.case_browser.reload_projects()
        assert window.controller.project is not None

        project.archived = True
        repo.update_project(project, "Archived for this test.")
        window.case_browser.reload_projects()

        assert window.case_browser.project_combo.count() == 0
        assert window.controller.project is None, (
            "Nothing is named in the selector but a project is still open"
        )
        assert window.case_browser.tree.topLevelItemCount() == 0
