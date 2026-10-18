"""#7818 — the #5362 plan's recorded read-path residual vs the shipped contract.

The #5362 plan (``docs/plans/2026-10-09-5362-retroactive-correction.md``)
records the ``restore_point_at`` read-path in the places a later lane consults.
#7818 changed that read path — the method is now ``get_historical_knowledge``,
every reply carries ``status`` (``found`` | ``ambiguous`` | ``absent``) and a
boolean ``found``, and the ``ambiguous`` branch sets ``found=True`` with
``valid_point`` absent. The plan's §3.3 prose, §7 surface-map row 6 and §12 R10
row were corrected with it, but the residual-risk register (RES-1) and the
"Explicitly OUT of scope" list kept the *pre*-#7818 claim that the branch
*omits* ``found`` — so the plan simultaneously asserted and denied the same
gap. RES-1 is a decision artifact (an accepted residual with polarity), so a
later lane would read the omission as live and could re-open #7818 or route
work around a gap that is gone.

Hermetic (one file read, no fixture, no DB), so it can sit in the always-run
``tier1`` set and red on a docs-only edit to the plan — the change class the
drift arrives on.

Class-B test doctrine:
  (1) *What value makes this test fail?*  The pre-#7818 plan: a RES-1 row whose
      polarity cell still says the branch omits ``found`` and whose verdict is
      "Yes, for ``.get("found")`` consumers", plus the out-of-scope clause that
      lists the omission as unfixed.
  (2) *Does the test reach it?*  Yes — the committed plan is read, the RES-1
      row is sliced by its row key (so an unrelated mention elsewhere cannot
      satisfy it), and the stale out-of-scope phrase is asserted absent.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAN = REPO / "docs" / "plans" / "2026-10-09-5362-retroactive-correction.md"

# Any `file.py:NNN` citation — see the rot test at the bottom of this file.
_LINE_CITATION = re.compile(r"\S*\.py:\d+")

# The pre-#7818 claims. Both are asserted ABSENT; each is present verbatim in
# the plan before the correction and must not come back.
_STALE_OMISSION = "branch omits `found`"
_STALE_OUT_OF_SCOPE = "`found`-omission in the ambiguous branch"


def test_5362_plan_res_1_records_the_shipped_reply_contract() -> None:
    """RES-1 no longer tells a #5362 lane the ambiguous branch omits ``found``.

    (1) *What value makes this test fail?*  The pre-#7818 RES-1 row
        (``branch omits `found` `` / ``Yes, for .get("found") consumers``) and
        the out-of-scope clause listing the omission as unfixed.
    (2) *Does the test reach it?*  Yes — the plan is read and the RES-1 row is
        sliced by its row key, so the assertion cannot be satisfied by a
        mention elsewhere in the file.
    """
    plan = PLAN.read_text(encoding="utf-8")
    rows = [ln for ln in plan.splitlines() if ln.startswith("| RES-1 ")]
    assert len(rows) == 1, (
        f"expected exactly one RES-1 row in the #5362 plan, found {len(rows)}")
    row = rows[0]
    assert _STALE_OMISSION not in row, (
        "RES-1 still tells a #5362 lane the ambiguous branch omits `found`; "
        "post-#7818 the branch sets `found=True` with `valid_point` absent")
    assert "found=True" in row and "#7818" in row, (
        "RES-1 must record the post-#7818 contract (`found=True` on the "
        "ambiguous reply) and name the issue that shipped it")

    assert _STALE_OUT_OF_SCOPE not in plan, (
        "the #5362 plan still lists the `found`-omission as explicitly OUT of "
        "scope; #7818 fixed it, so the deferral is false")


def test_5362_plan_r2_prices_the_half_open_flip() -> None:
    """R2 carries the MEASURED cost of the flip, not only a re-verification appeal.

    R2 is the row the owner weighs when answering §13 Q3 (schedule the half-open
    representation as its own issue). Its first sentence used to price the flip
    by appeal — "every window consumer must all be re-verified" — which reads as
    an unbounded audit; measured, the flip reds exactly the tests that pin the
    closed contract and nothing else, and what it actually needs is the
    well-formed-empty marker it already names. The same cell also cited
    ``_inverted`` at ``sdk.py:19221``, a line the #8337 rename left behind.

    (1) *What value makes this test fail?*  The pre-measurement R2 row: no
        "Measured flip cost" clause, no red/green counts, no zero-length
        read-path shapes, and the stale `sdk.py:19221` citation — each of which
        lets the owner price the flip on an unbounded-blast-radius assumption.
    (2) *Does the test reach it?*  Yes — the row is sliced by its row key, so an
        unrelated mention elsewhere in the plan cannot satisfy these anchors.
    """
    plan = PLAN.read_text(encoding="utf-8")
    rows = [ln for ln in plan.splitlines() if ln.startswith("| **R2** |")]
    assert len(rows) == 1, (
        f"expected exactly one R2 row in the #5362 plan, found {len(rows)}")
    row = rows[0]
    assert "Measured flip cost" in row, (
        "R2 must carry the measured cost of the flip (the owner weighs this row)")
    assert "5 tests in 2 files" in row and (
        "130 tests in 5 adjacent files stay green") in row, (
        "R2 must record both halves of the measurement — the reds AND the "
        "adjacent files that do not move")
    assert "sdk.py:19221" not in row, (
        "the R2 cell still cites `_inverted` at a line the #8337 rename "
        "invalidated; cite the symbol instead")
    assert "well-formed-EMPTY" in row, (
        "the priced flip names the marker it needs WITH it (R2's own terms)")


def test_5362_plan_r2_cites_the_read_path_by_symbol_never_by_line() -> None:
    """R2 names the read-path surfaces; it carries no `file.py:NNN` citation.

    R2 is the row the owner weighs, and every line number it used to carry had
    rotted: `_inverted` was cited at `sdk.py:19221` (moved to `:25167` by the
    #8337 rename, then to `:25458` when `origin/main` #8487 added 291 lines to
    the same file), `retrieval._validity_marker` at `retrieval.py:865` (now
    `:905`), `assembly` at `:1014` (no longer that marker), `time_aware` at
    `:277`/`:316` (now `_as_date`/`is_stale_entry`), and `audit` at `:665`
    (still the inverted check, by luck). Six pointers, five wrong — and a lane
    that trusts one lands in unrelated code. Symbols do not rot; the numbers do.

    (1) *What value makes this test fail?*  Re-adding any `file.py:NNN` to the
        R2 row — e.g. the pre-fix `sdk.py:25167`, `retrieval.py:865`,
        `:1014`, `:277`/`:316`, `:665` — or restoring the bare `:NNN`
        continuation form the row used for sibling files.
    (2) *Does the test reach it?*  Yes — the pattern is applied to the R2 row
        sliced by its row key, so a citation added anywhere in the cell reds.
    """
    plan = PLAN.read_text(encoding="utf-8")
    rows = [ln for ln in plan.splitlines() if ln.startswith("| **R2** |")]
    assert len(rows) == 1, (
        f"expected exactly one R2 row in the #5362 plan, found {len(rows)}")
    row = rows[0]

    stale = _LINE_CITATION.findall(row)
    assert stale == [], (
        "R2 cites code by line number; these go stale on the next rename or "
        f"merge of the same file: {stale}. Cite the symbol instead")
    # the bare `:NNN` continuation form is how the sibling files were cited,
    # and it rots identically while evading the `file.py:NNN` pattern.
    assert not re.search(r"`:\d+`", row), (
        "R2 still carries a bare `:NNN` continuation citation — a reader cannot "
        "tell which file it belongs to, nor whether the line is current")
