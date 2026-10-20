"""Sign in, the ways back from a forgotten password, and guest access.

The sign in window offers a reset with the account's recovery key, and, where an
administrator allows it, a guest session that can look and change nothing.
"""

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
    QMessageBox,
    QVBoxLayout,
)

from ...security.auth import AccountLockedError, AuthenticationError, AuthService
from ...version import APP_LONG_NAME, APP_NAME, APP_VERSION
from ..icons import application_icon
from ..theme import PALETTE
from ..widgets.common import make_button
from .recovery_dialog import RecoveryKeyDialog, ResetPasswordDialog


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

        forgot_row = QHBoxLayout()
        forgot_row.addStretch(1)
        self.forgot_button = make_button(
            "Forgot password?", "lock",
            tooltip="Reset the password with the account's recovery key.",
            parent=self,
        )
        self.forgot_button.setAutoDefault(False)
        forgot_row.addWidget(self.forgot_button)
        layout.addLayout(forgot_row)

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

        # Offered only where an administrator has turned guest access on.
        self.guest_button = make_button(
            "Continue as guest", "visible",
            tooltip=(
                "Look at cases and images without an account. A guest cannot "
                "annotate, change or export anything."
            ),
            parent=self,
        )
        self.guest_button.setAutoDefault(False)
        self.guest_button.setVisible(bool(config.settings.allow_guest_access))

        button_row = QHBoxLayout()
        button_row.addWidget(self.guest_button)
        button_row.addStretch(1)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)

        version = QLabel(f"Version {APP_VERSION}", self)
        version.setProperty("dim", True)
        version.setAlignment(Qt.AlignRight)
        layout.addWidget(version)

        self.username.setFocus()
        self.password.returnPressed.connect(self._attempt)
        self.username.returnPressed.connect(lambda: self.password.setFocus())
        self.forgot_button.clicked.connect(self._forgot_password)
        self.guest_button.clicked.connect(self._continue_as_guest)

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

        if not user.has_recovery_key:
            self._offer_recovery_key(user)

        self.user = user
        self.accept()

    def _offer_recovery_key(self, user) -> None:
        """Offer a recovery key to an account that has none.

        Asked at every sign in until there is one, because the alternative is
        an account that only an administrator can get back into.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("No recovery key")
        box.setText("This account has no recovery key.")
        box.setInformativeText(
            "A recovery key resets a forgotten password without an "
            "administrator. It is shown once, for you to copy or save."
        )
        create = box.addButton("Create a recovery key", QMessageBox.AcceptRole)
        box.addButton("Not now", QMessageBox.RejectRole)
        box.setDefaultButton(create)
        box.exec()
        if box.clickedButton() is create:
            key = self.service.issue_recovery_key(user)
            RecoveryKeyDialog(key, user.username, self).exec()

    def _forgot_password(self) -> None:
        dialog = ResetPasswordDialog(self.service, self.username.text().strip(), self)
        if dialog.exec() != QDialog.Accepted or dialog.user is None:
            return
        # The key just used is spent, so the next one has to be seen now.
        RecoveryKeyDialog(dialog.new_key, dialog.user.username, self).exec()
        self.username.setText(dialog.user.username)
        self.password.clear()
        self.password.setFocus()
        self._show_message("The password was reset. Sign in with the new one.", "ok")

    def _continue_as_guest(self) -> None:
        try:
            self.user = self.service.sign_in_as_guest()
        except AuthenticationError as exc:
            self._show_message(str(exc))
            return
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
            "before continuing."
            if current_password
            else "Enter the current password, then the new one twice.",
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
