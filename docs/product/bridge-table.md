# Phase 0.1 — the bridge table

**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.

Every `file:line` in this document is **read from the source at build time**, so it cannot
drift from the code it cites. The destination map is data in the generator; every count
below is arithmetic computed against the **served registry** — the live tools plus the
73 retired names, which still answer through the #3883 warning shim. The generator **fails the build**
if the map and the registry disagree — a mismatch is a finding, not something to reconcile.

**Registry: 112 tools → 110 absorbed into the 27 MCP targets · 1 absorbed into a builder-only SDK method (not on the MCP) · 1 tenancy (SDK/REST only) · 0 retired.**

**These are not the same number.** The MCP has **27** tools; **0** current tools retire, **1** are tenancy-only, **1** is absorbed into a builder-only SDK method that is not on the MCP, and **110** are absorbed into those 27 — many-to-one. Writing "112 minus 27 equals 85 retired" conflates the two, and is wrong.

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
| `graph_overview` | 15 | `section=` | yes |
| `check_confidence` | 8 | *(none — dispatch is by argument)* | yes |
| `search_knowledge` | 8 | `mode=` (full-text / hybrid) | yes |
| `create_entity` | 7 | `type=` | yes |
| `get_entity` | 7 | `type=` | yes |
| `list_knowledge` | 7 | `kind=` | yes |
| `update_knowledge` | 5 | *(which fields — incl. the retract fields)* | yes |
| `adjust_relationship` | 4 | *(strength)* | yes |
| `delete_knowledge` | 4 | *(node or link)* | yes |
| `explore_connections` | 4 | *(none — dispatch is by argument)* | yes |
| `index_sources_from_directory` | 4 | *(none — dispatch is by argument)* | yes |
| `refresh_confidence` | 4 | `scope=` | yes |
| `review_link_candidates` | 4 | *(none — dispatch is by argument)* | yes |
| `link_entities` | 3 | *(relation kind)* | yes |
| `manage_source_trust` | 3 | *(none — dispatch is by argument)* | yes |
| `manage_sources` | 3 | *(none — dispatch is by argument)* | **no — Part C1** |
| `mine_knowledge_from_session` | 3 | *(none — dispatch is by argument)* | yes |
| `supersede_knowledge` | 3 | *(link policy)* | yes |
| `mine_knowledge_from_directory` | 2 | *(none — dispatch is by argument)* | yes |
| `poll_events` | 2 | *(none — dispatch is by argument)* | yes |
| `record_decision` | 2 | *(none — dispatch is by argument)* | yes |
| `register_source` | 2 | *(none — dispatch is by argument)* | yes |
| `write_question` | 2 | *(none — dispatch is by argument)* | yes |

**23 merged targets. 22 of them have a method behind them today.** The other **1** are Phase 2 work, not renames.

## Part B — every served name and its single destination (39 live + 73 retired)

**Read-only** is the entry's MCP `readOnlyHint` — what a client uses to auto-approve,
NOT what a key needs. A `no` here does not mean `graphs:write`: since #4122 the tier is
`risk` on the registry entry and `readOnlyHint` is derived from it, so a
`risk="maintenance"` entry (check_confidence, list_knowledge, search_knowledge) reads
`no` while a `graphs:read` key may still call it. Key permission is the derived `writes`
flag; see `canonical-mcp-tools.md` → *reads and writes never share a tool* for the ruling.

