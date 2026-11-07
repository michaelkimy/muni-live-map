"""Settings, read from environment variables or the project's .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    poll_interval_s: float = 75.0
    rate_limit_per_hour: int = 60
    poller_enabled: bool = True


def _bool(value: str) -> bool:
    return value.strip().lower() not in ("0", "false", "no", "off", "")


def load_settings() -> Settings:
    # Real environment variables win over .env (that's how the deploy host sets them).
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = Settings(
        api_key=os.environ.get("API_KEY_511", "").strip(),
        poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "75")),
        rate_limit_per_hour=int(os.environ.get("RATE_LIMIT_PER_HOUR", "60")),
        poller_enabled=_bool(os.environ.get("POLLER_ENABLED", "true")),
    )
    if settings.poller_enabled and not settings.api_key:
        raise RuntimeError("API_KEY_511 is not set. Add it to .env or the environment.")
    return settings
