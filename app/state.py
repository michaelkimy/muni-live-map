"""In-memory state: the latest vehicle snapshot and the 511 request budget."""
from __future__ import annotations

import asyncio
import math
from collections import deque
from datetime import datetime, timedelta, timezone

WINDOW = timedelta(hours=1)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RequestBudget:
    """Counts 511 calls in a sliding 60-minute window (SPEC.md section 4).

    The poller may use the whole limit. Predictions get only what the poller
    won't need: limit - ceil(3600 / poll_interval) - 2.
    """

    def __init__(self, limit_per_hour: int, poll_interval_s: float):
        self.limit = limit_per_hour
        self.prediction_limit = max(0, limit_per_hour - math.ceil(3600 / poll_interval_s) - 2)
        self._calls: deque[tuple[datetime, str]] = deque()

    def _prune(self, now: datetime) -> None:
        while self._calls and now - self._calls[0][0] >= WINDOW:
            self._calls.popleft()

    def used(self, now: datetime | None = None) -> int:
        self._prune(now or utcnow())
        return len(self._calls)

    def predictions_used(self, now: datetime | None = None) -> int:
        self._prune(now or utcnow())
        return sum(1 for _, kind in self._calls if kind == "prediction")

    def can_poll(self, now: datetime | None = None) -> bool:
        return self.used(now) < self.limit

    def can_predict(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return self.predictions_used(now) < self.prediction_limit and self.used(now) < self.limit

    def record(self, kind: str, now: datetime | None = None) -> None:
        self._calls.append((now or utcnow(), kind))


class AppState:
    def __init__(self, budget: RequestBudget, poll_interval_s: float):
        self.budget = budget
        self.poll_interval_s = poll_interval_s
        self.vehicles: dict[str, dict] = {}
        self.updated_at: datetime | None = None
        self.poller_status = "starting"
        self._positions: dict[str, tuple[float, float]] = {}
        self._last_moved: dict[str, datetime] = {}
        # Predictions (Phase 8)
        self.feed_arrivals: dict[str, list[dict]] = {}   # stop_id -> arrivals from the vehicle feed
        self.live_trips: set[str] = set()                 # trips running now (excluded from the timetable)
        self.schedule = None                              # app.schedule.Schedule, or None if not built
        self.prediction_cache: dict[str, tuple[datetime, list[dict]]] = {}  # stop_id -> (fetched, arrivals)
        self.known_stops: set[str] | None = None        # None = don't validate (no stops file)
        self.prediction_lock = asyncio.Lock()             # one 511 prediction fetch at a time

    def update_vehicles(self, vehicles: list[dict], now: datetime | None = None,
                        arrivals: dict[str, list[dict]] | None = None,
                        live_trips: set[str] | None = None) -> None:
        """Replace the snapshot. A vehicle's last_moved changes only when its position does,
        because the feed has no per-vehicle timestamp (notes/data.md)."""
        now = now or utcnow()
        positions, last_moved = {}, {}
        for v in vehicles:
            pos = (v["lat"], v["lon"])
            positions[v["id"]] = pos
            if self._positions.get(v["id"]) == pos:
                last_moved[v["id"]] = self._last_moved[v["id"]]
            else:
                last_moved[v["id"]] = now
        self._positions, self._last_moved = positions, last_moved
        self.vehicles = {v["id"]: v for v in vehicles}
        if arrivals is not None:
            self.feed_arrivals = arrivals
        if live_trips is not None:
            self.live_trips = live_trips
        self.updated_at = now

    def is_stale(self, now: datetime | None = None) -> bool:
        if self.updated_at is None:
            return True
        age = ((now or utcnow()) - self.updated_at).total_seconds()
        return age > 3 * self.poll_interval_s

    def vehicles_response(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        return {
            # The browser measures "moved X ago" against this, not its own clock,
            # which may be off.
            "now": iso(now),
            "updated_at": iso(self.updated_at),
            "stale": self.is_stale(now),
            "vehicles": [
                {**v, "last_moved": iso(self._last_moved.get(vid))}
                for vid, v in self.vehicles.items()
            ],
        }

    def health_response(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        age = None if self.updated_at is None else round((now - self.updated_at).total_seconds())
        return {
            "poller": self.poller_status,
            "snapshot_age_s": age,
            "requests_last_hour": self.budget.used(now),
            "prediction_budget_left": max(0, self.budget.prediction_limit - self.budget.predictions_used(now)),
        }
