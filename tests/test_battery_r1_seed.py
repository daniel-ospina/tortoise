# tests/test_battery_r1_seed.py — I-1 seeded-graph confound fix (#2284, Task 4)
"""Run-path loader only (battery.config.corpus.load_corpus -> Scenario list,
yaml source — mirrors run.py:153). corpus_loader is the DICT reader and is
never used here. Helpers live in battery/testing/seeds.py (the SINGLE
test-support home Task 4/10 import) — never left to executing-plans to
invent.

Locked contract: seed_mode never pre-seeds claim_b/k/NAND for contradiction
scenarios (¬A arrives in-context at turn k); A4 pre-k = claim_a + evidence
only; no-leak over the FULL ct population x every arm's real pre-k surface;
warm stores fail closed on stale PRE-FIX graphs (seeder-owned marker, never
raw content presence); ct≡bct via the SHARED surface_diff predicate;
derive_scenario_graph consumes graph_script so R3-for-lp has a real EP
surface (3 NAND edges + contested-pair binding).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from battery.config.arms import load_arms
from battery.config.control_diff import NEG_A_DELTA_SLOTS, surface_diff
from battery.config.corpus import load_corpus
from battery.exceptions import ConfigError
from battery.runner.setup import derive_scenario_graph
from battery.testing import seeds

_CONFIG = Path(__file__).resolve().parents[1] / "battery/config"
_CORPUS_YAML = _CONFIG / "corpus.yaml"
_ARMS_YAML = _CONFIG / "arms.yaml"


def _cts() -> list:
    return [sc for sc in load_corpus(_CORPUS_YAML) if sc.id.startswith("ct-")]


def _scenario(scenario_id):
    return next(sc for sc in load_corpus(_CORPUS_YAML) if sc.id == scenario_id)


def _fragments(sc) -> list:
    """¬A fragments from ALL planted pairs (empty for benign bct twins)."""
    return [p.claim_b[:40] for p in sc.contradiction_pairs]


def _search(store, fragments):            # each fragment separately
    return [(fr, store.find_content(fr)) for fr in fragments]


_CT_IDS = [sc.id for sc in _cts()]


# ── seed_mode content absence (white-box derive + real store) ─────────────
def test_seed_mode_derive_never_emits_claim_b_k_or_nand():
    """The runner's derivation in seed_mode never emits claim_b / k / NAND
    for a contradiction scenario; claim_a stays (adopted claim memory)."""
    sc = _cts()[0]
    graph = derive_scenario_graph(sc)  # seed_mode is the default
    assert graph.operators == []
    contents = [p["content"] for p in graph.points]
    assert sc.contradiction_pairs[0].claim_a in contents
    assert sc.contradiction_pairs[0].claim_b not in contents
    assert not any(p.get("k") is not None or p.get("contradiction")
                   for p in graph.points)


def test_seed_full_legacy_is_the_prefix_derivation(tmp_path):
    """The legacy (pre-fix) derivation is REACHABLE (seed_full_legacy +
    relocked harness tests use it): full graph incl. claim_b + NAND."""
    sc = _cts()[0]
    graph = derive_scenario_graph(sc, seed_mode=False)
    nands = [o for o in graph.operators if o["op_type"] == "NAND"]
    assert len(nands) == 1  # the planted pair's NAND
    contents = [p["content"] for p in graph.points]
    assert sc.contradiction_pairs[0].claim_b in contents


def test_claim_b_never_preseeded_in_seed_mode(tmp_path):
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)
    try:
        hits = _search(store, _fragments(sc))
        assert not [h for _, h in hits if h], f"¬A leaked pre-k: {hits}"
    finally:
        store.close()


def test_retrieve_pre_k_has_only_claim_a_evidence(tmp_path):
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)
    try:
        mems = store.retrieve(sc.to_episode_context()["render"][:200])
        texts = " ".join(str(m) for m in mems)
        assert not any(f in texts for f in _fragments(sc))
        assert sc.contradiction_pairs[0].claim_a[:40] in texts or any(
            sc.contradiction_pairs[0].claim_a[:40] in str(m.get("content", ""))
            for m in mems)
    finally:
        store.close()


# ── no-leak over the FULL ct population x every arm's pre-k surface ───────
@pytest.mark.parametrize("scenario_id", _CT_IDS)
def test_no_leak_full_policy_surface_all_arms(tmp_path, scenario_id):
    """¬A absent from every arm's real pre-k surface over the FULL ct
    population. Surface composition per arm (pinned, not a tuple — seeds.py
    consults a hermetic capability key in arms.yaml): a4 = seeded hermetic
    store + rendered policy truncated BEFORE turn k (pre-k projection);
    mock/a0/a1 + vendor arms (a2/a2b/a3) = policy surface only unless the
    hermetic carve-out capability key exists in arms.yaml."""
    sc = _scenario(scenario_id)
    fragments = _fragments(sc)
    for arm_cfg in load_arms(_ARMS_YAML).values():
        surface = seeds.real_prek_surface(
            arm_cfg.arm_id, scenario_id, namespace=tmp_path)
        assert not any(f in surface for f in fragments), \
            f"{arm_cfg.arm_id} leaked ¬A for {scenario_id}"


@pytest.mark.parametrize("n", range(1, 7))
def test_bct_benign_store_never_carries_twin_counterclaim(tmp_path, n):
    """bct-001..006 benign stores + policy carry NO ¬A content of their
    ct-00N twin (the benign surface has no planted pair to leak)."""
    ct_id, bct_id = f"ct-00{n}", f"bct-00{n}"
    twin = _scenario(ct_id)
    store = seeds.setup_seed_mode(tmp_path, bct_id)
    try:
        hits = _search(store, _fragments(twin))
        surface = seeds.prek_policy_render(_scenario(bct_id))
        assert not [h for _, h in hits if h], f"bct-00{n} carries twin ¬A"
        assert not any(f in surface for f in _fragments(twin))
    finally:
        store.close()


# ── ct≡bct via the SHARED surface_diff predicate (Task 3 home) ────────────
@pytest.mark.parametrize("n", range(1, 7))
def test_bct_twin_surface_equality(n):
    """ct-00N ≡ bct-00N apart from the ¬A turn + benign-question slot."""
    assert surface_diff(f"ct-00{n}", f"bct-00{n}") <= NEG_A_DELTA_SLOTS


# ── warm-store fail-closed on stale PRE-FIX graphs ────────────────────────
def test_seed_mode_warm_store_fails_closed_on_stale(tmp_path):
    """seed_mode over a stale PRE-FIX full graph refuses (seeder-owned
    marker distinguishes stale-seeder content from agent-filed content: the
    guard tests a seed-manifest marker written by the seeder, never raw
    content presence). Agent-filed claim_b content (Task 9/10) must NOT
    false-refuse — locked in Task 10."""
    seeds.seed_full_legacy(tmp_path, "ct-001")  # pre-fix seeding (fresh store)
    # purge=False: observe the existing legacy namespace — the warm guard
    # must refuse (marker absent + planted content present).
    with pytest.raises(ConfigError):
        store = seeds.setup_seed_mode(tmp_path, "ct-001", purge=False)
        store.close()  # pragma: no cover — setup must refuse


def test_seed_mode_warm_store_accumulates_over_clean(tmp_path):
    """Re-setup over a CLEAN seed_mode graph from a prior session
    accumulates (no refuse, no duplicate claim_a) — the Task 10 stream
    default, locked here at the seed boundary."""
    store1 = seeds.setup_seed_mode(tmp_path, "ct-001")  # fresh seed (purge)
    try:
        before = store1.surface_text()
    finally:
        store1.close()
    # purge=False: re-setup over the CLEAN marker-present graph accumulates.
    store2 = seeds.setup_seed_mode(tmp_path, "ct-001", purge=False)
    try:
        after = store2.surface_text()
        assert "server A is the bottleneck" in after  # claim_a still present
        assert "server A is not the bottleneck" not in after  # ¬A never seeded
        # accumulate, not duplicate: same deterministic claim_a appears once
        assert after.count("server A is the bottleneck") == before.count(
            "server A is the bottleneck")
    finally:
        store2.close()


def test_seed_mode_store_owns_seed_manifest_marker(tmp_path):
    """The seeder-owned seed-manifest marker is written into the seeded
    namespace (the warm guard's ownership record — Task 10's agent-filed
    content never false-refuses BECAUSE the marker is present) and is NOT
    part of the retrievable memory surface."""
    from battery.runner.setup import seed_manifest_point_id
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)
    try:
        g = store._arm._scenario_graph(sc)
        rows = g.query(
            "MATCH (n:Point {id: $mid}) RETURN n.content",
            params={"mid": seed_manifest_point_id(sc.id)}).result_set
        assert len(rows) == 1
        surface = store.surface_text()
        assert "seed_manifest" not in surface  # never a retrievable memory
    finally:
        store.close()


def test_record_never_targets_seed_manifest_marker(tmp_path):
    """A4 record() claim-targets exclude the seeder-owned marker (the
    retrieve exclusion is mirrored on the write path) — an agent-filed
    NAND/IMPL edge lands on a seeded statement, never on the marker."""
    from battery.arms.base import AgentContext, Memory
    from battery.runner.setup import seed_manifest_point_id
    sc = _cts()[0]
    store = seeds.setup_seed_mode(tmp_path, sc.id)
    try:
        store._arm.record(
            AgentContext(scenario=sc, episode_seed=0, user_message="go"),
            Memory(id="e1", content="finding", kind="nand"))
        g = store._arm._scenario_graph(sc)
        rows = g.query(
            "MATCH (o:Point {is_operator: true})-[e]->(t:Point) "
            "RETURN t.id, t.content LIMIT 3").result_set
        assert rows
        for tid, content in rows:
            assert tid != seed_manifest_point_id(sc.id)
            assert content  # never the empty-content marker
    finally:
        store.close()


@pytest.mark.parametrize("scenario_id", [f"xs-00{n}" for n in range(1, 7)])
def test_xs_planted_pairs_never_preseeded(tmp_path, scenario_id):
    """xs-* (L4 cross-session) planted pairs follow the same seed_mode
    contract: ¬A arrives in-context at its authored session at run time —
    never pre-seeded into the graph store (Task 10 extends the surfacing
    semantics; the seed boundary is locked here for the whole population)."""
    sc = _scenario(scenario_id)
    store = seeds.setup_seed_mode(tmp_path, scenario_id)
    try:
        mems = store.retrieve("")
        texts = " ".join(str(m) for m in mems)
        for pair in sc.contradiction_pairs:
            assert pair.claim_a[:40] in texts or any(
                pair.claim_a[:40] in str(m.get("content", "")) for m in mems)
            assert pair.claim_b[:40] not in texts
    finally:
        store.close()


# ── Step 4.3b — derive_scenario_graph consumes graph_script (R3-for-lp) ───
def test_lp_derive_emits_nand_triangle_and_contested_binding():
    """derive_scenario_graph on a REAL lp-* scenario (graph_script dict
    sub-shape: node-id NAND triangle + contested_pair refs) emits the 3 NAND
    edges + contested-pair binding so R3-for-lp has a real EP surface."""
    lp = _scenario("lp-001")
    gs = lp.graph_script
    graph = derive_scenario_graph(lp)
    nands = [o for o in graph.operators if o["op_type"] == "NAND"]
    assert len(nands) == 3  # the loopy triangle: p-q, q-r, r-p
    assert all(o["direction"] == "unidirectional" for o in nands)
    # contested-pair binding materialized: the A/¬A points carry the binding
    # props and their contents are the authored contested texts.
    by_content = {p["content"]: p for p in graph.points}
    a = by_content[gs["contested_pair"]["a"]]
    neg_a = by_content[gs["contested_pair"]["neg_a"]]
    assert a["contested_pair"] == "a"
    assert neg_a["contested_pair"] == "neg_a"
    # every NAND edge resolves to seeded point ids (endpoint validity) and
    # the triangle covers the contested pair + the third node.
    known = set(graph.point_ids)
    for op in nands:
        assert all(i["id"] in known for i in op["inputs"])
    edge_pairs = {frozenset(i["id"] for i in op["inputs"]) for op in nands}
    assert len(edge_pairs) == 3  # 3 distinct NAND operator edges


def test_lp_derive_refuses_unresolvable_graph_script(tmp_path):
    """Present-but-unresolvable graph_script sub-shapes raise ConfigError
    (never a silent drop): contested refs must resolve to node ids and
    contested claim texts must be non-empty."""
    from dataclasses import replace
    lp = _scenario("lp-001")
    gs = lp.graph_script

    bad_ref = dict(gs)
    bad_ref["contested_pair"] = {
        **gs["contested_pair"], "a_ref": "nope"}
    with pytest.raises(ConfigError):
        derive_scenario_graph(replace(lp, graph_script=bad_ref))

    empty_text = dict(gs)
    empty_text["contested_pair"] = {
        **gs["contested_pair"], "a": "   "}
    with pytest.raises(ConfigError):
        derive_scenario_graph(replace(lp, graph_script=empty_text))

    bad_turn_ref = dict(gs)
    bad_turn_ref["nodes"] = [
        {"id": "p", "claim_or_turn_ref": 0},
        {"id": "q", "claim_or_turn_ref": 1},
        {"id": "r", "claim_or_turn_ref": 99},
    ]
    with pytest.raises(ConfigError):
        derive_scenario_graph(replace(lp, graph_script=bad_turn_ref))
