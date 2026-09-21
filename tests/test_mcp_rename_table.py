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

# The last line of each citation the generator declares is the clause a
# right-truncation drops. Pinned as LITERALS: a tail-drop lands on a line boundary,
# which `_maximal` accepts by design, so only an explicit tail pin can see it.
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

    The PR number and SHA are pinned as LITERALS here, and the generator renders
    them from ONE source, so a second typed copy cannot drift with the suite
    green. The dead branch ref must be gone: the map is read live, not pinned to
    a branch that no longer exists.
    """
    doc = _doc()
    # The provenance paragraph, derived from the generator's single source.
    assert f"PR #{PR_NUMBER_LITERAL} MERGED as `{PR_MERGE_SHA_LITERAL}`" in doc, (
        f"the document does not state that PR #{PR_NUMBER_LITERAL} MERGED as "
        f"`{PR_MERGE_SHA_LITERAL}` — the paragraph's provenance is not the merged PR"
    )
    # EXACT, not substring: `PR #4031` must not be a prefix of another number.
    m = re.search(r"PR #(\d+) MERGED", doc)
    assert m, "the document no longer records the merge as `PR #NNNN MERGED`"
    assert int(m.group(1)) == PR_NUMBER_LITERAL, (
        f"the merge paragraph names PR #{m.group(1)}, expected #{PR_NUMBER_LITERAL}"
    )
    assert "`e3bb78a14`" in doc, "the merge SHA is not rendered as one exact token"
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


# ── The citation guard: a quote must be verbatim AND maximal ─────────────────

def test_maximal_rejects_a_mid_clause_truncation() -> None:
    """`_maximal` is the rule shape `sdk_rename_table`/`bridge_table` use, pinned."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    text = "alpha beta gamma. delta epsilon\nzeta | tail\n"
    assert mrt._maximal("alpha beta gamma. delta epsilon", text) is True  # line end
    assert mrt._maximal("zeta |", text) is True  # cell/row boundary
    assert mrt._maximal("alpha beta", text) is False, (
        "a mid-clause truncation was accepted — the clause that can contradict the "
        "row is exactly what a truncation drops"
    )
    # A sentence end is NOT a boundary: the NEXT sentence is where a contradiction
    # begins, which is exactly the defect this rule exists to catch (the sibling
    # `sdk_rename_table` tightened `_maximal` to reject it for that reason). The
    # sentence here ends MID-LINE, which is the shape a truncation actually takes.
    assert mrt._maximal("alpha beta gamma.", text) is False, (
        "a sentence-end truncation was accepted — the contradictory clause is the "
        "next sentence"
    )
    assert mrt._maximal("absent text", text) is True, (
        "absent text must pass here: CITATION DRIFT is reported by its own check"
    )


def test_committed_citations_are_verbatim_and_maximal() -> None:
    """Every citation the generator declares is present in full in the registry."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert mrt.CITES, "the generator declares no citation — the guard is unarmed"
    assert mrt._citation_errors() == [], (
        "a committed citation has drifted from the registry or been truncated"
    )


def test_declared_citations_reach_their_final_clause_in_the_document() -> None:
    """Each citation's LAST clause is rendered, so a right-truncation reds.

    The expected set and tails are LITERALS: a truncation that lands on a line
    boundary is accepted by `_maximal` (a line end is a region boundary), so an
    assertion that read the tail from the generator would move with the mutation
    and see nothing.
    """
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    assert set(mrt.CITES) == set(CITATION_TAIL_LITERAL), (
        "the declared citation set changed:\n"
        f"  declared: {sorted(mrt.CITES)}\n"
        f"  pinned:   {sorted(CITATION_TAIL_LITERAL)}"
    )
    doc = _doc()
    for key, tail in CITATION_TAIL_LITERAL.items():
        assert mrt.CITES[key][1].endswith(tail), (
            f"{key}'s declared quote no longer reaches its final clause {tail!r}"
        )
        assert f"> {tail}" in doc, (
            f"{key}'s final clause is not rendered in the document — a right-truncation "
            "drops exactly this clause"
        )


def test_build_fails_on_a_truncated_citation(monkeypatch, capsys) -> None:
    """A citation cut mid-clause must FAIL the build, not render as evidence."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    doc, quote = mrt.CITES["retired_behaviour"]
    # Stop on the fourth line, mid-clause: the registry continues
    # " AND warns the caller, naming the replacement." on the same line.
    truncated = "\n".join([*quote.splitlines()[:3],
                            "a shim that answers exactly as the live tool did"])
    assert truncated in mrt._registry_comment_text(), (
        "the truncation used by this test is not a real substring — it would red for "
        "DRIFT, not for TRUNCATION"
    )
    monkeypatch.setitem(mrt.CITES, "retired_behaviour", (doc, truncated))
    errs = mrt._citation_errors()
    assert any("TRUNCATED CITATION" in e for e in errs), (
        f"a mid-clause truncation was accepted by the guard: {errs!r}"
    )
    # The guard must be WIRED INTO the build, not merely available: `_validate`
    # has to surface it, or the CI form exits 0 with the defect.
    assert any(
        "TRUNCATED CITATION" in e for e in mrt._validate(mrt._registry_rows())
    ), "the citation guard is not wired into `_validate`"
    monkeypatch.setattr(sys, "argv", ["mcp_rename_table.py", "--check"])
    rc = mrt.main()
    err = capsys.readouterr().err
    assert rc == 1, "the generator exited 0 with a truncated citation"
    assert "TRUNCATED CITATION" in err, (
        "the build failed, but not for the truncated citation — the check is not wired "
        f"into `_validate` (stderr was {err!r})"
    )


def test_build_fails_on_a_drifted_citation(monkeypatch) -> None:
    """A citation whose source text is gone must FAIL the build as DRIFT."""
    sys.path.insert(0, str(ROOT))
    import tools.mcp_rename_table as mrt

    monkeypatch.setitem(
        mrt.CITES, "owner_decision",
        ("tortoise/tool_registry.py", "a sentence the registry does not contain"),
    )
    errs = mrt._citation_errors()
    assert any("CITATION DRIFT" in e for e in errs), (
        f"a citation absent from the registry was accepted: {errs!r}"
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
