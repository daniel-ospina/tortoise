"""#4015 — the analytics emit must run OFF the event loop, and the Stripe
webhook's emit must be fail-soft.

``_track_analytics_event`` is a synchronous ``httpx.Client`` POST and
``hosted_api`` runs a SINGLE uvicorn worker, so an inline call from an
``async def`` handler stalls every concurrent request for the duration of a
Supabase round-trip (the #2988/#3498 class). #3498 built the offload seam
(``monitoring.run_control_plane_call`` → ``_cp_offload``); this issue is the
analytics lane's five call sites plus the Stripe webhook's failure path.

These tests are behavioural where it matters:

* the emit's HTTP call is recorded from a pool thread, never ``MainThread``
  (delete the offload and the site test fails — the mutation that matters);
* an offload failure is swallowed (telemetry never gates a request);
* the strict-mode registration guard still escapes (the #3821 contract is not
  weakened by routing through the shared entry point);
* a RAISING analytics emit cannot 500 the Stripe webhook nor drop the billing
  notification — asserted as an ORDER (notify before telemetry), because the
  notification is unrecoverable once ``webhook_event_marker`` has claimed the
  event (a retry sees ``is_first=False``).

The structural pin for "no async body calls the blocking helper directly"
lives in ``tests/test_health_ready_nonblocking.py``
(``test_control_plane_seam_calls_are_all_offloaded``, inventory includes
``_track_analytics_event``); this file adds the shape pin that the ONE direct
call site is inside the off-loop entry point.
"""

from __future__ import annotations

import ast
import asyncio
import json
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest

import tortoise.hosted_api as ha
import tortoise.monitoring as monitoring

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"


# ── the emit runs off the loop ─────────────────────────────────────────────


class _ThreadRecordingClient:
    """Stands in for ``httpx.Client`` — records the THREAD of every POST.

    ``_track_analytics_event`` imports ``httpx`` lazily and constructs the
    client inside the function, so patching the class is enough to observe
    WHERE the network call ran. A ``MainThread`` record is the defect.
    """

    call_threads: ClassVar[list[str]] = []
    posts: ClassVar[list[tuple[str, dict | None]]] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, **kwargs):
        type(self).call_threads.append(threading.current_thread().name)
        type(self).posts.append((url, kwargs.get("json")))

        class _Resp:
            status_code = 201  # 2xx → the delivered arm

        return _Resp()


def _prod_env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://analytics4015.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-4015")
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_TELEMETRY_STRICT", raising=False)


def test_analytics_emit_post_runs_off_main_thread(monkeypatch):
    """#4015 falsifier: the emit's HTTP call is not on ``MainThread``.

    Without the offload (the pre-#4352/#4015 shape — the helper called inline)
    the POST records ``MainThread`` and this fails. The record also pins WHICH
    pool: telemetry, never the auth-critical one.
    """
    _prod_env(monkeypatch)
    _ThreadRecordingClient.call_threads = []
    _ThreadRecordingClient.posts = []
    monkeypatch.setattr("httpx.Client", _ThreadRecordingClient)

    asyncio.run(ha._emit_analytics_off_loop(
        "org-4015", "artifact_copied", {"harness": "claude", "section": "a"}))

    threads = _ThreadRecordingClient.call_threads
    assert threads, "the analytics POST was never issued"
    assert all(t != "MainThread" for t in threads), (
        f"the analytics write ran on the event loop: {threads} (#4015)"
    )
    assert all(
        t.startswith(monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME)
        for t in threads
    ), (
        f"the analytics emit used {threads} — it must use the dedicated "
        f"telemetry pool ({monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME}), "
        "never an auth slot (#3498 review P1)"
    )
    url, payload = _ThreadRecordingClient.posts[-1]
    assert url.endswith("/rest/v1/analytics_events")
    assert payload["event_name"] == "artifact_copied"


def test_analytics_emit_is_never_raise_on_an_offload_failure(monkeypatch):
    """A missed wait bound (a saturated/wedged telemetry pool) is swallowed —
    telemetry must never gate the request path."""
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    def _slow(org_id, event_name, properties=None):
        time.sleep(0.4)

    monkeypatch.setattr(ha, "_track_analytics_event", _slow)

    async def _run():
        return await ha._emit_analytics_off_loop("org-4015", "event")

    assert asyncio.run(_run()) is None


