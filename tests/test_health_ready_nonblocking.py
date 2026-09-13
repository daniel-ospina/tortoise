"""#2988 — /health/ready must never block the event loop.

Production, 2026-09-11: every route in the process (``/openapi.json`` included)
timed out for 15 minutes while ``/proc/loadavg`` was 0.01 and the database
answered PING in 0.38s — an idle process blocked on I/O with the loop held.
Cause: ``health_ready`` ran both plane probes as SYNCHRONOUS calls in the
coroutine body, so one stalled socket froze the whole process, and every deploy
curls this endpoint (a self-inflicted outage vector).

Two complementary guards:

* the AST pins — structural, so the old shape cannot come back through a
  refactor that keeps the observable response intact (a behavioural test alone
  cannot see the difference: the response is identical when nothing stalls);
* the behavioural tests — prove the added indirection actually works, and that
  a hung probe is REPORTED (503) instead of waited out.
"""

from __future__ import annotations

import ast
import asyncio
import re
import time
import tomllib
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
SELFHOST = REPO / "tortoise" / "selfhost.py"


def _handler(name: str, source: Path = HOSTED_API) -> ast.AsyncFunctionDef:
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {source.name}")


def _parents(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}


def _walk_own_body(node: ast.AST):
    """Walk the coroutine's OWN statements, not the bodies of functions it
    dispatches. A probe nested in the handler is exactly where the synchronous
    call is supposed to live — flagging it would make the pin unsatisfiable and
    would push the real call back onto the loop.
    """
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from ast.walk(stmt)


# ── structural pins ────────────────────────────────────────────────────────


def test_handler_makes_no_direct_query_call():
    """``.query(...)`` is synchronous network I/O — it belongs in the module
    level probes, never in the coroutine body. This is the exact line that
    caused the outage (``sdk._get_proj().g.query("RETURN 1")``), and its
    control-plane sibling."""
    node = _handler("health_ready")
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "query"
    ]
    assert not offenders, (
        f"health_ready calls .query(...) directly at line(s) {offenders} — "
        "that runs synchronous network I/O on the event loop and freezes every "
        "other request (#2988)"
    )


def test_both_probes_are_dispatched_through_their_coordinators():
    """#2850 x #2988 — ``health_ready`` must not run either plane probe inline.

    The #2988 guard pinned ``asyncio.to_thread(_probe_db)``. #2850 replaced that
    with dedicated single-flight coordinators on a private daemon worker, which
    is strictly stronger: ``to_thread`` rides the SHARED default executor, so a
    timed-out probe leaks a worker out of the pool every other request depends
    on, and the submission queue is unbounded. The INVARIANT this pins is
    unchanged — the handler must not perform the synchronous network I/O itself
    — so the mechanism pin moves with the mechanism instead of being dropped.
    """
    node = _handler("health_ready")
    dispatched = {
        call.func.value.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "run"
        and isinstance(call.func.value, ast.Name)
    }
    assert {"_READY_PROBE", "_CONTROL_PLANE_PROBE"} <= dispatched, (
        f"probes not dispatched through their coordinators (found {sorted(dispatched)})"
    )
    # No to_thread fallback for the plane probes may creep back in.
    inline = [
        arg.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
        for arg in call.args
        if isinstance(arg, ast.Name)
    ]
    assert "_probe_db" not in inline and "_probe_control_plane" not in inline, (
        f"a plane probe is dispatched via the SHARED default executor ({sorted(inline)}) — "
        "use the dedicated HealthProbe coordinators instead"
    )


