"""Setting the scale, and then checking that the scale is right.

A panoramic unit does not image the patient at one magnification. It magnifies
vertically and horizontally by different amounts, and the horizontal amount
changes across the arch, so the pixel spacing in a DICOM header describes the
detector and says nothing about the size of a jaw. Establishing a scale from a
single drawn line and calling it validated states a number; it does not show
that the number is right.

This dialog therefore has two halves. The first sets the scale from an object
of known size. The second measures a different object of known size and reports
how far out the answer came. Only the second one can answer the question
somebody eventually asks, which is whether a centimetre reported here is a
centimetre of patient.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.units import VERIFICATION_TOLERANCE_PERCENT
from ..theme import PALETTE
from ..widgets.common import Banner, HLine, SectionLabel, make_button


class MeasurementSlot(QWidget):
    """One drawn line, with the true size of what it was drawn on."""

    draw_requested = Signal()

    def __init__(self, title: str, prompt: str, default_mm: float, parent=None):
        super().__init__(parent)
        self.dx_px = 0.0
        self.dy_px = 0.0
        self.length_px = 0.0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(SectionLabel(title, self))

        self.hint = QLabel(prompt, self)
        self.hint.setWordWrap(True)
        self.hint.setProperty("dim", True)
        layout.addWidget(self.hint)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.draw_button = make_button("Draw on the image", "measure", parent=self)
        self.draw_button.clicked.connect(self.draw_requested.emit)
        row.addWidget(self.draw_button)

        self.drawn_label = QLabel("Nothing drawn yet", self)
        self.drawn_label.setProperty("dim", True)
        row.addWidget(self.drawn_label, 1)
        layout.addLayout(row)

        form = QFormLayout()
        form.setSpacing(8)
        self.true_mm = QDoubleSpinBox(self)
        self.true_mm.setRange(0.1, 500.0)
        self.true_mm.setDecimals(3)
        self.true_mm.setSuffix(" mm")
        self.true_mm.setValue(default_mm)
        form.addRow("True size", self.true_mm)

        self.description = QLineEdit(self)
        self.description.setPlaceholderText(
            "What it is and where it sits, for example 5 mm ball, right premolar"
        )
        form.addRow("Description", self.description)
        layout.addLayout(form)

    def set_measurement(self, dx_px: float, dy_px: float) -> None:
        self.dx_px = float(dx_px)
        self.dy_px = float(dy_px)
        self.length_px = (dx_px ** 2 + dy_px ** 2) ** 0.5
        bearing = "horizontal" if abs(dx_px) >= abs(dy_px) else "vertical"
        self.drawn_label.setText(
            f"{self.length_px:.1f} px, mostly {bearing}"
        )
        self.drawn_label.setProperty("dim", False)
        self.drawn_label.setStyleSheet(f"color: {PALETTE.success};")

    @property
    def has_measurement(self) -> bool:
        return self.length_px > 1.0


class CalibrationDialog(QDialog):
    """Set the scale from one object, then check it against another."""

    #: Asks the window to put the ruler in the person's hand. The dialog hides
    #: itself while they draw, because the line they need is under it.
    measure_requested = Signal(object)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.calibration = None
        self._active_slot = None

        self.setWindowTitle("Calibrate this image")
        # Tall enough that the check, which is the half people skip, is on
        # screen rather than below the fold. The body scrolls on a short
        # display rather than squeezing every section until the text collides.
        self.setMinimumSize(600, 640)
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        layout = QVBoxLayout(body)
        layout.setContentsMargins(16, 14, 16, 8)
        layout.setSpacing(10)

        heading = QLabel("Set the scale, then check it", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        intro = QLabel(
            "A panoramic unit magnifies the patient, by different amounts "
            "vertically and horizontally, and by different amounts across the "
            "arch. Header pixel spacing describes the detector, not the jaw. "
            "Use an object whose true size you know, placed in the image at the "
            "region you intend to measure.",
            self,
        )
        intro.setWordWrap(True)
        intro.setProperty("dim", True)
        layout.addWidget(intro)

        # -- how many axes ----------------------------------------------------
        axis_row = QVBoxLayout()
        axis_row.setSpacing(4)
        self.one_axis = QRadioButton("One reference, used for both axes", self)
        self.one_axis.setToolTip(
            "One drawn line sets a single scale used for height and width "
            "alike. Simple, and wrong by however much the unit's vertical and "
            "horizontal magnification differ."
        )
        self.two_axis = QRadioButton(
            "A separate reference for each axis, which is what a panoramic "
            "image needs", self,
        )
        self.two_axis.setToolTip(
            "A vertical and a horizontal reference give each axis its own "
            "scale, which is what a panoramic image actually needs."
        )
        self.one_axis.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.one_axis)
        group.addButton(self.two_axis)
        axis_row.addWidget(self.one_axis)
        axis_row.addWidget(self.two_axis)
        layout.addLayout(axis_row)

        layout.addWidget(HLine(self))

        # -- the references ---------------------------------------------------
        self.primary = MeasurementSlot(
            "Reference",
            "Draw along an object whose true size you know.",
            25.0, self,
        )
        self.primary.draw_requested.connect(lambda: self._request(self.primary))
        layout.addWidget(self.primary)

        self.horizontal = MeasurementSlot(
            "Horizontal reference",
            "Draw along the horizontal object. The first reference is then "
            "treated as the vertical one.",
            25.0, self,
        )
        self.horizontal.draw_requested.connect(lambda: self._request(self.horizontal))
        self.horizontal.setVisible(False)
        layout.addWidget(self.horizontal)

        layout.addWidget(HLine(self))

        # -- the check --------------------------------------------------------
        self.check = MeasurementSlot(
            "Check",
            f"Draw along a second object of known size, one the scale above was "
            f"not taken from. This is the only step that shows whether the "
            f"scale is right. Anything further than "
            f"{VERIFICATION_TOLERANCE_PERCENT:.3g} per cent out is reported as "
            f"a failure.",
            10.0, self,
        )
        self.check.draw_requested.connect(lambda: self._request(self.check))
        layout.addWidget(self.check)

        layout.addStretch(1)

        self.banner = Banner(self)
        self.banner.setContentsMargins(16, 0, 16, 0)
        outer.addWidget(self.banner)

        buttons = QDialogButtonBox(Qt.Horizontal, self)
        buttons.setContentsMargins(16, 8, 16, 12)
        self.apply_button = make_button("Apply calibration", "calibrate", accent=True, parent=self)
        self.cancel_button = make_button("Cancel", parent=self)
        buttons.addButton(self.apply_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        outer.addWidget(buttons)

        self.apply_button.clicked.connect(self._apply)
        self.cancel_button.clicked.connect(self.reject)
        self.two_axis.toggled.connect(self._on_axis_mode)
        self._update_state()

    # -- drawing -------------------------------------------------------------

    def _on_axis_mode(self, two: bool) -> None:
        self.horizontal.setVisible(two)
        self.primary.hint.setText(
            "Draw along the vertical object whose true size you know."
            if two else
            "Draw along an object whose true size you know."
        )
        self._update_state()

    def _request(self, slot: MeasurementSlot) -> None:
        self._active_slot = slot
        self.hide()
        self.measure_requested.emit(slot)

    def accept_measurement(self, dx_px: float, dy_px: float) -> None:
        """Called back by the window once a line has been drawn."""
        if self._active_slot is not None:
            self._active_slot.set_measurement(dx_px, dy_px)
            self._active_slot = None
        self.show()
        self.raise_()
        self._update_state()

    def cancel_measurement(self) -> None:
        self._active_slot = None
        self.show()
        self.raise_()

    # -- state ---------------------------------------------------------------

    def _update_state(self) -> None:
        ready = self.primary.has_measurement
        if self.two_axis.isChecked():
            ready = ready and self.horizontal.has_measurement
        self.apply_button.setEnabled(ready)

        if not ready:
            self.banner.show_message(
                "Draw the reference to set the scale.", "info"
            )
        elif not self.check.has_measurement:
            self.banner.show_message(
                "The scale can be applied now, but nothing has checked it. "
                "Draw a check object to find out how accurate it is.",
                "warn",
            )
        else:
            self.banner.show_message(
                "Ready. The check is measured when the calibration is applied.",
                "ok",
            )

    # -- applying ------------------------------------------------------------

    def _apply(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        from ...core.models import utc_now
        from ...core.units import Calibration

        user = self.controller.user
        who = user.pseudonym if user else "unknown"
        when = utc_now()
        description = self.primary.description.text().strip() or "Manual reference"

        try:
            if self.two_axis.isChecked():
                calibration = Calibration.from_known_lengths(
                    self.primary.true_mm.value(), abs(self.primary.dy_px) or self.primary.length_px,
                    self.horizontal.true_mm.value(),
                    abs(self.horizontal.dx_px) or self.horizontal.length_px,
                    description, who, when,
                )
            else:
                calibration = Calibration.from_known_length(
                    self.primary.true_mm.value(), self.primary.length_px,
                    description, who, when,
                )
        except ValueError as exc:
            QMessageBox.warning(self, "Calibration not accepted", str(exc))
            return

        if self.check.has_measurement:
            try:
                result = calibration.verify(
                    self.check.true_mm.value(), self.check.dx_px, self.check.dy_px,
                    self.check.description.text().strip() or "Check object",
                    who, when,
                )
            except ValueError as exc:
                QMessageBox.warning(self, "The check could not be recorded", str(exc))
                return

            if not result["passed"]:
                # Refusing outright would be wrong: the person may be
                # calibrating precisely to discover how far out this machine
                # is. Saying plainly what it means, and recording it either
                # way, is the useful behaviour.
                answer = QMessageBox.warning(
                    self, "The calibration did not pass its check",
                    f"{calibration.verification_line()}\n\n"
                    f"A measurement taken with this scale will be wrong by "
                    f"about that much. That usually means the check object sat "
                    f"at a different depth or a different part of the arch than "
                    f"the reference, which is exactly the error a panoramic "
                    f"image introduces.\n\n"
                    f"Apply it anyway? The result is recorded with the case and "
                    f"appears in every export, so a reader can see it.",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    self.show()
                    return

        self.calibration = calibration
        self.accept()
