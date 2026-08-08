---
title: "Scoping Plan — Issue #341: Mathematical EP validation (source priors monotonic + directionally correct)"
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
# Scoping Plan: Issue #341 — Mathematical EP validation (source priors monotonic + directionally correct)

**Date:** 2026-08-08 | **Tier:** complex | **Level:** project
**Scoping:** double diamond + problem-verify (2 cycles, clean) + solution-verify (2 verifiers)
**Status:** PLAN — approved via scoping gates

## Confirmed Problem (two-tier theorem)

**T1 — Exact prior-level monotonicity theorem** (provable, tolerance-free): `aggregate_prior` (tortoise/source_credibility.py:169-221) is strictly monotone non-decreasing in source addition under **uniform weight + decay=1.0**:
- Per-tier `pc_t = log2(N_t+1) · decay_t · mean(factor_i) · base_pc(tier)` — log2(N+1) strictly increasing, all terms nonnegative → adding any source strictly increases total pc.
- Per-source marginal decreases (log2 concavity): 1→2 adds 0.585·base vs 10→11 adds 0.126·base.
- Targets (pre-verified): 1M T4 pc≈1.99 < 2 T0 pc≈14.26; 10 T4 (0.346) > 1 T4 (0.1); 1000 T4 (0.997) < 1 T2 (2.0).

**T2a — Prior-level real-path ordering tests** (deterministic, NO EP run): real graph (Source T0-T4 → extractedFrom → Point, IMPL edges), `_apply_source_inheritance(recency_decay=1.0, recompute_interval=0)` + `get_point()` `ep_alpha` assertions (`pytest.approx rel=1e-9`). NOT `compute_confidence()` (its `_flush_cache` overwrites graph `ep_alpha` with posteriors). Situations 1-7.

**T2b — EP-level directional audit** (loose margins ≥0.02, `random.seed()` pinned): `compute_confidence()["confidences"][id]["mean"]`. Topologies A (linear chain), B (loopy cluster single-entry), C (loopy cluster multi-entry). Directional ordering only.

**NAND/mitigation (situations 8-10) — DOCUMENTED AUDIT, not encoded expectations:** phi_nand expected-vs-actual artifact + separate bug issue to EP-propagation owner.

