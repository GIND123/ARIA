"""Agreement statistics for duplicate annotation and adjudication (FR 044).

ARIA computes agreement and reports it. It does not impose a pass mark. There
is no universal agreement cutoff in this module by design (FR 045); thresholds
come from the project configuration that an administrator sets from the
approved annotation protocol.

Metrics provided
----------------
* point distance error, per landmark and summarised,
* line endpoint error, matched end to end and swapped, whichever is smaller,
* absolute measurement difference, with Bland and Altman bias and limits,
* intraclass correlation for continuous measures, forms (2,1) and (3,1),
* Dice and intersection over union for masks,
* Cohen kappa and linearly or quadratically weighted kappa for categorical
  labels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .geometry import euclidean
from .schema import MCIGrade


@dataclass
class AgreementItem:
    """One comparison between two annotators for one label on one side."""

    label: str
    side: str
    metric: str
    value: float | None
    unit: str
    n: int = 1
    detail: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "side": self.side,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "n": self.n,
            "note": self.note,
            **{f"detail_{k}": v for k, v in self.detail.items()},
        }


# ---------------------------------------------------------------------------
# Geometric error
# ---------------------------------------------------------------------------


def point_distance_error(a: tuple, b: tuple) -> float:
    """Straight line distance between two placements of the same landmark."""
    return euclidean(a, b)


def line_endpoint_error(
    line_a: tuple, line_b: tuple
) -> dict:
    """Endpoint error between two drawings of the same line.

    Both pairings are tested and the smaller total is kept, because whether an
    annotator drew a cortical width line from the periosteal end or the
    endosteal end is a drawing habit and not a disagreement.
    """
    a0, a1 = line_a
    b0, b1 = line_b
    direct = euclidean(a0, b0) + euclidean(a1, b1)
    swapped = euclidean(a0, b1) + euclidean(a1, b0)
    if direct <= swapped:
        e0, e1, order = euclidean(a0, b0), euclidean(a1, b1), "direct"
    else:
        e0, e1, order = euclidean(a0, b1), euclidean(a1, b0), "swapped"
    len_a = euclidean(a0, a1)
    len_b = euclidean(b0, b1)
    return {
        "start_error_px": e0,
        "end_error_px": e1,
        "mean_endpoint_error_px": (e0 + e1) / 2.0,
        "max_endpoint_error_px": max(e0, e1),
        "length_difference_px": abs(len_a - len_b),
        "pairing": order,
    }


def contour_distance(
    poly_a, poly_b, sample_spacing: float = 5.0
) -> dict:
    """Symmetric distance between two traced contours.

    Both contours are resampled to even spacing, then every sample on each is
    matched to its closest point on the other. The mean of the two directions is
    reported along with the worst case, which is where contour disagreements
    actually show up.
    """
    from .geometry import closest_point_on_polyline, resample_polyline

    if len(poly_a) < 2 or len(poly_b) < 2:
        return {"mean_contour_distance_px": None, "max_contour_distance_px": None}

    sa = resample_polyline(poly_a, sample_spacing)
    sb = resample_polyline(poly_b, sample_spacing)
    d_ab = [euclidean(p, closest_point_on_polyline(p, sb)[0]) for p in sa]
    d_ba = [euclidean(p, closest_point_on_polyline(p, sa)[0]) for p in sb]
    both = d_ab + d_ba
    return {
        "mean_contour_distance_px": float(np.mean(both)),
        "max_contour_distance_px": float(np.max(both)),
        "hausdorff_px": float(max(max(d_ab), max(d_ba))),
        "n_samples": len(both),
    }


# ---------------------------------------------------------------------------
# Mask overlap
# ---------------------------------------------------------------------------


def dice(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    """Dice similarity coefficient. Two empty masks agree perfectly."""
    a = np.asarray(mask_a, dtype=bool)
    b = np.asarray(mask_b, dtype=bool)
    if a.shape != b.shape:
        raise ValueError("Masks must have the same shape to be compared")
    total = a.sum() + b.sum()
    if total == 0:
        return 1.0
    return float(2.0 * np.logical_and(a, b).sum() / total)


def iou(mask_a: np.ndarray, mask_b: np.ndarray) -> float:
    """Intersection over union. Two empty masks agree perfectly."""
    a = np.asarray(mask_a, dtype=bool)
    b = np.asarray(mask_b, dtype=bool)
    if a.shape != b.shape:
        raise ValueError("Masks must have the same shape to be compared")
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum() / union)


# ---------------------------------------------------------------------------
# Continuous measures
# ---------------------------------------------------------------------------


def absolute_difference(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs(a - b)


def bland_altman(values_a, values_b) -> dict:
    """Bias and limits of agreement for paired continuous measurements.

    Reported alongside the intraclass correlation because a high correlation can
    still hide a systematic offset between two annotators, and the offset is the
    part a protocol can actually fix.
    """
    a = np.asarray([v for v in values_a], dtype=float)
    b = np.asarray([v for v in values_b], dtype=float)
    ok = ~(np.isnan(a) | np.isnan(b))
    a, b = a[ok], b[ok]
    if len(a) < 2:
        return {"n": int(len(a)), "bias": None, "lower_limit": None, "upper_limit": None}
    diff = a - b
    bias = float(diff.mean())
    sd = float(diff.std(ddof=1))
    return {
        "n": int(len(a)),
        "bias": bias,
        "sd_of_differences": sd,
        "lower_limit": bias - 1.96 * sd,
        "upper_limit": bias + 1.96 * sd,
        "mean_absolute_difference": float(np.abs(diff).mean()),
        "max_absolute_difference": float(np.abs(diff).max()),
    }


def icc(ratings: np.ndarray, form: str = "2,1") -> dict:
    """Intraclass correlation for a subjects by raters matrix.

    ``form`` is ``"2,1"`` for two way random effects with absolute agreement, or
    ``"3,1"`` for two way mixed effects with consistency. Single measure in both
    cases, which is the relevant form when one annotator produces the value that
    will actually be used.
    """
    x = np.asarray(ratings, dtype=float)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 2:
        return {"icc": None, "n_subjects": int(x.shape[0]) if x.ndim == 2 else 0,
                "note": "At least two subjects and two raters are required."}

    complete = ~np.isnan(x).any(axis=1)
    x = x[complete]
    n, k = x.shape
    if n < 2:
        return {"icc": None, "n_subjects": int(n),
                "note": "Fewer than two complete cases after removing missing values."}

    grand = x.mean()
    ms_rows = k * ((x.mean(axis=1) - grand) ** 2).sum() / (n - 1)
    ms_cols = n * ((x.mean(axis=0) - grand) ** 2).sum() / (k - 1)
    residual = x - x.mean(axis=1, keepdims=True) - x.mean(axis=0, keepdims=True) + grand
    ms_error = (residual ** 2).sum() / ((n - 1) * (k - 1))

    if ms_rows <= 0:
        return {"icc": 0.0, "n_subjects": int(n), "n_raters": int(k),
                "note": "No between subject variance."}

    if form == "3,1":
        value = (ms_rows - ms_error) / (ms_rows + (k - 1) * ms_error)
    else:
        denom = ms_rows + (k - 1) * ms_error + k * (ms_cols - ms_error) / n
        value = (ms_rows - ms_error) / denom if denom != 0 else float("nan")

    return {
        "icc": float(value),
        "form": form,
        "n_subjects": int(n),
        "n_raters": int(k),
        "ms_between_subjects": float(ms_rows),
        "ms_between_raters": float(ms_cols),
        "ms_error": float(ms_error),
    }


# ---------------------------------------------------------------------------
# Categorical measures
# ---------------------------------------------------------------------------

#: Ordered categories used when a weighted kappa is requested for the cortical
#: index. Values that are not a grade do not sit on the ordinal scale, so they
#: are compared with unweighted kappa only.
MCI_ORDER = (MCIGrade.C1.value, MCIGrade.C2.value, MCIGrade.C3.value)


def confusion_matrix(labels_a, labels_b, categories) -> np.ndarray:
    index = {c: i for i, c in enumerate(categories)}
    m = np.zeros((len(categories), len(categories)), dtype=np.int64)
    for a, b in zip(labels_a, labels_b):
        if a in index and b in index:
            m[index[a], index[b]] += 1
    return m


def cohen_kappa(labels_a, labels_b, categories=None, weights: str = "none") -> dict:
    """Cohen kappa, optionally linearly or quadratically weighted.

    ``weights`` is ``none``, ``linear`` or ``quadratic``. Weighting is only
    meaningful when the categories are ordered, so the caller passes an ordered
    category list for weighted forms.
    """
    labels_a = list(labels_a)
    labels_b = list(labels_b)
    if categories is None:
        categories = sorted(set(labels_a) | set(labels_b))
    if not categories:
        return {"kappa": None, "n": 0, "note": "No categories to compare."}

    m = confusion_matrix(labels_a, labels_b, categories)
    n = int(m.sum())
    if n == 0:
        return {"kappa": None, "n": 0, "note": "No paired observations."}

    k = len(categories)
    observed = m.astype(float) / n
    row = observed.sum(axis=1)
    col = observed.sum(axis=0)
    expected = np.outer(row, col)

    if weights == "none":
        w = 1.0 - np.eye(k)
    else:
        i, j = np.mgrid[0:k, 0:k]
        d = np.abs(i - j).astype(float)
        denom = (k - 1) if k > 1 else 1
        w = d / denom if weights == "linear" else (d / denom) ** 2

    po = float((w * observed).sum())
    pe = float((w * expected).sum())
    if abs(pe) < 1e-12:
        kappa = 1.0 if abs(po) < 1e-12 else 0.0
    else:
        kappa = 1.0 - po / pe

    exact = float(np.trace(m) / n)
    return {
        "kappa": float(kappa),
        "weights": weights,
        "n": n,
        "categories": list(categories),
        "observed_agreement": exact,
        "confusion_matrix": m.tolist(),
    }


def percent_agreement(labels_a, labels_b) -> dict:
    pairs = [(a, b) for a, b in zip(labels_a, labels_b)]
    if not pairs:
        return {"percent_agreement": None, "n": 0}
    same = sum(1 for a, b in pairs if a == b)
    return {"percent_agreement": 100.0 * same / len(pairs), "n": len(pairs)}


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------


@dataclass
class AgreementReport:
    """Everything the review module needs to present a comparison."""

    case_ids: list = field(default_factory=list)
    annotator_a: str = ""
    annotator_b: str = ""
    items: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    #: Project configured tolerances, recorded so a reader knows what the
    #: highlighting in the report was based on (FR 045).
    tolerances: dict = field(default_factory=dict)
    note: str = (
        "ARIA reports agreement and does not impose a pass mark. Any acceptance "
        "threshold shown here comes from the project configuration approved in "
        "the annotation protocol."
    )

    def add(self, item: AgreementItem) -> None:
        self.items.append(item)

    def to_dict(self) -> dict:
        return {
            "case_ids": list(self.case_ids),
            "annotator_a": self.annotator_a,
            "annotator_b": self.annotator_b,
            "items": [i.to_dict() for i in self.items],
            "summary": dict(self.summary),
            "tolerances": dict(self.tolerances),
            "note": self.note,
        }


def compare_case_pair(data_a, data_b, schema, image_shape=None) -> AgreementReport:
    """Compare two annotation sets over the same case.

    ``data_a`` and ``data_b`` are :class:`~aria.core.models.CaseData` values.
    """
    from .measurements import LINEAR_KINDS, MeasurementEngine, SOURCE_CLASS
    from .schema import GeometryType, Side, get_class

    report = AgreementReport(
        case_ids=[data_a.case.id],
        annotator_a=data_a.annotation_set.annotator_id,
        annotator_b=data_b.annotation_set.annotator_id,
        tolerances={
            "point_tolerance_px": schema.point_tolerance_px,
            "line_endpoint_tolerance_px": schema.line_endpoint_tolerance_px,
            "measurement_tolerance_mm": schema.measurement_tolerance_mm,
            "mask_dice_tolerance": schema.mask_dice_tolerance,
        },
    )

    for cls in schema.active_classes():
        sides = (Side.RIGHT, Side.LEFT) if cls.side_scoped else (Side.NONE, Side.MIDLINE)
        for side in sides:
            a = data_a.present(cls.key, side if cls.side_scoped else None)
            b = data_b.present(cls.key, side if cls.side_scoped else None)
            if a is None or b is None:
                if a is not None or b is not None:
                    report.add(
                        AgreementItem(
                            label=cls.key,
                            side=side.value,
                            metric="presence_mismatch",
                            value=None,
                            unit="none",
                            note=(
                                "One annotator recorded this label and the other "
                                "did not."
                            ),
                        )
                    )
                continue

            gt = GeometryType(cls.geometry)
            if gt is GeometryType.POINT:
                pa, pb = a.points(), b.points()
                if pa and pb:
                    report.add(
                        AgreementItem(
                            label=cls.key,
                            side=side.value,
                            metric="point_distance_error",
                            value=point_distance_error(pa[0], pb[0]),
                            unit="px",
                        )
                    )
            elif gt is GeometryType.LINE:
                pa, pb = a.points(), b.points()
                if len(pa) >= 2 and len(pb) >= 2:
                    d = line_endpoint_error((pa[0], pa[-1]), (pb[0], pb[-1]))
                    report.add(
                        AgreementItem(
                            label=cls.key,
                            side=side.value,
                            metric="line_endpoint_error",
                            value=d["mean_endpoint_error_px"],
                            unit="px",
                            detail=d,
                        )
                    )
            elif gt is GeometryType.POLYLINE:
                d = contour_distance(a.points(), b.points())
                report.add(
                    AgreementItem(
                        label=cls.key,
                        side=side.value,
                        metric="contour_distance",
                        value=d.get("mean_contour_distance_px"),
                        unit="px",
                        detail=d,
                    )
                )
            elif gt in (GeometryType.POLYGON, GeometryType.MASK, GeometryType.BOX, GeometryType.ROI_RECT):
                if image_shape is not None:
                    from .geometry import rasterize_polygon, box_to_polygon

                    def _mask(ann):
                        pts = ann.points()
                        if gt in (GeometryType.BOX, GeometryType.ROI_RECT) and len(pts) >= 2:
                            x0, y0 = pts[0]
                            x1, y1 = pts[-1]
                            pts = box_to_polygon(
                                min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
                            )
                        return rasterize_polygon(pts, image_shape)

                    ma, mb = _mask(a), _mask(b)
                    report.add(
                        AgreementItem(
                            label=cls.key, side=side.value, metric="dice",
                            value=dice(ma, mb), unit="ratio",
                        )
                    )
                    report.add(
                        AgreementItem(
                            label=cls.key, side=side.value, metric="iou",
                            value=iou(ma, mb), unit="ratio",
                        )
                    )

    # Derived measurement differences.
    engine = MeasurementEngine(schema)
    ms_a = {(m.kind, m.side): m for m in engine.compute(data_a)}
    ms_b = {(m.kind, m.side): m for m in engine.compute(data_b)}
    for key in sorted(set(ms_a) & set(ms_b)):
        ma, mb = ms_a[key], ms_b[key]
        if not (ma.assessable and mb.assessable):
            continue
        if ma.value_mm is not None and mb.value_mm is not None:
            unit, va, vb = "mm", ma.value_mm, mb.value_mm
        elif ma.value_ratio is not None and mb.value_ratio is not None:
            unit, va, vb = "ratio", ma.value_ratio, mb.value_ratio
        elif ma.value_px is not None and mb.value_px is not None:
            unit, va, vb = "px", ma.value_px, mb.value_px
        else:
            continue
        report.add(
            AgreementItem(
                label=key[0], side=key[1], metric="absolute_measurement_difference",
                value=abs(va - vb), unit=unit,
                detail={"value_a": va, "value_b": vb},
            )
        )

    # Categorical grades.
    from .schema import Side as _Side

    for side in (_Side.RIGHT, _Side.LEFT):
        ga = data_a.grade("mci_grade", side)
        gb = data_b.grade("mci_grade", side)
        if ga and gb:
            report.add(
                AgreementItem(
                    label="mci_grade", side=side.value, metric="exact_match",
                    value=1.0 if ga.value == gb.value else 0.0, unit="ratio",
                    detail={"grade_a": ga.value, "grade_b": gb.value},
                )
            )

    report.summary = _summarise(report.items)
    return report


def _summarise(items) -> dict:
    by_metric: dict = {}
    for item in items:
        if item.value is None:
            continue
        by_metric.setdefault(item.metric, []).append(item.value)
    summary: dict = {}
    for metric, values in by_metric.items():
        arr = np.asarray(values, dtype=float)
        summary[metric] = {
            "n": int(arr.size),
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "min": float(arr.min()),
            "max": float(arr.max()),
            "sd": float(arr.std(ddof=1)) if arr.size > 1 else 0.0,
        }
    return summary


def aggregate_reports(reports, schema) -> dict:
    """Roll several per case comparisons into study level statistics.

    Intraclass correlation and kappa only make sense over a set of cases, so
    they are computed here rather than per case.
    """
    from .measurements import MeasurementKind

    measure_pairs: dict = {}
    grade_pairs: dict = {"a": [], "b": []}

    for rep in reports:
        for item in rep.items:
            if item.metric == "absolute_measurement_difference":
                va = item.detail.get("value_a")
                vb = item.detail.get("value_b")
                if va is None or vb is None:
                    continue
                key = (item.label, item.side, item.unit)
                measure_pairs.setdefault(key, {"a": [], "b": []})
                measure_pairs[key]["a"].append(va)
                measure_pairs[key]["b"].append(vb)
            elif item.metric == "exact_match" and item.label == "mci_grade":
                grade_pairs["a"].append(item.detail.get("grade_a"))
                grade_pairs["b"].append(item.detail.get("grade_b"))

    continuous: list = []
    for (label, side, unit), pair in sorted(measure_pairs.items()):
        ratings = np.column_stack(
            [np.asarray(pair["a"], dtype=float), np.asarray(pair["b"], dtype=float)]
        )
        entry = {
            "measure": label,
            "side": side,
            "unit": unit,
            "n": len(pair["a"]),
            "icc_2_1": icc(ratings, "2,1"),
            "icc_3_1": icc(ratings, "3,1"),
            "bland_altman": bland_altman(pair["a"], pair["b"]),
        }
        try:
            entry["measure_display"] = MeasurementKind(label).display
        except ValueError:
            entry["measure_display"] = label
        continuous.append(entry)

    categorical: list = []
    if grade_pairs["a"]:
        all_cats = sorted(set(grade_pairs["a"]) | set(grade_pairs["b"]))
        ordered = [c for c in MCI_ORDER if c in all_cats]
        other = [c for c in all_cats if c not in MCI_ORDER]
        categorical.append(
            {
                "label": "mci_grade",
                "unweighted": cohen_kappa(grade_pairs["a"], grade_pairs["b"], all_cats, "none"),
                "linear_weighted": (
                    cohen_kappa(
                        [g for g in grade_pairs["a"]],
                        [g for g in grade_pairs["b"]],
                        ordered,
                        "linear",
                    )
                    if len(ordered) > 1
                    else {"kappa": None, "note": "Not enough ordered grades present."}
                ),
                "quadratic_weighted": (
                    cohen_kappa(grade_pairs["a"], grade_pairs["b"], ordered, "quadratic")
                    if len(ordered) > 1
                    else {"kappa": None, "note": "Not enough ordered grades present."}
                ),
                "percent_agreement": percent_agreement(grade_pairs["a"], grade_pairs["b"]),
                "non_ordinal_categories_present": other,
            }
        )

    return {
        "n_cases": len(reports),
        "continuous_measures": continuous,
        "categorical_labels": categorical,
        "tolerances": {
            "point_tolerance_px": schema.point_tolerance_px,
            "line_endpoint_tolerance_px": schema.line_endpoint_tolerance_px,
            "measurement_tolerance_mm": schema.measurement_tolerance_mm,
            "mask_dice_tolerance": schema.mask_dice_tolerance,
        },
        "note": (
            "Thresholds shown are project configuration from the approved "
            "annotation protocol. ARIA does not apply a universal cutoff."
        ),
    }
