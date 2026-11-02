"""#10612 P1 — a reused ``Context`` makes the read lane's retry an unretryable 500.

Measured at PR #10612's head (comment 6586045159) on ``/v1/search`` and
``/v1/topics/{t}/summary``:

    bound miss; concurrent future cancelled? False done? False
    immediately after bound miss, first run still executing: True
    RETRY RAISED: RuntimeError : cannot enter context: <_contextvars.Context object at 0x...> is already entered

The mechanism is a single-entry ``contextvars.Context`` submitted TWICE. The
branch's offload idiom was ``submitted = functools.partial(contextvars.copy_context().run, fn)``
built ONCE and reused by a re-arm loop; a bound miss cancels the AWAIT and never
the running thread (CPython #87185), so the first submission is still inside
``ctx.run`` when the retry enters the same context — ``RuntimeError``, which is
NOT a ``ControlPlaneOffloadError``, so it escaped the offload seam's fail-closed
mapping onto the route's blanket ``except Exception``: HTTP 500 ``"Search
failed"`` / ``"Topic summary failed"``, no ``error_code``, not retryable.

``hosted_api`` wraps once per submission, so that shape is a LATENT trap there
rather than a live one; these tests pin the MECHANISM at the seam so neither the
branch nor any future caller can reintroduce it.

The fix is ``monitoring.confine_context``: ONE place that re-materialises a
FRESH copy of the submitting context per invocation, so a retry, a
re-submission or an overlapping worker cannot re-enter anything and the
per-submission isolation ``copy_context`` existed for is preserved.

Three properties, each its own test:
  (a) a resubmission while the first is still running completes (the fix) and
      the one-shot idiom it replaces raises (the mechanism, pinned so a
      mutation is red);
  (b) two concurrent requests keep their own tenant context — no bleed;
  (c) when the context genuinely cannot be reproduced the failure is NAMED and
      fail-closed (``ContextConfinementError`` → the seam's retryable 503 with
      an ``error_code``), never a bare 500 and never a run under the worker's
      ambient context (which would answer another request's tenant).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import contextvars
import functools
import threading

import pytest
from fastapi import HTTPException

import tortoise.hosted_api as ha
import tortoise.mcp_auth as mcp_auth
import tortoise.monitoring as monitoring
from tortoise.monitoring import (
    ContextConfinementError,
    ControlPlaneOffloadError,
    confine_context,
    run_control_plane_call,
)

_TIMEOUT = 10.0


# ── (a) the recorded reproduction ───────────────────────────────────────────


def test_the_one_shot_idiom_cannot_be_resubmitted() -> None:
    """The mechanism, pinned: one ``Context``, two overlapping submissions.

    This is the arm that MUST keep raising — it is what the fix removes from
    the seam, and the mutation check (swap ``confine_context`` back to this
    idiom) turns the test below red. If CPython ever makes ``Context`` objects
    re-entrant this assert fires and we revisit the fix.
    """
    started = threading.Event()
    release = threading.Event()

    def fn(tag: str) -> str:
        started.set()
        release.wait(_TIMEOUT)
        return f"{tag}-done"

    ctx = contextvars.copy_context()
    submitted = functools.partial(ctx.run, fn, "first")

    async def _run() -> None:
        # The bound miss leaves the callable RUNNING (wait_for cancels the
        # await, never the thread) — the #4055 cold-setup drain then re-arms.
        try:
            await run_control_plane_call(
                submitted, op="ctxvar-repro", pool="graph", timeout=0.3)
        except ControlPlaneOffloadError as exc:
            assert exc.refused is False, (
                "a bound miss over a RUNNING callable is not a refusal")
        assert started.wait(_TIMEOUT), "the first submission never started"

        with pytest.raises(RuntimeError, match="already entered"):
            await run_control_plane_call(
                functools.partial(ctx.run, fn, "second"),
                op="ctxvar-repro", pool="graph", timeout=_TIMEOUT)

    try:
        asyncio.run(_run())
    finally:
        release.set()


def test_confined_context_survives_a_resubmission_while_the_first_runs() -> None:
    """The fix: the SAME wrapped callable, submitted twice, overlapping.

    Fails on main (``confine_context`` does not exist / a one-shot
    ``partial(copy_context().run, ...)`` raises ``RuntimeError: cannot enter
    context ... already entered``).
    """
    first_started = threading.Event()
    first_finished = threading.Event()
    release = threading.Event()
    seen: list[str] = []

    def fn(tag: str) -> str:
        if tag == "first":
            first_started.set()
            release.wait(_TIMEOUT)
        seen.append(tag)
        if tag == "first":
            # JOIN the worker's append before the outer assert reads `seen`:
            # `release.set()` alone races it (measured: 2 of 37 runs failed).
            first_finished.set()
        return f"{tag}-done"

    confined = confine_context(fn)
    first_submission = functools.partial(confined, "first")
    retry = functools.partial(confined, "second")

    async def _run() -> tuple[str, str]:
        # The bound miss abandons the AWAIT; the worker keeps running.
        with contextlib.suppress(ControlPlaneOffloadError):
            await run_control_plane_call(
                first_submission, op="ctxvar-fix", pool="graph", timeout=0.3)
        assert first_started.wait(_TIMEOUT), "the first submission never started"
        # The retry, while the first is still inside its context. This is the
        # exact call that raised RuntimeError before the fix.
        second = await run_control_plane_call(
            retry, op="ctxvar-fix", pool="graph", timeout=_TIMEOUT)
        release.set()
        return "retry-returned", second

    try:
        marker, second = asyncio.run(_run())
    finally:
        release.set()

    assert first_finished.wait(_TIMEOUT), (
        "the first submission, abandoned at the bound, never completed")
    assert marker == "retry-returned"
    assert second == "second-done"
    assert seen == ["second", "first"], (
        "both submissions must COMPLETE — a re-entry would have raised instead "
        f"of running; saw {seen}")


# ── (b) two concurrent requests, two tenants, no bleed ─────────────────────


def test_concurrent_requests_keep_their_own_tenant_context() -> None:
    """Isolation is per REQUEST: neither tenant sees the other's org/graph.

    Both submissions are forced to overlap on a barrier, so a shared context
    (the pre-fix shape once re-entered, or a worker-ambient read) would show the
    other request's values.
    """
    barrier = threading.Barrier(2, timeout=_TIMEOUT)
    seen: dict[str, tuple[object, object]] = {}

    def _read(tag: str) -> str:
        # The tenant vars a graph read resolves from (mcp_auth's per-request
        # contextvars). Force overlap BEFORE reading so a leak is unavoidable.
        barrier.wait()
        seen[tag] = (mcp_auth._current_org_id.get(),
                     mcp_auth._current_graph_id.get())
        # A write inside the invocation must not survive into the other one.
        mcp_auth._current_graph_id.set(f"written-by-{tag}")
        return tag

    async def _request(org: str, graph: str, tag: str) -> str:
        mcp_auth._current_org_id.set(org)
        mcp_auth._current_graph_id.set(graph)
        confined = confine_context(_read)          # snapshot taken HERE
        result = await run_control_plane_call(
            functools.partial(confined, tag), op="ctxvar-tenant", pool="graph",
            timeout=_TIMEOUT)
        assert mcp_auth._current_graph_id.get() == graph, (
            "the worker's write leaked back into the request context")
        return result

    async def _run() -> list[str]:
        return await asyncio.gather(
            _request("org-a", "graph-a", "a"),
            _request("org-b", "graph-b", "b"),
        )

    assert asyncio.run(_run()) == ["a", "b"]
    assert seen == {"a": ("org-a", "graph-a"), "b": ("org-b", "graph-b")}, (
        f"tenant context bled between concurrent requests: {seen}")


# ── (c) honest failure when the context cannot be established ──────────────


def test_unreproducible_context_fails_closed_and_named(monkeypatch) -> None:
    """No ambient fallback: ``ContextConfinementError`` and ``fn`` never runs.

    The worker's own context is process-lifetime state — running there would
    read whichever request the thread served last. So the seam REFUSES, and the
    refusal is a named error, never a silent default.
    """
    ran: list[str] = []

    def fn() -> str:
        ran.append("ran")
        return "answer"

    def _poison(_snapshot):
        # A plausible way a factory that must RETURN a Context can fail:
        # `copy()` raising (MemoryError in practice) — which is exactly what the
        # `except` in `_confined` exists to convert. Deliberately NOT shaped
        # like a re-entry error: that mode is the one this change removes.
        raise MemoryError("simulated: allocation failure while copying the context")

    monkeypatch.setattr(monitoring, "_fresh_context", _poison)

    with pytest.raises(ContextConfinementError) as excinfo:
        confine_context(fn)()
    assert "could not reproduce the submitting context" in str(excinfo.value)
    assert "simulated" in str(excinfo.value)
    assert ran == [], "the callable must NOT run without its request context"

    # …and at the seam it is the retryable, LABELLED 503 — not a bare 500.
    async def _seam() -> None:
        await ha._cp_offload(confine_context(fn), op="ctxvar-honest",
                             pool="graph", unavailable=ha._graph_unavailable)

    with pytest.raises(HTTPException) as http:
        asyncio.run(_seam())
    assert http.value.status_code == 503
    assert http.value.detail["error_code"] == "graph_unavailable"
    assert ran == [], "a refused context must never produce an answer"


# ── (d) the two call sites this diff actually changes ──────────────────────


def test_graph_offload_hands_over_a_resubmittable_callable(monkeypatch) -> None:
    """``_graph_offload`` must not hand the pool a one-shot ``ctx.run`` partial.

    Reverts the fix at the call site (back to
    ``functools.partial(ctx.run, fn)``) and this goes red, which the
    ``confine_context`` unit tests alone cannot see: they never touch the seam
    the product uses.
    """
    captured: dict = {}

    async def _fake_cp_offload(fn, **kwargs):
        captured["fn"] = fn
        captured["op"] = kwargs.get("op")
        captured["pool"] = kwargs.get("pool")
        return "read-answer-from-fake"

    monkeypatch.setattr(ha, "_cp_offload", _fake_cp_offload)
    # Hand the seam's own callable back, so the test can invoke what the pool
    # would have run instead of a re-wrapped stand-in.
    asyncio.run(ha._graph_offload(lambda: "read", op="data_sdk"))
    assert captured["op"] == "data_sdk"
    assert captured["pool"] == "graph", (
        "the graph lane's pool is what carries the isolation")

    held = threading.Event()
    entered = threading.Event()
    calls: list[str] = []

    def _offloaded() -> str:
        calls.append("call")
        if len(calls) == 1:              # the FIRST submission parks…
            entered.set()
            held.wait(_TIMEOUT)
        return "graph-answer"          # …the second must not

    # The callable the pool WOULD have run, straight from the seam — invoked
    # twice, overlapping. A one-shot ctx.run partial raises on the second.
    submitted = asyncio.run(ha._graph_offload(_offloaded, op="data_sdk"))
    assert submitted == "read-answer-from-fake", submitted

    wrapped = captured["fn"]
    first = threading.Thread(target=wrapped)
    first.start()
    assert entered.wait(_TIMEOUT)
    # The second invocation goes through the SAME callable while the first is
    # still inside its context — a stored Context cannot survive this (the
    # measured RuntimeError).
    assert wrapped() == "graph-answer"
    held.set()
    first.join(_TIMEOUT)
    assert not first.is_alive()
    assert calls == ["call", "call"]


def test_submit_off_loop_preserves_positional_and_keyword_args() -> None:
    """The fix must not change how ``_submit_off_loop`` binds arguments."""
    seen: dict = {}

    def fn(a, *, b):
        seen["thread"] = threading.current_thread().name
        return f"{a}:{b}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = ha._submit_off_loop(executor, fn, "pos", b="kw")
        assert future.result(timeout=_TIMEOUT) == "pos:kw"
    assert seen["thread"] != threading.main_thread().name, (
        "the callable must run OFF the calling thread")


def test_submit_off_loop_still_propagates_the_caller_context() -> None:
    """The seam's documented REASON to exist ("preserving contextvars").

    The diff replaced its mechanism (``ctx.run`` + ``partial`` →
    ``confine_context``), so the property has to be pinned: dropping the copy
    (``executor.submit(fn, ...)``) is silent, not loud — ``graph_ops._ACTIVE``
    defaults to ``None``, so a lost context makes graph-op accounting a no-op.
    Mirrors ``test_graph_offload_isolates_the_caller_context``.
    """
    var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "ctxvar_submit_off_loop_probe", default=None)

    def fn():
        inherited = var.get()
        var.set("written-in-the-worker")
        return inherited, var.get()

    var.set("caller-value")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        inherited, in_worker = ha._submit_off_loop(executor, fn).result(
            timeout=_TIMEOUT)

    assert inherited == "caller-value", (
        "the caller's contextvars must reach the worker")
    assert in_worker == "written-in-the-worker"
    assert var.get() == "caller-value", (
        "the worker's write must NOT leak back into the caller's context")
