"""The minutes→CRM bridge's refusal accounting, on its OWN surface (#11153).

Why this file exists at all, instead of living in `tests/test_bridge_mcp.py`: that
file is registered on the `api` surface only, while a diff that touches
`integrations/crm/twenty/bridge.py` and nothing else selects `core` (the bridge is a
`CORE_ALSO` path in `tools/ci_selection.py`). So a regression in these guards —
dropping `_write`'s `except ToolError`, or the `landed` term of the term — would
merge green on exactly the half that regresses it. Same reasoning this PR applied to
`tests/test_11153_payload_refusal_envelope.py`; measured, review round 9:
`select(["integrations/crm/twenty/bridge.py"])` → `core`, with
`tests/test_bridge_mcp.py` absent from the selected set.

Everything here is monkeypatched: no daemon, no DB, no network — which is what makes
it safe on the default (`core`) surface. The daemon-backed end-to-end push stays in
`tests/test_bridge_mcp.py`, where it belongs.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

# Repo root on sys.path so `import integrations` works under any pytest invocation.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_FRONTMATTER = {
    "title": "Test Meeting",
    "date": "2026-08-07",
    "decisions": [{"text": "Ship the service model."}],
    "commitments": [{"text": "Write the license."}],
}


def _bridge():
    from integrations.crm.twenty import bridge

    return bridge


def test_a_refused_write_keeps_the_ids_that_already_landed(monkeypatch):
    """#11153's audit item, for THIS caller: the surface flags a refusal with
    `isError: true`, and `tortoise.mcp_client` runs the fastmcp client with
    `raise_on_error=True` — so a refusal arrives as a raised `ToolError`.

    Left uncaught it aborted the loop into `tortoise_push_failed`, DISCARDING the
    ids of the points that had already landed: a partial minutes push that cannot be
    reconciled, produced by a DESIGNED refusal (review round 4, measured).
    """
    from fastmcp.exceptions import ToolError

    import tortoise.mcp_client as mcp_client

    bridge = _bridge()
    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    calls = {"n": 0}

    def _refusing_call_tool(_name, _args):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "p1"}
        raise ToolError('{"error": "Team points limit reached (25000)", '
                        '"code": -32006}')

    monkeypatch.setattr(mcp_client, "call_tool", _refusing_call_tool)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_partial", (
        "a refused write reported the whole push as failed, discarding what landed")
    assert result["points"][0]["id"] == "p1", result["points"]
    assert any("points limit" in f["error"] for f in result["failed"]), result


def test_a_push_where_nothing_landed_is_not_called_partial(monkeypatch):
    """The TERM has to follow what actually landed (review round 5).

    `tortoise_push_partial` means "some rows are on the graph, reconcile the ids"; a
    push where every write failed — including a server CRASH, which is also a
    `ToolError` — must keep the `tortoise_push_failed` alarm rather than read as a
    partial reconciliation case.
    """
    from fastmcp.exceptions import ToolError

    import tortoise.mcp_client as mcp_client

    bridge = _bridge()
    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    def _always_refuse(_name, _args):
        raise ToolError("Error calling tool: TypeError: 'NoneType' object is not "
                        "subscriptable")

    monkeypatch.setattr(mcp_client, "call_tool", _always_refuse)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_failed", result
    assert result["failed"], "the per-write detail must survive the report"


def test_an_escape_between_writes_keeps_the_ids_too(monkeypatch):
    """The handler for a NON-`ToolError` escape, one exception class over (round 9).

    `_write` narrows only `ToolError`, so a `ConnectionError`/`McpError` between two
    writes escapes to `push_to_tortoise`'s outer handler. That handler returned
    `{"status": "error", "reason": "tortoise_push_failed"}` with NO `points` and no
    `failed`, so a push whose first row landed was reported as a total failure and the
    landed id was dropped — the unreconcilable-partial class this hunk exists to
    close, on the arm it did not cover.
    """
    import tortoise.mcp_client as mcp_client

    bridge = _bridge()
    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    calls = {"n": 0}

    def _dies_after_the_first_write(_name, _args):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"id": "p1"}
        raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(mcp_client, "call_tool", _dies_after_the_first_write)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_partial", (
        "an escape between writes reported a push with a landed row as a total "
        f"failure: {result}")
    assert result["points"][0]["id"] == "p1", result["points"]


def test_an_escape_before_anything_landed_stays_a_total_failure(monkeypatch):
    """The mirror: no landed id means no reconciliation to do — keep the alarm."""
    import tortoise.mcp_client as mcp_client

    bridge = _bridge()
    monkeypatch.setattr(bridge, "tortoise_available", lambda: True)

    def _dies_immediately(_name, _args):
        raise ConnectionError("connection reset by peer")

    monkeypatch.setattr(mcp_client, "call_tool", _dies_immediately)

    result = bridge.push_to_tortoise(SAMPLE_FRONTMATTER)

    assert result["status"] == "error"
    assert result["reason"] == "tortoise_push_failed", result
