"""Reusable widgets: collapsible sections, status chips, field rows and banners.

The collapsible section is the backbone of the module panel. Sections remember
whether they were open, so an annotator who collapses everything except the
grading section gets that layout back on the next case rather than reopening it
every time.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon as make_icon
from ..theme import METRICS, PALETTE


class CollapsibleSection(QWidget):
    """A titled section that expands and collapses.

    The disclosure arrow is drawn by the button itself so the control reads the
    same on both platforms, where the native arrow glyphs differ noticeably.
    """

    toggled_open = Signal(bool)

    def __init__(self, title: str, parent=None, expanded: bool = True, icon_name: str = ""):
        super().__init__(parent)
        self.title = title

        self.toggle = QToolButton(self)
        self.toggle.setProperty("section", True)
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.toggle.setCursor(Qt.PointingHandCursor)
        if icon_name:
            self.toggle.setIcon(make_icon(icon_name, 16))
        self.toggle.clicked.connect(self._on_toggle)

        self.body = QFrame(self)
        self.body.setProperty("sectionBody", True)
        self.body.setVisible(expanded)
        self._body_layout = QVBoxLayout(self.body)
        self._body_layout.setContentsMargins(10, 9, 10, 10)
        self._body_layout.setSpacing(7)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.body)

    def _on_toggle(self, checked: bool) -> None:
        self.toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)
        self.body.setVisible(checked)
        self.toggled_open.emit(checked)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)
        self._on_toggle(expanded)

    def is_expanded(self) -> bool:
        return self.toggle.isChecked()

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def add_layout(self, layout: QLayout) -> None:
        self._body_layout.addLayout(layout)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def set_badge(self, text: str) -> None:
        """Show a short count or state beside the title."""
        self.toggle.setText(f"{self.title}   {text}" if text else self.title)


class StatusChip(QLabel):
    """A small status pill carrying a glyph, text and colour.

    The glyph is what makes the state readable without colour (NFR 010); the
    colour is a secondary cue, never the only one.
    """

    LEVELS = {
        "ok": (PALETTE.success, "✓"),
        "warn": (PALETTE.warning, "!"),
        "danger": (PALETTE.danger, "✗"),
        "info": (PALETTE.info, "i"),
        "neutral": (PALETTE.text_dim, "–"),
    }

    def __init__(self, text: str = "", level: str = "neutral", parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.set_state(text, level)

    def set_state(self, text: str, level: str = "neutral", tooltip: str = "") -> None:
        colour, glyph = self.LEVELS.get(level, self.LEVELS["neutral"])
        self.level = level
        self.setText(f"{glyph}  {text}" if text else glyph)
        self.setStyleSheet(
            f"QLabel {{ color: {colour};"
            f" border: 1px solid {colour};"
            f" border-radius: {METRICS.radius}px;"
            f" padding: 2px 8px;"
            f" background: transparent; }}"
        )
        if tooltip:
            self.setToolTip(tooltip)


class FieldRow(QWidget):
    """A label and value pair, aligned across a panel."""

    def __init__(self, label: str, value: str = "", mono: bool = False, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.label = QLabel(label, self)
        self.label.setProperty("dim", True)
        self.label.setMinimumWidth(118)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.label.setWordWrap(True)

        self.value = QLabel(value, self)
        self.value.setWordWrap(True)
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if mono:
            self.value.setProperty("mono", True)
        self.value.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        layout.addWidget(self.label, 0)
        layout.addWidget(self.value, 1)

    def set_value(self, text: str, status: str = "") -> None:
        self.value.setText(text)
        if status:
            self.value.setProperty("status", status)
        else:
            self.value.setProperty("status", None)
        self.value.style().unpolish(self.value)
        self.value.style().polish(self.value)

    def set_tooltip(self, text: str) -> None:
        self.label.setToolTip(text)
        self.value.setToolTip(text)


class Banner(QFrame):
    """An inline message with a level, a headline and an optional action."""

    action_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self.setVisible(False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(9)

        self.glyph = QLabel(self)
        self.glyph.setFixedWidth(16)
        self.glyph.setAlignment(Qt.AlignTop | Qt.AlignHCenter)

        self.text = QLabel(self)
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.action = QPushButton(self)
        self.action.setVisible(False)
        self.action.clicked.connect(self.action_clicked)

        layout.addWidget(self.glyph, 0)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.action, 0)

    def show_message(
        self, message: str, level: str = "info", action_text: str = "", tooltip: str = ""
    ) -> None:
        colour, glyph = StatusChip.LEVELS.get(level, StatusChip.LEVELS["info"])
        self.glyph.setText(glyph)
        self.glyph.setStyleSheet(f"color: {colour}; font-weight: 700;")
        self.text.setText(message)
        self.setStyleSheet(
            f"QFrame[card='true'] {{ border-left: 3px solid {colour};"
            f" background-color: {PALETTE.panel_alt}; }}"
        )
        self.action.setText(action_text)
        self.action.setVisible(bool(action_text))
        if tooltip:
            self.setToolTip(tooltip)
        self.setVisible(True)

    def clear(self) -> None:
        self.setVisible(False)


class SearchBox(QLineEdit):
    """A line edit with placeholder text and a clear action."""

    def __init__(self, placeholder: str = "Search", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.addAction(make_icon("search", 14), QLineEdit.LeadingPosition)


class SectionLabel(QLabel):
    """A small uppercase heading used inside sections."""

    def __init__(self, text: str, parent=None):
        super().__init__(text.upper(), parent)
        font = QFont()
        font.setPointSizeF(7.5)
        font.setBold(True)
        font.setLetterSpacing(QFont.PercentageSpacing, 108)
        self.setFont(font)
        self.setProperty("dim", True)


class HLine(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setProperty("hline", True)
        self.setFixedHeight(1)
        self.setFrameShape(QFrame.NoFrame)


class LegendSwatch(QWidget):
    """Colour and stroke sample for one label class and side."""

    def __init__(self, colour: str, dashed: bool, parent=None):
        super().__init__(parent)
        self.colour = colour
        self.dashed = dashed
        self.setFixedSize(QSize(26, 14))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(self.colour), 2.4)
        pen.setStyle(Qt.DashLine if self.dashed else Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        y = self.height() / 2
        painter.drawLine(2, int(y), self.width() - 2, int(y))
        painter.end()


class ScrollPanel(QScrollArea):
    """A vertically scrolling container for stacked sections.

    Horizontal scrolling is off, so the content has to fit the width. A wide
    child, such as a table with content sized columns, would otherwise force the
    container wider than the viewport and clip the wrapped text beside it. The
    container is therefore held to the viewport width, which makes labels wrap
    and tables squeeze rather than disappear off the edge.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.container = QWidget()
        self.container.setObjectName("scrollPanelContainer")
        self._layout = QVBoxLayout(self.container)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(7)
        self._layout.addStretch(1)
        self.setWidget(self.container)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.container.setMaximumWidth(self.viewport().width())

    def add_section(self, section: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, section)

    def add_widget(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def clear(self) -> None:
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()


class KeyValueGrid(QWidget):
    """A two column grid of label and value pairs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(4)
        self._grid.setColumnStretch(1, 1)
        self._rows: dict = {}

    def set(self, key: str, label: str, value: str, status: str = "", mono: bool = False) -> None:
        if key not in self._rows:
            name = QLabel(label, self)
            name.setProperty("dim", True)
            name.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            val = QLabel(self)
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextSelectableByMouse)
            if mono:
                val.setProperty("mono", True)
            row = self._grid.rowCount()
            self._grid.addWidget(name, row, 0)
            self._grid.addWidget(val, row, 1)
            self._rows[key] = (name, val)
        name, val = self._rows[key]
        name.setText(label)
        val.setText(value)
        val.setProperty("status", status or None)
        val.style().unpolish(val)
        val.style().polish(val)

    def clear_values(self) -> None:
        for _name, val in self._rows.values():
            val.setText("")


def make_button(
    text: str, icon_name: str = "", accent: bool = False, danger: bool = False,
    tooltip: str = "", parent=None,
) -> QPushButton:
    button = QPushButton(text, parent)
    if icon_name:
        button.setIcon(make_icon(icon_name, 15))
    if accent:
        button.setProperty("accent", True)
    if danger:
        button.setProperty("danger", True)
    if tooltip:
        button.setToolTip(tooltip)
    return button


def make_tool_button(
    icon_name: str, tooltip: str, checkable: bool = False, parent=None, size: int = 20
) -> QToolButton:
    button = QToolButton(parent)
    button.setIcon(make_icon(icon_name, size))
    button.setIconSize(QSize(size, size))
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    return button
