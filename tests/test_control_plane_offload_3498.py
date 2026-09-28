"""#3498 — the synchronous control-plane/auth HTTP must not run on the loop.

``tortoise/supabase_control.py`` is synchronous end to end (0 ``async def``
across ~100 functions, an ``httpx.Client`` transport), so every async handler
that needs auth bridges to a blocking API. Before this change the bridge was
ad hoc — some sites wrapped a call in ``asyncio.to_thread``, most did not — and
the auth/DI seams ran their PostgREST round-trips directly in the coroutine.
One slow dependency call therefore held the event loop and delayed every other
request in the process, including ``/health`` (the #2850/#3060 de-registration
signature).

These tests are the design review's FALSIFIER (§F): they assert the seam calls
run OFF ``MainThread``, that the pool is genuinely multi-worker (the rejected
single-slot option would serialise auth to ~one request), and that a missed
bound / saturated backlog is fail-closed. Delete the offload and the site test
fails.

The structural pin lives in ``tests/test_health_ready_nonblocking.py``
(``test_control_plane_seam_calls_are_all_offloaded``).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

import tortoise.hosted_api as ha
import tortoise.monitoring as monitoring
import tortoise.supabase_control as sc

# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_offload_state():
    """Bounded record buffers are process-wide — isolate every test."""
    monitoring.reset_control_plane_records()
    yield
    monitoring.reset_control_plane_records()


class _StubTransport:
    """Records the THREAD NAME of every control-plane HTTP call.

    ``_record_client_call`` in ``supabase_control`` records the calling thread
    at the real HTTP choke point; this stub stands in for ``httpx.Client`` so a
    test can prove a *site* left the loop without a live PostgREST.
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.threads: list[str] = []

    def _reply(self):
        self.threads.append(threading.current_thread().name)

        class _Resp:
            status_code = 200
            content = b"[]"

            def __init__(self, rows):
                self._rows = rows

            def json(self):
                return self._rows

        return _Resp(self.rows)

    def get(self, url, **kwargs):
        return self._reply()

    def post(self, url, **kwargs):
        return self._reply()

    def patch(self, url, **kwargs):
        return self._reply()

    def delete(self, url, **kwargs):
        return self._reply()


def _stub_control_plane(monkeypatch, rows):
    cp = sc.SupabaseControlPlane(url="https://stub.supabase.co",
                                 service_key="svc-3498")
    transport = _StubTransport(rows)
    cp._http = transport
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)
    return cp, transport


# ── the seam ────────────────────────────────────────────────────────────────


def test_control_plane_call_runs_off_main_thread():
    """The unit of the falsifier: an offloaded call is not on ``MainThread``."""
    thread_name = asyncio.run(
        monitoring.run_control_plane_call(
            lambda: threading.current_thread().name, op="probe")
    )
    assert thread_name != "MainThread"
    assert thread_name.startswith(monitoring.CONTROL_PLANE_WORKER_NAME)


def test_control_plane_pool_is_multi_worker():
    """A single slot would collapse auth concurrency to ~one request (the
    design review's rejected option); the declared pool is multi-worker."""
    assert monitoring.CONTROL_PLANE_WORKERS >= 2
    assert monitoring.control_plane_worker().workers == monitoring.CONTROL_PLANE_WORKERS


def test_control_plane_pool_runs_calls_concurrently():
    """Four calls must be in flight at once — impossible on one worker slot."""
    barrier = threading.Barrier(4, timeout=5)

    def _wait_at_barrier():
        barrier.wait()
        return threading.current_thread().name

    async def _run():
        return await asyncio.gather(*[
            monitoring.run_control_plane_call(_wait_at_barrier, op="concurrent")
            for _ in range(4)
        ])

    names = asyncio.run(_run())
    assert len(set(names)) >= 2, f"pool serialised four calls onto {names}"


def test_offload_records_op_and_duration():
    async def _run():
        return await monitoring.run_control_plane_call(lambda: "ok", op="unit")

    assert asyncio.run(_run()) == "ok"
    records = monitoring.control_plane_offload_records()
    assert records and records[-1][0] == "unit"
    assert records[-1][1] >= 0.0


def test_run_on_daemon_worker_honours_an_explicit_bound():
    """#3498: ``run_on_daemon_worker`` gains an OPTIONAL wait bound; ``None``
    (the default) keeps the historical unbounded probe/sweep await."""
    async def _run():
        await monitoring.run_on_daemon_worker(
            lambda: time.sleep(0.4), name="test-bounded-sweep", timeout=0.05)

    with pytest.raises(TimeoutError):
        asyncio.run(_run())


