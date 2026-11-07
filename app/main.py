"""FastAPI app: a background poller fills the cache, and endpoints serve only from it.

Run with:  .venv/bin/uvicorn app.main:app --reload
Run a single worker only: all state lives in this process's memory.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.client import Client511, RateLimited, Upstream511Error
from app.config import PROJECT_ROOT, Settings, load_settings
from app.parse import parse_vehicles
from app.state import AppState, RequestBudget

log = logging.getLogger("muni.poller")
MAX_BACKOFF_S = 600


async def poll_once(client: Client511, state: AppState) -> None:
    siri = await client.vehicle_monitoring()
    state.update_vehicles(parse_vehicles(siri))


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


def create_app(settings: Settings | None = None, state: AppState | None = None) -> FastAPI:
    settings = settings or load_settings()
    if state is None:
        budget = RequestBudget(settings.rate_limit_per_hour, settings.poll_interval_s)
        state = AppState(budget, settings.poll_interval_s)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not settings.poller_enabled:
            state.poller_status = "disabled"
            yield
            return
        client = Client511(settings.api_key, state.budget)
        task = asyncio.create_task(poller(client, state))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await client.aclose()

    app = FastAPI(title="Muni Live Map", lifespan=lifespan)
    app.state.muni = state
    # GeoJSON compresses about 5x; browsers decompress automatically.
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    @app.get("/api/vehicles")
    def vehicles():
        return state.vehicles_response()

    @app.get("/api/health")
    def health():
        return state.health_response()

    static_dir = PROJECT_ROOT / "static"
    if static_dir.is_dir():  # the frontend arrives in Phase 4
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")

    return app


app = create_app()
