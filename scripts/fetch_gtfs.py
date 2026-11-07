"""Download Muni's static GTFS feed from 511 and summarize the files we need.

Usage:
    python scripts/fetch_gtfs.py

Saves and unzips to data/raw/gtfs/. Uses 1 API request.
"""
from __future__ import annotations

import csv
import io
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from _env import PROJECT_ROOT, get_api_key

URL = "https://api.511.org/transit/datafeeds"
OUT_DIR = PROJECT_ROOT / "data" / "raw" / "gtfs"


def main() -> None:
    query = urllib.parse.urlencode({"api_key": get_api_key(), "operator_id": "SF"})
    try:
        with urllib.request.urlopen(f"{URL}?{query}", timeout=120) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        sys.exit(f"511 returned HTTP {e.code} {e.reason}.")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR.parent / "gtfs_sf.zip").write_bytes(payload)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        zf.extractall(OUT_DIR)
        print(f"Extracted {len(zf.namelist())} files to {OUT_DIR.relative_to(PROJECT_ROOT)}\n")

    for name in ["routes.txt", "shapes.txt", "stops.txt"]:
        path = OUT_DIR / name
        if not path.exists():
            print(f"{name}: MISSING")
            continue
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        print(f"{name}: {len(rows)} rows, columns: {', '.join(reader.fieldnames or [])}")
        for row in rows[:3]:
            print("   ", dict(row))
        print()


if __name__ == "__main__":
    main()
