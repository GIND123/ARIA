"""Audit event taxonomy and tamper evident chaining (NFR 003, NFR 004).

Every record stores the digest of the record before it. Recomputing the chain
detects an edited or removed row anywhere in the history, which is what makes
"append only" a property that can be checked rather than a promise.

The store enforces append only at the database level as well, with triggers that
reject updates and deletes on the audit table.
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

GENESIS_HASH = "0" * 64


class AuditEvent(str, Enum):
    """The events that must appear in the log (NFR 003)."""

    # Session
    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    LOGOUT = "logout"
    SESSION_TIMEOUT = "session_timeout"
    PASSWORD_CHANGED = "password_changed"

    # Data
    IMPORT_STARTED = "import_started"
    IMPORT_COMPLETED = "import_completed"
    IMPORT_REJECTED = "import_rejected"
    DEIDENTIFY_APPLIED = "deidentify_applied"
    CASE_VIEWED = "case_viewed"
    CASE_ASSIGNED = "case_assigned"
    CASE_UNASSIGNED = "case_unassigned"
    CASE_STATE_CHANGED = "case_state_changed"
    CASE_ARCHIVED = "case_archived"

    # Annotation
    ANNOTATION_CREATED = "annotation_created"
    ANNOTATION_UPDATED = "annotation_updated"
    ANNOTATION_DELETED = "annotation_deleted"
    ANNOTATION_RESTORED = "annotation_restored"
    PRESENCE_CHANGED = "presence_changed"
    GRADE_ASSIGNED = "grade_assigned"
    QUALITY_FLAG_ADDED = "quality_flag_added"
    QUALITY_FLAG_REMOVED = "quality_flag_removed"
    CALIBRATION_SET = "calibration_set"
    CALIBRATION_VALIDATED = "calibration_validated"
    CALIBRATION_REJECTED = "calibration_rejected"
    LATERALITY_CONFIRMED = "laterality_confirmed"

    # Workflow
    SUBMITTED = "submitted"
    REVIEW_STARTED = "review_started"
    REVIEW_COMMENT = "review_comment"
    ACCEPTED = "accepted"
    RETURNED = "returned"
    ADJUDICATED = "adjudicated"
    REVISION_CREATED = "revision_created"

    # Export
    EXPORT_STARTED = "export_started"
    EXPORT_COMPLETED = "export_completed"
    EXPORT_FAILED = "export_failed"
    BUNDLE_CREATED = "bundle_created"

    # Administration
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    USER_DEACTIVATED = "user_deactivated"
    ROLE_CHANGED = "role_changed"
    PROJECT_CREATED = "project_created"
    PROJECT_UPDATED = "project_updated"
    SCHEMA_CHANGED = "schema_changed"
    THRESHOLD_CHANGED = "threshold_changed"
    DEID_PROFILE_CHANGED = "deid_profile_changed"
    POLICY_CHANGED = "policy_changed"
    CALIBRATION_SET_APPROVED = "calibration_set_approved"
    DIAGNOSTICS_RUN = "diagnostics_run"
    DATABASE_MIGRATED = "database_migrated"
    INTEGRITY_CHECK = "integrity_check"

    @property
    def display(self) -> str:
        return self.value.replace("_", " ").capitalize()


#: Events an auditor may read but that never carry clinical content.
ADMINISTRATIVE_EVENTS = {
    AuditEvent.USER_CREATED, AuditEvent.USER_UPDATED, AuditEvent.USER_DEACTIVATED,
    AuditEvent.ROLE_CHANGED, AuditEvent.PROJECT_CREATED, AuditEvent.PROJECT_UPDATED,
    AuditEvent.SCHEMA_CHANGED, AuditEvent.THRESHOLD_CHANGED,
    AuditEvent.DEID_PROFILE_CHANGED, AuditEvent.POLICY_CHANGED,
}


def canonical_json(payload) -> str:
    """Stable JSON used for hashing.

    Keys are sorted and separators are fixed so the same content always produces
    the same digest, regardless of dictionary insertion order.
    """
    if payload is None or payload == "":
        return ""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return payload
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_record_hash(
    sequence: int,
    timestamp: str,
    actor_id: str,
    event: str,
    object_type: str,
    object_id: str,
    before_json: str,
    after_json: str,
    detail: str,
    previous_hash: str,
) -> str:
    """Digest of one audit record, chained to the record before it."""
    parts = [
        str(sequence),
        timestamp,
        actor_id,
        event,
        object_type,
        object_id,
        canonical_json(before_json),
        canonical_json(after_json),
        detail or "",
        previous_hash or GENESIS_HASH,
    ]
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def verify_chain(records) -> dict:
    """Recompute the whole chain and report the first break.

    ``records`` is an iterable of objects with the audit record fields, ordered
    by sequence.
    """
    previous = GENESIS_HASH
    checked = 0
    for record in records:
        expected_prev = previous
        if (record.previous_hash or GENESIS_HASH) != expected_prev:
            return {
                "valid": False,
                "records_checked": checked,
                "broken_at_sequence": record.sequence,
                "reason": (
                    "The stored previous hash does not match the hash of the "
                    "preceding record, so a record was altered or removed."
                ),
            }
        recomputed = compute_record_hash(
            record.sequence,
            record.timestamp,
            record.actor_id,
            record.event,
            record.object_type,
            record.object_id,
            record.before_json,
            record.after_json,
            record.detail,
            record.previous_hash,
        )
        if recomputed != record.record_hash:
            return {
                "valid": False,
                "records_checked": checked,
                "broken_at_sequence": record.sequence,
                "reason": (
                    "The recomputed digest does not match the stored digest, so "
                    "the content of this record was altered."
                ),
            }
        previous = record.record_hash
        checked += 1
    return {
        "valid": True,
        "records_checked": checked,
        "head_hash": previous,
        "reason": "Every record matches its recomputed digest and the chain is continuous.",
    }


def redact_for_auditor(payload: dict) -> dict:
    """Strip clinical geometry from an audit payload.

    An auditor reads immutable histories without editing clinical content. They
    still need to see that an object changed and who changed it, so the shape of
    the change is preserved while coordinate lists are replaced by counts.
    """
    if not isinstance(payload, dict):
        return payload
    out: dict = {}
    for key, value in payload.items():
        if key in ("coordinates", "mask_rle"):
            if isinstance(value, list):
                out[key] = f"<{len(value) // 2} points withheld>"
            else:
                out[key] = "<mask withheld>"
        elif isinstance(value, dict):
            out[key] = redact_for_auditor(value)
        else:
            out[key] = value
    return out
