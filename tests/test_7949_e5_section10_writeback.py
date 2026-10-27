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
green), the holdout `(decision input)`
row's subset label, n / both losses / **Δloss and its Δprecision / Δrecall legs**
(the `#10108` review measured the leg gap: `+0.8` retyped to `+8.0` stayed GREEN
while publishing a branch-1 firing the run does not have), the
`#### ⚠️ REPLICATED 2026-10-18` heading itself
(deleted or renamed reds, named in the failure), a duplicated or deleted table row
(exactly-one-match), or the scorer's own thresholds (an instrument change with no
write-back).

It does NOT pin the split table's `tuning` / `bound` / `unbound` rows, the prose
beside the tables, or the run-1 block — run 1's receipts were never retained, so
its numbers are a record and not re-derivable (`#9738`). The unpinned split cells
are a known gap, stated as one: measured, editing the `tuning` row's `5 (33%)` to
`99%` leaves this test GREEN.

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


#: The doc's `<loss> (<rate>%)` loss cell (`6 (60%)`) — the holdout row's form.
_LOSS_CELL = re.compile(r"^(\d+)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)$")


def _loss_cell(cell: str, *, row: str) -> tuple[int, float]:
    """Read a `<loss> (<rate>%)` cell as `(loss, rate-in-percent)`.

    Compared as NUMBERS, never as text. The document renders the holdout row's
    rates in whole percent (`6 (60%)`), while any formatter here (`:g`) renders
    two-thirds as `66.6667` — so a TEXT compare would red a re-publication that is
    CORRECT in the document's own format, and the tempting repair for that red is
    to edit §10 to fit the test instead of the test to fit the document. The rate
    is compared on the document's own one-decimal grid, so `60`, `60.0` and
    `60.00` all read the same and 66.6667 matches `66.7`.
    """
    match = _LOSS_CELL.match(cell.strip())
    assert match, (
        f"§10's {row} cell {cell!r} is not `<loss> (<rate>%)` — the row can no "
        "longer be read, so this pin would be comparing nothing")
    return int(match.group(1)), float(match.group(2))


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

    Compared as a NUMBER, never as text — the same reason `_loss_cell` is (`#10108`
    review P3). The document renders `52.0%`; a re-publication that writes `52%` or
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

    # The decision input: the holdout Δloss, which is what the registered rule
    # reads. Published as its own row in the split table below the arms table.
    #
    # The subset LABEL is pinned too (`#10108` review): the row's figures are the
    # POOLED `all qualifiers` ones, while the `bound` row below it carries different
    # numbers (3/6, 4/6). Comparing the cells from `n` onward left the label free, so
    # the row could be relabelled `bound` and stay GREEN while publishing the pooled
    # figures under it — on the one row the decision rule reads.
    holdout = score["decision_input"]["branches"]["holdout"]
    agg_holdout = score["splits"]["aggregate"]["holdout"]
    got_holdout = _row(block, r"\*\*holdout\*\*\s*\(decision input\)")
    assert got_holdout[0] == "all qualifiers", (
        "§10's holdout decision-input row no longer publishes the POOLED figures: "
        f"its subset cell reads {got_holdout[0]!r}, expected 'all qualifiers'. The "
        "`bound` / `unbound` rows carry different numbers, so a relabelled row "
        "would publish the pooled figures under the wrong subset.")
    assert got_holdout[1] == str(agg_holdout["raw"]["n"]), (
        f"§10's holdout row publishes n={got_holdout[1]!r}; the registered "
        f"falsifier ran n={agg_holdout['raw']['n']}")
    for cell, branch in ((got_holdout[2], "raw"), (got_holdout[3], "narrative")):
        loss, rate = _loss_cell(cell, row="holdout")
        assert loss == agg_holdout[branch]["loss"], (
            f"§10's holdout {branch} cell {cell!r} publishes loss={loss}; the "
            f"registered falsifier produced {agg_holdout[branch]['loss']}")
        assert abs(rate - agg_holdout[branch]["loss_rate"] * 100) <= 0.05, (
            f"§10's holdout {branch} cell {cell!r} publishes {rate}%; the "
            f"registered falsifier produced "
            f"{agg_holdout[branch]['loss_rate'] * 100:.4g}%")
    # Δloss: PARSED, never compared as text (`#10108` review P2). The document
    # renders this column both ways (`+10.0 pp` here, `0 pp` on the `unbound` row
    # at `EXTRACTOR-V4-ARCHITECTURE.md:1332`), so a text compare reds a
    # re-publication that is CORRECT in the document's own format — the same trap
    # the comment above names — and `f"+{...:.1f} pp"` additionally renders a
    # negative Δ as `+-12.3 pp`, which can never match.
    delta_loss = _pp(got_holdout[4], row="holdout Δloss")
    assert abs(delta_loss - holdout["delta_loss_pp"]) <= 0.05, (
        "§10's holdout decision-input row no longer publishes the Δloss the "
        f"registered rule reads.\n  §10:    {got_holdout[4]}\n  scorer: "
        f"{holdout['delta_loss_pp']:+.1f} pp")

    # …and its LAST cell, which was UNPINNED until the `#10108` review (P2). The pin
    # stopped at `[4]` (Δloss), so `Δprecision +0.8` could be retyped `+8.0` and stay
    # GREEN — publishing a branch-1 FIRING the run does not have, in the one cell
    # §10's own prose reads (`EXTRACTOR-V4-ARCHITECTURE.md:1338-1340`: *"the holdout's
    # (+0.8) is inside it"*, sc. the registered ±5 tolerance; `+8.0` is outside it).
    # The registered rule consumes all three legs (`score_qualifiers.py:760-761`
    # `evaluate_branches(hold["delta_loss_pp"], hold["delta_precision_pp"],
    # hold["delta_recall_pp"], …)`), so an unpinned leg is an unpinned verdict.
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
