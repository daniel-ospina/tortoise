"""#3675 — ``result_count`` on ``mcp_tool_call``: the result-bearing prop.

WHY THIS FILE EXISTS
--------------------
The ``mcp_tool_call`` row carried no result information, so ``status == "ok"``
— which means only "the tool did not raise" — was the entire success signal. A
``tortoise_search`` that returned 8 relevant points and one that returned
NOTHING were the same row, which is why the activation scorecard (#3674) could
ship ``recall_attempted`` and not "answered from memory".

These tests pin the three properties the prop is USELESS without:

* it survives the PII allowlist — an unregistered key is silently stripped
  (#501 / #3359 / #3821's documented silent-loss mode), so a prop that looks
  emitted and never arrives is the failure mode;
* ABSENCE and ZERO stay different facts — ``0`` is "the tool ran and returned
  nothing", ``None``/absent is "the count cannot be known";
* nothing but a count travels in it — no retrieved text, no point id.

The allowlist's AST guard (``tests/test_telemetry_registration.py``) already
proves the EMIT SITE's keys are registered; this file proves the VALUE that
reaches the store. Those are different failures and only the second one is a
lost measurement.
"""
from __future__ import annotations

import json

import pytest
from fastmcp.tools import FunctionTool

from tortoise import hosted_api as ha
from tortoise import mcp_server as ms


# ── The prop is registered, and the filter keeps it ───────────────────────

def test_result_count_is_registered_in_the_allowlist():
    """The registration half of the contract. An unregistered prop is dropped
    WITHOUT an error, so this assertion is the only thing standing between the
    emit and a silent strip."""
    assert "result_count" in ha._ALLOWED_ANALYTICS_PROPS


def test_result_count_survives_the_pii_filter_with_a_zero_intact(monkeypatch,
                                                                tmp_path):
    """The measured half. A ``0`` reaching the store is the single most
    important value this prop carries — it is the "recall ran and answered
    nothing" finding — so it must survive the filter AS 0, not be dropped as
    falsy and not be coerced."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    for var in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)

    ha._track_analytics_event("org-1", "mcp_tool_call",
                              {"tool_name": "tortoise_search",
                               "status": "ok", "result_count": 0,
                               "not_registered": "dropped"})
    event = json.loads(fallback.read_text().strip())
    assert event["properties"]["result_count"] == 0, event["properties"]
    assert "not_registered" not in event["properties"], event["properties"]


def test_a_null_result_count_is_not_coerced_to_zero(monkeypatch, tmp_path):
    """``None`` means "the count cannot be known" and must arrive as ``None``.
    A filter that normalised it to 0 would turn every uncountable call into a
    confident "returned nothing" — the exact conflation the prop exists to
    remove."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    for var in ("SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY"):
        monkeypatch.delenv(var, raising=False)
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH", fallback)

    ha._track_analytics_event("org-1", "mcp_tool_call",
                              {"tool_name": "tortoise_add_point",
                               "result_count": None})
    props = json.loads(fallback.read_text().strip())["properties"]
    assert "result_count" in props, props
    assert props["result_count"] is None, props


def test_abstained_is_deliberately_not_registered():
    """#3675's body asks for an ``abstained`` prop alongside ``result_count``,
    for the ``POST /v1/ask`` abstention path (#1987). That path NO LONGER
    EXISTS — ``tortoise_ask`` is eval-only and was removed from the MCP surface,
    the SDK and REST (#3849 / #3929) — so the prop would have no producer and
    could only ever be absent (unreadable as "did not abstain") or a hardcoded
    ``False`` (a fabricated fact).

    Pinned as a REFUSAL rather than an omission: the next lane to read the issue
    will reach for the key, and the honest answer is "register it WITH its
    producer", not "add the name".
    """
    assert "abstained" not in ha._ALLOWED_ANALYTICS_PROPS


# ── The extractor: measured shapes, and every uncountable one ─────────────

class _Result:
    """The part of FastMCP's ``ToolResult`` this seam reads."""

    def __init__(self, structured_content):
        self.structured_content = structured_content


@pytest.mark.parametrize(("payload", "expected"), [
    # FastMCP's x-fastmcp-wrap-result envelope — what a `-> list[dict]` tool
    # actually returns through mcp.call_tool (measured against FastMCP 3.4.6).
    ({"result": [{"a": 1}, {"a": 2}]}, 2),
    ({"result": []}, 0),
    # tortoise_recall's non-subgraph envelope, and its subgraph one.
    ({"mode": "state", "results": [1, 2, 3]}, 3),
    ({"mode": "subgraph", "nodes": [1, 2], "edges": [1, 2, 3],
      "stats": {}}, 2),
    # A bare list payload (a result object that IS the collection).
    ([1, 2, 3, 4], 4),
])
def test_measured_result_shapes(payload, expected):
    assert ms._mcp_result_count(_Result(payload)) == expected


