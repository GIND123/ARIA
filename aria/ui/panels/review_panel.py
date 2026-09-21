"""Review, revision history and agreement analysis.

A reviewer needs three things: the submission as it stands, what changed since
the last one, and how it compares with another annotator. Those are the three
sections here.

Blinding is enforced by the panel, not only by convention: the comparison
section refuses to show another annotator's work until both sets are submitted
(FR 043, AC 007).
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.models import ReviewComment, ReviewDecision, SetKind
from ...core.schema import CaseState, Side, get_class
from ..theme import PALETTE
from ..widgets.common import (
    Banner,
    CollapsibleSection,
    KeyValueGrid,
    ScrollPanel,
    SectionLabel,
    StatusChip,
    make_button,
)


class ReviewPanel(QWidget):
    """Submission review, revision comparison and agreement reporting."""

    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = ScrollPanel(self)
        layout.addWidget(self.scroll)

        self._build_submission_section()
        self._build_comments_section()
        self._build_revisions_section()
        self._build_agreement_section()

        controller.case_opened.connect(lambda _d: self.refresh())
        controller.case_closed.connect(self.clear)
        controller.annotations_changed.connect(self.refresh_revisions)

    # -- submission ----------------------------------------------------------

    def _build_submission_section(self) -> None:
        self.submission_section = CollapsibleSection("Submission", self, True, "review")
        body = self.submission_section.body_layout()

        self.state_chip = StatusChip("No case open", "neutral", self)
        body.addWidget(self.state_chip)

        self.details = KeyValueGrid(self)
        body.addWidget(self.details)

        self.blind_banner = Banner(self)
        body.addWidget(self.blind_banner)

        body.addWidget(SectionLabel("Decision", self))
        self.summary_edit = QTextEdit(self)
        self.summary_edit.setPlaceholderText(
            "Summary for the annotator. Say what is right as well as what needs changing."
        )
        self.summary_edit.setMaximumHeight(78)
        body.addWidget(self.summary_edit)

        row = QHBoxLayout()
        self.accept_button = make_button("Accept", "check", accent=True, parent=self)
        self.return_button = make_button("Return", "undo", parent=self)
        self.adjudicate_button = make_button("Adjudicate", "review", parent=self)
        row.addWidget(self.accept_button)
        row.addWidget(self.return_button)
        row.addWidget(self.adjudicate_button)
        body.addLayout(row)

        self.reopen_button = make_button(
            "Reopen for editing",
            tooltip="Return a case to the annotator and unlock it for editing.",
            parent=self,
        )
        body.addWidget(self.reopen_button)

        self.accept_button.clicked.connect(self.accept_case)
        self.return_button.clicked.connect(self.return_case)
        self.adjudicate_button.clicked.connect(self.adjudicate_case)
        self.reopen_button.clicked.connect(self.controller.reopen_for_edit)

        self.scroll.add_section(self.submission_section)

    # -- comments ------------------------------------------------------------

    def _build_comments_section(self) -> None:
        self.comments_section = CollapsibleSection("Comments on labels", self, True, "flag")
        body = self.comments_section.body_layout()

        note = QLabel(
            "Attach a comment to one label so the annotator knows exactly what to "
            "change. A returned case keeps the prior submission and records the "
            "changes as a new revision.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.comment_target = QComboBox(self)
        body.addWidget(self.comment_target)

        self.comment_text = QLineEdit(self)
        self.comment_text.setPlaceholderText("What needs attention on this label")
        body.addWidget(self.comment_text)

        row = QHBoxLayout()
        self.add_comment_button = make_button("Add comment", "check", parent=self)
        self.remove_comment_button = make_button("Remove selected", parent=self)
        row.addWidget(self.add_comment_button)
        row.addWidget(self.remove_comment_button)
        body.addLayout(row)

        self.comment_list = QListWidget(self)
        self.comment_list.setMaximumHeight(130)
        body.addWidget(self.comment_list)

        self.history_list = QListWidget(self)
        self.history_list.setMaximumHeight(120)
        body.addWidget(SectionLabel("Previous reviews", self))
        body.addWidget(self.history_list)

        self.add_comment_button.clicked.connect(self._add_comment)
        self.remove_comment_button.clicked.connect(self._remove_comment)

        self._pending_comments: list = []
        self.scroll.add_section(self.comments_section)

    # -- revisions -----------------------------------------------------------

    def _build_revisions_section(self) -> None:
        self.revisions_section = CollapsibleSection("Revisions", self, False, "audit")
        body = self.revisions_section.body_layout()

        self.revision_list = QListWidget(self)
        self.revision_list.setMaximumHeight(120)
        body.addWidget(self.revision_list)

        compare_row = QHBoxLayout()
        compare_row.addWidget(QLabel("Compare", self))
        self.revision_a = QComboBox(self)
        self.revision_b = QComboBox(self)
        compare_row.addWidget(self.revision_a, 1)
        compare_row.addWidget(QLabel("with", self))
        compare_row.addWidget(self.revision_b, 1)
        body.addLayout(compare_row)

        self.compare_button = make_button("Show differences", "side_by_side", parent=self)
        self.compare_button.clicked.connect(self._compare_revisions)
        body.addWidget(self.compare_button)

        self.diff_tree = QTreeWidget(self)
        self.diff_tree.setHeaderLabels(["Label", "Change"])
        self.diff_tree.setRootIsDecorated(False)
        self.diff_tree.setAlternatingRowColors(True)
        self.diff_tree.setMinimumHeight(150)
        self.diff_tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        body.addWidget(self.diff_tree)

        self.scroll.add_section(self.revisions_section)

    # -- agreement -----------------------------------------------------------

    def _build_agreement_section(self) -> None:
        self.agreement_section = CollapsibleSection("Agreement", self, False, "chart")
        body = self.agreement_section.body_layout()

        note = QLabel(
            "Compare this case against another annotator's independent set. "
            "ARIA reports agreement and does not apply a pass mark; thresholds "
            "come from the project configuration.",
            self,
        )
        note.setWordWrap(True)
        note.setProperty("dim", True)
        body.addWidget(note)

        self.compare_set = QComboBox(self)
        body.addWidget(self.compare_set)

        self.agreement_button = make_button("Build agreement report", "chart", parent=self)
        self.agreement_button.clicked.connect(self.build_agreement)
        body.addWidget(self.agreement_button)

        self.agreement_table = QTableWidget(0, 4, self)
        self.agreement_table.setHorizontalHeaderLabels(["Label", "Side", "Metric", "Value"])
        self.agreement_table.verticalHeader().setVisible(False)
        self.agreement_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.agreement_table.setAlternatingRowColors(True)
        self.agreement_table.setMinimumHeight(200)
        self.agreement_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        body.addWidget(self.agreement_table)

        self.agreement_summary = QLabel("", self)
        self.agreement_summary.setWordWrap(True)
        self.agreement_summary.setProperty("dim", True)
        body.addWidget(self.agreement_summary)

        self.scroll.add_section(self.agreement_section)

    # -- refresh -------------------------------------------------------------

    def refresh(self) -> None:
        data = self.controller.case_data
        if data is None:
            self.clear()
            return

        state = data.case.state_enum
        self.state_chip.set_state(
            state.display,
            {"submitted": "info", "accepted": "ok", "adjudicated": "ok",
             "returned": "warn"}.get(state.value, "neutral"),
        )

        users = {u.id: u for u in self.controller.repo.list_users(include_inactive=True)}
        annotator = users.get(data.annotation_set.annotator_id)
        self.details.set("case", "Case", data.case.pseudonym)
        self.details.set(
            "annotator", "Annotator",
            annotator.pseudonym if annotator else data.annotation_set.annotator_id or "unknown",
        )
        self.details.set("kind", "Set", data.annotation_set.kind)
        self.details.set("revision", "Revision", str(data.annotation_set.annotation_version))
        self.details.set(
            "submitted", "Submitted",
            (data.annotation_set.submitted_at or "not submitted")[:19].replace("T", " "),
        )
        self.details.set("objects", "Objects", str(len(data.live_annotations())))
        self.details.set("flags", "Quality flags", str(len(data.quality_flags)))

        from ...security.auth import Permission

        can_review = self.controller.can(Permission.REVIEW_CASES)
        submitted = state in (CaseState.SUBMITTED, CaseState.ACCEPTED, CaseState.ADJUDICATED)
        self.accept_button.setEnabled(can_review and state is CaseState.SUBMITTED)
        self.return_button.setEnabled(can_review and submitted)
        self.adjudicate_button.setEnabled(
            can_review and self.controller.can(Permission.ADJUDICATE) and submitted
        )
        self.reopen_button.setEnabled(can_review and state is CaseState.RETURNED)

        if not submitted:
            self.blind_banner.show_message(
                "This set has not been submitted. Review actions become available "
                "once the annotator submits it.",
                "info",
            )
        else:
            self.blind_banner.clear()

        self._populate_comment_targets()
        self._refresh_review_history()
        self.refresh_revisions()
        self._populate_compare_sets()

    def _populate_comment_targets(self) -> None:
        data = self.controller.case_data
        self.comment_target.clear()
        self.comment_target.addItem("Whole case", "")
        if data is None:
            return
        for annotation in sorted(data.live_annotations(), key=lambda a: a.class_key):
            try:
                cls = get_class(annotation.class_key)
            except KeyError:
                continue
            self.comment_target.addItem(
                f"{cls.display_name} ({Side(annotation.side).display})", annotation.id
            )

    def _refresh_review_history(self) -> None:
        self.history_list.clear()
        data = self.controller.case_data
        if data is None:
            return
        users = {u.id: u for u in self.controller.repo.list_users(include_inactive=True)}
        for review in self.controller.repo.list_reviews(data.annotation_set.id):
            reviewer = users.get(review.reviewer_id)
            name = reviewer.pseudonym if reviewer else review.reviewer_id
            item = QListWidgetItem(
                f"{review.created_at[:16].replace('T', ' ')}   {review.decision}   "
                f"{name}   revision {review.reviewed_revision}",
                self.history_list,
            )
            detail = [review.summary] if review.summary else []
            for comment in review.comments:
                detail.append(f"  {comment.class_key or 'case'}: {comment.text}")
            item.setToolTip("\n".join(detail) or "No summary recorded.")

    def refresh_revisions(self) -> None:
        data = self.controller.case_data
        self.revision_list.clear()
        self.revision_a.clear()
        self.revision_b.clear()
        if data is None:
            return
        revisions = self.controller.repo.list_revisions(data.annotation_set.id)
        for revision in revisions:
            text = (
                f"Revision {revision.revision_no}   "
                f"{revision.created_at[:16].replace('T', ' ')}   {revision.reason}"
            )
            item = QListWidgetItem(text, self.revision_list)
            item.setToolTip(f"Snapshot digest {revision.sha256[:16]}")
            self.revision_a.addItem(f"Revision {revision.revision_no}", revision.revision_no)
            self.revision_b.addItem(f"Revision {revision.revision_no}", revision.revision_no)
        self.revision_b.addItem("Current working state", -1)
        if self.revision_a.count() >= 2:
            self.revision_a.setCurrentIndex(self.revision_a.count() - 2)
        self.revision_b.setCurrentIndex(self.revision_b.count() - 1)
        self.revisions_section.set_badge(f"{len(revisions)}")

    def _populate_compare_sets(self) -> None:
        data = self.controller.case_data
        self.compare_set.clear()
        if data is None:
            return
        users = {u.id: u for u in self.controller.repo.list_users(include_inactive=True)}
        for other in self.controller.repo.list_sets_for_case(data.case.id):
            if other.id == data.annotation_set.id:
                continue
            user = users.get(other.annotator_id)
            name = user.pseudonym if user else other.annotator_id
            blinded = other.submitted_at is None
            label = f"{name}   {other.kind}"
            if blinded:
                label += "   not yet submitted"
            self.compare_set.addItem(label, other.id)
            self.compare_set.setItemData(
                self.compare_set.count() - 1,
                (
                    "This set has not been submitted. It stays hidden until both "
                    "annotators submit, so neither is influenced by the other."
                    if blinded else "Available for comparison."
                ),
                Qt.ToolTipRole,
            )

    def clear(self) -> None:
        self.state_chip.set_state("No case open", "neutral")
        self.details.clear_values()
        self.comment_list.clear()
        self.history_list.clear()
        self.revision_list.clear()
        self.diff_tree.clear()
        self.agreement_table.setRowCount(0)
        self.agreement_summary.setText("")
        self.blind_banner.clear()
        self._pending_comments = []

    # -- comments ------------------------------------------------------------

    def _add_comment(self) -> None:
        text = self.comment_text.text().strip()
        if not text:
            return
        annotation_id = self.comment_target.currentData() or ""
        class_key = ""
        side = Side.NONE.value
        if annotation_id:
            annotation = self.controller._find(annotation_id)
            if annotation is not None:
                class_key, side = annotation.class_key, annotation.side

        comment = ReviewComment(
            annotation_id=annotation_id, class_key=class_key, side=side,
            decision=ReviewDecision.COMMENT, text=text,
            created_by=self.controller.user.id if self.controller.user else "",
        )
        self._pending_comments.append(comment)
        label = self.comment_target.currentText()
        item = QListWidgetItem(f"{label}: {text}", self.comment_list)
        item.setData(Qt.UserRole, comment.id)
        self.comment_text.clear()

    def _remove_comment(self) -> None:
        for item in self.comment_list.selectedItems():
            comment_id = item.data(Qt.UserRole)
            self._pending_comments = [
                c for c in self._pending_comments if c.id != comment_id
            ]
            self.comment_list.takeItem(self.comment_list.row(item))

    # -- decisions -----------------------------------------------------------

    def accept_case(self) -> None:
        self._record(ReviewDecision.ACCEPT, CaseState.ACCEPTED)

    def return_case(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        if not self.summary_edit.toPlainText().strip() and not self._pending_comments:
            QMessageBox.information(
                self, "Say what needs changing",
                "Add a summary or at least one comment before returning the case, "
                "so the annotator knows what to do.",
            )
            return
        self._record(ReviewDecision.RETURN, CaseState.RETURNED)

    def adjudicate_case(self) -> None:
        self._record(ReviewDecision.ADJUDICATE, CaseState.ADJUDICATED)

    def _record(self, decision: str, state: CaseState) -> None:
        ok = self.controller.record_review(
            decision, self.summary_edit.toPlainText().strip(),
            self._pending_comments, state,
        )
        if ok:
            self._pending_comments = []
            self.comment_list.clear()
            self.summary_edit.clear()
            self.refresh()

    # -- revision comparison -------------------------------------------------

    def _compare_revisions(self) -> None:
        data = self.controller.case_data
        if data is None:
            return
        a_no = self.revision_a.currentData()
        b_no = self.revision_b.currentData()
        if a_no is None:
            return

        revision_a = self.controller.repo.get_revision(data.annotation_set.id, a_no)
        if revision_a is None:
            return
        snapshot_a = revision_a.snapshot()

        if b_no == -1:
            snapshot_b = {
                "annotations": [a.to_dict() for a in data.live_annotations()],
                "categorical_labels": [
                    {"key": c.key, "side": c.side, "value": c.value}
                    for c in data.categorical
                ],
            }
            b_label = "current working state"
        else:
            revision_b = self.controller.repo.get_revision(data.annotation_set.id, b_no)
            if revision_b is None:
                return
            snapshot_b = revision_b.snapshot()
            b_label = f"revision {b_no}"

        self.diff_tree.clear()
        differences = _diff_snapshots(snapshot_a, snapshot_b)
        if not differences:
            item = QTreeWidgetItem(self.diff_tree, ["No differences", ""])
            item.setForeground(0, QBrush(QColor(PALETTE.text_dim)))
            return

        for label, change, level in differences:
            item = QTreeWidgetItem(self.diff_tree, [label, change])
            colour = {
                "added": PALETTE.success, "removed": PALETTE.danger,
                "changed": PALETTE.warning,
            }.get(level, PALETTE.text)
            item.setForeground(1, QBrush(QColor(colour)))
        self.revisions_section.set_badge(
            f"{len(differences)} changes against {b_label}"
        )

    # -- agreement -----------------------------------------------------------

    def build_agreement(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        data = self.controller.case_data
        if data is None:
            return
        other_id = self.compare_set.currentData()
        if not other_id:
            QMessageBox.information(
                self, "Nothing to compare",
                "No second annotation set exists for this case.\n\n"
                "A data manager marks a case for duplicate annotation and assigns "
                "it to a second annotator.",
            )
            return

        other = self.controller.repo.load_set_data(other_id)
        if other is None:
            return

        # Blinding: refuse until both are submitted (FR 043, AC 007).
        if other.annotation_set.submitted_at is None:
            QMessageBox.information(
                self, "The other set is still blinded",
                "The second annotator has not submitted yet.\n\n"
                "Their work stays hidden until both sets are submitted, so "
                "neither annotator is influenced by the other.",
            )
            return
        if data.annotation_set.submitted_at is None:
            QMessageBox.information(
                self, "Submit this set first",
                "Comparison becomes available once this set is also submitted.\n\n"
                "This keeps duplicate annotation genuinely independent.",
            )
            return

        from ...core.agreement import compare_case_pair

        shape = (data.case.source.rows, data.case.source.columns)
        report = compare_case_pair(data, other, self.controller.schema, shape)

        self.agreement_table.setRowCount(len(report.items))
        tolerances = report.tolerances
        for row, item in enumerate(report.items):
            try:
                label = get_class(item.label).display_name
            except KeyError:
                label = item.label
            self.agreement_table.setItem(row, 0, QTableWidgetItem(label))
            self.agreement_table.setItem(
                row, 1, QTableWidgetItem(Side(item.side).display if item.side else "")
            )
            self.agreement_table.setItem(
                row, 2, QTableWidgetItem(item.metric.replace("_", " "))
            )
            value_text = (
                f"{item.value:.3f} {item.unit}" if item.value is not None else item.note
            )
            value_item = QTableWidgetItem(value_text)
            value_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

            tolerance = {
                "point_distance_error": tolerances.get("point_tolerance_px"),
                "line_endpoint_error": tolerances.get("line_endpoint_tolerance_px"),
                "absolute_measurement_difference": (
                    tolerances.get("measurement_tolerance_mm") if item.unit == "mm" else None
                ),
            }.get(item.metric)
            if tolerance is not None and item.value is not None:
                exceeded = item.value > tolerance
                value_item.setForeground(
                    QBrush(QColor(PALETTE.warning if exceeded else PALETTE.success))
                )
                value_item.setToolTip(
                    f"Project tolerance is {tolerance}. "
                    + ("This exceeds it." if exceeded else "This is within it.")
                    + "\n\nThe tolerance comes from the project configuration, "
                    "not from a universal cutoff."
                )
            elif item.metric in ("dice", "iou") and item.value is not None:
                limit = tolerances.get("mask_dice_tolerance")
                if limit:
                    value_item.setForeground(
                        QBrush(QColor(PALETTE.success if item.value >= limit else PALETTE.warning))
                    )
            self.agreement_table.setItem(row, 3, value_item)

        parts = []
        for metric, stats in report.summary.items():
            parts.append(
                f"{metric.replace('_', ' ')}: mean {stats['mean']:.3f}, "
                f"max {stats['max']:.3f}, n {stats['n']}"
            )
        self.agreement_summary.setText("   ".join(parts) + f"\n\n{report.note}")
        self.agreement_section.set_expanded(True)


def _diff_snapshots(a: dict, b: dict) -> list:
    """Compare two snapshots and describe what changed."""
    out: list = []

    def index(snapshot):
        return {
            (item.get("class_key"), item.get("side")): item
            for item in snapshot.get("annotations", [])
            if not item.get("deleted")
        }

    ia, ib = index(a), index(b)

    for key in sorted(set(ia) | set(ib), key=lambda k: (k[0] or "", k[1] or "")):
        try:
            name = get_class(key[0]).display_name
        except (KeyError, TypeError):
            name = str(key[0])
        side = Side(key[1]).display if key[1] else ""
        label = f"{name} ({side})" if side else name

        if key not in ib:
            out.append((label, "removed", "removed"))
            continue
        if key not in ia:
            out.append((label, "added", "added"))
            continue

        pa = ia[key].get("coordinates") or []
        pb = ib[key].get("coordinates") or []
        if pa != pb:
            if len(pa) != len(pb):
                out.append((label, f"{len(pa) // 2} points became {len(pb) // 2}", "changed"))
            else:
                shift = max(
                    (abs(x - y) for x, y in zip(pa, pb)), default=0.0
                )
                out.append((label, f"moved, largest shift {shift:.1f} px", "changed"))
        if ia[key].get("presence") != ib[key].get("presence"):
            out.append(
                (
                    label,
                    f"state {ia[key].get('presence')} became {ib[key].get('presence')}",
                    "changed",
                )
            )

    ga = {(c.get("key"), c.get("side")): c.get("value") for c in a.get("categorical_labels", [])}
    gb = {(c.get("key"), c.get("side")): c.get("value") for c in b.get("categorical_labels", [])}
    for key in sorted(set(ga) | set(gb), key=lambda k: (k[0] or "", k[1] or "")):
        if ga.get(key) != gb.get(key):
            side = Side(key[1]).display if key[1] else ""
            out.append(
                (
                    f"Cortical index ({side})",
                    f"{ga.get(key, 'not set')} became {gb.get(key, 'not set')}",
                    "changed",
                )
            )
    return out
