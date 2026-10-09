"""#2886 — deterministic temporal aggregation over admitted dated events.

HERMETIC tests (no graph, no model, no IO): the morphology classification
table, the distinct-event tally (the multi-session restatement trap), the
calendar-difference arithmetic, and the never-guess abstention contract.

The census ``frequency/count`` class is exercised directly off the committed
``tests/_assembly_census.json`` — the 12 rows the issue names — and pinned to
their measured shapes, so a future edit that stops recognising the class
fails here.

Product module under test: ``tortoise/temporal_aggregation.py``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.temporal_aggregation import (
    EventTally,
    TemporalAggregateKind,
    as_date,
    classify_temporal_aggregate,
    count_distinct_events,
    difference_in_unit,
    resolve_temporal_aggregate,
)

_CENSUS = json.loads(
    (Path(__file__).resolve().parent / "_assembly_census.json").read_text())

#: The measured shape of every one of the 12 census `frequency/count` rows.
#: NONE is a count/frequency surface: the class is date arithmetic (the
#: finding the issue's acceptance reports). Pinned so a vocabulary edit that
#: drops a member of the class fails here.
_FREQ_COUNT_SHAPES: dict[str, TemporalAggregateKind] = {
    "b46e15ed": TemporalAggregateKind.INTERVAL,
    "gpt4_1d80365e": TemporalAggregateKind.DURATION,
    "gpt4_a1b77f9c": TemporalAggregateKind.TOTAL,
    "2ebe6c90": TemporalAggregateKind.DURATION,
    "370a8ff4": TemporalAggregateKind.INTERVAL,
    "6e984301": TemporalAggregateKind.DURATION,
    "0bb5a684": TemporalAggregateKind.BEFORE_OFFSET,
    "bbf86515": TemporalAggregateKind.BEFORE_OFFSET,
    "c8090214": TemporalAggregateKind.BEFORE_OFFSET,
    "a3045048": TemporalAggregateKind.BEFORE_OFFSET,
    "gpt4_4cd9eba1": TemporalAggregateKind.DURATION,
    "c8090214_abs": TemporalAggregateKind.BEFORE_OFFSET,
}


# ── (a) the census class is recognised and bucketed ───────────────────────

def test_census_frequency_count_class_classified():
    """Every one of the 12 census `frequency/count` rows resolves to a
    temporal-aggregation shape (never None), and to its pinned shape."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] == "frequency/count"]
    assert len(rows) == 12
    got = {}
    for r in rows:
        intent = classify_temporal_aggregate(r["question"])
        assert intent is not None, (
            f"unclassified census row {r['qid']}: {r['question']!r}")
        got[r["qid"]] = intent.kind
    assert got == _FREQ_COUNT_SHAPES


def test_census_class_has_no_frequency_surface():
    """The measured mislabelling finding, pinned: NONE of the 12 is a
    counting/frequency question — all are date arithmetic. A future row edit
    that reintroduces a genuine count is a deliberate diff here."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] == "frequency/count"]
    assert all(
        classify_temporal_aggregate(r["question"]).kind
        is not TemporalAggregateKind.COUNT
        for r in rows)


def test_census_class_units_are_time_units():
    """Every class member carries a calendar unit (no bare `how long`)."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] == "frequency/count"]
    for r in rows:
        intent = classify_temporal_aggregate(r["question"])
        assert intent.unit in ("days", "weeks", "months", "years"), r["qid"]


# ── (b) classification table ──────────────────────────────────────────────

