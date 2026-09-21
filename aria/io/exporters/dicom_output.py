"""DICOM derived output: Structured Report and Segmentation (FR 050, AC 010).

This output is disabled by default and stays disabled until an administrator
records that the objects have passed a validator and opened correctly in each
named target viewer. That gate is the requirement, not a formality: an object
that a receiving system silently mis-reads is worse than no object at all,
because the numbers look official.

What is produced
----------------
Structured Report
    A Comprehensive SR carrying the derived measurements, one measurement group
    per side, with coded concept names and UCUM units.

Segmentation
    A binary Segmentation object carrying one segment per annotated region.

Coding
------
Standard codes are used where they exist. The mandibular radiomorphometric
indices do not have standard codes, so they are issued under a private coding
scheme designator, which is what the standard provides for exactly this case.
The scheme, its version and the full measure name travel with every item, so a
receiving system that does not know the scheme still gets a readable name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ...version import APP_NAME, APP_VENDOR, APP_VERSION, version_block
from ...core.measurements import MeasurementKind
from ...core.schema import GeometryType, MCIGrade, Side, get_class

#: Private coding scheme designator for measures without a standard code.
PRIVATE_SCHEME = "99ARIA"
PRIVATE_SCHEME_VERSION = "1.0.0"

#: SOP Class identifiers.
COMPREHENSIVE_SR_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.88.33"
SEGMENTATION_SOP_CLASS = "1.2.840.10008.5.1.4.1.1.66.4"
EXPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2.1"

#: UID root used for generated objects. An administrator replaces this with the
#: institution's registered root in Preferences, Privacy.
UID_ROOT = "2.25"

#: Concept codes for each measure.
MEASURE_CODES = {
    MeasurementKind.MCW.value: (
        "ARIA001", "Mandibular cortical width", "mm"
    ),
    MeasurementKind.PMI_SUPERIOR_HEIGHT.value: (
        "ARIA002", "Superior mental foramen to inferior border height", "mm"
    ),
    MeasurementKind.PMI_INFERIOR_HEIGHT.value: (
        "ARIA003", "Inferior mental foramen to inferior border height", "mm"
    ),
    MeasurementKind.PMI_SUPERIOR.value: (
        "ARIA004", "Panoramic mandibular index superior", "1"
    ),
    MeasurementKind.PMI_INFERIOR.value: (
        "ARIA005", "Panoramic mandibular index inferior", "1"
    ),
    MeasurementKind.ANTEGONIAL_INDEX.value: (
        "ARIA006", "Antegonial index", "mm"
    ),
    MeasurementKind.GONIAL_INDEX.value: (
        "ARIA007", "Gonial index", "mm"
    ),
}

#: Laterality codes from the standard vocabulary.
SIDE_CODES = {
    Side.RIGHT.value: ("24028007", "SCT", "Right"),
    Side.LEFT.value: ("7771000", "SCT", "Left"),
}


@dataclass
class InteroperabilityRecord:
    """Evidence that DICOM output may be enabled (AC 010).

    Until a validator result and every named target viewer are recorded as
    passing, :meth:`is_satisfied` is false and the exporter refuses to write.
    """

    validator_name: str = ""
    validator_version: str = ""
    validator_passed: bool = False
    validated_at: str = ""
    validated_by: str = ""
    #: Each entry is ``{"viewer": name, "version": v, "passed": bool, "notes": s}``.
    target_viewers: list = field(default_factory=list)
    sop_classes_documented: list = field(default_factory=list)
    transfer_syntaxes_documented: list = field(default_factory=list)
    notes: str = ""

    def is_satisfied(self) -> bool:
        if not (self.validator_passed and self.validator_name):
            return False
        if not self.target_viewers:
            return False
        return all(v.get("passed") for v in self.target_viewers)

    def blocking_reasons(self) -> list:
        reasons: list = []
        if not self.validator_name:
            reasons.append("No validator has been named.")
        elif not self.validator_passed:
            reasons.append(f"The objects have not passed {self.validator_name}.")
        if not self.target_viewers:
            reasons.append(
                "No target viewer has been named. Interoperability cannot be "
                "claimed without testing against the viewers that will receive "
                "these objects."
            )
        for v in self.target_viewers:
            if not v.get("passed"):
                reasons.append(
                    f"{v.get('viewer', 'A target viewer')} has not confirmed the "
                    f"objects open correctly."
                )
        if not self.sop_classes_documented:
            reasons.append("The supported SOP classes have not been documented.")
        if not self.transfer_syntaxes_documented:
            reasons.append("The supported transfer syntaxes have not been documented.")
        return reasons

    def to_dict(self) -> dict:
        return {
            "validator_name": self.validator_name,
            "validator_version": self.validator_version,
            "validator_passed": self.validator_passed,
            "validated_at": self.validated_at,
            "validated_by": self.validated_by,
            "target_viewers": list(self.target_viewers),
            "sop_classes_documented": list(self.sop_classes_documented),
            "transfer_syntaxes_documented": list(self.transfer_syntaxes_documented),
            "satisfied": self.is_satisfied(),
            "blocking_reasons": self.blocking_reasons(),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "InteroperabilityRecord":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


class DicomOutputDisabled(RuntimeError):
    """Raised when DICOM output is requested before it may be claimed."""


def _generate_uid(*parts) -> str:
    """Deterministic identifier under the configured root."""
    import hashlib

    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    number = str(int.from_bytes(digest, "big"))[:30].lstrip("0") or "1"
    return f"{UID_ROOT}.{number}"[:64]


def _code(value: str, scheme: str, meaning: str):
    from pydicom.dataset import Dataset

    ds = Dataset()
    ds.CodeValue = value
    ds.CodingSchemeDesignator = scheme
    ds.CodeMeaning = meaning
    if scheme == PRIVATE_SCHEME:
        ds.CodingSchemeVersion = PRIVATE_SCHEME_VERSION
    return ds


def _container(concept, relationship: str = "CONTAINS"):
    from pydicom.dataset import Dataset

    item = Dataset()
    item.RelationshipType = relationship
    item.ValueType = "CONTAINER"
    item.ConceptNameCodeSequence = [concept]
    item.ContinuityOfContent = "SEPARATE"
    item.ContentSequence = []
    return item


def _text_item(concept, text: str, relationship: str = "CONTAINS"):
    from pydicom.dataset import Dataset

    item = Dataset()
    item.RelationshipType = relationship
    item.ValueType = "TEXT"
    item.ConceptNameCodeSequence = [concept]
    item.TextValue = str(text)[:1024]
    return item


def _num_item(concept, value: float, units_code, relationship: str = "CONTAINS"):
    from pydicom.dataset import Dataset

    measured = Dataset()
    measured.NumericValue = f"{float(value):.6f}"
    measured.MeasurementUnitsCodeSequence = [units_code]

    item = Dataset()
    item.RelationshipType = relationship
    item.ValueType = "NUM"
    item.ConceptNameCodeSequence = [concept]
    item.MeasuredValueSequence = [measured]
    return item


def _code_item(concept, code, relationship: str = "CONTAINS"):
    from pydicom.dataset import Dataset

    item = Dataset()
    item.RelationshipType = relationship
    item.ValueType = "CODE"
    item.ConceptNameCodeSequence = [concept]
    item.ConceptCodeSequence = [code]
    return item


def _units(unit: str):
    return _code(unit, "UCUM", {"mm": "millimeter", "1": "ratio", "px": "pixel"}.get(unit, unit))


def _base_dataset(case, sop_class: str, modality: str, series_description: str):
    """Common patient, study and equipment modules.

    Patient identity is written as the case pseudonym. No direct identifier is
    placed into a derived object.
    """
    from pydicom.dataset import Dataset, FileMetaDataset

    now = datetime.now()
    ds = Dataset()

    ds.PatientName = case.pseudonym
    ds.PatientID = case.pseudonym
    ds.PatientBirthDate = ""
    ds.PatientSex = ""
    ds.PatientIdentityRemoved = "YES"
    ds.DeidentificationMethod = f"{APP_NAME} deidentification profile"

    ds.StudyInstanceUID = case.source.study_instance_uid or _generate_uid("study", case.id)
    ds.SeriesInstanceUID = _generate_uid("series", case.id, modality)
    ds.SOPInstanceUID = _generate_uid("instance", case.id, modality, APP_VERSION)
    ds.SOPClassUID = sop_class
    ds.StudyID = ""
    ds.SeriesNumber = "1"
    ds.InstanceNumber = "1"
    ds.AccessionNumber = ""
    ds.Modality = modality
    ds.SeriesDescription = series_description

    ds.StudyDate = now.strftime("%Y%m%d")
    ds.StudyTime = now.strftime("%H%M%S")
    ds.ContentDate = now.strftime("%Y%m%d")
    ds.ContentTime = now.strftime("%H%M%S")
    ds.SpecificCharacterSet = "ISO_IR 192"

    ds.Manufacturer = APP_VENDOR
    ds.ManufacturerModelName = APP_NAME
    ds.SoftwareVersions = APP_VERSION

    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = sop_class
    meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    meta.TransferSyntaxUID = EXPLICIT_VR_LITTLE_ENDIAN
    meta.ImplementationClassUID = _generate_uid("implementation", APP_NAME, APP_VERSION)
    meta.ImplementationVersionName = f"{APP_NAME}_{APP_VERSION}"[:16]
    ds.file_meta = meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    return ds


def build_measurement_sr(case, data, measurements, annotator=None):
    """Build a Comprehensive Structured Report carrying the measurements."""
    from pydicom.dataset import Dataset

    ds = _base_dataset(
        case, COMPREHENSIVE_SR_SOP_CLASS, "SR",
        "Mandibular radiomorphometric measurements",
    )
    ds.CompletionFlag = "COMPLETE"
    ds.VerificationFlag = "UNVERIFIED"
    ds.ValueType = "CONTAINER"
    ds.ConceptNameCodeSequence = [
        _code("126000", "DCM", "Imaging Measurement Report")
    ]
    ds.ContinuityOfContent = "SEPARATE"

    if case.source.sop_instance_uid:
        reference = Dataset()
        reference.ReferencedSOPClassUID = (
            case.source.sop_class_uid or "1.2.840.10008.5.1.4.1.1.1"
        )
        reference.ReferencedSOPInstanceUID = case.source.sop_instance_uid
        series = Dataset()
        series.SeriesInstanceUID = case.source.series_instance_uid or ds.SeriesInstanceUID
        series.ReferencedSOPSequence = [reference]
        study = Dataset()
        study.StudyInstanceUID = case.source.study_instance_uid or ds.StudyInstanceUID
        study.ReferencedSeriesSequence = [series]
        ds.CurrentRequestedProcedureEvidenceSequence = [study]

    content: list = []

    # Language and procedure context.
    content.append(
        _code_item(
            _code("121049", "DCM", "Language of Content Item and Descendants"),
            _code("en", "RFC5646", "English"),
            relationship="HAS CONCEPT MOD",
        )
    )
    content.append(
        _text_item(
            _code("121058", "DCM", "Procedure reported"),
            "Dental panoramic radiograph, mandibular radiomorphometric analysis",
        )
    )

    # Calibration and provenance.
    provenance = _container(_code("ARIA100", PRIVATE_SCHEME, "Analysis provenance"))
    provenance.ContentSequence = [
        _text_item(_code("ARIA101", PRIVATE_SCHEME, "Case pseudonym"), case.pseudonym),
        _text_item(
            _code("ARIA102", PRIVATE_SCHEME, "Source checksum"), case.source.sha256
        ),
        _text_item(
            _code("ARIA103", PRIVATE_SCHEME, "Calibration"), case.calibration.summary_line()
        ),
        _text_item(
            _code("ARIA104", PRIVATE_SCHEME, "Calculation version"),
            version_block()["calculation_version"],
        ),
        _text_item(
            _code("ARIA105", PRIVATE_SCHEME, "Annotator pseudonym"),
            annotator.pseudonym if annotator else "",
        ),
        _text_item(
            _code("ARIA106", PRIVATE_SCHEME, "Intended use"),
            (
                "Research annotation record. This object does not state a "
                "diagnosis and does not estimate bone mineral density."
            ),
        ),
    ]
    content.append(provenance)

    # Measurements, one group per side.
    measurements_container = _container(
        _code("126010", "DCM", "Imaging Measurements")
    )
    groups: list = []

    for side in (Side.RIGHT, Side.LEFT, Side.NONE):
        side_values = [m for m in measurements if m.side == side.value and m.assessable]
        if not side_values:
            continue
        label = (
            side.display if side is not Side.NONE else "Bilateral aggregate"
        )
        group = _container(_code("125007", "DCM", "Measurement Group"))
        items: list = [
            _text_item(_code("125007", "DCM", "Measurement Group"), label)
        ]
        if side.value in SIDE_CODES:
            value, scheme, meaning = SIDE_CODES[side.value]
            items.append(
                _code_item(
                    _code("272741003", "SCT", "Laterality"),
                    _code(value, scheme, meaning),
                    relationship="HAS CONCEPT MOD",
                )
            )

        for m in side_values:
            code_info = MEASURE_CODES.get(m.kind)
            if code_info is None:
                continue
            code_value, meaning, unit = code_info
            if m.value_ratio is not None:
                numeric, unit_used = m.value_ratio, "1"
            elif m.value_mm is not None:
                numeric, unit_used = m.value_mm, "mm"
            elif m.value_px is not None:
                numeric, unit_used = m.value_px, "px"
            else:
                continue
            item = _num_item(
                _code(code_value, PRIVATE_SCHEME, meaning), numeric, _units(unit_used)
            )
            aliases = MeasurementKind(m.kind).aliases
            if aliases:
                item.ContentSequence = [
                    _text_item(
                        _code("ARIA110", PRIVATE_SCHEME, "Accepted alternative names"),
                        ", ".join(aliases),
                        relationship="HAS PROPERTIES",
                    )
                ]
            items.append(item)

        # Klemetti grade for the side.
        if side in (Side.RIGHT, Side.LEFT):
            grade_label = data.grade("mci_grade", side)
            if grade_label is not None:
                grade = MCIGrade(grade_label.value)
                items.append(
                    _code_item(
                        _code("ARIA008", PRIVATE_SCHEME, "Mandibular cortical index"),
                        _code(
                            f"ARIA008{grade.value}", PRIVATE_SCHEME,
                            f"{grade.display}: {grade.definition}"[:64],
                        ),
                    )
                )

        group.ContentSequence = items
        groups.append(group)

    measurements_container.ContentSequence = groups
    content.append(measurements_container)

    ds.ContentSequence = content
    return ds


def build_segmentation(case, data, image_shape, class_index=None):
    """Build a binary Segmentation object from the annotated regions."""
    import numpy as np
    from pydicom.dataset import Dataset

    from .mask_export import build_class_index, rasterise_case

    class_index = class_index or build_class_index()
    label, written, _overlaps = rasterise_case(data, image_shape, class_index)
    present = sorted({int(v) for v in np.unique(label) if v})
    if not present:
        return None

    ds = _base_dataset(
        case, SEGMENTATION_SOP_CLASS, "SEG", "Mandibular annotation segmentation"
    )
    rows, cols = image_shape
    ds.Rows = int(rows)
    ds.Columns = int(cols)
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 1
    ds.BitsStored = 1
    ds.HighBit = 0
    ds.PixelRepresentation = 0
    ds.LossyImageCompression = "00"
    ds.SegmentationType = "BINARY"
    ds.ContentLabel = "ARIA_SEG"
    ds.ContentDescription = "Mandibular radiomorphometric annotation regions"
    ds.ContentCreatorName = APP_VENDOR
    ds.ImageType = ["DERIVED", "PRIMARY"]

    index_to_class = {v: k for k, v in class_index.items()}

    segments: list = []
    frames: list = []
    frame_items: list = []

    for number, value in enumerate(present, start=1):
        class_key, side = index_to_class.get(value, ("unknown", Side.NONE.value))
        try:
            cls = get_class(class_key)
            label_text = f"{cls.display_name} ({Side(side).display})"
            colour = cls.colour
        except KeyError:
            label_text, colour = class_key, "#FFFFFF"

        segment = Dataset()
        segment.SegmentNumber = number
        segment.SegmentLabel = label_text[:64]
        segment.SegmentDescription = label_text[:64]
        segment.SegmentAlgorithmType = "MANUAL"
        segment.SegmentedPropertyCategoryCodeSequence = [
            _code("91723000", "SCT", "Anatomical Structure")
        ]
        segment.SegmentedPropertyTypeCodeSequence = [
            _code(f"ARIA2{number:03d}", PRIVATE_SCHEME, label_text[:64])
        ]
        if side in SIDE_CODES:
            code_value, scheme, meaning = SIDE_CODES[side]
            segment.SegmentSurfaceGenerationAlgorithmIdentificationSequence = []
            laterality = Dataset()
            laterality.CodeValue = code_value
            laterality.CodingSchemeDesignator = scheme
            laterality.CodeMeaning = meaning
            segment.SegmentedPropertyTypeModifierCodeSequence = [laterality]
        segments.append(segment)

        frames.append((label == value).astype(np.uint8))

        frame_item = Dataset()
        identification = Dataset()
        identification.ReferencedSegmentNumber = number
        frame_item.SegmentIdentificationSequence = [identification]
        plane = Dataset()
        plane.ImagePositionPatient = [0.0, 0.0, 0.0]
        frame_item.PlanePositionSequence = [plane]
        frame_items.append(frame_item)

    ds.SegmentSequence = segments
    ds.NumberOfFrames = len(frames)
    ds.PerFrameFunctionalGroupsSequence = frame_items

    shared = Dataset()
    orientation = Dataset()
    orientation.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    cal = case.calibration
    if cal.has_spacing:
        measures = Dataset()
        measures.PixelSpacing = [
            f"{cal.effective_row_mm:.6f}", f"{cal.effective_col_mm:.6f}"
        ]
        measures.SliceThickness = "1.0"
        shared.PixelMeasuresSequence = [measures]
    shared.PlaneOrientationSequence = [orientation]
    ds.SharedFunctionalGroupsSequence = [shared]

    packed = np.packbits(np.concatenate([f.ravel() for f in frames]), bitorder="little")
    ds.PixelData = packed.tobytes()

    if case.source.sop_instance_uid:
        reference = Dataset()
        reference.ReferencedSOPClassUID = (
            case.source.sop_class_uid or "1.2.840.10008.5.1.4.1.1.1"
        )
        reference.ReferencedSOPInstanceUID = case.source.sop_instance_uid
        series = Dataset()
        series.SeriesInstanceUID = case.source.series_instance_uid or ds.SeriesInstanceUID
        series.ReferencedInstanceSequence = [reference]
        ds.ReferencedSeriesSequence = [series]

    return ds


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

#: Attributes each object type must carry before it is written.
SR_REQUIRED = [
    "SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID",
    "Modality", "PatientID", "CompletionFlag", "VerificationFlag",
    "ValueType", "ConceptNameCodeSequence", "ContentSequence",
]
SEG_REQUIRED = [
    "SOPClassUID", "SOPInstanceUID", "StudyInstanceUID", "SeriesInstanceUID",
    "Modality", "PatientID", "Rows", "Columns", "BitsAllocated",
    "SegmentationType", "SegmentSequence", "NumberOfFrames",
    "PerFrameFunctionalGroupsSequence", "SharedFunctionalGroupsSequence",
    "PixelData",
]


def validate_object(ds, kind: str) -> dict:
    """Structural check before writing, independent of any external validator.

    This does not replace the external validator that AC 010 requires. It
    catches the obvious problems early so the external run is about genuine
    conformance rather than missing attributes.
    """
    required = SR_REQUIRED if kind == "sr" else SEG_REQUIRED
    missing = [tag for tag in required if not hasattr(ds, tag) or getattr(ds, tag) in (None, "")]
    issues: list = []
    if missing:
        issues.append(f"Missing required attributes: {', '.join(missing)}.")

    if kind == "seg":
        try:
            expected_bits = int(ds.Rows) * int(ds.Columns) * int(ds.NumberOfFrames)
            expected_bytes = (expected_bits + 7) // 8
            actual = len(ds.PixelData)
            if actual < expected_bytes:
                issues.append(
                    f"Pixel data holds {actual} bytes but {expected_bytes} are "
                    f"needed for {ds.NumberOfFrames} frames."
                )
            if len(ds.SegmentSequence) != int(ds.NumberOfFrames):
                issues.append(
                    "The number of segments does not match the number of frames."
                )
        except (AttributeError, TypeError, ValueError) as exc:
            issues.append(f"Pixel data could not be checked: {exc}")

    if kind == "sr":
        try:
            if not ds.ContentSequence:
                issues.append("The content sequence is empty.")
        except AttributeError:
            issues.append("The content sequence is missing.")

    return {"ok": not issues, "issues": issues, "missing": missing}


def conformance_statement(record: InteroperabilityRecord) -> dict:
    """The conformance statement that must accompany any claim (FR 050)."""
    return {
        "application": APP_NAME,
        "version": APP_VERSION,
        "vendor": APP_VENDOR,
        "role": "Creator of derived objects. ARIA does not act as a network node.",
        "sop_classes_created": [
            {
                "name": "Comprehensive SR Storage",
                "uid": COMPREHENSIVE_SR_SOP_CLASS,
                "purpose": "Derived radiomorphometric measurements",
            },
            {
                "name": "Segmentation Storage",
                "uid": SEGMENTATION_SOP_CLASS,
                "purpose": "Binary segmentation of annotated regions",
            },
        ],
        "transfer_syntaxes": [
            {"name": "Explicit VR Little Endian", "uid": EXPLICIT_VR_LITTLE_ENDIAN}
        ],
        "sop_classes_read": [
            {"name": "Computed Radiography Image Storage", "uid": "1.2.840.10008.5.1.4.1.1.1"},
            {"name": "Digital X-Ray Image Storage, For Presentation", "uid": "1.2.840.10008.5.1.4.1.1.1.1"},
            {"name": "Digital Intra-Oral X-Ray Image Storage, For Presentation", "uid": "1.2.840.10008.5.1.4.1.1.1.3"},
            {"name": "Secondary Capture Image Storage", "uid": "1.2.840.10008.5.1.4.1.1.7"},
        ],
        "private_coding_scheme": {
            "designator": PRIVATE_SCHEME,
            "version": PRIVATE_SCHEME_VERSION,
            "reason": (
                "The mandibular radiomorphometric indices have no standard code. "
                "Every private code carries its full meaning as text."
            ),
            "codes": [
                {"code": code, "meaning": meaning, "unit": unit}
                for code, meaning, unit in MEASURE_CODES.values()
            ],
        },
        "network_services": "None. ARIA does not send or receive over a network.",
        "interoperability_evidence": record.to_dict(),
        "status": (
            "Interoperability accepted" if record.is_satisfied()
            else "Not accepted. DICOM derived output is disabled."
        ),
        "versions": version_block(),
    }


class DicomExporter:
    """Writes derived DICOM objects, subject to the interoperability gate."""

    def __init__(self, enabled: bool, record: InteroperabilityRecord | None = None):
        self.enabled = enabled
        self.record = record or InteroperabilityRecord()

    def can_export(self) -> tuple:
        if not self.enabled:
            return False, ["DICOM derived output is switched off in the project settings."]
        reasons = self.record.blocking_reasons()
        return (not reasons), reasons

    def export_case(
        self, directory, case, data, measurements, image_shape, annotator=None,
        write_segmentation: bool = True,
    ) -> dict:
        ok, reasons = self.can_export()
        if not ok:
            raise DicomOutputDisabled(
                "DICOM derived output cannot be written yet. "
                + " ".join(reasons)
                + " Record the validator result and the target viewer results in "
                "Administration, DICOM output before enabling it."
            )

        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        written: dict = {"validation": {}}

        sr = build_measurement_sr(case, data, measurements, annotator)
        check = validate_object(sr, "sr")
        written["validation"]["sr"] = check
        if not check["ok"]:
            raise ValueError(
                "The structured report failed its structural check: "
                + " ".join(check["issues"])
            )
        sr_path = directory / f"{case.pseudonym}_measurements_sr.dcm"
        sr.save_as(str(sr_path), enforce_file_format=True)
        written["sr"] = sr_path

        if write_segmentation:
            seg = build_segmentation(case, data, image_shape)
            if seg is not None:
                check = validate_object(seg, "seg")
                written["validation"]["seg"] = check
                if not check["ok"]:
                    raise ValueError(
                        "The segmentation object failed its structural check: "
                        + " ".join(check["issues"])
                    )
                seg_path = directory / f"{case.pseudonym}_segmentation.dcm"
                seg.save_as(str(seg_path), enforce_file_format=True)
                written["segmentation"] = seg_path

        statement_path = directory / "dicom_conformance_statement.json"
        from ...io.fsutil import atomic_write_text

        atomic_write_text(
            statement_path,
            json.dumps(conformance_statement(self.record), indent=2, default=str),
        )
        written["conformance_statement"] = statement_path
        return written