def _fly_check_budget_proxy_s() -> float | None:
    """fly.toml's configured ``[[services.http_checks]] timeout``, in seconds.

    NOTE this check targeted ``/health`` — pure in-memory — NOT ``/health/ready``.
    It was borrowed only as a coarse, repo-owned BUDGET PROXY for the readiness
    surface: ``deploy-hosted.yml`` curls ``/health/ready`` with no
    ``--max-time``, so there is no real deadline for it anywhere.

    Returns ``None`` when the repo no longer configures an HTTP check at all.
    """
    import tomllib

    path = REPO / "fly.toml"
    if not path.exists():  # pragma: no cover — repo invariant
        return None
    try:
        cfg = tomllib.loads(path.read_text())
        raw = cfg["services"][0]["http_checks"][0]["timeout"]
    except (KeyError, IndexError, TypeError):
        # #2850 (2026-09-10) removed [[services.http_checks]] deliberately: the
        # /health HTTP check flapped and de-registered the sole machine, costing
        # ~35 min of unreachability while the process was alive on loopback. It
        # was replaced by [[services.tcp_checks]], whose ``timeout`` (5s) is
        # documented in fly.toml as "generous headroom, not a latency budget" —
        # it times a KERNEL accept, not an application response, so borrowing it
        # as a /health/ready budget proxy would be a different quantity entirely
        # (and smaller than the ready worst case, so it cannot serve as a ceiling).
        # There is consequently NO HTTP-check budget to compare against until the
        # deferred top-level [checks.loop_liveness] lands (#2850 follow-up).
        return None
    try:
        return float(str(raw).rstrip("s"))
    except ValueError:  # pragma: no cover — malformed config
        return None


def test_every_plane_probe_is_hard_bounded_and_fail_closed():
    """The bound is what makes the endpoint ANSWER when a plane black-holes.

    The bound now lives on each ``HealthProbe`` rather than in a per-handler
    ``wait_for``, so pin BOTH the single-source-of-truth equality (the signature
    default IS the shared module constant) AND the ordering property (it clears
    the DB probes' statically-known total), plus each plane's fail-closed flag
    and the liveness refresher's alignment. ``_READY_PROBE_TIMEOUT_S`` is gone
    with the mechanism it bounded; a reintroduced per-handler literal would be
    an unreasoned second source of truth.
    """
    import inspect

    import tortoise.hosted_api as mod
    from tortoise.monitoring import (
        PROBE_DB_TOTAL_TIMEOUT,
        PROBE_HARD_TIMEOUT,
        PROBE_SDK_ACQUISITION_BUDGET,
    )

    default = inspect.signature(mod.HealthProbe.__init__).parameters["timeout"].default
    # (1) SINGLE-SOURCE-OF-TRUTH PIN. The signature default must BE the shared
    # module constant, not a hand-typed literal: a safe-LOOKING literal (a
    # reviewer proved ``6.0``) satisfies the ordering property below while
    # silently diverging from the constant the module derives and documents.
    assert default == PROBE_HARD_TIMEOUT, (
        "HealthProbe's default wall bound must be the shared module constant "
        f"PROBE_HARD_TIMEOUT ({PROBE_HARD_TIMEOUT}s), not a hand-typed literal "
        f"(got {default}s)"
    )
    # (2) ORDERING PROPERTY. The default must clear the DB probes'
    # statically-known total (probe_db's two-attempt ceiling PLUS the nominal
    # SDK-acquisition budget). Necessary but NOT sufficient: the embedded
    # acquisition prefix is unbounded, so this is a best-effort alignment, not
    # a proven invariant (see monitoring.PROBE_MAX_SUPERSEDES).
    inner_total = PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET
    assert default > inner_total, (
        f"HealthProbe's default wall bound ({default}s) does not clear the DB "
        f"probes' statically-known total ({inner_total}s) — an omitted timeout "
        "strands a worker thread on every timeout (#2988)"
    )
    assert "_READY_PROBE_TIMEOUT_S" not in HOSTED_API.read_text(), (
        "the superseded per-handler readiness bound is back — the bound belongs "
        "to HealthProbe (one reasoned place)"
    )
    for name in ("_READY_PROBE", "_CONTROL_PLANE_PROBE"):
        probe = getattr(mod, name)
        assert probe._timeout > 0, f"{name} has no usable bound"
        assert probe._fresh_only is True, (
            f"{name} must be fresh_only=True — readiness is a FAIL-CLOSED gate and "
            "must never answer 200 from a verdict older than its read budget (#1384/#2850)"
        )
    # ``_HEALTH_PROBE`` is the NON-fresh refresher that feeds /health
    # (``fresh_only`` is False BY DESIGN), so it cannot join the loop above —
    # assert its ordering SEPARATELY, and pin the identity too. The ordering
    # check alone is satisfied by the class DEFAULT, so it cannot catch an
    # accidental drop of ``timeout=DB_PROBE_HARD_TIMEOUT`` from the
    # construction. Only _READY_PROBE used to be pinned at all.
    assert mod._HEALTH_PROBE._timeout == mod.DB_PROBE_HARD_TIMEOUT, (
        "the /health refresher must pass the explicit DB_PROBE_HARD_TIMEOUT — "
        "the ordering assertion below is satisfied by the class default too, "
        "so it cannot catch a dropped timeout= on its own"
    )
    assert mod._HEALTH_PROBE._timeout > inner_total, (
        f"/health's refresher bound ({mod._HEALTH_PROBE._timeout}s) must clear "
        f"_probe_db's statically-known total ({inner_total}s = probe_db's "
        f"{PROBE_DB_TOTAL_TIMEOUT}s + the {PROBE_SDK_ACQUISITION_BUDGET}s "
        "nominal SDK-acquisition budget), or it abandons a live worker on every "
        "timeout (#2988). This is alignment, not proof: the embedded "
        "acquisition prefix is unbounded."
    )


