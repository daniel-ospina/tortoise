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
| 1 | `tortoise_analyze` | `tool_registry.py:831` | **no replacement** | `REMOVED` | absent |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:504` | **no replacement** | `adjust_relationship` | absent |
| 3 | `tortoise_approve_merge` | `tool_registry.py:373` | **no replacement** | `approve_merge` | absent |
| 4 | `tortoise_assess_source` | `tool_registry.py:1019` | **no replacement** | `manage_source_trust` | absent |
| 5 | `tortoise_audit` | `tool_registry.py:174` | **no replacement** | `graph_overview` | absent |
| 6 | `tortoise_backfill_v25` | `tool_registry.py:1168` | **no replacement** | `REMOVED` | absent |
| 7 | `tortoise_belief_timeline` | `tool_registry.py:396` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_calibrate_summary` | `tool_registry.py:444` | **no replacement** | `check_confidence` | absent |
| 9 | `tortoise_check_structure` | `tool_registry.py:141` | **no replacement** | `graph_overview` | absent |
| 10 | `tortoise_checkpoint` | `tool_registry.py:640` | **no replacement** | `mine_knowledge_from_session` | absent |
| 11 | `tortoise_compute_confidence` | `tool_registry.py:407` | **no replacement** | `check_confidence` | absent |
| 12 | `tortoise_create_document` | `tool_registry.py:986` | **no replacement** | `create_entity` | absent |
| 13 | `tortoise_create_edge` | `tool_registry.py:1124` | **no replacement** | `link_entities` | absent |
| 14 | `tortoise_create_entity` | `tool_registry.py:1071` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_event` | `tool_registry.py:925` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_object` | `tool_registry.py:916` | **no replacement** | `create_entity` | absent |
| 17 | `tortoise_create_operator` | `tool_registry.py:492` | **no replacement** | `link_entities` | absent |
| 18 | `tortoise_create_point` | `tool_registry.py:103` | **no replacement** | `create_entity` | absent |
| 19 | `tortoise_create_source` | `tool_registry.py:996` | **no replacement** | `register_source` | absent |
| 20 | `tortoise_create_subject` | `tool_registry.py:907` | **no replacement** | `create_entity` | absent |
| 21 | `tortoise_delete` | `tool_registry.py:1100` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_delete_entity` | `tool_registry.py:1060` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_delete_point` | `tool_registry.py:557` | **no replacement** | `delete_knowledge` | absent |
| 24 | `tortoise_diary_read` | `tool_registry.py:660` | **no replacement** | `list_knowledge` | absent |
| 25 | `tortoise_diary_write` | `tool_registry.py:650` | **no replacement** | `create_entity` | absent |
| 26 | `tortoise_dream` | `tool_registry.py:452` | **no replacement** | `refresh_confidence` | absent |
| 27 | `tortoise_dream_health` | `tool_registry.py:469` | **no replacement** | `graph_overview` | absent |
| 28 | `tortoise_entity_profile` | `tool_registry.py:620` | **no replacement** | `explore_connections` | absent |
| 29 | `tortoise_events_poll` | `tool_registry.py:592` | **no replacement** | `poll_events` | absent |
| 30 | `tortoise_expand_relationships` | `tool_registry.py:311` | **no replacement** | `explore_connections` | absent |
| 31 | `tortoise_file_decision` | `tool_registry.py:534` | **no replacement** | `write_question` | absent |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:544` | **no replacement** | `record_decision` | absent |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:867` | **no replacement** | `review_link_candidates` | absent |
| 34 | `tortoise_get` | `tool_registry.py:1157` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 35 | `tortoise_get_confidence` | `tool_registry.py:436` | **no replacement** | `check_confidence` | absent |
| 36 | `tortoise_get_entity` | `tool_registry.py:1040` | **no replacement** | `get_entity` | absent |
| 37 | `tortoise_get_events` | `tool_registry.py:934` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 38 | `tortoise_get_governance` | `tool_registry.py:1137` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 39 | `tortoise_get_operator` | `tool_registry.py:514` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 40 | `tortoise_get_point` | `tool_registry.py:282` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 41 | `tortoise_get_session` | `tool_registry.py:942` | **no replacement** | `get_entity` | absent |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:1008` | **no replacement** | `list_knowledge` | absent |
| 43 | `tortoise_graph_set_recording` | `tool_registry.py:733` | **no replacement** | `graph_set_recording` | absent |
| 44 | `tortoise_health` | `tool_registry.py:685` | `tortoise_overview(section="health")` | `graph_overview` | warning shim |
| 45 | `tortoise_index_files` | `tool_registry.py:961` | **no replacement** | `index_sources_from_directory` | absent |
| 46 | `tortoise_index_sessions` | `tool_registry.py:950` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 47 | `tortoise_ingest` | `tool_registry.py:778` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 48 | `tortoise_ingest_corpus` | `tool_registry.py:766` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 49 | `tortoise_invalidate` | `tool_registry.py:568` | **no replacement** | `supersede_knowledge` | absent |
| 50 | `tortoise_issue_insight` | `tool_registry.py:752` | **no replacement** | `search_knowledge` | absent |
| 51 | `tortoise_list_batch` | `tool_registry.py:214` | **no replacement** | `list_knowledge` | absent |
| 52 | `tortoise_list_batches` | `tool_registry.py:226` | **no replacement** | `list_knowledge` | absent |
| 53 | `tortoise_list_dedup_candidates` | `tool_registry.py:364` | **no replacement** | `review_link_candidates` | absent |
| 54 | `tortoise_list_graphs` | `tool_registry.py:668` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 55 | `tortoise_list_namespaces` | `tool_registry.py:205` | **no replacement** | `list_knowledge` | absent |
| 56 | `tortoise_list_pointkinds` | `tool_registry.py:189` | `tortoise_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 57 | `tortoise_list_sources` | `tool_registry.py:197` | `tortoise_overview(section="sources")` | `graph_overview` | warning shim |
| 58 | `tortoise_list_tags` | `tool_registry.py:264` | `tortoise_overview(section="tags")` | `graph_overview` | warning shim |
| 59 | `tortoise_list_topics` | `tool_registry.py:820` | **no replacement** | `list_knowledge` | absent |
| 60 | `tortoise_manage_sources` | `tool_registry.py:1212` | **no replacement** | `manage_sources` | absent |
| 61 | `tortoise_mine_conversations` | `tool_registry.py:350` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 62 | `tortoise_mitigate_operator` | `tool_registry.py:523` | **no replacement** | `adjust_relationship` | absent |
| 63 | `tortoise_onboarding_demo_create` | `tool_registry.py:1179` | **no replacement** | `REMOVED` | absent |
| 64 | `tortoise_onboarding_github_connect` | `tool_registry.py:1254` | **no replacement** | `REMOVED` | absent |
| 65 | `tortoise_onboarding_github_index` | `tool_registry.py:1264` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 66 | `tortoise_onboarding_github_status` | `tool_registry.py:1274` | **no replacement** | `REMOVED` | absent |
| 67 | `tortoise_onboarding_seed` | `tool_registry.py:1198` | **no replacement** | `REMOVED` | absent |
| 68 | `tortoise_onboarding_session_recording` | `tool_registry.py:1244` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 69 | `tortoise_onboarding_state` | `tool_registry.py:1189` | **no replacement** | `REMOVED` | absent |
| 70 | `tortoise_operator_action` | `tool_registry.py:1112` | **no replacement** | `adjust_relationship` | absent |
| 71 | `tortoise_org_create` | `tool_registry.py:895` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 72 | `tortoise_overview` | `tool_registry.py:1146` | **no replacement** | `graph_overview` | absent |
| 73 | `tortoise_pack_install` | `tool_registry.py:247` | **no replacement** | `REMOVED` | absent |
| 74 | `tortoise_packs_list` | `tool_registry.py:236` | **no replacement** | `REMOVED` | absent |
| 75 | `tortoise_paginated_query` | `tool_registry.py:132` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 76 | `tortoise_promote_point` | `tool_registry.py:384` | **no replacement** | `refresh_confidence` | absent |
| 77 | `tortoise_provenance` | `tool_registry.py:885` | **no replacement** | `check_confidence` | absent |
| 78 | `tortoise_query` | `tool_registry.py:119` | **no replacement** | `search_knowledge` | absent |
| 79 | `tortoise_query_points_by_tag` | `tool_registry.py:272` | `tortoise_query(tag=...)` | `search_knowledge` | warning shim |
| 80 | `tortoise_recall` | `tool_registry.py:322` | **no replacement** | `check_confidence` | absent |
| 81 | `tortoise_retract_point` | `tool_registry.py:607` | **no replacement** | `update_knowledge` | absent |
| 82 | `tortoise_review_connections` | `tool_registry.py:849` | **no replacement** | `review_link_candidates` | absent |
| 83 | `tortoise_search` | `tool_registry.py:299` | **no replacement** | `search_knowledge` | absent |
| 84 | `tortoise_search_sessions` | `tool_registry.py:978` | **no replacement** | `search_knowledge` | absent |
| 85 | `tortoise_session_capture` | `tool_registry.py:703` | **no replacement** | `mine_knowledge_from_session` | absent |
| 86 | `tortoise_session_context` | `tool_registry.py:693` | **no replacement** | `check_confidence` | absent |
| 87 | `tortoise_set_point_baseline` | `tool_registry.py:427` | **no replacement** | `refresh_confidence` | absent |
| 88 | `tortoise_set_source_tier` | `tool_registry.py:1030` | **no replacement** | `manage_source_trust` | absent |
| 89 | `tortoise_stale` | `tool_registry.py:841` | `tortoise_overview(section="stale")` | `graph_overview` | warning shim |
| 90 | `tortoise_status` | `tool_registry.py:676` | `tortoise_overview(section="status")` | `graph_overview` | warning shim |
| 91 | `tortoise_suggest_entry_points` | `tool_registry.py:290` | **no replacement** | `search_knowledge` | absent |
| 92 | `tortoise_summarize_structure` | `tool_registry.py:161` | **no replacement** | `graph_overview` | absent |
| 93 | `tortoise_supersede` | `tool_registry.py:578` | **no replacement** | `supersede_knowledge` | absent |
| 94 | `tortoise_taxonomy` | `tool_registry.py:810` | `tortoise_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 95 | `tortoise_traverse` | `tool_registry.py:630` | **no replacement** | `explore_connections` | absent |
| 96 | `tortoise_update` | `tool_registry.py:1088` | **no replacement** | `update_knowledge` | absent |
| 97 | `tortoise_update_entity` | `tool_registry.py:1051` | **no replacement** | `update_knowledge` | absent |
| 98 | `tortoise_update_point` | `tool_registry.py:482` | **no replacement** | `update_knowledge` | absent |
| 99 | `tortoise_validate_domain` | `tool_registry.py:149` | **no replacement** | `graph_overview` | absent |

**99 served MCP tools, 29 destinations.** 18 retire **with a warning shim**; the other **81** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 18 `warning shim` names are
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

99 served tools → 29 destinations: **87** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **9** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 18 names, and they must not
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
| `tortoise_onboarding_github_index` | `manage_sources` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | AGREE |
| `tortoise_onboarding_session_recording` | `manage_sources` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=...)` | `search_knowledge` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_overview(section="stale")` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_overview(section="taxonomy")` | `graph_overview` | AGREE |

**18 of 18 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 18 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
