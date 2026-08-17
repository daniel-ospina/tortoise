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
