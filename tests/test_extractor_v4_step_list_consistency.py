"""#5092 — the v4 design must not place one extraction operation in two sites.

THE DEFECT THIS PINS
--------------------
`docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` carried three passages that
placed the arbitrated near-duplicate MERGE at two different points, and its
canonical step list placed it nowhere:

  * §4.2's "resulting order" put a merge stage BEFORE the gate
    (`S2.2a-β NEAR-DUP  detect; MERGE only when nothing distinguishing is lost`);
  * §S2.2 said the opposite — *"Merging is S3's decision, made after the gate"*;
  * §4.2's constraint 2 forbids a non-exact stage before VET, while the table it
    sits above listed one there;
  * §4.1's canonical list (`S0 · S1 · S2.1 · S2.2 · S2.3 · S3 · S5 · S6`)
    contained no `S2.2a` at all, so the two lists disagreed on whether the stage
    exists.

That is load-bearing, not cosmetic: `#5006` is chartered to implement the merge,
and a later lane could cite §S2.2's plain text to revert the work — while an
implementer reading §4.2 would build a stage §S2.2 excludes.

THE RECONCILIATION (three operations, one site each)
----------------------------------------------------
`S2.2a-β` **detects** the pair and applies the §16.4 never-across guard;
`S2.2b VET` records the **verdict** `MERGE` (`tortoise/vet_gate.py:OUTCOMES` —
a flag, nothing more); `S3` executes the **fusion**. The token split is what the
shipped `vet_gate.py` already draws (`MERGE` is an admissible outcome;
`MERGE-INTO-EXISTING` is deliberately not emitted), and §S2.2 already reserved
the *action* to S3. So no new placement is created here — a pre-gate FUSION
claim is removed, because it was the only passage asserting one.

WHAT THESE GUARDS PIN, AND WHAT THEY DO NOT PROMISE
---------------------------------------------------
Pinned: (1) every step id §4.2's ordering table uses appears in §4.1's canonical
list — the drift that hid the stage; (2) the two blocks order their common ids
identically; (3) NO row before the gate row claims a fusion — the placement, in
both blocks; (4) §S2.2's passage separates the verdict token from the action
token.

NOT promised:

1. This is a STRUCTURAL read of two fenced blocks plus one paragraph. It cannot
   detect a contradiction stated in prose elsewhere in this document (or in
   `STORAGE-ARCHITECTURE.md` §8, which the issue tracks separately). It pins the
   two blocks that the issue names, not the whole document.
2. Rule 3 is deliberately FAIL-CLOSED and slightly over-broad: a pre-gate row
   that merely *mentions* `MERGE` in a parenthetical reds too. That is the safe
   direction — the flag belongs to the gate's row — but a legitimate future
   edit that wants to name it there must move the mention to the gate row.
3. The id regex accepts the document's own spellings (`S2.2a-α`, `S2.2b`,
   `S2.3`). A THIRD spelling of the gate step (today: `S2.2` in §4.0 and the
   `### S2.2 — VET` heading, `S2.2b` in §4.1/§4.2/§4.3) is not caught — the
   blocks are the pinned artifacts.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC = (
    Path(__file__).resolve().parent.parent
    / "docs"
    / "architecture"
    / "EXTRACTOR-V4-ARCHITECTURE.md"
)

CANONICAL_HEADING = "### 4.1 The step list"
ORDERING_HEADING = "#### The resulting order"

#: The step spellings this document uses: `S0`, `S0a`, `S2.1`, `S2.2a-α`,
#: `S2.2a-β`, `S2.2b`, `S3` … The `-α`/`-β` suffix is the dedup leg's, not a
#: sub-step of its own.
_STEP_ID = re.compile(r"^(S\d+(?:\.\d+)?[a-z]?(?:-[αβ])?)\s")

#: The gate row. Both blocks name it `S2.2b`; §4.0 and the `### S2.2 — VET`
#: heading call the family `S2.2`. Pinned here so a rename reds loudly.
GATE_ID = "S2.2b"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _fenced_rows(heading: str) -> list[tuple[str, str]]:
    """The (id, row) pairs of the first ```text block after `heading`."""
    text = _document()
    assert heading in text, f"heading not found in {DOC.name}: {heading!r}"
    start = text.index(heading)
    fence = text.index("```text", start)
    body = text[fence + len("```text") : text.index("```", fence + len("```text"))]
    rows: list[tuple[str, str]] = []
    for line in body.splitlines():
        match = _STEP_ID.match(line)
        if match:
            rows.append((match.group(1), line))
    return rows


