#!/usr/bin/env python3
"""Generate the Phase 0.3b **SDK rename table** for tortoise #4282.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT
---------------------------------------
The first attempt at the sibling Phase 0.1 artifact was hand-written prose carrying
`file:line` citations. Five verification passes each found MORE that did not resolve.
**Hand-written line numbers do not converge**, so every citation here is READ FROM THE
SOURCE at build time and cannot drift from the code it cites.

The SDK half of the rename table (the MCP half is a sibling lane). Two questions, per
#4282:

  1. For **every public method on `TortoiseSDK` today**, what does a caller use instead
     — a new method, "unchanged", or "discarded, and here is why"?
  2. Which of those rows are **not actually backed by a doc**? An unbacked row is a
     finding, not a row to be quietly invented.

WHAT IS DERIVED AND WHAT IS AUTHORED
------------------------------------
Derived (computed, never typed):
  * the **method set** — an AST walk of the `TortoiseSDK` class body (via
    `tools.bridge_table._sdk_targets`, the Phase 0.1 walker; a second copy would be a
    second answer to "what is the public surface" and the two would drift);
  * every `sdk.py:N` line number, and the 284/150 def count;
  * the **40 targets** — parsed out of `docs/product/beta-sdk-surface.md` (the
    owner-approved surface), not re-typed here;
  * the **canonical group partition** (R1–R9 / W1–W17 / N1–N6) — parsed out of
    `docs/product/canonical-sdk-methods.md` and checked to cover the AST walk exactly;
  * every count in this document, and the findings in Part C.

Authored (a design decision, reviewed once):
  * which target each group collapses to, and the handful of per-method exceptions;
  * the citation for each row — a `(doc, quote)` pair whose quote is **verified to be a
    substring of the cited doc at build time**, so a citation cannot drift either.

The generator **FAILS** (it does not paper over) if the map and the SDK disagree, if the
canonical partition does not cover the public surface exactly, or if a citation's quote
is no longer in the doc it names.

USAGE
    uv run python tools/sdk_rename_table.py            # write the doc
    uv run python tools/sdk_rename_table.py --check    # verify only, non-zero on drift
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The Phase 0.1 generator owns the one AST walk of `TortoiseSDK` and the one reader of
# the tool registry. Importing them is the point: a second `_sdk_targets()` would be a
# second opinion about what the public surface IS, and the two would eventually differ.
from tools.bridge_table import _sdk_targets  # noqa: E402

SDK_SRC = ROOT / "tortoise" / "sdk.py"
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"
CANON_DOC = ROOT / "docs" / "product" / "canonical-sdk-methods.md"
OUT = ROOT / "docs" / "product" / "sdk-rename-table.md"

BETA = "beta-sdk-surface.md"
CANON = "canonical-sdk-methods.md"

# The three dispositions that are not a target method name.
UNCHANGED = "UNCHANGED"
DISCARDED = "DISCARDED"
UNBACKED = "UNBACKED"

# Why a row has no destination. Authored, because the reason IS the finding.
UNBACKED_REASON = {
    "org_create": "No target method creates an organisation account. The tenancy block "
                  "reads one (`get_organisation_account`) and files account *closure* "
                  "as a console operation, but no row covers creation.",
    "compute_reputation": "The canonical `stabilize_beliefs` group lists it, but that "
                          "group's beta target is `refresh_confidence` — “Recompute "
                          "confidence after changes”. Reputation scoring is not "
                          "confidence recomputation, and no other target absorbs it.",
    "record_calibration": "Same group, same mismatch: `refresh_confidence` recomputes "
                          "confidence; recording a calibration milestone is a different "
                          "operation and has no target.",
}

# ─────────────────────────────────────────────────────────────────────
# CITATIONS. A citation is only real if the doc still says it, so each
# `needle` is verified as a literal substring of the named doc at build
# time (`_validate`). `named` is the set of methods the quote NAMES: a row
# whose method is in its citation's `named` set is *stated*; otherwise it
# is *derived* from the group collapse.
# ─────────────────────────────────────────────────────────────────────
CITES: dict[str, tuple[str, str, frozenset[str]]] = {
    # ── READ ────────────────────────────────────────────────────────
    "r1": (BETA, "| `search_sessions`, `suggest_entry_points`, `topic_summarize`, "
                 "`issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |",
           frozenset({"search_sessions", "suggest_entry_points", "topic_summarize",
                      "issue_insight", "annotate_ask_hits"})),
    "r2": (BETA, "| `query`, `paginated_query`, `query_points_by_tag` | 3 | "
                 "→ `list_knowledge`. |",
           frozenset({"query", "paginated_query", "query_points_by_tag"})),
    "r1_canon": (CANON, "| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, "
                        "`suggest_entry_points`, `search_sessions`, `issue_insight`, "
                        "`topic_summarize`, `annotate_ask_hits` |",
                 frozenset({"tortoise_fts_query"})),
    "r3": (BETA, "| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, "
                 "`calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence`",
           frozenset({"recall_gaps", "recall_state", "calibrate_summary",
                      "calibration_passed"})),
    "r3_restore": (BETA, "| `restore_point_at` | → row 7 **`get_historical_knowledge`**.",
                   frozenset({"restore_point_at"})),
    "r3_drop_subgraph": (BETA, "**`recall_subgraph` is dropped, not folded**",
                         frozenset({"recall_subgraph"})),
    "r3_context": (BETA, "| `provenance`, `belief_timeline`, `session_context`, "
                         "`volunteer_context` | 4 | → `check_confidence`",
                   frozenset({"provenance", "belief_timeline", "session_context",
                              "volunteer_context"})),
    "r3_canon": (CANON, "| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, "
                        "`recall_subgraph`, `retrieval_legs`, `volunteer_context`, "
                        "`session_context`, `get_confidence`",
                 frozenset({"get_confidence", "retrieval_legs"})),
    "r4": (BETA, "| narrow readers (`get_session`, `get_events`, `get_owned_entities`, "
                 "`get_provenance_chain`, …) | ~8 | → `get_entity`",
           frozenset({"get_session", "get_events", "get_owned_entities",
                      "get_provenance_chain"})),
    "r4_canon": (CANON, "| R4 | `get_entity` | #4 | `get_point`, `get_entity`, "
                        "`get_session`, `get_events`, `resolve_id` |",
                 frozenset({"get_point", "resolve_id"})),
    "r5": (BETA, "| `traverse`, `expand_relationships`, `get_org_structure` | 3 | "
                 "→ `explore_connections`. |",
           frozenset({"traverse", "expand_relationships", "get_org_structure"})),
    "r6": (BETA, "| `audit`, `validate_domain`, `summarize_structure`, "
                 "`dream_health_check`, `dream_health_state` | ~5 | → `graph_overview`",
           frozenset({"audit", "validate_domain", "summarize_structure",
                      "dream_health_check", "dream_health_state"})),
    "r6_aliases": (BETA, "narrow aliases absorbed by `graph_overview` — `taxonomy`, "
                         "`list_pointkinds`, `list_tags`, `list_namespaces`, "
                         "`list_graphs`, `status`, `stale`, `check_structure`, "
                         "`list_topics` | **Deleted, not folded.**",
                   frozenset({"taxonomy", "list_pointkinds", "list_tags",
                              "list_namespaces", "list_graphs", "status", "stale",
                              "check_structure", "list_topics"})),
    "r6_test_guard": (BETA, "| `test_guard` | **Kept and relocated.**",
                      frozenset({"test_guard"})),
    "r6_canon": (CANON, "| R6 | `graph_overview` | #6 | `status`, `taxonomy`, "
                        "`list_pointkinds`, `list_sources`, `list_tags`, "
                        "`list_namespaces`, `list_relations`",
                 frozenset({"list_relations"})),
    "r6_list_sources": (BETA, "It folds into **row 4 `list_knowledge(kind='source')`**",
                        frozenset({"list_sources"})),
    "r7": (BETA, "| `review_connections`, `get_cross_lens_candidates`, "
                 "`list_dedup_candidates` | 3 | → `review_link_candidates`. |",
           frozenset({"review_connections", "get_cross_lens_candidates",
                      "list_dedup_candidates"})),
    "r8": (BETA, "| `events_poll` | → row 11 `poll_events`. |",
           frozenset({"events_poll"})),
    "r9": (BETA, "| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`.",
           frozenset({"list_batch", "list_batches"})),
    # ── WRITE ───────────────────────────────────────────────────────
    "w1": (BETA, "| `create_subject`, `create_object`, `create_event`, "
                 "`create_document`, `create_point` | 5 | Collapsed into "
                 "`create_entity(type=)`.",
           frozenset({"create_subject", "create_object", "create_event",
                      "create_document", "create_point"})),
    "w1_coup": (CANON, "| `create_or_update_point` → `create_point` |",
                frozenset({"create_or_update_point"})),
    "w1_batch": (BETA, "| `batch_create_points` | 1 | → `write_knowledge_batch`. |",
                 frozenset({"batch_create_points"})),
    "w2": (CANON, "| W2 | `write_knowledge` | — | `ingest` |", frozenset({"ingest"})),
    "w2_rename": (CANON, "`write_knowledge` and `stabilize_beliefs` where the current "
                         "target says", frozenset()),
    "w3": (CANON, "| W3 | `register_source` | #11 | `create_source`, `complete_source` |",
           frozenset({"create_source", "complete_source"})),
    "w3_cut": (BETA, "| `complete_source` | 1 | **Cut.**",
               frozenset({"complete_source"})),
    "w4": (BETA, "| `ingest_corpus`, `index_file`, `session_index_health` | 3 | "
                 "→ `index_sources_from_directory`. |",
           frozenset({"ingest_corpus", "index_file", "session_index_health"})),
    "w4_rename": (BETA, "| `index_sources` (bare) | 1 | Renamed → "
                        "`index_sources_from_directory`", frozenset()),
    "w4_canon": (CANON, "| W4 | `index_files` | #12 | `index_file`, `index_directory`",
                 frozenset({"index_directory"})),
    "w4_mine": (BETA, "| `mine_corpus` | 1 | → `mine_knowledge_from_directory`.",
                frozenset({"mine_corpus"})),
    "w4_index_sessions": (CANON, "| `index_sessions` / `ingest_corpus` → "
                                 "`index_directory` |",
                          frozenset({"index_sessions", "ingest_corpus"})),
    "w5": (BETA, "**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.",
           frozenset({"checkpoint", "diary_write", "diary_read"})),
    "w6": (BETA, "| `capture_session` / `commit_session` | → row 16 "
                 "`mine_knowledge_from_session`, one method.",
           frozenset({"capture_session", "commit_session"})),
    "w8": (BETA, "| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | "
                 "→ `manage_source_trust`",
           frozenset({"assess_source", "set_source_tier", "get_source_reliability"})),
    "w8_backfill": (BETA, "| `backfill_v25`, `backfill_sources`, "
                          "`backfill_about_entities`, `reconcile_sessions` | 4 | "
                          "One-shot migrations.",
                    frozenset({"backfill_v25", "backfill_sources",
                               "backfill_about_entities", "reconcile_sessions"})),
    "w9": (BETA, "| `create_operator`, `create_direct_edge`, `create_derivation`, "
                 "`link_source_to_entity` | 4 | → `link_entities`",
           frozenset({"create_operator", "create_direct_edge", "create_derivation",
                      "link_source_to_entity"})),
    "w9_canon": (CANON, "| W9 | `link_entities` | #15 | `create_edge`,",
                 frozenset({"create_edge"})),
    "w10": (BETA, "| `file_human_approval` | 1 | → `record_decision`. |",
            frozenset({"file_human_approval"})),
    "w10_file_decision": (BETA, "| `file_decision` | → rows 20/21 **`write_question`** "
                                "+ **`record_decision`**.",
                          frozenset({"file_decision"})),
    "w11": (BETA, "| `update_point`, `update_entity` | 2 | → `update_knowledge`. |",
            frozenset({"update_point", "update_entity"})),
    "w11_canon": (CANON, "| W11 | `revise_knowledge` | #17 | `update`,",
                  frozenset({"update"})),
    "w11_supersede": (BETA, "| `supersede`, `supersede_point` | 2 | "
                            "→ `supersede_knowledge`.",
                      frozenset({"supersede", "supersede_point"})),
    "w11_retract": (BETA, "| `retract_point`, `invalidate_point` | 2 | → fields on "
                          "`update_knowledge`.",
                    frozenset({"retract_point", "invalidate_point"})),
    "w11_lifecycle": (BETA, "| `promote_point`, `set_point_baseline`, `list_drafts`, "
                            "`quarantine_batch` | 4 | Lifecycle and confidence wrangling",
                      frozenset({"promote_point", "set_point_baseline", "list_drafts",
                                 "quarantine_batch"})),
    "w12": (BETA, "| `delete_point`, `delete_point_wrapped` | 2 | "
                  "→ `delete_knowledge`. |",
            frozenset({"delete_point", "delete_point_wrapped"})),
    "w12_canon": (CANON, "| W12 | `delete_knowledge` | #18 | `delete`,",
                  frozenset({"delete", "delete_entity"})),
    "w13_canon": (CANON, "| W13 | `stabilize_beliefs` | #19 | `dream`, "
                        "`compute_confidence`, `compute_reputation`, "
                        "`record_calibration` |",
                  frozenset({"dream", "compute_confidence", "compute_reputation",
                             "record_calibration"})),
    "w15": (BETA, "| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | "
                  "→ `adjust_relationship`",
            frozenset({"mitigate_operator", "operator_action", "annotate_operator"})),
    "w17_ulid": (BETA, "| `ulid` | 1 | A ULID generator. Not a memory operation. |",
                 frozenset({"ulid"})),
    "unchanged4": (BETA, "current SDK (`create_entity`, `get_entity`, `approve_merge`, "
                         "`close`)",
                   frozenset({"create_entity", "get_entity", "approve_merge", "close"})),
    # ── Control plane ───────────────────────────────────────────────
    "n1_console": (BETA, "| `org_update`, `org_delete`, `membership_get`, "
                         "`membership_update_role`, `apikey_verify` | 5 | "
                         "Console plumbing.",
                   frozenset({"org_update", "org_delete", "membership_get",
                              "membership_update_role", "apikey_verify"})),
    "n1_account": (BETA, "| 28 | `get_organisation_account` | Read the account and the "
                         "plan it is on |", frozenset()),
    "n2_rename": (BETA, "| `graph_delete`, `graph_restore`, `graph_list`, "
                        "`graph_set_name` | → rows 30–33 `*_memory_graph*`. |",
                  frozenset({"graph_delete", "graph_restore", "graph_list",
                             "graph_set_name"})),
    "n2_keys": (BETA, "| `graph_key_ids`, `graph_active_key_count` | 2 | Console "
                      "diagnostics. Both fold into `list_keys`. |",
                frozenset({"graph_key_ids", "graph_active_key_count"})),
    "n2_recording": (BETA, "The override therefore folds into **`update_memory_graph`**",
                     frozenset({"graph_set_recording"})),
    "n2_count": (BETA, "`list_memory_graphs` answers \"how many\" for any real N.",
                 frozenset()),
    "maintenance": (BETA, "| `trash_graphs`, `migrate_orgs_to_registry`, "
                          "`cleanup_expired_invitations`, "
                          "`sweep_invite_ghost_memberships` | 4 | **Our maintenance.**",
                    frozenset({"trash_graphs", "migrate_orgs_to_registry",
                               "cleanup_expired_invitations",
                               "sweep_invite_ghost_memberships"})),
    "n3_members": (BETA, "| 37 | `add_member` | Grant a person access to the account |",
                   frozenset()),
    "n4_keys": (BETA, "| 34 | `create_key` | Mint a credential scoped to one memory "
                      "graph.", frozenset()),
    "n5_invite": (BETA, "| `invitation_*` (6) | 6 | The invite **UX** belongs to the "
                        "console, where a human clicks it. |", frozenset()),
    "n6_signup": (BETA, "| `signup_token_*` (3) | 3 | Operator-side agent self-signup",
                  frozenset()),
}

# ─────────────────────────────────────────────────────────────────────
# THE MAP. Group default first (the collapse the canonical inventory
# states), then per-method exceptions. Every value is either a name in
# the 40-method target surface parsed from the beta doc, or one of
# UNCHANGED / DISCARDED / UNBACKED.
# ─────────────────────────────────────────────────────────────────────
GROUP_TARGET: dict[str, tuple[str, str]] = {
    "R1": ("search_knowledge", "r1"),
    "R2": ("list_knowledge", "r2"),
    "R3": ("check_confidence", "r3"),
    "R4": ("get_entity", "r4"),
    "R5": ("explore_connections", "r5"),
    "R6": ("graph_overview", "r6"),
    "R7": ("review_link_candidates", "r7"),
    "R8": ("poll_events", "r8"),
    "R9": ("list_knowledge", "r9"),
    "W1": ("create_entity", "w1"),
    "W2": ("write_knowledge_batch", "w2"),
    "W3": ("register_source", "w3"),
    "W4": ("index_sources_from_directory", "w4"),
    "W5": (DISCARDED, "w5"),
    "W6": ("mine_knowledge_from_session", "w6"),
    "W7": ("mine_knowledge_from_session", "w6"),
    "W8": ("manage_source_trust", "w8"),
    "W9": ("link_entities", "w9"),
    "W10": ("record_decision", "w10"),
    "W11": ("update_knowledge", "w11"),
    "W12": ("delete_knowledge", "w12"),
    # The canonical group `W13 stabilize_beliefs` is renamed to the beta target
    # `refresh_confidence` — canonical-sdk-methods.md records exactly that rename.
    "W13": ("refresh_confidence", "w13_canon"),
    "W14": (UNCHANGED, "unchanged4"),
    "W15": ("adjust_relationship", "w15"),
    "W17": (DISCARDED, "w17_ulid"),
    "N1": (DISCARDED, "n1_console"),
    "N2": (DISCARDED, "n2_rename"),
    "N3": (DISCARDED, "n3_members"),
    "N4": (DISCARDED, "n4_keys"),
    "N5": (DISCARDED, "n5_invite"),
    "N6": (DISCARDED, "n6_signup"),
    "ARCHIVE": (DISCARDED, "w8_backfill"),
}

OVERRIDE: dict[str, tuple[str, str]] = {
    # R1 — the one member the beta doc's collapse row does not name.
    "tortoise_fts_query": ("search_knowledge", "r1_canon"),
    # R3 — three members leave the group default.
    "restore_point_at": ("get_historical_knowledge", "r3_restore"),
    "recall_subgraph": (DISCARDED, "r3_drop_subgraph"),
    "provenance": ("check_confidence", "r3_context"),
    "belief_timeline": ("check_confidence", "r3_context"),
    "session_context": ("check_confidence", "r3_context"),
    "volunteer_context": ("check_confidence", "r3_context"),
    "get_confidence": ("check_confidence", "r3_canon"),
    "retrieval_legs": ("check_confidence", "r3_canon"),
    # The beta doc names get_provenance_chain as a narrow reader; the canonical
    # inventory groups it with the recall/confidence reads. See the tension list.
    "get_provenance_chain": ("get_entity", "r4"),
    # R4 — the one member that is already a target, and two the beta row omits.
    "get_entity": (UNCHANGED, "unchanged4"),
    "get_point": ("get_entity", "r4_canon"),
    "resolve_id": ("get_entity", "r4_canon"),
    # R5 — the beta doc names get_owned_entities as a narrow reader; the canonical
    # inventory calls it a governance/traversal member. See the tension list.
    "get_owned_entities": ("get_entity", "r4"),
    # R6 — aliases, and the members the beta doc treats individually.
    "list_relations": ("graph_overview", "r6_canon"),
    "test_guard": (DISCARDED, "r6_test_guard"),
    "taxonomy": ("graph_overview", "r6_aliases"),
    "list_pointkinds": ("graph_overview", "r6_aliases"),
    "list_tags": ("graph_overview", "r6_aliases"),
    "list_namespaces": ("graph_overview", "r6_aliases"),
    "list_graphs": ("graph_overview", "r6_aliases"),
    "status": ("graph_overview", "r6_aliases"),
    "stale_points": ("graph_overview", "r6_aliases"),
    "check_structure": ("graph_overview", "r6_aliases"),
    "list_topics": ("graph_overview", "r6_aliases"),
    "list_sources": ("list_knowledge", "r6_list_sources"),
    # W1
    "create_entity": (UNCHANGED, "unchanged4"),
    "create_or_update_point": ("create_entity", "w1_coup"),
    "batch_create_points": ("write_knowledge_batch", "w1_batch"),
    # W3
    "complete_source": (DISCARDED, "w3_cut"),
    # W4
    "mine_corpus": ("mine_knowledge_from_directory", "w4_mine"),
    "index_directory": ("index_sources_from_directory", "w4_canon"),
    "index_sessions": ("index_sources_from_directory", "w4_index_sessions"),
    "backfill_about_entities": (DISCARDED, "w8_backfill"),
    "reconcile_sessions": (DISCARDED, "w8_backfill"),
    # W8
    "backfill_sources": (DISCARDED, "w8_backfill"),
    # W9 — the one member the beta collapse row does not name.
    "create_edge": ("link_entities", "w9_canon"),
    # W10
    "file_decision": ("write_question", "w10_file_decision"),
    # W11
    "update": ("update_knowledge", "w11_canon"),
    "retract_point": ("update_knowledge", "w11_retract"),
    "invalidate_point": ("update_knowledge", "w11_retract"),
    "supersede": ("supersede_knowledge", "w11_supersede"),
    "supersede_point": ("supersede_knowledge", "w11_supersede"),
    "promote_point": (DISCARDED, "w11_lifecycle"),
    "set_point_baseline": (DISCARDED, "w11_lifecycle"),
    "list_drafts": (DISCARDED, "w11_lifecycle"),
    "quarantine_batch": (DISCARDED, "w11_lifecycle"),
    # W12 — two members the beta row omits.
    "delete": ("delete_knowledge", "w12_canon"),
    "delete_entity": ("delete_knowledge", "w12_canon"),
    # W13 — the canonical group names all four members; two of them have no
    # plausible place on the 40-method target surface, so they are findings.
    "dream": ("refresh_confidence", "w13_canon"),
    "compute_confidence": ("refresh_confidence", "w13_canon"),
    "compute_reputation": (UNBACKED, ""),
    "record_calibration": (UNBACKED, ""),
    # W17
    "close": (UNCHANGED, "unchanged4"),
    # N1 — the five the beta doc files as console plumbing.
    "org_update": (DISCARDED, "n1_console"),
    "org_delete": (DISCARDED, "n1_console"),
    "org_create": (UNBACKED, ""),
    "org_get": ("get_organisation_account", "n1_account"),
    "org_list": ("get_organisation_account", "n1_account"),
    "migrate_orgs_to_registry": (DISCARDED, "maintenance"),
    # N2
    "graph_delete": ("delete_memory_graph", "n2_rename"),
    "graph_restore": ("restore_memory_graph", "n2_rename"),
    "graph_list": ("list_memory_graphs", "n2_rename"),
    "graph_set_name": ("update_memory_graph", "n2_rename"),
    "graph_set_recording": ("update_memory_graph", "n2_recording"),
    "graph_key_ids": ("list_keys", "n2_keys"),
    "graph_active_key_count": ("list_keys", "n2_keys"),
    "graph_count": ("list_memory_graphs", "n2_count"),
    "trash_graphs": (DISCARDED, "maintenance"),
    # N3
    "membership_create": ("add_member", "n3_members"),
    "membership_list": ("list_members", "n3_members"),
    "membership_delete": ("remove_member", "n3_members"),
    "membership_get": (DISCARDED, "n1_console"),
    "membership_update_role": (DISCARDED, "n1_console"),
    # N4
    "apikey_create": ("create_key", "n4_keys"),
    "apikey_list": ("list_keys", "n4_keys"),
    "apikey_revoke": ("revoke_key", "n4_keys"),
    "apikey_verify": (DISCARDED, "n1_console"),
    # N5 — the two that are maintenance, not invite UX.
    "cleanup_expired_invitations": (DISCARDED, "maintenance"),
    "sweep_invite_ghost_memberships": (DISCARDED, "maintenance"),
}

# ─────────────────────────────────────────────────────────────────────
# CROSS-DOC TENSIONS. Two docs name different destinations for the same
# method. The beta doc governs the target NAMES (it is the owner-approved
# surface, and the canonical doc says so itself), so the row carries the
# beta destination — but the conflict is a finding, listed in Part C3.
# ─────────────────────────────────────────────────────────────────────
TENSIONS: list[tuple[str, str, str]] = [
    # (method, the destination the OTHER doc implies, citation showing it)
    ("get_owned_entities", "`explore_connections`", "t_r5"),
    ("get_provenance_chain", "`check_confidence`", "t_r3"),
    ("restore_point_at", "`check_confidence`", "t_r3"),
    ("list_sources", "`graph_overview`", "t_r6"),
    ("test_guard", "`graph_overview`", "t_r6"),
    ("graph_set_recording", "kept, inside the control-plane block", "t_n2"),
]
TENSION_CITES: dict[str, tuple[str, str, frozenset[str]]] = {
    "t_r3": (CANON, "| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, "
                    "`recall_subgraph`, `retrieval_legs`, `volunteer_context`, "
                    "`session_context`, `get_confidence`, `calibrate_summary`, "
                    "`calibration_passed`, `get_provenance_chain`, `provenance`, "
                    "`belief_timeline`, `restore_point_at` |", frozenset()),
    "t_r5": (CANON, "| R5 | `explore_connections` | #5 | `expand_relationships`, "
                    "`traverse`, `get_owned_entities`, `get_org_structure` |",
             frozenset()),
    "t_r6": (CANON, "| R6 | `graph_overview` | #6 | `status`, `taxonomy`, "
                    "`list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, "
                    "`list_relations`, `list_topics`, `list_graphs`, `stale_points`, "
                    "`summarize_structure`, `check_structure`, `audit`, "
                    "`validate_domain`, `dream_health_check`, `dream_health_state`, "
                    "`test_guard` |", frozenset()),
    "t_n2": (CANON, "| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, "
                    "`graph_restore`, `trash_graphs`, `graph_set_name`, "
                    "`graph_set_recording`, `graph_key_ids`, "
                    "`graph_active_key_count` |", frozenset()),
}

# ─────────────────────────────────────────────────────────────────────
# PHANTOM NAMES. A name the disposition docs use on the SDK side for which
# `TortoiseSDK` has no `def` — a doc→code name mismatch, and the reason a
# row's referent had to be inferred. `_validate` re-checks the "no def"
# half, so this list cannot go stale while passing.
# ─────────────────────────────────────────────────────────────────────
PHANTOMS: list[tuple[str, str, str]] = [
    # (name the doc uses, the real method it appears to mean, citation)
    ("recall_legs", "retrieval_legs", "r3"),
    ("stale", "stale_points", "r6_aliases"),
    ("count_memory_graphs", "graph_count", "p_graph_count"),
    ("set_memory_graph_name", "graph_set_name", "p_set_name"),
    ("set_memory_graph_backend", "", "p_set_name"),
    ("index_sources", "index_directory", "w4_rename"),
    ("withdraw_knowledge", "", "p_withdraw"),
]
PHANTOM_CITES: dict[str, tuple[str, str, frozenset[str]]] = {
    "p_graph_count": (BETA, "| `count_memory_graphs` | The plan is unlimited on builder "
                            "plans, so its stated purpose — checking an allowance — "
                            "does not exist. `list_memory_graphs` answers \"how many\" "
                            "for any real N. |", frozenset()),
    "p_set_name": (BETA, "| `set_memory_graph_name`, `set_memory_graph_backend`, "
                         "`count_memory_graphs` | 3 | See \"Provisioning\" above. |",
                   frozenset()),
    "p_withdraw": (BETA, "| `withdraw_knowledge` | 1 | **Never existed**",
                   frozenset()),
}


# ─────────────────────────────────────────────────────────────────────
# DERIVATION
# ─────────────────────────────────────────────────────────────────────
def _public_methods() -> dict[str, int]:
    """Public method name → `def` line, from the Phase 0.1 AST walker."""
    return {n: ln for n, ln in _sdk_targets().items() if not n.startswith("_")}


def _targets(doc_text: str) -> list[str]:
    """The 40 target method names, read from the beta doc's surface table.

    The table is `| # | Name | Does | MCP twin | Who needs it |`. Rows are located
    by their NUMERIC first cell, so a reordered or reformatted table cannot make
    this parse silently return the wrong column. The row numbers must be exactly
    1..40 — a gap or a duplicate is a build failure, not a quietly shorter list.
    """
    found: dict[int, str] = {}
    for line in doc_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        # `Tortoise(...)` is the constructor and `close()` carries its parens; the
        # target NAME is what the SDK exposes.
        name = cells[1].strip("`").strip()
        name = re.sub(r"\(.*\)$", "", name)
        if name:
            found[int(cells[0])] = name
    return [found[i] for i in sorted(found)]


def _groups(canon_text: str) -> dict[str, list[str]]:
    """The canonical inventory partition: group label → member method names.

    Scoped to the `## The groups (the earlier sketch)` section on purpose. The doc
    reuses the labels R1–R9 / W1–W19 for a SECOND, differently-shaped table
    ("What we have that competitors do not"); parsing the whole file would read the
    wrong rows and would silently overwrite the real groups.
    """
    start = canon_text.index("## The groups (the earlier sketch)")
    end = canon_text.index("## What each group does")
    out: dict[str, list[str]] = {}
    for line in canon_text[start:end].splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        label = cells[0]
        if not re.fullmatch(r"[RW]\d+|N\d+", label):
            continue
        idx = 2 if label.startswith("N") else 3
        members = re.findall(r"`([a-z_][a-z0-9_]*)`", cells[idx]) if len(cells) > idx else []
        out[label] = members
    return out


def _rows(methods: dict[str, int], groups: dict[str, list[str]]) -> list[dict]:
    """One migration row per public method, with a computed `basis`."""
    group_of: dict[str, str] = {}
    for label, members in groups.items():
        for m in members:
            group_of[m] = label
    for m in methods:
        group_of.setdefault(m, "ARCHIVE")  # `backfill_v25` lives in the Archived table

    rows = []
    for name in sorted(methods):
        label = group_of[name]
        if name in OVERRIDE:
            target, key = OVERRIDE[name]
        else:
            target, key = GROUP_TARGET[label]
        if not key:
            basis = "unbacked"
            quote = ""
            doc = ""
        else:
            doc, quote, named = CITES[key]
            basis = "stated" if name in named else "derived"
        rows.append({
            "name": name,
            "line": methods[name],
            "group": label,
            "target": target,
            "basis": basis,
            "doc": doc,
            "quote": quote,
        })
    return rows


def _findings(rows: list[dict], methods: dict[str, int], targets: list[str],
              groups: dict[str, list[str]]) -> dict:
    """The computed findings this artifact exists to produce."""
    by_target: dict[str, list[str]] = {}
    for r in rows:
        by_target.setdefault(r["target"], []).append(r["name"])
    no_def = [t for t in targets if t not in methods]
    return {
        "by_target": by_target,
        "no_def": no_def,
        "no_def_set": set(no_def),
        "unbacked": sorted(r["name"] for r in rows if r["target"] == UNBACKED),
        "tensions": TENSIONS,
        "phantoms": PHANTOMS,
        "unfilled": [g for g, m in groups.items() if not m],
    }


# ─────────────────────────────────────────────────────────────────────
# VALIDATION — fail loudly, never reconcile
# ─────────────────────────────────────────────────────────────────────
def _validate(methods: dict[str, int], groups: dict[str, list[str]],
              targets: list[str], cites: dict) -> list[str]:
    errs: list[str] = []

    # 1. The canonical partition must cover the public surface exactly.
    partitioned = [m for members in groups.values() for m in members]
    dupes = {m for m in partitioned if partitioned.count(m) > 1}
    for m in sorted(dupes):
        errs.append(f"DUPLICATE group membership in the canonical doc: {m}")
    grouped = set(partitioned) | {"backfill_v25"}  # the Archived table
    for m in sorted(set(methods) - grouped):
        errs.append(f"UNMAPPED public method (no canonical group, no row): {m}")
    for m in sorted(grouped - set(methods)):
        errs.append(f"STALE canonical member (not a public `def` on TortoiseSDK): {m}")

    # 2. Every public method must have a disposition row, and no row may be stale.
    mapped = set(OVERRIDE) | grouped
    for m in sorted(set(methods) - mapped):
        errs.append(f"UNMAPPED public method (no disposition): {m}")
    for m in sorted(set(OVERRIDE) - set(methods)):
        errs.append(f"STALE map key (not a public method): {m}")

    # 3. Every target must be a real target name, or a recognised non-target.
    known = set(targets) | {UNCHANGED, DISCARDED, UNBACKED}
    for m in sorted(methods):
        target = OVERRIDE.get(m, (None, None))[0]
        if target is None:
            label = next((g for g, ms in groups.items() if m in ms), "ARCHIVE")
            target = GROUP_TARGET.get(label, (None, None))[0]
        if target not in known:
            errs.append(f"UNRECOGNISED destination for {m}: {target!r}")

    # 4. A citation is only real if the doc still says it.
    for key, (doc, quote, _named) in cites.items():
        text = (BETA_DOC if doc == BETA else CANON_DOC).read_text(encoding="utf-8")
        if quote not in text:
            errs.append(f"CITATION DRIFT: {key} quotes {doc} but that text is gone: "
                        f"{quote[:70]!r}")

    # 5. A phantom must NOT have a def — that is the whole finding.
    for name, _referent, _key in PHANTOMS:
        if name in methods:
            errs.append(f"PHANTOM RESOLVED: {name} now has a `def` on TortoiseSDK — "
                        f"remove it from PHANTOMS (it is no longer a doc/code mismatch)")

    # 6. The target surface is 40 methods. A count that moves is a finding.
    if len(targets) != 40:
        errs.append(f"TARGET SURFACE: parsed {len(targets)} target methods from "
                    f"{BETA}, expected 40")
    return errs


def _cell(text: str) -> str:
    """A markdown table cell: escape pipes so a quoted row cannot split the table."""
    return text.replace("|", "\\|").replace("\n", " ")


def render(rows: list[dict], targets: list[str], groups: dict[str, list[str]],
           findings: dict) -> str:
    n = len(rows)
    unchanged = sum(1 for r in rows if r["target"] == UNCHANGED)
    discarded = sum(1 for r in rows if r["target"] == DISCARDED)
    unbacked = sum(1 for r in rows if r["target"] == UNBACKED)
    migrated = n - unchanged - discarded - unbacked
    no_def = findings["no_def"]
    empty_groups = findings["unfilled"]

    out = [
        "# Phase 0.3b — the SDK rename table",
        "",
        "**GENERATED — do not edit.** `uv run python tools/sdk_rename_table.py`; "
        "verify with `--check`.",
        "",
        "Every `sdk.py:N` citation is **read from the AST at build time**, so it cannot "
        "drift from the code it cites. The 40 target names are **parsed out of "
        "`docs/product/beta-sdk-surface.md`** (owner-approved 2026-09-21), and the "
        "R/W/N group partition out of `docs/product/canonical-sdk-methods.md`; every "
        "count below is arithmetic over those, never a typed number. Each row's citation "
        "quote is **verified to still be in the doc it names** — a citation that no "
        "longer resolves fails the build.",
        "",
        "**This is the SDK half of the rename table.** The MCP half (current tool → "
        "target tool) is `docs/product/bridge-table.md` (Phase 0.1), plus a sibling "
        "Phase 0.3b lane; nothing here restates it.",
        "",
        f"**The surface: {n} public methods on `TortoiseSDK` → {len(targets)} target "
        f"methods.** {migrated} of the {n} are renames to a target; **{unchanged}** are "
        f"already targets (unchanged); **{discarded}** are discarded with a rationale; "
        f"and **{unbacked}** have no destination anywhere on the target "
        "surface — those are findings, not rows to be guessed at.",
        "",
        f"**These are not the same number.** The target has {len(targets)} methods; only "
        f"**{len(targets) - len(no_def)}** of them exist on `TortoiseSDK` today. The other "
        f"**{len(no_def)}** are Phase 2 work, listed in Part C1.",
        "",
        "---",
        "",
        "## Part A — every public method and its migration row",
        "",
        "`Basis` says how strongly the row is backed: **stated** — the cited quote names "
        "this method and gives its collapse, rename or deletion; **derived** — a doc gives "
        "the destination only for a namespace or wildcard covering this method, without "
        "naming it; **unbacked** — no doc gives a destination.",
        "",
        "The canonical inventory's group names are an **earlier sketch** "
        "(`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). The "
        "`Target` column always carries the **beta** target name (`update_knowledge`, "
        "`refresh_confidence`, `write_knowledge_batch`, "
        "`index_sources_from_directory`) — the canonical doc itself says beta governs where "
        "the two disagree, and records the renames.",
        "",
        "| # | Method | Source | Group | Target | Basis | Citation |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        if r["basis"] == "unbacked":
            cite = "**no doc states a destination**"
        else:
            cite = f"`{r['doc']}` — “{_cell(r['quote'])}”"
        out.append(
            f"| {i} | `{r['name']}` | `sdk.py:{r['line']}` | {r['group']} | "
            f"`{r['target']}` | {r['basis']} | {cite} |"
        )

    out += [
        "",
        "## Part B — destination counts",
        "",
        "| Destination | Current methods | Count |",
        "|---|---|---|",
    ]
    by_target = findings["by_target"]
    for target in sorted(by_target, key=lambda t: (-len(by_target[t]), t)):
        names = ", ".join(f"`{m}`" for m in by_target[target])
        out.append(f"| `{target}` | {names} | {len(by_target[target])} |")
    replaced = sum(1 for t in by_target if t in findings["no_def_set"])
    real_targets = len(by_target) - replaced - _non_target_destinations(by_target)
    out += [
        f"| **total** | — | **{n}** |",
        "",
        f"Distinct destinations: **{len(by_target)}** — **{replaced}** are target methods "
        f"with no `def` today (Phase 2 work, Part C1), **{real_targets}** are target "
        "methods that already exist (`create_entity`, `get_entity`), and "
        f"**{_non_target_destinations(by_target)}** are the non-target dispositions "
        "(`UNCHANGED` / `DISCARDED` / `UNBACKED`).",
        "",
        "## Part C — findings",
        "",
        "### C1 — target methods with no `def` on `TortoiseSDK`",
        "",
        "A rename whose destination does not exist yet is **Phase 2 work**, not a rename.",
        "The plan listed the whole table as renames; this is what Part A exists to catch.",
        "",
    ]
    if no_def:
        out += ["| Target method | Status |", "|---|---|"]
        out += [f"| `{t}` | no `def` on `TortoiseSDK` today |" for t in no_def]
        out.append("")
        out.append("`Tortoise` is row 1 of the target table (the constructor), not a "
                   "method; the class today is `TortoiseSDK`, so the approved surface "
                   "also renames the type.")
    else:
        out.append("None — every target method has a `def`.")

    out += [
        "",
        "### C2 — rows with NO doc backing",
        "",
        "No document states a destination for these; the row is an open question, not an",
        "answer. Each needs an owner ruling before Phase 2 implements it.",
        "",
    ]
    if findings["unbacked"]:
        out += ["| Method | Source | Why it has no destination |", "|---|---|---|"]
        out += [
            f"| `{m}` | `sdk.py:{next(r['line'] for r in rows if r['name'] == m)}` | "
            f"{_cell(UNBACKED_REASON.get(m, 'no doc states a destination'))} |"
            for m in findings["unbacked"]
        ]
        out.append("")
        out.append("**An unbacked row is a finding, not a gap to fill by analogy.** "
                   "Rolling these into a nearby target would silently drop a capability "
                   "the surface has today.")
    else:
        out.append("None.")

    out += [
        "",
        "### C3 — cross-doc tensions",
        "",
        "Two docs name **different** destinations for the same method. The row in Part A",
        "carries the `beta-sdk-surface.md` destination, because that is the owner-approved",
        "surface and the canonical inventory itself says so (\"Where the two disagree, that",
        "doc governs\"). The conflict is recorded here rather than resolved silently.",
        "",
    ]
    if findings["tensions"]:
        out += ["| Method | Part A carries | The other doc implies | Other doc's grouping |",
                "|---|---|---|---|"]
        for method, other, key in findings["tensions"]:
            doc, quote, _ = TENSION_CITES[key]
            out.append(f"| `{method}` | `{OVERRIDE[method][0]}` | {other} | "
                       f"`{doc}` — “{_cell(quote)}” |")
    else:
        out.append("None.")

    out += [
        "",
        "### C4 — names the disposition docs use that are NOT SDK methods",
        "",
        "A doc→code name mismatch. Where a referent is named, the Part A row for that",
        "referent cites the doc under its *doc* name; where no referent exists, the",
        "doc's statement is about a method that was never there.",
        "",
        "| Doc's name | Real method (if any) | Where the doc uses it |",
        "|---|---|---|",
    ]
    for name, referent, key in findings["phantoms"]:
        doc, quote, _ = PHANTOM_CITES.get(key) or CITES[key]
        ref = f"`{referent}`" if referent else "**none**"
        out.append(f"| `{name}` | {ref} | `{doc}` — “{_cell(quote)}” |")

    if empty_groups:
        out += [
            "",
            "### C5 — canonical groups with no distinct member",
            "",
            "The inventory's group table has group labels whose members are expressed as",
            "wildcards (`org_*`, `graph_*`, …) that expand to the same methods as the",
            "control-plane families. They carry no distinct member, so the partition here",
            "resolves each method to its family group: "
            + ", ".join(f"`{g}`" for g in empty_groups) + ".",
        ]

    out += [
        "",
        "### Structural notes",
        "",
        "- The canonical inventory partitions the surface into **"
        f"{len([g for g in groups if not g.startswith('ARCHIVE')])} groups** over "
        f"**{sum(len(m) for m in groups.values())}** named members; `backfill_v25` is in "
        "its Archived table instead. Total: "
        f"{sum(len(m) for m in groups.values()) + 1} = the {n}-method surface.",
        "- Every group collapse above is checked against the AST walk at build time: a "
        "method the docs know and the code does not (or the reverse) **fails the build**.",
        "",
        "---",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python tools/sdk_rename_table.py          # regenerate this file",
        "uv run python tools/sdk_rename_table.py --check  # verify, non-zero on drift",
        "```",
        "",
    ]
    return "\n".join(out)


def _non_target_destinations(by_target: dict[str, list[str]]) -> int:
    """Destinations that are not a target name at all (UNCHANGED/DISCARDED/UNBACKED)."""
    return sum(1 for t in by_target if t in (UNCHANGED, DISCARDED, UNBACKED))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; do not write")
    ap.add_argument("--out", type=Path, default=OUT,
                    help="override the output path (used by the drift test)")
    args = ap.parse_args(argv)

    methods = _public_methods()
    groups = _groups(CANON_DOC.read_text(encoding="utf-8"))
    targets = _targets(BETA_DOC.read_text(encoding="utf-8"))

    cites = dict(CITES)
    cites.update(TENSION_CITES)
    cites.update(PHANTOM_CITES)
    errs = _validate(methods, groups, targets, cites)
    if errs:
        print("SDK RENAME TABLE BUILD FAILURE — the map and the SDK disagree:",
              file=sys.stderr)
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        return 1

    rows = _rows(methods, groups)
    findings = _findings(rows, methods, targets, groups)
    doc = render(rows, targets, groups, findings)

    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != doc:
            print(f"DRIFT: {args.out} is stale. "
                  f"Run: uv run python tools/sdk_rename_table.py", file=sys.stderr)
            return 1
        print(f"OK: {args.out} is current ({len(rows)} public methods).")
        return 0

    args.out.write_text(doc, encoding="utf-8")
    print(f"wrote {args.out}")
    print(f"  public methods: {len(rows)}  targets: {len(targets)}")
    print(f"  unchanged: {sum(1 for r in rows if r['target'] == UNCHANGED)}  "
          f"discarded: {sum(1 for r in rows if r['target'] == DISCARDED)}  "
          f"unbacked: {len(findings['unbacked'])}")
    print(f"  targets with NO def on TortoiseSDK ({len(findings['no_def'])}): "
          f"{', '.join(findings['no_def'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
