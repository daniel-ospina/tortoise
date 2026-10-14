"""#8482 — a wrap-envelope tool must not ship a payload its own schema forbids.

WHY THIS FILE EXISTS
--------------------
`tortoise_search` and `tortoise_expand_relationships` failed on the serving
build with::

    MCP error -32602: Structured content does not match the tool's output
    schema: data/result must be array

The issue could not identify the discriminator: `tortoise_check_structure`,
`tortoise_packs_list`, `tortoise_list_batches` and `tortoise_list_namespaces`
all return arrays and all WORKED. It is **not** "returns a list". It is whether
the underlying SDK call **RAISED**: `_safe` turns an exception into a dict, and
FastMCP's wrap envelope (`x-fastmcp-wrap-result`, built from a `-> list[dict]`
annotation) then carries `{"result": {"error": ...}}` where the advertised
schema requires an array. A conforming client validates `structuredContent`
against `outputSchema` and refuses the WHOLE call, so the SDK's real message is
destroyed and replaced by a schema complaint about a payload nobody can read.

These tests pin the properties the fix must have, in the order the issue asked
for them:

* the second factor is the RAISE, not the annotation (two tools, one
  annotation, different outcomes);
* the failure is delivered as an ERROR result a client accepts — no
  `structuredContent` at all — with the real message intact;
* a healthy wrap tool's envelope is byte-identical (the schema is NOT relaxed);
* an object-annotated tool's `_safe` error dict is UNCHANGED (the established
  contract, which works);
* an `anyOf` envelope (`tortoise_query`, `tortoise_delete_entity`) is untouched
  — a dict `result` is VALID there;
* the second trigger of the same class — the wait-bound refusal, whose payload
  has no `result` key at all — is repaired too;
* the raw tool FUNCTION still returns the `_safe` error dict: the repair lives
  at the MCP wire seam, not in the SDK-facing contract.
"""
from __future__ import annotations

import contextlib
import gc
from unittest import mock

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

# ── Harness ───────────────────────────────────────────────────────────────

@pytest.fixture
def quiet(monkeypatch):
    """No analytics sink: these tests must not reach the network."""
    monkeypatch.setattr(ms, "_emit_mcp_tool_call_telemetry", lambda *a, **k: None)