@pytest.mark.parametrize("payload", [
    None,                                        # no structured payload
    {"error": {"code": "timeout"}},              # the wait-bound refusal
    {"id": "pt_123"},                            # a write op
    {"results": "not a list"},                   # shape drift
    {"nodes": {"0": "not a list"}},
    {"total": 7},                                # a count we did not define
    42,                                          # a scalar result
])
def test_uncountable_result_shapes_yield_none(payload):
    """A count is emitted ONLY when the shape makes it knowable. Every shape
    here yields ``None`` — deliberately, instead of a plausible guess: a wrong
    number cannot be told from a right one by the consumer, and the scorecard's
    contract is that a count it reports is exact or refused."""
    assert ms._mcp_result_count(_Result(payload)) is None


def test_a_result_object_without_structured_content_is_not_counted():
    """A tool with no output schema and a non-dict return has no structured
    payload; the seam must not fall back to parsing content blocks (which are
    free text — the one thing that must never reach the props)."""
    class _Bare:
        structured_content = None
        content = ["some retrieved prose that must not be counted or emitted"]

    assert ms._mcp_result_count(_Bare()) is None


# ── The wrapper: the count is emitted per call, through the seam ──────────

@pytest.fixture
def emitted(monkeypatch):
    """Captures the positional args of every ``mcp_tool_call`` emit."""
    calls: list[tuple] = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda *a, **kw: calls.append((a, kw)))
    return calls


def _probe(name: str, fn) -> None:
    ms.mcp.add_tool(FunctionTool.from_function(fn, name=name, description=name))


def _drop(name: str) -> None:
    try:
        ms.mcp.local_provider.remove_tool(name)
    except Exception:  # noqa: BLE001 — cleanup must never mask the assertion
        pass


@pytest.mark.asyncio
async def test_wrapped_call_tool_emits_the_items_returned(emitted):
    def _two_points() -> list[dict]:
        return [{"id": "p1"}, {"id": "p2"}]

    _probe("_result_count_two", _two_points)
    try:
        await ms.mcp.call_tool("_result_count_two", {})
    finally:
        _drop("_result_count_two")
    assert len(emitted) == 1, emitted
    args, _kw = emitted[0]
    # (org_id, tool_name, status, latency_ms, error_kind, result_count)
    assert args[1] == "_result_count_two", args
    assert args[2] == "ok", args
    assert args[5] == 2, args


@pytest.mark.asyncio
async def test_wrapped_call_tool_emits_zero_for_an_empty_result(emitted):
    """THE core #3675 requirement: zero is EMITTED as ``0``, not omitted. A
    consumer reads ``0`` as the finding and the absence of the key as a
    different fact (see the scorecard's ``result_count_not_recorded``)."""
    def _nothing() -> list[dict]:
        return []

    _probe("_result_count_zero", _nothing)
    try:
        await ms.mcp.call_tool("_result_count_zero", {})
    finally:
        _drop("_result_count_zero")
    args, _kw = emitted[0]
    assert args[2] == "ok", args
    assert args[5] == 0, args
    assert args[5] is not None, args


@pytest.mark.asyncio
async def test_wrapped_call_tool_emits_none_for_an_uncountable_result(emitted):
    """A write op returns a scalar/mapping with no item collection, so the
    count is unknowable and must be ``None`` — never 0, which would assert the
    op "returned nothing"."""
    def _created() -> dict:
        return {"id": "pt_1", "status": "created"}

    _probe("_result_count_uncountable", _created)
    try:
        await ms.mcp.call_tool("_result_count_uncountable", {})
    finally:
        _drop("_result_count_uncountable")
    args, _kw = emitted[0]
    assert args[5] is None, args
    assert args[5] != 0, args


@pytest.mark.asyncio
async def test_only_a_count_travels_in_the_props_never_content(emitted):
    """AC: "No retrieved content, point id, or free text enters the props."
    The retrieved text and the point ids are distinctive strings, so a leak of
    ANY kind (into this prop or another) is visible as a substring of the
    emitted row."""
    secret_text = "zzz-supersecret-retrieved-content-zzz"
    secret_id = "zzz-point-id-zzz"

    def _content() -> list[dict]:
        return [{"id": secret_id, "content": secret_text},
                {"id": secret_id + "2", "content": secret_text}]

    _probe("_result_count_no_leak", _content)
    try:
        await ms.mcp.call_tool("_result_count_no_leak", {})
    finally:
        _drop("_result_count_no_leak")
    args, kw = emitted[0]
    blob = json.dumps({"args": args, "kwargs": kw})
    assert secret_text not in blob, blob
    assert secret_id not in blob, blob
    assert args[5] == 2, args
