#!/usr/bin/env python3
"""Check epic #5090's integration-surface map against its own rules (#11084).

**What this is.** `docs/epics/2026-11-04-5090-vector-layer/TEST-DESIGN-surface-map.md`
is the Test-Design artifact that epic #5090 never had. Its consumer is the
capstone (**#11089**), whose checklist *is* "walk every surface through its
assigned layer". A checklist read by a human can shrink silently: delete a row,
blank a layer cell, or drop a child, and the capstone still looks complete. This
tool is what makes that impossible — it parses the map and refuses when the map
stops being a map.

**It checks the document, not the product.** No imports of `tortoise`, no
database, no network: the whole point is that #11089 can run it at the closing
head cost-free, and that any lane editing the map gets an answer in ~0.1 s.

Rules (the map's own §8 states them; this file is the enforcement):

* **R1** every capability `S1`–`S5` names at least one surface; every surface
  named in the capability or child tables has a row in the surface table; and
  the surface POPULATION is pinned `SF1`–`SF15` **both ways** — a row deleted
  together with every mention of it passes every referential check (measured),
  so only a pin catches the consistent shrink.
* **R2** every surface row's layer is non-empty and drawn only from
  `{unit, contract, live-docker, doc-assert, guard}`.
* **R3** every surface row states a failure in at least `MIN_FAILURE_WORDS`
  words — a restated mechanism is not a named failure.
* **R4** every surface row is touched by at least one child (the issue's
  "no surface is listed that no child touches"), and every child `#11084`–`#11089`
  appears in the child table.
* **R5** every seam named by `04-plan.md` §5–§7 appears in the crosswalk, every
  surface a crosswalk row names has a row in the surface table, and no crosswalk
  row leaves its surface cell naming nothing. R1 and R4 carry the equivalent
  **intent** over the whole row; R5 must read the surface CELL, because a
  crosswalk row's note cell may legitimately name another surface (§6.4's does),
  and the whole-row form was measured not to fire.

A surface row is refused (exit 2) unless it has exactly 7 cells. A cell delimiter
is a pipe carrying an EVEN run of preceding backslashes (CommonMark parity), so a
single-escaped `\\|` stays inside its cell while `\\\\|` is an escaped backslash
followed by a REAL delimiter and a raw `|` splits; a width the parser cannot
interpret is an unreadable map, not a map that happens to be tidy, because
`row[4]`/`row[6]` would then grade the wrong column and report clean.

A row is a markdown table row at **0-3 leading spaces** (`MAX_TABLE_INDENT`):
markdown renders a `|` line as part of the table up to three spaces in, so those
are rows a reader sees and every arm grades them — blocks, seam discovery, the
presence arm, the §1/§3/§4 row anchors. `--self-test` runs its whole population
against BOTH the shipped map and an indented copy, so a harness or an arm that can
only reach column-0 rows is caught rather than silent. #11415 measured the pre-fix
shape: `line.startswith("|")` put an indented row in no block at all, so the
identical blanked crosswalk row graded `VIOLATION R5 zzz: names no surface` at
column 0 and **CLEAN, exit 0**, with one leading space.

⚠️ The 4+ boundary is **relative to the containing block**, and this predicate
models document-column indentation only: a `|` line at document column 4 is an
indented code block when the table is TOP-LEVEL content, but inside a list item
(a `- ` marker puts content at column 2) the same line can still render as part of
a nested table and would be ungraded. The shipped map's §1/§3/§4/§5 tables are
top-level, so no row can be hidden that way today; the residual is stated rather
than claimed away, and such a line is neither graded nor refused (refusing it would
red a docs-only code-block edit).

A `|` line inside a FENCED code block is still **graded**, deliberately: moving a
row into a fence takes it out of the rendered table, which is the shrink this
checker exists to catch, so the fail-closed direction is to grade it. The cost is
that a fenced table EXAMPLE that is itself invalid reds — the shipped map has none,
and the alternative (skipping fences) is a one-line way to hide a row. This
predicate has no fence model at all, at column 0 either; #11415 widened the
indentation band, not the fence boundary.

**Exit codes:** `0` clean · `1` violations (printed, one per line) · `2` the map
could not be read or a required section is missing (an unparseable map is not a
clean one — same posture as the census's unreadable-catalogue refusal).

Usage:
    uv run python tools/test_design_map_check.py
    uv run python tools/test_design_map_check.py --doc path/to/map.md --json
"""

from __future__ import annotations

import sys

# The #5128 interpreter guard MUST precede every other import: a module-level
# 3.12-only import raises ImportError on an older interpreter BEFORE a later
# guard could run, and the refusal then looks like a broken tool rather than a
# floor. Print-then-`SystemExit(int)` is the shape the #5128 census's detector
# accepts (`tests/test_entry_point_python_guard.py`), and exit 2 is this tool's
# "cannot answer the question here" code. Mirrors `tools/substrate_map_check.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/test_design_map_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/test_design_map_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOC = (
    REPO_ROOT
    / "docs"
    / "epics"
    / "2026-11-04-5090-vector-layer"
    / "TEST-DESIGN-surface-map.md"
)

#: The layers a surface may be assigned. "none" is deliberately absent: the
#: skill's rule is that an *identified* surface always has a layer, and a surface
#: with no layer is a surface nobody will test.
ALLOWED_LAYERS = frozenset({"unit", "contract", "live-docker", "doc-assert", "guard"})

#: A failure cell below this many words is almost always the mechanism restated
#: ("it uses db.indexes()"). The map's contract is a consequence, not a verb.
MIN_FAILURE_WORDS = 8

#: The scope items, the children the decomposition names, and the surface
#: population. All three are pinned rather than discovered, so deleting one from
#: the map is a violation instead of a smaller map. The surface pin is what stops
#: the CONSISTENT shrink — delete a row and every mention of it and the
#: referential checks all still pass (measured: exactly that on SF13 exited 0
#: before the pin existed).
CAPABILITIES = ("S1", "S2", "S3", "S4", "S5")
CHILD_ISSUES = ("11084", "11085", "11086", "11087", "11088", "11089")
SURFACES = tuple(f"SF{n}" for n in range(1, 16))

#: The seams `04-plan.md` names in §5, §6 and §7, as the crosswalk must carry
#: them. Keyed by the token the crosswalk's first column uses. `§7` is here for
#: its EXISTENCE: the row enumerates D1–D13's surfaces, so deleting it silently
#: drops that enumeration unless the row's presence is pinned (measured: the
#: deletion graded CLEAN before this pin existed).
PLAN_SEAMS = (
    "§5 row 1",
    "§5 row 2",
    "§5 row 3",
    "§5 row 4",
    "§5 row 5",
    "§6.1",
    "§6.2",
    "§6.3",
    "§6.4",
    "§7",
)

#: How many leading spaces a line may carry and still be a TABLE ROW.
#:
#: Markdown renders a `|` line as part of the table with up to THREE leading
#: spaces; FOUR or more makes it an indented code block (`CommonMark` §5.1, and
#: GFM tables inherit it). So 0-3 is the population of "rows a reader sees as
#: rows", and it is the population this checker must grade. #11415: the builder
#: used `line.startswith("|")`, so a row indented 1-3 spaces belonged to no block
#: and reached no rule — measured, an identical 4-cell row appended inside §5
#: graded `VIOLATION R5 zzz: names no surface` at column 0 and **CLEAN, exit 0**
#: with one leading space (and with three). A row that is not read is not graded,
#: and a map whose row was reflowed is not the map that was checked.
#:
#: The boundary is markdown's, not a tolerance knob: a 4+-space-indented `|` line
#: is an indented CODE BLOCK when the table is top-level content, which the reader
#: does not see as a table row at all, so grading it would refuse a docs-only edit
#: that pastes a table example into §5 (the false-RED class the
#: `_is_foreign_table` negative control exists for). ⚠️ Indentation is RELATIVE to
#: the containing block, so inside a list item (content at column 2) a line at
#: document column 4 can still render as part of a nested table — this predicate
#: models document-column indentation only, and the module docstring states that
#: residual. Either way the shape is neither graded nor refused, never a silent pass
#: that also reds.
MAX_TABLE_INDENT = 3


