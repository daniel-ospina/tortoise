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
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"


def _handler(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in hosted_api.py")


def _parents(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}


# ── structural pins ────────────────────────────────────────────────────────


def test_handler_makes_no_direct_query_call():
    """``.query(...)`` is synchronous network I/O — it belongs in the module
    level probes, never in the coroutine body. This is the exact line that
    caused the outage (``sdk._get_proj().g.query("RETURN 1")``), and its
    control-plane sibling."""
    node = _handler("health_ready")
    offenders = [
        n.lineno
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "query"
    ]
    assert not offenders, (
        f"health_ready calls .query(...) directly at line(s) {offenders} — "
        "that runs synchronous network I/O on the event loop and freezes every "
        "other request (#2988)"
    )


def test_both_probes_run_off_the_loop():
    node = _handler("health_ready")
    off_loop = {
        arg.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
        for arg in call.args
        if isinstance(arg, ast.Name)
    }
    assert {"_probe_db", "_probe_control_plane"} <= off_loop, (
        f"probes not dispatched with asyncio.to_thread (found {sorted(off_loop)})"
    )


def test_every_off_loop_probe_is_bounded():
    """Dispatching off the loop keeps the process alive; the bound is what makes
    the endpoint ANSWER when a plane black-holes. Without it a hung probe still
    leaks an executor thread per request."""
    node = _handler("health_ready")
    parents = _parents(node)
    unbounded = []
    for call in ast.walk(node):
        if not (
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "to_thread"
        ):
            continue
        walker, inside = call, False
        while walker in parents:
            walker = parents[walker]
            if (
                isinstance(walker, ast.Call)
                and isinstance(walker.func, ast.Attribute)
                and walker.func.attr == "wait_for"
            ):
                inside = True
                break
        if not inside:
            unbounded.append(call.lineno)
    assert not unbounded, (
        f"to_thread at line(s) {unbounded} is not wrapped in asyncio.wait_for — "
        "a black-holed probe would be waited on indefinitely (#2988)"
    )
    # Non-vacuity: with NO to_thread calls the check above passes trivially.
    # (test_both_probes_run_off_the_loop catches that, but each pin should stand
    # on its own — a vacuous guard is a guard that silently stops guarding.)
    dispatched = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
    ]
    assert len(dispatched) >= 2, (
        f"expected the data-plane and control-plane probes dispatched off the loop, "
        f"found {len(dispatched)} to_thread call(s)"
    )


def test_wait_for_uses_the_module_bound():
    node = _handler("health_ready")
    uses = [
        kw
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "wait_for"
        for kw in call.keywords
        if kw.arg == "timeout" and isinstance(kw.value, ast.Name)
    ]
    assert uses, "no wait_for(...) pins the module-level probe bound"
    assert all(kw.value.id == "_READY_PROBE_TIMEOUT_S" for kw in uses), (
        "the probe bound must be the module constant, so it can be reasoned "
        "about (and tested) in one place"
    )


def test_probe_bound_is_smaller_than_the_platform_health_check():
    """Fly's http_check allows 15s (fly.toml). The bound must stay well under
    it, or a slow plane reads as an unhealthy MACHINE rather than a 503."""
    import re

    src = HOSTED_API.read_text()
    bound = float(re.search(r"_READY_PROBE_TIMEOUT_S = ([\d.]+)", src).group(1))
    assert 0 < bound < 15, bound


# ── behavioural proof ──────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_loop_stays_responsive_while_probes_are_slow(monkeypatch):
    """The decisive test: with a probe that BUSY-WAITS, a blocking
    implementation starves the ticker to ~0. The off-loop one keeps ticking
    (measured: tens of ticks)."""
    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(mod, "_probe_db", lambda: (time.sleep(0.3), {"ok": True})[1])
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            result = await mod.health_ready()
        finally:
            task.cancel()
        return result, ticks

    result, ticks = _run(scenario())
    assert result["status"] == "ok"
    assert ticks >= 10, (
        f"the event loop only ticked {ticks} times while the probe slept — "
        "the probe is running ON the loop (#2988)"
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
    monkeypatch.setattr(mod, "_READY_PROBE_TIMEOUT_S", 0.2)
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
    monkeypatch.setattr(mod, "_READY_PROBE_TIMEOUT_S", 0.2)
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
    monkeypatch.setattr(mod, "_probe_control_plane", lambda: called.append(1))
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
