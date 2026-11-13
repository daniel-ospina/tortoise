"""#12364 — an MCP wrapper must declare the type its SDK method actually returns.

The defect this file pins
-------------------------
Two live MCP tools could not return ANY result. Every call failed with:

    Error calling tool '<name>': structured_content must be a dict or None.
    Got list: []. Tools should wrap non-dict values based on their output_schema.

FastMCP derives a tool's ``output_schema`` from the **wrapper's** return
annotation. When the wrapper said ``-> dict`` and the SDK method returned a
``list``, FastMCP built an ``{"type": "object"}`` schema and then refused the
list it was handed, while building the result — *after* ``_safe()`` had already
returned. So ``_safe()`` could not help, ``isError`` was True for every call, and
the failure was **not** data-dependent: it happened on an EMPTY queue.

Both instances were shipped dead:
``tortoise_belief_timeline`` (#12364) and ``tortoise_list_dedup_candidates``
(found by the AREA B template in #11081 — the same root, so it is fixed here
rather than filed as a peer).

What is pinned here
-------------------
* **the class**: for every ``ToolDefinition`` that names an ``sdk_method``, if
  the SDK method is declared to return a ``list``, the wrapper's FastMCP-derived
  ``output_schema`` MUST be the ``x-fastmcp-wrap-result`` envelope. This is the
  guard — the next ``-> dict`` typed over a list-returning SDK method fails in
  CI instead of in a client.
* **the two named regressions**: a real MCP round-trip (``mcp.call_tool``, not a
  direct call of the wrapper — a direct call returns the list happily and the
  defect is invisible) returns a result with ``structured_content`` and no
  ``isError``, including for the empty queue/list.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import textwrap
import typing

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastmcp.tools import FunctionTool

import tortoise.mcp_server as ms
from tortoise import mcp_auth, tool_registry
from tortoise.sdk import TortoiseSDK

# ── helpers ───────────────────────────────────────────────────────────────


def _registry_pairs() -> list[tuple[str, str]]:
    """(tool name, sdk_method) for every registry row that names one."""
    pairs = []
    for row in tool_registry.TOOL_REGISTRY:
        sdk_method = getattr(row, "sdk_method", None)
        if sdk_method:
            pairs.append((row.name, sdk_method))
    assert len(pairs) > 50, f"registry scan found only {len(pairs)} rows — the scan is broken"
    return pairs


def _wrapper(name: str):
    fn = getattr(ms, name, None)
    assert fn is not None, (
        f"{name} is in TOOL_REGISTRY but there is no function of that name in "
        f"mcp_server — the registry and the surface disagree")
    return fn


def _output_schema(fn) -> dict:
    tool = FunctionTool.from_function(fn, name=fn.__name__, description="probe")
    return tool.output_schema or {}


def _annotation_kind(ann) -> str | None:
    """'list' | 'dict' | None, read from the RAW annotation.

    Never through ``typing.get_type_hints``: the SDK modules use ``from
    __future__ import annotations``, so evaluation resolves every forward
    reference in the signature and raises on any annotation imported only under
    ``TYPE_CHECKING`` (measured: ``NameError: name 'datetime' is not defined``).
    The container kind is legible in the un-evaluated text.
    """
    if ann is inspect.Signature.empty or ann is None:
        return None
    if isinstance(ann, str):
        compact = ann.replace(" ", "")
        if compact.startswith(("list[", "List[", "Sequence[", "tuple[", "set[")) \
                or compact in ("list", "List"):
            return "list"
        if "dict" in compact or "Mapping" in compact or compact == "Any":
            return "dict" if compact != "Any" else None
        return None
    origin = typing.get_origin(ann)
    if origin in (list, set, tuple) or ann is list:
        return "list"
    if origin is dict or ann is dict:
        return "dict"
    return None


def _passthrough_method(name: str) -> str | None:
    """The SDK method a wrapper RETURNS UNCHANGED, or None.

    Only a pass-through wrapper has to declare its SDK method's type. A wrapper
    that reshapes the result is entitled to a different annotation — measured:
    ``tortoise_recall`` returns a ``dict`` (``{"mode", "results"}``) although
    ``TortoiseSDK.recall_state`` returns a list, and it is correct. Recognising
    the pass-through by its AST keeps the guard from demanding a rewrite of
    every reshaping tool.
    """
    try:
        src = textwrap.dedent(inspect.getsource(getattr(ms, name)))
    except (OSError, TypeError):
        return None
    body = [n for n in ast.parse(src).body[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return None
    val = body[0].value
    if not (isinstance(val, ast.Call) and getattr(val.func, "id", None) == "_safe" and val.args):
        return None
    first = val.args[0]
    if (isinstance(first, ast.Attribute)
            and isinstance(first.value, ast.Call)
            and getattr(first.value.func, "id", None) == "_get_org_sdk"):
        return first.attr
    return None


# ── 1. THE GUARD: a list-returning SDK method needs the wrap envelope ─────


def test_every_passthrough_wrapper_declares_its_sdk_methods_type():
    """THE guard this issue asks for. A pass-through wrapper annotated `-> dict`
    over an SDK method declared `-> list[dict]` produces an object schema over a
    list value, and FastMCP refuses that value — the tool is dead, silently, on
    every call."""
    offenders = []
    checked = 0
    for name, sdk_method in _registry_pairs():
        if not hasattr(TortoiseSDK, sdk_method):
            continue  # a row whose method is not on the SDK is another test's business
        passed_through = _passthrough_method(name)
        if passed_through is None:
            continue  # a reshaping wrapper is entitled to its own annotation
        checked += 1
        wrapper_kind = _annotation_kind(
            inspect.signature(_wrapper(name)).return_annotation)
        sdk_kind = _annotation_kind(
            inspect.signature(getattr(TortoiseSDK, passed_through)).return_annotation)
        if wrapper_kind != sdk_kind:
            offenders.append(
                f"{name}: wrapper declares {wrapper_kind!r} but "
                f"TortoiseSDK.{passed_through} returns {sdk_kind!r}")
    assert checked >= 20, (
        f"only {checked} pass-through wrappers were found — the scan is not "
        f"reaching the registry, so a clean result here would be vacuous")
    assert not offenders, (
        "these pass-through MCP tools declare a return type their SDK method "
        "contradicts, so FastMCP refuses their own result and every call fails:\n  "
        + "\n  ".join(offenders))


def test_the_two_named_tools_declare_a_shape_fastmcp_accepts():
    """The named regressions, asserted at the contract level: each tool is on
    the surface, its SDK method returns a LIST, and the wrapper advertises a
    shape a client can actually read — an object envelope, never an object
    annotation over the raw list (which FastMCP refuses)."""
    for name, sdk_method in (("tortoise_belief_timeline", "belief_timeline"),
                             ("tortoise_list_dedup_candidates", "list_dedup_candidates")):
        assert callable(getattr(ms, name, None)), f"{name} is not on the MCP surface"
        sdk_kind = _annotation_kind(
            inspect.signature(getattr(TortoiseSDK, sdk_method)).return_annotation)
        assert sdk_kind == "list", (
            f"TortoiseSDK.{sdk_method} no longer returns a list — this test's "
            f"premise changed; re-derive the guard instead of deleting it")
        wrapper_kind = _annotation_kind(
            inspect.signature(getattr(ms, name)).return_annotation)
        assert wrapper_kind == "dict", (
            f"{name} declares {wrapper_kind!r} over a list-returning SDK method; "
            f"either envelop the rows (-> dict) or annotate -> list[dict]")
        schema = _output_schema(getattr(ms, name))
        assert schema.get("type") == "object"


# ── 2. THE ROUND-TRIP: a real call_tool must not be refused ───────────────


class _StubSDK:
    """The SDK as both wrappers see it: a method that returns a list."""

    def belief_timeline(self, topic, limit=50):
        return []

    def list_dedup_candidates(self, candidate_type="content", limit=50):
        return []


@pytest.fixture()
def stubbed(monkeypatch):
    monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: _StubSDK())
    token = mcp_auth._transport_mode.set("http")
    try:
        yield
    finally:
        mcp_auth._transport_mode.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args", [
    ("tortoise_belief_timeline", {"topic": "Helios"}),
    ("tortoise_list_dedup_candidates", {}),
])
async def test_an_empty_list_result_round_trips_through_call_tool(name, args, stubbed):
    """The acceptance test #12364 asks for: a REAL MCP round-trip. The wrapper
    call alone succeeds on the broken tree, which is how both tools shipped."""
    result = await ms.mcp.call_tool(name, args)

    assert not result.is_error, (
        f"{name} was refused: "
        + "\n".join(getattr(b, "text", "") for b in (result.content or [])))
    assert result.structured_content is not None, (
        f"{name} returned no structured_content — the client cannot unwrap it")
    assert result.structured_content["result"] == []


@pytest.mark.asyncio
async def test_a_non_empty_list_result_keeps_its_rows(stubbed):
    """The fix must not just satisfy the empty case: the rows a caller wants
    have to survive the envelope."""
    class _Rows(_StubSDK):
        def list_dedup_candidates(self, candidate_type="content", limit=50):
            return [{"id": "c1", "content": "a", "candidate_type": candidate_type}]

    ms._get_org_sdk = lambda *a, **k: _Rows()
    try:
        result = await ms.mcp.call_tool("tortoise_list_dedup_candidates", {})
    finally:
        ms._get_org_sdk = lambda *a, **k: _StubSDK()

    assert not result.is_error
    assert result.structured_content["result"] == [
        {"id": "c1", "content": "a", "candidate_type": "content"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,raising", [
    ("tortoise_belief_timeline", {"topic": "Helios"}, "belief_timeline"),
    ("tortoise_list_dedup_candidates", {}, "list_dedup_candidates"),
])
async def test_a_safe_failure_is_not_demoted_into_the_success_payload(
        name, args, raising, monkeypatch):
    """The envelope must not swallow `_safe`'s failure dict.

    Measured on a mutant that deletes the `isinstance(rows, _SafeError)` branch
    (mutation probe, this file unchanged): the call still returns 5 passed, and
    a raising SDK arrives as `structured_content == {"result": {"error": "db
    down"}}` with `is_error=False` — a failure delivered in the SUCCESS channel,
    which is the #8482 sin. It is invisible to the #8482 repair seam too: these
    two tools carry no `x-fastmcp-wrap-result` envelope, so
    `_maybe_wrap_envelope_violation` never fires on them. This is the test that
    branch was missing.
    """
    class _Boom:
        """A stub whose ONE method raises, so `_safe` answers with its error dict."""

        def __init__(self):
            def _raise(*a, **k):
                raise RuntimeError("db down")
            setattr(self, raising, _raise)

    monkeypatch.setattr(ms, "_get_org_sdk", lambda *a, **k: _Boom())
    token = mcp_auth._transport_mode.set("http")
    try:
        result = await ms.mcp.call_tool(name, args)
    finally:
        mcp_auth._transport_mode.reset(token)

    payload = result.structured_content
    assert payload is not None
    assert "result" not in payload, (
        f"{name} enveloped a _safe failure: {payload!r} — the error is now inside "
        f"the success payload instead of on _safe's channel")
    assert payload.get("error"), f"{name} lost the failure message: {payload!r}"
    # `_safe` answers a raising SDK with its own error dict; the SDK's message survives.
    assert "db down" in str(payload), payload
