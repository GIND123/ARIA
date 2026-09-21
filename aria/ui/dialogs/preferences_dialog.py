"""Preferences: appearance, viewer, storage, import limits and security policy.

The security page holds the institutional policy settings: session timeout,
password rules, retention and encryption at rest. They are here rather than in a
file so an administrator can set them to an approved policy without editing
configuration by hand.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...io.fsutil import directory_size
from ...io.guards import human_bytes
from ..theme import PALETTE
from ..widgets.common import Banner, SectionLabel, make_button


class PreferencesDialog(QDialog):
    """Edits the stored settings."""

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.settings = config.settings

        self.setWindowTitle("Preferences")
        self.setMinimumSize(660, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)

        self.tabs.addTab(self._appearance_tab(), "Appearance")
        self.tabs.addTab(self._viewer_tab(), "Viewer")
        self.tabs.addTab(self._storage_tab(), "Storage")
        self.tabs.addTab(self._import_tab(), "Import")
        self.tabs.addTab(self._security_tab(), "Security")

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults,
            Qt.Horizontal, self,
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self._restore)
        layout.addWidget(buttons)

    # -- tabs ----------------------------------------------------------------

    def _appearance_tab(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(9)

        self.font_size = QSpinBox(page)
        self.font_size.setRange(7, 16)
        self.font_size.setValue(self.settings.font_point_size)
        self.font_size.setSuffix(" pt")
        form.addRow("Text size", self.font_size)

        self.ui_scale = QDoubleSpinBox(page)
        self.ui_scale.setRange(0.8, 2.0)
        self.ui_scale.setSingleStep(0.1)
        self.ui_scale.setDecimals(1)
        self.ui_scale.setValue(self.settings.ui_scale)
        self.ui_scale.setToolTip("Scales spacing and control sizes across the interface.")
        form.addRow("Interface scale", self.ui_scale)

        self.high_contrast = QCheckBox("High contrast palette", page)
        self.high_contrast.setChecked(self.settings.high_contrast_annotations)
        self.high_contrast.setToolTip(
            "Raises contrast throughout and brightens annotation colours. Useful "
            "on a dim display or in a bright room."
        )
        form.addRow("", self.high_contrast)

        self.show_labels = QCheckBox("Show label codes beside objects", page)
        self.show_labels.setChecked(self.settings.show_annotation_labels)
        form.addRow("", self.show_labels)

        self.line_width = QDoubleSpinBox(page)
        self.line_width.setRange(0.5, 6.0)
        self.line_width.setSingleStep(0.5)
        self.line_width.setValue(self.settings.annotation_line_width)
        self.line_width.setToolTip(
            "Stroke width in screen pixels. It does not change with zoom, so a "
            "thin cortical margin stays readable."
        )
        form.addRow("Annotation stroke width", self.line_width)

        self.landmark_size = QDoubleSpinBox(page)
        self.landmark_size.setRange(3.0, 20.0)
        self.landmark_size.setSingleStep(1.0)
        self.landmark_size.setValue(self.settings.landmark_size)
        form.addRow("Landmark size", self.landmark_size)

        self.remember_geometry = QCheckBox("Remember window position and layout", page)
        self.remember_geometry.setChecked(self.settings.remember_window_geometry)
        form.addRow("", self.remember_geometry)

        return page

    def _viewer_tab(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(9)

        self.zoom_step = QDoubleSpinBox(page)
        self.zoom_step.setRange(1.02, 2.0)
        self.zoom_step.setSingleStep(0.05)
        self.zoom_step.setDecimals(2)
        self.zoom_step.setValue(self.settings.zoom_step)
        form.addRow("Zoom step per wheel notch", self.zoom_step)

        self.crosshair = QCheckBox("Show a crosshair at the cursor", page)
        self.crosshair.setChecked(self.settings.crosshair)
        form.addRow("", self.crosshair)

        self.magnifier = QCheckBox("Magnifier available", page)
        self.magnifier.setChecked(self.settings.magnifier_enabled)
        form.addRow("", self.magnifier)

        self.magnifier_factor = QDoubleSpinBox(page)
        self.magnifier_factor.setRange(2.0, 12.0)
        self.magnifier_factor.setSingleStep(0.5)
        self.magnifier_factor.setValue(self.settings.magnifier_factor)
        form.addRow("Magnifier factor", self.magnifier_factor)

        self.probe = QCheckBox("Show the data probe strip", page)
        self.probe.setChecked(self.settings.show_data_probe)
        form.addRow("", self.probe)

        note = QLabel(
            "None of these change stored coordinates. Zoom, windowing, inversion "
            "and filters act on the display only.",
            page,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        form.addRow("", note)

        return page

    def _storage_tab(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        form = QFormLayout()
        form.setSpacing(9)

        row = QHBoxLayout()
        self.data_dir = QLineEdit(page)
        self.data_dir.setText(
            self.settings.data_dir_override or str(self.config.paths.data_dir)
        )
        browse = make_button("Browse", "folder", parent=page)
        browse.clicked.connect(self._browse_data_dir)
        row.addWidget(self.data_dir, 1)
        row.addWidget(browse)
        form.addRow("Data folder", row)

        self.keep_working = QCheckBox("Keep decoded working copies", page)
        self.keep_working.setChecked(self.settings.keep_working_copies)
        self.keep_working.setToolTip(
            "Reopening a case is faster with a working copy. Turning this off "
            "saves disk space and rebuilds from the retained original each time."
        )
        form.addRow("", self.keep_working)

        self.backup_on_launch = QCheckBox("Back up the database at startup", page)
        self.backup_on_launch.setChecked(self.settings.backup_on_launch)
        form.addRow("", self.backup_on_launch)

        self.backup_count = QSpinBox(page)
        self.backup_count.setRange(1, 90)
        self.backup_count.setValue(self.settings.backup_keep_count)
        form.addRow("Backups to keep", self.backup_count)

        layout.addLayout(form)
        layout.addWidget(SectionLabel("Current usage", page))

        usage = QLabel(page)
        usage.setWordWrap(True)
        usage.setProperty("dim", True)
        try:
            paths = self.config.paths
            usage.setText(
                f"Retained originals: {human_bytes(directory_size(paths.sources_dir))}\n"
                f"Working copies: {human_bytes(directory_size(paths.working_dir))}\n"
                f"Exports: {human_bytes(directory_size(paths.exports_dir))}\n"
                f"Backups: {human_bytes(directory_size(paths.backups_dir))}"
            )
        except OSError:
            usage.setText("Usage could not be measured.")
        layout.addWidget(usage)

        note = QLabel(
            "Moving the data folder does not move existing data. Copy the "
            "contents across yourself, then restart.",
            page,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        layout.addWidget(note)
        layout.addStretch(1)
        return page

    def _import_tab(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(9)

        self.max_file = QSpinBox(page)
        self.max_file.setRange(1, 8192)
        self.max_file.setValue(self.settings.max_file_mb)
        self.max_file.setSuffix(" MB")
        self.max_file.setToolTip(
            "A single image larger than this is refused. A panoramic study is "
            "normally well under a hundred megabytes."
        )
        form.addRow("Largest single file", self.max_file)

        self.max_pixels = QSpinBox(page)
        self.max_pixels.setRange(1, 4000)
        self.max_pixels.setValue(self.settings.max_pixels_millions)
        self.max_pixels.setSuffix(" megapixels")
        self.max_pixels.setToolTip(
            "Refuses an image whose declared size would exhaust memory. The "
            "header is read before any pixels are decoded."
        )
        form.addRow("Largest image", self.max_pixels)

        self.max_batch = QSpinBox(page)
        self.max_batch.setRange(1, 100000)
        self.max_batch.setValue(self.settings.max_batch_files)
        form.addRow("Largest import batch", self.max_batch)

        self.bundle_compression = QSpinBox(page)
        self.bundle_compression.setRange(0, 9)
        self.bundle_compression.setValue(self.settings.bundle_compression)
        self.bundle_compression.setToolTip(
            "Zero is fastest and largest, nine is slowest and smallest."
        )
        form.addRow("Bundle compression", self.bundle_compression)

        note = QLabel(
            "These limits protect the workstation. Raising them does not make an "
            "unsupported format readable.",
            page,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        form.addRow("", note)
        return page

    def _security_tab(self) -> QWidget:
        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(9)

        self.session_timeout = QSpinBox(page)
        self.session_timeout.setRange(0, 480)
        self.session_timeout.setValue(self.settings.session_timeout_minutes)
        self.session_timeout.setSuffix(" minutes")
        self.session_timeout.setSpecialValueText("No timeout")
        form.addRow("Lock after idle", self.session_timeout)

        self.lock_on_idle = QCheckBox("Enforce the idle timeout", page)
        self.lock_on_idle.setChecked(self.settings.lock_on_idle)
        form.addRow("", self.lock_on_idle)

        self.password_length = QSpinBox(page)
        self.password_length.setRange(6, 64)
        self.password_length.setValue(self.settings.password_min_length)
        form.addRow("Minimum password length", self.password_length)

        self.require_mixed = QCheckBox("Require upper and lower case", page)
        self.require_mixed.setChecked(self.settings.password_require_mixed_case)
        self.require_digit = QCheckBox("Require a digit", page)
        self.require_digit.setChecked(self.settings.password_require_digit)
        self.require_symbol = QCheckBox("Require a symbol", page)
        self.require_symbol.setChecked(self.settings.password_require_symbol)
        for box in (self.require_mixed, self.require_digit, self.require_symbol):
            form.addRow("", box)

        self.max_failed = QSpinBox(page)
        self.max_failed.setRange(1, 20)
        self.max_failed.setValue(self.settings.max_failed_logins)
        form.addRow("Failed sign ins before lockout", self.max_failed)

        self.lockout_minutes = QSpinBox(page)
        self.lockout_minutes.setRange(1, 1440)
        self.lockout_minutes.setValue(self.settings.lockout_minutes)
        self.lockout_minutes.setSuffix(" minutes")
        form.addRow("Lockout duration", self.lockout_minutes)

        self.encrypt = QCheckBox("Encrypt derived content at rest", page)
        self.encrypt.setChecked(self.settings.encrypt_at_rest)
        form.addRow("", self.encrypt)

        self.retention = QSpinBox(page)
        self.retention.setRange(0, 3650)
        self.retention.setValue(self.settings.retention_days)
        self.retention.setSuffix(" days")
        self.retention.setSpecialValueText("No automatic deletion")
        form.addRow("Retention", self.retention)

        third_party = QLabel(
            "ARIA has no network layer. It does not send images, metadata or "
            "annotations to any service, and there is no setting that enables it.",
            page,
        )
        third_party.setWordWrap(True)
        third_party.setStyleSheet(
            f"color: {PALETTE.text_dim}; border-left: 3px solid {PALETTE.success};"
            f" padding: 8px 10px;"
        )
        form.addRow("", third_party)
        return page

    # -- actions -------------------------------------------------------------

    def _browse_data_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Choose the data folder", self.data_dir.text()
        )
        if folder:
            self.data_dir.setText(folder)

    def _restore(self) -> None:
        answer = QMessageBox.question(
            self, "Restore defaults",
            "Reset every preference to its default?\n\n"
            "Projects, cases and annotations are not affected.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.config.reset_to_defaults()
            self.accept()

    def _apply(self) -> None:
        s = self.settings
        s.font_point_size = self.font_size.value()
        s.ui_scale = self.ui_scale.value()
        s.high_contrast_annotations = self.high_contrast.isChecked()
        s.show_annotation_labels = self.show_labels.isChecked()
        s.annotation_line_width = self.line_width.value()
        s.landmark_size = self.landmark_size.value()
        s.remember_window_geometry = self.remember_geometry.isChecked()

        s.zoom_step = self.zoom_step.value()
        s.crosshair = self.crosshair.isChecked()
        s.magnifier_enabled = self.magnifier.isChecked()
        s.magnifier_factor = self.magnifier_factor.value()
        s.show_data_probe = self.probe.isChecked()

        new_dir = self.data_dir.text().strip()
        if new_dir and new_dir != str(self.config.paths.data_dir):
            ok, message = __import__(
                "aria.io.fsutil", fromlist=["ensure_writable"]
            ).ensure_writable(Path(new_dir))
            if not ok:
                QMessageBox.warning(
                    self, "That folder cannot be used",
                    f"{message}\n\nChoose a folder this account can write to.",
                )
                return
            s.data_dir_override = new_dir
            QMessageBox.information(
                self, "Restart needed",
                "The data folder changes when ARIA next starts.\n\n"
                "Copy the contents of the old folder across before restarting, "
                "otherwise existing cases will not be found.",
            )
        s.keep_working_copies = self.keep_working.isChecked()
        s.backup_on_launch = self.backup_on_launch.isChecked()
        s.backup_keep_count = self.backup_count.value()

        s.max_file_mb = self.max_file.value()
        s.max_pixels_millions = self.max_pixels.value()
        s.max_batch_files = self.max_batch.value()
        s.bundle_compression = self.bundle_compression.value()

        s.session_timeout_minutes = self.session_timeout.value()
        s.lock_on_idle = self.lock_on_idle.isChecked()
        s.password_min_length = self.password_length.value()
        s.password_require_mixed_case = self.require_mixed.isChecked()
        s.password_require_digit = self.require_digit.isChecked()
        s.password_require_symbol = self.require_symbol.isChecked()
        s.max_failed_logins = self.max_failed.value()
        s.lockout_minutes = self.lockout_minutes.value()
        s.encrypt_at_rest = self.encrypt.isChecked()
        s.retention_days = self.retention.value()

        self.config.save()
        self.accept()
