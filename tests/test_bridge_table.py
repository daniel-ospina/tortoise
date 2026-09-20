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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GENERATOR = ROOT / "tools" / "bridge_table.py"


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
