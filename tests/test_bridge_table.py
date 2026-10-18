"""The bridge table must not drift from the code it describes (#4282).

`tools/bridge_table.py` reads every `file:line` it emits straight from the AST, so
a citation in the generated doc cannot be wrong. What it CAN still do is go
*stale* — someone edits `tool_registry.py` or `sdk.py` and never regenerates —
and that is exactly the drift the generator's own docstring promises cannot
happen. Nothing ran `--check`, so the promise was unenforced.

This is the enforcement, and since #4454 it is doubled: the ordinary suite runs
it on any PR that selects `core` or `api`, and the required `docs` job runs
`tools/bridge_table.py --check` on EVERY PR — including the docs-only edits this
test cannot see. The generator imports cleanly with no database, no API key and
no network, which is why it can run in both places. It is registered in BOTH `api` and `core` in
`config/ci-surfaces.yml`, and `tools/bridge_table.py` is named in the `api`
SOURCE_PATTERNS — because `tools/` and `docs/` are in NON_PYTHON_PREFIXES, an
edit to the generator alone used to select NO surface, so the gate did not run on
the PR that can break it. A change touching ONLY the docs still skips the matrix
by the repo's deliberate docs-PR policy; that direction is covered by the `docs`
job's `--check` instead, which is why the step has to live there rather than here
(#4454).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "bridge_table.py"


def test_target_mcp_matches_the_beta_doc() -> None:
    """The MCP target set is declared in TWO places; they must be equal.

    This is the round-2 P1 of the PR #4177 review, and it is the *same* defect
    class round 1 found: two sets that both said the same number while being different
    sets. The round-1 fix made them agree by hand and added no test, so a
    one-token edit to `TARGET_MCP` re-diverged them behind a green gate.

    The beta doc is the authority the owner reviews; the generator is the
    authority that renders. Neither may drift from the other unnoticed.
    """
    beta = (ROOT / "docs" / "product" / "beta-sdk-surface.md").read_text(encoding="utf-8")

    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import TARGET_MCP

    # The beta doc carries one markdown row per SDK method: `| # | Name | Does |
    # MCP twin | Who needs it |`. The twin column is located BY HEADER, not by
    # position -- an earlier version used cells[-1], which is `Who needs it`.
    twins: set[str] = set()
    twin_col: int | None = None
    for line in beta.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if twin_col is None:
            # Locate the column by its header, so a reordered table cannot make
            # this test silently compare the wrong column.
            for i, cell in enumerate(cells):
                if cell.lower() == "mcp twin":
                    twin_col = i
            continue
        if len(cells) <= twin_col:
            continue
        for name in re.findall(r"`([a-z_][a-z0-9_]*)`", cells[twin_col]):
            twins.add(name)
    assert twin_col is not None, (
        "the beta doc has no `MCP twin` column header - this test cannot bind the two target sets"
    )

    assert twins, (
        "parsed no MCP twin names from the beta doc's `MCP twin` column - did its "
        "header text or table shape change? This test is the only thing binding the two target sets."
    )
    # The beta doc's method table is one row per SDK METHOD, so a target that has NO
    # SDK twin cannot appear in it. Ruling B (2026-10-12, on #3540) re-admitted
    # exactly one such tool, `manage_sources`, and the doc declares it in its own
    # table — so it is read from the DOC, not from a literal here. (A literal is the
    # hand-maintained constant this test exists to eliminate; it is what broke when
    # the owner's ruling took the target from 25 to 26.)
    mcp_only: set[str] = set()
    in_mcp_only = False
    for line in beta.splitlines():
        if line.startswith("| MCP-only addition"):
            in_mcp_only = True
            continue
        if in_mcp_only:
            if not line.startswith("|"):
                in_mcp_only = False
                continue
            if not line.strip().strip("|").strip():
                continue  # the `|---|---|` separator
            mcp_only.update(re.findall(r"`([a-z_][a-z0-9_]*)`", line))
    twins = twins | mcp_only
    declared = set(TARGET_MCP)
    # `set()` hides a DUPLICATE entry, which inflates `len(TARGET_MCP)` and every
    # count rendered from it while the set-equality above still passes. Anchored
    # by a second, independent declaration of the same number: the vision doc's
    # reconciliation row.
    assert len(TARGET_MCP) == len(declared), (
        f"TARGET_MCP has {len(TARGET_MCP)} entries but only {len(declared)} distinct "
        "names -- a duplicate silently inflates every rendered tool count"
    )
    vision = (ROOT / "docs" / "product" / "vision-mcp-sdk-surface.md").read_text(encoding="utf-8")
    vision_count = re.search(r"^\| MCP target tools \| \*\*(\d+)\*\* \|$", vision, re.M)
    assert vision_count, "the vision doc has no `MCP target tools` reconciliation row"
    assert int(vision_count.group(1)) == len(TARGET_MCP), (
        f"the vision doc says the MCP target has {vision_count.group(1)} tools, "
        f"TARGET_MCP has {len(TARGET_MCP)}"
    )
    if twins != declared:
        raise AssertionError(
            "the generator's TARGET_MCP and the beta doc's MCP column disagree.\n"
            f"  only in generator ({len(declared - twins)}): {sorted(declared - twins)}\n"
            f"  only in beta doc ({len(twins - declared)}): {sorted(twins - declared)}"
        )
    # Guard against an empty parse only. The size is NOT asserted here: a literal
    # here is the same hand-maintained constant this test exists to eliminate, and
    # it is what broke when the owner's ruling took the target from 25 to 26.
    assert declared, "parsed TARGET_MCP is empty -- the parse broke, not the set"


def _sdk_defs_independently() -> set[str]:
    """Public method names on `TortoiseSDK`, derived here without the generator.

    `tools.bridge_table._sdk_targets()` is the implementation under test, so a
    test that called it would be checking the generator against itself. This
    walks the AST directly.
    """
    import ast

    tree = ast.parse((ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "TortoiseSDK":
            return {
                s.name for s in node.body
                if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise AssertionError("no `class TortoiseSDK` found in tortoise/sdk.py")


def test_c2_lists_exactly_the_bindings_that_do_not_resolve() -> None:
    """C2 must list every unresolvable `sdk_method`, and nothing else.

    Round 4 of the #4177 review found this section unguarded: replacing the
    blocker function with one that returns `[]` for the C2 half renders
    "None." where five findings belong, and **all eight tests still pass** -
    because no test reads C2 at all and `--check` compares the doc to the same
    mutated render. A "find the declaration defects" section that can silently
    empty itself is worse than absent.

    Expected values are derived from the registry here, not from the generator.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import _registry_rows

    defs = _sdk_defs_independently()
    expected = {
        r["name"]: r["sdk_method"]
        for r in _registry_rows()
        if r["sdk_method"] and r["sdk_method"] not in defs
    }
    assert expected, "no unresolvable bindings found - the registry may have changed"

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    # Take the C2 section up to the next heading, then match its table rows. Do
    # NOT split on a blank line first: the table's header row is itself preceded
    # by one, so that truncates to the section title and silently parses nothing.
    c2 = doc.split("### C2")[1]
    c2 = re.split(r"\n###? ", c2)[0]
    rows = re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| `([A-Za-z_][A-Za-z0-9_]*)` \| `tool_registry\.py:(\d+)` \|",
        c2, re.M,
    )
    listed = {name: binding for name, binding, _ in rows}

    assert listed == expected, (
        "Part C2 does not list exactly the bindings that fail to resolve.\n"
        f"  listed but resolvable: {sorted(set(listed) - set(expected))}\n"
        f"  missing from C2 ({len(set(expected) - set(listed))}): {sorted(set(expected) - set(listed))}"
    )

    # The `Source` line numbers are generated too, so assert them against the
    # registry rather than only checking that *a* number is present.
    import ast

    tree = ast.parse((ROOT / "tortoise" / "tool_registry.py").read_text(encoding="utf-8"))
    truth: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id != "ToolDefinition":
                continue
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    truth[kw.value.value] = node.lineno
    bad_lines = [
        f"{name}: cited :{line}, defined at :{truth.get(name)}"
        for name, _, line in rows
        if truth.get(name) != int(line)
    ]
    assert not bad_lines, "Part C2 cites the wrong line for:\n  " + "\n  ".join(bad_lines)


def test_part_b_line_numbers_point_at_the_right_tool() -> None:
    """Every `tool_registry.py:N` citation must equal the tool's real definition line.

    This closes the artifact's *central* promise, which nothing guarded: the doc
    says every `file:line` is read from the source at build time so it cannot
    drift. A constant offset added to every emitted line number makes all 98
    citations wrong while every other test stays green.

    The check derives the truth **here**, by parsing `ToolDefinition(` calls out
    of `tool_registry.py` and mapping each `name=` to its call's line, so the
    citation is compared against an independent oracle rather than a second
    reading of the generator's map.

    An earlier version of this test scanned a forward window for the tool's
    `name=` and passed for any *negative* offset from -1 to -13 (a citation into
    a neighbouring tool's definition still finds the right name ahead of it), so
    it enforced only half of what it claimed. Exact equality has no such gap.
    """
    import ast

    registry = ROOT / "tortoise" / "tool_registry.py"
    tree = ast.parse(registry.read_text(encoding="utf-8"))
    truth: dict[str, int] = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "ToolDefinition":
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                truth[kw.value.value] = node.lineno
    assert truth, "no `ToolDefinition(name=...)` calls found in tool_registry.py"

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    part_b = doc.split("## Part B")[1].split("## Part C")[0]
    rows = re.findall(
        r"^\| \d+ \| `([a-z_][a-z0-9_]*)` \| `tool_registry\.py:(\d+)` \|", part_b, re.M
    )
    assert len(rows) == len(truth), (
        f"Part B cites {len(rows)} tools but the registry defines {len(truth)}"
    )

    wrong = [
        f"{name}: cited tool_registry.py:{cited}, defined at :{truth[name]}"
        for name, cited in rows
        if truth.get(name) != int(cited)
    ]
    assert not wrong, (
        "Part B cites the wrong line for these tools; every citation in this "
        "document is supposed to be read from the AST at build time:\n  "
        + "\n  ".join(wrong[:10])
    )


