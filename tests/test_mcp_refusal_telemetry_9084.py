"""#9084 — a tool-LEVEL refusal must not be recorded `status="ok"`.

WHY THIS FILE EXISTS
--------------------
#8135 taught the `mcp_tool_call` classifier to recognise a failure `_safe`
SWALLOWED, by recording the `_SafeError` at construction and matching it by
value at the seam. That covers every refusal produced by `_safe`.

It does not cover the other producer: a tool body that answers a caller-side
refusal with a **plain dict literal** it built itself —

    if mode not in _RECALL_MODES:
        return {"mode": mode, "error": "recall mode ... not recognized"}

Those read ``status="ok"``, ``error_kind=None`` in the very series the #888/
#3834 friction evidence and the activation scorecard are measured from, so a
refused call was indistinguishable from a successful one. Measured on the
pre-fix tree for `tortoise_recall`'s unrecognised mode (the probe #8135's own
body listed and could not classify).

WHY THE FIX IS IN THE PRODUCERS, NOT IN THE SEAM
------------------------------------------------
The seam cannot test ``"error" in payload``: a *successful* write returns the
created node's own property dict, and a user prop may be literally named
``error`` — gating on key presence re-classifies those writes as failures
(#3926, pinned in ``test_mcp_safe_error_telemetry_8135.py``). There is no
property of the PAYLOAD that separates the two, so the tool body — the one
place that knows which dict it is building — has to say so by making the
refusal an `_SafeError`. `_SafeError` is a ``dict`` subclass, so the wire shape
does not move: the caller receives the same JSON as before.

WHAT IS PINNED HERE
-------------------
1. **The class, structurally** — no tool body in `mcp_server.py` returns a
   plain dict literal carrying an ``error`` key. This is the gate that keeps
   the 40+ sites from growing a 41st: a new refusal written the old way fails
   here rather than silently re-opening the defect.
2. **The behaviour, per producer** — the real tools (`tortoise_recall`,
   `tortoise_create_point`, `tortoise_query`, `tortoise_file_decision`,
   `tortoise_graph_set_recording`) answer their refusal with a non-``ok``
   status and a named `error_kind`.
3. **No wire change** — the refusal's payload is byte-identical to the plain
   dict it replaced.
4. **No re-classification of successes** — the #3926 boundary (a success whose
   props carry an ``error`` key) still reads ``ok``, and the "recovered from a
   secondary `_safe` failure" shape still reads ``ok``.

The issue's Indicator 2 (a post-fix count of `mcp_tool_call` rows with
``status="ok"`` whose result carried an error payload, target 0) is the
production measurement of (1) + (2) over real traffic; it needs a live window
and is not assertable here. (1) is its structural equivalent: with no plain-dict
refusal producer left, a refusal cannot reach the series as a success.
"""
from __future__ import annotations

import ast
import contextlib
import pathlib

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

# ── Harness ───────────────────────────────────────────────────────────────

MCP_SERVER = pathlib.Path(ms.__file__)


@pytest.fixture
def events(monkeypatch):
    """Capture emitted rows, and make sure no analytics write leaves the box."""
    captured: list[dict] = []

    def _capture(org_id, tool_name, status, latency_ms, error_kind,
                 result_count=None):
        captured.append({"tool_name": tool_name, "status": status,
                         "error_kind": error_kind,
                         "result_count": result_count})

    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry", _capture)
    return captured


