"""Server-side PostHog analytics events (#528) — account/usage telemetry.

Consent framing: the events emitted here (tenant_provisioned,
api_key_created, first_api_call) are account/usage telemetry for the
tenant lifecycle — covered by the privacy policy, with PostHog as a
disclosed data processor (US Cloud project, see website/privacy.html +
website/dpa.html). They are NOT gated by the web consent banner: the
banner (website/consent.js) gates CLIENT-side tracking (posthog-js);
server telemetry is operational, consistent with the existing audit-event
logger, and cannot be opted out client-side (a server that records who
provisioned a team must keep that record).

Fail-safe by design (R19 — telemetry never degrades the API):
  * Disabled when POSTHOG_API_KEY is empty or starts with "__" (the same
    placeholder convention as website/consent.js).
  * Every call wrapped in try/except — capture() never raises.
  * posthog.capture is sync + buffered (the HTTP send happens in posthog's
    background flush thread), so handlers run it via asyncio.to_thread
    (fire-and-forget, consistent with _async_audit in hosted_api.py).

Identity: distinct_id is the Supabase user UUID wherever it is resolvable
(created_by on provision, key creator on first_api_call), falling back to
the team id — this joins the web funnel (user_signed_up with
distinct_id = user UUID) to server events.
"""
from __future__ import annotations

import os
import threading

import posthog

POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "")
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")

posthog.project_api_key = POSTHOG_API_KEY
posthog.host = POSTHOG_HOST
# Disabled when the key is missing or a placeholder ("__..." — the same
# convention consent.js uses to detect a non-wired key).
posthog.disabled = not POSTHOG_API_KEY or POSTHOG_API_KEY.startswith("__")

# In-process dedup for first_api_call (activation): one event per team per
# process. Thread-safe via the lock (capture may be called from multiple
# asyncio.to_thread workers concurrently).
# NOTE: single-worker caveat — under multiple Fly replicas each worker
# dedups independently, so a team's first call could in theory be recorded
# once per worker. Acceptable for funnel activation; a cross-worker store
# (Redis / FalkorDB) is the follow-up if exact-once is ever required.
_first_api_call_seen: set[str] = set()
_first_api_call_lock = threading.Lock()


def is_enabled() -> bool:
    """True when PostHog is wired (non-placeholder key configured)."""
    return not posthog.disabled


def capture(event: str, distinct_id: str, properties: dict | None = None) -> None:
    """Record an event. Never raises; no-op when disabled.

    Sync + buffered — async handlers should call via
    ``await asyncio.to_thread(capture, ...)`` so the enqueue never blocks
    the event loop (the actual HTTP send happens in posthog's flush thread).
    """
    if posthog.disabled:
        return
    try:  # noqa: SIM105
        posthog.capture(
            distinct_id=distinct_id,
            event=event,
            properties=properties or {},
        )
    except Exception:
        pass  # drop-on-failure — analytics must never break the API


def tenant_provisioned(
    distinct_id: str, team_id: str, team_name: str, tier: str, graph_name: str
) -> None:
    """Team provisioned (server, on /internal/provision success)."""
    capture(
        "tenant_provisioned",
        distinct_id,
        {"team_id": team_id, "team_name": team_name, "tier": tier,
         "graph_name": graph_name},
    )


def api_key_created(
    distinct_id: str, team_id: str, key_prefix: str, key_id: str, source: str
) -> None:
    """API key created (source='provision' or 'team_keys')."""
    capture(
        "api_key_created",
        distinct_id,
        {"team_id": team_id, "key_prefix": key_prefix, "key_id": key_id,
         "source": source},
    )


def first_api_call_pending(team_id: str) -> bool:
    """Cheap thread-safe peek: True only before the team's activation event
    has been claimed in this process. Guards against spawning a worker
    thread for every authenticated request once the team has fired."""
    if posthog.disabled:
        return False
    with _first_api_call_lock:
        return team_id not in _first_api_call_seen


def first_api_call(
    distinct_id: str, team_id: str, endpoint: str, method: str
) -> None:
    """Activation event — deduped per team (in-process set, thread-safe).

    The dedup claim is authoritative here even when the caller also peeked
    via first_api_call_pending (idempotent — safe for direct callers too).
    """
    with _first_api_call_lock:
        if team_id in _first_api_call_seen:
            return
        _first_api_call_seen.add(team_id)
    capture(
        "first_api_call",
        distinct_id,
        {"team_id": team_id, "endpoint": endpoint, "method": method},
    )