@pytest.fixture
def stdio(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed gate does not pre-empt the
    SDK call these tests are about (unset mode → the auth refusal)."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


def _probe(name: str, fn):
    ms.mcp.add_tool(FunctionTool.from_function(fn, name=name, description=name))


def _drop(name: str) -> None:
    # Cleanup must never mask the assertion — hence the blanket suppression.
    with contextlib.suppress(Exception):
        ms.mcp.local_provider.remove_tool(name)


def _wire_payload(result) -> dict:
    """The exact JSON-RPC fields a client validates, as the TS SDK sees them.

    Mirrors `client.callToolStream`: when `structuredContent` is present it is
    validated against the advertised `outputSchema` REGARDLESS of `isError`;
    when it is absent, `isError` must be true or the client refuses the result
    as "has an output schema but did not return structured content".
    """
    return {"structuredContent": result.structured_content,
            "isError": result.is_error,
            "text": "".join(getattr(b, "text", "") for b in (result.content or ()))}


_DECLARED_TYPE_CHECKS = {"array": list, "object": dict, "string": str,
                        "boolean": bool}


def _schema_validates_against(payload, output_schema) -> bool:
    """The client's rule — for envelope rows AND for non-wrap rows.

    Two shapes reach a conforming client, and both are checked here:

    * a **wrap** tool ships ``{"result": …}`` (FastMCP's
      ``x-fastmcp-wrap-result`` envelope), so the declared contract is the
      schema's ``properties.result``;
    * a **non-wrap** tool ships its result ITSELF as ``structuredContent``
      (no ``result`` property), so the declared top-level ``type`` IS the
      contract. The sweep used to return ``True`` unconditionally for these
      rows — a tautology for the 69 object-annotated tools, which the
      sweep's own prose claimed were validated.

    ⚠️ SCOPE, stated rather than implied: only a *declared* type is checked.
    A union row (``anyOf``), a schema that declares no top-level ``type``,
    and violations of nested ``items``/``properties`` rules are accepted by
    construction — this helper is the envelope guard's mirror, not a general
    JSON-Schema validator.
    """
    structured = payload["structuredContent"]
    if structured is None:
        return payload["isError"]          # absent payload is allowed on an error
    if not output_schema:
        # No advertised schema → a conforming client has no validator for this
        # tool and cannot refuse the payload. Several registry tools are in
        # this form, so the sweep must not read it as a violation.
        return True
    prop = (output_schema.get("properties") or {}).get("result")
    if prop is None:
        # Non-wrap tool: the payload IS the value the client validates, so its
        # declared top-level type is the whole contract.
        declared = output_schema.get("type")
        if not isinstance(declared, str):
            return True                    # undeclared: not our call
        expected = _DECLARED_TYPE_CHECKS.get(declared)
        return expected is None or isinstance(structured, expected)
    declared = prop.get("type") if isinstance(prop, dict) else None
    if not isinstance(declared, str):
        return True                        # anyOf / undeclared: not our call
    expected = _DECLARED_TYPE_CHECKS.get(declared)
    return expected is None or isinstance(structured.get("result"), expected)


async def _registered_schema(name: str):
    return (await ms.mcp.get_tool(name)).output_schema


def _envelope_result_type(schema: dict) -> str | None:
    """The JSON-schema `type` an envelope declares for its `result` key.

    A guard-LOCAL classifier (moved out of the production module: the seam
    reads the same contract through ``_violates_wrap_envelope``, and a
    production function with no production caller was flagged in review). It
    buckets the sweep into typed vs union from the schema the sweep already
    fetched. A union envelope declares `anyOf` (or nothing) and returns None.
    """
    prop = (schema.get("properties") or {}).get("result") or {}
    declared = prop.get("type") if isinstance(prop, dict) else None
    return declared if isinstance(declared, str) else None


# ── The second factor: the RAISE, not the annotation ──────────────────────

@pytest.mark.asyncio
async def test_two_tools_one_annotation_the_raise_is_the_discriminator(quiet):
    """The exact confusion the issue could not resolve.

    `_healthy` and `_failing` carry the SAME `-> list[dict]` annotation and the
    same advertised schema. Only the one whose body RAISES (i.e. whose `_safe`
    result is a dict) produced -32602 — so the discriminator is the failure,
    never the shape of the declared return.
    """
    def _healthy() -> list[dict]:
        return [{"id": "p1"}]

    def _failing() -> list[dict]:
        return ms._SafeError({"error": "Type mismatch: expected Null or "
                                       "VectorF32 but was List"})

    _probe("_8482_healthy", _healthy)
    _probe("_8482_failing", _failing)
    try:
        ok = await ms.mcp.call_tool("_8482_healthy", {})
        bad = await ms.mcp.call_tool("_8482_failing", {})

        assert ok.structured_content == {"result": [{"id": "p1"}]}
        assert ok.is_error is False

        assert bad.is_error is True, "the client must be told this failed"
        assert bad.structured_content is None, (
            "the invalid envelope must not reach the wire: a client validates "
            "structuredContent even when isError is true")
        assert "VectorF32" in _wire_payload(bad)["text"], (
            "the SDK's real message must survive — that is the whole point")

        bad_schema = await _registered_schema("_8482_failing")
        assert _schema_validates_against(_wire_payload(bad), bad_schema), (
            "the repaired result must be one the client ACCEPTS")
    finally:
        _drop("_8482_healthy")
        _drop("_8482_failing")


@pytest.mark.asyncio
async def test_the_envelope_is_untouched_when_the_tool_succeeds(quiet):
    """No schema relaxation: a healthy wrap tool's payload is byte-identical."""
    def _two() -> list[dict]:
        return [{"id": "a"}, {"id": "b"}]

    _probe("_8482_two", _two)
    try:
        result = await ms.mcp.call_tool("_8482_two", {})
    finally:
        _drop("_8482_two")
    assert result.structured_content == {"result": [{"id": "a"}, {"id": "b"}]}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_an_object_tool_keeps_its_structured_error(quiet):
    """The established `_safe` contract for dict-returning tools WORKS (the
    advertised schema is an object), so it must not be swept up by the fix."""
    def _mapping() -> dict:
        return ms._SafeError({"error": "boom"})

    _probe("_8482_mapping", _mapping)
    try:
        result = await ms.mcp.call_tool("_8482_mapping", {})
    finally:
        _drop("_8482_mapping")
    assert result.structured_content == {"error": "boom"}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_an_anyof_envelope_is_left_alone(quiet):
    """`tortoise_query` / `tortoise_delete_entity` advertise `anyOf`, where a
    dict `result` is VALID — repairing them would break a working surface."""
    def _union() -> list[dict] | dict:
        return {"error": "page must be >= 1 (1-based), got 0"}

    _probe("_8482_union", _union)
    try:
        result = await ms.mcp.call_tool("_8482_union", {})
    finally:
        _drop("_8482_union")
    # The envelope is kept EXACTLY as FastMCP built it — a dict `result` is one
    # of the types this schema admits.
    assert result.structured_content == {
        "result": {"error": "page must be >= 1 (1-based), got 0"}}
    assert result.is_error is False


# ── The second trigger: the wait-bound refusal ────────────────────────────

@pytest.mark.asyncio
async def test_the_wait_bound_refusal_is_repaired_for_a_wrap_tool(quiet,
                                                                 monkeypatch):
    """Same class, second producer: the refusal payload is `{"error": {...}}`
    with no `result` key, which a wrap schema rejects just as hard. It must
    still carry its wait-bound marker so the timeout classification is not
    lost, and the retry delay it advertises must still be READABLE."""
    from tortoise.mcp_auth import (
        _TRANSPORT_WAIT_BOUND_MESSAGE,
        _TRANSPORT_WAIT_RETRY_AFTER_S,
        ERR_TIMEOUT,
    )

    refusal = ms.ToolResult(
        content=_TRANSPORT_WAIT_BOUND_MESSAGE,
        structured_content={"error": {"code": ERR_TIMEOUT,
                                      "message": _TRANSPORT_WAIT_BOUND_MESSAGE,
                                      "data": {"retry_after":
                                               _TRANSPORT_WAIT_RETRY_AFTER_S}}},
        meta={ms._WAIT_BOUND_META_KEY: True},
        is_error=True,
    )

    async def _refuse(*a, **k):
        return refusal

    monkeypatch.setattr(ms, "_await_under_mcp_wait_bound", _refuse)
    result = await ms.mcp.call_tool("tortoise_search", {"query": "q"})
    assert result.is_error is True
    assert result.structured_content is None
    assert _TRANSPORT_WAIT_BOUND_MESSAGE in _wire_payload(result)["text"]
    assert result.meta[ms._WAIT_BOUND_META_KEY] is True, (
        "dropping the envelope must not drop the wait-bound marker — it is "
        "what classifies the call as a timeout rather than a failure")
    # The refusal's prose is digit-free BY DESIGN (`mcp_auth`: the advertised
    # delay has exactly one source, and that source is this payload), so a
    # repair that dropped it would tell the caller to wait for a delay it never
    # advertises. It must be re-homed where the client still reads it.
    rehomed = result.meta[ms._SCHEMA_ERROR_META_KEY]
    assert rehomed["code"] == ERR_TIMEOUT
    assert rehomed["data"]["retry_after"] == _TRANSPORT_WAIT_RETRY_AFTER_S
    # `fastmcp.wrap_result` described a payload that is no longer there.
    assert "fastmcp" not in (result.meta or {})


# ── The real tool, and the contract the repair must NOT change ────────────

@pytest.mark.asyncio
async def test_the_real_search_tool_is_repaired_when_its_sdk_raises(quiet,
                                                                    stdio):
    """End-to-end on the tool the issue named. The SDK raise is what the issue's
    reproduction hit (`Query timed out` / the #8316 `List` type mismatch); the
    seam must translate it into an error the client can READ."""
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError(
                "Type mismatch: expected Null or VectorF32 but was List")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        result = await ms.mcp.call_tool("tortoise_search",
                                        {"query": "positioning", "limit": 10})
    assert result.is_error is True
    assert result.structured_content is None
    assert "VectorF32" in _wire_payload(result)["text"]
    assert _schema_validates_against(
        _wire_payload(result),
        await _registered_schema("tortoise_search"))


def test_the_raw_tool_function_still_returns_the_safe_error_dict(quiet, stdio):
    """The repair is a WIRE-seam repair. `_safe`'s function-level contract —
    an error dict, not an exception — is unchanged (`test_mcp_server.py` relies
    on it), so the MCP fix cannot silently become an SDK-facing one."""
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("boom")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        raw = ms.tortoise_search("positioning")
    assert isinstance(raw, ms._SafeError)
    assert raw["error"] == "boom"


# ── The surface guard: EVERY registry tool, not the two the issue named ───

@pytest.mark.asyncio
async def test_no_registry_tool_can_deliver_a_payload_its_schema_rejects(
        quiet, monkeypatch):
    """The class guard, over all 82 registry tools — the #8482 invariant.

    The issue could only name two tools and explicitly asked for a repo-wide
    sweep of declared-vs-returned shape. A refusal is forced for EVERY registry
    tool at the dispatch seam (so no per-tool arguments are needed) and the
    result a conforming client would receive is checked against the tool's OWN
    advertised schema. Before the fix every array-envelope tool failed this;
    after it, none can.

    It also pins the retry signal across the whole surface: the wait-bound
    refusal's delay must be findable whichever channel the schema permits
    (structured payload for an object tool, the re-homed `_meta` for a wrap
    tool). A repair that made a call legible but dropped the delay would fail
    here for all 11 typed tools at once.
    """
    from tortoise.mcp_auth import _TRANSPORT_WAIT_BOUND_MESSAGE, ERR_TIMEOUT
    from tortoise.tool_registry import TOOL_REGISTRY

    retry_after = 5
    refusal = ms.ToolResult(
        content=_TRANSPORT_WAIT_BOUND_MESSAGE,
        structured_content={"error": {"code": ERR_TIMEOUT,
                                      "message": _TRANSPORT_WAIT_BOUND_MESSAGE,
                                      "data": {"retry_after": retry_after}}},
        meta={ms._WAIT_BOUND_META_KEY: True},
        is_error=True,
    )

    async def _refuse(*a, **k):
        return refusal

    monkeypatch.setattr(ms, "_await_under_mcp_wait_bound", _refuse)

    repaired = delivered = 0
    for entry in TOOL_REGISTRY:
        schema = await _registered_schema(entry.name)
        result = await ms.mcp.call_tool(entry.name, {})
        wire = _wire_payload(result)
        assert result.is_error is True, entry.name
        assert _schema_validates_against(wire, schema), (
            f"{entry.name}: a conforming client would reject this payload "
            "against the schema the tool itself advertises")
        if isinstance(schema, dict) and schema.get("x-fastmcp-wrap-result"):
            repaired += 1
            assert wire["structuredContent"] is None, entry.name
        else:
            delivered += 1
        assert _TRANSPORT_WAIT_BOUND_MESSAGE in wire["text"], entry.name
        # The retry delay, on whichever channel this tool's schema permits.
        carried = (getattr(result, "meta", None) or {}).get(
            ms._SCHEMA_ERROR_META_KEY) or (wire["structuredContent"] or {}).get(
                "error") or {}
        assert carried.get("data", {}).get("retry_after") == retry_after, (
            f"{entry.name}: the refusal tells the caller to wait for an "
            "advertised delay — that delay must survive the repair")

    # Shrinkage is the dangerous direction: the sweep must keep measuring the
    # surface it was written for. Growth (a new envelope tool) is fine and is
    # covered by the loop itself.
    assert repaired >= 11 and delivered >= 60, (
        f"sweep saw {repaired} wrap + {delivered} object tools; measured at "
        "11 + 71 when this guard was written")


def test_the_sweep_helper_can_reject_a_non_wrap_payload():
    """Non-vacuity pin for the NON-wrap rows of the sweep.

    The helper used to return ``True`` for every schema with no declared
    ``result`` property, so no object-annotated row could ever fail it — even
    though the sweep's prose claimed those rows were validated against their
    own schema. A non-wrap tool's payload IS the value the client validates,
    so its declared top-level type is now checked. This pins that the check
    can fail: a LIST is not an ``object``.
    """
    schema = {"type": "object", "additionalProperties": True}
    accepted = {"structuredContent": {"status": "ok"}, "isError": False,
                "text": ""}
    assert _schema_validates_against(accepted, schema) is True
    # The exact tautology first: a DICT payload against a non-object top-level
    # type. Pre-fix the helper had no `result` property to read and returned
    # True here; it must now refuse.
    assert _schema_validates_against(
        {"structuredContent": {"status": "ok"}, "isError": False,
         "text": ""}, {"type": "array"}) is False
    # And the realistic shape the claim names: a list is not an `object`.
    refused = {"structuredContent": ["not", "an", "object"],
               "isError": False, "text": ""}
    assert _schema_validates_against(refused, schema) is False, (
        "the helper must be able to reject a payload a conforming client "
        "would refuse against the tool's own declared top-level type")


@pytest.mark.asyncio
async def test_a_safe_failure_is_repaired_without_rehoming_meta(quiet, stdio):
    """The RECORDED contract for a `_safe` failure: no `_meta.tortoise_error`.

    `_safe` returns a MAPPING with machine-readable fields (``code``,
    ``violations``, ``batch_id``), but inside a wrap envelope that mapping sits
    under ``result``, not at the payload's top level — and the re-home reads
    ``payload["error"]``. So the mapping is NOT re-homed on ``_meta``; it is
    dropped from ``structured_content`` and survives VERBATIM as the JSON text
    in ``content``. `config/surface-manifest.yml` records exactly this, so it
    is pinned here rather than left asserted only in prose.
    """
    code = ms.ERR_QUOTA

    def _quota() -> list[dict]:
        return ms._SafeError({"error": "quota exceeded", "code": code})

    _probe("_8482_quota", _quota)
    try:
        result = await ms.mcp.call_tool("_8482_quota", {})
    finally:
        _drop("_8482_quota")

    assert result.is_error is True
    assert result.structured_content is None
    assert ms._SCHEMA_ERROR_META_KEY not in (result.meta or {}), (
        "a `_safe` failure must not claim the `_meta` re-home the manifest "
        "scopes to the transport wait-bound refusal alone")
    text = _wire_payload(result)["text"]
    assert "quota exceeded" in text and str(code) in text, (
        "the `_safe` mapping must survive verbatim in `content` — that is the "
        "channel the record points a reader at")


@pytest.mark.asyncio
async def test_a_wrapped_scalar_is_repaired_too():
    """A wrap envelope whose `result` is a SCALAR is the same escape.

    `_safe` returns the SDK value unchanged on success, so a tool whose SDK
    answers with the wrong shape (the non-exception sibling of the raise this
    issue is about) ships `{"result": <scalar>}` — admitted by neither an
    array nor a union envelope, and refused by the client just as hard. The
    pre-filter must schedule the schema read for it; a dict-only pre-filter
    would have let it through.
    """
    def _scalar() -> list[dict]:
        return "boom"  # type: ignore[return-value]

    _probe("_8482_scalar", _scalar)
    try:
        result = await ms.mcp.call_tool("_8482_scalar", {})
        schema = await _registered_schema("_8482_scalar")
    finally:
        _drop("_8482_scalar")
    assert result.is_error is True
    assert result.structured_content is None
    assert _schema_validates_against(_wire_payload(result), schema)


@pytest.mark.asyncio
async def test_a_repaired_failure_is_not_recorded_as_ok(monkeypatch, stdio):
    """The wire and the scorecard must agree about the same call.

    The repair PROVES the dispatch failed and tells the client so
    (`is_error`), so leaving the `mcp_tool_call` row `ok` would re-create the
    exact "a failure that reads as a success" defect this issue is about — and
    it would be newly *created* by the repair rather than merely inherited
    (#8135 owns the general `_SafeError` case).
    """
    events: list[dict] = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, kind, count=None:
        events.append({"status": status, "error_kind": kind}))

    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("boom")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        result = await ms.mcp.call_tool("tortoise_search", {"query": "q"})
    assert result.is_error is True
    assert events and events[0]["status"] == "exec_error"
    assert events[0]["error_kind"] == "safe_error"


