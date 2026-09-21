# Phase 0.1 — the bridge table

**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.

Every `file:line` in this document is **read from the source at build time**, so it cannot
drift from the code it cites. The destination map is data in the generator; every count
below is arithmetic computed against the **served registry** — the live tools plus the
16 retired names, which still answer through the #3883 warning shim. The generator **fails the build**
if the map and the registry disagree — a mismatch is a finding, not something to reconcile.

**Registry: 98 tools → 81 absorbed into the 26 MCP targets · 1 absorbed into a builder-only SDK method (not on the MCP) · 2 tenancy (SDK/REST only) · 14 retired.**

**These are not the same number.** The MCP has **26** tools; **14** current tools retire, **2** are tenancy-only, **1** is absorbed into a builder-only SDK method that is not on the MCP, and **81** are absorbed into those 26 — many-to-one. Writing "98 minus 26 equals 72 retired" conflates the two, and is wrong.

---

## Part A — the merged tools

A *merged* target absorbs more than one current tool, so it dispatches internally on a
discriminator. **Which tools are merged is computed from the map** — not listed by hand — and
**whether the method exists is read from `sdk_defs`**. A discriminator value with no method
behind it is invisible until someone writes the handler and finds nothing to call, which is
what this part exists to catch.

Only the `Discriminator` column is authored data: it is a design fact about the target, not
something derivable from today's code.

| Target tool | Sources absorbed | Discriminator | Exists |
|---|---|---|---|
| `graph_overview` | 13 | `section=` | **no — Part C1** |
| `check_confidence` | 7 | *(none — dispatch is by argument)* | **no — Part C1** |
| `get_entity` | 7 | `type=` | yes |
| `search_knowledge` | 7 | `mode=` (full-text / hybrid) | **no — Part C1** |
| `create_entity` | 6 | `type=` | yes |
| `list_knowledge` | 5 | `kind=` | **no — Part C1** |
| `update_knowledge` | 4 | *(which fields — incl. the retract fields)* | **no — Part C1** |
| `adjust_relationship` | 3 | *(strength)* | **no — Part C1** |
| `delete_knowledge` | 3 | *(node or link)* | **no — Part C1** |
| `explore_connections` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `index_sources_from_directory` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `refresh_confidence` | 3 | `scope=` | **no — Part C1** |
| `review_link_candidates` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `link_entities` | 2 | *(relation kind)* | **no — Part C1** |
| `manage_source_trust` | 2 | *(none — dispatch is by argument)* | **no — Part C1** |
| `supersede_knowledge` | 2 | *(link policy)* | **no — Part C1** |

**16 merged targets. 2 of them have a method behind them today.** The other **14** are Phase 2 work, not renames.

## Part B — every current tool and its single destination