# ── fail-closed error mapping ───────────────────────────────────────────────


def test_missed_wait_bound_raises_control_plane_offload_error(monkeypatch):
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        await monitoring.run_control_plane_call(
            lambda: time.sleep(0.4), op="slow")

    with pytest.raises(monitoring.ControlPlaneOffloadError):
        asyncio.run(_run())


def test_missed_wait_bound_maps_to_the_standard_503(monkeypatch):
    """The hosted seam converts the offload failure into the repo-standard
    ``control_plane_unavailable`` 503 — never a hang, never a raw 500."""
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        await ha._cp_offload(lambda: time.sleep(0.4), op="slow")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error_code"] == "control_plane_unavailable"


def test_best_effort_offload_swallows_a_missed_bound(monkeypatch):
    """#3498 review P1: telemetry (``update_last_used``, the analytics emit)
    must never gate the request path — an offload failure on a best-effort
    call is swallowed, not turned into a 503."""
    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        return await ha._cp_offload(
            lambda: time.sleep(0.4), op="telemetry", best_effort=True)

    assert asyncio.run(_run()) is None


def test_best_effort_leaves_helper_errors_visible():
    """Only the OFFLOAD failure is swallowed — a domain exception from the
    helper (e.g. strict-mode ``UnregisteredTelemetryKey``) still propagates."""
    class _Boom(RuntimeError):
        pass

    def _raise_boom():
        raise _Boom()

    async def _run():
        await ha._cp_offload(_raise_boom, op="telemetry", best_effort=True)

    with pytest.raises(_Boom):
        asyncio.run(_run())


def test_best_effort_uses_a_separate_pool_from_auth():
    """#3498 review P1: best-effort work must never occupy an auth slot — a
    telemetry burst parking every auth worker is the same outage class."""
    auth = monitoring.control_plane_worker("auth")
    telemetry = monitoring.control_plane_worker("telemetry")
    assert auth is not telemetry
    assert auth.workers == monitoring.CONTROL_PLANE_WORKERS
    assert telemetry.workers == monitoring.CONTROL_PLANE_TELEMETRY_WORKERS


def test_oauth_pool_is_separate_from_auth_and_telemetry():
    """#3669: a CIMD fetch is attacker-reachable, so its pool must be its OWN —
    sharing ``auth`` would let a fetch flood park every auth slot (the #3498
    review P1 argument applied to a new attacker class)."""
    auth = monitoring.control_plane_worker("auth")
    oauth = monitoring.control_plane_worker("oauth")
    telemetry = monitoring.control_plane_worker("telemetry")
    assert oauth is not auth and oauth is not telemetry
    assert oauth.workers == monitoring.CONTROL_PLANE_OAUTH_WORKERS


def test_oauth_offload_routes_to_the_oauth_pool(monkeypatch):
    """WIRING guard: reverting ``_oauth_offload`` to the auth pool would keep
    every behavioural test green, so record what it actually passes."""
    seen: list[tuple[str, object]] = []

    async def _recorder(fn, *, op, pool="auth", timeout=None,
                        cancel_on_timeout=True):
        seen.append((pool, timeout))
        return "ok"

    monkeypatch.setattr(ha, "run_control_plane_call", _recorder)

    async def _run():
        await ha._oauth_offload(lambda: None, op="read-only")
        await ha._oauth_offload(lambda: None, op="grant", no_wait_bound=True)

    asyncio.run(_run())
    assert [pool for pool, _t in seen] == ["oauth", "oauth"], (
        f"_oauth_offload used pools {seen} — the OAuth lane must never share "
        "the auth pool (#3669)"
    )
    # The reading lane is bounded by the seam default; the MUTATING grant lane
    # must be awaited WITHOUT a wait bound (inf), or the bound would abandon a
    # mid-write grant and answer a retryable state it cannot observe (#2863).
    assert seen[0][1] is None
    assert seen[1][1] == float("inf")


def test_fetch_deadline_sits_below_the_offload_bound():
    """Constant ordering: a fetch must return before its caller's offload bound
    would abandon it. ``_DeadlineStream`` bounds every SOCKET phase (connect,
    TLS, status/header reads, body); the OS resolver's ``getaddrinfo`` tail is
    the one exception (see ``cimd.FETCH_MAX_S``), and is bounded in aggregate by
    the in-flight cap and the budget rather than by this ordering."""
    from tortoise import cimd
    assert cimd.FETCH_MAX_S <= monitoring.CONTROL_PLANE_OFFLOAD_TIMEOUT_S


