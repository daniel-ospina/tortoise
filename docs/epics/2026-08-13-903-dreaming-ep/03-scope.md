# Epic Scope — Dreaming: EP across the whole/expanding graph (#903)

**Date:** 2026-08-13
**Pipeline:** epic-workflow Stage 3 (Scope) — `epic-scope`
**Inputs:** `01-align.md` (PROCEED), `02-research.md` (CLEAN)

---

## Axis Research Notes

> **Findings date:** 2026-08-13
> **Provenance:** granular query `[2026-08-13 22:40Z]` (exa: "warm-starting loopy belief propagation in production — reusing previous messages, pitfalls, convergence quality vs from-scratch"); brief sections cited for the remaining boundary questions.

**Axis with `medium+` rating: Architecture (high).**

| Boundary question | Finding | Resolution for this epic |
|---|---|---|
| Is cross-run message reuse (warm-start) theoretically safe? | **Ihler, Fisher, Willsky "Loopy BP: Convergence and Effects of Message Errors" (JMLR 2005):** censoring a message update (reusing the previous message when the two are sufficiently similar) is a bounded-distortion approximation — Theorem 15 bounds belief distance from the true loopy-BP fixed points. This is the theoretical license for EFBP-style warm-start. | Warm-start is **safe to attempt**, with a strict parity gate (see E2E-6): warm-started runs must preserve `(iterations, converged, max\|Δconf\|)` vs from-scratch on the G1–G6 corpus; failure → ship windowed without warm-start (still meets Indicators 1–4). |
| Pragmatic vs exact warm-start? | **Nath & Domingos (AAAI 2010) EFBP:** EFBP* (recomputing exact bounds every iteration) "unlikely to provide large speedups"; **fixed threshold** γ slightly above BP's convergence threshold gives near-identical results "in a fraction of the time". | Use a fixed message-delta threshold; never exact-bound recomputation. |
| Within-run scheduling? | **Elidan, McGraw, Koller "Residual Belief Propagation" (UAI 2006):** schedule message updates by residual (magnitude of change); greedy bound-pushing; converges more often, far fewer messages. | Optional improvement inside window runs (TortoiseEP currently uses randomized factor order — RBP is a compatible, better alternative); not required for the epic's O/I/T. |
| Window/region definition | Brief §Tech Stack: BFS neighborhood from stale anchors (`_bfs_select_operators`) is the existing primitive; community detection has workload-imbalance pathology + heavy cost; workload-aware partitioning quality depends on workload coverage. | Windows = ranked batches of stale-anchor BFS neighborhoods, deduped by operator set. No community detection. |
| Freshness trigger semantics | Brief §Tech Stack: CPU-aware MV refresh (staleness + CPU budget + concurrency) + MAUVE (optimal frequency exists) + memory-drift watermarks. | Trigger = staleness threshold + per-tenant CPU/budget caps; `lastDreamedAt` watermark per Point. |
| Bounded cost mechanics | Brief §Tech Stack + raw notes: EFBP Δ-frontier; IVM dirty flags; operator-set dedup fixes `dream_all`'s double-BFS overlap (reviewer-confirmed: `dream_all` BFSes each chunk twice). | Operator-set dedup across windows/batches + per-pass budget + warm-start (gated). |

**Justified skips (brief covers at sufficient granularity):** Ontology (low — `lastDreamedAt` fits the free-form `confidence`/`updatedAt` node-property pattern; no property whitelist in `validation/`; only an ONTOLOGY.md vocabulary note needed) — brief §Assumptions A3. UX (low — engine epic, no UI; staleness annotation is a data-contract change on existing read surfaces) — brief §UX Pattern Research. Accessibility (low — no UI).

---

## Scope Boundaries

