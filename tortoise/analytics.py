"""Funnel analytics event hooks (D10 #577) — fire-and-forget, non-blocking.

Wires the event schema from plan §6.4 so the signup → first_api_call funnel
is measurable. PostHog tooling itself is implemented via #528 (hosted vs
self-host decision); THIS module is the in-epic hook contract:

- first_api_call: server-side activation event (FastAPI middleware on /v1/*)
- tenant_provisioned: server event after provisioning
- All events joined on the Supabase user UUID (distinct_id)

R19: fire-and-forget with a bounded timeout; drop-on-failure — telemetry must
never degrade the API.
"""
from __future__ import annotations

import asyncio
import os
import time

# PostHog endpoint — wired when #528 lands (hosted or self-host decision).
_POSTHOG_ENDPOINT = os.environ.get("POSTHOG_ENDPOINT", "")
_POSTHOG_KEY = os.environ.get("POSTHOG_KEY", "")
_FIRE_TIMEOUT = float(os.environ.get("TORTOISE_ANALYTICS_TIMEOUT", "1.0"))
_enabled = bool(_POSTHOG_ENDPOINT and _POSTHOG_KEY)


def is_enabled() -> bool:
    return _enabled


def _fire(event: str, distinct_id: str, props: dict) -> None:
    if not _enabled:
        return
    import httpx
    try:
        payload = {
            "api_key": _POSTHOG_KEY,
            "event": event,
            "distinct_id": distinct_id,
            "properties": {**props, "timestamp": time.time()},
        }
        with httpx.Client(timeout=_FIRE_TIMEOUT) as client:
            client.post(f"{_POSTHOG_ENDPOINT.rstrip('/')}/capture/", json=payload)
    except Exception:
        pass  # drop-on-failure (R19)


def fire_and_forget(event: str, distinct_id: str, props: dict) -> None:
    """Non-blocking fire; never raises, never blocks the caller."""
    if not _enabled:
        return
    try:
        asyncio.get_event_loop().run_in_executor(None, _fire, event, distinct_id, props)
    except Exception:
        pass


def first_api_call(user_id: str, team_id: str, endpoint: str) -> None:
    fire_and_forget("first_api_call", user_id,
                    {"team_id": team_id, "endpoint": endpoint})


def tenant_provisioned(user_id: str, team_id: str, status: str = "confirmed") -> None:
    fire_and_forget("tenant_provisioned", user_id,
                    {"team_id": team_id, "status": status})
