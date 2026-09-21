"""The SDK rename table must not drift from the code and docs it describes (#4282).

`tools/sdk_rename_table.py` reads every `sdk.py:N` straight from the AST, parses the
40 target names out of `docs/product/beta-sdk-surface.md`, and parses the R/W/N group
partition out of `docs/product/canonical-sdk-methods.md`. What it can still do is go
*stale* — someone edits `sdk.py` or either doc and never regenerates — which is exactly
the drift the generator's own docstring promises cannot happen. This is the enforcement.

What this file deliberately does NOT do is import the generator's data and compare it to
itself. The sibling Phase 0.1 PR was red-green for rounds because a test did
`from tools.bridge_table import C1_FINDING` and then asserted the rendered doc equalled
that imported constant: both sides moved together, so the assertion proved nothing. Here
every expected value is either a **literal** or derived from an **independent oracle** —
a fresh AST walk, or a fresh parse of the source doc.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an unregistered
test file; `test_bridge_table.py` is the precedent for the dual `api` + `core` entry).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "sdk_rename_table.py"
DOC = ROOT / "docs" / "product" / "sdk-rename-table.md"
BETA = ROOT / "docs" / "product" / "beta-sdk-surface.md"
CANON = ROOT / "docs" / "product" / "canonical-sdk-methods.md"

# One Part A row: `| 1 | \`name\` | \`sdk.py:123\` | R1 | \`target\` | stated | citation |`.
# The citation cell is captured greedily to the final pipe so escaped (`\|`) pipes
# inside a quoted markdown row do not split it.
ROW_RE = re.compile(
    r"^\| \d+ \| `([a-z_][a-z0-9_]*)` \| `sdk\.py:(\d+)` \| ([A-Z]\d+|ARCHIVE) \| "
    r"`([^`]+)` \| (stated|derived|unbacked) \| (.*) \|$"
)
CITE_RE = re.compile(r"^`([a-z-]+\.md)` — “(.*)”$")


# ─────────────────────────────────────────────────────────────────────
# INDEPENDENT ORACLES — derived here, not imported from the generator
# ─────────────────────────────────────────────────────────────────────
def public_methods_independently() -> dict[str, int]:
    """Public method name -> `def` line, by walking `tortoise/sdk.py` here."""
    tree = ast.parse((ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                s.name: s.lineno
                for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not s.name.startswith("_")
            }
    raise AssertionError("no `class TortoiseSDK` found in tortoise/sdk.py")


def targets_independently() -> list[str]:
    """The 40 target names, parsed here from the beta doc's surface table."""
    out: dict[int, str] = {}
    for line in BETA.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].isdigit():
            continue
        out[int(cells[0])] = re.sub(r"\(.*\)$", "", cells[1].strip("`").strip())
    return [out[i] for i in sorted(out)]


def part_a_rows() -> list[dict]:
    rows = []
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append({
                "name": m.group(1), "line": int(m.group(2)), "group": m.group(3),
                "target": m.group(4), "basis": m.group(5), "cite": m.group(6),
            })
    return rows


# Every citation's QUOTE TEXT, keyed by citation key. Pinning only `{method: target}`
# left the evidence free: a row could keep its target and swap in ANY other real
# sentence from the same document and stay `stated`, because `basis` is computed from
# the authored `named` set rather than from the quote itself. Row 68 then quoted
# `test_guard`'s disposition while claiming `update_memory_graph` — suite green.
CITES_LITERAL: dict[str, str] = {
    'maintenance': '| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.**',
    'n1_account': '| 28 | `get_organisation_account` | Read the account and the plan it is on |',
    'n1_console': '| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing.',
    'n2_count': '`list_memory_graphs` answers "how many" for any real N.',
    'n2_keys': '| `graph_key_ids`, `graph_active_key_count` | 2 | Console diagnostics. Both fold into `list_keys`. |',
    'n2_recording': "| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. |",
    'n2_rename': '| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 `*_memory_graph*`. |',
    'n3_members': '| 37 | `add_member` | Grant a person access to the account |',
    'n4_keys': '| 34 | `create_key` | Mint a credential scoped to one memory graph.',
    'n5_invite': '| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. |',
    'n6_signup': '| `signup_token_*` (3) | 3 | Operator-side agent self-signup',
    'p_graph_count': '| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |',
    'p_set_name': '| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` | 3 | See "Provisioning" above. |',
    'p_withdraw': '| `withdraw_knowledge` | 1 | **Never existed**',
    'r1': '| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |',
    'r1_canon': '| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` |',
    'r2': '| `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |',
    'r3': '| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence`',
    'r3_canon': '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`',
    'r3_context': '| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `check_confidence`',
    'r3_drop_subgraph': '**`recall_subgraph` is dropped, not folded**',
    'r3_restore': '| `restore_point_at` | → row 7 **`get_historical_knowledge`**.',
    'r4': '| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`',
    'r4_canon': '| R4 | `get_entity` | #4 | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` |',
    'r5': '| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |',
    'r6': '| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview`',
    'r6_aliases': 'narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` | **Deleted, not folded.**',
    'r6_canon': '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`',
    'r6_list_sources': "| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |",
    'r6_test_guard': '| `test_guard` | **Kept and relocated.**',
    'r7': '| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |',
    'r8': '| `events_poll` | → row 11 `poll_events`. |',
    'r9': "| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`.",
    't_n2': '| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` |',
    't_r3': '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` |',
    't_r5': '| R5 | `explore_connections` | #5 | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` |',
    't_r6': '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` |',
    'unchanged4': 'current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)',
    'w1': '| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`.',
    'w10': '| `file_human_approval` | 1 | → `record_decision`. |',
    'w10_file_decision': '| `file_decision` | → rows 20/21 **`write_question`** + **`record_decision`**.',
    'w11': '| `update_point`, `update_entity` | 2 | → `update_knowledge`. |',
    'w11_canon': '| W11 | `revise_knowledge` | #17 | `update`,',
    'w11_lifecycle': '| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence wrangling',
    'w11_retract': '| `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`.',
    'w11_supersede': '| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`.',
    'w12': '| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |',
    'w12_canon': '| W12 | `delete_knowledge` | #18 | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | keep, collapse |',
    'w13_canon': '| W13 | `stabilize_beliefs` | #19 | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` |',
    'w15': '| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship`',
    'w17_ulid': '| `ulid` | 1 | A ULID generator. Not a memory operation. |',
    'w1_batch': '| `batch_create_points` | 1 | → `write_knowledge_batch`. |',
    'w1_coup': '| `create_or_update_point` → `create_point` |',
    'w2': '| W2 | `write_knowledge` | — | `ingest` |',
    'w2_rename': '`write_knowledge` and `stabilize_beliefs` where the current target says',
    'w3': '| W3 | `register_source` | #11 | `create_source`, `complete_source` |',
    'w3_cut': '| `complete_source` | 1 | **Cut.**',
    'w4': '| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources_from_directory`. |',
    'w4_canon': '| W4 | `index_files` | #12 | `index_file`, `index_directory`',
    'w4_index_sessions': '| `index_sessions` / `ingest_corpus` → `index_directory` |',
    'w4_mine': '| `mine_corpus` | 1 | → `mine_knowledge_from_directory`.',
    'w4_rename': '| `index_sources` (bare) | 1 | Renamed → `index_sources_from_directory`',
    'w5': '**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.',
    'w6': '| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, one method.',
    'w8': '| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust`',
    'w8_backfill': '| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations.',
    'w9': '| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`',
    'w9_canon': '| W9 | `link_entities` | #15 | `create_edge`,',
}

