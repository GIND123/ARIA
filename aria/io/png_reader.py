"""PNG reading (FR 001, FR 006).

Eight bit and sixteen bit grayscale content is read at its native depth. A
sixteen bit file is never quietly reduced to eight bits, because the whole point
of a sixteen bit export is the extra tonal separation across the cortical
margin.

Colour PNG files are converted for display only. The colour channels are kept on
the image object and the original file is retained unchanged, so nothing about
the source is lost by importing it.

A PNG carries no clinical calibration. Some files carry a physical pixel
dimension chunk, which is offered as an unvalidated candidate and never as a
validated scale, because that chunk usually describes a print size rather than
an imaging geometry.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import numpy as np

from ..core.models import SourceImage
from ..core.units import Calibration, CalibrationSource, ValidationStatus
from .image import ImageData


def read_phys_chunk(path) -> dict | None:
    """Read the physical pixel dimensions chunk if the file carries one."""
    try:
        with open(path, "rb") as fh:
            if fh.read(8) != b"\x89PNG\r\n\x1a\n":
                return None
            while True:
                header = fh.read(8)
                if len(header) < 8:
                    return None
                length, chunk_type = struct.unpack(">I4s", header)
                if chunk_type == b"pHYs":
                    data = fh.read(length)
                    if len(data) < 9:
                        return None
                    x_ppu, y_ppu, unit = struct.unpack(">IIB", data[:9])
                    return {
                        "x_pixels_per_unit": int(x_ppu),
                        "y_pixels_per_unit": int(y_ppu),
                        "unit": "metre" if unit == 1 else "unknown",
                    }
                if chunk_type in (b"IDAT", b"IEND"):
                    return None
                fh.seek(length + 4, 1)
    except OSError:
        return None


def calibration_from_phys(path) -> Calibration:
    """Offer the physical pixel chunk as an unvalidated calibration candidate."""
    phys = read_phys_chunk(path)
    if not phys or phys["unit"] != "metre":
        return Calibration(
            source=CalibrationSource.NONE, status=ValidationStatus.NOT_AVAILABLE
        )
    x_ppu = phys["x_pixels_per_unit"]
    y_ppu = phys["y_pixels_per_unit"]
    if x_ppu <= 0 or y_ppu <= 0:
        return Calibration(
            source=CalibrationSource.NONE, status=ValidationStatus.NOT_AVAILABLE
        )
    cal = Calibration(
        source=CalibrationSource.MANUAL_KNOWN_LENGTH,
        row_spacing_mm=1000.0 / y_ppu,
        col_spacing_mm=1000.0 / x_ppu,
        status=ValidationStatus.UNVALIDATED,
        reference_description="PNG physical pixel dimensions chunk",
        notes=(
            "This scale comes from the PNG physical pixel dimensions chunk, "
            "which usually describes an intended print size rather than the "
            "imaging geometry. Validate it against a known length before using "
            "millimetre values."
        ),
    )
    cal.warnings = cal.plausibility_warnings()
    cal.warnings.append(
        "A PNG carries no imaging calibration. Complete a manual calibration "
        "with a known length before relying on millimetre values."
    )
    return cal


def read_png(path) -> ImageData:
    """Read a PNG file into an :class:`ImageData`."""
    from PIL import Image

    p = Path(path)
    with Image.open(p) as im:
        im.load()
        mode = im.mode
        original_mode = mode
        original_channels = None

        if mode == "P":
            # An indexed image is expanded so it can be displayed. The palette
            # is what the file actually stores, so the expansion is recorded.
            im = im.convert("RGB")
            mode = "RGB"

        if mode in ("RGB", "RGBA"):
            rgb = np.asarray(im, dtype=np.uint8)
            original_channels = rgb
            from .image import luminance

            pixels = np.rint(luminance(rgb[..., :3])).astype(np.uint8)
            bits = 8
            samples = rgb.shape[2]
            photometric = "RGB" if mode == "RGB" else "RGBA"
        elif mode in ("I;16", "I;16B", "I;16L", "I"):
            pixels = np.asarray(im, dtype=np.uint16 if mode != "I" else np.int32)
            if mode == "I":
                pixels = np.clip(pixels, 0, 65535).astype(np.uint16)
            bits = 16
            samples = 1
            photometric = "MONOCHROME2"
        elif mode == "L":
            pixels = np.asarray(im, dtype=np.uint8)
            bits = 8
            samples = 1
            photometric = "MONOCHROME2"
        elif mode == "LA":
            la = np.asarray(im, dtype=np.uint8)
            original_channels = la
            pixels = la[..., 0]
            bits = 8
            samples = 2
            photometric = "MONOCHROME2"
        elif mode == "1":
            pixels = (np.asarray(im, dtype=bool).astype(np.uint8)) * 255
            bits = 8
            samples = 1
            photometric = "MONOCHROME2"
        else:
            im = im.convert("L")
            pixels = np.asarray(im, dtype=np.uint8)
            bits = 8
            samples = 1
            photometric = "MONOCHROME2"

    meta = SourceImage(
        source_format="png",
        original_filename=p.name,
        byte_size=p.stat().st_size,
        rows=int(pixels.shape[0]),
        columns=int(pixels.shape[1]),
        bits_stored=bits,
        bits_allocated=bits,
        photometric_interpretation=photometric,
        samples_per_pixel=samples,
        pixel_representation=0,
        transfer_syntax_uid="",
        rescale_slope=1.0,
        rescale_intercept=0.0,
    )

    if original_channels is not None:
        meta.converted_for_display = True
        meta.original_channel_description = (
            f"{original_mode} PNG with {original_channels.shape[2]} channels. "
            f"Grayscale conversion is applied for display only and the original "
            f"channels are preserved."
        )
        meta.notes.append(
            "Colour channels were converted to grayscale for display. The "
            "retained source file is unchanged."
        )
    if original_mode == "P":
        meta.notes.append(
            "The source is an indexed colour PNG. The palette was expanded for "
            "display only."
        )
    if bits == 16:
        meta.notes.append(
            "Sixteen bit grayscale content was read at its native depth without "
            "reduction."
        )

    return ImageData(pixels=pixels, meta=meta, original_channels=original_channels)


def read_image(path, device_policy: dict | None = None) -> tuple:
    """Read either supported format and return ``(image, calibration)``."""
    from .guards import sniff_format

    p = Path(path)
    kind = sniff_format(p)
    if kind == "dicom":
        from .dicom_reader import read_dicom, read_dicom_calibration

        return read_dicom(p, device_policy), read_dicom_calibration(p, device_policy)
    if kind == "png":
        return read_png(p), calibration_from_phys(p)
    raise ValueError(
        f"{p.name} is neither a DICOM Part 10 file nor a PNG image. "
        f"ARIA reads those two formats."
    )
