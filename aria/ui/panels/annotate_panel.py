"""The annotation module panel.

This is where an annotator spends the session, so the ordering follows the work
rather than the data model: which side, which structure, draw it, say what is
absent, grade the cortex, flag image problems, then check what still blocks
submission.

The label list shows completion state per side with a glyph as well as a
colour, so an annotator can see at a glance what is still outstanding without
relying on colour vision.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.schema import (
    LABEL_CLASSES,
    MEASUREMENT_CLASSES,
    LabelCategory,
    MCIGrade,
    Presence,
    QualityFlag,
    Side,
    get_class,
)
from ..icons import colour_swatch, icon as make_icon
from ..theme import PALETTE
from ..widgets.common import (
    Banner,
    CollapsibleSection,
    HLine,
    ScrollPanel,
    SectionLabel,
    StatusChip,
    make_button,
    make_tool_button,
)

#: Glyphs for per class completion state. Text, not colour, carries the meaning.
STATE_GLYPHS = {
    "done": ("●", PALETTE.success, "Recorded"),
    "absent": ("○", PALETTE.text_dim, "Recorded as absent or not assessable"),
    "missing": ("△", PALETTE.warning, "Required and not yet recorded"),
    "optional": ("·", PALETTE.text_disabled, "Optional and not recorded"),
}

CATEGORY_ORDER = [
    (LabelCategory.LANDMARK, "Landmarks"),
    (LabelCategory.CONTOUR, "Contours"),
    (LabelCategory.INDEX_LINE, "Index lines"),
    (LabelCategory.REGION, "Regions"),
    (LabelCategory.OPTIONAL, "Optional"),
]


class AnnotatePanel(QWidget):
    """Label selection, object management, grading, flags and submission checks."""

    class_activated = Signal(str, object)     # class key, Side
    focus_requested = Signal(str)             # annotation id
    tool_requested = Signal(str)

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.active_side = Side.RIGHT
        self.active_class = "mental_foramen_centre"
        self._building = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_case_section()
        self._build_side_section()
        self._build_labels_section()
        self._build_presence_section()
        self._build_objects_section()
        self._build_grading_section()
        self._build_flags_section()
        self._build_checks_section()

        self._connect()
        self.set_enabled_state(False)

    # -- construction --------------------------------------------------------

    def _build_case_section(self) -> None:
        self.case_section = CollapsibleSection("Case", self, True, "image")
        body = self.case_section.body_layout()

        self.case_name = QLabel("No case open", self)
        self.case_name.setProperty("subheading", True)
        body.addWidget(self.case_name)

        # The chips wrap onto a second line rather than clipping when the panel
        # is narrow, because each one carries information the annotator needs.
        chips = QGridLayout()
        chips.setSpacing(5)
        self.state_chip = StatusChip("", "neutral", self)
        self.laterality_chip = StatusChip("", "neutral", self)
        self.calibration_chip = StatusChip("", "neutral", self)
        chips.addWidget(self.state_chip, 0, 0)
        chips.addWidget(self.calibration_chip, 0, 1)
        chips.addWidget(self.laterality_chip, 1, 0, 1, 2)
        chips.setColumnStretch(2, 1)
        body.addLayout(chips)

        self.laterality_banner = Banner(self)
        self.laterality_banner.action_clicked.connect(self._confirm_laterality)
        body.addWidget(self.laterality_banner)

        self.scroll.add_section(self.case_section)

    def _build_side_section(self) -> None:
        self.side_section = CollapsibleSection("Side", self, True, "grid")
        body = self.side_section.body_layout()

        row = QHBoxLayout()
        row.setSpacing(6)
        self.side_group = QButtonGroup(self)
        self.side_buttons = {}
        for side, label, hint in (
            (Side.RIGHT, "Right", "Anatomical right, drawn with a solid stroke"),
            (Side.LEFT, "Left", "Anatomical left, drawn with a dashed stroke"),
            (Side.MIDLINE, "Midline", "Structures on the midline, such as menton"),
        ):
            button = QPushButton(label, self)
            button.setCheckable(True)
            button.setToolTip(hint)
            button.setMinimumHeight(30)
            self.side_group.addButton(button)
            self.side_buttons[side] = button
            row.addWidget(button)
        self.side_buttons[Side.RIGHT].setChecked(True)
        body.addLayout(row)

        note = QLabel(
            "Right is on the left of the image. Stroke pattern also shows the "
            "side: solid for right, dashed for left.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.scroll.add_section(self.side_section)

    def _build_labels_section(self) -> None:
        self.labels_section = CollapsibleSection("Labels", self, True, "point")
        body = self.labels_section.body_layout()

        self.label_tree = QTreeWidget(self)
        self.label_tree.setHeaderLabels(["Label", "R", "L"])
        self.label_tree.setRootIsDecorated(False)
        self.label_tree.setIndentation(12)
        self.label_tree.setAlternatingRowColors(True)
        self.label_tree.setUniformRowHeights(True)
        self.label_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.label_tree.setMinimumHeight(250)
        self.label_tree.setTextElideMode(Qt.ElideRight)
        header = self.label_tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.label_tree.setColumnWidth(1, 24)
        self.label_tree.setColumnWidth(2, 24)
        self.label_tree.setToolTip(
            "The two columns show whether each label is recorded on the right and "
            "the left side."
        )
        body.addWidget(self.label_tree)

        self.construct_button = make_button(
            "Construct from contours", "construct",
            tooltip=(
                "Compute this index line from the traced contours using the "
                "protocol geometry. The result is editable and must be reviewed."
            ),
            parent=self,
        )
        self.construct_button.setEnabled(False)
        body.addWidget(self.construct_button)

        self.scroll.add_section(self.labels_section)

    def _build_presence_section(self) -> None:
        self.presence_section = CollapsibleSection("Record as absent", self, False, "flag")
        body = self.presence_section.body_layout()

        hint = QLabel(
            "When a structure cannot be annotated, record why. Absence is stored "
            "explicitly and is never written as a zero coordinate.",
            self,
        )
        hint.setWordWrap(True)
        hint.setProperty("dim", True)
        body.addWidget(hint)

        self.presence_combo = QComboBox(self)
        for presence in (
            Presence.NOT_VISIBLE, Presence.NOT_ASSESSABLE,
            Presence.ABSENT_ANATOMY, Presence.UNCERTAIN,
        ):
            self.presence_combo.addItem(presence.display, presence.value)
        body.addWidget(self.presence_combo)

        self.presence_note = QLineEdit(self)
        self.presence_note.setPlaceholderText("Reason, optional but helpful to a reviewer")
        body.addWidget(self.presence_note)

        row = QHBoxLayout()
        self.presence_apply = make_button("Record for selected label", "check", parent=self)
        self.presence_clear = make_button("Mark as present", parent=self)
        row.addWidget(self.presence_apply)
        row.addWidget(self.presence_clear)
        body.addLayout(row)

        self.scroll.add_section(self.presence_section)

    def _build_objects_section(self) -> None:
        self.objects_section = CollapsibleSection("Objects on this case", self, True, "polyline")
        body = self.objects_section.body_layout()

        # Each row carries its own tick for whether the object is drawn. It
        # used to be reachable only by right clicking, which meant an annotator
        # with forty objects on an image had no way of seeing what could be
        # turned off, let alone of doing it quickly.
        hint = QLabel(
            "Untick an object to take it off the image. Nothing is deleted, "
            "and the tick comes back with the case.",
            self,
        )
        hint.setWordWrap(True)
        hint.setProperty("dim", True)
        body.addWidget(hint)

        show_row = QHBoxLayout()
        show_row.setSpacing(4)
        self.show_all_button = make_button("Show all", "visible", parent=self)
        self.hide_all_button = make_button("Hide all", "hidden", parent=self)
        self.isolate_button = make_button("Isolate selected", "cursor", parent=self)
        self.isolate_button.setToolTip(
            "Show only what is selected, and take everything else off the "
            "image. The quickest way to look at one structure on a crowded "
            "radiograph."
        )
        for b in (self.show_all_button, self.hide_all_button, self.isolate_button):
            show_row.addWidget(b)
        show_row.addStretch(1)
        body.addLayout(show_row)

        self._suppress_ticks = False
        #: Sides already moved to automatically on this case, so the
        #: move happens once rather than on every later edit.
        self._sides_advanced_from: set = set()
        # Grouped, with an eye on every group as well as every object. A
        # finished case carries both cortical borders, four index lines, a
        # dozen landmarks a side and a grading region, and the thing an
        # annotator wants is almost never one object: it is "take the index
        # lines off so I can see the border underneath".
        self.object_list = QTreeWidget(self)
        self.object_list.setColumnCount(2)
        self.object_list.setHeaderLabels(["", "Object"])
        self.object_list.setRootIsDecorated(True)
        self.object_list.setAlternatingRowColors(True)
        self.object_list.setMinimumHeight(180)
        self.object_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.object_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.object_list.setUniformRowHeights(True)
        # A tree indents column zero, so a child's eye would be pushed into the
        # next column and land on top of the name. The indent is kept small and
        # the column is wide enough to hold both it and the icon.
        self.object_list.setIndentation(12)
        header = self.object_list.header()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.object_list.setColumnWidth(0, 46)
        body.addWidget(self.object_list)

        row = QHBoxLayout()
        row.setSpacing(4)
        self.visibility_button = make_tool_button("visible", "Show or hide the selected objects", parent=self)
        self.lock_button = make_tool_button("lock", "Lock or unlock the selected objects", parent=self)
        self.copy_button = make_tool_button("copy", "Duplicate the selected object", parent=self)
        self.mirror_button = make_tool_button(
            "side_by_side",
            "Mirror the selected object to the other side as a starting position",
            parent=self,
        )
        self.delete_button = make_tool_button("delete", "Delete the selected objects", parent=self)
        for b in (
            self.visibility_button, self.lock_button, self.copy_button,
            self.mirror_button, self.delete_button,
        ):
            row.addWidget(b)
        row.addStretch(1)

        self.ambiguous_button = make_tool_button(
            "warning", "Flag the selected object as ambiguous", checkable=True, parent=self
        )
        row.addWidget(self.ambiguous_button)
        body.addLayout(row)

        visibility_row = QHBoxLayout()
        visibility_row.addWidget(QLabel("Visibility score", self))
        self.visibility_score = QSpinBox(self)
        self.visibility_score.setRange(-1, 4)
        self.visibility_score.setSpecialValueText("Not set")
        self.visibility_score.setValue(-1)
        self.visibility_score.setToolTip(
            "How clearly the structure is visible, from 0 for not visible to 4 "
            "for clearly defined."
        )
        visibility_row.addWidget(self.visibility_score)
        visibility_row.addStretch(1)
        body.addLayout(visibility_row)

        self.scroll.add_section(self.objects_section)

    def _build_grading_section(self) -> None:
        self.grading_section = CollapsibleSection("Cortical index grading", self, True, "grade")
        body = self.grading_section.body_layout()

        self.grade_buttons = {}
        for side in (Side.RIGHT, Side.LEFT):
            body.addWidget(SectionLabel(f"{side.display} side", self))
            row = QHBoxLayout()
            row.setSpacing(4)
            group = QButtonGroup(self)
            group.setExclusive(True)
            for grade in (
                MCIGrade.C1, MCIGrade.C2, MCIGrade.C3,
                MCIGrade.NOT_ASSESSABLE, MCIGrade.UNCERTAIN,
            ):
                text = {"not_assessable": "NA", "uncertain": "?"}.get(grade.value, grade.value)
                button = QPushButton(text, self)
                button.setCheckable(True)
                button.setMinimumHeight(28)
                button.setToolTip(f"{grade.display}. {grade.definition}")
                group.addButton(button)
                self.grade_buttons[(side, grade)] = button
                button.clicked.connect(
                    lambda _checked, s=side, g=grade: self._on_grade_clicked(s, g)
                )
                row.addWidget(button)
            body.addLayout(row)

        self.grade_rationale = QLineEdit(self)
        self.grade_rationale.setPlaceholderText("Reason, required when a grade is uncertain")
        body.addWidget(self.grade_rationale)

        definitions = QLabel(
            "C1 even and sharp endosteal margin.   "
            "C2 semilunar defects or one to three layers of residues.   "
            "C3 clearly porous with more than three layers.",
            self,
        )
        definitions.setWordWrap(True)
        definitions.setProperty("dim", True)
        body.addWidget(definitions)

        self.scroll.add_section(self.grading_section)

    def _build_flags_section(self) -> None:
        self.flags_section = CollapsibleSection("Image quality flags", self, False, "flag")
        body = self.flags_section.body_layout()

        self.flag_combo = QComboBox(self)
        for flag in QualityFlag:
            self.flag_combo.addItem(flag.display, flag.value)
        body.addWidget(self.flag_combo)

        self.flag_comment = QLineEdit(self)
        self.flag_comment.setPlaceholderText("Comment, required for Other")
        body.addWidget(self.flag_comment)

        row = QHBoxLayout()
        self.flag_add = make_button("Add flag", "check", parent=self)
        self.flag_remove = make_button("Remove selected", parent=self)
        row.addWidget(self.flag_add)
        row.addWidget(self.flag_remove)
        body.addLayout(row)

        self.flag_list = QListWidget(self)
        self.flag_list.setMaximumHeight(110)
        body.addWidget(self.flag_list)

        self.scroll.add_section(self.flags_section)

    def _build_checks_section(self) -> None:
        self.checks_section = CollapsibleSection("Submission checks", self, True, "check")
        body = self.checks_section.body_layout()

        self.checks_summary = QLabel("No case open", self)
        self.checks_summary.setWordWrap(True)
        body.addWidget(self.checks_summary)

        self.checks_list = QListWidget(self)
        self.checks_list.setMinimumHeight(120)
        self.checks_list.setWordWrap(True)
        body.addWidget(self.checks_list)

        self.submit_button = make_button("Submit for review", "submit", accent=True, parent=self)
        self.submit_button.setMinimumHeight(32)
        body.addWidget(self.submit_button)

        self.scroll.add_section(self.checks_section)

    # -- wiring --------------------------------------------------------------

    def _connect(self) -> None:
        c = self.controller
        c.case_opened.connect(self._on_case_opened)
        c.case_closed.connect(self._on_case_closed)
        c.annotations_changed.connect(self.refresh_labels)
        c.annotations_changed.connect(self.refresh_objects)
        # Checked after the lists have been rebuilt, so the move happens on a
        # view that already shows the side as finished.
        c.annotations_changed.connect(
            lambda: QTimer.singleShot(0, self.maybe_advance_side)
        )
        c.case_opened.connect(lambda _d: self._sides_advanced_from.clear())
        c.grades_changed.connect(self.refresh_grades)
        c.flags_changed.connect(self.refresh_flags)
        c.validation_changed.connect(self.refresh_checks)
        c.calibration_changed.connect(lambda _cal: self._refresh_chips())
        c.read_only_changed.connect(self._on_read_only)

        for side, button in self.side_buttons.items():
            button.clicked.connect(lambda _checked, s=side: self.set_side(s))

        self.label_tree.currentItemChanged.connect(self._on_label_selected)
        self.label_tree.itemDoubleClicked.connect(self._on_label_double_clicked)
        self.construct_button.clicked.connect(self._on_construct)

        self.presence_apply.clicked.connect(self._on_presence_apply)
        self.presence_clear.clicked.connect(self._on_presence_clear)

        self.object_list.itemSelectionChanged.connect(self._on_object_selection)
        self.object_list.itemDoubleClicked.connect(self._on_object_double_clicked)
        self.object_list.customContextMenuRequested.connect(self._on_object_context)
        self.object_list.itemClicked.connect(self._on_object_clicked)
        self.show_all_button.clicked.connect(lambda: self.set_all_visible(True))
        self.hide_all_button.clicked.connect(lambda: self.set_all_visible(False))
        self.isolate_button.clicked.connect(self.isolate_selected)
        self.visibility_button.clicked.connect(self._toggle_visibility)
        self.lock_button.clicked.connect(self._toggle_lock)
        self.copy_button.clicked.connect(lambda: self._duplicate(False))
        self.mirror_button.clicked.connect(lambda: self._duplicate(True))
        self.delete_button.clicked.connect(self._delete_selected)
        self.ambiguous_button.clicked.connect(self._toggle_ambiguous)
        self.visibility_score.valueChanged.connect(self._on_visibility_score)

        self.flag_add.clicked.connect(self._on_add_flag)
        self.flag_remove.clicked.connect(self._on_remove_flag)
        self.submit_button.clicked.connect(self._on_submit)

    # -- state ---------------------------------------------------------------

    def set_enabled_state(self, enabled: bool) -> None:
        for section in (
            self.side_section, self.labels_section, self.presence_section,
            self.objects_section, self.grading_section, self.flags_section,
            self.checks_section,
        ):
            section.setEnabled(enabled)

    def _on_case_opened(self, data) -> None:
        self.set_enabled_state(True)
        self.case_name.setText(data.case.pseudonym)
        self._refresh_chips()
        self.build_label_tree()
        self.refresh_objects()
        self.refresh_grades()
        self.refresh_flags()

    def _on_case_closed(self) -> None:
        self.set_enabled_state(False)
        self.case_name.setText("No case open")
        self.label_tree.clear()
        self.object_list.clear()
        self.flag_list.clear()
        self.checks_list.clear()
        self.checks_summary.setText("No case open")
        self.laterality_banner.clear()
        for chip in (self.state_chip, self.laterality_chip, self.calibration_chip):
            chip.set_state("", "neutral")

    def _on_read_only(self, read_only: bool, reason: str) -> None:
        for widget in (
            self.presence_section, self.grading_section, self.flags_section,
            self.construct_button, self.submit_button, self.delete_button,
            self.copy_button, self.mirror_button,
        ):
            widget.setEnabled(not read_only)
        if read_only and reason:
            self.laterality_banner.show_message(reason, "warn")

    def _refresh_chips(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        case = data.case
        state = case.state_enum
        self.state_chip.set_state(
            state.display,
            {"accepted": "ok", "adjudicated": "ok", "returned": "warn",
             "submitted": "info"}.get(state.value, "neutral"),
            f"Case state: {state.display}",
        )

        if case.laterality_confirmed:
            self.laterality_chip.set_state("Orientation confirmed", "ok", case.laterality_note)
            self.laterality_banner.clear()
        elif case.source.image_laterality:
            self.laterality_chip.set_state("Orientation unconfirmed", "warn")
            self.laterality_banner.show_message(
                f"The source reports laterality {case.source.image_laterality!r}. "
                f"Confirm anatomical right and left before submitting.",
                "warn", "Confirm",
            )
        else:
            self.laterality_chip.set_state("Orientation not set", "danger")
            self.laterality_banner.show_message(
                "This image does not carry laterality. Confirm that the patient's "
                "right is on the left of the image before submitting.",
                "danger", "Confirm",
            )

        cal = case.calibration
        if cal.millimetres_available:
            self.calibration_chip.set_state("Millimetres", "ok", cal.summary_line())
        elif cal.has_spacing:
            self.calibration_chip.set_state("Pixels only", "warn", cal.summary_line())
        else:
            self.calibration_chip.set_state("No calibration", "warn", cal.summary_line())

    def set_side(self, side: Side) -> None:
        self.active_side = side
        self.side_buttons[side].setChecked(True)
        self.class_activated.emit(self.active_class, side)
        self.refresh_labels()

    # -- label tree ----------------------------------------------------------

    def build_label_tree(self) -> None:
        self._building = True
        self.label_tree.clear()
        schema = self.controller.schema
        active = {c.key for c in schema.active_classes()}

        for category, title in CATEGORY_ORDER:
            classes = [
                c for c in LABEL_CLASSES
                if c.category is category and c.key in active
            ]
            if not classes:
                continue
            parent = QTreeWidgetItem(self.label_tree, [title, "", ""])
            font = QFont()
            font.setBold(True)
            parent.setFont(0, font)
            parent.setFlags(Qt.ItemIsEnabled)
            parent.setExpanded(True)

            for cls in classes:
                # The short code leads the row so related labels stay
                # distinguishable when the name is elided in a narrow panel,
                # and so the codes drawn on the image are learned in passing.
                item = QTreeWidgetItem(
                    parent, [f"{cls.short_code}   {cls.display_name}", "", ""]
                )
                item.setData(0, Qt.UserRole, cls.key)
                item.setIcon(0, make_icon("point", 12, cls.colour))
                required = schema.is_required(cls.key)
                tooltip = cls.description
                if cls.aliases:
                    tooltip += f"\n\nAlso known as: {', '.join(cls.aliases)}."
                if cls.requirements:
                    tooltip += f"\n\nSpecification: {', '.join(cls.requirements)}."
                if required:
                    tooltip += "\n\nRequired before submission."
                item.setToolTip(0, tooltip)
                if required:
                    font = QFont()
                    font.setBold(True)
                    item.setFont(0, font)
        self._building = False
        self.refresh_labels()
        self._select_class(self.active_class)

    def refresh_labels(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        schema = self.controller.schema

        root = self.label_tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                key = item.data(0, Qt.UserRole)
                if not key:
                    continue
                try:
                    cls = get_class(key)
                except KeyError:
                    continue
                required = schema.is_required(key)
                sides = (
                    (Side.RIGHT, 1), (Side.LEFT, 2)
                ) if cls.side_scoped else ((Side.MIDLINE, 1),)
                if not cls.side_scoped:
                    item.setText(2, "")
                for side, column in sides:
                    state = self._class_state(data, key, side, required)
                    glyph, colour, tip = STATE_GLYPHS[state]
                    item.setText(column, glyph)
                    item.setForeground(column, _brush(colour))
                    item.setTextAlignment(column, Qt.AlignCenter)
                    item.setToolTip(column, f"{side.display}: {tip}")

    @staticmethod
    def _class_state(data, key: str, side: Side, required: bool) -> str:
        cls = get_class(key)
        lookup_side = side if cls.side_scoped else None
        matches = data.by_class(key, lookup_side)
        if not matches:
            return "missing" if required else "optional"
        annotation = matches[0]
        if annotation.presence != Presence.PRESENT.value:
            return "absent"
        if not annotation.coordinates and not annotation.mask_rle:
            return "missing" if required else "optional"
        return "done"

    def side_is_complete(self, side: Side) -> bool:
        """True when every required, side scoped label on this side is done.

        Only side scoped classes count. The midline landmarks and the whole
        mandible outline belong to neither side, so waiting for them would mean
        a side was never finished.
        """
        data = self.controller.case_data
        if data is None:
            return False
        schema = self.controller.schema
        required = set(schema.required_classes)
        sided = [
            c for c in schema.active_classes()
            if c.side_scoped and c.key in required
        ]
        if not sided:
            return False
        return all(
            self._class_state(data, c.key, side, True) in ("done", "absent")
            for c in sided
        )

    def maybe_advance_side(self) -> None:
        """Move to the other side once this one is finished.

        Both sides are annotated on every case, so finishing the right and then
        having to remember to choose the left is a step that exists only
        because nobody removed it. Going back is one click, and the move is
        announced rather than silent.
        """
        if self.active_side not in (Side.RIGHT, Side.LEFT):
            return
        other = Side.LEFT if self.active_side is Side.RIGHT else Side.RIGHT
        if other in self._sides_advanced_from:
            # Only ever offered once per side per case, so that editing a
            # finished side does not keep throwing the person across the image.
            return
        if not self.side_is_complete(self.active_side) or self.side_is_complete(other):
            return

        finished = self.active_side
        self._sides_advanced_from.add(other)
        self.set_side(other)
        self.controller.status_message.emit(
            f"The {finished.display.lower()} side is complete. Moved to the "
            f"{other.display.lower()} side.",
            7000,
        )

    def _select_class(self, key: str) -> None:
        root = self.label_tree.invisibleRootItem()
        for i in range(root.childCount()):
            parent = root.child(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                if item.data(0, Qt.UserRole) == key:
                    self.label_tree.setCurrentItem(item)
                    return

    def _on_label_selected(self, current, _previous) -> None:
        if self._building or current is None:
            return
        key = current.data(0, Qt.UserRole)
        if not key:
            return
        self.active_class = key
        try:
            cls = get_class(key)
        except KeyError:
            return
        side = self.active_side if cls.side_scoped else Side.MIDLINE
        if not cls.side_scoped:
            self.side_buttons[Side.MIDLINE].setChecked(True)
        self.class_activated.emit(key, side)
        self.construct_button.setEnabled(
            key in (
                "mcw_line", "pmi_superior_line", "pmi_inferior_line",
                "antegonial_index_line", "gonial_index_line",
            )
            and not self.controller.read_only
        )

    def _on_label_double_clicked(self, item, _column) -> None:
        key = item.data(0, Qt.UserRole)
        if not key:
            return
        data = self.controller.case_data
        if data is None:
            return
        try:
            cls = get_class(key)
        except KeyError:
            return
        side = self.active_side if cls.side_scoped else None
        existing = data.present(key, side)
        if existing is not None:
            self.focus_requested.emit(existing.id)

    def _on_construct(self) -> None:
        self.controller.construct_index_line(self.active_class, self.active_side)

    # -- presence ------------------------------------------------------------

    def _on_presence_apply(self) -> None:
        presence = Presence(self.presence_combo.currentData())
        self.controller.set_presence(
            self.active_class, self.active_side, presence, self.presence_note.text().strip()
        )
        self.presence_note.clear()

    def _on_presence_clear(self) -> None:
        self.controller.set_presence(
            self.active_class, self.active_side, Presence.PRESENT, ""
        )

    # -- objects -------------------------------------------------------------

    #: Marks a row as a group rather than one object, so a click on the eye
    #: column knows whether it is toggling one thing or a whole category.
    GROUP_ROLE = Qt.UserRole + 1
    #: Whether a row's object is currently off the image, kept on the row
    #: rather than read back from the colour it happens to be painted in.
    HIDDEN_ROLE = Qt.UserRole + 2

    def refresh_objects(self) -> None:
        data = self.controller.case_data
        if data is None:
            self.object_list.clear()
            return

        selected = {
            item.data(0, Qt.UserRole) for item in self.object_list.selectedItems()
        }
        collapsed = {
            self.object_list.topLevelItem(i).text(1)
            for i in range(self.object_list.topLevelItemCount())
            if not self.object_list.topLevelItem(i).isExpanded()
        }

        self._suppress_ticks = True
        self.object_list.blockSignals(True)
        self.object_list.clear()

        grouped: dict = {}
        for annotation in sorted(
            data.live_annotations(), key=lambda a: (a.class_key, a.side)
        ):
            try:
                cls = get_class(annotation.class_key)
            except KeyError:
                continue
            grouped.setdefault(cls.category, []).append((annotation, cls))

        for category, title in CATEGORY_ORDER:
            entries = grouped.pop(category, [])
            if not entries:
                continue
            self._add_object_group(title, entries, selected, collapsed)

        # Anything whose category is not in the usual running order still has
        # to appear, or an object could be hidden from the one list that is
        # meant to show everything.
        for category, entries in grouped.items():
            title = getattr(category, "value", str(category)).replace("_", " ").capitalize()
            self._add_object_group(title, entries, selected, collapsed)

        self.object_list.blockSignals(False)
        self._suppress_ticks = False

    def _add_object_group(self, title, entries, selected, collapsed) -> None:
        hidden_count = sum(1 for a, _c in entries if a.hidden)
        group = QTreeWidgetItem(self.object_list, ["", f"{title}  ({len(entries)})"])
        group.setData(0, self.GROUP_ROLE, True)
        group.setFirstColumnSpanned(False)
        group.setIcon(0, self._eye_icon(hidden_count, len(entries)))
        group.setToolTip(
            0,
            "Show or hide every object in this group."
            if hidden_count != len(entries) else
            "Bring this whole group back onto the image.",
        )
        font = group.font(1)
        font.setBold(True)
        group.setFont(1, font)
        group.setExpanded(f"{title}  ({len(entries)})" not in collapsed)

        for annotation, cls in entries:
            side = Side(annotation.side)
            marks = []
            if annotation.locked:
                marks.append("locked")
            if annotation.ambiguous:
                marks.append("ambiguous")
            if annotation.presence != Presence.PRESENT.value:
                marks.append(Presence(annotation.presence).display.lower())
            origin = annotation.properties.get("origin")
            if origin == "geometric_construction":
                marks.append("constructed")
            elif origin == "mirrored":
                marks.append("mirrored")

            suffix = f"   [{', '.join(marks)}]" if marks else ""
            label = f"{cls.short_code} {side.value}   {cls.display_name}{suffix}"
            value = self.controller.measurement_text(annotation, short=True)                 if cls.key in MEASUREMENT_CLASSES else ""
            if value:
                label = f"{label}   {value}"

            row = QTreeWidgetItem(group, ["", label])
            row.setData(0, Qt.UserRole, annotation.id)
            row.setData(0, self.GROUP_ROLE, False)
            row.setData(0, self.HIDDEN_ROLE, bool(annotation.hidden))
            row.setIcon(0, self._eye_icon(1 if annotation.hidden else 0, 1))
            row.setIcon(1, QIcon(colour_swatch(cls.colour, 12, side is Side.LEFT)))
            row.setToolTip(
                0,
                "Hidden. Click to bring it back onto the image."
                if annotation.hidden else
                "On the image. Click to take it off.",
            )
            detail = [
                cls.display_name,
                f"Side: {side.display}",
                f"Points: {len(annotation.points())}",
                f"Revision: {annotation.revision}",
            ]
            if value:
                detail.append(f"Measures: {value}")
            if annotation.notes:
                detail.append(annotation.notes)
            row.setToolTip(1, "\n".join(detail))
            if annotation.id in selected:
                row.setSelected(True)
            if annotation.hidden:
                row.setForeground(1, _brush(PALETTE.text_disabled))

    @staticmethod
    def _eye_icon(hidden: int, total: int):
        """An open eye, a crossed one, or a dimmed eye for a mixed group."""
        if hidden == 0:
            return make_icon("visible", 14, PALETTE.text)
        if hidden >= total:
            return make_icon("hidden", 14, PALETTE.text_disabled)
        return make_icon("visible", 14, PALETTE.text_dim)

    def _on_object_clicked(self, item, column: int) -> None:
        """A click in the eye column shows or hides, anywhere else selects."""
        if column != 0 or self._suppress_ticks:
            return
        if item.data(0, self.GROUP_ROLE):
            ids, hidden = [], 0
            for i in range(item.childCount()):
                child = item.child(i)
                ids.append(child.data(0, Qt.UserRole))
                hidden += 1 if child.data(0, self.HIDDEN_ROLE) else 0
            # Showing wins on a mixed group: the common intent is to get
            # everything back, and hiding again is one more click.
            target_hidden = hidden == 0
            QTimer.singleShot(
                0, lambda: self._set_hidden_for(ids, target_hidden)
            )
            return

        annotation_id = item.data(0, Qt.UserRole)
        if not annotation_id:
            return
        current = self.controller.case_data
        if current is None:
            return
        annotation = next(
            (a for a in current.live_annotations() if a.id == annotation_id), None
        )
        if annotation is None:
            return
        hidden = not annotation.hidden
        QTimer.singleShot(
            0, lambda: self._set_hidden_for([annotation_id], hidden)
        )

    def _set_hidden_for(self, ids, hidden: bool) -> None:
        for annotation_id in ids:
            if annotation_id:
                self.controller.set_annotation_flags(annotation_id, hidden=hidden)
        self.refresh_objects()

    def set_all_visible(self, visible: bool) -> None:
        """Put everything back on the image, or take it all off."""
        data = self.controller.case_data
        if data is None:
            return
        changed = 0
        for annotation in data.live_annotations():
            if annotation.hidden == (not visible):
                continue
            self.controller.set_annotation_flags(annotation.id, hidden=not visible)
            changed += 1
        self.refresh_objects()
        self.controller.status_message.emit(
            f"{changed} objects {'shown' if visible else 'hidden'}."
            if changed else
            f"Everything is already {'shown' if visible else 'hidden'}.",
            4000,
        )

    def isolate_selected(self) -> None:
        """Show only what is selected.

        A radiograph with both borders, both foramina, four index lines and a
        grading region on it is unreadable. This is the fastest way back to one
        structure, and Show all is next to it.
        """
        data = self.controller.case_data
        if data is None:
            return
        chosen = set(self._selected_annotation_ids())
        if not chosen:
            self.controller.status_message.emit(
                "Select the objects to isolate first.", 5000
            )
            return
        for annotation in data.live_annotations():
            hidden = annotation.id not in chosen
            if annotation.hidden != hidden:
                self.controller.set_annotation_flags(annotation.id, hidden=hidden)
        self.refresh_objects()
        self.controller.status_message.emit(
            f"Showing {len(chosen)} of {len(data.live_annotations())} objects. "
            f"Show all brings the rest back.",
            6000,
        )

    def _selected_annotation_ids(self) -> list:
        # Group rows carry no identifier, so only the object rows answer.
        return [
            item.data(0, Qt.UserRole)
            for item in self.object_list.selectedItems()
            if item.data(0, Qt.UserRole)
        ]

    def _on_object_selection(self) -> None:
        ids = self._selected_annotation_ids()
        if len(ids) == 1:
            annotation = self.controller._find(ids[0])
            if annotation is not None:
                self.ambiguous_button.setChecked(annotation.ambiguous)
                self.visibility_score.blockSignals(True)
                self.visibility_score.setValue(
                    annotation.visibility_score if annotation.visibility_score is not None else -1
                )
                self.visibility_score.blockSignals(False)
        if ids:
            self.focus_requested.emit(ids[0])

    def _on_object_double_clicked(self, item, column: int = 0) -> None:
        annotation_id = item.data(0, Qt.UserRole)
        if annotation_id:
            self.focus_requested.emit(annotation_id)

    def _on_object_context(self, position) -> None:
        item = self.object_list.itemAt(position)
        if item is None or not item.data(0, Qt.UserRole):
            return
        annotation_id = item.data(0, Qt.UserRole)
        annotation = self.controller._find(annotation_id)
        if annotation is None:
            return

        menu = QMenu(self)
        menu.addAction("Zoom to this object", lambda: self.focus_requested.emit(annotation_id))
        menu.addSeparator()
        menu.addAction(
            "Unhide" if annotation.hidden else "Hide",
            lambda: self.controller.set_annotation_flags(annotation_id, hidden=not annotation.hidden),
        )
        menu.addAction(
            "Unlock" if annotation.locked else "Lock",
            lambda: self.controller.set_annotation_flags(annotation_id, locked=not annotation.locked),
        )
        menu.addAction(
            "Clear ambiguous flag" if annotation.ambiguous else "Flag as ambiguous",
            lambda: self.controller.set_annotation_flags(
                annotation_id, ambiguous=not annotation.ambiguous
            ),
        )
        menu.addSeparator()
        menu.addAction("Duplicate", lambda: self.controller.duplicate_annotation(annotation_id, False))
        menu.addAction(
            "Mirror to other side",
            lambda: self.controller.duplicate_annotation(annotation_id, True),
        )
        menu.addSeparator()
        menu.addAction("Delete", lambda: self.controller.delete_annotation(annotation_id))
        menu.exec(self.object_list.mapToGlobal(position))

    def _toggle_visibility(self) -> None:
        for annotation_id in self._selected_annotation_ids():
            annotation = self.controller._find(annotation_id)
            if annotation is not None:
                self.controller.set_annotation_flags(annotation_id, hidden=not annotation.hidden)

    def _toggle_lock(self) -> None:
        for annotation_id in self._selected_annotation_ids():
            annotation = self.controller._find(annotation_id)
            if annotation is not None:
                self.controller.set_annotation_flags(annotation_id, locked=not annotation.locked)

    def _toggle_ambiguous(self) -> None:
        checked = self.ambiguous_button.isChecked()
        for annotation_id in self._selected_annotation_ids():
            self.controller.set_annotation_flags(annotation_id, ambiguous=checked)

    def _on_visibility_score(self, value: int) -> None:
        if value < 0:
            return
        for annotation_id in self._selected_annotation_ids():
            self.controller.set_annotation_flags(annotation_id, visibility_score=value)

    def _duplicate(self, mirror: bool) -> None:
        ids = self._selected_annotation_ids()
        if not ids:
            self.controller.status_message.emit("Select an object first.", 3000)
            return
        self.controller.duplicate_annotation(ids[0], mirror)

    def _delete_selected(self) -> None:
        ids = self._selected_annotation_ids()
        if not ids:
            return
        from PySide6.QtWidgets import QMessageBox

        if len(ids) > 1:
            answer = QMessageBox.question(
                self, "Delete objects",
                f"Delete {len(ids)} annotation objects?\n\n"
                f"They can be restored with undo.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        for annotation_id in ids:
            self.controller.delete_annotation(annotation_id)

    # -- grading -------------------------------------------------------------

    def refresh_grades(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        for (side, grade), button in self.grade_buttons.items():
            label = data.grade("mci_grade", side)
            button.setChecked(bool(label and label.value == grade.value))
        for side in (Side.RIGHT, Side.LEFT):
            label = data.grade("mci_grade", side)
            if label and label.rationale:
                self.grade_rationale.setText(label.rationale)
                break

    def _on_grade_clicked(self, side: Side, grade: MCIGrade) -> None:
        rationale = self.grade_rationale.text().strip()
        if grade is MCIGrade.UNCERTAIN and not rationale:
            self.controller.status_message.emit(
                "Add a reason before recording an uncertain grade.", 5000
            )
        self.controller.set_grade(side, grade, rationale)

    # -- flags ---------------------------------------------------------------

    def refresh_flags(self) -> None:
        data = self.controller.case_data
        self.flag_list.clear()
        if data is None:
            return
        for flag in data.quality_flags:
            try:
                display = QualityFlag(flag.flag).display
            except ValueError:
                display = flag.flag
            text = display
            if flag.side and flag.side != Side.NONE.value:
                text += f" ({Side(flag.side).display})"
            if flag.comment:
                text += f": {flag.comment}"
            item = QListWidgetItem(text, self.flag_list)
            item.setData(Qt.UserRole, flag.id)
            item.setIcon(make_icon("flag", 12, PALETTE.warning))

    def _on_add_flag(self) -> None:
        flag = self.flag_combo.currentData()
        comment = self.flag_comment.text().strip()
        if flag == QualityFlag.OTHER.value and not comment:
            self.controller.status_message.emit(
                "The flag Other needs a comment describing what was seen.", 5000
            )
            return
        if self.controller.add_quality_flag(flag, comment, self.active_side):
            self.flag_comment.clear()

    def _on_remove_flag(self) -> None:
        for item in self.flag_list.selectedItems():
            self.controller.remove_quality_flag(item.data(Qt.UserRole))

    # -- checks --------------------------------------------------------------

    def refresh_checks(self, result) -> None:
        self.checks_list.clear()
        if result is None:
            self.checks_summary.setText("No case open")
            self.submit_button.setEnabled(False)
            return

        self.checks_summary.setText(result.summary())
        self.checks_summary.setProperty(
            "status", "ok" if result.can_submit and not result.warnings
            else "warn" if result.can_submit else "danger"
        )
        self.checks_summary.style().unpolish(self.checks_summary)
        self.checks_summary.style().polish(self.checks_summary)

        for issue in result.blockers + result.warnings + result.infos:
            glyph = issue.severity_enum.glyph
            item = QListWidgetItem(f"{glyph}  {issue.label()}", self.checks_list)
            item.setToolTip(
                f"{issue.message}\n\nWhat to do: {issue.remedy}"
                + (f"\n\nSpecification: {issue.requirement}" if issue.requirement else "")
            )
            colour = {
                "blocker": PALETTE.danger, "warning": PALETTE.warning,
            }.get(issue.severity, PALETTE.text_dim)
            item.setForeground(_brush(colour))
            item.setData(Qt.UserRole, issue.class_key)

        self.checks_section.set_badge(
            f"{len(result.blockers)} blocking" if result.blockers else "ready"
        )
        self.submit_button.setEnabled(result.can_submit and not self.controller.read_only)

    def _on_submit(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        note, ok = QInputDialog.getText(
            self, "Submit for review",
            "Add a note for the reviewer, optional:", QLineEdit.Normal, "",
        )
        if not ok:
            return
        self.controller.submit(note.strip())

    def _confirm_laterality(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        note, ok = QInputDialog.getText(
            self, "Confirm orientation",
            "Confirm that the patient's anatomical right is displayed on the "
            "left of the image.\n\nNote, optional:",
            QLineEdit.Normal, "",
        )
        if ok:
            self.controller.confirm_laterality(note.strip())
            self._refresh_chips()


def _brush(colour: str):
    from PySide6.QtGui import QBrush, QColor

    return QBrush(QColor(colour))
