"""Data access for every domain object, with auditing built in.

Two rules hold throughout this module:

* every state changing method writes an audit record in the same transaction as
  the change, so a change can never exist without its history entry,
* every write to an annotation set checks the expected edit counter, so two
  users working on one case cannot silently overwrite one another (FR 015).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path

from ..core.audit import AuditEvent, canonical_json, compute_record_hash, verify_chain
from ..core.models import (
    Annotation,
    AnnotationSet,
    AuditRecord,
    CalibrationSetResult,
    Case,
    CaseData,
    CategoricalLabel,
    Project,
    QualityFlagRecord,
    Review,
    ReviewComment,
    Revision,
    Role,
    SetKind,
    SourceImage,
    User,
    new_id,
    utc_now,
)
from ..core.schema import CaseState, STATE_TRANSITIONS, Side
from ..core.units import Calibration
from .db import Database


class ConcurrentEditError(RuntimeError):
    """Raised when a write would overwrite another user's change (FR 015)."""

    def __init__(self, expected: int, actual: int, who: str = ""):
        self.expected = expected
        self.actual = actual
        self.who = who
        holder = f" by {who}" if who else ""
        super().__init__(
            f"This case was changed{holder} since it was loaded. Your view is at "
            f"edit {expected} and the stored case is at edit {actual}. Reload the "
            f"case to see the current state before saving again."
        )


class CaseLockedError(RuntimeError):
    """Raised when another session holds the editing lock on a case."""

    def __init__(self, user_name: str, since: str):
        self.user_name = user_name
        self.since = since
        super().__init__(
            f"{user_name or 'Another user'} has had this case open for editing "
            f"since {since}. Open it read only, or ask them to close it."
        )


class TransitionError(RuntimeError):
    """Raised for a case state change the workflow does not allow."""


#: A lock older than this without a heartbeat is treated as abandoned, which is
#: what happens after a crash or a forced shutdown.
LOCK_STALE_SECONDS = 180


