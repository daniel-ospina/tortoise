"""#10852 review — the projection doc must not re-arm the "re-capture heals" premise.

WHY A DOC NEEDS A TEST. This PR measured that a pre-#10000 session `:Source` is
**NOT** repaired by a re-capture: `_materialize_session_source`'s only production
caller sits behind `capture_session`'s fresh-capture gate
(`if not session_existed or (retry_failed_capture and not no_provider)`), so
re-capturing an already-captured session id is the `#2104`/`#1727` no-op replay and
the stale `summary`/`_searchText` pair survives. FOUR artifacts said the opposite
and were corrected — the method docstring, the inline comment above the
`_searchText` SET, a test that was renamed off the claim, and the PR body. Then
`#10852`'s own diff added a FIFTH:
`docs/epics/2026-10-10-7879-postgres-record/06-cache-projection.md` told an
operator the legacy population repairs itself *"until it is re-captured (the
capture now overwrites the pair with the lexicon)"*. That is the actionable form
of the false premise: a reader who believes it skips the three routes that DO
clear the pair — a capture under a NEW `session_id`, a retry of a prior FAILED
capture, or a record-sourced re-projection for a node the projection CREATES —
and the legacy session stays un-indexed on the recovery door indefinitely.

WHY ITS OWN FILE, REGISTERED IN `tier1`. The guarded artifact is under `docs/`,
and a docs-only diff selects no surface and runs `tier1` only, so a pin placed in
`tests/test_10000_source_prose.py` (the `core` surface, graph-touching, one
throwaway FalkorDB per case) would SLEEP on exactly the diff that re-arms the
claim. Same shape and the same reason as `tests/test_10191_record_move_doc_pin.py`.
DB-free, fixture-free, network-free: it reads two files and asserts on text.

WHAT IT ASSERTS is the CORRECTION in both directions, in the doc AND in the module
docstring the doc is supposed to mirror, so the two cannot drift apart silently:

  (1) the retracted shapes are ABSENT from the "Costs, named rather than implied"
      section — the bullet list a reader consults for what the drop costs;
  (2) the mechanism is PRESENT — the no-op replay, `#1727`, and all three routes
      that actually clear the pair (deleting the bullet cannot pass this, which is
      what stops the cheap green of an empty section);
  (3) the doc's own "### The pin" list cites the `#10965` per-row legs it claims to
      state, and every `test_*` name it cites RESOLVES in the module it points at
      (`#7948`/`#10852`'s lesson: a hand-maintained prose reference rots and
      nothing notices);
  (4) `tortoise/cache_projection.py`'s module docstring — the artifact the doc
      paraphrases — states the same correction and none of the retracted shapes.

NON-VACUITY (measured, not asserted): all three of (1), (2) and (3) red against
the pre-fix document at `e13c6eeea7`; (4) was already green there, because the
code was corrected first and the doc was the artifact left behind.
"""
from __future__ import annotations

import re
from pathlib import Path

from tortoise import cache_projection

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs" / "epics" / "2026-10-10-7879-postgres-record"
       / "06-cache-projection.md")

#: The section of the doc that states what the drop costs — the bullet list an
#: operator reads before deciding whether their legacy sessions are fine.
COSTS_HEADING = "### Costs, named rather than implied"

#: The section that names the legs pinning the door.
PIN_HEADING = "### The pin"

#: Shapes a future lane re-introduces the retracted claim in. The first is the
#: pre-fix text VERBATIM (so this arm was RED at `e13c6eeea7`); the second is the
#: mechanism clause that followed it.
RETRACTED_SHAPES = (
    "until it is re-captured",
    "the capture now overwrites the pair",
)

#: The correction, as keywords rather than a sentence: a reworded but CORRECT
#: statement passes, a correct-looking one that loses the mechanism does not.
CORRECTION_KEYWORDS = ("does not repair", "no-op replay", "1727")

#: All three routes that actually clear a legacy pair. A reader who is told only
#: "a re-capture does not help" is still stuck; the doc has to say what does.
CLEARING_ROUTES = ("NEW `session_id`", "FAILED capture", "projection CREATES")

#: The `#10965` per-row legs this PR added. They must be NAMED here, because the
#: section's whole claim is that it states "the pin" — a list short by one arm
#: reads as coverage and delivers less.
REQUIRED_PINNED_LEGS = (
    "test_a_post_10000_session_row_keeps_the_field_its_fts_index_is_declared_on",
    "test_the_drop_is_decided_by_the_rows_own_provenance_not_by_the_column_name",
    "test_the_display_summary_stays_declined_and_the_residual_is_named",
    "test_the_reconciler_compares_the_restored_lexicon_on_a_post_10000_row",
)

#: The modules a cited `test_*` name may resolve in — the two files the pin
#: section points at, and nothing else.
PIN_MODULES = (
    ROOT / "tests" / "test_10853_session_source_prose.py",
    ROOT / "tests" / "test_10000_source_prose.py",
)


def _section(heading: str) -> str:
    """``heading``'s body, verbatim, from the doc.

    ⛔ THE ANCHOR IS ASSERTED TO MATCH EXACTLY ONCE. A ``break``-on-first-hit
    extraction over a doc with two candidate headings would silently inspect the
    wrong section, and the guard would then be measuring prose nobody reads —
    the failure mode `_the_search_text_set_comment_block` in
    ``tests/test_10000_source_prose.py`` calls out for the same reason. An anchor
    that stops matching is a FAIL, never an empty section.
    """
    lines = DOC.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == heading]
    assert len(starts) == 1, (
        f"`{DOC.name}` must carry the heading `{heading}` EXACTLY ONCE; found "
        f"{len(starts)} occurrence(s) at lines {starts}. Re-point this guard at "
        "the section rather than deleting it — it is the only thing tying the "
        "doc's claim to the code's")
    start = starts[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].startswith("### ")), len(lines))
    return "\n".join(lines[start:end])


