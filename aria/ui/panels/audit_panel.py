"""The audit module: read the immutable history.

An auditor reads this without being able to edit clinical content. Coordinate
lists are replaced by counts and extents in the detail view, so the shape of a
change is visible without exposing the annotation itself.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.audit import AuditEvent, redact_for_auditor
from ...core.models import Role
from ...security.auth import Permission
from ..theme import PALETTE
from ..widgets.common import (
    CollapsibleSection,
    KeyValueGrid,
    ScrollPanel,
    SearchBox,
    StatusChip,
    make_button,
)

PAGE_SIZE = 300

#: Events grouped for the filter, so a reader can ask a question rather than
#: pick from forty event names.
EVENT_GROUPS = {
    "": "Every event",
    "session": "Sign in and sessions",
    "data": "Import and cases",
    "annotation": "Annotation changes",
    "workflow": "Submission and review",
    "export": "Exports",
    "administration": "Administration",
}

GROUP_EVENTS = {
    "session": {
        AuditEvent.LOGIN_SUCCESS, AuditEvent.LOGIN_FAILURE, AuditEvent.LOGOUT,
        AuditEvent.SESSION_TIMEOUT, AuditEvent.PASSWORD_CHANGED,
    },
    "data": {
        AuditEvent.IMPORT_STARTED, AuditEvent.IMPORT_COMPLETED,
        AuditEvent.IMPORT_REJECTED, AuditEvent.DEIDENTIFY_APPLIED,
        AuditEvent.CASE_VIEWED, AuditEvent.CASE_ASSIGNED,
        AuditEvent.CASE_UNASSIGNED, AuditEvent.CASE_STATE_CHANGED,
        AuditEvent.CASE_ARCHIVED,
    },
    "annotation": {
        AuditEvent.ANNOTATION_CREATED, AuditEvent.ANNOTATION_UPDATED,
        AuditEvent.ANNOTATION_DELETED, AuditEvent.ANNOTATION_RESTORED,
        AuditEvent.PRESENCE_CHANGED, AuditEvent.GRADE_ASSIGNED,
        AuditEvent.QUALITY_FLAG_ADDED, AuditEvent.QUALITY_FLAG_REMOVED,
        AuditEvent.CALIBRATION_SET, AuditEvent.CALIBRATION_VALIDATED,
        AuditEvent.CALIBRATION_REJECTED, AuditEvent.LATERALITY_CONFIRMED,
    },
    "workflow": {
        AuditEvent.SUBMITTED, AuditEvent.REVIEW_STARTED, AuditEvent.REVIEW_COMMENT,
        AuditEvent.ACCEPTED, AuditEvent.RETURNED, AuditEvent.ADJUDICATED,
        AuditEvent.REVISION_CREATED,
    },
    "export": {
        AuditEvent.EXPORT_STARTED, AuditEvent.EXPORT_COMPLETED,
        AuditEvent.EXPORT_FAILED, AuditEvent.BUNDLE_CREATED,
    },
    "administration": {
        AuditEvent.USER_CREATED, AuditEvent.USER_UPDATED,
        AuditEvent.USER_DEACTIVATED, AuditEvent.ROLE_CHANGED,
        AuditEvent.PROJECT_CREATED, AuditEvent.PROJECT_UPDATED,
        AuditEvent.SCHEMA_CHANGED, AuditEvent.THRESHOLD_CHANGED,
        AuditEvent.DEID_PROFILE_CHANGED, AuditEvent.POLICY_CHANGED,
        AuditEvent.CALIBRATION_SET_APPROVED, AuditEvent.DIAGNOSTICS_RUN,
        AuditEvent.DATABASE_MIGRATED, AuditEvent.INTEGRITY_CHECK,
    },
}


class AuditPanel(QWidget):
    """A filterable view of the audit history, with chain verification."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._offset = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_integrity_section()
        self._build_history_section()
        self._build_detail_section()

    # -- integrity -----------------------------------------------------------

    def _build_integrity_section(self) -> None:
        self.integrity_section = CollapsibleSection("Integrity", self, True, "lock")
        body = self.integrity_section.body_layout()

        note = QLabel(
            "Every record carries the digest of the record before it. Recomputing "
            "the chain detects an altered or removed record anywhere in the "
            "history.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.integrity_chip = StatusChip("Not checked", "neutral", self)
        body.addWidget(self.integrity_chip)

        self.integrity_details = KeyValueGrid(self)
        body.addWidget(self.integrity_details)

        row = QHBoxLayout()
        self.verify_button = make_button("Verify the chain", "check", parent=self)
        self.export_button = make_button("Export history", "export", parent=self)
        row.addWidget(self.verify_button)
        row.addWidget(self.export_button)
        body.addLayout(row)

        self.verify_button.clicked.connect(self.verify)
        self.export_button.clicked.connect(self.export_history)

        self.scroll.add_section(self.integrity_section)

    def verify(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = self.controller.repo.verify_audit_chain()
        finally:
            QApplication.restoreOverrideCursor()

        if result["valid"]:
            self.integrity_chip.set_state(
                f"Intact, {result['records_checked']} records", "ok", result["reason"]
            )
            self.integrity_details.set(
                "head", "Head digest", result.get("head_hash", "")[:32], mono=True
            )
        else:
            self.integrity_chip.set_state("Chain broken", "danger", result["reason"])
            self.integrity_details.set(
                "break", "Breaks at record", str(result.get("broken_at_sequence", "")), "danger"
            )
            self.integrity_details.set("reason", "Reason", result["reason"], "danger")
            QMessageBox.critical(
                self, "The audit history is not intact",
                f"{result['reason']}\n\nThe database may have been modified outside "
                f"the application. Report this to the project administrator and "
                f"restore from a verified backup.",
            )
        self.integrity_details.set(
            "count", "Records", str(self.controller.repo.audit_count())
        )

    def export_history(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(
            self, "Export audit history",
            str(self.controller.paths.exports_dir / "aria_audit_history.json"),
            "JSON (*.json)",
        )
        if not path:
            return
        records = self.controller.repo.audit_records(limit=1_000_000)
        auditor = (
            self.controller.user is not None
            and self.controller.user.role == Role.AUDITOR
        )
        payload = {
            "exported_at": __import__("aria.core.models", fromlist=["utc_now"]).utc_now(),
            "n_records": len(records),
            "chain_verification": self.controller.repo.verify_audit_chain(),
            "redacted_for_auditor": auditor,
            "records": [
                {
                    "sequence": r.sequence, "timestamp": r.timestamp,
                    "actor": r.actor_name, "event": r.event,
                    "object_type": r.object_type, "object_id": r.object_id,
                    "case_id": r.case_id, "detail": r.detail,
                    "before": _payload(r.before_json, auditor),
                    "after": _payload(r.after_json, auditor),
                    "record_hash": r.record_hash,
                    "previous_hash": r.previous_hash,
                }
                for r in reversed(records)
            ],
        }
        from ...io.fsutil import atomic_write_text

        atomic_write_text(path, json.dumps(payload, indent=2, default=str))
        QMessageBox.information(
            self, "History exported",
            f"{len(records)} records written to:\n\n{path}\n\n"
            f"The chain verification result is included so a reader can confirm "
            f"the history was intact when it was exported.",
        )

    # -- history -------------------------------------------------------------

    def _build_history_section(self) -> None:
        self.history_section = CollapsibleSection("History", self, True, "audit")
        body = self.history_section.body_layout()

        filter_row = QHBoxLayout()
        self.group_filter = QComboBox(self)
        for key, label in EVENT_GROUPS.items():
            self.group_filter.addItem(label, key)
        filter_row.addWidget(self.group_filter, 1)

        self.actor_filter = QComboBox(self)
        self.actor_filter.addItem("Anyone", "")
        filter_row.addWidget(self.actor_filter, 1)
        body.addLayout(filter_row)

        self.search = SearchBox("Find in the detail text", self)
        body.addWidget(self.search)

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["When", "Who", "Event", "Object", "Detail"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(300)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        body.addWidget(self.table)

        page_row = QHBoxLayout()
        self.previous_button = make_button("Newer", parent=self)
        self.next_button = make_button("Older", parent=self)
        self.page_label = QLabel("", self)
        self.page_label.setProperty("dim", True)
        page_row.addWidget(self.previous_button)
        page_row.addWidget(self.next_button)
        page_row.addWidget(self.page_label, 1)
        body.addLayout(page_row)

        self.group_filter.currentIndexChanged.connect(self._reset_and_refresh)
        self.actor_filter.currentIndexChanged.connect(self._reset_and_refresh)
        self.search.textChanged.connect(self._reset_and_refresh)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.previous_button.clicked.connect(self._previous_page)
        self.next_button.clicked.connect(self._next_page)

        self.scroll.add_section(self.history_section)

    def _build_detail_section(self) -> None:
        self.detail_section = CollapsibleSection("Record detail", self, True, "info")
        body = self.detail_section.body_layout()

        self.detail_grid = KeyValueGrid(self)
        body.addWidget(self.detail_grid)

        self.detail_text = QTextEdit(self)
        self.detail_text.setReadOnly(True)
        self.detail_text.setMinimumHeight(170)
        self.detail_text.setProperty("mono", True)
        body.addWidget(self.detail_text)

        self.scroll.add_section(self.detail_section)

    def _reset_and_refresh(self) -> None:
        self._offset = 0
        self.refresh()

    def _previous_page(self) -> None:
        self._offset = max(0, self._offset - PAGE_SIZE)
        self.refresh()

    def _next_page(self) -> None:
        self._offset += PAGE_SIZE
        self.refresh()

    def refresh(self) -> None:
        if not self.controller.can(Permission.VIEW_AUDIT):
            self.table.setRowCount(0)
            self.page_label.setText("This account cannot read the audit history.")
            return

        current_actor = self.actor_filter.currentData()
        self.actor_filter.blockSignals(True)
        self.actor_filter.clear()
        self.actor_filter.addItem("Anyone", "")
        for user in self.controller.repo.list_users(include_inactive=True):
            self.actor_filter.addItem(f"{user.display_name} ({user.pseudonym})", user.id)
        if current_actor:
            index = self.actor_filter.findData(current_actor)
            if index >= 0:
                self.actor_filter.setCurrentIndex(index)
        self.actor_filter.blockSignals(False)

        records = self.controller.repo.audit_records(
            limit=PAGE_SIZE, offset=self._offset,
            actor_id=self.actor_filter.currentData() or "",
        )

        group = self.group_filter.currentData()
        if group:
            allowed = {e.value for e in GROUP_EVENTS.get(group, set())}
            records = [r for r in records if r.event in allowed]

        needle = self.search.text().strip().lower()
        if needle:
            records = [
                r for r in records
                if needle in r.detail.lower() or needle in r.event.lower()
                or needle in r.object_id.lower()
            ]

        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            when = QTableWidgetItem(record.timestamp[:19].replace("T", " "))
            when.setData(Qt.UserRole, record.sequence)
            self.table.setItem(row, 0, when)
            self.table.setItem(row, 1, QTableWidgetItem(record.actor_name or "system"))
            self.table.setItem(
                row, 2, QTableWidgetItem(record.event.replace("_", " "))
            )
            self.table.setItem(
                row, 3, QTableWidgetItem(f"{record.object_type} {record.object_id[:14]}".strip())
            )
            detail_item = QTableWidgetItem(record.detail)
            self.table.setItem(row, 4, detail_item)

            if record.event in (
                AuditEvent.LOGIN_FAILURE.value, AuditEvent.IMPORT_REJECTED.value,
                AuditEvent.EXPORT_FAILED.value,
            ):
                for column in range(5):
                    cell = self.table.item(row, column)
                    if cell:
                        cell.setForeground(QBrush(QColor(PALETTE.warning)))

        self.table.resizeColumnsToContents()
        total = self.controller.repo.audit_count()
        shown_from = self._offset + 1 if records else 0
        self.page_label.setText(
            f"Showing {shown_from} to {self._offset + len(records)} of {total} records."
        )
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(self._offset + PAGE_SIZE < total)
        self.history_section.set_badge(str(total))

    def _show_detail(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        sequence = item.data(Qt.UserRole)
        records = self.controller.repo.audit_records(limit=1, offset=0)
        record = None
        for candidate in self.controller.repo.audit_records(
            limit=PAGE_SIZE, offset=self._offset
        ):
            if candidate.sequence == sequence:
                record = candidate
                break
        if record is None:
            return

        auditor = (
            self.controller.user is not None
            and self.controller.user.role == Role.AUDITOR
        )
        self.detail_grid.set("sequence", "Record", str(record.sequence))
        self.detail_grid.set("when", "When", record.timestamp)
        self.detail_grid.set("who", "Who", record.actor_name or "system")
        self.detail_grid.set("event", "Event", record.event.replace("_", " "))
        self.detail_grid.set("object", "Object", f"{record.object_type} {record.object_id}")
        self.detail_grid.set("case", "Case", record.case_id or "not case specific")
        self.detail_grid.set("digest", "Digest", record.record_hash[:32], mono=True)
        self.detail_grid.set("previous", "Previous digest", record.previous_hash[:32], mono=True)

        before = _payload(record.before_json, auditor)
        after = _payload(record.after_json, auditor)
        text = []
        if record.detail:
            text.append(record.detail)
            text.append("")
        if before is not None:
            text.append("Before:")
            text.append(json.dumps(before, indent=2, default=str))
            text.append("")
        if after is not None:
            text.append("After:")
            text.append(json.dumps(after, indent=2, default=str))
        if auditor:
            text.append("")
            text.append(
                "Coordinate lists are withheld in this view. An auditor reads the "
                "history without reading clinical content."
            )
        self.detail_text.setPlainText("\n".join(text))


def _payload(raw: str, redact: bool):
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return raw
    return redact_for_auditor(value) if redact else value
