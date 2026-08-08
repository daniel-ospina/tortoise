---
title: "Research Brief — Issue #341: Mathematical EP validation (source priors monotonic + directionally correct)"
type: data
domain: data
status: live
created: 2026-08-08
updated: 2026-08-08
ownedBy: epistemic-team
subjects:
  team: epistemic-team
doc_status: live
aboutSubjects: epistemic-team
aboutObjects: Point, Operator, Mitigation, Source
---
# Research Brief — Issue #341: Mathematical EP validation (source priors monotonic + directionally correct)

**Date:** 2026-08-08
**Issue:** tortoise #341 (project / complex)
**Status:** Verified by fresh-session research verifier (PASS, 5 P2 line-drift corrections applied)

## Dependency Verification (all landed on origin/main)

| Dep | PR | Merge | Evidence |
|---|---|---|---|
| #398 source credibility/reliability + sourceKind T0-T4 | #552 | `5052f63` | `tortoise/source_credibility.py` (306 lines), `sdk.py::_apply_source_inheritance` (l.1834), `create_source` (l.4188), `get_source_reliability` (l.4356), MCP tools |
| #420 n-ary factor + falsification | #536, #550 | `2b2bf2e`, `57fd2cd` | `ep.py::_update_nary_factor` (l.290), `tests/test_ep_nary_falsification.py` |
| #330 EP cache/state | #539 | `ffaddc8` | `ep.py::_clear_caches` (l.107), run-scoped evidence |

## Key Finding: Log-scale aggregation is ALREADY IMPLEMENTED (live)

`aggregate_prior` (source_credibility.py:169-221) implements exactly the issue's spec:

```
per tier t:  pc_t = log2(N_t + 1) × decay_t × (Σ_i base_pc(tier_i)·factor_i) / N_t
total_pc = Σ_t pc_t
return (1.0 + total_pc, 1.0)   # Beta prior
```

- base_pc: T0=9, T1=4, T2=2, T3=1, T4=0.1 (matches issue)
- The issue's reparameterization `alpha_eff = mean×(pc_eff+2)`, `beta_eff = (1−mean)×(pc_eff+2)` is **mathematically identical** to `(1+pc, 1)` — verified symbolically (mean=(1+pc)/(2+pc)).
- **#341 is VALIDATION work, not implementation.** Single edit point if changes emerge: `aggregate_prior`.

Call chain: `compute_confidence` (sdk.py:1682) → `_hydrate_evidence` (1765) → `_apply_source_inheritance` (1834) → `aggregate_prior` → `set_point_baseline` (1781, source="inherited") → `ep.run` (ep.py:447) → writeback (sdk.py:1727).

`calibrate_summary` (sdk.py:2021) is audit-only — does NOT compute priors. It traverses extractedFrom→Source and emits guidance.

## EP Internals (for test design)

- `_update_factor` (ep.py:214): binary factor EP, Gauss-Jacobi quadrature, boost `1+2/max(α+β−1,1)`, damping 0.5, clamp ±1000.
- `_update_nary_factor` (ep.py:290): IMPL source→target pairs; NAND all C(N,2) pairs.
- `_update_claim_posterior` (ep.py:308): posterior = evidence prior + Σ messages (messages can be NEGATIVE).
- `phi_impl` = exp(w·ca·cb); `phi_nand` = exp(−w·(ca(1−cb)+cb(1−ca))/2).
- `run` (ep.py:447): max_hops=2 BFS on reverse IMPL/NAND, random-shuffle factor schedule, tol 1e-3, flush+clear caches.
- `compute_confidence` (ep.py:421): mean = α/(α+β).

## Mathematical Validation Targets (pre-verified numerically)

