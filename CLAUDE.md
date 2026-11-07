# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@PLAN.md

## Session workflow

PLAN.md (imported above) is the project's phased plan and progress tracker.

- At the start of each session, find the first phase in PLAN.md with unchecked tasks. That is the current phase. Tell the user which task is next.
- Work on one feature per session. Once `SPEC.md` exists (Phase 2), read it as well.
- When a task is done and verified (its phase's "Done when" condition is met and tests pass), change its `- [ ]` to `- [x]` in PLAN.md and commit that edit together with the work.
- Don't tick off partially done tasks. Don't tick off user-only tasks (getting the API key, installing tools) until the user confirms them.
- When something breaks, explain the cause before fixing it.

## Commands

Use the project virtualenv (Python 3.13): run everything with `.venv/bin/python`, or run `source .venv/bin/activate` first. The system `python3` is 3.8; don't use it. Recreate the venv with `python3.13 -m venv .venv`.

Phase 1 scripts use only the standard library and read `API_KEY_511` from `.env`. Each live call spends one 511 request.

- `.venv/bin/python scripts/fetch_vehicles.py`: fetch live vehicles, save the raw JSON to `data/raw/`, and print a table plus counts of missing fields
- `.venv/bin/python scripts/fetch_vehicles.py data/raw/vehicles_X.json`: re-parse a saved response without calling 511 (use this while iterating)
- `.venv/bin/python scripts/fetch_gtfs.py`: download and unzip Muni GTFS to `data/raw/gtfs/` and summarize routes, shapes, and stops

No lint or test commands exist yet.

## Architecture rules

- The backend polls 511 on a timer (60–90 s) into an in-memory cache. `/vehicles` and other endpoints serve **only from the cache** and never call 511 per request. 511's default key allows about 60 requests/hour in total.
- `/predictions/{stop_id}` calls 511 on demand, so it must cache each stop's result for 1–2 minutes and respect a shared hourly request budget. The vehicle poller always gets priority.
- The frontend is plain HTML/JS with Leaflet, with no build tools or frameworks. The user is learning JavaScript, so explain non-obvious JS when you write it.

## 511.org API gotchas

- 511's JSON starts with a UTF-8 byte-order mark. Decode with `utf-8-sig` before parsing.
- Some vehicle fields are sometimes missing, so handle absent values.

## Conventions

- The 511 API key lives in `.env`, which must be in `.gitignore` and never committed. In deployment, set it as a host environment variable.
- Tests use saved sample 511 responses, never live API calls.
- `/vehicles` returns a slimmed JSON list, not 511's verbose format.

## Data reference

`notes/data.md` records what the live 511 feed and the GTFS files actually contain: field paths, presence counts, how IDs join to GTFS, and quirks such as vehicles with no route and the lack of per-vehicle timestamps. Check it before writing parsing code. `tests/fixtures/vehicles_sample.json` is a trimmed real response that includes the BOM and the edge cases.
