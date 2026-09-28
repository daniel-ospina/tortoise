"""#4456 — ``notify_billing_event`` must run OFF the event loop in the Stripe webhook.

``notify_billing_event`` (``tortoise/notify.py``) is blocking sync HTTP — Resend
via ``httpx.post(..., timeout=15.0)`` plus Telegram on its own 15 s timeout —
and ``hosted_api`` runs a SINGLE uvicorn worker (``Dockerfile.hosted``, no
``--workers``), so calling it inline from the ``webhooks_stripe`` coroutine held
the one event loop for up to ~30 s and stalled every concurrent request. Same
class as #2988 / #3498 / #4015; #4015 covered only ``_track_analytics_event``.

The fix routes the notify through the #3498 offload seam
(``_cp_offload`` → the dedicated ``telemetry`` daemon pool) rather than the
issue's proposed ``asyncio.to_thread``, for the #2850 reason: the default
executor's workers are non-daemon and joined at shutdown, and they are the
SHARED pool the /health probe and the abuse hooks use. ``best_effort=True``
plus a call-site guard keep the never-raise contract at the hand-off, because
a hand-off is a NEW failure mode the inline call did not have.

These tests are behavioural where it matters:

* the notify's ``threading.current_thread().name`` is NEVER ``MainThread``,
  and it is the telemetry pool (delete the offload → the falsifier fails);
* a notifier that BLOCKS does not freeze the loop — the loop keeps ticking AND
  a concurrent ``/health`` request is answered INSIDE the blocking window
  (the same invariant ``tests/test_read_routes_loop_responsiveness.py`` pins
  for the graph reads);
* a RAISING notifier cannot 500 the webhook nor skip the audit/analytics legs;
* an offload bound miss is swallowed (best_effort), so the webhook still 200s —
  and a bound miss on a QUEUED submission must NOT cancel it (the #4456 P1:
  a still-queued ``Future.cancel()`` succeeds and the worker then SKIPS the
  callable), so the notify is submitted ``cancel_on_timeout=False``;
* a REFUSAL (saturated backlog — the callable never ran) is distinguishable
  per call (``OFFLOAD_REFUSED``) and escalated through ``alert_operator`` on a
  incident whose SUBJECT is platform-scoped (one outage = one issue) rather
  than swallowed, with the rate-limited ERROR floor AC3 promises when the
  alert channel is absent;
* a structural pin: the direct call site sits INSIDE the ``_cp_offload``
  callable, so a revert to the inline shape fails regardless of argument.

The ``best_effort`` op-set invariant is co-owned by
``tests/test_control_plane_offload_3498.py::test_never_raise_offload_sites_pass_best_effort``
(``billing_notify`` is declared there).
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import threading
import time
from pathlib import Path

import httpx

import tortoise.hosted_api as ha
import tortoise.monitoring as monitoring
import tortoise.notify as nt

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"

# A stall long enough to be unambiguous, short enough to keep CI quick.
STALL_S = 1.0
# The loop's own tick period while the notify is blocked.
TICK_S = 0.02
# Ticks completed INSIDE the blocking window. A blocked loop yields 0 (the
# next tick can only run once the freeze releases). Loose on purpose.
MIN_TICKS_IN_STALL = 5


# ── webhook wiring (mirrors tests/test_analytics_offloop_4015.py) ──────────


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


def _wire_stripe_webhook(monkeypatch, order: list[str]):
    """Drive ``webhooks_stripe`` to its first-processing block, with every
    seam stubbed and recorded into ``order`` (Supabase mode)."""
    import tortoise.billing as billing
    import tortoise.supabase_control as sc

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_4456")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")

    event = {
        "type": "checkout.session.completed",
        "id": "evt_4456",
        "data": {"object": {"client_reference_id": "org-4456",
                            "customer": "cus_4456", "subscription": "sub_4456"}},
    }
    monkeypatch.setattr(billing.StripeClient, "verify_webhook_signature",
                        lambda self, payload, sig: event)

    class _Cp:
        pass

    monkeypatch.setattr(sc, "get_control_plane", lambda: _Cp())
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sc, "webhook_event_marker",
                        lambda cp, event_id, etype: True)
    monkeypatch.setattr(sc, "org_tier", lambda cp, org_id: "pro")

    monkeypatch.setattr(ha, "_webhook_apply_event",
                        lambda sdk, org_id, event: ("billing_upgrade", org_id))

    async def _audit(*a, **k):
        order.append("audit")

    monkeypatch.setattr(ha, "_async_audit", _audit)

    def _emit(org_id, event_name, properties=None):
        order.append("analytics")

    monkeypatch.setattr(ha, "_track_analytics_event", _emit)
    return event


# ── the notify runs off the loop ───────────────────────────────────────────


def test_stripe_billing_notify_runs_off_the_event_loop(monkeypatch):
    """#4456 falsifier: the notifier's own view of the loop must be a worker
    thread, and that worker must be the telemetry pool.

    Without the off-load the notifier runs ON the loop (``MainThread``), which
    is the ~30 s stall this issue is about.
    """
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)
    seen: list[str] = []

    def _notify(kind, org, details=None):
        order.append("notify")
        seen.append(threading.current_thread().name)

    monkeypatch.setattr(nt, "notify_billing_event", _notify)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200, resp.body[:200]
    assert seen, "the billing notifier was never reached — this run proves nothing"
    assert all(name != "MainThread" for name in seen), (
        f"notify_billing_event ran ON the event loop: {seen} — one stalled "
        f"notification holds every concurrent request for up to ~30 s (#4456)"
    )
    assert all(
        name.startswith(monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME)
        for name in seen
    ), (
        f"the notify used {seen} — it must use the dedicated telemetry daemon "
        f"pool ({monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME}), never the "
        "loop's shared default executor (the #2850 non-daemon shutdown join) "
        "nor an auth slot (#4456)"
    )


def test_stripe_billing_notify_does_not_freeze_the_event_loop(monkeypatch):
    """#4456: a BLOCKED notifier must not stall the loop.

    Two assertions that fail on the inline shape:

    * ``ticks_in_stall >= MIN_TICKS_IN_STALL`` — the loop kept scheduling while
      the notify was blocked (inline yields 0: the ticker's next wake-up cannot
      run until the freeze releases);
    * ``/health`` answered INSIDE the blocking window — the user-visible
      consequence: a concurrent client is served DURING the stall, not queued
      behind it. /health is pure in-memory (#2850), so a delay in it can only
      come from the loop being blocked, never from a saturated executor.

    MUTATION (run manually, and the reason this test is load-bearing): revert
    the call site to the inline ``notify_billing_event(...)`` → the block runs
    on the loop, the ticker completes 0 ticks in the window, and ``/health``
    cannot be served until the block releases.
    """
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)

    window: dict = {}

    def _blocking_notify(kind, org, details=None):
        order.append("notify")
        window["entered"] = time.perf_counter()
        time.sleep(STALL_S)  # stand-in for a slow Resend + Telegram send
        window["exited"] = time.perf_counter()

    monkeypatch.setattr(nt, "notify_billing_event", _blocking_notify)

    async def _drive():
        from tortoise.hosted_api import app

        ticks: list[float] = []
        stop = {"done": False}

        async def _ticker():
            while not stop["done"]:
                ticks.append(time.perf_counter())
                await asyncio.sleep(TICK_S)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            tick = asyncio.create_task(_ticker())
            await asyncio.sleep(TICK_S * 2)  # ticker mid-sleep before the notify
            handler = asyncio.create_task(
                ha.webhooks_stripe(_stripe_request({})))
            for _ in range(1000):  # let the handler reach the blocking notify
                if "entered" in window:
                    break
                await asyncio.sleep(TICK_S / 2)
            assert "entered" in window, (
                "the billing notifier was never reached — this run proves nothing"
            )
            # Sampled BEFORE /health: the assertion below rests on /health being
            # answered while the notify is STILL in flight.
            handler_in_flight = not handler.done()
            health = await ac.get("/health")
            health_done = time.perf_counter()
            resp = await handler
            stop["done"] = True
            await tick
        return ticks, health, health_done, resp, handler_in_flight

    ticks, health, health_done, response, handler_in_flight = asyncio.run(_drive())

    entered, exited = window["entered"], window["exited"]
    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during a "
        f"blocked notification (need {MIN_TICKS_IN_STALL}) — the notify is "
        f"blocking the event loop, so every concurrent request waits for the "
        f"Resend/Telegram round trips (#4456)"
    )

    assert health.json()["status"] in {"ok", "degraded"}, health.text
    assert health_done < exited, (
        "the /health probe was NOT served while the notify was blocked "
        "(answered %.0fms AFTER the block released) — a concurrent request "
        "queued behind the notification instead of being served (#4456)"
        % ((health_done - exited) * 1000)
    )
    assert handler_in_flight, (
        "the webhook had already returned before /health was issued, so this "
        "run proves nothing about a request served DURING the blocking window "
        "(#4456)"
    )
    assert response.status_code == 200, response.body[:200]


# ── the never-raise contract survives the hand-off ─────────────────────────


def test_stripe_billing_notify_raise_cannot_500_the_webhook(monkeypatch):
    """#4456 AC4: the hand-off must not let an exception surface.

    ``notify_billing_event`` is documented never-raise, but the hand-off is a
    NEW failure mode the inline call did not have — so the call site guards it.
    A raise must neither 500 the webhook (the event is already CLAIMED, so a
    500 is retried into ``is_first=False`` and the payment's ack is lost) nor
    skip the audit/analytics legs that follow it.
    """
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)

    def _boom(kind, org, details=None):
        order.append("notify")
        raise RuntimeError("resend client exploded")

    monkeypatch.setattr(nt, "notify_billing_event", _boom)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200, (
        f"a notifier raise 500'd a claimed webhook: {resp.status_code} "
        f"{resp.body[:200]!r} (#4456)"
    )
    assert order == ["notify", "audit", "analytics"], (
        f"a notifier raise aborted the following legs: {order} — the audit and "
        "telemetry legs must still run (#4456)"
    )


def test_stripe_billing_notify_bound_miss_returns_promptly(monkeypatch):
    """A notify that outlives the offload's wait bound must NOT hold the webhook.

    This is the RUNNING case: on a FREE pool the worker dequeues immediately,
    so the future is already RUNNING when the 0.1 s bound expires and
    ``wait_for``'s cancel is a no-op. The seam therefore abandons only the
    AWAIT and the send completes on the (daemon) worker; ``best_effort=True``
    swallows the ``ControlPlaneOffloadError``, so the handler returns promptly
    and is never 500'd.

    The QUEUED case is the one that LOSES the notification — a still-queued
    submission is genuinely cancelled by ``wait_for`` (via
    ``asyncio.wrap_future`` → ``Future.cancel()``), and the pool worker then
    SKIPS it (``set_running_or_notify_cancel()`` returns False). That case is
    neither simulated nor asserted here (the pool must be saturated for it);
    it is covered by ``test_stripe_billing_notify_queued_submission_still_runs``
    below, which is the #4456 P1 falsifier.

    The TIMING assertion is the discriminating one: inline, the handler blocks
    for the full send (the mutation this test exists to catch); a 1.0 s send
    against a 0.1 s bound separates the two shapes with room to spare. The
    ``best_effort=True`` flag itself is pinned structurally by
    ``test_webhook_notify_direct_call_is_inside_the_offload_boundary`` and by
    ``test_control_plane_offload_3498.py::test_never_raise_offload_sites_pass_best_effort``;
    this test pins the OUTCOME (a bound miss neither 500s nor blocks the
    handler).

    Deterministic: the worker signals BEFORE it blocks, so this never races
    thread-pool start-up against the bound.
    """
    order: list[str] = []
    started = threading.Event()
    _wire_stripe_webhook(monkeypatch, order)

    def _slow_notify(kind, org, details=None):
        order.append("notify")
        started.set()
        time.sleep(1.0)

    monkeypatch.setattr(nt, "notify_billing_event", _slow_notify)
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.1)

    t0 = time.perf_counter()
    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))
    elapsed = time.perf_counter() - t0

    assert resp.status_code == 200, (
        f"a notify offload bound miss 500'd the webhook: {resp.status_code} "
        f"{resp.body[:200]!r} — best_effort must swallow the OFFLOAD failure "
        f"(#4456)"
    )
    assert started.wait(5.0), "the notify worker never started"
    assert order == ["notify", "audit", "analytics"], order
    assert elapsed < 0.5, (
        f"the webhook took {elapsed:.2f}s for a 1.0s notify with a 0.1s offload "
        f"bound — it waited for the notification instead of letting the seam "
        f"abandon the worker (inline shape), stalling every concurrent request "
        f"for the full Resend/Telegram round trips (#4456)"
    )


# ── the QUEUED bound miss: the seam must not CANCEL the notification ───────


def test_stripe_billing_notify_queued_submission_still_runs(monkeypatch):
    """#4456 P1: a bound miss on a QUEUED notify must not CANCEL it.

    A still-queued ``concurrent.futures.Future`` is NOT merely abandoned by
    ``asyncio.wait_for``: ``asyncio.wrap_future`` propagates the cancellation
    to the concurrent future, whose ``cancel()`` SUCCEEDS while queued. When a
    worker later dequeues it, ``set_running_or_notify_cancel()`` returns False
    and ``_SingleSlotWorker._loop`` SKIPS the callable — the notification is
    dropped, not delayed. ``best_effort=True`` swallows the
    ``ControlPlaneOffloadError``, so the webhook still 200s; and because the
    ``WebhookEvent`` marker was committed BEFORE the notify
    (``is_first=True``), Stripe's retry sees ``is_first=False`` and the
    notification is lost PERMANENTLY.

    This test saturates all ``CONTROL_PLANE_TELEMETRY_WORKERS`` slots with a
    FRESH pool (so it cannot be perturbed by another test's leftover work),
    drives the real ``webhooks_stripe`` handler so the notify is submitted
    while every worker is busy (QUEUED), lets the 0.1 s bound expire, and
    then releases the blockers. The notification must ACTUALLY RUN.

    On the shipped shape this fails: the queued callable is cancelled and
    never executes (``notify_ran`` is never set).
    """
    order: list[str] = []
    notify_ran = threading.Event()
    _wire_stripe_webhook(monkeypatch, order)

    def _notify(kind, org, details=None):
        order.append("notify")
        notify_ran.set()

    monkeypatch.setattr(nt, "notify_billing_event", _notify)
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.1)

    # A FRESH pool, so the saturation is deterministic regardless of what the
    # process-wide telemetry singleton is doing for another test.
    fresh = monitoring._SingleSlotWorker(
        "test-4456-queued",
        workers=monitoring.CONTROL_PLANE_TELEMETRY_WORKERS,
        max_backlog=monitoring.CONTROL_PLANE_TELEMETRY_BACKLOG)
    monkeypatch.setattr(monitoring, "control_plane_worker",
                        lambda pool="auth": fresh)

    blocker_started = [threading.Event()
                       for _ in range(monitoring.CONTROL_PLANE_TELEMETRY_WORKERS)]
    release = threading.Event()

    def _make_blocker(ev: threading.Event):
        def _block():
            ev.set()
            release.wait(10.0)
        return _block

    for ev in blocker_started:
        fresh.submit(_make_blocker(ev))

    try:
        for ev in blocker_started:
            assert ev.wait(5.0), (
                "a telemetry worker never picked up its blocker — the pool "
                "was not saturated, so the notify would not be queued"
            )
        # Every worker is busy: the notify submission lands in the QUEUE.
        t0 = time.perf_counter()
        resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))
        elapsed = time.perf_counter() - t0
        assert resp.status_code == 200, (
            f"a queued-notify bound miss 500'd the webhook: {resp.status_code} "
            f"{resp.body[:200]!r} (#4456)"
        )
        assert elapsed < 0.5, (
            f"the webhook took {elapsed:.2f}s with a 0.1s bound — it waited "
            f"for the queued notification instead of returning at the bound "
            f"(#4456)"
        )
        assert not notify_ran.is_set(), (
            "the notifier ran before the bound expired — the pool was not "
            "actually saturated, so this run does not exercise the QUEUED "
            "case (#4456)"
        )
    finally:
        release.set()

    assert notify_ran.wait(5.0), (
        "the QUEUED billing notification was CANCELLED by the offload wait "
        "bound and never ran — the claimed Stripe event's notification is "
        "lost permanently because the retry sees is_first=False (#4456)"
    )
    assert "notify" in order, order


def test_stripe_billing_notify_refusal_escalates_to_operator_alert(
        monkeypatch, caplog):
    """#4456: a REFUSED submission is a REAL drop — distinguishable per call
    (``OFFLOAD_REFUSED``) and escalated through the existing
    ``operator_alert`` path, with the ERROR floor AC3 requires.

    Three things are pinned here that the round-1 shape did not pin:

    * the escalation reaches ``operator_alert.alert_operator`` (the real
      driver), not a test-local double of the wrapper;
    * the incident SUBJECT is PLATFORM-SCOPED (``""``) per the issue's
      recorded plan — one Resend account serves every team, so a per-org key
      would file N issues for one outage — while the org travels in the
      detail (dedup is ``(kind, subject)``);
    * the rate-limited ERROR line fires at the site (plan §4), which is the
      promised floor when the alert channel is ABSENT.
    """
    import tortoise.operator_alert as oa

    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order)
    monkeypatch.setattr(nt, "notify_billing_event",
                        lambda kind, org, details=None: order.append("notify"))

    async def _refuse(fn, *, op, pool="auth", timeout=None,
                      cancel_on_timeout=True):
        raise monitoring.ControlPlaneOffloadError(
            f"control-plane call {op!r} pool backlog full", refused=True)

    monkeypatch.setattr(ha, "run_control_plane_call", _refuse)
    seen: list[tuple] = []
    monkeypatch.setattr(
        oa, "alert_operator",
        lambda kind, org_id, detail=None: seen.append((kind, org_id, detail)))
    # The ERROR floor is rate-limited process-wide; a prior test must not
    # silence this one.
    monkeypatch.setattr(ha, "_LAST_BILLING_REFUSED_LOG", None)

    with caplog.at_level(logging.ERROR, logger="tortoise.hosted_api"):
        resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200, resp.body[:200]
    assert "notify" not in order, (
        "the notifier ran despite the seam refusing the submission — a real "
        "refusal must not execute the callable (#4456)"
    )
    assert seen == [(oa.BILLING_NOTIFY_REFUSED_KIND, "",
                     {"op": "billing_notify",
                      "event_type": "checkout.session.completed",
                      "org_id": "org-4456"})], (
        f"a REFUSED billing notification was not escalated with a "
        f"PLATFORM subject (alert got {seen}) — the claimed event's "
        "notification is lost silently, and a per-org key would file one "
        "issue per tenant for one outage (#4456)"
    )
    heard = [r.getMessage() for r in caplog.records
             if r.name == "tortoise.hosted_api" and r.levelno >= logging.ERROR]
    assert any("REFUSED" in m and "billing notify" in m for m in heard), (
        "no ERROR line was emitted for the refusal — with no alert channel "
        "(no DR_ISSUES_PAT, or an unbuildable object store) the permanent "
        "loss would read only as routine best-effort WARNINGs (#4456 AC3). "
        f"ERROR lines seen: {heard!r}"
    )


def test_stripe_billing_notify_refused_through_the_real_seam(monkeypatch,
                                                             caplog):
    """#4456: the REFUSAL discriminator, exercised through the REAL path.

    The escalation depends entirely on ``monitoring.py`` computing
    ``refused`` from the submission's own future, but the other refusal tests
    FAKE the exception (or assert only the exception TYPE). Here a FRESH
    ``_SingleSlotWorker(workers=1, max_backlog=1)`` is saturated with a
    blocker plus one queued filler, so the real ``webhooks_stripe`` →
    ``_cp_offload`` → ``run_control_plane_call`` submission is genuinely
    refused by the pool. Mutating ``refused=False`` at the seam makes this
    test the one that fails.
    """
    import tortoise.operator_alert as oa

    order: list[str] = []
    notify_ran = threading.Event()
    _wire_stripe_webhook(monkeypatch, order)

    def _notify(kind, org, details=None):
        order.append("notify")
        notify_ran.set()

    monkeypatch.setattr(nt, "notify_billing_event", _notify)

    fresh = monitoring._SingleSlotWorker("test-4456-real-refusal",
                                         workers=1, max_backlog=1)
    monkeypatch.setattr(monitoring, "control_plane_worker",
                        lambda pool="auth": fresh)

    blocker_started = threading.Event()
    release = threading.Event()

    def _block():
        blocker_started.set()
        release.wait(10.0)

    def _filler():
        release.wait(10.0)

    fresh.submit(_block)
    assert blocker_started.wait(5.0), "the blocker never occupied the slot"
    fresh.submit(_filler)  # fills the one-slot backlog

    seen: list[tuple] = []
    monkeypatch.setattr(
        oa, "alert_operator",
        lambda kind, org_id, detail=None: seen.append((kind, org_id, detail)))
    monkeypatch.setattr(ha, "_LAST_BILLING_REFUSED_LOG", None)

    try:
        with caplog.at_level(logging.ERROR, logger="tortoise.hosted_api"):
            resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))
    finally:
        release.set()

    assert resp.status_code == 200, (
        f"a genuinely refused notify 500'd the claimed webhook: "
        f"{resp.status_code} {resp.body[:200]!r} (#4456)"
    )
    assert not notify_ran.is_set(), (
        "the notifier RAN even though the pool refused the submission — the "
        "pool was not saturated, so this run does not exercise the real "
        "refusal path (#4456)"
    )
    assert seen == [(oa.BILLING_NOTIFY_REFUSED_KIND, "",
                     {"op": "billing_notify",
                      "event_type": "checkout.session.completed",
                      "org_id": "org-4456"})], (
        f"a refusal exercised through the real pool did not escalate (alert "
        f"got {seen}) — the escalation would never fire in production "
        "(#4456)"
    )
    heard = [r.getMessage() for r in caplog.records
             if r.name == "tortoise.hosted_api" and r.levelno >= logging.ERROR]
    assert any("REFUSED" in m for m in heard), heard


def test_billing_notify_refused_error_floor_is_rate_limited(monkeypatch,
                                                            caplog):
    """#4456 plan §4: the ERROR floor is one line per window.

    ONE telemetry-pool saturation refuses a notify per billing webhook for
    EVERY tenant, so an unthrottled ERROR would make the alert mechanism
    amplify the outage it reports — the same ruling as
    ``operator_alert._log_shed``.
    """
    monkeypatch.setattr(ha, "_LAST_BILLING_REFUSED_LOG", None)
    with caplog.at_level(logging.ERROR, logger="tortoise.hosted_api"):
        ha._log_billing_notify_refused("org-a", "checkout.session.completed")
        ha._log_billing_notify_refused("org-b", "checkout.session.completed")
    assert len(caplog.records) == 1, (
        f"{len(caplog.records)} ERROR lines inside one window — a "
        "platform-wide saturation would flood the log (#4456)"
    )
    assert "org-a" in caplog.records[0].getMessage()

    # The interval elapsed -> the floor logs again (a suppressed outage must
    # not silence the next one forever).
    monkeypatch.setattr(
        ha, "_LAST_BILLING_REFUSED_LOG",
        time.monotonic() - ha._BILLING_REFUSED_LOG_INTERVAL_S - 1.0)
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="tortoise.hosted_api"):
        ha._log_billing_notify_refused("org-c", "checkout.session.completed")
    assert len(caplog.records) == 1, "the interval elapsed -> logs again"


# ── the shape pin: the direct call must sit INSIDE the offload boundary ────


def test_webhook_notify_direct_call_is_inside_the_offload_boundary():
    """#4456 (shape): the ONLY direct ``notify_billing_event`` call in
    ``webhooks_stripe`` sits inside the ``_cp_offload`` callable, carrying
    ``op="billing_notify"``, ``best_effort=True`` and
    ``cancel_on_timeout=False``.

    An inline call plus a dummy ``_cp_offload(lambda: None, ...)`` would leave
    the blocking HTTP on the loop, so the call is bound to the callable
    argument rather than merely co-existing with an offload. The
    ``cancel_on_timeout=False`` pin is the P1 shape guard: with the default
    ``True`` a bound miss on a QUEUED submission cancels it and the worker
    skips the callable, dropping the notification (#4456).
    """
    tree = ast.parse(HOSTED_API.read_text())
    handler = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "webhooks_stripe")

    direct = [n for n in ast.walk(handler)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id == "notify_billing_event"]
    assert len(direct) == 1, (
        "webhooks_stripe must make exactly ONE direct notify_billing_event "
        f"call: {[n.lineno for n in direct]} (#4456)"
    )
    site = direct[0]

    offloads = [n for n in ast.walk(handler)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name)
                and n.func.id == "_cp_offload"]
    assert len(offloads) == 1, (
        "webhooks_stripe must offload exactly one call — the blocking notify is "
        f"otherwise back on the event loop (#4456): {len(offloads)} found"
    )
    offload = offloads[0]
    assert offload.args and isinstance(offload.args[0], ast.Lambda), (
        "the notify _cp_offload argument is not an offloaded callable (#4456)"
    )
    assert any(node is site for node in ast.walk(offload.args[0])), (
        "the direct notify_billing_event call is NOT inside the _cp_offload "
        "callable — an inline notify plus a dummy offload would leave the "
        "blocking HTTP on the event loop (#4456)"
    )
    kwargs = {kw.arg: kw.value for kw in offload.keywords}
    assert (isinstance(kwargs.get("op"), ast.Constant)
            and kwargs["op"].value == "billing_notify"), (
        "the notify offload lost its op label (#4456)"
    )
    assert (isinstance(kwargs.get("best_effort"), ast.Constant)
            and kwargs["best_effort"].value is True), (
        "the notify offload lost best_effort=True — a saturated telemetry pool "
        "could then 503 a claimed webhook (#4456)"
    )
    assert (isinstance(kwargs.get("cancel_on_timeout"), ast.Constant)
            and kwargs["cancel_on_timeout"].value is False), (
        "the notify offload lost cancel_on_timeout=False — a bound miss on a "
        "QUEUED notify would then CANCEL it and the worker would SKIP the "
        "callable, silently dropping a claimed event's notification (#4456)"
    )