def test_each_plane_bound_sits_above_its_own_client_timeout(monkeypatch):
    """The #2988 layered-timeout ALIGNMENT, expressed PER PLANE.

    Abandoning a probe does not stop its thread — ``wait_for`` cancels the
    awaitable, not the worker (CPython #87185), so the worker stays parked in
    its socket read. The defence is ordering: keep the outer bound ABOVE the
    probe's statically-known inner bound, so the inner bound normally fires
    first and the thread returns by itself. This is a best-effort ALIGNMENT
    that reduces stranding, NOT a proven invariant — the inner worst case is
    unbounded (httpx's ``read`` is per-read; the embedded acquisition prefix
    runs real queries bounded by the redis read timeout).

    #2850 initially INVERTED this (2s outer vs a 5s inner on the control plane)
    and leaned on ``PROBE_MAX_SUPERSEDES`` instead. That rationale was
    overstated: the supersede counter RESETS on any live completion, so it caps
    a single wedge episode rather than the process lifetime. Both properties
    are now asserted at once — each bound is above its own statically-known
    inner total — AND the probe still runs on a dedicated coordinator rather
    than the shared default pool.

    This test is THE canonical per-plane tripwire (the structural test above
    deliberately does not duplicate these predicates): raise a PHASE in
    ``CONTROL_PLANE_PROBE_PHASES`` above its plane's bound and it fails. (It
    does NOT cover the client-level ``SupabaseControlPlane(timeout=...)``
    default — that is per-phase too, which is why the probe overrides it
    per-request rather than relying on it.)

    2026-09-13 (#3458): the cross-endpoint ceiling
    (``ready_worst_case < fly.toml's http_check timeout``) is now CONDITIONAL.
    #2850 removed ``[[services.http_checks]]`` from fly.toml (the /health HTTP
    check flapped and de-registered the sole machine), so the borrowed quantity
    no longer exists. When it is absent the test asserts instead that its
    documented replacement (``[[services.tcp_checks]]``) IS present — so the
    branch is a positive assertion, never a silent skip — and the ceiling
    re-arms automatically if an HTTP check budget ever returns.
    """
    import httpx

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc
    from tortoise.monitoring import PROBE_DB_TOTAL_TIMEOUT, PROBE_TIMEOUT

    # Data plane: ``probe_db`` retries one TRANSIENT connect failure, so its
    # real ceiling is PROBE_DB_TOTAL_TIMEOUT (2 x PROBE_TIMEOUT + the retry
    # delay), NOT the bare PROBE_TIMEOUT. Bounding against the per-attempt
    # figure looked correct (2.0 > 1.5) while actually sitting BELOW the true
    # 3.1s inner bound — the exact inversion this test exists to prevent.
    assert mod._READY_PROBE._timeout > PROBE_DB_TOTAL_TIMEOUT, (
        f"the FalkorDB readiness bound ({mod._READY_PROBE._timeout}s) must exceed "
        f"probe_db's TOTAL bound ({PROBE_DB_TOTAL_TIMEOUT}s = 2 x {PROBE_TIMEOUT}s "
        "+ the retry delay) or the outer bound wins the race and strands a worker "
        "thread per timeout (#2988)"
    )

    # Control plane: the probe request carries its OWN composed per-request
    # timeout whose phases SUM to CONTROL_PLANE_PROBE_TOTAL_S. Assert against
    # that composed TOTAL — NOT SupabaseControlPlane's 5.0s constructor default,
    # which httpx applies PER PHASE (connect/read/write/pool) and is therefore
    # not a deadline at all: a multi-phase stall could run ~15-20s and outrun
    # the outer bound.
    assert mod.CONTROL_PLANE_HARD_TIMEOUT > mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        f"the control-plane bound ({mod.CONTROL_PLANE_HARD_TIMEOUT}s) must exceed the "
        f"probe request's composed total ({mod.CONTROL_PLANE_PROBE_TOTAL_S}s) or the "
        "outer bound wins the race and strands a worker thread (#2988)"
    )
    assert sum(mod.CONTROL_PLANE_PROBE_PHASES.values()) == mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        "DRIFT GUARD (NOT a bound): CONTROL_PLANE_PROBE_TOTAL_S must remain the "
        "sum of the request's per-phase timeouts"
    )
    assert mod._CONTROL_PLANE_PROBE._timeout == mod.CONTROL_PLANE_HARD_TIMEOUT, (
        "the control-plane coordinator must use the derived bound"
    )

    # Behavioural half: the composed timeout must actually reach the request.
    # A constant nothing passes is dead code, and the client would silently keep
    # its per-phase default.
    seen: dict = {}

    class _CapturingControlPlane:
        def query(self, table, *, select=None, filters=None, method="GET",
                  json_body=None, order=None, limit=None, timeout=None):
            seen["timeout"] = timeout
            seen["table"] = table
            return []

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CapturingControlPlane())
    assert mod._probe_control_plane()["ok"] is True
    passed = seen["timeout"]
    assert isinstance(passed, httpx.Timeout), (
        f"the control-plane probe passed {passed!r} — it must pass a composed "
        "httpx.Timeout, or the per-phase default stays in force"
    )
    assert (passed.connect + passed.read + passed.write + passed.pool) \
        == mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        "the probe request's phases must sum to CONTROL_PLANE_PROBE_TOTAL_S"
    )

    # /health/ready runs the two planes SEQUENTIALLY, so its worst case is the
    # sum of the two bounds. The cross-endpoint ceiling that used to be asserted
    # here (`ready_worst_case < fly.toml's http_check timeout`) was DROPPED on
    # 2026-09-13 (#3458): #2850 removed [[services.http_checks]] from fly.toml,
    # so the borrowed proxy no longer exists. Its successor is a TCP check whose
    # timeout times a kernel accept and is explicitly "not a latency budget",
    # and the top-level [checks.loop_liveness] that would restore a real proxy is
    # deferred. The docstring already disclaimed that ceiling as "deliberate
    # conservatism, not a claim about a real readiness deadline" — with the
    # borrowed quantity gone, asserting it against a different one would be
    # fabricated evidence. The PER-PLANE ordering assertions above are the
    # substantive tripwire and remain fully enforced.
    ready_worst_case = mod.DB_PROBE_HARD_TIMEOUT + mod.CONTROL_PLANE_HARD_TIMEOUT
    ceiling = _fly_check_budget_proxy_s()
    if ceiling is not None:
        # Restored automatically if/when fly.toml regains an HTTP check budget.
        assert ready_worst_case < ceiling, (
            f"/health/ready's sequential worst case ({ready_worst_case}s) must stay "
            f"under fly.toml's http_check timeout ({ceiling}s), borrowed ONLY as a "
            "coarse budget proxy for /health/ready (that check actually targets "
            "/health, and deploy-hosted.yml curls /health/ready with no --max-time)"
        )
    else:
        # No HTTP-check budget in fly.toml (the #2850 state). Make that ABSENCE a
        # positive assertion rather than a silent skip: it may only mean the
        # documented HTTP->TCP migration, so the replacement must be present. An
        # accidental deletion of the whole checks block therefore still reds here.
        _cfg = tomllib.loads((REPO / "fly.toml").read_text())
        assert _cfg["services"][0].get("tcp_checks"), (
            "fly.toml exposes NEITHER an http_check budget proxy NOR the "
            "tcp_checks that replaced it (#2850) — the services checks block "
            "was removed or altered without the documented migration, so the "
            "cross-endpoint ceiling is now unverifiable rather than migrated"
        )


