"""`#7949` E5 — the falsifier is run on OUR corpus, and §10 publishes ITS numbers.

E5's criterion, verbatim from the epic body:

    **E5** | **The premise is measured, not assumed** | the raw-vs-narrative
    falsifier is run on OUR corpus and written back into §10 (`#7947`) | v4
    ships on an unmeasured premise

Both halves were done by hand, and until this test NOTHING in the tree failed if
either came apart:

* **Half one — the run.** Pinned by
  `tests/eval/write_path/test_score_qualifiers.py::
  test_the_committed_score_artifact_regenerates_byte_identically`: a fresh score
  over the committed arm receipts still reproduces the committed artifact.
* **Half two — the write-back.** `#7949`'s verdict rows and `#7947`'s closure both
  rest on §10 *carrying* that measurement, and every `§10` figure was published by
  hand. No test read the architecture doc for a number, so the table could be
  retyped, edited one digit, or deleted and the whole gate suite stayed green —
  the epic would then be citing a measurement the design document no longer
  records. That is the failure this pin prevents, and it is the one the capstone
  is supposed to be able to check (`#7949`: *"a child closing is a claim; this
  epic is where the claim is checked"*).

**One behaviour:** the figures §10 publishes for the replicated run are the
figures the registered falsifier produces when it is run now.

**FAILS IF** the cells §10 publishes for the replicated run move: each arm's
pooled loss / absent / flattened, distortion, recall, precision and claims emitted
(measured: raw's `52.0%` to `53.0%` reds this), the arms table's column HEADER
(measured: swapping the `recall` / `precision` labels reds — without it every cell
below is read by POSITION, so a relabelled column would publish the wrong figure
green), the SPLIT table's column HEADER and every one of its six numeric rows
(`#10108` review round 6: with only the holdout decision-input row pinned, swapping
`raw loss` / `narrative loss` left this pin GREEN while §10 published the decision
input INVERTED beside its own `**+10.0 pp**` cell, and `holdout | bound`'s
`**+16.7 pp**` — the one figure §10 names as *"past branch 1's trigger"* — could be
retyped `**+99.9 pp**` and stay GREEN), the holdout `(decision input)`
row's subset label, n / both losses / **Δloss and its Δprecision / Δrecall legs**
(the `#10108` review measured the leg gap: `+0.8` retyped to `+8.0` stayed GREEN
while publishing a branch-1 firing the run does not have), the
`#### ⚠️ REPLICATED 2026-10-18` heading itself
(deleted or renamed reds, named in the failure), a SECOND
`#### ⚠️ REPLICATED 2026-10-18` subsection above or below the real one
(`_block()` reads the FIRST occurrence, so a duplicate is invisible without an
explicit count — measured: a copy placed BELOW the original with its
`holdout | bound` Δ retyped `+99.9 pp` stayed GREEN until the count was added),
a DUPLICATED, deleted,
column-misaligned or fork-ordered table row, a second `| split |` table placed
above the real one, a printed rate dropped from one of the four split rows that
carry one, or the scorer's own thresholds (an instrument change with no
write-back).

It does NOT pin the prose beside the tables, the split table's `other` column
(except the decision-input row's Δ legs, which the registered rule reads), or the
run-1 block — run 1's receipts were never retained, so its numbers are a record and
not re-derivable (`#9738`). That gap is stated, not hidden: measured, editing the
`tuning` row's `other` cell leaves this test GREEN, and so does editing the prose
sentence that reads the holdout's `(+0.8)`.

Every figure is compared as a NUMBER on the precision the document printed
(`_lup_tol`: half a unit in the last place), so a correct re-rendering (`52%` for
`52.0%`, `+16.67 pp` for `+16.7 pp`, `0.0 pp` for `0 pp`) is GREEN while a MOVED
figure reds.

Deterministic and hermetic: the scorer is stdlib-only over the committed arm
receipts and this tree's gold. No DB, no network, no model.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.eval.write_path import score_qualifiers as sq

_REPO = Path(__file__).resolve().parent.parent
DOC = _REPO / "docs" / "architecture" / "EXTRACTOR-V4-ARCHITECTURE.md"
ARMS_DIR = _REPO / "tests" / "eval" / "write_path" / "receipts" / "7947-arms-2026-10-18"
SESSIONS = (*sq.TUNING_SESSIONS, *sq.HOLDOUT_SESSIONS)

#: The subsection whose table is the write-back. §10 also carries RUN 1's table,
#: whose numbers are a record (its receipts are gone) and must not be confused
#: with the replicated run's — the heading is the unit, not "the first raw row".
_REPLICATED = "#### ⚠️ REPLICATED 2026-10-18"


#: A bare rate cell (`20.0%`, `52%`) — the unit the document prints on its own
#: one-decimal grid.
_RATE_CELL = re.compile(r"^(\d+(?:\.\d+)?)\s*%$")


#: The holdout row's LAST cell: the decision rule's precision/recall legs
#: (`Δprecision +0.8, Δrecall +3.0`). A separate unit from the loss cells, because
#: `evaluate_branches` reads all three legs (`score_qualifiers.py:760-761`) — a pin
#: that stops before this cell leaves the verdict's other two inputs free.
_DELTA_CELL = re.compile(
    r"^Δprecision\s*([+-]?\d+(?:\.\d+)?)\s*,\s*Δrecall\s*([+-]?\d+(?:\.\d+)?)$")


def _rate(cell: str, *, row: str) -> float:
    """Read a `<rate>%` cell as a percentage number.

    Compared as a NUMBER, never as text — the same reason `_split_loss_cell` is
    (`#10108` review P3). The document renders `52.0%`; a re-publication that writes
    `52%` or
    `52.00%` is numerically the SAME figure and must not red. What must red is a
    figure that MOVED. The accepted forms are the document's own grid (a plain
    decimal percentage); an exponent form such as `5.2e+01%` is REJECTED on
    purpose, because this document never renders one and accepting it would
    tolerate a format nothing produces.
    """
    match = _RATE_CELL.match(cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not `<rate>%` — the row can no longer be "
        "read, so this pin would be comparing nothing")
    return float(match.group(1))


#: A loss cell with its breakdown: `52.0% (13/25; 10 absent, 3 flattened)`. The
#: separators are the document's, and `13 of 25` is accepted for the same reason
#: `_rate` accepts `52%`: a correct re-rendering is not a moved number.
_LOSS_DETAIL = re.compile(
    r"^(\d+(?:\.\d+)?)\s*%\s*"
    r"\(\s*(\d+)\s*(?:/|of)\s*(\d+)\s*;\s*"
    r"(\d+)\s*absent\s*,\s*(\d+)\s*flattened\s*\)$")


def _loss_detail(cell: str, *, row: str) -> tuple[float, int, int, int, int]:
    """Read `<rate>% (<loss>/<n>; <absent> absent, <flattened> flattened)`.

    Returns `(rate-in-percent, loss, n, absent, flattened)` — every figure the
    document publishes for the arm, as numbers.
    """
    match = _LOSS_DETAIL.match(cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not "
        "`<rate>% (<loss>/<n>; <absent> absent, <flattened> flattened)` — the row "
        "can no longer be read, so this pin would be comparing nothing")
    return (float(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4)), int(match.group(5)))


#: A rate-with-ratio cell: `78.9% (71/90)`, or the document's `71 of 90`.
_RATIO_CELL = re.compile(
    r"^(\d+(?:\.\d+)?)\s*%\s*\(\s*(\d+)\s*(?:/|of)\s*(\d+)\s*\)$")


def _ratio_cell(cell: str, *, row: str) -> tuple[float, int, int]:
    """Read `<rate>% (<hit>/<total>)` as `(rate-in-percent, hit, total)`."""
    match = _RATIO_CELL.match(cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not `<rate>% (<hit>/<total>)` — the row can "
        "no longer be read, so this pin would be comparing nothing")
    return float(match.group(1)), int(match.group(2)), int(match.group(3))


def _block(doc: str, marker: str) -> str:
    """The text from `marker` to the next `####` heading (or the section's end)."""
    assert marker in doc, (
        f"§10 no longer carries the {marker!r} subsection — E5's write-back is "
        "gone. Re-publish the replicated run (and its table) before closing "
        "#7949; do not delete this pin")
    # …and it must be the ONLY one. `split(marker, 1)` reads the FIRST occurrence,
    # so a duplicated subsection placed BELOW the real one is never looked at:
    # a conflict resolution that keeps both sides of a whole subsection (the exact
    # shape `_split_table()` pins one level down) leaves §10 rendering TWO
    # replicated-run tables, the second free to publish a contradicting figure
    # while this pin validates the first. Nothing else in the tree reads §10 for a
    # number, and a duplicated table is not malformed — it is two valid tables
    # that both render — so no linter or renderer notices either (`#10108` review
    # P3, measured: the copy's `holdout | bound` Δ retyped `+99.9 pp` below the
    # original stayed GREEN before this assertion).
    assert doc.count(marker) == 1, (
        f"§10 carries {doc.count(marker)} {marker!r} subsections — only the "
        "FIRST would be read, so a second (or a forked) replicated-run table "
        "could publish different figures while this pin validated the other "
        "one. Exactly one is required")
    tail = doc.split(marker, 1)[1]
    return tail.split("\n####", 1)[0]


def _row(block: str, first_cell_pattern: str) -> list[str]:
    """The cells of the one table row whose first cell matches, `**` stripped."""
    found = re.findall(rf"^\|\s*{first_cell_pattern}\s*\|(.*)\|\s*$", block, re.M)
    assert len(found) == 1, (
        f"expected exactly one table row matching {first_cell_pattern!r} under "
        f"{_REPLICATED!r}, found {len(found)}")
    return [c.strip().replace("*", "") for c in found[0].split("|")]


#: §10's arms-table column header. Pinned because every arm cell below is compared
#: by POSITION (`got[2]` is recall, `got[3]` is precision), so a header EDIT that
#: swaps two labels would publish the wrong figure under the right number and stay
#: green — measured (`#10108` review P2): swapping `recall` / `precision` in the
#: header, deleting the header row, and renaming `qualifier loss` → `distortion` all
#: left the pin GREEN while the table said something the run does not.
_ARM_HEADER = ("qualifier loss", "distortion", "recall", "precision",
               "claims emitted")


def _header_cells(block: str) -> list[str]:
    """The cells of the arms table's header row (the one beginning `| arm |`)."""
    found = re.findall(r"^\|\s*arm\s*\|(.*)\|\s*$", block, re.M)
    assert len(found) == 1, (
        "expected exactly one `| arm | ... |` header row under "
        f"{_REPLICATED!r}, found {len(found)} — without it the cells below are "
        "read by POSITION and a relabelled column publishes the wrong figure")
    return [c.strip().replace("*", "") for c in found[0].split("|")]


#: A Δ cell in percentage points: `+10.0 pp`, `−13.3 pp`, `0 pp`. Parsed, never
#: compared as text — the document itself renders the same column as `+10.0 pp`
#: (holdout) and `0 pp` (unbound), so a text compare would red a re-publication
#: that is CORRECT in the document's own format (`#10108` review P2). The Unicode
#: minus is accepted because §10 already uses it in the tuning row.
_PP_CELL = re.compile(r"^([+\-\u2212]?\s*\d+(?:\.\d+)?)\s*pp$")


def _pp(cell: str, *, row: str) -> float:
    """Read a `±<n> pp` cell as a signed float, `−` (U+2212) as `-`."""
    match = _PP_CELL.match(cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not `<±n> pp` — the row can no longer be "
        "read, so this pin would be comparing nothing")
    return float(match.group(1).replace("\u2212", "-").replace(" ", ""))


#: The first number in a published cell, with its decimal digits — the precision the
#: document chose to print. A figure is correct if it rounds to the printed digits,
#: so the tolerance is half a unit in the last place: `67%` tolerates ±0.5, `+16.7 pp`
#: tolerates ±0.05. A fixed tolerance would either pass a moved figure or red a
#: correct rounding (4 of 6 prints as `67%`, not `66.7%`).
_FIRST_NUMBER = re.compile(r"[+\-\u2212]?\s*(\d+)(?:\.(\d+))?")


def _lup_tol(published: str) -> float:
    """Half a unit in the last place the document printed, plus float slop."""
    match = _FIRST_NUMBER.search(published)
    assert match, (
        f"{published!r} carries no number — the tolerance cannot be derived from "
        "a cell that prints no figure")
    return 0.5 * 10 ** (-len(match.group(2) or "")) + 1e-9


#: §10's SPLIT table header — pinned for the same reason as the arms header, and
#: measured the hard way (`#10108` review round 6): the rows below are compared by
#: POSITION, so swapping these two labels left this pin GREEN while §10 then
#: published the holdout decision input INVERTED (narrative 60% vs raw 70%, i.e. a
#: Δ of −10 pp) beside its own unchanged `**+10.0 pp**` cell — the document stating
#: the opposite of the run about the one metric the pre-registered rule reads.
_SPLIT_HEADER = ("split", "subset", "n", "raw loss", "narrative loss", "Δ",
                 "other")

#: The split rows that print a rate beside each loss today (`6 (60%)`, `4 (67%)`).
#: Required on these four, so DELETING a printed rate cannot narrow this pin in
#: silence (`#10108` review round 7: measured, stripping the parentheticals from
#: `holdout | bound` left it GREEN). The two `tuning` bound/unbound rows print bare
#: counts, so a rate there is accepted and compared if present, never demanded —
#: requiring its ABSENCE would red a future document that improved by printing it.
_RATE_ROWS = frozenset({("holdout", "all"), ("holdout", "bound"),
                        ("holdout", "unbound"), ("tuning", "all")})


def _split_table(block: str) -> list[list[str]]:
    """Every row of §10's split table, header first, `**` stripped.

    A dedicated reader rather than `_row()` because six rows share two first cells
    (`holdout`, `tuning`) — `_row()`'s exactly-one-match invariant is the right
    guard for the arms table and the wrong one here.
    """
    lines = block.splitlines()
    assert len(re.findall(r"^\|\s*split\s*\|", block, re.M)) == 1, (
        "§10 carries more than one `| split | ... |` table under "
        f"{_REPLICATED!r} — only the FIRST would be read, so a second (or a "
        "forked) table could publish different figures while this pin validated "
        "the other one. Exactly one is required")
    # `next()` rather than an `enumerate` loop: the index is needed AFTER the loop
    # (`lines[index:]` below), and a loop variable read only past the body is what
    # ruff's B007 refuses — it reddened the REQUIRED `agent-infra-ci / lint` check
    # on this very function (`819e0585f3`: "Loop control variable `index` not used
    # within loop body", `ruff check .`, 2026-10-27T18:28:43Z). Behaviour is
    # identical: the first line matching the split-table header.
    try:
        index = next(i for i, line in enumerate(lines)
                     if re.match(r"^\|\s*split\s*\|", line))
    except StopIteration:  # pragma: no cover — the count above already proved it
        raise AssertionError(
            "§10 no longer carries the `| split | ... |` table under "
            f"{_REPLICATED!r} — the table the decision input is published in is "
            "gone, so this pin would be comparing nothing") from None
    rows = []
    for line in lines[index:]:
        stripped = line.strip()
        if not stripped.startswith("|"):
            break
        # Strip ONE leading and ONE trailing pipe, never `str.strip('|')` — that
        # eats every trailing pipe, so a row whose last cell is empty and written
        # `||` (valid GFM, the same row as `| |`) would read as a cell SHORT and
        # red as "misaligned" (`#10108` review P3, measured).
        body = stripped[1:-1] if stripped.endswith("|") else stripped[1:]
        cells = [c.strip().replace("*", "") for c in body.split("|")]
        if all(c and set(c) <= set("-: ") for c in cells):
            continue  # the `|---|---|…` separator
        rows.append(cells)
    return rows


def _split_loss_cell(cell: str, *, row: str) -> tuple[int, float | None, float | None]:
    """Read a split-table loss cell: `3`, or `3 (50%)`.

    Returns `(loss, rate-percent-or-None, rate-tolerance-or-None)`. The rate is
    OPTIONAL at the PARSER level because the document prints it on some rows and
    not others (`holdout | bound` carries `4 (67%)`, `tuning | bound` carries a bare
    `2`) — a parser that demanded it would red a table the document publishes
    as-is. The CALLER then requires it on exactly the rows that print one today
    (`_RATE_ROWS`), so deleting a printed rate is not silently tolerated either.
    When it is printed, it is compared on the precision it is printed at.
    """
    match = re.match(r"^(\d+)(?:\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\))?$",
                     cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not `<loss>` or `<loss> (<rate>%)` — the "
        "row can no longer be read, so this pin would be comparing nothing")
    if match.group(2) is None:
        return int(match.group(1)), None, None
    return (int(match.group(1)), float(match.group(2)),
            _lup_tol(match.group(2)))


def _assert_split_table(block: str, score: dict) -> None:
    """Pin every NUMERIC cell of §10's split table against the fresh run.

    Six rows — `holdout` / `tuning` × `all qualifiers` / `bound` / `unbound` — whose
    n, both loss counts, the rates the document prints, and the Δ all come straight
    out of `score`. Pinned wholesale because the gap this closes was measured, not
    suspected (`#10108` review round 6): with only the holdout decision-input row
    pinned, `holdout | bound`'s `**+16.7 pp**` could be retyped `**+99.9 pp**` and
    stay GREEN — and `+16.7 pp` is the one figure §10 names as *"past branch 1's
    trigger"*, i.e. the single place the registered falsifier's trigger is crossed.

    The `other` column is NOT read here (it mixes a Δ-legs sentence with prose like
    *"past branch 1's trigger"*); the decision-input row's legs are pinned by
    `_DELTA_CELL` in the caller, and the rest is a stated gap, not a silent one.
    """
    rows = _split_table(block)
    assert rows and rows[0] == list(_SPLIT_HEADER), (
        f"§10's split table header is {rows[0] if rows else None!r}; the registered "
        f"table is {list(_SPLIT_HEADER)!r}. The cells below are read by POSITION, "
        "so a relabelled column publishes a different figure as green")
    # The ROW count is physical, and it is asserted SEPARATELY from the keyed count
    # below: `got` is a dict, so a DUPLICATED row collapses onto its key and the
    # corrupted original is simply discarded — measured (`#10108` review round 7):
    # `holdout | bound`'s Δ retyped `+99.9 pp` plus a re-appended correct row stayed
    # GREEN, and the same content graded RED with the rows in the other order, i.e.
    # the verdict depended on merge order. A conflict resolution that keeps both
    # sides of one row produces exactly that.
    assert len(rows) - 1 == 6, (
        f"§10's split table publishes {len(rows) - 1} data rows, expected 6 "
        "(holdout and tuning × all qualifiers / bound / unbound). A duplicated row "
        "is how one corrupted row hides behind a correct twin, so the count is "
        "physical, not keyed")
    got = {(r[0].split(" ")[0], r[1].split(" ")[0]): r for r in rows[1:]}
    assert len(got) == 6, (
        f"§10's split table keys {len(got)} rows, expected 6: {sorted(got)}")

    for split in ("holdout", "tuning"):
        aggregate = score["splits"]["aggregate"][split]
        anchors = score["splits"]["anchor_subset"][split]
        for key in ("all", "bound", "unbound"):
            row = got[(split, key)]
            label = f"{split} {key}"
            assert len(row) == len(_SPLIT_HEADER), (
                f"§10's {label} row publishes {len(row)} cells against a "
                f"{len(_SPLIT_HEADER)}-column header — the row no longer lines up "
                "with the table it sits in, so its cells are being read under the "
                f"wrong columns: {row!r}")
            if key == "all":
                raw, narrative = aggregate["raw"], aggregate["narrative"]
            else:
                raw, narrative = anchors["raw"][key], anchors["narrative"][key]

            assert row[2] == str(raw["n"]), (
                f"§10's {label} row publishes n={row[2]!r}; the registered "
                f"falsifier ran n={raw['n']}")
            for cell, branch in ((row[3], raw), (row[4], narrative)):
                loss, rate, tol = _split_loss_cell(cell, row=f"{label} {branch}")
                assert loss == branch["loss"], (
                    f"§10's {label} row publishes loss={loss} (cell {cell!r}); the "
                    f"registered falsifier produced {branch['loss']}")
                if (split, key) in _RATE_ROWS:
                    assert rate is not None, (
                        f"§10's {label} row no longer prints the rate beside the "
                        f"loss (cell {cell!r}) — the rate the document published "
                        "is a figure the run produced, and dropping it narrows "
                        "this pin silently")
                if rate is not None:
                    produced = branch["loss_rate"] * 100
                    assert abs(rate - produced) <= tol, (
                        f"§10's {label} row publishes {rate}% (cell {cell!r}); "
                        f"the registered falsifier produced {produced:.4g}%")

            delta = _pp(row[5], row=f"{label} Δ")
            produced = (narrative["loss_rate"] - raw["loss_rate"]) * 100
            assert abs(delta - produced) <= _lup_tol(row[5]), (
                f"§10's {label} row publishes Δ {row[5]!r}; the registered "
                f"falsifier produced {produced:+.4g} pp")


def _pooled(score: dict, arm: str) -> dict:
    """The arm's pooled precision/recall, summed over the per-session rows."""
    rows = [r for r in score["precision_recall"] if r["arm"] == arm]
    assert rows, f"no precision/recall rows for arm {arm!r}"
    return {
        "recalled": sum(r["recalled"] for r in rows),
        "planted_units": sum(r["planted_units"] for r in rows),
        "claims_hit": sum(r["claims_hit"] for r in rows),
        "claims_emitted": sum(r["claims_emitted"] for r in rows),
    }


def test_section10_publishes_the_numbers_the_registered_falsifier_produces(tmp_path):
    out = tmp_path / "score.json"
    assert sq.main(["--sessions", *SESSIONS,
                    "--arms-dir", str(ARMS_DIR),
                    "--out", str(out)]) == 0, "the falsifier did not run"
    score = json.loads(out.read_text())

    block = _block(DOC.read_text(), _REPLICATED)

    # The HEADER first: every arm cell below is compared by POSITION, so a header
    # edit that swaps two labels publishes the wrong figure under the right number
    # and would otherwise stay green (`#10108` review P2 — measured: swapping
    # `recall` / `precision`, deleting the header row, and renaming `qualifier
    # loss` → `distortion` all left the pin GREEN while the table said something
    # the run does not).
    assert _header_cells(block) == list(_ARM_HEADER), (
        f"§10's arms table header is {_header_cells(block)!r}; the registered "
        f"table is {list(_ARM_HEADER)!r}. The cells below are read by POSITION, "
        "so a relabelled column publishes a different figure as green — restore "
        "the column order (or re-derive the pin if the registration moved)")

    # The arms' cells are compared as NUMBERS on the document's own grid, not as
    # text (`#10108` review P3 — the same class the holdout row's `_loss_cell`
    # already fixes). A TEXT compare reds when a factually-correct re-publication
    # is formatted differently (`52%` for `52.0%`, `13 of 25` for `13/25`), and
    # the tempting repair for that red is to edit §10 to fit the test instead of
    # the test to fit the document. Every figure the document publishes is still
    # pinned: a cell that MOVES is a different number, and reds.
    for arm in ("raw", "narrative"):
        agg = score["aggregate"][arm]
        pr = _pooled(score, arm)
        got = _row(block, rf"\*\*{arm}\*\*")
        assert len(got) == 5, (
            f"§10's {arm} row publishes {len(got)} cells; the registered table "
            "has 5 (loss, distortion, recall, precision, claims emitted)")

        rate, loss, n, absent, flattened = _loss_detail(got[0], row=f"{arm} loss")
        for label, published, produced in (
                ("loss", loss, agg["loss"]),
                ("n", n, agg["n"]),
                ("absent", absent, agg["absent"]),
                ("flattened", flattened, agg["flattened"])):
            assert published == produced, (
                f"§10's {arm} row publishes {label}={published} (cell {got[0]!r}); "
                f"the registered falsifier produced {label}={produced}. The "
                "epic's E5 write-back and every figure quoted from it are now "
                "unsourced — re-derive the table from a fresh run (and say so in "
                "the row's note); do not edit the pin")
        assert abs(rate - agg["loss_rate"] * 100) <= 0.05, (
            f"§10's {arm} row publishes {rate}% loss; the registered falsifier "
            f"produced {agg['loss_rate'] * 100:.4g}% (cell {got[0]!r})")

        distortion = _rate(got[1], row=f"{arm} distortion")
        assert abs(distortion - agg["distortion_rate"] * 100) <= 0.05, (
            f"§10's {arm} row publishes {distortion}% distortion; the "
            f"registered falsifier produced "
            f"{agg['distortion_rate'] * 100:.4g}% (cell {got[1]!r})")

        for cell, hit_key, total_key, label in (
                (got[2], "recalled", "planted_units", "recall"),
                (got[3], "claims_hit", "claims_emitted", "precision")):
            pct, hit, total = _ratio_cell(cell, row=f"{arm} {label}")
            assert (hit, total) == (pr[hit_key], pr[total_key]), (
                f"§10's {arm} row publishes {label} {hit}/{total} (cell {cell!r}); "
                f"the registered falsifier produced {pr[hit_key]}/{pr[total_key]}")
            assert abs(pct - (pr[hit_key] / pr[total_key]) * 100) <= 0.05, (
                f"§10's {arm} row publishes {label} {pct}%; the registered "
                f"falsifier produced "
                f"{pr[hit_key] / pr[total_key] * 100:.4g}% (cell {cell!r})")

        assert got[4] == str(pr["claims_emitted"]), (
            f"§10's {arm} row publishes claims emitted {got[4]!r}; the registered "
            f"falsifier produced {pr['claims_emitted']}")

    # The SPLIT table — every numeric cell, both splits, all three subsets.
    _assert_split_table(block, score)

    # …and the ONE cell the sweep does not read: the holdout decision-input row's
    # `other` column, which carries the two precision/recall legs `evaluate_branches`
    # also consumes (`score_qualifiers.py:760-761`). It was UNPINNED until the
    # `#10108` review: `Δprecision +0.8` could be retyped `+8.0` and stay GREEN —
    # publishing a branch-1 FIRING the run does not have, in the cell §10's own
    # prose reads (`EXTRACTOR-V4-ARCHITECTURE.md:1340-1341`: *"the holdout's (+0.8)
    # is inside it"*, sc. the registered ±5 tolerance; `+8.0` is outside it).
    holdout = score["decision_input"]["branches"]["holdout"]
    got_holdout = _row(block, r"\*\*holdout\*\*\s*\(decision input\)")
    m = _DELTA_CELL.match(got_holdout[5])
    assert m, (
        "§10's holdout decision-input 'other' cell is no longer "
        f"`Δprecision <pp>, Δrecall <pp>`: {got_holdout[5]!r} — the row's other "
        "two decision inputs can no longer be read, so this pin would be "
        "comparing nothing")
    for published, key, label in (
            (m.group(1), "delta_precision_pp", "Δprecision"),
            (m.group(2), "delta_recall_pp", "Δrecall")):
        assert abs(float(published) - holdout[key]) <= 0.05, (
            f"§10's holdout row publishes {label} {published}; the registered "
            f"falsifier produced {holdout[key]:+.4g} — the decision input's "
            "precision/recall legs move `evaluate_branches`, so the row's "
            "verdict is no longer the one the run produced")