def _is_table_line(line: str) -> bool:
    """True when ``line`` is a markdown table ROW (0-3 leading spaces, then `|`).

    ONE predicate for the block builder, the seam discovery and the presence arm,
    so the three can never disagree about which lines are rows — the split between
    them is what #11415 measured: the builder used `startswith("|")` while `cells()`
    stripped leading whitespace, so the two halves of the parser disagreed about
    indentation and the disagreement was silent.
    """
    indent = len(line) - len(line.lstrip(" "))
    return indent <= MAX_TABLE_INDENT and line[indent:].startswith("|")


def _line_matches(line: str, prefix: str) -> bool:
    """True when ``line`` is the line a mutation targets, indentation aside.

    The MUTATORS need the same tolerance the ARMS have (#11415). They anchored at
    column 0 (`line.startswith("| **SF2**")`), so indenting a mutation-target row
    by 1-3 spaces — a docs-only reflow, which `check()` now grades correctly —
    made every affected mutator raise `AssertionError`, which `self_test` reports
    as UNTESTABLE and exits 1 on the required, path-filter-free `docs` job. The
    fix that made `check()` indentation-tolerant would otherwise have left the
    SAME edit red through the harness arm — the false RED #11415 is about, moved
    one function over.

    `prefix` is matched against the row's text with its leading spaces removed, and
    only when the line IS a table row; a non-row prefix (`## 6. Integration`, a
    heading `_insert_before` also serves) keeps plain prefix semantics.
    """
    if prefix.startswith("|"):
        return _is_table_line(line) and line.lstrip(" ").startswith(prefix)
    return line.startswith(prefix)


def _carries_seam_token(line: str, seam: str) -> bool:
    """True when a table row's FIRST cell STARTS WITH ``seam`` — discovery only.

    Which block IS the crosswalk is discovered by the seam tokens its data rows
    carry, and a reworded first cell (`| §5 row 1 (the default) |`) must still
    identify the block, so this is a PREFIX match. The PRESENCE arm in `check()`
    is the whole-cell one: the `§5 row 10` decoy is refused there, not here.

    Indentation is handled by `_is_table_line`, not by this pattern, so a row
    indented 1-3 spaces is discovery's row too (#11415).
    """
    if not _is_table_line(line):
        return False
    return re.match(rf"^ {{0,{MAX_TABLE_INDENT}}}\|\s*{re.escape(seam)}", line) is not None


SF_ROW = re.compile(rf"^ {{0,{MAX_TABLE_INDENT}}}\|\s*\*\*(SF\d+)\*\*\s*\|")
SF_ANY = re.compile(r"\bSF(\d+)\b")
SECTION = re.compile(r"^##\s+(\d+)\.\s", re.MULTILINE)


class Refusal(Exception):
    """The document could not be parsed — exit 2, never a silent pass."""


