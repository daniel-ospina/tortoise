"""Calibration tests for the epic 903 shared fixtures (#1250, epic 903-C12).

Proves the five builders are deterministic and fit-for-purpose:
- F1 constructs: 60 claims / 20 premises / 25 IMPL edges / 6 derivation trees
  / 10 NAND contradictions / 5 near-dup pairs; EP converges on the corpus.
- F2 stamps are the fixed ISO values (direct Cypher SET — never wall-clock);
  the null region and the isolated claim carry no stamp.
- F3 actually FAILS convergence within max_iter (the calibration — the
  eval-spec B7 odd-NAND triangle is shown to converge trivially, documenting
  why it is NOT suitable).
- F5 has the pinned counts; fan-out sums to the edge count.
- F4's oracle is computed on a separate sandboxed clone (distinct DB path)
  and is reproducible from scratch.

Hermetic embedded pattern per tests/test_dream.py; single-SDK threading;
per-test fresh fixtures → order-independent under pytest-randomly.
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: F401, I001

from tests.epic903_fixtures import (
    EP_MAX_ITER,
    FIXED_SEED,
    F5_FAN_OUT,
    F5_N_CLAIMS,
    F5_N_EDGES,
    F5_N_OPERATORS,
    STAMP_FRESH,
    STAMP_MEDIUM,
    STAMP_OLD,
    compute_diagnostics_stats,
    f1_corpus,
    f2_staleness_regions,
    f3_nonconvergent,
    f4_frozen_truth,
    f5_diagnostics,
    fresh_sdk,
    _make_claim,
)
from tortoise.sdk import TortoiseSDK  # noqa: F401


# ── F1 — EP-parity corpus ───────────────────────────────────────────


class TestF1Corpus:
    def test_f1_counts_match_spec_shape(self):
        f = f1_corpus(seed=FIXED_SEED)
        try:
            assert f.n_claims == 60, f"expected 60 claims, got {f.n_claims}"
            premises = {k for k in f.claims if k.startswith("p")}
            tree = {k for k in f.claims if k.startswith("t")}
            noise = {k for k in f.claims if k.startswith("n")}
            assert len(premises) == 20
            assert len(tree) == 25
            assert len(noise) == 15
            assert f.n_impl == 25, f"expected 25 IMPL edges, got {f.n_impl}"
            assert f.n_nand == 10, f"expected 10 NAND edges, got {f.n_nand}"
        finally:
            f.sdk.close()

    def test_f1_six_derivation_trees(self):
        """The 25 IMPL edges form exactly 6 CONNECTED derivation trees whose
        leaves are premises and whose conclusions are tree claims (corpus v1
        shape)."""
        f = f1_corpus(seed=FIXED_SEED)
        try:
            proj = f.sdk._get_proj()
            rows = proj.g.query(
                "MATCH (o:Point {is_operator:true, op_type:'IMPL'})-[r:IMPL]->(c:Point) "
                "RETURN o.id, c.id"
            ).result_set
            # 25 distinct IMPL operators (each operator is one edge).
            assert len({r[0] for r in rows}) == 25

            # Connected components among TREE claims via IMPL edges == 6.
            parent: dict[str, str] = {}

            def find(x: str) -> str:
                if x not in parent:
                    parent[x] = x
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: str, b: str) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            tree_claims = {f.claims[f"t{i}"] for i in range(1, 26)}
            # one query for ALL IMPL operator→input edges with their idx —
            # union each operator's idx:0 source with its targets (a tree).
            edge_rows = proj.g.query(
                "MATCH (o:Point {is_operator:true, op_type:'IMPL'})-"
                "[r:IMPL]->(c:Point) RETURN o.id, c.id, r.idx"
            ).result_set
            by_op: dict[str, dict[int, str]] = {}
            for op_id, cid, idx in edge_rows:
                by_op.setdefault(op_id, {})[idx] = cid
            for inputs in by_op.values():
                src = inputs.get(0)
                for tgt in inputs.values():
                    union(src, tgt)
            roots = {find(c) for c in tree_claims}
            assert len(roots) == 6, (
                f"expected 6 derivation-tree components, got {len(roots)}"
            )

            # premises carry calibrated baselines (EP-viable evidence).
            for key in ("p1", "p2", "p20"):
                row = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.baseline_set",
                    params={"id": f.claims[key]},
                ).result_set
                assert row[0][0] is True, f"{key} must be baseline-calibrated"
        finally:
            f.sdk.close()

    def test_f1_five_near_dup_pairs(self):
        """5 near-dup pairs = 10 noise claims sharing identical content."""
        f = f1_corpus(seed=FIXED_SEED)
        try:
            proj = f.sdk._get_proj()
            contents = {}
            for i in range(1, 16):
                row = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.content",
                    params={"id": f.claims[f"n{i}"]},
                ).result_set
                contents[f"n{i}"] = row[0][0]
            dup_pairs = 0
            for i in range(1, 11, 2):
                if contents[f"n{i}"] == contents[f"n{i + 1}"]:
                    dup_pairs += 1
            assert dup_pairs == 5, f"expected 5 near-dup pairs, got {dup_pairs}"
            # unique noise claims are distinct from everything else.
            assert len({contents[f"n{i}"] for i in range(11, 16)}) == 5
        finally:
            f.sdk.close()

    def test_f1_ep_converges(self):
        """EP-parity: a full-graph EP run over the corpus converges (the
        corpus is a well-formed epistemic graph, not a degenerate one)."""
        f = f1_corpus(seed=FIXED_SEED)
        try:
            random.seed(FIXED_SEED)
            all_ops = list(f.operators.values())
            result = f.sdk.compute_confidence(factors=all_ops,
                                              require_calibration=False)
            assert result["converged"] is True
            # converged BEFORE exhausting the cap — i.e., the production
            # tolerance EP_TOL was actually reached (not a vacuous pass).
            assert 1 <= result["iterations"] < EP_MAX_ITER
            assert len(result["confidences"]) >= 1
        finally:
            f.sdk.close()


# ── F2 — staleness fixture ──────────────────────────────────────────


class TestF2Staleness:
    def test_f2_stamps_are_fixed_iso_values(self):
        """Stamps read back EXACTLY equal the fixed ISO constants — never
        wall-clock manufacturing (a sub-second pass would produce identical
        stamps → the old flaky behavior)."""
        f = f2_staleness_regions()
        try:
            proj = f.sdk._get_proj()
            by_name = {r.name: r for r in f.regions}
            assert by_name["old"].stamp == STAMP_OLD
            assert by_name["medium"].stamp == STAMP_MEDIUM
            assert by_name["fresh"].stamp == STAMP_FRESH
            assert by_name["null"].stamp is None

            for region in f.regions:
                for pid in region.claims:
                    row = proj.g.query(
                        "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
                        params={"id": pid},
                    ).result_set
                    actual = row[0][0]
                    assert actual == region.stamp, (
                        f"region {region.name}: expected stamp {region.stamp!r}, "
                        f"got {actual!r}"
                    )
        finally:
            f.sdk.close()

    def test_f2_null_region_and_isolated_claim_unstamped(self):
        f = f2_staleness_regions()
        try:
            proj = f.sdk._get_proj()
            null_region = next(r for r in f.regions if r.name == "null")
            for pid in null_region.claims:
                row = proj.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
                    params={"id": pid},
                ).result_set
                assert row[0][0] is None, "null region must carry NO stamp"
            row = proj.g.query(
                "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
                params={"id": f.isolated_claim},
            ).result_set
            assert row[0][0] is None, "isolated claim must carry NO stamp"
        finally:
            f.sdk.close()

    def test_f2_regions_disconnected_with_operators(self):
        """DE2E-1 contract: ≥3 disconnected regions, each with ≥1 operator."""
        f = f2_staleness_regions()
        try:
            assert len(f.regions) == 4
            all_claims = set()
            for region in f.regions:
                assert len(region.operators) >= 1
                assert not (all_claims & set(region.claims)), (
                    f"region {region.name} shares claims with another region"
                )
                all_claims |= set(region.claims)
        finally:
            f.sdk.close()


# ── F3 — fails-to-converge calibration ──────────────────────────────


class TestF3Calibration:
    def test_f3_fails_convergence_within_max_iter(self):
        """THE calibration: dreaming the F3 structure must return
        converged=False, exhausting the production max_iter. If this ever
        starts passing, the structure has converged — adjust the 2:1
        IMPL:NAND force balance until the limit cycle returns (documented in
        f3_nonconvergent's docstring)."""
        f = f3_nonconvergent()
        try:
            random.seed(FIXED_SEED)
            result = f.sdk.dream(dirty_only=True)
            assert result["converged"] is False, (
                "F3 converged — the fails-to-converge structure lost its "
                "limit cycle; re-calibrate"
            )
            assert result["iterations"] == EP_MAX_ITER, (
                f"expected max_iter={EP_MAX_ITER} exhausted, got "
                f"{result['iterations']}"
            )
        finally:
            f.sdk.close()

    def test_f3_robust_across_seeds(self):
        """The limit cycle must not be a factor-order fluke: fresh builds
        fail under several seeds (shuffle order varies)."""
        for seed in (1, 42, 2026):
            f = f3_nonconvergent()
            try:
                random.seed(seed)
                result = f.sdk.dream(dirty_only=True)
                assert result["converged"] is False, f"seed {seed} converged"
            finally:
                f.sdk.close()

    def test_f3_b7_triangle_unsuitable_negative_control(self):
        """Documents why the eval-spec B7 odd-NAND triangle is NOT the F3
        structure: it converges trivially today (plan Substep 7 note). The
        FULL triangle (all three NAND operators) is exercised — not a
        2-NAND fragment — so this guard rots loudly if the real triangle
        ever starts oscillating."""
        sdk, _ = fresh_sdk(prefix="tortoise_epic903_b7_")
        try:
            ids = {name: _make_claim(sdk, name)["id"]
                   for name in ("A", "B", "C")}
            ops = [
                sdk.create_operator("NAND", ids[a], [ids[b]],
                                    direction="bidirectional")["id"]
                for a, b in (("A", "B"), ("B", "C"), ("C", "A"))
            ]
            for name in ("A", "B", "C"):
                sdk.set_point_baseline(ids[name], 10.0, 1.0)
            random.seed(FIXED_SEED)
            result = sdk.compute_confidence(factors=ops,
                                            require_calibration=False)
            assert result["converged"] is True, (
                "B7 triangle unexpectedly non-convergent — re-examine whether "
                "it is now a suitable F3 candidate"
            )
        finally:
            sdk.close()


# ── F4 — frozen-ground-truth fixture ────────────────────────────────


class TestF4FrozenTruth:
    def test_f4_oracle_computed_on_sandboxed_clone(self):
        """The oracle must come from a SEPARATE DB path (sandboxed clone),
        never from the live fixture."""
        f = f4_frozen_truth(seed=FIXED_SEED)
        try:
            assert f.clone_db_path != f.db_path, (
                "oracle must be computed on a distinct sandboxed clone path"
            )
            assert os.path.dirname(f.clone_db_path) != os.path.dirname(f.db_path), (
                "clone and live fixtures must not share a tempfile directory"
            )
            # oracle covers exactly the live fixture's claims, keyed by the
            # stable corpus keys (clone's ulid ids differ from live's).
            assert set(f.oracle.keys()) == set(f.ids.keys())
            for key, mean in f.oracle.items():
                assert 0.0 <= mean <= 1.0, f"oracle {key} mean {mean} out of range"
                assert f.ids[key], f"oracle key {key} missing live id"
        finally:
            f.sdk.close()

    def test_f4_oracle_reproducible_from_scratch(self):
        """Frozen ground truth must be reproducible: a from-scratch recompute
        on a SECOND sandboxed clone matches the oracle (tight tolerance — the
        corpus converges to a fixed point, calibrated |Δ| = 0.0)."""
        f = f4_frozen_truth(seed=FIXED_SEED)
        try:
            clone_sdk, clone_db = fresh_sdk(prefix="tortoise_epic903_f4_check_")  # noqa: RUF059
            try:
                from tests.epic903_fixtures import _build_f4_graph  # noqa: PLC0415, RUF100
                clone_ids, clone_ops = _build_f4_graph(clone_sdk)
                random.seed(FIXED_SEED)
                clone_sdk.compute_confidence(factors=clone_ops,
                                             require_calibration=False)
                for key, mean in f.oracle.items():
                    recomputed = clone_sdk.get_confidence(clone_ids[key])["mean"]
                    assert abs(recomputed - mean) < 1e-6, (
                        f"oracle not reproducible for {key}: "
                        f"recomputed {recomputed} vs oracle {mean}"
                    )
            finally:
                clone_sdk.close()
        finally:
            f.sdk.close()


# ── F5 — diagnostics fixture ────────────────────────────────────────


class TestF5Diagnostics:
    def test_f5_pinned_counts(self):
        f = f5_diagnostics()
        try:
            stats = f.stats
            assert stats["n_claims"] == F5_N_CLAIMS, stats
            assert stats["n_operators"] == F5_N_OPERATORS, stats
            assert stats["n_edges"] == F5_N_EDGES, stats
            assert stats["fan_out"] == dict(sorted(F5_FAN_OUT.items())), stats
        finally:
            f.sdk.close()

    def test_f5_fan_out_sums_to_edge_count(self):
        f = f5_diagnostics()
        try:
            fan_total = sum(arity * count
                            for arity, count in f.stats["fan_out"].items())
            assert fan_total == f.stats["n_edges"], (
                f"fan-out {f.stats['fan_out']} must sum to edge count "
                f"{f.stats['n_edges']}"
            )
            # region/neighborhood + component stats emitted for DE2E-10.
            assert f.stats["n_components"] >= 1
            assert sum(f.stats["component_sizes"]) == (
                f.stats["n_claims"] + f.stats["n_operators"]
            )
        finally:
            f.sdk.close()

    def test_f5_stats_computable_after_reopen(self):
        """compute_diagnostics_stats is idempotent on the same graph (DE2E-10
        re-runs diagnostics without rebuilding)."""
        f = f5_diagnostics()
        try:
            stats2 = compute_diagnostics_stats(f.sdk)
            assert stats2 == f.stats
        finally:
            f.sdk.close()
