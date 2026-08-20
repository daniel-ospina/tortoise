"""SVBP Gate 4: Production Hardening — API integration + end-to-end test.

Validates SVBP wired into the Tortoise EventAPI:
  1. add_operator() triggers incremental SVBP
  2. get_confidence() returns SVBP-computed values
  3. Results match standalone SVBP expectations

Usage:
    python -m validation.svbp_gate4
"""
import sys, os  # noqa: E401, I001
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import tempfile, time  # noqa: E401, I001
import jax.numpy as jnp
import jax
import numpy as np

from tortoise.api import EventAPI, provenance
from tortoise.projection import FalkorProjection
from tortoise.log import EventLog


def main():
    print("=" * 72)
    print("SVBP Gate 4: Production Hardening — API Integration")
    print("=" * 72)

    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "gate4.db")
    log_path = os.path.join(tmpdir, "gate4.jsonl")

    proj = FalkorProjection(db_path)
    log = EventLog(log_path)
    api = EventAPI(log, initiated_by="user", agent_id="gate4", projection=proj)
    api.current_run = "gate4"
    api._emit("ingest_begin", source_id="gate4", extractor_version="manual@1.0")
    prov = lambda q: provenance("gate4", (0, 0), q, speaker="test", extracted_by="manual@1.0")  # noqa: E731

    # ── Test 1: Build graph → get confidence ──────────────────────
    print()
    print("--- Test 1: Build graph, query confidence ---")

    # Add 10 claims
    cids = [api.add_point(f"Claim {i}", "test", prov(f"c{i}")) for i in range(10)]

    # Add NAND operators
    api.add_operator("NAND", [cids[0], cids[1]], "test", prov("NAND A-B"))
    api.add_operator("NAND", [cids[2], cids[3]], "test", prov("NAND C-D"))

    # Add IMPL operators
    for src, tgt in [(4, 5), (6, 7), (8, 9)]:
        api.add_operator("IMPL", [cids[src], cids[tgt]], "test", prov(f"IMPL {src}-{tgt}"))

    # Query confidence (lazy-inits SVBP)
    t0 = time.time()
    conf_a = api.get_confidence(cids[0])
    elapsed = time.time() - t0
    print(f"  First confidence query: {elapsed:.1f}s (includes SVBP init)")

    assert conf_a is not None, "SVBP should return confidence"
    assert 0 < conf_a["mean"] < 1, f"Mean should be in (0,1), got {conf_a['mean']}"
    assert conf_a["variance"] > 0, "Variance should be positive"
    print(f"  Claim A: mean={conf_a['mean']:.4f}, var={conf_a['variance']:.4f}")

    # Query another claim (should be fast — SVBP already converged)
    t0 = time.time()
    conf_b = api.get_confidence(cids[1])
    elapsed_b = time.time() - t0
    print(f"  Claim B query: {elapsed_b:.4f}s (cached)")
    assert conf_b is not None
    print(f"  Claim B: mean={conf_b['mean']:.4f}, var={conf_b['variance']:.4f}")

    # ── Test 2: Incremental update ────────────────────────────────
    print()
    print("--- Test 2: Add operator → incremental update ---")

    # Add a new operator
    t0 = time.time()
    new_op = api.add_operator("IMPL", [cids[4], cids[6]], "test", prov("IMPL E-G"))  # noqa: F841
    elapsed_add = time.time() - t0
    print(f"  add_operator + SVBP update: {elapsed_add:.1f}s")

    # Query affected claims — should reflect new operator
    conf_e2 = api.get_confidence(cids[4])
    conf_g2 = api.get_confidence(cids[6])
    print(f"  After IMPL(E→G): E mean={conf_e2['mean']:.4f}, G mean={conf_g2['mean']:.4f}")

    # ── Test 3: All claims have confidence ────────────────────────
    print()
    print("--- Test 3: All claims confidence ---")
    all_ok = True
    for i, cid in enumerate(cids):
        conf = api.get_confidence(cid)
        if conf is None or not (0 < conf["mean"] < 1):
            print(f"  Claim {i}: FAIL — conf={conf}")
            all_ok = False
    print(f"  {'✓ All 10 claims OK' if all_ok else '✗ Some failed'}")

    # ── Test 4: Speed check ───────────────────────────────────────
    print()
    print("--- Test 4: Operator addition speed ---")
    times = []
    for i in range(5):
        # Add operator between unused claim pairs
        src = cids[(i * 2) % 8]
        tgt = cids[(i * 2 + 1) % 8 + 2]
        t0 = time.time()
        api.add_operator("IMPL", [src, tgt], "test", prov(f"perf-{i}"))
        times.append(time.time() - t0)
    avg_time = np.mean(times)
    max_time = np.max(times)
    speed_ok = avg_time < 1.0
    print(f"  Avg: {avg_time:.3f}s, Max: {max_time:.3f}s {'✓' if speed_ok else '✗ target <1s'}")

    # ── Test 5: Compare with standalone SVBP ──────────────────────
    print()
    print("--- Test 5: API SVBP vs standalone SVBP ---")
    from tortoise.svbp import TortoiseSVBP

    # Build same graph with standalone SVBP
    stand_factors = [
        ("NAND_AB", "NAND", [cids[0], cids[1]], 3.0),
        ("NAND_CD", "NAND", [cids[2], cids[3]], 3.0),
        ("IMPL_45", "IMPL", [cids[4], cids[5]], 1.0),
        ("IMPL_67", "IMPL", [cids[6], cids[7]], 1.0),
        ("IMPL_89", "IMPL", [cids[8], cids[9]], 1.0),
    ]
    stand_svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                              damping=0.5, max_iter=40, tol=5e-3, seed=42)
    stand_svbp.run(stand_factors)

    # Compare API results vs standalone
    w2s = []
    for i, cid in enumerate(cids):
        api_conf = api.get_confidence(cid)
        stand_conf = stand_svbp.compute_confidence(cid)
        # Both use Beta fit — compare via sampled W₂
        key = jax.random.PRNGKey(700 + i)
        api_samples = jax.random.beta(key, api_conf["alpha"], api_conf["beta"], (500,))
        stand_samples = jax.random.beta(key, stand_conf["alpha"], stand_conf["beta"], (500,))
        a_sort = jnp.sort(api_samples)
        s_sort = jnp.sort(stand_samples)
        w2 = float(jnp.sqrt(jnp.mean((a_sort - s_sort) ** 2)))
        w2s.append(w2)

    w2_max = max(w2s)
    w2_mean = np.mean(w2s)
    api_match = w2_max < 0.10
    print(f"  W₂ (API vs standalone): mean={w2_mean:.4f}, max={w2_max:.4f} "
          f"{'✓' if api_match else '✗ target <0.10'}")

    # ── Verdict ───────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("GATE 4 VERDICT")
    print("=" * 72)
    print(f"  Confidence API works:     {'✓ PASS' if all_ok else '✗ FAIL'}")
    print(f"  Incremental update <1s:   {'✓ PASS' if speed_ok else '✗ FAIL'} (avg={avg_time:.3f}s)")
    print(f"  API matches standalone:    {'✓ PASS' if api_match else '✗ FAIL'} (W₂ max={w2_max:.4f})")

    gate4_pass = all_ok and speed_ok and api_match
    if gate4_pass:
        print()
        print("  ★ GATE 4 PASSES ★ — SVBP is production-ready for Tortoise.")
    else:
        print()
        print("  ⚠️ GATE 4 NEEDS WORK — see failures above.")

    proj.close()
    return gate4_pass


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