@pytest.mark.asyncio
async def test_every_wrap_envelope_tool_cannot_satisfy_a_failed_payload():
    """The CLASS guard: every wrap-envelope tool, not the two the issue named.

    #8482 asked for a repo-wide sweep of declared-vs-returned shape. This walks
    the REAL registry and asserts the seam RECOGNISES both failure shapes for
    every tool FastMCP gave an envelope — and that a `result` a UNION envelope
    legitimately admits is left alone. Had this existed, it would have caught
    the defect for all 13 affected tools at once instead of two.
    """
    from tortoise.tool_registry import TOOL_REGISTRY

    #: A `result` the declared schema ACCEPTS, built FROM that schema (its own
    #: `type`, and for an array its `items.type`) rather than hardcoded: a fixed
    #: `[{"id": "p1"}]` is NOT valid for, say, `tortoise_list_graphs`
    #: (`items: {type: string}`), so a "must pass untouched" assertion against it
    #: would be about a payload that is not a success shape at all.
    def _accepted(prop):
        kind = prop.get("type")
        if kind != "array":
            return {"object": {"id": "p1"}, "string": "s", "boolean": True,
                    "number": 1.0, "integer": 1}.get(kind)
        element = {"string": "s", "object": {"id": "p1"}, "number": 1.0,
                   "integer": 1, "boolean": True}.get(
                       (prop.get("items") or {}).get("type"))
        return [] if element is None else [element]

    #: (1) `_safe`'s error dict lands INSIDE the envelope.
    _SAFE_FAILURE = {"result": {"error": "boom"}}
    #: (2) the wait-bound refusal carries no `result` at all — and `result` is
    #: REQUIRED, so this one is unreadable even to a union envelope.
    _WAIT_BOUND = {"error": {"code": -32001, "message": "x", "data": {}}}

    typed = unions = 0
    for entry in TOOL_REGISTRY:
        schema = await _registered_schema(entry.name)
        if not isinstance(schema, dict) or not schema.get("x-fastmcp-wrap-result"):
            continue
        assert ms._violates_wrap_envelope(schema, _WAIT_BOUND), (
            f"{entry.name}: a payload with no `result` is invalid against an "
            "envelope that requires one")
        kind = _envelope_result_type(schema)
        if kind is None:
            unions += 1
            assert not ms._violates_wrap_envelope(schema, _SAFE_FAILURE), (
                f"{entry.name}: a UNION envelope admits a dict `result` — "
                "repairing it would break a working surface")
            continue
        typed += 1
        assert ms._violates_wrap_envelope(schema, _SAFE_FAILURE), entry.name
        assert not ms._violates_wrap_envelope(
            schema, {"result": _accepted(
                (schema.get("properties") or {}).get("result") or {})}), (
            f"{entry.name}: a successful `{kind}` payload must pass untouched")
        assert not ms._violates_wrap_envelope(schema, None), (
            f"{entry.name}: FastMCP's own no-payload form is out of scope")
    # Shrinkage is the dangerous direction — the sweep must keep measuring the
    # surface it was written for. Growth (a new envelope tool) is fine and is
    # covered by the loop itself.
    assert typed >= 11 and unions >= 2, (
        f"sweep found {typed} typed + {unions} union envelopes; measured at "
        "11 + 2 when this guard was written")


