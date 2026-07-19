"""P1-9 #6984: API key Bearer token auth + dev bypass.

If TORTOISE_API_KEY is set, require `Authorization: Bearer <key>`.
If not set, warn but allow (dev mode).
"""
from __future__ import annotations

import logging
import os

_logger = logging.getLogger(__name__)

_API_KEY = os.environ.get("TORTOISE_API_KEY", "")


def require_auth(headers: dict | None = None) -> bool:
    """Check if the request is authorized. Returns True if allowed.

    Dev mode (no TORTOISE_API_KEY set): always True.
    Production mode: requires `Authorization: Bearer <key>` header match.
    """
    if not _API_KEY:
        _logger.warning("TORTOISE_API_KEY not set — running in dev mode (no auth)")
        return True

    if headers is None:
        return False

    auth = headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        return token == _API_KEY

    return False


def is_dev_mode() -> bool:
    """Return True if no API key is configured (dev bypass active)."""
    return not bool(_API_KEY)
