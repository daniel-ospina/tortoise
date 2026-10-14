"""Tests for MCP tool-call telemetry (#889) — friction evidence for epic #888.

Covers the issue's verification checklist:
- event emitted per call (exactly one, incl. the middleware re-dispatch guard)
- the 4 status categories produced here (ok / validation_error / auth_error /
  exec_error; the emission also carries timeout / cancelled / refused — see
  ``_emit_mcp_tool_call_telemetry``)
- validation vs exec error classification (pydantic → validation_error with
  '<error_type>:<field>' kind; everything else → exec_error with class name)
- latency present and measured around the tool execution
- analytics failure never breaks a tool call (fail-safe emitter)
- the wrapper's overhead on the tool-call path adds NO event-loop scheduling
  hop, at any result size, and a bounded incremental cost (#3678)

Runs against the shared module-level mcp instance with tiny test-only tools
registered per-test and removed afterwards (same pattern as test_tool_registry).
"""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp.tools import FunctionTool  # noqa: I001
from tortoise.mcp_auth import _current_org_id, _transport_mode

TEST_TOOLS = [
    ("_telemetry_echo", "echo(message: str) -> dict", "telemetry test: echo"),
    ("_telemetry_slow", "slow() -> dict", "telemetry test: slow"),
    ("_telemetry_boom", "boom() -> dict", "telemetry test: raises"),
    ("_telemetry_gated", "gated() -> dict", "telemetry test: _safe auth gate"),
]


def _telemetry_echo(message: str) -> dict:
    return {"echo": message}


async def _telemetry_slow() -> dict:
    await asyncio.sleep(0.02)
    return {"slow": True}


def _telemetry_boom() -> dict:
    raise RuntimeError("telemetry boom")


def _telemetry_gated() -> dict:
    from tortoise.mcp_server import _safe
    return _safe(lambda: {"authed": True})


@pytest.fixture(autouse=True)
def _transport_context():
    """Stdio-mode transport context (same as test_mcp_server)."""
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)


@pytest.fixture
def test_tools():
    """Register test-only tools on the shared mcp instance; remove after."""
    from tortoise.mcp_server import mcp
    fns = {"_telemetry_echo": _telemetry_echo, "_telemetry_slow": _telemetry_slow,
           "_telemetry_boom": _telemetry_boom, "_telemetry_gated": _telemetry_gated}
    for name, fn in fns.items():
        mcp.add_tool(FunctionTool.from_function(fn, name=name,
                                                description=f"telemetry test: {name}"))
    yield
    for name in fns:
        try:  # noqa: SIM105
            mcp.local_provider.remove_tool(name)
        except Exception:
            pass


@pytest.fixture
def captured_events(monkeypatch):
    """Swap the emitter for a synchronous recorder (deterministic, no I/O).

    The recorder mirrors the emitter's ARITY: an exact-5-arg stub used to sit
    here, and when #3675 added ``result_count`` the wrapper's call raised
    TypeError INSIDE the wrapper's own `except Exception` guard — so every event
    silently vanished and seven tests failed with an empty list rather than a
    signature error. Recording the param (and asserting it is present) is what
    turns that into a legible failure next time.
    """
    from tortoise import mcp_server
    events = []

    def _capture(org_id, tool_name, status, latency_ms, error_kind,
                 result_count=None):
        events.append({"org_id": org_id, "tool_name": tool_name,
                       "status": status, "latency_ms": latency_ms,
                       "error_kind": error_kind,
                       "result_count": result_count})

    monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", _capture)
    return events