def read_doc(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # missing, unreadable, a directory — all exit 2
        raise Refusal(f"cannot read the surface map at {path}: {exc}") from exc


def split_sections(text: str) -> dict[str, str]:
    """Split on `## <n>.` headings. Returns {"1": body, ...}.

    A DUPLICATE number is a refusal, not a last-wins overwrite. Overwriting meant an
    appended decoy section (`## 1. The surfaces (appendix)` + a clean copy of the
    table) replaced the real one, so a map that had LOST a surface row graded CLEAN
    over the copy — measured, two edits: delete `| **SF13** | … |` and append the
    decoy -> `CLEAN`, exit 0, where the row deletion alone red red `R1 SF13: pinned
    surface is missing`; likewise a blanked §7 cell plus an appended §5 decoy. The
    document then renders two sections with the same number, which no reader can
    resolve — an unreadable map is exit 2 everywhere else in this file, so it is here
    too. The self-test cannot see this (it mutates the document, not the splitter),
    which is why the arm reports it as a REFUSAL rather than a violation.
    """
    marks = list(SECTION.finditer(text))
    if not marks:
        raise Refusal("no `## <n>.` sections found — the map's structure is gone")
    sections: dict[str, str] = {}
    for i, match in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        number = match.group(1)
        if number in sections:
            raise Refusal(
                f"section `## {number}.` appears twice — two sections cannot share a "
                "number, and grading the last one would hide whatever the first one lost"
            )
        sections[number] = text[match.start() : end]
    return sections


#: A pipe is a cell delimiter only when it carries an EVEN number of preceding
#: backslashes (CommonMark's escape parity): `\|` is a LITERAL pipe inside a cell,
#: `\\|` is an escaped backslash followed by a REAL delimiter. Splitting on every
#: `|` shifted every column after an escaped pipe — R2/R3 then read `row[4]`/`row[6]`
#: of a shifted row and reported CLEAN while the real failure cell was never read.
#: ⚠️ Parity, not the presence of a backslash: `(?<!\\)\|` gets `\|` right and
#: `\\|` WRONG (it swallows a real delimiter, so an 8-column row scored CLEAN and a
#: valid 7-column row was falsely refused — both measured before this scanner).


def _is_escaped(text: str, index: int) -> bool:
    """True when `text[index]` carries an ODD run of preceding backslashes."""
    run = 0
    while index - run - 1 >= 0 and text[index - run - 1] == "\\":
        run += 1
    return run % 2 == 1


def _split_cells(raw: str) -> list[str]:
    """Split on delimiter pipes only, honouring backslash parity."""
    parts: list[str] = []
    buf: list[str] = []
    for index, char in enumerate(raw):
        if char == "|" and not _is_escaped(raw, index):
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(char)
    parts.append("".join(buf))
    return parts


def cells(line: str) -> list[str]:
    """Split a markdown table row into trimmed cells (escape-parity aware)."""
    raw = line.strip()
    # Drop the row's boundary delimiters. Leading pipes are always delimiters; a
    # trailing pipe is one only when it is not escaped (otherwise it is content).
    raw = raw.lstrip("|")
    while raw.endswith("|") and not _is_escaped(raw, len(raw) - 1):
        raw = raw[:-1]
    return [cell.strip() for cell in _split_cells(raw)]


def parse_surface_rows(section: str) -> dict[str, dict[str, str]]:
    """Parse the surface table.

    Column order is the map's own header:
    `| SF | surface | type | flow | layer | contract | failure |`
    """
    rows: dict[str, dict[str, str]] = {}
    for line in section.splitlines():
        match = SF_ROW.match(line)
        if not match:
            continue
        row = cells(line)
        if len(row) != 7:
            # EXACT, not a floor. `cells()` is escape-aware, so a `\|` stays
            # inside its cell; what still makes an 8-cell row is a RAW extra
            # `|`, and `row[4]`/`row[6]` then read the WRONG columns — R2 and R3
            # grade a shifted row and report clean while the real failure cell is
            # never read. Measured before this arm existed: an 8-cell row scored
            # CLEAN. A width the parser cannot interpret is an unreadable map
            # (exit 2), the same posture as the short-row arm.
            raise Refusal(
                f"{match.group(1)} has {len(row)} column(s); the table needs exactly 7 "
                "(SF · surface · type · flow · layer · contract · failure) — a "
                "`|` inside a cell shifts every column after it"
            )
        sf = match.group(1)
        if sf in rows:
            raise Refusal(f"{sf} appears twice in the surface table")
        rows[sf] = {
            "surface": row[1].replace("**", "").strip(),
            "layer": row[4].replace("**", "").strip(),
            "contract": row[5],
            "failure": row[6],
        }
    if not rows:
        raise Refusal("no `| **SF<n>** |` rows found — the surface table is empty")
    return rows


def layer_tokens(cell: str) -> list[str]:
    """`unit + doc-assert` / `unit (negative) + guard` → ["unit", "guard"].

    A parenthetical is a QUALIFIER on the layer ("(negative)", "(skip-if-
    unavailable)"), not a second layer — it is dropped before splitting so the
    allowed-set test stays strict about the layer name itself.
    """
    stripped = re.sub(r"\([^)]*\)", " ", cell.replace("**", ""))
    tokens = [t.strip() for t in re.split(r"[+,/]| and ", stripped)]
    return [t for t in tokens if t]


def surface_tokens(text: str) -> set[str]:
    return {f"SF{n}" for n in SF_ANY.findall(text)}


def check(text: str) -> tuple[list[str], dict[str, object]]:
    """Return (violations, stats). Never raises for a content violation."""
    sections = split_sections(text)
    for needed in ("1", "3", "4", "5"):
        if needed not in sections:
            raise Refusal(f"section `## {needed}.` is missing — cannot check the map")

    violations: list[str] = []
    surfaces = parse_surface_rows(sections["1"])

    # R1a — the surface POPULATION is pinned: a row plus every mention of it can
    # be deleted consistently, and every cross-reference below would still
    # resolve. Only the pin catches that. Both directions: a missing pinned
    # surface, and a surface nobody pinned.
    for sf in SURFACES:
        if sf not in surfaces:
            violations.append(f"R1 {sf}: pinned surface is missing from the surface table")
    for sf in sorted(set(surfaces) - set(SURFACES)):
        violations.append(f"R1 {sf}: surface is not in the pinned population {SURFACES[0]}..{SURFACES[-1]}")

    # R2 / R3 — layers and named failures, per surface row.
    for sf, row in sorted(surfaces.items()):
        tokens = layer_tokens(row["layer"])
        if not tokens:
            violations.append(f"R2 {sf}: no test layer assigned")
        for token in tokens:
            if token not in ALLOWED_LAYERS:
                violations.append(
                    f"R2 {sf}: layer {token!r} is not one of {sorted(ALLOWED_LAYERS)}"
                )
        failure = row["failure"].replace("**", "").strip()
        words = len(failure.split())
        if words < MIN_FAILURE_WORDS:
            violations.append(
                f"R3 {sf}: failure cell is {words} word(s) (min {MIN_FAILURE_WORDS}) — "
                "a restated mechanism is not a named failure"
            )

    # R1 — capabilities → surfaces, both directions.
    capability_section = sections["3"]
    for capability in CAPABILITIES:
        row = next(
            (
                line
                for line in capability_section.splitlines()
                if re.match(rf"^ {{0,{MAX_TABLE_INDENT}}}\|\s*\*\*{capability}\*\*", line)
            ),
            None,
        )
        if row is None:
            violations.append(f"R1 {capability}: no row in the capability table")
            continue
        named = surface_tokens(row)
        if not named:
            violations.append(f"R1 {capability}: names no surface")
        for sf in sorted(named - set(surfaces)):
            violations.append(f"R1 {capability}: names {sf}, which has no surface row")

    # R4 — every surface touched by ≥1 child; every child present.
    child_section = sections["4"]
    touched: set[str] = set()
    for issue in CHILD_ISSUES:
        row = next(
            (
                line
                for line in child_section.splitlines()
                if re.match(
                    rf"^ {{0,{MAX_TABLE_INDENT}}}\|\s*\*\*C\d\*\*\s*\|\s*\*\*#{issue}\*\*",
                    line,
                )
            ),
            None,
        )
        if row is None:
            violations.append(f"R4 #{issue}: no row in the child table")
            continue
        named = surface_tokens(row)
        if not named:
            violations.append(f"R4 #{issue}: touches no surface")
        for sf in sorted(named - set(surfaces)):
            violations.append(f"R4 #{issue}: names {sf}, which has no surface row")
        touched |= named
    for sf in sorted(set(surfaces) - touched):
        violations.append(
            f"R4 {sf}: no child touches it — the issue forbids a surface no child touches"
        )

    # R5 — the plan's seams are all crosswalked, AND each crosswalk row's surface
    # cell resolves to a real surface row. The second half was missing in the
    # first version: R1 and R4 both referentially check what they read, so a
    # seam bound to `SF99` was the one referential hole left in the map — a typo
    # that produces a checklist row pointing at nothing. (A surface number typed
    # a digit OFF lands on a real surface row, so R5 does NOT catch it; R5's
    # wording is "names a surface with no row in §1", and that is all it claims.)
    crosswalk = sections["5"]
    present: set[str] = set()
    for seam in PLAN_SEAMS:
        # The seam must EQUAL the row's first cell, not merely prefix it. A prefix
        # match let a row labelled one digit off stand in for the pinned seam:
        # rename `| §5 row 1 |` to `| §5 row 10 |` and the pinned `§5 row 1`
        # crosswalk (SF1/SF2) was gone while the map graded CLEAN — measured, and
        # the same for `§6.1` masked by `§6.10`. The message is unchanged.
        row = next(
            (
                line
                for line in crosswalk.splitlines()
                if _is_table_line(line) and cells(line)[:1] == [seam]
            ),
            None,
        )
        if row is None:
            violations.append(f"R5 {seam}: missing from the §5/§6 crosswalk")
            continue
        present.add(seam)
    # The referential half covers EVERY row of the crosswalk table, not only the
    # nine pinned seams: the table also carries a `§7` row (D1–D13), and a scope
    # limited to `PLAN_SEAMS` let `| §7 | SF99 … |` exit 0 — a claim of "every
    # crosswalk row" that only held for 9 of the 10. A pinned seam's first cell IS
    # its seam token, so its violation message is unchanged. (A surface number
    # typed a digit OFF still lands on a real row, so R5 does NOT catch that; its
    # wording is "names a surface with no row in §1", and that is all it claims.)
    # Grade EVERY markdown table ROW in §5 — a `|` line at 0-3 leading spaces
    # (`_is_table_line`; #11415 measured that a row indented 1-3 spaces belonged to no
    # block and so was graded by nothing: the identical blanked 4-cell row graded
    # `VIOLATION R5 zzz: names no surface` at column 0 and CLEAN, exit 0, with one
    # leading space) — and let a line out only as one of THREE classes: an
    # alignment row (`|---|---|`), a line that IS the crosswalk header by SHAPE
    # (`_is_crosswalk_header`), or a line inside a whole WELL-FORMED block that is
    # provably some other table (`_is_foreign_table`: a different separator width, no
    # pinned seam token). In particular the section
    # is NOT split into "the crosswalk table" and "other tables" and graded block by
    # block, because that split was itself the last false PASS:
    #
    #   A block counted as the crosswalk only when the line directly ABOVE its
    #   separator was a header, so a BLANK LINE plus `|---|---|---|---|` inserted above
    #   `| §7 | … |` started a block that OPENED with a separator and had NO header
    #   (`_separator(block) == 0`, so `separator > 0` fails and the block can never be
    #   recognised as crosswalk-headed). That block was excluded from grading, so every
    #   row beneath it went unread: blank §7's surface cell, insert the blank line and
    #   the separator, and the map graded CLEAN (exit 0, measured) while the same
    #   document with the cell blanked and NO split red (`R5 §7: names no surface`,
    #   exit 1). One inserted line was the difference between the arm firing and the
    #   arm blind, which makes it a bypass rather than a nuance.
    #
    # 1. A positional header skip was one-shot for the whole section, so deleting
    #    the header made the first DATA row the "header" and left it ungraded
    #    (measured: header deleted + §5 row 1's cell blanked -> CLEAN).
    # 2. Breaking at the first non-`|` line graded only the FIRST table, so a
    #    second validly-headed crosswalk table's rows were never read (measured:
    #    §5 split into two headed tables, one cell blanked in the second -> CLEAN).
    # 3. Selecting by header SHAPE (not position) is what makes both directions
    #    right: an ordinary `| area | kind | surface |` table is not the crosswalk
    #    wherever it sits, so its rows are not graded as crosswalk rows (measured:
    #    content-only matching refused them as "one column short" — a false RED).
    #
    # ⚠️ Grading only BLOCKS the selection recognised was itself a bypass, and that
    # is why the lines below are filtered rather than the blocks: a block counted as
    # the crosswalk only when the line directly ABOVE its separator was a header, so a
    # BLANK LINE plus `|---|---|---|---|` inserted above `| §7 | … |` started a block
    # that OPENED with a separator and had NO header — excluded from grading, leaving
    # every row beneath it unread.
    # Measured on one document: blank §7's surface cell -> exit 1; blank it AND insert
    # that blank line with the separator -> CLEAN, exit 0 (false PASS), and exit 1 here.
    #
    # So: EVERY `|` line in §5 that belongs to a BLOCK is graded, minus THREE classes —
    # an alignment row, a line that IS the crosswalk header by shape, and a line inside
    # a WELL-FORMED foreign block (`_is_foreign_table`). That third exemption is a
    # WELL-FORMED table whose own separator row is a DIFFERENT width from the
    # crosswalk's: it is genuinely another table, and grading it would refuse a
    # docs-only edit that appends one to §5 (a false RED). In the SHIPPED map no such
    # table exists — R5 reads §5 only, and §6's 3-wide checklist table is never in
    # scope — so the exemption exists for an editor's edit, and its negative control is
    # what keeps it
    # honest.
    # A 4-wide decoy gets no such exemption — that is the shape an impersonating row
    # or a split-off tail would take.
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in crosswalk.splitlines():
        if _is_table_line(line):
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    def _separator(block: list[str]) -> int | None:
        return next(
            (
                index
                for index, line in enumerate(block)
                if all(set(cell) <= {"-", ":", ""} for cell in cells(line))
            ),
            None,
        )

    # The crosswalk's WIDTH is measured from the table that actually carries the pinned
    # seam tokens, NOT from a shape-matching header: a reworded first cell makes the
    # header stop matching, and anchoring on it made every block "differently shaped"
    # — i.e. exempt — so the real crosswalk went ungraded (measured through the merge:
    # a reworded first cell plus a decoy graded a deleted §6.4 cell CLEAN). The seam
    # token is the crosswalk's actual signature; the header is only a hint.
    seam_block = next(
        (
            block
            for block in blocks
            if any(_carries_seam_token(line, seam) for line in block for seam in PLAN_SEAMS)
        ),
        None,
    )
    measured_from = seam_block if seam_block is not None else None
    if measured_from is None:
        shape_header = next(
            (line for line in crosswalk.splitlines() if _is_crosswalk_header(line)), None
        )
        crosswalk_width = len(cells(shape_header)) if shape_header is not None else 0
    else:
        separator = _separator(measured_from)
        sample = measured_from[separator] if separator is not None else measured_from[0]
        crosswalk_width = len(cells(sample))

    def _is_foreign_table(block: list[str]) -> bool:
        """True only for a WELL-FORMED table that is demonstrably NOT the crosswalk.

        Three conditions, each closing a measured false PASS:
        1. The block carrying the pinned seam tokens is never exempt — that block IS
           the crosswalk, whatever its header looks like. Measured: exempting it let a
           blanked §5 row 3 grade CLEAN once a decoy header block was inserted above it.
        2. Any block carrying a pinned seam token is never exempt (a second crosswalk
           table is graded, not skipped).
        3. The block must be internally CONSISTENT and a different WIDTH from the
           crosswalk, measured on each block's OWN separator row. Measured: comparing
           the crosswalk's separator to this block's header excused the crosswalk itself
           in a well-formed table, and an inconsistent block (a row one column short)
           silently swallowed the rows after it.
        """
        if block is measured_from:
            return False
        if any(_carries_seam_token(line, seam) for line in block for seam in PLAN_SEAMS):
            return False
        separator = _separator(block)
        if separator is None or separator == 0:
            return False
        width = len(cells(block[separator]))
        if width == crosswalk_width:
            return False
        return all(len(cells(line)) == width for line in block if not _is_separator_line(line))

    graded_blocks = blocks if not crosswalk_width else [block for block in blocks if not _is_foreign_table(block)]
    rows: list[str] = [
        line
        for block in graded_blocks
        for line in block
        if not _is_separator_line(line) and not _is_crosswalk_header(line)
    ]

    for line in rows:
        row = cells(line)
        label = row[0] or "?"
        # A crosswalk row that names NO surface was the third "names nothing" hole:
        # R1 (`R1 <capability>: names no surface`) and R4 (`R4 #<issue>: touches no
        # surface`) both carry the arm, R5 did not, so blanking
        # §6.4's surface cell — the row that carries the approval request — graded
        # CLEAN on the REQUIRED `docs` job (measured). Deleting the row reds R5 as
        # `missing`; emptying its cell must red it as `names no surface`.
        #
        # This reads the SURFACE CELL, not the whole line (the referential arm below
        # reads the line, which is correct for it): §6.4's own `why` cell says
        # "**SF13 is the row a capstone must not skip**", so a whole-line test would
        # keep the blanked row green — the self-test case below proved that the whole-
        # line form of this arm does not fire (measured: `expected violation, got
        # clean`).
        #
        # EVERY data row of a crosswalk-headed table is tested, not only rows whose
        # label starts with `§`: a label-based gate let two shrink edits through, both
        # measured CLEAN — renaming `| §7 |` to `| 7 |` and blanking its cell, and
        # deleting the `§7` row outright. `§7` is now in PLAN_SEAMS so its existence is
        # pinned too.
        #
        # The row must be the WIDTH the parser can read, exactly as a §1 row must be
        # 7 cells: a crosswalk row one cell SHORT keeps `row[2]` pointing at the NOTE,
        # and a note that names surfaces ("… **SF13 is the row a capstone must not
        # skip**") then satisfies the arm below. Measured on the live map: deleting
        # §6.4's surface cell outright — not blanking it, deleting it — graded CLEAN,
        # exit 0, on the REQUIRED `docs` job, and the same deletion on §7 did too.
        # Blanking was covered (the case above); deleting was the hole beside it.
        # Re-measured at `a4f055a96`, after the per-table-block selection rewrite:
        # the hole survived it (both deletes still CLEAN), so the arm belongs inside
        # this loop rather than keyed to how the table was found.
        if len(row) != 4:
            raise Refusal(
                f"{label} crosswalk row has {len(row)} column(s); the table needs "
                f"exactly 4 (plan § · seam / interface · surface(s) · note) — a DELETED "
                f"cell shifts the note into the surface column, where its own words "
                f"satisfy the arm below"
            )
        surface_cell = row[2]
        if not surface_tokens(surface_cell):
            violations.append(f"R5 {label}: names no surface")
        for sf in sorted(surface_tokens(line) - set(surfaces)):
            violations.append(f"R5 {label}: names {sf}, which has no surface row")

    stats: dict[str, object] = {
        "surfaces": len(surfaces),
        "layers": sorted({t for row in surfaces.values() for t in layer_tokens(row["layer"])}),
        "capabilities": len(CAPABILITIES),
        "children": len(CHILD_ISSUES),
        "plan_seams": len(present),
        "violations": len(violations),
    }
    return violations, stats


def _rewrite_cell(line: str, column: int, value: str, target: str) -> str:
    """Rewrite one cell of a table row, re-emitting it — the shared mutation primitive.

    Parses with `cells()` — the SAME escape-aware split the checker grades with —
    so the mutation lands on the cell its case names even when the row carries an
    escaped `\\|`. Splitting on every `|` here made a CORRECT document report
    "the checker is a rubber stamp" (measured: 2/11 NOT caught on a copy of the
    live map whose only change was an escaped pipe in SF2's surface cell).

    The width the caller INTENDS (a mutation whose value splices a raw `|` into a
    cell must widen the row — that is the case's whole point) is derived from the
    value itself; what is refused is an accidental width change from the rewrite,
    which would move the column the next case names.
    """
    row = cells(line)
    if column >= len(row):
        raise AssertionError(
            f"self-test mutation target {target} has {len(row)} cell(s); column {column} does not exist"
        )
    row[column] = value
    rewritten = "| " + " | ".join(row) + " |\n"
    added = len(cells(f"| {value} |")) - 1  # extra cells the value itself splices in
    width, expected = len(cells(rewritten)), len(cells(line)) + added
    if width != expected:
        raise AssertionError(
            f"self-test mutation on {target} changed the row's width {expected} → {width}"
        )
    return rewritten


def _rewrite_row(text: str, sf: str, column: int, value: str) -> str:
    """Replace one cell of one surface row — the mutation primitive."""
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if _line_matches(line, f"| **{sf}**"):
            line = _rewrite_cell(line, column, value, sf)
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {sf} not found")
    return "".join(out)


#: The surface-cell values a crosswalk HEADER carries. Matched by SHAPE (with the
#: four-cell count and the `plan §` first cell) — there is NO positional condition:
#: nothing here looks at the line above or below a candidate header. Indentation is
#: NOT part of the shape: `cells()` strips it, so an indented header is still the
#: header (#11415 closed the block builder's `startswith("|")` blind spot with
#: `_is_table_line`, which is the one predicate every row-reading arm shares).
_HEADER_SURFACE_CELLS = frozenset({"surface(s)", "surfaces", "surface"})

#: The crosswalk header's FIRST cell. The crosswalk maps `04-plan.md`'s seams, so
#: its first column is the plan section — the one cell that tells it apart from an
#: ordinary table that happens to have a `surface` column.
_HEADER_PLAN_CELL = "plan §"


def _is_crosswalk_header(line: str) -> bool:
    """True when this `|` line is the crosswalk's header row, judged by SHAPE.

    Four cells, a first cell naming the plan section, and a third cell naming
    surfaces — all three, not any one of them. A content hint alone made an
    ORDINARY §5 table classify as the crosswalk: in a document that IS a surface
    map, `| area | kind | surface |` is a natural edit, and grading its rows as
    crosswalk rows refused them as "one column short" — a FALSE RED on the
    REQUIRED, path-filter-free `docs` job, blaming a deleted cell nobody deleted
    (measured at `3b642c7ca`: exit 2; the same map graded `CLEAN` before the
    per-block selection landed). The `plan §` cell is the crosswalk's defining
    property, so requiring it costs nothing that a reworded `surface(s)` cell
    still gets: renaming THAT cell keeps the block headed.

    A reworded FIRST cell does NOT drop the block into the fail-closed fallback below
    (every §5 block graded): the seam block is still discovered by its data rows, so
    `measured_from` — and with it the crosswalk width — is set from the separator
    anyway. That fallback fires only when NO block carries a pinned seam token in a
    first cell. What a reworded first cell costs is the header exemption itself, so
    the header row reds `R5 <label>: names no surface` (measured).
    """
    row = cells(line)
    if len(row) != 4:
        return False
    return row[0].strip() == _HEADER_PLAN_CELL and row[2].strip().lower() in _HEADER_SURFACE_CELLS


def _is_separator_line(line: str) -> bool:
    """True when a `|` line is a markdown table's alignment row (`|---|---|`).

    Deliberately stricter than "every cell is blank": at least one cell must carry a
    dash run, so a row an editor blanked to `|  |  |  |  |` is still GRADED (and reds
    `names no surface`) instead of being read as an alignment row and skipped.
    """
    parsed = cells(line)
    return bool(parsed) and any("-" in cell for cell in parsed) and all(
        set(cell) <= {"-", ":", ""} for cell in parsed
    )


def _insert_before(text: str, prefix: str, inserted: str) -> str:
    """Insert a whole line before the first line starting with `prefix`."""
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if not hit and _line_matches(line, prefix):
            out.append(inserted if inserted.endswith("\n") else inserted + "\n")
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {prefix!r} not found")
    return "".join(out)


def _rewrite_crosswalk_row(text: str, seam: str, value: str) -> str:
    """Point one crosswalk row's surface cell somewhere else — the mapping primitive."""
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if _line_matches(line, f"| {seam}"):
            line = _rewrite_cell(line, 2, value, seam)
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {seam!r} not found")
    return "".join(out)


def _delete_crosswalk_cell(text: str, seam: str, column: int) -> str:
    """Delete one cell from one crosswalk row — the width primitive.

    Blanking a cell keeps the table's width; DELETING it does not, and the row that
    loses its `surface(s)` cell is graded by whatever moved into that position. This
    is the edit the width arm exists for, so the mutation must perform it the way an
    editor would (`| a | b | c |` → `| a | c |`), not as an empty cell.
    """
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if not hit and _line_matches(line, f"| {seam}"):
            row = cells(line)
            if column >= len(row):
                raise AssertionError(
                    f"self-test mutation target {seam!r} has {len(row)} cell(s); "
                    f"column {column} does not exist"
                )
            row.pop(column)
            line = "| " + " | ".join(row) + " |\n"
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {seam!r} not found")
    return "".join(out)


def _relabel_seam(text: str, seam: str, label: str) -> str:
    r"""Re-label a pinned seam's first cell, keeping everything else — the decoy a
    PREFIX match cannot tell from the pinned seam.

    R5's presence arm used `re.match(r"^\|\s*" + re.escape(seam), line)`, so renaming
    `| §5 row 1 |` to `| §5 row 10 |` still "matched" the pinned `§5 row 1`: the
    SF1/SF2 crosswalk was gone and the map graded CLEAN (exit 0, measured), and the
    same held for `§6.1` masked by `§6.10`. The label is the only cell this edits, so
    a whole-cell match is the ONLY thing that can refuse it — which is why this case
    exists: before it, reverting the fix reddened no row at all (`--self-test` stayed
    green and the decoy graded clean).
    """
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if _line_matches(line, f"| {seam} |"):
            row = cells(line)
            row[0] = f" {label} "
            line = "| " + " | ".join(row) + " |\n"
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {seam!r} not found")
    return "".join(out)


def _rename_and_blank_crosswalk_row(text: str, seam: str, label: str) -> str:
    """Re-label one crosswalk row AND blank its surface cell — the evasion primitive.

    Reproduces the edit that a label-gated "names no surface" arm could not see:
    `| §7 | … | **SF1** … |` becomes `| 7 | … |  |` in one edit. Kept as a named
    helper, not an inline lambda, so the case's intent is readable where it runs.
    """
    out: list[str] = []
    hit = False
    for line in text.splitlines(keepends=True):
        if _line_matches(line, f"| {seam}"):
            row = cells(line)
            row[0] = f" {label} "
            row[2] = ""
            line = "| " + " | ".join(row) + " |\n"
            hit = True
        out.append(line)
    if not hit:
        raise AssertionError(f"self-test mutation target {seam!r} not found")
    return "".join(out)


def _drop_line(text: str, prefix: str) -> str:
    """Delete every line starting with `prefix` — the removal primitive."""
    kept = [line for line in text.splitlines(keepends=True) if not _line_matches(line, prefix)]
    if len(kept) == len(text.splitlines(keepends=True)):
        raise AssertionError(f"self-test mutation target {prefix!r} not found")
    return "".join(kept)


def _split_and_blank_crosswalk_row(text: str, seam: str) -> str:
    """Blank one crosswalk row's surface cell AND split it off with a bare separator.

    The bypass a BLOCK-BY-BLOCK selection allowed, in one edit: a `|---|---|---|---|`
    (plus a blank line) placed above the row starts a block that has a separator and
    no header, and such a block was excluded from grading — so the blanked row
    beneath it was never read and the map graded CLEAN (measured: exit 0), while the
    same document with the cell blanked and NO split red
    (`R5 §7: names no surface`, exit 1). The separator's width is taken from the
    crosswalk header so the split stays a plausible edit rather than a malformed
    table, and both anchors raise instead of no-oping.
    """
    lines = text.splitlines(keepends=True)
    index = next((i for i, line in enumerate(lines) if _line_matches(line, f"| {seam}")), None)
    if index is None:
        raise AssertionError(f"self-test mutation target {seam!r} not found")
    header = next((line for line in lines if _is_crosswalk_header(line)), None)
    if header is None:
        raise AssertionError("the crosswalk header moved — cannot size the separator")
    separator = "|" + "---|" * len(cells(header)) + "\n"
    blanked = _rewrite_cell(lines[index], 2, "", seam)
    return "".join([*lines[:index], "\n", separator, blanked, *lines[index + 1 :]])


def _indent_and_blank_inserted_row(text: str, spaces: int = 1) -> str:
    """Insert a crosswalk row with a blank surface cell, INDENTED by ``spaces``.

    The `#11415` shape, executed the way an editor produces it: a paste into a list
    item (or a reflow) adds leading spaces to a row whose surface cell is empty. The
    block builder selected rows with `line.startswith("|")`, so an indented row
    joined no block and was graded by nothing — measured on the live map: the same
    4-cell row with a blank surface cell graded `VIOLATION R5 zzz: names no surface`
    (exit 1) at column 0 and **CLEAN, exit 0** with one leading space, and with three.

    ⛔ The row is INSERTED rather than taken from the pinned population on purpose:
    indenting a pinned row (§7, §6.4, …) would red the missing-seam arm even with the
    hole open, so the case would pass for the wrong reason and prove nothing about
    indentation. A non-pinned row can only be caught by GRADING it — which is exactly
    the arm under test.

    ⛔ This case is why the hole survived to `#11415`: all 21 pre-existing mutations
    were written at column 0, so the self-test could only exercise the population the
    bug already read. A mutator that cannot EXPRESS the failing shape is a rubber
    stamp over that shape. It is RED before the fix (`expected violation, got clean`)
    and green after.

    One width is enough: every row-reading arm shares `_is_table_line`, so 1 and 3
    spaces are the same code path (`MAX_TABLE_INDENT`), and 4+ is deliberately not a
    row — markdown renders it as an indented code block.
    """
    if not 1 <= spaces <= MAX_TABLE_INDENT:
        raise AssertionError(
            f"indent of {spaces} space(s) is not a markdown table row — the mutation "
            f"would express a shape that is an indented code block, not a row"
        )
    row = "| extra row | a row the gate must still read |  | note |"
    return _insert_before(text, "| §6.4 |", " " * spaces + row)


def _duplicate_section_decoy(text: str) -> str:
    """Append a SECOND `## 1.` section carrying a clean copy of the first — the decoy.

    The splitter used to overwrite on a duplicate number, so this decoy REPLACED the
    real §1: a map that had lost a surface row graded CLEAN over the copy (measured —
    deleting SF13's row alone red, deleting it plus appending this decoy graded
    `CLEAN`, exit 0), and the rendered document still carries the loss. Duplicate
    numbers are now a REFUSAL, which is a different posture from a rule violation and
    is why this case expects `refusal`.
    """
    sections = split_sections(text)
    if "1" not in sections:
        raise AssertionError("self-test mutation target `## 1.` not found")
    return text.rstrip("\n") + "\n\n" + sections["1"].rstrip("\n") + "\n"


def _drop_section_heading(text: str) -> str:
    """Replace §4's heading marker, whatever its title currently says.

    Anchored on the section NUMBER, not on a title. A bare
    `t.replace("## 4. Every surface", …)` is a silent NO-OP the moment the heading
    is reworded — and `check()` still grades that map CLEAN, because sections are
    keyed by number only — so the no-op used to red the REQUIRED `docs` job with
    "the checker is a rubber stamp": a false diagnosis of a correct document
    (measured). Raising here makes `self_test` report UNTESTABLE instead, which is
    a different and honest statement, and this anchor cannot no-op silently.
    """
    mutated, count = re.subn(r"(?m)^## 4\.\s+.*$", "### surface-to-child map", text)
    if not count:
        raise AssertionError(
            "self-test mutation target '## 4.' not found — the mutation would be a no-op"
        )
    return mutated


#: Each entry is `(name, mutator, expected)`, where `expected` is `"violation"`
#: (a rule fires, exit 1) or `"refusal"` (the map no longer parses, exit 2).
#: These are the deletions an editor actually makes — remove a row, blank a cell,
#: drop a child, forget a seam — and a checker that stayed green through any of
#: them would be a rubber stamp. Run with `--self-test`.
SELF_TEST_MUTATIONS: tuple[tuple[str, object, str], ...] = (
    (
        "delete the SF13 row (the surface-change surface)",
        lambda t: _drop_line(t, "| **SF13**"),
        "violation",
    ),
    (
        "blank SF10's layer (the new state S4 exists for)",
        lambda t: _rewrite_row(t, "SF10", 4, ""),
        "violation",
    ),
    (
        "drop the #11088 child row (SF11/SF13 become orphans)",
        lambda t: _drop_line(t, "| **C5**"),
        "violation",
    ),
    (
        "delete the S3 capability row",
        lambda t: _drop_line(t, "| **S3**"),
        "violation",
    ),
    (
        "drop the §6.4 surface-statement seam from the crosswalk",
        lambda t: _drop_line(t, "| §6.4"),
        "violation",
    ),
    (
        "restate SF2's layer as a mechanism name",
        lambda t: _rewrite_row(t, "SF2", 4, "encode_for_row"),
        "violation",
    ),
    (
        "shorten SF2's failure to a mechanism restatement",
        lambda t: _rewrite_row(t, "SF2", 6, "it calls encode_for_row"),
        "violation",
    ),
    (
        # The DELETE is the point: `.replace("SF13", "SF1")` — what this case
        # did first — RENAMES the remaining mentions instead of removing them,
        # so the document it built was not the consistent shrink its own label
        # (and the map's §8 record) claims. Measured at `2cb1f35c2` (the commit that
        # shipped the rename form): 13 mentions renamed. The map has grown since, so
        # the CURRENT number is 18 at `3b642c7ca` — re-measure, do not quote 13 as if
        # it were this head's count.
        "delete the SF13 row AND every mention of it (a consistent shrink)",
        lambda t: re.sub(r"\bSF13\b", "", _drop_line(t, "| **SF13**")),
        "violation",
    ),
    (
        "retype a crosswalk surface as one that has no row",
        lambda t: _rewrite_crosswalk_row(t, "§6.4", "**SF99**"),
        "violation",
    ),
    (
        # The referential half's own "names nothing" hole. R1
        # (`R1 <capability>: names no surface`) and R4 (`R4 #<issue>: touches no
        # surface`) both refuse a row that names no surface; R5 did not, so
        # blanking §6.4's surface cell graded CLEAN on the REQUIRED `docs` job
        # (measured). Deleting the row reds R5 as `missing`; emptying its cell must
        # red it as `names no surface`, and this case is what proves the arm runs —
        # it is also what proved the arm must read the SURFACE CELL: a whole-line
        # form does not fire, because §6.4's `why` cell names SF13 too (measured).
        "blank the §6.4 surface cell (a crosswalk row that names nothing)",
        lambda t: _rewrite_crosswalk_row(t, "§6.4", ""),
        "violation",
    ),
    (
        # The evasion a LABEL-gated arm allowed, and the reason the arm now tests
        # EVERY data row: rename `| §7 |` to `| 7 |` and blank its surface cell in
        # the same edit. Both the rename and the blank graded CLEAN (measured), so a
        # shrinker could shed the D1–D13 → surface enumeration by re-labelling the
        # row it emptied. This case fails if the arm ever regains a `§` gate.
        # This one guards the §7 PIN (the rename removes the pinned seam), not the
        # every-row arm — the case below guards that.
        "rename the §7 label AND blank its surface cell (a row the gate cannot see)",
        lambda t: _rename_and_blank_crosswalk_row(t, "§7", "7"),
        "violation",
    ),
    (
        # The every-row arm, and the case that fails if it ever regains a `§` gate:
        # a crosswalk row whose label carries no `§`, with a blank surface cell. Under
        # a label gate this edit graded CLEAN (measured); it must be refused. The
        # rename case above is satisfied by the PIN, so it cannot guard this.
        "add a crosswalk row with no `§` label and a blank surface cell",
        lambda t: _insert_before(
            t, "| §6.4 |", "| extra row | a row the gate must still read |  | note |"
        ),
        "violation",
    ),
    (
        # The block-selection bypass: a blank cell is refused ONLY while the row sits
        # in a block the selection recognises. Insert a bare separator above it and
        # the row used to fall into an excluded block and go unread (measured: CLEAN,
        # exit 0, against a control that reds without the split). This case fails if
        # the selection ever goes back to grading some blocks and not others.
        "blank the §7 surface cell AND split it off with a bare separator (a row in an ungradeable block)",
        lambda t: _split_and_blank_crosswalk_row(t, "§7"),
        "violation",
    ),
    (
        # A crosswalk row must not be able to impersonate the header. The header is
        # matched by SHAPE (four cells, `plan §`, a surface-naming third cell), and a
        # row whose surface cell was rewritten to the bare token `surface` must still
        # be graded — it holds a seam label, not `plan §`, so it is not the header.
        "retype §6.4's surface cell as the bare token `surface` (a row impersonating the header)",
        lambda t: _rewrite_crosswalk_row(t, "§6.4", "surface"),
        "violation",
    ),
    (
        # ⛔ `#11415`, and the case whose ABSENCE let the hole live: every other case
        # in this tuple writes its row at column 0, so none of them could express the
        # shape the block builder failed to reach. This one indents a blanked row by
        # ONE space — markdown's own table indentation, so a reader still sees it as a
        # row — and requires the checker to grade it. Measured pre-fix: CLEAN, exit 0;
        # post-fix: `R5 extra row: names no surface`.
        "insert a blanked crosswalk row INDENTED one space (a row the block builder never reached)",
        lambda t: _indent_and_blank_inserted_row(t, 1),
        "violation",
    ),
    (
        # The DELETE, not the blank: an editor that removes the `surface(s)` cell
        # leaves a 3-cell row whose note lands in the surface column, so the arm
        # below reads §6.4's own "**SF13 is the row a capstone must not skip**" and
        # scores the row against a surface it never assigned. Measured before the
        # width arm existed: CLEAN, exit 0, on the REQUIRED `docs` job — the same
        # false PASS class as an 8-cell §1 row, one edit over from the blank case.
        "delete the §6.4 surface cell outright (a crosswalk row one column short)",
        lambda t: _delete_crosswalk_cell(t, "§6.4", 2),
        "refusal",
    ),
    (
        # A NEGATIVE control, and the one this round added: not every mutation must
        # red. This one must stay CLEAN. An ordinary table appended to §5 whose third
        # column is literally `surface` is a natural docs-only edit in a document that
        # IS a surface map, and grading it as the crosswalk refused its rows as "one
        # column short" — a false RED on the required check blaming a deleted cell
        # nobody deleted (measured at `3b642c7ca`, exit 2; the crosswalk is now
        # selected by header SHAPE). Without this case, content-only header matching
        # could come back with every other case still green.
        "append an ordinary §5 table with a `surface` column (must stay CLEAN)",
        lambda t: _insert_before(
            t, "## 6. Integration", "| area | kind | surface |\n|---|---|---|\n| a | b | c |\n\n"
        ),
        "clean",
    ),
    (
        # The width rule must be covered by the arm that is supposed to catch a
        # quietly weakened rule. Without this case, reverting `!= 7` to the old
        # `< 7` floor left --self-test green at 10/10 — the false PASS this rule
        # closes, re-opened with the REQUIRED `docs` job fully green. Since
        # MIN_SELF_TEST_MUTATIONS exists, re-opening it costs TWO edits: at the
        # shipped floor, deleting this case alone is red — `self-test: N mutation(s)
        # registered, at least <floor> required`, exit 1 — before the relaxed rule is
        # ever exercised. (The counts are deliberately not written out here: they
        # drifts every time a case is added, and this comment quoted a population
        # three cases out of date.)
        "splice a raw `|` into SF2's contract cell (a row that is no longer 7 columns)",
        lambda t: _rewrite_row(t, "SF2", 5, "live-docker | unit"),
        "refusal",
    ),
    (
        # The PARITY rule must be covered by the same arm, for the same reason.
        # Without this case, reverting `_is_escaped` to the pre-parity `(?<!\\)\|`
        # test left --self-test green at 11/11 (measured) while the rule's own
        # headline direction came back: on the pre-parity rule a `\\|`-spliced row
        # parsed as 7 cells and scored CLEAN (a swallowed real delimiter), and a
        # valid 7-cell row ending in `\\` parsed as 6 and was refused. The value
        # below is `\\|` — an escaped backslash followed by a REAL delimiter.
        "splice a `\\\\|` into SF2's contract cell (escaped backslash + REAL delimiter)",
        lambda t: _rewrite_row(t, "SF2", 5, "\\\\| unit"),
        "refusal",
    ),
    (
        # A duplicated section number was last-wins, so an appended decoy replaced the
        # real section and a map that had lost a row graded CLEAN over the copy
        # (measured). This case fails if the splitter ever goes back to overwriting.
        "append a duplicate `## 1.` section carrying a clean copy (a decoy that hides a shrink)",
        _duplicate_section_decoy,
        "refusal",
    ),
    (
        # Anchored on the section NUMBER and asserted to have moved. A bare
        # `t.replace("## 4. Every surface", …)` is a silent NO-OP once the heading
        # is reworded — and `check()` still grades such a map CLEAN, because
        # sections are keyed by number — so the no-op used to red the REQUIRED
        # `docs` job with "the checker is a rubber stamp": a false diagnosis of a
        # correct document (measured). `_drop_section_heading` raises instead, and
        # `self_test` reports UNTESTABLE separately from a mutation that was
        # applied and not caught.
        "delete the child table's section heading",
        _drop_section_heading,
        "refusal",
    ),
    (
        "relabel `§5 row 1` as `§5 row 10` (a pinned seam one digit off — only a whole-cell match refuses it)",
        lambda t: _relabel_seam(t, "§5 row 1", "§5 row 10"),
        "violation",
    ),
)


#: The population `--self-test` must carry. A floor, not a count-only print:
#: emptying `SELF_TEST_MUTATIONS` is a ONE-place edit and used to print
#: "self-test: 0/0 mutation(s) behaved as designed — the checker is not a rubber stamp" and
#: exit 0 (measured), which is the false PASS this arm exists to prevent. The
#: map's own residual covers a THREE-place edit (row + pin + case together), so
#: the shrink that is two places short of that must still be loud.
MIN_SELF_TEST_MUTATIONS = 22

#: How many SHAPES `--self-test` must run the population against. A floor for the
#: same reason as `MIN_SELF_TEST_MUTATIONS`: deleting the indented entry is a
#: ONE-place edit that otherwise leaves `--self-test` green with its second arm
#: gone (fresh review of PR #12016, P3 — measured: the summary printed one success
#: line and every pin stayed green). With the floor, that edit exits 1 and names
#: what went missing.
MIN_SELF_TEST_PASSES = 2


def self_test_passes(text: str) -> tuple[tuple[str, str, bool], ...]:
    """The shapes the population runs against: the map, and the map indented.

    The second shape is the #11415 arm: a mutator or a rule that can only reach
    column-0 rows reports UNTESTABLE (or misses) on it, which is exactly how the
    hole survived 21 column-0-only mutations.

    ⛔ The shapes are the SAME DOCUMENT, so their BASELINE verdicts must be equal,
    and `self_test` checks that before it trusts either run (fresh review of PR
    #12016, P2). Without that comparison the second shape fails OPEN for an
    arm that fires only on column-0 rows: the arm's regressions become part of the
    indented copy's BASELINE, and every `"violation"`-expecting mutation is then
    satisfied by a violation the baseline already carried. Measured: reverting the
    R5 presence arm to `line.startswith("|")` left `--self-test` green on both
    shapes while `check()` red `R5 <seam>: missing from the §5/§6 crosswalk` on an
    indented map — a guard that cannot see the class it was added for.
    """
    return (
        ("shipped map", text, True),
        (f"rows indented {SELF_TEST_INDENT} spaces", _indented_tables(text), False),
    )


#: The indentation the SECOND self-test pass applies to every table row. Within
#: `MAX_TABLE_INDENT`, so the copy is the same map a reader sees (#11415 measured
#: that it is not the same map to a column-0 parser) — and non-zero, so a mutator
#: or an arm that only reaches column-0 rows is visible.
SELF_TEST_INDENT = 2


def _indented_tables(text: str, spaces: int = SELF_TEST_INDENT) -> str:
    """The same map with every table row at EXACTLY ``spaces`` leading spaces.

    ⛔ NORMALISE, then indent — do not simply prepend. Prepending to a document that
    already has rows at 1-3 spaces (the population `#11415` extends grading to) put
    those rows at 4-5, where `_is_table_line` is false: the copy lost its surface
    table, every mutator targeting those rows raised AssertionError, and
    `--self-test` exited 1 as a wall of UNTESTABLE on a document `check()` grades
    CLEAN — a false RED on the required `docs` job, for exactly the shape the fix
    exists for (fresh review of PR #12016, P2; measured: one row at 2 spaces gave
    `7/44 case(s) UNTESTABLE`, every row at 2 spaces gave `19/44` plus a
    `1/44 NOT caught`). The copy is therefore the same map a reader sees, whatever
    the source indentation, and it stays inside `MAX_TABLE_INDENT`.
    """
    indent = " " * spaces
    return "".join(
        (indent + line.lstrip(" ")) if _is_table_line(line) else line
        for line in text.splitlines(keepends=True)
    )


def _run_mutation_pass(text: str, label: str, *, verbose: bool) -> tuple[int, int]:
    """Run the whole population against ONE text; return (failures, untestable).

    Split out so `self_test` can run it twice — the shipped map and an indented
    copy (#11415). A harness whose mutators, or whose arms, only reach column-0
    rows reports UNTESTABLE on the second pass; that is the defect this pass exists
    to keep closed, and measuring it is the only way to keep it closed.
    """
    failures = 0
    untestable = 0
    # The verdict the SHIPPED document already earns. A "clean"-expected case is a
    # NEGATIVE control — it asserts the mutation does not CHANGE that verdict — so the
    # baseline is what it is compared against. Compared against an ABSOLUTE "clean",
    # every unrelated violation in the map made the control FAIL and the summary accuse
    # the checker of being a rubber stamp on a map that merely carried a violation
    # (measured: one `R2 SF10` violation produced `1/17 NOT caught`), which is the false
    # diagnosis this arm exists to avoid.
    try:
        baseline_violations: list[str] = check(text)[0]
    except Refusal:
        baseline_violations = []
    for name, mutate, expected in SELF_TEST_MUTATIONS:
        try:
            mutated = mutate(text)  # type: ignore[operator]
        except AssertionError as exc:
            # A mutation that could not be APPLIED is not the same finding as one
            # that was applied and not caught: reporting it as "the checker is a
            # rubber stamp" misdiagnoses a correct document whose row was merely
            # reformatted (measured — a reworded §4 heading did exactly that).
            print(f"  UNTESTABLE [{label}] {name}: {exc}", file=sys.stderr)
            untestable += 1
            continue
        violations: list[str] | None = None
        try:
            violations, _stats = check(mutated)
            actual = "violation" if violations else "clean"
            detail = violations[0] if violations else "no rule fired"
        except Refusal as exc:
            actual, detail = "refusal", str(exc)
        if expected == "clean":
            # NEGATIVE control: the verdict must be UNCHANGED, not globally clean.
            ok = violations is not None and violations == baseline_violations
            if ok:
                detail = (
                    f"verdict unchanged — {len(violations or [])} violation(s) before and after"
                )
            else:
                detail = f"the mutation changed the verdict — {detail}"
        else:
            ok = actual == expected
        if ok:
            if verbose:
                print(f"  ok   {name} → {actual} ({detail})")
        else:
            print(
                f"  FAIL [{label}] {name} → expected {expected}, got {actual} ({detail})",
                file=sys.stderr,
            )
            failures += 1
    return failures, untestable


def self_test(text: str) -> int:
    """Prove the checker is not a rubber stamp.

    Output shape: the SHIPPED-MAP shape prints one line per mutation; the second
    shape runs quietly and prints only its summary line (failures go to stderr in
    both). TWO shapes (#11415): the shipped map, and the same map with every table
    row at `SELF_TEST_INDENT` spaces (`_indented_tables`). The second is what
    catches a harness that can only express column-0 rows — the reason all 21
    pre-fix mutations missed an indentation-blind parser — and a checker that
    grades an indented row differently from a flush one.
    """
    if len(SELF_TEST_MUTATIONS) < MIN_SELF_TEST_MUTATIONS:
        print(
            f"self-test: {len(SELF_TEST_MUTATIONS)} mutation(s) registered, at least "
            f"{MIN_SELF_TEST_MUTATIONS} required — a shrunk mutation population proves nothing",
            file=sys.stderr,
        )
        return 1
    # ⛔ The indented shape must be a FIXED POINT, and that is checked rather than
    # assumed (fresh review of PR #12016, P2): a version that PREPENDED spaces moved
    # rows that already sat at 1-3 spaces out to 4-5, where they are no longer rows,
    # so the second shape silently lost its surface table and reported a wall of
    # UNTESTABLE against a document `check()` grades CLEAN. Idempotence is exactly
    # the property that failure breaks, and it costs one comparison.
    once = _indented_tables(text)
    if _indented_tables(once) != once:
        print(
            "self-test: the indented shape is NOT idempotent — applying it twice "
            "changes the document, so a map that is already indented would lose "
            "rows from the second pass and red the required `docs` job on a "
            "correct map",
            file=sys.stderr,
        )
        return 1
    passes = self_test_passes(text)
    if len(passes) < MIN_SELF_TEST_PASSES:
        print(
            f"self-test: {len(passes)} shape(s) exercised, at least "
            f"{MIN_SELF_TEST_PASSES} required — a single-shape run cannot see an "
            "indentation-blind arm or mutator",
            file=sys.stderr,
        )
        return 1

    def _baseline(candidate: str) -> list[str] | None:
        try:
            return check(candidate)[0]
        except Refusal:
            return None

    # ⛔ Every shape is the SAME document, so it must earn the SAME baseline verdict.
    # This is the arm that catches an indentation-blind RULE (as opposed to a
    # mutator): such a rule adds violations to the indented copy's baseline, where
    # every "violation"-expected mutation would otherwise be satisfied by them.
    shipped_baseline = _baseline(text)
    total = len(SELF_TEST_MUTATIONS)
    failures = 0
    untestable = 0
    for label, candidate, verbose in passes:
        candidate_baseline = _baseline(candidate)
        if candidate_baseline != shipped_baseline:
            print(
                f"self-test: the {label} shape grades "
                f"{len(candidate_baseline or [])} violation(s) where the shipped map "
                f"grades {len(shipped_baseline or [])} — the shapes are the SAME "
                "document, so a rule that reaches only column-0 rows is firing here "
                "(and every mutation's expected violation would be satisfied by that "
                "baseline instead of by the mutation)",
                file=sys.stderr,
            )
            failures += 1
            continue
        pass_failures, pass_untestable = _run_mutation_pass(candidate, label, verbose=verbose)
        failures += pass_failures
        untestable += pass_untestable
        if pass_failures or pass_untestable:
            continue
        print(
            f"self-test: {total}/{total} mutation(s) behaved as designed on the {label}"
        )
    denominator = total * len(passes)
    if failures:
        print(
            f"self-test: {failures}/{denominator} mutation(s) NOT caught — "
            "the checker is a rubber stamp",
            file=sys.stderr,
        )
    if untestable:
        print(
            f"self-test: {untestable}/{denominator} case(s) UNTESTABLE — a mutation "
            "target moved, so those rules were NOT exercised by this run",
            file=sys.stderr,
        )
    if failures or untestable:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check epic #5090's integration-surface map against its own rules.",
    )
    parser.add_argument(
        "--doc",
        type=Path,
        default=DEFAULT_DOC,
        help=f"surface map to check (default: {DEFAULT_DOC.relative_to(REPO_ROOT)})",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="apply targeted mutations to the map and require each to be caught",
    )
    args = parser.parse_args(argv)

    try:
        text = read_doc(args.doc)
    except Refusal as exc:
        if args.json:
            print(json.dumps({"ok": False, "refused": str(exc)}))
        else:
            print(f"test-design-map-check: REFUSED — {exc}", file=sys.stderr)
        return 2

    if args.self_test:
        return self_test(text)

    try:
        violations, stats = check(text)
    except Refusal as exc:
        if args.json:
            print(json.dumps({"ok": False, "refused": str(exc)}))
        else:
            print(f"test-design-map-check: REFUSED — {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({"ok": not violations, "stats": stats, "violations": violations}))
        return 0 if not violations else 1

    print(
        "test-design-map-check: "
        f"{stats['surfaces']} surface(s) · "
        f"{stats['capabilities']} capabilit(y/ies) · "
        f"{stats['children']} child(ren) · "
        f"{stats['plan_seams']}/{len(PLAN_SEAMS)} plan seam(s) crosswalked · "
        f"layers={','.join(stats['layers'])}"
    )
    if violations:
        for violation in violations:
            print(f"  VIOLATION {violation}", file=sys.stderr)
        print(
            f"test-design-map-check: {len(violations)} violation(s) — the map is not a map",
            file=sys.stderr,
        )
        return 1
    print("test-design-map-check: CLEAN — every rule holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
