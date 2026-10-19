"""Scoring route options and picking the one to recommend.

Pure functions: scored checkpoints in, a ranking out. No network, no Django.

Preference order, from the assessment spec
------------------------------------------
    1. Fewest Severe miles
    2. Fewest High miles
    3. Lowest average risk
    4. Shortest travel time

Applied strictly in order: criterion 2 is only consulted when criterion 1 ties,
and so on, so a route is never recommended because it is faster when another
route is safer.

Two things the spec leaves open, decided here and surfaced in the response
-------------------------------------------------------------------------
No Travel miles rank ahead of Severe miles. A route carrying a No Travel
checkpoint is one the rules say the truck must not drive, so it cannot be
preferred over a merely Severe route just for having fewer Severe miles. Such
routes are still returned and still scored, but they are marked `viable: False`
and sorted last, and the UI says so rather than hiding them.

Average risk is weighted by the miles each checkpoint stands for, not a plain
mean over checkpoints. The first and last checkpoints own half-stretches, so an
unweighted mean would quietly over-count the route ends. The plain mean is
included in the payload as `mean_risk` for transparency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .risk import Risk

# Comparisons round to these precisions so that floating-point noise - a stretch
# of 12.4999 against 12.5001 miles - cannot flip a recommendation.
MILE_PRECISION = 1
RISK_PRECISION = 3


@dataclass
class RouteScore:
    """Risk roll-up for one route option."""

    route_index: int
    total_miles: float
    duration_s: float
    miles_by_risk: dict[Risk, float] = field(default_factory=dict)
    avg_risk: float = 0.0
    mean_risk: float = 0.0
    worst: Risk = Risk.LOW
    checkpoint_count: int = 0

    @property
    def no_travel_miles(self) -> float:
        return self.miles_by_risk.get(Risk.NO_TRAVEL, 0.0)

    @property
    def severe_miles(self) -> float:
        return self.miles_by_risk.get(Risk.SEVERE, 0.0)

    @property
    def high_miles(self) -> float:
        return self.miles_by_risk.get(Risk.HIGH, 0.0)

    @property
    def viable(self) -> bool:
        """False when any checkpoint on the route is a No Travel checkpoint."""
        return self.no_travel_miles <= 0.0

    @property
    def sort_key(self) -> tuple:
        return (
            round(self.no_travel_miles, MILE_PRECISION),
            round(self.severe_miles, MILE_PRECISION),
            round(self.high_miles, MILE_PRECISION),
            round(self.avg_risk, RISK_PRECISION),
            round(self.duration_s),
        )


# The criteria in the order they are applied, with the label shown to the user.
CRITERIA = (
    ("no_travel_miles", "no-travel miles"),
    ("severe_miles", "severe miles"),
    ("high_miles", "high-risk miles"),
    ("avg_risk", "average risk"),
    ("duration_s", "travel time"),
)


def score(route_index: int, checkpoints: list[dict], total_miles: float, duration_s: float) -> RouteScore:
    """Roll up scored checkpoints into one route score.

    Each checkpoint is a dict with at least `risk` (a `Risk`) and `segment_miles`,
    which is what `checkpoints.sample` plus `risk.classify` produce.
    """
    miles_by_risk: dict[Risk, float] = {}
    weighted = 0.0
    owned = 0.0
    levels = []

    for cp in checkpoints:
        risk: Risk = cp["risk"]
        miles = float(cp.get("segment_miles") or 0.0)
        miles_by_risk[risk] = miles_by_risk.get(risk, 0.0) + miles
        weighted += int(risk) * miles
        owned += miles
        levels.append(int(risk))

    return RouteScore(
        route_index=route_index,
        total_miles=round(total_miles, 2),
        duration_s=round(duration_s),
        miles_by_risk={k: round(v, 2) for k, v in miles_by_risk.items()},
        avg_risk=round(weighted / owned, 4) if owned else 0.0,
        mean_risk=round(sum(levels) / len(levels), 4) if levels else 0.0,
        worst=Risk(max(levels)) if levels else Risk.LOW,
        checkpoint_count=len(levels),
    )


def rank(scores: list[RouteScore]) -> list[RouteScore]:
    """Route scores best-first, by the preference order above."""
    return sorted(scores, key=lambda s: s.sort_key)


def recommend(scores: list[RouteScore]) -> dict:
    """Pick the route to recommend and say which criterion decided it.

    The explanation names the first criterion on which the winner actually beat
    the runner-up, so the UI can tell the driver why this route won instead of
    just colouring it green.
    """
    if not scores:
        return {"recommended_index": None, "order": [], "decided_by": None, "reason": "No routes available"}

    ordered = rank(scores)
    best = ordered[0]
    result = {
        "recommended_index": best.route_index,
        "order": [s.route_index for s in ordered],
        "decided_by": None,
        "reason": "",
        "viable": best.viable,
    }

    if len(ordered) == 1:
        result["reason"] = "Only one route option was available"
        return result

    runner_up = ordered[1]
    for attr, label in CRITERIA:
        mine, theirs = getattr(best, attr), getattr(runner_up, attr)
        precision = RISK_PRECISION if attr == "avg_risk" else (MILE_PRECISION if attr.endswith("miles") else 0)
        if round(mine, precision) != round(theirs, precision):
            result["decided_by"] = attr
            result["reason"] = _explain(attr, label, mine, theirs)
            break
    else:
        result["decided_by"] = None
        result["reason"] = "Routes scored identically; kept the provider's first route"

    if not best.viable:
        result["reason"] = ("Every route has a No Travel checkpoint. "
                            "This is the least-exposed option, but the rules say do not drive it.")
    return result


def _explain(attr: str, label: str, mine: float, theirs: float) -> str:
    if attr == "duration_s":
        return f"Fastest of the equally safe options, by {abs(theirs - mine) / 60:.0f} min"
    if attr == "avg_risk":
        return f"Lowest {label} ({mine:.2f} against {theirs:.2f})"
    if mine == 0:
        return f"The only option with no {label}"
    return f"Fewest {label} ({mine:.0f} against {theirs:.0f})"