class TestEventPerCall:
    """Requirement 1: one structured analytics event per tool call."""

    pytestmark = pytest.mark.asyncio

    async def test_ok_event_emitted_exactly_once(self, test_tools, captured_events):
        from tortoise.mcp_server import mcp
        result = await mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert result.structured_content == {"echo": "hi"}
        # Exactly one event — the middleware re-dispatch (run_middleware=False)
        # must not emit a second one.
        assert len(captured_events) == 1
        ev = captured_events[0]
        assert ev["tool_name"] == "_telemetry_echo"
        assert ev["status"] == "ok"
        assert ev["error_kind"] is None
        # #3675: the result-bearing prop rides the same call. ``_telemetry_echo``
        # returns ``{"echo": ...}`` — no item collection — so the count is
        # UNKNOWABLE and must be None, never 0 ("the tool returned nothing").
        # Asserting the KEY is present at all is the plumbing pin: a wrapper
        # that stopped passing it would show up here rather than as a silently
        # missing measurement in production.
        assert "result_count" in ev, ev
        assert ev["result_count"] is None, ev
        assert isinstance(ev["latency_ms"], int) and ev["latency_ms"] >= 0
        # Unauthenticated path (stdio, no team context) → empty org_id.
        assert ev["org_id"] == ""

    async def test_org_id_resolved_from_auth_context(self, test_tools,
                                                      captured_events):
        from tortoise.mcp_server import mcp
        _current_org_id.set("team_abc")
        await mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert captured_events[0]["org_id"] == "team_abc"

    async def test_latency_measured_around_execution(self, test_tools,
                                                     captured_events):
        """A 20ms tool must report >=15ms — latency spans the tool body."""
        from tortoise.mcp_server import mcp
        await mcp.call_tool("_telemetry_slow")
        assert captured_events[0]["latency_ms"] >= 15


class TestStatusCategories:
    """Requirement 3: all four statuses produced with correct classification."""

    pytestmark = pytest.mark.asyncio

    async def test_ok_status(self, test_tools, captured_events):
        from tortoise.mcp_server import mcp
        await mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert captured_events[0]["status"] == "ok"

    async def test_validation_error_status(self, test_tools, captured_events):
        """Wrong argument type → pydantic failure → validation_error."""
        from tortoise.mcp_server import mcp
        with pytest.raises(Exception):  # noqa: B017
            await mcp.call_tool("_telemetry_echo", {"message": 123})
        ev = captured_events[0]
        assert ev["status"] == "validation_error"
        assert ev["error_kind"] == "string_type:message"

    async def test_auth_error_status_from_stdio_gate(self, test_tools,
                                                     captured_events, monkeypatch):
        """Non-dev stdio mode: _safe returns the auth-required dict → auth_error.

        Exercises the REAL stdio auth gate (not a simulated dict), the exact
        production failure mode when TORTOISE_API_KEY is set.
        """
        from tortoise import mcp_server
        monkeypatch.setattr(mcp_server, "_is_dev_mode", lambda: False)
        result = await mcp_server.mcp.call_tool("_telemetry_gated")
        assert "Authentication required" in result.structured_content["error"]
        ev = captured_events[0]
        assert ev["status"] == "auth_error"
        assert ev["error_kind"] == "stdio_auth_gate"
        assert ev["org_id"] == ""

    async def test_exec_error_status(self, test_tools, captured_events):
        """Raised tool body error → exec_error with the CAUSE class name."""
        from tortoise.mcp_server import mcp
        with pytest.raises(Exception):  # noqa: B017
            await mcp.call_tool("_telemetry_boom")
        ev = captured_events[0]
        assert ev["status"] == "exec_error"
        assert ev["error_kind"] == "RuntimeError"  # unwrapped from ToolError


