"""#5329 — the boundary's marker vocabularies are finite; an unlisted marker
must be COUNTED, never silently absorbed.

The boundary refuses a fold on differences read from closed, hand-written word
lists (``_NEGATION_MARKERS``, ``_CONDITION_MARKERS``/``_CONDITION_PHRASES``,
``_DATE_WORDS``, ``_CLOCK_UNITS``, ``_SCRIPT_BLOCKS``).  A marker outside every
list reaches no identity dimension, so the pair reads as a one-sided broadening
and FOLDS — and before this census the fold left no trace anywhere.

What is under test here is the OBSERVABILITY, not the fold decision: the census
records what the vocabularies did not understand, and the decision is
bit-identical to before (a wrong keep is noise; a wrong drop is memory loss, and
failing closed on every unknown token would over-block the legitimate
broadening case).
"""
from __future__ import annotations

import logging

import pytest

from tortoise import extractor_v2 as v2


@pytest.fixture(autouse=True)
def _fresh_census():
    """Each test measures its own window — the census is process-global."""
    v2.reset_vocabulary_gap_census()
    yield
    v2.reset_vocabulary_gap_census()


#: The exemplars #5329 pins as folds: negators/quantifiers, a condition
#: connective, relative days.  None is named by any boundary vocabulary.
UNLISTED_MARKERS = [
    ("we hardly ship", "we ship", "hardly"),
    ("we rarely ship", "we ship", "rarely"),
    ("nobody approved the plan", "the plan approved", "nobody"),
    ("we ship lest the build fails", "we ship the build fails", "lest"),
    ("we ship tonight", "we ship", "tonight"),
]


@pytest.mark.parametrize("a,b,marker", UNLISTED_MARKERS)
def test_an_unlisted_marker_is_counted_not_silently_absorbed(a, b, marker):
    """The fold still happens (unchanged decision) — but it now says what it
    dropped.  Before the census this assertion set was ``{}``."""
    assert v2.fold_allowed(a, b) is True
    report = v2.vocabulary_gap_report()
    assert marker in report, (
        f"{marker!r} was absorbed by a fold and no vocabulary named it, yet "
        f"the census did not report it; report={report}"
    )
    assert report[marker] >= 1


def test_the_census_counts_each_occurrence():
    """A token is a COUNT, not a set membership: a marker absorbed 3 times in
    a corpus reports 3, which is what makes 'how much fell off the end'
    answerable."""
    for _ in range(3):
        assert v2.fold_allowed("we hardly ship", "we ship")
    snap = v2.vocabulary_gap_snapshot()
    assert snap["tokens"]["hardly"] == 3
    # Named for what it counts (review F3): boundary CALLS that returned a fold
    # decision, not claims folded — `fold_allowed` + `supersede_allowed` on one
    # pair counts twice.
    assert snap["fold_decisions"] == 3
    assert snap["truncated_observations"] == 0


def test_a_named_pair_read_token_is_reported_in_its_own_bucket_not_as_a_gap():
    """Review F2 + cycle-2 ISSUE 2: named by a list is not UNDERSTOOD by a fold.

    ``off`` is in ``_POLARITY_MEMBERS`` and ``because`` in
    ``_CONNECTIVE_MEMBERS``; both are read from the PAIR (#5134/#5139), so
    neither is an unnamed vocabulary gap and the flat report must not claim one
    (review F2).

    But the guard fires only on a SWAP (``_connective_swap``) or on a copula
    whose whole complement is the state (``_polarity_drop``) — so a one-sided
    `because` / `off` reaches no identity dimension and the fold DROPS it. That
    drop is the pair-read guard's residual, and it used to be subtracted on
    token membership, which reported the residual as zero (review cycle 2,
    ISSUE 2: "the subtraction suppresses exactly the one-sided case the census
    exists to surface"). It is now COUNTED, in its own bucket.

    Non-vacuity: on the pre-fix census the snapshot had no such keys at all, so
    this leg errors rather than passing; the flat-report half is unchanged and
    still pins F2.
    """
    assert v2.fold_allowed("we turn it off please", "we turn it please")
    assert v2.fold_allowed("we ship because tests pass", "we ship tests pass")
    report = v2.vocabulary_gap_report()
    assert "off" not in report
    assert "because" not in report
    snap = v2.vocabulary_gap_snapshot()
    assert snap["one_sided_state"] == {"off": 1}
    assert snap["one_sided_connective"] == {"because": 1}
    assert snap["pair_read_residual"] == 2
    # The narrow claim is the one that was wrong: the residual is NOT zero.
    assert sum(snap["tokens"].values()) == 0, (
        "a pair-read family token leaked into the UNNAMED report — the flat "
        f"report now calls a named token a vocabulary gap: {snap['tokens']}")


def test_a_pair_read_guard_that_FIRES_is_not_residual():
    """The other half of ISSUE 2: residual means the guard did NOT fire.

    A `because`/`but` swap fills the same slot on both sides, so
    ``_connective_swap`` refuses the pair — the boundary DID understand that
    difference, and a refusal is not a fold, so nothing is recorded anywhere.
    Without this leg, the buckets could be fed by token membership alone and
    would report a loss on every pair that carries a connective.
    """
    assert v2.distinguishing_difference(
        "we ship because tests pass", "we ship but tests pass") \
        == "substituted_content"
    assert not v2.fold_allowed("we ship because tests pass",
                               "we ship but tests pass")
    snap = v2.vocabulary_gap_snapshot()
    assert snap["one_sided_connective"] == {}
    assert snap["one_sided_state"] == {}
    assert snap["pair_read_residual"] == 0
    assert snap["fold_decisions"] == 0, (
        "a refused pair was counted as a fold decision — the census records "
        "FOLDS, and a guard that fires is not one")
    assert snap["tokens"] == {}


