# Weather-Aware Truck Routing

Pick the safest route for a loaded truck, scored on the weather each route will
actually meet — not the weather at the moment you press the button.

Enter an origin, a destination, a departure time and a load weight. The app
generates three route options, samples the forecast every 10, 25 or 50 miles
**at the time the truck arrives at each point**, classifies every sample against
the risk and load rules, and recommends one route with the reason it won.

---

## Quick start

```bash
git clone <this repo>
cd weather-aware-truck-routing
cp .env.example .env          # then put a Mapbox token in it
```

A free Mapbox public token (`pk.…`) is the only credential needed —
<https://account.mapbox.com/access-tokens/>. Open-Meteo needs no key.

**Backend**

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r backend/requirements.txt   # Windows
# source .venv/bin/activate && pip install -r backend/requirements.txt

cd backend
python manage.py runserver 8000
```

**Frontend** (second terminal)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Vite proxies `/api` to the backend, so the browser sees one origin.

**Tests**

```bash
cd backend && python -m unittest discover -s routing/tests -t .
cd frontend && npm run typecheck
```

---

## How it works

```
  origin, destination, departure, load weight, sampling interval
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────┐
  │ services/mapbox.py   geocode both ends                        │
  │                      3 distinct route options, full geometry  │
  │                      with per-segment distance and duration   │
  └───────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────┐
  │ domain/checkpoints.py   walk the duration curve and place a   │
  │                         checkpoint every N miles, each with   │
  │                         the exact time the truck gets there   │
  └───────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────┐
  │ services/weather.py   ONE batched Open-Meteo fetch covering   │
  │                       every route and all 48 slider hours     │
  └───────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌───────────────────────────────────────────────────────────────┐
  │ domain/risk.py        classify each checkpoint, apply load    │
  │ domain/recommend.py   rank routes, explain the winner         │
  └───────────────────────────────────────────────────────────────┘
```

The single weather fetch is the design. Checkpoint risk, route scoring and every
hour of the heatmap all read the same in-memory result, so the browser never
needs a second request and the slider never touches the network.

### Layout

```
backend/
  config/                 Django project (no database: nothing is stored)
  routing/
    domain/               pure logic - no network, no Django, no I/O
      risk.py             the risk table and the load rules
      checkpoints.py      sampling along a route, and arrival times
      recommend.py        scoring and the preference order
      geo.py              geodesics and polyline simplification
    services/             everything that talks to a provider
      mapbox.py           geocoding, directions, the third-route fallback
      weather.py          batched Open-Meteo fetch, grid cache
    planner.py            the one place services and domain meet
    views.py  urls.py     HTTP: parse, map errors, return JSON
    tests/                76 tests, all against domain/
frontend/
  src/
    components/           TripForm, RouteMap, RouteSummary,
                          CheckpointList, HeatmapSlider
    api/client.ts         typed fetch wrapper
    risk.ts               the shared risk palette and formatters
    types.ts              mirrors the API response
