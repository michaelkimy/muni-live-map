from datetime import datetime, timezone

import pytest

from app.predictions import get_predictions
from app.schedule import Schedule, write_schedule
from app.state import AppState, RequestBudget

import asyncio

# Friday 2026-11-06 in San Francisco (PST = UTC-8).
TRIPS = [
    ["wk_0700", "38", "Ocean Beach", "WEEKDAY"],   # 0
    ["wk_0715", "38", "Ocean Beach", "WEEKDAY"],   # 1
    ["sat_0700", "38", "Ocean Beach", "SAT"],      # 2
    ["owl_2510", "N", "Ocean Beach", "WEEKDAY"],   # 3  runs at 01:10 the next morning
    ["holiday_0705", "38", "Ocean Beach", "HOL"],  # 4  only on 2026-11-11 (added by exception)
]
CALENDAR = {
    "WEEKDAY": {"days": [1, 1, 1, 1, 1, 0, 0], "start": "20260801", "end": "20270131"},
    "SAT": {"days": [0, 0, 0, 0, 0, 1, 0], "start": "20260801", "end": "20270131"},
    "HOL": {"days": [0, 0, 0, 0, 0, 0, 0], "start": "20260801", "end": "20270131"},
}
EXCEPTIONS = {"20261111": {"add": ["HOL"], "remove": ["WEEKDAY"]}}  # Veterans Day
ROWS = {"100": [(0, 7 * 3600), (1, 7 * 3600 + 900), (2, 7 * 3600), (3, 25 * 3600 + 600), (4, 7 * 3600 + 300)]}


@pytest.fixture
def schedule(tmp_path):
    path = tmp_path / "schedule.bin.gz"
    write_schedule(path, TRIPS, CALENDAR, EXCEPTIONS, ROWS)
    return Schedule.load(path)


def pst(y, m, d, hh, mm):
    return datetime(y, m, d, hh + 8, mm, tzinfo=timezone.utc)  # PST -> UTC


def routes_and_times(arrivals):
    return [(a["route"], a["expected"]) for a in arrivals]


def test_weekday_trips_only_on_weekdays(schedule):
    fri = schedule.upcoming("100", pst(2026, 11, 6, 6, 50), set())
    assert routes_and_times(fri) == [("38", "2026-11-06T15:00:00Z"), ("38", "2026-11-06T15:15:00Z")]
    sat = schedule.upcoming("100", pst(2026, 11, 7, 6, 50), set())
    assert routes_and_times(sat) == [("38", "2026-11-07T15:00:00Z")]
    assert all(a["scheduled"] and a["vehicle_id"] is None for a in fri)


def test_calendar_exception_swaps_service(schedule):
    holiday = schedule.upcoming("100", pst(2026, 11, 11, 6, 50), set())
    assert routes_and_times(holiday) == [("38", "2026-11-11T15:05:00Z")]


def test_times_past_midnight_belong_to_previous_service_day(schedule):
    # 25:10 on Thursday's schedule = 01:10 Friday morning
    owl = schedule.upcoming("100", pst(2026, 11, 6, 1, 0), set())
    assert routes_and_times(owl) == [("N", "2026-11-06T09:10:00Z")]


def test_live_trips_are_excluded(schedule):
    result = schedule.upcoming("100", pst(2026, 11, 6, 6, 50), {"wk_0700"})
    assert routes_and_times(result) == [("38", "2026-11-06T15:15:00Z")]


def test_window_and_unknown_stop(schedule):
    assert schedule.upcoming("100", pst(2026, 11, 6, 3, 0), set()) == []   # nothing within 90 min
    assert schedule.upcoming("999", pst(2026, 11, 6, 6, 50), set()) == []


def test_predictions_merge_live_and_scheduled(schedule):
    state = AppState(RequestBudget(60, 75), 75)
    now = pst(2026, 11, 6, 6, 50)
    live = {"route": "38", "destination": "Ocean Beach", "vehicle_id": "6512",
            "expected": "2026-11-06T15:02:00Z", "scheduled": False}
    state.update_vehicles([], now, arrivals={"100": [live]}, live_trips={"wk_0700"})
    state.schedule = schedule
    result = asyncio.run(get_predictions("100", state, client=None, now=now))
    assert result["source"] == "feed"
    assert [(a["expected"], a["scheduled"]) for a in result["arrivals"]] == [
        ("2026-11-06T15:02:00Z", False),   # live bus on wk_0700, running 2 min late
        ("2026-11-06T15:15:00Z", True),    # next trip hasn't started: timetable
    ]
