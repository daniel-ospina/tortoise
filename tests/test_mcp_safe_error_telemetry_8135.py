"""#8135 — a payload-carried `_safe` failure must not be recorded `status="ok"`.

WHY THIS FILE EXISTS
--------------------
`_safe` does not raise: an SDK exception becomes an `_SafeError` (a dict
subclass) that the tool RETURNS. The `mcp_tool_call` classifier in
``_wrapped_call_tool`` is exception-driven, so a swallowed failure reached the
emitter with ``status="ok"``, ``error_kind=None`` — measured on the pre-fix
tree for a quota rejection and for an unrecognised recall mode. The series is
where the #888/#3834 friction evidence and the activation scorecard's stage 4/5
are measured, so a failed retrieval was indistinguishable from a successful one
there.

The tests below pin the four payload classes the issue asked to be sampled, and
— more importantly — the two FALSE POSITIVES a cheaper fix would have created:

* a SUCCESSFUL write whose result carries a user prop literally named ``error``
  (#3926's discriminator) must stay ``ok``;
* a dispatch that RECOVERED from a secondary `_safe` failure
  (``tortoise_suggest_entry_points``' exact shape) must stay ``ok``.

The discriminator cannot be the `_SafeError` type at the seam — FastMCP
serializes the payload through ``pydantic_core.to_jsonable_python``, so the
dict that arrives is a plain dict (``test_the_type_is_gone_at_the_seam`` pins
that measurement, because it is the premise the whole fix rests on).
"""
from __future__ import annotations

import contextlib

import pytest
from fastmcp.tools import FunctionTool

from tortoise import mcp_server as ms

# ── Harness ───────────────────────────────────────────────────────────────

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
def stdio(monkeypatch):
    """A transport mode, so `_safe`'s fail-closed gate does not pre-empt the
    SDK call these tests are about."""
    token = ms._transport_mode.set("http")
    yield
    ms._transport_mode.reset(token)


def _probe(name: str, fn) -> None:
    ms.mcp.add_tool(FunctionTool.from_function(fn, name=name, description=name))


def _drop(name: str) -> None:
    with contextlib.suppress(Exception):
        ms.mcp.local_provider.remove_tool(name)


async def _call(name: str, fn, arguments: dict | None = None):
    """Register a probe tool, call it through the REAL dispatch seam, drop it."""
    _probe(name, fn)
    try:
        return await ms.mcp.call_tool(name, arguments or {})
    finally:
        _drop(name)


class _QuotaSDK:
    def query(self, *a, **kw):
        raise _quota_error()


class _BundleSDK:
    def create_point(self, *a, **kw):
        raise _bundle_error()


class _GenericSDK:
    def query(self, *a, **kw):
        raise RuntimeError("connection reset by peer")


def _quota_error():
    from tortoise.quota import QuotaExceededError
    return QuotaExceededError("points quota exceeded")


def _bundle_error():
    from tortoise.exceptions import BundleValidationError
    return BundleValidationError(
        [{"section": "points", "index": 0, "message": "missing endpoint"}])


# ── The four sampled payload classes (issue Indicator 1) ──────────────────

@pytest.mark.asyncio
async def test_quota_rejection_is_recorded_as_a_failure(events, stdio):
    """``ERR_QUOTA`` — an object-annotated tool, whose dict the schema ACCEPTS.

    This is the case #8482's envelope repair cannot reach: the payload is
    schema-legal, so nothing downstream marks it, and pre-fix it read ``ok``.
    """
    def quota_tool() -> dict:
        return ms._safe(_QuotaSDK().query, "statement")

    result = await _call("_8135_quota", quota_tool)

    assert result.structured_content == {"error": "points quota exceeded",
                                        "code": ms.ERR_QUOTA}
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row
    # #3675: a failure returned no collection, so the count is UNKNOWN — never
    # 0, which would claim the call returned an empty result set.
    assert row["result_count"] is None, row


