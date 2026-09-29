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

import json
import os
from datetime import UTC, datetime

import pytest

from tortoise.log import EventLog
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


# ═══════════════════════════════════════════════════════════════════════
# #2489 Structural-edge (2b) transfer parity — journaled DirectEdgeRepoint
# replay of supersede's structural-edge transfer (extractedFrom + about*).
# Live supersede 2b MERGE+DELETEs structural edges with no event; rebuild
# pass-2 resurrects them at OLD from the point's immutable snapshot
# (_upsert_point_edges — extractedFrom prop / aboutEntities list) while the
# successor loses them. The fix journals flat DirectEdgeRepoint descriptors
# {src, tgt=<key>, target_label, edge_type} at 2b and replays them in
# pass-2b (delete the resurrection at old + create at the final successor).
# ═══════════════════════════════════════════════════════════════════════

_STRUCT_RELS = ("aboutSubject", "aboutObject", "aboutEvent", "aboutPoint",
                "aboutDocument", "extractedFrom")


def _struct_edges(proj, pid: str) -> set:
    """Structural edges out of a Point: rel → target key (name/title/url) —
    the transferred set live supersede 2b moves to the successor."""
    return {tuple(r) for r in proj.g.query(
        "MATCH (p:Point {id:$id})-[r]->(t) "
        "WHERE type(r) IN $rels "
        "RETURN type(r), coalesce(t.name, t.title, t.url)",
        params={"id": pid, "rels": list(_STRUCT_RELS)}).result_set}


def _descriptors(events) -> list[dict]:
    """DirectEdgeRepoint descriptors journaled so far (structural 2b + 2a)."""
    import json
    out = []
    with open(events / "events.jsonl") as fh:
        for line in fh:
            ev = json.loads(line)
            if ev.get("type") == "DirectEdgeRepoint":
                out.append(ev)
    return out


# ── Parity via extractedFrom: A→B→C chain, old-side zero-incident ──