## Files Touched
| File | Action |
|---|---|
| `tests/test_ep_sources.py` | REWRITE: embedded real-path suite (T1 + T2a + T2b). Preserve pure-math helpers (TIER_MAP, TIER_PC, log_aggregate_pc, log_aggregate_prior, beta_mean). Remove Docker `fresh_sdk`, `set_source_evidence`/`set_aggregated_evidence` (set_point_baseline bypass), `log_aggregate_prior_mixed` (fictional NAND→beta), and all set_point_baseline-based tests. |
| `tests/test_ep_nary_falsification.py` | Carry-forward fix: `_RecordingEP` snapshot `_node_cache` before `_clear_caches` (test-side, #330 interaction) |
| `docs/plans/2026-08-08-ep-source-validation-research.md` | Already written (research brief) |
| `docs/plans/2026-08-08-ep-source-validation-proof.md` | NEW: the mathematical proof writeup (T1 derivation + audit) |
| GitHub issue (new) | NAND phi_nand audit bug issue → EP owner |

## Implementation Steps (TDD)

### Step 1 — Carry-forward regression fix (test-side)
`tests/test_ep_nary_falsification.py`: override `_clear_caches` in `_RecordingEP` to snapshot `self._node_cache`/`self._msg_cache` into `self._final_node_cache` before calling super. Change `test_run_converges_with_gentle_factor` (l.333) to read `ep._final_node_cache["a"]`.
**Verify:** `python -m pytest tests/test_ep_nary_falsification.py -q` → all pass.

### Step 2 — Rewrite `tests/test_ep_sources.py`: harness + helpers
- Embedded `fresh_sdk()` (tempfile db_path — copy test_source_inheritance_own.py pattern).
- `tier_source(sdk, url, tier, source_date=FRESH)` — create_point(extractedFrom=url) + raw query SET credibilityTier/sourceDate/ingestedAt.
- `link_tiered_source(sdk, pid, url, tier)` — `_get_proj()._link_source(pid, url)` + raw tier SET.
- Preserve: `TIER_MAP`, `TIER_PC`, `log_aggregate_pc`, `log_aggregate_prior`, `beta_mean`.
- `make_operator(sdk, src, tgt, op_type="IMPL", direction="bidirectional")` → `create_operator`.
- `inherited_alpha(sdk, pid)` → `get_point(pid)["ep_alpha"]`.

### Step 3 — T1 theorem tests (pure function)
- `test_log2_increasing`: log2(N+1) strictly increasing for N=1..10^6.
- `test_per_source_marginal_decreases`: pc(10)−pc(9) < pc(1)−pc(0) for each tier (log concavity).
- `test_anti_sybil_1m_t4_lt_2_t0`: aggregate_prior-style formula: 0.1·log2(1+1e6) < 9·log2(3).
- `test_10_t4_gt_1_t4`: 0.1·log2(11) > 0.1·log2(2).
- `test_1000_t4_lt_1_t2`: 0.1·log2(1001) < 2·log2(2).
- `test_monotone_in_n`: for each tier, pc(n+1) > pc(n) for n in 1..20.
- `test_alpha_beta_reparameterization_identity`: mean·(pc+2) ≡ 1+pc, (1−mean)·(pc+2) ≡ 1 (the issue's formula == implementation).

### Step 4 — T2a real-path tests (situations 1-7)
- `test_s1_no_source_to_t4_above_baseline`: ep_alpha(1 T4) > 1.0 (Beta(1,1) baseline).
- `test_s2_tier_proportional`: alpha order T4 < T3 < T2 < T1 < T0 with exact TIER_PRIORS.
- `test_s3_cumulative_weak_sources`: 1→2→3→10 T4 each addition strictly increases ep_alpha; exact 0.1·log2(n+1).
- `test_s4_10_t4_lt_1_t2`: real-path 10 T4 alpha < 1 T2 alpha (ordering, NOT equality — issue's "≈" is a 5.8× gap).
- `test_s5_ceiling_2_gold_plus_t4`: 2 T0 + 1 T4 alpha slightly above 2 T0 alpha (increase < single T0 addition).
- `test_s6_5_gold_plus_t4_not_pull_down`: 5 T0 + 1 T4 alpha >= 5 T0 alpha (regression guard).
- `test_s7_add_remove_idempotent`: add T4 → alpha > baseline; raw edge DELETE + recompute_interval=0 → alpha returns to baseline (exact).
- `test_log_targets_feasible_n`: 10 T4 real path > 1 T4; 100 T4 < 1 T2; 1000 T4 < 1 T2.

### Step 5 — T2b EP directional tests (topologies A/B/C)
- `test_scenario_a_linear_chain`: source T0 → A, A IMPL B. B's confidence mean > 0.5 and rises with more/tier sources on A.
- `test_scenario_b_loopy_single_entry`: A→B→C→A all IMPL, sources on A only. All three means rise above no-source baseline.
- `test_scenario_c_loopy_multi_entry`: sources on A AND B. Cluster means ≥ single-entry case (loose ≥0.02 margins).
- `test_s10_chain_response`: source on A, B responds through IMPL (+ bidirectional EP). B > no-source.
- All: `random.seed(42)` fixture; assert directional ordering only.
- `test_edge_case_convergence_under_50`: real path, topologies A/B/C with source configs {T4:1, T0:1, T4:10, T0:1+T4:1} — converged=True, iterations<=50 (replaces old set_point_baseline version).
- `test_edge_case_confidence_bounds`: confidences in [0,1] for all configs above (real path).
- `test_edge_case_determinism`: with random.seed() pinned, same config → same confidence (replaces old determinism test, which was trivially deterministic on a 1-factor graph).

### Step 6 — NAND/mitigation audit (situations 8-10) — documentation + bug issue
- `test_s8_gold_plus_nand_audit` / `test_s9_mitigation_audit`: run real path, capture observed confidences, assert NOTHING about NAND direction (document behavior). Assert only: gold source alone anchors high (>= 0.8).
- Compute + document phi_nand table: docstring claims 0.637@(0.91,0.91)/0.064@(0.5,0.5); actual 0.519/0.135; phi_nand max at (1,1)/(0,0)=1.0, min at (1,0)/(0,1)=0.018.
- File bug issue: "NAND factor phi_nand is agreement-potential not contradiction" with expected-vs-actual + minimal 2-point repro, routed to EP-propagation owner.
- Audit finding (S9): real mitigation mechanism is `compute_operator_weight` (weights.py:9) — w *= 2.0 when an operator targets another operator (input_ops > 0). The issue's mental model (mitigation reduces pseudo-count, e.g. pc × 0.5) is NOT how the real path works; the old set_point_baseline test modeled a fictional pc×0.5 mitigation. The real-path S9 directional claim "mitigated < unmitigated, both > no-source" depends on the EP weight mechanism and is audit-documented, not asserted.

### Step 7 — Proof writeup `docs/plans/2026-08-08-ep-source-validation-proof.md`
Formal T1 derivation + scoping statement (uniform weight, decay=1.0) + audit findings + spec corrections (log-flatten misstatement, 10 T4 ≈ 1 T2 misstatement, NAND→beta fictional model).

## Test Matrix (issue → test)
| Issue requirement | Test |
|---|---|
| S1: no source → T4 above 50% | test_s1_no_source_to_t4_above_baseline |
| S2: tier-proportional | test_s2_tier_proportional |
| S3: cumulative weak sources | test_s3_cumulative_weak_sources |
| S4: 10 T4 ≈ 1 T2 | test_s4_10_t4_lt_1_t2 (ordering) |
| S5: ceiling effect | test_s5_ceiling_2_gold_plus_t4 |
| S6: 5 gold + T4 not pull down | test_s6_5_gold_plus_t4_not_pull_down |
| S7: add-then-remove idempotent | test_s7_add_remove_idempotent |
| S8: gold + NAND | audit (document) |
| S9: mitigation weakens but above no-source | audit (document) |
| S10: chain response | test_s10_chain_response |
| Scenario A: linear chain | test_scenario_a_linear_chain |
| Scenario B: loopy single-entry | test_scenario_b_loopy_single_entry |
| Scenario C: loopy multi-entry | test_scenario_c_loopy_multi_entry |
| Log: 1M T4 < 2 T0 | test_anti_sybil_1m_t4_lt_2_t0 |
| Log: 10 T4 > 1 T4 | test_10_t4_gt_1_t4 |
| Log: curve flattens | test_per_source_marginal_decreases |
| Formula identity | test_alpha_beta_reparameterization_identity |
| Edge: convergence | test_edge_case_convergence_under_50 |
| Edge: bounds | test_edge_case_confidence_bounds |
| Edge: determinism | test_edge_case_determinism |

## Acceptance Criteria
1. `python -m pytest tests/test_ep_sources.py tests/test_ep_nary_falsification.py tests/test_source_inheritance_own.py -q` → all pass (embedded, no Docker).
2. Full suite: `python -m pytest tests/ -q` → no NEW failures vs baseline (baseline: 1 pre-existing fail `test_run_converges_with_gentle_factor` — now FIXED).
3. T1 theorem documented in proof writeup with derivation.
4. NAND audit bug issue filed with repro + expected-vs-actual.
5. Issue label: implementing → implemented; PR via commit-workflow.

## Spec Corrections (issue body vs implementation — documented in proof writeup)
1. **calibrate_summary does NOT apply inheritance** — it's audit-only (sdk.py:2021). Inheritance happens in `compute_confidence()` → `_apply_source_inheritance` (sdk.py:1834). The plan's T2a drives `_apply_source_inheritance` directly; `calibrate_summary` is not required for the tests (issue body's "run calibrate_summary" is a mental-model error, corrected).
2. **"log curve flattens (10→100 adds less than 1→10)" is numerically false** for decade totals (log2(101)−log2(11)=3.199 > log2(11)−log2(2)=2.459). True only per-source (1→2: 0.585·base vs 10→11: 0.126·base). Tests assert per-source marginal.
3. **"10 T4 ≈ 1 T2" is a 5.8× gap** (0.346 vs 2.0 pc) — assert ordering, not equality.
4. **NAND→beta pseudo-count model is fictional** — production inheritance is positive-only; NAND lives in EP factor domain (audit).
5. **Mitigation ≠ pc×0.5** — real mechanism is `compute_operator_weight` (weights.py:9): w×2 when an operator targets another operator (audit finding).

## Runtime Prerequisites
- Python 3.11+, embedded falkordblite (no Docker). `uv pip install -e .` + pytest.

## Risks & Mitigations
| Risk | Mitigation |
|---|---|
| EP nondeterminism (random.shuffle) | random.seed() fixture; directional-only assertions; loose margins |
| recompute_interval gate traps | fresh SDK per state or recompute_interval=0; never rely on repeated compute_confidence |
| _flush_cache overwrites ep_alpha | T2a reads via get_point() after _apply_source_inheritance only (no EP) |
| Situation 7 revert staleness (in-memory _evidence) | assert via get_point() with zero compute_confidence in that SDK |
| Topology C not monotone in practice | it's a validation finding — document, adjust to directional not-equal |
| Duplication with test_source_inheritance_own.py | deliberate; documented in file docstring |
| Sub-agent timeouts | controller-run verification; fresh verifier dispatch per gate |

## Rejected Alternatives
- **Approach B (extend test_source_inheritance_own.py):** issue body names tests/test_ep_sources.py; would bloat 817-line file; mixes concerns. Rejected.
- **Approach C (proof-doc only):** under-delivers executable proof; no regression protection. Rejected.
- **Fix NAND inside #341:** ep.py owned by parallel agent (feat/326-ep-propagation); behavior change risk; silent fix violates audit boundary. Rejected → bug issue.
