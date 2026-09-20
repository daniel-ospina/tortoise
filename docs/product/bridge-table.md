# Phase 0.1 — the bridge table

**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.

Every `file:line` in this document is **read from the source at build time**, so it cannot
drift from the code it cites. The destination map is data in the generator; every count
below is arithmetic computed against the live registry. The generator **fails the build**
if the map and the registry disagree — a mismatch is a finding, not something to reconcile.

**Registry: 98 tools → 71 absorbed into MCP destinations · 2 tenancy (SDK/REST only) · 25 retired.**

**These are not the same number.** The MCP has **25** tools; **25** current tools retire and **71** are absorbed into those 25 — many-to-one. Writing "98 minus 25 equals 73 retired" conflates the two, and is wrong.

---

## Part A — the discriminator map

The target tools that are *merged* dispatch internally on a discriminator. A discriminator
value with no method behind it is invisible until someone writes the handler and finds
nothing to call — which is exactly what this part exists to catch.

| Target tool | Discriminator | SDK method it must call | Exists |
|---|---|---|---|
| `create_entity` | `type=` | `create_entity` | yes |
| `link_entities` | *(relation kind)* | `link_entities` | no — see Part C |
| `list_knowledge` | `kind=` | `list_knowledge` | no — see Part C |
| `update_knowledge` | *(retract fields)* | `update_knowledge` | no — see Part C |
| `delete_knowledge` | *(node or link)* | `delete_knowledge` | no — see Part C |
| `graph_overview` | `section=` | `graph_overview` | no — see Part C |
| `adjust_relationship` | *(strength)* | `adjust_relationship` | no — see Part C |
| `refresh_confidence` | `scope=` | `refresh_confidence` | no — see Part C |
| `record_decision` | *(inline question)* | `record_decision` | no — see Part C |

**The `Exists` column is the finding.** Of the nine merged tools, only one has a method
behind it today. The rest are Phase 2 work, not renames.

## Part B — every current tool and its single destination

