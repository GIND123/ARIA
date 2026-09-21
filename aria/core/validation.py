"""Completeness and consistency checks that gate submission (FR 041).

A blocker stops submission. A warning is shown and recorded but does not stop
the annotator. Every issue carries the remedy, so the annotator is told what to
do rather than only what is wrong (NFR 011).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .schema import (
    GeometryType,
    LabelCategory,
    MCIGrade,
    Presence,
    ProjectSchema,
    Side,
    get_class,
)
from .units import ValidationStatus


class Severity(str, Enum):
    BLOCKER = "blocker"
    WARNING = "warning"
    INFO = "info"

    @property
    def glyph(self) -> str:
        return {
            Severity.BLOCKER: "✗",
            Severity.WARNING: "!",
            Severity.INFO: "i",
        }[self]


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    remedy: str = ""
    class_key: str = ""
    side: str = ""
    annotation_id: str = ""
    requirement: str = ""

    @property
    def severity_enum(self) -> Severity:
        return Severity(self.severity)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "remedy": self.remedy,
            "class_key": self.class_key,
            "side": self.side,
            "annotation_id": self.annotation_id,
            "requirement": self.requirement,
        }

    def label(self) -> str:
        parts = [self.message]
        if self.class_key:
            try:
                name = get_class(self.class_key).display_name
            except KeyError:
                name = self.class_key
            side = f" ({Side(self.side).display})" if self.side and self.side != Side.NONE.value else ""
            parts.insert(0, f"{name}{side}:")
        return " ".join(parts)


@dataclass
class ValidationResult:
    issues: list = field(default_factory=list)

    @property
    def blockers(self) -> list:
        return [i for i in self.issues if i.severity == Severity.BLOCKER.value]

    @property
    def warnings(self) -> list:
        return [i for i in self.issues if i.severity == Severity.WARNING.value]

    @property
    def infos(self) -> list:
        return [i for i in self.issues if i.severity == Severity.INFO.value]

    @property
    def can_submit(self) -> bool:
        return not self.blockers

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    def to_dict(self) -> dict:
        return {
            "can_submit": self.can_submit,
            "n_blockers": len(self.blockers),
            "n_warnings": len(self.warnings),
            "issues": [i.to_dict() for i in self.issues],
        }

    def summary(self) -> str:
        if self.can_submit and not self.warnings:
            return "All submission checks passed."
        if self.can_submit:
            return (
                f"Ready to submit with {len(self.warnings)} "
                f"{'warning' if len(self.warnings) == 1 else 'warnings'}."
            )
        return (
            f"{len(self.blockers)} "
            f"{'item blocks' if len(self.blockers) == 1 else 'items block'} "
            f"submission."
        )


class SubmissionValidator:
    """Runs every completeness check for one annotation set."""

    def __init__(self, schema: ProjectSchema | None = None):
        self.schema = schema or ProjectSchema()

    def validate(self, data) -> ValidationResult:
        result = ValidationResult()
        self._check_laterality(data, result)
        self._check_calibration(data, result)
        self._check_required_labels(data, result)
        self._check_geometry(data, result)
        self._check_dependencies(data, result)
        self._check_grades(data, result)
        self._check_bounds(data, result)
        self._check_quality_flags(data, result)
        self._check_consistency(data, result)
        return result

    # -- individual checks ---------------------------------------------------

    def _check_laterality(self, data, result: ValidationResult) -> None:
        """Anatomical right and left must be unambiguous (FR 010)."""
        if not self.schema.require_laterality_confirmation:
            return
        laterality = (data.case.source.image_laterality or "").strip()
        if data.case.laterality_confirmed:
            return
        if laterality:
            result.add(
                Issue(
                    code="laterality_unconfirmed",
                    severity=Severity.WARNING.value,
                    message=(
                        f"The source reports laterality {laterality!r} but it has "
                        f"not been confirmed for this case."
                    ),
                    remedy="Open Case, Orientation and confirm anatomical right and left.",
                    requirement="FR 010",
                )
            )
        else:
            result.add(
                Issue(
                    code="laterality_missing",
                    severity=Severity.BLOCKER.value,
                    message=(
                        "The source does not carry image laterality, so anatomical "
                        "right and left must be confirmed before submission."
                    ),
                    remedy="Open Case, Orientation and confirm anatomical right and left.",
                    requirement="FR 010",
                )
            )

    def _check_calibration(self, data, result: ValidationResult) -> None:
        cal = data.case.calibration
        if cal.is_validated:
            if cal.is_anisotropic:
                result.add(
                    Issue(
                        code="calibration_anisotropic",
                        severity=Severity.INFO.value,
                        message=(
                            "Row and column spacing differ. Millimetre distances "
                            "are computed per axis."
                        ),
                        remedy="No action needed. Confirm the header values are intended.",
                        requirement="FR 031",
                    )
                )
            return

        severity = (
            Severity.BLOCKER.value
            if self.schema.require_calibration_for_submission
            else Severity.WARNING.value
        )
        if cal.status is ValidationStatus.REJECTED:
            result.add(
                Issue(
                    code="calibration_rejected",
                    severity=severity,
                    message="The calibration for this case was rejected by a reviewer.",
                    remedy="Recalibrate using a known length, or record why millimetre values are not required.",
                    requirement="FR 007",
                )
            )
        elif cal.has_spacing:
            result.add(
                Issue(
                    code="calibration_unvalidated",
                    severity=severity,
                    message=(
                        "Pixel spacing is present but has not been validated, so "
                        "millimetre values are withheld."
                    ),
                    remedy="Open Calibration and validate the spacing, or complete a manual calibration.",
                    requirement="FR 007",
                )
            )
        else:
            result.add(
                Issue(
                    code="calibration_absent",
                    severity=severity,
                    message=(
                        "No spatial calibration is available. Pixel measurements "
                        "and dimensionless ratios are recorded; millimetre values "
                        "are marked unavailable."
                    ),
                    remedy="Complete a manual calibration using a known length if millimetre values are required.",
                    requirement="FR 008",
                )
            )

    def _check_required_labels(self, data, result: ValidationResult) -> None:
        for key in self.schema.required_classes:
            if key in self.schema.allowed_omissions:
                continue
            try:
                cls = get_class(key)
            except KeyError:
                continue
            if cls.key == "hemimandible" and self.schema.mandible_mode == "whole":
                continue
            if cls.key == "mandible_whole" and self.schema.mandible_mode != "whole":
                continue

            sides = (Side.RIGHT, Side.LEFT) if cls.side_scoped else (Side.MIDLINE,)
            for side in sides:
                matches = data.by_class(key, side if cls.side_scoped else None)
                if not matches:
                    result.add(
                        Issue(
                            code="required_label_missing",
                            severity=Severity.BLOCKER.value,
                            message="This required label has not been recorded.",
                            remedy=(
                                f"Draw {cls.display_name} on the {side.display.lower()} "
                                f"side, or mark it explicitly as not visible or not "
                                f"assessable."
                            ),
                            class_key=key,
                            side=side.value,
                            requirement=" ".join(cls.requirements),
                        )
                    )
                    continue

                ann = matches[0]
                if ann.presence == Presence.PRESENT.value and not ann.coordinates:
                    result.add(
                        Issue(
                            code="required_label_empty",
                            severity=Severity.BLOCKER.value,
                            message="The label is marked present but carries no geometry.",
                            remedy="Draw the geometry, or change the presence state to not visible or not assessable.",
                            class_key=key,
                            side=side.value,
                            annotation_id=ann.id,
                            requirement="FR 017",
                        )
                    )

    def _check_geometry(self, data, result: ValidationResult) -> None:
        for ann in data.live_annotations():
            try:
                cls = get_class(ann.class_key)
            except KeyError:
                result.add(
                    Issue(
                        code="unknown_class",
                        severity=Severity.BLOCKER.value,
                        message=f"The label class {ann.class_key!r} is not in the schema.",
                        remedy="Delete the object, or update the project schema.",
                        annotation_id=ann.id,
                        requirement="FR 053",
                    )
                )
                continue

            if ann.presence != Presence.PRESENT.value:
                if ann.coordinates:
                    result.add(
                        Issue(
                            code="absent_with_geometry",
                            severity=Severity.WARNING.value,
                            message=(
                                f"Recorded as {ann.presence_enum.display.lower()} but "
                                f"geometry is still stored."
                            ),
                            remedy="Remove the geometry, or set the presence state back to present.",
                            class_key=ann.class_key,
                            side=ann.side,
                            annotation_id=ann.id,
                            requirement="FR 017",
                        )
                    )
                continue

            pts = ann.points()
            gt = GeometryType(ann.geometry_type)
            n = len(pts)

            if gt is GeometryType.LINE and n != 2:
                result.add(
                    Issue(
                        code="line_vertex_count",
                        severity=Severity.BLOCKER.value,
                        message=f"A two endpoint line must have exactly two points, found {n}.",
                        remedy="Redraw the line with a start point and an end point.",
                        class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        requirement="FR 022",
                    )
                )
            elif n < cls.min_vertices:
                result.add(
                    Issue(
                        code="too_few_vertices",
                        severity=Severity.BLOCKER.value,
                        message=f"This class needs at least {cls.min_vertices} points, found {n}.",
                        remedy="Add more points, or mark the label as not visible.",
                        class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                    )
                )

            if gt is GeometryType.LINE and n == 2:
                from .geometry import euclidean

                if euclidean(pts[0], pts[1]) < 1.0:
                    result.add(
                        Issue(
                            code="degenerate_line",
                            severity=Severity.BLOCKER.value,
                            message="The two endpoints are less than one pixel apart.",
                            remedy="Redraw the line across the structure being measured.",
                            class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        )
                    )

            if gt is GeometryType.POLYGON and n >= 3:
                from .geometry import polygon_area

                if polygon_area(pts) < 4.0:
                    result.add(
                        Issue(
                            code="degenerate_polygon",
                            severity=Severity.WARNING.value,
                            message="The polygon encloses almost no area.",
                            remedy="Check the outline; it may have been closed too early.",
                            class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        )
                    )

            if cls.side_scoped and ann.side not in (Side.RIGHT.value, Side.LEFT.value):
                result.add(
                    Issue(
                        code="side_missing",
                        severity=Severity.BLOCKER.value,
                        message="This class is side specific but no side is recorded.",
                        remedy="Set the side to Right or Left.",
                        class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        requirement="FR 017",
                    )
                )

    def _check_dependencies(self, data, result: ValidationResult) -> None:
        for ann in data.live_annotations():
            if not ann.is_assessable:
                continue
            try:
                cls = get_class(ann.class_key)
            except KeyError:
                continue
            side = Side(ann.side) if cls.side_scoped else None
            for dep in cls.depends_on:
                try:
                    dep_cls = get_class(dep)
                except KeyError:
                    continue
                lookup_side = side if dep_cls.side_scoped else None
                if data.present(dep, lookup_side) is None:
                    result.add(
                        Issue(
                            code="dependency_missing",
                            severity=Severity.WARNING.value,
                            message=(
                                f"{cls.display_name} is drawn but "
                                f"{dep_cls.display_name} is not recorded, so the "
                                f"construction cannot be checked."
                            ),
                            remedy=f"Record {dep_cls.display_name} on the same side.",
                            class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        )
                    )

    def _check_grades(self, data, result: ValidationResult) -> None:
        if not self.schema.require_mci_grade:
            return
        for side in (Side.RIGHT, Side.LEFT):
            label = data.grade("mci_grade", side)
            if label is None:
                result.add(
                    Issue(
                        code="mci_grade_missing",
                        severity=Severity.BLOCKER.value,
                        message="No cortical index grade has been assigned for this side.",
                        remedy="Assign C1, C2, C3, Not assessable or Uncertain in the Grading section.",
                        class_key="mci_grade", side=side.value,
                        requirement="FR 027",
                    )
                )
                continue
            if label.grade in (MCIGrade.C1, MCIGrade.C2, MCIGrade.C3):
                if not label.region_annotation_id and data.present("mci_region", side) is None:
                    result.add(
                        Issue(
                            code="mci_region_missing",
                            severity=Severity.WARNING.value,
                            message=(
                                "A grade was assigned but the region it was read "
                                "from is not marked."
                            ),
                            remedy="Draw the assessment region on the inferior cortex distal to the mental foramen.",
                            class_key="mci_region", side=side.value,
                            requirement="FR 027",
                        )
                    )
            if label.grade is MCIGrade.UNCERTAIN and not label.rationale:
                result.add(
                    Issue(
                        code="uncertain_without_rationale",
                        severity=Severity.WARNING.value,
                        message="The grade is Uncertain but no reason was recorded.",
                        remedy="Add a short note explaining what prevents a confident grade.",
                        class_key="mci_grade", side=side.value,
                    )
                )

    def _check_bounds(self, data, result: ValidationResult) -> None:
        rows, cols = data.case.shape
        if rows <= 0 or cols <= 0:
            return
        for ann in data.live_annotations():
            for x, y in ann.points():
                if x < -0.5 or y < -0.5 or x > cols + 0.5 or y > rows + 0.5:
                    result.add(
                        Issue(
                            code="coordinate_out_of_bounds",
                            severity=Severity.BLOCKER.value,
                            message=(
                                f"A point at ({x:.1f}, {y:.1f}) lies outside the "
                                f"image, which is {cols} by {rows} pixels."
                            ),
                            remedy="Move the point back inside the image.",
                            class_key=ann.class_key, side=ann.side, annotation_id=ann.id,
                        )
                    )
                    break

    def _check_quality_flags(self, data, result: ValidationResult) -> None:
        for flag in data.quality_flags:
            if flag.flag == "other" and not flag.comment.strip():
                result.add(
                    Issue(
                        code="other_flag_without_comment",
                        severity=Severity.BLOCKER.value,
                        message="The quality flag Other requires a comment.",
                        remedy="Describe the finding in the comment field, or choose a specific flag.",
                        requirement="FR 016",
                    )
                )

        not_visible = [
            a for a in data.live_annotations()
            if a.presence in (Presence.NOT_VISIBLE.value, Presence.NOT_ASSESSABLE.value)
        ]
        flagged = {f.flag for f in data.quality_flags}
        if not_visible and not flagged:
            result.add(
                Issue(
                    code="omission_without_flag",
                    severity=Severity.WARNING.value,
                    message=(
                        f"{len(not_visible)} labels are recorded as not visible or "
                        f"not assessable but no case quality flag explains why."
                    ),
                    remedy="Add the quality flag that describes the image problem.",
                    requirement="FR 016",
                )
            )

    def _check_consistency(self, data, result: ValidationResult) -> None:
        """Cross checks that catch a plausible but wrong annotation."""
        from .geometry import closest_point_on_polyline, euclidean

        for side in (Side.RIGHT, Side.LEFT):
            mf = data.present("mental_foramen_centre", side)
            sup = data.present("mental_foramen_superior", side)
            inf = data.present("mental_foramen_inferior", side)
            if mf and sup and inf:
                cy = mf.points()[0][1]
                sy = sup.points()[0][1]
                iy = inf.points()[0][1]
                if not (sy < cy < iy):
                    result.add(
                        Issue(
                            code="foramen_margins_order",
                            severity=Severity.WARNING.value,
                            message=(
                                "The superior margin is not above the centre and "
                                "the inferior margin below it."
                            ),
                            remedy="Check that the superior and inferior margin points are not swapped.",
                            class_key="mental_foramen_centre", side=side.value,
                            requirement="FR 018",
                        )
                    )

            peri = data.present("periosteal_border", side)
            endo = data.present("endosteal_border", side)
            mcw = data.present("mcw_line", side)
            if peri and endo and mcw:
                p0, p1 = mcw.points()[0], mcw.points()[-1]
                d_peri = euclidean(p0, closest_point_on_polyline(p0, peri.points())[0])
                d_endo = euclidean(p1, closest_point_on_polyline(p1, endo.points())[0])
                tol = max(6.0, self.schema.line_endpoint_tolerance_px)
                if d_peri > tol or d_endo > tol:
                    result.add(
                        Issue(
                            code="mcw_endpoints_off_cortex",
                            severity=Severity.WARNING.value,
                            message=(
                                f"The cortical width endpoints sit {d_peri:.1f} px "
                                f"and {d_endo:.1f} px from the traced borders."
                            ),
                            remedy="Snap the endpoints to the periosteal and endosteal borders.",
                            class_key="mcw_line", side=side.value,
                            annotation_id=mcw.id,
                            requirement="FR 022",
                        )
                    )

            pmi_s = data.present("pmi_superior_line", side)
            pmi_i = data.present("pmi_inferior_line", side)
            if mcw and pmi_s and pmi_i:
                s_len = euclidean(pmi_s.points()[0], pmi_s.points()[-1])
                i_len = euclidean(pmi_i.points()[0], pmi_i.points()[-1])
                if i_len >= s_len:
                    result.add(
                        Issue(
                            code="pmi_height_order",
                            severity=Severity.WARNING.value,
                            message=(
                                "The inferior margin height is not shorter than the "
                                "superior margin height."
                            ),
                            remedy="Check that the two panoramic mandibular index heights are not swapped.",
                            class_key="pmi_inferior_line", side=side.value,
                            requirement="FR 026",
                        )
                    )

            roi = data.by_class("trabecular_roi", side)
            for r in roi:
                if peri and r.points():
                    from .geometry import bounding_box

                    x, y, w, h = bounding_box(r.points())
                    centre = (x + w / 2.0, y + h / 2.0)
                    d = euclidean(centre, closest_point_on_polyline(centre, peri.points())[0])
                    if d < max(w, h) / 2.0:
                        result.add(
                            Issue(
                                code="roi_overlaps_cortex",
                                severity=Severity.WARNING.value,
                                message=(
                                    "A trabecular region appears to overlap the "
                                    "cortical border."
                                ),
                                remedy="Move the region clear of the cortex, roots and the mandibular canal.",
                                class_key="trabecular_roi", side=side.value,
                                annotation_id=r.id,
                                requirement="FR 029",
                            )
                        )


def validate_for_submission(data, schema: ProjectSchema | None = None) -> ValidationResult:
    return SubmissionValidator(schema).validate(data)
