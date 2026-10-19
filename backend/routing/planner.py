"""Trip planning: the one place the services and the domain logic meet.

Flow for a single request:

    geocode both endpoints
      -> three distinct route options
      -> sample checkpoints along each, with an exact arrival time at each
      -> ONE batched forecast fetch covering every route and all 48 slider hours
      -> classify each checkpoint against the risk and load rules
      -> score the routes and recommend one

The single forecast fetch is the point. Checkpoint risk, route scoring and every
hour of the heatmap all read from the same in-memory result, so the slider in the
browser never needs another request and the backend never recomputes a thing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .domain import checkpoints as cp
from .domain import recommend as rec
from .domain.risk import classify, severity
from .services import mapbox, weather

log = logging.getLogger(__name__)

# Spacing of the forecast points that make up the heatmap corridor. Independent
# of the user's checkpoint interval, so the heatmap stays evenly dense whether
# checkpoints are every 10 or every 50 miles.
CORRIDOR_SPACING_MILES = 25.0

# How far ahead a departure may be. Open-Meteo serves 16 days; this leaves room
# for a long drive on top of a late departure.
MAX_DEPARTURE_DAYS = 10


class PlanError(ValueError):
    """The request cannot be planned as asked."""


def plan(
    *,
    origin_query: str,
    destination_query: str,
    departure: datetime,
    load_lb: float,
    interval_miles: float,
    want_routes: int = 3,
) -> dict:
    """Plan a trip and return everything the UI draws, as plain JSON-ready data."""
    if interval_miles not in cp.SAMPLE_INTERVALS_MILES:
        raise PlanError(f"Sampling interval must be one of {cp.SAMPLE_INTERVALS_MILES} miles.")
    if load_lb < 0:
        raise PlanError("Load weight cannot be negative.")

    departure = departure.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if departure > now + timedelta(days=MAX_DEPARTURE_DAYS):
        raise PlanError(f"Departure must be within {MAX_DEPARTURE_DAYS} days; forecasts do not reach further.")

    origin = mapbox.geocode(origin_query)
    destination = mapbox.geocode(destination_query)
    options = mapbox.route_options(origin, destination, want=want_routes)

    warnings = []
    if len(options) < want_routes:
        warnings.append(
            f"Only {len(options)} distinct route option"
            f"{'s' if len(options) != 1 else ''} could be generated for this trip."
        )

    # Sample every route before fetching anything, so one forecast call can cover
    # all of them at once.
    sampled = [
        cp.sample(
            option.geometry, option.distances, option.durations,
            total_distance_m=option.distance_m,
            total_duration_s=option.duration_s,
            interval_miles=interval_miles,
            departure=departure,
        )
        for option in options
    ]
    corridor = [
        point
        for option in options
        for point in cp.sample(
            option.geometry, option.distances, option.durations,
            total_distance_m=option.distance_m,
            total_duration_s=option.duration_s,
            interval_miles=CORRIDOR_SPACING_MILES,
            departure=departure,
        )
    ]

    longest_s = max((option.duration_s for option in options), default=0.0)
    points = [(p.lon, p.lat) for points_ in sampled for p in points_]
    points += [(p.lon, p.lat) for p in corridor]

    series = weather.fetch(points, start=departure, hours=int(longest_s // 3600) + 2)
    if not series:
        raise PlanError("No forecast data was returned for this corridor.")

    routes, scores = [], []
    for option, points_ in zip(options, sampled):
        scored = _score_checkpoints(points_, series, load_lb)
        score = rec.score(len(routes), scored, option.miles, option.duration_s)
        scores.append(score)
        routes.append(_route_payload(len(routes), option, scored, score, departure))

    recommendation = rec.recommend(scores)
    heatmap = _heatmap(corridor, series, departure)

    return {
        "origin": {"name": origin.name, "lon": origin.lon, "lat": origin.lat},
        "destination": {"name": destination.name, "lon": destination.lon, "lat": destination.lat},
        "departure": departure.isoformat(),
        "load_lb": load_lb,
        "interval_miles": interval_miles,
        "routes": routes,
        "recommendation": recommendation,
        "heatmap": heatmap,
        "warnings": warnings,
    }


def _score_checkpoints(points, series, load_lb: float) -> list[dict]:
    """Attach a forecast and a risk verdict to each checkpoint."""
    out = []
    for point in points:
        cell = weather.lookup(series, point.lon, point.lat)
        reading = cell.at(point.eta) if cell else None
        verdict = classify(
            reading["wind_mph"] if reading else None,
            reading["rain_in_hr"] if reading else None,
            reading["snow_in_hr"] if reading else None,
            load_lb,
        )
        out.append({
            "index": point.index,
            "lon": point.lon,
            "lat": point.lat,
            "miles": point.miles,
            "eta": point.eta.isoformat(),
            "segment_miles": point.segment_miles,
            "risk": verdict["overall"],
            "driver": verdict["driver"],
            "reason": verdict["reason"],
            "load_escalated": verdict["load_escalated"],
            "bands": {k: v.label for k, v in verdict["bands"].items()},
            "forecast": {
                "wind_mph": round(reading["wind_mph"], 1) if reading else None,
                "rain_in_hr": round(reading["rain_in_hr"], 3) if reading else None,
                "snow_in_hr": round(reading["snow_in_hr"], 3) if reading else None,
            },
            "has_forecast": reading is not None,
        })
    return out


def _route_payload(index: int, option, scored: list[dict], score, departure: datetime) -> dict:
    """One route as the UI needs it, with the drawn line thinned down."""
    return {
        "index": index,
        "source": option.source,
        "via": option.via,
        "geometry": option.display_geometry(),
        "distance_miles": round(option.miles, 1),
        "duration_s": round(option.duration_s),
        "arrival": (departure + timedelta(seconds=option.duration_s)).isoformat(),
        "summary": {
            "worst": score.worst.label,
            "viable": score.viable,
            "avg_risk": score.avg_risk,
            "mean_risk": score.mean_risk,
            "no_travel_miles": score.no_travel_miles,
            "severe_miles": score.severe_miles,
            "high_miles": score.high_miles,
            "miles_by_risk": {risk.label: miles for risk, miles in sorted(score.miles_by_risk.items())},
            "checkpoint_count": score.checkpoint_count,
        },
        "checkpoints": [
            {**point, "risk": point["risk"].label, "risk_level": int(point["risk"])}
            for point in scored
        ],
    }


def _heatmap(corridor, series, departure: datetime) -> dict:
    """Corridor forecast points carrying a weather severity for each of 48 hours.

    Sent as flat arrays rather than per-hour GeoJSON properties: the browser
    builds the feature collection once, then moves the slider by swapping which
    property the heatmap reads, with no further data transfer.

    Weights come from `risk.severity`, a continuous 0..1 scale, not from the five
    risk bands - see that function for why a band-based heatmap renders empty
    whenever the weather is fair.
    """
    hours = weather.SLIDER_HOURS
    cells, points = set(), []

    for point in corridor:
        cell = weather.snap(point.lon, point.lat)
        if cell in cells:
            continue
        cells.add(cell)
        window = weather.lookup(series, point.lon, point.lat)
        if window is None:
            continue
        sliced = window.window(departure, hours)
        weights = [
            round(severity(sliced.wind[i], sliced.rain[i], sliced.snow[i]), 3)
            for i in range(len(sliced))
        ]
        weights += [0.0] * (hours - len(weights))
        points.append({"lon": cell[0], "lat": cell[1], "h": weights})

    times = [(departure + timedelta(hours=i)).isoformat() for i in range(hours)]
    log.info("heatmap: %d corridor cells x %d hours", len(points), hours)
    return {"hours": hours, "start": departure.isoformat(), "times": times, "points": points}
