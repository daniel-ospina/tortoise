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
| 1 | `tortoise_annotate_operator` | `tool_registry.py:734` | **no replacement** | `adjust_relationship` | absent |
| 2 | `tortoise_approve_merge` | `tool_registry.py:571` | **no replacement** | `approve_merge` | absent |
| 3 | `tortoise_assess_source` | `tool_registry.py:1319` | **no replacement** | `manage_source_trust` | absent |
| 4 | `tortoise_audit` | `tool_registry.py:370` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | warning shim |
| 5 | `tortoise_belief_timeline` | `tool_registry.py:603` | **no replacement** | `check_confidence` | absent |
| 6 | `tortoise_calibrate_summary` | `tool_registry.py:657` | **no replacement** | `check_confidence` | absent |
| 7 | `tortoise_check_structure` | `tool_registry.py:337` | **no replacement** | `graph_overview` | absent |
| 8 | `tortoise_checkpoint` | `tool_registry.py:910` | **no replacement** | `mine_knowledge_from_session` | absent |
| 9 | `tortoise_compute_confidence` | `tool_registry.py:615` | **no replacement** | `check_confidence` | absent |
| 10 | `tortoise_create_document` | `tool_registry.py:1266` | **no replacement** | `create_entity` | absent |
| 11 | `tortoise_create_edge` | `tool_registry.py:1424` | **no replacement** | `link_entities` | absent |
| 12 | `tortoise_create_entity` | `tool_registry.py:1367` | **no replacement** | `create_entity` | absent |
| 13 | `tortoise_create_event` | `tool_registry.py:1208` | **no replacement** | `create_entity` | absent |
| 14 | `tortoise_create_object` | `tool_registry.py:1196` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_operator` | `tool_registry.py:710` | **no replacement** | `link_entities` | absent |
| 16 | `tortoise_create_point` | `tool_registry.py:267` | **no replacement** | `create_entity` | absent |
| 17 | `tortoise_create_source` | `tool_registry.py:1284` | **no replacement** | `register_source` | absent |
| 18 | `tortoise_create_subject` | `tool_registry.py:1183` | **no replacement** | `create_entity` | absent |
| 19 | `tortoise_delete` | `tool_registry.py:1394` | **no replacement** | `delete_knowledge` | absent |
| 20 | `tortoise_delete_entity` | `tool_registry.py:1357` | **no replacement** | `delete_knowledge` | absent |
| 21 | `tortoise_delete_point` | `tool_registry.py:793` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_diary_read` | `tool_registry.py:929` | **no replacement** | `list_knowledge` | absent |
| 23 | `tortoise_diary_write` | `tool_registry.py:920` | **no replacement** | `create_entity` | absent |
| 24 | `tortoise_dream` | `tool_registry.py:665` | **no replacement** | `refresh_confidence` | absent |
| 25 | `tortoise_dream_health` | `tool_registry.py:681` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | warning shim |
| 26 | `tortoise_entity_profile` | `tool_registry.py:890` | **no replacement** | `explore_connections` | absent |
| 27 | `tortoise_events_poll` | `tool_registry.py:825` | **no replacement** | `poll_events` | absent |
| 28 | `tortoise_expand_relationships` | `tool_registry.py:508` | **no replacement** | `explore_connections` | absent |
| 29 | `tortoise_file_decision` | `tool_registry.py:772` | **no replacement** | `write_question` | absent |
| 30 | `tortoise_file_human_approval` | `tool_registry.py:781` | **no replacement** | `record_decision` | absent |
| 31 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1144` | **no replacement** | `review_link_candidates` | absent |
| 32 | `tortoise_get` | `tool_registry.py:1471` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 33 | `tortoise_get_confidence` | `tool_registry.py:646` | **no replacement** | `check_confidence` | absent |
| 34 | `tortoise_get_entity` | `tool_registry.py:1338` | **no replacement** | `get_entity` | absent |
| 35 | `tortoise_get_events` | `tool_registry.py:1216` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 36 | `tortoise_get_governance` | `tool_registry.py:1436` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 37 | `tortoise_get_operator` | `tool_registry.py:743` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 38 | `tortoise_get_point` | `tool_registry.py:467` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 39 | `tortoise_get_session` | `tool_registry.py:1224` | **no replacement** | `get_entity` | absent |
| 40 | `tortoise_get_source_reliability` | `tool_registry.py:1296` | **no replacement** | `list_knowledge` | absent |
| 41 | `tortoise_graph_overview` | `tool_registry.py:1445` | **no replacement** | `graph_overview` | absent |
| 42 | `tortoise_graph_set_recording` | `tool_registry.py:1001` | **no replacement** | `graph_set_recording` | absent |
| 43 | `tortoise_health` | `tool_registry.py:954` | `tortoise_graph_overview(section="health")` | `graph_overview` | warning shim |
| 44 | `tortoise_index_files` | `tool_registry.py:1242` | **no replacement** | `index_sources_from_directory` | absent |
| 45 | `tortoise_index_sessions` | `tool_registry.py:1232` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 46 | `tortoise_ingest` | `tool_registry.py:1044` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 47 | `tortoise_ingest_corpus` | `tool_registry.py:1033` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 48 | `tortoise_invalidate` | `tool_registry.py:803` | **no replacement** | `supersede_knowledge` | absent |
| 49 | `tortoise_issue_insight` | `tool_registry.py:1019` | **no replacement** | `search_knowledge` | absent |
| 50 | `tortoise_list_batch` | `tool_registry.py:414` | **no replacement** | `list_knowledge` | absent |
| 51 | `tortoise_list_batches` | `tool_registry.py:426` | **no replacement** | `list_knowledge` | absent |
| 52 | `tortoise_list_dedup_candidates` | `tool_registry.py:560` | **no replacement** | `review_link_candidates` | absent |
| 53 | `tortoise_list_graphs` | `tool_registry.py:937` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 54 | `tortoise_list_namespaces` | `tool_registry.py:405` | **no replacement** | `list_knowledge` | absent |
| 55 | `tortoise_list_pointkinds` | `tool_registry.py:389` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 56 | `tortoise_list_sources` | `tool_registry.py:397` | `tortoise_graph_overview(section="sources")` | `graph_overview` | warning shim |
| 57 | `tortoise_list_tags` | `tool_registry.py:446` | `tortoise_graph_overview(section="tags")` | `graph_overview` | warning shim |
| 58 | `tortoise_list_topics` | `tool_registry.py:1095` | **no replacement** | `list_knowledge` | absent |
| 59 | `tortoise_manage_sources` | `tool_registry.py:1509` | **no replacement** | `manage_sources` | absent |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:547` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:752` | **no replacement** | `adjust_relationship` | absent |
| 62 | `tortoise_onboarding_github_index` | `tool_registry.py:1550` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 63 | `tortoise_onboarding_session_recording` | `tool_registry.py:1541` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 64 | `tortoise_operator_action` | `tool_registry.py:1413` | **no replacement** | `adjust_relationship` | absent |
| 65 | `tortoise_org_create` | `tool_registry.py:1172` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 66 | `tortoise_overview` | `tool_registry.py:1460` | `tortoise_graph_overview(section=...)` | `graph_overview` | warning shim |
| 67 | `tortoise_paginated_query` | `tool_registry.py:328` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 68 | `tortoise_promote_point` | `tool_registry.py:592` | **no replacement** | `refresh_confidence` | absent |
| 69 | `tortoise_provenance` | `tool_registry.py:1162` | **no replacement** | `check_confidence` | absent |
| 70 | `tortoise_query` | `tool_registry.py:297` | **no replacement** | `search_knowledge` | absent |
| 71 | `tortoise_query_points_by_tag` | `tool_registry.py:454` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | warning shim |
| 72 | `tortoise_recall` | `tool_registry.py:519` | **no replacement** | `check_confidence` | absent |
| 73 | `tortoise_retract_point` | `tool_registry.py:878` | **no replacement** | `update_knowledge` | absent |
| 74 | `tortoise_review_connections` | `tool_registry.py:1121` | **no replacement** | `review_link_candidates` | absent |
| 75 | `tortoise_search` | `tool_registry.py:496` | **no replacement** | `search_knowledge` | absent |
| 76 | `tortoise_search_sessions` | `tool_registry.py:1258` | **no replacement** | `search_knowledge` | absent |
| 77 | `tortoise_session_capture` | `tool_registry.py:972` | **no replacement** | `mine_knowledge_from_session` | absent |
| 78 | `tortoise_session_context` | `tool_registry.py:962` | **no replacement** | `check_confidence` | absent |
| 79 | `tortoise_set_point_baseline` | `tool_registry.py:638` | **no replacement** | `refresh_confidence` | absent |
| 80 | `tortoise_set_source_tier` | `tool_registry.py:1329` | **no replacement** | `manage_source_trust` | absent |
| 81 | `tortoise_stale` | `tool_registry.py:1113` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | warning shim |
| 82 | `tortoise_status` | `tool_registry.py:945` | `tortoise_graph_overview(section="status")` | `graph_overview` | warning shim |
| 83 | `tortoise_suggest_entry_points` | `tool_registry.py:487` | **no replacement** | `search_knowledge` | absent |
| 84 | `tortoise_summarize_structure` | `tool_registry.py:357` | `tortoise_graph_overview(section="structure")` | `graph_overview` | warning shim |
| 85 | `tortoise_supersede` | `tool_registry.py:812` | **no replacement** | `supersede_knowledge` | absent |
| 86 | `tortoise_taxonomy` | `tool_registry.py:1085` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 87 | `tortoise_traverse` | `tool_registry.py:900` | **no replacement** | `explore_connections` | absent |
| 88 | `tortoise_update` | `tool_registry.py:1383` | **no replacement** | `update_knowledge` | absent |
| 89 | `tortoise_update_entity` | `tool_registry.py:1349` | **no replacement** | `update_knowledge` | absent |
| 90 | `tortoise_update_point` | `tool_registry.py:694` | **no replacement** | `update_knowledge` | absent |
| 91 | `tortoise_validate_domain` | `tool_registry.py:345` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | warning shim |

**91 served MCP tools, 28 destinations.** 23 retire **with a warning shim**; the other **68** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 23 `warning shim` names are
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
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `explore_connections` | 3 |
| `index_sources_from_directory` | 3 |
| `manage_sources` | 3 |
| `refresh_confidence` | 3 |
| `review_link_candidates` | 3 |
| `link_entities` | 2 |
| `manage_source_trust` | 2 |
| `mine_knowledge_from_session` | 2 |
| `supersede_knowledge` | 2 |
| `approve_merge` | 1 |
| `graph_set_recording` | 1 |
| `mine_knowledge_from_directory` | 1 |
| `poll_events` | 1 |
| `record_decision` | 1 |
| `register_source` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| `write_question` | 1 |
| **total** | **91** |

91 served tools → 28 destinations: **88** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **0** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 23 names, and they must not
contradict each other: a caller who follows a redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | PR #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_audit` | `graph_overview` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | AGREE |
| `tortoise_dream_health` | `graph_overview` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | AGREE |
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_graph_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_list_pointkinds` | `graph_overview` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | AGREE |
| `tortoise_list_sources` | `graph_overview` | `tortoise_graph_overview(section="sources")` | `graph_overview` | AGREE |
| `tortoise_list_tags` | `graph_overview` | `tortoise_graph_overview(section="tags")` | `graph_overview` | AGREE |
| `tortoise_onboarding_github_index` | `manage_sources` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | AGREE |
| `tortoise_onboarding_session_recording` | `manage_sources` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | AGREE |
| `tortoise_overview` | `graph_overview` | `tortoise_graph_overview(section=...)` | `graph_overview` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_graph_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_summarize_structure` | `graph_overview` | `tortoise_graph_overview(section="structure")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | AGREE |
| `tortoise_validate_domain` | `graph_overview` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | AGREE |

**23 of 23 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 23 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
