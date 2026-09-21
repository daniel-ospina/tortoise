# Phase 0.3b — the SDK rename table

**GENERATED — do not edit.** `uv run python tools/sdk_rename_table.py`; verify with `--check`.

Every `sdk.py:N` citation is **read from the AST at build time**, so it cannot drift from the code it cites. The 40 target names are **parsed out of `docs/product/beta-sdk-surface.md`** (owner-approved 2026-09-21), and the R/W/N group partition out of `docs/product/canonical-sdk-methods.md`; every count below is arithmetic over those, never a typed number. Each row's citation quote is **verified to still be in the doc it names** — a citation that no longer resolves fails the build.

**This is the SDK half of the rename table.** The MCP half (current tool → target tool) is `docs/product/bridge-table.md` (Phase 0.1), plus a sibling Phase 0.3b lane; nothing here restates it.

**The surface: 150 public methods on `TortoiseSDK` → 40 target methods.** 110 of the 150 are renames to a target; **4** are already targets (unchanged); **33** are discarded with a rationale; and **3** have no destination anywhere on the target surface — those are findings, not rows to be guessed at.

**These are not the same number.** The target has 40 methods; only **4** of them exist on `TortoiseSDK` today. The other **36** are Phase 2 work, listed in Part C1.

---

## Part A — every public method and its migration row

`Basis` says how strongly the row is backed: **stated** — the cited quote names this method and gives its collapse, rename or deletion; **derived** — a doc gives the destination only for a namespace or wildcard covering this method, without naming it; **unbacked** — no doc gives a destination.

The canonical inventory's group names are an **earlier sketch** (`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). The `Target` column always carries the **beta** target name (`update_knowledge`, `refresh_confidence`, `write_knowledge_batch`, `index_sources_from_directory`) — the canonical doc itself says beta governs where the two disagree, and records the renames.

