"""The framed view pane and the layout manager that arranges panes.

Each pane carries a coloured header bar with the pane name, a control slider and
a small set of pane level actions. The colour identifies the pane at a glance
and matches the name printed in the same bar, so the colour is a convenience
rather than the only way to tell panes apart.

The slider is contextual. On the main pane it is zoom; on a magnifier pane it is
the magnification factor; on a comparison pane it is the blend between the two
revisions being compared.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon as make_icon
from ..theme import METRICS, PALETTE
from ..widgets.common import make_tool_button
from .canvas import ImageCanvas


class ViewHeader(QFrame):
    """The coloured bar at the top of a pane."""

    slider_changed = Signal(float)
    maximise_requested = Signal()
    close_requested = Signal()

    def __init__(self, name: str, letter: str, colour: str, parent=None):
        super().__init__(parent)
        self.colour = colour
        self.setFixedHeight(METRICS.view_header_height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(6)

        self.tag = QLabel(letter, self)
        self.tag.setFixedWidth(22)
        self.tag.setAlignment(Qt.AlignCenter)
        font = QFont()
        font.setBold(True)
        font.setPointSizeF(8.5)
        self.tag.setFont(font)
        self.tag.setStyleSheet(
            f"background-color: {colour}; color: #10131a; font-weight: 700;"
        )

        self.name_label = QLabel(name, self)
        self.name_label.setStyleSheet(f"color: {PALETTE.text}; font-weight: 600;")

        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setMinimum(0)
        self.slider.setMaximum(1000)
        self.slider.setValue(500)
        self.slider.setFixedHeight(14)
        self.slider.setMinimumWidth(80)
        self.slider.valueChanged.connect(
            lambda v: self.slider_changed.emit(v / 1000.0)
        )

        self.value_label = QLabel("", self)
        self.value_label.setMinimumWidth(74)
        self.value_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value_label.setProperty("mono", True)
        self.value_label.setStyleSheet(f"color: {PALETTE.text_dim};")

        self.maximise_button = make_tool_button("one_up", "Maximise this pane", size=14, parent=self)
        self.maximise_button.clicked.connect(self.maximise_requested)

        layout.addWidget(self.tag, 0)
        layout.addWidget(self.name_label, 0)
        layout.addStretch(1)
        layout.addWidget(self.slider, 0)
        layout.addWidget(self.value_label, 0)
        layout.addWidget(self.maximise_button, 0)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PALETTE.header))
        painter.fillRect(0, self.height() - 2, self.width(), 2, QColor(self.colour))
        painter.end()
        super().paintEvent(event)

    def set_value_text(self, text: str) -> None:
        self.value_label.setText(text)

    def set_slider_silently(self, fraction: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        self.slider.blockSignals(False)

    def set_name(self, name: str) -> None:
        self.name_label.setText(name)


class ViewPane(QFrame):
    """A canvas with its header bar."""

    maximise_requested = Signal(object)

    #: Zoom range the header slider maps onto, as a multiple of the fit scale.
    ZOOM_MIN = 0.05
    ZOOM_MAX = 16.0

    def __init__(self, key: str, name: str, letter: str, colour: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(f"background-color: {PALETTE.canvas};")

        self.header = ViewHeader(name, letter, colour, self)
        self.canvas = ImageCanvas(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.header, 0)
        layout.addWidget(self.canvas, 1)

        self.header.slider_changed.connect(self._on_slider)
        self.header.maximise_requested.connect(lambda: self.maximise_requested.emit(self))
        self.canvas.zoom_changed.connect(self._on_zoom_changed)

    def _on_slider(self, fraction: float) -> None:
        import math

        span = math.log(self.ZOOM_MAX / self.ZOOM_MIN)
        factor = self.ZOOM_MIN * math.exp(fraction * span)
        self.canvas.set_zoom(factor)

    def _on_zoom_changed(self, factor: float) -> None:
        import math

        self.header.set_value_text(f"{factor * 100:.0f}%")
        span = math.log(self.ZOOM_MAX / self.ZOOM_MIN)
        fraction = math.log(max(self.ZOOM_MIN, factor) / self.ZOOM_MIN) / span
        self.header.set_slider_silently(fraction)


#: Available workspace layouts. Each entry is a display name and the panes it
#: shows, in row major order with the grid shape.
LAYOUTS = {
    "one_up": {
        "name": "One pane",
        "icon": "one_up",
        "shape": (1, 1),
        "panes": ["main"],
        "description": "A single large view. The default for annotation work.",
    },
    "two_by_two": {
        "name": "Four panes",
        "icon": "grid",
        "shape": (2, 2),
        "panes": ["main", "right", "left", "overview"],
        "description": (
            "The full image with a magnified right side, a magnified left side "
            "and an overview."
        ),
    },
    "side_by_side": {
        "name": "Two panes",
        "icon": "side_by_side",
        "shape": (1, 2),
        "panes": ["main", "compare"],
        "description": "Two views side by side, for comparing revisions or annotators.",
    },
    "main_and_details": {
        "name": "Main and details",
        "icon": "grid",
        "shape": (2, 2),
        "panes": ["main", "right", "left"],
        "description": "A large main view with two magnified regions beside it.",
    },
}

PANE_DEFINITIONS = {
    "main": ("Main", "M", PALETTE.view_main),
    "right": ("Right side", "R", PALETTE.view_right),
    "left": ("Left side", "L", PALETTE.view_left),
    "overview": ("Overview", "O", PALETTE.view_compare),
    "compare": ("Comparison", "C", PALETTE.view_compare),
}


class ViewWorkspace(QWidget):
    """Holds the panes and switches between layouts."""

    active_pane_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.panes: dict = {}
        self.layout_key = "one_up"
        self._maximised: str | None = None

        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(2)

        for key, (name, letter, colour) in PANE_DEFINITIONS.items():
            pane = ViewPane(key, name, letter, colour, self)
            pane.maximise_requested.connect(self._toggle_maximise)
            pane.setVisible(False)
            self.panes[key] = pane

        self.set_layout("one_up")

    # -- access --------------------------------------------------------------

    @property
    def main(self) -> ViewPane:
        return self.panes["main"]

    @property
    def main_canvas(self) -> ImageCanvas:
        return self.panes["main"].canvas

    def visible_panes(self) -> list:
        return [p for p in self.panes.values() if p.isVisible()]

    def canvases(self) -> list:
        return [p.canvas for p in self.visible_panes()]

    # -- layout --------------------------------------------------------------

    def set_layout(self, key: str) -> None:
        definition = LAYOUTS.get(key)
        if definition is None:
            return
        self.layout_key = key
        self._maximised = None

        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(self)
                widget.setVisible(False)

        # Stretch factors persist on a grid after its widgets are removed, so a
        # two by two layout would leave the single pane of a one up layout
        # occupying a quarter of the area. They are cleared before the new
        # layout sets its own.
        for index in range(max(self._grid.rowCount(), 4)):
            self._grid.setRowStretch(index, 0)
        for index in range(max(self._grid.columnCount(), 4)):
            self._grid.setColumnStretch(index, 0)

        rows, cols = definition["shape"]
        panes = definition["panes"]

        if key == "main_and_details":
            # The main view spans both rows on the left, details stack on the right.
            self._grid.addWidget(self.panes["main"], 0, 0, 2, 1)
            self._grid.addWidget(self.panes["right"], 0, 1)
            self._grid.addWidget(self.panes["left"], 1, 1)
            self._grid.setColumnStretch(0, 2)
            self._grid.setColumnStretch(1, 1)
            self._grid.setRowStretch(0, 1)
            self._grid.setRowStretch(1, 1)
        else:
            for index, pane_key in enumerate(panes):
                row, col = divmod(index, cols)
                self._grid.addWidget(self.panes[pane_key], row, col)
            for c in range(cols):
                self._grid.setColumnStretch(c, 1)
            for r in range(rows):
                self._grid.setRowStretch(r, 1)

        for pane_key in panes:
            self.panes[pane_key].setVisible(True)
        self.active_pane_changed.emit("main")

    def _toggle_maximise(self, pane: ViewPane) -> None:
        if self._maximised == pane.key:
            self.set_layout(self.layout_key)
            return
        for other in self.panes.values():
            other.setVisible(other is pane)
        self._maximised = pane.key

    def apply_to_all(self, action) -> None:
        """Run a callable against every visible canvas."""
        for canvas in self.canvases():
            action(canvas)