def test_part_a_exists_column_is_derived_from_the_ast() -> None:
    """The `Exists` column must be computed, not asserted (#4177 round 3, P1).

    A third review round emptied the C1 blocker list and hard-coded the `Exists`
    column to `yes` in the generator, regenerated, and watched **all five tests
    pass** while the document contradicted itself - Part A said every target had
    a method while its own summary and C1 said 15 did not.

    Round 1 fixed the generator's existence logic; round 2 guarded the *merge*
    predicate. Neither guarded existence, which is the artifact's whole point.
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    part_a = doc.split("## Part A")[1].split("## Part B")[0]
    defs = _sdk_defs_independently()

    rows = re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| \d+ \| .*? \| (.+?) \|$", part_a, re.M
    )
    assert rows, "Part A rendered no rows with an Exists cell"
    for name, exists_cell in rows:
        # A namespaced target (`sdk:foo`) names the method after the colon.
        method = name.split(":", 1)[1] if name.startswith("sdk:") else name
        should_exist = method in defs
        says_yes = exists_cell.strip().startswith("yes")
        assert says_yes == should_exist, (
            f"Part A says Exists='{exists_cell}' for {name}, but {method} is "
            f"{'present' if should_exist else 'ABSENT'} on TortoiseSDK"
        )


def test_c1_blockers_are_exactly_the_targets_without_a_method() -> None:
    """C1 must list every target with no method - no more, no fewer.

    This is the second half of the round-3 P1: the reviewer emptied the blocker
    list, C1 fell from 25 findings to 3, and the suite stayed green. A "find the
    gaps" artifact that silently stops finding gaps is worse than none.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION, MCP_BACKING, NAMESPACES, TARGET_MCP

    defs = _sdk_defs_independently()

    # Every target the map names, from all three scopes. A target's EXISTENCE is
    # decided by its backing method (MCP_BACKING), matching the generator.
    targets: set[str] = set()
    for t in TARGET_MCP:
        targets.add(MCP_BACKING.get(t, t))
    for dest in DESTINATION.values():
        ns = next((n for n in NAMESPACES if dest.startswith(n)), None)
        targets.add(dest[len(ns):] if ns else dest)
    targets.discard("REMOVED")

    # The backing map is load-bearing authored data and MUST NOT be imported as
    # its own oracle: mutating it to `graph_set_recording: graph_set_recording`
    # silently dropped the `update_memory_graph` finding (26 rows -> 25) and the
    # suite stayed green. The independent oracle is the two docs that state the
    # same design fact.
    from tools.bridge_table import MCP_BACKING as _backing
    assert _backing == {"graph_set_recording": "update_memory_graph"}, (
        f"MCP_BACKING is {_backing!r}; docs/product/beta-sdk-surface.md row 30 and "
        "docs/product/canonical-mcp-tools.md:137 both state the override folds into "
        "`update_memory_graph`"
    )

    expected = {t for t in targets if t not in defs}
    assert expected, "no target lacks a method - the artifact may be stale"

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    c1 = doc.split("### C1")[1].split("### C2")[0]
    # `Where` is one of MCP / tenancy / sdk-only, so it may contain a hyphen.
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| [A-Za-z-]+ \|", c1, re.M))

    # `Where` must name the target's real scope, not merely be well-formed.
    def scope_of(target: str) -> str:
        for dest in DESTINATION.values():
            ns = next((n for n in NAMESPACES if dest.startswith(n)), None)
            if (dest[len(ns):] if ns else dest) == target and dest != "REMOVED":
                if ns == "tenancy:":
                    return "tenancy"
                if ns == "sdk:":
                    return "sdk-only"
                return "MCP"
        return "MCP"  # a TARGET_MCP entry with no source today

    where = dict(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| ([A-Za-z-]+) \|", c1, re.M))
    mismatched = [
        f"{t}: doc says {where[t]!r}, map says {scope_of(t)!r}"
        for t in sorted(listed) if where.get(t) != scope_of(t)
    ]
    assert not mismatched, "Part C1 mislabels the scope of:\n  " + "\n  ".join(mismatched)

    assert listed == expected, (
        "Part C1 does not list exactly the targets with no method.\n"
        f"  listed but implemented: {sorted(listed - expected)}\n"
        f"  missing from C1 ({len(expected - listed)}): {sorted(expected - listed)}"
    )

    # The Finding column is prose, but it is the section's whole claim: rewriting
    # it to "method exists but is not exported" -- the OPPOSITE of the heading --
    # left every row misstated with the suite green.
    # Assert the LITERAL, NOT `from tools.bridge_table import C1_FINDING`: the
    # generator renders that constant, so importing it makes the assertion
    # self-referential -- editing the constant moved both sides together and
    # stated the OPPOSITE of the section heading with the suite green.
    C1_FINDING = "no `def` on TortoiseSDK"
    findings = re.findall(r"^\| `[a-z_][a-z0-9_]*` \| [A-Za-z-]+ \| (.+?) \|$", c1, re.M)
    assert findings, "Part C1's Finding column did not parse - the surface is unguarded"
    assert len(findings) == len(listed), (
        f"C1 has {len(listed)} rows but {len(findings)} Findings"
    )
    assert set(findings) == {C1_FINDING}, (
        f"C1's Finding says {sorted(set(findings))!r}, expected {C1_FINDING!r} on every row"
    )


def test_part_a_merged_set_is_recomputed_independently() -> None:
    """Part A's merged set is derived, so derive it a second way and compare.

    Round-2 P2-3 of the PR #4177 review: all three tests passed when the
    generator's merge predicate was changed from `len(v) > 1` to `len(v) >= 1`,
    which re-introduced round 1's exact bug (single-source targets listed as
    merged, including `record_decision`). The tests only proved the document
    matched the generator, never that the generator was right.

    This recomputes the merged set from the destination map without calling the
    generator's own predicate, and pins the three facts the document asserts.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION, DISCRIMINATORS, TARGET_MCP, _registry_rows

    sources: dict[str, int] = {}
    for dest in DESTINATION.values():
        sources[dest] = sources.get(dest, 0) + 1

    merged = {d for d, n in sources.items() if n > 1 and d != "REMOVED"}
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    defs = _sdk_defs_independently()

    assert merged, "the merged set is empty -- every destination has one source, which cannot be right"
    # Every merged target must appear in Part A, and nothing else may.
    part_a = doc.split("## Part A")[1].split("## Part B")[0]
    # LITERAL again -- see the C1 Finding note below.
    C1_REF = "**no — Part C1**"
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| \d+ \|", part_a, re.M))
    assert listed, "Part A rendered no rows - did its table shape change?"
    assert listed == merged, (
        "Part A does not list exactly the merged targets.\n"
        f"  listed but not merged: {sorted(listed - merged)}\n"
        f"  merged but not listed: {sorted(merged - listed)}"
    )
    # The summary sentence is generated from the same numbers, so assert it too:
    # a mutated count would otherwise regenerate a self-consistent wrong document.
    summary = re.search(
        r"\*\*(\d+) merged targets\. (\d+) of them have a method behind them today\.\*\*"
        r" The other \*\*(\d+)\*\*",
        doc,
    )
    assert summary, "Part A's summary sentence is missing or changed shape"
    n_merged, n_ok, n_remaining = (int(g) for g in summary.groups())
    assert n_merged == len(merged), f"summary says {n_merged} merged, table lists {len(merged)}"
    truth_ok = sum(1 for t in merged if (t.split(":", 1)[1] if t.startswith("sdk:") else t) in defs)
    assert n_ok == truth_ok, f"summary says {n_ok} implemented, {truth_ok} are"
    assert n_remaining == len(merged) - truth_ok, (
        f"summary says {n_remaining} remaining, {len(merged) - truth_ok} are"
    )

    # The "Sources absorbed" column is a count, so a rendering mutation would
    # produce a self-consistent-looking but wrong number. The presence check is
    # not decorative: without it an empty match skips the loop entirely and the
    # surface goes silently unguarded (found by the round-11 verifier).
    counts_in_doc = dict(re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| (\d+) \| .*? \| .*? \|$", part_a, re.M
    ))
    assert counts_in_doc, "Part A's `Sources absorbed` column did not parse - the surface is unguarded"
    assert len(counts_in_doc) == len(merged), (
        f"Part A's counts column has {len(counts_in_doc)} rows, the merged set has {len(merged)}"
    )
    for target, rendered in counts_in_doc.items():
        assert int(rendered) == sources.get(target, 0), (
            f"Part A says {target} absorbed {rendered} tools, the map says {sources.get(target, 0)}"
        )

    # The `Discriminator` column is the ONE authored cell in Part A, so it is also
    # the only one no computation can catch: overwriting every row with the same
    # placeholder left all tests green and the design fact silently unreachable.
    discs_in_doc = dict(re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| \d+ \| (.+?) \| .+? \|$", part_a, re.M
    ))
    assert discs_in_doc, "Part A's `Discriminator` column did not parse - the surface is unguarded"
    assert set(discs_in_doc) == set(counts_in_doc), (
        "Part A's Discriminator column and its counts column cover different rows"
    )
    # The `Exists` cell's cross-reference is a claim about WHERE the finding is
    # listed: pointing at C2 (unresolvable bindings) instead of C1 (missing
    # methods) was green, because the old check only tested `startswith("yes")`.
    exists_in_doc = dict(re.findall(
        r"^\| `([a-z_][a-z0-9_]*)` \| \d+ \| .+? \| (yes|.+?) \|$", part_a, re.M
    ))
    assert exists_in_doc, "Part A's `Exists` column did not parse - the surface is unguarded"
    for target, rendered in exists_in_doc.items():
        assert rendered in ("yes", C1_REF), (
            f"Part A says {target} exists={rendered!r}; expected 'yes' or {C1_REF!r}"
        )

    for target, rendered in discs_in_doc.items():
        want = DISCRIMINATORS.get(target, "*(none — dispatch is by argument)*")
        assert rendered.strip() == want, (
            f"Part A says {target}'s discriminator is {rendered.strip()!r}, the map says {want!r}"
        )

    # The destination-counts table must agree with the same map, and sum to the
    # registry size. Assert presence rather than guarding with `if`, so a
    # generator that stops emitting the table reds this test instead of skipping it.
    dest_rows = dict(re.findall(r"^\| `([A-Za-z_:]+)` \| (\d+) \|$", doc, re.M))
    assert dest_rows, "the destination-counts table did not parse - the surface is unguarded"
    assert len(dest_rows) == len(sources), (
        f"the counts table has {len(dest_rows)} rows, the map has {len(sources)} destinations"
    )
    n_registry = len(_registry_rows())
    assert sum(int(v) for v in dest_rows.values()) == n_registry, (
        f"the destination-counts table does not sum to {n_registry}: "
        f"{sum(int(v) for v in dest_rows.values())}"
    )
    for dest, rendered in dest_rows.items():
        assert int(rendered) == sources.get(dest, 0), (
            f"the counts table says {dest} has {rendered} sources, the map says {sources.get(dest, 0)}"
        )

    # The rendered total cell and the prose counts are counts too, and both were
    # unguarded: mutating only the total line passed every test.
    total_cell = re.search(r"^\| \*\*total\*\* \| \*\*(\d+)\*\* \|$", doc, re.M)
    assert total_cell, "the destination-counts table has no `**total**` row"
    assert int(total_cell.group(1)) == n_registry, (
        f"the counts table's total cell says {total_cell.group(1)}, the registry has {n_registry}"
    )
    prose = re.search(r"Destination rows: \*\*(\d+)\*\*\. Registry tools: \*\*(\d+)\*\*\.", doc)
    assert prose, "the `Destination rows / Registry tools` prose counts are missing"
    assert int(prose.group(1)) == len(dest_rows), (
        f"prose says {prose.group(1)} destination rows, the table has {len(dest_rows)}"
    )
    assert int(prose.group(2)) == n_registry, (
        f"prose says {prose.group(2)} registry tools, there are {n_registry}"
    )

    # The specific regression both prior rounds found.
    assert "record_decision" not in listed, (
        "record_decision has one source, so it is not merged, and must not be in Part A"
    )
    assert "check_confidence" in listed, "check_confidence has 7 sources and must be in Part A"
    # A target with no source at all is net-new and cannot be merged by definition.
    for name in TARGET_MCP:
        if name not in sources:
            assert name not in listed, f"{name} has no source tool but is listed as merged"


def test_bridge_table_not_drifted() -> None:
    """The committed bridge table matches what the generator produces now."""
    proc = subprocess.run(
        [sys.executable, str(GENERATOR), "--check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, (
        "The bridge table is stale or the destination map disagrees with the "
        "registry. Regenerate with:\n"
        "    uv run python tools/bridge_table.py\n"
        f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )


def test_vision_doc_buckets_match_generated_doc() -> None:
    """The vision doc restates the generator's buckets, so they must agree.

    This is the round-2 finding of the PR #4177 review: the absorbed figure was
    updated in the vision doc's reconciliation table but the retired figure was
    left at its pre-fix value, so the table summed to 108 against its own
    Registry row of 98. Hand-copied numbers drift; this fails when they do.
    """
    import re

    from tools.bridge_table import TARGET_MCP

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    vision = (ROOT / "docs" / "product" / "vision-mcp-sdk-surface.md").read_text(encoding="utf-8")

    m = re.search(
        r"Registry: (\d+) tools → (\d+) absorbed into the (\d+) MCP targets · "
        r"(\d+) absorbed into a builder-only SDK method \(not on the MCP\) · "
        r"(\d+) tenancy \(SDK/REST only\) · (\d+) retired\.",
        doc,
    )
    assert m, "could not parse the generated headline; did its wording change?"
    total, mcp, mcp_targets, sdk_only, tenancy, retired = (int(g) for g in m.groups())
    assert mcp + sdk_only + tenancy + retired == total, "generated buckets do not sum"
    # The target count in the headline was a `\d+` wildcard, so it was free: the
    # headline could say "99 MCP targets" while the next sentence said 26.
    assert mcp_targets == len(TARGET_MCP), (
        f"the headline says {mcp_targets} MCP targets, TARGET_MCP has {len(TARGET_MCP)}"
    )

    # The SECOND headline sentence restates the same four numbers in prose, and
    # nothing read it: bumping one of them there left line 10 and line 12 (and the
    # vision doc) disagreeing, green.
    m2 = re.search(
        r"The MCP has \*\*(\d+)\*\* tools; "
        r"\*\*(\d+)\*\* current tools retire, \*\*(\d+)\*\* are tenancy-only, "
        r"\*\*(\d+)\*\* is absorbed into a builder-only SDK method that is not on the MCP, "
        r"and \*\*(\d+)\*\* are absorbed into those "
        r"(\d+) — many-to-one\. "
        r'Writing "(\d+) minus (\d+) equals (\d+) retired"',
        doc,
    )
    assert m2, "could not parse the headline's second sentence; did its wording change?"
    mcp2, retired2, tenancy2, sdk_only2, on_mcp2, those2, tot3, minus3, eq3 = (
        int(g) for g in m2.groups()
    )
    assert (mcp2, retired2, tenancy2, sdk_only2, on_mcp2) == (
        mcp_targets, retired, tenancy, sdk_only, mcp
    ), (
        "the headline's second sentence disagrees with its first: "
        f"got {(mcp2, retired2, tenancy2, sdk_only2, on_mcp2)}, "
        f"expected {(mcp_targets, retired, tenancy, sdk_only, mcp)}"
    )
    # The sentence's TAIL restates the target count a third time, and its worked
    # "X minus Y equals Z" example is computed arithmetic in prose -- `Z` was
    # free: bumping it rendered "98 minus 26 equals 73", contradicting the same
    # sentence's own 14, green.
    assert those2 == mcp_targets, (
        f"the sentence's tail says {those2} targets, the headline says {mcp_targets}"
    )
    assert (tot3, minus3, eq3) == (total, mcp_targets, total - mcp_targets), (
        f'the worked example says "{tot3} minus {minus3} equals {eq3}"; expected '
        f'"{total} minus {mcp_targets} equals {total - mcp_targets}"'
    )

    # Labels carry a number that the owner's rulings can change. Match the shape,
    # then assert the number -- so the doc cannot silently keep a stale count.
    for label_pat, value in [
        (r"Registry tools", total),
        (r"Absorbed into the \d+ MCP targets", mcp),
        (r"Absorbed into a builder-only SDK method \(not on the MCP\)", sdk_only),
        (r"Tenancy \(SDK/REST only\)", tenancy),
        (r"Retired", retired),
    ]:
        row = re.search(rf"^\| {label_pat} \| \*\*(\d+)\*\* \|$", vision, re.M)
        assert row, f"vision doc has no reconciliation row matching {label_pat!r}"
        assert int(row.group(1)) == value, (
            f"vision doc row {label_pat!r} = {row.group(1)}, generated doc says {value}. "
            "The vision doc restates computed numbers; update it with the generator's output."
        )


def test_destination_map_matches_registry() -> None:
    """Every registry tool is mapped, and every map key exists.

    `--check` only compares the rendered document. This asserts the stronger
    property directly, so a map that has drifted in a way that happens to render
    identically still fails.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION, _registry_rows, _validate

    errs = _validate(_registry_rows())
    assert not errs, "destination map disagrees with the registry:\n  " + "\n  ".join(errs)
    assert len(DESTINATION) == len(_registry_rows())


def test_part_b_columns_are_not_unchecked() -> None:
    """Part B's `Destination` / `Read-only` columns are claims, so check them.

    Round-5 P1 of the PR #4177 review. `test_part_b_line_numbers_point_at_the_right_tool`
    parsed only the `name` and the `file:line` from each row, so every OTHER column
    was free. Overwriting the whole Destination column with `REMOVED` -- a document
    that flatly contradicts its own headline count and its destination table --
    left the suite green.
    """
    from tools.bridge_table import CONTESTED_DESTINATION, DESTINATION, _registry_rows

    rows = {r["name"]: r for r in _registry_rows()}
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    part_b = doc.split("## Part B")[1].split("## Part C")[0]

    parsed: dict[str, tuple[str, str, str]] = {}
    for line in part_b.splitlines():
        m = re.match(
            r"^\| \d+ \| `([a-z_][a-z0-9_]*)` \| `[^`]+` \| (.+?) \| (yes|no) \| (.+?) \|$",
            line,
        )
        if m:
            parsed[m.group(1)] = (m.group(2), m.group(3), m.group(4).strip())

    assert parsed, "Part B rendered no parseable rows - did its column shape change?"
    # The `#` index is a cell too: renumbering every row from 101 was green.
    idx = [int(x) for x in re.findall(r"^\| (\d+) \| `", part_b, re.M)]
    assert idx == list(range(1, len(rows) + 1)), (
        f"Part B's row numbers are not 1..{len(rows)}: got {idx[:5]}...{idx[-3:]}"
    )
    assert set(parsed) == set(rows), (
        "Part B does not render exactly the registry tools.\n"
        f"  only in doc: {sorted(set(parsed) - set(rows))}\n"
        f"  only in registry: {sorted(set(rows) - set(parsed))}"
    )

    for name, (binding, read_only, dest_cell) in parsed.items():
        expected_ro = "yes" if rows[name]["read_only"] else "no"
        assert read_only == expected_ro, (
            f"Part B says {name} read-only={read_only}, the registry says {expected_ro}"
        )
        # The SDK binding is a claim too: what the tool declares today, plus a
        # marker when that name does not resolve. Blanking the whole column to
        # `**none declared**` left every test green.
        declared = rows[name]["sdk_method"]
        want_bind = f"`{declared}`" if declared else "**none declared**"
        if declared and not rows[name]["resolves"]:
            want_bind += " ⚠️ **does not resolve**"
        assert binding.strip() == want_bind, (
            f"Part B says {name} binds {binding.strip()!r}, the registry says {want_bind!r}"
        )
        expected_dest = DESTINATION[name]
        marker = " ⚠️" if name in CONTESTED_DESTINATION else ""
        # EXACT, not a substring: the generator emits the marker for exactly the rows
        # §D2c names and no others, so appending one to every cell is a red.
        assert dest_cell == f"`{expected_dest}`{marker}", (
            f"Part B says {name} -> {dest_cell!r}, the map plus §D2c says "
            f"`{expected_dest}`{marker!r} exactly"
        )
        if expected_dest == "REMOVED":
            assert "does not resolve" not in dest_cell, (
                f"Part B marks {name} as not resolving, but it is simply REMOVED"
            )


def test_the_d2c_disclosure_names_the_sibling_wrong_destinations() -> None:
    """§D2c must disclose the rows the sibling table records as WRONG — without editing them.

    The destination map is owner-approved, so the generator may DISCLOSE a disagreement but
    never change a value. This pins both halves: the rendered D2c rows equal the generator's
    authored disclosure, the `⚠️` marker appears on exactly those Part B rows, and the map's
    own value for them is unchanged (`refresh_confidence`) — so a "fix" that silently
    re-pointed the map would red here even though the sibling would then agree.
    """
    from tools.bridge_table import CONTESTED_DESTINATION, DESTINATION

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    d2c = _section(doc, "### D2c —")

    listed = re.findall(r"^- \*\*`([a-z_]+)`\*\* — map says `([a-z_]+)`; the documented "
                       r"reading is `([a-z_]+)`$", d2c, re.M)
    parsed = {t: (d, r) for t, d, r in listed}
    assert set(parsed) == set(CONTESTED_DESTINATION), (
        "§D2c does not list exactly the disclosed rows: "
        f"{sorted(set(parsed) ^ set(CONTESTED_DESTINATION))}"
    )
    for tool, (reading, _authority) in CONTESTED_DESTINATION.items():
        # INDEPENDENTLY pinned: the reading must be the documented `update_knowledge` and
        # must DIFFER from the map's value. Without this, setting the reading equal to the
        # map value renders a self-contradictory "wrong destination" row green.
        assert reading == "update_knowledge" and reading != DESTINATION[tool], (
            f"the documented reading for {tool} must be `update_knowledge` and must differ "
            f"from the map's {DESTINATION[tool]!r} — otherwise the disclosure is vacuous"
        )
        dest, rendered_reading = parsed[tool]
        assert dest == DESTINATION[tool], (
            f"§D2c says the map carries {dest} for {tool}; the map says {DESTINATION[tool]} "
            "— the disclosure must not misquote the map it reports"
        )
        assert rendered_reading == reading, (
            f"§D2c's documented reading for {tool} is {rendered_reading}, authored as {reading}"
        )
        # The map is owner-approved: the value must be the ORIGINAL one, not the reading.
        assert DESTINATION[tool] == "refresh_confidence", (
            f"the owner-approved destination for {tool} was MOVED to "
            f"{DESTINATION[tool]!r} — the disagreement must be disclosed, never edited"
        )
    assert "owner-approved, so it is reported, not edited" in d2c, (
        "§D2c's not-an-edit caveat is missing"
    )

    part_b = doc.split("## Part B")[1].split("## Part C")[0]
    marked = set(re.findall(r"^\| \d+ \| `([a-z_]+)` \| [^\n]* ⚠️ \|$", part_b, re.M))
    assert marked == set(CONTESTED_DESTINATION), (
        "the `⚠️` marker does not appear on exactly the §D2c rows: "
        f"{sorted(marked ^ set(CONTESTED_DESTINATION))}"
    )


def test_check_exits_nonzero_on_drift() -> None:
    """`--check` must FAIL on a drifted doc, not merely succeed when clean.

    Round-5 P2 of the PR #4177 review: the only test of `--check` asserted a zero
    exit, so a mutation making `main()` return 0 unconditionally -- silently
    disarming the entire drift gate -- would have passed.
    """
    import shutil
    import tempfile

    doc = ROOT / "docs" / "product" / "bridge-table.md"
    with tempfile.TemporaryDirectory() as td:
        backup = Path(td) / "bridge-table.md"
        shutil.copy2(doc, backup)
        try:
            doc.write_text(doc.read_text(encoding="utf-8") + "\nDRIFT\n", encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(GENERATOR), "--check"],
                capture_output=True, text=True, cwd=str(ROOT),
            )
            assert proc.returncode != 0, (
                "--check exited 0 on a deliberately drifted document; the drift gate is disarmed"
            )
        finally:
            shutil.copy2(backup, doc)


def test_retired_rows_lead_to_their_replacement_destination() -> None:
    """A retired name's destination is its REPLACEMENT's destination.

    The retirement warning sends the caller to the replacement, so the bridge
    table must not claim the same name lands somewhere else: two maps disagreeing
    about one journey is a silent contradiction in an owner-facing document, and
    nothing else in this file compares them.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION, _registry_rows

    retired = [r for r in _registry_rows() if r["use_instead"]]
    assert len(retired) == 18, f"expected 18 retired rows, got {len(retired)}"
    for r in retired:
        assert r["use_instead"] in DESTINATION, (
            f"{r['name']} names a replacement the destination map does not know: "
            f"{r['use_instead']}"
        )
        assert DESTINATION[r["name"]] == DESTINATION[r["use_instead"]], (
            f"{r['name']} leads to {DESTINATION[r['name']]!r} but its replacement "
            f"{r['use_instead']} leads to {DESTINATION[r['use_instead']]!r} — the "
            f"retirement warning and the bridge table disagree about one journey"
        )


# ──────────────────────────────────────────────────────────────────────
# THE REASONED-RETIREMENT PROOF (#4282)
#
# The owner's prerequisite was that every one of the 98 declared names resolves either
# to a target that exists in the approved menu or to an explicit, REASONED retirement.
# `_validate` refuses both failure directions — a `REMOVED` row with no reason, and a
# reason outliving the row it describes. The reason map is the load-bearing half of the
# reconciliation (it is the "explicit" in "explicit, reasoned retirement"), so the
# mutation tests below red the build in BOTH directions: a check that nothing exercises
# can be deleted silently, and `_validate` returning `[]` on the live map cannot tell
# the difference between "the proof passed" and "the proof is gone".
# ──────────────────────────────────────────────────────────────────────


def test_the_live_retirement_reasons_account_for_exactly_the_retired_rows() -> None:
    """The reason map's key set IS the retired set — the invariant the build enforces."""
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION, REMOVALS, _registry_rows, _validate

    assert _validate(_registry_rows()) == [], (
        "the live map does not validate — every assertion below is then describing a "
        "state the build already refuses"
    )

    retired = {n for n, d in DESTINATION.items() if d == "REMOVED"}
    assert retired == set(REMOVALS), (
        "the retired set and the reason set disagree, so a retirement is either "
        f"unreasoned ({sorted(retired - set(REMOVALS))}) or the reason is stale "
        f"({sorted(set(REMOVALS) - retired)})"
    )
    assert len(retired) == 9, f"expected 9 retired tools, got {len(retired)}"

    for name, reason in sorted(REMOVALS.items()):
        assert reason.strip(), f"{name} carries an empty reason"
        # A reason is only a PROOF if it names what authorises it. Every entry names
        # either the canonical doc or the dated ruling that settled it.
        assert "canonical" in reason or "settled" in reason, (
            f"{name}'s reason names no authority: {reason!r}"
        )


def test_a_retirement_without_a_reason_fails_the_build(monkeypatch) -> None:
    """Drop ONE reason from a retired name -> the build must refuse that name.

    Mutated per name rather than on a single sample: a guard that happens to fire for
    `tortoise_analyze` but not for the onboarding block would leave the block silently
    unreasoned.
    """
    sys.path.insert(0, str(ROOT))
    from tools import bridge_table as bt

    rows = bt._registry_rows()
    retired = sorted(n for n, d in bt.DESTINATION.items() if d == "REMOVED")
    assert retired, "the fixture is vacuous: nothing is retired"

    for name in retired:
        reasons = dict(bt.REMOVALS)
        del reasons[name]
        monkeypatch.setattr(bt, "REMOVALS", reasons)
        errs = bt._validate(rows)
        assert any("UNREASONED retirement" in e and name in e for e in errs), (
            f"dropping {name}'s reason did not red the build — the retirement proof "
            f"does not cover {name}; got {errs}"
        )


def test_a_stale_reason_fails_the_build(monkeypatch) -> None:
    """A reason outliving its `REMOVED` row must red the build (the other direction).

    Without this direction the map could accumulate reasons for tools that resolve to a
    real target — a reader would then be told a live tool was retired.
    """
    sys.path.insert(0, str(ROOT))
    from tools import bridge_table as bt

    rows = bt._registry_rows()
    live = sorted(n for n, d in bt.DESTINATION.items() if d != "REMOVED")
    assert live, "the fixture is vacuous: everything is retired"

    for name in live[:5]:
        reasons = dict(bt.REMOVALS)
        reasons[name] = "canonical-mcp-tools.md §Removed entirely — stale"
        monkeypatch.setattr(bt, "REMOVALS", reasons)
        errs = bt._validate(rows)
        assert any("STALE retirement reason" in e and name in e for e in errs), (
            f"a reason for the live tool {name} did not red the build — a resolved "
            f"retirement can leave a stale reason behind; got {errs}"
        )

    # And the block must actually be REACHABLE: with no reasons at all, every retired
    # name is unreasoned. This is what a deleted guard cannot satisfy.
    monkeypatch.setattr(bt, "REMOVALS", {})
    errs = bt._validate(rows)
    assert sum("UNREASONED retirement" in e for e in errs) == len(
        [n for n, d in bt.DESTINATION.items() if d == "REMOVED"]
    ), f"the proof does not cover every retirement: {errs}"