class TestClassification:
    """Requirement 3 (unit): validation vs exec vs auth classification."""

    def test_pydantic_validation_error_direct(self):
        from tortoise.mcp_server import _classify_mcp_call_error  # noqa: I001
        from pydantic import ValidationError
        try:
            from pydantic import BaseModel
            class M(BaseModel):
                query: str
            M()  # missing required field
            raise AssertionError("should have raised")
        except ValidationError as e:
            status, kind = _classify_mcp_call_error(e)
        assert status == "validation_error"
        assert kind == "missing:query"

    def test_fastmcp_validation_error_wrapping_pydantic(self):
        from tortoise.mcp_server import _classify_mcp_call_error  # noqa: I001
        from fastmcp.exceptions import ValidationError as FmValidationError
        from pydantic import ValidationError
        try:
            from pydantic import BaseModel
            class M(BaseModel):
                query: str
            M()
            raise AssertionError("should have raised")
        except ValidationError as e:
            wrapped = FmValidationError(str(e))
            wrapped.__cause__ = e
        status, kind = _classify_mcp_call_error(wrapped)
        assert status == "validation_error"
        assert kind == "missing:query"

    def test_exec_error_unwraps_fastmcp_wrapper(self):
        from tortoise.mcp_server import _classify_mcp_call_error  # noqa: I001
        from fastmcp.exceptions import ToolError
        status, kind = _classify_mcp_call_error(ToolError("boom"))
        assert status == "exec_error"
        assert kind == "ToolError"  # no cause → wrapper class itself
        inner = RuntimeError("kaboom")
        wrapped = ToolError("boom")
        wrapped.__cause__ = inner
        status, kind = _classify_mcp_call_error(wrapped)
        assert status == "exec_error"
        assert kind == "RuntimeError"  # cause class wins

    def test_auth_error(self):
        from tortoise.mcp_server import _classify_mcp_call_error  # noqa: I001
        from fastmcp.exceptions import AuthorizationError
        status, kind = _classify_mcp_call_error(AuthorizationError("nope"))
        assert status == "auth_error"
        assert kind == "AuthorizationError"

    def test_not_found_is_exec_error(self):
        from tortoise.mcp_server import _classify_mcp_call_error  # noqa: I001
        from fastmcp.exceptions import NotFoundError
        status, kind = _classify_mcp_call_error(NotFoundError("no tool"))
        assert status == "exec_error"
        assert kind == "NotFoundError"


class TestFailSafe:
    """Requirement: a telemetry failure must never break a tool call."""

    pytestmark = pytest.mark.asyncio


    async def test_emitter_exception_does_not_break_call(self, test_tools,
                                                         monkeypatch):
        from tortoise import mcp_server
        def _boom(org_id, tool_name, status, latency_ms, error_kind,
                  result_count=None):
            raise RuntimeError("telemetry exploded")
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", _boom)
        result = await mcp_server.mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert result.structured_content == {"echo": "hi"}

    async def test_emitter_exception_does_not_mask_tool_error(self, test_tools,
                                                              monkeypatch):
        from tortoise import mcp_server
        def _boom(org_id, tool_name, status, latency_ms, error_kind,
                  result_count=None):
            raise RuntimeError("telemetry exploded")
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", _boom)
        with pytest.raises(Exception) as ei:
            await mcp_server.mcp.call_tool("_telemetry_boom")
        assert "telemetry boom" in str(ei.value)

    async def test_writer_failure_is_swallowed(self, monkeypatch, tmp_path):
        """_track_analytics_event raising must not propagate from the emitter."""
        from tortoise import mcp_server, hosted_api  # noqa: I001

        def _broken(org_id, event_name, properties=None):
            raise RuntimeError("supabase down")
        monkeypatch.setattr(hosted_api, "_track_analytics_event", _broken)
        # No exception, even though the underlying writer explodes.
        mcp_server._emit_mcp_tool_call_telemetry("t1", "tortoise_status",
                                                 "ok", 3, None)
        await mcp_server._flush_mcp_telemetry()


class TestRealWritePath:
    """Emitter → existing _track_analytics_event path (JSONL fallback, no net).

    Proves the event lands with the exact #889 schema through the REAL writer,
    including the analytics props whitelist in hosted_api (tool_name/status/
    latency_ms/error_kind, and #3675's result_count, must survive the filter).
    """

    pytestmark = pytest.mark.asyncio


    async def test_event_lands_via_track_analytics_event(self, monkeypatch,
                                                         tmp_path):
        from tortoise import mcp_server, hosted_api  # noqa: I001
        # Force the local JSONL fallback (no Supabase configured).
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        # #3820 (cycle-2 P1): the canonical key name too — `SUPABASE_SERVICE_KEY`
        # is LEGACY, and an ambient production `SUPABASE_SERVICE_ROLE_KEY` would
        # otherwise make this "no Supabase" premise false.
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        fallback = tmp_path / "analytics_fallback.jsonl"
        monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH", str(fallback))

        mcp_server._emit_mcp_tool_call_telemetry(
            "team_x", "tortoise_create_point", "validation_error", 7,
            "missing:content", 0)
        await mcp_server._flush_mcp_telemetry()

        assert fallback.exists()
        event = json.loads(fallback.read_text().strip().splitlines()[0])
        assert event["org_id"] == "team_x"
        assert event["event_name"] == "mcp_tool_call"
        props = event["properties"]
        assert props["tool_name"] == "tortoise_create_point"
        assert props["status"] == "validation_error"
        assert props["latency_ms"] == 7
        assert props["error_kind"] == "missing:content"
        # #3675: a ZERO survives the writer's PII filter AS 0. This is the
        # value the whole prop exists for — "the retrieval ran and answered
        # nothing" — so the filter must not drop it as falsy, and an
        # unregistered key (which is stripped with no error) would fail here.
        assert props["result_count"] == 0, props