@pytest.mark.asyncio
async def test_bundle_validation_failure_is_recorded_as_a_failure(events, stdio):
    """``ERR_BUNDLE_INVALID`` — the code and its ``violations`` ride through."""
    def bundle_tool() -> dict:
        return ms._safe(_BundleSDK().create_point, "statement", "x")

    result = await _call("_8135_bundle", bundle_tool)

    assert result.structured_content["code"] == ms.ERR_BUNDLE_INVALID
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_a_code_less_safe_error_is_recorded_as_a_failure(events, stdio):
    """A tool body that answers with an `_SafeError` of its OWN, with no ``code``.

    The generic branch of `_safe` and tool-built failures carry only ``error``,
    so a code-based discriminator would miss them entirely — which is why the
    recording hangs off the TYPE (`_SafeError.__init__`) rather than off a list
    of construction sites.
    """
    def bad_mode() -> dict:
        return ms._SafeError({"mode": "bogus",
                              "error": "recall mode 'bogus' not recognized"})

    await _call("_8135_badmode", bad_mode)

    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_the_real_recall_tool_is_classified_through_its_wider_payload(
        events, stdio, monkeypatch):
    """The FLAGSHIP retrieval tool, driven through its real body (#8135 P1).

    `tortoise_recall` does NOT return the `_SafeError` verbatim: it answers
    ``{"mode": mode, **results}`` — a NEW plain dict. Whole-dict equality
    therefore never matched it, and a swallowed quota/backend failure on
    ``tortoise_recall`` (one of only three names in
    `RETRIEVAL_TOOL_ALLOWLIST`, i.e. the population stage 4/5 measures) was
    still recorded ``ok``. The match must use the identifying ``error`` text
    `_safe` produced in this dispatch, which survives the spread.

    Pre-fix this asserts ``ok``: that is what makes it a pin and not a
    restatement of the #8482 envelope repair (which cannot reach a ``-> dict``
    tool at all — there is no wrap envelope to repair).
    """
    class _RaisingRecallSDK:
        def recall_state(self, *a, **kw):
            raise _quota_error()

    monkeypatch.setattr(ms, "_get_org_sdk", _RaisingRecallSDK)

    result = await ms.mcp.call_tool("tortoise_recall", {"query": "q"})

    # The wire is unchanged: the caller still sees the mode-spread failure.
    assert result.structured_content == {
        "mode": "state", "error": "points quota exceeded",
        "code": ms.ERR_QUOTA}
    row = events[-1]
    assert row["status"] == "exec_error", (
        "tortoise_recall reshapes the _SafeError into {mode, **results}; a "
        f"whole-dict match misses it and the failure reads ok: {row}")
    assert row["error_kind"] == "safe_error", row
    # A failure returned no collection — unknown, never 0 (#3675).
    assert row["result_count"] is None, row


@pytest.mark.asyncio
async def test_a_plain_dict_refusal_is_still_recorded_ok_this_is_the_boundary(
        events, stdio):
    """SCOPE, pinned: this fix covers `_SafeError` payloads, not plain dicts.

    The issue's second sample — `tortoise_recall`'s unrecognised-mode refusal —
    is measured HERE as what it actually is: a PLAIN dict built by the tool body
    (``mcp_server.py::tortoise_recall``), NOT an `_SafeError`. It belongs to a
    separate producer class (40+ tool-level refusals) and is deliberately out of
    this fix's scope: that class cannot be discriminated by key presence without
    re-creating the #3926 defect, so it needs the refusals to BE `_SafeError`s.

    Tracked as #9084. This test exists so the boundary is visible rather than
    implied, and it will be deleted (not fixed) when that issue lands — the
    recording already hangs off the type, so converting the refusals to
    `_SafeError` is all that takes.
    """
    def plain_refusal() -> dict:
        return {"mode": "bogus",
                "error": "recall mode 'bogus' not recognized — use state|gaps"}

    await _call("_8135_plain", plain_refusal)

    row = events[-1]
    assert row["status"] == "ok", (
        "a plain-dict refusal is outside this fix's scope; if this now asserts "
        f"exec_error, update the docstring's scope claim: {row}")


@pytest.mark.asyncio
async def test_generic_sdk_exception_is_recorded_as_a_failure(events, stdio):
    """A bare SDK exception: pre-fix, the whole exception class vanished."""
    def generic_tool() -> dict:
        return ms._safe(_GenericSDK().query, "statement")

    await _call("_8135_generic", generic_tool)

    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