def test_oauth_pool_is_larger_than_the_in_flight_cap():
    """The CIMD in-flight cap must be the binding constraint on concurrent
    fetches (defence in depth), with the remaining oauth workers still free for
    the token grants and registry reads."""
    from tortoise import cimd
    assert monitoring.CONTROL_PLANE_OAUTH_WORKERS > cimd.MAX_IN_FLIGHT_FETCHES


def test_oauth_offload_maps_failure_to_the_oauth_503(monkeypatch):
    """The OAuth lane's fail-closed error is the RFC 6749 §5.2
    ``temporarily_unavailable`` shape its consumers parse (#2863) — NOT the
    FastAPI ``control_plane_unavailable`` body the auth/REST lane uses."""
    from tortoise.oauth import OAuthTemporarilyUnavailable

    monkeypatch.setattr(monitoring, "CONTROL_PLANE_OFFLOAD_TIMEOUT_S", 0.05)

    async def _run():
        await ha._oauth_offload(lambda: time.sleep(0.4), op="slow")

    with pytest.raises(OAuthTemporarilyUnavailable) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status == 503
    assert excinfo.value.error == "temporarily_unavailable"


def test_graph_pool_is_separate_from_all_other_pools():
    """#3773: a graph (data-plane) burst must not park an auth slot — the
    #3498 review P1 isolation argument applied to the write handlers'
    per-request graph helpers (``_data_sdk`` / ``_check_org_limit``). Asserted
    against all three sibling pools (the claim names auth, telemetry AND
    oauth)."""
    auth = monitoring.control_plane_worker("auth")
    graph = monitoring.control_plane_worker("graph")
    telemetry = monitoring.control_plane_worker("telemetry")
    oauth = monitoring.control_plane_worker("oauth")
    assert len({auth, graph, telemetry, oauth}) == 4, (
        "a graph submission shared a pool with a control-plane lane")
    assert graph.workers == monitoring.CONTROL_PLANE_GRAPH_WORKERS


def test_graph_bound_sits_above_the_projection_cold_start_allowance(monkeypatch):
    """#3773: the graph lane's wait bound must sit ABOVE the probe lane's own
    projection cold-start allowance. ``_make_sdk`` / ``_get_proj()`` can open a
    COLD projection (~28 sequential round trips), which the repo budgets via
    ``probe_setup_timeout()`` precisely so a round-trip bound does not
    false-degrade it (#3143). The graph bound is derived from the RESOLVED
    allowance at call time, so raising ``TORTOISE_PROBE_SETUP_TIMEOUT`` cannot
    invert the ordering (the CIMD sibling is
    ``test_fetch_deadline_sits_below_the_offload_bound``)."""
    assert (monitoring.graph_offload_timeout_s()
            > monitoring.probe_setup_timeout()), (
        "the graph offload bound fell to/below the projection cold-start "
        "allowance — a cold first write would be abandoned and 503'd (#3143)")
    monkeypatch.setenv("TORTOISE_PROBE_SETUP_TIMEOUT", "120")
    assert (monitoring.graph_offload_timeout_s()
            > monitoring.probe_setup_timeout())


def test_graph_offload_routes_to_the_graph_pool(monkeypatch):
    """WIRING guard (mutation-verified gap, #3773 re-review): reverting
    ``_graph_offload``'s ``pool="graph"`` to ``"auth"`` kept every graph test
    green — silently re-parking the auth slots the pool exists to protect.
    Record what ``_graph_offload`` actually passes (mirrors the
    ``_oauth_offload`` and best-effort wiring guards)."""
    seen: list[str] = []

    async def _recorder(fn, *, op, pool="auth", timeout=None,
                        cancel_on_timeout=True):
        seen.append(pool)
        return "ok"

    monkeypatch.setattr(ha, "run_control_plane_call", _recorder)

    async def _run():
        await ha._graph_offload(lambda: None, op="write_preamble")

    asyncio.run(_run())
    assert seen == ["graph"], (
        f"_graph_offload used pools {seen} — the data-plane offload must use "
        "the dedicated `graph` pool, never the auth pool (#3773)")


def test_graph_offload_maps_failure_to_the_graph_503(monkeypatch):
    """#3773: a saturated / bound-missed data-plane offload is a 503 the
    client can retry — NOT the sign-in-specific ``control_plane_unavailable``
    body the auth/REST lane uses, which would mislead a client retrying a
    graph write. The graph lane's OWN bound is what applies (not the seam's
    PostgREST default), so patch the resolver."""
    monkeypatch.setattr(ha, "graph_offload_timeout_s", lambda: 0.05)

    async def _run():
        await ha._graph_offload(lambda: time.sleep(0.4), op="graph-slow")

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(_run())
    assert excinfo.value.status_code == 503
    assert excinfo.value.detail["error_code"] == "graph_unavailable"


