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
    """Documents store their display name in `title` (#211): the descriptor
    key is coalesce(title, name) and replay resolution matches by title (or
    name). X-[:aboutDocument]->(Document title-only) follows the successor.
    create_document does NOT journal DocumentCreated (the #2296 durability
    audit tracks that surface) — the fixture journals the line the way the
    document-index path does, so the rebuild can re-create the Document."""
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
    # the edge lands on the DOCUMENT node (label-scoped), resolved by title
    n = proj.g.query(
        "MATCH (p:Point {id:$id})-[r:aboutDocument]->(d:Document) "
        "WHERE d.title = $title RETURN count(r)",
        params={"id": succ, "title": "DocTitleOnly"}).result_set[0][0]
    assert n == 1, f"aboutDocument should terminate on the Document: {n}"


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
