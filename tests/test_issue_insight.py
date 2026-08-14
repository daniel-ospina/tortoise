"""#1196 — tortoise_issue_insight MCP tool (surface b: graph insight at issue creation).

Research: docs/research/2026-08-14-1196-issue-insight-approaches.md — Approach A
(SDK method + new read-only MCP tool). E2E follows the E2E-17 convention
(tests/test_index_mcp.py): fresh embedded DB per test, seed graph via
create_point with github-indexer-shaped props, invoke the module-level
mcp_server handler directly (the exact function body FastMCPAdapter wraps),
stdio transport ContextVar fixture, assert via raw Cypher / returned payload.

"Issue-created → insight" is simulated: seed prior knowledge as the indexer
would have, then call the insight tool with the would-be issue's title.
"""
from __future__ import annotations

import os

import pytest

from tortoise.sdk import TortoiseSDK

GRAPH_TOPIC = "decision: keep JWT rotation for auth refresh tokens"


@pytest.fixture(autouse=True)
def _transport_context(monkeypatch):
    """MCP tools require an initialized transport mode (#236 auth gate).

    Same pattern as tests/test_index_mcp.py::_transport_context — stdio
    mode, dev auth, no team context; restore after each test.
    """
    from tortoise.mcp_auth import (
        _current_team_id, _current_team_limits, _transport_mode,
    )
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    _transport_mode.set("stdio")
    _current_team_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_team_id.set(None)
    _current_team_limits.set(None)


def _sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(os.path.join(str(tmp_path), "t.db"), namespace="e2e-1196")


def _dispatch_sdk(monkeypatch, sdk):
    """Route the MCP handler's team-SDK resolution to an isolated embedded DB."""
    import tortoise.mcp_server as ms
    monkeypatch.setattr(ms, "_get_team_sdk", lambda: sdk)
    return ms


def _seed_graph(sdk: TortoiseSDK, *, include_repo_a: bool = True) -> None:
    """Seed prior knowledge the github indexer would have written."""
    if include_repo_a:
        sdk.create_point(
            kind="observation",
            content="owner/a #101: auth refresh token rotation failed in prod",
            source="github", github_repo="owner/a", github_number=101, github_state="closed",
        )
        sdk.create_point(
            kind="observation",
            content="owner/a #102: plan JWT rotation rollout to all services",
            source="github", github_repo="owner/a", github_number=102, github_state="open",
        )
    # repo b — must never bleed into owner/a scoped queries
    sdk.create_point(
        kind="observation",
        content="owner/b #7: unrelated payment retry backoff tuning",
        source="github", github_repo="owner/b", github_number=7, github_state="open",
    )
    # cross-session decision (the semantic "aha" — non-GitHub-covered space)
    sdk.create_point(
        kind="decision",
        content=GRAPH_TOPIC,
    )


# ── E2E: issue-created → insight with ≥1 graph-derived data point ─────

class TestIssueInsightE2E:
    def test_issue_created_insight_emits_graph_derived_data_points(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(
            title="Should we keep JWT rotation for auth refresh tokens?",
            repo="owner/a",
        )

        assert result["has_prior"] is True
        assert result["no_prior_knowledge"] is False
        # ≥1 live-derived data point, content from the graph (never hardcoded)
        assert len(result["data_points"]) >= 1
        assert result["data_points"][0]["content"] == GRAPH_TOPIC
        assert result["data_points"][0]["kind"] == "decision"
        # repo stage: prior-issue stats for owner/a only (no bleed from owner/b)
        assert result["repo_stats"] == {"repo": "owner/a", "prior_issues": 2, "open": 1}
        # pointer topic is live-derived from the top hit
        assert "JWT rotation" in result["more_in_graph"]
        assert "graph hit" in result["insight"]

    def test_repo_scope_does_not_bleed_across_repos(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(title="retry backoff tuning", repo="owner/b")

        assert result["repo_stats"]["repo"] == "owner/b"
        assert result["repo_stats"]["prior_issues"] == 1
        assert all("owner/a" not in (dp.get("content") or "") for dp in result["data_points"])

    def test_empty_graph_fails_closed_to_no_prior_knowledge(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)  # fresh, empty
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(title="anything at all", repo="owner/a")

        assert result["no_prior_knowledge"] is True
        assert result["has_prior"] is False
        assert result["data_points"] == []
        assert "no prior knowledge" in result["insight"]

    def test_unindexed_repo_fails_closed_with_repo_not_indexed(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(title="JWT rotation", repo="owner/c")

        assert result["repo_not_indexed"] is True
        assert result["has_prior"] is True  # graph populated, repo not
        assert "no indexed issues" in result["insight"]

    def test_graph_service_failure_returns_error_dict_not_raise(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        def _boom(*args, **kwargs):
            raise RuntimeError("graph down")

        monkeypatch.setattr(sdk, "issue_insight", _boom)

        result = ms.tortoise_issue_insight(title="x", repo="owner/a")

        assert isinstance(result, dict)
        assert result.get("error") is not None or "error" in result


# ── SDK unit legs ─────────────────────────────────────────────────────

class TestIssueInsightSDK:
    def test_sdk_method_no_title_returns_no_matches_gracefully(self, tmp_path):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)

        result = sdk.issue_insight(title="")

        assert result["has_prior"] is False
        assert result["data_points"] == []
        assert "No graph matches" in result["insight"]
