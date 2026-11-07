# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@PLAN.md

## Session workflow

PLAN.md (imported above) is the project's phased plan and progress tracker.

- At the start of each session, find the first phase in PLAN.md with unchecked tasks. That is the current phase. Tell the user which task is next.
- Work on one feature per session. Read `SPEC.md` before implementing anything: it is the source of truth for API shapes, module responsibilities, the rate-limit budget and the folder layout. If the code needs to differ from the spec, update SPEC.md in the same commit.
- When a task is done and verified (its phase's "Done when" condition is met and tests pass), change its `- [ ]` to `- [x]` in PLAN.md and commit that edit together with the work.
- Don't tick off partially done tasks. Don't tick off user-only tasks (getting the API key, installing tools) until the user confirms them.
- When something breaks, explain the cause before fixing it.

## Commands

Use the project virtualenv (Python 3.13): run everything with `.venv/bin/python`, or run `source .venv/bin/activate` first. The system `python3` is 3.8; don't use it. Recreate the venv with `python3.13 -m venv .venv`.

Phase 1 scripts use only the standard library (plus `certifi` when available: the python.org macOS Python has no CA bundle, so use `_env.urlopen`, not `urllib.request.urlopen`) and read `API_KEY_511` from `.env`. Each live call spends one 511 request.

- `.venv/bin/python scripts/fetch_vehicles.py`: fetch live vehicles, save the raw JSON to `data/raw/`, and print a table plus counts of missing fields
- `.venv/bin/python scripts/fetch_vehicles.py data/raw/vehicles_X.json`: re-parse a saved response without calling 511 (use this while iterating)
- `.venv/bin/python scripts/fetch_gtfs.py`: download and unzip Muni GTFS to `data/raw/gtfs/` and summarize routes, shapes, and stops
- `.venv/bin/python scripts/build_schedule.py`: rebuild `app/data/schedule.bin.gz` (the timetable used for trips that haven't started). The current GTFS is valid until **2027-01-15**; before then, rerun `fetch_gtfs.py`, both build scripts, and commit the output.
- `.venv/bin/python scripts/build_geojson.py`: rebuild `static/data/` (routes.geojson, stops.geojson, routes.json) from the downloaded GTFS. Rerun it after Muni changes service, and commit the output.

Backend and tests:

- `.venv/bin/pip install -r requirements-dev.txt`: install runtime and test dependencies (pinned)
- `.venv/bin/uvicorn app.main:app --reload`: run the server at http://localhost:8000. It polls 511 every 75 s, about 48 requests/hour while running. Set `POLLER_ENABLED=false` to run it without calling 511.
- `.venv/bin/python -m pytest`: run all tests (offline; `conftest.py` disables the poller)
- `.venv/bin/python -m pytest tests/test_parse.py::test_no_route_vehicles_are_dropped`: run a single test

`app.main.create_app(settings, state)` is the app factory. Tests build their own `AppState` and fill it with `poll_once()` and a fake client, so no network is needed.

## Must-not-break rules

These are covered in detail in SPEC.md.

- Only `app/client.py` calls 511, and every call counts against the shared hourly budget. Endpoints serve from the in-memory cache; the vehicle poller always has priority over predictions.
- Never log 511 request URLs, because they contain the API key.
- Run a single process/worker, because all state is in memory.
- Plain HTML/JS frontend with Leaflet and no build tools or frameworks. The user is learning JavaScript, so explain non-obvious JS when you write it.
- No SFMTA/Muni logos or branding. The site is unofficial.

## 511.org API gotchas

- 511's JSON starts with a UTF-8 byte-order mark. Decode with `utf-8-sig` before parsing.
- Some vehicle fields are sometimes missing, so handle absent values.

## Conventions

- The 511 API key lives in `.env`, which must be in `.gitignore` and never committed. In deployment, set it as a host environment variable.
- Tests use saved sample 511 responses, never live API calls.

## Data reference

`notes/data.md` records what the live 511 feed and the GTFS files actually contain: field paths, presence counts, how IDs join to GTFS, and quirks such as vehicles with no route and the lack of per-vehicle timestamps. Check it before writing parsing code. `tests/fixtures/vehicles_sample.json` is a trimmed real response that includes the BOM and the edge cases.

## Deployment

- Live at **https://muni-live-map.onrender.com** (Render free plan, `render.yaml`). Code is at https://github.com/michaelkimy/muni-live-map (public).
- Every push to `main` redeploys automatically, which takes about 1–2 minutes. `API_KEY_511` is set only in Render's dashboard.
- **The live site and a local server share one 511 key (60 requests/hour) and must not both poll.** For local work while the site is up, run `POLLER_ENABLED=false .venv/bin/uvicorn app.main:app --reload`. That shows the page and data files without calling 511. Check `remaining_511` at `/api/health` for 511's own count.
- Pushing uses HTTPS with a token stored in the macOS Keychain. This repo sets `http.version=HTTP/1.1` because git 2.28 failed larger pushes over HTTP/2 with `RPC failed; HTTP 400`.
- Python scripts that fetch over HTTPS need certifi (`scripts/_env.urlopen`), because the python.org macOS Python has no CA bundle.
