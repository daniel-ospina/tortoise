# Archive index — code removed from the product surface

> **Generated** by `uv run python tools/archive_surface.py index`. Do not edit by hand.
> The machine-readable record is [`MANIFEST.json`](MANIFEST.json); this file is its
> readable view, and `tools/archive_surface.py check` fails when the two disagree.

An archived symbol is **out of the product**: it is not importable from `tortoise`, it
is not served on the MCP surface, and no retired-forward name points at it. The code
is kept here so a removal can be inspected and — if it was a mistake — read back.
The mechanism and its rules: [`README.md`](README.md).

**4 archived MCP tools · 106 archived SDK methods · 1 archived modules.**

## MCP tools (4)

| archived tool | batch | reason | archived from |
|---|---|---|---|
| `tortoise_analyze` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `b85194d6bcb1` |
| `tortoise_backfill_v25` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_pack_install` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |
| `tortoise_packs_list` | b1-removed-no-destination | no destination in the approved 27-tool surface; no replacement (owner ruling, #11609) | `tortoise/tool_registry.py` @ `73c479ce405b` |

## SDK methods (106)

| archived method | batch | reason | archived from |
|---|---|---|---|
| `annotate_ask_hits` | 12901-sdk-final | Body survives PRIVATE as `_annotate_ask_hits`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `annotate_operator` | 12901-sdk-final | Body survives PRIVATE as `_annotate_operator`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `apikey_create` | 12901-wave-2 | Mint a control-plane API key. Body survives PRIVATE as `_apikey_create`; recorded destination `create_key` (row 34). | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `apikey_revoke` | 12901-wave-2 | Revoke a key (soft delete). Body survives PRIVATE as `_apikey_revoke`; recorded destination `revoke_key` (row 36). | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `apikey_verify` | 12901-wave-2 | Verify a presented key against stored hashes. Body survives PRIVATE as `_apikey_verify`; beta files it under CONSOLE PLUMBING; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `audit` | 12901-sdk-final | Body survives PRIVATE as `_audit__svc`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `backfill_v25` | b2-sdk-retirement-wave-1 | One-shot ONTOLOGY v2.5 migration (status/pointKind backfill); zero callers. Beta w8_backfill region: dead code carrying a public promise. No recorded destination. | `tortoise/sdk.py` @ `32011c4dad4b` |
| `belief_timeline` | 12901-sdk-final | Body survives PRIVATE as `_belief_timeline`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `calibrate_summary` | 12901-sdk-final | Body survives PRIVATE as `_calibrate_summary`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `calibration_passed` | b2-sdk-retirement-wave-1 | Gate B calibration-milestone reader. It had ONE production caller - `tortoise/gates.py`'s CLI read `TortoiseSDK().calibration_passed()` inside a try/except - so the body went PRIVATE as `_calibration_marker_recorded()` and gates.py reads that: the public door closed and the capability stayed. Beta `r3` records `check_confidence`. `_CALIBRATION_MARKER_KEY` stays: projection config-reset reads it. | `tortoise/sdk.py` @ `32011c4dad4b` |
| `check_structure` | 12901-sdk-final | Body survives PRIVATE as `_check_structure`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `checkpoint` | 11609-phase2-sources-containers | name retired into mine_knowledge_from_session(items=...) (#4282 Phase 2, #11609): row 16 keeps this batch filer as its items arm, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `compute_confidence` | 12901-sdk-final | Body survives PRIVATE as `_compute_confidence`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `compute_reputation` | 12901-sdk-final | Body survives PRIVATE as `_compute_reputation`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_derivation` | 12901-sdk-final | Body survives PRIVATE as `_create_derivation`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_direct_edge` | 12901-sdk-final | Body survives PRIVATE as `_create_direct_edge`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_document` | 12901-sdk-final | Body survives PRIVATE as `_create_document`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_edge` | 12901-sdk-final | Body survives PRIVATE as `_create_edge`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_event` | 12901-sdk-final | Body survives PRIVATE as `_create_event`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_object` | 12901-sdk-final | Body survives PRIVATE as `_create_object`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_operator` | 12901-sdk-final | Body survives PRIVATE as `_create_operator`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_point` | 12901-sdk-final | Body survives PRIVATE as `_create_point`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `create_source` | 11609-phase2-sources-containers | name retired into register_source (#4282 Phase 2, #11609): the approved row 14 keeps this body under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `create_subject` | 12901-sdk-final | Body survives PRIVATE as `_create_subject`; recorded destination `create_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `delete` | 12901-sdk-final | Body survives PRIVATE as `_delete`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `delete_entity` | 12901-sdk-final | Body survives PRIVATE as `_delete_entity__svc`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `delete_point` | 12901-sdk-final | Body survives PRIVATE as `_delete_point`; recorded destination `delete_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `delete_point_wrapped` | 12901-sdk-final | Body survives PRIVATE as `_delete_point_wrapped`; recorded destination `delete_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `diary_read` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `diary_write` | 12901-sdk-final | Body survives PRIVATE as `_diary_write`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `dream` | 12901-sdk-final | Body survives PRIVATE as `_dream`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `dream_health_check` | 12901-sdk-final | Body survives PRIVATE as `_dream_health_check`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `dream_health_state` | 12901-sdk-final | Body survives PRIVATE as `_dream_health_state`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `expand_relationships` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `get_confidence` | 12901-sdk-final | Body survives PRIVATE as `_get_confidence`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_cross_lens_candidates` | 12901-sdk-final | Body survives PRIVATE as `_get_cross_lens_candidates`; recorded destination `review_link_candidates` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_events` | 12901-sdk-final | Body survives PRIVATE as `_get_events`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_owned_entities` | 12901-sdk-final | Body survives PRIVATE as `_get_owned_entities`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_point` | 12901-sdk-final | Body survives PRIVATE as `_get_point`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_session` | 12901-sdk-final | Body survives PRIVATE as `_get_session`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `get_source_reliability` | 12901-sdk-final | Body survives PRIVATE as `_get_source_reliability`; recorded destination `manage_source_trust` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `graph_active_key_count` | 12901-wave-2 | Non-revoked key count per graph. Body survives PRIVATE as `_graph_active_key_count`; recorded destination `list_keys`. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `graph_delete` | 12901-wave-2 | Soft-delete of a Graph node (tombstone). Body survives PRIVATE as `_graph_delete`; recorded destination `delete_memory_graph` (rows 30-33 range). | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `graph_key_ids` | 12901-wave-2 | APIKey ids bound to a graph (delete cascade). Body survives PRIVATE as `_graph_key_ids`; recorded destination `list_keys`. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `index_directory` | 11609-phase2-sources-containers | name retired into index_sources_from_directory (#4282 Phase 2, #11609): row 15 keeps this walker under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `index_sessions` | 11609-phase2-sources-containers | name retired into index_sources_from_directory(file_type='agent_session') (#4282 Phase 2, #11609); it was a single delegation, frozen as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `ingest` | 12901-sdk-final | Body survives PRIVATE as `_ingest`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `ingest_corpus` | 12901-sdk-final | Body survives PRIVATE as `_ingest_corpus`; recorded destination `index_sources_from_directory` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `invalidate_point` | 12901-sdk-final | Body survives PRIVATE as `_invalidate_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `invitation_get_by_id` | 12901-wave-2 | Read one invitation row. Body survives PRIVATE as `_invitation_get_by_id`; beta: the invite UX belongs to the console; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `invitation_list` | 12901-wave-2 | List an org's pending invitations. Body survives PRIVATE as `_invitation_list`; beta: the invite UX belongs to the console; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `invitation_revoke` | 12901-wave-2 | Revoke a pending invitation. Body survives PRIVATE as `_invitation_revoke`; beta: the invite UX belongs to the console; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `issue_insight` | 12901-sdk-final | Body survives PRIVATE as `_issue_insight`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `link_source_to_entity` | 12901-sdk-final | Body survives PRIVATE as `_link_source_to_entity`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `list_batch` | 12901-sdk-final | Body survives PRIVATE as `_list_batch`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `list_batches` | 12901-sdk-final | Body survives PRIVATE as `_list_batches`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `list_dedup_candidates` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_graphs` | 12901-sdk-final | Body survives PRIVATE as `_list_graphs`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `list_namespaces` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_pointkinds` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_relations` | b2-sdk-retirement-wave-1 | Listed pack-declared relation declarations; zero callers. CANONICAL r6_canon (row R6) records graph_overview as destination (no def today, so no hint); the beta tables never recorded a target for this name. Registry call _get_kind_expander().list_relations() survives. | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_sources` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_tags` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `list_topics` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `membership_create` | 12901-wave-2 | Add a user to an org with a role. Body survives PRIVATE as `_membership_create`; recorded destination `add_member` (row 37). | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `membership_get` | b2-sdk-retirement-wave-1 | Read one membership row by id. No PRODUCTION caller: the only call sites were two tests (test_membership_get_returns_membership / _returns_none_for_missing), DELETED with it because the empty branch they exercised went too. Beta n1_console: console plumbing, tenancy admin is not on the SDK. No recorded destination. | `tortoise/sdk.py` @ `32011c4dad4b` |
| `mine_corpus` | 11609-phase2-sources-containers | name retired into mine_knowledge_from_directory (#4282 Phase 2, #11609): row 17 keeps this miner under its own name, frozen here as the record of what the retired name did | `tortoise/sdk.py` @ `32011c4dad4b` |
| `mitigate_operator` | 12901-sdk-final | Body survives PRIVATE as `_mitigate_operator`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `org_create` | 12901-sdk-final | Body survives PRIVATE as `_org_create`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `org_get` | 12901-wave-2 | Control-plane org read. Body survives PRIVATE as `_org_get`; recorded destination `get_organisation_account` (beta row 27). No hint: beta never names org_get. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `paginated_query` | 12901-sdk-final | Body survives PRIVATE as `_paginated_query`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `promote_point` | 12901-sdk-final | Body survives PRIVATE as `_promote_point`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `provenance` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `quarantine_batch` | b2-sdk-retirement-wave-1 | Thin SDK delegate to `tortoise.mining.quarantine_batch` (which stays). No PRODUCTION caller: the only call sites were tests, re-pointed at tortoise.mining.quarantine_batch, which is exactly what the delegate called. Beta `w11_lifecycle` records `update_knowledge` as destination. | `tortoise/sdk.py` @ `32011c4dad4b` |
| `query` | 12901-sdk-final | Body survives PRIVATE as `_query`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `query_points_by_tag` | 12901-sdk-final | Body survives PRIVATE as `_query_points_by_tag`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `recall_gaps` | 12901-sdk-final | Body survives PRIVATE as `_recall_gaps`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `recall_state` | 12901-sdk-final | Body survives PRIVATE as `_recall_state`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `recall_subgraph` | 12901-sdk-final | Body survives PRIVATE as `_recall_subgraph`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `reconcile_sessions` | 12901-sdk-final | Body survives PRIVATE as `_reconcile_sessions`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `resolve_id` | 12901-sdk-final | Body survives PRIVATE as `_resolve_id`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `retract_point` | 12901-sdk-final | Body survives PRIVATE as `_retract_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `review_connections` | 12901-sdk-final | Body survives PRIVATE as `_review_connections`; recorded destination `review_link_candidates` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `search_sessions` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `session_context` | 12901-sdk-final | Body survives PRIVATE as `_session_context`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `session_index_health` | 12901-sdk-final | Body survives PRIVATE as `_session_index_health`; recorded destination `index_sources_from_directory` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `set_point_baseline` | 12901-sdk-final | Body survives PRIVATE as `_set_point_baseline`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `signup_token_lookup` | 12901-wave-2 | Resolve an agent self-signup token. Body survives PRIVATE as `_signup_token_lookup`; beta: operator-side provisioning, not product surface; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `signup_token_recover` | 12901-wave-2 | Re-mint the bootstrap key for a signup token. Body survives PRIVATE as `_signup_token_recover`; beta: operator-side provisioning; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `signup_token_revoke` | 12901-wave-2 | Revoke a signup token. Body survives PRIVATE as `_signup_token_revoke`; beta: operator-side provisioning; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `stale_points` | 12901-sdk-final | Body survives PRIVATE as `_stale_points`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `status` | 11609-a-read-containers | retired into graph_overview(section='status') (#11609 Phase 2, #4282 row 9): the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `suggest_entry_points` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `summarize_structure` | 12901-sdk-final | Body survives PRIVATE as `_summarize_structure`; recorded destination `graph_overview` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `supersede_point` | 12901-sdk-final | Body survives PRIVATE as `_supersede_point`; recorded destination `supersede_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `sweep_invite_ghost_memberships` | 12901-wave-2 | Scheduled repair of membership rows whose invite never completed. Body survives PRIVATE as `_sweep_invite_ghost_memberships`; beta rules it OUR MAINTENANCE; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `taxonomy` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `test_guard` | 12901-sdk-final | Body survives PRIVATE as `_test_guard`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `topic_summarize` | 12901-sdk-final | Body survives PRIVATE as `_topic_summarize`; recorded destination `search_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `tortoise_fts_query` | 12901-sdk-final | Body survives PRIVATE as `_tortoise_fts_query`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `trash_graphs` | 12901-wave-2 | Tombstoned custom graphs (the trash view). Body survives PRIVATE as `_trash_graphs`; beta rules it OUR MAINTENANCE, never product surface; no destination. | `tortoise/sdk.py` @ `d741bd4a5a8d` |
| `ulid` | 12901-sdk-final | Body survives PRIVATE as `_ulid`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `update` | 12901-sdk-final | Body survives PRIVATE as `_update`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `update_point` | 12901-sdk-final | Body survives PRIVATE as `_update_point`; recorded destination `update_knowledge` (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |
| `validate_domain` | 11609-a-read-containers | retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container | `tortoise/sdk.py` @ `32011c4dad4b` |
| `volunteer_context` | 12901-sdk-final | Body survives PRIVATE as `_volunteer_context`; a departure with no replacement to name (#12901 final batch, #4282 Phase 2). | `tortoise/sdk.py` @ `1231060fd2be` |

## Modules (1)

| archived module | batch | reason | archived from |
|---|---|---|---|
| `tortoise.archived.search_legacy` | pre-mechanism-legacy-search | pre-mechanism archive: shipped importable inside the package; moved out of the product (#11609 batch 0) | `tortoise/archived/search_legacy.py` @ `4abff72cbb65` |
