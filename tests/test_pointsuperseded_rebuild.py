"""#2423 (issue #2423) — PointSuperseded rebuild-parity suite.

The Object-side analog (#2164) fixed ObjectSuperseded rebuild replay; the
Point side had NO PointSuperseded branch in the projection rebuild chain at
all — a superseded Point resurrected as live after rebuild_all, lost its
validTo/expiredAt/outdated stamps + CORRECTS edge, and re-materialized its
transferred operator/direct edges at the OLD point (live supersede's
transfer is CREATE+DELETE graph mutation only; operator.inputs is never
updated and DirectEdgeRepoint descriptors had no replay consumer).

Fix shape (mirrors #2164): pass-1b deferred-trailing-sweep fold
(status/validity/CORRECTS) + pass-2b re-point replay (operator edges
old→final-live-successor per the transfer semantics; DirectEdgeRepoint
descriptor replay). Both order-independent per the #2249 contract.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_pointsuperseded_rebuild.py -q
"""
from __future__ import annotations

import os

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sup(tmp_path):
    """(db, events_dir, sdk) with the journal wired."""
    db = os.path.join(str(tmp_path), "ps.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _rebuild(sdk, events_dir) -> None:
    sdk._get_proj().rebuild_all(str(events_dir))


def _sem_edges(proj, pid: str) -> set:
    """Operator-mediated typed edges INTO pid (IMPL/NAND/hasPart) — the
    transferred set live supersede moves to the successor."""
    return {tuple(r) for r in proj.g.query(
        "MATCH (op:Point {is_operator:true})-[r]->(p:Point {id:$id}) "
        "RETURN type(r), p.id, r.idx",
        params={"id": pid}).result_set}


def _direct_edges(proj, pid: str) -> set:
    """Operator-less direct IMPL/NAND edges incident to pid (both
    directions), excluding operator endpoints (E2E-11.6 shape)."""
    out = {tuple(r) for r in proj.g.query(
        "MATCH (p:Point {id:$id})-[r:IMPL|NAND]->(x) "
        "WHERE NOT coalesce(x.is_operator, false) RETURN type(r), x.id",
        params={"id": pid}).result_set}
    inn = {tuple(r) for r in proj.g.query(
        "MATCH (x)-[r:IMPL|NAND]->(p:Point {id:$id}) "
        "WHERE NOT coalesce(x.is_operator, false) RETURN type(r), x.id",
        params={"id": pid}).result_set}
    return out | inn


def _corr(proj, old_id: str, new_id: str) -> int:
    return proj.g.query(
        "MATCH (a:Point {id:$new})-[r:CORRECTS]->(b:Point {id:$old}) "
        "RETURN count(r)",
        params={"new": new_id, "old": old_id}).result_set[0][0]


def _point_state(sdk, pid: str) -> dict:
    p = sdk.get_point(pid) or {}
    return {k: p.get(k) for k in
            ("status", "outdated", "validTo", "expiredAt")}


# ═══════════════════════════════════════════════════════════════════════
# Indicator 1 + 4: status/validity/CORRECTS fold — supersede → rebuild →
# query cycle returns identical state (no resurrection)
# ═══════════════════════════════════════════════════════════════════════

def test_superseded_point_stays_superseded_after_rebuild(sup):
    """The core P1: a superseded Point must NOT resurrect as live on
    rebuild. Status + outdated flag + bi-temporal window stamps + CORRECTS
    edge survive the JSONL wipe+replay."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "old A", status="live")["id"]
    succ = sdk.create_point("statement", "successor A'", status="live",
                            valid_from="2026-01-01T00:00:00+00:00")["id"]
    sdk.supersede_point(a, succ)
    proj = sdk._get_proj()
    pre = _point_state(sdk, a)
    assert pre["status"] == "superseded"
    assert pre["outdated"] is True
    assert pre["validTo"]  # window END stamped
    assert pre["expiredAt"]
    assert _corr(proj, a, succ) == 1
    _rebuild(sdk, events)
    post = _point_state(sdk, a)
    assert post == pre, (
        f"superseded point state drifted across rebuild: {pre} != {post}")
    assert _corr(proj, a, succ) == 1, "CORRECTS edge lost on rebuild"


def test_supersede_chain_each_link_stays_superseded(sup):
    """A→B→C chain: BOTH superseded links re-fold independently (each event
    folds its own target) — A superseded by B, B superseded by C, C live."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    c = sdk.create_point("statement", "C", status="live")["id"]
    sdk.supersede_point(a, b)
    sdk.supersede_point(b, c)
    proj = sdk._get_proj()
    pre = {pid: _point_state(sdk, pid)["status"] for pid in (a, b, c)}
    assert pre == {a: "superseded", b: "superseded", c: "live"}
    pre_corr = {_corr(proj, x, y) for x, y in ((a, b), (b, c))}
    assert pre_corr == {1}
    _rebuild(sdk, events)
    post = {pid: _point_state(sdk, pid)["status"] for pid in (a, b, c)}
    assert post == pre
    assert {_corr(proj, x, y) for x, y in ((a, b), (b, c))} == pre_corr


# ═══════════════════════════════════════════════════════════════════════
# Indicator 2 + 4: operator edge re-point — zero semantic edges incident
# to the superseded old point; successor holds the transferred edges
# ═══════════════════════════════════════════════════════════════════════

def test_operator_edge_repoints_to_successor_on_rebuild(sup):
    """Live supersede transfers operator edges old→successor by graph
    mutation only; rebuild pass-2 recreates them from operator snapshots
    (stale operator.inputs naming OLD). Pass-2b must re-point them back —
    zero semantic edges incident to A, successor holds the transferred
    IMPL edge, idx preserved."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    succ = sdk.create_point("statement", "A'", status="live")["id"]
    sdk.create_operator("IMPL", a, [b])
    sdk.supersede_point(a, succ)
    proj = sdk._get_proj()
    pre_old = _sem_edges(proj, a)
    pre_new = _sem_edges(proj, succ)
    assert not pre_old, "live supersede left a semantic edge on old"
    assert pre_new, "successor should hold the transferred edge live"
    _rebuild(sdk, events)
    post_old = _sem_edges(proj, a)
    post_new = _sem_edges(proj, succ)
    assert not post_old, (
        "operator edge re-materialized at superseded point after rebuild — "
        "resurrection (indicator 2)")
    assert post_new == pre_new, (
        "transferred operator edge drifted post-rebuild")


def test_operator_edge_repoints_through_chain_to_final_successor(sup):
    """A→B→C chain: an operator edge on A ends on the FINAL live successor
    C post-rebuild (transitive resolution — not stranded on the
    intermediate terminal B). The operator's OTHER input X is untouched in
    both live and rebuild (only the superseded A edge transfers)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    c = sdk.create_point("statement", "C", status="live")["id"]
    x = sdk.create_point("statement", "X", status="live")["id"]
    sdk.create_operator("IMPL", a, [x])
    sdk.supersede_point(a, b)
    sdk.supersede_point(b, c)
    proj = sdk._get_proj()
    pre_x = _sem_edges(proj, x)
    pre_c = _sem_edges(proj, c)
    assert not _sem_edges(proj, a) and not _sem_edges(proj, b)
    assert pre_c  # live: transferred edge ended on C (via B)
    _rebuild(sdk, events)
    assert not _sem_edges(proj, a), "edge stranded on superseded A"
    assert not _sem_edges(proj, b), "edge stranded on intermediate terminal B"
    assert _sem_edges(proj, c) == pre_c, "edge must end on final successor C"
    assert _sem_edges(proj, x) == pre_x, "untouched input edge drifted"


# ═══════════════════════════════════════════════════════════════════════
# Indicator 2 + E2E-11.6: DirectEdgeRepoint descriptor replay — transferred
# direct edges survive rebuild at the successor with their attrs
# ═══════════════════════════════════════════════════════════════════════

def test_direct_edge_repoints_and_keeps_attrs_on_rebuild(sup):
    """A→B direct (operator-less) IMPL transferred to A' on supersede; the
    DirectEdgeRepoint descriptor (the ONLY durable record of the transfer —
    direct edges have no operator snapshot) is replayed by pass-2b: the
    edge survives at the successor with confidence/weight/label attrs, and
    zero direct edges remain incident to the superseded A (E2E-11.6)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    succ = sdk.create_point("statement", "A'", status="live")["id"]
    sdk.create_direct_edge("IMPL", a, b, confidence=0.8, weight=0.5,
                           label="supports")
    sdk.supersede_point(a, succ)
    proj = sdk._get_proj()
    attrs_pre = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:IMPL]->(x) "
        "RETURN x.id, r.confidence, r.weight, r.label",
        params={"id": succ}).result_set
    assert attrs_pre, "successor should hold the direct edge live"
    _rebuild(sdk, events)
    assert not _direct_edges(proj, a), (
        "direct edge re-materialized at superseded point post-rebuild")
    attrs_post = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:IMPL]->(x) "
        "RETURN x.id, r.confidence, r.weight, r.label",
        params={"id": succ}).result_set
    assert attrs_post == attrs_pre, (
        "direct-edge repoint lost attrs across rebuild")


def test_direct_edge_chain_resolves_to_final_successor(sup):
    """A→B direct edge; A superseded by A1 then A1 by A2 — TWO repoint
    descriptors journaled. Replay must resolve BOTH to the final live
    successor A2 (order-independent collapse), not leave the intermediate
    A1 edge (phantom)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    a1 = sdk.create_point("statement", "A1", status="live")["id"]
    a2 = sdk.create_point("statement", "A2", status="live")["id"]
    sdk.create_direct_edge("IMPL", a, b)
    sdk.supersede_point(a, a1)
    sdk.supersede_point(a1, a2)
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    assert not _direct_edges(proj, a)
    assert not _direct_edges(proj, a1), "edge stranded at intermediate"
    assert _direct_edges(proj, a2), "edge should end on final successor A2"


# ═══════════════════════════════════════════════════════════════════════
# Carve-outs (#1080 / order-faithful): alreadyDecided + post-supersede ops
# ═══════════════════════════════════════════════════════════════════════

def test_already_decided_operator_stays_attached_to_superseded_prior(sup):
    """#1080: an alreadyDecided operator declares the OLD decision a
    duplicate — its edge must stay on the superseded prior (live supersede
    skips it; rebuild pass-2b must too)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "prior A", status="live")["id"]
    succ = sdk.create_point("statement", "kept A'", status="live")["id"]
    sdk.create_operator(
        "IMPL", succ, [a], label="alreadyDecided",
        direction="unidirectional")
    sdk.supersede_point(a, succ)
    proj = sdk._get_proj()
    pre = _sem_edges(proj, a)
    assert pre, "alreadyDecided edge should point at the prior live"
    _rebuild(sdk, events)
    post = _sem_edges(proj, a)
    assert post == pre, (
        "alreadyDecided dedup-context edge must not re-point to successor")


def test_operator_created_after_supersede_keeps_terminal_link(sup):
    """create_operator has no terminal guard — an operator created AFTER a
    supersede targeting the terminal old point legitimately keeps its link.
    The re-point is order-faithful (OperatorAdded seq vs PointSuperseded
    seq): a post-supersede operator must NOT be dragged to the successor."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    succ = sdk.create_point("statement", "A'", status="live")["id"]
    sdk.supersede_point(a, succ)
    sdk.create_operator("IMPL", a, [b])
    proj = sdk._get_proj()
    pre = _sem_edges(proj, a)
    assert pre, "post-supersede operator edge should point at old live"
    _rebuild(sdk, events)
    post = _sem_edges(proj, a)
    assert post == pre, (
        "post-supersede operator must keep its terminal link on rebuild")
    assert not _sem_edges(proj, succ)


def test_rebuild_is_idempotent_for_supersession_state(sup):
    """Indicator 4: rebuild → rebuild converges — the second replay leaves
    the graph identical to the first (fold + re-point idempotent)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    succ = sdk.create_point("statement", "A'", status="live")["id"]
    sdk.create_operator("IMPL", a, [b])
    sdk.create_direct_edge("IMPL", a, b)
    sdk.supersede_point(a, succ)
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    snapshot1 = {
        "a": _point_state(sdk, a), "succ": _point_state(sdk, succ),
        "sem_a": _sem_edges(proj, a), "sem_succ": _sem_edges(proj, succ),
        "dir_a": _direct_edges(proj, a), "dir_succ": _direct_edges(proj, succ),
        "corr": _corr(proj, a, succ),
    }
    _rebuild(sdk, events)
    snapshot2 = {
        "a": _point_state(sdk, a), "succ": _point_state(sdk, succ),
        "sem_a": _sem_edges(proj, a), "sem_succ": _sem_edges(proj, succ),
        "dir_a": _direct_edges(proj, a), "dir_succ": _direct_edges(proj, succ),
        "corr": _corr(proj, a, succ),
    }
    assert snapshot2 == snapshot1, "second rebuild drifted from first"


def test_id_reuse_double_supersede_no_parallel_edges(sup):
    """Review P2-3: two PointSuperseded folds for the SAME old id (a raw
    producer reusing an id across a delete+recreate between supersedes)
    both resolve the same pass-2 (op)-[type{idx}]->(old) edge to the same
    final successor. The pass-2b re-point must dedupe on (op, type, idx,
    final) so rebuild does not mint a parallel duplicate operator edge
    (double EP weight) or a ghost CORRECTS pair."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    s1 = sdk.create_point("statement", "S1", status="live")["id"]
    s2 = sdk.create_point("statement", "S2", status="live")["id"]
    op = sdk.create_operator("IMPL", a, [b])["id"]
    sdk.supersede_point(a, s1)
    # The SDK terminal guard blocks a live second supersede of the same old
    # id, so journal the second fold directly the way a raw id-reusing
    # producer would (a second PointSuperseded event naming the SAME id).
    import datetime
    import json
    with open(events / "events.jsonl", "a") as fh:
        line = {
            "event_id": sdk.ulid(), "ts": datetime.datetime.now(
                datetime.UTC).isoformat(),
            "type": "PointSuperseded", "initiated_by": "sdk",
            "projection_version": 2, "id": a, "new_id": s2,
        }
        fh.write(json.dumps(line) + "\n")
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    from collections import Counter
    op_edges = proj.g.query(
        "MATCH (o:Point {id:$id})-[r]->(x) RETURN type(r), x.id",
        params={"id": op}).result_set
    targets = Counter(r[1] for r in op_edges if r[0] in ("IMPL", "NAND"))
    assert targets[s2] == 1, f"parallel operator edge on successor: {targets}"
    assert not targets[a], "old point kept a semantic edge"
    assert _sem_edges(proj, a) == set()
    # CORRECTS: only the FINAL successor S2 links old A; the first fold's
    # edge (S1→A) was superseded along with the S1 node's own lifecycle and
    # must not survive as a live pair against the reused id.
    assert _corr(proj, a, s2) == 1
    assert _corr(proj, a, s1) == 0, "ghost CORRECTS from the earlier fold"
