"""Pure geometry used by the annotation tools and the measurement engine.

Everything in this module works in original image pixel coordinates (FR 013).
The viewer may zoom, pan, window or invert freely; none of that reaches these
functions, which is what keeps stored coordinates stable (FR 004, AC 003).

Coordinate convention
---------------------
A point is ``(x, y)`` where ``x`` is the column index and ``y`` is the row
index, both as floats with sub pixel precision. The origin is the top left
corner of the top left pixel, so the centre of pixel ``(row 0, column 0)`` is
``(0.5, 0.5)``.

The construction helpers here are deterministic geometry, not prediction. They
compute the line a protocol defines, for example the perpendicular through the
mental foramen centre, and hand it to the annotator as an editable proposal.
Nothing is committed without the annotator accepting it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

Point = tuple[float, float]

EPSILON = 1e-9


# ---------------------------------------------------------------------------
# Basic vector helpers
# ---------------------------------------------------------------------------


def euclidean(p: Point, q: Point) -> float:
    """Euclidean distance between two points in pixel units (FR 031)."""
    return math.hypot(q[0] - p[0], q[1] - p[1])


def vector(p: Point, q: Point) -> Point:
    return (q[0] - p[0], q[1] - p[1])


def magnitude(v: Point) -> float:
    return math.hypot(v[0], v[1])


def normalise(v: Point) -> Point:
    m = magnitude(v)
    if m < EPSILON:
        raise ValueError("Cannot normalise a zero length vector")
    return (v[0] / m, v[1] / m)


def dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def cross(a: Point, b: Point) -> float:
    return a[0] * b[1] - a[1] * b[0]


def perpendicular(v: Point) -> Point:
    """Rotate a vector by ninety degrees counter clockwise."""
    return (-v[1], v[0])


def angle_between(a: Point, b: Point) -> float:
    """Unsigned angle in radians between two direction vectors."""
    ma, mb = magnitude(a), magnitude(b)
    if ma < EPSILON or mb < EPSILON:
        raise ValueError("Cannot take the angle of a zero length vector")
    c = max(-1.0, min(1.0, dot(a, b) / (ma * mb)))
    return math.acos(c)


def add(p: Point, v: Point, scale: float = 1.0) -> Point:
    return (p[0] + v[0] * scale, p[1] + v[1] * scale)


def midpoint(p: Point, q: Point) -> Point:
    return ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0)


def bisector(d1: Point, d2: Point) -> Point:
    """Unit vector bisecting two directions (FR 024, gonial index axis).

    Both inputs are normalised first so the bisector is independent of the
    lengths of the tangents the caller measured them from. When the directions
    are opposed the bisector is degenerate, and the perpendicular to the first
    direction is returned instead so the caller still gets a usable axis.
    """
    u1 = normalise(d1)
    u2 = normalise(d2)
    s = (u1[0] + u2[0], u1[1] + u2[1])
    if magnitude(s) < 1e-6:
        return perpendicular(u1)
    return normalise(s)


# ---------------------------------------------------------------------------
# Polyline operations
# ---------------------------------------------------------------------------


def polyline_length(points: Sequence[Point]) -> float:
    return sum(euclidean(points[i], points[i + 1]) for i in range(len(points) - 1))


def closest_point_on_segment(p: Point, a: Point, b: Point) -> tuple[Point, float]:
    """Return the closest point on segment ``ab`` to ``p`` and its parameter t."""
    ab = vector(a, b)
    denom = dot(ab, ab)
    if denom < EPSILON:
        return a, 0.0
    t = max(0.0, min(1.0, dot(vector(a, p), ab) / denom))
    return (a[0] + ab[0] * t, a[1] + ab[1] * t), t


def closest_point_on_polyline(
    p: Point, points: Sequence[Point]
) -> tuple[Point, int, float]:
    """Closest point on a polyline.

    Returns the point, the index of the segment it lies on and the parameter
    along that segment.
    """
    if len(points) == 1:
        return points[0], 0, 0.0
    best: tuple[Point, int, float] = (points[0], 0, 0.0)
    best_d = float("inf")
    for i in range(len(points) - 1):
        q, t = closest_point_on_segment(p, points[i], points[i + 1])
        d = euclidean(p, q)
        if d < best_d:
            best_d = d
            best = (q, i, t)
    return best


def distance_to_polyline(p: Point, points: Sequence[Point]) -> float:
    return euclidean(p, closest_point_on_polyline(p, points)[0])


def resample_polyline(points: Sequence[Point], spacing: float) -> list[Point]:
    """Resample a polyline to approximately uniform spacing.

    Used for contour comparison and for building cortical masks, where uneven
    click spacing would otherwise bias the result.
    """
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    if len(points) < 2:
        return [tuple(p) for p in points]  # type: ignore[misc]
    out: list[Point] = [tuple(points[0])]  # type: ignore[list-item]
    # Distance still to travel before the next sample is emitted.
    remaining = spacing
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg = euclidean(a, b)
        if seg < EPSILON:
            continue
        d = normalise(vector(a, b))
        pos = 0.0
        while pos + remaining <= seg + EPSILON:
            pos += remaining
            out.append(add(a, d, pos))
            remaining = spacing
        remaining -= seg - pos
    last: Point = tuple(points[-1])  # type: ignore[assignment]
    if euclidean(out[-1], last) > EPSILON:
        out.append(last)
    return out


def tangent_at(
    points: Sequence[Point], anchor: Point, window_px: float = 40.0
) -> Point:
    """Estimate the local tangent direction of a polyline near ``anchor``.

    A total least squares line is fitted to the vertices and densified samples
    that lie within ``window_px`` of the anchor, which is far more stable than
    taking the direction of the single nearest segment when the annotator has
    clicked unevenly. The returned direction is oriented along increasing
    polyline arc length so that side handling stays predictable.
    """
    if len(points) < 2:
        raise ValueError("A tangent needs at least two vertices")

    dense = densify_polyline(points, max_step=max(2.0, window_px / 12.0))
    arr = np.asarray(dense, dtype=float)
    a = np.asarray(anchor, dtype=float)
    d = np.linalg.norm(arr - a, axis=1)
    sel = arr[d <= window_px]
    if len(sel) < 2:
        order = np.argsort(d)
        sel = arr[order[: min(len(arr), 5)]]
    if len(sel) < 2:
        return normalise(vector(points[0], points[-1]))

    centred = sel - sel.mean(axis=0)
    # Principal direction via singular value decomposition.
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    direction = (float(vt[0, 0]), float(vt[0, 1]))
    # Orient along increasing arc length.
    flow = vector(tuple(sel[0]), tuple(sel[-1]))
    if dot(direction, flow) < 0:
        direction = (-direction[0], -direction[1])
    return normalise(direction)


def densify_polyline(points: Sequence[Point], max_step: float = 2.0) -> list[Point]:
    """Insert intermediate vertices so no segment is longer than ``max_step``."""
    if len(points) < 2:
        return list(points)
    out: list[Point] = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        seg = euclidean(a, b)
        out.append(a)
        if seg > max_step:
            n = int(math.ceil(seg / max_step))
            d = normalise(vector(a, b))
            for k in range(1, n):
                out.append(add(a, d, seg * k / n))
    out.append(points[-1])
    return out


def polyline_normal_at(
    points: Sequence[Point], anchor: Point, window_px: float = 40.0
) -> Point:
    """Unit normal to a polyline at an anchor point."""
    return perpendicular(tangent_at(points, anchor, window_px))


# ---------------------------------------------------------------------------
# Intersections
# ---------------------------------------------------------------------------


def segment_intersection(
    p1: Point, p2: Point, p3: Point, p4: Point
) -> Point | None:
    """Intersection of segments ``p1p2`` and ``p3p4``, or None."""
    r = vector(p1, p2)
    s = vector(p3, p4)
    denom = cross(r, s)
    if abs(denom) < EPSILON:
        return None
    qp = vector(p1, p3)
    t = cross(qp, s) / denom
    u = cross(qp, r) / denom
    if -EPSILON <= t <= 1 + EPSILON and -EPSILON <= u <= 1 + EPSILON:
        return add(p1, r, t)
    return None


def ray_polyline_intersections(
    origin: Point, direction: Point, points: Sequence[Point], max_distance: float = 1e6
) -> list[tuple[Point, float]]:
    """All intersections of a ray with a polyline.

    Returns ``(point, signed_distance)`` pairs sorted by absolute distance from
    the origin. The distance is signed along ``direction`` so a caller can tell
    which side of the anchor an intersection lies on, which is what lets the
    cortical width construction pick the periosteal hit in one direction and
    the endosteal hit in the other.
    """
    d = normalise(direction)
    far = add(origin, d, max_distance)
    near = add(origin, d, -max_distance)
    hits: list[tuple[Point, float]] = []
    for i in range(len(points) - 1):
        hit = segment_intersection(near, far, points[i], points[i + 1])
        if hit is not None:
            signed = dot(vector(origin, hit), d)
            hits.append((hit, signed))
    hits.sort(key=lambda h: abs(h[1]))
    # Drop duplicates produced at shared vertices.
    deduped: list[tuple[Point, float]] = []
    for hit, signed in hits:
        if all(euclidean(hit, prev) > 1e-6 for prev, _ in deduped):
            deduped.append((hit, signed))
    return deduped


def first_intersection_in_direction(
    origin: Point,
    direction: Point,
    points: Sequence[Point],
    positive: bool = True,
) -> Point | None:
    """Nearest intersection strictly ahead of (or behind) the origin."""
    for hit, signed in ray_polyline_intersections(origin, direction, points):
        if positive and signed > EPSILON:
            return hit
        if not positive and signed < -EPSILON:
            return hit
    return None


def line_intersection(p1: Point, d1: Point, p2: Point, d2: Point) -> Point | None:
    """Intersection of two infinite lines given by point and direction."""
    denom = cross(d1, d2)
    if abs(denom) < EPSILON:
        return None
    t = cross(vector(p1, p2), d2) / denom
    return add(p1, d1, t)


# ---------------------------------------------------------------------------
# Construction helpers for the index lines
# ---------------------------------------------------------------------------


@dataclass
class ConstructionResult:
    """A proposed construction line plus an explanation of how it was built.

    The explanation is shown to the annotator before they accept the proposal,
    so the geometry is never a black box.
    """

    start: Point
    end: Point
    axis: Point
    method: str
    notes: list[str]
    complete: bool

    def as_tuple(self) -> tuple[Point, Point]:
        return self.start, self.end

    def length_px(self) -> float:
        return euclidean(self.start, self.end)


def construct_cortical_width(
    foramen_centre: Point,
    periosteal: Sequence[Point],
    endosteal: Sequence[Point],
    window_px: float = 60.0,
) -> ConstructionResult:
    """Build the cortical width line for one side (FR 022).

    The axis is the perpendicular to the periosteal border at the point closest
    to the mental foramen centre. The line runs from the periosteal border to
    the endosteal border along that axis. This is the same construction the
    protocol describes; ARIA performs it so the annotator adjusts a correct
    starting geometry rather than eyeballing a perpendicular.
    """
    notes: list[str] = []
    foot, _, _ = closest_point_on_polyline(foramen_centre, periosteal)
    tangent = tangent_at(periosteal, foot, window_px)
    axis = perpendicular(tangent)
    notes.append(
        "Axis is the perpendicular to the periosteal border at the point "
        "closest to the mental foramen centre."
    )

    # Orient the axis from the periosteal border toward the foramen, which is
    # the direction the cortex thickness is measured in.
    toward_foramen = vector(foot, foramen_centre)
    if magnitude(toward_foramen) > EPSILON and dot(axis, toward_foramen) < 0:
        axis = (-axis[0], -axis[1])

    peri_hit = first_intersection_in_direction(
        foramen_centre, axis, periosteal, positive=False
    )
    if peri_hit is None:
        peri_hit = foot
        notes.append(
            "The axis did not cross the periosteal border, so the closest "
            "point on that border was used."
        )
    endo_hit = first_intersection_in_direction(
        foramen_centre, axis, endosteal, positive=False
    )
    if endo_hit is None:
        endo_hit = first_intersection_in_direction(
            foramen_centre, axis, endosteal, positive=True
        )
    complete = endo_hit is not None
    if endo_hit is None:
        endo_hit, _, _ = closest_point_on_polyline(peri_hit, endosteal)
        notes.append(
            "The axis did not cross the endosteal border. The closest point on "
            "that border was used and the proposal needs review."
        )

    return ConstructionResult(
        start=peri_hit,
        end=endo_hit,
        axis=normalise(vector(peri_hit, endo_hit)) if euclidean(peri_hit, endo_hit) > EPSILON else axis,
        method="perpendicular_through_mental_foramen",
        notes=notes,
        complete=complete,
    )


def construct_pmi_height(
    margin_point: Point,
    mcw_axis: Point,
    periosteal: Sequence[Point],
) -> ConstructionResult:
    """Height from a mental foramen margin to the inferior border (FR 025, FR 026).

    The axis is the cortical width axis, so the superior and inferior heights
    and the cortical width all share one direction, which is what makes the
    panoramic mandibular index a ratio of collinear distances.
    """
    notes = ["Height runs along the cortical width axis."]
    axis = normalise(mcw_axis)
    hit = first_intersection_in_direction(margin_point, axis, periosteal, positive=True)
    if hit is None:
        hit = first_intersection_in_direction(
            margin_point, axis, periosteal, positive=False
        )
    complete = hit is not None
    if hit is None:
        hit, _, _ = closest_point_on_polyline(margin_point, periosteal)
        notes.append(
            "The axis did not cross the inferior border. The closest point on "
            "the periosteal border was used and the proposal needs review."
        )
    return ConstructionResult(
        start=margin_point,
        end=hit,
        axis=axis,
        method="mcw_axis_to_inferior_border",
        notes=notes,
        complete=complete,
    )


def construct_antegonial_thickness(
    antegonial_point: Point,
    periosteal: Sequence[Point],
    endosteal: Sequence[Point],
    window_px: float = 60.0,
) -> ConstructionResult:
    """Cortical thickness perpendicular to the cortex at the antegonial point
    (FR 023)."""
    notes = ["Axis is the perpendicular to the cortex at the antegonial point."]
    foot, _, _ = closest_point_on_polyline(antegonial_point, periosteal)
    tangent = tangent_at(periosteal, foot, window_px)
    axis = perpendicular(tangent)

    endo_foot, _, _ = closest_point_on_polyline(foot, endosteal)
    toward_endo = vector(foot, endo_foot)
    if magnitude(toward_endo) > EPSILON and dot(axis, toward_endo) < 0:
        axis = (-axis[0], -axis[1])

    endo_hit = first_intersection_in_direction(foot, axis, endosteal, positive=True)
    complete = endo_hit is not None
    if endo_hit is None:
        endo_hit = endo_foot
        notes.append(
            "The axis did not cross the endosteal border. The closest point on "
            "that border was used and the proposal needs review."
        )
    return ConstructionResult(
        start=foot,
        end=endo_hit,
        axis=axis,
        method="perpendicular_at_antegonial_point",
        notes=notes,
        complete=complete,
    )


def construct_gonial_thickness(
    gonion: Point,
    ramus_border: Sequence[Point],
    periosteal: Sequence[Point],
    endosteal: Sequence[Point],
    window_px: float = 80.0,
) -> ConstructionResult:
    """Cortical thickness along the gonial bisector (FR 024).

    The axis bisects the tangent to the posterior border of the ramus and the
    tangent to the inferior border of the mandible, both taken near gonion.
    """
    notes: list[str] = []
    ramus_tangent = tangent_at(ramus_border, gonion, window_px)
    border_foot, _, _ = closest_point_on_polyline(gonion, periosteal)
    border_tangent = tangent_at(periosteal, border_foot, window_px)

    # Orient both tangents to point away from the gonial corner so the bisector
    # falls inside the angle rather than outside it.
    ramus_far = max(ramus_border, key=lambda p: euclidean(p, gonion))
    border_far = max(periosteal, key=lambda p: euclidean(p, gonion))
    if dot(ramus_tangent, vector(gonion, ramus_far)) < 0:
        ramus_tangent = (-ramus_tangent[0], -ramus_tangent[1])
    if dot(border_tangent, vector(gonion, border_far)) < 0:
        border_tangent = (-border_tangent[0], -border_tangent[1])

    axis = bisector(ramus_tangent, border_tangent)
    notes.append(
        "Axis bisects the posterior ramus tangent and the inferior border "
        "tangent taken at gonion."
    )

    start = first_intersection_in_direction(gonion, axis, periosteal, positive=False)
    if start is None:
        start = first_intersection_in_direction(gonion, axis, ramus_border, positive=False)
    if start is None:
        start = gonion
        notes.append("The axis did not cross an outer border, so gonion was used.")

    end = first_intersection_in_direction(start, axis, endosteal, positive=True)
    complete = end is not None
    if end is None:
        end, _, _ = closest_point_on_polyline(start, endosteal)
        notes.append(
            "The axis did not cross the endosteal border. The closest point on "
            "that border was used and the proposal needs review."
        )
    return ConstructionResult(
        start=start,
        end=end,
        axis=axis,
        method="gonial_bisector",
        notes=notes,
        complete=complete,
    )


# ---------------------------------------------------------------------------
# Polygons and rasterisation
# ---------------------------------------------------------------------------


def polygon_area(points: Sequence[Point]) -> float:
    """Absolute polygon area by the shoelace formula, in square pixels."""
    if len(points) < 3:
        return 0.0
    total = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def polygon_centroid(points: Sequence[Point]) -> Point:
    if len(points) < 3:
        xs = [p[0] for p in points] or [0.0]
        ys = [p[1] for p in points] or [0.0]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    cx = cy = a = 0.0
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        a += f
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    if abs(a) < EPSILON:
        return polygon_centroid(points[:2])
    a *= 0.5
    return (cx / (6 * a), cy / (6 * a))


def point_in_polygon(p: Point, points: Sequence[Point]) -> bool:
    """Even odd ray casting test."""
    x, y = p
    inside = False
    n = len(points)
    for i in range(n):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / (y2 - y1 + EPSILON) + x1
            if x < xin:
                inside = not inside
    return inside


def rasterize_polygon(
    points: Sequence[Point], shape: tuple[int, int]
) -> np.ndarray:
    """Scanline fill of a polygon into a boolean mask of ``shape`` (rows, cols).

    Implemented directly rather than pulled from an imaging library so the
    exported mask is bit for bit reproducible from the exported vertices, which
    is what acceptance criterion AC 006 checks.
    """
    rows, cols = shape
    mask = np.zeros((rows, cols), dtype=bool)
    if len(points) < 3:
        return mask

    pts = np.asarray(points, dtype=float)
    y_min = max(0, int(math.floor(pts[:, 1].min())))
    y_max = min(rows - 1, int(math.ceil(pts[:, 1].max())))
    n = len(pts)

    for row in range(y_min, y_max + 1):
        yc = row + 0.5
        xs: list[float] = []
        for i in range(n):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % n]
            if y1 == y2:
                continue
            if (y1 > yc) != (y2 > yc):
                xs.append((x2 - x1) * (yc - y1) / (y2 - y1) + x1)
        if not xs:
            continue
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            left = int(math.ceil(xs[i] - 0.5))
            right = int(math.floor(xs[i + 1] - 0.5))
            if right < left:
                continue
            left = max(0, left)
            right = min(cols - 1, right)
            if right >= left:
                mask[row, left : right + 1] = True
    return mask


def rasterize_polyline(
    points: Sequence[Point], shape: tuple[int, int], width: float = 1.0
) -> np.ndarray:
    """Rasterise a polyline with a given stroke width into a boolean mask."""
    rows, cols = shape
    mask = np.zeros((rows, cols), dtype=bool)
    if len(points) < 2:
        return mask
    half = max(0.5, width / 2.0)
    dense = densify_polyline(points, max_step=max(0.5, half / 2.0))
    r = int(math.ceil(half))
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    disc = (xx ** 2 + yy ** 2) <= half ** 2
    for px, py in dense:
        cx, cy = int(round(px - 0.5)), int(round(py - 0.5))
        y0, y1 = cy - r, cy + r + 1
        x0, x1 = cx - r, cx + r + 1
        sy0, sx0 = max(0, y0), max(0, x0)
        sy1, sx1 = min(rows, y1), min(cols, x1)
        if sy1 <= sy0 or sx1 <= sx0:
            continue
        sub = disc[sy0 - y0 : sy1 - y0, sx0 - x0 : sx1 - x0]
        mask[sy0:sy1, sx0:sx1] |= sub
    return mask


def cortical_band_mask(
    periosteal: Sequence[Point],
    endosteal: Sequence[Point],
    shape: tuple[int, int],
) -> np.ndarray:
    """Filled mask of the band between the periosteal and endosteal borders.

    The two open polylines are joined end to end into a closed ring. The
    endosteal border is reversed first so the ring does not self cross, and the
    joining direction is chosen by testing both pairings and keeping the one
    with the shorter closing segments.
    """
    if len(periosteal) < 2 or len(endosteal) < 2:
        return np.zeros(shape, dtype=bool)

    peri = list(periosteal)
    endo = list(endosteal)
    direct = euclidean(peri[-1], endo[0]) + euclidean(endo[-1], peri[0])
    reversed_ = euclidean(peri[-1], endo[-1]) + euclidean(endo[0], peri[0])
    ring = peri + (endo if direct <= reversed_ else list(reversed(endo)))
    return rasterize_polygon(ring, shape)


def box_to_polygon(x: float, y: float, w: float, h: float) -> list[Point]:
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def bounding_box(points: Sequence[Point]) -> tuple[float, float, float, float]:
    """Return ``(x, y, width, height)`` covering the points."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return x0, y0, x1 - x0, y1 - y0


def clamp_point(p: Point, shape: tuple[int, int]) -> Point:
    """Clamp a point into the image, keeping sub pixel precision."""
    rows, cols = shape
    return (
        min(max(p[0], 0.0), float(cols)),
        min(max(p[1], 0.0), float(rows)),
    )


def points_to_flat(points: Iterable[Point]) -> list[float]:
    flat: list[float] = []
    for x, y in points:
        flat.extend((float(x), float(y)))
    return flat


def flat_to_points(flat: Sequence[float]) -> list[Point]:
    if len(flat) % 2:
        raise ValueError("A flat coordinate list must have an even length")
    return [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat), 2)]
