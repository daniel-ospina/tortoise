"""#10539 (C5 #2513 scope unit 4) — aggregation-aware reading.

The defect this pins: ``system_prompt_for`` selects its reasoning fragment by
``detect_question_type``, a single-winner precedence classifier, so an
AGGREGATIVE question whose surface also matches a coarser cue loses counting
discipline entirely. The purpose-built detector that answers exactly this
question — ``tortoise.aggregate.detect_aggregative_intent`` (which carries its
own temporal-arithmetic exclusion, delegated to the #2886 classifier) — was
never consulted by the reader path at all.

Measured pre-fix (this file's first two tests are the red ones):
  "How many clothing items did I buy last month?"  -> type=temporal-reasoning  agg=True  discipline=False
  "What is the sum of my monthly subscriptions?"   -> type=None                agg=True  discipline=False

The first is the D3 fixture's own face-valid aggregative failure named in
#2513 ("how many clothing items"). The fix extends the fragment mechanism that
already decides the reader's instructions — it does NOT add a second gate, and
it does not re-implement aggregative detection.

§8 owner ruling (2026-09-22, posture-dependent honesty) is what the fragment's
honesty clause implements: flag by default, and abstain on a numeric total when
coverage is visibly incomplete. It is not a new policy.
"""

from __future__ import annotations

import pytest

from tortoise import reader as _reader_mod
from tortoise.aggregate import detect_aggregative_intent
from tortoise.reader import (
    detect_question_type,
    reader_prompt_constants,
    system_prompt_for,
)

#: Resolved defensively so THIS FILE IMPORTS ON THE PRE-FIX TREE. A hard
#: ``from tortoise.reader import _AGGREGATION_FRAGMENT`` raises ImportError
#: there, which turns the whole module into ONE collection error and destroys
#: the evidence this file exists to produce: a red run whose *assertions* fail,
#: proving the defect rather than proving the symbol is missing.
_AGGREGATION_FRAGMENT = getattr(_reader_mod, "_AGGREGATION_FRAGMENT", "")

#: A fragment-only marker: this phrase must appear when the reader has been
#: given counting discipline, and must not appear otherwise. Kept as one
#: lowercase substring so the assertion cannot drift from the prompt text.
DISCIPLINE_MARKER = "aggregation instructions"


def _prompt(question: str) -> str:
    """The exact system prompt the ask lane hands the reader for `question`.

    The ``TypeError`` arm is deliberate and is NOT a compatibility shim for a
    shipped path: it is what makes this file's RED run meaningful. On the
    pre-fix tree there is no question-aware seam at all, so the fallback
    returns the prompt the reader genuinely received — and the counting-
    discipline assertions then fail on their own merits rather than on an
    argument error. On the fixed tree the keyword is accepted and the real
    path is used, so a later signature regression re-reddens these tests
    instead of hiding behind the fallback.
    """
    qtype = detect_question_type(question)
    try:
        return system_prompt_for(qtype, question=question)
    except TypeError:  # pre-fix tree: no aggregation-aware seam exists yet
        return system_prompt_for(qtype)


# ── the two red-on-pre-fix cases ───────────────────────────────────────────

def test_aggregative_question_shadowed_by_a_temporal_cue_keeps_counting_discipline():
    """#10539 RED #1 — the D3 fixture's own failure.

    ``detect_question_type`` sees "last month" and returns
    ``temporal-reasoning`` first, so the question is answered with temporal
    instructions and NO counting discipline — while ``detect_aggregative_intent``
    correctly classifies it as a counting aggregation (its temporal exclusion
    is for date ARITHMETIC, not for a date word inside a count).
    """
    q = "How many clothing items did I buy last month?"
    assert detect_question_type(q) == "temporal-reasoning"  # the shadowing cause
    assert detect_aggregative_intent(q).is_aggregative is True
    assert DISCIPLINE_MARKER in _prompt(q).lower()


