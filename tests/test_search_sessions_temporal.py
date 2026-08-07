"""#243 search_sessions temporal after/before filters.

Indexes AgentSession Events with distinct startedAt values and asserts
after/before bounds (ISO-8601 strings, datetime objects, composed with
agent filters) return the right subsets. Sessions without startedAt are
excluded whenever a bound is set.

Uses TortoiseSDK(file_path) for embedded FalkorDB Lite (no Docker needed).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK                          # noqa: E402

# R5 (#221): session-scoped shared embedded DB path (set by autouse fixture
# below) — one redislite server per session instead of one per test.
_SHARED_DB_PATH: str | None = None


@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    global _SHARED_DB_PATH
    _SHARED_DB_PATH = shared_embedded_db


def _fresh_sdk():
    """SDK backed by a fresh, isolated embedded FalkorDB Lite instance."""
    db_path = _SHARED_DB_PATH or os.path.join(tempfile.mkdtemp(prefix="tortoise_tsess_"), "test.db")
    sdk = TortoiseSDK(db_path)
    # Wipe before use (shared DB — hermeticity comes from the wipe, not a
    # fresh path). Embedded mode bypasses the test-graph guard (#99).
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


# ISO-8601 UTC markers (canonical stored format: datetime.now(timezone.utc).isoformat())
_JUL_01 = "2026-07-01T10:00:00+00:00"
_JUL_15 = "2026-07-15T12:30:00+00:00"
_JUL_20 = "2026-07-20T12:00:00+00:00"
_JUL_31 = "2026-07-31T18:45:00+00:00"


def _index_session(sdk, event_id: str, started_at: str | None, *,
                   agent: str = "pi", topics: list[str] | None = None):
    """Mirror session_indexer.py indexing: CREATE Event + props incl. startedAt."""
    props = {
        "eventId": event_id,
        "eventKind": "AgentSession",
        "name": f"Session {event_id}",
        "session_id": f"s-{event_id}",
        "agent": agent,
        "keywords": [f"kw-{event_id}"],
        "topics": topics or ["general"],
        "eventStatus": "completed",
        "classificationLevel": "internal",
    }
    if started_at is not None:
        props["startedAt"] = started_at
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {eventId: $eid}) SET e += $props",
        params={"eid": event_id, "props": props},
    )


def _ids(sessions: list[dict]) -> set[str]:
    return {s.get("eventId") or s.get("session_id") for s in sessions}


def test_search_sessions_temporal_after_before():
    """after/before filter AgentSession events by startedAt (string bounds)."""
    sdk = _fresh_sdk()
    try:
        _index_session(sdk, "s1", _JUL_01)
        _index_session(sdk, "s2", _JUL_15)
        _index_session(sdk, "s3", _JUL_31)
        _index_session(sdk, "s4", None)  # no startedAt — excluded when bound set

        # after only
        got = _ids(sdk.search_sessions("session", after="2026-07-10T00:00:00Z"))
        assert got == {"s2", "s3"}, got
        # before only (inclusive)
        got = _ids(sdk.search_sessions("session", before="2026-07-31T18:45:00+00:00"))
        assert got == {"s1", "s2", "s3"}, got
        got = _ids(sdk.search_sessions("session", before="2026-07-16T00:00:00Z"))
        assert got == {"s1", "s2"}, got
        # after + before window
        got = _ids(sdk.search_sessions("session", after="2026-07-10T00:00:00Z",
                                       before="2026-07-20T00:00:00Z"))
        assert got == {"s2"}, got
        # composed: agent + date bounds
        _index_session(sdk, "s5", _JUL_20, agent="opine")
        got = _ids(sdk.search_sessions("session", agent="pi",
                                       after="2026-07-15T00:00:00Z"))
        assert got == {"s2", "s3"}, got
        # no-startedAt session is excluded whenever a bound is set
        got = _ids(sdk.search_sessions("session", after="2026-01-01T00:00:00Z"))
        assert "s4" not in got and len(got) == 4, got
        # ...but appears in unbounded results
        got = _ids(sdk.search_sessions("session"))
        assert "s4" in got, got
    finally:
        sdk.close()


def test_search_sessions_temporal_datetime_bounds():
    """datetime bounds are normalized to UTC (naive=UTC, offsets converted)."""
    sdk = _fresh_sdk()
    try:
        _index_session(sdk, "d1", "2026-07-01T00:00:00+00:00")
        _index_session(sdk, "d2", "2026-07-15T12:00:00+00:00")
        _index_session(sdk, "d3", "2026-07-31T00:00:00+00:00")

        # naive datetime treated as UTC
        after = datetime(2026, 7, 10)
        got = _ids(sdk.search_sessions("session", after=after))
        assert got == {"d2", "d3"}, got
        # aware datetime in another tz converted to UTC (PDT = UTC-7)
        before = datetime(2026, 7, 20, 12, 0, tzinfo=timezone(timedelta(hours=-7)))
        got = _ids(sdk.search_sessions("session", before=before))
        assert got == {"d1", "d2"}, got
        # string with Z + offset both work together
        got = _ids(sdk.search_sessions("session",
                                       after="2026-07-01T00:00:00Z",
                                       before="2026-07-31T00:00:00+00:00"))
        assert got == {"d1", "d2", "d3"}, got
    finally:
        sdk.close()
