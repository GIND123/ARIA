"""The in memory image, and the display pipeline built on top of it.

The separation this module enforces is the one acceptance criterion AC 003
tests: the stored pixels and the annotation coordinate space never change, and
every display control acts only on a lookup applied at draw time.

``ImageData.pixels`` is the original stored array. Nothing in the viewer writes
to it. Windowing, inversion, contrast, brightness and the enhancement filters
all produce a separate 8 bit display array, and the settings that produced it
are saved apart from the pixels (FR 012).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.models import SourceImage


@dataclass
class DisplaySettings:
    """Non destructive display state, saved separately from image pixels."""

    window_centre: float = 0.0
    window_width: float = 1.0
    invert: bool = False
    brightness: float = 0.0       # -1.0 to 1.0, added after windowing
    contrast: float = 1.0         # 0.1 to 4.0, multiplied around mid grey
    gamma: float = 1.0            # 0.1 to 4.0
    filter_name: str = "none"
    filter_strength: float = 0.5
    show_original_pixels: bool = False

    def to_dict(self) -> dict:
        return {
            "window_centre": self.window_centre,
            "window_width": self.window_width,
            "invert": self.invert,
            "brightness": self.brightness,
            "contrast": self.contrast,
            "gamma": self.gamma,
            "filter_name": self.filter_name,
            "filter_strength": self.filter_strength,
            "show_original_pixels": self.show_original_pixels,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "DisplaySettings":
        if not data:
            return cls()
        allowed = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def copy(self) -> "DisplaySettings":
        return DisplaySettings(**self.to_dict())


@dataclass
class ImageData:
    """Decoded pixels plus everything needed to display and measure them."""

    #: Stored pixel values exactly as decoded, before any rescale is applied.
    pixels: np.ndarray = field(default_factory=lambda: np.zeros((1, 1), dtype=np.uint8))
    meta: SourceImage = field(default_factory=SourceImage)
    #: Original colour channels when the source carried them. Kept so the
    #: conversion to grayscale stays display only (FR 006).
    original_channels: np.ndarray | None = None
    #: Cache of the modality value array, computed on first use.
    _modality_cache: np.ndarray | None = field(default=None, repr=False, compare=False)

    # -- shape ---------------------------------------------------------------

    @property
    def rows(self) -> int:
        return int(self.pixels.shape[0])

    @property
    def columns(self) -> int:
        return int(self.pixels.shape[1])

    @property
    def shape(self) -> tuple:
        return (self.rows, self.columns)

    @property
    def is_monochrome1(self) -> bool:
        """MONOCHROME1 stores low values as white, so display inverts it."""
        return self.meta.photometric_interpretation.upper() == "MONOCHROME1"

    # -- value spaces --------------------------------------------------------

    def modality_values(self) -> np.ndarray:
        """Stored values after the rescale slope and intercept (FR 003).

        Measurement and texture read this array, not the display array, so a
        change to windowing can never move a measured value.
        """
        if self._modality_cache is not None:
            return self._modality_cache
        a = self.pixels
        slope = float(self.meta.rescale_slope or 1.0)
        intercept = float(self.meta.rescale_intercept or 0.0)
        if slope == 1.0 and intercept == 0.0:
            out = a
        else:
            out = a.astype(np.float32) * slope + intercept
        object.__setattr__(self, "_modality_cache", out)
        return out

    def analysis_array(self) -> np.ndarray:
        """Two dimensional array used for texture and profile analysis."""
        a = self.modality_values()
        if a.ndim == 3:
            return luminance(a)
        return a

    def value_at(self, x: float, y: float):
        """Stored and modality value under a cursor position, or None."""
        col = int(np.floor(x))
        row = int(np.floor(y))
        if not (0 <= row < self.rows and 0 <= col < self.columns):
            return None
        stored = self.pixels[row, col]
        modality = self.modality_values()[row, col]
        if isinstance(stored, np.ndarray):
            stored = tuple(int(v) for v in stored)
            modality = tuple(float(v) for v in np.atleast_1d(modality))
        else:
            stored = stored.item() if hasattr(stored, "item") else stored
            modality = float(modality)
        return {"row": row, "column": col, "stored": stored, "modality": modality}

    # -- windowing -----------------------------------------------------------

    def value_range(self) -> tuple:
        a = self.analysis_array()
        return float(a.min()), float(a.max())

    def default_window(self) -> tuple:
        """Window centre and width to open with.

        The values carried by the file are used when present (FR 003).
        Otherwise a robust range from the first and ninety ninth percentiles is
        used, which keeps a few saturated pixels from flattening the whole
        image.
        """
        if self.meta.window_centre is not None and self.meta.window_width:
            width = float(self.meta.window_width)
            if width > 0:
                return float(self.meta.window_centre), width
        a = self.analysis_array()
        sample = a if a.size <= 4_000_000 else a[:: max(1, a.shape[0] // 1000), :: max(1, a.shape[1] // 1000)]
        lo = float(np.percentile(sample, 1.0))
        hi = float(np.percentile(sample, 99.0))
        if hi - lo < 1e-6:
            lo, hi = self.value_range()
        if hi - lo < 1e-6:
            hi = lo + 1.0
        return (lo + hi) / 2.0, hi - lo

    def full_window(self) -> tuple:
        lo, hi = self.value_range()
        if hi - lo < 1e-6:
            hi = lo + 1.0
        return (lo + hi) / 2.0, hi - lo

    def default_display_settings(self) -> DisplaySettings:
        centre, width = self.default_window()
        return DisplaySettings(
            window_centre=centre,
            window_width=width,
            invert=self.is_monochrome1,
        )

    # -- display -------------------------------------------------------------

    def to_display(
        self, settings: DisplaySettings, region: tuple | None = None
    ) -> np.ndarray:
        """Render an 8 bit display image.

        ``region`` is an optional ``(row0, row1, col0, col1)`` slice so the
        viewer can render only the visible part of a large image.
        """
        a = self.analysis_array()
        if region is not None:
            r0, r1, c0, c1 = region
            a = a[max(0, r0) : min(self.rows, r1), max(0, c0) : min(self.columns, c1)]

        if settings.show_original_pixels:
            centre, width = self.full_window()
            out = apply_window(a, centre, width)
            if self.is_monochrome1:
                out = 255 - out
            return out

        out = apply_window(a, settings.window_centre, max(1e-6, settings.window_width))

        if settings.filter_name and settings.filter_name != "none":
            out = apply_filter(out, settings.filter_name, settings.filter_strength)

        if settings.contrast != 1.0 or settings.brightness != 0.0:
            f = out.astype(np.float32)
            f = (f - 127.5) * float(settings.contrast) + 127.5
            f += float(settings.brightness) * 127.5
            out = np.clip(f, 0, 255).astype(np.uint8)

        if settings.gamma and abs(settings.gamma - 1.0) > 1e-3:
            lut = np.clip(
                ((np.arange(256) / 255.0) ** (1.0 / float(settings.gamma))) * 255.0, 0, 255
            ).astype(np.uint8)
            out = lut[out]

        if settings.invert:
            out = 255 - out
        return out


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def luminance(rgb: np.ndarray) -> np.ndarray:
    """Convert colour channels to a single grayscale plane for display.

    Conversion is display only. The original channels stay on the ImageData and
    in the retained source file (FR 006).
    """
    a = np.asarray(rgb)
    if a.ndim == 2:
        return a
    if a.shape[2] >= 3:
        r = a[..., 0].astype(np.float32)
        g = a[..., 1].astype(np.float32)
        b = a[..., 2].astype(np.float32)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    return a[..., 0].astype(np.float32)


def apply_window(a: np.ndarray, centre: float, width: float) -> np.ndarray:
    """DICOM linear VOI transform to 8 bit.

    Follows the standard linear function: values at or below the low edge map to
    zero, values at or above the high edge map to 255.
    """
    width = max(1e-6, float(width))
    lo = float(centre) - width / 2.0
    scaled = (a.astype(np.float32) - lo) * (255.0 / width)
    return np.clip(scaled, 0, 255).astype(np.uint8)


def _box_blur_u8(a: np.ndarray, radius: int) -> np.ndarray:
    if radius < 1:
        return a.astype(np.float32)
    f = a.astype(np.float32)
    pad = np.pad(f, radius, mode="edge")
    integral = pad.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")
    h, w = f.shape
    k = 2 * radius + 1
    total = (
        integral[k:, k:]
        - integral[:-k, k:]
        - integral[k:, :-k]
        + integral[:-k, :-k]
    )
    return total[:h, :w] / (k * k)


def apply_filter(a: np.ndarray, name: str, strength: float) -> np.ndarray:
    """Optional enhancement filters. Non destructive by construction (FR 012).

    Each filter takes the 8 bit windowed image and returns another 8 bit image.
    The source pixels are never touched, and the filter choice is stored in the
    display settings rather than baked into anything.
    """
    strength = float(np.clip(strength, 0.0, 1.0))
    if name == "none" or strength <= 0:
        return a

    if name == "unsharp":
        blurred = _box_blur_u8(a, radius=2)
        amount = 0.2 + 1.8 * strength
        out = a.astype(np.float32) + amount * (a.astype(np.float32) - blurred)
        return np.clip(out, 0, 255).astype(np.uint8)

    if name == "edge_enhance":
        blurred = _box_blur_u8(a, radius=1)
        detail = a.astype(np.float32) - blurred
        out = a.astype(np.float32) + (1.0 + 3.0 * strength) * detail
        return np.clip(out, 0, 255).astype(np.uint8)

    if name == "smooth":
        radius = 1 + int(round(2 * strength))
        return np.clip(_box_blur_u8(a, radius), 0, 255).astype(np.uint8)

    if name == "equalise":
        hist = np.bincount(a.ravel(), minlength=256).astype(np.float64)
        cdf = hist.cumsum()
        if cdf[-1] <= 0:
            return a
        cdf = cdf / cdf[-1]
        lut = np.clip(cdf * 255.0, 0, 255).astype(np.uint8)
        full = lut[a]
        return _blend(a, full, strength)

    if name == "clahe":
        return _blend(a, _clahe(a), strength)

    if name == "cortex_boost":
        # A mild local contrast lift tuned for reading the endosteal margin,
        # which is the judgement the cortical index depends on.
        local = _clahe(a, tiles=12, clip=2.5)
        blurred = _box_blur_u8(local, radius=1)
        detail = local.astype(np.float32) - blurred
        out = local.astype(np.float32) + 1.2 * detail
        return _blend(a, np.clip(out, 0, 255).astype(np.uint8), strength)

    return a


def _blend(base: np.ndarray, other: np.ndarray, t: float) -> np.ndarray:
    t = float(np.clip(t, 0.0, 1.0))
    return np.clip(
        base.astype(np.float32) * (1.0 - t) + other.astype(np.float32) * t, 0, 255
    ).astype(np.uint8)


def _clahe(a: np.ndarray, tiles: int = 8, clip: float = 3.0) -> np.ndarray:
    """Contrast limited adaptive histogram equalisation.

    Implemented directly so the display pipeline has no optional dependency.
    Tile histograms are clipped and redistributed, then the per tile mappings
    are interpolated bilinearly, which is what avoids visible tile edges.
    """
    h, w = a.shape
    tiles = max(2, int(tiles))
    ty = max(1, h // tiles)
    tx = max(1, w // tiles)
    ny = max(1, int(np.ceil(h / ty)))
    nx = max(1, int(np.ceil(w / tx)))

    maps = np.zeros((ny, nx, 256), dtype=np.float32)
    for i in range(ny):
        for j in range(nx):
            block = a[i * ty : min(h, (i + 1) * ty), j * tx : min(w, (j + 1) * tx)]
            if block.size == 0:
                maps[i, j] = np.arange(256)
                continue
            hist = np.bincount(block.ravel(), minlength=256).astype(np.float32)
            limit = max(1.0, clip * block.size / 256.0)
            excess = np.maximum(hist - limit, 0).sum()
            hist = np.minimum(hist, limit) + excess / 256.0
            cdf = hist.cumsum()
            cdf /= max(1e-6, cdf[-1])
            maps[i, j] = cdf * 255.0

    ys = np.arange(h, dtype=np.float32)
    xs = np.arange(w, dtype=np.float32)
    fy = np.clip((ys - ty / 2.0) / ty, 0, ny - 1)
    fx = np.clip((xs - tx / 2.0) / tx, 0, nx - 1)
    y0 = np.floor(fy).astype(np.int32)
    x0 = np.floor(fx).astype(np.int32)
    y1 = np.minimum(y0 + 1, ny - 1)
    x1 = np.minimum(x0 + 1, nx - 1)
    wy = (fy - y0)[:, None].astype(np.float32)
    wx = (fx - x0)[None, :].astype(np.float32)

    idx = a
    g00 = maps[y0[:, None], x0[None, :], idx]
    g01 = maps[y0[:, None], x1[None, :], idx]
    g10 = maps[y1[:, None], x0[None, :], idx]
    g11 = maps[y1[:, None], x1[None, :], idx]
    top = g00 * (1 - wx) + g01 * wx
    bottom = g10 * (1 - wx) + g11 * wx
    out = top * (1 - wy) + bottom * wy
    return np.clip(out, 0, 255).astype(np.uint8)


#: Filters offered in the viewer, with the label shown in the toolbar.
AVAILABLE_FILTERS = (
    ("none", "No filter"),
    ("unsharp", "Unsharp mask"),
    ("edge_enhance", "Edge enhance"),
    ("smooth", "Smooth"),
    ("equalise", "Histogram equalise"),
    ("clahe", "Adaptive equalise"),
    ("cortex_boost", "Cortex readability"),
)
