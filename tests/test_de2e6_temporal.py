"""DE2E-6 — Temporal belief tracking (#786).

Epic plan §7 DE2E-6: dated NAND chain (D1/D2), CORRECTS branch via
supersede_point (D3), live-prior variant (wire at promotion), no-date
fallback (validFrom == ingestedAt). #438 carve-out: TemporalWire runs
only inside the mining post-pass with source_session provenance.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tortoise.extractor import entity_stage_fixture
from tortoise.sdk import TortoiseSDK

T1 = "2026-07-01T10:00:00Z"
T2 = "2026-07-05T10:00:00Z"
T3 = "2026-07-10T10:00:00Z"

D1_TX = "Alice: We decided to use port 16379 for FalkorDB.\n"
D2_TX = "Alice: We decided to revert to port 16380 because the port 16379 decision was wrong.\n"
D3_TX = "Alice: We decided to supersede the port 16379 decision and use port 16380.\n"


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))


def _mine(sdk, transcript, source_id, session_date, tmp):
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import mine_conversation
    base = tmp or tempfile.mkdtemp()
    api = EventAPI(EventLog(os.path.join(base, "e.jsonl")),
                   initiated_by="extractor", agent_id="t",
                   projection=sdk._get_proj())
    return mine_conversation(
        transcript, source_id, api,
        entity_stage=entity_stage_fixture(),
        content_dedup=False,  # isolate the temporal pass (dedup is #784's)
        session_date=session_date,
        sdk=sdk)


def _decision_points(sdk):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {pointKind:'decision'}) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN n.id, n.content, n.validFrom, n.status, n.outdated "
        "ORDER BY n.validFrom"
    ).result_set
    return rows


def _nand_between(sdk, a_id, b_id):
    rows = sdk._get_proj().g.query(
        "MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$a}), "
        "(op)-[:NAND]->(:Point {id:$b}) RETURN count(op)",
        params={"a": a_id, "b": b_id},
    ).result_set
    return rows[0][0]


class TestDe2e6:
    def test_dated_nand_chain_and_timeline(self, sdk, tmp_path):
        """D1 (T1) + D2 (T2, refute cues) → draft-to-draft NAND; validFrom
        stamped from the session dates; belief_timeline shows the ordered
        chain with linked_by NAND."""
        r1 = _mine(sdk, D1_TX, "s-d1", T1, str(tmp_path))
        r2 = _mine(sdk, D2_TX, "s-d2", T2, str(tmp_path))
        assert r2["temporal_wired"] >= 1, r2
        pts = _decision_points(sdk)
        assert len(pts) >= 2
        by_content = {p[1]: p for p in pts}
        d1 = by_content.get("We decided to use port 16379 for FalkorDB.")
        d2 = by_content.get("We decided to revert to port 16380 because the port 16379 decision was wrong.")
        assert d1 and d2, f"decision points not found: {list(by_content)}"
        assert d1[2] == T1 and d2[2] == T2, (d1[2], d2[2])
        assert d1[3] == "draft" and d2[3] == "draft"
        assert _nand_between(sdk, d2[0], d1[0]) >= 1, (
            "D1/D2 must be linked by a draft-to-draft NAND"
        )
        timeline = sdk.belief_timeline("port 16379")
        assert len(timeline) >= 2
        vfs = [t["validFrom"] for t in timeline]
        assert vfs == sorted(vfs), f"timeline must be validFrom-ordered: {vfs}"
        linked = [t for t in timeline if t["linked_by"] == "NAND"]
        assert linked, f"timeline must show the NAND link: {timeline}"

    def test_live_prior_variant_wires_at_promotion(self, sdk, tmp_path):
        """D1 promoted live, then D2 mined: NO NAND at extraction, the
        temporal candidate surfaces to the review queue; after D2's
        promotion the NAND is wired live→live and the timeline shows both."""
        r1 = _mine(sdk, D1_TX, "s-v-d1", T1, str(tmp_path))
        pts = _decision_points(sdk)
        d1 = [p for p in pts if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        r2 = _mine(sdk, D2_TX, "s-v-d2", T2, str(tmp_path))
        assert r2["temporal_wired"] == 0, r2
        assert r2["temporal_candidates"] >= 1, r2
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        assert cands, "temporal candidate must surface to the review queue"
        assert cands[0]["target_id"] == d1[0]
        pts2 = _decision_points(sdk)
        d2 = [p for p in pts2 if p[1].startswith("We decided to revert")][0]
        assert _nand_between(sdk, d2[0], d1[0]) == 0, (
            "no NAND may be wired to a live prior at extraction"
        )
        approved = sdk.approve_merge(cands[0]["id"], action="merge")
        assert approved["deferred_to_promotion"] is True
        promoted = sdk.promote_point(d2[0])
        assert promoted["temporal_wired"] is True, promoted
        assert _nand_between(sdk, d2[0], d1[0]) == 1
        assert sdk.get_point(d1[0])["status"] == "live"
        assert sdk.get_point(d2[0])["status"] == "live"
        timeline = sdk.belief_timeline("port 16379")
        assert len(timeline) >= 2

    def test_explicit_replacement_supersedes(self, sdk, tmp_path):
        """D3 with supersede cues (prior live) → replacement candidate;
        approve + promote → supersede_point: D1 outdated:true + CORRECTS."""
        r1 = _mine(sdk, D1_TX, "s-r-d1", T1, str(tmp_path))
        d1 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        r3 = _mine(sdk, D3_TX, "s-r-d3", T3, str(tmp_path))
        assert r3["temporal_replacements"] >= 1, r3
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        repl = [c for c in cands if c["replacement"]]
        assert repl, f"replacement candidate expected: {cands}"
        sdk.approve_merge(repl[0]["id"], action="merge")
        d3 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to supersede")][0]
        promoted = sdk.promote_point(d3[0])
        assert promoted["superseded"] is True, promoted
        after = {p[1]: p for p in _decision_points(sdk)}
        assert after[d1[1]][4] is True or after[d1[1]][4] == "true", (
            "superseded prior must be outdated:true"
        )
        rows = sdk._get_proj().g.query(
            "MATCH (:Point {id:$new})-[:CORRECTS]->(:Point {id:$old}) "
            "RETURN count(*)",
            params={"new": d3[0], "old": d1[0]},
        ).result_set
        assert rows[0][0] >= 1, "CORRECTS edge must exist after supersede"

    def test_no_date_falls_back_to_ingested(self, sdk, tmp_path):
        """No frontmatter date → validFrom == ingestedAt (documented)."""
        r = _mine(sdk, D1_TX, "s-n-d1", None, str(tmp_path))
        pts = _decision_points(sdk)
        d1 = [p for p in pts if p[1].startswith("We decided to use port")][0]
        assert d1[2], "validFrom must be stamped (fallback ingestedAt)"
        assert d1[2] != T1  # not the fake session date — the ingest timestamp

    def test_source_session_provenance(self, sdk, tmp_path):
        """#438 carve-out audit: temporal candidates carry source_session."""
        r1 = _mine(sdk, D1_TX, "s-p-d1", T1, str(tmp_path))
        d1 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        _mine(sdk, D2_TX, "s-p-d2", T2, str(tmp_path))
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.source_session",
            params={"id": cands[0]["id"]},
        ).result_set
        assert rows and rows[0][0] == "s-p-d2", (
            "temporal candidate must carry the source_session provenance"
        )


