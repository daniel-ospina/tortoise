"""#14033 — a state question that misses the cue set must not become a search.

The defect: `tortoise/state_intent.py` recognises a state question by a small set
of ANCHORED shapes, and anything it does not recognise falls through to the
ordinary relevance read. For a question that is plainly *about state* but is not
in one of the recognised shapes, that fallthrough is not a miss — it is a
confident answer to a different question. Measured against `origin/main`
``e6b3cf2908ba``:

    has anything we relied on been withdrawn     -> ended        (routes)
    what ended                                   -> ended        (routes)
    what has ended recently                      -> ORDINARY SEARCH
    is the onboarding flow retired?              -> ORDINARY SEARCH
    list what we retired                         -> ORDINARY SEARCH

and nothing in the reply says the state route was never engaged, so an empty
result is indistinguishable from an honest "nothing has ended".

WHAT IS PINNED HERE, and it is the two directions at once — widening a cue set is
only safe if it costs no precision:

* **the three phrasings #14033 names reach the ended population**, each through
  a NAMED shape, so the row says which words selected the population;
* **the four content questions #14033 names must NOT route** — a policy question,
  an attributive participle, and a constrained question are all content lookups,
  and a detector that hijacks them answers a different question than the one
  asked (`have any customers cancelled this month`, `which features are
  deprecated in v3`, `do we archive customer emails`, `which claims were
  retracted by legal`), which is the defect class #13558's precision rounds
  removed;
* **the attributive class stays refused** — `which of our withdrawn claims
  affected pricing` is #14033's own example of a question the default currency
  filter hides the answer to, and it is still NOT routed here on purpose: the
  participle MODIFIES `claims`, which is the same slot as the pinned
  `which archived report has the Q3 numbers`. Routing a modifier is how the
  round-2/4/5 hijacks came back; the caller's way to the population is the
  unqualified form or ``ended_scope=True``;
* **a constraint is still not dropped in silence** — `what has ended recently`
  routes because a vague adverb adds no window the population could have
  applied, while `what has ended in Q3` still does not, because `in Q3` is a
  narrowing and the population read cannot honour it.

The population itself is not re-tested here: `tests/test_13557_13558_reachable_reads.py`
owns the set semantics (live graph). This file is the ROUTING table, offline and
deterministic, so it runs anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: #14033's three unfired phrasings, and the shape each must reach the ended
#: population through. The shape name is part of the pin: it is what the reply
#: would cite as "which words selected the population".
NOW_ROUTING = [
    # a trailing vague adverb — "recently"/"lately" names no window the
    # population read could apply, so it does not narrow anything away.
    ("what has ended recently", "wh_whole_question"),
    ("what ended recently", "wh_whole_question"),
    ("what has been withdrawn lately", "wh_whole_question"),
    # a yes/no question about ONE record — "is X retired" is a state question
    # whose predicate IS the ending, and the population answers it WITH the
    # record's own status word (``ended.py`` serves how each record ended).
    ("is the onboarding flow retired?", "is_predicate"),
    ("is our pricing decision withdrawn", "is_predicate"),
    ("is the Q3 target abandoned?", "is_predicate"),
    # an imperative that asks for the SET by clause, not for one named record.
    # ⛔ `show me the retired onboarding flow` is still refused (it asks for ONE
    # record); the `what we <verb>` clause is what makes this a set request.
    ("list what we retired", "list_what"),
    ("list what we archived", "list_what"),
]


@pytest.mark.parametrize(("query", "shape"), NOW_ROUTING)
def test_a_state_question_that_missed_the_cue_set_now_reaches_the_population(query, shape):
    from tortoise.state_intent import ENDED, detect_state_question

    got = detect_state_question(query)
    assert got.population == ENDED, (
        f"{query!r} answered as an ordinary search ({got.population!r}, shape "
        f"{got.shape!r}) — a state question the recogniser cannot classify is "
        f"served as a relevance answer, so the caller gets a confident answer "
        f"to a different question and cannot tell the state route never fired "
        f"(#14033)")
    assert got.shape == shape, (
        f"{query!r} reached {got.population!r} by shape {got.shape!r}, not "
        f"{shape!r} — the table this file pins is the routing contract, so a "
        f"shape that drifted is a different claim about the question")


#: #14033's four content questions, which must KEEP the relevance read. Each is
#: a different reason, and the reason is why the widening above is narrow:
#:   * policy (present-habitual) — asks what we do, not which records ended;
#:   * attributive participle inside a `which` clause — a modifier, not the
#:     question's predicate;
#:   * constrained — the caller narrowed the request and a population cannot
#:     apply the narrowing, so routing would serve the whole set as if it had.
STILL_NOT_ROUTING = [
    "have any customers cancelled this month",
    "which features are deprecated in v3",
    "do we archive customer emails",
    "which claims were retracted by legal",
]


@pytest.mark.parametrize("query", STILL_NOT_ROUTING)
def test_the_content_questions_14033_names_still_do_not_route(query):
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population is None, (
        f"{query!r} was routed to the {got.population!r} population (shape "
        f"{got.shape!r}) — it is a content question, and a detector that "
        f"hijacks one answers a question the caller did not ask (#14033)")


def test_the_widening_does_not_admit_the_attributive_participle():
    """#14033's own fourth table row stays a relevance read, and says why.

    `which of our withdrawn claims affected pricing` is a question whose answer
    the default currency filter hides — but the participle here MODIFIES
    `claims`, which is exactly the slot the pinned
    `which archived report has the Q3 numbers` occupies. Routing a modifier is
    the round-2/4/5 hijack class, so this refuses and the caller reaches the
    population the stated way instead (``ended_scope=True``, or the unqualified
    `which claims were withdrawn`).
    """
    from tortoise.state_intent import ENDED, detect_state_question

    got = detect_state_question("which of our withdrawn claims affected pricing")
    assert got.population is None, (
        f"the attributive participle routed to {got.population!r} (shape "
        f"{got.shape!r}) — `withdrawn` modifies `claims` here, and a detector "
        f"that reads a modifier as the question's predicate is the hijack the "
        f"precision rounds removed")

    # …and the same question in its unqualified form does reach the population,
    # so the refusal above is a boundary and not a dead end.
    assert detect_state_question("which claims were withdrawn").population == ENDED


def test_a_constraint_is_still_not_dropped_in_silence():
    """The adverb is admitted; an actual WINDOW is not.

    `what has ended recently` routes because `recently` names no boundary the
    population read could apply — the rows carry their own `endedAt`, so nothing
    the caller said is discarded. `in Q3` IS a boundary, and a population read
    cannot honour it, so it keeps the relevance read rather than being served as
    if the narrowing had been applied.
    """
    from tortoise.state_intent import ENDED, detect_state_question

    assert detect_state_question("what has ended recently").population == ENDED
    assert detect_state_question("what has ended in Q3").population is None
    assert detect_state_question("what was retired this quarter").population is None
    assert detect_state_question("list what we retired last quarter").population is None


def test_the_two_guards_inside_is_predicate_are_each_load_bearing():
    """Both halves of `is_predicate` have a pinned falsifier.

    ⛔ A pin has to be able to reach the guard it pins. The `what is the payment
    terms clause given the contract was archived` row of ``NOT_SELECTED`` is
    refused by the START anchor (`^is`), so it cannot detect the tail anchor or
    the NP bound at all; the strings below are the ones that go red when either
    is removed:

    * dropping the tail anchor routes `is the report archived in S3` — a lookup
      whose ending is not the whole predicate;
    * widening the NP bound routes `is the payment terms clause given the
      contract was archived` and `is the policy when a booking is cancelled` —
      subordinate clauses carrying their own finite verb;
    * dropping the relative-clause stop routes `is the report that legal
      archived` and `is the ticket that support cancelled` — the NP word run
      would swallow the clause and read its own finite verb as the question's
      predicate, which is the round-4/5 hijack class inside the word budget.
    """
    from tortoise.state_intent import detect_state_question

    for narrowed_or_subordinate in (
        "is the report archived in S3",
        "is the payment terms clause given the contract was archived",
        "is the policy when a booking is cancelled",
        "is the onboarding flow retired this quarter",
        "is the report that legal archived",
        "is the ticket that support cancelled",
        "is the doc which we archived",
    ):
        got = detect_state_question(narrowed_or_subordinate)
        assert got.population is None, (
            f"{narrowed_or_subordinate!r} routed to {got.population!r} (shape "
            f"{got.shape!r}) — the ending is inside a narrowing or a subordinate "
            f"clause, so the question is not about state (#14033 keeps #13558's "
            f"tail anchor)")


def test_the_imperative_lookup_for_one_record_is_still_refused():
    """`list what we <verb>` is a set request; `show me the retired X` is not."""
    from tortoise.state_intent import detect_state_question

    for one_record in (
        "show me the retired onboarding flow",
        "the retired onboarding flow",
        "onboarding flow retired",
        "list our commitments",
    ):
        got = detect_state_question(one_record)
        assert got.population is None, (
            f"{one_record!r} routed to {got.population!r} (shape {got.shape!r}) "
            f"— an imperative naming ONE record is a lookup, not a request for "
            f"the population (#14033 keeps #13558's measured hijack refused)")