def test_aggregative_question_with_no_question_type_keeps_counting_discipline():
    """#10539 RED #2 — an aggregative surface the type vocabulary misses.

    "sum of" is a counting aggregation but matches none of the type patterns,
    so the question gets the generic prompt with no discipline.
    """
    q = "What is the sum of my monthly subscriptions?"
    assert detect_question_type(q) is None
    assert detect_aggregative_intent(q).is_aggregative is True
    assert DISCIPLINE_MARKER in _prompt(q).lower()


# ── guards: the fragment must not over-fire ────────────────────────────────

@pytest.mark.parametrize("q", [
    "What is my preferred ratio for espresso?",
    "What did I decide about the gym schedule?",
    "Where did I put the spare key?",
])
def test_non_aggregative_question_gets_no_counting_discipline(q):
    """No counting aggregation present => the fragment must be absent, so the
    change cannot buy its two red cases by making every prompt aggregative."""
    assert detect_aggregative_intent(q).is_aggregative is False
    assert DISCIPLINE_MARKER not in _prompt(q).lower()


#: Cycle-4 finding: `is_aggregative` is the detector's SURFACE-ONLY flag, and
#: every other in-repo consumer runs it behind #2521's double gate (corpus text
#: gate + Object-anchor index gate) precisely because the quantifier vocabulary
#: matches non-counting English. The reader path is the FIRST ungated consumer,
#: so these surface true positives must be neutralized by the fragment's own
#: self-guard rather than by a second predicate (#1178).
_QUANTIFIER_FALSE_POSITIVES = [
    "What is the phone number of my dentist?",
    "What is my account number of the savings account?",
    "Do I take my vitamins every morning?",
    "What time do I usually wake up every day?",
    "Which cafe did I go to each morning?",
    "Did my sister and I call each other every week?",
    "How much do I like my new apartment?",
]


@pytest.mark.parametrize("q", _QUANTIFIER_FALSE_POSITIVES)
def test_surface_quantifier_questions_are_told_to_ignore_the_section(q):
    """These DO trip the surface detector, so the fragment will be appended —
    the fragment must therefore carry its own scope guard, telling the reader to
    ignore the section when the question is not a count. Without that guard a
    phone-number or an every-morning question is instructed to "answer with the
    aggregate" and to report a counting basis."""
    assert detect_aggregative_intent(q).is_aggregative is True  # the surface hit
    prompt = _prompt(q).lower()
    assert DISCIPLINE_MARKER in prompt
    assert "ignore this section entirely" in prompt, (
        "a surface-only quantifier hit reached the reader without a self-guard: "
        f"{q!r}")
    assert "ignore this section entirely" in _AGGREGATION_FRAGMENT.lower()


def test_the_fragment_is_scoped_to_counts_not_asserted_as_a_fact():
    """The clause must not begin by ASSERTING the question is a count — that is
    the shape that turned a surface false positive into a wrong instruction.
    It must open conditionally ("applies ONLY IF")."""
    frag = _AGGREGATION_FRAGMENT.lower()
    assert "only if this question asks for a count" in frag


@pytest.mark.parametrize("q", [
    "How many days ago did I buy the bike?",
    "How many weeks have I been a member?",
    "How long have I been working there?",
])
def test_pure_date_arithmetic_is_not_treated_as_counting(q):
    """The temporal-arithmetic exclusion is the EXISTING detector's, not this
    change's: a date-difference question must stay on the temporal fragment
    (no drift between the two detectors — #2886's classifier owns that
    vocabulary)."""
    assert detect_aggregative_intent(q).is_aggregative is False
    assert DISCIPLINE_MARKER not in _prompt(q).lower()


# ── the discipline content itself ──────────────────────────────────────────