def test_extracted_from_transfer_parity_chain(sup):
    """extractedFrom A→B→C: post-rebuild the FINAL successor C holds the
    edge and every superseded old (A, B) is clean — the descriptor replay's
    delete-leg removes the pass-2 resurrection at A and the create-leg lands
    the edge on the transitively-final successor (E2E-11.6 mirror for the
    structural family)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live",
                         extractedFrom="https://ex.test/s1")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    c = sdk.create_point("statement", "C", status="live")["id"]
    sdk.supersede_point(a, b)
    sdk.supersede_point(b, c)
    proj = sdk._get_proj()
    pre_c = _struct_edges(proj, c)
    assert pre_c == {("extractedFrom", "https://ex.test/s1")}, \
        "live chain transfer should end the edge on C"
    assert not _struct_edges(proj, a) and not _struct_edges(proj, b)
    descs = _descriptors(events)
    assert {(d["src"], d["edge_type"], d["tgt"])
            for d in descs} == {(a, "extractedFrom", "https://ex.test/s1"),
                                (b, "extractedFrom", "https://ex.test/s1")}, \
        "one descriptor per (old, rel, target) transfer"
    _rebuild(sdk, events)
    assert not _struct_edges(proj, a), \
        "extractedFrom resurrected at superseded A after rebuild"
    assert not _struct_edges(proj, b)
    post_c = _struct_edges(proj, c)
    assert post_c == pre_c, (
        "transferred extractedFrom drifted post-rebuild")
    # exactly ONE edge C→Source survives the double descriptor replay
    n = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:extractedFrom]->(s) RETURN count(r)",
        params={"id": c}).result_set[0][0]
    assert n == 1, f"parallel extractedFrom edges on C: {n}"


# ── REBUILD→supersede→REBUILD lane (where 2b sees live about edges) ──

def test_about_edge_follows_successor_after_second_rebuild(sup):
    """rebuild 1 resurrects X-[:aboutSubject]->(Subject) from X's snapshot;
    supersede transfers it (descriptor journaled); rebuild 2 replays: the
    about edge follows the successor AND old X carries no about edge after
    the 2nd rebuild (the only lane where 2b sees live about edges)."""
    _, events, sdk = sup
    sdk.create_subject("ParitySubj", "org")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["ParitySubj"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    assert _struct_edges(proj, x) == {("aboutSubject", "ParitySubj")}, \
        "rebuild 1 should resurrect X's aboutSubject edge"
    sdk.supersede_point(x, succ)
    assert not _struct_edges(proj, x)
    assert _struct_edges(proj, succ) == {("aboutSubject", "ParitySubj")}
    _rebuild(sdk, events)
    assert not _struct_edges(proj, x), (
        "about edge re-materialized at old X after the 2nd rebuild")
    assert _struct_edges(proj, succ) == {("aboutSubject", "ParitySubj")}, \
        "about edge must follow the successor after the 2nd rebuild"


# ── No-self-edge guard (2b was missing it): delete_only descriptor ──

def test_no_self_edge_guard_delete_only_descriptor(sup):
    """2b's missing tid==new guard: X has a LIVE (X)-[:aboutPoint]->(Y)
    edge whose target IS the successor Y. supersede(X→Y) must delete the
    edge WITHOUT minting the (Y)-[:aboutPoint]->(Y) phantom self-edge, and
    journal a DELETE-ONLY descriptor so a later rebuild deletes the
    pass-2-resurrected phantom at old X (old's snapshot re-mints the edge
    on every rebuild; without the descriptor nothing removes it). Assert
    both live and post-rebuild states."""
    _, events, sdk = sup
    y = sdk.create_point("statement", "Y content", status="live",
                         name="SelfTarget")["id"]
    x = sdk.create_point("statement", "X about Y", status="live",
                         aboutEntities=["SelfTarget"])["id"]
    proj = sdk._get_proj()
    # live id-targeted aboutPoint edge X→Y (create_about_edge wires by id)
    assert proj.create_about_edge(x, y, "aboutPoint")
    assert _struct_edges(proj, x) == {("aboutPoint", "SelfTarget")}
    sdk.supersede_point(x, y)
    # LIVE assertions: guard fired — old edge deleted, NO self-edge on Y
    assert not _struct_edges(proj, x), "X's aboutPoint edge should be deleted"
    assert not _struct_edges(proj, y), (
        "guard must not mint a (Y)-[:aboutPoint]->(Y) phantom self-edge")
    descs = _descriptors(events)
    self_desc = [d for d in descs if d.get("delete_only") is True]
    assert len(self_desc) == 1, f"expected one delete_only descriptor: {descs}"
    assert self_desc[0]["src"] == x and self_desc[0]["tgt"] == y
    assert self_desc[0]["edge_type"] == "aboutPoint"
    assert self_desc[0]["target_label"] == "Point"
    # REBUILD 2: delete_only replay — old X keeps NO aboutPoint edge; the
    # descriptor's literal-tgt delete-leg keys the DIRECT successor Y.
    _rebuild(sdk, events)
    assert not {e for e in _struct_edges(proj, x)
                if e[0] == "aboutPoint"}, (
        "old X must carry no aboutPoint edge post-rebuild")
    assert not {e for e in _struct_edges(proj, y)
                if e[0] == "aboutPoint"}, \
        "Y must carry no aboutPoint self-edge post-rebuild"


def test_no_self_edge_guard_chain_delete_keys_direct_successor(sup):
    """Chain-gated guard: supersede(X→Y) with the X-[:aboutPoint]->Y phantom
    (delete_only descriptor tgt=Y), THEN supersede(Y→Z) before the rebuild.
    The delete-leg keys the DIRECT successor (literal tgt Y), and since Y was
    itself superseded it ALSO follows the succ map to Z — old X ends clean,
    Y has no self-edge, Z is unaffected."""
    _, events, sdk = sup
    y = sdk.create_point("statement", "Y content", status="live",
                         name="ChainTarget")["id"]
    x = sdk.create_point("statement", "X about Y", status="live",
                         aboutEntities=["ChainTarget"])["id"]
    z = sdk.create_point("statement", "Z content", status="live")["id"]
    proj = sdk._get_proj()
    assert proj.create_about_edge(x, y, "aboutPoint")
    sdk.supersede_point(x, y)
    sdk.supersede_point(y, z)   # direct successor Y now superseded → Z
    assert not _struct_edges(proj, x)
    assert not _struct_edges(proj, y)
    assert not _struct_edges(proj, z)
    _rebuild(sdk, events)
    for pid, who in ((x, "old X"), (y, "intermediate Y"), (z, "final Z")):
        assert not {e for e in _struct_edges(proj, pid)
                    if e[0] == "aboutPoint"}, \
            f"{who} must carry no aboutPoint edge post-rebuild"
    assert not _struct_edges(proj, z), "Z unaffected by the X-side phantom"


# ── Mixed 2a+2b supersede: operator + structural edges both replay ──

def test_mixed_operator_and_structural_transfer_replays(sup):
    """One supersede transferring BOTH an operator edge (2a) and a structural
    extractedFrom edge (2b): rebuild replays both — successor holds the IMPL
    (from its operator) AND the extractedFrom edge; old X is clean of both."""
    _, events, sdk = sup
    x = sdk.create_point("statement", "X", status="live",
                         extractedFrom="https://ex.test/mixed")["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    tgt = sdk.create_point("statement", "T", status="live")["id"]
    sdk.create_operator("IMPL", x, [tgt])
    sdk.supersede_point(x, succ)
    proj = sdk._get_proj()
    pre_sem = _sem_edges(proj, succ)
    pre_struct = _struct_edges(proj, succ)
    assert pre_sem and pre_struct == {("extractedFrom", "https://ex.test/mixed")}
    _rebuild(sdk, events)
    assert not _sem_edges(proj, x), "operator edge resurrected at old X"
    assert not _struct_edges(proj, x), "structural edge resurrected at old X"
    assert _sem_edges(proj, succ) == pre_sem
    assert _struct_edges(proj, succ) == pre_struct


# ── Multi-edge: dedupe keyed (src, edge_type, resolved target node) ──

def test_two_sources_share_one_target_both_survive(sup):
    """X1→S and X2→S (distinct sources, one shared Subject target): the
    dedupe key (src, edge_type, resolved-target-id) keeps BOTH — per-src
    keys never collapse; each successor holds its own about edge."""
    _, events, sdk = sup
    sdk.create_subject("SharedSubj", "org")
    x1 = sdk.create_point("statement", "X1", status="live",
                          aboutEntities=["SharedSubj"])["id"]
    x2 = sdk.create_point("statement", "X2", status="live",
                          aboutEntities=["SharedSubj"])["id"]
    s1 = sdk.create_point("statement", "S1", status="live")["id"]
    s2 = sdk.create_point("statement", "S2", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)  # resurrect X1/X2 about edges
    sdk.supersede_point(x1, s1)
    sdk.supersede_point(x2, s2)
    _rebuild(sdk, events)
    assert _struct_edges(proj, s1) == {("aboutSubject", "SharedSubj")}
    assert _struct_edges(proj, s2) == {("aboutSubject", "SharedSubj")}
    assert not _struct_edges(proj, x1) and not _struct_edges(proj, x2)


def test_multi_target_same_rel_not_collapsed_by_descriptor_id(sup):
    """X has TWO aboutSubject edges (distinct Subject targets, same rel):
    both descriptors share the f\"{old}->{new}:{rel}\" id base — dedupe is
    keyed on (src, edge_type, RESOLVED node id), never descriptor id, so
    both edges survive to the successor."""
    _, events, sdk = sup
    sdk.create_subject("SubjOne", "org")
    sdk.create_subject("SubjTwo", "org")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["SubjOne", "SubjTwo"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    assert len(_struct_edges(proj, x)) == 2, \
        "X should resurrect BOTH aboutSubject edges"
    sdk.supersede_point(x, succ)
    _rebuild(sdk, events)
    assert _struct_edges(proj, succ) == {("aboutSubject", "SubjOne"),
                                         ("aboutSubject", "SubjTwo")}, \
        "both same-rel targets must survive; descriptor-id dedupe would collapse"
    assert not _struct_edges(proj, x)


# ── aboutDocument-title keyed resolution ──

def test_about_document_title_keyed_resolution(sup):
    """A document stores its display name in `title` (#211) and its identity
    in `url` (D10, ONTOLOGY v3.15 §4.4): live auto-detect matches a
    document-bearing Source by url OR title, while the replay descriptor is
    keyed `url`. X-[:aboutDocument]->(Source title-only) follows the
    successor. create_document does NOT journal DocumentCreated (the #2296
    durability audit tracks that surface) — the fixture journals the line the
    way the document-index path does, so the rebuild can re-create the
    document :Source."""
    import datetime
    import json
    _, events, sdk = sup
    doc = sdk.create_document("DocTitleOnly", "report")
    with open(events / "events.jsonl", "a") as fh:
        line = {
            "event_id": sdk.ulid(),
            "ts": datetime.datetime.now(datetime.UTC).isoformat(),
            "type": "DocumentCreated", "initiated_by": "sdk",
            "projection_version": 2, "id": doc["id"],
            "title": "DocTitleOnly", "document_kind": "report",
            "objectKind": "document", "status": "draft",
        }
        fh.write(json.dumps(line) + "\n")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["DocTitleOnly"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    assert _struct_edges(proj, x) == {("aboutDocument", "DocTitleOnly")}
    sdk.supersede_point(x, succ)
    _rebuild(sdk, events)
    assert _struct_edges(proj, succ) == {("aboutDocument", "DocTitleOnly")}
    assert not _struct_edges(proj, x)
    # the edge lands on the document :Source (label-scoped), matched by title
    n = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:aboutDocument]->(d:Source) "
        "WHERE d.title = $title RETURN count(r)",
        params={"id": succ, "title": "DocTitleOnly"}).result_set[0][0]
    assert n == 1, f"aboutDocument should terminate on the document Source: {n}"


# ── Unresolvable-key skips at emission (zero regression) ──

def test_name_less_about_point_target_skipped_at_emission(sup):
    """A name-less Point target (id-targeted create_about_edge — Points only
    carry `name` if a producer set it) has no replayable key: emission SKIPS
    the descriptor (no crash); live transfer still moves the edge."""
    _, events, sdk = sup
    w = sdk.create_point("statement", "W nameless", status="live")["id"]
    x = sdk.create_point("statement", "X", status="live")["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    assert proj.create_about_edge(x, w, "aboutPoint")
    r = sdk.supersede_point(x, succ)
    assert r["edges_transferred"] >= 1
    descs = _descriptors(events)
    assert not [d for d in descs if d.get("edge_type") == "aboutPoint"], \
        "name-less target must not emit a (null-key) descriptor"
    assert not _struct_edges(proj, x)
    _rebuild(sdk, events)  # no crash
    assert not {e for e in _struct_edges(proj, x) if e[0] == "aboutPoint"}


def test_url_less_source_target_skipped_at_emission(sup):
    """A Source node without `url` (raw producer) is not keyed by anything
    replayable: emission skips the descriptor — no crash, live transfer still
    runs (zero regression: those edges die at rebuild today anyway)."""
    _, events, sdk = sup
    x = sdk.create_point("statement", "X", status="live")["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    # raw Source keyed only by id + raw extractedFrom edge (never journaled)
    proj.g.query("CREATE (s:Source {id:'raw-src-1'})")
    src_id = proj.g.query(
        "MATCH (s:Source {id:'raw-src-1'}) RETURN ID(s)").result_set[0][0]
    proj.g.query(
        "MATCH (x:Point {id:$x}), (s) WHERE ID(s) = $sid "
        "MERGE (x)-[:extractedFrom]->(s)",
        params={"x": x, "sid": src_id})
    r = sdk.supersede_point(x, succ)
    assert r["edges_transferred"] >= 1
    descs = _descriptors(events)
    assert not [d for d in descs
                if d.get("edge_type") == "extractedFrom"], \
        "url-less target must not emit a (null-key) descriptor"
    _rebuild(sdk, events)  # no crash


# ── Double-rebuild idempotence ──

def test_structural_replay_is_idempotent_across_rebuilds(sup):
    """rebuild → rebuild converges: the second replay leaves the structural
    edges identical to the first (delete-leg + MERGE create idempotent)."""
    _, events, sdk = sup
    sdk.create_subject("IdemSubj", "org")
    x = sdk.create_point("statement", "X", status="live",
                         extractedFrom="https://ex.test/idem",
                         aboutEntities=["IdemSubj"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    sdk.supersede_point(x, succ)
    _rebuild(sdk, events)
    snap1 = {"old": _struct_edges(proj, x), "succ": _struct_edges(proj, succ)}
    _rebuild(sdk, events)
    snap2 = {"old": _struct_edges(proj, x), "succ": _struct_edges(proj, succ)}
    assert snap2 == snap1, "second rebuild drifted from first"
    assert snap2["old"] == set()
    assert snap2["succ"] == {("aboutSubject", "IdemSubj"),
                             ("extractedFrom", "https://ex.test/idem")}


# ── id-reuse: duplicate descriptor dedupe on resolved node identity ──

def test_structural_id_reuse_duplicate_descriptor_dedupe(sup):
    """P2-3 mirror for structural edges: two supersede folds for the SAME old
    id journal duplicate structural descriptors. Rebuild dedupes on (src,
    edge_type, resolved node identity) — no parallel edge on the final
    successor — and the old-side delete-leg still runs for the duplicate."""
    _, events, sdk = sup
    sdk.create_subject("ReuseSubj", "org")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["ReuseSubj"])["id"]
    s1 = sdk.create_point("statement", "S1", status="live")["id"]
    s2 = sdk.create_point("statement", "S2", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)  # resurrect X's about edge
    sdk.supersede_point(x, s1)
    # Duplicate the FIRST fold's descriptor (a raw id-reusing producer's
    # second PointSuperseded journals the transfer again) — append the raw
    # fold + a byte-identical descriptor.
    import datetime
    import json
    first_desc = next(d for d in _descriptors(events)
                      if d.get("edge_type") == "aboutSubject")
    with open(events / "events.jsonl", "a") as fh:
        fold2 = {
            "event_id": sdk.ulid(), "ts": datetime.datetime.now(
                datetime.UTC).isoformat(),
            "type": "PointSuperseded", "initiated_by": "sdk",
            "projection_version": 2, "id": x, "new_id": s2,
        }
        dup_desc = {k: v for k, v in first_desc.items()}
        dup_desc["event_id"] = sdk.ulid()
        fh.write(json.dumps(fold2) + "\n")
        fh.write(json.dumps(dup_desc) + "\n")
    _rebuild(sdk, events)
    from collections import Counter
    targets = Counter(
        r[0] for r in proj.g.query(
            "MATCH (p:Point {id:$id})-[r:aboutSubject]->(t) "
            "RETURN t.name",
            params={"id": s2}).result_set)
    assert targets["ReuseSubj"] == 1, \
        f"parallel aboutSubject edge on final successor: {targets}"
    assert not _struct_edges(proj, x), "old X kept a resurrected about edge"
    assert not _struct_edges(proj, s1)


# ── Resolver stub-mint equivalence (live vs replay create path) ──

def test_resolver_stub_mint_equivalence_for_absent_entities(sup):
    """Replay against ABSENT entities mints the same stubs live wiring would:
    absent Subject name → Subject stub (id=name, subjectKind='other'); absent
    Source url → Source stub (title=url, sourceKind='document'). One create
    path (edges.py _mint_subject_stub/_mint_source_stub) for live + replay."""
    _, events, sdk = sup
    x = sdk.create_point("statement", "X", status="live",
                         extractedFrom="https://eq.test/absent-src")["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    # Live wiring for the aboutSubject edge: a Subject that exists ONLY live
    # (raw — never journaled, so the rebuild cannot re-create it) + direct
    # live aboutSubject edge (mirrors the live _create_about_edges wiring).
    proj.g.query(
        "CREATE (s:Subject {name:'AbsentSubj', id:'AbsentSubj', "
        "subjectKind:'other'})")
    x_row = proj.g.query(
        "MATCH (x:Point {id:$id}) RETURN ID(x)",
        params={"id": x}).result_set
    proj.g.query(
        "MATCH (x) WHERE ID(x) = $xi "
        "MATCH (s:Subject {name:'AbsentSubj'}) "
        "MERGE (x)-[:aboutSubject]->(s)",
        params={"xi": x_row[0][0]})
    assert _struct_edges(proj, x) == {("extractedFrom", "https://eq.test/absent-src"),
                                      ("aboutSubject", "AbsentSubj")}
    sdk.supersede_point(x, succ)
    # Wipe the raw entities so replay must RE-MINT the stubs (they are not
    # journaled — pass-2 resurrection + the descriptor resolver both recreate
    # them from the key).
    proj.g.query("MATCH (s:Subject {name:'AbsentSubj'}) DELETE s")
    proj.g.query("MATCH (s:Source {url:'https://eq.test/absent-src'}) DELETE s")
    _rebuild(sdk, events)
    assert _struct_edges(proj, succ) == {
        ("extractedFrom", "https://eq.test/absent-src"),
        ("aboutSubject", "AbsentSubj")}, \
        "replay must mint the absent-entity stubs and land the edges"
    assert not _struct_edges(proj, x)
    subj = proj.g.query(
        "MATCH (s:Subject {name:'AbsentSubj'}) "
        "RETURN s.id, s.subjectKind").result_set
    assert subj == [["AbsentSubj", "other"]], \
        f"Subject stub must mirror live fallback props: {subj}"
    src = proj.g.query(
        "MATCH (s:Source {url:'https://eq.test/absent-src'}) "
        "RETURN s.title, s.sourceKind").result_set
    assert src == [["https://eq.test/absent-src", "document"]], \
        f"Source stub must mirror _link_source props: {src}"


# ── Dual-label name collision (same-name Subject + Object) ──

def test_dual_label_name_collision_label_scoped_resolution(sup):
    """A Subject AND an Object share the name 'Collide' — the descriptor's
    aboutSubject resolution is LABEL-SCOPED (never auto-detect): the edge
    lands on the Subject, the same-name Object is untouched. Auto-detect
    (_create_about_edges) would have attached to whatever matched first."""
    _, events, sdk = sup
    sdk.create_subject("Collide", "org")
    sdk.create_object("Collide", "artifact")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["Collide"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    assert _struct_edges(proj, x) == {("aboutSubject", "Collide")}, \
        "live auto-detect is Subject-first; fixture must hold"
    sdk.supersede_point(x, succ)
    _rebuild(sdk, events)
    assert _struct_edges(proj, succ) == {("aboutSubject", "Collide")}
    subj = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:aboutSubject]->(s:Subject {name:'Collide'}) "
        "RETURN count(r)",
        params={"id": succ}).result_set[0][0]
    obj = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:aboutObject]->(o:Object {name:'Collide'}) "
        "RETURN count(r)",
        params={"id": succ}).result_set[0][0]
    assert subj == 1 and obj == 0, \
        f"label-scoped resolution: subject={subj} object={obj}"
    assert not _struct_edges(proj, x)


# ── Pre-fix journal boundary: no descriptor → no delete-leg ──

def test_pre_fix_journal_boundary_resurrection_persists(sup):
    """A journal whose supersedes PREDATE the fix carries no structural
    DirectEdgeRepoint descriptors. Rebuild replays with NO delete-leg — the
    old's pass-2 resurrection persists (rebuild does NOT repair pre-existing
    graphs; #2500-style backfill is out of scope). Pins the boundary."""
    _, events, sdk = sup
    sdk.create_subject("BoundarySubj", "org")
    x = sdk.create_point("statement", "X", status="live",
                         aboutEntities=["BoundarySubj"])["id"]
    succ = sdk.create_point("statement", "X'", status="live")["id"]
    proj = sdk._get_proj()
    _rebuild(sdk, events)  # resurrect X's about edge (live, pre-fix shape)
    # Journal a pre-fix-style supersede: strip every DirectEdgeRepoint
    # descriptor the (fixed) supersede would have emitted.
    sdk.supersede_point(x, succ)
    import json as _json
    kept, stripped = [], 0
    with open(events / "events.jsonl") as fh:
        for line in fh:
            ev = _json.loads(line)
            if (ev.get("type") == "DirectEdgeRepoint"
                    and ev.get("edge_type") in _STRUCT_RELS):
                stripped += 1
                continue
            kept.append(line)
    assert stripped >= 1, "fixture must have stripped a structural descriptor"
    with open(events / "events.jsonl", "w") as fh:
        fh.writelines(kept)
    _rebuild(sdk, events)
    # No delete-leg: the resurrection at old X persists; the successor lost
    # the edge — exactly the pre-fix defect this issue fixes for NEW journals.
    assert _struct_edges(proj, x) == {("aboutSubject", "BoundarySubj")}, (
        "pre-fix journal: old's resurrection must persist (no descriptor)")
    assert not _struct_edges(proj, succ)


def test_delete_leg_skips_live_recreated_src(sup):
    """code-review P1: the structural delete-leg must ONLY clean the pass-2
    resurrection at a TERMINAL (dead) old point. A LIVE re-created src
    (id-reuse: supersede fold dropped by #2488's survivor filter) owns its
    edges legitimately — the delete-leg would clobber a live point.

    Lane: live X with a legit extractedFrom edge; a stale structural
    descriptor whose src is X is replayed (the id-reuse fold-drop lane —
    #2488's survivor filter would drop the pre-recreation supersede fold, so
    X stays LIVE at replay). The delete-leg must NOT fire (X is not
    terminal — its edge is legit).
    """
    _, events, sdk = sup
    s = "https://parity.example/doc"
    x = sdk.create_point("statement", "X", status="live",
                         extractedFrom=s)["id"]
    # Simplest id-reuse stand-in: X is LIVE at replay (no supersede fold ran).
    # Emit a structural descriptor whose src is the LIVE X (stale descriptor
    # from a dropped fold) + a fresh PointAdded snapshot so pass-2 rebuilds X.
    # Fresh PointAdded for X (rebuild materializes X live from this snapshot).
    with open(events / "events.jsonl", "a") as fh:
        fh.write(json.dumps({
            "event_id": sdk.ulid(), "ts": datetime.now(UTC).isoformat(),
            "type": "PointAdded", "initiated_by": "raw-producer",
            "projection_version": 2,
            "point": {"id": x, "kind": "statement", "content": "X",
                      "status": "live", "extractedFrom": s}})+"\n")
        fh.write(json.dumps({
            "event_id": sdk.ulid(), "ts": datetime.now(UTC).isoformat(),
            "type": "DirectEdgeRepoint", "initiated_by": "raw-producer",
            "projection_version": 2,
            "src": x, "tgt": s, "target_label": "Source",
            "edge_type": "extractedFrom"})+"\n")
    # Live X has the edge pre-rebuild.
    assert _struct_edges(sdk._get_proj(), x) == {("extractedFrom", s)}
    _rebuild(sdk, events)
    proj = sdk._get_proj()
    post = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, coalesce(n.outdated,false)",
        params={"id": x}).result_set[0]
    assert post[0] == "live", "X must be LIVE at replay (no fold ran)"
    assert _struct_edges(proj, x) == {("extractedFrom", s)}, (
        "delete-leg must NOT fire against a live src — the edge is legit"
    )


