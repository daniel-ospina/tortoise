# Phase 0.1 — the bridge table

**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.

Every `file:line` in this document is **read from the source at build time**, so it cannot
drift from the code it cites. The destination map is data in the generator; every count
below is arithmetic computed against the **served registry** — the live tools plus the
18 retired names, which still answer through the #3883 warning shim. The generator **fails the build**
if the map and the registry disagree — a mismatch is a finding, not something to reconcile.

**Registry: 99 tools → 87 absorbed into the 27 MCP targets · 1 absorbed into a builder-only SDK method (not on the MCP) · 2 tenancy (SDK/REST only) · 9 retired.**

**These are not the same number.** The MCP has **27** tools; **9** current tools retire, **2** are tenancy-only, **1** is absorbed into a builder-only SDK method that is not on the MCP, and **87** are absorbed into those 27 — many-to-one. Writing "99 minus 27 equals 72 retired" conflates the two, and is wrong.

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
| `create_entity` | 7 | `type=` | yes |
| `get_entity` | 7 | `type=` | yes |
| `search_knowledge` | 7 | `mode=` (full-text / hybrid) | **no — Part C1** |
| `list_knowledge` | 6 | `kind=` | **no — Part C1** |
| `update_knowledge` | 4 | *(which fields — incl. the retract fields)* | **no — Part C1** |
| `adjust_relationship` | 3 | *(strength)* | **no — Part C1** |
| `delete_knowledge` | 3 | *(node or link)* | **no — Part C1** |
| `explore_connections` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `index_sources_from_directory` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `manage_sources` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `refresh_confidence` | 3 | `scope=` | **no — Part C1** |
| `review_link_candidates` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `link_entities` | 2 | *(relation kind)* | **no — Part C1** |
| `manage_source_trust` | 2 | *(none — dispatch is by argument)* | **no — Part C1** |
| `mine_knowledge_from_session` | 2 | *(none — dispatch is by argument)* | **no — Part C1** |
| `supersede_knowledge` | 2 | *(link policy)* | **no — Part C1** |

**18 merged targets. 2 of them have a method behind them today.** The other **16** are Phase 2 work, not renames.

## Part B — every served name and its single destination (81 live + 18 retired)

