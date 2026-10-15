"""Tests for supersede_point edge cleanup — Issue #95.

Verifies that supersede_point transfers IMPL/NAND/hasPart edges from the old
point to the new point, preventing EP from propagating through superseded claims.

Runnable with:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise_test_sup95 python3 -m pytest tests/test_supersede_edges.py -q
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_supersede_test_"), "test.db"
    )
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _make_point(sdk: TortoiseSDK, **kw):
    return sdk.create_point(
        kw.pop("kind", "statement"), kw.pop("content", "test content"), **kw
    )


# ── IMPL edge transfer (target) ────────────────────────────────────

def test_supersede_transfers_impl_to_target(sdk):
    """IMPL evidence→claim_A: supersede A→B should move IMPL edge to B."""
    claim_a = _make_point(sdk, content="Claim A: original version")
    evidence = _make_point(sdk, kind="evidence", content="Evidence for claim")
    sdk.create_operator("IMPL", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B: revised version")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])

    # Return value
    assert result["invalidated"] is True
    assert result["id"] == claim_a["id"]
    assert result["corrected_by"] == claim_b["id"]
    assert result["edges_transferred"] >= 1

    # A is outdated, B is not
    assert sdk.get_point(claim_a["id"])["outdated"] is True
    assert not sdk.get_point(claim_b["id"]).get("outdated")

    # IMPL edge moved to B
    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) >= 1, "IMPL edge should be transferred to B"

    # A has no live IMPL edges
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0, (
        f"A should have no incoming IMPL after supersede, got {incoming_a}"
    )


# ── NAND edge transfer (target) ────────────────────────────────────

def test_supersede_transfers_nand_to_target(sdk):
    """NAND evidence→claim_A: supersede A→B should move NAND edge to B."""
    claim_a = _make_point(sdk, content="Claim A: disputed")
    evidence = _make_point(sdk, kind="evidence", content="Contradicting evidence")
    sdk.create_operator("NAND", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B: revised")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] >= 1

    # NAND edge moved to B
    incoming_b = sdk.traverse(claim_b["id"], "NAND", direction="incoming")
    assert len(incoming_b) >= 1, "NAND edge should be transferred to B"

    # A has no live NAND edges
    incoming_a = sdk.traverse(claim_a["id"], "NAND", direction="incoming")
    assert len(incoming_a) == 0, (
        f"A should have no incoming NAND after supersede, got {incoming_a}"
    )


# ── Source edge transfer ───────────────────────────────────────────

def test_supersede_transfers_source_edge(sdk):
    """IMPL claim_A→target: supersede A→B should move source role to B."""
    claim_a = _make_point(sdk, content="Claim A (source)")
    target = _make_point(sdk, content="Target claim")
    sdk.create_operator("IMPL", claim_a["id"], [target["id"]])

    claim_b = _make_point(sdk, content="Claim B (supersedes A)")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] >= 1

    # B now has incoming IMPL (as source of the operator)
    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) >= 1, "B should receive the source IMPL edge"

    # A has no incoming IMPL
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0

    # The target still has its IMPL edge (operator→target not affected)
    incoming_target = sdk.traverse(target["id"], "IMPL", direction="incoming")
    assert len(incoming_target) >= 1, "Target should still have its IMPL edge"


# ── Multiple edges ─────────────────────────────────────────────────

def test_supersede_transfers_multiple_edges(sdk):
    """Multiple IMPL edges into A: all should transfer to B."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence_1 = _make_point(sdk, kind="evidence", content="Evidence 1")
    evidence_2 = _make_point(sdk, kind="evidence", content="Evidence 2")
    sdk.create_operator("IMPL", evidence_1["id"], [claim_a["id"]])
    sdk.create_operator("IMPL", evidence_2["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] == 2

    incoming_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    assert len(incoming_b) == 2
    incoming_a = sdk.traverse(claim_a["id"], "IMPL", direction="incoming")
    assert len(incoming_a) == 0


# ── Mixed edges (IMPL + NAND) ──────────────────────────────────────

def test_supersede_transfers_mixed_edges(sdk):
    """IMPL and NAND into A: both transfer to B."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence_support = _make_point(sdk, kind="evidence", content="Supporting")
    evidence_oppose = _make_point(sdk, kind="evidence", content="Opposing")
    sdk.create_operator("IMPL", evidence_support["id"], [claim_a["id"]])
    sdk.create_operator("NAND", evidence_oppose["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["edges_transferred"] == 2

    impl_to_b = sdk.traverse(claim_b["id"], "IMPL", direction="incoming")
    nand_to_b = sdk.traverse(claim_b["id"], "NAND", direction="incoming")
    assert len(impl_to_b) == 1
    assert len(nand_to_b) == 1

    # A has zero of either
    assert len(sdk.traverse(claim_a["id"], "IMPL", direction="incoming")) == 0
    assert len(sdk.traverse(claim_a["id"], "NAND", direction="incoming")) == 0


# ── No edges to transfer ───────────────────────────────────────────

def test_supersede_no_edges_transfers_zero(sdk):
    """Superseding a point with no operator edges reports 0 transferred."""
    claim_a = _make_point(sdk, content="Claim A (no edges)")
    claim_b = _make_point(sdk, content="Claim B")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert result["invalidated"] is True
    assert result["edges_transferred"] == 0


# ── Idempotency guard: double supersede ────────────────────────────

def test_supersede_then_supersede_again_transfers_zero(sdk):
    """Superseding an already-superseded point raises (#432 terminal guard).

    The old point is terminal after the first supersede, so a second
    supersede is rejected — guaranteeing no duplicate edge transfers
    (updated from the #547 no-op contract, code-review #803)."""
    claim_a = _make_point(sdk, content="Claim A")
    evidence = _make_point(sdk, kind="evidence", content="Evidence")
    sdk.create_operator("IMPL", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B")
    claim_c = _make_point(sdk, content="Claim C")

    # First supersede: A → B
    r1 = sdk.supersede_point(claim_a["id"], claim_b["id"])
    assert r1["edges_transferred"] == 1

    # Second supersede: A → C — A is terminal ('superseded'), the guard
    # rejects the transition, so no duplicate edge writes can occur (#432).
    import pytest
    with pytest.raises(ValueError, match="already terminal"):
        sdk.supersede_point(claim_a["id"], claim_c["id"])


# ── #329: edge-type validation (two-pass, zero mutation on failure) ────

def test_supersede_unknown_edge_type_zero_mutation(sdk):
    """An unknown (crafted) operator edge type must abort BEFORE any mutation:
    old point NOT outdated, NO CORRECTS edge, NO edges transferred."""
    proj = sdk._get_proj()
    old = _make_point(sdk, content="Old (crafted edge)")
    new = _make_point(sdk, content="New")
    # Create a real operator, then craft an unknown-type edge from it
    sdk.create_operator("IMPL", old["id"], [new["id"]])
    op_rows = proj.g.query(
        "MATCH (op:Point {is_operator:true}) RETURN op.id LIMIT 1"
    ).result_set
    op_id = op_rows[0][0]
    proj.g.query(
        "MATCH (op:Point {id:$oid}), (old:Point {id:$oldid}) "
        "CREATE (op)-[:EVIL_CRAFTED]->(old)",
        params={"oid": op_id, "oldid": old["id"]},
    )
    with pytest.raises(ValueError, match="Invalid relationship type"):
        sdk.supersede_point(old["id"], new["id"])

    # Zero mutation: old not outdated, no CORRECTS edge from new
    old_now = sdk.get_point(old["id"])
    assert not old_now.get("outdated")
    rows = proj.g.query(
        "MATCH (a:Point {id:$nid})-[r:CORRECTS]->(b:Point {id:$oid}) RETURN count(r)",
        params={"nid": new["id"], "oid": old["id"]},
    ).result_set
    assert rows[0][0] == 0


def test_supersede_mixed_valid_and_invalid_zero_mutation(sdk):
    """A valid edge + an invalid edge → still zero mutation (validation is
    all-or-nothing, hoisted before the transfer)."""
    proj = sdk._get_proj()
    a = _make_point(sdk, content="A")
    ev = _make_point(sdk, content="Evidence")
    sdk.create_operator("IMPL", ev["id"], [a["id"]])  # valid IMPL edge
    b = _make_point(sdk, content="B")
    op_rows = proj.g.query(
        "MATCH (op:Point {is_operator:true}) RETURN op.id LIMIT 1"
    ).result_set
    op_id = op_rows[0][0]
    proj.g.query(
        "MATCH (op:Point {id:$oid}), (old:Point {id:$oldid}) "
        "CREATE (op)-[:EVIL_CRAFTED]->(old)",
        params={"oid": op_id, "oldid": a["id"]},
    )
    with pytest.raises(ValueError):
        sdk.supersede_point(a["id"], b["id"])
    a_now = sdk.get_point(a["id"])
    assert not a_now.get("outdated")
    # IMPL edge still on old (untouched — zero mutation)
    impl_rows = proj.g.query(
        "MATCH (op:Point {is_operator:true})-[:IMPL]->(old:Point {id:$oid}) "
        "RETURN count(*)",
        params={"oid": a["id"]},
    ).result_set
    assert impl_rows[0][0] == 1


def test_supersede_mitigated_point_transfers(sdk):
    """Points with mitigated_by edges supersede without error (mitigated_by
    is a known rel type created by the live mitigate_operator feature)."""
    proj = sdk._get_proj()
    old = _make_point(sdk, content="Old mitigated")
    new = _make_point(sdk, content="New")
    # Create a mitigation Point + mitigated_by edge like mitigate_operator does
    mit = sdk.create_point("mitigation", "mitigation rationale")
    proj.g.query(
        "MATCH (m:Point {id:$mid}), (t:Point {id:$oid}) "
        "CREATE (m)-[:mitigated_by]->(t)",
        params={"mid": mit["id"], "oid": old["id"]},
    )
    result = sdk.supersede_point(old["id"], new["id"])
    assert result["invalidated"] is True
    # mitigated_by edges are not part of the transfer (only operator→old edges
    # transfer) — but the supersede must not raise
    assert "edges_transferred" in result


# ── #1391: terminal-status exclusion on read surfaces ────────────────

def test_superseded_point_excluded_from_default_read(sdk):
    """#1391: after supersede_point the old point (superseded+outdated) is
    excluded from query() AND tortoise_fts_query by default — stale claims
    are not served as current."""
    old = _make_point(sdk, content="strategy alpha is our current strategy")
    new = _make_point(sdk, content="strategy beta is our current strategy")
    sdk.supersede_point(old["id"], new["id"])
    # query() excludes it by default
    assert old["id"] not in [p["id"] for p in sdk.query()]
    # FTS excludes it (the terminal-status filter lives in the base query)
    hits = sdk.tortoise_fts_query("strategy alpha", limit=10)
    assert old["id"] not in [h["id"] for h in hits]
    # the new (current) point remains findable
    hits_new = sdk.tortoise_fts_query("strategy beta", limit=10)
    assert new["id"] in [h["id"] for h in hits_new]


def test_terminal_status_audit_opt_in(sdk):
    """#1391: audit/history queries opt in — include_retracted surfaces ALL
    terminal statuses; an explicit status= filter controls visibility."""
    old = _make_point(sdk, content="old approach still referenced")
    new = _make_point(sdk, content="new approach adopted")
    sdk.supersede_point(old["id"], new["id"])
    ids_default = [p["id"] for p in sdk.query()]
    assert old["id"] not in ids_default
    ids_all = [p["id"] for p in sdk.query(include_retracted=True)]
    assert old["id"] in ids_all
    ids_explicit = [p["id"] for p in sdk.query(status="superseded")]
    assert old["id"] in ids_explicit


def test_outdated_status_excluded_too(sdk):
    """#1391: 'outdated' (written alongside superseded) is excluded as well —
    the exclusion covers the full terminal set, not just retracted."""
    old = _make_point(sdk, content="the outdated claim")
    new = _make_point(sdk, content="the current claim")
    sdk.supersede_point(old["id"], new["id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
        params={"id": old["id"]},
    ).result_set
    status, outdated = rows[0]
    assert status in ("superseded", "outdated") or outdated is True
    assert old["id"] not in [p["id"] for p in sdk.query()]


def test_outdated_flag_excluded_even_when_status_live(sdk):
    """#1391 (review P2): invalidate_point sets outdated=true WITHOUT touching
    status — the exclusion must cover the legacy flag too, not just terminal
    status values."""
    p = _make_point(sdk, content="the invalidated claim")
    replacement = _make_point(sdk, content="the replacement claim")
    sdk.invalidate_point(p["id"], corrected_by_id=replacement["id"])
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, n.outdated",
        params={"id": p["id"]},
    ).result_set
    status, outdated = rows[0]
    assert outdated is True or status in ("superseded", "outdated")
    assert p["id"] not in [x["id"] for x in sdk.query()]
    # audit opt-in surfaces it
    assert p["id"] in [x["id"] for x in sdk.query(include_retracted=True)]


# ── E6 (#1538) additive regression — window stamping is additive ──────────

def test_supersede_window_stamps_additive(sdk):
    """E6 T1 additive regression: the window stamp adds validTo/expiredAt
    without changing the supersede return shape or the transfer behavior."""
    claim_a = _make_point(sdk, content="Claim A: original version")
    evidence = _make_point(sdk, kind="evidence", content="Evidence for claim")
    sdk.create_operator("IMPL", evidence["id"], [claim_a["id"]])

    claim_b = _make_point(sdk, content="Claim B: revised version")

    result = sdk.supersede_point(claim_a["id"], claim_b["id"])

    # Return shape unchanged
    assert set(result) == {"invalidated", "id", "corrected_by",
                           "edges_transferred"}
    assert result["invalidated"] is True
    assert result["id"] == claim_a["id"]
    assert result["corrected_by"] == claim_b["id"]
    assert result["edges_transferred"] >= 1

    # Additive stamps on the old point
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.validTo, n.expiredAt, n.validFrom",
        params={"id": claim_a["id"]},
    ).result_set
    valid_to, expired_at, valid_from = row[0]  # noqa: RUF059
    assert valid_to, "validTo must be stamped (fallback chain)"
    assert expired_at, "expiredAt must be stamped"
    # window end == successor's validFrom (absent) → createdAt fallback
    assert valid_to == claim_b["createdAt"]


# ── #6180: the operator's reverse INPUT mirror travels with its support ──

def test_supersede_transfers_operator_input_mirror(sdk):
    """#6180: `(support)-[:INPUT {idx:0}]->(operator)` IS the support source
    why.py reads (`_SUPPORT_OP_CYPHER`). Leaving it on the superseded source
    emptied the replacement's support chain and let the dead point keep
    exerting influence through the operator — both must be gone."""
    from tortoise.why import assemble_why_blocks

    old = _make_point(sdk, content="Old supported source")
    target = _make_point(sdk, content="Target claim")
    sdk.create_operator("IMPL", old["id"], [target["id"]])
    new = _make_point(sdk, content="New supported source")
    proj = sdk._get_proj()

    pre = assemble_why_blocks(proj, [target["id"]])[target["id"]]["support_chain"]
    assert pre, "precondition: the support chain resolves before the supersede"

    sdk.supersede_point(old["id"], new["id"])

    old_in = proj.g.query(
        "MATCH (old:Point {id:$o})-[r:INPUT]->(:Point {is_operator:true}) "
        "RETURN count(r)", params={"o": old["id"]}).result_set[0][0]
    assert old_in == 0, "the dead point must not keep the operator's INPUT"
    new_in = [r[0] for r in proj.g.query(
        "MATCH (new:Point {id:$n})-[r:INPUT]->(:Point {is_operator:true}) "
        "RETURN r.idx", params={"n": new["id"]}).result_set]
    assert new_in == [0], f"the successor must carry the idx-0 mirror, got {new_in}"

    post = assemble_why_blocks(proj, [target["id"]])[target["id"]]["support_chain"]
    assert post, "the successor's support chain must resolve after the supersede"
    assert new["id"] in {s["point_id"] for s in post}, (
        f"the chain must resolve THROUGH the successor, got {post}")


def test_supersede_transfers_target_input_mirror(sdk):
    """#6180: every INPUT edge whose source is the superseded point follows —
    including the operator's idx>0 target mirror, so no dead-incident INPUT
    edge is left behind."""
    claim = _make_point(sdk, content="Claim A")
    evidence = _make_point(sdk, kind="evidence", content="Evidence")
    sdk.create_operator("IMPL", evidence["id"], [claim["id"]])
    new = _make_point(sdk, content="Claim B")
    sdk.supersede_point(claim["id"], new["id"])
    proj = sdk._get_proj()

    old_in = proj.g.query(
        "MATCH (old:Point {id:$o})-[r:INPUT]->() RETURN count(r)",
        params={"o": claim["id"]}).result_set[0][0]
    assert old_in == 0
    new_idx = [r[0] for r in proj.g.query(
        "MATCH (new:Point {id:$n})-[r:INPUT]->() RETURN r.idx",
        params={"n": new["id"]}).result_set]
    assert new_idx == [1], f"the successor must carry the idx-1 mirror, got {new_idx}"


def test_supersede_keeps_alreadydecided_input_mirror(sdk):
    """#1080 carve-out reaches the INPUT leg too: an alreadyDecided operator is
    deliberately kept on the dead prior, so its INPUT mirror stays there.

    The plain operator (`op_move`) is a CONTROL: its mirror must MOVE. Without
    it these assertions also hold on the pre-fix code (which never touched the
    INPUT leg at all), so the control is what makes the test discriminate the
    #6180 fix instead of pinning an invariant every implementation satisfies.
    """
    old = _make_point(sdk, content="Old decision")
    target = _make_point(sdk, content="Target")
    op_keep = sdk.create_operator("IMPL", old["id"], [target["id"]],
                                  label="alreadyDecided")
    control_target = _make_point(sdk, content="Control target")
    op_move = sdk.create_operator("IMPL", old["id"], [control_target["id"]])
    new = _make_point(sdk, content="New decision")
    sdk.supersede_point(old["id"], new["id"])
    proj = sdk._get_proj()

    kept = {r[0] for r in proj.g.query(
        "MATCH (old:Point {id:$o})-[:INPUT]->(op:Point {is_operator:true}) "
        "RETURN op.id", params={"o": old["id"]}).result_set}
    assert kept == {op_keep["id"]}, (
        f"only the alreadyDecided operator keeps its mirror, got {kept}")
    moved = {r[0] for r in proj.g.query(
        "MATCH (new:Point {id:$n})-[:INPUT]->(op:Point {is_operator:true}) "
        "RETURN op.id", params={"n": new["id"]}).result_set}
    assert moved == {op_move["id"]}, (
        f"the control operator's mirror must move to the successor, got {moved}")


def test_supersede_mitigation_point_without_idx(sdk):
    """#6180 P1 regression: `mitigate_operator` writes `(m)-[:IMPL]->(op)` and
    `(op)-[:mitigated_by]->(m)` with NO idx, so the transfer receives
    `idx=None`. It MUST NOT issue `MERGE … {idx:null}` (FalkorDB: "Cannot merge
    node using null property value") and must still remove the old idx-less
    edge. The pre-fix CREATE left that edge stranded on the dead point; a naive
    CREATE→MERGE turns it into a hard failure on every mitigation point (and a
    journaled `PointSuperseded` then wedges `rebuild_all`).
    """
    source = _make_point(sdk, content="source")
    target = _make_point(sdk, content="target")
    op = sdk.create_operator("IMPL", source["id"], [target["id"]])
    mit = sdk.mitigate_operator(op["id"], "no control group", 0.3)
    new = _make_point(sdk, content="new mitigation context")
    proj = sdk._get_proj()

    pre = proj.g.query(
        "MATCH (op:Point {id:$op})-[r:mitigated_by]->(m:Point {id:$m}) "
        "RETURN r.idx", params={"op": op["id"], "m": mit["id"]}).result_set
    assert pre and pre[0][0] is None, (
        f"precondition: the edge is idx-less, got {pre}")

    sdk.supersede_point(mit["id"], new["id"])  # must not raise

    moved = proj.g.query(
        "MATCH (op:Point {id:$op})-[r:mitigated_by]->(m:Point {id:$m}) "
        "RETURN count(r)",
        params={"op": op["id"], "m": new["id"]}).result_set[0][0]
    assert moved == 1, "the idx-less edge must transfer to the successor"
    left = proj.g.query(
        "MATCH (op:Point {id:$op})-[r:mitigated_by]->(m:Point {id:$m}) "
        "RETURN count(r)",
        params={"op": op["id"], "m": mit["id"]}).result_set[0][0]
    assert left == 0, "the idx-less edge must be removed from the superseded point"


def test_supersede_does_not_duplicate_a_prewired_operator_edge(sdk):
    """#6180 complementary case: when the successor already carries the shared
    operator edge (the successor-first write pattern), the transfer must MERGE
    — a CREATE doubles the edge and inflates the operator's evidence weight
    while passing every "is the edge there?" check."""
    target = _make_point(sdk, content="target")
    old = _make_point(sdk, content="old")
    new = _make_point(sdk, content="new")
    op = sdk.create_operator("IMPL", old["id"], [target["id"]])
    proj = sdk._get_proj()
    # Caller pre-wires the shared edge onto the successor — and the shared
    # INPUT mirror — before the supersede.
    proj.g.query(
        "MATCH (op:Point {id:$op}), (new:Point {id:$new}) "
        "CREATE (op)-[:IMPL {idx:0}]->(new)",
        params={"op": op["id"], "new": new["id"]},
    )
    # ... and the shared INPUT mirror, so `mirror == 1` below genuinely tests
    # the MERGE collapse (a CREATE-based mirror leg would double it).
    proj.g.query(
        "MATCH (op:Point {id:$op}), (new:Point {id:$new}) "
        "CREATE (new)-[:INPUT {idx:0}]->(op)",
        params={"op": op["id"], "new": new["id"]},
    )

    sdk.supersede_point(old["id"], new["id"])

    typed = proj.g.query(
        "MATCH (op:Point {id:$op})-[r:IMPL {idx:0}]->(new:Point {id:$new}) "
        "RETURN count(r)", params={"op": op["id"], "new": new["id"]},
    ).result_set[0][0]
    assert typed == 1, f"the transfer must not double the pre-wired edge ({typed})"
    mirror = proj.g.query(
        "MATCH (new:Point {id:$new})-[ri:INPUT {idx:0}]->(op:Point {id:$op}) "
        "RETURN count(ri)", params={"op": op["id"], "new": new["id"]},
    ).result_set[0][0]
    assert mirror == 1, f"exactly one INPUT mirror, got {mirror}"
    # old is fully free of both halves
    assert proj.g.query(
        "MATCH (old:Point {id:$o})-[:INPUT]->(:Point {is_operator:true}) "
        "RETURN count(*)", params={"o": old["id"]}).result_set[0][0] == 0