# ══════════════════════════════════════════════════════════════════════
# #3305 — the apply() replay arm must fold PointSuperseded too
#
# ``rebuild(EventLog)`` is the one-record engine behind ``recover_from_log``
# (DB-loss recovery) and the backup JSONL restore. It had NO branch for
# PointSuperseded, so the record fell through to the ``unrecognized event
# type`` warning and a superseded Point re-materialized as status='live' with
# the CORRECTS edge + belief decay gone. The fold is now shared with
# ``rebuild_all``'s sweep through ``_fold_point_restamp``.
# ══════════════════════════════════════════════════════════════════════

def _apply_replay(sdk, events_dir) -> None:
    """The ``apply()`` arm: wipe + replay via ``rebuild(EventLog)`` — the
    canonical apply()-based engine. ``consistency.recover_from_log`` and
    ``backup.restore`` are independent replay loops wired to the SAME shared
    plan (``plan_point_restamp_folds`` + ``apply_journal_point_restamp``)."""
    sdk._get_proj().rebuild(EventLog(str(events_dir / "events.jsonl")))


def test_apply_replay_folds_supersede(sup):
    """#3305: an apply()-based replay of a superseded Point must end
    'superseded' — asserted as the literal live status, not something read
    back from the replay it is testing."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "old A", status="live")["id"]
    succ = sdk.create_point("statement", "successor A'", status="live",
                            valid_from="2026-01-01T00:00:00+00:00")["id"]
    sdk.supersede_point(a, succ)
    pre = _point_state(sdk, a)
    _apply_replay(sdk, events)
    post = _point_state(sdk, a)
    assert post["status"] == "superseded", (
        f"apply() replay resurrected a dead Point: {post}")
    assert post["outdated"] is True
    assert post["validTo"] and post["expiredAt"]
    assert post == pre, (
        f"apply() replay drifted from live: {pre} != {post}")
    assert _corr(sdk._get_proj(), a, succ) == 1, (
        "CORRECTS edge dropped by the apply() arm")


def test_apply_replay_folds_the_supersede_belief_decay(sup):
    """#3305: the terminalizer's BELIEF half rides the same record — an
    apply()-based replay must decay the claim to the vacuous posterior, or
    the restored Point keeps a frozen promoted posterior."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "old A", status="live")["id"]
    succ = sdk.create_point("statement", "successor A'", status="live")["id"]
    sdk.supersede_point(a, succ)
    _apply_replay(sdk, events)
    decayed = sdk.get_point(a)
    assert decayed["confidence"] == 0.5, (
        f"belief decay not folded by the apply() arm: {decayed.get('confidence')}")
    assert decayed["posterior_alpha"] == 1.0
    assert decayed["posterior_beta"] == 1.0