| # | Method | Source | Group | Target | Basis | Citation |
|---|---|---|---|---|---|---|
| 1 | `annotate_ask_hits` | `sdk.py:13289` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 2 | `annotate_operator` | `sdk.py:6766` | W15 | `adjust_relationship` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship`” |
| 3 | `apikey_create` | `sdk.py:16201` | N4 | `create_key` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph.” |
| 4 | `apikey_list` | `sdk.py:16305` | N4 | `list_keys` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph.” |
| 5 | `apikey_revoke` | `sdk.py:16330` | N4 | `revoke_key` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph.” |
| 6 | `apikey_verify` | `sdk.py:16351` | N4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing.” |
| 7 | `approve_merge` | `sdk.py:6443` | W14 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)” |
| 8 | `assess_source` | `sdk.py:20036` | W8 | `manage_source_trust` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust`” |
| 9 | `audit` | `sdk.py:7130` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview`” |
| 10 | `backfill_about_entities` | `sdk.py:9570` | W4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations.” |
| 11 | `backfill_sources` | `sdk.py:19038` | W8 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations.” |
| 12 | `backfill_v25` | `sdk.py:20371` | ARCHIVE | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations.” |
| 13 | `batch_create_points` | `sdk.py:7342` | W1 | `write_knowledge_batch` | stated | `beta-sdk-surface.md` — “\| `batch_create_points` \| 1 \| → `write_knowledge_batch`. \|” |
| 14 | `belief_timeline` | `sdk.py:6187` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence`” |
| 15 | `calibrate_summary` | `sdk.py:11913` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence`” |
| 16 | `calibration_passed` | `sdk.py:20495` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence`” |
| 17 | `capture_session` | `sdk.py:3200` | W6 | `mine_knowledge_from_session` | stated | `beta-sdk-surface.md` — “\| `capture_session` / `commit_session` \| → row 16 `mine_knowledge_from_session`, one method.” |
| 18 | `check_structure` | `sdk.py:7096` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 19 | `checkpoint` | `sdk.py:12214` | W5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.” |
| 20 | `cleanup_expired_invitations` | `sdk.py:16764` | N5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.**” |
| 21 | `close` | `sdk.py:9525` | W17 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)” |
| 22 | `commit_session` | `sdk.py:3037` | W7 | `mine_knowledge_from_session` | stated | `beta-sdk-surface.md` — “\| `capture_session` / `commit_session` \| → row 16 `mine_knowledge_from_session`, one method.” |
| 23 | `complete_source` | `sdk.py:20350` | W3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `complete_source` \| 1 \| **Cut.**” |
| 24 | `compute_confidence` | `sdk.py:11455` | W13 | `refresh_confidence` | stated | `canonical-sdk-methods.md` — “\| W13 \| `stabilize_beliefs` \| #19 \| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \|” |
| 25 | `compute_reputation` | `sdk.py:20160` | W13 | `UNBACKED` | unbacked | **no doc states a destination** |
| 26 | `create_derivation` | `sdk.py:19852` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`” |
| 27 | `create_direct_edge` | `sdk.py:8984` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`” |
| 28 | `create_document` | `sdk.py:19686` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`.” |
| 29 | `create_edge` | `sdk.py:20252` | W9 | `link_entities` | stated | `canonical-sdk-methods.md` — “\| W9 \| `link_entities` \| #15 \| `create_edge`,” |
| 30 | `create_entity` | `sdk.py:17303` | W1 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)” |
| 31 | `create_event` | `sdk.py:17486` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`.” |
| 32 | `create_object` | `sdk.py:17477` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`.” |
| 33 | `create_operator` | `sdk.py:6585` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`” |
| 34 | `create_or_update_point` | `sdk.py:2984` | W1 | `create_entity` | stated | `canonical-sdk-methods.md` — “\| `create_or_update_point` → `create_point` \|” |
| 35 | `create_point` | `sdk.py:2503` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`.” |
| 36 | `create_source` | `sdk.py:19698` | W3 | `register_source` | stated | `canonical-sdk-methods.md` — “\| W3 \| `register_source` \| #11 \| `create_source`, `complete_source` \|” |
| 37 | `create_subject` | `sdk.py:17468` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`.” |
| 38 | `delete` | `sdk.py:4965` | W12 | `delete_knowledge` | stated | `canonical-sdk-methods.md` — “\| W12 \| `delete_knowledge` \| #18 \| `delete`,” |
| 39 | `delete_entity` | `sdk.py:20245` | W12 | `delete_knowledge` | stated | `canonical-sdk-methods.md` — “\| W12 \| `delete_knowledge` \| #18 \| `delete`,” |
| 40 | `delete_point` | `sdk.py:5097` | W12 | `delete_knowledge` | stated | `beta-sdk-surface.md` — “\| `delete_point`, `delete_point_wrapped` \| 2 \| → `delete_knowledge`. \|” |
| 41 | `delete_point_wrapped` | `sdk.py:5150` | W12 | `delete_knowledge` | stated | `beta-sdk-surface.md` — “\| `delete_point`, `delete_point_wrapped` \| 2 \| → `delete_knowledge`. \|” |
| 42 | `diary_read` | `sdk.py:12390` | W5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.” |
| 43 | `diary_write` | `sdk.py:12378` | W5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.” |
| 44 | `dream` | `sdk.py:10876` | W13 | `refresh_confidence` | stated | `canonical-sdk-methods.md` — “\| W13 \| `stabilize_beliefs` \| #19 \| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \|” |
| 45 | `dream_health_check` | `sdk.py:10461` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview`” |
| 46 | `dream_health_state` | `sdk.py:10610` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview`” |
| 47 | `events_poll` | `sdk.py:2313` | R8 | `poll_events` | stated | `beta-sdk-surface.md` — “\| `events_poll` \| → row 11 `poll_events`. \|” |
| 48 | `expand_relationships` | `sdk.py:14580` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 49 | `file_decision` | `sdk.py:9267` | W10 | `write_question` | stated | `beta-sdk-surface.md` — “\| `file_decision` \| → rows 20/21 **`write_question`** + **`record_decision`**.” |
| 50 | `file_human_approval` | `sdk.py:9323` | W10 | `record_decision` | stated | `beta-sdk-surface.md` — “\| `file_human_approval` \| 1 \| → `record_decision`. \|” |
| 51 | `get_confidence` | `sdk.py:11692` | R3 | `check_confidence` | stated | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`” |
| 52 | `get_cross_lens_candidates` | `sdk.py:9765` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 53 | `get_entity` | `sdk.py:20236` | R4 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)” |
| 54 | `get_events` | `sdk.py:17676` | R4 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`” |
| 55 | `get_org_structure` | `sdk.py:20324` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 56 | `get_owned_entities` | `sdk.py:20281` | R5 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`” |
| 57 | `get_point` | `sdk.py:7054` | R4 | `get_entity` | stated | `canonical-sdk-methods.md` — “\| R4 \| `get_entity` \| #4 \| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \|” |
| 58 | `get_provenance_chain` | `sdk.py:20295` | R3 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`” |
| 59 | `get_session` | `sdk.py:17689` | R4 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`” |
| 60 | `get_source_reliability` | `sdk.py:19932` | W8 | `manage_source_trust` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust`” |
| 61 | `graph_active_key_count` | `sdk.py:15846` | N2 | `list_keys` | stated | `beta-sdk-surface.md` — “\| `graph_key_ids`, `graph_active_key_count` \| 2 \| Console diagnostics. Both fold into `list_keys`. \|” |
| 62 | `graph_count` | `sdk.py:15690` | N2 | `list_memory_graphs` | derived | `beta-sdk-surface.md` — “`list_memory_graphs` answers "how many" for any real N.” |
| 63 | `graph_delete` | `sdk.py:15729` | N2 | `delete_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 64 | `graph_key_ids` | `sdk.py:15834` | N2 | `list_keys` | stated | `beta-sdk-surface.md` — “\| `graph_key_ids`, `graph_active_key_count` \| 2 \| Console diagnostics. Both fold into `list_keys`. \|” |
| 65 | `graph_list` | `sdk.py:15650` | N2 | `list_memory_graphs` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 66 | `graph_restore` | `sdk.py:15757` | N2 | `restore_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 67 | `graph_set_name` | `sdk.py:15869` | N2 | `update_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 68 | `graph_set_recording` | `sdk.py:15798` | N2 | `update_memory_graph` | stated | `beta-sdk-surface.md` — “The override therefore folds into **`update_memory_graph`**” |
| 69 | `index_directory` | `sdk.py:17779` | W4 | `index_sources_from_directory` | stated | `canonical-sdk-methods.md` — “\| W4 \| `index_files` \| #12 \| `index_file`, `index_directory`” |
| 70 | `index_file` | `sdk.py:17701` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 71 | `index_sessions` | `sdk.py:19505` | W4 | `index_sources_from_directory` | stated | `canonical-sdk-methods.md` — “\| `index_sessions` / `ingest_corpus` → `index_directory` \|” |
| 72 | `ingest` | `sdk.py:8209` | W2 | `write_knowledge_batch` | stated | `canonical-sdk-methods.md` — “\| W2 \| `write_knowledge` \| — \| `ingest` \|” |
| 73 | `ingest_corpus` | `sdk.py:12429` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 74 | `invalidate_point` | `sdk.py:5206` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `retract_point`, `invalidate_point` \| 2 \| → fields on `update_knowledge`.” |
| 75 | `invitation_accept` | `sdk.py:16694` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 76 | `invitation_create` | `sdk.py:16613` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 77 | `invitation_get_by_id` | `sdk.py:16738` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 78 | `invitation_get_by_token` | `sdk.py:16689` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 79 | `invitation_list` | `sdk.py:16671` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 80 | `invitation_revoke` | `sdk.py:16747` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 81 | `issue_insight` | `sdk.py:13046` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 82 | `link_source_to_entity` | `sdk.py:20305` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`” |
| 83 | `list_batch` | `sdk.py:8864` | R9 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `list_batch`, `list_batches` \| 2 \| → `list_knowledge(kind='batch')`.” |
| 84 | `list_batches` | `sdk.py:8933` | R9 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `list_batch`, `list_batches` \| 2 \| → `list_knowledge(kind='batch')`.” |
| 85 | `list_dedup_candidates` | `sdk.py:6374` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 86 | `list_drafts` | `sdk.py:6535` | W11 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling” |
| 87 | `list_graphs` | `sdk.py:9461` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 88 | `list_namespaces` | `sdk.py:7288` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 89 | `list_pointkinds` | `sdk.py:7222` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 90 | `list_relations` | `sdk.py:9478` | R6 | `graph_overview` | stated | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`” |
| 91 | `list_sources` | `sdk.py:7247` | R6 | `list_knowledge` | stated | `beta-sdk-surface.md` — “It folds into **row 4 `list_knowledge(kind='source')`**” |
| 92 | `list_tags` | `sdk.py:7261` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 93 | `list_topics` | `sdk.py:7301` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 94 | `membership_create` | `sdk.py:16053` | N3 | `add_member` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \|” |
| 95 | `membership_delete` | `sdk.py:16143` | N3 | `remove_member` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \|” |
| 96 | `membership_get` | `sdk.py:16105` | N3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing.” |
| 97 | `membership_list` | `sdk.py:16114` | N3 | `list_members` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \|” |
| 98 | `membership_update_role` | `sdk.py:16123` | N3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing.” |
| 99 | `migrate_orgs_to_registry` | `sdk.py:16014` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.**” |
| 100 | `mine_corpus` | `sdk.py:12860` | W4 | `mine_knowledge_from_directory` | stated | `beta-sdk-surface.md` — “\| `mine_corpus` \| 1 \| → `mine_knowledge_from_directory`.” |
| 101 | `mitigate_operator` | `sdk.py:6806` | W15 | `adjust_relationship` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship`” |
| 102 | `operator_action` | `sdk.py:6743` | W15 | `adjust_relationship` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship`” |
| 103 | `org_create` | `sdk.py:15385` | N1 | `UNBACKED` | unbacked | **no doc states a destination** |
| 104 | `org_delete` | `sdk.py:15951` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing.” |
| 105 | `org_get` | `sdk.py:15904` | N1 | `get_organisation_account` | derived | `beta-sdk-surface.md` — “\| 28 \| `get_organisation_account` \| Read the account and the plan it is on \|” |
| 106 | `org_list` | `sdk.py:15913` | N1 | `get_organisation_account` | derived | `beta-sdk-surface.md` — “\| 28 \| `get_organisation_account` \| Read the account and the plan it is on \|” |
| 107 | `org_update` | `sdk.py:15921` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing.” |
| 108 | `paginated_query` | `sdk.py:6992` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 109 | `promote_point` | `sdk.py:5878` | W11 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling” |
| 110 | `provenance` | `sdk.py:9540` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence`” |
| 111 | `quarantine_batch` | `sdk.py:6175` | W11 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling” |
| 112 | `query` | `sdk.py:6939` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 113 | `query_points_by_tag` | `sdk.py:7277` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 114 | `recall_gaps` | `sdk.py:15184` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence`” |
| 115 | `recall_state` | `sdk.py:14759` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence`” |
| 116 | `recall_subgraph` | `sdk.py:15307` | R3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “**`recall_subgraph` is dropped, not folded**” |
| 117 | `reconcile_sessions` | `sdk.py:19606` | W4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations.” |
| 118 | `record_calibration` | `sdk.py:20410` | W13 | `UNBACKED` | unbacked | **no doc states a destination** |
| 119 | `resolve_id` | `sdk.py:2990` | R4 | `get_entity` | stated | `canonical-sdk-methods.md` — “\| R4 \| `get_entity` \| #4 \| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \|” |
| 120 | `restore_point_at` | `sdk.py:14618` | R3 | `get_historical_knowledge` | stated | `beta-sdk-surface.md` — “\| `restore_point_at` \| → row 7 **`get_historical_knowledge`**.” |
| 121 | `retract_point` | `sdk.py:5813` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `retract_point`, `invalidate_point` \| 2 \| → fields on `update_knowledge`.” |
| 122 | `retrieval_legs` | `sdk.py:14946` | R3 | `check_confidence` | stated | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`” |
| 123 | `review_connections` | `sdk.py:9639` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 124 | `search_sessions` | `sdk.py:17535` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 125 | `session_context` | `sdk.py:12984` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence`” |
| 126 | `session_index_health` | `sdk.py:19520` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 127 | `set_point_baseline` | `sdk.py:11625` | W11 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling” |
| 128 | `set_source_tier` | `sdk.py:19811` | W8 | `manage_source_trust` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust`” |
| 129 | `signup_token_lookup` | `sdk.py:16435` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup” |
| 130 | `signup_token_recover` | `sdk.py:16466` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup” |
| 131 | `signup_token_revoke` | `sdk.py:16546` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup” |
| 132 | `stale_points` | `sdk.py:9630` | R6 | `graph_overview` | derived | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 133 | `status` | `sdk.py:12410` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 134 | `suggest_entry_points` | `sdk.py:12887` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 135 | `summarize_structure` | `sdk.py:7148` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview`” |
| 136 | `supersede` | `sdk.py:5299` | W11 | `supersede_knowledge` | stated | `beta-sdk-surface.md` — “\| `supersede`, `supersede_point` \| 2 \| → `supersede_knowledge`.” |
| 137 | `supersede_point` | `sdk.py:5317` | W11 | `supersede_knowledge` | stated | `beta-sdk-surface.md` — “\| `supersede`, `supersede_point` \| 2 \| → `supersede_knowledge`.” |
| 138 | `sweep_invite_ghost_memberships` | `sdk.py:16810` | N5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.**” |
| 139 | `taxonomy` | `sdk.py:7217` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| 140 | `test_guard` | `sdk.py:2136` | R6 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `test_guard` \| **Kept and relocated.**” |
| 141 | `topic_summarize` | `sdk.py:7306` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 142 | `tortoise_fts_query` | `sdk.py:13442` | R1 | `search_knowledge` | stated | `canonical-sdk-methods.md` — “\| R1 \| `search_knowledge` \| #1 \| `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` \|” |
| 143 | `trash_graphs` | `sdk.py:15779` | N2 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.**” |
| 144 | `traverse` | `sdk.py:7068` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 145 | `ulid` | `sdk.py:20344` | W17 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `ulid` \| 1 \| A ULID generator. Not a memory operation. \|” |
| 146 | `update` | `sdk.py:4945` | W11 | `update_knowledge` | stated | `canonical-sdk-methods.md` — “\| W11 \| `revise_knowledge` \| #17 \| `update`,” |
| 147 | `update_entity` | `sdk.py:20239` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `update_point`, `update_entity` \| 2 \| → `update_knowledge`. \|” |
| 148 | `update_point` | `sdk.py:4981` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `update_point`, `update_entity` \| 2 \| → `update_knowledge`. \|” |
| 149 | `validate_domain` | `sdk.py:7114` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview`” |
| 150 | `volunteer_context` | `sdk.py:15016` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence`” |

