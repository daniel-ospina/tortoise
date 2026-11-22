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
| 1 | `tortoise_adjust_relationship` | `tool_registry.py:1881` | **no replacement** | `adjust_relationship` | absent |
| 2 | `tortoise_annotate_operator` | `tool_registry.py:761` | `tortoise_adjust_relationship(id, bias=..., precision=..., consistency=..., directness=...)` | `adjust_relationship` | warning shim |
| 3 | `tortoise_approve_merge` | `tool_registry.py:598` | **no replacement** | `approve_merge` | absent |
| 4 | `tortoise_assess_source` | `tool_registry.py:1402` | `tortoise_manage_source_trust(url, score=..., assessor=..., rationale=...)` | `manage_source_trust` | warning shim |
| 5 | `tortoise_audit` | `tool_registry.py:379` | `tortoise_graph_overview(section="audit", point_kinds=...)` | `graph_overview` | warning shim |
| 6 | `tortoise_belief_timeline` | `tool_registry.py:630` | `tortoise_check_confidence(topic=...)` | `check_confidence` | warning shim |
| 7 | `tortoise_calibrate_summary` | `tool_registry.py:684` | **no replacement** | `check_confidence` | absent |
| 8 | `tortoise_check_confidence` | `tool_registry.py:1715` | **no replacement** | `check_confidence` | absent |
| 9 | `tortoise_check_connection` | `tool_registry.py:2144` | **no replacement** | `check_connection` | absent |
| 10 | `tortoise_check_structure` | `tool_registry.py:346` | **no replacement** | `graph_overview` | absent |
| 11 | `tortoise_checkpoint` | `tool_registry.py:964` | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., threshold=...)` | `mine_knowledge_from_session` | warning shim |
| 12 | `tortoise_compute_confidence` | `tool_registry.py:642` | `tortoise_check_confidence(recompute=True, factors=..., evidence=..., anchors=...)` | `check_confidence` | warning shim |
| 13 | `tortoise_create_document` | `tool_registry.py:1349` | `tortoise_create_entity(type="document", name=<title>, props={"documentKind": ..., ...})` | `create_entity` | warning shim |
| 14 | `tortoise_create_edge` | `tool_registry.py:1573` | `tortoise_link_entities(relation=<predicate>, from_id=..., to_id=...)` | `link_entities` | warning shim |
| 15 | `tortoise_create_entity` | `tool_registry.py:1450` | **no replacement** | `create_entity` | absent |
| 16 | `tortoise_create_event` | `tool_registry.py:1285` | `tortoise_create_entity(type="event", name=..., props={"eventKind": ...})` | `create_entity` | warning shim |
| 17 | `tortoise_create_object` | `tool_registry.py:1273` | `tortoise_create_entity(type="object", name=..., props={"objectKind": ...})` | `create_entity` | warning shim |
| 18 | `tortoise_create_operator` | `tool_registry.py:737` | `tortoise_link_entities(relation=<op_type>, from_id=..., target_ids=..., direction=..., semantics=...)` | `link_entities` | warning shim |
| 19 | `tortoise_create_point` | `tool_registry.py:273` | `tortoise_create_entity(type="point", name=<content>, props={"pointKind": ..., ...})  (a repeat folds by content, as this door did)` | `create_entity` | warning shim |
| 20 | `tortoise_create_source` | `tool_registry.py:1367` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | warning shim |
| 21 | `tortoise_create_subject` | `tool_registry.py:1260` | `tortoise_create_entity(type="subject", name=..., props={"subjectKind": ...})` | `create_entity` | warning shim |
| 22 | `tortoise_delete` | `tool_registry.py:1550` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | warning shim |
| 23 | `tortoise_delete_entity` | `tool_registry.py:1440` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | warning shim |
| 24 | `tortoise_delete_knowledge` | `tool_registry.py:1514` | **no replacement** | `delete_knowledge` | absent |
| 25 | `tortoise_delete_point` | `tool_registry.py:820` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | warning shim |
| 26 | `tortoise_diary_read` | `tool_registry.py:983` | **no replacement** | `list_knowledge` | absent |
| 27 | `tortoise_diary_write` | `tool_registry.py:974` | `tortoise_create_entity(type="diary", name=<entry>, props={"authoredBy": ..., "topic": ..., "wing": ...})` | `create_entity` | warning shim |
| 28 | `tortoise_dream` | `tool_registry.py:692` | `tortoise_refresh_confidence(scope="dirty"\|"full", ...)` | `refresh_confidence` | warning shim |
| 29 | `tortoise_dream_health` | `tool_registry.py:708` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | warning shim |
| 30 | `tortoise_entity_profile` | `tool_registry.py:938` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 31 | `tortoise_events_poll` | `tool_registry.py:916` | `tortoise_poll_events(after=..., types=..., limit=...)` | `poll_events` | warning shim |
| 32 | `tortoise_expand_relationships` | `tool_registry.py:535` | **no replacement** | `explore_connections` | absent |
| 33 | `tortoise_explore_connections` | `tool_registry.py:1759` | **no replacement** | `explore_connections` | absent |
| 34 | `tortoise_file_decision` | `tool_registry.py:799` | `tortoise_write_question(options, evidence, choice)` | `write_question` | warning shim |
| 35 | `tortoise_file_human_approval` | `tool_registry.py:808` | `tortoise_record_decision(approver_id, artifact_id, point_ids, decision_content=...)` | `record_decision` | warning shim |
| 36 | `tortoise_find_cross_lens_candidates` | `tool_registry.py:1209` | `tortoise_review_link_candidates(kind="cross_lens", limit=..., threshold=...)` | `review_link_candidates` | warning shim |
| 37 | `tortoise_get` | `tool_registry.py:1799` | `tortoise_get_entity(id, type=...)` | `get_entity` | warning shim |
| 38 | `tortoise_get_confidence` | `tool_registry.py:673` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | warning shim |
| 39 | `tortoise_get_entity` | `tool_registry.py:1421` | **no replacement** | `get_entity` | absent |
| 40 | `tortoise_get_events` | `tool_registry.py:1293` | `tortoise_get_entity(None, type="events")` | `get_entity` | warning shim |
| 41 | `tortoise_get_governance` | `tool_registry.py:1585` | `tortoise_get_entity(id, type="governance")` | `get_entity` | warning shim |
| 42 | `tortoise_get_historical_knowledge` | `tool_registry.py:1599` | **no replacement** | `get_historical_knowledge` | absent |
| 43 | `tortoise_get_operator` | `tool_registry.py:770` | `tortoise_get_entity(id, type="operator")` | `get_entity` | warning shim |
| 44 | `tortoise_get_point` | `tool_registry.py:476` | `tortoise_get_entity(id, type="point")` | `get_entity` | warning shim |
| 45 | `tortoise_get_session` | `tool_registry.py:1301` | `tortoise_get_entity(id, type="session")` | `get_entity` | warning shim |
| 46 | `tortoise_get_source_reliability` | `tool_registry.py:1379` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | warning shim |
| 47 | `tortoise_graph_overview` | `tool_registry.py:1638` | **no replacement** | `graph_overview` | absent |
| 48 | `tortoise_graph_set_recording` | `tool_registry.py:1055` | **no replacement** | `graph_set_recording` | absent |
| 49 | `tortoise_health` | `tool_registry.py:1008` | `tortoise_graph_overview(section="health")` | `graph_overview` | warning shim |
| 50 | `tortoise_index_files` | `tool_registry.py:1322` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | warning shim |
| 51 | `tortoise_index_sessions` | `tool_registry.py:1309` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | warning shim |
| 52 | `tortoise_index_sources_from_directory` | `tool_registry.py:2083` | **no replacement** | `index_sources_from_directory` | absent |
| 53 | `tortoise_ingest` | `tool_registry.py:1100` | **no replacement** | `sdk:write_knowledge_batch` | absent |
| 54 | `tortoise_ingest_corpus` | `tool_registry.py:1087` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | warning shim |
| 55 | `tortoise_invalidate` | `tool_registry.py:830` | `tortoise_supersede_knowledge(id, corrected_by_id, transfer_edges=False)` | `supersede_knowledge` | warning shim |
| 56 | `tortoise_issue_insight` | `tool_registry.py:1073` | `tortoise_search_knowledge(title=..., body=..., repo=..., mode="issue_insight")` | `search_knowledge` | warning shim |
| 57 | `tortoise_link_entities` | `tool_registry.py:1619` | **no replacement** | `link_entities` | absent |
| 58 | `tortoise_list_batch` | `tool_registry.py:423` | `tortoise_list_knowledge(kind="batch", id=...)` | `list_knowledge` | warning shim |
| 59 | `tortoise_list_batches` | `tool_registry.py:435` | **no replacement** | `list_knowledge` | absent |
| 60 | `tortoise_list_dedup_candidates` | `tool_registry.py:587` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | warning shim |
| 61 | `tortoise_list_graphs` | `tool_registry.py:991` | `tortoise_graph_overview(section="graphs")` | `graph_overview` | warning shim |
| 62 | `tortoise_list_knowledge` | `tool_registry.py:1690` | **no replacement** | `list_knowledge` | absent |
| 63 | `tortoise_list_namespaces` | `tool_registry.py:414` | **no replacement** | `list_knowledge` | absent |
| 64 | `tortoise_list_pointkinds` | `tool_registry.py:398` | `tortoise_graph_overview(section="pointkinds")` | `graph_overview` | warning shim |
| 65 | `tortoise_list_sources` | `tool_registry.py:406` | `tortoise_graph_overview(section="sources")` | `graph_overview` | warning shim |
| 66 | `tortoise_list_tags` | `tool_registry.py:455` | `tortoise_graph_overview(section="tags")` | `graph_overview` | warning shim |
| 67 | `tortoise_list_topics` | `tool_registry.py:1151` | `tortoise_list_knowledge(kind="topic", entity_id=...)` | `list_knowledge` | warning shim |
| 68 | `tortoise_manage_source_trust` | `tool_registry.py:2069` | **no replacement** | `manage_source_trust` | absent |
| 69 | `tortoise_manage_sources` | `tool_registry.py:1991` | **no replacement** | `manage_sources` | absent |
| 70 | `tortoise_mine_conversations` | `tool_registry.py:574` | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities=...) for a corpus — the single-transcript arm is tortoise_mine_knowledge_from_session(conversation=<transcript>, source_id=...)` | `mine_knowledge_from_directory` | warning shim |
| 71 | `tortoise_mine_knowledge_from_directory` | `tool_registry.py:2131` | **no replacement** | `mine_knowledge_from_directory` | absent |
| 72 | `tortoise_mine_knowledge_from_session` | `tool_registry.py:2105` | **no replacement** | `mine_knowledge_from_session` | absent |
| 73 | `tortoise_mitigate_operator` | `tool_registry.py:779` | `tortoise_adjust_relationship(id, reason=..., strength=..., credibility=...)` | `adjust_relationship` | warning shim |
| 74 | `tortoise_onboarding_github_index` | `tool_registry.py:2032` | `tortoise_manage_sources(action="enable", source="github_issues") (no per-repo scope: it indexes every connected repo)` | `manage_sources` | warning shim |
| 75 | `tortoise_onboarding_session_recording` | `tool_registry.py:2023` | `tortoise_manage_sources(action="enable" or "disable", source="agent_sessions")` | `manage_sources` | warning shim |
| 76 | `tortoise_operator_action` | `tool_registry.py:1562` | `tortoise_adjust_relationship(id, action="mitigate"\|"annotate", ...)` | `adjust_relationship` | warning shim |
| 77 | `tortoise_org_create` | `tool_registry.py:1249` | **no replacement** | `tenancy:create_memory_graph` | absent |
| 78 | `tortoise_overview` | `tool_registry.py:1788` | `tortoise_graph_overview(section=...)` | `graph_overview` | warning shim |
| 79 | `tortoise_paginated_query` | `tool_registry.py:337` | `tortoise_query(offset=..., limit=...)` | `search_knowledge` | warning shim |
| 80 | `tortoise_poll_events` | `tool_registry.py:861` | **no replacement** | `poll_events` | absent |
| 81 | `tortoise_promote_point` | `tool_registry.py:619` | `tortoise_refresh_confidence(scope="promote", point_id=...)` | `refresh_confidence` | warning shim |
| 82 | `tortoise_provenance` | `tool_registry.py:1227` | `tortoise_check_confidence(claim_id, include_provenance=True)` | `check_confidence` | warning shim |
| 83 | `tortoise_query` | `tool_registry.py:306` | **no replacement** | `search_knowledge` | absent |
| 84 | `tortoise_query_points_by_tag` | `tool_registry.py:463` | `tortoise_query(tag=..., offset=..., limit=...)` | `search_knowledge` | warning shim |
| 85 | `tortoise_recall` | `tool_registry.py:546` | `tortoise_check_confidence(recall=True, query=..., mode="state", min_confidence=..., limit=...)` | `check_confidence` | warning shim |
| 86 | `tortoise_record_decision` | `tool_registry.py:1949` | **no replacement** | `record_decision` | absent |
| 87 | `tortoise_refresh_confidence` | `tool_registry.py:1907` | **no replacement** | `refresh_confidence` | absent |
| 88 | `tortoise_register_source` | `tool_registry.py:2056` | **no replacement** | `register_source` | absent |
| 89 | `tortoise_retract_point` | `tool_registry.py:926` | `tortoise_update_knowledge(id, invalid_at=...)` | `update_knowledge` | warning shim |
| 90 | `tortoise_review_connections` | `tool_registry.py:1186` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | warning shim |
| 91 | `tortoise_review_link_candidates` | `tool_registry.py:1773` | **no replacement** | `review_link_candidates` | absent |
| 92 | `tortoise_search` | `tool_registry.py:517` | **no replacement** | `search_knowledge` | absent |
| 93 | `tortoise_search_knowledge` | `tool_registry.py:1660` | **no replacement** | `search_knowledge` | absent |
| 94 | `tortoise_search_sessions` | `tool_registry.py:1338` | **no replacement** | `search_knowledge` | absent |
| 95 | `tortoise_session_capture` | `tool_registry.py:1026` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=..., machine_id=..., model=...)` | `mine_knowledge_from_session` | warning shim |
| 96 | `tortoise_session_context` | `tool_registry.py:1016` | `tortoise_check_confidence(session=True)` | `check_confidence` | warning shim |
| 97 | `tortoise_set_point_baseline` | `tool_registry.py:665` | `tortoise_refresh_confidence(scope="baseline", point_id=..., alpha=..., beta=...)` | `refresh_confidence` | warning shim |
| 98 | `tortoise_set_source_tier` | `tool_registry.py:1412` | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | `manage_source_trust` | warning shim |
| 99 | `tortoise_stale` | `tool_registry.py:1172` | `tortoise_graph_overview(section="stale", days=..., limit=...)` | `graph_overview` | warning shim |
| 100 | `tortoise_status` | `tool_registry.py:999` | `tortoise_graph_overview(section="status")` | `graph_overview` | warning shim |
| 101 | `tortoise_suggest_entry_points` | `tool_registry.py:506` | **no replacement** | `search_knowledge` | absent |
| 102 | `tortoise_summarize_structure` | `tool_registry.py:366` | `tortoise_graph_overview(section="structure")` | `graph_overview` | warning shim |
| 103 | `tortoise_supersede` | `tool_registry.py:839` | `tortoise_supersede_knowledge(old_id, new_id, transfer_edges=...)` | `supersede_knowledge` | warning shim |
| 104 | `tortoise_supersede_knowledge` | `tool_registry.py:1861` | **no replacement** | `supersede_knowledge` | absent |
| 105 | `tortoise_taxonomy` | `tool_registry.py:1141` | `tortoise_graph_overview(section="taxonomy")` | `graph_overview` | warning shim |
| 106 | `tortoise_traverse` | `tool_registry.py:954` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | warning shim |
| 107 | `tortoise_update` | `tool_registry.py:1496` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 108 | `tortoise_update_entity` | `tool_registry.py:1432` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 109 | `tortoise_update_knowledge` | `tool_registry.py:1830` | **no replacement** | `update_knowledge` | absent |
| 110 | `tortoise_update_point` | `tool_registry.py:721` | `tortoise_update_knowledge(id, props=...)` | `update_knowledge` | warning shim |
| 111 | `tortoise_validate_domain` | `tool_registry.py:354` | `tortoise_graph_overview(section="validate_domain", domain=...)` | `graph_overview` | warning shim |
| 112 | `tortoise_write_question` | `tool_registry.py:1937` | **no replacement** | `write_question` | absent |

**112 served MCP tools, 29 destinations.** 73 retire **with a warning shim**; the other **39** are simply absent — no shim.

**`Retirement` is today's behaviour, not a forecast.** PR #4031 MERGED as `e3bb78a14` (2026-09-21), so the 73 `warning shim` names are
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

112 served tools → 29 destinations: **110** absorbed into the **27** MCP targets, **1** into a builder-only SDK method (not on the MCP), **1** tenancy (SDK/REST only), **0** removed.

---

## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`

PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*
destinations. They answer different questions about the same 73 names, and they must not
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
| `tortoise_compute_confidence` | `check_confidence` | `tortoise_check_confidence(recompute=True, factors=..., evidence=..., anchors=...)` | `check_confidence` | AGREE |
| `tortoise_create_document` | `create_entity` | `tortoise_create_entity(type="document", name=<title>, props={"documentKind": ..., ...})` | `create_entity` | AGREE |
| `tortoise_create_edge` | `link_entities` | `tortoise_link_entities(relation=<predicate>, from_id=..., to_id=...)` | `link_entities` | AGREE |
| `tortoise_create_event` | `create_entity` | `tortoise_create_entity(type="event", name=..., props={"eventKind": ...})` | `create_entity` | AGREE |
| `tortoise_create_object` | `create_entity` | `tortoise_create_entity(type="object", name=..., props={"objectKind": ...})` | `create_entity` | AGREE |
| `tortoise_create_operator` | `link_entities` | `tortoise_link_entities(relation=<op_type>, from_id=..., target_ids=..., direction=..., semantics=...)` | `link_entities` | AGREE |
| `tortoise_create_point` | `create_entity` | `tortoise_create_entity(type="point", name=<content>, props={"pointKind": ..., ...})  (a repeat folds by content, as this door did)` | `create_entity` | AGREE |
| `tortoise_create_source` | `register_source` | `tortoise_register_source(url, sourceKind, tier=...)` | `register_source` | AGREE |
| `tortoise_create_subject` | `create_entity` | `tortoise_create_entity(type="subject", name=..., props={"subjectKind": ...})` | `create_entity` | AGREE |
| `tortoise_delete` | `delete_knowledge` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | AGREE |
| `tortoise_delete_entity` | `delete_knowledge` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | AGREE |
| `tortoise_delete_point` | `delete_knowledge` | `tortoise_delete_knowledge(id, dry_run=True) for the preview, then tortoise_delete_knowledge(id, confirmation=<token>)` | `delete_knowledge` | AGREE |
| `tortoise_diary_write` | `create_entity` | `tortoise_create_entity(type="diary", name=<entry>, props={"authoredBy": ..., "topic": ..., "wing": ...})` | `create_entity` | AGREE |
| `tortoise_dream` | `refresh_confidence` | `tortoise_refresh_confidence(scope="dirty"\|"full", ...)` | `refresh_confidence` | AGREE |
| `tortoise_dream_health` | `graph_overview` | `tortoise_graph_overview(section="dream_health")` | `graph_overview` | AGREE |
| `tortoise_entity_profile` | `explore_connections` | `tortoise_explore_connections(node_id, hops=...)` | `explore_connections` | AGREE |
| `tortoise_events_poll` | `poll_events` | `tortoise_poll_events(after=..., types=..., limit=...)` | `poll_events` | AGREE |
| `tortoise_file_decision` | `write_question` | `tortoise_write_question(options, evidence, choice)` | `write_question` | AGREE |
| `tortoise_file_human_approval` | `record_decision` | `tortoise_record_decision(approver_id, artifact_id, point_ids, decision_content=...)` | `record_decision` | AGREE |
| `tortoise_find_cross_lens_candidates` | `review_link_candidates` | `tortoise_review_link_candidates(kind="cross_lens", limit=..., threshold=...)` | `review_link_candidates` | AGREE |
| `tortoise_get` | `get_entity` | `tortoise_get_entity(id, type=...)` | `get_entity` | AGREE |
| `tortoise_get_confidence` | `check_confidence` | `tortoise_check_confidence(claim_id=...)` | `check_confidence` | AGREE |
| `tortoise_get_events` | `get_entity` | `tortoise_get_entity(None, type="events")` | `get_entity` | AGREE |
| `tortoise_get_governance` | `get_entity` | `tortoise_get_entity(id, type="governance")` | `get_entity` | AGREE |
| `tortoise_get_operator` | `get_entity` | `tortoise_get_entity(id, type="operator")` | `get_entity` | AGREE |
| `tortoise_get_point` | `get_entity` | `tortoise_get_entity(id, type="point")` | `get_entity` | AGREE |
| `tortoise_get_session` | `get_entity` | `tortoise_get_entity(id, type="session")` | `get_entity` | AGREE |
| `tortoise_get_source_reliability` | `list_knowledge` | `tortoise_list_knowledge(kind="source_reliability", url=...)` | `list_knowledge` | AGREE |
| `tortoise_health` | `graph_overview` | `tortoise_graph_overview(section="health")` | `graph_overview` | AGREE |
| `tortoise_index_files` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type=..., progress_file=...)` | `index_sources_from_directory` | AGREE |
| `tortoise_index_sessions` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="agent_session")` | `index_sources_from_directory` | AGREE |
| `tortoise_ingest_corpus` | `index_sources_from_directory` | `tortoise_index_sources_from_directory(directory, file_type="doc")` | `index_sources_from_directory` | AGREE |
| `tortoise_invalidate` | `supersede_knowledge` | `tortoise_supersede_knowledge(id, corrected_by_id, transfer_edges=False)` | `supersede_knowledge` | AGREE |
| `tortoise_issue_insight` | `search_knowledge` | `tortoise_search_knowledge(title=..., body=..., repo=..., mode="issue_insight")` | `search_knowledge` | AGREE |
| `tortoise_list_batch` | `list_knowledge` | `tortoise_list_knowledge(kind="batch", id=...)` | `list_knowledge` | AGREE |
| `tortoise_list_dedup_candidates` | `review_link_candidates` | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | `review_link_candidates` | AGREE |
| `tortoise_list_graphs` | `graph_overview` | `tortoise_graph_overview(section="graphs")` | `graph_overview` | AGREE |
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
| `tortoise_recall` | `check_confidence` | `tortoise_check_confidence(recall=True, query=..., mode="state", min_confidence=..., limit=...)` | `check_confidence` | AGREE |
| `tortoise_retract_point` | `update_knowledge` | `tortoise_update_knowledge(id, invalid_at=...)` | `update_knowledge` | AGREE |
| `tortoise_review_connections` | `review_link_candidates` | `tortoise_review_link_candidates(kind="connections", mode=...)` | `review_link_candidates` | AGREE |
| `tortoise_session_capture` | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session(conversation=..., harness=..., session_id=..., machine_id=..., model=...)` | `mine_knowledge_from_session` | AGREE |
| `tortoise_session_context` | `check_confidence` | `tortoise_check_confidence(session=True)` | `check_confidence` | AGREE |
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

**73 of 73 agree; 0 disagree.**

### B1 — the disagreements

**None — 0 findings.** The 0.1 map and the redirect agree on the target for all 73 names. This section is kept, not deleted: an explicit empty finding list stays machine-visible, and a section that silently vanished would hide the difference between *no disagreements* and *nobody looked*.

---

## Reproduce

```bash
uv run python tools/mcp_rename_table.py          # regenerate this file
uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift
```
