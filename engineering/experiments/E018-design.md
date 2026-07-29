# E018 Experiment Design

**Date:** 2026-07-29
**Pipeline:** experiment-workflow → Stage 4 — DESIGN
**Pre-registration:** [E018-preregistration.md](./E018-preregistration.md)

## 1. Experiment Type

**Mathematical validation experiment.** Unlike product experiments (A/B tests on live users), this validates the EP engine's mathematical behavior. No user data, no statistical sampling — it's a property test of the belief propagation algorithm.

## 2. Independent Variable

**Source credibility configuration:**
- Source tier (T0-T4) — categorical
- Source count (1, 2, 3, 5, 10, 100, 1000) — continuous (log-scale)
- Source connection topology (single-point, multi-point)
- Edge type (IMPL, NAND, mitigated)
- Graph structure (linear chain, loopy cluster single-entry, loopy cluster multi-entry)

## 3. Dependent Variable

**EP-computed confidence mean** (α/(α+β)) for claim points after convergence. Measured via `tortoise_get_confidence`.

## 4. Experimental Setup

### 4.1 Infrastructure
- **Database:** FalkorDB (in-memory `FalkorDBLite` for test isolation)
- **Engine:** `tortoise.ep.EPEngine` with default parameters (damping=0.8, n_quad=12, max_iter=50, tol=1e-4)
- **SDK:** `TortoiseClient` with `fresh_sdk()` context manager per test

### 4.2 Graph Construction
Three graph topologies tested:

**Scenario A — Linear Chain:**
```
Source₁ → Source₂ → ... → Pₐ (claim) ──IMPL──→ P_b (downstream)
```
Sources connect to Pₐ. Pₐ implies P_b. Tests propagation: does P_b inherit source credibility?

**Scenario B — Loopy Cluster, Single Entry:**
```
Pₓ ←──IMPL──→ P_y
 ↕              ↕
P_z ←──IMPL──→ P_w
     (sources on Pₓ only)
```
All points connected via mutual IMPL. Sources on Pₓ only. Tests: does credibility propagate through the loop?

**Scenario C — Loopy Cluster, Multi Entry:**
```
Pₓ ←──IMPL──→ P_y
 ↕              ↕
P_z ←──IMPL──→ P_w
(sources)     (sources)
```
Sources on Pₓ and P_w. Tests: do multi-point sources combine?

### 4.3 Source Creation Flow
1. Create Source nodes with `credibilityTier` property
2. Create Point nodes with `extractedFrom` edge to Source
3. Source points IMPL the claim point
4. `calibrate_summary()` applies source credibility inheritance
5. Log-scale aggregation: `effective_pc = base_pc × log₂(N + 1)` for same-tier, same-claim sources

## 5. Test Matrix

40 tests across 14 classes. See `tests/test_ep_sources.py` for full implementation.

| Class | Tests | What it validates |
|-------|-------|-------------------|
| TestLogAggregationMath | 12 | Log-scale formula correctness (no EP needed) |
| TestSituation1_NoSourceToT4 | 2 | H1: T4 > no-source |
| TestSituation2_TierProportional | 4 | H1: tier monotonicity |
| TestSituation3_CumulativeT4 | 4 | H2: cumulative within tier |
| TestSituation4_AntiSybil | 4 | H3: 1000×T4 < 2×T0 |
| TestSituation5_CeilingEffect | 2 | H4: ceiling doesn't regress |
| TestSituation6_NoRegression | 2 | H4: never pulls down |
| TestSituation7_Idempotency | 1 | Add/remove returns to baseline |
| TestSituation8_NAND | 3 | Contradictory signals |
| TestSituation9_Mitigation | 2 | Mitigation weakens but positive |
| TestSituation10_Chain | 3 | H5: linear propagation |
| TestScenarioB_LoopySingle | 4 | H5: loopy single-entry |
| TestScenarioC_LoopyDual | 4 | H5: loopy multi-entry |
| TestEdgeCases | 4 | Boundary conditions |

## 6. Confounds & Pre-Mortem

### Potential Confounds
1. **EP non-convergence:** Some graph topologies may not converge within max_iter. Mitigation: assert `converged=True` in all tests.
2. **Order effects:** Source creation order shouldn't affect EP. Mitigation: tests create sources in deterministic order.
3. **Numerical stability:** Beta parameters near zero may cause division issues. Mitigation: all α,β ≥ 1 after aggregation.
4. **Log-scale edge cases:** N=0 (no sources) → pc=0. N=1 → log₂(2)=1 → pc=base. Validated in TestLogAggregationMath.

### Pre-Mortem: "It's 6 months later and E018 failed."
1. **Log-scale was wrong:** The formula `log₂(N+1)` was too aggressive for T4 (barely moves) or too weak for T0 (saturates too fast). The experiment caught it but we shipped anyway.
2. **EP didn't converge on loopy graphs:** The tolerance was too tight and some cluster configurations oscillated. We loosened ε_loop to 0.05 which masked real issues.
3. **Tests passed but real-world failed:** The test uses isolated FalkorDBLite. Production uses networked FalkorDB with different EP initialization. The tests validate an environment that doesn't match reality.

## 7. Success Criteria
- All 40 tests pass
- H1-H5 confirmed (no falsification triggered)
- No test skipped or marked as "known failure"
