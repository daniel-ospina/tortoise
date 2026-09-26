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
# The generator's default output path. It is NO LONGER COMMITTED (#5373) — every
# reader below goes through the session fixture, which rebinds this to a freshly
# rendered temp copy. Keeping the name `DOC` means the ~40 existing readers are
# unchanged; the artifact they read is now guaranteed current instead of
# guaranteed-possibly-stale.
DOC = ROOT / "docs" / "product" / "sdk-rename-table.md"
BETA = ROOT / "docs" / "product" / "beta-sdk-surface.md"
CANON = ROOT / "docs" / "product" / "canonical-sdk-methods.md"

_DOC_PATH: Path | None = None


def _render_doc() -> Path:
    """Render the rename table into a temp dir, once per session.

    The file is generated ON DEMAND because committing it was the defect (#5373): it
    is a function of `sdk.py` line numbers, so any two concurrent `sdk.py` PRs
    conflicted on it — 8 of the 44 unclean PRs measured 2026-09-26, and normalising
    `sdk.py:\\d+` made the two sides byte-identical. Rendering here is STRONGER than
    reading a committed copy: the asserted content cannot be a stale artifact, and
    the ~40 independent AST/doc oracles below still check every row.
    """
    global _DOC_PATH
    if _DOC_PATH is None:
        import tempfile

        out = Path(tempfile.mkdtemp(prefix="sdk-rename-table-")) / "sdk-rename-table.md"
        proc = subprocess.run(
            [sys.executable, str(GENERATOR), "--out", str(out)],
            cwd=ROOT, capture_output=True, text=True,
        )
        assert proc.returncode == 0, f"the generator failed:\n{proc.stderr}"
        _DOC_PATH = out
    return _DOC_PATH


@pytest.fixture(scope="session", autouse=True)
def _generated_doc() -> None:
    """Point `DOC` at the rendered copy before any test in this module reads it."""
    global DOC
    DOC = _render_doc()

# One Part A row: `| 1 | \`name\` | \`sdk.py:123\` | R1 | \`target\` | stated | citation |`.
# The citation cell is captured greedily to the final pipe so escaped (`\|`) pipes
# inside a quoted markdown row do not split it. The Target cell is either a backticked
# method name or an em-dash; the three axis cells (Lifecycle/Visibility/Delete) are a
# single lowercase word or an em-dash, and Basis is one of the four evidence values.
ROW_RE = re.compile(
    r"^\| \d+ \| `([a-z_][a-z0-9_]*)` \| `sdk\.py:(\d+)` \| ([A-Z]\d+|ARCHIVE) \| "
    r"(`([a-z_][a-z0-9_]*)`|—) \| ([a-z]+|—) \| ([a-z]+|—) \| "
    r"(stated|derived|unbacked|contested) \| ([a-z]+|—) \| (.*) \|$"
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
                "target": m.group(5) or "",
                "lifecycle": m.group(6), "visibility": m.group(7),
                "basis": m.group(8), "delete": m.group(9),
                "cite": m.group(10),
            })
    return rows


# Every citation's QUOTE TEXT, keyed by citation key. Pinning only `{method: target}`
# left the evidence free: a row could keep its target and swap in ANY other real
# sentence from the same document and stay `stated`, because `basis` is computed from
# the authored `named` set rather than from the quote itself. Row 68 then quoted
# `test_guard`'s disposition while claiming `update_memory_graph` — suite green.
CITES_ANCHOR_LITERAL: dict[str, tuple[str, str]] = {
    'maintenance': ('beta-sdk-surface.md', '| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface. |'),
    'n1_account': ('beta-sdk-surface.md', '| 28 | `get_organisation_account` | Read the account and the plan it is on |'),
    'n1_console': ('beta-sdk-surface.md', "| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. |"),
    'n2_count': ('beta-sdk-surface.md', '| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |'),
    'n2_keys': ('beta-sdk-surface.md', '| `graph_key_ids`, `graph_active_key_count` | 2 | Console diagnostics. Both fold into `list_keys`. |'),
    'n2_recording': ('beta-sdk-surface.md', "| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. |"),
    'n2_rename': ('beta-sdk-surface.md', '| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 `*_memory_graph*`. |'),
    'n3_members': ('beta-sdk-surface.md', '| 37 | `add_member` | Grant a person access to the account |'),
    'n4_keys': ('beta-sdk-surface.md', '| 34 | `create_key` | Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id | — | builder |'),
    'n5_invite': ('beta-sdk-surface.md', '| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. |'),
    'n6_signup': ('beta-sdk-surface.md', '| `signup_token_*` (3) | 3 | Operator-side agent self-signup — our provisioning, not product surface. |'),
    'p_graph_count': ('beta-sdk-surface.md', '| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |'),
    'p_set_name': ('beta-sdk-surface.md', '| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` | 3 | See "Provisioning" above. |'),
    'p_withdraw': ('beta-sdk-surface.md', '| `withdraw_knowledge` | 1 | **Never existed** — removed from the plan. Retraction is a field on `update_knowledge`. |'),
    'r1': ('beta-sdk-surface.md', '| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |'),
    'r1_canon': ('canonical-sdk-methods.md', '| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` |'),
    'r2': ('beta-sdk-surface.md', '| `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |'),
    'r3': ('beta-sdk-surface.md', '| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |'),
    'r3_canon': ('canonical-sdk-methods.md', '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` |'),
    'r3_context': ('beta-sdk-surface.md', '| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. |'),
    'r3_drop_subgraph': ('beta-sdk-surface.md', '**`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |'),
    'r3_restore': ('beta-sdk-surface.md', '| `restore_point_at` | → row 7 **`get_historical_knowledge`**. A **read**, not a write — it returns the version of a claim valid on a date and mutates nothing. |'),
    'r4': ('beta-sdk-surface.md', '| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |'),
    'r4_canon': ('canonical-sdk-methods.md', '| R4 | `get_entity` | #4 | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` |'),
    'r5': ('beta-sdk-surface.md', '| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |'),
    'r6': ('beta-sdk-surface.md', '| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` where they are orientation. The diagnostics are the held question above. |'),
    'r6_aliases': ('beta-sdk-surface.md', 'narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` | **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. |'),
    'r6_canon': ('canonical-sdk-methods.md', '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` |'),
    'r6_list_sources': ('beta-sdk-surface.md', "| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |"),
    'r6_test_guard': ('beta-sdk-surface.md', '| `test_guard` | **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK. |'),
    'r7': ('beta-sdk-surface.md', '| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |'),
    'r8': ('beta-sdk-surface.md', '| `events_poll` | → row 11 `poll_events`. |'),
    'r9': ('beta-sdk-surface.md', "| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. |"),
    't_n2': ('canonical-sdk-methods.md', '| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` |'),
    't_r3': ('canonical-sdk-methods.md', '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` |'),
    't_r5': ('canonical-sdk-methods.md', '| R5 | `explore_connections` | #5 | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` |'),
    't_r6': ('canonical-sdk-methods.md', '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` |'),
    't_w15': ('canonical-sdk-methods.md', '| W15 | `adjust_relationship` | #21 | `operator_action`, `mitigate_operator`, `annotate_operator` | keep, collapse |'),
    't_w8': ('canonical-sdk-methods.md', '| W8 | `manage_source_trust` | #14 | `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` | keep — **`get_source_reliability` writes** |'),
    'unchanged4': ('beta-sdk-surface.md', '> **Every name here is a target, not a description of today.**'),
    'w1': ('beta-sdk-surface.md', '| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`. The ontology models all of them as entities. |'),
    'w10': ('beta-sdk-surface.md', '| `file_human_approval` | 1 | → `record_decision`. |'),
    'w10_file_decision': ('beta-sdk-surface.md', '| `file_decision` | → rows 20/21 **`write_question`** + **`record_decision`**. It was filing a *question* and calling it a decision. |'),
    'w11': ('beta-sdk-surface.md', '| `update_point`, `update_entity` | 2 | → `update_knowledge`. |'),
    'w11_canon': ('canonical-sdk-methods.md', '| W11 | `revise_knowledge` | #17 | `update`, `update_point`, `update_entity`, `supersede`, `supersede_point`, `invalidate_point`, `retract_point`, `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` |'),
    'w11_lifecycle': ('beta-sdk-surface.md', "| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. |"),
    'w11_retract': ('beta-sdk-surface.md', "| `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. |"),
    'w11_supersede': ('beta-sdk-surface.md', '| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |'),
    'w12': ('beta-sdk-surface.md', '| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |'),
    'w12_canon': ('canonical-sdk-methods.md', '| W12 | `delete_knowledge` | #18 | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | keep, collapse |'),
    'w13_canon': ('canonical-sdk-methods.md', '| W13 | `stabilize_beliefs` | #19 | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` |'),
    'w15': ('beta-sdk-surface.md', '| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. |'),
    'w17_ulid': ('beta-sdk-surface.md', '| `ulid` | 1 | A ULID generator. Not a memory operation. |'),
    'w1_batch': ('beta-sdk-surface.md', '| `batch_create_points` | 1 | → `write_knowledge_batch`. |'),
    'w1_coup': ('canonical-sdk-methods.md', '| `create_or_update_point` → `create_point` |'),
    'w2': ('canonical-sdk-methods.md', '| W2 | `write_knowledge` | — | `ingest` |'),
    'w2_rename': ('canonical-sdk-methods.md', '> ⛔ **The SDK target is `docs/product/beta-sdk-surface.md` (40 methods), not this document.**'),
    'w3': ('canonical-sdk-methods.md', '| W3 | `register_source` | #11 | `create_source`, `complete_source` |'),
    'w3_cut': ('beta-sdk-surface.md', '| `complete_source` | 1 | **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes — and it has **zero callers in the repo**. |'),
    'w4': ('beta-sdk-surface.md', '| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources_from_directory`. |'),
    'w4_canon': ('canonical-sdk-methods.md', '| W4 | `index_files` | #12 | `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` |'),
    'w4_index_sessions': ('canonical-sdk-methods.md', '| `index_sessions` / `ingest_corpus` → `index_directory` |'),
    'w4_mine': ('beta-sdk-surface.md', '| `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. |'),
    'w4_rename': ('beta-sdk-surface.md', '| `index_sources` (bare) | 1 | Renamed → `index_sources_from_directory`, so the index/mine distinction is unmissable. |'),
    'w5': ('beta-sdk-surface.md', '- **The journal capability**'),
    'w6': ('beta-sdk-surface.md', "| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. |"),
    'w8': ('beta-sdk-surface.md', '| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |'),
    'w8_backfill': ('beta-sdk-surface.md', '| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. Run once, then dead code carrying a public promise. |'),
    'w9': ('beta-sdk-surface.md', '| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. |'),
    'w9_canon': ('canonical-sdk-methods.md', '| W9 | `link_entities` | #15 | `create_edge`, `create_derivation`, `link_source_to_entity`, `create_operator`, `create_direct_edge` |'),
}

