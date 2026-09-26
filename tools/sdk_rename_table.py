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
  * every `sdk.py:N` line number, and the def count — **284** `def` statements in
    the class body, of which **150** are public (no leading underscore). The 281
    sometimes quoted is 284 minus the 3 dunders; this file uses 284/150 throughout;
  * the **40 targets** — parsed out of `docs/product/beta-sdk-surface.md` (the
    owner-approved surface), not re-typed here;
  * the **canonical group partition** (R1–R9 / W1–W17 / N1–N6) — parsed out of
    `docs/product/canonical-sdk-methods.md` and checked to cover the AST walk exactly;
  * every count in this document, and the findings in Part C.

Authored (a design decision, reviewed once):
  * which target each group collapses to, and the handful of per-method exceptions;
  * the citation for each row — a `(doc, quote)` pair whose quote is **verified to be a
    substring of the cited doc AND to be a maximal one at build time**: a quote may not
    stop mid-clause, because that is where a truncation can drop the very clause that
    contradicts it (`… → `manage_source_trust`` used to drop `; reads via `list_sources``).

The generator **FAILS** (it does not paper over) if the map and the SDK disagree, if the
canonical partition does not cover the public surface exactly, or if a citation's quote
is no longer in the doc it names **or is a truncation of it**.

USAGE
    uv run python tools/sdk_rename_table.py            # write the doc
    uv run python tools/sdk_rename_table.py --check    # verify only, non-zero on drift

