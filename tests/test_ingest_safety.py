"""Track A E2E safety suite (epic #902 A8, issue #1058).

Owns the Track A E2E core not covered by the task-owned files:
  - E2E-1  — pointKind validation: `event` is NOT a write kind (rejected);
             kind-absent → default `statement`.
  - E2E-6  — crash-retry idempotency: the identical bundle re-submission
             converges (all-deduped, exactly-one artifacts).
  - E2E-8  — gated semantics: points stay draft (no silent promotion);
             an explicit `status:"live"` on a point item is a row-9
             violation.
  - E2E-13 — GATE-2 Q3 derived-liveness acceptance (CYCLE-26 FLIP): a
             gated + operator-requiring bundle COMMITS (the fail-closed
             rejection is retired); the operator is EP-inert with <2 live
             endpoints; the 1-live/1-draft boundary activates only after
             the second endpoint is promoted.
  - E2E-17 — read-surface reachability after ingest (recall/query).

Track B red-first (post-#780 — the sentinels `promote_source` in
create_operator + `promote_point` have shipped, so these RUN in the
track-b reporting job; they assert the post-#780 semantics):
  - E2E-3  — zombie-operator resolution: promote_point promotes incident
             DRAFT operator nodes once ALL their endpoints are live.
  - E2E-7  — promote_point draft→live (reviewer-gated semantics).

CI: registered in the explicit-file matrix; runs under `-m "not track_b"`
in default CI; the track-b job runs `-m track_b` explicitly.
"""
from __future__ import annotations

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.analyze import _bfs_select_operators

# ── Track B sentinels (§7 capability-probe sentinels) ────────────────
_INGEST_CAN_SUPPRESS_PROMOTION = (
    "promote_source" in inspect.signature(TortoiseSDK.create_operator).parameters)
_CAN_PROMOTE_POINT = hasattr(TortoiseSDK, "promote_point")
_TRACK_B_FULL = _INGEST_CAN_SUPPRESS_PROMOTION and _CAN_PROMOTE_POINT

TRACK_B = pytest.mark.track_b
TRACK_B_SKIP = pytest.mark.skipif(not _TRACK_B_FULL,
                                  reason="Track B (#780) machinery not shipped")


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_safety_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _query(sdk, cypher: str, params: dict | None = None):
    return sdk._get_proj().g.query(cypher, params=params or {}).result_set


def _count(sdk, cypher: str, params: dict | None = None) -> int:
    rows = _query(sdk, cypher, params)
    return int(rows[0][0]) if rows else 0


def _status(sdk, pid: str) -> str | None:
    rows = _query(sdk, "MATCH (n:Point {id:$id}) RETURN n.status",
                  {"id": pid})
    return rows[0][0] if rows else None


def _two_point_bundle(operator: str | None = "IMPL",
                      mitigation: bool = False) -> dict:
    conn: dict = {}
    if operator:
        conn = {"ref": "c1", "from": "p1", "to": "p2", "operator": operator}
        if mitigation:
            conn["mitigation"] = {"reason": "x", "strength": 0.6}
    return {
        "points": [
            {"ref": "p1", "kind": "claim", "content": "A implies B."},
            {"ref": "p2", "kind": "claim", "content": "B."},
        ],
        "entities": [], "sources": [],
        "connections": [conn] if conn else [],
    }


# ── E2E-1 — pointKind validation ─────────────────────────────────────

def test_e2e1_event_pointkind_rejected(sdk):
    """E2E-1 (CYCLE-25): pointKind 'event' is NOT a write kind — the item
    is rejected (episodic records are ENTITY items type:'event')."""
    from tortoise.exceptions import BundleValidationError
    bundle = {"points": [
        {"ref": "p1", "kind": "event", "eventKind": "launch",
         "content": "Launched."}],
        "entities": [], "sources": [], "connections": []}
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(bundle)
    msgs = " ".join(v["message"] for v in exc.value.violations)
    assert "event" in msgs.lower() or "kind" in msgs.lower(), msgs
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0, \
        "zero-mutation on the rejection"


