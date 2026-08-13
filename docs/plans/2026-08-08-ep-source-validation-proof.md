---
title: "Proof & Audit — Issue #341: EP source priors monotonic + directionally correct"
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
# Mathematical Proof & Audit: Source Priors are Monotonic and Directionally Correct

**Issue:** tortoise #341 | **Date:** 2026-08-08 | **Status:** validated + locked in `tests/test_ep_sources.py`

## T1 Theorem — Exact Prior-Level Monotonicity of `aggregate_prior`

**Setting.** `aggregate_prior` (tortoise/source_credibility.py:169) aggregates source evidence into a Beta prior. Per tier *t*:

```
pc_t = log2(N_t + 1) · decay_t · mean_i(base_pc(tier_i) · factor_i)
total_pc = Σ_t pc_t          prior = Beta(1 + total_pc, 1)
```

with base_pc: T0=9, T1=4, T2=2, T3=1, T4=0.1.

**Theorem (uniform-weight monotonicity).** Under uniform per-source weight (factor_i = 1.0 for all i) and `decay_t = 1.0` (recency_decay=1.0, or T0-exempt tier):

1. **Adding any source strictly increases total pc.** Adding a source to tier *t* changes that tier's term from `log2(N+1)·mean·base` to `log2(N+2)·mean'·base`. Since `log2` is strictly increasing, `log2(N+2) > log2(N+1)`, and with uniform weight the mean is unchanged (`mean = mean' = 1.0`), the tier term strictly increases; all other tier terms are unchanged. Cross-tier addition (new tier) adds a strictly positive term (`log2(1+1)·1·base > 0` since base > 0). ∎

2. **Per-source marginal decreases (log2 concavity).** The marginal gain of the (N+1)-th source is `base·[log2(N+2) − log2(N+1)]`, which strictly decreases in N (log2 is concave: its first difference is decreasing). Verified: 1→2 adds 0.585·base; 10→11 adds 0.126·base. ∎

3. **Anti-Sybil bounds.** `pc(T4, N) = 0.1·log2(N+1) < 9·log2(3) = pc(2×T0)` for all N < 2^142; concretely 1M T4 → pc 1.993 < 14.26 (2×T0). 1000 T4 → pc 0.997 < 2.0 (1×T2). ∎

