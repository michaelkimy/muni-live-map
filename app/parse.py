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


def _service_delivery(data: dict) -> dict:
    """VehicleMonitoring wraps everything in "Siri"; StopMonitoring doesn't."""
    return (data.get("Siri") or data).get("ServiceDelivery") or {}


def _activities(data: dict) -> list[dict]:
    delivery = _service_delivery(data)
    return [
        activity
        for vmd in _as_list(delivery.get("VehicleMonitoringDelivery"))
        for activity in _as_list(vmd.get("VehicleActivity"))
    ]


def _arrival(mvj: dict, call: dict) -> dict | None:
    """One upcoming arrival at a stop, or None if it has no predicted time."""
    expected = _text(call.get("ExpectedArrivalTime")) or _text(call.get("ExpectedDepartureTime"))
    route = _text(mvj.get("LineRef"))
    if not expected or not route:
        return None
    return {
        "route": route,
        # DestinationDisplay is the short sign text ("Ocean Beach"); fall back to the stop name.
        "destination": _text(call.get("DestinationDisplay")) or _text(mvj.get("DestinationName")),
        "vehicle_id": _text(mvj.get("VehicleRef")),  # null for trips that haven't started yet
        "expected": expected,
        # No vehicle yet means the trip hasn't started: the time is the timetable's.
        "scheduled": _text(mvj.get("VehicleRef")) is None,
    }


def parse_vehicles(siri: dict) -> list[dict]:
    """Return in-service vehicles. Vehicles with no route or no position are dropped."""
    vehicles = []
    for activity in _activities(siri):
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


def feed_arrivals(siri: dict) -> dict[str, list[dict]]:
    """Index the vehicle feed's next-stop and onward-stop predictions by stop id.

    These cost no extra 511 requests and cover about 80% of stops (notes/data.md).
    """
    by_stop: dict[str, list[dict]] = {}
    for activity in _activities(siri):
        mvj = activity.get("MonitoredVehicleJourney") or {}
        calls = _as_list(mvj.get("MonitoredCall")) + _as_list((mvj.get("OnwardCalls") or {}).get("OnwardCall"))
        for call in calls:
            stop_id = _text(call.get("StopPointRef"))
            arrival = _arrival(mvj, call)
            if stop_id and arrival:
                by_stop.setdefault(stop_id, []).append(arrival)
    return by_stop


def parse_stop_monitoring(data: dict) -> list[dict]:
    """Arrivals from a 511 StopMonitoring response (one stop)."""
    delivery = _service_delivery(data)
    arrivals = []
    for smd in _as_list(delivery.get("StopMonitoringDelivery")):
        for visit in _as_list(smd.get("MonitoredStopVisit")):
            mvj = visit.get("MonitoredVehicleJourney") or {}
            arrival = _arrival(mvj, mvj.get("MonitoredCall") or {})
            if arrival:
                arrivals.append(arrival)
    return arrivals


def live_trip_ids(siri: dict) -> set[str]:
    """GTFS trip ids of trips vehicles are running right now (they match trips.txt)."""
    trips = set()
    for activity in _activities(siri):
        mvj = activity.get("MonitoredVehicleJourney") or {}
        ref = _text((mvj.get("FramedVehicleJourneyRef") or {}).get("DatedVehicleJourneyRef"))
        if ref and _text(mvj.get("LineRef")):
            trips.add(ref)
    return trips
