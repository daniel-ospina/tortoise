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
| 1 | `tortoise_analyze` | `tool_registry.py:811` | **no replacement** | `REMOVED` | absent |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:495` | **no replacement** | `adjust_relationship` | absent |
| 3 | `tortoise_approve_merge` | `tool_registry.py:367` | **no replacement** | `approve_merge` | absent |
| 4 | `tortoise_assess_source` | `tool_registry.py:996` | **no replacement** | `manage_source_trust` | absent |
| 5 | `tortoise_audit` | `tool_registry.py:168` | **no replacement** | `graph_overview` | absent |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1139` | **no replacement** | `REMOVED` | absent |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:390` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:438` | **no replacement** | `check_confidence` | absent |
| 9 | `tortoise_check_structure` | `tool_registry.py:135` | **no replacement** | `graph_overview` | absent |
| 10 | `tortoise_checkpoint` | `tool_registry.py:628` | **no replacement** | `REMOVED` | absent |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:401` | **no replacement** | `check_confidence` | absent |
| 12 | `tortoise_create_document` | `tool_registry.py:964` | **no replacement** | `create_entity` | absent |
| 13 | `tortoise_create_edge` | `tool_registry.py:1095` | **no replacement** | `link_entities` | absent |
| 14 | `tortoise_create_entity` | `tool_registry.py:1048` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_event` | `tool_registry.py:904` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_object` | `tool_registry.py:895` | **no replacement** | `create_entity` | absent |
| 17 | `tortoise_create_operator` | `tool_registry.py:485` | **no replacement** | `link_entities` | absent |
| 18 | `tortoise_create_point` | `tool_registry.py:100` | **no replacement** | `create_entity` | absent |
| 19 | `tortoise_create_source` | `tool_registry.py:973` | **no replacement** | `register_source` | absent |
| 20 | `tortoise_create_subject` | `tool_registry.py:886` | **no replacement** | `create_entity` | absent |
| 21 | `tortoise_delete` | `tool_registry.py:1071` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1037` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_delete_point` | `tool_registry.py:548` | **no replacement** | `delete_knowledge` | absent |
| 24 | `tortoise_diary_read` | `tool_registry.py:648` | **no replacement** | `REMOVED` | absent |
| 25 | `tortoise_diary_write` | `tool_registry.py:638` | **no replacement** | `REMOVED` | absent |
| 26 | `tortoise_dream` | `tool_registry.py:446` | **no replacement** | `refresh_confidence` | absent |
| 27 | `tortoise_dream_health` | `tool_registry.py:462` | **no replacement** | `graph_overview` | absent |
| 28 | `tortoise_entity_profile` | `tool_registry.py:608` | **no replacement** | `explore_connections` | absent |
| 29 | `tortoise_events_poll` | `tool_registry.py:583` | **no replacement** | `poll_events` | absent |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:305` | **no replacement** | `explore_connections` | absent |
| 31 | `tortoise_file_decision` | `tool_registry.py:525` | **no replacement** | `write_question` | absent |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:535` | **no replacement** | `record_decision` | absent |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:847` | **no replacement** | `review_link_candidates` | absent |
| 34 | `tortoise_get` | `tool_registry.py:1128` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 35 | `tortoise_get_confidence` | `tool_registry.py:430` | **no replacement** | `check_confidence` | absent |
| 36 | `tortoise_get_entity` | `tool_registry.py:1017` | **no replacement** | `get_entity` | absent |
| 37 | `tortoise_get_events` | `tool_registry.py:913` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 38 | `tortoise_get_governance` | `tool_registry.py:1108` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 39 | `tortoise_get_operator` | `tool_registry.py:505` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 40 | `tortoise_get_point` | `tool_registry.py:276` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 41 | `tortoise_get_session` | `tool_registry.py:921` | **no replacement** | `get_entity` | absent |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:985` | **no replacement** | `list_knowledge` | absent |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:717` | **no replacement** | `graph_set_recording` | absent |
| 44 | `tortoise_health` | `tool_registry.py:673` | `tortoise_overview(section="health")` | `graph_overview` | warning shim |
| 45 | `tortoise_index_files` | `tool_registry.py:939` | **no replacement** | `index_sources_from_directory` | absent |
| 46 | `tortoise_index_sessions` | `tool_registry.py:929` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 47 | `tortoise_ingest` | `tool_registry.py:760` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:750` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 49 | `tortoise_invalidate` | `tool_registry.py:559` | **no replacement** | `supersede_knowledge` | absent |
| 50 | `tortoise_issue_insight` | `tool_registry.py:736` | **no replacement** | `search_knowledge` | absent |
| 51 | `tortoise_list_batch` | `tool_registry.py:208` | **no replacement** | `list_knowledge` | absent |
| 52 | `tortoise_list_batches` | `tool_registry.py:220` | **no replacement** | `list_knowledge` | absent |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:358` | **no replacement** | `review_link_candidates` | absent |
| 54 | `tortoise_list_graphs` | `tool_registry.py:656` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:199` | **no replacement** | `list_knowledge` | absent |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:183` | `tortoise_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 57 | `tortoise_list_sources` | `tool_registry.py:191` | `tortoise_overview(section="sources")` | `graph_overview` | warning shim |
| 58 | `tortoise_list_tags` | `tool_registry.py:258` | `tortoise_overview(section="tags")` | `graph_overview` | warning shim |
| 59 | `tortoise_list_topics` | `tool_registry.py:800` | **no replacement** | `list_knowledge` | absent |
| 60 | `tortoise_mine_conversations` | `tool_registry.py:344` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 61 | `tortoise_mitigate_operator` | `tool_registry.py:514` | **no replacement** | `adjust_relationship` | absent |
| 62 | `tortoise_onboarding_demo_create` | `tool_registry.py:1149` | **no replacement** | `REMOVED` | absent |
| 63 | `tortoise_onboarding_github_connect` | `tool_registry.py:1192` | **no replacement** | `REMOVED` | absent |
| 64 | `tortoise_onboarding_github_index` | `tool_registry.py:1202` | **no replacement** | `REMOVED` | absent |
| 65 | `tortoise_onboarding_github_status` | `tool_registry.py:1212` | **no replacement** | `REMOVED` | absent |
| 66 | `tortoise_onboarding_seed` | `tool_registry.py:1168` | **no replacement** | `REMOVED` | absent |
| 67 | `tortoise_onboarding_session_recording` | `tool_registry.py:1182` | **no replacement** | `REMOVED` | absent |
| 68 | `tortoise_onboarding_state` | `tool_registry.py:1159` | **no replacement** | `REMOVED` | absent |
| 69 | `tortoise_operator_action` | `tool_registry.py:1083` | **no replacement** | `adjust_relationship` | absent |
| 70 | `tortoise_org_create` | `tool_registry.py:875` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 71 | `tortoise_overview` | `tool_registry.py:1117` | **no replacement** | `graph_overview` | absent |
| 72 | `tortoise_pack_install` | `tool_registry.py:241` | **no replacement** | `REMOVED` | absent |
| 73 | `tortoise_packs_list` | `tool_registry.py:230` | **no replacement** | `REMOVED` | absent |
| 74 | `tortoise_paginated_query` | `tool_registry.py:126` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 75 | `tortoise_promote_point` | `tool_registry.py:378` | **no replacement** | `refresh_confidence` | absent |
| 76 | `tortoise_provenance` | `tool_registry.py:865` | **no replacement** | `check_confidence` | absent |
| 77 | `tortoise_query` | `tool_registry.py:113` | **no replacement** | `search_knowledge` | absent |
| 78 | `tortoise_query_points_by_tag` | `tool_registry.py:266` | `tortoise_query(tag=...)` | `search_knowledge` | warning shim |
| 79 | `tortoise_recall` | `tool_registry.py:316` | **no replacement** | `check_confidence` | absent |
| 80 | `tortoise_retract_point` | `tool_registry.py:595` | **no replacement** | `update_knowledge` | absent |
| 81 | `tortoise_review_connections` | `tool_registry.py:829` | **no replacement** | `review_link_candidates` | absent |
| 82 | `tortoise_search` | `tool_registry.py:293` | **no replacement** | `search_knowledge` | absent |
| 83 | `tortoise_search_sessions` | `tool_registry.py:956` | **no replacement** | `search_knowledge` | absent |
| 84 | `tortoise_session_capture` | `tool_registry.py:691` | **no replacement** | `mine_knowledge_from_session` | absent |
| 85 | `tortoise_session_context` | `tool_registry.py:681` | **no replacement** | `check_confidence` | absent |
| 86 | `tortoise_set_point_baseline` | `tool_registry.py:421` | **no replacement** | `refresh_confidence` | absent |
| 87 | `tortoise_set_source_tier` | `tool_registry.py:1007` | **no replacement** | `manage_source_trust` | absent |
| 88 | `tortoise_stale` | `tool_registry.py:821` | `tortoise_overview(section="stale")` | `graph_overview` | warning shim |
| 89 | `tortoise_status` | `tool_registry.py:664` | `tortoise_overview(section="status")` | `graph_overview` | warning shim |
| 90 | `tortoise_suggest_entry_points` | `tool_registry.py:284` | **no replacement** | `search_knowledge` | absent |
| 91 | `tortoise_summarize_structure` | `tool_registry.py:155` | **no replacement** | `graph_overview` | absent |
| 92 | `tortoise_supersede` | `tool_registry.py:569` | **no replacement** | `supersede_knowledge` | absent |
| 93 | `tortoise_taxonomy` | `tool_registry.py:791` | `tortoise_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 94 | `tortoise_traverse` | `tool_registry.py:618` | **no replacement** | `explore_connections` | absent |
| 95 | `tortoise_update` | `tool_registry.py:1060` | **no replacement** | `update_knowledge` | absent |
| 96 | `tortoise_update_entity` | `tool_registry.py:1028` | **no replacement** | `update_knowledge` | absent |
| 97 | `tortoise_update_point` | `tool_registry.py:475` | **no replacement** | `update_knowledge` | absent |
| 98 | `tortoise_validate_domain` | `tool_registry.py:143` | **no replacement** | `graph_overview` | absent |

**98 served MCP tools, 28 destinations.** 16 retire **with a warning shim**; the other **82** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 16 `warning shim` names are
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

98 served tools → 28 destinations: **81** absorbed into the **26** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **14** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 16 names, and they must not
contradict each other: a caller who follows a redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | PR #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_list_pointkinds` | `graph_overview` | `tortoise_overview(section="pointkinds")` | `graph_overview` | AGREE |
| `tortoise_list_sources` | `graph_overview` | `tortoise_overview(section="sources")` | `graph_overview` | AGREE |
| `tortoise_list_tags` | `graph_overview` | `tortoise_overview(section="tags")` | `graph_overview` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=...)` | `search_knowledge` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_overview(section="stale")` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_overview(section="taxonomy")` | `graph_overview` | AGREE |

**16 of 16 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 16 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