```

`domain/` is the part worth reading first. It is pure Python — numbers in,
verdicts out — which is why all 76 tests live there and why the risk table can
be checked line by line against the spec.

---

## The risk rules

| Condition | Low | Moderate | High | Severe | No Travel |
|---|---|---|---|---|---|
| Wind | <25 mph | 25–34 | 35–44 | 45–54 | ≥55 mph |
| Rain | <0.10 in/hr | 0.10–0.25 | 0.25–0.50 | 0.50–1.00 | >1.00 |
| Snow | <0.5 in/hr | 0.5–1.0 | 1.0–2.0 | 2.0–3.0 | >3.0 |

A checkpoint takes the worst of its three readings, then the load rules can
raise it further — never lower it:

- wind ≥ 55 mph → No Travel at any load
- wind 45–54 mph **and** load > 30,000 lb → No Travel
- wind 35–44 mph **and** load > 40,000 lb → Severe

So the same route in the same weather can be drivable empty and undrivable
loaded. The checkpoint list marks every verdict the load changed with `↑`.

### Units

Open-Meteo is asked for `wind_speed_unit=mph` and `precipitation_unit=inch` and
returns mph, inch and inch for wind, rain and snowfall. **Nothing in this app
converts weather units**, so there is no conversion to get wrong. That snowfall
is converted to inches is not stated in the docs — snowfall defaults to
centimetres — so it was confirmed against the live API before being relied on.
Had it not been, every snow reading would have been wrong by a factor of 2.54.

### Boundaries

Bands are lower-bound inclusive and upper-bound exclusive, which is the only
reading of the table that leaves no gaps — a 34.5 mph wind has to land
somewhere. One asymmetry in the table is deliberate and is preserved exactly:

| | written as | so the bound is |
|---|---|---|
| Wind | `≥ 55` | 55.0 is already **No Travel** |
| Rain | `> 1.00` | 1.00 is still **Severe** |
| Snow | `> 3.0` | 3.0 is still **Severe** |

Each of those boundaries has its own test. Getting one backwards silently
downgrades the single most important verdict the app makes.

---

## Choosing a route

Applied strictly in order, so a faster route never wins over a safer one:

1. fewest No Travel miles
2. fewest Severe miles
3. fewest High miles
4. lowest average risk
5. shortest travel time

The response says which criterion actually decided it, and the UI shows that
sentence on the recommended card — "Fewest severe miles (0 against 47)",
"Fastest of the equally safe options, by 115 min".

**Miles, not checkpoints.** Each checkpoint owns the road from the midpoint
behind it to the midpoint ahead, with the first and last reaching the route
ends. Those stretches tile the route exactly — their sum is the route length,
which is asserted in the tests — so "severe miles" is a real distance.

---

## Decisions the spec left open

Each of these is a judgement call, made explicitly rather than silently.

**No Travel ranks above Severe.** The spec lists four criteria starting at
Severe miles. A route carrying a No Travel checkpoint is one the rules say the
truck must not drive, so it cannot win just by having fewer Severe miles. Such
routes are still returned and still scored, but are marked `viable: false`,
sorted last, and labelled "Do not drive" rather than hidden.

**Average risk is weighted by miles.** The first and last checkpoints own half
stretches, so an unweighted mean would over-count the route ends. The plain mean
is also in the payload as `mean_risk`.

**Forecasts are read at the nearest hour.** Hourly data is only accurate to the
hour; nearest rather than preceding halves the worst-case error.

**The heatmap shows weather severity, not the risk band.** Bands are the right
answer for a checkpoint — a driver needs "Severe", not "0.62" — but a band-based
heatmap renders completely empty whenever the weather is fair, which reads as
broken rather than as calm. The heatmap instead uses the worst reading as a
fraction of its own No Travel threshold, so the scale is anchored to the same
numbers as the risk table: 0.45 means wind at roughly 45% of the no-travel
limit. Light wind still registers, so the map shows weather building long before
anything reaches Moderate. Load rules are thresholds on bands and have no
continuous form, so they apply to checkpoints only.

---

## Getting three routes

The spec asks for three. **Mapbox returns two.** Its docs say "up to two
alternatives", and in testing it returned the primary plus exactly one
alternative for every pair tried — 1,000-mile and 180-mile trips alike:

```
Chicago → Denver          2 routes
Seattle → Salt Lake City  2 routes
Dallas → Atlanta          2 routes
Chicago → Indianapolis    2 routes
```

So two come from `alternatives=true`, and the shortfall is made up by routing
through a via-point pushed perpendicular to the corridor at 12% then 28% of the
direct distance, either side. A candidate is rejected unless it strays at least
12 km from every route already accepted, so three options always means three
distinct corridors:

```
r0  1003.1 mi  14.88 h  alternatives
r1  1129.3 mi  16.80 h  alternatives
r2  1196.2 mi  18.31 h  via-offset

separation:  r0–r1 210 km   r0–r2 99 km   r1–r2 254 km
```

If every via-point attempt still collides, a motorway-free route is tried last.
When fewer than three distinct corridors exist at all, the response says so in
`warnings` instead of inventing one.

### Arrival times are exact

`annotations=distance,duration` gives a value per pair of consecutive geometry
points, so cumulative time at any mile mark is real rather than inferred from an
average speed. The annotation totals match the route totals exactly:

```
r0  1003.1 mi  14.88 h   10,385 points   annotation sum 14.88 h
r1  1129.3 mi  16.80 h   11,996 points   annotation sum 16.80 h
```

It matters. On a real Chicago → Denver route the first 100 miles take 1 h 45 m
and the next hundreds take about 1 h 25 m each — leaving a city is slower than
crossing Nebraska. An average-speed estimate puts every checkpoint at the wrong
hour, and therefore reads the wrong forecast.

---

## Performance

All figures measured on the target machine — a two-core i7-6600U with
integrated graphics — not estimated.

### The map

```
map idle                                         60 fps
panning the map, no app code involved            34–38 fps   ← hardware ceiling
dragging the forecast slider, first version      14 fps
dragging the forecast slider, now                29 fps
```

The ceiling is the basemap redrawing at this canvas size. Hiding the heatmap,
the routes and the checkpoints moved it by about two frames, so **what this app
draws is close to free** — the work was all in how updates were delivered.

Three things got it from 14 to 29:

1. **The sidebar stopped re-rendering.** The forecast hour lives in App state,
   so every tick was reconciling the route cards and the whole checkpoint table —
   a few hundred elements per frame. `React.memo` on the components that do not
   depend on the hour, plus stable props, removed it. *14 → 28 fps.*

2. **One heatmap layer per hour instead of one rewritten paint property.**
   Rewriting `heatmap-weight` makes mapbox re-evaluate the expression across
   every feature and re-upload its paint buffers. 48 layers with fixed weight
   expressions are built once (about 80 ms) and the hour toggles visibility.
   *28 → 34 fps measured directly.*

3. **React left the drag path.** Routing the hour through component state cost
   about 5 ms per change for a value only the map consumes. The slider now calls
   the map's imperative handle while the pointer is down and commits to state on
   release; the time label and the strip marker are written straight to the DOM.

The slider also never fetches or recomputes: each corridor point arrives
carrying a weight for all 48 hours as `h0…h47`, so changing the hour only
changes which of them is read.

### Payload

Route geometry comes back from Mapbox at full density. The browser gets a
simplified line while **every distance, arrival time and risk verdict is
computed server-side on the full-precision geometry**, so accuracy is unaffected
and only detail smaller than a pixel is lost:

```
3 routes   34,649 points   846.2 KB   →   1,248 points   30.5 KB   (96.4% smaller)
```

A full response for a 1,000-mile trip sampled every 25 miles is about 127 KB.

### Weather

Latency to Open-Meteo tracks payload almost exactly — on the connection this was
built on, about 3 KB/s:

```
  1 cell   200   1.7 s     2.2 KB
 10 cells  200   6.6 s    22.3 KB
 40 cells  200  30.9 s    89.2 KB