NOT COMMITTED (#5373)
    `docs/product/sdk-rename-table.md` is GENERATED ON DEMAND and gitignored. It is a
    function of `sdk.py` line numbers, so committing it made any two concurrent
    `sdk.py` PRs conflict on it even when their real changes were orthogonal — measured
    2026-09-26: it was the sole conflict in 8 of the 44 PRs that did not merge cleanly,
    and normalising `sdk.py:<line>` made the two sides byte-identical.

    It is deliberately NOT `merge=union` (two regenerations are different documents; a
    line-level union duplicates rows and matches neither side) and NOT a custom merge
    driver (a driver would have to regenerate from the MERGED `sdk.py`, which does not
    exist while a per-file driver runs). Not committing it removes the conflict class.
    `tests/test_sdk_rename_table.py` renders it to a temp file and asserts every row
    against independent AST/doc oracles, so the mapping is still checked on every run.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
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


def _doc_text(name: str) -> str:
    """The source text a citation names, read fresh at build time."""
    return (BETA_DOC if name == BETA else CANON_DOC).read_text(encoding="utf-8")

# ─────────────────────────────────────────────────────────────────────
# THE DISPOSITION VOCABULARY — declared in `config/disposition-vocabulary.yml` and
# READ from there, never restated here. The field models a disposition as three
# ORTHOGONAL AXES (lifecycle, visibility, basis) plus an explicit DELETE instruction;
# a single flat enum was the defect. The observed harm: `DISCARDED` was consumed as a
# COMMAND ("Phase 2's signal to DELETE"), so "not on the public surface" and "delete
# this" were spelled the same way, so one word carried a visibility fact and a
# delete COMMAND.
#
# ⛔ `deferred` is deliberately absent: it already means *postponed / not done* in
# `docs/ONTOLOGY.md`, the OPPOSITE of what the old rename table used `DEFERRED` for.
# The state it named is `visibility: unlisted`.
# ─────────────────────────────────────────────────────────────────────
VOCAB_PATH = ROOT / "config" / "disposition-vocabulary.yml"


def _load_vocabulary() -> dict:
    """The declared vocabulary, read from its one authoritative home."""
    import yaml

    if not VOCAB_PATH.is_file():
        raise SystemExit(
            f"the disposition vocabulary is UNDECLARED: {VOCAB_PATH} is missing. "
            "The axes and the disposition combinations must have one home."
        )
    doc = yaml.safe_load(VOCAB_PATH.read_text(encoding="utf-8"))
    for key in ("axes", "delete_instruction", "dispositions"):
        if not isinstance(doc, dict) or key not in doc:
            raise SystemExit(f"{VOCAB_PATH.name} declares no `{key}` section")
    for axis in ("lifecycle", "visibility"):
        if "values" not in doc["axes"].get(axis, {}):
            raise SystemExit(f"{VOCAB_PATH.name} declares no `{axis}` values")
    return doc


VOCAB = _load_vocabulary()
AXIS_VALUES = {axis: tuple(spec["values"]) for axis, spec in VOCAB["axes"].items()}
DELETE_VALUES = tuple(VOCAB["delete_instruction"]["values"])

# The disposition KEYS. A target method name in a row's authored value means RENAMED
# (or UNCHANGED when the target IS the method); everything else is one of these keys.
UNCHANGED = "UNCHANGED"
RENAMED = "RENAMED"
DISCARDED = "DISCARDED"
UNLISTED = "UNLISTED"  # the state the old vocabulary spelled `DEFERRED`
RELOCATED = "RELOCATED"
UNBACKED = "UNBACKED"
CONTESTED = "CONTESTED"
NON_TARGET = (UNCHANGED, RENAMED, DISCARDED, UNLISTED, RELOCATED, UNBACKED, CONTESTED)
BASIS_UNBACKED = "unbacked"
BASIS_CONTESTED = "contested"


def _disposition(method: str, value: str) -> dict:
    """Expand an authored value into the axes, per the DECLARED vocabulary.

    A disposition key expands through the declaration; a target method name is a RENAME
    (or UNCHANGED when it is the method itself) — the two implicit dispositions, derived
    here rather than declared per row.
    """
    key = (
        value if value in VOCAB["dispositions"]
        else UNCHANGED if value == method
        else RENAMED
    )
    spec = VOCAB["dispositions"][key]
    target = ""
    if spec.get("target") == "self":
        target = method
    elif spec.get("target") == "named":
        target = value
    return {
        "disposition": key,
        "target": target,
        "lifecycle": spec.get("lifecycle"),
        "visibility": spec.get("visibility"),
        "delete": spec.get("delete"),
        "basis_override": spec.get("basis"),
    }


# A disposition is only as strong as the section it is cited from. A row claiming
# DISCARDED may cite text from beta's "Discarded — and why" → "### Removed" ONLY,
# and a row claiming UNLISTED from its "Named but not solved" — a quote from one
# section cannot back the other's disposition. This is exactly how the journal (filed
# under "Named but not solved" as live and unlisted) was rendered DISCARDED, and how
# `test_guard` ("**Kept and relocated**", in the *relocated* subsection, not Removed)
# was too. Narrowing DISCARDED to "### Removed" — not the whole "Discarded" section —
# is what makes "kept" mechanically distinguishable from "deleted". Heading text is
# located in the doc at build time; a missing heading fails the build rather than
# silently freeing every row that cites it.
DISPOSITION_SECTION: dict[str, str] = {
    DISCARDED: "### Removed",
    UNLISTED: "## Named but not solved",
}

# RETENTION VOCABULARY — the clause test that makes a `DISCARDED` row more than a row
# that merely SITS under "### Removed".
#
# `DISCARDED` is Phase 2's signal to DELETE. beta's "### Removed" table contains one row
# whose rationale says the opposite of its section — it names the capability as *state*
# folded onto `update_knowledge` / `list_knowledge`, with "No separate verb" — and that
# is a FOLD, not a delete. Sitting under a heading is a LOCATION, not a clause:
# `list_drafts` and `quarantine_batch` were rendered `DISCARDED` there while
# `promote_point`/`set_point_baseline` were flagged contested in C3b, so the same row
# produced two different readings of the same ruling.
#
# The check is a DENYLIST rather than an allowlist because retention is phrased a small
# closed number of ways in these docs ("reachable", "kept", "live" …) while removal is
# phrased many ("cut", "dropped", "dead code", "never product surface", "not in the
# SDK", …). It is a tripwire over the DERIVED region — the primary fix is that these rows
# carry `CONTESTED`, not `DISCARDED` at all.
RETENTION_MARKERS = (
    "reachable", "kept", "relocated", "not discarded", "live", "unlisted",
    "survives", "retained", "remains", "stays", "continues to serve",
    "still serves",
    # The fold vocabulary the w11 row now uses: a capability retained as a FIELD on the
    # general update, selected by a FILTER on the general list — never a separate verb.
    "field on", "fields on", "no separate verb", "filter on",
)


def _retention_clauses(text: str) -> list[str]:
    """The retention words a derived region uses — the clause test for DISCARDED."""
    return [m for m in RETENTION_MARKERS
            if re.search(rf"(?<![A-Za-z]){re.escape(m)}(?![A-Za-z])", text, re.I)]


def _section_bounds(text: str, heading: str) -> tuple[int, int]:
    """The span of `heading`'s section, ending at the NEXT markdown heading (any level).

    Stopping at the next `## ` would let `### Removed` swallow the sibling
    `### Renamed, relocated…` subsection — the two are different rulings, and a
    `DISCARDED` row citing the relocated table must not pass as "Removed".
    """
    start = text.index(heading)
    nxt = text.find("\n#", start + len(heading))
    return start, (len(text) if nxt < 0 else nxt)

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
# C6 — the lifecycle/confidence FOLD, and its three-way disagreement.
#
# beta's "### Removed" row filed `promote_point`, `set_point_baseline`, `list_drafts` and
# `quarantine_batch` as *reachable* without NAMING a destination, so they rendered as an
# open finding. Three of the project's own docs
# disagreed about the fold:
#   * `canonical-mcp-tools.md` row 17 (owner-approved 2026-09-18): `revise_knowledge`
#     ABSORBS `promote_point` and `set_point_baseline`;
#   * `beta-sdk-surface.md` (approved 2026-09-21): all four under "### Removed", no
#     destination — and it SPLIT `revise_knowledge` into `update_knowledge` +
#     `supersede_knowledge`, so the approved absorber's NAME no longer exists;
#   * `bridge-table.md` (generated): `refresh_confidence`, which `sdk-rename-table.md`
#     §C3b already records as wrong.
#
# The convergent shape (lifecycle/confidence state as a FIELD on the general update,
# selected by a filter on the general list) agrees with the owner's recorded ruling that
# "retract is a temporal FIELD on update, not a separate verb". Each entry carries its
# AUTHORITY, because two of the four rest on an owner-ruled absorption and two only on
# beta's own row clause — that difference is exactly what decides whether the item is
# still genuinely owner-open.
# ─────────────────────────────────────────────────────────────────────
C6_FOLD: dict[str, tuple[str, str]] = {
    "promote_point": (
        "update_knowledge",
        "the owner-approved MCP list absorbs it into `revise_knowledge`, whose beta "
        "successor is `update_knowledge`; the new status is a FIELD, and promote's "
        "incident-operator cascade and approval gate ride with it",
    ),
    "set_point_baseline": (
        "update_knowledge",
        "same approved absorption; the starting belief is a FIELD, not a separate verb",
    ),
    "quarantine_batch": (
        "update_knowledge",
        "beta's own row: reachable through `update_knowledge`; the quarantine state is "
        "a FIELD",
    ),
    "list_drafts": (
        "list_knowledge",
        "a filter on the general list: `list_knowledge(kind=…, status=…)`",
    ),
}

# ─────────────────────────────────────────────────────────────────────
# CITATIONS. A citation carries an ANCHOR that LOCATES a REGION of the named doc; the
# generator EXTRACTS the whole region at build time and renders its text. The anchor is
# verified to be a **unique** substring of the doc (`_validate`) — a locator that is
# ambiguous locates nothing — and the derived region is checked by `_maximal` as a
# secondary tripwire. A row is *stated* when its derived region NAMES the method and
# *derived* otherwise — the basis is COMPUTED from the region by `_names`, never
# authored beside it. An authored `named` set was the earlier design and let a row claim
# `stated` while its evidence named a different method; the two can no longer diverge
# because there is only one of them.
# ─────────────────────────────────────────────────────────────────────
CITES: dict[str, tuple[str, str]] = {
    # ── READ ────────────────────────────────────────────────────────
    "r1": (BETA, "| `search_sessions`, `suggest_entry_points`, `topic_summarize`, "
                 "`issue_insight`, `annotate_ask_hits` | 5 | → `search_knowledge`. |"),
    "r2": (BETA, "| `query`, `paginated_query`, `query_points_by_tag` | 3 | "
                 "→ `list_knowledge`. |"),
    "r1_canon": (CANON, "| R1 | `search_knowledge` | #1 | `tortoise_fts_query`, "
                        "`suggest_entry_points`, `search_sessions`, `issue_insight`, "
                        "`topic_summarize`, `annotate_ask_hits` |"),
    "r3": (BETA, "| `recall_gaps`, `recall_subgraph`, `recall_state`, `recall_legs`, "
                 "`calibrate_summary`, `calibration_passed` | ~6 | → `check_confidence` "
                 "for the confidence view; **`recall_subgraph` is dropped, not folded** "
                 "— `explore_connections` answers that question. The gaps question is "
                 "flagged in \"Named but not solved\". |"),
    "r3_restore": (BETA, "| `restore_point_at` | → row 7 **`get_historical_knowledge`**. "
                         "A **read**, not a write — it returns the version of a claim "
                         "valid on a date and mutates nothing. |"),
    "r3_drop_subgraph": (BETA, "**`recall_subgraph` is dropped, not folded** — "
                                "`explore_connections` answers that question. The gaps "
                                "question is flagged in \"Named but not solved\". |"),
    "r3_context": (BETA, "| `provenance`, `belief_timeline`, `session_context`, "
                         "`volunteer_context` | 4 | → `check_confidence` where they are "
                         "confidence context; `poll_events` where they are a timeline. |"),
    "r3_canon": (CANON, "| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, "
                        "`recall_subgraph`, `retrieval_legs`, `volunteer_context`, "
                        "`session_context`, `get_confidence`, `calibrate_summary`, "
                        "`calibration_passed`, `get_provenance_chain`, `provenance`, "
                        "`belief_timeline`, `restore_point_at` |"),
    "r4": (BETA, "| narrow readers (`get_session`, `get_events`, `get_owned_entities`, "
                 "`get_provenance_chain`, …) | ~8 | → `get_entity`, except where a "
                 "genuinely different shape is returned. |"),
    "r4_canon": (CANON, "| R4 | `get_entity` | #4 | `get_point`, `get_entity`, "
                        "`get_session`, `get_events`, `resolve_id` |"),
    "r5": (BETA, "| `traverse`, `expand_relationships`, `get_org_structure` | 3 | "
                 "→ `explore_connections`. |"),
    "r6": (BETA, "| `audit`, `validate_domain`, `summarize_structure`, "
                 "`dream_health_check`, `dream_health_state` | ~5 | → `graph_overview` "
                 "where they are orientation. The diagnostics are the held question "
                 "above. |"),
    "r6_aliases": (BETA, "narrow aliases absorbed by `graph_overview` — `taxonomy`, "
                         "`list_pointkinds`, `list_tags`, `list_namespaces`, "
                         "`list_graphs`, `status`, `stale`, `check_structure`, "
                         "`list_topics` | **Deleted, not folded.** The approved list "
                         "contains the container and not the aliases; shipping both is "
                         "the merge failing at its own goal. |"),
    "r6_test_guard": (BETA, "| `test_guard` | **Kept and relocated.** It guards the "
                            "production-wipe incident, so the code must survive — but it "
                            "is *test infrastructure* and moves out of the product SDK. |"),
    "r6_canon": (CANON, "| R6 | `graph_overview` | #6 | `status`, `taxonomy`, "
                        "`list_pointkinds`, `list_sources`, `list_tags`, "
                        "`list_namespaces`, `list_relations`, `list_topics`, "
                        "`list_graphs`, `stale_points`, `summarize_structure`, "
                        "`check_structure`, `audit`, `validate_domain`, "
                        "`dream_health_check`, `dream_health_state`, `test_guard` |"),
    "r6_list_sources": (BETA, "| `list_sources` | **Not discarded.** Present at "
                              "`tortoise/sdk.py` with an MCP tool and a CLI command "
                              "(`tortoise/__main__.py`), and it is covered by "
                              "`tests/test_enumeration_surfaces.py` and "
                              "`tests/test_connector_sources.py`. It folds into "
                              "**row 4 `list_knowledge(kind='source')`** — the *question* "
                              "it asks stays first-class and gains the credibility tier; "
                              "it no longer needs its own method. |"),
    "r7": (BETA, "| `review_connections`, `get_cross_lens_candidates`, "
                 "`list_dedup_candidates` | 3 | → `review_link_candidates`. |"),
    "r8": (BETA, "| `events_poll` | → row 11 `poll_events`. |"),
    "r9": (BETA, "| `list_batch`, `list_batches` | 2 | → `list_knowledge(kind='batch')`. "
                 "The batch contents come back inline in the bounded, paged page. |"),
    # ── WRITE ───────────────────────────────────────────────────────
    "w1": (BETA, "| `create_subject`, `create_object`, `create_event`, "
                 "`create_document`, `create_point` | 5 | Collapsed into "
                 "`create_entity(type=)`. The ontology models all of them as entities. |"),
    "w1_coup": (CANON, "| `create_or_update_point` → `create_point` |"),
    "w1_batch": (BETA, "| `batch_create_points` | 1 | → `write_knowledge_batch`. |"),
    "w2": (CANON, "| W2 | `write_knowledge` | — | `ingest` |"),
    # The anchor names the blockquote paragraph's opening line; the region is the whole
    # paragraph (canonical lines 27–32). The old anchor authored a mid-paragraph wrapped
    # line, so the sentence it belonged to could be cut.
    "w2_rename": (CANON, "> ⛔ **The SDK target is `docs/product/beta-sdk-surface.md` "
                         "(40 methods), not this document.**"),
    "w3": (CANON, "| W3 | `register_source` | #11 | `create_source`, `complete_source` |"),
    "w3_cut": (BETA, "| `complete_source` | 1 | **Cut.** Its entire body populates "
                     "`contentHash`, `version`, `externalId` — fields `register_source` "
                     "already writes — and it has **zero callers in the repo**. |"),
    "w4": (BETA, "| `ingest_corpus`, `index_file`, `session_index_health` | 3 | "
                 "→ `index_sources_from_directory`. |"),
    "w4_rename": (BETA, "| `index_sources` (bare) | 1 | Renamed → "
                        "`index_sources_from_directory`, so the index/mine distinction "
                        "is unmissable. |"),
    "w4_canon": (CANON, "| W4 | `index_files` | #12 | `index_file`, `index_directory`, "
                        "`ingest_corpus`, `index_sessions`, `mine_corpus`, "
                        "`reconcile_sessions`, `session_index_health`, "
                        "`backfill_about_entities` |"),
    "w4_mine": (BETA, "| `mine_corpus` | 1 | → `mine_knowledge_from_directory`. It is "
                      "the **batch form of `mine_knowledge_from_session`**, not a kind "
                      "of indexing. |"),
    "w4_index_sessions": (CANON, "| `index_sessions` / `ingest_corpus` → "
                                 "`index_directory` |"),
    # The anchor names the bullet's OPENING; the extracted region is the WHOLE bullet —
    # its wrapped continuation lines included. The old citation AUTHORED the bullet's
    # extent, so a prefix that stopped at the "live in the MCP server" line wrap still
    # passed while dropping "Filed post-beta … unlisted until then."
    "w5": (BETA, "- **The journal capability**"),
    "w6": (BETA, "| `capture_session` / `commit_session` | → row 16 "
                 "`mine_knowledge_from_session`, one method. The backend is the target "
                 "graph's configuration. |"),
    "w8": (BETA, "| `assess_source`, `set_source_tier`, `get_source_reliability` | 3 | "
                 "→ `manage_source_trust` for the setter; reads via `list_sources`. |"),
    "w8_backfill": (BETA, "| `backfill_v25`, `backfill_sources`, "
                          "`backfill_about_entities`, `reconcile_sessions` | 4 | "
                          "One-shot migrations. Run once, then dead code carrying a "
                          "public promise. |"),
    "w9": (BETA, "| `create_operator`, `create_direct_edge`, `create_derivation`, "
                 "`link_source_to_entity` | 4 | → `link_entities`, which dispatches on "
                 "the relation. |"),
    "w9_canon": (CANON, "| W9 | `link_entities` | #15 | `create_edge`, "
                        "`create_derivation`, `link_source_to_entity`, `create_operator`, "
                        "`create_direct_edge` |"),
    "w10": (BETA, "| `file_human_approval` | 1 | → `record_decision`. |"),
    "w10_file_decision": (BETA, "| `file_decision` | → rows 20/21 **`write_question`** "
                                "+ **`record_decision`**. It was filing a *question* "
                                "and calling it a decision. |"),
    "w11": (BETA, "| `update_point`, `update_entity` | 2 | → `update_knowledge`. |"),
    "w11_canon": (CANON, "| W11 | `revise_knowledge` | #17 | `update`, "
                         "`update_point`, `update_entity`, `supersede`, `supersede_point`, "
                         "`invalidate_point`, `retract_point`, `promote_point`, "
                         "`set_point_baseline`, `list_drafts`, `quarantine_batch` |"),
    "w11_supersede": (BETA, "| `supersede`, `supersede_point` | 2 | "
                            "→ `supersede_knowledge`. They also **disagree** — "
                            "`supersede_point` carries a `valid_from` the other "
                            "silently drops. |"),
    "w11_retract": (BETA, "| `retract_point`, `invalidate_point` | 2 | → fields on "
                          "`update_knowledge`. **Zep's shape:** retraction is "
                          "`invalid_at`/`expired_at` on the existing update, not a "
                          "separate verb. |"),
    "w11_lifecycle": (BETA, "| `promote_point`, `set_point_baseline`, `list_drafts`, "
                            "`quarantine_batch` | 4 | Lifecycle and confidence **state** "
                            "— a **field on `update_knowledge`** (promote, baseline, "
                            "quarantine), selected by a **filter on "
                            "`list_knowledge(kind=…, status=…)`** (drafts). No separate "
                            "verb. `promote_point` also promotes its incident operators "
                            "and carries the approval gate, so it is not "
                            "`update_point(status='live')`. |"),
    "w12": (BETA, "| `delete_point`, `delete_point_wrapped` | 2 | "
                  "→ `delete_knowledge`. |"),
    "w12_canon": (CANON, "| W12 | `delete_knowledge` | #18 | `delete`, "
                        "`delete_point`, `delete_entity`, `delete_point_wrapped` | "
                        "keep, collapse |"),
    "w13_canon": (CANON, "| W13 | `stabilize_beliefs` | #19 | `dream`, "
                        "`compute_confidence`, `compute_reputation`, "
                        "`record_calibration` |"),
    "w15": (BETA, "| `mitigate_operator`, `operator_action`, `annotate_operator` | 3 | "
                  "→ `adjust_relationship` for strength, `update_knowledge` for "
                  "annotation. `operator_action(**kwargs)` currently **accepts and "
                  "silently ignores** `credibility` — a bug. |"),
    "w17_ulid": (BETA, "| `ulid` | 1 | A ULID generator. Not a memory operation. |"),
    # The anchor names the blockquote's opening line; the extracted region is the WHOLE
    # paragraph (beta lines 107–111). The old quote stopped at line 108 and so could
    # never be contradicted by line 109's "The old→new mapping is a Phase 0.3b
    # deliverable and does not exist yet".
    "unchanged4": (BETA, "> **Every name here is a target, not a description of today.**"),
    # ── Control plane ───────────────────────────────────────────────
    "n1_console": (BETA, "| `org_update`, `org_delete`, `membership_get`, "
                         "`membership_update_role`, `apikey_verify` | 5 | "
                         "Console plumbing. `org_delete` is **settled**: an "
                         "end-customer must never be able to delete the builder's "
                         "account. **The builder's own account closure is a console "
                         "operation** — not in the SDK. |"),
    "n1_account": (BETA, "| 28 | `get_organisation_account` | Read the account and the "
                         "plan it is on |"),
    "n2_rename": (BETA, "| `graph_delete`, `graph_restore`, `graph_list`, "
                        "`graph_set_name` | → rows 30–33 `*_memory_graph*`. |"),
    "n2_keys": (BETA, "| `graph_key_ids`, `graph_active_key_count` | 2 | Console "
                      "diagnostics. Both fold into `list_keys`. |"),
    "n2_recording": (BETA, "| ~~`graph_set_recording`~~ (SDK method) | 1 | **Discarded "
                           "as an SDK method, KEPT as an MCP tool.** It is a per-field "
                           "setter, the same shape as `set_memory_graph_name`/"
                           "`set_memory_graph_backend`, which were deleted so that fields "
                           "go on create plus one partial update. The override therefore "
                           "folds into **`update_memory_graph`** (row 30) — while the "
                           "**MCP tool** `graph_set_recording` survives, because it is an "
                           "agent's only in-MCP recovery from the capture 409. |"),
    "n2_count": (BETA, "| `count_memory_graphs` | The plan is unlimited on builder "
                       "plans, so its stated purpose — checking an allowance — does "
                       "not exist. `list_memory_graphs` answers \"how many\" for any "
                       "real N. |"),
    "maintenance": (BETA, "| `trash_graphs`, `migrate_orgs_to_registry`, "
                          "`cleanup_expired_invitations`, "
                          "`sweep_invite_ghost_memberships` | 4 | **Our maintenance.** "
                          "Never product surface. |"),
    "n3_members": (BETA, "| 37 | `add_member` | Grant a person access to the account |"),
    "n4_keys": (BETA, "| 34 | `create_key` | Mint a credential scoped to one memory "
                      "graph. **The credential carries the tenant** — the client does "
                      "not pass a graph id | — | builder |"),
    "n5_invite": (BETA, "| `invitation_*` (6) | 6 | The invite **UX** belongs to the "
                        "console, where a human clicks it. |"),
    "n6_signup": (BETA, "| `signup_token_*` (3) | 3 | Operator-side agent self-signup — "
                        "our provisioning, not product surface. |"),
}

# ─────────────────────────────────────────────────────────────────────
# THE MAP. Group default first (the collapse the canonical inventory
# states), then per-method exceptions. Every value is either a name in
# the 40-method target surface parsed from the beta doc, or one of the
# non-target dispositions in `NON_TARGET` (UNCHANGED / DISCARDED /
# UNBACKED / UNLISTED / RELOCATED).
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
    # W5 is beta's "Named but not solved": the journal is LIVE (in the MCP server),
    # filed post-beta and unlisted — NOT dead. `DISCARDED` here would tell Phase 2 to
    # delete a capability the owner ruled is merely unlisted.
    "W5": (UNLISTED, "w5"),
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
    # beta's "Renamed, relocated, or re-homed": the code must survive but it is *test
    # infrastructure* and moves OUT of the product SDK — kept, but not a target.
    "test_guard": (RELOCATED, "r6_test_guard"),
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
    # W8 — the beta row splits the group: the setter and the assessment fold into
    # `manage_source_trust`, the *read* goes to `list_sources` (which the beta doc
    # folds into `list_knowledge(kind='source')`). The canonical W8 grouping puts all
    # three under `manage_source_trust` and calls `get_source_reliability` a writer;
    # that conflict is recorded in C3.
    "backfill_sources": (DISCARDED, "w8_backfill"),
    "get_source_reliability": ("list_knowledge", "w8"),
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
    # W11 — the four rows beta filed under "### Removed" as *reachable* without a
    # destination NAMED. The fold is now named in beta: lifecycle
    # and confidence **state** is a FIELD on `update_knowledge`, selected by a FILTER on
    # `list_knowledge`. `promote_point`/`set_point_baseline` rest on the owner-approved MCP
    # list's own absorption (into `revise_knowledge`, whose beta successor is
    # `update_knowledge`); `list_drafts`/`quarantine_batch` rest on beta's row clause —
    # that difference is recorded in C6, because it decides what is still owner-open.
    "promote_point": ("update_knowledge", "w11_lifecycle"),
    "set_point_baseline": ("update_knowledge", "w11_lifecycle"),
    "list_drafts": ("list_knowledge", "w11_lifecycle"),
    "quarantine_batch": ("update_knowledge", "w11_lifecycle"),
    # W12 — two members the beta row omits.
    "delete": ("delete_knowledge", "w12_canon"),
    "delete_entity": ("delete_knowledge", "w12_canon"),
    # W13 — the canonical group names all four members; two of them have no
    # plausible place on the 40-method target surface, so they are findings.
    "dream": ("refresh_confidence", "w13_canon"),
    "compute_confidence": ("refresh_confidence", "w13_canon"),
    "compute_reputation": (UNBACKED, ""),
    "record_calibration": (UNBACKED, ""),
    # W15 — the beta collapse row splits by clause: strength to `adjust_relationship`,
    # annotation to `update_knowledge` (and beta's target row 25 repeats it: “Annotating
    # a link is `update_knowledge` on it”). The canonical W15 grouping puts all three
    # under `adjust_relationship`; that conflict is recorded in C3.
    "annotate_operator": ("update_knowledge", "w15"),
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
    # The beta row 258 splits W8: reads go to `list_sources`; the canonical W8 row keeps
    # `get_source_reliability` on `manage_source_trust`. Part A carries beta's read.
    ("get_source_reliability", "`manage_source_trust`", "t_w8"),
    # The beta row 244 splits W15: annotation goes to `update_knowledge`; the canonical
    # W15 row keeps `annotate_operator` on `adjust_relationship`. Part A carries beta.
    ("annotate_operator", "`adjust_relationship`", "t_w15"),
]
TENSION_CITES: dict[str, tuple[str, str]] = {
    "t_r3": (CANON, "| R3 | `recall_beliefs` | #3 | `recall_state`, `recall_gaps`, "
                    "`recall_subgraph`, `retrieval_legs`, `volunteer_context`, "
                    "`session_context`, `get_confidence`, `calibrate_summary`, "
                    "`calibration_passed`, `get_provenance_chain`, `provenance`, "
                    "`belief_timeline`, `restore_point_at` |"),
    "t_r5": (CANON, "| R5 | `explore_connections` | #5 | `expand_relationships`, "
                    "`traverse`, `get_owned_entities`, `get_org_structure` |"),
    "t_r6": (CANON, "| R6 | `graph_overview` | #6 | `status`, `taxonomy`, "
                    "`list_pointkinds`, `list_sources`, `list_tags`, `list_namespaces`, "
                    "`list_relations`, `list_topics`, `list_graphs`, `stale_points`, "
                    "`summarize_structure`, `check_structure`, `audit`, "
                    "`validate_domain`, `dream_health_check`, `dream_health_state`, "
                    "`test_guard` |"),
    "t_n2": (CANON, "| N2 | `graph` | `graph_list`, `graph_count`, `graph_delete`, "
                    "`graph_restore`, `trash_graphs`, `graph_set_name`, "
                    "`graph_set_recording`, `graph_key_ids`, "
                    "`graph_active_key_count` |"),
    "t_w8": (CANON, "| W8 | `manage_source_trust` | #14 | `assess_source`, "
                    "`set_source_tier`, `get_source_reliability`, `backfill_sources` | "
                    "keep — **`get_source_reliability` writes** |"),
    "t_w15": (CANON, "| W15 | `adjust_relationship` | #21 | `operator_action`, "
                     "`mitigate_operator`, `annotate_operator` | keep, collapse |"),
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
PHANTOM_CITES: dict[str, tuple[str, str]] = {
    "p_graph_count": (BETA, "| `count_memory_graphs` | The plan is unlimited on builder "
                            "plans, so its stated purpose — checking an allowance — "
                            "does not exist. `list_memory_graphs` answers \"how many\" "
                            "for any real N. |"),
    "p_set_name": (BETA, "| `set_memory_graph_name`, `set_memory_graph_backend`, "
                         "`count_memory_graphs` | 3 | See \"Provisioning\" above. |"),
    "p_withdraw": (BETA, "| `withdraw_knowledge` | 1 | **Never existed** — removed from "
                         "the plan. Retraction is a field on `update_knowledge`. |"),
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
    seen: list[int] = []
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
            seen.append(int(cells[0]))
            found[int(cells[0])] = name
    # A DUPLICATE row number silently overwrites its predecessor and yields the same
    # 40 keys, so a count check cannot see it: the target on the overwritten row is
    # lost while `len(targets) == 40` still holds. The row numbers are the guard —
    # and they must be counted BEFORE the dict collapses them, or the check is blind.
    dupes = sorted({n for n in seen if seen.count(n) > 1})
    if dupes:
        raise SystemExit(
            f"the beta doc's target table repeats row number(s) {dupes}; a duplicate "
            f"silently drops the target on the row it overwrites, leaving 40 names "
            f"with one of them wrong"
        )
    numbers = sorted(found)
    if numbers != list(range(1, len(numbers) + 1)):
        raise SystemExit(
            f"the beta doc's target table is not numbered 1..N: {len(numbers)} rows, "
            f"range {numbers[0] if numbers else '-'}..{numbers[-1] if numbers else '-'}, "
            f"with gaps"
        )
    return [found[i] for i in numbers]


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


def _names(quote: str, method: str) -> bool:
    """Does `quote` NAME `method` as a whole token?

    The Basis legend defines `stated` as "the cited quote names this method", so this
    is the only thing that may decide it. A plain substring test is wrong: `delete` is
    a substring of `delete_entity`, so a quote naming the latter would mark the former
    `stated`. The boundary is the same character class as the method names themselves.
    """
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(method)}(?![A-Za-z0-9_])",
                     quote) is not None


