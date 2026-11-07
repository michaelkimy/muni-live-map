# Muni Live Map: Specification

This is the source of truth for what we're building and how the pieces fit together. `PLAN.md` covers *when* each piece gets built. `notes/data.md` covers what the raw data looks like.

## 1. Goal and non-goals

**Goal:** a public web page showing every in-service SF Muni bus, light-rail train, streetcar and cable car live on a map. It also shows route lines, a line filter, vehicle details and stop arrival predictions.

**Non-goals:** other Bay Area agencies, user accounts, trip planning, historical playback, a database, and a native app.

## 2. Features and acceptance criteria

| Phase | Feature | Done when |
|---|---|---|
| 3 | Backend vehicles API | `GET /api/vehicles` returns in-service vehicles from cache. 511 is called only by the poller. Tests pass offline. |
| 4 | Map with vehicles | `localhost:8000` shows a Leaflet map of SF with a route-number badge per vehicle. The markers refresh every 30 s without flicker. |
| 5 | Route lines | Every route is drawn in its official GTFS color beneath the vehicles, and vehicle badges use the same color. |
| 6 | Line filter | A searchable picker groups routes by bus, light rail, streetcar and cable car. Selecting routes shows only their lines and vehicles. The selection is in the URL (`#routes=38,N`) so it can be shared. |
| 7 | Vehicle popup | Clicking a vehicle shows the route, direction, destination, next stop and "moved X s ago". Vehicles that haven't moved in more than 3 minutes are faded. |
| 8 | Stops and predictions | Stops appear at zoom 16 and closer. Clicking a stop shows the upcoming arrivals, or "predictions temporarily unavailable" when the budget is used up. |
| 9 | Polish | The page has a loading state, an error banner when 511 is down or the data is stale, a "last updated" time and a phone-friendly layout. |
| 10 | Deploy | The site is public on Render, Railway or Fly.io, and the key is set only in the host's environment. |

## 3. Data sources

| Source | Used for | Cost |
|---|---|---|
| 511 `transit/VehicleMonitoring?agency=SF&format=json` | vehicle positions and next stops, plus upcoming stops in `OnwardCalls` | 1 request per poll |
| 511 `transit/StopMonitoring?agency=SF&stopcode=<id>&format=json` | fallback predictions for one stop | 1 request per stop, cached |
| 511 `transit/datafeeds?operator_id=SF` (GTFS zip) | route shapes, colors, stops, and the timetable for trips that haven't started | only when the static data is rebuilt by hand |

See `notes/data.md` for field paths, presence counts and quirks. These include the byte-order mark (BOM), vehicles with no route, the lack of per-vehicle timestamps, and T Third's N/S directions.

## 4. Rate-limit budget (core constraint)

511's default key allows about **60 requests per hour**. This number still needs verifying, so the code reads it from config. The app protects the limit as follows:

- **One shared counter** records the times of all 511 calls in a sliding 60-minute window. Every call goes through `client.py`, which checks and records it.
- **The vehicle poller** runs every `POLL_INTERVAL_S` (default 75 s), which is about 48 calls per hour. It always gets priority.
- **The prediction budget** is `RATE_LIMIT_PER_HOUR − ceil(3600 / POLL_INTERVAL_S) − 2`, so about 10 per hour by default. A StopMonitoring call is allowed only while the predictions made in the window stay under this budget. Otherwise the endpoint returns `source: "unavailable"`.
- **Predictions come from the feed first.** Before spending a request, the endpoint combines two free sources. The first is the cached vehicle snapshot (`MonitoredCall` + `OnwardCalls`), which gives live times for buses already on their trip. The second is the GTFS timetable (`app/schedule.py`), which covers trips that haven't started, since buses waiting at their first stop aren't in the feed. If the timetable is current, its answer is final, including "nothing in the next 90 minutes" (some stops are served once a day). StopMonitoring is only used when the timetable is missing or expired. Those results are cached per stop for 90 s.
- **Empty answers:** a 200 response with no vehicles is treated as a 511 failure. The last snapshot is kept and goes stale, rather than blanking the map.
- **Errors:** on a 429, 5xx or network error, the poller keeps the last good snapshot and backs off exponentially, up to 10 minutes. The API marks data `stale` once the snapshot is older than 3 poll intervals.
- **Tests never call 511.**

## 5. Backend (`app/`)

