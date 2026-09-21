"""The bridge table must not drift from the code it describes (#4282).

`tools/bridge_table.py` reads every `file:line` it emits straight from the AST, so
a citation in the generated doc cannot be wrong. What it CAN still do is go
*stale* — someone edits `tool_registry.py` or `sdk.py` and never regenerates —
and that is exactly the drift the generator's own docstring promises cannot
happen. Nothing ran `--check`, so the promise was unenforced.

This is the enforcement. It is deliberately a test rather than a workflow step:
the generator imports cleanly with no database, no API key and no network, so it
runs in the ordinary suite on every PR without a new CI surface.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "bridge_table.py"


def test_target_mcp_matches_the_beta_doc() -> None:
    """The 25-tool target set is declared in TWO places; they must be equal.

    This is the round-2 P1 of the PR #4177 review, and it is the *same* defect
    class round 1 found: two sets that both said "25" while being different
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
        "the beta doc has no `MCP twin` column header - this test cannot bind the two 25-sets"
    )

    assert twins, (
        "parsed no MCP twin names from the beta doc's `MCP twin` column - did its "
        "header text or table shape change? This test is the only thing binding the two 25-sets."
    )
    declared = set(TARGET_MCP)
    if twins != declared:
        raise AssertionError(
            "the generator's TARGET_MCP and the beta doc's MCP column disagree.\n"
            f"  only in generator ({len(declared - twins)}): {sorted(declared - twins)}\n"
            f"  only in beta doc ({len(twins - declared)}): {sorted(twins - declared)}"
        )
    assert len(declared) == 25, f"TARGET_MCP has {len(declared)} entries, expected 25"


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
    from tools.bridge_table import DESTINATION, NAMESPACES, TARGET_MCP

    defs = _sdk_defs_independently()

    # Every target the map names, from all three scopes.
    targets = set(TARGET_MCP)
    for dest in DESTINATION.values():
        ns = next((n for n in NAMESPACES if dest.startswith(n)), None)
        targets.add(dest[len(ns):] if ns else dest)
    targets.discard("REMOVED")

    expected = {t for t in targets if t not in defs}
    assert expected, "no target lacks a method - the artifact may be stale"

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    c1 = doc.split("### C1")[1].split("### C2")[0]
    # `Where` is one of MCP / tenancy / sdk-only, so it may contain a hyphen.
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| [A-Za-z-]+ \|", c1, re.M))

    # `Where` must name the target's real scope, not merely be well-formed.
    sys.path.insert(0, str(ROOT))
    from tools.bridge_table import DESTINATION

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
    from tools.bridge_table import DESTINATION, TARGET_MCP

    sources: dict[str, int] = {}
    for dest in DESTINATION.values():
        sources[dest] = sources.get(dest, 0) + 1

    merged = {d for d, n in sources.items() if n > 1 and d != "REMOVED"}
    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")

    assert len(merged) == 17, f"merged set changed size: {len(merged)}"
    # Every merged target must appear in Part A, and nothing else may.
    part_a = doc.split("## Part A")[1].split("## Part B")[0]
    listed = set(re.findall(r"^\| `([a-z_][a-z0-9_]*)` \| \d+ \|", part_a, re.M))
    assert listed, "Part A rendered no rows - did its table shape change?"
    assert listed == merged, (
        "Part A does not list exactly the merged targets.\n"
        f"  listed but not merged: {sorted(listed - merged)}\n"
        f"  merged but not listed: {sorted(merged - listed)}"
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

    doc = (ROOT / "docs" / "product" / "bridge-table.md").read_text(encoding="utf-8")
    vision = (ROOT / "docs" / "product" / "vision-mcp-sdk-surface.md").read_text(encoding="utf-8")

    m = re.search(
        r"Registry: (\d+) tools → (\d+) absorbed into the \d+ MCP targets · "
        r"(\d+) absorbed into a builder-only SDK method \(not on the MCP\) · "
        r"(\d+) tenancy \(SDK/REST only\) · (\d+) retired\.",
        doc,
    )
    assert m, "could not parse the generated headline; did its wording change?"
    total, mcp, sdk_only, tenancy, retired = (int(g) for g in m.groups())
    assert mcp + sdk_only + tenancy + retired == total, "generated buckets do not sum"

    for label, value in [
        ("Registry tools", total),
        ("Absorbed into the 25 MCP targets", mcp),
        ("Absorbed into a builder-only SDK method (not on the MCP)", sdk_only),
        ("Tenancy (SDK/REST only)", tenancy),
        ("Retired", retired),
    ]:
        row = re.search(rf"^\| {re.escape(label)} \| \*\*(\d+)\*\* \|$", vision, re.M)
        assert row, f"vision doc has no reconciliation row for {label!r}"
        assert int(row.group(1)) == value, (
            f"vision doc says {label} = {row.group(1)}, generated doc says {value}. "
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