@pytest.mark.asyncio
async def test_the_named_read_path_siblings(quiet, stdio):
    """The siblings the issue named — and the one it asked about that is fine.

    `tortoise_search` and `tortoise_expand_relationships` carry an ARRAY
    envelope and are repaired. `tortoise_recall` is NOT in that class: it is
    annotated `-> dict`, so it has no wrap envelope and `_safe`'s flat dict is
    exactly what its advertised OBJECT schema admits. It must be left as it is
    — which is the proof this repair is driven by the DECLARED SCHEMA and not
    by "the call failed".

    Both repaired tools returning a client-ACCEPTABLE error is the point: the
    pre-fix payload was refused by the client as a schema violation, so the
    caller could not read the failure at all.
    """
    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("boom-search")

        def expand_relationships(self, *a, **kw):
            raise RuntimeError("boom-expand")

        def recall_state(self, *a, **kw):
            raise RuntimeError("boom-recall")

    calls = {"tortoise_search": {"query": "q"},
             "tortoise_expand_relationships": {"point_id": "p1"},
             "tortoise_recall": {"query": "q"}}
    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        for name, args in calls.items():
            schema = await _registered_schema(name)
            result = await ms.mcp.call_tool(name, args)
            if schema.get("x-fastmcp-wrap-result"):
                assert result.is_error is True, name
                assert result.structured_content is None, name
                assert "boom" in _wire_payload(result)["text"], name
                assert _schema_validates_against(
                    _wire_payload(result), schema), name
            else:
                # Recall: no envelope, so `_safe`'s dict IS the advertised
                # shape — untouched, and NOT an error result.
                assert result.is_error is False, name
                assert result.structured_content == {
                    "mode": "state", "error": "boom-recall"}, name