| # | Current tool | Source | SDK binding | Read-only | Destination |
|---|---|---|---|---|---|
| 1 | `tortoise_analyze` | `tool_registry.py:698` | `analyze` ⚠️ **does not resolve** | yes | `REMOVED` |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:428` | `annotate_operator` | no | `adjust_relationship` |
| 3 | `tortoise_approve_merge` | `tool_registry.py:316` | `approve_merge` | no | `approve_merge` |
| 4 | `tortoise_assess_source` | `tool_registry.py:859` | `assess_source` | no | `manage_source_trust` |
| 5 | `tortoise_audit` | `tool_registry.py:136` | `audit` | yes | `graph_overview` |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:973` | `backfill_v25` | no | `REMOVED` |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:335` | `belief_timeline` | yes | `check_confidence` |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:378` | `calibrate_summary` | yes | `check_confidence` |
| 9 | `tortoise_check_structure` | `tool_registry.py:106` | `check_structure` | yes | `graph_overview` |
| 10 | `tortoise_checkpoint` | `tool_registry.py:536` | `checkpoint` | no | `REMOVED` |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:345` | `compute_confidence` | yes | `check_confidence` |
| 12 | `tortoise_create_document` | `tool_registry.py:833` | `create_document` | no | `create_entity` |
| 13 | `tortoise_create_edge` | `tool_registry.py:934` | `create_edge` | no | `link_entities` |
| 14 | `tortoise_create_entity` | `tool_registry.py:897` | `create_entity` | no | `create_entity` |
| 15 | `tortoise_create_event` | `tool_registry.py:781` | `create_event` | no | `create_entity` |
| 16 | `tortoise_create_object` | `tool_registry.py:774` | `create_object` | no | `create_entity` |
| 17 | `tortoise_create_operator` | `tool_registry.py:420` | `create_operator` | no | `link_entities` |
| 18 | `tortoise_create_point` | `tool_registry.py:75` | `create_point` | no | `create_entity` |
| 19 | `tortoise_create_source` | `tool_registry.py:840` | `create_source` | no | `register_source` |
| 20 | `tortoise_create_subject` | `tool_registry.py:767` | `create_subject` | no | `create_entity` |
| 21 | `tortoise_delete` | `tool_registry.py:916` | `delete` | no | `delete_knowledge` |
| 22 | `tortoise_delete_entity` | `tool_registry.py:890` | `delete_entity` | no | `delete_knowledge` |
| 23 | `tortoise_delete_point` | `tool_registry.py:472` | `delete_point_wrapped` | no | `delete_knowledge` |
| 24 | `tortoise_diary_read` | `tool_registry.py:552` | `diary_read` | yes | `REMOVED` |
| 25 | `tortoise_diary_write` | `tool_registry.py:544` | `diary_write` | no | `REMOVED` |
| 26 | `tortoise_dream` | `tool_registry.py:385` | `dream` | no | `refresh_confidence` |
| 27 | `tortoise_dream_health` | `tool_registry.py:400` | `dream_health_check` | yes | `REMOVED` |
| 28 | `tortoise_entity_profile` | `tool_registry.py:518` | `entity_profile` ⚠️ **does not resolve** | yes | `REMOVED` |
| 29 | `tortoise_events_poll` | `tool_registry.py:497` | `events_poll` | yes | `poll_events` |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:259` | `expand_relationships` | yes | `explore_connections` |
| 31 | `tortoise_file_decision` | `tool_registry.py:453` | `file_decision` | no | `write_question` |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:461` | `file_human_approval` | no | `record_decision` |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:731` | `get_cross_lens_candidates` | yes | `review_link_candidates` |
| 34 | `tortoise_get` | `tool_registry.py:963` | **none declared** | yes | `get_entity` |
| 35 | `tortoise_get_confidence` | `tool_registry.py:371` | `get_confidence` | yes | `check_confidence` |
| 36 | `tortoise_get_entity` | `tool_registry.py:876` | `get_entity` | yes | `get_entity` |
| 37 | `tortoise_get_events` | `tool_registry.py:788` | `get_events` | yes | `poll_events` |
| 38 | `tortoise_get_governance` | `tool_registry.py:945` | `get_owned_entities` | yes | `REMOVED` |
| 39 | `tortoise_get_operator` | `tool_registry.py:436` | `get_point` | yes | `get_entity` |
| 40 | `tortoise_get_point` | `tool_registry.py:233` | `get_point` | yes | `get_entity` |
| 41 | `tortoise_get_session` | `tool_registry.py:795` | `get_session` | yes | `REMOVED` |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:850` | `get_source_reliability` | no | `list_knowledge` |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:612` | **none declared** | no | `REMOVED` |
| 44 | `tortoise_health` | `tool_registry.py:574` | `health` ⚠️ **does not resolve** | yes | `REMOVED` |
| 45 | `tortoise_index_files` | `tool_registry.py:811` | `index_directory` | no | `index_sources_from_directory` |
| 46 | `tortoise_index_sessions` | `tool_registry.py:802` | `index_sessions` | no | `index_sources_from_directory` |
| 47 | `tortoise_ingest` | `tool_registry.py:651` | `ingest` | no | `REMOVED` |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:642` | `ingest_corpus` | no | `index_sources_from_directory` |
| 49 | `tortoise_invalidate` | `tool_registry.py:479` | `invalidate_point` | no | `supersede_knowledge` |
| 50 | `tortoise_issue_insight` | `tool_registry.py:629` | `issue_insight` | yes | `REMOVED` |
| 51 | `tortoise_list_batch` | `tool_registry.py:172` | `list_batch` | yes | `list_knowledge` |
| 52 | `tortoise_list_batches` | `tool_registry.py:183` | `list_batches` | yes | `list_knowledge` |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:308` | `list_dedup_candidates` | yes | `review_link_candidates` |
| 54 | `tortoise_list_graphs` | `tool_registry.py:559` | `list_graphs` | yes | `tenancy:list_memory_graphs` |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:164` | `list_namespaces` | yes | `list_knowledge` |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:150` | `list_pointkinds` | yes | `list_knowledge` |
| 57 | `tortoise_list_sources` | `tool_registry.py:157` | `list_sources` | yes | `list_knowledge` |
| 58 | `tortoise_list_tags` | `tool_registry.py:217` | `list_tags` | yes | `list_knowledge` |
| 59 | `tortoise_list_topics` | `tool_registry.py:688` | `list_topics` | yes | `list_knowledge` |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:296` | `mine_corpus` | no | `mine_knowledge_from_directory` |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:444` | `mitigate_operator` | no | `adjust_relationship` |
| 62 | `tortoise_onboarding_demo_create` | `tool_registry.py:982` | **none declared** | no | `REMOVED` |
| 63 | `tortoise_onboarding_github_connect` | `tool_registry.py:1018` | **none declared** | no | `REMOVED` |
| 64 | `tortoise_onboarding_github_index` | `tool_registry.py:1026` | **none declared** | no | `REMOVED` |
| 65 | `tortoise_onboarding_github_status` | `tool_registry.py:1034` | **none declared** | yes | `REMOVED` |
| 66 | `tortoise_onboarding_seed` | `tool_registry.py:998` | **none declared** | no | `REMOVED` |
| 67 | `tortoise_onboarding_session_recording` | `tool_registry.py:1010` | **none declared** | no | `REMOVED` |
| 68 | `tortoise_onboarding_state` | `tool_registry.py:990` | **none declared** | yes | `REMOVED` |
| 69 | `tortoise_operator_action` | `tool_registry.py:924` | `operator_action` | no | `adjust_relationship` |
| 70 | `tortoise_org_create` | `tool_registry.py:757` | `org_create` | no | `tenancy:create_memory_graph` |
| 71 | `tortoise_overview` | `tool_registry.py:953` | **none declared** | yes | `graph_overview` |
| 72 | `tortoise_pack_install` | `tool_registry.py:202` | `upsert_tenant_manifest` ⚠️ **does not resolve** | no | `REMOVED` |
| 73 | `tortoise_packs_list` | `tool_registry.py:192` | `get_tenant_packs` ⚠️ **does not resolve** | yes | `REMOVED` |
| 74 | `tortoise_paginated_query` | `tool_registry.py:98` | `paginated_query` | yes | `list_knowledge` |
| 75 | `tortoise_promote_point` | `tool_registry.py:325` | `promote_point` | no | `refresh_confidence` |
| 76 | `tortoise_provenance` | `tool_registry.py:748` | `provenance` | yes | `check_confidence` |
| 77 | `tortoise_query` | `tool_registry.py:86` | `query` | yes | `search_knowledge` |
| 78 | `tortoise_query_points_by_tag` | `tool_registry.py:224` | `query_points_by_tag` | yes | `list_knowledge` |
| 79 | `tortoise_recall` | `tool_registry.py:269` | `recall_state` | yes | `check_confidence` |
| 80 | `tortoise_retract_point` | `tool_registry.py:508` | `retract_point` | no | `update_knowledge` |
| 81 | `tortoise_review_connections` | `tool_registry.py:714` | `review_connections` | yes | `review_link_candidates` |
| 82 | `tortoise_search` | `tool_registry.py:248` | `tortoise_fts_query` | yes | `search_knowledge` |
| 83 | `tortoise_search_sessions` | `tool_registry.py:826` | `search_sessions` | yes | `search_knowledge` |
| 84 | `tortoise_session_capture` | `tool_registry.py:590` | **none declared** | no | `mine_knowledge_from_session` |
| 85 | `tortoise_session_context` | `tool_registry.py:581` | `session_context` | yes | `REMOVED` |
| 86 | `tortoise_set_point_baseline` | `tool_registry.py:364` | `set_point_baseline` | no | `REMOVED` |
| 87 | `tortoise_set_source_tier` | `tool_registry.py:868` | `set_source_tier` | no | `manage_source_trust` |
| 88 | `tortoise_stale` | `tool_registry.py:707` | `stale_points` | yes | `graph_overview` |
| 89 | `tortoise_status` | `tool_registry.py:566` | `status` | yes | `graph_overview` |
| 90 | `tortoise_suggest_entry_points` | `tool_registry.py:240` | `suggest_entry_points` | yes | `REMOVED` |
| 91 | `tortoise_summarize_structure` | `tool_registry.py:124` | `summarize_structure` | yes | `graph_overview` |
| 92 | `tortoise_supersede` | `tool_registry.py:486` | `supersede` | no | `supersede_knowledge` |
| 93 | `tortoise_taxonomy` | `tool_registry.py:680` | `taxonomy` | yes | `graph_overview` |
| 94 | `tortoise_traverse` | `tool_registry.py:527` | `traverse` | yes | `explore_connections` |
| 95 | `tortoise_update` | `tool_registry.py:907` | `update` | no | `update_knowledge` |
| 96 | `tortoise_update_entity` | `tool_registry.py:883` | `update_entity` | no | `update_knowledge` |
| 97 | `tortoise_update_point` | `tool_registry.py:412` | `update_point` | no | `update_knowledge` |
| 98 | `tortoise_validate_domain` | `tool_registry.py:113` | `validate_domain` | yes | `graph_overview` |

