# Phase 0.3 — the MCP rename table

**GENERATED — do not edit.** `uv run python tools/mcp_rename_table.py`; verify with `--check`.

**The caller's question.** Phase 0.1 answers *which target absorbs this tool?* for
implementers. This table answers *I call `tortoise_get_point` — what do I call now?*
for callers. Both answers are one row per served MCP tool: the name to call **today**,
and the **target-surface** name that will replace it.

Every `file:line` below is **read from the source at build time**, so a citation cannot
drift. Every count is **arithmetic** computed from the authored maps against the live
registry. The generator **fails the build** if those disagree — a mismatch is a finding,
not something to reconcile silently.

---

## Part A — one migration row per served MCP tool

| # | Current tool | Source | Call instead today | Destination (target surface) | Retirement |
|---|---|---|---|---|---|
| 1 | `tortoise_annotate_operator` | `tool_registry.py:722` | **no replacement** | `adjust_relationship` | absent |
| 2 | `tortoise_approve_merge` | `tool_registry.py:559` | **no replacement** | `approve_merge` | absent |
| 3 | `tortoise_assess_source` | `tool_registry.py:1312` | `tortoise_manage_source_trust(url, score=..., assessor=..., rationale=...)` | `manage_source_trust` | warning shim |
| 4 | `tortoise_audit` | `tool_registry.py:370` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | warning shim |
| 5 | `tortoise_belief_timeline` | `tool_registry.py:591` | **no replacement** | `check_confidence` | absent |
| 6 | `tortoise_calibrate_summary` | `tool_registry.py:645` | **no replacement** | `check_confidence` | absent |
| 7 | `tortoise_check_connection` | `tool_registry.py:1647` | **no replacement** | `check_connection` | absent |
| 8 | `tortoise_check_structure` | `tool_registry.py:337` | **no replacement** | `graph_overview` | absent |
| 9 | `tortoise_checkpoint` | `tool_registry.py:898` | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., threshold=...)` | `mine_knowledge_from_session` | warning shim |
| 10 | `tortoise_compute_confidence` | `tool_registry.py:603` | **no replacement** | `check_confidence` | absent |
| 11 | `tortoise_create_document` | `tool_registry.py:1259` | **no replacement** | `create_entity` | absent |
| 12 | `tortoise_create_edge` | `tool_registry.py:1417` | **no replacement** | `link_entities` | absent |
| 13 | `tortoise_create_entity` | `tool_registry.py:1360` | **no replacement** | `create_entity` | absent |
| 14 | `tortoise_create_event` | `tool_registry.py:1198` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_object` | `tool_registry.py:1186` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_operator` | `tool_registry.py:698` | **no replacement** | `link_entities` | absent |
| 17 | `tortoise_create_point` | `tool_registry.py:267` | **no replacement** | `create_entity` | absent |
| 18 | `tortoise_create_source` | `tool_registry.py:1277` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | warning shim |
| 19 | `tortoise_create_subject` | `tool_registry.py:1173` | **no replacement** | `create_entity` | absent |
| 20 | `tortoise_delete` | `tool_registry.py:1387` | **no replacement** | `delete_knowledge` | absent |
| 21 | `tortoise_delete_entity` | `tool_registry.py:1350` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_delete_point` | `tool_registry.py:781` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_diary_read` | `tool_registry.py:917` | **no replacement** | `list_knowledge` | absent |
| 24 | `tortoise_diary_write` | `tool_registry.py:908` | **no replacement** | `create_entity` | absent |
| 25 | `tortoise_dream` | `tool_registry.py:653` | **no replacement** | `refresh_confidence` | absent |
| 26 | `tortoise_dream_health` | `tool_registry.py:669` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | warning shim |
| 27 | `tortoise_entity_profile` | `tool_registry.py:878` | **no replacement** | `explore_connections` | absent |
| 28 | `tortoise_events_poll` | `tool_registry.py:813` | **no replacement** | `poll_events` | absent |
| 29 | `tortoise_expand_relationships` | `tool_registry.py:496` | **no replacement** | `explore_connections` | absent |
| 30 | `tortoise_file_decision` | `tool_registry.py:760` | **no replacement** | `write_question` | absent |
| 31 | `tortoise_file_human_approval` | `tool_registry.py:769` | **no replacement** | `record_decision` | absent |
| 32 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1134` | **no replacement** | `review_link_candidates` | absent |
| 33 | `tortoise_get` | `tool_registry.py:1464` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 34 | `tortoise_get_confidence` | `tool_registry.py:634` | **no replacement** | `check_confidence` | absent |
| 35 | `tortoise_get_entity` | `tool_registry.py:1331` | **no replacement** | `get_entity` | absent |
| 36 | `tortoise_get_events` | `tool_registry.py:1206` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 37 | `tortoise_get_governance` | `tool_registry.py:1429` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 38 | `tortoise_get_operator` | `tool_registry.py:731` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 39 | `tortoise_get_point` | `tool_registry.py:467` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 40 | `tortoise_get_session` | `tool_registry.py:1214` | **no replacement** | `get_entity` | absent |
| 41 | `tortoise_get_source_reliability` | `tool_registry.py:1289` | **no replacement** | `list_knowledge` | absent |
| 42 | `tortoise_graph_overview` | `tool_registry.py:1438` | **no replacement** | `graph_overview` | absent |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:989` | **no replacement** | `graph_set_recording` | absent |
| 44 | `tortoise_health` | `tool_registry.py:942` | `tortoise_graph_overview(section="health")` | `graph_overview` | warning shim |
| 45 | `tortoise_index_files` | `tool_registry.py:1235` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | warning shim |
| 46 | `tortoise_index_sessions` | `tool_registry.py:1222` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | warning shim |
| 47 | `tortoise_index_sources_from_directory` | `tool_registry.py:1594` | **no replacement** | `index_sources_from_directory` | absent |
| 48 | `tortoise_ingest` | `tool_registry.py:1034` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 49 | `tortoise_ingest_corpus` | `tool_registry.py:1021` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | warning shim |
| 50 | `tortoise_invalidate` | `tool_registry.py:791` | **no replacement** | `supersede_knowledge` | absent |
| 51 | `tortoise_issue_insight` | `tool_registry.py:1007` | **no replacement** | `search_knowledge` | absent |
| 52 | `tortoise_list_batch` | `tool_registry.py:414` | **no replacement** | `list_knowledge` | absent |
| 53 | `tortoise_list_batches` | `tool_registry.py:426` | **no replacement** | `list_knowledge` | absent |
| 54 | `tortoise_list_dedup_candidates` | `tool_registry.py:548` | **no replacement** | `review_link_candidates` | absent |
| 55 | `tortoise_list_graphs` | `tool_registry.py:925` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 56 | `tortoise_list_namespaces` | `tool_registry.py:405` | **no replacement** | `list_knowledge` | absent |
| 57 | `tortoise_list_pointkinds` | `tool_registry.py:389` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 58 | `tortoise_list_sources` | `tool_registry.py:397` | `tortoise_graph_overview(section="sources")` | `graph_overview` | warning shim |
| 59 | `tortoise_list_tags` | `tool_registry.py:446` | `tortoise_graph_overview(section="tags")` | `graph_overview` | warning shim |
| 60 | `tortoise_list_topics` | `tool_registry.py:1085` | **no replacement** | `list_knowledge` | absent |
| 61 | `tortoise_manage_source_trust` | `tool_registry.py:1580` | **no replacement** | `manage_source_trust` | absent |
| 62 | `tortoise_manage_sources` | `tool_registry.py:1502` | **no replacement** | `manage_sources` | absent |
| 63 | `tortoise_mine_conversations` | `tool_registry.py:535` | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities=...)` | `mine_knowledge_from_directory` | warning shim |
| 64 | `tortoise_mine_knowledge_from_directory` | `tool_registry.py:1634` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 65 | `tortoise_mine_knowledge_from_session` | `tool_registry.py:1613` | **no replacement** | `mine_knowledge_from_session` | absent |
| 66 | `tortoise_mitigate_operator` | `tool_registry.py:740` | **no replacement** | `adjust_relationship` | absent |
| 67 | `tortoise_onboarding_github_index` | `tool_registry.py:1543` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 68 | `tortoise_onboarding_session_recording` | `tool_registry.py:1534` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 69 | `tortoise_operator_action` | `tool_registry.py:1406` | **no replacement** | `adjust_relationship` | absent |
| 70 | `tortoise_org_create` | `tool_registry.py:1162` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 71 | `tortoise_overview` | `tool_registry.py:1453` | `tortoise_graph_overview(section=...)` | `graph_overview` | warning shim |
| 72 | `tortoise_paginated_query` | `tool_registry.py:328` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 73 | `tortoise_promote_point` | `tool_registry.py:580` | **no replacement** | `refresh_confidence` | absent |
| 74 | `tortoise_provenance` | `tool_registry.py:1152` | **no replacement** | `check_confidence` | absent |
| 75 | `tortoise_query` | `tool_registry.py:297` | **no replacement** | `search_knowledge` | absent |
| 76 | `tortoise_query_points_by_tag` | `tool_registry.py:454` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | warning shim |
| 77 | `tortoise_recall` | `tool_registry.py:507` | **no replacement** | `check_confidence` | absent |
| 78 | `tortoise_register_source` | `tool_registry.py:1567` | **no replacement** | `register_source` | absent |
| 79 | `tortoise_retract_point` | `tool_registry.py:866` | **no replacement** | `update_knowledge` | absent |
| 80 | `tortoise_review_connections` | `tool_registry.py:1111` | **no replacement** | `review_link_candidates` | absent |
| 81 | `tortoise_search` | `tool_registry.py:484` | **no replacement** | `search_knowledge` | absent |
| 82 | `tortoise_search_sessions` | `tool_registry.py:1251` | **no replacement** | `search_knowledge` | absent |
| 83 | `tortoise_session_capture` | `tool_registry.py:960` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=...)` | `mine_knowledge_from_session` | warning shim |
| 84 | `tortoise_session_context` | `tool_registry.py:950` | **no replacement** | `check_confidence` | absent |
| 85 | `tortoise_set_point_baseline` | `tool_registry.py:626` | **no replacement** | `refresh_confidence` | absent |
| 86 | `tortoise_set_source_tier` | `tool_registry.py:1322` | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | `manage_source_trust` | warning shim |
| 87 | `tortoise_stale` | `tool_registry.py:1103` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | warning shim |
| 88 | `tortoise_status` | `tool_registry.py:933` | `tortoise_graph_overview(section="status")` | `graph_overview` | warning shim |
| 89 | `tortoise_suggest_entry_points` | `tool_registry.py:475` | **no replacement** | `search_knowledge` | absent |
| 90 | `tortoise_summarize_structure` | `tool_registry.py:357` | `tortoise_graph_overview(section="structure")` | `graph_overview` | warning shim |
| 91 | `tortoise_supersede` | `tool_registry.py:800` | **no replacement** | `supersede_knowledge` | absent |
| 92 | `tortoise_taxonomy` | `tool_registry.py:1075` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 93 | `tortoise_traverse` | `tool_registry.py:888` | **no replacement** | `explore_connections` | absent |
| 94 | `tortoise_update` | `tool_registry.py:1376` | **no replacement** | `update_knowledge` | absent |
| 95 | `tortoise_update_entity` | `tool_registry.py:1342` | **no replacement** | `update_knowledge` | absent |
| 96 | `tortoise_update_point` | `tool_registry.py:682` | **no replacement** | `update_knowledge` | absent |
| 97 | `tortoise_validate_domain` | `tool_registry.py:345` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | warning shim |

**97 served MCP tools, 29 destinations.** 30 retire **with a warning shim**; the other **67** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 30 `warning shim` names are
retired ON `main` and this column is what a caller experiences now. The map is read live from `tortoise.tool_registry.RETIRED_USE_INSTEAD` rather than pinned, so it cannot describe a plan that has since landed differently.

The registry states what retiring a name DOES — quoted verbatim and in full:

> ── Retired names (#3883 / #3863) ─────────────────────────────────────────
> A name in this mapping is RETIRED: it is not in TOOL_REGISTRY, so it is not
> registered as an MCP tool and never appears in `tools/list`. It is NOT gone —
> `_RetiredToolTransform` in mcp_server.py resolves it on `get_tool` and serves
> a shim that answers exactly as the live tool did AND warns the caller, naming
> the replacement. That is the #3836 (b) decision: a retired name keeps working
> and tells us who still calls it.

The direction of the `get` retirement is an owner decision, recorded in the registry's own comment — quoted verbatim and in full:

> ⚠ This direction is an OWNER DECISION, not an implementation preference:
> `docs/product/canonical-mcp-tools.md` (approved, approval_pr 4120) rules
> that `tortoise_get_entity` must NOT be retired and that the map must
> retire `tortoise_get` in its place. Retiring the pair the other way sends
> every caller of `get_entity` to a name that is itself retired — a churn
> loop — which is why the pointers below name `tortoise_get_entity`.

### Destination counts

| Destination | Count |
|---|---|
| `graph_overview` | 14 |
| `check_confidence` | 7 |
| `create_entity` | 7 |
| `get_entity` | 7 |
| `search_knowledge` | 7 |
| `list_knowledge` | 6 |
| `index_sources_from_directory` | 4 |
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `explore_connections` | 3 |
| `manage_source_trust` | 3 |
| `manage_sources` | 3 |
| `mine_knowledge_from_session` | 3 |
| `refresh_confidence` | 3 |
| `review_link_candidates` | 3 |
| `link_entities` | 2 |
| `mine_knowledge_from_directory` | 2 |
| `register_source` | 2 |
| `supersede_knowledge` | 2 |
| `approve_merge` | 1 |
| `check_connection` | 1 |
| `graph_set_recording` | 1 |
| `poll_events` | 1 |
| `record_decision` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| `write_question` | 1 |
| **total** | **97** |

97 served tools → 29 destinations: **94** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **0** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 30 names, and they must not
contradict each other: a caller who follows a redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | PR #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_assess_source` | `manage_source_trust` | `tortoise_manage_source_trust(url, score=..., assessor=..., rationale=...)` | `manage_source_trust` | AGREE |
| `tortoise_audit` | `graph_overview` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | AGREE |
| `tortoise_checkpoint` | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., threshold=...)` | `mine_knowledge_from_session` | AGREE |
| `tortoise_create_source` | `register_source` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | AGREE |
| `tortoise_dream_health` | `graph_overview` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | AGREE |
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_graph_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_files` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | AGREE |
| `tortoise_list_pointkinds` | `graph_overview` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | AGREE |
| `tortoise_list_sources` | `graph_overview` | `tortoise_graph_overview(section="sources")` | `graph_overview` | AGREE |
| `tortoise_list_tags` | `graph_overview` | `tortoise_graph_overview(section="tags")` | `graph_overview` | AGREE |
| `tortoise_mine_conversations` | `mine_knowledge_from_directory` | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities=...)` | `mine_knowledge_from_directory` | AGREE |
| `tortoise_onboarding_github_index` | `manage_sources` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | AGREE |
| `tortoise_onboarding_session_recording` | `manage_sources` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | AGREE |
| `tortoise_overview` | `graph_overview` | `tortoise_graph_overview(section=...)` | `graph_overview` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_session_capture` | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=...)` | `mine_knowledge_from_session` | AGREE |
| `tortoise_set_source_tier` | `manage_source_trust` | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | `manage_source_trust` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_graph_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_summarize_structure` | `graph_overview` | `tortoise_graph_overview(section="structure")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | AGREE |
| `tortoise_validate_domain` | `graph_overview` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | AGREE |

**30 of 30 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 30 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