def test_namespaced_destinations_are_rehomes_and_owe_no_retirement_reason() -> None:
    """`tenancy:`/`sdk:` are rehomes, NOT retirements — so they must not carry a reason."""
    sys.path.insert(0, str(ROOT))
    from tools import bridge_table as bt

    # `tenancy:` and `sdk:` are self-describing non-target destinations, NOT retirements,
    # so they must not owe a reason: requiring one would make the map describe an
    # account-tenancy rehome as a deletion.
    for name, dest in bt.DESTINATION.items():
        if dest.startswith(("tenancy:", "sdk:")):
            assert name not in bt.REMOVALS, (
                f"{name} is rehomed to {dest!r}, not retired, but carries a REMOVALS "
                "reason — the reason map would read as a deletion"
            )


def test_check_exits_nonzero_on_an_unreasoned_retirement(monkeypatch) -> None:
    """`--check` is the gate the migration runs through: it must exit non-zero."""
    sys.path.insert(0, str(ROOT))
    from tools import bridge_table as bt

    monkeypatch.setattr(bt, "REMOVALS", {})
    monkeypatch.setattr(sys, "argv", ["bridge_table.py", "--check"])
    assert bt.main() != 0, (
        "`bridge_table.py --check` returned 0 with every retirement unreasoned — the "
        "gate the migration is driven through does not enforce the proof"
    )


# ──────────────────────────────────────────────────────────────────────
# THE DESTINATION CITATIONS (#4282, defect class of PR #4477)
#
# The `Destination` column's evidence is the beta doc's own disposition row. A read
# that stops at the clause AGREEING with the row drops the clause that contradicts
# it — and because a truncated prefix of a real sentence is still a real substring,
# a bare `quote in text` test cannot see the difference. `_maximal` is the fix, and
# the tests below derive the boundary rule THEMSELVES: `tools.bridge_table._maximal`
# is the implementation under test, so importing it would move both sides of the
# assertion together.
# ──────────────────────────────────────────────────────────────────────


def _part_d_citations() -> dict[str, str]:
    """`tool -> its rendered maximal citation`, read out of the generated document."""
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    assert "## Part D" in doc, (
        "the generated doc has no Part D — the citation corpus is unreachable, so a "
        "truncated citation is invisible to every reader"
    )
    # The slice's real end is the `**Documented hops.**` cut below, NOT `_section`'s heading
    # bound: the marker sits inside D1 and ahead of the next heading, so the cut truncates
    # first and the bound never determines the parse (bound, old literal, and no bound all
    # yield the same 4 443 chars / 19 rows). The assert below does not ADD detection — a moved
    # marker is already caught downstream by the row/quote parity checks; it exists so the
    # failure is named here instead of surfacing as four unrelated row-parity failures.
    d1 = _section(doc, "### D1")
    hops = "**Documented hops.**"
    assert hops in d1, (
        f"the D1 slice contains no {hops!r} marker — the citation corpus end moved, so this "
        "parse would run past the documented-hop block instead of stopping at the D1 rows"
    )
    # The documented-hop block uses the same bullet+quote shape, so cut it off first.
    d1 = d1.split(hops, 1)[0]
    rows = re.findall(r"^- `([^`]+)` · rows (.+)$", d1, re.M)
    quotes = re.findall(r"^  > (.+)$", d1, re.M)
    assert rows, "Part D1 rendered no citation rows — the citation guard is unarmed"
    # A rendered row with no quote (or vice versa) would silently shift the zip below.
    assert len(quotes) == len(rows), (
        f"Part D1 has {len(rows)} citation rows but {len(quotes)} quotes"
    )
    out: dict[str, str] = {}
    for (docname, toollist), quote in zip(rows, quotes, strict=True):
        assert docname in ("beta-sdk-surface.md",), f"unexpected citation doc {docname!r}"
        for t in re.findall(r"`([a-z_][a-z0-9_]*)`", toollist):
            assert t not in out, f"{t} is cited twice in Part D1"
            out[t] = quote
    return out


def _is_maximal(quote: str, text: str) -> bool:
    """The maximality rule, re-derived — a quote must end at a region boundary.

    Boundaries: a cell/row `|`, a line end, the document end, or a sentence end. A quote
    stopping mid-clause is a right-truncation, and the cut is where a contradiction hides.
    """
    idx = text.find(quote)
    assert idx >= 0, f"citation is not in its doc at all: {quote[:60]!r}"
    after = text[idx + len(quote):]
    if after == "" or after.startswith("\n") or quote.endswith("|"):
        return True
    return re.search(r"[.!?][\"')\]\u201d`*_]*$", quote) is not None


def _section(doc: str, heading: str) -> str:
    """The body of `heading`, up to the next heading of the same-or-higher level.

    `#{2,3}` is same-or-higher than the level-3 `###` headings this is called with, so a
    deeper `####` inside a section does not truncate it. The split is asserted to have applied:
    without that, a section with no following heading silently returns the rest of the doc.

    PRECONDITION: `heading` must be followed by another h2/h3 heading. This helper cannot
    serve the document's FINAL section — there the rest of the doc IS the correct body, but
    the assert cannot tell that case apart from a bound that failed to apply, so it raises.
    No call site targets a final section; add one only with a different helper.
    """
    assert heading in doc, f"the generated doc has no {heading!r} section"
    parts = re.split(r"\n#{2,3} ", doc.split(heading)[1])
    assert len(parts) > 1, (
        f"no heading follows {heading!r} at level 2-3 — either the section bound did not "
        "apply (so this would return the rest of the document), or this is the document's "
        "final section, which _section does not support"
    )
    return parts[0]


def _cited_findings() -> tuple[set[str], set[str], set[str]]:
    """(unsupported, clause-only, ambiguous) from the RENDERED doc — the oracle subject."""
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    d2 = _section(doc, "### D2 —")
    d2b = _section(doc, "### D2b —")
    d3 = _section(doc, "### D3 —")
    unsupported = set(re.findall(r"^- \*\*`([a-z_][a-z0-9_]*)`\*\*", d2, re.M))
    clause_only = set(re.findall(r"^- \*\*`([a-z_][a-z0-9_]*)`\*\*", d2b, re.M))
    ambiguous = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \|", d3, re.M))
    return unsupported, clause_only, ambiguous


def test_rendered_citations_are_present_and_maximal() -> None:
    """Every citation in the rendered doc must reach a region boundary, in its own doc.

    The end-to-end half of the rule: a generator that starts emitting a citation cut at
    the clause that agrees with its row would render a document that *looks* sourced here.
    """
    beta = (ROOT / "docs" / "product" / "beta-sdk-surface.md").read_text(encoding="utf-8")
    cites = _part_d_citations()
    # Non-vacuity: a corpus that shrank to nothing cannot fail the loop below.
    assert len(cites) >= 20, (
        f"only {len(cites)} citations rendered — the corpus shrank, and an empty or tiny "
        "corpus makes every maximality assertion below vacuous"
    )
    bad = [f"{t}: {q[:90]!r}" for t, q in cites.items() if not _is_maximal(q, beta)]
    assert not bad, (
        "these rendered citations stop mid-clause, so a clause is being hidden from the "
        "reader — and the dropped clause is where a contradiction lives:\n  "
        + "\n  ".join(bad)
    )


def test_a_truncated_citation_is_rejected_by_the_build(monkeypatch) -> None:
    """The guard must RED on a truncated citation, and must not write the doc.

    The fixture is the named failure mode itself: the `get_source_reliability` row cut at
    the `;` that introduces `reads via \\`list_sources\\`` — the clause that decides the
    row's destination. The cut is a real substring of the doc, so `quote in text` accepts
    it; `_maximal` must not.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    beta = (ROOT / "docs" / "product" / "beta-sdk-surface.md").read_text(encoding="utf-8")
    full = _part_d_citations()["tortoise_get_source_reliability"]
    cut = full[: full.index(";")]
    assert cut != full, "the truncation fixture is no longer truncating anything"
    assert cut in beta, "the truncated fixture is no longer a substring of the doc"
    assert not _is_maximal(cut, beta), (
        "the independent boundary oracle accepts the truncated fixture — the fixture "
        "no longer demonstrates a mid-clause cut"
    )

    # 1. The predicate. `tortoise_get_source_reliability` is exactly the row that needs
    #    this clause: without it the row reads as supported by `manage_source_trust`.
    assert not bt._maximal(cut, beta), "`_maximal` accepted a mid-clause truncation"
    assert bt._maximal(full, beta), "`_maximal` rejected its own maximal citation"

    # 2. The wired path — the guard must fail the BUILD, not merely exist.
    before = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    bad = {"tortoise_get_source_reliability": {"doc": "beta-sdk-surface.md", "quote": cut}}
    errs = bt._citation_errors(bad)
    assert any("TRUNCATED CITATION" in e for e in errs), (
        f"`_citation_errors` did not flag the truncation: {errs}"
    )
    monkeypatch.setattr(bt, "_doc_citations", lambda: bad)
    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "the generator's main() did not fail the build on a truncated citation"
    after = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    assert after == before, "the generator WROTE the document despite a truncated citation"
    # A guard that resolves nothing cannot fail. Assert the armed case is non-empty too.
    assert bt._doc_citations(), "the citation guard resolved no citations at all"


# The destination-anchor rule, re-derived in the test — deliberately NOT imported from
# `tools.bridge_table`. A target named after the arrow is a destination only when the text
# before it is the arrow itself, a clause separator, or a destination preposition. A name
# mid-sentence ("the batch form of `mine_knowledge_from_session`", "— `explore_connections`
# answers that question") is a comparison or an aside, not an alternative destination.
_DEST_LEAD = re.compile(r"(?:→|[;,]+\s*|\b(?:on|via|to|into|toward|towards)\s+)\**\s*$")
_FOLDS_LITERAL = {"list_sources": "list_knowledge"}


def _recompute_citation_rows() -> dict[str, dict]:
    """tool -> {dest, named, prefix, quote}, from the RENDERED corpus plus the map.

    The generator's `_citation_findings` is not imported, so a mangled rendered cell or a
    map edit that erases a finding cannot move both sides of an assertion together.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION

    folds = dict(_FOLDS_LITERAL)
    keep = {d.split(":", 1)[-1] for d in DESTINATION.values()} | set(folds)

    def resolve(names: list[str]) -> list[str]:
        out: list[str] = []
        for n in names:
            r = folds.get(n, n)
            if r not in out:
                out.append(r)
        return out

    def targets(quote: str, first_clause: bool) -> list[str]:
        segs = quote.split("→")[1:]
        if first_clause:
            segs = [re.split(r"[;.]", segs[0], maxsplit=1)[0]] if segs else []
        names: list[str] = []
        for s in segs:
            for m in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^`]*\))?`", s):
                if m.group(1) not in keep:
                    continue
                before = s[: m.start()]
                if before.strip() == "" or _DEST_LEAD.search(before):
                    names.append(m.group(1))
        return names

    out: dict[str, dict] = {}
    for tool, quote in _part_d_citations().items():
        out[tool] = {
            "tool": tool,
            "quote": quote,
            "dest": DESTINATION[tool],
            "named": resolve(targets(quote, first_clause=False)),
            "prefix": resolve(targets(quote, first_clause=True)),
        }
    return out


def _recompute_citation_sets(
    rows: dict[str, dict],
) -> tuple[set[str], set[str], set[str]]:
    unsupported: set[str] = set()
    clause_only: set[str] = set()
    ambiguous: set[str] = set()
    for tool, r in rows.items():
        if r["dest"] not in r["named"]:
            unsupported.add(tool)
        elif r["dest"] not in r["prefix"]:
            clause_only.add(tool)
        if len(r["named"]) > 1:
            ambiguous.add(tool)
    return unsupported, clause_only, ambiguous


def test_part_d_findings_match_an_independent_recomputation() -> None:
    """D2 / D2b / D3 must list exactly what the citations and the map imply.

    Recomputed here from the RENDERED corpus plus the map — the generator's own
    `_citation_findings` is not imported — so emptying any of those sections reds this
    test instead of passing as "nothing to report".

    The membership sets are pinned as LITERALS (P2-1). A map edit that makes a finding
    agree with the doc — `tortoise_invalidate` re-pointed at `update_knowledge` — drops D2
    4->3, and the rendered set and the recomputed set moved TOGETHER, so only a literal pin
    can see it.
    """
    rows = _recompute_citation_rows()
    unsupported, clause_only, ambiguous = _recompute_citation_sets(rows)

    # Non-vacuity, and the named failure mode this rule exists to produce: the clause-only
    # row must be present, so a rule that reports "nothing" is a broken rule.
    assert clause_only == {"tortoise_get_source_reliability"}, (
        "expected exactly the `get_source_reliability` row to be supported only beyond its "
        f"citation's first clause; computed {sorted(clause_only)}"
    )
    assert unsupported, "no citation disagrees with its row — the audit found nothing"
    assert unsupported == {
        "tortoise_invalidate", "tortoise_paginated_query",
        "tortoise_query", "tortoise_query_points_by_tag",
    }, f"D2 membership changed: {sorted(unsupported)}"
    # The destination-anchor rule removed the three prose false positives; a regression to
    # scanning the whole post-arrow segment re-adds them here.
    # #3904 dropped `tortoise_operator_action` from this set — NOT a map edit racing the
    # doc, but the consequence of repairing its declaration: its `sdk_method` was
    # `operator_action`, a name that does not exist on `TortoiseSDK`, and it is now
    # `none declared`. The row's destination moved from a phantom method to no method, so
    # its citation stopped being ambiguous. This literal is the pin doing its job (see the
    # docstring): the change was noticed and attributed, which is what it exists to force.
    assert ambiguous == {
        "tortoise_annotate_operator", "tortoise_assess_source", "tortoise_belief_timeline",
        "tortoise_get_source_reliability", "tortoise_mitigate_operator",
        "tortoise_provenance", "tortoise_session_context",
        "tortoise_set_source_tier",
    }, f"D3 membership changed: {sorted(ambiguous)}"

    got = _cited_findings()
    for label, want, have in (
        ("D2 (disagree)", unsupported, got[0]),
        ("D2b (clause-only)", clause_only, got[1]),
        ("D3 (ambiguous)", ambiguous, got[2]),
    ):
        assert have == want, (
            f"{label} does not list what the citations imply.\n"
            f"  listed but not implied: {sorted(have - want)}\n"
            f"  implied but not listed: {sorted(want - have)}"
        )


def test_part_d_rendered_cells_are_pinned_to_the_recomputation() -> None:
    r"""Every Part D cell and every Part D count is compared to a recomputation (P1).

    Pinning only membership left the rendered prose free. Each mutation below changed the
    document, left `--check` green, and left the suite at 16 passed:

    * D2 could say `map says \`search_knowledge\`` for a row whose map says
      `supersede_knowledge`, contradicting Part B two screens up (whose Destination IS
      pinned);
    * D2b could SWAP "the first clause names X, the full citation names Y", contradicting
      the D1 quote rendered directly above it;
    * D3's `Destination (map)` / `Citation names` / `First clause names` columns could be
      wrong on every row;
    * the D1 headline count could be typed as `25 citations` while D1 lists 19 groups.

    This compares each rendered cell — and each rendered count — to a value recomputed from
    the rendered corpus plus the map.
    """
    rows = _recompute_citation_rows()
    unsupported, clause_only, ambiguous = _recompute_citation_sets(rows)
    cites = _part_d_citations()
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")

    def name_list(cell: str) -> list[str]:
        return [f"`{n}`" for n in re.findall(r"`([a-z_][a-z0-9_]*)`", cell)]

    # ── D2 rows: dest + named + the rendered quote ─────────────────
    d2 = _section(doc, "### D2 —")
    parsed_d2: dict[str, dict] = {}
    for m in re.finditer(
        r"^- \*\*`([a-z_][a-z0-9_]*)`\*\* — map says `([^`]+)`; citation names ([^\n]+)\n"
        r"  > ([^\n]+)$",
        d2, re.M,
    ):
        tool = m.group(1)
        assert tool not in parsed_d2, f"{tool} rendered twice in D2"
        parsed_d2[tool] = {
            "dest": m.group(2),
            "named": name_list(m.group(3)),
            "quote": m.group(4),
        }
    assert "**D2 is a LOWER BOUND" in d2, (
        "D2's lower-bound caveat is missing — the section then reads EXHAUSTIVE, when its "
        "predicate cannot decide clause attribution (the `tortoise_assess_source` reading)"
    )
    assert set(parsed_d2) == unsupported, (
        f"D2 rendered {sorted(parsed_d2)} but the recomputation says {sorted(unsupported)}"
    )
    for tool, cell in parsed_d2.items():
        want = rows[tool]
        assert cell["dest"] == want["dest"], (
            f"D2 says {tool}'s map destination is {cell['dest']!r}; the map says {want['dest']!r}"
        )
        assert cell["named"] == [f"`{n}`" for n in want["named"]], (
            f"D2 says {tool}'s citation names {cell['named']}; recomputed {want['named']}"
        )
        assert cell["quote"] == want["quote"], (
            f"D2 renders a different quote for {tool} than the corpus recomputation"
        )

    # ── D2b rows: clause attribution must not be swapped ───────────
    d2b = _section(doc, "### D2b —")
    parsed_d2b: dict[str, dict] = {}
    for m in re.finditer(
        r"^- \*\*`([a-z_][a-z0-9_]*)`\*\* — map says `([^`]+)`; the first clause names "
        r"([^\n]+?), the full citation names ([^\n]+)\n  > ([^\n]+)$",
        d2b, re.M,
    ):
        tool = m.group(1)
        parsed_d2b[tool] = {
            "dest": m.group(2),
            "prefix": name_list(m.group(3)),
            "named": name_list(m.group(4)),
            "quote": m.group(5),
        }
    assert set(parsed_d2b) == clause_only, (
        f"D2b rendered {sorted(parsed_d2b)} but the recomputation says {sorted(clause_only)}"
    )
    for tool, cell in parsed_d2b.items():
        want = rows[tool]
        assert cell["dest"] == want["dest"]
        # EXACT ORDER: this is precisely the clause-attribution claim, and swapping the two
        # sides was green while the bullet contradicted the D1 quote directly above it.
        assert cell["prefix"] == [f"`{n}`" for n in want["prefix"]], (
            f"D2b says the first clause of {tool} names {cell['prefix']}; recomputed "
            f"{want['prefix']} (swapped with the full citation?)"
        )
        assert cell["named"] == [f"`{n}`" for n in want["named"]], (
            f"D2b says the full citation of {tool} names {cell['named']}; recomputed "
            f"{want['named']}"
        )
        assert cell["quote"] == want["quote"]

    # ── D3 rows: every column ──────────────────────────────────────
    d3 = _section(doc, "### D3 —")
    parsed_d3: dict[str, dict] = {}
    for m in re.finditer(
        r"^\| `([a-z_][a-z0-9_]*)` \| `([^`]+)` \| ([^|]+)\| ([^|]+)\|$", d3, re.M
    ):
        parsed_d3[m.group(1)] = {
            "dest": m.group(2),
            "named": name_list(m.group(3)),
            "prefix": name_list(m.group(4)),
        }
    assert set(parsed_d3) == ambiguous, (
        f"D3 rendered {sorted(parsed_d3)} but the recomputation says {sorted(ambiguous)}"
    )
    for tool, cell in parsed_d3.items():
        want = rows[tool]
        assert cell["dest"] == want["dest"], (
            f"D3 says {tool}'s map destination is {cell['dest']!r}; the map says {want['dest']!r}"
        )
        assert cell["named"] == [f"`{n}`" for n in want["named"]], (
            f"D3 says {tool}'s citation names {cell['named']}; recomputed {want['named']}"
        )
        assert cell["prefix"] == [f"`{n}`" for n in want["prefix"]], (
            f"D3 says {tool}'s first clause names {cell['prefix']}; recomputed {want['prefix']}"
        )

    # ── Derived citations are WHOLE rows by construction (P2-2) ────
    beta = (ROOT / "docs" / "product" / "beta-sdk-surface.md").read_text(encoding="utf-8")
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import _registry_rows

    method_of = {r["name"]: r["sdk_method"] for r in _registry_rows()}
    for tool, q in cites.items():
        # The citation ASSIGNED to a tool must name that tool's sdk_method. Without this a
        # D1 grouping could hand tool A tool B's row (both maximal, both whole rows) and
        # every maximality/set assertion above would still pass.
        first_cell = q.strip().strip("|").split("|")[0]
        cited_names = re.findall(r"`([a-z_][a-z0-9_]*)`", first_cell)
        assert method_of.get(tool) in cited_names, (
            f"D1 assigns {tool} a citation that does not name its sdk_method "
            f"{method_of.get(tool)!r}: {first_cell.strip()[:80]!r}"
        )
        assert q.endswith("|"), f"{tool}'s citation does not end at a row boundary: {q[-40:]!r}"
        assert f"\n{q}\n" in beta, (
            f"{tool}'s citation is not a WHOLE line of beta-sdk-surface.md — it is a prefix or "
            "a truncation, which is exactly what 'whole region by construction' must prevent"
        )

    # ── Every Part D count ─────────────────────────────────────────
    n_registry = len(_registry_rows())
    n_cited = len(cites)
    n_groups = len(set(cites.values()))
    m = re.search(
        r"\*\*(\d+) citations cover (\d+) of the (\d+) registry rows\.\*\* The other \*\*(\d+)\*\*",
        doc,
    )
    assert m, "D1's headline counts are missing or changed shape"
    assert tuple(int(g) for g in m.groups()) == (
        n_groups, n_cited, n_registry, n_registry - n_cited,
    ), f"D1 headline is {m.groups()}, recomputed {(n_groups, n_cited, n_registry, n_registry - n_cited)}"

    m = re.search(
        r"\*\*(\d+) rows disagree with their own citation;\s*(\d+) are supported only\s*"
        r"beyond the first clause;\s*(\d+) sit under an ambiguous citation\.\*\*",
        doc,
    )
    assert m, "the Part D summary counts are missing or changed shape"
    assert tuple(int(g) for g in m.groups()) == (
        len(unsupported), len(clause_only), len(ambiguous),
    ), f"Part D summary is {m.groups()}"


