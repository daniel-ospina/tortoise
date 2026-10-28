"""#5087 / PR #10072 — §8 of the design record must BE a table, and cite by NAME.

THE DEFECT THIS PINS (two, both found at the 17th review round of PR #10072)
----------------------------------------------------------------------------
1. **A blank line ends a GFM table.** `docs/architecture/S2-DECISION-SEAM.md` carried
   one *inside* §8's test table, between the sixth row and the remaining eight. Under GFM
   the blank line terminates the table, and the rows below it have no header and no
   delimiter row, so they are not a table at all — on GitHub eight of the thirteen named
   tests rendered as paragraph text. It is invisible to CI by construction: the `docs`
   leg runs `markdownlint-cli2`, and a block without a delimiter row is not a table, so
   no MD055/MD056 fires. The leg stays green while the artifact reads wrong — and §8 is
   the artifact the PR body points a reviewer at to check that all thirteen tests exist.

2. **§8 rows cited siblings by ORDINAL** (*"dropping it is row 11"*, *"row 22 is the
   raise-branch latch"*). Those pointers went stale in the same direction as the counts
   did: three rows inserted above moved every one of them by three, so a reader following
   "row 11" landed on `the classify fold drops model_served` and one following "row 22"
   landed on `_http_post reverts to bare urlopen`. The table's own preamble already
   forbade the class for LINE NUMBERS (*"Rows cite methods and assertions by NAME, never by
   line number"*); a row ordinal is a line number in disguise and moves the same way.

WHAT THIS PINS
--------------
1. No blank line splits a table anywhere in the design record, unless the block below it
   opens its OWN table (a header row followed by a delimiter row — the legitimate case of
   two tables back to back).
2. No row inside §8 — the section that holds the test table and the mutant census —
   cites a sibling by ordinal. Prose may say "row" in a sentence; a **row** may not point
   at another row by number.

Non-vacuity: on the pre-fix document check 1 fails on the blank line inside §8's test
table, and check 2 fails on the two `row 11` citations in the `_noul` mutant row. Both are
re-verified below by running the same checkers over the pre-fix text, so a future edit that
reintroduces either shape reddens here instead of merging green.

Static: one file read. No network, no DB, no graph, no model.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "architecture" / "S2-DECISION-SEAM.md"

#: A GFM table delimiter row: `|---|---|`, `| :-- | --: |`, … (≥3 dashes per column).
_DELIMITER_ROW = re.compile(r"^\|(\s*:?-{3,}:?\s*\|)+$")

#: "row 11", "row 22", "Rows 3" — a citation by ORDINAL, which is a line number in
#: disguise: it moves whenever a row is inserted above it.
_ORDINAL_CITATION = re.compile(r"\brows?\s+\d+\b", re.IGNORECASE)

#: §8 is "The tests, and the mutants that redden them".
_SECTION_8_HEADING = "## 8."


def _lines() -> list[str]:
    return DOC.read_text(encoding="utf-8").splitlines()


def _is_row(line: str) -> bool:
    """A table row (or a table's header/delimiter row) — GFM needs the leading pipe."""
    return line.lstrip().startswith("|")


def _is_delimiter(line: str) -> bool:
    return bool(_DELIMITER_ROW.match(line.strip()))


def _split_tables(lines: list[str]) -> list[int]:
    """Return the 1-based line numbers of blank lines that CUT a table in half.

    A blank line whose neighbours are both table rows is only legitimate when the block
    below it opens its own table — i.e. the next row is a header and the one after it is a
    delimiter row. Anything else leaves rows with no header: not a table, no lint error.
    """
    splits: list[int] = []
    for index in range(1, len(lines) - 1):
        if lines[index].strip():
            continue
        if not (_is_row(lines[index - 1]) and _is_row(lines[index + 1])):
            continue
        if index + 2 < len(lines) and _is_delimiter(lines[index + 2]):
            continue  # two tables back to back: the blank line separates, it does not cut
        splits.append(index + 1)
    return splits


def _section_8_rows(lines: list[str]) -> list[tuple[int, str]]:
    """Every table ROW (1-based line number, text) inside §8, up to the next `## ` heading."""
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(_SECTION_8_HEADING)),
        None,
    )
    assert start is not None, f"{DOC.name} no longer carries a '{_SECTION_8_HEADING}' section"
    rows: list[tuple[int, str]] = []
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            break
        if _is_row(lines[index]):
            rows.append((index + 1, lines[index]))
    assert rows, f"§8 of {DOC.name} carries no table rows — the census moved or was lost"
    return rows


def _ordinal_citations(lines: list[str]) -> list[tuple[int, str]]:
    return [
        (lineno, line)
        for lineno, line in _section_8_rows(lines)
        if _ORDINAL_CITATION.search(line)
    ]


def test_no_table_in_the_design_record_is_split_by_a_blank_line():
    splits = _split_tables(_lines())
    assert not splits, (
        "a blank line ENDS a GFM table, so every row below it has no header and renders as "
        "plain paragraph text — and markdownlint-cli2 does not fire (a block with no "
        f"delimiter row is not a table), so the docs leg stays green. {DOC.relative_to(ROOT)} "
        f"has a table-cutting blank line at {splits}."
    )


def test_no_row_in_section_8_cites_a_sibling_by_ordinal():
    offenders = _ordinal_citations(_lines())
    assert not offenders, (
        "§8's rows must cite a sibling mutant or test by NAME, never by ordinal: an ordinal "
        "moves the moment a row is inserted above it, and every one of them had already moved "
        "by three. Offending row(s): "
        + "; ".join(
            f"line {lineno}: {line[:160]}" for lineno, line in offenders
        )
    )


def test_the_two_checkers_redden_on_the_pre_fix_record():
    """Non-vacuity: the checks fail on the shapes the #10072 review found, and pass on this head."""
    pre_fix_test_table_split = (
        "| test | what it proves |",
        "|---|---|",
        "| `test_the_module_guards_the_suite_never_proved_redden_when_removed` | `Bands` … |",
        "",
        "| `test_a_NOOP_verdict_does_not_rescind_a_mechanical_discard` | an emitted `NOOP` … |",
        "| `test_a_standalone_KEEP_verdict_keeps_its_own_attribution` | a standalone `KEEP` … |",
    )
    assert _split_tables(list(pre_fix_test_table_split)) == [4], (
        "the blank line between two rows, with no new header+delimiter below it, must be "
        "reported as a table split"
    )

    # Two tables back to back are legitimate and must NOT be flagged.
    legitimate = (
        "| test | what it proves |",
        "|---|---|",
        "| `test_a` | … |",
        "",
        "| mutant | red |",
        "|---|---|",
        "| `_noul` drops its `[0, 1]` bound | ✅ … |",
    )
    assert _split_tables(list(legitimate)) == [], (
        "a blank line separating two complete tables (each with its own header and delimiter "
        "row) is not a defect"
    )

    assert _ordinal_citations(
        [
            "## 8. The tests, and the mutants that redden them",
            "| mutant | red |",
            "|---|---|",
            "| `_noul` replaces its `_finite(...)` call (the band is not part of this "
            "mutant — dropping it is row 11) | ✅ … |",
            "",
            "## 9. What this does NOT close",
        ]
    ), "a row that points at a sibling by ordinal must be reported"