## Part B — destination counts

| Destination | Current methods | Count |
|---|---|---|
| `DISCARDED` | `apikey_verify`, `backfill_about_entities`, `backfill_sources`, `backfill_v25`, `checkpoint`, `cleanup_expired_invitations`, `complete_source`, `diary_read`, `diary_write`, `invitation_accept`, `invitation_create`, `invitation_get_by_id`, `invitation_get_by_token`, `invitation_list`, `invitation_revoke`, `list_drafts`, `membership_get`, `membership_update_role`, `migrate_orgs_to_registry`, `org_delete`, `org_update`, `promote_point`, `quarantine_batch`, `recall_subgraph`, `reconcile_sessions`, `set_point_baseline`, `signup_token_lookup`, `signup_token_recover`, `signup_token_revoke`, `sweep_invite_ghost_memberships`, `test_guard`, `trash_graphs`, `ulid` | 33 |
| `graph_overview` | `audit`, `check_structure`, `dream_health_check`, `dream_health_state`, `list_graphs`, `list_namespaces`, `list_pointkinds`, `list_relations`, `list_tags`, `list_topics`, `stale_points`, `status`, `summarize_structure`, `taxonomy`, `validate_domain` | 15 |
| `check_confidence` | `belief_timeline`, `calibrate_summary`, `calibration_passed`, `get_confidence`, `provenance`, `recall_gaps`, `recall_state`, `retrieval_legs`, `session_context`, `volunteer_context` | 10 |
| `create_entity` | `create_document`, `create_event`, `create_object`, `create_or_update_point`, `create_point`, `create_subject` | 6 |
| `get_entity` | `get_events`, `get_owned_entities`, `get_point`, `get_provenance_chain`, `get_session`, `resolve_id` | 6 |
| `list_knowledge` | `list_batch`, `list_batches`, `list_sources`, `paginated_query`, `query`, `query_points_by_tag` | 6 |
| `search_knowledge` | `annotate_ask_hits`, `issue_insight`, `search_sessions`, `suggest_entry_points`, `topic_summarize`, `tortoise_fts_query` | 6 |
| `index_sources_from_directory` | `index_directory`, `index_file`, `index_sessions`, `ingest_corpus`, `session_index_health` | 5 |
| `link_entities` | `create_derivation`, `create_direct_edge`, `create_edge`, `create_operator`, `link_source_to_entity` | 5 |
| `update_knowledge` | `invalidate_point`, `retract_point`, `update`, `update_entity`, `update_point` | 5 |
| `UNCHANGED` | `approve_merge`, `close`, `create_entity`, `get_entity` | 4 |
| `delete_knowledge` | `delete`, `delete_entity`, `delete_point`, `delete_point_wrapped` | 4 |
| `UNBACKED` | `compute_reputation`, `org_create`, `record_calibration` | 3 |
| `adjust_relationship` | `annotate_operator`, `mitigate_operator`, `operator_action` | 3 |
| `explore_connections` | `expand_relationships`, `get_org_structure`, `traverse` | 3 |
| `list_keys` | `apikey_list`, `graph_active_key_count`, `graph_key_ids` | 3 |
| `manage_source_trust` | `assess_source`, `get_source_reliability`, `set_source_tier` | 3 |
| `review_link_candidates` | `get_cross_lens_candidates`, `list_dedup_candidates`, `review_connections` | 3 |
| `get_organisation_account` | `org_get`, `org_list` | 2 |
| `list_memory_graphs` | `graph_count`, `graph_list` | 2 |
| `mine_knowledge_from_session` | `capture_session`, `commit_session` | 2 |
| `refresh_confidence` | `compute_confidence`, `dream` | 2 |
| `supersede_knowledge` | `supersede`, `supersede_point` | 2 |
| `update_memory_graph` | `graph_set_name`, `graph_set_recording` | 2 |
| `write_knowledge_batch` | `batch_create_points`, `ingest` | 2 |
| `add_member` | `membership_create` | 1 |
| `create_key` | `apikey_create` | 1 |
| `delete_memory_graph` | `graph_delete` | 1 |
| `get_historical_knowledge` | `restore_point_at` | 1 |
| `list_members` | `membership_list` | 1 |
| `mine_knowledge_from_directory` | `mine_corpus` | 1 |
| `poll_events` | `events_poll` | 1 |
| `record_decision` | `file_human_approval` | 1 |
| `register_source` | `create_source` | 1 |
| `remove_member` | `membership_delete` | 1 |
| `restore_memory_graph` | `graph_restore` | 1 |
| `revoke_key` | `apikey_revoke` | 1 |
| `write_question` | `file_decision` | 1 |
| **total** | — | **150** |