| # | Current tool | Source | SDK binding | Read-only | Destination |
|---|---|---|---|---|---|
| 1 | `tortoise_analyze` | `tool_registry.py:831` | **none declared** | yes | `REMOVED` |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:504` | `annotate_operator` | no | `adjust_relationship` |
| 3 | `tortoise_approve_merge` | `tool_registry.py:373` | `approve_merge` | no | `approve_merge` |
| 4 | `tortoise_assess_source` | `tool_registry.py:1019` | `assess_source` | no | `manage_source_trust` |
| 5 | `tortoise_audit` | `tool_registry.py:174` | `audit` | yes | `graph_overview` |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1168` | `backfill_v25` | no | `REMOVED` |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:396` | `belief_timeline` | yes | `check_confidence` |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:444` | `calibrate_summary` | yes | `check_confidence` |
| 9 | `tortoise_check_structure` | `tool_registry.py:141` | `check_structure` | yes | `graph_overview` |
| 10 | `tortoise_checkpoint` | `tool_registry.py:640` | `checkpoint` | no | `mine_knowledge_from_session` |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:407` | `compute_confidence` | yes | `check_confidence` |
| 12 | `tortoise_create_document` | `tool_registry.py:986` | `create_document` | no | `create_entity` |
| 13 | `tortoise_create_edge` | `tool_registry.py:1124` | `create_edge` | no | `link_entities` |
| 14 | `tortoise_create_entity` | `tool_registry.py:1071` | `create_entity` | no | `create_entity` |
| 15 | `tortoise_create_event` | `tool_registry.py:925` | `create_event` | no | `create_entity` |
| 16 | `tortoise_create_object` | `tool_registry.py:916` | `create_object` | no | `create_entity` |
| 17 | `tortoise_create_operator` | `tool_registry.py:492` | `create_operator` | no | `link_entities` |
| 18 | `tortoise_create_point` | `tool_registry.py:103` | `create_point` | no | `create_entity` |
| 19 | `tortoise_create_source` | `tool_registry.py:996` | `create_source` | no | `register_source` |
| 20 | `tortoise_create_subject` | `tool_registry.py:907` | `create_subject` | no | `create_entity` |
| 21 | `tortoise_delete` | `tool_registry.py:1100` | `delete` | no | `delete_knowledge` |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1060` | `delete_entity` | no | `delete_knowledge` |
| 23 | `tortoise_delete_point` | `tool_registry.py:557` | `delete_point_wrapped` | no | `delete_knowledge` |
| 24 | `tortoise_diary_read` | `tool_registry.py:660` | `diary_read` | yes | `list_knowledge` |
| 25 | `tortoise_diary_write` | `tool_registry.py:650` | `diary_write` | no | `create_entity` |
| 26 | `tortoise_dream` | `tool_registry.py:452` | `dream` | no | `refresh_confidence` |
| 27 | `tortoise_dream_health` | `tool_registry.py:469` | `dream_health_check` | yes | `graph_overview` |
| 28 | `tortoise_entity_profile` | `tool_registry.py:620` | **none declared** | yes | `explore_connections` |
| 29 | `tortoise_events_poll` | `tool_registry.py:592` | `poll_events` | yes | `poll_events` |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:311` | `expand_relationships` | yes | `explore_connections` |
| 31 | `tortoise_file_decision` | `tool_registry.py:534` | `file_decision` | no | `write_question` |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:544` | `file_human_approval` | no | `record_decision` |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:867` | `get_cross_lens_candidates` | yes | `review_link_candidates` |
| 34 | `tortoise_get` | `tool_registry.py:1157` | **none declared** | yes | `get_entity` |
| 35 | `tortoise_get_confidence` | `tool_registry.py:436` | `get_confidence` | yes | `check_confidence` |
| 36 | `tortoise_get_entity` | `tool_registry.py:1040` | `get_entity` | yes | `get_entity` |
| 37 | `tortoise_get_events` | `tool_registry.py:934` | `get_events` | yes | `get_entity` |
| 38 | `tortoise_get_governance` | `tool_registry.py:1137` | `get_owned_entities` | yes | `get_entity` |
| 39 | `tortoise_get_operator` | `tool_registry.py:514` | `get_point` | yes | `get_entity` |
| 40 | `tortoise_get_point` | `tool_registry.py:282` | `get_point` | yes | `get_entity` |
| 41 | `tortoise_get_session` | `tool_registry.py:942` | `get_session` | yes | `get_entity` |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:1008` | `get_source_reliability` | no | `list_knowledge` |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:733` | **none declared** | no | `graph_set_recording` |
| 44 | `tortoise_health` | `tool_registry.py:685` | `health` ⚠️ **does not resolve** | yes | `graph_overview` |
| 45 | `tortoise_index_files` | `tool_registry.py:961` | `index_directory` | no | `index_sources_from_directory` |
| 46 | `tortoise_index_sessions` | `tool_registry.py:950` | `index_sessions` | no | `index_sources_from_directory` |
| 47 | `tortoise_ingest` | `tool_registry.py:778` | `ingest` | no | `sdk:write_knowledge_batch` |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:766` | `ingest_corpus` | no | `index_sources_from_directory` |
| 49 | `tortoise_invalidate` | `tool_registry.py:568` | `invalidate_point` | no | `supersede_knowledge` |
| 50 | `tortoise_issue_insight` | `tool_registry.py:752` | `issue_insight` | yes | `search_knowledge` |
| 51 | `tortoise_list_batch` | `tool_registry.py:214` | `list_batch` | yes | `list_knowledge` |
| 52 | `tortoise_list_batches` | `tool_registry.py:226` | `list_batches` | yes | `list_knowledge` |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:364` | `list_dedup_candidates` | yes | `review_link_candidates` |
| 54 | `tortoise_list_graphs` | `tool_registry.py:668` | `list_graphs` | yes | `tenancy:list_memory_graphs` |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:205` | `list_namespaces` | yes | `list_knowledge` |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:189` | `list_pointkinds` | yes | `graph_overview` |
| 57 | `tortoise_list_sources` | `tool_registry.py:197` | `list_sources` | yes | `graph_overview` |
| 58 | `tortoise_list_tags` | `tool_registry.py:264` | `list_tags` | yes | `graph_overview` |
| 59 | `tortoise_list_topics` | `tool_registry.py:820` | `list_topics` | yes | `list_knowledge` |
| 60 | `tortoise_manage_sources` | `tool_registry.py:1212` | **none declared** | no | `manage_sources` |
| 61 | `tortoise_mine_conversations` | `tool_registry.py:350` | `mine_corpus` | no | `mine_knowledge_from_directory` |
| 62 | `tortoise_mitigate_operator` | `tool_registry.py:523` | `mitigate_operator` | no | `adjust_relationship` |
| 63 | `tortoise_onboarding_demo_create` | `tool_registry.py:1179` | **none declared** | no | `REMOVED` |
| 64 | `tortoise_onboarding_github_connect` | `tool_registry.py:1254` | **none declared** | no | `REMOVED` |
| 65 | `tortoise_onboarding_github_index` | `tool_registry.py:1264` | **none declared** | no | `manage_sources` |
| 66 | `tortoise_onboarding_github_status` | `tool_registry.py:1274` | **none declared** | yes | `REMOVED` |
| 67 | `tortoise_onboarding_seed` | `tool_registry.py:1198` | **none declared** | no | `REMOVED` |
| 68 | `tortoise_onboarding_session_recording` | `tool_registry.py:1244` | **none declared** | no | `manage_sources` |
| 69 | `tortoise_onboarding_state` | `tool_registry.py:1189` | **none declared** | yes | `REMOVED` |
| 70 | `tortoise_operator_action` | `tool_registry.py:1112` | **none declared** | no | `adjust_relationship` |
| 71 | `tortoise_org_create` | `tool_registry.py:895` | `org_create` | no | `tenancy:create_memory_graph` |
| 72 | `tortoise_overview` | `tool_registry.py:1146` | **none declared** | yes | `graph_overview` |
| 73 | `tortoise_pack_install` | `tool_registry.py:247` | **none declared** | no | `REMOVED` |
| 74 | `tortoise_packs_list` | `tool_registry.py:236` | **none declared** | yes | `REMOVED` |
| 75 | `tortoise_paginated_query` | `tool_registry.py:132` | `paginated_query` | yes | `search_knowledge` |
| 76 | `tortoise_promote_point` | `tool_registry.py:384` | `promote_point` | no | `refresh_confidence` ⚠️ |
| 77 | `tortoise_provenance` | `tool_registry.py:885` | `provenance` | yes | `check_confidence` |
| 78 | `tortoise_query` | `tool_registry.py:119` | `query` | yes | `search_knowledge` |
| 79 | `tortoise_query_points_by_tag` | `tool_registry.py:272` | `query_points_by_tag` | yes | `search_knowledge` |
| 80 | `tortoise_recall` | `tool_registry.py:322` | `recall_state` | yes | `check_confidence` |
| 81 | `tortoise_retract_point` | `tool_registry.py:607` | `retract_point` | no | `update_knowledge` |
| 82 | `tortoise_review_connections` | `tool_registry.py:849` | `review_connections` | yes | `review_link_candidates` |
| 83 | `tortoise_search` | `tool_registry.py:299` | `tortoise_fts_query` | yes | `search_knowledge` |
| 84 | `tortoise_search_sessions` | `tool_registry.py:978` | `search_sessions` | yes | `search_knowledge` |
| 85 | `tortoise_session_capture` | `tool_registry.py:703` | **none declared** | no | `mine_knowledge_from_session` |
| 86 | `tortoise_session_context` | `tool_registry.py:693` | `session_context` | yes | `check_confidence` |
| 87 | `tortoise_set_point_baseline` | `tool_registry.py:427` | `set_point_baseline` | no | `refresh_confidence` ⚠️ |
| 88 | `tortoise_set_source_tier` | `tool_registry.py:1030` | `set_source_tier` | no | `manage_source_trust` |
| 89 | `tortoise_stale` | `tool_registry.py:841` | `stale_points` | yes | `graph_overview` |
| 90 | `tortoise_status` | `tool_registry.py:676` | `status` | yes | `graph_overview` |
| 91 | `tortoise_suggest_entry_points` | `tool_registry.py:290` | `suggest_entry_points` | yes | `search_knowledge` |
| 92 | `tortoise_summarize_structure` | `tool_registry.py:161` | `summarize_structure` | yes | `graph_overview` |
| 93 | `tortoise_supersede` | `tool_registry.py:578` | `supersede` | no | `supersede_knowledge` |
| 94 | `tortoise_taxonomy` | `tool_registry.py:810` | `taxonomy` | yes | `graph_overview` |
| 95 | `tortoise_traverse` | `tool_registry.py:630` | **none declared** | yes | `explore_connections` |
| 96 | `tortoise_update` | `tool_registry.py:1088` | `update` | no | `update_knowledge` |
| 97 | `tortoise_update_entity` | `tool_registry.py:1051` | `update_entity` | no | `update_knowledge` |
| 98 | `tortoise_update_point` | `tool_registry.py:482` | `update_point` | no | `update_knowledge` |
| 99 | `tortoise_validate_domain` | `tool_registry.py:149` | `validate_domain` | yes | `graph_overview` |

**18** of these are RETIRED names (#3883): off the advertised surface, but they
still answer through the warning shim, and each one's `Destination` is the destination of
the replacement that warning names. The other **81** are live.

Listed so a reader can tell them apart from the live rows that share their destination: `tortoise_get`, `tortoise_get_events`, `tortoise_get_governance`, `tortoise_get_operator`, `tortoise_get_point`, `tortoise_health`, `tortoise_index_sessions`, `tortoise_ingest_corpus`, `tortoise_list_pointkinds`, `tortoise_list_sources`, `tortoise_list_tags`, `tortoise_onboarding_github_index`, `tortoise_onboarding_session_recording`, `tortoise_paginated_query`, `tortoise_query_points_by_tag`, `tortoise_stale`, `tortoise_status`, `tortoise_taxonomy`

**A `⚠️` after a destination means the sibling SDK rename table**
**(`docs/product/sdk-rename-table.md` (generated on demand) §C3b, and its C6 fold record) records that**
**destination as WRONG.** The map is owner-approved, so it is NOT edited here; §D2c states
the documented reading and the authority for it.

### Destination counts

| Destination | Count |
|---|---|
| `graph_overview` | 13 |
| `REMOVED` | 9 |
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
| **total** | **99** |

Destination rows: **29**. Registry tools: **99**.

## Part C — blockers

### C1 — target tools with NO method on `TortoiseSDK`

| Target tool | Where | Finding |
|---|---|---|
| `search_knowledge` | MCP | no `def` on TortoiseSDK |
| `list_knowledge` | MCP | no `def` on TortoiseSDK |
| `check_confidence` | MCP | no `def` on TortoiseSDK |
| `explore_connections` | MCP | no `def` on TortoiseSDK |
| `graph_overview` | MCP | no `def` on TortoiseSDK |
| `review_link_candidates` | MCP | no `def` on TortoiseSDK |
| `check_connection` | MCP | no `def` on TortoiseSDK |
| `register_source` | MCP | no `def` on TortoiseSDK |
| `index_sources_from_directory` | MCP | no `def` on TortoiseSDK |
| `manage_sources` | MCP | no `def` on TortoiseSDK |
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
| `tortoise_health` | `health` | `tool_registry.py:685` |

A tool that declares a binding to a method **that is not a `def` on `TortoiseSDK`** is a
declaration that cannot be honoured. It fails silently today because nothing checks it.

## Part D — destination citations

The `Destination` column's evidence is the beta doc's own disposition row, quoted
**in full**. The quote is not decoration. A read that stops at the clause agreeing with
the row drops the clause that contradicts it, and because a truncated prefix of a real
sentence is still a real substring, a `quote in text` test passes while the evidence has
been edited to agree with the row. **Every quote below is the whole disposition ROW, read
at build time** — a truncation is not merely detected, it is *impossible to construct*,
because there is no slicing step: the row **is** the quote. (The authored FOLD hop below
is hand-typed, so the generator additionally rejects it with a maximality check.)

**19 citations cover 39 of the 99 registry rows.** The other **60**
are map decisions with no disposition row in the doc to cite — a net-new target, or a row
that table does not carry.

### D1 — the citation corpus

- `beta-sdk-surface.md` · rows `tortoise_assess_source`, `tortoise_get_source_reliability`, `tortoise_set_source_tier`
  > | `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |
