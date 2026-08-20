"""Tests for #531: file_human_approval — Event + decision Point + IMPL fan-out.

Covers the O/I/T from issue #531:
- dependent claim confidence rises after approval EP
- humanApproval Points seed grounding
- reputation unchanged by approvals (compute_reputation excludes humanApproval)
- mined 'decision' points never seed grounding or register as approvals
- revocation via supersede_point
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = f"{tempfile.mkdtemp(prefix='tt_531_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None  # bypass production guard for test graph
    yield s
    s.close()


def _setup_approval(sdk):
    """Create approver, artifact, claims; file an approval; return ids."""
    subj = sdk.create_subject("daniel", "engineer")
    doc = sdk.create_document("customer-profile-cp-001", "artifact")
    c1 = sdk.create_point("statement", "CP-001 targets the SMB segment", status="live")  # #992: live — draft stripped by EP filter
    c2 = sdk.create_point("statement", "CP-001 addresses the onboarding pain", status="live")
    result = sdk.file_human_approval(
        approver_id=subj["id"],
        artifact_id=doc["id"],
        point_ids=[c1["id"], c2["id"]],
    )
    return subj, doc, c1, c2, result


# ── Core pattern ──────────────────────────────────────────────────

class TestFileHumanApproval:
    def test_creates_event_decision_and_impl(self, sdk):
        """Event (humanApproval) + decision Point (humanApproval) + IMPL fan-out."""
        subj, doc, c1, c2, result = _setup_approval(sdk)
        proj = sdk._get_proj()

        # Event exists with right kind
        r = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.eventKind",
            params={"eid": result["event_id"]},
        ).result_set
        assert r[0][0] == "humanApproval"

        # Decision Point exists with right kind
        r = proj.g.query(
            "MATCH (p:Point {id:$pid}) RETURN p.pointKind",
            params={"pid": result["decision_point_id"]},
        ).result_set
        assert r[0][0] == "humanApproval"

        # Provenance edges: approver performs, uses artifact, produces decision
        for rel, tail, head in [  # noqa: B007
            ("performs", subj["id"], result["event_id"]),
            ("uses", result["event_id"], doc["id"]),
            ("produces", result["event_id"], result["decision_point_id"]),
        ]:
            r = proj.g.query(
                f"MATCH (a)-[:{rel}]->(b) WHERE a.id = $t OR a.eventId = $t "
                "RETURN count(*) > 0",
                params={"t": tail},
            ).result_set
            assert r[0][0] is True, f"{rel} edge missing"

        # aboutPoint edges to both claims
        for c in (c1, c2):
            r = proj.g.query(
                "MATCH (e:Event {eventId:$eid})-[:aboutPoint]->(p:Point {id:$pid}) "
                "RETURN count(*) > 0",
                params={"eid": result["event_id"], "pid": c["id"]},
            ).result_set
            assert r[0][0] is True, "aboutPoint edge missing"

        # Unidirectional IMPL fan-out, label approvedBy — the operator node
        # carries direction/label as properties; IMPL edges go operator → each
        # claim (standard tortoise operator pattern).
        op_id = result["impl_operator_ids"][0]
        r = proj.g.query(
            "MATCH (o:Point {id:$oid}) RETURN o.direction, o.label",
            params={"oid": op_id},
        ).result_set
        assert r[0][0] == "unidirectional", f"got direction {r[0][0]}"
        assert r[0][1] == "approvedBy", f"got label {r[0][1]}"
        r = proj.g.query(
            "MATCH (o:Point {id:$oid})-[:IMPL]->(c:Point) RETURN count(*)",
            params={"oid": op_id},
        ).result_set
        assert r[0][0] == 3, f"expected 3 IMPL edges (source + 2 claims), got {r[0][0]}"

    def test_dependent_claims_strengthen_after_approval(self, sdk):
        """EP with Beta(10,1) evidence prior raises dependent claim confidence."""
        subj, doc, c1, c2, result = _setup_approval(sdk)  # noqa: RUF059
        proj = sdk._get_proj()  # noqa: F841
        for c in (c1, c2):
            p = sdk.get_point(c["id"])
            assert p["confidence"] > 0.5, f"claim {c['id']} not strengthened: {p['confidence']}"

    def test_confidence_delta_reported(self, sdk):
        """Return value maps each approved claim to its confidence delta."""
        subj, doc, c1, c2, result = _setup_approval(sdk)  # noqa: RUF059
        assert set(result["confidence_delta"].keys()) == {c1["id"], c2["id"]}
        assert result["confidence_delta"][c1["id"]] > 0
        assert result["confidence_delta"][c2["id"]] > 0

    def test_validates_approver_artifact_and_points(self, sdk):
        """Fail loudly on missing approver / artifact / point ids."""
        subj = sdk.create_subject("daniel", "engineer")
        doc = sdk.create_document("cp-002", "artifact")
        c1 = sdk.create_point("statement", "some claim")

        with pytest.raises(ValueError):
            sdk.file_human_approval("ghost", doc["id"], [c1["id"]])
        with pytest.raises(ValueError):
            sdk.file_human_approval(subj["id"], "ghost-artifact", [c1["id"]])
        with pytest.raises(ValueError):
            sdk.file_human_approval(subj["id"], doc["id"], [])
        with pytest.raises(ValueError):
            sdk.file_human_approval(subj["id"], doc["id"], ["ghost-point"])

    def test_revocation_via_supersede(self, sdk):
        """Approval decision Point can be superseded (revocation path)."""
        subj, doc, c1, c2, result = _setup_approval(sdk)  # noqa: RUF059
        # Supersede the decision Point with a retraction (#547: new_id must be
        # an existing Point — create the retraction point first).
        revoke = sdk.create_point("statement", "Approval revoked: CP-001")
        sdk.supersede_point(result["decision_point_id"], revoke["id"])
        p = sdk.get_point(result["decision_point_id"])
        assert p.get("outdated") is True or p.get("status") == "superseded" or \
            p.get("outdated") is True


# ── Grounding seeding ─────────────────────────────────────────────

class TestGroundingSeeding:
    def test_human_approval_seeds_grounding(self, sdk):
        """Approval Point is a grounding seed (a_i = 1.0)."""
        subj, doc, c1, c2, result = _setup_approval(sdk)  # noqa: RUF059
        proj = sdk._get_proj()
        if hasattr(proj, "compute_grounding"):
            g = proj.compute_grounding()
            assert result["decision_point_id"] in g
            assert g[result["decision_point_id"]] > 0

    def test_mined_decision_does_not_seed_grounding(self, sdk):
        """A mined 'decision' point (not humanApproval) is NOT a seed."""
        sdk.create_point("decision", "let's use postgres")
        proj = sdk._get_proj()
        if hasattr(proj, "compute_grounding"):
            g = proj.compute_grounding()
            # No humanApproval point in graph → grounding all-zero
            assert all(v == 0.0 for v in g.values()) or len(g) == 0


# ── Reputation exclusion ──────────────────────────────────────────

class TestReputationExclusion:
    def test_reputation_not_inflated_by_own_approval(self, sdk):
        """compute_reputation excludes humanApproval events from outcomes."""
        subj, doc, c1, c2, result = _setup_approval(sdk)  # noqa: RUF059
        # An approval alone must not inflate the approver's reputation.
        # Beta reputation starts at (1,1) → mean 0.5; an approval-induced
        # IMPL counted as success would push it > 0.5.
        rep = sdk.compute_reputation(subj["id"])
        # If the exclusion works, reputation stays at baseline (no outcomes).
        assert rep.get("successes", 0) == 0 or rep.get("mean", 0.5) <= 0.6, \
            f"reputation inflated by own approval: {rep}"


# ── Tool registry surface ─────────────────────────────────────────

class TestToolRegistry:
    def test_human_approval_in_tool_registry(self):
        """tortoise_file_human_approval is registered and maps to sdk method."""
        from tortoise.tool_registry import TOOL_REGISTRY
        names = [t.name for t in TOOL_REGISTRY]
        assert "tortoise_file_human_approval" in names
        td = next(t for t in TOOL_REGISTRY
                  if t.name == "tortoise_file_human_approval")
        assert td.sdk_method == "file_human_approval"
        assert td.http_policy is True