def test_apply_replay_matches_rebuild_all_on_supersede(sup):
    """#3305: the two replay engines must converge on the same Point state —
    the shared ``_fold_point_restamp`` dispatch is what keeps them from
    drifting."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "old A", status="live")["id"]
    succ = sdk.create_point("statement", "successor A'", status="live")["id"]
    sdk.supersede_point(a, succ)
    live = _point_state(sdk, a)
    assert live["status"] == "superseded"

    _rebuild(sdk, events)
    via_all = _point_state(sdk, a)
    _apply_replay(sdk, events)
    via_apply = _point_state(sdk, a)

    assert via_all == live, f"rebuild_all drifted from live: {via_all}"
    assert via_apply == via_all, (
        f"apply() replay != rebuild_all: {via_apply} != {via_all}")


def test_apply_replay_keeps_a_live_point_live(sup):
    """#3305 no-over-correction: a Point with no terminalizer in the journal
    stays live across the apply() arm — the fix must not fold anything onto
    an ordinary claim."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "never touched", status="live")["id"]
    _apply_replay(sdk, events)
    post = _point_state(sdk, a)
    assert post["status"] == "live"
    assert post["outdated"] is None
    assert post["validTo"] is None


def test_apply_replay_shares_rebuild_all_selection_on_double_supersede(sup):
    """#3305 (review P1): the apply() arm must obey the SAME SELECTION as
    rebuild_all, not merely the same fold body. A raw id-reusing producer can
    journal TWO PointSuperseded events for one old id; rebuild_all
    CANONICALIZES them (only the last folds) so the earlier ``S1→A`` CORRECTS
    cannot ghost beside the final ``S2→A``. Folding every terminalizer inline
    would re-introduce that ghost on the recovery path."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    s1 = sdk.create_point("statement", "S1", status="live")["id"]
    s2 = sdk.create_point("statement", "S2", status="live")["id"]
    sdk.supersede_point(a, s1)
    # A second fold for the SAME old id — the SDK's terminal guard refuses a
    # live second supersede, so journal it the way a raw producer would.
    with open(events / "events.jsonl", "a") as fh:
        fh.write(json.dumps({
            "event_id": sdk.ulid(), "ts": datetime.now(UTC).isoformat(),
            "type": "PointSuperseded", "initiated_by": "raw-producer",
            "projection_version": 2, "id": a, "new_id": s2,
        }) + "\n")

    _rebuild(sdk, events)
    via_all = {k: _point_state(sdk, a)[k] for k in ("status", "outdated")}
    corr_all = (sdk._get_proj().g.query(
        "MATCH (a:Point {id:$new})-[r:CORRECTS]->(b:Point {id:$old}) "
        "RETURN a.id",
        params={"new": s2, "old": a}).result_set)
    _apply_replay(sdk, events)
    via_apply = {k: _point_state(sdk, a)[k] for k in ("status", "outdated")}
    corr_apply = (sdk._get_proj().g.query(
        "MATCH (a:Point {id:$new})-[r:CORRECTS]->(b:Point {id:$old}) "
        "RETURN a.id",
        params={"new": s2, "old": a}).result_set)

    assert via_all["status"] == "superseded"
    # expiredAt is the ``_now_iso()`` fallback for a raw line that carries no
    # ``expired_at`` — a replay-time clock, so the comparison is on the fold's
    # SEMANTIC fields (the selection is what must agree), not on wall clock.
    assert via_apply == via_all, (
        f"apply() replay != rebuild_all on double supersede: "
        f"{via_apply} != {via_all}")
    assert _corr(sdk._get_proj(), a, s2) == 1
    assert _corr(sdk._get_proj(), a, s1) == 0, (
        "ghost CORRECTS from the earlier, non-canonical supersede")
    assert corr_apply == corr_all


def test_apply_replay_warns_when_a_fold_matches_no_point(sup, caplog):
    """#3305/#3299: a terminalizer whose target was never created is a
    dropped fold — the whole-journal apply() arm must SAY SO, like the
    one-record branch and rebuild_all's sweep already do."""
    import logging

    _, events, sdk = sup
    sdk.create_point("statement", "kept", status="live")
    EventLog(str(events / "events.jsonl")).append(
        {"type": "PointSuperseded", "id": "never-created-id",
         "new_id": "also-never-created", "event_id": "ghost-1",
         "ts": "2026-01-02T00:00:00+00:00"})
    with caplog.at_level(logging.WARNING):
        _apply_replay(sdk, events)
    assert any("matched no Point" in r.message for r in caplog.records), (
        "an apply()-based replay silently dropped a terminalizer fold: "
        + repr([r.message for r in caplog.records if "fold" in r.message]))


