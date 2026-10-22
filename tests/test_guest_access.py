"""Opening ARIA as a guest, where an administrator allows it.

A guest is nobody in particular, so it is given the least that is still useful:
the case list and the images, with nobody's annotations, and nothing it can
change. Two things make that hold. The permission table gives a guest only the
two viewing permissions, and for the length of a guest session the database
connection refuses every change except an append to the audit log, so a panel
that forgets to check a permission fails rather than writes.

The account roles stay two. A guest is not an account and holds no row.
"""

from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture
def guest():
    from aria.security.auth import guest_user

    return guest_user()


def _events(repo, event):
    return [r for r in repo.audit_records(limit=500) if r.event == event]


# ---------------------------------------------------------------------------
# What a guest may do
# ---------------------------------------------------------------------------


class TestAGuestOnlyLooks:
    def test_holds_the_two_viewing_permissions_and_nothing_else(self, guest):
        from aria.security.auth import Permission, permissions_for

        assert permissions_for(guest.role) == {Permission.VIEW_CASES, Permission.VIEW_IMAGE}

    def test_cannot_change_take_away_or_run_anything(self, guest):
        from aria.security.auth import Permission, has_permission

        for permission in Permission:
            if permission in (Permission.VIEW_CASES, Permission.VIEW_IMAGE):
                continue
            assert not has_permission(guest, permission), (
                f"A guest can {permission.value.replace('_', ' ')}"
            )

    def test_is_not_a_role_an_account_can_be_given(self, service_and_settings, guest):
        from aria.core.models import Role

        service, _settings = service_and_settings
        assert Role.GUEST not in Role.ALL
        with pytest.raises(ValueError):
            service.create_account("visitor", "Visitor", Role.GUEST, "GuestPassword-2026")

    def test_is_named_in_the_interface(self, guest):
        assert guest.role_display == "Guest"
        assert guest.is_guest and not guest.has_recovery_key


@pytest.fixture
def service_and_settings(repo, settings):
    from aria.security.auth import AuthService

    return AuthService(repo, settings), settings


class TestSigningInAsAGuest:
    def test_is_refused_until_an_administrator_allows_it(self, service_and_settings):
        from aria.security.auth import AuthenticationError

        service, settings = service_and_settings
        assert settings.allow_guest_access is False
        with pytest.raises(AuthenticationError, match="turned off"):
            service.sign_in_as_guest()

    def test_is_recorded_and_creates_no_account(self, service_and_settings, repo):
        from aria.security.auth import GUEST_ID

        service, settings = service_and_settings
        settings.allow_guest_access = True
        before = repo.count_users()

        guest = service.sign_in_as_guest()

        assert guest.is_guest
        assert repo.count_users() == before
        started = _events(repo, "guest_session_started")
        assert started and started[0].actor_id == GUEST_ID
        assert started[0].actor_name == "Guest"

    def test_there_is_no_password_to_reset(self, service_and_settings):
        service, settings = service_and_settings
        with pytest.raises(LookupError):
            service.reset_password_at_workstation("guest", "george")


# ---------------------------------------------------------------------------
# The database refuses a guest's changes
# ---------------------------------------------------------------------------


class TestTheConnectionRefusesChanges:
    def test_a_change_is_refused_and_an_audit_append_is_not(self, repo, imported_case):
        db = repo.db
        db.refuse_changes()
        try:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                repo.set_case_split(imported_case.id, "test")
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                db.execute("DELETE FROM cases")
            repo.log("case_viewed", "case", imported_case.id, case_id=imported_case.id)
            assert repo.get_case(imported_case.id).split != "test"
        finally:
            db.accept_changes()
        assert repo.verify_audit_chain()["valid"]

    def test_a_statement_prepared_before_is_refused_too(self, repo, imported_case):
        """SQLite keeps prepared statements, and one cached before the guard
        must not slip past it."""
        repo.set_case_split(imported_case.id, "train")   # prepared and cached
        repo.db.refuse_changes()
        try:
            with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
                repo.set_case_split(imported_case.id, "test")
        finally:
            repo.db.accept_changes()
        assert repo.get_case(imported_case.id).split == "train"

    def test_reading_and_checking_still_work(self, repo, imported_case):
        repo.db.refuse_changes()
        try:
            assert repo.get_case(imported_case.id) is not None
            assert repo.list_users()
            assert repo.db.integrity_check()["ok"]
        finally:
            repo.db.accept_changes()

    def test_changes_are_accepted_again_afterwards(self, repo, imported_case):
        repo.db.refuse_changes()
        assert repo.db.changes_refused
        repo.db.accept_changes()
        assert not repo.db.changes_refused
        repo.set_case_split(imported_case.id, "validation")
        assert repo.get_case(imported_case.id).split == "validation"


