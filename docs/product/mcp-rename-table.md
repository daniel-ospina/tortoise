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
| 1 | `tortoise_annotate_operator` | `tool_registry.py:724` | **no replacement** | `adjust_relationship` | absent |
| 2 | `tortoise_approve_merge` | `tool_registry.py:561` | **no replacement** | `approve_merge` | absent |
| 3 | `tortoise_assess_source` | `tool_registry.py:1318` | **no replacement** | `manage_source_trust` | absent |
| 4 | `tortoise_audit` | `tool_registry.py:370` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | warning shim |
| 5 | `tortoise_belief_timeline` | `tool_registry.py:593` | `tortoise_check_confidence(topic=...)` | `check_confidence` | warning shim |
| 6 | `tortoise_calibrate_summary` | `tool_registry.py:647` | **no replacement** | `check_confidence` | absent |
| 7 | `tortoise_check_confidence` | `tool_registry.py:1521` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_check_structure` | `tool_registry.py:337` | **no replacement** | `graph_overview` | absent |
| 9 | `tortoise_checkpoint` | `tool_registry.py:900` | **no replacement** | `mine_knowledge_from_session` | absent |
| 10 | `tortoise_compute_confidence` | `tool_registry.py:605` | **no replacement** | `check_confidence` | absent |
| 11 | `tortoise_create_document` | `tool_registry.py:1265` | **no replacement** | `create_entity` | absent |
| 12 | `tortoise_create_edge` | `tool_registry.py:1423` | **no replacement** | `link_entities` | absent |
| 13 | `tortoise_create_entity` | `tool_registry.py:1366` | **no replacement** | `create_entity` | absent |
| 14 | `tortoise_create_event` | `tool_registry.py:1204` | **no replacement** | `create_entity` | absent |
| 15 | `tortoise_create_object` | `tool_registry.py:1192` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_operator` | `tool_registry.py:700` | **no replacement** | `link_entities` | absent |
| 17 | `tortoise_create_point` | `tool_registry.py:267` | **no replacement** | `create_entity` | absent |
| 18 | `tortoise_create_source` | `tool_registry.py:1283` | **no replacement** | `register_source` | absent |
| 19 | `tortoise_create_subject` | `tool_registry.py:1179` | **no replacement** | `create_entity` | absent |
| 20 | `tortoise_delete` | `tool_registry.py:1393` | **no replacement** | `delete_knowledge` | absent |
| 21 | `tortoise_delete_entity` | `tool_registry.py:1356` | **no replacement** | `delete_knowledge` | absent |
| 22 | `tortoise_delete_point` | `tool_registry.py:783` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_diary_read` | `tool_registry.py:919` | **no replacement** | `list_knowledge` | absent |
| 24 | `tortoise_diary_write` | `tool_registry.py:910` | **no replacement** | `create_entity` | absent |
| 25 | `tortoise_dream` | `tool_registry.py:655` | **no replacement** | `refresh_confidence` | absent |
| 26 | `tortoise_dream_health` | `tool_registry.py:671` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | warning shim |
| 27 | `tortoise_entity_profile` | `tool_registry.py:880` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 28 | `tortoise_events_poll` | `tool_registry.py:815` | **no replacement** | `poll_events` | absent |
| 29 | `tortoise_expand_relationships` | `tool_registry.py:498` | **no replacement** | `explore_connections` | absent |
| 30 | `tortoise_explore_connections` | `tool_registry.py:1544` | **no replacement** | `explore_connections` | absent |
| 31 | `tortoise_file_decision` | `tool_registry.py:762` | **no replacement** | `write_question` | absent |
| 32 | `tortoise_file_human_approval` | `tool_registry.py:771` | **no replacement** | `record_decision` | absent |
| 33 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1140` | **no replacement** | `review_link_candidates` | absent |
| 34 | `tortoise_get` | `tool_registry.py:1584` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 35 | `tortoise_get_confidence` | `tool_registry.py:636` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | warning shim |
| 36 | `tortoise_get_entity` | `tool_registry.py:1337` | **no replacement** | `get_entity` | absent |
| 37 | `tortoise_get_events` | `tool_registry.py:1212` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 38 | `tortoise_get_governance` | `tool_registry.py:1435` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 39 | `tortoise_get_operator` | `tool_registry.py:733` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 40 | `tortoise_get_point` | `tool_registry.py:467` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 41 | `tortoise_get_session` | `tool_registry.py:1220` | **no replacement** | `get_entity` | absent |
| 42 | `tortoise_get_source_reliability` | `tool_registry.py:1295` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | warning shim |
| 43 | `tortoise_graph_overview` | `tool_registry.py:1444` | **no replacement** | `graph_overview` | absent |
| 44 | `tortoise_graph_set_recording` | `tool_registry.py:991` | **no replacement** | `graph_set_recording` | absent |
| 45 | `tortoise_health` | `tool_registry.py:944` | `tortoise_graph_overview(section="health")` | `graph_overview` | warning shim |
| 46 | `tortoise_index_files` | `tool_registry.py:1238` | **no replacement** | `index_sources_from_directory` | absent |
| 47 | `tortoise_index_sessions` | `tool_registry.py:1228` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 48 | `tortoise_ingest` | `tool_registry.py:1034` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 49 | `tortoise_ingest_corpus` | `tool_registry.py:1023` | `tortoise_index_files(directory)` | `index_sources_from_directory` | warning shim |
| 50 | `tortoise_invalidate` | `tool_registry.py:793` | **no replacement** | `supersede_knowledge` | absent |
| 51 | `tortoise_issue_insight` | `tool_registry.py:1009` | **no replacement** | `search_knowledge` | absent |
| 52 | `tortoise_list_batch` | `tool_registry.py:414` | **no replacement** | `list_knowledge` | absent |
| 53 | `tortoise_list_batches` | `tool_registry.py:426` | **no replacement** | `list_knowledge` | absent |
| 54 | `tortoise_list_dedup_candidates` | `tool_registry.py:550` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | warning shim |
| 55 | `tortoise_list_graphs` | `tool_registry.py:927` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 56 | `tortoise_list_knowledge` | `tool_registry.py:1496` | **no replacement** | `list_knowledge` | absent |
| 57 | `tortoise_list_namespaces` | `tool_registry.py:405` | **no replacement** | `list_knowledge` | absent |
| 58 | `tortoise_list_pointkinds` | `tool_registry.py:389` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 59 | `tortoise_list_sources` | `tool_registry.py:397` | `tortoise_graph_overview(section="sources")` | `graph_overview` | warning shim |
| 60 | `tortoise_list_tags` | `tool_registry.py:446` | `tortoise_graph_overview(section="tags")` | `graph_overview` | warning shim |
| 61 | `tortoise_list_topics` | `tool_registry.py:1085` | `tortoise_list_knowledge(kind="topic", entity_id=...)` | `list_knowledge` | warning shim |
| 62 | `tortoise_manage_sources` | `tool_registry.py:1624` | **no replacement** | `manage_sources` | absent |
| 63 | `tortoise_mine_conversations` | `tool_registry.py:537` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 64 | `tortoise_mitigate_operator` | `tool_registry.py:742` | **no replacement** | `adjust_relationship` | absent |
| 65 | `tortoise_onboarding_github_index` | `tool_registry.py:1665` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 66 | `tortoise_onboarding_session_recording` | `tool_registry.py:1656` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 67 | `tortoise_operator_action` | `tool_registry.py:1412` | **no replacement** | `adjust_relationship` | absent |
| 68 | `tortoise_org_create` | `tool_registry.py:1168` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 69 | `tortoise_overview` | `tool_registry.py:1573` | `tortoise_graph_overview(section=...)` | `graph_overview` | warning shim |
| 70 | `tortoise_paginated_query` | `tool_registry.py:328` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 71 | `tortoise_promote_point` | `tool_registry.py:582` | **no replacement** | `refresh_confidence` | absent |
| 72 | `tortoise_provenance` | `tool_registry.py:1158` | `tortoise_check_confidence(claim_id, include_provenance=True)` | `check_confidence` | warning shim |
| 73 | `tortoise_query` | `tool_registry.py:297` | **no replacement** | `search_knowledge` | absent |
| 74 | `tortoise_query_points_by_tag` | `tool_registry.py:454` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | warning shim |
| 75 | `tortoise_recall` | `tool_registry.py:509` | **no replacement** | `check_confidence` | absent |
| 76 | `tortoise_retract_point` | `tool_registry.py:868` | **no replacement** | `update_knowledge` | absent |
| 77 | `tortoise_review_connections` | `tool_registry.py:1117` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | warning shim |
| 78 | `tortoise_review_link_candidates` | `tool_registry.py:1558` | **no replacement** | `review_link_candidates` | absent |
| 79 | `tortoise_search` | `tool_registry.py:486` | **no replacement** | `search_knowledge` | absent |
| 80 | `tortoise_search_knowledge` | `tool_registry.py:1466` | **no replacement** | `search_knowledge` | absent |
| 81 | `tortoise_search_sessions` | `tool_registry.py:1254` | **no replacement** | `search_knowledge` | absent |
| 82 | `tortoise_session_capture` | `tool_registry.py:962` | **no replacement** | `mine_knowledge_from_session` | absent |
| 83 | `tortoise_session_context` | `tool_registry.py:952` | **no replacement** | `check_confidence` | absent |
| 84 | `tortoise_set_point_baseline` | `tool_registry.py:628` | **no replacement** | `refresh_confidence` | absent |
| 85 | `tortoise_set_source_tier` | `tool_registry.py:1328` | **no replacement** | `manage_source_trust` | absent |
| 86 | `tortoise_stale` | `tool_registry.py:1103` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | warning shim |
| 87 | `tortoise_status` | `tool_registry.py:935` | `tortoise_graph_overview(section="status")` | `graph_overview` | warning shim |
| 88 | `tortoise_suggest_entry_points` | `tool_registry.py:475` | **no replacement** | `search_knowledge` | absent |
| 89 | `tortoise_summarize_structure` | `tool_registry.py:357` | `tortoise_graph_overview(section="structure")` | `graph_overview` | warning shim |
| 90 | `tortoise_supersede` | `tool_registry.py:802` | **no replacement** | `supersede_knowledge` | absent |
| 91 | `tortoise_taxonomy` | `tool_registry.py:1075` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 92 | `tortoise_traverse` | `tool_registry.py:890` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 93 | `tortoise_update` | `tool_registry.py:1382` | **no replacement** | `update_knowledge` | absent |
| 94 | `tortoise_update_entity` | `tool_registry.py:1348` | **no replacement** | `update_knowledge` | absent |
| 95 | `tortoise_update_point` | `tool_registry.py:684` | **no replacement** | `update_knowledge` | absent |
| 96 | `tortoise_validate_domain` | `tool_registry.py:345` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | warning shim |