def test_apply_one_record_folds_a_terminalizer(sup):
    """#3305: the ``apply()`` ONE-RECORD default (no journal view) folds a
    terminalizer at the record's own position — the branch a live caller or an
    unwired engine uses. Also pins that a ``PointSuperseded`` with no ``new_id``
    neither folds nor decays."""
    _, _events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    s = sdk.create_point("statement", "S", status="live")["id"]
    proj = sdk._get_proj()
    proj.apply({"type": "PointSuperseded", "id": a, "new_id": s,
                "ts": "2026-01-02T00:00:00+00:00"})
    post = _point_state(sdk, a)
    assert post["status"] == "superseded"
    assert post["outdated"] is True
    assert _corr(proj, a, s) == 1

    # An inapplicable supersede (no successor) must not decay the target — the
    # guard runs BEFORE the belief half on the one-record default.
    c = sdk.create_point("statement", "C", status="live")["id"]
    before = (sdk.get_point(c) or {}).get("confidence")
    proj.apply({"type": "PointSuperseded", "id": c})
    assert (sdk.get_point(c) or {}).get("confidence") == before
    assert sdk.get_point(c)["status"] == "live"


def _synthesize_journal(path, entries) -> None:
    """Replace the journal with an explicit ordered list (a raw producer can
    journal in any order; the SDK's guards refuse some of these)."""
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def test_apply_replay_matches_rebuild_all_when_a_promote_is_the_only_record(
        sup):
    """#3305 (review P1), #2256: a node whose ONLY durable journal record is a
    ``PointPromoted`` snapshot still EXISTs for the fold's existence gate. A
    terminalizer journaled before that promote must be inert on BOTH engines —
    folding it on ``rebuild_all``'s trailing sweep while the chronological
    apply() arm misses is the divergence this pins. Same for
    ``OperatorPromoted``."""
    from tortoise.consistency import recover_from_log

    _, events, sdk = sup
    for promote_type in ("PointPromoted", "OperatorPromoted"):
        b = sdk.create_point("statement", f"B {promote_type}",
                             status="draft")["id"]
        c = sdk.create_point("statement", f"C {promote_type}",
                             status="live")["id"]
        added_c = next(
            e for e in EventLog(str(events / "events.jsonl")).read_all()
            if e.get("type") == "PointAdded" and e["point"]["id"] == c)
        snap = dict(sdk.get_point(b))
        snap["status"] = "live"
        ts = "2026-01-01T00:00:00+00:00"
        # A DEDICATED log dir: the sdk's own journal still holds the
        # ``PointAdded`` for ``b`` (which is exactly the record this shape
        # removes), and its buffered writes would land in the fixture's log.
        synth_dir = events.parent / f"synth-{promote_type}"
        synth_dir.mkdir()
        _synthesize_journal(synth_dir / "events.jsonl", [
            {"event_id": sdk.ulid(), "ts": ts, "type": "PointSuperseded",
             "initiated_by": "raw-producer", "projection_version": 2,
             "id": b, "new_id": c},
            {"event_id": sdk.ulid(), "ts": ts, "type": promote_type,
             "initiated_by": "raw-producer", "projection_version": 2,
             "point": snap},
            added_c,
        ])
        proj = sdk._get_proj()

        # ``rebuild_all`` snapshots GRAPH-ONLY nodes and injects them as
        # synthetic ``PointAdded`` (#548) — which would restore exactly the
        # ``PointAdded`` for ``b`` this shape removes. Empty the graph first.
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild_all(str(synth_dir))
        via_all = _point_state(sdk, b)
        assert via_all["status"] == "live", (
            f"{promote_type}: a terminalizer before the node existed must not "
            f"fold after the promote: {via_all}")

        _apply_replay(sdk, synth_dir)
        via_apply = _point_state(sdk, b)
        assert via_apply == via_all, (
            f"{promote_type}: apply() replay != rebuild_all: "
            f"{via_apply} != {via_all}")

        # The DB-loss recovery engine shares the same plan.
        proj.g.query("MATCH (n) DETACH DELETE n")
        assert recover_from_log(str(synth_dir), proj)["recovered"]
        assert _point_state(sdk, b) == via_all, (
            f"{promote_type}: recover_from_log != rebuild_all")


