---
title: "Epic Scope — #903: Dreaming (EP across the whole/expanding graph)"
type: decisions
domain: strategy
doc_status: live
subjects.team: epistemic-team
created: 2026-08-13
---

# Epic Scope — Dreaming: EP across the whole/expanding graph (#903)

**Date:** 2026-08-13
**Pipeline:** epic-workflow Stage 3 (Scope) — `epic-scope`
**Inputs:** `01-align.md` (PROCEED), `02-research.md` (CLEAN)

---

## Axis Research Notes

> **Findings date:** 2026-08-13 — plain-language summary; source identifiers in the raw notes of `02-research.md`.

**Only Architecture rates `medium+`, so only it needs per-axis research. The one genuinely open architecture question was: "is it safe to carry over the last pass's intermediate math instead of recomputing from scratch?"**

| Boundary question | Finding (plain) | Resolution for this epic |
|---|---|---|
| Is carrying over the math from the last pass safe? | Yes — the theory (Ihler et al., JMLR 2005, *Loopy BP: Convergence and Effects of Message Errors*) proves that reusing an old message when the new one is nearly identical changes the final beliefs only within a bounded, controlled amount. This is the same reasoning that lets the math be *approximate but good enough* in the first place. | Safe to attempt, with a hard correctness check: a carried-over run must produce the same beliefs as a from-scratch run on the standard test corpus. If it doesn't, we ship the simpler version (still meets the epic's targets). |
| Should we compute exact error bounds every pass? | No — the research says exact bounds are not worth it; a fixed "close enough" threshold gives nearly identical results at a fraction of the cost. | Use a fixed threshold. Never compute exact bounds per pass. |
| Within-pass ordering? | Scheduling messages by "how much they changed" (residual) converges faster, but it's an optimization, not a requirement. | Optional later; not needed for this epic's targets. |
| How to pick a "region"? | The graph already has a region primitive (breadth-first neighborhood from a point). Fancy graph-clustering algorithms are heavier AND worse for our use (known workload-imbalance problem). | Regions = neighborhoods around stale points. No clustering. |
| When to trigger a refresh? | The trigger should be "this area is stale enough" AND "the machine has spare budget" — not a blind timer. | Trigger = staleness threshold + per-tenant cost cap. |
| How to keep cost bounded? | The main waste today: overlapping refresh batches recompute the same areas repeatedly. Dedup fixes it. | Deduplicate areas across passes + per-pass budget + (gated) carry-over. |

**Skipped axes (justified):** Ontology low — adding a `lastDreamedAt` timestamp fits the existing free-form property pattern (`confidence`/`updatedAt` already exist); no schema gate. UX low — engine epic, no UI; freshness is exposed via the existing API surface. Accessibility low — no UI.

---

## Scope Boundaries

### In Scope
1. **Freshness metadata** — every Point records when its confidence was last recomputed (`lastDreamedAt`), and that timestamp is exposed on read surfaces so anyone can see how fresh a belief is — and which areas were recomputed when.
2. **One dream call, sane defaults** — `dream()` keeps working as-is; internally it picks the right strategy (writes → fix the neighborhood; background schedule → stale-first; small graph/first run → full). A `mode` override exists for operators with a compute budget but is hidden by default — users never need to think about it.
3. **Stale-first refresh** — each background pass refreshes the stalest chunk of the graph first; every pass costs the same bounded amount (a fixed "budget" of work, not "however big the graph is"); several passes eventually refresh the whole graph. No single pass ever costs O(whole graph).
4. **No double work** — when two refresh passes touch the same area, that area gets recomputed once, not twice (today, overlapping batches recompute the same subgraphs repeatedly).
5. **Carry over the math** — start each refresh from the previous pass's results instead of from scratch ("only redo the parts that changed"). This is the biggest cost saver, but it's **gated by a correctness check** (E2E-6: carried-over results must match from-scratch results on the test corpus); if it fails, we ship the simpler version — the epic's targets are still met without it.
6. **Retry failed areas** — if a refresh can't converge on an area, that area stays queued and gets retried later (today it's silently dropped — a bug found during research, A2).
7. **Dream observability** — health metrics (last pass, per-area coverage, failure rate), a staleness report, and an alarm when dreaming produces nothing while areas are still stale (a silently-dead background job looks identical to "nothing to do" — a known failure mode, A8).
8. **Lifecycle events feed dreaming** — supersede/invalidate/merge (the operations that change what the graph believes) automatically mark their area for refresh, so the surviving belief gets recomputed after the change. The wiring already exists in the code (verified: supersede L1777, invalidate L1475, approve_merge transitively L2616); this epic verifies + tests the chain.
9. **Freshness is measured, not assumed** — an automated test that proves the point: freeze the "true" state, change some evidence, measure how wrong the stale beliefs are vs the refreshed ones, and assert the error shrinks as more of the graph gets refreshed.
10. **Graph-scale diagnostics** — a small metrics task (graph size, how connected it is, how big a typical neighborhood is) to answer the open question from research: does stale-first refresh actually beat a full refresh at *our* scale, or is the graph small enough that full refresh is simpler? We don't build the machine until we know the answer.

