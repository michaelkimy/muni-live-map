import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.client import RateLimited
from app.config import Settings
from app.main import create_app
from app.parse import decode_511, feed_arrivals, parse_stop_monitoring
from app.predictions import get_predictions
from app.state import AppState, RequestBudget

# Both fixtures were recorded around 04:09–04:59 UTC on 2026-11-07.
VEH_NOW = datetime(2026, 11, 7, 4, 9, 15, tzinfo=timezone.utc)
STOP_NOW = datetime(2026, 11, 7, 4, 59, 10, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, stop_raw=None, error=None):
        self.stop_raw, self.error, self.calls = stop_raw, error, []

    async def stop_monitoring(self, stop_id):
        self.calls.append(stop_id)
        if self.error:
            raise self.error
        return decode_511(self.stop_raw)


def make_state():
    return AppState(RequestBudget(60, 75), 75)


# ---- parsing ----

def test_stop_monitoring_has_no_siri_wrapper_but_parses(stop_raw):
    data = decode_511(stop_raw)
    assert "Siri" not in data
    arrivals = parse_stop_monitoring(data)
    assert len(arrivals) == 10
    first = arrivals[0]
    assert first == {"route": "7", "destination": "Ocean Beach", "vehicle_id": "6607",
                     "expected": "2026-11-07T05:05:52Z", "scheduled": False}
    # trips that haven't started yet have no vehicle, and their time is the timetable's
    unstarted = [a for a in arrivals if a["vehicle_id"] is None]
    assert unstarted and all(a["scheduled"] for a in unstarted)


def test_feed_arrivals_indexes_next_and_onward_stops(vehicles_raw):
    by_stop = feed_arrivals(decode_511(vehicles_raw))
    assert by_stop, "fixture vehicles with MonitoredCall/OnwardCalls should produce arrivals"
    for stop_id, arrivals in by_stop.items():
        assert stop_id.isdigit()
        assert all(a["route"] and a["expected"] for a in arrivals)


# ---- source selection ----

def test_feed_is_used_first_and_costs_nothing(vehicles_raw):
    state = make_state()
    state.update_vehicles([], VEH_NOW, arrivals=feed_arrivals(decode_511(vehicles_raw)))
    stop_id = next(iter(state.feed_arrivals))
    client = FakeClient()
    result = asyncio.run(get_predictions(stop_id, state, client, now=VEH_NOW))
    assert result["source"] == "feed" and result["arrivals"]
    assert client.calls == []
    times = [a["expected"] for a in result["arrivals"]]
    assert times == sorted(times)


def test_falls_back_to_511_then_caches(stop_raw):
    state, client = make_state(), FakeClient(stop_raw)
    first = asyncio.run(get_predictions("15694", state, client, now=STOP_NOW))
    assert first["source"] == "511" and len(first["arrivals"]) == 10
    again = asyncio.run(get_predictions("15694", state, client, now=STOP_NOW + timedelta(seconds=60)))
    assert again["source"] == "511" and client.calls == ["15694"]       # served from cache
    asyncio.run(get_predictions("15694", state, client, now=STOP_NOW + timedelta(seconds=91)))
    assert client.calls == ["15694", "15694"]                          # cache expired


def test_past_arrivals_are_dropped(stop_raw):
    state, client = make_state(), FakeClient(stop_raw)
    later = datetime(2026, 11, 7, 5, 30, tzinfo=timezone.utc)
    result = asyncio.run(get_predictions("15694", state, client, now=later))
    assert all(a["expected"] >= "2026-11-07T05:29:00Z" for a in result["arrivals"])
    assert len(result["arrivals"]) < 10


def test_budget_exhausted_means_unavailable():
    state = make_state()
    result = asyncio.run(get_predictions("15694", state, FakeClient(error=RateLimited("x")), now=STOP_NOW))
    assert result["source"] == "unavailable" and result["arrivals"] == []


def test_real_budget_blocks_predictions_before_the_poller():
    from app.client import Client511
    import httpx
    budget = RequestBudget(60, 75)
    for _ in range(budget.prediction_limit):
        budget.record("prediction")
    calls = []
    http = httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(
        lambda r: calls.append(r) or httpx.Response(200, content=b"{}")))
    state = AppState(budget, 75)
    result = asyncio.run(get_predictions("15694", state, Client511("k", budget, http), now=STOP_NOW))
    assert result["source"] == "unavailable" and calls == []
    assert budget.can_poll()


# ---- endpoint ----

def make_app(client=None):
    state = make_state()
    return create_app(Settings(poller_enabled=False), state, client=client), state


def test_endpoint_rejects_bad_and_unknown_stop_ids(stop_raw):
    client = FakeClient(stop_raw)
    app, _ = make_app(client)
    with TestClient(app) as c:
        assert c.get("/api/predictions/abc").status_code == 404
        assert c.get("/api/predictions/99999999").status_code == 404   # not in stops.geojson
        assert c.get("/api/predictions/15694").status_code == 200
    assert client.calls == ["15694"]


def test_endpoint_without_511_access_is_unavailable():
    app, _ = make_app(client=None)
    with TestClient(app) as c:
        body = c.get("/api/predictions/15694").json()
    assert body["source"] == "unavailable" and body["now"].endswith("Z")
