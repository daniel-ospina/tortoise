"""Tests for tools/issue_process_lint.py — the two advisory arms, their negative controls, and the
adversarial shapes a first version of this tool got wrong.

RED-FIRST NOTE (why each arm has a pair)
----------------------------------------
The tool exists to flag exactly two shapes and to *not* flag two others. A test file that only
proved the flags would be satisfied by a tool that flags everything, so every arm is paired with the
legitimate population it must leave alone:

    flagged:        PR CLAIMS an issue, nothing closes it            <->  control: claims none
    flagged:        issue has WIP, no `## Satisfies` checklist           <->  control: fresh issue

The pairs are the requirement, not decoration: a false positive points bureaucracy at legitimate
work, which is worse than the defect it would catch. A review of the first version measured 19 of
28 live open PRs flagged (68 %) because it counted *mentions* — criterion ids, a PR's own number, a
cited precedent, a command in inline code — so the CLAIM-vs-MENTION tests below are regression pins
for a real, measured false-positive population.

Nothing here needs a database, and nothing here touches the network: the `gh` seam is monkeypatched
(or given an impossible repo) so `rc=2 != rc=0` is pinned without a live call.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "issue_process_lint.py"
sys.path.insert(0, str(ROOT / "tools"))

import issue_process_lint as L  # noqa: E402


# --- arm 1: a PR that CLAIMS an issue which nothing resolves ------------------------------
def test_a_pr_that_only_says_refs_is_flagged():
    """The measured shape: 18 of 18 P0/P1-naming PRs used `Refs` (#10534)."""
    findings = L.classify_pr("This fixes the thing.\n\nRefs #10534\n", 1234)
    assert len(findings) == 1
    assert findings[0].arm == L.ARM_PR
    assert findings[0].evidence["unresolved"] == [10534]


@pytest.mark.parametrize("body", [
    "Closes #10534\n",
    "closes #10534\n",
    "Fixes #10534\n",
    "Resolves: #10534\n",
    "Part of the programme.\n\nRefs #4282 and Closes #4282\n",
])
def test_a_closing_keyword_resolves_the_claim(body):
    assert L.classify_pr(body, 1) == []


def test_satisfies_resolves_the_claim_for_a_partial_pr():
    """`Satisfies #N: <ids>` is what a partial PR writes instead of `Closes`."""
    assert L.classify_pr("Satisfies #4282: 3.2a, 3.2b\nRefs #4282\n", 1) == []


def test_a_keyword_for_a_different_issue_does_not_resolve_the_claimed_one():
    """THE #10534 SHAPE EXACTLY: a closing keyword elsewhere does not close the issue it serves."""
    findings = L.classify_pr("Refs #4282\n\nCloses #9999\n", 1)
    assert [f.evidence["unresolved"] for f in findings] == [[4282]]
    assert findings[0].evidence["resolved_by_closing"] == [9999]


def test_a_pr_that_claims_no_issue_is_not_flagged():
    """NEGATIVE CONTROL: not every change serves an issue. Flagging this is the bureaucracy."""
    body = "chore: bump the pinned ruff version\n\nNo issue: a dependency bump.\n"
    assert L.classify_pr(body, 999) == []


# --- mention vs claim: the measured 68 % false-positive population -----------------------
@pytest.mark.parametrize("body,why", [
    ("This reds only #10 and #11, the two criterion ids.\n", "criterion id in prose"),
    ("Pins the source count (#9) and the guard #2981 cited above.\n", "cited precedent"),
    ("Ported from the review on #11064.\n", "its own PR number, quoted"),
    ("A fence example:\n\n```markdown\nRefs #1234\n```\n\nNo claim here.\n", "fenced quote"),
    ("Run `gh pr view #1234` to see it.\n", "inline code"),
    ("<!-- tracking: Refs #4282 -->\nNothing claimed.\n", "HTML comment"),
])
def test_a_mention_is_not_a_claim(body, why):
    assert L.classify_pr(body, 11064) == [], why


def test_a_declined_closing_reference_is_the_authors_answer_not_a_defect():
    """PR #11054, measured: `Refs #10602 — deliberately **not** `Closes`.` A measurement PR that
    serves an issue without fixing it says so, and that sentence must clear the flag."""
    body = "Refs #10602 — deliberately **not** `Closes`.\n"
    assert L.classify_pr(body, 11054) == []
    # the same sentence on the next line does NOT clear a different line's claim
    assert [f.evidence["unresolved"] for f in L.classify_pr("Refs #1\n\nNot closing this one.\n", 2)] == [[1]]


@pytest.mark.parametrize("tail", [
    "the lint is deliberately not a gate.",
    "deliberately not the goal of this PR.",
    "deliberately not in scope here.",
    "deliberately not the thing this measures.",
])
def test_an_incidental_deliberately_not_does_not_delete_the_claim(tail):
    """Review F1, measured at `aade694c7`: the bare `deliberately{_EMPH}not` alternative matched
    ANY occurrence of the phrase, and the decline is applied per LINE to every ref on it — so an
    honest sentence *about the work* (`…is deliberately not a gate.`) silently deleted the claim
    beside it and the arm read CLEAN.

    That is a false PASS in the #10534 direction (delivered work never closes, and nothing says
    so), the one this module's docstring ranks worst — worth a false positive nowhere.
    """
    body = f"Refs #11077 — {tail}\n"
    assert L.claims(body, 1)["declined"] == [], tail
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11077]], tail


def test_the_decline_still_works_when_the_quoted_keyword_has_been_blanked():
    """The control for the F1 fix, and the reason the alternative could not simply be deleted.

    `strip_code` blanks the inline-code `Closes` in the pinned case, so what DECLINE_RE sees is
    `deliberately **not**   .` — no closing word left for the sibling `not … clos` alternative. A
    decline that NAMES the keyword in prose must keep working too.
    """
    assert L.claims("Refs #10602 — deliberately **not** `Closes`.\n", 1)["declined"] == [10602]
    assert L.claims("Refs #10602 — deliberately not a closing one.\n", 1)["declined"] == [10602]
    assert L.claims("Refs #10602 — deliberately not Closes.\n", 1)["declined"] == [10602]
    assert L.classify_pr("Refs #10602 — deliberately not a closing one.\n", 1) == []


@pytest.mark.parametrize("body,why", [
    ("Refs #1234 — the estimate does not close the delta.", "`does not close` + a transitive object"),
    ("Refs #1234 — mitigates the symptom; does not close the root.", "the phrase follows a semicolon"),
    ("Refs #1234 — the numbers are not close to the target.", "`not close` + a preposition"),
    ("Refs #1234 — no closing costs involved.", "`no closing` + an object"),
    ("Refs #1234 — non-closing reference, see the review.", "`non-closing` + a noun"),
])
def test_ordinary_closing_prose_does_not_delete_the_claim(body, why):
    """F1, the remainder the round before left unanchored — measured at `fb2913a966`.

    Round 11 anchored the `deliberately not` alternative and left the other four (`does not clos`,
    `not … clos`, `no closing`, `non-closing`) matching ANYWHERE on the line. Because
    `declined_lines` is computed per line and applied to EVERY ref on that line, each of the five
    bodies below read CLEAN — the claim was silently dropped — while the bare control
    `Refs #1234` flagged. That is a false PASS in the #10534 direction, the one this module ranks
    worst; every one of these bodies is prose about something OTHER than the reference.

    GitHub does not honour a decline either (its closing-keyword match does not understand
    negation), so "the author answered" is true only when the sentence really is about the
    reference — which is exactly what these five do not require. All five are RED against the
    pre-fix regex (verified by re-running them with the old `DECLINE_RE` swapped back in:
    5 failed / 0 passed).
    """
    assert L.claims(body + "\n", 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body + "\n", 1)] == [[1234]], why


@pytest.mark.parametrize("body,why", [
    ("Refs #1234 — this does not close the issue.", "clause-terminal, naming the reference"),
    ("Refs #1234 — not a closing one.", "article + closing word + reference noun"),
    ("Refs #1234 — non-closing keyword.", "compound adjective + reference noun"),
    ("Refs #1234 — non-closing.", "compound adjective, clause-terminal"),
    ("Refs #1234 — no closing.", "`no closing`, clause-terminal"),
    ("Refs #1234 — does not close.", "`does not close`, clause-terminal"),
])
def test_a_statement_about_the_reference_is_still_a_decline(body, why):
    """The other side of the same narrowing: an author who says the reference is not closing is
    answering, and flagging them punishes exactly the honesty the process block asks for.

    The tail admitted after the closing word is the REFERENCE and nothing else — that closed list
    is what separates `does not close the issue` (a decline) from `does not close the delta`
    (prose), and it is why this is a grammar rather than a wider word list.
    """
    assert L.claims(body + "\n", 1)["declined"] == [1234], why
    assert L.classify_pr(body + "\n", 1) == [], why


@pytest.mark.parametrize("body,why", [
    ("Refs #1234 — the sprint is not closed.", "an ordinary subject ('the sprint')"),
    ("Refs #1234 — the trading window is not closed.", "an ordinary subject ('the window')"),
    ("Refs #1234 — the incident is not resolved.", "`resolv` in a decline shape"),
    ("Refs #1234 — the outage on the other box is not fixed.", "a long subject phrase"),
    ("Refs #1234 — the flaky test is not fixed.", "a subject with an adjective"),
])
def test_a_negated_closing_verb_about_another_subject_does_not_delete_the_claim(body, why):
    """Measured at `d580a3401` (review, P2): anchoring the negation's TAIL left its SUBJECT free.

    `DECLINE_RE`'s ordinary-English alternative only ever checked what FOLLOWED the closing word, so
    any `<not|no|non-> <closing-verb>.` at end of line matched — and because a decline cleared every
    ref on its line, `Refs #1234 — the sprint is not closed.` read **CLEAN** while the bare control
    `Refs #1234` flagged. Appending one word restores the flag (`… not closed yet.` -> rc 3), which
    is what shows the drop was the tail anchor and not the sentence.

    These are false PASSes in the #10534 direction (delivered work never closes, and nothing says
    so) — the direction this module's docstring ranks worst. The bridge between the reference and
    the negation now admits punctuation, a one-word subject OF THE REFERENCE (`this`/`it`/`that`)
    and a copula; a real subject noun there is prose about that noun, so the claim survives.
    """
    assert L.claims(body + "\n", 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body + "\n", 1)] == [[1234]], why


def test_a_decline_clears_only_the_reference_it_is_about():
    """Measured at `d580a3401` (review, P2): the decline was applied PER LINE to EVERY ref on it.

    Round 12 fixed *when* the phrase matches and left the amplification alone, so the repo's own
    mandated words cleared a SIBLING claim: in `Refs #100, #200 — #200 is deliberately not Closes.`
    the `#200` sentence removed `#100`'s unresolved claim too, and the run read CLEAN. `#100` is
    claimed and resolved by nothing — that is the whole defect this arm exists to report.

    Both directions are pinned: the declined ref still clears (so the refusal above is not a blanket
    one), and a claim on a DIFFERENT line is untouched.
    """
    body = "Refs #100, #200 — #200 is deliberately not Closes.\n"
    assert L.claims(body, 1)["declined"] == [200]
    assert [[f.evidence["unresolved"] for f in L.classify_pr(body, 1)]] == [[[100]]]
    # the control: when the sentence IS about the only reference, it clears it
    assert L.claims("Refs #1234 — #1234 is deliberately not Closes.\n", 1)["declined"] == [1234]
    assert L.classify_pr("Refs #1234 — #1234 is deliberately not Closes.\n", 1) == []


@pytest.mark.parametrize("body,why", [
    ("Refs #1234 — deliberately not closing it.", "pronoun object"),
    ("Refs #1234 — deliberately not closing #1234.", "the reference itself as the object"),
    ("Refs #1234 — deliberately not closing them.", "plural pronoun object"),
])
def test_the_object_of_an_honest_decline_may_be_the_reference_itself(body, why):
    """The false-positive half of the same round (review, P3), measured at `d580a3401`.

    The closed `_REF_NOUN` list admitted `the issue` / `a closing keyword` / `this one` but not `it`
    or a literal `#N` — so the two most natural ways to write "this PR does not close #N" were
    FLAGGED at that head, while the pre-round-12 grammar declined both. Flagging honest work is the
    direction this module treats as worse than the defect, so the object list gains them and the
    controls below prove the refusal is not blanket.
    """
    assert L.claims(body + "\n", 1)["declined"] == [1234], why
    assert L.classify_pr(body + "\n", 1) == [], why


def test_a_decline_that_names_its_object_clears_that_object_not_its_neighbour():
    """Measured at `e7dd41058` (review, P2): a NEW false PASS the round-13 widening introduced.

    Admitting a literal `#N` as the decline's object made `deliberately not closing #N` match, but
    the target was still resolved by PROXIMITY — so a sentence about `#11078` cleared the claim on
    `#11000`, the ref before it, and arm 1 read CLEAN. The module's own doctrine one line above the
    code says a decline "clears the refs it names and NO OTHERS"; a named object is the strongest
    statement of what the sentence is about, so it overrides proximity.
    """
    body = "Part of #11000 — deliberately not closing #11078.\n"
    # The decline belongs to `#11078`, so it says NOTHING about the `#11000` claim beside it — which
    # is therefore still claimed and still unresolved (rc 3). Before the fix this read CLEAN.
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]]
    # The positive control: when the named object IS one of the claims, exactly that one clears and
    # the sibling stays flagged.
    body2 = "Refs #100, #11078 — deliberately not closing #11078.\n"
    assert L.claims(body2, 1)["declined"] == [11078]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body2, 1)] == [[100]]
    body3 = "Refs #100 — deliberately not closing #999.\n"
    assert L.claims(body3, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body3, 1)] == [[100]]


@pytest.mark.parametrize("body,why", [
    ("Refs #1234 — deliberately does not close the issue.", "the convention word in idiomatic order"),
    ("Refs #1234 — this deliberately does not close the issue.", "subject + convention word"),
    ("Refs #1234 — this PR does not close the issue.", "a two-word subject"),
    ("Refs #1234 — we deliberately do not close the issue.", "first-person plural"),
    ("Refs #1234 — the author deliberately does not close the issue.", "article + agent + convention"),
])
def test_the_conventions_own_word_order_is_still_a_decline(body, why):
    """Measured at `e7dd41058` (review, P2): the round-13 bridge admitted exactly ONE subject word
    and ONE copula, so every natural way of writing the SAME honest sentence after it was FLAGGED.

    `deliberately` is called "the AUTHOR-FACING convention" by this module's own comment, and
    `Refs #1234 — this does not close the issue.` is one of the suite's own green controls —
    inserting the word the convention is NAMED AFTER flipped that control to a finding. The bridge
    now admits a short closed vocabulary (subject words, copulas, the article that introduces
    `the author`, and `deliberately` itself), which fixes these without re-admitting the round-12
    negatives (`the sprint is not closed`, `does not close the delta`).
    """
    assert L.claims(body + "\n", 1)["declined"] == [1234], why
    assert L.classify_pr(body + "\n", 1) == [], why


def test_a_decline_that_names_a_list_clears_every_reference_in_it():
    """Measured at `049548f2c` (review, P2): the object capture held ONE `#N`, so a written-out
    list contributed only its first ref and then returned — discarding the proximity target too.

    `Refs #100, #200 — deliberately not closing #100, #200.` declined `#100` and flagged `#200`,
    and `Part of #100 — deliberately not closing #200, #100.` (rc 0 at `e7dd41058`) became rc 3:
    a single-claim body whose decline explicitly names that claim was flagged. The object zone now
    takes every ref the clause names, and STOPS at a conjunction that starts a new sentence
    (`... Closes and #200 is deliberately not Closes.`) rather than continuing the list.
    """
    for body in ("Refs #100, #200 — deliberately not closing #100, #200.\n",
                 "Refs #100, #200 — deliberately not closing #100 and #200.\n"):
        assert sorted(L.claims(body, 1)["declined"]) == [100, 200], body
        assert L.classify_pr(body, 1) == [], body
    # a single claim whose decline names it, behind a ref that is NOT the decline's subject
    assert L.classify_pr("Part of #100 — deliberately not closing #200, #100.\n", 1) == []


@pytest.mark.parametrize("body,why", [
    ("Part of #11000 — deliberately not closing `#11078`.", "the object inside inline code"),
    ("Part of #11000 — deliberately not closing <!-- #11078 -->.", "the object inside an HTML comment"),
])
def test_an_object_the_scan_cannot_read_is_not_evidence_for_the_claim_before_it(body, why):
    """Measured at `049548f2c` (review, P3): `strip_code` blanks an inline-code span or an HTML
    comment to WHITESPACE, so the decline reached the target logic with its object GONE — and then
    fell through to proximity and cleared the ref BEFORE the sentence. Both bodies read
    `NOTHING FLAGGED`, rc 0, while the same sentence with a readable object flags.

    An unreadable object is no evidence at all: the decline gets no target and `#11000` stays
    flagged. The clause-terminal convention (`deliberately not Closes.`, `deliberately not
    closing.`) is unaffected — it has no object to lose, which is why it is pinned separately.
    """
    assert L.claims(body + "\n", 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body + "\n", 1)] == [[11000]], why


def test_a_named_list_does_not_sweep_in_the_next_CLAUSE_s_claim():
    """Measured at `2219dfe86` (review, P2): the object ZONE (a raw text scan to the clause end)
    continued past `and` whenever a ref sat nearby, so a claim introduced by a LATER clause was
    moved from `weak` to `declined` and disappeared — the silent-drop direction, introduced by the
    fix that closed the previous round.

    The object is captured by the GRAMMAR now (a list must continue with `#N` right after its
    separator), so `#200 and Refs #300` stops at `#200`: the `and` there joins clauses, not refs.
    """
    # `#200` is the decline's OBJECT, not a claim (no claiming word precedes it), so it is not in
    # `declined` — what matters is that `#300`, which IS claimed and resolved by nothing, was not
    # swept into the decline and dropped.
    a = "Closes #100 — deliberately not closing #200 and Refs #300 for the follow-up.\n"
    assert L.claims(a, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(a, 1)] == [[300]]
    b = "Refs #100 — deliberately not closing #200, and Refs #300 is separate.\n"
    assert L.claims(b, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(b, 1)] == [[100, 300]]


def test_a_context_reference_in_the_same_clause_does_not_steal_the_decline():
    """Measured at `2219dfe86` (review, P2): the zone added every ref in the clause to `targets`,
    so a context ref (`see #11078`) stole the decline from the ref the sentence was about and an
    HONEST decline was flagged — rc 0 at `049548f2c`, rc 3 at the head that introduced the zone.

    A noun object (`it`) names no reference, so the proximity rule applies and `#11000` clears.
    """
    assert L.classify_pr("Refs #11000 — deliberately not closing it, see #11078 for the review.\n",
                         1) == []
    assert L.classify_pr("Refs #11000 — deliberately not closing it; #11078 has the detail.\n",
                         1) == []


@pytest.mark.parametrize("tail,why", [
    (", the review issue.", "an ordinary appositive"),
    (", none of which is confirmed.", "`none` contains `one`"),
    (", my other PR.", "`PR` matches `pr`"),
    (", see the commit.", "`commit` contains `it`"),
])
def test_an_unreadable_object_stays_unreadable_when_the_sentence_continues(tail, why):
    """Measured at `2219dfe86` (review, P2): the unreadable-object guard searched `_REF_NOUN` over
    the WHOLE rest of the line, so one incidental reference noun — `none`, `PR`, `commit` — satisfied
    it and the blanked object fell through to proximity again, clearing the ref before the sentence.

    The test is now on the CAPTURED object, not on the rest of the line: a clause that continues
    with an appositive cannot tell us what was blanked, so the claim stays flagged.
    """
    body = "Part of #11000 — deliberately not closing `#11078`" + tail + "\n"
    assert L.claims(body, 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]], why


def test_a_blanked_object_followed_by_a_conjunction_is_still_unreadable():
    """The residual round 19 declared rather than fixed — and the class it belongs to.

    The unreadable-object guard admitted a bare `and`/`or` after the gap as a clause continuation,
    so a blanked object followed by PROSE read CLEAN while the claim before it was silently dropped
    (measured at `6d8e4ed8b`):

        Part of #11000 — deliberately not closing `#3300` and this one is separate.   -> rc 0

    That is the same silent drop as the appositive tails above, one word over, and the direction
    this module ranks worst. The conjunction is now admitted only when it actually introduces the
    next reference-naming clause. The pinned continuation
    (`test_two_declines_on_one_line_each_own_their_reference`) is the control that keeps the
    narrowing from deleting a real decline — asserted here too, so this case cannot be made green
    by closing the conjunction door altogether.
    """
    body = "Part of #11000 — deliberately not closing `#3300` and this one is separate.\n"
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]]

    control = ("Refs #100, #200 — #100 is deliberately not Closes and "
               "#200 is deliberately not Closes.\n")
    assert L.claims(control, 1)["declined"] == [100, 200]
    assert L.classify_pr(control, 1) == []


def test_a_decline_list_stops_at_the_clause_end():
    """Measured at `0e785300b` (review, P2): `_REF_LIST`'s separator was `_DECLINE_TAIL`, whose class
    contains `.` `;` `:` and `—` — the CLAUSE ENDS — so a list continued straight through a full stop
    and swept the NEXT sentence's reference into the decline, where it silently disappeared:

        Refs #300, #100 — deliberately not closing #100. #300     -> NOTHING FLAGGED, rc 0
        Part of #100 — deliberately not closing #100; #200        -> rc 0

    A list separator is a comma or a conjunction (`and`/`or`) and nothing else. The retained comment
    that claimed "the region stops at the clause end" was false at that head; the grammar now makes
    it true.
    """
    # The trailing `#300` is NOT swept into the decline any more, so the line no longer reports
    # CLEAN: the claim the author made and never resolved survives to be flagged. (The decline
    # itself is not recognised either, because the sentence has no clause terminator — the flag is
    # the safe direction for both refs.)
    body = "Refs #300, #100 — deliberately not closing #100. #300\n"
    assert L.claims(body, 1)["declined"] == [100]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[300]]


@pytest.mark.parametrize("body", [
    "Refs #100, #200 — deliberately not closing #100, #200.\n",
    "Refs #100, #200 — deliberately not closing #100 and #200.\n",
    "Refs #100, #200 — deliberately not closing #100 or #200.\n",
    "Refs #100, #200, #300 — deliberately not closing #100, #200, #300.\n",
])
def test_a_written_out_object_list_declines_every_ref_in_it(body):
    """`or` was missing from the separator while `and` was admitted, so the SAME honest sentence
    written with `or` was not a decline at all (measured at `0e785300b`, review P3)."""
    assert L.classify_pr(body, 1) == []


@pytest.mark.parametrize("body", [
    "Part of #100, #200, #300 — deliberately not closing #100, #200, and #300.\n",
    "Part of #100, #200, #300 — deliberately not closing #100, #200, or #300.\n",
    "Refs #100, #200 — deliberately not closing #100, #200. Done.\n",
])
def test_a_serial_comma_and_trailing_text_do_not_truncate_the_object_list(body):
    """Measured at `e960ed6d1` (review, 2 × P2), both in the lookahead that terminates the clause.

    The conjunction branch could not consume the comma of an Oxford list (`#100, #200, and #300`), so
    the list stopped after two refs and the third — which the author explicitly declined — was
    flagged; and `.` was missing from the terminator set, so any text AFTER the full stop truncated
    the list the same way. Both are the false-positive direction, and both were CLEAN at a previous
    head. The terminator is now `$`/`.`/`;`/`and`/`or`, with a comma accepted only when it does NOT
    start another ref (a list continuation is the object list's job, not the clause's).
    """
    assert L.classify_pr(body, 1) == [], body


@pytest.mark.parametrize("body,why", [
    ("Part of #1234 — deliberately not closing a closing one.", "article + closing stem + `one`"),
    ("Part of #1234 — deliberately not closing the closing ref.", "`the closing ref`"),
    ("Part of #1234 — deliberately not closing a resolving one.", "`resolving` is a closing stem"),
    ("Part of #1234 — deliberately not closing a closing issue.", "`a closing issue`"),
])
def test_the_decline_may_name_the_reference_by_its_kind(body, why):
    """Measured at `6d8e4ed8b` (review, P2 — the one finding that round declared, not fixed).

    The object slot was `_DET` immediately followed by `_REF_NOUN`, so a closing WORD between them
    had no route through the grammar, the object captured empty, and the unreadable-object guard
    suppressed the proximity path — the honest decline was FLAGGED (rc 3). The same sentence with
    the word `closing` in the other position (`deliberately not a closing one.`) declined, which is
    what makes this a grammar gap rather than a doctrine.

    The object phrase is strictly shaped — a determiner, a closing stem, one reference noun — so it
    cannot swallow a following clause; the control below pins that.
    """
    assert L.claims(body + "\n", 1)["declined"] == [1234], why
    assert L.classify_pr(body + "\n", 1) == [], why
    # the object phrase stops at the noun: the next clause's ref is not swept into the decline
    assert L.claims("Part of #100 — deliberately not closing a closing one and #300 is"
                    " separate.\n", 1)["declined"] == [100]


