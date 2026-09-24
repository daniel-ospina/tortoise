"""The bridge table must not drift from the code it describes (#4282).

`tools/bridge_table.py` reads every `file:line` it emits straight from the AST, so
a citation in the generated doc cannot be wrong. What it CAN still do is go
*stale* — someone edits `tool_registry.py` or `sdk.py` and never regenerates —
and that is exactly the drift the generator's own docstring promises cannot
happen. Nothing ran `--check`, so the promise was unenforced.

This is the enforcement. It is deliberately a test rather than a workflow step:
the generator imports cleanly with no database, no API key and no network, so it
runs with the ordinary suite. It is registered in BOTH `api` and `core` in
`config/ci-surfaces.yml`, and `tools/bridge_table.py` is named in the `api`
SOURCE_PATTERNS — because `tools/` and `docs/` are in NON_PYTHON_PREFIXES, an
edit to the generator alone used to select NO surface, so the gate did not run on
the PR that can break it. Residual: a change touching ONLY the docs skips the
matrix by the repo's deliberate docs-PR policy (filed as tortoise #4454).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "bridge_table.py"


def test_target_mcp_matches_the_beta_doc() -> None:
    """The 26-tool target set is declared in TWO places; they must be equal.

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
    d2c = _section(doc, "#### D2c —")

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
    assert len(retired) == 16, f"expected 16 retired rows, got {len(retired)}"
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
    d1 = doc.split("#### D1")[1].split("#### D2")[0]
    # The documented-hop block uses the same bullet+quote shape, so cut it off first.
    d1 = d1.split("**Documented hops.**")[0]
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
    """The body of `heading`, up to the next heading of the same-or-higher level."""
    assert heading in doc, f"the generated doc has no {heading!r} section"
    return re.split(r"\n#{2,4} ", doc.split(heading)[1])[0]


def _cited_findings() -> tuple[set[str], set[str], set[str]]:
    """(unsupported, clause-only, ambiguous) from the RENDERED doc — the oracle subject."""
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    d2 = _section(doc, "#### D2 —")
    d2b = _section(doc, "#### D2b —")
    d3 = _section(doc, "#### D3 —")
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
    assert ambiguous == {
        "tortoise_annotate_operator", "tortoise_assess_source", "tortoise_belief_timeline",
        "tortoise_get_source_reliability", "tortoise_mitigate_operator",
        "tortoise_operator_action", "tortoise_provenance", "tortoise_session_context",
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
    d2 = _section(doc, "#### D2 —")
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
    d2b = _section(doc, "#### D2b —")
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
    d3 = _section(doc, "#### D3 —")
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