@pytest.mark.parametrize(("query", "kind", "unit"), [
    # explicit frequency / count surface (the literal issue class)
    ("How many times did we discuss the API migration?", "count", None),
    ("How often do I go to the gym?", "count", None),
    ("How frequently did I travel last year?", "count", None),
    ("What is the number of times I called my sister?", "count", None),
    # summed span → total
    ("How many weeks in total do I spend on reading and listening?",
     "total", "weeks"),
    ("How many weeks altogether did I practise?", "total", "weeks"),
    # before-offset
    ("How many days before I bought the iPhone did I attend the market?",
     "before-offset", "days"),
    ("How many weeks prior to the Rack Fest did I participate?",
     "before-offset", "weeks"),
    # interval
    ("How many months have passed since I attended the charity events?",
     "interval", "months"),
    ("How many weeks had passed since I recovered when I went jogging?",
     "interval", "weeks"),
    ("How many days passed between the MoMA visit and the exhibit?",
     "interval", "days"),
    ("Between buying the couch and selling it, how many days passed?",
     "interval", "days"),
    # duration
    ("How many days did it take me to finish the book?", "duration", "days"),
    ("How many days did I spend on my camping trip?", "duration", "days"),
    ("How many weeks have I been taking sculpting classes?", "duration",
     "weeks"),
    ("How long did the flight take?", "duration", None),
    # not a temporal aggregation
    ("What did I eat for dinner on Tuesday?", None, None),
    ("What is my current API key status?", None, None),
    ("", None, None),
    (None, None, None),
])
def test_classification_table(query, kind, unit):
    intent = classify_temporal_aggregate(query)
    if kind is None:
        assert intent is None
        return
    assert intent is not None, query
    assert intent.kind == TemporalAggregateKind(kind)
    assert intent.unit == unit


def test_classification_is_order_sensitive_by_design():
    """`in total` wins over a bare duration/preposition reading — the TOTAL
    sum must not be mistaken for one bounded span."""
    got = classify_temporal_aggregate(
        "How many weeks in total do I spend on reading and listening?")
    assert got.kind is TemporalAggregateKind.TOTAL


# ── (c) distinct-event tally (the restatement trap) ───────────────────────

def _event(content, date_, eid=None):
    e = {"content": content, "session_date": date_}
    if eid is not None:
        e["id"] = eid
    return e


