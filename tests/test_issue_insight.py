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
    from tortoise.mcp_auth import (  # noqa: I001
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
    return TortoiseSDK(os.path.join(str(tmp_path), "t.db"), namespace=f"test_e2e1196_{os.urandom(4).hex()}")


def _dispatch_sdk(monkeypatch, sdk):
    """Route the MCP handler's team-SDK resolution to an isolated embedded DB."""
    import tortoise.mcp_server as ms
    monkeypatch.setattr(ms, "_get_team_sdk", lambda: sdk)
    return ms


def _seed_graph(sdk: TortoiseSDK, *, include_repo_a: bool = True) -> None:
    """Seed prior knowledge the github indexer would have written."""
    obs_101 = None
    if include_repo_a:
        obs_101 = sdk.create_point(
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
    decision = sdk.create_point(
        kind="decision",
        content=GRAPH_TOPIC,
    )
    if obs_101 is not None:
        # EP-back the decision (review c70: the semantic stage counts only
        # EP-confirmed claims — confidence_mean >= 0.5). FIXTURE FIDELITY ONLY:
        # the decision clears the relevance gate on the >= 2-shared-token floor
        # (7 shared tokens with the query) in every retrieval mode, so this edge
        # is NOT load-bearing for any assertion in this file — mutation-tested by
        # deleting it, and the decision still reports has_ep=True at 0.75 (the
        # same class of leak as #3276). The old `confidence_mean = 1/1 = 1.0`
        # claim here was simply stale; nothing asserts the posterior, whose key
        # is attached only when `ep` is present.
        sdk._get_proj().g.query(
            "MATCH (a:Point), (b:Point) WHERE a.id = $a AND b.id = $b "
            "CREATE (a)-[:IMPL]->(b)",
            params={"a": obs_101["id"], "b": decision["id"]},
        )


# ── E2E: issue-created → insight with ≥1 graph-derived data point ─────

class TestIssueInsightE2E:
    def test_issue_created_insight_emits_graph_derived_data_points(self, tmp_path, monkeypatch):
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        # #3254: `limit=5` exceeds the fixture's gate-passing candidate count
        # (exactly 3), so the decision is EMITTED regardless of rank — that is a
        # structural invariant, not a mode- or rank-dependent one.
        #
        # The shipped default `limit=2` is deliberately NOT asserted here. It is
        # MODE-DEPENDENT, which cost a full CI cycle to learn: in the degraded /
        # TF-IDF lane the two unmeasured observations rank above the decision and
        # an `xfail(strict=True)` on "the default truncates it" held, but in the
        # `test (b)` lane the decision IS emitted at `limit=2` and that strict
        # xfail XPASSed into a FAILURE. A behaviour that differs by retrieval
        # mode cannot be pinned either way in a lane-agnostic suite — so this
        # asserts only what is true in every mode, and the default-limit shape is
        # left to #3277.
        result = ms.tortoise_issue_insight(
            title="Should we keep JWT rotation for auth refresh tokens?",
            repo="owner/a",
            limit=5,
        )

        assert result["has_prior"] is True
        assert result["no_prior_knowledge"] is False
        # #3254: the EP-confirmed cross-session decision must be EMITTED, not sit
        # at index 0. Candidate order is retrieved (and #3018 re-derived it), so
        # an index-0 pin was an incidental-value assertion — the same stale-pin
        # class as #3095.
        #
        # NO `confidence_mean` assertion here, deliberately (code review):
        # (a) not a mutation-killer — deleting the seeded IMPL edge leaves the
        # decision at has_ep=True / 0.75, so the discriminating variable is point
        # KIND (#3276); and (b) mode-fragile — `issue_insight` attaches the key
        # only when `ep` is present. Caveat, recorded so it is not lost: this
        # leaves the payload-side attachment of `confidence_mean` UNCOVERED.
        # TestIssueInsightRelevanceGate does NOT cover it — that class tests the
        # gate's threshold on injected `ep` dicts, never a `data_points` row.
        decisions = [dp for dp in result["data_points"] if dp["kind"] == "decision"]
        assert [dp["content"] for dp in decisions] == [GRAPH_TOPIC]
        # repo stage: prior-issue stats for owner/a only (no bleed from owner/b)
        assert result["repo_stats"] == {"repo": "owner/a", "prior_issues": 2, "open": 1}
        # `more_in_graph` is semantic_hits[0][:80], i.e. rank-0 dependent. Assert
        # only on a token EVERY gate-passing candidate shares: "rotation" is in
        # all three, whereas "JWT" is absent from `owner/a #101` (which has only
        # "rotation"), so a re-rank that puts #101 first would red on "JWT".
        assert "rotation" in result["more_in_graph"]
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


# ── #2206: relevance gate — unmeasured neutral 0.5 is NOT "we already decided" ─

class TestIssueInsightRelevanceGate:
    """Post-#2206 confidence_mean is the belief mean; an unmeasured point reads
    the neutral Beta(1,1) mean 0.5 with has_ep=False. The EP-confirmed branch of
    _issue_insight_relevant must therefore require has_ep — otherwise every
    never-measured hit would qualify as 'we already decided this'."""

    # query tokens share nothing with the hit content below → token floor inert
    _Q = "quagga zebroid"

    def test_unmeasured_neutral_not_ep_confirmed(self, tmp_path):
        sdk = _sdk(tmp_path)
        hit = {"content": "entirely unrelated sentence body", "ep": {"has_ep": False, "confidence_mean": 0.5}}
        assert sdk._issue_insight_relevant(hit, self._Q) is False

    def test_measured_high_posterior_is_ep_confirmed(self, tmp_path):
        sdk = _sdk(tmp_path)
        hit = {"content": "entirely unrelated sentence body", "ep": {"has_ep": True, "confidence_mean": 0.9}}
        assert sdk._issue_insight_relevant(hit, self._Q) is True

    def test_measured_but_low_belief_not_confirmed(self, tmp_path):
        sdk = _sdk(tmp_path)
        hit = {"content": "entirely unrelated sentence body", "ep": {"has_ep": True, "confidence_mean": 0.4}}
        assert sdk._issue_insight_relevant(hit, self._Q) is False

    def test_unmeasured_still_counts_on_two_token_overlap(self, tmp_path):
        sdk = _sdk(tmp_path)
        hit = {"content": "quagga zebroid live here", "ep": {"has_ep": False, "confidence_mean": 0.5}}
        assert sdk._issue_insight_relevant(hit, self._Q) is True
