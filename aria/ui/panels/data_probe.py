"""The data probe strip along the bottom of the workspace.

It reports what is under the cursor: the pixel position, the stored value, the
value after any rescale, the position in millimetres when a validated
calibration exists, and the current display settings. When there is no validated
calibration the millimetre field says so rather than showing a number.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ...core.schema import Side
from ..theme import PALETTE


class ProbeField(QWidget):
    """One labelled readout in the probe strip."""

    def __init__(self, caption: str, width: int = 96, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self.caption = QLabel(caption, self)
        self.caption.setProperty("dim", True)
        font = QFont()
        font.setPointSizeF(7.5)
        self.caption.setFont(font)

        self.value = QLabel("–", self)
        self.value.setProperty("mono", True)
        self.value.setMinimumWidth(width)
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(self.caption)
        layout.addWidget(self.value)

    def set_value(self, text: str, tooltip: str = "", muted: bool = False) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(
            f"color: {PALETTE.text_disabled};" if muted else f"color: {PALETTE.text};"
        )
        if tooltip:
            self.setToolTip(tooltip)


class DataProbe(QFrame):
    """The bottom strip showing cursor and display state."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setFixedHeight(26)
        self.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE.header};"
            f" border-top: 1px solid {PALETTE.border}; }}"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(9, 2, 9, 2)
        layout.setSpacing(14)

        self.position = ProbeField("Position", 118, self)
        self.stored = ProbeField("Stored", 74, self)
        self.modality = ProbeField("Value", 84, self)
        self.millimetres = ProbeField("Millimetres", 130, self)
        self.zoom = ProbeField("Zoom", 56, self)
        self.window = ProbeField("Window", 130, self)
        self.side_hint = ProbeField("Region", 70, self)

        for field in (
            self.position, self.stored, self.modality, self.millimetres,
            self.zoom, self.window, self.side_hint,
        ):
            layout.addWidget(field)
        layout.addStretch(1)

        self.message = QLabel("", self)
        self.message.setProperty("dim", True)
        layout.addWidget(self.message)

        self.clear()

    # -- updates -------------------------------------------------------------

    def update_cursor(self, x: float, y: float) -> None:
        image = self.controller.image
        if image is None:
            self.clear()
            return

        self.position.set_value(f"{x:8.2f}, {y:8.2f}")

        probe = image.value_at(x, y)
        if probe is None:
            self.stored.set_value("outside", muted=True)
            self.modality.set_value("–", muted=True)
            self.millimetres.set_value("–", muted=True)
            self.side_hint.set_value("–", muted=True)
            return

        stored = probe["stored"]
        modality = probe["modality"]
        self.stored.set_value(
            str(stored) if not isinstance(stored, tuple) else ", ".join(str(v) for v in stored),
            "Pixel value exactly as stored in the source file.",
        )
        self.modality.set_value(
            f"{modality:.1f}" if isinstance(modality, float) else str(modality),
            "Stored value after the rescale slope and intercept.",
        )

        case = self.controller.case_data.case if self.controller.case_data else None
        if case is not None and case.calibration.millimetres_available:
            cal = case.calibration
            mm_x = x * cal.effective_col_mm
            mm_y = y * cal.effective_row_mm
            self.millimetres.set_value(
                f"{mm_x:7.2f}, {mm_y:7.2f}", cal.summary_line()
            )
        else:
            reason = (
                "No validated calibration, so millimetre positions are not shown."
                if case is None or not case.calibration.has_spacing
                else "The calibration is present but not validated."
            )
            self.millimetres.set_value("unavailable", reason, muted=True)

        if image.columns:
            self.side_hint.set_value(
                Side.RIGHT.display if x < image.columns / 2 else Side.LEFT.display,
                "Anatomical side of the image at the cursor. Patient right is on "
                "the left of the image.",
            )

    def update_zoom(self, factor: float) -> None:
        self.zoom.set_value(f"{factor * 100:6.1f}%")

    def update_display(self, settings) -> None:
        text = f"C {settings.window_centre:.0f}  W {settings.window_width:.0f}"
        extras = []
        if settings.invert:
            extras.append("inverted")
        if settings.filter_name and settings.filter_name != "none":
            extras.append(settings.filter_name.replace("_", " "))
        if settings.show_original_pixels:
            extras.append("original pixels")
        if extras:
            text += "  " + ", ".join(extras)
        self.window.set_value(
            text,
            "Display settings only. These never change stored coordinates or "
            "measured values.",
        )

    def set_message(self, text: str) -> None:
        self.message.setText(text)

    def clear(self) -> None:
        for field in (
            self.position, self.stored, self.modality, self.millimetres, self.side_hint
        ):
            field.set_value("–", muted=True)
        self.zoom.set_value("–", muted=True)
        self.window.set_value("–", muted=True)
