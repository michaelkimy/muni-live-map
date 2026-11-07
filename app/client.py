"""The only code that talks to 511. Every call is checked against and recorded in the budget.

Request URLs contain the API key, so they are never logged or put in error messages.
"""
from __future__ import annotations

import logging

import httpx

from app.parse import decode_511
from app.state import RequestBudget

BASE_URL = "https://api.511.org/transit"

# httpx logs full request URLs (including the key) at INFO level. Keep it quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class Upstream511Error(Exception):
    """511 failed or couldn't be reached. The message never includes the URL."""


class RateLimited(Upstream511Error):
    """511 returned 429, or our own budget has no room."""


class Client511:
    def __init__(self, api_key: str, budget: RequestBudget, http: httpx.AsyncClient | None = None):
        self._api_key = api_key
        self._budget = budget
        self._http = http or httpx.AsyncClient(base_url=BASE_URL, timeout=30)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get(self, path: str, params: dict, kind: str) -> dict:
        allowed = self._budget.can_poll() if kind == "poll" else self._budget.can_predict()
        if not allowed:
            raise RateLimited(f"local {kind} budget used up")
        self._budget.record(kind)  # count it even if it fails: 511 still counts it
        try:
            resp = await self._http.get(path, params={**params, "api_key": self._api_key})
        except httpx.HTTPError as e:
            raise Upstream511Error(f"network error: {type(e).__name__}") from None
        if resp.status_code == 429:
            raise RateLimited("511 returned 429 Too Many Requests")
        if resp.status_code != 200:
            raise Upstream511Error(f"511 returned HTTP {resp.status_code}")
        try:
            return decode_511(resp.content)
        except ValueError:
            raise Upstream511Error("511 returned invalid JSON") from None

    async def vehicle_monitoring(self) -> dict:
        return await self._get("/VehicleMonitoring", {"agency": "SF", "format": "json"}, "poll")

    async def stop_monitoring(self, stop_id: str) -> dict:
        """Predictions for one stop. Counts against the (smaller) prediction budget."""
        return await self._get("/StopMonitoring",
                               {"agency": "SF", "stopcode": stop_id, "format": "json"}, "prediction")