def test_apply_replay_routes_a_type_in_point_terminalizer_through_the_plan(
        sup):
    """#3305/#325: ``_norm`` splices a nested payload, so a record whose TYPE
    lives inside ``point`` is planned by ``plan_point_restamp_folds``. The
    engines must therefore dispatch on the PLAN, not the raw envelope type —
    otherwise the record falls through to ``apply()``'s inline branch, which
    folds EVERY terminalizer and re-introduces the ghost ``CORRECTS`` the
    canonicalization exists to prevent."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    s1 = sdk.create_point("statement", "S1", status="live")["id"]
    s2 = sdk.create_point("statement", "S2", status="live")["id"]
    base = EventLog(str(events / "events.jsonl")).read_all()
    synth_dir = events.parent / "synth-type-in-point"
    synth_dir.mkdir()
    ts = "2026-01-01T00:00:00+00:00"
    _synthesize_journal(synth_dir / "events.jsonl", [
        *base,
        {"event_id": sdk.ulid(), "ts": ts, "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"type": "PointSuperseded", "id": a, "new_id": s1}},
        {"event_id": sdk.ulid(), "ts": ts, "initiated_by": "raw-producer",
         "projection_version": 2,
         "point": {"type": "PointSuperseded", "id": a, "new_id": s2}},
    ])

    def _successors(proj) -> list:
        return sorted(r[0] for r in proj.g.query(
            "MATCH (a:Point)-[r:CORRECTS]->(b:Point {id:$old}) RETURN a.id",
            params={"old": a}).result_set)

    proj = sdk._get_proj()
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(synth_dir))
    via_all = _point_state(sdk, a)
    assert via_all["status"] == "superseded"
    corr_all = _successors(proj)
    assert corr_all == [s2], (
        f"rebuild_all canonicalization dropped: {corr_all}")

    _apply_replay(sdk, synth_dir)
    assert _point_state(sdk, a)["status"] == "superseded"
    assert _successors(sdk._get_proj()) == corr_all, (
        "the apply() arm folded a non-canonical supersede — the shared "
        "selection was bypassed (ghost CORRECTS)")


def test_apply_replay_matches_rebuild_all_on_a_nested_terminalizer_payload(
        sup):
    """#3305/#325: ``_norm`` tolerates a NESTED terminalizer payload
    (``{"type": ..., "point": {"id": ..., "new_id": ...}}``). The
    whole-journal apply() arm must normalize like the plan and ``rebuild_all``
    do, or it silently drops a fold both engines otherwise land — and emits a
    FALSE fold-miss warning naming the target it could not see."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    base = EventLog(str(events / "events.jsonl")).read_all()
    synth_dir = events.parent / "synth-nested"
    synth_dir.mkdir()
    _synthesize_journal(synth_dir / "events.jsonl", [
        *base,
        {"event_id": sdk.ulid(), "ts": "2026-01-01T00:00:00+00:00",
         "type": "PointSuperseded", "initiated_by": "raw-producer",
         "projection_version": 2, "point": {"id": a, "new_id": b}},
    ])

    proj = sdk._get_proj()
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(synth_dir))
    via_all = _point_state(sdk, a)
    assert via_all["status"] == "superseded"
    assert via_all["outdated"] is True
    assert _corr(proj, a, b) == 1

    _apply_replay(sdk, synth_dir)
    # expiredAt is the ``_now_iso()`` fallback (the record carries none) — a
    # replay-time clock, so compare the fold's SEMANTIC fields, not wall clock.
    via_apply = _point_state(sdk, a)
    assert via_apply["status"] == via_all["status"] == "superseded", (
        "the apply() arm dropped a NESTED terminalizer payload that "
        "rebuild_all folded")
    assert via_apply["outdated"] is True
    assert via_apply["validTo"] == via_all["validTo"]
    assert _corr(sdk._get_proj(), a, b) == 1, (
        "the nested payload's CORRECTS edge was dropped")


