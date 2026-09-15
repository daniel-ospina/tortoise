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
        _current_org_id, _current_team_limits, _transport_mode,
    )
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    _transport_mode.set("stdio")
    _current_org_id.set(None)
    _current_team_limits.set(None)
    yield
    _transport_mode.set(None)
    _current_org_id.set(None)
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
        # EP-confirmed claims — confidence_mean >= 0.5).
        #
        # create_point(kind='decision') ALREADY applies the #2199 system-default
        # Beta(3,1) prior (ep_alpha/beta), so has_ep is True even with NO
        # posterior and NO edge — #3276's kind-derived-prior defect in
        # miniature. The SET below records a PERSISTED POSTERIOR so the claim is
        # still has_ep=True once #3276 makes has_ep measurement-derived. It is
        # INERT FOR THE EP PAYLOAD pre-#3276 (both paths coalesce to alpha=3.0,
        # so the payload is byte-identical — mutation-proven by deleting the
        # SET). Side effect, recorded: a post-CREATE SET is the #2952
        # index-mutation class and can shift the raw fulltext score — but the
        # predicate is score-INDEPENDENT and the decision stays retrieved either
        # way, so the ranked outcome is unaffected. The incoming IMPL edge is
        # the SOLE pre-#3276 reason the #3277 ranking predicate fires
        # (ep.evidence.total > 0). Without the edge the decision still clears
        # the relevance gate on the >= 2-shared-token floor (7 shared tokens
        # with the query), but it must NOT outrank lexical hits.
        sdk._get_proj().g.query(
            "MATCH (a:Point), (b:Point) WHERE a.id = $a AND b.id = $b "
            "CREATE (a)-[:IMPL]->(b) "
            "SET b.posterior_alpha = 3.0, b.posterior_beta = 1.0",
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
        # sparse lane (no dense leg) the two unmeasured observations rank above
        # the decision and an `xfail(strict=True)` on "the default truncates it"
        # held, but in the `test (b)` lane the decision IS emitted at `limit=2`
        # and that strict xfail XPASSed into a FAILURE. A behaviour that differs
        # by retrieval mode cannot be pinned either way in a lane-agnostic suite
        # — so this asserts only what is true in every mode, and the
        # default-limit shape is asserted by the #3277 E2E tests below.
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
        # (a) not a mutation-killer — the fixture seeds a persisted posterior,
        # so has_ep=True / 0.75 holds with or without the IMPL edge; the
        # discriminating variable for the #3277 RANKING is the incoming edge
        # (evidence.total), covered by TestIssueInsightRanking; and
        # (b) mode-fragile — `issue_insight` attaches the key only when `ep` is
        # present. Caveat, recorded so it is not lost: this
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

    def test_decision_survives_the_shipped_default_limit(self, tmp_path, monkeypatch):
        """#3277 — at the SHIPPED default limit, the EP-confirmed decision must
        be emitted, not truncated behind the two unmeasured observations.

        Pre-fix this was mode-dependent (#3254: the degraded/sparse lane
        truncated it, the `test (b)` lane did not), so it could not be pinned.
        Post-fix it is a structural invariant: the EP-confirmed claim WITH
        incoming evidence is ranked index 0 before `[:limit]`, so it wins in
        every EP-annotated retrieval mode — the default-limit gap this file
        previously left to a strict xfail (since removed; its reason cited
        #3277), now asserted directly here.
        """
        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(
            title="Should we keep JWT rotation for auth refresh tokens?",
            repo="owner/a",
        )  # default limit=2 — deliberately NOT raised

        assert len(result["data_points"]) == 2  # limit contract still holds
        decisions = [dp for dp in result["data_points"] if dp["kind"] == "decision"]
        assert [dp["content"] for dp in decisions] == [GRAPH_TOPIC]
        # the confirmed claim drives the rank-0-derived field too
        assert result["more_in_graph"].startswith("decision:")

    def test_decision_survives_default_limit_without_dense_leg(
        self, tmp_path, monkeypatch, force_sparse_tfidf,
    ):
        """#3277 — same invariant with the dense leg pinned OUT (sparse FTS/RRF).

        This is the lane where the unmeasured observations ranked above the
        decision and the pre-fix behaviour was non-deterministic. Same fixture,
        no dense leg: the EP-confirmed claim must still be index 0 at limit=2.
        The shared `force_sparse_tfidf` fixture (conftest) pins the embedder
        state; in an env without the embeddings extra the other E2E test
        exercises this same lane.

        NOTE: this is NOT the true `fallback_tfidf()` lane — that path only runs
        when the sparse leg is empty and carries `ep=None`, so there is no
        EP-confirmed claim to rank and the #3277 predicate is inert there.
        """
        from tortoise.embeddings import EmbeddingModel
        assert EmbeddingModel.get() is None  # honest precondition

        sdk = _sdk(tmp_path)
        _seed_graph(sdk)
        ms = _dispatch_sdk(monkeypatch, sdk)

        result = ms.tortoise_issue_insight(
            title="Should we keep JWT rotation for auth refresh tokens?",
            repo="owner/a",
        )

        decision_contents = [dp["content"] for dp in result["data_points"] if dp["kind"] == "decision"]
        assert decision_contents == [GRAPH_TOPIC]

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


# ── #3277: ranking — measured EP-confirmed claim outranks lexical matches ──

def _rank_hit(content: str, *, kind: str = "observation", has_ep: bool = False,
               confidence_mean: float = 0.5, evidence_total: int = 0) -> dict:
    """A `tortoise_fts_query`-shaped hit for the ranking predicate.

    `evidence_total` is `ep.evidence.total` (incoming IMPL+NAND edges) — the
    structural-evidence half of the #3277 ranking predicate. It is NOT a proof
    of measurement (#3276: `has_ep` alone cannot distinguish a persisted
    posterior from a kind-derived prior); `has_ep` carries that half.
    """
    return {
        "content": content,
        "point_kind": kind,
        "ep": {
            "has_ep": has_ep,
            "confidence_mean": confidence_mean,
            "evidence": {"impl_count": evidence_total, "nand_count": 0,
                         "total": evidence_total},
        },
    }


class TestIssueInsightRanking:
    """#3277 — the semantic stage returned retrieval order, so two unmeasured
    lexical matches could precede an EP-confirmed claim with incoming evidence
    and `[:limit]` (shipped default 2) then dropped the "we already decided"
    signal.
    `_issue_insight_rank` fixes the order deterministically; `[:limit]` still
    caps the result (reorder, not exempt)."""

    _DECISION = GRAPH_TOPIC
    _OBS_1 = "owner/a #102: plan JWT rotation rollout to all services"
    _OBS_2 = "owner/a #101: auth refresh token rotation failed in prod"
    # query tokens share nothing with the synthetic contents → token floor inert
    _Q = "quagga zebroid"

    def test_measured_ep_confirmed_outranks_unmeasured_lexical_matches(self, tmp_path):
        """The core bug, in the exact pre-fix order: two unmeasured lexical
        matches first, the measured confirmed decision last."""
        sdk = _sdk(tmp_path)
        hits = [
            _rank_hit(self._OBS_1),
            _rank_hit(self._OBS_2),
            _rank_hit(self._DECISION, kind="decision", has_ep=True,
                      confidence_mean=0.75, evidence_total=1),
        ]

        ranked = sdk._issue_insight_rank(hits)

        assert ranked[0]["content"] == self._DECISION
        # deterministic tie-break: the unmeasured tail keeps retrieval order
        assert [h["content"] for h in ranked[1:]] == [self._OBS_1, self._OBS_2]
        assert sdk._issue_insight_rank(hits) == ranked  # same input → same output
        assert ranked[:2][0]["content"] == self._DECISION  # survives default limit=2

    def test_unmeasured_kind_prior_decision_is_not_confirmed(self, tmp_path):
        """#3276 guard: a never-measured `decision` reads has_ep=True at the
        Beta(3,1) prior 0.75 with evidence.total == 0. It must NOT be ranked as
        the confirmed signal — the measured lexical hit wins instead."""
        sdk = _sdk(tmp_path)
        fake_confirmed = _rank_hit(
            self._DECISION, kind="decision", has_ep=True,
            confidence_mean=0.75, evidence_total=0,  # #3276: kind-derived prior
        )
        measured = _rank_hit("auth refresh rotation: measured finding",
                             has_ep=True, confidence_mean=0.6, evidence_total=1)

        ranked = sdk._issue_insight_rank([fake_confirmed, measured])

        assert ranked[0]["content"] == "auth refresh rotation: measured finding"
        assert ranked[1]["content"] == self._DECISION

    def test_edge_having_kind_prior_is_undecidable_from_the_payload(self, tmp_path):
        """#3276 residual, pinned deliberately: the ep payload carries no
        posterior-vs-prior flag, so an UNMEASURED decision that merely has an
        incoming edge (`evidence.total > 0`) still clears the predicate. The
        predicate requires the `has_ep` signal + structural evidence; it does
        not and cannot prove a posterior exists. This is the case #3276's fix
        changes (measurement-derived `has_ep`), so it is asserted explicitly
        rather than left implicit."""
        sdk = _sdk(tmp_path)
        assert sdk._issue_insight_measured_ep(
            {"content": self._DECISION, "point_kind": "decision",
             "ep": {"has_ep": True, "confidence_mean": 0.75,
                    "evidence": {"impl_count": 1, "nand_count": 0, "total": 1}}}
        ) is True

    def test_confirmed_sorted_by_belief_then_retrieval_order(self, tmp_path):
        """Deterministic total order among confirmed hits: belief desc, then the
        original retrieval index for equal belief (never set/hash order)."""
        sdk = _sdk(tmp_path)
        low = _rank_hit("alpha measured", has_ep=True, confidence_mean=0.6, evidence_total=1)
        high_a = _rank_hit("beta measured", has_ep=True, confidence_mean=0.9, evidence_total=1)
        high_b = _rank_hit("gamma measured", has_ep=True, confidence_mean=0.9, evidence_total=1)

        ranked = sdk._issue_insight_rank([low, high_a, high_b])

        assert [h["content"] for h in ranked] == ["beta measured", "gamma measured", "alpha measured"]

    def test_hit_without_evidence_is_not_boosted(self, tmp_path):
        """A `has_ep=True` hit with NO evidence key, or with `evidence.total == 0`
        (the #3276 kind-derived kind-prior shape), is not the ranking's confirmed
        signal — the predicate fails closed rather than treating missing/zero
        evidence as measured."""
        sdk = _sdk(tmp_path)
        assert sdk._issue_insight_measured_ep(
            {"content": "x", "ep": {"has_ep": True, "confidence_mean": 0.9}}
        ) is False
        assert sdk._issue_insight_measured_ep(
            {"content": "x", "point_kind": "decision",
             "ep": {"has_ep": True, "confidence_mean": 0.75,
                    "evidence": {"impl_count": 0, "nand_count": 0, "total": 0}}}
        ) is False
        # ...but the RELEVANCE gate still admits the measured-shaped hit (gate ≠
        # ranking): #3276's leak still reaches admission through the gate (the
        # assertion BELOW shows it) — #3277 is confined to ORDERING and does
        # not narrow the gate.
        assert sdk._issue_insight_relevant(
            {"content": "entirely unrelated sentence body",
             "ep": {"has_ep": True, "confidence_mean": 0.9}},
            self._Q,
        ) is True