**96 served MCP tools, 28 destinations.** 32 retire **with a warning shim**; the other **64** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 32 `warning shim` names are
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
| `check_confidence` | 8 |
| `search_knowledge` | 8 |
| `create_entity` | 7 |
| `get_entity` | 7 |
| `list_knowledge` | 7 |
| `explore_connections` | 4 |
| `review_link_candidates` | 4 |
| `update_knowledge` | 4 |
| `adjust_relationship` | 3 |
| `delete_knowledge` | 3 |
| `index_sources_from_directory` | 3 |
| `manage_sources` | 3 |
| `refresh_confidence` | 3 |
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
| **total** | **96** |

96 served tools → 28 destinations: **93** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **0** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 32 names, and they must not
contradict each other: a caller who follows a redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | PR #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_audit` | `graph_overview` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | AGREE |
| `tortoise_belief_timeline` | `check_confidence` | `tortoise_check_confidence(topic=...)` | `check_confidence` | AGREE |
| `tortoise_dream_health` | `graph_overview` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | AGREE |
| `tortoise_entity_profile` | `explore_connections` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | AGREE |
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_confidence` | `check_confidence` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_get_source_reliability` | `list_knowledge` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_graph_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_files(directory)` | `index_sources_from_directory` | AGREE |
| `tortoise_list_dedup_candidates` | `review_link_candidates` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | AGREE |
| `tortoise_list_pointkinds` | `graph_overview` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | AGREE |
| `tortoise_list_sources` | `graph_overview` | `tortoise_graph_overview(section="sources")` | `graph_overview` | AGREE |
| `tortoise_list_tags` | `graph_overview` | `tortoise_graph_overview(section="tags")` | `graph_overview` | AGREE |
| `tortoise_list_topics` | `list_knowledge` | `tortoise_list_knowledge(kind="topic", entity_id=...)` | `list_knowledge` | AGREE |
| `tortoise_onboarding_github_index` | `manage_sources` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | AGREE |
| `tortoise_onboarding_session_recording` | `manage_sources` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | AGREE |
| `tortoise_overview` | `graph_overview` | `tortoise_graph_overview(section=...)` | `graph_overview` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_provenance` | `check_confidence` | `tortoise_check_confidence(claim_id, include_provenance=True)` | `check_confidence` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_review_connections` | `review_link_candidates` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_graph_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_summarize_structure` | `graph_overview` | `tortoise_graph_overview(section="structure")` | `graph_overview` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | AGREE |
| `tortoise_traverse` | `explore_connections` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | AGREE |
| `tortoise_validate_domain` | `graph_overview` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | AGREE |

**32 of 32 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 32 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
