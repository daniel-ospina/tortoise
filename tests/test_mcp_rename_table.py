"""The MCP rename table must not drift from the maps and code it describes (#4282).

`tools/mcp_rename_table.py` reads every `file:line` straight from the AST and every
count from authored data, so a citation in the generated doc cannot be wrong. What it
CAN still do is go *stale* — someone edits `tool_registry.py`, `tools/bridge_table.py`'s
destination map, or the registry's `RETIRED_USE_INSTEAD` and never regenerates — and
that is the drift the generator's own docstring promises cannot happen. Nothing runs
`--check` unless a test does. This is that enforcement.

WHY EVERY EXPECTED VALUE HERE IS A LITERAL OR AN INDEPENDENT WALK
-----------------------------------------------------------------
The sibling 0.1 test was red-green for SEVEN review rounds because an assertion imported
the very constant it was verifying (`from tools.bridge_table import C1_FINDING`, then
`assert rendered == C1_FINDING`): editing the constant moved both sides together, so the
assertion could not fail. The same trap is available here for the redirect map
and for every rendered count. So:

  * the redirects are pinned as LITERALS in this file, never imported from the
    generator — and cross-checked against the live registry module (an oracle that is
    independent of the generator);
  * the registry's tool set and `file:line`s are re-derived here with a second AST walk;
  * every prose count is recomputed from those two independent oracles.

The generator is only ever imported to exercise its BEHAVIOUR (does it exit non-zero on
a disagreement or a truncated citation?), never to supply an expected value.

Registered in `config/ci-surfaces.yml` under both `api` and `core`, mirroring
`test_bridge_table.py`: `api` carries the arm that fires when only the generator is edited
(`tools/mcp_rename_table.py` is named in `SOURCE_PATTERNS["api"]`), `core` is the docs
fallback.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "mcp_rename_table.py"
DOC = ROOT / "docs" / "product" / "mcp-rename-table.md"
REGISTRY = ROOT / "tortoise" / "tool_registry.py"

# ── LITERAL ORACLES — never imported from the generator under test ───────────
# Read from `tortoise/tool_registry.py::RETIRED_USE_INSTEAD` on `main` after PR
# #4031 merged (e3bb78a14). Main's landed map retires `tortoise_get` and KEEPS
# `tortoise_get_entity` — the reverse of the pre-merge branch copy — because
# retiring the pair the other way is the churn loop the registry's own
# owner-decision comment forbids. If the generator's map drifts from this, the
# doc reds; if the registry drifts from this, the cross-check below reds.
RETIRED_LITERAL: dict[str, str] = {
    "tortoise_get": 'tortoise_get_entity(id, type=...)',
    "tortoise_get_events": 'tortoise_get_entity(None, type="events")',
    "tortoise_get_governance": 'tortoise_get_entity(id, type="governance")',
    "tortoise_get_operator": 'tortoise_get_entity(id, type="operator")',
    "tortoise_get_point": 'tortoise_get_entity(id, type="point")',
    "tortoise_health": 'tortoise_overview(section="health")',
    "tortoise_index_sessions": "tortoise_index_files(directory)",
    "tortoise_ingest_corpus": "tortoise_index_files(directory)",
    "tortoise_list_pointkinds": 'tortoise_overview(section="pointkinds")',
    "tortoise_list_sources": 'tortoise_overview(section="sources")',
    "tortoise_list_tags": 'tortoise_overview(section="tags")',
    "tortoise_paginated_query": "tortoise_query(offset=..., limit=...)",
    "tortoise_query_points_by_tag": "tortoise_query(tag=...)",
    "tortoise_stale": 'tortoise_overview(section="stale")',
    "tortoise_status": 'tortoise_overview(section="status")',
    "tortoise_taxonomy": 'tortoise_overview(section="taxonomy")',
}

# The names whose 0.1 destination differs from the destination of their redirect.
# Recomputed against main's map this is EMPTY — the six disagreements the artifact
# was originally built around were an artefact of the pinned pre-merge copy, and
# #4031 changed `bridge_table.DESTINATION` for exactly those six names. The empty
# set is pinned rather than merely recomputed: a recomputation that inverted would
# otherwise agree with itself.
DISAGREEMENTS_LITERAL: set[str] = set()

# Provenance is ONE fact, pinned as literals: PR #4031 merged as `e3bb78a14`. The
# rendered PR number and SHA must come from the generator's single source, so a
# second typed copy cannot drift with the suite green.
PR_NUMBER_LITERAL = 4031
PR_MERGE_SHA_LITERAL = "e3bb78a14"
# `e3bb78a14` is committed at 2026-09-21 12:06:35 -0500. Pinned as a LITERAL
# because nothing used to read the rendered date: it could be typed as any value
# and the suite stayed green (independent review, P2-1).
PR_MERGED_DATE_LITERAL = "2026-09-21"

# The region each citation NAMES — a (source document, anchor line) pair, pinned
# as LITERALS. The generator derives the rendered quote from these, so a mutation
# of either moves the document away from what `_derived_paragraph` finds here and
# reds `test_rendered_citations_are_the_full_derived_region`.
REGION_LITERAL: dict[str, tuple[str, str]] = {
    "retired_behaviour": (
        "tortoise/tool_registry.py",
        "A name in this mapping is RETIRED: it is not in TOOL_REGISTRY, so it is not",
    ),
    "owner_decision": (
        "tortoise/tool_registry.py",
        "⚠ This direction is an OWNER DECISION, not an implementation preference:",
    ),
}

# The last line of each derived region is the clause a tail-truncation drops.
# Pinned as LITERALS: a tail-drop lands on a line boundary, which the old
# right-edge-only rule accepted, so only an explicit tail pin can see it.
CITATION_TAIL_LITERAL: dict[str, str] = {
    "retired_behaviour": "and tells us who still calls it.",
    "owner_decision":
        "loop — which is why the pointers below name `tortoise_get_entity`.",
}

NO_REPLACEMENT = "**no replacement**"
RETIRE_WARN = "warning shim"
RETIRE_ABSENT = "absent"


def _registry_names() -> dict[str, int]:
    """`ToolDefinition(name=…)` → its call line, from an AST walk of the registry.

    Derived here rather than from `tools.bridge_table._registry_rows` (the
    generator's own source for the same fact), so the citation column is
    compared against an independent oracle instead of a second reading of the
    generator.
    """
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "ToolDefinition":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                out[kw.value.value] = node.lineno
    assert out, "no `ToolDefinition(name=…)` calls found in tool_registry.py"
    return out


def _destination_map() -> dict[str, str]:
    """The authored Phase 0.1 destination map — the table's INPUT, not its output.

    This is data in `tools/bridge_table.py` (reviewed by a human), so importing
    it is comparing the rendered doc against its source, exactly as the sibling
    suite does. It is NOT the generator's render.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION

    assert DESTINATION, "the 0.1 destination map is empty"
    return dict(DESTINATION)


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _parse_part_a() -> dict[str, dict]:
    doc = _doc()
    part = doc.split("## Part A")[1].split("### Destination counts")[0]
    rows: dict[str, dict] = {}
    for line in part.splitlines():
        m = re.match(
            r"^\| (\d+) \| `([a-z_][a-z0-9_]*)` \| `tool_registry\.py:(\d+)` \| "
            r"(.+?) \| `([A-Za-z_:]+)` \| (warning shim|absent) \|$",
            line,
        )
        if m:
            idx, name, lineno, instead, dest, retirement = m.groups()
            rows[name] = {
                "idx": int(idx),
                "lineno": int(lineno),
                "instead": instead.strip(),
                "destination": dest,
                "retirement": retirement,
            }
    return rows


def _parse_part_b() -> dict[str, dict]:
    doc = _doc()
    part = doc.split("## Part B")[1].split("### B1")[0]
    rows: dict[str, dict] = {}
    for line in part.splitlines():
        m = re.match(
            r"^\| `([a-z_][a-z0-9_]*)` \| `([A-Za-z_:]+)` \| `(.+?)` \| "
            r"(`[A-Za-z_:]+`|—) \| (\*\*DISAGREE\*\*|AGREE) \|$",
            line,
        )
        if m:
            _, d_old, redirect, d_new, verdict = m.groups()
            rows[m.group(1)] = {
                "destination": d_old,
                "redirect": redirect,
                "redirect_destination": d_new,
                "verdict": verdict,
            }
    return rows


# ── Part A: coverage, citations, and every free-standing column ──────────────

def test_retired_literal_matches_the_live_registry() -> None:
    """The typed literals above must equal the registry's live map.

    One side is typed (so a registry edit reds), one is live (so the generator
    cannot hide behind a private copy), and the assertion is on equality — not
    on a substring, which a prefix would satisfy.
    """
    from tortoise.tool_registry import RETIRED_USE_INSTEAD

    assert dict(RETIRED_USE_INSTEAD) == RETIRED_LITERAL, (
        "the registry's RETIRED_USE_INSTEAD no longer equals the pinned literals:\n"
        f"  only in registry: {sorted(set(RETIRED_USE_INSTEAD) - set(RETIRED_LITERAL))}\n"
        f"  only in literals: {sorted(set(RETIRED_LITERAL) - set(RETIRED_USE_INSTEAD))}"
    )


def test_part_a_covers_exactly_the_registry() -> None:
    """One row per served registry tool, numbered 1..N, citing the tool's real line."""
    truth = _registry_names()
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows — did its column shape change?"
    assert set(rows) == set(truth), (
        "Part A does not render exactly the registry tools.\n"
        f"  only in doc ({len(set(rows) - set(truth))}): {sorted(set(rows) - set(truth))}\n"
        f"  only in registry ({len(set(truth) - set(rows))}): {sorted(set(truth) - set(rows))}"
    )
    assert len(rows) == len(truth)
    idx = [r["idx"] for r in rows.values()]
    assert sorted(idx) == list(range(1, len(rows) + 1)), (
        f"Part A's `#` column is not 1..{len(rows)}: got {sorted(idx)[:5]}…{sorted(idx)[-3:]}"
    )
    wrong = [
        f"{name}: cited tool_registry.py:{rows[name]['lineno']}, defined at :{truth[name]}"
        for name in rows
        if rows[name]["lineno"] != truth[name]
    ]
    assert not wrong, (
        "Part A cites the wrong line for these tools; every citation is supposed to "
        "be read from the AST at build time:\n  " + "\n  ".join(wrong[:10])
    )


def test_part_a_call_instead_and_retirement_are_the_live_map() -> None:
    """The `Call instead today` and `Retirement` columns come from the live map.

    Both are computed from ONE registry dict, so a mutation of the map moves both
    columns together — and neither is read by any other test. The literal oracle
    below is what stops that mutation being free.
    """
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows"

    for name, row in rows.items():
        if name in RETIRED_LITERAL:
            assert row["instead"] == f"`{RETIRED_LITERAL[name]}`", (
                f"Part A says {name} should call {row['instead']!r} instead; the "
                f"registry map says `{RETIRED_LITERAL[name]}`"
            )
            assert row["retirement"] == RETIRE_WARN, (
                f"Part A says {name} retires as {row['retirement']!r}; it is in the "
                f"registry map, so it must be {RETIRE_WARN!r}"
            )
        else:
            assert row["instead"] == NO_REPLACEMENT, (
                f"Part A says {name} should call {row['instead']!r} instead; it is not in "
                f"the registry map, so it must be {NO_REPLACEMENT!r}"
            )
            assert row["retirement"] == RETIRE_ABSENT, (
                f"Part A says {name} retires as {row['retirement']!r}; it is not in the "
                f"registry map, so it must be {RETIRE_ABSENT!r}"
            )

    # The map's keys must be exactly the registry tools that are redirected: a
    # name in the map with no row, or a row marked `warning shim` while not in
    # the literal map, is a drift the per-row loop above cannot see.
    warned = {n for n, r in rows.items() if r["retirement"] == RETIRE_WARN}
    assert warned == set(RETIRED_LITERAL), (
        f"Part A marks {len(warned)} tools `warning shim`; the registry map has "
        f"{len(RETIRED_LITERAL)} entries.\n"
        f"  extra: {sorted(warned - set(RETIRED_LITERAL))}\n"
        f"  missing: {sorted(set(RETIRED_LITERAL) - warned)}"
    )


def test_part_a_destination_column_is_the_0_1_map() -> None:
    """Every `Destination (target surface)` cell equals the 0.1 map, exactly.

    Without this the whole column is free: overwriting every cell with `REMOVED`
    contradicts the doc's own counts table and headline while every other test
    stays green.
    """
    dest = _destination_map()
    rows = _parse_part_a()
    assert rows, "Part A rendered no parseable rows"
    for name, row in rows.items():
        assert row["destination"] == dest.get(name), (
            f"Part A says {name} → {row['destination']!r}; the 0.1 map says "
            f"{dest.get(name)!r}"
        )


def test_destination_counts_table_and_prose_are_arithmetic() -> None:
    """The counts table, its total cell, and every prose count must all agree."""
    dest = _destination_map()
    truth: dict[str, int] = {}
    for v in dest.values():
        truth[v] = truth.get(v, 0) + 1
    n_tools = len(_registry_names())
    assert sum(truth.values()) == n_tools

    doc = _doc()
    table = dict(re.findall(r"^\| `([A-Za-z_:]+)` \| (\d+) \|$", doc, re.M))
    assert table, "the destination-counts table did not parse — the surface is unguarded"
    assert len(table) == len(truth), (
        f"the counts table has {len(table)} rows; the 0.1 map has {len(truth)} destinations"
    )
    for cell, rendered in table.items():
        assert int(rendered) == truth.get(cell), (
            f"the counts table says {cell} has {rendered} sources; the map says "
            f"{truth.get(cell)}"
        )
    total_cell = re.search(r"^\| \*\*total\*\* \| \*\*(\d+)\*\* \|$", doc, re.M)
    assert total_cell, "the destination-counts table has no `**total**` row"
    assert int(total_cell.group(1)) == n_tools, (
        f"the total cell says {total_cell.group(1)}; the registry has {n_tools} tools"
    )

    # Headline sentence 1: "N served MCP tools, M destinations."
    m = re.search(r"^\*\*(\d+) served MCP tools, (\d+) destinations\.\*\*", doc, re.M)
    assert m, "the `N served MCP tools, M destinations` headline is missing or changed shape"
    assert int(m.group(1)) == n_tools, f"headline says {m.group(1)} tools; there are {n_tools}"
    assert int(m.group(2)) == len(truth), (
        f"headline says {m.group(2)} destinations; the map has {len(truth)}"
    )

    # Sentence 2: the retirement split.
    m2 = re.search(
        r"(\d+) retire \*\*with a warning shim\*\*; the other \*\*(\d+)\*\* are simply absent",
        doc,
    )
    assert m2, "the retirement-split sentence is missing or changed shape"
    assert int(m2.group(1)) == len(RETIRED_LITERAL), (
        f"the doc says {m2.group(1)} retire with a warning shim; the registry map has "
        f"{len(RETIRED_LITERAL)}"
    )
    assert int(m2.group(2)) == n_tools - len(RETIRED_LITERAL), (
        f"the doc says {m2.group(2)} are simply absent; "
        f"{n_tools - len(RETIRED_LITERAL)} are"
    )

    # Sentence 3: the destination-bucket breakdown.
    m3 = re.search(
        r"\*\*(\d+)\*\* absorbed into the \*\*(\d+)\*\* MCP targets, "
        r"\*\*(\d+)\*\* into a builder-only SDK method \(not on the MCP\), "
        r"\*\*(\d+)\*\* tenancy \(SDK/REST only\), \*\*(\d+)\*\* removed\.",
        doc,
    )
    assert m3, "the destination-bucket sentence is missing or changed shape"
    on_mcp, n_targets, sdk_only, tenancy, removed = (int(g) for g in m3.groups())
    assert sdk_only == sum(n for d, n in truth.items() if d.startswith("sdk:"))
    assert tenancy == sum(n for d, n in truth.items() if d.startswith("tenancy:"))
    assert removed == truth.get("REMOVED", 0)
    assert on_mcp == n_tools - sdk_only - tenancy - removed, (
        "the bucket sentence's `absorbed into MCP targets` figure does not match the "
        "other four buckets"
    )
    assert n_targets == 26, (
        f"the bucket sentence says {n_targets} MCP targets; the approved target surface is 26"
    )
    assert on_mcp + sdk_only + tenancy + removed == n_tools


# ── Part B: the agreement check ──────────────────────────────────────────────

def test_part_b_rows_are_exactly_the_live_map_and_verdicts_recompute() -> None:
    """Part B covers the registry's redirect names, and each verdict is the comparison."""
    dest = _destination_map()
    rows = _parse_part_b()
    assert rows, "Part B rendered no parseable rows — did its column shape change?"
    assert set(rows) == set(RETIRED_LITERAL), (
        "Part B does not render exactly the registry's redirect map.\n"
        f"  only in doc: {sorted(set(rows) - set(RETIRED_LITERAL))}\n"
        f"  only in map: {sorted(set(RETIRED_LITERAL) - set(rows))}"
    )

    for name, row in rows.items():
        assert row["destination"] == dest[name], (
            f"Part B says {name}'s 0.1 destination is {row['destination']!r}; the map says "
            f"{dest[name]!r}"
        )
        assert row["redirect"] == RETIRED_LITERAL[name], (
            f"Part B says {name} redirects to {row['redirect']!r}; the map says "
            f"{RETIRED_LITERAL[name]!r}"
        )
        target = re.match(r"(tortoise_[a-z0-9_]+)", RETIRED_LITERAL[name]).group(1)
        assert row["redirect_destination"] == f"`{dest[target]}`", (
            f"Part B says {name}'s redirect lands on {row['redirect_destination']!r}; "
            f"{target} maps to `{dest[target]}`"
        )
        assert row["verdict"] == ("AGREE" if dest[name] == dest[target] else "**DISAGREE**"), (
            f"Part B's verdict for {name} is {row['verdict']!r}; "
            f"`{dest[name]}` vs `{dest[target]}` says otherwise"
        )

    # The verdict-tally prose is a count, so assert it against the recomputed set.
    agree = {n for n, r in rows.items() if r["verdict"] == "AGREE"}
    m = re.search(r"\*\*(\d+) of (\d+) agree; (\d+) disagree\.\*\*", _doc())
    assert m, "the agreement-tally sentence is missing or changed shape"
    assert int(m.group(1)) == len(agree)
    assert int(m.group(2)) == len(rows)
    assert int(m.group(3)) == len(rows) - len(agree)


def test_b1_states_plainly_that_there_are_no_disagreements() -> None:
    """B1 is zero findings, and it says so — it is not an absent section.

    The expected set is the LITERAL disagreement set, plus an independent
    recomputation — a generator whose comparison logic inverted would render a
    self-consistent Part B, so the literal is the anchor.
    """
    dest = _destination_map()
    recomputed = {
        name for name, expr in RETIRED_LITERAL.items()
        if dest[name] != dest[re.match(r"(tortoise_[a-z0-9_]+)", expr).group(1)]
    }
    assert recomputed == DISAGREEMENTS_LITERAL, (
        "the #4282 disagreement set changed:\n"
        f"  recomputed: {sorted(recomputed)}\n"
        f"  pinned here: {sorted(DISAGREEMENTS_LITERAL)}"
    )
    assert not DISAGREEMENTS_LITERAL, (
        "the pinned disagreement set is not empty — the artifact is back to shipping a finding"
    )

    b1 = _doc().split("### B1")[1].split("## Reproduce")[0]
    # A findings list that is absent is indistinguishable from one nobody wrote,
    # so B1 must carry an EXPLICIT zero, not empty space.
    assert re.search(r"\*\*None — 0 findings\.\*\*", b1), (
        "B1 no longer states plainly that there are no disagreements"
    )
    # And it must not silently list rows: a disagreement reappearing has to be a
    # rendered finding, not a paragraph.
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", b1, re.M))
    assert listed == DISAGREEMENTS_LITERAL, (
        "B1 lists rows while its pinned disagreement set is empty — the section "
        "contradicts its own count:\n"
        f"  listed: {sorted(listed)}"
    )
    assert "tortoise #4475" not in _doc(), (
        "the document still names #4475, whose premise (6 disagreements) is false on main"
    )
    # The Part B intro's map size is rendered from the same dict, but nothing read
    # it: `the same 16 names` could drift to any number with the suite green.
    assert f"the same {len(RETIRED_LITERAL)} names" in _doc(), (
        "the Part B intro no longer states the redirect-map size the table covers"
    )
    assert len(RETIRED_LITERAL) == 16, (
        f"the redirect map is {len(RETIRED_LITERAL)} names, not the landed 16"
    )


def test_provenance_states_the_merge_and_that_the_column_is_live() -> None:
    """The document must state #4031 MERGED, and that the column is present behaviour.

    The PR number, SHA and merge date are pinned as LITERALS here, and the
    generator renders them from ONE source, so a second typed copy cannot drift
    with the suite green. EVERY rendered mention is pinned: `re.search` saw only
    the first `PR #NNNN`, so a hand-typed second `#40310` (in the Part B heading,
    say) passed. The dead branch ref must be gone: the map is read live, not
    pinned to a branch that no longer exists.
    """
    doc = _doc()
    # The provenance paragraph, derived from the generator's single source.
    assert f"PR #{PR_NUMBER_LITERAL} MERGED as `{PR_MERGE_SHA_LITERAL}`" in doc, (
        f"the document does not state that PR #{PR_NUMBER_LITERAL} MERGED as "
        f"`{PR_MERGE_SHA_LITERAL}` — the paragraph's provenance is not the merged PR"
    )
    # EXACT, not substring: EVERY rendered PR mention must be the one number. A
    # second hand-typed copy is a claim that can drift.
    mentions = re.findall(r"PR #(\d+)", doc)
    assert mentions, "the document no longer records any `PR #NNNN`"
    assert set(mentions) == {str(PR_NUMBER_LITERAL)}, (
        f"the document names PRs {sorted(set(mentions))!r}; every one of its "
        f"{len(mentions)} mentions must be #{PR_NUMBER_LITERAL}"
    )
    # Every backticked short-SHA token must be the one merge SHA.
    shas = re.findall(r"`([0-9a-f]{7,40})`", doc)
    assert set(shas) == {PR_MERGE_SHA_LITERAL}, (
        f"the document renders SHAs {sorted(set(shas))!r}; every one must be "
        f"`{PR_MERGE_SHA_LITERAL}`"
    )
    # The merge DATE is rendered from one source and pinned here: nothing used to
    # read it, so it could be typed as any value with the suite green (P2-1).
    assert f"({PR_MERGED_DATE_LITERAL})" in doc, (
        f"the merge paragraph does not render the date ({PR_MERGED_DATE_LITERAL})"
    )
    # The column is PRESENT behaviour, not a forecast.
    assert "today's behaviour, not a forecast" in doc, (
        "the document no longer says the `Retirement` column is today's behaviour"
    )
    assert "read live from `tortoise.tool_registry.RETIRED_USE_INSTEAD`" in doc, (
        "the document no longer says the map is read live from the registry"
    )
    # The dead branch must not come back: the pre-merge provenance named a branch
    # that was deleted when #4031 merged, and an unverifiable pin is worse than none.
    assert "3883-retired-name-warning" not in doc, (
        "the document names the pre-merge branch again — the map is read live now"
    )
    # The Reproduce block is what a reader runs; nothing read it, so the script name
    # could be renamed in the generator and the document would instruct a reader to
    # run a file that does not exist.
    for cmd in (
        "uv run python tools/mcp_rename_table.py          # regenerate this file",
        "uv run python tools/mcp_rename_table.py --check  # verify, non-zero exit on drift",
    ):
        assert cmd in doc, f"the Reproduce block no longer contains: {cmd!r}"


# ── The citation guard: a region is DERIVED, not typed ──────────────────────

def _comment_lines_of(path: Path) -> list[str]:
    """`path`'s comment content per line, non-comment lines blanked.

    The test's OWN extractor, independent of `tools/mcp_rename_table.py`.
    """
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*#\s?(.*)$", line)
        out.append(m.group(1) if m else "")
    return out


def _derived_paragraph(doc: str, anchor: str) -> str:
    """Independently derive the blank-line-bounded paragraph containing `anchor`.

    The expected citation region is computed from the SOURCE (with the pinned
    anchor), never read back from the generator under test.
    """
    lines = _comment_lines_of(ROOT / doc)
    hits = [i for i, line in enumerate(lines) if line == anchor]
    assert len(hits) == 1, f"anchor {anchor!r} matched {len(hits)} lines of {doc}"
    i = hits[0]
    start = i
    while start > 0 and lines[start - 1] != "":
        start -= 1
    end = i
    while end + 1 < len(lines) and lines[end + 1] != "":
        end += 1
    return "\n".join(lines[start:end + 1])


def test_maximal_rejects_a_truncation_on_either_edge() -> None:
    """`_maximal` (the secondary tripwire) demands BOTH edges be region boundaries.

    The rule is stated inline here in the generator as its own definition, so
    this test pins that definition rather than a cited prior art.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    text = "alpha beta gamma.\ndelta epsilon\n\nzeta | tail\n\nomega\n"
    assert mrt._maximal("alpha beta gamma.\ndelta epsilon", text) is True
    assert mrt._maximal("omega", text) is True  # bounded by the document edge
    # HEAD truncation: the paragraph's first line dropped. The quote still starts
    # at a LINE boundary — which a left-edge-only check would accept.
    assert mrt._maximal("delta epsilon", text) is False, (
        "a head truncation was accepted — dropping the first line leaves a line "
        "boundary on the left, and the dropped line carries the claim"
    )
    # TAIL truncation: stops mid-paragraph, on a line boundary.
    assert mrt._maximal("alpha beta gamma.", text) is False, (
        "a tail truncation was accepted — the next line continues the claim"
    )
    # A sentence end is not a boundary either: the rest of the line continues the
    # claim, and the next sentence is where a contradiction can begin.
    assert mrt._maximal("one. two", "one. two\n\nthree\n") is True
    assert mrt._maximal("one.", "one. two\n\nthree\n") is False, (
        "a sentence end mid-line was accepted — the rest of the line continues the claim"
    )
    assert mrt._maximal("absent text", text) is True, (
        "absent text must pass here: a missing anchor is reported by its own check"
    )


def test_committed_citations_resolve_and_are_maximal() -> None:
    """Every declared region resolves, exactly once, to a maximal paragraph."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert mrt.REGIONS, "the generator declares no citation — the guard is unarmed"
    assert mrt._citation_errors() == [], (
        "a committed citation failed to resolve or came out truncated"
    )


def test_rendered_citations_are_the_full_derived_region() -> None:
    """The document renders EVERY line of each independently derived region.

    The expected region is computed by this file's OWN extractor from the source
    and the pinned anchors — never read back from the generator. A region cut on
    EITHER edge (the P1-1 head-truncation) is a mismatch. This is the assertion
    the review's P1-1 mutation reds.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert mrt.REGIONS == REGION_LITERAL, (
        "the declared regions changed:\n"
        f"  declared: {sorted(mrt.REGIONS.items())}\n"
        f"  pinned:   {sorted(REGION_LITERAL.items())}"
    )
    doc = _doc()
    for key, (src, anchor) in REGION_LITERAL.items():
        assert (ROOT / src).is_file(), f"{key} names a source that does not exist: {src}"
        region = _derived_paragraph(src, anchor)
        rendered = "\n".join(f"> {line}" for line in region.splitlines())
        assert rendered in doc, (
            f"{key}'s derived region is not rendered in full — the document is "
            f"truncated or the region moved:\n{rendered}"
        )
        # The anchor line is the FIRST clause of the claim; a head-truncation
        # drops exactly it while leaving a valid-looking blockquote.
        assert f"> {anchor}" in doc, f"{key}'s anchor line is not rendered in the document"


def test_declared_citations_reach_their_final_clause_in_the_document() -> None:
    """Each region's LAST clause is rendered, so a tail-truncation reds.

    A truncation that lands on a line boundary is what the old right-edge-only
    rule accepted, so an assertion that read the tail from the generator would
    move with the mutation and see nothing. The tails are LITERALS.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert set(mrt.REGIONS) == set(CITATION_TAIL_LITERAL), (
        "the declared citation set changed:\n"
        f"  declared: {sorted(mrt.REGIONS)}\n"
        f"  pinned:   {sorted(CITATION_TAIL_LITERAL)}"
    )
    doc = _doc()
    for key, tail in CITATION_TAIL_LITERAL.items():
        assert mrt._region(*mrt.REGIONS[key]).endswith(tail), (
            f"{key}'s derived region no longer reaches its final clause {tail!r}"
        )
        assert f"> {tail}" in doc, (
            f"{key}'s final clause is not rendered in the document — a truncation "
            "drops exactly this clause"
        )


def test_build_fails_on_a_truncated_region(monkeypatch, capsys) -> None:
    """A region that comes out cut on EITHER edge must FAIL the build.

    A derived region cannot be truncated by construction, so the mutation here is
    a derivation BUG — exactly the tripwire's job: `_region` is replaced with one
    that drops a whole line, head and tail in turn.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    real = mrt._region
    for label, cut in (
        ("head", lambda q: q.splitlines()[1:]),
        ("tail", lambda q: q.splitlines()[:-1]),
    ):
        monkeypatch.setattr(
            mrt, "_region",
            lambda doc, anchor, _cut=cut: "\n".join(_cut(real(doc, anchor))),
        )
        errs = mrt._citation_errors()
        assert any("TRUNCATED CITATION" in e for e in errs), (
            f"a {label} truncation was accepted by the guard: {errs!r}"
        )
    # The guard must be WIRED INTO the build, not merely available: `_validate`
    # has to surface it, or the CI form exits 0 with the defect.
    assert any(
        "TRUNCATED CITATION" in e for e in mrt._validate(mrt._registry_rows())
    ), "the citation guard is not wired into `_validate`"
    monkeypatch.setattr(sys, "argv", ["mcp_rename_table.py", "--check"])
    rc = mrt.main()
    err = capsys.readouterr().err
    assert rc == 1, "the generator exited 0 with a truncated region"
    assert "TRUNCATED CITATION" in err, (
        "the build failed, but not for the truncated region — the check is not wired "
        f"into `_validate` (stderr was {err!r})"
    )


def test_build_fails_when_the_declared_source_does_not_resolve(monkeypatch) -> None:
    """The source half of a region is LOAD-BEARING, not decorative.

    It used to appear only in a drift message, so changing it to any path —
    including one that does not exist — left the suite green (P2-5). It is now the
    file the region is EXTRACTED from, so a missing source, a vanished anchor, and
    an ambiguous anchor all fail the build.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    anchor = REGION_LITERAL["owner_decision"][1]
    monkeypatch.setitem(
        mrt.REGIONS, "owner_decision", ("tortoise/does_not_exist.py", anchor)
    )
    errs = mrt._citation_errors()
    assert any("CITATION SOURCE ERROR" in e and "does_not_exist" in e for e in errs), (
        f"a region in a nonexistent source was accepted: {errs!r}"
    )
    # A source that EXISTS but does not contain the anchor is also a fail.
    monkeypatch.setitem(
        mrt.REGIONS, "owner_decision", ("tortoise/sdk.py", "a line sdk.py does not contain")
    )
    errs = mrt._citation_errors()
    assert any("CITATION SOURCE ERROR" in e and "sdk.py" in e for e in errs), (
        f"an anchor absent from the declared source was accepted: {errs!r}"
    )
    # An anchor that matches more than one line must not silently pick one.
    monkeypatch.setitem(mrt.REGIONS, "owner_decision", ("tortoise/tool_registry.py", ""))
    errs = mrt._citation_errors()
    assert any("CITATION SOURCE ERROR" in e and "exactly" in e for e in errs), (
        f"an ambiguous anchor was accepted: {errs!r}"
    )


# ── The generator's failure paths ────────────────────────────────────────────


def test_generator_exits_nonzero_when_the_map_and_registry_disagree(monkeypatch, capsys) -> None:
    """The generator must FAIL, not paper over, a registry/map disagreement.

    Behavioural: `main()` is driven with `--check` and a phantom registry row,
    so the assertion is on the exit code the CI form produces — not on a
    private helper's return value.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    real = mrt._registry_rows()
    phantom = {
        "name": "tortoise_zzz_phantom", "lineno": 1, "sdk_method": "",
        "resolves": False, "read_only": True,
    }
    monkeypatch.setattr(mrt, "_registry_rows", lambda: [*real, phantom])
    monkeypatch.setattr(sys, "argv", ["mcp_rename_table.py", "--check"])
    assert mrt.main() == 1, "the generator exited 0 with an unmapped registry tool"
    err = capsys.readouterr().err
    assert "tortoise_zzz_phantom" in err, (
        "the failure message does not name the offending tool, so it reads like a bug "
        "rather than a fixable finding"
    )


def test_rendezvous_hint_fires_only_for_the_landed_registry_split() -> None:
    """Names moving TOOL_REGISTRY → RETIRED_TOOL_REGISTRY are the rendezvous.

    A red that reads like a bug gets worked around; a red that reads like a
    rendezvous gets acted on. The predicate is driven by the registry's OWN
    partition (the landed shape), not by hand-built fake rows: a reader that
    walks only the current TOOL_REGISTRY sees exactly the retired set as stale.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

    real = mrt._registry_rows()
    current_names = {t.name for t in TOOL_REGISTRY}
    retired_names = {t.name for t in RETIRED_TOOL_REGISTRY}
    assert current_names and retired_names and not (current_names & retired_names), (
        "the registry's current/retired partition is not the shape this test drives"
    )
    # The LANDED shape a TOOL_REGISTRY-only reader has.
    current_only = [r for r in real if r["name"] in current_names]
    hint = mrt._rendezvous_hint(current_only)
    assert hint, "the landed TOOL_REGISTRY-only shape produced no rendezvous hint"
    assert "#4031" in hint and "RETIRED_TOOL_REGISTRY" in hint, (
        f"the rendezvous hint does not name the PR or the fix: {hint!r}"
    )
    # The real, full rows already include the retired names — no rendezvous.
    assert mrt._rendezvous_hint(real) is None, (
        "the full served registry (current + retired) was mislabelled as a rendezvous"
    )
    # An unrelated stale key must NOT be described as the #4031 rendezvous: a
    # current name dropped from the rows leaves the retired set stale PLUS one
    # more, so the stale set is no longer exactly the retired set.
    with_stray = [r for r in current_only if r["name"] != "tortoise_audit"]
    assert mrt._rendezvous_hint(with_stray) is None, (
        "an unrelated stale map key was mislabelled as the #4031 rendezvous"
    )


def test_rendezvous_predicate_does_not_depend_on_the_map_being_validated(
    monkeypatch,
) -> None:
    """The hint must fire on the registry SPLIT, not on the map's own keys.

    The pre-merge copy of the map named the `get` pair the other way (`tortoise_get`
    kept, `tortoise_get_entity` retired), so a predicate written as
    `stale <= set(RETIRED_USE_INSTEAD)` returns `None` for exactly the landing that
    happened — and the red that should read as *two lanes meeting* reads as a wall.
    Replacing the map with that pre-merge direction must not disarm the hint.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt
    from tortoise.tool_registry import TOOL_REGISTRY

    current_only = [
        r for r in mrt._registry_rows() if r["name"] in {t.name for t in TOOL_REGISTRY}
    ]
    monkeypatch.setattr(
        mrt, "RETIRED_USE_INSTEAD",
        {"tortoise_get_entity": 'tortoise_get(id, type="entity")'},
    )
    assert mrt._rendezvous_hint(current_only), (
        "the rendezvous hint depends on RETIRED_USE_INSTEAD, so the pre-merge map "
        "direction would read as a bug instead of a rendezvous"
    )


def test_validate_rejects_a_bad_redirect(monkeypatch) -> None:
    """A redirect that names no registry tool must fail the build.

    The map is live data, so it is the one input nothing else checks; without
    this a typo'd redirect renders an agreement row comparing a destination to
    `None`.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert mrt._validate(mrt._rows(mrt._registry_rows())) == [], (
        "the live map is not internally clean"
    )
    monkeypatch.setitem(mrt.RETIRED_USE_INSTEAD, "tortoise_get_point", "not_a_tool_call")
    errs = mrt._validate(mrt._rows())
    assert any("tortoise_get_point" in e for e in errs), (
        f"a redirect naming no tool was accepted: {errs!r}"
    )


def test_redirect_target_extracts_the_tool_name() -> None:
    """The redirect parser is the comparison's only oracle, so pin it on the map."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    for expr, want in [
        ('tortoise_get_entity(id, type="point")', "tortoise_get_entity"),
        ('tortoise_overview(section="tags")', "tortoise_overview"),
        ("tortoise_index_files(directory)", "tortoise_index_files"),
        ("nothing_here", None),
    ]:
        assert mrt._redirect_target(expr) == want, (
            f"_redirect_target({expr!r}) returned {mrt._redirect_target(expr)!r}, "
            f"expected {want!r}"
        )


# ── Every rendered count and provenance claim has an independent oracle ──────


def test_second_warning_shim_count_is_the_map_size() -> None:
    """The `... so the N `warning shim` names are` figure is recomputed, not free.

    One `warning shim` occurrence was guarded; the retirement paragraph's second
    occurrence was not, so it could be typed as any number with the suite green
    (P2-2).
    """
    m = re.search(r"so the (\d+) `warning shim` names are", _doc())
    assert m, (
        "the retirement paragraph's `... so the N `warning shim` names are` clause "
        "is missing"
    )
    assert int(m.group(1)) == len(RETIRED_LITERAL), (
        f"the retirement paragraph says {m.group(1)} names retire with a warning shim; "
        f"the registry map has {len(RETIRED_LITERAL)}"
    )


def test_b1_names_count_is_the_map_size() -> None:
    """B1's own `... for all N names` figure is recomputed, not free (P2-3)."""
    b1 = _doc().split("### B1")[1].split("## Reproduce")[0]
    m = re.search(r"agree on the target for all (\d+) names\.", b1)
    assert m, "B1's `... for all N names` clause is missing or changed shape"
    assert int(m.group(1)) == len(RETIRED_LITERAL), (
        f"B1 says the redirect agrees for all {m.group(1)} names; the map has "
        f"{len(RETIRED_LITERAL)}"
    )


def test_index_row_summary_matches_the_agreement() -> None:
    """`docs/00_index.md` restates the agreement as a count — it must be true.

    A caller reads the index row INSTEAD of the artifact, so a stale
    `(N of N agree, M findings)` there is a false summary (P2-4). The expected
    values come from the pinned literals, not from the row.
    """
    index = (ROOT / "docs" / "00_index.md").read_text(encoding="utf-8")
    rows = [ln for ln in index.splitlines() if "docs/product/mcp-rename-table.md" in ln]
    assert len(rows) == 1, "docs/00_index.md must carry exactly one MCP rename-table row"
    m = re.search(r"\((\d+) of (\d+) agree, (\d+) findings\)", rows[0])
    assert m, "the index row no longer states `(N of N agree, M findings)`"
    agree, total, findings = (int(g) for g in m.groups())
    assert (agree, total) == (len(RETIRED_LITERAL), len(RETIRED_LITERAL)), (
        f"the index row says {agree} of {total} agree; the map has {len(RETIRED_LITERAL)}"
    )
    assert findings == len(DISAGREEMENTS_LITERAL), (
        f"the index row says {findings} findings; the pinned set has "
        f"{len(DISAGREEMENTS_LITERAL)}"
    )


def test_generator_names_only_existing_source_paths() -> None:
    """Every source path the generator's prose names must resolve on this branch.

    The rule's provenance used to cite `tools/sdk_rename_table.py::_maximal` —
    a file that is neither on this branch nor on `main` — and
    `tools/bridge_table.py`, which carries no `_maximal` (P2-6). A provenance
    claim a reader of this branch cannot resolve is the defect class this
    artifact exists to remove.
    """
    src = GENERATOR.read_text(encoding="utf-8")
    named = set(re.findall(r"(?:tools|tortoise)/[a-z0-9_/]+\.py", src))
    assert named, "the generator names no source path — did its prose change shape?"
    missing = sorted(p for p in named if not (ROOT / p).is_file())
    assert not missing, (
        f"the generator cites source paths that do not exist on this branch: {missing}"
    )
    assert "sdk_rename_table" not in src, (
        "the generator cites `tools/sdk_rename_table.py` as its rule's prior art, but "
        "that file is not on this branch or on `main`"
    )
    assert "bridge_table.py`'s `_maximal`" not in src, (
        "the generator cites `tools/bridge_table.py`'s `_maximal`, but that module "
        "carries no `_maximal`"
    )


def test_docs_only_edit_of_the_generated_doc_is_unguarded_by_ci() -> None:
    """The ci-surfaces note must not overstate a guard that does not exist.

    A docs-only edit of the GENERATED document selects NO surface (#4454), so
    this test file does not run — and no workflow runs the generator's `--check`
    standalone. The `core` note once claimed the guard was "the `api` arm plus
    the `--check` drift form"; both halves are false for a docs-only change
    (P2-7). This pins the real selection behaviour and forbids the claim.
    """
    sys.path.insert(0, str(ROOT))
    from tools.ci_selection import load_manifest, select

    sel = select(["docs/product/mcp-rename-table.md"], "pull_request", load_manifest())
    assert sel["surfaces"] == [], (
        f"a docs-only edit now selects {sel['surfaces']!r} — the ci-surfaces note "
        "may need updating"
    )
    assert "test_mcp_rename_table.py" not in sel["test_files"], (
        "the docs-only selection now runs this file — the note can be corrected upward"
    )

    workflows = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
    assert workflows, "no workflows found — the standalone `--check` claim cannot be checked"
    runners = [w.name for w in workflows if "mcp_rename_table.py --check" in w.read_text()]
    assert not runners, f"a workflow now runs the standalone check: {runners}"

    note = (ROOT / "config" / "ci-surfaces.yml").read_text(encoding="utf-8")
    m = re.search(r"((?:^  #.*\n)+)  - test_mcp_rename_table\.py", note, re.M)
    assert m, "the `core` note above test_mcp_rename_table.py is missing"
    # Strip the comment markers and collapse whitespace FIRST: a re-worded but
    # equivalent false claim must not slip through by being wrapped differently.
    flat = re.sub(
        r"\s+", " ",
        " ".join(re.sub(r"^\s*#\s?", "", ln) for ln in m.group(1).splitlines()),
    )
    assert "#4454" in flat, "the `core` note no longer carries the #4454 docs-only-gap qualifier"
    assert "selects no surface" in flat, (
        "the `core` note no longer states that a docs-only change selects no surface"
    )
    assert "the `api` arm plus the `--check` drift form" not in flat, (
        "the `core` note again claims the `--check` drift form guards a docs-only "
        "change — no workflow runs it standalone"
    )


def test_duration_weight_matches_the_sibling_generator_suite() -> None:
    """The durations weight is a defensible estimate, not a stale reading.

    `test_bridge_table.py` and this suite pay the same shared-conftest + generator
    import cost, so their weights must agree. The comment that shipped claimed
    "254s wall"; measured here, this file runs 7.1-11.6 s in-suite (10.3-15.7 s
    wall incl. interpreter start) across 8 runs on 2026-09-21 at load average
    34-38 (P2-8). The superseded reading must not be restated as fact.
    """
    import yaml

    manifest = yaml.safe_load((ROOT / "config" / "ci-surfaces.yml").read_text())
    durations = manifest.get("durations") or {}
    assert "test_mcp_rename_table.py" in durations, "the durations entry is missing"
    assert durations["test_mcp_rename_table.py"] == durations["test_bridge_table.py"], (
        "the two generator suites pay the same import cost, so their weights must "
        f"agree: {durations['test_mcp_rename_table.py']} vs "
        f"{durations['test_bridge_table.py']}"
    )
    m = re.search(
        r"((?:^  #.*\n)+)  test_mcp_rename_table\.py:",
        (ROOT / "config" / "ci-surfaces.yml").read_text(encoding="utf-8"),
        re.M,
    )
    assert m, "the durations comment above the entry is missing"
    comment = m.group(1)
    assert "load" in comment, (
        "the durations comment must state the condition the measurement was taken "
        "under — a bare wall number is not reproducible"
    )
    assert "254" not in comment, (
        "the superseded `254s` reading is restated; it measures ~10s on this box"
    )


# ── Drift gate ───────────────────────────────────────────────────────────────

def test_mcp_rename_table_not_drifted() -> None:
    """The committed table matches what the generator produces now."""
    import subprocess

    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        "The MCP rename table is stale, or the map disagrees with the registry. "
        "Regenerate with:\n"
        "    uv run python tools/mcp_rename_table.py\n"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_check_exits_nonzero_on_drift(monkeypatch, tmp_path) -> None:
    """`--check` must FAIL on drift, not merely succeed when clean.

    The sibling suite's only `--check` test asserted a zero exit, so a mutation
    making `main()` return 0 unconditionally — disarming the entire drift gate —
    would have passed. This is the negative arm.

    The drift is written to a TEMP copy and `main()` is called IN-PROCESS: the
    earlier version appended `DRIFT` to the tracked generated document and
    restored it only in a `finally`, so a killed or timed-out run left the
    artifact dirty (and a subsequent run then "restored" the dirt).
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    drifted = tmp_path / "mcp-rename-table.md"
    drifted.write_text(_doc() + "\nDRIFT\n", encoding="utf-8")
    monkeypatch.setattr(mrt, "OUT", drifted)
    monkeypatch.setattr(sys, "argv", ["mcp_rename_table.py", "--check"])
    assert mrt.main() != 0, (
        "--check exited 0 on a deliberately drifted document; the drift gate is disarmed"
    )

    clean = tmp_path / "clean.md"
    clean.write_text(_doc(), encoding="utf-8")
    monkeypatch.setattr(mrt, "OUT", clean)
    assert mrt.main() == 0, "--check must still pass on the un-drifted document"