- `beta-sdk-surface.md` · rows `tortoise_audit`, `tortoise_dream_health`, `tortoise_summarize_structure`, `tortoise_validate_domain`
  > | `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` where they are orientation. The diagnostics are the held question above. |
- `beta-sdk-surface.md` · rows `tortoise_create_operator`
  > | `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. |
- `beta-sdk-surface.md` · rows `tortoise_delete_point`
  > | `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |
- `beta-sdk-surface.md` · rows `tortoise_file_human_approval`
  > | `file_human_approval` | 1 | → `record_decision`. |
- `beta-sdk-surface.md` · rows `tortoise_ingest_corpus`
  > | `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources_from_directory`. |
- `beta-sdk-surface.md` · rows `tortoise_list_batch`, `tortoise_list_batches`
  > | `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. |
- `beta-sdk-surface.md` · rows `tortoise_mine_conversations`
  > | `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. |
- `beta-sdk-surface.md` · rows `tortoise_annotate_operator`, `tortoise_mitigate_operator`
  > | `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. |
- `beta-sdk-surface.md` · rows `tortoise_belief_timeline`, `tortoise_provenance`, `tortoise_session_context`
  > | `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. |
- `beta-sdk-surface.md` · rows `tortoise_paginated_query`, `tortoise_query`, `tortoise_query_points_by_tag`
  > | `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |
- `beta-sdk-surface.md` · rows `tortoise_calibrate_summary`, `tortoise_recall`
  > | `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |
