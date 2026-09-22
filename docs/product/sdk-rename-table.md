# Phase 0.3b — the SDK rename table

**GENERATED — do not edit.** `uv run python tools/sdk_rename_table.py`; verify with `--check`.

Every `sdk.py:N` citation is **read from the AST at build time**, so it cannot drift from the code it cites. The 40 target names are **parsed out of `docs/product/beta-sdk-surface.md`** (owner-approved 2026-09-21), and the R/W/N group partition out of `docs/product/canonical-sdk-methods.md`; every count below is arithmetic over those, never a typed number. Each row's citation **names a REGION of the doc — a table row, a bullet, or a paragraph — and the generator EXTRACTS that whole region verbatim**; the anchor only locates, so an anchor truncated at a line wrap or a mid-row cell still renders the region whole and cannot drop the clause that contradicts the row. A citation whose region cannot be resolved, or cannot be rendered whole, fails the build.

**This is the SDK half of the rename table.** The MCP half (current tool → target tool) is `docs/product/bridge-table.md` (Phase 0.1), plus a sibling Phase 0.3b lane; nothing here restates it.

**The surface: 150 public methods on `TortoiseSDK` → 40 target methods.** 110 of the 150 are renames to a target; **4** are already targets (unchanged); **25** are discarded with a rationale; **4** are **contested** (filed under “Removed” but their own clause says the capability is reachable — a fold, not a delete, with no destination stated); **3** are **deferred** (live, filed post-beta and unlisted — NOT deleted); **1** is **relocated** out of the product SDK; and **3** have no destination anywhere on the target surface — those are findings, not rows to be guessed at.

**These are not the same number.** The target has 40 methods; only **4** of them exist on `TortoiseSDK` today. The other **36** are Phase 2 work, listed in Part C1.

---

## Part A — every public method and its migration row

`Basis` says how strongly the row is backed: **stated** — the cited quote names this method and gives its collapse, rename or deletion; **derived** — a doc gives the destination only for a namespace or wildcard covering this method, without naming it; **unbacked** — no doc gives a destination.

A `Target` that is not a target method is a **disposition**, and each is an owner ruling transcribed, not a choice made here: **UNCHANGED** — already on the target surface; **DISCARDED** — retires, cited from beta's “Discarded — and why” and supported by the row's own CLAUSE, not merely its section; **CONTESTED** — the doc files it under “Removed” while its own clause says the capability is reachable, so it is a fold, not a delete, and no destination is stated: an open finding; **DEFERRED** — beta's “Named but not solved”: the capability is **live** (in the MCP server today), filed post-beta and unlisted until then, so it is NOT deleted; **RELOCATED** — beta's “Kept and relocated”: the code must survive but moves OUT of the product SDK (test infrastructure), so it is neither a target nor deleted; **UNBACKED** — no doc states a destination.

The canonical inventory's group names are an **earlier sketch** (`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). The `Target` column always carries the **beta** target name (`update_knowledge`, `refresh_confidence`, `write_knowledge_batch`, `index_sources_from_directory`) — the canonical doc itself says beta governs where the two disagree, and records the renames.

