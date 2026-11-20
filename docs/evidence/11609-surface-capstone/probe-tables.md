# #13036 — surface probe tables (GENERATED, do not hand-edit)

Produced by `uv run python tools/surface_capstone_probe.py --markdown <this file>`.
Regenerate rather than edit — a hand-typed count is the defect `#4185` was filed about,
and a hand-typed status is the same defect one level down.

Generated at `239b5abfd9` — the `HEAD` the probe was run FROM, derived at run time
(`git rev-parse --short=10 HEAD`, read before this file is written), never typed. It
cannot be the commit that carries this line: a file cannot name its own commit, so it is
always the parent of the regeneration commit. A `-dirty` suffix means the run measured a
working tree with uncommitted changes, so the tables are not reproducible from that sha.

Every row is ONE REAL INVOCATION: MCP through `create_http_app` (the authenticated
tenant door `hosted_api` mounts), SDK against a throwaway embedded store. Latency is
the call itself, not the fixture.

A status is one of: **PASS** (answered, seeded content readable back), **REFUSED**
(a measured refusal — `-32004` from `http_policy=False`, or a named product
precondition), **MISSING** (no live door at all), **ARM GAP** (the success arm is
unreachable from any seed the product accepts), **UNMEASURED** (cannot be driven
from this box; the mechanism is named).

## Approved MCP tools — one real call each, through the authenticated tenant door

