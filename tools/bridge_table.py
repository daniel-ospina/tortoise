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
from tortoise.tool_registry import TOOL_REGISTRY  # noqa: E402

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
    "tortoise_paginated_query": "list_knowledge",
    "tortoise_query_points_by_tag": "list_knowledge",
    "tortoise_search_sessions": "search_knowledge",
    "tortoise_suggest_entry_points": "search_knowledge",
    "tortoise_issue_insight": "search_knowledge",
    "tortoise_list_sources": "list_knowledge",
    "tortoise_list_topics": "list_knowledge",
    "tortoise_list_tags": "list_knowledge",
    "tortoise_list_namespaces": "list_knowledge",
    "tortoise_list_pointkinds": "list_knowledge",
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
    "tortoise_get_events": "poll_events",
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
    for entry in TOOL_REGISTRY:
        declared = getattr(entry, "sdk_method", "") or ""
        rows.append({
            "name": entry.name,
            "lineno": by_name.get(entry.name),
            "sdk_method": declared,
            # The only resolution test that matters: is there a real method?
            "resolves": bool(declared) and hasattr(TortoiseSDK, declared),
            "read_only": bool(getattr(entry.annotations, "readOnlyHint", False)),
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
    return errs


def _blockers(rows: list[dict], sdk_defs: dict[str, int]) -> tuple[list[dict], list[dict]]:
    """The findings this artifact exists to produce."""
    tenancy_targets = sorted(
        {d.split(":", 1)[1] for d in DESTINATION.values() if d.startswith("tenancy:")}
    )
    sdk_only_targets = sorted(
        {d.split(":", 1)[1] for d in DESTINATION.values() if d.startswith("sdk:")}
    )
    no_method = [
        {"what": m, "scope": "MCP", "why": "no `def` on TortoiseSDK"}
        for m in TARGET_MCP if m not in sdk_defs
    ] + [
        {"what": m, "scope": "tenancy", "why": "no `def` on TortoiseSDK"}
        for m in tenancy_targets if m not in sdk_defs
    ] + [
        {"what": m, "scope": "sdk-only", "why": "no `def` on TortoiseSDK"}
        for m in sdk_only_targets if m not in sdk_defs
    ]

    unresolved = [
        {"tool": r["name"], "declared": r["sdk_method"], "line": r["lineno"]}
        for r in rows
        if r["sdk_method"] and not r["resolves"]
    ]

    return no_method, unresolved


def render(rows: list[dict], sdk_defs: dict[str, int]) -> str:
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

    out = [
        "# Phase 0.1 — the bridge table",
        "",
        "**GENERATED — do not edit.** `uv run python tools/bridge_table.py`; verify with `--check`.",
        "",
        "Every `file:line` in this document is **read from the source at build time**, so it cannot",
        "drift from the code it cites. The destination map is data in the generator; every count",
        "below is arithmetic computed against the live registry. The generator **fails the build**",
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
        ok = "yes" if method in sdk_defs else "**no — Part C1**"
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
        "## Part B — every current tool and its single destination",
        "",
        "| # | Current tool | Source | SDK binding | Read-only | Destination |",
        "|---|---|---|---|---|---|",
    ]

    for i, r in enumerate(sorted(rows, key=lambda x: x["name"]), 1):
        src = f"`tool_registry.py:{r['lineno']}`" if r["lineno"] else "—"
        bind = f"`{r['sdk_method']}`" if r["sdk_method"] else "**none declared**"
        if r["sdk_method"] and not r["resolves"]:
            bind += " ⚠️ **does not resolve**"
        out.append(
            f"| {i} | `{r['name']}` | {src} | {bind} | "
            f"{'yes' if r['read_only'] else 'no'} | `{DESTINATION[r['name']]}` |"
        )

    out += [
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

    errs = _validate(rows)
    if errs:
        print("BRIDGE TABLE BUILD FAILURE — the map and the registry disagree:", file=sys.stderr)
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        return 1

    doc = render(rows, sdk_defs)

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != doc:
            print(f"DRIFT: {OUT} is stale. Run: uv run python tools/bridge_table.py", file=sys.stderr)
            return 1
        print(f"OK: {OUT} is current ({len(rows)} registry tools).")
        return 0

    OUT.write_text(doc, encoding="utf-8")
    no_method, _ = _blockers(rows, sdk_defs)
    print(f"wrote {OUT}")
    print(f"  registry tools: {len(rows)}")
    print(f"  target methods with NO def on TortoiseSDK ({len(no_method)}): "
          f"{', '.join(b['what'] for b in no_method) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
