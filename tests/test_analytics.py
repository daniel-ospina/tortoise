"""Tests for onboarding analytics instrumentation (#501)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.hosted_api import _track_analytics_event, _ALLOWED_ANALYTICS_PROPS


class TestAnalyticsHelper:
    def test_allowed_props_validated(self, monkeypatch):
        """Unknown/PII keys are stripped; allowed keys pass through."""
        events = []

        def _fake_write(team_id, event_name, properties):
            events.append((team_id, event_name, properties))

        # Redirect the Supabase path to capture (no env vars → JSONL path).
        # Instead, test the prop filter directly via a temp fallback.
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr("tortoise.hosted_api._ANALYTICS_FALLBACK_PATH",
                                str(Path(tmp) / "analytics.jsonl"))
            _track_analytics_event("team-1", "question_answered", {
                "question_id": "github_connect",  # allowed
                "answer": "yes",                   # allowed
                "email": "user@example.com",       # NOT allowed — stripped
                "api_key": "tt_secret",            # NOT allowed — stripped
            })
            lines = (Path(tmp) / "analytics.jsonl").read_text().strip().split("\n")
            event = json.loads(lines[0])
            props = event["properties"]
            assert "question_id" in props and props["question_id"] == "github_connect"
            assert props["answer"] == "yes"
            assert "email" not in props
            assert "api_key" not in props

    def test_never_raises_when_unconfigured(self, monkeypatch):
        """No SUPABASE_URL/KEY → JSONL fallback, never raises."""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr("tortoise.hosted_api._ANALYTICS_FALLBACK_PATH",
                                str(Path(tmp) / "analytics.jsonl"))
            # Should not raise
            _track_analytics_event("team-1", "signup_complete", {"method": "email"})
            assert (Path(tmp) / "analytics.jsonl").exists()

    def test_required_props_defined(self):
        """The funnel taxonomy keys are all in the allow-list."""
        required = {"method", "harness", "section", "question_id", "answer",
                    "source", "point_count", "error_type", "steps_completed"}
        assert required <= _ALLOWED_ANALYTICS_PROPS
