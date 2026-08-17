<!-- research-path: docs/scoping/2026-08-17-1353-relationships-decoration-scoping.md -->

# #1353 — Bounded State-Centric Relationships Decoration — Implementation Plan

> **For Pi:** Use `executing-plans` to implement this plan task-by-task.

**Goal:** Make search-result relationships decoration affordable (≤~50ms dense @ limit=100, was ~1.9s) while surfacing derived epistemic state (supersede/NAND/mitigate/contested) instead of raw graph inventory.

**Team:** epistemic-team
**Role:** epistemic-team

**Architecture:** New `get_relationships_bounded()` in `tortoise/search_engine.py` (decoupled Q1/Q2 Cypher queries, class-aware cap, peer state derived in-query) replaces the unbounded `get_relationships()` call at the `tortoise_fts_query` decoration site. Shared `get_relationships()` stays untouched (D12 — `topic_summarization` needs full NAND completeness). Promoted fields (`status`, `superseded_by`, `supersedes`, `subject`) become first-class on `SearchResult`. New `expand_relationships` SDK method + MCP tool for full content on demand (D14). EP stays eager + uncached (D11 — no change to `annotate_ep_batch`).

### Pattern Research

> **Findings date:** 2026-08-17
> Gate skipped: plan touches zero third-party dependencies — pure in-repo Python (`search_engine`, `sdk`, `mcp_server`, FalkorDBLite test fixture). No library version/API/pitfall buckets apply. Design pattern evidence is in the scoping doc §Context (GraphRAG `top_k_relationships=10` + token budget, LightRAG selection policy + smaller-top_k-better finding, Mem0 ~7K-token responses, Zep state-not-edges).

### Integration Surface Map

| Surface | Data Flow | Contract | Test Layer |
|---|---|---|---|
| `search_engine.get_relationships_bounded` (new) | In: graph + point_ids → Out: bounded entries dict | Entries keep legacy keys (predicate, mechanism, operator_id, related_id, related_kind, direction) + new (role, peer{...}, family_size); no `related_content` in list view | Unit (embedded FalkorDBLite, deterministic small graphs) |
| `search_engine.get_relationships` (existing) | Unchanged — full fidelity | Regression: `topic_summarization` disputed-pair detection intact | Unit (existing test_topic_summarization.py) |
| `SearchResult` / `to_dict` | Promoted fields added (status, superseded_by, supersedes, subject) | Additive keys; old keys byte-identical when new fields empty | Unit (test_search_engine.py existing + new) |
| `sdk.tortoise_fts_query` decoration site | Call-site switch: bounded decoration + promoted state | Same response shape + new keys; `related_content` only via expand | Integration (embedded SDK — create points/operators/CORRECTS/aboutSubject, search, assert) |
| `sdk.expand_relationships` (new) + `mcp_server.tortoise_expand_relationships` (new) | In: point_id → Out: full payload w/ related_content | Single-point unbounded payload | Integration + MCP tool registration |
| Graph schema (read-only) | Cypher: IMPL/NAND/hasPart operator edges, CORRECTS, mitigated_by, aboutSubject, Event→aboutSubject | Edge set extended in decoration: +CORRECTS, +mitigated_by (D6); no writes | Covered by the above |

**Bug pattern flags:** cartesian-product blowup in multi-OPTIONAL-MATCH Cypher (mitigate with separate queries / dedup in Python); mitigation points leaking as IMPL endpoints in Q2 (exclude via `NOT (op)-[:mitigated_by]->(other)`); peer-count EP cost (derive α/β → variance in Python, no second `annotate_ep_batch`); per-group LIMIT absent in Cypher (collect + Python cap after dedup).

### Verification Plan

- Deterministic tests (embedded, no Docker): preservation (critical classes survive cap), payload budget, contract (legacy keys), promoted fields, expand tool, topic_summarization regression.
- #316 harness: decoration ≤50ms dense @ limit=100 is the issue target — verified via the benchmark harness in the Docker (prod-parity) environment; embedded smoke only in CI (README: "numbers can reverse on Docker — smoke only"). E2E-8 ≤300ms verdict unchanged in scope (decoration sits inside the E2E path).
- No UX (zero UI files), no content/config domains.

**Tech Stack:** Python 3.12, FalkorDBLite (embedded tests), FalkorDB (prod verification), uv.

---

### Task 1: `get_relationships_bounded` — decoupled Q1/Q2 + class-aware assembly

**Intent:** The core affordability + signal-preservation fix. Replaces the unbounded 2-hop expansion (`n_results × operator degree` → 122K dicts) with operator-deduped queries and an epistemic-priority cap.

**Acceptance:** New function in `tortoise/search_engine.py`; bounded entries (per-point cap 10, global budget 140 — **both caps govern IMPL support-mass only; critical classes are exempt from both**); critical classes (NAND, contested, superseded/retracted, mitigated_by, CORRECTS) always survive; peer state (variance/contested) derived from coalesced `posterior_alpha/ep_alpha/beta` in Python (annotate_ep_batch parity — never NULL α/β); mitigation points excluded from IMPL endpoint list (direction-agnostic, via Q2b id set) and surfaced as `mitigated_by` entries; retracted operators excluded; self-peers (`other == n`) excluded in assembly; legacy keys preserved; `get_relationships` untouched.