def test_a_plural_decline_clears_the_whole_list_it_names():
    """`them` was added as an object in round 13 but resolved by proximity, so a PLURAL sentence
    cleared one ref and flagged the other (measured at `e7dd41058`, review P3).

    `them` can only refer to the list it follows, so it takes the whole preceding run on the line.
    The singular control stays put: `it` is about one reference, by construction.
    """
    body = "Refs #100, #200 — deliberately not closing them.\n"
    assert L.claims(body, 1)["declined"] == [100, 200]
    assert L.classify_pr(body, 1) == []
    assert L.classify_pr("Refs #100, #200 — deliberately not closing it.\n", 1) != []


def test_a_plural_decline_clears_the_list_it_names_not_the_whole_line():
    """Measured at the merge head (review, P2): the plural branch took EVERY ref earlier on the
    physical line, so a list in a LATER sentence swept a previous sentence's claim into the
    decline and arm 1 read CLEAN — the silent-drop direction this module ranks worst.

        Refs #100. Refs #200, #300 — deliberately not closing them.   -> rc 0, `#100` dropped

    `them` cannot refer to `#100` there: a full stop is not a list separator. The run now walks
    back from the nearest reference and stops at the first non-list gap, so the decline is about
    `#200, #300` and `#100`'s claim survives to be flagged.

    Both directions are pinned together, because narrowing a sweep is exactly how the previous
    rounds traded one error for the other: the single-sentence LIST control must still clear every
    ref in it (the case round 13 added), and so must the `and`-spelled list.

    RED against the pre-fix tool: `declined == [100, 200, 300]` and no finding.
    """
    cross = "Refs #100. Refs #200, #300 — deliberately not closing them.\n"
    assert L.claims(cross, 1)["declined"] == [200, 300]
    assert [f.evidence["unresolved"] for f in L.classify_pr(cross, 1)] == [[100]]

    # The same shape through alternative (b): the ordinary-English spelling must not be weaker.
    cross_b = "Part of #100. Refs #200 — does not close them.\n"
    assert L.claims(cross_b, 1)["declined"] == [200]
    assert [f.evidence["unresolved"] for f in L.classify_pr(cross_b, 1)] == [[100]]

    # CONTROLS — the decline sentences the narrowing must not delete.
    for control in (
        "Refs #100, #200 — deliberately not closing them.\n",
        "Refs #100, #200 and #300 — deliberately not closing them.\n",
        "Refs #100 and #200 — deliberately not closing them.\n",
    ):
        assert L.classify_pr(control, 1) == [], control
    assert L.claims("Refs #100, #200 and #300 — deliberately not closing them.\n",
                    1)["declined"] == [100, 200, 300]


def test_two_declines_on_one_line_each_own_their_reference():
    """Two independent declines joined by `and`: alternative (a)'s clause-terminal lookahead
    admitted `$` and `[,;]` but not a conjunction, so the FIRST sentence was not recognised and its
    reference was flagged (measured at `e7dd41058`, review P3; that body was rc 0 at `d580a3401`).
    """
    body = ("Refs #100, #200 — #100 is deliberately not Closes and "
            "#200 is deliberately not Closes.\n")
    assert L.claims(body, 1)["declined"] == [100, 200]
    assert L.classify_pr(body, 1) == []


def test_the_prs_own_number_is_never_a_claim():
    assert L.classify_pr("Refs #77\n", 77) == []


def test_a_foreign_repo_claim_is_not_counted_but_a_self_qualified_one_is():
    body = "Refs premise-labs/other#12\n"
    assert L.classify_pr(body, 42, repo="daniel-ospina/tortoise") == []  # another repo's issue
    findings = L.classify_pr("Refs daniel-ospina/tortoise#12\n", 42, repo="daniel-ospina/tortoise")
    assert [f.evidence["unresolved"] for f in findings] == [[12]]


def test_an_unterminated_fence_is_reported_not_silently_swallowed(tmp_path):
    """A miss in the silent direction is the failure mode; the note makes it visible."""
    body = tmp_path / "body.md"
    body.write_text("Refs #1234\n\n```markdown\nRefs #999\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(TOOL), "--body-file", str(body)],
                       capture_output=True, text=True)
    assert "unterminated fence" in r.stdout


# --- the fence grammar belongs to closes_landing, not to a weaker copy here (review P2) ---
#
# The first version recognised a fence only when the first NON-SPACE character of the line was a
# backtick/tilde, so a quoted example written as `- ```markdown` (the fence sitting ON the
# list-marker line) or `> ```markdown` (inside a blockquote) never opened one: every line of the
# block was appended as PROSE, and a `Refs #N` the author QUOTED became a claim. That is the exact
# false-positive direction this module's docstring says was already paid for, in the tool it cites.
# The mature copy is `tools/closes_landing.py::strip_code` — the one a real CommonMark renderer is
# checked against (`tests/test_closes_landing.py`) — so this module imports it instead of carrying
# a third grammar. Each shape below is asserted from BOTH sides, so the two cannot drift apart.
_CODE_BLOCK_RE = re.compile(r"<pre><code\b.*?</code></pre>|<code\b.*?</code>", re.S)
_FIRST_REF_RE = re.compile(r"#(\d+)")

_CONTAINER_FENCE_SHAPES = [
    ("- ```markdown\n  Refs #1234\n  ```\n\nNo claim here.\n", "a fence ON the list-marker line"),
    ("1. ```markdown\n   Refs #1234\n   ```\n", "an ORDERED-list marker-line fence"),
    ("> ```markdown\n> Refs #1234\n> ```\n\nNo claim here.\n", "a fence inside a blockquote"),
    ("> - ```markdown\n>   Refs #1234\n>   ```\n\nNo claim.\n", "a marker-line fence inside a quote"),
]


@pytest.mark.parametrize("body,why", _CONTAINER_FENCE_SHAPES)
def test_a_quoted_example_in_any_container_is_code_not_a_claim(body, why):
    html = MarkdownIt("commonmark").render(body)
    prose = _CODE_BLOCK_RE.sub("", html)
    n = int(_FIRST_REF_RE.search(body).group(1))
    assert f"#{n}" not in prose, (why, html)   # the oracle: a renderer calls this CODE
    assert L.classify_pr(body, 1) == [], why    # …so the lint must not flag what it quoted


def test_a_fence_inside_a_quote_ends_when_the_quote_ends():
    """The OTHER direction — and the reason this is an import rather than a `>`-stripping fix.

    A blanket "consume any blockquote markers, then look for a fence run" would keep this block
    open to EOF and silently swallow the REAL claim after it. CommonMark ends a block with its
    CONTAINER, so `Refs #9999` below is prose — and losing it is the silent direction.

    This case passes on the PRE-FIX code too (a fence the old scan never opened hid nothing), so it
    is not the non-vacuity pin for the fix: it is the control that CONSTRAINS it. `> Refs #9999` as
    plain quoted prose must stay a claim — GitHub acts on a keyword in a quoted paragraph — which is
    what `closes_landing`'s depth comparison gets right and a prefix-stripping shortcut does not.
    """
    body = "> ```markdown\n> quoted\nRefs #9999\n"
    assert "#9999" in _CODE_BLOCK_RE.sub("", MarkdownIt("commonmark").render(body))
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[9999]]


def test_the_hidden_line_count_is_the_UNTERMINATED_fence_only():
    """The note is only honest if a block that CLOSES hides nothing."""
    assert L.strip_code("- ```markdown\n  Refs #1\n")[1] == 1
    assert L.strip_code("- ```markdown\n  Refs #1\n  ```\n")[1] == 0


def test_the_unterminated_fence_note_survives_a_sweep(monkeypatch, capsys):
    """Only the two OFFLINE branches collected the note; `--sweep` — the one mode that reads a
    POPULATION — silently swallowed the one thing that says the scan could not see a whole body."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: [
        (7, "Refs #1\n\n```markdown\nRefs #2\n", []),
    ])
    assert L.main(["--sweep"]) == L.RC_FINDINGS
    out = capsys.readouterr().out
    assert "unterminated fence" in out
    assert "#7" in out


def test_the_unterminated_fence_note_survives_every_issue_mode(monkeypatch, capsys, tmp_path):
    """The note was collected for PRs and silently dropped for the SAME content read as an issue.

    A body the scan cannot see the tail of is one of two things (a quoted example that never closed,
    or an author's unfinished edit), and BOTH are the caller's business. Reporting it for
    `--body-file`/`--pr`/`--sweep` while `--issue-body-file`/`--issue`/`--sweep-issues` swallowed it
    is the same one-population-two-measurements shape as the discarded issue body: whichever side
    a human happens to run decides whether the gap is visible.
    """
    hidden_body = IN_FLIGHT + "\n\n- ```markdown\n  never closed\n"
    body = tmp_path / "issue.md"
    body.write_text(hidden_body, encoding="utf-8")
    # 1. offline
    assert "unterminated fence" in _run("--issue-body-file", str(body)).stdout
    # 2. live single issue
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "fetch_issue", lambda number, repo: (hidden_body, []))
    assert L.main(["--issue", "4282"]) == L.RC_FINDINGS
    assert "unterminated fence" in capsys.readouterr().out
    # 3. live population
    monkeypatch.setattr(L, "sweep_issues", lambda repo, limit, label: [(9, [hidden_body])])
    assert L.main(["--sweep-issues"]) == L.RC_FINDINGS
    out = capsys.readouterr().out
    assert "unterminated fence" in out
    assert "#9" in out


# --- arm 2: an issue with WIP and no `## Satisfies` checklist -----------------------------
SATISFIES = """## Satisfies

- [ ] `3.2a` — the five `run_onboarding` tools are gone; `tools/surface-target-check.py` prints 76
- [x] `3.2b` — the retired names warn; tests/test_retired_names.py
"""

IN_FLIGHT = "IN FLIGHT: 3.2c — branch feat/surface-3-2c — box cell-4 — since 2026-11-05T09:00Z"


def test_wip_without_satisfies_is_flagged():
    findings, measured = L.classify_issue(["opening note", IN_FLIGHT], 4282)
    assert [f.arm for f in findings] == [L.ARM_ISSUE]
    assert measured["wip_source"] == "comment-marker"
    assert measured["marker_count"] == 1
    assert findings[0].evidence["populations_not_consulted"] == ["branches", "worktrees", "ref-claims"]


def test_wip_with_satisfies_is_not_flagged():
    assert L.classify_issue([IN_FLIGHT, SATISFIES], 4282)[0] == []


def test_a_fresh_issue_with_no_criteria_is_not_flagged():
    """NEGATIVE CONTROL: criteria do not exist at creation — that is normal, not a defect."""
    findings, measured = L.classify_issue(["filed from the agent_finding form", "discussion"], 11111)
    assert findings == []
    assert measured["marker_count"] == 0  # the OUTPUT must be able to say this


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: 3.2c — branch b",
    "**IN FLIGHT:** 3.2c — branch b",
    "- IN FLIGHT: 3.2c — branch b",
    "IN-FLIGHT: 3.2c — branch b",
])
def test_the_natural_markdown_shapes_are_still_markers(marker):
    assert L.in_flight_markers([marker]) == ["3.2c — branch b"]


@pytest.mark.parametrize("line", [
    # A real line from docs/scoping/2026-09-08-2519-coverage-loop.md — `in flight` is this
    # fleet's everyday vocabulary, so an UNANCHORED search made ordinary prose a WIP marker and
    # flagged a finished issue (review P1, false-positive direction).
    "> levers already in flight: C1 baseline seal (#2513 Phase 0), C2 entity/fact-augmented",
    "The campaign was in flight: 6 lanes were reading. Nothing here is WIP.",
    "2. It already exists, green, in flight: **PR #10226** carries the 160-file deletion",
])
def test_prose_containing_the_phrase_in_flight_is_not_a_marker(line):
    """The marker BEGINS a line (optionally after a bullet/emphasis); a mid-sentence phrase is prose.

    Measured before the anchor: every one of these lines produced a live marker, so arm 2 reported
    "has WIP but no `## Satisfies`" about an issue whose only "WIP" was a clause — a false positive
    pointing bureaucracy at legitimate work, the direction this module treats as worse than the
    defect it looks for.
    """
    assert L.in_flight_markers([line]) == []


@pytest.mark.parametrize("line", [
    # The marker must BEGIN the line (optionally after a list bullet / emphasis). Text before it —
    # including a bullet's own prose with its own colon — means the marker is being QUOTED or
    # discussed, not asserted, so it is not WIP.
    "- note: see IN FLIGHT: 11077 for the lane",
    "1. the older convention read `status: IN FLIGHT: 3.2c`",
])
def test_a_marker_quoted_inside_another_sentence_is_not_wip(line):
    assert L.in_flight_markers([line]) == []


@pytest.mark.parametrize("line, expected", [
    (
        "IN FLIGHT: 3.2c — branch feat/x — since 09:00Z",
        "3.2c — branch feat/x — since 09:00Z",
    ),
    (
        "**IN FLIGHT:** 3.2c — box cell-3",
        "3.2c — box cell-3",
    ),
])
def test_the_marker_remainder_comes_from_the_markers_own_colon(line, expected):
    """The remainder is the marker's OWN captured group, never `line.split(":", 1)[1]`.

    Read-side half of the same review P1. Pinned against the regex's `rest` group rather than
    against a second copy of the stripping logic, so a future widening of the anchor cannot
    reintroduce the first-colon split: `IN_FLIGHT_RE`'s group IS the definition of "after the
    marker", and this asserts the helper agrees with it.
    """
    m = L.IN_FLIGHT_RE.search(line)
    assert m is not None, line
    assert L._marker_rest(line) == m.group("rest").strip(" *_`").strip("-–— ").strip()
    assert L.in_flight_markers([line]) == [expected]


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: ",
    "IN FLIGHT: none — released 2026-11-05",
    "IN FLIGHT: released",
    "IN FLIGHT: <ids> — branch <b> — box <c> — since <UTC>",   # the block being QUOTED
])
def test_a_release_or_a_quoted_convention_is_not_wip(marker):
    assert L.in_flight_markers([marker]) == []


def test_a_satisfies_heading_with_no_checkbox_is_not_a_checklist():
    findings, _ = L.classify_issue([IN_FLIGHT, "## Satisfies\n\nTo be derived.\n"], 4282)
    assert [f.arm for f in findings] == [L.ARM_ISSUE]


def test_a_tick_in_a_later_comment_still_counts():
    """A finished issue stays clear — but NOT because of the later tick (review round 26, P2).

    This test's name and docstring used to credit the `- [x] a` in the follow-up comment for clearing
    the arm. It never did: the heading's OWN comment carries `- [ ] a` / `- [ ] b`, so the first arm
    already returns True. Kept (and relabelled) as the control it actually is — the affordance this
    test was believed to justify is the one that was a false PASS, and it is pinned against below in
    `test_a_ticked_list_after_the_heading_is_not_this_headings_criteria`.
    """
    findings, _ = L.classify_issue([IN_FLIGHT, "## Satisfies\n\n- [ ] a\n- [ ] b\n", "- [x] a\n"], 1)
    assert findings == []
    # …and it clears on the heading's own UNticked boxes alone, with no follow-up comment at all —
    # which is the whole demonstration that the later tick is not load-bearing.
    assert L.classify_issue([IN_FLIGHT, "## Satisfies\n\n- [ ] a\n- [ ] b\n"], 1)[0] == []


def test_an_injected_wip_verdict_is_honoured_and_attributed():
    """`--wip` is how a caller forwards tools/who_is_on.py's branch/worktree verdict."""
    findings, measured = L.classify_issue([], 4282, wip=True)
    assert measured["wip_source"] == "injected"
    assert [f.arm for f in findings] == [L.ARM_ISSUE]
    assert findings[0].evidence["populations_not_consulted"] == []


def test_an_injected_no_wip_verdict_clears_the_marker():
    assert L.classify_issue([IN_FLIGHT], 4282, wip=False)[0] == []


# --- CLI contract: advisory, exit-coded, never a gate ------------------------------------
def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TOOL), *args], capture_output=True, text=True)


def test_cli_offline_pr_exit_codes(tmp_path):
    """Pins the offline PR exit-code contract."""
    flagged = tmp_path / "body.md"
    flagged.write_text("Refs #10534\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("Closes #10534\n", encoding="utf-8")
    r = _run("--body-file", str(flagged))
    assert r.returncode == L.RC_FINDINGS, r.stderr
    assert "advisory only" in r.stdout
    assert _run("--body-file", str(clean)).returncode == L.RC_CLEAN


def test_cli_offline_issue_mode_with_injected_wip(tmp_path):
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([{"body": "a comment"}]), encoding="utf-8")
    body = tmp_path / "issue.md"
    body.write_text("The problem.\n", encoding="utf-8")
    r = _run("--issue-body-file", str(body), "--comments-file", str(comments), "--wip")
    assert r.returncode == L.RC_FINDINGS, r.stderr
    assert _run("--issue-body-file", str(body), "--comments-file", str(comments)).returncode == L.RC_CLEAN


def test_cli_reads_the_issue_body_file_not_only_the_comments(tmp_path):
    """`--issue-body-file` was decoration in the first version: the file was never read."""
    body = tmp_path / "issue.md"
    body.write_text(IN_FLIGHT + "\n", encoding="utf-8")
    comments = tmp_path / "c.json"
    comments.write_text("[]", encoding="utf-8")
    r = _run("--issue-body-file", str(body), "--comments-file", str(comments))
    assert r.returncode == L.RC_FINDINGS, r.stdout + r.stderr
    # and the same body carrying the checklist clears it
    body.write_text(IN_FLIGHT + "\n\n" + SATISFIES, encoding="utf-8")
    assert _run("--issue-body-file", str(body), "--comments-file", str(comments)).returncode == L.RC_CLEAN