- `beta-sdk-surface.md` · rows `tortoise_invalidate`, `tortoise_retract_point`
  > | `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. |
- `beta-sdk-surface.md` · rows `tortoise_find_cross_lens_candidates`, `tortoise_list_dedup_candidates`, `tortoise_review_connections`
  > | `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |
- `beta-sdk-surface.md` · rows `tortoise_issue_insight`, `tortoise_search_sessions`, `tortoise_suggest_entry_points`
  > | `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |
- `beta-sdk-surface.md` · rows `tortoise_supersede`
  > | `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |
- `beta-sdk-surface.md` · rows `tortoise_expand_relationships`
  > | `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |
- `beta-sdk-surface.md` · rows `tortoise_update_entity`, `tortoise_update_point`
  > | `update_point`, `update_entity` | 2 | → `update_knowledge`. |
- `beta-sdk-surface.md` · rows `tortoise_get_events`, `tortoise_get_governance`, `tortoise_get_session`
  > | narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |

**Documented hops.** A citation can name something that is not a target because it is
itself folded one hop further. The hop is stated in the doc, so it is carried as a
citation of its own — checked by the same rule — rather than assumed. Without it
`tortoise_get_source_reliability`'s row reads as unsupported, which is not a finding.

- `list_sources` → `list_knowledge`
  > | `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |

**4 rows disagree with their own citation; 1 are supported only
beyond the first clause; 8 sit under an ambiguous citation.** Every count
here is computed from the doc, not typed.

### D2 — citations that do NOT name their row's destination

**These are findings, not edits.** A row whose destination is not named by the row's own
full citation is the `get_source_reliability` failure mode read one level up — the row and
its evidence disagree. The destination map is owner-approved, so the disagreement is
reported here with its evidence and the mapping is left ALONE. Changing an owner-approved
destination is not a build step.

**D2 is a LOWER BOUND, and reads that way on purpose.** Its predicate is exhaustive — every
row whose destination is named *nowhere* in its citation is listed. What it cannot decide
is clause ATTRIBUTION. `tortoise_assess_source` is the concrete case: its citation names the
setter's `manage_source_trust` first and the reader's `list_sources` second, and the map
puts it on the setter's target — a reading of which clause applies, not a computation. Such
rows are visible in D3, not here, and are not counted as disagreements.

- **`tortoise_invalidate`** — map says `supersede_knowledge`; citation names `update_knowledge`
  > | `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. |
- **`tortoise_paginated_query`** — map says `search_knowledge`; citation names `list_knowledge`
  > | `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |
- **`tortoise_query`** — map says `search_knowledge`; citation names `list_knowledge`
  > | `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |
- **`tortoise_query_points_by_tag`** — map says `search_knowledge`; citation names `list_knowledge`
  > | `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |

### D2b — rows whose support exists ONLY beyond the first clause

These rows are **why the first-clause split is load-bearing, not decorative**. Their
destination is named by the citation, but only in a clause after the first `→` — the
exact point a truncated read would stop. A read that took only the first clause would
lose the row's whole support, silently — so the generator computes the first-clause names
(`_prefix_targets`) and surfaces any discrepancy as this list.

- **`tortoise_get_source_reliability`** — map says `list_knowledge`; the first clause names `manage_source_trust`, the full citation names `manage_source_trust`, `list_knowledge`
  > | `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |

### D2c — destinations the sibling SDK rename table records as WRONG

`docs/product/sdk-rename-table.md` (generated on demand) reconciles the same surface this file maps, and its
§C3b finding plus its C6 fold record name a different destination for the rows below.
**The destination map here is owner-approved, so it is reported, not edited** — the same
rule D2 states. Each row's documented reading and the authority for it are shown, so the
disagreement is visible at the row instead of only in the sibling artifact.

- **`tortoise_promote_point`** — map says `refresh_confidence`; the documented reading is `update_knowledge`
  > The owner-approved MCP list absorbs `promote_point` into `revise_knowledge`, whose beta successor is `update_knowledge`; beta names the new status a FIELD on `update_knowledge`, and promote's incident-operator cascade and approval gate ride with it — `refresh_confidence` recomputes confidence and covers neither.
- **`tortoise_set_point_baseline`** — map says `refresh_confidence`; the documented reading is `update_knowledge`
  > Same approved absorption as `tortoise_promote_point`; beta names the starting belief a FIELD on `update_knowledge`, not a separate verb.

### D3 — citations that name more than one target

A citation here does not by itself determine a destination: it names several, split by
prose (`; reads via …`, `where they are …`, `for annotation`). Which clause applies to
which method is a reading, not a computation — so the **full** quote is rendered for these
rows in D1, where the clause a truncated read would have dropped is visible.

| Row | Destination (map) | Citation names | First clause names |
|---|---|---|---|
| `tortoise_annotate_operator` | `adjust_relationship` | `adjust_relationship`, `update_knowledge` | `adjust_relationship`, `update_knowledge` |
| `tortoise_assess_source` | `manage_source_trust` | `manage_source_trust`, `list_knowledge` | `manage_source_trust` |
| `tortoise_belief_timeline` | `check_confidence` | `check_confidence`, `poll_events` | `check_confidence` |
| `tortoise_get_source_reliability` | `list_knowledge` | `manage_source_trust`, `list_knowledge` | `manage_source_trust` |
| `tortoise_mitigate_operator` | `adjust_relationship` | `adjust_relationship`, `update_knowledge` | `adjust_relationship`, `update_knowledge` |
| `tortoise_provenance` | `check_confidence` | `check_confidence`, `poll_events` | `check_confidence` |
| `tortoise_session_context` | `check_confidence` | `check_confidence`, `poll_events` | `check_confidence` |
| `tortoise_set_source_tier` | `manage_source_trust` | `manage_source_trust`, `list_knowledge` | `manage_source_trust` |

### D4 — what the OWNER-APPROVED menu says about every disputed row (#4513)

Part D above reads `beta-sdk-surface.md`. This section reads the OTHER owner-approved
doc — `canonical-mcp-tools.md` (`approval_status: approved`, PR #4120) — whose `Absorbs`
cell is the owner's own record of **which current name folds into which target**. That
binding was never consulted here, so a row could contradict the approved menu and the
build stayed green (#4513 §1/§2).

**Nothing is decided here.** Where the two approved docs disagree, the ruling is the
owner's and the map is NOT edited — the disagreement is shown with BOTH citations so it
is visible at the row. What the approved menu *can* settle mechanically is narrower:
does a target the row's citation names actually absorb this method?

**Scope, stated exactly:** these are the rows Part D above already flags (D2/D2b/D3).
A row Part D does not flag is NOT here even when the approved menu disagrees with it —
covering every cited row is the whole-`Destination` derivation in #4518/#5063, which
this section deliberately does not pre-empt. The sibling
`docs/product/sdk-rename-table.md` (§C3b) reads these rows from beta's prose ALONE and
states a winner on several of them; this section is the other approved doc's side of the
same rows, not a correction of it.

- **`row`** — the target is an approved row that absorbs this method: the approved doc
  itself places the method there.
- **`successor`** — the target is one the absorbing row's cell names as beta's own split
  of that row, so the row supports it (one of several successors).
- **`refuted`** — an approved row (or a successor of one) covers this target and does
  **not** absorb this method: the approved doc does not place the method there. (It
  does not claim the doc places it SOMEWHERE ELSE — a method the doc never absorbs at
  all reads `refuted` too, and its evidence line below says so.)
- **`rehomed`** — the approved doc **does** absorb this method, but into a different row
  than this map's destination, and the destination is neither an approved row nor a
  name any row's beta split states. Read with `refuted`: the two approved docs
  disagree. It is NOT `unstated` — the `Approved home` cell and the quoted row below
  name the method, so the approved menu does answer it.
- **`unstated`** — the approved doc names neither this target nor any row that absorbs
  this method, so it neither supports nor refutes it. A row resting only on `unstated` is
  genuinely owner-open.

The membership set is the doc's OWN closure, parsed from its prose:
`get_events`/`get_governance`/`get_operator`/`get_point` `sit inside` `get`
`health`/`list_graphs`/`list_namespaces`/`list_pointkinds`/`list_sources`/`list_tags`/`list_topics`/`stale`/`status`/`taxonomy` `sit inside` `overview`
`paginated_query`/`query_points_by_tag` `sit inside` `query`
`index_sessions`/`ingest_corpus` `sit inside` `index_files`

A name it collapses without listing still resolves, and a closure the parse drops
fails the build. The lines above are emitted from that parse — not typed — so this
paragraph cannot describe a closure the membership does not use.

| Row | Map destination | Approved home | Home's beta successors | Map destination reads | Citation rivals read |
|---|---|---|---|---|---|
| `tortoise_annotate_operator` | `adjust_relationship` | `adjust_relationship` (row 21) | *(none)* | **row** | `update_knowledge` — **refuted** |
| `tortoise_assess_source` | `manage_source_trust` | `manage_source_trust` (row 14) | *(none)* | **row** | `list_knowledge` — **refuted** |
| `tortoise_belief_timeline` | `check_confidence` | `recall_beliefs` (row 3) | *(none)* | **rehomed** | `poll_events` — **refuted** |
| `tortoise_get_source_reliability` | `list_knowledge` | `manage_source_trust` (row 14) | *(none)* | **refuted** | `manage_source_trust` — **row** |
| `tortoise_invalidate` | `supersede_knowledge` | `revise_knowledge` (row 17) | `supersede_knowledge`, `update_knowledge` | **successor** | `update_knowledge` — **successor** |
| `tortoise_mitigate_operator` | `adjust_relationship` | `adjust_relationship` (row 21) | *(none)* | **row** | `update_knowledge` — **refuted** |
| `tortoise_paginated_query` | `search_knowledge` | `list_knowledge` (row 2) | *(none)* | **refuted** | `list_knowledge` — **row** |
| `tortoise_provenance` | `check_confidence` | `recall_beliefs` (row 3) | *(none)* | **rehomed** | `poll_events` — **refuted** |
| `tortoise_query` | `search_knowledge` | `list_knowledge` (row 2) | *(none)* | **refuted** | `list_knowledge` — **row** |
| `tortoise_query_points_by_tag` | `search_knowledge` | `list_knowledge` (row 2) | *(none)* | **refuted** | `list_knowledge` — **row** |
| `tortoise_session_context` | `check_confidence` | `recall_beliefs` (row 3) | *(none)* | **rehomed** | `poll_events` — **refuted** |
| `tortoise_set_source_tier` | `manage_source_trust` | `manage_source_trust` (row 14) | *(none)* | **row** | `list_knowledge` — **refuted** |

**5 of 12 disputed rows are placed by the approved menu where this
map places them; 7 are placed ELSEWHERE by it; 0 have
no place in it at all.** Every count is computed from the doc, not typed.

- **The approved menu contradicts the map on**: `tortoise_get_source_reliability`, `tortoise_paginated_query`, `tortoise_query`, `tortoise_query_points_by_tag` — an approved row (or a successor of one) covers the map's destination and does **not** absorb the method, while another row does. Two owner-approved docs disagreeing is an owner ruling, not a build step, so the destinations stand and the conflict is recorded here.
- **The approved menu absorbs the method in a DIFFERENT row than the map's destination** (and the destination is neither an approved row nor one any beta split names): `tortoise_belief_timeline`, `tortoise_provenance`, `tortoise_session_context` — the same disagreement as the sentence above, reached through a different door: the method is NOT unnamed, its approved home is in the table and the whole approved row is quoted below.

**The approved row itself, for each disputed row that has one** (whole table row, by
construction, so it cannot be a truncation of what the doc says):

- **`tortoise_annotate_operator`** — `annotate_operator` is absorbed by `adjust_relationship`:
  > | 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |
- **`tortoise_assess_source`** — `assess_source` is absorbed by `manage_source_trust`:
  > | 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |
- **`tortoise_belief_timeline`** — `belief_timeline` is absorbed by `recall_beliefs`:
  > | 3 | `recall_beliefs` | recall, belief_timeline, provenance, session_context, get_confidence, calibrate_summary |
- **`tortoise_get_source_reliability`** — `get_source_reliability` is absorbed by `manage_source_trust`:
  > | 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |
- **`tortoise_invalidate`** — `invalidate` (SDK `invalidate_point`) is absorbed by `revise_knowledge`:
  > | 17 | `revise_knowledge` | update, update_point, update_entity, supersede, invalidate, retract_point, promote_point, set_point_baseline — the beta target **splits this into `update_knowledge` + `supersede_knowledge`**; promote/baseline become **fields on `update_knowledge`** |
- **`tortoise_mitigate_operator`** — `mitigate_operator` is absorbed by `adjust_relationship`:
  > | 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |
- **`tortoise_paginated_query`** — `paginated_query` is absorbed by `list_knowledge`:
  > | 2 | `list_knowledge` | query |
- **`tortoise_provenance`** — `provenance` is absorbed by `recall_beliefs`:
  > | 3 | `recall_beliefs` | recall, belief_timeline, provenance, session_context, get_confidence, calibrate_summary |
- **`tortoise_query`** — `query` is absorbed by `list_knowledge`:
  > | 2 | `list_knowledge` | query |
- **`tortoise_query_points_by_tag`** — `query_points_by_tag` is absorbed by `list_knowledge`:
  > | 2 | `list_knowledge` | query |
- **`tortoise_session_context`** — `session_context` is absorbed by `recall_beliefs`:
  > | 3 | `recall_beliefs` | recall, belief_timeline, provenance, session_context, get_confidence, calibrate_summary |
- **`tortoise_set_source_tier`** — `set_source_tier` is absorbed by `manage_source_trust`:
  > | 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |

---

## Reproduce

```bash
uv run python tools/bridge_table.py          # regenerate this file
uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift
```
