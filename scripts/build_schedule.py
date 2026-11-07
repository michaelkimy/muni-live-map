"""Build app/data/schedule.bin.gz (Muni's timetable) from the downloaded GTFS.

Usage (after scripts/fetch_gtfs.py):
    .venv/bin/python scripts/build_schedule.py

The server uses it to show trips that haven't started yet (see app/schedule.py).
Rebuild and commit it whenever GTFS is refreshed. The current feed is valid
until the end_date in data/raw/gtfs/feed_info.txt.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.schedule import DEFAULT_PATH, Schedule, write_schedule  # noqa: E402

GTFS = ROOT / "data" / "raw" / "gtfs"


def read(name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(GTFS / name, dtype=str, encoding="utf-8-sig", keep_default_na=False, **kwargs)


def to_seconds(hms: pd.Series) -> pd.Series:
    """'7:15:00' or '25:10:00' -> seconds after service-day midnight."""
    parts = hms.str.split(":", expand=True).astype(int)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def main() -> None:
    trips = read("trips.txt", usecols=["trip_id", "route_id", "trip_headsign", "service_id"])
    trips = trips.reset_index(drop=True)
    trip_index = pd.Series(trips.index, index=trips["trip_id"])

    st = read("stop_times.txt", usecols=["trip_id", "arrival_time", "departure_time", "stop_id"])
    st["time"] = st["arrival_time"].where(st["arrival_time"] != "", st["departure_time"])
    st = st[st["time"] != ""]
    st["secs"] = to_seconds(st["time"])
    st["trip_idx"] = trip_index.reindex(st["trip_id"]).values
    st = st.dropna(subset=["trip_idx"])

    stop_rows = {
        stop_id: list(zip(g["trip_idx"].astype(int), g["secs"].astype(int)))
        for stop_id, g in st.groupby("stop_id")
    }

    calendar = {
        r.service_id: {
            "days": [int(getattr(r, d)) for d in
                     ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")],
            "start": r.start_date,
            "end": r.end_date,
        }
        for r in read("calendar.txt").itertuples()
    }
    exceptions: dict[str, dict[str, list[str]]] = {}
    for r in read("calendar_dates.txt").itertuples():
        kind = "add" if r.exception_type == "1" else "remove"
        exceptions.setdefault(r.date, {"add": [], "remove": []})[kind].append(r.service_id)

    DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_schedule(
        DEFAULT_PATH,
        trips[["trip_id", "route_id", "trip_headsign", "service_id"]].values.tolist(),
        calendar, exceptions, stop_rows,
    )
    loaded = Schedule.load(DEFAULT_PATH)
    print(f"{DEFAULT_PATH.relative_to(ROOT)}: {DEFAULT_PATH.stat().st_size / 1e6:.1f} MB, "
          f"{len(loaded.secs):,} stop times, {len(loaded.trips):,} trips, {len(loaded.stops):,} stops")


if __name__ == "__main__":
    main()