def test_the_live_issue_path_reads_the_body_not_only_the_comments(monkeypatch):
    """`--issue` DISCARDED `fetch_issue`'s body while the offline sibling included it, so identical
    content got two different verdicts depending on whether it came from gh or from a file —
    *WIP with no checklist* on the live path, *clean* offline (review P2)."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "fetch_issue", lambda number, repo: (IN_FLIGHT + "\n", []))
    assert L.main(["--issue", "4282"]) == L.RC_FINDINGS
    # …and the checklist in that same body still clears it (the pair, not just the flag)
    monkeypatch.setattr(L, "fetch_issue",
                        lambda number, repo: (IN_FLIGHT + "\n\n" + SATISFIES, []))
    assert L.main(["--issue", "4282"]) == L.RC_CLEAN


def test_the_issue_sweep_reads_the_body_too(monkeypatch):
    """`--sweep-issues` discarded it the same way, so the whole population was measured smaller
    than the offline path measures one issue."""
    def fake_gh(args, timeout=60):
        if args[:2] == ["issue", "list"]:
            return json.dumps([{"number": 7}])
        return json.dumps({"body": IN_FLIGHT, "comments": [{"body": "a comment"}]})

    monkeypatch.setattr(L, "_gh", fake_gh)
    assert L.sweep_issues(None, 10, None) == [(7, [IN_FLIGHT, "a comment"])]


def test_cli_refuses_an_injected_verdict_for_a_population():
    r = _run("--sweep-issues", "--wip")
    assert r.returncode == 2  # argparse usage error: one verdict cannot speak for a population
    assert "cannot be applied" in (r.stderr + r.stdout)


def test_cli_json_shape(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    payload = json.loads(_run("--body-file", str(body), "--json").stdout)
    assert payload["population"] == "1 body (offline)"
    assert payload["findings"][0]["arm"] == L.ARM_PR


def test_cli_annotate_emits_warning_lines(tmp_path):
    body = tmp_path / "body.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    r = _run("--body-file", str(body), "--annotate")
    assert "::warning title=issue-process-lint" in r.stdout
    assert r.returncode == L.RC_FINDINGS


def test_an_unmeasurable_read_is_not_a_pass(monkeypatch):
    """rc=2 is distinct from rc=0 on purpose. Pinned WITHOUT a network call."""
    def boom(args, timeout=60):
        raise L.Unmeasurable("gh not found: simulated")
    monkeypatch.setattr(L, "_gh", boom)
    assert L.main(["--pr", "1"]) == L.RC_UNMEASURABLE


# --- the pin that keeps it advisory ------------------------------------------------------
def test_the_lint_is_never_wired_as_a_required_check():
    """Per-STEP parse, not a string that could sit on some unrelated step."""
    workflows = ROOT / ".github" / "workflows"
    for path in sorted(workflows.glob("*.yml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (doc.get("jobs") or {}).items():
            for step in (job or {}).get("steps") or []:
                blob = json.dumps(step)
                if "issue_process_lint" not in blob:
                    continue
                assert step.get("continue-on-error") is True, (
                    f"{path.name}:{job_name} mounts issue_process_lint without "
                    f"`continue-on-error: true` — this lint is advisory by ruling "
                    f"(#10534 / closes-coherence precedent)")
                # and it must not be able to sit in the required-aggregate's `needs`
                gate = (doc.get("jobs") or {}).get("python-ci-gate") or {}
                assert job_name not in (gate.get("needs") or [])
    mergify = ROOT / ".mergify.yml"
    if mergify.exists():
        assert "issue_process_lint" not in mergify.read_text(encoding="utf-8")


def test_the_doc_block_matches_the_template():
    """The block is copied VERBATIM into the internal `agent_finding.yml` field; the external
    `bug_report.yml` deliberately does not carry it (it would pre-fill repo-internal commands an
    outside reporter cannot run). A byte-equal pin is what stops the two copies drifting."""
    doc = (ROOT / "docs" / "ops" / "ISSUE-WORKING-PROCESS.md").read_text(encoding="utf-8")
    m = L.re.search(r"```markdown\n(## Working this issue — process.*?)```", doc, L.re.DOTALL)
    assert m, "the doc no longer carries the paste-ready block in a ```markdown fence"
    block = m.group(1).strip()
    template = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "agent_finding.yml").read_text(encoding="utf-8"))
    fields = [b for b in template["body"] if b.get("id") == "working_process"]
    assert fields, "agent_finding.yml no longer carries the working_process field"
    assert fields[0]["attributes"]["value"].strip() == block


# --- review round 3: seven findings, each paired with the control it must not break ---------
# Every test here was red against the head that review saw (`a7a70671a`), and each is paired with
# the legitimate population its fix must leave alone — the same red-first discipline as the arms
# above, because a fix that over-corrects here would re-open the false-positive population the
# tool exists to avoid.


def test_a_multi_backtick_code_span_is_not_a_claim():
    """A ``Refs #N`` span is CODE, exactly as a `Refs #N` span is (F1).

    A single-backtick regex re-derived the grammar and read the wider span as prose, so an author
    quoting the link syntax in a double-backtick span was flagged for the line they quoted — the
    exact false-positive direction the module ranks worst, on a span `closes_landing` already
    handles. The oracle is the renderer, not a second copy of the pattern.
    """
    body = "Write ``Refs #1234`` to link a partial PR.\n"
    assert L.classify_pr(body, 9) == []
    # The oracle: CommonMark makes that span a <code> element, i.e. not prose.
    assert "<code>Refs #1234</code>" in MarkdownIt("commonmark").render(body)
    # ...and the fix did not simply stop stripping code spans altogether.
    assert L.classify_pr("Refs #1234\n", 9) != []
    assert L.classify_pr("Write `Refs #1234` to link.\n", 9) == []


def test_a_fenced_example_does_not_clear_the_satisfies_arm():
    """An issue that only QUOTES the checklist format still has no finish line (F2).

    `in_flight_markers` already read through `strip_code`; `has_satisfies_checklist` did not, so a
    pasted example of the format silently cleared the arm that exists to flag WIP-without-criteria
    — a silent miss, the harmful direction.
    """
    quoted = "The format is:\n\n```markdown\n## Satisfies\n- [ ] criterion\n```\n"
    findings, measured = L.classify_issue([IN_FLIGHT, quoted], 4282)
    assert [f.arm for f in findings] == [L.ARM_ISSUE]
    assert measured["had_satisfies"] is False
    # Control: a REAL checklist (not fenced) still clears it.
    assert L.classify_issue([IN_FLIGHT, "## Satisfies\n- [ ] a\n"], 4282)[0] == []


@pytest.mark.parametrize("body, expected", [
    ("Part of #10085, #10063\n", [10063, 10085]),
    ("Refs #10327, #10182, #7612, #5219, #3719\n", [3719, 5219, 7612, 10182, 10327]),
    # A conjunction / Oxford-comma list is the SAME list. Admitting only the comma form made
    # `Part of #100 and #200` + `Closes #100` read NOTHING FLAGGED with `#200` claimed and resolved
    # by nothing — the #10534 false PASS (review P1). The decline grammar in this same module
    # already treats `#A and #B` as one list, so the two arms disagreed about what a list is.
    ("Part of #100 and #200\n", [100, 200]),
    ("Part of #100, #200, and #300\n", [100, 200, 300]),
    ("Part of #100, #200 & #300\n", [100, 200, 300]),
])
def test_a_comma_continued_claim_list_claims_every_subject(body, expected):
    """`Part of #A, #B` claims BOTH; reading only the first dropped every subject after it (F3).

    Measured on three live open PRs (#11038, #11065, #11093) — the second subject was never
    evaluated, so a claimed-and-unresolved issue went unreported.
    """
    assert L._claims_in(L.strip_code(body)[0], None)["weak"] == expected


@pytest.mark.parametrize("body, expected", [
    # The conjunction separator requires a REF immediately after it, so ordinary prose that happens
    # to contain `and` claims nothing. Paired with the positives above, so tightening one direction
    # cannot silently reopen the other.
    ("Part of #100 and the fix is in tools/x.py\n", [100]),
    ("Refs #100 and see the plan for the rest\n", [100]),
    ("Part of #100 & the fix is in tools/x.py\n", [100]),
])
def test_a_conjunction_in_prose_does_not_manufacture_a_claim(body, expected):
    """The separator is admitted only when a `#N` follows it — not for every `and` on the line."""
    assert L._claims_in(L.strip_code(body)[0], None)["weak"] == expected


def test_a_conjunction_list_of_a_closing_word_is_claimed_but_never_RESOLVED():
    """The conjunction list widens the CLAIM, never the RESOLUTION — the safety half of review P1.

    Widening `more` to admit `and`/`&`/Oxford must not manufacture a false PASS in the other
    direction: if a continuations of `Closes` counted as resolutions, `Closes #100 and #200` would
    silently clear `#200` when GitHub credits only the first ref (the documented multi-issue form
    repeats the keyword) — the #10534 hole this arm exists to shut. The continuation therefore
    lands in `weak` and is still flagged, exactly as the comma form already did.
    """
    got = L._claims_in(L.strip_code("Closes #100 and #200\n")[0], None)
    assert got["closing"] == [100], got
    # `weak` carries the CONTINUATIONS only — the first ref of a closing word IS the resolution —
    # so `#200` is claimed-but-unresolved and therefore still flagged.
    assert got["weak"] == [200], got
    assert got["continued_resolving"] == [200], got

    oxford = L._claims_in(L.strip_code("Fixes #100, #200, and #300\n")[0], None)
    assert oxford["closing"] == [100], oxford
    assert oxford["weak"] == [200, 300], oxford


def test_a_continuation_keeps_its_own_repo_and_self_number_rules():
    """The list is a list of REFS, so each subject is subject to the same two exclusions (F3)."""
    assert L._claims_in(L.strip_code("Refs #1, other/repo#2, #3\n")[0], 9, "me/here")["weak"] == [1, 3]
    assert L._claims_in(L.strip_code("Refs #9, #10\n")[0], 9)["weak"] == [10]


def test_the_label_filter_is_refused_with_pr_sweep():
    """`--label` filters ISSUES; `--sweep` reads PRs and has no label filter (F4).

    The combination used to run anyway, sweeping an unfiltered population while the doc's own
    example implied a filtered one — so a reader measured a population they had not asked for.
    """
    r = _run("--sweep", "--label", "P0-blocker")
    assert r.returncode != L.RC_CLEAN
    assert r.returncode == 2, r.stderr
    assert "--label applies to --sweep-issues" in r.stderr


def test_the_label_filter_is_refused_on_the_single_subject_modes_too(tmp_path):
    """The `--sweep` refusal above was the ONLY one, so `--issue 4282 --label x` still ran.

    The same silent-no-op class: the flag is dropped, the population is unfiltered, and nothing in
    the output says the population is not the one the flags named. Measured at `d580a3401`.
    """
    body = tmp_path / "i.md"
    body.write_text("# an issue body\n", encoding="utf-8")
    for argv in (("--issue", "4282"), ("--issue-body-file", str(body)), ("--pr", "1"),
                 ("--body-file", str(body))):
        r = _run(*argv, "--label", "P0-blocker")
        assert r.returncode == 2, (argv, r.returncode, r.stderr)
        assert "--label applies to --sweep-issues" in r.stderr, (argv, r.stderr)
    # the control: the mode that DOES read the filter still accepts it
    r = _run("--sweep-issues", "--label", "P0-blocker")
    assert r.returncode in (L.RC_CLEAN, L.RC_FINDINGS, L.RC_UNMEASURABLE), r.stderr


def test_comments_file_is_refused_on_every_mode_that_never_reads_it(tmp_path):
    """Measured at `d580a3401`: `--comments-file` was read by `--issue-body-file` ALONE.

    Every other mode dropped it in silence, and `--issue 11077 --comments-file c.json` — the
    combination a caller would actually reach for — then printed `1 issue (0 comment(s) + body)`, a
    count of *gh* comments, with nothing saying the supplied file had been ignored. That is the same
    false-confidence shape `--wip` and `--label` are refused for, on the sibling flag.
    """
    comments = tmp_path / "c.json"
    comments.write_text(json.dumps([{"body": "IN FLIGHT: 9999 — branch from-the-FILE"}]),
                        encoding="utf-8")
    body = tmp_path / "i.md"
    body.write_text("# an issue body\n", encoding="utf-8")
    for argv in (("--issue", "4282"), ("--pr", "1"), ("--body-file", str(body)),
                 ("--sweep",), ("--sweep-issues",)):
        r = _run(*argv, "--comments-file", str(comments))
        assert r.returncode == 2, (argv, r.returncode, r.stderr)
        assert "--comments-file supplies the comment bodies of --issue-body-file only" in r.stderr
    # the control: the one mode that consumes it still does
    r = _run("--issue-body-file", str(body), "--comments-file", str(comments), "--wip")
    assert r.returncode in (L.RC_CLEAN, L.RC_FINDINGS), r.stderr


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: 3.2c — released 2026-11-05",
    "IN FLIGHT: 3.2c (released)",
])
def test_an_annotated_release_marker_is_not_wip(marker):
    """A lane often marks release by ANNOTATING the marker, not replacing it with `none` (F6).

    Reading only the first token left those issues reading as WIP, so finished work was flagged.
    """
    assert L.in_flight_markers([marker]) == []


def test_free_tier_work_is_still_wip():
    """The control for F6: `free` heads a release but also appears in ordinary work descriptions,
    so it is deliberately NOT in the anywhere-scan — a fix that swept it in would silently hide
    live work."""
    assert L.in_flight_markers(["IN FLIGHT: 3.2c — free tier refactor"]) == ["3.2c — free tier refactor"]


def test_the_injected_wip_provenance_is_not_called_unconsulted(tmp_path):
    """An INJECTED verdict CAME from the branches/worktrees population (F7).

    Printing "NOT consulted" under it — and hard-coding that list in `--json` — made one run
    describe itself two incompatible ways.
    """
    body = tmp_path / "i.md"
    body.write_text("# an issue body\n", encoding="utf-8")
    r = _run("--issue-body-file", str(body), "--wip")
    assert "NOT consulted" not in r.stdout
    assert "INJECTED" in r.stdout
    payload = json.loads(_run("--issue-body-file", str(body), "--wip", "--json").stdout)
    assert payload["wip_read"]["populations_not_consulted"] == []


def test_every_bash_example_in_the_doc_parses(monkeypatch, capsys, tmp_path):
    """The doc's examples are the only executable ones it has; a broken one wastes the reader (F5).

    Every population seam is stubbed, so this asserts that the FLAGS are coherent (not mutually
    exclusive, not refused) without touching the network — the examples are plain flags and paths.

    ⚠️ The doc's two file operands are PLACEHOLDERS a reader creates, so they are materialised here
    and substituted. An earlier version ran the examples verbatim against `/tmp/body.md` and
    `/tmp/i.md`, which passed only because another lane had left those files on the box — on a
    fresh runner it raised `FileNotFoundError` and this file sits on the REQUIRED `core` surface.
    A test must not depend on the host's `/tmp` contents in EITHER direction.
    """
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: [])
    monkeypatch.setattr(L, "sweep_issues", lambda repo, limit, label: [])
    monkeypatch.setattr(L, "fetch_issue", lambda number, repo: ("", []))
    monkeypatch.setattr(L, "fetch_pr", lambda number, repo: ("", []))
    operands = {"/tmp/body.md": tmp_path / "body.md", "/tmp/i.md": tmp_path / "i.md"}
    operands["/tmp/body.md"].write_text("Refs #1\n", encoding="utf-8")
    operands["/tmp/i.md"].write_text("# an issue body\n", encoding="utf-8")
    doc = (ROOT / "docs" / "ops" / "ISSUE-WORKING-PROCESS.md").read_text(encoding="utf-8")
    block = L.re.search(r"```bash\n(.*?)```", doc, L.re.DOTALL)
    assert block, "the doc no longer carries its invocations in a ```bash fence"
    examples = [line for line in block.group(1).splitlines()
                if line.strip().startswith("uv run python tools/issue_process_lint.py")]
    assert len(examples) >= 4, examples
    for line in examples:
        # The examples carry a trailing `# comment`; shlex does not treat `#` as a comment by
        # default, so drop it before splitting.
        argv = line.split("uv run python tools/issue_process_lint.py", 1)[1]
        argv = L.re.split(r"\s+#\s", argv)[0]
        for placeholder, real in operands.items():
            argv = argv.replace(placeholder, str(real))
        rc = L.main(shlex.split(argv))
        capsys.readouterr()  # each example owns its own output
        assert rc in (L.RC_CLEAN, L.RC_FINDINGS), (line, rc)


# --- review round 4: five findings, one of them a REGRESSION the round-3 fix introduced ------
# Round 3's F6 fix over-corrected (a release word anywhere in the marker cleared real WIP) and its
# F5 test depended on the host's /tmp. Both are pinned here, in both directions, because an
# over-correction in a warn-only tool silently stops flagging the thing it exists to flag.


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: 4282, 11077 — branch fix/unclaimed-issue-arm2 — box cell-3 — since 2026-11-05T09:00Z",
    "IN FLIGHT: 3.2c — branch fix/none-handling — box cell-4",
    "IN FLIGHT: 3.2c — branch released-work — box cell-4",
    "IN FLIGHT: 3.2c — free tier refactor",
])
def test_a_branch_slug_containing_a_release_word_is_still_wip(marker):
    """The F6 fix must not over-correct: a release word ANYWHERE cleared real WIP.

    The marker the process block mandates carries author text after the ids, and `unclaimed` is
    this tool's own vocabulary — so a lane named after the arm it is working on would have cleared
    its own marker and reported the issue clean. A release is a delimited annotation, and that is
    what is matched now.
    """
    assert L.in_flight_markers([marker]) == [L._marker_rest(marker)]


@pytest.mark.parametrize("marker", [
    "IN FLIGHT: 3.2c — branch b — box c — released 2026-11-05",
    "IN FLIGHT: 3.2c (released)",
    "IN FLIGHT: none — released",
])
def test_an_annotation_position_release_does_clear_wip(marker):
    """The other side of the same pin: the annotated forms still clear, so the anchor did not
    swallow the fix it was added for."""
    assert L.in_flight_markers([marker]) == []


def test_the_offline_modes_make_no_gh_call(monkeypatch, tmp_path, capsys):
    """`--body-file` / `--issue-body-file` are documented as OFFLINE (F2 of round 4).

    `resolve_repo` used to run before the mode dispatch, so both made a network call — falsifying
    the help text AND this PR's own `config/ci-surfaces.yml` note, and, on a box without `gh`,
    silently dropping every self-qualified `owner/repo#N` claim.
    """
    calls: list[str] = []
    real = L.resolve_repo
    monkeypatch.setattr(L, "resolve_repo", lambda repo: calls.append(str(repo)) or real(repo))
    body = tmp_path / "b.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    issue = tmp_path / "i.md"
    issue.write_text("# issue\n", encoding="utf-8")
    L.main(["--body-file", str(body)])
    L.main(["--issue-body-file", str(issue), "--wip"])
    capsys.readouterr()
    assert calls == [], f"the offline modes resolved the repo through gh: {calls}"


def test_the_annotate_mode_also_reports_an_injected_verdict(tmp_path, capsys):
    """F7 was fixed in the human and `--json` modes only; `--annotate` still said "NOT consulted"
    about the population the verdict came from (round-4 F3)."""
    issue = tmp_path / "i.md"
    issue.write_text("# an issue body\n", encoding="utf-8")
    L.main(["--issue-body-file", str(issue), "--wip", "--annotate"])
    out = capsys.readouterr().out
    assert "NOT consulted" not in out
    assert "INJECTED (--wip)" in out
    # ...and it names the flag that was actually passed.
    L.main(["--issue-body-file", str(issue), "--no-wip", "--annotate"])
    out = capsys.readouterr().out
    assert "INJECTED (--no-wip)" in out


def test_the_index_row_does_not_overstate_template_coverage():
    """`docs/00_index.md` is the artifact a later lane ROUTES from (round-4 F5).

    It said the block is rendered into "both issue forms"; only the internal `agent_finding.yml`
    carries it, and this PR's own doc calls the `bug_report.yml` exclusion deliberate.
    """
    index = (ROOT / "docs" / "00_index.md").read_text(encoding="utf-8")
    row = next(ln for ln in index.splitlines() if "ISSUE-WORKING-PROCESS" in ln)
    assert "both issue forms" not in row
    templates = ROOT / ".github" / "ISSUE_TEMPLATE"
    carriers = [p.name for p in templates.glob("*.yml")
                if "working_process" in p.read_text(encoding="utf-8")]
    assert carriers == ["agent_finding.yml"], carriers
    assert "agent_finding.yml" in row


# --- review round 5: the resolution window, the release anchor, the skipped claims ------------
# Round 3's review found that the 16-character window — added so `Refs, as agreed, #12` counts —
# was ALSO applied to the RESOLVING words, so a prose sentence manufactured a closing resolution
# and arm 1 reported clean. That is a false PASS, the direction this module ranks worst, and it is
# the #10534 hole the arm exists to close.


@pytest.mark.parametrize("body", [
    "Refs #12\n\nThe fix in #12 was reviewed this morning.\n",
    "Refs #12\n\nResolved by the approach discussed in #12.\n",
    "Refs #12\n\nSee the closure notes in #12.\n",
])
def test_prose_containing_a_resolving_word_does_not_resolve_the_claim(body):
    """A resolving word must be followed by punctuation/whitespace, not by 16 characters of prose."""
    assert L.claims(body, None, None)["closing"] == []
    assert [f.arm for f in L.classify_pr(body, 1)] == [L.ARM_PR]


@pytest.mark.parametrize("closer, group", [
    ("Closes #12", "closing"),
    ("closes #12", "closing"),
    ("Closes: #12", "closing"),
    ("Fixes #12", "closing"),
    ("Resolves #12", "closing"),
    ("Satisfies #12", "satisfies"),
])
def test_a_real_closing_keyword_still_resolves(closer, group):
    """The control: tightening the window must not stop `Closes #N` from resolving."""
    body = "Refs #12\n" + closer + "\n"
    assert L.claims(body, None, None)[group] == [12]
    assert L.classify_pr(body, 1) == []


def test_the_lenient_window_is_kept_for_linking_words():
    """The other control: the leniency is what makes `Refs, as agreed, #12` a claim, so it stays
    for the LINKING words even though it was removed from the resolving ones."""
    assert L.claims("Refs, as agreed, #12\n", None, None)["weak"] == [12]


@pytest.mark.parametrize("marker, released", [
    ("3.2c — released 2026-11-05", True),
    ("3.2c (released)", True),
    ("3.2c: released 2026-11-05", True),
    ("3.2c [released]", True),
    ("3.2c. Released 2026-11-05", True),
    # A HYPHEN is the in-word separator of every branch slug, so a bare `-` must not be a
    # delimiter — these are live WIP, and reading them as released is a silent miss.
    ("11077 — branch feat/retrieval-none-mode — box cell-3", False),
    ("11077 — branch feat/x — box cell-3 — since 2026-11-05T09:00Z (none pending)", False),
    ("3.2c — branch fix/unclaimed-issue-arm2 — box c", False),
    ("3.2c — branch released-work", False),
    ("3.2c — free tier refactor", False),
])
def test_the_release_anchor_is_correct_in_both_directions(marker, released):
    assert (L.in_flight_markers(["IN FLIGHT: " + marker]) == []) is released


def test_a_qualified_claim_skipped_offline_is_reported_not_silently_dropped(tmp_path, capsys):
    """Offline, the repo is unknown, so `owner/repo#N` is neither claimed nor forgotten (F3).

    Dropping it silently measured a SMALLER population than the same body with `--repo` and
    reported clean for content it had not read — the shape this module refuses everywhere else.
    """
    body = tmp_path / "b.md"
    body.write_text("Refs daniel-ospina/tortoise#12\n", encoding="utf-8")
    L.main(["--body-file", str(body)])
    out = capsys.readouterr().out
    assert "were NOT read" in out and "--repo" in out
    # ...and with the repo supplied the same body IS read, and flagged.
    L.main(["--body-file", str(body), "--repo", "daniel-ospina/tortoise"])
    out = capsys.readouterr().out
    assert "[pr-unclaimed-issue]" in out


def test_the_arm_table_states_the_claiming_word_requirement():
    """The arm table is the tool's own spec, and the spec a maintainer reads before editing
    `CLAIM_RE`. It used to claim a bare `#N` is flagged, which is the 68 %-false-positive
    behaviour that was deliberately removed — a table describing it invites restoring it.
    """
    doc = L.__doc__ or ""
    assert "a bare\n                           ``#N`` on that line" not in doc
    assert "claiming word" in doc
    assert "comma-continued list" in doc.lower()
    # ...and it agrees with the code: a bare `#N` with no claiming word is a mention.
    assert L.classify_pr("This reds only #10 and #11, the two criterion ids.\n", 1) == []


# --- review round 6: a list continuation is a CLAIM, never a RESOLUTION ------------------------
# Round 1 widened the comma-continued list to every claiming word, which made `Closes #A, #B`
# credit BOTH as resolved and report CLEAN — while GitHub closes only #A (its documented
# multi-issue form repeats the keyword: `Resolves #10, resolves #123`) and this repo's own
# `closes_landing.parse_closing_refs` credits exactly one. A false PASS, new in this PR series.


def test_a_continuation_of_a_closing_word_is_claimed_but_not_resolved():
    """`Closes #A, #B` resolves #A only; #B must still be FLAGGED.

    The sibling grammar is the oracle — crediting both would have reported clean on work nothing
    closed, which is the #10534 hole arm 1 exists to shut.
    """
    from tools.closes_landing import parse_closing_refs
    body = "Closes #10107, #10157.\n"
    c = L.claims(body, None, None)
    assert c["closing"] == parse_closing_refs(body) == [10107]
    assert c["weak"] == [10157]          # claimed, so it is still reported…
    assert [f.arm for f in L.classify_pr(body, 999)] == [L.ARM_PR]   # …and NOT silently clean


def test_a_linking_word_still_governs_its_whole_list():
    """The control: the round-1 fix must survive — a linking word claims every subject."""
    assert L._claims_in(L.strip_code("Part of #10085, #10063\n")[0], None)["weak"] == [10063, 10085]
    assert L._claims_in(L.strip_code("Refs #10327, #10182, #7613\n")[0], None)["weak"] == [7613, 10182, 10327]


@pytest.mark.parametrize("marker", [
    "3.2c — released (2026-11-05)",
    "3.2c — released on 2026-11-05",
])
def test_a_dated_release_annotation_still_clears(marker):
    """The terminal lookahead must admit a bracketed or `on <date>` annotation — requiring the
    date to sit immediately after the word re-broke the most natural dated forms and flagged
    finished work (the exact class rounds 2 and 3 fixed)."""
    assert L.in_flight_markers(["IN FLIGHT: " + marker]) == []


@pytest.mark.parametrize("marker", ["- [ ] a", "* [ ] a", "+ [ ] a", "1. [ ] a", "2) [x] a"])
def test_every_github_task_list_marker_counts_as_a_checklist(marker):
    """GitHub renders all four markers as task lists; counting only `-`/`*` read a scoped issue as
    UNSCOPED and flagged it. The marker set mirrors `closes_landing._TASK_ITEM_RE`."""
    assert L.has_satisfies_checklist(["## Satisfies\n" + marker + "\n"]) is True


def test_the_arm_two_wording_names_the_body_too():
    """Both the arm table and the emitted detail must name the BODY: since the round-2 fix every
    issue mode feeds `[body, *comments]` in, so "in its comments" understates the population."""
    doc = L.__doc__ or ""
    assert "anywhere in its\n                           body or comments" in doc
    findings, _ = L.classify_issue([IN_FLIGHT], 1)
    assert "body or comments" in findings[0].detail


def test_the_skipped_claim_note_names_the_right_cause(monkeypatch, capsys):
    """The note fires in the LIVE modes too, where the cause is a gh failure — calling that
    "offline" named a mode the caller was not in (round-4 F5)."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: None)
    monkeypatch.setattr(L, "fetch_pr", lambda n, repo: ("Refs daniel-ospina/tortoise#12\n", []))
    L.main(["--pr", "11078"])
    out = capsys.readouterr().out
    assert "could not be resolved" in out
    assert "NARROWER" in out
    assert "offline" not in out


# --- review round 7: the message must not misquote the author's body ---------------------------
# Round 6 made a comma-continuation of a CLOSING word a CLAIM rather than a RESOLUTION. The verdict
# became right; the emitted REASON did not, because every `weak` subject was described as "only a
# linking word" — so an author who wrote `Closes #A, #B` was told they wrote `Refs`. In a warn-only
# tool the message IS the product, and a correct flag with a false reason is indistinguishable from
# a broken tool.


def test_the_finding_does_not_misquote_a_closing_word_as_a_linking_word():
    """`Closes #A, #B` → #B is flagged, and the text must not claim the author wrote `Refs`."""
    findings = L.classify_pr("Closes #1, #2\n", 999)
    assert [f.arm for f in findings] == [L.ARM_PR]
    assert findings[0].evidence["unresolved"] == [2]
    detail = findings[0].detail
    assert "only a linking word" not in detail
    # ...and it states the actual rule, so the remedy the reader infers is the right one.
    assert "FIRST ref" in detail
    # A genuine linking-word claim still reads correctly under the same wording.
    assert "no closing keyword of its own" in L.classify_pr("Refs #1\n", 999)[0].detail


@pytest.mark.parametrize("marker", [
    "3.2c — released: 2026-11-05",
    "3.2c — released - 2026-11-05",
])
def test_a_date_separated_from_the_release_word_still_clears(marker):
    """`:` and `-` may separate the DATE from the word (the same class rounds 2-4 fixed)."""
    assert L.in_flight_markers(["IN FLIGHT: " + marker]) == []


@pytest.mark.parametrize("marker", [
    "3.2c — released: pending",
    "3.2c — released-pending",
    "3.2c — branch feat/retrieval-none-mode",
])
def test_a_separator_alone_does_not_make_a_release(marker):
    """The control: admitting `:`/`-` ONLY in front of a date must not turn a status note into a
    release — that would be a silent miss, the direction this arm cannot afford."""
    assert L.in_flight_markers(["IN FLIGHT: " + marker]) != []


def test_the_arm_table_states_that_a_continuation_does_not_resolve():
    """The table is the spec a maintainer edits `CLAIM_RE` from; round 6 fixed the CODE for
    continuations and left the table claiming the opposite (one clause over)."""
    doc = L.__doc__ or ""
    assert "only the\n                           **first** ref of a RESOLVING word resolves" in doc
    assert "a continuation is CLAIMED, never resolved" in doc
    # agree with the code
    assert L.claims("Closes #1, #2\n", None, None)["closing"] == [1]


# --- review round 8: the repo's OWN mandated form was being flagged ---------------------------
# `docs/ops/ISSUE-WORKING-PROCESS.md:91` records that `skills/code-review/SKILL.md` tells an author
# to write `Related: #N` for a NON-closing reference — i.e. to say the PR does NOT serve the issue.
# Treating that as a claim flagged work written exactly as this repo instructs, which is the
# bureaucracy-on-legitimate-work failure the whole arm is bounded against.


@pytest.mark.parametrize("body", ["Satisfies #1: 1\nRelated: #4282\n", "See also #4282\n",
                                  "Related: #4282\n"])
def test_the_non_serving_reference_forms_are_not_claims(body):
    """`Related:` / `See also` mean "does NOT serve" — a MENTION, never a claim."""
    assert L.classify_pr(body, 999) == []


@pytest.mark.parametrize("body", ["Refs #1\n", "Part of #1\n", "serves #1\n", "addresses #1\n"])
def test_the_claiming_words_are_still_claims(body):
    """The control: dropping the non-serving forms must not weaken the arm's real population."""
    assert [f.arm for f in L.classify_pr(body, 999)] == [L.ARM_PR]


@pytest.mark.parametrize("marker", ["3.2c — **released**", "3.2c — **released** 2026-11-05",
                                    "3.2c — _released_"])
def test_an_emphasised_release_word_clears(marker):
    """Emphasis wraps the word, and `_` is a word character so `\\b` never matched — finished work
    was flagged (the same class rounds 3-5 fixed)."""
    assert L.in_flight_markers(["IN FLIGHT: " + marker]) == []


@pytest.mark.parametrize("marker", ["3.2c — **released: pending**", "3.2c — **free** tier refactor",
                                    "3.2c — fix_unclaimed_issue"])
def test_emphasis_flattening_does_not_manufacture_a_release(marker):
    """The control: flattening `*`/`_`/`` ` `` must not turn a status note or a slug into a
    release — that would be a silent miss."""
    assert L.in_flight_markers(["IN FLIGHT: " + marker]) != []


def test_a_quoted_release_word_in_a_code_span_is_not_a_release_statement():
    """Deliberate, and pinned so nobody "fixes" it: `` `released` `` is a QUOTED word. `strip_code`
    blanks it before the marker scan — the same doctrine that makes `gh pr view #1234` a quotation
    rather than a claim."""
    assert L.in_flight_markers(["IN FLIGHT: 3.2c — `released`"]) == ["3.2c"]


def test_the_first_ref_clause_appears_only_where_it_applies():
    """The FIRST-ref sentence is about a comma-continued ref; emitting it for a plain `Refs #N`
    claim — the dominant class — is an irrelevant diagnosis in a tool whose product is the message."""
    cont = L.classify_pr("Closes #1, #2\n", 999)[0].detail
    plain = L.classify_pr("Refs #1\n", 999)[0].detail
    assert "FIRST ref" in cont and "comma-continued" in cont
    assert "FIRST ref" not in plain
    # both still name the real cause
    for d in (cont, plain):
        assert "no closing keyword of its own" in d


# --- review round 9: the spec drifted from the code, and an undelivered frontmatter fix --------
# Rounds 5, 6 and 8 each fixed the module's own spec table drifting from CLAIM_RE. Round 8 removed
# `Related`/`See also` from the REGEX and left both names in the table and in `claims()`'s
# contract — so a maintainer reading the spec re-adds them and re-opens round 8's P2, with nothing
# going red. The pin below is the thing that makes that impossible.


def test_the_arm_table_names_exactly_the_words_the_regex_claims():
    """The table is the spec a maintainer edits `CLAIM_RE` from, so it is pinned to the regex."""
    doc = L.__doc__ or ""
    # The row WRAPS across lines, so read the whole clause, not a single line.
    start = doc.index("attached to a **claiming word**")
    table_row = doc[start:doc.index("and the closing/satisfies words", start)]
    for word in ("Refs", "Part of", "serves", "addresses"):
        assert word in table_row, (word, table_row)
    # The two that must NOT be there — they say the PR does NOT serve the issue.
    assert "Related``" not in table_row
    assert "See also``" not in table_row
    for word in ("Refs", "Part of", "serves", "addresses"):
        assert L.classify_pr(f"{word} #1\n", 999), word
    for gone in ("Related: #1", "See also #1"):
        assert L.classify_pr(gone + "\n", 999) == [], gone


def test_the_declared_silences_are_counted_and_listed():
    """`Three populations are DELIBERATELY NOT FLAGGED` must match the number of bullets — the
    same spec-vs-code drift, one level up."""
    import re as _re
    doc = L.__doc__ or ""
    m = _re.search(r"(\w+) populations are DELIBERATELY NOT FLAGGED", doc)
    assert m, "the declaration header is gone"
    word = m.group(1).lower()
    start = doc.index(m.group(0))
    bullets = [ln for ln in doc[start:].splitlines() if ln.startswith("* a ")]
    expected = {"two": 2, "three": 3, "four": 4, "five": 5}[word]
    assert len(bullets) == expected, (word, len(bullets), bullets)


def test_the_first_ref_clause_is_a_closing_keyword_rule_only():
    """The FIRST-ref rule is about CLOSING keywords, so it must not appear for a linking-word list
    — `Part of #A, #B` and `Refs #A, #B` are the DOMINANT shapes in the measured population."""
    def detail(body):
        d = L.classify_pr(body, 999)
        return d[0].detail if d else ""
    assert "FIRST ref" in detail("Closes #1, #2\n")
    # ⛔ NOT `or detail(...) == ""`. That disjunct passes when the tool returns NO finding at all
    # for `Satisfies #1, #2` — i.e. exactly the round-6 false PASS this arm exists to shut, so the
    # assertion had no teeth (review at `2219dfe86`, P3, still open at `6d8e4ed8b`). Dropping the
    # `if continued` guard from the `SATISFIES_WORDS` branch makes `#2` RESOLVED, `classify_pr`
    # returns `[]`, and the old assertion still passed. The finding is now required, and the
    # claims() shape behind it is pinned directly so that mutation cannot be silent.
    satisfies_cont = detail("Satisfies #1, #2\n")
    assert satisfies_cont, "a continuation of `Satisfies` must still flag #2"
    assert "FIRST ref" in satisfies_cont
    continued = L.claims("Satisfies #1, #2\n", None, None)
    assert continued["satisfies"] == [1]
    assert continued["weak"] == [2]
    assert continued["continued_resolving"] == [2]
    assert "FIRST ref" not in detail("Refs #1, #2\n")
    assert "FIRST ref" not in detail("Part of #1, #2\n")
    assert "FIRST ref" not in detail("Refs #1\n")


def _peer_vocabulary(ops_dir: Path, exclude: str) -> tuple[set, set]:
    """The `type` / `domain` values used by the OTHER docs in `ops_dir`.

    Two defects lived in the inline version of this scan (review P3), and both are why it is a
    function here rather than a loop body:

    1. `ops_dir.glob("*.md")` includes the doc UNDER TEST, so its own value seeded the set and
       `front["type"] in peer_types` was true by construction for ANY value — an assertion that
       cannot fail, under a name promising peer conformance.
    2. `split("---", 2)[1]` raises `IndexError` for a peer with NO frontmatter. This test file is
       registered on the REQUIRED `core` surface, so the next doc added to `docs/ops/` without
       frontmatter would red a required check for a reason unrelated to its author.
    """
    types: set = set()
    domains: set = set()
    for path in sorted(ops_dir.glob("*.md")):
        if path.name == exclude:
            continue
        parts = path.read_text(encoding="utf-8").split("---", 2)
        if len(parts) < 3:  # no frontmatter at all — not a peer we can read a vocabulary from
            continue
        fm = yaml.safe_load(parts[1]) or {}
        if not isinstance(fm, dict):
            continue
        if "type" in fm:
            types.add(fm["type"])
        if "domain" in fm:
            domains.add(fm["domain"])
    return types, domains


def test_the_doc_frontmatter_matches_its_peers():
    """Every other `docs/ops/` doc uses `type: operations` / `domain: platform` / `doc_status:`,
    and none carries a `status:` key. This one said `type: ops` / `domain: process` beside a
    `status: draft` that contradicted its own `doc_status: live`."""
    doc = (ROOT / "docs" / "ops" / "ISSUE-WORKING-PROCESS.md").read_text(encoding="utf-8")
    front = yaml.safe_load(doc.split("---", 2)[1])
    assert front["type"] == "operations"
    assert front["domain"] == "platform"
    assert front["doc_status"] == "live"
    assert "status" not in front
    peer_types, peer_domains = _peer_vocabulary(ROOT / "docs" / "ops", "ISSUE-WORKING-PROCESS.md")
    # POPULATED from other docs, or the two assertions below assert nothing.
    assert peer_types and peer_domains, (peer_types, peer_domains)
    assert front["type"] in peer_types, (front["type"], peer_types)
    assert front["domain"] in peer_domains, (front["domain"], peer_domains)


def test_the_peer_vocabulary_excludes_the_doc_under_test_and_survives_a_frontmatterless_peer(
        tmp_path):
    """Non-vacuity of the peer check itself, in both directions.

    The pre-fix inline scan had (a) an assertion that could not fail — the doc under test seeded its
    own peer set — and (b) an `IndexError` landmine for a frontmatter-less peer, on a test file that
    runs in the REQUIRED `core` shards.
    """
    ops = tmp_path / "ops"
    ops.mkdir()
    (ops / "peer.md").write_text("---\ntype: operations\ndomain: platform\n---\n\nbody\n",
                                  encoding="utf-8")
    (ops / "no-frontmatter.md").write_text("# just a doc, no frontmatter\n", encoding="utf-8")
    # (b) a frontmatter-less peer is skipped rather than raising `IndexError`
    types, domains = _peer_vocabulary(ops, "under-test.md")
    assert types == {"operations"} and domains == {"platform"}, (types, domains)
    # (a) the doc UNDER TEST does not contribute its own value to its peer set
    subject = ops / "under-test.md"
    subject.write_text("---\ntype: process\ndomain: ops\n---\n\nbody\n", encoding="utf-8")
    types, domains = _peer_vocabulary(ops, "under-test.md")
    assert "process" not in types and "ops" not in domains, (types, domains)


# --- review round 10: `--wip` was accepted on the modes that never read it ---------------------
# `wip` is consumed by `classify_issue` ALONE; `--pr` and `--body-file` run arm 1 only. So the flag
# was silently DISCARDED there — and because `measured_rows` stayed empty, no WIP line printed
# either, so an operator saw arm 1's `rc 0` and read "NOTHING FLAGGED" as covering arm 2 as well.
# Worse, the module's own Usage block advertised exactly that combination (`--pr 11027 --wip`).
# This module's doctrine everywhere else is to REFUSE a flag it cannot honour rather than silently
# measure a different population than the flags name — so these pin the refusal.

_MODES_THAT_READ_WIP = ("--issue", "--issue-body-file")
_MODES_THAT_DO_NOT = ("--pr", "--body-file", "--sweep", "--sweep-issues")


def test_the_wip_line_is_printed_by_the_modes_that_actually_run_the_arm(tmp_path, capsys):
    """The module docstring said "every run prints how many subjects carried a marker" — it does not.

    Only a run whose population consults the WIP arm populates `measured_rows`, so the `WIP arm: N
    subject(s) read` line is printed for `--issue-body-file` and never for `--body-file`. The
    docstring is the spec a maintainer edits this arm from, so an overstated "every run" is the
    claim-vs-code class this PR is about (review P3). Pinned in BOTH directions: the issue mode
    must print the line and the PR mode must not, so neither always-printing it nor deleting it
    can read as a pass.
    """
    body = tmp_path / "issue.md"
    body.write_text("IN FLIGHT: 1.1 — branch b\n", encoding="utf-8")
    assert L.main(["--issue-body-file", str(body)]) == L.RC_FINDINGS
    assert "WIP arm:" in capsys.readouterr().out

    pr = tmp_path / "pr.md"
    pr.write_text("Refs #10534\n", encoding="utf-8")
    L.main(["--body-file", str(pr)])
    out = capsys.readouterr().out
    assert "WIP arm:" not in out, out
    assert "advisory only" in out, out


@pytest.mark.parametrize("flag", ["--wip", "--no-wip"])
@pytest.mark.parametrize("mode", _MODES_THAT_DO_NOT)
def test_a_wip_verdict_is_refused_on_every_mode_that_never_reads_it(mode, flag, tmp_path, capsys):
    """`--wip` answers ONE issue. On a PR/population mode the verdict was dropped in silence."""
    operand = {"--pr": "1", "--body-file": str(tmp_path / "b.md")}.get(mode)
    argv = [mode] if operand is None else [mode, operand]
    if mode == "--body-file":
        (tmp_path / "b.md").write_text("Refs #1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        L.main([*argv, flag])
    assert exc.value.code == 2, (mode, flag, exc.value.code)
    err = capsys.readouterr().err
    # Named either by the pre-existing population refusal (`--sweep*`) or by this one — both say
    # the flag answers ONE issue and name the flag, which is what makes it a refusal, not a no-op.
    assert "--wip/--no-wip" in err, err


def test_the_wip_flag_is_refused_through_the_real_cli_not_just_in_process(tmp_path):
    """The refusal must survive the actual entry point (a subprocess, real argv, real exit code)."""
    body = tmp_path / "b.md"
    body.write_text("Refs #1\n", encoding="utf-8")
    r = _run("--body-file", str(body), "--wip")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "--issue and --issue-body-file only" in r.stderr


@pytest.mark.parametrize("flag,want_wip", [("--wip", True), ("--no-wip", False)])
def test_the_modes_that_do_read_wip_still_accept_it(flag, want_wip, tmp_path, monkeypatch):
    """The control: the two ISSUE modes genuinely consume the verdict, so the refusal above must
    not be a blanket one — that would remove the feature this flag exists for."""
    body = tmp_path / "i.md"
    body.write_text("# an issue body\n", encoding="utf-8")
    captured = {}
    real = L.classify_issue

    def spy(bodies, number, wip=None, notes=None):
        captured["wip"] = wip
        return real(bodies, number, wip, notes)

    monkeypatch.setattr(L, "classify_issue", spy)
    L.main(["--issue-body-file", str(body), flag])
    assert captured["wip"] is want_wip, captured

    monkeypatch.setattr(L, "fetch_issue", lambda number, repo: ("# an issue\n", []))
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    L.main(["--issue", "4282", flag])
    assert captured["wip"] is want_wip, captured


def test_the_usage_block_never_advertises_wip_for_a_pr_mode():
    """The module's Usage block carried `--pr 11027 --wip`, i.e. it documented the exact no-op this
    round refuses — an operator copying it believed a WIP verdict had been injected."""
    doc = L.__doc__ or ""
    usage = doc[doc.index("Usage\n-----"):]
    examples = [ln.strip() for ln in usage.splitlines()
                if "issue_process_lint.py" in ln and ("--wip" in ln or "--no-wip" in ln)]
    assert examples, "the Usage block no longer shows the flag at all"
    for line in examples:
        assert any(m in line for m in _MODES_THAT_READ_WIP), line


# --- review round 11: the sweeps hid their own cap, and the peer scan was vacuous ---------------
# The `--sweep` / `--sweep-issues` cap and the peer-vocabulary defects live here; the third finding
# of that round (the decline escape) is pinned above, next to `DECLINE_RE`'s own controls — a
# concurrent lane landed the same fix on this branch, so the two copies were reconciled rather than
# stacked.
@pytest.mark.parametrize("body,why", [
    # the pinned real shape: the quoted keyword sits in an inline code span, so `strip_code` has
    # already blanked it — the decline must survive that (this is why the phrase is not deleted)
    ("Refs #10602 — deliberately **not** `Closes`.\n", "quoted keyword blanked as inline code"),
    ("Refs #10602 — deliberately not closing this one.\n", "followed by a closing-word stem"),
    ("Refs #10602 — deliberately not a closing keyword, see the plan.\n", "article + closing stem"),
    ("Refs #10602 — deliberately not.\n", "nothing but the phrase"),
])
def test_the_decline_still_clears_a_reference_the_author_says_is_not_closing(body, why):
    """The control: narrowing the escape must not delete it — an author who says the reference is
    deliberately not a closing one is answering, and flagging them punishes the honesty the block
    asks for."""
    assert L.classify_pr(body, 11054) == [], why


def test_the_decline_window_does_not_reach_across_a_clause():
    """The narrowing is a WINDOW, not a word list: the phrase must sit beside the reference's clause.

    `deliberately not a gate` is about the gate; `deliberately not closing` is about the reference.
    Both directions are pinned (the incident cases above, the controls here and in the parametrised
    case below), because the failure mode in the CLEAN direction is silent.
    """
    for tail in ("a gate", "a blocker for anyone", "worth blocking a merge over",
                 "the thing this measures"):
        assert L.classify_pr(f"Refs #1234 — deliberately not {tail}.\n", 99), tail


def _sweep_rows(n: int) -> list:
    return [(i, "Refs #1\n", []) for i in range(1, n + 1)]


def test_a_pr_sweep_that_stops_at_its_cap_says_so(monkeypatch, capsys):
    """`--sweep` makes ONE `gh pr list --limit N` call; a full page read as the whole population.

    `gh` lists NEWEST-first, so the subjects silently skipped are the OLDEST. Every other narrowed
    population in this module is named in the output; the truncation that can hide most of the
    subjects was the one that was silent (review P2).
    """
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: _sweep_rows(limit))
    L.main(["--sweep", "--limit", "5"])
    out = capsys.readouterr().out
    assert "stopped this sweep at its CAP" in out, out
    assert "5 open PR(s)" in out


def test_a_pr_sweep_short_of_its_cap_makes_no_such_claim(monkeypatch, capsys):
    """The control: a sweep that reached the end of the population must not claim truncation."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: _sweep_rows(limit - 1))
    L.main(["--sweep", "--limit", "5"])
    out = capsys.readouterr().out
    assert "CAP" not in out, out


def test_an_issue_sweep_that_stops_at_its_cap_says_so_too(monkeypatch, capsys):
    """Arm 2's own population — and the one where the skipped OLD issues matter most."""
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_issues",
                        lambda repo, limit, label: [(i, ["no markers here"]) for i in range(1, limit + 1)])
    L.main(["--sweep-issues", "--limit", "4"])
    out = capsys.readouterr().out
    assert "stopped this sweep at its CAP" in out, out
    assert "open issue" in out, out