### In Scope
1. **Freshness metadata** — `lastDreamedAt` node property on Points (set by every EP write-back, incl. existing `dream()`), `staleAfter`/`lastDreamedAt` exposed on read surfaces (`get_confidence`/recall), and a staleness report surface ("which regions recomputed when").
2. **Selectable dream modes** — explicit `dream(mode=full|window|dirty)` on SDK/MCP (`full` = current `dream_all` semantics with dedup; `window` = expanding window; `dirty` = current dirty-root behavior); hosted `/v1/dream` gains the mode parameter within the existing #329 budget.
3. **Expanding-window mode** — ranked stale-anchor windows (BFS neighborhoods, staleness-ranked, bounded per-pass operator budget); repeated passes expand coverage toward the whole graph (Indicators 1 + 4).
4. **Operator-set dedup** — across windows/batches (fixes `dream_all`'s per-chunk double-BFS + overlapping recompute).
5. **Cross-run message warm-start (EFBP-style)** — persist converged messages between runs; seed next run from them; fixed threshold γ. **POC-gated**: parity test (E2E-6) decides ship/fallback.
6. **Convergence-retention fix** — non-converged dirty claim-roots must be retained for retry (currently `sdk.dream()` drops them via `_dirty_roots -= affected` even when `converged=False` — A2 gap).
7. **Dream observability** — health metrics (last pass, per-region coverage, failure rate, operator counts), staleness-report endpoint, and an alarm condition on zero-output-when-backlog-exists (A8: silent-death is invisible).
8. **Consolidation interplay** — supersede/invalidate/approve-merge events absorb into the dirty set (supersede L1777, invalidate L1475, approve_merge transitive via create_operator L2616 — all verified); merged/superseded regions re-dreamed so the surviving node's confidence is re-derived.
9. **Staleness-error evaluation** — point-in-time retrieval-error metric + automated test (Indicator 3): freeze ground truth → apply change → measure un-dreamed vs dreamed error; assert error decays as windows expand.
10. **Graph-scale diagnostics** — lightweight metrics task (node/edge counts, operator fan-out, region connectivity) to validate assumption A5 (low/unknown: does windowed recompute actually beat full passes at production scale?).

### Out of Scope
- **Community-detection regions** — rejected in research (workload imbalance, cost); BFS neighborhoods are the primitive. Defer: no future epic needed unless graph topology proves pathological.
- **UI surfaces** — engine epic; no product UI. Staleness is exposed via SDK/MCP data contracts only.
- **Fast-path deepening** (raising `compute_confidence` max_hops 2→4+) — complementary, not required (Align alternative 5). Defer to a follow-up issue if freshness debt proves large.
- **Streaming/event-driven EP** — complementary mechanism (fixed-point schedule requirement); not required for this epic's O/I/T. Defer.
- **Residual-based scheduling (RBP)** — optional within-run improvement; not required. Defer unless window runs converge poorly in practice.
- **`recency_decay` activation** on reads — orthogonal freshness signal; not part of recompute scheduling. Defer.

### Boundary Rationale
The cut is anchored to the epic's four O/I/T indicators: each In-Scope item maps to at least one indicator (1 = expanding coverage, 2 = selectable modes, 3 = fresh graph-wide confidence + queryable freshness, 4 = bounded cost). The two rejected mechanisms (community detection, RBP) are *optimizations that could be layered on later without rework*; the deferred complements (fast-path deepening, streaming EP) are *independent workstreams* whose absence does not block this epic's delivery. Warm-start is included because it is the single largest cost lever and now has a theoretical license — but it is **gated** so the epic cannot be blocked by it.

---

## Customer Value Map

| Scoped Capability | User-Visible Value |
|-------------------|--------------------|
| Freshness metadata (1) | Users see exactly how fresh each belief is and which regions were recomputed when — no more trusting silently-stale confidence |
| Selectable dream modes (2) | Operators pick full refresh or bounded window per their cost/latency budget — one call, no manual batching |
| Expanding-window mode (3) | The whole graph's beliefs stay fresh over time without paying O(whole-graph) per pass |
| Operator-set dedup (4) | Dreaming costs scale with actual change, not with graph size × batching waste |
| Cross-run warm-start (5) | Incremental dreams get dramatically cheaper (orders-of-magnitude precedent) with a verified parity guarantee |
| Convergence-retention fix (6) | Failed dream regions get retried instead of silently abandoned — no unrecoverable stale regions |
| Dream observability (7) | Ops can tell a healthy dreamer from a silently-dead one, and see graph coverage at a glance |
| Consolidation interplay (8) | Supersedes/merges/invalidations immediately propagate through the graph — memory stays consistent with lifecycle events |
| Staleness-error eval (9) | Measurable proof that freshness improves retrieval correctness (the epic's Indicator 3 acceptance) |
| Graph-scale diagnostics (10) | A factual answer to "is windowing even worth it at our scale" — no blind over-engineering |

---

## Complexity Ratings

| Axis | Rating | Rationale |
|------|--------|-----------|
| UX | low | Engine epic, no UI. Staleness annotation on existing read surfaces is a data-contract change; mode selection is a parameter. |
| Architecture | high | Cross-run message state (warm-start), window scheduler + ranking, mode plumbing through SDK/MCP/hosted queue, operator dedup, convergence-retention change, observability — the highest-risk axis, gated by parity POC. |
| Ontology | low | New node property `lastDreamedAt` (free-form property pattern already used for `confidence`/`updatedAt`); ONTOLOGY.md vocabulary note; no entity/edge-kind changes, no schema gate in `validation/`. |
| Accessibility | low | No UI surface. |

---

## High-Level E2E Test Cases

### E2E-1: Full-graph mode refreshes every claim
**Given:** a graph with live Points across multiple disconnected regions, some never dreamed
**When:** `dream(mode=full)` completes
**Then:** every non-operator Point has a fresh `lastDreamedAt` set to this pass
**And:** the returned summary reports `converged_all=True` and `total_affected = #non-operator Points`

### E2E-2: Expanding-window mode covers the graph across passes with bounded per-pass cost
**Given:** a large graph with regions of varying staleness
**When:** `dream(mode=window, budget=<B>)` runs repeatedly until coverage is complete
**Then:** each pass processes ≤ B operators (bounded cost, Indicator 4)
**And:** the stalest regions are dreamed first each pass
**And:** after the pass count where cumulative coverage ≥ graph, every Point's `lastDreamedAt` is fresh (expanding coverage, Indicator 1)
**And:** pass cost does not grow with graph size, only with budget (incremental, not O(whole graph))

### E2E-3: Dirty-mode dream preserves existing behavior and isolation
**Given:** a post-write graph with dirty roots marked (`_mark_dirty`)
**When:** `dream(mode=dirty)` (the existing default) runs
**Then:** affected claims' confidence is refreshed and `lastDreamedAt` updated
**And:** unrelated claims' |Δconf| ≤ 0.01 (G7 grounding gate holds — no regression)

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

### E2E-6: Warm-start parity gate (POC decision)
**Given:** the G1–G6 synthetic corpus with known ground truth
**When:** warm-started dream runs are compared to from-scratch dream runs on identical input
**Then:** `(iterations, converged, max|Δconf|)` are within tolerance (message-reuse bounded error per JMLR 2005)
**And:** warm-started runs are measurably cheaper (fewer factor updates / lower wall time) OR the gate fails and the epic ships windowed without warm-start (documented fallback, Indicators 1–4 still met)

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

---

## Epic Scope Ready for Review

**Scope:** 10 capabilities in (freshness metadata, selectable modes, expanding window, operator dedup, gated warm-start, convergence retention, observability, consolidation interplay, staleness eval, graph diagnostics) — 6 mechanisms explicitly out (community detection, UI, fast-path deepening, streaming EP, RBP, recency_decay).
**Customer value map:** 10 capabilities mapped to user-visible value.
**E2E test cases:** 8 drafted, each mapped to ≥1 O/I/T indicator.
**Complexity:** UX low · Architecture high · Ontology low · Accessibility low.

Review the scope boundaries, customer value map, and E2E test cases.
Reply **"proceed"** to continue to detailed planning, or give feedback.

<!-- human-gate-1: pending -->
<!-- review-gate-status: pending -->
