"""DE2E-3 — Content dedup: "we already decided this" (#784).

Epic plan §7 DE2E-3 (Variants A/B/C + idempotency) + DE2E-N11 (pointKind
scoping) + N13/R14 (checkpoint back-compat). Pinned review band:
REVIEW_THRESHOLD=0.60, AUTO_MERGE=0.92.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tortoise.sdk import TortoiseSDK

DECISION_TEXT = "We decided to move the FalkorDB default port to 16379."

TRANSCRIPT = (
    "Alice: We decided to move the FalkorDB default port to 16379.\n"
    "Bob: I disagree because changing port 16379 breaks the redis config.\n"
)


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _decision(sdk, content=DECISION_TEXT, status="draft"):
    return sdk.create_point("decision", content, status=status)


def _impls_between(sdk, a_id, b_id):
    rows = sdk._get_proj().g.query(
        "MATCH (a:Point {id:$a})-[r:IMPL]->(b:Point {id:$b}) "
        "RETURN count(r)",
        params={"a": a_id, "b": b_id},
    ).result_set
    return rows[0][0]


def _mine(sdk, transcript=TRANSCRIPT, source_id="s-d2", tmp=None):
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import mine_conversation
    base = tmp or tempfile.mkdtemp()
    api = EventAPI(EventLog(os.path.join(base, "e.jsonl")),
                   initiated_by="extractor", agent_id="t",
                   projection=sdk._get_proj())
    return mine_conversation(transcript, source_id, api, sdk=sdk)


class TestDe2e3:
    def test_hash_tier_live_prior_no_wire(self, sdk, tmp_path):
        """Verbatim duplicate of a LIVE decision: candidate surfaced, no IMPL
        auto-wired (W-2 live-prior rule), the new point stays draft."""
        d1 = _decision(sdk, status="live")
        res = _mine(sdk, tmp=str(tmp_path))
        assert res["points"] > 0
        assert res["dedup_hits"] >= 1, res
        assert res["dedup_deferred"] >= 1, res
        assert res["dedup_wired"] == 0, res
        # The mined decision point is a flagged candidate.
        cands = sdk.list_dedup_candidates(candidate_type="content")
        assert cands, "candidate must be surfaced"
        c = cands[0]
        assert c["method"] == "hash"
        assert c["target_id"] == d1["id"]
        assert c["pointKind"] == "decision"
        # No IMPL from the candidate to the live prior.
        rows = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id})-[r:IMPL]->(m:Point {id:$tid}) "
            "RETURN count(r)",
            params={"id": c["id"], "tid": d1["id"]},
        ).result_set
        assert rows[0][0] == 0, "no IMPL may be wired to a live prior"

    def test_variant_a_draft_prior_wires_draft_to_draft(self, sdk, tmp_path):
        """D1 still draft → the 'already decided' IMPL is wired immediately
        (create_operator promote_source=False); BOTH endpoints stay draft."""
        d1 = _decision(sdk, status="draft")
        res = _mine(sdk, tmp=str(tmp_path))
        assert res["dedup_wired"] >= 1, res
        cand = sdk.list_dedup_candidates(candidate_type="content")[0]
        # The "alreadyDecided" operator connects the pair (operator-mediated).
        rows = sdk._get_proj().g.query(
            "MATCH (op:Point {label:'alreadyDecided'})-[r:IMPL]->(t:Point) "
            "RETURN op.id, op.status, op.is_operator, t.id"
        ).result_set
        linked = [r for r in rows
                  if r[3] == d1["id"]
                  and sdk._get_proj().g.query(
                      "MATCH (op:Point {id:$oid})-[:IMPL]->(s:Point {id:$sid}) "
                      "RETURN count(s)",
                      params={"oid": r[0], "sid": cand["id"]},
                  ).result_set[0][0] == 1]
        assert linked, "alreadyDecided operator must link D2→D1"
        # Operator node is DRAFT (promote_source=False) and both endpoints draft.
        assert linked[0][1] == "draft", linked
        assert sdk.get_point(cand["id"])["status"] == "draft"
        assert sdk.get_point(d1["id"])["status"] == "draft"

    def test_variant_b_reject(self, sdk, tmp_path):
        d1 = _decision(sdk, status="live")
        _mine(sdk, tmp=str(tmp_path))
        cand = sdk.list_dedup_candidates(candidate_type="content")[0]
        res = sdk.approve_merge(cand["id"], action="reject")
        assert res["action"] == "reject"
        assert sdk.get_point(cand["id"]).get("reviewed") is True
        assert sdk.get_point(cand["id"]).get("dedup_reviewed") == "reject"
        remaining = sdk.list_dedup_candidates(candidate_type="content")
        assert all(c["id"] != cand["id"] for c in remaining), (
            "rejected candidate must not be re-surfaced"
        )

    def test_variant_c_approve_then_promote_wires_live_to_live(self, sdk, tmp_path):
        """approve_merge(merge) against a LIVE prior defers; promote_point
        wires exactly ONE live→live IMPL."""
        d1 = _decision(sdk, status="live")
        _mine(sdk, tmp=str(tmp_path))
        cand = sdk.list_dedup_candidates(candidate_type="content")[0]
        res = sdk.approve_merge(cand["id"], action="merge")
        assert res["wired"] is False
        assert res["deferred_to_promotion"] is True
        assert _impls_between(sdk, cand["id"], d1["id"]) == 0
        promoted = sdk.promote_point(cand["id"])
        assert promoted["dedup_wired"] is True, promoted
        # Operator-mediated "alreadyDecided" link, exactly one.
        rows = sdk._get_proj().g.query(
            "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$tid}) "
            "RETURN op.id",
            params={"tid": d1["id"]},
        ).result_set
        assert len(rows) == 1, f"exactly one IMPL wired at promotion, got {rows}"
        assert sdk.get_point(cand["id"])["status"] == "live"
        assert sdk.get_point(d1["id"])["status"] == "live"
        # Re-promote: no duplicate wire.
        sdk.promote_point(cand["id"])
        rows2 = sdk._get_proj().g.query(
            "MATCH (op:Point {label:'alreadyDecided'})-[:IMPL]->(:Point {id:$tid}) "
            "RETURN op.id",
            params={"tid": d1["id"]},
        ).result_set
        assert len(rows2) == 1

    def test_corpus_rerun_idempotent(self, sdk, tmp_path):
        """Re-running the corpus adds no new candidates and no new
        DedupeRecorded (mined-marker skip)."""
        d1 = _decision(sdk, status="live")
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "s.md").write_text(
            "---\nsessionId: s-idem\n---\n\n" + TRANSCRIPT)
        r1 = sdk.mine_corpus(str(corpus), extract_entities=False)
        assert r1["dedup_hits"] >= 1, r1
        before = len(sdk.list_dedup_candidates(candidate_type="content"))
        r2 = sdk.mine_corpus(str(corpus), extract_entities=False)
        after = len(sdk.list_dedup_candidates(candidate_type="content"))
        assert after == before, f"re-run must not add candidates ({before}→{after})"

    def test_n11_pointkind_scope(self, sdk):
        """A duplicate observation must never suppress a decision: the
        hash check with pointKind='decision' ignores observation points."""
        obs = sdk.create_point("observation", "the port is 16379", status="live")
        assert sdk._content_exists("the port is 16379", pointKind="decision") is None
        assert sdk._content_exists("the port is 16379") == obs["id"]

    def test_semantic_dedup_modes(self, sdk):
        """_semantic_dedup return_pairs / similarity_out / pointKind modes."""
        sdk.create_point("decision", DECISION_TEXT, status="draft")
        pairs = sdk._semantic_dedup(
            [({"id": "x1", "content": DECISION_TEXT}, "")],
            threshold=0.60, pointKind="decision", return_pairs=True)
        assert pairs, "identical text must pair with sim >= threshold"
        assert pairs[0]["existing"] is not None
        assert 0.0 <= pairs[0]["similarity"] <= 1.0
        # Unrelated text → no pair.
        pairs2 = sdk._semantic_dedup(
            [({"id": "x2", "content": "completely unrelated topic about cats"}, "")],
            threshold=0.60, pointKind="decision", return_pairs=True)
        assert pairs2 == []
        # similarity_out shapes (item, ch, sim) for below-threshold survivors.
        out = sdk._semantic_dedup(
            [({"id": "x3", "content": "completely unrelated topic about cats"}, "ch3")],
            threshold=0.60, pointKind="decision", similarity_out=True)
        assert out and len(out[0]) == 3

    def test_checkpoint_back_compat(self, sdk):
        """R14: checkpoint() behavior is unchanged (checkpoint-item scoping)."""
        r1 = sdk.checkpoint([{"wing": "w", "room": "r", "content": "hello dedup"}])
        assert r1["filed"] == 1 and r1["duplicates"] == 0
        r2 = sdk.checkpoint([{"wing": "w", "room": "r", "content": "hello dedup"}])
        assert r2["filed"] == 0 and r2["duplicates"] == 1
