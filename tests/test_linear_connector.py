"""Tests for Linear connector — issue/cycle → EventRecorded mapping."""
from __future__ import annotations  # noqa: I001

import pytest  # noqa: F401
from tortoise.connectors.linear import LinearConnector, _now_iso


# ── Issue → Event mapping ────────────────────────────────────────

def test_issue_to_event_basic():
    lc = LinearConnector(config={"api_key": "lin_api_test", "team_id": "team-1"})
    issue = {
        "identifier": "TEAM-42",
        "title": "Fix login flow",
        "state": {"name": "In Progress", "type": "started"},
        "team": {"key": "TEAM", "name": "Engineering"},
        "assignee": {"name": "Alice", "email": "alice@example.com"},
        "createdAt": "2026-07-15T10:00:00Z",
        "updatedAt": "2026-07-19T12:00:00Z",
        "completedAt": None,
        "canceledAt": None,
        "cycle": {"name": "Cycle 5"},
        "priority": 2,
        "url": "https://linear.app/test/issue/TEAM-42",
    }
    ev = lc._issue_to_event(issue)
    assert ev is not None
    assert ev["type"] == "EventRecorded"
    assert ev["eventId"] == "linear-issue-TEAM-42"
    assert ev["eventKind"] == "linear.issue.in_progress"
    assert ev["subject"] == "issue:linear:TEAM-42"
    assert ev["object"] == "Fix login flow"
    assert ev["source"] == "linear:TEAM"
    assert ev["participants"] == ["alice@example.com"]
    assert ev["endedAt"] is None
    # #388: per-entity url flows through from the GraphQL `url` field
    assert ev["sourceUrl"] == "https://linear.app/test/issue/TEAM-42"
    assert ev["sourceKind"] == "linear_card"


def test_issue_to_event_completed():
    lc = LinearConnector(config={"api_key": "k"})
    issue = {
        "identifier": "BUG-7",
        "title": "Fix typo",
        "state": {"name": "Done"},
        "team": {"key": "BUG"},
        "createdAt": "2026-07-14T00:00:00Z",
        "completedAt": "2026-07-15T00:00:00Z",
        "canceledAt": None,
        "assignee": None,
    }
    ev = lc._issue_to_event(issue)
    assert ev["eventKind"] == "linear.issue.completed"
    assert ev["endedAt"] == "2026-07-15T00:00:00Z"
    assert ev["participants"] == []
    # no url in fixture → empty sourceUrl (projection falls back to `source`)
    assert ev["sourceUrl"] == ""


def test_issue_to_event_canceled():
    lc = LinearConnector(config={"api_key": "k"})
    issue = {
        "identifier": "TASK-1",
        "title": "Old task",
        "state": {"name": "Canceled"},
        "team": {"key": "T"},
        "createdAt": "2026-06-01T00:00:00Z",
        "completedAt": None,
        "canceledAt": "2026-07-01T00:00:00Z",
        "assignee": None,
    }
    ev = lc._issue_to_event(issue)
    assert ev["endedAt"] == "2026-07-01T00:00:00Z"


def test_issue_to_event_skips_empty():
    lc = LinearConnector(config={"api_key": "k"})
    assert lc._issue_to_event({"identifier": "", "title": "x"}) is None
    assert lc._issue_to_event({"identifier": "X-1", "title": ""}) is None
    assert lc._issue_to_event({}) is None


def test_issue_to_event_truncates_long_title():
    lc = LinearConnector(config={"api_key": "k"})
    long_title = "A" * 300
    issue = {
        "identifier": "TEAM-1",
        "title": long_title,
        "state": {},
        "team": {},
        "createdAt": "2026-01-01T00:00:00Z",
    }
    ev = lc._issue_to_event(issue)
    assert ev is not None
    assert len(ev["object"]) == 200


def test_issue_to_event_missing_team_key():
    lc = LinearConnector(config={"api_key": "k"})
    issue = {
        "identifier": "X-1",
        "title": "No team",
        "state": {},
        "team": {},  # no key
        "createdAt": "2026-01-01T00:00:00Z",
    }
    ev = lc._issue_to_event(issue)
    assert ev is not None
    assert ev["source"] == "linear:"


# ── Cycle → Event mapping ────────────────────────────────────────

def test_cycle_to_event_active():
    lc = LinearConnector(config={"api_key": "k"})
    cycle = {
        "number": 5,
        "name": "Sprint 5",
        "team": {"key": "ENG", "name": "Engineering"},
        "startsAt": "2026-07-01T00:00:00Z",
        "endsAt": "2026-07-14T00:00:00Z",
        "completedAt": None,
        "progress": 0.5,
    }
    ev = lc._cycle_to_event(cycle)
    assert ev is not None
    assert ev["eventId"] == "linear-cycle-ENG-5"
    assert ev["eventKind"] == "linear.cycle.active"
    assert ev["subject"] == "cycle:linear:ENG#5"
    assert ev["object"] == "Sprint 5"
    assert ev["endedAt"] is None  # active cycles have no endedAt
    # #388: cycles are not cards — own sourceKind + container-level fallback url
    assert ev["sourceKind"] == "linear_cycle"
    assert ev["sourceUrl"] == "linear:ENG"


