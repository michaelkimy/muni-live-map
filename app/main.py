"""FastAPI app: a background poller fills the cache, and endpoints serve only from it.

Run with:  .venv/bin/uvicorn app.main:app --reload
Run a single worker only: all state lives in this process's memory.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.client import Client511, RateLimited, Upstream511Error
from app.config import PROJECT_ROOT, Settings, load_settings
from app.parse import feed_arrivals, parse_vehicles
from app.predictions import get_predictions
from app.state import AppState, RequestBudget

log = logging.getLogger("muni.poller")
MAX_BACKOFF_S = 600


async def poll_once(client: Client511, state: AppState) -> None:
    siri = await client.vehicle_monitoring()
    state.update_vehicles(parse_vehicles(siri), arrivals=feed_arrivals(siri))


def load_stop_ids() -> set[str] | None:
    """Stop ids from the generated stops file, used to reject made-up ids before
    they can spend a 511 request. None if the file hasn't been built."""
    path = PROJECT_ROOT / "static" / "data" / "stops.geojson"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return {f["properties"]["stop_id"] for f in data["features"]}


async def poller(client: Client511, state: AppState) -> None:
    failures = 0
    while True:
        try:
            await poll_once(client, state)
            failures = 0
            state.poller_status = "ok"
            delay = state.poll_interval_s
        except (RateLimited, Upstream511Error) as e:
            failures += 1
            state.poller_status = "backoff"
            delay = min(state.poll_interval_s * 2 ** failures, MAX_BACKOFF_S)
            log.warning("poll failed (%s); retrying in %.0f s", e, delay)
        except Exception:  # never let the poller die silently
            failures += 1
            state.poller_status = "backoff"
            delay = min(state.poll_interval_s * 2 ** failures, MAX_BACKOFF_S)
            log.exception("unexpected poller error; retrying in %.0f s", delay)
        await asyncio.sleep(delay)


def create_app(settings: Settings | None = None, state: AppState | None = None,
               client: Client511 | None = None) -> FastAPI:
    """Build the app. Tests pass their own state and a fake client."""
    settings = settings or load_settings()
    if state is None:
        budget = RequestBudget(settings.rate_limit_per_hour, settings.poll_interval_s)
        state = AppState(budget, settings.poll_interval_s)
    if state.known_stops is None:
        state.known_stops = load_stop_ids()
    holder = {"client": client}  # the 511 client the endpoints use (None = no 511 access)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        owned = None
        if holder["client"] is None and settings.api_key:
            owned = holder["client"] = Client511(settings.api_key, state.budget)
        task = None
        if settings.poller_enabled and holder["client"] is not None:
            task = asyncio.create_task(poller(holder["client"], state))
        else:
            state.poller_status = "disabled"
        try:
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            if owned:
                await owned.aclose()
                holder["client"] = None

    app = FastAPI(title="Muni Live Map", lifespan=lifespan)
    app.state.muni = state
    # GeoJSON compresses about 5x; browsers decompress automatically.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/api/vehicles")
    def vehicles():
        return state.vehicles_response()

    @app.get("/api/predictions/{stop_id}")
    async def predictions(stop_id: str):
        # Stop ids are numeric. Reject anything else, or unknown ids, before
        # they can reach 511 and spend part of the hourly budget.
        if not re.fullmatch(r"\d{1,8}", stop_id) or (
                state.known_stops is not None and stop_id not in state.known_stops):
            raise HTTPException(status_code=404, detail="Unknown stop")
        return await get_predictions(stop_id, state, holder["client"])

    @app.get("/api/health")
    def health():
        return state.health_response()

    static_dir = PROJECT_ROOT / "static"
    if static_dir.is_dir():  # the frontend arrives in Phase 4
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