Distinct destinations: **38** — **33** are target methods with no `def` today (Phase 2 work, Part C1), **2** are target methods that already exist (`create_entity`, `get_entity`), and **3** are the non-target dispositions (`UNCHANGED` / `DISCARDED` / `UNBACKED`).

## Part C — findings

### C1 — target methods with no `def` on `TortoiseSDK`

A rename whose destination does not exist yet is **Phase 2 work**, not a rename.
The plan listed the whole table as renames; this is what Part A exists to catch.

| Target method | Status |
|---|---|
| `Tortoise` | no `def` on `TortoiseSDK` today |
| `search_knowledge` | no `def` on `TortoiseSDK` today |
| `list_knowledge` | no `def` on `TortoiseSDK` today |
| `check_confidence` | no `def` on `TortoiseSDK` today |
| `get_historical_knowledge` | no `def` on `TortoiseSDK` today |
| `explore_connections` | no `def` on `TortoiseSDK` today |
| `graph_overview` | no `def` on `TortoiseSDK` today |
| `review_link_candidates` | no `def` on `TortoiseSDK` today |
| `poll_events` | no `def` on `TortoiseSDK` today |
| `write_knowledge_batch` | no `def` on `TortoiseSDK` today |
| `register_source` | no `def` on `TortoiseSDK` today |
| `index_sources_from_directory` | no `def` on `TortoiseSDK` today |
| `mine_knowledge_from_session` | no `def` on `TortoiseSDK` today |
| `mine_knowledge_from_directory` | no `def` on `TortoiseSDK` today |
| `manage_source_trust` | no `def` on `TortoiseSDK` today |
| `link_entities` | no `def` on `TortoiseSDK` today |
| `write_question` | no `def` on `TortoiseSDK` today |
| `record_decision` | no `def` on `TortoiseSDK` today |
| `update_knowledge` | no `def` on `TortoiseSDK` today |
| `supersede_knowledge` | no `def` on `TortoiseSDK` today |
| `delete_knowledge` | no `def` on `TortoiseSDK` today |
| `adjust_relationship` | no `def` on `TortoiseSDK` today |
| `refresh_confidence` | no `def` on `TortoiseSDK` today |
| `get_organisation_account` | no `def` on `TortoiseSDK` today |
| `create_memory_graph` | no `def` on `TortoiseSDK` today |
| `update_memory_graph` | no `def` on `TortoiseSDK` today |
| `list_memory_graphs` | no `def` on `TortoiseSDK` today |
| `delete_memory_graph` | no `def` on `TortoiseSDK` today |
| `restore_memory_graph` | no `def` on `TortoiseSDK` today |
| `create_key` | no `def` on `TortoiseSDK` today |
| `list_keys` | no `def` on `TortoiseSDK` today |
| `revoke_key` | no `def` on `TortoiseSDK` today |
| `add_member` | no `def` on `TortoiseSDK` today |
| `list_members` | no `def` on `TortoiseSDK` today |
| `remove_member` | no `def` on `TortoiseSDK` today |
| `check_connection` | no `def` on `TortoiseSDK` today |

