"""Image display controls: brightness, contrast, magnification and sharpness.

These four change how the image is drawn and nothing else. The pixels that are
measured are always the ones that arrived, so a slider can be moved freely in
the middle of a case without putting a measurement in doubt. That separation is
the whole reason the panel can be used as casually as it is: turning the
sharpness up to find the endosteal margin does not quietly become part of the
record.

Reset returns every control to the state the image arrived in, which for a
DICOM study means the window the device recorded rather than a neutral grey.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..widgets.common import HLine, make_button

#: Each row: attribute, label, slider range, default, and the suffix shown
#: after the number. The ranges are integers because a slider is integer
#: valued; the conversion to the stored float lives in one place below.
CONTROLS = (
    ("brightness", "Brightness", -100, 100, 0, ""),
    ("contrast", "Contrast", 10, 400, 100, "%"),
    # The floor matches what the canvas itself will accept. A panoramic
    # radiograph fitted to a modest window can sit below a tenth of actual
    # size, and a slider that could not reach there would jump the image the
    # moment it was touched.
    ("magnification", "Magnification", 2, 800, 100, "%"),
    ("sharpness", "Sharpness", 0, 100, 0, ""),
)


class SliderRow(QWidget):
    """One named slider with its value shown alongside."""

    changed = Signal(int)

    def __init__(self, key, label, low, high, default, suffix, parent=None):
        super().__init__(parent)
        self.key = key
        self.default = default
        self.suffix = suffix

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 1, 10, 1)
        layout.setSpacing(8)

        self.name = QLabel(label, self)
        self.name.setFixedWidth(86)
        layout.addWidget(self.name)

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(low, high)
        self.slider.setValue(default)
        self.slider.setAccessibleName(label)
        # Page steps a keyboard user can rely on. One unit per key press is
        # unusable across a range of several hundred.
        self.slider.setSingleStep(max(1, (high - low) // 100))
        self.slider.setPageStep(max(1, (high - low) // 10))
        layout.addWidget(self.slider, 1)

        self.value_label = QLabel(self._format(default), self)
        self.value_label.setMinimumWidth(44)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setProperty("dim", True)
        layout.addWidget(self.value_label)

        self.slider.valueChanged.connect(self._on_value)

    def _format(self, value: int) -> str:
        return f"{value}{self.suffix}"

    def _on_value(self, value: int) -> None:
        self.value_label.setText(self._format(value))
        self.changed.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def set_value(self, value: int) -> None:
        """Move the slider without reporting it as somebody moving it."""
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(int(round(value)))
        self.slider.blockSignals(blocked)
        self.value_label.setText(self._format(self.slider.value()))


class DisplayPanel(QWidget):
    """The four controls, plus the reset that undoes all of them at once."""

    #: Emitted when a control moves, with the attribute that changed.
    control_changed = Signal(str)
    reset_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.rows: dict = {}
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 8)
        layout.setSpacing(2)

        # The dock's title bar already names the panel, so the only line above
        # the controls is the one saying what they do not touch.
        hint = QLabel(
            "Changes how the image is drawn. Measurements always come from the "
            "pixels as they arrived.",
            self,
        )
        hint.setProperty("dim", True)
        hint.setWordWrap(True)
        hint.setContentsMargins(10, 0, 10, 6)
        layout.addWidget(hint)

        for key, label, low, high, default, suffix in CONTROLS:
            row = SliderRow(key, label, low, high, default, suffix, self)
            row.changed.connect(lambda _v, k=key: self._on_changed(k))
            self.rows[key] = row
            layout.addWidget(row)

        layout.addWidget(HLine(self))

        button_row = QHBoxLayout()
        button_row.setContentsMargins(10, 4, 10, 0)
        self.reset_button = make_button("Reset to original", "undo", parent=self)
        self.reset_button.setToolTip(
            "Put every control back to the state the image arrived in, "
            "including the window the device recorded."
        )
        self.reset_button.clicked.connect(self.reset_requested.emit)
        button_row.addWidget(self.reset_button, 1)
        layout.addLayout(button_row)

        layout.addStretch(1)
        self.set_enabled_for_case(False)

    # -- state ---------------------------------------------------------------

    def set_enabled_for_case(self, has_image: bool) -> None:
        """Controls that cannot act on anything say so by being unavailable."""
        for row in self.rows.values():
            row.setEnabled(has_image)
        self.reset_button.setEnabled(has_image)

    def _on_changed(self, key: str) -> None:
        if self._loading:
            return
        self.control_changed.emit(key)

    def values(self) -> dict:
        return {key: row.value() for key, row in self.rows.items()}

    # -- conversion between slider integers and stored values ----------------

    def brightness(self) -> float:
        """Stored as -1.0 to 1.0, shown as -100 to 100."""
        return self.rows["brightness"].value() / 100.0

    def contrast(self) -> float:
        """Stored as a multiplier around mid grey, shown as a percentage."""
        return self.rows["contrast"].value() / 100.0

    def zoom_factor(self) -> float:
        return self.rows["magnification"].value() / 100.0

    def sharpness(self) -> float:
        return self.rows["sharpness"].value() / 100.0

    def load_from(self, settings, zoom: float | None = None) -> None:
        """Show what the image is currently being drawn with.

        Called when a case opens and whenever something other than this panel
        changes the display, so that the sliders never describe a state the
        image is not in.
        """
        self._loading = True
        try:
            self.rows["brightness"].set_value(settings.brightness * 100.0)
            self.rows["contrast"].set_value(settings.contrast * 100.0)
            sharp = (
                settings.filter_strength * 100.0
                if settings.filter_name == "unsharp" else 0.0
            )
            self.rows["sharpness"].set_value(sharp)
            if zoom is not None:
                self.rows["magnification"].set_value(zoom * 100.0)
        finally:
            self._loading = False
