"""Import guardrails.

Nothing reaches the decoder until it has passed these checks. The point is that
a user who drags in a holiday photo, a corrupt study, a four gigabyte volume or
a file that merely claims to be a radiograph gets a clear explanation and a next
step, not a stack trace and not an application that runs out of memory.

Checks are ordered from cheapest to most expensive: extension, then magic bytes,
then declared header dimensions, then available memory and disk, and only then
pixel decoding.
"""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

#: Accepted file extensions. Case is ignored.
ALLOWED_EXTENSIONS = {".dcm", ".dicom", ".png", ""}

#: Extensions that people commonly try and that deserve a specific message
#: rather than a generic refusal.
KNOWN_UNSUPPORTED = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".bmp": "Windows bitmap",
    ".gif": "GIF",
    ".webp": "WebP",
    ".heic": "HEIC",
    ".pdf": "PDF",
    ".nii": "NIfTI",
    ".gz": "compressed archive",
    ".zip": "archive",
    ".rar": "archive",
    ".7z": "archive",
    ".mha": "MetaImage",
    ".mhd": "MetaImage",
    ".nrrd": "NRRD",
    ".svs": "whole slide image",
    ".stl": "3D model",
    ".mp4": "video",
    ".avi": "video",
    ".doc": "document",
    ".docx": "document",
    ".xlsx": "spreadsheet",
    ".txt": "text file",
    ".exe": "executable",
    ".dll": "library",
}

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DICM_MAGIC = b"DICM"

#: Default limits. An administrator can raise or lower these in preferences.
DEFAULT_LIMITS = {
    "max_file_bytes": 512 * 1024 * 1024,        # 512 MB per file
    "min_file_bytes": 128,
    "max_pixels": 250_000_000,                   # 250 megapixels
    "max_dimension": 30_000,                     # per axis
    "min_dimension": 64,
    "max_batch_files": 5000,
    "max_batch_bytes": 40 * 1024 * 1024 * 1024,  # 40 GB per import batch
    "disk_headroom_bytes": 1024 * 1024 * 1024,   # keep 1 GB free
    "working_copy_factor": 2.5,                  # original plus working plus slack
    "memory_safety_factor": 3.0,                 # decoded pixels plus display copies
}


class GuardCode(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"
    NOT_A_FILE = "not_a_file"
    UNREADABLE = "unreadable"
    EMPTY = "empty"
    TOO_SMALL = "too_small"
    TOO_LARGE = "too_large"
    EXTENSION_NOT_ALLOWED = "extension_not_allowed"
    KNOWN_UNSUPPORTED_FORMAT = "known_unsupported_format"
    SIGNATURE_MISMATCH = "signature_mismatch"
    NOT_AN_IMAGE = "not_an_image"
    DIMENSION_TOO_LARGE = "dimension_too_large"
    DIMENSION_TOO_SMALL = "dimension_too_small"
    PIXEL_BUDGET_EXCEEDED = "pixel_budget_exceeded"
    MULTIFRAME_NOT_SUPPORTED = "multiframe_not_supported"
    UNSUPPORTED_TRANSFER_SYNTAX = "unsupported_transfer_syntax"
    MISSING_DECODER = "missing_decoder"
    UNSUPPORTED_BIT_DEPTH = "unsupported_bit_depth"
    INSUFFICIENT_DISK = "insufficient_disk"
    INSUFFICIENT_MEMORY = "insufficient_memory"
    BATCH_TOO_LARGE = "batch_too_large"
    CORRUPT_HEADER = "corrupt_header"
    DUPLICATE_CONTENT = "duplicate_content"


@dataclass
class GuardIssue:
    code: str
    message: str
    remedy: str
    fatal: bool = True
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "remedy": self.remedy,
            "fatal": self.fatal,
            **self.detail,
        }