def _module_doc() -> str:
    """``tortoise/cache_projection.py``'s module docstring, verbatim."""
    return cache_projection.__doc__ or ""


def test_the_doc_does_not_claim_a_re_capture_heals_a_pre_fix_node():
    """The false premise this PR retracted must not return to the doc's cost list.

    RED against the pre-fix document at `e13c6eeea7`, in THIS file's own subject:
    the bullet said *"A legacy session stays unfindable on the recovery door until
    it is re-captured (the capture now overwrites the pair with the lexicon)"* —
    both clauses false by the mint gate, and contradicted 150 lines earlier in the
    same repo by `tortoise/cache_projection.py` and
    `tests/test_10000_source_prose.py::test_a_re_capture_does_not_heal_a_pre_fix_node`.
    """
    costs = _section(COSTS_HEADING)

    for retracted in RETRACTED_SHAPES:
        assert retracted not in costs, (
            f"`{DOC.name}` re-arms the RETRACTED mechanism ({retracted!r}). A "
            "re-capture does NOT heal a pre-#10000 node: re-capturing an "
            "already-captured session id is the `#2104`/`#1727` no-op replay, so "
            "`_materialize_session_source` — whose only caller sits behind the "
            "fresh-capture gate — never runs and the stale pair survives "
            "(measured: `#10852` review, P1). Mirror "
            "`cache_projection.py`'s docstring instead.\n\n{costs}")


def test_the_doc_states_the_mechanism_and_the_routes_that_do_clear_the_pair():
    """The correction must be PRESENT, not merely the old claim deleted.

    Deleting the bullet is the cheap green this arm exists to stop: a cost list
    that is silent about the population is how the premise gets re-derived from
    scratch (measured — five artifacts carried it).
    """
    costs = _section(COSTS_HEADING)
    lowered = costs.lower()

    for keyword in CORRECTION_KEYWORDS:
        assert keyword.lower() in lowered, (
            f"`{DOC.name}`'s cost list no longer states the correcting "
            f"mechanism ({keyword!r}). Without the mint gate / no-op replay the "
            "sentence is readable as 'the legacy population sorts itself out', "
            f"which is false:\n\n{costs}")

    for route in CLEARING_ROUTES:
        assert route in costs, (
            f"`{DOC.name}`'s cost list omits the route {route!r} that DOES clear "
            "a legacy pair. A reader told only that a re-capture does not help "
            "has no way to repair the node — name the three routes the code "
            f"names:\n\n{costs}")


def test_the_pin_section_names_the_per_row_legs_and_they_resolve():
    """The `### The pin` list must cite the `#10965` legs — and cite real ones.

    `#10852`'s review recorded that the list was short by the four per-row legs
    the same PR added, so the section's own claim ("the pin") understated the
    suite. Citations are made EXECUTABLE rather than trusted (`#7948`'s lesson):
    a name that does not resolve is a prose reference that reads as proof and
    pins nothing.
    """
    pin = _section(PIN_HEADING)

    for leg in REQUIRED_PINNED_LEGS:
        assert leg in pin, (
            f"`{DOC.name}`'s `{PIN_HEADING}` does not name `{leg}` — the "
            "section states 'the pin' for the per-row (`#10965`) decision, so a "
            "list short by an arm reads as coverage it does not have")

    sources = {path: path.read_text(encoding="utf-8") for path in PIN_MODULES}
    # ⛔ The pattern skips a FILE reference (`tests/this_file.py`, which the
    # section cites for the leg modules) and admits only the bare-symbol use, so
    # the guard resolves the thing that must exist rather than the filename it
    # lives in — measured: matching the bare `\btest_…` shape red the guard on
    # this guard's own path instead of on a cited leg (same arm as the write-site
    # guard in `tests/test_10000_source_prose.py`).
    for cited in sorted(set(re.findall(
            r"(?<![/\w])(test_[a-z0-9_]+)(?!\.py)", pin))):
        assert any(re.search(rf"^def {cited}\(", text, re.MULTILINE)
                   for text in sources.values()), (
            f"`{DOC.name}`'s `{PIN_HEADING}` cites `{cited}`, which is not "
            f"defined in {' or '.join(p.name for p in PIN_MODULES)} — a doc that "
            "names a leg that does not exist pins nothing")


def test_the_module_docstring_the_doc_paraphrases_says_the_same_thing():
    """Doc and code must not disagree — the code was right and the doc wrong.

    The retracted claim lives in the doc, but the doc IS a paraphrase of
    `cache_projection`'s module docstring, so this arm keeps the pair in step in
    both directions: no retracted shape, and the correction stated.
    """
    module_doc = _module_doc()
    assert module_doc.strip(), "`cache_projection` lost its module docstring"
    assert "NOT a re-capture" in module_doc, (
        "`cache_projection`'s module docstring no longer states that the repair "
        "for a legacy node is NOT a re-capture — that sentence is the source the "
        "epic doc paraphrases and the reason the doc's cost bullet is correct")

    for retracted in RETRACTED_SHAPES:
        assert retracted not in module_doc, (
            f"`cache_projection`'s module docstring re-arms the retracted "
            f"mechanism ({retracted!r})")
