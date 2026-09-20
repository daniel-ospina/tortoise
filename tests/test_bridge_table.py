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
