"""The import dialog: inspect first, then import.

Files are inspected before anything is imported, so the person sees exactly
which files will be accepted, which will not and why, and can fix the selection
before committing. A rejected file always explains both the problem and the
next step.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...io.guards import human_bytes, inspect_file
from ..theme import PALETTE
from ..widgets.common import Banner, make_button


class InspectWorker(QThread):
    """Runs the guards off the interface thread."""

    progress = Signal(int, int, str)
    finished_inspection = Signal(list)

    def __init__(self, paths, limits, destination, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.limits = limits
        self.destination = destination
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        results = []
        for index, path in enumerate(self.paths):
            if self._cancelled:
                break
            self.progress.emit(index, len(self.paths), Path(path).name)
            results.append(inspect_file(path, self.limits, self.destination))
        self.finished_inspection.emit(results)


class ImportWorker(QThread):
    """Runs the import itself off the interface thread."""

    progress = Signal(int, int, str)
    finished_import = Signal(object)

    def __init__(self, importer, paths, project_id, profile, user_id, split, policy, parent=None):
        super().__init__(parent)
        self.importer = importer
        self.paths = paths
        self.project_id = project_id
        self.profile = profile
        self.user_id = user_id
        self.split = split
        self.policy = policy
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        summary = self.importer.import_files(
            self.paths, self.project_id, self.profile, self.user_id,
            self.split, self.policy,
            progress=lambda i, n, name: self.progress.emit(i, n, name),
            should_cancel=lambda: self._cancelled,
        )
        self.finished_import.emit(summary)


class ImportDialog(QDialog):
    """Shows what will be imported, then imports it."""

    def __init__(self, controller, paths, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.paths = list(paths)
        self.inspections: list = []
        self.worker = None
        self.import_worker = None

        self.setWindowTitle("Import images")
        self.setMinimumSize(880, 560)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel(f"Checking {len(self.paths)} files", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)
        self.heading = heading

        self.subheading = QLabel(
            "Each file is checked before anything is imported, so nothing "
            "unsuitable reaches the decoder.",
            self,
        )
        self.subheading.setProperty("dim", True)
        self.subheading.setWordWrap(True)
        layout.addWidget(self.subheading)

        options = QHBoxLayout()
        options.setSpacing(8)
        options.addWidget(QLabel("Dataset split", self))
        self.split_combo = QComboBox(self)
        self.split_combo.addItem("Not assigned", "")
        for split in ("train", "validation", "test", "calibration"):
            self.split_combo.addItem(split, split)
        options.addWidget(self.split_combo)

        options.addWidget(QLabel("   Privacy profile", self))
        self.profile_combo = QComboBox(self)
        from ...io.deident import BUILTIN_PROFILES

        for key, profile in BUILTIN_PROFILES.items():
            self.profile_combo.addItem(profile.display_name, key)
            self.profile_combo.setItemData(
                self.profile_combo.count() - 1, profile.description, Qt.ToolTipRole
            )
        project = controller.project
        if project is not None:
            index = self.profile_combo.findData(project.deid_profile)
            if index >= 0:
                self.profile_combo.setCurrentIndex(index)
        options.addWidget(self.profile_combo)
        options.addStretch(1)
        layout.addLayout(options)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["File", "Format", "Size", "Dimensions", "Result"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(280)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self.progress = QProgressBar(self)
        layout.addWidget(self.progress)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        self.import_button = make_button("Import accepted files", "import", accent=True, parent=self)
        self.cancel_button = make_button("Cancel", parent=self)
        buttons.addButton(self.import_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self._start_import)
        self.cancel_button.clicked.connect(self._cancel)

        self._start_inspection()

    # -- inspection ----------------------------------------------------------

    def _start_inspection(self) -> None:
        self.progress.setRange(0, len(self.paths))
        self.worker = InspectWorker(
            self.paths, self.controller.settings.import_limits(),
            self.controller.paths.data_dir, self,
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_inspection.connect(self._on_inspected)
        self.worker.start()

    def _on_progress(self, index: int, total: int, name: str) -> None:
        self.progress.setValue(index)
        self.progress.setFormat(f"{name}   %v of %m")

    def _on_inspected(self, results) -> None:
        self.inspections = results
        self.progress.setValue(len(results))
        self.tree.clear()

        accepted = 0
        total_bytes = 0
        for result in results:
            item = QTreeWidgetItem(self.tree)
            item.setText(0, Path(result.path).name)
            item.setText(1, result.detected_format.upper() or "unknown")
            item.setText(2, human_bytes(result.byte_size))
            item.setText(
                3,
                f"{result.columns} by {result.rows}" if result.rows else "",
            )

            if result.accepted:
                accepted += 1
                total_bytes += result.byte_size
                if result.warnings:
                    item.setText(4, f"Accepted, {len(result.warnings)} notes")
                    item.setForeground(4, QBrush(QColor(PALETTE.warning)))
                    item.setToolTip(4, "\n\n".join(result.warnings))
                else:
                    item.setText(4, "Accepted")
                    item.setForeground(4, QBrush(QColor(PALETTE.success)))
            else:
                problem = result.first_problem()
                item.setText(4, problem.message if problem else "Rejected")
                item.setForeground(4, QBrush(QColor(PALETTE.danger)))
                if problem:
                    item.setToolTip(
                        4, f"{problem.message}\n\nWhat to do: {problem.remedy}"
                    )
                font = QFont()
                font.setStrikeOut(True)
                item.setFont(0, font)

            item.setToolTip(0, result.path)

        self.tree.resizeColumnToContents(1)
        self.tree.resizeColumnToContents(2)
        self.tree.resizeColumnToContents(3)

        rejected = len(results) - accepted
        self.heading.setText(
            f"{accepted} of {len(results)} files can be imported"
        )
        self.subheading.setText(
            f"{human_bytes(total_bytes)} will be copied and retained unchanged."
            + (f"   {rejected} files were rejected." if rejected else "")
        )

        if accepted == 0:
            self.banner.show_message(
                "No file in this selection can be imported. Hover the Result "
                "column for what to do about each one.",
                "danger",
            )
        elif rejected:
            self.banner.show_message(
                f"{rejected} files will be skipped. The rest will be imported.",
                "warn",
            )
        else:
            self.banner.show_message("Every file passed the checks.", "ok")

        self.import_button.setEnabled(accepted > 0)
        self.progress.setFormat("%v of %m checked")

    # -- import --------------------------------------------------------------

    def _start_import(self) -> None:
        accepted = [r.path for r in self.inspections if r.accepted]
        if not accepted:
            return
        from ...io.deident import get_profile

        self.import_button.setEnabled(False)
        self.split_combo.setEnabled(False)
        self.profile_combo.setEnabled(False)
        self.progress.setRange(0, len(accepted))
        self.progress.setValue(0)
        self.heading.setText(f"Importing {len(accepted)} files")

        self.import_worker = ImportWorker(
            self.controller.importer(), accepted,
            self.controller.project.id if self.controller.project else "",
            get_profile(self.profile_combo.currentData()),
            self.controller.user.id if self.controller.user else "",
            self.split_combo.currentData() or "",
            self.controller.schema.device_calibration_policy,
            self,
        )
        self.import_worker.progress.connect(self._on_progress)
        self.import_worker.finished_import.connect(self._on_imported)
        self.import_worker.start()

    def _on_imported(self, summary) -> None:
        self.progress.setFormat(summary.summary_line())
        self.heading.setText(summary.summary_line())

        by_path = {o.path: o for o in summary.outcomes}
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            path = item.toolTip(0)
            outcome = by_path.get(path)
            if outcome is None:
                continue
            if outcome.succeeded:
                item.setText(4, f"Imported as {outcome.case.pseudonym}")
                item.setForeground(4, QBrush(QColor(PALETTE.success)))
                if outcome.warnings:
                    item.setToolTip(4, "\n\n".join(outcome.warnings))
            elif outcome.duplicate_of:
                item.setText(4, f"Already present as {outcome.duplicate_of}")
                item.setForeground(4, QBrush(QColor(PALETTE.warning)))
            else:
                item.setText(4, outcome.error_message)
                item.setForeground(4, QBrush(QColor(PALETTE.danger)))
                item.setToolTip(4, f"{outcome.error_message}\n\n{outcome.remedy}")

        notes = [w for o in summary.imported for w in o.warnings]
        unconfirmed = sum(
            1 for o in summary.imported
            if any("laterality" in w.lower() for w in o.warnings)
        )
        message = summary.summary_line()
        if unconfirmed:
            message += (
                f"   {unconfirmed} images do not carry laterality, so anatomical "
                f"orientation must be confirmed on each before submission."
            )
        self.banner.show_message(
            message, "ok" if summary.imported and not summary.failed else "warn"
        )

        self.cancel_button.setText("Close")
        self.import_button.setVisible(False)

    def _cancel(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        if self.import_worker is not None and self.import_worker.isRunning():
            self.import_worker.cancel()
            self.import_worker.wait(4000)
        self.reject()

    def closeEvent(self, event) -> None:
        self._cancel()
        super().closeEvent(event)
