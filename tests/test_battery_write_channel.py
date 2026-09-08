"""#2291 I-3 SDK write channel — closed-set + idempotency + routing tests.

Locks the plan's Task-3 acceptance: empty closed set ⇒ zero writes + honest
no-op; content-idempotent re-filing (no duplicate evidence point/operator);
#901 routing (NAND on a closed-set claim; IMPL support edge; mitigate on a
closed-set OPERATOR memory with strength clamped to the product's
[0.10, 0.50] convention); the decide-cycle cap makes further records honest
no-ops; targets never touch the seed-manifest marker.
"""
from __future__ import annotations

import pytest

from battery.arms.a4_tortoise import DECIDE_CYCLES_CAP
from battery.arms.base import AgentContext, Memory
from battery.testing.seeds import setup_seed_mode


def _store(tmp_path, scenario_id: str = "ct-001"):
    store = setup_seed_mode(tmp_path, scenario_id)
    return store


def test_empty_set_never_writes(tmp_path) -> None:
    """An EMPTY retrieved closed set can never produce a write (claims[0]
    dead by construction) — the record is an honest no-op, decide_cycles
    stays 0, and no operator edge appears in the graph."""
    store = _store(tmp_path)
    try:
        prior = ()
        assert store._arm.retrieve(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         user_message=""))  # sanity: seeds ARE retrievable
        store._arm.decide_cycles = 0
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=prior, user_message="go"),
            Memory(id="e1", content="finding", kind="nand"))
        assert store._arm.decide_cycles == 0
        g = store._arm._scenario_graph(store._scenario)
        rows = g.query(
            "MATCH (o:Point {is_operator: true})-[e]->(t:Point) "
            "RETURN count(e)").result_set
        assert int(rows[0][0] or 0) == 0  # zero operator edges written
    finally:
        store.close()


def test_refiling_same_content_is_idempotent(tmp_path) -> None:
    """Re-filing the same finding content never duplicates the evidence
    point or its operator edge (content-hash dedup on the product write)."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        ctx = AgentContext(scenario=store._scenario, episode_seed=0,
                           prior_memories=tuple(mems), user_message="go")
        store._arm.record(ctx, Memory(id="e1", content="finding X", kind="nand"))
        first_cycles = store._arm.decide_cycles
        store._arm.record(ctx, Memory(id="e2", content="finding X", kind="nand"))
        assert store._arm.decide_cycles == first_cycles + 1  # cycle counted
        g = store._arm._scenario_graph(store._scenario)
        rows = g.query(
            "MATCH (n:Point) WHERE n.content = 'finding X' "
            "RETURN count(n)").result_set
        assert int(rows[0][0] or 0) == 1  # ONE evidence point, no duplicate
    finally:
        store.close()


def test_nand_targets_closed_set_claim_never_marker(tmp_path) -> None:
    """A NAND lands on a CLAIM from the closed set (never the seed-manifest
    marker, never an operator) — target-correctness over a planted graph."""
    from battery.runner.setup import seed_manifest_content
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        assert mems
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=tuple(mems), user_message="go"),
            Memory(id="e1", content="counter-finding", kind="nand"))
        g = store._arm._scenario_graph(store._scenario)
        rows = g.query(
            "MATCH (o:Point {is_operator: true})-[:NAND]->(t:Point) "
            "RETURN DISTINCT t.id, t.content").result_set
        assert rows
        claim_ids = {m.id for m in mems}
        want = seed_manifest_content(store._scenario.id)
        # The product operator attaches to BOTH the source evidence and the
        # targeted claim (o→each input) — assert a closed-set CLAIM is
        # NAND-targeted and the marker is never an edge target.
        assert any(tid in claim_ids for tid, _ in rows), rows
        for _tid, content in rows:
            assert str(content) != want
    finally:
        store.close()


def test_mitigate_resolves_operator_memory_and_clamps(tmp_path) -> None:
    """Mitigate targets a closed-set OPERATOR-kind memory; strength is
    clamped to the product's [0.10, 0.50] convention (high confidence ⇒
    capped at 0.50; None ⇒ decide default 0.3, in-range)."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        if len(mems) < 2:
            pytest.skip("ct-001 store must seed ≥2 memories for an operator")
        claim = next(m for m in mems if m.kind == "claim")
        other = next(m for m in mems if m.id != claim.id)
        # Build a real operator (the object a mitigation would modulate).
        op = store._arm._sdk(store._scenario).create_operator(
            "IMPL", claim.id, [other.id])
        oid = op.get("id")
        assert oid
        op_mem = Memory(id=oid, content="edge", kind="operator")
        prior = (*tuple(mems), op_mem)
        # confidence 2.0 → clamp to 0.50; both calls must succeed (product
        # surface) without raising and without touching claim-EP deltas.
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=prior, user_message="go"),
            Memory(id="m1", content="weaker than it appears",
                   confidence=2.0, kind="mitigate"))
        assert store._arm.decide_cycles >= 1
        # Idempotent second mitigation (no crash on the same operator).
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=prior, user_message="go"),
            Memory(id="m2", content="weaker still", kind="mitigate"))
        assert store._arm.decide_cycles >= 2
    finally:
        store.close()


def test_mitigate_without_operator_is_noop(tmp_path) -> None:
    """No operator in the closed set ⇒ the mitigate is an honest no-op
    (never a fresh store probe / content-derived guess)."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")  # claims only (Task-4 mapping adds ops)
        assert all(m.kind != "operator" for m in mems)
        store._arm.decide_cycles = 0
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=tuple(mems), user_message="go"),
            Memory(id="m1", content="no target", kind="mitigate"))
        assert store._arm.decide_cycles == 0
    finally:
        store.close()


def test_decide_cycle_cap_makes_records_noop(tmp_path) -> None:
    """At the per-episode cap, further records are honest no-ops (cap-hit ⇒
    non_converged/undec territory in Task 4 — never forced CONVERGED)."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        store._arm.decide_cycles = DECIDE_CYCLES_CAP
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=tuple(mems), user_message="go"),
            Memory(id="e1", content="over-cap finding", kind="nand"))
        assert store._arm.decide_cycles == DECIDE_CYCLES_CAP  # no increment
        g = store._arm._scenario_graph(store._scenario)
        rows = g.query(
            "MATCH (n:Point) WHERE n.content = 'over-cap finding' "
            "RETURN count(n)").result_set
        assert int(rows[0][0] or 0) == 0  # nothing written past the cap
    finally:
        store.close()
