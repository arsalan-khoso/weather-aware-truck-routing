"""Mapbox geocoding and directions.

Everything that talks to Mapbox lives here, so the domain layer stays pure and
the provider can be swapped without touching the risk or ranking logic.

Three route options
-------------------
The assessment asks for three. Mapbox documents "up to two alternatives", and in
practice it returned the primary plus one alternative - two routes - for every
origin/destination pair tried, long haul or short. So two routes come from
`alternatives=true` and any shortfall is made up by routing through a via-point
pushed perpendicular to the corridor, which forces a genuinely different road
rather than a near-copy of the first. Candidates are rejected unless they stray
at least `MIN_SEPARATION_M` from every route already accepted, so three options
always means three distinct corridors.

Annotations
-----------
`annotations=distance,duration` returns a value per pair of consecutive geometry
points, which is what gives an exact arrival time at an arbitrary mile mark
instead of one inferred from an average speed. A via-point route comes back as
two legs sharing a waypoint, so the per-leg arrays are concatenated: the leg
lengths work out to exactly one fewer entry than the combined geometry.

Network
-------
Requests are retried with a backoff and generous timeouts. These calls were
observed failing mid-download on a slow connection, and a dropped alternative
would otherwise silently reduce the route count.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..domain import geo

log = logging.getLogger(__name__)

BASE = "https://api.mapbox.com"
GEOCODE_URL = f"{BASE}/search/geocode/v6/forward"
DIRECTIONS_URL = f"{BASE}/directions/v5/mapbox/driving"

CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 60

# A candidate route must stray at least this far from every accepted route to
# count as a different option, rather than the same road with a lane change.
MIN_SEPARATION_M = 12_000

# Via-points are tried in this order: (side, fraction of the direct distance).
# Mild offsets first, so the third route stays a plausible route and not a detour
# through another state.
VIA_ATTEMPTS = ((90, 0.12), (-90, 0.12), (90, 0.28), (-90, 0.28))

# Detail kept in the geometry sent to the browser. Below roughly a hundred metres
# the difference is invisible at the zoom levels this map uses, while the payload
# shrinks by more than an order of magnitude. All maths uses the full geometry.
DISPLAY_TOLERANCE_M = 120.0


class MapboxError(RuntimeError):
    """Mapbox could not be reached, or refused the request."""


@dataclass(frozen=True)
class Place:
    """A geocoded endpoint."""

    name: str
    lon: float
    lat: float

    @property
    def coords(self) -> list[float]:
        return [self.lon, self.lat]

    @property
    def pair(self) -> str:
        return f"{self.lon:.6f},{self.lat:.6f}"


@dataclass
class RouteOption:
    """One route, at full provider precision."""

    geometry: list[list[float]]
    distances: list[float]              # metres between consecutive geometry points
    durations: list[float]              # seconds between consecutive geometry points
    distance_m: float
    duration_s: float
    source: str                         # how this option was obtained
    via: list[float] | None = field(default=None)

    @property
    def miles(self) -> float:
        return self.distance_m / 1609.344

    def display_geometry(self, tolerance_m: float = DISPLAY_TOLERANCE_M) -> list[list[float]]:
        """Thinned geometry for drawing. Never used for distance or time."""
        return geo.simplify(self.geometry, tolerance_m)


def token() -> str:
    value = os.environ.get("MAPBOX_TOKEN", "").strip()
    if not value:
        raise MapboxError("MAPBOX_TOKEN is not set. Copy .env.example to .env and add a Mapbox token.")
    return value


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _get(url: str, params: dict) -> dict:
    params = {**params, "access_token": token()}
    try:
        response = _session().get(url, params=params, timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
    except requests.RequestException as exc:
        raise MapboxError(f"Could not reach Mapbox: {exc}") from exc

    if response.status_code == 401:
        raise MapboxError("Mapbox rejected the token (401). Check MAPBOX_TOKEN.")
    if response.status_code == 429:
        raise MapboxError("Mapbox rate limit reached (429). Try again shortly.")
    if not response.ok:
        raise MapboxError(f"Mapbox returned {response.status_code}: {response.text[:200]}")
    return response.json()


def geocode(query: str, *, country: str = "us") -> Place:
    """Resolve a free-text place to coordinates.

    `country` defaults to the US because the risk thresholds are given in miles
    and inches; it can be widened without touching anything else.
    """
    query = (query or "").strip()
    if not query:
        raise MapboxError("Enter an origin and a destination.")

    data = _get(GEOCODE_URL, {"q": query, "limit": 1, "country": country})
    features = data.get("features") or []
    if not features:
        raise MapboxError(f"No place found for {query!r}.")

    feature = features[0]
    lon, lat = feature["geometry"]["coordinates"][:2]
    properties = feature.get("properties") or {}
    name = properties.get("full_address") or properties.get("name") or query
    return Place(name=name, lon=float(lon), lat=float(lat))


def _parse(route: dict, source: str, via: list[float] | None = None) -> RouteOption:
    """Turn a Mapbox route into a RouteOption, concatenating per-leg annotations."""
    distances: list[float] = []
    durations: list[float] = []
    for leg in route.get("legs") or []:
        annotation = leg.get("annotation") or {}
        distances.extend(float(v or 0.0) for v in annotation.get("distance") or [])
        durations.extend(float(v or 0.0) for v in annotation.get("duration") or [])

    geometry = [list(c[:2]) for c in route["geometry"]["coordinates"]]
    return RouteOption(
        geometry=geometry,
        distances=distances,
        durations=durations,
        distance_m=float(route["distance"]),
        duration_s=float(route["duration"]),
        source=source,
        via=via,
    )


def _directions(coordinates: list[str], *, alternatives: bool, exclude: str | None = None) -> list[dict]:
    params = {
        "alternatives": "true" if alternatives else "false",
        "overview": "full",
        "geometries": "geojson",
        "annotations": "distance,duration",
    }
    if exclude:
        params["exclude"] = exclude

    path = ";".join(coordinates)
    data = _get(f"{DIRECTIONS_URL}/{path}", params)
    if data.get("code") != "Ok":
        raise MapboxError(f"Mapbox could not route this trip ({data.get('code')}): {data.get('message', '')}")
    return data.get("routes") or []


def _is_distinct(candidate: RouteOption, accepted: list[RouteOption]) -> bool:
    """True when the candidate follows a different corridor from everything accepted."""
    return all(
        geo.max_deviation_m(candidate.geometry, existing.geometry) >= MIN_SEPARATION_M
        for existing in accepted
    )


def route_options(origin: Place, destination: Place, *, want: int = 3) -> list[RouteOption]:
    """Up to `want` distinct route options between two places.

    Raises MapboxError only when no route at all could be produced; a shortfall
    of alternatives is reported by the caller, not treated as a failure.
    """
    accepted: list[RouteOption] = []

    for route in _directions([origin.pair, destination.pair], alternatives=True):
        candidate = _parse(route, source="alternatives")
        if not accepted or _is_distinct(candidate, accepted):
            accepted.append(candidate)
        if len(accepted) >= want:
            return accepted

    if not accepted:
        raise MapboxError("Mapbox returned no route for this origin and destination.")

    direct_m = geo.haversine_m(origin.coords, destination.coords)
    bearing = geo.initial_bearing(origin.coords, destination.coords)
    middle = geo.midpoint(origin.coords, destination.coords)

    for side, fraction in VIA_ATTEMPTS:
        if len(accepted) >= want:
            break
        via = geo.destination(middle, (bearing + side) % 360, direct_m * fraction)
        try:
            routes = _directions([origin.pair, f"{via[0]:.6f},{via[1]:.6f}", destination.pair],
                                 alternatives=False)
        except MapboxError as exc:
            log.warning("via-point route failed (side %s, offset %.0f%%): %s", side, fraction * 100, exc)
            continue
        for route in routes:
            candidate = _parse(route, source="via-offset", via=via)
            if _is_distinct(candidate, accepted):
                accepted.append(candidate)
                break

    if len(accepted) < want:
        try:
            for route in _directions([origin.pair, destination.pair],
                                     alternatives=False, exclude="motorway"):
                candidate = _parse(route, source="no-motorway")
                if _is_distinct(candidate, accepted):
                    accepted.append(candidate)
                    break
        except MapboxError as exc:
            log.warning("motorway-free fallback failed: %s", exc)

    return accepted[:want]