def test_counting_discipline_requires_enumeration_then_dedupe_then_count():
    """The mechanism the field converges on (and the issue asks for): list the
    qualifying items, resolve duplicates by identity, count the deduped set —
    rather than answering a count from recall.

    Asserted against the FRAGMENT, not against the whole prompt: bare
    "count"/"session" already appear in the pre-fix temporal fragment
    ("an off-by-one error in the day count is acceptable", "the relevant
    session date"), so whole-prompt substrings would pass for the wrong
    reason.
    """
    frag = _AGGREGATION_FRAGMENT.lower()
    for required in ("enumerate", "duplicate", "refine"):
        assert required in frag, f"counting discipline lost its {required!r} step"
    assert "count" in frag


def test_counting_discipline_names_the_denominator_and_sessions():
    """The issue's explicit requirement: "what was counted, from which
    sessions, and what the denominator is". Again asserted against the
    fragment — the pre-fix prompt already contains the word "session"."""
    frag = _AGGREGATION_FRAGMENT.lower()
    assert "basis you counted from" in frag
    assert "how many sessions" in frag


def test_dedupe_key_is_session_scoped_not_description_only():
    """Rounding out the issue's counting discipline without the under-count it
    would otherwise cause: two identical purchases in DIFFERENT sessions are
    two items, only a restatement WITHIN a session is one. A description-only
    dedupe key would collapse genuinely distinct items — failing in the
    under-count direction, which is the direction #10539 exists to fix."""
    frag = _AGGREGATION_FRAGMENT.lower()
    assert "within one session" in frag
    assert "two different session dates" in frag
    assert "under-count" in frag


def test_aggregation_rules_defer_to_the_presence_commit_rule():
    """The universal A1 clause (appended to EVERY prompt, before this one)
    claims precedence and says a count/total IS present when the inputs are
    present. A counting clause that licensed abstention without saying it
    defers to that rule would push the reader back toward the over-abstention
    the A1 clause exists to prevent."""
    frag = _AGGREGATION_FRAGMENT.lower()
    assert "refine" in frag and "do not override" in frag
    assert "do not hedge" in frag


# ── prompt-drift coverage (the recording surface must see the new fragment) ──

def test_aggregation_seam_failure_keeps_the_pre_10539_prompt_and_warns_once(
        monkeypatch, caplog):
    """The fail-open must stay LOUD (#10539 review, cycle 1 item 6).

    ``_is_aggregative_question`` swallows detector failures so a broken
    detector can never break a working prompt — but a SILENT swallow lets a run
    report the aggregation-aware configuration while no prompt carried the
    clause. Two properties, both pinned here: the prompt degenerates to exactly
    the pre-#10539 text, and the failure is reported once (not per question).
    """
    import logging as _logging

    import tortoise.aggregate as _agg

    def _boom(_q):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(_agg, "detect_aggregative_intent", _boom)
    monkeypatch.setattr(_reader_mod, "_AGGREGATION_SEAM_WARNED", False,
                        raising=False)
    q = "How many clothing items did I buy last month?"
    qtype = detect_question_type(q)
    with caplog.at_level(_logging.WARNING):
        first = system_prompt_for(qtype, question=q)
        second = system_prompt_for(qtype, question=q)
    # byte-identical to the pre-#10539 prompt: the seam failure changes nothing
    assert first == system_prompt_for(qtype)
    assert second == first
    # ...but it is not silent, and it does not repeat per question
    warns = [r for r in caplog.records
             if r.levelno == _logging.WARNING and "10539" in r.getMessage()]
    assert len(warns) == 1, f"expected exactly one seam warning, got {len(warns)}"


def test_aggregation_fragment_is_recorded_for_prompt_drift_reporting():
    """``reader_prompt_constants`` is what the report records verbatim so prompt
    drift is human-visible. A fragment that is not in that dict would change the
    live prompt while the recorded prompt hash stayed identical — the exact
    silent-drift failure the constant exists to prevent.

    Pinned as an exact key+equality, not an ``any(...)`` sweep: a marker found
    under some OTHER key would satisfy a sweep without the aggregation clause
    being recorded at all.
    """
    _system, fragments = reader_prompt_constants()
    assert fragments["aggregation"] == _AGGREGATION_FRAGMENT
    assert DISCIPLINE_MARKER in fragments["aggregation"].lower()