`Tortoise` is row 1 of the target table (the constructor), not a method; the class today is `TortoiseSDK`, so the approved surface also renames the type.

### C2 — rows with NO doc backing

No document states a destination for these; the row is an open question, not an
answer. Each needs an owner ruling before Phase 2 implements it.

| Method | Source | Why it has no destination |
|---|---|---|
| `compute_reputation` | `sdk.py:20160` | The canonical `stabilize_beliefs` group lists it, but that group's beta target is `refresh_confidence` — “Recompute confidence after changes”. Reputation scoring is not confidence recomputation, and no other target absorbs it. |
| `org_create` | `sdk.py:15385` | No target method creates an organisation account. The tenancy block reads one (`get_organisation_account`) and files account *closure* as a console operation, but no row covers creation. |
| `record_calibration` | `sdk.py:20410` | Same group, same mismatch: `refresh_confidence` recomputes confidence; recording a calibration milestone is a different operation and has no target. |

**An unbacked row is a finding, not a gap to fill by analogy.** Rolling these into a nearby target would silently drop a capability the surface has today.

### C3 — cross-doc tensions

Two docs name **different** destinations for the same method. The row in Part A
carries the `beta-sdk-surface.md` destination, because that is the owner-approved
surface and the canonical inventory itself says so ("Where the two disagree, that
doc governs"). The conflict is recorded here rather than resolved silently.

| Method | Part A carries | The other doc implies | Other doc's grouping |
|---|---|---|---|
| `get_owned_entities` | `get_entity` | `explore_connections` | `canonical-sdk-methods.md` — “\| R5 \| `explore_connections` \| #5 \| `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` \|” |
| `get_provenance_chain` | `get_entity` | `check_confidence` | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \|” |
| `restore_point_at` | `get_historical_knowledge` | `check_confidence` | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \|” |
| `list_sources` | `list_knowledge` | `graph_overview` | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \|” |
| `test_guard` | `DISCARDED` | `graph_overview` | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \|” |
| `graph_set_recording` | `update_memory_graph` | kept, inside the control-plane block | `canonical-sdk-methods.md` — “\| N2 \| `graph` \| `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` \|” |

### C4 — names the disposition docs use that are NOT SDK methods

A doc→code name mismatch. Where a referent is named, the Part A row for that
referent cites the doc under its *doc* name; where no referent exists, the
doc's statement is about a method that was never there.

| Doc's name | Real method (if any) | Where the doc uses it |
|---|---|---|
| `recall_legs` | `retrieval_legs` | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence`” |
| `stale` | `stale_points` | `beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.**” |
| `count_memory_graphs` | `graph_count` | `beta-sdk-surface.md` — “\| `count_memory_graphs` \| The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. \|” |
| `set_memory_graph_name` | `graph_set_name` | `beta-sdk-surface.md` — “\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \| 3 \| See "Provisioning" above. \|” |
| `set_memory_graph_backend` | **none** | `beta-sdk-surface.md` — “\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \| 3 \| See "Provisioning" above. \|” |
| `index_sources` | `index_directory` | `beta-sdk-surface.md` — “\| `index_sources` (bare) \| 1 \| Renamed → `index_sources_from_directory`” |
| `withdraw_knowledge` | **none** | `beta-sdk-surface.md` — “\| `withdraw_knowledge` \| 1 \| **Never existed**” |

### C5 — canonical groups with no distinct member

The inventory's group table has group labels whose members are expressed as
wildcards (`org_*`, `graph_*`, …) that expand to the same methods as the
control-plane families. They carry no distinct member, so the partition here
resolves each method to its family group: `W16`.

### Structural notes

- The canonical inventory partitions the surface into **32 groups** over **149** named members; `backfill_v25` is in its Archived table instead. Total: 150 = the 150-method surface.
- Every group collapse above is checked against the AST walk at build time: a method the docs know and the code does not (or the reverse) **fails the build**.

---

## Reproduce

```bash
uv run python tools/sdk_rename_table.py          # regenerate this file
uv run python tools/sdk_rename_table.py --check  # verify, non-zero on drift
```