def test_plan_point_restamp_folds_pins_the_shared_selection(caplog):
    """#3305: the plan is the ONE home for the terminalizer SELECTION (the
    survivor rule + supersede canonicalization + both belief anchors) that
    every replay engine obeys. Pinned directly — the two DB-level parity tests
    above pin the engines' agreement, this pins the contract itself."""
    from tortoise.projection import plan_point_restamp_folds

    # Two supersedes for ONE old id, no delete/recreate: only the LAST is a
    # stamp survivor (canonicalization), and only the LAST decays.
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointAdded", "point": {"id": "s1", "content": "S1"}},
        {"type": "PointAdded", "point": {"id": "s2", "content": "S2"}},
        {"type": "PointSuperseded", "id": "a", "new_id": "s1"},
        {"type": "PointSuperseded", "id": "a", "new_id": "s2"},
    ])
    assert decisions[3] == (False, False), (
        "the non-canonical supersede must neither fold nor decay")
    assert decisions[4] == (True, True)
    assert fold_seq == {"a": 4}

    # A bare same-id PointAdded RE-EMIT advances the recreate boundary, so the
    # pre-re-emit invalidate must NOT stamp — but the belief decay is anchored
    # on the REAL delete→recreate boundary, so it MUST still fold (#2884 A3).
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointInvalidated", "id": "a", "corrected_by": "b"},
        {"type": "PointAdded", "point": {"id": "a", "content": "A2"}},
    ])
    assert decisions[1] == (True, False), (
        "re-emit: decay folds, the pre-recreation stamp is dropped")
    assert fold_seq == {}

    # A REAL delete→recreate moves BOTH boundaries: nothing about the dead
    # incarnation folds or decays.
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointInvalidated", "id": "a", "corrected_by": "b"},
        {"type": "EntityMutated", "id": "a", "op": "delete",
         "label": "Point"},
        {"type": "PointAdded", "point": {"id": "a", "content": "A2"}},
    ])
    assert decisions[1] == (False, False)
    assert fold_seq == {}

    # PointPromoted is deliberately NOT a recreate boundary (same-node
    # draft→live: it clears neither ``outdated`` nor CORRECTS), so the
    # pre-promote invalidate SURVIVES and still decays.
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointInvalidated", "id": "a", "corrected_by": "b"},
        {"type": "PointPromoted",
         "point": {"id": "a", "content": "A", "status": "live"}},
    ])
    assert decisions[1] == (True, True), (
        "PointPromoted must not advance the recreate boundary")
    assert fold_seq == {}

    # A forward-reference journal (the terminalizer precedes its target's
    # creation) suppresses the DECAY so both engines agree — a chronological
    # apply() arm would fold before the node exists, while rebuild_all's
    # pass-1a hoist would land it.
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointSuperseded", "id": "a", "new_id": "b"},
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointAdded", "point": {"id": "b", "content": "B"}},
    ])
    assert decisions[0] == (False, False)
    assert fold_seq == {}

    # An empty-string / unwritable id is inapplicable (the plan's non-empty
    # writable gate) and is REPORTED, not silently dropped (#3299) — the plan is
    # the only audibility for such a record (it returns (False, False), so
    # apply_journal_point_restamp's own fold-miss warning never runs).
    import logging

    for bad_id in ("", "nul\x00id", "lone\ud800id"):
        caplog.clear()  # per-id: the assertion must be load-bearing for EACH id
        with caplog.at_level(logging.WARNING):
            decisions, fold_seq = plan_point_restamp_folds([
                {"type": "PointSuperseded", "id": bad_id, "new_id": "b"},
            ])
        assert decisions[0] == (False, False), bad_id
        assert fold_seq == {}
        assert any("has no writable non-empty id" in r.message
                   for r in caplog.records), (
            f"ineligible id {bad_id!r} was dropped silently")

    # A supersede with NO new_id still STAMPS when it is the id's last
    # recreate-surviving supersede (the fold returns 0 and the consumer warns),
    # but it never decays.
    decisions, fold_seq = plan_point_restamp_folds([
        {"type": "PointAdded", "point": {"id": "a", "content": "A"}},
        {"type": "PointAdded", "point": {"id": "b", "content": "B"}},
        {"type": "PointSuperseded", "id": "a"},
    ])
    assert decisions[2] == (False, True)
    assert fold_seq == {"a": 2}


