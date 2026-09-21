"""Spatial calibration and unit handling.

Millimetre values are only ever produced from a calibration that has been
validated (FR 007). Anything else is reported in pixels, and the millimetre
column is explicitly marked unavailable rather than left blank or filled with a
guess (FR 008).

Panoramic specific behaviour
----------------------------
A panoramic unit does not image a flat object at a single magnification. The
vertical and the horizontal magnification differ, and the horizontal factor
varies across the arch. Detector pixel spacing from the DICOM header therefore
describes the detector, not the patient. ARIA keeps the two ideas apart:

* ``row_spacing_mm`` and ``col_spacing_mm`` are the detector scale,
* ``magnification_vertical`` and ``magnification_horizontal`` are the optional
  correction from detector geometry to anatomical size.

Magnification correction is refused unless the project has enabled it and an
administrator has recorded the device policy, because applying an unverified
factor silently changes every measurement in a study.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from enum import Enum


class CalibrationSource(str, Enum):
    NONE = "none"
    DICOM_PIXEL_SPACING = "dicom_pixel_spacing"
    DICOM_IMAGER_PIXEL_SPACING = "dicom_imager_pixel_spacing"
    DICOM_NOMINAL_SCANNED_PIXEL_SPACING = "dicom_nominal_scanned_pixel_spacing"
    MANUAL_KNOWN_LENGTH = "manual_known_length"
    PROJECT_DEVICE_POLICY = "project_device_policy"

    @property
    def display(self) -> str:
        return {
            CalibrationSource.NONE: "None",
            CalibrationSource.DICOM_PIXEL_SPACING: "DICOM Pixel Spacing",
            CalibrationSource.DICOM_IMAGER_PIXEL_SPACING: "DICOM Imager Pixel Spacing",
            CalibrationSource.DICOM_NOMINAL_SCANNED_PIXEL_SPACING: (
                "DICOM Nominal Scanned Pixel Spacing"
            ),
            CalibrationSource.MANUAL_KNOWN_LENGTH: "Manual calibration, known length",
            CalibrationSource.PROJECT_DEVICE_POLICY: "Project device policy",
        }[self]


class ValidationStatus(str, Enum):
    NOT_AVAILABLE = "not_available"
    UNVALIDATED = "unvalidated"
    VALIDATED = "validated"
    REJECTED = "rejected"

    @property
    def display(self) -> str:
        return {
            ValidationStatus.NOT_AVAILABLE: "Not available",
            ValidationStatus.UNVALIDATED: "Present, not validated",
            ValidationStatus.VALIDATED: "Validated",
            ValidationStatus.REJECTED: "Rejected by reviewer",
        }[self]

    @property
    def glyph(self) -> str:
        return {
            ValidationStatus.NOT_AVAILABLE: "—",
            ValidationStatus.UNVALIDATED: "!",
            ValidationStatus.VALIDATED: "✓",
            ValidationStatus.REJECTED: "✗",
        }[self]


class Unit(str, Enum):
    PIXEL = "px"
    MILLIMETRE = "mm"
    RATIO = "ratio"
    DEGREE = "deg"
    NONE = "none"


#: Millimetre values are refused when the reported detector spacing is outside
#: this range. Panoramic detectors sit near 0.05 mm to 0.2 mm per pixel; a value
#: far outside that almost always means a header field was misread or a unit was
#: assumed wrongly, and a silent bad scale is worse than no scale at all.
PLAUSIBLE_SPACING_MM = (0.005, 1.0)

#: Panoramic magnification factors outside this range are rejected as data entry
#: errors rather than applied.
PLAUSIBLE_MAGNIFICATION = (1.0, 1.6)


@dataclass
class Calibration:
    """The spatial calibration attached to one case.

    Every quantitative result displays the source, value, unit, validation
    status and any correction factor from this record (FR 009).
    """

    source: CalibrationSource = CalibrationSource.NONE
    row_spacing_mm: float | None = None      # millimetres per pixel, vertical
    col_spacing_mm: float | None = None      # millimetres per pixel, horizontal
    status: ValidationStatus = ValidationStatus.NOT_AVAILABLE
    magnification_vertical: float = 1.0
    magnification_horizontal: float = 1.0
    magnification_applied: bool = False
    validated_by: str | None = None
    validated_at: str | None = None
    #: Free text describing the manual calibration reference, for example
    #: "25.0 mm steel ball, right premolar region".
    reference_description: str = ""
    reference_length_mm: float | None = None
    reference_length_px: float | None = None
    device_model: str = ""
    notes: str = ""
    warnings: list = field(default_factory=list)

    # -- state ---------------------------------------------------------------

    @property
    def has_spacing(self) -> bool:
        return (
            self.row_spacing_mm is not None
            and self.col_spacing_mm is not None
            and self.row_spacing_mm > 0
            and self.col_spacing_mm > 0
        )

    @property
    def is_validated(self) -> bool:
        return self.status is ValidationStatus.VALIDATED and self.has_spacing

    @property
    def millimetres_available(self) -> bool:
        """Millimetre output is enabled only for a validated calibration."""
        return self.is_validated

    @property
    def is_anisotropic(self) -> bool:
        if not self.has_spacing:
            return False
        return abs(self.row_spacing_mm - self.col_spacing_mm) > 1e-9

    @property
    def effective_row_mm(self) -> float | None:
        """Vertical millimetres per pixel after any approved correction."""
        if not self.has_spacing:
            return None
        if self.magnification_applied and self.magnification_vertical > 0:
            return self.row_spacing_mm / self.magnification_vertical
        return self.row_spacing_mm

    @property
    def effective_col_mm(self) -> float | None:
        """Horizontal millimetres per pixel after any approved correction."""
        if not self.has_spacing:
            return None
        if self.magnification_applied and self.magnification_horizontal > 0:
            return self.col_spacing_mm / self.magnification_horizontal
        return self.col_spacing_mm

    @property
    def correction_factor_text(self) -> str:
        if not self.magnification_applied:
            return "none"
        if abs(self.magnification_vertical - self.magnification_horizontal) < 1e-9:
            return f"{self.magnification_vertical:.4g}"
        return (
            f"v {self.magnification_vertical:.4g}, "
            f"h {self.magnification_horizontal:.4g}"
        )

    # -- conversion ----------------------------------------------------------

    def distance_mm(self, dx_px: float, dy_px: float) -> float | None:
        """Convert a pixel displacement to millimetres.

        Row and column spacing are applied separately, so an image with unequal
        spacing converts correctly rather than through a single averaged scale
        (FR 031).
        """
        if not self.millimetres_available:
            return None
        rm = self.effective_row_mm
        cm = self.effective_col_mm
        if rm is None or cm is None:
            return None
        return math.hypot(dx_px * cm, dy_px * rm)

    def length_mm(self, p: tuple, q: tuple) -> float | None:
        return self.distance_mm(q[0] - p[0], q[1] - p[1])

    def area_mm2(self, area_px: float) -> float | None:
        if not self.millimetres_available:
            return None
        rm, cm = self.effective_row_mm, self.effective_col_mm
        if rm is None or cm is None:
            return None
        return area_px * rm * cm

    # -- validation ----------------------------------------------------------

    def plausibility_warnings(self) -> list:
        """Structural problems that should block validation."""
        issues: list = []
        lo, hi = PLAUSIBLE_SPACING_MM
        for name, value in (
            ("row spacing", self.row_spacing_mm),
            ("column spacing", self.col_spacing_mm),
        ):
            if value is None:
                continue
            if not (lo <= value <= hi):
                issues.append(
                    f"Reported {name} of {value:.6g} mm per pixel is outside the "
                    f"plausible range {lo} to {hi} mm per pixel for a panoramic "
                    f"detector."
                )
        if self.magnification_applied:
            mlo, mhi = PLAUSIBLE_MAGNIFICATION
            for name, value in (
                ("vertical", self.magnification_vertical),
                ("horizontal", self.magnification_horizontal),
            ):
                if not (mlo <= value <= mhi):
                    issues.append(
                        f"The {name} magnification factor {value:.4g} is outside "
                        f"the accepted range {mlo} to {mhi}."
                    )
        if self.is_anisotropic:
            issues.append(
                "Row and column spacing differ. Millimetre distances are "
                "computed per axis, which is correct, but confirm the header "
                "values are intended."
            )
        return issues

    def can_validate(self) -> tuple:
        """Return ``(ok, reasons)`` describing whether validation may proceed."""
        reasons: list = []
        if not self.has_spacing:
            reasons.append("No pixel spacing value is present.")
        lo, hi = PLAUSIBLE_SPACING_MM
        for name, value in (
            ("row spacing", self.row_spacing_mm),
            ("column spacing", self.col_spacing_mm),
        ):
            if value is not None and not (lo <= value <= hi):
                reasons.append(
                    f"The {name} value {value:.6g} mm per pixel is not plausible "
                    f"for a panoramic detector."
                )
        if self.magnification_applied:
            mlo, mhi = PLAUSIBLE_MAGNIFICATION
            if not (mlo <= self.magnification_vertical <= mhi):
                reasons.append("The vertical magnification factor is out of range.")
            if not (mlo <= self.magnification_horizontal <= mhi):
                reasons.append("The horizontal magnification factor is out of range.")
        return (not reasons, reasons)

    def validate(self, by_user: str, at: str) -> None:
        ok, reasons = self.can_validate()
        if not ok:
            raise ValueError("; ".join(reasons))
        self.status = ValidationStatus.VALIDATED
        self.validated_by = by_user
        self.validated_at = at

    def reject(self, by_user: str, at: str, reason: str) -> None:
        self.status = ValidationStatus.REJECTED
        self.validated_by = by_user
        self.validated_at = at
        self.notes = reason

    # -- presentation --------------------------------------------------------

    def summary_line(self) -> str:
        """One line shown beside every quantitative result (FR 009)."""
        if not self.has_spacing:
            return (
                f"Calibration: {self.source.display}. "
                f"Status {self.status.display}. Millimetre values unavailable."
            )
        scale = (
            f"{self.row_spacing_mm:.6g} mm/px vertical, "
            f"{self.col_spacing_mm:.6g} mm/px horizontal"
        )
        return (
            f"Calibration: {self.source.display}. Value {scale}. "
            f"Status {self.status.display}. "
            f"Correction factor {self.correction_factor_text}."
        )

    def unit_note(self) -> str:
        return (
            "Millimetre" if self.millimetres_available else "Pixel only, millimetre unavailable"
        )

    # -- serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["source"] = self.source.value
        d["status"] = self.status.value
        d["effective_row_mm"] = self.effective_row_mm
        d["effective_col_mm"] = self.effective_col_mm
        d["millimetres_available"] = self.millimetres_available
        d["correction_factor"] = self.correction_factor_text
        return d

    @classmethod
    def from_dict(cls, data: dict | None) -> "Calibration":
        if not data:
            return cls()
        data = dict(data)
        for derived in (
            "effective_row_mm",
            "effective_col_mm",
            "millimetres_available",
            "correction_factor",
        ):
            data.pop(derived, None)
        source = data.pop("source", CalibrationSource.NONE.value)
        status = data.pop("status", ValidationStatus.NOT_AVAILABLE.value)
        allowed = set(cls.__dataclass_fields__)
        obj = cls(**{k: v for k, v in data.items() if k in allowed})
        obj.source = CalibrationSource(source)
        obj.status = ValidationStatus(status)
        return obj

    @classmethod
    def from_known_length(
        cls,
        reference_length_mm: float,
        reference_length_px: float,
        description: str,
        by_user: str,
        at: str,
    ) -> "Calibration":
        """Build a calibration from a manually drawn known length (FR 007).

        A manual calibration is isotropic by construction: one drawn reference
        gives one scale, applied to both axes. If a device needs different
        vertical and horizontal scales, that belongs in the device policy an
        administrator configures, not in a single drawn line.
        """
        if reference_length_mm <= 0:
            raise ValueError("The reference length in millimetres must be positive.")
        if reference_length_px <= 0:
            raise ValueError("The reference length in pixels must be positive.")
        scale = reference_length_mm / reference_length_px
        cal = cls(
            source=CalibrationSource.MANUAL_KNOWN_LENGTH,
            row_spacing_mm=scale,
            col_spacing_mm=scale,
            status=ValidationStatus.UNVALIDATED,
            reference_description=description,
            reference_length_mm=reference_length_mm,
            reference_length_px=reference_length_px,
        )
        ok, reasons = cal.can_validate()
        if ok:
            cal.validate(by_user, at)
        else:
            cal.warnings = reasons
        return cal


def format_measurement(
    value_px: float | None,
    value_mm: float | None,
    calibration: Calibration,
    decimals_px: int = 2,
    decimals_mm: int = 2,
) -> str:
    """Render a measurement with its unit, never implying a unit it does not have."""
    if value_px is None:
        return "not measured"
    px = f"{value_px:.{decimals_px}f} px"
    if value_mm is None:
        if calibration.millimetres_available:
            return px
        return f"{px} (mm unavailable)"
    return f"{value_mm:.{decimals_mm}f} mm ({px})"


def format_ratio(value: float | None, decimals: int = 4) -> str:
    """Ratios such as the panoramic mandibular index are dimensionless and are
    reported even without calibration (FR 008)."""
    if value is None:
        return "not measured"
    return f"{value:.{decimals}f}"
