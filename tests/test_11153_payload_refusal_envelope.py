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
import json

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
        # fastmcp raises the message that was in `content[0].text`, and for a
        # `_safe` refusal that text IS the JSON error payload.
        raise ToolError('{"error": "Team points limit reached (25000)", "code": -32006}')

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


def test_a_server_crash_is_not_relabelled_as_a_designed_refusal(monkeypatch):
    """fastmcp raises `ToolError` for ANY error result, including a crash in the
    tool body. Translating that into a refusal would report a crash as the rc-3
    "designed refusal" and a driver paging on rc 1 would stay silent."""
    from fastmcp.exceptions import ToolError

    from tortoise import mcp_client

    mod = _curate_graph_module("11153_crash")

    def _crash(*_a, **_k):
        raise ToolError("Error calling tool: TypeError: 'NoneType' object is not "
                        "subscriptable")

    monkeypatch.setattr(mcp_client, "call_tool", _crash)
    with pytest.raises(ToolError):
        mod._live_call_tool("tortoise_query", {})


def test_a_refused_census_still_says_which_offset_it_died_at():
    """The diagnostic the false PASS used to hide must survive the new shape.

    #11153 moved the refusal from DATA (`is_error=False`, handled by `_error_of`)
    to a CLIENT RAISE, which happens one frame BELOW `read_points` — so the
    offset/row diagnostic `_error_of`'s arm produces has to be re-raised on the
    raise path too, or an operator is told only "points limit reached" and cannot
    tell a first-page refusal from a mid-walk one (review round 2, measured).

    It must stay on `CurationRefused` specifically (review round 3, measured):
    `main()` catches THAT class to answer rc 3, and its parent `RuntimeError` is
    NOT caught — an uncaught traceback with rc 1 is exactly the "the run crashed"
    signal a designed refusal must not emit.
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
    with pytest.raises(C.CurationRefused) as caught:
        reader.read_points(page=2, verify=False)
    message = str(caught.value)
    assert "offset 2" in message, message
    assert "after 2 rows" in message, message
    assert "points limit" in message, message
    assert "partial census" in message, message


def test_a_refused_section_still_names_the_section():
    """Same class, one site over: the `_error_of` arm used to say
    "read of section 'status' was refused: …", and the client-raise shape
    bypassed it (review round 3)."""
    from tortoise import curation as C

    mod = _curate_graph_module("11153_section")

    def _refuse(*_a, **_k):
        raise C.CurationRefused("Team points limit reached (25000)")

    reader = mod.MCPReader(_refuse)
    with pytest.raises(C.CurationRefused) as caught:
        reader._section("status")
    assert "status" in str(caught.value), str(caught.value)
    assert "points limit" in str(caught.value), str(caught.value)


def test_the_operator_read_is_labelled_too(monkeypatch):
    """The same label defect one site over (review round 4): the operator-layer
    read propagated a raw refusal while `_section` named itself."""
    from tortoise import curation as C

    mod = _curate_graph_module("11153_operators")

    def _refuse(*_a, **_k):
        raise C.CurationRefused('{"error": "Team points limit reached (25000)"}')

    reader = mod.MCPReader(_refuse)
    with pytest.raises(C.CurationRefused) as caught:
        reader.read_operators()
    assert "operator layer" in str(caught.value), str(caught.value)


def test_the_routine_wait_bound_refusal_is_still_a_designed_refusal(monkeypatch):
    """A census read breaching the transport wait bound is the ROUTINE refusal on
    the hosted surface, and its `content` is PROSE, not the JSON error dict — so a
    translation that only parses JSON re-raised it and the reader exited 1 with a
    traceback instead of the rc-3 designed refusal (review round 4, measured)."""
    from fastmcp.exceptions import ToolError

    from tortoise import curation as C
    from tortoise import mcp_client
    from tortoise.mcp_auth import _TRANSPORT_WAIT_BOUND_MESSAGE

    mod = _curate_graph_module("11153_waitbound")

    def _refuse(*_a, **_k):
        raise ToolError(_TRANSPORT_WAIT_BOUND_MESSAGE)

    monkeypatch.setattr(mcp_client, "call_tool", _refuse)
    assert mod._is_refusal_envelope(_TRANSPORT_WAIT_BOUND_MESSAGE) is True
    with pytest.raises(C.CurationRefused) as caught:
        mod._live_call_tool("tortoise_query", {})
    assert "wait budget" in str(caught.value), str(caught.value)


def test_every_wait_bound_prose_arm_is_a_designed_refusal():
    """All THREE `-32009` prose arms must classify, not just the two named first.

    `_wait_bound_refusal_result` picks one of three constants by whether the call was
    dispatched and whether it was a WRITE (`mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE`,
    `…_NOT_SENT_MESSAGE`, `…_WRITE_MESSAGE`). `refusal_message` matched only the first
    two by NAME, and the third fell through only because all three happen to share the
    base prose's leading 60 characters — user-facing text a human may reword.

    ⛔ Measured: `WRITE.startswith(BASE[:60])` is True today, so this test is a PIN, not
    a fix (no behaviour changes). It exists because a reworded write arm would make a
    refused WRITE re-raise as rc 1 "the run crashed", which is the class rounds 4 and 8
    closed.
    """
    from tortoise import mcp_auth
    from tortoise.mcp_client import refusal_message

    arms = (
        mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE,
        mcp_auth._TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE,
        mcp_auth._TRANSPORT_WAIT_BOUND_WRITE_MESSAGE,
    )
    for prose in arms:
        assert refusal_message(prose) is not None, prose[:60]
    # The three really are distinct texts, so this is three arms and not one repeated.
    assert len(set(arms)) == 3


# ── the aggregate orientation door (review round 6) ────────────────────────


def test_every_designed_prose_refusal_is_classified_not_a_crash(monkeypatch):
    """The sentinel list must cover EVERY designed prose refusal this surface ships
    (review round 9, measured).

    Two shipped refusals were prose with no entry — the graph-lookup MARGIN arm and
    the sensitive-op rate-limit (429) — so `refusal_message` bucketed them as CRASHES:
    `tools/curate_graph.py` re-raised them bare (an uncaught traceback, rc 1, for a
    designed refusal) and the CLI printed `call failed: …` instead of the refusal
    payload. The list is read from `mcp_auth`, and each producer writes the CONSTANT
    rather than a copy of the sentence, so this pin cannot drift from the wire.

    A crash must still fall through — asserted here so a future widening cannot make
    the classifier answer yes for everything.
    """
    from fastmcp.exceptions import ToolError

    from tortoise import curation as C
    from tortoise import mcp_client
    from tortoise.mcp_auth import (
        _REFUSAL_GRAPH_LOOKUP_MARGIN_MESSAGE,
        _REFUSAL_RATE_LIMIT_LEAD,
        _TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE,
        REFUSAL_PROSE_MESSAGES,
    )

    # NON-VACUITY: the two round-9 arms are IN the list the classifier reads.
    assert _REFUSAL_GRAPH_LOOKUP_MARGIN_MESSAGE in REFUSAL_PROSE_MESSAGES
    assert _REFUSAL_RATE_LIMIT_LEAD in REFUSAL_PROSE_MESSAGES
    assert _TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE in REFUSAL_PROSE_MESSAGES

    # The two EXACT wire messages the producers emit.
    margin = (f"{_REFUSAL_GRAPH_LOOKUP_MARGIN_MESSAGE}Retry after 10s.")
    rate_limit = f"{_REFUSAL_RATE_LIMIT_LEAD}export. Please try again later. (Retry-After: 3600s)"
    for wire in (margin, rate_limit):
        assert mcp_client.refusal_message(wire) == wire, wire
        assert mcp_client.refusal_message(ToolError(wire)) == wire, wire

    mod = _curate_graph_module("11153_prose")
    for wire in (margin, rate_limit):
        def _refuse(*_a, **_k):
            raise ToolError(wire)  # noqa: B023 - called inside this iteration only

        monkeypatch.setattr(mcp_client, "call_tool", _refuse)
        with pytest.raises(C.CurationRefused) as caught:
            mod._live_call_tool("tortoise_query", {})
        assert str(caught.value) == wire, str(caught.value)

    # ⛔ SOURCE PINS, or "a copy cannot be reintroduced" is not actually enforced.
    # The wire strings above are BUILT FROM the constants, so this test would stay
    # green if a producer reverted to an inline copy of the sentence: the wire would
    # keep the OLD text, the constant would be reworded, and this test would rebuild
    # the wire from the NEW constant and never touch the producer. Read the
    # producers' own source instead (the idiom is
    # `tests/test_7357_memory_ceiling_legibility.py`).
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    server_src = (root / "tortoise" / "mcp_server.py").read_text(encoding="utf-8")
    hosted_src = (root / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    assert "_REFUSAL_GRAPH_LOOKUP_MARGIN_MESSAGE" in server_src, (
        "the graph-lookup margin PRODUCER must write the constant, not a copy of "
        "the sentence — otherwise rewording the constant leaves the wire on the old "
        "text and this pin silently stops covering it")
    assert "_REFUSAL_RATE_LIMIT_LEAD" in hosted_src, (
        "the sensitive-op 429 PRODUCER must write the constant, not a copy")

    for crash in ("Error calling tool: TypeError: 'NoneType' object is not subscriptable",
                  "connection reset by peer"):
        assert mcp_client.refusal_message(crash) is None, crash


def test_the_graph_target_family_is_a_designed_refusal_not_a_crash(monkeypatch):
    """Round 10: the graph-target refusals were in NEITHER bucket.

    A graph-bound credential declining to be re-targeted, and a graph that is not
    reachable, are the surface working CORRECTLY — but they are prose with no
    envelope, so `refusal_message` bucketed them as crashes. Measured consequence:
    `read_points` has only `except C.CurationRefused` around its query, so the
    refusal escaped as an uncaught `ToolError` traceback (rc 1, offset/row
    diagnostics lost) instead of taking the documented graph fallback that
    `_section`, `read_operators` and `read_events` all rely on for the SAME
    refusal.
    """
    from fastmcp.exceptions import ToolError

    from tortoise import curation as C
    from tortoise import mcp_client
    from tortoise.mcp_auth import _retarget_refusal

    mod = _curate_graph_module("11153_graphtarget")
    wire = str(_retarget_refusal("org_x_g_other"))
    assert mcp_client.refusal_message(wire) == wire, wire
    assert mcp_client.refusal_message(
        "No graph 'org_x_g_other' is reachable from this workspace. "
        "Available: a, b") is not None

    def _refuse(*_a, **_k):
        raise ToolError(wire)

    monkeypatch.setattr(mcp_client, "call_tool", _refuse)
    with pytest.raises(C.CurationRefused) as caught:
        mod._live_call_tool("tortoise_get_entity", {})
    assert str(caught.value) == wire, str(caught.value)

def test_the_payload_is_redacted_in_the_cli_refusal_arm(monkeypatch, capsys):
    """#10605 applies to the refusal arm too (round 10).

    `describe_failure` is the redaction gate every other arm in that file uses —
    fastmcp carries the server's JSON-RPC text into the exception, so a refusal
    that echoed a credential would otherwise land verbatim on stdout, where
    scripted callers log it. The classifier still runs on the RAW text, so
    redaction cannot change which branch is taken.

    The echoed value here IS this process's own credential (`TORTOISE_API_KEY`) —
    the #10605 shape. A value this process does not know is not redactable, and
    that is not what the rule is about.
    """
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_refused_secret_value")

    # `client/` is not on sys.path for the package's own tests; `test_client_cli_probe`
    # inserts the CLI package's own root the same way.
    import sys
    from pathlib import Path

    from fastmcp.exceptions import ToolError

    client_dir = str(Path(__file__).resolve().parent.parent / "client")
    sys.path.insert(0, client_dir)
    try:
        from tortoise_client import cli
    finally:
        sys.path.remove(client_dir)

    def refused(*_a, **_k):
        raise ToolError('{"error": "rejected key tt_refused_secret_value", '
                        '"code": -32001}')

    monkeypatch.setattr(cli, "call_tool", refused)
    assert cli.main(["call", "tortoise_query"]) == 1
    out = capsys.readouterr().out
    assert "tt_refused_secret_value" not in out, (
        f"the credential reached stdout: {out}")
    assert "<redacted>" in out, out
    assert json.loads(out)["is_error"] is True, out



def test_a_total_outage_of_the_orientation_summary_is_a_refusal(monkeypatch):
    """`tortoise_overview()` with no `section` must not bury a total outage.

    It used to copy each section's `_SafeError` into `combined[sec]`, one level
    down, where the #11153 verdict flip and the #8135 telemetry arm cannot see it
    — so a complete outage shipped `isError: false` (measured, review round 6).
    When NOTHING answered, the refusal belongs at the TOP level.
    """
    def _all_refused(_sec, *_a, **_k):
        return ms._SafeError({"error": QUOTA_REFUSAL, "code": QUOTA_CODE})

    monkeypatch.setattr(ms, "_overview_section", _all_refused)
    result = ms.tortoise_overview()
    assert isinstance(result, ms._SafeError), (
        "a total outage was folded into the summary, so neither the verdict flip "
        "nor the telemetry arm could see it")
    assert result["error"] == QUOTA_REFUSAL


@pytest.mark.asyncio
async def test_a_heterogeneous_outage_still_flips_the_verdict(quiet, http_mode,
                                                              monkeypatch):
    """The R6 test's hole, closed (review round 7, P1).

    The aggregate builds ELEVEN `_SafeError`s in one dispatch, while the seam's
    holder keeps only the LAST eight — so returning the FIRST section's refusal
    verbatim handed the gate a payload that had already been evicted, and a total
    outage whose per-section texts DIFFER still shipped `isError: false`. The
    producer now re-constructs the refusal (`_SafeError(dict(...))`), which
    re-records it as the most recent one.
    """
    produced = {"n": 0}

    def _unique_text_per_section(_sec, *_a, **_k):
        produced["n"] += 1
        return ms._SafeError({"error": f"section {produced['n']}: boom"})

    monkeypatch.setattr(ms, "_overview_section", _unique_text_per_section)
    result = await ms.mcp.call_tool("tortoise_overview", {})

    assert produced["n"] > ms._MAX_RECORDED_SAFE_FAILURES, (
        "this test only proves anything when the aggregate outruns the holder's "
        "window")
    assert result.is_error is True, (
        "a total outage with per-section texts shipped `isError: false` — the "
        "returned refusal had been evicted from the holder before the gate read it")


def test_a_partial_orientation_summary_keeps_reporting_what_answered(monkeypatch):
    """The other direction: a summary where SOME sections answered stays a
    summary — the aggregate is still useful, and only a total outage is a
    refusal."""
    def _one_refused(sec, *_a, **_k):
        if sec == "taxonomy":
            return {"kinds": 3}
        return ms._SafeError({"error": QUOTA_REFUSAL, "code": QUOTA_CODE})

    monkeypatch.setattr(ms, "_overview_section", _one_refused)
    result = ms.tortoise_overview()
    assert isinstance(result, dict) and not isinstance(result, ms._SafeError)
    assert result["taxonomy"] == {"kinds": 3}
