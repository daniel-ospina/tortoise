"""The DECLARED public SDK surface — the owner-approved target (#4282 phase 1.1).

This module is the declaration the rest of the surface machinery is cut from. It is
**hand-maintained on purpose**: the approved surface is an owner ruling, and a ruling is
not derivable from code. `tools/sdk_surface_declaration.py` is the gate that holds it.

WHY A MODULE AND NOT THE PACKAGE `__all__`
    #4282 phase 0.4 asked for "`__all__` in `tortoise/__init__.py`". The package's
    `__all__` cannot hold these names: they are **class members** of `TortoiseSDK`, not
    module-level bindings of the `tortoise` package (see
    `docs/product/sdk-surface-declaration.md`, which reconciles the *live* set). This
    module is the other declaration `tortoise/__all__` can be — a data module that states
    the approved set, imported by the tools, not re-exported by the package.

TWO SETS, AND WHY THE SECOND ONE EXISTS
    `__all__` is the approved surface: **40 names**, transcribed verbatim from
    `docs/product/beta-sdk-surface.md` rows 1-40 (approved 2026-09-21, `daniel-ospina`;
    approval record in `docs/product/canonical-mcp-tools.md`, PR 4120). It is the owner's
    ruling, not a re-derivation. Do not add to it, and do not re-litigate it — a change to
    it is a change to the contract every agent and customer integration is built on, and it
    needs Daniel's approval FIRST (`AGENTS.md` -> "MCP/SDK Surface Approval").

    `OFF_SURFACE` is the **frozen record** of what existed off-target at the cut: every
    public method that was live on `TortoiseSDK` and is *not* on the approved surface. It
    exists so that an ADDITION or a RENAME is distinguishable from a method that was
    already here and is on its way out. Without it, "there is a method I did not approve"
    and "there is a method the migration has not reached yet" are the same observation, and
    the surface can grow freely while the migration runs — which is exactly how 149 got
    here. Membership here is **not** an approval and **not** a removal decision: it is a
    record of what was there.

    A RECORD, DELIBERATELY NOT A MIRROR. It is not edited when a method is removed — the
    gate accounts for the current surface as `OFF_SURFACE ∩ live`, so the migration
    direction needs no declaration edit, and a name that leaves the class stays here as the
    evidence that it was on the way out. The alternative (`OFF_SURFACE == live − approved`)
    looks tighter and is WEAKER: a SWAP — one name out, one unapproved name in, same
    length — satisfies it, so a rename was invisible. The high-water pin keeps the record
    from absorbing a new method.

THE MISREADING THIS FILE FORECLOSES
    A 40-name declaration alone would have to say "over target" forever and could not
    distinguish growth from backlog. The three blocks below are checkable against the class
    and against each other, so the gate can NAME the method that appeared (`OFF_SURFACE`
    covers what was already here) and NAME the approved name that vanished
    (`APPROVED_PENDING` covers what was never here).
"""

#: The approved SDK surface — `docs/product/beta-sdk-surface.md` rows 1-40, in that order.
#: 40 names. The gate refuses any disagreement with that document, in either direction.
__all__ = (
    "Tortoise",
    "close",
    "search_knowledge",
    "list_knowledge",
    "check_confidence",
    "get_entity",
    "get_historical_knowledge",
    "explore_connections",
    "graph_overview",
    "review_link_candidates",
    "poll_events",
    "create_entity",
    "write_knowledge_batch",
    "register_source",
    "index_sources_from_directory",
    "mine_knowledge_from_session",
    "mine_knowledge_from_directory",
    "manage_source_trust",
    "link_entities",
    "write_question",
    "record_decision",
    "update_knowledge",
    "supersede_knowledge",
    "delete_knowledge",
    "adjust_relationship",
    "refresh_confidence",
    "approve_merge",
    "get_organisation_account",
    "create_memory_graph",
    "update_memory_graph",
    "list_memory_graphs",
    "delete_memory_graph",
    "restore_memory_graph",
    "create_key",
    "list_keys",
    "revoke_key",
    "add_member",
    "list_members",
    "remove_member",
    "check_connection",
)

#: Approved names with NO `def` on `TortoiseSDK` under the canonical name yet — the tracked
#: gaps a later phase of #4282 fills. Derived from reality at the cut, then frozen: the gate
#: refuses a name here that has since been implemented (move it OUT of this block in the same
#: PR) and refuses a declared name that is neither here nor on the class. May only SHRINK —
#: deleting an approved method means adding it here, which the high-water pin refuses.
APPROVED_PENDING = (
    "search_knowledge",
    "list_knowledge",
    "check_confidence",
    "explore_connections",
    "graph_overview",
    "review_link_candidates",
    "write_knowledge_batch",
    "register_source",
    "index_sources_from_directory",
    "mine_knowledge_from_session",
    "mine_knowledge_from_directory",
    "manage_source_trust",
    "link_entities",
    "write_question",
    "record_decision",
    "update_knowledge",
    "supersede_knowledge",
    "delete_knowledge",
    "adjust_relationship",
    "refresh_confidence",
    "get_organisation_account",
    "create_memory_graph",
    "delete_memory_graph",
    "create_key",
    "list_keys",
    "revoke_key",
    "add_member",
    "list_members",
    "remove_member",
    "check_connection",
)