| # | Current tool | Source | SDK binding | Read-only | Destination |
|---|---|---|---|---|---|
| 1 | `tortoise_analyze` | `tool_registry.py:791` | `analyze` ⚠️ **does not resolve** | yes | `REMOVED` |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:480` | `annotate_operator` | no | `adjust_relationship` |
| 3 | `tortoise_approve_merge` | `tool_registry.py:352` | `approve_merge` | no | `approve_merge` |
| 4 | `tortoise_assess_source` | `tool_registry.py:976` | `assess_source` | no | `manage_source_trust` |
| 5 | `tortoise_audit` | `tool_registry.py:153` | `audit` | yes | `graph_overview` |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1115` | `backfill_v25` | no | `REMOVED` |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:375` | `belief_timeline` | yes | `check_confidence` |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:423` | `calibrate_summary` | yes | `check_confidence` |
| 9 | `tortoise_check_structure` | `tool_registry.py:120` | `check_structure` | yes | `graph_overview` |
| 10 | `tortoise_checkpoint` | `tool_registry.py:608` | `checkpoint` | no | `REMOVED` |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:386` | `compute_confidence` | yes | `check_confidence` |
| 12 | `tortoise_create_document` | `tool_registry.py:944` | `create_document` | no | `create_entity` |
| 13 | `tortoise_create_edge` | `tool_registry.py:1071` | `create_edge` | no | `link_entities` |
| 14 | `tortoise_create_entity` | `tool_registry.py:1026` | `create_entity` | no | `create_entity` |
| 15 | `tortoise_create_event` | `tool_registry.py:884` | `create_event` | no | `create_entity` |
| 16 | `tortoise_create_object` | `tool_registry.py:875` | `create_object` | no | `create_entity` |
| 17 | `tortoise_create_operator` | `tool_registry.py:470` | `create_operator` | no | `link_entities` |
| 18 | `tortoise_create_point` | `tool_registry.py:85` | `create_point` | no | `create_entity` |
| 19 | `tortoise_create_source` | `tool_registry.py:953` | `create_source` | no | `register_source` |
| 20 | `tortoise_create_subject` | `tool_registry.py:866` | `create_subject` | no | `create_entity` |
| 21 | `tortoise_delete` | `tool_registry.py:1049` | `delete` | no | `delete_knowledge` |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1017` | `delete_entity` | no | `delete_knowledge` |
| 23 | `tortoise_delete_point` | `tool_registry.py:533` | `delete_point_wrapped` | no | `delete_knowledge` |
| 24 | `tortoise_diary_read` | `tool_registry.py:628` | `diary_read` | yes | `REMOVED` |
| 25 | `tortoise_diary_write` | `tool_registry.py:618` | `diary_write` | no | `REMOVED` |
| 26 | `tortoise_dream` | `tool_registry.py:431` | `dream` | no | `refresh_confidence` |
| 27 | `tortoise_dream_health` | `tool_registry.py:447` | `dream_health_check` | yes | `graph_overview` |
| 28 | `tortoise_entity_profile` | `tool_registry.py:588` | `entity_profile` ⚠️ **does not resolve** | yes | `explore_connections` |
| 29 | `tortoise_events_poll` | `tool_registry.py:564` | `events_poll` | yes | `poll_events` |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:290` | `expand_relationships` | yes | `explore_connections` |
| 31 | `tortoise_file_decision` | `tool_registry.py:510` | `file_decision` | no | `write_question` |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:520` | `file_human_approval` | no | `record_decision` |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:827` | `get_cross_lens_candidates` | yes | `review_link_candidates` |
| 34 | `tortoise_get` | `tool_registry.py:1104` | **none declared** | yes | `get_entity` |
| 35 | `tortoise_get_confidence` | `tool_registry.py:415` | `get_confidence` | yes | `check_confidence` |
| 36 | `tortoise_get_entity` | `tool_registry.py:997` | `get_entity` | yes | `get_entity` |
| 37 | `tortoise_get_events` | `tool_registry.py:893` | `get_events` | yes | `get_entity` |
| 38 | `tortoise_get_governance` | `tool_registry.py:1084` | `get_owned_entities` | yes | `get_entity` |
| 39 | `tortoise_get_operator` | `tool_registry.py:490` | `get_point` | yes | `get_entity` |
| 40 | `tortoise_get_point` | `tool_registry.py:261` | `get_point` | yes | `get_entity` |
| 41 | `tortoise_get_session` | `tool_registry.py:901` | `get_session` | yes | `get_entity` |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:965` | `get_source_reliability` | no | `list_knowledge` |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:697` | **none declared** | no | `graph_set_recording` |
| 44 | `tortoise_health` | `tool_registry.py:653` | `health` ⚠️ **does not resolve** | yes | `graph_overview` |
| 45 | `tortoise_index_files` | `tool_registry.py:919` | `index_directory` | no | `index_sources_from_directory` |
| 46 | `tortoise_index_sessions` | `tool_registry.py:909` | `index_sessions` | no | `index_sources_from_directory` |
| 47 | `tortoise_ingest` | `tool_registry.py:740` | `ingest` | no | `sdk:write_knowledge_batch` |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:730` | `ingest_corpus` | no | `index_sources_from_directory` |
| 49 | `tortoise_invalidate` | `tool_registry.py:542` | `invalidate_point` | no | `supersede_knowledge` |
| 50 | `tortoise_issue_insight` | `tool_registry.py:716` | `issue_insight` | yes | `search_knowledge` |
| 51 | `tortoise_list_batch` | `tool_registry.py:193` | `list_batch` | yes | `list_knowledge` |
| 52 | `tortoise_list_batches` | `tool_registry.py:205` | `list_batches` | yes | `list_knowledge` |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:343` | `list_dedup_candidates` | yes | `review_link_candidates` |
| 54 | `tortoise_list_graphs` | `tool_registry.py:636` | `list_graphs` | yes | `tenancy:list_memory_graphs` |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:184` | `list_namespaces` | yes | `list_knowledge` |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:168` | `list_pointkinds` | yes | `graph_overview` |
| 57 | `tortoise_list_sources` | `tool_registry.py:176` | `list_sources` | yes | `graph_overview` |
| 58 | `tortoise_list_tags` | `tool_registry.py:243` | `list_tags` | yes | `graph_overview` |
| 59 | `tortoise_list_topics` | `tool_registry.py:780` | `list_topics` | yes | `list_knowledge` |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:329` | `mine_corpus` | no | `mine_knowledge_from_directory` |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:499` | `mitigate_operator` | no | `adjust_relationship` |
| 62 | `tortoise_onboarding_demo_create` | `tool_registry.py:1125` | **none declared** | no | `REMOVED` |
| 63 | `tortoise_onboarding_github_connect` | `tool_registry.py:1168` | **none declared** | no | `REMOVED` |
| 64 | `tortoise_onboarding_github_index` | `tool_registry.py:1178` | **none declared** | no | `REMOVED` |
| 65 | `tortoise_onboarding_github_status` | `tool_registry.py:1188` | **none declared** | yes | `REMOVED` |
| 66 | `tortoise_onboarding_seed` | `tool_registry.py:1144` | **none declared** | no | `REMOVED` |
| 67 | `tortoise_onboarding_session_recording` | `tool_registry.py:1158` | **none declared** | no | `REMOVED` |
| 68 | `tortoise_onboarding_state` | `tool_registry.py:1135` | **none declared** | yes | `REMOVED` |
| 69 | `tortoise_operator_action` | `tool_registry.py:1059` | `operator_action` | no | `adjust_relationship` |
| 70 | `tortoise_org_create` | `tool_registry.py:855` | `org_create` | no | `tenancy:create_memory_graph` |
| 71 | `tortoise_overview` | `tool_registry.py:1093` | **none declared** | yes | `graph_overview` |
| 72 | `tortoise_pack_install` | `tool_registry.py:226` | `upsert_tenant_manifest` ⚠️ **does not resolve** | no | `REMOVED` |
| 73 | `tortoise_packs_list` | `tool_registry.py:215` | `get_tenant_packs` ⚠️ **does not resolve** | yes | `REMOVED` |
| 74 | `tortoise_paginated_query` | `tool_registry.py:111` | `paginated_query` | yes | `search_knowledge` |
| 75 | `tortoise_promote_point` | `tool_registry.py:363` | `promote_point` | no | `refresh_confidence` |
| 76 | `tortoise_provenance` | `tool_registry.py:845` | `provenance` | yes | `check_confidence` |
| 77 | `tortoise_query` | `tool_registry.py:98` | `query` | yes | `search_knowledge` |
| 78 | `tortoise_query_points_by_tag` | `tool_registry.py:251` | `query_points_by_tag` | yes | `search_knowledge` |
| 79 | `tortoise_recall` | `tool_registry.py:301` | `recall_state` | yes | `check_confidence` |
| 80 | `tortoise_retract_point` | `tool_registry.py:576` | `retract_point` | no | `update_knowledge` |
| 81 | `tortoise_review_connections` | `tool_registry.py:809` | `review_connections` | yes | `review_link_candidates` |
| 82 | `tortoise_search` | `tool_registry.py:278` | `tortoise_fts_query` | yes | `search_knowledge` |
| 83 | `tortoise_search_sessions` | `tool_registry.py:936` | `search_sessions` | yes | `search_knowledge` |
| 84 | `tortoise_session_capture` | `tool_registry.py:671` | **none declared** | no | `mine_knowledge_from_session` |
| 85 | `tortoise_session_context` | `tool_registry.py:661` | `session_context` | yes | `check_confidence` |
| 86 | `tortoise_set_point_baseline` | `tool_registry.py:406` | `set_point_baseline` | no | `refresh_confidence` |
| 87 | `tortoise_set_source_tier` | `tool_registry.py:987` | `set_source_tier` | no | `manage_source_trust` |
| 88 | `tortoise_stale` | `tool_registry.py:801` | `stale_points` | yes | `graph_overview` |
| 89 | `tortoise_status` | `tool_registry.py:644` | `status` | yes | `graph_overview` |
| 90 | `tortoise_suggest_entry_points` | `tool_registry.py:269` | `suggest_entry_points` | yes | `search_knowledge` |
| 91 | `tortoise_summarize_structure` | `tool_registry.py:140` | `summarize_structure` | yes | `graph_overview` |
| 92 | `tortoise_supersede` | `tool_registry.py:551` | `supersede` | no | `supersede_knowledge` |
| 93 | `tortoise_taxonomy` | `tool_registry.py:771` | `taxonomy` | yes | `graph_overview` |
| 94 | `tortoise_traverse` | `tool_registry.py:598` | `traverse` | yes | `explore_connections` |
| 95 | `tortoise_update` | `tool_registry.py:1038` | `update` | no | `update_knowledge` |
| 96 | `tortoise_update_entity` | `tool_registry.py:1008` | `update_entity` | no | `update_knowledge` |
| 97 | `tortoise_update_point` | `tool_registry.py:460` | `update_point` | no | `update_knowledge` |
| 98 | `tortoise_validate_domain` | `tool_registry.py:128` | `validate_domain` | yes | `graph_overview` |

**16** of these are RETIRED names (#3883): off the advertised surface, but they
still answer through the warning shim, and each one's `Destination` is the destination of
the replacement that warning names. The other **82** are live.

### Destination counts

| Destination | Count |
|---|---|
| `REMOVED` | 14 |
| `graph_overview` | 13 |
| `check_confidence` | 7 |
| `get_entity` | 7 |
| `search_knowledge` | 7 |
| `create_entity` | 6 |
| `list_knowledge` | 5 |
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `explore_connections` | 3 |
| `index_sources_from_directory` | 3 |
| `refresh_confidence` | 3 |
| `review_link_candidates` | 3 |
| `link_entities` | 2 |
| `manage_source_trust` | 2 |
| `supersede_knowledge` | 2 |
| `approve_merge` | 1 |
| `graph_set_recording` | 1 |
| `mine_knowledge_from_directory` | 1 |
| `mine_knowledge_from_session` | 1 |
| `poll_events` | 1 |
| `record_decision` | 1 |
| `register_source` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| `write_question` | 1 |
| **total** | **98** |

Destination rows: **28**. Registry tools: **98**.

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
| `update_memory_graph` | MCP | no `def` on TortoiseSDK |
| `create_memory_graph` | tenancy | no `def` on TortoiseSDK |
| `list_memory_graphs` | tenancy | no `def` on TortoiseSDK |
| `write_knowledge_batch` | sdk-only | no `def` on TortoiseSDK |

**These are not renames.** They are new methods that must be built in Phase 2, and the
plan listed them as if they were renames. This is what Part A exists to catch.

### C2 — registry bindings that do not resolve

| Registry tool | Declared binding | Source |
|---|---|---|
| `tortoise_packs_list` | `get_tenant_packs` | `tool_registry.py:215` |
| `tortoise_pack_install` | `upsert_tenant_manifest` | `tool_registry.py:226` |
| `tortoise_entity_profile` | `entity_profile` | `tool_registry.py:588` |
| `tortoise_analyze` | `analyze` | `tool_registry.py:791` |
| `tortoise_health` | `health` | `tool_registry.py:653` |

A tool that declares a binding to a method **that is not a `def` on `TortoiseSDK`** is a
declaration that cannot be honoured. It fails silently today because nothing checks it.

---

## Reproduce

```bash
uv run python tools/bridge_table.py          # regenerate this file
uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift
```