def test_e2e1_statement_default(sdk):
    """E2E-1 (CYCLE-25): a point item WITHOUT a kind defaults to
    `statement`."""
    res = sdk.ingest({"points": [{"ref": "p1", "content": "A bare point."}],
                      "entities": [], "sources": [], "connections": []})
    pid = res["ids"]["points"][0]
    kind = _query(sdk, "MATCH (n:Point {id:$id}) RETURN n.pointKind",
                  {"id": pid})[0][0]
    assert kind == "statement", kind


# ── E2E-6 — crash-retry idempotency ──────────────────────────────────

def test_e2e6_identical_resubmission_all_deduped(sdk):
    """E2E-6: the identical bundle re-submission converges — created 0,
    deduped N, exactly ONE artifact per item (the exactly-once contract)."""
    bundle = _two_point_bundle(operator="IMPL")
    r1 = sdk.ingest(bundle)
    r2 = sdk.ingest(bundle)
    assert r1["batch_id"] == r2["batch_id"]
    assert r2["created"]["points"] == 0 and r2["deduped"]["points"] == 2
    assert r2["created"]["connections"] == 0
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 2
    assert _count(sdk, "MATCH ()-[r:IMPL]->() RETURN count(r)") == 1


def test_e2e6_crash_between_create_and_stamp_converges(sdk):
    """E2E-6 sub-position: a crash BETWEEN the point create and the
    batch_id stamp leaves a hash-less point; the retry's content+kind
    fallback scan absorbs it → exactly ONE point post-retry."""
    bundle = {"points": [{"ref": "p1", "kind": "claim",
                          "content": "Crash window point."}],
              "entities": [], "sources": [], "connections": []}
    r1 = sdk.ingest(bundle)
    pid = r1["ids"]["points"][0]
    # simulate the crash residue: drop the content_hash (the mid-function
    # crash leaves it NULL) — the fallback scan must still dedup
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) REMOVE n.content_hash", params={"id": pid})
    r2 = sdk.ingest(bundle)
    assert r2["deduped"]["points"] == 1, r2
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 1, \
        "exactly ONE point post-retry (content+kind fallback scan)"


# ── E2E-8 — gated semantics ──────────────────────────────────────────

def test_e2e8_gated_points_stay_draft_no_silent_promotion(sdk):
    """E2E-8: under the gated default, bundle points stay draft — NO
    silent promotion happens during ingest."""
    res = sdk.ingest(_two_point_bundle())
    for pid in res["ids"]["points"]:
        assert _status(sdk, pid) == "draft", f"{pid} must stay draft"


def test_e2e8_gated_status_live_violation(sdk):
    """E2E-8 (row 9): an explicit status:'live' on a point item under the
    gated policy is a violation — no bypass of the gated contract."""
    from tortoise.exceptions import BundleValidationError
    bundle = {"points": [
        {"ref": "p1", "kind": "claim", "content": "X.",
         "status": "live"}],
        "entities": [], "sources": [], "connections": []}
    with pytest.raises(BundleValidationError) as exc:
        sdk.ingest(bundle, promotion_policy="gated")
    msgs = " ".join(v["message"] for v in exc.value.violations)
    assert "gated" in msgs.lower() or "draft" in msgs.lower(), msgs
    assert _count(sdk, "MATCH (n:Point) RETURN count(n)") == 0


# ── E2E-13 — GATE-2 Q3 derived-liveness acceptance (CYCLE-26 FLIP) ───

def test_e2e13_gated_operator_commits_and_is_inert(sdk):
    """E2E-13 (CYCLE-26 FLIP): a gated + operator-requiring bundle COMMITS
    (the fail-closed rejection is RETIRED); the draft operator is EP-INERT
    with <2 live endpoints — the selector does not select it."""
    bundle = _two_point_bundle(operator="IMPL", mitigation=True)
    res = sdk.ingest(bundle, promotion_policy="gated")
    p1, p2 = res["ids"]["points"]
    op_id = res["ids"]["connections"][0]
    assert _status(sdk, op_id) == "draft", "gated operator is draft"
    # both endpoints draft → <2 live → the operator is EP-inert
    ops, _ = _bfs_select_operators(sdk._get_proj(), [p1, p2], max_hops=1)
    assert op_id not in ops, \
        "operator with <2 live endpoints must be EP-inert (GATE-2 Q3)"
    # endpoints untouched (draft — no promotion happened)
    assert _status(sdk, p1) == "draft" and _status(sdk, p2) == "draft"


