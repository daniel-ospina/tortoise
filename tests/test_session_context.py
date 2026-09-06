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
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()  # noqa: UP017
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

    # ── #2207: digest noise filter ────────────────────────────────────
    # The session-start digest must surface genuine decision/claim points —
    # markdown rule lines ('---', '*Gate: filed as child issue…'), list/table
    # fragments and 'Label: value' config residue are filtered out.

    def test_digest_excludes_markdown_rule_noise(self, sdk):
        """#2207: markdown HR/bullet-rule/table/heading fragments and config
        label lines never appear in recent_points as 'recent decisions'."""
        sdk.create_point("decision", "We decided to adopt BSL 1.1 for the engine")
        for content in (
            "---",
            "*Gate: filed as child issue via `issue-creation` — number "
            "recorded in the epic plan doc (Stage 4).",
            "| Trigger | Must invoke | Consequence |",
            "## Git Workflow",
            "model: gpt-5",
            "* HARD RULE: Skill Compliance",
        ):
            sdk.create_point("statement", content)

        result = sdk.session_context()
        contents = [p.get("content", "") for p in result["recent_points"]]
        assert any("BSL 1.1" in c for c in contents), contents
        for noise in ("Gate:", "| Trigger", "## Git", "model: gpt-5",
                      "HARD RULE"):
            assert not any(noise in c for c in contents), contents
        # Bare '---' must not appear as a line (structural separator)
        assert not any(c.strip() == "---" for c in contents), contents
        print("PASS test_digest_excludes_markdown_rule_noise")

    def test_digest_keeps_genuine_points_of_any_kind(self, sdk):
        """#2207: filtering is noise-targeted — genuine prose points and
        decisions (any length/kind) still surface."""
        sdk.create_point("decision", "Use FalkorDB as primary graph store")
        sdk.create_point("observation", "Alpha in production")
        sdk.create_point("statement", "we decided X")
        sdk.create_point("statement", "something happened")

        result = sdk.session_context()
        contents = [p.get("content", "") for p in result["recent_points"]]
        for expected in ("Use FalkorDB as primary graph store",
                         "Alpha in production", "we decided X",
                         "something happened"):
            assert any(expected in c for c in contents), contents
        print("PASS test_digest_keeps_genuine_points_of_any_kind")

    def test_digest_filter_shared_pure_function(self, sdk):
        """#2207: the noise definition is a pure function over content so
        local digest, hosted /v1/context and the CLI share one rule set."""
        from tortoise.sdk import _is_digest_noise
        for noise in (None, "", "---", "***", "...", "* ",
                      "*Gate: filed as child issue via `issue-creation`.",
                      "| Trigger | Must invoke |", "## Header", "> quote",
                      "```python\nx=1\n```", "model: gpt-5",
                      "TORTOISE_DB_URI: docker://:x@localhost:6379/t",
                      "* HARD RULE: Skill Compliance", "1. **Model:** pick X"):
            assert _is_digest_noise(noise) is True, noise
        for clean in ("Use FalkorDB as primary graph store",
                      "We decided to adopt BSL 1.1 for the engine",
                      "Alpha in production", "we decided X",
                      "claim A", "decision X",
                      "We chose BSL 1.1 because it converts to MPL-2.0 later.",
                      "1. We decided to adopt BFS because it is simpler to operate.",
                      # #2225: the SDK's own label-led decision-writer shapes
                      # are genuine decisions, never digest noise.
                      "Decision: adopt FalkorDB as the primary graph store",
                      "Approved: scanner-release-2.4.1",
                      "Finding: FalkorDB survives the 10k-node benchmark",
                      "Reason: the migration cost is low",
                      "Option 1: JSON",
                      "2026-09-06: we decided to pin v2.5.1"):
            assert _is_digest_noise(clean) is False, clean
        print("PASS test_digest_filter_shared_pure_function")


# ── #2225 (post-batch bug hunt): SDK decision-writer shapes survive ───
# The digest noise filter (#2207/#2225) must NOT drop the SDK's own
# label-led decision content — 'Decision: …' (file_decision), 'Approved: …'
# (file_human_approval) and 'Option N: …' rows are genuine decisions, not
# rule/config residue. Pre-fix a decisions-only graph read as
# "no prior sessions" because the decision points were filtered out.