@pytest.mark.asyncio
async def test_real_mcp_round_trip_delivers_the_sdk_message(quiet, stdio):
    """The issue's reproduction, end to end through a REAL MCP session.

    The -32602 in the report is raised by the CLIENT validating
    `structuredContent` against the advertised `outputSchema`, so reading
    `mcp.call_tool`'s ToolResult cannot establish it. This test speaks real
    JSON-RPC to the server (in-memory transport) and asserts on what a client
    actually receives: no `structuredContent`, `isError` set, and the SDK's own
    message intact — where before the fix the whole call was replaced by a
    schema complaint about a payload nobody could read.
    """
    from fastmcp import Client
    from fastmcp.exceptions import ToolError

    class _RaisingSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError(
                "Type mismatch: expected Null or VectorF32 but was List")

    with mock.patch.object(ms, "_get_org_sdk", _RaisingSDK):
        async with Client(ms.mcp) as client:
            advertised = next(t for t in await client.list_tools()
                              if t.name == "tortoise_search")
            result = await client.call_tool(
                "tortoise_search", {"query": "positioning", "limit": 10},
                raise_on_error=False)
            with pytest.raises(ToolError) as raised:
                await client.call_tool(
                    "tortoise_search", {"query": "positioning", "limit": 10})

    assert result.is_error is True
    assert result.structured_content is None
    assert "VectorF32" in _wire_payload(result)["text"]
    # The message reaches the caller's error channel instead of being replaced
    # by the schema complaint the issue reported.
    assert "VectorF32" in str(raised.value)
    # ...and the advertised schema is UNCHANGED: this is not a relaxation.
    assert advertised.outputSchema["properties"]["result"]["type"] == "array"


