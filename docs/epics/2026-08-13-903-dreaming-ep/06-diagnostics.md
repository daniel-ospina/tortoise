---
title: "Graph-Scale Diagnostics + Stale-First-vs-Full Decision — #1239 (epic 903-C1)"
type: decisions
domain: strategy
doc_status: live
subjects.team: epistemic-team
created: 2026-08-14
---

# Graph-Scale Diagnostics + Stale-First-vs-Full Decision — issue #1239

**Epic:** `docs/epics/2026-08-13-903-dreaming-ep/04-plan.md` Substep 7 DE2E-10 /
Substep 8 risk ("Diagnostics says full wins → machinery wasted").
**Scope item:** 10. **Runs FIRST** — it gates the scope of the scheduler
machinery (items 3–5) before implementation.

**Decision type:** HUMAN gate (DE2E-10) — not a CI assertion. The automated
layer asserts measurable invariants only (counts > 0, fan-out sums to the edge
count, component stats emitted) via `tests/test_graph_diagnostics.py`; the
decision itself is recorded here by the epistemic team.

---

## Metrics (emitted by `graph-scripts/graph-diagnostics.py`)

| Metric | What it tells the decision |
|---|---|
| `n_claims` / `n_operators` / `n_edges` (IMPL + NAND split) | Absolute graph scale — whether a full pass is cheap enough to just run |
| fan-out distribution (edges per operator → # operators) | Whether high-arity operators make per-region windows expensive |
| neighborhood sizes (BFS from a sample of claim anchors, `_bfs_select_operators`) | Whether stale-first windows are materially SMALLER than the whole graph (if windows ≈ whole graph, windowing machinery buys nothing) |
| connected components (count + sizes, small BFS over operator edges) | Number/scale of disjoint regions — how a stale-first pass would chunk coverage across passes |

**Invariants asserted by the script** (fail → exit 1): `n_claims > 0`,
`n_operators > 0`, `n_edges > 0`, `sum(arity × count for fan_out) == n_edges`,
`IMPL + NAND == n_edges`, `n_components >= 1`,
`sum(component_sizes) == n_claims + n_operators`, neighborhood sample emitted.

---

## Decision Rule (DE2E-10)

The recorded rule (mirrored mechanically as a *hint* by the script — the
script never decides for the human):

1. **FULL wins** if `n_operators < 200` — the existing
   `_bfs_select_operators` 200-operator selector cap (I1's `budget=None`
   default). Below this cap a single full pass is bounded by the SAME
   per-pass budget the window passes would use, so windows can never be
   smaller than the whole graph; stale-first machinery adds cost, not
   savings.
2. **FULL wins** if the graph is above the cap but neighborhood analysis
   shows windows ≈ whole graph (mean neighborhood operators ≥ 50% of total
   operators) — a connected/star-heavy graph cannot be chunked into cheap
   passes.
3. **STALE-FIRST wins** only if `n_operators >= 200` AND neighborhoods are
   localized (windows materially smaller than the graph) — bounded per-pass
   cost is real, not cosmetic.

**Consequence if FULL wins** (per plan Substep 8 risk row): items 3–5 scope
is SIMPLIFIED via a recorded plan amendment BEFORE implementation — keep the
existing `dream(full=True)` path and drop the stale-first window scheduler
machinery at our scale.

---

## Recorded Outcome

### F5 fixture run — 2026-08-14 (deterministic, representative synthetic)

Run: `.venv/bin/python graph-scripts/graph-diagnostics.py --fixture` — source =
F5 representative synthetic fixture (the F5 builder pins the shape; real
production-snapshot runs are optional/TO-DO below).

**Metrics (F5):**

| Metric | Value |
|---|---|
| claims (non-operator Points) | 40 |
| operators | 12 |
| IMPL edges | 21 |
| NAND edges | 14 |
| total edges | 35 |
| fan-out distribution | `{2: 5, 3: 4, 4: 2, 5: 1}` (sums to 35) |
| neighborhoods (max_hops=1, sample of 10 claim anchors, content-sorted for determinism) | 0.8 operators per anchor mean (sizes `[1, 1, 1, 1, 1, 1, 1, 1, 0, 0]` — two sampled claims are operator-less isolated claims, neighborhood 0) |
| connected components | 17 (12 operator regions + 5 isolated claims; sizes sum to 52 = 40 + 12) |
| invariants | 8/8 PASS |

**Decision at the F5 (representative synthetic) scale: FULL refresh wins.**
Recorded 2026-08-14 (epistemic team, DE2E-10).

- `n_operators = 12 < 200` → rule 1: a full pass is bounded by the same cap
  window passes would use; windows can never beat whole-graph.
- Neighborhood mean 0.8 operators per claim anchor (sizes `[1 ×8, 0, 0]` — two sampled
  claims are operator-less isolated) confirms the graph is a disjoint star
  collection — a stale-first window pass would select ≈ the same operators as
  a full pass, minus the bookkeeping.
- **Consequence at F5 scale only:** the scheduler machinery would be
  simplified to `dream(full=True)`. NOTE: this consequence was NOT applied —
  the machinery (items 3–5) was built as planned because (a) Indicator 2
  requires the selectable strategy regardless, and (b) the production-scale
  data point below flips the decision.

**Decision at PRODUCTION scale: STALE-FIRST refresh wins — decisively.
Recorded 2026-08-14 (epistemic team, product owner statement + DE2E-10 rule 3).**

- **Product-owner data point (2026-08-14):** production graphs will be in
  the range **≈ 5,000–500,000 operators** — 25×–2,500× above the 200-operator
  threshold. Rule 3 applies: stale-first windowed passes win; a full-graph
  pass at the top of the range (500k operators) is O(whole graph) per pass —
  infeasible as a routine operation, and `dream_all`'s `max_total_operators`
  cap (200,000) cannot even complete a full pass at 500k.
- **Consequence:** the stale-first scheduler (#1241, C3) is the production
  strategy — the machinery was built as planned and this record confirms it
  is the RIGHT choice at the real scale. The F5-scale FULL decision is
  superseded for production by this data point; a real production-snapshot
  measurement remains a validation (exact numbers), not a decision gate.

### Production-snapshot run — TO-DO (optional, external dependency)

Real-snapshot runs are skipped in CI (external dependency — a live
FalkorDB with a production graph). When one is available:

```bash
# against the configured TORTOISE_DB_URI / docker://... instance:
.venv/bin/python graph-graph-scripts/graph-diagnostics.py
```

**Observed data point (2026-08-14, local dev embedded graph — NOT the
canonical production snapshot; single-writer EVAL-only store, not
representative of hosted tenants):** ≈ 19.6k claims / 653 operators / 1.3k
IMPL edges — above the 200-operator threshold, consistent with the
product-owner production range (5k–500k operators).

**SUPERSEDED by the production-scale data point above:** the product-owner
statement (5k–500k operators) is the canonical decision input; the recorded
decision is now STALE-FIRST at production scale. A real production snapshot
is still valuable as a validation/measurement (exact fan-out + neighborhood
stats to size the window budget), but the strategy decision is made.

- **Outcome:** ⬜ (empty until run)
- **Recorded by / date:** ⬜
- Re-evaluation: if the snapshot metrics move the decision, amend this record
  and file the corresponding plan amendment.

---

## Status

- [x] Diagnostics script (`graph-scripts/graph-diagnostics.py`) — emits all metrics + invariants
- [x] Automated invariants tested on the F5 fixture (`tests/test_graph_diagnostics.py`)
- [x] Decision recorded for the representative (F5) scale: **FULL** (synthetic only)
- [x] Decision recorded for PRODUCTION scale (product-owner data point
      5k–500k operators): **STALE-FIRST** — supersedes the F5 FULL decision
- [ ] Production-snapshot run (optional) — VALIDATION measurement (fan-out,
      neighborhood sizes to size the window budget), not a decision gate
