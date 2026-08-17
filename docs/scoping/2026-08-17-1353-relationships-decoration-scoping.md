---
title: Scoping — #1353 relationships decoration optimization
type: engineering
domain: platform
doc_status: draft
subjects.team: epistemic-team
created: 2026-08-17
---

# Scoping: Affordable default-on relationships + EP eager — decoration optimization (#1353)

> **Issue:** #1353 · **Status:** scoped (decisions locked 2026-08-17) · **Owner decisions:** design session 2026-08-17 (Daniel + Pi) — state-centric reframe, promoted fields, EP-uncached, subject fail-closed · **Related research:** #316 benchmark, #317 (parent), #1144 eval, GraphRAG/LightRAG/Mem0/Zep context-shaping survey

## O/I/T (as filed, one indicator amended below)

- **Objective:** Make search-result **relationships enrichment affordable while keeping it ON by default** ("the graph is the product — relationships default-on"), plus EP annotation eager. NOT deferring relationships; making them cheap — and shaping them so the payload carries **derived epistemic state**, not raw graph inventory.
- **Indicators:**
  1. relationships=true is the default on `tortoise_search`/hybrid_search (unchanged — already true).
  2. The 2-hop fan-out is batched AND bounded: decoupled two-query design (Q1 point→operators, Q2 operator→endpoints deduped per operator); per-result relationship cap ≈10; global decoration token budget; list view carries IDs+labels+direction+state, full `related_content` on expand only.
  3. **Amended:** EpBreakdown computed from persisted α/β stays in every result (~1ms), **eager and UNCACHED** — cache is a documented non-goal (D11). The "cached by point_id, invalidated via #395" indicator is retired; #395 machinery not needed.
  4. E2E-8 latency verdict improves — decoration tail moves from ~1.9s (dense, limit=100) toward bounded, verified by #316 harness.
- **Targets:** decoration stage ≤ ~50ms at limit=100 on the dense synthetic corpus (was ~1.9s); EP data present in 100% of results, unchanged; decoration payload bounded (total relationship entries/tokens capped — kills the 122K-dict / ~24MB class).

## Context

### Profiling (2026-08-15)
Decoration pipeline split: EP annotation ~1ms (1.1%, derived from persisted values — trivially cheap, stays eager); relationships 2-hop fan-out 68ms sparse → 1,919ms dense (84%, super-linear, 122K relationship dicts at limit=100) — the real cost; entity fetch + serialize ~1ms.

**Root cause of the blowup:** `get_relationships` runs `(n)-[r]-(op)-[r2]-(other)` — *per result point* it re-expands its operators' full neighborhoods. Dense corpus: operators every 50th point, ~200 edges each → 100 results × ~6 shared ops × 200 = 122K dicts. Cost scales with **n-results × operator degree**, not operator count.

### Eval-impact analysis (2026-08-17)
The memory eval (LongMemEval, `tools/longmem_eval/`) reader consumes ONLY hit `content` + session dates (`render_context`) — relationships/EP payloads never reach the reader. **#1353 is a product-speed/context-quality lever, not an eval-score lever.** The eval measures accuracy levers (#1369 extractor wiring, #1349/#1348 retrieval, #1367 supersede); the #316 benchmark measures speed levers. Both arms are intentional.

### Verified facts (design session 2026-08-17, re-verified on origin/main)
- `tortoise_fts_query` always decorates with `get_relationships` for `entity_type=point` — no flag, no cap today (sdk.py:8949).
- `get_relationships` is already a single batched Cypher (not N+1) — the problem is the unbounded expansion, not the batching.
- **`related_content` (truncated to 200 chars per entry) is unconsumed by every codebase consumer** — not topic_summarization, not the dashboard, not the eval. It is pure payload bloat (the single largest token source).
- `topic_summarization` is a second consumer of `get_relationships` that needs **full completeness** (disputed-pair detection over all NAND pairs) → the cap must NOT live inside the shared function (D12).
- Operator edges carry only `idx` (0=source, >0=target). **No timestamps on edges**; temporal signal lives on point `createdAt` + the event stream.
- Current edge set matched by decoration: `IMPL|NAND|hasPart` only (search_engine.py:846). **`mitigated_by` and `CORRECTS` (supersede links) are invisible in search today** — two of the four thesis structures (IMPL, NAND, supersede, mitigate) don't surface.
- **`status` is absent from the search payload entirely** — a superseded point can appear in results and the agent cannot tell. `exclude_status` is a filter, not a surface.
- Subject attribution today is indirect: Point →(aboutEvent)→ Event →(aboutSubject)→ Subject; points rarely get a direct `aboutSubject`. Chain-derived attribution (via operators) is unreliable and can misattribute (→ follow-up issue, filed).

