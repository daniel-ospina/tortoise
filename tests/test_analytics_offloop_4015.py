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
``_track_analytics_event``); the ``best_effort`` op-set invariant lives in
``tests/test_control_plane_offload_3498.py``
(``test_never_raise_offload_sites_pass_best_effort``). This file adds the shape
pin that no direct call site of that helper sits ON the event loop — every
one lives in a declared lane, each bound to its dispatch mechanism.
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


class _Rows:
    """The bare ``.result_set`` shape the registry queries are read through."""

    def __init__(self, rows):
        self.result_set = rows


class _FakeRegistry:
    """The selfhost lane's registry twin, keyed on the CYPHER the handler
    actually issues — so it records the real call order instead of assuming
    it. The tier read fails on demand."""

    def __init__(self, order: list[str], tier_raises: bool):
        self.order = order
        self.tier_raises = tier_raises

    def query(self, cypher, params=None):
        c = " ".join(cypher.split())
        if "t.tier" in c:
            self.order.append("tier")
            if self.tier_raises:
                raise _Boom("registry tier read failed")
            return _Rows([("pro",)])
        if c.startswith("MATCH (w:WebhookEvent"):
            self.order.append("claim")
            return _Rows([])  # never seen → first processing
        if c.startswith("CREATE (w:WebhookEvent"):
            self.order.append("claim-create")
            return _Rows([])
        raise AssertionError(f"unexpected registry cypher: {cypher!r}")


class _FakeRegistrySDK:
    def __init__(self, registry: _FakeRegistry):
        self._registry = registry

    def _get_registry(self):
        return self._registry


def _wire_stripe_webhook(monkeypatch, order: list[str], *,
                         analytics_raises=False, audit_raises=False,
                         tier_raises=False, registry_mode=False):
    """Drive ``webhooks_stripe`` to its first-processing block, with every
    seam stubbed and recorded into ``order``.

    Supabase mode is the default; ``registry_mode=True`` swaps the seam for a
    fake registry SDK (``ha._make_sdk``) so the SELFHOST branch is exercised
    instead — the lane whose ordering the Supabase tests cannot pin (#4459
    review P3)."""
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
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: not registry_mode)

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

    if registry_mode:
        # The registry (selfhost) branch reads the tier from the Teams node
        # and claims via the WebhookEvent node on the SAME registry handle,
        # so one fake covers the ordering question the test asks.
        registry = _FakeRegistry(order, tier_raises)
        monkeypatch.setattr(
            ha, "_make_sdk",
            lambda *, namespace=None, graph_name=None: _FakeRegistrySDK(registry))
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


def test_stripe_webhook_registry_tier_read_failure_leaves_the_event_unclaimed(
        monkeypatch):
    """The SELFHOST (registry) twin of the test above — the same reorder, in
    the OTHER branch (``is_supabase_enabled()`` false), which the Supabase
    seam wiring cannot reach (#4459 review P3).

    Both branches take the tier read BEFORE the claim; a passing Supabase
    test says nothing about the registry one, so moving the registry tier
    query back BELOW the ``seen_rows`` claim left every test green while
    reintroducing the defect: the ``WebhookEvent`` node is created, the tier
    read then raises, the route 500s, and Stripe's retry sees the event as
    already-seen — the notification is dropped permanently.

    RED (mutation): reorder the registry branch's tier query below the claim
    → ``claim``/``claim-create`` appear in ``order`` before the failure.
    """
    order: list[str] = []
    _wire_stripe_webhook(monkeypatch, order, tier_raises=True,
                         registry_mode=True)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 500, (
        f"the registry-lane tier-read failure must surface as an honest, "
        f"retryable 500: {resp.status_code} {getattr(resp, 'body', b'')!r}"
    )
    assert "tier" in order, (
        "the registry-lane tier read was never attempted — the branch under "
        "test is not the one being exercised"
    )
    assert "claim" not in order and "claim-create" not in order, (
        f"the tier read failed but the event was already CLAIMED in the "
        f"registry lane: {order} — Stripe's retry would see it as seen and "
        f"the billing notification is lost forever (#4015)"
    )
    assert "notify" not in order, (
        f"no notification may fire off a failed tier read: {order}"
    )


