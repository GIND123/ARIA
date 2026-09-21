"""The guided tour.

A translucent overlay dims the window, cuts a hole around the part being
described and anchors a callout beside it. Every step points at something real,
so the tour teaches the actual interface rather than a picture of it.

The tour can be skipped at any point and re-run from the Help menu. It is shown
once on first run, and again after an update that changes the tour, which is
what the stored tour version is for.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...version import APP_NAME
from ..theme import METRICS, PALETTE

#: Version of the tour content. Raising this offers the tour again after an
#: update that changed it.
TOUR_VERSION = "1.0.0"


@dataclass
class TourStep:
    """One step: what it points at, and what it says."""

    title: str
    body: str
    #: Dotted attribute path from the main window to the widget to highlight.
    target: str = ""
    #: Module to switch to before the step is shown.
    module: str = ""
    #: Where to put the callout relative to the target.
    placement: str = "auto"


STEPS = [
    TourStep(
        title=f"Welcome to {APP_NAME}",
        body=(
            "This tour takes about two minutes and points at the real controls as "
            "it goes. You can skip it now and start it again at any time from "
            "Help, Guided tour.\n\n"
            "ARIA is an annotation and research data tool. It records what you "
            "measure and how you measured it. It does not diagnose."
        ),
    ),
    TourStep(
        title="Modules",
        body=(
            "Work is grouped into modules. Cases is where you find and open an "
            "image, Annotate is where you draw, Measure holds calibration and "
            "derived values, Review is for checking submissions.\n\n"
            "The arrows beside the selector take you back to where you were."
        ),
        target="module_combo",
    ),
    TourStep(
        title="The case list",
        body=(
            "Cases are listed with their workflow state shown as both a symbol and "
            "a word. Double click a case to open it, or use Open read only to look "
            "without taking the editing lock.\n\n"
            "The Calibration column tells you at a glance whether millimetre "
            "values are available for that case."
        ),
        target="case_browser",
        module="cases",
    ),
    TourStep(
        title="Importing images",
        body=(
            "ARIA reads DICOM Part 10 files and PNG images. Anything else is "
            "refused with an explanation rather than a failure part way through.\n\n"
            "The original file is kept unchanged and linked to your annotations by "
            "a checksum, so there is always a way back to the exact pixels you "
            "worked on."
        ),
        target="action_import",
    ),
    TourStep(
        title="Choosing a side",
        body=(
            "Each side is annotated independently. Right is drawn with a solid "
            "stroke and left with a dashed one, so you can tell them apart without "
            "relying on colour.\n\n"
            "Remember that the patient's right appears on the left of the image. "
            "ARIA marks R and L on the view to keep that straight."
        ),
        target="side_combo",
        module="annotate",
    ),
    TourStep(
        title="The label list",
        body=(
            "Pick what you are about to draw. The two columns show whether each "
            "label is recorded on the right and the left: a filled circle means "
            "recorded, a triangle means required and still missing, a hollow "
            "circle means recorded as absent.\n\n"
            "Required labels are shown in bold."
        ),
        target="annotate_panel",
    ),
    TourStep(
        title="Drawing tools",
        body=(
            "The toolbar has a tool for each kind of geometry, with number key "
            "shortcuts. Press Escape to cancel a shape part way through, Enter to "
            "finish a contour.\n\n"
            "Snapping is on by default, so the endpoints of an index line land on "
            "the contour you traced rather than near it."
        ),
        target="tools_toolbar",
    ),
    TourStep(
        title="Constructing index lines",
        body=(
            "Once you have traced the periosteal and endosteal borders and marked "
            "the mental foramen, Construct computes the cortical width line along "
            "the perpendicular the protocol defines.\n\n"
            "It is ordinary geometry from your own contours, not a prediction. The "
            "result is editable and it tells you how it was built."
        ),
        target="action_construct",
    ),
    TourStep(
        title="Recording what is absent",
        body=(
            "When a structure cannot be annotated, say so explicitly: not visible, "
            "not assessable, anatomy absent or uncertain.\n\n"
            "Absence is stored as a state with a reason. It is never written as a "
            "coordinate of zero, so nothing downstream can mistake it for a "
            "measurement at the top left corner of the image."
        ),
        target="annotate_panel",
    ),
    TourStep(
        title="Measurements and units",
        body=(
            "Derived values appear as you draw, each with the calibration that "
            "produced it.\n\n"
            "Millimetre values appear only when a calibration has been validated. "
            "Otherwise you get pixel values and dimensionless ratios such as the "
            "panoramic mandibular index, and the millimetre column says "
            "unavailable rather than guessing."
        ),
        target="measure_panel",
        module="measure",
    ),
    TourStep(
        title="The data probe",
        body=(
            "The strip along the bottom reports what is under the cursor: the "
            "pixel position, the stored value, the value after rescaling, and the "
            "position in millimetres when that is available.\n\n"
            "Zoom, windowing, inversion and filters change only what you see. "
            "Stored coordinates and measured values never move."
        ),
        target="probe",
    ),
    TourStep(
        title="Submission checks",
        body=(
            "The checks list tells you what still blocks submission and what to do "
            "about each item. Hover any entry for the full explanation.\n\n"
            "Submission is blocked until the mandatory annotations, the side "
            "labels, the orientation confirmation and the required flags are "
            "complete."
        ),
        target="annotate_panel",
        module="annotate",
    ),
    TourStep(
        title="Your work is saved as you go",
        body=(
            "Every completed change is written immediately and the database is "
            "configured so a committed change survives a power loss. The status "
            "bar shows when the last save happened.\n\n"
            "Undo and redo survive a restart too, because the edit history is "
            "stored rather than held in memory."
        ),
        target="status_save",
    ),
    TourStep(
        title="You are ready",
        body=(
            "Open a case from the Cases module and start with the contours: trace "
            "the periosteal and endosteal borders, then mark the mental foramen, "
            "then construct the index lines.\n\n"
            "Help, Keyboard shortcuts lists every shortcut. Help, Guided tour "
            "brings this back whenever you want it."
        ),
    ),
]


class TourCallout(QWidget):
    """The floating card that carries the step text."""

    next_clicked = Signal()
    back_clicked = Signal()
    skip_clicked = Signal()
    finish_clicked = Signal()
    dont_show_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("tourCallout")
        self.setStyleSheet(
            f"#tourCallout {{ background-color: {PALETTE.panel};"
            f" border: 1px solid {PALETTE.accent};"
            f" border-radius: {METRICS.radius + 2}px; }}"
        )
        self.setFixedWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        self.step_label = QLabel(self)
        self.step_label.setProperty("dim", True)
        font = QFont()
        font.setPointSizeF(7.5)
        font.setBold(True)
        self.step_label.setFont(font)

        self.title_label = QLabel(self)
        self.title_label.setProperty("heading", True)
        self.title_label.setWordWrap(True)

        self.body_label = QLabel(self)
        self.body_label.setWordWrap(True)
        self.body_label.setMinimumHeight(110)
        self.body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        layout.addWidget(self.step_label)
        layout.addWidget(self.title_label)
        layout.addWidget(self.body_label, 1)

        self.dont_show = QCheckBox("Do not show this on startup", self)
        self.dont_show.toggled.connect(self.dont_show_changed)
        layout.addWidget(self.dont_show)

        buttons = QHBoxLayout()
        buttons.setSpacing(7)
        self.skip_button = QPushButton("Skip tour", self)
        self.back_button = QPushButton("Back", self)
        self.next_button = QPushButton("Next", self)
        self.next_button.setProperty("accent", True)
        self.next_button.setDefault(True)

        buttons.addWidget(self.skip_button)
        buttons.addStretch(1)
        buttons.addWidget(self.back_button)
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        self.skip_button.clicked.connect(self.skip_clicked)
        self.back_button.clicked.connect(self.back_clicked)
        self.next_button.clicked.connect(self._on_next)
        self._is_last = False

    def _on_next(self) -> None:
        if self._is_last:
            self.finish_clicked.emit()
        else:
            self.next_clicked.emit()

    def set_step(self, index: int, total: int, step: TourStep) -> None:
        self.step_label.setText(f"STEP {index + 1} OF {total}")
        self.title_label.setText(step.title)
        self.body_label.setText(step.body)
        self.back_button.setEnabled(index > 0)
        self._is_last = index == total - 1
        self.next_button.setText("Finish" if self._is_last else "Next")
        self.skip_button.setVisible(not self._is_last)
        self.dont_show.setVisible(index == 0)


class GuidedTour(QWidget):
    """The dimming overlay that runs the tour over the main window."""

    finished = Signal(bool)   # completed rather than skipped

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.steps = STEPS
        self.index = 0
        self._dont_show = False

        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)

        self.callout = TourCallout(self)
        self.callout.next_clicked.connect(self.next_step)
        self.callout.back_clicked.connect(self.previous_step)
        self.callout.skip_clicked.connect(lambda: self.stop(False))
        self.callout.finish_clicked.connect(lambda: self.stop(True))
        self.callout.dont_show_changed.connect(self._set_dont_show)

        self._highlight = QRect()
        self._animation = QPropertyAnimation(self.callout, b"pos", self)
        self._animation.setDuration(220)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    # -- lifecycle -----------------------------------------------------------

    def start(self, index: int = 0) -> None:
        self.index = index
        self.setGeometry(self.main_window.rect())
        self.show()
        self.raise_()
        self.setFocus()
        self._show_step()

    def stop(self, completed: bool) -> None:
        settings = self.main_window.config.settings
        if completed or self._dont_show:
            settings.tour_completed = True
            settings.tour_version_seen = TOUR_VERSION
            self.main_window.config.save()
        self.hide()
        self.finished.emit(completed)
        self.deleteLater()

    def next_step(self) -> None:
        if self.index < len(self.steps) - 1:
            self.index += 1
            self._show_step()
        else:
            self.stop(True)

    def previous_step(self) -> None:
        if self.index > 0:
            self.index -= 1
            self._show_step()

    def _set_dont_show(self, value: bool) -> None:
        self._dont_show = value

    # -- presentation --------------------------------------------------------

    def _resolve_target(self, path: str):
        if not path:
            return None
        node = self.main_window
        for part in path.split("."):
            node = getattr(node, part, None)
            if node is None:
                return None
        if isinstance(node, QWidget):
            return node
        # A QAction is reached through the widget that hosts it.
        from PySide6.QtGui import QAction

        if isinstance(node, QAction):
            for widget in node.associatedObjects():
                if isinstance(widget, QWidget) and widget.isVisible():
                    return widget
        return None

    def _show_step(self) -> None:
        step = self.steps[self.index]
        if step.module:
            try:
                self.main_window.set_module(step.module)
            except Exception:
                pass

        self.setGeometry(self.main_window.rect())
        self.callout.set_step(self.index, len(self.steps), step)
        self.callout.adjustSize()

        target = self._resolve_target(step.target)
        if target is not None and target.isVisible():
            top_left = target.mapTo(self.main_window, QPoint(0, 0))
            self._highlight = QRect(top_left, target.size()).adjusted(-6, -6, 6, 6)
        else:
            self._highlight = QRect()

        self._place_callout(step)
        self.update()

    def _place_callout(self, step: TourStep) -> None:
        area = self.rect()
        size = self.callout.sizeHint()
        width, height = self.callout.width(), max(size.height(), 260)
        self.callout.resize(width, height)

        if self._highlight.isNull():
            position = QPoint(
                area.center().x() - width // 2, area.center().y() - height // 2
            )
        else:
            gap = 18
            right_space = area.right() - self._highlight.right()
            left_space = self._highlight.left() - area.left()
            below_space = area.bottom() - self._highlight.bottom()

            if right_space >= width + gap:
                x = self._highlight.right() + gap
                y = max(12, min(self._highlight.center().y() - height // 2, area.bottom() - height - 12))
            elif left_space >= width + gap:
                x = self._highlight.left() - width - gap
                y = max(12, min(self._highlight.center().y() - height // 2, area.bottom() - height - 12))
            elif below_space >= height + gap:
                x = max(12, min(self._highlight.center().x() - width // 2, area.right() - width - 12))
                y = self._highlight.bottom() + gap
            else:
                x = max(12, min(self._highlight.center().x() - width // 2, area.right() - width - 12))
                y = max(12, self._highlight.top() - height - gap)
            position = QPoint(int(x), int(y))

        if self.callout.isVisible():
            self._animation.stop()
            self._animation.setStartValue(self.callout.pos())
            self._animation.setEndValue(position)
            self._animation.start()
        else:
            self.callout.move(position)
            self.callout.show()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        overlay = QPainterPath()
        overlay.addRect(self.rect())
        if not self._highlight.isNull():
            hole = QPainterPath()
            hole.addRoundedRect(self._highlight, 5, 5)
            overlay = overlay.subtracted(hole)

        painter.fillPath(overlay, QColor(8, 10, 12, 205))

        if not self._highlight.isNull():
            pen = QPen(QColor(PALETTE.accent), 2.0)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(self._highlight, 5, 5)
        painter.end()

    def mousePressEvent(self, event) -> None:
        # Clicking the dimmed area advances, which is what people try first.
        if not self.callout.geometry().contains(event.position().toPoint()):
            self.next_step()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.stop(False)
        elif event.key() in (Qt.Key_Right, Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.next_step()
        elif event.key() == Qt.Key_Left:
            self.previous_step()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._show_step()


def should_offer_tour(settings) -> bool:
    """True when the tour has not been seen, or has changed since it was."""
    if not settings.tour_completed:
        return True
    return settings.tour_version_seen != TOUR_VERSION