@pytest.mark.asyncio
async def test_a_union_envelope_tool_is_classified_through_the_envelope(
        events, stdio):
    """A ``-> list[dict] | dict`` tool ships ``{"result": <failure>}`` UNREPAIRED.

    This is the read-through's only non-vacuous producer — the shape
    ``tortoise_query`` actually has in the registry. FastMCP wraps the result
    (``{"result": ...}``, wrap marker set), but the envelope admits a dict
    (``anyOf`` in the generated ``result`` schema), so ``_violates_wrap_envelope``
    answers ``False`` and #8482's repair deliberately does NOT fire
    (``mcp_server.py::_violates_wrap_envelope``: ``anyOf``/undeclared → False).
    A TOP-LEVEL-ONLY read in ``_returned_safe_failure`` therefore leaves this row
    ``ok`` — which is the fail-open hole #8135 closes.

    Deliberately NOT the ``-> list[dict]`` case: pre-#8135 that row already read
    ``exec_error``/``safe_error`` via the #8482 envelope repair, so it cannot
    prove the read-through fires (see the agreement check below).
    """
    def union_tool(query: str = "q") -> list[dict] | dict:
        return ms._safe(_QuotaSDK().query, query)

    result = await _call("_8135_union", union_tool, {"query": "q"})

    assert result.structured_content == {
        "result": {"error": "points quota exceeded", "code": ms.ERR_QUOTA}}
    assert result.is_error is False, (
        "a union envelope admits a dict result, so #8482 leaves the wire alone")
    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


def test_returned_safe_failure_matches_a_failure_spread_into_a_wider_payload():
    """`tortoise_recall`'s shape: ``{"mode": ..., **_safe_failure}``.

    The failure is a NEW dict, so whole-dict equality cannot match it. The
    identifying ``error`` text can — and only that text: a payload carrying a
    DIFFERENT message, a non-string ``error`` (the wait-bound refusal's
    ``{"error": {code, message}}``), or a success whose user prop happens to be
    named ``error`` must all stay unmatched (the false-positive direction).
    """
    failure = ms._SafeError({"error": "points quota exceeded",
                            "code": ms.ERR_QUOTA})
    token = ms._dispatch_safe_failures.set([failure])
    try:
        spread = {"mode": "state", **failure}
        assert spread != failure, "premise: the spread is a different dict"
        assert ms._returned_safe_failure(spread) is failure
        # ...and still through a wrap envelope, for a union-annotated tool.
        assert ms._returned_safe_failure(
            {"result": {"mode": "state", **failure}}) is failure
        # A DIFFERENT message this dispatch never produced is not a match.
        assert ms._returned_safe_failure(
            {"mode": "state", "error": "a user prop"}) is None
        # A non-string `error` (the wait-bound refusal shape) is not a `_safe`
        # failure and must not be matched by it.
        assert ms._returned_safe_failure(
            {"error": {"code": ms.ERR_TIMEOUT, "message": "x"}}) is None
        # An unrelated payload with no text at all is not a match.
        assert ms._returned_safe_failure({"mode": "state"}) is None
    finally:
        ms._dispatch_safe_failures.reset(token)


def test_returned_safe_failure_reads_through_the_envelope():
    """The read-through itself: delete the inner candidate and this fails.

    ``_returned_safe_failure`` keeps TWO candidates (the payload, and the
    ``result`` inside a FastMCP wrap envelope) because ``_safe_failure_message``
    established that shape for #8482. This pins the inner one directly, so the
    read-through cannot be removed silently while the union-envelope test above
    happens to pass for another reason.
    """
    failure = {"error": "points quota exceeded", "code": ms.ERR_QUOTA}
    token = ms._dispatch_safe_failures.set([failure])
    try:
        assert ms._returned_safe_failure({"result": dict(failure)}) == failure
        # The top-level candidate still works — an object-annotated tool.
        assert ms._returned_safe_failure(dict(failure)) == failure
        # ...and a payload that is neither shape is not a recorded failure.
        assert ms._returned_safe_failure({"result": {"other": 1}}) is None
        assert ms._returned_safe_failure({"result": [failure]}) is None
    finally:
        ms._dispatch_safe_failures.reset(token)


@pytest.mark.asyncio
async def test_an_array_envelope_tool_agrees_with_the_repair(events, stdio):
    """A ``-> list[dict]`` tool: #8482's repair ALREADY produces this pair.

    An AGREEMENT check between the two producers of ``exec_error``/``safe_error``
    (the payload classification and the envelope repair) — not a pin on the
    read-through: pre-#8135 this row read the same pair via the repair, so it
    cannot tell the two apart. Kept so a divergence (one producer moving, the
    other not) is visible.
    """
    def search_tool(query: str = "q") -> list[dict]:
        return ms._safe(_QuotaSDK().query, query)

    await _call("_8135_search", search_tool, {"query": "q"})

    row = events[-1]
    assert row["status"] == "exec_error", row
    assert row["error_kind"] == "safe_error", row


# ── The false positives a cheaper fix would create ────────────────────────

