"""Texture features computed from annotated regions (FR 030).

These are derived features. They are computed after annotation from the region
the annotator drew, and they are never presented as something an annotator
types in as ground truth.

Everything here is implemented directly on numpy arrays rather than pulled from
an image processing toolkit. That is deliberate: the exported feature values
must be reproducible from the exported region and the recorded parameters
alone, without a reader having to match a third party library version.

Region size matters. Published work shows fractal dimension values shift with
the size of the analysed region, so the region size is a recorded parameter of
every result rather than an implementation detail, and the project schema fixes
it for a study.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..version import TEXTURE_VERSION

#: Box sizes used by the box counting step. This ladder follows the sequence
#: used in the dental fractal analysis literature.
DEFAULT_BOX_SIZES = (2, 3, 4, 6, 8, 12, 16, 32, 64)

#: Kernel width of the mean filter in the White and Rudolph preprocessing
#: sequence, expressed in pixels.
DEFAULT_BLUR_KERNEL = 45


@dataclass
class TextureResult:
    """Feature values plus every parameter needed to reproduce them."""

    region_id: str = ""
    region_class: str = ""
    side: str = ""
    #: Region geometry in original image pixels.
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    n_pixels: int = 0
    features: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    texture_version: str = TEXTURE_VERSION
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "region_id": self.region_id,
            "region_class": self.region_class,
            "side": self.side,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "n_pixels": self.n_pixels,
            "features": dict(self.features),
            "parameters": dict(self.parameters),
            "texture_version": self.texture_version,
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def quantise(region: np.ndarray, levels: int = 32) -> np.ndarray:
    """Linearly quantise a region to ``levels`` grey levels.

    A constant region maps to level zero rather than producing a division by
    zero, and the caller is expected to note the degenerate case.
    """
    a = np.asarray(region, dtype=np.float64)
    lo = float(a.min())
    hi = float(a.max())
    if hi - lo < 1e-12:
        return np.zeros(a.shape, dtype=np.int32)
    scaled = (a - lo) / (hi - lo) * (levels - 1)
    return np.clip(np.rint(scaled), 0, levels - 1).astype(np.int32)


def box_blur(a: np.ndarray, k: int) -> np.ndarray:
    """Mean filter with a square kernel, computed from an integral image.

    Edges use the mean of the pixels that actually fall inside the region, so
    the filtered image does not darken at the border the way zero padding would.
    """
    if k < 2:
        return a.astype(np.float64)
    a = np.asarray(a, dtype=np.float64)
    r = k // 2
    padded = np.pad(a, r + 1, mode="edge")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    integral = np.pad(integral, ((1, 0), (1, 0)), mode="constant")

    h, w = a.shape
    ys = np.arange(h) + r + 1
    xs = np.arange(w) + r + 1
    y0 = (ys - r)[:, None]
    y1 = (ys + r + 1)[:, None]
    x0 = (xs - r)[None, :]
    x1 = (xs + r + 1)[None, :]
    total = (
        integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
    )
    count = (y1 - y0) * (x1 - x0)
    return total / count


def _shift_or(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    out = np.zeros_like(mask)
    h, w = mask.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    yd0, yd1 = max(0, -dy), min(h, h - dy)
    xd0, xd1 = max(0, -dx), min(w, w - dx)
    if ys1 > ys0 and xs1 > xs0:
        out[ys0:ys1, xs0:xs1] = mask[yd0:yd1, xd0:xd1]
    return out


def dilate(mask: np.ndarray) -> np.ndarray:
    """Binary dilation with a three by three structuring element."""
    out = mask.copy()
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            out |= _shift_or(mask, dy, dx)
    return out


def erode(mask: np.ndarray) -> np.ndarray:
    """Binary erosion with a three by three structuring element."""
    return ~dilate(~mask)


def _neighbours(padded: np.ndarray):
    """The eight neighbours of every interior pixel, in Zhang and Suen order."""
    p2 = padded[0:-2, 1:-1]
    p3 = padded[0:-2, 2:]
    p4 = padded[1:-1, 2:]
    p5 = padded[2:, 2:]
    p6 = padded[2:, 1:-1]
    p7 = padded[2:, 0:-2]
    p8 = padded[1:-1, 0:-2]
    p9 = padded[0:-2, 0:-2]
    return p2, p3, p4, p5, p6, p7, p8, p9


def skeletonise(mask: np.ndarray, max_iterations: int = 100) -> np.ndarray:
    """Zhang and Suen thinning, vectorised.

    The trabecular pattern is reduced to a one pixel wide skeleton before box
    counting, which is the step that makes the resulting dimension describe the
    structure of the pattern rather than the amount of ink in it.
    """
    img = np.asarray(mask, dtype=bool).copy()
    for _ in range(max_iterations):
        changed = False
        for step in (0, 1):
            padded = np.pad(img, 1, mode="constant", constant_values=False)
            p2, p3, p4, p5, p6, p7, p8, p9 = _neighbours(padded)
            seq = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            b = sum(p.astype(np.int8) for p in seq[:8])
            transitions = sum(
                ((~seq[i]) & seq[i + 1]).astype(np.int8) for i in range(8)
            )
            if step == 0:
                c1 = p2 & p4 & p6
                c2 = p4 & p6 & p8
            else:
                c1 = p2 & p4 & p8
                c2 = p2 & p6 & p8
            remove = img & (b >= 2) & (b <= 6) & (transitions == 1) & (~c1) & (~c2)
            if remove.any():
                img &= ~remove
                changed = True
        if not changed:
            break
    return img


# ---------------------------------------------------------------------------
# Fractal dimension
# ---------------------------------------------------------------------------


def box_count_dimension(
    binary: np.ndarray, box_sizes=DEFAULT_BOX_SIZES
) -> tuple:
    """Box counting dimension of a binary pattern.

    Returns ``(dimension, r_squared, detail)``. The dimension is the negative
    slope of log(count) against log(size), fitted by least squares.
    """
    h, w = binary.shape
    usable = [s for s in box_sizes if s <= min(h, w)]
    if len(usable) < 3:
        return float("nan"), float("nan"), {"box_sizes": [], "counts": []}

    counts = []
    for s in usable:
        ph = int(math.ceil(h / s) * s)
        pw = int(math.ceil(w / s) * s)
        padded = np.zeros((ph, pw), dtype=bool)
        padded[:h, :w] = binary
        blocks = padded.reshape(ph // s, s, pw // s, s)
        occupied = blocks.any(axis=(1, 3)).sum()
        counts.append(int(occupied))

    valid = [(s, c) for s, c in zip(usable, counts) if c > 0]
    if len(valid) < 3:
        return float("nan"), float("nan"), {"box_sizes": usable, "counts": counts}

    xs = np.log(np.array([1.0 / s for s, _ in valid]))
    ys = np.log(np.array([c for _, c in valid], dtype=float))
    slope, intercept = np.polyfit(xs, ys, 1)
    predicted = slope * xs + intercept
    ss_res = float(((ys - predicted) ** 2).sum())
    ss_tot = float(((ys - ys.mean()) ** 2).sum())
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 1.0
    detail = {
        "box_sizes": [s for s, _ in valid],
        "counts": [c for _, c in valid],
        "intercept": float(intercept),
    }
    return float(slope), float(r2), detail


def fractal_dimension(
    region: np.ndarray,
    blur_kernel: int = DEFAULT_BLUR_KERNEL,
    box_sizes=DEFAULT_BOX_SIZES,
) -> dict:
    """Fractal dimension of a trabecular region.

    The preprocessing follows the sequence established for dental radiographs:
    mean filter the region, subtract the filtered image from the original, add a
    mid grey offset, threshold, erode, dilate, invert and skeletonise, then box
    count the skeleton. Each step is applied to the annotated region only.
    """
    a = np.asarray(region, dtype=np.float64)
    if a.size == 0:
        return {"fractal_dimension": float("nan"), "fd_r_squared": float("nan")}

    kernel = min(blur_kernel, max(3, min(a.shape) // 2 * 2 + 1))
    blurred = box_blur(a, kernel)
    difference = a - blurred
    # Shift to a mid grey so the threshold sits at the mean of the difference.
    offset = difference + 128.0
    binary = offset > 128.0
    binary = dilate(erode(binary))
    skeleton = skeletonise(~binary)

    dimension, r2, detail = box_count_dimension(skeleton, box_sizes)
    return {
        "fractal_dimension": dimension,
        "fd_r_squared": r2,
        "fd_box_sizes": detail.get("box_sizes", []),
        "fd_box_counts": detail.get("counts", []),
        "fd_skeleton_pixels": int(skeleton.sum()),
        "fd_blur_kernel": int(kernel),
    }


def differential_box_count_dimension(region: np.ndarray, box_sizes=(2, 3, 4, 6, 8, 12, 16)) -> dict:
    """Differential box counting on the grey surface.

    Reported alongside the binary skeleton dimension because it does not depend
    on a threshold, which makes it a useful cross check when a region is close
    to uniform.
    """
    a = np.asarray(region, dtype=np.float64)
    if a.size == 0:
        return {"dbc_dimension": float("nan")}
    lo, hi = float(a.min()), float(a.max())
    if hi - lo < 1e-12:
        return {"dbc_dimension": float("nan"), "dbc_note": "uniform region"}
    g = (a - lo) / (hi - lo) * 255.0
    h, w = g.shape
    usable = [s for s in box_sizes if s <= min(h, w)]
    xs, ys = [], []
    for s in usable:
        ph, pw = int(math.ceil(h / s) * s), int(math.ceil(w / s) * s)
        padded = np.full((ph, pw), np.nan)
        padded[:h, :w] = g
        blocks = padded.reshape(ph // s, s, pw // s, s)
        bmax = np.nanmax(blocks, axis=(1, 3))
        bmin = np.nanmin(blocks, axis=(1, 3))
        height = 255.0 * s / max(h, w)
        nr = np.floor(bmax / height) - np.floor(bmin / height) + 1
        total = float(np.nansum(nr))
        if total > 0:
            xs.append(math.log(1.0 / s))
            ys.append(math.log(total))
    if len(xs) < 3:
        return {"dbc_dimension": float("nan")}
    slope, _ = np.polyfit(np.array(xs), np.array(ys), 1)
    return {"dbc_dimension": float(slope), "dbc_box_sizes": usable}


# ---------------------------------------------------------------------------
# Grey level co-occurrence matrix
# ---------------------------------------------------------------------------

#: Offsets for zero, forty five, ninety and one hundred and thirty five degrees.
GLCM_OFFSETS = {
    "0": (0, 1),
    "45": (-1, 1),
    "90": (-1, 0),
    "135": (-1, -1),
}


def glcm_matrix(
    quantised: np.ndarray, levels: int, dy: int, dx: int, symmetric: bool = True
) -> np.ndarray:
    """Normalised co-occurrence matrix for one offset."""
    h, w = quantised.shape
    ys0, ys1 = max(0, -dy), min(h, h - dy)
    xs0, xs1 = max(0, -dx), min(w, w - dx)
    if ys1 <= ys0 or xs1 <= xs0:
        return np.zeros((levels, levels), dtype=np.float64)
    a = quantised[ys0:ys1, xs0:xs1].ravel()
    b = quantised[ys0 + dy : ys1 + dy, xs0 + dx : xs1 + dx].ravel()
    m = np.bincount(a * levels + b, minlength=levels * levels).reshape(levels, levels)
    m = m.astype(np.float64)
    if symmetric:
        m = m + m.T
    total = m.sum()
    return m / total if total > 0 else m


def glcm_features(matrix: np.ndarray) -> dict:
    """Haralick style descriptors from one normalised co-occurrence matrix."""
    levels = matrix.shape[0]
    i, j = np.mgrid[0:levels, 0:levels]
    diff = i - j
    p = matrix

    contrast = float((p * diff ** 2).sum())
    dissimilarity = float((p * np.abs(diff)).sum())
    homogeneity = float((p / (1.0 + diff ** 2)).sum())
    asm = float((p ** 2).sum())
    energy = float(math.sqrt(asm))
    with np.errstate(divide="ignore", invalid="ignore"):
        logp = np.where(p > 0, np.log(p), 0.0)
    entropy = float(-(p * logp).sum())

    mu_i = float((p * i).sum())
    mu_j = float((p * j).sum())
    sd_i = math.sqrt(float((p * (i - mu_i) ** 2).sum()))
    sd_j = math.sqrt(float((p * (j - mu_j) ** 2).sum()))
    if sd_i < 1e-12 or sd_j < 1e-12:
        correlation = float("nan")
    else:
        correlation = float((p * (i - mu_i) * (j - mu_j)).sum() / (sd_i * sd_j))

    return {
        "contrast": contrast,
        "dissimilarity": dissimilarity,
        "homogeneity": homogeneity,
        "asm": asm,
        "energy": energy,
        "entropy": entropy,
        "correlation": correlation,
    }


def glcm(region: np.ndarray, levels: int = 32, distance: int = 1) -> dict:
    """Co-occurrence features averaged over four directions, plus per direction
    values so a study can use a single orientation if its protocol requires it."""
    q = quantise(region, levels)
    out: dict = {}
    per_direction: dict = {}
    for name, (dy, dx) in GLCM_OFFSETS.items():
        m = glcm_matrix(q, levels, dy * distance, dx * distance)
        feats = glcm_features(m)
        per_direction[name] = feats
        for k, v in feats.items():
            out.setdefault(f"glcm_{k}_values", []).append(v)

    result: dict = {}
    for k in ("contrast", "dissimilarity", "homogeneity", "asm", "energy", "entropy", "correlation"):
        values = [v for v in out[f"glcm_{k}_values"] if not math.isnan(v)]
        result[f"glcm_{k}_mean"] = float(np.mean(values)) if values else float("nan")
        result[f"glcm_{k}_range"] = (
            float(np.max(values) - np.min(values)) if values else float("nan")
        )
    for name, feats in per_direction.items():
        for k, v in feats.items():
            result[f"glcm_{k}_{name}deg"] = v
    return result


# ---------------------------------------------------------------------------
# Local binary patterns
# ---------------------------------------------------------------------------


def lbp(region: np.ndarray, method: str = "uniform") -> dict:
    """Local binary pattern histogram over an eight pixel neighbourhood.

    The rotation invariant uniform mapping is used, which collapses the 256 raw
    codes to ten bins and is the variant normally reported for bone texture.
    """
    a = np.asarray(region, dtype=np.float64)
    if a.shape[0] < 3 or a.shape[1] < 3:
        return {"lbp_note": "region too small for local binary patterns"}

    centre = a[1:-1, 1:-1]
    offsets = [(-1, -1), (-1, 0), (-1, 1), (0, 1), (1, 1), (1, 0), (1, -1), (0, -1)]
    code = np.zeros(centre.shape, dtype=np.int32)
    for bit, (dy, dx) in enumerate(offsets):
        h, w = a.shape
        neighbour = a[1 + dy : h - 1 + dy, 1 + dx : w - 1 + dx]
        code |= ((neighbour >= centre).astype(np.int32) << bit)

    if method == "raw":
        hist = np.bincount(code.ravel(), minlength=256).astype(np.float64)
        hist /= max(1.0, hist.sum())
        return {f"lbp_raw_{i}": float(v) for i, v in enumerate(hist)}

    mapping = _riu2_mapping()
    mapped = mapping[code]
    hist = np.bincount(mapped.ravel(), minlength=10).astype(np.float64)
    hist /= max(1.0, hist.sum())
    out = {f"lbp_riu2_{i}": float(v) for i in range(10) for v in [hist[i]]}
    with np.errstate(divide="ignore", invalid="ignore"):
        nz = hist[hist > 0]
        out["lbp_entropy"] = float(-(nz * np.log(nz)).sum())
    out["lbp_uniformity"] = float((hist ** 2).sum())
    return out


_RIU2_CACHE: np.ndarray | None = None


def _riu2_mapping() -> np.ndarray:
    """Map each of the 256 codes to a rotation invariant uniform bin."""
    global _RIU2_CACHE
    if _RIU2_CACHE is not None:
        return _RIU2_CACHE
    mapping = np.zeros(256, dtype=np.int32)
    for code in range(256):
        bits = [(code >> b) & 1 for b in range(8)]
        transitions = sum(bits[b] != bits[(b + 1) % 8] for b in range(8))
        mapping[code] = sum(bits) if transitions <= 2 else 9
    _RIU2_CACHE = mapping
    return mapping


# ---------------------------------------------------------------------------
# Grey level run length
# ---------------------------------------------------------------------------

RUN_DIRECTIONS = {"0": (0, 1), "45": (-1, 1), "90": (1, 0), "135": (1, 1)}


def run_length_matrix(quantised: np.ndarray, levels: int, dy: int, dx: int) -> np.ndarray:
    """Run length matrix of shape ``(levels, max_run)`` for one direction."""
    h, w = quantised.shape
    max_run = max(h, w)
    matrix = np.zeros((levels, max_run + 1), dtype=np.int64)

    # Starting positions are the cells with no predecessor in this direction.
    starts = []
    for y in range(h):
        for x in range(w):
            py, px = y - dy, x - dx
            if not (0 <= py < h and 0 <= px < w):
                starts.append((y, x))

    for y0, x0 in starts:
        y, x = y0, x0
        current = quantised[y, x]
        length = 0
        while 0 <= y < h and 0 <= x < w:
            v = quantised[y, x]
            if v == current:
                length += 1
            else:
                matrix[current, min(length, max_run)] += 1
                current, length = v, 1
            y += dy
            x += dx
        if length:
            matrix[current, min(length, max_run)] += 1
    return matrix


def run_length_features(matrix: np.ndarray, n_pixels: int) -> dict:
    """Standard run length descriptors."""
    levels, max_run = matrix.shape
    runs = matrix.astype(np.float64)
    total_runs = runs.sum()
    if total_runs < 1:
        return {}

    j = np.arange(max_run, dtype=np.float64)[None, :]
    i = np.arange(levels, dtype=np.float64)[:, None]
    j_safe = np.where(j == 0, 1.0, j)
    i_safe = np.where(i == 0, 1.0, i)

    sre = float((runs / (j_safe ** 2)).sum() / total_runs)
    lre = float((runs * (j ** 2)).sum() / total_runs)
    gln = float(((runs.sum(axis=1)) ** 2).sum() / total_runs)
    rln = float(((runs.sum(axis=0)) ** 2).sum() / total_runs)
    rp = float(total_runs / max(1, n_pixels))
    lgre = float((runs / (i_safe ** 2)).sum() / total_runs)
    hgre = float((runs * (i ** 2)).sum() / total_runs)
    return {
        "short_run_emphasis": sre,
        "long_run_emphasis": lre,
        "grey_level_non_uniformity": gln,
        "run_length_non_uniformity": rln,
        "run_percentage": rp,
        "low_grey_level_run_emphasis": lgre,
        "high_grey_level_run_emphasis": hgre,
    }


def run_length(region: np.ndarray, levels: int = 16) -> dict:
    """Run length features averaged over four directions."""
    q = quantise(region, levels)
    n = int(q.size)
    collected: dict = {}
    for name, (dy, dx) in RUN_DIRECTIONS.items():
        m = run_length_matrix(q, levels, dy, dx)
        feats = run_length_features(m, n)
        for k, v in feats.items():
            collected.setdefault(k, []).append(v)
    out: dict = {}
    for k, values in collected.items():
        out[f"rl_{k}_mean"] = float(np.mean(values))
        out[f"rl_{k}_range"] = float(np.max(values) - np.min(values))
    return out


# ---------------------------------------------------------------------------
# First order statistics
# ---------------------------------------------------------------------------


def first_order(region: np.ndarray) -> dict:
    a = np.asarray(region, dtype=np.float64).ravel()
    if a.size == 0:
        return {}
    mean = float(a.mean())
    std = float(a.std())
    out = {
        "mean": mean,
        "std": std,
        "min": float(a.min()),
        "max": float(a.max()),
        "median": float(np.median(a)),
        "p10": float(np.percentile(a, 10)),
        "p90": float(np.percentile(a, 90)),
        "range": float(a.max() - a.min()),
    }
    if std > 1e-12:
        centred = (a - mean) / std
        out["skewness"] = float((centred ** 3).mean())
        out["kurtosis"] = float((centred ** 4).mean() - 3.0)
    else:
        out["skewness"] = float("nan")
        out["kurtosis"] = float("nan")
    hist, _ = np.histogram(a, bins=64)
    p = hist.astype(np.float64)
    p /= max(1.0, p.sum())
    nz = p[p > 0]
    out["histogram_entropy"] = float(-(nz * np.log(nz)).sum())
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def compute_region_features(
    image: np.ndarray,
    x: int,
    y: int,
    width: int,
    height: int,
    requested=("glcm", "fractal_dimension", "lbp", "run_length"),
    glcm_levels: int = 32,
    run_length_levels: int = 16,
    blur_kernel: int = DEFAULT_BLUR_KERNEL,
    box_sizes=DEFAULT_BOX_SIZES,
    region_id: str = "",
    region_class: str = "",
    side: str = "",
) -> TextureResult:
    """Compute the requested feature families over one rectangular region."""
    h, w = image.shape[:2]
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(w, int(x + width))
    y1 = min(h, int(y + height))

    result = TextureResult(
        region_id=region_id,
        region_class=region_class,
        side=side,
        x=x0,
        y=y0,
        width=max(0, x1 - x0),
        height=max(0, y1 - y0),
    )
    result.parameters = {
        "requested_features": list(requested),
        "glcm_levels": glcm_levels,
        "glcm_distance": 1,
        "glcm_angles_degrees": list(GLCM_OFFSETS.keys()),
        "run_length_levels": run_length_levels,
        "fd_blur_kernel": blur_kernel,
        "fd_box_sizes": list(box_sizes),
        "fd_method": "mean filter subtraction, threshold, erode, dilate, invert, skeletonise, box count",
        "lbp_method": "rotation invariant uniform, eight neighbours, radius one",
        "texture_version": TEXTURE_VERSION,
    }

    if x1 <= x0 or y1 <= y0:
        result.warnings.append("The region lies outside the image and was not analysed.")
        return result

    region = np.asarray(image[y0:y1, x0:x1], dtype=np.float64)
    result.n_pixels = int(region.size)

    if region.size < 16:
        result.warnings.append("The region is too small for stable texture features.")
    if float(region.max() - region.min()) < 1e-9:
        result.warnings.append("The region is uniform, so texture features are degenerate.")

    features: dict = {}
    features.update(first_order(region))
    if "glcm" in requested:
        features.update(glcm(region, levels=glcm_levels))
    if "fractal_dimension" in requested:
        features.update(fractal_dimension(region, blur_kernel=blur_kernel, box_sizes=box_sizes))
        features.update(differential_box_count_dimension(region))
    if "lbp" in requested:
        features.update(lbp(region))
    if "run_length" in requested:
        features.update(run_length(region, levels=run_length_levels))

    result.features = features
    return result
