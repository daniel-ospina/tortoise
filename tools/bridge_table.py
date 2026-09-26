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

import argparse
import ast
import re
import sys
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
    # ── Journal — filed post-beta, listed nowhere ────────────────────
    "tortoise_checkpoint": "REMOVED",
    "tortoise_diary_write": "REMOVED",
    "tortoise_diary_read": "REMOVED",
    # ── Onboarding and packs ────────────────────────────────────────
    "tortoise_onboarding_state": "REMOVED",
    "tortoise_onboarding_seed": "REMOVED",
    "tortoise_onboarding_demo_create": "REMOVED",
    "tortoise_onboarding_github_connect": "REMOVED",
    "tortoise_onboarding_github_index": "REMOVED",
    "tortoise_onboarding_github_status": "REMOVED",
    "tortoise_onboarding_session_recording": "REMOVED",
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

# Destinations the SIBLING SDK rename table (`docs/product/sdk-rename-table.md` §C3b, and
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


def render(rows: list[dict], sdk_defs: dict[str, int], cites: dict[str, dict]) -> str:
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
        "**(`docs/product/sdk-rename-table.md` §C3b, and its C6 fold record) records that**",
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
        "#### D1 — the citation corpus",
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
        "#### D2 — citations that do NOT name their row's destination",
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
        "#### D2b — rows whose support exists ONLY beyond the first clause",
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
        "#### D2c — destinations the sibling SDK rename table records as WRONG",
        "",
        "`docs/product/sdk-rename-table.md` reconciles the same surface this file maps, and its",
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
        "#### D3 — citations that name more than one target",
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

    errs = [*_validate(rows), *_citation_errors(cites)]
    if errs:
        print("BRIDGE TABLE BUILD FAILURE — the map and the registry disagree:", file=sys.stderr)
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        return 1

    doc = render(rows, sdk_defs, cites)

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
