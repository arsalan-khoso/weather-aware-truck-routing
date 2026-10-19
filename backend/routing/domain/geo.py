"""Geodesic helpers and polyline simplification.

Pure maths on [lon, lat] pairs, kept out of the service layer so it can be
tested without touching the network.

Two jobs here carry real weight in this app:

`simplify` - a 1,000 mile route comes back from the provider as ~10,000
coordinates, so three options are ~33,000 coordinates and well over a megabyte
of JSON. The browser is sent a simplified line instead, while every distance,
ETA and risk calculation keeps using the full-precision geometry server-side.
Accuracy is therefore unaffected; only the drawn line loses detail that is
smaller than a pixel at these zoom levels.

`destination` / `initial_bearing` - used to construct a via-point offset
perpendicular to the corridor, which is how a genuinely different third route is
produced when the provider only returns two.
"""

from __future__ import annotations

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_008.8
Point = list[float]      # [lon, lat]


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance in metres."""
    lon1, lat1, lon2, lat2 = radians(a[0]), radians(a[1]), radians(b[0]), radians(b[1])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(min(1.0, sqrt(h)))


def initial_bearing(a: Point, b: Point) -> float:
    """Bearing in degrees from `a` towards `b`, clockwise from north."""
    lon1, lat1, lon2, lat2 = radians(a[0]), radians(a[1]), radians(b[0]), radians(b[1])
    dlon = lon2 - lon1
    y = sin(dlon) * cos(lat2)
    x = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(dlon)
    return (degrees(atan2(y, x)) + 360) % 360


def destination(origin: Point, bearing_deg: float, distance_m: float) -> Point:
    """The point `distance_m` from `origin` along `bearing_deg`."""
    d = distance_m / EARTH_RADIUS_M
    brg = radians(bearing_deg)
    lon1, lat1 = radians(origin[0]), radians(origin[1])

    lat2 = asin(sin(lat1) * cos(d) + cos(lat1) * sin(d) * cos(brg))
    lon2 = lon1 + atan2(sin(brg) * sin(d) * cos(lat1), cos(d) - sin(lat1) * sin(lat2))
    return [round((degrees(lon2) + 540) % 360 - 180, 6), round(degrees(lat2), 6)]


def midpoint(a: Point, b: Point) -> Point:
    """Half way along the great circle between two points."""
    return destination(a, initial_bearing(a, b), haversine_m(a, b) / 2)


def resample(points: list[Point], count: int) -> list[Point]:
    """At most `count` points spread evenly by index, always including both ends.

    Used to compare two routes cheaply; index spacing is close enough to even
    spacing for that, because provider geometry is densest on curves.

    A line that already has no more than `count` points is returned whole. It
    must not be thinned to its endpoints: callers use the result as a set of
    candidate nearest-neighbours, and dropping the interior would make two
    identical routes look kilometres apart.
    """
    if len(points) <= 2:
        return list(points)
    if count <= 2:
        return [points[0], points[-1]]
    if len(points) <= count:
        return list(points)
    last = len(points) - 1
    return [points[round(i * last / (count - 1))] for i in range(count)]


def max_deviation_m(candidate: list[Point], reference: list[Point], *, samples: int = 24) -> float:
    """How far `candidate` strays from `reference`, in metres.

    For each of a few points on the candidate, the nearest point on a resampled
    reference is found, and the largest of those nearest-distances is returned.
    Near zero means the two routes follow the same corridor; this is what route
    de-duplication thresholds against. It is an approximation of the one-sided
    Hausdorff distance, which is all that is needed to tell "same road" from
    "different road".
    """
    if not candidate or not reference:
        return float("inf")
    probes = resample(candidate, samples)
    track = resample(reference, 240)
    return max(min(haversine_m(p, q) for q in track) for p in probes)


def simplify(points: list[Point], tolerance_m: float) -> list[Point]:
    """Douglas-Peucker, keeping any vertex further than `tolerance_m` off the line.

    Iterative rather than recursive: provider geometry runs to five figures of
    coordinates and recursion would risk the stack limit.
    """
    if len(points) < 3 or tolerance_m <= 0:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        index, worst = first, -1.0
        for i in range(first + 1, last):
            d = _cross_track_m(points[i], points[first], points[last])
            if d > worst:
                index, worst = i, d
        if worst > tolerance_m:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))

    return [p for p, k in zip(points, keep) if k]


def _cross_track_m(point: Point, start: Point, end: Point) -> float:
    """Perpendicular distance from `point` to the segment `start`-`end`, in metres.

    Uses a local equirectangular projection: over a single route segment the
    error is far below the simplification tolerance, and it is much cheaper than
    a spherical solution inside the Douglas-Peucker loop.
    """
    scale = cos(radians((start[1] + end[1]) / 2))
    m_per_deg = EARTH_RADIUS_M * radians(1.0)

    px = (point[0] - start[0]) * scale * m_per_deg
    py = (point[1] - start[1]) * m_per_deg
    ex = (end[0] - start[0]) * scale * m_per_deg
    ey = (end[1] - start[1]) * m_per_deg

    span = ex * ex + ey * ey
    if span <= 0:
        return sqrt(px * px + py * py)

    t = max(0.0, min(1.0, (px * ex + py * ey) / span))
    dx, dy = px - t * ex, py - t * ey
    return sqrt(dx * dx + dy * dy)