class TestOverhead:
    """Requirement 5: the telemetry wrapper's overhead on the tool-call path.

    ⛔ THE ABSOLUTE ``p95 < 5 ms`` WALL-CLOCK BUDGET IS GONE — DELIBERATELY
    (#3678). It asserted an absolute p95 on the TOTAL of ``mcp.call_tool``,
    which is dominated by FastMCP's own dispatch, printed with the emitter
    stubbed to a no-op. That quantity moves with HOST LOAD, not with the
    instrumentation: measured here (2026-10-13, origin/main ``f5823a6a4``) the
    unwrapped ``_original_call_tool`` arm is 77% of the asserted total (p95
    0.543 ms of 0.706 ms; the wrapper's own share 0.163 ms), and the recorded
    figures for the flake itself are 6.70–8.92 ms on a CLEAN tree (#3678 body,
    ``docs/plans/2026-10-13-8482-mcp-wrap-envelope-error.md``) against a 5 ms
    budget — i.e. it exceeded its own budget with ZERO instrumentation involved.
    On this host at load ~150 the unwrapped baseline ALONE measures 7.6–16.5 ms
    p95 (recorded at ``test_mcp_wait_bound_fast_path_overhead_is_bounded``). A
    guard that cannot tell "telemetry got slower" from "the box was busy" is a
    false-block generator, and its natural workaround — re-run until green —
    teaches everyone to ignore the signal (#3678).

    ⛔ WHAT REPLACES IT, AND WHAT IS *NOT* RE-ADDED. No absolute wall-clock
    budget returns to this file or to any other. The two guards here:

    * ``test_instrumentation_adds_no_scheduling_hop`` — DETERMINISTIC. It
      counts event-loop hops on the PUBLIC ``loop.call_soon`` (the entry point
      every ``Task`` step and ``Future`` callback is scheduled through) and
      pins that the wrapper adds NONE over the wait-bound seam it wraps, and
      that the count does not move with the result payload. Load cannot change
      it: it is a code-path count, not a clock reading.
    * ``test_instrumentation_cost_is_bounded_relative_to_the_seam`` —
      LOAD-ROBUST. Same tool, same dispatch and same seam on BOTH arms,
      position-alternated, asserted on the MEDIAN DELTA, so host contention
      lands on both arms and cancels. The p95 delta is REPORTED in the failure
      message and never asserted (on a shared host it is load-dominated).

    ⛔ RELATION TO THE SIBLING GUARD — a different pair, kept deliberately.
    ``tests/test_transport_wait_bound.py``'s
    ``test_mcp_wait_bound_fast_path_overhead_is_bounded`` bounds
    ``mcp.call_tool`` minus the UNWRAPPED ``_original_call_tool`` — the seam
    PLUS this wrapper, whose own share is invisible inside its 1.5 ms budget.
    The pair here is ``mcp.call_tool`` minus ``_await_under_mcp_wait_bound``:
    the seam is on both arms, so the increment IS this wrapper. Neither
    substitutes for the other.

    ⛔ REJECTED — option 3 of #3678, a dedicated benchmark job. It buys one
    number at the price of a new CI surface, and it is not where MCP overhead
    belongs: ``benchmarks/README.md`` is the #316 RETRIEVAL-latency harness
    (FTS/vector/RRF/TF-IDF vs the 300 ms E2E verdict), so this would be new
    machinery around behaviour that the two guards above already make
    trustworthy.
    """

    pytestmark = pytest.mark.asyncio

    #: Rounds per arm in the relative guard. 150 both arms ⇒ a median that a
    #: single scheduler spike cannot decide; the whole test measures ~0.15 s.
    ROUNDS = 150

    async def test_instrumentation_adds_no_scheduling_hop(self, test_tools,
                                                          monkeypatch):
        """DETERMINISTIC (#3678 AC 2): zero added event-loop hops.

        The wrapper's fast path is CPU-only: it must not insert an ``await``
        that yields to the loop, because each such hop is a full scheduler
        round-trip whose cost belongs to the request, not to telemetry. A
        regression that reached I/O, a thread/executor hop or a network call on
        the per-call path would add one — deterministically, with no timing
        involved, so this never flakes and never needs re-running.

        ``call_soon`` is patched on the RUNNING LOOP INSTANCE (public
        ``asyncio`` API, not ``loop._run_once``): every ``Task.__step`` and
        ``Future`` callback is scheduled through it, so a delivered hop is
        counted whether it comes from our code or a dependency's. The
        assertions are COMPARATIVE (wrapped vs seam-only vs large payload), so
        an incidental change in the absolute count — a new FastMCP internal, an
        asyncio version — moves all three arms together and does not red here.
        """
        from tortoise import mcp_server
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry",
                            lambda *a, **k: None)

        loop = asyncio.get_running_loop()
        original_call_soon = loop.call_soon
        scheduled = [0]

        def _counting_call_soon(*args, **kwargs):
            scheduled[0] += 1
            return original_call_soon(*args, **kwargs)

        monkeypatch.setattr(loop, "call_soon", _counting_call_soon)

        async def _hop_counts(call, rounds):
            seen = set()
            for _ in range(rounds):
                before = scheduled[0]
                await call()
                seen.add(scheduled[0] - before)
            return seen

        seam = await _hop_counts(
            lambda: mcp_server._await_under_mcp_wait_bound(
                "_telemetry_echo", {"message": "x"}, version=None,
                task_meta=None), 10)
        wrapped = await _hop_counts(
            lambda: mcp_server.mcp.call_tool(
                "_telemetry_echo", {"message": "x"}), 10)
        large = await _hop_counts(
            lambda: mcp_server.mcp.call_tool(
                "_telemetry_echo", {"message": "y" * 200_000}), 6)

        for label, seen in (("seam-only", seam), ("wrapped", wrapped),
                            ("wrapped, 200 KB result", large)):
            assert len(seen) == 1, (
                f"the {label} arm scheduled a VARIABLE number of event-loop "
                f"hops {sorted(seen)} over otherwise identical calls — the "
                "dispatch path is not determinate, so a hop count cannot "
                "bound anything here")

        assert wrapped == seam, (
            f"the telemetry wrapper added an event-loop hop of its own: "
            f"wrapped {sorted(wrapped)} hops vs seam-only {sorted(seam)}. The "
            "wrapper's fast path must stay CPU-only — an added hop (I/O, an "
            "executor/thread round-trip, a retry that yields) is a scheduler "
            "round-trip charged to every tool call. #3678 pin (deterministic: "
            "this is a code-path count, not a clock reading)")

        assert large == wrapped, (
            f"the wrapper's scheduling profile scales with the result payload: "
            f"{sorted(large)} hops for a 200 KB result vs {sorted(wrapped)} for "
            "a short one. Per-call instrumenting work must be O(1) in the "
            "payload — this is the shape of a per-call write/encode/parse of "
            "the result that would have to yield. #3678")

    async def test_instrumentation_cost_is_bounded_relative_to_the_seam(
            self, test_tools, monkeypatch):
        """LOAD-ROBUST (#3678 AC 1): the wrapper's incremental MEDIAN cost.

        Both arms dispatch the SAME tool through the SAME wait-bound seam, so
        the increment is the wrapper alone (budget check, scope enforcement,
        read metering, result count, the emitter call) and host load lands on
        BOTH arms instead of masquerading as wrapper cost. The arms alternate
        position so neither owns a fixed slot in the loop.

        The budget is a GROSS-REGRESSION alarm, not a tight one: measured p50
        delta is +0.01 ms (worst |median delta| 0.011 ms over 10 runs of 150
        samples at load 8-9 on this host, 2026-10-13), so 0.5 ms is ~45x the
        signal. The exact guard is the hop count above; this one catches an
        order-of-magnitude CPU regression the hop count cannot see.
        """
        import statistics

        from tortoise import mcp_server
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry",
                            lambda *a, **k: None)

        args = {"message": "x"}
        wrapped: list[float] = []
        seam: list[float] = []

        async def _wrapped():
            return await mcp_server.mcp.call_tool("_telemetry_echo", args)

        async def _seam():
            return await mcp_server._await_under_mcp_wait_bound(
                "_telemetry_echo", args, version=None, task_meta=None)

        for i in range(self.ROUNDS):
            order = (((_wrapped, wrapped), (_seam, seam)) if i % 2 == 0
                     else ((_seam, seam), (_wrapped, wrapped)))
            for call, bucket in order:
                t0 = time.perf_counter()
                await call()
                bucket.append((time.perf_counter() - t0) * 1000)

        wrapped.sort()
        seam.sort()
        w_p50, s_p50 = statistics.median(wrapped), statistics.median(seam)
        w_p95 = wrapped[int(len(wrapped) * 0.95) - 1]
        s_p95 = seam[int(len(seam) * 0.95) - 1]
        delta = w_p50 - s_p50
        assert delta < 0.5, (
            f"the telemetry wrapper added {delta:.3f} ms median per call "
            f"(wrapped {w_p50:.3f} ms, seam-only {s_p50:.3f} ms; p95 delta "
            f"{w_p95 - s_p95:+.3f} ms — REPORTED, never asserted: on a shared "
            "host it is load-dominated). Measured p50 ≈ +0.01 ms when this "
            "budget was set (#3678), so a median this high is a real "
            "incremental regression, not host load: both arms ran the same "
            "dispatch through the same seam, interleaved, so a busy box "
            "cancels out.")