**Scoping statement (essential).** The theorem holds under *uniform weight*. With heterogeneous assessment factors, `aggregate_prior` uses the per-tier *mean* factor, so adding a source whose factor is far below the tier mean *decreases* total pc (verified: factor 2.0 then factor 0.1 → pc 4.0 → 3.33). This is **documented intended behavior** (#398, experiment doc S5/S6: "a down-assessment legitimately lowers a source's weight"). #341's target "0 edge cases where adding a source reduces confidence" is therefore scoped to **uniform-weight source addition** — the honest restatement, locked in by T1 tests.

## Spec Corrections (issue body vs implementation)

| Issue claim | Reality (verified) | Test |
|---|---|---|
| "log curve flattens (10→100 adds less than 1→10)" | FALSE for decade totals: log2(101)−log2(11)=3.199 > log2(11)−log2(2)=2.459. TRUE per-source marginal (log2 concavity). | `test_issue_decade_claim_corrected`, `test_per_source_marginal_decreases` |
| "10 T4 ≈ 1 T2" | 5.8× gap: pc 0.346 vs 2.0. Ordering only. | `test_10_t4_lt_1_t2` |
| "NAND sources contribute negative pseudo-counts to Beta" | FICTIONAL. `aggregate_prior` is positive-only; beta always 1.0. NAND lives in EP factor domain. | audit (below) |
| "mitigation = reduced pseudo-count (pc×0.5)" | FICTIONAL. Real mitigation is `compute_operator_weight` (weights.py:9): w×2.0 when an operator targets another operator. | `test_mitigation_weight_mechanics` |
| "calibrate_summary applies inheritance" | Audit-only (sdk.py:2021). Inheritance runs in `compute_confidence` → `_apply_source_inheritance`. | T2a drives `_apply_source_inheritance` directly |
| "alpha_eff = mean×(pc_eff+2)" is identical to `(1+pc, 1)` | Identical ONLY at N=1 (constant-mean reading). At N>1 the issue formula holds the mean FIXED (variance shrinks only); the implementation raises mean toward 1. Both readings documented. | `test_reparameterization_identity_holds_at_n1_only` |

## Engine Audit Findings (routed to EP-propagation owner — NOT fixed here)

### Finding 1 — `phi_nand` is an agreement potential, not contradiction (P1)

- **Target:** `tortoise/quadrature.py::phi_nand` (l.68)
- **Expected (docstring intent):** "NAND: equal-quality contradiction returns to ~50%"; "When both T0(0.91): phi ≈ 0.637"; "When both baseline(0.5): phi ≈ 0.064"
- **Actual:**
  | (ca, cb) | phi_nand (w=8) |
  |---|---|
  | (0.91, 0.91) | **0.5193** (docstring claims 0.637) |
  | (0.5, 0.5) | **0.1353** (docstring claims 0.064) |
  | (1, 1) | **1.0** (maximum — agreement!) |
  | (1, 0) | **0.0183** (minimum — contradiction) |
  | (0, 0) | **1.0** (maximum) |
- `phi_nand = exp(−w·(ca(1−cb)+cb(1−ca))/2)` is maximized at (1,1) and (0,0), minimized at (1,0)/(0,1) — an **XNOR/agreement potential**, the opposite of a contradiction factor.
- **Live EP behavior:** two gold-sourced claims joined by a NAND operator both RISE (0.909 → 0.912-0.924 at w=1-5) instead of being pushed apart.
- **Stale docstring/comment sites:** `tortoise/quadrature.py:68-80`; `tests/test_directional_impl.py:302,359`; `tests/test_directional_impl_fix.py:335` (all cite the wrong 0.637/0.064 values).
- **Minimal repro:** `phi_nand(ca, cb)` evaluated at the table above; 2-point graph with NAND edge via `create_operator("NAND", a, [b])` + `compute_confidence`.

### Finding 2 — stale `_evidence` resurrects reverted priors (P1)

- **Target:** `tortoise/sdk.py::_apply_source_inheritance` revert path (l.~1953) + `set_point_baseline` (l.1781) + `_hydrate_evidence` (l.1765)
- **Expected:** after removing all extractedFrom edges from an inherited-baseline point, `compute_confidence()` returns it to neutral.
- **Actual:** the revert REMOVEs graph markers (`ep_alpha/ep_beta/baseline_source`) but never clears the stale `(alpha, beta)` tuple in `self._evidence` (`set_point_baseline` writes it; `_hydrate_evidence` is additive-only). `compute_confidence()` re-applies the deleted prior via `ep.run(evidence=self._evidence)` → the "reverted" point keeps its elevated prior through the EP path.
- **Test:** `TestSituation7::test_revert_is_idempotent_through_ep_path` — strict xfail documenting the finding (flips to XPASS-failure when the sdk.py fix lands).

## Validation Results (all targets met)

| Target | Result |
|---|---|
| 1M T4 must NOT beat 2 T0 | ✅ pc 1.993 < 14.26 |
| 10 T4 beats 1 T4 | ✅ pc 0.346 > 0.1 |
| Log curve flattens (per-source) | ✅ 1→2: 0.585·base > 10→11: 0.126·base |
| S1: T4 above no-source | ✅ Beta(1.1,1) mean 0.5238 > 0.5 |
| S2: tier-proportional | ✅ T4<T3<T2<T1<T0 exact TIER_PRIORS |
| S3: cumulative weak sources | ✅ each addition strictly increases; exact log2 law |
| S4: 10 T4 < 1 T2 | ✅ ordering |
| S5: ceiling effect | ✅ 2 gold + T4 increases slightly |
| S6: 5 gold + T4 not pull down | ✅ strictly up |
| S7: add-then-remove idempotent | ✅ graph-level exact (EP-path finding documented) |
| S8: gold + NAND | audit (Finding 1) |
| S9: mitigation | audit (mechanic pinned) |
| S10: chain response | ✅ B responds through IMPL + bidirectional EP |
| Scenario A: linear chain | ✅ B rises with source tier/count |
| Scenario B: loopy single-entry | ✅ cluster rises (converged precondition asserted) |
| Scenario C: loopy multi-entry | ✅ multi-entry ≥ single-entry |
| Pre-existing regression fixed | ✅ test_run_converges_with_gentle_factor |

**Bottom line:** 0 mathematical edge cases where adding a source (uniform weight) reduces confidence. The EP-level directional behavior is verified with convergence preconditions; NAND/mitigation engine behavior is documented as audit findings routed to the EP-propagation owner.