def test_the_pair_read_families_are_disjoint_so_a_token_has_one_bucket():
    """``_pair_read_bucket`` needs no precedence rule only while this holds.

    A token in BOTH families would be counted in one bucket by list order, and
    a reader sizing the residual could not tell which guard owns it.
    """
    assert not (v2._CONNECTIVE_MEMBERS & v2._POLARITY_MEMBERS)
    assert v2._VOCABULARY_GAP_UNDERSTOOD == (v2._CONNECTIVE_MEMBERS
                                             | v2._POLARITY_MEMBERS)
    for tok in sorted(v2._CONNECTIVE_MEMBERS):
        assert v2._pair_read_bucket(tok) == "one_sided_connective"
    for tok in sorted(v2._POLARITY_MEMBERS):
        assert v2._pair_read_bucket(tok) == "one_sided_state"
    assert v2._pair_read_bucket("hardly") is None, (
        "an UNLISTED token was bucketed as pair-read — the two populations "
        "must stay separable")


def test_a_raising_log_handler_cannot_change_the_decision():
    """Review F1 (a false REFUSAL, the worst direction): the census is called
    from the boundary's fail-closed path, so anything it raises would be read
    as an unreadable pair and refuse a fold the pair is entitled to.

    Structured/JSON/Sentry log handlers raise on emit in production. The
    decision must be identical with such a handler installed.
    """

    class _Boom(logging.Handler):
        def emit(self, record):
            raise RuntimeError("handler exploded")

    logger = logging.getLogger("tortoise.extractor_v2")
    boom = _Boom()
    logger.addHandler(boom)
    try:
        assert v2.fold_allowed("we ship quickly to production",
                               "we ship to production") is True
        # ...and the sighting is still counted, handler or not.
        assert v2.vocabulary_gap_report().get("quickly") == 1
        # A second, DIFFERENT token must not flip the decision either.
        assert v2.fold_allowed("we ship promptly to production",
                               "we ship to production") is True
    finally:
        logger.removeHandler(boom)


def test_an_oversize_token_is_counted_but_not_retained():
    """Review F4: a token comes from ``_content_tokens``, so a claim can carry
    a URL or a blob. Retaining it verbatim would pin claim-sized strings for
    the process's lifetime and emit claim-sized log records."""
    blob = "z" * (v2._VOCABULARY_GAP_MAX_TOKEN + 10)
    assert v2.fold_allowed(f"we ship {blob} now", "we ship now")
    snap = v2.vocabulary_gap_snapshot()
    assert blob not in snap["tokens"]
    assert snap["oversize_observations"] == 1
    assert all(len(t) <= v2._VOCABULARY_GAP_MAX_TOKEN for t in snap["tokens"])


def test_a_pair_the_vocabulary_does_understand_reports_no_gap():
    """A refusal is NOT a gap: "not" is named, so nothing was 'not understood'.
    Without this, the census would just count every claim pair and the count
    would mean nothing."""
    assert v2.distinguishing_difference("we ship", "we do not ship") == "negation"
    assert v2.distinguishing_difference(
        "we ship the build", "we ship the build if tests pass") == "condition"
    assert v2.vocabulary_gap_report() == {}


def test_a_near_miss_of_a_known_marker_is_not_coerced_into_it():
    """#5329 discriminator: the fix must REPORT, never widen or fuzzy-match.

    A naive fix ("extend the list so the pins pass") or a shape-based
    coercion would read ``notw`` as a form of ``not`` — a fabricated negation,
    which REFUSES a fold the boundary currently allows and changes a decision
    on evidence the code does not have.  It must be counted as its own token
    and the decision must stay a fold.
    """
    assert v2.distinguishing_difference("we notw ship", "we ship") is None
    report = v2.vocabulary_gap_report()
    assert report == {"notw": 1}
    assert "not" not in report


def test_the_census_is_bounded_and_says_so():
    """Memory-safety of the instrument itself: the token table is capped, and
    the snapshot reports truncation, so a bounded sample never reads as a
    complete one."""
    cap = v2._VOCABULARY_GAP_CAP
    for i in range(cap + 5):
        # Alphabetic only: a digit-bearing token is the VALUE dimension's job
        # (it reports a number difference, so no fold and no gap).
        tok = "zz" + "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3))
        v2._boundary(f"we ship {tok} now", "we ship now")
    snap = v2.vocabulary_gap_snapshot()
    assert snap["distinct_tokens"] <= cap
    assert snap["truncated_observations"] > 0
    assert snap["cap"] == cap


def test_a_first_sighting_is_loud(caplog):
    """The alarm half: an unlisted marker announces itself on FIRST sight,
    once — the census is the durable record, the WARNING is the alarm."""
    with caplog.at_level(logging.WARNING, logger="tortoise.extractor_v2"):
        v2.fold_allowed("we hardly ship", "we ship")
        v2.fold_allowed("we hardly ship", "we ship")
    hits = [r for r in caplog.records if "vocabulary gap" in r.getMessage()]
    assert len(hits) == 1
    assert "hardly" in hits[0].getMessage()