def test_stripe_webhook_analytics_offload_failure_does_not_500(monkeypatch):
    """An OFFLOAD failure (missed bound / saturated telemetry pool) is
    swallowed by the seam itself (``best_effort=True``), so the webhook stays
    200 and the notification still fires. This pins the SEAM contract; the
    handler's own guard is falsified by the raising-emit test above.

    Deterministic: the worker signals an Event BEFORE it blocks, so the test
    never races thread-pool start-up against the 0.05 s bound (#4015 review).
    """
    order: list[str] = []
    started = threading.Event()
    _wire_stripe_webhook(monkeypatch, order)

    def _slow(org_id, event_name, properties=None):
        order.append("analytics")
        started.set()
        time.sleep(0.4)

    monkeypatch.setattr(ha, "_track_analytics_event", _slow)
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    resp = asyncio.run(ha.webhooks_stripe(_stripe_request({})))

    assert resp.status_code == 200
    # The offloaded worker genuinely STARTED (so this is the bound path, not a
    # refused submission) — asserted BEFORE the ordering check, because the
    # worker appends its marker on entry: waiting here is what makes the order
    # read deterministic under a loaded pool.
    assert started.wait(5.0), "the telemetry worker never started"
    assert order.index("notify") < order.index("analytics"), (
        f"the notification must fire before the telemetry attempt: {order}"
    )


# ── the shape pin: no direct call site may sit ON the event loop ───────────