| # | Method | Source | Group | Target | Basis | Citation |
|---|---|---|---|---|---|---|
| 1 | `annotate_ask_hits` | `sdk.py:13403` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 2 | `annotate_operator` | `sdk.py:6822` | W15 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \|” |
| 3 | `apikey_create` | `sdk.py:16315` | N4 | `create_key` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \| — \| builder \|” |
| 4 | `apikey_list` | `sdk.py:16419` | N4 | `list_keys` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \| — \| builder \|” |
| 5 | `apikey_revoke` | `sdk.py:16444` | N4 | `revoke_key` | derived | `beta-sdk-surface.md` — “\| 34 \| `create_key` \| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \| — \| builder \|” |
| 6 | `apikey_verify` | `sdk.py:16465` | N4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \|” |
| 7 | `approve_merge` | `sdk.py:6499` | W14 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.” |
| 8 | `assess_source` | `sdk.py:20150` | W8 | `manage_source_trust` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust` for the setter; reads via `list_sources`. \|” |
| 9 | `audit` | `sdk.py:7186` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview` where they are orientation. The diagnostics are the held question above. \|” |
| 10 | `backfill_about_entities` | `sdk.py:9626` | W4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations. Run once, then dead code carrying a public promise. \|” |
| 11 | `backfill_sources` | `sdk.py:19152` | W8 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations. Run once, then dead code carrying a public promise. \|” |
| 12 | `backfill_v25` | `sdk.py:20503` | ARCHIVE | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations. Run once, then dead code carrying a public promise. \|” |
| 13 | `batch_create_points` | `sdk.py:7398` | W1 | `write_knowledge_batch` | stated | `beta-sdk-surface.md` — “\| `batch_create_points` \| 1 \| → `write_knowledge_batch`. \|” |
| 14 | `belief_timeline` | `sdk.py:6243` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \|” |
| 15 | `calibrate_summary` | `sdk.py:12027` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| 16 | `calibration_passed` | `sdk.py:20627` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| 17 | `capture_session` | `sdk.py:3356` | W6 | `mine_knowledge_from_session` | stated | `beta-sdk-surface.md` — “\| `capture_session` / `commit_session` \| → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. \|” |
| 18 | `check_structure` | `sdk.py:7152` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 19 | `checkpoint` | `sdk.py:12328` | W5 | `DEFERRED` | stated | `beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.” |
| 20 | `cleanup_expired_invitations` | `sdk.py:16878` | N5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.** Never product surface. \|” |
| 21 | `close` | `sdk.py:9581` | W17 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.” |
| 22 | `commit_session` | `sdk.py:3193` | W7 | `mine_knowledge_from_session` | stated | `beta-sdk-surface.md` — “\| `capture_session` / `commit_session` \| → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. \|” |
| 23 | `complete_source` | `sdk.py:20482` | W3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `complete_source` \| 1 \| **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes — and it has **zero callers in the repo**. \|” |
| 24 | `compute_confidence` | `sdk.py:11517` | W13 | `refresh_confidence` | stated | `canonical-sdk-methods.md` — “\| W13 \| `stabilize_beliefs` \| #19 \| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \| keep — **`compute_confidence` mislabelled read** \|” |
| 25 | `compute_reputation` | `sdk.py:20292` | W13 | `UNBACKED` | unbacked | **no doc states a destination** |
| 26 | `create_derivation` | `sdk.py:19966` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`, which dispatches on the relation. \|” |
| 27 | `create_direct_edge` | `sdk.py:9040` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`, which dispatches on the relation. \|” |
| 28 | `create_document` | `sdk.py:19800` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \|” |
| 29 | `create_edge` | `sdk.py:20384` | W9 | `link_entities` | stated | `canonical-sdk-methods.md` — “\| W9 \| `link_entities` \| #15 \| `create_edge`, `create_derivation`, `link_source_to_entity`, `create_operator`, `create_direct_edge` \| keep, collapse — **one call, dispatching internally on `kind=`**: epistemic relations build a reified operator node, structural ones a bare edge. The caller never sees the split \|” |
| 30 | `create_entity` | `sdk.py:17417` | W1 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.” |
| 31 | `create_event` | `sdk.py:17600` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \|” |
| 32 | `create_object` | `sdk.py:17591` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \|” |
| 33 | `create_operator` | `sdk.py:6641` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`, which dispatches on the relation. \|” |
| 34 | `create_or_update_point` | `sdk.py:3140` | W1 | `create_entity` | stated | `canonical-sdk-methods.md` — “\| `create_or_update_point` → `create_point` \| `dedup` — a `**props` key popped with default **`False`** (not a declared parameter) \| single-statement delegate \|” |
| 35 | `create_point` | `sdk.py:2659` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \|” |
| 36 | `create_source` | `sdk.py:19812` | W3 | `register_source` | stated | `canonical-sdk-methods.md` — “\| W3 \| `register_source` \| #11 \| `create_source`, `complete_source` \| keep — **not foldable**: the URL is the node identity \|” |
| 37 | `create_subject` | `sdk.py:17582` | W1 | `create_entity` | stated | `beta-sdk-surface.md` — “\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \| 5 \| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \|” |
| 38 | `delete` | `sdk.py:5007` | W12 | `delete_knowledge` | stated | `canonical-sdk-methods.md` — “\| W12 \| `delete_knowledge` \| #18 \| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \| keep, collapse \|” |
| 39 | `delete_entity` | `sdk.py:20377` | W12 | `delete_knowledge` | stated | `canonical-sdk-methods.md` — “\| W12 \| `delete_knowledge` \| #18 \| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \| keep, collapse \|” |
| 40 | `delete_point` | `sdk.py:5153` | W12 | `delete_knowledge` | stated | `beta-sdk-surface.md` — “\| `delete_point`, `delete_point_wrapped` \| 2 \| → `delete_knowledge`. \|” |
| 41 | `delete_point_wrapped` | `sdk.py:5206` | W12 | `delete_knowledge` | stated | `beta-sdk-surface.md` — “\| `delete_point`, `delete_point_wrapped` \| 2 \| → `delete_knowledge`. \|” |
| 42 | `diary_read` | `sdk.py:12504` | W5 | `DEFERRED` | stated | `beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.” |
| 43 | `diary_write` | `sdk.py:12492` | W5 | `DEFERRED` | stated | `beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.” |
| 44 | `dream` | `sdk.py:10938` | W13 | `refresh_confidence` | stated | `canonical-sdk-methods.md` — “\| W13 \| `stabilize_beliefs` \| #19 \| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \| keep — **`compute_confidence` mislabelled read** \|” |
| 45 | `dream_health_check` | `sdk.py:10523` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview` where they are orientation. The diagnostics are the held question above. \|” |
| 46 | `dream_health_state` | `sdk.py:10672` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview` where they are orientation. The diagnostics are the held question above. \|” |
| 47 | `events_poll` | `sdk.py:2469` | R8 | `poll_events` | stated | `beta-sdk-surface.md` — “\| `events_poll` \| → row 11 `poll_events`. \|” |
| 48 | `expand_relationships` | `sdk.py:14694` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 49 | `file_decision` | `sdk.py:9323` | W10 | `write_question` | stated | `beta-sdk-surface.md` — “\| `file_decision` \| → rows 20/21 **`write_question`** + **`record_decision`**. It was filing a *question* and calling it a decision. \|” |
| 50 | `file_human_approval` | `sdk.py:9379` | W10 | `record_decision` | stated | `beta-sdk-surface.md` — “\| `file_human_approval` \| 1 \| → `record_decision`. \|” |
| 51 | `get_confidence` | `sdk.py:11792` | R3 | `check_confidence` | stated | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \| keep, collapse — absorbs the confidence reads and both provenance methods \|” |
| 52 | `get_cross_lens_candidates` | `sdk.py:9821` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 53 | `get_entity` | `sdk.py:20368` | R4 | `UNCHANGED` | stated | `beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.” |
| 54 | `get_events` | `sdk.py:17790` | R4 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`, except where a genuinely different shape is returned. \|” |
| 55 | `get_org_structure` | `sdk.py:20456` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 56 | `get_owned_entities` | `sdk.py:20413` | R5 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`, except where a genuinely different shape is returned. \|” |
| 57 | `get_point` | `sdk.py:7110` | R4 | `get_entity` | stated | `canonical-sdk-methods.md` — “\| R4 \| `get_entity` \| #4 \| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \| keep, collapse \|” |
| 58 | `get_provenance_chain` | `sdk.py:20427` | R3 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`, except where a genuinely different shape is returned. \|” |
| 59 | `get_session` | `sdk.py:17803` | R4 | `get_entity` | stated | `beta-sdk-surface.md` — “\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \| ~8 \| → `get_entity`, except where a genuinely different shape is returned. \|” |
| 60 | `get_source_reliability` | `sdk.py:20046` | W8 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust` for the setter; reads via `list_sources`. \|” |
| 61 | `graph_active_key_count` | `sdk.py:15960` | N2 | `list_keys` | stated | `beta-sdk-surface.md` — “\| `graph_key_ids`, `graph_active_key_count` \| 2 \| Console diagnostics. Both fold into `list_keys`. \|” |
| 62 | `graph_count` | `sdk.py:15804` | N2 | `list_memory_graphs` | derived | `beta-sdk-surface.md` — “\| `count_memory_graphs` \| The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. \|” |
| 63 | `graph_delete` | `sdk.py:15843` | N2 | `delete_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 64 | `graph_key_ids` | `sdk.py:15948` | N2 | `list_keys` | stated | `beta-sdk-surface.md` — “\| `graph_key_ids`, `graph_active_key_count` \| 2 \| Console diagnostics. Both fold into `list_keys`. \|” |
| 65 | `graph_list` | `sdk.py:15764` | N2 | `list_memory_graphs` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 66 | `graph_restore` | `sdk.py:15871` | N2 | `restore_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 67 | `graph_set_name` | `sdk.py:15983` | N2 | `update_memory_graph` | stated | `beta-sdk-surface.md` — “\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \| → rows 30–33 `*_memory_graph*`. \|” |
| 68 | `graph_set_recording` | `sdk.py:15912` | N2 | `update_memory_graph` | stated | `beta-sdk-surface.md` — “\| ~~`graph_set_recording`~~ (SDK method) \| 1 \| **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. \|” |
| 69 | `index_directory` | `sdk.py:17893` | W4 | `index_sources_from_directory` | stated | `canonical-sdk-methods.md` — “\| W4 \| `index_files` \| #12 \| `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` \| keep — **2 self-declared DEPRECATED** \|” |
| 70 | `index_file` | `sdk.py:17815` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 71 | `index_sessions` | `sdk.py:19619` | W4 | `index_sources_from_directory` | stated | `canonical-sdk-methods.md` — “\| `index_sessions` / `ingest_corpus` → `index_directory` \| `file_type` \| both self-declared DEPRECATED in their own docstrings \|” |
| 72 | `ingest` | `sdk.py:8265` | W2 | `write_knowledge_batch` | stated | `canonical-sdk-methods.md` — “\| W2 \| `write_knowledge` \| — \| `ingest` \| keep — **SDK-only name.** The batch call: one bundle writes points + entities + sources + connections atomically, with local `ref` labels so connections can address nodes created in the same call. Not to be called `ingest_bundle` (jargon) or `write_graph` (collides with `graph_overview` and the graph admin namespace) \|” |
| 73 | `ingest_corpus` | `sdk.py:12543` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 74 | `invalidate_point` | `sdk.py:5262` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `retract_point`, `invalidate_point` \| 2 \| → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. \|” |
| 75 | `invitation_accept` | `sdk.py:16808` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 76 | `invitation_create` | `sdk.py:16727` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 77 | `invitation_get_by_id` | `sdk.py:16852` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 78 | `invitation_get_by_token` | `sdk.py:16803` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 79 | `invitation_list` | `sdk.py:16785` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 80 | `invitation_revoke` | `sdk.py:16861` | N5 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `invitation_*` (6) \| 6 \| The invite **UX** belongs to the console, where a human clicks it. \|” |
| 81 | `issue_insight` | `sdk.py:13160` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 82 | `link_source_to_entity` | `sdk.py:20437` | W9 | `link_entities` | stated | `beta-sdk-surface.md` — “\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \| 4 \| → `link_entities`, which dispatches on the relation. \|” |
| 83 | `list_batch` | `sdk.py:8920` | R9 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `list_batch`, `list_batches` \| 2 \| → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. \|” |
| 84 | `list_batches` | `sdk.py:8989` | R9 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `list_batch`, `list_batches` \| 2 \| → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. \|” |
| 85 | `list_dedup_candidates` | `sdk.py:6430` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 86 | `list_drafts` | `sdk.py:6591` | W11 | `CONTESTED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling — reachable through the canonical two. \|” |
| 87 | `list_graphs` | `sdk.py:9517` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 88 | `list_namespaces` | `sdk.py:7344` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 89 | `list_pointkinds` | `sdk.py:7278` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 90 | `list_relations` | `sdk.py:9534` | R6 | `graph_overview` | stated | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \| keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability \|” |
| 91 | `list_sources` | `sdk.py:7303` | R6 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `list_sources` \| **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. \|” |
| 92 | `list_tags` | `sdk.py:7317` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 93 | `list_topics` | `sdk.py:7357` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 94 | `membership_create` | `sdk.py:16167` | N3 | `add_member` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \| — \| admin \|” |
| 95 | `membership_delete` | `sdk.py:16257` | N3 | `remove_member` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \| — \| admin \|” |
| 96 | `membership_get` | `sdk.py:16219` | N3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \|” |
| 97 | `membership_list` | `sdk.py:16228` | N3 | `list_members` | derived | `beta-sdk-surface.md` — “\| 37 \| `add_member` \| Grant a person access to the account \| — \| admin \|” |
| 98 | `membership_update_role` | `sdk.py:16237` | N3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \|” |
| 99 | `migrate_orgs_to_registry` | `sdk.py:16128` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.** Never product surface. \|” |
| 100 | `mine_corpus` | `sdk.py:12974` | W4 | `mine_knowledge_from_directory` | stated | `beta-sdk-surface.md` — “\| `mine_corpus` \| 1 \| → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. \|” |
| 101 | `mitigate_operator` | `sdk.py:6862` | W15 | `adjust_relationship` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \|” |
| 102 | `operator_action` | `sdk.py:6799` | W15 | `adjust_relationship` | stated | `beta-sdk-surface.md` — “\| `mitigate_operator`, `operator_action`, `annotate_operator` \| 3 \| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \|” |
| 103 | `org_create` | `sdk.py:15499` | N1 | `UNBACKED` | unbacked | **no doc states a destination** |
| 104 | `org_delete` | `sdk.py:16065` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \|” |
| 105 | `org_get` | `sdk.py:16018` | N1 | `get_organisation_account` | derived | `beta-sdk-surface.md` — “\| 28 \| `get_organisation_account` \| Read the account and the plan it is on \| — \| admin \|” |
| 106 | `org_list` | `sdk.py:16027` | N1 | `get_organisation_account` | derived | `beta-sdk-surface.md` — “\| 28 \| `get_organisation_account` \| Read the account and the plan it is on \| — \| admin \|” |
| 107 | `org_update` | `sdk.py:16035` | N1 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \| 5 \| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \|” |
| 108 | `paginated_query` | `sdk.py:7048` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 109 | `promote_point` | `sdk.py:5934` | W11 | `CONTESTED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling — reachable through the canonical two. \|” |
| 110 | `provenance` | `sdk.py:9596` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \|” |
| 111 | `quarantine_batch` | `sdk.py:6231` | W11 | `CONTESTED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling — reachable through the canonical two. \|” |
| 112 | `query` | `sdk.py:6995` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 113 | `query_points_by_tag` | `sdk.py:7333` | R2 | `list_knowledge` | stated | `beta-sdk-surface.md` — “\| `query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`. \|” |
| 114 | `recall_gaps` | `sdk.py:15298` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| 115 | `recall_state` | `sdk.py:14873` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| 116 | `recall_subgraph` | `sdk.py:15421` | R3 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| 117 | `reconcile_sessions` | `sdk.py:19720` | W4 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \| 4 \| One-shot migrations. Run once, then dead code carrying a public promise. \|” |
| 118 | `record_calibration` | `sdk.py:20542` | W13 | `UNBACKED` | unbacked | **no doc states a destination** |
| 119 | `resolve_id` | `sdk.py:3146` | R4 | `get_entity` | stated | `canonical-sdk-methods.md` — “\| R4 \| `get_entity` \| #4 \| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \| keep, collapse \|” |
| 120 | `restore_point_at` | `sdk.py:14732` | R3 | `get_historical_knowledge` | stated | `beta-sdk-surface.md` — “\| `restore_point_at` \| → row 7 **`get_historical_knowledge`**. A **read**, not a write — it returns the version of a claim valid on a date and mutates nothing. \|” |
| 121 | `retract_point` | `sdk.py:5869` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `retract_point`, `invalidate_point` \| 2 \| → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. \|” |
| 122 | `retrieval_legs` | `sdk.py:15060` | R3 | `check_confidence` | stated | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \| keep, collapse — absorbs the confidence reads and both provenance methods \|” |
| 123 | `review_connections` | `sdk.py:9695` | R7 | `review_link_candidates` | stated | `beta-sdk-surface.md` — “\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \| 3 \| → `review_link_candidates`. \|” |
| 124 | `search_sessions` | `sdk.py:17649` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 125 | `session_context` | `sdk.py:13098` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \|” |
| 126 | `session_index_health` | `sdk.py:19634` | W4 | `index_sources_from_directory` | stated | `beta-sdk-surface.md` — “\| `ingest_corpus`, `index_file`, `session_index_health` \| 3 \| → `index_sources_from_directory`. \|” |
| 127 | `set_point_baseline` | `sdk.py:11701` | W11 | `CONTESTED` | stated | `beta-sdk-surface.md` — “\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| 4 \| Lifecycle and confidence wrangling — reachable through the canonical two. \|” |
| 128 | `set_source_tier` | `sdk.py:19925` | W8 | `manage_source_trust` | stated | `beta-sdk-surface.md` — “\| `assess_source`, `set_source_tier`, `get_source_reliability` \| 3 \| → `manage_source_trust` for the setter; reads via `list_sources`. \|” |
| 129 | `signup_token_lookup` | `sdk.py:16549` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup — our provisioning, not product surface. \|” |
| 130 | `signup_token_recover` | `sdk.py:16580` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup — our provisioning, not product surface. \|” |
| 131 | `signup_token_revoke` | `sdk.py:16660` | N6 | `DISCARDED` | derived | `beta-sdk-surface.md` — “\| `signup_token_*` (3) \| 3 \| Operator-side agent self-signup — our provisioning, not product surface. \|” |
| 132 | `stale_points` | `sdk.py:9686` | R6 | `graph_overview` | derived | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 133 | `status` | `sdk.py:12524` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 134 | `suggest_entry_points` | `sdk.py:13001` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 135 | `summarize_structure` | `sdk.py:7204` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview` where they are orientation. The diagnostics are the held question above. \|” |
| 136 | `supersede` | `sdk.py:5355` | W11 | `supersede_knowledge` | stated | `beta-sdk-surface.md` — “\| `supersede`, `supersede_point` \| 2 \| → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. \|” |
| 137 | `supersede_point` | `sdk.py:5373` | W11 | `supersede_knowledge` | stated | `beta-sdk-surface.md` — “\| `supersede`, `supersede_point` \| 2 \| → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. \|” |
| 138 | `sweep_invite_ghost_memberships` | `sdk.py:16924` | N5 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.** Never product surface. \|” |
| 139 | `taxonomy` | `sdk.py:7273` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| 140 | `test_guard` | `sdk.py:2292` | R6 | `RELOCATED` | stated | `beta-sdk-surface.md` — “\| `test_guard` \| **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK. \|” |
| 141 | `topic_summarize` | `sdk.py:7362` | R1 | `search_knowledge` | stated | `beta-sdk-surface.md` — “\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \| 5 \| → `search_knowledge`. \|” |
| 142 | `tortoise_fts_query` | `sdk.py:13556` | R1 | `search_knowledge` | stated | `canonical-sdk-methods.md` — “\| R1 \| `search_knowledge` \| #1 \| `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` \| keep, collapse \|” |
| 143 | `trash_graphs` | `sdk.py:15893` | N2 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \| 4 \| **Our maintenance.** Never product surface. \|” |
| 144 | `traverse` | `sdk.py:7124` | R5 | `explore_connections` | stated | `beta-sdk-surface.md` — “\| `traverse`, `expand_relationships`, `get_org_structure` \| 3 \| → `explore_connections`. \|” |
| 145 | `ulid` | `sdk.py:20476` | W17 | `DISCARDED` | stated | `beta-sdk-surface.md` — “\| `ulid` \| 1 \| A ULID generator. Not a memory operation. \|” |
| 146 | `update` | `sdk.py:4987` | W11 | `update_knowledge` | stated | `canonical-sdk-methods.md` — “\| W11 \| `revise_knowledge` \| #17 \| `update`, `update_point`, `update_entity`, `supersede`, `supersede_point`, `invalidate_point`, `retract_point`, `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \| keep, collapse — **the widest group; see Open items** \|” |
| 147 | `update_entity` | `sdk.py:20371` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `update_point`, `update_entity` \| 2 \| → `update_knowledge`. \|” |
| 148 | `update_point` | `sdk.py:5023` | W11 | `update_knowledge` | stated | `beta-sdk-surface.md` — “\| `update_point`, `update_entity` \| 2 \| → `update_knowledge`. \|” |
| 149 | `validate_domain` | `sdk.py:7170` | R6 | `graph_overview` | stated | `beta-sdk-surface.md` — “\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \| ~5 \| → `graph_overview` where they are orientation. The diagnostics are the held question above. \|” |
| 150 | `volunteer_context` | `sdk.py:15130` | R3 | `check_confidence` | stated | `beta-sdk-surface.md` — “\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \| 4 \| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \|” |