class TestSchedulingGuardFailSafe:
    """#4023: the *scheduling* guards — executor submit and thread start.

    ``TestFailSafe`` above covers the emitter raising and the *writer* raising.
    Neither covers the two SCHEDULING branches of
    ``_emit_mcp_tool_call_telemetry`` — and that is exactly where #4020's real
    incident lived: on the (unmerged) ask emitter a ``Thread.start()`` failure
    escaped a function documented never to raise and turned a pinned 504 into a
    500. The mcp_server site carries the same guards; these tests pin that both
    branches are load-bearing rather than merely present.
    """

    @pytest.mark.asyncio
    async def test_executor_scheduling_failure_is_swallowed(self, monkeypatch):
        """``loop.run_in_executor`` raising must not escape the emitter."""
        from tortoise import mcp_server

        class _Loop:
            def is_closed(self) -> bool:
                return False

            def run_in_executor(self, *a, **k):
                raise RuntimeError("executor submit exploded")

        monkeypatch.setattr(asyncio, "get_running_loop", lambda: _Loop())
        # Documented never to raise — a scheduling failure must be swallowed.
        mcp_server._emit_mcp_tool_call_telemetry(
            "t1", "tortoise_status", "ok", 3, None)

    def test_thread_start_failure_is_swallowed(self, monkeypatch):
        """``Thread.start()`` raising must not escape the emitter (#4020 shape)."""
        from tortoise import mcp_server

        def _no_loop():
            raise RuntimeError("no running event loop")

        # Force the daemon-thread branch: no event loop is running.
        monkeypatch.setattr(asyncio, "get_running_loop", _no_loop)

        def _raising_start(self):
            raise RuntimeError("can't start new thread")

        monkeypatch.setattr(threading.Thread, "start", _raising_start)
        # Documented never to raise — a Thread.start() failure must not escape.
        mcp_server._emit_mcp_tool_call_telemetry(
            "t1", "tortoise_status", "ok", 3, None)
