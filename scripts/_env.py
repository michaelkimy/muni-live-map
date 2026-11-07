"""Tiny .env loader so Phase 1 scripts need only the standard library."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_api_key() -> str:
    """Return API_KEY_511 from the environment or the project's .env file."""
    key = os.environ.get("API_KEY_511")
    env_file = PROJECT_ROOT / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("API_KEY_511="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("API_KEY_511 not found. Add a line API_KEY_511=your_key to .env")
    return key


def urlopen(url: str, timeout: float = 30):
    """urllib.request.urlopen with a trusted certificate list.

    The python.org macOS installer ships without one, so plain urlopen fails with
    CERTIFICATE_VERIFY_FAILED. certifi (installed with httpx) provides it. Under
    a Python without certifi, this falls back to the system default.
    """
    import ssl
    import urllib.request
    try:
        import certifi
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        context = ssl.create_default_context()
    return urllib.request.urlopen(url, timeout=timeout, context=context)
