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


def test_every_plane_probe_is_hard_bounded_and_fail_closed():
    """The bound is what makes the endpoint ANSWER when a plane black-holes.

    It now lives on each ``HealthProbe`` (``timeout=PROBE_HARD_TIMEOUT``) rather
    than in a per-handler ``wait_for``, so pin BOTH the shared bound and the
    fail-closed flag. ``_READY_PROBE_TIMEOUT_S`` is gone with the mechanism it
    bounded; a reintroduced per-handler literal would be an unreasoned second
    source of truth.
    """
    import inspect

    import tortoise.hosted_api as mod
    from tortoise.monitoring import PROBE_HARD_TIMEOUT

    assert (
        inspect.signature(mod.HealthProbe.__init__).parameters["timeout"].default
        == PROBE_HARD_TIMEOUT
    ), "HealthProbe's default wall bound must be the shared module constant"
    assert "_READY_PROBE_TIMEOUT_S" not in HOSTED_API.read_text(), (
        "the superseded per-handler readiness bound is back — the bound belongs "
        "to HealthProbe (one reasoned place)"
    )
    for name in ("_READY_PROBE", "_CONTROL_PLANE_PROBE"):
        probe = getattr(mod, name)
        assert probe._timeout == PROBE_HARD_TIMEOUT, f"{name} overrides the shared bound"
        assert probe._fresh_only is True, (
            f"{name} must be fresh_only=True — readiness is a FAIL-CLOSED gate and "
            "must never answer 200 from a verdict older than its read budget (#1384/#2850)"
        )


def test_the_abandoned_worker_leak_is_bounded_by_construction():
    """#2850 deliberately INVERTS #2988's bound-ordering invariant — pin the why.

    #2988 kept the per-handler ``wait_for`` bound STRICTLY ABOVE the
    control-plane client timeout so the client timeout fired first and no
    executor worker was left parked in a socket read. That is the right fix for
    ``to_thread`` on the SHARED default pool, where each abandoned worker is
    lost from a pool every other request depends on.

    #2850 replaces that with a private single-slot daemon worker plus an
    explicit ``max_supersedes`` cap, so abandoning an in-flight probe costs one
    already-dedicated thread and can happen at most ``max_supersedes`` times for
    the process LIFETIME — never once per check. The leak is therefore bounded
    by CONSTRUCTION rather than by ordering, which is why the bound
    (``PROBE_HARD_TIMEOUT``) may sit below the client timeout here.

    If the ordering assumption is ever restored, or the supersede cap removed,
    this test is the tripwire.
    """
    import inspect

    from tortoise.monitoring import PROBE_HARD_TIMEOUT, PROBE_MAX_SUPERSEDES
    from tortoise.supabase_control import SupabaseControlPlane

    client_timeout = inspect.signature(SupabaseControlPlane.__init__).parameters["timeout"].default
    assert PROBE_HARD_TIMEOUT < client_timeout, (
        "this test documents why an outer bound BELOW the client timeout is safe "
        f"here (bound={PROBE_HARD_TIMEOUT}, client={client_timeout}); if the bound "
        "is now above it, the reasoning changed and this test is stale"
    )
    assert PROBE_MAX_SUPERSEDES > 0, (
        "the supersede cap is what bounds the abandoned workers — without it the "
        "#2988 ordering invariant becomes load-bearing again"
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