def test_cycle_to_event_completed():
    lc = LinearConnector(config={"api_key": "k"})
    cycle = {
        "number": 3,
        "name": "Sprint 3",
        "team": {"key": "DEV"},
        "startsAt": "2026-06-01T00:00:00Z",
        "endsAt": "2026-06-14T00:00:00Z",
        "completedAt": "2026-06-14T00:00:00Z",
    }
    ev = lc._cycle_to_event(cycle)
    assert ev["eventKind"] == "linear.cycle.completed"
    assert ev["endedAt"] == "2026-06-14T00:00:00Z"
    assert ev["sourceKind"] == "linear_cycle"
    assert ev["sourceUrl"] == "linear:DEV"


def test_cycle_to_event_skips_missing():
    lc = LinearConnector(config={"api_key": "k"})
    assert lc._cycle_to_event({"number": None, "name": "x"}) is None
    assert lc._cycle_to_event({"number": 1, "name": ""}) is None


# ── Polling ──────────────────────────────────────────────────────

def test_poll_no_api_key_returns_empty():
    lc = LinearConnector(config={})
    assert lc.poll() == []

    lc2 = LinearConnector(config={"api_key": ""})
    assert lc2.poll() == []


# ── _now_iso ─────────────────────────────────────────────────────

def test_now_iso():
    iso = _now_iso()
    assert "T" in iso
    assert iso.endswith("Z") or "+" in iso


# ── GraphQL query mock ───────────────────────────────────────────

def test_query_without_api_key_returns_empty():
    lc = LinearConnector(config={})
    assert lc._query("query { issues { nodes { id } } }") == {}


# ── #331: undeclared GraphQL variable + swallowed HTTP errors ──────

def test_cycles_query_uses_schema_valid_team_filter():
    """#331 (review r5): Query.cycles has no teamId argument in the Linear
    schema — team filtering must go through filter: CycleFilter (same
    pattern as _poll_issues). A direct teamId argument is a GraphQL
    validation error in every configuration."""
    lc = LinearConnector(config={"api_key": "lin_api_test", "team_id": "team-1"})
    captured: dict = {}

    def fake_query(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"data": {"cycles": {"nodes": []}}}

    lc._query = fake_query
    assert lc._poll_cycles() == []
    assert captured["variables"].get("filter") == {"team": {"id": {"eq": "team-1"}}}
    assert "$filter: CycleFilter" in captured["query"], \
        "query must declare $filter: CycleFilter"
    assert "filter: $filter" in captured["query"], \
        "query must pass filter: $filter to cycles()"
    assert "teamId" not in captured["query"], \
        "teamId is not a valid Query.cycles argument in the Linear schema"


def test_cycles_query_no_filter_without_team_id():
    """#331 (review r5): without a configured team_id the Cycles query must
    not send a filter variable (all cycles, schema-valid)."""
    lc = LinearConnector(config={"api_key": "lin_api_test"})
    captured: dict = {}

    def fake_query(query, variables=None):
        captured["query"] = query
        captured["variables"] = variables
        return {"data": {"cycles": {"nodes": []}}}

    lc._query = fake_query
    assert lc._poll_cycles() == []
    assert "filter" not in captured["variables"]


def test_query_tolerates_malformed_errors_payload(caplog):
    """#331 (review r5): a malformed GraphQL errors payload (null, string,
    non-dict entries) must be surfaced without crashing the connector —
    the error-reporting path itself must not become a crash vector."""
    import json as _json  # noqa: I001
    import urllib.request  # noqa: F401
    import unittest.mock as mock

    lc = LinearConnector(config={"api_key": "lin_api_test"})
    for bad_errors in (None, "oops", [{"message": "fine"}, "garbage", 42]):
        resp = mock.MagicMock()
        body = _json.dumps({"errors": bad_errors}).encode()
        resp.read.return_value = body
        cm = resp.__enter__.return_value
        cm.read.return_value = body
        with mock.patch("urllib.request.urlopen", return_value=resp):  # noqa: SIM117
            with caplog.at_level("WARNING", logger="tortoise.connectors.linear"):
                assert lc._query("query X { x }") == {}
    assert any("GraphQL" in r.message for r in caplog.records), \
        "malformed GraphQL errors must still be logged"


def test_query_logs_http_errors(caplog):
    """#331: HTTP errors must be LOGGED, not silently swallowed as 'no data'."""
    import json as _json  # noqa: F401, I001
    import urllib.error
    import urllib.request
    import unittest.mock as mock

    lc = LinearConnector(config={"api_key": "lin_api_test"})
    with mock.patch(  # noqa: SIM117
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        with caplog.at_level("WARNING", logger="tortoise.connectors.linear"):
            # Still degrades to empty (polling must not crash), but the failure
            # must be visible in the logs.
            assert lc.poll() == []
    assert any("connection refused" in r.message for r in caplog.records), \
        "HTTP error must be logged"


def test_query_logs_graphql_errors(caplog):
    """#331: GraphQL-level errors must be logged, not silently dropped."""
    import json as _json  # noqa: I001
    import urllib.request  # noqa: F401
    import unittest.mock as mock

    lc = LinearConnector(config={"api_key": "lin_api_test"})
    resp = mock.MagicMock()
    resp.read.return_value = _json.dumps(
        {"errors": [{"message": "Variable $teamId is not declared"}]}
    ).encode()
    cm = resp.__enter__.return_value
    cm.read.return_value = resp.read.return_value
    with mock.patch("urllib.request.urlopen", return_value=resp):  # noqa: SIM117
        with caplog.at_level("WARNING", logger="tortoise.connectors.linear"):
            result = lc._query("query X { x }")
    assert result == {}
    assert any("GraphQL" in r.message for r in caplog.records), \
        "GraphQL errors must be logged"