| # | approved tool | served door | status | latency | answer shape | note |
|---|---|---|---|---|---|---|
| 1 | `search_knowledge` | `tortoise_search_knowledge` | **PASS** | 915 ms | `list[5] of dict{id, content, point_kind, match_source, relationships}` |  |
| 2 | `list_knowledge` | `tortoise_list_knowledge` | **PASS** | 468 ms | `dict{results, total, hasMore}` |  |
| 3 | `check_confidence` | `tortoise_check_confidence` | **PASS** | 417 ms | `dict{mean, variance, alpha, beta, effective_n}` |  |
| 4 | `get_entity` | `tortoise_get_entity` | **PASS** | 260 ms | `dict{id, content, pointKind, is_operator, status, createdAt…}` |  |
| 5 | `get_historical_knowledge` | `—` | **MISSING** | — | `` | NO LIVE DOOR — approved (beta row 7) and implemented on the SDK, but no MCP tool binds it at this head. Owner: #13036 (the MCP half of the 27-tool … |
| 6 | `explore_connections` | `tortoise_explore_connections` | **PASS** | 318 ms | `dict{entity, connected}` |  |
| 7 | `graph_overview` | `tortoise_graph_overview` | **PASS** | 128 ms | `list[2] of dict{kind, count, pack}` |  |
| 8 | `review_link_candidates` | `tortoise_review_link_candidates` | **PASS** | 1271 ms | `dict{add, prune}` |  |
| 9 | `poll_events` | `tortoise_events_poll` | **PASS** | 100 ms | `dict{events, next_cursor}` |  |
| 10 | `create_entity` | `tortoise_create_entity` | **PASS** | 3779 ms | `dict{node, nudges, advisories}` |  |
| 11 | `register_source` | `tortoise_register_source` | **PASS** | 345 ms | `dict{canonicalUrl, contentHash, externalId, id, ingestedAt, sourceKind…}` |  |
| 12 | `index_sources_from_directory` | `tortoise_index_sources_from_directory` | **REFUSED** | 77 ms | `dict{error}` | the directory argument is a caller-controlled filesystem walk on the HOST — door said: {'jsonrpc': '2.0', 'error': {'code': -32004, 'message': 'Thi… |
| 13 | `mine_knowledge_from_session` | `tortoise_mine_knowledge_from_session` | **PASS** | 791 ms | `dict{filed, duplicates}` |  |
| 14 | `mine_knowledge_from_directory` | `tortoise_mine_knowledge_from_directory` | **REFUSED** | 30 ms | `dict{error}` | same HOST filesystem walk as index_sources_from_directory — door said: {'jsonrpc': '2.0', 'error': {'code': -32004, 'message': 'This tool is not av… |
| 15 | `manage_source_trust` | `tortoise_manage_source_trust` | **PASS** | 382 ms | `dict{url, credibilityTier, sourceKind}` |  |
| 16 | `link_entities` | `tortoise_create_edge` | **PASS** | 410 ms | `dict{edge, created, nudges}` |  |
| 17 | `write_question` | `tortoise_write_question` | **PASS** | 2221 ms | `dict{decision_id, option_ids, evidence_ids}` |  |
| 18 | `record_decision` | `tortoise_record_decision` | **PASS** | 2884 ms | `dict{event_id, decision_point_id, impl_operator_ids, confidence_delta}` |  |
| 19 | `update_knowledge` | `tortoise_update_knowledge` | **PASS** | 406 ms | `dict{confidence, content, content_hash, createdAt, embedding, ep_alpha…}` |  |
| 20 | `supersede_knowledge` | `tortoise_supersede_knowledge` | **PASS** | 760 ms | `dict{invalidated, id, corrected_by, edges_transferred}` |  |
| 21 | `delete_knowledge` | `tortoise_delete` | **PASS** | 201 ms | `dict{deleted, id}` | preview then confirm — `deleted` is True, and the re-read no longer serves the node |
| 22 | `adjust_relationship` | `tortoise_adjust_relationship` | **PASS** | 571 ms | `dict{id, content, pointKind, mitigation_strength, is_operator, status…}` |  |
| 23 | `refresh_confidence` | `tortoise_refresh_confidence` | **REFUSED** | 82 ms | `dict{error}` | the refresh family reaches whole-graph EP (#329); served by REST, not the tenant MCP door — door said: {'jsonrpc': '2.0', 'error': {'code': -32004,… |
| 24 | `approve_merge` | `tortoise_approve_merge` | **REFUSED** | 117 ms | `dict{error}` | the product refuses a non-candidate BY NAME (#2952) — the SUCCESS arm is the gap, so the refusal is the answer a caller with no ambiguity pair gets… |
| 25 | `check_connection` | `tortoise_check_connection` | **PASS** | 104 ms | `dict{connected, graph, account, account_error, key, key_error}` |  |
| 26 | `graph_set_recording` | `tortoise_graph_set_recording` | **PASS** | 221 ms | `dict{graph_id, recording}` |  |
| 27 | `manage_sources` | `tortoise_manage_sources` | **PASS** | 257 ms | `dict{sources, onboarding}` |  |

## Approved SDK methods — one real call each, against a throwaway embedded store

| # | approved method | status | latency | answer shape | note |
|---|---|---|---|---|---|
| 1 | `Tortoise` | **PASS** | 0 ms | `TortoiseSDK` | the row-1 entry point constructs and closes |
| 2 | `close` | **PASS** | 0 ms | `none` |  |
| 3 | `search_knowledge` | **PASS** | 547 ms | `list[10] of dict{id, content, point_kind, match_source, relationships}` |  |
| 4 | `list_knowledge` | **PASS** | 74 ms | `dict{results, total, hasMore}` |  |
| 5 | `check_confidence` | **PASS** | 1871 ms | `dict{mean, variance, alpha, beta, effective_n}` | default `require_calibration=None` raises (CalibrationError: dream: 5 uncalibrated evidence points. First 10: ['1a1fad0b186-d8af8bd469b8', '1a1fad0… |
| 6 | `get_entity` | **PASS** | 7 ms | `dict{confidence, content, content_hash, createdAt, embedding, ep_alpha…}` |  |
| 7 | `get_historical_knowledge` | **PASS** | 12 ms | `dict{point_id, at_date, current, chain, valid_point, found…}` |  |
| 8 | `explore_connections` | **PASS** | 91 ms | `dict{entity, connected}` |  |
| 9 | `graph_overview` | **PASS** | 3 ms | `list[6] of dict{kind, count, pack}` |  |
| 10 | `review_link_candidates` | **PASS** | 1422 ms | `dict{add, prune}` |  |
| 11 | `poll_events` | **PASS** | 8 ms | `dict{events, next_cursor}` |  |
| 12 | `create_entity` | **PASS** | 304 ms | `dict{node, nudges, advisories}` |  |
| 13 | `write_knowledge_batch` | **PASS** | 361 ms | `dict{granularity, batch_id, created, deduped, ids, nudges…}` |  |
| 14 | `register_source` | **PASS** | 36 ms | `dict{canonicalUrl, contentHash, externalId, id, ingestedAt, sourceKind…}` |  |
| 15 | `index_sources_from_directory` | **PASS** | 181 ms | `dict{directory, corpus_name, file_count, indexed, updated, skipped…}` |  |
| 16 | `mine_knowledge_from_session` | **PASS** | 616 ms | `dict{filed, duplicates}` |  |
| 17 | `mine_knowledge_from_directory` | **PASS** | 394 ms | `dict{sessions, ingested, updated, skipped, failed, entities…}` |  |
| 18 | `manage_source_trust` | **PASS** | 66 ms | `dict{url, credibilityTier, sourceKind}` |  |
| 19 | `link_entities` | **PASS** | 131 ms | `dict{direct_edge, from, to, created, deduped}` |  |
| 20 | `write_question` | **PASS** | 1437 ms | `dict{decision_id, option_ids, evidence_ids}` |  |
| 21 | `record_decision` | **PASS** | 3752 ms | `dict{event_id, decision_point_id, impl_operator_ids, confidence_delta}` |  |
| 22 | `update_knowledge` | **PASS** | 86 ms | `dict{confidence, content, content_hash, createdAt, embedding, ep_alpha…}` |  |
| 23 | `supersede_knowledge` | **PASS** | 212 ms | `dict{invalidated, id, corrected_by, edges_transferred}` |  |
| 24 | `delete_knowledge` | **PASS** | 111 ms | `bool` |  |
| 25 | `adjust_relationship` | **PASS** | 110 ms | `dict{id, is_operator, op_type, direction, direction_basis, status…}` |  |
| 26 | `refresh_confidence` | **PASS** | 187 ms | `dict{iterations, converged, confidences, diagnostic, truncated, operators_seen…}` |  |
| 27 | `approve_merge` | **ARM GAP** | 13 ms | `exception` | ARM GAP — the success arm needs a dedup/temporal candidate; the product refuses this one by name: ValueError: Point '1a1facfe1c1-855332cb6cdd' is n… |
| 28 | `get_organisation_account` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 29 | `create_memory_graph` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 30 | `update_memory_graph` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 31 | `list_memory_graphs` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 32 | `delete_memory_graph` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 33 | `restore_memory_graph` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 34 | `create_key` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 35 | `list_keys` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 36 | `revoke_key` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 37 | `add_member` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 38 | `list_members` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 39 | `remove_member` | **UNMEASURED** | — | `` | UNMEASURED — control plane: this method calls the Supabase control plane, which needs a hosted credential this box does not carry. Calling it again… |
| 40 | `check_connection` | **PASS** | 2 ms | `dict{connected, graph, account, account_error, key, key_error}` |  |

## V3 — the retirement shim: one retired name per destination family

| retired name | notice published | use_instead | what the retired door did |
|---|---|---|---|
| `tortoise_mitigate_operator` | YES | `tortoise_adjust_relationship(id, reason=..., strength=..., credibility` | answered (previous behaviour) |
| `tortoise_get_confidence` | YES | `tortoise_check_confidence(claim_id=...)` | answered (previous behaviour) |
| `tortoise_create_point` | YES | `tortoise_create_entity(type="point", name=<content>, props={"pointKind` | answered (previous behaviour) |
| `tortoise_delete_point` | YES | `tortoise_delete(id, dry_run=True) for the preview, then tortoise_delet` | answered (previous behaviour) |
| `tortoise_entity_profile` | YES | `tortoise_explore_connections(node_id, hops=...)` | answered (previous behaviour) |
| `tortoise_get` | YES | `tortoise_get_entity(id, type=...)` | answered (previous behaviour) |
| `tortoise_health` | YES | `tortoise_graph_overview(section="health")` | answered (previous behaviour) |
| `tortoise_index_files` | YES | `tortoise_index_sources_from_directory(directory, file_type=..., progre` | REFUSED (-32004, http_policy=False by design) |
| `tortoise_list_batch` | YES | `tortoise_list_knowledge(kind="batch", id=...)` | answered (previous behaviour) |
| `tortoise_set_source_tier` | YES | `tortoise_manage_source_trust(url, tier="T0"-"T4")` | answered (previous behaviour) |
| `tortoise_onboarding_github_index` | YES | `tortoise_manage_sources(action="enable", source="github_issues") (no p` | REFUSED (GitHub not connected. Authorise it from the dashboard (`POST /v1/onboa) |
| `tortoise_mine_conversations` | YES | `tortoise_mine_knowledge_from_directory(directory=..., extract_entities` | REFUSED (-32004, http_policy=False by design) |
| `tortoise_checkpoint` | YES | `tortoise_mine_knowledge_from_session(items=..., agent_name=..., thresh` | answered (previous behaviour) |
| `tortoise_paginated_query` | YES | `tortoise_query(offset=..., limit=...)` | answered (previous behaviour) |
| `tortoise_file_human_approval` | YES | `tortoise_record_decision(approver_id, artifact_id, point_ids, decision` | answered (previous behaviour) |
| `tortoise_dream` | YES | `tortoise_refresh_confidence(scope="dirty"\|"full", ...)` | REFUSED (-32004, http_policy=False by design) |
| `tortoise_create_source` | YES | `tortoise_register_source(url, sourceKind, tier=...)` | answered (previous behaviour) |
| `tortoise_list_dedup_candidates` | YES | `tortoise_review_link_candidates(kind="dedup", candidate_type=...)` | answered (previous behaviour) |
| `tortoise_issue_insight` | YES | `tortoise_search_knowledge(title=..., body=..., repo=..., mode="issue_i` | answered (previous behaviour) |
| `tortoise_supersede` | YES | `tortoise_supersede_knowledge(old_id, new_id, transfer_edges=...)` | answered (previous behaviour) |
| `tortoise_update_point` | YES | `tortoise_update_knowledge(id, props=...)` | answered (previous behaviour) |
| `tortoise_file_decision` | YES | `tortoise_write_question(options, evidence, choice)` | answered (previous behaviour) |

## V3 — archived with no destination: the documented not-found

| archived name | answer |
|---|---|
| `tortoise_analyze` | `Unknown tool: 'tortoise_analyze'` |
| `tortoise_backfill_v25` | `Unknown tool: 'tortoise_backfill_v25'` |
| `tortoise_packs_list` | `Unknown tool: 'tortoise_packs_list'` |
| `tortoise_pack_install` | `Unknown tool: 'tortoise_pack_install'` |
| `tortoise_onboarding_state` | `Unknown tool: 'tortoise_onboarding_state'` |
| `tortoise_onboarding_seed` | `Unknown tool: 'tortoise_onboarding_seed'` |
| `tortoise_onboarding_demo_create` | `Unknown tool: 'tortoise_onboarding_demo_create'` |
| `tortoise_onboarding_github_connect` | `Unknown tool: 'tortoise_onboarding_github_connect'` |
| `tortoise_onboarding_github_status` | `Unknown tool: 'tortoise_onboarding_github_status'` |