**Files:**
- Modify: `tortoise/search_engine.py` (after `get_relationships`, ~line 901)
- Test: `tests/test_relationships_bounded.py` (new)

**Steps:**
1. Write failing tests in `tests/test_relationships_bounded.py` (embedded graph fixtures): empty ids; cap respected (1 point, 1 op, 30 endpoints → ≤10 support-mass entries + family_size=30); NAND always survives (10 NAND + 30 IMPL); contested peer always survives (α/β → variance > threshold); superseded/retracted peer always survives; **12 NAND on one point → all kept (cap waived for criticals)**; **>140 critical entries → all kept (global budget governs support-mass only)**; mitigation surfaced as `mitigated_by` entry and excluded from IMPL endpoints (**both directions — legacy inbound graphs**); role/direction from idx; legacy keys present; `related_content` absent in list view; **operator with 0 non-operator endpoints (Q2 empty → no entry, family 0)**; **self-edge (`other==n`) excluded**; **retracted operator edges excluded**; global budget exhaustion → structure counts for tail (support-mass only); `get_relationships` regression (full content intact).
2. Run tests — expect FAIL (function not defined).
3. Implement `get_relationships_bounded(graph, point_ids, per_point_cap=10, global_budget=140, raw_cap=3000)`:
   - Q1a: `MATCH (n:Point) WHERE n.id IN $ids OPTIONAL MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point {is_operator:true}) WHERE (op.status IS NULL OR op.status <> 'retracted') RETURN n.id, type(r), r.idx, op.id, op.op_type, coalesce(op.label,''), op.createdAt`
   - Q1b (CORRECTS in/out): separate query, dedupe in Python. RETURN old/new `id, status, createdAt, content` (content for snippet).
   - Q2b (mitigations first — its id set feeds Q2 exclusion): `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids MATCH (op)-[:mitigated_by]->(m:Point) RETURN op.id, m.id, m.status, m.createdAt, m.content`
   - Q2 (endpoints, coalesced EP, NAND-first, raw-capped): `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) WHERE (other.is_operator = false OR other.is_operator IS NULL) AND NOT (op)-[:mitigated_by]->(other) WITH op, r2, other ORDER BY CASE WHEN type(r2) = 'NAND' THEN 0 ELSE 1 END, other.createdAt DESC RETURN op.id, type(r2), r2.idx, other.id, other.pointKind, other.status, coalesce(other.posterior_alpha, other.ep_alpha, 1.0), coalesce(other.posterior_beta, other.ep_beta, 1.0), (other.posterior_alpha IS NOT NULL OR other.ep_alpha IS NOT NULL), other.createdAt LIMIT $raw_cap`
   - Q2-family counts: `MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids OPTIONAL MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) WHERE (other.is_operator = false OR other.is_operator IS NULL) AND NOT (op)-[:mitigated_by]->(other) RETURN op.id, type(r2), count(other)`
   - Assembly: per point, critical classes always kept (exempt from per-point AND global caps); support-mass capped (per-point 10, global 140 — global governs support-mass only); **priority order per D4: NAND > contested > superseded/retracted > mitigated_by/CORRECTS > recency > deduped IMPL**; skip `other == n`; `family_size` per operator from the count query; `variance = _beta_variance(alpha, beta)`, `contested = has_ep and variance > CONTESTED_VARIANCE_THRESHOLD`; structure-count degradation for tail results (supports/contradicts counts, no peer lists).
4. Run tests — PASS.
5. Commit: `feat(retrieval): bounded state-centric get_relationships_bounded (T1 #1353)`.

### Task 2: `fetch_point_epistemic_state` + `SearchResult` promoted fields

**Intent:** The "promoted" half of D8 — `status`, `superseded_by`, `supersedes`, `subject` become first-class so the agent sees supersession/attribution without scanning.