def test_the_cap_is_reported_in_json_and_annotate_modes_too(monkeypatch, capsys):
    """The note must not be a human-output-only nicety: `--json` is what a caller parses."""
    import json as _json
    monkeypatch.setattr(L, "resolve_repo", lambda repo: "daniel-ospina/tortoise")
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: _sweep_rows(limit))
    L.main(["--sweep", "--limit", "3", "--json"])
    payload = _json.loads(capsys.readouterr().out)
    assert any("CAP" in n for n in payload["notes"]), payload["notes"]
    monkeypatch.setattr(L, "sweep_prs", lambda repo, limit: _sweep_rows(limit))
    L.main(["--sweep", "--limit", "3", "--annotate"])
    assert "CAP" in capsys.readouterr().out


def test_the_flags_do_not_call_a_cap_a_population(capsys):
    """The help text and the doc both said "every" and "page size", asserting a pagination loop
    that does not exist — there is ONE `gh … list --limit N` call per sweep (review P2)."""
    with pytest.raises(SystemExit):
        L.main(["--help"])
    help_text = capsys.readouterr().out
    assert "every open PR" not in help_text, help_text
    assert "every open issue" not in help_text, help_text
    assert "page size" not in help_text, help_text
    assert "CAP" in help_text, help_text
    doc = (ROOT / "docs" / "ops" / "ISSUE-WORKING-PROCESS.md").read_text(encoding="utf-8")
    assert "every open PR" not in doc
    assert "a CAP, not the population" in doc


# --- review round 20 (the merge head): the separator, the reason tail, and the PR's own state -----
# The review at `0b6cb52ca4c5` (the post-merge head) returned four findings; three were defects in
# THIS grammar and one was the merge resolution in `tests/test_ci_selection.py`. Each case below is
# pinned as a PAIR — the shape that must flag and the control that must not — for the reason rounds
# 13-19 recorded: every previous round moved one direction of error and traded it for the other.


@pytest.mark.parametrize("body,unresolved,why", [
    ("Part of #10543 — reverses the recorded #10119 trade.\n\n"
     "The arm was added to close. #10543 is the issue this PR serves.\n",
     10543, "a sentence-final verb plus a full stop"),
    ("Refs #100\n\nCloses. #100 remains open.\n", 100, "a full stop after the closing word"),
    ("Refs #100\n\nCloses (#100)\n", 100, "a parenthetical after the closing word"),
    ("Refs #100\n\nCloses — #100 is the umbrella.\n", 100, "an em dash after the closing word"),
    ("Refs #100\n\nCloses, #100 remains open.\n", 100, "a comma after the closing word"),
])
def test_a_closing_word_must_be_ADJACENT_like_githubs_own_grammar(body, unresolved, why):
    """Measured at the merge head `0b6cb52ca4c5` (review round 20, P1) — the FALSE PASS direction.

    Round 5 narrowed the WINDOW between a resolving word and its `#` and left the separator's CLASS
    carrying every clause end. So a sentence-final verb plus punctuation manufactured a closing
    resolution and arm 1 reported clean — `closes_landing.parse_closing_refs(body) == []`, i.e.
    GitHub performs NO close, while this lint had nothing to say. The separator is now
    `closes_landing`'s own (`\\s*:?\\s*`, `_CLOSING_SEPARATORS["lenient"]`), so the two grammars
    cannot disagree about whether a body closed a reference.

    Red-first: all five are RED against `0b6cb52ca4c5`'s tool (verified by swapping that head's file
    back in: `6 failed / 213 passed`, the sixth being the reason-tail case below).
    """
    assert L.claims(body, 1)["closing"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[unresolved]], why


@pytest.mark.parametrize("body,why", [
    ("Refs #100 — deliberately not closing this issue because it is a measurement PR.",
     "the convention with its reason attached"),
    ("Refs #100 — deliberately not closing this issue since the criteria are unproven.",
     "a `since` reason"),
    ("Refs #100 — deliberately not closing this issue (measurement only).",
     "a parenthetical qualifier"),
    ("Refs #100 — no close intended.", "`no … intended`"),
])
def test_an_honest_decline_may_carry_its_reason(body, why):
    """Measured at `0b6cb52ca4c5` (review round 20, P2) — the FALSE POSITIVE direction.

    The object's tail admitted `$`, `.`, `;`, a comma or a conjunction, so the most natural way to
    write the honest sentence — with the REASON attached — was flagged. The admitted shapes are one
    structural rule rather than a longer word list: whatever follows must contain NO `#`, so a
    reason clause can never sweep a live claim into the decline. That bound is pinned by the
    negative case below, which is the half that keeps this from being a new false PASS.
    """
    assert L.claims(body + "\n", 1)["declined"] == [100], why
    assert L.classify_pr(body + "\n", 1) == [], why


def test_a_reason_clause_that_names_a_reference_is_not_a_decline():
    """The other half of the rule above: a reason naming a `#N` is a live claim in the same breath
    as the decline, so it must NOT buy the decline — a structural bound, not a word list."""
    body = "Refs #100 — deliberately not closing this issue because #300 must land.\n"
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]]


@pytest.mark.parametrize("body,why", [
    ("Refs #100 — the PR is not closed.", "`the PR` is the pull request, not the reference"),
    ("Refs #100 — this PR is not closed.", "`this PR is not closed` is a merge state too"),
])
def test_the_pull_requests_own_merge_state_is_not_a_decline(body, why):
    """Measured at `0b6cb52ca4c5` (review round 20, P3) — the FALSE PASS direction.

    The bridge admitted bare `pr` as a subject word, so a sentence about the PULL REQUEST's merge
    state declined the reference and the claim vanished: `the PR is not closed` says nothing about
    whether #100 is closed. `pr`/`prs` are now admitted only in the two-word form whose
    demonstrative makes it the reference (`this PR does not close the issue`), and the state copulas
    (`is`/`was`/`are`/`were`) are admitted only behind a reference-only subject — both NARROWINGS of
    the decline direction, whose worst case is a false positive and never a new false PASS.
    """
    assert L.claims(body + "\n", 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body + "\n", 1)] == [[100]], why


@pytest.mark.parametrize("body,why", [
    ("Refs #100 — it is not closed.", "a pronoun subject IS the reference"),
    ("Refs #100 — this PR does not close the issue.", "an action with the reference as object"),
    ("Refs #100 — the author deliberately does not close the issue.", "a human agent"),
    ("Refs #100 — #100 is deliberately not Closes.", "the reference as the copula's subject"),
])
def test_the_narrowed_bridge_keeps_every_honest_decline(body, why):
    """The controls for the narrowing above — each is a decline the earlier rounds pinned, and each
    must survive it. Asserted here so the narrowing cannot be made green by deleting the branch."""
    assert L.claims(body + "\n", 1)["declined"] == [100], why
    assert L.classify_pr(body + "\n", 1) == [], why


def test_naming_the_parent_as_the_declines_object_is_a_DECLARED_residual():
    """`Refs #100 — deliberately not closing the parent.` is FLAGGED, and that is the recorded
    decision rather than an oversight (review round 20, P2, declared not fixed).

    Adding `parent`/`epic` to `_REF_NOUN` would decline `#100` by proximity, and the noun CAN name a
    different issue than the one on the line — the exact ambiguity the module's doctrine resolves
    the other way ("absent evidence is the flag, never the clear"). So the grammar does not guess:
    the honest author writes `… not closing this issue` / `… not closing #100`, which does decline.
    Pinned so the state is a decision a future round can reopen, not a silence it can "discover".
    """
    body = "Refs #100 — deliberately not closing the parent.\n"
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]]


