"""Getting back into an account whose password has been forgotten.

This file exists because a forgotten password used to be the end of the road.
The password is kept as a memory hard digest, so ARIA cannot say what it was,
and the only reset was an administrator's. When the forgotten password was the
only administrator's, the study was locked out of itself.

There are now three ways back, and these tests pin each of them:

* the account's recovery key, from Forgot password? in the sign in window,
* an administrator, as before, now recorded as a password reset,
* the workstation reset on the command line, for when there is neither.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

PASSWORD = "FirstPassword-2026"
NEW_PASSWORD = "SecondPassword-2026"


@pytest.fixture
def service(repo, settings):
    from aria.security.auth import AuthService

    return AuthService(repo, settings)


@pytest.fixture
def account(service):
    return service.create_account(
        "keyholder", "Key Holder", "project_administrator", PASSWORD, must_change=False
    )


def _events(repo, event):
    return [r for r in repo.audit_records(limit=500) if r.event == event]


# ---------------------------------------------------------------------------
# The recovery key itself
# ---------------------------------------------------------------------------


class TestTheRecoveryKey:
    def test_it_reads_the_same_on_paper(self):
        from aria.security.auth import RECOVERY_ALPHABET, generate_recovery_key

        key = generate_recovery_key()
        assert re.fullmatch(r"[A-Z0-9]{4}(-[A-Z0-9]{4}){4}", key), key
        assert set(key.replace("-", "")) <= set(RECOVERY_ALPHABET)
        for misread in "IL O01":
            assert misread not in RECOVERY_ALPHABET

    def test_two_keys_are_not_the_same(self):
        from aria.security.auth import generate_recovery_key

        assert len({generate_recovery_key() for _ in range(50)}) == 50

    def test_only_a_digest_is_kept(self, service, account, repo):
        key = service.issue_recovery_key(account)
        stored = repo.get_user(account.id)

        assert stored.has_recovery_key
        assert key.replace("-", "") not in stored.recovery_hash
        assert stored.recovery_hash.startswith("scrypt$")
        assert stored.recovery_created_at

    def test_issuing_one_is_recorded(self, service, account, repo):
        service.issue_recovery_key(account)
        assert _events(repo, "recovery_key_issued")

    def test_a_new_key_replaces_the_old(self, service, account):
        from aria.security.auth import AuthenticationError

        old = service.issue_recovery_key(account)
        new = service.issue_recovery_key(account)

        with pytest.raises(AuthenticationError):
            service.reset_with_recovery_key("keyholder", old, NEW_PASSWORD)
        service.reset_with_recovery_key("keyholder", new, NEW_PASSWORD)

    def test_a_guest_has_nothing_to_recover(self, service):
        from aria.security.auth import guest_user

        with pytest.raises(ValueError):
            service.issue_recovery_key(guest_user())


# ---------------------------------------------------------------------------
# Resetting with it
# ---------------------------------------------------------------------------


class TestResettingWithTheKey:
    def test_the_new_password_works_and_the_old_does_not(self, service, account):
        from aria.security.auth import AuthenticationError

        key = service.issue_recovery_key(account)
        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)

        assert service.authenticate("keyholder", NEW_PASSWORD).id == account.id
        with pytest.raises(AuthenticationError):
            service.authenticate("keyholder", PASSWORD)

    def test_the_key_is_spent_and_the_next_one_works(self, service, account):
        from aria.security.auth import AuthenticationError

        key = service.issue_recovery_key(account)
        _user, next_key = service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)

        assert next_key != key
        with pytest.raises(AuthenticationError):
            service.reset_with_recovery_key("keyholder", key, "ThirdPassword-2026")
        service.reset_with_recovery_key("keyholder", next_key, "ThirdPassword-2026")
        service.authenticate("keyholder", "ThirdPassword-2026")

    def test_case_spaces_and_dashes_do_not_matter(self, service, account):
        key = service.issue_recovery_key(account)
        typed = " " + key.lower().replace("-", " ") + " "
        service.reset_with_recovery_key("keyholder", typed, NEW_PASSWORD)

    def test_no_new_password_is_needed_at_the_next_sign_in(self, service, account, repo):
        key = service.issue_recovery_key(account)
        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)
        assert not repo.get_user(account.id).must_change_password

    def test_the_reset_is_recorded_against_the_account(self, service, account, repo):
        key = service.issue_recovery_key(account)
        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)

        resets = _events(repo, "password_reset")
        assert resets and resets[0].actor_id == account.id
        assert "recovery key" in resets[0].detail

    def test_nobody_is_left_signed_in_by_it(self, service, account, repo):
        """The reset is the account's, but nobody has signed in yet, so what
        happens next at the sign in window is not put down to it."""
        key = service.issue_recovery_key(account)
        repo.set_actor(None)
        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)

        record = repo.log("integrity_check", "database", "probe")
        assert record.actor_id == ""

    def test_the_new_password_still_meets_the_policy(self, service, account, repo):
        key = service.issue_recovery_key(account)
        with pytest.raises(ValueError, match="at least"):
            service.reset_with_recovery_key("keyholder", key, "short")

        # Nothing was spent by the refusal.
        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)

    def test_a_correct_key_lifts_a_lockout(self, service, account, settings):
        from aria.security.auth import AccountLockedError, AuthenticationError

        key = service.issue_recovery_key(account)
        settings.max_failed_logins = 2
        for _ in range(2):
            with pytest.raises(AuthenticationError):
                service.authenticate("keyholder", "wrong")
        with pytest.raises(AccountLockedError):
            service.authenticate("keyholder", PASSWORD)

        service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)
        service.authenticate("keyholder", NEW_PASSWORD)

    def test_a_deactivated_account_stays_out(self, service, account, repo):
        from aria.security.auth import AuthenticationError

        key = service.issue_recovery_key(account)
        account.active = False
        repo.update_user(account)

        with pytest.raises(AuthenticationError, match="deactivated"):
            service.reset_with_recovery_key("keyholder", key, NEW_PASSWORD)


class TestAWrongKey:
    def test_reads_the_same_as_an_unknown_account_or_no_key(self, service, account):
        from aria.security.auth import AuthenticationError

        service.create_account(
            "keyless", "No Key", "annotator", PASSWORD, must_change=False
        )
        service.issue_recovery_key(account)
        messages = set()
        for username in ("keyholder", "nobody", "keyless"):
            with pytest.raises(AuthenticationError) as info:
                service.reset_with_recovery_key(username, "AAAA-BBBB-CCCC-DDDD-EEEE", NEW_PASSWORD)
            messages.add(str(info.value))
        assert len(messages) == 1, messages

    def test_is_recorded(self, service, account, repo):
        from aria.security.auth import AuthenticationError

        service.issue_recovery_key(account)
        with pytest.raises(AuthenticationError):
            service.reset_with_recovery_key("keyholder", "AAAA-BBBB-CCCC-DDDD-EEEE", NEW_PASSWORD)
        failures = _events(repo, "login_failure")
        assert any("recovery key did not match" in r.detail for r in failures)

    def test_does_not_lock_the_owner_out(self, service, account, repo, settings):
        """Guessing a key is hopeless, so counting wrong ones would protect
        nothing and would let anyone lock the owner out by typing nonsense."""
        from aria.security.auth import AuthenticationError

        settings.max_failed_logins = 2
        service.issue_recovery_key(account)
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                service.reset_with_recovery_key("keyholder", "AAAA-BBBB-CCCC-DDDD-EEEE", NEW_PASSWORD)

        stored = repo.get_user(account.id)
        assert stored.failed_logins == 0 and not stored.locked_until
        service.authenticate("keyholder", PASSWORD)


# ---------------------------------------------------------------------------
# The other two ways back
# ---------------------------------------------------------------------------


class TestAnAdministratorReset:
    def test_is_recorded_as_a_password_reset(self, service, account, repo):
        colleague = service.create_account(
            "colleague", "Colleague", "annotator", PASSWORD, must_change=False
        )
        temporary = service.reset_password(account, colleague)

        assert _events(repo, "password_reset")
        user = service.authenticate("colleague", temporary)
        assert user.must_change_password


class TestTheWorkstationReset:
    def test_sets_a_temporary_password_that_must_be_changed(self, service, account):
        user, temporary = service.reset_password_at_workstation("keyholder", "george")

        signed_in = service.authenticate("keyholder", temporary)
        assert signed_in.id == account.id and signed_in.must_change_password

    def test_names_the_computer_account_that_made_it(self, service, account, repo):
        service.reset_password_at_workstation("keyholder", "george")
        resets = _events(repo, "password_reset")
        assert resets and "george" in resets[0].detail

    def test_clears_a_lockout(self, service, account, settings):
        from aria.security.auth import AuthenticationError

        settings.max_failed_logins = 1
        with pytest.raises(AuthenticationError):
            service.authenticate("keyholder", "wrong")
        _user, temporary = service.reset_password_at_workstation("keyholder", "george")
        service.authenticate("keyholder", temporary)

    def test_an_unknown_account_is_said_so(self, service):
        with pytest.raises(LookupError, match="nobody"):
            service.reset_password_at_workstation("nobody", "george")


# ---------------------------------------------------------------------------
# Upgrading
# ---------------------------------------------------------------------------


def _database_without_recovery_columns(path):
    """A database as the previous build left it: no recovery columns, version 5."""
    from aria.core.models import Role, User
    from aria.security.auth import hash_password
    from aria.store.db import open_database
    from aria.store.repository import Repository

    db = open_database(path)
    digest, salt = hash_password(PASSWORD)
    Repository(db).create_user(
        User(username="earlier", role=Role.ADMIN, password_hash=digest, password_salt=salt)
    )
    db.set_meta("db_schema_version", "5")
    db.close()

    raw = sqlite3.connect(path)
    for column in ("recovery_hash", "recovery_salt", "recovery_created_at"):
        raw.execute(f"ALTER TABLE users DROP COLUMN {column}")
    raw.commit()
    raw.close()


class TestAnEarlierDatabase:
    def test_gains_the_recovery_columns_and_its_accounts_still_sign_in(self, tmp_path, settings):
        from aria.security.auth import AuthService
        from aria.store.db import open_database
        from aria.store.repository import Repository
        from aria.version import DB_SCHEMA_VERSION

        path = tmp_path / "earlier.db"
        _database_without_recovery_columns(path)

        db = open_database(path)
        try:
            columns = {row[1] for row in db.query("PRAGMA table_info(users)")}
            assert {"recovery_hash", "recovery_salt", "recovery_created_at"} <= columns
            assert db.schema_version() == DB_SCHEMA_VERSION

            service = AuthService(Repository(db), settings)
            user = service.authenticate("earlier", PASSWORD)
            assert not user.has_recovery_key
            service.issue_recovery_key(user)
        finally:
            db.close()

    def test_can_be_read_without_being_migrated(self, tmp_path):
        """Listing accounts opens the file read only, which does not migrate."""
        from aria.store.db import open_database
        from aria.store.repository import Repository

        path = tmp_path / "earlier.db"
        _database_without_recovery_columns(path)

        db = open_database(path, read_only=True)
        try:
            users = Repository(db).list_users()
            assert [u.username for u in users] == ["earlier"]
            assert not users[0].has_recovery_key
            assert db.schema_version() == 5
        finally:
            db.close()


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """An installation in a temporary folder, with one account in it."""
    monkeypatch.setenv("ARIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARIA_CONFIG_DIR", str(tmp_path / "cfg"))
    import aria.config as config_module

    monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)

    from aria.config import get_config
    from aria.security.auth import AuthService
    from aria.store.db import open_database
    from aria.store.repository import Repository

    config = get_config()
    db = open_database(config.paths.database)
    AuthService(Repository(db), config.settings).create_account(
        "solo", "Solo Researcher", "project_administrator", PASSWORD, must_change=False
    )
    db.close()
    return config


def _open(config):
    from aria.security.auth import AuthService
    from aria.store.db import open_database
    from aria.store.repository import Repository

    db = open_database(config.paths.database)
    return db, Repository(db), AuthService(Repository(db), config.settings)


class TestResettingFromTheCommandLine:
    def test_prints_a_temporary_password_that_signs_in(self, installation, capsys):
        from aria.cli import main

        assert main(["--reset-password", "solo"]) == 0
        out = capsys.readouterr().out
        temporary = re.search(r"Temporary password: (\S+)", out).group(1)

        db, repo, service = _open(installation)
        try:
            user = service.authenticate("solo", temporary)
            assert user.must_change_password
            assert any(r.event == "password_reset" for r in repo.audit_records(limit=50))
        finally:
            db.close()

    def test_reports_as_json(self, installation, capsys):
        import json

        from aria.cli import main

        assert main(["--reset-password", "solo", "--json"]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["reset"] and report["username"] == "solo"
        assert report["temporary_password"] and report["must_change_password"]

    def test_with_no_name_lists_the_accounts_and_changes_nothing(self, installation, capsys):
        from aria.cli import main

        before = installation.paths.database.stat().st_mtime_ns
        assert main(["--reset-password"]) == 0
        out = capsys.readouterr().out
        assert "solo" in out and "--reset-password solo" in out
        assert installation.paths.database.stat().st_mtime_ns == before

    def test_an_unknown_name_fails_and_lists_the_real_ones(self, installation, capsys):
        from aria.cli import main

        assert main(["--reset-password", "nobody"]) == 1
        out = capsys.readouterr().out
        assert "nobody" in out and "solo" in out

    def test_runs_through_the_real_entry_point_without_the_interface(
        self, installation, capsys, monkeypatch
    ):
        import sys

        from aria.__main__ import main

        monkeypatch.setattr(sys, "argv", ["aria", "--reset-password", "solo"])
        assert main() == 0
        assert "Temporary password" in capsys.readouterr().out

    def test_with_nowhere_to_print_it_shows_a_window(self, installation, monkeypatch):
        """A packaged build is windowed and has no console to print into."""
        import sys

        from PySide6.QtWidgets import QMessageBox

        from aria.cli import main

        monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
        shown = []
        monkeypatch.setattr(
            QMessageBox, "information",
            staticmethod(lambda parent, title, text: shown.append(text)),
        )
        monkeypatch.setattr(sys, "stdout", None)
        assert main(["--reset-password", "solo"]) == 0
        assert shown and "Temporary password" in shown[0]

    def test_the_command_offered_in_the_window_is_this_one(self):
        from aria.cli import build_parser, workstation_reset_command

        command = workstation_reset_command("solo")
        assert command.endswith("--reset-password solo")
        assert build_parser().parse_args(["--reset-password", "solo"]).reset_password == "solo"


# ---------------------------------------------------------------------------
# The windows
# ---------------------------------------------------------------------------


@pytest.fixture
def qt(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def config(paths, settings):
    from aria.config import Config

    c = Config(paths)
    c.settings = settings
    return c


class TestTheSignInWindow:
    def test_offers_a_reset(self, qt, repo, config):
        from aria.ui.dialogs.login_dialog import LoginDialog

        dialog = LoginDialog(repo, config)
        assert dialog.forgot_button.text() == "Forgot password?"
        assert not dialog.forgot_button.isHidden()

    def test_a_reset_comes_back_ready_to_sign_in(self, qt, repo, config, service, account, monkeypatch):
        from PySide6.QtWidgets import QDialog

        from aria.ui.dialogs import login_dialog
        from aria.ui.dialogs.login_dialog import LoginDialog

        key = service.issue_recovery_key(account)
        shown = []

        def fill_and_apply(self):
            self.recovery_key.setText(key)
            self.new_password.setText(NEW_PASSWORD)
            self.confirm.setText(NEW_PASSWORD)
            self._apply()
            return QDialog.Accepted if self.user else QDialog.Rejected

        monkeypatch.setattr(login_dialog.ResetPasswordDialog, "exec", fill_and_apply)
        monkeypatch.setattr(
            login_dialog.RecoveryKeyDialog, "exec",
            lambda self: shown.append(self.panel.key) or QDialog.Accepted,
        )

        dialog = LoginDialog(repo, config)
        dialog.username.setText("keyholder")
        dialog._forgot_password()

        assert shown and shown[0] != key, "The next key was not shown"
        assert dialog.username.text() == "keyholder"
        assert "reset" in dialog.message.text().lower()
        service.authenticate("keyholder", NEW_PASSWORD)

    def test_an_account_without_a_key_is_offered_one(self, qt, repo, config, service, account, monkeypatch):
        from PySide6.QtWidgets import QDialog, QMessageBox

        from aria.ui.dialogs import login_dialog
        from aria.ui.dialogs.login_dialog import LoginDialog

        monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
        monkeypatch.setattr(
            QMessageBox, "clickedButton",
            lambda self: next(b for b in self.buttons() if b.text() == "Create a recovery key"),
        )
        shown = []
        monkeypatch.setattr(
            login_dialog.RecoveryKeyDialog, "exec",
            lambda self: shown.append(self.panel.key) or QDialog.Accepted,
        )

        dialog = LoginDialog(repo, config)
        dialog.username.setText("keyholder")
        dialog.password.setText(PASSWORD)
        dialog._attempt()

        assert dialog.user is not None
        assert shown, "No recovery key was shown"
        assert repo.get_user(account.id).has_recovery_key
        service.reset_with_recovery_key("keyholder", shown[0], NEW_PASSWORD)

    def test_an_account_with_a_key_is_not_asked_again(self, qt, repo, config, service, account, monkeypatch):
        from aria.ui.dialogs.login_dialog import LoginDialog

        service.issue_recovery_key(account)
        asked = []
        monkeypatch.setattr(LoginDialog, "_offer_recovery_key", lambda self, user: asked.append(user))

        dialog = LoginDialog(repo, config)
        dialog.username.setText("keyholder")
        dialog.password.setText(PASSWORD)
        dialog._attempt()

        assert dialog.user is not None and not asked


class TestTheResetWindow:
    def test_refuses_a_wrong_key_in_words(self, qt, service, account):
        from aria.ui.dialogs.recovery_dialog import ResetPasswordDialog

        service.issue_recovery_key(account)
        dialog = ResetPasswordDialog(service, "keyholder")
        dialog.recovery_key.setText("AAAA-BBBB-CCCC-DDDD-EEEE")
        dialog.new_password.setText(NEW_PASSWORD)
        dialog.confirm.setText(NEW_PASSWORD)
        dialog._apply()

        assert dialog.user is None
        assert "not correct" in dialog.message.text()

    def test_refuses_passwords_that_do_not_match(self, qt, service, account):
        from aria.ui.dialogs.recovery_dialog import ResetPasswordDialog

        key = service.issue_recovery_key(account)
        dialog = ResetPasswordDialog(service, "keyholder")
        dialog.recovery_key.setText(key)
        dialog.new_password.setText(NEW_PASSWORD)
        dialog.confirm.setText("SomethingElse-2026")
        dialog._apply()

        assert dialog.user is None
        assert "do not match" in dialog.message.text()

    def test_says_what_to_do_without_a_key(self, qt, service):
        from aria.ui.dialogs.recovery_dialog import ResetPasswordDialog

        dialog = ResetPasswordDialog(service, "")
        dialog.username.setText("solo")
        assert dialog.command.text().endswith("--reset-password solo")


class TestSetupGivesTheFirstAccountAKey:
    def _wizard(self, paths, repo, config, qt):
        from aria.ui.dialogs.first_run import FirstRunWizard

        wizard = FirstRunWizard(repo, paths, config, None)
        wizard.account_page.username.setText("solo")
        wizard.account_page.display_name.setText("Solo Researcher")
        wizard.account_page.password.setText(PASSWORD)
        wizard.account_page.confirm.setText(PASSWORD)
        wizard.project_page.name.setText("Solo project")
        return wizard

    def test_the_key_shown_is_the_key_that_works(self, qt, paths, repo, config, service):
        wizard = self._wizard(paths, repo, config, qt)
        assert wizard.recovery_key in wizard.recovery_page.panel.key_label.text()
        wizard.accept()

        assert wizard.created_user.has_recovery_key
        service.reset_with_recovery_key("solo", wizard.recovery_key, NEW_PASSWORD)

    def test_the_page_waits_until_the_key_has_been_kept(self, qt, paths, repo, config):
        wizard = self._wizard(paths, repo, config, qt)
        page = wizard.recovery_page
        assert not page.isComplete()
        page.kept.setChecked(True)
        assert page.isComplete()

    def test_the_page_comes_after_the_account_and_names_it(self, qt, paths, repo, config):
        wizard = self._wizard(paths, repo, config, qt)
        ids = wizard.pageIds()
        assert wizard.page(ids[2]) is wizard.account_page
        assert wizard.page(ids[3]) is wizard.recovery_page

        wizard.recovery_page.panel.set_account("solo")
        assert "solo" in wizard.recovery_page.panel.explanation.text()
