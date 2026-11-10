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
(7) the retention-floor obligation appears only inside its own
withdrawal (**a FORWARD guard**: `main` had already removed the live obligation,
so this half cannot red on the merge base) and the recall-arithmetic paragraph
carries the present-tense no-floor fact **and names O2 / §16.3 as its
authority** (**the regression pin that does** — it is on the FACT and its
authority, not on the change-history phrasing a `#8529`-compliant rewrite drops,
which is the `#8443` review's finding 3; the whole-document verbatim withdrawal
is checked in the sibling test); (8) the
review's `D6` carries a disposition naming the decided site, the shipped commit,
and the symbol of the pass it cites.

NOT promised:

1. This is a STRUCTURAL read of two fenced blocks plus one paragraph, **widened
   by `#5198` to four PROSE shapes** (see 5, 7 and 8 above). What it still cannot do is
   find a contradiction stated in prose it was not told to look for — it pins
   the claims `#5092` and `#5198` name, not the whole document (nor
   `STORAGE-ARCHITECTURE.md` §8, which the issues track separately). Rules 5–7
   are token-level, so a restatement in different words is a NAMED gap, not a
   caught one: the exact obligation `"must be paired with a measured retention
   floor"` reds only while it is live, and a newly-worded equivalent an editor
   invents does not. Rule (7a) is a FORWARD guard — `main` had already removed
   the live obligation before the merge base, so it cannot red there; rule (7b)
   — the recall paragraph must state the no-floor FACT and name §16.3 O2 as its
   authority (the whole-document verbatim withdrawal is checked in the sibling
   test) — is the structural half, and the non-vacuous pin, that survives
   rewording. The
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
   — the shape the reversal record uses to state the wording it replaced. A wrong site
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

import ast
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
#:
#: `#11002`: the id grammar is also the guard's EYES. It accepted only the
#: `-α`/`-β` suffix, so a row labelled with the two ids the shipped pipeline
#: actually uses — `S2.2b-RE-NARRATE` and `S2.2b-ADD` — did not parse AT ALL:
#: the row was invisible to `_fenced_rows`, and every presence/order assertion
#: below read the blocks as if those stages did not exist. That is why the two
#: stages `#11517`/`#11554` (pull requests; the ISSUES are `#11002`/`#11534`)
#: shipped were absent from both declared lists while the pipeline ran them, and no test reddened.
#:
#: ⚠️ **This grammar is NOT a total function over possible ids, and the fix is
#: not "a wider regex".** `-[A-Z][A-Z-]*` still cannot match an id with a
#: lowercase or a digit after the hyphen (`S2.2c-mentions`, `S2.2b-ADD-2`), so a
#: row spelled that way is still skipped by `_fenced_rows` — the residual is
#: inherent to a regex over free prose. What closes it is
#: `test_every_step_shaped_row_parses`: a row that starts like a step id but does
#: not parse now REDS, instead of being dropped silently.
_STEP_ID = re.compile(r"^(S\d+(?:\.\d+)?[a-z]?(?:-[αβ]|-[A-Z][A-Z-]*)?)\s")

#: Anything that CLAIMS to be a step row: `S` followed by a digit at COLUMN 0.
#: Deliberately looser than `_STEP_ID` — its job is to catch a row `_STEP_ID`
#: refused, so it can never be tighter than it. ⚠️ **Column 0 is the blocks'
#: own row convention and their continuation lines are INDENTED**, so an
#: indented row is invisible here — a named residual, not a claim of totality.
_STEP_SHAPED = re.compile(r"^S\d")

#: The gate row. Both blocks name it `S2.2b`; §4.0 and the `### S2.2 — VET`
#: heading call the family `S2.2`. Pinned here so a rename reds loudly.
GATE_ID = "S2.2b"


def _document() -> str:
    return DOC.read_text(encoding="utf-8")


def _fenced_body(heading: str) -> str:
    """The raw body of the first ```text block after `heading`."""
    text = _document()
    assert heading in text, f"heading not found in {DOC.name}: {heading!r}"
    start = text.index(heading)
    fence = text.index("```text", start)
    return text[fence + len("```text") : text.index("```", fence + len("```text"))]