@pytest.mark.parametrize("spelling, body", [
    ("does not close", "Part of #11000 — does not close `#11078`.\n"),
    ("not closing", "Part of #11000 — not closing `#11078`.\n"),
    ("no closing", "Part of #11000 — no closing of `#11078`.\n"),
    ("html comment", "Part of #11000 — does not close <!-- #11078 -->.\n"),
    ("deliberately not closing", "Part of #11000 — deliberately not closing `#11078`.\n"),
])
def test_an_unreadable_object_is_unreadable_in_every_spelling(spelling, body):
    """The unreadable-object guard was gated on alternative (a) (`deliberately not …`), so the SAME
    sentence read two ways depending on which spelling the author used (review, P3):

        Part of #11000 — deliberately not closing `#11078`.   -> flagged (guard fires)
        Part of #11000 — does not close `#11078`.             -> CLEAN, `#11000` dropped

    `strip_code` blanks the inline-code span (or the HTML comment) to whitespace, so the object is
    unreadable in every spelling; a decline the scan cannot read is not evidence, and falling
    through to PROXIMITY cleared the reference BEFORE the sentence. Both stems are now captured and
    the guard covers both.

    RED against the pre-fix tool for the three ordinary-English spellings (`declined == [11000]`,
    no finding); the `deliberately` row is the control that was already correct, kept here so the
    fix cannot be read as "the guard now fires on everything".

    The clause-terminal convention stays a decline, and is pinned here so the guard cannot be made
    to pass by suppressing the proximity path altogether.
    """
    assert L.claims(body, 1)["declined"] == [], spelling
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]], spelling

    terminal = ("Part of #11000 — does not close.\n" if spelling != "deliberately not closing"
                else "Part of #11000 — deliberately not closing.\n")
    assert L.claims(terminal, 1)["declined"] == [11000], spelling
    assert L.classify_pr(terminal, 1) == [], spelling

    # A READABLE noun object is still a decline — the guard must not swallow the honest sentence.
    assert L.classify_pr("Part of #11000 — does not close the issue.\n", 1) == [], spelling
    # …and a prose subject is still prose, so the claim stays flagged (the round-12 negative).
    assert L.classify_pr("Refs #1234 — the sprint is not closed.\n", 1) != [], spelling


@pytest.mark.parametrize("body, why", [
    ("Part of #11000 — does not close `#11078` because it is a measurement PR.\n",
     "inline code, alternative (b)"),
    ("Part of #11000 — deliberately not closing `#11078` (measurement only).\n",
     "inline code, alternative (a)"),
    ("Part of #11000 — does not close <!-- #11078 --> because it is a measurement PR.\n",
     "HTML comment"),
])
def test_a_reason_does_not_rescue_a_blanked_object(body, why):
    """The reason tail lets an honest decline carry WHY (round 20, P2). It must not become a route
    past the unreadable-object guard: a blanked object WITH a reason is still an object the scan
    cannot read, and clearing the reference before the sentence is the silent drop the guard exists
    to stop.

    The two are distinguishable because `strip_code` blanks a span to SPACES (length preserved) and
    DELETES an HTML comment, so a blanked object leaves a whitespace run of two or more characters
    between the stem and the reason, while an honest sentence has exactly one space there.
    RED without the run test: all three bodies decline `#11000` and arm 1 reports CLEAN.
    """
    assert L.claims(body, 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]], why


def test_a_reason_with_no_object_is_an_honest_decline():
    """The other half of the discriminator: with nothing blanked, a reason attached to a sentence
    that names no object is the author answering the question, so proximity applies.

    This is the pinned round-20 case (`no close intended.`) plus its two sibling spellings; it is
    asserted beside the run test so neither half can be "fixed" by tweaking the other without the
    pair moving together.
    """
    for body in (
        "Refs #100 — no close intended.\n",
        "Refs #100 — does not close because it is a measurement PR.\n",
        "Refs #100 — not closing (measurement only).\n",
    ):
        assert L.claims(body, 1)["declined"] == [100], body
        assert L.classify_pr(body, 1) == [], body
# --- review round 21 (the same head, the wider diff): the list, the fence/comment order, clause end
# Round 20's re-review verified its four findings held and then found three more on the WIDER diff.
# Same discipline: each is pinned as a PAIR, and each fix is a NARROWING or a bound, never a wider
# grammar — the rounds-13..19 lesson is that a widening trades one direction of error for the other.


@pytest.mark.parametrize("separator", [",", " and ", " or "])
def test_a_conjunction_continues_a_claim_list_like_a_comma_does(separator):
    """Measured at `ec38978a30c0` (review round 21, P2) — the FALSE PASS direction.

    The continuation admitted a COMMA only, so `Part of #100 and #300` claimed just #100. With #100
    resolved by a `Satisfies` elsewhere in the body, the run printed NOTHING FLAGGED while #300 —
    a claimed issue closed by nothing — had dropped out of the population entirely. The module was
    already inconsistent with itself: `_REF_LIST` admits `and`/`or` when DECLINING the same shape.
    A continuation is still a CLAIM and never a RESOLUTION (`_claims_in`), so a conjunction after a
    closing word cannot invent a close — pinned by the `Closes` case below.
    """
    body = f"Satisfies #100\n\nPart of #100{separator}#300\n"
    assert L.claims(body, 11078)["weak"] == [100, 300]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 11078)] == [[300]]


def test_a_conjunction_after_a_closing_word_is_a_CLAIM_not_a_resolution():
    """The bound that makes the widening safe: only the FIRST ref of a closing keyword resolves."""
    c = L.claims("Closes #100 and #300\n", 11078)
    assert c["closing"] == [100]
    assert c["weak"] == [300]
    assert [f.evidence["unresolved"] for f in L.classify_pr("Closes #100 and #300\n", 11078)] == [[300]]


def test_an_unterminated_comment_inside_a_fence_cannot_delete_the_prose_after_it():
    """Measured at `ec38978a30c0` (review round 21, P3) — the FALSE PASS direction.

    HTML comments were stripped from the RAW body before the fence grammar ran. That order handled
    the case its own comment gave for it (a COMMENT containing a fence) and left the mirror
    unhandled: a FENCE containing an unterminated `<!--`, whose opener then paired with a `-->` in
    the prose after the fence and deleted every line between them — including a real claim:

        ```markdown / <!-- / ``` / (blank) / Part of #100 / (blank) / -->

    read NOTHING FLAGGED, rc 0, and the only signal named the fence (which had hidden nothing). The
    fence grammar now runs first, so the `<!--` is code and is gone before the comment strip.
    """
    body = ("Example of the marker opener:\n\n```markdown\n<!--\n```\n\nPart of #100\n\n-->\n")
    assert L.claims(body, 1)["weak"] == [100]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]]
    # the direction the old order was written for still holds: a well-formed comment hides its refs
    assert L.classify_pr("<!-- tracking: Refs #4282 -->\nNothing claimed.\n", 11064) == []
    assert L.claims("<!--\n```markdown\nRefs #4282\n```\n-->\n", 11064)["weak"] == []


@pytest.mark.parametrize("punct", [".", ";"])
def test_an_ordinary_english_decline_ends_at_its_clause_too(punct):
    """Measured at `ec38978a30c0` (review round 21, P3) — the FALSE POSITIVE direction.

    Alternative (a) (`deliberately not …`) admits `$`, `.` and `;` as clause ends; alternative (b),
    the ordinary-English shapes, demanded END OF LINE. So the same honest sentence was flagged
    without the convention word and declined with it:

        Refs #100 — not closing it. The plan tracks the rest.          -> flagged
        Refs #100 — deliberately not closing it. The plan tracks ...   -> CLEAN

    A full stop or a semicolon IS clause-terminal, which is what `_DECLINE_TAIL`'s own comment says
    the rule is — so this is the code not implementing its stated rule, in the direction the module
    treats as the one to avoid.
    """
    body = f"Refs #100 — not closing it{punct} The plan tracks the rest.\n"
    assert L.claims(body, 1)["declined"] == [100]
    assert L.classify_pr(body, 1) == []


def test_the_clause_end_admission_still_does_not_reach_a_later_sentence_s_claim():
    """The bound on the case above: the decline ends at its clause, so a claim in the NEXT sentence
    is untouched. `#300` is claimed by `Refs #300` and closed by nothing, so it must flag."""
    body = "Refs #100 — not closing it. Refs #300 is separate.\n"
    assert L.claims(body, 1)["declined"] == [100]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[300]]


@pytest.mark.parametrize("body, expected, why", [
    ("Closes #100 (the parent), #200 (the child).\n",
     {"closing": [100], "weak": [200], "continued_resolving": [200]},
     "a gloss on the FIRST ref does not end the list"),
    ("Refs #10892 (the served-but-unusable blind spot), #10602 (graph latency).\n",
     {"weak": [10602, 10892]}, "the live #11093 body shape"),
    ("Part of #100 (a) and #200 (b)\n", {"weak": [100, 200]}, "conjunction with glosses"),
])
def test_a_parenthetical_gloss_does_not_end_the_claim_list(body, expected, why):
    """Measured at `06bc97f30` (review P2): the continuation required a `#N` immediately after the
    separator, so ONE parenthetical gloss between the first ref and the comma ended the list —
    `#200` was never evaluated, `Closes #100 (the parent), #200 (the child).` claimed `#100` alone,
    and arm 1 read CLEAN on a reference the author named under a closing keyword GitHub does not
    close. Not hypothetical: #11093's body carries the shape.

    The gloss is `_GLOSS` — ONE balanced parenthetical holding no `#` — shared with the decline's
    list and the plural run, so the three cannot drift. The control below pins that a gloss naming a
    reference does NOT bridge: `(see #999)` is a different clause, and treating it as a list
    continuation would claim `#200` on the strength of a sentence about `#999`.
    """
    got = {k: v for k, v in L._claims_in(L.strip_code(body)[0], None).items() if v}
    assert got == expected, why
    assert L._claims_in(L.strip_code("Closes #100 (see #999), #200.\n")[0], None)["weak"] == []


def test_a_glossed_list_is_one_list_for_the_decline_and_the_plural_run_too():
    """The same token, read by all three: the list a claim CONTINUES, the list a decline NAMES and
    the run a plural decline CLEARS. Admitting a gloss in one reader and not the others is the drift
    this module keeps re-discovering — and each direction has its own failure: a decline that cannot
    name the glossed list FLAGS an honest sentence, and a run that cannot bridge the gloss CLEARS
    one reference of a plural decline's list.
    """
    named = "Refs #100 (a), #200 (b) — deliberately not closing #100 (a) and #200 (b).\n"
    assert L.claims(named, 1)["declined"] == [100, 200]
    assert L.classify_pr(named, 1) == []

    plural = "Refs #100 (a), #200 (b) — deliberately not closing them.\n"
    assert L.claims(plural, 1)["declined"] == [100, 200]
    assert L.classify_pr(plural, 1) == []


@pytest.mark.parametrize("body", [
    "Part of #11000 — does not close<!-- #11078 --> because it is a measurement PR.\n",
    "Part of #11000 — deliberately not closing<!-- #11078 --> (measurement only).\n",
])
def test_a_comment_object_that_abuts_the_stem_is_still_unreadable(body):
    """An HTML comment object leaves NO whitespace of its own, so the run test that separates a
    blanked object from an honest reason was defeated when the comment abutted the closing stem
    (measured at `06bc97f30`, review P2):

        does not close<!-- #11078 --> because …   -> deleted: `does not close because …`, ONE space
                                                      -> `honest_reason` -> proximity -> CLEAN

    `strip_code` now BLANKS a comment to equal-length spaces instead of deleting it — offsets
    survive, and a blanked object always leaves a run of two or more. RED against the deleting
    implementation: both bodies decline `#11000` and arm 1 reports CLEAN.
    """
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[11000]]


@pytest.mark.parametrize("body", [
    "Refs <!-- prettier-ignore --> #1234\n",
    "Refs <!-- tracking: #4282 --> #1234\n",
])
def test_a_blanked_comment_between_a_claiming_word_and_its_ref_still_claims(body):
    """The regression the blanking fix could have introduced, pinned: a comment between `Refs` and
    the reference is now a run of SPACES longer than the 16-character window, so the window alone
    would stop matching and the claim would silently disappear — a false PASS created by a
    false-PASS fix. `CLAIM_RE`'s `mid` therefore also admits a run of HORIZONTAL whitespace of any
    length (never `\\s`, which would let a claim cross a newline — asserted here).
    """
    assert L.claims(body, 1)["weak"] == [1234]
    assert L.claims("Refs\n#1234\n", 1)["weak"] == [], "a claim must not cross a newline"


# --- review round 22 (the same head, the whole artifact): contractions, the doubled colon, the PR --
# Three findings from the third fresh-context review, each a ONE-DIRECTION defect with a paired
# control: two are class B (an honest decline flagged) and one is class A (a claim dropped). Pinned
# in pairs for the same reason as rounds 20 and 21 — a fix that only moves one direction is the
# pattern this grammar spent seven rounds on.


@pytest.mark.parametrize("negation", [
    "isn't", "wasn't", "aren't", "doesn't", "don't", "didn't", "can't", "cannot", "won't",
    "hasn't", "couldn't", "shouldn't", "never",
])
def test_a_contracted_negation_is_the_same_honest_decline(negation):
    """Measured at `53646f1f6fd0` (review round 22, P2) — the FALSE POSITIVE direction.

    Alternative (b) admitted `does not` / `not` / `no ` / `non-` and NO contraction, so the most
    natural spelling of the honest sentence was flagged while its uncontracted twin declined:

        `Refs #100 — it isn't closed.`        -> FLAGGED   (`it is not closed.`      -> clean)
        `Refs #100 — this doesn't close it.`  -> FLAGGED   (`this does not close it.` -> clean)
        `Refs #100 — we can't close it.`      -> FLAGGED

    Contractions are neither pinned nor mentioned anywhere else in this file, so this was a gap and
    not a decision. They are ALTERNATIONS of the negation, not a wider tail: the clause-end and
    reference-naming rules still apply to each (the negative pair below).
    """
    body = f"Refs #100 — it {negation} closed.\n" if negation in ("isn't", "wasn't", "aren't") \
        else f"Refs #100 — this {negation} close it.\n"
    assert L.claims(body, 1)["declined"] == [100], body
    assert L.classify_pr(body, 1) == [], body


def test_a_contracted_negation_about_another_subject_is_still_prose():
    """The bound on the case above: the bridge still decides WHOSE state the sentence is about, so a
    contraction about an ordinary noun leaves the claim flagged (the round-12 negative, contracted)."""
    for body in ("Refs #100 — the sprint isn't closed.\n",
                 "Refs #100 — the estimate doesn't close the delta.\n",
                 "Refs #100 — the outage wasn't fixed.\n"):
        assert L.claims(body, 1)["declined"] == [], body
        assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]], body


@pytest.mark.parametrize("body", [
    "Refs #100\n\nCloses:: #100\n",
    "Refs #100\n\nCloses: :#100\n",
    "Refs #100\n\nFixes:: #100\n",
    "Refs #100\n\nResolves::#100\n",
])
def test_a_doubled_colon_does_not_manufacture_a_close(body):
    """Measured at `53646f1f6fd0` (review round 22, P3) — the FALSE PASS direction.

    `cmid` was written as the character class `[\\s:]*` while its own comment claimed the separator
    was `closes_landing`'s `\\s*:?\\s*`. A class cannot express "at most one colon", so `Closes::
    #100` was credited as a resolution the landing grammar does not credit — measured as 33
    divergences across a keyword × separator sweep, all doubled-colon, all in this direction.
    """
    assert L.claims(body, 1)["closing"] == [], body
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]], body
    # …and the two grammars now AGREE on every legal spelling, which is the invariant the comment
    # claims: a keyword the landing grammar credits is a resolution here, and vice versa.
    import closes_landing
    for legal in ("Closes #100", "Closes: #100", "Closes : #100", "closes #100"):
        assert closes_landing.parse_closing_refs(legal, "o/r") == [100], legal
        assert L.claims(legal + "\n", 1)["closing"] == [100], legal


def test_the_pull_request_is_not_the_object_of_a_decline():
    """Measured at `53646f1f6fd0` (review round 22, P3) — the FALSE PASS direction, and the mirror of
    round 20's subject fix: `pr` was removed from the SUBJECT words and left in the OBJECT nouns.

        Refs #100 — not closing the PR.        -> CLEAN, declined=[100]   (#100 dropped)
        Refs #100 — does not close the PR.     -> CLEAN, declined=[100]

    "not closing the PR" is a statement about the pull request's merge state — the very thing
    `test_the_pull_requests_own_merge_state_is_not_a_decline` says "says NOTHING about whether #100
    is closed" — so it must not clear `#100`. The honest objects (`this issue`, `#100`) are pinned
    here as controls so the removal cannot be made green by deleting the object grammar.
    """
    for body in ("Refs #100 — not closing the PR.\n", "Refs #100 — does not close the PR.\n"):
        assert L.claims(body, 1)["declined"] == [], body
        assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]], body
    for body in ("Refs #100 — not closing this issue.\n", "Refs #100 — does not close #100.\n"):
        assert L.claims(body, 1)["declined"] == [100], body
        assert L.classify_pr(body, 1) == [], body


def test_a_list_does_not_continue_across_a_blank_line():
    """Measured at `53646f1f6fd0` (review round 22, P3-1) — the FALSE POSITIVE direction.

    The continuation's separator was `\\s`, which includes `\\n`, so a conjunction or a comma in a
    FOLLOWING PARAGRAPH continued the list and a legitimate MENTION was claimed and flagged:

        Closes #100\\n\\nand #200 is tracked separately.   -> closing=[100], weak=[200]  -> rc 3
        Refs #100,\\n\\n#200                               -> weak=[100, 200]            -> rc 3

    The mirror grammar already refuses that gap (`_REF_RUN_GAP_RE`/`_LIST_SEP`: "bare whitespace is
    excluded because `#100 #200` is not an English list … the conservative reading of an ambiguous
    gap is to END the run"), so this was the two-populations-one-grammar inconsistency again. ONE
    newline is still admitted — the wrapped list below is legitimate and stays claimed — but a blank
    line ends it, the same `\\n?`-never-`\\n\\n` rule `closes_landing` states for its own separator.
    """
    for body in ("Part of #100\n\nand #300 is tracked separately.\n",
                 "Part of #100\n\nor #300 for the follow-up.\n",
                 "Refs #100,\n\n#200\n"):
        assert L.claims(body, 1)["weak"] == [100], body
        # `Part of`/`Refs` close nothing, so #100 is correctly flagged — and #300/#200 is NOT.
        assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]], body
    # …and the closing spelling, whose first ref resolves rather than linking: no finding at all.
    closing = "Closes #100\n\nand #200 is tracked separately.\n"
    assert L.claims(closing, 1)["closing"] == [100]
    assert L.claims(closing, 1)["weak"] == []
    assert L.classify_pr(closing, 1) == []


def test_a_wrapped_list_is_still_one_list():
    """The other half: a single SOFT line break is a wrapped list, not a paragraph break — so the
    narrowing above cannot be made green by ending every list at the first newline.

    The CONJUNCTION form is the deliberate exception: `and` at end of line with its reference on the
    next one is genuinely ambiguous (it reads as prose continuing the sentence), so it does not
    continue the list. Pinned so the asymmetry is a decision, not an oversight.
    """
    assert L.claims("Part of #10085,\n#10063\n", 1)["weak"] == [10063, 10085]
    assert L.claims("Part of #100 and\n#300\n", 1)["weak"] == [100]


# --- review round 23: the same rule, read in the spelling that was not pinned ---------------------


@pytest.mark.parametrize("body", [
    "Refs #100 — not closing it, because the criteria are unproven.\n",
    "Refs #100 — not closing it, since the criteria are unproven.\n",
    "Refs #100 — does not close the issue, since it is a measurement PR.\n",
    "Refs #100 — deliberately does not close the issue, since it is a measurement PR.\n",
])
def test_a_comma_may_introduce_the_reason_in_EITHER_spelling(body):
    """Measured at `fd9d3e1f0a98` (review round 23, P2) — the FALSE POSITIVE direction.

    The reason tail was reachable without a comma in both spellings and with a comma in only ONE, so
    the engine of the whole "a decline may carry its reason" rule worked in 3 of the 4 cells of that
    2 × 2:

        not closing it, because …                       -> FLAGGED
        deliberately not closing it, because …          -> declined
        does not close the issue, since …               -> FLAGGED
        deliberately does not close the issue, since …  -> FLAGGED (this stem is alternative (b))

    That is the same one-spelling-at-a-time gap this grammar has produced in four consecutive rounds.
    The comma is admitted as the REASON's preamble and not as a bare clause end, so round 12's pinned
    negative (`Refs #1234 — non-closing reference, see the review.`, which must stay FLAGGED) is
    untouched, and a reason that names a reference still cannot buy the decline (pinned below).
    """
    assert L.claims(body, 1)["declined"] == [100], body
    assert L.classify_pr(body, 1) == [], body


def test_a_comma_introducing_a_reason_that_NAMES_a_reference_is_still_not_a_decline():
    """The bound: `_REASON_TAIL` requires the remainder to hold no `#`, so the comma route cannot
    swallow a live claim. `#300` is not claimed by this body at all, but the decline must not buy
    `#100` on the strength of a sentence about `#300`."""
    body = "Refs #100 — not closing it, because #300 must land.\n"
    assert L.claims(body, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]]


def test_a_parenthetical_does_not_reopen_the_cross_paragraph_claim():
    """Measured at `fd9d3e1f0a98` (review round 23, P3) — the FALSE POSITIVE direction.

    `_GLOSS`'s LEADING gap was `_LIST_SEP`, which contains `\\s`, so ONE parenthetical ahead of a
    conjunction quietly restored the cross-paragraph claim round 22's `_SOFT_GAP` rule had just
    removed:

        Closes #100\\n\\nand #200 is a separate follow-up.                -> closing=[100]  (clean)
        Closes #100\\n\\n(see the plan) and #200 is a separate follow-up.  -> weak=[200]      (flagged)

    The blank line is the gap, not the parenthetical, so the leading token is now the soft one. A
    single wrapped line break is still admitted, which is what keeps a glossed list a list.
    """
    for body in ("Closes #100\n\n(see the plan) and #200 is a separate follow-up.\n",
                 "Closes #100\n\n(see the plan) or #200 is a separate follow-up.\n"):
        assert L.claims(body, 1) == {**L.claims(body, 1), "closing": [100], "weak": []}, body
        assert L.classify_pr(body, 1) == [], body
    # control: a gloss followed by a comma-separated list is still one list
    assert L.claims("Closes #100 (the parent), #200 (the child).\n", 1)["weak"] == [200]


@pytest.mark.parametrize("body, why", [
    ("Refs <!-- an explanation\nover two lines --> #1234\n", "a wrapped comment"),
    ("Part of <!-- x\ny --> #1234\n", "a wrapped comment after a linking word"),
    ("Closes <!-- x\ny --> #1234\n", "…and after a closing word"),
])
def test_a_multi_line_comment_between_a_claiming_word_and_its_ref_still_claims(body, why):
    """Blanking a comment PRESERVED its newlines, so a comment wrapped over two lines was not the
    run of HORIZONTAL whitespace `CLAIM_RE`'s `mid` widening exists to consume — and the claim the
    author made was silently dropped, arm 1 CLEAN (measured at `8c74e5fca`, review P3):

        Refs <!-- an explanation
        over two lines --> #1234        -> no claim at all, NOTHING FLAGGED

    A comment is now blanked character-for-character with each SOFT line break (a lone `\n`)
    collapsed to ONE space, so the claim survives. RED against the newline-preserving blanking:
    `claims()` returns `{}` and there is no finding. The closing-word spelling is included because
    it is the one that never broke (`cmid` is ``\\s*:?\\s*`` — the separator ITSELF, never a
    character class (see the `cmid` note above), so it crossed the blanked newline) — the pair keeps
    the two keyword classes honest. The BLANK-line case is the next test: it must NOT collapse.
    """
    claimed = L.claims(body, 1)
    assert claimed["weak"] + claimed["closing"] == [1234], why
    assert claimed["declined"] == [], why