def test_restatement_across_sessions_counts_once():
    """The multi-session restatement trap: the SAME identity-less event
    restated in a later session is ONE event — byte-identical content on
    rows with NO explicit id collapses. (The true subject is content
    identity, not explicit ids.)"""
    events = [
        _event("We decided to go to Tokyo.", "2025-06-01"),
        _event("We decided to go to Tokyo.", "2025-06-12"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 1
    assert tally.n_input == 2
    assert tally.collapsed == 1


def test_distinct_events_count_separately():
    events = [
        _event("We decided to go to Tokyo.", "2025-06-01", "a"),
        _event("I bought a road bike.", "2025-06-05", "b"),
        _event("I adopted a cat named Momo.", "2025-06-09", "c"),
    ]
    assert count_distinct_events(events).n_events == 3


def test_explicit_event_id_collapses_same_id():
    events = [
        _event("First phrasing of the decision.", "2025-06-01", "evt-1"),
        _event("Totally different wording here.", "2025-06-12", "evt-1"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 1
    assert tally.keys == ("id:evt-1",)


def test_distinct_explicit_ids_identical_content_stay_separate():
    """Regression (P1): the explicit id is the strongest identity. Distinct
    ids carrying IDENTICAL content are distinct occurrences — the content
    fold must NOT override two explicit ids (which silently UNDERCCOUNTED
    repeated activity). Fails on the pre-fix code, which returned 1."""
    events = [
        _event("Went to the gym.", "2025-01-01", "e1"),
        _event("Went to the gym.", "2025-02-01", "e2"),
        _event("Went to the gym.", "2025-03-01", "e3"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 3
    assert tally.n_input == 3
    assert tally.collapsed == 0
    assert tally.keys == ("id:e1", "id:e2", "id:e3")


def test_distinct_explicit_ids_identical_content_sum_all_spans():
    """Regression (P1, total path): distinct ids with identical content are
    distinct occurrences, so TOTAL sums EVERY span (not one). Fails on the
    pre-fix code, which summed 10 days once."""
    events = [
        {"id": "e1", "content": "Run.",
         "start_date": "2025-01-01", "end_date": "2025-01-11"},
        {"id": "e2", "content": "Run.",
         "start_date": "2025-02-01", "end_date": "2025-02-11"},
    ]
    tally = count_distinct_events(events, unit="days", total=True)
    assert tally.n_events == 2
    assert tally.total == 20


def test_conservative_paraphrase_collapses():
    """A conservative paraphrase (same claim, no negation/substitution) of an
    already-kept event collapses to it."""
    events = [
        _event("I really enjoyed the concert last night.", "2025-06-01"),
        _event("I enjoyed the concert last night.", "2025-06-12"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 1


def test_negation_is_not_a_restatement():
    """D12/O4: a restatement that NEGATES the claim stays its own event —
    the fold band is conservative.

    IDENTITY-LESS rows on purpose: with explicit ids the content gate is
    never consulted, so the assertion would hold even with ``fold_allowed``
    forced True (the round-2 test was vacuous for exactly this reason). With
    no ids the content gate is the ONLY identity, so a broken fold gate
    collapses the pair and drops this to 1."""
    events = [
        _event("I bought a new bike.", "2025-06-01"),
        _event("I did not buy a new bike.", "2025-06-12"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 2
    assert tally.collapsed == 0


#: (negated, positive) contraction pairs — the reviewer's P1 repro. Each is
#: identity-less so the content-fold gate is actually exercised.
_NEGATION_PAIRS = [
    ("I can't attend the meeting.", "I can attend the meeting."),
    ("I isn't happy.", "I is happy."),
    ("It wasn't there.", "It was there."),
    ("She doesn't like it.", "She does like it."),
]


@pytest.mark.parametrize(("negated", "positive"), _NEGATION_PAIRS)
def test_contracted_negation_is_not_a_restatement(negated, positive):
    """Regression (P1): the negation gate must read the RAW content. The
    event-identity key strips every non-alnum char, so it normalizes "can't"
    to "can t"; ``fold_allowed`` finds the negator through the clitic SHAPE
    ``n[^\\w\\s]{1,2}t$`` (which needs the apostrophe), so gating on the
    normalized key makes the n't invisible and folds the negated claim into
    its positive form — an UNDERCOUNT. Fails on the pre-fix code for all four
    contractions (n_events == 1, reviewer repro)."""
    events = [
        _event(negated, "2025-06-01"),
        _event(positive, "2025-06-12"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 2
    assert tally.collapsed == 0
    # The two keys are genuinely distinct — not a single folded cluster.
    assert len(set(tally.keys)) == 2


def test_punctuation_collision_is_not_a_restatement():
    """Regression (P2): the identity key strips every non-alnum char, so two
    DIFFERENT claims can share a key — "I ran 3.5 hours." and "I ran 3-5
    hours." both key to "i ran 3 5 hours". The equality branch must still
    pass the committed fold gate, or the pair folds into one and UNDERCOUNTS
    distinct events (the failure this core exists to prevent). The gate is
    reflexive, so genuinely identical text must still fold — asserted here by
    the repeated row."""
    events = [
        _event("I ran 3.5 hours.", "2025-06-01"),
        _event("I ran 3-5 hours.", "2025-06-12"),
        _event("I ran 3.5 hours.", "2025-06-20"),
    ]
    tally = count_distinct_events(events)
    assert tally.n_events == 2        # the two 3.5 rows fold; 3-5 stays apart
    assert tally.collapsed == 1
    assert len(tally.keys) == 2       # two clusters, equal key strings aside


def test_tally_is_input_order_independent():
    events = [
        _event("We decided to go to Tokyo.", "2025-06-01"),
        _event("We decided to go to Tokyo.", "2025-06-12"),
        _event("I bought a road bike.", "2025-06-05", "c"),
    ]
    forward = count_distinct_events(events)
    backward = count_distinct_events(list(reversed(events)))
    assert forward.n_events == backward.n_events == 2
    assert set(forward.keys) == set(backward.keys)


def test_same_date_paraphrases_are_input_order_independent():
    """Regression (P1): same-date identity-less rows must not tie-break on
    input index, and restatements must cluster by the transitive closure of
    the fold relation (union-find), not a greedy sequential scan. The
    reviewer's repro gave 2 forward vs 3 reversed. Fails on the pre-fix
    code, whose canonical key fell back to the input index and whose greedy
    fold compared each row only against already-kept priors."""
    texts = [
        "mu eta beta kappa iota delta",
        "beta theta lambda mu iota delta epsilon zeta",
        "delta lambda iota beta mu epsilon",
        "beta lambda delta iota epsilon theta",
    ]
    dated = [{"content": t, "session_date": "2025-06-01"} for t in texts]
    undated = [{"content": t} for t in texts]
    for label, events in (("dated", dated), ("undated", undated)):
        forward = count_distinct_events(events)
        backward = count_distinct_events(list(reversed(events)))
        assert (forward.n_events, forward.keys, forward.total) == \
            (backward.n_events, backward.keys, backward.total), label
        # Pin the cluster too, not merely the equality: deterministic and
        # equals the reviewer-confirmed converged value (2, not 3).
        assert forward.n_events == 2, label
        assert backward.n_events == 2, label


def test_same_date_total_is_input_order_independent():
    """Regression (P1, TOTAL path): two same-date paraphrase rows with
    different spans must sum the SAME representative span regardless of
    input order. Fails on the pre-fix code, which kept whichever row arrived
    first (total 2 forward vs 7 reversed here)."""
    events = [
        {"content": "alpha beta gamma delta",
         "session_date": "2025-06-01",
         "start_date": "2025-01-01", "end_date": "2025-01-03"},
        {"content": "alpha beta gamma",
         "session_date": "2025-06-01",
         "start_date": "2025-01-01", "end_date": "2025-01-08"},
    ]
    forward = count_distinct_events(events, unit="days", total=True)
    backward = count_distinct_events(
        list(reversed(events)), unit="days", total=True)
    assert forward == backward
    assert forward.n_events == 1
    assert forward.total == 7


def test_tally_empty_input_is_a_real_zero():
    """The TALLY layer reports a real zero for empty input; the RESOLVER
    abstains instead (``no_events``) — see
    ``test_resolve_count_empty_events_abstains``."""
    tally = count_distinct_events([])
    assert isinstance(tally, EventTally)
    assert tally.n_events == 0
    assert tally.n_input == 0
    assert tally.collapsed == 0
    assert tally.reason is None


def test_tally_capped_abstains(monkeypatch):
    import tortoise.temporal_aggregation as ta
    monkeypatch.setattr(ta, "MAX_EVENTS", 2)
    tally = count_distinct_events([_event("a", "2025-01-01", "1"),
                                   _event("b", "2025-01-02", "2"),
                                   _event("c", "2025-01-03", "3")])
    assert tally.reason == "capped"
    assert tally.n_events == 0


def test_tally_total_sums_distinct_spans():
    """TOTAL sums each DISTINCT event's span; a restated event's span is
    added once (the trap closed for sums too)."""
    events = [
        {"content": "Reading The Nightingale.",
         "start_date": "2025-01-01", "end_date": "2025-01-15"},
        {"content": "Reading The Nightingale.",
         "start_date": "2025-01-01", "end_date": "2025-01-15"},
        {"id": "r3", "content": "Listening to Sapiens.",
         "start_date": "2025-01-16", "end_date": "2025-01-23"},
    ]
    tally = count_distinct_events(events, unit="weeks", total=True)
    assert tally.n_events == 2
    # 14 days + 7 days = 21 days = 3 completed weeks
    assert tally.total == 3
    assert tally.unit == "weeks"


def test_tally_total_undated_event_counts_but_adds_nothing():
    events = [
        {"id": "a", "content": "started reading",
         "start_date": "2025-01-01", "end_date": "2025-01-11"},
        {"id": "b", "content": "no dates here"},
    ]
    tally = count_distinct_events(events, unit="days", total=True)
    assert tally.n_events == 2
    assert tally.total == 10


def test_tally_total_months_abstains_not_raises():
    """Regression (P2): a ``total=True`` request in a unit that cannot be
    inverted from a day sum (months) ABSTAINS with the module's no-value
    result instead of raising. Fails on the pre-fix code, which raised
    ``ValueError``."""
    events = [
        {"id": "r1", "content": "Reading A.",
         "start_date": "2025-01-01", "end_date": "2025-01-15"},
    ]
    tally = count_distinct_events(events, unit="months", total=True)
    assert isinstance(tally, EventTally)
    assert tally.reason == "no_unit"
    assert tally.total is None
    assert tally.n_events == 0


def test_tally_total_ignores_a_reversed_span():
    """Regression (P2): an event whose end precedes its start is a data
    inconsistency, not a measurement. It must contribute NOTHING (exactly as
    an unparsable span does) while still counting once — never publish a
    negative duration as the measured total."""
    events = [
        {"id": "ok", "content": "Reading A.",
         "start_date": "2025-01-01", "end_date": "2025-01-11"},
        {"id": "rev", "content": "Reading B.",
         "start_date": "2025-05-01", "end_date": "2025-04-01"},
    ]
    tally = count_distinct_events(events, unit="days", total=True)
    assert tally.n_events == 2
    assert tally.total == 10          # 10 + 0, not 10 + (-30)
    assert tally.reason is None


def test_tied_key_total_is_input_order_independent():
    """Regression (P1): the canonical order key must be TOTAL for the TOTAL
    path. Two rows tying on (session_date, id, content) but differing in span
    are ONE cluster and its span is read off the cluster's earliest member —
    so if the span bounds were absent from the key, the published total would
    flip with the caller's input order (14 forward, 0 reversed pre-fix). The
    twin carrying no dates must never win the cluster."""
    dated = {"content": "Jogging.", "session_date": "2025-06-01",
             "start_date": "2025-05-25", "end_date": "2025-06-08"}
    undated = {"content": "Jogging.", "session_date": "2025-06-01"}
    forward = count_distinct_events([dated, undated], unit="days", total=True)
    backward = count_distinct_events([undated, dated], unit="days", total=True)
    assert forward == backward
    assert forward.n_events == 1
    assert forward.total == 14


def test_elapsed_marker_beats_the_total_marker():
    """Regression (P2): a question carrying BOTH a summed-span marker and an
    elapsed-time marker is the elapsed INTERVAL — the caller's anchors are
    authoritative, not the sum of event spans. Fails on the pre-fix code,
    which classified this TOTAL and ignored start/end."""
    q = "How many weeks in total have passed since I started jogging?"
    intent = classify_temporal_aggregate(q)
    assert intent.kind is TemporalAggregateKind.INTERVAL
    res = resolve_temporal_aggregate(
        q, start="2025-01-01", end="2025-06-01")
    assert res.value == difference_in_unit("2025-01-01", "2025-06-01", "weeks")
    assert res.reason is None
    # The suppression must not be a blanket word-match:
    #   * "between" as a LIST is a genuine sum, not an interval;
    #   * a LEADING elapsed clause must land on INTERVAL, never on None (None
    #     reads as "not temporal" and would file the row as unclassified).
    listed = ("How many days in total did I travel between New York, "
              "Boston and DC?")
    assert (classify_temporal_aggregate(listed).kind
            is TemporalAggregateKind.TOTAL)
    for leading in ("Since I started jogging, how many weeks in total have "
                    "passed?",
                    "Since 2020, how many days in total have I spent on "
                    "this?"):
        assert (classify_temporal_aggregate(leading).kind
                is TemporalAggregateKind.INTERVAL), leading


def test_abstained_tally_counters_are_unset(monkeypatch):
    """The EventTally contract: ``collapsed == n_input - n_events`` holds for
    a real tally, while on ABSTENTION the counters are UNSET (0) and only
    ``reason`` is meaningful — an abstained tally is never a real zero."""
    import tortoise.temporal_aggregation as ta
    monkeypatch.setattr(ta, "MAX_EVENTS", 2)
    capped = count_distinct_events([_event("a", "2025-01-01", "1"),
                                    _event("b", "2025-01-02", "2"),
                                    _event("c", "2025-01-03", "3")])
    assert capped.reason == "capped"
    assert (capped.n_events, capped.collapsed) == (0, 0)
    real = count_distinct_events([_event("a", "2025-01-01"),
                                 _event("a", "2025-01-02")])
    assert real.reason is None
    assert real.n_events == 1
    assert real.collapsed == real.n_input - real.n_events
    # The OTHER abstention return (months cannot be inverted from a day sum)
    # must satisfy the same contract — a regression that returned a collapsed
    # of n_input there would otherwise pass the whole suite.
    months = count_distinct_events(
        [{"id": "r1", "content": "Reading A.", "start_date": "2025-01-01",
          "end_date": "2025-01-15"}], unit="months", total=True)
    assert months.reason == "no_unit"
    assert (months.n_events, months.collapsed) == (0, 0)


def test_resolve_path_runs_over_all_12_census_qids():
    """The acceptance's "named aggregation path exercised against the 12
    census qids": every class member classifies to a date-arithmetic shape
    AND the RESOLUTION path abstains with no admitted anchors/events — it
    never guesses a number. (Exercising the classifier alone would not
    exercise the named path.)"""
    rows = [r for r in _CENSUS["rows"] if r.get("cls") == "frequency/count"]
    assert len(rows) == 12
    for row in rows:
        res = resolve_temporal_aggregate(row["question"])
        assert res.kind is not None, row["qid"]
        assert res.value is None, (row["qid"], res.value)
        assert res.reason in ("no_anchors", "no_events"), (row["qid"],
                                                           res.reason)


# ── (d) calendar difference ───────────────────────────────────────────────

def test_difference_days_weeks():
    assert difference_in_unit("2025-01-01", "2025-01-31", "days") == 30
    assert difference_in_unit("2025-01-01", "2025-01-31", "weeks") == 4
    # signed (end - start)
    assert difference_in_unit("2025-01-31", "2025-01-01", "days") == -30


def test_difference_months_completed():
    assert difference_in_unit("2025-01-15", "2025-03-14", "months") == 1
    assert difference_in_unit("2025-01-15", "2025-03-15", "months") == 2
    assert difference_in_unit("2025-01-15", "2025-01-15", "months") == 0


def test_difference_years_completed():
    assert difference_in_unit("2020-06-01", "2023-05-31", "years") == 2
    assert difference_in_unit("2020-06-01", "2023-06-01", "years") == 3


def test_difference_missing_or_garbage_is_none():
    assert difference_in_unit(None, "2025-01-01", "days") is None
    assert difference_in_unit("2025-01-01", None, "days") is None
    assert difference_in_unit("garbage", "2025-01-01", "days") is None


def test_difference_unknown_unit_raises():
    with pytest.raises(ValueError):
        difference_in_unit("2025-01-01", "2025-01-02", "fortnights")


def test_as_date_sentinel_and_iso():
    from datetime import date
    assert as_date("2025-06-10") == date(2025, 6, 10)
    assert as_date("2025-06-10T23:30:00Z") == date(2025, 6, 10)
    assert as_date("1969-12-31") is None   # pre-1970 sentinel
    assert as_date("") is None
    assert as_date(None) is None
    assert as_date("not-a-date") is None


# ── (e) the one-call resolution seam ──────────────────────────────────────

def test_resolve_count_distinct():
    events = [
        _event("We decided to go to Tokyo.", "2025-06-01"),
        _event("We decided to go to Tokyo.", "2025-06-12"),
        _event("We picked November for the trip.", "2025-06-20", "c"),
    ]
    res = resolve_temporal_aggregate(
        "How many times did we decide on Tokyo?", events=events)
    assert res.kind is TemporalAggregateKind.COUNT
    assert res.value == 2
    assert res.method == "count_distinct"
    assert res.n_events == 2
    assert res.reason is None


def test_resolve_counts_repeated_occurrences_with_distinct_ids():
    """Regression (P1 end-to-end): the reviewer's repro. Three explicit-id
    events with identical content on different dates are three occurrences;
    the count must be 3, not 1. Fails on the pre-fix code (value == 1)."""
    events = [
        _event("Went to the gym.", "2025-01-01", "e1"),
        _event("Went to the gym.", "2025-02-01", "e2"),
        _event("Went to the gym.", "2025-03-01", "e3"),
    ]
    res = resolve_temporal_aggregate(
        "How many times did I go to the gym?", events=events)
    assert res.kind is TemporalAggregateKind.COUNT
    assert res.n_events == 3
    assert res.value == 3
    assert res.reason is None


def test_resolve_count_same_date_paraphrases_is_input_order_independent():
    """Regression (P1 end-to-end): the resolver's COUNT value over a
    same-date paraphrase set is identical forward vs reversed. Fails on the
    pre-fix code (value 2 forward vs 3 reversed)."""
    texts = [
        "mu eta beta kappa iota delta",
        "beta theta lambda mu iota delta epsilon zeta",
        "delta lambda iota beta mu epsilon",
        "beta lambda delta iota epsilon theta",
    ]
    events = [{"content": t, "session_date": "2025-06-01"} for t in texts]
    question = "How many times did I do the thing?"
    forward = resolve_temporal_aggregate(question, events=events)
    backward = resolve_temporal_aggregate(
        question, events=list(reversed(events)))
    assert forward.value == backward.value == 2
    assert forward.n_events == backward.n_events == 2


def test_resolve_count_empty_events_abstains():
    """Regression (P2): an EMPTY admitted-event set is not a measured zero.
    COUNT must abstain (never guess 0). Fails on the pre-fix code, which
    returned value=0, reason=None, method='count_distinct'."""
    res = resolve_temporal_aggregate(
        "How many times did I go to the gym?", events=[])
    assert res.kind is TemporalAggregateKind.COUNT
    assert res.value is None
    assert res.n_events is None
    assert res.method is None
    assert res.reason == "no_events"


def test_resolve_total_empty_events_abstains():
    """Regression (P2, TOTAL path): an empty set abstains rather than
    publishing a 0 span. Fails on the pre-fix code (value=0)."""
    res = resolve_temporal_aggregate(
        "How many weeks in total did I spend reading?", events=[])
    assert res.kind is TemporalAggregateKind.TOTAL
    assert res.value is None
    assert res.n_events is None
    assert res.reason == "no_events"


def test_resolve_interval_difference():
    res = resolve_temporal_aggregate(
        "How many weeks had passed since I recovered when I went jogging?",
        start="2025-02-01", end="2025-02-22")
    assert res.kind is TemporalAggregateKind.INTERVAL
    assert res.value == 3
    assert res.method == "difference"
    assert res.unit == "weeks"


def test_resolve_before_offset_difference():
    res = resolve_temporal_aggregate(
        "How many days before I bought the iPad did I attend the market?",
        start="2025-03-01", end="2025-03-10")
    assert res.kind is TemporalAggregateKind.BEFORE_OFFSET
    assert res.value == 9
    assert res.unit == "days"


def test_resolve_per_question_unit_override():
    """A caller that knows the answer unit (the eval's per-question unit) is
    authoritative over the classifier's unit."""
    res = resolve_temporal_aggregate(
        "How many days did it take me to finish?", start="2025-01-01",
        end="2025-01-31", unit="months")
    assert res.value == 0
    assert res.unit == "months"


def test_resolve_abstains_without_anchors():
    """Never guess: a difference question with no anchors abstains and the
    reader lane keeps the case."""
    res = resolve_temporal_aggregate(
        "How many days before I bought the iPad did I attend the market?")
    assert res.value is None
    assert res.reason == "no_anchors"
    assert res.method is None


def test_resolve_abstains_without_unit():
    res = resolve_temporal_aggregate("How long did the flight take?")
    assert res.value is None
    assert res.reason == "no_unit"
    assert res.kind is TemporalAggregateKind.DURATION


def test_resolve_non_temporal_is_explicit():
    res = resolve_temporal_aggregate("What did I eat for dinner?")
    assert res.kind is None
    assert res.reason == "not_temporal"
    assert res.value is None


def test_resolve_total_sum():
    events = [
        {"id": "r1", "content": "Reading The Nightingale.",
         "start_date": "2025-01-01", "end_date": "2025-01-15"},
        {"id": "r2", "content": "Listening to Sapiens.",
         "start_date": "2025-01-16", "end_date": "2025-01-23"},
    ]
    res = resolve_temporal_aggregate(
        "How many weeks in total do I spend on reading and listening?",
        events=events)
    assert res.kind is TemporalAggregateKind.TOTAL
    assert res.n_events == 2
    assert res.value == 3
    assert res.unit == "weeks"


def test_resolve_total_in_months_abstains_not_raises():
    """TOTAL-in-calendar-months cannot be inverted from a day sum — the
    seam abstains (never raises, never approximates)."""
    events = [
        {"id": "r1", "content": "Reading A.",
         "start_date": "2025-01-01", "end_date": "2025-01-15"},
    ]
    res = resolve_temporal_aggregate(
        "How many months in total did I spend reading?", events=events)
    assert res.value is None
    assert res.reason == "no_unit"


def test_resolve_capped_abstains_with_no_value():
    """A capped tally abstains — never a truncated count reported as 0."""
    import tortoise.temporal_aggregation as ta
    events = [{"id": f"{i}", "content": f"event {i}"}
              for i in range(3)]
    original = ta.MAX_EVENTS
    ta.MAX_EVENTS = 2
    try:
        res = resolve_temporal_aggregate(
            "How many times did things happen?", events=events)
        assert res.value is None
        assert res.n_events is None
        assert res.reason == "capped"
    finally:
        ta.MAX_EVENTS = original


def test_resolution_is_deterministic():
    events = [
        _event("We decided to go to Tokyo.", "2025-06-01"),
        _event("We decided to go to Tokyo.", "2025-06-12"),
    ]
    q = "How many times did we decide on Tokyo?"
    first = resolve_temporal_aggregate(q, events=events)
    second = resolve_temporal_aggregate(q, events=list(reversed(events)))
    assert first.value == second.value == 1
