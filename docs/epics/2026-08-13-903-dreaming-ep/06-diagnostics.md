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

**Decision:** STALE-FIRST wins at production scale (≥5k operators) per the
recorded amendment — rule 3 applies; FULL wins below the 200-operator cap
and on star-heavy graphs (rules 1-2). The marker is asserted by
`test_graph_diagnostics` (marker-existence, never the value).

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

---

## Production-scale validation + tuning — issue #1338 (epic 903 follow-up)

**Date:** 2026-10-10 · **Team:** epistemic-team · **Complexity:** standard
**Instrument:** `graph-scripts/dream-scale-bench.py` (added by #1338)
**CI-checked half:** `tests/test_epic903_scale.py`

The recorded decision above (STALE-FIRST at 5k–500k operators) rests on a
product-owner data point, not on a measurement. #1338 validates the machinery
at that scale and tunes the scale-sensitive defaults. Five items; each is
resolved below with either a measurement or a recorded reason it is not.

### 1. `dream_all`'s cap at the top of the range — DEFINED (fixed)

`max_total_operators` (200,000) is below the top of the recorded production
range (500k operators), so a default-budget full pass on such a graph cannot
complete. The behaviour was **defined and de-silenced**:

| Before | After |
|---|---|
| truncate at the first over-cap chunk, `WARNING`, then `break` | same truncation point |
| `converged_all: True` (a partial pass reported as a completed one) | **`converged_all: False`** — an incomplete pass |
| graph-wide operator-less trivial-stamp scan still ran, stamping freshness onto regions the pass never covered | scan **skipped** (it is gated on a complete converged pass) |
| caller's dirty-root sweep ran and cleared a backlog the pass never covered | sweep skipped (it is gated on `converged_all`) |
| result shape: `{batches, total_affected, converged_all, scanned_count, budget_used}` | + `truncated` on the internal Dreamer result (dropped by the SDK adapter, whose I1 key-set is pinned); the failure is visible via `converged_all`, the `WARNING`, and `failure_rate` |

**Decision on the cap itself: NOT raised.** The issue offered "raise the cap
for explicit full requests (the #329 hourly budget becomes the DoS bound)" or
"document full-mode as non-viable at the top end". The second option is
recorded, because for a direct SDK / self-host call `max_total_operators` is
the *only* bound on a whole-graph EP, and weakening it silently trades a
data-integrity bug (fixed above) for a DoS one. **Full mode is non-viable
above 200,000 operators: use `mode="stale-first"`.** An operator who wants an
intentional one-shot full pass raises `max_total_operators` explicitly.

### 2. The default per-pass budget — CONFIGURABLE, value deliberately unchanged

`DEFAULT_WINDOW_BUDGET = 200` stays. **It is now configurable**, resolved on
every call through `dream.resolve_window_budget`:
`explicit budget` → `TORTOISE_DREAM_WINDOW_BUDGET` → the constant. A malformed
or negative value warns and falls back to the default rather than silently
meaning "unbounded" (-1) or "every scheduled pass is a no-op" (0).

**Why the value did not move to ~10,000 as the issue proposed.** The coverage
arithmetic in the issue is correct (below), but `dream_window` passes the
budget as BOTH the operator cap and the claim-window size, and the claim
window is the frontier `_bfs_select_operators` walks hop by hop. Measured on a
5,001-claim synthetic graph (see §4): a 5,000-claim window reliably raised
FalkorDB's `Query timed out` (2/2), while windows ≤ 2,000 completed. A 50×
default is therefore a per-pass **reliability** regression, not a free win —
and it is a regression in exactly the direction item 1 was just fixed in.
The knob is the deliverable; the value is the operator's call, taken against
their own deployment's numbers.

**Coverage-cycle trade-off** (passes = `ceil(operators / budget)`) — the
arithmetic the issue's "2,500 passes" note is about:

| operators | 200 (shipped) | 1,000 | 10,000 |
|---|---|---|---|
| 5,000 (floor) | 25 | 5 | 1 |
| 50,000 | 250 | 50 | 5 |
| 500,000 (top) | 2,500 | 500 | 50 |

Pinned as arithmetic by `tests/test_epic903_scale.py::TestCoverageCycleScalesWithTheBudget`.

### 3. `_AUTO_FULL_OPERATOR_THRESHOLD` (50) — VERIFIED, no change needed

Recorded production graphs are 100×–10,000× this threshold, so **auto-select
routes a production graph to `local` from both branches that can fire**:
dirty roots present (write context, W1) → `local`; no dirty roots and
`n_operators ≥ 50` → `local`. Auto-select never picks `stale-first`; the
production window strategy is reachable only by an explicit
`mode="stale-first"`, which is what the scheduled path must pass (903-C8
wiring; see `tortoise/hosted_api.py` §"Epic 903-C8 (#1246): explicit mode
routing"). Raising this threshold would turn a 5k-operator production graph
into a whole-graph pass on a plain `dream()` call — the O(whole-graph) cost
the epic exists to avoid — so it must stay far below the production floor.
The config is documented at the constant
(`TortoiseSDK._AUTO_FULL_OPERATOR_THRESHOLD`, `tortoise/sdk.py`) and pinned by
`tests/test_epic903_scale.py::TestRoutingAtScale`.

### 4. Perf validation at scale — PARTIAL (instrument shipped; production-scale numbers OPEN)

`graph-scripts/dream-scale-bench.py` builds a synthetic graph at a chosen
scale and measures the four quantities this item asks for: staleness-ranking
query time, window selection + per-pass budget adherence, warm-start γ-skip
savings (the DE2E-6b counter `TortoiseEP._warm_skipped_updates`), and
coverage-cycle time. Run it as:

```bash
# private lane (recommended; the shared container accumulates peer graphs)
uri="$(.venv/bin/python tools/test_lane.py uri)"; eval "$uri"
export TORTOISE_FALKORDB_SOCKET_TIMEOUT_S=60
.venv/bin/python graph-scripts/dream-scale-bench.py --operators 100000 --drop
```

**Measured (5,000 operators / 5,001 claims / 10,000 IMPL edges, private
`fdb-lane-*` container, 2026-10-10):**

| Query | Time |
|---|---|
| `_stale_first_count_stamped` (index, `IS NOT NULL`) | 0.45–0.57 s |
| `_stale_first_claims(limit=None)` (the I2 report's ranking) | 0.87–1.14 s |
| `_stale_first_claims(limit=200)` (the scheduler's window) | 0.16–0.20 s |
| `_bfs_select_operators`, window 50/100/200/1,000/2,000 | completes |
| `_bfs_select_operators`, window 5,000 | `ResponseError: Query timed out` (2/2) |

**The four indicators are NOT closed at the required ≥100k-operator scale.**
The host was saturated by peer lanes throughout (load average 14–27; the
private lane container failed its own 60 s `PING` bound during one run), so the
individual milliseconds above are not reproducible numbers and no confidence
interval is claimed. **What is structural and is reported as the finding:** the
engine's `TIMEOUT` is 1000 ms on every container (`GRAPH.CONFIG GET TIMEOUT` →
`1000`, `TIMEOUT_MAX` → `0`) and no code path in this repo raises it, so a
query whose cost tracks graph size — the ranking, and the
frontier-as-`$ids` hop — already sits on the cap at the *smallest* production
scale. Recorded in full on **#6211** (same root cause, the `compute_confidence`
entry point), which now also carries the scheduler's measurements.

**Consequence for this item:** the ≥100k-operator run remains OPEN and needs a
quiet host. It is a measurement task with the instrument already in place, not
a decision gate — and it cannot move the strategy decision (item 1's
STALE-FIRST record stands; the budget default in item 2 is what the ceiling
governs).

### 5. Deferred W7 refinement (shared operator-hour budget accounting) — RE-EVALUATED, stays v1

Plan W7: only full-mode passes count against the #329 hourly bucket; window
passes are bounded solely by their per-pass budget. #1338 asked whether the
deferred *shared operator-hour accounting* becomes relevant now that full
passes are known to be infeasible at the top of the range.

**Re-evaluation: it stays deferred.** The case for it was "if full passes are
not the metering unit, window passes need one". But a window pass IS bounded —
by construction, at `budget` operators, which is the property the deferral
rests on. What was missing was not a *meter* but a *realistic bound*, and
item 2 supplies the knob plus the documented trade-off. Adding shared
operator-hour accounting would introduce a second metering surface for a
quantity the per-pass budget already bounds, before any deployment has
recorded a single production-scale window pass. Re-open when a real
production graph's measured per-pass cost shows the budget bound is not what
limits throughput — i.e. after the §4 measurement above.

### Files

| File | Change |
|---|---|
| `tortoise/dream.py` | `resolve_window_budget` + `DREAM_WINDOW_BUDGET_ENV`; `dream_all` truncation semantics (`truncated`, `converged_all=False`, scan/sweep gating) |
| `tortoise/sdk.py` | `_dream_full` drops the internal `truncated` (I1 key-set pinned); `dream()` budget docs; `_AUTO_FULL_OPERATOR_THRESHOLD` scale note |
| `tests/test_epic903_scale.py` | NEW — the #1338 contracts |
| `tests/test_epic903_scheduler.py` | default-budget test updated to the configured-default contract |
| `graph-scripts/dream-scale-bench.py` | NEW — the §4 instrument |
| `config/ci-surfaces.yml` | the new test file registered under `core` + `ep` |
