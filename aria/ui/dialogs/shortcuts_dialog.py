"""The keyboard shortcut reference.

Grouped by what a person is doing rather than by menu, because the question is
usually "how do I finish this contour" rather than "what is in the Annotate
menu".
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..widgets.common import SearchBox

SHORTCUTS = {
    "Getting around": [
        ("Ctrl+I", "Import images"),
        ("Ctrl+Shift+I", "Import a whole folder"),
        ("Ctrl+W", "Close the open case"),
        ("Ctrl+E", "Open the export module"),
        ("Ctrl+Q", "Exit"),
        ("F1", "Guided tour"),
        ("Ctrl+/", "This shortcut list"),
    ],
    "Drawing": [
        ("S", "Select and edit"),
        ("1", "Point"),
        ("2", "Two endpoint line"),
        ("3", "Contour"),
        ("4", "Region outline"),
        ("5", "Box"),
        ("6", "Fixed size analysis region"),
        ("7", "Brush"),
        ("8", "Eraser"),
        ("9", "Calibration ruler"),
        ("Enter", "Finish the contour or region being drawn"),
        ("Double click", "Finish the contour, or add a vertex to an existing one"),
        ("Escape", "Cancel the shape being drawn, or clear the selection"),
        ("Backspace", "Remove the last point placed"),
        ("N", "Toggle snapping to contours"),
        ("Ctrl+G", "Construct the selected index line from the contours"),
        ("Tab", "Switch between the right and left side"),
        ("Ctrl+M", "Record the selected label as absent"),
    ],
    "Editing": [
        ("Ctrl+Z", "Undo"),
        ("Ctrl+Y", "Redo"),
        ("Delete", "Delete the selected objects"),
        ("Ctrl+A", "Select every object"),
        ("Ctrl+Shift+A", "Deselect"),
        ("Arrow keys", "Nudge the selection by one image pixel"),
        ("Shift and arrow keys", "Nudge by ten pixels"),
        ("Shift and drag", "Move a whole object"),
        ("Alt and click a handle", "Remove that vertex"),
    ],
    "Viewing": [
        ("Wheel", "Zoom about the cursor"),
        ("Shift and wheel", "Scroll"),
        ("Middle drag", "Pan"),
        ("Space and drag", "Pan without changing tool"),
        ("Right drag", "Window and level, horizontal is width and vertical is centre"),
        ("Ctrl+0", "Fit to the window"),
        ("Ctrl+1", "Actual size"),
        ("Ctrl++", "Zoom in"),
        ("Ctrl+-", "Zoom out"),
        ("Ctrl+R", "Reset the view and display settings"),
        ("I", "Invert the displayed greyscale"),
        ("O", "Show the original pixels with no windowing or filter"),
        ("L", "Show or hide label codes"),
        ("C", "Crosshair"),
        ("M", "Magnifier"),
        ("Ctrl and wheel", "Change the brush size, while the brush is active"),
    ],
    "Workflow": [
        ("Ctrl+Enter", "Submit for review"),
        ("Ctrl+,", "Preferences"),
    ],
}


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.setMinimumSize(560, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        heading = QLabel("Keyboard shortcuts", self)
        heading.setProperty("subheading", True)
        layout.addWidget(heading)

        self.search = SearchBox("Find a shortcut", self)
        layout.addWidget(self.search)

        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Key", "Action"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(True)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.tree, 1)

        self._populate()

        note = QLabel(
            "Mouse actions are listed alongside the keys they pair with.",
            self,
        )
        note.setProperty("dim", True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Close, Qt.Horizontal, self)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.search.textChanged.connect(self._filter)

    def _populate(self) -> None:
        self.tree.clear()
        for group, entries in SHORTCUTS.items():
            parent = QTreeWidgetItem(self.tree, [group, ""])
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFirstColumnSpanned(True)
            parent.setExpanded(True)
            for key, action in entries:
                QTreeWidgetItem(parent, [key, action])

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            visible_children = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                matches = (
                    not needle
                    or needle in child.text(0).lower()
                    or needle in child.text(1).lower()
                )
                child.setHidden(not matches)
                visible_children += int(matches)
            parent.setHidden(visible_children == 0)
            parent.setExpanded(True)
