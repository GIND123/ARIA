"""SQLite schema, connection handling and migrations.

Durability
----------
The connection runs with ``journal_mode = WAL`` and ``synchronous = FULL``. WAL
lets a reader work while a writer commits, and FULL forces each commit to the
device before it is acknowledged. Together they mean a committed annotation
survives a power loss, which is the property the autosave design rests on
(FR 015, NFR 009).

Append only history
-------------------
The audit table and the revision table carry triggers that reject every UPDATE
and DELETE. Append only is therefore a property of the database file itself, not
a convention the application code is trusted to follow (NFR 004).
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..version import DB_SCHEMA_VERSION

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id                     TEXT PRIMARY KEY,
    username               TEXT NOT NULL UNIQUE,
    display_name           TEXT NOT NULL DEFAULT '',
    role                   TEXT NOT NULL,
    pseudonym              TEXT NOT NULL DEFAULT '',
    password_hash          TEXT NOT NULL DEFAULT '',
    password_salt          TEXT NOT NULL DEFAULT '',
    active                 INTEGER NOT NULL DEFAULT 1,
    created_at             TEXT NOT NULL,
    last_login_at          TEXT,
    calibration_passed     INTEGER NOT NULL DEFAULT 0,
    calibration_passed_at  TEXT,
    calibration_approved_by TEXT,
    must_change_password   INTEGER NOT NULL DEFAULT 0,
    failed_logins          INTEGER NOT NULL DEFAULT 0,
    locked_until           TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    schema_json   TEXT NOT NULL DEFAULT '{}',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    deid_profile  TEXT NOT NULL DEFAULT 'aria_default',
    archived      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cases (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pseudonym               TEXT NOT NULL,
    source_json             TEXT NOT NULL DEFAULT '{}',
    calibration_json        TEXT NOT NULL DEFAULT '{}',
    display_settings_json   TEXT NOT NULL DEFAULT '{}',
    deid_report_json        TEXT NOT NULL DEFAULT '{}',
    state                   TEXT NOT NULL DEFAULT 'unassigned',
    assigned_to             TEXT REFERENCES users(id) ON DELETE SET NULL,
    laterality_confirmed    INTEGER NOT NULL DEFAULT 0,
    laterality_confirmed_by TEXT,
    laterality_confirmed_at TEXT,
    laterality_note         TEXT NOT NULL DEFAULT '',
    split                   TEXT NOT NULL DEFAULT '',
    imported_by             TEXT NOT NULL DEFAULT '',
    imported_at             TEXT NOT NULL,
    duplicate_target        INTEGER NOT NULL DEFAULT 0,
    archived                INTEGER NOT NULL DEFAULT 0,
    source_sha256           TEXT NOT NULL DEFAULT '',
    UNIQUE (project_id, pseudonym)
);
CREATE INDEX IF NOT EXISTS idx_cases_project ON cases(project_id, state);
CREATE INDEX IF NOT EXISTS idx_cases_assigned ON cases(assigned_to);
CREATE INDEX IF NOT EXISTS idx_cases_sha ON cases(source_sha256);

CREATE TABLE IF NOT EXISTS annotation_sets (
    id                 TEXT PRIMARY KEY,
    case_id            TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    annotator_id       TEXT NOT NULL DEFAULT '',
    kind               TEXT NOT NULL DEFAULT 'primary',
    schema_version     TEXT NOT NULL DEFAULT '1.0.0',
    annotation_version INTEGER NOT NULL DEFAULT 1,
    state              TEXT NOT NULL DEFAULT 'in_progress',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    submitted_at       TEXT,
    reviewed_by        TEXT,
    reviewed_at        TEXT,
    edit_counter       INTEGER NOT NULL DEFAULT 0,
    notes              TEXT NOT NULL DEFAULT '',
    UNIQUE (case_id, annotator_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_sets_case ON annotation_sets(case_id);
CREATE INDEX IF NOT EXISTS idx_sets_annotator ON annotation_sets(annotator_id, state);

CREATE TABLE IF NOT EXISTS annotations (
    id               TEXT PRIMARY KEY,
    set_id           TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    class_key        TEXT NOT NULL,
    side             TEXT NOT NULL DEFAULT 'NA',
    geometry_type    TEXT NOT NULL,
    coordinates_json TEXT NOT NULL DEFAULT '[]',
    mask_rle         TEXT NOT NULL DEFAULT '',
    mask_bbox_json   TEXT NOT NULL DEFAULT '[]',
    presence         TEXT NOT NULL DEFAULT 'present',
    properties_json  TEXT NOT NULL DEFAULT '{}',
    visibility_score INTEGER,
    ambiguous        INTEGER NOT NULL DEFAULT 0,
    locked           INTEGER NOT NULL DEFAULT 0,
    hidden           INTEGER NOT NULL DEFAULT 0,
    created_by       TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_by       TEXT NOT NULL DEFAULT '',
    updated_at       TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    deleted          INTEGER NOT NULL DEFAULT 0,
    notes            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ann_set ON annotations(set_id, deleted);
CREATE INDEX IF NOT EXISTS idx_ann_class ON annotations(set_id, class_key, side);

CREATE TABLE IF NOT EXISTS categorical_labels (
    id                   TEXT PRIMARY KEY,
    set_id               TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    key                  TEXT NOT NULL,
    side                 TEXT NOT NULL DEFAULT 'NA',
    value                TEXT NOT NULL,
    region_annotation_id TEXT,
    rationale            TEXT NOT NULL DEFAULT '',
    created_by           TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL,
    updated_at           TEXT NOT NULL,
    revision             INTEGER NOT NULL DEFAULT 1,
    UNIQUE (set_id, key, side)
);

CREATE TABLE IF NOT EXISTS quality_flags (
    id         TEXT PRIMARY KEY,
    set_id     TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    flag       TEXT NOT NULL,
    side       TEXT NOT NULL DEFAULT 'NA',
    comment    TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_flags_set ON quality_flags(set_id);

CREATE TABLE IF NOT EXISTS measurements (
    id             TEXT PRIMARY KEY,
    set_id         TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL,
    side           TEXT NOT NULL,
    payload_json   TEXT NOT NULL,
    computed_at    TEXT NOT NULL,
    calc_version   TEXT NOT NULL,
    UNIQUE (set_id, kind, side)
);

CREATE TABLE IF NOT EXISTS texture_results (
    id            TEXT PRIMARY KEY,
    set_id        TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    annotation_id TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    computed_at   TEXT NOT NULL,
    version       TEXT NOT NULL,
    UNIQUE (set_id, annotation_id)
);

CREATE TABLE IF NOT EXISTS revisions (
    id            TEXT PRIMARY KEY,
    set_id        TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    revision_no   INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    author_id     TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    reason        TEXT NOT NULL DEFAULT '',
    sha256        TEXT NOT NULL DEFAULT '',
    UNIQUE (set_id, revision_no)
);
CREATE INDEX IF NOT EXISTS idx_rev_set ON revisions(set_id, revision_no);

CREATE TABLE IF NOT EXISTS reviews (
    id                 TEXT PRIMARY KEY,
    set_id             TEXT NOT NULL REFERENCES annotation_sets(id) ON DELETE CASCADE,
    reviewer_id        TEXT NOT NULL DEFAULT '',
    decision           TEXT NOT NULL,
    summary            TEXT NOT NULL DEFAULT '',
    created_at         TEXT NOT NULL,
    reviewed_revision  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_reviews_set ON reviews(set_id);

CREATE TABLE IF NOT EXISTS review_comments (
    id            TEXT PRIMARY KEY,
    review_id     TEXT NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    annotation_id TEXT NOT NULL DEFAULT '',
    class_key     TEXT NOT NULL DEFAULT '',
    side          TEXT NOT NULL DEFAULT 'NA',
    decision      TEXT NOT NULL DEFAULT 'comment',
    text          TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    resolved      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS calibration_sets (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id   TEXT NOT NULL DEFAULT '',
    cases_completed INTEGER NOT NULL DEFAULT 0,
    cases_required  INTEGER NOT NULL DEFAULT 0,
    metrics_json TEXT NOT NULL DEFAULT '{}',
    passed       INTEGER NOT NULL DEFAULT 0,
    approved_by  TEXT,
    approved_at  TEXT,
    created_at   TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS case_locks (
    case_id    TEXT PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
    user_id    TEXT NOT NULL,
    user_name  TEXT NOT NULL DEFAULT '',
    session_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS edit_journal (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id      TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    sequence    INTEGER NOT NULL,
    operation   TEXT NOT NULL,
    undo_json   TEXT NOT NULL DEFAULT '{}',
    redo_json   TEXT NOT NULL DEFAULT '{}',
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    undone      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_journal_set ON edit_journal(set_id, sequence);

CREATE TABLE IF NOT EXISTS audit_log (
    id            TEXT PRIMARY KEY,
    sequence      INTEGER NOT NULL UNIQUE,
    timestamp     TEXT NOT NULL,
    actor_id      TEXT NOT NULL DEFAULT '',
    actor_name    TEXT NOT NULL DEFAULT '',
    event         TEXT NOT NULL,
    object_type   TEXT NOT NULL DEFAULT '',
    object_id     TEXT NOT NULL DEFAULT '',
    project_id    TEXT NOT NULL DEFAULT '',
    case_id       TEXT NOT NULL DEFAULT '',
    before_json   TEXT NOT NULL DEFAULT '',
    after_json    TEXT NOT NULL DEFAULT '',
    detail        TEXT NOT NULL DEFAULT '',
    previous_hash TEXT NOT NULL DEFAULT '',
    record_hash   TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_log(event);

CREATE TABLE IF NOT EXISTS export_records (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL,
    destination TEXT NOT NULL DEFAULT '',
    filters_json TEXT NOT NULL DEFAULT '{}',
    n_cases     INTEGER NOT NULL DEFAULT 0,
    sha256      TEXT NOT NULL DEFAULT '',
    created_by  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'completed',
    notes       TEXT NOT NULL DEFAULT ''
);
"""