@pytest.mark.parametrize("body, why", [
    ("Closes #100 <!-- x\n\ny --> and #200 is separate.\n", "the comment carries the blank line"),
    ("Closes #100<!--\n\n--> and #200 is separate.\n", "the comment IS the blank line"),
    ("Closes #100 <!-- a\nb\n\nc --> and #200 is separate.\n", "a soft break then a blank line"),
])
def test_a_blank_line_inside_a_comment_is_still_a_paragraph_boundary(body, why):
    """A claim list does not cross a BLANK LINE — that rule has to hold inside a comment too.

    Blanking every character of a comment with `.` under `DOTALL` also matched `\n`, so a comment
    that spanned a blank line spliced the paragraphs on either side into one, and arm 1 then claimed
    a ref the author had deliberately put in a SEPARATE paragraph (measured at `23d7a77ab`):

        `Closes #100 <!-- x` / blank / `y --> and #200 is separate.`
            -> FLAGGED, claimed [100, 200], unresolved [200]

    The renderer is the oracle and says that body is two paragraphs, with `#200` in the second — so
    the flag is the false-positive direction this module ranks as worse than the defect it catches:
    `MarkdownIt("commonmark")` renders `<p>Closes #100 &lt;!-- x</p>` + `<p>y --&gt; and #200 is
    separate.</p>`. The CONTROL below is the same document shape written without the comment on the
    blank line, and it was already CLEAN — the two must agree.

    RED against the pre-fix module: every row is flagged with `unresolved == [200]`, and the control
    is clean, so the test fails on the difference rather than on both sides being red.
    """
    assert L.classify_pr(body, 1) == [], f"{why}: {L.classify_pr(body, 1)}"
    assert L.claims(body, 1)["closing"] == [100], why
    assert 200 not in L.claims(body, 1)["closing"], why

    # …and the renderer agrees these bodies are TWO paragraphs, so CLEAN is the right verdict and
    # not merely a lenient one.
    rendered = MarkdownIt("commonmark").render(body)
    assert rendered.count("<p>") == 2, (why, rendered)


def test_the_control_for_the_comment_blank_line_shape_is_still_clean():
    """The shape the finding measured AGAINST — same two paragraphs, the comment off the blank line.

    It was CLEAN at `23d7a77ab` and must stay CLEAN: if this row ever reds, the fix has gone too far
    in the pass direction rather than the false-positive one being repaired.
    """
    body = "Closes #100\n\n<!-- x\ny -->\nand #200 is separate.\n"
    assert L.classify_pr(body, 1) == []
    assert L.claims(body, 1)["closing"] == [100]


def test_blanking_a_comment_keeps_every_offset_and_every_blank_line():
    """The docstring's claim, made executable: blanking is LENGTH-preserving and keeps every BLANK line.

    Both were false before the round-37 fix, one direction each — deletion shortened the body
    (round 21), and `.`-under-`DOTALL` collapsed two lines into one (round 37). A reader can rely on
    neither unless it is pinned: offsets are what the guards around the blanked object measure, and
    the BLANK lines are what the paragraph rule counts. (A lone newline is deliberately NOT kept —
    it is a soft break and becomes one space; see the wrapped-comment test above.)
    """
    for body in ("Closes #100 <!-- x\n\ny --> and #200.\n",
                 "Part of #11000 — does not close<!-- #11078 --> because it is a measurement PR.\n",
                 "Refs <!-- an explanation\nover two lines --> #1234\n",
                 "<!-- a\n\nb\n\n\nc -->\n"):
        stripped, _ = L.strip_code(body)
        # `strip_code` drops the body's trailing newline; nothing else may change the length.
        trimmed = body.rstrip("\n")
        assert len(stripped) == len(trimmed), body
        assert stripped.count("\n\n") == trimmed.count("\n\n"), body