@pytest.mark.asyncio
async def test_a_success_carrying_a_user_prop_named_error_stays_ok(events, stdio):
    """#3926's discriminator, at the TELEMETRY seam.

    A successful SDK write returns the node's own property dict, which may have
    a user-supplied key literally named ``error``. A fix gated on ``"error" in
    payload`` would reclassify every such write as a failure — which is exactly
    why `_SafeError` is a dict SUBCLASS in the first place.
    """
    def write_tool() -> dict:
        return {"id": "pt_1", "error": "a user prop", "pointKind": "statement"}

    result = await _call("_8135_user_prop", write_tool)

    assert result.structured_content["id"] == "pt_1"
    row = events[-1]
    assert row["status"] == "ok", row
    assert row["error_kind"] is None, row


@pytest.mark.asyncio
async def test_a_dispatch_that_recovered_from_a_secondary_failure_stays_ok(
        events, stdio):
    """``tortoise_suggest_entry_points``' exact shape.

    It calls `_safe(fts_query)` and, when that fails, returns a DIFFERENT
    `_safe` call's result. The dispatch SUCCEEDED. A "did any `_safe` call
    fail?" flag would record it as ``exec_error`` — the failure column would
    fill with successful calls, which is the same class of defect in reverse.
    """
    class _RecoveredSDK:
        def tortoise_fts_query(self, *a, **kw):
            raise RuntimeError("fts backend down")

        def suggest_entry_points(self, *a, **kw):
            return [{"id": "pt_1", "name": "Acme"}]

    def recovering_tool(query: str = "q") -> list[dict]:
        try:
            results = ms._safe(_RecoveredSDK().tortoise_fts_query, query)
            if isinstance(results, list) and results:
                return results
        except Exception:  # pragma: no cover - mirrors the real tool
            pass
        return ms._safe(_RecoveredSDK().suggest_entry_points, query)

    result = await _call("_8135_recovered", recovering_tool, {"query": "q"})

    assert result.structured_content == {"result": [{"id": "pt_1",
                                                     "name": "Acme"}]}
    row = events[-1]
    assert row["status"] == "ok", (
        "a dispatch that recovered from a secondary `_safe` failure succeeded; "
        f"recording it as a failure is a false positive: {row}")


# ── The premise the fix rests on, and the channel it must not touch ───────

@pytest.mark.asyncio
async def test_the_type_is_gone_at_the_seam(events, stdio):
    """MEASUREMENT: FastMCP rebuilds the payload, so the type cannot be used.

    This is why the discriminator is recorded in `_safe` and matched by value
    rather than read here. If a future fastmcp release preserves the subclass,
    this test fails and the fix can be simplified — the failure would be
    informative, not a regression.
    """
    def quota_tool() -> dict:
        return ms._safe(_QuotaSDK().query, "statement")

    result = await _call("_8135_type", quota_tool)

    assert isinstance(result.structured_content, dict)
    assert not isinstance(result.structured_content, ms._SafeError), (
        "the `_SafeError` type survived serialization — `_returned_safe_failure` "
        "can be simplified to an isinstance check")


@pytest.mark.asyncio
async def test_the_wire_is_unchanged_by_the_classification(events, stdio):
    """#8135 is a TELEMETRY fix: the client sees exactly what it saw before.

    Same ``structured_content``, same ``is_error`` (False — an object-annotated
    tool's error dict is a legal payload, and #8482 deliberately left it alone),
    same text. Only the analytics row changes.
    """
    def quota_tool() -> dict:
        return ms._safe(_QuotaSDK().query, "statement")

    result = await _call("_8135_wire", quota_tool)

    assert result.structured_content == {"error": "points quota exceeded",
                                        "code": ms.ERR_QUOTA}
    assert result.is_error is False
    assert "points quota exceeded" in "".join(
        getattr(b, "text", "") for b in (result.content or ()))
    assert events[-1]["status"] == "exec_error"


def test_safe_outside_a_dispatch_is_unchanged():
    """`_safe` driven directly (no MCP seam) must not need a dispatch holder.

    The holder is only open inside `_wrapped_call_tool`; `_note_safe_failure`
    is a no-op otherwise, and the return value is the same `_SafeError`.
    """
    token = ms._transport_mode.set("http")
    try:
        assert ms._dispatch_safe_failures.get() is None, (
            "a holder leaked out of a previous dispatch")
        result = ms._safe(_QuotaSDK().query, "statement")
    finally:
        ms._transport_mode.reset(token)

    assert isinstance(result, ms._SafeError)
    assert result == {"error": "points quota exceeded", "code": ms.ERR_QUOTA}
