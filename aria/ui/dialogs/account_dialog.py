"""Creating an account from the administration module."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from ...core.models import Role
from ...security.auth import AuthService, generate_temporary_password, permissions_for
from ..theme import PALETTE

ROLE_DESCRIPTIONS = {
    Role.ANNOTATOR: (
        "Creates and edits annotations, and submits them for review. Cannot "
        "import, export or change project settings."
    ),
    Role.REVIEWER: (
        "Accepts, returns or adjudicates an annotation set, validates "
        "calibration and reads agreement reports. Can also annotate."
    ),
    Role.ADMIN: (
        "Manages projects, label schemas, accounts, calibration policy, "
        "thresholds and exports."
    ),
    Role.DATA_MANAGER: (
        "Imports deidentified studies, assigns cases, runs dataset quality "
        "checks and produces exports and bundles."
    ),
    Role.AUDITOR: (
        "Reads the immutable history without editing clinical content. "
        "Coordinate lists are withheld from the audit view for this role."
    ),
}


class AccountDialog(QDialog):
    """Creates one account and shows its temporary password once."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.service = AuthService(controller.repo, controller.settings)
        self.created = None

        self.setWindowTitle("Add an account")
        self.setMinimumWidth(520)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        heading = QLabel("New account", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        form = QFormLayout()
        form.setSpacing(9)

        self.username = QLineEdit(self)
        self.username.setPlaceholderText("Used to sign in")
        self.display_name = QLineEdit(self)
        self.display_name.setPlaceholderText("Shown in the interface")

        self.role = QComboBox(self)
        for role in Role.ALL:
            self.role.addItem(Role.DISPLAY[role], role)
            self.role.setItemData(
                self.role.count() - 1, ROLE_DESCRIPTIONS[role], Qt.ToolTipRole
            )

        self.pseudonym = QLineEdit(self)
        self.pseudonym.setPlaceholderText("Left blank, one is generated")
        self.pseudonym.setToolTip(
            "Written into exports in place of the account name, so an export "
            "identifies who produced an annotation without naming them."
        )

        form.addRow("Username", self.username)
        form.addRow("Display name", self.display_name)
        form.addRow("Role", self.role)
        form.addRow("Export pseudonym", self.pseudonym)
        layout.addLayout(form)

        self.role_description = QLabel("", self)
        self.role_description.setWordWrap(True)
        self.role_description.setProperty("dim", True)
        layout.addWidget(self.role_description)

        self.permissions = QLabel("", self)
        self.permissions.setWordWrap(True)
        self.permissions.setProperty("dim", True)
        layout.addWidget(self.permissions)

        self.require_calibration = QCheckBox(
            "This annotator must complete the calibration set before production cases",
            self,
        )
        self.require_calibration.setChecked(True)
        layout.addWidget(self.require_calibration)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel, Qt.Horizontal, self
        )
        buttons.button(QDialogButtonBox.Ok).setText("Create account")
        buttons.button(QDialogButtonBox.Ok).setProperty("accent", True)
        buttons.accepted.connect(self._create)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.role.currentIndexChanged.connect(self._on_role)
        self._on_role(0)
        self.username.setFocus()

    def _on_role(self, _index: int) -> None:
        role = self.role.currentData()
        self.role_description.setText(ROLE_DESCRIPTIONS.get(role, ""))
        granted = sorted(p.value.replace("_", " ") for p in permissions_for(role))
        self.permissions.setText("Permissions: " + ", ".join(granted) + ".")
        self.require_calibration.setVisible(role == Role.ANNOTATOR)

    def _create(self) -> None:
        temporary = generate_temporary_password()
        try:
            user = self.service.create_account(
                self.username.text().strip(),
                self.display_name.text().strip(),
                self.role.currentData(),
                temporary,
                self.pseudonym.text().strip(),
                must_change=True,
            )
        except ValueError as exc:
            QMessageBox.warning(self, "The account could not be created", str(exc))
            return

        if self.role.currentData() == Role.ANNOTATOR and not self.require_calibration.isChecked():
            user.calibration_passed = True
            self.controller.repo.update_user(
                user, "Calibration set requirement waived at account creation."
            )

        self.created = user
        QMessageBox.information(
            self, "Account created",
            f"{user.display_name} was created as a {Role.DISPLAY[user.role].lower()}.\n\n"
            f"Username: {user.username}\n"
            f"Temporary password: {temporary}\n"
            f"Export pseudonym: {user.pseudonym}\n\n"
            f"They must change the password at first sign in. Pass it to them "
            f"directly rather than by email.",
        )
        self.accept()
