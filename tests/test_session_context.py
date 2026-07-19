"""Tests for session_context() (#6989) — what happened last session.

Runnable with: .venv/bin/python -m pytest tests/test_session_context.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_sc_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


class TestSessionContext:
    def test_empty_db_returns_no_prior_sessions(self, sdk):
        """Empty database → no_prior_sessions=true, all lists empty."""
        result = sdk.session_context()
        assert result["no_prior_sessions"] is True
        assert result["diary_entries"] == []
        assert result["recent_points"] == []
        assert result["recent_events"] == []
        assert result["confidence_changes"] == []

    def test_with_diary_entries(self, sdk):
        """Diary entries populate diary_entries, no_prior_sessions becomes false."""
        sdk.diary_write("pi-agent", "SESSION:2026-07-19|built.session.context|★★★")
        sdk.diary_write("claude-agent", "SESSION:2026-07-19|reviewed.code|★★")

        result = sdk.session_context()
        assert result["no_prior_sessions"] is False
        assert len(result["diary_entries"]) == 2
        assert result["diary_entries"][0]["pointKind"] == "diary"

    def test_with_points(self, sdk):
        """Non-diary points populate recent_points, no_prior_sessions false."""
        sdk.create_point("statement", "something happened")
        sdk.create_point("decision", "we decided X")

        result = sdk.session_context()
        assert result["no_prior_sessions"] is False
        assert len(result["recent_points"]) == 2
        assert result["diary_entries"] == []
        assert result["recent_events"] == []

    def test_with_confidence_changes(self, sdk):
        """Points with computed confidence populate confidence_changes section."""
        import datetime as _dt
        proj = sdk._get_proj()
        # Write confidence to graph directly (EP requires operator chains)
        p = sdk.create_point("statement", "a test claim")
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence = 0.85, n.updatedAt = $now",
            params={"id": p["id"], "now": now},
        )

        result = sdk.session_context()
        assert result["no_prior_sessions"] is False
        assert len(result["confidence_changes"]) >= 1
        cc = result["confidence_changes"][0]
        assert cc["id"] == p["id"]
        assert cc["confidence"] == 0.85
        assert "updatedAt" in cc

    def test_with_events(self, sdk):
        """Events populate recent_events section."""
        # checkpoint emits EventRecorded → Event nodes
        sdk.checkpoint(
            [{"wing": "proj", "room": "decisions", "content": "decision X"}],
            agent_name="test-agent",
        )

        result = sdk.session_context()
        assert result["no_prior_sessions"] is False
        assert len(result["recent_events"]) >= 1
        ev = result["recent_events"][0]
        assert ev["eventKind"] == "pointAdded"
        assert ev["subject"] == "test-agent"

    def test_operator_exclusion(self, sdk):
        """Operator Points are excluded from recent_points."""
        # Create normal points first (operators need source/target points)
        a = sdk.create_point("statement", "claim A")
        b = sdk.create_point("statement", "claim B")
        sdk.create_operator("IMPL", a["id"], [b["id"]])

        result = sdk.session_context()
        # Should have 2 regular points, not the operator
        point_kinds = [p["pointKind"] for p in result["recent_points"] if p.get("pointKind")]
        assert "statement" in point_kinds
        # No operator points should leak in
        for p in result["recent_points"]:
            assert not p.get("is_operator")
