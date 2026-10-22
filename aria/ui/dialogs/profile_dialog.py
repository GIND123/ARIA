"""Choosing who is annotating. No password, because there is nothing to prove.

This replaces the sign in window. It opens on the profile used last, so the
common case is pressing Enter, and a new profile is one field and one button
away rather than something only a setup wizard could create.

The window says plainly that a profile is a label and not a credential.
Somebody reading an audit trail months later needs to know whether the name
against an annotation was proved or merely selected, and the honest answer
here is selected.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ...core.profile import ProfileError, ProfileService
from ..icons import application_icon
from ..widgets.common import Banner, HLine, SectionLabel, make_button


class ProfileDialog(QDialog):
    """Pick the profile that will annotate, or make a new one."""

    def __init__(self, repository, config, parent=None):
        super().__init__(parent)
        self.repo = repository
        self.config = config
        self.service = ProfileService(repository)
        #: Set when the window is accepted.
        self.profile = None

        self.setWindowTitle("ARIA")
        self.setWindowIcon(application_icon())
        self.setMinimumWidth(460)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        heading = QLabel("Who is annotating?", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        intro = QLabel(
            "Every annotation is recorded against a profile, so that agreement "
            "between observers can be measured and the history says whose work "
            "each case is. A profile is a name, not a password: anybody at this "
            "workstation can choose any of them.",
            self,
        )
        intro.setWordWrap(True)
        intro.setProperty("dim", True)
        layout.addWidget(intro)

        # -- choose an existing one -------------------------------------------
        self.existing_row = QHBoxLayout()
        self.existing_row.setSpacing(8)
        self.existing_row.addWidget(QLabel("Profile", self))
        self.profiles = QComboBox(self)
        self.profiles.setMinimumWidth(240)
        self.existing_row.addWidget(self.profiles, 1)
        layout.addLayout(self.existing_row)

        layout.addWidget(HLine(self))
        layout.addWidget(SectionLabel("Or start a new one", self))

        new_row = QHBoxLayout()
        new_row.setSpacing(8)
        self.new_name = QLineEdit(self)
        self.new_name.setPlaceholderText("Name as it should appear in the history")
        new_row.addWidget(self.new_name, 1)
        self.add_button = make_button("Add profile", "user", parent=self)
        new_row.addWidget(self.add_button)
        layout.addLayout(new_row)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        self.start_button = make_button("Start annotating", "check", accent=True, parent=self)
        self.quit_button = make_button("Quit", parent=self)
        buttons.addButton(self.start_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.quit_button, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        self.add_button.clicked.connect(self._add)
        self.start_button.clicked.connect(self._start)
        self.quit_button.clicked.connect(self.reject)
        self.new_name.returnPressed.connect(self._add)

        self.reload()
        self.start_button.setDefault(True)

    # -- state ---------------------------------------------------------------

    def reload(self) -> None:
        """Fill the list, selecting the profile used last."""
        remembered = getattr(self.config.settings, "last_profile_id", "")
        self.profiles.blockSignals(True)
        self.profiles.clear()
        profiles = self.service.list_profiles()
        for user in profiles:
            label = f"{user.display_name}   ({user.pseudonym})"
            self.profiles.addItem(label, user.id)
        index = self.profiles.findData(remembered)
        if index >= 0:
            self.profiles.setCurrentIndex(index)
        self.profiles.blockSignals(False)

        has_any = bool(profiles)
        self.profiles.setEnabled(has_any)
        self.start_button.setEnabled(has_any)
        for i in range(self.existing_row.count()):
            widget = self.existing_row.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(has_any)

        if not has_any:
            self.banner.show_message(
                "No profiles yet. Put in a name to make the first one.", "info"
            )
            self.new_name.setFocus()
        else:
            self.banner.hide()

    # -- actions -------------------------------------------------------------

    def _add(self) -> None:
        name = self.new_name.text().strip()
        if not name:
            self.banner.show_message("Put in a name for the new profile.", "warn")
            self.new_name.setFocus()
            return
        try:
            created = self.service.create(name)
        except ProfileError as exc:
            self.banner.show_message(str(exc), "warn")
            return

        self.new_name.clear()
        self.reload()
        index = self.profiles.findData(created.id)
        if index >= 0:
            self.profiles.setCurrentIndex(index)
        self.banner.show_message(
            f"{created.display_name} added, with the export pseudonym "
            f"{created.pseudonym}.",
            "ok",
        )

    def _start(self) -> None:
        profile_id = self.profiles.currentData()
        if not profile_id:
            self.banner.show_message("Choose a profile, or make one.", "warn")
            return
        profile = self.service.get(profile_id)
        if profile is None:
            self.reload()
            return

        self.profile = profile
        self.config.settings.last_profile_id = profile.id
        try:
            self.config.save()
        except Exception:
            # Remembering the choice is a convenience. Failing to write it is
            # not a reason to refuse to start.
            pass
        self.accept()
