"""Tests for MCP tool-call telemetry (#889) — friction evidence for epic #888.

Covers the issue's verification checklist:
- event emitted per call (exactly one, incl. the middleware re-dispatch guard)
- all 4 status categories produced (ok / validation_error / auth_error / exec_error)
- validation vs exec error classification (pydantic → validation_error with
  '<error_type>:<field>' kind; everything else → exec_error with class name)
- latency present and measured around the tool execution
- analytics failure never breaks a tool call (fail-safe emitter)
- <5ms p95 overhead added by the wrapper on the tool-call path

Runs against the shared module-level mcp instance with tiny test-only tools
registered per-test and removed afterwards (same pattern as test_tool_registry).
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp.tools import FunctionTool
from tortoise.mcp_auth import _current_team_id, _transport_mode

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
    _current_team_id.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)


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
        try:
            mcp.local_provider.remove_tool(name)
        except Exception:
            pass


@pytest.fixture
def captured_events(monkeypatch):
    """Swap the emitter for a synchronous recorder (deterministic, no I/O)."""
    from tortoise import mcp_server
    events = []

    def _capture(team_id, tool_name, status, latency_ms, error_kind):
        events.append({"team_id": team_id, "tool_name": tool_name,
                       "status": status, "latency_ms": latency_ms,
                       "error_kind": error_kind})

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
        assert isinstance(ev["latency_ms"], int) and ev["latency_ms"] >= 0
        # Unauthenticated path (stdio, no team context) → empty team_id.
        assert ev["team_id"] == ""

    async def test_team_id_resolved_from_auth_context(self, test_tools,
                                                      captured_events):
        from tortoise.mcp_server import mcp
        _current_team_id.set("team_abc")
        await mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert captured_events[0]["team_id"] == "team_abc"

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
        with pytest.raises(Exception):
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
        assert ev["team_id"] == ""

    async def test_exec_error_status(self, test_tools, captured_events):
        """Raised tool body error → exec_error with the CAUSE class name."""
        from tortoise.mcp_server import mcp
        with pytest.raises(Exception):
            await mcp.call_tool("_telemetry_boom")
        ev = captured_events[0]
        assert ev["status"] == "exec_error"
        assert ev["error_kind"] == "RuntimeError"  # unwrapped from ToolError


class TestClassification:
    """Requirement 3 (unit): validation vs exec vs auth classification."""

    def test_pydantic_validation_error_direct(self):
        from tortoise.mcp_server import _classify_mcp_call_error
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
        from tortoise.mcp_server import _classify_mcp_call_error
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
        from tortoise.mcp_server import _classify_mcp_call_error
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
        from tortoise.mcp_server import _classify_mcp_call_error
        from fastmcp.exceptions import AuthorizationError
        status, kind = _classify_mcp_call_error(AuthorizationError("nope"))
        assert status == "auth_error"
        assert kind == "AuthorizationError"

    def test_not_found_is_exec_error(self):
        from tortoise.mcp_server import _classify_mcp_call_error
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
        def _boom(team_id, tool_name, status, latency_ms, error_kind):
            raise RuntimeError("telemetry exploded")
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", _boom)
        result = await mcp_server.mcp.call_tool("_telemetry_echo", {"message": "hi"})
        assert result.structured_content == {"echo": "hi"}

    async def test_emitter_exception_does_not_mask_tool_error(self, test_tools,
                                                              monkeypatch):
        from tortoise import mcp_server
        def _boom(team_id, tool_name, status, latency_ms, error_kind):
            raise RuntimeError("telemetry exploded")
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry", _boom)
        with pytest.raises(Exception) as ei:
            await mcp_server.mcp.call_tool("_telemetry_boom")
        assert "telemetry boom" in str(ei.value)

    async def test_writer_failure_is_swallowed(self, monkeypatch, tmp_path):
        """_track_analytics_event raising must not propagate from the emitter."""
        from tortoise import mcp_server, hosted_api

        def _broken(team_id, event_name, properties=None):
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
    latency_ms/error_kind must survive the filter).
    """

    pytestmark = pytest.mark.asyncio


    async def test_event_lands_via_track_analytics_event(self, monkeypatch,
                                                         tmp_path):
        from tortoise import mcp_server, hosted_api
        # Force the local JSONL fallback (no Supabase configured).
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        fallback = tmp_path / "analytics_fallback.jsonl"
        monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH", str(fallback))

        mcp_server._emit_mcp_tool_call_telemetry(
            "team_x", "tortoise_create_point", "validation_error", 7,
            "missing:content")
        await mcp_server._flush_mcp_telemetry()

        assert fallback.exists()
        event = json.loads(fallback.read_text().strip().splitlines()[0])
        assert event["team_id"] == "team_x"
        assert event["event_name"] == "mcp_tool_call"
        props = event["properties"]
        assert props["tool_name"] == "tortoise_create_point"
        assert props["status"] == "validation_error"
        assert props["latency_ms"] == 7
        assert props["error_kind"] == "missing:content"


class TestOverhead:
    """Requirement 5: <5ms p95 wrapper overhead on the tool-call path.

    With the emitter stubbed to a no-op, the measured call includes the
    wrapper's own cost (perf_counter, dict build, branch) plus the tool body —
    the tool body is a trivial echo, so p95 of the total approximates the
    wrapper+dispatch overhead budget.
    """

    pytestmark = pytest.mark.asyncio


    async def test_p95_under_5ms(self, test_tools, monkeypatch):
        from tortoise import mcp_server
        monkeypatch.setattr(mcp_server, "_emit_mcp_tool_call_telemetry",
                            lambda *a, **k: None)
        durations = []
        for _ in range(300):
            t0 = time.perf_counter()
            await mcp_server.mcp.call_tool("_telemetry_echo", {"message": "x"})
            durations.append((time.perf_counter() - t0) * 1000)
        durations.sort()
        p95 = durations[int(len(durations) * 0.95) - 1]
        assert p95 < 5.0, f"p95 latency {p95:.3f}ms exceeded the 5ms budget"
