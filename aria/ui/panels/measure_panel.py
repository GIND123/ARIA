"""Measurements, calibration and texture features.

Every quantitative result on this panel is shown with its calibration source,
value, unit, validation status and correction factor (FR 009). That information
sits in one place at the top of the panel and is repeated per row as a tooltip,
so a number can never be read without knowing what produced it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.measurements import MeasurementKind
from ...core.schema import Side
from ...core.units import Calibration, CalibrationSource, ValidationStatus
from ..theme import PALETTE
from ..widgets.common import (
    Banner,
    CollapsibleSection,
    KeyValueGrid,
    ScrollPanel,
    SectionLabel,
    StatusChip,
    make_button,
)

#: Order measurements appear in. Side specific rows first, then the aggregate.
DISPLAY_ORDER = [
    MeasurementKind.MCW,
    MeasurementKind.PMI_SUPERIOR,
    MeasurementKind.PMI_INFERIOR,
    MeasurementKind.ANTEGONIAL_INDEX,
    MeasurementKind.GONIAL_INDEX,
    MeasurementKind.PMI_SUPERIOR_HEIGHT,
    MeasurementKind.PMI_INFERIOR_HEIGHT,
]


class MeasurePanel(QWidget):
    """Live measurements, calibration control and texture analysis."""

    calibrate_requested = Signal()
    texture_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_calibration_section()
        self._build_measurements_section()
        self._build_texture_section()

        controller.measurements_changed.connect(self.refresh_measurements)
        controller.calibration_changed.connect(self.refresh_calibration)
        controller.case_opened.connect(lambda _d: self.refresh_calibration(None))
        controller.case_closed.connect(self._on_closed)

    # -- calibration ---------------------------------------------------------

    def _build_calibration_section(self) -> None:
        self.calibration_section = CollapsibleSection("Calibration", self, True, "calibrate")
        body = self.calibration_section.body_layout()

        self.calibration_chip = StatusChip("No case open", "neutral", self)
        body.addWidget(self.calibration_chip)

        self.calibration_grid = KeyValueGrid(self)
        body.addWidget(self.calibration_grid)

        self.calibration_banner = Banner(self)
        body.addWidget(self.calibration_banner)

        button_row = QHBoxLayout()
        self.validate_button = make_button(
            "Validate", "check",
            tooltip=(
                "Accept the spatial scale for this case. Millimetre values stay "
                "unavailable until a calibration is validated."
            ),
            parent=self,
        )
        self.reject_button = make_button(
            "Reject", danger=True,
            tooltip="Record that this scale must not be used.", parent=self,
        )
        self.manual_button = make_button(
            "Manual calibration", "calibrate",
            tooltip="Measure a known length on the image to set the scale.",
            parent=self,
        )
        button_row.addWidget(self.validate_button)
        button_row.addWidget(self.reject_button)
        body.addLayout(button_row)
        body.addWidget(self.manual_button)

        body.addWidget(SectionLabel("Magnification correction", self))
        correction_note = QLabel(
            "A panoramic unit magnifies differently in the vertical and the "
            "horizontal direction. Correction is applied only when the project "
            "allows it and a device policy is recorded.",
            self,
        )
        correction_note.setWordWrap(True)
        correction_note.setProperty("dim", True)
        body.addWidget(correction_note)

        correction_row = QHBoxLayout()
        correction_row.addWidget(QLabel("Vertical", self))
        self.magnification_v = QDoubleSpinBox(self)
        self.magnification_v.setRange(1.0, 1.6)
        self.magnification_v.setDecimals(4)
        self.magnification_v.setSingleStep(0.01)
        self.magnification_v.setValue(1.0)
        correction_row.addWidget(self.magnification_v)
        correction_row.addWidget(QLabel("Horizontal", self))
        self.magnification_h = QDoubleSpinBox(self)
        self.magnification_h.setRange(1.0, 1.6)
        self.magnification_h.setDecimals(4)
        self.magnification_h.setSingleStep(0.01)
        self.magnification_h.setValue(1.0)
        correction_row.addWidget(self.magnification_h)
        body.addLayout(correction_row)

        self.apply_correction = make_button("Apply correction", parent=self)
        body.addWidget(self.apply_correction)

        self.validate_button.clicked.connect(self._validate)
        self.reject_button.clicked.connect(self._reject)
        self.manual_button.clicked.connect(self.calibrate_requested)
        self.apply_correction.clicked.connect(self._apply_correction)

        self.scroll.add_section(self.calibration_section)

    def refresh_calibration(self, _calibration=None) -> None:
        data = self.controller.case_data
        if data is None:
            self.calibration_chip.set_state("No case open", "neutral")
            self.calibration_grid.clear_values()
            self.calibration_banner.clear()
            return

        cal = data.case.calibration
        level = (
            "ok" if cal.millimetres_available
            else "warn" if cal.has_spacing else "danger"
        )
        self.calibration_chip.set_state(
            cal.status.display, level, cal.summary_line()
        )

        self.calibration_grid.set("source", "Source", cal.source.display)
        if cal.has_spacing:
            self.calibration_grid.set(
                "scale", "Value",
                f"{cal.row_spacing_mm:.6g} mm/px vertical\n"
                f"{cal.col_spacing_mm:.6g} mm/px horizontal",
                mono=True,
            )
            self.calibration_grid.set(
                "effective", "After correction",
                f"{cal.effective_row_mm:.6g} mm/px vertical\n"
                f"{cal.effective_col_mm:.6g} mm/px horizontal",
                mono=True,
            )
        else:
            self.calibration_grid.set("scale", "Value", "Not available")
            self.calibration_grid.set("effective", "After correction", "Not available")
        self.calibration_grid.set(
            "status", "Status", cal.status.display,
            "ok" if cal.is_validated else "warn",
        )
        self.calibration_grid.set("unit", "Units", cal.unit_note())
        self.calibration_grid.set("correction", "Correction factor", cal.correction_factor_text)
        if cal.validated_by:
            self.calibration_grid.set(
                "validated", "Validated by",
                f"{cal.validated_by} at {(cal.validated_at or '')[:19].replace('T', ' ')}",
            )
        if cal.device_model:
            self.calibration_grid.set("device", "Device", cal.device_model)

        self.magnification_v.setValue(max(1.0, cal.magnification_vertical))
        self.magnification_h.setValue(max(1.0, cal.magnification_horizontal))

        allow = self.controller.schema.allow_magnification_correction
        self.apply_correction.setEnabled(allow and not self.controller.read_only)
        self.magnification_v.setEnabled(allow)
        self.magnification_h.setEnabled(allow)

        messages = list(cal.warnings)
        if not allow:
            messages.append(
                "Magnification correction is switched off for this project. An "
                "administrator enables it after approving a device policy."
            )
        if messages:
            self.calibration_banner.show_message(messages[0], "warn")
            self.calibration_banner.setToolTip("\n\n".join(messages))
        else:
            self.calibration_banner.clear()

        editable = not self.controller.read_only
        self.validate_button.setEnabled(editable and cal.has_spacing and not cal.is_validated)
        self.reject_button.setEnabled(editable and cal.has_spacing)
        self.manual_button.setEnabled(editable)

    def _validate(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        data = self.controller.case_data
        if data is None:
            return
        cal = data.case.calibration
        ok, reasons = cal.can_validate()
        if not ok:
            QMessageBox.warning(
                self, "Calibration cannot be validated",
                "\n".join(reasons)
                + "\n\nCorrect the scale, or complete a manual calibration instead.",
            )
            return

        warnings = cal.plausibility_warnings()
        detail = (
            "\n\nPoints to confirm:\n" + "\n".join(f"  {w}" for w in warnings)
            if warnings else ""
        )
        answer = QMessageBox.question(
            self, "Validate calibration",
            f"Accept this scale for {data.case.pseudonym}?\n\n{cal.summary_line()}"
            f"{detail}\n\nMillimetre values will be produced from it.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        from ...core.models import utc_now

        user = self.controller.user
        cal.validate(user.pseudonym if user else "unknown", utc_now())
        self.controller.set_calibration(cal, "Calibration validated by a reviewer.")

    def _reject(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        data = self.controller.case_data
        if data is None:
            return
        reason, ok = QInputDialog.getText(
            self, "Reject calibration", "Why must this scale not be used?"
        )
        if not ok or not reason.strip():
            return
        from ...core.models import utc_now

        user = self.controller.user
        cal = data.case.calibration
        cal.reject(user.pseudonym if user else "unknown", utc_now(), reason.strip())
        self.controller.set_calibration(cal, f"Calibration rejected: {reason.strip()}")

    def _apply_correction(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        cal = data.case.calibration
        cal.magnification_vertical = self.magnification_v.value()
        cal.magnification_horizontal = self.magnification_h.value()
        cal.magnification_applied = True
        ok, reasons = cal.can_validate()
        if not ok:
            from PySide6.QtWidgets import QMessageBox

            cal.magnification_applied = False
            QMessageBox.warning(
                self, "Correction not applied", "\n".join(reasons)
            )
            return
        self.controller.set_calibration(
            cal,
            f"Magnification correction applied: {cal.correction_factor_text}.",
        )

    # -- measurements --------------------------------------------------------

    def _build_measurements_section(self) -> None:
        self.measurements_section = CollapsibleSection("Measurements", self, True, "measure")
        body = self.measurements_section.body_layout()

        self.unit_note = QLabel("", self)
        self.unit_note.setWordWrap(True)
        self.unit_note.setProperty("dim", True)
        body.addWidget(self.unit_note)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["Measure", "Right", "Left", "Mean"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(210)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setMinimumWidth(0)
        self.table.setSizeAdjustPolicy(QAbstractItemView.AdjustIgnored)
        body.addWidget(self.table)

        self.grade_row = QLabel("", self)
        self.grade_row.setWordWrap(True)
        body.addWidget(self.grade_row)

        self.screening_banner = Banner(self)
        body.addWidget(self.screening_banner)

        self.copy_button = make_button(
            "Copy measurements", "copy",
            tooltip="Copy the table to the clipboard as tab separated text.",
            parent=self,
        )
        self.copy_button.clicked.connect(self._copy_measurements)
        body.addWidget(self.copy_button)

        self.scroll.add_section(self.measurements_section)

    def refresh_measurements(self, measurements) -> None:
        data = self.controller.case_data
        self.table.setRowCount(0)
        if data is None:
            self.unit_note.setText("")
            self.grade_row.setText("")
            self.screening_banner.clear()
            return

        cal = data.case.calibration
        self.unit_note.setText(cal.summary_line())

        indexed: dict = {}
        for m in measurements:
            indexed[(m.kind, m.side)] = m

        self.table.setRowCount(len(DISPLAY_ORDER))
        for row, kind in enumerate(DISPLAY_ORDER):
            short = {
                MeasurementKind.MCW: "MCW",
                MeasurementKind.PMI_SUPERIOR: "PMI sup",
                MeasurementKind.PMI_INFERIOR: "PMI inf",
                MeasurementKind.ANTEGONIAL_INDEX: "AI",
                MeasurementKind.GONIAL_INDEX: "GI",
                MeasurementKind.PMI_SUPERIOR_HEIGHT: "Height sup",
                MeasurementKind.PMI_INFERIOR_HEIGHT: "Height inf",
            }.get(kind, kind.display)
            name_item = QTableWidgetItem(short)
            aliases = kind.aliases
            tooltip = kind.display
            if aliases:
                tooltip += f"\nAlso known as: {', '.join(aliases)}"
            name_item.setToolTip(tooltip)
            font = QFont()
            font.setBold(kind in (MeasurementKind.MCW, MeasurementKind.PMI_SUPERIOR))
            name_item.setFont(font)
            self.table.setItem(row, 0, name_item)

            for column, side in ((1, Side.RIGHT), (2, Side.LEFT), (3, Side.NONE)):
                m = indexed.get((kind.value, side.value))
                if m is None:
                    self.table.setItem(row, column, QTableWidgetItem("–"))
                    continue
                item = QTableWidgetItem(m.compact())
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                detail = [m.formatted()]
                if m.source_annotation_ids:
                    detail.append(f"From: {', '.join(m.source_annotation_ids)}")
                detail.append(f"Calculation version: {m.calculation_version}")
                detail.append(f"Calibration: {m.calibration_source}, {m.calibration_status}")
                if m.aggregation != "none":
                    detail.append(m.aggregation_detail)
                for warning in m.warnings:
                    detail.append(f"Note: {warning}")
                item.setToolTip("\n".join(detail))
                if not m.assessable:
                    item.setForeground(QBrush(QColor(PALETTE.text_disabled)))
                elif m.warnings:
                    item.setForeground(QBrush(QColor(PALETTE.warning)))
                self.table.setItem(row, column, item)

        grades = []
        for side in (Side.RIGHT, Side.LEFT):
            label = data.grade("mci_grade", side)
            grades.append(f"{side.display}: {label.grade.display if label else 'not assigned'}")
        self.grade_row.setText("Cortical index   " + "    ".join(grades))

        screening = [s for m in measurements for s in m.screening]
        if screening:
            self.screening_banner.show_message(
                f"{len(screening)} screening rule "
                f"{'outcome' if len(screening) == 1 else 'outcomes'} recorded. "
                f"These are project configured rules, not a diagnosis.",
                "info",
            )
            self.screening_banner.setToolTip(
                "\n".join(s["statement"] for s in screening)
            )
        else:
            self.screening_banner.clear()

    def _copy_measurements(self) -> None:
        from PySide6.QtWidgets import QApplication

        lines = ["Measure\tRight\tLeft\tMean"]
        for row in range(self.table.rowCount()):
            cells = [
                self.table.item(row, column).text() if self.table.item(row, column) else ""
                for column in range(4)
            ]
            lines.append("\t".join(cells))
        data = self.controller.case_data
        if data is not None:
            lines.append("")
            lines.append(data.case.calibration.summary_line())
        QApplication.clipboard().setText("\n".join(lines))
        self.controller.status_message.emit("Measurements copied to the clipboard.", 3000)

    # -- texture -------------------------------------------------------------

    def _build_texture_section(self) -> None:
        self.texture_section = CollapsibleSection("Texture features", self, False, "texture")
        body = self.texture_section.body_layout()

        note = QLabel(
            "Texture features are computed from the regions you drew, after "
            "annotation. Region size changes the values, so the size used is "
            "recorded with every result.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.texture_progress = QProgressBar(self)
        self.texture_progress.setVisible(False)
        body.addWidget(self.texture_progress)

        self.texture_button = make_button("Compute for this case", "texture", parent=self)
        self.texture_button.clicked.connect(self.texture_requested)
        body.addWidget(self.texture_button)

        self.texture_table = QTableWidget(0, 2, self)
        self.texture_table.setHorizontalHeaderLabels(["Feature", "Value"])
        self.texture_table.verticalHeader().setVisible(False)
        self.texture_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.texture_table.setAlternatingRowColors(True)
        self.texture_table.setMinimumHeight(180)
        self.texture_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.texture_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.texture_table.setMinimumWidth(0)
        self.texture_table.setSizeAdjustPolicy(QAbstractItemView.AdjustIgnored)
        body.addWidget(self.texture_table)

        self.scroll.add_section(self.texture_section)

    def show_texture(self, results) -> None:
        """Display computed texture results, one block per region."""
        rows = []
        for result in results:
            features = result.get("features", {}) if isinstance(result, dict) else result.features
            region = result.get("region_class", "") if isinstance(result, dict) else result.region_class
            side = result.get("side", "") if isinstance(result, dict) else result.side
            width = result.get("width", 0) if isinstance(result, dict) else result.width
            height = result.get("height", 0) if isinstance(result, dict) else result.height
            rows.append((f"{region} {side}   {width} by {height} px", "", True))
            for key in (
                "fractal_dimension", "fd_r_squared", "dbc_dimension",
                "glcm_contrast_mean", "glcm_homogeneity_mean", "glcm_entropy_mean",
                "glcm_correlation_mean", "lbp_entropy",
                "rl_short_run_emphasis_mean", "rl_long_run_emphasis_mean",
                "mean", "std", "histogram_entropy",
            ):
                if key in features:
                    value = features[key]
                    text = f"{value:.5g}" if isinstance(value, (int, float)) else str(value)
                    rows.append((key.replace("_", " "), text, False))

        self.texture_table.setRowCount(len(rows))
        for index, (name, value, is_header) in enumerate(rows):
            name_item = QTableWidgetItem(name)
            value_item = QTableWidgetItem(value)
            value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if is_header:
                font = QFont()
                font.setBold(True)
                name_item.setFont(font)
                name_item.setForeground(QBrush(QColor(PALETTE.accent)))
            self.texture_table.setItem(index, 0, name_item)
            self.texture_table.setItem(index, 1, value_item)
        self.texture_section.set_expanded(True)

    def _on_closed(self) -> None:
        self.table.setRowCount(0)
        self.texture_table.setRowCount(0)
        self.calibration_grid.clear_values()
        self.calibration_chip.set_state("No case open", "neutral")
        self.unit_note.setText("")
        self.grade_row.setText("")
