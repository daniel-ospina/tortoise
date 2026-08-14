# Epic Plan — Dreaming: EP across the whole/expanding graph (#903)

**Date:** 2026-08-13 (rev 2 — consolidated review fixes from substep gates A+B)
**Pipeline:** epic-workflow Stage 4 (Plan) — `epic-plan` (8 substeps)
**Inputs:** `01-align.md` (PROCEED) · `02-research.md` (CLEAN) · `03-scope.md` (human APPROVED, review CLEAN) · `test-design-surface-map.md` (filed as issue #1232)
**Test-Design Gate:** issue **#1232** — integration-surface map (10 surfaces). Child issues derive verification checklists from it.

---

## Substep 1 — User Journeys

**Personas:**
- **P1 — SDK operator:** an agent or engineer calling the Tortoise Python SDK (direct or via MCP).
- **P2 — Hosted tenant:** a team using `/v1` endpoints on the hosted platform.
- **P3 — Graph maintainer:** the operator responsible for graph health (ops/engineering).

**Light research hook (UX):** no hook — engine epic, no novel interaction type; brief §UX Pattern Research covers the surface. Justified skip.

| Journey | Persona | Entry | Flow (user-visible steps) | Exit | In-scope items |
|---|---|---|---|---|---|
| J1 — Write-then-fresh | P1, P2 | Agent writes points/operators | 1. Agent creates/updates claims → 2. system marks area dirty → 3. background refresh (local mode) updates confidence + freshness timestamps → 4. agent's next read sees fresh confidence | Next read returns current beliefs; no action needed | 1, 2 (auto: local) |
| J2 — Scheduled whole-graph freshness | P1, P3 | Operator enables scheduled dreaming | 1. Operator configures dream schedule → 2. system refreshes the stalest chunk each pass (null-stamp claims rank STALEST — first deploy refreshes) → 3. coverage grows across passes → 4. operator sees graph-wide freshness report | Whole graph fresh within the expected number of passes; per-pass cost bounded | 3, 1, 4, 7 |
| J3 — Full refresh on demand | P1, P2 | Small graph / migration / first run | 1. Operator calls `dream(full=True)` (or mode="full") → 2. all reachable claims recomputed + freshness stamped (operator-less claims trivially stamped by the scan path) → 3. summary returned (converged_all, total_affected) | Full coverage in one pass (hosted: counts against the #329 hourly full-pass budget) | 1, 2 (explicit override), 5 |
| J4 — Freshness inspection | P1, P2, P3 | User distrusts a confidence value | 1. User reads a belief / calls `staleness_report()` → 2. sees `lastDreamedAt` (+ staleness annotation) → 3. sees which regions were recomputed when | User can judge trustworthiness of any belief | 1 |
| J5 — Lifecycle event propagates | P1, P2 | Agent supersedes/invalidates/merges | 1. Agent performs lifecycle write → 2. affected area marked dirty AND warm-start cache invalidated → 3. next refresh re-derives the surviving belief → 4. graph consistent with the event | Surviving node's confidence reflects the post-event graph | 8 |
| J6 — Dream health check | P3 | Ops suspects dreaming is not running | 1. Ops calls health endpoint / `dream_health_check()` → 2. sees last-pass ts, coverage %, failure rate → 3. alarm verdict (zero output while backlog exists) surfaced | Silent death detectable (hosted: endpoint; embedded: call-triggered check) | 7 |
| J7 — Failed area retries | P3 | A region won't converge | 1. Refresh hits a region that fails to converge → 2. region stays queued (retention) AND is NOT stamped fresh → 3. retried on later passes (attempt-capped, exponential backoff) → 4. converges after evidence resolves, or is surfaced as `stale_unresolved` | No stale areas beyond the attempt cap; capped regions surfaced in health | 6 |
| J8 — Diagnostics & eval decision | P3 | Maintainer before building the machine | 1. Maintainer runs graph-scale diagnostics → 2. sees size/connectivity/fan-out stats → 3. runs staleness-error eval → 4. decision recorded: stale-first vs full at our scale | Recorded decision gates items 3–5 scope before implementation | 9, 10 |

**Edge cases handled:** empty graph (no-op), all-null first run (window = full via null-as-stalest), legacy null `lastDreamedAt` (stale-first scheduler; report displays "never dreamed"), budget = 0 (no-op), non-convergence (retention + attempt cap + not-stamped-fresh), silent dreamer (alarm), concurrent writes during refresh (lock + per-tenant serialization), process crash mid-pass (dirty set rebuilt from staleness ranking — null/old stamps re-enter the window).

**Review gate (J/W/P):** CLEAN after batch A fixes (J8 added; J↔W mapping corrected; annotations fixed; post-cap semantics defined).

---

## Substep 2 — Workflows

System-level operational flows (handoffs + failure modes):

**W1 — Write → local refresh (existing, extended).** Write path calls `_mark_dirty` (1-hop reverse BFS, existing) — **plus: `promote_point` and any status→live transition now call `_mark_dirty` too** (verified gap: sdk.py L1835 does not today) → debounced dream (embedded: in-band ≤500ms then lazy-read fallback; hosted: per-tenant queue 100ms debounce) → EP on dirty anchors (max_hops=2) → atomic write-back `confidence` + `lastDreamedAt` → dirty roots cleared on convergence; **zero-affected converged runs (draft-excluded) do NOT clear dirty roots** (draft → promote → still-dirty path); non-converged claim-roots KEPT. Failure modes: dream exceeds latency budget (fallback to lazy-read + scheduled), EP non-convergence (retention + retry), DB unavailable (fail loudly + alarm).

**W2 — Stale-first window pass.** Trigger (scheduled or manual): staleness query ranks non-operator Points by `lastDreamedAt ASC` — **null `lastDreamedAt` ranks STALEST (first)** so first-deploy/legacy/crash-mid-pass graphs drain across passes; window = **retained-dirty-roots ∪ staleness-ranked-top-N** (union guarantees non-converged regions reselect) → BFS-neighborhood anchors → **operator-set dedup** (union across windows; fixes `dream_all` double-BFS) → `TortoiseEP.run` (warm-started, see W3) → atomic write-back → coverage + freshness metrics recorded. **All-null graph ⇒ window = whole graph (degenerates to full pass).** Failure modes: budget mis-accounting (dedup must count distinct ops), ties in staleness (deterministic order by id), window == whole graph (graceful).

**W3 — Warm-start cycle (reuses graph-persisted messages).** EP message state is **already persisted on operator edges** (`msg_alpha`/`msg_beta`, `back_msg_alpha`/`back_msg_beta` — verified: `ep.py` `_load_cache` reads them, `_flush_cache` writes them every run, `run()` flushes even on non-convergence). Warm-start therefore: (a) runs `run(..., warm_start=True)` which **loads the graph-persisted messages as seed** (existing `_load_cache` behavior) and skips message updates whose delta ≤ fixed threshold γ (new skip logic); (b) `_flush_cache` persists converged messages as today — **no new in-memory cache store** (avoids two-sources-of-truth). Cache-key discipline: messages are `(op_id, claim_id, rel_type)`-keyed with separate forward/back slots (`back_msg_*` for bidirectional operator-less edges) — the γ-skip must respect both slots. **Invalidation:** on topology change (create/delete operator or edge), on **edge transfer** (supersede L1777 / invalidate L1475 / approve_merge L2616 transfer edges with their message properties — a transfer is NOT a new edge), and on **evidence/baseline writes** (`set_point_baseline`, run-level evidence — priors change factor behavior without topology change). Invalidation = drop cached messages for affected edges (cheap; or full cache drop on baseline change). Failure modes: stale reuse after transfer/baseline (DE2E-6a negative cases), threshold too loose (parity gate), race (cache access only from `run(warm_start=True)` paths, which are Dreamer-locked — fast path `compute_confidence` calls `run(warm_start=False)` and never touches the γ-skip).

**W4 — Retry loop.** Non-converged region roots retained in dirty set → NOT stamped fresh (a failed run's affected claims keep their old `lastDreamedAt`) → re-selected via the W2 union → retried with attempt cap + exponential backoff → converges, or after cap: dropped from dirty set and surfaced as `stale_unresolved` in health metrics. Failure modes: persistent oscillation (cap + surface, not infinite retries), stamp-fresh-on-failure regression (DE2E-7 asserts failed run does NOT rank fresh).

**W5 — Observability loop.** Every pass writes metrics: last-pass ts, coverage %, failure rate, operator counts, per-mode counts → hosted: `/v1/dream/health` reads them; embedded: metrics to local log AND `dream_health_check()` SDK/MCP call-triggered alarm evaluation (no daemon — #176 design) → alarm verdict: dirty backlog non-empty AND zero output for a stale region. Failure modes: metrics not emitted in embedded mode, false-positive alarm on healthy idle (require backlog non-empty), alarm un-evaluated in embedded (call-triggered check + MCP tool).

**W6 — Lifecycle absorption.** supersede (L1777) / invalidate (L1475) / approve_merge (transitive via create_operator L2616) → dirty set AND warm-start invalidation (W3) → next pass re-derives surviving node; surviving node's `lastDreamedAt` keeps its pre-transfer stamp until re-derived (correct because W6 marks dirty). Failure modes: a lifecycle path stops marking dirty (regression — surface 7 integration test), invalidation missed on transfer (DE2E-6a negative).

**W7 — Budget enforcement.** Hosted: **only full-mode passes (including via override) count against the #329 hourly full-pass bucket** — window passes are bounded solely by their per-pass operator budget (I1) and do NOT consume the full bucket (explicit rule; the alternative — shared operator-hour accounting — is deferred as a future refinement). Explicit mode override to full is counted; budget rejection is explicit (reject/queue with 429-equivalent), never silent. Embedded: 500ms latency budget governs in-band local passes; window mode is scheduled-only (never in-band). Failure modes: budget bypass via override (counted), silent ignore of rejection (explicit error).

**J↔W correspondence (actual):** J1→W1 · J2→W2 · J3→W2-degenerate (full sub-flow) · J4→W2-report (staleness_report generation is W2's reporting output; report semantics in I2) · J5→W6 · J6→W5 · J7→W4 · J8→diagnostics/eval (Substep 7 DE2E-9/10).

**Review gate (J/W/P):** CLEAN after batch A fixes (full sub-flow, W4 cap semantics, J↔W mapping, report flow).

---

## Substep 3 — Prototype (non-GUI: pipeline diagram)

```
                        ┌─────────────────────────────────────────────────────┐
                        │                  DREAM PIPELINE                     │
                        └─────────────────────────────────────────────────────┘
  WRITES ──► _mark_dirty ──► dirty_roots ──┐
  (create/update/                          │
   supersede/invalidate/                   ▼
   promote/merge)              ┌─ dream() ──────────────────────────────┐
                               │  auto-select strategy:                │
   SCHEDULER ─────────────────►│   • write context  → local (W1)       │
   (timer/manual)              │   • scheduled      → stale-first (W2) │
                               │   • small/first    → full (W2-deg)    │
                               │  explicit mode override wins          │
                               └───────────────┬───────────────────────┘
                                               ▼
                     ┌──────────────────────────────────────────────────┐
                     │  DREAMER (dream.py)                             │
                     │  window select: retained-dirty-roots ∪          │
                     │    staleness-top-N  (null ranks STALEST)        │
                     │  budget gate: per-pass operator cap (B)         │
                     │  dedup: operator-set union across windows       │
                     │  retention: non-converged roots kept dirty,     │
                     │    NOT stamped fresh, attempt-capped            │
                     └───────────────────┬─────────────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────────────┐
                     │  TortoiseEP.run (ep.py)  [warm_start=True]      │
                     │  seed: graph-persisted messages (existing       │
                     │    msg_alpha/beta, back_msg_*) via _load_cache  │
                     │  fixed threshold γ → skip msgs with Δ ≤ γ       │
                     │  flush converged msgs to graph (existing)       │
                     │  invalidate on: topology change, edge transfer, │
                     │    evidence/baseline writes                     │
                     └───────────────────┬─────────────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────────────┐
                     │  WRITE-BACK (atomic)                            │
                     │  SET confidence + lastDreamedAt (same query)    │
                     │  (only when run converged — else keep old stamp)│
                     │  + trivial-stamp scan for operator-less claims  │
                     └───────────────────┬─────────────────────────────┘
                                         ▼
                     ┌──────────────────────────────────────────────────┐
                     │  OBSERVABILITY (health metrics)                 │
                     │  last-pass ts · coverage % · failures           │
                     │  alarm: zero-output while backlog exists        │
                     │  hosted: /v1/dream/health · embedded:           │
                     │    dream_health_check() call-triggered          │
                     └──────────────────────────────────────────────────┘
                     (retention feedback edge: non-converged region
                      roots re-enter dirty_roots for W4 retry)
```

States covered: idle (no backlog), queued (dirty set), running (locked), non-converged (retained, not fresh-stamped), capped (stale_unresolved), failed (alarmed), warm (graph messages as seed).

**Review gate (J/W/P):** CLEAN after batch A fixes (scheduler trigger, budget gate, retention feedback edge, operator-less stamp path).

---

## Substep 4 — Data Model

**Data model research hook (Ontology = low):** no hook — brief A3 + scope Axis Research Notes cover at sufficient granularity. Justified skip.

**Entities (no new kinds):**

| Entity | Existing | Change |
|---|---|---|
| `Point` node (non-operator claims) | `id`, `content`, `is_operator`, `status`, `confidence`, `ep_alpha/beta`, `posterior_alpha/beta`, `updatedAt`, `createdAt` | **+ `lastDreamedAt`** (ISO-8601 UTC, nullable). Semantics: timestamp of the last EP write-back that CONVERGED on this claim (a failed run does not update it). NULL = "never dreamed" — **scheduler ranks null STALEST**; report displays "never dreamed". Applies to non-operator claims only (operators are excluded from ranking/stamping). |
| Operator edge (IMPL/NAND) | exists, **already carries `msg_alpha`/`msg_beta` (+ `back_msg_alpha`/`back_msg_beta` for bidirectional operator-less edges)** — verified in `ep.py` `_load_cache`/`_flush_cache` | **No change.** These graph-persisted properties ARE the warm-start seed; `lastDreamedAt` is not added to edges. ONTOLOGY.md gains rows for the four message properties (currently undocumented — load-bearing for fast path AND warm-start). |
| Message state | graph-persisted (above) | **No new store.** Warm-start = `run(warm_start=True)`: load graph messages (existing), skip updates with Δ ≤ γ, flush back (existing). No in-memory mirror → no two-sources-of-truth. Cache-key discipline: `(op_id, claim_id, rel_type)` + separate forward/back slots. |

**Integrity constraints (engine level):**
- `confidence` + `lastDreamedAt` written in the SAME Cypher query (dream.py L98 pattern extended) — never two interleavable writes (surface 5 atomicity).
- `lastDreamedAt` updated ONLY when the run converges for that region; non-converged affected claims keep their old stamp (so they re-enter the staleness ranking — retention and freshness don't undermine each other).
- **Status→live transitions (`promote_point` and any equivalent) call `_mark_dirty`** (verified gap: sdk.py L1835 does not today); `dream()` does NOT clear dirty roots on converged runs that produced zero affected claims (draft-excluded runs — #780).
- Operator-less/isolated claims: **explicit trivial-stamp path** in full/scan passes — a dedicated query stamps `lastDreamedAt` for non-operator claims not covered by the EP flush (`run()` early-returns before flush when no factors; the scan path is separate and independent).
- Legacy null: no mass backfill on deploy (avoids stampede); scheduler's null-as-stalest drains them across passes naturally. Optional backfill policy documented (use `createdAt` as sentinel if a hard migration is ever wanted).
- FalkorDB index: `:Point(lastDreamedAt)` (verify composite `:Point(is_operator, lastDreamedAt)` support in implementation); must include/exclude null-property nodes per the chosen semantics (null must be rankable as stalest — if the index excludes them, ranking query must union a null scan); creation must be **idempotent + AOF-replay-safe** (existing `_ensure_registry_indexes` try/except pattern; `tests/test_embedded_concurrency.py:532` shows CREATE INDEX must survive replay) — created at init, not via migration, for new DBs.

**Ontology note (vocabulary):** ONTOLOGY.md Point-properties table gains `lastDreamedAt` (definition above, scoped to non-operator claims); edge-properties table gains `msg_alpha`/`msg_beta`/`back_msg_alpha`/`back_msg_beta` (documenting existing graph state this epic's warm-start operates on). No entity/edge-kind changes.

**Review gate (DM):** CLEAN after batch B fixes (graph-persisted message state recognized; null semantics split scheduler/report; promote→dirty + zero-affected retention; non-converged not-stamped; edge-transfer + baseline invalidation; trivial-stamp mechanism; index + ontology completeness).

---

## Substep 5 — Architecture

**Architecture research hook (Architecture = high):** no hook — scope Axis Research Notes + brief Tech Stack Research cover at sufficient granularity. Justified skip.

**Target state — components:**

| Component | File | Responsibility | Key changes |
|---|---|---|---|
| `SDK.dream()` (mode router + dirty-set owner) | `tortoise/sdk.py` | Auto-select strategy (write → local, scheduled → stale-first, small → full); explicit override wins (precedence table, I1); `staleness_report()`; dirty-root retention fix; promote→`_mark_dirty`; `dream_health_check()` | Mode plumbing; retention; promote hook; health check |
| `Dreamer` (scheduler) | `tortoise/dream.py` | `dream_window(budget)`: staleness-rank (null = stalest) ∪ retained-dirty → anchors; operator-set dedup; retention + attempt cap; health-metric emission; trivial-stamp scan | Window selection, dedup, retention, metrics, scan |
| `TortoiseEP.run` (engine) | `tortoise/ep.py` | `run(warm_start=True)`: load graph messages → seed → fixed-γ skip → flush; `warm_start=False` default (fast path unchanged, no γ-skip, no lock dependency) | warm_start param + γ-skip; invalidation helper (called from write paths) |
| Graph write-back | dream.py | Atomic `confidence` + `lastDreamedAt` SET (converged only) + trivial-stamp scan | lastDreamedAt in the write query |
| Hosted worker | `tortoise/hosted_api.py` | `_dream_worker` mode wiring; #329 full-bucket accounting (full + override only); `/v1/dream/health` | Mode wiring; budget rule; health endpoint |
| MCP tools | `tortoise/mcp_server.py` | `dream` (optional mode), `staleness_report`, `dream_health_check` tools | Additive to the #888-consolidated surface |
| Observability | dream.py + hosted + sdk | Metrics record + zero-output alarm (hosted endpoint; embedded call-triggered check) | Metrics + alarm |

**Key architecture decisions (from review):**
- **Warm-start state lives in the graph, not in a process-level cache.** Because messages are already persisted on edges and `run()` already loads them at entry, warm-start needs no new cross-run store — eliminating the hosted-mode problem (SDK rebuilt per drain would have killed an SDK-scoped cache) and the fast-path race (only `warm_start=True` runs, which are Dreamer-locked, engage the γ-skip). Invalidation is a write-path graph/cache operation (drop messages for affected edges), not a cache-owner lifecycle.
- **No new concurrency surface beyond existing locks.** `Dreamer._lock` covers dream-cycle runs; `compute_confidence` calls `run(warm_start=False)` and never touches γ-skip state. Write-path invalidation acquires `Dreamer._lock` (or defers via topology-version check at next run — implementation choice, must be race-tested).
- **Null-semantics split:** scheduler ranks null = stalest (first-deploy/crash recovery works); report displays "never dreamed". No special-case handling anywhere else.

**Concurrency contract:** single `Dreamer._lock` covers dream-cycle runs including γ-skip state; hosted per-tenant serialization (#85) unchanged; embedded 500ms latency budget unchanged (window mode scheduled-only). Invalidation from write paths acquires the lock or uses a version-check (race-tested).

**Failure-mode design:** budget exceeded → explicit reject/queue (never silent; 429-equivalent with Retry-After on hosted); DB unavailable → dreamer fails loudly + alarm; message seeds stale → invalidation on topology/transfer/baseline (DE2E-6a negatives); warm-start drift → E2E-6a gate before ship; non-convergence → retention + attempt cap + stale_unresolved; process crash mid-pass → no dirty-set persistence needed: old/null stamps re-enter the staleness ranking (scheduler self-heals).

**Deployment:** no new services, no new persistent stores. Hosted: per-tenant serialization + #329 full-bucket in-process (existing dicts); embedded: unchanged. No schema migration (property additive, null-safe, index idempotent at init).

**Consistency:** all 10 scope items have named homes — items 9/10 (staleness eval + diagnostics) are test/script artifacts owned by the verification checklist (DE2E-9/10), with the diagnostics decision recorded in epic docs before items 3–5 implementation.

**Review gate (Arch):** CLEAN after batch B fixes (graph-persisted warm-start; null-as-stalest scheduler; warm_start flag gating; baseline/transfer invalidation; embedded alarm evaluator; #329 rule explicit; item 10 named home).

---

## Substep 6 — Interfaces

**Interface research hook:** no hook — brief UX Pattern Research covers the staleness-on-read contract. Justified skip.

**Contract-first — all interfaces:**

**I1 — SDK `dream()`** (backward-compatible, additive):
```
dream(dirty_only: bool = True, full: bool = False, mode: str | None = None,
      max_hops: int = 2, budget: int | None = None) -> dict
```
**Precedence (explicit — resolves the review P1):**
1. Explicit `mode` (∈ {"local", "stale-first", "full"}) **wins over** the `full`/`dirty_only` sugar.
2. `full=True` maps to `mode="full"` **only when `mode is None`**.
3. `mode=None` + `full=False` → auto-select by context (write → local; scheduled → stale-first; small-graph/first-run → full).
4. `mode` × `_dirty_roots`: `local` operates on dirty roots (and unions explicit anchors); `stale-first` operates on the staleness-ranked window (dirty roots always unioned in, per W2); `full` ignores dirty roots.

**Per-mode return shapes (explicit key sets):**
- `local`: `{mode, iterations, converged, affected_claims, budget_used, coverage}` — `converged` retained for sdk.dream()'s dirty-root logic (reads `result.get("converged")`).
- `stale-first`: `{mode, batches, converged_all, converged, affected_claims, budget_used, coverage}` — `converged` = this pass's window-level convergence (used by the dirty logic); `budget_used` = distinct operators processed after dedup.
- `full`: `{mode, batches, total_affected, converged_all, budget_used, coverage}` (existing `batches/total_affected/converged_all` preserved).
- `coverage` semantics per mode: local → affected / window-reachable; stale-first → affected / remaining-stale-before-pass; full → affected / reachable non-operator claims.
- Errors: unknown mode → `ValueError`; `budget=0` → no-op result (not an error); **budget exceeded** → `BudgetExceededError` (new, `tortoise/exceptions.py`).

**I2 — SDK `staleness_report()`:**
```
staleness_report(budget: int = 100) -> dict
# budget = max regions returned (unit pinned)
# {"regions": [{"region_id", "stale_count", "oldest_lastDreamedAt", "coverage"}],
#  "generated_at"}  — ranked stalest-first; null lastDreamedAt sorts STALEST
# (region with only-null claims reports oldest_lastDreamedAt: null)
```

**I3 — MCP tools** (additive to #888 surface): `dream` (params: dirty_only, full, mode, budget), `staleness_report` (budget), `dream_health_check` (no params — returns alarm verdict + metrics; embedded alarm evaluator). Error mapping: `BudgetExceededError` → existing MCP `ERR_QUOTA` code (mcp_server.py:497); `ValueError` → `ERR_INVALID_ARG`.

**I4 — Hosted `/v1/dream`:** existing endpoint + `mode` field; **only full-mode passes (incl. via override) count against the #329 hourly bucket**; window passes bounded by per-pass operator budget (do not consume #329); response adds `mode` + `budget_used`; on budget exhaustion: **429 with `Retry-After` header** (seconds until hourly-window reset — consistent with the existing §6.1 429 contract hosted_api.py:493; **the existing full-mode 429 gains the header** in this epic).

**I5 — Hosted `/v1/dream/health` (new):** `{last_pass_at, coverage_pct (graph-wide), failure_rate, operator_counts, per_mode_counts, stale_backlog, alarm_verdict}`.

**I6 — Internal `Dreamer.dream_window(budget, staleness_rank_query, retained_dirty)`:** returns `{mode:"stale-first", batches, converged, converged_all, operators_deduped, budget_used, affected_claims}` — the SDK adapter maps `operators_deduped → budget_used` and exposes `converged` (window-level) to the dirty-root logic (explicit I6→I1 field mapping).

**I7 — Internal warm-start API (ep.py):** `run(..., warm_start: bool = False)` + invalidation helper `invalidate_messages(edge_ids: list[str] | None = None)` (None = drop all) called from topology writes, **edge transfers (supersede/invalidate/approve_merge)**, and evidence/baseline writes. Invalidation acquires `Dreamer._lock` or defers via topology-version check (race-tested). Fast path (`compute_confidence`) always uses `warm_start=False` — never engages γ-skip state.

**DB-down contract (shared):** hosted/selfhost → 500/503 with documented error shape; embedded → raise `TortoiseError` subtype. **Selfhost `/dream`** (selfhost_api.py:223) is an existing caller — **in scope**: it forwards `mode`/`budget` transparently (no budget accounting of its own — selfhost has no #329); verified not to break by the additive shape.

**Versioning:** additive fields only; per-mode key sets documented; existing callers (hosted worker, MCP, selfhost, embedded post-batch `compute_confidence` path — verified to ignore the return shape) unaffected.

**Review gate (Interfaces):** CLEAN after batch B fixes (per-mode shapes + I6 mapping, precedence table, BudgetExceededError + MCP ERR_QUOTA + Retry-After, DB-down contract, selfhost declared, coverage denominators, invalidation lock discipline, budget unit + null sorting).

---

## Substep 7 — Detailed E2E Test Cases

Each case is implementable as an automated test. **Harness (fixed per review):** hermetic embedded pattern per `tests/test_dream.py` (tempfile-backed `TortoiseSDK`, claims `status="live"` for the #780 draft filter) — NOT `tests/test_ep_directional.py`, which is Docker-gated (FalkorDBLite lacks live-docker graph semantics; its numeric cascades are calibrated against live FalkorDB). Docker-gated variants get hermetic twins; numeric thresholds re-locked at calibration on the embedded runner. No G-gates corpus fixture exists in code — a dedicated **EP-parity fixture builder** is defined below. Per-test fresh fixtures (uuid-namespace/tempfile) → order-independent under pytest-randomly; in-process state (hosted metrics, dirty sets) reset per test.

**Fixtures (shared builders):**
- `F1 — EP-parity corpus`: deterministic builder (defined in this epic's test module; ~60 claims / 20 premises / 25 IMPL edges / 6 derivation trees / 10 contradictions / 5 near-dups — synthetic corpus v1 shape from the eval spec, realized as code with fixed seed). Used for DE2E-6a/6b.
- `F2 — staleness fixture`: regions manufactured by **direct Cypher `SET lastDreamedAt` with fixed ISO timestamps** (never wall-clock dreaming — sub-second passes would produce identical stamps → flaky) + one null-stamp region.
- `F3 — fails-to-converge fixture`: dedicated builder with a specified oscillating structure (strong opposing baselines on a mutual/triangle NAND loop; structure verified at calibration to fail convergence within max_iter — the eval-spec B7 odd-NAND triangle is NOT suitable: it currently converges trivially).
- `F4 — frozen-ground-truth fixture`: pre-mutation converged confidence vector captured as oracle on a **sandboxed clone** (ground truth computed out-of-band — computing it on the live fixture would pollute the staleness being measured).
- `F5 — diagnostics fixture`: representative synthetic graph (pinned node/edge counts + fan-out distribution); real-snapshot run optional/skipped in CI.

**DE2E-1 — Full-graph refresh covers every reachable claim** (E2E-1)
- Setup: F2-style fixture with ≥3 disconnected regions, each with ≥1 operator; mix of live claims + ≥1 operator-less/isolated claim; record pre-pass confidence.
- Act: `sdk.dream(full=True)`; capture t0/t1 around the call.
- Assert: every non-operator reachable claim has `lastDreamedAt` in [t0, t1] (window assertion — never exact-equality to an implicit pass timestamp); operator-less claims stamped by the **trivial-scan path** (explicit query, independent of EP flush) and reported separately from reachable (total_affected = reachable only; scanned_count = operator-less stamped); `converged_all` True; return shape per I1 full key-set.
- Negative: zero-operator graph → no-op result, no crash, no stamp.
- **Atomicity sub-case:** injected partial failure mid write-back → both `confidence` and `lastDreamedAt` present or neither (same-query rule, surface 5).

**DE2E-2 — Stale-first pass is bounded, staleness-ranked, deduped** (E2E-2)
- Setup: F2 staleness fixture (old / medium / fresh / null stamps, fixed ISO) + a retained-dirty root outside the top-N.
- Act: `dream(mode="stale-first", budget=<B>)` repeatedly to full coverage.
- Assert: **primary outcomes** (not window-mechanics coupling): per-pass `budget_used ≤ B` (distinct operators after dedup — overlapping windows share operator sets, union not recompute); stalest region first each pass (null ranks first; deterministic id tie-break); retained-dirty root included despite being outside top-N (union); eventual full coverage within a bounded number of passes; all-null graph → single pass (window = full).
- Boundary: budget=0 → no-op; budget ≥ graph → single pass.

**DE2E-3 — Write-triggered refresh keeps isolation + precedence matrix** (E2E-3)
- Setup: freshly written claim + unrelated claims in another region.
- Act: write → `_mark_dirty` → `dream()` (default local).
- Assert: affected claims refreshed; unrelated claims |Δconf| ≤ 0.01 (G7, re-locked at calibration); return shape pinned to I1 local key-set `{mode, iterations, converged, affected_claims, budget_used, coverage}` (concrete, typed — not "backward-compatible" vagueness).
- **Precedence matrix sub-case** (I1 table, surface 1 guards): assert across mode×full×dirty_only combinations — explicit `mode` wins over sugar; `full=True` maps to full only when `mode is None`; `mode="stale-first"` + `dirty_only=True` → staleness window with dirty roots unioned; auto-selection: write-context → local.

**DE2E-4 — Freshness tracking is queryable; draft→promote stays stale** (E2E-4)
- Setup: F2 staleness fixture (3 stamped regions + null region) + a live claim that is then demoted to draft and promoted back.
- Act: `staleness_report()`; then a dream pass.
- Assert: each claim reports `lastDreamedAt`; report lists regions stalest-first matching pass order; null-stamp region reports `oldest_lastDreamedAt: null` and ranks first (stale); **draft→promote negative (fixed):** after demote+promote the claim re-enters `_dirty_roots` (promote→_mark_dirty fix) and the next pass re-stamps/re-derives it — asserted on the dirty-set/next-pass effect, NOT the report (a never-dreamed claim ranks stale regardless; dirty ≠ stale).
- **Zero-affected retention sub-case:** draft-only dirty roots + converged run with zero affected claims (#780 draft-excluded) → roots REMAIN dirty and are re-dreamed after promote.

**DE2E-5 — Lifecycle events absorb into dreaming + invalidation on transfer** (E2E-5)
- Setup: a claim + its operator; then supersede (L1777), invalidate (L1475), approve_merge (transitive L2616).
- Act: after each lifecycle write, run a dream pass.
- Assert: both endpoints enter dirty set; **transferred edges' `msg_*`/`back_msg_*` properties are DROPPED** after supersede/invalidate/approve_merge (graph-queryable observable — not "dream log" vagueness); surviving node re-derived confidence equals a from-scratch recompute within 1e-3.

**DE2E-6a — Warm-start equivalence (hard gate)** (E2E-6a)
- Setup: F1 EP-parity corpus; fixed seed.
- Act: run from-scratch (`warm_start=False`); mutate evidence; run warm-started (`warm_start=True`) **with NO message-flushing run interleaved between mutation and the warm-started execution** (prevents vacuous pass: a from-scratch reference run after mutation would re-flush fresh messages, hiding broken invalidation).
- Assert: `(iterations, converged, max|Δconf|)` within tolerance vs an isolated from-scratch run on the same post-mutation state — tolerance pinned at max|Δconf| ≤ 1e-3 (consistent with `test_rerun_stability_immutable_baselines`), re-locked at calibration; `converged` equality asserted; iterations recorded, not gated. MUST pass — failure blocks shipping warm-start (fallback: γ-skip disabled; epic targets still met).
- Negative cases (each: mutate → warm-started directly, no interleaved flush): (a) delete an operator; (b) supersede (edge transfer); (c) baseline change with identical topology → equivalence still holds (outcome assertion, not message-absence mechanism).

**DE2E-6b — Warm-start cost (measurement)** (E2E-6b)
- Setup: F1 fixture.
- Act: record factor-update count + wall time, warm vs from-scratch.
- Assert: **metric presence/recording only** — never wall-clock thresholds (CI variance would flake); savings surfaced in health metrics for the diagnostics decision.

**DE2E-7 — Non-converged region retained, not stamped fresh, retried with cap** (E2E-7)
- Setup: F3 fails-to-converge fixture; Dreamer attempt-cap configurable + backoff/clock injected (no real sleeps — flaky/slow otherwise).
- Act — **two explicit sub-scenarios, non-disjunctive:**
  - 7a: dream → non-converged → inspect dirty set + stamps → resolve conflicting evidence → dream again.
  - 7b: dream → non-converged with cap=1 → force cap.
- Assert 7a: roots REMAIN in dirty set after failed run (retention fix — currently dropped); affected claims' `lastDreamedAt` UNCHANGED by the failed run (not stamped fresh — W4); second dream converges and stamps.
- Assert 7b: region dropped from dirty set + surfaced as `stale_unresolved` in health metrics with pinned metric fields (attempt count, backoff state).

**DE2E-8 — Dream health observable, silent death detectable** (E2E-8)
- Setup: dreamer with stale backlog; in-process metrics/worker state reset per test (conftest `_reset_ip_rate_limits` pattern). Alarm trigger defined **counter-based** (not wall-clock): backlog > 0 ∧ output count == 0 since last pass.
- Act: hosted — GET `/v1/dream/health`; embedded — `dream_health_check()`; MCP — `dream`/`staleness_report`/`dream_health_check` tools.
- Assert: last-pass ts, coverage %, failure rate, operator counts surfaced (both surfaces); **alarm fires** when backlog > 0 AND zero output (EP stubbed to no-op via monkeypatch — layer-scoped); **positive-control (fixed):** non-empty backlog + a real converging run that produces output → alarm MUST NOT fire (an implementation that fires on backlog alone fails this — catches the ignored-output-conjunct bug); **MCP error mapping:** `BudgetExceededError` → ERR_QUOTA, unknown mode → ERR_INVALID_ARG (mcp_server.py:497 codes); no false positive on healthy idle.

**DE2E-9 — Freshness reduces staleness error (Indicator 3 acceptance)** (E2E-9)
- Setup: F4 frozen-ground-truth fixture (oracle on sandboxed clone). **Fixture validation step:** assert a from-scratch recompute on the mutated fixture moves confidence by > ε (otherwise the test is trivially green/flaky — the mutation must be load-bearing). Mutated region is **forced to be the stalest-ranked** (so the coverage→error test measures the causal path).
- Act: measure stale-error = mean |Δ| (stale confidence vs oracle); run stale-first passes with increasing coverage (coverage per I1 stale-first semantics: affected / remaining-stale-before-pass); re-measure.
- Assert: error shrinks below the pinned threshold — **ε = mean |Δ| ≤ 0.01, X = coverage ≥ 80%** (re-locked at calibration); error curve recorded in health metrics; eval spec gains this staleness-error gate next to G7 (thresholds pinned BEFORE implementation, not tuned post-hoc).

**DE2E-10 — Graph-scale diagnostics gate** (E2E-10)
- Setup: F5 representative synthetic fixture (pinned counts/fan-out); real-snapshot run optional (skipped in CI — external dependency).
- Act: run diagnostics script.
- Assert (**automated = measurable invariants only**): node/edge counts > 0; fan-out distribution sums to edge count; region/neighborhood sizes and connected-component stats emitted. **The stale-first-vs-full decision is a HUMAN gate** recorded in epic docs (not a CI assertion — a "decision recorded" assertion can't fail meaningfully); if full wins at our scale, items 3–5 are simplified via a recorded plan amendment BEFORE implementation.

**DE2E-11 — Hosted budget accounting (#329) + 429 contract** (new — surface 6)
- Setup: hosted fixture with capped hourly full-pass bucket; two tenants.
- Act: tenant A — `/v1/dream` mode=full twice (bucket cap); then mode=stale-first; tenant B — full-mode interleave.
- Assert: **full-mode passes (incl. via override) consume the #329 bucket; stale-first window passes do NOT** (bounded solely by per-pass operator budget); exhaustion returns **429 with `Retry-After` header** (seconds until hourly reset); rejection is explicit (error body), never silent; two-tenant interleave does not corrupt per-tenant accounting (surface 6 race).

**Negative-case coverage (consolidated):** empty graph; zero operators; budget=0; unknown mode (ValueError); budget exceeded (BudgetExceededError); legacy null lastDreamedAt; **single-SDK threaded concurrent dreams** (in-process `Dreamer._lock` serializes; assert identical results within tolerance, not bitwise — embedded redislite is not multi-connection-safe; multi-SDK interleave variants gated to live FalkorDB per conftest #432 note); **write-during-dream race** (supersede/baseline landing mid `warm_start=True` pass → no stale-message reuse, consistent final state — the I7 race that must be lock- or version-check-tested); fast-path `compute_confidence` + dream interleave without γ-skip corruption; DB-down (fails loudly + alarm); crash-mid-pass → re-scheduled via null/old stamps (scheduler self-heals).

**Review gates (E2E):** batch C — 3 parallel reviewers (e2e-coverage, e2e-reproducibility, test-quality) — CLEAN after fixes (DE2E-11 added; harness corrected to hermetic embedded pattern; vacuous-pass hazards closed in 6a/7/8; thresholds pinned in 9; wall-clock eliminated; precedence matrix + MCP mapping + atomicity + budget-accounting tests added).

## Substep 8 — Coherence Review + Risk Analysis

**Cross-substep drift checkpoints (actual mapping):** journeys J1–J8 ↔ workflows W1–W7 (+report flow, +full sub-flow) · scope items 1–10 ↔ surfaces 1–10 ↔ DE2E-1..10 1:1 · interfaces I1–I7 ↔ architecture components · data model ↔ interfaces (lastDreamedAt in I1/I2 return shapes + staleness query; msg_* properties recognized in I7).

**Risks + mitigations:**

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Warm-start reuses stale messages after edge transfer / baseline change (silent wrong beliefs) | medium | high | Invalidation on topology + transfer + baseline (W3/W6); DE2E-6a negatives (b)(c); γ threshold fixed; parity gate |
| Non-converged region stamped fresh → never reselected (retention undermined) | medium | high | lastDreamedAt written only on convergence (W4); DE2E-7 asserts failed run doesn't rank fresh |
| Null lastDreamedAt ambiguity (legacy vs fell-out-of-pipeline) | medium | high | promote→_mark_dirty fix; zero-affected runs don't clear dirty roots; scheduler null-as-stalest; DE2E-4 negative |
| Budget bypass via mode override on hosted (#329) | low | medium | Full+override counted against hourly bucket; explicit reject/queue; 429 + Retry-After |
| G7 regression (unrelated claims move) | medium | high | DE2E-3 isolation; window boundary = operator BFS closure |
| Embedded 500ms latency budget blown | low | medium | Window mode scheduled-only; local mode keeps ≤500ms |
| Silent-death invisible (A8) | medium | high | Health metrics + zero-output alarm (hosted endpoint; embedded call-triggered check); DE2E-8 |
| Non-converged roots dropped (A2) reintroduced | low | medium | Retention fix + DE2E-7 regression |
| Fast-path `compute_confidence` engages γ-skip state (race) | low | high | warm_start flag default False; only Dreamer-locked runs enable it; threaded test |
| Warm-start shipped without parity | low | high | E2E-6a hard gate before ship; fallback documented |
| Diagnostics says full wins → machinery wasted | medium | medium | Diagnostics runs FIRST (E2E-10 before items 3–5); decision recorded |
| Warm-start is a no-op in hosted (SDK rebuilt per drain) | medium | high (silent) | Eliminated by design: warm-start state is graph-persisted, not process-scoped (review fix) |

**Improvement opportunities:** (1) RBP-style within-run scheduling could be layered later without rework (out of scope now); (2) `recency_decay` on reads is a complementary freshness signal (deferred); (3) shared operator-hour budget accounting across modes (deferred refinement of W7 — full-bucket-only rule is the v1); (4) `dream_all`'s operator cap becomes mostly vestigial after dedup — keep as safety net.

**Review gates (Coherence):** 3 parallel reviewers — `cross-substep-drift`, `risk-completeness`, `improvement-opportunities` (batch D).

---

## Plan status markers

<!-- substep-gates: A CLEAN (6 fixed) · B CLEAN (17 fixed across DM/Arch/IF) · C CLEAN (16 fixed across coverage/repro/test-quality) · D pending -->
<!-- human-gate-2: pending -->