# ─────────────────────────────────────────────────────────────────────
# REGIONS — the citation is EXTRACTED, never authored.
#
# Every prior guard here tried to CHECK an authored quote string: `quote in text`, then
# a sentence boundary, then a cell boundary, then a line end. Each was a heuristic with
# a hole, and the adversarial review found truncations still surviving: a quote ending at
# a source LINE WRAP (the journal bullet dropping "Filed post-beta … unlisted until
# then.") and a quote ending mid-ROW at an inner cell boundary (w8 dropping
# "; reads via `list_sources`"). The design was wrong, not the heuristic.
#
# The fix inverts it. A citation now carries only an ANCHOR — a small locator — and the
# generator EXTRACTS the whole named region (the table row the anchor sits in, or the
# paragraph block the anchor sits in) and renders it verbatim. There is no authored
# extent left to truncate: an anchor edited to stop at a line wrap, a sentence, or a
# mid-row cell still derives the SAME whole region, so a truncation cannot drop the
# clause that contradicts the row. The LEFT edge is constructed too — a row anchor that
# names only the rationale cell still renders the row from its first `|`.
#
# `_maximal` survives only as a cheap SECONDARY tripwire over the derived text: a region
# that cannot be rendered whole is refused, never shortened.
# ─────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Region:
    """A named region of the source: a table row, or a paragraph block.

    `anchor` LOCATES the region; it does not define its extent. The region KIND is
    inferred from the anchor's shape — a region whose anchor ends at a table cell (the
    anchor ends with `|`) is a row; everything else is a paragraph block. Both are
    extracted whole, so neither edge of the rendered evidence is authored.
    """

    anchor: str

    @property
    def kind(self) -> str:
        s = self.anchor.strip()
        if s.endswith("|"):
            return "row"
        if s.startswith("- "):
            return "bullet"
        return "paragraph"