### Industry context (surveyed 2026-08-17)
- **GraphRAG (Microsoft):** local search caps at `top_k_relationships = 10` per entity + a **global token budget** (`max_context_tokens` 4K–12K; ~40% to entity+relationship descriptions). Ranks + filters to fit the budget — never dumps.
- **LightRAG:** ranks relationships by centrality/degree/edge weight (selection policy); their paper reports **smaller top_k performs better on factual QA** — "overly large retrieved context can be detrimental."
- **Mem0:** ~6.9K tokens per retrieval call average (whole response).
- **Zep (temporal KG):** "superseded facts are closed, not deleted" — point-in-time queries over derived state.
- **Temporal-KG literature:** validity/timeliness are properties of *facts-as-state*, not of arrow dates; stale facts hurt reasoning (HALO).

## Approach

1. **Decouple the hops (the cost fix).** Q1: per result point → its operator edges + operator metadata (`(n)-[r]-(op)`, bounded by point degree). Q2: per operator → its endpoints, deduped across result points, bounded per operator. **Q2 mechanics:** Cypher has no per-group LIMIT — fetch endpoints + their state (posterior_alpha/beta, status, createdAt, kind) in one query, `collect()` in Python, dedup across result points BEFORE the per-point cap is applied (so shared operators aren't starved by first-k slicing). Intermediate: ~n_ops × degree (≈20×200) instead of n_results × degree (≈100×200 per shared op) — the 20–50× win.
2. **State-centric decoration (the design fix).** Each entry pairs edge semantics (predicate, mechanism, role, direction — the *shape*) with derived state (peer status, confidence, variance, contested, created_at — the *values*). **Peer state is derived IN Q2, not via `annotate_ep_batch`:** the Q2 query fetches posterior_alpha/beta/status/createdAt per peer node; variance/contested are computed in Python (`search_engine._beta_variance`, same formula). This keeps the EP cost bounded by the Q2 row count (≈n_ops × degree) instead of a second annotation pass over potentially thousands of peers — the budget math holds. Derived state is ordering-independent: wiring an arrow today for entities that existed 6 months ago must not read as "superseded today."
3. **Bounded, epistemically-aware selection — class-aware cap.** Per-result cap ≈10 + global token budget ≈5K tokens (≈140 compact entries at ~35 tokens each; pinning the industry range — GraphRAG allocates ~40% of 4K–12K to relationships). **Critical classes are exempt from the count cap:** NAND edges, contested peers, superseded/retracted peers, mitigated_by/CORRECTS edges are always included (they are low-volume by construction). The ≈10 cap applies to the deduped IMPL support-mass. Selection priority: NAND > contested peers > superseded/retracted peers > mitigated_by/CORRECTS > recency > deduped IMPL support-mass. `family_size` per operator discloses sampling. Budget exhaustion degrades tail results to structure counts (supports 3 / contradicts 1) without peer lists.
4. **Promoted fields.** `status`, `superseded_by`, `supersedes`, `subject` become top-level result fields (like `ep` today). `ep` already promoted (confidence/variance/contested).
5. **List/expand split.** List view = compact state entries (no `related_content`). Full content on explicit expand — **scoped into this issue**: new `tortoise_expand_relationships` MCP tool + SDK method returning a single point's full relationship payload (unbounded `get_relationships` for one point is trivially cheap; also serves as the product-level expand surface).
6. **Subject surfacing ≤1 hop, fail-closed.** `subject` field populated only from the point's own `aboutSubject` or its event's `aboutSubject` — never chain-derived. Absent = honestly unknown, never wrong-via-chain.

## Locked decisions (2026-08-17)

| # | Question | Decision |
|---|---|---|
| D1 | Default-on | Relationships stay default-on (owner 08-15); #1353 is decoration-only — **retrieval/ranking byte-identical, untouched** |
| D2 | Fan-out design | Decoupled Q1/Q2 (point→operators, then operator→endpoints deduped) — kills n-results × operator-degree blowup |
| D3 | Cap shape | **Class-aware cap:** per-result count cap ≈10 applies to deduped IMPL support-mass; critical classes (NAND, contested, superseded/retracted, mitigated_by/CORRECTS) are exempt (low-volume by construction). + **global token budget ≈5K tokens** (~140 compact entries); tail results degrade to structure counts (supports 3 / contradicts 1) without peer lists |
| D4 | Selection priority | NAND > contested peers > superseded/retracted peers > mitigated_by/CORRECTS > recency > deduped IMPL support-mass |
| D5 | List view | Compact state entries: {predicate, mechanism, operator_id, role, direction, peer{id, kind, status, confidence, variance, contested, created_at}, family_size}; **`related_content` dropped** — its only consumer today is LLM agents as token bloat (no programmatic consumer); full content via expand (`tortoise_expand_relationships` tool, scoped in) |
| D6 | Edge set | + `mitigated_by`, + `CORRECTS`; **NOT** about* edges (noise, different purpose) |
| D7 | Decoration model | State-centric: derived state is the value, edge semantics carry the shape — not a raw relationship dump |
| D8 | Promoted fields | `status`, `superseded_by`, `supersedes`, `subject` promoted to top-level (today absent); `ep` stays promoted |
| D9 | Timestamps | **No edge-level timestamps** (v2 killed — solves the wrong problem: arrow date is discovery-order, not truth-order). Entity-level `createdAt` only; event log for provenance when needed |
| D10 | Subject surfacing | ≤1 hop (point's own or event's `aboutSubject`), **fail-closed** — never chain-derived |
| D11 | EP cache | **Non-goal.** EP stays eager + uncached (~1ms = 0.3% of budget; cache adds invalidation surface for a 1%-class win). #395 machinery not needed. Retires issue indicator 3's cache clause |
| D12 | Cap placement | Cap lives at the search-decoration call site, NOT inside shared `get_relationships` — `topic_summarization` keeps the unbounded full-fidelity path |
| D13 | Peer EP | Peer state (α/β → variance/contested, status, createdAt) derived **in the Q2 query**, not via a second `annotate_ep_batch` over peers — keeps EP cost bounded by Q2 rows |
| D14 | Expand surface | New `tortoise_expand_relationships` MCP tool + SDK method (single-point unbounded payload) — scoped into this issue |

## Complexity

- Standard (Architecture/Data). Low Ontology (payload fields only, no schema change), low UX/deps.

## Test plan / guardrails ("don't make search worse")

1. **Retrieval untouched:** RRF fusion + strategies byte-identical (decoration-only change).
2. **Preservation test (dense corpus fuzz):** random result sets → assert every NAND/contested/superseded/mitigated edge survives the cap (these classes are exempt from the count cap per D3, so the assertion is satisfiable). This is the test that catches "eliminating the critical things."
3. **Payload-budget test:** total relationship entries/tokens per search ≤ budget (≈5K tokens) — kills the 122K-dict / ~24MB class.
4. **SDK contract:** old keys preserved, new keys additive; `related_content` removal verified against zero consumers.
5. **#316 harness:** decoration ≤50ms dense @ limit=100; E2E-8 ≤300ms verdict `achieved`.
6. **`topic_summarization` regression:** unbounded path unchanged (D12).
7. **Attribution honesty:** no chain-derived `subject` (D10) — verified by search-decoration tests.

## Follow-up (filed)

- **Write-time subject binding for points** — confidence-gated direct `aboutSubject` edges at extraction, fail-closed, event-stream auditable, attribution audit tooling. Filed as its own issue (depends on #1353: the decoration's `subject` field makes gaps visible).