def test_graph_offload_isolates_the_caller_context():
    """#3773: ``_graph_offload`` runs the callable under a CONTEXT COPY.

    ``_data_sdk`` sets the #2600 actor ContextVar, and a pool thread is
    process-lifetime — a var set at its top level would survive into the NEXT
    request that worker served. The copy must carry the caller's value IN and
    must not let a worker-side write reach the caller's context."""
    import contextvars

    var = contextvars.ContextVar("graph-offload-isolation", default=None)

    async def _run_read():
        var.set("caller")
        return await ha._graph_offload(lambda: var.get(), op="ctx-read")

    assert asyncio.run(_run_read()) == "caller"

    async def _run_write():
        var.set(None)
        await ha._graph_offload(lambda: var.set("worker"), op="ctx-write")
        return var.get()

    assert asyncio.run(_run_write()) is None


def test_unknown_pool_fails_closed():
    """The pool selector is the only thing keeping best-effort work off auth
    capacity — a typo must raise, not silently fall back to the auth pool."""
    with pytest.raises(ValueError, match="unknown control-plane pool"):
        monitoring.control_plane_worker("best_effort")


def test_cp_offload_routes_best_effort_to_the_telemetry_pool(monkeypatch):
    """WIRING guard (mutation-verified gap, #3498 re-review P1): the factory
    test above does not cross ``_cp_offload``, so reverting
    ``pool="telemetry" if best_effort else "auth"`` kept every test green.
    Record what ``_cp_offload`` actually passes."""
    seen: list[str] = []

    async def _recorder(fn, *, op, pool="auth", timeout=None,
                        cancel_on_timeout=True):
        seen.append(pool)
        return "ok"

    monkeypatch.setattr(ha, "run_control_plane_call", _recorder)

    async def _run():
        await ha._cp_offload(lambda: None, op="critical")
        await ha._cp_offload(lambda: None, op="telemetry", best_effort=True)

    asyncio.run(_run())
    assert seen == ["auth", "telemetry"], (
        f"_cp_offload pool routing is wrong: {seen} — best-effort must use the "
        "telemetry pool (the P1 regression this guard exists for)"
    )


def test_best_effort_refusal_is_distinguishable_from_a_bound_miss(monkeypatch):
    """#4456: a REFUSED best-effort offload must not look like a bound miss.

    ``best_effort`` historically swallowed BOTH a saturating refusal (the
    pool never accepted the callable — it will NOT run) and a bound miss (the
    worker that accepted it still runs it) as ``None``, so a delivery-sensitive
    caller could not tell a real drop from a late completion. The seam returns
    the public ``OFFLOAD_REFUSED`` sentinel for a refusal only.
    """

    async def _fake(fn, *, op, pool="auth", timeout=None,
                    cancel_on_timeout=True):
        if op == "refused":
            raise monitoring.ControlPlaneOffloadError(
                "pool backlog full", refused=True)
        raise monitoring.ControlPlaneOffloadError("exceeded its bound")

    monkeypatch.setattr(ha, "run_control_plane_call", _fake)

    async def _run():
        refused = await ha._cp_offload(
            lambda: None, op="refused", best_effort=True)
        missed = await ha._cp_offload(
            lambda: None, op="missed", best_effort=True)
        return refused, missed

    refused, missed = asyncio.run(_run())
    assert refused is ha.OFFLOAD_REFUSED, (
        f"a refused best-effort offload returned {refused!r} — a real drop "
        "must be distinguishable from a bound miss (#4456)"
    )
    assert missed is None, (
        f"a bound miss returned {missed!r} — the worker still runs it, so "
        "only a genuine refusal carries the sentinel (#4456)"
    )


#: Ops whose helper is documented BEST-EFFORT / never-raise: an offload failure
#: must be swallowed, never a 503. Keep in sync with the `best_effort=True`
#: sites; the structural test below fails if one loses the flag.
_NEVER_RAISE_OPS = frozenset({
    "update_last_used", "analytics_event", "github_repos_count",
    # #4456: ``notify_billing_event`` is documented never-raise
    # (tortoise/notify.py). Routing it through the seam gives it a NEW failure
    # mode (a missed bound / a saturated telemetry backlog); ``best_effort``
    # keeps that from mapping onto the webhook's 500, which would strand a
    # claimed Stripe event whose payment was already taken.
    "billing_notify",
})