@pytest.fixture(scope="module")
def canonical() -> list[tuple[str, str]]:
    return _fenced_rows(CANONICAL_HEADING)


@pytest.fixture(scope="module")
def ordering() -> list[tuple[str, str]]:
    return _fenced_rows(ORDERING_HEADING)


def test_both_blocks_were_actually_parsed(canonical, ordering):
    """A guard over an empty parse passes vacuously — the #2386 failure shape."""
    assert len(canonical) >= 8, canonical
    assert len(ordering) >= 8, ordering
    assert GATE_ID in [i for i, _ in canonical]
    assert GATE_ID in [i for i, _ in ordering]


def test_canonical_step_list_carries_every_id_the_ordering_table_uses(canonical, ordering):
    """§4.1 and §4.2 must agree that a stage EXISTS.

    The #5092 drift: §4.2 gained `S2.2a-α`/`S2.2a-β` and §4.1 never did — so the
    canonical list read as if the near-duplicate stage did not exist, which is
    exactly how "MERGE is authorised" became "MERGE has no settled site".
    """
    have = {i for i, _ in canonical}
    missing = [i for i, _ in ordering if i not in have]
    assert not missing, (
        "the §4.2 ordering table uses step id(s) the §4.1 canonical list never "
        f"declares: {missing}. A stage that exists in one list and not the other "
        "re-opens #5092 — add it to §4.1 (`tests/test_extractor_v4_step_list_"
        "consistency.py`)."
    )


def test_both_blocks_order_their_common_ids_identically(canonical, ordering):
    """Same set, same sequence — presence is not enough if the order moved."""
    common = {i for i, _ in ordering}
    assert [i for i, _ in ordering if i in common] == [
        i for i, _ in canonical if i in common
    ], "§4.1 and §4.2 disagree on the ORDER of the steps they share"


def test_no_stage_before_the_gate_claims_the_fusion(canonical, ordering):
    """The placement itself: nothing upstream of the gate fuses.

    `S3` is where the union executes (§S2.2 — *"merging is S3's decision, made
    after the gate"*), and `S2.2b VET` is the only place the `MERGE` verdict is
    recorded. A row above the gate row that names `MERGE` is asserting a second
    site for one of those operations, in whichever direction (#5092).
    """
    for label, rows in ((CANONICAL_HEADING, canonical), (ORDERING_HEADING, ordering)):
        ids = [i for i, _ in rows]
        gate = ids.index(GATE_ID)
        offenders = [row for _, row in rows[:gate] if "MERGE" in row]
        assert not offenders, (
            f"{label}: a step BEFORE the {GATE_ID} gate row names `MERGE`: "
            f"{offenders}. Detection belongs at `S2.2a-β`, the VERDICT at the "
            "gate row, the FUSION at S3 — one site each (§S2.2, #5092)."
        )


def test_the_gate_passage_separates_the_verdict_from_the_action():
    """§S2.2 must keep BOTH tokens and the prohibition.

    The issue's second reading was enabled by one word doing two jobs: `MERGE`
    the gate VERDICT (admissible — `vet_gate.py:OUTCOMES`) and
    `MERGE-INTO-EXISTING` the ACTION (deliberately not emitted). A passage that
    keeps only one of them re-creates the conflation.
    """
    text = _document()
    marker = "**`MERGE-INTO-EXISTING` is deliberately NOT a VET output"
    assert marker in text, (
        "§S2.2's verdict/action passage is gone — the gate's admissible action "
        "set is no longer stated (#5092)"
    )
    passage = text[text.index(marker) :][:1600].lower()
    assert "merge-into-existing" in passage and "`merge`" in passage, passage
    assert "verdict" in passage and "action" in passage, passage
    assert "vet_gate.py" in passage, (
        "the passage must cite the shipped vocabulary (`tortoise/vet_gate.py`) "
        "it is reconciled against — otherwise it reads as a proposal, not as "
        "the code's own behaviour (#5092)"
    )
