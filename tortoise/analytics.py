"""Server-side PostHog analytics events (#528) — account/usage telemetry.

Consent framing: the events emitted here (tenant_provisioned,
api_key_created, first_api_call, onboarding_seed_complete,
onboarding_decide_complete) are account/usage telemetry for the
tenant lifecycle — covered by the privacy policy, with PostHog as a
disclosed data processor (US Cloud project, see website/privacy.html +
website/dpa.html). They are NOT gated by the web consent banner: the
banner (website/consent.js) gates CLIENT-side tracking (posthog-js);
server telemetry is operational, consistent with the existing audit-event
logger, and cannot be opted out client-side (a server that records who
provisioned an org must keep that record).

Fail-safe by design (R19 — telemetry never degrades the API):
  * Disabled when POSTHOG_API_KEY is empty or starts with "__" (the same
    placeholder convention as website/consent.js).
  * Every call wrapped in try/except — capture() never raises.
  * posthog.capture is sync + buffered (the HTTP send happens in posthog's
    background flush thread), so handlers run it via asyncio.to_thread
    (fire-and-forget, consistent with _async_audit in hosted_api.py).

Identity: distinct_id is the Supabase user UUID wherever it is resolvable
(created_by on provision, key creator on first_api_call), falling back to
the org id — this joins the web funnel (user_signed_up with
distinct_id = user UUID) to server events.

Once-only events have TWO shapes here, and which one applies is a property
of the DOMAIN fact, not of this module:
  * ``first_api_call`` — no durable once-only fact exists in the graph, so
    it keeps an in-process set (see the single-worker caveat documented on
    ``_first_api_call_seen`` below).
  * ``onboarding_seed_complete`` / ``onboarding_decide_complete`` (#2006
    W11) — the durable once-only fact ALREADY exists: the ``COMPLETED_STEP``
    edge's new creation, returned as ``created`` by
    ``onboarding.state.write_completed_step``. The CALLER emits only when it
    observed that ``created=True``, so these are exact-once per edge
    creation by construction — restart-safe and multi-worker-safe, with no
    second dedup store, no threshold and no in-process set. (The one W11
    edge with a sanctioned REMOVAL path is ``decide-completed`` — the #3912
    false-completion repair — after which a genuine re-completion creates
    the edge again and re-emits; see ``onboarding_decide_complete``.)
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

# In-process dedup for first_api_call (activation): one event per org per
# process. Thread-safe via the lock (capture may be called from multiple
# asyncio.to_thread workers concurrently).
# NOTE: single-worker caveat — under multiple Fly replicas each worker
# dedups independently, so an org's first call could in theory be recorded
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
    distinct_id: str, org_id: str, org_name: str, tier: str, graph_name: str
) -> None:
    """Org provisioned (server, on /internal/provision success)."""
    capture(
        "tenant_provisioned",
        distinct_id,
        {"org_id": org_id, "org_name": org_name, "tier": tier,
         "graph_name": graph_name},
    )


def api_key_created(
    distinct_id: str, org_id: str, key_prefix: str, key_id: str, source: str
) -> None:
    """API key created (source='provision' or 'org_keys')."""
    capture(
        "api_key_created",
        distinct_id,
        {"org_id": org_id, "key_prefix": key_prefix, "key_id": key_id,
         "source": source},
    )


def first_api_call_pending(org_id: str) -> bool:
    """Cheap thread-safe peek: True only before the org's activation event
    has been claimed in this process. Guards against spawning a worker
    thread for every authenticated request once the org has fired."""
    if posthog.disabled:
        return False
    with _first_api_call_lock:
        return org_id not in _first_api_call_seen


def first_api_call(
    distinct_id: str, org_id: str, endpoint: str, method: str
) -> None:
    """Activation event — deduped per org (in-process set, thread-safe).

    The dedup claim is authoritative here even when the caller also peeked
    via first_api_call_pending (idempotent — safe for direct callers too).
    """
    with _first_api_call_lock:
        if org_id in _first_api_call_seen:
            return
        _first_api_call_seen.add(org_id)
    capture(
        "first_api_call",
        distinct_id,
        {"org_id": org_id, "endpoint": endpoint, "method": method},
    )


def onboarding_seed_complete(
    distinct_id: str, org_id: str, source: str
) -> None:
    """Onboarding funnel: the seed step completed (#2006 W11).

    EMIT ONLY when the caller observed the ``first-points-filed``
    ``COMPLETED_STEP`` edge being NEWLY created — i.e. gated on
    ``onboarding.state.write_completed_step(...)["created"]``. That
    edge-creation transition IS the once-only fact, so this event is
    exact-once per edge creation by construction (restart-safe,
    multi-worker-safe). There is deliberately NO in-process dedup set here
    (unlike ``first_api_call``) and no threshold: a replay that reports
    ``created=False`` must emit nothing.

    ``source`` names the write path that observed the creation
    ('seed' | 'starter_seed' | 'checkpoint' | 'mcp_auto' | 'state_router')
    so the funnel read can attribute the entry point.
    """
    capture(
        "onboarding_seed_complete",
        distinct_id,
        {"org_id": org_id, "source": source},
    )


def onboarding_decide_complete(
    distinct_id: str, org_id: str, source: str
) -> None:
    """Onboarding funnel: the decide step completed (#2006 W11).

    EMIT ONLY when the caller observed the ``decide-completed``
    ``COMPLETED_STEP`` edge being NEWLY created — the same structural gate
    as ``onboarding_seed_complete`` (see it for the full contract).
    ``decide-completed`` is the self-fork display row; the build fork's
    ``catalog-presented`` carries no W11 event, and ``harness-connected`` and
    ``connection-written`` (#3451 — a client-side config-write trace, not a
    funnel transition) are deliberately uninstrumented.

    CAVEAT — ``decide-completed`` is the one W11 edge with a sanctioned
    REMOVAL path (the #3912 false-completion repair, an operator-only
    ``graph-scripts`` tool). After such a repair the next genuine decide
    write recreates the edge and reports ``created=True`` again, so the org
    re-emits. The invariant is exact-once per EDGE CREATION, not per org
    forever: the funnel read should therefore dedupe this event per org
    when a repaired cohort is in scope.
    """
    capture(
        "onboarding_decide_complete",
        distinct_id,
        {"org_id": org_id, "source": source},
    )
