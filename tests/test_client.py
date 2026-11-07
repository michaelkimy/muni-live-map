import asyncio

import httpx
import pytest

from app.client import Client511, RateLimited, Upstream511Error
from app.state import RequestBudget

KEY = "SECRET-KEY-123"


def make_client(handler, budget=None):
    budget = budget or RequestBudget(60, 75)
    http = httpx.AsyncClient(base_url="https://api.511.org/transit",
                             transport=httpx.MockTransport(handler))
    return Client511(KEY, budget, http), budget


def test_fetch_decodes_bom_and_records_call(vehicles_raw):
    seen = {}

    def handler(request):
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=vehicles_raw)

    client, budget = make_client(handler)
    data = asyncio.run(client.vehicle_monitoring())
    assert "Siri" in data
    assert seen["params"] == {"agency": "SF", "format": "json", "api_key": KEY}
    assert budget.used() == 1


@pytest.mark.parametrize("status,exc", [(429, RateLimited), (500, Upstream511Error)])
def test_errors_never_mention_the_key(status, exc):
    client, _ = make_client(lambda r: httpx.Response(status))
    with pytest.raises(exc) as info:
        asyncio.run(client.vehicle_monitoring())
    assert KEY not in str(info.value)


def test_network_error_message_hides_url():
    def handler(request):
        raise httpx.ConnectError(f"failed to connect to {request.url}")

    client, _ = make_client(handler)
    with pytest.raises(Upstream511Error) as info:
        asyncio.run(client.vehicle_monitoring())
    assert KEY not in str(info.value)


def test_no_request_when_budget_is_used_up():
    calls = []
    budget = RequestBudget(1, 75)
    budget.record("poll")
    client, _ = make_client(lambda r: calls.append(r) or httpx.Response(200, content=b"{}"), budget)
    with pytest.raises(RateLimited):
        asyncio.run(client.vehicle_monitoring())
    assert calls == []
