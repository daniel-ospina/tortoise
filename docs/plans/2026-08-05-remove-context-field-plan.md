# Implementation Plan: Delete `context` field from Point/Operator nodes (#49)

> ⚠️ **PARTIALLY EXECUTED — READ CAREFULLY.** Phase 1 (context deprecation, stop-writes, projection_version gate) was implemented in PR #137. Phase 2 (context removal tooling, read-path sweep, #99 guard restoration) is tracked in PR #155 (open). Retained for design rationale. Do not re-execute completed Phase 1 tasks; §3 Phase 2 tasks (2.1–2.13) are pending in #155.

**Status:** Executed  
**Complexity:** Complex  
**Issue:** [daniel-ospina/tortoise#49](https://github.com/daniel-ospina/tortoise/issues/49)  
**Prerequisites:** #86 (directional IMPL), #99 (test-guard), graph restored state (4,546 pts with context)  
**Written:** 2026-08-05  

---

## 1. Problem Statement

The `context` property on Point and operator nodes was an early design choice for namespacing and domain-scoping graph queries. It has since become a catch-all field: 4,546 restored points carry it today. The property conflates four distinct concerns—namespace membership, source provenance, discovery/structural enumeration, and EP subgraph selection—that each deserve first-class mechanisms. Additionally, `context` enables Cypher injection in `analyze.py:320`, carries dead `context_multipliers` in `weights.py:41-47`, and forces `kind_filter` in `suggest_entry_points` to filter by `n.context` instead of `n.pointKind`. Deleting `context` forces each of the four concerns onto clean, purpose-built infrastructure, removes injection surfaces, and simplifies the query layer.

---

## 2. Proposed Solution

**Phased across 2 releases:**

### Phase 1 (Release N): Build re-homes + stop writes

All four replacement mechanisms are built and operational. The SDK, MCP server, hosted API, and extractor **accept** `context` but **do NOT write it** to the graph—the parameter is consumed with a deprecation warning and routed to the correct new mechanism. No production graph gets new `context` properties after this release. The projection version-gates old `PointAdded` events (pops + discards `context` on replay).

### Phase 2 (Release N+1): Remove reads + migration

All `context` reads are removed from the SDK, MCP server, hosted API, taxonomy, search engine, weights, grounding, analyze, and session_continuity. A `REMOVE n.context` migration runs against the graph. Old methods (`list_domains`, `compute_confidence(context=)`, etc.) are deleted. Skills and tests are updated. After Phase 2: **0 `context` params in the public API, 0 `context` properties in the graph.**

### The 4 Re-home Mechanisms

| Concern | Was | Now |
|---------|-----|-----|
| **Namespace membership** | `context` string | Pack registry (`pack_registry.py`) — already built |
| **Source provenance** | `context` string | `_link_source` creates `extractedFrom` edge; NEW: `references` edge from Source→entity at write time; `get_provenance_chain` (sdk.py:2569) queries it |
| **Discovery / enumeration** | `list_domains()` / `summarize_structure()` by `context` | `list_pointkinds()` (MATCH on `pointKind` — what EXISTS), `list_sources()` (Source grouping — where FROM), `pack_registry.list_all_kinds()`/`list_relations()`/`expand_kind()` (what CAN exist); `summarize_structure()` re-keyed to `pointKind` |
| **EP subgraph selection** | `compute_confidence(context=...)` | `compute_confidence(anchors: list[str], max_hops: int = 2, rel_filter: str = "IMPL\|NAND", direction: str = "incoming"\|"outgoing"\|"both")` — BFS from anchors along operator edges, collect operator IDs, feed EP |

### API Compatibility

Phase 1: **Accept + ignore + warn** (deprecation). Not silent break (MCP tools are agent-facing), not hard break.
Phase 2: **Remove**. All `context` params become hard errors or are deleted.

---

## 3. Implementation Plan

> ⚠️ **SUPERSEDED by §10.4 — DO NOT EXECUTE from this section.**
> This is the original task list from plan v1. It contains stale details
> (MCP count "7" vs actual 11, missing fixes, wrong phase assignments).
> The consolidated, ontology-aligned task list is in **§10.4**.
> This section is preserved for revision history only.


### Phase 1 — Build (Release N)

#### Task 1.1: Build `references` edge creation + idempotent backfill

**Intent:** Make the `references` edge (Source→entity) a first-class write-time edge, completing the provenance DAG so `context` is unnecessary for source tracking.

**Acceptance:**
- `_link_source` (projection/edges.py) creates BOTH `extractedFrom` (Point→Source) AND `references` (Source→entity) edges at write time
- Idempotent backfill script populates `references` for all existing Points that have `extractedFrom` but no `references`
- `get_provenance_chain` (sdk.py:2569) query succeeds against real graph data

**Files:**
- **Modify:** `tortoise/projection/edges.py` — `_link_source` adds `MERGE (s)-[:references]->(entity)` after source creation; accept optional `entity_ref` param
- **Modify:** `tortoise/sdk.py` — `get_provenance_chain` (line 2569) query validated (already queries `references` edge); `create_point` passes source entity info through
- **Create:** `graph-scripts/backfill_references.py` — idempotent script: `MATCH (p:Point)-[:extractedFrom]->(s:Source) WHERE NOT (s)-[:references]->() MERGE ...`
- **Test:** `tests/test_references_edge.py`

---

#### Task 1.2: Build structural enumeration — `list_pointkinds`, `list_sources`, re-key `summarize_structure`

**Intent:** Replace `list_domains()` (GROUP BY context) with three surfaces that together cover all discovery use cases: what pointKinds exist in the graph, what Sources data came from, and what the pack registry declares.

**Acceptance:**
- `sdk.list_pointkinds()` → `[{pointKind, count}, ...]` ordered by count DESC
- `sdk.list_sources()` → `[{sourceKind, url, title, point_count}, ...]` ordered by point_count DESC
- `sdk.summarize_structure()` re-keyed to `n.pointKind` instead of `n.context`
- Phase 1: old `list_domains()` still works but emits deprecation warning

**Files:**
- **Modify:** `tortoise/sdk.py` — add `list_pointkinds()` (MATCH (n:Point) RETURN n.pointKind, count), `list_sources()` (MATCH (n:Point)-[:extractedFrom]->(s:Source) RETURN s group by sourceKind), re-key `summarize_structure()` from context→pointKind
- **Modify:** `tortoise/taxonomy.py` — add `list_domains` deprecation warning (Phase 1 compat shim); Phase 2 deletes it
- **Test:** `tests/test_discovery_surfaces.py`

---

#### Task 1.3: Build anchors-based `compute_confidence` selector

**Intent:** Replace `context`-scoped EP with BFS anchor expansion — "give me the confidence of everything connected to these anchors within N hops."

**Acceptance:**
- `compute_confidence(anchors: list[str], max_hops: int = 2, rel_filter: str = "IMPL|NAND", direction: str = "outgoing")` works
- Direction semantics: IMPL directional per #86 (incoming = what affects anchor, outgoing = what anchor affects), NAND symmetric
- BFS collects operator IDs along matched edges; feeds EP engine
- Cap `max_nodes` (200 default) with warning
- Phase 1: old `context` param still accepted → routes to anchors-based (mapped from context → matching point IDs); emits deprecation warning

**Files:**
- **Modify:** `tortoise/sdk.py` — `compute_confidence` signature change; add `_bfs_anchor_expansion()` helper; old `context` branch (lines 1081–1087) replaced with anchors-based
- **Modify:** `tortoise/sdk.py` — `_apply_source_inheritance` (line 1124) and `calibrate_summary` (line 1158) accept optional `point_ids` filter alongside deprecated `context`
- **Test:** `tests/test_ep_selector.py` — includes parity test: anchors-based vs old context-based on the restored `licensing-decision-compare` subgraph must produce same results (0.906 / 0.8875 / 0.794)

---

#### Task 1.4: Add query-layer silent-empty suggestions

**Intent:** When `query()` / `paginated_query()` / `tortoise_fts_query()` return 0 results, instead of silence, suggest nearby kind names via Levenshtein distance against `pack_registry.list_all_kinds()` and `list_pointkinds()`.

**Acceptance:**
- Silent-empty query responses include `suggestions: ["Did you mean 'statement'?", ...]` when close matches exist
- Performance: Levenshtein only runs on empty result sets; bounded to registered kind names (~100-200)
- No suggestions when query had no kind filter

**Files:**
- **Modify:** `tortoise/sdk.py` — `query()`, `paginated_query()`, `tortoise_fts_query()` → after result set check, compute suggestions if empty
- **Create:** `tortoise/query_suggestions.py` — `suggest_kind(needle: str, candidates: list[str], threshold: float = 0.3) -> list[str]`
- **Test:** `tests/test_query_suggestions.py`

---

#### Task 1.5: Repurpose `suggest_entry_points` `kind_filter` to filter by `pointKind`

**Intent:** `kind_filter` currently maps to `n.context` (line 1461). After context deletion, it maps to `n.pointKind` — its correct semantics.

**Acceptance:**
- `suggest_entry_points(query, kind_filter="statement")` filters by `n.pointKind = "statement"`, not `n.context`
- Phase 1: if `kind_filter` matches a known pointKind → filter by pointKind; if matches a known legacy domain name → emit deprecation warning, route to discovery surfaces
- Phase 2: only pointKind filtering

**Files:**
- **Modify:** `tortoise/sdk.py` — `suggest_entry_points` (line 1448): change `kind_filter` filter from `n.context` to `n.pointKind`; add deprecation shim
- **Test:** `tests/test_suggest_entry_points.py` — update existing tests

---

#### Task 1.6: Stop-writes — deprecation warnings on all `context` write paths

**Intent:** Accept `context` everywhere it's passed, but DON'T write it to the graph. Warn + redirect to the correct mechanism.

**Acceptance:**
- SDK `create_point(context="x")` → warning "context is deprecated; use extractedFrom for provenance, pack registry for namespacing"; `context` NOT written to node
- SDK `create_operator(context="x")` → same warning; `context` NOT written
- MCP server 7 tools with `context` param → wrap in `_safe()`, param still accepted, warning emitted
- `hosted_api.py` `CreatePointRequest.context` → accepted but not stored
- `extractor.py` 12+ sites → `context` param passed but stripped before write
- `api.py` `add_point(content, context, provenance)` → `context` accepted but not included in event; deprecated in signature; positional arg preserved for compat
- `file_decision(context=...)` → uses anchors-based internally, ignores context; warning
- `diary_write` / `diary_read` → move `diary_{agent_name}` namespace to `wing` property; `context` param deprecated

**Files:**
- **Modify:** `tortoise/sdk.py` — `create_point` (strip `context` from props before `CREATE`, emit warning), `create_operator` (line 504-506 removed, warning if context passed), `file_decision` (remove context from create_point calls, accept+ignore with warning), `diary_write`/`diary_read` (use `wing` instead of `context`)
- **Modify:** `tortoise/mcp_server.py` — wrap all 7 context-bearing tools: `tortoise_create_point` (148), `tortoise_query` (182), `tortoise_paginated_query` (234), `tortoise_search` (311), `tortoise_compute_confidence` (358), `tortoise_calibrate` (384), `tortoise_create_operator` (397)
- **Modify:** `tortoise/hosted_api.py` — `CreatePointRequest.context` (445) → accepted but not stored; `list_points` (541) context filter → route to `list_sources()` + emit deprecation
- **Modify:** `tortoise/api.py` — `add_point(content, context, provenance)` (line 94) → `context` accepted but not included in `_point()`; warning emitted
- **Modify:** `tortoise/extractor.py` — 12+ sites → remove `context=f"conversation:{source_id}"` / `f"document:{source_id}"`; pass `extractedFrom` instead
- **Test:** `tests/test_context_deprecation.py` — verify warnings on all paths; verify context NOT in created nodes

---

#### Task 1.7: Projection version gate — discard `context` from replayed events

**Intent:** Old `PointAdded`/`OperatorAdded` events in `.jsonl` logs carry `context` in their payload. On rebuild/replay, the projection must discard it so replayed events don't re-introduce `context` to the graph.

**Acceptance:**
- `_rebuild_pass1` (projection/__init__.py) strips `context` from `SET n.context=$context` in the MERGE query
- `_apply_one` (module-level fold) pops `context` from old events
- `_revise_point` no longer sets `n.context`

**Files:**
- **Modify:** `tortoise/projection/__init__.py` — `_apply_one`: pop `context` from `PointAdded`/`OperatorAdded` events; `_revise_point`: remove `n.context = coalesce($x, n.context)`; `_rebuild_pass1`: remove `n.context=$context` from SET clause; `_rebuild_pass2`: no changes needed
- **Test:** `tests/test_projection.py` — verify old events with `context` are replayed without `context` in graph

---

### Phase 2 — Remove (Release N+1)

#### Task 2.1: `REMOVE n.context` migration

**Intent:** One-shot migration that removes the `context` property from all 4,546 Point nodes in the graph.

**Acceptance:**
- `MATCH (n:Point) WHERE n.context IS NOT NULL REMOVE n.context` succeeds
- Post-migration: `MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)` → 0
- Script is idempotent (safe to run again)

**Files:**
- **Create:** `graph-scripts/remove_context_migration.py`
- **Test:** `tests/test_remove_context_migration.py` — verify graph count before/after

---

#### Task 2.2: Remove `context` from SDK reads

**Intent:** All `context`-accepting methods in `sdk.py` drop the parameter entirely (Phase 2 hard removal).

**Acceptance:**
- No method in `sdk.py` accepts `context` parameter
- Old callers that pass `context=` get `TypeError: unexpected keyword argument`

**Files:**
- **Modify:** `tortoise/sdk.py`:
  - `query()` (601) — remove `context` param and filter clause; add `kind_filter` from query_suggestions integration
  - `paginated_query()` (636) — same
  - `tortoise_fts_query()` (1346) — remove `context` param; full-scan mode uses kind-only filter
  - `compute_confidence()` (1039) — remove `context` param; anchors-based only
  - `_apply_source_inheritance()` (1124) — remove `context` param
  - `calibrate_summary()` (1158) — remove `context` param
  - `file_decision()` (851) — remove `context` param
  - `diary_write()` / `diary_read()` — remove `context` param (already migrated to `wing`)
- **Modify:** `tortoise/sdk.py` — `traverse()` (688) — remove `m.context` from RETURN
- **Test:** Update all existing tests that pass `context=` to SDK methods

---

#### Task 2.3: Remove `context` from MCP server

**Intent:** All 7 MCP tools drop their `context` parameter.

**Files:**
- **Modify:** `tortoise/mcp_server.py` — remove `context` param from:
  - `tortoise_create_point` (148)
  - `tortoise_query` (182)
  - `tortoise_paginated_query` (234)
  - `tortoise_search` (311)
  - `tortoise_compute_confidence` (358)
  - `tortoise_calibrate_summary` (384)
  - `tortoise_create_operator` (397)
  - `tortoise_file_decision` (451)
- **Test:** `tests/test_mcp_server.py` — update all test calls

---

#### Task 2.4: Remove `context` from hosted API

**Files:**
- **Modify:** `tortoise/hosted_api.py` — remove `context` from `CreatePointRequest` (445), `PointResponse` (471), `list_points` (541) context filter; remove 22 lines of context handling
- **Test:** `tests/test_hosted_api.py`

---

#### Task 2.5: Remove `context` from taxonomy.py

**Files:**
- **Modify:** `tortoise/taxonomy.py` — delete `list_domains()` (GROUP BY context); `list_topics()` (840) — remove `context` from return dict and neighbor queries
- **Modify:** `tortoise/sdk.py` — delete `list_domains()` wrapper (834); update `list_topics()` (840) to not return `context`

---

#### Task 2.6: Remove `context` from search_engine.py

**Files:**
- **Modify:** `tortoise/search_engine.py`:
  - `SearchResult` dataclass (49) — remove `context` field
  - `classify_query()` (81) — remove `context` param
  - `run_structural_query()` (218) — remove context filter clause (lines 247-251) and match_score logic
  - `degradation_chain()` (309) — remove `context` param propagation
  - `fallback_tfidf()` (585) — remove `context` field from SearchResult construction
  - `to_dict()` — remove context field
- **Test:** `tests/test_search_engine.py`

---

#### Task 2.7: Remove `context` from weights.py (dead code)

**Files:**
- **Modify:** `tortoise/weights.py` — delete `context_multipliers` dict (lines 41-47) and the `w *= context_multipliers[context]` block

---

#### Task 2.8: Remove `context` from grounding.py

**Files:**
- **Modify:** `tortoise/projection/grounding.py` — line 59: replace `WHERE n.context IN ['resolution-event','resolution-vector']` with pointKind-based filter; `a[idx[pid]] = 1.0` seeded on `n.pointKind IN ['resolution-event', 'resolution-vector']`
- **Test:** `tests/test_grounding.py`

---

#### Task 2.9: Fix `analyze.py` injection + remove `context`

**Files:**
- **Modify:** `tortoise/analyze.py` — line 320-321: remove `STARTS WITH` string interpolation; replace with parameterized kind filter or anchored subgraph selector
- **Test:** `tests/test_analyze_scoped.py`

---

#### Task 2.10: Remove `context` from session_continuity.py

**Files:**
- **Modify:** `tortoise/session_continuity.py` — line 20: `self.sdk.query(context=self.session_id)` → use pointKind filter or diary_read; line 42: remove `context=self.session_id` from create_point

---

#### Task 2.11: Remove `context` from projection edges

**Files:**
- **Modify:** `tortoise/projection/edges.py` — line 32: `s.context='orphan-stub'` → remove context from stub node creation

---

#### Task 2.12: Update skills

**Files:**
- **Modify:** `skills/how-to-use-tortoise/SKILL.md` — remove all `context` usage from examples; replace with `extractedFrom`, pack namespace, anchors-based EP
- **Modify:** `skills/tortoise-file-finding/SKILL.md` — same

---

#### Task 2.13: Update tests globally

**Files:**
- **Modify:** ~30 test files referencing `context` — update to use replacement mechanisms
- **Create:** `tests/test_context_removal.py` — meta-test: `grep -r "context" tortoise/` must return 0 hits in production code (excluding comments/docs referencing historical context)

---

## 4. Testing Strategy

### Phase 1 Tests

| Test File | What it verifies |
|-----------|-----------------|
| `tests/test_references_edge.py` | `_link_source` creates both `extractedFrom` + `references` edges; backfill idempotent |
| `tests/test_discovery_surfaces.py` | `list_pointkinds()` returns correct aggregates; `list_sources()` groups by source; `summarize_structure()` keyed by pointKind |
| `tests/test_ep_selector.py` | Anchors-based BFS collects correct operator IDs; **parity test**: anchors-based produces same confidences as context-based on `licensing-decision-compare` (0.906 / 0.8875 / 0.794); max_nodes cap + warning |
| `tests/test_query_suggestions.py` | Levenshtein on silent-empty results; threshold filtering; no suggestions for non-kind queries |
| `tests/test_suggest_entry_points.py` | `kind_filter` filters by `pointKind`, not `context`; deprecation shim for legacy domain names |
| `tests/test_context_deprecation.py` | Warnings on all 7 MCP tools + SDK create_point/create_operator/file_decision + extractor; context NOT in created nodes |
| `tests/test_projection.py` | Old events with `context` replayed without writing `context`; `_apply_one` pops it; `_revise_point` no longer sets it |

### Phase 2 Tests

| Test File | What it verifies |
|-----------|-----------------|
| `tests/test_remove_context_migration.py` | Pre-migration count > 0; post-migration count = 0; idempotent |
| `tests/test_context_removal.py` | Meta-grep: 0 `context` params in SDK/MCP/hosted API signatures; 0 context properties in graph |
| All existing tests (~30 files) | Updated to use replacement mechanisms; all pass |

### EP Selector Parity Test (Critical)

```python
# Phase 1: prove anchors-based EP matches context-based EP
def test_ep_parity_licensing_decision():
    """Anchors-based EP on licensing-decision subgraph matches old context-based."""
    # Old: compute_confidence(context="licensing-decision-compare")
    old_result = sdk.compute_confidence(context="licensing-decision-compare")
    
    # New: find all anchors in that subgraph, BFS from them
    anchors = sdk.query(kind="decision")  # or specific anchor IDs
    new_result = sdk.compute_confidence(anchors=anchors, max_hops=3, direction="both")
    
    assert old_result["confidences"]["claim-a"]["mean"] == pytest.approx(0.906, abs=0.001)
    assert old_result["confidences"]["claim-b"]["mean"] == pytest.approx(0.8875, abs=0.001)
    assert old_result["confidences"]["claim-c"]["mean"] == pytest.approx(0.794, abs=0.001)
    assert new_result["confidences"] == old_result["confidences"]
```

---

## 5. Verification Plan

### Phase 1 Gate

- [ ] `git diff main -- tortoise/ | grep "context"` shows only deprecation warnings + compat shims — no NEW context writes
- [ ] Running the extractor on a fresh source produces Points with `context` NOT in properties
- [ ] MCP tools accept `context` but emit deprecation warning; `tortoise_create_point` result has no `context` property
- [ ] `list_pointkinds()` returns correct aggregate from graph
- [ ] `compute_confidence(anchors=...)` works; parity test passes
- [ ] All Phase 1 tests pass (`python -m pytest tests/ -v -k "references or discovery or ep_selector or query_suggestions or context_deprecation"`)
- [ ] `REMOVE n.context` migration script is tested and idempotent

### Phase 2 Gate

- [ ] `grep -rn "context" tortoise/ --include="*.py" | grep -v "#" | grep -v '"""' | grep -v "def " | wc -l` → 0 or comments only
- [ ] `MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)` → 0
- [ ] No `TypeError: unexpected keyword argument 'context'` in any test
- [ ] All tests pass (`python -m pytest tests/ -v`)
- [ ] Type checker passes (`mypy tortoise/`)

---

## 6. Acceptance Criteria

### Concrete, verifiable

1. **0 `context` params in any public API method** — `grep "context:" tortoise/sdk.py tortoise/mcp_server.py tortoise/hosted_api.py` returns 0
2. **0 `context` properties in graph** — `MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)` → 0
3. **All tests pass** — `python -m pytest tests/ -v` green
4. **EP parity** — Anchors-based `compute_confidence` produces same confidences as old context-based on licensing-decision subgraph
5. **`list_domains()` deleted** — `grep "list_domains" tortoise/` returns 0 (except comments)
6. **Query suggestions on silent-empty** — Empty `tortoise_fts_query("nonexistent_kind")` response includes `suggestions` field
7. **`kind_filter` filters by `pointKind`** — `suggest_entry_points("test", kind_filter="statement")` filters by `n.pointKind`
8. **`context_multipliers` dead code removed** — `grep "context_multipliers" tortoise/weights.py` returns 0
9. **`analyze.py` injection fixed** — No string interpolation in Cypher query construction; parameterized
10. **Skills updated** — `grep "context" skills/how-to-use-tortoise/SKILL.md skills/tortoise-file-finding/SKILL.md` → 0 (in code examples)

---

## 7. Runtime Prerequisites

| Prerequisite | Status | Notes |
|-------------|--------|-------|
| #86 (directional IMPL) | Required before Phase 1 | Direction semantics for `compute_confidence(direction=...)`; IMPL is directional per #86 |
| #99 (test-guard) | Merged | Protects graph during migration testing |
| Graph restored state | Required | 4,546 pts carry context; parity test runs against restored licensing-decision subgraph |
| #85 (dreaming) | Separate | SubgraphSelector class not built here; extract inline from sdk.py when #85 lands |
| v3.0 §3.2-3.3 references | Done | Spec'd in ratified docs/ONTOLOGY.md (extractedFrom + references, layered provenance) |

---

## Rejected Alternatives (Documented)

| Alternative | Rejection Reason |
|------------|-----------------|
| Context nodes + `scoped_by` edges | Reifies the deleted concept; violates deletion direction — we're removing `context`, not relocating it to a new node type |
| Big-bang single PR | Unproven EP selector + silent MCP break = worst risk profile; phased approach lets each mechanism bake independently |
| Search-engine `start_nodes` reuse | Needs an unbuilt search-engine feature (`start_nodes` filtering); adds coupling to search subsystem for EP subgraph selection |
| New `SubgraphSelector` class | YAGNI; extract from sdk.py when #85 dreaming lands; inline BFS suffices for Phase 1 |
| Migrate context values to new fields | User decision: NO value migration. 4,546 pts keep `context` until Phase 2 REMOVE. No mapping table. |

---

## 8. Verification Cycle 2 — Controller Fixes (2026-08-05)

The solution-verify gate found 1 P0 + 8 P1s. All fixed below — these are MANDATORY additions to the plan.

### 8.1 FIXED (P0): EP parity test — redesign

**Original flaw:** anchors = 3 options, hops=2-3, direction="incoming"/"both" does NOT reproduce old context-based extraction semantics (`MATCH (op)-[r:IMPL|NAND]->(c {context:$ctx})` = operators targeting ANY context point). Also used unregistered kind "decision".

**Correct design:**
```python
def test_ep_parity_licensing_decision():
    # Old semantics: every operator targeting ANY non-operator point in the context
    ctx_points = sdk.query(context="licensing-decision-compare")  # Phase 1 still accepts context
    anchors = [p["id"] for p in ctx_points if not p.get("is_operator")]
    assert len(anchors) >= 31  # 3 options + 7 criteria + 21 findings (verify all found)

    result_old = sdk.compute_confidence(context="licensing-decision-compare")
    result_new = sdk.compute_confidence(anchors=anchors, max_hops=1, direction="incoming")

    # max_hops=1 + direction="incoming" from ALL non-operator points == EXACT old query
    # (every operator targeting a context point is 1 incoming hop from that point)
    for pid, conf in result_old["confidences"].items():
        assert pid in result_new["confidences"]
        assert abs(conf["mean"] - result_new["confidences"][pid]["mean"]) < 0.001
    # Ranking preserved: AGPLv3-dual 0.906 > BSL-ep 0.8875 > SSPL 0.794
    assert sorted((c["mean"] for c in result_new["confidences"].values()), reverse=True)[0] > 0.85
```
**Rationale:** identical operator sets ⇒ EP convergence differs by < 0.001 (25-particle SVBP, same inputs). The 0.05 tolerance in the earlier draft was wrong (it masked operator-set divergence). If operator sets differ, NO tolerance helps — investigate the BFS, don't widen tolerance. Fallback plan: if `max_hops=1 incoming` from all anchors misses operators (e.g., finding↔finding truth edges at distance 2 from any anchor), add `direction="both"` for the operator→operator leg only, and document the delta.

### 8.2 FIXED (P1): projection/entities.py — the actual write path

`projection/entities.py:29-41` MERGE sets `n.context=$context`. **Without patching this, stop-writes is false.** Add to Task 1.7: remove `n.context=$context` from SET clause + `"context"` from params in both Point and operator branches. This is THE critical file for Phase 1.

### 8.3 FIXED (P1): __main__.py CLI (33 hits)

Add Task 1.8/2.14: `tortoise list-contexts` subcommand (calls sdk.list_domains — deprecate in P1, remove in P2); `tortoise decide` `--context`/`--context-free` flags (P1: route --context → anchors-based internally + warn; P2: remove flags, anchors is default).

### 8.4 FIXED (P1): tortoise_client.py standalone CLI (23 hits)

Add Task: `--context` flag on query-visions/write-claim/write-point + `query_domain(context)` + `write_claims` context propagation. P1: accept+warn+route; P2: remove.

### 8.5 FIXED (P1): audit.py — second Cypher injection surface

`audit_graph(proj, context)` builds `n.context CONTAINS "{c}"` via string interpolation (lines 49-61) — same injection class as analyze.py. Add Task: re-key audit to pointKind/subgraph filtering, parameterized. `AuditResult.context` field → descriptive label.

### 8.6 FIXED (P1): graph-scripts sweep (15+ files)

decide.py (18), audit_graph_deep.py (16), audit_graph.py (15), auto_discovery_cycle*.py (8+), fix_670*.py, add_convergence_evidence.py, smoke_test.py, file_pricing_decision.py, cost_control_cycle*.py. Add Task 2.15: audit all; migrate active scripts (decide.py, audit_graph*.py) to anchors/pointKind; mark historical one-shot scripts frozen (already ran; context dependency inert).

### 8.7 FIXED (P1): create_point dedup logic breaks between phases

`sdk.py:205-218` dedup matches `content_hash AND context` — after P1 stops writing context, new points match `context IS NULL` while old points have context values → dedup misses, duplicates created. Fix: P1 removes the context branch from dedup (match content_hash alone) — dedup becomes phase-independent.

### 8.8 FIXED (P1): TORTOISE_PHASE2 guard insufficient

Env var gates only the migration, not code removal. Add: (a) migration preflight refuses if `grep -rn "n\.context" tortoise/ --include="*.py" | grep -v test_` returns > 0; (b) CI check on Phase 2 branches: same grep = 0 before merge; (c) test asserting `create_point(context=...)` raises TypeError only when TORTOISE_PHASE2=1.

### 8.9 FIXED (P2): "no value migration" vs backfill tension

Clarify: Task 1.1 backfill operates on **extractedFrom edges only** — never fabricates Sources from context VALUES. Points without extractedFrom are not backfilled (provenance gap pre-dates this change). Keeps the no-migration promise clean.

### 8.10 FIXED (P2): MCP deprecation_warnings mechanism

Specify: SDK functions accepting deprecated `context` return a top-level `deprecation_warnings: list[str]` key in their result dict; `_safe()` passes through. Additive, JSON-RPC-safe (result shape unconstrained). Affects 7+1 MCP tools (incl. tortoise_analyze at mcp_server.py:652 — the 8th tool with context).

### 8.11 ADDED (P2/P3 sweep — from verifier findings)

- `sdk.check_structure` orphaned_draft message: n.context → n.pointKind (sdk.py:791-799)
- `sdk.traverse()` return: drop context field (4-field → 3-field dict)
- `sdk.resolve_id()` RETURN: remove n.context (sdk.py:317)
- `projection/entities.py:29,41` — covered in 8.2
- `projection.py:766` backup property enumeration: remove "context" from tuple
- `tortoise_list_domains`/`tortoise_list_contexts` MCP wrappers → replace with list_pointkinds/list_sources
- `tortoise_get_point` return shape: stop returning context after P2
- memory_scope.py:11 docstring — cosmetic, exclude from grep gate
- test_tortoise_client.py in prod tree — include in test sweep

### 8.12 Rejected alternatives — "when this would have been better" (P2)

| Rejected | When it WOULD have been better |
|----------|-------------------------------|
| Context-node reification (B3) | If the goal were to preserve context semantics with richer structure (hierarchy, permissions) rather than delete it |
| Big-bang single PR | If the EP selector were already proven AND MCP clients had a deprecation window |
| Search start_nodes reuse | If tortoise_search already supported anchor traversal (it doesn't) |
| SubgraphSelector class | If #85 dreaming needed the same traversal NOW (extract later) |


### 8.13 FIXED (P1, cycle-3): api.py Phase 2 removal + resolution-event trigger

- **Task 2.16 (new):** remove `context` from `tortoise/api.py` signatures: `_point` (:57), `add_point` (:110), `add_operator` (:121), `revise_point` (:175).
- **`api.py:116` behavior trigger:** `if context == "resolution-event": compute_grounding()` → re-key to `kind == "resolution-event"` (pointKind check).
- **Positional callers to update:** `tortoise/ingest.py:224` and `graph-scripts/resolve-github.py:81` pass `"resolution-event"` positionally — change to keyword `kind=`.
- Add `ingest.py` explicitly to the plan's file list (it's in tortoise/, not graph-scripts — Fix 8.6's sweep doesn't cover it).

### 8.14 FIXED (P3, cycle-3): weights.py query cleanup

- Task 2.7 extended: remove `o.context` from Cypher RETURN (weights.py:14) AND from the unpack tuple (:23) — keeps the grep-gate clean after context_multipliers deletion.

### 8.15 ADDED (residual, cycle-3): parity test operator→operator assertion

- Add to the §8.1 parity test: assert no operator→operator edges exist in the subgraph (or document operator-count delta if they do). Currently 0 such edges globally — guards against future meta-operators silently breaking the 1-hop parity.


---

## 9. Parallel Review Gate — Controller Fixes (2026-08-05, cycle 4)

Four parallel reviewers found the deepest issues yet. ALL fixes mandatory. The single most important is 9.1 (a structural flaw in the phased design).

### 9.1 CRITICAL (P0): Phase-1 semantic trap — stop-writes vs shim-reads

**The flaw:** P1 stops WRITING context, but P1's deprecation shims still READ context (`compute_confidence(context=X)` maps context→anchors via `MATCH (n {context:$ctx})`; `query(context=X)` filters `n.context`). New points created in P1 have no context → shims find zero anchors → EP returns empty confidences, queries return empty for new data. Skills that create-then-query (tortoise-file-finding, decision-comparison) silently break.

**The fix (ordering change in P1):**
1. **Skills migrate FIRST** (Task 1.6 reordered before stop-writes): update how-to-use-tortoise + tortoise-file-finding to the new patterns (no context in create, use extractedFrom + anchors) BEFORE any stop-write lands
2. **Deprecation shim maintains in-session mapping:** when `create_point(context=X)` is called in P1, record `context → [point_ids]` in an in-memory session map (per SDK instance); `query(context=X)`/`compute_confidence(context=X)` first check the session map, then fall back to graph `MATCH n.context`. This preserves create-then-query semantics within a session even though context isn't persisted.
3. **Session-scoped map is dropped at Phase 2** (no persistence — P1-only compat).

### 9.2 (P0): MCP tools must expose the NEW params (not just remove old)

- `tortoise_compute_confidence` gains `anchors: list[str] | None, max_hops: int = 2, rel_filter: str = "IMPL|NAND", direction: str = "incoming"` — NEW params in P1 alongside deprecated `context`
- NEW MCP tools: `tortoise_list_pointkinds`, `tortoise_list_sources` (P1)
- `tortoise_analyze` gains `anchor_ids` param (P1)
- `tortoise_file_decision`: `context` becomes optional `str | None = None` in P1 (it's REQUIRED today — mcp_server.py:451)

### 9.3 (P0): summarize_structure re-key moves to Phase 1

Gate counting (sdk.py:807-828, `tortoise-wf-gate0..4` hardcoded contexts) breaks the moment P1 stops writing context. Re-key to pointKind in Task 1.2 (P1), not P2. Gate kinds already have distinct pointKinds (useCase, jobToBeDone, userJourney, workflow, requirement).

### 9.4 (P0): references edge needs its own API

`_link_source(point_id, source_ref, source_kind)` can't create Source→entity references (no entity knowledge at that call site). Split:
- `_link_source` stays extractedFrom-only
- NEW `link_source_to_entity(source_url, entity_name, entity_label)` — called by connectors/extractors that know the entity
- Backfill re-scoped: operates on extractedFrom + about* edges (Source → Point → about* → entity), NOT context values
- This matches v3.0 §2.4 semantics (references: Source → Entity where Entity = Document|Event|Object — Action dissolved in v3.0, see §10.3)

### 9.5 (P1): parity test must be self-contained

Build a synthetic epistemic subgraph in the isolated test graph (conftest graph): 3 claims + 5-6 operators with known structure + baselines. Run old vs new, assert parity <0.001. The 0.906/0.8875/0.794 values become documentation, NOT a test dependency. (The #99 isolation guard forbids connecting to the restored licensing data.)

### 9.6 (P1): deprecation warnings — dual channel

`_safe()` wrapper in mcp_server.py prints each deprecation_warning to stderr BEFORE returning (visible in agent's text stream) AND keeps the result-dict key (programmatic). Agents see the warning in context, not just JSON.

### 9.7 (P1): migration safety — snapshot + cross-subgraph parity + rollback

Before `REMOVE n.context`:
1. FalkorDB BGSAVE snapshot (or Cloud backup) — documented restore procedure in script header
2. Pre-migration scan: for a random sample of 50 context subgraphs (not just licensing), compute EP old-vs-new, report any operator-set delta >0. Block migration on unexplained deltas.
3. Write `data/migrations/2026-08_context_removal_audit.json` = {context_value: [point_ids]} BEFORE removal (information-preserving sidecar — Agent 2 P2-2)

### 9.8 (P1): dedup must include pointKind

Fix 8.7's content_hash-only dedup is too broad (statement vs criterion with same text dedup to one). Dedup condition: `content_hash AND pointKind`. Phase-independent.

### 9.9 (P1): session_continuity moves to P1

`session_continuity.py` (context=self.session_id write+read) migrates to `wing`/`session_id` property in P1 Task 1.6 — NOT deferred to P2 Task 2.10. Test: session A writes, session B reads, verify continuity.

### 9.10 (P2): BFS direction semantics — per-edge-type

Specify: BFS traverses IMPL edges per `direction` flag; NAND edges ALWAYS bidirectionally (symmetric). Test: subgraph with NAND edge where anchor is the source, `direction="incoming"` must still traverse NAND. rel_filter is a filter on which types to include, not a uniform direction override.

### 9.11 (P2): P2 grep gate → AST-based check

Replace grep with AST parse: no function/method param named `context`, no Cypher string containing `n.context`/`.context`, no dataclass field named `context` in tortoise/*.py (tests exempt, manual verify).

### 9.12 (P2): CLI replacements

P1 adds `tortoise list-kinds` + `tortoise list-sources` subcommands; `tortoise list-contexts` emits deprecation pointing to them; P2 removes list-contexts.

### 9.13 (P2): v3.0 §5 heading + minimal spec

Add `## §5 Provenance Model` [TODO] heading to docs/ONTOLOGY_v3.0_proposal.md with minimal spec: references edge (Source→Entity, direction, no properties initially). Gates Task 1.1's references implementation against drift. (Agent 3 ISSUE-2/7)

### 9.14 (P3): decision rationale section

Add §10 "Decision Rationale — why delete, not fix naming": (1) 4 conflated concerns in 1 field proven by code (list_domains=discovery, weights=EP, suggest=namespace, extractor=provenance — same string, 4 jobs); (2) injection surfaces in analyze.py/audit.py fixed by removal; (3) the 4 replacements are already partly built (pack_registry, source provenance); (4) keeping context = maintaining TWO systems forever; (5) original "fix naming" framing was written before the codebase analysis showed the conflation. (Agent 4 P3-9)

### 9.15 (P3): extractor context-split documented

P1 known limitation: context-scoped queries return pre-P1 but not post-P1 points. Document + add `query(source_id=...)` alternative using extractedFrom traversal so callers can migrate early. Test: source-based query returns both pre and post-P1 points.

### 9.16 (P3): hosted_api P1 compat shim

`list_points` context filter: P1 tries `n.context` (old) then `n.wing`/`n.pointKind` (new); `PointResponse.context` field marked deprecated in P1.

### 9.17 (P1): #86 coupling decision

Option (b) per Agent 3: Phase 1 uses `direction="both"` for all BFS (direction-agnostic, works pre-#86); Phase 2 tightens to directional semantics after #86 lands. Decouples P1 from #86. The parity test uses direction="both" + max_hops=1 from all non-operator anchors (direction-agnostic equivalence).


---

## 10. Ontology v3.0 Ratification Alignment + Re-Review Integration (2026-08-05, cycle 5)

**REGRESSION GUARD:** Main advanced during planning: v3.0 was RATIFIED (#7869) and moved to the tortoise repo as `docs/ONTOLOGY.md`; `docs/ONTOLOGY_v3.0_proposal.md` was DELETED (merged into canonical); the Action entity was DISSOLVED (5 types: Subject/Object/Point/Event/Source); the FalkorDB sidecar was removed (Cloud-only, confirming #101). The plan was checked against the LATEST ontology (`git log -1 = 0f9e6a2`) and the new codebase. All plan references below supersede earlier sections where they conflict.

### 10.1 (P0) Fix all ontology doc references — proposal file is DELETED

The plan references `docs/ONTOLOGY_v3.0_proposal.md` in multiple places (§7 prerequisites, 9.13, 9.14). **That file no longer exists on main** (merged into `docs/ONTOLOGY.md` at 31a1dd6, then rewritten at 0f9e6a2).
- ALL references to `docs/ONTOLOGY_v3.0_proposal.md` → `docs/ONTOLOGY.md` (canonical, ratified)
- §9.13 (add §5 heading) is **OBSOLETE**: `references` is already spec'd in canonical v3.0 §3.2-3.3 (`extractedFrom` Point→Source; `references` Source→Entity; layered provenance `(Point)-[:extractedFrom]->(Source)-[:references]->(Entity)`). Replace 9.13 with: "Task 1.1's references implementation is gated by the RATIFIED spec at docs/ONTOLOGY.md §3.2-3.3 — no proposal edit needed."

### 10.2 (P0) `context` IS in the ratified v3.0 — deletion requires an ontology AMENDMENT

Canonical `docs/ONTOLOGY.md` §4.1 Point metadata still lists: `| context | string | — | Namespace context |`. The plan deletes this field.
- **NEW TASK 0.0 (prerequisite, before Phase 1):** amend ratified v3.0 — remove the `context` row from `docs/ONTOLOGY.md` §4.1, add a migration note ("context removed in #49 — provenance via extractedFrom/references, namespace via pack pointKinds, EP scoping via anchors"). This is a RATIFIED-doc amendment (v3.0 → v3.0.1 or v4.0-note), human-approved per the ontology governance gate.
- The amendment and the code deletion must reference the same issue (#49) to keep them linked.

### 10.3 (P0) Action dissolved — references Entity range fix

Canonical v3.0 §3.2-3.3: `references: Source → Entity` where Entity = **Document | Event | Object** (Action no longer exists). The plan's §9.4 says "Entity = Document|Event|Object|Action" — **Action must be removed**. Task 1.1's `link_source_to_entity` and the backfill must target Document/Event/Object only.

### 10.4 (P0) §3 task list must integrate ALL §8/§9 fixes (re-review cycle-2 finding)

The re-review confirmed: §3 (executable task list) was never updated with ~80% of §8/§9 fixes. A fresh agent following §3 alone misses 12+ work areas. **This section authorizes a full §3 rewrite.** The consolidated Phase 1 task list (supersedes §3):

**Phase 0 (prerequisites):**
- 0.0: v3.0 amendment (10.2) — remove context from §4.1, human-approved
- 0.1: Verify #86 state (directionality); P1 uses direction="both" if not merged (9.17)

**Phase 1 (build + migrate + stop-writes), ORDERED:**
- 1.0: **Skills migrate FIRST** (9.1): how-to-use-tortoise + tortoise-file-finding to new patterns (extractedFrom, pack pointKinds, anchors-based EP) — MUST precede 1.5
- 1.1: references edge — RATIFIED spec (10.1): `link_source_to_entity(source_url, entity_name, entity_label)` NEW API (9.4); backfill via extractedFrom+about* only (9.4); Entity range Document|Event|Object (10.3)
- 1.2: enumeration surfaces — list_pointkinds()/list_sources()/list_namespaces() (sdk) + re-key summarize_structure to pointKind **IN P1** (9.3) + MCP wrappers tortoise_list_pointkinds/tortoise_list_sources (9.2) + CLI tortoise list-kinds/list-sources (9.12)
- 1.3: compute_confidence(anchors, max_hops, rel_filter, direction) — direction="both" default in P1 (9.17); MCP tool gains anchors/max_hops/rel_filter/direction (9.2); tortoise_analyze gains anchor_ids (9.2); per-edge-type BFS direction, NAND always symmetric (9.10)
- 1.4: query suggestions — Levenshtein on kind names; kind-valid-but-empty hint (Agent2 P2-1)
- 1.5: **stop-writes** — SDK/MCP/api.py/extractor/session_continuity(9.9)/diary: accept context, DON'T write; in-session context→anchor map with UNION semantics (9.1, Agent2 P1-5); dedup content_hash AND pointKind (9.8) BEFORE stop-writes; api.py:116 resolution-event trigger re-key (8.13); ALL 11 MCP tools wrapped (Agent2 P1-3: create_point, query, paginated_query, search, compute_confidence, calibrate_summary, create_operator, file_decision, list_domains, list_contexts, analyze); dual-channel deprecation (9.6); query/search/paginated_query gain kind param (Agent2 P1-1)
- 1.6: projection version gate + entities.py MERGE fix (8.2, 9.1)

**Phase 2 (remove + migrate), ORDERED:**
- 2.0: **Pre-migration safety** (9.7): BGSAVE snapshot, 50-subgraph parity sample (exclude P1-created subgraphs — Agent2 P2-2), audit sidecar data/migrations/2026-08_context_removal_audit.json
- 2.1: REMOVE n.context migration (TORTOISE_PHASE2=1 + grep/AST preflight 9.11)
- 2.2-2.16: read-path removal (SDK/MCP/hosted/taxonomy/search/weights/grounding/analyze/audit/session_continuity/CLI/client/graph-scripts/api.py) — per §8.3-8.6, 8.13, 8.14 + docs/ sweep (Agent2 P2-2)
- 2.17: docs/ + skills final sweep; v3.0 §4.1 already amended in 0.0

**MCP tool count corrected:** 11 tools touch context (Agent2 P1-3) — the earlier "7" counts were wrong. Task 1.5 covers all 11.

### 10.5 (P0) Parity test — reconcile 3 specs into ONE (Agent1 P0-2)

Delete the §4 code block and §8.1's licensing-specific assertions. Canonical test (from §9.5): **self-contained synthetic subgraph** in the isolated test graph — 3 claims + 5-6 operators + baselines, run old-vs-new, assert <0.001. Uses `direction="both"`, `max_hops=1`, anchors = all non-operator points of the synthetic subgraph. Include the proof paragraph (Agent2 P2-2): at max_hops=1 from non-operator anchors, IMPL edges are only traversable incoming (non-operators aren't IMPL sources) and NAND edges connect operators (unreachable at hop 1) → direction="both" ≡ direction="incoming" ≡ old `(op)-[:IMPL|NAND]->(c {context})` query. The 0.906/0.8875/0.794 values become docstring documentation, NOT assertions.

### 10.6 (P1) session map semantics — UNION (Agent2 P1-5)

In-session context→anchor map: query checks map FIRST then UNIONs with graph MATCH results (dedupe by point ID). Exclusive fallback would hide pre-P1 points. Document cross-agent map sharing as a known P1 limitation (single long-lived MCP server process).

### 10.7 (P1) in-session map + MCP statelessness (Agent2 cycle-2 #1)

MCP tools are per-call (no SDK instance persistence across calls in a stateless proxy). The in-session map works for SDK-in-process callers (CLI, scripts) but NOT across stateless MCP calls. Resolution: the MCP server maintains the map at the SERVER level (one TortoiseSDK instance per server process, as today — mcp_server.py module-level sdk) — map is server-lifetime, shared across calls from the same server. This is exactly the cross-agent sharing noted in 10.6. Document both.

### 10.8 (P2) direction default consistency (Agent1 P0-3)

MCP compute_confidence default `direction="both"` in P1 (matching 9.17); change to `"incoming"` in P2 after #86. State the P1→P2 default change explicitly.


---

## 11. Final Gate Amendments (2026-08-05, cycle 6)

### 11.1 (P0) Task 0.0 governance — ontology amendment approval mechanism

The v3.0 amendment (remove `context` from docs/ONTOLOGY.md §4.1) is **ontology-governance work, separate from #49's code deletion**:
- **File a SEPARATE issue** under epistemic-team ownership: "Amend ratified v3.0 §4.1: remove context field (per #49)" — linked as a #49 blocker
- **Approval:** human PR review with explicit ontology-approval (the canonical ontology governance gate: "any edit requires explicit human approval — PR with proposed diff, review from organisation-design-team")
- **Commits:** the ONTOLOGY.md amendment commit is SEPARATE from code-deletion commits, so it's reviewed independently
- #49 Phase 1 does NOT start until the amendment is approved (or the plan documents that code can proceed with the field-deprecation, amendment lands in parallel)

### 11.2 (P1) projection.py is DEAD CODE — confirmed, no fix needed

Import test on new main (0f9e6a2): `tortoise.projection` resolves to `projection/__init__.py`. The standalone `tortoise/projection.py` (34KB, 13 context hits) is unreachable dead code. **No action needed for #49 stop-writes** — the active module is the package. Optional hygiene: delete `projection.py` in a separate cleanup PR (out of #49 scope).

### 11.3 (P2) Session-map roundtrip test added

Add to Phase 1 tests: `test_session_map_roundtrip.py` — (1) create point with context="test-domain" in same SDK instance → query(context="test-domain") returns it via session map despite context not persisted; (2) UNION test: pre-existing context point (graph MATCH) + newly created (session map) both returned, deduped by ID. This closes the loop on the §9.1 semantic-trap fix.

### 11.4 (P2) Opaque reviewer cross-references cleaned

§10.4-10.8 "Agent1/Agent2" parentheticals are session-ephemeral. Add note: "All §10 items reference the cycle 4-6 parallel review findings; the work descriptions are self-contained. Review provenance available in session logs, not required for execution." (Full removal deferred — cosmetic, not blocking.)

### 11.5 (P3) Line-number polish

- §1 "analyze.py:320" → "analyze.py:321" (off-by-one)
- §8.11 "memory_scope.py:11" — file doesn't exist on new main; remove reference (docstring reference was in the OLD projection.py; moot)

