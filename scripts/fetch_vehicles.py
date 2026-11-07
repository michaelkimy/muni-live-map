"""Fetch live Muni vehicle positions from 511 and print them as a table.

Usage:
    python scripts/fetch_vehicles.py            # call 511 (uses 1 request of ~60/hour)
    python scripts/fetch_vehicles.py FILE.json  # re-read a saved response, no API call

Each live call saves the raw response to data/raw/vehicles_<timestamp>.json.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from _env import PROJECT_ROOT, get_api_key

URL = "https://api.511.org/transit/VehicleMonitoring"
RAW_DIR = PROJECT_ROOT / "data" / "raw"


def fetch_raw() -> bytes:
    query = urllib.parse.urlencode({"api_key": get_api_key(), "agency": "SF", "format": "json"})
    try:
        with urllib.request.urlopen(f"{URL}?{query}", timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        # Don't print the URL: it contains the API key.
        sys.exit(f"511 returned HTTP {e.code} {e.reason}. 429 means the hourly limit is used up.")


def as_list(value):
    """511 sometimes gives a single object where SIRI allows a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def first(value):
    """Some SIRI text fields come as a string, others as a one-item list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def parse(data: dict) -> list[dict]:
    delivery = data["Siri"]["ServiceDelivery"]
    rows = []
    for vmd in as_list(delivery.get("VehicleMonitoringDelivery")):
        for activity in as_list(vmd.get("VehicleActivity")):
            mvj = activity.get("MonitoredVehicleJourney") or {}
            loc = mvj.get("VehicleLocation") or {}
            call = mvj.get("MonitoredCall") or {}
            rows.append({
                "route": mvj.get("LineRef"),
                "vehicle": mvj.get("VehicleRef"),
                "dir": mvj.get("DirectionRef"),
                "lat": loc.get("Latitude"),
                "lon": loc.get("Longitude"),
                "destination": first(mvj.get("DestinationName")),
                "next_stop": first(call.get("StopPointName")),
                "next_stop_id": call.get("StopPointRef"),
                "recorded_at": activity.get("RecordedAtTime"),
            })
    return rows


def print_table(rows: list[dict]) -> None:
    cols = ["route", "vehicle", "dir", "lat", "lon", "destination", "next_stop", "recorded_at"]
    rows = sorted(rows, key=lambda r: (str(r["route"]), str(r["vehicle"])))
    widths = {c: min(28, max(len(c), *(len(str(r[c] or "")) for r in rows))) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c] if r[c] is not None else "-")[: widths[c]].ljust(widths[c]) for c in cols))

    print(f"\n{len(rows)} vehicles on {len({r['route'] for r in rows})} routes")
    print("Missing values per field:")
    for key in rows[0]:
        missing = sum(1 for r in rows if r[key] in (None, ""))
        if missing:
            print(f"  {key}: {missing}")


def main() -> None:
    if len(sys.argv) > 1:
        raw = Path(sys.argv[1]).read_bytes()
    else:
        raw = fetch_raw()
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        out = RAW_DIR / f"vehicles_{datetime.now():%Y%m%d_%H%M%S}.json"
        out.write_bytes(raw)
        print(f"Saved raw response to {out.relative_to(PROJECT_ROOT)}\n")

    data = json.loads(raw.decode("utf-8-sig"))  # 511 prepends a byte-order mark
    rows = parse(data)
    if not rows:
        sys.exit("No vehicles in the response. Inspect the saved file.")
    print_table(rows)


if __name__ == "__main__":
    main()