def test_the_named_failure_mode_is_visible_in_the_document() -> None:
    """The clause that decides `get_source_reliability` must be RENDERED, not summarised.

    This is the whole point of quoting maximally: the row's destination (`list_knowledge`)
    is reachable only through `; reads via \\`list_sources\\``. If the document shows only
    the clause that agrees, a reader cannot audit the row at all — so pin the continuation
    and the hop that resolves it.
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    assert "; reads via `list_sources`." in doc, (
        "the citation for the source-trust family is truncated in the document: the clause "
        "that decides `get_source_reliability`'s destination is missing"
    )
    assert "folds into **row 4 `list_knowledge(kind='source')`**" in doc, (
        "the documented hop from `list_sources` to `list_knowledge` is not rendered"
    )


def test_doc_citations_do_not_drop_a_shared_sdk_method(tmp_path, monkeypatch) -> None:
    """A method declared by two registry tools must cite BOTH (latent drop, P2-5).

    `tortoise_get_point` and `tortoise_get_operator` both declare `get_point`. Keying the
    method->tool lookup by a plain dict kept only the LAST tool, so a disposition row naming
    `get_point` would cite one tool and silently drop the other. No disposition row names
    `get_point` TODAY, so the defect is latent — which is exactly why a synthetic row is the
    only way to exercise the fix and keep it from regressing.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    declared = sorted(r["name"] for r in bt._registry_rows() if r["sdk_method"] == "get_point")
    assert len(declared) >= 2, f"expected >=2 registry tools declaring `get_point`, got {declared}"

    synthetic = tmp_path / "beta.md"
    synthetic.write_text(
        "| names | count | dest |\n"
        "|---|---|---|\n"
        "| `get_point` | 1 | → `get_entity`. |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bt, "BETA_DOC", synthetic)
    cites = bt._doc_citations()
    missing = [n for n in declared if n not in cites]
    assert not missing, (
        f"the shared `get_point` binding dropped {missing} from the citations — the "
        "method->tool lookup is last-wins again"
    )


# ─────────────────────────────────────────────────────────────────────
# §D4 — the owner-approved MENU cross-check (#4513)
#
# #4513 was filed because the bridge table's destination map was never bound to the
# OTHER owner-approved doc: `canonical-mcp-tools.md`, whose `Absorbs` cell is the
# owner's own record of which current name folds into which target. Two artifacts
# of the same epic disagreed and nothing could tell. §D4 reads that doc, so these
# tests pin the derivation — not the wording.
# ─────────────────────────────────────────────────────────────────────


def _d4_rows(doc: str) -> dict[str, list[str]]:
    """§D4's table, row -> cells. Parsed out of the GENERATED doc, not the source."""
    section = _section(doc, "### D4 —")
    rows: dict[str, list[str]] = {}
    for line in section.splitlines():
        m = re.match(r"^\| `(tortoise_[a-z0-9_]+)` \| (.+) \|$", line)
        if m:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    return rows


def test_d4_covers_exactly_the_disputed_rows_and_recomputes_every_cell() -> None:
    """§D4 must list every disputed row — and only those — with the recomputed verdicts.

    Non-vacuity matters here more than elsewhere: the section's whole value is that a
    row the approved menu CONTRADICTS is visible at the row. A section that silently
    emptied (a parse break, a renamed helper) would read as "no disagreements".
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")

    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    cites = bt._doc_citations()
    findings = bt._citation_findings(cites)
    got = _d4_rows(doc)
    assert got, "§D4 rendered no rows — the approved-menu cross-check is unarmed"

    disputed = {f["tool"] for group in findings for f in group}
    assert set(got) == disputed, (
        f"§D4 rendered {sorted(got)} but the disputed set is {sorted(disputed)} — the "
        "section must disclose the rows the other sections flag, and nothing else"
    )

    want = {a["tool"]: a for a in bt._approved_readings(bt._approved_menu(), findings)}
    for tool, cells in got.items():
        row = want[tool]
        assert f"`{row['dest']}`" in cells[0], (
            f"§D4 says {tool}'s map destination is {cells[0]!r}; the map says {row['dest']!r}"
        )
        assert cells[3].strip("*") == row["dest_verdict"], (
            f"§D4 says the approved menu reads {tool}'s destination as {cells[3]!r}; "
            f"recomputed {row['dest_verdict']!r}"
        )
        for rival, verdict in row["rivals"]:
            assert f"`{rival}` — **{verdict}**" in cells[4], (
                f"§D4 does not report rival `{rival}` as {verdict!r} for {tool}: {cells[4]!r}"
            )


def test_d4_records_the_two_4513_rows_that_were_open() -> None:
    """#4513's own two open rows, pinned to the approved menu's answer.

    §1 (`annotate_operator`) and §2 (`belief_timeline`) were both filed as needing a
    ruling because beta's prose clause is ambiguous. The approved menu is NOT
    ambiguous about either, and the artifact must say so — that is what stops the next
    lane from re-litigating them from the clause alone:

    * `annotate_operator` is named in `adjust_relationship`'s own Absorbs cell (row 21),
      so the map's `adjust_relationship` is the approved reading and the citation's
      `update_knowledge` is refuted — row 17's split absorbs it for `promote_point`
      and `set_point_baseline`, NOT for annotation.
    * `belief_timeline`'s `poll_events` reading ("where they are a timeline") is
      refuted: row 8 `poll_events` absorbs `events_poll` and nothing else.
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    rows = _d4_rows(doc)
    approved_doc = (ROOT / "docs" / "product" / "canonical-mcp-tools.md").read_text(
        encoding="utf-8"
    )

    annotate = rows["tortoise_annotate_operator"]
    assert "`adjust_relationship` (row 21)" in annotate[1], (
        "§D4 does not name the approved row that absorbs `annotate_operator`: "
        f"{annotate[1]!r} — the row the owner reviews must be visible"
    )
    assert annotate[3].strip("*") == "row", (
        "the approved menu places `annotate_operator` in `adjust_relationship`, so its "
        f"reading of the map's destination must be `row`, got {annotate[3]!r}"
    )
    assert "`update_knowledge` — **refuted**" in annotate[4], (
        "§D4 must refute the citation's `update_knowledge` reading for "
        f"`annotate_operator`: {annotate[4]!r}"
    )

    timeline = rows["tortoise_belief_timeline"]
    assert "`poll_events` — **refuted**" in timeline[4], (
        "§D4 must refute `poll_events` for `belief_timeline` — that is #4513 §2's "
        f"open question, answered by the approved doc: {timeline[4]!r}"
    )
    assert "`recall_beliefs` (row 3)" in timeline[1], (
        f"§D4 does not name `belief_timeline`'s approved home: {timeline[1]!r}"
    )
    # ...and its DESTINATION is a disagreement, not silence: the approved doc DOES absorb
    # `belief_timeline` (row 3), it just never names the map's `check_confidence`. Reading
    # that as `unstated` told the owner the row was "not named by" a menu whose quoted
    # row, three lines below, names it — and it understated a contradiction it exists to
    # disclose (#4513 review, P1).
    assert timeline[3].strip("*") == "rehomed", (
        "`belief_timeline` has an approved home (row 3) but not at the map's destination, so "
        f"its destination is a DISAGREEMENT, not `unstated`: got {timeline[3]!r}"
    )

    # Both claims are about the doc, so they are re-checked against the doc itself.
    assert (
        "| 21 | `adjust_relationship` | operator_action, annotate_operator, "
        "mitigate_operator |" in approved_doc
    )
    assert "| 8 | `poll_events` | events_poll |" in approved_doc


def test_d4_never_calls_a_row_unnamed_while_naming_its_home() -> None:
    """A row with an approved home must not be described as unnamed by the approved menu.

    The generated section is the owner's evidence. A summary sentence that says the
    approved menu "does not name" a row, printed under a table cell that names the row's
    approved home and beside the quoted approved row, is self-contradicting evidence —
    and it is the exact defect class §D4 exists to close (#8930 review, P1).
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    section = _section(doc, "### D4 —")
    rows = _d4_rows(doc)

    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    approved = bt._approved_readings(
        bt._approved_menu(), bt._citation_findings(bt._doc_citations())
    )
    homed = {a["tool"] for a in approved if a["homes"]}
    assert homed, "no disputed row has an approved home — this test would be vacuous"

    # Every row with a home renders that home in the table. Not `startswith("`")`: the
    # multi-home form renders `*(several: `a`, `b`)*`, so the assertion is "every home the
    # derivation found is NAMED in the cell", which holds for one home and for several.
    for tool in homed:
        row = next(a for a in approved if a["tool"] == tool)
        for home in row["homes"]:
            assert f"`{home}`" in rows[tool][1], (
                f"§D4's `Approved home` cell for {tool} omits its home `{home}`: "
                f"{rows[tool][1]!r}"
            )

    unnamed_sentences = [
        line
        for line in section.splitlines()
        if "does not name" in line or "not named by it" in line or "names neither" in line
    ]
    for line in unnamed_sentences:
        for tool in homed:
            assert f"`{tool}`" not in line, (
                f"§D4 says {tool} is unnamed by the approved menu, but the same section "
                f"gives its approved home and quotes its row: {line!r}"
            )

    # The counts partition the rows: placed + elsewhere + no place == all of them, and each
    # bucket is the one the verdicts establish — "placed ELSEWHERE" is reserved for rows that
    # HAVE an approved home, because `refuted` no longer implies the doc places the method
    # somewhere (a method it never absorbs reads `refuted` too).
    m = re.search(
        r"\*\*(\d+) of (\d+) disputed rows are placed by the approved menu where this\s*"
        r"map places them; (\d+) are placed ELSEWHERE by it; (\d+) have\s*"
        r"no place in it at all\.\*\*",
        section,
    )
    assert m, f"§D4's summary sentence is not in the rendered shape: {section[-2000:]!r}"
    placed, total, elsewhere, uncovered = (int(g) for g in m.groups())
    assert placed + elsewhere + uncovered == total == len(rows), (
        f"§D4's counts ({placed}+{elsewhere}+{uncovered} vs {total}) do not partition the "
        f"{len(rows)} rendered rows"
    )
    elsewhere_want = [
        a for a in approved if a["homes"] and a["dest_verdict"] in ("refuted", "rehomed")
    ]
    uncovered_want = [
        a for a in approved if not a["homes"] or a["dest_verdict"] == "unstated"
    ]
    assert elsewhere == len(elsewhere_want), (
        f"§D4 counts {elsewhere} rows as placed ELSEWHERE; the rows with a home and a "
        f"disagreeing verdict are {[a['tool'] for a in elsewhere_want]}"
    )
    assert uncovered == len(uncovered_want), (
        f"§D4 counts {uncovered} rows as having no approved place; the home-less rows are "
        f"{[a['tool'] for a in uncovered_want]}"
    )


def test_d4_evidence_line_names_a_token_its_quoted_row_contains() -> None:
    """The evidence line must name the alias the MATCH was made on, not the SDK method.

    `tortoise_invalidate`'s SDK method is `invalidate_point`, which occurs 0 times in the
    approved doc — the match is on its other alias, `invalidate`. Printing the SDK method
    made the claim unverifiable against the row quoted directly beneath it (#8930 review).
    """
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    section = _section(doc, "### D4 —")
    approved_doc = (ROOT / "docs" / "product" / "canonical-mcp-tools.md").read_text(
        encoding="utf-8"
    )

    lines = [line for line in section.splitlines() if " is absorbed by " in line]
    assert lines, "§D4 rendered no evidence line — this test would be vacuous"
    for line in lines:
        claimed = re.search(r"— (.+?) is absorbed by `([a-z_]+)`:", line)
        assert claimed, f"an evidence line is not in the rendered shape: {line!r}"
        name = claimed.group(1).split(" (")[0].strip("`")
        assert name in approved_doc, (
            f"the evidence line names `{name}`, which the approved doc never states — the "
            f"quoted row below it cannot be checked against the claim: {line!r}"
        )


def test_approved_menu_closure_comes_from_the_doc_not_a_copy(tmp_path, monkeypatch) -> None:
    """The transitive members are PARSED from the doc's own sentence, not hand-copied.

    `canonical-mcp-tools.md` does not list `query_points_by_tag` in row 2 — it says, in
    prose, that it `sit inside` `query`. A copy of that closure in this file would be a
    second answer that can drift from the doc, which is the defect class of the whole
    issue. Removing the sentence must change the derivation; if it does not, the closure
    is not being read.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    assert "sit inside" in real, (
        "the approved doc no longer states its transitive members — the closure parse "
        "below would be vacuous"
    )
    menu = bt._approved_menu()
    assert "query_points_by_tag" in menu["list_knowledge"]["members"], (
        "the doc says `query_points_by_tag` sits inside `query`, and row 2 absorbs "
        "`query` — the closure is not being applied"
    )

    # ALL FOUR statements, not just the one whose phrase happens to be on one line: the
    # doc line-wraps the fourth (`... ingest_corpus` sit\ninside `index_files`), so a
    # literal-space match parsed three and left `index_files` without two members the doc
    # gives it — silently, because nothing bounded the closure count (#8930 review, P2).
    for member in ("index_sessions", "ingest_corpus"):
        assert member in menu["index_files"]["members"], (
            f"the doc says `{member}` sits inside `index_files`, and row 12 absorbs "
            "`index_files` — the WRAPPED closure statement is not being parsed"
        )
    closures = bt._approved_closures(real)
    assert len(closures) >= bt.APPROVED_MIN_CLOSURES, (
        f"parsed {len(closures)} of the doc's {bt.APPROVED_MIN_CLOSURES} closure "
        "statements — `get`, `overview`, `query` and the wrapped `index_files`"
    )
    assert not bt._approved_errors(*bt._approved_state()), (
        "the real approved doc fails its own structural guards"
    )

    stripped = re.sub(r"[`\w,\s]+?sit\s+inside `[a-z_]+`", "", real)
    path = tmp_path / "approved-without-closure.md"
    path.write_text(stripped, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    closed = bt._approved_menu()
    assert "query_points_by_tag" not in closed["list_knowledge"]["members"], (
        "the closure survived deleting the doc's own sentence — so it is hard-coded "
        "somewhere instead of parsed"
    )
    assert "index_sessions" not in closed["index_files"]["members"], (
        "the wrapped closure survived deleting the doc's own sentence"
    )


def test_approved_menu_guards_fail_closed_on_a_parse_break(tmp_path, monkeypatch) -> None:
    """A parse break must FAIL, not silently resolve every row to "no approved row".

    A guard that resolves nothing cannot fail. If the approved doc is reflowed (or
    renamed), `_approved_menu()` returns {} and every disputed row would read
    `unstated`/`(no approved row names it)` — a green build claiming the owner never
    spoke about any of them.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    empty = tmp_path / "empty.md"
    empty.write_text("# nothing here\n", encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", empty)
    errs = bt._approved_errors(*bt._approved_state())
    assert errs and "UNARMED" in errs[0], f"an empty approved doc did not fail: {errs}"

    thin = tmp_path / "thin.md"
    thin.write_text(
        "| 1 | `search_knowledge` | search |\n| 2 | `list_knowledge` | query |\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bt, "APPROVED_DOC", thin)
    errs = bt._approved_errors(*bt._approved_state())
    assert errs and "silently dropping rows" in errs[0], (
        f"a truncated approved doc did not fail closed: {errs}"
    )


def test_approved_menu_guard_fails_closed_when_a_closure_is_dropped(tmp_path, monkeypatch):
    """A closure the doc DROPS must fail the build — the row bound cannot see it.

    Deleting one of the doc's four `sit inside` statements leaves all 23 rows intact, so
    `APPROVED_MIN_ROWS` stays green while every member that statement carried silently
    disappears. The count is a separate guard for exactly that blind spot (#8930, P2):
    a reflow the parse cannot read must fail, not narrow `members`.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    dropped = re.sub(r"`index_sessions` and `ingest_corpus` sit\s+inside `index_files`\.", "", real)
    assert dropped != real, "the wrapped closure sentence is not in the doc — test is stale"
    path = tmp_path / "approved-minus-one-closure.md"
    path.write_text(dropped, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)

    menu, closures, _ = bt._approved_state()
    assert len(menu) >= bt.APPROVED_MIN_ROWS, (
        "the row bound is what is being bypassed here — the doc must still parse its rows"
    )
    errs = bt._approved_errors(menu, closures)
    assert errs and "transitive-closure" in errs[0], (
        f"a dropped closure statement did not fail the build: {errs}"
    )


def test_approved_menu_guard_fails_closed_when_a_single_row_is_dropped(
    tmp_path, monkeypatch
):
    """The row bound is the doc's own count, and the numbering must stay gapless.

    A loose floor (20 of 23) let THREE rows vanish green; the reviewer's single-row reflow
    (row 3 wrapped out of the `| N | name | absorbs |` shape) parsed 22 rows, passed every
    guard, and flipped `belief_timeline`/`provenance`/`session_context` to "no approved row
    absorbs it" — asserting the menu is silent about a method its own row 3 names (#8930).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    row3 = next(line for line in real.splitlines() if line.startswith("| 3 |"))
    dropped = real.replace(row3 + "\n", "")
    assert dropped != real, "row 3 is not in the doc — test is stale"
    path = tmp_path / "row-dropped.md"
    path.write_text(dropped, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)

    menu, closures, _ = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS - 1, (
        "one row must be gone here — that is what the bound has to catch"
    )
    errs = bt._approved_errors(menu, closures)
    assert errs and "silently dropping rows" in errs[0], (
        f"dropping one approved row did not fail the build: {errs}"
    )

    # ...and the numbering catches a drop the COUNT cannot: a doc that grows a row while
    # losing another still has >= the bound, but a hole in the sequence. The replacement
    # number is one no other row states (24): renumbering row 3 to an EXISTING number would
    # now be caught earlier and more precisely, as a repeated row number (round 3, P1).
    renumbered = real.replace("| 3 |", "| 24 |", 1)
    path2 = tmp_path / "row-renumbered.md"
    path2.write_text(renumbered, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path2)
    menu2, closures2, _ = bt._approved_state()
    assert len(menu2) == bt.APPROVED_MIN_ROWS, "the count is unchanged — only the numbering moved"
    errs = bt._approved_errors(menu2, closures2)
    assert errs and "are missing from the" in errs[0], (
        f"a hole in the row numbering did not fail the build: {errs}"
    )


def test_approved_menu_guard_fails_closed_when_a_member_moves_after_the_em_dash(
    tmp_path, monkeypatch
):
    """Prose is not a member list — including when the doc puts a MEMBER in the prose.

    The em-dash rule is what stops prose minting a home, and it cuts both ways: a member
    list misplaced after the dash silently strips that member, and for the very row #4513
    §1 is about the artifact then prints "no approved row absorbs `annotate_operator`" over
    the approved row that absorbs it (#8930 review).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    moved = real.replace(
        "| 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |",
        "| 21 | `adjust_relationship` | operator_action, mitigate_operator — "
        "annotate_operator also |",
    )
    assert moved != real, "row 21 is not in the doc — test is stale"
    path = tmp_path / "member-after-dash.md"
    path.write_text(moved, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)

    menu, closures, _ = bt._approved_state()
    assert "annotate_operator" not in menu["adjust_relationship"]["members"], (
        "the misplaced list must be the failure under test, so the member is gone here"
    )
    errs = bt._approved_errors(menu, closures)
    assert errs and "AFTER" in errs[0] and "annotate_operator" in errs[0], (
        f"a current tool name in the cell's prose did not fail the build: {errs}"
    )


def test_approved_menu_successors_come_from_the_split_sentence_not_prose() -> None:
    """A cell's PROSE must not mint membership: `dropped for X` is the opposite relation.

    Row 23's cell reads "the seven `onboarding_*` — **OFF the MCP (settled
    2026-09-22)**, dropped for `check_connection`", and a scan of every TARGET name in the
    cell made `check_connection` a member of `run_onboarding` AND its only beta successor
    — so the verdict for it read `successor`/`refuted` (the doc places it there) when the
    doc says the container was dropped FOR it (#8930 review, P3).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    menu = bt._approved_menu()
    assert "check_connection" not in menu["run_onboarding"]["successors"], (
        "`check_connection` was read as `run_onboarding`'s beta successor out of the "
        f"replacement prose: {menu['run_onboarding']['successors']!r}"
    )
    assert (
        bt._approved_verdict(menu, {"run_onboarding"}, "check_connection") != "successor"
    ), "prose minted a successor relation the doc does not state"

    # ...while the sentence that DOES state a split still yields its successors. The
    # assertion is the RULE (successors are exactly the clause's target names), plus the
    # fact the doc currently states — pinning only the exact list would fail on a
    # legitimate owner amendment that adds a third successor, and blame the clause.
    quote = menu["revise_knowledge"]["quote"]
    clause = re.search(r"splits this into([^;]*)", quote)
    assert clause, f"row 17 no longer states its split: {quote!r}"
    clause_targets = {
        t for t in re.findall(r"`([a-z_][a-z0-9_]*)`", clause.group(1)) if t in bt.TARGET_MCP
    }
    assert menu["revise_knowledge"]["successors"] == sorted(clause_targets), (
        "`successors` must be the split clause's target names and nothing else: "
        f"{menu['revise_knowledge']['successors']!r} vs {sorted(clause_targets)!r}"
    )
    assert {"update_knowledge", "supersede_knowledge"} <= clause_targets, (
        "row 17 no longer names the beta split the doc records — update this test with "
        f"the doc: {sorted(clause_targets)!r}"
    )

    # A cell's PROSE must not mint MEMBERSHIP either — `members` is what the verdicts call
    # a row's approved HOME, so a prose-minted member is a false home in the artifact.
    homes_of = {t: [n for n, r in menu.items() if t in r["members"]] for t in
                ("check_connection", "update_knowledge", "supersede_knowledge")}
    assert homes_of["check_connection"] == [], (
        "`check_connection` was read as a MEMBER of "
        f"{homes_of['check_connection']} — the doc says `run_onboarding` was dropped FOR "
        "it (row 23), the opposite relation"
    )
    assert "update_knowledge" not in menu["revise_knowledge"]["members"], (
        "row 17's `fields on \u0060update_knowledge\u0060` prose minted a membership: "
        f"{sorted(menu['revise_knowledge']['members'])!r}"
    )


def test_approved_menu_guards_fail_closed_when_a_closure_does_not_RESOLVE(
    tmp_path, monkeypatch
):
    """Presence is not resolution: a clause that parses, and resolves nothing, must fail.

    The captured member span is raw identifiers (prose words included) and members are
    applied through a `known` filter, so a mangled member list — "these sit inside
    `index_files`" — passes any presence-only check while contributing NO member. The
    container is the other half: renaming only the closure sentence's container parses
    cleanly and drops the whole closure. Both are the "a guard that resolves nothing
    cannot fail" class this build exists to close (#8930 review).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")

    detached = re.sub(
        r"`index_sessions` and `ingest_corpus` sit\s+inside `index_files`",
        "these sit inside `index_files`",
        real,
    )
    assert detached != real, "the wrapped closure sentence is not in the doc — test is stale"
    path = tmp_path / "members-detached.md"
    path.write_text(detached, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    menu, closures, _ = bt._approved_state()
    assert len(closures) >= bt.APPROVED_MIN_CLOSURES, (
        "the statement still PARSES here — that is the point of this test"
    )
    errs = bt._approved_errors(menu, closures)
    assert errs and "member(s) the doc never uses" in errs[0], (
        f"a closure that parses to no known member did not fail the build: {errs}"
    )

    # ...and a PARTIAL loss is the same failure one name at a time: `zzz` beside a real
    # member would otherwise narrow the row silently (resolution is all-or-nothing).
    partial = re.sub(
        r"`query_points_by_tag` sit\s+inside `query`",
        "`zzz` sit inside `query`",
        real,
    )
    assert partial != real, "the `query` closure sentence is not in the doc — test is stale"
    path3 = tmp_path / "member-unresolved.md"
    path3.write_text(partial, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path3)
    menu3, closures3, _ = bt._approved_state()
    assert "paginated_query" in menu3["list_knowledge"]["members"], (
        "the real member resolved — so the failure below is about `zzz` alone"
    )
    errs = bt._approved_errors(menu3, closures3)
    assert errs and "zzz" in errs[0], (
        f"a PARTIALLY unresolved closure statement did not fail the build: {errs}"
    )

    renamed = real.replace(
        "`index_sessions` and `ingest_corpus` sit\ninside `index_files`",
        "`index_sessions` and `ingest_corpus` sit\ninside `some_other_container`",
    )
    assert renamed != real, "the wrapped closure sentence is not in the doc — test is stale"
    path2 = tmp_path / "container-renamed.md"
    path2.write_text(renamed, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path2)
    menu2, closures2, _ = bt._approved_state()
    errs = bt._approved_errors(menu2, closures2)
    assert errs and "container" in errs[0], (
        f"a closure whose CONTAINER the doc never names did not fail the build: {errs}"
    )


def test_approved_menu_guard_fails_closed_when_the_split_clause_is_reworded(
    tmp_path, monkeypatch
):
    """The successor parse is bounded like the closure parse, not just content-pinned.

    Row 17's "splits this into `update_knowledge` + `supersede_knowledge`" is the only
    place a successor is stated. Rewording the clause (a benign-looking doc edit) emptied
    `revise_knowledge.successors`, which flipped `tortoise_invalidate` from `successor`
    (support) to `rehomed` (a disagreement) and changed an owner-facing count — with a
    green build, because only the TEST pinned the list (#8930 review, P3).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")

    reworded = real.replace("splits this into", "splits into")
    assert reworded != real, "the doc no longer states a split — test is stale"
    path = tmp_path / "split-reworded.md"
    path.write_text(reworded, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    menu, closures, _ = bt._approved_state()
    assert len(menu) >= bt.APPROVED_MIN_ROWS, "the doc must still parse its rows"
    assert menu["revise_knowledge"]["successors"] == [], (
        "the reword is what this test is about — it does empty the successors"
    )
    errs = bt._approved_errors(menu, closures)
    assert errs and "beta-split" in errs[0], (
        f"a reworded split clause did not fail the build: {errs}"
    )

    # ...and a clause that still parses but names no target is the second half.
    emptied = real.replace(
        "splits this into `update_knowledge` + `supersede_knowledge`",
        "splits this into two new tools",
    )
    assert emptied != real, "the split clause is not in the doc — test is stale"
    path2 = tmp_path / "split-unresolved.md"
    path2.write_text(emptied, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path2)
    menu2, closures2, _ = bt._approved_state()
    assert menu2["revise_knowledge"]["split_clauses"] == 1, (
        "the clause still parses here — that is the point of this half"
    )
    errs = bt._approved_errors(menu2, closures2)
    assert errs and "NO target name" in errs[0], (
        f"a split clause naming no target did not fail the build: {errs}"
    )

    # A PARTIAL loss is the same failure one name at a time: a successor renamed to a
    # name the target surface does not have used to be DISCARDED silently — narrowing the
    # row, flipping `tortoise_invalidate` from `successor` (support) to `rehomed` (a
    # disagreement) and moving an owner-facing count, with a green build.
    partial = real.replace(
        "`update_knowledge` + `supersede_knowledge`",
        "`update_knowledge` + `supersede_beliefs`",
    )
    assert partial != real, "the split clause is not in the doc — test is stale"
    path3 = tmp_path / "split-partial.md"
    path3.write_text(partial, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path3)
    menu3, closures3, _ = bt._approved_state()
    assert menu3["revise_knowledge"]["successors"] == ["update_knowledge"], (
        "the surviving successor must still resolve — only the renamed one is lost"
    )
    assert menu3["revise_knowledge"]["split_unresolved"] == ["supersede_beliefs"], (
        f"the clause's unresolved name was discarded, not recorded: "
        f"{menu3['revise_knowledge']['split_unresolved']!r}"
    )
    errs = bt._approved_errors(menu3, closures3)
    assert errs and "supersede_beliefs" in errs[0], (
        f"a PARTIALLY unresolved split clause did not fail the build: {errs}"
    )


def test_approved_menu_expands_the_docs_wildcard_member_family() -> None:
    """Row 23 states its members as a FAMILY (`onboarding_*`), and the parse must honour it.

    The em-dash fix removed prose-minted members, and row 23's only member statement is
    "the seven `onboarding_*`" — which parses to no identifier. Without expanding it, any of
    the seven real tools read `refuted` and the artifact prints "no approved row absorbs
    `onboarding_state`": the exact opposite of the row, which absorbs all seven (#8930
    review). Latent (§D4 renders none of them today); the family is the doc's own words.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    menu = bt._approved_menu()
    seven = {
        "onboarding_state",
        "onboarding_seed",
        "onboarding_demo_create",
        "onboarding_github_connect",
        "onboarding_github_index",
        "onboarding_github_status",
        "onboarding_session_recording",
    }
    missing = seven - menu["run_onboarding"]["members"]
    assert not missing, f"the `onboarding_*` family was not expanded: missing {sorted(missing)}"
    assert bt._approved_verdict(menu, {"onboarding_state"}, "run_onboarding") == "row", (
        "row 23 absorbs `onboarding_state`; a false `refuted` here would publish a "
        "contradiction the approved doc does not contain"
    )
    # The replacement prose is still NOT membership: `check_connection` replaced the
    # container, so it is not one of its members.
    assert "check_connection" not in menu["run_onboarding"]["members"]


def test_approved_evidence_line_never_renders_an_empty_method() -> None:
    """A retired registry entry has no `sdk_method`; the evidence line must still read.

    Seven retired onboarding entries carry `sdk_method == ""`, so the no-home branch
    rendered "no approved row absorbs ``" — malformed markdown with the name missing
    exactly where the reader is told no row absorbs it (#8930 review).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    lines = bt._approved_evidence_lines(
        [{"tool": "tortoise_onboarding_state", "method": "", "homes": [], "homes_detail": []}]
    )
    assert "``" not in "\n".join(lines), f"an empty method rendered an empty code span: {lines!r}"
    assert "tortoise_onboarding_state" in lines[0], (
        f"the line names neither the method nor the tool: {lines!r}"
    )


def test_multi_home_row_quotes_every_home_and_never_says_none_absorbs() -> None:
    """A method absorbed by SEVERAL approved rows must render all of them, not "none".

    `home`/`quote` are the single-home convenience, so they are `None`/`""` for a
    multi-home method. Branching the evidence list on the QUOTE therefore printed "no
    approved row absorbs `x`" a few lines under a table cell that named several rows
    absorbing it — two cells of one generated section asserting opposite things (#8930
    review, P2). Latent today (every disputed row has exactly one home), and reachable the
    moment the approved doc names one name in two rows.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    menu = {
        "list_knowledge": {
            "lineno": 2,
            "members": {"list_knowledge", "query"},
            "successors": [],
            "quote": "| 2 | `list_knowledge` | query |",
        },
        "poll_events": {
            "lineno": 8,
            "members": {"poll_events", "query"},
            "successors": ["check_connection"],
            "quote": "| 8 | `poll_events` | query |",
        },
    }
    findings = ([{"tool": "tortoise_query", "named": ["list_knowledge"]}], [], [])
    readings = bt._approved_readings(menu, findings)
    assert len(readings) == 1, readings
    a = readings[0]
    assert a["homes"] == ["list_knowledge", "poll_events"], a["homes"]
    assert a["home"] is None and a["quote"] == "", "the single-home fields are the shape under test"
    assert [h["row_no"] for h in a["homes_detail"]] == [2, 8], (
        f"the multi-home row dropped a home's row number: {a['homes_detail']!r}"
    )
    assert a["successors"] == ["check_connection"], (
        f"the multi-home row dropped a home's successors: {a['successors']!r}"
    )

    lines = "\n".join(bt._approved_evidence_lines(readings))
    assert "no approved row absorbs" not in lines, (
        f"the evidence list contradicts its own table cell: {lines!r}"
    )
    for home in ("`list_knowledge` (row 2)", "`poll_events` (row 8)"):
        assert home in lines, f"the evidence list omits {home}: {lines!r}"
    for quote in (menu["list_knowledge"]["quote"], menu["poll_events"]["quote"]):
        assert quote in lines, f"the evidence list omits a home's quoted row: {lines!r}"


def test_approved_verdict_is_mechanical_in_all_five_directions() -> None:
    """`row` / `successor` / `refuted` / `rehomed` / `unstated`, on a synthetic menu.

    The verdicts are the entire ruling-free content of §D4: `row` and `successor` are
    support, `refuted` and `rehomed` are the approved doc placing the method elsewhere,
    and `unstated` is silence. Collapsing `refuted` into `unstated` would turn #4513 §2
    back into an open question; collapsing `rehomed` into `unstated` denies an approved
    home the same section quotes. Each direction is pinned separately.
    """
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import _approved_verdict

    menu = {
        "poll_events": {"members": {"poll_events", "events_poll"}, "successors": []},
        "revise_knowledge": {
            "members": {"revise_knowledge", "promote_point", "invalidate"},
            "successors": ["update_knowledge"],
        },
        "adjust_relationship": {
            "members": {"adjust_relationship", "annotate_operator"},
            "successors": [],
        },
    }
    assert _approved_verdict(menu, {"annotate_operator"}, "adjust_relationship") == "row"
    assert _approved_verdict(menu, {"promote_point"}, "update_knowledge") == "successor"
    assert _approved_verdict(menu, {"annotate_operator"}, "update_knowledge") == "refuted"
    assert _approved_verdict(menu, {"belief_timeline"}, "poll_events") == "refuted"
    assert _approved_verdict(menu, {"belief_timeline"}, "check_confidence") == "unstated"
    # `rehomed`: the method HAS an approved home, the destination is a name the doc never
    # uses. That is a disagreement reached through a different door, not silence — the
    # difference is what stops an owner-facing row from denying its own quoted evidence.
    assert _approved_verdict(menu, {"events_poll"}, "check_confidence") == "rehomed"
    assert _approved_verdict(menu, {"invalidate"}, "refresh_confidence") == "rehomed"


def test_approved_row_that_reflows_fails_the_build_instead_of_truncating_its_members(
    tmp_path, monkeypatch, capsys
) -> None:
    """A wrapped `Absorbs` cell must FAIL the build — never parse as a shorter row.

    `cells` is split out of ONE physical line, so a cell that reflows onto a second line
    leaves its continuation unread while the row still parses. Measured before the fix:
    adding a newline inside row 21's cell still parsed 23 rows with no guard firing,
    `mitigate_operator` silently left the row, and the regenerated artifact told the owner
    `annotate_operator` had "*no approved row* … refuted" — a false statement about the
    owner-approved doc, in the artifact #4513 exists to make trustworthy (#8930 review, P1).

    The row is DETECTED, not joined: a widened regex would accept the wrap and keep
    guessing at the continuation.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    wrapped = real.replace(
        "operator_action, annotate_operator, mitigate_operator |",
        "operator_action, annotate_operator,\nmitigate_operator |",
        1,
    )
    path = tmp_path / "row-reflowed.md"
    path.write_text(wrapped, encoding="utf-8")
    monkeypatch.setattr(bt, "APPROVED_DOC", path)

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS - 1, (
        f"the wrapped row must NOT parse — {len(menu)} rows were read"
    )
    assert "adjust_relationship" not in menu, (
        "row 21 parsed anyway, so its member list is a truncation of what the doc says"
    )

    errs = bt._approved_errors(menu, closures, row_errors)
    assert errs and "is not a complete physical row" in errs[0], (
        f"a reflowed row reported nothing about itself: {errs}"
    )
    assert "row 21" in errs[0], f"the diagnostic does not name the row: {errs[0]!r}"
    assert any("silently dropping rows" in e for e in errs), (
        f"the row-count bound must still fire as the backstop: {errs}"
    )

    # ...and through the entry point the gate runs: the build is RED, so no artifact is
    # written carrying a `refuted` verdict the approved doc does not support.
    monkeypatch.setattr(sys, "argv", ["bridge_table.py", "--check"])
    assert bt.main() == 1, "a reflowed approved row did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "is not a complete physical row" in err, err


def test_approved_row_with_two_cells_reports_a_diagnostic_not_an_traceback(
    tmp_path, monkeypatch, capsys
) -> None:
    """A row with no `Absorbs` cell must fail the build through the NORMAL diagnostic.

    `cells[2]` was indexed blind, so a row that states only `| N | name |` raised
    `IndexError` — a traceback that bypassed `main()`'s `BRIDGE TABLE BUILD FAILURE` path,
    which is the only thing that reports a broken parse as a broken parse (#8930 review,
    P2a). BOTH physical shapes that state two cells are pinned: the short row
    (`| 3 | `recall_beliefs` |`) and `| 3 | `recall_beliefs` ||` — the second one ENDS in
    `|`, so it reads as a complete row and only the cell-count guard can see it.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    row3 = next((ln for ln in real.splitlines() if ln.startswith("| 3 |")), "")
    assert row3, "row 3 is not in the doc — this test is stale"

    for shape in ("| 3 | `recall_beliefs` |", "| 3 | `recall_beliefs` ||"):
        path = tmp_path / f"row-shape-{len(shape)}.md"
        path.write_text(real.replace(row3, shape, 1), encoding="utf-8")
        monkeypatch.setattr(bt, "APPROVED_DOC", path)

        menu, _, _ = bt._approved_state()
        assert len(menu) == bt.APPROVED_MIN_ROWS - 1, (
            f"{shape!r}: a row with no Absorbs cell must not parse — {len(menu)} rows "
            "were read"
        )

        # The diagnostic, not a traceback: `main()` must report it and return 1.
        monkeypatch.setattr(sys, "argv", ["bridge_table.py", "--check"])
        assert bt.main() == 1, f"{shape!r} did not fail the build"
        err = capsys.readouterr().err
        assert "BRIDGE TABLE BUILD FAILURE" in err, err
        assert f"approved row 3 (`recall_beliefs`) in {path.name}" in err, err
        assert "parses to 2 cell(s)" in err, f"{shape!r} was not reported as a short row:\n{err}"
        assert "IndexError" not in err and "Traceback" not in err, err


def _assert_an_orphaned_continuation_fails_the_build(
    tmp_path, monkeypatch, capsys, continuation: str
) -> None:
    """A wrapped row that leaves an ORPHANED line in the table body must fail the build.

    `_APPROVED_ROW` only proves the line ENDS in `|`; it is not a completeness test. When a
    wrap leaves a `|` at the end of line 1 the row prefix still matches, `cells` is split
    from line 1 alone, and the continuation matches no row pattern — so it was never
    inspected at all. Measured on the real doc, row 21, both callers of this helper built
    GREEN (`rows=23`, `row_errors=[]`, `main()` → 0) and wrote an artifact asserting
    `tortoise_mitigate_operator` "*(no approved row names it)*" / **refuted** — the same
    false claim about the owner-approved doc the P1 was raised for, with no diagnostic.

    The mutation is in the test, so the guard cannot rot into a tautology: with the orphan
    test DISABLED (every body line reads as a separator) the exact same document parses,
    builds green and writes the false artifact; with it ON the build is red and nothing is
    written. Both halves are asserted here, in that order.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    wrapped = real.replace(
        cell, cell.replace("mitigate_operator |", continuation), 1
    )
    assert wrapped != real, f"the wrap {continuation!r} did not change the doc"
    path = tmp_path / "row-orphaned.md"
    path.write_text(wrapped, encoding="utf-8")
    out = tmp_path / "bridge-table.md"
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    monkeypatch.setattr(bt, "OUT", out)

    # ---- the guard ON: the orphan line is reported, attributed to the row it truncates.
    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every row is still syntactically a row here ({len(menu)} parsed) — the orphan "
        "line is the ONLY evidence, which is why the body, not the row regex, decides"
    )
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"], (
        "this shape is supposed to TRUNCATE row 21 — if it no longer does, the test no "
        "longer reproduces the defect"
    )
    assert len(row_errors) == 1, f"the orphan line was not reported exactly once: {row_errors}"
    assert "ORPHANED continuation line after row 21 (`adjust_relationship`)" in row_errors[0], (
        f"the diagnostic does not name the row the wrap truncated: {row_errors[0]!r}"
    )
    errs = bt._approved_errors(menu, closures, row_errors)
    assert errs[0] == row_errors[0], (
        f"the specific cause must print BEFORE the generic row-count diagnostic: {errs}"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])  # WRITE mode, not --check
    assert bt.main() == 1, f"the wrap {continuation!r} did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "ORPHANED continuation line after row 21" in err, err
    assert not out.exists(), (
        f"the build failed but still wrote {out.name} — a false refutation of the "
        "owner-approved doc is exactly what must not reach the artifact"
    )

    # ---- MUTATION: disable the body test outright. Same document, same row parse — and
    # the defect comes straight back, green, in the artifact. (Before round 3 this mutated
    # `_APPROVED_SEPARATOR` to `.*`; the separator rule is part of `_body_error` itself now,
    # so disabling the whole body test is both the equivalent and the narrower mutation.)
    monkeypatch.setattr(bt, "_body_error", lambda *a, **k: None)
    menu_off, _, row_errors_off = bt._approved_state()
    assert row_errors_off == [], (
        f"the mutation did not disable the orphan test: {row_errors_off}"
    )
    assert "mitigate_operator" not in menu_off["adjust_relationship"]["members"]
    assert bt.main() == 0, "with the orphan test disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the defect — the test proves nothing"
    )
    out.unlink()


def test_orphaned_continuation_line_that_starts_with_a_pipe_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 1: `…annotate_operator, |` ⏎ `| mitigate_operator |`.

    The first line still ENDS in `|`, so `_APPROVED_ROW` matches and the continuation was
    never read — measured green with `rows=23`/`row_errors=[]` before the fix. This is the
    shape a person or agent produces when repairing a wrapped row, because every physical
    line keeps looking like table syntax (#8930 review, round 2, P1).
    """
    _assert_an_orphaned_continuation_fails_the_build(
        tmp_path, monkeypatch, capsys, "|\n| mitigate_operator |"
    )


def test_orphaned_continuation_line_without_a_pipe_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 2: `…annotate_operator, |` ⏎ `mitigate_operator |`.

    Same escape, spelled without the leading `|` — the continuation is a bare member list,
    so it is not even shaped like a row. Both shapes shared ONE root: completeness was
    decided by how a line ENDS, never by whether the table body is intact. Also measured
    green with `rows=23`/`row_errors=[]` before the fix (#8930 review, round 2, P1).
    """
    _assert_an_orphaned_continuation_fails_the_build(
        tmp_path, monkeypatch, capsys, "|\nmitigate_operator |"
    )


def _edited_doc(tmp_path, monkeypatch, edited: str, name: str) -> tuple[Path, Path]:
    """Point the builder at `edited` as the approved doc, with a fresh artifact path."""
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    doc = tmp_path / f"{name}.md"
    doc.write_text(edited, encoding="utf-8")
    out = tmp_path / f"{name}-artifact.md"
    monkeypatch.setattr(bt, "APPROVED_DOC", doc)
    monkeypatch.setattr(bt, "OUT", out)
    return doc, out


def _disable_the_round_3_guards(monkeypatch) -> None:
    """MUTATION: the identity guard and the body test contribute nothing.

    Turning them into `None` is the only way a test can state what they prevent, and it
    keeps each test below from rotting into a tautology: the SAME document is built twice,
    once with the guards answering nothing (the pre-fix behaviour, measured) and once armed.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    monkeypatch.setattr(bt, "_row_identity_error", lambda *a, **k: None)
    monkeypatch.setattr(bt, "_body_error", lambda *a, **k: None)


def test_a_row_shaped_continuation_cannot_replace_the_row_above_it(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 1 — a wrap whose continuation is ITSELF a row RE-REGISTERED as row 21.

    `…annotate_operator, |` ⏎ `| 21 | `adjust_relationship` | mitigate_operator |` ⏎ `| 22 | …`

    Both lines are legal table rows and both are legal table syntax, so the body test had
    nothing to say: the row loop matched the continuation, and `menu[name] = {…}` is an
    unconditional assign keyed by NAME, so the one-member remainder silently REPLACED the
    complete row. Measured before the fix: `main()` → 0, artifact WRITTEN, 23 rows parsed,
    `row_errors == []`, `tortoise_annotate_operator` rendered "*(no approved row names it)*"
    / **refuted**, and the section count moved 5/7/0 → 4/7/1. Nothing bounded duplicates —
    the count guard checks only for MISSING row numbers (#8930 review, round 3, P1).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    edited = real.replace(
        cell,
        "operator_action, annotate_operator, |\n"
        "| 21 | `adjust_relationship` | mitigate_operator |",
        1,
    )
    assert edited != real, "the row-shaped continuation did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "dup-number-same-name")

    # ---- the guard ON: the repeated identity is reported, naming BOTH occurrences.
    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every line here is shaped like a row ({len(menu)} parsed) — which is why the "
        "row's IDENTITY, not its shape, is the datum"
    )
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"], (
        "the discarded row must contribute NOTHING — merging it is the same truncation "
        "from the other side"
    )
    assert "annotate_operator" in menu["adjust_relationship"]["members"], (
        "the row the doc states must survive intact"
    )
    assert len(row_errors) == 1, f"the repeated row was not reported exactly once: {row_errors}"
    assert "approved row 21 (`adjust_relationship`) in" in row_errors[0], row_errors[0]
    assert "REUSES both the number 21 and the name `adjust_relationship`" in row_errors[0], (
        f"the diagnostic does not name both occurrences: {row_errors[0]!r}"
    )
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0], (
        "the specific cause must print BEFORE the generic structure diagnostics"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])  # WRITE mode, not --check
    assert bt.main() == 1, "a row-shaped continuation did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "approved row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), (
        f"the build failed but still wrote {out.name} — a false refutation of the "
        "owner-approved doc is exactly what must not reach the artifact"
    )

    # ---- MUTATION: both round-3 guards contribute nothing. The later row replaces row 21,
    # `annotate_operator` leaves it, and the defect returns — green, in the artifact.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert menu_off["adjust_relationship"]["members"] == {
        "adjust_relationship",
        "mitigate_operator",
    }, f"the mutation did not reproduce the overwrite: {menu_off['adjust_relationship']}"
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_annotate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the false refutation — the test proves nothing"
    )
    out.unlink()


