"""Agent-ops rules-with-why — supersede EP cascade + failure-path tests
(issue #1933, epic #1891; test-design #1898 surface 11, E2E-4).

SDK-level integration (no HTTP, no network — offline EP):

- test_supersede_ep_cascade_retains_argument_tree: a rule grounded by a
  rationale argument tree (3 rationale Points + IMPL operators) is
  superseded → the old rule is superseded with a CORRECTS edge, the IMPL
  edges transfer to the new rule, the rationale Points are retained
  (argument tree survives), and the EP cascade re-propagates confidence
  (the new rule's posterior moves off its unmeasured prior).
- test_supersede_failure_path_contested_claims (E2E-4 negative b): when
  the post-supersede re-propagation fails (unreachable graph — simulated
  by a raising dream), get_contested_claims surfaces the new rule's
  ELEVATED variance (Beta(1,1) fallback ≈ 0.083 > 0.04); after a
  successful dream (control) the new rule is no longer contested.

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os
import tempfile
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest  # noqa: I001

from tortoise.sdk import TortoiseSDK

RULE_TEXT = "destructive actions require a verbal token acknowledgement"
NEW_RULE_TEXT = (
    "destructive actions require a verbal token acknowledgement before any "
    "destructive action"
)
RATIONALE_TEXTS = [
    "a prior incident of unacknowledged destructive action caused rollback loss",
    "unacknowledged actions bypass the operator oversight checkpoint",
    "the verbal token forces a deliberate pause before irreversible commands",
]


@pytest.fixture
def sdk():
    """Fresh isolated SDK (docker lane: redirects to a per-test server graph)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_aops_"), "test.db")
    sdk = TortoiseSDK(
        db_path, namespace=f"test_aops_{os.urandom(4).hex()}")
    yield sdk
    sdk.close()


def _build_rule_graph(sdk: TortoiseSDK) -> dict:
    """The rules-with-why graph: 3 rationale Points grounding a rule Point
    via unidirectional IMPL operators + explicit baselines (the argument
    tree). Returns the created point ids."""
    rationales = [
        sdk.create_point("rationale", text, status="live")
        for text in RATIONALE_TEXTS
    ]
    rule = sdk.create_point("statement", RULE_TEXT, status="live")
    for r in rationales:
        sdk.create_operator("IMPL", r["id"], [rule["id"]],
                            direction="unidirectional")
        # strong evidence-side priors — the EP factors have work to do
        sdk.set_point_baseline(r["id"], 20.0, 2.0)
    sdk.set_point_baseline(rule["id"], 2.0, 6.0)
    return {"rationales": rationales, "rule": rule}


def _confidence(sdk, point_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence",
        params={"id": point_id}).result_set
    return rows[0][0] if rows and rows[0][0] is not None else None


