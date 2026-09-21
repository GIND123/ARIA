"""Pure domain records.

These dataclasses carry no database and no Qt dependency, which keeps the
measurement engine, the validators and the exporters testable on their own. The
store layer maps them to and from SQLite rows.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

from .schema import (
    CaseState,
    GeometryType,
    MCIGrade,
    Presence,
    QualityFlag,
    Side,
    get_class,
)
from .units import Calibration


def new_id(prefix: str = "") -> str:
    """A collision resistant identifier that is readable in an export."""
    raw = uuid.uuid4().hex[:20]
    return f"{prefix}{raw}" if prefix else raw


def utc_now() -> str:
    """Timestamps are stored in ISO 8601 with an explicit UTC offset so that an
    audit trail read in another timezone stays unambiguous."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Role(str):
    """Role identifiers. Held as plain strings so they serialise transparently."""

    ANNOTATOR = "annotator"
    REVIEWER = "clinical_reviewer"
    ADMIN = "project_administrator"
    DATA_MANAGER = "data_manager"
    AUDITOR = "auditor"

    ALL = (ANNOTATOR, REVIEWER, ADMIN, DATA_MANAGER, AUDITOR)

    DISPLAY = {
        ANNOTATOR: "Annotator",
        REVIEWER: "Clinical reviewer",
        ADMIN: "Project administrator",
        DATA_MANAGER: "Data manager",
        AUDITOR: "Auditor",
    }


@dataclass
class User:
    id: str = field(default_factory=lambda: new_id("usr_"))
    username: str = ""
    display_name: str = ""
    role: str = Role.ANNOTATOR
    #: Pseudonym written into exports in place of the account name (FR 047).
    pseudonym: str = ""
    password_hash: str = ""
    password_salt: str = ""
    active: bool = True
    created_at: str = field(default_factory=utc_now)
    last_login_at: str | None = None
    #: An annotator must pass the calibration set before production access
    #: (FR 046).
    calibration_passed: bool = False
    calibration_passed_at: str | None = None
    calibration_approved_by: str | None = None
    must_change_password: bool = False
    failed_logins: int = 0
    locked_until: str | None = None

    @property
    def role_display(self) -> str:
        return Role.DISPLAY.get(self.role, self.role)


@dataclass
class Project:
    id: str = field(default_factory=lambda: new_id("prj_"))
    name: str = ""
    description: str = ""
    schema_json: str = "{}"
    created_by: str = ""
    created_at: str = field(default_factory=utc_now)
    #: Name of the administrator approved deidentification profile (FR 005).
    deid_profile: str = "aria_default"
    archived: bool = False


@dataclass
class SourceImage:
    """Everything ARIA knows about the file on disk and its pixels.

    The original file is never modified. ``sha256`` links the working
    representation back to those exact bytes (FR 002).
    """

    sha256: str = ""
    source_format: str = ""          # dicom or png
    original_filename: str = ""
    stored_path: str = ""            # path of the retained original
    byte_size: int = 0
    rows: int = 0
    columns: int = 0
    bits_stored: int = 0
    bits_allocated: int = 0
    photometric_interpretation: str = ""
    samples_per_pixel: int = 1
    pixel_representation: int = 0
    transfer_syntax_uid: str = ""
    sop_instance_uid: str = ""
    study_instance_uid: str = ""
    series_instance_uid: str = ""
    sop_class_uid: str = ""
    rescale_slope: float = 1.0
    rescale_intercept: float = 0.0
    window_centre: float | None = None
    window_width: float | None = None
    modality: str = ""
    manufacturer: str = ""
    manufacturer_model: str = ""
    image_laterality: str = ""
    acquisition_date: str = ""
    #: True when the source carried colour channels that are converted for
    #: display only, with the original channels preserved (FR 006).
    converted_for_display: bool = False
    original_channel_description: str = ""
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceImage":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


@dataclass
class Case:
    id: str = field(default_factory=lambda: new_id("cas_"))
    project_id: str = ""
    #: Case pseudonym. Never a patient identifier (FR 047, FR 052).
    pseudonym: str = ""
    source: SourceImage = field(default_factory=SourceImage)
    calibration: Calibration = field(default_factory=Calibration)
    state: str = CaseState.UNASSIGNED.value
    assigned_to: str | None = None
    #: Explicit confirmation of anatomical right and left when the source does
    #: not carry reliable laterality (FR 010).
    laterality_confirmed: bool = False
    laterality_confirmed_by: str | None = None
    laterality_confirmed_at: str | None = None
    laterality_note: str = ""
    #: Dataset split label used when exporting by split (FR 052).
    split: str = ""
    imported_by: str = ""
    imported_at: str = field(default_factory=utc_now)
    #: Set when the data manager marks the case as a duplicate annotation case.
    duplicate_target: bool = False
    archived: bool = False

    @property
    def state_enum(self) -> CaseState:
        return CaseState(self.state)

    @property
    def shape(self) -> tuple:
        return (self.source.rows, self.source.columns)