def test_strict_registration_guard_still_escapes_the_entry_point(monkeypatch):
    """#3821 is preserved through the shared entry point: strict mode exists so
    a misregistered prop cannot be silently swallowed."""
    _prod_env(monkeypatch)
    monkeypatch.setenv("TORTOISE_TELEMETRY_STRICT", "1")

    async def _run():
        await ha._emit_analytics_off_loop(
            "org-4015", "not_a_registered_event", {"nope": 1})

    with pytest.raises(ha.UnregisteredTelemetryKey):
        asyncio.run(_run())


# ── the Stripe webhook's failure path ──────────────────────────────────────


def _stripe_request(payload: dict):
    """A real Starlette ``Request`` over a one-shot body — no TestClient, no
    lifespan, no DB: the webhook's seams are monkeypatched below."""
    from starlette.requests import Request

    raw = json.dumps(payload).encode()
    scope = {
        "type": "http", "http_version": "1.1", "method": "POST",
        "scheme": "https", "path": "/webhooks/stripe",
        "raw_path": b"/webhooks/stripe", "query_string": b"",
        "headers": [(b"stripe-signature", b"t=1,v1=deadbeef"),
                    (b"content-type", b"application/json")],
        "client": ("127.0.0.1", 12345), "server": ("testserver", 443),
        "root_path": "",
    }
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return Request(scope, receive)


class _Boom(RuntimeError):
    """A helper failure that is NOT an offload failure — it propagates
    through ``best_effort=True`` (which swallows only the seam's own
    ``ControlPlaneOffloadError``)."""


def _wire_stripe_webhook(monkeypatch, order: list[str], *,
                         analytics_raises=False, audit_raises=False,
                         tier_raises=False):
    """Drive ``webhooks_stripe`` to its first-processing block, in Supabase
    mode, with every seam stubbed and recorded into ``order``."""
    import tortoise.billing as billing
    import tortoise.notify as nt
    import tortoise.supabase_control as sc

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_4015")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")

    event = {
        "type": "checkout.session.completed",
        "id": "evt_4015",
        "data": {"object": {"client_reference_id": "org-4015",
                            "customer": "cus_4015", "subscription": "sub_4015"}},
    }
    monkeypatch.setattr(billing.StripeClient, "verify_webhook_signature",
                        lambda self, payload, sig: event)

    class _Cp:
        pass

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Cp())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    def _marker(cp, event_id, etype):
        order.append("claim")
        return True  # first processing

    monkeypatch.setattr(sc, "webhook_event_marker", _marker)

    def _tier(cp, org_id):
        order.append("tier")
        if tier_raises:
            raise _Boom("orgs row read failed")
        return "pro"

    monkeypatch.setattr(sc, "org_tier", _tier)

    def _apply(sdk, org_id, event):  # sync: the handler to_threads it
        return ("billing_upgrade", org_id)

    monkeypatch.setattr(ha, "_webhook_apply_event", _apply)

    async def _audit(*a, **k):
        order.append("audit")
        if audit_raises:
            raise _Boom("audit DSN malformed")

    monkeypatch.setattr(ha, "_async_audit", _audit)

    def _notify(kind, org, details=None):
        order.append("notify")

    monkeypatch.setattr(nt, "notify_billing_event", _notify)

    def _emit(org_id, event_name, properties=None):
        order.append("analytics")
        if analytics_raises:
            raise _Boom("supabase unreachable")

    monkeypatch.setattr(ha, "_track_analytics_event", _emit)
    return event


def test_stripe_webhook_analytics_failure_does_not_500_or_drop_the_notification(
        monkeypatch):
    """#4015 AC3: a raising analytics emit must neither 500 the webhook nor
    drop the billing notification.

    The event is CLAIMED inside the handler, so a 500 is retried into
    ``is_first=False`` and every ``is_first``-gated side effect is lost
    forever. The ORDER assertion is the load-bearing one: the notification
    fires before the (failing) telemetry — reverting either the reorder or the
    fail-soft guard reddens this test (mutation-verified).
    """
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order, analytics_raises=True)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200, (
        f"a telemetry failure 500'd the webhook: {resp.status_code} "
        f"{getattr(resp, 'body', b'')!r} (#4015)"
    )
    assert order.index("notify") < order.index("analytics"), (
        f"expected the billing notification BEFORE the telemetry attempt, "
        f"observed {order} — the notification is unrecoverable once the "
        "webhook event is claimed (#4015)"
    )
    assert "analytics" in order, (
        "the failing emit was never attempted — the guard is untested"
    )