def test_e2e13_boundary_promote_second_endpoint_activates(sdk):
    """E2E-13 boundary (NEW — GATE-2 Q3): 1-live/1-draft endpoint →
    operator inert (no EP contribution) → promote the second endpoint →
    the operator becomes EP-active (the derived-liveness predicate flips)."""
    bundle = _two_point_bundle(operator="IMPL", mitigation=True)
    res = sdk.ingest(bundle, promotion_policy="gated")
    p1, p2 = res["ids"]["points"]
    op_id = res["ids"]["connections"][0]
    # promote ONE endpoint → still 1 live / 1 draft → inert
    sdk.update_point(p1, status="live")
    ops, _ = _bfs_select_operators(sdk._get_proj(), [p1, p2], max_hops=1)
    assert op_id not in ops, "1-live/1-draft operator must stay inert"
    # promote the SECOND endpoint → 2 live → active (default-mode selection
    # still excludes the draft-status operator — the #780 live filter — so
    # also promote the operator node itself for the EP-active assertion)
    sdk.update_point(p2, status="live")
    sdk.update_point(op_id, status="live")
    ops2, _ = _bfs_select_operators(sdk._get_proj(), [p1, p2], max_hops=1)
    assert op_id in ops2, \
        "operator with 2 live endpoints + live status is EP-active"


# ── E2E-17 — read-surface reachability after ingest ──────────────────

def test_e2e17_read_surfaces_reachable_after_ingest(sdk):
    """E2E-17: after ingest, the read surfaces (recall_subgraph / query /
    list_batch) reach the ingested knowledge — the operator can
    query-back-verify what was written (J8 exit state)."""
    res = sdk.ingest(_two_point_bundle())
    pid = res["ids"]["points"][0]
    # recall: the ingested content is retrievable (the UC3 subgraph recall)
    hits = sdk.recall_subgraph("A implies B", max_nodes=10)
    ids = {n.get("id") for n in hits.get("nodes", [])} if isinstance(
        hits, dict) else set()
    assert pid in ids or len(ids) >= 1, \
        f"recall must reach ingested content: {str(hits)[:200]}"
    # list_batch (A13 surface): the stamped set is queryable
    audit = sdk.list_batch(res["batch_id"])
    assert audit["counts"]["points"] == 2, audit


# ── Track B (post-#780) — red-first, inert under -m "not track_b" ────

@TRACK_B
@TRACK_B_SKIP
def test_track_b_e2e3_zombie_operator_resolution(sdk):
    """E2E-3 (Track B): promote_point resolves ZOMBIE operators — incident
    DRAFT operator nodes go live ONCE ALL their endpoint Points are live
    (R16: a contradiction never stays a dead draft operator after its
    claims go live)."""
    bundle = _two_point_bundle(operator="IMPL", mitigation=True)
    res = sdk.ingest(bundle, promotion_policy="gated")
    p1, p2 = res["ids"]["points"]
    op_id = res["ids"]["connections"][0]
    assert _status(sdk, op_id) == "draft"
    # promote BOTH endpoints via promote_point → the incident draft
    # operator auto-promotes (zombie resolution)
    sdk.promote_point(p1)
    assert _status(sdk, op_id) == "draft", "one live endpoint is not enough"
    sdk.promote_point(p2)
    assert _status(sdk, op_id) == "live", \
        "zombie operator must resolve once ALL endpoints are live"


@TRACK_B
@TRACK_B_SKIP
def test_track_b_e2e7_promote_point_reviewer_gated(sdk):
    """E2E-7.1 (Track B): promote_point is the reviewer-gated draft→live
    path — draft→live with the reviewed flag; already-live is a NO-OP."""
    res = sdk.ingest(_two_point_bundle())
    pid = res["ids"]["points"][0]
    out = sdk.promote_point(pid)
    assert out["status"] == "live" and out["promoted"] is True
    # already-live → NO-OP
    out2 = sdk.promote_point(pid)
    assert out2["promoted"] is False and out2["blocked"] is False
    assert out2.get("reason") == "already_live"