def test_a_gloss_ending_on_an_auxiliary_cannot_carry_a_clause_subject_past_the_bridge():
    """The bridge's gloss was `_GLOSS` — opaque, any ≤ 80 characters without `)` or `#` — so a
    clause could move its SUBJECT one level into the parenthetical and go CLEAN, while the inlined
    form of the SAME sentence flags (measured at `8c74e5fca`, review P3):

        Refs #1234 (the outage on the other box is) — not closed.   -> declined, CLEAN
        Refs #1234 — the outage on the other box is not closed.     -> flagged

    The bridge's closed vocabulary exists so a real subject noun is prose about that noun and not a
    decline; a gloss whose content ENDS on an auxiliary is that subject with its verb parked
    outside, so `_GLOSS_BRIDGE` refuses it. The honest glosses the feature exists for still decline
    — asserted here, so the narrowing cannot be made green by removing the gloss altogether.
    """
    subject = "Refs #1234 (the outage on the other box is) — not closed.\n"
    assert L.claims(subject, 1)["declined"] == []
    assert [f.evidence["unresolved"] for f in L.classify_pr(subject, 1)] == [[1234]]

    for honest in (
        "Refs #10892 (blind spot), #10602 (latency) — deliberately not closing them.\n",
        "Refs #100 (the parent) — deliberately not closing it.\n",
        "Refs #100 (a) — not closing it.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest


def test_a_gloss_with_a_MID_CLAUSE_auxiliary_cannot_carry_the_subject_either():
    r"""Round 27, P2 — the SAME class-A silent drop as the case above, one word over.

    The discriminator was END-anchored (`[\W_]*\)` after the auxiliary), so a gloss whose auxiliary
    sat mid-clause slipped past it and still carried the clause subject past the bridge:

        Refs #1234 (the sprint is, apparently) — not closed.   -> declined=[1234], CLEAN  # #1234 dropped
        Refs #1234 — the sprint is not closed.                 -> flagged                  # the control

    The residual was DECLARED in the code and nowhere else, so the merge record certified a residual
    set that did not contain it. `\b{_AUX_TAIL}\b` now refuses the auxiliary anywhere in the gloss.
    """
    for dropped in (
        "Refs #1234 (the sprint is, apparently) — not closed.\n",
        "Refs #1234 (the outage is, as far as i can tell) — not closed.\n",
        "Refs #1234 (it did, eventually) — not closed.\n",
    ):
        assert L.claims(dropped, 1)["declined"] == [], dropped
        assert [f.evidence["unresolved"] for f in L.classify_pr(dropped, 1)] == [[1234]], dropped

    # the controls: a gloss naming NO auxiliary still declines, so the narrowing did not delete the
    # feature — `(2026-11-05.)` is why the tail is anchored on the auxiliary and not on punctuation.
    for honest in (
        "Refs #1234 (blind spot) — not closed.\n",
        "Refs #100 (the parent.) — not closed.\n",
        "Refs #100 (P3.) — not closed.\n",
        "Refs #100 (2026-11-05.) — not closed.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest


def test_a_CONTRACTED_auxiliary_in_a_gloss_cannot_carry_the_subject_either():
    r"""Round 28, P2 — a class-A silent drop: the auxiliary discriminator is blind to contractions.

    `_AUX_TAIL`'s tail is `(?:[\W_]|$)`, i.e. "the auxiliary ends the gloss OR is followed by a
    non-word character". A CONTRACTION puts a WORD character there — `isn't` is `is` + `n` — so the
    lookahead never fired and the gloss carried a clause subject past the bridge, dropping the claim:

        Refs #1234 (the sprint isn't) — not closed.            -> declined=[1234], CLEAN  # #1234 DROPPED
        Refs #1234 (the migration didn't land) — not closed.    -> declined=[100],  CLEAN  # DROPPED
        Refs #1234 (the sprint is, apparently) — not closed.   -> flagged                 # the control

    `n['’]?t` is now an admitted tail, and `wo` is spelled out because `won't` does not begin with
    `will`. Both arms only widen REFUSALS, so neither can cost a claim — the controls below keep the
    honest glosses declining.
    """
    for dropped in (
        "Refs #1234 (the sprint isn't) — not closed.\n",
        "Refs #100 (the migration didn't land) — not closed.\n",
        "Refs #100 (the sprint hasn't) — not closed.\n",
        "Refs #100 (the box won't) — not closed.\n",
    ):
        assert L.claims(dropped, 1)["declined"] == [], dropped
        assert [f.evidence["unresolved"] for f in L.classify_pr(dropped, 1)] == [[
            1234 if "1234" in dropped else 100]], dropped

    # the controls: a gloss naming NO auxiliary still declines, and the uncontracted spelling is
    # still refused — so the widening above did not delete the feature it protects.
    for honest in (
        "Refs #1234 (blind spot) — not closed.\n",
        "Refs #100 (the parent.) — not closed.\n",
        "Refs #100 (a) — not closed.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest
    for flagged in ("Refs #1234 (the sprint is, apparently) — not closed.\n",):
        assert [f.evidence["unresolved"] for f in L.classify_pr(flagged, 1)] == [[1234]], flagged

def test_a_colon_introduces_a_reason_like_the_other_spellings():
    """Round 28, P3 — the SAME honest decline flipped verdict on the punctuation alone.

    `_DECLINE_TAIL` treats `:` as clause punctuation, but the terminator admitted only `;`, `.`, the
    four reason words, `(` and a dash — so the colon spelling was the one form of the author's own
    convention that was FLAGGED:

        Refs #100 — deliberately not closing: measurement only.   -> FLAGGED
        Refs #100 — deliberately not closing; measurement only.   -> declined
        Refs #100 — deliberately not closing — measurement only.  -> declined

    The structural rule is unchanged: the reason may contain no `#`, so it still cannot sweep a live
    claim into the decline — asserted here with the pinned negative.
    """
    for honest in (
        "Refs #100 — deliberately not closing: measurement only.\n",
        "Refs #100 — deliberately not closing: the estimate is still open.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest

    # the reason may not name a reference — that would buy the decline with a second claim
    for still_flagged in (
        "Refs #100 — not closing it: #300 must land first.\n",
        "Refs #100 — non-closing reference, see the review.\n",
    ):
        assert [f.evidence["unresolved"] for f in L.classify_pr(still_flagged, 1)] == [[100]], still_flagged


def test_a_closing_list_after_an_UNREADABLE_gloss_under_counts_and_is_a_DECLARED_residual():
    """Round 28, P2 — the `closing` bucket under-counts; the UNRESOLVED direction was measured and
    is NOT a false PASS.

    `_GLOSS` cannot read a parenthetical holding a `#`, a `)`, or more than 80 characters, so the
    claim list stops at the gloss and a ref sharing that `Closes` list is not counted:

        Closes #100 (see #999), #200.      -> closing=[100]        # #200 uncounted
        Closes #100 (the parent), #200.    -> closing=[100], weak=[200]   # the readable control

    ⛔ BUT THE FINDING'S STRONGER CLAIM — "every `#N` after the gloss is silently dropped and arm 1
    reads CLEAN" — is FALSIFIED by measurement: a ref in its OWN claim is still read, so the case
    that would matter (an unresolved claim vanishing) does not occur:

        Closes #100 (see #999), Refs #200.  -> weak=[200], FLAGGED
        Refs #100 (see #999), #200.         -> weak=[100], FLAGGED

    So the residual is an under-count of the RESOLVED set (which can under-report what a PR closes),
    not a dropped claim. Making the gloss non-terminating is a change to the claim-list grammar —
    the part rounds 22–25 kept trading one direction of error for the other in — so it is declared
    here rather than restructured at the end of this lane. Pinned so it is a decision, not a silence.
    """
    under = "Closes #100 (see #999), #200.\n"
    assert L.claims(under, 1)["closing"] == [100], "the under-count this residual names"
    assert L.classify_pr(under, 1) == [], "rc 0 is correct: both refs ARE closed by `Closes`"
    # the control: a readable gloss keeps the list, so the narrowing is the gloss reader alone
    control = "Closes #100 (the parent), #200.\n"
    assert 200 in L.claims(control, 1)["closing"] + L.claims(control, 1)["weak"]
    # ⛔ and the falsified half — an UNRESOLVED claim is never dropped
    for flagged in (
        "Closes #100 (see #999), Refs #200.\n",
        "Refs #100 (see #999), #200.\n",
    ):
        assert L.classify_pr(flagged, 1) != [], flagged


def test_the_abbreviation_split_is_a_DECLARED_residual():
    """Round 16/17's residual, which this body's ledger claimed was pinned and was not.

    `Refs #100, #200 — deliberately not closing #100, e.g. #200.` declines `#100` and flags `#200`:
    the abbreviation splits the object list. An example is arguably not the same list, so the
    grammar does not stitch it — the honest author writes both refs out without the `e.g.`.
    """
    body = "Refs #100, #200 — deliberately not closing #100, e.g. #200.\n"
    assert L.claims(body, 1)["declined"] == [100]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[200]]
    # the control: the same list written without the abbreviation declines BOTH
    control = "Refs #100, #200 — deliberately not closing #100, #200.\n"
    assert L.classify_pr(control, 1) == [], control


def test_a_STATE_copula_after_an_agent_subject_is_a_DECLARED_residual():
    """Round 27, P3 — a CLASS-B false positive, declared rather than fixed, and pinned so the state
    is a recorded decision instead of a silence the next round can "discover".

    The bridge admits an ACT (`does`/`do`/modals) after a human-agent subject but not a STATE
    (`is`/`am`/`are`/`was`/`were`), so the same honest decline flips verdict on the auxiliary alone:

        Refs #100 — we do not close it.              -> declines
        Refs #100 — we are not closing it.           -> FLAGGED
        Refs #100 — the author does not close the issue.  -> declines
        Refs #100 — the author is not closing the issue.  -> FLAGGED

    Admitting the state copula after those subjects is NOT a one-line widening: `we are not closed`
    would then decline too, and that sentence says nothing about #100 — the round-13/round-20
    class-A hole this narrowing exists to close. Closing it needs the object to be proven to name
    the reference, which is a change to the target RESOLUTION, not to the bridge; the honest author
    has spellings that decline (`does not close it`, `not closing #100`). Declared, not guessed at.
    """
    # the false-positive direction, pinned as the current (declared) behaviour
    for flagged in (
        "Refs #100 — we are not closing it.\n",
        "Refs #100 — the author is not closing the issue.\n",
        "Refs #100 — I am not closing it.\n",
    ):
        assert L.claims(flagged, 1)["declined"] == [], flagged

    # the ACT spelling of each is the working form, and the class-A hole stays closed
    for honest in (
        "Refs #100 — we do not close it.\n",
        "Refs #100 — the author does not close the issue.\n",
        "Refs #100 — I do not close it.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest
    for about_something_else in (
        "Refs #100 — we are not closed.\n",
        "Refs #100 — the PR is not closed.\n",
        "Refs #100 — the sprint is not closed.\n",
    ):
        assert [f.evidence["unresolved"] for f in L.classify_pr(about_something_else, 1)] == [[100]]
# --- review round 24: one list grammar read by three readers, and one object slot -------------------


def test_the_ampersand_separator_is_read_by_ALL_three_readers():
    """Measured at `b45bb9cf31c5` (review round 24, P2) — the FALSE POSITIVE direction.

    `CLAIM_RE` admitted `and|or|&` while `_REF_LIST` and `_REF_RUN_GAP_RE` admitted only `and|or`, so
    the invariant those two constants state about themselves ("ONE definition, read by CLAIM_RE,
    `_REF_LIST` and `_REF_RUN_GAP_RE`") did not hold:

        Refs #100 & #200 — deliberately not closing them.   -> claimed #100, declined #200, FLAGGED
        Refs #100 and #200 — deliberately not closing them. -> clean

    An author who declined, warned, only because of how they spelled the conjunction. Either all
    three readers admit `&` or none does; the control on the next line is the twin spelling.
    """
    assert L.claims("Refs #100 & #200 — deliberately not closing them.\n", 1)["declined"] == [100, 200]
    assert L.claims("Refs #100 & #200\n", 1)["weak"] == [100, 200]
    assert L.claims("Refs #100 and #200 — deliberately not closing them.\n", 1)["declined"] == [100, 200]


@pytest.mark.parametrize("body", [
    "Refs #100 — not closing a closing one.\n",
    "Refs #100 — does not close a closing one.\n",
    "Refs #100 — not closing the closing ref.\n",
])
def test_naming_the_reference_by_its_kind_works_in_EITHER_spelling(body):
    """Measured at `b45bb9cf31c5` (review round 24, P3) — the FALSE POSITIVE direction.

    `_CLOSING_OBJ` (the object that names the reference by its KIND — `a closing one`, `the closing
    ref`) sits in alternative (a)'s object slot only, so round 19's sentence read two ways depending
    on which negation spelling the author used. Round 19 fixed it for the spelling it tested; this
    pins the other three cells of the same 2 × N.
    """
    assert L.claims(body, 1)["declined"] == [100], body
    assert L.classify_pr(body, 1) == [], body


def test_a_decline_on_a_WRAPPED_lists_own_line_is_found():
    """Measured at `b45bb9cf31c5` (review round 24, P2) — the FALSE POSITIVE direction.

    Every ref of a claim-list match was looked up against the line the CLAIMING KEYWORD sits on, so a
    decline written on a continuation ref's own line was discarded and a markdown reflow flipped the
    verdict on the same sentence:

        Refs #100, #200 — #100 is deliberately not Closes.      -> declined [100], flags [200]
        Refs #100,\\n#200 — #100 is deliberately not Closes.      -> flagged BOTH (before this fix)

    The claim reader already spans the break (`test_a_wrapped_list_is_still_one_list`), so the
    decline reader now looks at every line the match covers. The named-ref form and its one-line
    control must agree — which is the assertion below.
    """
    wrapped = "Refs #100,\n#200 — #100 is deliberately not Closes.\n"
    flat = "Refs #100, #200 — #100 is deliberately not Closes.\n"
    assert L.claims(wrapped, 1)["declined"] == L.claims(flat, 1)["declined"] == [100]
    assert [f.evidence["unresolved"] for f in L.classify_pr(wrapped, 1)] == [[200]]


def test_a_plural_decline_across_a_wrapped_list_is_a_DECLARED_residual():
    """`Part of #10085,\\n#10063 — deliberately not closing them.` declines `#10063` and still flags
    `#10085` (review round 24, P2, declared not fixed).

    The plural run-back that `them` needs is computed per LINE by `_decline_targets`, so it cannot
    reach a ref on the line above — closing it means teaching the run-back to walk a wrapped list,
    which is a change to the target RESOLUTION rather than to a separator, and it lands in the
    class-A direction if it walks one line too far. The named form works and is pinned above
    (`deliberately not closing #100 and #200`), so the honest author has a spelling that declines;
    the pronoun across a wrap is the residual. Pinned so the state is a decision, not a silence.
    """
    body = "Part of #10085,\n#10063 — deliberately not closing them.\n"
    assert L.claims(body, 1)["declined"] == [10063]
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[10085]]
    # …and the named spelling of the SAME sentence does decline both, on either layout.
    assert L.claims("Part of #10085,\n#10063 — deliberately not closing #10085 and #10063.\n",
                    1)["declined"] == [10063, 10085]


@pytest.mark.parametrize("tail", [",", ";", ".", "*", "_", "`", " ", "!", "?", "\u2026", '"', ">", "'", "%", "|", "@", "~"])
def test_one_character_after_the_auxiliary_does_not_turn_a_subject_into_a_gloss(tail):
    """The aux-tail guard tested `[ \\t]*` after the auxiliary, so `is` was refused and `is,`,
    `*is*`, `` `is` ``, `is;` and `is.` — the same clause subject with ONE character of emphasis or
    punctuation after it — were admitted, and the reference was CLEANED (measured at `fd5dfe39f`,
    review P3). One character is not a discriminator, and an ENUMERATED set cannot say that: the
    first fix listed a fixed set of characters and twenty outside it still bypassed. The tail is now
    INVERTED — ``[\\W_]``, every non-word character plus `_` — which only widens REFUSALS and so cannot
    cost a claim.

    The honest battery is asserted alongside, so the widening cannot be made green by refusing
    glosses generally.
    """
    body = f"Refs #1234 (the outage is{tail}) — not closed.\n"
    assert L.claims(body, 1)["declined"] == [], tail
    assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[1234]], tail

    for honest in (
        "Refs #100 (a) — not closing it.\n",
        "Refs #100 (the parent) — deliberately not closing it.\n",
        "Refs #10892 (blind spot), #10602 (latency) — deliberately not closing them.\n",
        "Refs #100 (P3) — not closing it.\n",
        "Refs #100 (2026-11-05) — not closing it.\n",
    ):
        assert L.classify_pr(honest, 1) == [], honest
def test_a_task_list_SOMEWHERE_is_not_a_satisfies_checklist():
    """Measured at `05585de08` (review round 25, P1) — the FALSE PASS direction, arm 2.

    `has_satisfies_checklist` was `any(headings) and any(boxes)` across the WHOLE population, so a
    task list anywhere — most often a `## Verification` list in the issue's own BODY — satisfied the
    checklist half for a `## Satisfies` comment with no criteria at all:

        [body with `- [ ]`s, IN FLIGHT, "## Satisfies\\n\\nTo be derived."]  -> NOTHING FLAGGED, rc 0
        [body WITHOUT them,   IN FLIGHT, same comment]                       -> wip-no-satisfies

    The only difference is a list written BEFORE scoping happened, so the arm cleared exactly the
    defect it exists to report ("work started before the completion criteria existed"). Measured live
    at the head: 8 of the 40 newest open issue bodies carry a line-start `- [ ]`.
    """
    inv = "IN FLIGHT: 3.2c — branch feat/census — box cell-4 — since 2026-11-05T09:00Z"
    body_list = "## Verification\n\n- [ ] the guard holds\n- [ ] the census writes nothing\n"
    numbered = "## Satisfies\n\n1. `3.2a` — tools/surface-target-check.py prints 76\n"
    empty = "## Satisfies\n\nTo be derived.\n"
    for satisfies in (empty, numbered):
        assert [f.arm for f in L.classify_issue([body_list, inv, satisfies], 1)[0]] == \
            [L.ARM_ISSUE], satisfies
        # …and the same comment WITHOUT the unrelated body list behaves identically, which is what
        # makes this a false pass rather than a second rule.
        assert [f.arm for f in L.classify_issue([inv, satisfies], 1)[0]] == [L.ARM_ISSUE], satisfies
    assert L.has_satisfies_checklist([body_list, empty]) is False


def test_the_checklist_must_belong_to_the_heading_and_a_LATER_tick_does_not_count():
    """The two sides the fix must keep apart: a box in the HEADING's own comment clears the arm, and
    an UNticked list in a later comment does not.

    ⛔ THE NAME AND THE THIRD ASSERTION ARE NOW THE WHOLE STORY (review round 26, P2): the "a TICK in
    a follow-up comment still does" half was removed because it was a false PASS — a finished list
    anywhere after the heading cleared a `## Satisfies` with no criteria. This test's second
    assertion still holds, but it holds on the heading's own `- [ ]` boxes, not on the later tick;
    the ticked/unrelated pair is pinned in
    `test_a_ticked_list_after_the_heading_is_not_this_headings_criteria`.
    """
    inv = "IN FLIGHT: 3.2c — branch feat/census"
    assert L.classify_issue([inv, "## Satisfies\n\n- [ ] a\n- [ ] b\n"], 1)[0] == []
    assert L.classify_issue([inv, "## Satisfies\n\n- [ ] a\n- [ ] b\n", "- [x] a\n"], 1)[0] == []
    # An UNticked list in a later comment is some other checklist, not this one's criteria.
    assert [f.arm for f in L.classify_issue([inv, "## Satisfies\n\nTo be derived.\n",
                                            "- [ ] unrelated\n"], 1)[0]] == [L.ARM_ISSUE]


# --- review round 26: four findings, each pinned from BOTH directions ----------------------
# The fresh-context review at `afd6de51dba1` returned 2 × P2 and 2 × P3. Every case below was RED
# against that head's own tool before the fix, and each rule gets a pair — the shape that must be
# FLAGGED and the honest sentence that must stay CLEAN — because this series' measured failure mode
# is a widening that trades one direction of error for the other. The four are independent: two are
# false PASSes (the #10534 direction, which this module ranks worst) and two are false positives on
# exactly the honesty the process block asks authors to write.

def test_a_closing_keyword_across_a_blank_line_does_not_resolve_a_claim():
    """F1 (P2), the #10534 false PASS — `closes_landing`'s OWN doctrine, applied to the PASS direction.

    `tools/closes_landing.py` ships TWO separators and says which question each backs (`:212-222`):
    LENIENT (`\\s*`) backs the FINDING arms, STRICT (`[ \\t]*:?[ \\t]*\\n?[ \\t]*`) backs **every
    PASS**, because `\\s` matches a BLANK LINE — so `"Closes\\n\\n#100"` is a claim to report but
    cannot buy a pass. This lint's CLEAN verdict is a PASS ("nothing to say"), and it spelled the
    resolving keyword's separator with the LENIENT form, so it credited a close GitHub will not
    perform:

        `Refs #100\\n\\nCloses\\n\\n#100 is the umbrella, not this PR.`  -> NOTHING FLAGGED, rc 0
        while `parse_closing_refs(body, strict=True) == []`                (nothing closes #100)

    Both sides are pinned: `#100` is claimed by `Refs`, so the lenient reading resolves it and the
    strict reading does not.
    """
    for closing in ("Closes", "Fixes", "Resolves"):
        body = f"Refs #100\n\n{closing}\n\n#100 is the umbrella, not this PR.\n"
        assert L.claims(body)["closing"] == [], (closing, L.claims(body))
        assert [f.arm for f in L.classify_pr(body, 999)] == [L.ARM_PR], closing
    # The strict oracle, so this pin tracks the sibling grammar rather than a second opinion.
    from tools.closes_landing import parse_closing_refs
    assert parse_closing_refs("Refs #100\n\nCloses\n\n#100\n", None, strict=True) == []
    # CONTROLS — the shapes that must KEEP resolving, so the narrowing is not a blanket ban:
    for ok in ("Closes #100\n",              # one space
               "Closes: #100\n",             # GitHub's colon form
               "Closes\n#100\n",             # ONE line break is not a paragraph break
               "Closes\t#100\n"):            # a tab is whitespace, not a blank line
        assert L.claims(ok)["closing"] == [100], ok
        assert L.classify_pr("Refs #100\n\n" + ok, 999) == [], ok
    # …and `Satisfies` is the SAME separator, so it narrows with it.
    assert L.claims("Refs #100\n\nSatisfies\n\n#100\n")["satisfies"] == []
    assert L.claims("Satisfies #100: 2\n")["satisfies"] == [100]


def test_the_checklist_belongs_to_the_satisfies_SECTION_not_the_whole_comment():
    """F2 (P2), arm 2's FALSE PASS — the round-25 fix closed the CROSS-comment case only.

    `classify_issue` passes the issue's own body as ONE element, and round 25's predicate asks
    "is there a box in the HEADING's own comment" — which is vacuous at body granularity, so an
    unrelated task list in the SAME body still satisfied it. That is the exact population the
    round-25 note cites ("most often a `## Verification` check-list in the issue's own BODY, which
    8 of the 40 newest open issue bodies carry"):

        one comment: `## Verification` + `- [ ]`s + `## Satisfies` + "To be derived."
        -> has_satisfies_checklist TRUE, classify_issue [] , rc 0

    The predicate is now the box in the `## Satisfies` SECTION (heading to the next ATX heading).
    """
    inv = "IN FLIGHT: 3.2c — branch feat/census — box cell-4 — since 2026-11-05T09:00Z"
    same_comment = ("## Verification\n\n- [ ] the guard holds\n- [ ] the census writes nothing\n"
                    "\n## Satisfies\n\nTo be derived.\n")
    assert L.has_satisfies_checklist([same_comment]) is False
    assert [f.arm for f in L.classify_issue([same_comment, inv], 1)[0]] == [L.ARM_ISSUE]
    # A box AFTER the heading is still some later section's list, not this one's criteria.
    later_section = "## Satisfies\n\nTo be derived.\n\n## Verification\n\n- [ ] the guard holds\n"
    assert L.has_satisfies_checklist([later_section]) is False
    assert [f.arm for f in L.classify_issue([later_section, inv], 1)[0]] == [L.ARM_ISSUE]
    # CONTROLS — a real checklist under the heading, in the same comment, still clears the arm.
    for real in ("## Satisfies\n\n- [ ] a\n- [ ] b\n",
                 "## Satisfies #11077: 2\n\n- [ ] a\n",
                 "## Satisfies\n\n* [ ] a\n\n### a sub-heading inside the section\n\n+ [ ] b\n"):
        assert L.has_satisfies_checklist([real]) is True, real
        assert L.classify_issue([real, inv], 1)[0] == [], real
    # ⛔ THE CROSS-COMMENT TICK DOES **NOT** CLEAR IT (the parallel lane's round-26 finding, kept).
    # The arm round 25 tolerated was itself a false PASS — a finished `## Verification` list in ANY
    # later comment cleared a criteria-less heading. The two narrowings compose: a box must be in the
    # heading's own SECTION, and a tick in some later comment is not this heading's criteria.
    assert L.has_satisfies_checklist(["## Satisfies\n\nTo be derived.\n", "- [x] a\n"]) is False
    assert [f.arm for f in L.classify_issue(
        [inv, "## Satisfies\n\nTo be derived.\n", "## Verification\n\n- [x] x\n"], 1)[0]] \
        == [L.ARM_ISSUE]


def test_an_honest_decline_may_carry_its_reason_as_an_APPOSITIVE():
    """F3 (P3), a false positive on the sentence the block asks for.

    `_REASON_TAIL` admitted an introduced reason (`because`/`since`/`as`/`intended`), a
    parenthetical, `;` and `.` — but not the appositive, which is how the same reason is most
    naturally written. The author answers the question the block asks and is pointed at
    bureaucracy for it (the direction the module's docstring ranks worse than the defect):

        `Refs #100 — not closing it — measurement only.`   -> FLAGGED
        `Refs #100 — not closing it; measurement only.`    -> CLEAN   (already admitted)

    The structural rule is unchanged and is what keeps this a ONE-SHAPE widening: the tail may
    contain NO `#`, so a reason can never sweep a live claim into the decline.

    ⚠️ THE COMMA FORM IS A DECLARED RESIDUAL, NOT A FIX, and this test records WHY. Admitting a
    bare `,` in `_REASON_TAIL` was measured, not assumed: it also matched round 12's PINNED
    NEGATIVE, whose comma opens a new CLAUSE rather than an appositive —
        `Refs #1234 — non-closing reference, see the review.`  -> declined, rc 0  (the false PASS
                                                                  round 12 exists to prevent)
    `non-closing reference` and `not closing it` are the SAME object shape (`_REF_LIST` admits both
    `reference` and `it`) and `measurement only.` is a noun phrase while `see the review.` is a
    clause, so telling them apart needs a clause-vs-NP grammar, not a punctuation class. A P3 false
    positive does not buy a category-A false PASS, so the dash form is admitted and the comma form
    is left flagged and declared. The convention's own comma form (`deliberately not closing it,
    measurement only.`) is already clean through alternative (a)'s comma clause-end.
    """
    for honest in ("Refs #100 — not closing it — measurement only.\n",
                   "Refs #100 — deliberately not closing it — measurement only.\n",
                   "Refs #100 — deliberately not closing it, measurement only.\n",
                   "Refs #100, #200 — deliberately not closing them — measurement only.\n"):
        assert L.classify_pr(honest, 1) == [], honest
    # The DECLARED residual, pinned so it reads as a decision rather than an oversight.
    assert [f.arm for f in L.classify_pr("Refs #100 — not closing it, measurement only.\n", 1)] \
        == [L.ARM_PR], "the comma appositive is a declared residual (see the docstring)"
    # CONTROLS — the `[^#\\n]*$` guard still refuses a tail that names a live claim, and the
    # round-12 negatives (prose about a DIFFERENT subject) are still flagged.
    for flagged in ("Refs #100 — not closing it — #300 must land first.\n",
                    "Refs #100 — not closing it, and #300 is separate.\n",
                    "Refs #1234 — the sprint is not closed.\n",
                    "Refs #1234 — the estimate does not close the delta.\n",
                    "Refs #1234 — non-closing reference, see the review.\n"):
        assert [f.arm for f in L.classify_pr(flagged, 1)] == [L.ARM_PR], flagged


def test_the_marker_spelling_the_block_ships_is_the_one_the_lint_reads():
    """F4 (P3), a silent miss caused by this PR's OWN shipped block.

    The block pre-fills `### Claim: `IN FLIGHT: <ids> — branch <b> — box <c> — since <UTC>` …`
    (`agent_finding.yml` / the doc's byte-equal copy). It is unreadable TWICE over: the marker is
    inside an inline CODE SPAN (`strip_code` blanks it — the quote-is-not-a-claim doctrine), and it
    sits behind a `### Claim: ` prefix, which `IN_FLIGHT_RE` does not anchor through. So a lane
    that copies the block and substitutes the placeholders produces NO marker, and arm 2's whole
    population is skipped without a word.

    The fix is at the SOURCE, not in the doctrine: the block now puts the marker on its OWN line in
    PLAIN TEXT, so the spelling it teaches is the spelling the lint reads. The code-span rule is
    untouched — a marker a comment QUOTES inside backticks or a fence is still not evidence.
    """
    # The block's own marker line, with placeholders substituted the way a lane would.
    live = "IN FLIGHT: 4282 — branch fix/x — box cell-3 — since 2026-11-05T09:00Z"
    assert L.in_flight_markers([live]) == ["4282 — branch fix/x — box cell-3 — since 2026-11-05T09:00Z"]
    # The parsed template line, read out of the template itself, produces a marker once the
    # placeholders are substituted — i.e. the block does not teach an unreadable spelling.
    template = yaml.safe_load(
        (ROOT / ".github" / "ISSUE_TEMPLATE" / "agent_finding.yml").read_text(encoding="utf-8"))
    value = next(b for b in template["body"] if b.get("id") == "working_process")["attributes"]["value"]
    marker_lines = [ln for ln in value.splitlines() if "IN FLIGHT:" in ln]
    assert marker_lines, "the block no longer mentions the marker at all"
    for line in marker_lines:
        assert "`" not in line, (
            f"the block spells the marker inside an inline code span, which strip_code blanks: "
            f"{line!r}")
        assert line.startswith("IN FLIGHT:"), (
            f"the block puts prose before the marker, which IN_FLIGHT_RE does not anchor through: "
            f"{line!r}")
    substituted = "\n".join(
        ln.replace("<ids>", "4282").replace("<b>", "fix/x").replace("<c>", "cell-3")
          .replace("<UTC>", "2026-11-05T09:00Z") for ln in marker_lines)
    assert L.in_flight_markers([substituted]), (
        f"the block's own marker line yields no marker after substitution: {substituted!r}")
    # CONTROLS — the quote-is-not-a-claim doctrine still holds, both ways it was already pinned.
    assert L.in_flight_markers(["`IN FLIGHT: 4282 — branch fix/x`"]) == []
    assert L.in_flight_markers(["```\nIN FLIGHT: 4282 — branch fix/x\n```"]) == []
    # …and the unsubstituted template is still not a claim.
    assert L.in_flight_markers(["IN FLIGHT: <ids> — branch <b> — box <c> — since <UTC>"]) == []

# --- review round 25: `commit` is not the reference, and two flags that could not be honoured -----


@pytest.mark.parametrize("body,why", [
    ("Refs #100 — does not close the commit.", "a COMMIT is the closing verb's object"),
    ("Refs #100 — this PR does not close the commit.", "…same, behind a two-word subject"),
    ("Refs #100 — no closing commit.", "`no closing commit` names a commit, not `#100`"),
    ("Refs #100 — does not close commits.", "the plural spelling, same object"),
    ("Refs #100 — deliberately not closing the commit.", "the convention word does not change the object"),
])
def test_the_commit_is_not_the_object_of_a_decline(body, why):
    """Measured at `afd6de51d` (review round 25, P2) — the FALSE PASS direction, class A.

    `commit|commits` sat in `_REF_NOUN`, the object list a decline may name, so a sentence about a
    COMMIT cleared the reference on the line while saying nothing about it (declined=[100], rc 0):

        Refs #100 — does not close the commit.    -> CLEAN, declined=[100]   (#100 dropped)

    A closing keyword closes an ISSUE (GitHub's own "Linking a pull request to an issue"), so a
    commit is not the reference — the same ruling round 22 made for `pr`/`prs`, one noun over. The
    constant's own comment already said the list admits "the reference, and nothing else".
    """
    assert L.claims(body + "\n", 1)["declined"] == [], why
    assert [f.evidence["unresolved"] for f in L.classify_pr(body + "\n", 1)] == [[100]], why


@pytest.mark.parametrize("body,why", [
    ("Refs #100 — this commit does not close it.", "the commit as SUBJECT, the reference as object"),
    ("Refs #100 — these commits do not close it.", "the plural demonstrative form"),
])
def test_the_commit_that_does_not_close_it_is_still_an_honest_decline(body, why):
    """The mirror half of the removal above, pinned so the fix cannot be made green by deleting the
    noun outright: deleting `commit` from the OBJECT list alone left the honest sentence FLAGGED
    (`declined=[]` at `afd6de51d`), a class-B false positive on exactly the honesty the block asks
    for. `commit` is now admitted where `pr` is — as the two-word subject whose demonstrative makes
    it the reference — so both spellings of the same sentence agree."""
    assert L.claims(body + "\n", 1)["declined"] == [100], why
    assert L.classify_pr(body + "\n", 1) == [], why


def test_the_commit_subject_narrowing_keeps_the_bare_and_the_forms_out():
    """`commit` is admitted ONLY behind `this`/`these`, exactly as `pr` is (round 20's narrowing) —
    so the forms that say nothing about the reference still take the object route and still flag."""
    for body in ("Refs #100 — the commit is not closed.\n",
                 "Refs #100 — commit is not closed.\n"):
        assert L.claims(body, 1)["declined"] == [], body
        assert [f.evidence["unresolved"] for f in L.classify_pr(body, 1)] == [[100]], body
    # …and the objects that genuinely ARE the reference keep declining, so the grammar is not
    # narrowed shut.
    for body in ("Refs #100 — not closing this issue.\n",
                 "Refs #100 — does not close #100.\n",
                 "Refs #100 — this PR does not close the issue.\n"):
        assert L.claims(body, 1)["declined"] == [100], body
        assert L.classify_pr(body, 1) == [], body


def test_json_and_annotate_are_refused_together(tmp_path):
    """Measured at `afd6de51d` (review round 25, P4): `main()` dispatches `if args.json: … elif
    args.annotate: …`, so both flags together ran the JSON mode and emitted NO `::warning`/`::notice`
    line at all — a workflow step that asked for annotations silently produced none. There is no
    union of one JSON document and a stream of annotations, so the pair is refused rather than
    resolved in silence, the same doctrine as `--label` / `--comments-file` above it."""
    body = tmp_path / "body.md"
    body.write_text("Refs #100\n", encoding="utf-8")
    r = _run("--body-file", str(body), "--json", "--annotate")
    assert r.returncode == 2, (r.returncode, r.stderr)
    assert "--json and --annotate are mutually exclusive" in r.stderr
    # the controls: each flag alone still runs its own surface
    assert _run("--body-file", str(body), "--json").returncode == L.RC_FINDINGS
    assert "::warning title=issue-process-lint" in _run("--body-file", str(body), "--annotate").stdout


@pytest.mark.parametrize("argv", [
    ("--pr", "1"),
    ("--issue", "1"),
    ("--body-file", "{body}"),
    ("--issue-body-file", "{body}"),
])
def test_limit_is_refused_on_every_mode_that_reads_a_fixed_subject(argv, tmp_path):
    """Measured at `afd6de51d` (review round 25, P4): `--limit` is read by the two sweeps and by
    nothing else, so on every other mode it was accepted and silently ignored while the caller
    believed the run had been narrowed. Refused here for the same reason `--label` is — and the
    sweeps stay the control, so the refusal cannot be made green by refusing the flag everywhere."""
    body = tmp_path / "body.md"
    body.write_text("Refs #100\n", encoding="utf-8")
    r = _run(*(a.format(body=str(body)) for a in argv), "--limit", "3")
    assert r.returncode == 2, (argv, r.returncode, r.stderr)
    assert "--limit applies to --sweep/--sweep-issues only" in r.stderr


def test_the_default_limit_is_not_refused_offline(tmp_path):
    """The control for the refusal above: `--limit`'s default must not read as an explicit flag, or
    every offline run would be refused for a cap it never asked for. Only the OFFLINE modes are run
    here — the live ones would reach `gh`, and this file is on the required `core` surface, which is
    hermetic (no network, modelled by the `fetch_*`/`sweep_*` monkeypatch seam elsewhere)."""
    body = tmp_path / "body.md"
    body.write_text("Refs #100\n", encoding="utf-8")
    for argv in (("--body-file", str(body)), ("--issue-body-file", str(body))):
        assert _run(*argv).returncode in (L.RC_CLEAN, L.RC_FINDINGS), argv

# --- review round 26: two false PASSes, in the two arms this file's pairs exist to pin ------------


def test_an_emphasised_continuation_is_the_same_list():
    """Measured at `afd6de51` (review round 26, P2) — the FALSE PASS direction, arm 1.

    `CLAIM_RE`'s continuation separator was `_SOFT_GAP` — whitespace only — while the DECLINE list
    (`_REF_LIST`), the plural run (`_REF_RUN_GAP_RE`) and this module's own comment all admitted
    Markdown emphasis (`_LIST_SEP`'s `*`, `_`, `` ` ``). So the emphasis a decline list ACCEPTS made
    the claim list lose the reference, and `#200` was never even evaluated:

        `Closes #100, #200`            -> closing=[100], weak=[200]                     (correct)
        `Closes #100, **#200**`        -> `#200` absent, rc 0                           (false PASS)
        `Part of **#100**, #200`       -> weak=[100], `#200` absent, rc 0               (false PASS)
        `Part of #100 and **#200**`    -> weak=[100], `#200` absent, rc 0               (false PASS)

    Emphasised references are prose this repo writes (`#11116`: `Also closes the gap tracked by
    **#5114**`), so this is not a theoretical spelling. Each shape is pinned as a PAIR — the
    emphasised body must read exactly like its unemphasised twin — and the round-22 blank-line rule
    and the conjunction prose control are re-run here so the widening cannot buy its green by
    re-opening either.
    """
    for plain, emph in (("Closes #100, #200\n", "Closes #100, **#200**\n"),
                        ("Refs #100, #200\n", "Refs #100, *#200*\n"),
                        ("Part of #100 and #200\n", "Part of #100 and **#200**\n"),
                        ("Part of #10085,\n#10063\n", "Part of #10085,\n**#10063**\n")):
        assert L.claims(emph, 1) == L.claims(plain, 1), emph
        assert [f.evidence["unresolved"] for f in L.classify_pr(emph, 1)] == \
            [f.evidence["unresolved"] for f in L.classify_pr(plain, 1)], emph
    # The emphasis may also close the ref the list starts FROM (`**#100**`), not just the continuation.
    assert L.claims("Part of **#100**, #200\n", 1) == L.claims("Part of #100, #200\n", 1)
    # ⛔ THE WIDENING MUST NOT RE-OPEN EITHER NARROWING. A blank line still ends the list, so the
    # `#N` after it is a MENTION and is never claimed (`Closes` claims #100 alone → clean;
    # `Part of` claims #100 alone → #100 flagged, #200 not).
    assert L.claims("Closes #100\n\nand **#200** is tracked separately.\n", 1)["weak"] == []
    assert L.claims("Closes #100\n\nand **#200** is tracked separately.\n", 1)["closing"] == [100]
    assert L.claims("Part of #100,\n\n**#200**\n", 1)["weak"] == [100]
    # …and a `#N` still has to follow the separator, so prose is not a list (`#200` in inline code,
    # and the no-separator spelling, both claim only `#100`).
    assert L.claims("Part of #100 and the fix is `#200`.\n", 1)["weak"] == [100]
    assert L.claims("Part of #100 and#200\n", 1)["weak"] == [100]


def test_a_ticked_list_after_the_heading_is_not_this_headings_criteria():
    """Measured at `afd6de51` (review round 26, P2) — the FALSE PASS direction, arm 2.

    `has_satisfies_checklist`'s second arm returned True on a TICKED box in ANY later comment, so a
    finished list cleared a `## Satisfies` that has no criteria at all:

        [INV, "## Satisfies\\n\\nTo be derived.", "## Verification\\n\\n- [x] x"]  -> rc 0   (false PASS)
        [INV, "## Satisfies\\n\\nTo be derived.", "- [ ] unrelated"]             -> wip-no-satisfies

    That is the same shape round 25 closed one layer up (a task list SOMEWHERE is not this heading's
    checklist) — the only difference being whether the unrelated list happens to be finished. The
    arm's stated justification ("criteria are ticked in a follow-up comment once proved, and that
    must not re-flag a finished issue") is measurably false: a finished issue's criteria are `[x]`
    boxes in the HEADING's own comment, which the first arm already accepts.

    The pairs: a TICKED and an UNticked unrelated list must get the SAME verdict (red-first — the
    ticked one read rc 0 before this fix), and every shape that legitimately clears still clears.
    """
    inv = "IN FLIGHT: 3.2c — branch feat/census"
    unrelated = ("- [ ] a\n",
                 "- [x] a\n",
                 "## Verification\n\n- [x] the guard holds\n",
                 "## Verification\n\n- [ ] the guard holds\n")
    for later in unrelated:
        assert [f.arm for f in L.classify_issue(
            [inv, "## Satisfies\n\nTo be derived.\n", later], 1)[0]] == [L.ARM_ISSUE], later
        assert L.has_satisfies_checklist(
            ["## Satisfies\n\nTo be derived.\n", later]) is False, later
    # ⛔ THE AFFORDANCE IS NOT LOST. A real checklist clears whether its boxes are unticked (scoping
    # happened) or ticked (the criteria were proved), with or without a follow-up comment.
    for satisfies in ("## Satisfies\n\n- [ ] a\n- [ ] b\n",
                      "## Satisfies\n\n- [x] a\n- [x] b\n"):
        assert L.classify_issue([inv, satisfies], 1)[0] == [], satisfies
        assert L.classify_issue([inv, satisfies, "- [x] a\n"], 1)[0] == [], satisfies
    assert L.has_satisfies_checklist(["## Satisfies\n\n- [x] a\n"]) is True


def test_a_code_span_that_wraps_a_line_break_is_still_code():
    """Measured at `e7061e8cd` (review round 28, P2) — the FALSE-POSITIVE direction, arm 1.

    The inline-code strip was the LENIENT arm of `closes_landing._strip_inline_code`, which searches
    only to the END OF THE LINE. CommonMark lets a code span cross one SOFT line break (one
    paragraph, §6.1), so a span that wrapped was never blanked and the reference it QUOTED was read
    as a claim:

        Example: `Part of #10085,\\ndeliberately not closing them.` declines #10063.
          -> renderer: <code>…</code>;   lenient scan: weak=[10085]   -> arm 1 FLAGS

    The measured population is this PR's own body: `--pr 11078` flagged it, and both hits were its
    two quoted examples. A warn-only tool that cries on the PR that ships it is exactly the failure
    the module's header ranks second-worst, and the renderer is the oracle (as in
    `test_a_multi_backtick_code_span_is_not_a_claim`), not a second copy of the pattern.

    The pairs: the SAME sentence as prose must still be flagged (the strip cannot simply be widened
    to swallow everything), and the hazard that keeps `closes_landing`'s own arms lenient — a stray
    opener whose real claim sits between it and the closer — is pinned as still-claimed, because
    strict blanks no further than the renderer's span.
    """
    quoted = "Example: `Part of #10085,\ndeliberately not closing them.` declines #10063.\n"
    assert "<code>" in MarkdownIt("commonmark").render(quoted)
    assert L.classify_pr(quoted, 9) == []
    # …while the same words OUTSIDE the span are a claim, so the fix is the span and not a blanket.
    prose = "Example: Part of #10085,\ndeliberately not closing them. declines #10063.\n"
    assert [f.evidence["unresolved"] for f in L.classify_pr(prose, 9)] == [[10085]]
    # ⛔ THE CLAIM-LOSING HAZARD STAYS CLOSED: an unmatched opener in one line must not blank a real
    # claim that follows it (§6.1's span ends at the paragraph, and the renderer's does too).
    assert L.claims("a` then the fix\n`Closes #100`\n", 9)["closing"] == [100]
    assert L.claims("Closes #100, and a ` tick later\n", 9)["closing"] == [100]


def test_a_pr_side_json_run_emits_no_wip_read(tmp_path):
    """Measured at `e7061e8cd` (review round 28, P3) — an arm reported as measured when it never ran.

    The module states that a PR-side run (`--pr`, `--sweep`, `--body-file`) never evaluates the WIP
    arm "so it prints NO WIP line at all", and the human and `--annotate` modes keep that promise via
    `if measured_rows:`. `--json` did not, so a machine consumer read

        "wip_read": {"subjects": 0, "with_marker": 0, "markerless": 0,
                     "populations_not_consulted": ["branches", "worktrees", "ref-claims"]}

    — indistinguishable from an issue-mode run over a population that carried no marker, plus a
    sentence asserting three populations were "not consulted" for an arm that never ran. `None` is
    the honest value: no measurement, nothing to misread.
    """
    body = tmp_path / "b.md"
    body.write_text("Refs #100\n", encoding="utf-8")
    r = _run("--body-file", str(body), "--json")
    assert r.returncode in (L.RC_CLEAN, L.RC_FINDINGS), r.stderr
    doc = json.loads(r.stdout)
    assert doc["wip_read"] is None, doc
    assert doc["findings"], "the flagged body must still report its finding in the same document"
    # The human mode keeps its own promise (the control: the guard is per-mode, and `--json`'s
    # absence must not be achieved by dropping the section from the modes that DO measure).
    assert "WIP arm:" not in _run("--body-file", str(body)).stdout


def test_an_injected_wip_verdict_prints_no_marker_count(tmp_path):
    """Measured at `2bc77dcc6` (review round 29, P3) — a population that does not add up.

    `markerless` counts only rows read from a `<!-- p01-status -->` COMMENT, so under an injected
    `--wip`/`--no-wip` verdict on a markerless issue the summary read

        1 subject(s) read — 0 carried an `IN FLIGHT:` marker, 0 carried none

    — one subject, and neither bucket. That sentence exists precisely so "0 markers read" and "0 with
    WIP" cannot be laundered into each other, so it may not print numbers describing an arm the caller
    did not run; the injection notice says where the verdict came from instead.

    The control: a NON-injected run keeps the count, and it must still add up.
    """
    body = tmp_path / "nomarker.md"
    body.write_text("no marker here\n", encoding="utf-8")
    for flag in ("--wip", "--no-wip"):
        out = _run("--issue-body-file", str(body), flag).stdout
        wip_line = [ln for ln in out.splitlines() if "WIP arm:" in ln]
        assert wip_line, out
        assert "INJECTED" in wip_line[0], wip_line[0]
        assert "carried" not in wip_line[0], wip_line[0]
    plain = next(ln for ln in _run("--issue-body-file", str(body)).stdout.splitlines()
                 if "WIP arm:" in ln)
    assert "1 subject(s) read" in plain and "1 carried none" in plain, plain
    assert "carried an `IN FLIGHT:` marker, 0" not in plain

# --- review round 28: the PASS arm is strict about CONTAINMENT, and only the PASS arm -----------
# Round 27 applied `strict=True` to the SHARED `strip_code`, which the arm-2 predicates also read.
# Two P2s followed, and both are pinned here from the direction each one broke: a claim credited
# through an INDENTED block (arm 1, false PASS) had to stop being credited, while a 4-space-indented
# list item — PROSE inside a list, not code — had to keep being read (arm 2, silent miss).

def test_an_indented_closing_keyword_does_not_resolve_a_claim():
    """Round 28 P2, class A — the PASS direction's CONTAINMENT, not just its separator.

    `closes_landing.parse_closing_refs(..., strict=True)` treats an INDENTED block as code; F1's fix
    narrowed only the SEPARATOR, so `Refs #100` + blank line + a 4-space-indented `Closes #100` was
    credited as a close — `NOTHING FLAGGED`, rc 0 — while the sibling grammar said nothing closes.
    F1's own invariant ("a CLEAN verdict may not rest on a shape the sibling grammar refuses to pass
    on") applied to one half of its own statement.

    ⚠️ RECORD, measured (round 33 review, P3): this pin was written against `e7061e8cda` and it PASSES
    at `ee01b70f6e`, because the arm-1 narrowing landed on THIS branch first (`5e1c557b83`, round 27).
    It came in with the cherry-pick as a REGRESSION GUARD, not as a red-first fix; nothing in it was
    fixed by the commits above it. Kept for that reason — no other case pins the arm-1 containment.
    """
    for body in ("Refs #100\n\n    Closes #100\n",
                 "Refs #100\n\n\tCloses #100\n"):
        assert L.claims(body)["closing"] == [], body
        assert [f.arm for f in L.classify_pr(body, 999)] == [L.ARM_PR], body
    from tools.closes_landing import parse_closing_refs
    assert parse_closing_refs("Refs #100\n\n    Closes #100\n", None, strict=True) == []
    # CONTROLS — prose closers (at any indent ≤ 3) still resolve, so this is not a blanket ban.
    for ok in ("Refs #100\n\nCloses #100\n", "Refs #100\n\n   Closes #100\n"):
        assert L.claims(ok)["closing"] == [100], ok
        assert L.classify_pr(ok, 999) == [], ok


def test_a_multiline_code_span_is_not_a_claim():
    """Round 28 P2 — a QUOTED multi-line example must not be read as prose claims.

    An inline code span may cross a soft line break (CommonMark), and `strict` is what makes the
    span scanner honour that boundary. Without it a quoted example's refs were read as claims —
    a false positive, live on PR #11078's own body.

    ⚠️ RECORD, measured (round 33 review, P3): the defect was live at `5e1c557b83` and fixed on this
    branch by `2bc77dcc6c`, both ANCESTORS of the commits this pin arrived with — so at
    `ee01b70f6e` it already passed. It is a regression guard, not a red-first fix of anything above it.
    """
    body = "Prose.\n\nRefs #100\n\n`Part of #10085,\n#10063` is a quoted example.\n"
    assert L.claims(body)["weak"] == [100], L.claims(body)
    # `#100` IS still flagged (a `Refs` with nothing closing it — arm 1's whole point). What the fix
    # buys is that the QUOTED refs are not dragged in with it.
    findings = L.classify_pr(body, 999)
    assert [f.evidence["unresolved"] for f in findings] == [[100]], findings
    # Control: the same refs OUTSIDE a span are still claims.
    assert sorted(L.claims("Refs #100\n\nPart of #10085,\n#10063\n")["weak"]) == [100, 10063, 10085]


def test_strict_containment_does_not_reach_the_WIP_arms():
    """Round 28 P2 — the regression the first attempt at the fix above introduced, pinned.

    `strict` was applied to the SHARED `strip_code`, which `has_satisfies_checklist` and
    `in_flight_markers` also read. Both predicates deliberately admit leading whitespace, and a line
    indented 4 spaces INSIDE A LIST ITEM is PROSE, not code (CommonMark measures the content column
    from the list marker), so:

        `## Satisfies` + `1. Criteria` + `    - [ ] a`   -> arm 2 said "no criteria" (false positive)
        `- note` + `    IN FLIGHT: 3.2c`                -> the marker vanished (silent miss)

    Only `claims()` (arm 1) passes `strict=True`; the arm-2 predicates ask for `indented_code`
    instead. (`classify_pr` — the other arm-1 reader — passes it too; this docstring said
    "only" until a review pointed at the call site.) The difference matters, and it is pinned in
    the next case: `strict` blanks on the WIDTH alone, which is wrong for both arms' purposes in
    the opposite direction.
    """
    assert L.has_satisfies_checklist(["## Satisfies\n\n1. Criteria\n    - [ ] a\n"]) is True
    assert L.in_flight_markers(["- note\n    IN FLIGHT: 3.2c\n"]) == ["3.2c"]
    # …and the indented-block narrowing still holds where it belongs (arm 1).
    assert L.claims("Refs #100\n\n    Closes #100\n")["closing"] == []


def test_arm_2_hides_a_QUOTED_example_but_keeps_a_list_items_continuation():
    """Round 29 P2 (the review at `d70bc57891`) — `strict` OFF is not the answer either.

    Turning `strict` off for arm 2 fixed list-item continuations and simultaneously un-blanked the
    OTHER CommonMark way to show an example, a top-level INDENTED CODE BLOCK — a class-A false PASS
    and a regression against the parent head:

        `## Satisfies` + `To be derived.` + `    - [ ] an example`   parent FALSE -> this TRUE
        `    IN FLIGHT: quoted example - branch b`                   parent []    -> this ["quoted…"]

    Arm 2 asks for `indented_code` now: blank a line only where the BLOCK grammar (§5.2) says it is
    code — past 3 columns AND past the enclosing item's content column + 4. The renderer is the
    oracle: both quoted shapes render as `<pre><code>`, while `1. Criteria` + `    - [ ] a` renders
    as a nested list. Each half is a pair, so neither direction can be satisfied by a blanket.
    """
    quoted_checklist = "## Satisfies\n\nTo be derived.\n\n    - [ ] an example from the template\n"
    assert "<pre><code>" in MarkdownIt("commonmark").render(quoted_checklist)
    assert L.has_satisfies_checklist([quoted_checklist]) is False
    assert [f.arm for f in L.classify_issue(
        [quoted_checklist, "IN FLIGHT: 3.2c - branch x"], 1)[0]] == [L.ARM_ISSUE]
    assert L.in_flight_markers(["    IN FLIGHT: quoted example - branch b"]) == []
    # CONTROLS — a list item's continuation is prose at ANY indent past the marker, and a genuine
    # code block INSIDE an item (content column + 4) is still code.
    assert L.has_satisfies_checklist(["## Satisfies\n\n1. Criteria\n    - [ ] a\n"]) is True
    assert L.in_flight_markers(["- note\n    IN FLIGHT: 3.2c\n"]) == ["3.2c"]
    assert L.has_satisfies_checklist(["## Satisfies\n\n- item\n\n      - [ ] x\n"]) is False
    # A fenced example keeps working (the always-on half), so this is not the only hiding rule.
    assert L.has_satisfies_checklist(["## Satisfies\n\n```\n- [ ] x\n```\n"]) is False
    # ⛔ THE CONTENT COLUMN IS COMMONMARK'S, NOT THE MARKER'S RAW LENGTH (round 30 review, P2).
    # A 5+-space gap collapses to ONE column (§5.2), so `-     item` has content column 2 — and a
    # checklist at 6+ columns is then an indented code block. While the item push and the fence
    # path computed that column DIFFERENTLY (raw marker length vs the collapsed one), the blanking
    # threshold sat four columns too far out and the quoted box cleared the heading:
    #     `-     item` + `        - [ ] x`   renderer `<pre><code>`  ->  was CLEARED, must be hidden
    # The expected values below come from the renderer: at 5 columns the checklist renders as a
    # nested list (`<li>[ ] x</li>`), at 6 and 8 the `- [ ] x` survives INSIDE `<pre><code>`.
    for spaces, quoted in ((5, False), (6, True), (8, True)):
        body = "## Satisfies\n\n-     item\n\n" + " " * spaces + "- [ ] x\n"
        assert ("- [ ] x" in MarkdownIt("commonmark").render(body)) is quoted, (spaces, body)
        assert L.has_satisfies_checklist([body]) is not quoted, (spaces, body)


def test_a_two_line_indented_example_is_hidden_by_BOTH_of_its_lines():
    """Round 31 review, P2 — a blanked line opened a PHANTOM list item for the next one.

    The capture-before-push fix kept a code line from being measured against the item it opens
    itself, but the item it pushed stayed on the stack — so the SECOND line of the same block was
    measured against a content column two to four columns too deep, came out as prose, and was
    counted:

        `## Satisfies` + `To be derived.` + `- item` + BLANK + `      - [ ] example` + `        - [ ] nested example`
        renderer: ONE `<pre><code>` holding BOTH boxes (the blank line is load-bearing — without it
                  the two boxes are a paragraph continuation under §4.4, not code)
        before:   arm 2 saw a satisfied heading, `classify_issue` returned [] -> rc 0   (false PASS)
        before:   `in_flight_markers` read a QUOTED marker as live                      (false positive)

    The sweep this docstring used to quote as numbers ("224 of 720", then "177 of 177") is run
    HERE instead, over the family it names — markers `-`/`*`/`1.`/`10.` x gaps 1,2,4,5,8 x first-line
    indents 4,5,6,7,8,10 x second line +2/+3/+4 — and asserts the two properties directly, because
    a prose count is what rotted twice: no body the PARSER puts in a `code_block` may be counted
    (class A), and no prose body may be over-hidden.
    The pairs below span the boundary — 6 to 10 columns, all of which the renderer treats as ONE code
    block, and a marker-led line that is PROSE because it is shallower than the code block.
    """
    md = MarkdownIt("commonmark")

    def in_code(body: str, text: str) -> bool:
        return any(t.type == "code_block" and text in t.content for t in md.parse(body))

    sweep = 0
    for marker in ("-", "*", "1.", "10."):
        for gap in (1, 2, 4, 5, 8):
            for indent in (4, 5, 6, 7, 8, 10):
                for extra in (2, 3, 4):
                    sweep += 1
                    body = ("## Satisfies\n\nTo be derived.\n\n" + marker + " " * gap + "item\n\n"
                            + " " * indent + "- [ ] example\n"
                            + " " * (indent + extra) + "- [ ] nested example\n")
                    quoted = in_code(body, "- [ ] nested example")
                    counted = L.has_satisfies_checklist([body])
                    assert not (quoted and counted), ("counts code", marker, gap, indent, extra)
                    assert not (not quoted and not counted), ("over-hides", marker, gap, indent, extra)
    assert sweep == 360, sweep
    for indent in (6, 7, 8, 9, 10):
        body = ("## Satisfies\n\nTo be derived.\n\n- item\n\n"
                + " " * indent + "- [ ] example\n"
                + " " * (indent + 2) + "- [ ] nested example\n")
        html = MarkdownIt("commonmark").render(body)
        quoted = "- [ ] nested example" in html
        assert quoted, (indent, html)
        assert L.has_satisfies_checklist([body]) is False, (indent, body)
        assert L.classify_issue([body + "\nIN FLIGHT: 3.2c - branch b\n"], 1)[0] != [], indent
    assert L.in_flight_markers(["- note\n\n      IN FLIGHT: quoted 3.2c\n"
                                 "        IN FLIGHT: quoted 3.2b\n"]) == []
    # CONTROL — a marker-led line that IS prose is still read (the rule cannot be "blank them all").
    assert L.in_flight_markers(["- note\n\n  IN FLIGHT: 3.2c - branch b\n"]) == ["3.2c - branch b"]


def test_a_wide_gap_MARKER_LINE_can_be_code_on_its_own():
    """Round 32 review, P2 — the marker line's OWN content can be an indented code block.

    `rest_indent` is the indentation BEFORE the marker, so it is 0 for `-     [ ] x` — but §5.2
    collapses that 5-space gap to ONE column, so the content sits 4 columns past the item's content
    column and §4.4 makes it code, starting on the marker line itself. The renderer is the oracle:

        `-     [ ] x`            -> <ul><li><pre><code>[ ] x</code></pre></li></ul>
        `-     IN FLIGHT: 3.2c`  -> the marker is INSIDE <pre><code>
        `-     Closes #100`      -> the keyword is INSIDE <pre><code>   (and `strict` did not save it:
                                    `parse_closing_refs(..., strict=True)` credited it too)

    All three were read as PROSE — a quoted checklist cleared a criteria-less `## Satisfies`, a quoted
    marker read as live, a quoted close was credited in BOTH arms. Parity with the parent head rather
    than a regression, and no instance in this repo's own docs, but the same family as the two
    preceding rounds: a false PASS is a false PASS.

    The sweep below is the evidence, not a quoted count (this lane has misquoted two already): it
    walks markers x gaps x indents and asserts, on every run, that no marker line the PARSER puts in
    a `code_block` is credited and that no marker line it calls prose is hidden. The `gap == 4`
    control is prose and is still read by every predicate.
    """
    md = MarkdownIt("commonmark")

    def is_code(line: str, text: str) -> bool:
        return any(t.type == "code_block" and text in t.content for t in md.parse(line + "\n"))

    sweep = 0
    for marker in ("-", "*", "1.", "10.", "+"):
        for gap in range(1, 10):
            for indent in (0, 1, 2, 3):
                sweep += 1
                line = " " * indent + marker + " " * gap + "[ ] x"
                body = "## Satisfies\n\nTo be derived.\n\n" + line + "\n"
                # The oracle is the PARSER, not a substring: from gap 6 on the code block's content
                # keeps leading spaces (` [ ] x`), so a `<pre><code>[ ] x` match would skip those.
                quoted = is_code(line, "[ ] x")
                counted = L.has_satisfies_checklist([body])
                assert not (quoted and counted), ("counts code", marker, gap, indent)
                assert not (not quoted and not counted), ("over-hides", marker, gap, indent)
    assert sweep == 180, sweep
    # The CLOSE sweeps every marker: the claim grammar has no list-marker class of its own (it reads
    # whatever survives `strip_code`), so an ordered marker is the same case as a dash.
    for marker in ("-", "*", "1.", "10.", "+"):
        for gap in range(1, 10):
            close_line = marker + " " * gap + "Closes #100"
            assert (L.claims(close_line)["closing"] == []) is is_code(close_line, "Closes"), \
                (marker, gap)
    # The `IN FLIGHT:` sweep is BULLET-ONLY, and for a different reason: `IN_FLIGHT_RE` carries its
    # own marker class (`[-*+][ \t]+`) and admits no ordered marker at all, so an ordered case would
    # be measuring that pre-existing arm limit rather than this rule.
    for marker in ("-", "*", "+"):
        for gap in range(1, 10):
            marker_line = marker + " " * gap + "IN FLIGHT: 3.2c"
            assert (L.in_flight_markers([marker_line]) == []) is is_code(marker_line, "IN FLIGHT"), \
                (marker, gap)
    # ⛔ AN INDENTED MARKER LINE OPENS NO ITEM AT ALL (round 33 review, P2 — the sub-case where the
    # gap rule and the INDENT rule fire on the SAME line). At a legal marker position the renderer
    # opens the `<li>` even when its content is code; indented past the allowance at top level the
    # line is a top-level INDENTED CODE BLOCK and no `<li>` is opened — so the push must be undone, or
    # the NEXT line of the same block is measured against a phantom column and comes out as prose:
    #     `    -     item` + `      - [ ] QZX`   -> ONE code_block holding both lines
    # (Round 32 suppressed the pop whenever `own_code`, which re-opened exactly this hole.)
    sweep = 0
    for marker in ("-", "*", "+", "1.", "10."):
        for indent in (4, 5, 6, 8):
            for gap in (5, 6, 9):
                sweep += 1
                lead = " " * indent + marker + " " * gap + "item"
                body = ("## Satisfies\n\nTo be derived.\n\n" + lead + "\n"
                        + " " * (indent + 2) + "- [ ] QZX\n")
                quoted = is_code(body, "- [ ] QZX")
                counted = L.has_satisfies_checklist([body])
                assert not (quoted and counted), ("counts code", marker, indent, gap)
                assert not (not quoted and not counted), ("over-hides", marker, indent, gap)
                if marker in ("-", "*", "+"):
                    follow = " " * (indent + 2) + "IN FLIGHT: QZX"
                    assert (L.in_flight_markers([lead + "\n" + follow]) == []) is quoted, \
                        (marker, indent, gap)
    assert sweep == 60, sweep
    # CONTROL — a 4-space gap is PROSE by §5.2, so every predicate still reads it.
    prose = "## Satisfies\n\nTo be derived.\n\n-    [ ] x\n"
    assert "<pre><code>" not in MarkdownIt("commonmark").render(prose)
    assert L.has_satisfies_checklist([prose]) is True
    assert L.in_flight_markers(["-    IN FLIGHT: 3.2c"]) == ["3.2c"]
    assert L.claims("-    Closes #100")["closing"] == [100]


def test_a_wide_gap_MARKER_LINE_after_an_OPEN_PARAGRAPH_is_PROSE():
    """Round 34 review, P2 — a REGRESSION the round-32 `own_code` rule introduced.

    `own_code` reads §5.2's **producer** rule (where a 5+-space gap puts an item's content) and
    ignored §5.2's **consumer** rule (*"a list item can interrupt a paragraph only if it is a bullet
    list item or an ordered list item that starts with 1"*). So an ordered marker ≥ 2 that merely
    CONTINUES a paragraph was blanked as if it had opened a list item:

        `Notes\n2.     Closes #100`   renderer <p>Notes\n2.     Closes #100</p>   PROSE

    `parse_closing_refs` returned `[]` — at the parent head and on `main` it returns `[100]`. That is
    the class-A direction (a delivered close nothing reports, so the stacked-PR `Closes #N` warning
    in `.github/workflows/closes-coherence.yml` cannot fire) AND a false positive in arm 1 of this
    lint (honest work flagged as claiming an issue nothing closes).

    ⛔ THE SWEEP IN THE TEST ABOVE COULD NOT SEE THIS BY CONSTRUCTION: it wraps its line in
    `"…To be derived.\n\n" + line`, i.e. after a CLOSED paragraph. The families below are all the
    containers a paragraph can still be open in — and the two that OVER-KEEP if the rule is read as
    "the previous line was prose" rather than "…in the SAME container", which is why the state this
    pins is (container, blockquote depth, content column) and not the previous line's text:

        `- Notes\n2.     Closes #100`   the ul CLOSES (col 0 is left of its content column) -> CODE
        `> Notes\n2.     Closes #100`   the blockquote closes at depth 0                      -> CODE

    Measured over 2,592 bodies (9 container families x 8 markers x gaps 1-9 x indents 0-3) with
    `MarkdownIt("commonmark")` as the oracle: the parent head and `main` credit **1,060** lines the
    renderer puts in a `code_block` and lose **0** prose lines; the round-33 head loses **380** prose
    lines and credits **0**; the tree this test ships in is **0 and 0**. The sweep is the evidence —
    the numbers are a snapshot of the heads named, not a constant.
    """
    from tools.closes_landing import parse_closing_refs
    md = MarkdownIt("commonmark")

    def in_code(body: str, needle: str) -> bool:
        return any(t.type == "code_block" and needle in t.content for t in md.parse(body))

    families = {
        "bare": lambda line: line + "\n",
        "preceded": lambda line: "Notes\n" + line + "\n",
        "blanksep": lambda line: "Notes\n\n" + line + "\n",      # para CLOSED: still code
        "preceded_item": lambda line: "- Notes\n" + line + "\n",
        "quoted_prec": lambda line: "> Notes\n" + line + "\n",
        "in_quote": lambda line: "> Notes\n> " + line + "\n",
        "in_item": lambda line: "- Notes\n  " + line + "\n",
        "deep": lambda line: "Notes\n   more text\n" + line + "\n",
        "after_ol": lambda line: "1. Notes\n" + line + "\n",
    }
    sweep = 0
    for family, wrap in families.items():
        for marker in ("-", "*", "+", "1.", "2.", "10.", "0.", "3)"):
            for gap in range(1, 10):
                for indent in (0, 1, 2, 3):
                    sweep += 1
                    line = " " * indent + marker + " " * gap + "Closes #100"
                    body = wrap(line)
                    quoted = in_code(body, "Closes #100")
                    claimed = 100 in parse_closing_refs(body)
                    assert not (quoted and claimed), ("credits code", family, marker, gap, indent)
                    assert not (not quoted and not claimed),                         ("loses prose", family, marker, gap, indent)
    assert sweep == 2592, sweep
    # The three shapes the rule names, spelled out, so a reader does not have to trust the sweep's
    # parameterisation to know what it commits to.
    assert parse_closing_refs("Notes\n2.     Closes #100\n") == [100]
    assert parse_closing_refs("Notes\n10.     Closes #100\n") == [100]
    assert parse_closing_refs("Notes\n1.     Closes #100\n") == []      # §5.2: `1.` CAN interrupt
    assert parse_closing_refs("Notes\n-     Closes #100\n") == []       # …and so can a bullet
    assert parse_closing_refs("Notes\n\n2.     Closes #100\n") == []    # para already closed

    # `strict` must not resurrect the loss: the blanking decision is above the strictness choice.
    assert parse_closing_refs("Notes\n2.     Closes #100\n", strict=True) == [100]
    # …and the INDENTED-code arm is untouched, so the residual the two rules share is unchanged.
    assert parse_closing_refs("Notes\n   more text\n    - [ ] x\n") == []


def test_a_deeper_heading_ends_the_satisfies_section():
    """Round 28 P2 — the false PASS that the "same-or-higher level" terminator introduced.

    Making a deeper heading part of the section meant every box under it was this section's
    criteria, so an unrelated list ONE LEVEL DOWN cleared a criteria-less `## Satisfies` — round
    25/26's exact population re-opened. ANY ATX heading ends the section.

    ⚠️ DECLARED trade-off, pinned so it reads as a decision: a `###`-per-criterion layout
    (`## Satisfies` + `### 1. Guard holds` + boxes) is therefore flagged. That is a false POSITIVE on
    honest scoping, and it is accepted because the alternative is a false PASS — which this module
    ranks worse. A structured checklist should put its boxes directly under `## Satisfies`.
    """
    false_pass = "## Satisfies\n\nTo be derived.\n\n### Verification\n\n- [ ] x\n"
    assert L.has_satisfies_checklist([false_pass]) is False
    assert [f.arm for f in L.classify_issue(
        [false_pass, "IN FLIGHT: 3.2c — branch x"], 1)[0]] == [L.ARM_ISSUE]
    # The accepted false positive, pinned rather than left to be "discovered".
    per_criterion = "## Satisfies\n\n### 1. Guard holds\n\n- [ ] a\n"
    assert L.has_satisfies_checklist([per_criterion]) is False
    # CONTROLS: boxes directly under the heading still clear it, at any level of `Satisfies`.
    for ok in ("## Satisfies\n\n- [ ] a\n", "### Satisfies\n\n- [ ] a\n"):
        assert L.has_satisfies_checklist([ok]) is True, ok


def test_the_ascii_hyphen_is_an_appositive_dash_too():
    """Round 28 P3 — a REGRESSION PIN, not a red-first fix.

    ⚠️ Measured honest: both bodies already returned `[]` at `5e1c557b83` and at the parent, so the
    code behaved this way before this commit and nothing here was "fixed". It is kept because no
    other case covers `-` or `–` as an appositive dash, so it is what stops a later narrowing of
    `_REASON_TAIL` from silently re-flagging the two most natural spellings.
    """
    for honest in ("Refs #100 — not closing it - measurement only.\n",
                   "Refs #100 — not closing it – measurement only.\n"):
        assert L.classify_pr(honest, 1) == [], honest
    # The no-`#` guard is what keeps the widening contained.
    assert [f.arm for f in L.classify_pr("Refs #100 — not closing it - #300 must land first.\n", 1)] \
        == [L.ARM_PR]