#: The frozen record of what existed OFF-target at the cut — public methods that were live
#: on `TortoiseSDK` and are NOT on the approved surface (see the module docstring). NOT
#: edited on a removal: the gate accounts for today's surface as `OFF_SURFACE & live`, and
#: the pin below keeps the record from absorbing an addition.
OFF_SURFACE = (
    "annotate_ask_hits",
    "annotate_operator",
    "apikey_create",
    "apikey_list",
    "apikey_revoke",
    "apikey_verify",
    "assess_source",
    "audit",
    "backfill_about_entities",
    "backfill_sources",
    "backfill_v25",
    "batch_create_points",
    "belief_timeline",
    "calibrate_summary",
    "calibration_passed",
    "capture_session",
    "check_structure",
    "checkpoint",
    "cleanup_expired_invitations",
    "commit_session",
    "complete_source",
    "compute_confidence",
    "compute_reputation",
    "create_derivation",
    "create_direct_edge",
    "create_document",
    "create_edge",
    "create_event",
    "create_object",
    "create_operator",
    "create_or_update_point",
    "create_point",
    "create_source",
    "create_subject",
    "delete",
    "delete_entity",
    "delete_point",
    "delete_point_wrapped",
    "diary_read",
    "diary_write",
    "dream",
    "dream_health_check",
    "dream_health_state",
    "expand_relationships",
    "file_decision",
    "file_human_approval",
    "get_confidence",
    "get_cross_lens_candidates",
    "get_events",
    "get_org_structure",
    "get_owned_entities",
    "get_point",
    "get_provenance_chain",
    "get_session",
    "get_source_reliability",
    "graph_active_key_count",
    "graph_count",
    "graph_delete",
    "graph_key_ids",
    "index_directory",
    "index_file",
    "index_sessions",
    "ingest",
    "ingest_corpus",
    "invalidate_point",
    "invitation_accept",
    "invitation_create",
    "invitation_get_by_id",
    "invitation_get_by_token",
    "invitation_list",
    "invitation_revoke",
    "issue_insight",
    "link_source_to_entity",
    "list_batch",
    "list_batches",
    "list_dedup_candidates",
    "list_drafts",
    "list_graphs",
    "list_namespaces",
    "list_pointkinds",
    "list_relations",
    "list_sources",
    "list_tags",
    "list_topics",
    "membership_create",
    "membership_delete",
    "membership_get",
    "membership_list",
    "membership_update_role",
    "migrate_orgs_to_registry",
    "mine_corpus",
    "mitigate_operator",
    "operator_action",
    "org_create",
    "org_delete",
    "org_get",
    "org_list",
    "org_update",
    "paginated_query",
    "promote_point",
    "provenance",
    "quarantine_batch",
    "query",
    "query_points_by_tag",
    "recall_gaps",
    "recall_state",
    "recall_subgraph",
    "reconcile_sessions",
    "record_calibration",
    "resolve_id",
    "retract_point",
    "retrieval_legs",
    "review_connections",
    "search_sessions",
    "session_context",
    "session_index_health",
    "set_point_baseline",
    "set_source_tier",
    "signup_token_lookup",
    "signup_token_recover",
    "signup_token_revoke",
    "stale_points",
    "status",
    "suggest_entry_points",
    "summarize_structure",
    "supersede",
    "supersede_point",
    "sweep_invite_ghost_memberships",
    "taxonomy",
    "test_guard",
    "topic_summarize",
    "tortoise_fts_query",
    "trash_graphs",
    "traverse",
    "ulid",
    "update",
    "update_entity",
    "update_point",
    "validate_domain",
    "volunteer_context",
)

#: HIGH-WATER PINS, NOT DERIVATIONS. `APPROVED_PENDING` may only shrink, so deleting an
#: approved method (which must land there) trips its pin. `OFF_SURFACE` is a frozen record,
#: so its pin stops it ABSORBING an addition — the one way a new method could otherwise pass.
#: Raising either is a deliberate, reviewable act in an approving PR, never a side effect of
#: a re-cut.
PENDING_HIGH_WATER = 30
OFF_SURFACE_HIGH_WATER = 140