**Acceptance:** `fetch_point_epistemic_state(graph, point_ids)` returns {pid: {status, superseded_by: {id, content_snippet, created_at}|None, supersedes: [{id, content_snippet, created_at}], subject: {id, name, kind}|None}}; subject ≤1 hop only (own `aboutSubject` or event's — fail-closed, no chains, **explicitly None when a subject is reachable only via operator 2-hop**); CORRECTS entry shape: {mechanism: "CORRECTS", direction: outgoing|incoming (arrow direction), related_id, related_kind: "point", peer{...}, created_at} — no operator_id/family_size (direct edge); `SearchResult` gains the four fields with safe defaults; `to_dict` emits them additively.

**Files:**
- Modify: `tortoise/search_engine.py` (new `fetch_point_epistemic_state`; `SearchResult` dataclass ~line 171)
- Test: `tests/test_relationships_bounded.py`

**Steps:**
1. Write failing tests: promoted fields shape; subject from own `aboutSubject`; subject from event's `aboutSubject`; **chain-reachable subject (operator 2-hop, no direct/event aboutSubject) → None**; superseded_by from incoming CORRECTS with content snippet; supersedes list from outgoing CORRECTS; CORRECTS entry shape (no operator_id); `to_dict` additive.
2. Run — FAIL. 3. Implement. 4. Run — PASS. 5. Commit: `feat(retrieval): promoted epistemic state fields on SearchResult (T2 #1353)`.

### Task 3: Wire bounded decoration + promoted fields into `tortoise_fts_query`

**Intent:** The call-site switch (D12) — product search gets bounded, state-centric decoration; retrieval/ranking untouched.

**Acceptance:** `tortoise_fts_query` (point) calls `get_relationships_bounded` + `fetch_point_epistemic_state`; entity fetch for points includes `status`; `SearchResult` populated with promoted fields; legacy search tests (tests/test_tortoise_search.py, tests/test_search_engine.py) still pass; unbounded `get_relationships` import removed from the sdk call site.

**Files:**
- Modify: `tortoise/sdk.py` (~8949-9027: import line, entity fetch, decoration site, SearchResult construction)
- Test: `tests/test_search_promoted_fields.py` (new, embedded SDK: build points+operators+CORRECTS+aboutSubject, search, assert bounded + promoted; assert legacy keys)

**Steps:**
1. Write failing sdk-level tests — **including the retrieval-identity guardrail: `test_retrieval_ranking_unchanged` — same query with decoration swapped (monkeypatch `get_relationships_bounded` → `{}` vs real) returns the identical result-id sequence/order** (proves decoration never reorders). 2. Run — FAIL. 3. Implement (swap call, extend point entity fetch with `n.status`, populate fields). 4. Run — PASS. 5. Commit: `feat(retrieval): bounded decoration + promoted fields in tortoise_fts_query (T3 #1353)`.

### Task 4: `expand_relationships` SDK method + MCP tool

**Intent:** D14 — the list/expand split's expand side: full content on demand, trivially cheap single-point.

**Acceptance:** `TortoiseSDK.expand_relationships(point_id)` returns the full unbounded relationship payload (incl. `related_content`); `tortoise_expand_relationships(point_id)` MCP tool registered and routed; MCP tool-registration test passes.

**Files:**
- Modify: `tortoise/sdk.py` (new method near the search API), `tortoise/mcp_server.py` (new tool)
- Test: `tests/test_search_promoted_fields.py` + MCP registration test (pattern: tests/test_mcp_server.py)

**Steps:** 1. Failing tests. 2. FAIL. 3. Implement both surfaces. 4. PASS. 5. Commit: `feat(retrieval): tortoise_expand_relationships SDK + MCP (T4 #1353)`.

### Task 5: Full-suite verification + benchmark smoke

**Intent:** Prove "don't make search worse": regression + guardrail suite green; latency signal.

**Acceptance:** `uv run pytest tests/test_relationships_bounded.py tests/test_search_promoted_fields.py tests/test_tortoise_search.py tests/test_search_engine.py tests/test_topic_summarization.py -v` all green; embedded bench smoke runs without error; dense-corpus preservation fuzz test passes (NAND/contested/superseded/mitigated survive cap); payload-budget assertion passes; note #316 Docker verdict as the official ≤50ms verification.

**Files:**
- Test: `tests/test_relationships_bounded.py` (fuzz preservation + budget), `tests/bench/test_bench_core.py` untouched

**Steps:** 1. Run the targeted suite. 2. Fix any failures. 3. **Fuzz preservation test on the dense synthetic corpus: random result sets → NAND/contested/superseded/mitigated/**CORRECTS** edges survive the cap**; **payload-budget assertion at search level (entries ≤ budget formula; >140-critical-entries case keeps all criticals)**; boundary fixtures (0-endpoint operator, 12-NAND cap-waiver, self-edge). 4. Run embedded bench smoke (`python -m benchmarks.run_report --corpus-size 200 --samples 5 --warmup-iters 2`). 5. Record results in the issue comment. 6. Commit: `test(retrieval): #1353 guardrail verification (T5 #1353)`.

### Failure Modes
- Mitigation points leak as IMPL endpoints in Q2 → excluded via `NOT (op)-[:mitigated_by]->(other)`; surfaced once as `mitigated_by` entry. **Expected:** no dupes; test asserts absence in endpoint list.
- Cartesian blowup from chained OPTIONAL MATCH (Q1b) → separate queries + Python dedup. **Expected:** row count ≤ edges; test asserts no dupes.
- Peer EP cost over thousands of Q2 peers → state derived in-query, no second `annotate_ep_batch`. **Expected:** decoration cost ∝ Q2 rows; budget test asserts bounded time on dense fixture.
- Global-budget starvation of shared operators → dedup operators across result points BEFORE per-point cap; family_size discloses sampling. **Expected:** every result point keeps its critical classes; test asserts.

<!-- plan-review: status=clean (2026-08-17, 13 findings addressed) -->