def test_never_raise_offload_sites_pass_best_effort():
    """Structural guard for the review's "telemetry must not gate auth" fix:
    every `_cp_offload` wrapping a never-raise helper carries
    ``best_effort=True``, AND the set of best-effort ops is exactly the declared
    never-raise set — so a rename/deletion cannot hollow the guard out.
    """
    import ast

    tree = ast.parse(Path(ha.__file__).read_text())
    offenders = []
    best_effort_ops: set[str] = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name != "_cp_offload":
            continue
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        op = kwargs.get("op")
        if not isinstance(op, ast.Constant):
            continue
        best_effort = kwargs.get("best_effort")
        marked = isinstance(best_effort, ast.Constant) and best_effort.value is True
        if marked:
            best_effort_ops.add(op.value)
        if op.value in _NEVER_RAISE_OPS and not marked:
            offenders.append((call.lineno, op.value))
    assert not offenders, (
        "never-raise offload site(s) missing best_effort=True: "
        f"{offenders} — telemetry must not be able to fail auth"
    )
    assert best_effort_ops == _NEVER_RAISE_OPS, (
        "the best-effort op set drifted from the declared never-raise set: "
        f"sites use {sorted(best_effort_ops)}, declared {sorted(_NEVER_RAISE_OPS)}"
    )


def test_saturated_backlog_fails_closed(monkeypatch):
    """A wedged pool fails fast instead of buffering without bound.

    ``max_backlog=1``: the worker takes the first submission, one more fills
    the queue, and the third must fail fast rather than queue without bound.
    The first submission signals an Event on entry so the wait is deterministic
    (no sleep-race, #3498 review).
    """
    tiny = monitoring._SingleSlotWorker("test-cp-saturated", workers=1,
                                        max_backlog=1)
    monkeypatch.setattr(monitoring, "control_plane_worker",
                        lambda pool="auth": tiny)
    first_started = threading.Event()
    gate = threading.Event()

    def _hold():
        first_started.set()
        gate.wait()

    async def _run():
        first = asyncio.ensure_future(
            monitoring.run_control_plane_call(_hold, op="hold-1"))
        # Deterministic: wait until the single slot has DEQUEUED `first`,
        # then its own Event wait blocks the slot.
        await asyncio.get_running_loop().run_in_executor(None, first_started.wait)
        second = asyncio.ensure_future(
            monitoring.run_control_plane_call(gate.wait, op="hold-2"))
        await asyncio.sleep(0)  # let `second` submit into the one-slot queue
        # The third submission is REFUSED by the one-slot backlog — the
        # callable never runs, so ``refused`` must be True. Asserting only the
        # exception TYPE let a ``refused=False`` mutation (which would disable
        # the #4456 escalation in production) pass the whole suite.
        with pytest.raises(monitoring.ControlPlaneOffloadError) as ex:
            await monitoring.run_control_plane_call(lambda: None, op="full")
        assert ex.value.refused is True, (
            f"a backlog-full refusal reported refused={ex.value.refused!r} — "
            "the callable never ran, so the #4456 delivery discriminator must "
            "be True"
        )
        gate.set()
        await asyncio.gather(first, second)

    asyncio.run(_run())


def test_bound_miss_on_a_queued_submission_is_refused_by_default(monkeypatch):
    """#4456: the DEFAULT bound semantics are FAIL-CLOSED (no fake, no seam double).

    ``cancel_on_timeout`` defaults to ``True``. A still-QUEUED submission has
    its ``concurrent.futures.Future.cancel()`` SUCCEED when the bound expires;
    the worker later reaches ``set_running_or_notify_cancel()``, gets False,
    and SKIPS the callable — so the call did not and will not run, and
    ``refused`` is True.

    This is the polarity pin: flipping the default to ``False`` (delivery-
    preserving, delivery for the Stripe notify lane only) would leave every
    auth/oauth/graph bound miss silently delivery-preserving — a semantics
    change to the AUTH lane — and the seam doubles elsewhere accept and IGNORE
    the kwarg, so they MASK the flip instead of detecting it. Here the pool is
    the REAL ``_SingleSlotWorker`` and the assertion reads an outcome, not the
    argument.
    """
    tiny = monitoring._SingleSlotWorker("test-cp-polarity", workers=1,
                                        max_backlog=1)
    monkeypatch.setattr(monitoring, "control_plane_worker",
                        lambda pool="auth": tiny)
    blocker_started = threading.Event()
    release = threading.Event()
    ran = threading.Event()

    def _hold():
        blocker_started.set()
        release.wait(10.0)

    def _queued():
        ran.set()

    async def _run():
        first = asyncio.ensure_future(
            monitoring.run_control_plane_call(_hold, op="polarity-hold"))
        await asyncio.get_running_loop().run_in_executor(
            None, blocker_started.wait)
        try:
            # DEFAULT args on purpose: the only difference from the
            # delivery-preserving lane is the parameter default.
            await monitoring.run_control_plane_call(
                _queued, op="polarity-queued", timeout=0.05)
        except monitoring.ControlPlaneOffloadError as exc:
            try:
                assert exc.refused is True, (
                    f"a bound miss on a QUEUED submission reported "
                    f"refused={exc.refused!r} — with the fail-closed default "
                    "the submission is CANCELLED and the worker SKIPS it, so "
                    "it did not run and the discriminator must be True "
                    "(#4456)"
                )
            finally:
                release.set()
        else:  # pragma: no cover - a delivered queued call is the mutation
            release.set()
            raise AssertionError(
                "the default bound DELIVERED a queued submission — the "
                "fail-closed default has been flipped to delivery-preserving "
                "(#4456)"
            )
        await first

    asyncio.run(_run())
    assert not ran.is_set(), (
        "the queued callable RAN under the default (fail-closed) bound — it "
        "must be cancelled and SKIPPED by the worker (#4456)"
    )