class TestSupersedeEPCascade:
    def test_supersede_ep_cascade_retains_argument_tree(self, sdk):
        """E2E-4 happy path (supersede leg): supersede re-propagates EP
        confidence and the old rule retains its argument tree — the
        rationale Points survive, the groundedIn IMPL edges transfer to the
        new rule, and the EP cascade moves the new rule's confidence off its
        unmeasured prior."""
        built = _build_rule_graph(sdk)
        rule, rationales = built["rule"], built["rationales"]
        pre = sdk.dream(dirty_only=True, max_hops=2,
                        require_calibration=False)
        assert pre["converged"] is True
        assert len(pre.get("affected_claims", [])) >= 4
        rule_conf_before = _confidence(sdk, rule["id"])
        assert rule_conf_before is not None

        # the agent rewrites the rule: new rule Point → supersede old → new
        new_rule = sdk.create_point("statement", NEW_RULE_TEXT, status="live")
        res = sdk.supersede_point(rule["id"], new_rule["id"])
        assert res["invalidated"] is True
        assert res["id"] == rule["id"]
        assert res["corrected_by"] == new_rule["id"]
        # every groundedIn IMPL edge transferred (the argument tree follows)
        assert res["edges_transferred"] == len(rationales), res

        g = sdk._get_proj().g
        # old rule is terminal (superseded + outdated) with a CORRECTS edge
        rows = g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
            params={"id": rule["id"]}).result_set
        status, outdated = rows[0]
        assert status == "superseded" and outdated is True, (status, outdated)
        n = g.query(
            "MATCH (n:Point {id:$new})-[:CORRECTS]->(o:Point {id:$old}) "
            "RETURN count(n)",
            params={"new": new_rule["id"], "old": rule["id"]}).result_set[0][0]
        assert n >= 1
        # the rationale Points are RETAINED (argument tree survives the
        # supersede — nothing is deleted)
        n = g.query(
            "MATCH (p:Point) WHERE p.pointKind = 'rationale' RETURN count(p)"
        ).result_set[0][0]
        assert n == len(rationales)
        # the groundedIn IMPL edges now serve the NEW rule
        n = g.query(
            "MATCH (op:Point {is_operator:true, op_type:'IMPL'})"
            "-[:IMPL {idx:1}]->(t:Point {id:$id}) RETURN count(DISTINCT op)",
            params={"id": new_rule["id"]}).result_set[0][0]
        assert n == len(rationales)

        # EP cascade: the dream after supersede re-propagates — the new
        # rule's confidence moves off its unmeasured prior (0.5 uniform)
        post = sdk.dream(dirty_only=True, max_hops=2,
                         require_calibration=False)
        assert post["converged"] is True
        assert new_rule["id"] in post.get("affected_claims", []), post
        new_conf = _confidence(sdk, new_rule["id"])
        assert new_conf is not None and abs(new_conf - 0.5) > 0.01, \
            f"EP cascade must move the new rule off the uniform prior: {new_conf}"
        # and the moved confidence sits in the grounded direction (rationales
        # support the rule → mean above the old rule's weak prior)
        assert new_conf > 0.5, new_conf

    def test_supersede_failure_path_contested_claims(self, sdk, monkeypatch):
        """E2E-4 negative (b): the supersede succeeds but the re-propagation
        FAILS (unreachable graph — the dream raises) → contested-claim
        detection surfaces the new rule's ELEVATED variance. Control: after
        a successful dream the new rule is no longer contested."""
        built = _build_rule_graph(sdk)
        rule = built["rule"]
        sdk.dream(dirty_only=True, max_hops=2, require_calibration=False)

        new_rule = sdk.create_point("statement", NEW_RULE_TEXT, status="live")
        res = sdk.supersede_point(rule["id"], new_rule["id"])
        assert res["invalidated"] is True

        # the re-propagation fails — the graph is unreachable (dream raises)
        def _boom(*_a, **_kw):
            raise ConnectionError("graph unreachable — EP re-propagation failed")

        monkeypatch.setattr(sdk, "dream", _boom)
        with pytest.raises(ConnectionError):
            sdk.dream(dirty_only=True, max_hops=2,
                      require_calibration=False)

        # contested-claim detection: the NEW rule (unmeasured — Beta(1,1)
        # fallback, variance 1/12 ≈ 0.083 > 0.04) surfaces; the strongly-
        # measured rationale tree does not
        ep = sdk._get_ep()
        contested = ep.get_contested_claims()
        by_id = {c["id"]: c for c in contested}
        assert new_rule["id"] in by_id, contested
        assert by_id[new_rule["id"]]["variance"] > 0.04
        for r in built["rationales"]:
            assert r["id"] not in by_id, r["id"]

        # control: the re-propagation recovers — after a successful dream the
        # new rule's variance drops below the contested threshold
        monkeypatch.undo()
        ok = sdk.dream(dirty_only=True, max_hops=2,
                       require_calibration=False)
        assert ok["converged"] is True
        post = {c["id"]: c for c in ep.get_contested_claims()}
        assert new_rule["id"] not in post, post