# The COMPLETE row → (target, citation text) binding, for all 150 rows. Pinning the
# quotes by citation KEY was not enough: nothing pinned WHICH key a row selected, and
# where two keys share a `named` member (`w3`/`w3_cut`, `w4`/`w4_index_sessions`) a
# key swap kept the target, the basis AND the pinned quote set intact — so row 23
# could cite the `register_source` grouping as evidence for a DISCARDED disposition.
# Binding the rendered citation text per row closes the evidence-swap in both
# directions.
ROW_CITE_LITERAL: dict[str, tuple[str, str]] = {
    "annotate_ask_hits": ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    "annotate_operator": ('adjust_relationship', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship`”'),
    "apikey_create": ('create_key', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph.”'),
    "apikey_list": ('list_keys', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph.”'),
    "apikey_revoke": ('revoke_key', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph.”'),
    "apikey_verify": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing.”'),
    "approve_merge": ('UNCHANGED', '`beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)”'),
    "assess_source": ('manage_source_trust', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust`”'),
    "audit": ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview`”'),
    "backfill_about_entities": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations.”'),
    "backfill_sources": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations.”'),
    "backfill_v25": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations.”'),
    "batch_create_points": ('write_knowledge_batch', '`beta-sdk-surface.md` — “\\| `batch_create_points` \\| 1 \\| → `write_knowledge_batch`. \\|”'),
    "belief_timeline": ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence`”'),
    "calibrate_summary": ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence`”'),
    "calibration_passed": ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence`”'),
    "capture_session": ('mine_knowledge_from_session', '`beta-sdk-surface.md` — “\\| `capture_session` / `commit_session` \\| → row 16 `mine_knowledge_from_session`, one method.”'),
    "check_structure": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "checkpoint": ('DISCARDED', '`beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.”'),
    "cleanup_expired_invitations": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.**”'),
    "close": ('UNCHANGED', '`beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)”'),
    "commit_session": ('mine_knowledge_from_session', '`beta-sdk-surface.md` — “\\| `capture_session` / `commit_session` \\| → row 16 `mine_knowledge_from_session`, one method.”'),
    "complete_source": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `complete_source` \\| 1 \\| **Cut.**”'),
    "compute_confidence": ('refresh_confidence', '`canonical-sdk-methods.md` — “\\| W13 \\| `stabilize_beliefs` \\| #19 \\| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \\|”'),
    "compute_reputation": ('UNBACKED', '**no doc states a destination**'),
    "create_derivation": ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`”'),
    "create_direct_edge": ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`”'),
    "create_document": ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`.”'),
    "create_edge": ('link_entities', '`canonical-sdk-methods.md` — “\\| W9 \\| `link_entities` \\| #15 \\| `create_edge`,”'),
    "create_entity": ('UNCHANGED', '`beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)”'),
    "create_event": ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`.”'),
    "create_object": ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`.”'),
    "create_operator": ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`”'),
    "create_or_update_point": ('create_entity', '`canonical-sdk-methods.md` — “\\| `create_or_update_point` → `create_point` \\|”'),
    "create_point": ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`.”'),
    "create_source": ('register_source', '`canonical-sdk-methods.md` — “\\| W3 \\| `register_source` \\| #11 \\| `create_source`, `complete_source` \\|”'),
    "create_subject": ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`.”'),
    "delete": ('delete_knowledge', '`canonical-sdk-methods.md` — “\\| W12 \\| `delete_knowledge` \\| #18 \\| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \\| keep, collapse \\|”'),
    "delete_entity": ('delete_knowledge', '`canonical-sdk-methods.md` — “\\| W12 \\| `delete_knowledge` \\| #18 \\| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \\| keep, collapse \\|”'),
    "delete_point": ('delete_knowledge', '`beta-sdk-surface.md` — “\\| `delete_point`, `delete_point_wrapped` \\| 2 \\| → `delete_knowledge`. \\|”'),
    "delete_point_wrapped": ('delete_knowledge', '`beta-sdk-surface.md` — “\\| `delete_point`, `delete_point_wrapped` \\| 2 \\| → `delete_knowledge`. \\|”'),
    "diary_read": ('DISCARDED', '`beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.”'),
    "diary_write": ('DISCARDED', '`beta-sdk-surface.md` — “**The journal capability** — `checkpoint`, `diary_write`, `diary_read`.”'),
    "dream": ('refresh_confidence', '`canonical-sdk-methods.md` — “\\| W13 \\| `stabilize_beliefs` \\| #19 \\| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \\|”'),
    "dream_health_check": ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview`”'),
    "dream_health_state": ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview`”'),
    "events_poll": ('poll_events', '`beta-sdk-surface.md` — “\\| `events_poll` \\| → row 11 `poll_events`. \\|”'),
    "expand_relationships": ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    "file_decision": ('write_question', '`beta-sdk-surface.md` — “\\| `file_decision` \\| → rows 20/21 **`write_question`** + **`record_decision`**.”'),
    "file_human_approval": ('record_decision', '`beta-sdk-surface.md` — “\\| `file_human_approval` \\| 1 \\| → `record_decision`. \\|”'),
    "get_confidence": ('check_confidence', '`canonical-sdk-methods.md` — “\\| R3 \\| `recall_beliefs` \\| #3 \\| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`”'),
    "get_cross_lens_candidates": ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    "get_entity": ('UNCHANGED', '`beta-sdk-surface.md` — “current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`)”'),
    "get_events": ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`”'),
    "get_org_structure": ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    "get_owned_entities": ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`”'),
    "get_point": ('get_entity', '`canonical-sdk-methods.md` — “\\| R4 \\| `get_entity` \\| #4 \\| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \\|”'),
    "get_provenance_chain": ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`”'),
    "get_session": ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`”'),
    "get_source_reliability": ('manage_source_trust', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust`”'),
    "graph_active_key_count": ('list_keys', '`beta-sdk-surface.md` — “\\| `graph_key_ids`, `graph_active_key_count` \\| 2 \\| Console diagnostics. Both fold into `list_keys`. \\|”'),
    "graph_count": ('list_memory_graphs', '`beta-sdk-surface.md` — “`list_memory_graphs` answers "how many" for any real N.”'),
    "graph_delete": ('delete_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    "graph_key_ids": ('list_keys', '`beta-sdk-surface.md` — “\\| `graph_key_ids`, `graph_active_key_count` \\| 2 \\| Console diagnostics. Both fold into `list_keys`. \\|”'),
    "graph_list": ('list_memory_graphs', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    "graph_restore": ('restore_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    "graph_set_name": ('update_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    "graph_set_recording": ('update_memory_graph', "`beta-sdk-surface.md` — “\\| ~~`graph_set_recording`~~ (SDK method) \\| 1 \\| **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. \\|”"),
    "index_directory": ('index_sources_from_directory', '`canonical-sdk-methods.md` — “\\| W4 \\| `index_files` \\| #12 \\| `index_file`, `index_directory`”'),
    "index_file": ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    "index_sessions": ('index_sources_from_directory', '`canonical-sdk-methods.md` — “\\| `index_sessions` / `ingest_corpus` → `index_directory` \\|”'),
    "ingest": ('write_knowledge_batch', '`canonical-sdk-methods.md` — “\\| W2 \\| `write_knowledge` \\| — \\| `ingest` \\|”'),
    "ingest_corpus": ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    "invalidate_point": ('update_knowledge', '`beta-sdk-surface.md` — “\\| `retract_point`, `invalidate_point` \\| 2 \\| → fields on `update_knowledge`.”'),
    "invitation_accept": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "invitation_create": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "invitation_get_by_id": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "invitation_get_by_token": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "invitation_list": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "invitation_revoke": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    "issue_insight": ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    "link_source_to_entity": ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`”'),
    "list_batch": ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_batch`, `list_batches` \\| 2 \\| → `list_knowledge(kind='batch')`.”"),
    "list_batches": ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_batch`, `list_batches` \\| 2 \\| → `list_knowledge(kind='batch')`.”"),
    "list_dedup_candidates": ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    "list_drafts": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence wrangling”'),
    "list_graphs": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "list_namespaces": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "list_pointkinds": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "list_relations": ('graph_overview', '`canonical-sdk-methods.md` — “\\| R6 \\| `graph_overview` \\| #6 \\| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`”'),
    "list_sources": ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_sources` \\| **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. \\|”"),
    "list_tags": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "list_topics": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "membership_create": ('add_member', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\|”'),
    "membership_delete": ('remove_member', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\|”'),
    "membership_get": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing.”'),
    "membership_list": ('list_members', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\|”'),
    "membership_update_role": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing.”'),
    "migrate_orgs_to_registry": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.**”'),
    "mine_corpus": ('mine_knowledge_from_directory', '`beta-sdk-surface.md` — “\\| `mine_corpus` \\| 1 \\| → `mine_knowledge_from_directory`.”'),
    "mitigate_operator": ('adjust_relationship', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship`”'),
    "operator_action": ('adjust_relationship', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship`”'),
    "org_create": ('UNBACKED', '**no doc states a destination**'),
    "org_delete": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing.”'),
    "org_get": ('get_organisation_account', '`beta-sdk-surface.md` — “\\| 28 \\| `get_organisation_account` \\| Read the account and the plan it is on \\|”'),
    "org_list": ('get_organisation_account', '`beta-sdk-surface.md` — “\\| 28 \\| `get_organisation_account` \\| Read the account and the plan it is on \\|”'),
    "org_update": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing.”'),
    "paginated_query": ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    "promote_point": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence wrangling”'),
    "provenance": ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence`”'),
    "quarantine_batch": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence wrangling”'),
    "query": ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    "query_points_by_tag": ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    "recall_gaps": ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence`”'),
    "recall_state": ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence`”'),
    "recall_subgraph": ('DISCARDED', '`beta-sdk-surface.md` — “**`recall_subgraph` is dropped, not folded**”'),
    "reconcile_sessions": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations.”'),
    "record_calibration": ('UNBACKED', '**no doc states a destination**'),
    "resolve_id": ('get_entity', '`canonical-sdk-methods.md` — “\\| R4 \\| `get_entity` \\| #4 \\| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \\|”'),
    "restore_point_at": ('get_historical_knowledge', '`beta-sdk-surface.md` — “\\| `restore_point_at` \\| → row 7 **`get_historical_knowledge`**.”'),
    "retract_point": ('update_knowledge', '`beta-sdk-surface.md` — “\\| `retract_point`, `invalidate_point` \\| 2 \\| → fields on `update_knowledge`.”'),
    "retrieval_legs": ('check_confidence', '`canonical-sdk-methods.md` — “\\| R3 \\| `recall_beliefs` \\| #3 \\| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`”'),
    "review_connections": ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    "search_sessions": ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    "session_context": ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence`”'),
    "session_index_health": ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    "set_point_baseline": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence wrangling”'),
    "set_source_tier": ('manage_source_trust', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust`”'),
    "signup_token_lookup": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup”'),
    "signup_token_recover": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup”'),
    "signup_token_revoke": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup”'),
    "stale_points": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "status": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "suggest_entry_points": ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    "summarize_structure": ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview`”'),
    "supersede": ('supersede_knowledge', '`beta-sdk-surface.md` — “\\| `supersede`, `supersede_point` \\| 2 \\| → `supersede_knowledge`.”'),
    "supersede_point": ('supersede_knowledge', '`beta-sdk-surface.md` — “\\| `supersede`, `supersede_point` \\| 2 \\| → `supersede_knowledge`.”'),
    "sweep_invite_ghost_memberships": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.**”'),
    "taxonomy": ('graph_overview', '`beta-sdk-surface.md` — “narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.**”'),
    "test_guard": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `test_guard` \\| **Kept and relocated.**”'),
    "topic_summarize": ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    "tortoise_fts_query": ('search_knowledge', '`canonical-sdk-methods.md` — “\\| R1 \\| `search_knowledge` \\| #1 \\| `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` \\|”'),
    "trash_graphs": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.**”'),
    "traverse": ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    "ulid": ('DISCARDED', '`beta-sdk-surface.md` — “\\| `ulid` \\| 1 \\| A ULID generator. Not a memory operation. \\|”'),
    "update": ('update_knowledge', '`canonical-sdk-methods.md` — “\\| W11 \\| `revise_knowledge` \\| #17 \\| `update`,”'),
    "update_entity": ('update_knowledge', '`beta-sdk-surface.md` — “\\| `update_point`, `update_entity` \\| 2 \\| → `update_knowledge`. \\|”'),
    "update_point": ('update_knowledge', '`beta-sdk-surface.md` — “\\| `update_point`, `update_entity` \\| 2 \\| → `update_knowledge`. \\|”'),
    "validate_domain": ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview`”'),
    "volunteer_context": ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence`”'),
}

def _doc() -> str:
    """The generated document, read fresh — never cached across tests."""
    return DOC.read_text(encoding="utf-8")


def _section(name: str) -> str:
    """The text of the `## {name}` / `### {name}` section up to the next heading."""
    text = DOC.read_text(encoding="utf-8")
    marker = f"## {name}"
    assert marker in text, f"the generated doc has no `{marker}` section"
    body = text.split(marker, 1)[1]
    return re.split(r"\n###? ", body)[0]


def _first_column(section: str) -> list[str]:
    out = []
    for line in section.splitlines():
        m = re.match(r"^\| `([A-Za-z_][A-Za-z0-9_]*)` \|", line)
        if m:
            out.append(m.group(1))
    return out


def canonical_groups_independently() -> dict[str, list[str]]:
    """The canonical inventory partition, parsed here from the source doc.

    Scoped to the `## The groups (the earlier sketch)` section: the doc reuses the
    R/W labels for a second, differently-shaped table further down.
    """
    text = CANON.read_text(encoding="utf-8")
    body = text[text.index("## The groups (the earlier sketch)"):text.index(
        "## What each group does")]
    out: dict[str, list[str]] = {}
    for line in body.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not re.fullmatch(r"[RW]\d+|N\d+", cells[0]):
            continue
        idx = 2 if cells[0].startswith("N") else 3
        out[cells[0]] = (re.findall(r"`([a-z_][a-z0-9_]*)`", cells[idx])
                         if len(cells) > idx else [])
    return out


@pytest.fixture(scope="module")
def generator_module():
    """Import the generator once, for the validation-path tests only.

    The constants that describe the surface are never imported: they are exactly what
    these tests exist to check.
    """
    sys.path.insert(0, str(ROOT))
    from tools import sdk_rename_table

    return sdk_rename_table


# ─────────────────────────────────────────────────────────────────────
# THE GATE — `--check` must be clean, and must RED on drift
# ─────────────────────────────────────────────────────────────────────
def test_check_mode_is_clean() -> None:
    """The committed doc must equal a fresh render. This is the CI form."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        "the generated SDK rename table is stale — run "
        f"`uv run python tools/sdk_rename_table.py`.\n{proc.stderr}"
    )
    assert "is current" in proc.stdout


def test_check_mode_detects_a_drifted_copy(tmp_path: Path) -> None:
    """A deliberately drifted copy must give a NON-ZERO exit.

    Without this, a mutation that makes `main()` return 0 unconditionally disarms the
    whole drift gate while `test_check_mode_is_clean` stays green. Run in-process (not
    as a second subprocess) purely to avoid paying a second ~16s `tortoise.sdk` import;
    it exercises the same `--check` branch.

    The drift is a **count in the prose** — the mutation class the sibling PR's review
    found unguarded (the table's rows were checked; its sentences were not).
    """
    sys.path.insert(0, str(ROOT))
    from tools import sdk_rename_table

    drifted = tmp_path / "sdk-rename-table.md"
    text = DOC.read_text(encoding="utf-8")
    assert "150 public methods" in text
    drifted.write_text(text.replace("150 public methods", "151 public methods"),
                       encoding="utf-8")

    rc = sdk_rename_table.main(["--check", "--out", str(drifted)])
    assert rc != 0, "a drifted copy passed `--check` — the drift gate is disarmed"


# The COMPLETE derived surface: every public method → its destination. Pinned in
# full because the representative-pin test below covers group DEFAULTS only — an
# `OVERRIDE` exception could be reassigned and the suite stayed green, with the row
# then contradicting the very citation it carries (58 of the 80 overrides were
# unpinned). A literal here is the anchor: any change to the mapping reds.
TARGETS_LITERAL: dict[str, str] = {
    "annotate_ask_hits": "search_knowledge",
    "annotate_operator": "adjust_relationship",
    "apikey_create": "create_key",
    "apikey_list": "list_keys",
    "apikey_revoke": "revoke_key",
    "apikey_verify": "DISCARDED",
    "approve_merge": "UNCHANGED",
    "assess_source": "manage_source_trust",
    "audit": "graph_overview",
    "backfill_about_entities": "DISCARDED",
    "backfill_sources": "DISCARDED",
    "backfill_v25": "DISCARDED",
    "batch_create_points": "write_knowledge_batch",
    "belief_timeline": "check_confidence",
    "calibrate_summary": "check_confidence",
    "calibration_passed": "check_confidence",
    "capture_session": "mine_knowledge_from_session",
    "check_structure": "graph_overview",
    "checkpoint": "DISCARDED",
    "cleanup_expired_invitations": "DISCARDED",
    "close": "UNCHANGED",
    "commit_session": "mine_knowledge_from_session",
    "complete_source": "DISCARDED",
    "compute_confidence": "refresh_confidence",
    "compute_reputation": "UNBACKED",
    "create_derivation": "link_entities",
    "create_direct_edge": "link_entities",
    "create_document": "create_entity",
    "create_edge": "link_entities",
    "create_entity": "UNCHANGED",
    "create_event": "create_entity",
    "create_object": "create_entity",
    "create_operator": "link_entities",
    "create_or_update_point": "create_entity",
    "create_point": "create_entity",
    "create_source": "register_source",
    "create_subject": "create_entity",
    "delete": "delete_knowledge",
    "delete_entity": "delete_knowledge",
    "delete_point": "delete_knowledge",
    "delete_point_wrapped": "delete_knowledge",
    "diary_read": "DISCARDED",
    "diary_write": "DISCARDED",
    "dream": "refresh_confidence",
    "dream_health_check": "graph_overview",
    "dream_health_state": "graph_overview",
    "events_poll": "poll_events",
    "expand_relationships": "explore_connections",
    "file_decision": "write_question",
    "file_human_approval": "record_decision",
    "get_confidence": "check_confidence",
    "get_cross_lens_candidates": "review_link_candidates",
    "get_entity": "UNCHANGED",
    "get_events": "get_entity",
    "get_org_structure": "explore_connections",
    "get_owned_entities": "get_entity",
    "get_point": "get_entity",
    "get_provenance_chain": "get_entity",
    "get_session": "get_entity",
    "get_source_reliability": "manage_source_trust",
    "graph_active_key_count": "list_keys",
    "graph_count": "list_memory_graphs",
    "graph_delete": "delete_memory_graph",
    "graph_key_ids": "list_keys",
    "graph_list": "list_memory_graphs",
    "graph_restore": "restore_memory_graph",
    "graph_set_name": "update_memory_graph",
    "graph_set_recording": "update_memory_graph",
    "index_directory": "index_sources_from_directory",
    "index_file": "index_sources_from_directory",
    "index_sessions": "index_sources_from_directory",
    "ingest": "write_knowledge_batch",
    "ingest_corpus": "index_sources_from_directory",
    "invalidate_point": "update_knowledge",
    "invitation_accept": "DISCARDED",
    "invitation_create": "DISCARDED",
    "invitation_get_by_id": "DISCARDED",
    "invitation_get_by_token": "DISCARDED",
    "invitation_list": "DISCARDED",
    "invitation_revoke": "DISCARDED",
    "issue_insight": "search_knowledge",
    "link_source_to_entity": "link_entities",
    "list_batch": "list_knowledge",
    "list_batches": "list_knowledge",
    "list_dedup_candidates": "review_link_candidates",
    "list_drafts": "DISCARDED",
    "list_graphs": "graph_overview",
    "list_namespaces": "graph_overview",
    "list_pointkinds": "graph_overview",
    "list_relations": "graph_overview",
    "list_sources": "list_knowledge",
    "list_tags": "graph_overview",
    "list_topics": "graph_overview",
    "membership_create": "add_member",
    "membership_delete": "remove_member",
    "membership_get": "DISCARDED",
    "membership_list": "list_members",
    "membership_update_role": "DISCARDED",
    "migrate_orgs_to_registry": "DISCARDED",
    "mine_corpus": "mine_knowledge_from_directory",
    "mitigate_operator": "adjust_relationship",
    "operator_action": "adjust_relationship",
    "org_create": "UNBACKED",
    "org_delete": "DISCARDED",
    "org_get": "get_organisation_account",
    "org_list": "get_organisation_account",
    "org_update": "DISCARDED",
    "paginated_query": "list_knowledge",
    "promote_point": "DISCARDED",
    "provenance": "check_confidence",
    "quarantine_batch": "DISCARDED",
    "query": "list_knowledge",
    "query_points_by_tag": "list_knowledge",
    "recall_gaps": "check_confidence",
    "recall_state": "check_confidence",
    "recall_subgraph": "DISCARDED",
    "reconcile_sessions": "DISCARDED",
    "record_calibration": "UNBACKED",
    "resolve_id": "get_entity",
    "restore_point_at": "get_historical_knowledge",
    "retract_point": "update_knowledge",
    "retrieval_legs": "check_confidence",
    "review_connections": "review_link_candidates",
    "search_sessions": "search_knowledge",
    "session_context": "check_confidence",
    "session_index_health": "index_sources_from_directory",
    "set_point_baseline": "DISCARDED",
    "set_source_tier": "manage_source_trust",
    "signup_token_lookup": "DISCARDED",
    "signup_token_recover": "DISCARDED",
    "signup_token_revoke": "DISCARDED",
    "stale_points": "graph_overview",
    "status": "graph_overview",
    "suggest_entry_points": "search_knowledge",
    "summarize_structure": "graph_overview",
    "supersede": "supersede_knowledge",
    "supersede_point": "supersede_knowledge",
    "sweep_invite_ghost_memberships": "DISCARDED",
    "taxonomy": "graph_overview",
    "test_guard": "DISCARDED",
    "topic_summarize": "search_knowledge",
    "tortoise_fts_query": "search_knowledge",
    "trash_graphs": "DISCARDED",
    "traverse": "explore_connections",
    "ulid": "DISCARDED",
    "update": "update_knowledge",
    "update_entity": "update_knowledge",
    "update_point": "update_knowledge",
    "validate_domain": "graph_overview",
    "volunteer_context": "check_confidence",
}


# The C3 tensions: method → (what Part A carries, what the other doc implies).
# Pinned because deleting the tensions list renders "None." and, before this test,
# the suite stayed green — the section's whole content was unread.
C3_TENSIONS_LITERAL = {
    "get_owned_entities": ("get_entity", "explore_connections"),
    "get_provenance_chain": ("get_entity", "check_confidence"),
    "restore_point_at": ("get_historical_knowledge", "check_confidence"),
    "list_sources": ("list_knowledge", "graph_overview"),
    "test_guard": ("DISCARDED", "graph_overview"),
    "graph_set_recording": ("update_memory_graph", "kept, inside the control-plane block"),
}


def test_part_c3_tensions_are_read() -> None:
    """C3 is a findings table; emptying it must red, and its cells must stay true."""
    doc = _doc()
    c3 = doc.split("### C3")[1].split("### C4")[0]
    rows = re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| `?([A-Za-z_][A-Za-z0-9_ ,-]*)`? \| "
        r"`?([A-Za-z_][A-Za-z0-9_ ,-]*)`? \|",
        c3, re.M,
    )
    parsed = {n: (a.strip(), b.strip()) for n, a, b in rows}
    assert parsed == C3_TENSIONS_LITERAL, (
        "C3's cross-doc tensions changed, or the section emptied itself.\n"
        f"  parsed: {parsed}\n  pinned: {C3_TENSIONS_LITERAL}"
    )
    # The 4th column is C3's EVIDENCE, and it was read by nothing: it could be swapped
    # for another real sentence from the same doc while the tension stayed as claimed.
    for name, marker in (
        ("get_owned_entities", r"\| R5 \| `explore_connections`"),
        ("get_provenance_chain", r"\| R3 \| `recall_beliefs`"),
        ("restore_point_at", r"\| R3 \| `recall_beliefs`"),
        ("list_sources", r"\| R6 \| `graph_overview`"),
        ("test_guard", r"\| R6 \| `graph_overview`"),
        ("graph_set_recording", r"\| N2 \| `graph`"),
    ):
        row = next(
            (ln for ln in c3.splitlines() if ln.startswith(f"| `{name}` |")), None
        )
        assert row, f"C3 has no row for {name}"
        assert marker in row, (
            f"C3's evidence for {name} is no longer the {marker!r} grouping — "
            f"the tension is now backed by a different doc's table:\n  {row}"
        )


def test_structural_counts_and_the_summary_sentence_are_read() -> None:
    """The summary sentence and the structural counts were rendered but never asserted.

    The summary sentence's numbers can be made self-contradictory ("1 are target
    methods with no def ... 34 are target methods that already exist") with the
    suite green, so each is pinned here.
    """
    doc = _doc()
    m = re.search(
        r"Distinct destinations: \*\*(\d+)\*\* — \*\*(\d+)\*\* are target methods "
        r"with no `def` today \(Part C1 lists all (\d+) Phase-2 methods\), "
        r"\*\*(\d+)\*\* are target "
        r"methods that already exist \(`create_entity`, `get_entity`\), and "
        r"\*\*(\d+)\*\* are the non-target dispositions",
        doc,
    )
    assert m, "the 'Distinct destinations' summary sentence is missing or changed shape"
    distinct, no_def, c1_total, exists, non_target = (int(g) for g in m.groups())
    # The summary counts DISTINCT DESTINATIONS; C1 lists METHODS. They differ by the
    # targets that have no `def` but are never a destination (`Tortoise`,
    # `check_connection`, `create_memory_graph`) — so 33 vs 36 is correct, and the
    # sentence must say which is which rather than attributing 33 to C1.
    assert c1_total == 36, f"Part C1 lists {c1_total} Phase-2 methods, expected 36"
    assert c1_total == no_def + 3, (
        f"C1's {c1_total} and the summary's {no_def} destinations should differ by exactly "
        f"the 3 never-a-destination targets"
    )
    assert distinct == no_def + exists + non_target, (
        f"the summary's parts ({no_def}+{exists}+{non_target}) do not sum to its "
        f"distinct-destination count ({distinct})"
    )
    # Each part is pinned, not just the sum: `replaced = 1` keeps `1 + 34 + 3 == 38`
    # true while making the sentence claim 1 method has no `def` when 33 do.
    assert (distinct, no_def, exists, non_target) == (38, 33, 2, 3), (
        "the destination summary moved: "
        f"{distinct} destinations = {no_def} no-def + {exists} existing + {non_target} other"
    )
    assert "**32 groups** over **149** named members" in doc, (
        "the structural note's canonical-group counts are gone"
    )
    assert "150 = the 150-method surface" in doc, (
        "the structural note no longer reconciles its members to the 150-method surface"
    )
    c5 = doc.split("### C5")[1].split("### Structural")[0]
    assert set(re.findall(r"`([WN]\d+)`", c5)) == {"W16"}, (
        "C5 no longer names exactly the wildcard families' resolved group (W16)"
    )


def test_every_citation_quote_is_pinned() -> None:
    """The quote TEXT is pinned, not merely the citation key that carries it.

    Without this, a citation can be swapped for a different real sentence in the same
    document: the row keeps its Target and its `stated` basis, and only the evidence
    is wrong. That is the failure mode this artifact exists to make impossible.
    """
    import tools.sdk_rename_table as gen

    actual = {k: v[1] for k, v in gen.CITES.items()}
    actual.update({k: v[1] for k, v in (gen.PHANTOM_CITES or {}).items()})
    # C3's tensions carry their own quotes; omitting them left the C3 evidence free.
    actual.update({k: v[1] for k, v in (gen.TENSION_CITES or {}).items()})
    assert actual == CITES_LITERAL, (
        "a citation quote changed, or cites were re-keyed.\n"
        f"  changed: {sorted(k for k in actual if CITES_LITERAL.get(k) != actual[k])}\n"
        f"  added:   {sorted(set(actual) - set(CITES_LITERAL))}\n"
        f"  dropped: {sorted(set(CITES_LITERAL) - set(actual))}"
    )


def test_every_row_is_bound_to_its_own_evidence() -> None:
    """Each row's Target AND its rendered citation text equal the pinned pair.

    This subsumes the per-key quote pin for Part A: swapping which citation key a row
    uses changes the text it renders, so the swap reds. Without it, a row could cite
    another method's grouping as the evidence for its own disposition.
    """
    rows = part_a_rows()
    actual = {r["name"]: (r["target"], r["cite"]) for r in rows}
    assert actual == ROW_CITE_LITERAL, (
        "a row's target or its citation text moved.\n"
        f"  changed: {sorted(k for k in actual if ROW_CITE_LITERAL.get(k) != actual[k])[:10]}\n"
        f"  added:   {sorted(set(actual) - set(ROW_CITE_LITERAL))}\n"
        f"  dropped: {sorted(set(ROW_CITE_LITERAL) - set(actual))}"
    )


# ─────────────────────────────────────────────────────────────────────
# PART A — the table
# ─────────────────────────────────────────────────────────────────────
def test_part_a_covers_exactly_the_public_surface() -> None:
    """One row per public method, and nothing else.

    Expected values come from a fresh AST walk here, so a generator change that drops,
    duplicates or invents a method fails even though `--check` would agree with itself.
    """
    rows = part_a_rows()
    names = [r["name"] for r in rows]
    assert len(rows) == 150, f"Part A has {len(rows)} rows, expected the 150-method surface"
    assert len(set(names)) == len(names), (
        "Part A lists a method twice — `--check` cannot see a duplicated row because the "
        "duplicate is in the constant too"
    )
    assert sorted(names) == sorted(public_methods_independently())


def test_the_whole_mapping_is_pinned() -> None:
    """Every one of the 150 `method → destination` pairs equals the pinned literal.

    The representative-pin test guards group defaults; this guards the exceptions.
    Without it a row can be reassigned to a different target while its citation
    still quotes the ORIGINAL target's sentence — a self-contradicting row that
    passes every substring check.
    """
    actual = {r["name"]: r["target"] for r in part_a_rows()}
    assert actual == TARGETS_LITERAL, (
        "the method → destination mapping moved.\n"
        f"  changed: {sorted(k for k in actual if TARGETS_LITERAL.get(k) != actual[k])}\n"
        f"  added:   {sorted(set(actual) - set(TARGETS_LITERAL))}\n"
        f"  dropped: {sorted(set(TARGETS_LITERAL) - set(actual))}"
    )


def test_part_a_line_numbers_point_at_the_right_method() -> None:
    """Every `sdk.py:N` citation must equal the method's real `def` line.

    This closes the artifact's central promise — that the citations cannot drift — and
    it is compared against a **fresh AST walk**, not a second reading of the generator.
    A constant offset added to every emitted line number makes all 150 citations wrong
    while every other test stays green.
    """
    truth = public_methods_independently()
    bad = [
        f"{r['name']}: cited :{r['line']}, defined at :{truth.get(r['name'])}"
        for r in part_a_rows()
        if truth.get(r["name"]) != r["line"]
    ]
    assert not bad, "Part A cites the wrong `sdk.py` line for:\n  " + "\n  ".join(bad)


def test_every_target_is_on_the_approved_surface() -> None:
    """The Target column may name a target, or be one of the three dispositions."""
    approved = set(targets_independently())
    assert len(approved) == 40, (
        f"parsed {len(approved)} target names from beta-sdk-surface.md, expected 40"
    )
    allowed = approved | {"UNCHANGED", "DISCARDED", "UNBACKED"}
    bad = sorted({r["target"] for r in part_a_rows()} - allowed)
    assert not bad, f"Part A names destinations that are not on the approved surface: {bad}"


def test_every_citation_still_resolves_in_the_doc_it_names() -> None:
    """A citation is a claim; the quote must still be in the cited doc.

    The generator validates this too, but a mutation that drops that check would leave
    every row pointing at prose that has since been reworded — and `--check` would not
    notice, because both sides are stale together.
    """
    docs = {
        "beta-sdk-surface.md": BETA.read_text(encoding="utf-8"),
        "canonical-sdk-methods.md": CANON.read_text(encoding="utf-8"),
    }
    bad = []
    for r in part_a_rows():
        if r["basis"] == "unbacked":
            assert r["cite"] == "**no doc states a destination**", (
                f"{r['name']}: an unbacked row must not carry a citation"
            )
            continue
        m = CITE_RE.match(r["cite"])
        assert m, f"{r['name']}: unparseable citation cell: {r['cite']!r}"
        doc, quote = m.group(1), m.group(2).replace("\\|", "|")
        assert doc in docs, f"{r['name']}: cites an unknown doc {doc!r}"
        if quote not in docs[doc]:
            bad.append(f"{r['name']} → {doc}: {quote[:60]!r}")
    assert not bad, "Part A cites text that is no longer in the cited doc:\n  " + \
                    "\n  ".join(bad)


def test_basis_and_target_agree() -> None:
    """`unbacked` is exactly the `UNBACKED` target; every other row is stated/derived."""
    for r in part_a_rows():
        if r["target"] == "UNBACKED":
            assert r["basis"] == "unbacked", f"{r['name']}: UNBACKED must have basis=unbacked"
        else:
            assert r["basis"] in ("stated", "derived"), (
                f"{r['name']}: a row with a destination must be stated or derived"
            )


def test_every_stated_row_names_its_method_in_the_quote() -> None:
    """A `stated` row must be backed by a quote that actually NAMES the method.

    The Basis legend defines `stated` as "the cited quote names this method". That was
    a hand-authored property (`named`, a frozenset beside each citation) rather than a
    computed one, and three rows contradicted their own legend while every other test
    stayed green: `delete_entity` cited a `W12` row that named only `delete`;
    `graph_set_recording` and `list_sources` cited prose that named no method at all.
    `basis` is now computed from the quote (`_names`), and THIS test is the independent
    check — it re-parses the rendered document and re-derives the invariant, so a
    regression that reintroduces an authored basis reds here even if `--check` agrees
    with itself. Method names are matched on token boundaries: `delete` appears inside
    `delete_entity`, so a substring test would pass the very row this test exists for.
    """
    bad = []
    for r in part_a_rows():
        if r["basis"] != "stated":
            continue
        m = CITE_RE.match(r["cite"])
        assert m, f"{r['name']}: a stated row has an unparseable citation: {r['cite']!r}"
        quote = m.group(2).replace("\\|", "|")
        if not re.search(rf"(?<![A-Za-z0-9_]){re.escape(r['name'])}(?![A-Za-z0-9_])",
                         quote):
            bad.append(f"{r['name']} → {quote[:70]!r}")
    assert not bad, (
        "rows render Basis=stated but their quoted evidence does not name the method, "
        "contradicting the Basis legend:\n  " + "\n  ".join(bad)
    )


def test_the_resolved_partition_never_emits_the_wildcard_group() -> None:
    """`W16` is the canonical doc's wildcard spelling of N1–N6; it has no own members."""
    groups = {r["group"] for r in part_a_rows()}
    assert "W16" not in groups, (
        "a row carries group W16 — its members are wildcards that duplicate N1–N6, so the "
        "partition must resolve each method to its family group"
    )
    assert groups <= {f"R{i}" for i in range(1, 10)} | {f"W{i}" for i in range(1, 18)} \
        | {f"N{i}" for i in range(1, 7)} | {"ARCHIVE"}


# ─────────────────────────────────────────────────────────────────────
# THE PROSE COUNTS — every one is a claim
# ─────────────────────────────────────────────────────────────────────
def test_prose_counts_are_the_literals_the_doc_claims() -> None:
    text = DOC.read_text(encoding="utf-8")
    # Digit-BOUNDED, not substring: `"150 public methods" in text` also passes when the
    # render produces `1150 public methods`, so the pin did not hold the number.
    for pattern in (
        r"(?<![\d])150 public methods",
        r"(?<![\d])40 target methods",
        r"(?<![\d])110 of the 150 are renames",
        r"\*\*4\*\* are already targets \(unchanged\)",
        r"\*\*33\*\* are discarded with a rationale",
        r"\*\*3\*\* have no destination",
        r"only \*\*4\*\* of them exist on `TortoiseSDK` today",
        r"\*\*36\*\* are Phase 2 work",
    ):
        assert re.search(pattern, text), f"the headline no longer says {pattern!r}"


def test_the_unread_cells_and_the_row_ordinals_are_pinned() -> None:
    """Cells that render facts but were read by no test, plus Part A's numbering.

    Each of these was mutable with the whole suite green: the ordinals could become
    `100, 101, …`, C1's status cell could state the opposite of its own heading, and
    C4's referent could name a method that does not exist. A cell that states a fact is
    a claim; unread, it is a claim that can be false. (C2's own Source and Reason columns
    are pinned separately, in `test_part_c2_reasons_and_sources_are_read`.)
    """
    text = DOC.read_text(encoding="utf-8")
    part_a = text.split("## Part A")[1].split("## Part B")[0]
    ordinals = [int(m) for m in re.findall(r"^\| (\d+) \|", part_a, re.M)]
    assert ordinals == list(range(1, 151)), (
        f"Part A's row ordinals are {ordinals[:5]}…{ordinals[-2:]}, expected 1..150"
    )
    c1 = text.split("### C1")[1].split("### C2")[0]
    assert c1.count("| no `def` on `TortoiseSDK` today |") == 36, (
        "C1's status column no longer repeats its one true statement on all 36 rows"
    )
    c4 = text.split("### C4")[1].split("### C5")[0]
    for name, referent in (
        ("recall_legs", "`retrieval_legs`"),
        ("stale", "`stale_points`"),
        ("count_memory_graphs", "`graph_count`"),
        ("set_memory_graph_name", "`graph_set_name`"),
        ("set_memory_graph_backend", "**none**"),
        ("index_sources", "`index_directory`"),
        ("withdraw_knowledge", "**none**"),
    ):
        assert re.search(rf"^\| `{name}` \| {re.escape(referent)} \|", c4, re.M), (
            f"C4's referent for {name} is no longer {referent}"
        )

    rows = part_a_rows()
    assert sum(1 for r in rows if r["target"] == "UNCHANGED") == 4
    assert sum(1 for r in rows if r["target"] == "DISCARDED") == 33
    assert sum(1 for r in rows if r["target"] == "UNBACKED") == 3


def test_part_a_rows_are_well_formed_markdown() -> None:
    r"""Every Part A row must carry exactly 7 cells when split on UNESCAPED pipes.

    The citation column quotes markdown table rows, so their `|` must be escaped as
    `\|` — otherwise the row silently grows extra columns and the quoted evidence
    renders as table syntax. Nothing else in the suite can see that: the row regex is
    greedy to the final pipe, so a broken table still parses and still round-trips.
    """
    bad = []
    for line in _section("Part A — every public method and its migration row").splitlines():
        if not re.match(r"^\| \d+ \| ", line):
            continue
        cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
        if len(cells) != 7:
            bad.append((line[:80], len(cells)))
    assert not bad, (
        "Part A rows have the wrong number of markdown cells (escaped pipes are "
        f"required in the citation column): {bad}"
    )


def test_group_column_matches_the_canonical_inventory() -> None:
    """Each row's Group must be the canonical group that names the method.

    Expected values come from a fresh parse of `canonical-sdk-methods.md` here.
    `W16` is the doc's wildcard spelling of N1–N6, so it contributes no distinct
    member; `backfill_v25` lives in the Archived table and is labelled ARCHIVE.
    """
    group_of = {m: g for g, ms in canonical_groups_independently().items() for m in ms}
    group_of["backfill_v25"] = "ARCHIVE"
    wrong = {
        r["name"]: (r["group"], group_of.get(r["name"]))
        for r in part_a_rows() if r["group"] != group_of.get(r["name"])
    }
    assert not wrong, f"Part A's Group column disagrees with the inventory: {wrong}"


def test_part_b_counts_match_the_rows_they_list() -> None:
    """Part B's counts must equal the members it lists, and the total must be 150."""
    rows = []
    total = None
    for line in _section("Part B — destination counts").splitlines():
        if not line.startswith("|") or "---" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 3:
            continue
        dest, names, count = cells
        if not count.strip("*").isdigit():
            continue  # the header row: `| Destination | Current methods | Count |`
        if dest.startswith("**total**"):
            total = int(count.strip("*"))
            continue
        rows.append((dest.strip("`"), re.findall(r"`([a-z_][a-z0-9_]*)`", names),
                     int(count)))
    assert rows, "parsed no Part B rows"
    assert total == 150, f"Part B's total row says {total}, expected 150"
    bad = [(d, len(ms), c) for d, ms, c in rows if len(ms) != c]
    assert not bad, f"Part B counts disagree with the members listed: {bad}"
    assert sum(c for _, _, c in rows) == 150

    by_target: dict[str, set[str]] = {}
    for r in part_a_rows():
        by_target.setdefault(r["target"], set()).add(r["name"])
    assert {d: set(ms) for d, ms, _ in rows} == by_target, (
        "Part B and Part A disagree about which methods reach each destination"
    )


def test_basis_split_is_the_literal() -> None:
    """`derived` is the 19 rows backed only by a wildcard/namespace statement.

    `stated` vs `derived` is the column that says *how strongly* a mapping is backed.
    A mutation that forces every row to `stated` leaves every other test green, so
    the split is pinned here by name.
    """
    derived = {r["name"] for r in part_a_rows() if r["basis"] == "derived"}
    assert derived == {
        "apikey_create", "apikey_list", "apikey_revoke", "graph_count",
        "invitation_accept", "invitation_create", "invitation_get_by_id",
        "invitation_get_by_token", "invitation_list", "invitation_revoke",
        "membership_create", "membership_delete", "membership_list",
        "org_get", "org_list", "signup_token_lookup", "signup_token_recover",
        "signup_token_revoke", "stale_points",
    }


def test_part_c1_lists_exactly_the_targets_with_no_def() -> None:
    """C1 is the finding Part A exists to produce; it must not empty itself silently.

    Expected values are recomputed here from the beta doc + a fresh AST walk.
    """
    expected = set(targets_independently()) - set(public_methods_independently())
    assert len(expected) == 36
    listed = set(_first_column(_section("C1 — target methods with no `def` on `TortoiseSDK`")))
    assert listed == expected, (
        "Part C1 does not list exactly the target methods with no `def`.\n"
        f"  listed but has a def: {sorted(listed - expected)}\n"
        f"  missing from C1 ({len(expected - listed)}): {sorted(expected - listed)}"
    )


def test_part_c2_is_exactly_the_unbacked_rows() -> None:
    """The unbacked list is a literal: three methods, by name.

    A mutation that makes the section render "None." would leave the tests above green,
    so this pins it against a hand-written constant.
    """
    listed = set(_first_column(_section("C2 — rows with NO doc backing")))
    assert listed == {"org_create", "compute_reputation", "record_calibration"}
    assert listed <= set(public_methods_independently()), (
        "the C2 names must be real public methods"
    )
    unbacked_in_table = {r["name"] for r in part_a_rows() if r["target"] == "UNBACKED"}
    assert listed == unbacked_in_table


def test_part_c2_reasons_and_sources_are_read() -> None:
    """C2's Source and Reason columns were rendered but read by no test.

    The finding *is* the reason, and the Source is the claim that the method exists at
    that line: a reason that drifts from its finding, or a Source pointing at the wrong
    line, is a false claim nothing else in the suite can see. The line numbers are
    checked against a fresh AST walk; the reasons are pinned literally.
    """
    section = _section("C2 — rows with NO doc backing")
    rows = re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| `sdk\.py:(\d+)` \| (.*) \|$",
                      section, re.M)
    parsed = {n: (int(ln), reason) for n, ln, reason in rows}
    assert parsed == {
        "org_create": (
            15385,
            "No target method creates an organisation account. The tenancy block reads "
            "one (`get_organisation_account`) and files account *closure* as a console "
            "operation, but no row covers creation.",
        ),
        "compute_reputation": (
            20160,
            "The canonical `stabilize_beliefs` group lists it, but that group's beta "
            "target is `refresh_confidence` — “Recompute confidence after changes”. "
            "Reputation scoring is not confidence recomputation, and no other target "
            "absorbs it.",
        ),
        "record_calibration": (
            20410,
            "Same group, same mismatch: `refresh_confidence` recomputes confidence; "
            "recording a calibration milestone is a different operation and has no target.",
        ),
    }, f"C2's rows changed:\n  {parsed}"
    truth = public_methods_independently()
    wrong = {n: (ln, truth[n]) for n, (ln, _) in parsed.items() if ln != truth[n]}
    assert not wrong, f"C2 cites the wrong `sdk.py` line: {wrong}"


def test_part_c4_lists_the_doc_code_name_mismatches() -> None:
    """Seven names the docs use that are not `def`s, two with no referent at all."""
    section = _section("C4 — names the disposition docs use that are NOT SDK methods")
    names = re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", section, re.M)
    assert set(names) == {
        "recall_legs", "stale", "count_memory_graphs", "set_memory_graph_name",
        "set_memory_graph_backend", "index_sources", "withdraw_knowledge",
    }
    public = set(public_methods_independently())
    assert not (set(names) & public), (
        "a C4 'phantom' is now a real method — the finding is resolved and the row must go"
    )
    no_referent = re.findall(r"^\| `[a-z_]+` \| \*\*none\*\* \|", section, re.M)
    assert len(no_referent) == 2, (
        "exactly two of the doc's names have no referent: `set_memory_graph_backend` and "
        "`withdraw_knowledge`"
    )


def test_load_bearing_mappings_are_literal() -> None:
    """A handful of rows pinned by hand — the ones a rename must not silently reverse."""
    got = {r["name"]: r["target"] for r in part_a_rows()}
    expected = {
        # The load-bearing SDK names from canonical-sdk-methods.md's caller list.
        "create_point": "create_entity",
        "create_operator": "link_entities",
        "create_event": "create_entity",
        "get_point": "get_entity",
        "ingest": "write_knowledge_batch",
        "recall_state": "check_confidence",
        "promote_point": "DISCARDED",
        "mitigate_operator": "adjust_relationship",
        "compute_confidence": "refresh_confidence",
        "retract_point": "update_knowledge",
        "dream": "refresh_confidence",
        "tortoise_fts_query": "search_knowledge",
        "close": "UNCHANGED",
        # Rows where the two docs disagree, or a capricious rewrite is easy.
        "restore_point_at": "get_historical_knowledge",
        "list_sources": "list_knowledge",
        "test_guard": "DISCARDED",
        "recall_subgraph": "DISCARDED",
        "file_decision": "write_question",
        "file_human_approval": "record_decision",
        "graph_set_recording": "update_memory_graph",
        "graph_count": "list_memory_graphs",
        "backfill_v25": "DISCARDED",
        "supersede_point": "supersede_knowledge",
        "invalidate_point": "update_knowledge",
        "list_batches": "list_knowledge",
        "create_or_update_point": "create_entity",
    }
    assert set(expected) <= set(got), f"missing rows: {sorted(set(expected) - set(got))}"
    wrong = {k: (got[k], v) for k, v in expected.items() if got[k] != v}
    assert not wrong, f"load-bearing mappings changed: {wrong}"


def test_every_group_has_a_pinned_representative() -> None:
    """One non-overridden member per canonical group, pinned by target.

    A mutation that flips a whole group's default target changes every one of its
    un-overridden members at once — the readable load-bearing list above only covers
    the names a caller is most likely to grep for, so it can miss a group whose
    members are all "quiet". This closes that hole for every group in the partition.
    """
    got = {r["name"]: r["target"] for r in part_a_rows()}
    representatives = {
        "R1": ("annotate_ask_hits", "search_knowledge"),
        "R2": ("query", "list_knowledge"),
        "R3": ("recall_state", "check_confidence"),
        "R4": ("get_events", "get_entity"),
        "R5": ("traverse", "explore_connections"),
        "R6": ("audit", "graph_overview"),
        "R7": ("review_connections", "review_link_candidates"),
        "R8": ("events_poll", "poll_events"),
        "R9": ("list_batch", "list_knowledge"),
        "W1": ("create_point", "create_entity"),
        "W2": ("ingest", "write_knowledge_batch"),
        "W3": ("create_source", "register_source"),
        "W4": ("index_file", "index_sources_from_directory"),
        "W5": ("checkpoint", "DISCARDED"),
        "W6": ("capture_session", "mine_knowledge_from_session"),
        "W7": ("commit_session", "mine_knowledge_from_session"),
        "W8": ("assess_source", "manage_source_trust"),
        "W9": ("create_operator", "link_entities"),
        "W10": ("file_human_approval", "record_decision"),
        "W11": ("update_point", "update_knowledge"),
        "W12": ("delete_point", "delete_knowledge"),
        "W13": ("compute_confidence", "refresh_confidence"),
        "W14": ("approve_merge", "UNCHANGED"),
        "W15": ("mitigate_operator", "adjust_relationship"),
        "W17": ("ulid", "DISCARDED"),
        "N1": ("org_create", "UNBACKED"),
        "N2": ("graph_count", "list_memory_graphs"),
        "N3": ("membership_create", "add_member"),
        "N4": ("apikey_create", "create_key"),
        "N5": ("invitation_create", "DISCARDED"),
        "N6": ("signup_token_lookup", "DISCARDED"),
        "ARCHIVE": ("backfill_v25", "DISCARDED"),
    }
    wrong = {
        g: (got.get(m), want) for g, (m, want) in representatives.items()
        if got.get(m) != want
    }
    assert not wrong, f"a group's default target changed: {wrong}"


# ─────────────────────────────────────────────────────────────────────
# THE GENERATOR MUST FAIL, NOT PAPER OVER
# ─────────────────────────────────────────────────────────────────────
def test_validate_rejects_an_unmapped_method(generator_module) -> None:
    """A public method with no disposition must FAIL the build."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    assert generator_module._validate(methods, groups, targets, cites) == []

    methods["a_brand_new_method"] = 99999
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("UNMAPPED public method" in e and "a_brand_new_method" in e for e in errs), (
        f"a method with no map entry did not fail the build: {errs}"
    )


def test_validate_rejects_a_resolved_phantom(generator_module) -> None:
    """If a doc's phantom name becomes a real method, the build must fail."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)

    methods["set_memory_graph_backend"] = 88888
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("PHANTOM RESOLVED" in e for e in errs), (
        f"a resolved phantom did not fail the build: {errs}"
    )


def test_validate_rejects_a_drifted_citation(generator_module) -> None:
    """A citation whose quote is no longer in its doc must FAIL the build."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites["drift"] = ("beta-sdk-surface.md", "this sentence is in no doc at all")
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("CITATION DRIFT" in e and "drift" in e for e in errs), (
        f"a drifted citation did not fail the build: {errs}"
    )


def test_validate_rejects_a_broken_partition(generator_module) -> None:
    """If a doc claims a method the code does not have, the build must fail."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    # Simulate the canonical doc growing a member that is not a public def.
    groups["R1"] = groups["R1"] + ["a_method_the_code_lacks"]
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("STALE canonical member" in e for e in errs), (
        f"a canonical member with no `def` did not fail the build: {errs}"
    )


def test_section_prose_paragraphs_are_read() -> None:
    """The section intros and footers are claims, and every one was unread.

    Mutation testing found all seven of these paragraphs mutable with the whole suite
    green: Part A's "earlier sketch" note, C1's Phase-2 intro and constructor footer,
    C2's "open question" intro and "finding, not a gap" footer, C3's "different
    destinations" intro, and C4's "doc→code mismatch" intro. They are pinned verbatim
    here so the document's own explanation cannot quietly invert.
    """
    doc = _doc()
    paragraphs = [
        "Every `sdk.py:N` citation is **read from the AST at build time**, so it cannot "
        "drift from the code it cites. The 40 target names are **parsed out of "
        "`docs/product/beta-sdk-surface.md`** (owner-approved 2026-09-21), and the "
        "R/W/N group partition out of `docs/product/canonical-sdk-methods.md`; every "
        "count below is arithmetic over those, never a typed number. Each row's "
        "citation quote is **verified to still be in the doc it names** — a citation "
        "that no longer resolves fails the build.",
        "The canonical inventory's group names are an **earlier sketch** "
        "(`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). "
        "The `Target` column always carries the **beta** target name "
        "(`update_knowledge`, `refresh_confidence`, `write_knowledge_batch`, "
        "`index_sources_from_directory`) — the canonical doc itself says beta governs "
        "where the two disagree, and records the renames.",
        "A rename whose destination does not exist yet is **Phase 2 work**, not a rename.",
        "The plan listed the whole table as renames; this is what Part A exists to catch.",
        "`Tortoise` is row 1 of the target table (the constructor), not a method; the "
        "class today is `TortoiseSDK`, so the approved surface also renames the type.",
        "No document states a destination for these; the row is an open question, not an",
        "answer. Each needs an owner ruling before Phase 2 implements it.",
        "**An unbacked row is a finding, not a gap to fill by analogy.** Rolling these "
        "into a nearby target would silently drop a capability the surface has today.",
        "Two docs name **different** destinations for the same method. The row in Part A",
        "carries the `beta-sdk-surface.md` destination, because that is the owner-approved",
        'surface and the canonical inventory itself says so ("Where the two disagree, that',
        'doc governs"). The conflict is recorded here rather than resolved silently.',
        "A doc→code name mismatch. Where a referent is named, the Part A row for that",
        "referent cites the doc under its *doc* name; where no referent exists, the",
        "doc's statement is about a method that was never there.",
        "The inventory's group table has group labels whose members are expressed as",
        "wildcards (`org_*`, `graph_*`, …) that expand to the same methods as the",
        "control-plane families. They carry no distinct member, so the partition here",
        "resolves each method to its family group: `W16`.",
        "- Every group collapse above is checked against the AST walk at build time: a "
        "method the docs know and the code does not (or the reverse) **fails the build**.",
    ]
    missing = [p for p in paragraphs if p not in doc]
    assert not missing, (
        "section prose changed or was dropped — each paragraph is a claim:\n  "
        + "\n  ".join(m[:90] for m in missing)
    )


def test_doc_header() -> None:
    """The doc must announce what it is and that editing it by hand is a mistake."""
    text = DOC.read_text(encoding="utf-8")
    assert text.startswith("# Phase 0.3b — the SDK rename table\n")
    assert "**GENERATED — do not edit.**" in text


def test_legend_structural_prose_and_reproduce_block_are_read() -> None:
    """The header prose, the Basis legend and the Reproduce block were unread.

    The legend is load-bearing: `stated`/`derived`/`unbacked` in Part A mean only what
    these sentences say, so if the legend drifts while the Basis values do not, the
    table contradicts its own definition in silence. The Reproduce block is the
    promise that the document can be regenerated; both commands are pinned.
    """
    doc = _doc()
    assert (
        "`Basis` says how strongly the row is backed: **stated** — the cited quote "
        "names this method and gives its collapse, rename or deletion; **derived** — a "
        "doc gives the destination only for a namespace or wildcard covering this "
        "method, without naming it; **unbacked** — no doc gives a destination."
    ) in doc, "the Basis legend's definitions changed"
    assert "**This is the SDK half of the rename table.**" in doc
    assert "`docs/product/bridge-table.md` (Phase 0.1)" in doc
    assert "## Reproduce" in doc
    assert "uv run python tools/sdk_rename_table.py          # regenerate this file" in doc
    assert ("uv run python tools/sdk_rename_table.py --check  # verify, non-zero on "
            "drift") in doc
    # The structural reconciliation must name its 32 groups and 149 members, and say
    # how `backfill_v25` (the Archived-table member) reconciles to the 150-method surface.
    assert "partitions the surface into **32 groups** over **149** named members" in doc
    assert "`backfill_v25` is in its Archived table instead" in doc
    assert "150 = the 150-method surface" in doc


def test_table_header_rows_are_read() -> None:
    """The six table header rows are labels, and every one was mutable unread.

    A header that no longer names its column makes the table ambiguous while every
    parser — which reads data rows, not labels — stays green. Pinned verbatim.
    """
    doc = _doc()
    headers = [
        "| # | Method | Source | Group | Target | Basis | Citation |",
        "| Destination | Current methods | Count |",
        "| Target method | Status |",
        "| Method | Source | Why it has no destination |",
        "| Method | Part A carries | The other doc implies | Other doc's grouping |",
        "| Doc's name | Real method (if any) | Where the doc uses it |",
    ]
    missing = [h for h in headers if h not in doc]
    assert not missing, f"a table header row changed or was dropped: {missing}"