def test_track_analytics_event_is_only_called_from_the_off_loop_entry_point():
    """#4015 AC1 (shape): every **direct-call** site of the blocking helper in
    ``hosted_api`` sits in a DECLARED off-loop lane, each bound to the dispatch
    mechanism that takes it off the loop — so the site is off-loop by
    construction rather than by argument. A direct call in any other function
    (the regression this issue exists for) fails here.

    Two lanes are declared (``declared_lanes`` below):

    * ``_emit_analytics_off_loop`` — the #4015 entry point, whose direct call
      must live INSIDE the callable the ``_cp_offload`` seam is handed, with
      ``best_effort=True``;
    * ``_write`` (nested in ``_emit_wait_bound_breach``) — main's #4412
      transport-wait-bound breach emit. It is SYNC and fire-and-forget, so it
      cannot await the async entry point; it dispatches to the SAME telemetry
      control-plane pool directly
      (``control_plane_worker("telemetry").submit(_write)``), which puts it
      off the request path by another route. The pin VERIFIES that dispatch
      rather than trusting the lane's name.

    The two halves are BOUND (#4015 review): asserting only that the helper
    contains SOME ``_cp_offload(..., best_effort=True)`` call left a mutation
    green — moving the direct call out of the lambda while keeping a dummy
    ``_cp_offload(lambda: None, ...)`` put the blocking POST back on the loop.

    The lane CHECK itself is not a bare name-based escape hatch: declaring
    ``_write`` only admits a direct call there, and the lane's own dispatch to
    the telemetry pool is asserted separately below. main's #4412 added that
    second OFF-loop lane; this pin forbids an ON-loop direct call, which is the
    defect #4015 names — it does not forbid a second off-loop route.

    Out of this pin's scope by design: partial-application lanes — the
    capture-cost ``asyncio.to_thread(_track_analytics_event, …)`` and
    ``mcp_server``'s retained emitter — which carry their own off-loop
    guarantees and are pinned by their own tests.

    The ``best_effort=True`` half is co-owned by
    ``tests/test_control_plane_offload_3498.py::test_never_raise_offload_sites_pass_best_effort``
    (which also pins the op-set invariant); this one additionally binds the
    flag to the same call site.
    """
    tree = ast.parse(HOSTED_API.read_text())
    parents = {child: parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}

    def _nearest_func(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur
            cur = parents.get(cur)
        return None

    # ONE walk of the module: a call nested in an inner def is attributed to
    # that inner def exactly once (the previous double walk counted it twice).
    direct = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Name)
              and n.func.id == "_track_analytics_event"]
    assert direct, "_track_analytics_event is no longer called at all?"

    # Declared off-loop lanes (see the docstring). A direct call whose
    # ENCLOSING function is not one of these is an undeclared emit — the
    # on-loop regression #4015 exists for.
    declared_lanes = {"_emit_analytics_off_loop", "_write"}
    sites_by_lane: dict = {}
    for _node in direct:
        sites_by_lane.setdefault(
            getattr(_nearest_func(_node), "name", None), []).append(_node)
    undeclared = sorted(set(sites_by_lane) - declared_lanes)
    assert not undeclared, (
        "analytics emit(s) outside every declared off-loop lane — "
        "_track_analytics_event is called from: "
        f"{[(name, [n.lineno for n in nodes]) for name, nodes in sites_by_lane.items()]}"
        f"; undeclared lane(s): {undeclared} (#4015)"
    )
    assert "_emit_analytics_off_loop" in sites_by_lane, (
        "the #4015 off-loop entry point no longer emits at all (#4015)"
    )
    assert len(sites_by_lane["_emit_analytics_off_loop"]) == 1, (
        "_emit_analytics_off_loop must contain exactly ONE direct "
        "_track_analytics_event call: "
        f"{[n.lineno for n in sites_by_lane['_emit_analytics_off_loop']]}"
    )
    site = sites_by_lane["_emit_analytics_off_loop"][0]
    enclosing = _nearest_func(site)
    assert enclosing is not None and enclosing.name == "_emit_analytics_off_loop", (
        f"the direct call site is in {getattr(enclosing, 'name', 'module')!r} "
        f"(line {site.lineno}) — it must be the off-loop entry point (#4015)"
    )

    # ... and it must be INSIDE the callable the seam is handed.
    offloads = [
        n for n in ast.walk(enclosing)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "_cp_offload"
    ]
    assert len(offloads) == 1, (
        "_emit_analytics_off_loop must offload EXACTLY one call — the blocking "
        f"POST is otherwise back on the event loop (#4015): {len(offloads)} found"
    )
    offload = offloads[0]
    assert offload.args and isinstance(offload.args[0], (ast.Lambda, ast.Name)), (
        "the _cp_offload argument is not an offloaded callable (#4015)"
    )
    assert any(node is site for node in ast.walk(offload.args[0])), (
        "the direct _track_analytics_event call is NOT inside the _cp_offload "
        "callable — an inline emit plus a dummy _cp_offload would leave the "
        "blocking POST on the event loop (#4015)"
    )
    kwargs = {kw.arg: kw.value for kw in offload.keywords}
    assert (isinstance(kwargs.get("best_effort"), ast.Constant)
            and kwargs["best_effort"].value is True), (
        "the analytics emit lost best_effort=True — telemetry would be able "
        "to fail a request (#3498 review P1)"
    )

    # The second declared lane's off-loop guarantee (main's #4412): its direct
    # call must be SUBMITTED to the telemetry control-plane worker pool. Without
    # this, `declared_lanes` would be a bare name-based escape hatch.
    if "_write" in sites_by_lane:
        assert len(sites_by_lane["_write"]) == 1, (
            "the `_write` telemetry lane must hold exactly one direct call: "
            f"{[n.lineno for n in sites_by_lane['_write']]}"
        )
        write_fn = _nearest_func(sites_by_lane["_write"][0])
        lane_fn = parents.get(write_fn)
        while lane_fn is not None and not isinstance(
                lane_fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lane_fn = parents.get(lane_fn)
        assert lane_fn is not None, (
            "the `_write` telemetry lane is not nested in a lane function "
            "(#4015)"
        )
        submit_calls = [
            c for c in ast.walk(lane_fn)
            if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute) and c.func.attr == "submit"
        ]
        assert any(
            isinstance(c.func.value, ast.Call)
            and isinstance(c.func.value.func, ast.Name)
            and c.func.value.func.id == "control_plane_worker"
            and c.func.value.args
            and isinstance(c.func.value.args[0], ast.Constant)
            and c.func.value.args[0].value == "telemetry"
            for c in submit_calls
        ), (
            f"the `_write` analytics lane in {lane_fn.name!r} is not dispatched "
            "to the telemetry control-plane pool — the blocking POST could be "
            "back on the event loop (#4015)"
        )
