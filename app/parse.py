"""Pure functions that turn 511 SIRI JSON into the app's simple records. No I/O here.

Field paths and quirks are documented in notes/data.md.
"""
from __future__ import annotations

import json
from typing import Any


def decode_511(raw: bytes) -> dict:
    """511 responses start with a UTF-8 byte-order mark; utf-8-sig strips it."""
    return json.loads(raw.decode("utf-8-sig"))


def _as_list(value: Any) -> list:
    """511 sometimes gives a single object where SIRI allows a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(value: Any) -> str | None:
    """Text fields arrive as a string, a one-item list, or null/empty."""
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_vehicles(siri: dict) -> list[dict]:
    """Return in-service vehicles. Vehicles with no route or no position are dropped."""
    delivery = siri.get("Siri", {}).get("ServiceDelivery", {})
    vehicles = []
    for vmd in _as_list(delivery.get("VehicleMonitoringDelivery")):
        for activity in _as_list(vmd.get("VehicleActivity")):
            mvj = activity.get("MonitoredVehicleJourney") or {}
            route = _text(mvj.get("LineRef"))
            vehicle_id = _text(mvj.get("VehicleRef"))
            loc = mvj.get("VehicleLocation") or {}
            lat, lon = _float(loc.get("Latitude")), _float(loc.get("Longitude"))
            if not route or not vehicle_id or lat is None or lon is None:
                continue
            call = mvj.get("MonitoredCall") or {}
            vehicles.append({
                "id": vehicle_id,
                "route": route,
                "direction": _text(mvj.get("DirectionRef")),
                "destination": _text(mvj.get("DestinationName")),
                "lat": lat,
                "lon": lon,
                "bearing": _float(mvj.get("Bearing")),
                "next_stop_id": _text(call.get("StopPointRef")),
                "next_stop_name": _text(call.get("StopPointName")),
            })
    return vehicles