## Part B — destination counts

| Destination | Current methods | Count |
|---|---|---|
| `DISCARDED` | `apikey_verify`, `backfill_about_entities`, `backfill_sources`, `backfill_v25`, `cleanup_expired_invitations`, `complete_source`, `invitation_accept`, `invitation_create`, `invitation_get_by_id`, `invitation_get_by_token`, `invitation_list`, `invitation_revoke`, `membership_get`, `membership_update_role`, `migrate_orgs_to_registry`, `org_delete`, `org_update`, `recall_subgraph`, `reconcile_sessions`, `signup_token_lookup`, `signup_token_recover`, `signup_token_revoke`, `sweep_invite_ghost_memberships`, `trash_graphs`, `ulid` | 25 |
| `graph_overview` | `audit`, `check_structure`, `dream_health_check`, `dream_health_state`, `list_graphs`, `list_namespaces`, `list_pointkinds`, `list_relations`, `list_tags`, `list_topics`, `stale_points`, `status`, `summarize_structure`, `taxonomy`, `validate_domain` | 15 |
| `check_confidence` | `belief_timeline`, `calibrate_summary`, `calibration_passed`, `get_confidence`, `provenance`, `recall_gaps`, `recall_state`, `retrieval_legs`, `session_context`, `volunteer_context` | 10 |
| `list_knowledge` | `get_source_reliability`, `list_batch`, `list_batches`, `list_sources`, `paginated_query`, `query`, `query_points_by_tag` | 7 |
| `create_entity` | `create_document`, `create_event`, `create_object`, `create_or_update_point`, `create_point`, `create_subject` | 6 |
| `get_entity` | `get_events`, `get_owned_entities`, `get_point`, `get_provenance_chain`, `get_session`, `resolve_id` | 6 |
| `search_knowledge` | `annotate_ask_hits`, `issue_insight`, `search_sessions`, `suggest_entry_points`, `topic_summarize`, `tortoise_fts_query` | 6 |
| `update_knowledge` | `annotate_operator`, `invalidate_point`, `retract_point`, `update`, `update_entity`, `update_point` | 6 |
| `index_sources_from_directory` | `index_directory`, `index_file`, `index_sessions`, `ingest_corpus`, `session_index_health` | 5 |
| `link_entities` | `create_derivation`, `create_direct_edge`, `create_edge`, `create_operator`, `link_source_to_entity` | 5 |
| `CONTESTED` | `list_drafts`, `promote_point`, `quarantine_batch`, `set_point_baseline` | 4 |
| `UNCHANGED` | `approve_merge`, `close`, `create_entity`, `get_entity` | 4 |
| `delete_knowledge` | `delete`, `delete_entity`, `delete_point`, `delete_point_wrapped` | 4 |
| `DEFERRED` | `checkpoint`, `diary_read`, `diary_write` | 3 |
| `UNBACKED` | `compute_reputation`, `org_create`, `record_calibration` | 3 |
| `explore_connections` | `expand_relationships`, `get_org_structure`, `traverse` | 3 |
| `list_keys` | `apikey_list`, `graph_active_key_count`, `graph_key_ids` | 3 |
| `review_link_candidates` | `get_cross_lens_candidates`, `list_dedup_candidates`, `review_connections` | 3 |
| `adjust_relationship` | `mitigate_operator`, `operator_action` | 2 |
| `get_organisation_account` | `org_get`, `org_list` | 2 |
| `list_memory_graphs` | `graph_count`, `graph_list` | 2 |
| `manage_source_trust` | `assess_source`, `set_source_tier` | 2 |
| `mine_knowledge_from_session` | `capture_session`, `commit_session` | 2 |
| `refresh_confidence` | `compute_confidence`, `dream` | 2 |
| `supersede_knowledge` | `supersede`, `supersede_point` | 2 |
| `update_memory_graph` | `graph_set_name`, `graph_set_recording` | 2 |
| `write_knowledge_batch` | `batch_create_points`, `ingest` | 2 |
| `RELOCATED` | `test_guard` | 1 |
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