### Out of Scope
- **Community-detection regions** — rejected in research (workload imbalance, cost); BFS neighborhoods are the primitive. Defer: no future epic needed unless graph topology proves pathological.
- **UI surfaces** — engine epic; no product UI. Staleness is exposed via SDK/MCP data contracts only.
- **Fast-path deepening** (raising `compute_confidence` max_hops 2→4+) — complementary, not required (Align alternative 5). Defer to a follow-up issue if freshness debt proves large.
- **Streaming/event-driven EP** — complementary mechanism (fixed-point schedule requirement); not required for this epic's O/I/T. Defer.
- **Fancier in-pass ordering** (schedule updates by magnitude of change) — optional within-run improvement; not required. Defer unless stale-first runs converge poorly in practice.
- **`recency_decay` activation** on reads — orthogonal freshness signal; not part of recompute scheduling. Defer.

### Boundary Rationale
The cut is anchored to the epic's four O/I/T indicators: each In-Scope item maps to at least one indicator (1 = expanding coverage, 2 = selectable strategy, 3 = fresh graph-wide confidence + queryable freshness, 4 = bounded cost). The two rejected mechanisms (graph clustering, fancier in-pass ordering) are *optimizations that could be layered on later without rework*; the deferred complements (deeper per-query refresh, streaming updates) are *independent workstreams* whose absence does not block this epic's delivery. Carry-over is included because it is the single largest cost saver and has a theoretical safety license — but it is **gated** so the epic cannot be blocked by it.