def _line_bounds(text: str, at: int) -> tuple[int, int]:
    """The `(start, end)` of the line containing offset `at` — newline excluded."""
    start = text.rfind("\n", 0, at) + 1
    end = text.find("\n", at)
    return start, (len(text) if end < 0 else end)


def _paragraph_bounds(text: str, at: int) -> tuple[int, int]:
    """The maximal run of consecutive non-blank lines containing offset `at`.

    A markdown bullet is a paragraph, so this extracts the WHOLE bullet including its
    wrapped continuation lines — the journal bullet's "Filed post-beta … unlisted until
    then." is inside the region by construction.
    """
    start, _ = _line_bounds(text, at)
    while start > 0:
        prev_end = start - 1
        prev_start = text.rfind("\n", 0, prev_end) + 1
        if text[prev_start:prev_end].strip() == "":
            break
        start = prev_start
    _, end = _line_bounds(text, at)
    while end < len(text):
        nxt_start = end + 1
        nxt_end = text.find("\n", nxt_start)
        nxt_end = len(text) if nxt_end < 0 else nxt_end
        if text[nxt_start:nxt_end].strip() == "":
            break
        end = nxt_end
    return start, end


def _bullet_bounds(text: str, at: int) -> tuple[int, int]:
    """The WHOLE bullet containing offset `at` — its wrapped continuation included.

    Ends at the next blank line, the next top-level bullet, or the next heading. The
    journal bullet's wrapped "Filed post-beta … unlisted until then." is inside the
    region by construction.
    """
    start, _ = _line_bounds(text, at)
    if not text[start:].split("\n", 1)[0].startswith("- "):
        # the anchor is mid-bullet: walk up to the line that opens it
        while start > 0:
            prev_end = start - 1
            prev_start = text.rfind("\n", 0, prev_end) + 1
            prev = text[prev_start:prev_end]
            if prev.strip() == "":
                break
            start = prev_start
            if prev.startswith("- "):
                break
    _, end = _line_bounds(text, at)
    while end < len(text):
        nxt_start = end + 1
        nxt_end = text.find("\n", nxt_start)
        nxt_end = len(text) if nxt_end < 0 else nxt_end
        nxt = text[nxt_start:nxt_end]
        if nxt.strip() == "" or nxt.startswith("- ") or nxt.startswith("#"):
            break
        end = nxt_end
    return start, end


