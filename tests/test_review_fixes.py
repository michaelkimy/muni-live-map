"""Regression tests for problems found in the Phase 9 review."""
import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.client import Upstream511Error
from app.config import Settings
from app.main import create_app, poll_once
from app.parse import parse_vehicles
from app.predictions import _upcoming
from app.state import AppState, RequestBudget


def siri_with(lat, lon):
    activity = {"MonitoredVehicleJourney": {"LineRef": "38", "VehicleRef": "1",
                "VehicleLocation": {"Latitude": lat, "Longitude": lon}, "Bearing": "NaN"}}
    return {"Siri": {"ServiceDelivery": {"VehicleMonitoringDelivery": {"VehicleActivity": [activity]}}}}


def test_nan_position_is_dropped_and_nan_bearing_becomes_null():
    assert parse_vehicles(siri_with("NaN", "-122.4")) == []
    (v,) = parse_vehicles(siri_with("37.78", "-122.45"))
    assert v["bearing"] is None


class FakeClient:
    def __init__(self, siri):
        self.siri = siri

    async def vehicle_monitoring(self):
        return self.siri


def test_empty_511_answer_keeps_last_snapshot():
    state = AppState(RequestBudget(60, 75), 75)
    asyncio.run(poll_once(FakeClient(siri_with("37.78", "-122.45")), state))
    empty = {"Siri": {"ServiceDelivery": {"VehicleMonitoringDelivery": {}}}}
    with pytest.raises(Upstream511Error):
        asyncio.run(poll_once(FakeClient(empty), state))
    assert len(state.vehicles) == 1


def test_naive_times_are_treated_as_utc():
    now = datetime(2026, 11, 7, 5, 0, tzinfo=timezone.utc)
    arrivals = [{"route": "7", "vehicle_id": "1", "expected": "2026-11-07T05:05:00", "scheduled": False}]
    assert len(_upcoming(arrivals, now)) == 1


def test_security_headers_on_pages_and_api():
    app = create_app(Settings(poller_enabled=False), AppState(RequestBudget(60, 75), 75))
    with TestClient(app) as c:
        for path in ("/", "/api/vehicles"):
            headers = c.get(path).headers
            assert "script-src 'self' https://unpkg.com" in headers["content-security-policy"]
            assert headers["x-content-type-options"] == "nosniff"