def test_db_probe_bound_covers_the_sdk_acquisition_prefix():
    """The DB probes' bound is aligned above the whole worker as far as the
    acquisition cost can be computed, not just ``probe_db``.

    ``_probe_db`` is ``_probe_sdk()`` THEN ``probe_db(sdk)``. In URI mode
    (the hosted steady state) the acquisition prefix is ~free — the SDK's
    projection is LAZY and the connect happens inside ``probe_db``'s own
    per-attempt bound. On the EMBEDDED path (cold ``_probe_sdk`` cache /
    ``_probe_sdk_reset()``) the anchor path connects EAGERLY and runs real
    queries bounded by the redis READ timeout, which the budget does NOT
    cover. The bound is DERIVED as ``PROBE_DB_TOTAL_TIMEOUT`` +
    ``PROBE_SDK_ACQUISITION_BUDGET`` (best-effort) — pin both the budget's
    source (so it cannot drift from projection) and the derivation (so a
    ``PROBE_TIMEOUT`` change propagates instead of leaving a stale hand-typed
    literal).
    """
    import tortoise.hosted_api as mod
    from tortoise.monitoring import (
        PROBE_DB_TOTAL_TIMEOUT,
        PROBE_HARD_TIMEOUT,
        PROBE_SDK_ACQUISITION_BUDGET,
    )
    from tortoise.projection import _DB_CONNECT_TIMEOUT_DEFAULT

    assert PROBE_SDK_ACQUISITION_BUDGET == _DB_CONNECT_TIMEOUT_DEFAULT, (
        "the SDK-acquisition budget must track the redis client's default "
        "socket_connect_timeout (projection._DB_CONNECT_TIMEOUT_DEFAULT) — a "
        "drift makes the DB probe's bound dishonest"
    )
    # The bound is the SINGLE shared derived constant (hosted_api aliases it),
    # so a PROBE_TIMEOUT change propagates instead of leaving a stale literal.
    assert mod.DB_PROBE_HARD_TIMEOUT == PROBE_HARD_TIMEOUT, (
        "DB_PROBE_HARD_TIMEOUT must be the shared derived bound, not a second "
        "hand-typed literal"
    )
    # STRICTLY above the statically-known total (probe_db's two-attempt total
    # PLUS the nominal acquisition budget) — equality would still be a race.
    # This does NOT cover the embedded acquisition prefix.
    assert mod.DB_PROBE_HARD_TIMEOUT > \
        PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET, (
        "DB_PROBE_HARD_TIMEOUT must sit strictly above PROBE_DB_TOTAL_TIMEOUT + "
        "the SDK-acquisition budget, or the outer bound can win the race"
    )


