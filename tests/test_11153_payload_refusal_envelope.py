"""#11153 — a refused tool call must not report itself as a success.

WHY THIS FILE EXISTS
--------------------
`_safe` answers every SDK failure with a ``_SafeError`` dict instead of raising, and
FastMCP then ships that dict as the *success* payload of a dict-annotated tool:

    {"content": [{"type": "text", "text": "{\"error\": \"Team points limit reached …\"}"}],
     "structuredContent": {"error": "Team points limit reached (25000)…", "code": -32006},
     "isError": false}

Measured on the production workspace (#11153, re-measured 2026-11-13): the write did
not happen, and nothing in the envelope said so. `isError` is the only failure signal
uniform across MCP clients, so every client that keys on it — including the fleet's own
graph CLI, which then exits 0 — recorded a REFUSED WRITE as a success.

#8482 built the repair for the tools whose declared schema forbids the error dict (the
wrap-envelope tools): the failure moved to ``is_error`` and the payload was dropped. It
deliberately left dict-annotated tools alone, because there the payload *is* schema-valid
and the message stays readable. That reasoning is about the PAYLOAD. The VERDICT was
never the payload's business — object tools kept ``isError: false`` on a refusal.

The fix pinned here flips ONLY the verdict:

* a payload the seam has PROVEN to be a `_safe` failure ships with ``is_error=True``;
* ``structured_content`` is kept BYTE-IDENTICAL, so every reader that already parses
  ``payload["error"]`` keeps working (the #8482 drop is not extended to these tools);
* a SUCCESSFUL dict tool is untouched, including one whose user props carry a key
  literally named ``error`` (#3926 — key presence is not failure);
* a tool that RECOVERED from a secondary `_safe` failure stays ``ok`` (#8135 — the
  payload it returned is not the failed call's).
"""
from __future__ import annotations

import contextlib

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

#: The quota refusal exactly as #11153 measured it on the hosted surface.
QUOTA_REFUSAL = ("Team points limit reached (25000). Upgrade your plan to increase it. "
                 "Need more? Contact support@premiselabs.co.")
QUOTA_CODE = -32006


@pytest.fixture
def quiet(monkeypatch):
    """No analytics sink: these tests must not reach the network."""
    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry", lambda *a, **k: None)


