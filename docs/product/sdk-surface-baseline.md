# SDK surface baseline — what exists today, NOT the target

**GENERATED** by `tools/sdk_surface_declaration.py baseline` (tortoise #4282 Phase
0.4/1.1). Never edit by hand — `baseline --check` reds if this file is stale.

> ⚠️ **This is a baseline, not an approval, and not a second declaration.** It records
> the public SDK surface as it exists today so the gap to the owner's target can be
> tracked while #4282 Phase 2 removes it. The **approved target is
> `tortoise/__all__.py`** — 40 methods, owner ruling 2026-09-21
> (`docs/product/beta-sdk-surface.md`, approval record in
> `docs/product/canonical-mcp-tools.md`). Nothing is approved by being listed here.
> **This list may only SHRINK.**

| measured | count |
|---|---|
| approved methods (the target) | 40 |
| live public methods on `TortoiseSDK` | 94 |
| approved and implemented (row 1 `Tortoise(...)` resolves to the `TortoiseSDK` class) | 29 |
| approved but not implemented yet | 11 |
| live and NOT approved (migration backlog) | 66 |

Row 1 of the owner's table is `Tortoise(...)` — the class a caller constructs. It resolves
to `TortoiseSDK` (the only constructible entry point); it is not a method on it.

`docs/product/beta-sdk-surface.md` states "only four of the 40 exist in the current SDK".
That sentence is STALE: 28 exist, plus the constructor. It is recorded here rather than
edited there, because the approved document is the owner's ruling and this baseline is not
the place to amend it (#10044).

## Approved but not implemented yet

The owner approved these; no `def` exists on `TortoiseSDK` under the canonical name.
Phase 2 implements them.

- `add_member`
- `adjust_relationship`
- `create_key`
- `create_memory_graph`
- `delete_memory_graph`
- `list_keys`
- `list_members`
- `refresh_confidence`
- `remove_member`
- `revoke_key`
- `write_knowledge_batch`

## Live and NOT approved — the migration backlog

Public on `TortoiseSDK` today, absent from the approved 40. Phase 2 removes them;
the count above is the honest measure of the gap.

- `annotate_ask_hits`
- `annotate_operator`
- `audit`
- `belief_timeline`
- `calibrate_summary`
- `check_structure`
- `compute_confidence`
- `compute_reputation`
- `create_derivation`
- `create_direct_edge`
- `create_document`
- `create_edge`
- `create_event`
- `create_object`
- `create_operator`
- `create_point`
- `create_subject`
- `delete`
- `delete_entity`
- `delete_point`
- `delete_point_wrapped`
- `diary_write`
- `dream`
- `dream_health_check`
- `dream_health_state`
- `get_confidence`
- `get_cross_lens_candidates`
- `get_events`
- `get_owned_entities`
- `get_point`
- `get_session`
- `get_source_reliability`
- `ingest`
- `ingest_corpus`
- `invalidate_point`
- `issue_insight`
- `link_source_to_entity`
- `list_batch`
- `list_batches`
- `list_graphs`
- `mitigate_operator`
- `org_create`
- `paginated_query`
- `promote_point`
- `query`
- `query_points_by_tag`
- `recall_gaps`
- `recall_state`
- `recall_subgraph`
- `reconcile_sessions`
- `resolve_id`
- `retract_point`
- `review_connections`
- `session_context`
- `session_index_health`
- `set_point_baseline`
- `stale_points`
- `summarize_structure`
- `supersede_point`
- `test_guard`
- `topic_summarize`
- `tortoise_fts_query`
- `ulid`
- `update`
- `update_point`
- `volunteer_context`