@dataclass
class InspectionResult:
    """What the guards learned about a candidate file."""

    path: str = ""
    accepted: bool = False
    detected_format: str = ""        # dicom or png
    byte_size: int = 0
    rows: int = 0
    columns: int = 0
    bits_stored: int = 0
    samples_per_pixel: int = 1
    photometric: str = ""
    transfer_syntax: str = ""
    frames: int = 1
    issues: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def megapixels(self) -> float:
        return self.rows * self.columns / 1_000_000.0

    @property
    def fatal_issues(self) -> list:
        return [i for i in self.issues if i.fatal]

    def first_problem(self) -> GuardIssue | None:
        fatal = self.fatal_issues
        return fatal[0] if fatal else None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "accepted": self.accepted,
            "detected_format": self.detected_format,
            "byte_size": self.byte_size,
            "rows": self.rows,
            "columns": self.columns,
            "bits_stored": self.bits_stored,
            "photometric": self.photometric,
            "transfer_syntax": self.transfer_syntax,
            "frames": self.frames,
            "megapixels": round(self.megapixels, 2),
            "issues": [i.to_dict() for i in self.issues],
            "warnings": list(self.warnings),
        }


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
# Format sniffing
# ---------------------------------------------------------------------------


def sniff_format(path: Path) -> str:
    """Identify a file from its content, not its name.

    Returns ``dicom``, ``png`` or an empty string. A file named ``scan.png``
    that is really a JPEG is rejected here rather than half way through
    decoding.
    """
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except OSError:
        return ""

    if head.startswith(PNG_SIGNATURE):
        return "png"
    if len(head) >= 132 and head[128:132] == DICM_MAGIC:
        return "dicom"
    # DICOM written without the 128 byte preamble still begins with a valid
    # group 0008 element in one of the two explicit little endian forms, or in
    # implicit little endian.
    if len(head) >= 8:
        group, element = struct.unpack("<HH", head[0:4])
        if group in (0x0002, 0x0008) and element in (0x0000, 0x0001, 0x0005, 0x0008, 0x0016, 0x0018):
            return "dicom"
    return ""


def read_png_header(path: Path) -> dict:
    """Read the PNG image header chunk without decoding any pixels.

    This is what stops a small file that declares an enormous canvas from being
    expanded into memory.
    """
    with open(path, "rb") as fh:
        signature = fh.read(8)
        if signature != PNG_SIGNATURE:
            raise ValueError("The file does not carry a PNG signature.")
        length_bytes = fh.read(4)
        chunk_type = fh.read(4)
        if chunk_type != b"IHDR" or len(length_bytes) != 4:
            raise ValueError("The PNG image header chunk is missing or malformed.")
        data = fh.read(13)
        if len(data) != 13:
            raise ValueError("The PNG image header chunk is truncated.")
    width, height, bit_depth, colour_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", data
    )
    colour_names = {
        0: "grayscale",
        2: "truecolour",
        3: "indexed",
        4: "grayscale with alpha",
        6: "truecolour with alpha",
    }
    samples = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour_type, 1)
    return {
        "width": int(width),
        "height": int(height),
        "bit_depth": int(bit_depth),
        "colour_type": int(colour_type),
        "colour_name": colour_names.get(colour_type, f"type {colour_type}"),
        "samples_per_pixel": samples,
        "interlace": int(interlace),
    }


# ---------------------------------------------------------------------------
# Resource checks
# ---------------------------------------------------------------------------


def available_memory_bytes() -> int | None:
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        return None


def free_disk_bytes(path: Path) -> int | None:
    try:
        target = path if path.exists() else path.parent
        while not target.exists() and target != target.parent:
            target = target.parent
        return int(shutil.disk_usage(target).free)
    except OSError:
        return None


def estimated_decoded_bytes(rows: int, cols: int, bits: int, samples: int = 1) -> int:
    bytes_per_sample = 2 if bits > 8 else 1
    return int(rows) * int(cols) * int(samples) * bytes_per_sample


def check_disk_space(
    source_size: int, destination: Path, limits: dict | None = None
) -> GuardIssue | None:
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    free = free_disk_bytes(destination)
    if free is None:
        return None
    needed = int(source_size * limits["working_copy_factor"]) + limits["disk_headroom_bytes"]
    if free < needed:
        return GuardIssue(
            code=GuardCode.INSUFFICIENT_DISK.value,
            message=(
                f"Importing this file needs about {human_bytes(needed)} of free "
                f"space and only {human_bytes(free)} is available."
            ),
            remedy=(
                "Free space on the storage location, or move the ARIA data "
                "folder to a larger drive in Preferences, Storage."
            ),
            detail={"needed_bytes": needed, "free_bytes": free},
        )
    return None