Python 3.13 with FastAPI and uvicorn, plus httpx for the 511 calls. It runs as **a single process** because all state lives in memory.

| Module | Responsibility |
|---|---|
| `config.py` | Settings from the environment or `.env`: `API_KEY_511` (required), `POLL_INTERVAL_S`, `RATE_LIMIT_PER_HOUR`, `POLLER_ENABLED` (tests set this to false). |
| `client.py` | The only code that talks to 511. It decodes with `utf-8-sig`, enforces and records the budget, and never logs request URLs, because they contain the key. |
| `parse.py` | Pure functions: SIRI dict → vehicle list (dropping vehicles with no route; lat, lon and bearing as floats), and SIRI dict → arrivals for a stop. No I/O. |
| `state.py` | The in-memory store: latest vehicles, `updated_at`, `last_moved` per vehicle id, the per-stop prediction cache and the budget counter. |
| `schedule.py` | Loads `app/data/schedule.bin.gz` (built by `scripts/build_schedule.py`; stdlib only, about 75 MB in memory). It returns timetable arrivals at a stop for the next 90 min, honoring service calendars, holiday exceptions and after-midnight trips, and skips trips that are live. |
| `predictions.py` | Stop predictions: the vehicle feed's arrivals plus timetable arrivals for unstarted trips first, then a 90 s per-stop cache, then StopMonitoring if the prediction budget allows. One lock prevents duplicate fetches. |
| `main.py` | The FastAPI app. Its lifespan starts and stops the background poller task. It mounts `static/` and defines the endpoints. |

**`last_moved`:** the feed has no per-vehicle GPS time. On each poll, if a vehicle's lat/lon changed or it is new, set `last_moved = now`. Otherwise keep the previous value. A vehicle missing from a poll is dropped from the snapshot.

## 6. API contract

All times are ISO 8601 UTC strings.

```
GET /api/vehicles
{
  "now": "2026-11-07T04:09:40Z",
  "updated_at": "2026-11-07T04:09:15Z",
  "stale": false,
  "vehicles": [
    {"id": "5743", "route": "1", "direction": "IB", "destination": "Clay St & Drumm St",
     "lat": 37.77985, "lon": -122.493141, "bearing": 45.0,
     "next_stop_id": "13544", "next_stop_name": "Geary Blvd & 33rd Ave",
     "last_moved": "2026-11-07T04:08:00Z"}
  ]
}
```
`now` is the server's clock. The frontend computes "moved X ago" as `now − last_moved`, plus the time since the response arrived, so a wrong phone clock doesn't matter. `next_stop_id` and `next_stop_name` may be `null`. Vehicles without a route are never included.

```
GET /api/predictions/{stop_id}
{
  "stop_id": "15694",
  "now": "...",
  "source": "feed" | "511" | "unavailable",
  "updated_at": "...",
  "arrivals": [{"route": "F", "destination": "Castro", "vehicle_id": "1010", "expected": "...", "scheduled": false}]
}
```
Arrivals are sorted by `expected`. Times up to 60 s in the past are still shown (as "Now"), with at most 10 arrivals. `scheduled: true` means the trip hasn't started, so the time is the timetable's. `vehicle_id` is `null` for those, and the popup shows them as "~N min" in grey. If the source is `unavailable`, `arrivals` is `[]`. Stop ids must be numeric and exist in `static/data/stops.geojson`; otherwise the response is 404, so made-up ids can't spend budget.

```
GET /api/health
{"poller": "ok" | "backoff" | "disabled", "snapshot_age_s": 42, "requests_last_hour": 47, "prediction_budget_left": 6}
```

Static files are served from `static/` at `/`, so `/` serves `index.html` and the data files are under `/data/`.

## 7. Static route data (`scripts/build_geojson.py`)

This is a one-off script run by hand after downloading GTFS with `scripts/fetch_gtfs.py`. Its outputs are committed:

- `static/data/routes.geojson`: one feature per route. The geometry is a MultiLineString of that route's distinct shapes (linked via `trips.txt`), simplified to keep the file small. Properties: `route_id`, `short_name`, `long_name`, `color` (`#RRGGBB`), `text_color`, `category`.
- `static/data/stops.geojson`: Point features with `stop_id`, `name` and the `routes` serving the stop.
- `static/data/routes.json`: a compact route list for the filter UI, sorted naturally (1, 2, …, 14, 14R, …).

