"""Turn Muni's GTFS files into the static map data the frontend loads (SPEC.md section 7).

Usage (after scripts/fetch_gtfs.py has downloaded data/raw/gtfs/):
    .venv/bin/python scripts/build_geojson.py

Writes, and these get committed:
    static/data/routes.geojson  one simplified (Multi)LineString per route, with color and category
    static/data/stops.geojson   one point per stop, with the routes that serve it
    static/data/routes.json     compact route list for the filter UI
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parent.parent
GTFS = ROOT / "data" / "raw" / "gtfs"
OUT = ROOT / "static" / "data"

SIMPLIFY_DEG = 0.00004  # about 4 m; invisible on the map but shrinks the file a lot
PRECISION_DEG = 0.00001  # round coordinates to about 1 m

CATEGORY_BY_ROUTE_TYPE = {"0": "lightrail", "3": "bus", "5": "cablecar"}
CATEGORY_OVERRIDES = {"F": "streetcar"}  # GTFS calls the F light rail (type 0)


def read(name: str, **kwargs) -> pd.DataFrame:
    return pd.read_csv(GTFS / name, dtype=str, encoding="utf-8-sig", keep_default_na=False, **kwargs)


def natural_key(route_id: str):
    """Sort 1, 2, 14, 14R, 38, F, N ... instead of 1, 14, 14R, 2 ..."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part)
            for part in re.findall(r"\d+|\D+", route_id)]


def build_routes() -> gpd.GeoDataFrame:
    routes = read("routes.txt")
    trips = read("trips.txt", usecols=["route_id", "shape_id"]).drop_duplicates()

    shapes = read("shapes.txt")
    shapes["seq"] = shapes["shape_pt_sequence"].astype(int)
    shapes = shapes.sort_values(["shape_id", "seq"])
    lines = {
        shape_id: LineString(zip(g["shape_pt_lon"].astype(float), g["shape_pt_lat"].astype(float)))
        for shape_id, g in shapes.groupby("shape_id")
    }

    records = []
    for route in routes.itertuples():
        shape_ids = trips.loc[trips["route_id"] == route.route_id, "shape_id"].unique()
        geoms = [lines[s] for s in shape_ids if s in lines]
        if not geoms:
            continue
        # unary_union merges the overlapping inbound/outbound/branch shapes so
        # shared street segments are stored once; line_merge joins the pieces back up.
        geom = shapely.line_merge(shapely.unary_union(geoms))
        geom = shapely.set_precision(geom.simplify(SIMPLIFY_DEG), PRECISION_DEG)
        records.append({
            "route_id": route.route_id,
            "short_name": route.route_short_name,
            "long_name": route.route_long_name.title(),
            "color": "#" + (route.route_color or "666666").upper(),
            "text_color": "#" + (route.route_text_color or "FFFFFF").upper(),
            "category": CATEGORY_OVERRIDES.get(route.route_id,
                                               CATEGORY_BY_ROUTE_TYPE.get(route.route_type, "bus")),
            "geometry": geom,
        })
    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    return gdf.iloc[sorted(range(len(gdf)), key=lambda i: natural_key(gdf.route_id.iloc[i]))]


def build_stops(route_ids: set[str]) -> gpd.GeoDataFrame:
    stops = read("stops.txt")
    trips = read("trips.txt", usecols=["trip_id", "route_id"])
    stop_times = read("stop_times.txt", usecols=["trip_id", "stop_id"]).drop_duplicates()
    served = stop_times.merge(trips, on="trip_id")[["stop_id", "route_id"]].drop_duplicates()
    served = served[served["route_id"].isin(route_ids)]
    routes_by_stop = served.groupby("stop_id")["route_id"].agg(lambda r: sorted(set(r), key=natural_key))

    stops = stops[stops["stop_id"].isin(routes_by_stop.index)]  # skip stops no trip uses
    return gpd.GeoDataFrame(
        {
            "stop_id": stops["stop_id"].values,
            "name": stops["stop_name"].values,
            "routes": [routes_by_stop[s] for s in stops["stop_id"]],
        },
        geometry=gpd.points_from_xy(stops["stop_lon"].astype(float).round(5),
                                    stops["stop_lat"].astype(float).round(5)),
        crs="EPSG:4326",
    )


def write_geojson(gdf: gpd.GeoDataFrame, path: Path) -> None:
    # Compact JSON (no spaces) keeps the files small for phones.
    data = json.loads(gdf.to_json(drop_id=True))
    path.write_text(json.dumps(data, separators=(",", ":")))


def main() -> None:
    if not (GTFS / "routes.txt").exists():
        raise SystemExit("No GTFS in data/raw/gtfs. Run scripts/fetch_gtfs.py first.")
    OUT.mkdir(parents=True, exist_ok=True)

    routes = build_routes()
    stops = build_stops(set(routes.route_id))
    write_geojson(routes, OUT / "routes.geojson")
    write_geojson(stops, OUT / "stops.geojson")
    route_list = routes.drop(columns="geometry").to_dict(orient="records")
    (OUT / "routes.json").write_text(json.dumps(route_list, separators=(",", ":")))

    for name in ("routes.geojson", "stops.geojson", "routes.json"):
        print(f"{name}: {(OUT / name).stat().st_size / 1024:.0f} KB")
    print(f"{len(routes)} routes, {len(stops)} stops")
    print("categories:", routes.category.value_counts().to_dict())


if __name__ == "__main__":
    main()
