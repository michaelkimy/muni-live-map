# Muni Live Map: Implementation Plan

## Overview and architecture

A public web app showing every Muni bus and train live, with route paths, line filters, vehicle details and stop arrival predictions. The backend is Python; Claude Code writes most of the JavaScript, which you learn by reviewing its work.

- **Python backend (FastAPI):** fetches Muni data from the 511.org API on a timer, caches it, and serves it to the site. It exists because 511's default key allows about 60 requests per hour (verify the current limit). One request per minute serves any number of visitors.
- **Static route file:** route shapes, colors and stop locations from Muni's GTFS schedule data, preprocessed once into GeoJSON.
- **Web frontend:** one HTML page using Leaflet with OpenStreetMap tiles, polling the backend every 30 to 60 seconds and redrawing the vehicles.

Streamlit would keep everything in Python, but live updates and click interactions get clunky. FastAPI plus Leaflet is also the likely stack for the rental-market map, so the practice transfers.

## Phase 0: Setup (one evening)

- [x] Request a free 511.org API key from its open data page. It arrives by email, sometimes not right away, so start here.
- [x] Install Python 3.11+, Git, a GitHub account, an editor, and Claude Code per Anthropic's official setup docs.
- [x] Make an empty project folder, run `git init`, start Claude Code inside it, and run `/init` to create CLAUDE.md for project notes across sessions.

## Phase 1: Explore the data in Python (one or two sessions)

Done when you can print a table of every Muni vehicle running right now.

- [x] Have Claude Code write a script calling 511's VehicleMonitoring endpoint (agency `SF`, `format=json`) and save the response. Decode with `utf-8-sig`: 511's JSON starts with a byte-order mark that breaks parsing.
- [x] In a notebook, find the fields you need: latitude, longitude, route ID, direction, destination, vehicle ID, next stop, timestamp. Note which are sometimes missing.
- [x] Download Muni's static GTFS feed (511 datafeeds endpoint or SFMTA's site) and inspect `routes.txt` (names, colors), `shapes.txt` (paths) and `stops.txt` (locations).

## Phase 2: Write the spec with Claude Code (one session)

The most important habit to practice: a clear spec makes every later prompt shorter and the output better.

- [x] In plan mode, discuss the architecture, then have Claude Code write `SPEC.md`: features, data sources, the rate-limit constraint, folder structure.
- [x] Record conventions in CLAUDE.md: API key in `.env`, never committed; FastAPI backend; plain HTML/JS frontend with Leaflet, no build tools.
- [x] Add `.env` to `.gitignore` immediately.

## Phase 3: Backend MVP

Done when `localhost:8000/api/vehicles` shows fresh data in your browser. (SPEC.md moved the endpoints under `/api/`.)

- [x] Build a `/vehicles` endpoint returning a clean JSON list with only the Phase 1 fields, not 511's verbose format.
- [x] Add a background poller fetching from 511 every 60 to 90 seconds into an in-memory cache; the endpoint serves only from the cache.
- [x] Have Claude Code write tests that use a saved sample response, so testing doesn't spend your rate limit.

## Phase 4: Frontend MVP

Done when you have a working app; everything after this is a feature.

- [x] Create a single `index.html` with a Leaflet map centered on SF.
- [x] Fetch `/vehicles` every 30 seconds and draw a marker per vehicle. Ask Claude Code to explain the JavaScript it writes.
- [x] Commit and tag this version.

## Phase 5: Route paths

- [x] Write a one-time Python script turning `shapes.txt` and `routes.txt` into a GeoJSON file of colored route lines (GeoPandas helps, and is good practice for the real estate project).
- [x] Serve the file as static data, draw the lines under the vehicle markers, and color each marker to match its route.

## Phase 6: Filter by line

- [x] Add a searchable route picker grouped by type: bus, light rail (N Judah, T Third), cable car, streetcar.
- [x] Selecting routes shows only their lines and vehicles.
- [x] Bonus: save the filter in the URL so links like "show me the 38 Geary" are shareable.

## Phase 7: Click a vehicle for details

- [x] Clicking a vehicle opens a popup: route, direction, destination, next stop, "updated X seconds ago."
- [x] Fade any vehicle whose position is more than a few minutes old. (Changed later: a dashed outline instead of fading, so nothing is transparent.)

## Phase 8: Arrival predictions

The trickiest feature, because of the rate limit.

- [x] Show stops as small dots only when zoomed in; all 3,000+ at once clutters the map.
- [x] Clicking a stop calls `/predictions/{stop_id}`, which calls 511's StopMonitoring and caches each stop's result for a minute or two.
- [x] Track the hourly request count so predictions never starve the vehicle poller; when the budget runs low, show "predictions temporarily unavailable." With real traffic, request a higher limit from 511 by email.

## Phase 9: Polish

- [x] Add loading indicators, an error banner if 511 is down, a "last updated" timestamp, and a phone-friendly layout.
- [x] Ask Claude Code to review the whole codebase for bugs, security problems (especially key handling) and cleanup.

## Phase 10: Deploy publicly

- [x] Push to GitHub after confirming `.env` isn't in the repo.
- [x] Deploy to Render, Railway or Fly.io and set the 511 key as an environment variable in the host's dashboard. Free tiers sleep when idle, so the first visit after a lull may take a few seconds.
- [ ] Share the link with a few friends and fix whatever confuses them.

## Habits and timeline

Expect two or three weekends for Phases 0 through 4, and one to two months for the full plan at side-project pace.

- Work on one feature per session, and start each session by having Claude Code read SPEC.md and PLAN.md.
- Commit after every working step so you can always roll back.
- When something breaks, paste the full error and ask Claude Code to explain the cause before fixing it.
