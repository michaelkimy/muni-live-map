import asyncio

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, poll_once
from app.parse import decode_511
from app.state import AppState, RequestBudget


class FakeClient:
    def __init__(self, raw):
        self.raw = raw

    async def vehicle_monitoring(self):
        return decode_511(self.raw)


def make_app():
    state = AppState(RequestBudget(60, 75), 75)
    app = create_app(Settings(poller_enabled=False), state)
    return app, state


def test_vehicles_empty_and_stale_before_first_poll():
    app, _ = make_app()
    with TestClient(app) as c:
        body = c.get("/api/vehicles").json()
    assert body == {"updated_at": None, "stale": True, "vehicles": []}


def test_vehicles_after_poll_match_spec_shape(vehicles_raw):
    app, state = make_app()
    asyncio.run(poll_once(FakeClient(vehicles_raw), state))
    with TestClient(app) as c:
        body = c.get("/api/vehicles").json()
    assert body["stale"] is False and body["updated_at"].endswith("Z")
    assert len(body["vehicles"]) == 15
    assert set(body["vehicles"][0]) == {
        "id", "route", "direction", "destination", "lat", "lon", "bearing",
        "next_stop_id", "next_stop_name", "last_moved"}


def test_health_reports_disabled_poller():
    app, _ = make_app()
    with TestClient(app) as c:
        body = c.get("/api/health").json()
    assert body == {"poller": "disabled", "snapshot_age_s": None,
                    "requests_last_hour": 0, "prediction_budget_left": 10}


def test_frontend_is_served_at_root():
    app, _ = make_app()
    with TestClient(app) as c:
        page = c.get("/")
        assert page.status_code == 200 and "leaflet" in page.text
        assert c.get("/app.js").status_code == 200
        assert c.get("/icons/bus.svg").status_code == 200
        # API routes still win over the static mount
        assert c.get("/api/health").json()["poller"] == "disabled"