def test_an_abandoned_callable_failure_is_retrieved_and_attributed(
        monkeypatch, caplog):
    """#4456: an abandoned failure must not be ONLY an asyncio warning.

    On the delivery-preserving lane ``shield`` does NOT mark the inner future's
    result retrieved: in CPython 3.12 ``_outer_done_callback`` runs on
    outer-cancel and, because the inner is not yet DONE (exactly the bound-miss
    case), REMOVES ``_inner_done_callback`` — whose only job was
    ``inner.exception()``. So a callable that fails AFTER the await was
    abandoned leaves ``Future._log_traceback`` set: the failure surfaces only
    as an unattributed "Future exception was never retrieved" when the future
    is collected. The seam must consume that outcome and report it against the
    op that abandoned it.
    """
    tiny = monitoring._SingleSlotWorker("test-cp-abandoned", workers=1,
                                        max_backlog=4)
    monkeypatch.setattr(monitoring, "control_plane_worker",
                        lambda pool="auth": tiny)
    blocker_started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _hold():
        blocker_started.set()
        release.wait(10.0)

    def _boom():
        try:
            raise RuntimeError("abandoned billing notify boom")
        finally:
            finished.set()

    tiny.submit(_hold)
    assert blocker_started.wait(5.0), "the blocker never occupied the slot"

    # Capture the EXACT asyncio future the seam wraps: the retrieval is only
    # observable on that object (``_log_traceback`` is cleared by
    # ``exception()``).
    wrapped: list = []
    real_wrap_future = asyncio.wrap_future

    def _spy_wrap_future(fut, **kwargs):
        inner = real_wrap_future(fut, **kwargs)
        wrapped.append(inner)
        return inner

    monkeypatch.setattr(monitoring.asyncio, "wrap_future", _spy_wrap_future)

    async def _run():
        with pytest.raises(monitoring.ControlPlaneOffloadError) as ex:
            await monitoring.run_control_plane_call(
                _boom, op="billing_notify", timeout=0.05,
                cancel_on_timeout=False)
        assert ex.value.refused is False
        release.set()
        # Let the abandoned worker run ``_boom`` and let the loop apply the
        # concurrent future's outcome to the wrapped one.
        await asyncio.get_running_loop().run_in_executor(None, finished.wait, 5.0)
        for _ in range(200):
            if wrapped and wrapped[0].done():
                break
            await asyncio.sleep(0.01)

    asyncio.run(_run())

    assert wrapped, "the seam never wrapped a future — this run proves nothing"
    inner = wrapped[0]
    assert inner.done(), (
        "the abandoned callable never completed — this run does not exercise "
        "a post-bound failure (#4456)"
    )
    assert getattr(inner, "_log_traceback", None) is False, (
        "the abandoned future's exception was NEVER retrieved: with "
        "``_log_traceback`` still set, this failure is reported only as an "
        "unattributed asyncio 'Future exception was never retrieved' warning "
        "(#4456)"
    )
    errors = [r.getMessage() for r in caplog.records
              if r.name == "tortoise.monitoring" and r.levelno >= 40]
    assert any("billing_notify" in m and "abandoned" in m
               and "abandoned billing notify boom" in m for m in errors), (
        f"the abandoned failure was not attributed to its op — ERROR lines "
        f"seen: {errors!r} (#4456)"
    )