#: Triggers that make the history tables append only at the storage layer.
APPEND_ONLY_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'The audit log is append only and cannot be updated.');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'The audit log is append only and cannot be deleted from.');
END;

CREATE TRIGGER IF NOT EXISTS revisions_no_update
BEFORE UPDATE ON revisions
BEGIN
    SELECT RAISE(ABORT, 'A submitted revision is immutable and cannot be updated.');
END;

CREATE TRIGGER IF NOT EXISTS revisions_no_delete
BEFORE DELETE ON revisions
WHEN (SELECT COUNT(*) FROM annotation_sets WHERE id = OLD.set_id) > 0
BEGIN
    SELECT RAISE(ABORT, 'A submitted revision is immutable and cannot be deleted.');
END;
"""


class Database:
    """Owns the SQLite connection for one process.

    A single connection is shared and guarded by a lock. SQLite handles
    concurrency between processes through WAL; within the process, serialising
    writes keeps the audit sequence and the hash chain consistent.
    """

    def __init__(self, path, read_only: bool = False):
        self.path = Path(path)
        self.read_only = read_only
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None

    # -- connection ----------------------------------------------------------

    def connect(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        self.path.parent.mkdir(parents=True, exist_ok=True)
        uri = f"file:{self.path.as_posix()}"
        if self.read_only:
            uri += "?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            check_same_thread=False,
            isolation_level=None,        # explicit transaction control
            timeout=30.0,
        )
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode = WAL")
        # FULL is the setting that makes a committed change survive a power cut.
        cur.execute("PRAGMA synchronous = FULL")
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA busy_timeout = 30000")
        cur.execute("PRAGMA temp_store = MEMORY")
        cur.execute("PRAGMA cache_size = -40000")   # about 40 MB of page cache
        cur.execute("PRAGMA wal_autocheckpoint = 512")
        cur.close()
        self._conn = conn
        return conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass
                self._conn.close()
                self._conn = None

    # -- transactions --------------------------------------------------------

    @contextmanager
    def transaction(self, immediate: bool = True):
        """Run a block inside one transaction.

        ``IMMEDIATE`` takes the write lock at the start rather than on first
        write, which turns a potential mid transaction lock failure into a clean
        wait at the beginning.
        """
        conn = self.connect()
        with self._lock:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    @contextmanager
    def cursor(self):
        conn = self.connect()
        with self._lock:
            cur = conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        conn = self.connect()
        with self._lock:
            return conn.execute(sql, params)

    def query(self, sql: str, params=()) -> list:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def query_one(self, sql: str, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- schema --------------------------------------------------------------

    def initialise(self) -> None:
        """Create the schema and bring an existing file up to date."""
        conn = self.connect()
        with self._lock:
            conn.executescript(SCHEMA)
            conn.executescript(APPEND_ONLY_TRIGGERS)
        current = self.get_meta("db_schema_version")
        if current is None:
            self.set_meta("db_schema_version", str(DB_SCHEMA_VERSION))
            self.set_meta("created_by_version", "1.0.0")
        else:
            self.migrate(int(current))

    def migrate(self, from_version: int) -> list:
        """Apply migrations in order.

        Existing annotations remain readable after a schema update (FR 053), so
        migrations only add tables and columns, and never drop or retype a
        column that annotations are stored in.
        """
        applied: list = []
        version = from_version
        while version < DB_SCHEMA_VERSION:
            step = MIGRATIONS.get(version + 1)
            if step is None:
                break
            with self.transaction() as conn:
                for statement in step["statements"]:
                    conn.execute(statement)
            version += 1
            self.set_meta("db_schema_version", str(version))
            applied.append(step["description"])
        return applied

    def schema_version(self) -> int:
        value = self.get_meta("db_schema_version")
        return int(value) if value else 0

    # -- metadata ------------------------------------------------------------

    def get_meta(self, key: str, default=None):
        row = self.query_one("SELECT value FROM app_meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO app_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # -- maintenance ---------------------------------------------------------

    def integrity_check(self) -> dict:
        """Run the storage level integrity checks."""
        result = {"ok": True, "checks": {}}
        try:
            rows = self.query("PRAGMA integrity_check")
            value = rows[0][0] if rows else "unknown"
            result["checks"]["integrity_check"] = value
            if value != "ok":
                result["ok"] = False
        except sqlite3.Error as exc:
            result["ok"] = False
            result["checks"]["integrity_check"] = f"failed: {exc}"

        try:
            rows = self.query("PRAGMA foreign_key_check")
            result["checks"]["foreign_key_violations"] = len(rows)
            if rows:
                result["ok"] = False
        except sqlite3.Error as exc:
            result["ok"] = False
            result["checks"]["foreign_key_check"] = f"failed: {exc}"

        try:
            rows = self.query("PRAGMA quick_check")
            result["checks"]["quick_check"] = rows[0][0] if rows else "unknown"
        except sqlite3.Error:
            pass
        return result

    def checkpoint(self) -> None:
        try:
            self.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass

    def vacuum(self) -> None:
        conn = self.connect()
        with self._lock:
            conn.execute("VACUUM")

    def backup_to(self, destination) -> Path:
        """Take a consistent online backup.

        The SQLite backup API copies a live database safely, so a backup can be
        taken while the application is running.
        """
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        with self._lock:
            target = sqlite3.connect(str(destination))
            try:
                source.backup(target)
                target.execute("PRAGMA journal_mode = DELETE")
            finally:
                target.close()
        return destination

    def table_counts(self) -> dict:
        tables = [
            "users", "projects", "cases", "annotation_sets", "annotations",
            "categorical_labels", "quality_flags", "measurements",
            "texture_results", "revisions", "reviews", "audit_log",
        ]
        out: dict = {}
        for t in tables:
            try:
                row = self.query_one(f"SELECT COUNT(*) AS n FROM {t}")
                out[t] = int(row["n"]) if row else 0
            except sqlite3.Error:
                out[t] = -1
        return out

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total


#: Migration steps keyed by the version they produce. Each step only adds
#: structure, so annotations written by an earlier version stay readable.
MIGRATIONS: dict = {
    # Version 1 is the initial schema created by SCHEMA above. Later versions
    # are added here as the product evolves, for example:
    # 2: {
    #     "description": "Add a per case acquisition note column",
    #     "statements": [
    #         "ALTER TABLE cases ADD COLUMN acquisition_note TEXT NOT NULL DEFAULT ''",
    #     ],
    # },
}


def open_database(path, read_only: bool = False) -> Database:
    db = Database(path, read_only=read_only)
    db.connect()
    if not read_only:
        db.initialise()
    return db