def test_a_repeated_row_number_under_a_new_name_cannot_add_a_phantom_row(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 2 — a duplicated row number under a DIFFERENT name: 24 rows, all green.

    Every bound in `_approved_structure_errors` is a FLOOR ("at least 23") plus a check for
    MISSING numbers, so a repeat that ADDS a row is invisible to all of them: measured
    before the fix, `main()` → 0, 24 rows parsed, `row_errors == []`, artifact written. The
    doc states one row 5; the artifact then carries two, and only a human comparing row
    numbers against the doc could see it (#8930 review, round 3, P1).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    row5 = next((ln for ln in real.splitlines() if ln.startswith("| 5 |")), "")
    assert row5, "row 5 is not in the doc — this test is stale"
    edited = real.replace(
        row5, row5 + "\n| 5 | `get_historical_knowledge` | check_connection |", 1
    )
    assert edited != real, "the repeated row number did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "dup-number-new-name")

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"the doc states 23 rows and parses {len(menu)} — the second `| 5 |` must not "
        "register as a row at all"
    )
    assert "get_historical_knowledge" not in menu, (
        "the phantom row was kept, so the menu holds a row the doc does not state"
    )
    assert len(row_errors) == 1, f"the repeated number was not reported exactly once: {row_errors}"
    assert (
        "approved row 5 (`get_historical_knowledge`) in" in row_errors[0]
        and "REUSES row number 5, already stated for row 5 (`explore_connections`)"
        in row_errors[0]
    ), f"the diagnostic does not name both occurrences: {row_errors[0]!r}"
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0]

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a repeated row number did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "approved row 5 (`get_historical_knowledge`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: 24 rows, rc 0, artifact written — the count bound cannot see it.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert len(menu_off) == bt.APPROVED_MIN_ROWS + 1, (
        f"the mutation must reproduce the phantom 24th row, parsed {len(menu_off)}"
    )
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    assert out.exists(), (
        "the pre-fix build wrote an artifact for a doc whose row 5 is stated TWICE — no "
        "count or sequence bound can see a repeat that only ADDS a row"
    )
    out.unlink()