CITATION_REGION_LITERAL: dict[str, str] = {
    'maintenance': '| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` | 4 | **Our maintenance.** Never product surface. |',
    'n1_account': '| 28 | `get_organisation_account` | Read the account and the plan it is on | — | admin |',
    'n1_console': "| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` | 5 | Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. |",
    'n2_count': '| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |',
    'n2_keys': '| `graph_key_ids`, `graph_active_key_count` | 2 | Console diagnostics. Both fold into `list_keys`. |',
    'n2_recording': "| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. |",
    'n2_rename': '| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` | → rows 30–33 `*_memory_graph*`. |',
    'n3_members': '| 37 | `add_member` | Grant a person access to the account | — | admin |',
    'n4_keys': '| 34 | `create_key` | Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id | — | builder |',
    'n5_invite': '| `invitation_*` (6) | 6 | The invite **UX** belongs to the console, where a human clicks it. |',
    'n6_signup': '| `signup_token_*` (3) | 3 | Operator-side agent self-signup — our provisioning, not product surface. |',
    'p_graph_count': '| `count_memory_graphs` | The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. |',
    'p_set_name': '| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` | 3 | See "Provisioning" above. |',
    'p_withdraw': '| `withdraw_knowledge` | 1 | **Never existed** — removed from the plan. Retraction is a field on `update_knowledge`. |',
    'r1': '| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |',
    'r1_canon': '| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` | keep, collapse |',
    'r2': '| `query`, `paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`. |',
    'r3': '| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |',
    'r3_canon': '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` | keep, collapse — absorbs the confidence reads and both provenance methods |',
    'r3_context': '| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` | 4 | → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. |',
    'r3_drop_subgraph': '| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". |',
    'r3_restore': '| `restore_point_at` | → row 7 **`get_historical_knowledge`**. A **read**, not a write — it returns the version of a claim valid on a date and mutates nothing. |',
    'r4': '| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) | ~8 | → `get_entity`, except where a genuinely different shape is returned. |',
    'r4_canon': '| R4 | `get_entity` | #4 | `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` | keep, collapse |',
    'r5': '| `traverse`, `expand_relationships`, `get_org_structure` | 3 | → `explore_connections`. |',
    'r6': '| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` where they are orientation. The diagnostics are the held question above. |',
    'r6_aliases': '| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` | **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. |',
    'r6_canon': '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` | keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability |',
    'r6_list_sources': "| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. |",
    'r6_test_guard': '| `test_guard` | **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK. |',
    'r7': '| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` | 3 | → `review_link_candidates`. |',
    'r8': '| `events_poll` | → row 11 `poll_events`. |',
    'r9': "| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. |",
    't_n2': '| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, `graph_restore`, `trash_graphs`, `graph_set_name`, `graph_set_recording`, `graph_key_ids`, `graph_active_key_count` |',
    't_r3': '| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` | keep, collapse — absorbs the confidence reads and both provenance methods |',
    't_r5': '| R5 | `explore_connections` | #5 | `expand_relationships`, `traverse`, `get_owned_entities`, `get_org_structure` | keep, collapse |',
    't_r6': '| R6 | `graph_overview` | #6 | `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` | keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability |',
    't_w15': '| W15 | `adjust_relationship` | #21 | `operator_action`, `mitigate_operator`, `annotate_operator` | keep, collapse |',
    't_w8': '| W8 | `manage_source_trust` | #14 | `assess_source`, `set_source_tier`, `get_source_reliability`, `backfill_sources` | keep — **`get_source_reliability` writes** |',
    'unchanged4': "> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the\n> current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix.\n> The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it.\nUntil it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every\n*current* tool to its destination but does not name the target's replacing name.",
    'w1': '| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` | 5 | Collapsed into `create_entity(type=)`. The ontology models all of them as entities. |',
    'w10': '| `file_human_approval` | 1 | → `record_decision`. |',
    'w10_file_decision': '| `file_decision` | → rows 20/21 **`write_question`** + **`record_decision`**. It was filing a *question* and calling it a decision. |',
    'w11': '| `update_point`, `update_entity` | 2 | → `update_knowledge`. |',
    'w11_canon': '| W11 | `revise_knowledge` | #17 | `update`, `update_point`, `update_entity`, `supersede`, `supersede_point`, `invalidate_point`, `retract_point`, `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | keep, collapse — **the widest group; see Open items** |',
    'w11_lifecycle': "| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` | 4 | Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. |",
    'w11_retract': "| `retract_point`, `invalidate_point` | 2 | → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. |",
    'w11_supersede': '| `supersede`, `supersede_point` | 2 | → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. |',
    'w12': '| `delete_point`, `delete_point_wrapped` | 2 | → `delete_knowledge`. |',
    'w12_canon': '| W12 | `delete_knowledge` | #18 | `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` | keep, collapse |',
    'w13_canon': '| W13 | `stabilize_beliefs` | #19 | `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` | keep — **`compute_confidence` mislabelled read** |',
    'w15': '| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. |',
    'w17_ulid': '| `ulid` | 1 | A ULID generator. Not a memory operation. |',
    'w1_batch': '| `batch_create_points` | 1 | → `write_knowledge_batch`. |',
    'w1_coup': '| `create_or_update_point` → `create_point` | `dedup` — a `**props` key popped with default **`False`** (not a declared parameter) | single-statement delegate |',
    'w2': '| W2 | `write_knowledge` | — | `ingest` | keep — **SDK-only name.** The batch call: one bundle writes points + entities + sources + connections atomically, with local `ref` labels so connections can address nodes created in the same call. Not to be called `ingest_bundle` (jargon) or `write_graph` (collides with `graph_overview` and the graph admin namespace) |',
    'w2_rename': '> ⛔ **The SDK target is `docs/product/beta-sdk-surface.md` (40 methods), not this document.**\n> Where the two disagree, that doc governs. They really do disagree: this sketch says\n> `write_knowledge` and `stabilize_beliefs` where the current target says\n> `write_knowledge_batch` and `refresh_confidence`. Note also that this file\'s second table\n> ("What we have that competitors do not") reuses the R/W/N labels for *different* groups —\n> cross-reference by method name, never by label.',
    'w3': '| W3 | `register_source` | #11 | `create_source`, `complete_source` | keep — **not foldable**: the URL is the node identity |',
    'w3_cut': '| `complete_source` | 1 | **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes — and it has **zero callers in the repo**. |',
    'w4': '| `ingest_corpus`, `index_file`, `session_index_health` | 3 | → `index_sources_from_directory`. |',
    'w4_canon': '| W4 | `index_files` | #12 | `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` | keep — **2 self-declared DEPRECATED** |',
    'w4_index_sessions': '| `index_sessions` / `ingest_corpus` → `index_directory` | `file_type` | both self-declared DEPRECATED in their own docstrings |',
    'w4_mine': '| `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. |',
    'w4_rename': '| `index_sources` (bare) | 1 | Renamed → `index_sources_from_directory`, so the index/mine distinction is unmissable. |',
    'w5': '- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the\n  **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room`\n  parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server**\n  today. **Filed post-beta** (issue to be created) and **unlisted** until then.',
    'w6': "| `capture_session` / `commit_session` | → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. |",
    'w8': '| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | → `manage_source_trust` for the setter; reads via `list_sources`. |',
    'w8_backfill': '| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` | 4 | One-shot migrations. Run once, then dead code carrying a public promise. |',
    'w9': '| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` | 4 | → `link_entities`, which dispatches on the relation. |',
    'w9_canon': '| W9 | `link_entities` | #15 | `create_edge`, `create_derivation`, `link_source_to_entity`, `create_operator`, `create_direct_edge` | keep, collapse — **one call, dispatching internally on `kind=`**: epistemic relations build a reified operator node, structural ones a bare edge. The caller never sees the split |',
}


