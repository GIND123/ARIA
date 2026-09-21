"""Sign in, and the forced password change that follows a reset."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ...security.auth import AccountLockedError, AuthenticationError, AuthService
from ...version import APP_LONG_NAME, APP_NAME, APP_VERSION
from ..icons import application_icon
from ..theme import PALETTE


class LoginDialog(QDialog):
    """Collects credentials and returns an authenticated user."""

    def __init__(self, repository, config, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.config = config
        self.service = AuthService(repository, config.settings)
        self.user = None

        self.setWindowTitle(f"Sign in to {APP_NAME}")
        self.setWindowIcon(application_icon())
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(14)

        title = QLabel(APP_NAME, self)
        title.setProperty("heading", True)
        subtitle = QLabel(APP_LONG_NAME, self)
        subtitle.setProperty("dim", True)
        subtitle.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        form = QFormLayout()
        form.setSpacing(9)
        self.username = QLineEdit(self)
        self.username.setPlaceholderText("Username")
        self.password = QLineEdit(self)
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        layout.addLayout(form)

        self.message = QLabel("", self)
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        layout.addWidget(self.message)

        note = QLabel(
            "Every action is recorded against the account that performed it.",
            self,
        )
        note.setProperty("dim", True)
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Sign in")
        buttons.button(QDialogButtonBox.Ok).setProperty("accent", True)
        buttons.accepted.connect(self._attempt)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        version = QLabel(f"Version {APP_VERSION}", self)
        version.setProperty("dim", True)
        version.setAlignment(Qt.AlignRight)
        layout.addWidget(version)

        self.username.setFocus()
        self.password.returnPressed.connect(self._attempt)
        self.username.returnPressed.connect(lambda: self.password.setFocus())

    def _show_message(self, text: str, level: str = "danger") -> None:
        colour = {"danger": PALETTE.danger, "warn": PALETTE.warning, "ok": PALETTE.success}
        self.message.setText(text)
        self.message.setStyleSheet(f"color: {colour.get(level, PALETTE.danger)};")
        self.message.setVisible(True)

    def _attempt(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            self._show_message("Enter a username and a password.", "warn")
            return

        try:
            user = self.service.authenticate(username, password)
        except AccountLockedError as exc:
            self._show_message(str(exc))
            return
        except AuthenticationError as exc:
            self._show_message(str(exc))
            self.password.clear()
            self.password.setFocus()
            return

        if user.must_change_password:
            dialog = ChangePasswordDialog(self.service, user, password, self)
            if dialog.exec() != QDialog.Accepted:
                self._show_message(
                    "The password must be changed before signing in.", "warn"
                )
                return

        self.user = user
        self.accept()


class ChangePasswordDialog(QDialog):
    """Forced password change after a reset, and voluntary change later."""

    def __init__(self, service: AuthService, user, current_password: str = "", parent=None):
        super().__init__(parent)
        self.service = service
        self.user = user
        self.known_current = current_password

        self.setWindowTitle("Change password")
        self.setMinimumWidth(430)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 16)
        layout.setSpacing(12)

        heading = QLabel("Choose a new password", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        explanation = QLabel(
            "This account is using a temporary password. Choose one only you know "
            "before continuing.",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setProperty("dim", True)
        layout.addWidget(explanation)

        form = QFormLayout()
        form.setSpacing(9)
        self.current = QLineEdit(self)
        self.current.setEchoMode(QLineEdit.Password)
        if current_password:
            self.current.setText(current_password)
            self.current.setEnabled(False)
        self.new_password = QLineEdit(self)
        self.new_password.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit(self)
        self.confirm.setEchoMode(QLineEdit.Password)
        form.addRow("Current password", self.current)
        form.addRow("New password", self.new_password)
        form.addRow("Confirm", self.confirm)
        layout.addLayout(form)

        self.message = QLabel("", self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Change password")
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.new_password.textChanged.connect(self._validate)
        self.confirm.textChanged.connect(self._validate)
        self.new_password.setFocus()

    def _validate(self) -> None:
        problems = self.service.settings.validate_password(self.new_password.text())
        if self.new_password.text() != self.confirm.text():
            problems.append("The two passwords do not match.")
        if problems:
            self.message.setText("   ".join(problems))
            self.message.setStyleSheet(f"color: {PALETTE.warning};")
        else:
            self.message.setText("This password meets the policy.")
            self.message.setStyleSheet(f"color: {PALETTE.success};")

    def _apply(self) -> None:
        try:
            self.service.change_password(
                self.user, self.current.text(), self.new_password.text()
            )
        except (AuthenticationError, ValueError) as exc:
            self.message.setText(str(exc))
            self.message.setStyleSheet(f"color: {PALETTE.danger};")
            return
        self.accept()