@pytest.fixture
def http_mode(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed auth gate does not pre-empt the
    tool body these tests are about."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


def _probe(name: str, fn) -> None:
    ms.mcp.add_tool(FunctionTool.from_function(fn, name=name, description=name))


def _drop(name: str) -> None:
    with contextlib.suppress(Exception):
        ms.mcp.local_provider.remove_tool(name)


def _text(result) -> str:
    return "".join(getattr(b, "text", "") for b in (result.content or ()))


# ── the defect ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_payload_carried_refusal_sets_is_error(quiet, http_mode):
    """THE contract #11153 asks for: a dict-annotated write that `_safe` refused
    tells the client it failed.

    On the unfixed tree this reads `is_error is False` — the false PASS.
    """
    def _refused_write() -> dict:
        return ms._SafeError({"error": QUOTA_REFUSAL, "code": QUOTA_CODE})

    _probe("_11153_refused_write", _refused_write)
    try:
        result = await ms.mcp.call_tool("_11153_refused_write", {})
    finally:
        _drop("_11153_refused_write")

    assert result.is_error is True, (
        "a refused write shipped `isError: false` — the client records a success "
        "for a write that never happened (#11153)")
    assert QUOTA_REFUSAL in _text(result), (
        "the refusal message must survive the verdict flip — a client that reads "
        "the text (the fleet CLI does) must still see WHY it was refused")


@pytest.mark.asyncio
async def test_the_payload_is_kept_byte_identical_for_object_tools(quiet, http_mode):
    """Only the VERDICT changes. A dict-annotated tool's error payload is already
    schema-valid, so it is NOT dropped the way #8482 drops an invalid envelope —
    readers that parse `payload["error"]` keep working."""
    def _refused_write() -> dict:
        return ms._SafeError({"error": QUOTA_REFUSAL, "code": QUOTA_CODE})

    _probe("_11153_payload_intact", _refused_write)
    try:
        result = await ms.mcp.call_tool("_11153_payload_intact", {})
    finally:
        _drop("_11153_payload_intact")

    assert result.structured_content == {"error": QUOTA_REFUSAL, "code": QUOTA_CODE}, (
        "the machine-readable half of the refusal must still reach the client — "
        "flipping the verdict must not cost the retry/policy code")


# ── the guards: this must not invent failures ─────────────────────────────


@pytest.mark.asyncio
async def test_a_successful_dict_tool_is_untouched(quiet, http_mode):
    def _write() -> dict:
        return {"id": "p1", "created": True}

    _probe("_11153_ok_write", _write)
    try:
        result = await ms.mcp.call_tool("_11153_ok_write", {})
    finally:
        _drop("_11153_ok_write")

    assert result.is_error is False
    assert result.structured_content == {"id": "p1", "created": True}


@pytest.mark.asyncio
async def test_a_successful_write_carrying_a_user_prop_named_error_is_not_a_failure(
        quiet, http_mode):
    """#3926's hazard, restated for this arm: key presence is not failure.

    A successful write may legitimately return a user property called ``error``.
    The discriminator is the recorded `_safe` failure, never the key.
    """
    def _write_with_error_prop() -> dict:
        return {"id": "p2", "error": "this is a USER PROPERTY, not a failure"}

    _probe("_11153_user_prop", _write_with_error_prop)
    try:
        result = await ms.mcp.call_tool("_11153_user_prop", {})
    finally:
        _drop("_11153_user_prop")

    assert result.is_error is False, (
        "a successful write whose props carry a key named `error` was reported as "
        "a failure — that is #3926 coming back through the new arm")
    assert result.structured_content["error"] == "this is a USER PROPERTY, not a failure"


@pytest.mark.asyncio
async def test_a_tool_that_recovered_from_a_secondary_safe_failure_stays_ok(
        quiet, http_mode):
    """#8135's identity rule, restated: a dispatch that ANSWERED is `ok`.

    The body calls `_safe` (it fails), then returns a DIFFERENT successful value.
    Only a payload that CARRIES the recorded failure may be re-verdict'ed.
    """
    def _recovered() -> dict:
        ms._safe(lambda: (_ for _ in ()).throw(RuntimeError("secondary leg failed")))
        return {"id": "p3", "recovered": True}

    _probe("_11153_recovered", _recovered)
    try:
        result = await ms.mcp.call_tool("_11153_recovered", {})
    finally:
        _drop("_11153_recovered")

    assert result.is_error is False, (
        "a recovered dispatch was reported as a failure — the arm must match the "
        "payload the tool RETURNED, not 'did any _safe call fail'")
    assert result.structured_content == {"id": "p3", "recovered": True}


# ── the in-repo consumer audit (checkbox 3 of the issue) ──────────────────


def _curate_graph_module(suffix: str):
    """Load `tools/curate_graph.py` as a module (it is a script, not a package)."""
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "tools" / "curate_graph.py"
    spec = importlib.util.spec_from_file_location(f"curate_graph_{suffix}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"curate_graph_{suffix}"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_curation_reader_turns_a_client_refusal_into_a_curation_refusal(
        monkeypatch):
    """`tools/curate_graph.py` reads the surface through `mcp_client`, and since
    #11153 the surface flags a refusal with `isError: true`.

    ⚠️ That is a behaviour change for THIS consumer, found by a review round:
    fastmcp's client runs with `raise_on_error=True`, so it raises `ToolError`
    BEFORE `C.normalise_tool_result` can see the payload — the refusal stopped
    arriving as data at all. Without a translation the census reader died on an
    uncaught client traceback instead of its documented `CurationRefused` (rc 3,
    with the offset/row diagnostics that make a partial census visible).
    """
    from fastmcp.exceptions import ToolError

    from tortoise import curation as C
    from tortoise import mcp_client

    mod = _curate_graph_module("11153")

    def _refuse(*_a, **_k):
        raise ToolError("Team points limit reached (25000)")

    monkeypatch.setattr(mcp_client, "call_tool", _refuse)
    with pytest.raises(C.CurationRefused) as caught:
        mod._live_call_tool("tortoise_query", {"offset": 0})
    assert "points limit" in str(caught.value), (
        "the refusal's own message must survive the translation — an operator "
        "needs to know WHICH refusal ended the census")


def test_an_unrelated_transport_error_is_not_relabelled_as_a_refusal(monkeypatch):
    """The translation must be narrow: a transport death is not a curation
    refusal, and mislabelling it would send an operator to re-read a graph that
    was never reached."""
    from tortoise import mcp_client

    mod = _curate_graph_module("11153_transport")

    def _boom(*_a, **_k):
        raise ConnectionError("daemon unreachable")

    monkeypatch.setattr(mcp_client, "call_tool", _boom)
    with pytest.raises(ConnectionError):
        mod._live_call_tool("tortoise_query", {})


def test_a_refused_census_still_says_which_offset_it_died_at():
    """The diagnostic the false PASS used to hide must survive the new shape.

    #11153 moved the refusal from DATA (`is_error=False`, handled by `_error_of`)
    to a CLIENT RAISE, which happens one frame BELOW `read_points` — so the
    offset/row diagnostic `_error_of`'s arm produces has to be re-raised on the
    raise path too, or an operator is told only "points limit reached" and cannot
    tell a first-page refusal from a mid-walk one (review round 2, measured).
    """
    from tortoise import curation as C

    mod = _curate_graph_module("11153_offset")

    calls = {"n": 0}

    def _refuse_on_page_two(*_a, **_k):
        calls["n"] += 1
        if calls["n"] > 1:
            raise C.CurationRefused("Team points limit reached (25000)")
        return {"points": [{"id": f"p{i}", "content": f"c{i}"} for i in range(2)],
                "total": 6}

    reader = mod.MCPReader(_refuse_on_page_two)
    with pytest.raises(RuntimeError) as caught:
        reader.read_points(page=2, verify=False)
    message = str(caught.value)
    assert "offset 2" in message, message
    assert "after 2 rows" in message, message
    assert "points limit" in message, message
    assert "partial census" in message, message