# The COMPLETE row → (target, citation text) binding, for all 150 rows. Pinning the
# quotes by citation KEY was not enough: nothing pinned WHICH key a row selected, and
# where two keys share a `named` member (`w3`/`w3_cut`, `w4`/`w4_index_sessions`) a
# key swap kept the target, the basis AND the pinned quote set intact — so row 23
# could cite the `register_source` grouping as evidence for a DISCARDED disposition.
# Binding the rendered citation text per row closes the evidence-swap in both
# directions.
ROW_CITE_LITERAL: dict[str, tuple[str, str]] = {
    'annotate_ask_hits': ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    'annotate_operator': ('update_knowledge', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \\|”'),
    'apikey_create': ('create_key', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \\| — \\| builder \\|”'),
    'apikey_list': ('list_keys', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \\| — \\| builder \\|”'),
    'apikey_revoke': ('revoke_key', '`beta-sdk-surface.md` — “\\| 34 \\| `create_key` \\| Mint a credential scoped to one memory graph. **The credential carries the tenant** — the client does not pass a graph id \\| — \\| builder \\|”'),
    'apikey_verify': ('', "`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \\|”"),
    'approve_merge': ('approve_merge', "`beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.”"),
    'assess_source': ('manage_source_trust', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust` for the setter; reads via `list_sources`. \\|”'),
    'audit': ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview` where they are orientation. The diagnostics are the held question above. \\|”'),
    'backfill_about_entities': ('', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations. Run once, then dead code carrying a public promise. \\|”'),
    'backfill_sources': ('', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations. Run once, then dead code carrying a public promise. \\|”'),
    'backfill_v25': ('', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations. Run once, then dead code carrying a public promise. \\|”'),
    'batch_create_points': ('write_knowledge_batch', '`beta-sdk-surface.md` — “\\| `batch_create_points` \\| 1 \\| → `write_knowledge_batch`. \\|”'),
    'belief_timeline': ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \\|”'),
    'calibrate_summary': ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|”'),
    'calibration_passed': ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|”'),
    'capture_session': ('mine_knowledge_from_session', "`beta-sdk-surface.md` — “\\| `capture_session` / `commit_session` \\| → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. \\|”"),
    'check_structure': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'checkpoint': ('', '`beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.”'),
    'cleanup_expired_invitations': ('', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.** Never product surface. \\|”'),
    'close': ('close', "`beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.”"),
    'commit_session': ('mine_knowledge_from_session', "`beta-sdk-surface.md` — “\\| `capture_session` / `commit_session` \\| → row 16 `mine_knowledge_from_session`, one method. The backend is the target graph's configuration. \\|”"),
    'complete_source': ('', '`beta-sdk-surface.md` — “\\| `complete_source` \\| 1 \\| **Cut.** Its entire body populates `contentHash`, `version`, `externalId` — fields `register_source` already writes — and it has **zero callers in the repo**. \\|”'),
    'compute_confidence': ('refresh_confidence', '`canonical-sdk-methods.md` — “\\| W13 \\| `stabilize_beliefs` \\| #19 \\| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \\| keep — **`compute_confidence` mislabelled read** \\|”'),
    'compute_reputation': ('', '—'),
    'create_derivation': ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`, which dispatches on the relation. \\|”'),
    'create_direct_edge': ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`, which dispatches on the relation. \\|”'),
    'create_document': ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \\|”'),
    'create_edge': ('link_entities', '`canonical-sdk-methods.md` — “\\| W9 \\| `link_entities` \\| #15 \\| `create_edge`, `create_derivation`, `link_source_to_entity`, `create_operator`, `create_direct_edge` \\| keep, collapse — **one call, dispatching internally on `kind=`**: epistemic relations build a reified operator node, structural ones a bare edge. The caller never sees the split \\|”'),
    'create_entity': ('create_entity', "`beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.”"),
    'create_event': ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \\|”'),
    'create_object': ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \\|”'),
    'create_operator': ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`, which dispatches on the relation. \\|”'),
    'create_or_update_point': ('create_entity', '`canonical-sdk-methods.md` — “\\| `create_or_update_point` → `create_point` \\| `dedup` — a `**props` key popped with default **`False`** (not a declared parameter) \\| single-statement delegate \\|”'),
    'create_point': ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \\|”'),
    'create_source': ('register_source', '`canonical-sdk-methods.md` — “\\| W3 \\| `register_source` \\| #11 \\| `create_source`, `complete_source` \\| keep — **not foldable**: the URL is the node identity \\|”'),
    'create_subject': ('create_entity', '`beta-sdk-surface.md` — “\\| `create_subject`, `create_object`, `create_event`, `create_document`, `create_point` \\| 5 \\| Collapsed into `create_entity(type=)`. The ontology models all of them as entities. \\|”'),
    'delete': ('delete_knowledge', '`canonical-sdk-methods.md` — “\\| W12 \\| `delete_knowledge` \\| #18 \\| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \\| keep, collapse \\|”'),
    'delete_entity': ('delete_knowledge', '`canonical-sdk-methods.md` — “\\| W12 \\| `delete_knowledge` \\| #18 \\| `delete`, `delete_point`, `delete_entity`, `delete_point_wrapped` \\| keep, collapse \\|”'),
    'delete_point': ('delete_knowledge', '`beta-sdk-surface.md` — “\\| `delete_point`, `delete_point_wrapped` \\| 2 \\| → `delete_knowledge`. \\|”'),
    'delete_point_wrapped': ('delete_knowledge', '`beta-sdk-surface.md` — “\\| `delete_point`, `delete_point_wrapped` \\| 2 \\| → `delete_knowledge`. \\|”'),
    'diary_read': ('', '`beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.”'),
    'diary_write': ('', '`beta-sdk-surface.md` — “- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. They arrived in the **initial codebase commit** (`a02ab48c7`) with no design record, and their `wing` / `room` parameters appear **nowhere in `docs/ONTOLOGY.md`**. They are **live in the MCP server** today. **Filed post-beta** (issue to be created) and **unlisted** until then.”'),
    'dream': ('refresh_confidence', '`canonical-sdk-methods.md` — “\\| W13 \\| `stabilize_beliefs` \\| #19 \\| `dream`, `compute_confidence`, `compute_reputation`, `record_calibration` \\| keep — **`compute_confidence` mislabelled read** \\|”'),
    'dream_health_check': ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview` where they are orientation. The diagnostics are the held question above. \\|”'),
    'dream_health_state': ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview` where they are orientation. The diagnostics are the held question above. \\|”'),
    'events_poll': ('poll_events', '`beta-sdk-surface.md` — “\\| `events_poll` \\| → row 11 `poll_events`. \\|”'),
    'expand_relationships': ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    'file_decision': ('write_question', '`beta-sdk-surface.md` — “\\| `file_decision` \\| → rows 20/21 **`write_question`** + **`record_decision`**. It was filing a *question* and calling it a decision. \\|”'),
    'file_human_approval': ('record_decision', '`beta-sdk-surface.md` — “\\| `file_human_approval` \\| 1 \\| → `record_decision`. \\|”'),
    'get_confidence': ('check_confidence', '`canonical-sdk-methods.md` — “\\| R3 \\| `recall_beliefs` \\| #3 \\| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \\| keep, collapse — absorbs the confidence reads and both provenance methods \\|”'),
    'get_cross_lens_candidates': ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    'get_entity': ('get_entity', "`beta-sdk-surface.md` — “> **Every name here is a target, not a description of today.** Only **four** of the 40 exist in the > current SDK (`create_entity`, `get_entity`, `approve_merge`, `close`). The MCP column names the *target* tool. None of the 26 exists verbatim — every registered MCP tool carries a `tortoise_` prefix — and only **4** (`create_entity`, `get_entity`, `approve_merge`, `graph_set_recording`) have a prefixed equivalent. So it is **26 of 26 by name**, or **22 of 26** if you normalise the prefix. > The old→new mapping is a **Phase 0.3b deliverable and does not exist yet** — do not look for it. Until it lands, the only per-tool mapping is `docs/product/bridge-table.md`, which maps every *current* tool to its destination but does not name the target's replacing name.”"),
    'get_events': ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`, except where a genuinely different shape is returned. \\|”'),
    'get_org_structure': ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    'get_owned_entities': ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`, except where a genuinely different shape is returned. \\|”'),
    'get_point': ('get_entity', '`canonical-sdk-methods.md` — “\\| R4 \\| `get_entity` \\| #4 \\| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \\| keep, collapse \\|”'),
    'get_provenance_chain': ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`, except where a genuinely different shape is returned. \\|”'),
    'get_session': ('get_entity', '`beta-sdk-surface.md` — “\\| narrow readers (`get_session`, `get_events`, `get_owned_entities`, `get_provenance_chain`, …) \\| ~8 \\| → `get_entity`, except where a genuinely different shape is returned. \\|”'),
    'get_source_reliability': ('list_knowledge', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust` for the setter; reads via `list_sources`. \\|”'),
    'graph_active_key_count': ('list_keys', '`beta-sdk-surface.md` — “\\| `graph_key_ids`, `graph_active_key_count` \\| 2 \\| Console diagnostics. Both fold into `list_keys`. \\|”'),
    'graph_count': ('list_memory_graphs', '`beta-sdk-surface.md` — “\\| `count_memory_graphs` \\| The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. \\|”'),
    'graph_delete': ('delete_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    'graph_key_ids': ('list_keys', '`beta-sdk-surface.md` — “\\| `graph_key_ids`, `graph_active_key_count` \\| 2 \\| Console diagnostics. Both fold into `list_keys`. \\|”'),
    'graph_list': ('list_memory_graphs', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    'graph_restore': ('restore_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    'graph_set_name': ('update_memory_graph', '`beta-sdk-surface.md` — “\\| `graph_delete`, `graph_restore`, `graph_list`, `graph_set_name` \\| → rows 30–33 `*_memory_graph*`. \\|”'),
    'graph_set_recording': ('update_memory_graph', "`beta-sdk-surface.md` — “\\| ~~`graph_set_recording`~~ (SDK method) \\| 1 \\| **Discarded as an SDK method, KEPT as an MCP tool.** It is a per-field setter, the same shape as `set_memory_graph_name`/`set_memory_graph_backend`, which were deleted so that fields go on create plus one partial update. The override therefore folds into **`update_memory_graph`** (row 30) — while the **MCP tool** `graph_set_recording` survives, because it is an agent's only in-MCP recovery from the capture 409. \\|”"),
    'index_directory': ('index_sources_from_directory', '`canonical-sdk-methods.md` — “\\| W4 \\| `index_files` \\| #12 \\| `index_file`, `index_directory`, `ingest_corpus`, `index_sessions`, `mine_corpus`, `reconcile_sessions`, `session_index_health`, `backfill_about_entities` \\| keep — **2 self-declared DEPRECATED** \\|”'),
    'index_file': ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    'index_sessions': ('index_sources_from_directory', '`canonical-sdk-methods.md` — “\\| `index_sessions` / `ingest_corpus` → `index_directory` \\| `file_type` \\| both self-declared DEPRECATED in their own docstrings \\|”'),
    'ingest': ('write_knowledge_batch', '`canonical-sdk-methods.md` — “\\| W2 \\| `write_knowledge` \\| — \\| `ingest` \\| keep — **SDK-only name.** The batch call: one bundle writes points + entities + sources + connections atomically, with local `ref` labels so connections can address nodes created in the same call. Not to be called `ingest_bundle` (jargon) or `write_graph` (collides with `graph_overview` and the graph admin namespace) \\|”'),
    'ingest_corpus': ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    'invalidate_point': ('update_knowledge', "`beta-sdk-surface.md` — “\\| `retract_point`, `invalidate_point` \\| 2 \\| → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. \\|”"),
    'invitation_accept': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'invitation_create': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'invitation_get_by_id': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'invitation_get_by_token': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'invitation_list': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'invitation_revoke': ('', '`beta-sdk-surface.md` — “\\| `invitation_*` (6) \\| 6 \\| The invite **UX** belongs to the console, where a human clicks it. \\|”'),
    'issue_insight': ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    'link_source_to_entity': ('link_entities', '`beta-sdk-surface.md` — “\\| `create_operator`, `create_direct_edge`, `create_derivation`, `link_source_to_entity` \\| 4 \\| → `link_entities`, which dispatches on the relation. \\|”'),
    'list_batch': ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_batch`, `list_batches` \\| 2 \\| → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. \\|”"),
    'list_batches': ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_batch`, `list_batches` \\| 2 \\| → `list_knowledge(kind='batch')`. The batch contents come back inline in the bounded, paged page. \\|”"),
    'list_dedup_candidates': ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    'list_drafts': ('list_knowledge', "`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. \\|”"),
    'list_graphs': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'list_namespaces': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'list_pointkinds': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'list_relations': ('graph_overview', '`canonical-sdk-methods.md` — “\\| R6 \\| `graph_overview` \\| #6 \\| `status`, `taxonomy`, `list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, `list_relations`, `list_topics`, `list_graphs`, `stale_points`, `summarize_structure`, `check_structure`, `audit`, `validate_domain`, `dream_health_check`, `dream_health_state`, `test_guard` \\| keep, collapse — `test_guard` is test infrastructure kept for the safety guard, not a capability \\|”'),
    'list_sources': ('list_knowledge', "`beta-sdk-surface.md` — “\\| `list_sources` \\| **Not discarded.** Present at `tortoise/sdk.py` with an MCP tool and a CLI command (`tortoise/__main__.py`), and it is covered by `tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it asks stays first-class and gains the credibility tier; it no longer needs its own method. \\|”"),
    'list_tags': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'list_topics': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'membership_create': ('add_member', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\| — \\| admin \\|”'),
    'membership_delete': ('remove_member', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\| — \\| admin \\|”'),
    'membership_get': ('', "`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \\|”"),
    'membership_list': ('list_members', '`beta-sdk-surface.md` — “\\| 37 \\| `add_member` \\| Grant a person access to the account \\| — \\| admin \\|”'),
    'membership_update_role': ('', "`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \\|”"),
    'migrate_orgs_to_registry': ('', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.** Never product surface. \\|”'),
    'mine_corpus': ('mine_knowledge_from_directory', '`beta-sdk-surface.md` — “\\| `mine_corpus` \\| 1 \\| → `mine_knowledge_from_directory`. It is the **batch form of `mine_knowledge_from_session`**, not a kind of indexing. \\|”'),
    'mitigate_operator': ('adjust_relationship', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \\|”'),
    'operator_action': ('adjust_relationship', '`beta-sdk-surface.md` — “\\| `mitigate_operator`, `operator_action`, `annotate_operator` \\| 3 \\| → `adjust_relationship` for strength, `update_knowledge` for annotation. `operator_action(**kwargs)` currently **accepts and silently ignores** `credibility` — a bug. \\|”'),
    'org_create': ('', '—'),
    'org_delete': ('', "`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \\|”"),
    'org_get': ('get_organisation_account', '`beta-sdk-surface.md` — “\\| 28 \\| `get_organisation_account` \\| Read the account and the plan it is on \\| — \\| admin \\|”'),
    'org_list': ('get_organisation_account', '`beta-sdk-surface.md` — “\\| 28 \\| `get_organisation_account` \\| Read the account and the plan it is on \\| — \\| admin \\|”'),
    'org_update': ('', "`beta-sdk-surface.md` — “\\| `org_update`, `org_delete`, `membership_get`, `membership_update_role`, `apikey_verify` \\| 5 \\| Console plumbing. `org_delete` is **settled**: an end-customer must never be able to delete the builder's account. **The builder's own account closure is a console operation** — not in the SDK. \\|”"),
    'paginated_query': ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    'promote_point': ('update_knowledge', "`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. \\|”"),
    'provenance': ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \\|”'),
    'quarantine_batch': ('update_knowledge', "`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. \\|”"),
    'query': ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    'query_points_by_tag': ('list_knowledge', '`beta-sdk-surface.md` — “\\| `query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`. \\|”'),
    'recall_gaps': ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|”'),
    'recall_state': ('check_confidence', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|”'),
    'recall_subgraph': ('', '`beta-sdk-surface.md` — “\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|”'),
    'reconcile_sessions': ('', '`beta-sdk-surface.md` — “\\| `backfill_v25`, `backfill_sources`, `backfill_about_entities`, `reconcile_sessions` \\| 4 \\| One-shot migrations. Run once, then dead code carrying a public promise. \\|”'),
    'record_calibration': ('', '—'),
    'resolve_id': ('get_entity', '`canonical-sdk-methods.md` — “\\| R4 \\| `get_entity` \\| #4 \\| `get_point`, `get_entity`, `get_session`, `get_events`, `resolve_id` \\| keep, collapse \\|”'),
    'restore_point_at': ('get_historical_knowledge', '`beta-sdk-surface.md` — “\\| `restore_point_at` \\| → row 7 **`get_historical_knowledge`**. A **read**, not a write — it returns the version of a claim valid on a date and mutates nothing. \\|”'),
    'retract_point': ('update_knowledge', "`beta-sdk-surface.md` — “\\| `retract_point`, `invalidate_point` \\| 2 \\| → fields on `update_knowledge`. **Zep's shape:** retraction is `invalid_at`/`expired_at` on the existing update, not a separate verb. \\|”"),
    'retrieval_legs': ('check_confidence', '`canonical-sdk-methods.md` — “\\| R3 \\| `recall_beliefs` \\| #3 \\| `recall_state`, `recall_gaps`, `recall_subgraph`, `retrieval_legs`, `volunteer_context`, `session_context`, `get_confidence`, `calibrate_summary`, `calibration_passed`, `get_provenance_chain`, `provenance`, `belief_timeline`, `restore_point_at` \\| keep, collapse — absorbs the confidence reads and both provenance methods \\|”'),
    'review_connections': ('review_link_candidates', '`beta-sdk-surface.md` — “\\| `review_connections`, `get_cross_lens_candidates`, `list_dedup_candidates` \\| 3 \\| → `review_link_candidates`. \\|”'),
    'search_sessions': ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    'session_context': ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \\|”'),
    'session_index_health': ('index_sources_from_directory', '`beta-sdk-surface.md` — “\\| `ingest_corpus`, `index_file`, `session_index_health` \\| 3 \\| → `index_sources_from_directory`. \\|”'),
    'set_point_baseline': ('update_knowledge', "`beta-sdk-surface.md` — “\\| `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| 4 \\| Lifecycle and confidence **state** — a **field on `update_knowledge`** (promote, baseline, quarantine), selected by a **filter on `list_knowledge(kind=…, status=…)`** (drafts). No separate verb. `promote_point` also promotes its incident operators and carries the approval gate, so it is not `update_point(status='live')`. \\|”"),
    'set_source_tier': ('manage_source_trust', '`beta-sdk-surface.md` — “\\| `assess_source`, `set_source_tier`, `get_source_reliability` \\| 3 \\| → `manage_source_trust` for the setter; reads via `list_sources`. \\|”'),
    'signup_token_lookup': ('', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup — our provisioning, not product surface. \\|”'),
    'signup_token_recover': ('', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup — our provisioning, not product surface. \\|”'),
    'signup_token_revoke': ('', '`beta-sdk-surface.md` — “\\| `signup_token_*` (3) \\| 3 \\| Operator-side agent self-signup — our provisioning, not product surface. \\|”'),
    'stale_points': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'status': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'suggest_entry_points': ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    'summarize_structure': ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview` where they are orientation. The diagnostics are the held question above. \\|”'),
    'supersede': ('supersede_knowledge', '`beta-sdk-surface.md` — “\\| `supersede`, `supersede_point` \\| 2 \\| → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. \\|”'),
    'supersede_point': ('supersede_knowledge', '`beta-sdk-surface.md` — “\\| `supersede`, `supersede_point` \\| 2 \\| → `supersede_knowledge`. They also **disagree** — `supersede_point` carries a `valid_from` the other silently drops. \\|”'),
    'sweep_invite_ghost_memberships': ('', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.** Never product surface. \\|”'),
    'taxonomy': ('graph_overview', '`beta-sdk-surface.md` — “\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|”'),
    'test_guard': ('', '`beta-sdk-surface.md` — “\\| `test_guard` \\| **Kept and relocated.** It guards the production-wipe incident, so the code must survive — but it is *test infrastructure* and moves out of the product SDK. \\|”'),
    'topic_summarize': ('search_knowledge', '`beta-sdk-surface.md` — “\\| `search_sessions`, `suggest_entry_points`, `topic_summarize`, `issue_insight`, `annotate_ask_hits` \\| 5 \\| → `search_knowledge`. \\|”'),
    'tortoise_fts_query': ('search_knowledge', '`canonical-sdk-methods.md` — “\\| R1 \\| `search_knowledge` \\| #1 \\| `tortoise_fts_query`, `suggest_entry_points`, `search_sessions`, `issue_insight`, `topic_summarize`, `annotate_ask_hits` \\| keep, collapse \\|”'),
    'trash_graphs': ('', '`beta-sdk-surface.md` — “\\| `trash_graphs`, `migrate_orgs_to_registry`, `cleanup_expired_invitations`, `sweep_invite_ghost_memberships` \\| 4 \\| **Our maintenance.** Never product surface. \\|”'),
    'traverse': ('explore_connections', '`beta-sdk-surface.md` — “\\| `traverse`, `expand_relationships`, `get_org_structure` \\| 3 \\| → `explore_connections`. \\|”'),
    'ulid': ('', '`beta-sdk-surface.md` — “\\| `ulid` \\| 1 \\| A ULID generator. Not a memory operation. \\|”'),
    'update': ('update_knowledge', '`canonical-sdk-methods.md` — “\\| W11 \\| `revise_knowledge` \\| #17 \\| `update`, `update_point`, `update_entity`, `supersede`, `supersede_point`, `invalidate_point`, `retract_point`, `promote_point`, `set_point_baseline`, `list_drafts`, `quarantine_batch` \\| keep, collapse — **the widest group; see Open items** \\|”'),
    'update_entity': ('update_knowledge', '`beta-sdk-surface.md` — “\\| `update_point`, `update_entity` \\| 2 \\| → `update_knowledge`. \\|”'),
    'update_point': ('update_knowledge', '`beta-sdk-surface.md` — “\\| `update_point`, `update_entity` \\| 2 \\| → `update_knowledge`. \\|”'),
    'validate_domain': ('graph_overview', '`beta-sdk-surface.md` — “\\| `audit`, `validate_domain`, `summarize_structure`, `dream_health_check`, `dream_health_state` \\| ~5 \\| → `graph_overview` where they are orientation. The diagnostics are the held question above. \\|”'),
    'volunteer_context': ('check_confidence', '`beta-sdk-surface.md` — “\\| `provenance`, `belief_timeline`, `session_context`, `volunteer_context` \\| 4 \\| → `check_confidence` where they are confidence context; `poll_events` where they are a timeline. \\|”'),
}


def _doc() -> str:
    """The generated document, read fresh — never cached across tests."""
    return DOC.read_text(encoding="utf-8")


def _find_quote(text: str, quote: str) -> re.Match | None:
    """Locate `quote` in `text`, tolerating the source's line wrapping.

    A quoted markdown bullet wraps across source lines; the rendered cell collapses
    that whitespace to single spaces, so an exact substring test would miss it. The
    lookup is whitespace-flexible but every other character must still match, so a
    reworded source still fails to resolve.
    """
    return re.compile(r"\s+".join(re.escape(t) for t in quote.split())).search(text)


def row_destination(r: dict) -> str:
    """A row's destination on one vocabulary: the target name, or its disposition key.

    Mirrors the generator's `_destination`: an UNCHANGED row carries its own name as the
    target; a no-destination row is identified by the disposition its axes express.
    """
    if r["target"]:
        return r["target"]
    if r["basis"] == "unbacked":
        return "UNBACKED"
    if r["basis"] == "contested":
        return "CONTESTED"
    if r["visibility"] == "unlisted":
        return "UNLISTED"
    if r["delete"] == "delete":
        return "DISCARDED"
    return "RELOCATED"


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
    """`--check` must be clean against the render it just produced, and stable.

    It no longer compares a COMMITTED copy (there is none, #5373) — it asserts the
    render is DETERMINISTIC, which is what makes `--check` meaningful for a local
    copy at all. The drifted-copy test below still proves `--check` reds.
    """
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check", "--out", str(_render_doc())],
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
    'annotate_ask_hits': 'search_knowledge',
    'annotate_operator': 'update_knowledge',
    'apikey_create': 'create_key',
    'apikey_list': 'list_keys',
    'apikey_revoke': 'revoke_key',
    'apikey_verify': '',
    'approve_merge': 'approve_merge',
    'assess_source': 'manage_source_trust',
    'audit': 'graph_overview',
    'backfill_about_entities': '',
    'backfill_sources': '',
    'backfill_v25': '',
    'batch_create_points': 'write_knowledge_batch',
    'belief_timeline': 'check_confidence',
    'calibrate_summary': 'check_confidence',
    'calibration_passed': 'check_confidence',
    'capture_session': 'mine_knowledge_from_session',
    'check_structure': 'graph_overview',
    'checkpoint': '',
    'cleanup_expired_invitations': '',
    'close': 'close',
    'commit_session': 'mine_knowledge_from_session',
    'complete_source': '',
    'compute_confidence': 'refresh_confidence',
    'compute_reputation': '',
    'create_derivation': 'link_entities',
    'create_direct_edge': 'link_entities',
    'create_document': 'create_entity',
    'create_edge': 'link_entities',
    'create_entity': 'create_entity',
    'create_event': 'create_entity',
    'create_object': 'create_entity',
    'create_operator': 'link_entities',
    'create_or_update_point': 'create_entity',
    'create_point': 'create_entity',
    'create_source': 'register_source',
    'create_subject': 'create_entity',
    'delete': 'delete_knowledge',
    'delete_entity': 'delete_knowledge',
    'delete_point': 'delete_knowledge',
    'delete_point_wrapped': 'delete_knowledge',
    'diary_read': '',
    'diary_write': '',
    'dream': 'refresh_confidence',
    'dream_health_check': 'graph_overview',
    'dream_health_state': 'graph_overview',
    'events_poll': 'poll_events',
    'expand_relationships': 'explore_connections',
    'file_decision': 'write_question',
    'file_human_approval': 'record_decision',
    'get_confidence': 'check_confidence',
    'get_cross_lens_candidates': 'review_link_candidates',
    'get_entity': 'get_entity',
    'get_events': 'get_entity',
    'get_org_structure': 'explore_connections',
    'get_owned_entities': 'get_entity',
    'get_point': 'get_entity',
    'get_provenance_chain': 'get_entity',
    'get_session': 'get_entity',
    'get_source_reliability': 'list_knowledge',
    'graph_active_key_count': 'list_keys',
    'graph_count': 'list_memory_graphs',
    'graph_delete': 'delete_memory_graph',
    'graph_key_ids': 'list_keys',
    'graph_list': 'list_memory_graphs',
    'graph_restore': 'restore_memory_graph',
    'graph_set_name': 'update_memory_graph',
    'graph_set_recording': 'update_memory_graph',
    'index_directory': 'index_sources_from_directory',
    'index_file': 'index_sources_from_directory',
    'index_sessions': 'index_sources_from_directory',
    'ingest': 'write_knowledge_batch',
    'ingest_corpus': 'index_sources_from_directory',
    'invalidate_point': 'update_knowledge',
    'invitation_accept': '',
    'invitation_create': '',
    'invitation_get_by_id': '',
    'invitation_get_by_token': '',
    'invitation_list': '',
    'invitation_revoke': '',
    'issue_insight': 'search_knowledge',
    'link_source_to_entity': 'link_entities',
    'list_batch': 'list_knowledge',
    'list_batches': 'list_knowledge',
    'list_dedup_candidates': 'review_link_candidates',
    'list_drafts': 'list_knowledge',
    'list_graphs': 'graph_overview',
    'list_namespaces': 'graph_overview',
    'list_pointkinds': 'graph_overview',
    'list_relations': 'graph_overview',
    'list_sources': 'list_knowledge',
    'list_tags': 'graph_overview',
    'list_topics': 'graph_overview',
    'membership_create': 'add_member',
    'membership_delete': 'remove_member',
    'membership_get': '',
    'membership_list': 'list_members',
    'membership_update_role': '',
    'migrate_orgs_to_registry': '',
    'mine_corpus': 'mine_knowledge_from_directory',
    'mitigate_operator': 'adjust_relationship',
    'operator_action': 'adjust_relationship',
    'org_create': '',
    'org_delete': '',
    'org_get': 'get_organisation_account',
    'org_list': 'get_organisation_account',
    'org_update': '',
    'paginated_query': 'list_knowledge',
    'promote_point': 'update_knowledge',
    'provenance': 'check_confidence',
    'quarantine_batch': 'update_knowledge',
    'query': 'list_knowledge',
    'query_points_by_tag': 'list_knowledge',
    'recall_gaps': 'check_confidence',
    'recall_state': 'check_confidence',
    'recall_subgraph': '',
    'reconcile_sessions': '',
    'record_calibration': '',
    'resolve_id': 'get_entity',
    'restore_point_at': 'get_historical_knowledge',
    'retract_point': 'update_knowledge',
    'retrieval_legs': 'check_confidence',
    'review_connections': 'review_link_candidates',
    'search_sessions': 'search_knowledge',
    'session_context': 'check_confidence',
    'session_index_health': 'index_sources_from_directory',
    'set_point_baseline': 'update_knowledge',
    'set_source_tier': 'manage_source_trust',
    'signup_token_lookup': '',
    'signup_token_recover': '',
    'signup_token_revoke': '',
    'stale_points': 'graph_overview',
    'status': 'graph_overview',
    'suggest_entry_points': 'search_knowledge',
    'summarize_structure': 'graph_overview',
    'supersede': 'supersede_knowledge',
    'supersede_point': 'supersede_knowledge',
    'sweep_invite_ghost_memberships': '',
    'taxonomy': 'graph_overview',
    'test_guard': '',
    'topic_summarize': 'search_knowledge',
    'tortoise_fts_query': 'search_knowledge',
    'trash_graphs': '',
    'traverse': 'explore_connections',
    'ulid': '',
    'update': 'update_knowledge',
    'update_entity': 'update_knowledge',
    'update_point': 'update_knowledge',
    'validate_domain': 'graph_overview',
    'volunteer_context': 'check_confidence',
}


AXES_LITERAL: dict[str, tuple[str, str, str, str]] = {
    'annotate_ask_hits': ('deprecated', 'public', 'stated', 'retain'),
    'annotate_operator': ('deprecated', 'public', 'stated', 'retain'),
    'apikey_create': ('deprecated', 'public', 'derived', 'retain'),
    'apikey_list': ('deprecated', 'public', 'derived', 'retain'),
    'apikey_revoke': ('deprecated', 'public', 'derived', 'retain'),
    'apikey_verify': ('removed', 'internal', 'stated', 'delete'),
    'approve_merge': ('stable', 'public', 'stated', 'retain'),
    'assess_source': ('deprecated', 'public', 'stated', 'retain'),
    'audit': ('deprecated', 'public', 'stated', 'retain'),
    'backfill_about_entities': ('removed', 'internal', 'stated', 'delete'),
    'backfill_sources': ('removed', 'internal', 'stated', 'delete'),
    'backfill_v25': ('removed', 'internal', 'stated', 'delete'),
    'batch_create_points': ('deprecated', 'public', 'stated', 'retain'),
    'belief_timeline': ('deprecated', 'public', 'stated', 'retain'),
    'calibrate_summary': ('deprecated', 'public', 'stated', 'retain'),
    'calibration_passed': ('deprecated', 'public', 'stated', 'retain'),
    'capture_session': ('deprecated', 'public', 'stated', 'retain'),
    'check_structure': ('deprecated', 'public', 'stated', 'retain'),
    'checkpoint': ('stable', 'unlisted', 'stated', 'retain'),
    'cleanup_expired_invitations': ('removed', 'internal', 'stated', 'delete'),
    'close': ('stable', 'public', 'stated', 'retain'),
    'commit_session': ('deprecated', 'public', 'stated', 'retain'),
    'complete_source': ('removed', 'internal', 'stated', 'delete'),
    'compute_confidence': ('deprecated', 'public', 'stated', 'retain'),
    'compute_reputation': ('—', '—', 'unbacked', '—'),
    'create_derivation': ('deprecated', 'public', 'stated', 'retain'),
    'create_direct_edge': ('deprecated', 'public', 'stated', 'retain'),
    'create_document': ('deprecated', 'public', 'stated', 'retain'),
    'create_edge': ('deprecated', 'public', 'stated', 'retain'),
    'create_entity': ('stable', 'public', 'stated', 'retain'),
    'create_event': ('deprecated', 'public', 'stated', 'retain'),
    'create_object': ('deprecated', 'public', 'stated', 'retain'),
    'create_operator': ('deprecated', 'public', 'stated', 'retain'),
    'create_or_update_point': ('deprecated', 'public', 'stated', 'retain'),
    'create_point': ('deprecated', 'public', 'stated', 'retain'),
    'create_source': ('deprecated', 'public', 'stated', 'retain'),
    'create_subject': ('deprecated', 'public', 'stated', 'retain'),
    'delete': ('deprecated', 'public', 'stated', 'retain'),
    'delete_entity': ('deprecated', 'public', 'stated', 'retain'),
    'delete_point': ('deprecated', 'public', 'stated', 'retain'),
    'delete_point_wrapped': ('deprecated', 'public', 'stated', 'retain'),
    'diary_read': ('stable', 'unlisted', 'stated', 'retain'),
    'diary_write': ('stable', 'unlisted', 'stated', 'retain'),
    'dream': ('deprecated', 'public', 'stated', 'retain'),
    'dream_health_check': ('deprecated', 'public', 'stated', 'retain'),
    'dream_health_state': ('deprecated', 'public', 'stated', 'retain'),
    'events_poll': ('deprecated', 'public', 'stated', 'retain'),
    'expand_relationships': ('deprecated', 'public', 'stated', 'retain'),
    'file_decision': ('deprecated', 'public', 'stated', 'retain'),
    'file_human_approval': ('deprecated', 'public', 'stated', 'retain'),
    'get_confidence': ('deprecated', 'public', 'stated', 'retain'),
    'get_cross_lens_candidates': ('deprecated', 'public', 'stated', 'retain'),
    'get_entity': ('stable', 'public', 'stated', 'retain'),
    'get_events': ('deprecated', 'public', 'stated', 'retain'),
    'get_org_structure': ('deprecated', 'public', 'stated', 'retain'),
    'get_owned_entities': ('deprecated', 'public', 'stated', 'retain'),
    'get_point': ('deprecated', 'public', 'stated', 'retain'),
    'get_provenance_chain': ('deprecated', 'public', 'stated', 'retain'),
    'get_session': ('deprecated', 'public', 'stated', 'retain'),
    'get_source_reliability': ('deprecated', 'public', 'stated', 'retain'),
    'graph_active_key_count': ('deprecated', 'public', 'stated', 'retain'),
    'graph_count': ('deprecated', 'public', 'derived', 'retain'),
    'graph_delete': ('deprecated', 'public', 'stated', 'retain'),
    'graph_key_ids': ('deprecated', 'public', 'stated', 'retain'),
    'graph_list': ('deprecated', 'public', 'stated', 'retain'),
    'graph_restore': ('deprecated', 'public', 'stated', 'retain'),
    'graph_set_name': ('deprecated', 'public', 'stated', 'retain'),
    'graph_set_recording': ('deprecated', 'public', 'stated', 'retain'),
    'index_directory': ('deprecated', 'public', 'stated', 'retain'),
    'index_file': ('deprecated', 'public', 'stated', 'retain'),
    'index_sessions': ('deprecated', 'public', 'stated', 'retain'),
    'ingest': ('deprecated', 'public', 'stated', 'retain'),
    'ingest_corpus': ('deprecated', 'public', 'stated', 'retain'),
    'invalidate_point': ('deprecated', 'public', 'stated', 'retain'),
    'invitation_accept': ('removed', 'internal', 'derived', 'delete'),
    'invitation_create': ('removed', 'internal', 'derived', 'delete'),
    'invitation_get_by_id': ('removed', 'internal', 'derived', 'delete'),
    'invitation_get_by_token': ('removed', 'internal', 'derived', 'delete'),
    'invitation_list': ('removed', 'internal', 'derived', 'delete'),
    'invitation_revoke': ('removed', 'internal', 'derived', 'delete'),
    'issue_insight': ('deprecated', 'public', 'stated', 'retain'),
    'link_source_to_entity': ('deprecated', 'public', 'stated', 'retain'),
    'list_batch': ('deprecated', 'public', 'stated', 'retain'),
    'list_batches': ('deprecated', 'public', 'stated', 'retain'),
    'list_dedup_candidates': ('deprecated', 'public', 'stated', 'retain'),
    'list_drafts': ('deprecated', 'public', 'stated', 'retain'),
    'list_graphs': ('deprecated', 'public', 'stated', 'retain'),
    'list_namespaces': ('deprecated', 'public', 'stated', 'retain'),
    'list_pointkinds': ('deprecated', 'public', 'stated', 'retain'),
    'list_relations': ('deprecated', 'public', 'stated', 'retain'),
    'list_sources': ('deprecated', 'public', 'stated', 'retain'),
    'list_tags': ('deprecated', 'public', 'stated', 'retain'),
    'list_topics': ('deprecated', 'public', 'stated', 'retain'),
    'membership_create': ('deprecated', 'public', 'derived', 'retain'),
    'membership_delete': ('deprecated', 'public', 'derived', 'retain'),
    'membership_get': ('removed', 'internal', 'stated', 'delete'),
    'membership_list': ('deprecated', 'public', 'derived', 'retain'),
    'membership_update_role': ('removed', 'internal', 'stated', 'delete'),
    'migrate_orgs_to_registry': ('removed', 'internal', 'stated', 'delete'),
    'mine_corpus': ('deprecated', 'public', 'stated', 'retain'),
    'mitigate_operator': ('deprecated', 'public', 'stated', 'retain'),
    'operator_action': ('deprecated', 'public', 'stated', 'retain'),
    'org_create': ('—', '—', 'unbacked', '—'),
    'org_delete': ('removed', 'internal', 'stated', 'delete'),
    'org_get': ('deprecated', 'public', 'derived', 'retain'),
    'org_list': ('deprecated', 'public', 'derived', 'retain'),
    'org_update': ('removed', 'internal', 'stated', 'delete'),
    'paginated_query': ('deprecated', 'public', 'stated', 'retain'),
    'promote_point': ('deprecated', 'public', 'stated', 'retain'),
    'provenance': ('deprecated', 'public', 'stated', 'retain'),
    'quarantine_batch': ('deprecated', 'public', 'stated', 'retain'),
    'query': ('deprecated', 'public', 'stated', 'retain'),
    'query_points_by_tag': ('deprecated', 'public', 'stated', 'retain'),
    'recall_gaps': ('deprecated', 'public', 'stated', 'retain'),
    'recall_state': ('deprecated', 'public', 'stated', 'retain'),
    'recall_subgraph': ('removed', 'internal', 'stated', 'delete'),
    'reconcile_sessions': ('removed', 'internal', 'stated', 'delete'),
    'record_calibration': ('—', '—', 'unbacked', '—'),
    'resolve_id': ('deprecated', 'public', 'stated', 'retain'),
    'restore_point_at': ('deprecated', 'public', 'stated', 'retain'),
    'retract_point': ('deprecated', 'public', 'stated', 'retain'),
    'retrieval_legs': ('deprecated', 'public', 'stated', 'retain'),
    'review_connections': ('deprecated', 'public', 'stated', 'retain'),
    'search_sessions': ('deprecated', 'public', 'stated', 'retain'),
    'session_context': ('deprecated', 'public', 'stated', 'retain'),
    'session_index_health': ('deprecated', 'public', 'stated', 'retain'),
    'set_point_baseline': ('deprecated', 'public', 'stated', 'retain'),
    'set_source_tier': ('deprecated', 'public', 'stated', 'retain'),
    'signup_token_lookup': ('removed', 'internal', 'derived', 'delete'),
    'signup_token_recover': ('removed', 'internal', 'derived', 'delete'),
    'signup_token_revoke': ('removed', 'internal', 'derived', 'delete'),
    'stale_points': ('deprecated', 'public', 'derived', 'retain'),
    'status': ('deprecated', 'public', 'stated', 'retain'),
    'suggest_entry_points': ('deprecated', 'public', 'stated', 'retain'),
    'summarize_structure': ('deprecated', 'public', 'stated', 'retain'),
    'supersede': ('deprecated', 'public', 'stated', 'retain'),
    'supersede_point': ('deprecated', 'public', 'stated', 'retain'),
    'sweep_invite_ghost_memberships': ('removed', 'internal', 'stated', 'delete'),
    'taxonomy': ('deprecated', 'public', 'stated', 'retain'),
    'test_guard': ('stable', 'internal', 'stated', 'retain'),
    'topic_summarize': ('deprecated', 'public', 'stated', 'retain'),
    'tortoise_fts_query': ('deprecated', 'public', 'stated', 'retain'),
    'trash_graphs': ('removed', 'internal', 'stated', 'delete'),
    'traverse': ('deprecated', 'public', 'stated', 'retain'),
    'ulid': ('removed', 'internal', 'stated', 'delete'),
    'update': ('deprecated', 'public', 'stated', 'retain'),
    'update_entity': ('deprecated', 'public', 'stated', 'retain'),
    'update_point': ('deprecated', 'public', 'stated', 'retain'),
    'validate_domain': ('deprecated', 'public', 'stated', 'retain'),
    'volunteer_context': ('deprecated', 'public', 'stated', 'retain'),
}



# The C3 tensions: method → (what Part A carries, what the other doc implies).
# Pinned because deleting the tensions list renders "None." and, before this test,
# the suite stayed green — the section's whole content was unread.
C3_TENSIONS_LITERAL = {
    "get_owned_entities": ("get_entity", "explore_connections"),
    "get_provenance_chain": ("get_entity", "check_confidence"),
    "restore_point_at": ("get_historical_knowledge", "check_confidence"),
    "list_sources": ("list_knowledge", "graph_overview"),
    "test_guard": ("RELOCATED", "graph_overview"),
    "graph_set_recording": ("update_memory_graph", "kept, inside the control-plane block"),
    "get_source_reliability": ("list_knowledge", "manage_source_trust"),
    "annotate_operator": ("update_knowledge", "adjust_relationship"),
}


def test_part_c3_tensions_are_read() -> None:
    """C3 is a findings table; emptying it must red, and its cells must stay true."""
    doc = _doc()
    c3 = doc.split("### C3 — cross-doc tensions")[1].split("### C3b")[0]
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
        ("get_source_reliability", r"\| W8 \| `manage_source_trust`"),
        ("annotate_operator", r"\| W15 \| `adjust_relationship`"),
    ):
        row = next(
            (ln for ln in c3.splitlines() if ln.startswith(f"| `{name}` |")), None
        )
        assert row, f"C3 has no row for {name}"
        assert marker in row, (
            f"C3's evidence for {name} is no longer the {marker!r} grouping — "
            f"the tension is now backed by a different doc's table:\n  {row}"
        )


# C3b — the cross-artifact divergences with the sibling Phase 0.1 `bridge-table.md`.
# Pinned so the section cannot empty itself, and so a *silent* re-merge of the two
# artifacts (harmonising by picking one) reds. The 4th column is the sibling tool; the
# 5th is the determination, and every one must name the sibling as the dissenting
# source or the divergence is not being reported, only listed.
C3B_DIVERGENCES_LITERAL: dict[str, tuple[str, str, str]] = {
    "annotate_operator": ("update_knowledge", "adjust_relationship",
                          "tortoise_annotate_operator"),
    "checkpoint": ("UNLISTED", "REMOVED", "tortoise_checkpoint"),
    "compute_confidence": ("refresh_confidence", "check_confidence",
                           "tortoise_compute_confidence"),
    "diary_read": ("UNLISTED", "REMOVED", "tortoise_diary_read"),
    "diary_write": ("UNLISTED", "REMOVED", "tortoise_diary_write"),
    "invalidate_point": ("update_knowledge", "supersede_knowledge",
                          "tortoise_invalidate"),
    "list_graphs": ("graph_overview", "tenancy:list_memory_graphs",
                    "tortoise_list_graphs"),
    "list_namespaces": ("graph_overview", "list_knowledge", "tortoise_list_namespaces"),
    "list_sources": ("list_knowledge", "graph_overview", "tortoise_list_sources"),
    "list_topics": ("graph_overview", "list_knowledge", "tortoise_list_topics"),
    "org_create": ("UNBACKED", "tenancy:create_memory_graph", "tortoise_org_create"),
    "paginated_query": ("list_knowledge", "search_knowledge",
                        "tortoise_paginated_query"),
    "promote_point": ("update_knowledge", "refresh_confidence", "tortoise_promote_point"),
    "query": ("list_knowledge", "search_knowledge", "tortoise_query"),
    "query_points_by_tag": ("list_knowledge", "search_knowledge",
                            "tortoise_query_points_by_tag"),
    "set_point_baseline": ("update_knowledge", "refresh_confidence",
                           "tortoise_set_point_baseline"),
}


C3B_DETERMINATIONS_LITERAL: dict[str, str] = {
    'annotate_operator': "**Part A is right; the bridge is wrong.** beta row 25 states it twice — “Annotating a link is `update_knowledge` on it” and the W15 row's “`adjust_relationship` for strength, `update_knowledge` for annotation”. The bridge follows the canonical sketch's W15 grouping, which beta governs.",
    'checkpoint': "**Part A is right; the sibling carries the same defect.** beta files `checkpoint` under “Named but not solved”: live in the MCP server, filed post-beta, unlisted — not dead. The bridge's `REMOVED` (“retires with no destination”) reads it as discarded.",
    'compute_confidence': "**Part A is right.** The canonical W13 group is renamed by beta to `refresh_confidence` (“Recompute confidence after changes”); `check_confidence` is the READ (“Returns the confidence view only”). `compute_confidence` recomputes, so the bridge follows the canonical group rather than beta's rename.",
    'diary_read': "**Part A is right; the sibling carries the same defect.** beta files `diary_read` under “Named but not solved” — live and unlisted, not dead. The bridge's `REMOVED` reads it as discarded.",
    'diary_write': "**Part A is right; the sibling carries the same defect.** beta files `diary_write` under “Named but not solved” — live and unlisted, not dead. The bridge's `REMOVED` reads it as discarded.",
    'invalidate_point': "**Part A is right.** beta: “`retract_point`, `invalidate_point` \\| 2 \\| → fields on `update_knowledge`”, and its retraction rationale is “not a separate verb”. The bridge follows the canonical collapse table's `invalidate_point` → `supersede`.",
    'list_graphs': "**Part A is right; the bridge conflates two methods.** beta's narrow-aliases row names `list_graphs` among the aliases absorbed by `graph_overview` and deleted, while `graph_list` is the one sent to `list_memory_graphs`. The canonical doc's “does not merge” list keeps `list_graphs` ≠ `graph_list` (raw DB names vs control-plane rows).",
    'list_namespaces': "**Part A is right; the bridge is wrong.** beta's narrow-aliases row names `list_namespaces` among the aliases absorbed by `graph_overview`, and canonical R6 lists it there too.",
    'list_sources': "**Part A is right (beta governs).** beta's `list_sources` row is explicit that it is **not discarded** and folds into **row 4 `list_knowledge(kind='source')`**, and beta's `get_source_reliability` row routes its reads via `list_sources`. The bridge sends it to `graph_overview` — canonical R6's home for it — but beta governs the surface, so Part A carries beta's destination.",
    'list_topics': "**Part A is right; the bridge is wrong.** beta's narrow-aliases row names `list_topics` among the aliases absorbed by `graph_overview`, and canonical R6 lists it there too.",
    'org_create': "**Genuinely contested — no owner ruling.** No approved doc places organisation-account creation. `create_memory_graph` (beta row 29) provisions a memory GRAPH, not an account, and beta's tenancy block has no creation row. Part A's `UNBACKED` is the honest record; the bridge asserts a destination no doc states.",
    'promote_point': "**Part A is right; the bridge is wrong — and the map is owner-approved, so it is reported here, not edited.** The owner-approved MCP list absorbs `promote_point` into `revise_knowledge`, whose beta successor is `update_knowledge`; beta names it a FIELD on that update, and promote's incident-operator cascade and approval gate ride with it. The bridge's `refresh_confidence` recomputes confidence and does not cover the cascade.",
    'query': '**Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method.',
    'paginated_query': '**Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method.',
    'query_points_by_tag': '**Part A is right; the bridge is wrong.** beta: “`query`, `paginated_query`, `query_points_by_tag` \\| 3 \\| → `list_knowledge`”, and canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit that `list_knowledge` is the browse-and-filter method.',
    'set_point_baseline': "**Part A is right; the bridge is wrong — and the map is owner-approved, so it is reported here, not edited.** Same approved absorption as `promote_point`; beta names the starting belief a FIELD on `update_knowledge`. The bridge's `refresh_confidence` recomputes confidence, which is a different operation.",
}

C4_EVIDENCE_LITERAL: dict[str, str] = {
    'recall_legs': ('`retrieval_legs`', 'beta-sdk-surface.md', '\\| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, `calibrate_summary`, `calibration_passed` \\| ~6 \\| → `check_confidence` for the confidence view; **`recall_subgraph` is dropped, not folded** — `explore_connections` answers that question. The gaps question is flagged in "Named but not solved". \\|'),
    'stale': ('`stale_points`', 'beta-sdk-surface.md', '\\| narrow aliases absorbed by `graph_overview` — `taxonomy`, `list_pointkinds`, `list_tags`, `list_namespaces`, `list_graphs`, `status`, `stale`, `check_structure`, `list_topics` \\| **Deleted, not folded.** The approved list contains the container and not the aliases; shipping both is the merge failing at its own goal. \\|'),
    'count_memory_graphs': ('`graph_count`', 'beta-sdk-surface.md', '\\| `count_memory_graphs` \\| The plan is unlimited on builder plans, so its stated purpose — checking an allowance — does not exist. `list_memory_graphs` answers "how many" for any real N. \\|'),
    'set_memory_graph_name': ('`graph_set_name`', 'beta-sdk-surface.md', '\\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \\| 3 \\| See "Provisioning" above. \\|'),
    'set_memory_graph_backend': ('**none**', 'beta-sdk-surface.md', '\\| `set_memory_graph_name`, `set_memory_graph_backend`, `count_memory_graphs` \\| 3 \\| See "Provisioning" above. \\|'),
    'index_sources': ('`index_directory`', 'beta-sdk-surface.md', '\\| `index_sources` (bare) \\| 1 \\| Renamed → `index_sources_from_directory`, so the index/mine distinction is unmissable. \\|'),
    'withdraw_knowledge': ('**none**', 'beta-sdk-surface.md', '\\| `withdraw_knowledge` \\| 1 \\| **Never existed** — removed from the plan. Retraction is a field on `update_knowledge`. \\|'),
}


def test_part_c3b_bridge_divergences_are_read() -> None:
    """C3b is the cross-artifact reconciliation; emptying or re-merging it must red.

    The point of C3b is that the two artifacts may disagree, and the disagreement is
    RECORDED rather than resolved by picking one. A test that only asserted the section
    exists would let a later edit delete the rows and leave the two artifacts silently
    disagreeing again.
    """
    doc = _doc()
    c3b = doc.split("### C3b")[1].split("### C4")[0]
    rows = re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| `([^`]+)` \| `([^`]+)` \| `([a-z_]+)` \| "
        r"(.*) \|$",
        c3b, re.M,
    )
    parsed = {n: (a, b, tool) for n, a, b, tool, _det in rows}
    assert parsed == C3B_DIVERGENCES_LITERAL, (
        "C3b's cross-artifact divergences changed, or the section emptied itself.\n"
        f"  parsed: {parsed}\n  pinned: {C3B_DIVERGENCES_LITERAL}"
    )
    # P1-2: the DETERMINATION column was read only for the substring "bridge"/"sibling",
    # so all four "Genuinely contested — no owner ruling." rows could be rewritten to
    # "Part A is right; the bridge is wrong." — inverting the claim about which rows are
    # settled — with 43 tests green. The complete rendered determination is pinned per
    # row, exactly as Part A/B pin their authored cells.
    det_parsed = {n: det for n, _a, _b, _tool, det in rows}
    assert det_parsed == C3B_DETERMINATIONS_LITERAL, (
        "a C3b determination changed, or a row's determination was swapped.\n"
        f"  changed: {sorted(k for k in det_parsed if C3B_DETERMINATIONS_LITERAL.get(k) != det_parsed[k])}\n"
        f"  added:   {sorted(set(det_parsed) - set(C3B_DETERMINATIONS_LITERAL))}\n"
        f"  dropped: {sorted(set(C3B_DETERMINATIONS_LITERAL) - set(det_parsed))}"
    )
    for name, _a, _b, _tool, det in rows:
        assert "bridge" in det.lower() or "sibling" in det.lower(), (
            f"C3b's determination for {name} no longer names the sibling as the "
            f"dissenting source: {det!r}"
        )


def test_structural_counts_and_the_summary_sentence_are_read() -> None:
    """The summary sentence and the structural counts were rendered but never asserted.

    The summary sentence's numbers can be made self-contradictory ("1 are target
    methods with no def ... 34 are target methods that already exist") with the
    suite green, so each is pinned here.
    """
    doc = _doc()
    m = re.search(
        r"Distinct destinations: \*\*(\d+)\*\* — \*\*(\d+)\*\* are target "
        r"methods with no `def` today \(Part C1 lists all (\d+) "
        r"Phase-2 methods\), \*\*(\d+)\*\* are target methods that already exist, "
        r"and \*\*(\d+)\*\* are the no-destination dispositions",
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
    # Each part is pinned, not just the sum. `exists` is now 4, not 2, because an
    # UNCHANGED row's Target is the method's own name (`create_entity`, `get_entity`,
    # `approve_merge`, `close`) rather than the old `UNCHANGED` token.
    assert (distinct, no_def, exists, non_target) == (41, 33, 4, 4), (
        "the destination summary moved: "
        f"{distinct} destinations = {no_def} no-def + {exists} existing + {non_target} other"
    )
    assert "**32 groups** over **149** named members" in doc, (
        "the structural note's canonical-group counts are gone"
    )
    assert "150 = the 150-method surface" in doc, (
        "the structural note no longer reconciles its members to the 150-method surface"
    )
    # C6 sits between C5 and the structural notes; terminate the C5 slice at C6 so a
    # `W11` mention in the contested section cannot leak into the wildcard set.
    c5 = doc.split("### C5")[1].split("### C6")[0]
    assert set(re.findall(r"`([WN]\d+)`", c5)) == {"W16"}, (
        "C5 no longer names exactly the wildcard families' resolved group (W16)"
    )


def test_every_citation_anchor_and_region_is_pinned() -> None:
    """The LOCATOR and the DERIVED REGION are pinned, not merely the citation key.

    A citation is a locator plus an extracted region, so both halves are pinned here.
    Pinning only the anchor would let the extraction rule move under it (a region could
    shrink to a prefix); pinning only the region would let two locators swap. Without
    either, a citation could be re-keyed to a different real row of the same document
    and the row would keep its Target and its `stated` basis while its evidence changed.
    """
    import tools.sdk_rename_table as gen
    from tools.sdk_rename_table import Region, _derive_region, _doc_text

    actual_anchors: dict[str, tuple[str, str]] = {}
    for src in (gen.CITES, gen.PHANTOM_CITES or {}, gen.TENSION_CITES or {}):
        actual_anchors.update({k: (v[0], v[1]) for k, v in src.items()})
    assert actual_anchors == CITES_ANCHOR_LITERAL, (
        "a citation anchor changed, or cites were re-keyed.\n"
        f"  changed: {sorted(k for k in actual_anchors if CITES_ANCHOR_LITERAL.get(k) != actual_anchors[k])}\n"
        f"  added:   {sorted(set(actual_anchors) - set(CITES_ANCHOR_LITERAL))}\n"
        f"  dropped: {sorted(set(CITES_ANCHOR_LITERAL) - set(actual_anchors))}"
    )
    actual_regions = {
        k: _derive_region(_doc_text(doc), Region(anchor))
        for k, (doc, anchor) in actual_anchors.items()
    }
    assert actual_regions == CITATION_REGION_LITERAL, (
        "the extracted region changed, or a region no longer renders whole.\n"
        f"  changed: {sorted(k for k in actual_regions if CITATION_REGION_LITERAL.get(k) != actual_regions[k])}\n"
        f"  added:   {sorted(set(actual_regions) - set(CITATION_REGION_LITERAL))}\n"
        f"  dropped: {sorted(set(CITATION_REGION_LITERAL) - set(actual_regions))}"
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
    """The Target column names a target method, or is empty (no destination)."""
    approved = set(targets_independently())
    assert len(approved) == 40, (
        f"parsed {len(approved)} target names from beta-sdk-surface.md, expected 40"
    )
    bad = sorted({r["target"] for r in part_a_rows()} - (approved | {""}))
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
            assert r["cite"] == "—", (
                f"{r['name']}: an unbacked row must not carry a citation"
            )
            continue
        m = CITE_RE.match(r["cite"])
        assert m, f"{r['name']}: unparseable citation cell: {r['cite']!r}"
        doc, quote = m.group(1), m.group(2).replace("\\|", "|")
        assert doc in docs, f"{r['name']}: cites an unknown doc {doc!r}"
        if not _find_quote(docs[doc], quote):
            bad.append(f"{r['name']} → {doc}: {quote[:60]!r}")
    assert not bad, "Part A cites text that is no longer in the cited doc:\n  " + \
                    "\n  ".join(bad)


def test_every_rendered_citation_quote_is_maximal() -> None:
    """Every quote in the RENDERED doc must reach a REGION boundary.

    Re-derived here from the rendered document plus the source docs — the generator's
    own `_maximal` is not imported — so dropping the guard from `_validate` still reds
    here. A quote cut mid-clause can hide the clause that contradicts its row, and a
    substring test cannot see it because a truncated prefix of a real sentence is still
    a real substring. Boundaries: a cell/row `|`, a line end, or the document end. A
    SENTENCE end does NOT qualify: the next sentence is exactly where the contradicting
    clause sits (the journal bullet's `… `diary_read`.` was accepted while the next
    sentence said `live in the MCP server … unlisted until then.`).
    """
    docs = {
        "beta-sdk-surface.md": BETA.read_text(encoding="utf-8"),
        "canonical-sdk-methods.md": CANON.read_text(encoding="utf-8"),
    }
    bad = []
    for r in part_a_rows():
        if r["basis"] == "unbacked":
            continue
        m = CITE_RE.match(r["cite"])
        assert m, f"{r['name']}: unparseable citation cell: {r['cite']!r}"
        doc, quote = m.group(1), m.group(2).replace("\\|", "|")
        text = docs[doc]
        found = _find_quote(text, quote)
        assert found, f"{r['name']}: quote is not in {doc}"
        # BOTH edges are checked. The generator's `_maximal` used to check only the
        # right, so a quote could begin mid-cell; and the left edge is exactly where a
        # region that was cut at its start would show. A region must begin at a line
        # start and end at a `|`, a line end, or the document end.
        before_ok = found.start() == 0 or text[found.start() - 1] == "\n"
        after = text[found.end():]
        after_ok = after == "" or after.startswith("\n") or quote.endswith("|")
        if before_ok and after_ok:
            continue
        bad.append(
            f"{r['name']} → begins {text[max(0, found.start()-30):found.start()][-30:]!r}, "
            f"ends …{quote[-50:]!r} continues {after[:50]!r}"
        )
    assert not bad, (
        "citation regions are cut at an edge — the part that was cut is exactly where a "
        "contradiction hides:\n  " + "\n  ".join(bad)
    )


def test_maximal_rejects_a_sentence_boundary_truncation() -> None:
    r"""A quote that stops at a full stop is NOT maximal — the next sentence can bite.

    This is the #4282 w5 defect, replayed on the *rule itself*. `_maximal` used to
    accept a sentence boundary, so `**The journal capability** — `checkpoint`,
    `diary_write`, `diary_read`.` was a legal quote for a `DISCARDED` row while the
    very next sentence — `They are **live in the MCP server** today … **Filed
    post-beta** … and **unlisted** until then.` — said the opposite. The generator's
    `_maximal` IS imported here on purpose: this is a unit test of the rule (the test
    above re-derives the same rule independently, from the rendered doc).
    """
    sys.path.insert(0, str(ROOT))
    from tools import sdk_rename_table as gen

    beta = BETA.read_text(encoding="utf-8")
    first_sentence = ("**The journal capability** — `checkpoint`, `diary_write`, "
                      "`diary_read`.")
    assert first_sentence in beta, "the fixture is stale — the journal bullet changed"
    assert not gen._maximal(first_sentence, beta), (
        "a quote ending at sentence boundary was accepted — the next sentence is "
        "exactly where the contradiction hid"
    )
    full_bullet = beta[beta.index("- **The journal capability**"):].split("\n\n", 1)[0]
    assert gen._maximal(full_bullet, beta), (
        "the widened full bullet (ending at a line end) must be maximal"
    )


def test_the_w8_read_clause_is_present_in_the_evidence() -> None:
    """The w8 row must carry the read/write split the old truncation cut off.

    `get_source_reliability` is the read and the beta row routes reads via
    `list_sources` (which folds into `list_knowledge(kind='source')`); `assess_source`
    and `set_source_tier` are the writes. The defect was keeping the target and dropping
    the clause, so this pins that the clause is now IN the quote, not merely that the
    target moved.
    """
    rows = {r["name"]: r for r in part_a_rows()}
    assert rows["get_source_reliability"]["target"] == "list_knowledge"
    assert rows["assess_source"]["target"] == "manage_source_trust"
    assert rows["set_source_tier"]["target"] == "manage_source_trust"
    for name in ("assess_source", "set_source_tier", "get_source_reliability"):
        assert "reads via `list_sources`" in rows[name]["cite"], (
            f"{name}'s w8 quote no longer shows the read clause — the truncation is back"
        )


def test_the_axes_are_consistent_with_the_declared_vocabulary() -> None:
    """The three axes + delete instruction are a DECLARED combination, never ad hoc.

    `config/disposition-vocabulary.yml` is the oracle here, not the generator: the
    rendered cells must match one of its `dispositions` combinations. This is what stops
    the flat enum from creeping back — a mutation that gave an `unbacked` row a
    visibility, or an `unlisted` row a `delete`, would no longer be a declared combo.
    """
    import yaml

    vocab = yaml.safe_load(
        (ROOT / "config" / "disposition-vocabulary.yml").read_text(encoding="utf-8")
    )
    combos = {
        (spec.get("lifecycle") or "", spec.get("visibility") or "",
         spec.get("delete") or "")
        for spec in vocab["dispositions"].values()
    }

    def norm(cell: str) -> str:
        return "" if cell == "—" else cell

    for r in part_a_rows():
        axes = (norm(r["lifecycle"]), norm(r["visibility"]), norm(r["delete"]))
        assert axes in combos, (
            f"{r['name']}: axes {axes} are not a declared disposition combination"
        )
        if r["basis"] == "unbacked":
            assert r["target"] == "" and axes == ("", "", ""), (
                f"{r['name']}: an unbacked row must state no destination and no axis"
            )
        if r["basis"] == "contested":
            assert r["target"] == "", f"{r['name']}: a contested row states no destination"


def test_the_axes_are_pinned() -> None:
    """Every row's lifecycle/visibility/basis/delete cells equal the pinned literal.

    The Target pin guards the destination; this guards the three axes and the delete
    instruction, which are what replaced the old flat enum. Without it a row could keep
    its target and silently flip `retain` to `delete`.
    """
    actual = {
        r["name"]: (r["lifecycle"], r["visibility"], r["basis"], r["delete"])
        for r in part_a_rows()
    }
    assert actual == AXES_LITERAL, (
        "a row's axes moved.\n"
        f"  changed: {sorted(k for k in actual if AXES_LITERAL.get(k) != actual[k])[:10]}\n"
        f"  added:   {sorted(set(actual) - set(AXES_LITERAL))}\n"
        f"  dropped: {sorted(set(AXES_LITERAL) - set(actual))}"
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
        r"(?<![\d])114 of the 150 are renames",
        r"\*\*4\*\* are already targets \(unchanged\)",
        r"\*\*25\*\* are discarded with a rationale \(lifecycle `removed` · "
        r"visibility `internal` · delete `delete`\)",
        r"\*\*3\*\* are live but \*\*unlisted\*\* \(visibility `unlisted` — NOT deleted\)",
        r"\*\*1\*\* is \*\*relocated\*\* out of the product SDK \(visibility `internal`\)",
        r"\*\*0\*\* are \*\*contested\*\* \(the docs name conflicting destinations\)",
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
    # The old flat enum is gone: a row is now classified by its AXES, so these counts are
    # read off lifecycle/visibility/delete rather than a single token. The four UNCHANGED
    # rows carry their own name as the target (they are already on the target surface).
    assert sum(1 for r in rows if r["target"] == r["name"]) == 4
    assert sum(1 for r in rows if r["lifecycle"] == "removed"
               and r["delete"] == "delete") == 25
    assert sum(1 for r in rows if r["visibility"] == "unlisted") == 3
    assert sum(1 for r in rows if r["visibility"] == "internal"
               and r["delete"] == "retain") == 1
    assert sum(1 for r in rows if r["basis"] == "unbacked") == 3
    assert sum(1 for r in rows if r["basis"] == "contested") == 0
    # The four contested rows of the old enum are now the C6 FOLD record: each names a
    # destination and the authority for it, so a re-classification cannot silently
    # revert to "no destination". The names are pinned here.
    c6 = _section("C6 — the lifecycle/confidence fold (the former `CONTESTED` rows)")
    fold = dict(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| `([a-z_][a-z0-9_]*)` \| ", c6, re.M))
    assert fold == {
        "promote_point": "update_knowledge",
        "set_point_baseline": "update_knowledge",
        "list_drafts": "list_knowledge",
        "quarantine_batch": "update_knowledge",
    }, f"C6's fold record changed: {fold}"


def test_part_a_rows_are_well_formed_markdown() -> None:
    r"""Every Part A row must carry exactly 10 cells when split on UNESCAPED pipes.

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
        if len(cells) != 10:
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
        by_target.setdefault(row_destination(r), set()).add(r["name"])
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
    unbacked_in_table = {r["name"] for r in part_a_rows() if r["basis"] == "unbacked"}
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
    # Only the REASONS are pinned as literals. The line numbers were previously
    # duplicated here as literals too, which asserted a value the fresh-AST-walk check
    # below already derives — so they added no coverage, and their only possible effect
    # was to fail as a stale-value alarm once the doc had been regenerated without them
    # being updated. The docstring's contract (lines from the AST, reasons literal) is
    # what the code now does.
    assert {n: reason for n, (_ln, reason) in parsed.items()} == {
        "org_create": (
            "No target method creates an organisation account. The tenancy block reads "
            "one (`get_organisation_account`) and files account *closure* as a console "
            "operation, but no row covers creation."
        ),
        "compute_reputation": (
            "The canonical `stabilize_beliefs` group lists it, but that group's beta "
            "target is `refresh_confidence` — “Recompute confidence after changes”. "
            "Reputation scoring is not confidence recomputation, and no other target "
            "absorbs it."
        ),
        "record_calibration": (
            "Same group, same mismatch: `refresh_confidence` recomputes confidence; "
            "recording a calibration milestone is a different operation and has no target."
        ),
    }, f"C2's reasons changed:\n  {parsed}"
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
        "promote_point": "update_knowledge",
        "mitigate_operator": "adjust_relationship",
        "compute_confidence": "refresh_confidence",
        "retract_point": "update_knowledge",
        "dream": "refresh_confidence",
        "tortoise_fts_query": "search_knowledge",
        "close": "close",
        # Rows where the two docs disagree, or a capricious rewrite is easy.
        "restore_point_at": "get_historical_knowledge",
        "list_sources": "list_knowledge",
        "test_guard": "",
        "recall_subgraph": "",
        "file_decision": "write_question",
        "file_human_approval": "record_decision",
        "graph_set_recording": "update_memory_graph",
        "graph_count": "list_memory_graphs",
        "backfill_v25": "",
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
        "W5": ("checkpoint", ""),
        "W6": ("capture_session", "mine_knowledge_from_session"),
        "W7": ("commit_session", "mine_knowledge_from_session"),
        "W8": ("assess_source", "manage_source_trust"),
        "W9": ("create_operator", "link_entities"),
        "W10": ("file_human_approval", "record_decision"),
        "W11": ("update_point", "update_knowledge"),
        "W12": ("delete_point", "delete_knowledge"),
        "W13": ("compute_confidence", "refresh_confidence"),
        "W14": ("approve_merge", "approve_merge"),
        "W15": ("mitigate_operator", "adjust_relationship"),
        "W17": ("ulid", ""),
        "N1": ("org_create", ""),
        "N2": ("graph_count", "list_memory_graphs"),
        "N3": ("membership_create", "add_member"),
        "N4": ("apikey_create", "create_key"),
        "N5": ("invitation_create", ""),
        "N6": ("signup_token_lookup", ""),
        "ARCHIVE": ("backfill_v25", ""),
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


def test_a_truncated_anchor_cannot_shorten_the_region() -> None:
    r"""P1-1: the anchor only LOCATES; the region is EXTRACTED whole.

    The w8 defect was a quote cut at an inner cell boundary (`| 3 |`), dropping
    "; reads via `list_sources`"; the journal defect was a quote cut at a source LINE
    WRAP, dropping "Filed post-beta … unlisted until then." Under the region design
    those same truncated anchors derive the WHOLE row / WHOLE bullet, so the dropped
    clause is restored by construction. There is no authored extent left to cut — which
    is why this is a positive guarantee rather than a check that can have a hole.
    """
    sys.path.insert(0, str(ROOT))
    from tools.sdk_rename_table import Region, _derive_region

    beta = BETA.read_text(encoding="utf-8")

    # P1-1b: a row anchor truncated at an inner cell boundary.
    cell_truncated = ("| `assess_source`, `set_source_tier`, `get_source_reliability` "
                      "| 3 |")
    assert cell_truncated in beta, "the fixture is stale — the w8 row changed"
    assert cell_truncated.endswith("|")
    region = _derive_region(beta, Region(cell_truncated))
    assert region.endswith("|") and "reads via `list_sources`" in region, (
        "a cell-truncated row anchor did not derive the whole row — the read clause "
        "is gone, which is exactly the w8 truncation"
    )

    # P1-1a: a bullet anchor truncated at a source line wrap.
    bullet_anchor = "- **The journal capability**"
    full_bullet = _derive_region(beta, Region(bullet_anchor))
    assert "Filed post-beta" in full_bullet and "unlisted" in full_bullet
    line_wrapped = (
        "- **The journal capability** — `checkpoint`, `diary_write`, `diary_read`. "
        "They arrived in the\n  **initial codebase commit** (`a02ab48c7`) with no "
        "design record, and their `wing` / `room`\n  parameters appear **nowhere in "
        "`docs/ONTOLOGY.md`**. They are **live in the MCP server**"
    )
    assert line_wrapped in beta, "the fixture is stale — the journal bullet changed"
    assert line_wrapped.endswith("server**")
    assert _derive_region(beta, Region(line_wrapped)) == full_bullet, (
        "a line-wrap-truncated bullet anchor derived something shorter than the whole "
        "bullet — the truncation survived"
    )


def test_a_region_that_cannot_be_rendered_whole_is_refused(generator_module) -> None:
    r"""A row anchor that does not resolve to a table row must be REFUSED, not cut.

    The derivation returns the whole region or raises — never a prefix and never a
    silently-shortened region. `_maximal` is the secondary tripwire for the derived
    text; both edges are asserted here on the rule itself.
    """
    from tools.sdk_rename_table import Region, _derive_region

    beta = BETA.read_text(encoding="utf-8")
    # A `|`-terminated anchor that resolves into a non-table line cannot be a row.
    with pytest.raises(ValueError):
        _derive_region("this line is prose, not a table |\n", Region("prose, not a table |"))
    # An absent anchor raises rather than returning the anchor text.
    with pytest.raises(KeyError):
        _derive_region(beta, Region("an anchor that is in no doc at all |"))
    # `_maximal` rejects EITHER edge being cut.
    full_row = "| `ulid` | 1 | A ULID generator. Not a memory operation. |"
    assert generator_module._maximal(full_row, beta)
    assert not generator_module._maximal(full_row[:-1], beta), (
        "a right-truncated row was accepted — the right edge must be a boundary"
    )
    assert not generator_module._maximal("ULID generator. Not a memory operation. |",
                                         beta), (
        "a left-truncated row was accepted — the left edge must begin at a line start"
    )


def test_validate_rejects_an_ambiguous_anchor(generator_module) -> None:
    """An anchor that locates more than one region locates NOTHING — refuse it.

    `quote in text` could never see this: a common token was fine as long as it was a
    substring. A region locator must resolve to exactly one region.
    """
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    assert generator_module._validate(methods, groups, targets, cites) == []

    cites["ambiguous"] = ("beta-sdk-surface.md", "create_entity")
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("AMBIGUOUS CITATION ANCHOR" in e and "ambiguous" in e for e in errs), (
        f"an anchor locating several regions did not fail the build: {errs}"
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


def _rows_for_validation(generator_module) -> tuple[list[dict], list[str]]:
    """Fresh rows + targets for the row-level validator tests (no shared mutation)."""
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    return generator_module._rows(methods, groups), targets


def test_validate_rows_rejects_a_target_the_quote_never_names(generator_module) -> None:
    """A `stated` row whose quote never names its Target must fail the build.

    Basis computes `stated` from whether the quote names the METHOD; nothing checked
    that the same quote named the DESTINATION, so a row could point at a target its
    evidence never mentions. `annotate_ask_hits`'s quote is the `search_sessions … →
    `search_knowledge`` cell; retargeting the row to `list_knowledge` leaves the
    citation untouched and used to stay green.
    """
    rows, targets = _rows_for_validation(generator_module)
    assert generator_module._validate_rows(rows, targets) == []
    victim = next(r for r in rows if r["name"] == "annotate_ask_hits")
    assert victim["target"] == "search_knowledge"
    victim["target"] = "list_knowledge"
    errs = generator_module._validate_rows(rows, targets)
    assert any("TARGET NOT IN EVIDENCE" in e and "annotate_ask_hits" in e for e in errs), (
        f"a stated row pointing at a target its quote never names did not fail: {errs}"
    )


def test_validate_rows_rejects_a_disposition_cited_from_the_wrong_section(
        generator_module) -> None:
    """A DISCARDED row must cite beta's "### Removed" — nothing else.

    Two real defects in one rule. The journal's bullet is filed under "Named but not
    solved" ("live in the MCP server … filed post-beta … unlisted"), and `test_guard`'s
    is filed under the sibling "### Renamed, relocated" table ("**Kept and
    relocated**") — DISCARDED is Phase 2's signal to DELETE, and both were rendered
    with it. Narrowing DISCARDED to `### Removed` (not the whole "Discarded" section)
    is what makes "kept" mechanically distinguishable from "deleted".
    """
    rows, targets = _rows_for_validation(generator_module)
    assert generator_module._validate_rows(rows, targets) == []

    journal = next(r for r in rows if r["name"] == "checkpoint")
    assert journal["disposition"] == generator_module.UNLISTED
    journal["disposition"] = generator_module.DISCARDED  # the old, wrong disposition
    errs = generator_module._validate_rows(rows, targets)
    assert any("DISPOSITION MISFILED" in e and "checkpoint" in e for e in errs), (
        f"a DISCARDED row cited from 'Named but not solved' did not fail: {errs}"
    )

    rows2, targets2 = _rows_for_validation(generator_module)
    guard = next(r for r in rows2 if r["name"] == "test_guard")
    assert guard["disposition"] == generator_module.RELOCATED
    guard["disposition"] = generator_module.DISCARDED  # "Kept and relocated" is not "Removed"
    errs = generator_module._validate_rows(rows2, targets2)
    assert any("DISPOSITION MISFILED" in e and "test_guard" in e for e in errs), (
        f"a 'Kept and relocated' row rendered DISCARDED did not fail: {errs}"
    )


def test_validate_rows_rejects_a_discarded_row_whose_clause_says_reachable(
        generator_module) -> None:
    r"""P1-4: a DISCARDED region must SAY removal — sitting under "### Removed" is not enough.

    beta's w11 row sits under "### Removed" but its rationale names the capability as
    *state* folded onto `update_knowledge` / `list_knowledge` with "No separate verb" —
    a FOLD, not a delete. `list_drafts` and `quarantine_batch` were rendered DISCARDED
    (Phase 2's DELETE signal) with no contested flag at all, while
    `promote_point`/`set_point_baseline` were surfaced only because the BRIDGE happens
    to bind them. The guard must reject rendering that row DISCARDED, and the four rows
    are the ones the C6 fold record now names.
    """
    rows, targets = _rows_for_validation(generator_module)
    assert generator_module._validate_rows(rows, targets) == []
    folds = {
        r["name"]: (r["disposition"], r["target"]) for r in rows
        if r["name"] in {"promote_point", "set_point_baseline", "list_drafts",
                         "quarantine_batch"}
    }
    assert folds == {
        "promote_point": (generator_module.RENAMED, "update_knowledge"),
        "set_point_baseline": (generator_module.RENAMED, "update_knowledge"),
        "list_drafts": (generator_module.RENAMED, "list_knowledge"),
        "quarantine_batch": (generator_module.RENAMED, "update_knowledge"),
    }, f"the four fold rows are no longer folds to a target: {folds}"
    for name in ("list_drafts", "quarantine_batch", "promote_point",
                 "set_point_baseline"):
        victim = next(r for r in rows if r["name"] == name)
        victim["disposition"] = generator_module.DISCARDED  # the old, wrong disposition
        errs = generator_module._validate_rows(rows, targets)
        assert any("DISCARDED BUT RETAINED" in e and name in e for e in errs), (
            f"{name} rendered DISCARDED from a clause that says it is reachable did "
            f"not fail: {errs}"
        )
        victim["disposition"] = generator_module.RENAMED  # restore for the next name
    # The inverse: CONTESTED on a region with no retention clause is refused too, so the
    # classification is mechanically derivable from the region, not a free choice.
    rows2, targets2 = _rows_for_validation(generator_module)
    plain = next(r for r in rows2 if r["name"] == "ulid")
    assert plain["disposition"] == generator_module.DISCARDED
    plain["disposition"] = generator_module.CONTESTED
    errs = generator_module._validate_rows(rows2, targets2)
    assert any("CONTESTED WITHOUT A RETENTION CLAUSE" in e and "ulid" in e for e in errs), (
        f"a CONTESTED row whose clause states no retention did not fail: {errs}"
    )


def test_validate_rejects_phantom_evidence_that_does_not_name_the_phantom(
        generator_module) -> None:
    r"""P1-3: a C4 finding IS "the doc uses this name" — its evidence must contain it.

    `test_part_c4_lists_the_doc_code_name_mismatches` pinned the name set and the
    referents, but nothing read the evidence column, so the `recall_legs` phantom could
    cite a canonical row that never contains `recall_legs` and the row would render
    evidence that does not support it. The build must refuse that.
    """
    methods = dict(public_methods_independently())
    groups = generator_module._groups(CANON.read_text(encoding="utf-8"))
    targets = targets_independently()
    cites = dict(generator_module.CITES)
    cites.update(generator_module.TENSION_CITES)
    cites.update(generator_module.PHANTOM_CITES)
    assert generator_module._validate(methods, groups, targets, cites) == []

    # r1's region names `search_sessions …` and never `recall_legs`.
    cites["r3"] = cites["r1"]
    errs = generator_module._validate(methods, groups, targets, cites)
    assert any("PHANTOM EVIDENCE DOES NOT NAME IT" in e and "recall_legs" in e
               for e in errs), (
        f"phantom evidence that does not contain the phantom name did not fail: {errs}"
    )


def test_part_c4_evidence_names_every_phantom() -> None:
    """The C4 evidence column is pinned, and must contain the name it is evidence for."""
    section = _section("C4 — names the disposition docs use that are NOT SDK methods")
    rows = re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| ([^|]*) \| `([a-z-]+\.md)` — “(.*)” \|$",
                      section, re.M)
    parsed = {n: (ref.strip(), doc, ev) for n, ref, doc, ev in rows}
    assert parsed == C4_EVIDENCE_LITERAL, (
        "C4's evidence column changed.\n"
        f"  changed: {sorted(k for k in parsed if C4_EVIDENCE_LITERAL.get(k) != parsed[k])}\n"
        f"  added:   {sorted(set(parsed) - set(C4_EVIDENCE_LITERAL))}\n"
        f"  dropped: {sorted(set(C4_EVIDENCE_LITERAL) - set(parsed))}"
    )
    for name, (_ref, _doc, evidence) in parsed.items():
        assert re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", evidence), (
            f"C4's evidence for {name} does not contain the name it is evidence for: "
            f"{evidence[:90]!r}"
        )


def test_part_c6_records_the_fold_for_exactly_the_four_rows() -> None:
    """P1-4: the fold-not-delete rows carry a destination and an AUTHORITY.

    The four w11 members are the only rows whose "Removed" clause says the capability is
    reachable. C6 must name each one's destination AND the authority for it, and the
    destinations must agree with Part A (the generator's check 10 enforces the same).
    Nothing here is silently fixed: the AUTHORITY column is what says how much the fold
    rests on — the owner-approved MCP list, or only beta's row clause.
    """
    section = _section("C6 — the lifecycle/confidence fold (the former `CONTESTED` rows)")
    rows = re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| `([a-z_][a-z0-9_]*)` \| (.*) \|$",
                      section, re.M)
    parsed = {n: (dest, why) for n, dest, why in rows}
    assert set(parsed) == {"promote_point", "set_point_baseline", "list_drafts",
                           "quarantine_batch"}
    assert parsed["list_drafts"][0] == "list_knowledge"
    for n in ("promote_point", "set_point_baseline", "quarantine_batch"):
        assert parsed[n][0] == "update_knowledge", f"C6's destination for {n} changed"
    for name, (_dest, authority) in parsed.items():
        assert authority.strip(), f"C6's authority for {name} is empty"
    # The AUTHORITY column must say which rows rest on the owner-approved MCP list and
    # which only on beta's row clause — that distinction is the whole finding.
    assert "owner-approved MCP list" in parsed["promote_point"][1]
    assert "beta's own row" in parsed["quarantine_batch"][1]
    part_a = {r["name"]: r["target"] for r in part_a_rows()}
    for name, (dest, _why) in parsed.items():
        assert part_a[name] == dest, (
            f"C6 says {name} → {dest} but Part A carries {part_a[name]!r}"
        )


def test_validate_rows_requires_a_determination_for_every_bridge_divergence(
        generator_module, monkeypatch) -> None:
    """Every cross-artifact divergence carries a determination, and none is stale.

    Without this, `BRIDGE_DETERMINATION` could be emptied and the C3b rows would
    render with a `KeyError` at build time — or, worse, the divergence would be
    silently dropped from the doc while the two artifacts still disagreed.
    """
    rows, targets = _rows_for_validation(generator_module)
    assert generator_module._validate_rows(rows, targets) == []

    trimmed = {k: v for k, v in generator_module.BRIDGE_DETERMINATION.items()
               if k != "query"}
    monkeypatch.setattr(generator_module, "BRIDGE_DETERMINATION", trimmed)
    errs = generator_module._validate_rows(rows, targets)
    assert any("BRIDGE DIVERGENCE UNDETERMINED" in e and "query" in e for e in errs), (
        f"an undetermined bridge divergence did not fail the build: {errs}"
    )

    stale = dict(trimmed)
    stale["approve_merge"] = "this method no longer diverges"
    monkeypatch.setattr(generator_module, "BRIDGE_DETERMINATION", stale)
    errs = generator_module._validate_rows(rows, targets)
    assert any("STALE BRIDGE DETERMINATION" in e and "approve_merge" in e for e in errs), (
        f"a determination with no divergence did not fail the build: {errs}"
    )


def test_the_bridge_divergences_are_computed_from_the_sibling_artifact() -> None:
    """C3b's rows equal the divergences recomputed here from `bridge-table.md`.

    Part A's `method → target` is re-derived from the rendered doc (an independent
    oracle), and the bridge's `sdk_method → destination` straight from the sibling
    artifact — so a hand-typed C3b row cannot drift from what the two documents
    actually say.
    """
    part_a = {r["name"]: row_destination(r) for r in part_a_rows()}
    bridge = ROOT / "docs" / "product" / "bridge-table.md"
    row_re = re.compile(
        r"^\| \d+ \| `tortoise_[a-z0-9_]+` \| `tool_registry\.py:\d+` \| (.*?) \| "
        r"(?:yes|no) \| (.*?) \|$"
    )
    diverged: dict[str, str] = {}
    for line in bridge.read_text(encoding="utf-8").splitlines():
        m = row_re.match(line)
        if not m:
            continue
        cell = m.group(1).strip()
        if not (cell.startswith("`") and cell.endswith("`")):
            continue  # `**none declared**` / a binding that does not resolve
        method = cell.strip("`")
        if method not in part_a:
            continue
        raw = m.group(2).strip()
        # `bridge-table.md` appends ` ⚠️` to a destination the sibling records as WRONG;
        # the marker is rendering, not part of the destination the row carries — and it
        # must come off BEFORE the backtick strip, or the closing backtick survives.
        raw = re.sub(r"\s*⚠️\s*$", "", raw).strip().strip("`")
        dest = raw
        if dest == "REMOVED":
            dest = "DISCARDED"
        for ns in ("sdk:", "tenancy:"):
            if dest.startswith(ns):
                dest = dest[len(ns):]
        if dest != part_a[method]:
            diverged[method] = raw  # the doc lists the sibling's destination VERBATIM

    doc = _doc()
    c3b = doc.split("### C3b")[1].split("### C4")[0]
    listed = {}
    for line in c3b.splitlines():
        m = re.match(r"^\| `([a-z_][a-z0-9_]*)` \| `([^`]+)` \| `([^`]+)` \|", line)
        if m:
            listed[m.group(1)] = m.group(3)
    assert listed, "C3b rendered no rows"
    assert listed == diverged, (
        "C3b does not equal the divergences recomputed from `bridge-table.md`.\n"
        f"  in doc only: {sorted(set(listed) - set(diverged))}\n"
        f"  in bridge only: {sorted(set(diverged) - set(listed))}\n"
        f"  different destination: "
        f"{sorted(k for k in set(listed) & set(diverged) if listed[k] != diverged[k])}"
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
        "count below is arithmetic over those, never a typed number. Each row's citation "
        "**names a REGION of the doc — a table row, a bullet, or a paragraph — and the "
        "generator EXTRACTS that whole region verbatim**; the anchor only locates, so an "
        "anchor truncated at a line wrap or a mid-row cell still renders the region "
        "whole and cannot drop the clause that contradicts the row. A citation whose "
        "region cannot be resolved, or cannot be rendered whole, fails the build.",
        "The canonical inventory's group names are an **earlier sketch** "
        "(`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). "
        "The `Target` column always carries the **beta** target name "
        "(`update_knowledge`, `refresh_confidence`, `write_knowledge_batch`, "
        "`index_sources_from_directory`) — the canonical doc itself says beta governs "
        "where the two disagree, and records the renames.",
        "beta's `### Removed` row filed these as *reachable* without NAMING a destination,\n"
        "so they were an open finding — and the project's\n"
        "own docs disagreed about the fold (the approved MCP list absorbed two into\n"
        "`revise_knowledge`; beta then SPLIT `revise_knowledge` into `update_knowledge` +\n"
        "`supersede_knowledge`, so the approved absorber's name no longer exists; the\n"
        "bridge asserted `refresh_confidence`, which §C3b records as wrong). The fold is\n"
        "the convergent shape — lifecycle/confidence **state as a field** on the general\n"
        "update, selected by a **filter** on the general list — and it agrees with the\n"
        "owner's recorded ruling that retraction “is a temporal FIELD on update, not a\n"
        "separate verb”. Each row names its AUTHORITY: two rest on the owner-approved MCP\n"
        "list's own absorption, two only on beta's row clause — which is what decides\n"
        "whether the item is still genuinely owner-open.",
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
        "This table and `docs/product/bridge-table.md` answer **different questions** — "
        "here, current SDK *method* → target *method*; there, current MCP *tool* → target "
        "*tool* — so a tool and the method it binds to can legitimately reach different "
        "targets, and where they do the divergence is recorded rather than harmonised by "
        "picking one. The sibling is named as the **dissenting source** on every row. The "
        "list is **computed from `bridge-table.md` at build time**; each row's "
        "determination is authored, because the reason IS the finding. The build fails if "
        "a divergence carries no determination, or a determination carries no divergence.",
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
        "A disposition is **three orthogonal axes plus a delete instruction** — never "
        "one flat value. The vocabulary is **declared in "
        "`config/disposition-vocabulary.yml`**; this legend names it and does not "
        "restate it:"
    ) in doc, "the axes legend intro changed"
    for row in (
        "| `Lifecycle` | What happens to the method? | `stable`, `experimental`, "
        "`deprecated`, `removed` |",
        "| `Visibility` | On which surface is the capability reachable? | `public`, "
        "`unlisted`, `internal` |",
        "| `Basis` | How strong is the evidence for this row? | `stated`, `derived`, "
        "`unbacked`, `contested` |",
        "| `Delete` | Must Phase 2 delete the implementation? | `delete`, `retain` |",
    ):
        assert row in doc, f"an axis legend row changed or was dropped: {row}"
    assert (
        "The axes are independent, and that is the point: `DISCARDED` used to mean both "
        "“not on the public surface” and “DELETE this” at once, so one word carried a "
        "visibility fact and a delete COMMAND. Now the visibility is `internal` **and** "
        "the delete instruction is `delete`, each in its own cell. A `—` in an axis cell "
        "means the doc states nothing for that axis (an `unbacked` or `contested` row)."
    ) in doc, "the axes-independence rationale changed or was dropped"
    assert (
        "`Target` names the destination method; `—` means no destination is stated."
    ) in doc, "the Target-cell legend line changed"
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
        "| # | Method | Source | Group | Target | Lifecycle | Visibility | Basis | "
        "Delete | Citation |",
        "| Destination | Current methods | Count |",
        "| Target method | Status |",
        "| Method | Source | Why it has no destination |",
        "| Method | Part A carries | The other doc implies | Other doc's grouping |",
        "| Doc's name | Real method (if any) | Where the doc uses it |",
    ]
    missing = [h for h in headers if h not in doc]
    assert not missing, f"a table header row changed or was dropped: {missing}"