def _json(value) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _loads(value, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


class Repository:
    """The application's single door to stored data."""

    def __init__(self, db: Database, session_id: str | None = None):
        self.db = db
        self.session_id = session_id or uuid.uuid4().hex
        self._actor_id = ""
        self._actor_name = ""

    # -- actor ---------------------------------------------------------------

    def set_actor(self, user: User | None) -> None:
        self._actor_id = user.id if user else ""
        self._actor_name = (user.display_name or user.username) if user else ""

    # -- auditing ------------------------------------------------------------

    def log(
        self,
        event,
        object_type: str = "",
        object_id: str = "",
        before=None,
        after=None,
        detail: str = "",
        project_id: str = "",
        case_id: str = "",
        conn: sqlite3.Connection | None = None,
    ) -> AuditRecord:
        """Append one audit record, chained to the record before it.

        When ``conn`` is supplied the record is written inside the caller's
        transaction, so the change and its audit entry commit together or not at
        all.
        """
        event_value = event.value if isinstance(event, AuditEvent) else str(event)

        def _write(c: sqlite3.Connection) -> AuditRecord:
            row = c.execute(
                "SELECT sequence, record_hash FROM audit_log "
                "ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            sequence = (int(row["sequence"]) + 1) if row else 1
            previous_hash = row["record_hash"] if row else ""

            record = AuditRecord(
                sequence=sequence,
                timestamp=utc_now(),
                actor_id=self._actor_id,
                actor_name=self._actor_name,
                event=event_value,
                object_type=object_type,
                object_id=object_id,
                project_id=project_id,
                case_id=case_id,
                before_json=canonical_json(before) if before is not None else "",
                after_json=canonical_json(after) if after is not None else "",
                detail=detail,
                previous_hash=previous_hash,
            )
            record.record_hash = compute_record_hash(
                record.sequence, record.timestamp, record.actor_id, record.event,
                record.object_type, record.object_id, record.before_json,
                record.after_json, record.detail, record.previous_hash,
            )
            c.execute(
                "INSERT INTO audit_log (id, sequence, timestamp, actor_id, actor_name,"
                " event, object_type, object_id, project_id, case_id, before_json,"
                " after_json, detail, previous_hash, record_hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.id, record.sequence, record.timestamp, record.actor_id,
                    record.actor_name, record.event, record.object_type,
                    record.object_id, record.project_id, record.case_id,
                    record.before_json, record.after_json, record.detail,
                    record.previous_hash, record.record_hash,
                ),
            )
            return record

        if conn is not None:
            return _write(conn)
        with self.db.transaction() as c:
            return _write(c)

    def audit_records(
        self, limit: int = 500, offset: int = 0, case_id: str = "",
        actor_id: str = "", event: str = "", since: str = "",
    ) -> list:
        clauses, params = [], []
        if case_id:
            clauses.append("case_id = ?")
            params.append(case_id)
        if actor_id:
            clauses.append("actor_id = ?")
            params.append(actor_id)
        if event:
            clauses.append("event = ?")
            params.append(event)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.db.query(
            f"SELECT * FROM audit_log{where} ORDER BY sequence DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._row_to_audit(r) for r in rows]

    def audit_count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM audit_log")
        return int(row["n"]) if row else 0

    def verify_audit_chain(self) -> dict:
        rows = self.db.query("SELECT * FROM audit_log ORDER BY sequence ASC")
        return verify_chain([self._row_to_audit(r) for r in rows])

    @staticmethod
    def _row_to_audit(row) -> AuditRecord:
        return AuditRecord(
            id=row["id"], sequence=row["sequence"], timestamp=row["timestamp"],
            actor_id=row["actor_id"], actor_name=row["actor_name"], event=row["event"],
            object_type=row["object_type"], object_id=row["object_id"],
            project_id=row["project_id"], case_id=row["case_id"],
            before_json=row["before_json"], after_json=row["after_json"],
            detail=row["detail"], previous_hash=row["previous_hash"],
            record_hash=row["record_hash"],
        )

    # -- users ---------------------------------------------------------------

    def create_user(self, user: User) -> User:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO users (id, username, display_name, role, pseudonym,"
                " password_hash, password_salt, active, created_at, last_login_at,"
                " calibration_passed, calibration_passed_at, calibration_approved_by,"
                " must_change_password, failed_logins, locked_until)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    user.id, user.username, user.display_name, user.role,
                    user.pseudonym or f"ANN-{user.id[-6:].upper()}",
                    user.password_hash, user.password_salt, int(user.active),
                    user.created_at, user.last_login_at, int(user.calibration_passed),
                    user.calibration_passed_at, user.calibration_approved_by,
                    int(user.must_change_password), user.failed_logins, user.locked_until,
                ),
            )
            self.log(
                AuditEvent.USER_CREATED, "user", user.id,
                after={"username": user.username, "role": user.role},
                detail=f"Account {user.username} created with role {user.role}.",
                conn=c,
            )
        return user

    def get_user(self, user_id: str) -> User | None:
        row = self.db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> User | None:
        row = self.db.query_one(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        )
        return self._row_to_user(row) if row else None

    def list_users(self, include_inactive: bool = False) -> list:
        sql = "SELECT * FROM users"
        if not include_inactive:
            sql += " WHERE active = 1"
        sql += " ORDER BY display_name COLLATE NOCASE, username COLLATE NOCASE"
        return [self._row_to_user(r) for r in self.db.query(sql)]

    def update_user(self, user: User, detail: str = "") -> None:
        before = self.get_user(user.id)
        with self.db.transaction() as c:
            c.execute(
                "UPDATE users SET username=?, display_name=?, role=?, pseudonym=?,"
                " password_hash=?, password_salt=?, active=?, last_login_at=?,"
                " calibration_passed=?, calibration_passed_at=?,"
                " calibration_approved_by=?, must_change_password=?, failed_logins=?,"
                " locked_until=? WHERE id=?",
                (
                    user.username, user.display_name, user.role, user.pseudonym,
                    user.password_hash, user.password_salt, int(user.active),
                    user.last_login_at, int(user.calibration_passed),
                    user.calibration_passed_at, user.calibration_approved_by,
                    int(user.must_change_password), user.failed_logins,
                    user.locked_until, user.id,
                ),
            )
            event = AuditEvent.USER_UPDATED
            if before and before.role != user.role:
                event = AuditEvent.ROLE_CHANGED
            if before and before.active and not user.active:
                event = AuditEvent.USER_DEACTIVATED
            self.log(
                event, "user", user.id,
                before={"role": before.role, "active": before.active} if before else None,
                after={"role": user.role, "active": user.active},
                detail=detail or f"Account {user.username} updated.",
                conn=c,
            )

    def count_users(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    @staticmethod
    def _row_to_user(row) -> User:
        return User(
            id=row["id"], username=row["username"], display_name=row["display_name"],
            role=row["role"], pseudonym=row["pseudonym"],
            password_hash=row["password_hash"], password_salt=row["password_salt"],
            active=bool(row["active"]), created_at=row["created_at"],
            last_login_at=row["last_login_at"],
            calibration_passed=bool(row["calibration_passed"]),
            calibration_passed_at=row["calibration_passed_at"],
            calibration_approved_by=row["calibration_approved_by"],
            must_change_password=bool(row["must_change_password"]),
            failed_logins=int(row["failed_logins"] or 0),
            locked_until=row["locked_until"],
        )

    # -- projects ------------------------------------------------------------

    def create_project(self, project: Project) -> Project:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO projects (id, name, description, schema_json,"
                " created_by, created_at, deid_profile, archived)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (
                    project.id, project.name, project.description, project.schema_json,
                    project.created_by, project.created_at, project.deid_profile,
                    int(project.archived),
                ),
            )
            self.log(
                AuditEvent.PROJECT_CREATED, "project", project.id,
                after={"name": project.name, "deid_profile": project.deid_profile},
                detail=f"Project {project.name} created.",
                project_id=project.id, conn=c,
            )
        return project

    def get_project(self, project_id: str) -> Project | None:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._row_to_project(row) if row else None

    def list_projects(self, include_archived: bool = False) -> list:
        sql = "SELECT * FROM projects"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY created_at DESC, id ASC"
        return [self._row_to_project(r) for r in self.db.query(sql)]

    def update_project(self, project: Project, detail: str = "") -> None:
        before = self.get_project(project.id)
        with self.db.transaction() as c:
            c.execute(
                "UPDATE projects SET name=?, description=?, schema_json=?,"
                " deid_profile=?, archived=? WHERE id=?",
                (
                    project.name, project.description, project.schema_json,
                    project.deid_profile, int(project.archived), project.id,
                ),
            )
            schema_changed = bool(before and before.schema_json != project.schema_json)
            self.log(
                AuditEvent.SCHEMA_CHANGED if schema_changed else AuditEvent.PROJECT_UPDATED,
                "project", project.id,
                before=_loads(before.schema_json, {}) if before and schema_changed else None,
                after=_loads(project.schema_json, {}) if schema_changed else {"name": project.name},
                detail=detail or f"Project {project.name} updated.",
                project_id=project.id, conn=c,
            )

    @staticmethod
    def _row_to_project(row) -> Project:
        return Project(
            id=row["id"], name=row["name"], description=row["description"],
            schema_json=row["schema_json"], created_by=row["created_by"],
            created_at=row["created_at"], deid_profile=row["deid_profile"],
            archived=bool(row["archived"]),
        )

    # -- cases ---------------------------------------------------------------

    def create_case(self, case: Case, deid_report: dict | None = None) -> Case:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO cases (id, project_id, pseudonym, source_json,"
                " calibration_json, display_settings_json, deid_report_json, state,"
                " assigned_to, laterality_confirmed, laterality_confirmed_by,"
                " laterality_confirmed_at, laterality_note, split, imported_by,"
                " imported_at, duplicate_target, archived, source_sha256)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    case.id, case.project_id, case.pseudonym,
                    _json(case.source.to_dict()), _json(case.calibration.to_dict()),
                    "{}", _json(deid_report or {}), case.state, case.assigned_to,
                    int(case.laterality_confirmed), case.laterality_confirmed_by,
                    case.laterality_confirmed_at, case.laterality_note, case.split,
                    case.imported_by, case.imported_at, int(case.duplicate_target),
                    int(case.archived), case.source.sha256,
                ),
            )
            self.log(
                AuditEvent.IMPORT_COMPLETED, "case", case.id,
                after={
                    "pseudonym": case.pseudonym,
                    "source_format": case.source.source_format,
                    "sha256": case.source.sha256,
                    "rows": case.source.rows,
                    "columns": case.source.columns,
                },
                detail=f"Case {case.pseudonym} imported from a {case.source.source_format} source.",
                project_id=case.project_id, case_id=case.id, conn=c,
            )
            if deid_report:
                self.log(
                    AuditEvent.DEIDENTIFY_APPLIED, "case", case.id,
                    after=deid_report,
                    detail=(
                        f"Deidentification profile "
                        f"{deid_report.get('profile_name', 'unknown')} applied."
                    ),
                    project_id=case.project_id, case_id=case.id, conn=c,
                )
        return case

    def get_case(self, case_id: str) -> Case | None:
        row = self.db.query_one("SELECT * FROM cases WHERE id = ?", (case_id,))
        return self._row_to_case(row) if row else None

    def find_case_by_checksum(self, project_id: str, sha256: str) -> Case | None:
        row = self.db.query_one(
            "SELECT * FROM cases WHERE project_id = ? AND source_sha256 = ?",
            (project_id, sha256),
        )
        return self._row_to_case(row) if row else None

    def list_cases(
        self, project_id: str = "", state: str = "", assigned_to: str = "",
        split: str = "", include_archived: bool = False, search: str = "",
        limit: int = 0, offset: int = 0,
    ) -> list:
        clauses, params = [], []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        if assigned_to:
            clauses.append("assigned_to = ?")
            params.append(assigned_to)
        if split:
            clauses.append("split = ?")
            params.append(split)
        if not include_archived:
            clauses.append("archived = 0")
        if search:
            clauses.append("pseudonym LIKE ?")
            params.append(f"%{search}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM cases{where} ORDER BY imported_at DESC, pseudonym ASC, id ASC"
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        return [self._row_to_case(r) for r in self.db.query(sql, tuple(params))]

    def count_cases(self, project_id: str = "") -> dict:
        sql = "SELECT state, COUNT(*) AS n FROM cases WHERE archived = 0"
        params: tuple = ()
        if project_id:
            sql += " AND project_id = ?"
            params = (project_id,)
        sql += " GROUP BY state"
        return {r["state"]: int(r["n"]) for r in self.db.query(sql, params)}

    def set_case_state(self, case_id: str, new_state: CaseState, detail: str = "") -> None:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(f"No case with identifier {case_id}")
        current = case.state_enum
        if new_state is not current and new_state not in STATE_TRANSITIONS.get(current, set()):
            allowed = ", ".join(sorted(s.display for s in STATE_TRANSITIONS.get(current, set())))
            raise TransitionError(
                f"A case that is {current.display} cannot move to {new_state.display}. "
                f"Allowed next states are: {allowed or 'none'}."
            )
        with self.db.transaction() as c:
            c.execute("UPDATE cases SET state = ? WHERE id = ?", (new_state.value, case_id))
            self.log(
                AuditEvent.CASE_STATE_CHANGED, "case", case_id,
                before={"state": current.value}, after={"state": new_state.value},
                detail=detail or f"State changed from {current.display} to {new_state.display}.",
                project_id=case.project_id, case_id=case_id, conn=c,
            )

    def assign_case(self, case_id: str, user_id: str | None, detail: str = "") -> None:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(f"No case with identifier {case_id}")
        with self.db.transaction() as c:
            new_state = CaseState.ASSIGNED.value if user_id else CaseState.UNASSIGNED.value
            if case.state in (
                CaseState.SUBMITTED.value, CaseState.ACCEPTED.value,
                CaseState.ADJUDICATED.value, CaseState.IN_PROGRESS.value,
            ):
                new_state = case.state
            c.execute(
                "UPDATE cases SET assigned_to = ?, state = ? WHERE id = ?",
                (user_id, new_state, case_id),
            )
            self.log(
                AuditEvent.CASE_ASSIGNED if user_id else AuditEvent.CASE_UNASSIGNED,
                "case", case_id,
                before={"assigned_to": case.assigned_to},
                after={"assigned_to": user_id},
                detail=detail or ("Case assigned." if user_id else "Case unassigned."),
                project_id=case.project_id, case_id=case_id, conn=c,
            )

    def confirm_laterality(self, case_id: str, user_id: str, note: str = "") -> None:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(f"No case with identifier {case_id}")
        now = utc_now()
        with self.db.transaction() as c:
            c.execute(
                "UPDATE cases SET laterality_confirmed = 1, laterality_confirmed_by = ?,"
                " laterality_confirmed_at = ?, laterality_note = ? WHERE id = ?",
                (user_id, now, note, case_id),
            )
            self.log(
                AuditEvent.LATERALITY_CONFIRMED, "case", case_id,
                after={"confirmed_by": user_id, "note": note},
                detail="Anatomical right and left confirmed for this case.",
                project_id=case.project_id, case_id=case_id, conn=c,
            )

    def set_calibration(self, case_id: str, calibration: Calibration, detail: str = "") -> None:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(f"No case with identifier {case_id}")
        before = case.calibration.to_dict()
        with self.db.transaction() as c:
            c.execute(
                "UPDATE cases SET calibration_json = ? WHERE id = ?",
                (_json(calibration.to_dict()), case_id),
            )
            event = AuditEvent.CALIBRATION_SET
            if calibration.is_validated:
                event = AuditEvent.CALIBRATION_VALIDATED
            elif calibration.status.value == "rejected":
                event = AuditEvent.CALIBRATION_REJECTED
            self.log(
                event, "case", case_id, before=before, after=calibration.to_dict(),
                detail=detail or calibration.summary_line(),
                project_id=case.project_id, case_id=case_id, conn=c,
            )

    def set_display_settings(self, case_id: str, settings: dict) -> None:
        """Display settings are saved separately from image pixels (FR 012)."""
        self.db.execute(
            "UPDATE cases SET display_settings_json = ? WHERE id = ?",
            (_json(settings), case_id),
        )

    def get_display_settings(self, case_id: str) -> dict:
        row = self.db.query_one(
            "SELECT display_settings_json FROM cases WHERE id = ?", (case_id,)
        )
        return _loads(row["display_settings_json"], {}) if row else {}

    def set_case_split(self, case_id: str, split: str) -> None:
        self.db.execute("UPDATE cases SET split = ? WHERE id = ?", (split, case_id))

    def set_duplicate_target(self, case_id: str, value: bool) -> None:
        self.db.execute(
            "UPDATE cases SET duplicate_target = ? WHERE id = ?", (int(value), case_id)
        )

    def archive_case(self, case_id: str, reason: str = "") -> None:
        case = self.get_case(case_id)
        with self.db.transaction() as c:
            c.execute("UPDATE cases SET archived = 1 WHERE id = ?", (case_id,))
            self.log(
                AuditEvent.CASE_ARCHIVED, "case", case_id, detail=reason or "Case archived.",
                project_id=case.project_id if case else "", case_id=case_id, conn=c,
            )

    def record_case_view(self, case_id: str) -> None:
        case = self.get_case(case_id)
        self.log(
            AuditEvent.CASE_VIEWED, "case", case_id, detail="Case opened in the viewer.",
            project_id=case.project_id if case else "", case_id=case_id,
        )

    def get_deid_report(self, case_id: str) -> dict:
        row = self.db.query_one("SELECT deid_report_json FROM cases WHERE id = ?", (case_id,))
        return _loads(row["deid_report_json"], {}) if row else {}

    @staticmethod
    def _row_to_case(row) -> Case:
        return Case(
            id=row["id"], project_id=row["project_id"], pseudonym=row["pseudonym"],
            source=SourceImage.from_dict(_loads(row["source_json"], {})),
            calibration=Calibration.from_dict(_loads(row["calibration_json"], {})),
            state=row["state"], assigned_to=row["assigned_to"],
            laterality_confirmed=bool(row["laterality_confirmed"]),
            laterality_confirmed_by=row["laterality_confirmed_by"],
            laterality_confirmed_at=row["laterality_confirmed_at"],
            laterality_note=row["laterality_note"], split=row["split"],
            imported_by=row["imported_by"], imported_at=row["imported_at"],
            duplicate_target=bool(row["duplicate_target"]),
            archived=bool(row["archived"]),
        )

    # -- annotation sets -----------------------------------------------------

    def get_or_create_set(
        self, case_id: str, annotator_id: str, kind: str = SetKind.PRIMARY
    ) -> AnnotationSet:
        row = self.db.query_one(
            "SELECT * FROM annotation_sets WHERE case_id = ? AND annotator_id = ? AND kind = ?",
            (case_id, annotator_id, kind),
        )
        if row:
            return self._row_to_set(row)
        aset = AnnotationSet(case_id=case_id, annotator_id=annotator_id, kind=kind)
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO annotation_sets (id, case_id, annotator_id, kind,"
                " schema_version, annotation_version, state, created_at, updated_at,"
                " submitted_at, reviewed_by, reviewed_at, edit_counter, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    aset.id, aset.case_id, aset.annotator_id, aset.kind,
                    aset.schema_version, aset.annotation_version, aset.state,
                    aset.created_at, aset.updated_at, aset.submitted_at,
                    aset.reviewed_by, aset.reviewed_at, aset.edit_counter, aset.notes,
                ),
            )
        return aset

    def get_set(self, set_id: str) -> AnnotationSet | None:
        row = self.db.query_one("SELECT * FROM annotation_sets WHERE id = ?", (set_id,))
        return self._row_to_set(row) if row else None

    def list_sets_for_case(self, case_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM annotation_sets WHERE case_id = ?"
            " ORDER BY kind, created_at, id",
            (case_id,),
        )
        return [self._row_to_set(r) for r in rows]

    def set_edit_counter(self, set_id: str) -> int:
        row = self.db.query_one(
            "SELECT edit_counter FROM annotation_sets WHERE id = ?", (set_id,)
        )
        return int(row["edit_counter"]) if row else 0

    def _bump_set(self, conn: sqlite3.Connection, set_id: str, expected: int | None) -> int:
        """Increment the edit counter, refusing a stale write (FR 015)."""
        row = conn.execute(
            "SELECT edit_counter FROM annotation_sets WHERE id = ?", (set_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"No annotation set with identifier {set_id}")
        actual = int(row["edit_counter"])
        if expected is not None and expected != actual:
            raise ConcurrentEditError(expected, actual)
        new_value = actual + 1
        conn.execute(
            "UPDATE annotation_sets SET edit_counter = ?, updated_at = ? WHERE id = ?",
            (new_value, utc_now(), set_id),
        )
        return new_value

    @staticmethod
    def _row_to_set(row) -> AnnotationSet:
        return AnnotationSet(
            id=row["id"], case_id=row["case_id"], annotator_id=row["annotator_id"],
            kind=row["kind"], schema_version=row["schema_version"],
            annotation_version=int(row["annotation_version"]), state=row["state"],
            created_at=row["created_at"], updated_at=row["updated_at"],
            submitted_at=row["submitted_at"], reviewed_by=row["reviewed_by"],
            reviewed_at=row["reviewed_at"], edit_counter=int(row["edit_counter"]),
            notes=row["notes"],
        )

    # -- annotations ---------------------------------------------------------

    def save_annotation(
        self, annotation: Annotation, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        """Insert or update one annotation and return the new edit counter."""
        existing = self.get_annotation(annotation.id)
        annotation.updated_at = utc_now()
        annotation.updated_by = self._actor_id
        if existing:
            annotation.revision = existing.revision + 1
        with self.db.transaction() as c:
            counter = self._bump_set(c, annotation.set_id, expected_counter)
            c.execute(
                "INSERT INTO annotations (id, set_id, class_key, side, geometry_type,"
                " coordinates_json, mask_rle, mask_bbox_json, presence, properties_json,"
                " visibility_score, ambiguous, locked, hidden, created_by, created_at,"
                " updated_by, updated_at, revision, deleted, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET class_key=excluded.class_key,"
                " side=excluded.side, geometry_type=excluded.geometry_type,"
                " coordinates_json=excluded.coordinates_json, mask_rle=excluded.mask_rle,"
                " mask_bbox_json=excluded.mask_bbox_json, presence=excluded.presence,"
                " properties_json=excluded.properties_json,"
                " visibility_score=excluded.visibility_score,"
                " ambiguous=excluded.ambiguous, locked=excluded.locked,"
                " hidden=excluded.hidden, updated_by=excluded.updated_by,"
                " updated_at=excluded.updated_at, revision=excluded.revision,"
                " deleted=excluded.deleted, notes=excluded.notes",
                (
                    annotation.id, annotation.set_id, annotation.class_key,
                    annotation.side, annotation.geometry_type,
                    _json(annotation.coordinates), annotation.mask_rle,
                    _json(annotation.mask_bbox), annotation.presence,
                    _json(annotation.properties), annotation.visibility_score,
                    int(annotation.ambiguous), int(annotation.locked),
                    int(annotation.hidden), annotation.created_by or self._actor_id,
                    annotation.created_at, annotation.updated_by, annotation.updated_at,
                    annotation.revision, int(annotation.deleted), annotation.notes,
                ),
            )
            event = AuditEvent.ANNOTATION_UPDATED if existing else AuditEvent.ANNOTATION_CREATED
            if existing and existing.presence != annotation.presence:
                event = AuditEvent.PRESENCE_CHANGED
            self.log(
                event, "annotation", annotation.id,
                before=self._audit_view(existing) if existing else None,
                after=self._audit_view(annotation),
                detail=(
                    f"{annotation.class_key} on side {annotation.side}, "
                    f"revision {annotation.revision}."
                ),
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def save_annotations(
        self, annotations: list, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        """Save several annotations in one transaction and one counter bump."""
        if not annotations:
            return self.set_edit_counter(annotations[0].set_id) if annotations else 0
        set_id = annotations[0].set_id
        existing_map = {a.id: a for a in self.list_annotations(set_id, include_deleted=True)}
        now = utc_now()
        with self.db.transaction() as c:
            counter = self._bump_set(c, set_id, expected_counter)
            for annotation in annotations:
                prev = existing_map.get(annotation.id)
                annotation.updated_at = now
                annotation.updated_by = self._actor_id
                if prev:
                    annotation.revision = prev.revision + 1
                c.execute(
                    "INSERT INTO annotations (id, set_id, class_key, side, geometry_type,"
                    " coordinates_json, mask_rle, mask_bbox_json, presence, properties_json,"
                    " visibility_score, ambiguous, locked, hidden, created_by, created_at,"
                    " updated_by, updated_at, revision, deleted, notes)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET class_key=excluded.class_key,"
                    " side=excluded.side, geometry_type=excluded.geometry_type,"
                    " coordinates_json=excluded.coordinates_json, mask_rle=excluded.mask_rle,"
                    " mask_bbox_json=excluded.mask_bbox_json, presence=excluded.presence,"
                    " properties_json=excluded.properties_json,"
                    " visibility_score=excluded.visibility_score,"
                    " ambiguous=excluded.ambiguous, locked=excluded.locked,"
                    " hidden=excluded.hidden, updated_by=excluded.updated_by,"
                    " updated_at=excluded.updated_at, revision=excluded.revision,"
                    " deleted=excluded.deleted, notes=excluded.notes",
                    (
                        annotation.id, annotation.set_id, annotation.class_key,
                        annotation.side, annotation.geometry_type,
                        _json(annotation.coordinates), annotation.mask_rle,
                        _json(annotation.mask_bbox), annotation.presence,
                        _json(annotation.properties), annotation.visibility_score,
                        int(annotation.ambiguous), int(annotation.locked),
                        int(annotation.hidden), annotation.created_by or self._actor_id,
                        annotation.created_at, annotation.updated_by,
                        annotation.updated_at, annotation.revision,
                        int(annotation.deleted), annotation.notes,
                    ),
                )
                self.log(
                    AuditEvent.ANNOTATION_UPDATED if prev else AuditEvent.ANNOTATION_CREATED,
                    "annotation", annotation.id,
                    before=self._audit_view(prev) if prev else None,
                    after=self._audit_view(annotation),
                    detail=f"{annotation.class_key} on side {annotation.side}.",
                    project_id=project_id, case_id=case_id, conn=c,
                )
        return counter

    def delete_annotation(
        self, annotation_id: str, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        """Soft delete. The row is kept so the history stays complete."""
        existing = self.get_annotation(annotation_id)
        if existing is None:
            raise KeyError(f"No annotation with identifier {annotation_id}")
        with self.db.transaction() as c:
            counter = self._bump_set(c, existing.set_id, expected_counter)
            c.execute(
                "UPDATE annotations SET deleted = 1, updated_at = ?, updated_by = ?,"
                " revision = revision + 1 WHERE id = ?",
                (utc_now(), self._actor_id, annotation_id),
            )
            self.log(
                AuditEvent.ANNOTATION_DELETED, "annotation", annotation_id,
                before=self._audit_view(existing), after={"deleted": True},
                detail=f"{existing.class_key} on side {existing.side} deleted.",
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def restore_annotation(
        self, annotation_id: str, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        existing = self.get_annotation(annotation_id)
        if existing is None:
            raise KeyError(f"No annotation with identifier {annotation_id}")
        with self.db.transaction() as c:
            counter = self._bump_set(c, existing.set_id, expected_counter)
            c.execute(
                "UPDATE annotations SET deleted = 0, updated_at = ?, updated_by = ?,"
                " revision = revision + 1 WHERE id = ?",
                (utc_now(), self._actor_id, annotation_id),
            )
            self.log(
                AuditEvent.ANNOTATION_RESTORED, "annotation", annotation_id,
                after={"deleted": False}, detail=f"{existing.class_key} restored.",
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def get_annotation(self, annotation_id: str) -> Annotation | None:
        row = self.db.query_one("SELECT * FROM annotations WHERE id = ?", (annotation_id,))
        return self._row_to_annotation(row) if row else None

    def list_annotations(self, set_id: str, include_deleted: bool = False) -> list:
        """Every annotation in a set, in an order defined by the stored rows.

        The timestamps carry milliseconds, so a construction that produces
        several annotations at once gives them all the same one. Sorting on the
        timestamp alone therefore leaves the engine to settle the tie however it
        likes, and that order reaches annotations.json, the rows of
        annotations.csv and the identifiers COCO hands out. The identifier
        breaks the tie so that the same stored set always exports the same
        bytes.
        """
        sql = "SELECT * FROM annotations WHERE set_id = ?"
        if not include_deleted:
            sql += " AND deleted = 0"
        sql += " ORDER BY created_at ASC, id ASC"
        return [self._row_to_annotation(r) for r in self.db.query(sql, (set_id,))]

    @staticmethod
    def _audit_view(annotation: Annotation | None) -> dict | None:
        """Compact view of an annotation for the audit log.

        Full coordinate lists would bloat the log without helping a reader, so
        the shape and the extent are recorded instead, and the full geometry
        stays in the revision snapshots.
        """
        if annotation is None:
            return None
        points = annotation.points()
        view = {
            "class_key": annotation.class_key,
            "side": annotation.side,
            "geometry_type": annotation.geometry_type,
            "presence": annotation.presence,
            "n_points": len(points),
            "revision": annotation.revision,
            "ambiguous": annotation.ambiguous,
            "deleted": annotation.deleted,
        }
        if points:
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            view["extent"] = [
                round(min(xs), 2), round(min(ys), 2),
                round(max(xs), 2), round(max(ys), 2),
            ]
            view["first_point"] = [round(points[0][0], 2), round(points[0][1], 2)]
        return view

    @staticmethod
    def _row_to_annotation(row) -> Annotation:
        return Annotation(
            id=row["id"], set_id=row["set_id"], class_key=row["class_key"],
            side=row["side"], geometry_type=row["geometry_type"],
            coordinates=_loads(row["coordinates_json"], []),
            mask_rle=row["mask_rle"], mask_bbox=_loads(row["mask_bbox_json"], []),
            presence=row["presence"], properties=_loads(row["properties_json"], {}),
            visibility_score=row["visibility_score"], ambiguous=bool(row["ambiguous"]),
            locked=bool(row["locked"]), hidden=bool(row["hidden"]),
            created_by=row["created_by"], created_at=row["created_at"],
            updated_by=row["updated_by"], updated_at=row["updated_at"],
            revision=int(row["revision"]), deleted=bool(row["deleted"]),
            notes=row["notes"],
        )

    # -- categorical labels and flags ---------------------------------------

    def set_grade(
        self, label: CategoricalLabel, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        existing = self.db.query_one(
            "SELECT * FROM categorical_labels WHERE set_id = ? AND key = ? AND side = ?",
            (label.set_id, label.key, label.side),
        )
        label.updated_at = utc_now()
        if existing:
            label.id = existing["id"]
            label.revision = int(existing["revision"]) + 1
        with self.db.transaction() as c:
            counter = self._bump_set(c, label.set_id, expected_counter)
            c.execute(
                "INSERT INTO categorical_labels (id, set_id, key, side, value,"
                " region_annotation_id, rationale, created_by, created_at, updated_at,"
                " revision) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(set_id, key, side) DO UPDATE SET value=excluded.value,"
                " region_annotation_id=excluded.region_annotation_id,"
                " rationale=excluded.rationale, updated_at=excluded.updated_at,"
                " revision=excluded.revision",
                (
                    label.id, label.set_id, label.key, label.side, label.value,
                    label.region_annotation_id, label.rationale,
                    label.created_by or self._actor_id, label.created_at,
                    label.updated_at, label.revision,
                ),
            )
            self.log(
                AuditEvent.GRADE_ASSIGNED, "categorical_label", label.id,
                before={"value": existing["value"]} if existing else None,
                after={"value": label.value, "rationale": label.rationale},
                detail=f"{label.key} on side {label.side} set to {label.value}.",
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def list_grades(self, set_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM categorical_labels WHERE set_id = ? ORDER BY key, side", (set_id,)
        )
        return [
            CategoricalLabel(
                id=r["id"], set_id=r["set_id"], key=r["key"], side=r["side"],
                value=r["value"], region_annotation_id=r["region_annotation_id"],
                rationale=r["rationale"], created_by=r["created_by"],
                created_at=r["created_at"], updated_at=r["updated_at"],
                revision=int(r["revision"]),
            )
            for r in rows
        ]

    def add_quality_flag(
        self, flag: QualityFlagRecord, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        with self.db.transaction() as c:
            counter = self._bump_set(c, flag.set_id, expected_counter)
            c.execute(
                "INSERT INTO quality_flags (id, set_id, flag, side, comment,"
                " created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                (
                    flag.id, flag.set_id, flag.flag, flag.side, flag.comment,
                    flag.created_by or self._actor_id, flag.created_at,
                ),
            )
            self.log(
                AuditEvent.QUALITY_FLAG_ADDED, "quality_flag", flag.id,
                after={"flag": flag.flag, "side": flag.side, "comment": flag.comment},
                detail=f"Quality flag {flag.flag} added.",
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def remove_quality_flag(
        self, flag_id: str, expected_counter: int | None = None,
        case_id: str = "", project_id: str = "",
    ) -> int:
        row = self.db.query_one("SELECT * FROM quality_flags WHERE id = ?", (flag_id,))
        if row is None:
            raise KeyError(f"No quality flag with identifier {flag_id}")
        with self.db.transaction() as c:
            counter = self._bump_set(c, row["set_id"], expected_counter)
            c.execute("DELETE FROM quality_flags WHERE id = ?", (flag_id,))
            self.log(
                AuditEvent.QUALITY_FLAG_REMOVED, "quality_flag", flag_id,
                before={"flag": row["flag"], "comment": row["comment"]},
                detail=f"Quality flag {row['flag']} removed.",
                project_id=project_id, case_id=case_id, conn=c,
            )
        return counter

    def list_quality_flags(self, set_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM quality_flags WHERE set_id = ? ORDER BY created_at, id", (set_id,)
        )
        return [
            QualityFlagRecord(
                id=r["id"], set_id=r["set_id"], flag=r["flag"], side=r["side"],
                comment=r["comment"], created_by=r["created_by"], created_at=r["created_at"],
            )
            for r in rows
        ]

    # -- aggregate load ------------------------------------------------------

    def load_case_data(self, case_id: str, annotator_id: str, kind: str = SetKind.PRIMARY) -> CaseData:
        case = self.get_case(case_id)
        if case is None:
            raise KeyError(f"No case with identifier {case_id}")
        aset = self.get_or_create_set(case_id, annotator_id, kind)
        return CaseData(
            case=case,
            annotation_set=aset,
            annotations=self.list_annotations(aset.id),
            categorical=self.list_grades(aset.id),
            quality_flags=self.list_quality_flags(aset.id),
        )

    def load_set_data(self, set_id: str) -> CaseData | None:
        aset = self.get_set(set_id)
        if aset is None:
            return None
        case = self.get_case(aset.case_id)
        if case is None:
            return None
        return CaseData(
            case=case,
            annotation_set=aset,
            annotations=self.list_annotations(set_id),
            categorical=self.list_grades(set_id),
            quality_flags=self.list_quality_flags(set_id),
        )

    # -- revisions -----------------------------------------------------------

    def create_revision(self, set_id: str, reason: str, author_id: str = "") -> Revision:
        """Freeze the current content of a set as an immutable snapshot.

        A returned case keeps its prior submission, and later edits appear as a
        new revision rather than replacing it (AC 008).
        """
        data = self.load_set_data(set_id)
        if data is None:
            raise KeyError(f"No annotation set with identifier {set_id}")
        row = self.db.query_one(
            "SELECT MAX(revision_no) AS n FROM revisions WHERE set_id = ?", (set_id,)
        )
        next_no = (int(row["n"]) + 1) if row and row["n"] is not None else 1

        snapshot = {
            "set": asdict(data.annotation_set),
            "case": {
                "id": data.case.id,
                "pseudonym": data.case.pseudonym,
                "state": data.case.state,
                "calibration": data.case.calibration.to_dict(),
                "laterality_confirmed": data.case.laterality_confirmed,
                "source_sha256": data.case.source.sha256,
            },
            "annotations": [a.to_dict() for a in data.annotations],
            "categorical_labels": [asdict(c) for c in data.categorical],
            "quality_flags": [asdict(q) for q in data.quality_flags],
        }
        payload = canonical_json(snapshot)
        revision = Revision(
            set_id=set_id, revision_no=next_no, snapshot_json=payload,
            author_id=author_id or self._actor_id, reason=reason,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO revisions (id, set_id, revision_no, snapshot_json,"
                " author_id, created_at, reason, sha256) VALUES (?,?,?,?,?,?,?,?)",
                (
                    revision.id, revision.set_id, revision.revision_no,
                    revision.snapshot_json, revision.author_id, revision.created_at,
                    revision.reason, revision.sha256,
                ),
            )
            c.execute(
                "UPDATE annotation_sets SET annotation_version = ? WHERE id = ?",
                (next_no, set_id),
            )
            self.log(
                AuditEvent.REVISION_CREATED, "revision", revision.id,
                after={"revision_no": next_no, "sha256": revision.sha256, "reason": reason},
                detail=f"Revision {next_no} created: {reason}",
                case_id=data.case.id, project_id=data.case.project_id, conn=c,
            )
        return revision

    def list_revisions(self, set_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM revisions WHERE set_id = ? ORDER BY revision_no ASC", (set_id,)
        )
        return [
            Revision(
                id=r["id"], set_id=r["set_id"], revision_no=int(r["revision_no"]),
                snapshot_json=r["snapshot_json"], author_id=r["author_id"],
                created_at=r["created_at"], reason=r["reason"], sha256=r["sha256"],
            )
            for r in rows
        ]

    def get_revision(self, set_id: str, revision_no: int) -> Revision | None:
        row = self.db.query_one(
            "SELECT * FROM revisions WHERE set_id = ? AND revision_no = ?",
            (set_id, revision_no),
        )
        if not row:
            return None
        return Revision(
            id=row["id"], set_id=row["set_id"], revision_no=int(row["revision_no"]),
            snapshot_json=row["snapshot_json"], author_id=row["author_id"],
            created_at=row["created_at"], reason=row["reason"], sha256=row["sha256"],
        )

    # -- submission and review ----------------------------------------------

    def submit_set(self, set_id: str, detail: str = "") -> Revision:
        data = self.load_set_data(set_id)
        if data is None:
            raise KeyError(f"No annotation set with identifier {set_id}")
        revision = self.create_revision(set_id, reason=detail or "Submitted for review")
        now = utc_now()
        with self.db.transaction() as c:
            c.execute(
                "UPDATE annotation_sets SET state = ?, submitted_at = ? WHERE id = ?",
                (CaseState.SUBMITTED.value, now, set_id),
            )
            c.execute(
                "UPDATE cases SET state = ? WHERE id = ?",
                (CaseState.SUBMITTED.value, data.case.id),
            )
            self.log(
                AuditEvent.SUBMITTED, "annotation_set", set_id,
                after={"revision_no": revision.revision_no},
                detail=detail or "Annotation set submitted for review.",
                case_id=data.case.id, project_id=data.case.project_id, conn=c,
            )
        return revision

    def record_review(self, review: Review, new_state: CaseState) -> Review:
        data = self.load_set_data(review.set_id)
        if data is None:
            raise KeyError(f"No annotation set with identifier {review.set_id}")
        now = utc_now()
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO reviews (id, set_id, reviewer_id, decision, summary,"
                " created_at, reviewed_revision) VALUES (?,?,?,?,?,?,?)",
                (
                    review.id, review.set_id, review.reviewer_id or self._actor_id,
                    review.decision, review.summary, review.created_at,
                    review.reviewed_revision,
                ),
            )
            for comment in review.comments:
                comment.review_id = review.id
                c.execute(
                    "INSERT INTO review_comments (id, review_id, annotation_id,"
                    " class_key, side, decision, text, created_by, created_at, resolved)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        comment.id, review.id, comment.annotation_id, comment.class_key,
                        comment.side, comment.decision, comment.text,
                        comment.created_by or self._actor_id, comment.created_at,
                        int(comment.resolved),
                    ),
                )
            c.execute(
                "UPDATE annotation_sets SET state = ?, reviewed_by = ?, reviewed_at = ?"
                " WHERE id = ?",
                (new_state.value, review.reviewer_id or self._actor_id, now, review.set_id),
            )
            c.execute("UPDATE cases SET state = ? WHERE id = ?", (new_state.value, data.case.id))

            event = {
                CaseState.ACCEPTED: AuditEvent.ACCEPTED,
                CaseState.RETURNED: AuditEvent.RETURNED,
                CaseState.ADJUDICATED: AuditEvent.ADJUDICATED,
            }.get(new_state, AuditEvent.REVIEW_COMMENT)
            self.log(
                event, "review", review.id,
                after={
                    "decision": review.decision,
                    "n_comments": len(review.comments),
                    "state": new_state.value,
                },
                detail=review.summary or f"Review decision: {review.decision}.",
                case_id=data.case.id, project_id=data.case.project_id, conn=c,
            )
        return review

    def list_reviews(self, set_id: str) -> list:
        rows = self.db.query(
            "SELECT * FROM reviews WHERE set_id = ? ORDER BY created_at DESC, id ASC", (set_id,)
        )
        out: list = []
        for r in rows:
            comments = self.db.query(
                "SELECT * FROM review_comments WHERE review_id = ? ORDER BY created_at, id",
                (r["id"],),
            )
            out.append(
                Review(
                    id=r["id"], set_id=r["set_id"], reviewer_id=r["reviewer_id"],
                    decision=r["decision"], summary=r["summary"], created_at=r["created_at"],
                    reviewed_revision=int(r["reviewed_revision"]),
                    comments=[
                        ReviewComment(
                            id=c["id"], review_id=c["review_id"],
                            annotation_id=c["annotation_id"], class_key=c["class_key"],
                            side=c["side"], decision=c["decision"], text=c["text"],
                            created_by=c["created_by"], created_at=c["created_at"],
                            resolved=bool(c["resolved"]),
                        )
                        for c in comments
                    ],
                )
            )
        return out

    def reopen_for_edit(self, set_id: str) -> None:
        data = self.load_set_data(set_id)
        if data is None:
            raise KeyError(f"No annotation set with identifier {set_id}")
        with self.db.transaction() as c:
            c.execute(
                "UPDATE annotation_sets SET state = ? WHERE id = ?",
                (CaseState.IN_PROGRESS.value, set_id),
            )
            c.execute(
                "UPDATE cases SET state = ? WHERE id = ?",
                (CaseState.IN_PROGRESS.value, data.case.id),
            )
            self.log(
                AuditEvent.CASE_STATE_CHANGED, "annotation_set", set_id,
                after={"state": CaseState.IN_PROGRESS.value},
                detail="A returned case was reopened for editing.",
                case_id=data.case.id, project_id=data.case.project_id, conn=c,
            )

    # -- measurements and texture -------------------------------------------

    def store_measurements(self, set_id: str, measurements: list) -> None:
        now = utc_now()
        with self.db.transaction() as c:
            c.execute("DELETE FROM measurements WHERE set_id = ?", (set_id,))
            for m in measurements:
                c.execute(
                    "INSERT INTO measurements (id, set_id, kind, side, payload_json,"
                    " computed_at, calc_version) VALUES (?,?,?,?,?,?,?)",
                    (
                        new_id("mea_"), set_id, m.kind, m.side, _json(m.to_dict()),
                        now, m.calculation_version,
                    ),
                )

    def load_measurements(self, set_id: str) -> list:
        from ..core.measurements import Measurement

        rows = self.db.query(
            "SELECT payload_json FROM measurements WHERE set_id = ?", (set_id,)
        )
        return [Measurement.from_dict(_loads(r["payload_json"], {})) for r in rows]

    def store_texture(self, set_id: str, results: list) -> None:
        from ..version import TEXTURE_VERSION

        now = utc_now()
        with self.db.transaction() as c:
            for r in results:
                c.execute(
                    "INSERT INTO texture_results (id, set_id, annotation_id,"
                    " payload_json, computed_at, version) VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(set_id, annotation_id) DO UPDATE SET"
                    " payload_json=excluded.payload_json,"
                    " computed_at=excluded.computed_at, version=excluded.version",
                    (
                        new_id("tex_"), set_id, r.region_id, _json(r.to_dict()),
                        now, TEXTURE_VERSION,
                    ),
                )

    def load_texture(self, set_id: str) -> list:
        rows = self.db.query(
            "SELECT payload_json FROM texture_results WHERE set_id = ?", (set_id,)
        )
        return [_loads(r["payload_json"], {}) for r in rows]

    # -- locks ---------------------------------------------------------------

    def acquire_lock(self, case_id: str, user: User) -> bool:
        """Take the editing lock for a case, or report who holds it (FR 015)."""
        from datetime import datetime, timezone

        now = utc_now()
        row = self.db.query_one("SELECT * FROM case_locks WHERE case_id = ?", (case_id,))
        if row:
            if row["session_id"] == self.session_id:
                self.db.execute(
                    "UPDATE case_locks SET heartbeat_at = ? WHERE case_id = ?",
                    (now, case_id),
                )
                return True
            try:
                last = datetime.fromisoformat(row["heartbeat_at"])
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - last).total_seconds()
            except (ValueError, TypeError):
                age = LOCK_STALE_SECONDS + 1
            if age <= LOCK_STALE_SECONDS:
                raise CaseLockedError(row["user_name"], row["acquired_at"])
            self.db.execute("DELETE FROM case_locks WHERE case_id = ?", (case_id,))

        try:
            self.db.execute(
                "INSERT INTO case_locks (case_id, user_id, user_name, session_id,"
                " acquired_at, heartbeat_at) VALUES (?,?,?,?,?,?)",
                (
                    case_id, user.id, user.display_name or user.username,
                    self.session_id, now, now,
                ),
            )
        except sqlite3.IntegrityError:
            current = self.db.query_one("SELECT * FROM case_locks WHERE case_id = ?", (case_id,))
            if current and current["session_id"] != self.session_id:
                raise CaseLockedError(current["user_name"], current["acquired_at"]) from None
        return True

    def heartbeat_lock(self, case_id: str) -> None:
        self.db.execute(
            "UPDATE case_locks SET heartbeat_at = ? WHERE case_id = ? AND session_id = ?",
            (utc_now(), case_id, self.session_id),
        )

    def release_lock(self, case_id: str) -> None:
        self.db.execute(
            "DELETE FROM case_locks WHERE case_id = ? AND session_id = ?",
            (case_id, self.session_id),
        )

    def release_all_locks(self) -> None:
        self.db.execute("DELETE FROM case_locks WHERE session_id = ?", (self.session_id,))

    def lock_holder(self, case_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM case_locks WHERE case_id = ?", (case_id,))
        if not row:
            return None
        return {
            "user_id": row["user_id"], "user_name": row["user_name"],
            "session_id": row["session_id"], "acquired_at": row["acquired_at"],
            "is_me": row["session_id"] == self.session_id,
        }

    # -- calibration sets ----------------------------------------------------

    def save_calibration_set(self, result: CalibrationSetResult) -> CalibrationSetResult:
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO calibration_sets (id, user_id, project_id, cases_completed,"
                " cases_required, metrics_json, passed, approved_by, approved_at,"
                " created_at, notes) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET cases_completed=excluded.cases_completed,"
                " metrics_json=excluded.metrics_json, passed=excluded.passed,"
                " approved_by=excluded.approved_by, approved_at=excluded.approved_at,"
                " notes=excluded.notes",
                (
                    result.id, result.user_id, result.project_id, result.cases_completed,
                    result.cases_required, result.metrics_json, int(result.passed),
                    result.approved_by, result.approved_at, result.created_at, result.notes,
                ),
            )
            self.log(
                AuditEvent.CALIBRATION_SET_APPROVED if result.approved_by else AuditEvent.POLICY_CHANGED,
                "calibration_set", result.id,
                after={"passed": result.passed, "approved_by": result.approved_by},
                detail=(
                    f"Calibration set recorded for user {result.user_id}, "
                    f"{'passed' if result.passed else 'not passed'}."
                ),
                project_id=result.project_id, conn=c,
            )
        return result

    def get_calibration_set(self, user_id: str, project_id: str = "") -> CalibrationSetResult | None:
        if project_id:
            row = self.db.query_one(
                "SELECT * FROM calibration_sets WHERE user_id = ? AND project_id = ?"
                " ORDER BY created_at DESC, id ASC LIMIT 1",
                (user_id, project_id),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM calibration_sets WHERE user_id = ?"
                " ORDER BY created_at DESC, id ASC LIMIT 1",
                (user_id,),
            )
        if not row:
            return None
        return CalibrationSetResult(
            id=row["id"], user_id=row["user_id"], project_id=row["project_id"],
            cases_completed=int(row["cases_completed"]),
            cases_required=int(row["cases_required"]), metrics_json=row["metrics_json"],
            passed=bool(row["passed"]), approved_by=row["approved_by"],
            approved_at=row["approved_at"], created_at=row["created_at"], notes=row["notes"],
        )

    # -- edit journal --------------------------------------------------------

    def push_journal(
        self, set_id: str, operation: str, undo: dict, redo: dict, label: str = ""
    ) -> int:
        """Record one reversible edit so undo survives a restart (FR 015)."""
        with self.db.transaction() as c:
            c.execute(
                "DELETE FROM edit_journal WHERE set_id = ? AND session_id = ? AND undone = 1",
                (set_id, self.session_id),
            )
            row = c.execute(
                "SELECT MAX(sequence) AS n FROM edit_journal WHERE set_id = ?", (set_id,)
            ).fetchone()
            sequence = (int(row["n"]) + 1) if row and row["n"] is not None else 1
            c.execute(
                "INSERT INTO edit_journal (set_id, session_id, sequence, operation,"
                " undo_json, redo_json, label, created_at, undone)"
                " VALUES (?,?,?,?,?,?,?,?,0)",
                (
                    set_id, self.session_id, sequence, operation, _json(undo),
                    _json(redo), label, utc_now(),
                ),
            )
        return sequence

    def peek_undo(self, set_id: str) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM edit_journal WHERE set_id = ? AND undone = 0"
            " ORDER BY sequence DESC LIMIT 1",
            (set_id,),
        )
        return dict(row) if row else None

    def peek_redo(self, set_id: str) -> dict | None:
        row = self.db.query_one(
            "SELECT * FROM edit_journal WHERE set_id = ? AND undone = 1"
            " ORDER BY sequence ASC LIMIT 1",
            (set_id,),
        )
        return dict(row) if row else None

    def mark_undone(self, journal_id: int, undone: bool) -> None:
        self.db.execute(
            "UPDATE edit_journal SET undone = ? WHERE id = ?", (int(undone), journal_id)
        )

    def clear_journal(self, set_id: str) -> None:
        self.db.execute("DELETE FROM edit_journal WHERE set_id = ?", (set_id,))

    def journal_depth(self, set_id: str) -> tuple:
        undo = self.db.query_one(
            "SELECT COUNT(*) AS n FROM edit_journal WHERE set_id = ? AND undone = 0", (set_id,)
        )
        redo = self.db.query_one(
            "SELECT COUNT(*) AS n FROM edit_journal WHERE set_id = ? AND undone = 1", (set_id,)
        )
        return (int(undo["n"]) if undo else 0, int(redo["n"]) if redo else 0)

    # -- export records ------------------------------------------------------

    def record_export(
        self, kind: str, destination: str, filters: dict, n_cases: int,
        sha256: str = "", project_id: str = "", status: str = "completed", notes: str = "",
    ) -> str:
        export_id = new_id("exp_")
        with self.db.transaction() as c:
            c.execute(
                "INSERT INTO export_records (id, project_id, kind, destination,"
                " filters_json, n_cases, sha256, created_by, created_at, status, notes)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    export_id, project_id, kind, destination, _json(filters), n_cases,
                    sha256, self._actor_id, utc_now(), status, notes,
                ),
            )
            event = {
                "completed": AuditEvent.EXPORT_COMPLETED,
                "failed": AuditEvent.EXPORT_FAILED,
                "bundle": AuditEvent.BUNDLE_CREATED,
            }.get(status, AuditEvent.EXPORT_COMPLETED)
            self.log(
                event, "export", export_id,
                after={
                    "kind": kind, "n_cases": n_cases, "sha256": sha256,
                    "destination": Path(destination).name if destination else "",
                },
                detail=notes or f"{kind} export of {n_cases} cases.",
                project_id=project_id, conn=c,
            )
        return export_id

    def list_exports(self, project_id: str = "", limit: int = 100) -> list:
        sql = "SELECT * FROM export_records"
        params: tuple = ()
        if project_id:
            sql += " WHERE project_id = ?"
            params = (project_id,)
        sql += " ORDER BY created_at DESC, id ASC LIMIT ?"
        return [dict(r) for r in self.db.query(sql, (*params, limit))]

    # -- statistics ----------------------------------------------------------

    def project_statistics(self, project_id: str) -> dict:
        counts = self.count_cases(project_id)
        total = sum(counts.values())
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM annotations a"
            " JOIN annotation_sets s ON a.set_id = s.id"
            " JOIN cases c ON s.case_id = c.id"
            " WHERE c.project_id = ? AND a.deleted = 0",
            (project_id,),
        )
        return {
            "total_cases": total,
            "by_state": counts,
            "total_annotations": int(row["n"]) if row else 0,
        }