| # | Current tool | Source | SDK binding | Read-only | Destination |
|---|---|---|---|---|---|
| 1 | `tortoise_adjust_relationship` | `tool_registry.py:1872` | **none declared** | no | `adjust_relationship` |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:749` | `_annotate_operator` | no | `adjust_relationship` |
| 3 | `tortoise_approve_merge` | `tool_registry.py:586` | `approve_merge` | no | `approve_merge` |
| 4 | `tortoise_assess_source` | `tool_registry.py:1390` | `assess_source` ⚠️ **does not resolve** | no | `manage_source_trust` |
| 5 | `tortoise_audit` | `tool_registry.py:373` | `_audit__svc` | yes | `graph_overview` |
| 6 | `tortoise_belief_timeline` | `tool_registry.py:618` | `_belief_timeline` | yes | `check_confidence` |
| 7 | `tortoise_calibrate_summary` | `tool_registry.py:672` | `_calibrate_summary` | yes | `check_confidence` |
| 8 | `tortoise_check_confidence` | `tool_registry.py:1706` | `check_confidence` | no | `check_confidence` |
| 9 | `tortoise_check_connection` | `tool_registry.py:2135` | `check_connection` | yes | `check_connection` |
| 10 | `tortoise_check_structure` | `tool_registry.py:340` | `_check_structure` | yes | `graph_overview` |
| 11 | `tortoise_checkpoint` | `tool_registry.py:952` | `checkpoint` ⚠️ **does not resolve** | no | `mine_knowledge_from_session` |
| 12 | `tortoise_compute_confidence` | `tool_registry.py:630` | `_compute_confidence` | no | `check_confidence` |
| 13 | `tortoise_create_document` | `tool_registry.py:1337` | `_create_document` | no | `create_entity` |
| 14 | `tortoise_create_edge` | `tool_registry.py:1561` | `_create_edge` | no | `link_entities` |
| 15 | `tortoise_create_entity` | `tool_registry.py:1438` | `create_entity` | no | `create_entity` |
| 16 | `tortoise_create_event` | `tool_registry.py:1273` | `_create_event` | no | `create_entity` |
| 17 | `tortoise_create_object` | `tool_registry.py:1261` | `_create_object` | no | `create_entity` |
| 18 | `tortoise_create_operator` | `tool_registry.py:725` | `_create_operator` | no | `link_entities` |
| 19 | `tortoise_create_point` | `tool_registry.py:267` | `_create_point` | no | `create_entity` |
| 20 | `tortoise_create_source` | `tool_registry.py:1355` | `create_source` ⚠️ **does not resolve** | no | `register_source` |
| 21 | `tortoise_create_subject` | `tool_registry.py:1248` | `_create_subject` | no | `create_entity` |
| 22 | `tortoise_delete` | `tool_registry.py:1538` | `_delete` | no | `delete_knowledge` |
| 23 | `tortoise_delete_entity` | `tool_registry.py:1428` | `_delete_entity__svc` | no | `delete_knowledge` |
| 24 | `tortoise_delete_knowledge` | `tool_registry.py:1502` | `_delete` | no | `delete_knowledge` |
| 25 | `tortoise_delete_point` | `tool_registry.py:808` | `_delete_point_wrapped` | no | `delete_knowledge` |
| 26 | `tortoise_diary_read` | `tool_registry.py:971` | **none declared** | yes | `list_knowledge` |
| 27 | `tortoise_diary_write` | `tool_registry.py:962` | `_diary_write` | no | `create_entity` |
| 28 | `tortoise_dream` | `tool_registry.py:680` | `_dream` | no | `refresh_confidence` |
| 29 | `tortoise_dream_health` | `tool_registry.py:696` | `_dream_health_check` | yes | `graph_overview` |
| 30 | `tortoise_entity_profile` | `tool_registry.py:926` | **none declared** | yes | `explore_connections` |
| 31 | `tortoise_events_poll` | `tool_registry.py:904` | `poll_events` | yes | `poll_events` |
| 32 | `tortoise_expand_relationships` | `tool_registry.py:523` | **none declared** | yes | `explore_connections` |
| 33 | `tortoise_explore_connections` | `tool_registry.py:1750` | `explore_connections` | yes | `explore_connections` |
| 34 | `tortoise_file_decision` | `tool_registry.py:787` | `write_question` | no | `write_question` |
| 35 | `tortoise_file_human_approval` | `tool_registry.py:796` | `record_decision` | no | `record_decision` |
| 36 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1197` | `_get_cross_lens_candidates` | yes | `review_link_candidates` |
| 37 | `tortoise_get` | `tool_registry.py:1790` | **none declared** | yes | `get_entity` |
| 38 | `tortoise_get_confidence` | `tool_registry.py:661` | `_get_confidence` | no | `check_confidence` |
| 39 | `tortoise_get_entity` | `tool_registry.py:1409` | `get_entity` | yes | `get_entity` |
| 40 | `tortoise_get_events` | `tool_registry.py:1281` | `_get_events` | yes | `get_entity` |
| 41 | `tortoise_get_governance` | `tool_registry.py:1576` | `_get_owned_entities` | yes | `get_entity` |
| 42 | `tortoise_get_historical_knowledge` | `tool_registry.py:1590` | `get_historical_knowledge` | yes | `get_historical_knowledge` |
| 43 | `tortoise_get_operator` | `tool_registry.py:758` | `_get_point` | yes | `get_entity` |
| 44 | `tortoise_get_point` | `tool_registry.py:470` | `_get_point` | yes | `get_entity` |
| 45 | `tortoise_get_session` | `tool_registry.py:1289` | `_get_session` | yes | `get_entity` |
| 46 | `tortoise_get_source_reliability` | `tool_registry.py:1367` | `_get_source_reliability` | no | `list_knowledge` |
| 47 | `tortoise_graph_overview` | `tool_registry.py:1629` | **none declared** | yes | `graph_overview` |
| 48 | `tortoise_graph_set_recording` | `tool_registry.py:1043` | **none declared** | no | `graph_set_recording` |
| 49 | `tortoise_health` | `tool_registry.py:996` | `health` ⚠️ **does not resolve** | yes | `graph_overview` |
| 50 | `tortoise_index_files` | `tool_registry.py:1310` | `index_directory` ⚠️ **does not resolve** | no | `index_sources_from_directory` |
| 51 | `tortoise_index_sessions` | `tool_registry.py:1297` | `index_sessions` ⚠️ **does not resolve** | no | `index_sources_from_directory` |
| 52 | `tortoise_index_sources_from_directory` | `tool_registry.py:2074` | `index_sources_from_directory` | no | `index_sources_from_directory` |
| 53 | `tortoise_ingest` | `tool_registry.py:1088` | `_ingest` | no | `sdk:write_knowledge_batch` |
| 54 | `tortoise_ingest_corpus` | `tool_registry.py:1075` | `_ingest_corpus` | no | `index_sources_from_directory` |
| 55 | `tortoise_invalidate` | `tool_registry.py:818` | `_invalidate_point` | no | `supersede_knowledge` |
| 56 | `tortoise_issue_insight` | `tool_registry.py:1061` | `_issue_insight` | yes | `search_knowledge` |
| 57 | `tortoise_link_entities` | `tool_registry.py:1610` | `link_entities` | no | `link_entities` |
| 58 | `tortoise_list_batch` | `tool_registry.py:417` | `_list_batch` | yes | `list_knowledge` |
| 59 | `tortoise_list_batches` | `tool_registry.py:429` | `_list_batches` | yes | `list_knowledge` |
| 60 | `tortoise_list_dedup_candidates` | `tool_registry.py:575` | **none declared** | yes | `review_link_candidates` |
| 61 | `tortoise_list_graphs` | `tool_registry.py:979` | `_list_graphs` | yes | `graph_overview` |
| 62 | `tortoise_list_knowledge` | `tool_registry.py:1681` | `list_knowledge` | no | `list_knowledge` |
| 63 | `tortoise_list_namespaces` | `tool_registry.py:408` | **none declared** | yes | `list_knowledge` |
| 64 | `tortoise_list_pointkinds` | `tool_registry.py:392` | **none declared** | yes | `graph_overview` |
| 65 | `tortoise_list_sources` | `tool_registry.py:400` | **none declared** | yes | `graph_overview` |
| 66 | `tortoise_list_tags` | `tool_registry.py:449` | **none declared** | yes | `graph_overview` |
| 67 | `tortoise_list_topics` | `tool_registry.py:1139` | **none declared** | yes | `list_knowledge` |
| 68 | `tortoise_manage_source_trust` | `tool_registry.py:2060` | `manage_source_trust` | no | `manage_source_trust` |
| 69 | `tortoise_manage_sources` | `tool_registry.py:1982` | **none declared** | no | `manage_sources` |
| 70 | `tortoise_mine_conversations` | `tool_registry.py:562` | `mine_corpus` ⚠️ **does not resolve** | no | `mine_knowledge_from_directory` |
| 71 | `tortoise_mine_knowledge_from_directory` | `tool_registry.py:2122` | `mine_knowledge_from_directory` | no | `mine_knowledge_from_directory` |
| 72 | `tortoise_mine_knowledge_from_session` | `tool_registry.py:2096` | `mine_knowledge_from_session` | no | `mine_knowledge_from_session` |
| 73 | `tortoise_mitigate_operator` | `tool_registry.py:767` | `_mitigate_operator` | no | `adjust_relationship` |
| 74 | `tortoise_onboarding_github_index` | `tool_registry.py:2023` | **none declared** | no | `manage_sources` |
| 75 | `tortoise_onboarding_session_recording` | `tool_registry.py:2014` | **none declared** | no | `manage_sources` |
| 76 | `tortoise_operator_action` | `tool_registry.py:1550` | **none declared** | no | `adjust_relationship` |
| 77 | `tortoise_org_create` | `tool_registry.py:1237` | `_org_create` | no | `tenancy:create_memory_graph` |
| 78 | `tortoise_overview` | `tool_registry.py:1779` | **none declared** | yes | `graph_overview` |
| 79 | `tortoise_paginated_query` | `tool_registry.py:331` | `_paginated_query` | yes | `search_knowledge` |
| 80 | `tortoise_poll_events` | `tool_registry.py:849` | `poll_events` | yes | `poll_events` |
| 81 | `tortoise_promote_point` | `tool_registry.py:607` | `_promote_point` | no | `refresh_confidence` ⚠️ |
| 82 | `tortoise_provenance` | `tool_registry.py:1215` | **none declared** | no | `check_confidence` |
| 83 | `tortoise_query` | `tool_registry.py:300` | `_paginated_query` | yes | `search_knowledge` |
| 84 | `tortoise_query_points_by_tag` | `tool_registry.py:457` | `_query_points_by_tag` | yes | `search_knowledge` |
| 85 | `tortoise_recall` | `tool_registry.py:534` | `_recall_state` | yes | `check_confidence` |
| 86 | `tortoise_record_decision` | `tool_registry.py:1940` | `record_decision` | no | `record_decision` |
| 87 | `tortoise_refresh_confidence` | `tool_registry.py:1898` | **none declared** | no | `refresh_confidence` |
| 88 | `tortoise_register_source` | `tool_registry.py:2047` | `register_source` | no | `register_source` |
| 89 | `tortoise_retract_point` | `tool_registry.py:914` | `_retract_point` | no | `update_knowledge` |
| 90 | `tortoise_review_connections` | `tool_registry.py:1174` | `_review_connections` | yes | `review_link_candidates` |
| 91 | `tortoise_review_link_candidates` | `tool_registry.py:1764` | `review_link_candidates` | yes | `review_link_candidates` |
| 92 | `tortoise_search` | `tool_registry.py:511` | `_tortoise_fts_query` | yes | `search_knowledge` |
| 93 | `tortoise_search_knowledge` | `tool_registry.py:1651` | `search_knowledge` | no | `search_knowledge` |
| 94 | `tortoise_search_sessions` | `tool_registry.py:1326` | **none declared** | yes | `search_knowledge` |
| 95 | `tortoise_session_capture` | `tool_registry.py:1014` | **none declared** | no | `mine_knowledge_from_session` |
| 96 | `tortoise_session_context` | `tool_registry.py:1004` | `_session_context` | yes | `check_confidence` |
| 97 | `tortoise_set_point_baseline` | `tool_registry.py:653` | `_set_point_baseline` | no | `refresh_confidence` ⚠️ |
| 98 | `tortoise_set_source_tier` | `tool_registry.py:1400` | `set_source_tier` ⚠️ **does not resolve** | no | `manage_source_trust` |
| 99 | `tortoise_stale` | `tool_registry.py:1160` | **none declared** | yes | `graph_overview` |
| 100 | `tortoise_status` | `tool_registry.py:987` | **none declared** | yes | `graph_overview` |
| 101 | `tortoise_suggest_entry_points` | `tool_registry.py:500` | **none declared** | yes | `search_knowledge` |
| 102 | `tortoise_summarize_structure` | `tool_registry.py:360` | `_summarize_structure` | yes | `graph_overview` |
| 103 | `tortoise_supersede` | `tool_registry.py:827` | `supersede_knowledge` | no | `supersede_knowledge` |
| 104 | `tortoise_supersede_knowledge` | `tool_registry.py:1852` | `supersede_knowledge` | no | `supersede_knowledge` |
| 105 | `tortoise_taxonomy` | `tool_registry.py:1129` | **none declared** | yes | `graph_overview` |
| 106 | `tortoise_traverse` | `tool_registry.py:942` | **none declared** | yes | `explore_connections` |
| 107 | `tortoise_update` | `tool_registry.py:1484` | `update_knowledge` | no | `update_knowledge` |
| 108 | `tortoise_update_entity` | `tool_registry.py:1420` | `update_knowledge` | no | `update_knowledge` |
| 109 | `tortoise_update_knowledge` | `tool_registry.py:1821` | **none declared** | no | `update_knowledge` |
| 110 | `tortoise_update_point` | `tool_registry.py:709` | `update_knowledge` | no | `update_knowledge` |
| 111 | `tortoise_validate_domain` | `tool_registry.py:348` | **none declared** | yes | `graph_overview` |
| 112 | `tortoise_write_question` | `tool_registry.py:1928` | `write_question` | no | `write_question` |