# ── the selfhost twin of the same defect ───────────────────────────────────


def test_selfhost_ready_does_not_probe_on_the_loop():
    """``tortoise/selfhost.py::health_ready`` had the identical bug: it built the
    SDK and touched the DB inline. ``publish-selfhost.yml`` curls this endpoint
    on every publish, so it is the same outage vector in the other image."""
    node = _handler("health_ready", SELFHOST)
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"_get_proj", "query"}
    ]
    assert not offenders, (
        f"selfhost health_ready calls DB-touching code directly at line(s) {offenders} — "
        "synchronous DB work on the event loop (#2988)"
    )
    off_loop = {
        arg.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
        for arg in call.args
        if isinstance(arg, ast.Name)
    }
    assert off_loop, "selfhost health_ready dispatches nothing with asyncio.to_thread"


def test_selfhost_probe_is_bounded():
    node = _handler("health_ready", SELFHOST)
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "wait_for"
        for n in ast.walk(node)
    ), "selfhost health_ready's probe is unbounded — a black-holed DB would hang it"
    assert re.search(r"_READY_PROBE_TIMEOUT_S = ([\d.]+)", SELFHOST.read_text()), (
        "selfhost.py must own its bound as a module constant"
    )


# ── behavioural proof ──────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_loop_stays_responsive_while_probes_are_slow(monkeypatch):
    """The decisive test: with a probe that BUSY-WAITS, a blocking
    implementation starves the ticker. Deterministic by construction — the
    probe signals when it is actually running and the assertion is about ticks
    that happened WHILE it was pending, so scheduler jitter cannot flip it.

    (The first version of this test asserted a fixed tick count against a
    sleeping probe and false-failed ~1 run in 5 under the docker lane: the loop
    was merely descheduled, not blocked. A flaky guard in a registered CI
    surface is worse than no guard.)
    """
    import threading

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    entered = threading.Event()
    release = threading.Event()

    def slow_probe():
        entered.set()
        release.wait(30)
        return {"ok": True, "latency_ms": 1.0, "error": None}

    monkeypatch.setattr(mod, "_probe_db", slow_probe)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            ready_task = asyncio.create_task(mod.health_ready())
            # The probe is dispatched before the first await completes; wait for
            # it to be inside its thread, then count ticks while it is pending.
            while not entered.is_set():
                await asyncio.sleep(0.01)
            before = ticks
            deadline = time.time() + 5.0
            while ticks - before < 3 and time.time() < deadline:
                await asyncio.sleep(0.01)
            pending_ticks = ticks - before
            release.set()
            result = await ready_task
        finally:
            release.set()
            ticker_task.cancel()
        return result, pending_ticks

    result, pending_ticks = _run(scenario())
    assert result["status"] == "ok"
    assert pending_ticks >= 3, (
        f"the event loop ticked {pending_ticks} times while the probe was pending — "
        "a blocking implementation freezes it at 0 (#2988)"
    )


