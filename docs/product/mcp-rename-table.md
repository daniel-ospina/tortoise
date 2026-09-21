# Phase 0.3 — the MCP rename table

**GENERATED — do not edit.** `uv run python tools/mcp_rename_table.py`; verify with `--check`.

**The caller's question.** Phase 0.1 answers *which target absorbs this tool?* for
implementers. This table answers *I call `tortoise_get_point` — what do I call now?*
for callers. Both answers are one row per current MCP tool: the name to call **today**,
and the **target-surface** name that will replace it.

Every `file:line` below is **read from the source at build time**, so a citation cannot
drift. Every count is **arithmetic** computed from the authored maps against the live
registry. The generator **fails the build** if those disagree — a mismatch is a finding,
not something to reconcile silently.

---

## Part A — one migration row per current MCP tool

| # | Current tool | Source | Call instead today | Destination (target surface) | Retirement |
|---|---|---|---|---|---|
| 1 | `tortoise_analyze` | `tool_registry.py:784` | **no replacement** | `REMOVED` | absent |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:473` | **no replacement** | `adjust_relationship` | absent |
| 3 | `tortoise_approve_merge` | `tool_registry.py:345` | **no replacement** | `approve_merge` | absent |
| 4 | `tortoise_assess_source` | `tool_registry.py:969` | **no replacement** | `manage_source_trust` | absent |
| 5 | `tortoise_audit` | `tool_registry.py:146` | **no replacement** | `graph_overview` | absent |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1105` | **no replacement** | `REMOVED` | absent |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:368` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:416` | **no replacement** | `check_confidence` | absent |
| 9 | `tortoise_check_structure` | `tool_registry.py:113` | **no replacement** | `graph_overview` | absent |
| 10 | `tortoise_checkpoint` | `tool_registry.py:601` | **no replacement** | `REMOVED` | absent |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:379` | **no replacement** | `check_confidence` | absent |
| 12 | `tortoise_create_document` | `tool_registry.py:937` | **no replacement** | `create_entity` | absent |
| 13 | `tortoise_create_edge` | `tool_registry.py:1061` | **no replacement** | `link_entities` | absent |
| 14 | `tortoise_create_entity` | `tool_registry.py:1016` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_event` | `tool_registry.py:877` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_object` | `tool_registry.py:868` | **no replacement** | `create_entity` | absent |
| 17 | `tortoise_create_operator` | `tool_registry.py:463` | **no replacement** | `link_entities` | absent |
| 18 | `tortoise_create_point` | `tool_registry.py:78` | **no replacement** | `create_entity` | absent |
| 19 | `tortoise_create_source` | `tool_registry.py:946` | **no replacement** | `register_source` | absent |
| 20 | `tortoise_create_subject` | `tool_registry.py:859` | **no replacement** | `create_entity` | absent |
| 21 | `tortoise_delete` | `tool_registry.py:1039` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1007` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_delete_point` | `tool_registry.py:526` | **no replacement** | `delete_knowledge` | absent |
| 24 | `tortoise_diary_read` | `tool_registry.py:621` | **no replacement** | `REMOVED` | absent |
| 25 | `tortoise_diary_write` | `tool_registry.py:611` | **no replacement** | `REMOVED` | absent |
| 26 | `tortoise_dream` | `tool_registry.py:424` | **no replacement** | `refresh_confidence` | absent |
| 27 | `tortoise_dream_health` | `tool_registry.py:440` | **no replacement** | `graph_overview` | absent |
| 28 | `tortoise_entity_profile` | `tool_registry.py:581` | **no replacement** | `explore_connections` | absent |
| 29 | `tortoise_events_poll` | `tool_registry.py:557` | **no replacement** | `poll_events` | absent |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:283` | **no replacement** | `explore_connections` | absent |
| 31 | `tortoise_file_decision` | `tool_registry.py:503` | **no replacement** | `write_question` | absent |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:513` | **no replacement** | `record_decision` | absent |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:820` | **no replacement** | `review_link_candidates` | absent |
| 34 | `tortoise_get` | `tool_registry.py:1094` | **no replacement** | `get_entity` | absent |
| 35 | `tortoise_get_confidence` | `tool_registry.py:408` | **no replacement** | `check_confidence` | absent |
| 36 | `tortoise_get_entity` | `tool_registry.py:990` | `tortoise_get(id, type="entity")` | `get_entity` | warning shim |
| 37 | `tortoise_get_events` | `tool_registry.py:886` | `tortoise_get(None, type="events")` | `poll_events` | warning shim |
| 38 | `tortoise_get_governance` | `tool_registry.py:1074` | `tortoise_get(id, type="governance")` | `get_entity` | warning shim |
| 39 | `tortoise_get_operator` | `tool_registry.py:483` | `tortoise_get(id, type="operator")` | `get_entity` | warning shim |
| 40 | `tortoise_get_point` | `tool_registry.py:254` | `tortoise_get(id, type="point")` | `get_entity` | warning shim |
| 41 | `tortoise_get_session` | `tool_registry.py:894` | **no replacement** | `get_entity` | absent |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:958` | **no replacement** | `list_knowledge` | absent |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:690` | **no replacement** | `graph_set_recording` | absent |
| 44 | `tortoise_health` | `tool_registry.py:646` | `tortoise_overview(section="health")` | `graph_overview` | warning shim |
| 45 | `tortoise_index_files` | `tool_registry.py:912` | **no replacement** | `index_sources_from_directory` | absent |
| 46 | `tortoise_index_sessions` | `tool_registry.py:902` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 47 | `tortoise_ingest` | `tool_registry.py:733` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:723` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 49 | `tortoise_invalidate` | `tool_registry.py:535` | **no replacement** | `supersede_knowledge` | absent |
| 50 | `tortoise_issue_insight` | `tool_registry.py:709` | **no replacement** | `search_knowledge` | absent |
| 51 | `tortoise_list_batch` | `tool_registry.py:186` | **no replacement** | `list_knowledge` | absent |
| 52 | `tortoise_list_batches` | `tool_registry.py:198` | **no replacement** | `list_knowledge` | absent |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:336` | **no replacement** | `review_link_candidates` | absent |
| 54 | `tortoise_list_graphs` | `tool_registry.py:629` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:177` | **no replacement** | `list_knowledge` | absent |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:161` | `tortoise_overview(section="pointkinds")` | `list_knowledge` | warning shim |
| 57 | `tortoise_list_sources` | `tool_registry.py:169` | `tortoise_overview(section="sources")` | `list_knowledge` | warning shim |
| 58 | `tortoise_list_tags` | `tool_registry.py:236` | `tortoise_overview(section="tags")` | `list_knowledge` | warning shim |
| 59 | `tortoise_list_topics` | `tool_registry.py:773` | **no replacement** | `list_knowledge` | absent |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:322` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:492` | **no replacement** | `adjust_relationship` | absent |
| 62 | `tortoise_onboarding_demo_create` | `tool_registry.py:1115` | **no replacement** | `REMOVED` | absent |
| 63 | `tortoise_onboarding_github_connect` | `tool_registry.py:1158` | **no replacement** | `REMOVED` | absent |
| 64 | `tortoise_onboarding_github_index` | `tool_registry.py:1168` | **no replacement** | `REMOVED` | absent |
| 65 | `tortoise_onboarding_github_status` | `tool_registry.py:1178` | **no replacement** | `REMOVED` | absent |
| 66 | `tortoise_onboarding_seed` | `tool_registry.py:1134` | **no replacement** | `REMOVED` | absent |
| 67 | `tortoise_onboarding_session_recording` | `tool_registry.py:1148` | **no replacement** | `REMOVED` | absent |
| 68 | `tortoise_onboarding_state` | `tool_registry.py:1125` | **no replacement** | `REMOVED` | absent |
| 69 | `tortoise_operator_action` | `tool_registry.py:1049` | **no replacement** | `adjust_relationship` | absent |
| 70 | `tortoise_org_create` | `tool_registry.py:848` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 71 | `tortoise_overview` | `tool_registry.py:1083` | **no replacement** | `graph_overview` | absent |
| 72 | `tortoise_pack_install` | `tool_registry.py:219` | **no replacement** | `REMOVED` | absent |
| 73 | `tortoise_packs_list` | `tool_registry.py:208` | **no replacement** | `REMOVED` | absent |
| 74 | `tortoise_paginated_query` | `tool_registry.py:104` | `tortoise_query(offset=..., limit=...)` | `list_knowledge` | warning shim |
| 75 | `tortoise_promote_point` | `tool_registry.py:356` | **no replacement** | `refresh_confidence` | absent |
| 76 | `tortoise_provenance` | `tool_registry.py:838` | **no replacement** | `check_confidence` | absent |
| 77 | `tortoise_query` | `tool_registry.py:91` | **no replacement** | `search_knowledge` | absent |
| 78 | `tortoise_query_points_by_tag` | `tool_registry.py:244` | `tortoise_query(tag=...)` | `list_knowledge` | warning shim |
| 79 | `tortoise_recall` | `tool_registry.py:294` | **no replacement** | `check_confidence` | absent |
| 80 | `tortoise_retract_point` | `tool_registry.py:569` | **no replacement** | `update_knowledge` | absent |
| 81 | `tortoise_review_connections` | `tool_registry.py:802` | **no replacement** | `review_link_candidates` | absent |
| 82 | `tortoise_search` | `tool_registry.py:271` | **no replacement** | `search_knowledge` | absent |
| 83 | `tortoise_search_sessions` | `tool_registry.py:929` | **no replacement** | `search_knowledge` | absent |
| 84 | `tortoise_session_capture` | `tool_registry.py:664` | **no replacement** | `mine_knowledge_from_session` | absent |
| 85 | `tortoise_session_context` | `tool_registry.py:654` | **no replacement** | `check_confidence` | absent |
| 86 | `tortoise_set_point_baseline` | `tool_registry.py:399` | **no replacement** | `refresh_confidence` | absent |
| 87 | `tortoise_set_source_tier` | `tool_registry.py:980` | **no replacement** | `manage_source_trust` | absent |
| 88 | `tortoise_stale` | `tool_registry.py:794` | `tortoise_overview(section="stale")` | `graph_overview` | warning shim |
| 89 | `tortoise_status` | `tool_registry.py:637` | `tortoise_overview(section="status")` | `graph_overview` | warning shim |
| 90 | `tortoise_suggest_entry_points` | `tool_registry.py:262` | **no replacement** | `search_knowledge` | absent |
| 91 | `tortoise_summarize_structure` | `tool_registry.py:133` | **no replacement** | `graph_overview` | absent |
| 92 | `tortoise_supersede` | `tool_registry.py:544` | **no replacement** | `supersede_knowledge` | absent |
| 93 | `tortoise_taxonomy` | `tool_registry.py:764` | `tortoise_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 94 | `tortoise_traverse` | `tool_registry.py:591` | **no replacement** | `explore_connections` | absent |
| 95 | `tortoise_update` | `tool_registry.py:1028` | **no replacement** | `update_knowledge` | absent |
| 96 | `tortoise_update_entity` | `tool_registry.py:998` | **no replacement** | `update_knowledge` | absent |
| 97 | `tortoise_update_point` | `tool_registry.py:453` | **no replacement** | `update_knowledge` | absent |
| 98 | `tortoise_validate_domain` | `tool_registry.py:121` | **no replacement** | `graph_overview` | absent |

**98 current MCP tools, 28 destinations.** 16 retire **with a warning shim**; the other **82** are simply absent — no shim.

**`Retirement` is a forecast, not today's behaviour.** PR #4031 (`feat/3883-retired-name-warning`) is **not merged**, so on `main` every name in
this table still resolves. The column says what happens to the OLD name once that
plan lands: a `warning shim` name is still served and warns; an `absent` name simply
disappears. The snapshot was read from `origin/feat/3883-retired-name-warning @ c01ad93b569e514e94d0d813c0216de9cb746d3b`.

### Destination counts

| Destination | Count |
|---|---|
| `REMOVED` | 14 |
| `graph_overview` | 10 |
| `list_knowledge` | 10 |
| `check_confidence` | 7 |
| `create_entity` | 6 |
| `get_entity` | 6 |
| `search_knowledge` | 5 |
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `explore_connections` | 3 |
| `index_sources_from_directory` | 3 |
| `refresh_confidence` | 3 |
| `review_link_candidates` | 3 |
| `link_entities` | 2 |
| `manage_source_trust` | 2 |
| `poll_events` | 2 |
| `supersede_knowledge` | 2 |
| `approve_merge` | 1 |
| `graph_set_recording` | 1 |
| `mine_knowledge_from_directory` | 1 |
| `mine_knowledge_from_session` | 1 |
| `record_decision` | 1 |
| `register_source` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| `write_question` | 1 |
| **total** | **98** |

98 current tools → 28 destinations: **81** absorbed into the **26** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **14** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 16 names, and they must not
contradict each other: a caller who follows a #4031 redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_get_entity` | `get_entity` | `tortoise_get(id, type="entity")` | `get_entity` | AGREE |
| `tortoise_get_events` | `poll_events` | `tortoise_get(None, type="events")` | `get_entity` | **DISAGREE** |
| `tortoise_get_governance` | `get_entity` | `tortoise_get(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get(id, type="point")` | `get_entity` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_list_pointkinds` | `list_knowledge` | `tortoise_overview(section="pointkinds")` | `graph_overview` | **DISAGREE** |
| `tortoise_list_sources` | `list_knowledge` | `tortoise_overview(section="sources")` | `graph_overview` | **DISAGREE** |
| `tortoise_list_tags` | `list_knowledge` | `tortoise_overview(section="tags")` | `graph_overview` | **DISAGREE** |
| `tortoise_paginated_query` | `list_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | **DISAGREE** |
| `tortoise_query_points_by_tag` | `list_knowledge` | `tortoise_query(tag=...)` | `search_knowledge` | **DISAGREE** |
| `tortoise_stale` | `graph_overview` | `tortoise_overview(section="stale")` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_overview(section="taxonomy")` | `graph_overview` | AGREE |

**10 of 16 agree; 6 disagree.**

### B1 — the disagreements

| Current tool | 0.1 destination | #4031 redirect | Redirect's 0.1 destination |
|---|---|---|---|
| `tortoise_get_events` | `poll_events` | `tortoise_get(None, type="events")` | `get_entity` |
| `tortoise_list_pointkinds` | `list_knowledge` | `tortoise_overview(section="pointkinds")` | `graph_overview` |
| `tortoise_list_sources` | `list_knowledge` | `tortoise_overview(section="sources")` | `graph_overview` |
| `tortoise_list_tags` | `list_knowledge` | `tortoise_overview(section="tags")` | `graph_overview` |
| `tortoise_paginated_query` | `list_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` |
| `tortoise_query_points_by_tag` | `list_knowledge` | `tortoise_query(tag=...)` | `search_knowledge` |

**6 findings.** Each row is a name whose absorbing target is stated two ways:
the 0.1 map's destination, and the destination of the name #4031 tells the caller to
use instead. Both cannot be right for the caller. This generator does not resolve them;
they are tracked as tortoise #4475 for the #4282 controller to decide.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