def test_stripe_webhook_audit_failure_cannot_strand_the_notification(
        monkeypatch):
    """The sibling drop path the same reorder closes: ``_async_audit`` used to
    run BETWEEN the claim and the notification, so a raise there (e.g. a
    malformed ``TORTOISE_AUDIT_DSN``) 500'd the webhook and stranded the
    notification exactly as the analytics call did (#4015 review)."""
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order, audit_raises=True)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200
    assert order.index("notify") < order.index("audit")
    assert order.index("audit") < order.index("analytics")


def test_stripe_webhook_tier_read_failure_leaves_the_event_unclaimed(
        monkeypatch):
    """The other post-claim drop path: a read taken AFTER the claim turns a
    transient control-plane blip into a permanent loss. The tier read now
    precedes the claim, so a failure leaves the event UNCLAIMED and Stripe's
    retry reprocesses it (the 500 itself is honest and retryable)."""
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order, tier_raises=True)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 500  # retryable, and nothing was consumed
    assert "claim" not in order, (
        f"the tier read failed but the event was already CLAIMED: {order} — a "
        "retry would see is_first=False and the notification is lost (#4015)"
    )
    assert "notify" not in order


def test_stripe_webhook_analytics_offload_failure_does_not_500(monkeypatch):
    """An OFFLOAD failure (missed bound / saturated telemetry pool) is
    swallowed by the seam itself (``best_effort=True``), so the webhook stays
    200 and the notification still fires. This pins the SEAM contract; the
    handler's own guard is falsified by the raising-emit test above."""
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)

    def _slow(org_id, event_name, properties=None):
        order.append("analytics")
        time.sleep(0.4)

    monkeypatch.setattr(ha, "_track_analytics_event", _slow)
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200
    assert order.index("notify") < order.index("analytics")


# ── the shape pin: ONE direct call site, inside the off-loop entry point ────


def test_track_analytics_event_is_only_called_from_the_off_loop_entry_point():
    """#4015 AC1 (shape): ``hosted_api`` has exactly ONE **direct-call** site
    for the blocking helper — the ``_cp_offload``-wrapped lambda inside
    ``_emit_analytics_off_loop``. A new direct call (the regression this issue
    exists for) adds a second site and fails here, and the site is off-loop by
    construction rather than by argument.

    Out of this pin's scope by design: partial-application lanes — the
    capture-cost ``asyncio.to_thread(_track_analytics_event, …)`` and
    ``mcp_server``'s retained emitter — which carry their own off-loop
    guarantees and are pinned by their own tests.
    """
    tree = ast.parse(HOSTED_API.read_text())
    call_sites: list[tuple[str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_track_analytics_event"):
                call_sites.append((fn.name, node.lineno))

    assert call_sites, "_track_analytics_event is no longer called at all?"
    assert len(call_sites) == 1, (
        "every analytics emit must go through _emit_analytics_off_loop, but "
        f"_track_analytics_event is called from: {call_sites} (#4015)"
    )
    enclosing, lineno = call_sites[0]
    assert enclosing == "_emit_analytics_off_loop", (
        f"the one direct call site is in {enclosing!r} (line {lineno}) — it "
        "must be the off-loop entry point (#4015)"
    )

    # And that entry point's call is wrapped by the offload seam.
    helper = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_emit_analytics_off_loop")
    offloads = [
        n for n in ast.walk(helper)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "_cp_offload"
    ]
    assert offloads, (
        "_emit_analytics_off_loop no longer offloads its call — the blocking "
        "POST is back on the event loop (#4015)"
    )
    kwargs = {kw.arg: kw.value for kw in offloads[0].keywords}
    assert (isinstance(kwargs.get("best_effort"), ast.Constant)
            and kwargs["best_effort"].value is True), (
        "the analytics emit lost best_effort=True — telemetry would be able "
        "to fail a request (#3498 review P1)"
    )
