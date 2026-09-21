"""Canonical label schema, colour nomenclature and project schema configuration.

This module is the single authoritative registry of every label class ARIA can
produce. The viewer, the validators, the exporters and the data dictionary all
read from here, so a class can never be drawn in one colour and exported under a
different name.

Colour policy
-------------
Base colours are taken from the Okabe and Ito qualitative palette, which stays
separable under the common forms of colour vision deficiency. Colour is never
the only carrier of meaning (NFR 010):

* side is carried by stroke pattern, Right is a solid stroke and Left is a
  dashed stroke, in addition to the side letter drawn in the label chip,
* state is carried by a glyph and a text chip, not by colour alone,
* every class carries a short code that is drawn next to the geometry when
  labels are switched on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable


class Side(str, Enum):
    """Anatomical side. Orientation is anatomical right and left (FR 010)."""

    RIGHT = "R"
    LEFT = "L"
    MIDLINE = "M"
    NONE = "NA"

    @property
    def display(self) -> str:
        return {
            Side.RIGHT: "Right",
            Side.LEFT: "Left",
            Side.MIDLINE: "Midline",
            Side.NONE: "Not sided",
        }[self]


class GeometryType(str, Enum):
    POINT = "point"
    LINE = "line"            # exactly two endpoints
    POLYLINE = "polyline"
    POLYGON = "polygon"
    BOX = "box"              # axis aligned bounding box
    MASK = "mask"            # raster brush mask
    ROI_RECT = "roi_rect"    # fixed size analysis region


class LabelCategory(str, Enum):
    LANDMARK = "landmark"
    CONTOUR = "contour"
    REGION = "region"
    INDEX_LINE = "index_line"
    GRADE = "grade"
    OPTIONAL = "optional"


class Presence(str, Enum):
    """Explicit presence state. Missing anatomy is never a zero coordinate
    (FR 017)."""

    PRESENT = "present"
    NOT_VISIBLE = "not_visible"
    NOT_ASSESSABLE = "not_assessable"
    ABSENT_ANATOMY = "absent_anatomy"
    UNCERTAIN = "uncertain"

    @property
    def display(self) -> str:
        return {
            Presence.PRESENT: "Present",
            Presence.NOT_VISIBLE: "Not visible",
            Presence.NOT_ASSESSABLE: "Not assessable",
            Presence.ABSENT_ANATOMY: "Anatomy absent",
            Presence.UNCERTAIN: "Uncertain",
        }[self]


class MCIGrade(str, Enum):
    """Mandibular Cortical Index, Klemetti classification (FR 027, FR 028)."""

    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    NOT_ASSESSABLE = "not_assessable"
    UNCERTAIN = "uncertain"

    @property
    def definition(self) -> str:
        return MCI_DEFINITIONS[self]

    @property
    def display(self) -> str:
        return {
            MCIGrade.C1: "C1",
            MCIGrade.C2: "C2",
            MCIGrade.C3: "C3",
            MCIGrade.NOT_ASSESSABLE: "Not assessable",
            MCIGrade.UNCERTAIN: "Uncertain",
        }[self]

    @property
    def ordinal(self) -> int | None:
        """Ordinal rank for weighted kappa. Non gradable values have none."""
        return {MCIGrade.C1: 1, MCIGrade.C2: 2, MCIGrade.C3: 3}.get(self)


MCI_DEFINITIONS = {
    MCIGrade.C1: (
        "Even and sharp endosteal margin of the inferior cortex on the "
        "assessed side."
    ),
    MCIGrade.C2: (
        "Semilunar defects, or one to three layers of endosteal cortical "
        "residues."
    ),
    MCIGrade.C3: (
        "Clearly porous cortical margin with more than three layers of "
        "endosteal cortical residues."
    ),
    MCIGrade.NOT_ASSESSABLE: (
        "The inferior cortex distal to the mental foramen cannot be assessed "
        "on this image."
    ),
    MCIGrade.UNCERTAIN: (
        "The cortex is visible but the grade cannot be decided with "
        "confidence."
    ),
}


class QualityFlag(str, Enum):
    """Per case quality flags (FR 016)."""

    NOT_VISIBLE = "not_visible"
    AMBIGUOUS = "ambiguous"
    ANATOMICAL_VARIANT = "anatomical_variant"
    ARTEFACT = "artefact"
    CROPPED_ANATOMY = "cropped_anatomy"
    POOR_POSITIONING = "poor_positioning"
    MOTION = "motion"
    OTHER = "other"

    @property
    def display(self) -> str:
        return QUALITY_FLAG_LABELS[self]


QUALITY_FLAG_LABELS = {
    QualityFlag.NOT_VISIBLE: "Not visible",
    QualityFlag.AMBIGUOUS: "Ambiguous",
    QualityFlag.ANATOMICAL_VARIANT: "Anatomical variant",
    QualityFlag.ARTEFACT: "Artefact",
    QualityFlag.CROPPED_ANATOMY: "Cropped anatomy",
    QualityFlag.POOR_POSITIONING: "Poor positioning",
    QualityFlag.MOTION: "Motion",
    QualityFlag.OTHER: "Other",
}


class CaseState(str, Enum):
    """Case workflow states (FR 039)."""

    UNASSIGNED = "unassigned"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    RETURNED = "returned"
    ACCEPTED = "accepted"
    ADJUDICATED = "adjudicated"

    @property
    def display(self) -> str:
        return CASE_STATE_LABELS[self]

    @property
    def glyph(self) -> str:
        """Colour independent state indicator (NFR 010)."""
        return CASE_STATE_GLYPHS[self]


CASE_STATE_LABELS = {
    CaseState.UNASSIGNED: "Unassigned",
    CaseState.ASSIGNED: "Assigned",
    CaseState.IN_PROGRESS: "In progress",
    CaseState.SUBMITTED: "Submitted",
    CaseState.RETURNED: "Returned",
    CaseState.ACCEPTED: "Accepted",
    CaseState.ADJUDICATED: "Adjudicated",
}

CASE_STATE_GLYPHS = {
    CaseState.UNASSIGNED: "○",       # hollow circle
    CaseState.ASSIGNED: "◔",         # quarter filled circle
    CaseState.IN_PROGRESS: "◑",      # half filled circle
    CaseState.SUBMITTED: "△",        # hollow triangle
    CaseState.RETURNED: "↺",         # return arrow
    CaseState.ACCEPTED: "✓",         # check mark
    CaseState.ADJUDICATED: "✔",      # heavy check mark
}

#: States in which an annotator may still edit geometry.
EDITABLE_STATES = {CaseState.ASSIGNED, CaseState.IN_PROGRESS, CaseState.RETURNED}

#: Legal state transitions. Anything not listed here is rejected by the store.
STATE_TRANSITIONS: dict[CaseState, set[CaseState]] = {
    CaseState.UNASSIGNED: {CaseState.ASSIGNED},
    CaseState.ASSIGNED: {CaseState.IN_PROGRESS, CaseState.UNASSIGNED},
    CaseState.IN_PROGRESS: {CaseState.SUBMITTED, CaseState.ASSIGNED},
    CaseState.SUBMITTED: {CaseState.RETURNED, CaseState.ACCEPTED, CaseState.ADJUDICATED},
    CaseState.RETURNED: {CaseState.IN_PROGRESS, CaseState.SUBMITTED},
    CaseState.ACCEPTED: {CaseState.ADJUDICATED, CaseState.RETURNED},
    CaseState.ADJUDICATED: {CaseState.RETURNED},
}


class LineStyle(str, Enum):
    SOLID = "solid"
    DASHED = "dashed"
    DOTTED = "dotted"
    DASH_DOT = "dash_dot"


@dataclass(frozen=True)
class LabelClass:
    """One annotatable class in the ARIA schema."""

    key: str
    display_name: str
    short_code: str
    geometry: GeometryType
    category: LabelCategory
    colour: str
    glyph: str
    description: str
    side_scoped: bool = True
    required_by_default: bool = False
    aliases: tuple[str, ...] = ()
    #: Minimum and maximum vertex counts for polyline and polygon classes.
    min_vertices: int = 1
    max_vertices: int | None = None
    #: Classes this one is geometrically derived from or constrained by.
    depends_on: tuple[str, ...] = ()
    #: Requirement identifiers in the specification that this class satisfies.
    requirements: tuple[str, ...] = ()

    def line_style_for(self, side: "Side") -> LineStyle:
        """Side is encoded by stroke pattern so colour is never the only cue."""
        if not self.side_scoped or side in (Side.MIDLINE, Side.NONE):
            return LineStyle.SOLID
        return LineStyle.SOLID if side is Side.RIGHT else LineStyle.DASHED

    def label_for(self, side: "Side") -> str:
        if not self.side_scoped or side is Side.NONE:
            return self.short_code
        return f"{self.short_code} {side.value}"


# ---------------------------------------------------------------------------
# Colour nomenclature
# ---------------------------------------------------------------------------
# Okabe and Ito qualitative palette, extended with four neutral tones for
# contour work. Documented in docs/COLOUR_NOMENCLATURE.md.

PALETTE = {
    "orange": "#E69F00",
    "sky_blue": "#56B4E9",
    "bluish_green": "#009E73",
    "yellow": "#F0E442",
    "blue": "#0072B2",
    "vermillion": "#D55E00",
    "reddish_purple": "#CC79A7",
    "black": "#000000",
    # Neutral extensions
    "bone_white": "#ECECEC",
    "slate": "#8C9BAB",
    "teal": "#2FA3A3",
    "amber_deep": "#B87400",
}


LABEL_CLASSES: tuple[LabelClass, ...] = (
    # -- Landmarks ---------------------------------------------------------
    LabelClass(
        key="mental_foramen_centre",
        display_name="Mental foramen centre",
        short_code="MF",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["orange"],
        glyph="●",
        description=(
            "Centre of the mental foramen on the annotated side. Anchors the "
            "perpendicular used for cortical width and the panoramic "
            "mandibular index."
        ),
        required_by_default=True,
        requirements=("FR 018",),
    ),
    LabelClass(
        key="mental_foramen_superior",
        display_name="Mental foramen superior margin",
        short_code="MFs",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["orange"],
        glyph="▲",
        description="Superior margin of the mental foramen outline.",
        required_by_default=True,
        depends_on=("mental_foramen_centre",),
        requirements=("FR 018", "FR 025"),
    ),
    LabelClass(
        key="mental_foramen_inferior",
        display_name="Mental foramen inferior margin",
        short_code="MFi",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["orange"],
        glyph="▼",
        description="Inferior margin of the mental foramen outline.",
        required_by_default=True,
        depends_on=("mental_foramen_centre",),
        requirements=("FR 018", "FR 026"),
    ),
    LabelClass(
        key="mental_foramen_box",
        display_name="Mental foramen bounding box",
        short_code="MFb",
        geometry=GeometryType.BOX,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["amber_deep"],
        glyph="□",
        description="Optional bounding box around the mental foramen.",
        required_by_default=False,
        requirements=("FR 018",),
    ),
    LabelClass(
        key="antegonial_point",
        display_name="Antegonial point",
        short_code="AG",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["bluish_green"],
        glyph="◆",
        description=(
            "Deepest point of the antegonial notch on the inferior border of "
            "the mandible."
        ),
        required_by_default=True,
        requirements=("FR 021", "FR 023"),
    ),
    LabelClass(
        key="gonion",
        display_name="Gonion",
        short_code="GO",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["reddish_purple"],
        glyph="◆",
        description=(
            "Intersection of the tangent to the posterior border of the ramus "
            "and the tangent to the inferior border of the mandible."
        ),
        required_by_default=True,
        requirements=("FR 021", "FR 024"),
    ),
    LabelClass(
        key="menton",
        display_name="Menton",
        short_code="ME",
        geometry=GeometryType.POINT,
        category=LabelCategory.LANDMARK,
        colour=PALETTE["sky_blue"],
        glyph="◆",
        description="Most inferior midline point of the mandibular symphysis.",
        side_scoped=False,
        required_by_default=True,
        requirements=("FR 021",),
    ),
    # -- Contours ----------------------------------------------------------
    LabelClass(
        key="periosteal_border",
        display_name="Inferior cortex, periosteal border",
        short_code="PER",
        geometry=GeometryType.POLYLINE,
        category=LabelCategory.CONTOUR,
        colour=PALETTE["yellow"],
        glyph="─",
        description=(
            "Outer surface of the mandibular inferior cortex, traced from the "
            "symphysis toward the gonial region."
        ),
        required_by_default=True,
        min_vertices=2,
        requirements=("FR 019",),
    ),
    LabelClass(
        key="endosteal_border",
        display_name="Inferior cortex, endosteal border",
        short_code="END",
        geometry=GeometryType.POLYLINE,
        category=LabelCategory.CONTOUR,
        colour=PALETTE["bone_white"],
        glyph="─",
        description=(
            "Inner surface of the mandibular inferior cortex, traced over the "
            "same span as the periosteal border."
        ),
        required_by_default=True,
        min_vertices=2,
        requirements=("FR 019",),
    ),
    LabelClass(
        key="cortical_mask",
        display_name="Inferior cortical mask",
        short_code="CTX",
        geometry=GeometryType.MASK,
        category=LabelCategory.REGION,
        colour=PALETTE["amber_deep"],
        glyph="▓",
        description=(
            "Optional filled mask of the cortical band between the periosteal "
            "and endosteal borders."
        ),
        required_by_default=False,
        depends_on=("periosteal_border", "endosteal_border"),
        requirements=("FR 019",),
    ),
    LabelClass(
        key="posterior_ramus_border",
        display_name="Posterior ramus border",
        short_code="PRB",
        geometry=GeometryType.POLYLINE,
        category=LabelCategory.CONTOUR,
        colour=PALETTE["slate"],
        glyph="│",
        description=(
            "Posterior border of the ramus. Supplies the ramus tangent used to "
            "construct the gonial bisector."
        ),
        required_by_default=False,
        min_vertices=2,
        requirements=("FR 024",),
    ),
    # -- Regions -----------------------------------------------------------
    LabelClass(
        key="mandible_whole",
        display_name="Mandible, whole",
        short_code="MAN",
        geometry=GeometryType.POLYGON,
        category=LabelCategory.REGION,
        colour=PALETTE["teal"],
        glyph="⬛",
        description="Whole mandible outline as a single polygon mask.",
        side_scoped=False,
        required_by_default=False,
        min_vertices=3,
        requirements=("FR 020",),
    ),
    LabelClass(
        key="hemimandible",
        display_name="Hemimandible",
        short_code="HMN",
        geometry=GeometryType.POLYGON,
        category=LabelCategory.REGION,
        colour=PALETTE["teal"],
        glyph="⬛",
        description="Paired hemimandible polygon mask for the annotated side.",
        required_by_default=False,
        min_vertices=3,
        requirements=("FR 020",),
    ),
    LabelClass(
        key="mci_region",
        display_name="MCI assessment region",
        short_code="MCIr",
        geometry=GeometryType.BOX,
        category=LabelCategory.REGION,
        colour=PALETTE["vermillion"],
        glyph="▣",
        description=(
            "Region of inferior cortex distal to the mental foramen that the "
            "Klemetti grade was assigned from."
        ),
        required_by_default=True,
        depends_on=("mental_foramen_centre",),
        requirements=("FR 027",),
    ),
    # -- Index construction lines -----------------------------------------
    LabelClass(
        key="mcw_line",
        display_name="Mandibular cortical width",
        short_code="MCW",
        geometry=GeometryType.LINE,
        category=LabelCategory.INDEX_LINE,
        colour=PALETTE["vermillion"],
        glyph="↕",
        description=(
            "Two endpoint line from the periosteal to the endosteal cortex "
            "along the perpendicular through the mental foramen centre. Stored "
            "under the canonical names Mandibular Cortical Width and Mental "
            "Index."
        ),
        aliases=("Cortical Width Index", "CWI", "Mental Index", "MI"),
        required_by_default=True,
        min_vertices=2,
        max_vertices=2,
        depends_on=("mental_foramen_centre", "periosteal_border", "endosteal_border"),
        requirements=("FR 022", "FR 032"),
    ),
    LabelClass(
        key="pmi_superior_line",
        display_name="PMI superior height",
        short_code="PMIs",
        geometry=GeometryType.LINE,
        category=LabelCategory.INDEX_LINE,
        colour=PALETTE["sky_blue"],
        glyph="↕",
        description=(
            "Height from the superior mental foramen margin to the inferior "
            "mandibular border along the cortical width axis."
        ),
        required_by_default=True,
        min_vertices=2,
        max_vertices=2,
        depends_on=("mental_foramen_superior", "mcw_line"),
        requirements=("FR 025", "FR 033"),
    ),
    LabelClass(
        key="pmi_inferior_line",
        display_name="PMI inferior height",
        short_code="PMIi",
        geometry=GeometryType.LINE,
        category=LabelCategory.INDEX_LINE,
        colour=PALETTE["blue"],
        glyph="↕",
        description=(
            "Height from the inferior mental foramen margin to the inferior "
            "mandibular border along the same axis."
        ),
        required_by_default=True,
        min_vertices=2,
        max_vertices=2,
        depends_on=("mental_foramen_inferior", "mcw_line"),
        requirements=("FR 026", "FR 034"),
    ),
    LabelClass(
        key="antegonial_index_line",
        display_name="Antegonial index",
        short_code="AI",
        geometry=GeometryType.LINE,
        category=LabelCategory.INDEX_LINE,
        colour=PALETTE["bluish_green"],
        glyph="↕",
        description=(
            "Cortical thickness line drawn perpendicular to the cortex at the "
            "antegonial point."
        ),
        required_by_default=True,
        min_vertices=2,
        max_vertices=2,
        depends_on=("antegonial_point",),
        requirements=("FR 023", "FR 035"),
    ),
    LabelClass(
        key="gonial_index_line",
        display_name="Gonial index",
        short_code="GI",
        geometry=GeometryType.LINE,
        category=LabelCategory.INDEX_LINE,
        colour=PALETTE["reddish_purple"],
        glyph="↕",
        description=(
            "Cortical thickness line through gonion along the bisector of the "
            "posterior ramus tangent and the inferior border tangent."
        ),
        required_by_default=True,
        min_vertices=2,
        max_vertices=2,
        depends_on=("gonion",),
        requirements=("FR 024", "FR 035"),
    ),
    # -- Optional project labels ------------------------------------------
    LabelClass(
        key="trabecular_roi",
        display_name="Trabecular bone region",
        short_code="TRB",
        geometry=GeometryType.ROI_RECT,
        category=LabelCategory.OPTIONAL,
        colour=PALETTE["bluish_green"],
        glyph="▦",
        description=(
            "Square analysis region over trabecular bone. Must exclude roots, "
            "the mandibular canal, the cortical border, lesions and obvious "
            "artefacts."
        ),
        required_by_default=False,
        requirements=("FR 029", "FR 030"),
    ),
    LabelClass(
        key="crestal_roi",
        display_name="Alveolar crestal region",
        short_code="CRS",
        geometry=GeometryType.ROI_RECT,
        category=LabelCategory.OPTIONAL,
        colour=PALETTE["orange"],
        glyph="▦",
        description="Analysis region over the alveolar crest.",
        required_by_default=False,
        requirements=("FR 029",),
    ),
    LabelClass(
        key="symphysis_roi",
        display_name="Symphysis region",
        short_code="SYM",
        geometry=GeometryType.ROI_RECT,
        category=LabelCategory.OPTIONAL,
        colour=PALETTE["sky_blue"],
        glyph="▦",
        description="Analysis region over the mandibular symphysis.",
        side_scoped=False,
        required_by_default=False,
        requirements=("FR 029",),
    ),
    LabelClass(
        key="user_landmark",
        display_name="User defined landmark",
        short_code="USR",
        geometry=GeometryType.POINT,
        category=LabelCategory.OPTIONAL,
        colour=PALETTE["slate"],
        glyph="✦",
        description=(
            "Project specific landmark. The free text name is stored in the "
            "annotation properties."
        ),
        required_by_default=False,
        requirements=("FR 029",),
    ),
)

CLASS_BY_KEY: dict[str, LabelClass] = {c.key: c for c in LABEL_CLASSES}

#: Canonical name mapping for the cortical width family. The clinical team
#: signs off the naming convention (specification section 13); ARIA stores one
#: canonical record and exports the agreed aliases alongside it.
CANONICAL_ALIASES = {
    "mcw_line": ("MCW", "CWI", "MI"),
}

#: Named anatomical presets for fixed size analysis regions. Region placement
#: rules are subject to clinical sign off; these are the starting presets an
#: administrator can edit per project.
ROI_PRESETS = (
    ("trabecular_condyle", "Condylar trabecular", "trabecular_roi"),
    ("trabecular_angle", "Mandibular angle trabecular", "trabecular_roi"),
    ("trabecular_canine", "Canine apical trabecular", "trabecular_roi"),
    ("trabecular_premolar", "First premolar apical trabecular", "trabecular_roi"),
    ("trabecular_incisor", "Central incisor apical trabecular", "trabecular_roi"),
    ("cortical_basal", "Basal cortical bone", "trabecular_roi"),
    ("crestal_posterior", "Posterior alveolar crest", "crestal_roi"),
    ("symphysis_midline", "Midline symphysis", "symphysis_roi"),
)


def get_class(key: str) -> LabelClass:
    try:
        return CLASS_BY_KEY[key]
    except KeyError as exc:
        raise KeyError(f"Unknown label class: {key!r}") from exc


def classes_in_category(category: LabelCategory) -> tuple[LabelClass, ...]:
    return tuple(c for c in LABEL_CLASSES if c.category is category)


def default_required_keys() -> tuple[str, ...]:
    return tuple(c.key for c in LABEL_CLASSES if c.required_by_default)


# ---------------------------------------------------------------------------
# Project schema configuration
# ---------------------------------------------------------------------------


@dataclass
class ThresholdRule:
    """A configurable screening threshold (FR 038).

    Thresholds are never compiled into label definitions. They are project
    configuration, they are versioned, they are labelled as screening rules in
    every surface that shows them, and they are disabled until an administrator
    records clinical approval.
    """

    key: str
    display_name: str
    measure: str
    comparator: str            # one of lt, lte, gt, gte
    value: float
    unit: str
    version: str = "1"
    enabled: bool = False
    clinically_approved: bool = False
    approved_by: str | None = None
    approved_at: str | None = None
    citation: str = ""

    def is_active(self) -> bool:
        return self.enabled and self.clinically_approved

    def evaluate(self, measured: float | None) -> bool | None:
        """Return the rule outcome, or None when the rule is not active."""
        if measured is None or not self.is_active():
            return None
        ops = {
            "lt": lambda a, b: a < b,
            "lte": lambda a, b: a <= b,
            "gt": lambda a, b: a > b,
            "gte": lambda a, b: a >= b,
        }
        return ops[self.comparator](measured, self.value)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "measure": self.measure,
            "comparator": self.comparator,
            "value": self.value,
            "unit": self.unit,
            "version": self.version,
            "enabled": self.enabled,
            "clinically_approved": self.clinically_approved,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "citation": self.citation,
            "rule_type": "screening_rule",
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ThresholdRule":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class ProjectSchema:
    """Per project configuration of required labels and tolerances (FR 040)."""

    schema_version: str = "1.0.0"
    mandible_mode: str = "whole"          # whole or hemimandible (FR 020)
    required_classes: list = field(default_factory=lambda: list(default_required_keys()))
    allowed_omissions: list = field(default_factory=list)
    optional_classes: list = field(
        default_factory=lambda: [
            c.key for c in LABEL_CLASSES if c.category is LabelCategory.OPTIONAL
        ]
        + ["mental_foramen_box", "cortical_mask", "posterior_ramus_border",
           "mandible_whole", "hemimandible"]
    )
    require_mci_grade: bool = True
    require_laterality_confirmation: bool = True
    require_calibration_for_submission: bool = False
    #: Acceptance tolerances used by the review and agreement modules.
    point_tolerance_px: float = 8.0
    line_endpoint_tolerance_px: float = 10.0
    measurement_tolerance_mm: float = 0.30
    mask_dice_tolerance: float = 0.80
    #: Fraction of cases double annotated for agreement analysis (FR 043).
    duplicate_fraction: float = 0.20
    #: Fixed analysis region size in pixels. Region size materially changes
    #: texture feature values, so it is recorded with every result.
    roi_size_px: int = 64
    #: Texture features to compute after annotation (FR 030).
    texture_features: list = field(
        default_factory=lambda: ["glcm", "fractal_dimension", "lbp", "run_length"]
    )
    #: Screening thresholds. Empty and disabled until clinically approved.
    thresholds: list = field(default_factory=list)
    #: Calibration policy per acquisition device, keyed by manufacturer model.
    device_calibration_policy: dict = field(default_factory=dict)
    allow_magnification_correction: bool = False

    def is_required(self, key: str) -> bool:
        return key in self.required_classes and key not in self.allowed_omissions

    def active_classes(self) -> tuple[LabelClass, ...]:
        keys = set(self.required_classes) | set(self.optional_classes)
        if self.mandible_mode == "whole":
            keys.discard("hemimandible")
        else:
            keys.discard("mandible_whole")
        return tuple(c for c in LABEL_CLASSES if c.key in keys)

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "mandible_mode": self.mandible_mode,
            "required_classes": list(self.required_classes),
            "allowed_omissions": list(self.allowed_omissions),
            "optional_classes": list(self.optional_classes),
            "require_mci_grade": self.require_mci_grade,
            "require_laterality_confirmation": self.require_laterality_confirmation,
            "require_calibration_for_submission": self.require_calibration_for_submission,
            "point_tolerance_px": self.point_tolerance_px,
            "line_endpoint_tolerance_px": self.line_endpoint_tolerance_px,
            "measurement_tolerance_mm": self.measurement_tolerance_mm,
            "mask_dice_tolerance": self.mask_dice_tolerance,
            "duplicate_fraction": self.duplicate_fraction,
            "roi_size_px": self.roi_size_px,
            "texture_features": list(self.texture_features),
            "thresholds": [t.to_dict() for t in self.thresholds],
            "device_calibration_policy": dict(self.device_calibration_policy),
            "allow_magnification_correction": self.allow_magnification_correction,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProjectSchema":
        data = dict(data or {})
        thresholds = [ThresholdRule.from_dict(t) for t in data.pop("thresholds", [])]
        allowed = set(cls.__dataclass_fields__)
        obj = cls(**{k: v for k, v in data.items() if k in allowed})
        obj.thresholds = thresholds
        return obj


def validate_class_keys(keys: Iterable) -> list:
    """Return the subset of keys that are not present in the schema."""
    return [k for k in keys if k not in CLASS_BY_KEY]
