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

* **the two phrasings #14033 names that can move WITHOUT a surface re-cut reach
  the ended population** — `is the onboarding flow retired?` and
  `list what we retired` — each through a NAMED shape, so the row says which
  words selected the population;
* **`what has ended recently` deliberately still does NOT route**, and this file
  pins that too: the `tortoise_search` HANDLER DOCSTRING
  (`tortoise/mcp_server.py` — not the served `ToolDefinition.description`, which
  carries no state-routing sentence) names that exact string as a RELEVANCE
  read, so routing it here would leave that documented contract wrong about a
  phrasing it names — #14033's own acceptance item #3. The adverb is harmless
  to the POPULATION (the ended read is ordered by `endedAt` newest-first, so
  nothing a caller said is narrowed away), so this is deferred rather than
  refused: it moves together with the docstring, which is a frozen baseline cell
  and an owner-gated surface edit (#4282);
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
* **a constraint is still not dropped in silence** — `what has ended in Q3`,
  `what was retired this quarter` and `list what we retired last quarter` keep
  the relevance read, because `in Q3` / `this quarter` are narrowings the
  population read cannot honour. The same tail anchor refuses the adverb, but
  the two are pinned in SEPARATE tests because they refuse for different
  reasons and will move at different times: a window is refused on the merits,
  the adverb is deferred on the contract.

The population itself is not re-tested here: `tests/test_13557_13558_reachable_reads.py`
owns the set semantics (live graph). This file is the ROUTING table, offline and
deterministic, so it runs anywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


#: #14033's phrasings that now reach the ended population, and the shape each
#: must reach it through. The shape name is part of the pin: it is what the reply
#: would cite as "which words selected the population".
#: ⛔ Only TWO of the issue's three: `what has ended recently` is pinned as still
#: refused, in `test_the_adverb_tail_stays_out_until_the_docstring_moves_with_it`.
NOW_ROUTING = [
    # a yes/no question about ONE record — "is X retired" is a state question
    # whose predicate IS the ending, and the population answers it WITH the
    # record's own status word (``ended.py`` serves how each record ended).
    ("is the onboarding flow retired?", "is_predicate"),
    ("is our pricing decision withdrawn", "is_predicate"),
    ("is the Q3 target abandoned?", "is_predicate"),
    # …and the same question in another tense / number, and with an ordinary
    # apostrophe or hyphen inside the head noun. Each of these answered `0 rows`
    # before (the relevance read EXCLUDES terminal statuses), so refusing them is
    # the same silent-empty defect in a different spelling.
    ("was the onboarding flow retired?", "is_predicate"),
    ("are our endpoints deprecated", "is_predicate"),
    ("were the claims retracted", "is_predicate"),
    ("is the company's policy archived", "is_predicate"),
    ("is the all-hands meeting cancelled", "is_predicate"),
    # an imperative that asks for the SET by clause, not for one named record.
    # ⛔ `show me the retired onboarding flow` is still refused (it asks for ONE
    # record); the `what we <verb>` clause is what makes this a set request.
    ("list what we retired", "list_what"),
    ("list what we archived", "list_what"),
    # the PAST form of `withdrawn`, which the participle family does not contain
    ("list what we withdrew", "list_what"),
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


def test_the_adverb_tail_stays_out_until_the_docstring_moves_with_it():
    """`what has ended recently` is DEFERRED, not forgotten, and this is the pin.

    #14033 names it as one of the three measured misses, and this change does not
    fix that one. The reason is not precision — `recently` names no boundary and
    the read is ordered by `endedAt` newest-first, so the population read would
    honour it — it is the CONTRACT: the `tortoise_search` HANDLER DOCSTRING
    (`tortoise/mcp_server.py` — not the served `ToolDefinition.description` in
    `tortoise/tool_registry.py`, which carries no state-routing sentence) states
    that `what has ended recently` is a RELEVANCE read, so routing it here would
    leave that documented contract wrong about a phrasing it names, which is
    #14033's acceptance item #3 rather than a way to satisfy it. That docstring
    is a frozen baseline cell (its `co_consts` feed `served_from`, so the edit
    moves the recorded surface identity; an owner-gated re-cut, #4282), so the
    two move in one change or not at all.

    ⛔ This test reds if the adverb tail is admitted WITHOUT the re-cut — that is
    the whole point. Whoever lands the re-cut deletes these rows and moves them
    into ``NOW_ROUTING`` with shape `wh_whole_question`, and the diff then shows
    both halves of the change together.
    """
    from tortoise.state_intent import ENDED, detect_state_question

    for adverb_tailed in (
        "what has ended recently",
        "what ended recently",
        "what has been withdrawn lately",
    ):
        got = detect_state_question(adverb_tailed)
        assert got.population is None, (
            f"{adverb_tailed!r} now routes to {got.population!r} (shape "
            f"{got.shape!r}) while the `tortoise_search` handler docstring still "
            f"names it a relevance read — that leaves the documented contract "
            f"wrong about a phrasing it names (#14033 acceptance #3). Admit it "
            f"only together with the owner-gated docstring re-cut (#4282).")

    # …and the unqualified forms are the advertised route, so the deferral above
    # is a boundary and not a dead end.
    assert detect_state_question("what ended").population == ENDED
    assert detect_state_question("what has been withdrawn").population == ENDED


def test_a_constraint_is_still_not_dropped_in_silence():
    """An appended WINDOW keeps the relevance read, and so does any tail token.

    `in Q3` and `this quarter` are narrowings a population read cannot apply, so
    serving the whole ended set as if the narrowing had been honoured would drop
    what the caller said — the mirror defect of the fallthrough this change
    fixes. It is the same tail anchor that keeps the adverb out (see
    `test_the_adverb_tail_stays_out_until_the_docstring_moves_with_it`), which
    is why the two are pinned separately: they refuse for different reasons and
    will move at different times.
    """
    from tortoise.state_intent import detect_state_question

    for narrowed in (
        "what has ended in Q3",
        "what was retired this quarter",
        "list what we retired last quarter",
        "is our pricing decision withdrawn in Q3",
    ):
        got = detect_state_question(narrowed)
        assert got.population is None, (
            f"{narrowed!r} routed to {got.population!r} (shape {got.shape!r}) — "
            f"a window is a narrowing the population read cannot apply, so "
            f"routing drops what the caller asked for")


def test_the_six_guards_inside_is_predicate_are_each_load_bearing():
    """All six guards of `is_predicate` have a pinned falsifier.

    ⛔ A pin has to be able to reach the guard it pins. The
    `is the payment terms clause given the contract was archived` row of
    ``NOT_SELECTED`` (``tests/test_13557_13558_reachable_reads.py``) is refused
    by the NP STOP — `the` — and by nothing else: measured, relaxing the start
    anchor, dropping the tail anchor and widening the word bound all leave it
    refused, and only dropping `_NP_STOP` routes it. So it cannot pin any of the
    other guards, and the strings below are the ones that go red when a guard is
    removed:

    * dropping the tail anchor routes `is the report archived in S3` — a lookup
      whose ending is not the whole predicate;
    * widening the NP bound (three extra words allowed) routes
      `is the very old legacy pricing plan retired` — a five-word NP. ⛔ The
      payment-terms and `when` strings do NOT red under that mutant: each is
      already refused by the NP stop (`the`, `when`), so naming them here would
      be an attribution the measurement does not support;
    * dropping the relative-clause stop routes `is the report that legal
      archived` and `is the ticket that support cancelled` — the NP word run
      would swallow the clause and read its own finite verb as the question's
      predicate, which is the round-4/5 hijack class inside the word budget;
    * dropping the NP stop routes the ZERO-relative spelling, which the
      marker-only guard left open: `is the plan the team abandoned`, `is the
      data we retired`, `is the ticket they deprecated`,
      `is the release the team cancelled`. ⛔ The residual is stated, not hidden:
      a zero relative with a BARE-NOUN subject (`is the claim finance retracted`)
      is lexically identical to a two-word head noun plus a predicate, so it
      routes, and so does one with a bare quantifier subject (`is the plan some
      team abandoned`), and so does a TIME SPAN that names no record
      (`is the quarter ended`) — see the module's ``_NP_STOP`` note for all
      four residuals;
    * …and the same stop is what refuses an ADJUNCT: `is the data safe once
      archived`, `is the report still accurate once archived` and
      `is the policy unless bookings were cancelled` — the ending there belongs
      to an adjunct clause and the question's predicate is `safe` / `accurate` /
      the policy's applicability, so the run would swallow it;
    * the PREDICATE-MODIFIER stop is the sixth guard, and its falsifier is the
      twelve measured rows of
      :func:`test_a_modified_predicate_is_not_the_question_asked` — dropping the
      `not|never|…|or` line routes the negated / future / disjunctive predicate
      class, which answers the question with the POLARITY-INVERTED (or a
      not-yet-existing) population;
    * relaxing the START anchor (`^(?:is|are|was|were)` →
      `(?:^|\\b)(?:is|are|was|were)`) routes the two NESTED
      clauses at the end of this loop — measured, with that one edit, nothing
      else in this file moves. That anchor was the clause with no falsifier
      (PR #14491 review, round 3): the shape is a yes/no question that OPENS the
      query, and a subordinate `… is the report archived` is the same indirect
      question class `which vendor said our commitments were archived` refuses.
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
        "is the plan the team abandoned",
        "is the data we retired",
        "is the ticket they deprecated",
        "is the release the team cancelled",
        "is the data safe once archived",
        "is the report still accurate once archived",
        "is the policy unless bookings were cancelled",
        "is the plan anyone abandoned",
        "is the ticket both teams cancelled",
        "is the report nobody archived",
        "is the ticket every team cancelled",
        "is the plan another team abandoned",
        "is the report few engineers archived",
        "the question is the report archived",
        "the question is the onboarding flow retired",
        "is the very old legacy pricing plan retired",
    ):
        got = detect_state_question(narrowed_or_subordinate)
        assert got.population is None, (
            f"{narrowed_or_subordinate!r} routed to {got.population!r} (shape "
            f"{got.shape!r}) — the ending is inside a narrowing or a subordinate "
            f"clause, so the question is not about state (#14033 keeps #13558's "
            f"tail anchor)")


def test_the_imperative_lookup_for_one_record_is_still_refused():
    """The imperative frame is a lookup; only the `what we <verb>` clause is a set.

    ⛔ WHAT THESE ROWS PIN, stated exactly (PR #14491 review, P1/P3): they are
    BOUNDARY pins, not coverage of ``list_what``'s discriminator. Every row here
    refuses because no shape in :data:`_ENDING_SHAPES` admits a bare imperative
    naming a record — `^list …` reaches nothing (`list_what` requires the
    `what we <verb>` clause) and `show me …` reaches nothing at all — so no
    mutant of THIS diff reds them. They are the falsifier against the widening
    that would admit the imperative mood itself (dropping `what we` and relaxing
    the tail anchor), which is the hijack #13558 measured: an imperative naming
    ONE record would then be answered with the whole ended population.

    ``list_what``'s own two guards are pinned where they can be reached:
    removing the shape reds the ``NOW_ROUTING`` rows, and dropping its tail
    anchor reds `list what we retired last quarter` in
    :func:`test_a_constraint_is_still_not_dropped_in_silence`.
    """
    from tortoise.state_intent import detect_state_question

    for one_record in (
        "show me the retired onboarding flow",
        "the retired onboarding flow",
        "onboarding flow retired",
        "list the retired onboarding flow",
        "list our commitments",
    ):
        got = detect_state_question(one_record)
        assert got.population is None, (
            f"{one_record!r} routed to {got.population!r} (shape {got.shape!r}) "
            f"— an imperative naming ONE record is a lookup, not a request for "
            f"the population (#14033 keeps #13558's measured hijack refused)")


#: A PREDICATE MODIFIER between the head noun and the ending — the slot can only
#: be modifying the predicate, and every row here was answered with the ended
#: population before the NP stop carried `not|never|to|be|going|about|due|
#: scheduled|or|neither|nor|longer` (PR #14491 review, P1/P2 — measured on the
#: pre-fix head, all routed to `ended`/`is_predicate`).
#:
#: Three classes, one failure each, and none of them is the question asked:
#:
#: * NEGATION — `is the plan not cancelled` was served the population of
#:   **cancelled** records: the polarity-INVERTED answer to the question. That is
#:   #14033's own defect class ("a confident answer to a different question")
#:   reached by a new route, and it is over-admission, not an inherited boundary:
#:   every pre-existing shape refuses the class on the same tree
#:   (`which plans are not cancelled`, `what was not archived`, `what did we not
#:   archive` all stay relevance reads).
#: * FUTURITY / INTENT — `is the v2 api to be retired` asks about something that
#:   has NOT ended; the ended population can only answer with records that have.
#: * DISJUNCTION — `is the ticket still open or cancelled` asks about two states
#:   at once, and one population cannot answer both.
#:
#: ⛔ ATTRIBUTION, row by row, because a pin must be able to reach its guard:
#: every row reds on the pre-fix head EXCEPT `is the plan not going to be
#: cancelled`, which the NP bound (`{0,3}`) already refused — it is a BOUNDARY
#: pin for the future class, not a non-vacuity row, and it is named here rather
#: than left to be re-measured. The rest were all measured leaking.
MODIFIED_PREDICATE_STAYS_OUT = (
    # negation
    "is the plan not cancelled",
    "is the api not deprecated",
    "is the claim never archived",
    "is the plan not yet archived",
    "was the plan not cancelled",
    "were the claims not retracted",
    "are our endpoints not deprecated",
    # negation spelt with a correlative or a `no longer` — the same class, and
    # the reason `longer`/`neither`/`nor` are stop words while `no` is not
    "is the plan no longer archived",
    "is the plan neither deprecated nor retired",
    "is the ticket still not archived",
    "is the plan any longer cancelled",
    # futurity / intent — the ending has not happened, so the ended population
    # cannot answer it (the second row is the NP-bound boundary pin above)
    "is the v2 api to be retired",
    "is the report scheduled to be archived",
    "is the report going to be archived",
    "is the deal about to be cancelled",
    "is the ticket due to be archived",
    "is the plan not going to be cancelled",
    "is the plan still to be cancelled",
    # disjunction
    "is the ticket still open or cancelled",
    # the CONTRACTION spelling — `n't` is a SUFFIX inside the token, so a bare
    # `n't` alternative stopped only the literal word and these routed
    "is the plan isn't cancelled",
    "is the report wasn't archived",
    "is the ticket hasn't been archived",
    "is the api doesn't work retired",
    # an ADJUNCT/CLAUSE LINKER — the ending is inside the adjunct, so the
    # question's predicate is `safe` / `accurate` / the policy's applicability
    "is the data safe once archived",
    "is the report still accurate once archived",
    "is the policy unless bookings were cancelled",
    "is the report accurate since archived",
    "is the report accurate whereas archived",
    "is the data safe whenever archived",
    "is the policy valid whether archived",
    # an `of`-COMPLEMENT whose head is the ending word: the question is about
    # the LIST, and the ending modifies the complement
    "is the list of retired services archived",
    "is the number of cancelled claims archived",
    "is the total of cancelled claims archived",
    "is the rate of cancelled bookings archived",
)

#: …and the recall the same stop words must NOT cost: the frame words are
#: ordinary noun modifiers and `of`-complements over LIVE records are real
#: questions. Each of these routes at this head and refuses if the corresponding
#: stop alternative is widened past the frame it is about.
STOP_WORDS_KEEP_THIS_RECALL = (
    "is the go to market plan archived",
    "is the past due invoice archived",
    "is the point to point integration archived",
    "is the board of directors report archived",
    "is the list of open tickets archived",
)


@pytest.mark.parametrize("query", STOP_WORDS_KEEP_THIS_RECALL)
def test_the_predicate_stop_words_do_not_cost_this_recall(query):
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population is not None, (
        f"{query!r} keeps the relevance read — the stop word that refuses its "
        f"neighbouring failure spelling (`to`/`due`/`of`) must only apply in the "
        f"FRAME it is about, or a real state question answers `0 rows` (the "
        f"relevance read excludes terminal statuses)")


@pytest.mark.parametrize("query", MODIFIED_PREDICATE_STAYS_OUT)
def test_a_modified_predicate_is_not_the_question_asked(query):
    """A negated / future / disjunctive predicate must not reach the population.

    The ended population is "records that HAVE ended", so a modifier that flips
    the predicate's polarity, or moves it into the future, or conjoins it with a
    second state, makes the population an answer to a different question. The
    detector's contract is the module's own — *where a shape could go either way
    it does not fire* — so the shape refuses and the caller keeps the relevance
    read they had before (#14033's fallthrough is the lesser harm here: the
    population read would be a WRONG answer, not a miss).
    """
    from tortoise.state_intent import detect_state_question

    got = detect_state_question(query)
    assert got.population is None, (
        f"{query!r} routed to {got.population!r} (shape {got.shape!r}) — the "
        f"ending is modified by a negation, a futurity or a disjunction, so the "
        f"population is an answer to a different question. A negated predicate "
        f"is the worst: the caller is served the POLARITY-INVERTED set "
        f"(#14033, PR #14491 review P1/P2)")


def test_an_affirmative_temporal_modifier_still_routes():
    """The modifier guard is narrower than "any adverb in the slot", measured.

    ⛔ These three rows are the RECALL half of the P1/P2 remedy, and they are
    why the stop list is `not|never|neither|nor|longer|to|be|going|about|due|
    scheduled|or` rather than that list plus `yet|still|already` (the shape the
    review measured). Both close the whole over-admission class; the wider list
    additionally refuses all three strings below, which ask exactly the question
    the ended population answers — has this named record already ended. Every
    row here routes on the pre-fix head too, so this test is a RECALL guard, not
    a non-vacuity pin: it reds only if someone widens the modifier list.
    """
    from tortoise.state_intent import ENDED, detect_state_question

    for affirmative in (
        "is the claim already archived",
        "is the ticket still archived",
        "is the report already withdrawn",
    ):
        got = detect_state_question(affirmative)
        assert got.population == ENDED, (
            f"{affirmative!r} answered as {got.population!r} (shape "
            f"{got.shape!r}) — `already`/`still` modify the AFFIRMATIVE predicate, "
            f"so this is the state question #14033 exists to route; refusing it "
            f"is a recall loss with no precision gain")
