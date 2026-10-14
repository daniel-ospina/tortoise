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

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLAN = REPO / "docs" / "plans" / "2026-10-09-5362-retroactive-correction.md"

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