**73** of these are RETIRED names (#3883): off the advertised surface, but they
still answer through the warning shim, and each one's `Destination` is the destination of
the replacement that warning names. The other **39** are live.

Listed so a reader can tell them apart from the live rows that share their destination: `tortoise_annotate_operator`, `tortoise_assess_source`, `tortoise_audit`, `tortoise_belief_timeline`, `tortoise_checkpoint`, `tortoise_compute_confidence`, `tortoise_create_document`, `tortoise_create_edge`, `tortoise_create_event`, `tortoise_create_object`, `tortoise_create_operator`, `tortoise_create_point`, `tortoise_create_source`, `tortoise_create_subject`, `tortoise_delete`, `tortoise_delete_entity`, `tortoise_delete_point`, `tortoise_diary_write`, `tortoise_dream`, `tortoise_dream_health`, `tortoise_entity_profile`, `tortoise_events_poll`, `tortoise_file_decision`, `tortoise_file_human_approval`, `tortoise_find_cross_lens_candidates`, `tortoise_get`, `tortoise_get_confidence`, `tortoise_get_events`, `tortoise_get_governance`, `tortoise_get_operator`, `tortoise_get_point`, `tortoise_get_session`, `tortoise_get_source_reliability`, `tortoise_health`, `tortoise_index_files`, `tortoise_index_sessions`, `tortoise_ingest_corpus`, `tortoise_invalidate`, `tortoise_issue_insight`, `tortoise_list_batch`, `tortoise_list_dedup_candidates`, `tortoise_list_graphs`, `tortoise_list_pointkinds`, `tortoise_list_sources`, `tortoise_list_tags`, `tortoise_list_topics`, `tortoise_mine_conversations`, `tortoise_mitigate_operator`, `tortoise_onboarding_github_index`, `tortoise_onboarding_session_recording`, `tortoise_operator_action`, `tortoise_overview`, `tortoise_paginated_query`, `tortoise_promote_point`, `tortoise_provenance`, `tortoise_query_points_by_tag`, `tortoise_recall`, `tortoise_retract_point`, `tortoise_review_connections`, `tortoise_session_capture`, `tortoise_session_context`, `tortoise_set_point_baseline`, `tortoise_set_source_tier`, `tortoise_stale`, `tortoise_status`, `tortoise_summarize_structure`, `tortoise_supersede`, `tortoise_taxonomy`, `tortoise_traverse`, `tortoise_update`, `tortoise_update_entity`, `tortoise_update_point`, `tortoise_validate_domain`

**A `⚠️` after a destination means the sibling SDK rename table**
**(`docs/product/sdk-rename-table.md` (generated on demand) §C3b, and its C6 fold record) records that**
**destination as WRONG.** The map is owner-approved, so it is NOT edited here; §D2c states
the documented reading and the authority for it.

### Destination counts

| Destination | Count |
|---|---|
| `graph_overview` | 15 |
| `check_confidence` | 8 |
| `search_knowledge` | 8 |
| `create_entity` | 7 |
| `get_entity` | 7 |
| `list_knowledge` | 7 |
| `update_knowledge` | 5 |
| `adjust_relationship` | 4 |
| `delete_knowledge` | 4 |
| `explore_connections` | 4 |
| `index_sources_from_directory` | 4 |
| `refresh_confidence` | 4 |
| `review_link_candidates` | 4 |
| `link_entities` | 3 |
| `manage_source_trust` | 3 |
| `manage_sources` | 3 |
| `mine_knowledge_from_session` | 3 |
| `supersede_knowledge` | 3 |
| `mine_knowledge_from_directory` | 2 |
| `poll_events` | 2 |
| `record_decision` | 2 |
| `register_source` | 2 |
| `write_question` | 2 |
| `approve_merge` | 1 |
| `check_connection` | 1 |
| `get_historical_knowledge` | 1 |
| `graph_set_recording` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| **total** | **112** |

Destination rows: **29**. Registry tools: **112**.

## Part C — blockers

### C1 — target tools with NO method on `TortoiseSDK`

| Target tool | Where | Finding |
|---|---|---|
| `manage_sources` | MCP | no `def` on TortoiseSDK |

**These are not renames.** They are new methods that must be built in Phase 2, and the
plan listed them as if they were renames. This is what Part A exists to catch.

### C2 — registry bindings that do not resolve

| Registry tool | Declared binding | Source |
|---|---|---|
| `tortoise_mine_conversations` | `mine_corpus` | `tool_registry.py:562` |
| `tortoise_checkpoint` | `checkpoint` | `tool_registry.py:952` |
| `tortoise_health` | `health` | `tool_registry.py:996` |
| `tortoise_index_sessions` | `index_sessions` | `tool_registry.py:1297` |
| `tortoise_index_files` | `index_directory` | `tool_registry.py:1310` |
| `tortoise_create_source` | `create_source` | `tool_registry.py:1355` |
| `tortoise_assess_source` | `assess_source` | `tool_registry.py:1390` |
| `tortoise_set_source_tier` | `set_source_tier` | `tool_registry.py:1400` |

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

**2 citations cover 3 of the 112 registry rows.** The other **109**
are map decisions with no disposition row in the doc to cite — a net-new target, or a row
that table does not carry.

### D1 — the citation corpus

- `beta-sdk-surface.md` · rows `tortoise_assess_source`, `tortoise_set_source_tier`
  > | `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |
- `beta-sdk-surface.md` · rows `tortoise_mine_conversations`
  > | `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. |

**Documented hops.** A citation can name something that is not a target because it is
itself folded one hop further. The hop is stated in the doc, so it is carried as a
citation of its own — checked by the same rule — rather than assumed. Without it
`tortoise_get_source_reliability`'s row reads as unsupported, which is not a finding.

- `list_sources` → `list_knowledge`
  > | `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |

**0 rows disagree with their own citation; 0 are supported only
beyond the first clause; 2 sit under an ambiguous citation.** Every count
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

None — every citation names its row's destination.

### D2b — rows whose support exists ONLY beyond the first clause

These rows are **why the first-clause split is load-bearing, not decorative**. Their
destination is named by the citation, but only in a clause after the first `→` — the
exact point a truncated read would stop. A read that took only the first clause would
lose the row's whole support, silently — so the generator computes the first-clause names
(`_prefix_targets`) and surfaces any discrepancy as this list.

None — every row's destination is named in its citation's first clause.

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
| `tortoise_assess_source` | `manage_source_trust` | `manage_source_trust`, `list_knowledge` | `manage_source_trust` |
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
| `tortoise_assess_source` | `manage_source_trust` | `manage_source_trust` (row 14) | *(none)* | **row** | `list_knowledge` — **refuted** |
| `tortoise_set_source_tier` | `manage_source_trust` | `manage_source_trust` (row 14) | *(none)* | **row** | `list_knowledge` — **refuted** |

**2 of 2 disputed rows are placed by the approved menu where this
map places them; 0 are placed ELSEWHERE by it; 0 have
no place in it at all.** Every count is computed from the doc, not typed.

**The approved row itself, for each disputed row that has one** (whole table row, by
construction, so it cannot be a truncation of what the doc says):

- **`tortoise_assess_source`** — `assess_source` is absorbed by `manage_source_trust`:
  > | 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |
- **`tortoise_set_source_tier`** — `set_source_tier` is absorbed by `manage_source_trust`:
  > | 14 | `manage_source_trust` | assess_source, get_source_reliability, set_source_tier |

---

## Reproduce

```bash
uv run python tools/bridge_table.py          # regenerate this file
uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift
```