`category` comes from `route_type`: `3` → bus, `0` → light rail, `5` → cable car. The **F is overridden to streetcar**, because GTFS calls it type 0. The script uses GeoPandas, installed as a dev dependency only and not needed at runtime.

## 8. Frontend (`static/`)

Plain HTML, CSS and JS with no framework or build step. The files are `index.html`, `app.js`, `style.css` and `data/`. Leaflet is loaded from a pinned CDN version over OpenStreetMap tiles.

- **Markers:** a badge showing the route name (13px bold, or 12px and 10px for 3- and 4-character names), in the route's GTFS `color` with its `text_color`. Buses are round and rail (light rail, streetcar, cable car) is a rounded square. A small arrow outside the badge points along `bearing`.
  - **Don't use the SFMTA/Muni logo or branding**: it's trademarked, and the site must not look like an official SFMTA product. The page footer says it is unofficial and credits 511.org and OpenStreetMap.
- **Polling:** fetch `/api/vehicles` every 30 s and update markers by vehicle id: move the existing ones, add new ones, remove missing ones. Never clear and redraw everything.
- **Layers:** route lines go below vehicles. Stops are drawn only at zoom 16 and closer, and only for stops in view.
- **State:** the filter selection lives in `location.hash`.
- **Pick on the map:** clicking a route line selects only that route; Shift-click adds or removes it. Every visible line within 8px of the click (16px on touch screens) counts. If several routes overlap there (e.g. 38/38R on Geary), a small "Which route?" popup lists them. Vehicle and stop clicks don't reach the map, so they never pick a route.
- **Filter panel:** compact (220px wide, at most 420px tall, with the list scrolling inside). Each category heading is a checkbox that selects or clears all of that category's routes. It shows a dash when only some are selected. After any selection change, the map zooms to fit the selected lines, or back to the whole city when the selection is cleared. The zoom waits about 350 ms after the last click so several quick clicks give one zoom. Shared links zoom on load without animation.
- **Staleness:** fade a vehicle when `now − last_moved > 3 min`. Show a banner when `stale` is true or the fetch fails.
- **Mobile:** a full-screen map with the filter in a collapsible panel.

### Security headers
Every response sets a Content-Security-Policy. It allows scripts only from this site and unpkg (Leaflet, pinned with SRI), images from OpenStreetMap tiles, and API calls only to this site; inline styles are allowed because Leaflet needs them. Responses also set `X-Content-Type-Options: nosniff` and `Referrer-Policy: strict-origin-when-cross-origin`. OSM's tile policy needs the referer.

## 9. Folder structure

```
app/            FastAPI backend (section 5)
static/         frontend + generated data (sections 7–8)
  data/         routes.geojson, stops.geojson, routes.json (committed)
app/data/       schedule.bin.gz: timetable index built from GTFS (committed)
scripts/        fetch_vehicles.py, fetch_gtfs.py, build_geojson.py, build_schedule.py
tests/          pytest; fixtures/ holds saved 511 responses
notes/          data findings
data/raw/       downloaded 511 responses and GTFS (gitignored)
requirements.txt        runtime: fastapi, uvicorn, httpx, python-dotenv
requirements-dev.txt    pytest, geopandas
```

## 10. Testing

pytest, run from `.venv`. Tests make no network calls: the HTTP layer is mocked and the poller is disabled with `POLLER_ENABLED=false`.

- `parse.py`, using `tests/fixtures/vehicles_sample.json`: no-route vehicles are dropped, a missing `MonitoredCall` gives `null` next stop, T Third's N/S directions pass through, and floats are converted.
- The budget: predictions are refused once the prediction budget is used up, and the poller is never blocked by predictions.
- `last_moved`: it stays the same when the position is unchanged and updates when the vehicle moves.
- The endpoints via FastAPI `TestClient`: response shapes match section 6, and predictions come from the feed before 511.

## 11. Deployment

- Run a single uvicorn worker. More workers would mean separate caches and several times the 511 usage.
- Set `API_KEY_511` in the host dashboard. Never commit `.env`.
- Free tiers sleep when idle. After waking, the first request may get a `stale` or empty snapshot until the first poll completes.

## 12. Open questions

- Verify 511's current default rate limit. Request a higher one by email if real traffic arrives.
- Decide whether StopMonitoring fallback is needed at all once we see how many stops the feed's `OnwardCalls` covers.