@pytest.mark.asyncio
async def test_the_auth_gate_is_classified_through_a_wrap_envelope(monkeypatch):
    """The stdio auth gate, read through the envelope it gets wrapped in.

    The gate's refusal is a dict, so for a `-> list[dict]` tool FastMCP wraps
    it and a TOP-LEVEL `payload.get("error")` probe misses it — the call was
    recorded as `ok` while it had failed. That is the same "a failure that
    reads as a success" class as #8482, and the reason the defect could go
    unnoticed (#8135). The repair also makes the refusal legible, where it used
    to arrive as a -32602 about the envelope.
    """
    events: list[dict] = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, kind, count=None:
        events.append({"status": status, "error_kind": kind}))
    # Deterministic gate: `is_dev_mode()` reads TORTOISE_API_KEY at call time.
    monkeypatch.setattr(ms, "_is_dev_mode", lambda: False)
    token = ms._transport_mode.set("stdio")
    try:
        result = await ms.mcp.call_tool("tortoise_search", {"query": "q"})
    finally:
        ms._transport_mode.reset(token)

    assert events and events[0]["status"] == "auth_error"
    assert events[0]["error_kind"] == "stdio_auth_gate"
    assert result.is_error is True
    assert result.structured_content is None
    assert "Authentication required" in _wire_payload(result)["text"]


