# Test-Design — Epic #903 Integration Surface Map

**Date:** 2026-08-13
**Pipeline:** epic-workflow Test-Design Gate (between Scope and Plan)
**Input:** `03-scope.md` (Customer Value Map — 10 capabilities)
**Purpose:** every scoped capability maps to ≥1 integration surface with an assigned test layer — the contract between scope and implementation that child issues reference for their verification checklists.

---

## Integration Surface Map

| # | Surface | Type | Data Flow | Test Layer | Contract | Key Failure Modes |
|---|---------|------|-----------|-----------|----------|-------------------|
| 1 | `tortoise/sdk.py` `dream()` (mode auto-select + override) | State mutation / API | In | Unit + Integration | `dream(dirty_only=True, full=False, mode=None, max_hops=2) -> {iterations, converged, affected_claims}` (existing) + `{mode, budget_used, coverage}` fields (new). Mode param optional; auto-selection: write-context → local, schedule-context → stale-first, small-graph/first-run → full. | Auto-selection branch misclassifies context (wrong strategy); mode param accepted but ignored; return-shape change breaks existing callers (MCP, hosted worker) |
| 2 | `tortoise/mcp_server.py` dream/staleness tools | API | In | Integration | `dream` tool gains optional mode param; `staleness_report` tool returns per-region `{region, stale_count, oldest_lastDreamedAt, coverage}`. | Tool param mismatch with SDK; staleness report missing regions (query bug); tool registry collision (epic #888 consolidated the surface — additive change) |
| 3 | `tortoise/ep.py` `TortoiseEP.run()` warm-start cache | State mutation / Concurrent | Both | Unit + Integration | Cross-run message cache: persist converged messages (operator/edge → msg) between runs; seed next run; fixed threshold γ; equivalence: warm-started vs from-scratch beliefs within tolerance (E2E-6a). Cache keyed by edge id; invalidated by topology change (new/deleted operator/edge). | **Race:** concurrent dreams corrupting the shared cache (dreamer lock must cover it — verify); stale cache after topology change (must invalidate on edge/operator writes); threshold too loose → beliefs drift (parity test catches); cache growth unbounded (cap + evict) |
| 4 | `tortoise/dream.py` stale-first scheduler + windowing + dedup + retention | State mutation | Both | Unit + Integration | `Dreamer.dream_window(budget=<op cap>, staleness_rank=<query>)`; operator-set dedup across windows/batches (fixes `dream_all` double-BFS: cap-check BFS + `dream()` inner BFS recompute the same subgraph); non-converged claim-roots retained (fix `_dirty_roots -= affected` bug). | **N+1 / double-work:** overlapping windows recompute same operators (dedup must collapse to one); retention bug regression (claim-roots dropped on non-convergence — A2); staleness ranking query returns wrong order (ties, missing `lastDreamedAt` nulls) |
| 5 | Graph DB (FalkorDB) `lastDreamedAt` property + staleness queries | DB | Both | Integration | `lastDreamedAt` ISO timestamp on Point nodes (free-form property, same pattern as `confidence`/`updatedAt`); set atomically with confidence write (dream.py L98 pattern); staleness query: `MATCH (n:Point) WHERE n.is_operator=false RETURN n.id, n.lastDreamedAt ORDER BY n.lastDreamedAt ASC LIMIT <budget>`. | Atomicity (confidence updated but lastDreamedAt not, or vice versa); null/absent `lastDreamedAt` on legacy nodes (default = epoch → always stale, or never-stale?); property not set on draft-filtered claims |
| 6 | `tortoise/hosted_api.py` dream queue + budget + health | Concurrent / API | In | Integration | `_dream_worker` picks mode (default stale-first for scheduled; local for write bursts); #329 hourly budget enforced across modes; health endpoint surfaces last-pass ts, per-region coverage, failure rate, operator counts. | **Race:** two tenants' dreams interleave (existing per-tenant serialization must hold for new mode); budget bypass via mode param (full-mode via override must count against #329); health metrics never emitted (silent death — A8) |
| 7 | Lifecycle writes → dirty set (supersede L1777, invalidate L1475, approve_merge transitive L2616) | State mutation | Both | Integration | Verify: supersede → both ids in dirty set; invalidate → pair in set; approve_merge → resulting operator's inputs in set; next dream (any mode) re-derives surviving node's confidence. | Chain broken (a lifecycle path stops calling `_mark_dirty` — regression test needed); surviving node confidence not re-derived (dream doesn't cover merged region); dirty-set cleared before absorbed |
| 8 | Observability: dream health metrics + silent-death alarm | State mutation | In | Unit + Integration | Metrics record: last pass ts, coverage %, failure rate, operator counts, per-mode counts. Alarm: zero-output-when-backlog-exists (dirty backlog non-empty AND no output for a stale region). | **Silent function skip:** dreamer produces no output while backlog exists (A8 — the alarm exists precisely because this is invisible); metrics not emitted in embedded mode (SQLite); alarm fires on healthy idle (false positive) |
| 9 | Staleness-error evaluation (new test + eval-spec entry) | State mutation | Both | Integration | New test: freeze ground truth → mutate evidence → measure stale-belief error vs ground truth → assert error shrinks as refresh coverage grows (E2E-9). Eval spec: add staleness-error gate next to G7. | Test measures wrong error (uses |Δconf| between runs instead of vs ground truth); error curve non-monotonic (assert threshold not monotonicity); fixture too small (no real drift) |
| 10 | Graph-scale diagnostics task | DB | In | Integration (script) | Reports: node/edge counts, operator fan-out distribution, region/neighborhood sizes, connected-component stats. Decision: stale-first vs full at our scale (E2E-10) — recorded in epic docs. | Query too expensive on production graph (sample or time-box); decision not recorded (gate outcome lost); stats mislead (fan-out on tiny fixture ≠ production) |

---

## Bug Pattern Flags

- **Race conditions** (surface 3): warm-start message cache is cross-run shared state — the existing `Dreamer._lock` serializes runs, but the cache persist/load path must sit INSIDE the lock or concurrent dreams corrupt it. Required verification: concurrent `dream()` calls (threaded test) produce identical results to serial calls and leave a consistent cache.
- **Silent function skips** (surfaces 4, 8): the dreamer's zero-output path is the exact A8 failure — required verification that the execution path reaches a real EP run (not an early-return/no-op) when a stale backlog exists, and that the alarm fires.
- **N+1 / double-work** (surface 4): `dream_all` currently BFSes each chunk twice (once for the operator-cap check, once inside `dream()`). The no-double-work fix must collapse both; required verification: query count per batch ≤ 1 BFS, and overlapping windows share operator sets (dedup).
- **Conditional guards** (surface 1): the mode auto-selection has three branches (write→local, schedule→stale-first, small-graph→full) — boundary-value tests for each branch and for the override param (explicit mode wins).

## Checklist Notes

- **Contract defined?** `dream()` return shape is load-bearing (hosted worker + MCP read it) — the new `mode`/`coverage` fields are additive; existing callers must not break. Document the shape in the plan.
- **Boundary values:** budget = 0 (no-op), budget = 1 (single operator), budget > graph (degenerates to full); staleness ties (two regions equally stale → deterministic order); null `lastDreamedAt` on legacy nodes.
- **Atomic writes:** `confidence` + `lastDreamedAt` must be set in the same Cypher write (existing dream.py L98 pattern extended) — never two writes that can interleave.
- **Idempotency:** re-running a dream on an already-fresh region is a no-op (covered by G7-style isolation: unrelated claims |Δconf| ≤ 0.01).
- **Concurrent access:** per-tenant serialization in hosted mode is existing (#85) — new modes must run inside the same serialized worker, not spawn parallel dreams.
- **Failure modes needing explicit tests:** 429/backoff equivalent (hourly budget exceeded → mode override rejected or queued, not silently ignored); service-down equivalent (FalkorDB unavailable → dreamer fails loudly, alarm surfaces it).

---

*Gate: filed as child issue via `issue-creation` — number recorded in the epic plan doc (Stage 4).*