def _fenced_rows(heading: str) -> list[tuple[str, str]]:
    """The (id, row) pairs of the first ```text block after `heading`.

    A row's INDENTED CONTINUATION lines are joined into its text. The document
    wraps nearly every step row, so a scan that reads only the first physical
    line is blind to the half a lane actually reads — and to the half where a
    retirement claim naturally goes: BOTH rows this change adds are two-line
    rows, and `RETIRED — the gap fill is no longer run` written on the second
    line passed every check (`#11002` review, cycle 7).
    """
    rows: list[tuple[str, str]] = []
    for line in _fenced_body(heading).splitlines():
        match = _STEP_ID.match(line)
        if match:
            rows.append((match.group(1), line))
        elif not line.strip():
            continue
        elif line[:1].isspace() and rows:
            rows[-1] = (rows[-1][0], rows[-1][1] + " " + line.strip())
        else:
            # A line INSIDE a step block that is neither a row nor an indented
            # continuation is a row the parser lost — the silent-drop mechanism
            # that hid two shipped stages. Redden rather than discard it: a
            # retirement sentence at column 0 under a row was measured green
            # (`#11002` review, cycle 8).
            rows.append((f"<unparsed {line.strip()[:40]!r}>", line))
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
#: never because a reversal mark happens to sit within a window around it. The
#: reversal record (§7's one-line value-gate statement) is ONE physical line
#: carrying many sentences, so a window- or
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
    re-assertion inserted next to the reversal record, or appended to the
    one-line statement, therefore reds even though the line also carries the
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

    The reversal record — §7's one-line value-gate statement, formerly §15's
    issue-map row — is the exact location `#5198` was filed about, and it is one
    physical line ending in the reversal clause. A line-scoped (or
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
    rows = [line for line in _document().splitlines() if "The design requires ENFORCEMENT BY DEFAULT" in line]
    assert len(rows) == 1, (
        "§7's statement of the shipped gate is not uniquely identifiable "
        "(#5198)"
    )
    row = rows[0]
    assert not _discard_e7_offenders(row), (
        "the live §7 statement must be the reversal record and nothing else — this "
        "assertion keeps the mutations below from passing trivially (#5198):\n"
        + row
    )
    appended = row + " ⚠️ **The mechanical `DISCARD` gate runs at `E7`.**"
    assert _discard_e7_offenders(appended), (
        "appending a live re-assertion of the E7 site to the one-line statement "
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
    marker = "recall compounds multiplicatively"
    haystack = text.lower()
    assert marker in haystack, (
        "§4.2's multiplicative-recall paragraph is gone. It is a fact about "
        "composing stages and it is NOT a floor this pipeline owes anyone — if "
        "it is removed, remove it with the O2 note, not silently (#5198)"
    )
    # The paragraph, read by its blank-line boundaries rather than a fixed
    # character window — a window silently truncates the sentence it is asked
    # to find, which is the guard failing rather than the document (#5198).
    index = haystack.index(marker)
    paragraph = text[text.rfind("\n\n", 0, index) + 2 : text.find("\n\n", index)]
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


# ══════════════════════════════════════════════════════════════════════════════
# #11002 — the DECLARATION must name the stages the PIPELINE RUNS, in order
# ══════════════════════════════════════════════════════════════════════════════
#
# The issue's premise, in its own objective: "the pipeline is the eight steps, in
# that order" — and the DECLARATION was where that stopped being true.  `#11517`
# and `#11554` closed two thirds of the disagreement by shipping the stages (the
# bounded re-narrate and the
# the ADD-capable partial-gap fill); the remaining disagreement was the
# DOCUMENT — and it was silent, because the guard above could not parse the ids
# those stages use (see `_STEP_ID`).  So the blocks named neither stage, and the
# drift marker's own "shipped code" column still published the pre-`#11517`
# order (`run_s1 → run_s2 → S2.2 VET → S3 → run_s4 → S5`).  A lane reading the
# canonical list therefore could not learn that the gap-filling had been
# relocated, nor WHERE — the one fact `#11002`'s second half exists to record.
#
# WHY THIS DIRECTION (declaration → code, not code → declaration).  The other
# option is deleting `run_s4` so the code matches the eight-step list, and it is
# contradicted by three recorded decisions, not by preference:
#   * §4.3 G5's ordering obligation — removal is a MIGRATION deliverable and
#     "must not leave the step list before its successor lands";
#   * `#1536` E4 ("S4 merges, not replaces") and the requirement recorded on
#     `#11002` on 2026-11-08 that the retry must UNION both arms — derived
#     there from those two records, NOT a new ruling (that comment withdraws
#     the owner question);
#   * F3, measured on `#11002` (2026-11-08): `added_by_s4 > 0` while
#     `coverage_suspect` is False in 4 of the 7 `legacy`-arm sessions of the
#     primary run, 3 of 7 on the re-run, and 5 of 7 across their UNION — the arm
#     is named because the count is arm-specific (§4.3).  The retry provably
#     cannot reach those items, so the deletion would remove gap-filling rather
#     than relocate it.  That is the failure the issue names by name.
#
# So the eight TOP-LEVEL steps stand, the two stages are declared as `S2.2`
# SUB-STEPS (which §4.1's own note allows: "a sub-step does not make a ninth
# step"), and `run_s4` keeps its own marker row as the LIVE stage — a statement
# about the shipped code, never a step added to the design.
#
# WHAT THIS PINS, AND WHAT IT DOES NOT.  Pinned: (1) both blocks DECLARE every
# stage this guard can locate; (2) the declared order equals the source order of
# the pipeline's own call sites; (3) the gap-fill runs BEFORE the stage it
# fills; (4) the live S4 sits between S3 and S5; (5) the drift marker's
# stage-order row names those stages in the same order, with the gate before the
# retry and S4 between S3 and S5.  NOT pinned: stage ids whose work has
# no distinct call site in `extract_session_v2` — `S0a`/`S0b` (no distinct call
# site in the pipeline body: the mechanism is `tortoise/source_identity.py`,
# reached through `projection/entities.py`, and `config/v4-mechanisms.yml` carries
# it as `state: implemented`), `S2`/`S2.1`/`S2.2` (inside `_arm_extract`, one
# call) and `S6`/`S6a`
# (inside `execute_embed`/the write path).  `S0` IS pinned
# (`_edus_from_conversation` is the body's own segmentation call).  A wrong
# ORDER among the unpinned ids is a NAMED gap, not a caught one.

#: `extract_session_v2`'s own body is the behavior being compared to the
#: declaration, so the guard reads that function and nothing else.  A file-wide
#: `index()` would find the DEFINITIONS (`def run_s4`, `def execute_embed`),
#: which sit at module level and would make every order assertion vacuous.
EXTRACTOR = Path(__file__).resolve().parent.parent / "tortoise" / "extractor_v2.py"
PIPELINE_FN = "extract_session_v2"

#: Each declared stage that has ONE distinguishable call site in the pipeline
#: body, mapped to the marker that identifies it.  Keyed by the id §4.1/§4.2
#: use, so the comparison is declaration→code and a rename on either side reds.
#: `S1`'s call site is `_arm_extract`'s FIRST occurrence (arm A); the second is
#: the retry arm, which is what `S2.2b-RE-NARRATE` names, and `body.index`
#: returns the first — so the two stages are not conflated.
#: `S2.3`'s first `_collect_classify_items` call is the S2 classify pass (the
#: second is the post-union re-classify).
#: ⚠️ `S2.2 VET` has NO marker: its pre-S3 pass lives inside `_arm_extract`, and
#: the only `_run_vet_pass(` call in the pipeline body is the POST-union one, so
#: a marker there would pin the wrong site. The two `S2.2b` rows' position
#: relative to the gate is pinned at the DECLARATION level instead
#: (`test_the_new_substeps_are_declared_after_the_gate`) — otherwise moving both
#: new rows above the gate row passed every check, which would declare the ADD
#: stage running before the gate (`#11002` review, finding 2).
_CODE_MARKERS: dict[str, str] = {
    # §4.1's S0 is mechanical parsing, and the document itself names the call
    # (`_edus_from_conversation(conversation)` segments the input into units), so
    # it is order-pinnable like the rest. `S0a`/`S0b` have no distinct call site
    # in the PIPELINE BODY (their code is `tortoise/source_identity.py`, reached
    # through `projection/entities.py`), which is a statement about where the
    # call is, never about whether it exists.
    "S0": "_edus_from_conversation(",
    "S1": "_arm_extract(",
    # The retry's DECISION is `_renarrate_trigger`; its decisive ACT is
    # `_renarrate_pick`, which binds the winning narrative and list. Pinning the
    # decision alone left "the ADD stage reads the WINNING narrative"
    # unenforced: the gap fill could be moved between the trigger and the second
    # arm and every assertion stayed green (`#11002` review, finding 1).
    "S2.2b-RE-NARRATE": "_renarrate_pick(",
    "S2.2b-ADD": "run_partial_gap_fill(",
    "S2.3": "_collect_classify_items(",
    "S3": "search_graph(",
    "S5": "execute_embed(",
}

#: The LIVE stage that is deliberately NOT a declared step (§4.1: "There is no
#: S4").  It is pinned by POSITION instead, because the shipped code must not be
#: allowed to run it somewhere the declaration's order would forbid.
LIVE_S4_MARKER = "run_s4("


@pytest.fixture(scope="module")
def pipeline_body() -> str:
    source = EXTRACTOR.read_text(encoding="utf-8")
    node = next(
        (
            n
            for n in ast.parse(source).body
            if isinstance(n, ast.FunctionDef) and n.name == PIPELINE_FN
        ),
        None,
    )
    assert node is not None, (
        f"`{PIPELINE_FN}` is gone from {EXTRACTOR.name} — that function is the "
        "pipeline whose order this guard compares to the declaration. Re-point "
        "the guard at its replacement, or retire it with the function (#11002)."
    )
    lines = source.splitlines()
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def _declared_ids(heading: str) -> list[str]:
    return [i for i, _ in _fenced_rows(heading)]


def _order_problems(body: str, declared_ids: list[str]) -> list[str]:
    """Every way the declaration and the pipeline's own order disagree.

    Takes `body` as an argument (rather than reading the file) so the mutation
    tests below can prove each check reds on a source it did not come from.
    """
    problems: list[str] = []
    missing = [
        f"{sid} ({marker!r})"
        for sid, marker in _CODE_MARKERS.items()
        if marker not in body
    ]
    if missing:
        return [
            "the pipeline body no longer carries the call site(s) these declared "
            f"steps are pinned by: {', '.join(missing)}. Either the step moved "
            "out of `extract_session_v2` (re-point the guard, #11002) or it was "
            "renamed (update §4.1/§4.2 in the same commit)."
        ]
    positions = {sid: body.index(m) for sid, m in _CODE_MARKERS.items()}

    # ⛔ ONE call site per stage. `.index` reads the FIRST occurrence, so an
    # ADDED second site is otherwise unexamined — measured: a second
    # `run_partial_gap_fill` call placed after `run_s4` (re-filling gaps BEHIND
    # the stage it replaces, the exact regression this guard exists for) left
    # the file green (`#11002` review, cycle 5). The two stages that legitimately
    # have two sites are named: `S1` (arm A + the retry arm) and `S2.3` (the S2
    # classify pass + the post-union re-classify).
    _EXPECTED_SITES = {"S1": 2, "S2.3": 2}
    for sid, marker in _CODE_MARKERS.items():
        want = _EXPECTED_SITES.get(sid, 1)
        got = body.count(marker)
        if got != want:
            problems.append(
                f"{sid} has {got} call site(s) for {marker!r} in the pipeline "
                f"body, expected {want} — an added site is not read by ANY "
                "order assertion here"
            )
    if body.count(LIVE_S4_MARKER) != 1:
        problems.append(
            f"the live `run_s4(` has {body.count(LIVE_S4_MARKER)} call sites "
            "in the pipeline body, expected 1"
        )

    declared = [i for i in declared_ids if i in _CODE_MARKERS]
    actual = sorted(declared, key=lambda i: positions[i])
    if declared != actual:
        problems.append(
            "the declared order is not the order the pipeline runs: §4.1/§4.2 "
            f"declare {declared}, the source order is {actual}."
        )

    if LIVE_S4_MARKER not in body:
        problems.append(
            "`run_s4(` is absent from the pipeline body. If S4 was REMOVED, "
            "that is a migration deliverable (§4.3 G5) — retire this guard and "
            "the drift marker's stage-order row in the same commit; do not "
            "leave them publishing an order the code no longer runs (#11002)."
        )
        return problems

    s4 = body.index(LIVE_S4_MARKER)
    if "S3" in positions and s4 < positions["S3"]:
        problems.append(
            "`run_s4(` now runs BEFORE `search_graph` (S3). S4 reviews gaps "
            "against S3's index, so a pre-lookup S4 is a different stage, not a "
            "reorder (#11002)."
        )
    if "S5" in positions and s4 > positions["S5"]:
        problems.append(
            "`run_s4(` now runs AFTER `execute_embed` (S5) — its union would "
            "never reach the write (#11002)."
        )
    # NOTE: this branch is SUBSUMED by the declared-order comparison above (an
    # ADD stage after `run_s4` also reorders the declared ids), and it is kept
    # only for its message: it names the relocation, which the generic order
    # message would not. It is therefore not separately mutation-provable.
    if "S2.2b-ADD" in positions and positions["S2.2b-ADD"] > s4:
        problems.append(
            "the ADD-capable gap fill now runs AFTER `run_s4` — the stage it "
            "was relocated to replace. §4.1/§4.3 G5 place it before the point "
            "it fills; moving it back re-creates the disagreement #11002 is "
            "about (and doubles the cost S4 already pays)."
        )
    return problems


def _presence_problems(ids: list[str], required: tuple[str, ...]) -> list[str]:
    return [f"{sid} is not declared" for sid in required if sid not in ids]


#: ⛔ NO ROW IS EXEMPT from the retirement scan, and `S0a`/`S0b` are why. They
#: are annotated `← NEW (proposal, §4.2)` in §4.1 while `config/v4-mechanisms.yml`
#: carries `source_dedup` as `state: implemented` (`tortoise/source_identity.py`,
#: wired in `projection/entities.py`), and `S2.2a-α`/`-β` carry the same
#: annotation over mechanisms the registry also calls `implemented` — so the
#: annotation is a stale DESIGN-status marker and cannot stand in for code
#: status. A carve-out on it left a retirement claim about a SHIPPED stage green
#: (`#11002` review, cycles 8-9).


def _declared_row_problems(
    rows: list[tuple[str, str]], required: tuple[str, ...]
) -> list[str]:
    """Presence, plus: a declared row of a stage the code RUNS may not retire it.

    Presence alone is satisfied by a row that says the stage is gone — measured:
    rewriting §4.1's `S2.2b-ADD` row to *"RETIRED — the gap fill is no longer run"*
    left the file green (`#11002` review, cycle 5). This is the same failure class
    the `run_s4` token pin closes for the stage-order row.
    """
    problems = _presence_problems([i for i, _ in rows], required)
    # EVERY declared row: `S2.2b` (the gate), `S2.1`, `S2.2`, `S2.2a`, `S0a`,
    # `S0b`, `S6` and `S6a` all carry shipped code too, so a retirement claim in
    # one of them is the same false statement (see the note above on why the
    # `← NEW (proposal)` annotation cannot exempt a row).
    for sid, row in rows:
        lowered = row.lower()
        retired = [w for w in _RETIREMENT_WORDS if w in lowered]
        if retired:
            problems.append(
                f"{sid}'s declared row says the stage is retired ({retired}): "
                f"{row.strip()[:90]} — if it really is retired, the row and this "
                "guard retire together (§4.3 G5: removal is a migration "
                "deliverable); a row that says so while the pipeline still "
                "runs the stage is the #11002 disagreement itself"
            )
    return problems


def test_the_step_blocks_declare_the_stages_the_pipeline_runs(canonical, ordering):
    """#11002 — the omission was invisible, not merely present.

    `#11517`/`#11554` shipped the retry and the ADD stage, and neither appeared
    in §4.1 or §4.2 because `_STEP_ID` could not parse `S2.2b-ADD` /
    `S2.2b-RE-NARRATE`.

    ⚠️ **EVERY stage the body-order guard compares must be present**, not only the
    two new ids: `_order_problems` compares the ids that SURVIVE in the document,
    so a stage deleted from both blocks passed on both sides of the comparison —
    measured: deleting both `S3  RESOLVE` rows left the whole file green, from a
    test whose name promises presence coverage (`#11002` review, cycle 3). §4.2's
    ordering table carries no plain `S0` row (only `S0a`/`S0b`), so `S0` is
    required in §4.1 only.
    """
    problems: list[str] = []
    problems += _declared_row_problems(canonical, tuple(_CODE_MARKERS))
    problems += _declared_row_problems(
        ordering, tuple(sid for sid in _CODE_MARKERS if sid != "S0")
    )
    assert not problems, (
        f"a declared block no longer names a stage the pipeline runs: {problems}. "
        "The declaration must name the stages the code performs — and the id "
        "grammar (`_STEP_ID`) must be able to parse each label (#11002)."
    )
    # Non-vacuity: dropping the `S3` row from EITHER block must red…
    assert _presence_problems(
        [i for i, _ in canonical if i != "S3"], tuple(_CODE_MARKERS)
    ), "deleting the S3 row from §4.1 MUST red the presence check"
    # …and a row that RETIRES a stage it declares must red.
    poisoned = [(sid, row + " RETIRED — no longer run") for sid, row in canonical]
    assert _declared_row_problems(poisoned, tuple(_CODE_MARKERS)), (
        "a declared row that retires its own stage MUST red"
    )
    # The JOIN is non-vacuous: both rows this change adds are TWO-LINE rows, and
    # the retirement claim goes on the continuation — where a first-line-only
    # read is blind (`#11002` review, cycle 7).
    add_row = next(row for sid, row in canonical if sid == "S2.2b-ADD")
    assert "no candidate covers" in add_row, (
        "the parsed S2.2b-ADD row does not carry its continuation line, so a "
        "retirement sentence written there is invisible to every scan"
    )
    continuation_retired = [
        (sid, row.replace("no candidate covers", "RETIRED — never runs"))
        if sid == "S2.2b-ADD"
        else (sid, row)
        for sid, row in canonical
    ]
    assert _declared_row_problems(continuation_retired, tuple(_CODE_MARKERS)), (
        "a retirement claim on a declared row's CONTINUATION must red"
    )
    # …and on an id the body-order guard does NOT map (`S6` runs too).
    unmapped_retired = [
        (sid, row + " GONE — not shipped") if sid == "S6" else (sid, row)
        for sid, row in canonical
    ]
    assert _declared_row_problems(unmapped_retired, tuple(_CODE_MARKERS)), (
        "a retirement claim on a declared row outside `_CODE_MARKERS` must red — "
        "`S6`, `S2.2b`, `S2.1` and `S2.2a` all run"
    )
    # …and a PROPOSAL row is NOT exempt: `S0a`/`S0b` are annotated as proposals
    # while `config/v4-mechanisms.yml` carries `source_dedup` as `implemented`, so
    # the annotation is a design-status marker and cannot stand in for code
    # status (`#11002` review, cycle 9).
    proposal_retired = [
        (sid, row + " RETIRED — never shipped") if sid == "S0b" else (sid, row)
        for sid, row in canonical
    ]
    assert proposal_retired != canonical, "no S0b row in §4.1 to mutate"
    assert _declared_row_problems(proposal_retired, tuple(_CODE_MARKERS)), (
        "a retirement claim on the S0b row must red — `source_dedup` is "
        "`state: implemented` in config/v4-mechanisms.yml"
    )
    # A column-0 line under a row is neither a row nor a continuation, so the
    # parser must SURFACE it rather than drop it (cycle-8 review).
    strays = [
        sid
        for sid, _ in _fenced_rows(CANONICAL_HEADING)
        if sid.startswith("<unparsed")
    ]
    assert not strays, f"the shipped block already carries stray lines: {strays}"


def test_the_declared_order_is_the_order_the_pipeline_runs(canonical, pipeline_body):
    """The order assertion, on the REAL document and the REAL function."""
    problems = _order_problems(pipeline_body, _declared_ids(CANONICAL_HEADING))
    assert not problems, "; ".join(problems)


def test_the_gap_fill_precedes_the_stage_it_fills(pipeline_body):
    """#11002's second half, stated on its own: relocation means BEFORE."""
    assert pipeline_body.index("run_partial_gap_fill(") < pipeline_body.index(
        LIVE_S4_MARKER
    ), (
        "`run_partial_gap_fill` (S2.2b-ADD) does not precede `run_s4`: the "
        "relocation that lets S4 leave the step list has been undone (#11002)."
    )


def test_the_renarrate_precedes_the_add_stage(pipeline_body):
    """The ADD stage reads the WINNING narrative, so it must follow the PICK.

    Pinned to `_renarrate_pick`, not `_renarrate_trigger`: the trigger only
    decides, so a gap fill moved between the trigger and the pick would address
    spans of the FIRST arm's narrative — the exact bug this asserts against —
    while a trigger-based pin stayed green (`#11002` review, finding 1).
    """
    assert pipeline_body.index("_renarrate_pick(") < pipeline_body.index(
        "run_partial_gap_fill("
    ), (
        "the gap fill now runs before the re-narrate's pick: it would address "
        "spans of a narrative the run may discard, and the winning arm's own "
        "gap would never be filled (#11002)."
    )


def test_the_live_s4_stage_sits_between_s3_and_s5(pipeline_body):
    """S4 is not a declared step, so its POSITION is the only thing pinning it."""
    body = pipeline_body
    assert (
        body.index("search_graph(")
        < body.index(LIVE_S4_MARKER)
        < body.index("execute_embed(")
    ), (
        "the live S4 stage has left its position between S3 `search_graph` and "
        "S5 `execute_embed` — §4.3 G5's ordering obligation and the drift "
        "marker's stage-order row both describe it there (#11002)."
    )


def _unparsed_step_rows(body: str) -> list[str]:
    """Rows that claim to be steps but that `_STEP_ID` refuses.

    A regex over free prose cannot accept every id spelling, so the residual is
    closed by REDDENING on an unparsed step-shaped row rather than by silently
    dropping it — silently dropping it is the exact mechanism that let two
    shipped stages go undeclared (`#11002` review, finding 5).
    """
    return [
        line
        for line in body.splitlines()
        if _STEP_SHAPED.match(line) and not _STEP_ID.match(line)
    ]


def test_every_step_shaped_row_parses():
    """No row may be skipped for starting like a step and parsing as none.

    Two shapes, both silent before this: a row whose ID the grammar refuses, and
    a line inside the block that is neither a row nor an indented continuation
    (`_fenced_rows` now surfaces the second as an `<unparsed …>` row rather than
    dropping it — a retirement sentence at column 0 stayed green, `#11002`
    review, cycle 8).
    """
    for heading in (CANONICAL_HEADING, ORDERING_HEADING):
        bad = _unparsed_step_rows(_fenced_body(heading))
        assert not bad, (
            f"{heading}: row(s) start like a step id but `_STEP_ID` refuses "
            f"them, so they declare nothing: {bad}. Spell the id so it parses "
            "(or extend the grammar AND this guard's marker map) — a row no "
            "parser sees is the #11002 defect itself."
        )
        stray = [sid for sid, _ in _fenced_rows(heading) if sid.startswith("<unparsed")]
        assert not stray, (
            f"{heading}: line(s) inside the block are neither a step row nor an "
            f"indented continuation, so they are attached to nothing: {stray}. "
            "Indent them under the row they belong to, or give them a step id."
        )
    # Non-vacuity: an id the grammar genuinely cannot spell must be reported…
    assert _unparsed_step_rows("S2.2c-mentions  a stage the grammar refuses"), (
        "the unparsed-row check must red on an id the grammar cannot match"
    )
    # …and ids the grammar accepts must not be flagged.
    for ok in ("S3  RESOLVE", "S2.2b-ADD  parses", "S2.2b-RE-NARRATE  parses"):
        assert not _unparsed_step_rows(ok), f"false positive on {ok!r}"


def _gate_order_problems(ids: list[str]) -> list[str]:
    """The two S2.2 sub-steps must be DECLARED after the gate row, before S2.3."""
    problems: list[str] = []
    if GATE_ID not in ids or "S2.3" not in ids:
        return [f"the blocks no longer carry {GATE_ID!r} and/or 'S2.3'"]
    gate, classify = ids.index(GATE_ID), ids.index("S2.3")
    for sid in ("S2.2b-RE-NARRATE", "S2.2b-ADD"):
        if sid not in ids:
            problems.append(f"{sid} is not declared")
            continue
        at = ids.index(sid)
        if not gate < at < classify:
            problems.append(
                f"{sid} is declared at index {at}, outside ({gate}, {classify}) — "
                "the ADD stage must not be placed before the gate: a stage that "
                "ADDED before VET would put candidates in front of the gate "
                "that VET never saw"
            )
    return problems


#: The drift marker's `stage order` row — the ONE line that states the shipped
#: pipeline's order for a reader of the step list. It is a blockquote table row,
#: so the fenced-block guards never read it; a cycle-2 review measured that
#: reverting it to its pre-`#11517` wording left every test green (`#11002`).
STAGE_ORDER_ANCHOR = "> | stage order |"

#: Whitespace- and spelling-tolerant: a second row written `>  | stage order |`,
#: `> | **stage order** |` or `> | Stage order |` is the same claim to a
#: reader, so it must be the same row to this guard (`#11002` review, cycles 7-8).
_STAGE_ORDER_ROW = re.compile(
    r"^\s*>\s*\|\s*\**\s*stage order\s*\**\s*\|", re.IGNORECASE
)


def _stage_order_rows(text: str | None = None) -> list[str]:
    """EVERY `stage order` row — a second, stale one must not hide behind the first.

    `.index`-style single-match reads were this guard's own defect class twice
    (`#11002` review, cycle 5's added call site and cycle 6's added row): a
    document that publishes the pre-`#11517` order in a second row would leave
    the first row reading correct and every test green.
    """
    source = DOC.read_text(encoding="utf-8") if text is None else text
    return [
        line for line in source.splitlines() if _STAGE_ORDER_ROW.match(line)
    ]


def _stage_order_row(text: str | None = None) -> str:
    rows = _stage_order_rows(text)
    assert len(rows) == 1, (
        "the document must carry EXACTLY ONE `stage order` row; found "
        f"{len(rows)}. A second row lets a stale order be published behind the "
        "first, which is the #11002 defect with a working guard in front of it."
    )
    return rows[0]


#: The stages the row is expected to name, in the order the body runs them.
#: `S0` is excluded only because the row starts at `_arm_extract`; it is pinned
#: against the body by `test_the_declared_order_is_the_order_the_pipeline_runs`.
_ROW_STAGES = tuple(sid for sid in _CODE_MARKERS if sid != "S0")


#: The row's OWN token for the live stage. Pinned in this exact form because a
#: substring test (`"run_s4" in row`) is satisfied by prose asserting the
#: OPPOSITE — `**\`run_s4\`** was RETIRED here` measured green before this
#: (`#11002` review, cycle 4).
S4_ROW_TOKEN = "**`run_s4`**"

#: Words that turn a stage mention into a retirement claim. BOUNDED on purpose:
#: it is the list this guard implements, not a claim that no synonym exists
#: (`#11002` review, cycle 7: `DELETED`, `SUPERSEDED`, `DEPRECATED` escape it).
_RETIREMENT_WORDS = ("retired", "removed", "gone", "no longer", "not run")


def _row_order_problems(row: str, body: str) -> list[str]:
    """The stage-order row must NAME each shipped stage, in the source order."""
    problems: list[str] = []
    # The GATE is not in `_CODE_MARKERS` (its pre-S3 site is inside
    # `_arm_extract`), so it is required here by name: the row is the one place a
    # reader learns the sub-steps run AFTER the gate (`#11002` review, cycle 3).
    if "S2.2 VET" not in row:
        problems.append("the stage-order row no longer names `S2.2 VET`")
    missing = [sid for sid in _ROW_STAGES if sid not in row]
    if missing:
        return [*problems, f"the stage-order row does not name: {missing}"]
    at = {sid: row.index(sid) for sid in _ROW_STAGES}
    body_order = sorted(_ROW_STAGES, key=lambda s: body.index(_CODE_MARKERS[s]))
    row_order = sorted(_ROW_STAGES, key=lambda s: at[s])
    if row_order != body_order:
        problems.append(
            f"the stage-order row publishes {row_order} but the pipeline runs "
            f"{body_order}"
        )
    # The row's OWN internal claim: the gate runs INSIDE the arm. Pinned by
    # POSITION only — the row has no closing parenthesis to read, so this
    # catches `S2.2 VET` placed before the retry but not "inside the arm" in a
    # strict syntactic sense (`#11002` review, cycle 9).
    if "S2.2 VET" in row and not (
        row.index("_arm_extract") < row.index("S2.2 VET") < at["S2.2b-RE-NARRATE"]
    ):
        problems.append(
            "the stage-order row no longer places `S2.2 VET` AFTER `_arm_extract` "
            "and before the retry — the retry IS the gate's action"
        )
    if S4_ROW_TOKEN not in row:
        problems.append(
            f"the stage-order row no longer shows the live `{S4_ROW_TOKEN}`"
        )
    else:
        s4_at = row.index(S4_ROW_TOKEN)
        tail = row[s4_at + len(S4_ROW_TOKEN) : s4_at + len(S4_ROW_TOKEN) + 60].lower()
        if any(word in tail for word in _RETIREMENT_WORDS):
            problems.append(
                "the stage-order row says the live S4 is retired while the "
                "pipeline still calls `run_s4(`"
            )
        if not at["S3"] < s4_at < at["S5"]:
            problems.append(
                "the stage-order row does not place the live S4 between S3 and S5"
            )
    return problems


def test_the_stage_order_row_matches_the_pipeline(pipeline_body):
    """The row is READ, not merely written — it was the stale artifact itself."""
    problems = _row_order_problems(_stage_order_row(), pipeline_body)
    assert not problems, "; ".join(problems)

    # Non-vacuity: the pre-`#11517` wording must red (it omits both sub-steps).
    old = (
        "> | stage order | the eight steps above | `run_s1` → `run_s2` → "
        "`S2.2 VET` → `S3 search_graph` → **`run_s4`** → `S5` |"
    )
    assert _row_order_problems(old, pipeline_body), (
        "the stale pre-`#11517` row MUST red the row check"
    )


def test_every_order_branch_has_a_mutation(pipeline_body):
    """Every branch that can be a SOLE discriminator must RED on a mutation.

    Added after a cycle-4 review measured that FIVE of these checks could be
    deleted with the whole file green. The mutation base is a SYNTHETIC row, not
    the document's: a base taken from the document broke whenever the row was
    reworded, which is itself a false red (`#11002` review, cycle 5).
    """
    row = (
        "> | stage order | the eight steps above | `_arm_extract` (S1 → S2 → "
        "**`S2.2 VET`**) → **`S2.2b-RE-NARRATE`** → **`S2.2b-ADD`** → "
        "`S2.3 CLASSIFY` → `S3 search_graph` → **`run_s4`** → `S5 execute_embed` |"
    )
    body = pipeline_body
    ordering_ids = _declared_ids(ORDERING_HEADING)
    required_ordering = tuple(sid for sid in _CODE_MARKERS if sid != "S0")
    assert not _row_order_problems(row, body), (
        "the synthetic base row must be clean, or the mutations below cannot be "
        "attributed to them"
    )

    row_cases = {
        "row: gate not named": row.replace("S2.2 VET", "the gate"),
        "row: gate before the arm": "S2.2 VET → " + row,
        "row: a stage not named": row.replace("`S2.3 CLASSIFY`", "`classify`"),
        "row: stage order swapped": row.replace(
            "`S2.3 CLASSIFY` → `S3 search_graph`",
            "`S3 search_graph` → `S2.3 CLASSIFY`",
        ),
        "row: S4 token weakened": row.replace(S4_ROW_TOKEN, "`run_s4`"),
        "row: S4 claimed retired": row.replace(
            S4_ROW_TOKEN, S4_ROW_TOKEN + " was RETIRED here"
        ),
        "row: S4 moved before S3": row.replace(
            "`S3 search_graph` → " + S4_ROW_TOKEN,
            S4_ROW_TOKEN + " → `S3 search_graph`",
        ),
    }
    for name, mutated in row_cases.items():
        assert mutated != row, f"mutation {name!r} did not apply — re-point it"
        assert _row_order_problems(mutated, body), f"mutation {name!r} stayed green"

    # The §4.2 half of the presence check (the cycle-3 fix proved §4.1 only).
    assert _presence_problems(
        [i for i in ordering_ids if i != "S3"], required_ordering
    ), "deleting the S3 row from §4.2 MUST red the presence check"

    # The gate-placement helper's THREE branches.
    assert _gate_order_problems(
        ["S0", "S1", "S2.2b-RE-NARRATE", "S2.2b-ADD", GATE_ID, "S2.3"]
    ), "declaring the sub-steps before the gate MUST red"
    assert _gate_order_problems(
        ["S0", "S1", GATE_ID, "S2.3", "S2.2b-RE-NARRATE", "S2.2b-ADD"]
    ), "declaring the sub-steps after S2.3 MUST red"
    assert _gate_order_problems(["S0", "S1", GATE_ID]), (
        "a block with no S2.3 row MUST red the gate-placement check"
    )
    assert _gate_order_problems(["S0", "S1", GATE_ID, "S2.3"]), (
        "a block missing a sub-step MUST red the gate-placement check"
    )

    # The body check's early return (a marker gone).
    assert _order_problems(body.replace("run_partial_gap_fill(", "@@X(", 1), list(_CODE_MARKERS)), (
        "a missing call-site marker MUST red the body order check"
    )

    # The body check's two `run_s4`-position branches, and the added-site guard.
    renamed = body.replace(LIVE_S4_MARKER, "@@S4@@(", 1)
    assert renamed != body, "the S4 rename mutation did not apply"
    before_s3 = renamed.replace(
        "search_graph(",
        "run_s4(model, story, search, embed_list)\n    search_graph(",
        1,
    )
    assert _order_problems(before_s3, list(_CODE_MARKERS)), (
        "a `run_s4` call placed before S3 MUST red the order check"
    )
    line_end = renamed.index("\n", renamed.index("execute_embed(")) + 1
    after_s5 = (
        renamed[:line_end]
        + "    run_s4(model, story, search, embed_list)\n"
        + renamed[line_end:]
    )
    assert _order_problems(after_s5, list(_CODE_MARKERS)), (
        "a `run_s4` call placed after S5 MUST red the order check"
    )
    s4_line_end = body.index("\n", body.index(LIVE_S4_MARKER)) + 1
    added_second_fill = (
        body[:s4_line_end]
        + "    added_late = run_partial_gap_fill(model, story, embed_list)\n"
        + body[s4_line_end:]
    )
    assert _order_problems(added_second_fill, list(_CODE_MARKERS)), (
        "an ADDED second gap-fill call site MUST red — a moved call is caught "
        "by the order comparison, an added one only by the site count"
    )
    # The `run_s4`-count branch on its own: an ADJACENT duplicate leaves the
    # order untouched, so ONLY the count can fire (cycle-6 review).
    s4_start = body.index("        s4 = " + LIVE_S4_MARKER)
    doubled_s4 = body[:s4_line_end] + body[s4_start:s4_line_end] + body[s4_line_end:]
    assert _order_problems(doubled_s4, list(_CODE_MARKERS)), (
        "a DUPLICATED `run_s4(` call must red: order is unchanged, so only the "
        "call-site count can catch it"
    )
    # A second `stage order` row must not hide behind the first — and a
    # WHITESPACE variant is the same claim to a reader, so it must be the same
    # row to the guard.
    for second in ("> | stage order | `run_s1` → `run_s2` → `S3` → `run_s4` → `S5`",
                   ">  | stage order | `run_s1` → `run_s2` → `S3` → `run_s4` → `S5`",
                   "> |  stage order | `run_s1` → `run_s2` → `S3` → `run_s4` → `S5`",
                   ">|stage order|`run_s1` → `S3`",
                   "> | Stage order | `run_s1` → `S3`",
                   "> | **stage order** | `run_s1` → `S3`"):
        try:
            _stage_order_row(_stage_order_row() + "\n" + second)
        except AssertionError as exc:
            assert "EXACTLY ONE" in str(exc), exc
        else:
            raise AssertionError(
                f"a second `stage order` row ({second[:20]}…) must red"
            )


def test_the_new_substeps_are_declared_after_the_gate(canonical, ordering):
    """Declaration-level placement, because the gate has no pipeline-body marker."""
    for label, rows in ((CANONICAL_HEADING, canonical), (ORDERING_HEADING, ordering)):
        problems = _gate_order_problems([i for i, _ in rows])
        assert not problems, f"{label}: " + "; ".join(problems)
    # Non-vacuity: a CONSISTENT move of both rows above the gate must red.
    assert _gate_order_problems(
        ["S0", "S1", "S2.2b-RE-NARRATE", "S2.2b-ADD", GATE_ID, "S2.3"]
    ), "declaring the two sub-steps before the gate MUST red this check"


def test_the_order_checks_are_not_vacuous(pipeline_body):
    """Non-vacuity, the way this file's neighbours do it: mutate, then assert.

    Four mutations, one per failure mode the body-order guard exists for: a
    SWAP of two declared stages, the gap fill moved BEFORE the pick, the gap
    fill moved BEHIND the stage it fills, and S4 removed without its records
    retired.
    """
    # The DECLARED list here is literal on purpose: this is a unit test of the
    # CHECKER, so it must not inherit the document's state (a document that is
    # missing an id would otherwise make the swap look clean — the vacuity this
    # test exists to prevent).
    declared = ["S0", "S1", "S2.2b-RE-NARRATE", "S2.2b-ADD", "S2.3", "S3", "S5"]
    assert not _order_problems(pipeline_body, declared), (
        "the unmutated pipeline must be clean, or the mutations below cannot be "
        "attributed to them"
    )

    swapped = pipeline_body.replace("_renarrate_pick(", "@@A@@", 1).replace(
        "run_partial_gap_fill(", "_renarrate_pick(", 1
    ).replace("@@A@@", "run_partial_gap_fill(", 1)
    assert swapped != pipeline_body, "the swap mutation did not apply"
    assert _order_problems(swapped, declared), (
        "swapping the re-narrate's pick and the gap fill MUST red the order check"
    )

    # The reviewer's own mutation (`#11002` review, finding 1): the gap fill moved
    # BETWEEN the trigger and the pick, i.e. before the winning narrative is
    # bound. A trigger-keyed marker passed this; a pick-keyed one must not.
    early = pipeline_body.replace(
        "run_partial_gap_fill(model, story, embed_list,",
        "@@RELOCATED@@(model, story, embed_list,",
        1,
    )
    assert early != pipeline_body, "the early-move mutation's rename did not apply"
    anchor = "            second = _arm_extract("
    assert anchor in early, "the retry-arm call site moved — re-point this mutation"
    early = early.replace(
        anchor, "            run_partial_gap_fill(model, story, embed_list)\n" + anchor, 1
    )
    assert _order_problems(early, declared), (
        "a gap fill placed before the re-narrate's PICK must red: it would read "
        "the first arm's narrative, which the run may discard"
    )

    moved = pipeline_body.replace(
        "run_partial_gap_fill(model, story, embed_list,",
        "@@RELOCATED@@(model, story, embed_list,",
        1,
    )
    assert moved != pipeline_body, "the move mutation's rename did not apply"
    cut = moved.index(LIVE_S4_MARKER)
    line_end = moved.index("\n", cut) + 1
    moved = (
        moved[:line_end]
        + "        run_partial_gap_fill(model, story, embed_list)\n"
        + moved[line_end:]
    )
    assert _order_problems(moved, declared), (
        "moving the gap fill AFTER `run_s4` MUST red the order check — that is "
        "the relocation #11002 exists to make permanent"
    )

    removed = pipeline_body.replace(LIVE_S4_MARKER, "@@GONE@@(", 1)
    assert removed != pipeline_body, "the removal mutation did not apply"
    assert _order_problems(removed, declared), (
        "deleting the live S4 call MUST red the guard rather than let it pass "
        "vacuously: the records that describe S4 have to be retired with it"
    )

