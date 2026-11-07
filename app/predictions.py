"""Stop arrival predictions (SPEC.md section 4): the vehicle feed first, 511 only as a fallback."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.client import Client511, RateLimited, Upstream511Error
from app.parse import parse_stop_monitoring
from app.state import AppState, iso, utcnow

CACHE_TTL = timedelta(seconds=90)
MAX_ARRIVALS = 10
GRACE = timedelta(seconds=60)  # keep an arrival listed for a minute after its time ("Now")


def _upcoming(arrivals: list[dict], now: datetime) -> list[dict]:
    """Drop past arrivals and duplicates, sort soonest first, keep the first few."""
    seen, result = set(), []
    for a in arrivals:
        try:
            when = datetime.fromisoformat(a["expected"])
        except ValueError:
            continue
        key = (a["route"], a["vehicle_id"], a["expected"], a.get("scheduled"))
        if when >= now - GRACE and key not in seen:
            seen.add(key)
            result.append((when, a))
    result.sort(key=lambda pair: pair[0])
    return [a for _, a in result[:MAX_ARRIVALS]]


def _response(stop_id: str, source: str, now: datetime, updated_at: datetime | None,
              arrivals: list[dict]) -> dict:
    return {"stop_id": stop_id, "source": source, "now": iso(now),
            "updated_at": iso(updated_at), "arrivals": arrivals}


async def get_predictions(stop_id: str, state: AppState, client: Client511 | None,
                          now: datetime | None = None) -> dict:
    now = now or utcnow()

    # 1. Free: live arrivals from the latest vehicle snapshot, plus timetable
    # times for trips that haven't started yet (buses waiting at their first
    # stop aren't in the feed). Live trips are excluded from the timetable, so
    # nothing is listed twice.
    combined = list(state.feed_arrivals.get(stop_id, []))
    if state.schedule is not None and state.updated_at is not None:
        combined += state.schedule.upcoming(stop_id, now, state.live_trips)
    from_feed = _upcoming(combined, now)
    if from_feed:
        return _response(stop_id, "feed", now, state.updated_at, from_feed)

    # 2. A recent 511 answer for this stop.
    def cached():
        hit = state.prediction_cache.get(stop_id)
        if hit and now - hit[0] < CACHE_TTL:
            return _response(stop_id, "511", now, hit[0], _upcoming(hit[1], now))
        return None

    if (hit := cached()) is not None:
        return hit
    if client is None:
        return _response(stop_id, "unavailable", now, None, [])

    # 3. Spend a request, if the budget allows. The lock stops two people
    # clicking the same stop at once from paying for it twice.
    async with state.prediction_lock:
        if (hit := cached()) is not None:
            return hit
        try:
            data = await client.stop_monitoring(stop_id)
        except (RateLimited, Upstream511Error):
            return _response(stop_id, "unavailable", now, None, [])
        arrivals = parse_stop_monitoring(data)
        state.prediction_cache[stop_id] = (now, arrivals)
        return _response(stop_id, "511", now, now, _upcoming(arrivals, now))