class TestAGuestLooksAtACase:
    def test_creates_no_annotation_set(self, repo, imported_case, annotator):
        from conftest import build_annotation_set

        build_annotation_set(repo, imported_case, annotator)
        before = len(repo.list_sets_for_case(imported_case.id))

        data = repo.load_case_for_viewing(imported_case.id)

        assert data.case.id == imported_case.id
        assert data.annotations == [] and data.categorical == []
        assert len(repo.list_sets_for_case(imported_case.id)) == before

    def test_an_unknown_case_is_said_so(self, repo):
        with pytest.raises(KeyError):
            repo.load_case_for_viewing("no-such-case")


# ---------------------------------------------------------------------------
# The workspace as a guest
# ---------------------------------------------------------------------------


@pytest.fixture
def guest_window(paths, repo, admin, project, imported_case, annotator, monkeypatch):
    """A main window signed in as a guest, with the connection guarded the way
    startup guards it."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from aria.config import Config
    from aria.security.auth import AuthService
    from conftest import build_annotation_set

    build_annotation_set(repo, imported_case, annotator)

    config = Config(paths)
    config.settings.first_run_completed = True
    config.settings.tour_completed = True
    config.settings.backup_on_launch = False
    config.settings.allow_guest_access = True

    app = QApplication.instance() or QApplication([])
    service = AuthService(repo, config.settings)
    guest = service.sign_in_as_guest()
    session = service.start_session(guest)
    repo.db.refuse_changes()

    from aria.ui.main_window import MainWindow

    w = MainWindow(repo, paths, config, session)
    w.controller.set_project(project)
    w.case_browser.reload_projects()
    app.processEvents()
    yield w
    w.controller.close_case()
    w.close()
    w.deleteLater()
    app.processEvents()
    repo.db.accept_changes()


class TestTheGuestWorkspace:
    def test_offers_only_the_viewing_modules(self, guest_window):
        combo = guest_window.module_combo
        offered = {combo.itemData(i) for i in range(combo.count())}
        assert offered == {"cases", "annotate", "measure"}

    def test_a_menu_cannot_open_a_module_that_is_not_offered(self, guest_window):
        guest_window.set_module("review")
        assert guest_window.module_stack.currentWidget() is not guest_window.panels["review"]

    def test_nothing_that_changes_or_takes_away_is_enabled(self, guest_window):
        w = guest_window
        for action in (
            w.action_import, w.action_import_folder, w.action_export, w.action_bundle,
            w.action_backup, w.action_open_data_folder, w.action_preferences,
            w.action_diagnostics, w.action_change_password, w.action_recovery_key,
        ):
            assert not action.isEnabled(), f"{action.text()} is enabled for a guest"
        assert not w.case_browser.import_button.isEnabled()
        assert not w.review_menu.isEnabled()

    def test_says_who_is_signed_in(self, guest_window):
        assert "Guest" in guest_window.status_user.text()

    def test_opens_a_case_to_look_at_and_nothing_more(self, guest_window, repo, imported_case):
        from aria.security.auth import GUEST_ID

        before_sets = len(repo.list_sets_for_case(imported_case.id))
        before_display = repo.get_display_settings(imported_case.id)

        guest_window.open_case(imported_case.id)
        controller = guest_window.controller

        assert controller.case_data is not None
        assert controller.read_only
        assert "guest" in controller.read_only_reason.lower()
        assert controller.case_data.annotations == [], "A guest was shown annotations"
        assert not guest_window.action_calibrate.isEnabled()
        assert not guest_window.action_patient_factors.isEnabled()

        controller.display_settings.brightness = 0.4
        guest_window.close_case()

        assert len(repo.list_sets_for_case(imported_case.id)) == before_sets
        assert repo.get_display_settings(imported_case.id) == before_display
        views = _events(repo, "case_viewed")
        assert views and views[0].actor_id == GUEST_ID

    def test_an_edit_is_turned_away(self, guest_window, imported_case):
        from aria.core.models import Annotation

        guest_window.open_case(imported_case.id)
        annotation = Annotation(class_key="menton", side="M", geometry_type="point")
        annotation.set_points([(10.0, 10.0)])
        assert guest_window.controller.add_annotation(annotation) is False

    def test_closing_the_window_raises_nothing(self, guest_window, imported_case):
        guest_window.open_case(imported_case.id)
        guest_window.close()


# ---------------------------------------------------------------------------
# Turning it on, and the sign in window
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_window(paths, repo, admin, project, monkeypatch):
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

    from aria.ui.main_window import MainWindow

    w = MainWindow(repo, paths, config, AuthService(repo, config.settings).start_session(admin))
    w.controller.set_project(project)
    app.processEvents()
    yield w
    w.deleteLater()
    app.processEvents()


class TestAnAdministratorTurnsItOn:
    def test_the_switch_saves_the_policy_and_records_it(self, admin_window, repo, paths):
        from aria.config import Config

        panel = admin_window.admin_panel
        panel.refresh()
        assert panel.guest_access.isEnabled()
        assert not panel.guest_access.isChecked()

        panel.guest_access.setChecked(True)

        assert admin_window.config.settings.allow_guest_access
        assert Config(paths).load().settings.allow_guest_access, "Not saved"
        changes = _events(repo, "policy_changed")
        assert changes and changes[0].object_id == "allow_guest_access"

    def test_an_annotator_cannot(self, paths, repo, annotator, project, monkeypatch):
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from aria.config import Config
        from aria.security.auth import AuthService
        from aria.ui.main_window import MainWindow

        QApplication.instance() or QApplication([])
        config = Config(paths)
        config.settings.tour_completed = True
        w = MainWindow(repo, paths, config, AuthService(repo, config.settings).start_session(annotator))
        try:
            w.admin_panel.refresh()
            assert not w.admin_panel.guest_access.isEnabled()
            w.admin_panel._set_guest_access(True)
            assert not config.settings.allow_guest_access
        finally:
            w.deleteLater()


@pytest.fixture
def qt(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


class TestTheSignInWindowAsAGuest:
    def test_offers_no_guest_session_by_default(self, qt, repo, paths):
        from aria.config import Config
        from aria.ui.dialogs.login_dialog import LoginDialog

        dialog = LoginDialog(repo, Config(paths))
        assert dialog.guest_button.isHidden()

    def test_offers_one_where_it_is_allowed(self, qt, repo, paths):
        from aria.config import Config
        from aria.ui.dialogs.login_dialog import LoginDialog

        config = Config(paths)
        config.settings.allow_guest_access = True
        dialog = LoginDialog(repo, config)
        assert not dialog.guest_button.isHidden()

        dialog.guest_button.click()
        assert dialog.user is not None and dialog.user.is_guest

    def test_a_policy_turned_off_meanwhile_is_honoured(self, qt, repo, paths):
        from aria.config import Config
        from aria.ui.dialogs.login_dialog import LoginDialog

        config = Config(paths)
        config.settings.allow_guest_access = True
        dialog = LoginDialog(repo, config)
        config.settings.allow_guest_access = False

        dialog._continue_as_guest()
        assert dialog.user is None
        assert "turned off" in dialog.message.text()


# ---------------------------------------------------------------------------
# The real startup path
# ---------------------------------------------------------------------------


class TestStartingAsAGuest:
    def test_runs_guarded_and_shuts_down_cleanly(self, tmp_path, monkeypatch):
        from PySide6.QtWidgets import QApplication, QDialog

        monkeypatch.setenv("ARIA_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("ARIA_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        import aria.config as config_module
        import aria.ui.dialogs.tour as tour_module

        monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)
        monkeypatch.setattr(QApplication, "exec", lambda self: 0)
        monkeypatch.setattr(tour_module, "should_offer_tour", lambda settings: False)

        from aria.app import run
        from aria.config import get_config
        from aria.security.auth import AuthService
        from aria.store.db import open_database
        from aria.store.repository import Repository

        config = get_config()
        config.settings.first_run_completed = True
        config.settings.allow_guest_access = True
        config.save()
        db = open_database(config.paths.database)
        AuthService(Repository(db), config.settings).create_account(
            "solo", "Solo", "project_administrator", "SoloStudy-2026", must_change=False
        )
        db.close()
        monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)

        from aria.ui.dialogs.login_dialog import LoginDialog
        import aria.ui.main_window as main_window_module

        def as_guest(self):
            self.guest_button.click()
            return QDialog.DialogCode.Accepted if self.user else QDialog.DialogCode.Rejected

        monkeypatch.setattr(LoginDialog, "exec", as_guest)
        seen = {}
        real_show = main_window_module.MainWindow.show

        def record(self):
            seen["guest"] = self.session.user.is_guest
            seen["refused"] = self.repo.db.changes_refused
            real_show(self)

        monkeypatch.setattr(main_window_module.MainWindow, "show", record)

        assert run([]) == 0
        assert seen == {"guest": True, "refused": True}

        db = open_database(get_config().paths.database, read_only=True)
        try:
            repo = Repository(db)
            assert repo.count_users() == 1
            assert _events(repo, "guest_session_started")
            assert repo.verify_audit_chain()["valid"]
        finally:
            db.close()