def test_a_repeated_row_name_cannot_overwrite_another_rows_members(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 3 — the same NAME on a different number, which no number bound can see.

    `menu` is keyed by name, so this is the overwrite from the other side: row 21's cell is
    replaced by the new row's and `annotate_operator` / `mitigate_operator` lose their
    approved home. Renumbering hides it from the missing-number check too, so before the fix
    the ONLY thing that fired was the anonymous row count ("only 22 approved rows parsed") —
    a diagnostic that names no row and states no cause, on a document that is otherwise
    shaped perfectly (#8930 review, round 3, P1).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    row23 = next((ln for ln in real.splitlines() if ln.startswith("| 23 |")), "")
    assert row23, "row 23 is not in the doc — this test is stale"
    edited = real.replace(
        row23, row23 + "\n| 24 | `adjust_relationship` | mitigate_operator |", 1
    )
    assert edited != real, "the repeated row name did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "dup-name-other-number")

    menu, closures, row_errors = bt._approved_state()
    assert row_errors and row_errors[0].startswith(
        "approved row 24 (`adjust_relationship`) in"
    ), f"the repeated name was not named first: {row_errors}"
    assert "REUSES the name `adjust_relationship` of row 21" in row_errors[0], (
        f"the diagnostic does not name both occurrences: {row_errors[0]!r}"
    )
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0], (
        "the named cause must print BEFORE the anonymous row-count complaint"
    )
    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a repeated row name did not fail the build"
    err = capsys.readouterr().err
    assert "approved row 24 (`adjust_relationship`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: no guard fires at all; the row 21 cell is simply GONE, and the only
    # diagnostic left is the anonymous row count — the failure this guard replaces.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert menu_off["adjust_relationship"]["lineno"] == 24, (
        f"the mutation must reproduce the overwrite, got {menu_off['adjust_relationship']}"
    )
    assert "annotate_operator" not in menu_off["adjust_relationship"]["members"]
    assert len(menu_off) == bt.APPROVED_MIN_ROWS, (
        f"the count is unchanged ({len(menu_off)}) — a repeat that only RENAMES leaves no "
        "count to see"
    )
    assert bt.main() == 1, "the numbered-sequence bound is still the backstop"
    err_off = capsys.readouterr().err
    assert "row number(s) 21 are missing" in err_off, err_off
    assert "REUSES" not in err_off and "adjust_relationship" not in err_off, (
        "the pre-fix diagnostic named the offending row after all — then this mutation is "
        f"not the behaviour it claims to fix: {err_off}"
    )
    assert not out.exists()


def test_a_continuation_after_a_blank_line_still_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 4 — the wrap STRADDLES the blank line that ends the table.

    `…annotate_operator, |` ⏎ *(blank)* ⏎ `mitigate_operator |`: the body pass ends at the
    blank line, so the orphan belongs to no body and no in-body rule can see it. Measured
    before the fix: `main()` → 0, artifact WRITTEN, `mitigate_operator` falsely refuted,
    5/7/0 → 4/7/1 (#8930 review, round 3, P2).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    edited = real.replace(
        cell, cell.replace("mitigate_operator |", "|\n\nmitigate_operator |"), 1
    )
    assert edited != real, "the blank-line wrap did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "continuation-after-blank")

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every row still parses here ({len(menu)}) — the fragment is the only evidence"
    )
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"]
    assert len(row_errors) == 1, f"the fragment was not reported exactly once: {row_errors}"
    assert (
        "ORPHANED continuation line after row 21 (`adjust_relationship`), on the far side "
        "of a BLANK line" in row_errors[0]
    ), f"the diagnostic does not name the row and the blank: {row_errors[0]!r}"
    assert "'mitigate_operator |'" in row_errors[0], row_errors[0]
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0]

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a continuation across a blank line did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "after row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: the body test contributes nothing; the same false artifact returns.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert "mitigate_operator" not in menu_off["adjust_relationship"]["members"]
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the false refutation — the test proves nothing"
    )
    out.unlink()


def test_a_separator_shaped_continuation_still_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 5 — the continuation is SEPARATOR-shaped, so it read as table syntax.

    `…, |` ⏎ `|---|---|---|`: the separator branch skipped it as legitimate formatting, and
    every body test passed. Measured before the fix: `main()` → 0, artifact WRITTEN,
    `mitigate_operator` falsely refuted, 5/7/0 → 4/7/1 (#8930 review, round 3, P2).

    The rule is positional, not cosmetic: the header's own separator comes BEFORE every row,
    so a `|---|` line after a row has started cannot be one.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    edited = real.replace(cell, cell.replace("mitigate_operator |", "|\n|---|---|---|"), 1)
    assert edited != real, "the separator-shaped continuation did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "separator-shaped")

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"]
    assert len(row_errors) == 1, f"the separator fragment was not reported once: {row_errors}"
    assert (
        "SEPARATOR-shaped line after row 21 (`adjust_relationship`)" in row_errors[0]
    ), f"the diagnostic does not name the row: {row_errors[0]!r}"
    assert "'|---|---|---|'" in row_errors[0], row_errors[0]
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0]

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a separator-shaped continuation did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "after row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: the body test contributes nothing; the same false artifact returns.
    _disable_the_round_3_guards(monkeypatch)
    _, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the false refutation — the test proves nothing"
    )
    out.unlink()


def test_a_pipe_less_continuation_after_a_blank_line_still_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 6 — the wrap straddles the blank AND the remainder carries no `|` at all.

    `…annotate_operator, |` ⏎ *(blank)* ⏎ `mitigate_operator`: round 3's post-blank rule
    reported a line there only when it was PIPE-BOUNDED, so the pipe-less spelling a paste
    or a soft reflow produces went straight through. Measured before the fix: `main()` → 0,
    artifact WRITTEN, `rows=23`, `row_errors=[]`, partition 5/7/0 → 4/7/1, and the artifact
    quoting the TRUNCATED row as though it were "the approved row itself … so it cannot be a
    truncation of what the doc says" (#8930 review, round 4, P2).

    The rule is a FRAGMENT test, not a pipe test: an `Absorbs` remainder is a
    comma-separated list of names, a shape a sentence cannot take — which is what keeps the
    doc's own post-table paragraphs green (`_is_member_list`).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    edited = real.replace(
        cell, cell.replace("mitigate_operator |", "|\n\nmitigate_operator"), 1
    )
    assert edited != real, "the pipe-less blank-line wrap did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "pipe-less-after-blank")

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every row still parses here ({len(menu)}) — the bare remainder is the only evidence"
    )
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"]
    assert len(row_errors) == 1, f"the bare fragment was not reported exactly once: {row_errors}"
    assert (
        "ORPHANED continuation line after row 21 (`adjust_relationship`), on the far side "
        "of a BLANK line" in row_errors[0]
    ), f"the diagnostic does not name the row and the blank: {row_errors[0]!r}"
    assert "'mitigate_operator'" in row_errors[0], (
        "the diagnostic does not quote the bare remainder, so it cannot be about it: "
        f"{row_errors[0]!r}"
    )
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0]

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a pipe-less continuation across a blank line did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "after row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: the body test contributes nothing; the same false artifact returns.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert "mitigate_operator" not in menu_off["adjust_relationship"]["members"]
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the false refutation — the test proves nothing"
    )
    out.unlink()