# ══════════════════════════════════════════════════════════════════════
# #3305 — the SUCCESSOR arm of the fold's existence gate
#
# The plan gates each terminalizer fold on the id it TERMINALIZES (its
# TARGET), but the CORRECTS edge names the SUCCESSOR — a second endpoint the
# record may name before the journal creates it. A chronological replay
# reached the record first, so the inline edge MERGE no-op'd while
# rebuild_all's after-creations sweep resolved it: the mirror of #3305's own
# symptom, reachable from every apply()-based engine. The edge half is
# deferred to a trailing sweep (``fold_deferred_corrects_edges``); the
# flags/validity half stays inline, because it depends on the target only and
# the engines already agree on it.
# ══════════════════════════════════════════════════════════════════════

def test_terminalizer_successor_created_later_folds_the_same_on_every_engine(
        sup):
    """#3305 (review P1, sibling arm): for
    ``[PointAdded a, PointSuperseded a→s2, PointAdded s2]`` the CORRECTS edge
    must fold IDENTICALLY on ``rebuild_all`` and on BOTH chronological
    engines — pinning one engine's value is exactly the hole this closes.

    The successor endpoint is created AFTER the record that names it, so the
    edge can only be merged by a trailing sweep. Asserted for BOTH lifecycle
    families: ``PointSuperseded`` (``new_id``) and ``PointInvalidated``
    (``corrected_by``) share the arm."""
    from tortoise.consistency import recover_from_log

    _, events, sdk = sup
    ts = "2026-01-01T00:00:00+00:00"
    proj = sdk._get_proj()

    def _added(pid):
        return {"event_id": sdk.ulid(), "ts": ts,
                "initiated_by": "raw-producer", "projection_version": 2,
                "type": "PointAdded",
                "point": {"id": pid, "label": "statement", "content": pid,
                          "status": "live", "confidence": 0.9}}

    shapes = {
        # ``expired_at`` is journaled, not left to the ``_now_iso()`` fallback:
        # the folds replay the ORIGINAL stamp verbatim, and a rebuild-time
        # fallback would differ between the three sequential engine runs.
        "supersede": {"type": "PointSuperseded", "id": "a", "new_id": "s2",
                      "valid_to": ts, "expired_at": ts},
        "invalidate": {"type": "PointInvalidated", "id": "a",
                       "corrected_by": "c", "valid_to": ts,
                       "expired_at": ts},
    }
    for name, term in shapes.items():
        successor = term.get("new_id") or term.get("corrected_by")
        synth_dir = events.parent / f"synth-forward-{name}"
        synth_dir.mkdir()
        _synthesize_journal(synth_dir / "events.jsonl", [
            _added("a"),
            {"event_id": sdk.ulid(), "ts": ts, "initiated_by": "raw-producer",
             "projection_version": 2, **term},
            _added(successor),
        ])

        def _observable(successor=successor):
            return (_point_state(sdk, "a"),
                    _corr(proj, "a", successor),
                    (sdk.get_point("a") or {}).get("confidence"))

        # rebuild_all (trailing sweep) — the reference value.
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj.rebuild_all(str(synth_dir))
        via_all = _observable()

        # rebuild(EventLog) — the chronological apply() arm.
        _apply_replay(sdk, synth_dir)
        via_apply = _observable()

        # recover_from_log — the DB-loss engine, sharing the same plan.
        proj.g.query("MATCH (n) DETACH DELETE n")
        assert recover_from_log(str(synth_dir), proj)["recovered"]
        via_recover = _observable()

        assert via_apply == via_all, (
            f"{name}: rebuild(EventLog) != rebuild_all — "
            f"{via_apply} != {via_all}")
        assert via_recover == via_all, (
            f"{name}: recover_from_log != rebuild_all — "
            f"{via_recover} != {via_all}")
        # The CORRECTS edge IS the subject: a remedy that drops it on BOTH
        # engines must not pass as "parity".
        assert via_all[1] == 1, (
            f"{name}: the CORRECTS edge was dropped on every engine: {via_all}")


def test_supersede_then_revision_keeps_the_revision_stamp_on_every_engine(sup):
    """#3305 (review P2): a supersede is status-TERMINAL but NOT stamp-frozen.

    Live accepts a ``PointRevised`` after a supersede, so the trailing sweep
    must not clobber that revision's ``updatedAt`` with the older journaled
    supersede ts — the invalidate family already carried this seq-gate
    (``skip_updated_at``); the supersede sibling did not. Pins the gate on ALL
    three engines by asserting none of them ends at the supersede ts."""
    from tortoise.consistency import recover_from_log

    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    sdk.supersede_point(a, b)
    sdk.update_point(a, content="REVISED-AFTER-SUPERSEDE")
    records = EventLog(str(events / "events.jsonl")).read_all()
    sup_ts = next(e["ts"] for e in records
                  if e.get("type") == "PointSuperseded")
    proj = sdk._get_proj()

    def _row():
        out = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status, n.content, n.updatedAt",
            params={"id": a}).result_set[0]
        return out[0], out[1], out[2]

    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.rebuild_all(str(events))
    via_all = _row()

    _apply_replay(sdk, events)
    via_apply = _row()

    proj.g.query("MATCH (n) DETACH DELETE n")
    assert recover_from_log(str(events), proj)["recovered"]
    via_recover = _row()

    for name, row in (("rebuild_all", via_all), ("rebuild", via_apply),
                      ("recover_from_log", via_recover)):
        assert row[0] == "superseded", f"{name}: status {row[0]!r}"
        assert row[1] == "REVISED-AFTER-SUPERSEDE", f"{name}: content {row[1]!r}"
        assert row[2] != sup_ts, (
            f"{name}: the sweep clobbered the later revision's updatedAt with "
            f"the older journaled supersede ts ({sup_ts!r})")