Distinct destinations: **41** — **33** are target methods with no `def` today (Part C1 lists all 36 Phase-2 methods), **2** are target methods that already exist (`create_entity`, `get_entity`), and **6** are the non-target dispositions (`CONTESTED` / `DEFERRED` / `DISCARDED` / `RELOCATED` / `UNBACKED` / `UNCHANGED`).

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
| `compute_reputation` | `sdk.py:20292` | The canonical `stabilize_beliefs` group lists it, but that group's beta target is `refresh_confidence` — “Recompute confidence after changes”. Reputation scoring is not confidence recomputation, and no other target absorbs it. |
| `org_create` | `sdk.py:15499` | No target method creates an organisation account. The tenancy block reads one (`get_organisation_account`) and files account *closure* as a console operation, but no row covers creation. |
| `record_calibration` | `sdk.py:20542` | Same group, same mismatch: `refresh_confidence` recomputes confidence; recording a calibration milestone is a different operation and has no target. |

**An unbacked row is a finding, not a gap to fill by analogy.** Rolling these into a nearby target would silently drop a capability the surface has today.

### C3 — cross-doc tensions

Two docs name **different** destinations for the same method. The row in Part A
carries the `beta-sdk-surface.md` destination, because that is the owner-approved
surface and the canonical inventory itself says so ("Where the two disagree, that
doc governs"). The conflict is recorded here rather than resolved silently.

| Method | Part A carries | The other doc implies | Other doc's grouping |
|---|---|---|---|
| `get_owned_entities` | `get_entity` | `explore_connections` | `canonical-sdk-methods.md` — “\| R5 \| `explore_connections` \| #5 \| `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` \| keep, collapse \|” |
| `get_provenance_chain` | `get_entity` | `check_confidence` | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \| keep, collapse — absorbs the confidence reads and both provenance methods \|” |
| `restore_point_at` | `get_historical_knowledge` | `check_confidence` | `canonical-sdk-methods.md` — “\| R3 \| `recall_beliefs` \| #3 \| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \| keep, collapse — absorbs the confidence reads and both provenance methods \|” |
| `list_sources` | `list_knowledge` | `graph_overview` | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \| keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability \|” |
| `test_guard` | `RELOCATED` | `graph_overview` | `canonical-sdk-methods.md` — “\| R6 \| `graph_overview` \| #6 \| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \| keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability \|” |
| `graph_set_recording` | `update_memory_graph` | kept, inside the control-plane block | `canonical-sdk-methods.md` — “\| N2 \| `graph` \| `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` \|” |
| `get_source_reliability` | `list_knowledge` | `manage_source_trust` | `canonical-sdk-methods.md` — “\| W8 \| `manage_source_trust` \| #14 \| `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` \| keep — **`get_source_reliability` writes** \|” |
| `annotate_operator` | `update_knowledge` | `adjust_relationship` | `canonical-sdk-methods.md` — “\| W15 \| `adjust_relationship` \| #21 \| `operator_action`, `mitigate_operator`, `annotate_operator` \| keep, collapse \|” |

### C3b — cross-artifact divergences with the bridge table (Phase 0.1)

This table and `docs/product/bridge-table.md` answer **different questions** — here, current SDK *method* → target *method*; there, current MCP *tool* → target *tool* — so a tool and the method it binds to can legitimately reach different targets, and where they do the divergence is recorded rather than harmonised by picking one. The sibling is named as the **dissenting source** on every row. The list is **computed from `bridge-table.md` at build time**; each row's determination is authored, because the reason IS the finding. The build fails if a divergence carries no determination, or a determination carries no divergence.

| Method | Part A carries | `bridge-table.md` carries | Its current tool | Determination |
|---|---|---|---|---|
| `annotate_operator` | `update_knowledge` | `adjust_relationship` | `tortoise_annotate_operator` | **Part A is right; the bridge is wrong.** beta row 25 states it twice — “Annotating a link is `update_knowledge` on it” and the W15 row's “`adjust_relationship` for strength, `update_knowledge` for annotation”. The bridge follows the canonical sketch's W15 grouping, which beta governs. |
| `checkpoint` | `DEFERRED` | `REMOVED` | `tortoise_checkpoint` | **Part A is right; the sibling carries the same defect.** beta files `checkpoint` under “Named but not solved”: live in the MCP server, filed post-beta, unlisted — not dead. The bridge's `REMOVED` (“retires with no destination”) reads it as discarded. |
| `compute_confidence` | `refresh_confidence` | `check_confidence` | `tortoise_compute_confidence` | **Part A is right.** The canonical W13 group is renamed by beta to `refresh_confidence` (“Recompute confidence after changes”); `check_confidence` is the READ (“Returns the confidence view only”). `compute_confidence` recomputes, so the bridge follows the canonical group rather than beta's rename. |
| `diary_read` | `DEFERRED` | `REMOVED` | `tortoise_diary_read` | **Part A is right; the sibling carries the same defect.** beta files `diary_read` under “Named but not solved” — live and unlisted, not dead. The bridge's `REMOVED` reads it as discarded. |
| `diary_write` | `DEFERRED` | `REMOVED` | `tortoise_diary_write` | **Part A is right; the sibling carries the same defect.** beta files `diary_write` under “Named but not solved” — live and unlisted, not dead. The bridge's `REMOVED` reads it as discarded. |
| `invalidate_point` | `update_knowledge` | `supersede_knowledge` | `tortoise_invalidate` | **Part A is right.** beta: “`retract_point`, `invalidate_point` \| 2 \| → fields on `update_knowledge`”, and its retraction rationale is “not a separate verb”. The bridge follows the canonical collapse table's `invalidate_point` → `supersede`. |
| `list_graphs` | `graph_overview` | `tenancy:list_memory_graphs` | `tortoise_list_graphs` | **Part A is right; the bridge conflates two methods.** beta's narrow-aliases row names `list_graphs` among the aliases absorbed by `graph_overview` and deleted, while `graph_list` is the one sent to `list_memory_graphs`. The canonical doc's “does not merge” list keeps `list_graphs` ≠ `graph_list` (raw DB names vs control-plane rows). |
| `list_namespaces` | `graph_overview` | `list_knowledge` | `tortoise_list_namespaces` | **Part A is right; the bridge is wrong.** beta's narrow-aliases row names `list_namespaces` among the aliases absorbed by `graph_overview`, and canonical R6 lists it there too. |
| `list_sources` | `list_knowledge` | `graph_overview` | `tortoise_list_sources` | **Part A is right (beta governs).** beta's `list_sources` row is explicit that it is **not discarded** and folds into **row 4 `list_knowledge(kind='source')`**, and beta's `get_source_reliability` row routes its reads via `list_sources`. The bridge sends it to `graph_overview` — canonical R6's home for it — but beta governs the surface, so Part A carries beta's destination. |
| `list_topics` | `graph_overview` | `list_knowledge` | `tortoise_list_topics` | **Part A is right; the bridge is wrong.** beta's narrow-aliases row names `list_topics` among the aliases absorbed by `graph_overview`, and canonical R6 lists it there too. |
| `org_create` | `UNBACKED` | `tenancy:create_memory_graph` | `tortoise_org_create` | **Genuinely contested — no owner ruling.** No approved doc places organisation-account creation. `create_memory_graph` (beta row 29) provisions a memory GRAPH, not an account, and beta's tenancy block has no creation row. Part A's `UNBACKED` is the honest record; the bridge asserts a destination no doc states. |
| `paginated_query` | `list_knowledge` | `search_knowledge` | `tortoise_paginated_query` | **Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method. |
| `promote_point` | `CONTESTED` | `refresh_confidence` | `tortoise_promote_point` | **Genuinely contested — no owner ruling.** beta's row sits in “Removed” but reads “reachable through the canonical two” (a fold, not a delete), and the canonical doc's “does not merge” keeps `promote_point` ≠ `update_point(status='live')` because promote also promotes incident operators. The bridge's `refresh_confidence` does not cover that either. |
| `query` | `list_knowledge` | `search_knowledge` | `tortoise_query` | **Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method. |
| `query_points_by_tag` | `list_knowledge` | `search_knowledge` | `tortoise_query_points_by_tag` | **Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \| 3 \| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method. |
| `set_point_baseline` | `CONTESTED` | `refresh_confidence` | `tortoise_set_point_baseline` | **Genuinely contested — no owner ruling.** Same beta row as `promote_point`. The bridge's `refresh_confidence` recomputes confidence; declaring a claim's starting belief is a different operation, and canonical's “What we have” rates it a strong novelty (“no product has a per-claim prior”). |

### C4 — names the disposition docs use that are NOT SDK methods

A doc→code name mismatch. Where a referent is named, the Part A row for that
referent cites the doc under its *doc* name; where no referent exists, the
doc's statement is about a method that was never there.

| Doc's name | Real method (if any) | Where the doc uses it |
|---|---|---|
| `recall_legs` | `retrieval_legs` | `beta-sdk-surface.md` — “\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \| ~6 \| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \|” |
| `stale` | `stale_points` | `beta-sdk-surface.md` — “\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \|” |
| `count_memory_graphs` | `graph_count` | `beta-sdk-surface.md` — “\| `count_memory_graphs` \| The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. \|” |
| `set_memory_graph_name` | `graph_set_name` | `beta-sdk-surface.md` — “\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \| 3 \| See "Provisioning" above. \|” |
| `set_memory_graph_backend` | **none** | `beta-sdk-surface.md` — “\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \| 3 \| See "Provisioning" above. \|” |
| `index_sources` | `index_directory` | `beta-sdk-surface.md` — “\| `index_sources` (bare) \| 1 \| Renamed → `index_sources_from_directory`, so the index/mine distinction is unmissable. \|” |
| `withdraw_knowledge` | **none** | `beta-sdk-surface.md` — “\| `withdraw_knowledge` \| 1 \| **Never existed** — removed from the plan. Retraction is a field on `update_knowledge`. \|” |

### C5 — canonical groups with no distinct member

The inventory's group table has group labels whose members are expressed as
wildcards (`org_*`, `graph_*`, …) that expand to the same methods as the
control-plane families. They carry no distinct member, so the partition here
resolves each method to its family group: `W16`.

### C6 — rows filed under “Removed” whose own clause says they are reachable

`DISCARDED` is Phase 2's signal to **delete** a method. beta's `### Removed`
table is one ruling and the rationale cell is another, and for these rows the
clause says the capability is *reachable* — a **fold, not a delete** — while
naming no destination. They carry `CONTESTED`, which is an open finding: the
capability is not deleted and no target absorbs it, so Phase 2 cannot
implement them from this document and needs an owner ruling.

| Method | Source | Why the disposition is contested |
|---|---|---|
| `list_drafts` | `sdk.py:6591` | Its “Removed” row says “reachable” — a fold, not a delete, with no destination named. |
| `promote_point` | `sdk.py:5934` | Its “Removed” row says “reachable” — a fold, not a delete, with no destination named. |
| `quarantine_batch` | `sdk.py:6231` | Its “Removed” row says “reachable” — a fold, not a delete, with no destination named. |
| `set_point_baseline` | `sdk.py:11701` | Its “Removed” row says “reachable” — a fold, not a delete, with no destination named. |

### Structural notes

- The canonical inventory partitions the surface into **32 groups** over **149** named members; `backfill_v25` is in its Archived table instead. Total: 150 = the 150-method surface.
- Every group collapse above is checked against the AST walk at build time: a method the docs know and the code does not (or the reverse) **fails the build**.

---

## Reproduce

```bash
uv run python tools/sdk_rename_table.py          # regenerate this file
uv run python tools/sdk_rename_table.py --check  # verify, non-zero on drift
```
