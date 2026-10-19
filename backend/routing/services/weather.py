"""Hourly forecasts from Open-Meteo.

Open-Meteo is used because it needs no API key and, crucially, returns the exact
units the risk table is written in. Asking for `wind_speed_unit=mph` and
`precipitation_unit=inch` gives mph, inch and inch for wind, rain and snowfall
respectively - snowfall included, which the docs leave unstated and which was
confirmed against the live API. Nothing in this app converts weather units, so
there is no conversion to get wrong.

Why one batched call
--------------------
Measured against the live API, latency is dominated by the round trip rather
than the number of locations: two locations took about 5 s, a hundred about 10 s,
four hundred about 28 s. That shapes the whole design.

  * Points are snapped to a `GRID_DEG` grid and de-duplicated. Three route
    options overlap heavily and the model's own resolution is coarser than a
    10-mile sample anyway, so a few hundred checkpoints collapse to a hundred or
    so distinct cells.
  * The full hourly series is fetched once, deep enough to cover both the drive
    and the 48-hour slider. The slider therefore never touches the network: the
    browser already holds every hour it can show.
  * Cells are cached individually, so a second trip sharing most of its corridor
    only fetches what is new.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

CONNECT_TIMEOUT_S = 10
READ_TIMEOUT_S = 90

# Grid used to collapse nearby sample points onto one forecast lookup. 0.1 degrees
# is roughly 7 miles of longitude at US latitudes, which is finer than the model
# resolution and far finer than the coarsest 50-mile sampling interval.
GRID_DEG = 0.1

# Locations per request, and how many requests are in flight at once.
#
# Measured against the live API, a single request's cost is mostly its round trip
# rather than its size, and one 110-location request for a Chicago-Denver
# corridor took 80 s end to end. Splitting the same work into several smaller
# requests that run concurrently pays for the extra round trips many times over,
# because the trips overlap. The batch stays large enough that a short corridor
# is still a single request.
BATCH_SIZE = 40
MAX_WORKERS = 6

# Hours the slider spans, and so the minimum depth of forecast to fetch.
SLIDER_HOURS = 48

CACHE_TTL_S = 30 * 60      # forecasts refresh hourly; half an hour is safe
CACHE_VERSION = 1

# Exactly the three variables the risk table is written against, and nothing
# else. Open-Meteo bills by a weighted call cost that scales with the number of
# variables and the length of the window, and every location in a batch counts
# against the per-IP limit (600/min, 5,000/hour, 10,000/day), so an unused
# variable is not free - it is a quarter of the quota for this corridor. Wind
# gusts were dropped for exactly that reason: no rule in the spec reads them.
HOURLY_VARIABLES = ("wind_speed_10m", "rain", "snowfall")

Cell = tuple[float, float]      # snapped (lon, lat)


class WeatherError(RuntimeError):
    """Open-Meteo could not be reached, or returned something unusable."""


@dataclass
class Series:
    """An hourly forecast series for one grid cell.

    `times` holds unix seconds on exact hours, so an index is found by
    arithmetic rather than a search.
    """

    times: list[int]
    wind: list[float]
    rain: list[float]
    snow: list[float]

    def __len__(self) -> int:
        return len(self.times)

    def index_at(self, when: datetime) -> int:
        """Index of the hour nearest `when`, clamped to the series.

        Hourly data is only ever accurate to the hour, so the nearest hour is
        used rather than the preceding one: that halves the worst-case error for
        an instantaneous reading like wind.
        """
        if not self.times:
            raise WeatherError("empty forecast series")
        offset = (when.timestamp() - self.times[0]) / 3600.0
        return max(0, min(len(self.times) - 1, round(offset)))

    def at(self, when: datetime) -> dict:
        """Readings for the hour nearest `when`, in risk-table units."""
        i = self.index_at(when)
        return {
            "time": self.times[i],
            "wind_mph": self.wind[i],
            "rain_in_hr": self.rain[i],
            "snow_in_hr": self.snow[i],
        }

    def window(self, start: datetime, hours: int) -> "Series":
        """A copy covering `hours` hours from `start`, for the slider."""
        i = self.index_at(start)
        j = min(len(self.times), i + hours)
        return Series(
            times=self.times[i:j],
            wind=self.wind[i:j],
            rain=self.rain[i:j],
            snow=self.snow[i:j],
        )


def snap(lon: float, lat: float) -> Cell:
    """Nearest grid cell centre, as the key a forecast is cached and looked up by."""
    return (round(round(lon / GRID_DEG) * GRID_DEG, 4),
            round(round(lat / GRID_DEG) * GRID_DEG, 4))


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=2.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _cache_key(cell: Cell, window: tuple[str, str]) -> str:
    return f"om:{CACHE_VERSION}:{cell[0]}:{cell[1]}:{window[0]}:{window[1]}"


def _as_series(block: dict) -> Series:
    """Convert one Open-Meteo `hourly` block into a Series, filling gaps with calm."""
    hourly = block.get("hourly") or {}
    times = [int(t) for t in hourly.get("time") or []]

    def column(name: str) -> list[float]:
        values = hourly.get(name) or []
        return [float(v) if v is not None else 0.0 for v in values] + [0.0] * (len(times) - len(values))

    return Series(
        times=times,
        wind=column("wind_speed_10m"),
        rain=column("rain"),
        snow=column("snowfall"),
    )


def _fetch_batch(cells: list[Cell], window: tuple[str, str]) -> dict[Cell, Series]:
    """One request covering many locations, returned per cell in request order."""
    params = {
        "latitude": ",".join(f"{lat:.4f}" for _, lat in cells),
        "longitude": ",".join(f"{lon:.4f}" for lon, _ in cells),
        "hourly": ",".join(HOURLY_VARIABLES),
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timeformat": "unixtime",
        "timezone": "UTC",
        # An explicit hour window rather than whole days. Measured on a 20-cell
        # corridor, asking for the 48 hours actually needed instead of the three
        # calendar days containing them cut the response from 44.7 KB to 32.0 KB.
        # Response time tracks payload closely here, so that is time, not just bytes.
        "start_hour": window[0],
        "end_hour": window[1],
    }
    try:
        response = _session().get(FORECAST_URL, params=params,
                                  timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S))
    except requests.RequestException as exc:
        raise WeatherError(f"Could not reach Open-Meteo: {exc}") from exc

    if response.status_code == 429:
        # Every location in a batch counts against the per-IP limit, so a long
        # corridor planned repeatedly can reach it. Say so plainly: the cached
        # cells mean a retry is usually much cheaper than the first attempt.
        wait = response.headers.get("retry-after", "a minute")
        raise WeatherError(
            f"Open-Meteo rate limit reached for this corridor. Try again in {wait}."
        )
    if not response.ok:
        raise WeatherError(f"Open-Meteo returned {response.status_code}: {response.text[:200]}")

    payload = response.json()
    blocks = payload if isinstance(payload, list) else [payload]
    if len(blocks) != len(cells):
        raise WeatherError(f"Open-Meteo returned {len(blocks)} blocks for {len(cells)} locations")
    return {cell: _as_series(block) for cell, block in zip(cells, blocks)}


def fetch(points: list[tuple[float, float]], *, start: datetime, hours: int) -> dict[Cell, Series]:
    """Hourly forecasts for every distinct grid cell covering `points`.

    `points` are raw (lon, lat) pairs and may repeat; the result is keyed by
    `snap(lon, lat)`, which is what callers look up with. The series is fetched
    deep enough to cover `hours` from `start`, and always at least the slider's
    48-hour span.
    """
    if not points:
        return {}

    start = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    depth = max(hours, SLIDER_HOURS)
    # Exactly the hours the drive and the slider need, on an hour boundary so two
    # plans with the same departure share cache entries - which is the common
    # case, because re-planning usually means changing the load or the interval.
    window = (start.strftime("%Y-%m-%dT%H:00"),
              (start + timedelta(hours=depth - 1)).strftime("%Y-%m-%dT%H:00"))

    wanted = list(dict.fromkeys(snap(lon, lat) for lon, lat in points))
    series: dict[Cell, Series] = {}
    missing: list[Cell] = []

    for cell in wanted:
        hit = cache.get(_cache_key(cell, window))
        if hit is not None:
            series[cell] = hit
        else:
            missing.append(cell)

    log.info("weather: %d cells for %d points (%d cached, %d to fetch)",
             len(wanted), len(points), len(series), len(missing))
    if not missing:
        return series

    batches = [missing[i:i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
    if len(batches) == 1:
        fetched_batches = [_fetch_batch(batches[0], window)]
    else:
        # Requests are network-bound, so threads are the right tool: they overlap
        # the waiting, which is nearly all of the elapsed time.
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batches))) as pool:
            futures = [pool.submit(_fetch_batch, batch, window) for batch in batches]
            fetched_batches = [future.result() for future in futures]

    for fetched in fetched_batches:
        for cell, value in fetched.items():
            cache.set(_cache_key(cell, window), value, CACHE_TTL_S)
        series.update(fetched)

    return series


def lookup(series: dict[Cell, Series], lon: float, lat: float) -> Series | None:
    """The series covering a point, or None when the fetch did not include it."""
    return series.get(snap(lon, lat))