# ── the rerouted SITE (this fails without the fix) ──────────────────────────


def test_user_memberships_site_reads_off_main_thread(monkeypatch):
    """#3498 §A1 item 6: ``_user_memberships`` is an async session-lane seam.

    Without the offload, ``user_memberships(...)`` runs in the coroutine and
    the transport records ``MainThread`` — the exact bug. With it, every call
    is recorded from a pool thread.
    """
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "role": "owner"}])

    rows = asyncio.run(ha._user_memberships("user-1"))

    assert rows == [{"org_id": "org-1", "role": "owner"}]
    assert transport.threads, "the control-plane transport was never called"
    assert all(name != "MainThread" for name in transport.threads), (
        f"the session membership read ran on the event loop: {transport.threads}"
    )
    recorded = monitoring.control_plane_client_records()
    assert recorded and all(thread != "MainThread" for _dur, thread in recorded)


def test_membership_org_seam_reads_off_main_thread(monkeypatch):
    """The design's §B ``_membership_team`` seam (``_membership_org``) — it
    is reached by every membership-gated session endpoint."""
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "role": "owner"}])

    result = asyncio.run(ha._membership_org("user-1", "org-1"))

    assert result == {"org_id": "org-1", "role": "owner"}
    assert transport.threads and all(n != "MainThread" for n in transport.threads), (
        f"the membership seam ran on the event loop: {transport.threads}"
    )


def test_org_node_seam_reads_off_main_thread(monkeypatch):
    """``_org_node`` is the orgs-row seam reached by ~10 session endpoints
    (and by ``_require_owner_admin``'s suspension-stamp check)."""
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"org_id": "org-1", "tier": "pro"}])

    row = asyncio.run(ha._org_node("org-1"))
    assert row is not None and row["org_id"] == "org-1" and row["tier"] == "pro"
    assert transport.threads and all(n != "MainThread" for n in transport.threads), (
        f"the orgs-row seam ran on the event loop: {transport.threads}"
    )


def test_session_recording_gate_reads_off_main_thread(monkeypatch):
    """#4625 leg 12: the capture's recording gate must not run on the loop.

    ``_session_recording_allowed`` resolves ``_get_onboarding_state`` — a
    blocking PostgREST read in hosted mode (plus a graph override probe) — so
    calling it inline from ``_capture_session_impl`` parked the single event
    loop for the round trip. ``_session_recording_allowed_off_loop`` offloads
    the whole resolution; the stub records the thread of every control-plane
    HTTP call.

    The stored state is ``session_recording: False`` with NO ``graph_id``, so
    the resolution short-circuits to the team layer after the PostgREST read —
    the graph override probe (which this test does not stub) is never reached,
    keeping the assertion about the one read this leg is named for.
    """
    _cp, transport = _stub_control_plane(
        monkeypatch, [{"onboarding_state": {"session_recording": False}}])

    allowed, layer = asyncio.run(
        ha._session_recording_allowed_off_loop({"org_id": "org-4625"}))

    assert (allowed, layer) == (False, "team")
    assert transport.threads, "the control-plane transport was never called"
    assert all(name != "MainThread" for name in transport.threads), (
        f"the recording gate read ran on the event loop: {transport.threads}"
    )


def test_invite_info_supabase_lane_reads_off_main_thread(monkeypatch):
    """#3718: the hosted (Supabase) lane of the public invite-info handler.

    ``GET /v1/invites/info`` is unauthenticated, so its two blocking reads —
    the token lookup (``invitation_info_by_token``) and the org-name
    resolution (``org_by_id``) — are reachable without a session. Both must
    run off the loop. The registry lane is pinned behaviourally in
    ``test_read_routes_loop_responsiveness.py``.

    This case (with ``test_invite_info_submits_one_offload_regardless_of_token``
    below) is the SOLE guard for the hosted lane: the two reads live in the
    nested sync ``def _hosted_invite`` handed to ``_cp_offload`` as a callable
    reference, and ``_unoffloaded_calls`` deliberately skips nested ``def``
    bodies (a nested *sync* def has no inventory entry of its own). Inventory
    membership for ``invitation_info_by_token`` does NOT observe this call site
    — inlining the unit leaves the static pins green (only these behavioural
    cases fail). See #4587 for closing that scan gap.

    The stub answers every PostgREST query with the same row, so it carries
    both the invite fields and the org ``name``.
    """
    _cp, transport = _stub_control_plane(monkeypatch, [{
        "id": "inv-3718", "org_id": "org-1", "role": "member",
        "inviter_email": "owner@example.com", "expires_at": None,
        "status": "pending", "accepted_at": None, "name": "Stub Org",
    }])

    result = asyncio.run(ha.invite_info("tok-3718"))

    assert result["org_name"] == "Stub Org"
    assert result["role"] == "member"
    assert transport.threads, "the control-plane transport was never called"
    assert all(name != "MainThread" for name in transport.threads), (
        f"the hosted invite-info lane ran on the event loop: {transport.threads}"
    )


