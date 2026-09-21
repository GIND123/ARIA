"""Derived measurements and the rules that produce them.

Every value this module returns carries the identifiers of the annotations it
was computed from and the version of these rules (FR 037), so a reviewer can
take an export and arrive at the same numbers without the application
(AC 004). :func:`reproduce_from_export` is that path, and the test suite runs
it against real exports.

Rules implemented here
----------------------
FR 031  Pixel distance is Euclidean in original image coordinates. Millimetre
        distance applies row and column spacing separately.
FR 032  Cortical width is the calibrated distance between the periosteal and
        endosteal endpoints on the mental foramen perpendicular.
FR 033  Superior panoramic mandibular index is cortical width divided by the
        superior foramen to inferior border height on the same side.
FR 034  Inferior panoramic mandibular index uses the inferior margin height.
FR 035  Antegonial and gonial indices report side specific thickness in pixels,
        and millimetres only when calibrated.
FR 036  Bilateral means use assessable sides only and record the rule applied.
FR 038  Screening thresholds are project configuration, never label content.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum

from ..version import CALC_VERSION
from .geometry import euclidean
from .models import Annotation, CaseData
from .schema import MCIGrade, ProjectSchema, Side
from .units import Calibration, Unit


class MeasurementKind(str, Enum):
    MCW = "mandibular_cortical_width"
    PMI_SUPERIOR_HEIGHT = "pmi_superior_height"
    PMI_INFERIOR_HEIGHT = "pmi_inferior_height"
    PMI_SUPERIOR = "pmi_superior"
    PMI_INFERIOR = "pmi_inferior"
    ANTEGONIAL_INDEX = "antegonial_index"
    GONIAL_INDEX = "gonial_index"
    MCI_GRADE = "mci_grade"

    @property
    def display(self) -> str:
        return MEASUREMENT_LABELS[self]

    @property
    def aliases(self) -> tuple:
        return MEASUREMENT_ALIASES.get(self, ())


MEASUREMENT_LABELS = {
    MeasurementKind.MCW: "Mandibular cortical width",
    MeasurementKind.PMI_SUPERIOR_HEIGHT: "Superior foramen to inferior border height",
    MeasurementKind.PMI_INFERIOR_HEIGHT: "Inferior foramen to inferior border height",
    MeasurementKind.PMI_SUPERIOR: "Panoramic mandibular index, superior",
    MeasurementKind.PMI_INFERIOR: "Panoramic mandibular index, inferior",
    MeasurementKind.ANTEGONIAL_INDEX: "Antegonial index",
    MeasurementKind.GONIAL_INDEX: "Gonial index",
    MeasurementKind.MCI_GRADE: "Mandibular cortical index",
}

#: The cortical width family is stored once under a canonical name and exported
#: with the agreed aliases beside it (FR 022). The clinical team signs off which
#: alias is authoritative for a given project.
MEASUREMENT_ALIASES = {
    MeasurementKind.MCW: ("MCW", "CWI", "MI"),
    MeasurementKind.PMI_SUPERIOR: ("PMI superior",),
    MeasurementKind.PMI_INFERIOR: ("PMI inferior",),
    MeasurementKind.ANTEGONIAL_INDEX: ("AI", "AGI"),
    MeasurementKind.GONIAL_INDEX: ("GI",),
}

#: Which annotation class supplies each linear measurement.
SOURCE_CLASS = {
    MeasurementKind.MCW: "mcw_line",
    MeasurementKind.PMI_SUPERIOR_HEIGHT: "pmi_superior_line",
    MeasurementKind.PMI_INFERIOR_HEIGHT: "pmi_inferior_line",
    MeasurementKind.ANTEGONIAL_INDEX: "antegonial_index_line",
    MeasurementKind.GONIAL_INDEX: "gonial_index_line",
}

LINEAR_KINDS = (
    MeasurementKind.MCW,
    MeasurementKind.PMI_SUPERIOR_HEIGHT,
    MeasurementKind.PMI_INFERIOR_HEIGHT,
    MeasurementKind.ANTEGONIAL_INDEX,
    MeasurementKind.GONIAL_INDEX,
)

RATIO_KINDS = (MeasurementKind.PMI_SUPERIOR, MeasurementKind.PMI_INFERIOR)


class Aggregation(str, Enum):
    NONE = "none"
    MEAN_ASSESSABLE_SIDES = "mean_of_assessable_sides"

    @property
    def description(self) -> str:
        return {
            Aggregation.NONE: "Side specific value, no aggregation applied.",
            Aggregation.MEAN_ASSESSABLE_SIDES: (
                "Arithmetic mean over sides that were assessable. Sides recorded "
                "as not assessable, not visible or absent are excluded and the "
                "number of contributing sides is reported alongside the value."
            ),
        }[self]


@dataclass
class Measurement:
    """One derived value with full provenance (FR 037)."""

    kind: str
    side: str
    value_px: float | None = None
    value_mm: float | None = None
    value_ratio: float | None = None
    unit: str = Unit.PIXEL.value
    assessable: bool = True
    unavailable_reason: str = ""
    #: Identifiers of every annotation that fed this value.
    source_annotation_ids: list = field(default_factory=list)
    #: The raw inputs used, so the arithmetic can be repeated by hand.
    inputs: dict = field(default_factory=dict)
    calculation_version: str = CALC_VERSION
    aggregation: str = Aggregation.NONE.value
    aggregation_detail: str = ""
    sides_used: list = field(default_factory=list)
    calibration_source: str = ""
    calibration_status: str = ""
    calibration_scale: str = ""
    correction_factor: str = "none"
    millimetres_available: bool = False
    #: Basis used for a dimensionless ratio, millimetre or pixel.
    ratio_basis: str = ""
    warnings: list = field(default_factory=list)
    #: Screening rule outcomes, always labelled as screening (FR 038).
    screening: list = field(default_factory=list)

    @property
    def kind_enum(self) -> MeasurementKind:
        return MeasurementKind(self.kind)

    @property
    def display_name(self) -> str:
        return MeasurementKind(self.kind).display

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Measurement":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})

    def compact(self) -> str:
        """A short form for a narrow column. The full form is in the tooltip."""
        if not self.assessable:
            return "–"
        if self.value_ratio is not None:
            return f"{self.value_ratio:.3f}"
        if self.value_mm is not None:
            return f"{self.value_mm:.2f} mm"
        if self.value_px is not None:
            return f"{self.value_px:.1f} px"
        return "–"

    def formatted(self) -> str:
        if not self.assessable:
            return f"Not assessable ({self.unavailable_reason})" if self.unavailable_reason else "Not assessable"
        if self.value_ratio is not None:
            return f"{self.value_ratio:.4f}"
        if self.value_px is None:
            return "not measured"
        if self.value_mm is not None:
            return f"{self.value_mm:.2f} mm ({self.value_px:.2f} px)"
        return f"{self.value_px:.2f} px (mm unavailable)"


def _stamp_calibration(m: Measurement, cal: Calibration) -> Measurement:
    m.calibration_source = cal.source.value
    m.calibration_status = cal.status.value
    m.millimetres_available = cal.millimetres_available
    m.correction_factor = cal.correction_factor_text
    if cal.has_spacing:
        m.calibration_scale = (
            f"{cal.row_spacing_mm:.6g} mm/px row, {cal.col_spacing_mm:.6g} mm/px column"
        )
    else:
        m.calibration_scale = "not available"
    return m


def _line_points(ann: Annotation | None) -> tuple | None:
    if ann is None:
        return None
    pts = ann.points()
    if len(pts) < 2:
        return None
    return pts[0], pts[-1]


def measure_line(
    ann: Annotation | None,
    kind: MeasurementKind,
    side: Side,
    cal: Calibration,
    missing_reason: str = "The required line is not annotated.",
) -> Measurement:
    """Measure one two endpoint line in pixels and, when calibrated, millimetres."""
    m = Measurement(kind=kind.value, side=side.value)
    _stamp_calibration(m, cal)

    if ann is None:
        m.assessable = False
        m.unavailable_reason = missing_reason
        return m
    m.source_annotation_ids = [ann.id]

    if not ann.is_assessable:
        m.assessable = False
        m.unavailable_reason = (
            f"Recorded as {ann.presence_enum.display.lower()} on the "
            f"{side.display.lower()} side."
        )
        return m

    pts = _line_points(ann)
    if pts is None:
        m.assessable = False
        m.unavailable_reason = "The line does not have two endpoints."
        return m

    p, q = pts
    m.value_px = euclidean(p, q)
    m.value_mm = cal.length_mm(p, q)
    m.unit = Unit.MILLIMETRE.value if m.value_mm is not None else Unit.PIXEL.value
    m.inputs = {
        "start_x": p[0], "start_y": p[1],
        "end_x": q[0], "end_y": q[1],
        "dx_px": q[0] - p[0], "dy_px": q[1] - p[1],
        "row_spacing_mm": cal.effective_row_mm,
        "col_spacing_mm": cal.effective_col_mm,
        "formula": "sqrt((dx*col_spacing)^2 + (dy*row_spacing)^2)",
    }
    if m.value_mm is None:
        m.warnings.append(
            "Millimetre value unavailable because the calibration is not validated."
        )
    if ann.ambiguous:
        m.warnings.append("The source annotation is flagged as ambiguous.")
    return m


def _collinearity_warning(
    numerator: Annotation | None, denominator: Annotation | None, cal: Calibration
) -> str | None:
    """Warn when a ratio is taken in pixels across non parallel lines on an
    image whose row and column spacing differ.

    When both lines share the cortical width axis, the pixel ratio equals the
    millimetre ratio and no warning is needed. When they diverge and the scale
    is anisotropic, the pixel ratio is not equal to the millimetre ratio.
    """
    if not cal.is_anisotropic:
        return None
    a = _line_points(numerator)
    b = _line_points(denominator)
    if a is None or b is None:
        return None
    va = (a[1][0] - a[0][0], a[1][1] - a[0][1])
    vb = (b[1][0] - b[0][0], b[1][1] - b[0][1])
    na = math.hypot(*va)
    nb = math.hypot(*vb)
    if na < 1e-9 or nb < 1e-9:
        return None
    cosang = abs((va[0] * vb[0] + va[1] * vb[1]) / (na * nb))
    cosang = min(1.0, cosang)
    deviation = math.degrees(math.acos(cosang))
    if deviation > 2.0:
        return (
            f"The two lines differ in direction by {deviation:.1f} degrees and "
            f"the image scale is anisotropic, so a pixel based ratio is only "
            f"approximate. Validate the calibration to obtain the millimetre "
            f"based ratio."
        )
    return None


def measure_ratio(
    numerator: Measurement,
    denominator: Measurement,
    kind: MeasurementKind,
    side: Side,
    cal: Calibration,
    numerator_ann: Annotation | None = None,
    denominator_ann: Annotation | None = None,
) -> Measurement:
    """Compute a dimensionless index such as the panoramic mandibular index.

    A ratio is dimensionless, so it is produced even without a validated
    calibration (FR 008). When millimetre values exist the ratio is taken from
    them; otherwise it is taken from pixel values and the basis is recorded.
    """
    m = Measurement(kind=kind.value, side=side.value, unit=Unit.RATIO.value)
    _stamp_calibration(m, cal)
    m.source_annotation_ids = sorted(
        set(numerator.source_annotation_ids) | set(denominator.source_annotation_ids)
    )

    if not numerator.assessable or not denominator.assessable:
        m.assessable = False
        reasons = []
        if not numerator.assessable:
            reasons.append(f"numerator: {numerator.unavailable_reason}")
        if not denominator.assessable:
            reasons.append(f"denominator: {denominator.unavailable_reason}")
        m.unavailable_reason = "; ".join(reasons)
        return m

    if numerator.value_mm is not None and denominator.value_mm is not None:
        num, den, basis = numerator.value_mm, denominator.value_mm, "millimetre"
    else:
        num, den, basis = numerator.value_px, denominator.value_px, "pixel"

    if den is None or abs(den) < 1e-9:
        m.assessable = False
        m.unavailable_reason = "The height used as the denominator is zero."
        return m

    m.value_ratio = num / den
    m.ratio_basis = basis
    m.inputs = {
        "numerator": num,
        "denominator": den,
        "basis": basis,
        "formula": "cortical width divided by foramen to inferior border height",
    }
    if basis == "pixel":
        warn = _collinearity_warning(numerator_ann, denominator_ann, cal)
        if warn:
            m.warnings.append(warn)
        else:
            m.warnings.append(
                "Ratio computed from pixel distances because no validated "
                "calibration is present. A ratio is dimensionless, so this value "
                "is reported rather than withheld."
            )
    return m


def bilateral_mean(
    values: list, kind: MeasurementKind, cal: Calibration
) -> Measurement:
    """Mean over assessable sides only, with the rule recorded (FR 036)."""
    m = Measurement(
        kind=kind.value,
        side=Side.NONE.value,
        aggregation=Aggregation.MEAN_ASSESSABLE_SIDES.value,
        aggregation_detail=Aggregation.MEAN_ASSESSABLE_SIDES.description,
    )
    _stamp_calibration(m, cal)

    usable = [v for v in values if v.assessable]
    m.sides_used = [v.side for v in usable]
    m.source_annotation_ids = sorted(
        {aid for v in usable for aid in v.source_annotation_ids}
    )

    if not usable:
        m.assessable = False
        m.unavailable_reason = "No side was assessable."
        return m

    if kind in RATIO_KINDS:
        ratios = [v.value_ratio for v in usable if v.value_ratio is not None]
        if ratios:
            m.value_ratio = sum(ratios) / len(ratios)
            m.unit = Unit.RATIO.value
            m.ratio_basis = usable[0].ratio_basis
    else:
        pxs = [v.value_px for v in usable if v.value_px is not None]
        mms = [v.value_mm for v in usable if v.value_mm is not None]
        if pxs:
            m.value_px = sum(pxs) / len(pxs)
        if mms and len(mms) == len(usable):
            m.value_mm = sum(mms) / len(mms)
            m.unit = Unit.MILLIMETRE.value
        elif mms:
            m.warnings.append(
                "Only some sides had a millimetre value, so the millimetre mean "
                "was not produced."
            )

    m.inputs = {
        "n_sides_used": len(usable),
        "n_sides_total": len(values),
        "sides_used": m.sides_used,
        "excluded": [
            {"side": v.side, "reason": v.unavailable_reason}
            for v in values
            if not v.assessable
        ],
    }
    if len(usable) < len(values):
        m.warnings.append(
            f"Mean computed from {len(usable)} of {len(values)} sides. The "
            f"excluded sides and the reason are recorded with this value."
        )
    return m


def apply_thresholds(
    measurements: list, schema: ProjectSchema
) -> None:
    """Attach screening rule outcomes in place (FR 038).

    Nothing is attached unless the project has both enabled the rule and
    recorded clinical approval. Outcomes are always labelled as screening rules,
    never as a diagnosis.
    """
    if not schema.thresholds:
        return
    for m in measurements:
        for rule in schema.thresholds:
            if rule.measure != m.kind:
                continue
            measured = (
                m.value_mm
                if m.value_mm is not None
                else (m.value_ratio if m.value_ratio is not None else None)
            )
            outcome = rule.evaluate(measured)
            if outcome is None:
                continue
            m.screening.append(
                {
                    "rule_key": rule.key,
                    "rule_name": rule.display_name,
                    "rule_version": rule.version,
                    "rule_type": "screening_rule",
                    "comparator": rule.comparator,
                    "threshold": rule.value,
                    "unit": rule.unit,
                    "outcome": bool(outcome),
                    "statement": (
                        f"Screening rule {rule.display_name} version "
                        f"{rule.version} was met."
                        if outcome
                        else f"Screening rule {rule.display_name} version "
                        f"{rule.version} was not met."
                    ),
                    "disclaimer": (
                        "This is a configurable screening rule recorded for "
                        "research workflow. It is not a diagnosis."
                    ),
                }
            )


class MeasurementEngine:
    """Computes every derived measurement for one annotation set."""

    def __init__(self, schema: ProjectSchema | None = None):
        self.schema = schema or ProjectSchema()

    def compute(self, data: CaseData) -> list:
        cal = data.case.calibration
        results: list = []

        per_side_linear: dict = {k: [] for k in LINEAR_KINDS}
        per_side_ratio: dict = {k: [] for k in RATIO_KINDS}

        for side in (Side.RIGHT, Side.LEFT):
            linear: dict = {}
            for kind in LINEAR_KINDS:
                ann = data.first(SOURCE_CLASS[kind], side)
                m = measure_line(
                    ann,
                    kind,
                    side,
                    cal,
                    missing_reason=(
                        f"{MEASUREMENT_LABELS[kind]} is not annotated on the "
                        f"{side.display.lower()} side."
                    ),
                )
                linear[kind] = m
                per_side_linear[kind].append(m)
                results.append(m)

            mcw_ann = data.first("mcw_line", side)
            sup_ann = data.first("pmi_superior_line", side)
            inf_ann = data.first("pmi_inferior_line", side)

            pmi_sup = measure_ratio(
                linear[MeasurementKind.MCW],
                linear[MeasurementKind.PMI_SUPERIOR_HEIGHT],
                MeasurementKind.PMI_SUPERIOR,
                side,
                cal,
                mcw_ann,
                sup_ann,
            )
            pmi_inf = measure_ratio(
                linear[MeasurementKind.MCW],
                linear[MeasurementKind.PMI_INFERIOR_HEIGHT],
                MeasurementKind.PMI_INFERIOR,
                side,
                cal,
                mcw_ann,
                inf_ann,
            )
            per_side_ratio[MeasurementKind.PMI_SUPERIOR].append(pmi_sup)
            per_side_ratio[MeasurementKind.PMI_INFERIOR].append(pmi_inf)
            results.extend([pmi_sup, pmi_inf])

        # Bilateral aggregates, assessable sides only.
        for kind, values in per_side_linear.items():
            if kind in (
                MeasurementKind.PMI_SUPERIOR_HEIGHT,
                MeasurementKind.PMI_INFERIOR_HEIGHT,
            ):
                continue
            results.append(bilateral_mean(values, kind, cal))
        for kind, values in per_side_ratio.items():
            results.append(bilateral_mean(values, kind, cal))

        apply_thresholds(results, self.schema)
        return results

    def grades(self, data: CaseData) -> list:
        """Klemetti grades per side as measurement style records (FR 027)."""
        out: list = []
        for side in (Side.RIGHT, Side.LEFT):
            label = data.grade("mci_grade", side)
            m = Measurement(
                kind=MeasurementKind.MCI_GRADE.value,
                side=side.value,
                unit=Unit.NONE.value,
            )
            _stamp_calibration(m, data.case.calibration)
            if label is None:
                m.assessable = False
                m.unavailable_reason = (
                    f"No cortical index grade recorded for the "
                    f"{side.display.lower()} side."
                )
            else:
                grade = label.grade
                m.inputs = {
                    "grade": grade.value,
                    "definition": grade.definition,
                    "rationale": label.rationale,
                }
                m.source_annotation_ids = (
                    [label.region_annotation_id] if label.region_annotation_id else []
                )
                if grade in (MCIGrade.NOT_ASSESSABLE, MCIGrade.UNCERTAIN):
                    m.assessable = False
                    m.unavailable_reason = grade.display
            out.append(m)
        return out


# ---------------------------------------------------------------------------
# Reproduction path for reviewers (AC 004)
# ---------------------------------------------------------------------------


def reproduce_from_export(geometry: dict, calibration: dict) -> list:
    """Recompute every derived value from an exported geometry document.

    The input is the ``annotations.json`` payload and the calibration block that
    ARIA writes. Nothing else is consulted, which is the point: if this function
    reproduces the exported measurements, then the export is self sufficient.
    """
    from .models import Annotation as _Annotation
    from .models import AnnotationSet, Case, CaseData, CategoricalLabel

    cal = Calibration.from_dict(calibration)
    case = Case(calibration=cal)
    annotations = [_Annotation.from_dict(a) for a in geometry.get("annotations", [])]
    categorical = [
        CategoricalLabel(
            **{
                k: v
                for k, v in c.items()
                if k in set(CategoricalLabel.__dataclass_fields__)
            }
        )
        for c in geometry.get("categorical_labels", [])
    ]
    data = CaseData(
        case=case,
        annotation_set=AnnotationSet(),
        annotations=annotations,
        categorical=categorical,
    )
    engine = MeasurementEngine()
    return engine.compute(data) + engine.grades(data)


def measurements_to_rows(measurements: list) -> list:
    """Flatten measurements for tabular export (FR 049)."""
    rows: list = []
    for m in measurements:
        rows.append(
            {
                "measure": m.kind,
                "measure_display": MeasurementKind(m.kind).display,
                "aliases": "|".join(MeasurementKind(m.kind).aliases),
                "side": m.side,
                "value_px": m.value_px,
                "value_mm": m.value_mm,
                "value_ratio": m.value_ratio,
                "unit": m.unit,
                "assessable": m.assessable,
                "unavailable_reason": m.unavailable_reason,
                "aggregation": m.aggregation,
                "sides_used": "|".join(m.sides_used),
                "source_annotation_ids": "|".join(m.source_annotation_ids),
                "calculation_version": m.calculation_version,
                "calibration_source": m.calibration_source,
                "calibration_status": m.calibration_status,
                "calibration_scale": m.calibration_scale,
                "correction_factor": m.correction_factor,
                "millimetres_available": m.millimetres_available,
                "ratio_basis": m.ratio_basis,
                "warnings": " | ".join(m.warnings),
                "screening_rules": " | ".join(s["statement"] for s in m.screening),
            }
        )
    return rows
