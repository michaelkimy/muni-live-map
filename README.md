# Muni Live Map (unofficial)

A live map of every San Francisco Muni bus, train, streetcar and cable car. It also shows route lines, a route filter you can share as a link, vehicle details, and stop arrival predictions.

This is an unofficial personal project. It is not affiliated with SFMTA or Muni. Vehicle data comes from [511.org](https://511.org/open-data/transit), and the map is © [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors.

## How it works

- **Backend (FastAPI):** polls 511's VehicleMonitoring feed every 75 s into memory. Visitors are served from that cache, so traffic never adds 511 requests. The 511 key allows 60 requests/hour.
- **Arrivals:** live times come from the vehicle feed. Trips that haven't started come from Muni's GTFS timetable. 511 is only asked for a stop if the timetable is missing or expired, and that is capped.
- **Frontend:** a single page in plain HTML/JS with [Leaflet](https://leafletjs.com) and no build step.

`SPEC.md` has the design, and `notes/data.md` describes what the 511 and GTFS data actually look like.

## Run locally

Requires Python 3.13 and a free [511.org API key](https://511.org/open-data/token).

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
echo "API_KEY_511=your_key_here" > .env      # never commit this file
.venv/bin/uvicorn app.main:app --reload       # http://localhost:8000
.venv/bin/python -m pytest                    # tests run offline
```

## Refreshing the timetable

Muni's GTFS feed expires (the current one ends 2027-01-15). To refresh it:

```bash
.venv/bin/python scripts/fetch_gtfs.py
.venv/bin/python scripts/build_geojson.py
.venv/bin/python scripts/build_schedule.py
```

Then commit the files that changed under `static/data/` and `app/data/`.

## Deploy

`render.yaml` deploys a single free web service on [Render](https://render.com). Set `API_KEY_511` in Render's dashboard. Run exactly one instance, because a second copy would double the 511 polling.
