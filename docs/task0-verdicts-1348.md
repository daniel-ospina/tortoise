---
title: "Task 0 — Cheap Falsification Verdicts (issue #1348)"
type: engineering
domain: capability
doc_status: live
created: 2026-08-17
subjects.team: epistemic-team
---

# Task 0 — Cheap Falsification Verdicts (issue #1348)

Run: 2026-08-17, embedded FalkorDBLite, seed 42, corpus 2000, committed baseline
(no enhancement code). Harness: `tests/eval/retrieval/run.py` (depth curve) +
`tests/eval/retrieval/task0_probe.py` (k sweep + ceiling probe, in-memory lists).

## 0a — Depth curve verdict: FLAT → depth claim FALSIFIED on this corpus

| eval-depth | fused nDCG@10 | fused P@5 | note |
|---|---|---|---|
| 10 | 0.8337 | 0.928 | production-parity leg (SDK limit=5 → str_limit 10) |
| 20 | 0.8336 | 0.928 | **status-quo anchor** (SDK limit=10 → str_limit 20) |
| 25 | 0.835 | 0.928 | eval-only depth |
| 50 | 0.8350 | 0.924 | == committed baseline 0.835023/0.924 (byte-identical reproduce) |
| 100 | 0.839 | 0.932 | |

- delta(20,50) = **0.14 points** (nDCG@10) — below the 1.0-point FLOOR-ACTION
  threshold → **default-50 NOT blessed by quality headroom**.
- delta(20,100) = 0.54 points — below the 1.0-point SIGNAL@100 threshold.
- Classifier: **CEILING-CAPPED** (delta < 1.0 AND top-10 RRF inertia confirmed —
  the grade-2/1 count in the fused top-10 is at/near the achievable max; see 0c).
- **Floor decision:** configurability-only, **env-only opt-in default** (no baked
  default 50). The floor remains useful as a config knob + E2E-8 latency guard,
  but the quality rationale (deeper pool → better fusion) does not hold at
  corpus ≈2000. Docker leg would be authoritative confirmation; embedded verdict
  stands as pre-registered.
- Depth-50 run reproduces `baseline-embedded-2026-08-17.json` exactly
  (0.835023/0.924, corpus fingerprint match) — harness determinism confirmed.

## 0b — k sweep verdict: INTERPRETABLE (population-dependent), k=60 near-optimal

- At depth 50: k∈{20,60,100} reorders the fused top-10 on **28/100 queries**
  (k=20 vs 60) and 10/100 (k=100 vs 60) → the "guaranteed single-list
  degeneracy" premise was FALSE (embedded FTS populates 40/100 → 2-list fusion
  on that subset). Verdict driven by per-query population counts, as
  re-pre-registered.
- nDCG@10 deltas are tiny: k=20 → 0.83472, k=60 → 0.83502, k=100 → 0.83443
  (±0.0006). **k=60 (Cormack) confirmed near-optimal on this corpus.**
- At shallow depths (10, 20) k is degenerate (0 reorders) — single-strategy
  dominance, confirming the sweep is only interpretable where ≥2 strategies
  populate.
- **K-verdict:** documented; k=60 stays the default. Docker leg authoritative.

## 0c — Ceiling probe verdict: HEADROOM EXISTS → Phase 2 PROCEEDS (per-lever)

| leg | mean ceiling nDCG@10 | mean fused nDCG@10 | headroom |
|---|---|---|---|
| limit 50 | 0.898 | 0.835 | **0.063 ≥ 0.02** → enhancement can move the needle |
| limit 10 | 0.849 | 0.834 | 0.016 < 0.02 → thin at production-parity |

- **0c-PASS at limit 50:** the fused pool is REORDERABLE — a reranker (or the
  query-conditioned positive control) has 6.3 points of nDCG@10 headroom.
  Phase 2 (enhancement + GraphRanker arms) proceeds per the pre-registered
  per-lever gate.
- **0c caveat (P1-A corollary):** ceiling ≥ 0.02 means the MEASUREMENT is
  informative, NOT that the static enhanced arm will realize the headroom — the
  static-signal↔query-target correlation is the determining factor. Expected
  REALISM outcome on the balanced 24-topic mix: static arm ≈ 0 → "needs real-EP
  corpus" (the pre-registered escape hatch). The query-conditioned positive
  control (MECHANISM test) must approach 0.898.
- At limit 10 (production-parity) headroom is 0.016 < 0.02 — a production
  reranker at the customer surface has little to gain on this corpus.

## 0d — Latency probe: DEFERRED (environment-blocked)

Docker FalkorDB unavailable in this session; #1349 (embedder swap) still
scoping/OPEN. Per the P2-D fix: 0d runs AFTER the Task-8 depth knob lands, floor
default held provisional until then. The "~2.5×" release-notes figure remains a
linear-depth UPPER-BOUND ASSUMPTION, not measured.

