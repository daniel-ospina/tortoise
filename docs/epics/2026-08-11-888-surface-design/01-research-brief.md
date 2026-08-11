---
title: "Stage 2 Research Brief — Epic #888: MCP + SDK Surface Design v2"
type: research
domain: engineering
doc_status: revised
subjects.team: epistemic-team
created: 2026-07-21
revised: 2026-08-12
revision: "adversarial review fixes — P0 lifecycle semantics, benchmark corrections, orphan re-verification, diagnostic protocol"
---

# Stage 2 Research Brief — Epic #888: MCP + SDK Surface Design v2

**Date:** 2026-07-21 (revised 2026-08-12)
**Repository:** daniel-ospina/tortoise
**Epic decision:** CONDITIONAL PROCEED (falsification-first, telemetry-gated)
**Status:** Research deliverable for Scope gate (HUMAN approval required)

---

## Executive Summary

Tortoise exposes **70 MCP tools** across 8 curation groups — 2.6× the largest shipping memory MCP server (Hindsight: 26 tools single-bank / 29 multi-bank [0.4], Mem0: 11 full-surface, Zep: 3–6). **43 of 140 SDK methods (~31%) have no MCP tool**, and 14 of those are purely internal plumbing never called from hosted_api, mcp_server, or selfhost_api. Six onboarding tools run once per team and then sit idle. Nine read-tool pairs differ only by pagination or tag-filter parameters.

**Domain-normalized comparison** adds critical context: stripping ~13 EP/governance/provenance tools (unique Tortoise capabilities with no competitor equivalent) yields ~57 operational-memory tools vs Hindsight 26 ≈ **2.2×** — material but less alarming than the raw 70 vs 26 figure. The ~13 epistemic tools are a unique capability, not surface bloat.

**The no-regret parallel workstream** (already authorized in the Stage 0 workstream) is the headline deliverable. The telemetry quality gate (≥500 tool calls, ≥10 distinct tools, ≥7 days) is the well-supported mechanism that determines whether COUNT actually degrades agent tool selection. The ~35-tool target sketched below is **preliminary-pending-telemetry** — the keep-and-fix fallback (~60 tools after no-regret consolidation + remove onboarding) remains the default path.

---

## Part 1 — Reference Benchmarks

### 1.1 Shipping MCP Memory Servers: Tool Counts and Patterns

| Server | Tool Count | Read/Write Ratio | Pattern | Discovery | Session/Memory Lifecycle |
|---|---|---|---|---|---|
| **Mem0 MCP** | 11 (full surface incl. entity/event lifecycle); core memory-only = 7 | 8R / 3W (2.7:1) | `verb_noun`: `add_memory`, `search_memories`, `get_memories` (core 7: add/search/get_memories/get/update/delete/delete_all) | One search tool (`search_memories`) with filters param | `delete_entities`, `list_entities`, `list_events`, `get_event_status` for lifecycle |
| **Zep Memory MCP** | 3 core, 6 with standalone graphs | 2R / 1W core | Collapsed from 8 → 3 in June 2026 by making `scope` a parameter | One search tool (`search_graph`) with `scope` param (auto/observations/thread_summaries/episodes) | `get_user_summary` for narrative state; no explicit session tools |
| **Hindsight MCP** | 26 (single-bank), 29 (multi-bank) [0.4 docs] | ~18R / 9W | `verb[_modifier]`: `retain`, `sync_retain`, `recall`, `reflect`, `create_mental_model`, `list_tags` | `recall` (unified search with types/tags/query_timestamp params) | `list_banks`, `create_bank`, `get_bank_stats` for multi-bank lifecycle |
| **Basic Memory** | 17 | ~12R / 5W | `verb_noun`: `write_note`, `read_note`, `search_notes`, `build_context` | `search_notes` with 11 filter params (search_type, note_types, entity_types, categories, tags, metadata_filters, after_date, etc.) | `list_memory_projects`, `create_memory_project`, `delete_project` for project lifecycle |
| **Supabase MCP** | 23–25 across 8 groups | ~16R / 7W | `verb_noun` by domain: `list_tables`, `execute_sql`, `get_logs`, `deploy_edge_function` | Feature-gated: tools disabled by `?features=` query param. Read-only mode via `?read_only=true` | `list_projects`, `create_project`, `pause_project`, `restore_project` for project lifecycle |

### 1.2 Convergence Patterns

1. **Tool count range:** Memory-specific MCP servers ship **3–30 tools**. The mode is 11–27. Tortoise at 70 is 2.6× the high-water mark.

2. **Zep's collapse (8→3) is the key reference precedent:** They collapsed `search_graph` + `search_graph_by_scope` + `get_facts` + `get_episodes` + `get_thread_summaries` + `get_observations` into a single `search_graph` tool with a `scope` parameter. Same pattern applicable to Tortoise's query/search/list surface.

3. **Unified search with parameterized filters:** Every benchmark server uses ONE primary search/query tool with filter parameters rather than separate tools per filter dimension (by tag, by kind, by date, paginated). Basic Memory's `search_notes` has 11 parameters. Hindsight's `recall` has 6. Mem0's `search_memories` has 4.

4. **Read-heavy surfaces:** All memory servers are read-biased (2:1 to 3:1 read/write ratio). Tortoise at 36R / 27W / 7I (1.1:1 including idempotent as write-like) is anomalously write-heavy for a memory server.

5. **Onboarding is NOT in the MCP surface:** None of the benchmark servers expose onboarding/setup tools via MCP. Supabase has project lifecycle tools, but those are administrative (create/pause/restore), not guided onboarding flows.

6. **Group/feature gating:** Supabase and Hindsight allow tool groups to be enabled/disabled per connection. Tortoise's `GROUP_BY_NAME` curation groups already support this pattern — but groups are not surfaced to the MCP client as a gating mechanism today.

### 1.3 Domain-Normalized Comparison

Tortoise's raw 70-tool count includes ~13 tools with no competitor equivalent: EP belief propagation (`compute_confidence`, `get_confidence`, `set_point_baseline`, `calibrate_summary`), governance (`get_governance`), provenance chain tools (`provenance`, `create_derivation`), structure integrity (`check_structure`, `summarize_structure`), epistemic operators (`create_operator`, `annotate_operator`, `mitigate_operator`), and the `dream` stabilization engine. These are unique capabilities, not surface bloat.

**Operational-memory baseline:** stripping those ~13 tools yields ~57 operational-memory tools. vs Hindsight 26 (single-bank) ≈ **2.2×**, vs Mem0 11 ≈ 5.2×, vs Zep 3 ≈ 19×. This is a more meaningful comparison for the "can agents navigate this surface?" question — the EP/governance tools are used in distinct, specialized workflows, not competing for attention during routine memory operations.