def test_a_fragment_that_is_not_the_first_line_after_the_close_still_fails_the_build(
    tmp_path, monkeypatch, capsys
) -> None:
    """Shape 7 — a prose line lands BETWEEN the closing blank and the fragment.

    The round-3 scan consumed `closed_row` on the first non-blank line, so it inspected
    exactly one line and a single intervening paragraph hid the fragment completely.
    Measured before the fix: `main()` → 0, artifact WRITTEN, `row_errors=[]`,
    `mitigate_operator` falsely refuted (#8930 review, round 4, P2). The scan now runs to
    the next TABLE, so any number of prose lines in between is fine.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"
    edited = real.replace(
        cell,
        cell.replace(
            "mitigate_operator |",
            "|\n\nThat table above is the write half.\n\nmitigate_operator",
        ),
        1,
    )
    assert edited != real, "the not-first-after-close wrap did not change the doc"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "fragment-not-first")

    menu, _, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every row still parses here ({len(menu)}) — the fragment is the only evidence"
    )
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"]
    assert len(row_errors) == 1, (
        "the fragment behind a prose line was not reported exactly once — the intervening "
        f"paragraph still shadows it: {row_errors}"
    )
    assert (
        "ORPHANED continuation line after row 21 (`adjust_relationship`)" in row_errors[0]
    ), f"the diagnostic does not name the row the wrap truncated: {row_errors[0]!r}"
    assert "'mitigate_operator'" in row_errors[0], row_errors[0]
    assert "That table above is the write half" not in row_errors[0], (
        "the intervening PROSE was reported; only the fragment may be: "
        f"{row_errors[0]!r}"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 1, "a fragment behind a prose line did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "after row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), "the build failed but still wrote the artifact"

    # ---- MUTATION: the body test contributes nothing; the same false artifact returns.
    _disable_the_round_3_guards(monkeypatch)
    menu_off, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not disable the guards: {errs_off}"
    assert "mitigate_operator" not in menu_off["adjust_relationship"]["members"]
    assert bt.main() == 0, "with the guards disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*" in written, (
        "the mutation did not reproduce the false refutation — the test proves nothing"
    )
    out.unlink()


# ─────────────────────────────────────────────────────────────────────
# Round 5 — the remaining shape is decided by NORMALISING the tokens, not
# by enumerating the ways a wrap can be spelled.  Each test below is one
# spelling that round 4's predicate answered `False` for, and each was
# measured building GREEN with the false refutation in the artifact.
# ─────────────────────────────────────────────────────────────────────
def _restore_the_round_4_predicate(monkeypatch) -> None:
    """MUTATION: put back round 4's `_is_member_list` — spelling by spelling.

    The pre-fix predicate, verbatim: strip TRAILING commas/whitespace, split on `,`, strip
    backticks, and demand every token be a known name. Restoring it is the sharpest mutation
    available — the rest of the parse is untouched, so a document that builds green under it
    and red under the current one differs in this predicate and nothing else. Measured: every
    spelling below goes green with the false `refuted` claim written to the artifact.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    def round_4_predicate(stripped: str, known: set[str]) -> bool:
        body = stripped.rstrip(", \t")
        if not body:
            return False
        tokens = [t.strip().strip("`").strip() for t in body.split(",")]
        return all(tokens) and all(t in known for t in tokens)

    monkeypatch.setattr(bt, "_is_member_list", round_4_predicate)


def _row_21_broken_at(real: str, line1_cell: str, remainder: str) -> str:
    """Row 21's cell cut so `remainder` lands on its own line, past a BLANK line.

    The row's trailing `|` stays on line 1, which is the shape that was measured: the row
    still parses as a legal table row, with a TRUNCATED `Absorbs` cell.
    """
    row = "| 21 | `adjust_relationship` | operator_action, annotate_operator, mitigate_operator |"
    assert row in real, "row 21's row is not in the doc — this test is stale"
    broken = f"| 21 | `adjust_relationship` | {line1_cell} |\n\n{remainder}\n"
    edited = real.replace(row, broken, 1)
    assert edited != real, "the break did not change the doc"
    return edited


def _the_break_fails_closed(
    tmp_path,
    monkeypatch,
    capsys,
    *,
    edited: str,
    name: str,
    remainder: str,
    lost: set[str],
) -> None:
    """Shared body of the round-5 shape tests: red now, green-with-a-lie under round 4.

    `lost` are the row-21 members this spelling truncates away, i.e. exactly the methods the
    false "no approved row absorbs it" claim would be made about.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    _, out = _edited_doc(tmp_path, monkeypatch, edited, name)

    # ---- the guard ON: the remainder is reported, attributed to the row it truncated.
    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, (
        f"every row still parses here ({len(menu)}) — the remainder is the ONLY evidence, "
        "which is why the body, not the row regex, has to decide"
    )
    members = menu["adjust_relationship"]["members"]
    for member in sorted(lost):
        assert member not in members, (
            f"this spelling is supposed to TRUNCATE row 21 and drop `{member}` — if it no "
            "longer does, the test no longer reproduces the defect"
        )
    assert len(row_errors) == 1, (
        f"the remainder was not reported exactly once (the guard answers `False` for it): "
        f"{row_errors}"
    )
    assert "ORPHANED continuation line after row 21 (`adjust_relationship`)" in row_errors[0], (
        f"the diagnostic does not name the row the wrap truncated: {row_errors[0]!r}"
    )
    assert repr(remainder) in row_errors[0], (
        f"the diagnostic does not quote this remainder, so it cannot be about it: "
        f"{row_errors[0]!r}"
    )
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0], (
        "the specific cause must print BEFORE the generic row-count diagnostic"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])  # WRITE mode, not --check
    assert bt.main() == 1, f"the remainder {remainder!r} did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "after row 21 (`adjust_relationship`)" in err, err
    assert not out.exists(), (
        "the build failed but still wrote the artifact — a false refutation of the "
        "owner-approved doc is exactly what must not reach it"
    )

    # ---- MUTATION: round 4's predicate, nothing else changed. Same document, same row
    # parse — and the defect comes straight back, green, in the artifact.
    _restore_the_round_4_predicate(monkeypatch)
    _, _, errs_off = bt._approved_state()
    assert errs_off == [], f"the mutation did not restore round 4's behaviour: {errs_off}"
    assert bt.main() == 0, "with round 4's predicate this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert (
        "| `tortoise_mitigate_operator` | `adjust_relationship` | *(no approved row names it)*"
        in written
    ), "the mutation did not reproduce the false refutation — the test proves nothing"
    out.unlink()


def test_a_leading_comma_remainder_fails_the_build(tmp_path, monkeypatch, capsys) -> None:
    """Round 5, shape 1 — the remainder keeps the cell's separator at its HEAD.

    `| 21 | `adjust_relationship` | operator_action |` ⏎ blank ⏎
    `, annotate_operator, mitigate_operator`. This is the measured example from the review:
    round 4 split on `,` and demanded `all(tokens)`, so the EMPTY first token defeated it and
    the build went green with `row_errors=[]`, `rows=23`, partition 5/7/0 → 4/7/1 (#8930
    review, round 5, P2). It is not a sentence by any reading — which is why round 4's "the
    only miss is an ordinary sentence" claim was false.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    remainder = ", annotate_operator, mitigate_operator"
    # NOT VACUOUS: the predicate is what changes, and it must say yes to this spelling.
    assert bt._is_member_list(remainder, bt._known_names()), (
        f"the member-list test still answers `False` for a leading comma: {remainder!r}"
    )
    _the_break_fails_closed(
        tmp_path,
        monkeypatch,
        capsys,
        edited=_row_21_broken_at(real, "operator_action", remainder),
        name="leading-comma",
        remainder=remainder,
        lost={"annotate_operator", "mitigate_operator"},
    )


def test_a_trailing_period_remainder_fails_the_build(tmp_path, monkeypatch, capsys) -> None:
    """Round 5, shape 2 — the remainder ends in a sentence period.

    `… annotate_operator |` ⏎ blank ⏎ `mitigate_operator.` — round 4 stripped trailing
    commas/whitespace only, so the `.` made the token unknown and the build went green with
    row 21 truncated (#8930 review, round 5, P2).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    remainder = "mitigate_operator."
    assert bt._is_member_list(remainder, bt._known_names()), remainder
    _the_break_fails_closed(
        tmp_path,
        monkeypatch,
        capsys,
        edited=_row_21_broken_at(
            real, "operator_action, annotate_operator", remainder
        ),
        name="trailing-period",
        remainder=remainder,
        lost={"mitigate_operator"},
    )


def test_an_emphasis_remainder_fails_the_build(tmp_path, monkeypatch, capsys) -> None:
    """Round 5, shape 3 — the remainder arrives wrapped in markdown emphasis.

    `… annotate_operator |` ⏎ blank ⏎ `**mitigate_operator**`: round 4 stripped backticks
    only, so the asterisks survived into the token (#8930 review, round 5, P2).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    remainder = "**mitigate_operator**"
    assert bt._is_member_list(remainder, bt._known_names()), remainder
    _the_break_fails_closed(
        tmp_path,
        monkeypatch,
        capsys,
        edited=_row_21_broken_at(
            real, "operator_action, annotate_operator", remainder
        ),
        name="emphasis",
        remainder=remainder,
        lost={"mitigate_operator"},
    )


def test_an_inline_annotation_remainder_fails_the_build(tmp_path, monkeypatch, capsys) -> None:
    """Round 5, shape 4 — the remainder keeps the cell's inline `*(…)` annotation.

    `… annotate_operator |` ⏎ blank ⏎ `mitigate_operator *(operator-only)*`. Not a contrived
    spelling: row 12's real `Absorbs` cell qualifies its names in exactly this style, so a
    wrap through it produces exactly this remainder (#8930 review, round 5, P2).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    remainder = "mitigate_operator *(operator-only)*"
    assert "*(operator-only)*" in real, (
        "the doc no longer uses an inline `*(…)` annotation — this test is stale"
    )
    assert bt._is_member_list(remainder, bt._known_names()), remainder
    _the_break_fails_closed(
        tmp_path,
        monkeypatch,
        capsys,
        edited=_row_21_broken_at(
            real, "operator_action, annotate_operator", remainder
        ),
        name="inline-annotation",
        remainder=remainder,
        lost={"mitigate_operator"},
    )


def test_a_mid_token_break_fails_the_build(tmp_path, monkeypatch, capsys) -> None:
    """Round 5, shape 5 — the break lands INSIDE a name (the largest green slice, 46/52).

    `| 21 | `adjust_relationship` | operator_action, annotate_operator, mitig |` ⏎ blank ⏎
    `ate_operator`. No enumeration of spellings reaches this: the remainder is not a name,
    not a known name and not decorated at all — it is a FRAGMENT of one, so the predicate has
    to decide by SHAPE. The rule is that a line whose every comma-token is a bare identifier
    is a name list, and a name list carrying a name this doc does not have fails CLOSED —
    which is what distinguishes it from prose (a sentence's tokens carry spaces and sentence
    punctuation, and the real doc's post-table paragraphs say so:
    `test_the_normalised_member_list_test_still_leaves_real_prose_green`).
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    remainder = "ate_operator"
    assert remainder not in bt._known_names(), (
        "this shape only proves anything if the truncated token is NOT a known name"
    )
    assert bt._is_member_list(remainder, bt._known_names()), remainder
    _the_break_fails_closed(
        tmp_path,
        monkeypatch,
        capsys,
        edited=_row_21_broken_at(
            real, "operator_action, annotate_operator, mitig", remainder
        ),
        name="mid-token",
        remainder=remainder,
        lost={"mitigate_operator"},
    )


def test_the_normalised_member_list_test_still_leaves_real_prose_green(
    tmp_path, monkeypatch, capsys
) -> None:
    """ANTI-FALSE-POSITIVE: normalising tokens must not turn a paragraph into a name list.

    The predicate got strictly wider in round 5 (it now fails closed on a bare-identifier
    line carrying an unknown name), so the pin has to be on what it must still answer `No`
    to — and the discriminator has to be stated, not hoped for: a SENTENCE's comma-tokens
    carry spaces and sentence punctuation, so they are never bare identifiers, and a member
    list's always are.

    The real doc is the sharpest case in the whole file: its post-table prose NAMES
    `manage_deployment`, which is not in the known set at all. A rule that failed closed on
    "a line containing an unknown identifier" would refuse the document the owner approved;
    the shape-based rule does not, because that identifier sits inside a sentence.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    known = bt._known_names()
    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    # The real doc's own post-table prose, including an identifier the known set does NOT
    # have — the exact false positive a word-list rule would produce.
    assert "manage_deployment" not in known, (
        "this pin needs a real prose line carrying an UNKNOWN identifier — the doc's "
        "`manage_deployment` is it; if it becomes known, choose another"
    )
    for line in (
        "**Rows 22 and 23 are settled OFF the MCP (2026-09-22).** `manage_deployment` is account tenancy",
        "(`org_create`) plus two tenant-served pack operations; `run_onboarding` mixed a write with two",
        "listed as retired.",
        "## Removed entirely",
    ):
        assert line in real, f"the post-table prose changed — this test is stale: {line!r}"
        assert not bt._is_table_fragment(line.strip(), known), (
            f"the real doc's own prose is now a 'fragment': {line!r}"
        )

    # Planted near-misses, in the gap both post-close rules look at.
    prose = [
        # a sentence whose LAST token carries an inline `*(…)` annotation — the annotation
        # drop must not turn a sentence into a member list
        "The operator half is grantable on its own *(operator-only)*",
        # a comma list of PLAIN WORDS: no name, and no bare-identifier shape either
        "writes, reads, tenancy, and the switch",
        # a sentence naming two absorbed members and then continuing
        "`mitigate_operator` and `annotate_operator` are the pair this row is about",
    ]
    for paragraph in prose:
        assert " " in paragraph.split(",")[-1].strip(), (
            f"this pin is vacuous unless a token carries a space: {paragraph!r}"
        )
        assert not bt._is_member_list(paragraph, known), paragraph

    anchor = "\n**Rows 22 and 23 are settled OFF the MCP"
    assert anchor in real, "the paragraph after the WRITE table is not in the doc — stale test"
    edited = real.replace(anchor, "\n" + "\n\n".join(prose) + "\n" + anchor, 1)
    assert edited != real, "the prose block was not planted in the gap"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "normalised-prose-in-the-gap")

    menu, closures, row_errors = bt._approved_state()
    assert row_errors == [], f"the planted prose tripped the widened guard: {row_errors}"
    assert len(menu) == bt.APPROVED_MIN_ROWS == 23, len(menu)
    assert len(closures) == bt.APPROVED_MIN_CLOSURES, closures
    assert "mitigate_operator" in menu["adjust_relationship"]["members"], (
        "the planted prose cost the parse a row or a member"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 0, "ordinary post-table prose failed the build"
    capsys.readouterr()
    assert out.exists(), "the green build wrote no artifact"
    out.unlink()

    # ...and the REAL document still parses its own 23 rows with no row error, which is the
    # measurement the widening has to leave untouched.
    monkeypatch.setattr(bt, "APPROVED_DOC", ROOT / "docs" / "product" / "canonical-mcp-tools.md")
    real_menu, real_closures, real_errors = bt._parse_approved(real)
    assert real_errors == [], f"the real doc tripped the widened guard: {real_errors}"
    assert len(real_menu) == 23, len(real_menu)
    assert len(real_closures) == bt.APPROVED_MIN_CLOSURES, real_closures


def test_ordinary_post_table_prose_stays_green(tmp_path, monkeypatch, capsys) -> None:
    """ANTI-FALSE-POSITIVE COMPANION: the fragment test must not refuse real prose.

    This one passes BEFORE and AFTER the round-4 widening, which is the point: the guard has
    to reach a pipe-less fragment without ever reporting a paragraph. A block of ordinary
    post-table prose is planted IMMEDIATELY after the WRITE table's closing blank — where
    both the round-3 rule and the widened scan look — and the build must stay green with all
    23 rows, no row error, and 4 closures.

    The block deliberately carries the three shapes that a looser test would eat:
      * a sentence with commas in it;
      * a comma-separated run of words that are NOT absorbed names;
      * a sentence that NAMES two absorbed members and then keeps talking (the pin on the
        guard's known limit: a remainder reflowed into an ordinary sentence cannot be told
        from prose, and the module comment beside `_APPROVED_HEADER` records that as
        accepted rather than claiming it is caught);
    plus a heading, a bulleted list and a blockquote, none of which is table-shaped.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    anchor = "\n**Rows 22 and 23 are settled OFF the MCP"
    assert anchor in real, "the paragraph after the WRITE table is not in the doc — stale test"
    prose = [
        "Every write tool above is grantable, once the tenancy rows are settled, and it "
        "says nothing about reads.",
        "writes, reads, and the tenancy switch, so this list is not a member list",
        "`mitigate_operator`, `annotate_operator`, plus the operator tools it replaces",
    ]
    blocklike = [
        "## A heading in the gap",
        "- a bullet that names `adjust_relationship` and then says more",
        "> a quoted aside about the write half",
    ]
    planted = "\n\n".join(prose + blocklike)
    edited = real.replace(anchor, "\n" + planted + "\n" + anchor, 1)
    assert edited != real, "the prose block was not planted in the gap"
    _, out = _edited_doc(tmp_path, monkeypatch, edited, "prose-in-the-gap")

    menu, closures, row_errors = bt._approved_state()
    assert row_errors == [], f"the prose in the gap tripped a guard: {row_errors}"
    assert len(menu) == bt.APPROVED_MIN_ROWS == 23, len(menu)
    assert len(closures) == bt.APPROVED_MIN_CLOSURES, closures
    # Row 21 is untouched here, and its third member is still read — the prose block did not
    # cost the parse a row, which is what a false positive would look like from a distance.
    assert "mitigate_operator" in menu["adjust_relationship"]["members"]

    # NOT VACUOUS: every planted paragraph really is a COMMA LIST, so the green above is the
    # fragment test answering NO rather than the prose never reaching it. (The predicate's
    # own behaviour is pinned separately — this test deliberately depends on no round-4
    # symbol, because it has to pass before the widening as well as after it.)
    for paragraph in prose:
        assert "," in paragraph, paragraph

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])
    assert bt.main() == 0, "an ordinary post-table paragraph failed the build"
    capsys.readouterr()
    assert out.exists(), "the green build wrote no artifact"
    out.unlink()


