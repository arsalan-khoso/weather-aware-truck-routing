"""Weather risk classification for a loaded truck at a single point in space and time.

Pure functions only: numbers in, risk out. No network, no Django, no I/O, so the
whole module is directly unit-testable and is where correctness is pinned down.

Units are the ones the weather provider is asked for, so nothing is converted here:
    wind  - miles per hour
    rain  - inches per hour
    snow  - inches per hour

Boundary semantics follow the assessment table literally. Bands are
lower-bound-inclusive and upper-bound-exclusive, which is the only reading that
leaves no gaps (a 34.5 mph wind has to land somewhere).

One asymmetry in the table is deliberate and is preserved exactly:

    wind  ">= 55 mph"  -> No Travel includes 55.0 itself
    rain  "> 1.00"     -> No Travel starts strictly above 1.00, so 1.00 is Severe
    snow  "> 3.0"      -> No Travel starts strictly above 3.0, so 3.0 is Severe

Getting that backwards silently misclassifies the single most important case in
the whole app, so each boundary has its own test in tests/test_risk.py.
"""

from __future__ import annotations

from enum import IntEnum


class Risk(IntEnum):
    """Risk bands, ordered so that `max()` means "the worse of the two"."""

    LOW = 0
    MODERATE = 1
    HIGH = 2
    SEVERE = 3
    NO_TRAVEL = 4

    @property
    def label(self) -> str:
        return self.name.replace("_", " ").title()


# Thresholds as (lower_bound, risk) pairs, highest band first. A reading matches
# the first band whose lower bound it reaches. Kept as data so the tables can be
# read straight off the assessment spec and diffed against it.
WIND_MPH = ((55.0, Risk.NO_TRAVEL), (45.0, Risk.SEVERE), (35.0, Risk.HIGH), (25.0, Risk.MODERATE))
RAIN_IN_HR = ((1.00, Risk.NO_TRAVEL), (0.50, Risk.SEVERE), (0.25, Risk.HIGH), (0.10, Risk.MODERATE))
SNOW_IN_HR = ((3.0, Risk.NO_TRAVEL), (2.0, Risk.SEVERE), (1.0, Risk.HIGH), (0.5, Risk.MODERATE))

# Load thresholds, in pounds, for the escalation rules below.
HEAVY_LB = 30_000
VERY_HEAVY_LB = 40_000


def _band(value: float | None, table, *, top_inclusive: bool) -> Risk:
    """Classify `value` against a (lower_bound, risk) table.

    `top_inclusive` controls only the No Travel bound: wind reaches No Travel at
    exactly 55 mph, while rain and snow need to exceed their top bound.
    """
    v = max(0.0, float(value or 0.0))
    for i, (lower, risk) in enumerate(table):
        if i == 0 and not top_inclusive:
            if v > lower:
                return risk
            continue
        if v >= lower:
            return risk
    return Risk.LOW


def wind_risk(mph: float | None) -> Risk:
    """Wind band. No Travel at 55 mph and above (inclusive, per the table)."""
    return _band(mph, WIND_MPH, top_inclusive=True)


def rain_risk(inches_per_hour: float | None) -> Risk:
    """Rain band. No Travel strictly above 1.00 in/hr, so 1.00 itself is Severe."""
    return _band(inches_per_hour, RAIN_IN_HR, top_inclusive=False)


def snow_risk(inches_per_hour: float | None) -> Risk:
    """Snow band. No Travel strictly above 3.0 in/hr, so 3.0 itself is Severe."""
    return _band(inches_per_hour, SNOW_IN_HR, top_inclusive=False)


def load_floor(wind_mph: float | None, load_lb: float | None) -> tuple[Risk, str | None]:
    """Minimum risk forced by the load rules, which depend on wind alone.

    These rules can only raise risk, never lower it, so the caller takes the
    worse of this floor and the weather bands.

        wind >= 55                         -> No Travel for any load
        wind 45-54 and load > 30,000 lb    -> No Travel
        wind 35-44 and load > 40,000 lb    -> Severe

    Returns the floor plus a short human-readable reason, or (LOW, None) when no
    load rule applies.
    """
    w = max(0.0, float(wind_mph or 0.0))
    load = max(0.0, float(load_lb or 0.0))

    if w >= 55.0:
        return Risk.NO_TRAVEL, "Wind at or above 55 mph - no travel at any load"
    if w >= 45.0 and load > HEAVY_LB:
        return Risk.NO_TRAVEL, f"Wind {w:.0f} mph with load above {HEAVY_LB:,} lb - no travel"
    if w >= 35.0 and load > VERY_HEAVY_LB:
        return Risk.SEVERE, f"Wind {w:.0f} mph with load above {VERY_HEAVY_LB:,} lb - severe"
    return Risk.LOW, None


def classify(
    wind_mph: float | None,
    rain_in_hr: float | None,
    snow_in_hr: float | None,
    load_lb: float | None = 0.0,
) -> dict:
    """Full risk assessment for one checkpoint.

    The overall risk is the worst of the three weather bands, then raised if a
    load rule demands it. `driver` names what actually set the overall risk,
    which is what the UI shows when a driver asks why a checkpoint is red.
    """
    bands = {
        "wind": wind_risk(wind_mph),
        "rain": rain_risk(rain_in_hr),
        "snow": snow_risk(snow_in_hr),
    }
    weather_worst = max(bands.values())
    floor, load_reason = load_floor(wind_mph, load_lb)
    overall = max(weather_worst, floor)

    if floor > weather_worst:
        driver, reason = "load", load_reason
    else:
        driver = max(bands, key=lambda k: bands[k])
        reason = _weather_reason(driver, wind_mph, rain_in_hr, snow_in_hr, overall)

    return {
        "overall": overall,
        "bands": bands,
        "driver": driver,
        "reason": reason,
        "load_escalated": floor > weather_worst,
    }


def severity(
    wind_mph: float | None,
    rain_in_hr: float | None,
    snow_in_hr: float | None,
) -> float:
    """Continuous 0..1 weather severity, for the corridor heatmap.

    `classify` returns one of five bands, which is the right answer for a
    checkpoint: a driver needs "Severe", not "0.62". It is the wrong input for a
    heatmap, because in fair weather every band is Low and the map renders
    uniformly empty - which reads as broken rather than as calm.

    So the heatmap uses the worst of the three readings as a fraction of its own
    No Travel threshold, clamped to 1.0. The scale is therefore anchored to the
    same numbers as the risk table rather than being invented: 0.45 means wind at
    roughly 45% of the no-travel limit, and 1.0 means at or past it. Light wind
    still registers, so the map shows where the weather is building long before
    anything reaches Moderate.

    This is weather severity, not route risk: the load rules are thresholds on
    bands and have no meaningful continuous form, so they apply to checkpoints
    only. The heatmap answers "what is the weather doing in this corridor", and
    the checkpoints answer "what does that mean for this truck".
    """
    wind = max(0.0, float(wind_mph or 0.0)) / WIND_MPH[0][0]
    rain = max(0.0, float(rain_in_hr or 0.0)) / RAIN_IN_HR[0][0]
    snow = max(0.0, float(snow_in_hr or 0.0)) / SNOW_IN_HR[0][0]
    return min(1.0, max(wind, rain, snow))


def _weather_reason(driver: str, wind, rain, snow, overall: Risk) -> str:
    if overall is Risk.LOW:
        return "Clear conditions"
    if driver == "wind":
        return f"Wind {float(wind or 0):.0f} mph"
    if driver == "rain":
        return f"Rain {float(rain or 0):.2f} in/hr"
    return f"Snow {float(snow or 0):.2f} in/hr"
