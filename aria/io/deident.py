"""Deidentification of DICOM identifiers (FR 005, AC 009).

Deidentification runs at import, before a case is available to annotate. The
retained original file is left untouched on disk; what is deidentified is the
metadata ARIA stores and everything that can reach an export.

The tag actions follow the DICOM confidentiality profile vocabulary:

======  =============================================================
Action  Meaning
======  =============================================================
X       remove the attribute
Z       replace with a zero length value
D       replace with a non identifying dummy value of the correct form
U       replace with a consistently remapped identifier
K       keep the value
C       clean, keep the attribute but strip identifying content
======  =============================================================

Profiles are administrator configurable. Three are shipped, and a project
selects one. A profile is stored with the project, so an export can always say
which rules produced it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

#: UID root used when remapping identifiers. An administrator replaces this
#: with the institution's own registered root in Preferences, Privacy.
DEFAULT_UID_ROOT = "2.25"


class Action:
    REMOVE = "X"
    ZERO = "Z"
    DUMMY = "D"
    REMAP_UID = "U"
    KEEP = "K"
    CLEAN = "C"


#: Attributes that carry a direct identifier. These are never kept, in any
#: profile, and the export scanner checks the resulting records for them.
DIRECT_IDENTIFIER_TAGS = {
    (0x0010, 0x0010): ("PatientName", Action.ZERO),
    (0x0010, 0x0020): ("PatientID", Action.REMAP_UID),
    (0x0010, 0x0021): ("IssuerOfPatientID", Action.REMOVE),
    (0x0010, 0x0030): ("PatientBirthDate", Action.REMOVE),
    (0x0010, 0x0032): ("PatientBirthTime", Action.REMOVE),
    (0x0010, 0x1000): ("OtherPatientIDs", Action.REMOVE),
    (0x0010, 0x1001): ("OtherPatientNames", Action.REMOVE),
    (0x0010, 0x1002): ("OtherPatientIDsSequence", Action.REMOVE),
    (0x0010, 0x1005): ("PatientBirthName", Action.REMOVE),
    (0x0010, 0x1040): ("PatientAddress", Action.REMOVE),
    (0x0010, 0x1060): ("PatientMotherBirthName", Action.REMOVE),
    (0x0010, 0x2154): ("PatientTelephoneNumbers", Action.REMOVE),
    (0x0010, 0x2155): ("PatientTelecomInformation", Action.REMOVE),
    (0x0010, 0x1090): ("MedicalRecordLocator", Action.REMOVE),
    (0x0010, 0x2160): ("EthnicGroup", Action.REMOVE),
    (0x0010, 0x21B0): ("AdditionalPatientHistory", Action.REMOVE),
    (0x0010, 0x4000): ("PatientComments", Action.REMOVE),
    (0x0038, 0x0300): ("CurrentPatientLocation", Action.REMOVE),
    (0x0038, 0x0400): ("PatientInstitutionResidence", Action.REMOVE),
    (0x0038, 0x0500): ("PatientState", Action.REMOVE),
    (0x0040, 0x1400): ("RequestedProcedureComments", Action.REMOVE),
    (0x0008, 0x0090): ("ReferringPhysicianName", Action.ZERO),
    (0x0008, 0x0092): ("ReferringPhysicianAddress", Action.REMOVE),
    (0x0008, 0x0094): ("ReferringPhysicianTelephoneNumbers", Action.REMOVE),
    (0x0008, 0x0096): ("ReferringPhysicianIdentificationSequence", Action.REMOVE),
    (0x0008, 0x1048): ("PhysiciansOfRecord", Action.REMOVE),
    (0x0008, 0x1049): ("PhysiciansOfRecordIdentificationSequence", Action.REMOVE),
    (0x0008, 0x1050): ("PerformingPhysicianName", Action.REMOVE),
    (0x0008, 0x1060): ("NameOfPhysiciansReadingStudy", Action.REMOVE),
    (0x0008, 0x1070): ("OperatorsName", Action.REMOVE),
    (0x0032, 0x1032): ("RequestingPhysician", Action.REMOVE),
    (0x0040, 0x0006): ("ScheduledPerformingPhysicianName", Action.REMOVE),
    (0x0040, 0xA123): ("PersonName", Action.REMOVE),
}

#: Institution and location attributes.
INSTITUTION_TAGS = {
    (0x0008, 0x0080): ("InstitutionName", Action.REMOVE),
    (0x0008, 0x0081): ("InstitutionAddress", Action.REMOVE),
    (0x0008, 0x0082): ("InstitutionCodeSequence", Action.REMOVE),
    (0x0008, 0x1040): ("InstitutionalDepartmentName", Action.REMOVE),
    (0x0040, 0x0001): ("ScheduledStationAETitle", Action.REMOVE),
    (0x0040, 0x0010): ("ScheduledStationName", Action.REMOVE),
    (0x0040, 0x0011): ("ScheduledProcedureStepLocation", Action.REMOVE),
    (0x0008, 0x1010): ("StationName", Action.REMOVE),
}

#: Device identity. Retaining this is what makes a per device calibration
#: policy possible, so it is kept by default and can be removed by profile.
DEVICE_TAGS = {
    (0x0008, 0x0070): ("Manufacturer", Action.KEEP),
    (0x0008, 0x1090): ("ManufacturerModelName", Action.KEEP),
    (0x0018, 0x1000): ("DeviceSerialNumber", Action.REMOVE),
    (0x0018, 0x1020): ("SoftwareVersions", Action.KEEP),
    (0x0018, 0x1200): ("DateOfLastCalibration", Action.REMOVE),
    (0x0018, 0x700A): ("DetectorID", Action.REMOVE),
}

#: Patient characteristics that many studies need. Kept by profile choice.
CHARACTERISTIC_TAGS = {
    (0x0010, 0x0040): ("PatientSex", Action.KEEP),
    (0x0010, 0x1010): ("PatientAge", Action.KEEP),
    (0x0010, 0x1020): ("PatientSize", Action.KEEP),
    (0x0010, 0x1030): ("PatientWeight", Action.KEEP),
    (0x0010, 0x2000): ("MedicalAlerts", Action.REMOVE),
    (0x0010, 0x2110): ("Allergies", Action.REMOVE),
    (0x0010, 0x21C0): ("PregnancyStatus", Action.KEEP),
}

#: Date and time attributes.
DATE_TAGS = {
    (0x0008, 0x0020): ("StudyDate", Action.CLEAN),
    (0x0008, 0x0021): ("SeriesDate", Action.CLEAN),
    (0x0008, 0x0022): ("AcquisitionDate", Action.CLEAN),
    (0x0008, 0x0023): ("ContentDate", Action.CLEAN),
    (0x0008, 0x0030): ("StudyTime", Action.REMOVE),
    (0x0008, 0x0031): ("SeriesTime", Action.REMOVE),
    (0x0008, 0x0032): ("AcquisitionTime", Action.REMOVE),
    (0x0008, 0x0033): ("ContentTime", Action.REMOVE),
    (0x0038, 0x0020): ("AdmittingDate", Action.REMOVE),
    (0x0040, 0x0244): ("PerformedProcedureStepStartDate", Action.REMOVE),
    (0x0040, 0x0245): ("PerformedProcedureStepStartTime", Action.REMOVE),
}

#: Identifier attributes that are remapped consistently rather than removed, so
#: two images of the same study stay linked after deidentification.
UID_TAGS = {
    (0x0020, 0x000D): ("StudyInstanceUID", Action.REMAP_UID),
    (0x0020, 0x000E): ("SeriesInstanceUID", Action.REMAP_UID),
    (0x0008, 0x0018): ("SOPInstanceUID", Action.REMAP_UID),
    (0x0020, 0x0052): ("FrameOfReferenceUID", Action.REMAP_UID),
    (0x0008, 0x0050): ("AccessionNumber", Action.ZERO),
    (0x0020, 0x0010): ("StudyID", Action.ZERO),
    (0x0040, 0x1001): ("RequestedProcedureID", Action.REMOVE),
    (0x0040, 0x0253): ("PerformedProcedureStepID", Action.REMOVE),
    (0x0008, 0x1030): ("StudyDescription", Action.CLEAN),
    (0x0008, 0x103E): ("SeriesDescription", Action.CLEAN),
    (0x0008, 0x0008): ("ImageType", Action.KEEP),
    (0x0018, 0x1030): ("ProtocolName", Action.CLEAN),
}


@dataclass
class DeidProfile:
    """An administrator approved deidentification profile."""

    name: str = "aria_default"
    display_name: str = "ARIA default"
    description: str = ""
    retain_device_identity: bool = True
    retain_patient_characteristics: bool = True
    retain_study_year: bool = True
    retain_full_dates: bool = False
    retain_private_tags: bool = False
    remap_uids: bool = True
    uid_root: str = DEFAULT_UID_ROOT
    #: Extra tags an institution has declared prohibited.
    additional_removals: list = field(default_factory=list)
    approved_by: str = ""
    approved_at: str = ""
    version: str = "1.0.0"

    def actions(self) -> dict:
        """Resolve the profile into a tag to action mapping."""
        table: dict = {}
        table.update(DIRECT_IDENTIFIER_TAGS)
        table.update(INSTITUTION_TAGS)

        for tag, (name, action) in DEVICE_TAGS.items():
            table[tag] = (name, action if self.retain_device_identity else Action.REMOVE)
        for tag, (name, action) in CHARACTERISTIC_TAGS.items():
            table[tag] = (
                name,
                action if self.retain_patient_characteristics else Action.REMOVE,
            )
        for tag, (name, action) in DATE_TAGS.items():
            if self.retain_full_dates:
                table[tag] = (name, Action.KEEP)
            elif self.retain_study_year and action == Action.CLEAN:
                table[tag] = (name, Action.CLEAN)
            else:
                table[tag] = (name, Action.REMOVE)
        for tag, (name, action) in UID_TAGS.items():
            if action == Action.REMAP_UID and not self.remap_uids:
                table[tag] = (name, Action.REMOVE)
            else:
                table[tag] = (name, action)

        for entry in self.additional_removals:
            try:
                group, element = entry if isinstance(entry, (list, tuple)) else (None, None)
                if group is not None:
                    table[(int(group), int(element))] = ("AdditionalRemoval", Action.REMOVE)
            except (TypeError, ValueError):
                continue
        return table

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "retain_device_identity": self.retain_device_identity,
            "retain_patient_characteristics": self.retain_patient_characteristics,
            "retain_study_year": self.retain_study_year,
            "retain_full_dates": self.retain_full_dates,
            "retain_private_tags": self.retain_private_tags,
            "remap_uids": self.remap_uids,
            "uid_root": self.uid_root,
            "additional_removals": list(self.additional_removals),
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeidProfile":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (data or {}).items() if k in allowed})


BUILTIN_PROFILES = {
    "aria_strict": DeidProfile(
        name="aria_strict",
        display_name="Strict",
        description=(
            "Removes every identifier, every date and all device identity. Use "
            "when the dataset leaves the institution."
        ),
        retain_device_identity=False,
        retain_patient_characteristics=False,
        retain_study_year=False,
        retain_full_dates=False,
        remap_uids=True,
    ),
    "aria_default": DeidProfile(
        name="aria_default",
        display_name="ARIA default",
        description=(
            "Removes direct identifiers, institution and location. Keeps device "
            "make and model so a per device calibration policy can be applied, "
            "keeps age and sex, and reduces dates to the year."
        ),
        retain_device_identity=True,
        retain_patient_characteristics=True,
        retain_study_year=True,
        retain_full_dates=False,
        remap_uids=True,
    ),
    "aria_internal_dates": DeidProfile(
        name="aria_internal_dates",
        display_name="Internal, dates retained",
        description=(
            "As the default profile but retains full dates. Only for analysis "
            "that stays inside the institution and where dates are needed."
        ),
        retain_device_identity=True,
        retain_patient_characteristics=True,
        retain_study_year=True,
        retain_full_dates=True,
        remap_uids=True,
    ),
}


def get_profile(name: str) -> DeidProfile:
    return BUILTIN_PROFILES.get(name, BUILTIN_PROFILES["aria_default"])


# ---------------------------------------------------------------------------
# UID remapping
# ---------------------------------------------------------------------------


def remap_uid(original: str, salt: str, root: str = DEFAULT_UID_ROOT) -> str:
    """Deterministically map an identifier to a new one under ``root``.

    The same input and salt always produce the same output, which keeps images
    of one study linked, while the mapping cannot be reversed without the salt.
    """
    if not original:
        return ""
    digest = hashlib.sha256(f"{salt}|{original}".encode("utf-8")).digest()
    number = int.from_bytes(digest, "big")
    suffix = str(number)[:32].lstrip("0") or "1"
    uid = f"{root}.{suffix}"
    return uid[:64]


def pseudonym_for(original: str, salt: str, prefix: str = "ARIA") -> str:
    """Stable case pseudonym derived from a source identifier."""
    digest = hashlib.sha256(f"{salt}|{original}".encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:12].upper()}"


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@dataclass
class DeidReport:
    """What deidentification did, recorded with the case."""

    profile_name: str = ""
    profile_version: str = ""
    removed: list = field(default_factory=list)
    zeroed: list = field(default_factory=list)
    remapped: list = field(default_factory=list)
    cleaned: list = field(default_factory=list)
    kept: list = field(default_factory=list)
    private_removed: int = 0
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "profile_version": self.profile_version,
            "removed": list(self.removed),
            "zeroed": list(self.zeroed),
            "remapped": list(self.remapped),
            "cleaned": list(self.cleaned),
            "kept": list(self.kept),
            "private_tags_removed": self.private_removed,
            "warnings": list(self.warnings),
        }

    def summary(self) -> str:
        return (
            f"{len(self.removed)} removed, {len(self.zeroed)} emptied, "
            f"{len(self.remapped)} remapped, {len(self.cleaned)} cleaned, "
            f"{self.private_removed} private tags removed."
        )


def deidentify_dataset(ds, profile: DeidProfile, salt: str) -> DeidReport:
    """Apply a profile to a pydicom dataset in place.

    The dataset passed here is a working copy. The retained original file on
    disk is never written to (FR 002).
    """
    report = DeidReport(profile_name=profile.name, profile_version=profile.version)
    table = profile.actions()

    if not profile.retain_private_tags:
        try:
            before = len(ds)
            ds.remove_private_tags()
            report.private_removed = max(0, before - len(ds))
        except Exception as exc:
            report.warnings.append(f"Private tags could not be removed: {exc}")

    for tag, (name, action) in table.items():
        try:
            element = ds.get(tag, None)
        except Exception:
            continue
        if element is None:
            continue

        if action == Action.KEEP:
            report.kept.append(name)
            continue
        if action == Action.REMOVE:
            try:
                del ds[tag]
                report.removed.append(name)
            except KeyError:
                pass
            continue
        if action == Action.ZERO:
            try:
                ds[tag].value = ""
                report.zeroed.append(name)
            except Exception:
                pass
            continue
        if action == Action.REMAP_UID:
            try:
                value = str(ds[tag].value or "")
                if value:
                    if name in ("PatientID",):
                        ds[tag].value = pseudonym_for(value, salt)
                    else:
                        ds[tag].value = remap_uid(value, salt, profile.uid_root)
                    report.remapped.append(name)
            except Exception:
                pass
            continue
        if action == Action.CLEAN:
            try:
                value = str(ds[tag].value or "")
                if name.endswith("Date") and len(value) >= 4 and profile.retain_study_year:
                    ds[tag].value = value[:4] + "0101"
                    report.cleaned.append(name)
                else:
                    ds[tag].value = ""
                    report.zeroed.append(name)
            except Exception:
                pass

    # Record that deidentification happened, as the standard expects.
    try:
        ds.PatientIdentityRemoved = "YES"
        ds.DeidentificationMethod = (
            f"ARIA {profile.display_name} profile version {profile.version}"
        )
    except Exception as exc:
        report.warnings.append(f"The deidentification method could not be recorded: {exc}")

    if getattr(ds, "BurnedInAnnotation", "").upper() == "YES":
        report.warnings.append(
            "The file declares burned in annotation. Text rendered into the "
            "pixel data cannot be removed by tag based deidentification. Review "
            "the image before export."
        )
    return report


def deidentify_source_meta(meta, profile: DeidProfile, salt: str) -> DeidReport:
    """Apply a profile to the :class:`SourceImage` record ARIA stores."""
    report = DeidReport(profile_name=profile.name, profile_version=profile.version)

    if profile.remap_uids:
        for field_name in ("sop_instance_uid", "study_instance_uid", "series_instance_uid"):
            value = getattr(meta, field_name, "")
            if value:
                setattr(meta, field_name, remap_uid(value, salt, profile.uid_root))
                report.remapped.append(field_name)
    else:
        for field_name in ("sop_instance_uid", "study_instance_uid", "series_instance_uid"):
            if getattr(meta, field_name, ""):
                setattr(meta, field_name, "")
                report.removed.append(field_name)

    if not profile.retain_device_identity:
        for field_name in ("manufacturer", "manufacturer_model"):
            if getattr(meta, field_name, ""):
                setattr(meta, field_name, "")
                report.removed.append(field_name)
    else:
        report.kept.extend(["manufacturer", "manufacturer_model"])

    if meta.acquisition_date:
        if profile.retain_full_dates:
            report.kept.append("acquisition_date")
        elif profile.retain_study_year and len(meta.acquisition_date) >= 4:
            meta.acquisition_date = meta.acquisition_date[:4]
            report.cleaned.append("acquisition_date")
        else:
            meta.acquisition_date = ""
            report.removed.append("acquisition_date")

    return report


# ---------------------------------------------------------------------------
# Export scanning (AC 009)
# ---------------------------------------------------------------------------

#: Attribute names that must never appear as a key in an export payload.
PROHIBITED_KEYS = {
    "patientname", "patient_name", "patientid", "patient_id", "patientbirthdate",
    "patient_birth_date", "birthdate", "birth_date", "patientaddress",
    "patient_address", "address", "telephone", "phone", "patienttelephonenumbers",
    "institutionname", "institution_name", "institutionaddress",
    "referringphysicianname", "referring_physician", "accessionnumber",
    "accession_number", "medicalrecordlocator", "otherpatientids", "nhs_number",
    "ssn", "social_security", "mrn", "medical_record_number",
}

_DATE_PATTERN = re.compile(r"\b(19|20)\d{2}[-/]?(0[1-9]|1[0-2])[-/]?(0[1-9]|[12]\d|3[01])\b")
_PHONE_PATTERN = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")

#: A DICOM identifier is a dotted numeric string. It is structurally similar to
#: a telephone number, so it is recognised and excluded before the telephone
#: check runs, otherwise every export would report false findings.
_UID_PATTERN = re.compile(r"^\d+(\.\d+)+$")

#: A telephone number written inside free text, with separators. Kept strict so
#: that measurements and identifiers in prose do not trigger it.
_EMBEDDED_PHONE_PATTERN = re.compile(
    r"(?<![\d.])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?![\d.])"
)

#: Keys whose values are structural identifiers rather than personal data.
_STRUCTURAL_KEY_HINTS = (
    "uid", "checksum", "sha", "hash", "version", "_id", "id_", "guid",
    "instance", "class", "syntax", "sequence", "revision", "counter",
)


def _is_structural(path: str, value: str) -> bool:
    """True when a value is an identifier of the data rather than of a person."""
    if _UID_PATTERN.match(value):
        return True
    lowered = path.lower()
    return any(hint in lowered for hint in _STRUCTURAL_KEY_HINTS)


@dataclass
class ScanFinding:
    path: str
    kind: str
    message: str
    sample: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "message": self.message, "sample": self.sample}


def scan_payload(payload, prohibited_terms=None, _path: str = "$") -> list:
    """Scan an export payload for anything that looks like a direct identifier.

    This runs on every export before the file is written. It is a safety net
    behind the import time deidentification, not a substitute for it.
    """
    findings: list = []
    terms = {t.strip().lower() for t in (prohibited_terms or []) if t and t.strip()}

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower().replace(" ", "")
                if key_l in PROHIBITED_KEYS and value not in (None, "", [], {}):
                    findings.append(
                        ScanFinding(
                            path=f"{path}.{key}",
                            kind="prohibited_key",
                            message=f"The field {key} is a direct identifier and must not be exported.",
                            sample=str(value)[:40],
                        )
                    )
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str):
            text = node.strip()
            if not text:
                return
            low = text.lower()
            for term in terms:
                if term and term in low:
                    findings.append(
                        ScanFinding(
                            path=path,
                            kind="prohibited_term",
                            message=f"The configured prohibited term {term!r} appears in this value.",
                            sample=text[:40],
                        )
                    )
            if _EMAIL_PATTERN.search(text):
                findings.append(
                    ScanFinding(path=path, kind="email", message="A value looks like an email address.", sample=text[:40])
                )
            if not _is_structural(path, text):
                if len(text) >= 9 and _PHONE_PATTERN.fullmatch(text):
                    findings.append(
                        ScanFinding(
                            path=path, kind="phone",
                            message="A value looks like a telephone number.",
                            sample=text[:40],
                        )
                    )
                elif _EMBEDDED_PHONE_PATTERN.search(text):
                    # A number written inside a free text comment is the usual
                    # way a contact detail reaches an export.
                    findings.append(
                        ScanFinding(
                            path=path, kind="phone",
                            message="A telephone number appears inside this text.",
                            sample=text[:60],
                        )
                    )

    walk(payload, _path)
    return findings


def scan_json_file(path, prohibited_terms=None) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return scan_payload(payload, prohibited_terms)


def date_findings(payload) -> list:
    """Separate pass for full dates, which a project may legitimately retain."""
    findings: list = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, str) and _DATE_PATTERN.search(node):
            if "timestamp" in path.lower() or "_at" in path.lower() or "created" in path.lower():
                return
            findings.append(
                ScanFinding(
                    path=path, kind="full_date",
                    message="A value contains a full date. Confirm the profile intends to retain dates.",
                    sample=node[:40],
                )
            )

    walk(payload, "$")
    return findings
