"""The compatibility report, re-runnable from the Help menu."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...platform.system_check import CheckStatus, run_system_check
from ...version import APP_NAME
from ..theme import PALETTE
from ..widgets.common import Banner, make_button

STATUS_COLOURS = {
    CheckStatus.PASS: PALETTE.success,
    CheckStatus.WARN: PALETTE.warning,
    CheckStatus.FAIL: PALETTE.danger,
    CheckStatus.UNKNOWN: PALETTE.text_dim,
}


class CheckThread(QThread):
    done = Signal(object)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self) -> None:
        self.done.emit(run_system_check(self.paths, include_display=False))


class SystemCheckDialog(QDialog):
    """Shows the compatibility report and can save it."""

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.report = None

        self.setWindowTitle(f"{APP_NAME} compatibility")
        self.setMinimumSize(820, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.verdict = QLabel("Running checks...", self)
        self.verdict.setProperty("subheading", True)
        self.verdict.setWordWrap(True)
        layout.addWidget(self.verdict)

        self.machine = QLabel("", self)
        self.machine.setProperty("dim", True)
        self.machine.setWordWrap(True)
        layout.addWidget(self.machine)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Check", "Measured", "Requirement"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(360)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        self.rerun_button = make_button("Run again", "refresh", parent=self)
        self.save_button = make_button("Save report", "save", parent=self)
        close = make_button("Close", parent=self)
        buttons.addButton(self.rerun_button, QDialogButtonBox.ActionRole)
        buttons.addButton(self.save_button, QDialogButtonBox.ActionRole)
        buttons.addButton(close, QDialogButtonBox.RejectRole)
        layout.addWidget(buttons)

        self.rerun_button.clicked.connect(self._run)
        self.save_button.clicked.connect(self._save)
        close.clicked.connect(self.reject)

        self._run()

    def _run(self) -> None:
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.rerun_button.setEnabled(False)
        self.thread = CheckThread(self.paths, self)
        self.thread.done.connect(self._on_report)
        self.thread.start()

    def _on_report(self, report) -> None:
        self.report = report
        self.progress.setVisible(False)
        self.rerun_button.setEnabled(True)

        machine = report.machine
        self.machine.setText(
            f"{machine.get('system')} {machine.get('release')} on "
            f"{machine.get('machine')}, Python {machine.get('python')}"
            + (", packaged build" if machine.get("frozen") else "")
        )

        self.tree.clear()
        for category, results in report.by_category().items():
            parent = QTreeWidgetItem(self.tree, [category, "", ""])
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            parent.setExpanded(True)
            for result in results:
                status = result.status_enum
                item = QTreeWidgetItem(
                    parent,
                    [f"{status.glyph}  {result.name}", result.measured, result.requirement],
                )
                item.setForeground(0, QBrush(QColor(STATUS_COLOURS[status])))
                tooltip = f"{result.name}\n\nMeasured: {result.measured}"
                if result.requirement:
                    tooltip += f"\nRequirement: {result.requirement}"
                if result.remedy:
                    tooltip += f"\n\nWhat to do: {result.remedy}"
                if result.detail:
                    tooltip += f"\n\n{result.detail}"
                for column in range(3):
                    item.setToolTip(column, tooltip)

        self.verdict.setText(report.verdict())
        if report.failures:
            self.banner.show_message(
                f"{report.failures[0].name}: {report.failures[0].remedy}", "danger"
            )
        elif report.warnings:
            self.banner.show_message(
                f"{len(report.warnings)} points worth noting. Hover a row for detail.",
                "warn",
            )
        else:
            self.banner.show_message("Every requirement is met.", "ok")

    def _save(self) -> None:
        if self.report is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "Save compatibility report",
            str(self.paths.data_dir / "aria_compatibility_report.txt"),
            "Text (*.txt);;JSON (*.json)",
        )
        if not path:
            return
        import json

        from ...io.fsutil import atomic_write_text

        if path.lower().endswith(".json") or "JSON" in selected:
            atomic_write_text(
                path, json.dumps(self.report.to_dict(), indent=2, default=str)
            )
        else:
            atomic_write_text(path, self.report.to_text())
        QMessageBox.information(
            self, "Report saved", f"The compatibility report was written to:\n\n{path}"
        )
