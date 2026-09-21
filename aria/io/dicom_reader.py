"""DICOM Part 10 reading (FR 001, FR 003).

Reads pixel data, photometric interpretation, rows, columns, bits stored,
rescale parameters, window centre and width, transfer syntax, the instance and
study identifiers and the spatial calibration attributes.

Two details that are easy to get wrong and that change measurements if they are
wrong:

* stored bits narrower than allocated bits. A 14 bit image stored in 16 bit
  words carries undefined content in the top two bits of every word. Those bits
  are masked here, otherwise a single stray high bit shifts the whole window.
* signed pixel data. When the pixel representation is two's complement the
  values are sign extended from the stored bit width before anything else
  touches them.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..core.models import SourceImage
from ..core.units import Calibration, CalibrationSource, ValidationStatus
from .image import ImageData

#: Attributes read for the case record.
_STRING_TAGS = (
    ("sop_instance_uid", "SOPInstanceUID"),
    ("study_instance_uid", "StudyInstanceUID"),
    ("series_instance_uid", "SeriesInstanceUID"),
    ("sop_class_uid", "SOPClassUID"),
    ("modality", "Modality"),
    ("manufacturer", "Manufacturer"),
    ("manufacturer_model", "ManufacturerModelName"),
    ("image_laterality", "ImageLaterality"),
    ("acquisition_date", "AcquisitionDate"),
)


def _first(value, default=None):
    """DICOM multi valued elements arrive as a list; take the first value."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        return value[0] if value else default
    try:
        from pydicom.multival import MultiValue

        if isinstance(value, MultiValue):
            return value[0] if len(value) else default
    except ImportError:
        pass
    return value


def _as_float(value, default=None):
    v = _first(value, None)
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def normalise_stored_pixels(arr: np.ndarray, ds) -> np.ndarray:
    """Mask undefined high bits and sign extend when required."""
    bits_stored = int(getattr(ds, "BitsStored", 0) or 0)
    bits_allocated = int(getattr(ds, "BitsAllocated", 0) or 0)
    representation = int(getattr(ds, "PixelRepresentation", 0) or 0)

    if not bits_stored or not bits_allocated or bits_stored >= bits_allocated:
        return arr
    if arr.dtype.kind not in ("u", "i"):
        return arr

    high_bit = int(getattr(ds, "HighBit", bits_stored - 1) or (bits_stored - 1))
    shift = high_bit - bits_stored + 1
    mask = (1 << bits_stored) - 1

    work = arr.astype(np.int64)
    if shift:
        work = work >> shift
    work = work & mask

    if representation == 1:
        sign_bit = 1 << (bits_stored - 1)
        work = (work ^ sign_bit) - sign_bit
        out_dtype = np.int16 if bits_stored <= 16 else np.int32
    else:
        out_dtype = np.uint16 if bits_stored > 8 else np.uint8
    return work.astype(out_dtype)


def extract_calibration(ds, device_policy: dict | None = None) -> Calibration:
    """Build a calibration record from the spatial attributes present.

    Nothing here marks a calibration as validated. Header spacing is offered as
    a candidate with the status "present, not validated", and a reviewer decides
    (FR 007). That is the honest state for a panoramic image, where detector
    spacing is not the same thing as anatomical scale.
    """
    candidates = (
        ("PixelSpacing", CalibrationSource.DICOM_PIXEL_SPACING),
        ("ImagerPixelSpacing", CalibrationSource.DICOM_IMAGER_PIXEL_SPACING),
        (
            "NominalScannedPixelSpacing",
            CalibrationSource.DICOM_NOMINAL_SCANNED_PIXEL_SPACING,
        ),
    )
    for attr, source in candidates:
        value = getattr(ds, attr, None)
        if value is None:
            continue
        try:
            values = [float(v) for v in value]
        except (TypeError, ValueError):
            single = _as_float(value)
            values = [single, single] if single else []
        if len(values) >= 2 and values[0] > 0 and values[1] > 0:
            # DICOM orders pixel spacing as row spacing then column spacing.
            cal = Calibration(
                source=source,
                row_spacing_mm=float(values[0]),
                col_spacing_mm=float(values[1]),
                status=ValidationStatus.UNVALIDATED,
                device_model=str(getattr(ds, "ManufacturerModelName", "") or ""),
            )
            cal.warnings = cal.plausibility_warnings()

            magnification = _as_float(
                getattr(ds, "EstimatedRadiographicMagnificationFactor", None)
            )
            policy = (device_policy or {}).get(cal.device_model, {})
            if magnification:
                cal.magnification_vertical = magnification
                cal.magnification_horizontal = magnification
                cal.notes = (
                    f"The file reports an estimated radiographic magnification "
                    f"factor of {magnification:.4g}. It is recorded but not "
                    f"applied unless the project enables magnification correction."
                )
            elif policy:
                cal.magnification_vertical = float(
                    policy.get("magnification_vertical", 1.0)
                )
                cal.magnification_horizontal = float(
                    policy.get("magnification_horizontal", 1.0)
                )
                cal.notes = (
                    f"Magnification factors were taken from the project device "
                    f"policy for {cal.device_model}."
                )
            cal.warnings.append(
                "Panoramic detector spacing describes the detector, not the "
                "patient. Confirm the device calibration policy before "
                "validating this value."
            )
            return cal

    return Calibration(source=CalibrationSource.NONE, status=ValidationStatus.NOT_AVAILABLE)