def test_hung_data_plane_returns_503_within_the_bound(monkeypatch):
    """A black-holed probe must be REPORTED, not waited out.

    The latency is measured INSIDE the coroutine: ``asyncio.run`` joins the
    default executor at shutdown (``loop.shutdown_default_executor``), so wall
    time around it includes the still-running probe thread — in production the
    loop is long-lived and there is no such join.
    """
    import threading

    import tortoise.hosted_api as mod

    release = threading.Event()
    monkeypatch.setattr(mod._READY_PROBE, "_timeout", 0.2)
    mod._READY_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_db", lambda: release.wait(30))

    async def scenario():
        started = time.time()
        try:
            with pytest.raises(HTTPException) as excinfo:
                await mod.health_ready()
        finally:
            release.set()
        return time.time() - started, excinfo.value

    elapsed, exc = _run(scenario())
    assert exc.status_code == 503
    assert elapsed < 1.2, (
        f"the endpoint waited {elapsed:.2f}s for a hung probe — the bound is not applied"
    )


def test_hung_control_plane_returns_503_within_the_bound(monkeypatch):
    import threading

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    release = threading.Event()
    monkeypatch.setattr(mod._CONTROL_PLANE_PROBE, "_timeout", 0.2)
    mod._CONTROL_PLANE_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": True, "latency_ms": 1.0, "error": None})
    monkeypatch.setattr(mod, "_probe_control_plane", lambda: release.wait(30))
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    async def scenario():
        started = time.time()
        try:
            with pytest.raises(HTTPException) as excinfo:
                await mod.health_ready()
        finally:
            release.set()
        return time.time() - started, excinfo.value

    elapsed, exc = _run(scenario())
    assert exc.status_code == 503
    assert "Control plane" in str(exc.detail)
    assert elapsed < 1.2, f"waited {elapsed:.2f}s for a hung control plane"


def test_ready_when_both_planes_answer(monkeypatch):
    """The happy path keeps the exact response shape the deploy gates consume."""
    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": True, "latency_ms": 2.0, "error": None})
    called = []

    def _fake_control():
        # #2850: the control-plane probe now RETURNS its verdict (the
        # coordinator reads `{"ok": ...}`); under #2988 success was implied by
        # not raising, so this stub used to return None.
        called.append(1)
        return {"ok": True, "latency_ms": 1.0, "error": None}

    mod._CONTROL_PLANE_PROBE.reset()
    mod._READY_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_control_plane", _fake_control)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    assert _run(mod.health_ready()) == {
        "status": "ok",
        "db": "connected",
        "control_plane": "connected",
    }
    assert called == [1], "the control-plane probe did not run"


def test_dead_db_is_503_and_never_probes_the_control_plane(monkeypatch):
    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": False, "latency_ms": 0.0, "error": "down"})
    probed = []
    monkeypatch.setattr(mod, "_probe_control_plane", lambda: probed.append(1))
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    with pytest.raises(HTTPException) as excinfo:
        _run(mod.health_ready())
    assert excinfo.value.status_code == 503
    assert probed == []
