"""Sampling checkpoints along a route, with the truck's arrival time at each one.

Pure functions: route geometry in, checkpoints out. No network, no Django.

The whole point of this module is that a checkpoint 400 miles along the route is
not looked up with the departure-time forecast but with the forecast for the hour
the truck actually gets there. That needs a real cumulative-time curve, not an
assumed average speed, so this works off the routing provider's per-segment
`distance` and `duration` annotations.

Mile attribution
----------------
"Fewest Severe miles" needs each checkpoint to stand for a stretch of road, not
just a pin. Each checkpoint owns the road from the midpoint behind it to the
midpoint ahead of it, with the first and last reaching the route ends. The owned
stretches therefore tile the route exactly: their sum is the route length, which
`tests/test_checkpoints.py` asserts.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta

METERS_PER_MILE = 1609.344

# Intervals the UI offers, in miles.
SAMPLE_INTERVALS_MILES = (10, 25, 50)


@dataclass(frozen=True)
class Checkpoint:
    """One sampling point along a route."""

    index: int
    lon: float
    lat: float
    miles: float            # distance from the origin, along the route
    eta: datetime           # when the truck reaches this point
    segment_miles: float    # length of road this checkpoint stands for

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lon, self.lat)


def cumulative(annotation: list[float] | None, count: int, total: float) -> list[float]:
    """Running totals along the geometry, as a list of length `count`.

    `annotation[i]` is the value between geometry points i and i+1, so the
    returned list starts at 0.0 and ends at the route total.

    If the provider omitted the annotation, fall back to spreading `total`
    evenly over the segments. That keeps the app working on a degraded response
    instead of failing, and is the only place an assumption is made.
    """
    if annotation and len(annotation) >= count - 1:
        out = [0.0]
        for i in range(count - 1):
            out.append(out[-1] + float(annotation[i] or 0.0))
        return out
    step = total / max(1, count - 1)
    return [i * step for i in range(count)]


def sample(
    geometry: list[list[float]],
    distances: list[float] | None,
    durations: list[float] | None,
    *,
    total_distance_m: float,
    total_duration_s: float,
    interval_miles: float,
    departure: datetime,
) -> list[Checkpoint]:
    """Place a checkpoint every `interval_miles`, plus the origin and destination.

    The origin is included because the weather where the driver starts is part of
    the decision, and the destination because the last stretch of road has to be
    owned by something.
    """
    if len(geometry) < 2:
        raise ValueError("route geometry needs at least two points")

    n = len(geometry)
    cum_m = cumulative(distances, n, total_distance_m)
    cum_s = cumulative(durations, n, total_duration_s)
    route_m = cum_m[-1] or total_distance_m

    step_m = max(1.0, interval_miles * METERS_PER_MILE)
    targets = [d for d in _frange(0.0, route_m, step_m)]
    if route_m - targets[-1] > 1.0:          # always finish at the destination
        targets.append(route_m)

    bounds = _ownership_bounds(targets, route_m)
    out = []
    for i, (d, (lo, hi)) in enumerate(zip(targets, bounds)):
        lon, lat, seconds = _at_distance(geometry, cum_m, cum_s, d)
        out.append(Checkpoint(
            index=i,
            lon=round(lon, 6),
            lat=round(lat, 6),
            miles=round(d / METERS_PER_MILE, 2),
            eta=departure + timedelta(seconds=seconds),
            segment_miles=round((hi - lo) / METERS_PER_MILE, 3),
        ))
    return out


def _frange(start: float, stop: float, step: float) -> list[float]:
    out, v = [], start
    while v < stop:
        out.append(v)
        v += step
    return out or [start]


def _ownership_bounds(targets: list[float], route_m: float) -> list[tuple[float, float]]:
    """Midpoint-to-midpoint stretch owned by each checkpoint; these tile the route."""
    bounds = []
    for i, d in enumerate(targets):
        lo = 0.0 if i == 0 else (targets[i - 1] + d) / 2
        hi = route_m if i == len(targets) - 1 else (d + targets[i + 1]) / 2
        bounds.append((lo, hi))
    return bounds


def _at_distance(
    geometry: list[list[float]],
    cum_m: list[float],
    cum_s: list[float],
    target_m: float,
) -> tuple[float, float, float]:
    """Position and elapsed seconds at `target_m` along the route.

    Interpolates inside the geometry segment the target falls in, so a checkpoint
    lands exactly on its mile mark rather than snapping to the nearest vertex.
    """
    if target_m <= 0:
        return geometry[0][0], geometry[0][1], cum_s[0]
    if target_m >= cum_m[-1]:
        return geometry[-1][0], geometry[-1][1], cum_s[-1]

    i = max(0, bisect_right(cum_m, target_m) - 1)
    j = min(i + 1, len(geometry) - 1)
    span = cum_m[j] - cum_m[i]
    t = 0.0 if span <= 0 else (target_m - cum_m[i]) / span

    lon = geometry[i][0] + (geometry[j][0] - geometry[i][0]) * t
    lat = geometry[i][1] + (geometry[j][1] - geometry[i][1]) * t
    seconds = cum_s[i] + (cum_s[j] - cum_s[i]) * t
    return lon, lat, seconds