## Per-lever gate summary (pre-registered, non-conjunctive)

| lever | verdict | action |
|---|---|---|
| 0a | FLAT (CEILING-CAPPED) | pool floor = env-only opt-in; NO baked default 50 |
| 0b | INTERPRETABLE, k=60 near-optimal | k stays 60; sweep documented in report |
| 0c | PASS at limit50 (0.063 ≥ 0.02) | **Phase 2 proceeds** (enhancement + GraphRanker arms) |
| 0d | DEFERRED (no Docker) | floor provisional; re-measure after Task 8 knob |

Mixed cell 0a-FAIL + 0c-PASS confirmed as the pre-registered GraphRanker use
case — 0c governs Phase 2, 0a governs only the floor default.

---

## Phase 1 execution verdict (Task 6) — 4-class discriminator applied

| depth | fused nDCG@10 | delta vs depth-20 |
|---|---|---|
| 10 | 0.8337 | — |
| **20 (status quo)** | 0.8336 | 0 |
| 25 | 0.835 | +0.14 |
| 50 | 0.8350 | +0.14 |
| 100 | 0.839 | +0.54 |

**Outcome class: CEILING-CAPPED.** delta(20,100) = 0.54 pts < 1.0 threshold; the
ceiling probe (Task 0c) confirmed the fused top-10 already contains the
grade-2/1 items — depth does not change the top-10 composition on this corpus
(top-10 RRF inertia). **FLOOR-ACTION rule:** delta(20,50) = 0.14 pts < 1.0 at
the ADOPTED depth → **default-50 is NOT blessed by quality headroom**; the
floor ships as an env-only opt-in config knob (still useful for the E2E-8
latency guard + production tuneability), not a baked default.

## Phase 2 execution verdict (Task 7) — GraphRanker arms

All on enhanced corpus (topic-correlated confidence + connectivity), embedded,
n=100 oracle queries, fuse→truncate→rerank (production order sdk.py:8851→9004):

| arm | nDCG@10 | Δ vs fused (90% CI) | interpretation |
|---|---|---|---|
| fused@60 (OFF) | 0.835 | — | status quo |
| **fused_rerank (stub positive control)** | 0.898 | **+6.30 [3.76, 8.98]** | MECHANISM: control ≈ ceiling probe 0.898 → harness NOT broken, pool IS reorderable |
| fused_rerank (static enhanced) | 0.855 | **+1.99 [0.88, 3.11]** | REALISM: static EP signal captures ~30% of headroom (post code-review topic-correlation fix) |
| fused_rerank (enhanced-conf-only, use_degree=False) | 0.838 | +0.26 [−0.59, 1.13] | ablation: confidence-only adds NOTHING (CI includes 0) |
| fused_rerank (production-parity --depth 50 --limit 10) | 0.839 | +0.36 [0.05, 0.69] | customer-surface bracket: small but real |

**Power-relevant statistic (P1-A corollary, verified):** static-confidence ↔
per-query oracle-grade correlation ≈ **0.0003** — static confidence is
orthogonal to query-conditioned relevance on the balanced 24-topic mix, which
is exactly why the confidence-only ablation shows no lift while the
topic-correlated connectivity does. Cohen's d on between-topic confidence was
necessary-but-not-sufficient, as pre-registered.

**GRAPHRANKER VERDICT: HYBRID (evidence, corpus-bounded).**
- The graph signal as a static, query-independent EP boost delivers a real but
  modest lift at eval depth 50 (+1.86 nDCG pts) and a small real lift at the
  production-parity surface (+0.36, CI excludes 0) — **but the lift is carried
  ENTIRELY by the connectivity (edge) signal; confidence alone is null**
  (ablation CI [−0.59, 1.13]).
- This is a mechanism-capability result on topic-correlated synthetic EP — NOT
  a production-value claim. A static EP confidence field uncorrelated with the
  query's target (correlation ≈ 0.0003) cannot be the differentiator; the
  graph-structure (connectivity) signal is the component with measurable value.
- **Recommendation: HYBRID** — wire the connectivity-weighted graph boost as an
  optional `order_by="graph"`/composite path (adopt the STRUCTURE signal, keep
  confidence as annotation-only), validated on real-EP data (#317 GATE INPUT B
  labeled set / --no-seed-corpus real graph) before any production default.
  Reject-as-primary (the full static EP boost) per the confidence-null ablation.
- **Corpus-bound:** the +1.99 is on synthetic topic-correlated EP (weak proxy
  per #1144); real-EP validation is the surfaced follow-up dependency. (Original
  +1.86 measured before the code-review topic-derivation correction — the
  corrected corpus yields +1.99, same conclusion.)