def _digest_contents(sdk) -> list[str]:
    return [p.get("content", "") for p in sdk.session_context()["recent_points"]]


class TestDigestKeepsSdkDecisionShapes:
    def test_file_decision_surfaces_decision_and_options(self, sdk):
        """A file_decision() call leaves its 'Decision: …' point AND its
        'Option N: …' rows in session recent_points (pre-fix the decision
        point was filtered as rule noise; only options/evidence survived)."""
        res = sdk.file_decision(
            ["adopt FalkorDB as the primary graph store",
             "keep the current store"],
            ["FalkorDB survives the 10k-node benchmark",
             "the current store times out on write bursts"],
            choice=0,
        )
        contents = _digest_contents(sdk)
        assert any(c.startswith("Decision: adopt FalkorDB") for c in contents), (
            f"decision point missing from digest: {contents}")
        assert any("Option 1: adopt FalkorDB" in c for c in contents), contents
        assert any("Option 2: keep the current store" in c for c in contents), \
            contents
        # The decision point itself is the point file_decision created.
        assert any(
            p.get("id") == res["decision_id"]
            for p in sdk.session_context()["recent_points"]), contents

    def test_human_approval_surfaces_approved_point(self, sdk):
        """file_human_approval()'s default 'Approved: <artifact>' decision
        point appears in recent_points (pre-fix: filtered as rule noise)."""
        claim = sdk.create_point("decision", "ship the scanner build to internal testing")
        subj = sdk.create_subject("daniel", "engineer")
        doc = sdk.create_document("scanner-release-2.4.1", "artifact")
        res = sdk.file_human_approval(
            approver_id=subj["id"],
            artifact_id=doc["id"],
            point_ids=[claim["id"]],
        )
        contents = _digest_contents(sdk)
        assert any(c.startswith("Approved: ") and doc["id"] in c
                   for c in contents), (
            f"approval point missing from digest: {contents}")
        assert any(p.get("id") == res["decision_point_id"]
                   for p in sdk.session_context()["recent_points"]), contents

    def test_decisions_only_graph_is_not_no_prior_sessions(self, sdk):
        """Second-order regression: a graph whose recent points are ALL
        SDK decision/approval shapes must NOT report 'no prior sessions' —
        no_prior_sessions is computed AFTER the noise filter, so dropping
        the decisions made an all-decision graph read as empty."""
        sdk.file_decision(
            ["adopt BSL 1.1"], ["it converts to MPL-2.0 later"], choice=0)
        claim = sdk.create_point("decision", "pin tortoise at v2.5.1")
        subj = sdk.create_subject("daniel", "engineer")
        doc = sdk.create_document("tortoise-v2.5.1", "artifact")
        sdk.file_human_approval(
            approver_id=subj["id"], artifact_id=doc["id"],
            point_ids=[claim["id"]],
        )
        ctx = sdk.session_context()
        assert ctx["no_prior_sessions"] is False, (
            "all-decision graph must not read as empty")
        assert len(ctx["recent_points"]) >= 3, ctx["recent_points"]

    def test_rule_noise_still_filtered_beside_decision_shapes(self, sdk):
        """The #2207 filter keeps working: rule/config residue created in
        the SAME graph as decision shapes never reaches the digest."""
        sdk.file_decision(["adopt YAML"], ["schemas validate"], choice=0)
        for content in ("---", "*Gate: filed as child issue via `issue-creation`.",
                        "| Trigger | Must invoke |", "model: gpt-5",
                        "TORTOISE_DB_URI: docker://:x@localhost:6379/t",
                        "* HARD RULE: Skill Compliance"):
            sdk.create_point("statement", content)
        contents = _digest_contents(sdk)
        assert any(c.startswith("Decision: adopt YAML") for c in contents), contents
        for noise in ("Gate:", "| Trigger", "model: gpt-5", "TORTOISE_DB_URI",
                      "HARD RULE"):
            assert not any(noise in c for c in contents), (noise, contents)
        assert not any(c.strip() == "---" for c in contents), contents