@pytest.fixture
def hosted(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed gate does not pre-empt the
    refusal these tests are about."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


# ── 1. The class, structurally ────────────────────────────────────────────

def _tool_body_dict_returns(tree: ast.Module, tool_names: set[str]):
    """(tool, lineno) of every `return <dict literal with an "error" key>`
    in a registered tool body — `_SafeError({...})` payloads excluded."""
    out = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in tool_names:
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Return) or sub.value is None:
                continue
            value = sub.value
            # `_SafeError({"error": ...})` is the CONVERTED shape — not a hit.
            if (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                    and value.func.id == "_SafeError"):
                continue
            if not isinstance(value, ast.Dict):
                continue
            keys = [k.value for k in value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "error" in keys:
                out.append((node.name, sub.lineno))
    return out


def test_no_tool_body_returns_a_plain_dict_refusal():
    """The gate: a refusal must BE an `_SafeError`, not a dict that looks like
    one (#9084). Without the fix this fails with the 53 measured sites.

    Scope, stated so a green run is not over-read: this is a check on dict
    LITERALS returned from a registered tool body. A refusal built dynamically
    and returned as a name (`return payload`) is not visible here — no such
    producer exists today, and `_safe`'s own failures are already `_SafeError`
    by construction.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    tree = ast.parse(MCP_SERVER.read_text())
    names = {t.name for t in TOOL_REGISTRY}
    hits = _tool_body_dict_returns(tree, names)

    assert hits == [], (
        "a tool body answers a refusal with a plain dict, so the dispatch is "
        "recorded status='ok' (#9084). Wrap the payload in `_SafeError({...})` "
        "— it IS a dict, so the wire shape is unchanged:\n"
        + "\n".join(f"  {MCP_SERVER.name}:{ln}  {tool}" for tool, ln in hits))


# ── 2. The behaviour, per producer ────────────────────────────────────────

@pytest.mark.asyncio
async def test_recall_bad_mode_is_recorded_as_a_failure(events, hosted):
    """#8135's own unclassifiable probe — `tortoise_recall`, unrecognised mode.

    The refusal is a tool-body branch: the SDK is never reached, so there is no
    exception for the ladder to catch and nothing `_safe` produced.
    """
    result = await ms.mcp.call_tool("tortoise_recall", {"query": "q",
                                                        "mode": "bogus"})

    payload = result.structured_content
    assert "not recognized" in payload["error"], payload
    row = events[-1]
    assert row["tool_name"] == "tortoise_recall", row
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row
    # #3675: a refusal returned no collection — UNKNOWN, never 0.
    assert row["result_count"] is None, row


@pytest.mark.asyncio
async def test_create_point_tag_cap_is_recorded_as_a_failure(events, hosted):
    """`ERR_QUOTA`, produced by the tool body's own batch cap (#329)."""
    from tortoise.quota import MAX_TAGS_PER_POINT

    tags = [f"t{i}" for i in range(MAX_TAGS_PER_POINT + 1)]
    result = await ms.mcp.call_tool(
        "tortoise_create_point",
        {"kind": "statement", "content": "x", "props": {"tags": tags}})

    payload = result.structured_content
    assert payload["code"] == ms.ERR_QUOTA, payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_query_bad_paging_is_recorded_as_a_failure(events, hosted):
    """`tortoise_query` is a `list[dict] | dict` tool — a dict is ADMISSIBLE in
    its envelope, so #8482's repair has nothing to act on and the row used to
    read `ok`. This is the union-envelope half of the producer class.

    The refusal arrives under the wrap envelope's ``result`` (measured), so the
    read is through it — the seam's `_returned_safe_failure` reads both levels
    for exactly this reason (#8135 P1).
    """
    result = await ms.mcp.call_tool("tortoise_query", {"page": 0})

    payload = result.structured_content
    refusal = payload.get("result", payload)
    assert "page must be >= 1" in refusal["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_file_decision_option_cap_is_recorded_as_a_failure(events, hosted):
    from tortoise.quota import MAX_FILE_DECISION_OPTIONS

    options = [f"o{i}" for i in range(MAX_FILE_DECISION_OPTIONS + 1)]
    result = await ms.mcp.call_tool(
        "tortoise_file_decision",
        {"options": options, "evidence": ["e"], "choice": 0})

    payload = result.structured_content
    assert payload["code"] == ms.ERR_QUOTA, payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_a_refusal_carrying_an_http_status_still_reads_exec_error(
        events, hosted):
    """A refusal whose payload carries a second, unrelated key.

    `tortoise_graph_set_recording`'s scope branch answers
    ``{"error": ..., "status": 403}``. Pinned separately because the
    classification must follow the `_SafeError` TYPE: a ``status`` field is also
    what a SUCCESS carries on several tools, so a fix that keyed on it would
    re-classify real successes as failures. The org/scope contextvars are set
    here so the SCOPE branch is the one that answers (the earlier branch is the
    hosted-mode refusal, which carries no ``status``).
    """
    tok_org = ms._current_org_id.set("team-9084")
    # graphs:write so the #4170 scope gate admits the call, and NO
    # team:manage so the tool's own scope branch is what answers.
    tok_scopes = ms._current_scopes.set(["graphs:write"])
    try:
        result = await ms.mcp.call_tool("tortoise_graph_set_recording",
                                       {"recording": True})
    finally:
        ms._current_scopes.reset(tok_scopes)
        ms._current_org_id.reset(tok_org)

    payload = result.structured_content
    assert payload["status"] == 403, payload
    assert "team:manage" in payload["error"], payload
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


# ── 3. No wire change ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_refusal_wire_shape_is_the_plain_dict_it_replaced(
        events, hosted):
    """`_SafeError` is a dict subclass: the caller sees the same JSON.

    Stated as an equality against the literal the body used to return, so a
    later refactor that "cleans up" the payload is caught here rather than by a
    customer parsing the wire.
    """
    result = await ms.mcp.call_tool("tortoise_recall", {"query": "q",
                                                        "mode": "bogus"})

    payload = dict(result.structured_content)      # what a client deserializes
    assert type(payload) is dict
    assert payload == {"mode": "bogus",
                       "error": "recall mode 'bogus' not recognized — use "
                                "state|gaps|subgraph|custom."}


@pytest.mark.asyncio
async def test_the_constructed_refusal_is_recorded_even_through_a_spread(
        events, hosted):
    """A MUTATION guard for the mechanism the whole fix rests on.

    `_SafeError.__init__` records the payload for the dispatch in flight, and
    the seam matches it BY VALUE — including when a tool spreads the refusal
    into a wider payload (`tortoise_recall` answers `{mode, **refusal}`). A
    tool body that surfaced only the inner dict would still be caught, so the
    conversion does not depend on each site returning the payload verbatim.
    """
    def spread_tool() -> dict:
        return {"mode": "bogus", **ms._SafeError({"error": "spread refusal"})}

    ms.mcp.add_tool(FunctionTool.from_function(spread_tool, name="_9084_spread",
                                               description="_9084_spread"))
    try:
        await ms.mcp.call_tool("_9084_spread", {})
    finally:
        with contextlib.suppress(Exception):
            ms.mcp.local_provider.remove_tool("_9084_spread")

    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


# ── 4. The false positives that must NOT move ─────────────────────────────

@pytest.mark.asyncio
async def test_a_successful_write_carrying_a_user_error_prop_stays_ok(
        events, hosted, monkeypatch):
    """#3926's boundary, re-measured against a REAL create tool.

    The conversion is at the refusal sites only; a successful write still
    returns the SDK's own props dict (a plain dict), so a user prop named
    ``error`` cannot be mistaken for a refusal.
    """
    class _WriteSDK:
        def create_point(self, *a, **kw):
            return {"id": "pt_1", "error": "a user prop",
                    "pointKind": "statement"}

    monkeypatch.setattr(ms, "_get_org_sdk", _WriteSDK)

    result = await ms.mcp.call_tool(
        "tortoise_create_point",
        {"kind": "statement", "content": "x",
         "props": {"error": "a user prop"}})

    assert result.structured_content["id"] == "pt_1"
    row = events[-1]
    assert row["status"] == "ok", row
    assert row["error_kind"] is None, row