def check_memory(
    rows: int, cols: int, bits: int, samples: int, limits: dict | None = None
) -> GuardIssue | None:
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    available = available_memory_bytes()
    if available is None:
        return None
    needed = int(
        estimated_decoded_bytes(rows, cols, bits, samples) * limits["memory_safety_factor"]
    )
    if available < needed:
        return GuardIssue(
            code=GuardCode.INSUFFICIENT_MEMORY.value,
            message=(
                f"Opening this image needs roughly {human_bytes(needed)} of free "
                f"memory and only {human_bytes(available)} is available."
            ),
            remedy="Close other applications and try again, or import on a workstation with more memory.",
            detail={"needed_bytes": needed, "available_bytes": available},
        )
    return None


# ---------------------------------------------------------------------------
# The inspector
# ---------------------------------------------------------------------------


def inspect_file(path, limits: dict | None = None, destination=None) -> InspectionResult:
    """Run every pre decode check against one candidate file."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    p = Path(path)
    result = InspectionResult(path=str(p))

    # -- existence and readability ----------------------------------------
    if not p.exists():
        result.issues.append(
            GuardIssue(
                GuardCode.NOT_FOUND.value,
                "The file could not be found.",
                "Check that the file still exists and that the drive is connected.",
            )
        )
        return result
    if not p.is_file():
        result.issues.append(
            GuardIssue(
                GuardCode.NOT_A_FILE.value,
                "This path is a folder, not a file.",
                "Select the image files inside the folder, or use Import Folder.",
            )
        )
        return result
    if not os.access(p, os.R_OK):
        result.issues.append(
            GuardIssue(
                GuardCode.UNREADABLE.value,
                "The file cannot be read with the current permissions.",
                "Check the file permissions, or copy the file to a location you can read.",
            )
        )
        return result

    size = p.stat().st_size
    result.byte_size = size

    if size == 0:
        result.issues.append(
            GuardIssue(GuardCode.EMPTY.value, "The file is empty.", "Re export the study from the source system.")
        )
        return result
    if size < limits["min_file_bytes"]:
        result.issues.append(
            GuardIssue(
                GuardCode.TOO_SMALL.value,
                f"The file is only {human_bytes(size)}, which is too small to be a radiograph.",
                "Check that the export completed and that the file is not a placeholder.",
            )
        )
        return result
    if size > limits["max_file_bytes"]:
        result.issues.append(
            GuardIssue(
                GuardCode.TOO_LARGE.value,
                f"The file is {human_bytes(size)}, above the {human_bytes(limits['max_file_bytes'])} limit for a single image.",
                "Confirm this is a single panoramic image. An administrator can raise the limit in Preferences, Import.",
                detail={"byte_size": size, "limit": limits["max_file_bytes"]},
            )
        )
        return result

    # -- extension ---------------------------------------------------------
    suffix = p.suffix.lower()
    if suffix in KNOWN_UNSUPPORTED:
        result.issues.append(
            GuardIssue(
                GuardCode.KNOWN_UNSUPPORTED_FORMAT.value,
                f"{KNOWN_UNSUPPORTED[suffix]} files are not supported.",
                "ARIA reads DICOM Part 10 files and PNG images. Export the study as DICOM, or convert the image to PNG without resampling it.",
                detail={"extension": suffix},
            )
        )
        return result
    if suffix not in ALLOWED_EXTENSIONS:
        result.issues.append(
            GuardIssue(
                GuardCode.EXTENSION_NOT_ALLOWED.value,
                f"The extension {suffix or '(none)'} is not one ARIA accepts.",
                "ARIA reads DICOM Part 10 files and PNG images.",
                detail={"extension": suffix},
            )
        )
        return result

    # -- content signature -------------------------------------------------
    detected = sniff_format(p)
    if not detected:
        result.issues.append(
            GuardIssue(
                GuardCode.NOT_AN_IMAGE.value,
                "The file content is neither a DICOM Part 10 file nor a PNG image.",
                "Check that the file is not corrupt and that it was exported completely.",
            )
        )
        return result
    if suffix == ".png" and detected != "png":
        result.issues.append(
            GuardIssue(
                GuardCode.SIGNATURE_MISMATCH.value,
                "The file is named as a PNG but its content is not a PNG image.",
                "Rename the file to match its real format, or re export it.",
            )
        )
        return result
    if suffix in (".dcm", ".dicom") and detected != "dicom":
        result.issues.append(
            GuardIssue(
                GuardCode.SIGNATURE_MISMATCH.value,
                "The file is named as DICOM but its content is not a DICOM Part 10 file.",
                "Re export the study from the source system as DICOM Part 10.",
            )
        )
        return result

    result.detected_format = detected

    if detected == "png":
        _inspect_png(p, result, limits)
    else:
        _inspect_dicom(p, result, limits)

    if result.fatal_issues:
        return result

    # -- resources ---------------------------------------------------------
    if destination is not None:
        issue = check_disk_space(size, Path(destination), limits)
        if issue:
            result.issues.append(issue)
    issue = check_memory(
        result.rows, result.columns, result.bits_stored or 8, result.samples_per_pixel, limits
    )
    if issue:
        result.issues.append(issue)

    result.accepted = not result.fatal_issues
    return result


def _check_dimensions(result: InspectionResult, limits: dict) -> None:
    rows, cols = result.rows, result.columns
    if rows <= 0 or cols <= 0:
        result.issues.append(
            GuardIssue(
                GuardCode.CORRUPT_HEADER.value,
                "The image header does not declare a usable size.",
                "Re export the study from the source system.",
            )
        )
        return
    if max(rows, cols) > limits["max_dimension"]:
        result.issues.append(
            GuardIssue(
                GuardCode.DIMENSION_TOO_LARGE.value,
                f"The image is {cols} by {rows} pixels, which exceeds the {limits['max_dimension']} pixel limit on a single axis.",
                "Confirm this is a panoramic radiograph rather than a whole slide or volume export.",
                detail={"rows": rows, "columns": cols},
            )
        )
        return
    if min(rows, cols) < limits["min_dimension"]:
        result.warnings.append(
            f"The image is only {cols} by {rows} pixels. Radiomorphometric "
            f"measurement on an image this small carries a large relative error."
        )
    if rows * cols > limits["max_pixels"]:
        result.issues.append(
            GuardIssue(
                GuardCode.PIXEL_BUDGET_EXCEEDED.value,
                f"The image contains {rows * cols / 1e6:.0f} megapixels, above the {limits['max_pixels'] / 1e6:.0f} megapixel limit.",
                "An administrator can raise the limit in Preferences, Import, if the workstation has enough memory.",
                detail={"pixels": rows * cols},
            )
        )


def _inspect_png(p: Path, result: InspectionResult, limits: dict) -> None:
    try:
        header = read_png_header(p)
    except Exception as exc:
        result.issues.append(
            GuardIssue(
                GuardCode.CORRUPT_HEADER.value,
                f"The PNG header could not be read: {exc}",
                "Re export the image, or open it in an image viewer to confirm it is not damaged.",
            )
        )
        return

    result.rows = header["height"]
    result.columns = header["width"]
    result.bits_stored = header["bit_depth"]
    result.samples_per_pixel = header["samples_per_pixel"]
    result.photometric = header["colour_name"]

    _check_dimensions(result, limits)
    if result.fatal_issues:
        return

    if header["bit_depth"] not in (8, 16):
        if header["colour_type"] == 3:
            result.warnings.append(
                f"This is an indexed colour PNG with a {header['bit_depth']} bit "
                f"palette. It will be expanded to 8 bit for display and the "
                f"original file is retained unchanged."
            )
        else:
            result.issues.append(
                GuardIssue(
                    GuardCode.UNSUPPORTED_BIT_DEPTH.value,
                    f"A bit depth of {header['bit_depth']} is not supported.",
                    "ARIA reads 8 bit and 16 bit grayscale PNG content. Re export at 8 or 16 bits.",
                    detail={"bit_depth": header["bit_depth"]},
                )
            )
            return

    if header["colour_type"] in (2, 6):
        result.warnings.append(
            f"This is a {header['colour_name']} PNG. It is converted to "
            f"grayscale for display only, and the original channels are "
            f"preserved in the retained file."
        )
    if header["interlace"]:
        result.warnings.append(
            "The image is interlaced. It decodes correctly but takes longer to load."
        )


def _inspect_dicom(p: Path, result: InspectionResult, limits: dict) -> None:
    try:
        import pydicom
        from pydicom.errors import InvalidDicomError
    except ImportError:
        result.issues.append(
            GuardIssue(
                GuardCode.MISSING_DECODER.value,
                "The DICOM reader is not available in this installation.",
                "Reinstall ARIA. If the problem continues, contact the administrator.",
            )
        )
        return

    try:
        ds = pydicom.dcmread(str(p), stop_before_pixels=True, force=False)
    except InvalidDicomError:
        result.issues.append(
            GuardIssue(
                GuardCode.SIGNATURE_MISMATCH.value,
                "The file is not a valid DICOM Part 10 file.",
                "Re export the study from the source system as DICOM Part 10 with a file meta header.",
            )
        )
        return
    except Exception as exc:
        result.issues.append(
            GuardIssue(
                GuardCode.CORRUPT_HEADER.value,
                f"The DICOM header could not be read: {exc}",
                "Re export the study, or open it in a DICOM viewer to confirm it is not damaged.",
            )
        )
        return

    result.rows = int(getattr(ds, "Rows", 0) or 0)
    result.columns = int(getattr(ds, "Columns", 0) or 0)
    result.bits_stored = int(getattr(ds, "BitsStored", 0) or 0)
    result.samples_per_pixel = int(getattr(ds, "SamplesPerPixel", 1) or 1)
    result.photometric = str(getattr(ds, "PhotometricInterpretation", "") or "")
    frames = int(getattr(ds, "NumberOfFrames", 1) or 1)
    result.frames = frames

    try:
        ts = ds.file_meta.TransferSyntaxUID
        result.transfer_syntax = str(ts)
    except AttributeError:
        result.issues.append(
            GuardIssue(
                GuardCode.CORRUPT_HEADER.value,
                "The file has no transfer syntax in its meta header.",
                "Re export the study as DICOM Part 10.",
            )
        )
        return

    if frames > 1:
        result.issues.append(
            GuardIssue(
                GuardCode.MULTIFRAME_NOT_SUPPORTED.value,
                f"This is a multi frame object with {frames} frames.",
                "ARIA annotates single image panoramic studies. Export the required frame as a single image object.",
                detail={"frames": frames},
            )
        )
        return

    _check_dimensions(result, limits)
    if result.fatal_issues:
        return

    if result.bits_stored and result.bits_stored > 16:
        result.issues.append(
            GuardIssue(
                GuardCode.UNSUPPORTED_BIT_DEPTH.value,
                f"A stored bit depth of {result.bits_stored} is not supported.",
                "ARIA reads up to 16 bits per sample.",
            )
        )
        return

    issue = _check_transfer_syntax(ts)
    if issue:
        result.issues.append(issue)
        return

    photometric = result.photometric.upper()
    if photometric in ("RGB", "YBR_FULL", "YBR_FULL_422", "PALETTE COLOR"):
        result.warnings.append(
            f"The photometric interpretation is {result.photometric}. The image "
            f"is converted to grayscale for display only and the original pixel "
            f"data is retained unchanged."
        )
    elif photometric not in ("MONOCHROME1", "MONOCHROME2"):
        result.warnings.append(
            f"Unusual photometric interpretation {result.photometric or 'none'}. "
            f"Check the displayed image against the source before annotating."
        )

    modality = str(getattr(ds, "Modality", "") or "")
    if modality and modality.upper() not in ("PX", "DX", "CR", "OP", "IO", "XA", "RF", "OT"):
        result.warnings.append(
            f"The modality is {modality}, which is not a projection radiograph "
            f"modality. Confirm this is a panoramic study."
        )

    if str(getattr(ds, "LossyImageCompression", "") or "") == "01":
        method = str(getattr(ds, "LossyImageCompressionMethod", "") or "an unspecified method")
        result.warnings.append(
            f"The image was lossy compressed using {method}. Cortical margins may "
            f"be altered, which affects width measurement and cortical index grading."
        )


#: Transfer syntaxes ARIA reads without an optional decoder package.
NATIVE_TRANSFER_SYNTAXES = {
    "1.2.840.10008.1.2": "Implicit VR Little Endian",
    "1.2.840.10008.1.2.1": "Explicit VR Little Endian",
    "1.2.840.10008.1.2.1.99": "Deflated Explicit VR Little Endian",
    "1.2.840.10008.1.2.2": "Explicit VR Big Endian",
    "1.2.840.10008.1.2.5": "RLE Lossless",
}

#: Compressed syntaxes that need an optional decoder package installed.
CODEC_TRANSFER_SYNTAXES = {
    "1.2.840.10008.1.2.4.50": ("JPEG Baseline", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.51": ("JPEG Extended", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.57": ("JPEG Lossless", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.70": ("JPEG Lossless SV1", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.80": ("JPEG-LS Lossless", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.81": ("JPEG-LS Near Lossless", "pylibjpeg with the libjpeg plugin"),
    "1.2.840.10008.1.2.4.90": ("JPEG 2000 Lossless", "pylibjpeg with the openjpeg plugin"),
    "1.2.840.10008.1.2.4.91": ("JPEG 2000", "pylibjpeg with the openjpeg plugin"),
}


def _decoder_available(uid: str) -> bool:
    try:
        import pydicom
        from pydicom.uid import UID

        return bool(UID(uid).is_supported) if hasattr(UID(uid), "is_supported") else False
    except Exception:
        pass
    try:
        if "4.90" in uid or "4.91" in uid:
            import openjpeg  # noqa: F401
            return True
        import libjpeg  # noqa: F401
        return True
    except Exception:
        return False


def _check_transfer_syntax(uid) -> GuardIssue | None:
    text = str(uid)
    if text in NATIVE_TRANSFER_SYNTAXES:
        return None
    if text in CODEC_TRANSFER_SYNTAXES:
        name, package = CODEC_TRANSFER_SYNTAXES[text]
        if _decoder_available(text):
            return None
        return GuardIssue(
            code=GuardCode.MISSING_DECODER.value,
            message=f"This file uses {name} compression and no decoder for it is installed.",
            remedy=(
                f"Install the optional decoder package ({package}), or re export "
                f"the study using Explicit VR Little Endian."
            ),
            detail={"transfer_syntax": text, "name": name},
        )
    return GuardIssue(
        code=GuardCode.UNSUPPORTED_TRANSFER_SYNTAX.value,
        message=f"The transfer syntax {text} is not supported.",
        remedy="Re export the study using Explicit VR Little Endian, which every DICOM system can produce.",
        detail={"transfer_syntax": text},
    )


def inspect_batch(paths, limits: dict | None = None, destination=None) -> dict:
    """Inspect a set of files and apply the batch level limits as well."""
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    paths = [Path(p) for p in paths]
    summary = {
        "n_files": len(paths),
        "accepted": [],
        "rejected": [],
        "total_bytes": 0,
        "batch_issues": [],
    }

    if len(paths) > limits["max_batch_files"]:
        summary["batch_issues"].append(
            GuardIssue(
                GuardCode.BATCH_TOO_LARGE.value,
                f"This selection contains {len(paths)} files, above the batch limit of {limits['max_batch_files']}.",
                "Import in smaller batches. An administrator can raise the limit in Preferences, Import.",
            ).to_dict()
        )
        return summary

    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            continue
    summary["total_bytes"] = total

    if total > limits["max_batch_bytes"]:
        summary["batch_issues"].append(
            GuardIssue(
                GuardCode.BATCH_TOO_LARGE.value,
                f"This selection totals {human_bytes(total)}, above the batch limit of {human_bytes(limits['max_batch_bytes'])}.",
                "Import in smaller batches.",
            ).to_dict()
        )
        return summary

    if destination is not None:
        free = free_disk_bytes(Path(destination))
        needed = int(total * limits["working_copy_factor"]) + limits["disk_headroom_bytes"]
        if free is not None and free < needed:
            summary["batch_issues"].append(
                GuardIssue(
                    GuardCode.INSUFFICIENT_DISK.value,
                    f"This import needs about {human_bytes(needed)} of free space and only {human_bytes(free)} is available.",
                    "Free space, import fewer files at a time, or move the ARIA data folder to a larger drive.",
                ).to_dict()
            )
            return summary

    for p in paths:
        r = inspect_file(p, limits, destination)
        (summary["accepted"] if r.accepted else summary["rejected"]).append(r)
    return summary
