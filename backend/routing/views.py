"""HTTP layer: request parsing, error mapping, JSON out.

Plain Django views rather than DRF. There are no models, no auth and one real
endpoint, so a serializer framework would add a dependency without removing any
code. All the work lives in `planner`, which these views only translate for.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from django.http import JsonResponse
from django.shortcuts import render
from django.template import TemplateDoesNotExist
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import planner
from .domain.checkpoints import SAMPLE_INTERVALS_MILES
from .services.mapbox import MapboxError
from .services.mapbox import token as mapbox_token
from .services.weather import WeatherError

log = logging.getLogger(__name__)

MAX_LOAD_LB = 200_000       # well above any legal US gross weight; catches typos


@require_GET
def config(request):
    """Public settings the browser needs, so no key is baked into the bundle."""
    try:
        token = mapbox_token()
    except MapboxError as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    return JsonResponse({
        "mapbox_token": token,
        "intervals_miles": list(SAMPLE_INTERVALS_MILES),
        "slider_hours": planner.weather.SLIDER_HOURS,
    })


@csrf_exempt            # stateless JSON API: no cookies, no session, nothing to forge
@require_POST
def plan(request):
    """Plan a trip. See `planner.plan` for the shape of the response."""
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Request body must be JSON."}, status=400)

    try:
        params = _parse(body)
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    try:
        return JsonResponse(planner.plan(**params))
    except planner.PlanError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except MapboxError as exc:
        # The trip could not be routed or geocoded: the user can act on this.
        return JsonResponse({"error": str(exc)}, status=502)
    except WeatherError as exc:
        return JsonResponse({"error": str(exc)}, status=502)
    except Exception:
        log.exception("unexpected failure while planning a trip")
        return JsonResponse({"error": "Something went wrong planning this trip."}, status=500)


def _parse(body: dict) -> dict:
    """Validate the request body, raising ValueError with a message for the user."""
    origin = str(body.get("origin") or "").strip()
    destination = str(body.get("destination") or "").strip()
    if not origin or not destination:
        raise ValueError("Both an origin and a destination are required.")

    raw_departure = body.get("departure")
    if not raw_departure:
        raise ValueError("A departure date and time is required.")
    departure = parse_datetime(str(raw_departure))
    if departure is None:
        raise ValueError("Departure must be an ISO 8601 date and time.")
    if departure.tzinfo is None:
        departure = departure.replace(tzinfo=timezone.utc)

    try:
        load_lb = float(body.get("load_lb"))
    except (TypeError, ValueError):
        raise ValueError("Load weight must be a number, in pounds.") from None
    if not 0 <= load_lb <= MAX_LOAD_LB:
        raise ValueError(f"Load weight must be between 0 and {MAX_LOAD_LB:,} lb.")

    try:
        interval_miles = float(body.get("interval_miles", 25))
    except (TypeError, ValueError):
        raise ValueError("Sampling interval must be a number of miles.") from None
    if interval_miles not in SAMPLE_INTERVALS_MILES:
        raise ValueError(f"Sampling interval must be one of {SAMPLE_INTERVALS_MILES} miles.")

    return {
        "origin_query": origin,
        "destination_query": destination,
        "departure": departure,
        "load_lb": load_lb,
        "interval_miles": interval_miles,
    }


@require_GET
def index(request):
    """Serve the built single-page app.

    In development the app is served by Vite on its own port and this template
    does not exist yet, so say so plainly instead of raising a 500.
    """
    try:
        return render(request, "index.html")
    except TemplateDoesNotExist:
        return JsonResponse(
            {"error": "Frontend is not built. Run `npm run dev` in frontend/, or "
                      "`npm run build` to serve it from here."},
            status=503,
        )
