#!/usr/bin/env python3
"""Generate the Phase 0.1 bridge table for tortoise #4282.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT
---------------------------------------
The first attempt at this artifact was hand-written prose containing `file:line`
citations. Five verification passes each found MORE that did not resolve (4, then
3, then 2, then 4), and a regex cleanup made it worse by leaving orphaned bare
integers that name no file and no symbol. **Hand-written line numbers do not
converge.** A citation that can drift from the code it cites is not evidence.

So the line numbers here are READ FROM THE SOURCE at build time and cannot drift.
The destination map below is DATA (reviewed once, by a human); every count,
every total, and every coverage claim is ARITHMETIC computed from that data
against the live registry. If the map and the registry disagree, this script
FAILS — it does not paper over the difference.

USAGE
    uv run python tools/bridge_table.py            # write the doc
    uv run python tools/bridge_table.py --check    # verify only, non-zero on drift

The two things this exists to prove, per #4282:
  1. Every current registry tool has exactly ONE destination on the new surface.
  2. Every target surface method has something real behind it — a `def` on
     TortoiseSDK — so a merged MCP tool is not dispatching into a void.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/bridge_table.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/bridge_table.py`"
    )

import argparse
import ast
import re
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.tool_registry import (  # noqa: E402
    RETIRED_TOOL_REGISTRY,
    TOOL_REGISTRY,
)

_REPLACEMENT_RE = re.compile(r"^([A-Za-z0-9_]+)")

REGISTRY_SRC = ROOT / "tortoise" / "tool_registry.py"
MCP_SRC = ROOT / "tortoise" / "mcp_server.py"
SDK_SRC = ROOT / "tortoise" / "sdk.py"
OUT = ROOT / "docs" / "product" / "bridge-table.md"

# ─────────────────────────────────────────────────────────────────────
# THE TARGET SURFACE (#4282). Tenancy is SDK/REST only — not on the MCP.
# ─────────────────────────────────────────────────────────────────────
TARGET_MCP = [
    "search_knowledge", "list_knowledge", "check_confidence", "get_entity",
    "get_historical_knowledge", "explore_connections", "graph_overview",
    "review_link_candidates", "poll_events", "check_connection",
    "create_entity", "register_source", "index_sources_from_directory",
    # #3540 ruling B: the consolidated source-management operation. It is on the
    # target because the owner approved it as the ONE way an agent switches
    # sources on/off — replacing two per-source tools with one, so the target
    # GROWS a name while the served surface SHRINKS a tool.
    "manage_sources",
    "mine_knowledge_from_session", "mine_knowledge_from_directory",
    "manage_source_trust", "link_entities", "write_question", "record_decision",
    "update_knowledge", "supersede_knowledge", "delete_knowledge",
    "adjust_relationship", "refresh_confidence", "approve_merge",
    # Owner decision 2026-09-21: KEPT. Dropping it would leave an agent that hits
    # the capture 409 ("Session recording is disabled for this graph") with no
    # recovery path inside MCP, since REST is unreachable from an MCP client.
    #
    # Note which SDK method backs it. A public `graph_set_recording` DOES exist on
    # TortoiseSDK, but it is a per-field setter, and the owner's Decision 3
    # deleted `set_memory_graph_name`/`set_memory_graph_backend` for exactly that
    # shape ("fields go on create, plus one partial update"). Folding the
    # override into `update_memory_graph` is the consistent reading, so the SDK
    # method stays discarded while the MCP tool survives. That makes
    # `graph_set_recording` this surface's one deliberate exception to "tenancy is
    # not on the MCP" - and the reason it needs a `team:manage`-scoped key.
    "graph_set_recording",
]

# ─────────────────────────────────────────────────────────────────────
# THE DESTINATION MAP — data, not arithmetic. One entry per CURRENT
# registry tool. Every value is a target-surface method, or one of:
#   "tenancy:SDK-method"  → moves to the tenancy block (not on the MCP)
#   "REMOVED"             → retires with no destination
# `_validate()` fails the build if any registry tool is missing from this map,
# if any key is not in the registry, or if a value is not recognised.
# ─────────────────────────────────────────────────────────────────────
DESTINATION = {
    # ── Core CRUD → create_entity ───────────────────────────────────
    "tortoise_create_point": "create_entity",
    "tortoise_create_subject": "create_entity",
    "tortoise_create_object": "create_entity",
    "tortoise_create_event": "create_entity",
    "tortoise_create_document": "create_entity",
    "tortoise_create_entity": "create_entity",
    "tortoise_create_operator": "link_entities",
    "tortoise_create_edge": "link_entities",
    "tortoise_create_source": "register_source",
    "tortoise_get": "get_entity",
    "tortoise_get_entity": "get_entity",
    "tortoise_get_point": "get_entity",
    "tortoise_get_operator": "get_entity",
    "tortoise_get_session": "get_entity",
    "tortoise_get_governance": "get_entity",
    "tortoise_update_point": "update_knowledge",
    "tortoise_update_entity": "update_knowledge",
    "tortoise_update": "update_knowledge",
    "tortoise_delete_point": "delete_knowledge",
    "tortoise_delete_entity": "delete_knowledge",
    "tortoise_delete": "delete_knowledge",
    "tortoise_retract_point": "update_knowledge",
    "tortoise_invalidate": "supersede_knowledge",
    "tortoise_supersede": "supersede_knowledge",
    # ── Search / read ───────────────────────────────────────────────
    "tortoise_search": "search_knowledge",
    "tortoise_query": "search_knowledge",
    "tortoise_paginated_query": "search_knowledge",
    "tortoise_query_points_by_tag": "search_knowledge",
    "tortoise_search_sessions": "search_knowledge",
    "tortoise_suggest_entry_points": "search_knowledge",
    "tortoise_issue_insight": "search_knowledge",
    "tortoise_list_sources": "graph_overview",
    "tortoise_list_topics": "list_knowledge",
    "tortoise_list_tags": "graph_overview",
    "tortoise_list_namespaces": "list_knowledge",
    "tortoise_list_pointkinds": "graph_overview",
    "tortoise_list_graphs": "tenancy:list_memory_graphs",
    "tortoise_taxonomy": "graph_overview",
    "tortoise_overview": "graph_overview",
    "tortoise_status": "graph_overview",
    "tortoise_health": "graph_overview",
    "tortoise_stale": "graph_overview",
    "tortoise_check_structure": "graph_overview",
    "tortoise_audit": "graph_overview",
    "tortoise_summarize_structure": "graph_overview",
    "tortoise_validate_domain": "graph_overview",
    "tortoise_dream_health": "graph_overview",
    "tortoise_entity_profile": "explore_connections",
    # ── Confidence / reasoning ──────────────────────────────────────
    "tortoise_get_confidence": "check_confidence",
    "tortoise_compute_confidence": "check_confidence",
    "tortoise_recall": "check_confidence",
    "tortoise_calibrate_summary": "check_confidence",
    "tortoise_belief_timeline": "check_confidence",
    "tortoise_provenance": "check_confidence",
    "tortoise_session_context": "check_confidence",
    "tortoise_dream": "refresh_confidence",
    "tortoise_promote_point": "refresh_confidence",
    "tortoise_set_point_baseline": "refresh_confidence",
    # ── Traversal ───────────────────────────────────────────────────
    "tortoise_traverse": "explore_connections",
    "tortoise_expand_relationships": "explore_connections",
    "tortoise_get_events": "get_entity",
    "tortoise_events_poll": "poll_events",
    # ── Review queues ───────────────────────────────────────────────
    "tortoise_review_connections": "review_link_candidates",
    "tortoise_find_cross_lens_candidates": "review_link_candidates",
    "tortoise_list_dedup_candidates": "review_link_candidates",
    "tortoise_list_batch": "list_knowledge",
    "tortoise_list_batches": "list_knowledge",
    # ── Sources and trust ───────────────────────────────────────────
    "tortoise_set_source_tier": "manage_source_trust",
    "tortoise_assess_source": "manage_source_trust",
    "tortoise_get_source_reliability": "list_knowledge",
    # ── Ingestion ───────────────────────────────────────────────────
    "tortoise_index_files": "index_sources_from_directory",
    "tortoise_index_sessions": "index_sources_from_directory",
    "tortoise_ingest_corpus": "index_sources_from_directory",
    "tortoise_ingest": "sdk:write_knowledge_batch",
    "tortoise_session_capture": "mine_knowledge_from_session",
    "tortoise_mine_conversations": "mine_knowledge_from_directory",
    "tortoise_backfill_v25": "REMOVED",
    # ── Operators / relations ───────────────────────────────────────
    "tortoise_mitigate_operator": "adjust_relationship",
    "tortoise_operator_action": "adjust_relationship",
    "tortoise_annotate_operator": "adjust_relationship",
    # ── Decisions and questions ─────────────────────────────────────
    "tortoise_file_decision": "write_question",
    "tortoise_file_human_approval": "record_decision",
    "tortoise_approve_merge": "approve_merge",
    # ── Journal — RECONCILED to the owner-approved canonical menu ────
    # Two owner-approved docs disagreed for the whole life of this table: the
    # canonical MCP menu ABSORBS all three (row 10 absorbs `diary_write`, row 13
    # absorbs `checkpoint`, and "Removed entirely" dissolves `diary_read` into
    # `list_knowledge(kind="diary")`), while `beta-sdk-surface.md` filed the same
    # three under "Named but not solved" (post-beta, unlisted, #4667) — and this map
    # followed beta, so it read all three as `REMOVED`.
    #
    # The owner ruled which governs (#4282, 2026-10-09): "it maps all three
    # journaling tools to REMOVED, while the approved menu absorbs … The approval
    # wins; the table is the defect." Each name therefore retires INTO the
    # replacement that already exists for it, and the sibling SDK rename table
    # (`tools/sdk_rename_table.py`) carries the same reconciliation so the two
    # artifacts cannot contradict each other.
    #
    # OVERRIDES: the beta doc's `#4667` post-beta/unlisted reading of the journal —
    # the canonical menu's absorptions govern, so each journal name retires into the
    # replacement that already exists for it.
    "tortoise_diary_write": "create_entity",              # canonical row 10
    "tortoise_checkpoint": "mine_knowledge_from_session",  # canonical row 13 (`capture_knowledge`, renamed by beta)
    "tortoise_diary_read": "list_knowledge",               # canonical "Removed entirely": `list_knowledge(kind="diary")`
    # #3540 ruling B: the consolidated source-management operation.
    "tortoise_manage_sources": "manage_sources",
    # ── Onboarding and packs ────────────────────────────────────────
    "tortoise_onboarding_state": "REMOVED",
    "tortoise_onboarding_seed": "REMOVED",
    "tortoise_onboarding_demo_create": "REMOVED",
    "tortoise_onboarding_github_connect": "REMOVED",
    # #3540 ruling B: retired INTO the consolidated operation — the 0.1
    # destination is the tool that replaces it, so the redirect agrees.
    "tortoise_onboarding_github_index": "manage_sources",
    "tortoise_onboarding_github_status": "REMOVED",
    # #3540 ruling B: retired INTO the consolidated operation — the 0.1
    # destination is the tool that replaces it, so the redirect agrees.
    "tortoise_onboarding_session_recording": "manage_sources",
    "tortoise_pack_install": "REMOVED",
    "tortoise_packs_list": "REMOVED",
    # ── Tenancy / admin ─────────────────────────────────────────────
    "tortoise_org_create": "tenancy:create_memory_graph",
    "tortoise_graph_set_recording": "graph_set_recording",
    # ── Analytics ───────────────────────────────────────────────────
    "tortoise_analyze": "REMOVED",
}

# Namespaces whose suffix must be a real method name. `tenancy:` = the tenancy block
# (SDK/REST only); `sdk:` = a builder-only SDK method that is NOT on the MCP.
NAMESPACES = ("tenancy:", "sdk:")

# A target MCP tool whose BACKING method has a different name. Without this the
# existence check resolves a target by its own name, and `graph_set_recording`
# would "resolve" to the per-field SDK method that the owner's Decision 3
# DELETES — reporting the target as implemented when the method is going away.
# The override folds into `update_memory_graph`, so that is what the tool will
# call, and that is what must exist for the target to be implementable.
MCP_BACKING = {"graph_set_recording": "update_memory_graph"}

# C1's one Finding column. A single constant so a test can pin the whole
# section by equality: the section is headed "target tools with NO method",
# and a rewrite of this string stated the opposite on every row, green.
C1_FINDING = "no `def` on TortoiseSDK"

# Part A's `Exists` cell when the method is missing. The cross-reference is a
# claim about WHERE the finding is listed, and it was free: pointing at C2 --
# the unresolvable-BINDINGS section -- sent readers to the wrong table.
C1_REF = "**no — Part C1**"

# ─────────────────────────────────────────────────────────────────────
# AUTHORED DATA — the one column that is a design fact rather than a
# derivation. Everything else in Part A (which targets are merged, and
# whether their method exists) is computed.
# ─────────────────────────────────────────────────────────────────────
DISCRIMINATORS = {
    "create_entity": "`type=`",
    "list_knowledge": "`kind=`",
    "graph_overview": "`section=`",
    "refresh_confidence": "`scope=`",
    "search_knowledge": "`mode=` (full-text / hybrid)",
    "get_entity": "`type=`",
    "delete_knowledge": "*(node or link)*",
    "update_knowledge": "*(which fields — incl. the retract fields)*",
    "link_entities": "*(relation kind)*",
    "adjust_relationship": "*(strength)*",
    "supersede_knowledge": "*(link policy)*",
}

VALID_DEST = set(TARGET_MCP) | {"REMOVED"}

# ─────────────────────────────────────────────────────────────────────
# THE RETIREMENT REASONS — the reconciliation PROOF (#4282).
#
# `REMOVED` is a destination with NO target, and before this map it was a bare
# catch-all: a name could be retired by typing `REMOVED` and nothing recorded WHY.
# The owner's prerequisite for the migration (2026-10-09) is that every served name
# "resolves to a target that exists in the approved list, or to an explicit, reasoned
# retirement" — so a retirement carries its reason here, and `_validate` requires
# this map's key set to be EXACTLY the set of tools mapped to `REMOVED`. A new
# retirement therefore cannot appear without a recorded reason, and a resolved one
# cannot leave a stale reason behind (both directions fail the build).
#
# The other two non-target destinations are namespaced and self-describing:
# `tenancy:` is account tenancy (SDK/REST, off the agent surface) and `sdk:` is a
# builder-only SDK method — neither is a retirement, and neither owes a reason here.
# ─────────────────────────────────────────────────────────────────────
REMOVALS: dict[str, str] = {
    "tortoise_analyze": (
        "canonical-mcp-tools.md §Removed entirely — spends LLM tokens at read "
        "time; read-time LLM is not offered (write-time LLM cost is ours)"
    ),
    "tortoise_backfill_v25": (
        "canonical-mcp-tools.md §Removed entirely — a one-shot migration to "
        "ONTOLOGY v2.5; pre-beta there are no legacy v2.5 graphs to migrate, so "
        "the tool has no caller"
    ),
    "tortoise_onboarding_state": (
        "settled 2026-09-22 (canonical §run_onboarding) — `run_onboarding` dropped "
        "for `check_connection`; this tenant-visible READ is rehomed to the tenancy "
        "block (SDK/REST, GET /v1/onboarding/state), so no capability is deleted"
    ),
    "tortoise_onboarding_github_status": (
        "settled 2026-09-22 (canonical §run_onboarding) — `run_onboarding` dropped "
        "for `check_connection`; this tenant-visible READ is rehomed to the tenancy "
        "block (SDK/REST, GET /v1/onboarding/github/status)"
    ),
    "tortoise_onboarding_seed": (
        "settled 2026-09-22 — `run_onboarding` is not on the MCP target; a setup "
        "member, not a tenant-visible read, so it retires with no destination"
    ),
    "tortoise_onboarding_demo_create": (
        "settled 2026-09-22 — `run_onboarding` is not on the MCP target"
    ),
    "tortoise_onboarding_github_connect": (
        "settled 2026-09-22 — `run_onboarding` is not on the MCP target"
    ),
    "tortoise_packs_list": (
        "settled 2026-09-22 — SDK/REST only and POST-BETA (#4663); the tenant read "
        "is preserved on the SDK/REST pack surface, and no competitor exposes pack "
        "installation on its agent API"
    ),
    "tortoise_pack_install": (
        "settled 2026-09-22 — SDK/REST only and POST-BETA (#4663); the "
        "tenant-visible WRITE is preserved there rather than folded into an "
        "operator-only tool"
    ),
}

# Destinations the SIBLING SDK rename table (`docs/product/sdk-rename-table.md`, GENERATED
# ON DEMAND — not committed, #5373; render with `uv run python tools/sdk_rename_table.py`;
# `§C3b`, and
# its C6 fold record) records as WRONG. `tools/sdk_rename_table.py` reconciles the two
# artifacts and determined that beta's row — and the owner-approved MCP list it rests on —
# puts these two on `update_knowledge`, not `refresh_confidence`.
#
# The destination map below is OWNER-APPROVED, so the disagreement is DISCLOSED on the row
# and collected in §D2c — it is never edited here. That is the same rule D2 already states:
# changing an owner-approved destination is not a build step.
# tool -> (the reading the sibling carries, the authority for it).
CONTESTED_DESTINATION: dict[str, tuple[str, str]] = {
    "tortoise_promote_point": (
        "update_knowledge",
        "The owner-approved MCP list absorbs `promote_point` into `revise_knowledge`, whose "
        "beta successor is `update_knowledge`; beta names the new status a FIELD on "
        "`update_knowledge`, and promote's incident-operator cascade and approval gate ride "
        "with it — `refresh_confidence` recomputes confidence and covers neither.",
    ),
    "tortoise_set_point_baseline": (
        "update_knowledge",
        "Same approved absorption as `tortoise_promote_point`; beta names the starting belief "
        "a FIELD on `update_knowledge`, not a separate verb.",
    ),
}


# ─────────────────────────────────────────────────────────────────────
# THE OWNER-APPROVED MENU — the SECOND authority, read from a doc (#4513).
#
# Part D reads `beta-sdk-surface.md`. `canonical-mcp-tools.md` is the other
# owner-approved doc (`approval_status: approved`, approvedBy daniel-ospina,
# approval_pr 4120) and its `Absorbs` cell is the owner's OWN record of which
# current name folds into which target — a binding this file never consulted, so
# a row could contradict it and the build stayed green (#4513 §1/§2). Where the
# two approved docs disagree there is no ruling to apply, and picking one here
# would silently reverse an owner decision: the disagreement is DISCLOSED (§D4)
# with both citations, and the map is NOT edited.
#
# What this section CAN decide is narrower and fully mechanical: whether a target
# the row's citation names is one the approved menu actually absorbs this method
# into. That is what turns `belief_timeline`'s "`poll_events` where they are a
# timeline" from an open question into a REFUTED reading — `poll_events`'s
# approved row absorbs `events_poll` and nothing else.
# ─────────────────────────────────────────────────────────────────────
APPROVED_DOC = ROOT / "docs" / "product" / "canonical-mcp-tools.md"

# The doc states, in its own prose, that some members are absorbed only
# TRANSITIVELY ("`paginated_query` and `query_points_by_tag` sit inside `query`").
# Reading that is not an inference — it is the doc's own closure, so it is parsed
# rather than hand-copied: a copy would be a second answer that can drift.
#
# `sit\s+inside`, NOT the literal phrase: the doc line-wraps one of its four
# statements mid-phrase — "`index_sessions` and `ingest_corpus` sit\ninside
# `index_files`" — so a literal match parsed only three of the four and left
# `index_files` without the two members the doc gives it. Nothing failed: the row
# bound below cannot see a closure, so a closure the doc STATES and the parse DROPS was
# indistinguishable from one the doc never stated (#8930 review).
_SIT_INSIDE = re.compile(r"([`\w,\s]+?)\s+sit\s+inside\s+`([a-z_][a-z0-9_]*)`")
# The beta-successor sentence — the ONLY place the doc states a successor relation:
# row 17's "the beta target **splits this into `update_knowledge` +
# `supersede_knowledge`**". Parsed rather than token-scanned, because scanning any
# TARGET name in the cell mints membership out of prose: row 23 says `run_onboarding`
# was "dropped for `check_connection`" — a REPLACEMENT, the OPPOSITE relation — and a
# scan made `check_connection` a member of row 23 and its only beta successor (#8930).
_SPLIT_INTO = re.compile(r"splits this into([^;]*)")
# An `Absorbs` cell is a member LIST, then an em dash, then prose (row 17's fields, row
# 22's "OFF the MCP", row 23's replacement). Only the list is membership.
_CELL_PROSE = "—"
# `onboarding_*` — the doc's shorthand for a family of names (row 23).
_WILDCARD = re.compile(r"`([a-z_][a-z0-9_]*)\*`")
# A numbered table row. The trailing `|` is a SYNTACTIC requirement of the table shape, not
# a completeness test (completeness is decided by the table BODY — see `_APPROVED_HEADER`
# below). `cells` is split out of ONE line, so a row whose `Absorbs` cell REFLOWED onto a
# second line parsed as a row with a SHORTER member list: measured, adding a newline inside
# row 21's cell still parsed 23 rows with no guard firing, `mitigate_operator` silently left
# the row, and the regenerated artifact reported
# `tortoise_mitigate_operator` as "*(no approved row names it)*" / **refuted**, shifting
# the partition from 5/7/0 to 4/7/1 — a false claim about the owner-approved doc, in the
# artifact #4513 exists to make trustworthy (#8930 review, P1).
# Deliberately NOT a widening that joins continuation lines: a wrapped row must be
# DETECTED (`_APPROVED_ROW_START` + the diagnostics in `_parse_approved`), never accepted.
_APPROVED_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([a-z_][a-z0-9_]*)`\s*\|.*\|\s*$")
# ⛔ `_APPROVED_ROW` only proves the line ENDS in `|` — it is NOT a completeness test, and
# reading it as one left the guard above bypassable. When a wrap leaves a `|` at the END of
# the first physical line (`…annotate_operator, |` ⏎ `| mitigate_operator |`) the prefix
# matches and `cells` is split from line 1 alone; the continuation matches no row pattern
# and was never inspected. Measured on the real doc, row 21, BOTH shapes built GREEN and
# wrote the artifact (`rows=23`, `row_errors=[]`, `main()` → 0) while `mitigate_operator`
# left the row — the same false `refuted` claim, on a green build with no diagnostic, and
# the NATURAL way a person or agent repairs a wrapped row (keep each line looking like
# table syntax) (#8930 review, round 2).
#
# So completeness is decided by the TABLE BODY, not by the row regex: from the
# `| # | Tool | Absorbs |` header to the next blank line, the ONLY legal lines are the
# header's own separator (before any row) and `_APPROVED_ROW_START` rows — anything else
# is a REFLOWED continuation and fails the build (`_body_error`).
#
# ⛔ Round 3: that test was STILL bypassable, three ways, all measured green on the real
# doc at row 21 (`rows=23`, `row_errors=[]`, `main()` → 0, artifact WRITTEN, partition
# 5/7/0 → 4/7/1) — the same false `refuted` (#8930 review, round 3):
#   * the continuation is ROW-SHAPED (`| 21 | `adjust_relationship` | mitigate_operator |`)
#     so the body saw a legal row, and the row loop — keyed by NAME with an unconditional
#     assign — REPLACED row 21 with the one-member remainder. Nothing bounded duplicates:
#     the count guard checks only for MISSING numbers, and a duplicate number under a
#     DIFFERENT name built green with 24 rows. Fixed in the row loop, not here: a repeated
#     number OR a repeated name is a row error (`_row_identity_error`).
#   * the wrap straddled a BLANK line, so the orphan fell outside every body — fixed here:
#     a first non-blank line after the body closes that is table-fragment-shaped is a
#     fragment of the row it followed.
#   * the continuation was SEPARATOR-SHAPED (`|---|---|---|`), which was skipped as table
#     syntax — fixed here: a separator AFTER a row has started is not the header's.
#
# ⛔ Round 4: the blank-straddling rule left TWO residuals, both measured green on the real
# doc at row 21 (`rows=23`, `row_errors=[]`, `main()` → 0, artifact WRITTEN, partition
# 5/7/0 → 4/7/1, `mitigate_operator` quoted from the TRUNCATED row as though it were the
# whole thing) — the same false `refuted` (#8930 review, round 4):
#   * the fragment was spelled with NO PIPE (`… annotate_operator, |` ⏎ blank ⏎
#     `mitigate_operator`), which a PIPE-BOUND test passes straight through. The test is
#     therefore a FRAGMENT test, not a pipe test: an `Absorbs` remainder is a name list, so
#     the line — with its decoration stripped — is a fragment when it is a comma-separated
#     list of names (`_is_member_list`).
#   * the fragment was not the FIRST non-blank line after the close (a prose line landing
#     between the blank and the fragment), and the row was consumed on first use, so the
#     fragment was never inspected. The scan now runs to the next TABLE instead
#     (`_starts_a_table`), so any number of intervening prose lines is fine.
#
# ⛔ Round 5: round 4's predicate stripped only trailing commas/whitespace before demanding
# every token be a known name — i.e. it enumerated SPELLINGS, which is unbounded. A leading
# comma, a trailing period, `**emphasis**`, an inline `*(…)` annotation and a break landing
# INSIDE a name each defeated it. Measured on the real doc at row 21, over every single
# break position in that cell (52 of them): 48 built GREEN, wrote the artifact, and asserted
# `mitigate_operator` was "*(no approved row names it)*" / refuted — partition 5/7/0 →
# 4/7/1; only the 4 whitespace-boundary breaks were caught. The predicate now NORMALISES
# rather than enumerates (`_normalise_member_token`): per-token separators, trailing
# punctuation and emphasis are stripped, a trailing `*(…)` / `**…**` annotation is dropped,
# empty tokens are discarded — and the NORMALISED tokens are what is tested. A member list
# is certain when every token is a name this doc absorbs; it fails CLOSED when every token is
# a bare identifier, because a name broken across the wrap is exactly that (`ate_operator`,
# `perator`, `action`). Re-measured on the same 52 positions: 52 fail closed, 0 written.
#
# What is caught, stated exactly, because an over-claim here is what let round 3 read as
# complete: every wrap whose remainder stays TABLE SYNTAX (pipe-bounded, separator-shaped, or
# row-shaped while REUSING the row's number or name — `_row_identity_error`) or is a MEMBER
# LIST, in any decoration, fails the build. TWO shapes it does NOT catch, both recorded gaps
# and not closed ones:
#   * a remainder that reflowed into an ORDINARY SENTENCE (`… and the duplicate name is
#     dropped`). Its commas separate words, not names, so it is indistinguishable from the
#     doc's own post-table prose and reporting it would refuse real paragraphs.
#   * a ROW-SHAPED remainder that states a NEW number AND a NEW name (`| 24 | `totally_new`
#     | mitigate_operator |`). Nothing marks it as a phantom: the row loop sees a first
#     sighting of both identity fields and the count guard is a floor, so it builds green
#     with 24 rows and fabricates `totally_new` as the approved home. Closing it needs a way
#     to tell a real row from a fragment after the table has CLOSED, which this parse does
#     not have — so it is stated here rather than claimed as caught.
# A row whose cell continued is therefore not "unrepresentable however it is spelled": it
# cannot be expressed by WIDENING the row parse, because the orphan is the datum.
# The header is what ARMS the body-integrity pass below (`in_table`), so its own match count
# is bounded too (`APPROVED_MIN_BODIES`): a guard that never arms cannot fail.
_APPROVED_HEADER = re.compile(r"^\|\s*#\s*\|\s*Tool\s*\|\s*Absorbs\s*\|\s*$")
_APPROVED_SEPARATOR = re.compile(r"^\|[\s:|-]+\|\s*$")
# The same prefix on its own, so an INCOMPLETE row is REPORTED instead of skipped: a line
# that starts like a row and does not end in `|` is a reflow, and a two-cell row (`| 3 |
# `recall_beliefs` |`) has no `Absorbs` cell to read at all.
_APPROVED_ROW_START = re.compile(r"^\|\s*(\d+)\s*\|\s*`([a-z_][a-z0-9_]*)`\s*\|")
_IDENT = re.compile(r"[a-z_][a-z0-9_]*")

# The doc's title claims 23 rows, and every row it states is numbered. The bound is the
# doc's own count, not a loose floor: at 20, THREE rows could vanish from the parse with a
# green build, and a lost row 3 turns `belief_timeline`/`provenance`/`session_context` into
# "no approved row absorbs it" — the exact false claim §D4 exists to prevent. Adding a row
# keeps the build green; losing one (or renumbering) needs a human to update this.
APPROVED_MIN_ROWS = 23

# The doc states FOUR transitive-closure statements, over four containers (`get`,
# `overview`, `query`, `index_files`). Counted separately from the rows because dropping
# one leaves every ROW intact and the build green, while the members it carried are gone:
# the container's absorbing row loses them from `members`, and the next disputed row naming
# them renders "no approved row absorbs it" — a row the doc absorbs it in. A reflow, not a
# doc edit, is the likely cause, which is exactly what this bound must catch.
APPROVED_MIN_CLOSURES = 4

# The doc states exactly ONE beta split (row 17's "splits this into `update_knowledge` +
# `supersede_knowledge`"). Bounded for the same reason as the closures: rewording the
# clause drops the successors silently, which flips `tortoise_invalidate` from
# `successor` (support) to `rehomed` and changes an owner-facing count with a green build.
APPROVED_MIN_SPLITS = 1

# The doc states TWO approved tables (two `| # | Tool | Absorbs |` headers, ~line 142 and
# ~line 156). The body-integrity pass — the row-reflow/orphan/separator test, the ONLY
# detector for a wrap whose first physical line still ends in `|` — is entered from a
# header match, and nothing else in the guard set notices a table body it never scanned.
# So a benign header edit (a fourth column, the third column reworded) silently disarms
# the whole family while every row still parses: 23 rows, gapless numbering, closures and
# split green, build green, and the truncated row regenerates the artifact with a false
# 'no approved row absorbs it'. Bounding the count makes that edit loud (#8930 review, P1).
APPROVED_MIN_BODIES = 2


def _known_names() -> set[str]:
    """Every identifier an `Absorbs` cell can legitimately name.

    Prose sits in the SAME cell as the members (row 17 explains beta's split in a
    sentence), so a bare token scan would mint `fields`/`become`/`into` as absorbed
    names. Restricting to the known universe is what keeps the parse honest without
    needing the cell to be machine-formatted.
    """
    names = set(TARGET_MCP)
    for r in _registry_rows():
        if r["sdk_method"]:
            names.add(r["sdk_method"])
        names.add(r["name"].removeprefix("tortoise_"))
    return names


def _split_clauses(cell: str) -> list[dict]:
    """Each `splits this into ...` clause: the targets it states, and any it cannot resolve.

    Only a `splits this into` clause counts, and only its BACKTICKED names. The rest of the
    cell states OTHER relations — row 22 is "OFF the MCP", row 23 was "dropped for
    `check_connection`" — so a scan of every TARGET name in the cell read that REPLACEMENT
    as a beta successor of the very row it replaced.

    A name the clause states that the target surface does not have is kept as
    `unresolved` rather than DISCARDED: dropping it narrows the row's successors with no
    error, which flips a `successor` reading (support) to `rehomed` (a disagreement) and
    moves a count in the owner-facing table. Resolution is the caller's fail-closed check.
    """
    out: list[dict] = []
    for m in _SPLIT_INTO.finditer(cell):
        named = re.findall(r"`([a-z_][a-z0-9_]*)`", m.group(1))
        out.append(
            {
                "targets": sorted(t for t in named if t in TARGET_MCP),
                "unresolved": sorted(t for t in named if t not in TARGET_MCP),
            }
        )
    return out


def _cell_members(cell: str, name: str, known: set[str]) -> set[str]:
    """The names the cell's member LIST states — the prose after the em dash is not one.

    An `Absorbs` cell is a comma list, then an em dash, then prose. The prose states OTHER
    relations: row 17's "promote/baseline become fields on `update_knowledge`", row 22's
    "OFF the MCP", row 23's "dropped for `check_connection`". Scanning the whole cell for
    known names minted all three as MEMBERSHIP, which made `check_connection` a member of
    `run_onboarding` — a row the doc says was dropped FOR it. `members` is what the
    verdicts call a row's approved HOME, so a prose-minted member is a false home.

    The rule cuts both ways, so `_prose_names` reports the mirror risk: a CURRENT name
    stated after the dash is either prose ABOUT a tool or a member list the em dash
    misplaced. Only the first is legitimate for a target-surface name (row 17's "fields on
    `update_knowledge`", row 23's "dropped for `check_connection`"), which is what the
    guard keys on.

    A `prefix_*` token is a FAMILY, and row 23 states its members that way ("the seven
    `onboarding_*`"). Expanding it is reading the doc, not inferring: without it the seven
    real tools read as "no approved row absorbs" — the opposite of the row.
    """
    head = cell.split(_CELL_PROSE, 1)[0]
    members = {t for t in _IDENT.findall(head) if t in known and t != name}
    for prefix in _WILDCARD.findall(head):
        members |= {k for k in known if k.startswith(prefix) and k != name}
    return members


def _prose_names(cell: str) -> list[str]:
    """Known names stated in a cell's PROSE that are not target-surface names.

    A target name in the prose is a sentence about a tool (row 23's "dropped for
    `check_connection`"); a CURRENT tool name there is a member list the em dash was
    misplaced into — which silently strips that member, and for a disputed row prints "no
    approved row absorbs it" about the row the doc absorbs it in.
    """
    if _CELL_PROSE not in cell:
        return []
    known = _known_names()
    post = cell.split(_CELL_PROSE, 1)[1]
    # NOT backtick-only: a misplaced list can arrive bare ("… mitigate_operator —
    # annotate_operator also"), and a bare name is just as lost as a quoted one.
    return sorted(
        {n for n in _IDENT.findall(post) if n in known and n not in TARGET_MCP}
    )


def _approved_closures(text: str) -> list[tuple[str, set[str]]]:
    """`X, Y sit inside Z` — Z absorbs X and Y transitively, per the doc's own prose.

    Only the BACKTICKED names are members: the span also reads "and" in "X and Y sit
    inside Z", and a member span that is not a name cannot be checked for resolution.
    """
    return [
        (m.group(2), set(re.findall(r"`([a-z_][a-z0-9_]*)`", m.group(1))))
        for m in _SIT_INSIDE.finditer(text)
    ]


def _row_identity_error(
    name: str,
    lineno: int,
    seen_numbers: dict[int, str],
    seen_names: dict[str, int],
) -> str | None:
    """The diagnostic for a row that REPEATS another row's number or name, else `None`.

    Two physical rows cannot both be row N, and two rows cannot share one name: `menu` is
    keyed by NAME with an unconditional assign, so a repeat does not merely add a row — the
    later one REPLACES the earlier one, and the replaced row's `Absorbs` cell silently
    leaves the parse. Measured on the real doc (row 21): a row-shaped continuation of a
    wrap replaced the complete row with its one-member remainder and built GREEN with the
    artifact written; a repeated number under a DIFFERENT name built green with 24 rows.
    Neither is visible to the count guard, which is a floor and checks only for MISSING
    numbers (#8930 review, round 3, P1).

    A repeated NUMBER and a repeated NAME are separate claims, so both are reported — the
    earlier row is named in each, because "which occurrence is the real one" is exactly
    what a reader needs and what the parse cannot decide.
    """
    earlier_number = seen_numbers.get(lineno)
    earlier_name = seen_names.get(name)
    if earlier_number is not None and earlier_name is not None:
        return (
            f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} REUSES both the "
            f"number {lineno} and the name `{name}` of the row above it; two physical rows "
            "cannot be one row, so the later one is DISCARDED and every member that is "
            "only on it silently leaves the row — which renders as a false 'no approved "
            "row absorbs it' for each of them"
        )
    if earlier_number is not None:
        return (
            f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} REUSES row number "
            f"{lineno}, already stated for row {lineno} (`{earlier_number}`); two physical "
            "rows cannot be one row, and the menu is keyed by name, so the number tells "
            "the reader which row vanished while the parse cannot — a same-named repeat "
            "silently REPLACES the earlier row, and a differently-named one adds a row "
            "the doc does not state. Either way the discarded row renders as a false "
            "'no approved row absorbs it'"
        )
    if earlier_name is None:
        return None  # a first sighting: this row claims no identity another row holds
    return (
        f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} REUSES the name "
        f"`{name}` of row {earlier_name}; the menu is keyed by NAME with an unconditional "
        f"assign, so this row silently REPLACES row {earlier_name} and its whole `Absorbs` "
        "cell leaves the parse — every member only on it renders as a false 'no approved "
        "row absorbs it'"
    )


# One token of a member list, after NORMALISATION: a bare lower-case name and nothing
# else. This is the shape a member list has and a sentence does not, and it is what makes
# the fail-closed branch of `_is_member_list` safe — a line whose every comma-token is one
# of these is a name list, however its names are spelled, because a sentence's tokens carry
# spaces and sentence punctuation.
_MEMBER_TOKEN = re.compile(r"[a-z_][a-z0-9_]*")
# The cell's own separator. `,` is what the `Absorbs` cell uses; `;` is the same relation
# spelled differently, and a reflow can leave either at the edge of a line.
_MEMBER_SEP = re.compile(r"[,;]")
# A trailing INLINE ANNOTATION attached to the last name — row 12's real cell qualifies its
# names exactly this way (`… *(operator-only)*`), so a remainder carrying one is still the
# cell's own text. The `(?<=\S)` is load-bearing: without it the `**…**` arm would swallow a
# token that is ENTIRELY emphasis (`**mitigate_operator**`) and leave nothing behind, which
# would turn the very spelling this is meant to catch into `False`.
_TRAILING_ANNOTATION = re.compile(r"(?<=\S)\s*(?:\*\([^)]*\)\*|\*\*[^*]+\*\*)\s*$")
# Trailing punctuation and emphasis a reflow can leave on a name: a cell's separator kept at
# the end of a line, a sentence period, markdown emphasis, a code backtick.
_TOKEN_TRAILING = ".,;*_`"


def _normalise_member_token(token: str) -> str:
    """One member-list token with the DECORATION a reflow can leave on it removed.

    Written as a NORMALISATION because enumerating the ways a wrap can be spelled is
    unbounded, and four rounds of doing that left the predicate answering `False` for shapes
    a paste or a soft reflow produces routinely: a leading separator, a trailing period,
    `**emphasis**`, an inline `*(…)` annotation, a break landing INSIDE a name. Measured on
    the real doc, row 21 — 48 of the 52 single-break positions built GREEN with the false
    refutation in the artifact (#8930 review, round 5, P2).
    """
    token = token.strip()
    token = token.strip("`*").strip()  # markdown quotes/emphasis around the whole token
    # Drop a trailing annotation BEFORE the trailing-punctuation pass, so the `*` and `)`
    # that end `*(operator-only)*` cannot be half-stripped into an unreadable token.
    token = _TRAILING_ANNOTATION.sub("", token)
    return token.rstrip(_TOKEN_TRAILING).strip()


def _is_member_list(stripped: str, known: set[str]) -> bool:
    """Whether `stripped` is a bare `Absorbs` MEMBER LIST — the pipe-less fragment shape.

    An `Absorbs` cell is a comma-separated list of names, so the remainder of a cell that
    reflowed onto its own line is that list too: `mitigate_operator`, or
    `` `mitigate_operator`, `annotate_operator` ``. The tokens are NORMALISED first
    (`_normalise_member_token`) — leading separators, a trailing period, emphasis, an inline
    `*(…)` annotation and a token that normalises to nothing are all just decoration a wrap
    can carry — and the normalised tokens are what get tested.

    Then TWO tests, and the second is what makes the guard survive a break landing inside a
    NAME (46 of the 52 positions in row 21's cell, the largest green slice):

      * every normalised token is a name this doc can absorb — a certain member list;
      * else every normalised token is a bare identifier (`_MEMBER_TOKEN`) — a name list by
        SHAPE, carrying at least one name this doc does not have. A name broken across the
        wrap is exactly that (`ate_operator`, `perator`, `action`), so it fails CLOSED.

    What keeps genuine prose green is the SHAPE, not a word list: a sentence's tokens carry
    spaces and sentence punctuation, so they are never bare identifiers. Measured on the
    real doc — every line of both post-table gaps (9 after the WRITE table, 1 after the READ
    table), and every heading, bullet and blockquote there, answers `False`
    (`test_the_real_approved_doc_parses_every_row_with_no_row_error`,
    `test_the_normalised_member_list_test_still_leaves_real_prose_green`).
    """
    body = _TRAILING_ANNOTATION.sub("", stripped.strip())
    tokens = [t for t in (_normalise_member_token(x) for x in _MEMBER_SEP.split(body)) if t]
    if not tokens:
        return False
    if all(t in known for t in tokens):
        return True
    return all(_MEMBER_TOKEN.fullmatch(t) for t in tokens)


def _is_table_fragment(stripped: str, known: set[str]) -> bool:
    """Whether a line after a CLOSED body is a reflowed fragment of the row it followed.

    Two spellings, and the round-3 rule was only the first: PIPE-BOUNDED (it starts or ends
    with `|`, i.e. table syntax), or a bare MEMBER LIST (`_is_member_list`). A paste or a
    soft reflow produces either. Anything else is prose, which is not reported — see
    `_is_member_list` for why that is a measurement and not a convenience.
    """
    if stripped.startswith("|") or stripped.endswith("|"):
        return True
    return _is_member_list(stripped, known)


def _starts_a_table(lines: Sequence[str], i: int) -> bool:
    """Whether `lines[i]` is the HEADER of a table, so the post-close scan must stop there.

    The scan after a closed body runs past intervening prose, and the doc has an UNRELATED
    table further down (`| Name | Why |`) whose rows are pipe-bounded too — scanning into it
    would refuse the document the owner approved. A markdown table always states its header
    and its `|---|` separator together, while an orphaned continuation is a single line, so
    "a pipe-bounded line whose next non-blank line is a separator" separates the two
    structurally rather than by distance (#8930 review, round 4).

    The approved header is matched earlier in the loop, so this covers every OTHER table.
    The residual is a fragment that is itself immediately followed by a new table's
    separator; that spelling is contrived and is not claimed to be caught.
    """
    line = lines[i].strip()
    if not (line.startswith("|") or line.endswith("|")):
        return False
    for following in lines[i + 1 :]:
        following = following.strip()
        if following:
            return bool(_APPROVED_SEPARATOR.match(following))
    return False


def _body_error(
    stripped: str,
    owner: tuple[int, str] | None,
    *,
    closed_by_blank: bool,
    known: set[str] | None = None,
) -> str | None:
    """The approved-table body test: `None` when `stripped` is legitimate table syntax.

    Three ways a line inside the table is NOT legitimate, each measured green on the real
    doc with the artifact written and `mitigate_operator` falsely refuted (#8930 review,
    rounds 3-5, P2):

      * a non-blank line after the body closes on a BLANK line, when it is
        table-fragment-shaped: PIPE-BOUNDED (starts OR ends with `|`), or a MEMBER LIST —
        a comma-separated run whose every token is a bare NAME once the cell's own
        decoration is normalised away (separators, a trailing period, emphasis, an inline
        `*(…)`), INCLUDING one carrying a name this doc does not have, which is what a break
        landing inside a name produces. The wrap straddles the blank that ends the table, so
        the orphan belongs to no body at all and no in-body rule can see it. Prose is NOT
        reported: real paragraphs follow both of the doc's tables, and a sentence's tokens
        carry spaces and sentence punctuation, so no sentence can take a name list's shape
        (`_is_member_list`).
      * a SEPARATOR after any row has started. The header's own separator comes BEFORE every
        row (`owner is None`), so a later one is a row's continuation reflowed into a
        `|---|` line — table syntax that silently swallowed the member.
      * anything else that is neither a row prefix nor the header separator: the original
        orphan, the row's remainder left on its own line.

    `owner` is the row the line continues — the one above it inside a body, or the one the
    body closed on. The diagnostic names it, because the failure it causes is stated in
    terms of that row's membership. `known` is the absorbed-name universe the member-list
    test needs; it is required for `closed_by_blank` and is not read otherwise.
    """
    if closed_by_blank:
        if not _is_table_fragment(stripped, known if known is not None else _known_names()):
            return None
        return (
            f"approved table in {APPROVED_DOC.name} has an ORPHANED continuation line "
            f"after row {owner[0]} (`{owner[1]}`), on the far side of a BLANK line: "
            f"{stripped!r} is table-fragment-shaped — it is pipe-bounded, or its every "
            "comma-separated token is a bare name once the cell's own decoration "
            "(separators, a trailing period, emphasis, an inline `*(…)`) is normalised "
            "away, including a name this doc does not have, which is what a break landing "
            "inside a name leaves — and it "
            "appears between the blank that ends the table and the next table, so a row's "
            "cells have REFLOWED across that blank — the row still parses with the members "
            "on this fragment missing, which renders as a false 'no approved row absorbs "
            "it' for each of them"
        )
    if _APPROVED_SEPARATOR.match(stripped):
        if owner is None:  # the header's own separator — real table syntax
            return None
        return (
            f"approved table in {APPROVED_DOC.name} has a SEPARATOR-shaped line after row "
            f"{owner[0]} (`{owner[1]}`): {stripped!r} is a `|---|` line in the middle of the "
            "body, where no table syntax allows one (the header's separator comes before "
            "every row) — so it is a row's continuation that reflowed into a separator, and "
            "the members it carried have silently left the row, which renders as a false "
            "'no approved row absorbs it' for each of them"
        )
    if owner is None:
        owner_text = "the header (before any row)"
    else:
        owner_text = f"row {owner[0]} (`{owner[1]}`)"
    return (
        f"approved table in {APPROVED_DOC.name} has an ORPHANED continuation line "
        f"after {owner_text}: {stripped!r} is neither a `| N | name | … |` row nor a "
        "separator, so a row's cells have REFLOWED onto a second physical line — the "
        "row still parses, but every member on the wrapped remainder silently leaves "
        "it, which renders as a false 'no approved row absorbs it' for each of them"
    )


def _cell_count_error(lineno: int, name: str, cells: list[str]) -> str | None:
    """The `| N | name | Absorbs |` shape test, in BOTH directions — both TRUNCATE.

    Too FEW cells indexes `cells[2]` blind: that raised `IndexError` on a two-cell row,
    which escaped `main()`'s build-failure path entirely — a traceback where the diagnostic
    belonged (#8930 review, P2a).

    Too MANY is the same truncation from the other side: an extra pipe — inside the
    `Absorbs` cell, or inside any other cell, which shifts what `cells[2]` holds (the
    markdown-ESCAPED pipe splits too) — leaves `cells[2]` a SHORTER member list while the
    physical line still ends in `|`, so the row reads as complete and every other guard
    stays green — 23 rows, gapless numbering, closures and split untouched. The members
    past that pipe leave the row, flipping the destination they support from
    `successor`/`row` to `rehomed`/`refuted` with no diagnostic (#8930 review, P2b). A
    markdown cell that needs a pipe has to say it in words; the table has exactly three
    columns.
    """
    if len(cells) == 3:
        return None
    if len(cells) < 3:
        return (
            f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} parses to "
            f"{len(cells)} cell(s); the table shape is `| N | name | Absorbs |`, so "
            "there is no `Absorbs` cell to read — the row is dropped and every method "
            "it absorbs renders as 'no approved row absorbs it'"
        )
    return (
        f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} parses to "
        f"{len(cells)} cell(s); the table shape is `| N | name | Absorbs |`, so a pipe "
        "has been written inside one of the row's cells and SPLIT the row — `cells[2]` "
        "is then a TRUNCATED `Absorbs` list, so every member past that pipe silently "
        "leaves the row, which renders as a false 'no approved row absorbs it' for each "
        "of them"
    )


def _parse_approved(
    text: str,
) -> tuple[dict[str, dict], list[tuple[str, set[str]]], list[str]]:
    """One parse of the approved doc: (approved row name -> row, closures, row errors).

    `row_errors` is a row the parse could not read COMPLETELY — a row that reflowed onto a
    second line (detected by its cell prefix not ending in `|`, by the orphaned
    continuation line that the wrap leaves in the table body, through a blank line, or
    behind a separator-shaped fragment), one with too few cells, or a row that REPEATS
    another row's number or name.
    It is an ERROR, never a silent skip: dropping or replacing the row narrows what the doc
    says the row absorbs, and the verdicts treat that set as the row's approved home
    (#8930 review, P1/P2a, round 3).
    """
    known = _known_names()
    closures = _approved_closures(text)

    menu: dict[str, dict] = {}
    row_errors: list[str] = []
    # Two physical rows claiming ONE identity is unrepresentable in a menu keyed by name,
    # and BOTH ways it happens were measured green: a repeated number under the SAME name
    # made the later assign REPLACE the row, and a repeated number under a DIFFERENT name
    # added a 24th row (the count guard is a floor, so it cannot see either). A repeated
    # NAME reaches the same overwrite from the other side (#8930 review, round 3).
    seen_numbers: dict[int, str] = {}
    seen_names: dict[str, int] = {}
    for line in text.splitlines():
        start = _APPROVED_ROW_START.match(line)
        if not start:
            continue
        name = start.group(2)
        lineno = int(start.group(1))
        repeat = _row_identity_error(name, lineno, seen_numbers, seen_names)
        if repeat:
            row_errors.append(repeat)
            # The later row is DISCARDED, never merged: keeping it is what let a one-member
            # remainder replace a complete row's member list.
            continue
        seen_numbers[lineno], seen_names[name] = name, lineno
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # `cells[2]` is the `Absorbs` cell — see `_cell_count_error` for why the count is
        # bounded in both directions rather than only from below.
        shape = _cell_count_error(lineno, name, cells)
        if shape:
            row_errors.append(shape)
            continue
        # A complete row ends in `|` on this line. If it does not, the cell reflowed and
        # its continuation was never read: the row would parse with a TRUNCATED member
        # list — a false refutation of the owner-approved doc, on a green build.
        if not _APPROVED_ROW.match(line):
            row_errors.append(
                f"approved row {lineno} (`{name}`) in {APPROVED_DOC.name} is not a "
                "complete physical row — it does not end in `|` on the same line, so its "
                "`Absorbs` cell has REFLOWED and the continuation is not read; the members "
                "on the wrapped remainder silently leave the row, which renders as a false "
                "'no approved row absorbs it' for each of them"
            )
            continue
        clauses = _split_clauses(cells[2])
        menu[name] = {
            "lineno": lineno,
            "members": _cell_members(cells[2], name, known) | {name},
            "successors": sorted({t for c in clauses for t in c["targets"]}),
            "prose_names": _prose_names(cells[2]),
            # How many `splits this into` clauses the cell states, and any name they state
            # that did not resolve — so a clause that parses to NO target, or to a PARTIAL
            # one, fails the build instead of reading as "this row has fewer successors"
            # (the same fail-open direction as a dropped closure).
            "split_clauses": len(clauses),
            "split_unresolved": sorted({t for c in clauses for t in c["unresolved"]}),
            "quote": line.rstrip(),
            "closes": set(),
        }

    # BODY INTEGRITY — the completeness test `_APPROVED_ROW` is NOT. A separate pass, because
    # it answers a different question: not "is this line a row" but "is the table the rows
    # sit in intact". From the `| # | Tool | Absorbs |` header until the next blank line,
    # the only legal lines are the header's own separator and row prefixes; anything else is
    # a REFLOWED CONTINUATION — the continuation of the row above it, which has ALREADY
    # parsed with the members on this line missing. Appended before any cell was read from
    # it, so the build fails and no artifact carries the truncated membership (#8930 review,
    # round 2, P1).
    last_row: tuple[int, str] | None = None
    # A body that ended on a BLANK line leaves its row OPEN to a reflow that straddles that
    # blank: the remainder belongs to no body, so no in-body rule can see it. The scan runs
    # past the blank and past ANY prose that lands in between, until the next table — the
    # approved header (which resets it) or a foreign table's header+separator
    # (`_starts_a_table`). Consuming it on the first non-blank line hid a fragment behind a
    # single intervening paragraph (#8930 review, rounds 3-4, P2).
    closed_row: tuple[int, str] | None = None
    in_table = False
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not in_table and closed_row is not None and stripped:
            if _starts_a_table(lines, index):
                closed_row = None  # a table of its own: the gap is over
            elif not _APPROVED_ROW_START.match(stripped):
                # Only a line the ROW PARSE could not place is evidence of the wrap. A row
                # sitting here is one the row loop already read — rows 22-23 land "in the
                # gap" merely because the wrap broke the body above them — so reporting it
                # would blame an intact row for another row's reflow.
                err = _body_error(
                    stripped, closed_row, closed_by_blank=True, known=known
                )
                if err:
                    row_errors.append(err)
        if _APPROVED_HEADER.match(stripped):
            in_table, last_row, closed_row = True, None, None
            continue
        if not in_table:
            continue
        if not stripped:  # a blank line ends the table body (the doc has TWO such tables)
            in_table = False
            last_row, closed_row = None, last_row
            continue
        start = _APPROVED_ROW_START.match(stripped)
        if start:
            last_row = (int(start.group(1)), start.group(2))
            continue
        err = _body_error(stripped, last_row, closed_by_blank=False)
        if err:
            row_errors.append(err)

    # Apply the closure the doc states, one hop at a time until it settles: a member
    # that `sit inside`s another member is a member too.
    by_outer: dict[str, set[str]] = {}
    for outer, inners in closures:
        by_outer.setdefault(outer, set()).update(inners)
    for name, r in menu.items():
        members = set(r["members"])
        changed = True
        while changed:
            changed = False
            for outer in list(members):
                for inner in by_outer.get(outer, ()):
                    if inner in known and inner not in members:
                        members.add(inner)
                        changed = True
        r["members"] = members
        r["closes"] = by_outer.get(name, set())
    return menu, closures, row_errors


def _approved_menu() -> dict[str, dict]:
    """approved row name -> {members, successors, quote, closes, lineno}.

    `members` is the row's member LIST (the cell's comma list, expanded for a `prefix_*`
    family) with the row's OWN name added unconditionally — the cell may or may not list it,
    most do not — plus every name the doc's own `sit inside` sentence puts inside one of
    those members; `successors` are the TARGET names the cell names as beta's successors of
    the row, parsed from its `splits this into` clause ONLY (`revise_knowledge`'s cell
    states its own split into `update_knowledge` + `supersede_knowledge`) — not from any
    target token in the cell, because the cell's prose states other relations too; `quote`
    is the WHOLE table row, by construction, so it cannot be a truncation of what the doc
    says — and that is enforced, not assumed: a row that lost cells to a reflow is a
    `row_errors` entry, so the build fails and no artifact quotes it (the body test and
    `_row_identity_error` are what make the sentence true).
    """
    return _parse_approved(APPROVED_DOC.read_text(encoding="utf-8"))[0]


def _approved_state() -> tuple[dict[str, dict], list[tuple[str, set[str]]], list[str]]:
    """The menu, the closure statements AND any unreadable row — from ONE read."""
    return _parse_approved(APPROVED_DOC.read_text(encoding="utf-8"))


def _approved_header_count(text: str) -> int:
    """How many approved table BODIES the doc states (`| # | Tool | Absorbs |` headers).

    Counted from the text rather than threaded through the parse so the body-integrity
    guard cannot be disarmed by a caller that drops it: `APPROVED_MIN_BODIES` is checked
    against the document itself, the way `_known_names()` is read here rather than
    supplied.
    """
    return sum(1 for line in text.splitlines() if _APPROVED_HEADER.match(line.strip()))


def _approved_errors(
    menu: dict[str, dict],
    closures: list[tuple[str, set[str]]],
    row_errors: Sequence[str] = (),
) -> list[str]:
    """Row-level parse errors FIRST, then the structural guards.

    A row the parse could not read completely is the ROOT of the structural failure it
    causes (the row count drops below the doc's own title), so it is reported ahead of the
    count's diagnostic: `only 22 approved rows parsed` does not say WHICH row, or why.
    """
    return [*row_errors, *_approved_structure_errors(menu, closures)]


def _approved_structure_errors(
    menu: dict[str, dict], closures: list[tuple[str, set[str]]]
) -> list[str]:
    """Structural failures only — a DISAGREEMENT is a finding, not an error."""
    if not menu:
        return [
            f"NO owner-approved menu parsed out of {APPROVED_DOC.name} — the approved-list "
            "cross-check is UNARMED, so every disputed row would read as 'not stated'"
        ]
    if len(menu) < APPROVED_MIN_ROWS:
        return [
            f"only {len(menu)} approved rows parsed out of {APPROVED_DOC.name} (expected at "
            f"least {APPROVED_MIN_ROWS} — the doc's own title claims 23); the table shape has "
            "changed and the parse is now silently dropping rows"
        ]
    # Every row the doc states is numbered, so a NaN in the sequence is a row the parse
    # dropped — which the count alone cannot see when a reflow BLENDS with a doc edit.
    linenos = sorted(r["lineno"] for r in menu.values())
    missing = sorted(set(range(1, linenos[-1] + 1)) - set(linenos))
    if missing:
        return [
            f"approved row number(s) {', '.join(str(n) for n in missing)} are missing from the "
            f"parse of {APPROVED_DOC.name} (parsed {len(menu)} rows, numbered "
            f"{linenos[0]}-{linenos[-1]}); a row the doc states is being dropped, so every "
            "method it absorbs would render as 'no approved row absorbs it'"
        ]
    # The body-integrity pass is ARMED by the header, so the header must be found: a
    # reworded or re-columned header leaves every row parsing while the pass that catches
    # a wrap ending in `|` never runs. Placed after the row-count/numbering guards (a doc
    # that lost ROWS is reported as that, not as a header break) and before the content
    # guards, because a body that was never scanned makes each of them a narrower reading
    # of the doc than they claim.
    bodies = _approved_header_count(APPROVED_DOC.read_text(encoding="utf-8"))
    if bodies < APPROVED_MIN_BODIES:
        return [
            f"only {bodies} `| # | Tool | Absorbs |` header(s) matched in "
            f"{APPROVED_DOC.name} (expected {APPROVED_MIN_BODIES} — the doc states two "
            "approved tables); the row-reflow guard is decided by the table BODY, so with "
            "no body entered it is UNARMED — a row whose cells wrapped with the first "
            "line still ending in `|` parses with the members on the remainder silently "
            "missing, which renders as a false 'no approved row absorbs it' for each of "
            "them"
        ]
    # A CURRENT tool name in a cell's PROSE is a member list the em dash was misplaced
    # into — which silently strips that member from its row.
    prose = sorted(
        (n, names) for n, r in menu.items() if (names := r["prose_names"])
    )
    if prose:
        detail = "; ".join(
            f"`{n}` ← {', '.join(f'`{t}`' for t in names)}" for n, names in prose
        )
        return [
            f"approved row(s) in {APPROVED_DOC.name} name a CURRENT tool in the prose AFTER "
            f"the em dash: {detail}; prose is not a member list, so that name loses its "
            "approved home and renders as 'no approved row absorbs it'"
        ]
    # A closure statement that does not RESOLVE is a parse break, not a doc that states an
    # empty closure. Resolution — not presence — is the test, and it is ALL-or-nothing:
    # the applied membership is a `known` filter, so one unresolved name narrows the row
    # silently, and a statement whose members are all unresolved narrows it to nothing.
    # (`these sit inside `index_files`` is the second case; `zzz` beside a real name the
    # first.)
    known = _known_names()
    unresolved: dict[str, list[str]] = {}
    for outer, inners in closures:
        lost = sorted(i for i in inners if i not in known)
        if not inners:
            lost = ["(no backticked member name)"]
        if lost:
            unresolved[outer] = lost
    if unresolved:
        detail = "; ".join(
            f"`{o}` ← {', '.join(f'`{i}`' for i in lost)}"
            for o, lost in sorted(unresolved.items())
        )
        return [
            f"transitive-closure statement(s) in {APPROVED_DOC.name} name member(s) the doc "
            f"never uses: {detail}; those members are silently missing from every row that "
            "absorbs the container"
        ]
    if len(closures) < APPROVED_MIN_CLOSURES:
        return [
            f"only {len(closures)} transitive-closure statement(s) parsed out of "
            f"{APPROVED_DOC.name} (expected at least {APPROVED_MIN_CLOSURES} — the doc states "
            "that `index_sessions` and `ingest_corpus` sit inside `index_files`, alongside the "
            "`get`/`overview`/`query` closures); a reflow the parse cannot read shrinks every "
            "row's membership silently, and the next disputed row naming those members renders "
            "'no approved row absorbs it' — a row the doc absorbs it in"
        ]
    # The CONTAINER must be a name the doc actually uses (a row, or a member of one):
    # renaming only the closure sentence's container to a name the doc never mentions
    # otherwise parses cleanly and drops the closure. This is a known-ness check, not a
    # correctness one — a container renamed to ANOTHER name the doc uses relocates the
    # closure silently, and nothing in the doc's shape can tell that from a legitimate
    # re-statement.
    named = set(menu) | {m for r in menu.values() for m in r["members"]}
    stray = sorted(outer for outer, _ in closures if outer not in named)
    if stray:
        return [
            f"transitive-closure container(s) {', '.join(f'`{o}`' for o in stray)} parsed out "
            f"of {APPROVED_DOC.name} are neither a row the doc names nor a member of one; "
            "renaming the container in the sentence alone silently drops the whole closure"
        ]
    # ...and the same for the beta-split clause: a clause that parses to no TARGET name is
    # a parse break, and a PARTIAL loss is the same failure one name at a time — the row's
    # successors narrow with no error, which flips `successor` (support) to `rehomed` (a
    # disagreement) in the owner-facing table. A doc that states no split at all means the
    # clause was reworded, which is why the count is bounded too.
    unresolved_split = sorted(
        (n, names) for n, r in menu.items() if (names := r["split_unresolved"])
    )
    if unresolved_split:
        detail = "; ".join(
            f"`{n}` ← {', '.join(f'`{t}`' for t in names)}" for n, names in unresolved_split
        )
        return [
            f"approved row(s) in {APPROVED_DOC.name} state a beta split naming something the "
            f"target surface does not have: {detail}; a dropped successor reads as a "
            "DISAGREEMENT (`rehomed`) in the rendered table"
        ]
    empty_split = sorted(
        n for n, r in menu.items() if r["split_clauses"] and not r["successors"]
    )
    if empty_split:
        return [
            f"approved row(s) {', '.join(f'`{n}`' for n in empty_split)} state a beta split "
            f"in {APPROVED_DOC.name} but NO target name parsed out of it; the clause shape "
            "has changed, and a row whose successors silently empty flips from `successor` "
            "(support) to `rehomed` (a disagreement) in the rendered table"
        ]
    if sum(1 for r in menu.values() if r["split_clauses"]) < APPROVED_MIN_SPLITS:
        return [
            f"only {sum(1 for r in menu.values() if r['split_clauses'])} beta-split "
            f"statement(s) parsed out of {APPROVED_DOC.name} (expected at least "
            f"{APPROVED_MIN_SPLITS} — row 17 states `splits this into `update_knowledge` + "
            "`supersede_knowledge``); a reworded clause drops those successors silently"
        ]
    return []


def _approved_verdict(menu: dict[str, dict], aliases: set[str], target: str) -> str:
    """How the approved menu reads `target` for a method — five mechanical verdicts.

    `row`       the target is an approved row that absorbs this method — the strongest
                reading available: the approved doc ITSELF places the method there.
    `successor` the target is a TARGET the absorbing row's cell names as beta's split
                of that row, so the row does support it (one of several successors).
    `refuted`   an approved row (or a successor of one) covers this target and does NOT
                absorb the method: the approved doc does not place the method there. It
                does NOT claim the doc places it SOMEWHERE ELSE — a method the doc never
                absorbs at all reads `refuted` too, and the evidence line beside it says
                so ("no approved row absorbs …").
    `rehomed`   the approved doc does absorb this method, but into a DIFFERENT row than
                the map's destination (which the doc never names) — so the destination is
                not the approved answer even though the method has one. Reached through a
                different door than `refuted` and counted with it: the two docs disagree.
    `unstated`  the approved doc names neither this target nor any row that absorbs the
                method, so it neither supports nor refutes it.

    `rehomed` is not folded into `unstated`: `unstated` is silence, and a row whose
    Approved-home cell names the method is not silent — reporting it as "not named by the
    approved doc" told the owner the opposite of what the quoted row below it said.
    `refuted` is not folded into `rehomed` for the mirror reason: `rehomed` asserts the
    method HAS an approved home, which is false when no row absorbs it.
    """
    row = menu.get(target)
    if row is not None:
        return "row" if aliases & row["members"] else "refuted"
    hosts = [r for r in menu.values() if target in r["successors"]]
    if hosts:
        return "successor" if any(aliases & h["members"] for h in hosts) else "refuted"
    if any(aliases & r["members"] for r in menu.values()):
        return "rehomed"
    return "unstated"


def _approved_readings(
    menu: dict[str, dict], findings: tuple[list[dict], list[dict], list[dict]]
) -> list[dict]:
    """One reading per DISPUTED row, from the approved menu.

    Disputed = every row a downstream section already flags (D2 disagreeing with its
    citation, D2b supported only beyond the first clause, D3 under a multi-target
    citation). The reading covers the map's own destination AND every rival target the
    citation names, because the rival is what the ambiguity is about.
    """
    methods = {r["name"]: r["sdk_method"] for r in _registry_rows()}
    disputed: dict[str, dict] = {}
    for group in findings:
        for f in group:
            disputed.setdefault(f["tool"], f)

    out: list[dict] = []
    for tool, f in sorted(disputed.items()):
        method = methods.get(tool, "")
        aliases = {a for a in (method, tool.removeprefix("tortoise_")) if a}
        homes = sorted(n for n, r in menu.items() if aliases & r["members"])
        home = homes[0] if len(homes) == 1 else None
        dest = DESTINATION[tool]
        rivals = [t for t in f["named"] if t != dest]
        out.append(
            {
                "tool": tool,
                "method": method,
                "dest": dest,
                "homes": homes,
                "home": home,
                "row_no": menu[home]["lineno"] if home else 0,
                "quote": menu[home]["quote"] if home else "",
                # A method absorbed by MORE THAN ONE approved row still has every home's
                # evidence: `home`/`row_no`/`quote` are the single-home convenience, and
                # the renderer reads `homes_detail` so the multi-home case never prints
                # "no approved row absorbs" beside a table cell naming several.
                "homes_detail": [
                    {
                        "name": h,
                        "row_no": menu[h]["lineno"],
                        "quote": menu[h]["quote"],
                        "successors": menu[h]["successors"],
                        # The alias the MATCH was made on: the doc names `invalidate`, not
                        # the SDK method `invalidate_point`, and the evidence line quotes
                        # the row — so it must name the token that row actually contains.
                        "matched": sorted(aliases & menu[h]["members"]),
                    }
                    for h in homes
                ],
                "successors": sorted({s for h in homes for s in menu[h]["successors"]}),
                "dest_verdict": _approved_verdict(menu, aliases, dest),
                "rivals": [(t, _approved_verdict(menu, aliases, t)) for t in rivals],
            }
        )
    return out


def _approved_evidence_lines(approved: list[dict]) -> list[str]:
    """The `-> quoted approved row(s)` list under §D4's table.

    Branched on `homes`, NEVER on whether a single quote exists: a method absorbed by
    MORE THAN ONE approved row has `home is None`/`quote == ''` by construction, and an
    empty-quote branch printed "no approved row absorbs `x`" a few lines under a table
    cell that named several rows absorbing it — two cells of one generated section
    asserting opposite things, in the artifact the owner reads for the evidence (#8930).

    The absorbed name is the one the MATCH was made on (`invalidate`), not necessarily the
    SDK method (`invalidate_point`): the quoted row beneath it has to contain the token the
    line claims it absorbs, or the owner cannot check the claim against the doc.
    """
    out: list[str] = []
    for a in approved:
        if len(a["homes"]) > 1:
            out.append(
                f"- **`{a['tool']}`** — `{a['method'] or a['tool']}` is absorbed by SEVERAL "
                "approved rows: "
                + ", ".join(f"`{h['name']}` (row {h['row_no']})" for h in a["homes_detail"])
                + ":"
            )
            out += [f"  > {h['quote']}" for h in a["homes_detail"]]
            continue
        if not a["homes"]:
            out.append(
                f"- **`{a['tool']}`** — no approved row absorbs "
                f"`{a['method'] or a['tool']}`."
            )
            continue
        matched = a["homes_detail"][0]["matched"]
        named = ", ".join(f"`{m}`" for m in matched) if matched else f"`{a['method']}`"
        sdk = f" (SDK `{a['method']}`)" if a["method"] and a["method"] not in matched else ""
        out.append(f"- **`{a['tool']}`** — {named}{sdk} is absorbed by `{a['home']}`:")
        out.append(f"  > {a['quote']}")
    return out


def _registry_rows() -> list[dict]:
    """Every ToolDefinition in the registry, with its SOURCE line number.

    The line number is read from the AST — it is a fact about the file, not a
    claim typed by a human.
    """
    tree = ast.parse(REGISTRY_SRC.read_text(encoding="utf-8"))
    by_name: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "ToolDefinition"):
            continue
        name = None
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                name = kw.value.value
        if name is None and node.args and isinstance(node.args[0], ast.Constant):
            name = node.args[0].value
        if name:
            by_name[name] = node.lineno

    rows = []
    # The SERVED set (#3883): a retired name still answers through the warning
    # shim, so it is still a name an agent can call — and the bridge table's job is
    # to say where every caller-visible name leads. Retiring a name re-points its
    # entry here; it does not remove the row, or the map would stop accounting for
    # names that still emit calls.
    for entry in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY):
        declared = getattr(entry, "sdk_method", "") or ""
        rows.append({
            "name": entry.name,
            "lineno": by_name.get(entry.name),
            "sdk_method": declared,
            # The only resolution test that matters: is there a real method?
            "resolves": bool(declared) and hasattr(TortoiseSDK, declared),
            "read_only": bool(getattr(entry.annotations, "readOnlyHint", False)),
            # The tool the retirement warning actually sends the caller to.
            "use_instead": (
                m.group(1)
                if (u := getattr(entry, "retired_use_instead", None))
                and (m := _REPLACEMENT_RE.match(u))
                else ""
            ),
        })
    return rows


def _sdk_targets() -> dict[str, int]:
    """Public method -> its `def` line in sdk.py, **on TortoiseSDK only**.

    A plain "is there a `def` anywhere in sdk.py" scan is not the question. It
    would count nested helpers and helper-class methods as if they were public
    SDK surface, so a target that happened to exist only as an internal helper
    would be reported as implemented. Walk the class body instead.
    """
    tree = ast.parse(SDK_SRC.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for node in tree.body:
        if not (isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK"):
            continue
        for sub in node.body:
            if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out[sub.name] = sub.lineno
    return out


# ─────────────────────────────────────────────────────────────────────
# THE CITATIONS. The destination map is authored from the beta doc's disposition
# tables, and until now nothing bound the two together: the doc is the authority
# the owner reviews, the map is what renders. A citation is only evidence if the
# doc still says it AND the read is MAXIMAL. A read that stops at the clause that
# agrees with the row drops the clause that contradicts it, and because a
# truncated prefix of a real sentence is still a real substring, a bare
# `quote in text` test cannot see the difference.
#
# The quotes below are DERIVED from the doc — the whole disposition row, read at
# build time — never typed beside the map. The rule shape (`_maximal`, and its
# boundary set) is `tools/sdk_rename_table.py`'s, deliberately not a second rule.
# ─────────────────────────────────────────────────────────────────────
BETA_DOC = ROOT / "docs" / "product" / "beta-sdk-surface.md"

# A disposition cell can name something that is not a target because it is itself
# folded one hop further. The hop is stated IN THE DOC, so it is recorded as a
# citation of its own rather than assumed: without it `get_source_reliability`
# reads as unsupported (its own cell says `list_sources`), which is not a finding.
# name -> (doc, quote, the target it folds into).
FOLDS: dict[str, tuple[str, str, str]] = {
    "list_sources": (
        BETA_DOC.name,
        "| `list_sources` | **Not discarded.** Present at `tortoise/sdk.py` with an MCP "
        "tool and a CLI command (`tortoise/__main__.py`), and it is covered by "
        "`tests/test_enumeration_surfaces.py` and `tests/test_connector_sources.py`. "
        "It folds into **row 4 `list_knowledge(kind='source')`** — the *question* it "
        "asks stays first-class and gains the credibility tier; it no longer needs its "
        "own method. |",
        "list_knowledge",
    ),
}

# A quote that stops mid-clause is exactly what this rule rejects. Boundaries: a
# cell/row `|`, a line end, the document end, or a sentence end.
_REGION_END = re.compile(r"[.!?][\"')\]\u201d`*_]*$")
_ARROW_TARGET = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^`]*\))?`")
_COUNT_CELL = re.compile(r"~?\d+$")


def _maximal(quote: str, text: str) -> bool:
    """Is `quote` a maximal region of `text` — not a right-truncation of one?

    True when the match ends at a table-cell/row boundary (`|`), at a line end, at the
    end of the document, or at a sentence boundary. A quote that stops mid-clause is
    rejected, because the cut is exactly where a contradiction can hide.

    Sentence boundaries count on purpose: the rule exists to stop a quote MID-clause,
    not to force every quote to span a whole table row.
    """
    idx = text.find(quote)
    if idx < 0:
        return True  # absent text is CITATION DRIFT, reported by its own check
    after = text[idx + len(quote):]
    if after == "" or after.startswith("\n"):
        return True
    if quote.endswith("|"):
        return True
    return _REGION_END.search(quote) is not None


# A target named after the arrow is a DESTINATION only when it is INTRODUCED as one —
# directly after the arrow, after a clause separator, or after a destination
# preposition. Scanning the whole post-arrow segment counted anything in prose: a
# comparison ("the **batch form of `mine_knowledge_from_session`**") and an aside about a
# DROPPED sibling ("**`recall_subgraph` is dropped, not folded** — `explore_connections`
# answers that question") both became "alternative destinations" and made D3 over-report
# rows that name exactly one. Restricting to the destination clause is the fix.
_DEST_LEAD = re.compile(r"(?:→|[;,]+\s*|\b(?:on|via|to|into|toward|towards)\s+)\**\s*$")


def _dest_targets(text: str) -> list[str]:
    """TARGETS in `text` that are introduced as destinations, in document order.

    A name counts only when it sits at the start of a destination clause (directly after the
    `→`, or after a `;`/`,`/destination preposition). Only target names are kept, plus the
    names `FOLDS` maps into a target — a disposition cell also names fields (`invalid_at`),
    parameter values (`credibility`) and the methods being folded, and counting those as
    destinations would make this guard PERMISSIVE, the one direction a guard must never fail.
    """
    keep = set(TARGET_MCP) | set(FOLDS)
    out: list[str] = []
    for m in _ARROW_TARGET.finditer(text):
        name = m.group(1)
        if name not in keep:
            continue
        before = text[: m.start()]
        # `before.strip()` empty = the name is at the start of the destination clause
        # (the `→` itself was consumed by the caller's split).
        if before.strip() == "" or _DEST_LEAD.search(before):
            out.append(name)
    return out


def _arrow_targets(cell: str) -> list[str]:
    """Every TARGET introduced as a destination after a `→` in a disposition cell.

    Each segment after an arrow is scanned separately, so the FIRST name in each is
    anchored by the arrow itself; later names must be anchored by `;`/`,`/a preposition.
    """
    out: list[str] = []
    for seg in cell.split("→")[1:]:
        out += _dest_targets(seg)
    return out


def _prefix_targets(cell: str) -> list[str]:
    r"""Targets in the FIRST CLAUSE after the first `→` — the prefix a truncated read keeps.

    The cut is the clause boundary a right-truncation actually lands on: the first `;` or
    `.` after the arrow. That is the shape the defect takes — a quote that keeps
    "→ `manage_source_trust`" and drops the continuation "; reads via `list_sources`" — and
    reading only that prefix is how a row's support can vanish from its own evidence while
    the substring test stays green.
    """
    segs = cell.split("→")[1:]
    if not segs:
        return []
    seg = re.split(r"[;.]", segs[0], maxsplit=1)[0]
    return _dest_targets(seg)


def _doc_citations() -> dict[str, dict]:
    """tool -> the doc row that dispositions it, read out of the doc itself.

    Beta's disposition tables are `| names… | count | prose |`. A row is the citation
    for every registry tool whose `sdk_method` it names, and the QUOTE IS THE ROW — so
    it cannot drift from the doc without the doc changing.
    """
    text = BETA_DOC.read_text(encoding="utf-8")
    # sdk_method -> EVERY tool that declares it. Two registry tools can declare the same
    # method (`tortoise_get_point` and `tortoise_get_operator` both declare `get_point`),
    # and a dict keyed by method silently kept only the LAST one — a disposition row
    # naming `get_point` would then cite one tool and drop the other, invisibly.
    by_method: dict[str, list[str]] = {}
    for r in _registry_rows():
        if r["sdk_method"]:
            by_method.setdefault(r["sdk_method"], []).append(r["name"])

    cites: dict[str, dict] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # Only the disposition tables: a numeric count cell, names, and an arrow.
        if len(cells) < 3 or not _COUNT_CELL.match(cells[1]):
            continue
        names = re.findall(r"`([a-z_][a-z0-9_]*)`", cells[0])
        if not names or "→" not in cells[2]:
            continue
        # The citation is the WHOLE region — the entire disposition row — taken by
        # construction. It is not a prefix or a slice, so a truncated citation cannot be
        # built: the row IS the quote. `_maximal` (below) is a secondary tripwire for the
        # authored FOLD hop, not the mechanism that keeps these quotes whole.
        quote = _citation_region(line)
        for n in names:
            for tool in by_method.get(n, ()):
                cites[tool] = {
                    "doc": BETA_DOC.name,
                    "quote": quote,
                    "targets": _arrow_targets(cells[2]),
                    # The prefix a truncated read keeps. A row supported only beyond
                    # this point is supported ONLY by the clause maximality preserves.
                    "first_targets": _prefix_targets(cells[2]),
                }
    return cites


def _citation_region(line: str) -> str:
    """The citation region: the WHOLE table row, taken by construction.

    Deliberately not a prefix or a slice. A quote that *is* the row cannot be a truncation,
    so a doc-derived citation cannot be edited to agree with its row. The maximality check
    is therefore a secondary tripwire for the hand-typed FOLD quote, not the guarantee.
    """
    row = line.rstrip()
    if not row.startswith("|") or not row.endswith("|"):
        raise ValueError(f"citation region is not a whole table row: {row[:60]!r}")
    return row


def _citation_errors(cites: dict[str, dict]) -> list[str]:
    """Fail the build on DRIFT or on a TRUNCATED citation — never on a disagreement.

    A disagreement between the map and its full citation is a FINDING that needs an
    owner ruling, so it is rendered, not raised. A citation that is missing or
    truncated is a defect in the EVIDENCE itself, and evidence that can be edited to
    agree with the row is worse than no evidence at all.
    """
    errs: list[str] = []
    # Non-vacuity: a guard that resolves nothing cannot fail, and would read as green.
    if not cites:
        errs.append("NO destination citation resolved — the citation guard is UNARMED")
    for tool, c in sorted(cites.items()):
        text = (ROOT / "docs" / "product" / c["doc"]).read_text(encoding="utf-8")
        if c["quote"] not in text:
            errs.append(f"CITATION DRIFT: {tool}'s quote is not in {c['doc']}")
        elif not _maximal(c["quote"], text):
            errs.append(
                f"TRUNCATED CITATION for {tool}: the quote stops mid-clause in "
                f"{c['doc']} — it must reach a cell `|`, a line end or a sentence end, "
                "because the clause it drops is where a contradiction hides"
            )
    for name, (doc, quote, _target) in sorted(FOLDS.items()):
        text = (ROOT / "docs" / "product" / doc).read_text(encoding="utf-8")
        if quote not in text:
            errs.append(f"FOLD CITATION DRIFT: {name}'s quote is not in {doc}")
        elif not _maximal(quote, text):
            errs.append(f"TRUNCATED FOLD CITATION: {name} in {doc} stops mid-clause")
    return errs


def _citation_findings(cites: dict[str, dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """(unsupported, clause_only, ambiguous) — all REPORT-ONLY, all rendered.

    `unsupported`: the map's destination is not named anywhere in the row's full
    citation — the row and its own evidence disagree, one level up from the truncated
    quote that hides the same thing. The mapping is owner-approved, so it is reported
    with its evidence and NOT silently changed.

    `clause_only`: the destination IS named, but only in a clause BEYOND the first `→`.
    A read that stopped at the first clause would drop the row's entire support, so this
    list is the first-clause split's load-bearing set — computed, not asserted.

    `ambiguous`: the citation names more than one target, so it does not by itself
    determine a destination. Which clause applies to which method is a reading, not a
    computation — the maximal quote is rendered so the clause is visible and must be
    read, which is the most a mechanical rule can do.
    """
    folds = {k: v[2] for k, v in FOLDS.items()}

    def resolve(names: list[str]) -> list[str]:
        out: list[str] = []
        for t in names:
            r = folds.get(t, t)
            if r not in out:
                out.append(r)
        return out

    unsupported: list[dict] = []
    clause_only: list[dict] = []
    ambiguous: list[dict] = []
    for tool, c in sorted(cites.items()):
        dest = DESTINATION.get(tool)
        if dest is None:
            continue
        named = resolve(c["targets"])
        prefix = resolve(c["first_targets"])
        row = {"tool": tool, "dest": dest, "named": named, "prefix": prefix, **c}
        if dest not in named:
            unsupported.append(row)
        elif dest not in prefix:
            clause_only.append(row)
        if len(named) > 1:
            ambiguous.append(row)
    return unsupported, clause_only, ambiguous


def _validate(rows: list[dict]) -> list[str]:
    """Fail loudly. Never reconcile silently — a mismatch IS the finding."""
    errs: list[str] = []
    reg = {r["name"] for r in rows}
    mapped = set(DESTINATION)

    for missing in sorted(reg - mapped):
        errs.append(f"UNMAPPED registry tool (no destination recorded): {missing}")
    for extra in sorted(mapped - reg):
        errs.append(f"STALE map key (not in the registry): {extra}")

    for name, dest in sorted(DESTINATION.items()):
        if dest in VALID_DEST:
            continue
        ns = next((n for n in NAMESPACES if dest.startswith(n)), None)
        if ns is None:
            errs.append(f"UNRECOGNISED destination for {name}: {dest!r}")
            continue
        # A namespaced destination is a claim about a METHOD, so the suffix must at
        # least be an identifier. Whether the method exists yet is a FINDING (C1),
        # not an integrity error -- this generator fails on the map disagreeing with
        # the registry, never on the target not having been built yet.
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", dest[len(ns):]):
            errs.append(f"NAMESPACED destination is not a method name for {name}: {dest!r}")

    # The reconciliation PROOF (#4282): a retirement must carry an explicit reason, and
    # the reason set must be EXACTLY the retired set — so a new `REMOVED` cannot appear
    # without a recorded reason, and a resolved one cannot leave a stale reason behind.
    retired = {n for n, d in DESTINATION.items() if d == "REMOVED"}
    for name in sorted(retired - set(REMOVALS)):
        errs.append(
            f"UNREASONED retirement (no REMOVALS entry): {name} — a retirement is a "
            "surface decision, so it must name the doc that authorises it"
        )
    for name in sorted(set(REMOVALS) - retired):
        errs.append(
            f"STALE retirement reason (no longer mapped to REMOVED): {name} — "
            "remove its REMOVALS entry, or restore its destination"
        )

    # A disclosure that has rotted is worse than none: it would name a row that no longer
    # exists, or a "documented reading" that is not a destination at all.
    for tool, (reading, _authority) in sorted(CONTESTED_DESTINATION.items()):
        if tool not in DESTINATION:
            errs.append(f"STALE contested-destination key (not in the map): {tool}")
        if reading not in VALID_DEST and not any(reading.startswith(n) for n in NAMESPACES):
            errs.append(
                f"UNRECOGNISED contested reading for {tool}: {reading!r} is not a target"
            )
    return errs


def _blockers(rows: list[dict], sdk_defs: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """The findings this artifact exists to produce."""
    tenancy_targets = sorted(
        {d.split(":", 1)[1] for d in DESTINATION.values() if d.startswith("tenancy:")}
    )
    sdk_only_targets = sorted(
        {d.split(":", 1)[1] for d in DESTINATION.values() if d.startswith("sdk:")}
    )
    # Named by the method that must EXIST, which for an MCP target is its backing
    # method (MCP_BACKING) -- `graph_set_recording`'s tool survives while its
    # same-named SDK method is discarded, so the method Phase 2 must write is
    # `update_memory_graph`.
    no_method = [
        {"what": MCP_BACKING.get(m, m), "scope": "MCP", "why": C1_FINDING}
        for m in TARGET_MCP if MCP_BACKING.get(m, m) not in sdk_defs
    ] + [
        {"what": m, "scope": "tenancy", "why": C1_FINDING}
        for m in tenancy_targets if m not in sdk_defs
    ] + [
        {"what": m, "scope": "sdk-only", "why": C1_FINDING}
        for m in sdk_only_targets if m not in sdk_defs
    ]

    unresolved = [
        {"tool": r["name"], "declared": r["sdk_method"], "line": r["lineno"]}
        for r in rows
        if r["sdk_method"] and not r["resolves"]
    ]

    return no_method, unresolved


def _closure_sentences(closures: list[tuple[str, set[str]]]) -> list[str]:
    """The `X/Y `sit inside` Z` lines of §D4's membership paragraph — from the PARSE.

    Hand-typed here, this list was a SECOND answer that could drift from the closure the
    membership actually uses: removing `list_topics` from the doc's `overview` statement
    left the build green and the artifact still asserting that `list_topics` `sits inside`
    `overview` (#8930 review, P2b). Emitting it from `closures` makes disagreement
    impossible — the sentence and the applied membership are one value.

    Members are sorted: the parse yields a set, and the sentence is prose, so a stable
    order is all it needs.
    """
    return [
        f"{'/'.join(f'`{m}`' for m in sorted(inners))} `sit inside` `{outer}`"
        for outer, inners in closures
    ]


def render(
    rows: list[dict],
    sdk_defs: dict[str, int],
    cites: dict[str, dict],
    menu: dict[str, dict],
    closures: list[tuple[str, set[str]]],
) -> str:
    by_dest: dict[str, list[str]] = {}
    for r in rows:
        by_dest.setdefault(DESTINATION[r["name"]], []).append(r["name"])

    no_method, other = _blockers(rows, sdk_defs)
    counts = {d: len(v) for d, v in by_dest.items()}
    total = sum(counts.values())
    removed = counts.get("REMOVED", 0)
    tenancy = sum(v for k, v in counts.items() if k.startswith("tenancy:"))
    # Four disjoint buckets, computed -- never a subtraction from a moving number.
    sdk_only = sum(v for k, v in counts.items() if k.startswith("sdk:"))
    on_mcp = total - removed - tenancy - sdk_only
    # ONE source for the retirement count: it appears in the intro prose, the
    # Part B heading, the prose under the table and the name list. A hardcoded
    # number in the prose would silently contradict the derived one below and
    # `--check` could not see it, because a regenerated contradiction is
    # identical on both sides of the comparison.
    retired_names = sorted(r["name"] for r in rows if r.get("use_instead"))
    n_retired = len(retired_names)
    n_live = len(rows) - n_retired

    out = [
        "# Phase 0.1 — the bridge table",
        "",
        "**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.",
        "",
        "Every `file:line` in this document is **read from the source at build time**, so it cannot",
        "drift from the code it cites. The destination map is data in the generator; every count",
        "below is arithmetic computed against the **served registry** — the live tools plus the",
        f"{n_retired} retired names, which still answer through the #3883 warning shim. The generator **fails the build**",
        "if the map and the registry disagree — a mismatch is a finding, not something to reconcile.",
        "",
        f"**Registry: {total} tools → {on_mcp} absorbed into the {len(TARGET_MCP)} MCP targets · "
        f"{sdk_only} absorbed into a builder-only SDK method (not on the MCP) · "
        f"{tenancy} tenancy (SDK/REST only) · {removed} retired.**",
        "",
        f"**These are not the same number.** The MCP has **{len(TARGET_MCP)}** tools; "
        f"**{removed}** current tools retire, **{tenancy}** are tenancy-only, **{sdk_only}** is absorbed into a "
        f"builder-only SDK method that is not on the MCP, and **{on_mcp}** are absorbed into those "
        f"{len(TARGET_MCP)} — many-to-one. "
        f"Writing \"{total} minus {len(TARGET_MCP)} equals {total - len(TARGET_MCP)} retired\" conflates the two, and is wrong.",
        "",
        "---",
        "",
        "## Part A — the merged tools",
        "",
        "A *merged* target absorbs more than one current tool, so it dispatches internally on a",
        "discriminator. **Which tools are merged is computed from the map** — not listed by hand — and",
        "**whether the method exists is read from `sdk_defs`**. A discriminator value with no method",
        "behind it is invisible until someone writes the handler and finds nothing to call, which is",
        "what this part exists to catch.",
        "",
        "Only the `Discriminator` column is authored data: it is a design fact about the target, not",
        "something derivable from today's code.",
        "",
        "| Target tool | Sources absorbed | Discriminator | Exists |",
        "|---|---|---|---|",
    ]
    merged = {d: v for d, v in by_dest.items() if len(v) > 1 and d != "REMOVED"}
    for dest in sorted(merged, key=lambda d: (-len(merged[d]), d)):
        disc = DISCRIMINATORS.get(dest, "*(none — dispatch is by argument)*")
        # Check the method, and for a namespaced target the method it names.
        method = dest.split(":", 1)[1] if dest.startswith(NAMESPACES) else dest
        ok = "yes" if method in sdk_defs else C1_REF
        out.append(f"| `{dest}` | {len(merged[dest])} | {disc} | {ok} |")
    n_merged = len(merged)
    n_ok = sum(
        1 for d in merged
        if (d.split(":", 1)[1] if d.startswith(NAMESPACES) else d) in sdk_defs
    )
    out += [
        "",
        f"**{n_merged} merged targets. {n_ok} of them have a method behind them today.** The other"
        f" **{n_merged - n_ok}** are Phase 2 work, not renames.",
        "",
        f"## Part B — every served name and its single destination ({n_live} live + {n_retired} retired)",
        "",
        "| # | Current tool | Source | SDK binding | Read-only | Destination |",
        "|---|---|---|---|---|---|",
    ]

    for i, r in enumerate(sorted(rows, key=lambda x: x["name"]), 1):
        src = f"`tool_registry.py:{r['lineno']}`" if r["lineno"] else "—"
        bind = f"`{r['sdk_method']}`" if r["sdk_method"] else "**none declared**"
        if r["sdk_method"] and not r["resolves"]:
            bind += " ⚠️ **does not resolve**"
        # A `⚠️` means the sibling SDK rename table records this destination as WRONG.
        # The map is owner-approved, so the marker discloses; it never edits the value.
        flag = " ⚠️" if r["name"] in CONTESTED_DESTINATION else ""
        out.append(
            f"| {i} | `{r['name']}` | {src} | {bind} | "
            f"{'yes' if r['read_only'] else 'no'} | `{DESTINATION[r['name']]}`{flag} |"
        )

    out += [
        "",
        f"**{n_retired}** of these are RETIRED names (#3883): off the advertised surface, but they",
        "still answer through the warning shim, and each one's `Destination` is the destination of",
        f"the replacement that warning names. The other **{n_live}** are live.",
        "",
        "Listed so a reader can tell them apart from the live rows that share their destination: "
        + ", ".join(f"`{n}`" for n in retired_names),
        "",
        "**A `⚠️` after a destination means the sibling SDK rename table**",
        "**(`docs/product/sdk-rename-table.md` (generated on demand) §C3b, and its C6 fold record) records that**",
        "**destination as WRONG.** The map is owner-approved, so it is NOT edited here; §D2c states",
        "the documented reading and the authority for it.",
        "",
        "### Destination counts",
        "",
        "| Destination | Count |",
        "|---|---|",
    ]
    for dest, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| `{dest}` | {n} |")
    out += [
        f"| **total** | **{total}** |",
        "",
        f"Destination rows: **{len(counts)}**. Registry tools: **{len(rows)}**.",
        "",
        "## Part C — blockers",
        "",
        "### C1 — target tools with NO method on `TortoiseSDK`",
        "",
    ]
    if no_method:
        out += ["| Target tool | Where | Finding |", "|---|---|---|"]
        out += [f"| `{b['what']}` | {b['scope']} | {b['why']} |" for b in no_method]
        out.append("")
        out += [
            "**These are not renames.** They are new methods that must be built in Phase 2, and the",
            "plan listed them as if they were renames. This is what Part A exists to catch.",
        ]
    else:
        out.append("None — every target tool has a method behind it.")

    out += ["", "### C2 — registry bindings that do not resolve", ""]
    if other:
        out += ["| Registry tool | Declared binding | Source |", "|---|---|---|"]
        for b in other:
            out.append(
                f"| `{b['tool']}` | `{b['declared']}` | `tool_registry.py:{b['line']}` |"
            )
        out.append("")
        out += [
            "A tool that declares a binding to a method **that is not a `def` on `TortoiseSDK`** is a",
            "declaration that cannot be honoured. It fails silently today because nothing checks it.",
        ]
    else:
        out.append("None.")

    # ── Part D — the destination citations ──────────────────────────
    n_cited = len(cites)
    unsupported, clause_only, ambiguous = _citation_findings(cites)
    approved = _approved_readings(menu, (unsupported, clause_only, ambiguous))
    by_quote: dict[tuple[str, str], list[str]] = {}
    for tool, c in sorted(cites.items()):
        by_quote.setdefault((c["doc"], c["quote"]), []).append(tool)

    out += [
        "",
        "## Part D — destination citations",
        "",
        "The `Destination` column's evidence is the beta doc's own disposition row, quoted",
        "**in full**. The quote is not decoration. A read that stops at the clause agreeing with",
        "the row drops the clause that contradicts it, and because a truncated prefix of a real",
        "sentence is still a real substring, a `quote in text` test passes while the evidence has",
        "been edited to agree with the row. **Every quote below is the whole disposition ROW, read",
        "at build time** — a truncation is not merely detected, it is *impossible to construct*,",
        "because there is no slicing step: the row **is** the quote. (The authored FOLD hop below",
        "is hand-typed, so the generator additionally rejects it with a maximality check.)",
        "",
        f"**{len(by_quote)} citations cover {n_cited} of the {len(rows)} registry rows.** The other **{len(rows) - n_cited}**",
        "are map decisions with no disposition row in the doc to cite — a net-new target, or a row",
        "that table does not carry.",
        "",
        "### D1 — the citation corpus",
        "",
    ]
    for (doc, quote), tools in sorted(by_quote.items()):
        out.append(f"- `{doc}` · rows {', '.join(f'`{t}`' for t in tools)}")
        out.append(f"  > {quote}")

    out += [
        "",
        "**Documented hops.** A citation can name something that is not a target because it is",
        "itself folded one hop further. The hop is stated in the doc, so it is carried as a",
        "citation of its own — checked by the same rule — rather than assumed. Without it",
        "`tortoise_get_source_reliability`'s row reads as unsupported, which is not a finding.",
        "",
    ]
    for name, (_fold_doc, fold_quote, fold_target) in sorted(FOLDS.items()):
        out.append(f"- `{name}` → `{fold_target}`")
        out.append(f"  > {fold_quote}")

    out += [
        "",
        f"**{len(unsupported)} rows disagree with their own citation; {len(clause_only)} are supported only",
        f"beyond the first clause; {len(ambiguous)} sit under an ambiguous citation.** Every count",
        "here is computed from the doc, not typed.",
        "",
        "### D2 — citations that do NOT name their row's destination",
        "",
        "**These are findings, not edits.** A row whose destination is not named by the row's own",
        "full citation is the `get_source_reliability` failure mode read one level up — the row and",
        "its evidence disagree. The destination map is owner-approved, so the disagreement is",
        "reported here with its evidence and the mapping is left ALONE. Changing an owner-approved",
        "destination is not a build step.",
        "",
        "**D2 is a LOWER BOUND, and reads that way on purpose.** Its predicate is exhaustive — every",
        "row whose destination is named *nowhere* in its citation is listed. What it cannot decide",
        "is clause ATTRIBUTION. `tortoise_assess_source` is the concrete case: its citation names the",
        "setter's `manage_source_trust` first and the reader's `list_sources` second, and the map",
        "puts it on the setter's target — a reading of which clause applies, not a computation. Such",
        "rows are visible in D3, not here, and are not counted as disagreements.",
        "",
    ]
    if unsupported:
        for f in unsupported:
            named = ", ".join(f"`{n}`" for n in f["named"]) or "*(no target named)*"
            out.append(f"- **`{f['tool']}`** — map says `{f['dest']}`; citation names {named}")
            out.append(f"  > {f['quote']}")
    else:
        out.append("None — every citation names its row's destination.")

    out += [
        "",
        "### D2b — rows whose support exists ONLY beyond the first clause",
        "",
        "These rows are **why the first-clause split is load-bearing, not decorative**. Their",
        "destination is named by the citation, but only in a clause after the first `→` — the",
        "exact point a truncated read would stop. A read that took only the first clause would",
        "lose the row's whole support, silently — so the generator computes the first-clause names",
        "(`_prefix_targets`) and surfaces any discrepancy as this list.",
        "",
    ]
    if clause_only:
        for f in clause_only:
            named = ", ".join(f"`{n}`" for n in f["named"])
            prefix = ", ".join(f"`{n}`" for n in f["prefix"]) or "*(no target in the first clause)*"
            out.append(f"- **`{f['tool']}`** — map says `{f['dest']}`; the first clause names {prefix}, the full citation names {named}")
            out.append(f"  > {f['quote']}")
    else:
        out.append("None — every row's destination is named in its citation's first clause.")

    out += [
        "",
        "### D2c — destinations the sibling SDK rename table records as WRONG",
        "",
        "`docs/product/sdk-rename-table.md` (generated on demand) reconciles the same surface this file maps, and its",
        "§C3b finding plus its C6 fold record name a different destination for the rows below.",
        "**The destination map here is owner-approved, so it is reported, not edited** — the same",
        "rule D2 states. Each row's documented reading and the authority for it are shown, so the",
        "disagreement is visible at the row instead of only in the sibling artifact.",
        "",
    ]
    if CONTESTED_DESTINATION:
        for tool, (reading, authority) in sorted(CONTESTED_DESTINATION.items()):
            out.append(
                f"- **`{tool}`** — map says `{DESTINATION[tool]}`; the documented reading is "
                f"`{reading}`"
            )
            out.append(f"  > {authority}")
    else:
        out.append("None — no destination is recorded as wrong in the sibling artifact.")

    out += [
        "",
        "### D3 — citations that name more than one target",
        "",
        "A citation here does not by itself determine a destination: it names several, split by",
        "prose (`; reads via …`, `where they are …`, `for annotation`). Which clause applies to",
        "which method is a reading, not a computation — so the **full** quote is rendered for these",
        "rows in D1, where the clause a truncated read would have dropped is visible.",
        "",
        "| Row | Destination (map) | Citation names | First clause names |",
        "|---|---|---|---|",
    ]
    if ambiguous:
        for f in ambiguous:
            out.append(
                f"| `{f['tool']}` | `{f['dest']}` | "
                f"{', '.join(f'`{n}`' for n in f['named'])} | "
                f"{', '.join(f'`{n}`' for n in f['prefix']) or '*(none)*'} |"
            )
    else:
        out.append("| *(none)* | | | |")
    out += [
        "",
        "### D4 — what the OWNER-APPROVED menu says about every disputed row (#4513)",
        "",
        "Part D above reads `beta-sdk-surface.md`. This section reads the OTHER owner-approved",
        "doc — `canonical-mcp-tools.md` (`approval_status: approved`, PR #4120) — whose `Absorbs`",
        "cell is the owner's own record of **which current name folds into which target**. That",
        "binding was never consulted here, so a row could contradict the approved menu and the",
        "build stayed green (#4513 §1/§2).",
        "",
        "**Nothing is decided here.** Where the two approved docs disagree, the ruling is the",
        "owner's and the map is NOT edited — the disagreement is shown with BOTH citations so it",
        "is visible at the row. What the approved menu *can* settle mechanically is narrower:",
        "does a target the row's citation names actually absorb this method?",
        "",
        "**Scope, stated exactly:** these are the rows Part D above already flags (D2/D2b/D3).",
        "A row Part D does not flag is NOT here even when the approved menu disagrees with it —",
        "covering every cited row is the whole-`Destination` derivation in #4518/#5063, which",
        "this section deliberately does not pre-empt. The sibling",
        "`docs/product/sdk-rename-table.md` (§C3b) reads these rows from beta's prose ALONE and",
        "states a winner on several of them; this section is the other approved doc's side of the",
        "same rows, not a correction of it.",
        "",
        "- **`row`** — the target is an approved row that absorbs this method: the approved doc",
        "  itself places the method there.",
        "- **`successor`** — the target is one the absorbing row's cell names as beta's own split",
        "  of that row, so the row supports it (one of several successors).",
        "- **`refuted`** — an approved row (or a successor of one) covers this target and does",
        "  **not** absorb this method: the approved doc does not place the method there. (It",
        "  does not claim the doc places it SOMEWHERE ELSE — a method the doc never absorbs at",
        "  all reads `refuted` too, and its evidence line below says so.)",
        "- **`rehomed`** — the approved doc **does** absorb this method, but into a different row",
        "  than this map's destination, and the destination is neither an approved row nor a",
        "  name any row's beta split states. Read with `refuted`: the two approved docs",
        "  disagree. It is NOT `unstated` — the `Approved home` cell and the quoted row below",
        "  name the method, so the approved menu does answer it.",
        "- **`unstated`** — the approved doc names neither this target nor any row that absorbs",
        "  this method, so it neither supports nor refutes it. A row resting only on `unstated` is",
        "  genuinely owner-open.",
        "",
        "The membership set is the doc's OWN closure, parsed from its prose:",
        *_closure_sentences(closures),
        "",
        "A name it collapses without listing still resolves, and a closure the parse drops",
        "fails the build. The lines above are emitted from that parse — not typed — so this",
        "paragraph cannot describe a closure the membership does not use.",
        "",
        "| Row | Map destination | Approved home | Home's beta successors | Map destination reads | Citation rivals read |",
        "|---|---|---|---|---|---|",
    ]
    if approved:
        for a in approved:
            home = (
                f"`{a['home']}` (row {a['row_no']})"
                if a["home"]
                else (
                    "*(several: " + ", ".join(f"`{h}`" for h in a["homes"]) + ")*"
                    if a["homes"]
                    else "*(no approved row names it)*"
                )
            )
            succ = ", ".join(f"`{s}`" for s in a["successors"]) or "*(none)*"
            rivals = ", ".join(f"`{t}` — **{v}**" for t, v in a["rivals"]) or "*(none)*"
            out.append(
                f"| `{a['tool']}` | `{a['dest']}` | {home} | {succ} | "
                f"**{a['dest_verdict']}** | {rivals} |"
            )
    else:
        out.append("| *(none)* | | | | | |")

    # The summary is a PARTITION of the rows, and each bucket's claim is exactly what the
    # verdict establishes: `refuted` no longer means "the doc places it elsewhere" (a method
    # the doc never absorbs reads `refuted` too), so "placed ELSEWHERE" is reserved for rows
    # that HAVE a home, and a home-less `refuted` row belongs with `unstated` — neither is
    # placed by the approved menu.
    agrees = sum(1 for a in approved if a["dest_verdict"] in ("row", "successor"))
    placed_elsewhere = [
        a["tool"]
        for a in approved
        if a["homes"] and a["dest_verdict"] in ("refuted", "rehomed")
    ]
    refuted_with_home = [
        a["tool"] for a in approved if a["dest_verdict"] == "refuted" and a["homes"]
    ]
    refuted_no_home = [
        a["tool"] for a in approved if a["dest_verdict"] == "refuted" and not a["homes"]
    ]
    rehomed = [a["tool"] for a in approved if a["dest_verdict"] == "rehomed"]
    unstated = [a["tool"] for a in approved if a["dest_verdict"] == "unstated"]
    uncovered = len(refuted_no_home) + len(unstated)
    out += [
        "",
        f"**{agrees} of {len(approved)} disputed rows are placed by the approved menu where this",
        f"map places them; {len(placed_elsewhere)} are placed ELSEWHERE by it; {uncovered} have",
        "no place in it at all.** Every count is computed from the doc, not typed.",
        "",
    ]
    if refuted_with_home:
        out.append(
            "- **The approved menu contradicts the map on**: "
            + ", ".join(f"`{t}`" for t in refuted_with_home)
            + " — an approved row (or a successor of one) covers the map's destination and does"
            " **not** absorb the method, while another row does. Two owner-approved docs"
            " disagreeing is an owner ruling, not a build step, so the destinations stand and"
            " the conflict is recorded here."
        )
    if rehomed:
        out.append(
            "- **The approved menu absorbs the method in a DIFFERENT row than the map's"
            " destination** (and the destination is neither an approved row nor one any beta"
            " split names): "
            + ", ".join(f"`{t}`" for t in rehomed)
            + " — the same disagreement as the sentence above, reached through a different"
            " door: the method is NOT unnamed, its approved home is in the table and the"
            " whole approved row is quoted below."
        )
    if refuted_no_home:
        out.append(
            "- **The approved menu covers the map's destination and does not absorb the method "
            "ANYWHERE**: "
            + ", ".join(f"`{t}`" for t in refuted_no_home)
            + " — the destination row exists and excludes the method, and no other approved row"
            " takes it, so the map's destination has no approved answer."
        )
    if unstated:
        out.append(
            "- **The approved menu names neither the map's destination nor any row that absorbs"
            + " the method, for: "
            + ", ".join(f"`{t}`" for t in unstated)
            + " — the row is not refuted either; it rests on beta alone and is genuinely"
            " owner-open."
        )
    out += [
        "",
        "**The approved row itself, for each disputed row that has one** (whole table row, by",
        "construction, so it cannot be a truncation of what the doc says):",
        "",
    ]
    if approved:
        out += _approved_evidence_lines(approved)
    else:
        out.append("None.")

    out += [
        "",
        "---",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python tools/bridge_table.py          # regenerate this file",
        "uv run python tools/bridge_table.py --check  # verify, non-zero exit on drift",
        "```",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; do not write")
    args = ap.parse_args()

    rows = _registry_rows()
    sdk_defs = _sdk_targets()
    cites = _doc_citations()
    # ONE read of the approved doc, for the guards AND the section that renders it: the
    # membership sentence and the membership applied are then the same parse, by
    # construction (#8930 review, P2b).
    menu, closures, row_errors = _approved_state()

    errs = [
        *_validate(rows),
        *_citation_errors(cites),
        *_approved_errors(menu, closures, row_errors),
    ]
    if errs:
        print("BRIDGE TABLE BUILD FAILURE — the map and the registry disagree:", file=sys.stderr)
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        return 1

    doc = render(rows, sdk_defs, cites, menu, closures)

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != doc:
            print(f"DRIFT: {OUT} is stale. Run: uv run python tools/bridge_table.py", file=sys.stderr)
            return 1
        print(f"OK: {OUT} is current ({len(rows)} registry tools).")
        return 0

    OUT.write_text(doc, encoding="utf-8")
    no_method, _ = _blockers(rows, sdk_defs)
    unsupported, clause_only, ambiguous = _citation_findings(cites)
    print(f"wrote {OUT}")
    print(f"  registry tools: {len(rows)}")
    print(f"  target methods with NO def on TortoiseSDK ({len(no_method)}): "
          f"{', '.join(b['what'] for b in no_method) or 'none'}")
    print(f"  citations: {len(cites)} rows cited from {BETA_DOC.name}; "
          f"{len(unsupported)} disagree with their citation, {len(clause_only)} supported "
          f"only beyond the first clause, {len(ambiguous)} under an ambiguous citation "
          f"(all report-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