| Claim | Expected | Verifier result |
|---|---|---|
| 1M T4 must NOT beat 2 T0 | pc(T4,1M)=0.1·log2(1+1M)≈1.99 vs 2 T0=9·log2(3)=14.26 | OK T4 loses |
| 10 T4 beats 1 T4 | 0.1·log2(11)=0.346 > 0.1 | OK |
| Log curve flattens | Per-source marginal decreases: 1→2 adds 0.585·base vs 10→11 adds 0.125·base | ⚠️ ISSUE'S LITERAL CLAIM IS FALSE: decade totals log2(11)−log2(2)=2.459 < log2(101)−log2(11)=3.199 — 10→100 adds MORE total. True intent (diminishing returns per additional source) holds as per-source marginal (log2 concavity). Tests must assert per-source marginal, never decade totals. |
| 10 T4 ≈ 1 T2 | 0.1·log2(11)=0.346 vs 2·log2(2)=2 | Scale mismatch — issue's "approximates" is loose. Test should assert ordering (1 T4 < 10 T4 < 1 T2), not approximate equality. |

## Known Non-Monotonicity Edge (must scope in plan)

`aggregate_prior` uses **mean(factor_i)** per tier. Adding a source whose *assessment factor* is far below the tier mean **decreases** total pc (verified: N=1 mean factor 2.0 → add factor 0.1 ⇒ pc 2.0 → 1.66). Documented as intended (#398, doc S5/S6: monotonicity scoped to uniform-weight addition).

**#341 test discipline:** pin `recency_decay=1.0`, use fresh fixed-epoch source dates, no assessments → isolate the pure log-scale law. Monotonicity proof scoped to: adding sources (uniform weight) never reduces confidence.

## Harness Traps (from research + verifier)

1. `compute_confidence()` does NOT expose `recompute_interval` — incremental same-SDK additions must call `_apply_source_inheritance(recency_decay=1.0, recompute_interval=0)` directly, OR use fresh SDK per state (existing convention). Note: write-path additions (`create_point(extractedFrom=...)`, `create_source`, `set_source_tier`, `assess_source`) dirty-mark and recompute immediately; only bare `_link_source` does NOT invalidate.
2. `dream()` does NOT re-inherit sources — only `compute_confidence()` does.
3. Decay default env = 0.95 — tests MUST pass `recency_decay=1.0`.
4. Distinct URLs per source (MERGE key on url). Fixed epoch `"2024-01-01T00:00:00+00:00"`.
5. Embedded pattern: `TortoiseSDK(db_path=tempfile)` — no Docker. (Existing `test_ep_sources.py` is Docker-only + uses `set_point_baseline` bypass — that's the gap #341 fills.)

## Pre-existing Regression (carry-forward, NOT introduced by us)

`tests/test_ep_nary_falsification.py::test_run_converges_with_gentle_factor` FAILS on origin/main:
- Mechanism: `_RecordingEP` reads `ep._node_cache` after `run()` — but #330 made `run()` call `_clear_caches()` → `AttributeError: '_RecordingEP' object has no attribute '_node_cache'` (line 333).
- This is a test-harness issue interacting with the #330 cache lifecycle, not an EP math bug. Parallel agent owns ep.py; this test fix belongs with #341 branch (or documented). Decision: fix the test harness (read cache before run completes / capture via _flush_cache) — it's test-side, additive, and makes the suite green. Flag in PR description.

## Flaky (environmental, non-deterministic identity)

One embedded test fails intermittently under parallel process contention (research run: `test_legacy_baseline_set_true_no_marker_never_clobbered`; verifier run: `test_100_t4_below_1_t2`). Both pass in isolation. Not a code regression.

## Docs Routing

- `docs/ep-source-credibility-experiment.md` — living spec (updated with #398 annotations)
- `docs/plans/2026-08-07-source-credibility.md` — implementation plan (l.287: "#341 tests, NOT the inheritance implementation")
- A #341 validation writeup belongs in `docs/plans/2026-08-08-...` naming pattern (this file).

## Codebase Explorer Findings (Phase 3 — controller-run)

### Key API surface for the test suite
- `TortoiseSDK(db_path)` — embedded mode (tempfile path). `fresh_sdk()` contextmanager pattern in test_source_inheritance_own.py.
- `create_point(kind, content, extractedFrom=url)` — single URL string only (list MERGEs a broken Source with list url). Multi-source: create_point(extractedFrom=url1) + `sdk._get_proj()._link_source(pid, url2)`.
- `_link_source(point_id, source_ref, source_kind="document")` — edges.py:177; MERGE on url; auto-creates stub Source with ingestedAt=_now_iso().
- Tier setting: raw query `MATCH (s:Source {url:$url}) SET s.credibilityTier=$t, s.sourceDate=$sd, s.ingestedAt=$sd`.
- `create_operator(op_type, source_id, target_ids, label=None, direction="bidirectional")` — sdk.py:794. op_type ∈ {IMPL, NAND, composedOf, ...}; direction ∈ {bidirectional, unidirectional}.
- `_apply_source_inheritance(recency_decay=None, recompute_interval=None)` — sdk.py:1834. Defaults from env (0.95 / 3600). recompute_interval=0 → always.
- `get_point(pid)` returns props dict incl. ep_alpha, ep_beta, baseline_source.
- `compute_confidence(...)` returns {"iterations", "converged", "confidences": {id: {"mean","variance","alpha","beta","effective_n"}}}. Writes back n.confidence to graph.
- `ep.compute_confidence(claim_id)` — ep.py:421: mean=α/(α+β).
- `calibrate_summary()` — sdk.py:2021 — audit-only (suggestions/notes), does NOT compute priors.
- Edge delete for situation 7: raw `MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r` then `_apply_source_inheritance(recompute_interval=0)` — revert path live-verified (sdk.py:1948-1972).
- Revert path: points with baseline_source='inherited' + no eligible sources → ep_alpha/ep_beta removed → neutral.
- EP nondeterminism: ep.py run() uses random.shuffle(factors) (no seed) — pin random.seed() in tests.
- `_RecordingEP` (test_ep_nary_falsification.py:234) stubs graph I/O; run() calls _clear_caches() at end (#330) which deletes _node_cache → the failing test reads post-run cache. Fix: capture in _flush_cache or override _clear_caches to snapshot.

### Existing test_ep_sources.py (1103 lines — to be rewritten)
- Helpers: TIER_MAP (l.33), TIER_PC (l.41), log_aggregate_pc (l.53), log_aggregate_prior (l.66), log_aggregate_prior_mixed (l.77, FICTIONAL NAND→beta), fresh_sdk (l.105, Docker-only db_path=None+namespace), set_source_evidence (l.137, set_point_baseline bypass), set_aggregated_evidence (l.152), make_point (l.128?), make_operator (l.~136), beta_mean (l.~98).
- Test classes: TestLogAggregationMath (incl. test_nand_contribution_to_beta l.308, test_nand_only_below_baseline l.325, test_equal_tier_contradiction_neutral l.335 — FICTIONAL), TestSituation1..10 (set_point_baseline-based), TestScenarioB_LoopySingleSource, TestScenarioC_LoopyDualSource, TestEdgeCases.
- All integration tests use set_point_baseline → validate fictional prior-level model, Docker-only. To be replaced by real-path tests.

### test_source_inheritance_own.py (817 lines — real-path template, keep)
- TestCorroboration (2×T4 > 1×T4 exact), TestAntiSybil (100 T4 < 1 T2; 1000 T4 ≈ 1 T3), TestTierOrdering, plus temporal/legacy/NAND-reliability/assessment tests. Embedded, prior-level via ep_alpha.

### conftest.py
- Forces test graphs to tortoise_test_ prefix; autouse per-test graph recomposition; embedded falkordblite via redislite. TortoiseSDK(db_path=tempfile) works in embedded mode.

### No imports of test_ep_sources.py helpers anywhere (verified zero matches) — rewrite is isolated.
