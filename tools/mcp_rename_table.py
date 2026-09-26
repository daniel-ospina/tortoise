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
  3. **Derived-region citations.** The registry prose this artifact quotes is not
     typed: each citation NAMES a region of a source file (the maximal
     blank-line-bounded comment paragraph containing an anchor line) and the
     generator EXTRACTS that region verbatim. A claim nothing quotes is a claim
     nothing can check, and a hand-typed quote can be cut on either edge — `quote
     in text` cannot see it, because a truncated prefix is still a substring.
     Deriving the region removes the failure mode instead of detecting it: a
     truncation is impossible by construction, and an anchor that does not match
     exactly one source line FAILS the build. `_maximal` is retained only as a
     cheap second tripwire, and it requires BOTH edges to be region boundaries.
     The rule is stated here, inline, as its own definition.
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
# THE CITATIONS — DERIVED REGIONS, never hand-typed quotes.
#
# A citation NAMES a region of a source file and the generator EXTRACTS it whole.
# The region is the maximal blank-line-bounded comment paragraph containing the
# anchor line, so BOTH edges are boundaries the source itself provides and a
# truncation is impossible by construction. The anchor is a SELECTOR, not
# evidence: it must match exactly one source line (or the build refuses), and the
# rendered quote is read out of the source, never re-typed.
# ─────────────────────────────────────────────────────────────────────

# key -> (source document, the anchor line that names the region). The anchor is a
# selector, not evidence: it either matches exactly one source line or the build
# fails, and the rendered quote is the extracted paragraph, never the anchor.
REGIONS: dict[str, tuple[str, str]] = {
    "retired_behaviour": (
        "tortoise/tool_registry.py",
        "A name in this mapping is RETIRED: it is not in TOOL_REGISTRY, so it is not",
    ),
    "owner_decision": (
        "tortoise/tool_registry.py",
        "⚠ This direction is an OWNER DECISION, not an implementation preference:",
    ),
}

# A region boundary is a BLANK LINE or the document's own edge. A SENTENCE end is
# deliberately NOT a boundary: the next sentence is exactly where a contradictory
# clause begins. A LEFT line boundary is not enough either — dropping a region's
# first line leaves the quote starting at a line boundary, which is the
# head-truncation the derived-region design exists to make impossible; `_maximal`
# is the second tripwire for exactly that, and it demands BOTH edges.
_COMMENT_LINE = re.compile(r"^\s*#\s?(.*)$")


def _comment_text(path: Path) -> str:
    """`path`'s comment prose with the `#` markers stripped, as one text.

    The citations quote the source's OWN comments, so the text they are checked
    against has to be the comment content — against the raw file a quote of a
    comment never matches and the guard would be vacuous. Non-comment lines become
    empty lines, so a contiguous comment region stays contiguous.
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _COMMENT_LINE.match(line)
        out.append(m.group(1) if m else "")
    return "\n".join(out)


class _RegionError(Exception):
    """An anchor that does not name exactly one region — a defect in the EVIDENCE."""


def _region(doc: str, anchor: str) -> str:
    """The maximal blank-line-bounded comment paragraph containing `anchor`.

    The region is READ OUT OF THE SOURCE, never typed, so neither edge can be a
    truncation: both are the blank separators the source itself provides.
    """
    path = ROOT / doc
    if not path.is_file():
        raise _RegionError(f"source document does not exist: {doc}")
    lines = _comment_text(path).split("\n")
    hits = [i for i, line in enumerate(lines) if line == anchor]
    if not hits:
        raise _RegionError(f"anchor line is not in {doc}: {anchor!r}")
    if len(hits) > 1:
        raise _RegionError(
            f"anchor line matches {len(hits)} lines of {doc} — it must name exactly "
            f"one region: {anchor!r}"
        )
    i = hits[0]
    start = i
    while start > 0 and lines[start - 1] != "":
        start -= 1
    end = i
    while end + 1 < len(lines) and lines[end + 1] != "":
        end += 1
    return "\n".join(lines[start:end + 1])


def _at_paragraph_start(text: str, start: int) -> bool:
    """Is `start` the first character of a blank-line-bounded region?"""
    if start == 0:
        return True
    if text[start - 1] != "\n":
        return False
    prev_start = text.rfind("\n", 0, start - 1) + 1
    return text[prev_start:start - 1].strip() == ""


def _at_paragraph_end(text: str, end: int) -> bool:
    """Is `end` the exclusive end of a blank-line-bounded region?"""
    if end >= len(text):
        return True
    if text[end] != "\n":
        return False
    next_end = text.find("\n", end + 1)
    if next_end == -1:
        next_end = len(text)
    return text[end + 1:next_end].strip() == ""


def _maximal(quote: str, text: str) -> bool:
    """Is `quote` a maximal region of `text` — truncated on NEITHER edge?

    The SECOND tripwire over the derived region: a derivation bug that returned a
    sub-paragraph, or a future edit that re-introduced a typed quote, must fail
    here. Both edges are required; a left line boundary alone is NOT a region
    boundary (dropping a paragraph's first line leaves exactly that).
    """
    idx = text.find(quote)
    if idx < 0:
        return True  # absent text is reported as CITATION SOURCE ERROR by `_region`
    return _at_paragraph_start(text, idx) and _at_paragraph_end(text, idx + len(quote))


def _citation_errors() -> list[str]:
    """Fail the build on a region that will not resolve or comes out truncated.

    A disagreement between the map and the registry is a FINDING to report. A
    citation whose source file is missing, whose anchor does not name exactly one
    region, or that does not come out as a maximal paragraph is a defect in the
    EVIDENCE itself, and evidence that can be edited to agree with the row is
    worse than no evidence.
    """
    errs: list[str] = []
    # Non-vacuity: a guard that resolves nothing cannot fail, and would read green.
    if not REGIONS:
        errs.append("NO citation is declared — the citation guard is UNARMED")
    for key, (doc, anchor) in sorted(REGIONS.items()):
        try:
            quote = _region(doc, anchor)
        except _RegionError as e:
            errs.append(f"CITATION SOURCE ERROR: {key}: {e}")
            continue
        if not quote.strip():
            errs.append(
                f"EMPTY CITATION: {key} resolves to nothing — an empty string is a "
                "substring of every document, so the guard passes while the claim "
                "is backed by nothing"
            )
            continue
        text = _comment_text(ROOT / doc)
        if not _maximal(quote, text):
            idx = text.find(quote)
            before = text[:idx][-60:]
            after = text[idx + len(quote):][:60]
            errs.append(
                f"TRUNCATED CITATION: {key}'s region is not a maximal paragraph of "
                f"{doc} — it continues {after!r}, preceded by {before!r}. A region "
                "must be extracted whole: a truncation on either edge drops the "
                "clause that can contradict the row it backs, and a sentence end is "
                "NOT a boundary because the next sentence is where a contradiction "
                "begins."
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
    """Render a derived citation as blockquote lines, verbatim."""
    return [f"> {line}" for line in _region(*REGIONS[key]).splitlines()]


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