110 cells  200  65.2 s   246.1 KB
```

So the work is to ask for less, and then to ask once:

- **Grid de-duplication.** Sample points snap to a 0.1° grid. Three overlapping
  routes plus the heatmap corridor — 276 points — collapse to 110 forecast cells.
- **Only the three variables the rules read.** Wind gusts were dropped: no rule
  in the spec reads them, and they were a quarter of the payload.
- **An explicit hour window** rather than whole calendar days. For 20 cells,
  44.7 KB → 32.0 KB.
- **Per-cell caching**, so a re-plan that shares a corridor refetches nothing:

```
cold cache   60.3 s
warm cache    1.7 s        36× faster
```

Re-planning usually means changing the load or the interval, which keeps the
same departure and therefore the same cache entries — the common case is the
fast one. On a server with ordinary bandwidth the cold case is a fraction of
this; the slow figures above are a property of the connection, not the design.

**Open-Meteo's free tier** allows 600 calls/minute, 5,000/hour and 10,000/day
per IP, and **every location in a batch counts as a call**. A 1,000-mile trip
sampled every 10 miles is about 280 of them. That is comfortable for interactive
use and will not survive a load test; a commercial key removes the limit. A 429
is reported to the user as a rate limit with the retry delay, not as a crash.

---

## API

### `GET /api/config`

Public settings, so no key is baked into the JS bundle.

```json
{ "mapbox_token": "pk.…", "intervals_miles": [10, 25, 50], "slider_hours": 48 }
```

### `POST /api/plan`

```json
{
  "origin": "Chicago, IL",
  "destination": "Denver, CO",
  "departure": "2026-10-19T16:00:00Z",
  "load_lb": 45000,
  "interval_miles": 25
}
```

Returns `origin`, `destination`, `routes[]`, `recommendation`, `heatmap` and
`warnings[]`. Each route carries its simplified `geometry`, `distance_miles`,
`duration_s`, `arrival`, a `summary` with miles bucketed by risk band, and every
`checkpoint` with its mile mark, `eta`, forecast readings, risk band, the reason
for that band, and whether the load raised it.

Errors return `{"error": "…"}` with a message meant to be shown to the user:
`400` for a bad request, `502` when a provider fails or rate-limits, `500`
otherwise.

---

## Tests

76 tests, all against `domain/`, because that is where correctness lives.

```
test_risk.py          band boundaries one by one, the wind/rain/snow asymmetry,
                      every load rule, load-as-a-floor, missing readings
test_checkpoints.py   exact mile marks, interpolation between vertices,
                      arrival times against a known duration curve,
                      owned stretches summing to the route length
test_recommend.py     each criterion tested with everything above it tied
test_geo.py           distances against independently known values,
                      simplification, route separation
```

The checkpoint tests run against a synthetic route of 99 one-mile, one-minute
segments, so every expected distance and arrival time is known by hand rather
than taken from the implementation's own output.

They also caught a real bug: `resample()` returned only a line's endpoints when
asked for more points than the line had, which made two identical routes look
400 km apart and would have broken route de-duplication in a way nothing visible
would have revealed.

---

## Known limits

- **Forecast resolution.** Hourly, on a ~0.1° grid. Convective storms smaller
  than that are not resolved by the model and cannot be by this app.
- **Departure window.** Up to 10 days out; beyond that the forecast does not
  reach and the request is rejected with that explanation.
- **US only** by default, since the thresholds are in miles and inches. One
  parameter in `mapbox.geocode` widens it.
- **The cache is per process.** Fine for one server; a shared cache would be the
  first change for more than one.
- **Truck restrictions are not routed around.** Mapbox's `max_weight` was tested
  and barely altered the route while making requests less reliable. Load weight
  is used where the spec uses it — in the risk rules — and the routes are
  ordinary driving routes.

## What I would do next

- Share the forecast cache across processes, and warm the common corridors.
- Let a driver shift departure and re-score without re-routing — all the data
  for it is already in the browser.
- Resolve ambiguous place names in the UI instead of taking the first match.
