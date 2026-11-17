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
| 1 | `tortoise_adjust_relationship` | `tool_registry.py:1722` | **no replacement** | `adjust_relationship` | absent |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:746` | `tortoise_adjust_relationship(id, bias=..., precision=..., consistency=..., directness=...)` | `adjust_relationship` | warning shim |
| 3 | `tortoise_approve_merge` | `tool_registry.py:583` | **no replacement** | `approve_merge` | absent |
| 4 | `tortoise_assess_source` | `tool_registry.py:1363` | `tortoise_manage_source_trust(url, score=..., assessor=..., rationale=...)` | `manage_source_trust` | warning shim |
| 5 | `tortoise_audit` | `tool_registry.py:370` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | warning shim |
| 6 | `tortoise_belief_timeline` | `tool_registry.py:615` | `tortoise_check_confidence(topic=...)` | `check_confidence` | warning shim |
| 7 | `tortoise_calibrate_summary` | `tool_registry.py:669` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_check_confidence` | `tool_registry.py:1573` | **no replacement** | `check_confidence` | absent |
| 9 | `tortoise_check_connection` | `tool_registry.py:1985` | **no replacement** | `check_connection` | absent |
| 10 | `tortoise_check_structure` | `tool_registry.py:337` | **no replacement** | `graph_overview` | absent |
| 11 | `tortoise_checkpoint` | `tool_registry.py:928` | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., threshold=...)` | `mine_knowledge_from_session` | warning shim |
| 12 | `tortoise_compute_confidence` | `tool_registry.py:627` | **no replacement** | `check_confidence` | absent |
| 13 | `tortoise_create_document` | `tool_registry.py:1310` | **no replacement** | `create_entity` | absent |
| 14 | `tortoise_create_edge` | `tool_registry.py:1475` | **no replacement** | `link_entities` | absent |
| 15 | `tortoise_create_entity` | `tool_registry.py:1411` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_event` | `tool_registry.py:1246` | **no replacement** | `create_entity` | absent |
| 17 | `tortoise_create_object` | `tool_registry.py:1234` | **no replacement** | `create_entity` | absent |
| 18 | `tortoise_create_operator` | `tool_registry.py:722` | **no replacement** | `link_entities` | absent |
| 19 | `tortoise_create_point` | `tool_registry.py:267` | **no replacement** | `create_entity` | absent |
| 20 | `tortoise_create_source` | `tool_registry.py:1328` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | warning shim |
| 21 | `tortoise_create_subject` | `tool_registry.py:1221` | **no replacement** | `create_entity` | absent |
| 22 | `tortoise_delete` | `tool_registry.py:1445` | **no replacement** | `delete_knowledge` | absent |
| 23 | `tortoise_delete_entity` | `tool_registry.py:1401` | **no replacement** | `delete_knowledge` | absent |
| 24 | `tortoise_delete_point` | `tool_registry.py:805` | **no replacement** | `delete_knowledge` | absent |
| 25 | `tortoise_diary_read` | `tool_registry.py:947` | **no replacement** | `list_knowledge` | absent |
| 26 | `tortoise_diary_write` | `tool_registry.py:938` | **no replacement** | `create_entity` | absent |
| 27 | `tortoise_dream` | `tool_registry.py:677` | `tortoise_refresh_confidence(scope="dirty"\|"full", ...)` | `refresh_confidence` | warning shim |
| 28 | `tortoise_dream_health` | `tool_registry.py:693` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | warning shim |
| 29 | `tortoise_entity_profile` | `tool_registry.py:908` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 30 | `tortoise_events_poll` | `tool_registry.py:843` | **no replacement** | `poll_events` | absent |
| 31 | `tortoise_expand_relationships` | `tool_registry.py:520` | **no replacement** | `explore_connections` | absent |
| 32 | `tortoise_explore_connections` | `tool_registry.py:1600` | **no replacement** | `explore_connections` | absent |
| 33 | `tortoise_file_decision` | `tool_registry.py:784` | `tortoise_write_question(options, evidence, choice)` | `write_question` | warning shim |
| 34 | `tortoise_file_human_approval` | `tool_registry.py:793` | `tortoise_record_decision(approver_id, artifact_id, point_ids, decision_content=...)` | `record_decision` | warning shim |
| 35 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1170` | **no replacement** | `review_link_candidates` | absent |
| 36 | `tortoise_get` | `tool_registry.py:1640` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 37 | `tortoise_get_confidence` | `tool_registry.py:658` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | warning shim |
| 38 | `tortoise_get_entity` | `tool_registry.py:1382` | **no replacement** | `get_entity` | absent |
| 39 | `tortoise_get_events` | `tool_registry.py:1254` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 40 | `tortoise_get_governance` | `tool_registry.py:1487` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 41 | `tortoise_get_operator` | `tool_registry.py:755` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 42 | `tortoise_get_point` | `tool_registry.py:467` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 43 | `tortoise_get_session` | `tool_registry.py:1262` | **no replacement** | `get_entity` | absent |
| 44 | `tortoise_get_source_reliability` | `tool_registry.py:1340` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | warning shim |
| 45 | `tortoise_graph_overview` | `tool_registry.py:1496` | **no replacement** | `graph_overview` | absent |
| 46 | `tortoise_graph_set_recording` | `tool_registry.py:1019` | **no replacement** | `graph_set_recording` | absent |
| 47 | `tortoise_health` | `tool_registry.py:972` | `tortoise_graph_overview(section="health")` | `graph_overview` | warning shim |
| 48 | `tortoise_index_files` | `tool_registry.py:1283` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | warning shim |
| 49 | `tortoise_index_sessions` | `tool_registry.py:1270` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | warning shim |
| 50 | `tortoise_index_sources_from_directory` | `tool_registry.py:1924` | **no replacement** | `index_sources_from_directory` | absent |
| 51 | `tortoise_ingest` | `tool_registry.py:1064` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 52 | `tortoise_ingest_corpus` | `tool_registry.py:1051` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | warning shim |
| 53 | `tortoise_invalidate` | `tool_registry.py:815` | `tortoise_supersede_knowledge(id, corrected_by_id, transfer_edges=False)` | `supersede_knowledge` | warning shim |
| 54 | `tortoise_issue_insight` | `tool_registry.py:1037` | **no replacement** | `search_knowledge` | absent |
| 55 | `tortoise_list_batch` | `tool_registry.py:414` | **no replacement** | `list_knowledge` | absent |
| 56 | `tortoise_list_batches` | `tool_registry.py:426` | **no replacement** | `list_knowledge` | absent |
| 57 | `tortoise_list_dedup_candidates` | `tool_registry.py:572` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | warning shim |
| 58 | `tortoise_list_graphs` | `tool_registry.py:955` | **no replacement** | `tenancy:list_memory_graphs` | absent |
| 59 | `tortoise_list_knowledge` | `tool_registry.py:1548` | **no replacement** | `list_knowledge` | absent |
| 60 | `tortoise_list_namespaces` | `tool_registry.py:405` | **no replacement** | `list_knowledge` | absent |
| 61 | `tortoise_list_pointkinds` | `tool_registry.py:389` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 62 | `tortoise_list_sources` | `tool_registry.py:397` | `tortoise_graph_overview(section="sources")` | `graph_overview` | warning shim |
| 63 | `tortoise_list_tags` | `tool_registry.py:446` | `tortoise_graph_overview(section="tags")` | `graph_overview` | warning shim |
| 64 | `tortoise_list_topics` | `tool_registry.py:1115` | `tortoise_list_knowledge(kind="topic", entity_id=...)` | `list_knowledge` | warning shim |
| 65 | `tortoise_manage_source_trust` | `tool_registry.py:1910` | **no replacement** | `manage_source_trust` | absent |
| 66 | `tortoise_manage_sources` | `tool_registry.py:1832` | **no replacement** | `manage_sources` | absent |
| 67 | `tortoise_mine_conversations` | `tool_registry.py:559` | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities=...) for a corpus — the single-transcript arm is tortoise_mine_knowledge_from_session(conversation=<transcript>, source_id=...)` | `mine_knowledge_from_directory` | warning shim |
| 68 | `tortoise_mine_knowledge_from_directory` | `tool_registry.py:1972` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 69 | `tortoise_mine_knowledge_from_session` | `tool_registry.py:1946` | **no replacement** | `mine_knowledge_from_session` | absent |
| 70 | `tortoise_mitigate_operator` | `tool_registry.py:764` | `tortoise_adjust_relationship(id, reason=..., strength=..., credibility=...)` | `adjust_relationship` | warning shim |
| 71 | `tortoise_onboarding_github_index` | `tool_registry.py:1873` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 72 | `tortoise_onboarding_session_recording` | `tool_registry.py:1864` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 73 | `tortoise_operator_action` | `tool_registry.py:1464` | `tortoise_adjust_relationship(id, action="mitigate"\|"annotate", ...)` | `adjust_relationship` | warning shim |
| 74 | `tortoise_org_create` | `tool_registry.py:1210` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 75 | `tortoise_overview` | `tool_registry.py:1629` | `tortoise_graph_overview(section=...)` | `graph_overview` | warning shim |
| 76 | `tortoise_paginated_query` | `tool_registry.py:328` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 77 | `tortoise_promote_point` | `tool_registry.py:604` | `tortoise_refresh_confidence(scope="promote", point_id=...)` | `refresh_confidence` | warning shim |
| 78 | `tortoise_provenance` | `tool_registry.py:1188` | `tortoise_check_confidence(claim_id, include_provenance=True)` | `check_confidence` | warning shim |
| 79 | `tortoise_query` | `tool_registry.py:297` | **no replacement** | `search_knowledge` | absent |
| 80 | `tortoise_query_points_by_tag` | `tool_registry.py:454` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | warning shim |
| 81 | `tortoise_recall` | `tool_registry.py:531` | **no replacement** | `check_confidence` | absent |
| 82 | `tortoise_record_decision` | `tool_registry.py:1790` | **no replacement** | `record_decision` | absent |
| 83 | `tortoise_refresh_confidence` | `tool_registry.py:1748` | **no replacement** | `refresh_confidence` | absent |
| 84 | `tortoise_register_source` | `tool_registry.py:1897` | **no replacement** | `register_source` | absent |
| 85 | `tortoise_retract_point` | `tool_registry.py:896` | `tortoise_update_knowledge(id, invalid_at=...)` | `update_knowledge` | warning shim |
| 86 | `tortoise_review_connections` | `tool_registry.py:1147` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | warning shim |
| 87 | `tortoise_review_link_candidates` | `tool_registry.py:1614` | **no replacement** | `review_link_candidates` | absent |
| 88 | `tortoise_search` | `tool_registry.py:508` | **no replacement** | `search_knowledge` | absent |
| 89 | `tortoise_search_knowledge` | `tool_registry.py:1518` | **no replacement** | `search_knowledge` | absent |
| 90 | `tortoise_search_sessions` | `tool_registry.py:1299` | **no replacement** | `search_knowledge` | absent |
| 91 | `tortoise_session_capture` | `tool_registry.py:990` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=..., machine_id=..., model=...)` | `mine_knowledge_from_session` | warning shim |
| 92 | `tortoise_session_context` | `tool_registry.py:980` | **no replacement** | `check_confidence` | absent |
| 93 | `tortoise_set_point_baseline` | `tool_registry.py:650` | `tortoise_refresh_confidence(scope="baseline", point_id=..., alpha=..., beta=...)` | `refresh_confidence` | warning shim |
| 94 | `tortoise_set_source_tier` | `tool_registry.py:1373` | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | `manage_source_trust` | warning shim |
| 95 | `tortoise_stale` | `tool_registry.py:1133` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | warning shim |
| 96 | `tortoise_status` | `tool_registry.py:963` | `tortoise_graph_overview(section="status")` | `graph_overview` | warning shim |
| 97 | `tortoise_suggest_entry_points` | `tool_registry.py:497` | **no replacement** | `search_knowledge` | absent |
| 98 | `tortoise_summarize_structure` | `tool_registry.py:357` | `tortoise_graph_overview(section="structure")` | `graph_overview` | warning shim |
| 99 | `tortoise_supersede` | `tool_registry.py:824` | `tortoise_supersede_knowledge(old_id, new_id, transfer_edges=...)` | `supersede_knowledge` | warning shim |
| 100 | `tortoise_supersede_knowledge` | `tool_registry.py:1702` | **no replacement** | `supersede_knowledge` | absent |
| 101 | `tortoise_taxonomy` | `tool_registry.py:1105` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 102 | `tortoise_traverse` | `tool_registry.py:918` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 103 | `tortoise_update` | `tool_registry.py:1427` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 104 | `tortoise_update_entity` | `tool_registry.py:1393` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 105 | `tortoise_update_knowledge` | `tool_registry.py:1671` | **no replacement** | `update_knowledge` | absent |
| 106 | `tortoise_update_point` | `tool_registry.py:706` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 107 | `tortoise_validate_domain` | `tool_registry.py:345` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | warning shim |
| 108 | `tortoise_write_question` | `tool_registry.py:1778` | **no replacement** | `write_question` | absent |

**108 served MCP tools, 29 destinations.** 53 retire **with a warning shim**; the other **55** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 53 `warning shim` names are
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
| `update_knowledge` | 5 |
| `adjust_relationship` | 4 |
| `explore_connections` | 4 |
| `index_sources_from_directory` | 4 |
| `refresh_confidence` | 4 |
| `review_link_candidates` | 4 |
| `delete_knowledge` | 3 |
| `manage_source_trust` | 3 |
| `manage_sources` | 3 |
| `mine_knowledge_from_session` | 3 |
| `supersede_knowledge` | 3 |
| `link_entities` | 2 |
| `mine_knowledge_from_directory` | 2 |
| `record_decision` | 2 |
| `register_source` | 2 |
| `write_question` | 2 |
| `approve_merge` | 1 |
| `check_connection` | 1 |
| `graph_set_recording` | 1 |
| `poll_events` | 1 |
| `sdk:write_knowledge_batch` | 1 |
| `tenancy:create_memory_graph` | 1 |
| `tenancy:list_memory_graphs` | 1 |
| **total** | **108** |

108 served tools → 29 destinations: **105** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **2** tenancy (SDK/REST only), **0** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 53 names, and they must not
contradict each other: a caller who follows a redirect to `tortoise_overview` lands
on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two
artifacts disagree about which target absorbs it.

**A disagreement is a FINDING.** The generator reports it and does not reconcile it —
resolving *which* destination is right is a design decision, not a build step.

| Current tool | 0.1 destination | PR #4031 says call | That name's 0.1 destination | Verdict |
|---|---|---|---|---|
| `tortoise_annotate_operator` | `adjust_relationship` | `tortoise_adjust_relationship(id, bias=..., precision=..., consistency=..., directness=...)` | `adjust_relationship` | AGREE |
| `tortoise_assess_source` | `manage_source_trust` | `tortoise_manage_source_trust(url, score=..., assessor=..., rationale=...)` | `manage_source_trust` | AGREE |
| `tortoise_audit` | `graph_overview` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | AGREE |
| `tortoise_belief_timeline` | `check_confidence` | `tortoise_check_confidence(topic=...)` | `check_confidence` | AGREE |
| `tortoise_checkpoint` | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., threshold=...)` | `mine_knowledge_from_session` | AGREE |
| `tortoise_create_source` | `register_source` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | AGREE |
| `tortoise_dream` | `refresh_confidence` | `tortoise_refresh_confidence(scope="dirty"\|"full", ...)` | `refresh_confidence` | AGREE |
| `tortoise_dream_health` | `graph_overview` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | AGREE |
| `tortoise_entity_profile` | `explore_connections` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | AGREE |
| `tortoise_file_decision` | `write_question` | `tortoise_write_question(options, evidence, choice)` | `write_question` | AGREE |
| `tortoise_file_human_approval` | `record_decision` | `tortoise_record_decision(approver_id, artifact_id, point_ids, decision_content=...)` | `record_decision` | AGREE |
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_confidence` | `check_confidence` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_get_source_reliability` | `list_knowledge` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_graph_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_files` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | AGREE |
| `tortoise_invalidate` | `supersede_knowledge` | `tortoise_supersede_knowledge(id, corrected_by_id, transfer_edges=False)` | `supersede_knowledge` | AGREE |
| `tortoise_list_dedup_candidates` | `review_link_candidates` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | AGREE |
| `tortoise_list_pointkinds` | `graph_overview` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | AGREE |
| `tortoise_list_sources` | `graph_overview` | `tortoise_graph_overview(section="sources")` | `graph_overview` | AGREE |
| `tortoise_list_tags` | `graph_overview` | `tortoise_graph_overview(section="tags")` | `graph_overview` | AGREE |
| `tortoise_list_topics` | `list_knowledge` | `tortoise_list_knowledge(kind="topic", entity_id=...)` | `list_knowledge` | AGREE |
| `tortoise_mine_conversations` | `mine_knowledge_from_directory` | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities=...) for a corpus — the single-transcript arm is tortoise_mine_knowledge_from_session(conversation=<transcript>, source_id=...)` | `mine_knowledge_from_directory` | AGREE |
| `tortoise_mitigate_operator` | `adjust_relationship` | `tortoise_adjust_relationship(id, reason=..., strength=..., credibility=...)` | `adjust_relationship` | AGREE |
| `tortoise_onboarding_github_index` | `manage_sources` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | AGREE |
| `tortoise_onboarding_session_recording` | `manage_sources` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | AGREE |
| `tortoise_operator_action` | `adjust_relationship` | `tortoise_adjust_relationship(id, action="mitigate"\|"annotate", ...)` | `adjust_relationship` | AGREE |
| `tortoise_overview` | `graph_overview` | `tortoise_graph_overview(section=...)` | `graph_overview` | AGREE |
| `tortoise_paginated_query` | `search_knowledge` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_promote_point` | `refresh_confidence` | `tortoise_refresh_confidence(scope="promote", point_id=...)` | `refresh_confidence` | AGREE |
| `tortoise_provenance` | `check_confidence` | `tortoise_check_confidence(claim_id, include_provenance=True)` | `check_confidence` | AGREE |
| `tortoise_query_points_by_tag` | `search_knowledge` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | AGREE |
| `tortoise_retract_point` | `update_knowledge` | `tortoise_update_knowledge(id, invalid_at=...)` | `update_knowledge` | AGREE |
| `tortoise_review_connections` | `review_link_candidates` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | AGREE |
| `tortoise_session_capture` | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=..., machine_id=..., model=...)` | `mine_knowledge_from_session` | AGREE |
| `tortoise_set_point_baseline` | `refresh_confidence` | `tortoise_refresh_confidence(scope="baseline", point_id=..., alpha=..., beta=...)` | `refresh_confidence` | AGREE |
| `tortoise_set_source_tier` | `manage_source_trust` | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | `manage_source_trust` | AGREE |
| `tortoise_stale` | `graph_overview` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | AGREE |
| `tortoise_status` | `graph_overview` | `tortoise_graph_overview(section="status")` | `graph_overview` | AGREE |
| `tortoise_summarize_structure` | `graph_overview` | `tortoise_graph_overview(section="structure")` | `graph_overview` | AGREE |
| `tortoise_supersede` | `supersede_knowledge` | `tortoise_supersede_knowledge(old_id, new_id, transfer_edges=...)` | `supersede_knowledge` | AGREE |
| `tortoise_taxonomy` | `graph_overview` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | AGREE |
| `tortoise_traverse` | `explore_connections` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | AGREE |
| `tortoise_update` | `update_knowledge` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | AGREE |
| `tortoise_update_entity` | `update_knowledge` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | AGREE |
| `tortoise_update_point` | `update_knowledge` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | AGREE |
| `tortoise_validate_domain` | `graph_overview` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | AGREE |

**53 of 53 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 53 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