def test_invite_info_submits_one_offload_regardless_of_token(monkeypatch):
    """#3718: the hosted lane submits ONE offload unit for ANY token.

    The route's 404 copy is deliberately oracle-free, so its offload-FAILURE
    exposure must not depend on whether the token matched. If a matched token
    were the only case that submitted a SECOND offload (the org read), then
    under a saturated pool ``P(503 | valid) > P(503 | unknown)`` — a capacity
    oracle an attacker can drive by loading the shared auth pool. Both hosted
    reads are therefore one unit, and an unknown token never issues the org read.
    """
    def _ops_since(mark: int) -> list[str]:
        return [op for op, _dur in monitoring.control_plane_offload_records()[mark:]]

    _stub_control_plane(monkeypatch, [{
        "id": "inv-3718", "org_id": "org-1", "role": "member",
        "inviter_email": "owner@example.com", "expires_at": None,
        "status": "pending", "accepted_at": None, "name": "Stub Org",
    }])
    mark = len(monitoring.control_plane_offload_records())
    asyncio.run(ha.invite_info("tok-valid"))
    assert _ops_since(mark) == ["invite_info"], (
        "a matched token must submit exactly one offload unit"
    )

    _stub_control_plane(monkeypatch, [])  # unknown token: the lookup finds no row
    mark = len(monitoring.control_plane_offload_records())
    with pytest.raises(HTTPException) as exc:
        asyncio.run(ha.invite_info("tok-unknown"))
    assert exc.value.status_code == 404
    assert _ops_since(mark) == ["invite_info"], (
        "an unknown token must submit the SAME single offload unit — a second "
        "submission on the matched path is a capacity oracle"
    )


# ── #3498 item 1: the loop-lag baseline ─────────────────────────────────────


def test_loop_lag_stats_empty_is_none_never_zero():
    monitoring.reset_loop_lag()
    stats = monitoring.loop_lag_stats()
    assert stats == {"samples": 0, "max_ms": None, "p99_ms": None, "mean_ms": None}


def test_loop_lag_stats_reports_max_and_p99():
    monitoring.reset_loop_lag()
    for lag in (0.001, 0.002, 0.003, 5.0):
        monitoring.record_loop_lag(lag)
    stats = monitoring.loop_lag_stats()
    assert stats["samples"] == 4
    assert stats["max_ms"] == 5000.0
    assert stats["p99_ms"] == 5000.0  # nearest-rank: the top value
    assert stats["mean_ms"] > 0


def test_loop_lag_clamps_negative_drift():
    monitoring.reset_loop_lag()
    monitoring.record_loop_lag(-1.0)
    assert monitoring.loop_lag_stats()["max_ms"] == 0.0


def test_heartbeat_info_exposes_the_loop_lag_baseline():
    monitoring.reset_loop_lag()
    monitoring.record_loop_lag(0.01)
    info = monitoring.loop_heartbeat_info()
    assert info["loop_lag_max_ms"] == pytest.approx(10.0, abs=0.5)
    assert info["loop_lag_samples"] == 1


def test_heartbeat_task_records_lag_each_tick():
    monitoring.reset_loop_lag()
    monitoring._reset_heartbeat()

    async def _run():
        task = asyncio.ensure_future(monitoring.loop_heartbeat_task(interval=0.01))
        await asyncio.sleep(0.06)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(_run())
    assert monitoring.loop_lag_stats()["samples"] >= 1
    monitoring._reset_heartbeat()


# ── premise guard ───────────────────────────────────────────────────────────


def test_control_plane_module_is_still_synchronous_by_construction():
    """The seam exists because ``supabase_control`` is sync end-to-end. If a
    future change made the helpers async, the offload's premise (and this
    file's) would be wrong — fail loudly rather than keep a no-op seam."""
    import ast

    tree = ast.parse(Path(sc.__file__).read_text())
    async_defs = [n.name for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]
    assert async_defs == [], (
        f"supabase_control gained async defs {async_defs} — revisit the #3498 "
        "offload seam (it assumes a synchronous control plane)"
    )