def test_the_fragment_predicate_is_a_member_list_test_not_a_pipe_or_comma_test() -> None:
    """The fragment predicate in isolation: what it reports, and what it must not.

    The guard has to reach a pipe-less remainder WITHOUT becoming a comma test — a looser
    rule reports the doc's own post-table paragraphs and refuses the document the owner
    approved. Each case below is one of those two directions, so neither can drift.

    Round 5 replaced the SPELLING list with a NORMALISATION, so the fragment cases below are
    no longer a taxonomy to be extended: every one of them is the same list with decoration
    on it, and the predicate strips the decoration instead of being taught each spelling.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    known = bt._known_names()
    # FRAGMENTS — a bare member list, in every spelling a reflow produces.
    for fragment in (
        "mitigate_operator",
        "mitigate_operator,",  # a trailing comma kept from the cell
        "mitigate_operator, annotate_operator",
        "`mitigate_operator`, `annotate_operator`,",
        "operator_action, annotate_operator, mitigate_operator",  # row 21's own cell
        "| mitigate_operator |",  # pipe-bounded: the round-3 spelling, unchanged
        "mitigate_operator |",
        # Round 5: the SAME list, decorated. Each of these was measured building green.
        ", annotate_operator, mitigate_operator",  # the cell's separator kept at the head
        "mitigate_operator.",  # a sentence period on the last name
        "**mitigate_operator**",  # emphasis
        "_mitigate_operator_",  # emphasis, underscore spelling
        "mitigate_operator *(operator-only)*",  # the doc's own inline annotation style
        "ate_operator",  # a break INSIDE a name — no decoration at all to enumerate
        "action, annotate_operator, mitigate_operator",  # a break on a name's underscore
        "mitig",
    ):
        assert bt._is_table_fragment(fragment, known), fragment
        assert bt._is_member_list(fragment.strip("| "), known), fragment

    # NOT fragments — prose, and the near-misses that a comma test would eat.
    for line in (
        "**Rows 22 and 23 are settled OFF the MCP (2026-09-22).** `manage_deployment` is "
        "account tenancy",
        "writes, reads, and the tenancy switch, so this list is not a member list",
        "`mitigate_operator`, `annotate_operator`, plus the operator tools it replaces",
        "## WRITE — 14 tools",
        "- a bullet that names `adjust_relationship` and then says more",
        "> a quoted aside about the write half",
        "",
        ",",
    ):
        assert not bt._is_table_fragment(line, known), line

    # Two tiers, and the SECOND is the round-5 widening: a bare-identifier line is a name
    # list by SHAPE, so an unknown name in it fails CLOSED — that is exactly what a break
    # landing inside a name leaves behind (`ate_operator`, `perator`). Round 4 answered `No`
    # here, which is how 46 of the 52 break positions in row 21's cell built green.
    assert bt._is_member_list("zzz_not_a_tool", known)
    assert bt._is_member_list("mitigate_operator, zzz_not_a_tool", known)
    assert bt._is_member_list("ate_operator", known)
    # ...but the SHAPE is the test, not the word: the same unknown name inside a SENTENCE is
    # prose, which is what keeps the doc's own paragraphs green.
    assert not bt._is_member_list("`zzz_not_a_tool` is not on the target surface here", known)


def test_the_real_approved_doc_parses_every_row_with_no_row_error(monkeypatch) -> None:
    """ANTI-FALSE-POSITIVE: the row guards must not reject the REAL document.

    `docs/product/canonical-mcp-tools.md` holds TWO `| # | Tool | Absorbs |` tables
    (bounded by a blank line each) and its rows are long enough that a badge-counting
    guard could easily start refusing legitimate lines. The guards are therefore pinned to
    the real doc's own numbers: 23 rows, no row errors, `--check` rc 0. If a later
    tightening starts firing on the document the owner approved, this fails first.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    menu, closures, row_errors = bt._parse_approved(real)
    assert row_errors == [], f"the real doc tripped a row guard: {row_errors}"
    assert len(menu) == bt.APPROVED_MIN_ROWS == 23, (
        f"the real doc must parse its own 23 rows, parsed {len(menu)}"
    )
    assert bt._approved_errors(menu, closures, row_errors) == [], (
        "the real doc must raise no approved-menu error at all"
    )
    assert len(closures) == bt.APPROVED_MIN_CLOSURES, closures
    # Row 21 is complete in the real doc, so the guard must see its third member.
    assert "mitigate_operator" in menu["adjust_relationship"]["members"]
    # ...and the entry point the gate runs agrees.
    monkeypatch.setattr(sys, "argv", ["bridge_table.py", "--check"])
    assert bt.main() == 0, "--check went red on the unmodified approved doc"

    # EVERY round-3 guard must be UNSATISFIED by this document. Without these, "the doc is
    # green" says nothing about a guard that would refuse a legitimate line: each new rule
    # is pinned to the shape the doc actually has.
    #
    # (a) no row number and no row name is stated twice. Read from the DOC rather than from
    # `menu`, because the menu is the thing that would collapse a repeat silently.
    body_rows = [
        ln.strip() for ln in real.splitlines()
        if bt._APPROVED_ROW_START.match(ln.strip())
    ]
    numbers = [int(bt._APPROVED_ROW_START.match(r).group(1)) for r in body_rows]
    names = [bt._APPROVED_ROW_START.match(r).group(2) for r in body_rows]
    assert numbers == list(range(1, 24)), f"the doc states 23 numbered rows: {numbers}"
    assert len(set(names)) == len(names) == 23, f"no name is stated twice: {names}"
    # (b) the table bodies hold EXACTLY ONE separator each — the header's own, before any
    # row — and every line after it is a row prefix. That is the mid-body separator rule,
    # and the real doc satisfies it without an exemption.
    bodies: list[list[str]] = []
    gaps: list[list[str]] = []
    lines = real.splitlines()
    body: list[str] | None = None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s == "| # | Tool | Absorbs |":
            body = []
            bodies.append(body)
            continue
        if body is None:
            continue
        if not s:  # the blank line that ends this body; the gap after it is pinned below
            gaps.append(_post_close_gap(lines, i + 1, bt))
            body = None
            continue
        body.append(s)
    assert len(bodies) == 2, f"the doc has TWO approved tables, found {len(bodies)}"
    assert sum(len(b) - 1 for b in bodies) == bt.APPROVED_MIN_ROWS == 23, bodies
    for b in bodies:
        assert bt._APPROVED_SEPARATOR.match(b[0]), (
            f"the header's own separator must come first: {b[0]!r}"
        )
        for s in b[1:]:
            assert bt._APPROVED_ROW_START.match(s), f"a body line is not a row: {s!r}"
            assert bt._APPROVED_SEPARATOR.match(s) is None, (
                f"a separator appears AFTER rows started ({s!r}) — the real doc must never "
                "need an exemption from the mid-body separator rule"
            )
    # (c) nothing in the gap between a body's closing blank and the NEXT table is
    # fragment-shaped — in EITHER spelling (pipe-bounded, or a bare `Absorbs` member list).
    # Pinned with the module's own predicate and its own termination rule, over the WHOLE
    # gap rather than just its first line, so a later widening of the fragment test cannot
    # start refusing the document the owner approved. Measured on the real doc: 0 fragments
    # in 2 gaps — the READ gap holds 1 line (the `## WRITE` heading) and the WRITE gap holds
    # 9 (its paragraph, the `## Removed entirely` heading, which ends at the doc's own
    # `| Name | Why |` table).
    assert len(gaps) == 2, gaps
    assert [len(g) for g in gaps] == [1, 9], [len(g) for g in gaps]
    known = bt._known_names()
    for gap in gaps:
        for s in gap:
            assert not bt._is_table_fragment(s, known), (
                f"the real doc has a fragment-shaped line in the gap after a table body "
                f"({s!r}); the post-blank fragment guard would refuse the document the "
                "owner approved"
            )
            # ...and the bare-member-list half of the test is not satisfied either, even
            # with the pipe rule out of the picture.
            assert not bt._is_member_list(s, known), (s, gap)


def _post_close_gap(lines: list[str], start: int, bt) -> list[str]:
    """Every non-blank line after a body's closing blank, up to the next table.

    Mirrors `_parse_approved`'s termination rule (`_APPROVED_HEADER`, or `_starts_a_table`
    for a foreign table) so the pin cannot drift from the guard it pins.
    """
    gap: list[str] = []
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if not s:
            continue
        if bt._APPROVED_HEADER.match(s) or bt._starts_a_table(lines, i):
            break
        if bt._APPROVED_ROW_START.match(s):  # a row the row loop already read — not a gap
            continue
        gap.append(s)
    return gap



def _d4_membership_lines(doc: str) -> list[str]:
    """The `X/Y `sit inside` Z` lines of §D4's membership paragraph."""
    section = _section(doc, "### D4 —")
    marker = "The membership set is the doc's OWN closure"
    assert marker in section, "§D4 no longer states where its membership comes from"
    block = section.split(marker, 1)[1].split("\n\n", 1)[0]
    return [ln for ln in block.splitlines() if "sit inside" in ln]


def test_d4_membership_sentence_is_derived_from_the_parsed_closure() -> None:
    """§D4's closure enumeration must be EMITTED from the parse, not typed beside it.

    It was a literal list in `render()`, while the membership it described was parsed — so
    deleting `list_topics` from the doc's `overview` statement left the build green and the
    artifact still asserting that `list_topics` `sits inside` `overview`: the "second answer
    that can drift" the module comment forbids (#8930 review, P2b). Derived means the
    sentence MOVES when the doc does.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    rows = bt._registry_rows()
    sdk_defs = bt._sdk_targets()
    cites = bt._doc_citations()
    menu, closures, _ = bt._approved_state()
    lines = _d4_membership_lines(bt.render(rows, sdk_defs, cites, menu, closures))

    assert lines, "§D4 rendered no closure line — this test would be vacuous"
    assert len(lines) == len(closures), (
        f"§D4 rendered {len(lines)} closure line(s) for {len(closures)} parsed statement(s)"
    )
    # Every member and container of the closure ACTUALLY USED is stated, on its container's
    # own line: the sentence cannot be a subset of the membership.
    for outer, inners in closures:
        for m in inners:
            assert any(f"`{m}`" in ln and f"`{outer}`" in ln for ln in lines), (
                f"§D4's membership paragraph omits `{m}`, which the parse puts inside "
                f"`{outer}`"
            )
    assert any("`list_topics`" in ln and "`overview`" in ln for ln in lines), (
        f"the doc says `list_topics` sits inside `overview`; §D4 does not: {lines}"
    )

    # Now move the DOC: `list_topics` leaves the `overview` statement. The rendered
    # sentence must move with it — a hand-typed list stays put.
    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    stripped = real.replace("`list_graphs`, `list_topics` sit inside `overview`", "`list_graphs` sit inside `overview`", 1)
    assert stripped != real, "the overview closure statement is not in the doc — test is stale"
    menu2, closures2, errs2 = bt._parse_approved(stripped)
    assert not errs2, f"the edited doc fails its own row guards: {errs2}"
    assert len(closures2) == len(closures), "the edit removed a whole statement, not a member"
    lines2 = _d4_membership_lines(bt.render(rows, sdk_defs, cites, menu2, closures2))
    assert not any("`list_topics`" in ln for ln in lines2), (
        "§D4 still asserts `list_topics` sits inside `overview` after the doc stopped "
        f"saying so — the enumeration is typed beside the parse, not derived from it: {lines2}"
    )
    assert any("`list_graphs`" in ln and "`overview`" in ln for ln in lines2), (
        "the edited closure statement vanished entirely, so the check above is vacuous"
    )


def test_a_reworded_header_cannot_disarm_the_row_reflow_guard(
    tmp_path, monkeypatch, capsys
) -> None:
    """The body-integrity pass is ARMED by the header — so the header must be found.

    Every row-completeness rule this build owns (the orphaned continuation, the mid-body
    separator, the fragment across a blank) is decided by the TABLE BODY: `in_table` is set
    only where `_APPROVED_HEADER` matches. The row loop is header-agnostic, so a benign
    header edit — a fourth column, the third column reworded — left every row parsing (23,
    gapless) while the pass that could see a wrap never ran. Measured green: the wrapped row
    parsed TRUNCATED, `_approved_errors` returned `[]`, `main()` returned 0 and the artifact
    was written with `mitigate_operator` falsely refuted — the exact false claim about the
    owner-approved doc §D4 exists to prevent (#8930 review, P1).

    The mutation is in the test, so the guard cannot rot into a tautology: with the count
    neutered the SAME document builds green and writes the false claim; armed, the build is
    red and nothing is written. Both halves are asserted, in that order.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    cell = "operator_action, annotate_operator, mitigate_operator |"
    assert cell in real, "row 21's Absorbs cell is not in the doc — this test is stale"

    # Two plausible owner edits, and BOTH are needed to reproduce it: re-column the header
    # (so the body pass never arms) and wrap row 21 leaving `|` at the end of line 1 (so
    # the row still reads as complete and only the body test could see the remainder).
    mutated = real.replace("| # | Tool | Absorbs |", "| # | Tool | Absorbs | Notes |")
    assert mutated != real, "the approved header is not in the doc — this test is stale"
    assert mutated.count("| # | Tool | Absorbs | Notes |") == bt.APPROVED_MIN_BODIES, (
        "both approved tables must be re-columned here, so the count guard sees zero"
    )
    mutated = mutated.replace(
        cell, "operator_action, annotate_operator, |\n| mitigate_operator |", 1
    )
    path = tmp_path / "header-reworded.md"
    path.write_text(mutated, encoding="utf-8")
    out = tmp_path / "bridge-table.md"
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    monkeypatch.setattr(bt, "OUT", out)

    # The row parse is untouched — which is exactly why the header count is the only thing
    # that can see this. The wrap has TRUNCATED row 21, and no ROW error reports it.
    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS, f"{len(menu)} rows parsed, expected all of them"
    assert "mitigate_operator" not in menu["adjust_relationship"]["members"], (
        "this shape is supposed to truncate row 21 — if it no longer does, the test no "
        "longer reproduces the defect"
    )
    assert row_errors == [], (
        f"the body pass is disarmed here, so no row may report the wrap: {row_errors}"
    )

    errs = bt._approved_errors(menu, closures, row_errors)
    assert errs and "header(s) matched" in errs[0], (
        f"a header the body pass cannot arm on did not fail the build: {errs}"
    )
    assert "UNARMED" in errs[0], f"the diagnostic does not say the guard is unarmed: {errs[0]!r}"

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])  # WRITE mode, not --check
    assert bt.main() == 1, "a re-columned header did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "header(s) matched" in err, err
    assert not out.exists(), (
        f"the build failed but still wrote {out.name} — the false refutation of the "
        "owner-approved doc is what must not reach the artifact"
    )

    # ---- MUTATION: neuter the count. Same document, same (intact) row parse — the defect
    # comes straight back, green, in the artifact.
    monkeypatch.setattr(bt, "_approved_header_count", lambda _text: bt.APPROVED_MIN_BODIES)
    assert bt._approved_errors(*bt._approved_state()) == [], (
        "the mutation did not disable the header bound"
    )
    assert bt.main() == 0, "with the header bound neutered this document MUST build green"
    written = out.read_text(encoding="utf-8")
    assert "no approved row absorbs `mitigate_operator`" in written, (
        "the mutation did not reproduce the defect — the test proves nothing"
    )
    out.unlink()


def test_a_pipe_inside_the_absorbs_cell_cannot_truncate_it(tmp_path, monkeypatch, capsys):
    """The cell-count guard is two-sided: an extra cell CUTS the `Absorbs` cell.

    Only `len(cells) < 3` was an error, so a pipe written inside the `Absorbs` cell split
    it into a fourth cell and `cells[2]` became a SHORTER member list — while the physical
    line still ends in `|`, so the row reads as complete and every other guard stays green
    (23 rows, gapless, closures and split untouched). Row 17's split reworded with a raw
    pipe is the realistic spelling: `supersede_knowledge` leaves the successors, flipping
    `tortoise_invalidate` from `successor` (support) to `rehomed` (a disagreement) in the
    owner-facing table, with no diagnostic (#8930 review, P2b).

    The mutation half disables the shape test and shows the same document building green
    with the truncated membership in the written artifact.
    """
    sys.path.insert(0, str(ROOT))
    import tools.bridge_table as bt

    real = bt.APPROVED_DOC.read_text(encoding="utf-8")
    clause = "splits this into `update_knowledge` + `supersede_knowledge`"
    assert clause in real, "row 17's split clause is not in the doc — this test is stale"
    mutated = real.replace(
        clause, "splits this into `update_knowledge` | `supersede_knowledge`", 1
    )
    assert mutated != real, "the mutation did not change the doc"
    path = tmp_path / "pipe-in-cell.md"
    path.write_text(mutated, encoding="utf-8")
    out = tmp_path / "bridge-table.md"
    monkeypatch.setattr(bt, "APPROVED_DOC", path)
    monkeypatch.setattr(bt, "OUT", out)

    # What the OLD parse would have read: the truncated cell, against the whole row.
    row17 = next(line for line in mutated.splitlines() if line.startswith("| 17 |"))
    cells = [c.strip() for c in row17.strip().strip("|").split("|")]
    assert len(cells) == 4, f"the pipe did not split the cell: {len(cells)} cell(s)"
    assert "supersede_knowledge" in row17, "the row still states the successor..."
    assert "supersede_knowledge" not in cells[2], (
        "...and `cells[2]` is where it is CUT OFF — that truncation is the defect"
    )

    menu, closures, row_errors = bt._approved_state()
    assert len(menu) == bt.APPROVED_MIN_ROWS - 1, (
        f"the row must be DROPPED, not silently truncated — {len(menu)} rows parsed"
    )
    assert "revise_knowledge" not in menu
    assert len(row_errors) == 1, f"the extra cell was not reported exactly once: {row_errors}"
    assert "parses to 4 cell(s)" in row_errors[0], (
        f"the diagnostic does not report the cell count: {row_errors[0]!r}"
    )
    assert "row 17 (`revise_knowledge`)" in row_errors[0], (
        f"the diagnostic does not name the row: {row_errors[0]!r}"
    )
    assert bt._approved_errors(menu, closures, row_errors)[0] == row_errors[0], (
        "the specific cause must print BEFORE the generic row-count diagnostic"
    )

    monkeypatch.setattr(sys, "argv", ["bridge_table.py"])  # WRITE mode
    assert bt.main() == 1, "a pipe inside the Absorbs cell did not fail the build"
    err = capsys.readouterr().err
    assert "BRIDGE TABLE BUILD FAILURE" in err, err
    assert "parses to 4 cell(s)" in err, err
    assert not out.exists(), (
        f"the build failed but still wrote {out.name} — a truncated member list is exactly "
        "what must not reach the artifact"
    )

    # ---- MUTATION: disable the shape test. Same document, and the truncation lands in the
    # artifact as a `rehomed` verdict the approved doc does not support.
    monkeypatch.setattr(bt, "_cell_count_error", lambda *a, **k: None)
    menu_off, _, row_errors_off = bt._approved_state()
    assert row_errors_off == [], f"the mutation did not disable the shape test: {row_errors_off}"
    assert len(menu_off) == bt.APPROVED_MIN_ROWS
    assert menu_off["revise_knowledge"]["successors"] == ["update_knowledge"], (
        "the mutation did not reproduce the truncation — the test proves nothing: "
        f"{menu_off['revise_knowledge']['successors']!r}"
    )
    assert bt.main() == 0, "with the shape test disabled this document MUST build green"
    written = out.read_text(encoding="utf-8")
    invalidate_row = next(
        (ln for ln in written.splitlines() if ln.startswith("| `tortoise_invalidate` |")), ""
    )
    assert invalidate_row, "§D4 no longer renders `tortoise_invalidate` — test is stale"
    verdict_cell = [c.strip() for c in invalidate_row.strip().strip("|").split("|")][4]
    assert verdict_cell == "**rehomed**", (
        "the mutation did not flip the destination verdict the lost successor supported: "
        f"{invalidate_row}"
    )
    out.unlink()