@pytest.mark.asyncio
async def test_a_hosted_tenant_refusal_is_not_labelled_the_stdio_gate(monkeypatch):
    """The `auth_error` STATUS is right; the #236 KIND is not the producer.

    `tortoise_packs_list` refuses on its OWN fail-closed branch when the HTTP
    transport carries no tenant context — a hosted guard that never touches the
    stdio gate (``tortoise/mcp_server.py``, the ``_current_org_id is None and
    _transport_mode == "http"`` return). Its text shares the gate's
    ``Authentication required`` prefix, so a prefix-only classifier claims it
    for #236 and the metric no longer isolates the gate. The kind must follow
    the TRANSPORT that refused.
    """
    events: list[dict] = []
    monkeypatch.setattr(
        ms, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, kind, count=None:
        events.append({"status": status, "error_kind": kind}))
    token = ms._transport_mode.set("http")
    org = ms._current_org_id.set(None)
    try:
        result = await ms.mcp.call_tool("tortoise_packs_list", {})
    finally:
        ms._current_org_id.reset(org)
        ms._transport_mode.reset(token)

    assert events and events[0]["status"] == "auth_error", events
    assert events[0]["error_kind"] is None, (
        "an HTTP tenant-context refusal is not the #236 stdio gate: "
        f"got {events[0]['error_kind']!r}")
    # ...and the refusal is still legible to the client (the #8482 repair).
    assert result.is_error is True
    assert result.structured_content is None
    assert "Authentication required" in _wire_payload(result)["text"]


# ── The two review findings that pin cost and readability ──────────────────

@pytest.mark.asyncio
async def test_a_union_envelope_is_classified_once_not_every_success(quiet):
    """A union tool's legitimate non-list success must not be re-classified
    (via a full `mcp.get_tool` rebuild) on every dispatch.

    `tortoise_delete_entity` returns a bool and `tortoise_query` returns
    `{results, total, hasMore}` on the paginated path — both non-list, so both
    reach `_maybe_wrap_envelope_violation` on EVERY success and would resolve
    the schema each time. `_wrap_envelope_schema` memoises the classification
    per stored tool, so the rebuild is paid once per tool.

    Non-vacuity is proven in-process: the same 20 dispatches are then measured
    with the memo NEUTRALISED (`_local_tool_cache_key` forced to ``None``), and
    the count must double. Before this memo existed the count was the
    neutralised one for every dispatch.
    """
    def _union() -> list[dict] | dict:
        return {"results": [], "total": 0, "hasMore": False}

    _probe("_8482_union_memo", _union)

    async def _run(*, neutralise: bool) -> int:
        ms._WRAP_ENVELOPE_SCHEMA_CACHE.clear()
        seen: list[str] = []
        real_get_tool = ms.mcp.get_tool

        async def _counting(name, *a, **k):
            seen.append(name)
            return await real_get_tool(name, *a, **k)

        patches = [mock.patch.object(ms.mcp, "get_tool", _counting)]
        if neutralise:
            patches.append(mock.patch.object(
                ms, "_local_tool_cache_key", lambda name: None))
        for p in patches:
            p.start()
        try:
            for _ in range(20):
                result = await ms.mcp.call_tool("_8482_union_memo", {})
                assert result.is_error is False
                assert result.structured_content == {
                    "result": {"results": [], "total": 0, "hasMore": False}}
        finally:
            for p in reversed(patches):
                p.stop()
        return len(seen)

    try:
        cached = await _run(neutralise=False)
        uncached = await _run(neutralise=True)
    finally:
        _drop("_8482_union_memo")

    # The neutralised arm is FastMCP's own dispatch lookup PLUS the seam's read
    # on every call (2 × 20). The memoised arm keeps only the dispatch lookups
    # plus the ONE classification the cache now holds.
    assert uncached == 40, (
        f"neutralised arm measured {uncached} get_tool calls over 20 union "
        "dispatches; expected 20 dispatch + 20 seam")
    assert cached == 21, (
        f"the union success re-resolved the schema per dispatch: {cached} "
        "get_tool calls (21 = 20 FastMCP dispatch lookups + 1 classification)")


