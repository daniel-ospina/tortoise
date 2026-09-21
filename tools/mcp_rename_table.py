#!/usr/bin/env python3
"""Generate the Phase 0.3 MCP rename table for tortoise #4282.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT
---------------------------------------
The sibling Phase 0.1 artifact (`tools/bridge_table.py`) answers the implementer's
question — *which target surface absorbs this current tool?* — in 98 hand-mapped
rows. The first attempt at THAT artifact was hand-written prose and five review
passes each found more `file:line` citations that did not resolve. **Hand-written
line numbers and counts do not converge.** So, exactly as 0.1 does, every
citation here is READ FROM THE SOURCE at build time and every count is
ARITHMETIC computed from data against the live registry. If the data and the
registry disagree, this script FAILS — it never reconciles silently.

The question 0.3 answers is the CALLER's, and it is not the same question:

    "I call `tortoise_get_point` — what do I call now?"

Two answers matter, and both are in the table:

  1. **`Call instead today`** — the name that survives on the CURRENT surface,
     from PR #4031's `RETIRED_USE_INSTEAD`. A caller can migrate with it before
     the target surface exists. `**no replacement**` means the name is still
     live and needs no redirect.
  2. **`Destination (target surface)`** — the 0.1 destination, from the authored
     map in `tools/bridge_table.py`. This is the eventual name.

`tools/bridge_table.py` already holds the authored destination map, so this
generator IMPORTS it rather than keeping a second copy that can drift. Imported:
`TARGET_MCP`, `DESTINATION`, `NAMESPACES`, `MCP_BACKING`, `DISCRIMINATORS`,
`_registry_rows`, and `_validate` (the map-vs-registry integrity gate).

USAGE
    uv run python tools/mcp_rename_table.py            # write the doc
    uv run python tools/mcp_rename_table.py --check    # verify only, non-zero on drift

WHAT THIS ARTIFACT ADDS OVER 0.1 (#4282)
----------------------------------------
  1. A per-tool **client migration row**: the surviving name to call today, or
     an explicit "no replacement".
  2. An **agreement check** against PR #4031's `RETIRED_USE_INSTEAD`, name by
     name. That map is a SNAPSHOT (the PR is unmerged), so it is data in this
     generator with the branch + commit it was read from. A name whose 0.1
     destination differs from the destination of its #4031 redirect is a
     **FINDING** and is reported as such — never quietly reconciled.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import bridge_table  # noqa: E402
from tools.bridge_table import (  # noqa: E402
    DESTINATION,
    NAMESPACES,
    TARGET_MCP,
    _registry_rows,
)

# The two destination namespaces (`tenancy:` = SDK/REST only, `sdk:` = a
# builder-only SDK method that is not on the MCP). Read from the 0.1 map's own
# tuple rather than re-typing the prefixes here, so the two artifacts cannot
# disagree about what `tenancy:` means.
TENANCY_NS = next(n for n in NAMESPACES if n.startswith("tenancy"))
SDK_NS = next(n for n in NAMESPACES if n.startswith("sdk"))

OUT = ROOT / "docs" / "product" / "mcp-rename-table.md"

# ─────────────────────────────────────────────────────────────────────
# AUTHORED SNAPSHOT — PR #4031's `RETIRED_USE_INSTEAD`, read from
#     git show origin/feat/3883-retired-name-warning:tortoise/tool_registry.py
# It is NOT importable: the PR is unmerged, so it does not exist on main. It is
# pinned with the branch + commit it was read from, and a test pins the same 16
# entries as LITERALS (never by importing this constant — an assertion whose
# expected value is imported from the thing under test is not an assertion).
#
# Value shape: old name → the surviving CURRENT-surface call to use instead.
# A leading `tortoise_*` token in the value is the redirect TARGET whose 0.1
# destination must agree with the old name's 0.1 destination (Part C).
# ─────────────────────────────────────────────────────────────────────
PR4031_REF = "origin/feat/3883-retired-name-warning @ c01ad93b569e514e94d0d813c0216de9cb746d3b"

# Where the disagreements below are tracked. The generator REPORTS a disagreement and
# refuses to resolve it (picking a target is a design decision), so the finding needs a
# home that outlives this document. Pinned as data so the doc cannot keep pointing at a
# number this file no longer names; that the issue still exists is checked online, not
# here — see the test's residual note.
FINDINGS_ISSUE = "tortoise #4475"

RETIRED_USE_INSTEAD: dict[str, str] = {
    "tortoise_get_point": 'tortoise_get(id, type="point")',
    "tortoise_get_entity": 'tortoise_get(id, type="entity")',
    "tortoise_get_events": 'tortoise_get(None, type="events")',
    "tortoise_get_operator": 'tortoise_get(id, type="operator")',
    "tortoise_get_governance": 'tortoise_get(id, type="governance")',
    "tortoise_list_pointkinds": 'tortoise_overview(section="pointkinds")',
    "tortoise_list_tags": 'tortoise_overview(section="tags")',
    "tortoise_list_sources": 'tortoise_overview(section="sources")',
    "tortoise_taxonomy": 'tortoise_overview(section="taxonomy")',
    "tortoise_health": 'tortoise_overview(section="health")',
    "tortoise_status": 'tortoise_overview(section="status")',
    "tortoise_stale": 'tortoise_overview(section="stale")',
    "tortoise_paginated_query": "tortoise_query(offset=..., limit=...)",
    "tortoise_query_points_by_tag": "tortoise_query(tag=...)",
    "tortoise_index_sessions": "tortoise_index_files(directory)",
    "tortoise_ingest_corpus": "tortoise_index_files(directory)",
}

# Part A's `Retirement` cell. Two states, both computed from the snapshot above —
# never authored per row. On main, PR #4031 is NOT merged, so these describe the
# PLAN: a name in the snapshot is served through the warning shim; every other
# name simply disappears when the target surface lands (no shim).
RETIRE_WARN = "warning shim"
RETIRE_ABSENT = "absent"

# Part A's `Call instead today` cell for a name with no redirect.
NO_REPLACEMENT = "**no replacement**"

# The redirect target is the first `tortoise_*` token in the replacement
# expression. `tortoise_get(id, type="point")` → `tortoise_get`.
_TARGET_RE = re.compile(r"(tortoise_[a-z0-9_]+)")


def _redirect_target(expr: str) -> str | None:
    """The surviving tool name a `RETIRED_USE_INSTEAD` call names, or None.

    None is a FAILURE, not a silent blank: a replacement that names no
    `tortoise_*` tool cannot be checked for agreement, and an uncheckable row
    is exactly the kind of free claim this generator exists to remove.
    """
    m = _TARGET_RE.match(expr)
    return m.group(1) if m else None


def _rows(registry_rows: list[dict] | None = None) -> list[dict]:
    """One migration row per current registry tool, sorted by name.

    The source line is read from the AST by `_registry_rows()` — it is a fact
    about `tool_registry.py`, not a number a human typed.

    `registry_rows` is a parameter so `main()` can validate the raw rows BEFORE
    this function indexes `DESTINATION`: an unmapped tool must be reported as a
    finding, never crash the build with a `KeyError`.
    """
    out = []
    for r in (registry_rows if registry_rows is not None else _registry_rows()):
        expr = RETIRED_USE_INSTEAD.get(r["name"])
        out.append({
            "name": r["name"],
            "lineno": r["lineno"],
            "destination": DESTINATION[r["name"]],
            "instead": expr,
            "retirement": RETIRE_WARN if expr is not None else RETIRE_ABSENT,
        })
    return sorted(out, key=lambda r: r["name"])


def _agreement(rows: list[dict]) -> list[dict]:
    """Per #4031 entry: does 0.1 send the old name and its redirect to the SAME target?

    The redirect is a CURRENT-surface call; the 0.1 map is the TARGET surface. A
    caller who follows the redirect lands on `tortoise_overview`, and the 0.1
    map says `tortoise_overview` → `graph_overview`. If the 0.1 map sends the
    original name somewhere ELSE, the two artifacts disagree about which target
    absorbs it, and that is a finding — not something for this generator to
    settle.
    """
    out = []
    for name in sorted(RETIRED_USE_INSTEAD):
        expr = RETIRED_USE_INSTEAD[name]
        target = _redirect_target(expr)
        d_old = DESTINATION.get(name)
        d_new = DESTINATION.get(target) if target else None
        out.append({
            "name": name,
            "destination": d_old,
            "redirect": target,
            "redirect_destination": d_new,
            "agrees": d_old is not None and d_old == d_new,
        })
    return out


def _validate(rows: list[dict]) -> list[str]:
    """Fail loudly. Never reconcile silently — a mismatch IS the finding.

    The map-vs-registry half is `tools.bridge_table._validate`, IMPORTED rather
    than re-implemented so the two artifacts cannot disagree about what
    "the map matches the registry" means. The snapshot half is this artifact's
    own: a redirect whose target is not a registry tool, or has no 0.1
    destination, cannot be rendered as an agreement row.
    """
    errs = list(bridge_table._validate(rows))
    reg = {r["name"] for r in rows}

    for name in sorted(RETIRED_USE_INSTEAD):
        if name not in reg:
            errs.append(
                f"RETIRED_USE_INSTEAD key is not a registry tool: {name} — the snapshot "
                "has drifted from the registry it describes"
            )
        target = _redirect_target(RETIRED_USE_INSTEAD[name])
        if target is None:
            errs.append(
                f"RETIRED_USE_INSTEAD[{name!r}] names no `tortoise_*` tool: "
                f"{RETIRED_USE_INSTEAD[name]!r} — its agreement cannot be checked"
            )
            continue
        if target not in reg:
            errs.append(
                f"RETIRED_USE_INSTEAD[{name!r}] redirects to {target!r}, "
                "which is not a registry tool"
            )
        elif DESTINATION.get(target) is None:
            errs.append(
                f"RETIRED_USE_INSTEAD[{name!r}] redirects to {target!r}, "
                "which has no 0.1 destination"
            )
    return errs


def _rendezvous_hint(rows: list[dict]) -> str | None:
    """If the map broke only because #4031 moved names out of TOOL_REGISTRY, say so.

    A red that reads like a bug gets worked around; a red that reads like a
    rendezvous gets acted on. `_registry_rows()` reads `TOOL_REGISTRY`, and
    PR #4031 moves every retired name into `RETIRED_TOOL_REGISTRY` — so landing
    #4031 first turns 16 map keys STALE and this generator fails. That failure
    is the two lanes meeting, and the fix is to read the retired names too,
    not to delete their rows.
    """
    reg = {r["name"] for r in rows}
    stale = set(DESTINATION) - reg
    if stale and stale <= set(RETIRED_USE_INSTEAD):
        return (
            f"RENDEZVOUS WITH PR #4031: the {len(stale)} stale map keys are exactly the "
            "names #4031 moves out of TOOL_REGISTRY into RETIRED_TOOL_REGISTRY. Landing "
            "#4031 first makes this generator fail by design. Fix by reading the retired "
            "names from RETIRED_TOOL_REGISTRY as well (so the rename table still covers "
            "them), not by deleting their rows. See tools/mcp_rename_table.py."
        )
    return None


def render(rows: list[dict]) -> str:
    agree_rows = _agreement(rows)

    by_dest: dict[str, int] = {}
    for r in rows:
        by_dest[r["destination"]] = by_dest.get(r["destination"], 0) + 1
    total = len(rows)
    destinations = len(by_dest)
    removed = by_dest.get("REMOVED", 0)
    tenancy = sum(n for d, n in by_dest.items() if d.startswith(TENANCY_NS))
    sdk_only = sum(n for d, n in by_dest.items() if d.startswith(SDK_NS))
    on_mcp = total - removed - tenancy - sdk_only
    warn = sum(1 for r in rows if r["retirement"] == RETIRE_WARN)
    absent = total - warn
    agree = sum(1 for a in agree_rows if a["agrees"])
    disagree = len(agree_rows) - agree

    out = [
        "# Phase 0.3 — the MCP rename table",
        "",
        "**GENERATED — do not edit.** `uv run python tools/mcp_rename_table.py`; "
        "verify with `--check`.",
        "",
        "**The caller's question.** Phase 0.1 answers *which target absorbs this tool?* for",
        "implementers. This table answers *I call `tortoise_get_point` — what do I call now?*",
        "for callers. Both answers are one row per current MCP tool: the name to call **today**,",
        "and the **target-surface** name that will replace it.",
        "",
        "Every `file:line` below is **read from the source at build time**, so a citation cannot",
        "drift. Every count is **arithmetic** computed from the authored maps against the live",
        "registry. The generator **fails the build** if those disagree — a mismatch is a finding,",
        "not something to reconcile silently.",
        "",
        "---",
        "",
        "## Part A — one migration row per current MCP tool",
        "",
        "| # | Current tool | Source | Call instead today | Destination (target surface) | Retirement |",
        "|---|---|---|---|---|---|",
    ]

    for i, r in enumerate(rows, 1):
        src = f"`tool_registry.py:{r['lineno']}`" if r["lineno"] else "—"
        instead = f"`{r['instead']}`" if r["instead"] else NO_REPLACEMENT
        out.append(
            f"| {i} | `{r['name']}` | {src} | {instead} | "
            f"`{r['destination']}` | {r['retirement']} |"
        )

    out += [
        "",
        f"**{total} current MCP tools, {destinations} destinations.** "
        f"{warn} retire **with a warning shim**; the other **{absent}** are simply absent — no shim.",
        "",
        "**`Retirement` is a forecast, not today's behaviour.** PR #4031 "
        "(`feat/3883-retired-name-warning`) is **not merged**, so on `main` every name in",
        "this table still resolves. The column says what happens to the OLD name once that",
        "plan lands: a `warning shim` name is still served and warns; an `absent` name simply",
        f"disappears. The snapshot was read from `{PR4031_REF}`.",
        "",
        "### Destination counts",
        "",
        "| Destination | Count |",
        "|---|---|",
    ]
    for dest, n in sorted(by_dest.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"| `{dest}` | {n} |")
    out += [
        f"| **total** | **{total}** |",
        "",
        f"{total} current tools → {destinations} destinations: **{on_mcp}** absorbed into the "
        f"**{len(TARGET_MCP)}** MCP targets, **{sdk_only}** into a builder-only SDK method "
        f"(not on the MCP), **{tenancy}** tenancy (SDK/REST only), **{removed}** removed.",
        "",
        "---",
        "",
        "## Part B — agreement with PR #4031's `RETIRED_USE_INSTEAD`",
        "",
        "PR #4031 encodes the *current-surface* redirects. This table encodes the *target-surface*",
        "destinations. They answer different questions about the same 16 names, and they must not",
        "contradict each other: a caller who follows a #4031 redirect to `tortoise_overview` lands",
        "on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two",
        "artifacts disagree about which target absorbs it.",
        "",
        "**A disagreement is a FINDING.** The generator reports it and does not reconcile it —",
        "resolving *which* destination is right is a design decision, not a build step.",
        "",
        "| Current tool | 0.1 destination | #4031 says call | That name's 0.1 destination | Verdict |",
        "|---|---|---|---|---|",
    ]
    for a in agree_rows:
        verdict = "AGREE" if a["agrees"] else "**DISAGREE**"
        rd = f"`{a['redirect_destination']}`" if a["redirect_destination"] else "—"
        out.append(
            f"| `{a['name']}` | `{a['destination']}` | `{RETIRED_USE_INSTEAD[a['name']]}` | "
            f"{rd} | {verdict} |"
        )
    out += [
        "",
        f"**{agree} of {len(agree_rows)} agree; {disagree} disagree.**",
        "",
        "### B1 — the disagreements",
        "",
    ]
    conflict = [a for a in agree_rows if not a["agrees"]]
    if conflict:
        out += ["| Current tool | 0.1 destination | #4031 redirect | Redirect's 0.1 destination |",
                "|---|---|---|---|"]
        for a in conflict:
            out.append(
                f"| `{a['name']}` | `{a['destination']}` | "
                f"`{RETIRED_USE_INSTEAD[a['name']]}` | `{a['redirect_destination']}` |"
            )
        out += [
            "",
            f"**{disagree} findings.** Each row is a name whose absorbing target is stated two ways:",
            "the 0.1 map's destination, and the destination of the name #4031 tells the caller to",
            "use instead. Both cannot be right for the caller. This generator does not resolve them;",
            f"they are tracked as {FINDINGS_ISSUE} for the #4282 controller to decide.",
        ]
    else:
        out.append("None — every #4031 redirect and the 0.1 map agree.")

    out += [
        "",
        "---",
        "",
        "## Reproduce",
        "",
        "```bash",
        "uv run python tools/mcp_rename_table.py          # regenerate this file",
        "uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift",
        "```",
        "",
    ]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="verify only; do not write")
    args = ap.parse_args()

    raw = _registry_rows()

    errs = _validate(raw)
    if errs:
        print(
            "MCP RENAME TABLE BUILD FAILURE — the rename map and the registry disagree:",
            file=sys.stderr,
        )
        for e in errs:
            print(f"  • {e}", file=sys.stderr)
        hint = _rendezvous_hint(raw)
        if hint:
            print(f"\n  → {hint}", file=sys.stderr)
        return 1

    rows = _rows(raw)
    doc = render(rows)

    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != doc:
            print(
                f"DRIFT: {OUT} is stale. Run: uv run python tools/mcp_rename_table.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {OUT} is current ({len(rows)} tools).")
        return 0

    OUT.write_text(doc, encoding="utf-8")
    agree = _agreement(rows)
    conflicts = [a["name"] for a in agree if not a["agrees"]]
    print(f"wrote {OUT}")
    print(f"  current tools: {len(rows)}")
    print(f"  #4031 redirects: {len(agree)} ({len(agree) - len(conflicts)} agree, "
          f"{len(conflicts)} disagree)")
    if conflicts:
        print(f"  DISAGREEMENTS (findings): {', '.join(conflicts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
