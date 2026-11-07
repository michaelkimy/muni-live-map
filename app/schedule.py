"""Muni's published timetable, used to fill in trips that haven't started yet.

The live vehicle feed only predicts arrivals for buses already on their trip. A
bus waiting at its first stop isn't in it, so the soonest arrival at stops near
a terminal can be missing. For those trips, the timetable is the best answer
(511 StopMonitoring returns the same thing for them).

The file is built from GTFS by scripts/build_schedule.py and committed as
app/data/schedule.bin.gz. Layout (gzip-compressed):
    JSON header, a newline, then two little-endian int32 arrays of equal length:
    trip index and seconds after service-day midnight, grouped by stop and
    sorted by time within each stop. The header's "stops" maps stop_id to
    [offset, count] in those arrays.
Only the standard library is used, so the server doesn't need pandas.
"""
from __future__ import annotations

import gzip
import json
import sys
from array import array
from bisect import bisect_left
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "schedule.bin.gz"
WINDOW = timedelta(minutes=90)


def _int32(values) -> array:
    arr = array("i", values)
    assert arr.itemsize == 4
    return arr


def write_schedule(path: Path, trips: list[list], calendar: dict, exceptions: dict,
                   stop_rows: dict[str, list[tuple[int, int]]]) -> None:
    """Write a schedule file.

    trips:      [[trip_id, route_id, headsign, service_id], ...] (index = trip index)
    calendar:   service_id -> {"days": [mon..sun as 0/1], "start": "YYYYMMDD", "end": "YYYYMMDD"}
    exceptions: "YYYYMMDD" -> {"add": [service_id...], "remove": [service_id...]}
    stop_rows:  stop_id -> [(trip_index, seconds), ...]
    """
    trip_idx, secs, stops = _int32([]), _int32([]), {}
    for stop_id, rows in stop_rows.items():
        rows = sorted(rows, key=lambda r: r[1])
        stops[stop_id] = [len(secs), len(rows)]
        trip_idx.extend(r[0] for r in rows)
        secs.extend(r[1] for r in rows)
    if sys.byteorder == "big":
        trip_idx.byteswap(); secs.byteswap()
    header = {"trips": trips, "calendar": calendar, "exceptions": exceptions, "stops": stops}
    with gzip.open(path, "wb", compresslevel=9) as f:
        f.write(json.dumps(header, separators=(",", ":")).encode())
        f.write(b"\n")
        f.write(trip_idx.tobytes())
        f.write(secs.tobytes())


class Schedule:
    def __init__(self, header: dict, trip_idx: array, secs: array):
        self.trips = header["trips"]
        self.calendar = header["calendar"]
        self.exceptions = header["exceptions"]
        self.stops = header["stops"]
        self.trip_idx = trip_idx
        self.secs = secs

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Schedule | None":
        if not path.exists():
            return None
        raw = gzip.decompress(path.read_bytes())
        newline = raw.index(b"\n")
        header = json.loads(raw[:newline])
        body = raw[newline + 1:]
        half = len(body) // 2
        trip_idx, secs = array("i"), array("i")
        trip_idx.frombytes(body[:half])
        secs.frombytes(body[half:])
        if sys.byteorder == "big":
            trip_idx.byteswap(); secs.byteswap()
        return cls(header, trip_idx, secs)

    def active_services(self, day: date) -> set[str]:
        """Service ids running on a date: the weekly calendar plus that date's exceptions."""
        key = day.strftime("%Y%m%d")
        active = {
            sid for sid, c in self.calendar.items()
            if c["start"] <= key <= c["end"] and c["days"][day.weekday()]
        }
        exc = self.exceptions.get(key, {})
        return (active | set(exc.get("add", []))) - set(exc.get("remove", []))

    def in_service(self, now: datetime) -> bool:
        """True if the timetable covers today, i.e. it hasn't expired. When it
        does, "nothing scheduled" is a real answer and 511 can't add to it."""
        return bool(self.active_services(now.astimezone(TZ).date()))

    def upcoming(self, stop_id: str, now: datetime, exclude_trips: set[str],
                 window: timedelta = WINDOW, grace: timedelta = timedelta(seconds=60)) -> list[dict]:
        """Scheduled arrivals at a stop between now - grace and now + window,
        skipping trips in exclude_trips (the ones live in the vehicle feed)."""
        span = self.stops.get(stop_id)
        if not span:
            return []
        start, count = span
        local_now = now.astimezone(TZ)
        results = []
        # GTFS times count from the service day's midnight and can exceed 24:00,
        # so a trip at 25:10 "yesterday" runs at 1:10 today. Check both days.
        for day in (local_now.date() - timedelta(days=1), local_now.date()):
            midnight = datetime.combine(day, time(0), TZ)
            lo_s = int((now - grace - midnight).total_seconds())
            hi_s = int((now + window - midnight).total_seconds())
            services = self.active_services(day)
            i = bisect_left(self.secs, lo_s, start, start + count)
            while i < start + count and self.secs[i] <= hi_s:
                trip_id, route_id, headsign, service_id = self.trips[self.trip_idx[i]]
                if service_id in services and trip_id not in exclude_trips:
                    when = midnight + timedelta(seconds=self.secs[i])
                    results.append({
                        "route": route_id,
                        "destination": headsign or None,
                        "vehicle_id": None,
                        "expected": when.astimezone(now.tzinfo).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "scheduled": True,
                    })
                i += 1
        return results
