"""The case browser: the list of cases in a project and how to get into one.

Cases are shown with their workflow state as a glyph and a word, never as a
colour alone. The filters match how the work is actually divided: by state, by
who it is assigned to, and by dataset split.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.schema import CaseState
from ..icons import icon as make_icon
from ..theme import PALETTE
from ..widgets.common import SearchBox, StatusChip, make_button, make_tool_button

STATE_LEVEL = {
    CaseState.UNASSIGNED: "neutral",
    CaseState.ASSIGNED: "info",
    CaseState.IN_PROGRESS: "info",
    CaseState.SUBMITTED: "warn",
    CaseState.RETURNED: "warn",
    CaseState.ACCEPTED: "ok",
    CaseState.ADJUDICATED: "ok",
}


class CaseBrowser(QWidget):
    """Lists cases and opens them."""

    case_open_requested = Signal(str, bool)      # case id, read only
    import_requested = Signal()

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._thumbnail_cache: dict = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)

        # Project selector.
        project_row = QHBoxLayout()
        project_row.setSpacing(6)
        project_row.addWidget(QLabel("Project", self))
        self.project_combo = QComboBox(self)
        self.project_combo.setMinimumWidth(140)
        project_row.addWidget(self.project_combo, 1)
        self.refresh_button = make_tool_button("refresh", "Reload the case list", parent=self)
        project_row.addWidget(self.refresh_button)
        layout.addLayout(project_row)

        # Filters.
        self.search = SearchBox("Find a case by pseudonym", self)
        layout.addWidget(self.search)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(6)
        self.state_filter = QComboBox(self)
        self.state_filter.addItem("All states", "")
        for state in CaseState:
            self.state_filter.addItem(f"{state.glyph}  {state.display}", state.value)
        filter_row.addWidget(self.state_filter, 1)

        self.assignment_filter = QComboBox(self)
        self.assignment_filter.addItem("Anyone", "")
        self.assignment_filter.addItem("Assigned to me", "me")
        self.assignment_filter.addItem("Unassigned", "none")
        filter_row.addWidget(self.assignment_filter, 1)
        layout.addLayout(filter_row)

        # The list.
        self.tree = QTreeWidget(self)
        self.tree.setHeaderLabels(["Case", "State", "Calibration"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIconSize(QSize(64, 34))
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        # Summary and actions.
        self.summary = QLabel("", self)
        self.summary.setProperty("dim", True)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        self.open_button = make_button("Open", "image", accent=True, parent=self)
        self.open_read_only_button = make_button("Open read only", "visible", parent=self)
        self.import_button = make_button("Import", "import", parent=self)
        actions.addWidget(self.open_button)
        actions.addWidget(self.open_read_only_button)
        actions.addWidget(self.import_button)
        layout.addLayout(actions)

        self._connect()

    def _connect(self) -> None:
        self.project_combo.currentIndexChanged.connect(self._on_project_changed)
        self.refresh_button.clicked.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.state_filter.currentIndexChanged.connect(self.refresh)
        self.assignment_filter.currentIndexChanged.connect(self.refresh)
        self.tree.itemDoubleClicked.connect(self._on_double_click)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.open_button.clicked.connect(lambda: self._open(False))
        self.open_read_only_button.clicked.connect(lambda: self._open(True))
        self.import_button.clicked.connect(self.import_requested)
        self.tree.currentItemChanged.connect(lambda *_: self._update_actions())
        self.tree.itemSelectionChanged.connect(self._update_actions)
        self.controller.case_list_changed.connect(self.refresh)
        self._update_actions()

    # -- projects ------------------------------------------------------------

    def reload_projects(self) -> None:
        """Rebuild the selector so that it agrees with the open project.

        The list below the selector is drawn from the controller, so the
        selector has to follow the controller rather than its own previous
        value. Taking the previous value as the truth let the two drift apart:
        the selector named one project while the list underneath it held
        another project's cases, and nothing on screen said which one a newly
        imported case had gone into.
        """
        projects = self.controller.repo.list_projects()
        open_project = self.controller.project

        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project in projects:
            self.project_combo.addItem(project.name, project.id)

        index = (
            self.project_combo.findData(open_project.id)
            if open_project is not None
            else -1
        )
        if index < 0 and projects:
            # Either nothing was open, or what was open has since been archived
            # or removed. Either way the selector falls back to the first entry
            # and the controller is moved to match it below.
            index = 0
        self.project_combo.setCurrentIndex(index)
        self.project_combo.blockSignals(False)

        chosen = self.project_combo.currentData()
        if chosen:
            if open_project is None or open_project.id != chosen:
                project = self.controller.repo.get_project(chosen)
                if project is not None:
                    # This emits case_list_changed, which redraws the list.
                    self.controller.set_project(project)
                    return
        elif open_project is not None:
            # The last project was archived while it was the one open. The
            # selector has nothing left to name, so the controller cannot go on
            # holding a project it no longer offers.
            self.controller.set_project(None)
            return
        self.refresh()

    def _on_project_changed(self, _index: int) -> None:
        project_id = self.project_combo.currentData()
        if not project_id:
            return
        project = self.controller.repo.get_project(project_id)
        if project is not None:
            self.controller.set_project(project)
        self.refresh()

    # -- listing -------------------------------------------------------------

    def refresh(self) -> None:
        project = self.controller.project
        # A refresh rebuilds every row, which would otherwise drop the current
        # item and leave Open with nothing to act on. The selection is carried
        # across the rebuild instead.
        previous = self.selected_case_id()
        self.tree.clear()
        if project is None:
            self.summary.setText("No project selected.")
            self._update_actions()
            return

        assignment = self.assignment_filter.currentData()
        assigned_to = ""
        if assignment == "me" and self.controller.user is not None:
            assigned_to = self.controller.user.id

        cases = self.controller.repo.list_cases(
            project_id=project.id,
            state=self.state_filter.currentData() or "",
            assigned_to=assigned_to,
            search=self.search.text().strip(),
        )
        if assignment == "none":
            cases = [c for c in cases if not c.assigned_to]

        users = {u.id: u for u in self.controller.repo.list_users(include_inactive=True)}

        for case in cases:
            state = case.state_enum
            item = QTreeWidgetItem(self.tree)
            item.setText(0, case.pseudonym)
            item.setData(0, Qt.UserRole, case.id)
            item.setText(1, f"{state.glyph}  {state.display}")

            cal = case.calibration
            if cal.millimetres_available:
                item.setText(2, "mm")
                item.setToolTip(2, cal.summary_line())
            elif cal.has_spacing:
                item.setText(2, "px")
                item.setToolTip(
                    2,
                    cal.summary_line()
                    + "\n\nMillimetre values are withheld until the calibration is validated.",
                )
            else:
                item.setText(2, "px")
                item.setToolTip(2, "No spatial calibration. Pixel measurements only.")

            thumbnail = self._thumbnail(case.id)
            if thumbnail is not None:
                item.setIcon(0, thumbnail)

            assignee = users.get(case.assigned_to)
            detail = [
                f"{case.pseudonym}",
                f"State: {state.display}",
                f"Source: {case.source.source_format.upper()}, "
                f"{case.source.columns} by {case.source.rows} pixels",
                f"Assigned to: {assignee.display_name if assignee else 'nobody'}",
                f"Imported: {case.imported_at[:19].replace('T', ' ')}",
            ]
            if case.split:
                detail.append(f"Split: {case.split}")
            if not case.laterality_confirmed:
                detail.append("Orientation not yet confirmed")
            item.setToolTip(0, "\n".join(detail))

        counts = self.controller.repo.count_cases(project.id)
        total = sum(counts.values())
        parts = [f"{total} cases"]
        for state in CaseState:
            n = counts.get(state.value, 0)
            if n:
                parts.append(f"{n} {state.display.lower()}")
        line = ", ".join(parts) + f".   Showing {len(cases)}."
        if not cases and total:
            line += "   No case matches the current filters."
        self.summary.setText(line)

        # Leave a case selected, so Open is immediately meaningful. Without
        # this, the list after an import has no current row and Open does
        # nothing at all.
        if not self.select_case(previous) and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._update_actions()

    def _thumbnail(self, case_id: str) -> QIcon | None:
        if case_id in self._thumbnail_cache:
            return self._thumbnail_cache[case_id]
        path = self.controller.paths.thumbnail_path(case_id)
        result = None
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                result = QIcon(
                    pixmap.scaled(
                        QSize(64, 34), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                )
        self._thumbnail_cache[case_id] = result
        return result

    # -- actions -------------------------------------------------------------

    def selected_case_id(self) -> str:
        item = self.tree.currentItem()
        return item.data(0, Qt.UserRole) if item else ""

    def select_case(self, case_id: str) -> bool:
        """Make ``case_id`` the current row. Returns whether it is listed."""
        if not case_id:
            return False
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == case_id:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                self._update_actions()
                return True
        return False

    def _update_actions(self) -> None:
        """An action that cannot do anything is disabled rather than silent."""
        has_selection = bool(self.selected_case_id())
        self.open_button.setEnabled(has_selection)
        self.open_read_only_button.setEnabled(has_selection)
        hint = (
            "Open the selected case for annotation."
            if has_selection
            else "Select a case in the list first."
        )
        self.open_button.setToolTip(hint)
        self.open_read_only_button.setToolTip(
            "Open the selected case without taking the edit lock."
            if has_selection else hint
        )

    def _open(self, read_only: bool) -> None:
        case_id = self.selected_case_id()
        if not case_id:
            self.controller.status_message.emit(
                "Select a case in the list before opening it.", 5000
            )
            return
        self.case_open_requested.emit(case_id, read_only)

    def _on_double_click(self, item, _column) -> None:
        self.case_open_requested.emit(item.data(0, Qt.UserRole), False)

    def _on_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        case_id = item.data(0, Qt.UserRole)
        case = self.controller.repo.get_case(case_id)
        if case is None:
            return

        menu = QMenu(self)
        menu.addAction("Open", lambda: self.case_open_requested.emit(case_id, False))
        menu.addAction("Open read only", lambda: self.case_open_requested.emit(case_id, True))
        menu.addSeparator()

        from ...security.auth import Permission

        if self.controller.can(Permission.ASSIGN_CASES):
            assign_menu = menu.addMenu("Assign to")
            assign_menu.addAction("Nobody", lambda: self._assign(case_id, None))
            assign_menu.addSeparator()
            for user in self.controller.repo.list_users():
                assign_menu.addAction(
                    f"{user.display_name} ({user.role_display})",
                    lambda _checked=False, u=user.id: self._assign(case_id, u),
                )

            split_menu = menu.addMenu("Dataset split")
            for split in ("", "train", "validation", "test", "calibration"):
                label = split or "Not assigned"
                split_menu.addAction(
                    label, lambda _checked=False, s=split: self._set_split(case_id, s)
                )

            menu.addAction(
                "Clear duplicate annotation flag" if case.duplicate_target
                else "Mark for duplicate annotation",
                lambda: self._toggle_duplicate(case_id, not case.duplicate_target),
            )

        menu.addSeparator()
        menu.addAction("Check source file integrity", lambda: self._verify(case_id))
        menu.exec(self.tree.mapToGlobal(position))

    def _assign(self, case_id: str, user_id) -> None:
        self.controller.repo.assign_case(case_id, user_id)
        self.refresh()

    def _set_split(self, case_id: str, split: str) -> None:
        self.controller.repo.set_case_split(case_id, split)
        self.refresh()

    def _toggle_duplicate(self, case_id: str, value: bool) -> None:
        self.controller.repo.set_duplicate_target(case_id, value)
        self.refresh()

    def _verify(self, case_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        case = self.controller.repo.get_case(case_id)
        if case is None:
            return
        result = self.controller.importer().verify_case_integrity(case)
        if result["ok"]:
            QMessageBox.information(
                self, "Source file verified",
                f"{case.pseudonym}\n\n{result['message']}",
            )
        else:
            QMessageBox.warning(
                self, "Source file problem",
                f"{case.pseudonym}\n\n{result['message']}\n\n"
                f"Annotations on this case were made against the original pixels. "
                f"Restore the file from backup before annotating further.",
            )