### 1.4 Key Takeaway

> The industry norm for agent-memory MCP servers is **3–29 tools** with a **unified search surface** (one query tool with rich filter parameters) and a **2:1–3:1 read/write ratio**. Tortoise at 70 tools (~57 operational) with 9 query/list/search tools and a near-1:1 read/write ratio is an outlier on every axis. **But outlier ≠ problem** — the unique epistemic capabilities explain part of the gap, and the telemetry quality gate determines whether the operational surface actually degrades agent tool selection.

---

## Part 2 — Tortoise Surface Catalog

### 2.1 Full Tool Inventory (70 tools, 8 groups)

Source: `tortoise/tool_registry.py` (static analysis of commit at clone time).

**Group: memory (17 tools: 11R / 5W / 1I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 1 | `tortoise_create_point` | I | Create a Point node | **Core** — every session |
| 2 | `tortoise_query` | R | Query by kind + property filters | **Core** — primary read path |
| 3 | `tortoise_paginated_query` | R | Query with SKIP/LIMIT pagination | **Redundant** — merge into query param |
| 4 | `tortoise_search` | R | Hybrid search with RRF fusion + EP annotation | **Core** — semantic retrieval |
| 5 | `tortoise_get_point` | R | Get single Point by ID | **Core** — point lookup |
| 6 | `tortoise_update_point` | W | Update properties on a Point | **Core** — every session |
| 7 | `tortoise_delete_point` | W | Delete a Point (destructive) | **Rare** — human confirmation required |
| 8 | `tortoise_supersede` | W | Replace old Point with new (CORRECTS edge) | **Periodic** — content refresh |
| 9 | `tortoise_invalidate` | W | Mark Point outdated with CORRECTS edge | **Periodic** — content refresh |
| 10 | `tortoise_list_tags` | R | List all Tag names with counts | **Periodic** — discovery/navigation |
| 11 | `tortoise_query_points_by_tag` | R | Query Points by tag | **Redundant** — merge into query tag param |
| 12 | `tortoise_list_pointkinds` | R | List all pointKinds with counts | **Periodic** — discovery |
| 13 | `tortoise_suggest_entry_points` | R | NL query → matching entities | **Core** — entity disambiguation |
| 14 | `tortoise_compute_confidence` | R | EP belief propagation | **Periodic** — EP workflows |
| 15 | `tortoise_get_confidence` | R | Get EP confidence for a claim | **Periodic** — EP workflows |
| 16 | `tortoise_set_point_baseline` | W | Set Beta prior for a claim | **Rare** — EP calibration |
| 17 | `tortoise_calibrate_summary` | R | Audit graph calibration state | **Rare** — EP calibration audit |

**Group: reasoning (10 tools: 9R / 1W / 0I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 18 | `tortoise_check_structure` | R | Gate 0→4 chain integrity | **Periodic** — structure audit |
| 19 | `tortoise_summarize_structure` | R | Count points per Gate | **Periodic** — structure overview |
| 20 | `tortoise_traverse` | R | Multi-hop graph traversal | **Core** — graph navigation |
| 21 | `tortoise_entity_profile` | R | Entity-centric BFS traversal | **Core** — graph navigation |
| 22 | `tortoise_analyze` | R | NL questions about the epistemic graph | **Core** — agent reasoning |
| 23 | `tortoise_taxonomy` | R | Count entities by node label | **Rare** — admin diagnostic |
| 24 | `tortoise_list_topics` | R | entityProfile lite for an entity | **Redundant** — entity_profile covers this |
| 25 | `tortoise_provenance` | R | "Who decided this?" chain | **Periodic** — audit/investigation |
| 26 | `tortoise_stale` | R | Find Points not updated in N days | **Periodic** — maintenance |
| 27 | `tortoise_dream` | W | Run EP stabilization | **Rare** — compute-heavy, operator-only |

**Group: graph (12 tools: 3R / 7W / 2I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 28 | `tortoise_create_operator` | I | Create an operator (IMPL, NAND, etc.) | **Core** — linking claims |
| 29 | `tortoise_annotate_operator` | W | Annotate operator epistemic dimensions | **Periodic** — quality marking |
| 30 | `tortoise_get_operator` | R | Get operator Point by ID | **Redundant** — get_point works |
| 31 | `tortoise_mitigate_operator` | I | Create mitigation modulating edge strength | **Periodic** — EP refinement |
| 32 | `tortoise_create_subject` | W | Create Subject node | **Periodic** — entity creation |
| 33 | `tortoise_create_object` | W | Create Object node | **Periodic** — entity creation |
| 34 | `tortoise_create_event` | W | Create Event node | **Core** — session logging |
| 35 | `tortoise_get_events` | R | Get recent Events by eventKind | **Core** — session awareness |
| 36 | `tortoise_create_edge` | W | Create edge between entities | **Periodic** — entity linking |
| 37 | `tortoise_get_entity` | R | Get any entity by ID/eventId/url | **Core** — entity lookup |
| 38 | `tortoise_update_entity` | W | Update any entity properties | **Periodic** — entity maintenance |
| 39 | `tortoise_delete_entity` | W | Delete any entity by ID | **Rare** — destructive |

**Group: sessions (6 tools: 5R / 1W / 0I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 40 | `tortoise_session_context` | R | "What happened last session?" | **Core** — every session start |
| 41 | `tortoise_get_session` | R | Get single session by ID | **Periodic** — session lookup |
| 42 | `tortoise_index_sessions` | W | Index session .md files | **Rare** — admin/onboarding |
| 43 | `tortoise_search_sessions` | R | Search indexed agent sessions | **Periodic** — session discovery |
| 44 | `tortoise_list_graphs` | R | List all graph names in DB | **Periodic** — namespace discovery |
| 45 | `tortoise_list_namespaces` | R | List installed pack namespaces | **Rare** — pack management |

**Group: sources (4 tools: 1R / 1W / 2I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 46 | `tortoise_list_sources` | R | All Sources with point counts | **Periodic** — source discovery |
| 47 | `tortoise_create_source` | I | Create Source node | **Periodic** — provenance |
| 48 | `tortoise_create_document` | I | Create Document node | **Periodic** — document ingestion |
| 49 | `tortoise_ingest_corpus` | W | Batch document ingestion from directory | **Rare** — admin, filesystem access |

**Group: journal (5 tools: 1R / 3W / 1I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 50 | `tortoise_checkpoint` | I | Session batch save with dedup | **Core** — session end |
| 51 | `tortoise_diary_write` | W | Write agent diary entry | **Core** — session reflection |
| 52 | `tortoise_diary_read` | R | Read recent diary entries | **Core** — session continuity |
| 53 | `tortoise_file_decision` | W | File decision with options+evidence | **Periodic** — decision recording |
| 54 | `tortoise_file_human_approval` | W | Record human approval artifact | **Periodic** — gate approvals |

**Group: admin (5 tools: 3R / 2W / 0I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 55 | `tortoise_status` | R | Graph health + entity counts | **Periodic** — health check |
| 56 | `tortoise_health` | R | Health check + basic metrics | **Redundant** — status covers this |
| 57 | `tortoise_team_create` | W | Create isolated team graph | **Rare** — provisioning, admin-only |
| 58 | `tortoise_get_governance` | R | Get entities owned by a Subject | **Periodic** — governance |
| 59 | `tortoise_backfill_v25` | W | Schema migration to v2.5 | **Rare** — migration, one-shot |

**Group: onboarding (6 tools: 2R / 3W / 1I)**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 60 | `tortoise_onboarding_demo_create` | I | Create demo epistemic graph | **Onboarding-only** — run once |
| 61 | `tortoise_onboarding_state` | R | Return onboarding progress | **Onboarding-only** — run once |
| 62 | `tortoise_onboarding_session_recording` | W | Toggle session recording | **Onboarding-only** — toggle once |
| 63 | `tortoise_onboarding_github_connect` | W | Initiate GitHub OAuth | **Onboarding-only** — run once |
| 64 | `tortoise_onboarding_github_index` | W | Start GitHub indexing | **Onboarding-only** — run once |
| 65 | `tortoise_onboarding_github_status` | R | Return GitHub connection status | **Onboarding-only** — run once |

**Also (not in GROUP_BY_NAME, defaulting to "memory"):**
| # | Tool | R/W/I | Purpose | Agent-need classification |
|---|---|---|---|---|
| 66 | `tortoise_assess_source` | W | Record agent's Source assessment | **Periodic** — source quality |
| 67 | `tortoise_get_source_reliability` | W | Derive Source reliability (0-1) | **Periodic** — source quality |
| 68 | `tortoise_set_source_tier` | W | Set Source credibility tier (T0-T4) | **Periodic** — source management |
| 69 | `tortoise_retract_point` | W | Tombstone-retract a Point | **Rare** — terminal state |
| 70 | `tortoise_events_poll` | R | Poll graph/claim events after cursor | **Core** — subscription/CDC |

> **Note:** Tools 66–68 (`assess_source`, `get_source_reliability`, `set_source_tier`) are NOT in the GROUP_BY_NAME map — they default to "memory" group. This is a **coherence bug**: they semantically belong in "sources" group. **Tool 69 (`retract_point`) and Tool 70 (`events_poll`) are ALSO ungrouped** — they default to "memory" via `GROUP_BY_NAME.get(t.name, "memory")` (tool_registry.py:807). `retract_point` should be in "memory" (semantically close to invalidate/supersede). `events_poll` is a subscription/CDC tool — its discoverability is at risk when hidden in the already-large "memory" group; consider surfacing it as a top-level tool or moving to "sessions".

---

### 2.2 Redundancy Clusters

#### Cluster A: Query surface (3 tools → 1 with merged params)

**`tortoise_query`**
```
query(kind=None, *, include_retracted=False, **filters) → list[dict]
```
**`tortoise_paginated_query`**
```
paginated_query(kind=None, skip=0, limit=20, *, include_retracted=False, **filters) → {results, total, hasMore}
```

**Consolidation:** `paginated_query` is a strict superset. Merge: add `skip`, `limit` params to `tortoise_query`. When both are None/0, return `list[dict]` (backward-compatible). When either is set, return `{results, total, hasMore}`.

**Evidence:** Both accept identical `kind` + `**filters`. `paginated_query` wraps the same Cypher path with `SKIP`/`LIMIT` appended. Zero semantic difference.

---

**`tortoise_query_points_by_tag`**
```
query_points_by_tag(tag: str) → list[dict]
```
vs:
```
query(kind=None, **filters) → list[dict]
```

**Consolidation:** Add `tag: str | None = None` parameter to `tortoise_query`. When set, append `TAGGED` edge traversal to the query. This is how every benchmark server handles it.

**Evidence:** `query_points_by_tag` is a one-line Cypher wrapper: `MATCH (p:Point)-[:TAGGED]->(t:Tag {name: $tag}) RETURN p`. `query()` already supports arbitrary `**filters` that get translated to property filters. Tag filtering is just another filter dimension.

---

#### Cluster B: Search surface (2 tools → 1)

**`tortoise_search`** (sdk_method: `tortoise_fts_query`)
```
tortoise_fts_query(query=None, kind=None, *, entity_type="point",
    min_confidence=0.0, order_by="relevance", limit=10,
    threshold=0.0, relationship_filter=None, traversal_path=None) → list[dict]
```
**`tortoise_search_sessions`**
```
search_sessions(query, *, agent=None, topics=None, after=None, before=None,
    limit=10, offset=0) → list[dict]
```

**Consolidation:** Add `entity_type="session"` to `tortoise_search` (already has `entity_type` param!). `search_sessions` is a session-specific wrapper around the same hybrid search engine with narrower filter params. Merge by expanding `entity_type` handling and adding `agent`, `topics`, `after`, `before` as optional params.

**Evidence:** Both route through the hybrid search engine (`tortoise_fts_query`). The session search just adds Event-specific metadata annotation.

---

#### Cluster C: List/Discovery surface (4 tools → 1 `tortoise_list`)

| Tool | Returns | Filter |
|---|---|---|
| `tortoise_list_tags` | `[{name, count}]` | Tag nodes |
| `tortoise_list_sources` | `[{url, sourceKind, points}]` | Source nodes |
| `tortoise_list_pointkinds` | `[{kind, count, pack}]` | Point kinds |
| `tortoise_list_namespaces` | `[{namespace, name, kind_count}]` | Pack namespaces |

**Consolidation:** Single `tortoise_list` tool with `entity: str` parameter (`"tags"`, `"sources"`, `"pointkinds"`, `"namespaces"`, `"graphs"`, `"topics"`). Each backend query is trivially different (one Cypher match each). This is the Zep pattern: 4 tools collapsed into 1 with a `scope`/`entity` parameter.

**Evidence:** All four are single-query list operations with no shared logic. The tool descriptions are identical in structure: "List all X with counts."

---

#### Cluster D: Status/Health/Structure diagnostic surface (5 tools → 2)

| Tool | Returns | Infrastructure |
|---|---|---|
| `tortoise_status` | `{connected, counts, total_entities}` | Direct FalkorDB queries (`sdk.status()` → `taxonomy()` + connectivity probe) |
| `tortoise_health` | `{status, falkordb, graph_size, last_ingest, errors, uptime}` | In-memory monitoring counters (`monitoring.metrics()` — ERROR_COUNT, uptime, last_ingest timestamps) |
| `tortoise_taxonomy` | `{Point: N, Event: N, ...}` | Direct DB query (`sdk.taxonomy()`) |
| `tortoise_check_structure` | `[{violation}]` | Chain integrity queries |
| `tortoise_summarize_structure` | `{gateN_*, total}` | Structure count queries |

**Evidence:**
- `tortoise_health` (mcp_server.py:867) calls `monitoring.metrics()` — an in-memory monitoring layer with counters for errors, uptime, and last ingest timestamps. `tortoise_status` (mcp_server.py:864) calls `sdk.status()` — direct FalkorDB queries for connectivity + taxonomy counts. **These are different infrastructure, not a subset relationship.** They are partially overlapping (both check FalkorDB connectivity, both return entity counts via taxonomy), but health adds in-memory metrics (errors, uptime) that status cannot produce.
- `tortoise_taxonomy` returns a flat dict of label counts. `tortoise_status` already returns `counts` via the same `taxonomy()` call. Merge `taxonomy` into `status`.
- `tortoise_check_structure` and `tortoise_summarize_structure` are distinct operations (violations vs counts) but can be merged with a `detail: "summary" | "violations"` parameter.

**Consolidation target:** `tortoise_status` with `detail: "basic" | "full"` param. Basic = current status output (connected + counts). Full = adds health metrics (uptime, errors, last_ingest from monitoring layer) + taxonomy detail. `tortoise_check_structure` with `detail` param for summary vs violations.

---

#### Cluster E: Entity CRUD surface (4 tools → 1–2)

| Tool | SDK Method | Behavior |
|---|---|---|
| `tortoise_create_subject` | `create_subject(name, subjectKind, **props)` | Creates Subject node via `_create_entity` |
| `tortoise_create_object` | `create_object(name, objectKind, **props)` | Creates Object node via `_create_entity` |
| `tortoise_create_event` | `create_event(name, eventKind, **props)` | Creates Event node + extracts `aboutSubject`/`aboutObject`/`aboutPoint`/`aboutDocument` from `**props` and creates corresponding `about*` EDGES to target entities (sdk.py:4277–4320) |
| `tortoise_create_document` | `create_document(title, documentKind, **props)` | Creates Document node via `_create_entity` |

**Evidence:** All four call `self._create_entity(label, id_val, props, event_type)` with only the label differing (Subject/Object/Event/Document). **However, `create_event` is NOT a thin wrapper** — it extracts `aboutSubject`, `aboutObject`, `aboutPoint`, `aboutDocument` from `**props` and creates `about*` graph edges (`(Event)-[:aboutSubject]->(Subject)`, etc.), including name-resolution for non-ULID values (sdk.py:4292–4320). The other three create-entity tools are thin wrappers with no edge-creation logic.

**Consolidation:** Single `tortoise_create_entity` with `entity_type: "subject" | "object" | "event" | "document"` — but the unified tool MUST preserve `create_event`'s about-entity edge wiring behavior when `entity_type="event"` and the corresponding about* props are present. This is a required feature, not optional sugar. Consolidation risk: entity types have different semantic meanings — agents may confuse them. **Preliminary-pending-telemetry:** only consolidate if agents show confusion between these tools.

---

#### Cluster F: Point lifecycle mutations (3 tools — keep separate; merge only invalidate+retract)

| Tool | SDK Method | Actual Code Behavior (verified sdk.py) |
|---|---|---|
| `tortoise_invalidate` | `invalidate_point(id, corrected_by_id)` (line 895) | Sets `outdated=true` on old Point + creates `CORRECTS` edge from new→old. Flags-only; NO edge transfer. Marks dirty roots for EP dreaming. |
| `tortoise_supersede` | `supersede_point(old_id, new_id)` (line 944) | Inlines outdated flag + CORRECTS edge (does NOT call `invalidate_point`). THEN: validates all edge types for safety (#329), transfers ALL operator edges (IMPL, NAND, hasPart) old→new preserving idx, transfers ALL structural edges (aboutSubject, aboutObject, aboutAction, aboutEvent, aboutPoint, aboutDocument, extractedFrom, wasDerivedFrom) old→new, deletes old edges. Emits `PointSuperseded` event (#548). Marks dirty roots. Returns `{invalidated, edges_transferred}`. |
| `tortoise_retract_point` | `retract_point(id)` (via mcp_server.py:712→sdk) | Sets `status='retracted'` — tombstone; Point stays in graph, excluded from default query surfaces. Terminal state. |

**Corrected evidence:** The ToolDefinition description (tool_registry.py:277) says "Equivalent to invalidate(old_id, new_id)" — this is a **description inaccuracy**, not a code fact. `supersede_point` inlines the invalidate logic (outdated flag + CORRECTS) but adds edge-transfer semantics that `invalidate_point` does NOT have. They share the first 2 writes (outdated flag, CORRECTS edge) but diverge fundamentally on edge transfer. The tool description should be corrected separately.

**Consolidation analysis:**
- `supersede` must remain a separate tool — edge-transfer semantics are qualitatively different from flag-only operations. Merging them would force agents to understand a `mode` parameter that toggles 80+ lines of edge-transfer logic.
- `invalidate` + `retract` could merge: both are flag-only operations on a single Point (`outdated=true` vs `status='retracted'`). A `mode: "invalidate" | "retract"` parameter is reasonable. But `retract` is terminal (requires `corrects` context); `invalidate` requires `corrected_by_id`. The parameter shapes differ.
- **Verdict:** Keep `supersede` separate (edge-transfer semantics). At most merge `invalidate` + `retract` into one tool with `mode` param (both flag-only). Mark any lifecycle consolidation as **high-risk pending telemetry evidence** that agents confuse these three operations.

---

#### Cluster G: Entity CRUD accessors (3 read tools → 1)

| Tool | SDK Method | Notes |
|---|---|---|
| `tortoise_get_point` | `get_point(id)` | Point-specific |
| `tortoise_get_entity` | `get_entity(id_val)` | Any entity by ID/eventId/url |
| `tortoise_get_operator` | `get_point(id)` + `is_operator` check | Same as get_point, adds validation |

**Evidence:** `get_operator` uses `get_point` under the hood. `get_entity` is a thin wrapper around `_get_entity`. All three are single-ID lookups.

**Consolidation:** `tortoise_get_entity` already handles any entity type. `tortoise_get_point` and `tortoise_get_operator` are redundant with it. The `get_operator` validation can be preserved by checking the entity type in `get_entity`.

---

### 2.3 SDK Orphan Triage (43 methods with no MCP tool)

Each orphan is classified by: (a) where it's used, (b) whether an agent needs it via MCP, (c) candidate action.

| # | SDK Method | Used In | Files | Agent Need? | Candidate Action | Confidence |
|---|---|---|---|---|---|---|
| 1 | `close` | sdk.py (cleanup) | 142 (test imports) | No | **KEEP SDK-ONLY** — lifecycle method, not a tool | HIGH |
| 2 | `ulid` | sdk.py, ingest.py, mining.py, ids.py, security.py, api.py, audit_events.py | 13 | No | **KEEP SDK-ONLY** — ID generation utility | HIGH |
| 3 | `test_guard` | sdk.py | 12 (tests) | No | **KEEP SDK-ONLY** — test-only gate | HIGH |
| 4 | `resolve_id` | sdk.py only | 1 | No | **KEEP SDK-ONLY** — internal ID resolution | HIGH |
| 5 | `capture_session` | hosted_api.py (2 refs), metering.py, tool_registry.py | 7 | No (REST-only) | **KEEP SDK-ONLY** — REST /v1/sessions endpoint, has dedup bug (#490) | HIGH |
| 6 | `delete_point` | sdk.py, mcp_server.py (2 refs, internal), tool_registry.py | 11 | **YES** — but tool uses `delete_point_wrapped` | **DEPRECATE** `delete_point` → `delete_point_wrapped` already wrapped for MCP. `delete_point` returns `bool`, `delete_point_wrapped` returns `dict`. Standardize on one. | HIGH |
| 7 | `create_or_update_point` | sdk.py only | 2 | **YES** — idempotent create | **EXPOSE** — agents need "create or update if exists" semantics. `create_point(dedup=True)` already does this. Add `dedup` param to tool description or expose as separate tool. | HIGH |
| 8 | `batch_create_points` | sdk.py, tests | 2 | **YES** — bulk operations | **EXPOSE** — `checkpoint` covers some bulk use but is session-scoped. Agents doing batch migrations need this. | MEDIUM |
| 9 | `topic_summarize` | sdk.py (not present in current main — only in worktree branches), topic_summarization.py | 6 (worktree refs only) | **YES** — topic summarization | **NOT WIRED in mcp_server.py** — grep verified; no function or reference. Expose-candidate if needed for agent-facing topic summaries. | MEDIUM |
| 10 | `compute_reputation` | sdk.py:4916 | 5 | **YES** — EP quality | **NOT WIRED in mcp_server.py** — comment-only reference at line 1097 ("compute_reputation at write time"), no function definition. Expose-candidate if EP quality tooling needed. | MEDIUM |
| 11 | `session_index_health` | sdk.py (not present in current main — only in worktree branches), __main__.py | 3 (worktree refs only) | **Rare** — admin diagnostic | **NOT WIRED in mcp_server.py** — not in current main sdk.py. Expose-candidate as admin tool if session indexing health becomes agent-facing. | LOW |
| 12 | `reconcile_sessions` | sdk.py, __main__.py | 3 | **Rare** — admin maintenance | **EXPOSE as admin tool** or **KEEP SDK-ONLY** | LOW |
| 13 | `complete_source` | sdk.py:5073 | 1 | **Periodic** — source enrichment | **NOT WIRED in mcp_server.py** — grep verified; no function or reference. Expose-candidate for source completion workflows. | MEDIUM |
| 14 | `create_derivation` | sdk.py:4624 | 2 | **Periodic** — provenance | **NOT WIRED in mcp_server.py** — grep verified; no function or reference. Expose-candidate for provenance linking. | MEDIUM |
| 15 | `link_source_to_entity` | sdk.py, projection/edges.py, projection/entities.py | 4 | **Periodic** — provenance linking | **FOLD INTO** `tortoise_create_edge` with predicate="sourcedFrom" | MEDIUM |
| 16 | `get_provenance_chain` | sdk.py | 2 | **Periodic** — audit | **FOLD INTO** `tortoise_provenance` (already a tool, but returns different shape) | MEDIUM |
| 17 | `get_org_structure` | sdk.py | 2 | **Periodic** — governance | **FOLD INTO** `tortoise_get_governance` or expose as standalone | LOW |
| 18 | `backfill_about_entities` | sdk.py, scripts/manifest.py | 3 | **Rare** — migration | **KEEP SDK-ONLY** — migration utility | HIGH |
| 19 | `migrate_teams_to_registry` | sdk.py | 2 | **Rare** — migration | **KEEP SDK-ONLY** — migration utility | HIGH |
| 20 | `list_relations` | sdk.py, pack_registry.py | 4 | **Rare** — pack inspection | **EXPOSE** or fold into `tortoise_list` with `entity="relations"` | LOW |
| 21–26 | `apikey_*` (6 methods) | sdk.py, mcp_auth.py, __main__.py, hosted_api.py | 2–8 each | **No** — auth infrastructure, not memory tools | **KEEP SDK-ONLY** — control-plane, handled by REST/internal endpoints | HIGH |
| 27–32 | `invitation_*` (6 methods) | sdk.py, supabase_control.py, hosted_api.py | 2–5 each | **No** — team provisioning | **KEEP SDK-ONLY** — control-plane | HIGH |
| 33–37 | `membership_*` (5 methods) | sdk.py, supabase_control.py, hosted_api.py | 2–8 each | **No** — team management | **KEEP SDK-ONLY** — control-plane | HIGH |
| 38–41 | `team_{get,list,update,delete}` (4 methods) | sdk.py, hosted_api.py (2 use team_delete), quota.py | 3–5 each | **No** — team management | **KEEP SDK-ONLY** — control-plane; only `team_create` is exposed as a tool (and it's admin-only) | HIGH |
| 42 | `graph_list` | sdk.py, supabase_control.py, hosted_api.py (4 refs) | 6 | **Periodic** — graph inventory | **EXPOSE** — `tortoise_list_graphs` already a tool but uses `list_graphs()` not `graph_list()`. Consolidate. | MEDIUM |
| 43 | `graph_count` | sdk.py, hosted_api.py (1 ref) | 4 | **Rare** — admin | **FOLD INTO** `tortoise_status` | MEDIUM |

**Summary (corrected after grep re-verification):**
- **14/43 orphans are control-plane/team/auth methods** → KEEP SDK-ONLY (no tool needed)
- **3/43 are internal utilities** (`close`, `ulid`, `test_guard`, `resolve_id`) → KEEP SDK-ONLY
- **4/43 are migrations** (`backfill_*`, `migrate_*`) → KEEP SDK-ONLY
- **0/43 are already wired in mcp_server but unregistered** — prior claim was FALSE. Grep verification of mcp_server.py shows: `topic_summarize` (no reference), `compute_reputation` (comment-only at line 1097, no function), `complete_source` (no reference), `create_derivation` (no reference), `session_index_health` (no reference). None are wired.
- **5/43 are SDK methods with no MCP tool that are expose-candidates pending need** (`topic_summarize` if brought to main, `compute_reputation` for EP quality, `complete_source` for source enrichment, `create_derivation` for provenance, `session_index_health` if brought to main)
- **6/43 should be folded into existing tools via params** (`link_source_to_entity`, `get_provenance_chain`, `get_org_structure`, `graph_count`, `delete_point`, `create_or_update_point`)
- **2/43 are bulk/convenience methods** (`batch_create_points`, `reconcile_sessions`) → EXPOSE or keep-SDK-only
- **1/43 is REST-only** (`capture_session`) → KEEP SDK-ONLY

> **Net new tool candidates:** ~5 tools (topic_summarize, compute_reputation, batch_create_points, complete_source, create_derivation). These would INCREASE the surface to ~75 tools — which strengthens the case for parallel consolidation.

---

### 2.4 Onboarding Tool Analysis

| Tool | Runs | Steady-State Need | Post-Onboarding Fate |
|---|---|---|---|
| `tortoise_onboarding_demo_create` | Once | None — demo graph already created | **RETIRE** — no-op after first call (idempotent) |
| `tortoise_onboarding_state` | Once/twice | None — onboarding complete | **RETIRE** — returns static post-completion |
| `tortoise_onboarding_session_recording` | Once | None — toggle set | **RETIRE** — setting persists |
| `tortoise_onboarding_github_connect` | Once | Rare (re-auth) | **RETIRE** — OAuth is a one-time flow |
| `tortoise_onboarding_github_index` | Once per org | Periodic if new repos added | **DEMOTE to admin** — background indexing, not agent-facing |
| `tortoise_onboarding_github_status` | Once/twice | None — connection verified | **RETIRE** |

**Decision:** All 6 onboarding tools should be **removed from the steady-state MCP surface**. The controlled removal path from the converged decision (cycle 4) applies:
- Remove at onboarding-completion signal, not at connect time
- Dedicated POST /v1/onboarding/* REST endpoints remain for the web onboarding flow
- MCP tools are the target for removal

---

## Part 3 — Root-Cause Hypothesis Framework

The telemetry (work item #0, issue #889) emits: `{tool_name, status: ok|validation_error|auth_error|exec_error, latency_ms}`.

### 3.1 Diagnostic Matrix

**Diagnostic protocol** — two-phase, cheapest-first:
1. **Fix descriptions first** (cheapest intervention): improve tool descriptions for the top 10 tools by validation-error rate. Re-measure after 3 days / 200 calls.
2. **If errors persist on the SAME tools** → NAMING is the root cause (confusing names, not unclear descriptions). Only then rename/alias tools.

This protocol avoids the confound: the 4-field schema cannot distinguish NAMING from DESCRIPTIONS in a single observation window — a tool with a bad name AND a bad description looks the same in telemetry. Fix the cheap one first, re-measure.

| Root Cause | Telemetry Signature | Metric Pattern | Confirmation Test |
|---|---|---|---|
| **COUNT** (too many tools) | Agent's first-tool-choice is wrong >30% of time; agent asks for non-existent tools; agent picks `paginated_query` when `search` was correct, or vice versa. | Tool-selection error is uncorrelated with any specific tool — spread across the surface. | **A/B test (primary signal):** serve curated 25-tool subset vs full 70-tool surface. If error rate drops significantly → COUNT. **Note:** `latency_ms` measures execution time, not model decision time — it is NOT a valid COUNT signal without `inter_call_latency_ms` (time between tool calls, measuring model "thinking"). Add `inter_call_latency_ms` as a schema extension proposal (issue #889 follow-up). |
| **NAMING** (inconsistent names) | `validation_error` spikes on specific tools with confusing names (e.g., `tortoise_invalidate` vs `tortoise_supersede` vs `tortoise_retract_point`). Agent passes wrong param because name doesn't match mental model. | Errors cluster on tools with naming issues (entity CRUD surface: `create_subject`/`create_object`/`create_event`/`create_document` vs generic `create_entity`). | **Phase 2 only (after descriptions fixed):** fix names (add aliases, improve names). If error rate drops → NAMING. |
| **DESCRIPTIONS** (unclear tool descriptions) | `validation_error` rate high on tools whose description is ambiguous about what params are required vs optional. Agent passes wrong params. | Errors on tools where description doesn't match actual signature (e.g., `tortoise_entity_profile` description says "BFS from entity node" but doesn't specify what entity ID means). | **Phase 1 (cheapest):** fix descriptions only. If error rate drops → DESCRIPTIONS. If errors persist on same tools → NAMING. |
| **STEERING** (skill/prompt doesn't guide agent) | Agent uses correct tools but in wrong sequence (e.g., creates Points without first calling `suggest_entry_points` for dedup). `exec_error` from business logic, not validation. | Errors are in tool SEQUENCING not selection. Agent knows which tool to call but doesn't know WHEN. | **Fix skill steering only** (update `how-to-use-tortoise` skill with decision trees). If error rate drops → STEERING. |

### 3.2 Composite Diagnosis

If the data shows:
- **High validation_error rate + spread across 40+ tools** → COUNT + DESCRIPTIONS both likely (surface is too large AND poorly described)
- **High validation_error rate + concentrated on 8–10 tools** → DESCRIPTIONS or NAMING (fix the specific tools — descriptions first, then names if needed)
- **Low validation_error rate + high exec_error rate** → STEERING (tools work but agent doesn't know the workflow)
- **Low error rate overall + high inter_call_latency** → COUNT (agent can handle 70 tools but spends too many tokens deciding). Requires `inter_call_latency_ms` telemetry (schema extension proposal, not in current telemetry).

### 3.3 Telemetry Quality Gate (from cycle 4 decision)

The gate requires **≥500 tool calls, ≥10 distinct tools invoked, over ≥7 days** before Stage 2 can conclude. If threshold unmet:
1. Extend window (option a)
2. Default to **keep-and-fix path** (option b) — fix docs + merge 9 overlaps + retire onboarding tools; no shrink

---

## Part 4 — Preliminary Design Direction

> **⚠️ EVERY claim in this section is preliminary-pending-telemetry.** The converged decision (cycle 4) requires that the telemetry quality gate be met before any design conclusion is finalized. This section describes the design IF the evidence supports shrinking. The keep-and-fix fallback (69 tools → ~60 after no-regret consolidation + remove onboarding) remains the default path.

### 4.1 Preliminary Target Manifest (~35 tools, 5 groups)

IF telemetry supports shrinking, the target is a **~35-tool surface** organized into 5 curation groups (down from 8). This follows the benchmark pattern of ~1 primary search/query tool per domain. **This target is preliminary-pending-telemetry** — the keep-and-fix fallback (~60 tools after no-regret consolidation + remove onboarding) remains the default path.

**Group: memory (10 tools) — the core agent surface**
| # | Tool | Consolidated From | R/W/I |
|---|---|---|---|
| 1 | `tortoise_create_point` | (unchanged, add explicit `dedup` param description) | I |
| 2 | `tortoise_query` | `query` + `paginated_query` + `query_points_by_tag` (new params: `skip`, `limit`, `tag`) | R |
| 3 | `tortoise_search` | `tortoise_search` + `tortoise_search_sessions` (new param: `entity_type="session"`, add session-specific filters) | R |
| 4 | `tortoise_get_entity` | `get_point` + `get_entity` + `get_operator` (unified entity lookup) | R |
| 5 | `tortoise_update_point` | (unchanged) | W |
| 6 | `tortoise_invalidate` | `invalidate` + `retract_point` (new param: `mode`). **`supersede` kept separate** — edge-transfer semantics are qualitatively different from flag-only operations. See Cluster F analysis. | W |
| 7 | `tortoise_delete_point` | (unchanged, destructive gate preserved) | W |
| 8 | `tortoise_suggest_entry_points` | (unchanged) | R |
| 9 | `tortoise_compute_confidence` | `compute_confidence` + `get_confidence` (add `mode="propagate"|"read"`) | R |
| 10 | `tortoise_set_point_baseline` | (unchanged) | W |

**Group: graph (8 tools)**
| # | Tool | Consolidated From | R/W/I |
|---|---|---|---|
| 11 | `tortoise_create_entity` | `create_subject` + `create_object` + `create_event` + `create_document` (new param: `entity_type`) | W |
| 12 | `tortoise_create_operator` | (unchanged) | I |
| 13 | `tortoise_annotate_operator` | (unchanged) | W |
| 14 | `tortoise_mitigate_operator` | (unchanged) | I |
| 15 | `tortoise_create_edge` | + merge `link_source_to_entity` as predicate | W |
| 16 | `tortoise_update_entity` | (unchanged) | W |
| 17 | `tortoise_delete_entity` | (unchanged) | W |
| 18 | `tortoise_get_governance` | + merge `get_org_structure` | R |

**Group: navigation (7 tools)**
| # | Tool | Consolidated From | R/W/I |
|---|---|---|---|
| 19 | `tortoise_traverse` | (unchanged) | R |
| 20 | `tortoise_entity_profile` | + merge `list_topics` | R |
| 21 | `tortoise_analyze` | (unchanged) | R |
| 22 | `tortoise_provenance` | + merge `get_provenance_chain` | R |
| 23 | `tortoise_list` | `list_tags` + `list_sources` + `list_pointkinds` + `list_namespaces` + `list_graphs` + `list_relations` (new param: `entity`) | R |
| 24 | `tortoise_status` | `status` + `health` + `taxonomy` + `graph_count` | R |
| 25 | `tortoise_check_structure` | + merge `summarize_structure` (new param: `detail`) | R |

**Group: sessions (7 tools)**
| # | Tool | Consolidated From | R/W/I |
|---|---|---|---|
| 26 | `tortoise_session_context` | (unchanged) | R |
| 27 | `tortoise_checkpoint` | (unchanged) | I |
| 28 | `tortoise_diary_write` | (unchanged) | W |
| 29 | `tortoise_diary_read` | (unchanged) | R |
| 30 | `tortoise_events_poll` | (unchanged — CDC/subscription surface, distinct from get_events) | R |
| 31 | `tortoise_get_events` | (new — historical event queries, distinct from events_poll CDC) | R |
| 32 | `tortoise_file_decision` | (kept standalone — atomic multi-Point creation; semantic mismatch with create_point) | W |

**Group: sources (4 tools)**
| # | Tool | Consolidated From | R/W/I |
|---|---|---|---|
| 33 | `tortoise_create_source` | (unchanged — provenance is core to remember→connect loop) | I |
| 34 | `tortoise_create_document` | (unchanged — first-class entity) | I |
| 35 | `tortoise_assess_source` | (unchanged — source quality) | W |
| 36 | `tortoise_set_source_tier` | + merge `get_source_reliability` as a detail param | W |

### 4.2 Tools NOT in the preliminary target (but preserved)

**Excluded from steady-state MCP surface, kept as SDK methods + REST endpoints:**
- 6 onboarding tools (removed at onboarding-completion)
- `tortoise_team_create` (provisioning, admin-only, REST endpoint preserved)
- `tortoise_backfill_v25` (migration, one-shot)
- `tortoise_ingest_corpus` (filesystem access, excluded from HTTP already)
- `tortoise_index_sessions` (filesystem access, admin)
- `tortoise_dream` (compute-heavy, operator-only per #329)
- `tortoise_calibrate_summary` (rare EP audit, folded into `compute_confidence` with `mode="calibrate"`)
- `tortoise_stale` (maintenance, agent doesn't need this during normal work)
- `tortoise_file_human_approval` (rare — folded into `create_point` with `pointKind="humanApproval"`)
- `tortoise_assess_source` + `tortoise_get_source_reliability` + `tortoise_set_source_tier` (source management — collocated with `tortoise_create_source` in "sources" group)

**`tortoise_file_decision` — KEPT, NOT folded into `create_point`:**
`file_decision` (sdk.py:1586) creates a decision Point + N option Points + M evidence Points + (N+M) IMPL edges atomically. Folding into `create_point(pointKind="decision")` loses atomic multi-Point creation. Either keep `file_decision` as a standalone tool or propose an explicit `batch_create_points` primitive that can express the decision+options+evidence+IMPL pattern. This is a **semantic mismatch**, not a surface reduction opportunity.

**Source tools cluster — resolved explicitly:**
The 4 source-related tools (`create_source`, `assess_source`, `get_source_reliability`, `set_source_tier`) are a coherent "sources" curation group. Resolution: keep them as a separate `sources` group (4 tools) rather than folding into the memory group. This follows the Supabase/Hindsight pattern of domain-specific groups. If source management is not needed by routine agent sessions, feature-gate the group. The preliminary target thus includes a 5th curation group (`sources`) bringing the target from 30 to ~35 tools. This is still within the benchmark range (Hindsight: 29).

**`tortoise_get_events` vs `tortoise_events_poll` — distinct tools:**
`get_events` (sdk.py:4470) returns historical Events filtered by `eventKind` — a simple query. `events_poll` is a CDC/subscription tool with cursor-based at-least-once semantics. Both are needed. Added to target manifest.

### 4.3 Consolidation Summary Table

| Consolidation | Tools Before | Tools After | Net Change |
|---|---|---|---|
| Query surface (query + paginated_query + query_by_tag) | 3 | 1 | -2 |
| Search surface (search + search_sessions) | 2 | 1 | -1 |
| List/Discovery surface (6 list_* tools) | 6 | 1 | -5 |
| Status/Health/Taxonomy (3 tools) | 3 | 1 | -2 |
| Structure diagnostics (2 tools) | 2 | 1 | -1 |
| Entity CRUD (4 create_* tools) | 4 | 1 | -3 |
| Point lifecycle (3 tools) | 3 | 1 | -2 |
| Entity accessors (3 tools) | 3 | 1 | -2 |
| Onboarding removal | 6 | 0 | -6 |
| **Total consolidation** | **32** | **8** | **-24** |

70 - 24 = 46. Accounting for ~5 new exposed orphans (topic_summarize, compute_reputation, batch_create_points, complete_source, create_derivation), 46 + 5 = 51, minus further consolidation (file_human_approval into create_point, source tools grouped separately, stale/calibrate/dream removed from MCP) + sources group kept distinct (+4) + file_decision kept (+1) + get_events added (+1) = **~35 tools**. This preliminary-pending-telemetry target is within the benchmark range (Hindsight multi-bank: 29).

### 4.4 No-Regret Actions (already authorized)

The Stage 0 parallel workstream authorized three no-regret actions that proceed regardless of telemetry outcome:
1. **Remove 6 onboarding tools** from steady-state MCP surface (at onboarding-completion signal)
2. **Fix 9 read-tool overlaps** (query+paginated_query+query_by_tag, search+search_sessions, list_* tools, status+taxonomy, check_structure+summarize_structure)
3. **Fix GROUP_BY_NAME coherence bugs** (assess_source/get_source_reliability/set_source_tier → "sources"; retract_point → "memory" or keep in "memory"; events_poll → surface explicitly or move to "sessions")

These are well-supported by static analysis and benchmark evidence, independent of telemetry. They are the **headline deliverable** of this research brief.

### 4.5 Rollback Safety (per cycle 4 decision)

| Change Type | Example | Removable Without Breakage? |
|---|---|---|
| **ADDITIVE** — new params | `skip`, `limit`, `tag` added to `tortoise_query` | ✅ Yes — old calls without params work unchanged |
| **ADDITIVE** — new tools | `tortoise_list` replacing 6 list tools | ✅ Yes — old list tools remain as aliases during deprecation |
| **DEPRECATION with grace** | `tortoise_paginated_query` → alias to `tortoise_query` | ✅ Yes — alias preserved ≥1 release |
| **HARD REMOVAL** | 6 onboarding tools | ✅ Yes — blast radius contained: only the documented onboarding flow consumes them, runs once per user |

### 4.6 What Telemetry Could Overturn

If the telemetry shows:
- **Tool-selection error rate <5% across all 70 tools** → COUNT is NOT the problem. Keep 70, fix only descriptions/naming.
- **90% of agent calls hit the same 15 tools** → The long tail is harmless. Prune onboarding + merge 9 overlaps only.
- **Zep collapse didn't improve their metrics** (external research) → Consolidation may not be the answer for any memory server.

---

## Appendix A — Methodological Notes

- **Static analysis source:** Fresh clone of `git clone --depth 1 https://github.com/daniel-ospina/tortoise.git /tmp/research888`
- **Tool count:** 70 ToolDefinition entries in `tortoise/tool_registry.py`
- **SDK method count:** 140 `def` methods in `tortoise/sdk.py`, 102 public (non-`_`), 63 referenced in tool registry's `sdk_method` field → 39 not referenced; plus 4 additional that are in registry but use handler_override or empty string → 43 orphans
- **Grep evidence:** All orphan usage sourced from `grep -rl` across `tortoise/` and `tests/` directories
- **Benchmark sources:** Official documentation pages for Mem0, Zep, Hindsight, Basic Memory, Supabase; fetched via web_fetch July 2026

## Appendix B — Open Questions for Scope Gate

1. **Entity CRUD consolidation risk:** Merging `create_subject`/`create_object`/`create_event`/`create_document` into `create_entity` may confuse agents about which `entity_type` values are valid. Mitigation: auto-complete enum in tool description. Critical requirement: the unified tool MUST preserve `create_event`'s about-entity edge wiring (aboutSubject/aboutObject/aboutPoint/aboutDocument extraction + edge creation, sdk.py:4292–4320). Is the UX risk worth 3 tool slots saved?

2. **Point lifecycle consolidation — HIGH RISK:** `supersede` has edge-transfer semantics (~80 lines, sdk.py:944–1068) that `invalidate` lacks (flag-only, sdk.py:895–943). Merging them into one tool with a `mode` param would force agents to toggle between fundamentally different operations. **Keep `supersede` separate.** At most merge `invalidate` + `retract` (both flag-only on a single Point). Only consolidate further if telemetry shows agents confuse these three operations.

3. **`tortoise_list` as a mega-tool:** A single list tool with `entity` param replacing 6 tools is the biggest surface shrink (-5 tools). But it's also the biggest UX risk: the agent must know what entity types can be listed. Solution: make `entity` an enum in the tool description with valid values: `tags`, `sources`, `pointkinds`, `namespaces`, `graphs`, `relations`.

4. **Source tools grouping — RESOLVED:** The 4 source-related tools form a coherent "sources" curation group. Keep them as a separate group (not folded into memory). The preliminary target includes this as a 5th curation group. If source management is not needed by routine agent sessions, feature-gate the group via connection parameters (Supabase/Hindsight pattern).

5. **`file_decision` semantic mismatch — RESOLVED:** `file_decision` (sdk.py:1586) creates N+M+1 Points + edges atomically. Folding into `create_point(pointKind="decision")` loses this atomic multi-Point creation. Keep `file_decision` as a standalone tool OR propose an explicit `batch_create_points` primitive. This is a semantic mismatch, not a surface reduction opportunity.

---

> **Document version:** v1.1 — Stage 2 research brief for Epic #888 (revised: adversarial review fixes)
> **Next gate:** Scope (HUMAN APPROVAL) — no implementation proceeds until this document is approved
> **Telemetry dependency:** Work item #0 must complete with ≥500 tool calls, ≥10 distinct tools, ≥7 days before design decision can be finalized
