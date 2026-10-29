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

3. **§5 cited the two classify passes by BARE line number** (*"`(:8608`, the S2 list, and
   `:8740`, the post-S4 union)"*). Both landed on unrelated statements — `:8608` is
   `"recovery": recovery_stats},` inside `extract_session_v2`'s **empty-conversation early
   return** (the branch that returns before any classify pass can exist), and `:8740` is
   `stats=stage_stats,` in the `run_s4(...)` call — while the two passes were at `:8700`
   and `:8832`. A bare `:NNNN` is the one shape with **nothing authoritative to fall back
   on**: it names no symbol, so a reader following it cannot tell a stale cite from a
   correct one, and this one sent them to the no-conversation path. §8's preamble had
   already claimed the record's guard *"reddens on either form"* while nothing checked
   this one.

WHAT THIS PINS
--------------
1. No blank line splits a table anywhere in the design record, unless the block below it
   opens its OWN table (a header row followed by a delimiter row — the legitimate case of
   two tables back to back).
2. No row inside §8 — the section that holds the test table and the mutant census —
   cites a sibling by ordinal. Prose may say "row" in a sentence; a **row** may not point
   at another row by number.
3. No line citation anywhere in the record is **bare** — a `:NNNN` with no symbol, file or
   quoted identifier in front of it. The rule is the one `tests/test_extractor_v4_citation_rot.py`
   states for the sibling document ("the symbol name is authoritative, the line number is a
   convenience"; a citation whose symbol has merely MOVED passes and that is deliberate), so a
   file-anchored `FILE:line` is out of scope here by that recorded tolerance — it is the bare
   form, with no symbol at all, that cannot be checked by its reader.

Non-vacuity: on the pre-fix document check 1 fails on the blank line inside §8's test
table, check 2 fails on the two `row 11` citations in the `_noul` mutant row, and check 3
fails on §5's `:8608`/`:8740` pair. All three are re-verified below by running the same
checkers over the pre-fix text, so a future edit that reintroduces any of them reddens here
instead of merging green.

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

#: A BARE line citation: `:8608` with nothing that names the cited thing in front of it.
#: `EXTRACTOR-V4-ARCHITECTURE.md:823` and `foo.py:12` are anchored by a file name and are
#: NOT this shape — their symbol is recoverable, which is the tolerance
#: `tests/test_extractor_v4_citation_rot.py` documents. A clock time (`12:30`) is excluded
#: by the same lookbehind, because the character before its colon is a digit.
_BARE_LINE_CITATION = re.compile(r"(?<![A-Za-z0-9_.]):\d{2,4}\b")


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


def _bare_line_citations(lines: list[str]) -> list[tuple[int, str]]:
    """1-based line numbers of every line carrying a `:NNNN` with no symbol in front of it."""
    return [
        (index + 1, line)
        for index, line in enumerate(lines)
        if _BARE_LINE_CITATION.search(line)
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


def test_no_line_citation_in_the_design_record_is_bare():
    offenders = _bare_line_citations(_lines())
    assert not offenders, (
        "a bare `:NNNN` names no symbol, so a reader has nothing authoritative to fall back on "
        "and cannot tell a stale cite from a correct one — one such pair landed inside "
        "`extract_session_v2`'s empty-conversation early return. Cite the symbol instead. "
        f"Offending line(s) in {DOC.relative_to(ROOT)}: "
        + "; ".join(f"line {lineno}: {line.strip()[:160]}" for lineno, line in offenders)
    )


def test_the_three_checkers_redden_on_the_pre_fix_record():
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

    # §5 at the pre-fix head, verbatim — both cites bare, both on unrelated lines.
    pre_fix_section_5 = (
        "runs **two** classify passes on the same `KindClassifier` instance (`:8608`, the",
        "S2 list, and `:8740`, the post-S4 union), so the S2.3 side also pays 2 ×",
    )
    assert [lineno for lineno, _ in _bare_line_citations(list(pre_fix_section_5))] == [1, 2], (
        "a `:NNNN` with no symbol in front of it must be reported — this is the §5 pair the "
        "19th review round found pointing into the empty-conversation early return"
    )

    # An ANCHORED cite is the tolerated form and must NOT be flagged: the file name is the
    # symbol to fall back on, which is what `test_extractor_v4_citation_rot.py` documents.
    assert _bare_line_citations(
        [
            "(`EXTRACTOR-V4-ARCHITECTURE.md:823`) implies once the rate this section",
            "`tortoise/extractor_v2.py:8700` is the S2 classify pass, and 12:30 is a time",
        ]
    ) == [], (
        "a file-anchored `FILE:line` carries a symbol to resolve against and is deliberately "
        "tolerated; a clock time is not a citation at all"
    )
