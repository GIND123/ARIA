"""The diagnostics dialog: run the self tests and report them."""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...platform.selftest import run_self_tests
from ..theme import PALETTE
from ..widgets.common import Banner, make_button


class SelfTestThread(QThread):
    progress = Signal(int, int, str)
    done = Signal(object)

    def __init__(self, paths, parent=None):
        super().__init__(parent)
        self.paths = paths

    def run(self) -> None:
        report = run_self_tests(
            self.paths,
            progress=lambda i, n, name: self.progress.emit(i, n, name),
        )
        self.done.emit(report)


class DiagnosticsDialog(QDialog):
    """Runs the built in self tests and shows what each one checked."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.report = None

        self.setWindowTitle("Diagnostics")
        self.setMinimumSize(860, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel("Self tests", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        explanation = QLabel(
            "These run against the installed code, not a copy of it. They check "
            "the measurement arithmetic, the display independence rule, the "
            "import guardrails, the identifier scan, storage durability and the "
            "export layout.",
            self,
        )
        explanation.setWordWrap(True)
        explanation.setProperty("dim", True)
        layout.addWidget(explanation)

        self.summary = QLabel("Running...", self)
        self.summary.setWordWrap(True)
        font = QFont()
        font.setBold(True)
        self.summary.setFont(font)
        layout.addWidget(self.summary)

        self.progress = QProgressBar(self)
        layout.addWidget(self.progress)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Test", "Result", "Time"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setMinimumHeight(380)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self.banner = Banner(self)
        layout.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        self.rerun_button = make_button("Run again", "refresh", parent=self)
        self.save_button = make_button("Save report", "save", parent=self)
        self.copy_button = make_button("Copy report", "copy", parent=self)
        close = make_button("Close", parent=self)
        for button, role in (
            (self.rerun_button, QDialogButtonBox.ActionRole),
            (self.save_button, QDialogButtonBox.ActionRole),
            (self.copy_button, QDialogButtonBox.ActionRole),
            (close, QDialogButtonBox.RejectRole),
        ):
            buttons.addButton(button, role)
        layout.addWidget(buttons)

        self.rerun_button.clicked.connect(self._run)
        self.save_button.clicked.connect(self._save)
        self.copy_button.clicked.connect(self._copy)
        close.clicked.connect(self.reject)

        self._run()

    def _run(self) -> None:
        self.rerun_button.setEnabled(False)
        self.progress.setValue(0)
        self.tree.clear()
        self.summary.setText("Running self tests...")
        self.thread = SelfTestThread(self.controller.paths, self)
        self.thread.progress.connect(self._on_progress)
        self.thread.done.connect(self._on_done)
        self.thread.start()

    def _on_progress(self, index: int, total: int, name: str) -> None:
        self.progress.setRange(0, total)
        self.progress.setValue(index)
        self.progress.setFormat(f"{name}   %v of %m")

    def _on_done(self, report) -> None:
        self.report = report
        self.rerun_button.setEnabled(True)
        self.progress.setValue(self.progress.maximum())
        self.progress.setFormat("%v of %m complete")

        self.tree.clear()
        for group, results in report.by_group().items():
            parent = QTreeWidgetItem(self.tree, [group, "", ""])
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            parent.setExpanded(True)
            for result in results:
                glyph = "✓" if result.passed else "✗"
                item = QTreeWidgetItem(
                    parent,
                    [
                        f"{glyph}  {result.name}",
                        result.detail,
                        f"{result.duration_ms:.0f} ms",
                    ],
                )
                colour = PALETTE.success if result.passed else PALETTE.danger
                item.setForeground(0, QBrush(QColor(colour)))
                tooltip = f"{result.name}\n\n{result.detail}"
                if not result.passed:
                    tooltip += f"\n\nWhy it matters: {result.matters}"
                    if result.error:
                        tooltip += f"\n\n{result.error}"
                for column in range(3):
                    item.setToolTip(column, tooltip)

        self.summary.setText(report.summary())
        if report.passed:
            self.banner.show_message(
                "Everything checked out. The installation is behaving as expected.",
                "ok",
            )
        else:
            first = report.failures[0]
            self.banner.show_message(
                f"{first.name}: {first.detail}", "danger", tooltip=first.matters
            )

        try:
            self.controller.repo.log(
                "diagnostics_run", "application", "selftest",
                after={
                    "passed": report.passed, "n_tests": len(report.results),
                    "n_failures": len(report.failures),
                },
                detail=report.summary(),
            )
        except Exception:
            pass

    def _save(self) -> None:
        if self.report is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "Save diagnostics report",
            str(self.controller.paths.data_dir / "aria_diagnostics.txt"),
            "Text (*.txt);;JSON (*.json)",
        )
        if not path:
            return
        import json

        from ...io.fsutil import atomic_write_text

        if path.lower().endswith(".json") or "JSON" in selected:
            atomic_write_text(path, json.dumps(self.report.to_dict(), indent=2, default=str))
        else:
            atomic_write_text(path, self.report.to_text())
        QMessageBox.information(
            self, "Report saved",
            f"The diagnostics report was written to:\n\n{path}\n\n"
            f"Include it when reporting a problem.",
        )

    def _copy(self) -> None:
        if self.report is None:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.report.to_text())
        self.banner.show_message("The report was copied to the clipboard.", "info")
