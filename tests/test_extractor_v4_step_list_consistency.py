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

**#5198 — the same defect class, in the PROSE the block read cannot reach.**
This file's own limit 1 below ("a STRUCTURAL read of two fenced blocks plus one
paragraph … cannot detect a contradiction stated in prose elsewhere") is not
hypothetical: it is `#5198`. Two recorded decisions were contradicted by prose
this guard could not see, and both are load-bearing the same way — a lane can
cite either and revert the decision:

  * §15's issue map read *"the mechanical DISCARD outcome on E7"* — the site
    `#4899`'s **DEC-1** (2026-09-25) reversed to **`S2.2b`**, and the only line
    in the document coupling the DISCARD with `E7`;
  * §4.2's recall-arithmetic paragraph carried no record that §16.3's **O2**
    (owner, 2026-09-24) had withdrawn the retention-floor framing outright —
    *"no volume target, no recall floor, no node budget"* — so the arithmetic
    still read as a volume floor (`main` removed the floor sentence itself in
    `#8529`, but left no trace of *why*; this PR restores the record, not the
    requirement);
  * the review's **D6** was still listed as an open decision-grade question
    (`REVIEW-CONSOLIDATED-2026-09-23.md`) with no disposition.

So the class this file guards is wider than one MERGE: **the v4 design must not
assert two conflicting things about one operation or one recorded decision.**
Pinned additionally: (5) no passage sites the mechanical DISCARD on `E7` unless
the coupling's OWN `E7` token is the reversal record — negated in place
(`NOT `E7``) or quoted as history — NOT merely sharing a line or a window with
a reversal mark, which the `#8443` review showed was a fail-open on §15's
one-line row (a live re-assertion placed next to the record inherited its
marks and shipped green);
(6) §15's issue-map row itself states the decided site — named, `E7` negated,
and the shipped predicate symbols cited — not merely quoting a title that
mentions both; (7) the retention-floor obligation appears only inside its own
withdrawal (**a FORWARD guard**: `main` had already removed the live obligation,
so this half cannot red on the merge base) and the recall-arithmetic paragraph
carries O2's withdrawal verbatim **plus the present-tense no-floor fact and O2
as its authority** (**the regression pin that does** — it is on the FACT, not on
the change-history phrasing a `#8529`-compliant rewrite drops, which is the
`#8443` review's finding 3); (8) the
review's `D6` carries a disposition naming the decided site, the shipped commit,
and the symbol of the pass it cites.

NOT promised:

1. This is a STRUCTURAL read of two fenced blocks plus one paragraph, **widened
   by `#5198` to four PROSE shapes** (see 5–8 above). What it still cannot do is
   find a contradiction stated in prose it was not told to look for — it pins
   the claims `#5092` and `#5198` name, not the whole document (nor
   `STORAGE-ARCHITECTURE.md` §8, which the issues track separately). Rules 5–7
   are token-level, so a restatement in different words is a NAMED gap, not a
   caught one: the exact obligation `"must be paired with a measured retention
   floor"` reds only while it is live, and a newly-worded equivalent an editor
   invents does not. Rule (7a) is a FORWARD guard — `main` had already removed
   the live obligation before the merge base, so it cannot red there; rule (7b)
   — the recall paragraph must state the no-floor FACT together with O2's
   verbatim withdrawal and name §16.3 O2 as its authority — is the
   structural half, and the non-vacuous pin, that survives rewording. The
   7b pin is deliberately NOT on the withdrawal *phrasing*: a target-state
   rewrite that keeps the fact and the authority but drops the change-history
   clause must stay green (the `#8443` review's finding 3).
2. Rule 3 is deliberately FAIL-CLOSED and slightly over-broad: a pre-gate row
   that merely *mentions* `MERGE` in a parenthetical reds too. That is the safe
   direction — the flag belongs to the gate's row — but a legitimate future
   edit that wants to name it there must move the mention to the gate row.
3. The id regex accepts the document's own spellings (`S2.2a-α`, `S2.2b`,
   `S2.3`). A THIRD spelling of the gate step (today: `S2.2` in §4.0 and the
   `### S2.2 — VET` heading, `S2.2b` in §4.1/§4.2/§4.3) is not caught — the
   blocks are the pinned artifacts.
4. Rule (5) exempts a `DISCARD`↔`E7` coupling whose tokens BOTH sit inside one
   quoted span that is MARKED AS HISTORY (`once said`, `formerly`, `reversed`, …)
   — the shape §15 uses to record the wording it replaced. A wrong site
   asserted inside such a marked quotation is therefore not caught; the pin is
   on live prose, not on history. Every other quoted coupling (a bare quotation
   of `#5198`'s title, for one) still reds. Quotes are paired positionally per
   line, so the exemption is the span, not the prose between two spans.
5. Rule (5)'s coupling scan is BOUNDED: a `DISCARD` token and an `E7` token more
   than `_COUPLING_WINDOW` (200) characters apart on one line are not read as a
   coupling, so a claim split across a long line is a NAMED gap rather than a
   caught one. Both tokens are matched case-insensitively and as whole words.

Static, stdlib-only, no network / DB / graph.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parent.parent / "docs" / "architecture"
DOC = _DOCS / "EXTRACTOR-V4-ARCHITECTURE.md"
#: `#5198`: the review whose D6 listed the site as an open decision-grade
#: question. It is a historical record — the disposition is added, never
#: rewritten, so a lane reading the table does not act on a settled question.
REVIEW = _DOCS / "REVIEW-CONSOLIDATED-2026-09-23.md"

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


# ══════════════════════════════════════════════════════════════════════════════
# #5198 — the SITE decision, and the WITHDRAWN objective (prose, not blocks)
# ══════════════════════════════════════════════════════════════════════════════
#
# #5092 (above) pinned one operation's site INSIDE the two fenced blocks. #5198
# is the same class surviving in the prose a block read cannot reach — the
# document's own limit 1 above. Two recorded decisions were still contradicted
# by text a lane reads:
#
#   * `#4899`'s **DEC-1** (2026-09-25) moved the mechanical DISCARD to `S2.2b`
#     and reversed the issue's own `E7` title. §15's issue map kept saying
#     `E7` — the LAST line in the document coupling the two tokens, and the
#     only prose a citing lane would find there.
#   * §16.3's **O2** (owner, 2026-09-24) withdrew the volume framing outright.
#     §4.2 kept *requiring* a retention floor, and §16.3's own cell claimed
#     "§4.2's objective is re-stated accordingly" — it was not.
#
# Why this is load-bearing rather than cosmetic: both surviving sentences are
# quotable as authority. A lane holding §15 reverts DEC-1; a lane holding §4.2
# re-imposes a metric the owner rejected. Neither change would say so.

#: The decided site (`#4899` DEC-1; shipped in PR `#5237`). The predicate is
#: `tortoise/value_gate.py:370` (`mechanical_verdict`), called from
#: `tortoise/vet_gate.py:469` — Level-2 of `vet_candidates`, before the arbiter
#: and before S3 RESOLVE (`tortoise/extractor_v2.py`, `_run_vet_pass`, inside
#: `extract_session_v2`). Cited by symbol, not by line: the line number rotted
#: twice while this branch was open (the `#8443` review), and the document's own
#: rule is that the symbol is authoritative.
DISCARD_SITE = "S2.2b"

#: The site DEC-1 reversed. The TOKEN is still legitimate — `E7`
#: (`classify_consolidation`, `tortoise/extractor_v2.py`) remains the home of
#: *against-priors consolidation* after S3 (§4.2 constraint 5). Only the
#: mechanical DISCARD must never be sited there.
REJECTED_SITE = "E7"

#: §16.3 O2's withdrawal, verbatim. Its presence is what makes the §4.2
#: sentence a contradiction rather than the document's settled position.
O2_WITHDRAWAL = (
    "No volume target, no recall floor, no node budget anywhere in this document."
)

#: The withdrawn OBLIGATION (not the words around it), so the same requirement
#: re-worded in the same shape still reds. It may appear in the document only
#: as a quoted, withdrawn record — see `_WITHDRAWN_REQUIREMENT`.
_FLOOR_OBLIGATION = re.compile(
    r"\b(?:must|shall|has to|needs to)\s+be\s+paired\s+with\s+a\s+measured\s+"
    r"retention\s+floor",
    re.IGNORECASE,
)

#: The withdrawal must bind to the OBLIGATION, not merely share its line. The
#: §4.2 passage is ONE long line, so a line-scoped exemption lets an incidental
#: `withdrawn` (e.g. *"…before rows are withdrawn"*) exempt a live re-assertion
#: of the very requirement O2 withdrew — the fail-open the `#5198` review found.
#: Requiring the mark to name the requirement/floor/obligation closes it: the
#: reversal record quotes the requirement and then states *that it* is gone.
_WITHDRAWN_REQUIREMENT = re.compile(
    r"(?:requirement|obligation|floor|framing|target)[^.\n]{0,40}?"
    r"\b(?:is|was|are|were|has been|had been|now)\s+"
    r"(?:withdrawn|superseded|no longer|reversed|rejected|dropped)",
    re.IGNORECASE,
)

#: Rule (7b)'s pin is the target-state FACT plus O2 as its authority, NOT the
#: change-history phrasing (*"an earlier version of this line read …"*) the
#: document's status line bans (`#8529`/`#8542`). The fact is that no retention
#: floor / recall floor / node budget is owed; a compliant rewrite states it in
#: the present tense and drops the withdrawal clause (the `#8443` review,
#: finding 3).
_NO_FLOOR_FACT = re.compile(
    r"no\s+retention\s+floor|no\s+recall\s+floor|no\s+node[- ]?budget|"
    r"not\s+a\s+floor",
    re.IGNORECASE,
)

#: How far after an obligation occurrence its own withdrawal may sit on the
#: line. A bounded window stops a re-assertion appended to the far end of a
#: one-line paragraph from inheriting an earlier reversal's mark.
_REVERSAL_WINDOW = 200

#: `DISCARD` and `E7` in one coupling is only legitimate as the reversal
#: record. That is the form `#5198` left in §15 (*"THE SITE IS `S2.2b`, NOT
#: `E7`"*): the coupling's OWN `E7` token is the one negated here.
#: The negation must ATTACH to the token it negates — `\bnot\s+`?E7` — because a
#: looser `\bnot\b[^.\n]{0,12}E7` read *"not only E7"* as a negation and
#: exempted a passage that asserts `E7` **is** a site (the `#8443` review).
_NEGATES_REJECTED = re.compile(r"\bnot\s+`?E7\b", re.IGNORECASE)

#: The rejected site token, matched CASE-INSENSITIVELY and as a whole word. The
#: `discard` scan and `_NEGATES_REJECTED` were already case-insensitive while
#: this scan was not, so `` `e7` `` escaped rule (5) entirely; the word
#: boundaries keep a hex-digest substring (`ae7b`) from being read as the site
#: (the `#8443` review).
_REJECTED_TOKEN = re.compile(
    rf"(?<![A-Za-z0-9_]){re.escape(REJECTED_SITE)}(?![A-Za-z0-9_])", re.IGNORECASE
)

#: How far apart a `DISCARD` token and an `E7` token may sit and still count as
#: one coupling. The bound keeps two tokens in unrelated clauses from being
#: read as one claim; it is NOT the exemption's scope (see below), and it is a
#: NAMED gap (module docstring, "NOT promised" 5): a pair farther apart than
#: this on one line is not scanned.
_COUPLING_WINDOW = 200

#: How far back the negation may start from the `E7` it rejects (`\bnot\s+` plus
#: the optional backtick, so this comfortably covers the whole match).
_REVERSAL_PROXIMITY = 24

#: A coupling is exempt ONLY when its own `E7` token is the reversal record —
#: never because a reversal mark happens to sit within a window around it. §15's
#: issue-map row is ONE physical line carrying many sentences, so a window- or
#: line-scoped exemption let a live re-assertion placed beside the reversal
#: record inherit its marks and ship green — the fail-open the `#8443` review
#: reproduced by inserting *"The mechanical `DISCARD` gate runs at `E7`"* right
#: after *"NOT `E7`"* (#5198). Two shapes are legitimate, and both are tested
#: against the coupling's own tokens:
#:   * the NEGATION: this `E7` is the one `not … E7` rejects;
#:   * the QUOTATION: `DISCARD` and `E7` sit inside ONE `"…"` span that is
#:     MARKED AS HISTORY (`once said`, `formerly`, `reversed`, …), i.e. the row
#:     is recording the reverted wording
#:     (*"the mechanical DISCARD outcome on E7"*) rather than asserting it.
#:     A quoted coupling with no such mark is still an assertion — which is why
#:     the merge-base row's bare quotation of `#5198`'s title reds.
_QUOTE = '"'
_HISTORICAL_QUOTATION = re.compile(
    r"once said|formerly|previously|used to|no longer|reversed|superseded|withdrawn",
    re.IGNORECASE,
)
#: How far on either side of a quoted span its history mark may sit.
_HISTORY_WINDOW = 100


def _quoted_spans(line: str) -> list[tuple[int, int]]:
    """The `"…"` spans of one line, quotes paired positionally (0-1, 2-3, …).

    Pairing left to right is what makes the exemption span-accurate.
    `rfind(quote, 0, discard.start())` resolves to the CLOSING quote when the
    text before the coupling ends a quotation, and `find(quote, opening + 1)`
    then resolves to the NEXT OPENING one — so the ordinary prose BETWEEN two
    quotations was read as "inside a quotation", and a live assertion in that
    gap inherited the record's history mark (the `#8443` review).
    """
    positions = [m.start() for m in re.finditer(re.escape(_QUOTE), line)]
    return [
        (positions[index], positions[index + 1])
        for index in range(0, len(positions) - 1, 2)
    ]


def _coupling_is_the_reversal_record(
    line: str, discard: re.Match, rejected: re.Match
) -> bool:
    """True when THIS coupling's own `E7` token is the reversal record.

    The negation must end at this `E7` (not merely appear on the line), and the
    quotation exemption requires BOTH tokens to sit inside ONE quoted span that
    is itself marked as history. A live re-assertion inherits neither, even
    when it is inserted immediately next to the reversal record or in the gap
    between two quotations. The span is found by pairing quotes positionally,
    and `E7` must start after the span opens as well as end before it closes —
    an `E7` to the LEFT of the quotation its `DISCARD` sits inside is not
    covered by it (the `#8443` review).
    """
    prefix = line[max(0, rejected.start() - _REVERSAL_PROXIMITY) : rejected.end()]
    negated = _NEGATES_REJECTED.search(prefix)
    if negated is not None and negated.end() == len(prefix):
        return True
    for opening, closing in _quoted_spans(line):
        # BOTH tokens fully inside THIS span — not merely "to the left of a
        # closing quote" (which is what the old rfind/find pair resolved to).
        if not (
            opening <= discard.start()
            and discard.end() <= closing
            and opening <= rejected.start()
            and rejected.end() <= closing
        ):
            continue
        surrounding = line[
            max(0, opening - _HISTORY_WINDOW) : closing + _HISTORY_WINDOW
        ]
        return bool(_HISTORICAL_QUOTATION.search(surrounding))
    return False


def _discard_e7_offenders(text: str) -> list[str]:
    """`DISCARD`↔`E7` couplings that are NOT the reversal record.

    The exemption is decided per COUPLING, on that coupling's own `E7` token —
    not on a window around the coupling, and not on the physical line. A live
    re-assertion inserted next to §15's reversal record, or appended to the
    one-line row, therefore reds even though the line also carries the
    reversal record.
    """
    offenders: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        couplings: list[tuple[re.Match, re.Match]] = []
        discards = [m for m in re.finditer(r"discard", line, re.IGNORECASE)]
        rejected = list(_REJECTED_TOKEN.finditer(line))
        for discard in discards:
            for candidate in rejected:
                start, end = min(discard.start(), candidate.start()), max(
                    discard.end(), candidate.end()
                )
                if end - start <= _COUPLING_WINDOW:
                    couplings.append((discard, candidate))
        for discard, candidate in couplings:
            if _coupling_is_the_reversal_record(line, discard, candidate):
                continue  # the reversal record: names the rejected site on purpose
            start = min(discard.start(), candidate.start())
            end = max(discard.end(), candidate.end())
            offenders.append(
                f"{DOC.name}:{number}: "
                f"...{line[max(0, start - 60) : end + 60].strip()[:200]}..."
            )
            break  # one report per line is enough to red it
    return offenders


def test_no_passage_sites_the_mechanical_discard_on_e7():
    """One site per operation — in PROSE too (the #5092 class, widened).

    The scan is over the whole document, not the two blocks: §15's issue map
    was where the losing site survived, and a block read cannot see it.

    The exemption is decided on the coupling's OWN `E7` token, NOT the physical
    line and NOT a window around the coupling — see `_discard_e7_offenders`
    and `test_the_reversal_exemption_does_not_cover_a_second_coupling_on_the_row`.
    """
    offenders = _discard_e7_offenders(_document())
    assert not offenders, (
        "a passage couples the mechanical DISCARD with E7 without being the "
        "reversal record (the coupling's own `E7` must be the negated one, or "
        "`DISCARD` and `E7` must sit inside one quotation of the reverted "
        "wording). `#4899`'s DEC-1 moved the gate to "
        "S2.2b; E7 stays the home of against-priors consolidation only (§4.2 "
        "constraint 5). A lane citing this line reverts the decision (#5198):\n"
        + "\n".join(offenders)
    )


def test_the_reversal_exemption_does_not_cover_a_second_coupling_on_the_row():
    """Rule (5) must judge the COUPLING's own `E7`, not a span of the line.

    §15's issue-map row is the exact location `#5198` was filed about, and it
    is one physical line ending in the reversal record. A line-scoped (or
    window-scoped) exemption therefore let a live re-assertion share the row
    with the reversal record and ship green. The `#8443` review reproduced four
    placements, and all must red: an assertion APPENDED to the row, one
    inserted immediately ADJACENT to the reversal clause, one placed in the GAP
    between two quoted spans, and one whose `E7` sits OUTSIDE the quotation its
    `DISCARD` is inside (and to its left). The last two defeated the earlier
    exemption because it paired its quotes with `rfind`/`find` — which resolves
    to a closing quote followed by the next opening quote — and never required
    the rejected token to start after the span opened.
    """
    rows = [line for line in _document().splitlines() if "§1 the mechanism" in line]
    assert len(rows) == 1, "§15's §1 row is not uniquely identifiable (#5198)"
    row = rows[0]
    assert not _discard_e7_offenders(row), (
        "the live §15 row must be the reversal record and nothing else — this "
        "assertion keeps the mutations below from passing trivially (#5198):\n"
        + row
    )
    appended = row + " ⚠️ **The mechanical `DISCARD` gate runs at `E7`.**"
    assert _discard_e7_offenders(appended), (
        "appending a live re-assertion of the E7 site to §15's one-line row "
        "must red rule (5). A line-scoped exemption cannot tell `the row quotes "
        "the losing site in the reversal record` from `the row also asserts "
        "the losing site` — that is the #8443 review's fail-open (#5198)"
    )
    # The placement that defeated the FIRST fix (`6790d9b6a`): a ±window
    # exemption still saw the reversal marks around this insertion, because the
    # re-assertion itself supplies the coupling the window was drawn around.
    reversal = "**THE GATE SITE IS `S2.2b`, NOT `E7`**"
    assert reversal in row, "the reversal clause is not in the row (#5198)"
    adjacent = row.replace(
        reversal,
        reversal + " The mechanical `DISCARD` gate runs at `E7`.",
    )
    assert _discard_e7_offenders(adjacent), (
        "a live re-assertion of the E7 site placed IMMEDIATELY next to the "
        "reversal clause must red rule (5) — otherwise the exemption is "
        "window-scoped and the re-assertion inherits the record's marks; this "
        "is the #8443 review's second fail-open (#5198)"
    )
    # THE QUOTE GAP (`#8443` review finding 1, first shape): a live, unquoted
    # coupling placed BETWEEN two quoted spans. `rfind` resolved to the CLOSING
    # quote of the first span and `find` to the OPENING quote of the second, so
    # the ordinary prose between them was read as one quotation and inherited
    # the history mark.
    quote_gap = (
        'the reversed record read *"the old E7 wording"*; '
        "**The mechanical `DISCARD` gate runs at `E7`.** "
        'the docstring says *"a definite description is left to the arbiter"*'
    )
    assert _discard_e7_offenders(quote_gap), (
        "a live re-assertion of the E7 site placed between two quoted spans "
        "must red rule (5) — otherwise the exemption reads the prose between "
        "quotations as if it were inside one; this is the #8443 review's "
        "quote-gap fail-open (#5198)"
    )
    # LEFT OF THE SPAN (`#8443` review finding 1, second shape): `DISCARD`
    # inside a quoted, history-marked span, but the coupled `E7` OUTSIDE it and
    # to its left. The old code bounded only `rejected.end() > closing`, so an
    # `E7` before the opening quote inherited the span's exemption.
    left_of_span = (
        "The gate site E7 was reversed — "
        'this row records *"the mechanical DISCARD outcome"* as history.'
    )
    assert _discard_e7_offenders(left_of_span), (
        "a coupling whose `E7` sits OUTSIDE the quotation its `DISCARD` is "
        "inside must red rule (5) — otherwise the exemption never requires "
        "the rejected token to be inside the span; this is the #8443 review's "
        "left-of-span fail-open (#5198)"
    )
    # THE NEGATION MUST ATTACH (`#8443` review finding 2): `not only E7`
    # asserts `E7` IS a site; the old `\bnot\b[^.]{0,12}E7` read it as a
    # reversal and exempted the coupling.
    not_only = (
        "**THE GATE SITE IS `S2.2b`.** "
        "The gate site is not only E7, where the mechanical DISCARD runs."
    )
    assert _discard_e7_offenders(not_only), (
        "a coupling negated as `not only E7` still asserts that `E7` carries "
        "the gate, so it must red rule (5) — the negation must attach to the "
        "token it negates (the #8443 review's negation fail-open, #5198)"
    )
    # THE TOKEN SCAN IS CASE-INSENSITIVE (`#8443` review finding 2): the
    # `discard` scan already was, while the `E7` scan was not, so a lowercase
    # `e7` escaped rule (5) entirely.
    lower_case = "The mechanical `DISCARD` gate runs at `e7`."
    assert _discard_e7_offenders(lower_case), (
        "a lowercase `e7` coupling must red rule (5) — the `discard` scan was "
        "case-insensitive while the `E7` scan was not, an undocumented "
        "asymmetry (the #8443 review, #5198)"
    )


def test_the_issue_map_row_names_the_decided_site():
    """§15 is the index a lane holding an issue reads — it must state the site.

    It said *"the mechanical DISCARD outcome on E7"* while §4.1/§4.2/§4.3 and
    the shipped code all said `S2.2b`, so the document asserted both again
    (#5198) — exactly what #5092 eliminated inside the blocks.

    The row must STATE the site in its own words (`THE GATE SITE IS `S2.2b`,
    NOT `E7``), not merely embed the tokens inside the quoted `#5198` title:
    that quote satisfied the bare-token checks on the pre-fix row, which is how
    this pin first passed vacuously (the #8443 review).
    """
    # `main` replaced the row header (`the mechanism (\`^the <X>\`)`) with
    # `the mechanism — **two halves**`; anchor on the prefix both spellings
    # share so the row stays findable, and keep its scope text intact — the
    # identifier-only predicate does NOT own the `the <X>` class (#8498).
    rows = [
        line
        for line in _document().splitlines()
        if "§1 the mechanism" in line
    ]
    assert len(rows) == 1, (
        "§15's issue-map row for §1 is not uniquely identifiable — the guard "
        "cannot pin a row it cannot find (#5198)"
    )
    row = rows[0]
    assert DISCARD_SITE in row, (
        "§15's issue-map row does not name the decided gate site "
        f"({DISCARD_SITE!r}). It is the index a lane holding `#4899` reads "
        "(#5198):\n" + row
    )
    assert "value_gate._REFERENCE" in row and "identifier_only()" in row, (
        "§15's issue-map row must name the shipped predicate symbols "
        "(`value_gate._REFERENCE` / `identifier_only()`), not a hand-written "
        "pattern, and must scope itself to the identifier-only half — the "
        "62.3% `the <X>` class is explicitly NOT owned by this row "
        "(#8498 / #5198):\n" + row
    )
    assert _NEGATES_REJECTED.search(row), (
        "§15's issue-map row does not STATE the decided site — it must name "
        "`S2.2b` and negate `E7` in its own words (`THE GATE SITE IS `S2.2b`, "
        "NOT `E7``). Merely quoting the `#5198` title, which mentions both "
        "sites, is what let this pin pass before the fix (#8443):\n" + row
    )
    assert row.index(DISCARD_SITE) < row.index(REJECTED_SITE), (
        "§15's issue-map row names E7 BEFORE the decided site — so the row "
        "still asserts the losing site (`#4899` DEC-1). Put `S2.2b` first "
        "(#5198):\n" + row
    )
    assert "still unowned" not in row, (
        "§15's issue-map row still records the placement as *\"still unowned\"* "
        "while the same row states it is decided and shipped, and `#5237` "
        "removed that phrase from `vet_gate.py` — so the row contradicts itself "
        "in both directions (the #8443 review, #5198):\n" + row
    )


def test_the_withdrawn_floor_requirement_is_never_reasserted():
    """§16.3 O2 withdrew the floor framing; §4.2 must not keep requiring one.

    The obligation is ALLOWED only inside its own withdrawal — the document
    records the reversal by quoting what it no longer says. Anywhere else it
    is the contradiction `#5198` is filed for.

    This half is a FORWARD guard. `main` (`#8529`) had already removed the live
    obligation before the merge base, so this scan cannot red there; the
    non-vacuous pin on the merge base is
    `test_the_recall_arithmetic_paragraph_carries_o2s_withdrawal` (rule 7b).
    """
    text = _document()
    assert O2_WITHDRAWAL in text, (
        "§16.3 O2's withdrawal sentence is gone. It is the authority the "
        "recall-arithmetic paragraph is reconciled against — without it, a "
        "retention floor reads as the documented rule again (#5198)"
    )
    offenders = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in _FLOOR_OBLIGATION.finditer(line):
            # The mark must bind to THIS obligation, not merely the line: an
            # incidental "withdrawn" elsewhere in a one-line paragraph must not
            # exempt a live re-assertion (the #5198 review's fail-open).
            window = line[match.end() : match.end() + _REVERSAL_WINDOW]
            if not _WITHDRAWN_REQUIREMENT.search(window):
                offenders.append(f"{DOC.name}:{number}: {line.strip()[:200]}")
                break
    assert not offenders, (
        "a line requires a measured retention floor without recording that the "
        "requirement is withdrawn — O2 (owner, 2026-09-24) withdrew it "
        "outright. Quote it as withdrawn, or drop it (#5198):\n"
        + "\n".join(offenders)
    )


def test_the_recall_arithmetic_paragraph_carries_o2s_withdrawal():
    """The reconciliation SITE, not just the absence of the old phrasing.

    Rule 3 above would pass on a document that deleted the sentence and said
    nothing — leaving the arithmetic looking like a floor nobody has stated.
    The paragraph that makes the multiplicative-recall claim must carry O2's
    withdrawal verbatim, NAME O2/§16.3 as the authority, and state the
    target-state fact, so the next reader is told which governs (#5198).

    The pin is on the FACT plus its authority, deliberately NOT on the
    change-history phrasing (*"an earlier version … THAT REQUIREMENT IS
    WITHDRAWN"*): the document's status line bans the change-history shape
    (`#8529`/`#8542`), so a rewrite that keeps the fact and the authority but
    drops that clause must stay green (the `#8443` review, finding 3).
    """
    text = _document()
    marker = "recall compounds MULTIPLICATIVELY"
    assert marker in text, (
        "§4.2's multiplicative-recall paragraph is gone. It is a fact about "
        "composing stages and it is NOT a floor this pipeline owes anyone — if "
        "it is removed, remove it with the O2 note, not silently (#5198)"
    )
    # The paragraph, read by its blank-line boundaries rather than a fixed
    # character window — a window silently truncates the sentence it is asked
    # to find, which is the guard failing rather than the document (#5198).
    index = text.index(marker)
    paragraph = text[text.rfind("\n\n", 0, index) + 2 : text.find("\n\n", index)]
    assert O2_WITHDRAWAL in paragraph, (
        "the recall-arithmetic paragraph does not carry O2's withdrawal. §16.3 "
        "O2 (owner, 2026-09-24) rejected the volume framing outright, so the "
        "paragraph must say so where the arithmetic is read — otherwise the "
        "next lane cites it to demand a retention floor (#5198):\n"
        + paragraph[:600]
    )
    assert "§16.3" in paragraph and "O2" in paragraph, (
        "the paragraph states a withdrawal without naming its authority — it "
        "must cite §16.3 O2 (owner, 2026-09-24). A fact with no authority is "
        "what let the floor framing be cited as a live rule (#5198):\n"
        + paragraph[:600]
    )
    assert _NO_FLOOR_FACT.search(paragraph), (
        "the paragraph does not state the target-state FACT — that no "
        "retention floor, no recall floor and no node budget is owed by this "
        "pipeline. The pin is on the fact plus its authority, not on the "
        "change-history phrasing a `#8529`-compliant rewrite drops (#8443):\n"
        + paragraph[:600]
    )


def test_the_reviews_d6_question_carries_a_disposition():
    """D6 asked where the gate lives; the answer is recorded, not pending.

    `REVIEW-CONSOLIDATED-2026-09-23.md` is a historical review, so the row is
    NOT rewritten — but a lane reading its open-question table must not act on
    a question the record has closed (#5198).
    """
    text = REVIEW.read_text(encoding="utf-8")
    assert "**D6**" in text, "the review's D6 row is gone (#5198)"
    row_window = text[text.index("| **D6**") :][:400]
    assert "ANSWERED" in row_window, (
        "the D6 row does not point at its disposition — a lane reading only the "
        "table takes the site as still open (#5198):\n" + row_window[:200]
    )
    assert "D6 — ANSWERED" in text, (
        "the D6 disposition block is gone. It must state the decided site and "
        "cite the shipped commit, so the disposition is verifiable rather than "
        "claimed — the failure §E of that review warns about (#5198)"
    )
    disposition = text[text.index("D6 — ANSWERED") :][:3000]
    assert DISCARD_SITE in disposition, (
        "the D6 disposition does not name the decided site (#5198)"
    )
    assert "ea70025aa" in disposition, (
        "the D6 disposition does not cite the shipped commit (`#5237`, "
        "`ea70025aa`) — an unverified disposition is the class that review's "
        "§E was written about (#5198)"
    )
    assert "_run_vet_pass" in disposition, (
        "the D6 disposition cites the VET pass by line number only; the "
        "document's own rule is that the SYMBOL is authoritative — name "
        "`_run_vet_pass` so a verifier can find it when the line moves "
        "(the cited `extractor_v2.py` line was already wrong as of #8443):\n"
        + disposition[:600]
    )
    assert "extract_session_v2" in disposition, (
        "the D6 disposition must name the ENCLOSING function of the pre-S3 "
        "`_run_vet_pass` call — `extract_session_v2`. `execute_embed` runs "
        "AFTER both VET passes (S5), so naming it implies the opposite timing "
        "of the claim (the #8443 review, #5198):\n" + disposition[:600]
    )
    assert not re.search(r"extractor_v2\.py:\d", disposition), (
        "the D6 disposition must cite `extractor_v2.py` by SYMBOL, not by line "
        "number: the number was wrong at two consecutive heads while this "
        "branch was open (the #8443 review), and the document's own rule is "
        "that the symbol is authoritative:\n" + disposition[:600]
    )
    assert "execute_embed" not in disposition, (
        "the D6 disposition names `execute_embed` as the site of the VET pass. "
        "`execute_embed` does not call `_run_vet_pass` — it is the S5 "
        "function that follows both passes, i.e. the container of the "
        "post-S3 site DEC-1 rejected (the #8443 review, #5198):\n"
        + disposition[:600]
    )