---

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Freshness metadata (1) | Users see exactly how fresh each belief is and which regions were recomputed when — no more trusting silently-stale confidence |
| One dream call, sane defaults (2) | Users never think about modes — writes refresh the neighborhood, background refreshes stale areas first; an override exists for operators with a compute budget |
| Stale-first refresh (3) | The whole graph's beliefs stay fresh over time without any single pass costing O(whole graph) |
| No double work (4) | Refresh cost scales with actual change, not with graph size × wasteful overlap |
| Carry over the math (5) | Each refresh redoes only the parts that changed — verified to match from-scratch results |
| Retry failed areas (6) | A refresh that can't finish on an area gets retried later instead of silently abandoned — no unrecoverable stale areas |
| Dream observability (7) | Ops can tell a healthy dreamer from a silently-dead one, and see graph coverage at a glance |
| Consolidation interplay (8) | Supersedes/merges/invalidations immediately propagate through the graph — memory stays consistent with lifecycle events |
| Staleness-error eval (9) | Measurable proof that freshness improves retrieval correctness (the epic's Indicator 3 acceptance) |
| Graph-scale diagnostics (10) | A factual answer to "is windowing even worth it at our scale" — no blind over-engineering |

---

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | low | Engine epic, no UI. Staleness annotation on existing read surfaces is a data-contract change; mode selection is a parameter. |
| Architecture | high | The risky parts: carrying math across refreshes (gated by correctness check), the stale-first scheduler, plumbing one dream call through SDK/MCP/hosted, no-double-work, retry-failed-areas change, observability. |
| Ontology | low | New node property `lastDreamedAt` (free-form property pattern already used for `confidence`/`updatedAt`); ONTOLOGY.md vocabulary note; no entity/edge-kind changes, no schema gate in `validation/`. |
| Accessibility | low | No UI surface. |

---

## High-Level E2E Test Cases

### E2E-1: Full-graph mode refreshes every reachable claim
**Given:** a graph with live Points across multiple disconnected regions, some never dreamed (fixture regions each contain ≥1 operator, so every claim is EP-reachable)
**When:** `dream(full)` completes
**Then:** every non-operator Point reachable via operators has a fresh `lastDreamedAt` set to this pass
**And:** the returned summary reports `converged_all=True` and `total_affected = #reachable non-operator Points`
**And (semantics decision):** operator-less/isolated claims are marked trivially fresh on the scan (nothing can change their confidence — no message path exists), so full mode still reports full coverage

### E2E-2: Stale-first refresh covers the graph across passes at a bounded cost
**Given:** a large graph with areas of varying staleness
**When:** `dream()` (stale-first strategy) runs repeatedly until coverage is complete
**Then:** each pass does ≤ a fixed budget of work — cost does not grow with graph size (incremental, not O(whole graph))
**And:** the stalest areas are refreshed first each pass
**And:** after enough passes, every Point's confidence has been refreshed (whole-graph coverage without a whole-graph pass)
**And:** passes that touch the same area do that work once, not twice

### E2E-3: Write-triggered refresh preserves existing behavior and isolation
**Given:** a post-write graph with dirty areas marked
**When:** `dream()` (default) runs
**Then:** affected claims' confidence is refreshed and `lastDreamedAt` updated
**And:** unrelated claims' confidence changes by ≤ 0.01 (existing grounding gate holds — no regression)

### E2E-4: Freshness tracking is queryable
**Given:** regions dreamed at different times (some just now, some long ago)
**When:** a staleness report / read surface is queried
**Then:** each Point reports `lastDreamedAt` (and `staleAfter` where applicable)
**And:** the report lists regions ranked by staleness, matching which regions the last pass actually touched

### E2E-5: Consolidation events absorb into dreaming
**Given:** a supersede/invalidate/merge on a Point
**When:** the next dream (any mode) runs
**Then:** the surviving node's confidence is re-derived from the post-event graph (its region was scheduled)
**And:** lifecycle-write → dirty-set → dream chain is observable in the dream log

### E2E-6a: Carry-over correctness (hard gate)
**Given:** the standard test corpus with known ground truth
**When:** a carried-over refresh is compared to a from-scratch refresh on identical input
**Then:** the two produce the same beliefs within tolerance (the theory's bounded-error guarantee, verified empirically)
**And:** this equivalence MUST hold — if it fails, the carry-over work is not shipped (documented fallback: ship the simpler version; the epic's targets are still met without carry-over)

### E2E-6b: Carry-over cost (measured, not asserted)
**Given:** the same corpus
**When:** the carried-over refresh runs
**Then:** the run records cost deltas (fewer updates / lower wall time vs from-scratch) as a measurement in the dream health metrics — recorded, not gated; if the savings are nil, that is data for the graph-scale diagnostics decision, not a test failure

### E2E-7: Non-converged regions are retained and retried
**Given:** a region whose EP run does not converge within max_iter (oscillating subgraph)
**When:** the dream completes with `converged=False` for that region
**Then:** the region's roots remain in the dirty set (retention fix, A2)
**And:** a later dream retries them (and converges after the conflicting evidence is resolved)

### E2E-8: Dream health is observable and silent-death is detectable
**Given:** a running dreamer with a non-empty dirty backlog
**When:** dream health metrics are inspected
**Then:** last-pass timestamp, per-region coverage, failure rate, and operator counts are surfaced
**And:** the zero-output-when-backlog-exists alarm condition triggers when dreaming produces no output for a stale region (silent-death, A8)

### E2E-9: Freshness reduces staleness error (Indicator 3 acceptance)
**Given:** a graph whose "true" belief state is known (ground truth frozen)
**When:** some evidence changes (so some beliefs become wrong) and a stale-belief error is measured: mean |Δ| of confidence vs ground truth
**Then:** after successive stale-first refresh passes, the measured error shrinks (monotonically or below a threshold) as refresh coverage grows
**And:** the error-reduction curve is recorded in the dream health metrics — this is the epic's Indicator 3 acceptance, and the basis for the staleness-error eval added to the eval spec

### E2E-10: Graph-scale diagnostics gate
**Given:** a production-scale graph snapshot (or representative fixture)
**When:** the diagnostics task runs
**Then:** it reports graph size, connectivity (how many areas, how big a typical neighborhood is), and a recorded decision: stale-first refresh vs full refresh at this scale
**And:** the decision is written to the epic docs — if full refresh is cheaper at our scale, items 3–5 are simplified accordingly before implementation

---

## Epic Scope Ready for Review

**Scope:** 10 capabilities in (freshness metadata, one dream call with sane defaults, stale-first refresh, no double work, gated carry-over, retry failed areas, observability, lifecycle events feed dreaming, freshness measured, graph-scale diagnostics) — 6 mechanisms explicitly out (graph clustering, UI, deeper per-query refresh, streaming updates, fancier in-pass ordering, read-time decay).
**Customer value map:** 10 capabilities mapped to user-visible value.
**E2E test cases:** 10 drafted (8 + staleness-error acceptance E2E-9 + diagnostics gate E2E-10), each mapped to ≥1 O/I/T indicator.
**Complexity:** UX low · Architecture high · Ontology low · Accessibility low.

Review the scope boundaries, customer value map, and E2E test cases.
Reply **"proceed"** to continue to detailed planning, or give feedback.

<!-- human-gate-1: APPROVED 2026-08-13 -->
<!-- review-gate-status: CLEAN (5 issues fixed: E2E-9/10 added, dup row removed, E2E-6 split, E2E-1 semantics) -->
