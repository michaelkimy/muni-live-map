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


# ---- Can visitors use up the 511 limit? ----

def test_every_real_stop_costs_zero_511_requests_with_current_timetable():
    """With the committed timetable in service, no stop click reaches 511."""
    import json
    from app.config import PROJECT_ROOT
    from app.schedule import Schedule
    from app.predictions import get_predictions

    class CountingClient:
        calls = 0
        async def stop_monitoring(self, stop_id):
            CountingClient.calls += 1
            return {}

    schedule = Schedule.load()
    state = AppState(RequestBudget(60, 75), 75)
    now = datetime(2026, 11, 6, 20, 0, tzinfo=timezone.utc)   # a weekday, timetable in service
    state.update_vehicles([], now, arrivals={}, live_trips=set())
    state.schedule = schedule
    stops = [f["properties"]["stop_id"] for f in
             json.loads((PROJECT_ROOT / "static/data/stops.geojson").read_text())["features"]]

    async def all_stops():
        await asyncio.gather(*(get_predictions(s, state, CountingClient(), now=now) for s in stops))

    asyncio.run(all_stops())
    assert CountingClient.calls == 0


def test_flood_with_no_timetable_is_capped_and_poller_keeps_its_share():
    """Worst case: no usable timetable, 1,000 simultaneous clicks on different stops."""
    import httpx
    from app.client import Client511
    from app.predictions import get_predictions

    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=b'{"ServiceDelivery": {}}')

    budget = RequestBudget(60, 75)
    http = httpx.AsyncClient(base_url="https://api.511.org/transit", transport=httpx.MockTransport(handler))
    client = Client511("k", budget, http)
    state = AppState(budget, 75)                     # no schedule, no feed data

    async def flood():
        return await asyncio.gather(*(get_predictions(str(10000 + i), state, client) for i in range(1000)))

    results = asyncio.run(flood())
    assert len(calls) == budget.prediction_limit == 10       # only the prediction allowance
    assert sum(r["source"] == "unavailable" for r in results) == 990
    # The poller's share is untouched: 48 polls/hour still fit under 60.
    for _ in range(48):
        assert budget.can_poll()
        budget.record("poll")
    assert budget.used() == 58 <= budget.limit


def test_predictions_stop_when_511_says_the_key_is_nearly_used_up():
    """Requests we can't see (a restart, the fetch scripts) still count at 511."""
    import httpx
    from app.client import Client511, RateLimited

    budget = RequestBudget(60, 75)
    def handler(request):
        return httpx.Response(200, content=b"{}", headers={"RateLimit-Limit": "60", "RateLimit-Remaining": "9"})
    client = Client511("k", budget, httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler)))
    asyncio.run(client.vehicle_monitoring())
    assert budget.remaining_511 == 9
    assert not budget.can_predict()      # below the reserve of 10: the poller keeps the rest
    assert budget.can_poll()
    with pytest.raises(RateLimited):
        asyncio.run(client.stop_monitoring("15694"))