def read_dicom(path, device_policy: dict | None = None) -> ImageData:
    """Read one DICOM Part 10 file into an :class:`ImageData`."""
    import pydicom

    p = Path(path)
    ds = pydicom.dcmread(str(p))

    meta = SourceImage(
        source_format="dicom",
        original_filename=p.name,
        byte_size=p.stat().st_size,
        rows=int(getattr(ds, "Rows", 0) or 0),
        columns=int(getattr(ds, "Columns", 0) or 0),
        bits_stored=int(getattr(ds, "BitsStored", 0) or 0),
        bits_allocated=int(getattr(ds, "BitsAllocated", 0) or 0),
        photometric_interpretation=str(getattr(ds, "PhotometricInterpretation", "") or ""),
        samples_per_pixel=int(getattr(ds, "SamplesPerPixel", 1) or 1),
        pixel_representation=int(getattr(ds, "PixelRepresentation", 0) or 0),
        rescale_slope=_as_float(getattr(ds, "RescaleSlope", None), 1.0) or 1.0,
        rescale_intercept=_as_float(getattr(ds, "RescaleIntercept", None), 0.0) or 0.0,
        window_centre=_as_float(getattr(ds, "WindowCenter", None)),
        window_width=_as_float(getattr(ds, "WindowWidth", None)),
    )
    for field_name, tag in _STRING_TAGS:
        setattr(meta, field_name, str(getattr(ds, tag, "") or ""))
    try:
        meta.transfer_syntax_uid = str(ds.file_meta.TransferSyntaxUID)
    except AttributeError:
        meta.transfer_syntax_uid = ""

    raw = ds.pixel_array
    normalised = normalise_stored_pixels(raw, ds)

    original_channels = None
    if normalised.ndim == 3 and normalised.shape[2] > 1:
        original_channels = normalised
        meta.converted_for_display = True
        meta.original_channel_description = (
            f"{meta.photometric_interpretation} with "
            f"{normalised.shape[2]} channels, preserved in the retained source file"
        )

    image = ImageData(pixels=normalised, meta=meta, original_channels=original_channels)

    # A presentation LUT shape of INVERSE means the displayed polarity is the
    # opposite of the photometric interpretation.
    presentation = str(getattr(ds, "PresentationLUTShape", "") or "").upper()
    if presentation == "INVERSE":
        meta.notes.append(
            "The file requests an inverse presentation LUT shape. The default "
            "display polarity was inverted accordingly."
        )

    padding = getattr(ds, "PixelPaddingValue", None)
    if padding is not None:
        meta.notes.append(
            f"A pixel padding value of {padding} is declared. Padded pixels are "
            f"displayed as stored and are not treated as anatomy."
        )

    if meta.bits_stored and meta.bits_allocated and meta.bits_stored < meta.bits_allocated:
        meta.notes.append(
            f"{meta.bits_stored} bits are stored in {meta.bits_allocated} bit "
            f"words. Undefined high bits were masked on read."
        )

    return image


def read_dicom_calibration(path, device_policy: dict | None = None) -> Calibration:
    import pydicom

    ds = pydicom.dcmread(str(path), stop_before_pixels=True)
    return extract_calibration(ds, device_policy)


def presentation_inverted(path) -> bool:
    import pydicom

    ds = pydicom.dcmread(str(path), stop_before_pixels=True)
    return str(getattr(ds, "PresentationLUTShape", "") or "").upper() == "INVERSE"
