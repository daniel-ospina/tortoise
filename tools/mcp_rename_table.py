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
     from `tortoise.tool_registry.RETIRED_USE_INSTEAD`. A caller can migrate
     with it before the target surface exists. `**no replacement**` means the
     name is still live and needs no redirect.
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
  2. An **agreement check** against `RETIRED_USE_INSTEAD`, name by name. The map
     is read LIVE from the registry (PR #4031 merged as `e3bb78a14`), so it
     cannot describe a plan that landed differently. A name whose 0.1 destination
     differs from the destination of its redirect is a **FINDING**, reported and
     never quietly reconciled.
  3. A **maximality guard** over the registry prose this artifact quotes. A claim
     nothing quotes is a claim nothing can check, and a quote cut off mid-clause
     drops exactly the clause that can contradict the row it backs — `quote in
     text` cannot see it, because a truncated prefix is still a substring. The
     rule shape (`_maximal`) is `tools/sdk_rename_table.py`'s and
     `tools/bridge_table.py`'s, deliberately not a third rule.
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
from tortoise.tool_registry import (  # noqa: E402
    RETIRED_TOOL_REGISTRY,
    RETIRED_USE_INSTEAD,
    TOOL_REGISTRY,
)

# The two destination namespaces (`tenancy:` = SDK/REST only, `sdk:` = a
# builder-only SDK method that is not on the MCP). Read from the 0.1 map's own
# tuple rather than re-typing the prefixes here, so the two artifacts cannot
# disagree about what `tenancy:` means.
TENANCY_NS = next(n for n in NAMESPACES if n.startswith("tenancy"))
SDK_NS = next(n for n in NAMESPACES if n.startswith("sdk"))

OUT = ROOT / "docs" / "product" / "mcp-rename-table.md"

# ─────────────────────────────────────────────────────────────────────
# LIVE, not a snapshot. PR #4031 MERGED as e3bb78a14 (2026-09-21T17:06:36Z), so
# `RETIRED_USE_INSTEAD` exists on `main` and is read from the registry itself.
#
# The previous revision pinned a copy of the unmerged branch's map. Main's landed map
# then differed in 6 of 16 entries — most importantly it retires `tortoise_get` and
# KEEPS `tortoise_get_entity`, the reverse of the pinned copy, because retiring the
# pair the other way sends every caller of `get_entity` to a name that is itself
# retired — the churn loop the registry's own owner-decision comment names. A pinned
# copy of a map that lives in this repo can only go stale; read it.
#
# Value shape: old name → the surviving CURRENT-surface call to use instead.
# A leading `tortoise_*` token in the value is the redirect TARGET whose 0.1
# destination must agree with the old name's 0.1 destination (Part B).
# ─────────────────────────────────────────────────────────────────────

# The retirement is a MERGED behaviour, so its provenance is ONE fact in ONE place.
# Every rendered mention of the PR and the commit reads these; a second typed copy is
# a claim that can drift with the suite green.
PR_NUMBER = 4031
PR_MERGE_SHA = "e3bb78a14"
PR_MERGED_DATE = "2026-09-21"

# Part A's `Retirement` cell. Two states, both computed from the live map above —
# never authored per row. A name in the map is served through the warning shim;
# every other name simply disappears when the target surface lands (no shim).
RETIRE_WARN = "warning shim"
RETIRE_ABSENT = "absent"

# Part A's `Call instead today` cell for a name with no redirect.
NO_REPLACEMENT = "**no replacement**"

# The redirect target is the first `tortoise_*` token in the replacement
# expression. `tortoise_get(id, type="point")` → `tortoise_get`.
_TARGET_RE = re.compile(r"(tortoise_[a-z0-9_]+)")

# ─────────────────────────────────────────────────────────────────────
# THE CITATIONS. The `Retirement` prose asserts what the registry DOES with a
# retired name, and the direction of the `get` retirement is an owner decision
# recorded in the registry's own comment. Both are claims ABOUT the registry, and
# a claim that is not quoted is a claim nothing can check. Each is quoted
# VERBATIM from the registry's comment prose and MAXIMALLY: a read that stops
# mid-clause drops exactly the clause that can contradict the row it backs, and
# `quote in text` cannot see it, because a truncated prefix is still a substring.
# ─────────────────────────────────────────────────────────────────────
REGISTRY_SRC = ROOT / "tortoise" / "tool_registry.py"

# key -> (source document, the quote). The quotes are TYPED evidence — the whole
# point is that a change in the source is detectable, so they are not derived from
# the thing they quote.
CITES: dict[str, tuple[str, str]] = {
    "retired_behaviour": (
        "tortoise/tool_registry.py",
        "A name in this mapping is RETIRED: it is not in TOOL_REGISTRY, so it is not\n"
        "registered as an MCP tool and never appears in `tools/list`. It is NOT gone —\n"
        "`_RetiredToolTransform` in mcp_server.py resolves it on `get_tool` and serves\n"
        "a shim that answers exactly as the live tool did AND warns the caller, naming\n"
        "the replacement. That is the #3836 (b) decision: a retired name keeps working\n"
        "and tells us who still calls it.",
    ),
    "owner_decision": (
        "tortoise/tool_registry.py",
        "⚠ This direction is an OWNER DECISION, not an implementation preference:\n"
        "`docs/product/canonical-mcp-tools.md` (approved, approval_pr 4120) rules\n"
        "that `tortoise_get_entity` must NOT be retired and that the map must\n"
        "retire `tortoise_get` in its place. Retiring the pair the other way sends\n"
        "every caller of `get_entity` to a name that is itself retired — a churn\n"
        "loop — which is why the pointers below name `tortoise_get_entity`.",
    ),
}

# A quote that stops mid-clause is exactly what this rule rejects. Boundaries: a
# cell/row boundary (`|`), a line end, or the document end.
#
# A SENTENCE end is deliberately NOT a boundary. `… \u2192 `manage_source_trust`.` ends
# at a full stop while the NEXT sentence ("…; reads via `list_sources`") continues the
# claim — a sentence boundary is exactly where the contradictory clause begins, so it
# cannot be a safe stopping point. This is the tightened form of
# `tools/sdk_rename_table.py::_maximal`, and the same rule shape the task specifies
# (cell/row boundary, line end, document end — no sentence end).
_COMMENT_LINE = re.compile(r"^\s*#\s?(.*)$")


def _maximal(quote: str, text: str) -> bool:
    """Is `quote` a maximal region of `text` — not a right-truncation of one?

    True only when the match ends at a table-cell/row boundary (`|`), a line end, or
    the end of the document. A quote that stops anywhere else — including at a
    sentence end, where the next sentence can contradict it — is rejected.
    """
    idx = text.find(quote)
    if idx < 0:
        return True  # absent text is CITATION DRIFT, reported by its own check
    after = text[idx + len(quote):]
    return after == "" or after.startswith("\n") or quote.endswith("|")


def _registry_comment_text() -> str:
    """The registry's comment prose with the `#` markers stripped, as one text.

    The citations quote the registry's OWN comments, so the text they are checked
    against has to be the comment content — against the raw file a quote of a
    comment never matches and the guard would be vacuous. Non-comment lines become
    empty lines, so a contiguous comment region stays contiguous.
    """
    out: list[str] = []
    for line in REGISTRY_SRC.read_text(encoding="utf-8").splitlines():
        m = _COMMENT_LINE.match(line)
        out.append(m.group(1) if m else "")
    return "\n".join(out)


def _citation_errors() -> list[str]:
    """Fail the build on a DRIFTED or TRUNCATED citation — never on a disagreement.

    A disagreement between the map and the registry is a FINDING to report. A
    citation that is missing or truncated is a defect in the EVIDENCE itself, and
    evidence that can be edited to agree with the row is worse than no evidence.
    """
    text = _registry_comment_text()
    errs: list[str] = []
    # Non-vacuity: a guard that resolves nothing cannot fail, and would read green.
    if not CITES:
        errs.append("NO citation is declared — the citation guard is UNARMED")
    for key, (doc, quote) in sorted(CITES.items()):
        if not quote.strip():
            errs.append(
                f"EMPTY CITATION: {key} carries no quote — an empty string is a "
                "substring of every document, so the guard passes while the claim "
                "is backed by nothing"
            )
        elif quote not in text:
            errs.append(f"CITATION DRIFT: {key} quotes {doc} but that text is gone")
        elif not _maximal(quote, text):
            after = text[text.find(quote) + len(quote):][:60]
            errs.append(
                f"TRUNCATED CITATION: {key}'s quote stops mid-clause — the registry "
                f"continues {after!r}, which can contradict the row this quote backs. "
                "A quote must end at a cell/row boundary (`|`), a line end, or the "
                "document end — a sentence end is NOT enough, because the next "
                "sentence is where a contradiction begins."
            )
    return errs


def _redirect_target(expr: str) -> str | None:
    """The surviving tool name a `RETIRED_USE_INSTEAD` call names, or None.

    None is a FAILURE, not a silent blank: a replacement that names no
    `tortoise_*` tool cannot be checked for agreement, and an uncheckable row
    is exactly the kind of free claim this generator exists to remove.
    """
    m = _TARGET_RE.match(expr)
    return m.group(1) if m else None


def _rows(registry_rows: list[dict] | None = None) -> list[dict]:
    """One migration row per served registry tool, sorted by name.

    The source line is read from the AST by `_registry_rows()` — it is a fact
    about `tool_registry.py`, not a number a human typed. `_registry_rows()` walks
    BOTH registries, so a name retired by #4031 keeps its row: the warning shim
    still serves it, so it is still a name a caller can call.

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
    """Per redirect entry: does 0.1 send the old name and its target to the SAME place?

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
    "the map matches the registry" means. The redirect half is this artifact's
    own: a redirect whose target is not a registry tool, or has no 0.1
    destination, cannot be rendered as an agreement row. The citation half is the
    maximality guard over the registry prose this artifact quotes.
    """
    errs = list(bridge_table._validate(rows))
    reg = {r["name"] for r in rows}

    for name in sorted(RETIRED_USE_INSTEAD):
        if name not in reg:
            errs.append(
                f"RETIRED_USE_INSTEAD key is not a registry tool: {name} — the map "
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
    errs += _citation_errors()
    return errs


def _rendezvous_hint(rows: list[dict]) -> str | None:
    """If the map broke because names moved between the registries, say so.

    A red that reads like a bug gets worked around; a red that reads like a
    rendezvous gets acted on. The real structure is the registry's own split:
    a retired name moves OUT of `TOOL_REGISTRY` and INTO `RETIRED_TOOL_REGISTRY`.
    A reader that walks only `TOOL_REGISTRY` therefore turns every map key for a
    retired name STALE and fails. The predicate is the split itself — the stale
    keys are EXACTLY the retired set and none of them is still current — so it
    holds no matter WHICH names #4031 moved, including a direction (`tortoise_get`
    retired, `tortoise_get_entity` kept) that no pinned copy of the old map names.
    Predicating on the map (`stale <= set(RETIRED_USE_INSTEAD)`) would instead
    depend on the very data being validated, and missed exactly that case.
    """
    reg = {r["name"] for r in rows}
    stale = set(DESTINATION) - reg
    retired = {t.name for t in RETIRED_TOOL_REGISTRY}
    current = {t.name for t in TOOL_REGISTRY}
    if stale and stale == retired and not (stale & current):
        return (
            f"RENDEZVOUS WITH PR #{PR_NUMBER}: the {len(stale)} stale map keys are exactly "
            "the names moved out of TOOL_REGISTRY into RETIRED_TOOL_REGISTRY. A reader "
            "that walks only TOOL_REGISTRY fails by design here. Fix by reading the "
            "retired names from RETIRED_TOOL_REGISTRY as well (so the rename table still "
            "covers them), not by deleting their rows. See tools/mcp_rename_table.py."
        )
    return None


def _quote_lines(key: str) -> list[str]:
    """Render a citation as blockquote lines, verbatim."""
    return [f"> {line}" for line in CITES[key][1].splitlines()]


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
        "for callers. Both answers are one row per served MCP tool: the name to call **today**,",
        "and the **target-surface** name that will replace it.",
        "",
        "Every `file:line` below is **read from the source at build time**, so a citation cannot",
        "drift. Every count is **arithmetic** computed from the authored maps against the live",
        "registry. The generator **fails the build** if those disagree — a mismatch is a finding,",
        "not something to reconcile silently.",
        "",
        "---",
        "",
        "## Part A — one migration row per served MCP tool",
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
        f"**{total} served MCP tools, {destinations} destinations.** "
        f"{warn} retire **with a warning shim**; the other **{absent}** are simply absent — no shim.",
        "",
        f"**`Retirement` is today's behaviour, not a forecast.** PR #{PR_NUMBER} MERGED as "
        f"`{PR_MERGE_SHA}` ({PR_MERGED_DATE}), so the {warn} `warning shim` names are",
        "retired ON `main` and this column is what a caller experiences now. The map is "
        "read live from `tortoise.tool_registry.RETIRED_USE_INSTEAD` rather than pinned, so "
        "it cannot describe a plan that has since landed differently.",
        "",
        "The registry states what retiring a name DOES — quoted verbatim and in full:",
        "",
        *_quote_lines("retired_behaviour"),
        "",
        "The direction of the `get` retirement is an owner decision, recorded in the "
        "registry's own comment — quoted verbatim and in full:",
        "",
        *_quote_lines("owner_decision"),
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
        f"{total} served tools → {destinations} destinations: **{on_mcp}** absorbed into the "
        f"**{len(TARGET_MCP)}** MCP targets, **{sdk_only}** into a builder-only SDK method "
        f"(not on the MCP), **{tenancy}** tenancy (SDK/REST only), **{removed}** removed.",
        "",
        "---",
        "",
        f"## Part B — agreement with PR #{PR_NUMBER}'s `RETIRED_USE_INSTEAD`",
        "",
        f"PR #{PR_NUMBER} encodes the *current-surface* redirects. This table encodes the *target-surface*",
        f"destinations. They answer different questions about the same {len(RETIRED_USE_INSTEAD)} "
        "names, and they must not",
        "contradict each other: a caller who follows a redirect to `tortoise_overview` lands",
        "on `graph_overview`, so if the 0.1 map sends the ORIGINAL name somewhere else the two",
        "artifacts disagree about which target absorbs it.",
        "",
        "**A disagreement is a FINDING.** The generator reports it and does not reconcile it —",
        "resolving *which* destination is right is a design decision, not a build step.",
        "",
        f"| Current tool | 0.1 destination | PR #{PR_NUMBER} says call | That name's 0.1 destination | Verdict |",
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
        out += [f"| Current tool | 0.1 destination | PR #{PR_NUMBER} redirect | Redirect's 0.1 destination |",
                "|---|---|---|---|"]
        for a in conflict:
            out.append(
                f"| `{a['name']}` | `{a['destination']}` | "
                f"`{RETIRED_USE_INSTEAD[a['name']]}` | `{a['redirect_destination']}` |"
            )
        out += [
            "",
            f"**{disagree} findings.** Each row is a name whose absorbing target is stated two ways:",
            "the 0.1 map's destination, and the destination of the name the redirect tells the",
            "caller to use instead. Both cannot be right for the caller. This generator does not",
            "resolve them; each is a finding for the #4282 controller to decide before landing.",
        ]
    else:
        # The section is KEPT as an explicit zero, not removed: a findings list that
        # is absent is indistinguishable from one nobody wrote, and the tests read
        # this section's shape.
        out.append(
            f"**None — 0 findings.** The 0.1 map and the redirect agree on the target for all "
            f"{len(agree_rows)} names. This section is kept, not deleted: an explicit empty "
            "finding list stays machine-visible, and a section that silently vanished would "
            "hide the difference between *no disagreements* and *nobody looked*."
        )

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
    print(f"  served tools: {len(rows)}")
    print(f"  redirects: {len(agree)} ({len(agree) - len(conflicts)} agree, "
          f"{len(conflicts)} disagree)")
    if conflicts:
        print(f"  DISAGREEMENTS (findings): {', '.join(conflicts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
