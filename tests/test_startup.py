"""The real application startup path.

This file exists because a first launch crashed on a line no other test reached.
The interface tests built the main window directly and the first run test called
``wizard.accept()``, so neither ever executed ``aria.app.run``. The bug was a Qt
result code read from an instance rather than from the class, which raises only
when that exact line runs.

These tests drive ``run`` itself, with the dialogs answered automatically, so
the startup sequence is covered end to end: first run, sign in, and the failure
paths where someone cancels.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Static scan for the class of error that caused the crash
# ---------------------------------------------------------------------------

#: Qt enum members that live on the class, never on an instance. Reading one
#: from an instance raises AttributeError at runtime.
CLASS_ONLY_MEMBERS = {
    "Accepted", "Rejected",
    "Ok", "Cancel", "Yes", "No", "Save", "Discard", "Close", "Apply",
    "Critical", "Warning", "Information", "Question",
    "AlignLeft", "AlignRight", "AlignCenter", "AlignTop", "AlignBottom",
    "Horizontal", "Vertical",
    "UserRole", "DisplayRole", "ToolTipRole",
    "ReadOnly", "Password", "Normal",
    "ItemIsUserCheckable", "ItemIsEnabled", "ItemIsSelectable",
    "Checked", "Unchecked", "PartiallyChecked",
    "Stretch", "Fixed", "ResizeToContents", "Interactive",
    "Expanding", "Preferred", "Maximum", "Minimum",
    "SolidLine", "DashLine", "DotLine", "NoPen", "NoBrush",
    "Antialiasing", "SmoothPixmapTransform",
    "ElideRight", "ElideLeft", "ElideMiddle",
    "DownArrow", "RightArrow", "UpArrow", "LeftArrow",
    "WaitCursor", "ArrowCursor", "CrossCursor", "PointingHandCursor",
}

#: Names that are Qt classes, where the access is correct. Anything else that
#: looks like an instance is reported.
QT_CLASS_PATTERN = re.compile(r"^(Q[A-Z]\w*|Qt)$")


def _python_files():
    for path in sorted((ROOT / "aria").rglob("*.py")):
        yield path


class TestNoInstanceEnumAccess:
    """Qt enum members must be read from the class, not from an instance."""

    def test_no_enum_read_from_an_instance(self):
        offences = []

        for path in _python_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Attribute):
                    continue
                if node.attr not in CLASS_ONLY_MEMBERS:
                    continue
                owner = node.value
                # Only a bare name can be checked statically. An attribute
                # chain such as QDialog.DialogCode.Accepted is already correct.
                if not isinstance(owner, ast.Name):
                    continue
                if QT_CLASS_PATTERN.match(owner.id):
                    continue
                offences.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: "
                    f"{owner.id}.{node.attr} reads a Qt enum from an instance; "
                    f"use the class instead"
                )

        assert not offences, "\n".join(offences)


# ---------------------------------------------------------------------------
# The startup sequence
# ---------------------------------------------------------------------------


@pytest.fixture
def startup_env(tmp_path, monkeypatch):
    """Point the application at a temporary data folder and stub the event loop."""
    from PySide6.QtWidgets import QApplication

    monkeypatch.setenv("ARIA_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ARIA_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    import aria.config as config_module

    monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)

    # The application would otherwise block in its event loop.
    monkeypatch.setattr(QApplication, "exec", lambda self: 0)

    return tmp_path


def _answer_first_run(monkeypatch, username="startup_admin", password="StartupTest-2026"):
    """Fill the first run wizard and accept it, as a person would."""
    from PySide6.QtWidgets import QDialog

    from aria.ui.dialogs.first_run import FirstRunWizard

    def fake_exec(self):
        # Wait for the compatibility check the wizard starts on that page.
        self.compatibility_page.initializePage()
        if getattr(self, "worker", None) is None and hasattr(self.compatibility_page, "worker"):
            self.compatibility_page.worker.wait(30_000)
        self.account_page.username.setText(username)
        self.account_page.display_name.setText("Startup Administrator")
        self.account_page.password.setText(password)
        self.account_page.confirm.setText(password)
        self.project_page.name.setText("Startup project")
        self.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FirstRunWizard, "exec", fake_exec)


def _answer_login(monkeypatch, username, password):
    from PySide6.QtWidgets import QDialog

    from aria.ui.dialogs.login_dialog import LoginDialog

    def fake_exec(self):
        self.username.setText(username)
        self.password.setText(password)
        self._attempt()
        return (
            QDialog.DialogCode.Accepted if self.user is not None
            else QDialog.DialogCode.Rejected
        )

    monkeypatch.setattr(LoginDialog, "exec", fake_exec)


class TestStartup:
    """``aria.app.run`` itself, not a reconstruction of it."""

    def test_first_run_creates_the_installation(self, startup_env, monkeypatch):
        """The path that crashed on a first launch."""
        from aria.app import run

        _answer_first_run(monkeypatch)
        # The tour would otherwise be scheduled onto a timer that never fires.
        import aria.ui.dialogs.tour as tour_module

        monkeypatch.setattr(tour_module, "should_offer_tour", lambda settings: False)

        assert run([]) == 0

        from aria.config import get_config
        from aria.store.db import open_database
        from aria.store.repository import Repository

        config = get_config()
        assert config.paths.database.exists(), "No database was created"
        assert config.settings.first_run_completed

        database = open_database(config.paths.database, read_only=True)
        try:
            repository = Repository(database)
            users = repository.list_users()
            projects = repository.list_projects()
            assert len(users) == 1
            assert users[0].username == "startup_admin"
            assert users[0].role == "project_administrator"
            assert len(projects) == 1
            assert projects[0].name == "Startup project"
            assert repository.verify_audit_chain()["valid"]
        finally:
            database.close()

    def test_second_launch_signs_in_and_opens_the_window(self, startup_env, monkeypatch):
        """The path taken on every launch after the first, through to a window.

        Asserting that the main window is actually constructed and shown is the
        point: a startup that returns zero without opening anything would look
        like success from the outside.
        """
        from aria.app import run

        import aria.ui.dialogs.tour as tour_module
        import aria.ui.main_window as main_window_module

        monkeypatch.setattr(tour_module, "should_offer_tour", lambda settings: False)
        _answer_first_run(monkeypatch)
        assert run([]) == 0

        # Start again. The installation now exists, so sign in is used instead.
        import aria.config as config_module

        monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)
        _answer_login(monkeypatch, "startup_admin", "StartupTest-2026")

        opened = {}
        real_show = main_window_module.MainWindow.show

        def record_show(self):
            opened["window"] = self
            opened["title"] = self.windowTitle()
            opened["modules"] = self.module_combo.count()
            opened["user"] = self.session.user.username if self.session.user else None
            real_show(self)

        monkeypatch.setattr(main_window_module.MainWindow, "show", record_show)

        assert run([]) == 0
        assert opened, "The main window was never shown"
        assert "ARIA" in opened["title"]
        assert opened["user"] == "startup_admin"
        assert opened["modules"] >= 5, "Modules were not populated"

        window = opened["window"]
        assert window.canvas is not None
        assert window.statusBar() is not None
        assert window.menuBar().actions(), "The menu bar is empty"

    def test_cancelling_first_run_leaves_no_account(self, startup_env, monkeypatch):
        from PySide6.QtWidgets import QDialog

        from aria.app import run
        from aria.ui.dialogs.first_run import FirstRunWizard

        monkeypatch.setattr(
            FirstRunWizard, "exec", lambda self: QDialog.DialogCode.Rejected
        )
        assert run([]) == 0

        from aria.config import get_config
        from aria.store.db import open_database
        from aria.store.repository import Repository

        database = open_database(get_config().paths.database, read_only=True)
        try:
            assert Repository(database).count_users() == 0
        finally:
            database.close()

    def test_cancelling_sign_in_exits_cleanly(self, startup_env, monkeypatch):
        from PySide6.QtWidgets import QDialog

        from aria.app import run
        from aria.ui.dialogs.login_dialog import LoginDialog

        import aria.ui.dialogs.tour as tour_module

        monkeypatch.setattr(tour_module, "should_offer_tour", lambda settings: False)
        _answer_first_run(monkeypatch)
        assert run([]) == 0

        import aria.config as config_module

        monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)
        monkeypatch.setattr(LoginDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
        assert run([]) == 0

    def test_a_wrong_password_does_not_open_the_application(self, startup_env, monkeypatch):
        from aria.app import run

        import aria.ui.dialogs.tour as tour_module

        monkeypatch.setattr(tour_module, "should_offer_tour", lambda settings: False)
        _answer_first_run(monkeypatch)
        assert run([]) == 0

        import aria.config as config_module

        monkeypatch.setattr(config_module, "_CONFIG", None, raising=False)
        _answer_login(monkeypatch, "startup_admin", "not-the-password")

        # Sign in fails, so the application closes rather than opening.
        assert run([]) == 0

        from aria.config import get_config
        from aria.store.db import open_database
        from aria.store.repository import Repository

        database = open_database(get_config().paths.database, read_only=True)
        try:
            failures = [
                r for r in Repository(database).audit_records(limit=50)
                if r.event == "login_failure"
            ]
            assert failures, "A failed sign in was not recorded"
        finally:
            database.close()


class TestCommandLineStartup:
    """The flags a build pipeline uses, through the real entry point."""

    @pytest.mark.parametrize("flag", ["--version", "--self-test", "--system-check"])
    def test_flag_runs_without_opening_the_interface(self, startup_env, flag, capsys):
        import sys

        from aria.__main__ import main

        original = sys.argv
        try:
            sys.argv = ["aria", flag]
            code = main()
        finally:
            sys.argv = original

        assert code == 0
        assert capsys.readouterr().out.strip(), f"{flag} produced no output"

    def test_help_prints_usage_and_exits(self, startup_env, capsys):
        """argparse exits the process for --help, which is what a shell expects."""
        import sys

        from aria.__main__ import main

        original = sys.argv
        try:
            sys.argv = ["aria", "--help"]
            with pytest.raises(SystemExit) as exit_info:
                main()
        finally:
            sys.argv = original

        assert exit_info.value.code == 0
        assert "usage" in capsys.readouterr().out.lower()
