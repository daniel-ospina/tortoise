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

## Part B — every served name and its single destination (82 live + 16 retired)

| # | Current tool | Source | SDK binding | Read-only | Destination |
|---|---|---|---|---|---|
| 1 | `tortoise_analyze` | `tool_registry.py:811` | **none declared** | yes | `REMOVED` |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:495` | `annotate_operator` | no | `adjust_relationship` |
| 3 | `tortoise_approve_merge` | `tool_registry.py:367` | `approve_merge` | no | `approve_merge` |
| 4 | `tortoise_assess_source` | `tool_registry.py:996` | `assess_source` | no | `manage_source_trust` |
| 5 | `tortoise_audit` | `tool_registry.py:168` | `audit` | yes | `graph_overview` |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1139` | `backfill_v25` | no | `REMOVED` |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:390` | `belief_timeline` | yes | `check_confidence` |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:438` | `calibrate_summary` | yes | `check_confidence` |
| 9 | `tortoise_check_structure` | `tool_registry.py:135` | `check_structure` | yes | `graph_overview` |
| 10 | `tortoise_checkpoint` | `tool_registry.py:628` | `checkpoint` | no | `REMOVED` |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:401` | `compute_confidence` | yes | `check_confidence` |
| 12 | `tortoise_create_document` | `tool_registry.py:964` | `create_document` | no | `create_entity` |
| 13 | `tortoise_create_edge` | `tool_registry.py:1095` | `create_edge` | no | `link_entities` |
| 14 | `tortoise_create_entity` | `tool_registry.py:1048` | `create_entity` | no | `create_entity` |
| 15 | `tortoise_create_event` | `tool_registry.py:904` | `create_event` | no | `create_entity` |
| 16 | `tortoise_create_object` | `tool_registry.py:895` | `create_object` | no | `create_entity` |
| 17 | `tortoise_create_operator` | `tool_registry.py:485` | `create_operator` | no | `link_entities` |
| 18 | `tortoise_create_point` | `tool_registry.py:100` | `create_point` | no | `create_entity` |
| 19 | `tortoise_create_source` | `tool_registry.py:973` | `create_source` | no | `register_source` |
| 20 | `tortoise_create_subject` | `tool_registry.py:886` | `create_subject` | no | `create_entity` |
| 21 | `tortoise_delete` | `tool_registry.py:1071` | `delete` | no | `delete_knowledge` |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1037` | `delete_entity` | no | `delete_knowledge` |
| 23 | `tortoise_delete_point` | `tool_registry.py:548` | `delete_point_wrapped` | no | `delete_knowledge` |
| 24 | `tortoise_diary_read` | `tool_registry.py:648` | `diary_read` | yes | `REMOVED` |
| 25 | `tortoise_diary_write` | `tool_registry.py:638` | `diary_write` | no | `REMOVED` |
| 26 | `tortoise_dream` | `tool_registry.py:446` | `dream` | no | `refresh_confidence` |
| 27 | `tortoise_dream_health` | `tool_registry.py:462` | `dream_health_check` | yes | `graph_overview` |
| 28 | `tortoise_entity_profile` | `tool_registry.py:608` | **none declared** | yes | `explore_connections` |
| 29 | `tortoise_events_poll` | `tool_registry.py:583` | `events_poll` | yes | `poll_events` |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:305` | `expand_relationships` | yes | `explore_connections` |
| 31 | `tortoise_file_decision` | `tool_registry.py:525` | `file_decision` | no | `write_question` |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:535` | `file_human_approval` | no | `record_decision` |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:847` | `get_cross_lens_candidates` | yes | `review_link_candidates` |
| 34 | `tortoise_get` | `tool_registry.py:1128` | **none declared** | yes | `get_entity` |
| 35 | `tortoise_get_confidence` | `tool_registry.py:430` | `get_confidence` | yes | `check_confidence` |
| 36 | `tortoise_get_entity` | `tool_registry.py:1017` | `get_entity` | yes | `get_entity` |
| 37 | `tortoise_get_events` | `tool_registry.py:913` | `get_events` | yes | `get_entity` |
| 38 | `tortoise_get_governance` | `tool_registry.py:1108` | `get_owned_entities` | yes | `get_entity` |
| 39 | `tortoise_get_operator` | `tool_registry.py:505` | `get_point` | yes | `get_entity` |
| 40 | `tortoise_get_point` | `tool_registry.py:276` | `get_point` | yes | `get_entity` |
| 41 | `tortoise_get_session` | `tool_registry.py:921` | `get_session` | yes | `get_entity` |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:985` | `get_source_reliability` | no | `list_knowledge` |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:717` | **none declared** | no | `graph_set_recording` |
| 44 | `tortoise_health` | `tool_registry.py:673` | `health` ⚠️ **does not resolve** | yes | `graph_overview` |
| 45 | `tortoise_index_files` | `tool_registry.py:939` | `index_directory` | no | `index_sources_from_directory` |
| 46 | `tortoise_index_sessions` | `tool_registry.py:929` | `index_sessions` | no | `index_sources_from_directory` |
| 47 | `tortoise_ingest` | `tool_registry.py:760` | `ingest` | no | `sdk:write_knowledge_batch` |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:750` | `ingest_corpus` | no | `index_sources_from_directory` |
| 49 | `tortoise_invalidate` | `tool_registry.py:559` | `invalidate_point` | no | `supersede_knowledge` |
| 50 | `tortoise_issue_insight` | `tool_registry.py:736` | `issue_insight` | yes | `search_knowledge` |
| 51 | `tortoise_list_batch` | `tool_registry.py:208` | `list_batch` | yes | `list_knowledge` |
| 52 | `tortoise_list_batches` | `tool_registry.py:220` | `list_batches` | yes | `list_knowledge` |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:358` | `list_dedup_candidates` | yes | `review_link_candidates` |
| 54 | `tortoise_list_graphs` | `tool_registry.py:656` | `list_graphs` | yes | `tenancy:list_memory_graphs` |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:199` | `list_namespaces` | yes | `list_knowledge` |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:183` | `list_pointkinds` | yes | `graph_overview` |
| 57 | `tortoise_list_sources` | `tool_registry.py:191` | `list_sources` | yes | `graph_overview` |
| 58 | `tortoise_list_tags` | `tool_registry.py:258` | `list_tags` | yes | `graph_overview` |
| 59 | `tortoise_list_topics` | `tool_registry.py:800` | `list_topics` | yes | `list_knowledge` |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:344` | `mine_corpus` | no | `mine_knowledge_from_directory` |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:514` | `mitigate_operator` | no | `adjust_relationship` |
| 62 | `tortoise_onboarding_demo_create` | `tool_registry.py:1149` | **none declared** | no | `REMOVED` |
| 63 | `tortoise_onboarding_github_connect` | `tool_registry.py:1192` | **none declared** | no | `REMOVED` |
| 64 | `tortoise_onboarding_github_index` | `tool_registry.py:1202` | **none declared** | no | `REMOVED` |
| 65 | `tortoise_onboarding_github_status` | `tool_registry.py:1212` | **none declared** | yes | `REMOVED` |
| 66 | `tortoise_onboarding_seed` | `tool_registry.py:1168` | **none declared** | no | `REMOVED` |
| 67 | `tortoise_onboarding_session_recording` | `tool_registry.py:1182` | **none declared** | no | `REMOVED` |
| 68 | `tortoise_onboarding_state` | `tool_registry.py:1159` | **none declared** | yes | `REMOVED` |
| 69 | `tortoise_operator_action` | `tool_registry.py:1083` | `operator_action` | no | `adjust_relationship` |
| 70 | `tortoise_org_create` | `tool_registry.py:875` | `org_create` | no | `tenancy:create_memory_graph` |
| 71 | `tortoise_overview` | `tool_registry.py:1117` | **none declared** | yes | `graph_overview` |
| 72 | `tortoise_pack_install` | `tool_registry.py:241` | **none declared** | no | `REMOVED` |
| 73 | `tortoise_packs_list` | `tool_registry.py:230` | **none declared** | yes | `REMOVED` |
| 74 | `tortoise_paginated_query` | `tool_registry.py:126` | `paginated_query` | yes | `search_knowledge` |
| 75 | `tortoise_promote_point` | `tool_registry.py:378` | `promote_point` | no | `refresh_confidence` ⚠️ |
| 76 | `tortoise_provenance` | `tool_registry.py:865` | `provenance` | yes | `check_confidence` |
| 77 | `tortoise_query` | `tool_registry.py:113` | `query` | yes | `search_knowledge` |
| 78 | `tortoise_query_points_by_tag` | `tool_registry.py:266` | `query_points_by_tag` | yes | `search_knowledge` |
| 79 | `tortoise_recall` | `tool_registry.py:316` | `recall_state` | yes | `check_confidence` |
| 80 | `tortoise_retract_point` | `tool_registry.py:595` | `retract_point` | no | `update_knowledge` |
| 81 | `tortoise_review_connections` | `tool_registry.py:829` | `review_connections` | yes | `review_link_candidates` |
| 82 | `tortoise_search` | `tool_registry.py:293` | `tortoise_fts_query` | yes | `search_knowledge` |
| 83 | `tortoise_search_sessions` | `tool_registry.py:956` | `search_sessions` | yes | `search_knowledge` |
| 84 | `tortoise_session_capture` | `tool_registry.py:691` | **none declared** | no | `mine_knowledge_from_session` |
| 85 | `tortoise_session_context` | `tool_registry.py:681` | `session_context` | yes | `check_confidence` |
| 86 | `tortoise_set_point_baseline` | `tool_registry.py:421` | `set_point_baseline` | no | `refresh_confidence` ⚠️ |
| 87 | `tortoise_set_source_tier` | `tool_registry.py:1007` | `set_source_tier` | no | `manage_source_trust` |
| 88 | `tortoise_stale` | `tool_registry.py:821` | `stale_points` | yes | `graph_overview` |
| 89 | `tortoise_status` | `tool_registry.py:664` | `status` | yes | `graph_overview` |
| 90 | `tortoise_suggest_entry_points` | `tool_registry.py:284` | `suggest_entry_points` | yes | `search_knowledge` |
| 91 | `tortoise_summarize_structure` | `tool_registry.py:155` | `summarize_structure` | yes | `graph_overview` |
| 92 | `tortoise_supersede` | `tool_registry.py:569` | `supersede` | no | `supersede_knowledge` |
| 93 | `tortoise_taxonomy` | `tool_registry.py:791` | `taxonomy` | yes | `graph_overview` |
| 94 | `tortoise_traverse` | `tool_registry.py:618` | `traverse` | yes | `explore_connections` |
| 95 | `tortoise_update` | `tool_registry.py:1060` | `update` | no | `update_knowledge` |
| 96 | `tortoise_update_entity` | `tool_registry.py:1028` | `update_entity` | no | `update_knowledge` |
| 97 | `tortoise_update_point` | `tool_registry.py:475` | `update_point` | no | `update_knowledge` |
| 98 | `tortoise_validate_domain` | `tool_registry.py:143` | `validate_domain` | yes | `graph_overview` |

**16** of these are RETIRED names (#3883): off the advertised surface, but they
still answer through the warning shim, and each one's `Destination` is the destination of
the replacement that warning names. The other **82** are live.

Listed so a reader can tell them apart from the live rows that share their destination: `tortoise_get`, `tortoise_get_events`, `tortoise_get_governance`, `tortoise_get_operator`, `tortoise_get_point`, `tortoise_health`, `tortoise_index_sessions`, `tortoise_ingest_corpus`, `tortoise_list_pointkinds`, `tortoise_list_sources`, `tortoise_list_tags`, `tortoise_paginated_query`, `tortoise_query_points_by_tag`, `tortoise_stale`, `tortoise_status`, `tortoise_taxonomy`

**A `⚠️` after a destination means the sibling SDK rename table**
**(`docs/product/sdk-rename-table.md` §C3b, and its C6 fold record) records that**
**destination as WRONG.** The map is owner-approved, so it is NOT edited here; §D2c states
the documented reading and the authority for it.

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
| `tortoise_health` | `health` | `tool_registry.py:673` |

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

**19 citations cover 41 of the 98 registry rows.** The other **57**
are map decisions with no disposition row in the doc to cite — a net-new target, or a row
that table does not carry.

#### D1 — the citation corpus

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
- `beta-sdk-surface.md` · rows `tortoise_annotate_operator`, `tortoise_mitigate_operator`, `tortoise_operator_action`
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
- `beta-sdk-surface.md` · rows `tortoise_expand_relationships`, `tortoise_traverse`
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
beyond the first clause; 9 sit under an ambiguous citation.** Every count
here is computed from the doc, not typed.

#### D2 — citations that do NOT name their row's destination

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

#### D2b — rows whose support exists ONLY beyond the first clause

These rows are **why the first-clause split is load-bearing, not decorative**. Their
destination is named by the citation, but only in a clause after the first `→` — the
exact point a truncated read would stop. A read that took only the first clause would
lose the row's whole support, silently — so the generator computes the first-clause names
(`_prefix_targets`) and surfaces any discrepancy as this list.

- **`tortoise_get_source_reliability`** — map says `list_knowledge`; the first clause names `manage_source_trust`, the full citation names `manage_source_trust`, `list_knowledge`
  > | `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |

#### D2c — destinations the sibling SDK rename table records as WRONG

`docs/product/sdk-rename-table.md` reconciles the same surface this file maps, and its
§C3b finding plus its C6 fold record name a different destination for the rows below.
**The destination map here is owner-approved, so it is reported, not edited** — the same
rule D2 states. Each row's documented reading and the authority for it are shown, so the
disagreement is visible at the row instead of only in the sibling artifact.

- **`tortoise_promote_point`** — map says `refresh_confidence`; the documented reading is `update_knowledge`
  > The owner-approved MCP list absorbs `promote_point` into `revise_knowledge`, whose beta successor is `update_knowledge`; beta names the new status a FIELD on `update_knowledge`, and promote's incident-operator cascade and approval gate ride with it — `refresh_confidence` recomputes confidence and covers neither.
- **`tortoise_set_point_baseline`** — map says `refresh_confidence`; the documented reading is `update_knowledge`
  > Same approved absorption as `tortoise_promote_point`; beta names the starting belief a FIELD on `update_knowledge`, not a separate verb.

#### D3 — citations that name more than one target

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
| `tortoise_operator_action` | `adjust_relationship` | `adjust_relationship`, `update_knowledge` | `adjust_relationship`, `update_knowledge` |
| `tortoise_provenance` | `check_confidence` | `check_confidence`, `poll_events` | `check_confidence` |
| `tortoise_session_context` | `check_confidence` | `check_confidence`, `poll_events` | `check_confidence` |
| `tortoise_set_source_tier` | `manage_source_trust` | `manage_source_trust`, `list_knowledge` | `manage_source_trust` |

---

## Reproduce

```bash
uv run python tools/bridge_table.py          # regenerate this file
uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift
```