@pytest.mark.asyncio
async def test_a_replaced_component_is_not_served_from_the_memo(quiet):
    """A name re-registered in place must not answer from the memo.

    ``FastMCPAdapter.register_all`` skips a duplicate, but FastMCP's own
    ``add_tool`` REPLACES on a duplicate name. The memo is keyed on the stored
    component, so a replacement must miss rather than answer with the replaced
    schema's classification — otherwise the reverse of this PR's bug comes
    back: a union tool's legitimate dict success would be judged against a
    stale ``array`` envelope and repaired into a visible failure.

    Non-vacuity: the memo is cleared and primed with the ARRAY classification
    before the replacement registers; reverting ``_local_tool_cache_key`` to
    the pre-fix ``component.key`` form (dropping the ``id`` half) makes the
    second call answer from that entry and ``is_error`` flip to True. The
    address-recycling half of the same property is pinned deterministically in
    :func:`test_a_removed_and_re_added_component_is_not_served_from_the_memo`.
    """
    def _array() -> list[dict]:
        return [{"id": "p1"}]

    def _union() -> list[dict] | dict:
        return {"results": [], "total": 0, "hasMore": False}

    _probe("_8482_stale", _array)
    try:
        ms._WRAP_ENVELOPE_SCHEMA_CACHE.clear()
        # Prime the memo with the ARRAY classification. A list success is
        # short-circuited by the pre-filter and never reaches the schema read,
        # so prime the classifier directly — it is the unit that memoises.
        assert await ms._wrap_envelope_schema("_8482_stale") is not None
        assert ms._WRAP_ENVELOPE_SCHEMA_CACHE, "priming cached nothing"

        # Same name, different envelope shape.
        _probe("_8482_stale", _union)

        result = await ms.mcp.call_tool("_8482_stale", {})
        assert result.is_error is False, (
            "a replaced component was served from the memo: the union tool's "
            "legitimate dict success was repaired against the stale array "
            "classification")
        assert result.structured_content == {
            "result": {"results": [], "total": 0, "hasMore": False}}
    finally:
        ms._WRAP_ENVELOPE_SCHEMA_CACHE.clear()
        _drop("_8482_stale")


@pytest.mark.asyncio
async def test_a_removed_and_re_added_component_is_not_served_from_the_memo(quiet):
    """A key built from an ``id`` is sound only while something holds the object.

    ``remove_tool`` drops the component, so CPython can reuse its address for
    a later component under the same key — the memo would then answer with the
    replaced schema and repair a legitimate success into a visible failure,
    which is the reverse of this PR's bug. The cache VALUE holds the component
    itself, which pins that identity. The first assertion is the pin and is
    deterministic; the rest drives the path it protects.
    """
    def _array() -> list[dict]:
        return [{"id": "p1"}]

    def _union() -> list[dict] | dict:
        return {"results": [], "total": 0, "hasMore": False}

    _probe("_8482_readd", _array)
    try:
        ms._WRAP_ENVELOPE_SCHEMA_CACHE.clear()
        assert await ms._wrap_envelope_schema("_8482_readd") is not None
        live = ms._local_tool_component("_8482_readd")
        pinned = next(iter(ms._WRAP_ENVELOPE_SCHEMA_CACHE.values()))[0]
        assert pinned is live, (
            "the memo key embeds the component's address but the entry does "
            "not hold the component, so that address can be recycled by a "
            "later component and the key collides")

        _drop("_8482_readd")
        gc.collect()
        _probe("_8482_readd", _union)

        result = await ms.mcp.call_tool("_8482_readd", {})
        assert result.is_error is False, (
            "a removed-and-re-added component was served from the memo: a "
            "union tool's legitimate dict success was repaired")
        assert result.structured_content == {
            "result": {"results": [], "total": 0, "hasMore": False}}
    finally:
        ms._WRAP_ENVELOPE_SCHEMA_CACHE.clear()
        _drop("_8482_readd")


@pytest.mark.asyncio
async def test_a_repaired_error_is_never_message_less(quiet):
    """A `-> list[dict]` tool that returns `None` ships `{"result": None}` —
    a schema violation with NO text block. The repair used to rewrite it to
    `is_error=True` with `content=[]`: an error the caller cannot read, which
    is exactly the unreadable degradation #8482 exists to prevent. When the
    kept content is empty the repair must SYNTHESISE a message naming the tool
    and the violated schema.

    No typed registry tool has this return path today (AST-checked across all
    11) — the test pins the class, not a live producer.
    """
    def _none() -> list[dict]:
        return None  # type: ignore[return-value]

    _probe("_8482_none", _none)
    try:
        result = await ms.mcp.call_tool("_8482_none", {})
        schema = await _registered_schema("_8482_none")
    finally:
        _drop("_8482_none")

    assert result.is_error is True
    assert result.structured_content is None
    assert _schema_validates_against(_wire_payload(result), schema)
    text = _wire_payload(result)["text"]
    assert text.strip(), "a repaired error must never be message-less"
    assert "_8482_none" in text, "the message must name the tool"
    assert "array" in text, (
        "the message must name the schema violation it repaired")
