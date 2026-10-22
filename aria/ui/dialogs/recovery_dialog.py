"""Recovery keys: showing a new one, and using one to reset a forgotten password.

A recovery key is the way back into an account that needs nobody else. It is
shown once, when it is issued. ARIA keeps a digest of it and not the key, so the
panel that shows it says so plainly and offers to copy it or save it.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...security.auth import AuthenticationError, AuthService
from ...version import APP_NAME
from ..theme import PALETTE
from ..widgets.common import make_button


class RecoveryKeyPanel(QWidget):
    """The key, what it is for, and two ways to keep it."""

    def __init__(self, key: str, username: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self.username = username

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.key_label = QLabel(key, self)
        self.key_label.setProperty("mono", True)
        self.key_label.setAlignment(Qt.AlignCenter)
        self.key_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.key_label.setStyleSheet(
            f"font-family: 'Consolas', 'SF Mono', 'DejaVu Sans Mono', monospace;"
            f" font-size: 17pt; font-weight: 600; letter-spacing: 1px;"
            f" padding: 14px; border: 1px solid {PALETTE.border};"
            f" background: {PALETTE.panel_alt};"
        )
        layout.addWidget(self.key_label)

        self.explanation = QLabel(self)
        self.explanation.setWordWrap(True)
        layout.addWidget(self.explanation)

        keeping = QLabel(
            "Keep it where other people cannot read it, such as a password "
            "manager or a printed copy filed away. To use it, choose Forgot "
            "password? when signing in. Using it replaces it with a new key, "
            "which is shown to you then.",
            self,
        )
        keeping.setWordWrap(True)
        keeping.setProperty("dim", True)
        layout.addWidget(keeping)

        row = QHBoxLayout()
        self.copy_button = make_button("Copy", "copy", parent=self)
        self.save_button = make_button("Save as a text file", "save", parent=self)
        for button in (self.copy_button, self.save_button):
            button.setAutoDefault(False)
            row.addWidget(button)
        self.feedback = QLabel("", self)
        self.feedback.setProperty("dim", True)
        row.addWidget(self.feedback, 1)
        layout.addLayout(row)

        self.copy_button.clicked.connect(self._copy)
        self.save_button.clicked.connect(self._save)
        self.set_account(username)

    def set_account(self, username: str) -> None:
        self.username = username
        whose = f"the account {username}" if username else "this account"
        self.explanation.setText(
            f"This key resets the password for {whose} if it is ever "
            f"forgotten. It is shown only now: {APP_NAME} keeps a digest of it, "
            f"not the key itself, so it cannot be shown again."
        )

    def file_text(self) -> str:
        return (
            f"{APP_NAME} recovery key\n\n"
            f"Account:  {self.username}\n"
            f"Key:      {self.key}\n"
            f"Issued:   {date.today().isoformat()}\n\n"
            f"Choose Forgot password? when signing in to use it. Using it\n"
            f"replaces it with a new key.\n"
        )

    def _copy(self) -> None:
        QGuiApplication.clipboard().setText(self.key)
        self.feedback.setText("Copied.")

    def _save(self) -> None:
        name = f"{APP_NAME} recovery key {self.username}".strip() + ".txt"
        path, _filter = QFileDialog.getSaveFileName(
            self, "Save the recovery key", name, "Text files (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.file_text())
        except OSError as exc:
            QMessageBox.warning(self, "The key was not saved", str(exc))
            return
        self.feedback.setText("Saved.")


class RecoveryKeyDialog(QDialog):
    """Shows a recovery key that has just been issued."""

    def __init__(self, key: str, username: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recovery key")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        heading = QLabel("Keep this recovery key", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        self.panel = RecoveryKeyPanel(key, username, self)
        layout.addWidget(self.panel)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok, Qt.Horizontal, self)
        buttons.button(QDialogButtonBox.Ok).setText("I have kept it")
        buttons.button(QDialogButtonBox.Ok).setProperty("accent", True)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class ResetPasswordDialog(QDialog):
    """Sets a new password for whoever holds the account's recovery key."""

    def __init__(self, service: AuthService, username: str = "", parent=None):
        super().__init__(parent)
        self.service = service
        self.user = None
        self.new_key = ""

        self.setWindowTitle("Reset a forgotten password")
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        heading = QLabel("Reset a forgotten password", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        explanation = QLabel(
            "Enter the recovery key that was shown when the account was set "
            "up, and choose a new password.",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setProperty("dim", True)
        layout.addWidget(explanation)

        form = QFormLayout()
        form.setSpacing(9)
        self.username = QLineEdit(username, self)
        self.recovery_key = QLineEdit(self)
        self.recovery_key.setPlaceholderText("XXXX-XXXX-XXXX-XXXX-XXXX")
        self.new_password = QLineEdit(self)
        self.new_password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(self)
        self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Username", self.username)
        form.addRow("Recovery key", self.recovery_key)
        form.addRow("New password", self.new_password)
        form.addRow("Confirm", self.confirm)
        layout.addLayout(form)

        self.message = QLabel("", self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        without = QLabel("No recovery key?", self)
        without.setProperty("subheading", True)
        layout.addWidget(without)

        alternatives = QLabel(
            "Another administrator can reset the password from "
            "Administration, Accounts, and give you a temporary one.\n\n"
            "If there is no other administrator, anyone at this workstation can "
            "reset it from a terminal with the command below. The reset is "
            "recorded, the old password stops working, and a new one is chosen "
            "at the next sign in.",
            self,
        )
        alternatives.setWordWrap(True)
        alternatives.setProperty("dim", True)
        layout.addWidget(alternatives)

        command_row = QHBoxLayout()
        self.command = QLabel("", self)
        self.command.setProperty("mono", True)
        self.command.setWordWrap(True)
        self.command.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_command = make_button("Copy command", "copy", parent=self)
        self.copy_command.setAutoDefault(False)
        command_row.addWidget(self.command, 1)
        command_row.addWidget(self.copy_command, 0, Qt.AlignTop)
        layout.addLayout(command_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Reset password")
        buttons.button(QDialogButtonBox.Ok).setProperty("accent", True)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.username.textChanged.connect(self._show_command)
        self.new_password.textChanged.connect(self._validate)
        self.confirm.textChanged.connect(self._validate)
        self.copy_command.clicked.connect(
            lambda: QGuiApplication.clipboard().setText(self.command.text())
        )
        self._show_command()
        (self.recovery_key if username else self.username).setFocus()

    def _show_command(self) -> None:
        from ...cli import workstation_reset_command

        self.command.setText(workstation_reset_command(self.username.text().strip()))

    def _problems(self) -> list:
        problems = self.service.settings.validate_password(self.new_password.text())
        if self.new_password.text() != self.confirm.text():
            problems.append("The two passwords do not match.")
        return problems

    def _validate(self) -> None:
        problems = self._problems()
        if problems:
            self.message.setText("   ".join(problems))
            self.message.setStyleSheet(f"color: {PALETTE.warning};")
        else:
            self.message.setText("This password meets the policy.")
            self.message.setStyleSheet(f"color: {PALETTE.success};")

    def _apply(self) -> None:
        if not self.username.text().strip() or not self.recovery_key.text().strip():
            self.message.setText("Enter the username and the recovery key.")
            self.message.setStyleSheet(f"color: {PALETTE.warning};")
            return
        problems = self._problems()
        if problems:
            self.message.setText("   ".join(problems))
            self.message.setStyleSheet(f"color: {PALETTE.danger};")
            return
        try:
            self.user, self.new_key = self.service.reset_with_recovery_key(
                self.username.text(), self.recovery_key.text(), self.new_password.text()
            )
        except (AuthenticationError, ValueError) as exc:
            self.message.setText(str(exc))
            self.message.setStyleSheet(f"color: {PALETTE.danger};")
            return
        self.accept()