@dataclass
class Annotation:
    """One geometric object drawn by one annotator.

    Coordinates are always original image pixels (FR 013). ``presence`` records
    missing anatomy explicitly so absence is never encoded as a zero coordinate
    (FR 017).
    """

    id: str = field(default_factory=lambda: new_id("ann_"))
    set_id: str = ""
    class_key: str = ""
    side: str = Side.NONE.value
    geometry_type: str = GeometryType.POINT.value
    #: Flat coordinate list, x then y, in original image pixels.
    coordinates: list = field(default_factory=list)
    #: Raster mask payload for brush classes: run length encoded, with the
    #: bounding box that locates it in the full image.
    mask_rle: str = ""
    mask_bbox: list = field(default_factory=list)
    presence: str = Presence.PRESENT.value
    properties: dict = field(default_factory=dict)
    visibility_score: int | None = None     # 0 to 4, FR 018
    ambiguous: bool = False
    locked: bool = False
    hidden: bool = False
    created_by: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_by: str = ""
    updated_at: str = field(default_factory=utc_now)
    revision: int = 1
    deleted: bool = False
    notes: str = ""

    @property
    def side_enum(self) -> Side:
        return Side(self.side)

    @property
    def presence_enum(self) -> Presence:
        return Presence(self.presence)

    @property
    def label_class(self):
        return get_class(self.class_key)

    def points(self) -> list:
        """Coordinates as ``(x, y)`` pairs."""
        c = self.coordinates
        return [(float(c[i]), float(c[i + 1])) for i in range(0, len(c) - 1, 2)]

    def set_points(self, points: Iterable) -> None:
        flat: list = []
        for x, y in points:
            flat.extend((float(x), float(y)))
        self.coordinates = flat

    @property
    def is_present(self) -> bool:
        return self.presence == Presence.PRESENT.value and not self.deleted

    @property
    def is_assessable(self) -> bool:
        """An object contributes to measurements only when it is present and is
        not flagged as unassessable."""
        return self.is_present and self.presence_enum not in (
            Presence.NOT_ASSESSABLE,
            Presence.NOT_VISIBLE,
            Presence.ABSENT_ANATOMY,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Annotation":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


@dataclass
class CategoricalLabel:
    """A qualitative grade, for example the Klemetti grade per side (FR 027)."""

    id: str = field(default_factory=lambda: new_id("cat_"))
    set_id: str = ""
    key: str = "mci_grade"
    side: str = Side.NONE.value
    value: str = MCIGrade.UNCERTAIN.value
    #: Identifier of the region annotation the grade was read from.
    region_annotation_id: str | None = None
    rationale: str = ""
    created_by: str = ""
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    revision: int = 1

    @property
    def grade(self) -> MCIGrade:
        return MCIGrade(self.value)


@dataclass
class QualityFlagRecord:
    id: str = field(default_factory=lambda: new_id("qfl_"))
    set_id: str = ""
    flag: str = QualityFlag.OTHER.value
    side: str = Side.NONE.value
    comment: str = ""
    created_by: str = ""
    created_at: str = field(default_factory=utc_now)

    @property
    def flag_enum(self) -> QualityFlag:
        return QualityFlag(self.flag)


class SetKind(str):
    PRIMARY = "primary"
    DUPLICATE = "duplicate"
    ADJUDICATION = "adjudication"

    ALL = (PRIMARY, DUPLICATE, ADJUDICATION)


@dataclass
class AnnotationSet:
    """One annotator's complete pass over one case.

    Blinded duplicate annotation works by keeping a second set of kind
    ``duplicate`` that no other annotator can read until both are submitted
    (FR 043).
    """

    id: str = field(default_factory=lambda: new_id("set_"))
    case_id: str = ""
    annotator_id: str = ""
    kind: str = SetKind.PRIMARY
    schema_version: str = "1.0.0"
    annotation_version: int = 1
    state: str = CaseState.IN_PROGRESS.value
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    submitted_at: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    #: Incremented on every material change; used for optimistic locking so two
    #: users cannot silently overwrite one another (FR 015).
    edit_counter: int = 0
    notes: str = ""

    @property
    def state_enum(self) -> CaseState:
        return CaseState(self.state)


@dataclass
class CaseData:
    """A case together with one annotation set and all of its content.

    This is the unit the measurement engine, the validator and the exporters
    consume.
    """

    case: Case
    annotation_set: AnnotationSet
    annotations: list = field(default_factory=list)
    categorical: list = field(default_factory=list)
    quality_flags: list = field(default_factory=list)

    # -- lookups -------------------------------------------------------------

    def live_annotations(self) -> list:
        return [a for a in self.annotations if not a.deleted]

    def by_class(self, class_key: str, side: Side | None = None) -> list:
        out = [a for a in self.live_annotations() if a.class_key == class_key]
        if side is not None:
            out = [a for a in out if a.side == side.value]
        return out

    def first(self, class_key: str, side: Side | None = None) -> Annotation | None:
        items = self.by_class(class_key, side)
        return items[0] if items else None

    def present(self, class_key: str, side: Side | None = None) -> Annotation | None:
        """First annotation of a class that actually carries usable geometry."""
        for a in self.by_class(class_key, side):
            if a.is_assessable and a.coordinates:
                return a
        return None

    def grade(self, key: str, side: Side) -> CategoricalLabel | None:
        for c in self.categorical:
            if c.key == key and c.side == side.value:
                return c
        return None

    def flags(self) -> list:
        return list(self.quality_flags)

    def sides_in_use(self) -> list:
        return [Side.RIGHT, Side.LEFT]


@dataclass
class Revision:
    """An immutable snapshot taken at submission and at adjudication.

    A returned case keeps its prior submission and records subsequent edits as a
    new revision (AC 008).
    """

    id: str = field(default_factory=lambda: new_id("rev_"))
    set_id: str = ""
    revision_no: int = 1
    snapshot_json: str = "{}"
    author_id: str = ""
    created_at: str = field(default_factory=utc_now)
    reason: str = ""
    sha256: str = ""

    def snapshot(self) -> dict:
        return json.loads(self.snapshot_json)


class ReviewDecision(str):
    ACCEPT = "accept"
    RETURN = "return"
    ADJUDICATE = "adjudicate"
    COMMENT = "comment"


@dataclass
class ReviewComment:
    id: str = field(default_factory=lambda: new_id("rcm_"))
    review_id: str = ""
    #: Identifier of the annotation the comment is attached to, or empty for a
    #: case level comment.
    annotation_id: str = ""
    class_key: str = ""
    side: str = Side.NONE.value
    decision: str = ReviewDecision.COMMENT
    text: str = ""
    created_by: str = ""
    created_at: str = field(default_factory=utc_now)
    resolved: bool = False


@dataclass
class Review:
    id: str = field(default_factory=lambda: new_id("rev_"))
    set_id: str = ""
    reviewer_id: str = ""
    decision: str = ReviewDecision.COMMENT
    summary: str = ""
    created_at: str = field(default_factory=utc_now)
    comments: list = field(default_factory=list)
    #: Revision number of the set that was reviewed.
    reviewed_revision: int = 1


@dataclass
class AuditRecord:
    """One append only audit entry (NFR 003, NFR 004).

    Entries are chained by hash: each record stores the digest of the previous
    record, so removing or editing a row anywhere in the history is detectable.
    """

    id: str = field(default_factory=lambda: new_id("aud_"))
    sequence: int = 0
    timestamp: str = field(default_factory=utc_now)
    actor_id: str = ""
    actor_name: str = ""
    event: str = ""
    object_type: str = ""
    object_id: str = ""
    project_id: str = ""
    case_id: str = ""
    before_json: str = ""
    after_json: str = ""
    detail: str = ""
    previous_hash: str = ""
    record_hash: str = ""


@dataclass
class CalibrationSetResult:
    """Result of an annotator's calibration set, stored with reviewer approval
    (FR 046)."""

    id: str = field(default_factory=lambda: new_id("cal_"))
    user_id: str = ""
    project_id: str = ""
    cases_completed: int = 0
    cases_required: int = 0
    metrics_json: str = "{}"
    passed: bool = False
    approved_by: str | None = None
    approved_at: str | None = None
    created_at: str = field(default_factory=utc_now)
    notes: str = ""

    def metrics(self) -> dict:
        return json.loads(self.metrics_json or "{}")


def json_default(obj: Any):
    """JSON encoder hook for the dataclasses above."""
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dataclass_fields__"):
        return asdict(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")
