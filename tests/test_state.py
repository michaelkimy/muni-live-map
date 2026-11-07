from datetime import datetime, timedelta, timezone

from app.state import AppState, RequestBudget

T0 = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)


def vehicle(vid, lat, lon):
    return {"id": vid, "route": "1", "direction": "IB", "destination": "X", "lat": lat,
            "lon": lon, "bearing": 0.0, "next_stop_id": None, "next_stop_name": None}


def test_prediction_limit_formula():
    # 60/hour, poll every 75 s -> 48 for the poller, 2 spare -> 10 for predictions
    assert RequestBudget(60, 75).prediction_limit == 10


def test_predictions_cannot_use_poller_share():
    b = RequestBudget(60, 75)
    for i in range(10):
        assert b.can_predict(T0)
        b.record("prediction", T0)
    assert not b.can_predict(T0)
    assert b.can_poll(T0)


def test_poller_stops_at_hard_limit_and_window_slides():
    b = RequestBudget(60, 75)
    for _ in range(60):
        b.record("poll", T0)
    assert not b.can_poll(T0)
    assert b.can_poll(T0 + timedelta(hours=1))
    assert b.used(T0 + timedelta(hours=1)) == 0


def test_last_moved_only_changes_when_position_changes():
    s = AppState(RequestBudget(60, 75), 75)
    s.update_vehicles([vehicle("a", 37.7, -122.4), vehicle("b", 37.8, -122.4)], T0)
    t1 = T0 + timedelta(seconds=75)
    s.update_vehicles([vehicle("a", 37.7, -122.4), vehicle("b", 37.81, -122.4)], t1)
    moved = {v["id"]: v["last_moved"] for v in s.vehicles_response(t1)["vehicles"]}
    assert moved == {"a": "2026-11-07T04:00:00Z", "b": "2026-11-07T04:01:15Z"}


def test_vehicle_that_disappears_is_dropped():
    s = AppState(RequestBudget(60, 75), 75)
    s.update_vehicles([vehicle("a", 37.7, -122.4)], T0)
    s.update_vehicles([], T0 + timedelta(seconds=75))
    assert s.vehicles_response()["vehicles"] == []


def test_stale_after_three_poll_intervals():
    s = AppState(RequestBudget(60, 75), 75)
    assert s.is_stale(T0)  # nothing fetched yet
    s.update_vehicles([], T0)
    assert not s.is_stale(T0 + timedelta(seconds=225))
    assert s.is_stale(T0 + timedelta(seconds=226))
