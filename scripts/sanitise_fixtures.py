"""Replace identifying and traceable metadata in the sample images.

The files in ``Test Artifacts`` are real panoramic radiographs used as test
fixtures. They carried no patient name, identifier or birth date, but they did
carry values that are traceable or that describe the patient:

* identifiers under the acquiring vendor's organisation root,
* the real acquisition date and time,
* the device manufacturer, model, serial number and software version,
* vendor private tags,
* the patient's age and sex.

This script rewrites those with fabricated values, so the published fixtures
exercise every code path while leading nowhere if anyone follows them. It is in
the repository rather than run once and forgotten, so the substitution is
auditable and repeatable.

    python scripts/sanitise_fixtures.py --check     report what would change
    python scripts/sanitise_fixtures.py --apply     rewrite the files in place

Pixel data is never altered. The radiographs themselves are unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES = ROOT / "Test Artifacts"

#: A synthetic organisation root. It is not registered to anyone, so an
#: identifier built under it resolves to no institution and no device.
SYNTHETIC_ROOT = "1.2.826.0.1.3680043.10.9999"

#: Fabricated acquisition context. Plausible in shape, correct in none of its
#: parts.
DUMMY = {
    "StudyDate": "20200115",
    "SeriesDate": "20200115",
    "AcquisitionDate": "20200115",
    "ContentDate": "20200115",
    "InstanceCreationDate": "20200115",
    "StudyTime": "101500",
    "SeriesTime": "101500",
    "AcquisitionTime": "101500",
    "ContentTime": "101500",
    "InstanceCreationTime": "101500",
    "Manufacturer": "Example Imaging Systems",
    "ManufacturerModelName": "Example Panoramic Unit",
    "SoftwareVersions": "1.0.0",
    "StudyID": "FIXTURE001",
    "PatientSex": "F",
    "PatientAge": "042Y",
    "PatientIdentityRemoved": "YES",
    "DeidentificationMethod": (
        "Identifiers, dates, device identity and patient characteristics "
        "replaced with fabricated values for use as a public test fixture"
    ),
}

#: Attributes removed outright. Nothing here is needed to exercise ARIA.
REMOVE = [
    "DeviceSerialNumber",
    "InstanceCreatorUID",
    "AccessionNumber",
    "ReferringPhysicianName",
    "PatientComments",
    "StudyDescription",
    "SeriesDescription",
    "ProtocolName",
    "DetectorID",
    "DateOfLastCalibration",
    "TimeOfLastCalibration",
    "StationName",
    "InstitutionName",
    "InstitutionAddress",
    "InstitutionalDepartmentName",
    "OperatorsName",
    "PerformingPhysicianName",
]

#: Identifier attributes rewritten under the synthetic root, keeping the study,
#: series and instance relationship intact so the files still behave as a study.
UID_FIELDS = [
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "FrameOfReferenceUID",
]


def synthetic_uid(original: str, salt: str = "aria-public-fixture") -> str:
    """A deterministic identifier under the synthetic root.

    The same input always gives the same output, so the study, series and
    instance stay related. The mapping cannot be reversed without the salt, and
    the root belongs to nobody.
    """
    digest = hashlib.sha256(f"{salt}|{original}".encode("utf-8")).digest()
    number = int.from_bytes(digest, "big") % (10 ** 18)
    return f"{SYNTHETIC_ROOT}.{number}"


def sanitise_dicom(path: Path, apply: bool) -> list:
    """Rewrite one DICOM file. Returns the changes made or that would be made."""
    import pydicom

    changes: list = []
    ds = pydicom.dcmread(str(path))

    private_count = sum(1 for el in ds if el.tag.is_private)
    if private_count:
        changes.append(f"remove {private_count} vendor private tags")
        if apply:
            ds.remove_private_tags()

    for field in UID_FIELDS:
        current = getattr(ds, field, None)
        if current:
            replacement = synthetic_uid(str(current))
            changes.append(f"{field}: {current} -> {replacement}")
            if apply:
                setattr(ds, field, replacement)

    for field in REMOVE:
        if field in ds:
            value = getattr(ds, field, "")
            if str(value).strip():
                changes.append(f"remove {field} ({str(value)[:40]!r})")
            if apply:
                delattr(ds, field)

    for field, value in DUMMY.items():
        current = getattr(ds, field, None)
        if current is not None and str(current) == value:
            continue
        if current is not None and str(current).strip():
            changes.append(f"{field}: {current!r} -> {value!r}")
        elif field in ("PatientIdentityRemoved", "DeidentificationMethod"):
            changes.append(f"{field}: set")
        if apply:
            try:
                setattr(ds, field, value)
            except Exception as exc:
                changes.append(f"  could not set {field}: {exc}")

    # The file meta header carries its own copy of the instance identifier and
    # the writing implementation, both of which point at the vendor.
    if apply or True:
        meta_changes = []
        if getattr(ds.file_meta, "MediaStorageSOPInstanceUID", None):
            new = synthetic_uid(str(ds.file_meta.MediaStorageSOPInstanceUID))
            meta_changes.append("MediaStorageSOPInstanceUID")
            if apply:
                ds.file_meta.MediaStorageSOPInstanceUID = new
        if getattr(ds.file_meta, "ImplementationClassUID", None):
            meta_changes.append("ImplementationClassUID")
            if apply:
                ds.file_meta.ImplementationClassUID = synthetic_uid("implementation")
        if getattr(ds.file_meta, "ImplementationVersionName", None):
            meta_changes.append("ImplementationVersionName")
            if apply:
                ds.file_meta.ImplementationVersionName = "FIXTURE_1_0"
        if meta_changes:
            changes.append("file meta: " + ", ".join(meta_changes))

    if apply:
        # Keep the pixel data exactly as it is. Only the header is rewritten.
        ds.save_as(str(path), enforce_file_format=True)

    return changes


def sanitise_png(path: Path, apply: bool) -> list:
    """Strip every ancillary chunk from a PNG, keeping the pixels."""
    from PIL import Image

    changes: list = []
    with Image.open(path) as im:
        metadata = {k: v for k, v in (im.info or {}).items() if k not in ("transparency",)}
        mode, size = im.mode, im.size
        pixels = im.copy()

    if metadata:
        changes.append(f"strip PNG metadata keys: {sorted(metadata)}")
    else:
        changes.append("no PNG metadata present")

    if apply:
        # Saving a fresh image writes only the header, palette and pixel data,
        # so any text, timestamp or profile chunk is dropped.
        clean = Image.new(mode, size)
        clean.putdata(list(pixels.getdata()))
        clean.save(path, format="PNG", optimize=True)
    return changes


#: The original file names encode the acquisition date and time, so they are
#: replaced along with the metadata.
RENAMES = {
    ".dcm": "fixture_panoramic_{index:02d}.dcm",
    ".png": "fixture_cropped_{index:02d}.png",
}


def neutral_names(files) -> dict:
    """Map each fixture to a name that carries no acquisition information."""
    mapping: dict = {}
    counters: dict = {}
    for path in files:
        suffix = path.suffix.lower()
        pattern = RENAMES.get(suffix)
        if pattern is None:
            continue
        counters[suffix] = counters.get(suffix, 0) + 1
        target = path.with_name(pattern.format(index=counters[suffix]))
        if target != path:
            mapping[path] = target
    return mapping


def verify(path: Path) -> list:
    """Report anything identifying that survives in a sanitised file."""
    problems: list = []
    if path.suffix.lower() == ".png":
        from PIL import Image

        with Image.open(path) as im:
            leftovers = {k for k in (im.info or {}) if k not in ("transparency",)}
        if leftovers:
            problems.append(f"PNG still carries metadata: {sorted(leftovers)}")
        return problems

    import pydicom

    ds = pydicom.dcmread(str(path), stop_before_pixels=True)

    for el in ds:
        if el.tag.is_private:
            problems.append(f"private tag remains: {el.tag}")

    vendor_roots = ("1.2.410.", "1.2.840.113654.")
    for field in UID_FIELDS + ["SOPClassUID"]:
        value = str(getattr(ds, field, "") or "")
        if field == "SOPClassUID":
            continue
        if value and any(value.startswith(root) for root in vendor_roots):
            problems.append(f"{field} still under a vendor root: {value}")

    for field in ("MediaStorageSOPInstanceUID", "ImplementationClassUID"):
        value = str(getattr(ds.file_meta, field, "") or "")
        if value and any(value.startswith(root) for root in vendor_roots):
            problems.append(f"file meta {field} still under a vendor root: {value}")

    for field in REMOVE:
        if field in ds and str(getattr(ds, field, "")).strip():
            problems.append(f"{field} was not removed")

    for field in ("StudyDate", "AcquisitionDate", "ContentDate", "SeriesDate"):
        value = str(getattr(ds, field, "") or "")
        if value and value != DUMMY[field]:
            problems.append(f"{field} is {value}, not the fabricated date")

    for field in ("Manufacturer", "ManufacturerModelName"):
        value = str(getattr(ds, field, "") or "")
        if value != DUMMY[field]:
            problems.append(f"{field} is {value!r}, not the fabricated value")

    if str(getattr(ds, "BurnedInAnnotation", "")).upper() == "YES":
        problems.append(
            "the file declares burned in annotation, so the pixels need review"
        )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="report what would change")
    group.add_argument("--apply", action="store_true", help="rewrite the files in place")
    parser.add_argument(
        "--backup", action="store_true",
        help="keep a copy of each original beside it with a .original suffix",
    )
    args = parser.parse_args()

    if not FIXTURES.exists():
        print(f"No fixtures folder at {FIXTURES}")
        return 1

    files = sorted(p for p in FIXTURES.iterdir() if p.suffix.lower() in (".dcm", ".png"))
    if not files:
        print("No fixture files found.")
        return 1

    print(f"{'Rewriting' if args.apply else 'Checking'} {len(files)} fixture files\n")

    for path in files:
        print(f"{path.name}")
        if args.apply and args.backup:
            shutil.copy2(path, path.with_suffix(path.suffix + ".original"))

        if path.suffix.lower() == ".dcm":
            changes = sanitise_dicom(path, args.apply)
        else:
            changes = sanitise_png(path, args.apply)

        for change in changes:
            print(f"    {change}")
        print()

    # The file names encode the acquisition date and time.
    renames = neutral_names(files)
    if renames:
        print("File names")
        for source, target in renames.items():
            print(f"    {source.name} -> {target.name}")
            if args.apply:
                source.rename(target)
        print()
        if args.apply:
            files = sorted(
                p for p in FIXTURES.iterdir()
                if p.suffix.lower() in (".dcm", ".png")
            )

    if args.apply:
        print("Verifying the result\n")
        clean = True
        for path in files:
            problems = verify(path)
            status = "clean" if not problems else f"{len(problems)} problems"
            print(f"  {path.name}: {status}")
            for problem in problems:
                print(f"      {problem}")
                clean = False
        print()
        if not clean:
            print("Sanitisation did not fully succeed. Do not publish these files.")
            return 1
        print("Every fixture is clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
