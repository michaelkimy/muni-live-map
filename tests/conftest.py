import os
from pathlib import Path

import pytest

# Never start the real poller or need a real key during tests.
os.environ["POLLER_ENABLED"] = "false"

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def vehicles_raw() -> bytes:
    return (FIXTURES / "vehicles_sample.json").read_bytes()


@pytest.fixture
def stop_raw() -> bytes:
    """Real StopMonitoring response for stop 15694 (Market St & Stockton St)."""
    return (FIXTURES / "stop_sample.json").read_bytes()