### Destination counts

| Destination | Count |
|---|---|
| `REMOVED` | 25 |
| `list_knowledge` | 10 |
| `graph_overview` | 8 |
| `check_confidence` | 6 |
| `create_entity` | 6 |
| `get_entity` | 4 |
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `index_sources_from_directory` | 3 |
| `review_link_candidates` | 3 |
| `search_knowledge` | 3 |
| `explore_connections` | 2 |
| `link_entities` | 2 |
| `manage_source_trust` | 2 |
| `poll_events` | 2 |
| `refresh_confidence` | 2 |
| `supersede_knowledge` | 2 |
| `approve_merge` | 1 |
| `mine_knowledge_from_directory` | 1 |
| `mine_knowledge_from_session` | 1 |
| `record_decision` | 1 |
| `register_source` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| `write_question` | 1 |
| **total** | **98** |

Destination rows: **26**. Sum of counts: **98**. Registry tools: **98**. **MATCH**

## Part C — blockers

### C1 — target tools with NO method on `TortoiseSDK`

| Target tool | Where | Finding |
|---|---|---|
| `search_knowledge` | MCP | no `def` on TortoiseSDK |
| `list_knowledge` | MCP | no `def` on TortoiseSDK |
| `check_confidence` | MCP | no `def` on TortoiseSDK |
| `get_historical_knowledge` | MCP | no `def` on TortoiseSDK |
| `explore_connections` | MCP | no `def` on TortoiseSDK |
| `graph_overview` | MCP | no `def` on TortoiseSDK |
| `review_link_candidates` | MCP | no `def` on TortoiseSDK |
| `poll_events` | MCP | no `def` on TortoiseSDK |
| `check_connection` | MCP | no `def` on TortoiseSDK |
| `register_source` | MCP | no `def` on TortoiseSDK |
| `index_sources_from_directory` | MCP | no `def` on TortoiseSDK |
| `mine_knowledge_from_session` | MCP | no `def` on TortoiseSDK |
| `mine_knowledge_from_directory` | MCP | no `def` on TortoiseSDK |
| `manage_source_trust` | MCP | no `def` on TortoiseSDK |
| `link_entities` | MCP | no `def` on TortoiseSDK |
| `write_question` | MCP | no `def` on TortoiseSDK |
| `record_decision` | MCP | no `def` on TortoiseSDK |
| `update_knowledge` | MCP | no `def` on TortoiseSDK |
| `supersede_knowledge` | MCP | no `def` on TortoiseSDK |
| `delete_knowledge` | MCP | no `def` on TortoiseSDK |
| `adjust_relationship` | MCP | no `def` on TortoiseSDK |
| `refresh_confidence` | MCP | no `def` on TortoiseSDK |
| `create_memory_graph` | tenancy | no `def` on TortoiseSDK |
| `list_memory_graphs` | tenancy | no `def` on TortoiseSDK |

**These are not renames.** They are new methods that must be built in Phase 2, and the
plan listed them as if they were renames. This is what Part A exists to catch.

### C2 — registry bindings that do not resolve

| Registry tool | Declared binding | Source |
|---|---|---|
| `tortoise_packs_list` | `get_tenant_packs` | `tool_registry.py:192` |
| `tortoise_pack_install` | `upsert_tenant_manifest` | `tool_registry.py:202` |
| `tortoise_entity_profile` | `entity_profile` | `tool_registry.py:518` |
| `tortoise_health` | `health` | `tool_registry.py:574` |
| `tortoise_analyze` | `analyze` | `tool_registry.py:698` |

A tool that declares a binding to a method **that is not a `def` on `TortoiseSDK`** is a
declaration that cannot be honoured. It fails silently today because nothing checks it.

---

## Reproduce

```bash
uv run python tools/bridge_table.py          # regenerate this file
uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift
```
