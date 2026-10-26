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

**FAILS IF** §10's replicated-run table is edited (any digit — measured: changing
raw's pooled loss `52.0%` to `53.0%` reds this test), deleted (the heading is
named in the failure), or moves away from what the scorer emits (an instrument
change with no write-back). It does not pin prose, formatting outside those two
tables, or the run-1 block — run 1's receipts were never retained, so its numbers
are a record and not re-derivable (`#9738`).

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


def _pct(rate: float) -> str:
    """The doc's own rendering: one decimal place, always."""
    return f"{rate * 100:.1f}%"


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

    for arm in ("raw", "narrative"):
        agg = score["aggregate"][arm]
        pr = _pooled(score, arm)
        expected = [
            f"{_pct(agg['loss_rate'])} ({agg['loss']}/{agg['n']}; "
            f"{agg['absent']} absent, {agg['flattened']} flattened)",
            _pct(agg["distortion_rate"]),
            f"{_pct(pr['recalled'] / pr['planted_units'])} "
            f"({pr['recalled']}/{pr['planted_units']})",
            f"{_pct(pr['claims_hit'] / pr['claims_emitted'])} "
            f"({pr['claims_hit']}/{pr['claims_emitted']})",
            str(pr["claims_emitted"]),
        ]
        got = _row(block, rf"\*\*{arm}\*\*")
        assert got == expected, (
            f"§10's {arm} row no longer publishes what the falsifier produces — "
            "the epic's E5 write-back and every figure quoted from it are now "
            f"unsourced.\n  §10:    {got}\n  scorer: {expected}\n"
            "Re-derive the table from a fresh run (and say so in the row's "
            "note); do not edit the pin")

    # The decision input: the holdout Δloss, which is what the registered rule
    # reads. Published as its own row in the split table below the arms table.
    holdout = score["decision_input"]["branches"]["holdout"]
    expected_holdout = [
        str(score["splits"]["aggregate"]["holdout"]["raw"]["n"]),
        f"{score['splits']['aggregate']['holdout']['raw']['loss']} "
        f"({score['splits']['aggregate']['holdout']['raw']['loss_rate'] * 100:g}%)",
        f"{score['splits']['aggregate']['holdout']['narrative']['loss']} "
        f"({score['splits']['aggregate']['holdout']['narrative']['loss_rate'] * 100:g}%)",
        f"+{holdout['delta_loss_pp']:.1f} pp",
    ]
    # `_row` drops the label cell; the row's cells are [subset, n, raw,
    # narrative, delta, ...] — the doc renders a whole-percent loss as `6 (60%)`.
    got_holdout = _row(block, r"\*\*holdout\*\*\s*\(decision input\)")[1:5]
    assert got_holdout == expected_holdout, (
        "§10's holdout decision-input row no longer publishes the Δloss the "
        f"registered rule reads.\n  §10:    {got_holdout}\n  scorer: "
        f"{expected_holdout}")