class TestDe2e6ReviewFixes:
    """#1080 code-review regressions."""

    def test_replace_wording_is_replacement(self, sdk, tmp_path):
        """P1: 'replace' wording must be an explicit-replacement candidate
        (was: treated as a plain contradiction — no supersede)."""
        r1 = _mine(sdk, D1_TX, "s-f-r1", T1, str(tmp_path))
        d1 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        r3 = _mine(sdk,
                   "Alice: We decided to replace the port 16379 decision with port 16390.\n",
                   "s-f-r3", T3, str(tmp_path))
        assert r3["temporal_replacements"] >= 1, r3
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        assert any(c["replacement"] for c in cands), cands

    def test_no_double_nand_same_session(self, sdk, tmp_path):
        """P2: both sides of a contradictory pair carrying refute cues →
        exactly ONE NAND operator (was: wired twice, doubling the EP
        penalty)."""
        _mine(sdk,
              "Alice: We decided to use port 16379.\nBob: We decided to revert to port 16380 because the port 16379 decision was wrong.\n",
              "s-dbl", T1, str(tmp_path))
        rows = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true})-[:NAND]->(:Point) "
            "RETURN count(DISTINCT op)"
        ).result_set
        assert rows[0][0] == 1, f"exactly one NAND operator expected, got {rows}"

    def test_yaml_date_object_no_crash(self, sdk, tmp_path):
        """P2: unquoted YAML date (datetime.date) must not crash the pass —
        validFrom normalized to ISO."""
        from tortoise.sdk import TortoiseSDK
        d1 = sdk.create_point("decision", "We decided to use port 16379.",
                              status="live")
        corpus = tmp_path / "corpus-y"
        corpus.mkdir()
        (corpus / "s.md").write_text(
            "---\nsessionId: s-yaml\n"
            "date: 2026-07-01\n"  # YAML → datetime.date
            "---\n\n"
            "Alice: We decided to revert to port 16380 because the port 16379 decision was wrong.\n")
        res = sdk.mine_corpus(str(corpus), extract_entities=False)
        assert res.get("temporal_error") is None, res
        pts = _decision_points(sdk)
        d2 = [p for p in pts if p[1].startswith("We decided to revert")]
        if d2:
            assert d2[0][2] == "2026-07-01", d2[0][2]

    def test_reapprove_same_action_noop(self, sdk, tmp_path):
        """P2: re-approving with the same action emits no duplicate event."""
        import json
        r1 = _mine(sdk, D1_TX, "s-i-d1", T1, str(tmp_path))
        d1 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        _mine(sdk, D2_TX, "s-i-d2", T2, str(tmp_path))
        cand = sdk.list_dedup_candidates(candidate_type="temporal")[0]
        sdk.approve_merge(cand["id"], action="merge")
        sdk.approve_merge(cand["id"], action="merge")
        log = sdk._get_event_log().read_all()
        rec = [e for e in log if e.get("type") == "DedupeRecorded"]
        assert len(rec) == 1, f"no duplicate events expected, got {len(rec)}"

    def test_timeline_keeps_superseded_prior(self, sdk, tmp_path):
        """P2: after a replacement supersede, the timeline still shows the
        outdated prior (CORRECTS chain), with linked_by CORRECTS."""
        r1 = _mine(sdk, D1_TX, "s-t-d1", T1, str(tmp_path))
        d1 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to use port")][0]
        sdk.promote_point(d1[0])
        r3 = _mine(sdk, D3_TX, "s-t-d3", T3, str(tmp_path))
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        repl = [c for c in cands if c["replacement"]][0]
        sdk.approve_merge(repl["id"], action="merge")
        d3 = [p for p in _decision_points(sdk)
              if p[1].startswith("We decided to supersede")][0]
        sdk.promote_point(d3[0])
        timeline = sdk.belief_timeline("port 16379")
        assert len(timeline) >= 2, f"superseded prior must remain visible: {timeline}"
        corr = [t for t in timeline if t["linked_by"] == "CORRECTS"]
        assert corr, f"timeline must show the CORRECTS link: {timeline}"