def _derive_region(text: str, region: Region) -> str:
    """EXTRACT the whole region — the row, bullet, or paragraph. Never a prefix.

    Raises if the anchor is absent (CITATION DRIFT is reported by its own check) or if a
    row anchor resolves to a non-row line (the anchor is misfiled). Returning the anchor
    itself is never an option: that is the authored-extent design this replaces.
    """
    at = text.find(region.anchor)
    if at < 0:
        raise KeyError(f"anchor not found in the source: {region.anchor[:60]!r}")
    if region.kind == "row":
        start, end = _line_bounds(text, at)
        row = text[start:end]
        if not row.startswith("|"):
            raise ValueError(
                f"a row anchor resolved to a line that is not a table row: {row[:60]!r}"
            )
        return row
    if region.kind == "bullet":
        start, end = _bullet_bounds(text, at)
        return text[start:end]
    start, end = _paragraph_bounds(text, at)
    return text[start:end]


def _maximal(quote: str, text: str) -> bool:
    """Is `quote` a maximal region of `text` — neither edge cut?

    True only when the match BEGINS at a line start and ENDS at a table-cell/row
    boundary (`|`), a line end, or the document end. The left edge is checked too: an
    earlier version checked only the right, so a quote could begin mid-cell. This is a
    SECONDARY tripwire over a region that `_derive_region` already built at those
    boundaries — it catches a derivation that cannot be rendered whole, which is refused
    rather than silently shortened.
    """
    idx = text.find(quote)
    if idx < 0:
        return True  # absent text is CITATION DRIFT, reported by its own check
    before_ok = idx == 0 or text[idx - 1] == "\n"
    after = text[idx + len(quote):]
    after_ok = after == "" or after.startswith("\n") or quote.endswith("|")
    return before_ok and after_ok


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
            value, key = OVERRIDE[name]
        else:
            value, key = GROUP_TARGET[label]
        disp = _disposition(name, value)
        if not key:
            if disp["basis_override"] is None:
                raise SystemExit(
                    f"{name} has no citation region and no declared evidence basis"
                )
            basis = disp["basis_override"]
            quote = ""
            doc = ""
        else:
            doc, anchor = CITES[key]
            # The evidence is the EXTRACTED region, never the authored anchor. This is
            # the whole point of the design: the anchor only locates, so a truncated
            # anchor cannot shorten the rendered evidence.
            quote = _derive_region(_doc_text(doc), Region(anchor))
            basis = disp["basis_override"] or (
                "stated" if _names(quote, name) else "derived"
            )
        rows.append({
            "name": name,
            "line": methods[name],
            "group": label,
            "target": disp["target"],
            "disposition": disp["disposition"],
            "lifecycle": disp["lifecycle"],
            "visibility": disp["visibility"],
            "delete": disp["delete"],
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
        # A row with no destination is keyed by its DISPOSITION, so the no-destination
        # categories stay visible in the counts instead of collapsing into one bucket.
        by_target.setdefault(_destination(r), []).append(r["name"])
    no_def = [t for t in targets if t not in methods]
    return {
        "by_target": by_target,
        "no_def": no_def,
        "no_def_set": set(no_def),
        "unbacked": sorted(r["name"] for r in rows if r["basis"] == BASIS_UNBACKED),
        "contested": sorted(r["name"] for r in rows if r["basis"] == BASIS_CONTESTED),
        "tensions": TENSIONS,
        "phantoms": PHANTOMS,
        "unfilled": [g for g, m in groups.items() if not m],
        # Computed from the sibling Phase 0.1 artifact, never restated.
        "bridge_divergences": _bridge_divergences(rows),
    }


def _destination(r: dict) -> str:
    """A row's destination on one vocabulary: the target name, or its disposition key.

    `UNCHANGED` rows carry their own name as the target (they are already on the target
    surface) — the same name the sibling bridge renders, so the two artifacts compare.
    """
    return r["target"] if r["target"] else r["disposition"]


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
    known = set(targets) | set(NON_TARGET)
    for m in sorted(methods):
        target = OVERRIDE.get(m, (None, None))[0]
        if target is None:
            label = next((g for g, ms in groups.items() if m in ms), "ARCHIVE")
            target = GROUP_TARGET.get(label, (None, None))[0]
        if target not in known:
            errs.append(f"UNRECOGNISED destination for {m}: {target!r}")

    # 4. A citation LOCATES a region of the doc; the generator EXTRACTS it. The anchor
    #    must be unique (an ambiguous locator locates nothing) and the derived region
    #    must be renderable whole (`_maximal` is the secondary tripwire).
    for key, (doc, anchor) in cites.items():
        if not anchor.strip():
            errs.append(f"EMPTY CITATION: {key} carries no anchor — an empty string "
                        f"locates the start of every document, so the citation resolves "
                        f"while the destination is asserted by nothing")
            continue
        text = _doc_text(doc)
        n = text.count(anchor)
        if n == 0:
            errs.append(f"CITATION DRIFT: {key} names {doc} but its anchor no longer "
                        f"locates any region: {anchor[:70]!r}")
        elif n > 1:
            errs.append(f"AMBIGUOUS CITATION ANCHOR: {key} locates {n} regions in "
                        f"{doc}; a locator must be unique — {anchor[:70]!r}")
        else:
            region = _derive_region(text, Region(anchor))
            if not region.strip():
                errs.append(f"EMPTY CITATION REGION: {key} extracts nothing from {doc}")
            elif not _maximal(region, text):
                after = text[text.find(region) + len(region):][:60]
                errs.append(
                    f"CITATION REGION NOT MAXIMAL: {key}'s derived region is cut at an "
                    f"edge — the source continues {after!r}. A region that cannot be "
                    f"rendered whole is REFUSED, never silently shortened."
                )

    # 4b. A phantom row's finding IS "the doc uses this name". Evidence that does not
    #      contain the name does not support that finding, so it is refused here.
    for name, _referent, key in PHANTOMS:
        found = cites.get(key)
        if not found:
            errs.append(f"PHANTOM EVIDENCE UNRESOLVED: {name} cites unknown key {key!r}")
            continue
        doc, anchor = found
        region = _derive_region(_doc_text(doc), Region(anchor))
        if not _names(region, name):
            errs.append(f"PHANTOM EVIDENCE DOES NOT NAME IT: {name}'s cited region does "
                        f"not contain {name!r} — the evidence does not support the row")

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


# ─────────────────────────────────────────────────────────────────────
# CROSS-ARTIFACT RECONCILIATION — the sibling Phase 0.1 `bridge-table.md`.
#
# The two artifacts answer different questions: this one, current SDK *method* →
# target *method*; the bridge, current MCP *tool* → target *tool*. A tool and the
# method it binds to can therefore legitimately reach different targets, and where
# they do the divergence is RECORDED here rather than harmonised by picking one.
#
# The divergence list is COMPUTED from `bridge-table.md` (a copy would be a second
# answer and the two would drift); the DETERMINATION is authored, because the reason
# is the finding. The build fails if a computed divergence has no determination — or
# if a determination no longer corresponds to one, so the document cannot keep
# claiming a disagreement the sibling has already resolved.
# ─────────────────────────────────────────────────────────────────────
BRIDGE_DOC = ROOT / "docs" / "product" / "bridge-table.md"
_BRIDGE_ROW = re.compile(
    r"^\| \d+ \| `(tortoise_[a-z0-9_]+)` \| `tool_registry\.py:\d+` \| (.*?) \| "
    r"(?:yes|no) \| (.*?) \|$"
)

BRIDGE_DETERMINATION: dict[str, str] = {
    "annotate_operator": "**Part A is right; the bridge is wrong.** beta row 25 states it "
                         "twice — \u201cAnnotating a link is `update_knowledge` on it\u201d and "
                         "the W15 row's \u201c`adjust_relationship` for strength, "
                         "`update_knowledge` for annotation\u201d. The bridge follows the "
                         "canonical sketch's W15 grouping, which beta governs.",
    "compute_confidence": "**Part A is right.** The canonical W13 group is renamed by beta to "
                          "`refresh_confidence` (\u201cRecompute confidence after changes\u201d); "
                          "`check_confidence` is the READ (\u201cReturns the confidence view "
                          "only\u201d). `compute_confidence` recomputes, so the bridge "
                          "follows the canonical group rather than beta's rename.",
    "invalidate_point": "**Part A is right.** beta: \u201c`retract_point`, "
                        "`invalidate_point` | 2 | → fields on `update_knowledge`\u201d, and "
                        "its retraction rationale is \u201cnot a separate verb\u201d. The "
                        "bridge follows the canonical collapse table's "
                        "`invalidate_point` → `supersede`.",
    "list_graphs": "**Part A is right; the bridge conflates two methods.** beta's "
                   "narrow-aliases row names `list_graphs` among the aliases absorbed by "
                   "`graph_overview` and deleted, while `graph_list` is the one sent to "
                   "`list_memory_graphs`. The canonical doc's \u201cdoes not merge\u201d "
                   "list keeps `list_graphs` \u2260 `graph_list` (raw DB names vs "
                   "control-plane rows).",
    "list_namespaces": "**Part A is right; the bridge is wrong.** beta's narrow-aliases row "
                       "names `list_namespaces` among the aliases absorbed by "
                       "`graph_overview`, and canonical R6 lists it there too.",
    "list_sources": "**Part A is right (beta governs).** beta's `list_sources` row is "
                    "explicit that it is **not discarded** and folds into **row 4 "
                    "`list_knowledge(kind='source')`**, and beta's `get_source_reliability` "
                    "row routes its reads via `list_sources`. The bridge sends it to "
                    "`graph_overview` \u2014 canonical R6's home for it \u2014 but beta "
                    "governs the surface, so Part A carries beta's destination.",
    "list_topics": "**Part A is right; the bridge is wrong.** beta's narrow-aliases row names "
                   "`list_topics` among the aliases absorbed by `graph_overview`, and "
                   "canonical R6 lists it there too.",
    "org_create": "**Genuinely contested — no owner ruling.** No approved doc places "
                  "organisation-account creation. `create_memory_graph` (beta row 29) "
                  "provisions a memory GRAPH, not an account, and beta's tenancy block "
                  "has no creation row. Part A's `UNBACKED` is the honest record; the "
                  "bridge asserts a destination no doc states.",
    "promote_point": "**Part A is right; the bridge is wrong — and the map is owner-approved, so "\
                     "it is reported here, not edited.** The owner-approved MCP list absorbs "\
                     "`promote_point` into `revise_knowledge`, whose beta successor is "\
                     "`update_knowledge`; beta names it a FIELD on that update, and promote's "\
                     "incident-operator cascade and approval gate ride with it. The bridge's "\
                     "`refresh_confidence` recomputes confidence and does not cover the "\
                     "cascade.",
    "set_point_baseline": "**Part A is right; the bridge is wrong — and the map is owner-approved, "\
                          "so it is reported here, not edited.** Same approved absorption as "\
                          "`promote_point`; beta names the starting belief a FIELD on "\
                          "`update_knowledge`. The bridge's `refresh_confidence` recomputes "\
                          "confidence, which is a different operation.",
    "query": "**Part A is right; the bridge is wrong.** beta: \u201c`query`, "
             "`paginated_query`, `query_points_by_tag` | 3 | → `list_knowledge`\u201d, and "
             "canonical R2 (`list_knowledge`) lists all three. beta row 4 is explicit "
             "that `list_knowledge` is the browse-and-filter method.",
    "paginated_query": "**Part A is right; the bridge is wrong.** beta: \u201c`query`, "
                       "`paginated_query`, `query_points_by_tag` | 3 | → "
                       "`list_knowledge`\u201d, and canonical R2 (`list_knowledge`) lists "
                       "all three. beta row 4 is explicit that `list_knowledge` is the "
                       "browse-and-filter method.",
    "query_points_by_tag": "**Part A is right; the bridge is wrong.** beta: \u201c`query`, "
                           "`paginated_query`, `query_points_by_tag` | 3 | → "
                           "`list_knowledge`\u201d, and canonical R2 (`list_knowledge`) "
                           "lists all three. beta row 4 is explicit that `list_knowledge` "
                           "is the browse-and-filter method.",
    # The journal: the bridge's `REMOVED` repeats the exact defect this table fixes.
    "checkpoint": "**Part A is right; the sibling carries the same defect.** beta files "
                  "`checkpoint` under \u201cNamed but not solved\u201d: live in the MCP "
                  "server, filed post-beta, unlisted \u2014 not dead. The bridge's "
                  "`REMOVED` (\u201cretires with no destination\u201d) reads it as "
                  "discarded.",
    "diary_read": "**Part A is right; the sibling carries the same defect.** beta files "
                  "`diary_read` under \u201cNamed but not solved\u201d \u2014 live and "
                  "unlisted, not dead. The bridge's `REMOVED` reads it as discarded.",
    "diary_write": "**Part A is right; the sibling carries the same defect.** beta files "
                   "`diary_write` under \u201cNamed but not solved\u201d \u2014 live and "
                   "unlisted, not dead. The bridge's `REMOVED` reads it as discarded.",
}


def _bridge_bindings() -> list[tuple[str, str, str]]:
    """(tool, sdk_method, destination) for every Part B row of the sibling bridge.

    Read from the sibling ARTIFACT, not restated: a copy here would be a second
    answer to \u201cwhere does the bridge send this tool\u201d and the two would drift.
    """
    out: list[tuple[str, str, str]] = []
    for line in BRIDGE_DOC.read_text(encoding="utf-8").splitlines():
        m = _BRIDGE_ROW.match(line)
        if not m:
            continue
        tool, sdk_cell, dest = (m.group(1), m.group(2).strip(),
                                m.group(3).strip())
        # The bridge appends ` ⚠️` to a destination its sibling records as WRONG. That
        # marker is RENDERING, not part of the destination, so strip it here — the
        # destination itself is what this comparison is about.
        dest = re.sub(r"\s*⚠️\s*$", "", dest).strip().strip("`")
        # `**none declared**` and `\u26a0\ufe0f **does not resolve**` are not methods.
        method = sdk_cell.strip("`") if (sdk_cell.startswith("`")
                                          and sdk_cell.endswith("`")) else ""
        out.append((tool, method, dest))
    return out


def _normalise_bridge(dest: str, method: str) -> str:
    """Put a bridge destination on the same vocabulary as a Part A Target.

    `REMOVED` is the bridge's word for `DISCARDED`, a `sdk:`/`tenancy:` prefix names
    the namespace a method lives in, and a destination equal to the method itself is
    the bridge's way of spelling an unchanged row — Part A now carries the method's own
    name as the target for those, so the two sides compare directly.
    """
    if dest == "REMOVED":
        return DISCARDED
    for ns in ("sdk:", "tenancy:"):
        if dest.startswith(ns):
            dest = dest[len(ns):]
    return dest


def _bridge_divergences(rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """(method, Part A destination, bridge destination, bridge tool) for every disagreement."""
    target_of = {r["name"]: _destination(r) for r in rows}
    out: list[tuple[str, str, str, str]] = []
    for tool, method, dest in _bridge_bindings():
        if not method or method not in target_of:
            continue
        if _normalise_bridge(dest, method) != target_of[method]:
            out.append((method, target_of[method], dest, tool))
    return sorted(out)


def _validate_rows(rows: list[dict], targets: list[str]) -> list[str]:
    """Row-level checks `_validate` cannot make without the rendered rows.

    `_validate` sees the map and the citations; it does not see which quote a row
    actually carries, which is where these three findings live.
    """
    errs: list[str] = []
    target_of = {r["name"]: _destination(r) for r in rows}
    real = set(targets)

    # 7. A row's Target must be what its quote names. The Basis legend says `stated`
    #    means "the cited quote names this method and gives its collapse, rename or
    #    deletion"; nothing checked that the quoted sentence named the DESTINATION, so
    #    a quote could carry a target it never mentions. The quote may name the Target
    #    directly, or name a method whose own row reaches the same Target (a collapse
    #    chain the docs state). Scope, each exclusion argued:
    #      * `derived` rows — by definition the doc names only a namespace/wildcard;
    #      * quotes from the canonical sketch — it names its own group names
    #        (`write_knowledge`, `recall_beliefs`), which beta renames to the Target,
    #        so the name the quote carries is never the beta name;
    #      * a quote whose destination is a backticked WILDCARD/range (`*_memory_graph*`),
    #        where the Target is chosen by the range rather than named.
    #    The row's OWN name is excluded from its own evidence: `target_of` maps it to the
    #    very target under test, so counting it would make every `stated` row vacuously
    #    true — the exact hole this check exists to close.
    wildcard = re.compile(r"`([^`]*)`")
    def _is_wildcard(quote: str) -> bool:
        # A backticked TOKEN that is a glob (`*_memory_graph*`, `org_*`) — a leading or
        # trailing `*`, which is the form these docs use. A bare `* in token` would
        # read `operator_action(**kwargs)` as a wildcard and silently exempt the row.
        return any(m.group(1).startswith("*") or m.group(1).endswith("*")
                   for m in wildcard.finditer(quote))
    candidates = set(target_of) | real
    for r in rows:
        # An UNCHANGED row's target is the method's own name, which its quote names by
        # definition — the check below excludes the row's own name, so it is skipped.
        if r["basis"] != "stated" or not r["target"] or r["target"] not in real:
            continue
        if r["disposition"] == UNCHANGED:
            continue
        if r["doc"] != BETA or _is_wildcard(r["quote"]):
            continue
        names = {c for c in candidates if _names(r["quote"], c)}
        names.discard(r["name"])
        if r["target"] in names or any(target_of.get(n) == r["target"] for n in names):
            continue
        errs.append(
            f"TARGET NOT IN EVIDENCE: {r['name']}'s quote never names its Target "
            f"{r['target']!r}, nor a method that reaches it — the row is asserted by "
            f"prose that says something else"
        )

    # 8. A disposition must cite the section that rules it. beta's "Named but not
    #    solved" and "Discarded — and why" are different findings; rendering the
    #    former as DISCARDED — Phase 2's signal to DELETE — is how a LIVE capability
    #    was mapped to deletion.
    beta = BETA_DOC.read_text(encoding="utf-8")
    for r in rows:
        heading = DISPOSITION_SECTION.get(r["disposition"])
        if not heading or r["doc"] != BETA:
            continue
        if heading not in beta:
            errs.append(f"DISPOSITION SECTION GONE: {heading!r} is not in {BETA.name} — "
                        f"{r['name']} cannot be validated")
            continue
        start, end = _section_bounds(beta, heading)
        at = beta.find(r["quote"])
        if not (start <= at < end):
            errs.append(
                f"DISPOSITION MISFILED: {r['name']} is {r['disposition']} but cites "
                f"outside {heading!r} — the cited text does not rule that disposition"
            )

    # 8b. A `DISCARDED` disposition must be supported by the row's CLAUSE, not only by
    #     its location. Sitting under "### Removed" is where the row IS; the rationale is
    #     what it SAYS. beta's w11 row sits under "### Removed" while naming the
    #     capability as *state* folded onto the general update — a FOLD, not a delete, and
    #     `DISCARDED` is Phase 2's signal to DELETE. The inverse is checked too, so a
    #     `CONTESTED` disposition cannot be applied to a region that says nothing about
    #     retention.
    for r in rows:
        if r["disposition"] not in (DISCARDED, CONTESTED) or r["doc"] != BETA:
            continue
        retained = _retention_clauses(r["quote"])
        if r["disposition"] == DISCARDED and retained:
            errs.append(
                f"DISCARDED BUT RETAINED: {r['name']} renders DISCARDED — Phase 2's "
                f"signal to DELETE — but its own clause says it is "
                f"{', '.join(repr(m) for m in retained)}. That is a fold, not a delete; "
                f"the disposition must be CONTESTED (or a target), not DISCARDED."
            )
        elif r["disposition"] == CONTESTED and not retained:
            errs.append(
                f"CONTESTED WITHOUT A RETENTION CLAUSE: {r['name']} renders CONTESTED "
                f"but its clause does not say the capability is retained — the "
                f"disposition must be DISCARDED (or a target)."
            )

    # 9. Cross-artifact. Every disagreement with the sibling bridge table carries a
    #    determination, and no determination is stale.
    diverged = {m for m, _t, _d, _tool in _bridge_divergences(rows)}
    for m in sorted(diverged - set(BRIDGE_DETERMINATION)):
        errs.append(f"BRIDGE DIVERGENCE UNDETERMINED: {m} diverges from "
                    f"{BRIDGE_DOC.name} but carries no determination")
    for m in sorted(set(BRIDGE_DETERMINATION) - diverged):
        errs.append(f"STALE BRIDGE DETERMINATION: {m} no longer diverges from "
                    f"{BRIDGE_DOC.name} — remove it")

    # 10. C6's fold record must agree with Part A. A record that drifted from the table
    #     it records is worse than none: it would claim a destination the surface does
    #     not carry.
    for name, (dest, _authority) in sorted(C6_FOLD.items()):
        if name not in target_of:
            errs.append(f"C6 FOLD STALE: {name} is not a public method")
        elif target_of[name] != dest:
            errs.append(
                f"C6 FOLD DISAGREES WITH PART A: {name} is {target_of[name]!r} in Part A "
                f"but the C6 record says {dest!r}"
            )
    return errs


def _cell(text: str) -> str:
    """A markdown table cell: escape pipes and collapse wrapping whitespace.

    A quote may span source line breaks (a markdown bullet that wraps), so its
    newlines and indentation are collapsed to single spaces — otherwise the cell
    would carry a ragged run of spaces and a raw newline would split the table row.
    """
    return re.sub(r"\s+", " ", text.replace("|", "\\|")).strip()


def render(rows: list[dict], targets: list[str], groups: dict[str, list[str]],
           findings: dict) -> str:
    n = len(rows)
    unchanged = sum(1 for r in rows if r["disposition"] == UNCHANGED)
    renamed = sum(1 for r in rows if r["disposition"] == RENAMED)
    discarded = sum(1 for r in rows if r["disposition"] == DISCARDED)
    unlisted = sum(1 for r in rows if r["disposition"] == UNLISTED)
    relocated = sum(1 for r in rows if r["disposition"] == RELOCATED)
    unbacked = sum(1 for r in rows if r["basis"] == BASIS_UNBACKED)
    contested = sum(1 for r in rows if r["basis"] == BASIS_CONTESTED)
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
        "**names a REGION of the doc — a table row, a bullet, or a paragraph — and the "
        "generator EXTRACTS that whole region verbatim**; the anchor only locates, so an "
        "anchor truncated at a line wrap or a mid-row cell still renders the region "
        "whole and cannot drop the clause that contradicts the row. A citation whose "
        "region cannot be resolved, or cannot be rendered whole, fails the build.",
        "",
        "**This is the SDK half of the rename table.** The MCP half (current tool → "
        "target tool) is `docs/product/bridge-table.md` (Phase 0.1), plus a sibling "
        "Phase 0.3b lane; nothing here restates it.",
        "",
        f"**The surface: {n} public methods on `TortoiseSDK` → {len(targets)} target "
        f"methods.** Every row carries **three orthogonal axes** — lifecycle, visibility "
        f"and evidence basis — plus an explicit delete instruction, and the vocabulary is "
        f"**declared in `config/disposition-vocabulary.yml`**, not restated here. "
        f"{renamed} of the {n} are renames to a target; **{unchanged}** are already "
        f"targets (unchanged); **{discarded}** are discarded with a rationale "
        f"(lifecycle `removed` · visibility `internal` · delete `delete`); "
        f"**{unlisted}** are live but **unlisted** (visibility `unlisted` — NOT deleted); "
        f"**{relocated}** is **relocated** out of the product SDK (visibility `internal`); "
        f"**{contested}** are **contested** (the docs name conflicting destinations); and "
        f"**{unbacked}** have no destination anywhere on the target "
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
        "A disposition is **three orthogonal axes plus a delete instruction** — never one "
        "flat value. The vocabulary is **declared in "
        "`config/disposition-vocabulary.yml`**; this legend names it and does not restate it:",
        "",
        "| Axis | Question | Values |",
        "|---|---|---|",
        f"| `Lifecycle` | {VOCAB['axes']['lifecycle']['question']} | "
        + ", ".join(f"`{v}`" for v in AXIS_VALUES["lifecycle"]) + " |",
        f"| `Visibility` | {VOCAB['axes']['visibility']['question']} | "
        + ", ".join(f"`{v}`" for v in AXIS_VALUES["visibility"]) + " |",
        f"| `Basis` | {VOCAB['axes']['basis']['question']} | "
        + ", ".join(f"`{v}`" for v in AXIS_VALUES["basis"]) + " |",
        f"| `Delete` | {VOCAB['delete_instruction']['question']} | "
        + ", ".join(f"`{v}`" for v in DELETE_VALUES) + " |",
        "",
        "The axes are independent, and that is the point: `DISCARDED` used to mean both "
        "\u201cnot on the public surface\u201d and \u201cDELETE this\u201d at once, so one "
        "word carried a visibility fact and a delete COMMAND. Now the visibility is "
        "`internal` **and** the delete instruction is `delete`, each in its own cell. A "
        "`—` in an axis cell means the doc states nothing for that axis (an `unbacked` "
        "or `contested` row).",
        "",
        "`Target` names the destination method; `—` means no destination is stated. The "
        "canonical inventory's group names are an **earlier sketch** "
        "(`revise_knowledge`, `stabilize_beliefs`, `write_knowledge`, `index_files`). The "
        "`Target` column always carries the **beta** target name (`update_knowledge`, "
        "`refresh_confidence`, `write_knowledge_batch`, "
        "`index_sources_from_directory`) — the canonical doc itself says beta governs where "
        "the two disagree, and records the renames.",
        "",
        "| # | Method | Source | Group | Target | Lifecycle | Visibility | Basis | Delete | Citation |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(rows, 1):
        cite = (
            "—" if r["basis"] == BASIS_UNBACKED
            else f"`{r['doc']}` — “{_cell(r['quote'])}”"
        )
        target = f"`{r['target']}`" if r["target"] else "\u2014"
        out.append(
            f"| {i} | `{r['name']}` | `sdk.py:{r['line']}` | {r['group']} | "
            f"{target} | {r['lifecycle'] or '—'} | {r['visibility'] or '—'} | "
            f"{r['basis']} | {r['delete'] or '—'} | {cite} |"
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
    no_def_dests = sum(1 for t in by_target if t in findings["no_def_set"])
    non_target_dests = _non_target_destinations(by_target)
    existing_dests = len(by_target) - no_def_dests - non_target_dests
    out += [
        f"| **total** | — | **{n}** |",
        "",
        f"Distinct destinations: **{len(by_target)}** — **{no_def_dests}** are target "
        f"methods with no `def` today (Part C1 lists all {len(findings['no_def'])} "
        f"Phase-2 methods), **{existing_dests}** are target methods that already exist, "
        f"and **{non_target_dests}** are the no-destination dispositions "
        "(" + " / ".join(f"`{t}`" for t in sorted(t for t in by_target
                                                 if t in NON_TARGET)) + ").",
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
            doc, anchor = TENSION_CITES[key]
            quote = _derive_region(_doc_text(doc), Region(anchor))
            out.append(f"| `{method}` | `{OVERRIDE[method][0]}` | {other} | "
                       f"`{doc}` — “{_cell(quote)}” |")
    else:
        out.append("None.")

    divergences = findings["bridge_divergences"]
    out += [
        "",
        "### C3b — cross-artifact divergences with the bridge table (Phase 0.1)",
        "",
        "This table and `docs/product/bridge-table.md` answer **different questions** — "
        "here, current SDK *method* → target *method*; there, current MCP *tool* → target "
        "*tool* — so a tool and the method it binds to can legitimately reach different "
        "targets, and where they do the divergence is recorded rather than harmonised by "
        "picking one. The sibling is named as the **dissenting source** on every row. The "
        "list is **computed from `bridge-table.md` at build time**; each row's "
        "determination is authored, because the reason IS the finding. The build fails if "
        "a divergence carries no determination, or a determination carries no divergence.",
        "",
        "| Method | Part A carries | `bridge-table.md` carries | Its current tool | "
        "Determination |",
        "|---|---|---|---|---|",
    ]
    for method, part_a, bridge_dest, tool in divergences:
        out.append(f"| `{method}` | `{part_a}` | `{bridge_dest}` | `{tool}` | "
                   f"{_cell(BRIDGE_DETERMINATION[method])} |")
    if not divergences:
        out.append("None — the two artifacts agree on every shared method.")

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
        doc, anchor = PHANTOM_CITES.get(key) or CITES[key]
        quote = _derive_region(_doc_text(doc), Region(anchor))
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
        "### C6 — the lifecycle/confidence fold (the former `CONTESTED` rows)",
        "",
        "beta's `### Removed` row filed these as *reachable* without NAMING a destination,",
        "so they were an open finding — and the project's",
        "own docs disagreed about the fold (the approved MCP list absorbed two into",
        "`revise_knowledge`; beta then SPLIT `revise_knowledge` into `update_knowledge` +",
        "`supersede_knowledge`, so the approved absorber's name no longer exists; the",
        "bridge asserted `refresh_confidence`, which §C3b records as wrong). The fold is",
        "the convergent shape — lifecycle/confidence **state as a field** on the general",
        "update, selected by a **filter** on the general list — and it agrees with the",
        "owner's recorded ruling that retraction “is a temporal FIELD on update, not a",
        "separate verb”. Each row names its AUTHORITY: two rest on the owner-approved MCP",
        "list's own absorption, two only on beta's row clause — which is what decides",
        "whether the item is still genuinely owner-open.",
        "",
        "| Method | Destination | Authority |",
        "|---|---|---|",
    ]
    for name in sorted(C6_FOLD):
        dest, authority = C6_FOLD[name]
        out.append(f"| `{name}` | `{dest}` | {_cell(authority)} |")

    contested_rows = [r for r in rows if r["basis"] == BASIS_CONTESTED]
    if contested_rows:
        out += [
            "",
            "#### C6a — rows the docs still contradict",
            "",
            "These rows have no destination the docs agree on; the conflicting citations",
            "are in Part C3. They render `basis: contested` and carry no destination, so",
            "Phase 2 cannot implement them without an owner ruling.",
            "",
            "| Method | Source | Why it is still contested |",
            "|---|---|---|",
        ]
        for r in contested_rows:
            retained = ", ".join(f"“{m}”" for m in _retention_clauses(r["quote"]))
            out.append(
                f"| `{r['name']}` | `sdk.py:{r['line']}` | Its row says "
                f"{retained or 'the capability is retained'} — no destination the docs "
                f"agree on. |"
            )

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
    """Destinations that are not a target name at all (UNCHANGED/DISCARDED/…)."""
    return sum(1 for t in by_target if t in NON_TARGET)


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
    errs += _validate_rows(rows, targets)
    if errs:
        print("SDK RENAME TABLE BUILD FAILURE — the map and the SDK disagree:",
              file=sys.stderr)
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        return 1

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
    print(f"  unchanged: {sum(1 for r in rows if r['disposition'] == UNCHANGED)}  "
          f"renamed: {sum(1 for r in rows if r['disposition'] == RENAMED)}  "
          f"discarded: {sum(1 for r in rows if r['disposition'] == DISCARDED)}  "
          f"unlisted: {sum(1 for r in rows if r['disposition'] == UNLISTED)}  "
          f"relocated: {sum(1 for r in rows if r['disposition'] == RELOCATED)}  "
          f"unbacked: {len(findings['unbacked'])}  "
          f"contested: {len(findings['contested'])}")
    print(f"  targets with NO def on TortoiseSDK ({len(findings['no_def'])}): "
          f"{', '.join(findings['no_def'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
