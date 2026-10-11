"""#2165 Task 2 — pure shape classifier + subject-term extraction (R13/R15/R16).

Pure (no DB, no FTS, no model) RED→GREEN spec for ``tortoise/assembly.py``.
The fired decision is owned by high-precision ordered regexes over question
text. Shapes with MEASURED census support: current-state / ordering /
interval (docs/plans/2026-09-08-2165-connected-assembly.md Task 2 + the
Status—census block). Everything else falls through to the legacy lane.

MEASURED CONTRACT (pinned, not aspirational — recomputed against
tests/_assembly_census.json, the committed 133-Q temporal taxonomy):

* In-scope census rows that fire: 50/55. The 5 non-fires are duration /
  count / single-anchor rows the SEMANTIC census filed under in-scope
  classes ("how long did it take", "how many charity events … before",
  "how long have I been working before") — no shape template must fire on
  them; the classifier's shape-based rejection is the point.
* Residual measured-negative rows that fire (after the #2652 ago/recency
  promotion, the #2654 duration-state promotion, #2653's nary-ordering
  re-measurement, and #2655's calendar-anchor day lookup): exactly 1
  ADJUDICATED label-noise row below, on a residual set #2655 narrows
  further by re-adjudicating three calendar-anchored rows INTO the fired
  class (``_RELATIVE_DATE_CLASS_EXPANSION``). It IS counted in the
  denominator and is the only permitted fire; see
  ``test_census_negative_set_precision_floor`` for why the 0.95 RATIO
  constant is no longer asserted there while the fire SET — the guard that
  catches every new false fire — is.
  370a8ff4 "How many weeks had passed since I recovered from the flu when
  I went on my 10th jog" — two-anchor interval-since morphology, twin
  interval rows fire. #2653 additionally moved the whole "nary-ordering"
  class out of this set by re-measuring it: it was filed as 4 rows, but the
  committed census stores 140-char question TRUNCATIONS, and only 2 are
  genuine named-subject N-ary ordering questions — both now FIRE as
  ORDERING with 3 terms (``test_census_nary_ordering_remeasured``). The
  other two are gpt4_70e84552_abs, the binary `_abs` twin of an
  ordering/compare row (fired already; now asserted with its class), and
  gpt4_d6585ce8 ("the concerts and musical events I attended"), a
  CLASS-PLURAL enumeration request that names no subjects — a different
  feature, correctly never fired here.
* #2655 CLASS CORRECTION (measured, same evidence standard as the two
  adjudications): the census's ``relative-date-lookup`` label UNDER-COUNTS
  its own class. Of its 6 rows only 3 name a calendar anchor; 3 further rows
  filed under OTHER classes (``recency/current-state`` ×2, ``other`` ×1) ARE
  plain calendar-anchor lookups ("… last Saturday?", "… last Tuesday?",
  "… last Friday?") and now fire as ``RELATIVE_DATE``. They are re-read by
  MORPHOLOGY and moved OUT of the negative denominator rather than counted
  as precision loss; the class's 3 anchor-less rows stay in (and stay
  non-firing), and the classification is measured separately by
  ``test_census_relative_date_lookup_coverage``.

The adjudications keep the shape router (NOT the census labels) as the
arbiter — mirrored in the module docstring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.assembly import (
    _RE_OR_CHAIN,
    AssemblyShape,
    RelativeAnchor,
    ResolveResult,
    _count_is_fragment,
    classify_question,
    extract_subject_terms,
    resolve_relative_anchor,
    resolve_subjects,
)

_CENSUS = json.loads(
    (Path(__file__).resolve().parent / "_assembly_census.json").read_text())

# the three classes the shape router FIRES (the census's in-scope set)
_IN_SCOPE_CLASSES = {"interval", "ordering/compare", "current-state"}
# the measured-negative classes (the census's non-fireable sub-classes).
# #2653: "nary-ordering" is NOT here any more — the class was re-measured
# and its 2 genuine named-subject rows now FIRE by design (see
# test_census_nary_ordering_remeasured); the other two are a binary twin
# that already fired and a class-plural request that is a different feature.
# ── #2652 census re-measurement ──────────────────────────────────────────
# The R13 reject-guard's ago/recency vocabulary was PROMOTED to the
# AGO_RELATIVE shape (explicit offsets + recency — see
# ``_REL_TIME_CLASSES``), so those two classes are the recall contract, not
# negatives; the negative set for the precision floor is the RESIDUAL
# measured set of the classes below, minus the rows both changes adjudicate
# away (``_PROMOTED_MISFILED`` for #2652, ``_DURATION_STATE_COVERED`` for
# #2654).
# #2654: "duration-state" is still listed here — it IS the census's
# non-fireable class LABEL — but the 5 rows whose morphology the state-entry
# shape now covers are excluded from the set in
# ``test_census_negative_set_precision_floor`` (via
# ``_DURATION_STATE_COVERED``); the class's 2 residual rows stay in it.
# #2655: the ``relative-date-lookup`` LABEL stays in the set (it IS the
# census's non-fireable class), but its 3 calendar-anchored rows are excluded
# by QID (``_RELATIVE_DATE_ANCHORED``) because the day lookup now fires on
# them, and the 3 rows the census misfiled OUT of the class
# (``_RELATIVE_DATE_CLASS_EXPANSION``) are re-adjudicated INTO it — see
# ``test_census_negative_set_precision_floor``.
_NEG_CLASSES = {
    "frequency/count", "duration-state", "relative-date-lookup",
    "other", "pattern/recurring", "offset-comparison",
    "recency/current-state",
}
# the census classes #2652 promotes to a FIRING shape (recall contract)
_REL_TIME_CLASSES = {"ago-relative", "recency"}
#: census rows FILED under a residual class that are MORPHOLOGICALLY part of
#: the promoted family (a mis-file). They are removed from the negative set
#: and asserted as promotions instead — scoring a known positive as a false
#: fire would make the precision figure meaningless.
_PROMOTED_MISFILED = {
    "gpt4_4929293b",          # 'a week ago' — explicit offset, filed
                              # relative-date-lookup
}
# the adjudicated label-noise rows that MAY fire (pinned — do not grow this
# list without a documented census divergence).
# #2653: gpt4_70e84552_abs is NO LONGER here — its census class
# ("nary-ordering") was re-measured out of the negative set, so its fire is
# asserted by ``test_census_nary_ordering_remeasured`` as that class's binary
# twin rather than counted as a false fire in the residual set.
_ADJUDICATED_FIRE = {
    "370a8ff4",               # interval-since twin (filed frequency/count)
}
# the FULL (untruncated) question texts of the census nary-ordering class.
# tests/_assembly_census.json stores 140-char truncations, which is why this
# class was measured as 4 rows that fire nothing: the truncation cuts
# gpt4_f49edff3's third option away, leaving a 2-option tail no shape fires
# on. Re-measured against the dataset, only these 2 are genuine 3+
# named-subject ordering questions.
_NARY_FULL_QUESTIONS = {
    "gpt4_f49edff3": (
        "Which three events happened in the order from first to last: the "
        "day I helped my friend prepare the nursery, the day I helped my "
        "cousin pick out stuff for her baby shower, and the day I ordered "
        "a customized phone case for my friend's birthday?"),
    "gpt4_18c2b244": (
        "What is the order of the three events: 'I signed up for the "
        "rewards program at ShopRite', 'I used a Buy One Get One Free "
        "coupon on Luvs diapers at Walmart', and 'I redeemed $12 cashback "
        "for a $10 Amazon gift card from Ibotta'?"),
}
# #2655: rows the census filed OUTSIDE relative-date-lookup that ARE plain
# calendar-anchor lookups (each names a `last <weekday>` anchor and nothing
# else). Adjudicated INTO the class by morphology — the same rule the two
# entries above use — so they are removed from the negative denominator
# instead of being counted as precision loss.
_RELATIVE_DATE_CLASS_EXPANSION = {
    "gpt4_d6585ce9",  # "Who did I go with to the music event last Saturday?" (filed recency/current-state)
    "gpt4_468eb064",  # "Who did I meet with during the lunch last Tuesday?" (filed recency/current-state)
    "gpt4_fa19884d",  # "What is the artist that I started to listen to last Friday?" (filed other)
}
# the relative-date-lookup rows that name NO calendar anchor (pinned — these
# are the class's out-of-family remainder: one `ago`-offset day owned by #2652
# and two rows with no day at all)
_RELATIVE_DATE_NON_ANCHOR = {
    "gpt4_4929293b",  # "… a week ago?"            (#2652's ago-relative class)
    "gpt4_4edbafa2",  # "… first BBQ event in June?" (no anchor)
    "gpt4_9a159967",  # "… the most in March and April?" (#2886's class)
}
# in-scope rows whose DURATION/COUNT morphology must NOT fire (pinned)
_IN_SCOPE_NO_FIRE = {
    "How many days did it take for me to find a house I loved after "
    "starting to work with Rachel?",
    "How many charity events did I participate in before the 'Run for the "
    "Cure' event?",
    "How long have I been working before I started my current job at "
    "NovaTech?",
    "How long have I been working before I started my current job at "
    "Google?",
    "How long did I use my new binoculars before I saw the American "
    "goldfinches returning to the area?",
}
# #2655: the calendar-anchored rows of the relative-date-lookup class (the
# CLASS LABEL stays in _NEG_CLASSES; these three rows are excluded from its
# denominator because the shape now fires on them). Pinned by qid.
_RELATIVE_DATE_ANCHORED = {
    "71017277",       # "…jewelry last Saturday from whom?"
    "gpt4_f420262d",  # "…airline … on Valentine's day?"
    "gpt4_e414231f",  # "…bike … the past weekend?"
}

# #2654: the duration-state rows the state-entry variant COVERS, pinned by
# qid — a coverage regression on any of them fails loudly.
_DURATION_STATE_COVERED = {
    "e4e14d04",   # "…been a member of 'Book Lovers Unite' when … meetup?"
    "c9f37c46",   # "…been watching stand-up comedy specials regularly …"
    "cc6d1ec1",   # "…been bird watching when … workshop?"
    "993da5e2",   # "…been using the new area rug when … furniture?"
    "b29f3365",   # "…been taking guitar lessons when … amp?"
}
#: #2654: the duration-state rows whose morphology is NOT the state-entry
#: form, pinned as still-non-firing WITH the reason — a template widening
#: onto either of them fails here.
_DURATION_STATE_RESIDUAL = {
    # a SUMMED span over two books ("… and … combined"): the #2886 aggregate
    # lane owns the TOTAL morphology, and no state entry exists to anchor
    "b9cfe692",
    # an AGE ("how old was I when I moved"): the state entry would be a
    # birth date the graph does not hold
    "d01c6aa8",
}


# ── shape table (positive per shape + extraction) ─────────────────────────

@pytest.mark.parametrize("question,expected", [
    # ordering — two named options + "first"
    ("Which event happened first, my cousin's wedding or Michael's "
     "engagement party?", AssemblyShape.ORDERING),
    ("Which device did I get first, the Samsung Galaxy S22 or the Dell XPS "
     "13?", AssemblyShape.ORDERING),
    ("Who became a parent first, Rachel or Alex?", AssemblyShape.ORDERING),
    ("Which vehicle did I take care of first in February, the bike or the "
     "car?", AssemblyShape.ORDERING),
    ("Which book did I finish reading first, 'The Hate U Give' or 'The "
     "Nightingale'?", AssemblyShape.ORDERING),
    ("Which seeds were started first, the tomatoes or the marigolds?",
     AssemblyShape.ORDERING),
    ("Which event happened first, the meeting with Rachel or the pride "
     "parade?", AssemblyShape.ORDERING),
    # the fixture-canary compare (dash-delimited "which came first - A or B")
    ("which came first - the couch or the dog bed?",
     AssemblyShape.ORDERING),
    ("which came first - buying the couch or the dog bed getting chewed?",
     AssemblyShape.ORDERING),
])
def test_ordering_fires(question, expected):
    assert classify_question(question) is expected


@pytest.mark.parametrize("question,expected", [
    ("How many days passed between the day I bought my new tennis racket "
     "and the day I received it?", AssemblyShape.INTERVAL),
    ("How many weeks passed between the time I sold homemade baked goods "
     "at the Farmers' Market for the last time and the day I stopped?",
     AssemblyShape.INTERVAL),
    ("How many months passed between the completion of my undergraduate "
     "degree and the submission of my master's thesis?",
     AssemblyShape.INTERVAL),
    ("How many days had passed between the 'Walk for Hunger' event and "
     "the 'Coastal Cleanup' event?", AssemblyShape.INTERVAL),
    ("How many days had passed since I finished reading 'The Seven "
     "Husbands of Evelyn Hugo' when I attended the book club meeting?",
     AssemblyShape.INTERVAL),
    # fixture canary: bare "between" (no "passed")
    ("how many days between buying the couch and selling the couch?",
     AssemblyShape.INTERVAL),
    # "best friend" must NOT trip an advice guard (regression: review r2)
    ("How many days had passed between the day I bought a gift for my "
     "brother's graduation ceremony and the day I bought a birthday gift "
     "for my best friend?", AssemblyShape.INTERVAL),
    # natural rephrasings the eval corpus uses (review r2 P2)
    ("How many days were there between buying the couch and selling the "
     "couch?", AssemblyShape.INTERVAL),
    ("Between buying the couch and selling it, how many days passed?",
     AssemblyShape.INTERVAL),
    ("How much time passed between buying the couch and selling it?",
     AssemblyShape.INTERVAL),
    ("How long passed between buying the couch and selling it?",
     AssemblyShape.INTERVAL),
    # interval anchors containing "before" must survive the frequency guard
    ("How many days passed between the day before Christmas and New Year's "
     "Day?", AssemblyShape.INTERVAL),
    # past-ability/habitual "could"/"would" are NARRATIVE, not advice
    ("How many days passed between the day I could walk again and the "
     "day I ran my first 5K?", AssemblyShape.INTERVAL),
    ("How many days passed between the day I started playing along to my "
     "favorite songs on my old keyboard and the day I bought a new one?",
     AssemblyShape.INTERVAL),
])
def test_interval_fires(question, expected):
    assert classify_question(question) is expected


@pytest.mark.parametrize("question,expected", [
    ("what is the current status of the couch?", AssemblyShape.CURRENT_STATE),
    ("What is the current state of the sofa?", AssemblyShape.CURRENT_STATE),
    ("is the couch still live?", AssemblyShape.CURRENT_STATE),
])
def test_current_state_fires(question, expected):
    assert classify_question(question) is expected


# ── #2655 relative-date-lookup: fired coverage of the census class ─────────

def test_census_relative_date_lookup_coverage():
    """#2655 measured target: 3 of the 6 ``relative-date-lookup`` census rows
    fire (from 0/6 at HEAD). The other 3 name NO calendar anchor — one is an
    ``ago``-offset day (#2652 now routes it to ``AGO_RELATIVE``, a different
    shape), one is ordering-within-a-month, one is frequency/count (#2886's
    class) — and are never THIS shape by design.

    The census's label is preserved verbatim here (this test measures the
    class as filed); the classifier's own class correction over the misfiled
    rows is pinned separately in
    ``test_census_negative_set_precision_floor``.
    """
    rows = [r for r in _CENSUS["rows"] if r["cls"] == "relative-date-lookup"]
    assert len(rows) == 6
    fired = {r["qid"] for r in rows
             if classify_question(r["question"])
             is AssemblyShape.RELATIVE_DATE}
    assert fired == {"71017277", "gpt4_f420262d", "gpt4_e414231f"}, fired
    assert {r["qid"] for r in rows} - fired == _RELATIVE_DATE_NON_ANCHOR
    # the anchor-less remainder is never a DAY lookup — #2652's ago-offset
    # row fires, but as AGO_RELATIVE, not as this shape
    for r in rows:
        if r["qid"] not in fired:
            assert classify_question(r["question"]) \
                is not AssemblyShape.RELATIVE_DATE


@pytest.mark.parametrize("question", [
    # the three fireable census rows (verbatim)
    "I received a piece of jewelry last Saturday from whom?",
    "What was the airline that I flied with on Valentine's day?",
    "Which bike did I fixed or serviced the past weekend?",
    # the weekday-family surfaces
    "What did I do last Saturday?",
    "What did I do this past Tuesday?",
    "What did I do past Friday?",
    "Who did I meet with during the lunch last Tuesday?",
    # fixed dates and the weekend
    "What did I do on Christmas?",
    "What did I do on Christmas eve?",
    "What did I do on New Year's day?",
    "What did I do on Halloween?",
    "What did I do on the fourth of july?",
    "Which bike did I service the past weekend?",
    "What did I do last weekend?",
])
def test_relative_date_lookup_fires(question):
    assert classify_question(question) is AssemblyShape.RELATIVE_DATE
    # the day is the SCOPE, not a subject — extraction must never hand the
    # resolver a fragment of the question (it would fabricate a subject)
    assert extract_subject_terms(question) == []


@pytest.mark.parametrize("question", [
    # two distinct anchors is an interval question, not a day lookup
    "What did I do between Christmas and New Year's day?",
    "What did I do last Saturday and last Sunday?",
    # no anchor at all
    "What did I do yesterday?",
    "What did I do two weeks ago?",
    "Which airline did I fly with the most in March and April?",
    "What was the date on which I attended the first BBQ event in June?",
    "When did I buy the couch?",
    # a competing shape owns the question (unchanged behaviour — the anchor
    # branch falls through to the original pipeline, which is exactly what
    # classified these before #2655)
    "Which came first, buying the couch two months ago or selling the dog "
    "bed last Saturday?",
    # an anchor + a duration/frequency/advice morphology on the residual
    "How long did I use my new binoculars before I saw the goldfinches "
    "last Saturday?",
    "How many times did I go to the gym last Saturday?",
    "Which should I do first, the couch or the dog bed on Valentine's "
    "day?",
    "What was the most recent thing I did last Saturday?",
])
def test_relative_date_lookup_does_not_fire(question):
    assert classify_question(question) is not AssemblyShape.RELATIVE_DATE, \
        question
    assert extract_subject_terms(question) == []


@pytest.mark.parametrize("question", [
    # a bare `ago` the promoted vocabulary does NOT own (hours / a long
    # time / centuries) co-present with a holiday anchor
    "What did I buy two hours ago at the Christmas market?",
    "What did I do last Saturday a long time ago?",
    "What did I do last weekend centuries ago?",
])
def test_relative_date_lookup_rejects_bare_ago_residue(question):
    """#2655 merge-regression pin (P1): the pre-merge branch rejected these
    with ``_RE_AGO.search(residual)``. #2652 split that guard into
    ``_RE_AGO_OTHER`` / the promoted vocabulary / ``_RE_AGO_RESIDUAL``
    (``\\bago\\b``), and the merge resolution kept only the first two — so
    the day lookup fired on a question whose real window is the bare ``ago``
    (``two hours``), answered by an equality-day lookup on the holiday.
    Both parents returned ``None``; ``_date_lookup_surface`` must reject.

    Mutation: drop ``or _RE_AGO_RESIDUAL.search(residual)`` from
    ``_date_lookup_surface`` -> all three fire as RELATIVE_DATE.
    """
    assert classify_question(question) is not AssemblyShape.RELATIVE_DATE
    assert classify_question(question) is None


@pytest.mark.parametrize("question,expected", [
    # a question whose anchors are the ARGUMENTS of another shape keeps that
    # shape — the anchor branch must never take it over
    ("How many days passed between Christmas and my birthday?",
     AssemblyShape.INTERVAL),
    ("How many days had passed since Christmas when I bought the couch?",
     AssemblyShape.INTERVAL),
    ("Which event happened first, my wedding or the parade on Valentine's "
     "day?", AssemblyShape.ORDERING),
    # a state probe with a holiday adjunct (no ago-surface anchor) keeps
    # CURRENT_STATE, exactly as before #2655
    ("what is the current status of the couch on Christmas?",
     AssemblyShape.CURRENT_STATE),
])
def test_relative_date_lookup_does_not_hijack_another_shape(question,
                                                           expected):
    assert classify_question(question) is expected, question


# ── measured precision floor on the census negative set (R15/R16) ─────────

def test_census_negative_set_precision_floor():
    """Precision on the RESIDUAL measured negative set ≥ 0.95 — the
    #2652 + #2653 + #2654 + #2655 merged re-measurement.

    ALL FOUR changes shrink the negative denominator: #2653 removed the 4
    nary-ordering rows it re-measures (``test_census_nary_ordering_remeasured``),
    #2652 removed the 34 rows it promotes (``_REL_TIME_CLASSES`` ago-relative 31
    + recency 2, plus the morphologically-promoted mis-file
    ``_PROMOTED_MISFILED``), #2654 removed the 5 duration-state rows the
    state-entry shape now fires on (``_DURATION_STATE_COVERED``, pinned by
    ``test_census_duration_state_coverage``), and #2655 removed the 3
    calendar-anchored ``relative-date-lookup`` rows the day lookup now fires
    on (``_RELATIVE_DATE_ANCHORED``). What is left is the residual set
    asserted below; the class's 2 residual-morphology duration-state rows
    stay in and stay non-firing.

    #2655 also ADJUDICATES IN 3 rows the census filed OUTSIDE the class —
    plain ``last <weekday>`` lookups filed under recency/current-state ×2 and
    other ×1 (``_RELATIVE_DATE_CLASS_EXPANSION``). They are re-read by
    MORPHOLOGY, asserted to fire as RELATIVE_DATE, and leave the denominator
    — the same rule the #2165 adjudication below uses.

    The 1 remaining row #2165 Task 2 adjudicated as LABEL NOISE
    (``_ADJUDICATED_FIRE``) is counted in the denominator and stays the ONLY
    permitted fire: it fires as a DIFFERENT shape than the class the census
    filed it under, so it is a declared positive, not a classifier error.
    The guard that catches a regression is the SET assertion — no fire
    outside the adjudicated row — plus the equality below; both are
    denominator-invariant.

    ⚠ The 0.95 RATIO constant is deliberately NOT asserted here. It is a
    function of the denominator, and all four changes removed NON-firing
    rows from it while the 1 fixed adjudicated fire stays: 1 of ~30 rows is
    still ≥ 0.95, but the ratio is not the guard. Every regression the ratio
    could catch is a NEW fire, which the set assertion catches first —
    re-expressing that as a ratio would only restate it.

    Re-measured against ``tests/_assembly_census.json``. The #2165 figure
    (76/78 rejected) is NOT comparable: 34 of those 78 rows are now
    deliberate positives (78 − 34 − the 5 #2654 state-entry rows − the 4
    #2653 nary-ordering rows − the 3 #2655 calendar-anchored rows = the 32
    asserted below).
    """
    rows = [r for r in _CENSUS["rows"]
            if r["cls"] in _NEG_CLASSES
            and r["qid"] not in _PROMOTED_MISFILED
            and r["qid"] not in _DURATION_STATE_COVERED
            and r["qid"] not in _RELATIVE_DATE_ANCHORED]
    assert len(rows) == 32
    false_fires = [
        r for r in rows
        if classify_question(r["question"]) is not None]
    # #2655: the calendar-anchor rows the census misfiled are adjudicated INTO
    # their real class (all three fire as RELATIVE_DATE) and leave the
    # denominator; what remains is the unchanged in-shape-twin floor.
    reclassified = [r for r in false_fires
                    if r["qid"] in _RELATIVE_DATE_CLASS_EXPANSION]
    assert {r["qid"] for r in reclassified} == _RELATIVE_DATE_CLASS_EXPANSION
    assert all(classify_question(r["question"])
               is AssemblyShape.RELATIVE_DATE for r in reclassified)
    remaining = [r for r in false_fires
                 if r["qid"] not in _RELATIVE_DATE_CLASS_EXPANSION]
    fired = {r["qid"] for r in remaining}
    assert fired <= _ADJUDICATED_FIRE, \
        f"unexpected false fires: {[(r['qid'], r['cls'], r['question'][:70]) for r in remaining]}"
    assert len(remaining) <= 1, \
        f"precision floor breached: {len(remaining)}/{len(rows)} fired"
    # ... and the adjudicated row is not merely tolerated — a change that
    # stops it firing fails here (the floor is monotonic)
    assert fired == _ADJUDICATED_FIRE, \
        f"adjudicated label-noise fires drifted: {sorted(fired)}"
    # the denominator #2655's re-adjudication leaves (the RATIO is
    # deliberately not asserted — see the docstring)
    denominator = len(rows) - len(reclassified)
    assert denominator == 29


def test_census_relative_time_recall():
    """#2652 recall contract: EVERY promoted census row routes to the
    AGO_RELATIVE shape — ago-relative 31/31 + recency 2/2 + the one
    morphologically-promoted mis-file (measured)."""
    rows = [r for r in _CENSUS["rows"]
            if r["cls"] in _REL_TIME_CLASSES
            or r["qid"] in _PROMOTED_MISFILED]
    assert len(rows) == 34
    misses = [(r["qid"], r["question"][:80]) for r in rows
              if classify_question(r["question"])
              is not AssemblyShape.AGO_RELATIVE]
    assert not misses, f"promoted rows failed to route: {misses}"


def test_census_nary_ordering_remeasured():
    """#2653: the census's nary-ordering class, RE-MEASURED from the
    UNTRUNCATED dataset question text (the committed census stores 140-char
    truncations — see ``_NARY_FULL_QUESTIONS``).

    Measured: 2 of the 4 rows are genuine 3+ named-subject ordering
    questions and now FIRE as ORDERING with exactly 3 extracted terms; one
    is the binary `_abs` twin that already fired; one is a class-plural
    enumeration request that names no subjects and must never fire here.
    """
    by_qid = {r["qid"]: r for r in _CENSUS["rows"]}
    nary_rows = [r for r in _CENSUS["rows"] if r["cls"] == "nary-ordering"]
    assert {r["qid"] for r in nary_rows} == {
        "gpt4_f49edff3", "gpt4_18c2b244", "gpt4_d6585ce8",
        "gpt4_70e84552_abs"}, "census nary-ordering membership drifted"

    # the 2 genuine N-ary rows: fired shape + 3 terms, on the FULL text
    for qid, full in _NARY_FULL_QUESTIONS.items():
        assert classify_question(full) is AssemblyShape.ORDERING, qid
        terms = extract_subject_terms(full, AssemblyShape.ORDERING)
        assert len(terms) == 3, (qid, terms)
        assert all(t.strip() for t in terms), (qid, terms)

    # the class-plural row names NO subjects -> never a shape (a different
    # feature: enumerating every event of a class over a window)
    assert classify_question(by_qid["gpt4_d6585ce8"]["question"]) is None

    # the binary `_abs` twin still fires as the TWO-option form
    twin = by_qid["gpt4_70e84552_abs"]["question"]
    assert classify_question(twin) is AssemblyShape.ORDERING
    assert len(extract_subject_terms(twin, AssemblyShape.ORDERING)) == 2


def test_census_in_scope_recall_and_shape_rejections():
    """In-scope census rows: the 50 shape-typical rows fire; the 5 pinned
    duration/count mis-files do NOT (shape-based rejection is the point).

    The in-scope set is the census's three fired classes (interval /
    ordering-compare / current-state, 55 rows — #2653's nary-ordering class
    is measured separately by test_census_nary_ordering_remeasured)."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] in _IN_SCOPE_CLASSES]
    assert len(rows) == 55
    fired = [r for r in rows if classify_question(r["question"]) is not None]
    no_fire = [r for r in rows if classify_question(r["question"]) is None]
    assert len(fired) == 50, \
        f"in-scope fires drifted: {len(fired)} (expected 50)"
    assert len(no_fire) == 5
    by_text = {r["question"]: r for r in no_fire}
    assert set(by_text) == _IN_SCOPE_NO_FIRE, \
        f"in-scope rejection set drifted: {sorted(set(by_text) ^ _IN_SCOPE_NO_FIRE)}"


def test_census_duration_state_coverage():
    """#2654 census coverage (the issue's target, RE-MEASURED): all 7
    duration-state rows — 5 fire as the state-entry shape with exactly one
    usable term each; the 2 residual-morphology rows stay non-firing (pinned
    by qid). The class is not "fired coverage" by count alone: each fired
    row must also extract a non-empty term, because the shape's R1 gate
    needs one resolved subject to fire at all."""
    rows = {r["qid"]: r for r in _CENSUS["rows"]
            if r["cls"] == "duration-state"}
    assert len(rows) == 7
    fired = {q for q, r in rows.items()
             if classify_question(r["question"])
             is AssemblyShape.DURATION_STATE}
    assert fired == _DURATION_STATE_COVERED, \
        f"duration-state coverage drifted: {sorted(fired)}"
    for q in sorted(fired):
        terms = extract_subject_terms(rows[q]["question"])
        assert len(terms) == 1 and terms[0].strip(), (q, terms)
    residual = {q for q, r in rows.items()
                if classify_question(r["question"]) is None}
    assert residual == set(_DURATION_STATE_RESIDUAL), \
        f"residual duration-state set drifted: {sorted(residual)}"


def test_ago_relative_rejects_compounds_and_the_sibling_vocabulary():
    """#2652 boundary: the relative-offset vocabulary is PROMOTED (see
    ``test_ago_relative_shape_fires``); what still rejects is

    * the R13 COMPOUND pin — a relative offset inside a two-subject
      ordering/interval template (the offset is narrative anchor text
      there, not the question's window), and
    * the vocabulary two live sibling issues own: 'last <weekday>' /
      'the past weekend' (calendar anchors — #2655) and bare 'latest' as
      an ORDERING INSTRUCTION ('from earliest to latest' — #2653);
    * a BARE 'ago' the promoted units do not cover (minutes/hours/centuries,
      'a long time ago') — #2165's reject, narrowed to the residue. Without
      it the question falls through to whatever template matches next, e.g.
      "what is the current status of the couch two hours ago?" would fire
      CURRENT_STATE with the offset embedded in the subject.
    """
    for q in [
        # R13 compound pin (relative offset + interval/ordering template)
        "How many days had passed since I bought the couch two weeks ago "
        "when I sold the dog bed?",
        "Which came first, buying the couch two months ago or selling the "
        "dog bed last Saturday?",
        # un-promoted 'ago' residue (any unit/quantifier #2652 does not own)
        "what is the current status of the couch two hours ago?",
        "What did I do a long time ago?",
        "Which trip did I take three centuries ago?",
        # compound number words — the offset template would otherwise read
        # only the LAST word ('twenty two weeks' → 2) and fire a window ~20
        # units wrong
        "What did I do twenty two weeks ago?",
        "What did I do thirty one days ago?",
        # ... for EVERY separator spelling. A hyphen is a word separator
        # too, so 'twenty-one' presents the same `\b` to the offset template
        # as 'twenty one' — the guard must be spelling-independent (`\W+`),
        # not an allow-list: U+2010/U+2011/U+2212 are non-word characters
        # exactly like ASCII '-', and an allow-list read 'one' from a
        # twenty-one-week question and anchored it at one week.
        "What did I do twenty-one weeks ago?",
        "What did I do twenty‐one weeks ago?",   # U+2010 HYPHEN
        "What did I do twenty‑one weeks ago?",   # U+2011 NB-HYPHEN
        "What did I do twenty−one weeks ago?",   # U+2212 MINUS
        "What did I do twenty–one weeks ago?",   # U+2013 EN DASH
        "What did I do thirty—one days ago?",    # U+2014 EM DASH
        "What did I do thirty, one days ago?",
        "What did I do thirty and one days ago?",
        # a VERBAL RANGE / connector: the connector is word characters, so
        # neither the word nor the numeral branch sees it, and the range's
        # LAST bound is promoted as the count (a 2–5-week question answered
        # from the 5-week window alone). The left bound is a NUMBER, word or
        # digit — a digit-only left bound leaves the class half-open, so the
        # SPELLED forms below are the pin for that fix: they promote if the
        # range's left bound ever reverts to `\d+`.
        "What did I do 2 to 5 weeks ago?",
        "What did I do between 2 and 5 weeks ago?",
        "What did I do 2 or 5 weeks ago?",
        "What did I do 2 through 5 weeks ago?",
        "What did I do two to five weeks ago?",
        "What did I do two or five weeks ago?",
        "What did I do one to three weeks ago?",
        "What did I do two through five weeks ago?",
        "What did I do two until five weeks ago?",
        "What did I do two up to five weeks ago?",
        "What did I do two–five weeks ago?",   # U+2013, no word connector
        # ... and the range's LEFT side is not only a BARE bound: the same
        # class the digit-only left bound left half-open, one level down — a
        # left bound that repeats its unit, a quantifier left, a teen word
        # and a connector outside the first pass's list all promoted the
        # RIGHT bound as the count
        "What did I do from 2 weeks to 3 months ago?",
        "What did I do from one week to three weeks ago?",
        "What did I do 2 weeks or 5 weeks ago?",
        "What did I do 2 til 5 weeks ago?",
        "What did I do 2 upto 5 weeks ago?",
        "What did I do 2 up until 5 weeks ago?",
        "What did I do several to five weeks ago?",
        "What did I do a few to five weeks ago?",
        "What did I do thirteen to five weeks ago?",
        # ... and the connector can be a DASH, which branch 1 cannot reach
        # once the left bound carries its own unit ('2 weeks' is not adjacent
        # to the count) — the dash is the canonical range connector
        "What did I do from 2 weeks – 3 months ago?",   # U+2013
        "What did I do 2 weeks — 5 weeks ago?",         # U+2014
        "What did I do 2 weeks - 5 weeks ago?",         # ASCII
        # ... and a SPELLED place-value/decimal tail is a multi-part number
        # too: the offset template reads the trailing word alone. The zero of
        # 'two point oh five' may be spelled several ways
        "What did I do two point five weeks ago?",
        "What did I do twelve point three days ago?",
        "What did I do two point oh five weeks ago?",
        "What did I do two point zero five weeks ago?",
        "What did I do two point o five weeks ago?",
        # ... the connector may carry an 'up' prefix, in any of its spellings
        "What did I do 2 weeks up till 5 weeks ago?",
        "What did I do 2 weeks up through 5 weeks ago?",
        "What did I do 2 weeks up thru 5 weeks ago?",
        # ... and a BOUND may be a fraction, spelt or glyph
        "What did I do one and a half to five weeks ago?",
        "What did I do two and a half to five weeks ago?",
        "What did I do 1½ to 2 weeks ago?",
        # a multi-part NUMERAL is the same defect for digits: `_RE_AGO_OFFSET`
        # reads `\d+`, so it matches the LAST digit run alone — '2.5 weeks
        # ago' anchored at 5 weeks and '2,000 days ago' at 0 days (a window
        # collapsed onto question_date). A digit followed by a WORD char is
        # not a fragment, so the positive cases below must keep promoting.
        "What did I do 2.5 weeks ago?",
        "What did I do 2,000 days ago?",
        "What did I do 2 000 days ago?",
        "What did I do 1-2 weeks ago?",
        # #2655: a state question with a calendar-anchor adjunct is not a
        # date lookup (the anchor must not act as a shield), and a bare
        # weekday / a declarative line with no wh-lookup is never one either
        "what is the current status of the couch last weekend?",
        "Is the couch still live last Saturday?",
        "I bought the couch last Saturday.",
        "What did I do on Tuesday?",
    ]:
        assert classify_question(q) is None, q
        assert extract_subject_terms(q) == [], q
        assert resolve_relative_anchor(q, "2026-09-10") is None, q


def test_ago_relative_still_reads_a_standalone_digit_count():
    """The multi-part-numeral guard must not eat a count that IS the whole
    number: a digit followed by a WORD character is not a fragment."""
    for q, count in [
        ("I bought 2 things 3 days ago", 3),
        ("on 3rd, 4 days ago", 4),
        ("What did I do 22 weeks ago?", 22),
        ("What did I do 10 days ago?", 10),
    ]:
        a = resolve_relative_anchor(q, "2026-09-10")
        assert a is not None, q
        assert (a.kind, a.count) == ("ago-offset", count), (q, a)


def test_count_fragment_scan_is_linear():
    r"""The count-fragment scan must stay LINEAR on a pathological input.

    The guard it replaced was a regex with an ambiguous nested quantifier
    (`(?:\W+|\s+and\s+)+$`), which backtracked over 2**N partitions:
    measured 3 s at N=24 and never returned beyond. The input is bounded
    (`MAX_ASK_QUESTION_CHARS`) but `run_ask_lane` has no timeout and a HANG
    is not an exception the ask envelope can catch, so it was an
    availability defect. This test does not time anything: it asserts the
    scan terminates and returns the right verdict at the boundary sizes.

    ⚠️ This pins the SCAN only. `classify_question` is super-linear for an
    unrelated reason (`_RE_OR_CHAIN`, pinned separately below), so timing
    the classifier here would assert the wrong thing; what matters is that
    the guard the question reaches cannot hang on its input.

    The question MUST carry an `ago-offset`: the scan is only reached from
    `_relative_match` under `kind == "ago-offset"`, so a probe with no
    offset ('twenty…x') never consults the guard and asserts nothing about
    the ask path. '…x 1 day ago' is the production shape — on the ambiguous
    form `classify_question` took 4.8 s at N=24 and hung at N=200.
    """
    for n in (24, 200, 1900, 2000):
        pad = "twenty" + " " * n + "x"
        # the scan itself, addressed directly: 'x' stops it, so the count
        # in 'twenty   …   x 1 day ago' is the WHOLE number
        assert _count_is_fragment(pad + " ") is False, n
        # ... and through the entry points, where the guard is consulted
        q = pad + " 1 day ago"
        assert classify_question(q) is AssemblyShape.AGO_RELATIVE, n
        assert resolve_relative_anchor(q, "2026-09-10") is not None, n


def test_or_chain_does_not_backtrack():
    r"""`_RE_OR_CHAIN` must not be super-linear on a one-'or' question.

    Its whitespace runs were `\s+` on both sides of a lazy `.+?`, which
    overlap on whitespace, so a question with ONE 'or' and long space runs
    retried every (start, split, end) triple before failing: 307 ms at
    len 204 and ~4.8 s at len 404 for 'a' + ' '*200 + 'or' + ' '*200 + 'b'
    (O(n^3.8)). It runs unconditionally in `classify_question` on the ask
    path, which has no timeout, so this is the same availability defect the
    ago guard was rewritten to remove — and it is the DOMINANT cost of the
    classifier once the guard is linear.

    The assertion is on the MATCH VERDICT at the boundary sizes, not on a
    duration: under the `\s+` form this does not fail, it HANGS.
    """
    for n in (200, 500, 1000, 2000):
        # one 'or', so there is NO chain — and the verdict must be False
        assert _RE_OR_CHAIN.search(
            "a" + " " * (n // 2) + "or" + " " * (n // 2) + "b") is None, n
    # a real chain is still detected, both compactly and across padding
    assert _RE_OR_CHAIN.search("A or B or C") is not None
    assert _RE_OR_CHAIN.search("A or" + " " * 500 + "B or C") is not None


def test_count_fragment_scan_stops_at_a_sentence_word():
    """The scan's stopping rule is what makes a legitimate standalone count
    promote: it crosses CONNECTIVE tokens and reports a whole number at the
    first plain SENTENCE word, because nothing beyond that word can be part
    of this number."""
    # (text before the count, is it a fragment?). The `to ` row is the key
    # one a single-token lookbehind gets WRONG: 'to' is a connective, but
    # there is no number behind it, so the count is whole.
    for prefix, fragment in [
        ("I bought 2 things ", False),
        ("on the 3rd of May, ", False),
        ("I went to the store ", False),
        ("I mentioned it to my friend ", False),
        ("", False),
        ("to ", False),              # connective with no number behind it
        ("up to ", False),
        ("2 to ", True),             # range
        ("2 weeks to ", True),
        ("2 weeks up till ", True),
        ("up to and including ", False),
        ("one and a half to ", True),
        ("1½ to ", True),
        ("at 3pm ", False),          # a digit joined to LETTERS is not a number
        ("zero to ", True),          # a spelled zero is a cardinal, not a marker
        ("nought to ", True),
        ("at 4k ", False),
        ("at 5G ", False),
        ("2wks to ", True),          # ... unless the letters are its UNIT
        ("24h to ", True),
        ("2mo to ", True),
        ("3d to ", True),
        ("2 hrs to ", True),          # ... and the SPACED spelling of every
        ("2hrs to ", True),           # one the glued path knows
        ("2hours to ", True),
        ("between 2 hrs and ", True),
        ("2 mins to ", True),
        ("2 hours ", False),         # a duration ENDS before the count: two
        ("2 mins ", False),          # durations, not a range
        ("3 secs ", False),
        ("2 weeks ", False),
        ("2hrs ", False),            # ... and the GLUED duration agrees with
        ("2hours " , False),         # its spaced twin
        ("24h ", False),
        ("2wks ", False),
        ("2 weeks / ", True),        # ... unless a range GLYPH connects them
        ("2 weeks ~ ", True),
        ("2 weeks & ", True),
        ("2 weeks. ", False),        # sentence punctuation is not a connector
        ("2 weeks, ", False),
        ("2 weeks – ", True),
        ("2 hours 5 days ", False),
        # ... and the SAME termination holds across a DEGREE ADVERB: an
        # adverb modifies the count, it does not link it to the earlier
        # duration, so the scan must NOT cross the unit behind it
        ("2 hours about ", False),
        ("2 hours nearly ", False),
        ("2 hours just ", False),
        ("2hrs about ", False),
        ("2 hours ~ about ", True),   # ... unless a GLYPH does link them
        ("between 2 and about ", True),   # a connective CHAIN (already above)
        ("2 to roughly ", True),
        ("from 2 up to and including ", True),
        ("twenty ", True),           # compound number word
        ("twenty-", True),
        ("hundred ", True),          # a bare SCALE word IS a bound
        ("thousand ", True),
        ("dozens of ", True),
        ("two hundred ", True),
        ("2.", True),                # multi-part numeral
        ("2,", True),
        ("two point ", True),        # spelled place value
        ("two point oh ", True),
        ("two point zero ", True),
    ]:
        assert _count_is_fragment(prefix) is fragment, prefix


def test_count_fragment_duration_ends_across_a_degree_adverb():
    """Regression (PR #8097 review, P1): a standalone count preceded by a
    DURATION + degree adverb must still promote.

    The unit-terminates rule used to key on the unit being the FIRST token
    left of the count, so a degree adverb between a duration and the count
    defeated it: the scan crossed the adverb, then the unit, then reached the
    earlier duration's number and reported Fragment=True. That is a false
    reject against the merge-base `5d75752f7` — the count is standalone and
    must route to AGO_RELATIVE, not fall through to the legacy lane.

    The fix keys the termination on whether a RANGE LINKER (connector word or
    glyph) sits between the unit and the count, not on the unit's position.
    On the pre-fix tree the first two assertions fail (`_count_is_fragment`
    reports True; `classify_question` reports None); the control rows below
    hold on both trees and pin that a real range is still rejected.
    """
    # mechanism probe: the duration's number lies behind an adverb, not a
    # connector, so it is a NEW number — the count is whole
    for prefix in ("I ran for 2 hours about ", "I ran for 2 hours nearly ",
                   "I studied for 3 hours just ", "I slept for 8 hours "
                   "exactly ", "I ran for a couple of hours about "):
        assert _count_is_fragment(prefix) is False, prefix
    # ... and through the two entry points the reviewer reproduced on, which
    # must now route to AGO_RELATIVE with the STANDALONE count
    for q, count in [
        ("I ran for 2 hours about 3 days ago", 3),
        ("I ran for a couple of hours about 3 days ago", 3),
        ("I studied for 3 hours nearly 5 days ago", 5),
        ("I trained for 2 hours just 3 days ago", 3),
        ("I slept for 8 hours exactly 3 days ago", 3),
        # controls: no adverb, or a bare adverb with no preceding duration
        ("I ran for 2 hours 3 days ago", 3),
        ("I ran about 3 days ago", 3),
    ]:
        assert classify_question(q) is AssemblyShape.AGO_RELATIVE, q
        anchor = resolve_relative_anchor(q, "2026-09-10")
        assert anchor is not None, q
        assert (anchor.kind, anchor.count) == ("ago-offset", count), (q, anchor)
    # control: a real RANGE that merely qualifies its right bound is still a
    # fragment, so the adverb does not blanket-disable the guard
    for q in ("between 2 and about 5 weeks ago",
              "2 to roughly 5 weeks ago"):
        assert _count_is_fragment(q[:q.rfind(" 5 weeks ago") + 1]) is True, q
        assert classify_question(q) is not AssemblyShape.AGO_RELATIVE, q


def test_count_fragment_scan_crosses_a_connective_chain():
    """The scan must cross an ARBITRARY run of connectives, not just one
    token: a chain that reaches no number is whole ('up to and including 5'),
    a chain that reaches one is a fragment ('2 up to and including 5').

    A single-token check cannot tell these apart — both have a connective
    immediately before the count — which is the defect this pins.
    """
    for prefix in ("up to ", "up to and including ", "from 2 and about "):
        assert _count_is_fragment(prefix) is ("2" in prefix), prefix


def test_count_fragment_scan_ignores_a_preceding_ordinal():
    """An ordinal is a DATE reference, not a number span: 'on 3rd, 4 days
    ago' must still promote count=4. Same for a token whose digits are joined
    to letters — a time if it ends in 'pm', a label if it ends in 'k'/'G'."""
    for prefix in ("on 3rd, ", "on the 22nd, ", "since the 1st, ",
                   "what did I do at 3pm ", "I raised 4k ",
                   "the 5G "):
        assert _count_is_fragment(prefix) is False, prefix


def test_ago_relative_shape_fires():
    """#2652: explicit relative offsets route to AGO_RELATIVE — including
    the state-morphology form the R13 guard used to kill (the CURRENT_STATE
    shape must not claim *current* state for a past-time question; the
    window shape answers the as-of question instead)."""
    for q in [
        "what is the current status of the couch two weeks ago?",
        "What was the status of the couch two weeks ago?",
        "How many weeks ago did I meet up with my aunt and receive the "
        "crystal chandelier?",
        "How many months ago did I start my current job at Google?",
        "What kitchen appliance did I buy 10 days ago?",
        "I mentioned participating in a sports event two weeks ago. What "
        "was the event?",
        "What did I do with Rachel on the Wednesday two months ago?",
        "What was the significant buisiness milestone I mentioned four "
        "weeks ago?",
        "I mentioned cooking something for my friend a couple of days "
        "ago. What was it?",
        "Which book did I finish a week ago?",
    ]:
        assert classify_question(q) is AssemblyShape.AGO_RELATIVE, q


def test_most_recently_shape_fires():
    """#2652 recency: the ADVERBIAL 'most recently' routes to AGO_RELATIVE
    (the open as-of window: everything up to question_date). The
    ATTRIBUTIVE 'most recent' does NOT — it stays a guard (main parity),
    see ``test_nary_and_order_instruction_never_fire``."""
    for q in [
        "Which mode of transport did I use most recently, a bus or a "
        "train?",
        "Which streaming service did I start using most recently?",
        "Which mode of transport did I use most recently?",
    ]:
        assert classify_question(q) is AssemblyShape.AGO_RELATIVE, q


# ── #2652 anchor+offset arithmetic (deterministic, question_date minus
#    offset → as-of window) ───────────────────────────────────────────────

@pytest.mark.parametrize("question,qdate,kind,unit,count,anchor,lo,hi", [
    # explicit numeric offsets: the window is the unit's half-span around
    # the anchor (a '2 weeks ago' claim rounds to the nearest week)
    ("what did I do 2 weeks ago?", "2026-09-10", "ago-offset", "week", 2,
     "2026-08-27", "2026-08-24", "2026-08-30"),
    ("What kitchen appliance did I buy 10 days ago?", "2026-09-10",
     "ago-offset", "day", 10, "2026-08-31", "2026-08-31",
     "2026-08-31"),
    ("What was the significant buisiness milestone I mentioned four "
     "weeks ago?", "2026-09-10", "ago-offset", "week", 4,
     "2026-08-13", "2026-08-10", "2026-08-16"),
    # 'a week ago' / 'a month ago' — the article is the count 1
    ("Which book did I finish a week ago?", "2026-09-10", "ago-offset",
     "week", 1, "2026-09-03", "2026-08-31", "2026-09-06"),
    # calendar months/years (NOT 30/365 days) — the anchor is the same
    # day-of-month a month earlier
    ("What did I buy a month ago?", "2026-09-10", "ago-offset", "month",
     1, "2026-08-10", "2026-07-26", "2026-08-25"),
    ("how many months ago did I attend the festival?", "2026-09-10",
     "ago-count", "month", None, None, "2025-08-06", "2026-09-10"),
    # month-end clamp: 2026-03-31 minus one month = 2026-02-28
    ("What did I do a month ago?", "2026-03-31", "ago-offset", "month",
     1, "2026-02-28", "2026-02-13", "2026-03-15"),
    ("how many years ago did I move here?", "2026-09-10", "ago-count",
     "year", None, None, "2025-08-06", "2026-09-10"),
])
def test_relative_anchor_arithmetic(
        question, qdate, kind, unit, count, anchor, lo, hi):
    a = resolve_relative_anchor(question, qdate)
    assert isinstance(a, RelativeAnchor), question
    assert a.kind == kind
    assert a.unit == unit
    assert a.count == count
    assert (a.anchor.isoformat() if a.anchor else None) == anchor
    assert (a.window_lo.isoformat() if a.window_lo else None) == lo
    assert (a.window_hi.isoformat() if a.window_hi else None) == hi


def test_relative_anchor_fuzzy_determiners_widen_the_window():
    """'a couple of days ago' is not exactly 2 days — the window widens by
    the fuzzy tolerance instead of pinning a single day (never a
    confidently wrong anchor)."""
    from datetime import date as _date
    a = resolve_relative_anchor(
        "I mentioned cooking something for my friend a couple of days "
        "ago. What was it?", "2026-09-10")
    assert a is not None and a.kind == "ago-offset"
    assert a.unit == "day" and a.count == 2 and a.fuzzy is True
    assert a.anchor.isoformat() == "2026-09-08"
    assert a.window_lo.isoformat() == "2026-09-06"
    assert a.window_hi.isoformat() == "2026-09-10"
    # an exact numeric count keeps a one-day window
    exact = resolve_relative_anchor("what did I do 2 days ago?",
                                    "2026-09-10")
    assert exact.fuzzy is False
    assert exact.window_lo == exact.window_hi == exact.anchor
    # an ARTICLE means exactly one — 'a day ago' is not a fuzzy claim, and
    # widening it to ±2 days would push the window PAST question_date
    art = resolve_relative_anchor("what did I do a day ago?", "2026-09-10")
    assert art is not None and art.count == 1 and art.fuzzy is False
    assert art.window_lo == art.window_hi == art.anchor == _date(2026, 9, 9)
    # ... and no as-of window ever extends past question_date (the question
    # asks what was true AS OF it, so a future row is never an answer)
    for q in ("what did I do a week ago?", "what did I do a couple of days "
              "ago?", "what did I do 3 days ago?"):
        w = resolve_relative_anchor(q, "2026-09-10")
        assert w.window_hi <= _date(2026, 9, 10), (q, w.window_hi)


def test_relative_anchor_recency_is_an_open_as_of_window():
    """Recency has no offset to subtract — the anchor is unknown and the
    window is the open past up to question_date (never a fabricated date)."""
    a = resolve_relative_anchor(
        "Which streaming service did I start using most recently?",
        "2026-09-10")
    assert a is not None and a.kind == "recency"
    assert a.unit is None and a.count is None and a.anchor is None
    assert a.window_hi.isoformat() == "2026-09-10"
    assert (a.window_hi - a.window_lo).days == 400


def test_resolver_and_router_agree_on_the_shape_boundary():
    """The resolver is the router's second half, not a parallel definition
    of the boundary. A question ``classify_question`` does NOT route to
    AGO_RELATIVE must never yield an anchor, even when a promoted offset is
    textually present — ``_relative_guarded`` alone is only the two-subject
    half of that boundary. (Before this was pinned, 'How long had I been X
    two weeks ago when Y' classified as None but resolved to a window, so a
    caller that skipped the router could fire the wrong shape.)"""
    q = ("How long had I been a member of 'Book Lovers Unite' two weeks "
         "ago when I attended the meetup?")
    assert classify_question(q) is None
    assert resolve_relative_anchor(q, "2026-09-10") is None
    # ... while the as-of state question the shape DOES own still resolves
    q2 = "what is the current status of the couch two weeks ago?"
    assert classify_question(q2) is AssemblyShape.AGO_RELATIVE
    assert resolve_relative_anchor(q2, "2026-09-10") is not None


def test_relative_anchor_never_raises():
    """Deterministic degrade: no/invalid question_date, no anchor, blank
    input — all return None (the caller then falls through to legacy)."""
    assert resolve_relative_anchor("what did I do 2 weeks ago?",
                                  None) is None
    assert resolve_relative_anchor("what did I do 2 weeks ago?",
                                  "not-a-date") is None
    assert resolve_relative_anchor("what is the current status of the "
                                  "couch?", "2026-09-10") is None
    assert resolve_relative_anchor("", "2026-09-10") is None
    assert resolve_relative_anchor(None, "2026-09-10") is None
    # an UNREPRESENTABLE offset is an unparseable one: `_RE_AGO_OFFSET`
    # accepts unbounded digits, so the arithmetic must not escape as
    # ValueError/OverflowError (a raise surfaces as AskRetrievalUnavailable
    # instead of the legacy fall-through)
    assert resolve_relative_anchor(
        "what did I do 100000 years ago?", "2026-09-10") is None
    assert resolve_relative_anchor(
        "what did I do 99999999999999999999 days ago?", "2026-09-10") is None
    # ... and the WINDOW arithmetic is guarded too: the anchor can be
    # representable while `anchor ± half` is not (2026 − 2025 years falls
    # below date.min; a question_date at the top of date's range overflows
    # on the other side)
    assert resolve_relative_anchor(
        "what did I do 2025 years ago?", "2026-01-15") is None
    assert resolve_relative_anchor(
        "what did I do 0 years ago?", "9999-12-31") is None
    assert resolve_relative_anchor(
        "what did I do 0 months ago?", "9999-12-31") is None


def test_misfire_preference_with_two_named_options():
    """R16 misfire: preference/advice with two named options + compare
    syntax must NOT fire (a false fire would REPLACE a working generic pool
    with a shape block — the active-harm path)."""
    for q in [
        "compare the couch and the dog bed, which should i keep?",
        "Compare the Samsung Galaxy S22 and the Dell XPS 13 - which one "
        "should I buy?",
        "Should I go with the coffee maker or the stand mixer?",
        "Which is better for me, the bike or the car?",
        # advice + ordering morphology (review r2 P1 — the R16 family the
        # floor cannot catch: none of these are census rows)
        "Which should I buy first, the couch or the dog bed?",
        "Which should I watch first, The Crown or Game of Thrones?",
        "What should I do first, call my mom or text my dad?",
        "Who should I ask first, Rachel or Alex?",
        "Which task should I prioritize first, the report or the slides?",
        "Which is best to buy first, the couch or the dog bed?",
        "Which is preferable to watch first, The Crown or Game of "
        "Thrones?",
        # "best friend" is neutral — must NOT trip the guard
        "Which is best for my best friend to buy first, the couch or the "
        "dog bed?",
    ]:
        assert classify_question(q) is None, q


def test_duration_state_never_fires():
    """The RESIDUAL duration/count morphology never fires — single-anchor
    "how long" (no reference event), "how many X did I spend/take",
    single-anchor 'since', 'how many X before Y'.

    #2654: the state-entry form ("how long had I been <state> when
    <event>") moved OUT of this list — it is the duration-state shape now,
    pinned by ``test_duration_state_fires``."""
    for q in [
        "How long have I been working before I started my current job at "
        "NovaTech?",
        "How long did I use my new binoculars before I saw the American "
        "goldfinches returning to the area?",
        "How long did I take to finish 'The Seven Husbands of Evelyn Hugo' "
        "and 'The Nightingale' combined?",
        "How old was I when I moved to the United States?",
        "How many days did I spend on my solo camping trip to Yosemite "
        "National Park?",
        "How many times did I go to the gym in June?",
        "How many charity events did I participate in before the 'Run for "
        "the Cure' event?",
        "How many months have passed since I participated in two charity "
        "events in a row?",
        "How many months before my anniversary did Rachel get engaged?",
        "How many days did it take for me to find a house I loved after "
        "starting to work with Rachel?",
    ]:
        assert classify_question(q) is None, q
        assert extract_subject_terms(q) == [], q


# ── #2654 duration-state — the state-entry anchor variant ────────────────

_DURATION_STATE_FIRE = [
    # the five census rows whose morphology IS the state-entry form
    "How long had I been a member of 'Book Lovers Unite' when I attended "
    "the meetup?",
    "How long had I been watching stand-up comedy specials regularly when "
    "I attended the open mic night at the local comedy club?",
    "How long had I been bird watching when I attended the bird watching "
    "workshop?",
    "How long had I been using the new area rug when I rearranged my "
    "living room furniture?",
    "How long had I been taking guitar lessons when I bought the new "
    "guitar amp?",
    # auxiliary/pronoun variants — the TEMPLATE is the contract, not the
    # census's vocabulary
    "how long have I been studying spanish when i moved to madrid?",
    "How long has she been working at the clinic when the merger "
    "happened?",
    "How long had the narrator been living in Berlin when the wall fell?",
]


@pytest.mark.parametrize("question", _DURATION_STATE_FIRE)
def test_duration_state_fires(question):
    """#2654: "how long had I been <state> when <event>" is the state-entry
    anchor shape — no longer the blanket duration rejection. It needs a
    state's ENTRY date and the as-of anchor, so it is not an interval (no
    two named events)."""
    assert classify_question(question) is AssemblyShape.DURATION_STATE


@pytest.mark.parametrize("question", [
    "How long has she been working when the merger on Christmas happened?",
    "How long has she been working when the merger on Valentine's day "
    "happened?",
    "How long had I been living in Berlin when the wall fell on "
    "Christmas?",
])
def test_duration_state_anchor_adjunct_keeps_the_shape(question):
    """#2655 merge-regression pin (P1): a duration-state question carrying a
    CALENDAR-ANCHOR adjunct inside its ``when <event>`` clause is still
    DURATION_STATE. The anchor belongs to the reference event, not to the
    question's window, so the day-lookup branch must never preempt the shape
    — main (#2654) routes these to DURATION_STATE. The pinned non-anchor
    sibling (``... at the clinic when the merger happened?``,
    ``_DURATION_STATE_FIRE``) is unchanged: this only closes the anchor
    gap. The anchor branch preempted them to RELATIVE_DATE until
    ``_RE_DURATION_STATE.match(question)`` was added to
    ``_date_lookup_surface``'s competing-shape reject.

    The ``has`` auxiliary is the exposed spelling: ``_RE_DURATION`` covers
    ``had|have|did|was|were`` but not ``has``, so only ``has`` was left for
    the anchor branch to steal. The ``had`` param pins that the fix does not
    disturb the already-rejected spelling.

    Mutation: drop ``or _RE_DURATION_STATE.match(question)`` from that reject
    -> the ``has`` params fire as ``RELATIVE_DATE`` instead of
    ``DURATION_STATE``.
    """
    assert classify_question(question) is AssemblyShape.DURATION_STATE
    assert classify_question(question) is AssemblyShape.DURATION_STATE


def test_duration_state_extraction_peels_the_state_predicate():
    """The STATE span is a PREDICATED state, not an entity name: the lead
    predicate is peeled so the resolver probes the ENTITY, never the
    predicate phrase. The "when <event>" clause is deliberately NOT a
    second term — the window's other end is the as-of date the caller
    supplies, so a second term would gate the shape on a span the
    arithmetic never reads."""
    assert extract_subject_terms(
        "How long had I been a member of 'Book Lovers Unite' when I "
        "attended the meetup?") == ["'Book Lovers Unite'"]
    assert extract_subject_terms(
        "How long had I been watching stand-up comedy specials regularly "
        "when I attended the open mic night?") == ["stand-up comedy specials"]
    assert extract_subject_terms(
        "How long had I been taking guitar lessons when I bought the new "
        "guitar amp?") == ["guitar lessons"]
    # no predicate lead -> the _clean-ed span survives unchanged
    assert extract_subject_terms(
        "How long had I been bird watching when I attended the "
        "workshop?") == ["bird watching"]
    # exactly ONE term per duration-state question
    assert len(extract_subject_terms(
        "How long had I been using the new area rug when I rearranged my "
        "living room furniture?")) == 1
    # no state span to peel (empty template capture) -> no terms at all
    assert extract_subject_terms("How long had I been when I left?") == []


def test_duration_state_both_halves_is_the_single_subject_gate():
    """#2654: the state-entry shape has ONE graph anchor (the state
    subject); the as-of date needs no resolution. So it takes the
    single-subject gate — one resolved subject fires, an unresolved or
    empty one does not."""
    res = ResolveResult(
        candidates=(_cand("obj-club", "Book Lovers Unite", 0),),
        unresolved=())
    assert res.both_halves_ok(AssemblyShape.DURATION_STATE) is True
    assert ResolveResult().both_halves_ok(
        AssemblyShape.DURATION_STATE) is False
    assert ResolveResult(
        candidates=(_cand("obj-x", "x", 0),),
        unresolved=("term",)).both_halves_ok(
            AssemblyShape.DURATION_STATE) is False
    # the two-subject shapes keep the both-halves gate (no relaxation)
    assert ResolveResult(
        candidates=(_cand("obj-x", "x", 0),),
        unresolved=()).both_halves_ok(AssemblyShape.INTERVAL) is False


def test_duration_state_does_not_steal_interval_or_rejected_forms():
    """#2654 no-regression at the classifier: the new template changes no
    other shape's verdict — the since-until interval still classifies
    INTERVAL, the rejected duration forms stay None, and a relative offset
    still kills the duration-state form itself (R13)."""
    assert classify_question(
        "how many days had passed since I bought the couch when I sold the "
        "dog bed?") is AssemblyShape.INTERVAL
    for q in [
        "How long have I been working before I started my current job at "
        "NovaTech?",
        "How long did I take to finish 'A' and 'B' combined?",
        "How old was I when I moved to the United States?",
        # R13: 'ago' kills the duration-state form too
        "How long had I been a member of 'Book Lovers Unite' two weeks ago "
        "when I attended the meetup?",
        # R13 N-ary: the 3+ option list is a reject for EVERY template,
        # duration-state included (the guard runs BEFORE the template)
        "How long had I been dating Mark or Sarah or Tom when I moved?",
        # R13 'ago' residue: an un-promoted offset (hours/minutes/…) is a
        # window too — it must not be folded into the state subject
        "How long had I been asleep one hour ago when the alarm rang?",
    ]:
        assert classify_question(q) is None, q
        assert extract_subject_terms(q) == [], q


def test_nary_ordering_fires_with_n_terms():
    """#2653: 3+ named options DECOMPOSE (R13's 'N-ary out of v1' is lifted)
    — every delimiter family the pre-#2653 guard enumerated, plus the
    corpus's colon-introduced list form."""
    for q, expected in [
        # colon + comma/and list (the corpus form; gpt4_f49edff3)
        ("Which three events happened in order: the day I helped my friend, "
         "the trip, and the party?",
         ["I helped my friend", "the trip", "the party"]),
        # colon + quoted list (gpt4_18c2b244 shape)
        ("What is the order of the three events: 'A', 'B', and 'C'?",
         ["'A'", "'B'", "'C'"]),
        # repeated ' or '
        ("Which did I get first, the couch or the chair or the table?",
         ["the couch", "the chair", "the table"]),
        # comma list + final ' or '
        ("Which did I get first, the couch, the chair or the table?",
         ["the couch", "the chair", "the table"]),
        # colon + comma list + final ' or '
        ("Which came first: my graduation, my wedding or my first child?",
         ["my graduation", "my wedding", "my first child"]),
        # 4 options
        ("Which did I get first, the couch, the chair, the lamp or the "
         "table?",
         ["the couch", "the chair", "the lamp", "the table"]),
        # a colon INSIDE a quoted option is not the list introducer — the
        # span must not be cut there (the rsplit(":", 1) truncation)
        ("Which did I get first, 'Star Wars: A New Hope', 'Empire', or "
         "'Jedi'?",
         ["'Star Wars: A New Hope'", "'Empire'", "'Jedi'"]),
        # a time-of-day colon in the ordering HEAD is not the introducer
        # either — the list colon after it is (the first-colon-cut
        # regression)
        ("What is the order of the events at 3:00: the couch, the chair, "
         "and the lamp?",
         ["the couch", "the chair", "the lamp"]),
        # a POSSESSIVE apostrophe before the colon is not an opening quote —
        # counting it as one hid the list colon and fell back to the marker
        # span
        ("What is the order of Sarah's events: the wedding, the trip, and "
         "the party?",
         ["the wedding", "the trip", "the party"]),
    ]:
        assert classify_question(q) is AssemblyShape.ORDERING, q
        assert extract_subject_terms(q) == expected, q


def test_nary_ordering_introducer_and_current_state_guards():
    """The option-list introducer survives an apostrophe, an inch mark and a
    time-of-day head, and a CURRENT_STATE subject is not an option list.

    Each case is a family the colon-cut rewrite had to keep correct: a
    possessive head ("day's") whose `'` must not mask the introducer; an
    inch mark that leaves the quotes unpaired; a trailing time clause that
    must not be taken for the introducer; and a quoted option that itself
    contains a colon, which is ONE option and not two.
    """
    for q, expected in [
        ("What is the order of the day's events at 3:00: the couch, the "
         "chair, and the table?",
         ["the couch", "the chair", "the table"]),
        ("What is the order of the events at 5\" : the couch, the chair, and "
         "the meeting at 3:00?",
         ["the couch", "the chair", "the meeting at 3:00"]),
        ("What is the order of: 'Star Wars: A New Hope', 'Empire', and "
         "'Jedi'?",
         ["'Star Wars: A New Hope'", "'Empire'", "'Jedi'"]),
    ]:
        assert classify_question(q) is AssemblyShape.ORDERING, q
        assert extract_subject_terms(q) == expected, q
    # "the order I placed:" is a CURRENT_STATE subject, not an option list
    assert classify_question(
        "What is the current status of the order I placed: the couch, the "
        "chair, and the table?") is AssemblyShape.CURRENT_STATE
    # an or-chain outside an ordering head is not a shape at all, even though
    # `_nary_terms` alone would decompose it (origin/main answers None here).
    # Without the ordering-head half of the exemption these come back as the
    # duration-state / interval shapes.
    for q in ["How long had I been dating: the lamp or the table or the chair "
              "when it ended?",
              "How many days had passed since I bought: the couch or the lamp "
              "or the table when it ended?"]:
        assert classify_question(q) is None, q
    # an introducer colon inside an OPTION is not an introducer: the template
    # matches, but with no introducer the segment keeps the ordering head, and
    # the list must not fire on that leaked first term
    assert classify_question(
        "What is the order of the couch, the chair, and the meeting at "
        "3:00") is None


def test_binary_ordering_unaffected_by_the_nary_arm():
    """The two-option path is BYTE-IDENTICAL (#2653 acceptance): every
    binary delimiter family still yields exactly 2 terms, and a binary
    question whose A-side merely CONTAINS a comma is not split into three
    (the `_RE_OPTION_LEAD` guard — otherwise the R1 all-terms gate would fail
    closed where the binary path used to fire)."""
    for q, expected in [
        ("Which vehicle did I take care of first in February, the bike or "
         "the car?", ["the bike", "the car"]),
        ("Which book did I finish reading first, 'The Hate U Give' or 'The "
         "Nightingale'?", ["'The Hate U Give'", "'The Nightingale'"]),
        ("Which event happened first, my cousin's wedding or Michael's "
         "engagement party?",
         ["my cousin's wedding", "Michael's engagement party"]),
        ("which came first - the couch or the dog bed?",
         ["the couch", "the dog bed"]),
        ("Which did I get first: the couch or the dog bed?",
         ["the couch", "the dog bed"]),
        ("Which seeds were started first, the tomatoes or the marigolds?",
         ["the tomatoes", "the marigolds"]),
    ]:
        assert classify_question(q) is AssemblyShape.ORDERING, q
        terms = extract_subject_terms(q, AssemblyShape.ORDERING)
        assert terms == expected, (q, terms)
    # a comma INSIDE the A-side of a binary question (parenthetical clause)
    # must not be read as a third option — the shape does not fire at all
    assert classify_question(
        "Which did I get first, 'The Hate U Give', which I loved, or 'The "
        "Nightingale'?") is None


def test_narrative_verbs_do_not_trip_the_advice_guard():
    """#2653: bare 'pick/choose/decide' are ordinary narrative verbs. The
    census's own N-ary row says 'the day I helped my cousin PICK OUT STUFF
    for her baby shower' — a bare-verb advice guard rejected the shape for it,
    silently excluding a measured row. Preference forms stay rejected."""
    # narrative use -> the shape fires
    assert classify_question(
        "Which three events happened in the order from first to last: the "
        "day I helped my friend prepare the nursery, the day I helped my "
        "cousin pick out stuff for her baby shower, and the day I ordered a "
        "customized phone case for my friend's birthday?"
    ) is AssemblyShape.ORDERING
    # preference use -> still rejected (R16)
    for q in [
        "Which one shall I pick first, the couch or the dog bed?",
        "Which to choose first, the couch or the dog bed?",
        "Which would you pick first, the couch or the dog bed?",
        "Which should I decide first, the couch or the dog bed?",
        "Which one to pick first, the couch or the dog bed?",
    ]:
        assert classify_question(q) is None, q


def test_nary_recency_and_class_plural_never_fire():
    """Recency ('most recently'), the compound single-subject 'between the
    two Xs' form, and the CLASS-PLURAL enumeration request (no NAMED
    subjects) never fire — the N-ary arms need a decomposable option list,
    and 'the concerts and musical events I attended' is not one."""
    for q in [
        "What is the order of the six museums I visited from earliest to "
        "latest?",
        "What is the order of the concerts and musical events I attended "
        "in the past two months, starting from the earliest?",
        "What is the order of the three trips I took in the past three "
        "months, from earliest to latest?",
        # the SAME ordering instruction spelled with the ATTRIBUTIVE 'most
        # recent' — only the adverbial 'most recently' names a time, so the
        # attributive stays a guard (as on main) and is not promoted
        "What is the order of the six museums I visited from earliest to "
        "most recent?",
        "List the events from most recent to oldest.",
        # ... and the guard must catch it BEFORE the shape templates, or the
        # question falls through and fires ORDERING/CURRENT_STATE on a
        # recency question main deliberately rejected
        "Which did I buy first, my most recent phone or my laptop?",
        "what is the current status of my most recent car?",
        "Which came first, my most recent trip to Paris or my graduation?",
        # ... including the hyphen/dash spellings: `\W+` makes the guard
        # spelling-independent, so 'most-recent' is the same instruction
        "Which did I buy first, my most-recent phone or my laptop?",
        "what is the current status of my most‑recent car?",  # U+2010
        "How many days passed between the two deep-subject milestones?",
        "When did I buy the couch?",  # date-lookup w/o shape support (v1)
        # semicolon-delimited lists are NOT the measured form
        "Which came first; my graduation; my wedding or my first child?",
    ]:
        assert classify_question(q) is None, q
        assert extract_subject_terms(q) == [], q


def test_empty_and_garbage_input():
    assert classify_question("") is None
    assert classify_question("   ") is None
    assert classify_question(None) is None  # type: ignore[arg-type]
    assert classify_question("the dog went for a walk") is None


# ── subject-term extraction ───────────────────────────────────────────────

@pytest.mark.parametrize("question,expected", [
    # two named options with quotes/possessives intact (the Task-3 resolver
    # input — raw text spans, never cleaned to ungrammatical fragments)
    ("Which event happened first, my cousin's wedding or Michael's "
     "engagement party?", ["my cousin's wedding", "Michael's engagement "
                           "party"]),
    ("Which device did I get first, the Samsung Galaxy S22 or the Dell "
     "XPS 13?", ["the Samsung Galaxy S22", "the Dell XPS 13"]),
    ("Who became a parent first, Rachel or Alex?", ["Rachel", "Alex"]),
    ("Which book did I finish reading first, 'The Hate U Give' or 'The "
     "Nightingale'?", ["'The Hate U Give'", "'The Nightingale'"]),
    ("Who did I meet first, Mark and Sarah or Tom?",
     ["Mark and Sarah", "Tom"]),
    ("which came first - the couch or the dog bed?",
     ["the couch", "the dog bed"]),
    ("Which event happened first, the meeting with Rachel or the pride "
     "parade?", ["the meeting with Rachel", "the pride parade"]),
    # quoted title starting "Before" must survive (review r2 frequency-guard
    # over-reach regression)
    ("Which book did I finish first, 'Before the Fall' or 'The Watchman'?",
     ["'Before the Fall'", "'The Watchman'"]),
    ("Which event happened first, the event in May or the party in June?",
     ["the event in May", "the party in June"]),
    ("Which came first, the summer I would spend in Spain or the year I "
     "moved to Madrid?",
     ["the summer I would spend in Spain", "the year I moved to Madrid"]),
])
def test_ordering_subject_extraction(question, expected):
    assert extract_subject_terms(question) == expected


@pytest.mark.parametrize("question,expected", [
    ("How many days passed between the day I bought my new tennis racket "
     "and the day I received it?",
     ["I bought my new tennis racket", "I received it"]),
    ("How many days had passed since I finished reading 'The Seven "
     "Husbands of Evelyn Hugo' when I attended the book club meeting?",
     ["I finished reading 'The Seven Husbands of Evelyn Hugo'",
      "I attended the book club meeting"]),
    ("how many days between buying the couch and selling the couch?",
     ["buying the couch", "selling the couch"]),
    ("How many weeks passed between my visit to the Museum of Modern Art "
     "(MoMA) and the 'Ancient Civilizations' exhibit?",
     ["my visit to the Museum of Modern Art (MoMA)",
      "the 'Ancient Civilizations' exhibit"]),
    ("Between buying the couch and selling it, how many days passed?",
     ["buying the couch", "selling it"]),
    # the time-noun head strip must not eat real entity heads (review r2 P2)
    ("How many months passed between the month of my engagement and the "
     "month of my wedding?",
     ["the month of my engagement", "the month of my wedding"]),
])
def test_interval_subject_extraction(question, expected):
    assert extract_subject_terms(question) == expected


def test_current_state_subject_extraction():
    assert extract_subject_terms(
        "what is the current status of the couch?") == ["the couch"]
    assert extract_subject_terms(
        "What is the current status of the sofa?") == ["the sofa"]
    assert extract_subject_terms("is the couch still live?") == [
        "the couch"]


def test_extraction_matches_classification_consistency():
    """extract_subject_terms without a shape arg classifies first — the two
    public entry points must never disagree."""
    for q in [
        "Which event happened first, my cousin's wedding or Michael's "
        "engagement party?",
        "How many days between buying the couch and selling the couch?",
        "what is the current status of the couch?",
    ]:
        sh = classify_question(q)
        assert sh is not None
        assert extract_subject_terms(q) == extract_subject_terms(q, sh)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 3 — resolver: recall-first, confidence-tagged, no LLM
# (R1 both-halves gate, R7 never a silent single-match, R10 embedded
# degrade, R12/C7 collision). RED→GREEN: the functions resolve_subjects /
# SubjectCandidate / ResolveResult / ResolverPort live in tortoise/assembly.py
# and did not exist when these tests were authored.
# ══════════════════════════════════════════════════════════════════════════


# ── dict-stubbed ResolverPort (no DB — the R7 exact-probe path) ───────────

class _DictObjectPort:
    """In-memory ResolverPort: {name: {id, name}} + optional FTS/alias rows."""

    def __init__(self, objects, *, fts=None, alias=None):
        # name -> list (a collision — two Objects sharing a name — must
        # SURVIVE the stub: R12/C7 the resolver returns both with ids)
        self._objects: dict[str, list[dict]] = {}
        for o in objects:
            self._objects.setdefault(o["name"], []).append(o)
        self._fts = fts or (lambda term, limit=8: [])
        self._alias = alias or (lambda term, limit=8: [])

    def exact_objects(self, names):
        out = []
        for n in names:
            out.extend(self._objects.get(n, []))
        return out

    def excluded_exact_objects(self, names):
        # the dict stub has no exclusion concept (no status field)
        return []

    def fts_objects(self, term, limit=8):
        return self._fts(term, limit)

    def alias_objects(self, term, limit=8):
        return self._alias(term, limit)


_O1 = {"id": "obj-1", "name": "couch"}
_O2 = {"id": "obj-2", "name": "dog bed"}
_O3 = {"id": "obj-3", "name": "sofa"}


def _base_port():
    return _DictObjectPort([_O1, _O2, _O3])


def test_resolver_exact_probe_dict_stub():
    """R7 exact-name probe: 'the couch'/'the dog bed' resolve to couch /
    dog bed with confidence=high, source=exact — on a dict stub, no DB."""
    port = _base_port()
    res = resolve_subjects(port, ["the couch", "the dog bed"],
                           shape=AssemblyShape.ORDERING)
    assert isinstance(res, ResolveResult)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is True
    by_index = {c.subject_index: c for c in res.candidates}
    assert by_index[0].name == "couch"
    assert by_index[0].confidence == "high"
    assert by_index[0].source == "exact"
    assert by_index[0].object_id == "obj-1"
    assert by_index[1].name == "dog bed"


def test_resolver_one_half_unresolved_fires_nothing():
    """R1 both-halves: an ordering/interval shape whose SECOND subject does
    not resolve → unresolved flagged, both_halves_ok False (the fired
    decision stays false — legacy byte-identical)."""
    port = _base_port()
    res = resolve_subjects(port, ["the couch", "the exercise bike"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is False
    assert "the exercise bike" in res.unresolved
    # the resolved half alone must NOT silently pass as a single match (R7)
    assert [c.name for c in res.candidates] == ["couch"]


def test_resolver_collision_returns_both_with_ids():
    """R12/C7: two Objects sharing one name resolve to BOTH candidates with
    their distinct ids (the renderer sections per candidate at Task 5)."""
    dup = [{"id": "obj-a", "name": "bike"}, {"id": "obj-b", "name": "bike"}]
    port = _DictObjectPort(dup)
    res = resolve_subjects(port, ["the bike"], shape=AssemblyShape.CURRENT_STATE)
    assert len(res.candidates) == 2
    assert {c.object_id for c in res.candidates} == {"obj-a", "obj-b"}
    assert all(c.name == "bike" for c in res.candidates)
    # multi-candidate admission keeps per-candidate tags (Task 5 sections
    # per candidate on object_id — a dedup regression must fail loudly)
    assert all(c.confidence == "high" and c.source == "exact"
               for c in res.candidates)


def test_resolver_fts_missing_degrades_to_empty():
    """R10 embedded degrade: the docker-only FTS leg raising (absent index)
    → [] + no raise; the term stays unresolved (never a fabricated hit)."""
    class _NoFtsPort:
        def exact_objects(self, names):
            return [{"id": "obj-1", "name": "couch"}] \
                if "couch" in names else []

        def excluded_exact_objects(self, names):
            return []

        def fts_objects(self, term, limit=8):
            raise RuntimeError("no fulltext index (embedded)")

        def alias_objects(self, term, limit=8):
            raise RuntimeError("no alias leg")

    res = resolve_subjects(_NoFtsPort(),
                           ["the couch", "the missing item"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is False
    assert "the missing item" in res.unresolved
    assert [c.name for c in res.candidates] == ["couch"]


def test_resolver_alias_amplifier_low_confidence():
    """Alias amplifier: an Object whose NAME does not token-match the term
    but whose anchored Point search_keys do → resolves source=alias,
    confidence=low."""
    port = _base_port()
    port._alias = lambda term, limit=8: (
        [{"id": "obj-9", "name": "projector"}] if "ikea" in term else [])
    res = resolve_subjects(port, ["the thing from ikea"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert [c.name for c in res.candidates] == ["projector"]
    assert res.candidates[0].source == "alias"
    assert res.candidates[0].confidence == "low"


def test_resolver_interval_same_subject_both_indexes():
    """Interval 'buying the couch'/'selling the couch' → both halves resolve
    to the SAME object under distinct subject_indexes."""
    port = _base_port()
    res = resolve_subjects(port, ["buying the couch", "selling the couch"],
                           shape=AssemblyShape.INTERVAL)
    assert res.both_halves_ok(AssemblyShape.INTERVAL) is True
    by_index = {c.subject_index: c for c in res.candidates}
    assert by_index[0].object_id == by_index[1].object_id == "obj-1"


def test_resolver_edge_terms_never_raise():
    """Degenerate/edge terms (possessives w/o a stored name, mixed case,
    stopwords-only, empty) → no raise, honest unresolved."""
    port = _base_port()
    for term in ["", "   ", "the", "a", "my", "cousin's wedding",
                 "THE COUCH", "Mark and Sarah", "the thing from ikea xyz"]:
        res = resolve_subjects(port, [term],
                               shape=AssemblyShape.CURRENT_STATE)
        assert isinstance(res, ResolveResult)
    # mixed case: stored name is lowercase "couch" — "THE COUCH" variants
    # include "THE COUCH"/"COUCH"; exact probe is case-sensitive (honest
    # miss — the docker name index is lowercase-stored); must not raise
    assert isinstance(res, ResolveResult)


def test_resolver_empty_candidates_never_fire_current_state():
    """A zero-candidate ResolveResult must NOT satisfy the current-state
    gate (an empty resolved set is an unresolved subject, not a match)."""
    assert ResolveResult().both_halves_ok(AssemblyShape.CURRENT_STATE) is False
    assert ResolveResult().both_halves_ok(AssemblyShape.ORDERING) is False
    assert ResolveResult().both_halves_ok(AssemblyShape.INTERVAL) is False
    assert ResolveResult().both_halves_ok(None) is False


def test_resolver_excluded_statuses_stay_inside_the_recall_excluded_set():
    """#3317 pin — the resolver's unresolvable set is EXACTLY ``{retracted}``.

    The two Object sets in this file are different by design (the resolver's
    is the Object-SEARCH boundary, ``{retracted}``;
    ``_RECALL_OBJECT_EXCLUDED_STATUSES`` is the five-status successor-probe
    view). Together these three assertions PIN the resolver set without
    restating a literal: subset of the recall-excluded set (plus
    ``retracted`` present) and each of the other four members explicitly
    absent ⇒ exactly ``{retracted}``.

    The subset direction alone is NOT the oracle for ``outdated`` — the recall
    set CONTAINS it — so every status whose exclusion is a decision is named
    here rather than left implied. Widening to ``superseded`` would also break
    the pinned current-state render (`test_resolver_docker_exact_and_both_halves`),
    but that is a docker-lane consequence; this pure pin is the fast one.

    #3302: the equality assertion at the end pins the RECALL set's own
    contents. The subset assertions above are about the RESOLVER set and are
    blind to four of the recall set's five members (only ``retracted`` is
    shared), so they cannot substitute for it.
    """
    from tortoise.assembly import (
        _RECALL_OBJECT_EXCLUDED_STATUSES,
        _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES,
    )
    from tortoise.commit_ops import OBJECT_TERMINAL_STATUSES
    assert (
        _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES
        <= _RECALL_OBJECT_EXCLUDED_STATUSES
    )
    assert "retracted" in _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES
    for st in ("superseded", "deprecated", "archived", "outdated"):
        assert st not in _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES
    # #3302: the successor-probe set is EXACTLY the canonical OBJECT vocabulary
    # plus its one load-bearing divergence. `outdated` is not a dead member —
    # create_entity/update_entity can write it through the public MCP surface
    # (`status` is not server-managed), so an exact pin is required rather than
    # a subset check.
    assert set(_RECALL_OBJECT_EXCLUDED_STATUSES) == \
        set(OBJECT_TERMINAL_STATUSES) | {"outdated"}


# ── #4061 R1/R2/R3: the exclusion is decided where it is applied ───────────


def test_resolver_excluded_exact_match_abstains_not_substitutes():
    """#4061 R1: a term whose EXACT match is excluded must abstain — it must
    not fall through to a weaker leg and surface a DIFFERENT live Object.

    Models #3317's live fixture in the sparse lane: ``the couch`` names a
    retracted Object (invisible to every leg), while the alias leg would
    return the live ``sofa`` through the anchored point's search_keys. The
    ladder used to substitute ``sofa``; post-#4061 the term is unresolved.

    RED before the fix (candidates == [(sofa, alias)]), GREEN after.
    """

    class _ExcludedExactPort:
        def exact_objects(self, names):
            return []                     # #3317: excluded = invisible

        def excluded_exact_objects(self, names):
            return ([{"id": "obj-couch", "name": "couch"}]
                    if "couch" in names else [])

        def fts_objects(self, term, limit=8):
            return []

        def alias_objects(self, term, limit=8):
            return [{"id": "obj-sofa", "name": "sofa"}]

    res = resolve_subjects(_ExcludedExactPort(), ["the couch"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert res.candidates == ()
    assert res.unresolved == ("the couch",)
    # the abstention keeps the both-halves gate closed (the R1 signal)
    assert res.both_halves_ok(AssemblyShape.CURRENT_STATE) is False


def test_resolver_no_exact_match_still_falls_through_to_weaker_legs():
    """#4061 R1 control: the abstention is specific to "a match it must not
    surface". A term that names NOTHING (no exact match, no excluded exact
    match) still reaches the weaker legs — the fix must not narrow recall."""

    class _AliasOnlyPort:
        def exact_objects(self, names):
            return []

        def excluded_exact_objects(self, names):
            return []

        def fts_objects(self, term, limit=8):
            return []

        def alias_objects(self, term, limit=8):
            return ([{"id": "obj-9", "name": "projector"}]
                    if "ikea" in term else [])

    res = resolve_subjects(_AliasOnlyPort(), ["the thing from ikea"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert [(c.name, c.source) for c in res.candidates] == \
        [("projector", "alias")]
    assert res.unresolved == ()


def test_resolver_excluded_probe_failure_abstains_fail_safe():
    """#4061 R1 polarity: a probe that cannot answer must ABSTAIN, never fall
    through. Falling through on a probe failure is the very fail-open
    substitution this fix removes."""

    class _RaisingProbePort:
        def exact_objects(self, names):
            return []

        def excluded_exact_objects(self, names):
            raise RuntimeError("probe unavailable")

        def fts_objects(self, term, limit=8):
            return []

        def alias_objects(self, term, limit=8):
            return [{"id": "obj-sofa", "name": "sofa"}]

    res = resolve_subjects(_RaisingProbePort(), ["the couch"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert res.candidates == ()
    assert res.unresolved == ("the couch",)


def _stub_resolver_port(monkeypatch, hits, name_rows):
    """Build a resolver port whose FTS leg is a patched
    ``search_engine.run_fts_query``.

    Returns ``(port, captured, hydration)``: ``captured`` records the kwargs
    the leg passed to the FTS leg; ``hydration`` records the follow-up name
    read's params (absent when no name read happened).
    """
    import tortoise.search_engine as se

    captured: dict = {}
    hydration: dict = {}

    def fake_run_fts_query(graph, query, **kw):
        captured["query"] = query
        captured.update(kw)
        return list(hits)

    class _G:
        def query(self, cypher, params=None, **k):
            hydration.setdefault("calls", []).append(params)
            hydration["cypher"] = cypher

            class _R:
                def __init__(self):
                    self.result_set = [list(r) for r in name_rows]

            return _R()

    class _Proj:
        g = _G()

    class _StubSDK:
        def _get_proj(self):
            return _Proj()

    monkeypatch.setattr(se, "run_fts_query", fake_run_fts_query)
    from tortoise.assembly import docker_resolver_port
    return docker_resolver_port(_StubSDK()), captured, hydration


def test_resolver_fts_leg_is_the_name_index_not_the_hybrid(monkeypatch):
    """#3223: the leg runs THE FTS leg (``search_engine.run_fts_query``)
    with this port's NARROW exclusion — it does NOT filter hybrid rows.

    Three hybrid-recovery shapes were measured and rejected: unfiltered (the
    divergence), a re-derived lexical predicate (drops "happiness"/"happy",
    admits "rating"/"rat"), and an id-filtered index intersection (the
    vector-weighted top-``limit`` never contains the name match). Making the
    leg BE the index leg removes all three.
    """
    port, captured, hydration = _stub_resolver_port(
        monkeypatch,
        hits=[("obj-a", 1.0), ("obj-b", 0.5)],
        name_rows=[["obj-a", "couch"], ["obj-b", "sofa"]],
    )
    assert port.fts_objects("couch", 8) == [
        {"id": "obj-a", "name": "couch"},
        {"id": "obj-b", "name": "sofa"},
    ]
    assert captured["query"] == "couch"
    assert captured["entity_type"] == "object"
    assert captured["limit"] == 8
    # the port's own NARROW set — not the read surface's wider vocabulary
    assert captured["excluded_statuses"] == ("retracted",)
    # the name read is bounded to exactly the ids the index returned
    assert hydration["calls"] == [{"ids": ["obj-a", "obj-b"]}]
    assert "o.id IN $ids" in hydration["cypher"]


def test_resolver_fts_exclusion_is_pushed_into_the_query(monkeypatch):
    """#4061 R2: the exclusion is applied BY the FTS query, before its LIMIT
    — so an excluded Object cannot consume the leg's bound.

    Pre-#4061 the leg filtered rows the SDK had already truncated and
    returned [] wholesale when excluded Objects filled the window; the old
    window-growth dance existed only because the SDK's all-or-nothing
    ``include_terminal`` opt-in could not express this port's narrower set.
    """
    port, captured, _ = _stub_resolver_port(
        monkeypatch,
        hits=[("obj-live", 0.9)],
        name_rows=[["obj-live", "widget"]],
    )
    assert port.fts_objects("widget", 8) == [{"id": "obj-live",
                                               "name": "widget"}]
    # the exclusion travels WITH the query (in-query, pre-LIMIT), never as a
    # post-hoc filter on already-truncated rows
    assert captured["excluded_statuses"] == ("retracted",)
    assert captured["limit"] == 8


def test_resolver_fts_status_comes_from_the_query(monkeypatch):
    """#4061 R3: ``status`` is read by the FTS query itself, so the SDK's
    batch content-fetch degradation (which could drop ``status`` and let a
    retracted Object read as live) cannot apply to this leg at all.

    The leg makes no SDK read whose payload could lack a status — the old
    ``_excluded_object_ids`` re-read existed only because it did.
    """
    port, _captured, hydration = _stub_resolver_port(
        monkeypatch,
        hits=[("obj-live", 0.9)],
        name_rows=[["obj-live", "sofa"]],
    )
    assert port.fts_objects("sofa", 8) == [{"id": "obj-live",
                                             "name": "sofa"}]
    # the ONLY graph read is the name hydration, and it selects id+name — no
    # status, because the query's WHERE already decided it
    assert hydration["calls"] == [{"ids": ["obj-live"]}]
    assert "o.id, o.name" in hydration["cypher"]
    assert "status" not in hydration["cypher"]


def test_resolver_fts_no_index_hit_costs_no_name_read(monkeypatch):
    """An index miss must not pay a second round trip."""
    port, _captured, hydration = _stub_resolver_port(
        monkeypatch, hits=[], name_rows=[])
    assert port.fts_objects("nothing") == []
    assert "calls" not in hydration


# ── docker-lane resolver legs (fixture substrate; skip when the shared
#    server is unreachable — pure tests above never touch this) ────────────

import contextlib  # noqa: E402
import uuid  # noqa: E402

from tests import _assembly_graph as _ag  # noqa: E402

_DOCKER_URI = _os_environ_uri = __import__("os").environ.get(
    "TORTOISE_DB_URI", "")
_FALKORDB_UP = False
if _DOCKER_URI:
    try:
        import tortoise.sdk as _sdkmod

        _p = _sdkmod.TortoiseSDK()
        _p._get_proj().g.query("RETURN 1")
        _p.close()
        _FALKORDB_UP = True
    except Exception:
        _FALKORDB_UP = False

_docker_only = pytest.mark.skipif(
    not (_DOCKER_URI and _FALKORDB_UP),
    reason="docker lane unavailable (pure tests unaffected)")


@pytest.fixture
def _docker_sdk(monkeypatch):
    uri = f"{_DOCKER_URI.rstrip('/')}_{uuid.uuid4().hex[:10]}"
    monkeypatch.setenv("TORTOISE_DB_URI", uri)
    from tortoise.sdk import TortoiseSDK
    s = TortoiseSDK()
    try:
        yield s
    finally:
        with contextlib.suppress(Exception):
            s._get_proj().db.select_graph(uri.rsplit("/", 1)[-1]).delete()
        s.close()


@_docker_only
def test_resolver_docker_exact_and_both_halves(_docker_sdk):
    """Live graph: 'the couch'/'the dog bed' resolve high/exact through the
    real Object index; both halves ok on the fixture substrate."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the couch", "the dog bed"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is True
    by_index = {c.subject_index: c for c in res.candidates}
    assert by_index[0].name == "couch" and by_index[0].source == "exact"
    assert by_index[0].confidence == "high"
    assert by_index[1].name == "dog bed"


@_docker_only
def test_resolver_docker_fts_paraphrase(_docker_sdk, force_sparse_tfidf):
    """R7 docker-FTS paraphrase (RESOLUTION-ONLY — the fired-path assembly
    half is Task 6's): a subject term with NO exact-name head but a stored
    Object whose NAME token-matches via FTS ('the grey comfy couch from
    the store' → couch) resolves source='fts' confidence='med'.

    #3095/#3223: the embedder state is PINNED OFF (``force_sparse_tfidf``)
    so this stays a SPARSE-leg count contract. #3223 makes leg 2 BE the name
    index (``search_engine.run_fts_query``), so the vector half never enters
    it — but pinning keeps THIS test's assertion independent of whichever
    decomposition the lane happens to run. The shipped-configuration
    counterpart is ``test_resolver_docker_embedder_on_...`` (see the #3223
    note in ``docker_resolver_port.fts_objects``)."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the grey comfy couch from the store"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert not res.unresolved
    assert [(c.name, c.source, c.confidence) for c in res.candidates] == \
        [("couch", "fts", "med")]


@_docker_only
def test_resolver_docker_unresolved_keeps_legacy(_docker_sdk,
                                                  force_sparse_tfidf):
    """A term matching NO Object stays unresolved on the live lane — the R1
    fired=False signal (legacy fallback), never an empty/errored fire.

    #3095/#3223: the embedder state is PINNED OFF (``force_sparse_tfidf``) so
    this remains a SPARSE-leg contract pin. #3223 made the fallback reachable
    in the SHIPPED (embedder-live) configuration too, by making the resolver's
    name-FTS leg BE the Object NAME index (``search_engine.run_fts_query``)
    instead of the hybrid surface;
    the deterministic shipped-configuration counterpart is
    ``test_resolver_docker_embedder_on_unresolved_keeps_legacy``. The pin is
    kept — not because the assertion is now sparse-only, but because it keeps
    THIS test's leg mix fixed rather than ambient."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port,
                           ["the couch", "the teleporting exercise bike"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is False
    assert res.unresolved == ("the teleporting exercise bike",)


@_docker_only
def test_resolver_docker_alias_leg(_docker_sdk, force_sparse_tfidf):
    """R7 leg 3 live: the Object NAME does not token-match the term but an
    anchored Point search_keys does ('the ikea purchase' → couch — couch's
    bought-point search_keys 'couch ikea 800 dollars'). Exact + FTS both
    miss (Object names couch/dog bed/sofa share no ikea token).

    #3095/#3223: embedder state PINNED OFF (``force_sparse_tfidf``) so this
    stays a SPARSE-leg contract pin. Before #3223 an unpinned vector leg
    swallowed the term at the FTS step and the alias contract was never
    exercised; #3223 makes leg 2 BE the name index (so the vector half is not
    in it at all) and the alias leg is reached in the shipped configuration as
    well — the pin keeps THIS test's leg mix fixed rather than ambient."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the ikea purchase"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert not res.unresolved
    assert [(c.name, c.source, c.confidence) for c in res.candidates] == \
        [("couch", "alias", "low")]


@_docker_only
def test_resolver_docker_excludes_retracted_object(_docker_sdk,
                                                   force_sparse_tfidf):
    """#3317: a RETRACTED Object must not resolve through ANY resolver leg.

    Pre-fix every leg was status-blind — the exact (name/id) and alias
    (anchored search_keys) legs carried no status conjunct, and the FTS leg's
    terminal exclusion is gated on ``label == 'Point'`` in ``search_engine``,
    so Object hits pass it — and ``ask()`` resolved and rendered the retracted
    Object. Post-fix ``_RESOLVER_UNRESOLVABLE_OBJECT_STATUSES``
    (``retracted``) is excluded at all three legs.

    The exclusion must stay NARROW: the SUPERSEDED couch in this same fixture
    still resolves (its state render IS the answer) — pinned by
    ``test_resolver_docker_exact_and_both_halves`` and
    ``test_resolver_docker_alias_leg``, which RED if the read-surface object
    tuple (which contains ``superseded``) is used here instead.

    ``force_sparse_tfidf`` pins the leg decomposition (#3095): before #3223
    an embedder-live hybrid FTS leg swallowed the alias term before the alias
    leg was reached. #3223's NAME gate now rejects semantic-only hits, so
    that substitution no longer depends on the pin; it is kept so THIS test's
    leg mix stays fixed rather than ambient.

    DECIDED, and SUPERSEDED BY #4061 — the ladder's fall-through WAS not
    short-circuited here (a term whose exact name matched only an
    unresolvable Object did not become "unresolved"; the weaker legs still
    ran). #4061 has since ruled that falls-through a SUBSTITUTION defect: in
    the sparse lane ``"the couch"`` reached the alias leg and resolved the
    live ``sofa`` (low confidence) through the point ``pB-couch-sold``
    ("sold the old couch and ordered a new sofa", whose search_keys name
    both), i.e. ``ask()`` fired about an entity the question never named.
    #4061's abstention now closes that (``excluded_exact_objects`` +
    ``resolve_subjects``' short-circuit) — see
    ``test_resolver_docker_excluded_exact_match_abstains``.

    What THIS test pins (unchanged, and the reason it stays): the retracted
    Object is gone from every leg, the exclusion is NARROW (a live match and
    a ``deprecated`` match still resolve), and the assembled lane still
    FIRES on a non-excluded subject — the last assertion is driven with the
    LIVE ``dog bed`` because that is what proves the exclusion did not become
    an over-broad abort or data filter. The couch question's own outcome is
    now asserted to be an ABSTENTION, the #4061 contract.
    """
    _ag.build_base_graph(_docker_sdk)
    proj = _docker_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:'couch'}) SET o.status='retracted'")
    couch_id = proj.g.query(
        "MATCH (o:Object {name:'couch'}) RETURN o.id").result_set[0][0]
    from tortoise.assembly import (
        collect_slices,
        docker_resolver_port,
        docker_walker_port,
    )
    port = docker_resolver_port(_docker_sdk)

    # leg 1 — exact (name / id): the retracted Object is not returned
    assert couch_id not in {r["id"] for r in port.exact_objects(["couch"])}

    # leg 2 — name-FTS (the leg search_engine's exclusion is Point-gated on):
    # no other Object in this fixture bears a 'couch' name token
    assert port.fts_objects("couch") == []

    # leg 3 — alias amplifier (anchored search_keys)
    assert couch_id not in {r["id"] for r in port.alias_objects("couch")}

    # end-to-end through the ladder: no term returns the retracted Object —
    # including the paraphrase that only the FTS/alias legs could match
    for term in ("the couch", "couch", "the ikea purchase"):
        got = resolve_subjects(port, [term],
                               shape=AssemblyShape.CURRENT_STATE)
        assert couch_id not in {c.object_id for c in got.candidates}, term

    # LIVE control: the exclusion must not be over-broad
    live = resolve_subjects(port, ["the dog bed"],
                            shape=AssemblyShape.CURRENT_STATE)
    assert [(c.name, c.source) for c in live.candidates] == \
        [("dog bed", "exact")]

    # boundary of the decision: a status OUTSIDE the excluded set keeps
    # resolving and renders itself verbatim (never "current") — pinned here
    # so widening the set later cannot pass silently
    proj.g.query("MATCH (o:Object {name:'sofa'}) SET o.status='deprecated'")
    dep = resolve_subjects(port, ["the sofa"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert [(c.name, c.source) for c in dep.candidates] == \
        [("sofa", "exact")]
    dep_slices = collect_slices(
        docker_walker_port(_docker_sdk),
        [_cand(dep.candidates[0].object_id, "sofa", 0)],
        shape=AssemblyShape.CURRENT_STATE)
    assert [r["status"] for r in dep_slices.state_rows] == ["deprecated"]

    # the assembled lane still FIRES — pinned because a bare
    # `couch_id not in …` of `subjects=()` would also pass under an over-broad
    # exclusion (either a data filter or an abort). #4061 moved the
    # demonstration to a LIVE subject (the couch question now correctly
    # abstains — asserted below), so the anti-over-broad intent is preserved
    # without pinning the retired substitution.
    from tortoise.assembly import _assemble_connected
    block = _assemble_connected(
        _docker_sdk, "what is the current status of the dog bed?")
    assert block.fired is True
    assert couch_id not in {s["object_id"] for s in block.subjects}
    # #4061: the excluded exact match ABSTAINS — ask() no longer fires about
    # a subject the question did not name
    couch_block = _assemble_connected(
        _docker_sdk, "what is the current status of the couch?")
    assert couch_block.fired is False


@_docker_only
def test_resolver_docker_excluded_exact_match_abstains(_docker_sdk):
    """#4061 R1 (integration): with ``couch`` retracted, ``the couch`` names an
    excluded Object — the term ABSTAINS instead of resolving the live ``sofa``
    through a weaker leg, and ``_assemble_connected`` does not fire about a
    subject the question never named.

    Deliberately NOT embedder-pinned: the shipped configuration is exactly
    where the substitution was observed, and the assertion holds under both
    the sparse and hybrid FTS legs (``sofa`` is the substitute either way).
    """
    _ag.build_base_graph(_docker_sdk)
    proj = _docker_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:'couch'}) SET o.status='retracted'")
    couch_id = proj.g.query(
        "MATCH (o:Object {name:'couch'}) RETURN o.id").result_set[0][0]
    sofa_id = proj.g.query(
        "MATCH (o:Object {name:'sofa'}) RETURN o.id").result_set[0][0]
    from tortoise.assembly import _assemble_connected, docker_resolver_port, resolve_subjects
    port = docker_resolver_port(_docker_sdk)

    # the DISTINGUISHING probe sees the excluded exact match ...
    assert couch_id in {r["id"] for r in
                        port.excluded_exact_objects(["the couch", "couch"])}
    # ... while the live leg does not (so the two are genuinely distinct)
    assert couch_id not in {r["id"] for r in
                            port.exact_objects(["the couch", "couch"])}

    res = resolve_subjects(port, ["the couch"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert res.candidates == ()
    assert res.unresolved == ("the couch",)
    assert res.both_halves_ok(AssemblyShape.CURRENT_STATE) is False

    block = _assemble_connected(
        _docker_sdk, "what is the current status of the couch?")
    assert block.fired is False
    assert block.subjects == ()
    assert sofa_id not in {s["object_id"] for s in block.subjects}

    # LIVE control: an unexcluded term still resolves and fires
    live = resolve_subjects(port, ["the dog bed"],
                            shape=AssemblyShape.CURRENT_STATE)
    assert [(c.name, c.source) for c in live.candidates] == \
        [("dog bed", "exact")]


@_docker_only
def test_resolver_docker_fts_survives_retracted_crowd(_docker_sdk):
    """#4061 R2 (integration): more excluded Objects than the leg's ``limit``
    rank ahead of one live Object; the live candidate must still come back.

    The exclusion is applied BY the FTS query (pre-LIMIT), so the crowd can
    never consume the leg's bound — the old window-growth dance existed only
    because the SDK's all-or-nothing ``include_terminal`` opt-in could not
    express this port's narrower ``{retracted}`` set.
    """
    proj = _docker_sdk._get_proj()
    qid = "q4061r2"
    from tortoise.sdk import _entity_name_id

    live_name = "widget variant zz"
    variant_ids = [_entity_name_id("Object", f"widget variant {i}")
                   for i in range(10)]
    # Non-vacuity premise: with the live Object sorting BEHIND the crowd
    # under the ``id ASC`` tie-break, a post-truncation filter (the pre-#4061
    # shape) would return nothing — so this test cannot pass against the
    # regression it guards.
    assert _entity_name_id("Object", live_name) > max(variant_ids), (
        "the fixture's premise: the live Object must sort BEHIND the crowd "
        "under the id tie-break, or the crowd no longer fills the bound")
    for i in range(10):
        name = f"widget variant {i}"
        _docker_sdk.create_entity("object", name, objectKind="core:other",
                                  lme_question_id=qid, is_episodic=True)
        proj.g.query("MATCH (o:Object {name:$n}) SET o.status='retracted'",
                     params={"n": name})
    _docker_sdk.create_entity("object", live_name, objectKind="core:other",
                              lme_question_id=qid, is_episodic=True)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    got = port.fts_objects("widget", 8)
    assert [r["name"] for r in got] == [live_name]


@_docker_only
def test_resolver_docker_fts_status_is_read_by_the_query(_docker_sdk):
    """#4061 R3 (integration): a retracted Object is excluded by the FTS
    query's own WHERE, and a genuinely status-less (live) Object is still
    admitted — matching the Cypher legs' ``o.status IS NULL OR …`` admission.

    There is no SDK payload whose ``status`` could go missing, so the old
    ``_excluded_object_ids`` re-read is gone.
    """
    _ag.build_base_graph(_docker_sdk)
    proj = _docker_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:'couch'}) SET o.status='retracted'")
    proj.g.query("MATCH (o:Object {name:'sofa'}) REMOVE o.status")
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    assert port.fts_objects("couch", 8) == []
    sid = proj.g.query(
        "MATCH (o:Object {name:'sofa'}) RETURN o.id").result_set[0][0]
    assert port.fts_objects("sofa", 8) == [{"id": sid, "name": "sofa"}]


@_docker_only
def test_walker_explicit_id_renders_retracted_status_verbatim(_docker_sdk):
    """#3317 decision pin: the walker's state read is NOT a resolution leg.

    Given an id EXPLICITLY (the WalkerPort contract), the state row carries
    ``status`` VERBATIM; the renderer (``_state_header_hit``) then emits
    ``STATE (couch): retracted``. Adding a status conjunct to
    ``docker_walker_port.state_rows`` would not stop a retracted Object from
    being resolved (resolution has already happened) — it would only erase
    the honest state line of an admitted subject. The guard lives upstream,
    at the resolver legs.
    """
    _ag.build_base_graph(_docker_sdk)
    proj = _docker_sdk._get_proj()
    proj.g.query("MATCH (o:Object {name:'couch'}) SET o.status='retracted'")
    oid = proj.g.query(
        "MATCH (o:Object {name:'couch'}) RETURN o.id").result_set[0][0]
    from tortoise.assembly import collect_slices, docker_walker_port
    slices = collect_slices(
        docker_walker_port(_docker_sdk), [_cand(oid, "couch", 0)],
        shape=AssemblyShape.CURRENT_STATE)
    assert [r["status"] for r in slices.state_rows] == ["retracted"]


@_docker_only
def test_resolver_docker_embedder_on_unresolved_keeps_legacy(
        _docker_sdk, force_embedder_on):
    """#3223 (indicator 1): the R1 ``unresolved`` contract asserted with the
    embedder DETERMINISTICALLY ON — the configuration that actually ships.

    ``force_embedder_on`` replaces the ambient HF cache with a stand-in
    embedder, so this test RUNS in every lane instead of silently skipping or
    XPASSing — the hygiene failure #3275 filed against the
    ``xfail(strict=False)`` record this replaces. The no-match term must stay
    in ``unresolved`` even though the hybrid vector half returns the fixture's
    Objects for any term: leg 2 is the Object NAME-FTS leg and returns the
    index's rows only, so a semantic-only neighbour is not a name match.

    The ask-lane half is asserted too: the term must drive
    ``_assemble_connected`` to ``fired=False`` (the R1 legacy fallback)
    instead of firing an assembly about unrelated Objects.
    """
    _ag.build_base_graph(_docker_sdk)
    # PRECONDITION: the hybrid leg is genuinely live — its semantic half DOES
    # return Objects for a term that names none. Without this the test could
    # pass vacuously on a lane where leg 2 happens to be sparse.
    raw = _docker_sdk.tortoise_fts_query(
        "the teleporting exercise bike", entity_type="object", limit=8,
        include_terminal=True) or []
    assert raw, ("embedder-on lane did not exercise the hybrid leg — the "
                 "precondition of the #3223 divergence")
    from tortoise.assembly import (
        _assemble_connected,
        docker_resolver_port,
    )
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the teleporting exercise bike"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert res.candidates == ()
    assert res.unresolved == ("the teleporting exercise bike",)
    assert res.both_halves_ok(AssemblyShape.CURRENT_STATE) is False

    block = _assemble_connected(
        _docker_sdk,
        "what is the current status of the teleporting exercise bike?")
    assert block.fired is False
    assert block.subjects == ()


@_docker_only
def test_resolver_docker_embedder_on_name_match_survives_vector_crowd(
        _docker_sdk, force_embedder_on):
    """#3223 (the POSITIVE direction): with the embedder deterministically
    ON — the configuration that ships — a term whose NAME the Object index
    matches must still resolve even when the hybrid vector half ranks many
    unrelated Objects ahead of it.

    This is the direction BOTH gated designs failed: the hybrid's
    top-``limit`` is vector-weighted, so a name match ranked outside that
    window was gone before any post-hoc filter could see it (a predicate
    filter, or an id-filtered index intersection over the returned rows).
    The leg now queries the NAME index directly, so the crowd cannot
    displace it.

    The precondition asserts the crowding is real on THIS lane, so the test
    cannot pass vacuously: the name match must be absent from the hybrid's
    top-``limit``.
    """
    _ag.build_base_graph(_docker_sdk)
    target = "zorblax4 widgetfoo4"
    qid = "q3223crowd"
    _docker_sdk.create_entity("object", target, objectKind="core:other",
                              lme_question_id=qid, is_episodic=True)
    for i in range(200):
        _docker_sdk.create_entity("object", f"filler item {i}",
                                  objectKind="core:other",
                                  lme_question_id=qid, is_episodic=True)
    term = "please locate the zorblax4 widgetfoo4 somewhere near the garage"
    # PRECONDITION: the hybrid leg is live AND its top-`limit` is entirely
    # vector crowd — the window that swallowed the name match.
    raw = _docker_sdk.tortoise_fts_query(
        term, entity_type="object", limit=8, include_terminal=True) or []
    assert raw, ("embedder-on lane did not exercise the hybrid leg — the "
                 "precondition of the #3223 crowding")
    assert target not in [h.get("content") for h in raw], (
        "the fixture no longer crowds the name match out of the hybrid "
        "window, so this test would pass vacuously")
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    got = port.fts_objects(term, 8)
    assert target in [r["name"] for r in got], (
        "the NAME index matches this Object, so the leg must return it "
        "regardless of what the hybrid vector half ranks above it")


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 4 — typed walker + slice builder (R2/R3-8/R12, R17 P3-1/P3-3/
# P3-6). RED→GREEN: collect_slices / AssemblySlices / WalkerPort /
# docker_walker_port are new symbols in tortoise/assembly.py.
# ══════════════════════════════════════════════════════════════════════════

from tortoise.assembly import (  # noqa: E402
    _state_entry_date,
    collect_slices,
    docker_walker_port,
)


class _DictWalkerPort:
    """In-memory WalkerPort over {object_id: {state, points, events}}."""

    def __init__(self, graph):
        self._g = graph
        self.state_calls = 0

    def state_rows(self, object_ids):
        self.state_calls += 1
        out = []
        for oid in object_ids:
            o = self._g.get(oid)
            if o:
                out.append(dict(o["state"]))
        return out

    def spine_rows(self, object_ids, per_subject_cap=200):
        out = []
        for oid in object_ids:
            o = self._g.get(oid) or {}
            rows = list(o.get("points", [])) + list(o.get("events", []))
            out.extend(rows[:per_subject_cap])
        return out


def _fixture_walker_port():
    return _DictWalkerPort({
        "obj-couch": {
            "state": {"object_id": "obj-couch", "name": "couch",
                      "status": "superseded", "superseded_by": "sofa",
                      "superseded_at": "2026-09-01T00:00:00Z"},
            "points": [
                {"object_id": "obj-couch", "kind": "point",
                 "id": "pA-couch-bought", "content": "bought the grey couch",
                 "when": "2026-08-10", "created_at": "2026-08-10",
                 "status": "live", "ep_alpha": 8.0, "ep_beta": 1.5},
                {"object_id": "obj-couch", "kind": "point",
                 "id": "pX-undated", "content": "undated couch note",
                 "when": None, "created_at": "1970-01-01T00:00:00Z",
                 "status": "live", "ep_alpha": None, "ep_beta": None},
            ],
            "events": [],
        },
        "obj-dogbed": {
            "state": {"object_id": "obj-dogbed", "name": "dog bed",
                      "status": "live", "superseded_by": None,
                      "superseded_at": None},
            "points": [
                {"object_id": "obj-dogbed", "kind": "point",
                 "id": "pA-dogbed-chewed", "content": "dog chewed the bed",
                 "when": None, "created_at": "2026-08-10",
                 "status": "live", "ep_alpha": 6.0, "ep_beta": 2.0},
            ],
            "events": [],
        },
    })


def _cand(oid, name, idx):
    from tortoise.assembly import SubjectCandidate
    return SubjectCandidate(subject_index=idx, object_id=oid, name=name,
                            confidence="high", source="exact")


def test_walker_state_slice_single_statement_and_statuses():
    """State slice: status/supersededBy/supersededAt read in ONE statement
    (mock-port pins the single-statement read — never N reads); the
    superseded couch carries its full fold state + pinned supersededAt."""
    port = _fixture_walker_port()
    res = collect_slices(port,
                         [_cand("obj-couch", "couch", 0),
                          _cand("obj-dogbed", "dog bed", 1)],
                         shape=AssemblyShape.ORDERING)
    assert port.state_calls == 1, "state slice must be a SINGLE statement"
    by_id = {r["object_id"]: r for r in res.state_rows}
    assert by_id["obj-couch"]["status"] == "superseded"
    assert by_id["obj-couch"]["superseded_by"] == "sofa"
    assert by_id["obj-couch"]["superseded_at"] == "2026-09-01T00:00:00Z"
    assert by_id["obj-dogbed"]["status"] == "live"


def test_walker_date_ladder_tiers_and_undated_last():
    """R2 ladder: when (parseable) → tier when; createdAt (sentinel-stripped)
    → tier created; NO usable date → tier undated. Spine rows carry tier +
    a normalized date; dated rows sort before undated rows."""
    port = _fixture_walker_port()
    res = collect_slices(port,
                         [_cand("obj-couch", "couch", 0),
                          _cand("obj-dogbed", "dog bed", 1)],
                         shape=AssemblyShape.ORDERING)
    by_id = {r["id"]: r for r in res.timeline_rows}
    assert by_id["pA-couch-bought"]["tier"] == "when"
    assert by_id["pA-dogbed-chewed"]["tier"] == "created"
    assert by_id["pX-undated"]["tier"] == "undated"
    assert by_id["pX-undated"]["date"] is None
    # CHRONOLOGICAL order: dated rows nondecreasing by date, undated LAST
    # (date-major, NOT tier-major — sparse-when graphs mix tiers routinely)
    dated = [r for r in res.timeline_rows if r["date"] is not None]
    dates = [r["date"] for r in dated]
    assert dates == sorted(dates), dates
    assert res.timeline_rows[-1]["id"] == "pX-undated"
    # admission metadata
    assert res.admission["rows_requested"] >= len(res.timeline_rows)
    assert res.admission["truncated"] is False


def test_walker_malformed_when_falls_through_ladder():
    """Malformed `when` + a REAL (non-sentinel) createdAt → tier created —
    NEVER undated while a usable date exists; malformed when + sentinel
    createdAt → undated. Never a raise."""
    port = _fixture_walker_port()
    port._g["obj-garbage"] = {
        "state": {"object_id": "obj-garbage", "name": "garbage",
                  "status": "live"},
        "points": [
            {"object_id": "obj-garbage", "kind": "point",
             "id": "pG1", "content": "garbage when + valid created",
             "when": "not-a-real-date-2026", "created_at": "2026-08-01",
             "status": "live", "ep_alpha": None, "ep_beta": None},
            {"object_id": "obj-garbage", "kind": "point",
             "id": "pG2", "content": "garbage when + sentinel created",
             "when": "not-a-real-date-2026",
             "created_at": "1970-01-01T00:00:00Z",
             "status": "live", "ep_alpha": None, "ep_beta": None},
        ],
        "events": [],
    }
    res = collect_slices(port, [_cand("obj-garbage", "garbage", 0)],
                         shape=AssemblyShape.CURRENT_STATE)
    by_id = {r["id"]: r for r in res.timeline_rows}
    assert by_id["pG1"]["tier"] == "created", "usable createdAt must date it"
    assert by_id["pG2"]["tier"] == "undated"
    assert by_id["pG1"]["date"] is not None


@_docker_only
def test_walker_events_hosted_variant_started_tier(_docker_sdk):
    """Hosted-variant Event-aboutObject edges → Event spine rows dated by
    startedAt (tier started). Base graph (zero Event edges) → no event rows."""
    _ag.build_base_graph(_docker_sdk)
    _ag.build_hosted_variant(_docker_sdk)
    # resolve couch + dog bed through the real lane
    from tortoise.assembly import docker_resolver_port, resolve_subjects
    rport = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(rport, ["the couch", "the dog bed"],
                           shape=AssemblyShape.ORDERING)
    slices = collect_slices(docker_walker_port(_docker_sdk),
                            list(res.candidates),
                            shape=AssemblyShape.ORDERING)
    kinds = {r["kind"] for r in slices.timeline_rows}
    assert "event" in kinds
    ev_rows = [r for r in slices.timeline_rows if r["kind"] == "event"]
    assert ev_rows and all(r["tier"] == "started" for r in ev_rows)
    assert all(r["date"] is not None for r in ev_rows)


@_docker_only
def test_walker_base_graph_zero_event_rows(_docker_sdk):
    """Base v2-lane graph: Points only (zero Event-aboutObject edges) — the
    spine assembles from point rows alone (tier when/created), no event rows,
    no eventId join (R2 vacuous on the eval lane)."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port, resolve_subjects
    rport = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(rport, ["the couch"], shape=AssemblyShape.CURRENT_STATE)
    slices = collect_slices(docker_walker_port(_docker_sdk),
                            list(res.candidates),
                            shape=AssemblyShape.CURRENT_STATE)
    assert all(r["kind"] == "point" for r in slices.timeline_rows)
    assert all(not r.get("event_id") for r in slices.timeline_rows)
    assert slices.state_rows and slices.state_rows[0]["name"] == "couch"


def test_walker_hub_cap_binds_at_query():
    """R12/C8: a hub subject with rows > the per-slice pre-fetch cap →
    rows_requested == cap, truncated True, rows_admitted < total. The cap
    binds AT THE QUERY (the port never receives rows beyond cap)."""
    port = _fixture_walker_port()
    port._g["obj-hub"] = {
        "state": {"object_id": "obj-hub", "name": "hub", "status": "live"},
        "points": [
            {"object_id": "obj-hub", "kind": "point", "id": f"pH{i}",
             "content": f"hub note {i}", "when": None,
             "created_at": f"2026-0{(i % 9) + 1}-05", "status": "live",
             "ep_alpha": None, "ep_beta": None}
            for i in range(10)],
        "events": [],
    }
    res = collect_slices(port, [_cand("obj-hub", "hub", 0)],
                         shape=AssemblyShape.CURRENT_STATE,
                         per_subject_cap=4)
    assert res.admission["rows_requested"] == 4
    assert res.admission["truncated"] is True
    assert len(res.timeline_rows) == 4
    assert res.admission["rows_admitted"] <= 4


# ── Task-4 acceptance pins: as-of, terminal-points, tiebreak, interleave ──

def test_norm_date_utc_truncation_host_independent():
    """Shared helper contract (second-model P2-3): aware instants truncate
    in UTC regardless of the machine zone — near-day-boundary full-ISO
    values map identically on every host (2026-06-10T23:30:00Z → 2026-06-10;
    rollover 2026-06-11T00:30:00Z → 2026-06-11). Sentinel/garbage → None."""
    from tortoise.assembly import _norm_date
    assert _norm_date("2026-06-10T23:30:00Z").isoformat() == "2026-06-10"
    assert _norm_date("2026-06-11T00:30:00Z").isoformat() == "2026-06-11"
    assert _norm_date("2026-09-01T00:00:00Z").isoformat() == "2026-09-01"
    assert _norm_date("2026-09-01T02:00:00+02:00").isoformat() == "2026-09-01"
    assert _norm_date("2026-06-10").isoformat() == "2026-06-10"
    assert _norm_date("1970-01-01T00:00:00Z") is None
    assert _norm_date("not-a-date") is None
    assert _norm_date(None) is None and _norm_date("") is None


def test_walker_as_of_window_excludes_post_d_keeps_boundary():
    """As-of window: rows dated AFTER question_date excluded; the
    EQUALITY-DAY boundary (date-only row == question_date) is RETAINED
    (UTC-truncated comparison); undated rows admitted undated-last."""
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"].extend([
        {"object_id": "obj-couch", "kind": "point", "id": "pB-sold",
         "content": "sold couch on the boundary day", "when": "2026-09-01",
         "created_at": "2026-09-01", "status": "live",
         "ep_alpha": None, "ep_beta": None},
        {"object_id": "obj-couch", "kind": "point", "id": "pB-later",
         "content": "post-D couch event", "when": "2026-09-05",
         "created_at": "2026-09-05", "status": "live",
         "ep_alpha": None, "ep_beta": None},
    ])
    res = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                         shape=AssemblyShape.CURRENT_STATE,
                         question_date="2026-09-01T00:00:00Z")
    ids = [r["id"] for r in res.timeline_rows]
    assert "pB-sold" in ids, "equality-day row MUST be retained (UTC date)"
    assert "pB-later" not in ids, "post-D row must be excluded"
    assert ids[-1] == "pX-undated", "undated retained undated-LAST"
    # state slice is as-of-agnostic here (supersession header math is Task 5)
    assert res.state_rows[0]["superseded_at"] == "2026-09-01T00:00:00Z"


def test_walker_terminal_points_retained_distinct_from_object_tuple():
    """TERMINAL-POINT semantics pinned SEPARATELY from the Object-status
    tuple: a retracted POINT stays in the spine (tier + status intact) and
    in evidence_rows — the Object exclusion ({superseded,...}) is NEVER
    applied to Points (Task 5 renders the D8 marker)."""
    port = _fixture_walker_port()
    port._g["obj-dogbed"]["points"].append(
        {"object_id": "obj-dogbed", "kind": "point", "id": "pRetracted",
         "content": "dog bed note later retracted", "when": None,
         "created_at": "2026-08-11", "status": "retracted",
         "ep_alpha": None, "ep_beta": None})
    res = collect_slices(port, [_cand("obj-dogbed", "dog bed", 0)],
                         shape=AssemblyShape.CURRENT_STATE)
    by_id = {r["id"]: r for r in res.timeline_rows}
    assert "pRetracted" in by_id
    assert by_id["pRetracted"]["status"] == "retracted"
    assert by_id["pRetracted"]["tier"] == "created"
    assert any(r["id"] == "pRetracted" for r in res.evidence_rows)
    # the superseded OBJECT (couch) is a DIFFERENT slice (state_rows)
    res2 = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                          shape=AssemblyShape.CURRENT_STATE)
    assert res2.state_rows[0]["status"] == "superseded"


def test_walker_same_date_tiebreak_deterministic():
    """R17 P3-6: two same-date rows (cross-subject) order by object_id then
    id; two collect_slices calls on the same port yield byte-identical
    timeline id order."""
    port = _fixture_walker_port()
    # same when-date on both subjects; also same-date same-subject
    port._g["obj-couch"]["points"].append(
        {"object_id": "obj-couch", "kind": "point", "id": "pB1",
         "content": "b1", "when": "2026-09-01", "created_at": "2026-09-01",
         "status": "live", "ep_alpha": None, "ep_beta": None})
    port._g["obj-dogbed"]["points"].append(
        {"object_id": "obj-dogbed", "kind": "point", "id": "pB2",
         "content": "b2", "when": "2026-09-01", "created_at": "2026-09-01",
         "status": "live", "ep_alpha": None, "ep_beta": None})
    res1 = collect_slices(port,
                          [_cand("obj-couch", "couch", 0),
                           _cand("obj-dogbed", "dog bed", 1)],
                          shape=AssemblyShape.ORDERING)
    res2 = collect_slices(port,
                          [_cand("obj-couch", "couch", 0),
                           _cand("obj-dogbed", "dog bed", 1)],
                          shape=AssemblyShape.ORDERING)
    ids1 = [r["id"] for r in res1.timeline_rows]
    ids2 = [r["id"] for r in res2.timeline_rows]
    assert ids1 == ids2, "two calls must be byte-identical"
    # cross-subject same-date: obj-couch ('obj-couch') < obj-dogbed, so
    # the couch's pB1 comes before dogbed's pB2
    assert ids1.index("pB1") < ids1.index("pB2")


def test_walker_torn_read_interleave_no_crash():
    """cycle-2 P2: a #2242 fold injected BETWEEN the state read and the
    spine read renders deterministically (whichever window won) — no crash,
    each slice reflects its own read window."""
    port = _fixture_walker_port()
    orig_spine = port.spine_rows

    def _interleaved_spine(object_ids, per_subject_cap=200):
        # the fold lands mid-walk: couch flips to live + a new point appears
        port._g["obj-couch"]["state"]["status"] = "live"
        port._g["obj-couch"]["state"]["superseded_by"] = None
        port._g["obj-couch"]["points"].append(
            {"object_id": "obj-couch", "kind": "point", "id": "pMid",
             "content": "written after the fold", "when": None,
             "created_at": "2026-09-02", "status": "live",
             "ep_alpha": None, "ep_beta": None})
        return orig_spine(object_ids, per_subject_cap)

    port.spine_rows = _interleaved_spine
    res = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                         shape=AssemblyShape.CURRENT_STATE)
    # state read happened BEFORE the fold → superseded; spine read AFTER →
    # includes pMid. Deterministic per-window render, no crash.
    assert res.state_rows[0]["status"] == "superseded"
    assert any(r["id"] == "pMid" for r in res.timeline_rows)


def test_walker_cap_backstop_on_faithful_port():
    """The python-side per-subject cap is a real backstop for ports that
    return more than cap rows (the docker lane caps AT THE QUERY; the stub
    here returns ALL rows so the drop branch actually executes)."""
    port = _fixture_walker_port()
    port._g["obj-hub"] = {
        "state": {"object_id": "obj-hub", "name": "hub", "status": "live"},
        "points": [
            {"object_id": "obj-hub", "kind": "point", "id": f"pH{i}",
             "content": f"hub note {i}", "when": None,
             "created_at": f"2026-0{(i % 9) + 1}-05", "status": "live",
             "ep_alpha": None, "ep_beta": None}
            for i in range(10)],
        "events": [],
    }
    # faithful port: NO pre-slicing (returns all 10)
    class _Faithful(_DictWalkerPort):
        def spine_rows(self, object_ids, per_subject_cap=200):
            out = []
            for oid in object_ids:
                o = self._g.get(oid) or {}
                out.extend(o.get("points", []))
                out.extend(o.get("events", []))
            return out

    res = collect_slices(_Faithful(port._g), [_cand("obj-hub", "hub", 0)],
                         shape=AssemblyShape.CURRENT_STATE,
                         per_subject_cap=4)
    assert len(res.timeline_rows) == 4
    assert res.admission["rows_requested"] == 4
    assert res.admission["truncated"] is True
    assert res.admission["rows_admitted"] == 4


@_docker_only
def test_walker_docker_state_round_trip_superseded_at(_docker_sdk):
    """The docker state Cypher's positional mapping is pinned: couch state
    row round-trips status/supersededBy/supersededAt through the real lane
    (supersededAt == the fixture's pinned 2026-09-01T00:00:00Z)."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port, docker_walker_port, resolve_subjects
    rport = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(rport, ["the couch"], shape=AssemblyShape.CURRENT_STATE)
    slices = collect_slices(docker_walker_port(_docker_sdk),
                            list(res.candidates),
                            shape=AssemblyShape.CURRENT_STATE)
    row = slices.state_rows[0]
    assert row["name"] == "couch"
    assert row["status"] == "superseded"
    assert row["superseded_by"] == "sofa"
    assert row["superseded_at"] == "2026-09-01T00:00:00Z"


@_docker_only
def test_walker_docker_hub_cap_binds_per_subject(_docker_sdk):
    """R12/C8 live: build_hub_graph (60 points) walked at per_subject_cap 20
    → truncated True, rows_admitted == 20 < 60; and a two-subject walk keeps
    each subject's rows independent (hub capped at 20 does NOT starve the
    quiet dog-bed subject)."""
    _ag.build_hub_graph(_docker_sdk, n_points=60)
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port, docker_walker_port, resolve_subjects
    rport = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(rport, ["the hub-subject", "the dog bed"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is True, \
        "both halves must resolve"
    by_idx = {c.subject_index: c for c in res.candidates}
    hub_oid = by_idx[0].object_id
    dogbed_oid = by_idx[1].object_id
    slices = collect_slices(docker_walker_port(_docker_sdk),
                            list(res.candidates),
                            shape=AssemblyShape.ORDERING,
                            per_subject_cap=20)
    assert slices.admission["truncated"] is True
    hub_rows = [r for r in slices.timeline_rows
                if r["object_id"] == hub_oid]
    dogbed_rows = [r for r in slices.timeline_rows
                   if r["object_id"] == dogbed_oid]
    # hub kept EXACTLY its cap (ORDER BY id deterministic per subject);
    # the quiet co-subject still received ALL its rows — never starved
    assert len(hub_rows) == 20
    assert slices.admission["rows_admitted"] == 20 + len(dogbed_rows)
    assert len(dogbed_rows) == 2, \
        "base graph dog bed: pA-dogbed-chewed + pA-vet (2 anchored points)"
# ══════════════════════════════════════════════════════════════════════════
# #2655 — the as-of EQUALITY-DAY lookup over the typed spine.
# RED→GREEN: collect_day_slices / WalkerPort.day_rows are new symbols in
# tortoise/assembly.py. The port's own predicate is a SUPERSET probe; the
# day membership is decided HERE on the R2 ladder date.
# ══════════════════════════════════════════════════════════════════════════

from datetime import date as _py_date  # noqa: E402

from tortoise.assembly import collect_day_slices  # noqa: E402
from tortoise.fanout import PER_ENTITY_FANOUT_CAP as _FANOUT_CAP  # noqa: E402


class _DayStubPort:
    """Minimal day-rows port: returns whatever the test hands it, so every
    equality/ladder/cap branch executes (never a faithful re-implementation
    of the docker predicate)."""

    def __init__(self, rows):
        self._rows = rows
        self.calls: list[tuple] = []

    def day_rows(self, days, per_day_cap):
        self.calls.append((tuple(days), per_day_cap))
        return list(self._rows)


def _day_row(rid, *, when=None, created_at=None, started_at=None,
             kind="point", content=None):
    row = {"kind": kind, "id": rid, "content": content or rid}
    if when is not None:
        row["when"] = when
    if created_at is not None:
        row["created_at"] = created_at
    if started_at is not None:
        row["started_at"] = started_at
    return row


def test_day_slice_is_an_equality_not_a_range():
    port = _DayStubPort([
        _day_row("p-day", created_at="2026-09-01"),
        _day_row("p-before", created_at="2026-08-31"),
        _day_row("p-after", created_at="2026-09-02"),
    ])
    res = collect_day_slices(port, (_py_date(2026, 9, 1),))
    assert [r["id"] for r in res.timeline_rows] == ["p-day"]
    assert res.admission["rows_admitted"] == 1
    assert res.admission["rows_requested"] == _FANOUT_CAP  # one day


def test_day_slice_utc_truncation_and_full_iso_boundary():
    """A full ISO instant truncates to its UTC date (the SAME shared ladder
    the subject walk uses) — a 23:30Z instant is that day, never the next."""
    port = _DayStubPort([
        _day_row("p-late", created_at="2026-09-01T23:30:00Z"),
        _day_row("p-next", created_at="2026-09-02T00:30:00Z"),
    ])
    res = collect_day_slices(port, (_py_date(2026, 9, 1),))
    assert [r["id"] for r in res.timeline_rows] == ["p-late"]


def test_day_slice_undated_rows_are_excluded():
    """Unlike the subject walk (undated-last), a day EQUALITY can never be
    satisfied by an undated row — keeping it would render an unrelated row
    under a DATE (…) header, i.e. fabricate a date claim."""
    port = _DayStubPort([
        _day_row("p-undated", created_at="1970-01-01T00:00:00Z"),
        _day_row("p-missing"),
        _day_row("p-day", created_at="2026-09-01"),
    ])
    res = collect_day_slices(port, (_py_date(2026, 9, 1),))
    assert [r["id"] for r in res.timeline_rows] == ["p-day"]


def test_day_slice_ladder_priority_beats_the_port_predicate():
    """`when` is authoritative when it parses: a row whose `when` says another
    day is EXCLUDED even though the port matched it on `createdAt`; and a row
    whose `when` is garbage but whose `createdAt` IS the day is INCLUDED (the
    parse-gated fall-through the ladder exists to provide)."""
    port = _DayStubPort([
        _day_row("p-shadowed", when="2026-09-05", created_at="2026-09-01"),
        _day_row("p-fallback", when="not-a-date", created_at="2026-09-01"),
    ])
    res = collect_day_slices(port, (_py_date(2026, 9, 1),))
    by_id = {r["id"]: r for r in res.timeline_rows}
    assert set(by_id) == {"p-fallback"}
    assert by_id["p-fallback"]["tier"] == "created"


def test_day_slice_event_rows_use_the_started_tier():
    port = _DayStubPort([
        _day_row("ev-day", kind="event", started_at="2026-09-01"),
        _day_row("ev-other", kind="event", started_at="2026-08-01"),
    ])
    res = collect_day_slices(port, (_py_date(2026, 9, 1),))
    assert [r["id"] for r in res.timeline_rows] == ["ev-day"]
    assert res.timeline_rows[0]["tier"] == "started"
    # evidence view = point-kind rows only (the subject walk's rule)
    assert res.evidence_rows == ()


def test_day_slice_dedupes_by_id_and_is_input_order_independent():
    rows = [
        _day_row("p2", created_at="2026-09-01"),
        _day_row("p1", created_at="2026-09-01"),
        _day_row("p2", created_at="2026-09-01"),
    ]
    a = collect_day_slices(_DayStubPort(rows), (_py_date(2026, 9, 1),))
    b = collect_day_slices(_DayStubPort(list(reversed(rows))),
                           (_py_date(2026, 9, 1),))
    assert [r["id"] for r in a.timeline_rows] == ["p1", "p2"]
    assert [r["id"] for r in b.timeline_rows] == ["p1", "p2"]


def test_day_slice_two_day_window_orders_by_date():
    port = _DayStubPort([
        _day_row("sun", created_at="2026-05-21"),
        _day_row("sat", created_at="2026-05-20"),
    ])
    res = collect_day_slices(
        port, (_py_date(2026, 5, 20), _py_date(2026, 5, 21)))
    assert [r["id"] for r in res.timeline_rows] == ["sat", "sun"]
    assert res.admission["rows_requested"] == _FANOUT_CAP * 2


def test_day_slice_per_day_cap_applies_post_filter():
    """A row dated ELSEWHERE must not consume the day's budget: the port may
    over-fetch (its prefix predicate is a superset probe), and the cap is
    applied only to rows that actually belong to the day."""
    rows = [_day_row("off-day", created_at="2026-08-01")]
    rows += [_day_row(f"p{i}", created_at="2026-09-01") for i in range(5)]
    res = collect_day_slices(_DayStubPort(rows), (_py_date(2026, 9, 1),),
                             per_day_cap=3)
    assert [r["id"] for r in res.timeline_rows] == ["p0", "p1", "p2"]
    assert res.admission["truncated"] is True
    assert res.admission["rows_requested"] == 3


def test_day_slice_no_days_is_empty_and_never_calls_the_port():
    port = _DayStubPort([_day_row("p", created_at="2026-09-01")])
    assert collect_day_slices(port, ()).timeline_rows == ()
    assert collect_day_slices(port, (None,)).timeline_rows == ()
    assert port.calls == []


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 5 — renderer: synthesized hits, date-sorted sections,
# deterministic ordering/diff (R1/R2/R3-5/R6/R12, R17 P2-1). RED→GREEN:
# synthesize_hits is a new symbol in tortoise/assembly.py. The synthesized
# dicts are what assemble_context/_render_block render unchanged in Task 6 —
# the EXACT content strings pinned here become Task 6's byte-goldens.
# ══════════════════════════════════════════════════════════════════════════

from tortoise.assembly import AssemblySlices, synthesize_hits  # noqa: E402


def _rfixture_current():
    """couch (superseded by sofa, pinned 2026-09-01) + one dated sold-point
    (09-01) + pX-undated; sofa is NOT among the resolved subjects."""
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"].append(
        {"object_id": "obj-couch", "kind": "point", "id": "pB-sold",
         "content": "sold the old couch and ordered a new sofa instead",
         "when": "2026-09-01", "created_at": "2026-09-01", "status": "live",
         "valid_from": "2026-09-01", "ep_alpha": 9.0, "ep_beta": 1.0,
         "quote": "sold the old couch", "search_keys": "couch sold sofa",
         "event_id": None, "lme_session_index": 1})
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.CURRENT_STATE)
    return slices, port


def test_render_current_state_header_and_spine():
    """current-state: the state-header hit embeds the label
    'STATE (couch): superseded by sofa on 2026-09-01' (sofa VERIFIED via
    successors_verified — the fired path probes successor existence in
    Task 6); dated spine follows chronologically (undated last); real point
    rows keep their canonical id; synthesized rows carry NO id."""
    slices, _ = _rfixture_current()
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           successors_verified={"sofa"})
    assert hits and hits[0]["content"] == \
        "STATE (couch): superseded by sofa on 2026-09-01", \
        f"header golden drifted: {hits[0]['content']!r}"
    assert "id" not in hits[0], \
        "synthesized state rows carry NO id (why.enrich skips them)"
    point_hits = [h for h in hits if h.get("id")]
    ids = [h["id"] for h in point_hits]
    assert "pB-sold" in ids
    assert point_hits[-1]["id"] == "pX-undated", \
        "undated real row must stay LAST"
    # real rows must NOT expose a point_id key (W4-OUTPUT-only) nor the
    # pure walker derivation keys tier/date; object_id is an inert
    # passthrough (inert keys never reach the rendered line)
    for h in point_hits:
        assert "point_id" not in h and "tier" not in h \
            and "date" not in h, h.keys()


def test_render_state_row_superseded_by_dict_shaped():
    """R17 P2-1: the state-header hit carries dict-shaped superseded_by
    (never a flat string); empty snippet keeps the rendered line clean (the
    content label is authoritative) while the shape survives for W4."""
    slices, _ = _rfixture_current()
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           successors_verified={"sofa"})
    sb = hits[0].get("superseded_by")
    assert isinstance(sb, dict) and not isinstance(sb, str), sb
    assert sb.get("content_snippet") == "sofa", sb


def test_render_ordering_line_and_sections():
    """ordering: line computed THROUGH the shared date helper (couch +
    dog bed share the earliest day 2026-08-10 -> the order-independent tie
    phrase); per-subject sectioning keeps the subjects separate; both
    subjects present even when one has an undated row."""
    from datetime import date as _d

    from tortoise.assembly import _norm_date
    assert _norm_date("2026-08-10T23:30:00Z") == _d(2026, 8, 10)
    # fresh walk over BOTH subjects (the compare fired path resolves both)
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"].append(
        {"object_id": "obj-couch", "kind": "point", "id": "pB-sold",
         "content": "sold the old couch and ordered a new sofa instead",
         "when": "2026-09-01", "created_at": "2026-09-01", "status": "live",
         "ep_alpha": 9.0, "ep_beta": 1.0, "quote": "sold the old couch",
         "search_keys": "couch sold sofa", "event_id": None,
         "lme_session_index": 1})
    cands = [_cand("obj-couch", "couch", 0),
             _cand("obj-dogbed", "dog bed", 1)]
    slices = collect_slices(port, cands, shape=AssemblyShape.ORDERING)
    hits = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                           candidates=cands)
    # couch + dog bed first-known on the SAME day -> the order-INDEPENDENT
    # tie phrase (P2-1 review: never a word-order-dependent winner claim)
    assert hits[0]["content"] == \
        "couch and dog bed both appeared on 2026-08-10", \
        f"ordering golden drifted: {hits[0]['content']!r}"
    by_oid = {}
    for h in hits:
        oid = h.get("object_id")
        if oid:
            by_oid.setdefault(oid, []).append(h)
    # per-subject sectioning: couch rows in one group, dog-bed rows in the
    # other — the undated couch row sorts LAST within the couch section
    assert by_oid["obj-couch"] and by_oid["obj-dogbed"]
    couch_ids = [h["id"] for h in by_oid["obj-couch"]]
    assert couch_ids[-1] == "pX-undated"


# ── #2653: N-ary ordering — the N-way chain over the SAME dated spine ─────

_D = __import__("datetime").date  # the #2653 fixtures' date constructor


def _nary_slices(dated):
    """AssemblySlices for an N-subject ordering render. ``dated`` is a list
    of (object_id, name, date|None) in candidate order."""
    return AssemblySlices(
        state_rows=tuple(
            {"object_id": o, "name": n, "status": "live",
             "superseded_by": None, "superseded_at": None}
            for o, n, _dt in dated),
        timeline_rows=tuple(
            {"object_id": o, "kind": "point", "id": f"p-{o}",
             "content": f"event for {n}", "date": dt,
             "tier": "dated" if dt is not None else "undated"}
            for o, n, dt in dated),
        evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})


_THREE = [("obj-a", "couch", _D(2026, 8, 10)),
          ("obj-b", "dog bed", _D(2026, 8, 14)),
          ("obj-c", "bookshelf", _D(2026, 9, 1))]
_THREE_TERMS = ["the couch", "the dog bed", "the bookshelf"]


def test_render_nary_ordering_chain():
    """#2653: 3 named options render ONE deterministic chain over each
    subject's earliest dated row (the binary path's own anchor) — the whole
    answer to "which came first, A, B or C"."""
    cands = [_cand(o, n, i) for i, (o, n, _d) in enumerate(_THREE)]
    hits = synthesize_hits(_nary_slices(_THREE),
                           shape=AssemblyShape.ORDERING, candidates=cands,
                           halves=_THREE_TERMS)
    assert hits[0]["kind"] == "ordering"
    assert hits[0]["content"] == (
        "couch came first on 2026-08-10, then dog bed on 2026-08-14, "
        "then bookshelf on 2026-09-01"), f"chain drifted: {hits[0]['content']!r}"


def test_render_nary_ordering_chain_is_question_order_independent():
    """Determinism: the chain is a property of the DATED SPINE, not of the
    candidate order the resolver happened to return (a subject shuffle yields
    zero delta — the binary path's matched-control contract)."""
    base = [_cand(o, n, i) for i, (o, n, _d) in enumerate(_THREE)]
    shuffled = [_cand("obj-c", "bookshelf", 0), _cand("obj-a", "couch", 1),
                _cand("obj-b", "dog bed", 2)]
    first = synthesize_hits(_nary_slices(_THREE),
                            shape=AssemblyShape.ORDERING, candidates=base,
                            halves=_THREE_TERMS)[0]["content"]
    second = synthesize_hits(_nary_slices(_THREE),
                             shape=AssemblyShape.ORDERING,
                             candidates=shuffled,
                             halves=_THREE_TERMS)[0]["content"]
    assert first == second


def test_render_nary_ordering_tie_group_then_rest():
    """Same-day tie inside an N-ary chain: the tie group is named in OBJECT
    ID order (phrasing-independent canon, as in the binary tie phrase), then
    the later members follow."""
    dated = [("obj-a", "couch", _D(2026, 8, 10)),
             ("obj-b", "dog bed", _D(2026, 8, 10)),
             ("obj-c", "bookshelf", _D(2026, 9, 1))]
    cands = [_cand(o, n, i) for i, (o, n, _d) in enumerate(dated)]
    hits = synthesize_hits(_nary_slices(dated),
                           shape=AssemblyShape.ORDERING, candidates=cands,
                           halves=_THREE_TERMS)
    assert hits[0]["content"] == (
        "couch and dog bed both appeared on 2026-08-10, then bookshelf on "
        "2026-09-01"), f"tie chain drifted: {hits[0]['content']!r}"


def test_render_nary_undated_subject_suppresses_the_chain():
    """Fail-closed: the FULL order is claimed ONLY when every subject is
    dated — a chain silently missing a member would state a confidently wrong
    order. The per-subject sections still render (the evidence is not lost)."""
    dated = [("obj-a", "couch", _D(2026, 8, 10)),
             ("obj-b", "dog bed", _D(2026, 8, 14)),
             ("obj-c", "bookshelf", None)]
    cands = [_cand(o, n, i) for i, (o, n, _d) in enumerate(dated)]
    hits = synthesize_hits(_nary_slices(dated),
                           shape=AssemblyShape.ORDERING, candidates=cands,
                           halves=_THREE_TERMS)
    assert all(h.get("kind") != "ordering" for h in hits), hits
    # the sections still carry every subject's rows
    assert {h.get("object_id") for h in hits if h.get("object_id")} == {
        "obj-a", "obj-b", "obj-c"}


def test_both_halves_ok_is_all_terms_for_the_nary_width():
    """R1 (#2653): the N-ary width comes from the EXTRACTED term count —
    all N subjects must resolve or the shape fires nothing. Omitted
    ``n_terms`` keeps the legacy two-half contract (existing callers)."""
    from tortoise.assembly import ResolveResult
    three = ResolveResult(candidates=tuple(
        _cand(o, n, i) for i, (o, n, _d) in enumerate(_THREE)))
    assert three.both_halves_ok(AssemblyShape.ORDERING, n_terms=3) is True
    # a 4th term with no candidate -> all-N fails (no partial admit)
    assert three.both_halves_ok(AssemblyShape.ORDERING, n_terms=4) is False
    # one unresolved term -> fails whatever the width
    partial = ResolveResult(
        candidates=tuple(_cand(o, n, i)
                         for i, (o, n, _d) in enumerate(_THREE[:2])),
        unresolved=("the bookshelf",))
    assert partial.both_halves_ok(AssemblyShape.ORDERING, n_terms=3) is False
    # the legacy two-half contract is unchanged when n_terms is omitted
    assert three.both_halves_ok(AssemblyShape.ORDERING) is False
    assert ResolveResult(candidates=tuple(
        _cand(o, n, i)
        for i, (o, n, _d) in enumerate(_THREE[:2]))
    ).both_halves_ok(AssemblyShape.ORDERING) is True


def test_render_interval_diff_via_shared_helper():
    """interval: the diff line uses the SHARED _norm_date helper (same date
    semantics as the as-of boundary): buying (2026-08-10) -> selling
    (2026-09-01) = 22 days; both halves resolve to the SAME object (the
    canary 'how many days between buying the couch and selling the couch'),
    so the window is the object's story span (min..max dated row)."""
    from datetime import date as _d

    from tortoise.assembly import _norm_date
    assert (_d(2026, 9, 1) - _d(2026, 8, 10)).days == 22
    assert _norm_date("2026-08-10T23:30:00Z") == _d(2026, 8, 10)
    slices, _ = _rfixture_current()  # couch dated rows 08-10 + 09-01
    hits = synthesize_hits(slices, shape=AssemblyShape.INTERVAL,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           halves=["buying the couch", "selling the couch"])
    assert hits[0]["content"] == \
        "22 days between buying the couch and selling the couch", \
        f"interval golden drifted: {hits[0]['content']!r}"


def test_render_duration_state_entry_anchor_and_asof_span():
    """#2654: the duration-state render states the two anchors it used. The
    graph end is the subject's state entry — the ObjectRegistered stamp or
    the earliest dated row, whichever is EARLIER (this fixture carries no
    stamp, so the status-start fallback supplies 2026-08-10) — and the other
    end is the caller's as-of date."""
    from datetime import date as _d

    slices, _ = _rfixture_current()
    assert _state_entry_date(slices, "obj-couch") == _d(2026, 8, 10)
    hits = synthesize_hits(slices, shape=AssemblyShape.DURATION_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           question_date="2026-09-10")
    lines = [h["content"] for h in hits if h["kind"] == "duration-state"]
    assert lines == [
        "STATE ENTRY (couch): 2026-08-10",
        "31 days from couch state entry (2026-08-10) to the as-of date "
        "(2026-09-10)",
    ], lines


def test_render_duration_state_entry_takes_earlier_of_stamp_and_rows():
    """#2654: the entry is min(stamp, earliest dated row). The v2 lane's
    ObjectRegistered stamp is the INGEST clock, so a recorded row MUST be
    able to pull the anchor EARLIER — and a late stamp must never push it
    later. (The Book-Lovers-Unite shape: registered at ingest 2026-09-20,
    the membership recorded 2026-05-07.)"""
    from datetime import date as _d

    g = {"obj-club": {
        "state": {"object_id": "obj-club", "name": "Book Lovers Unite",
                  "status": "live", "superseded_by": None,
                  "superseded_at": None,
                  "created_at": "2026-09-20T00:00:00Z"},   # ingest clock
        "points": [{"object_id": "obj-club", "kind": "point",
                    "id": "p-join", "content": "joined the club",
                    "when": "2026-05-07", "created_at": "2026-05-07",
                    "status": "live"}],
        "events": []}}
    cands = [_cand("obj-club", "Book Lovers Unite", 0)]
    slices = collect_slices(_DictWalkerPort(g), cands,
                            shape=AssemblyShape.DURATION_STATE)
    assert _state_entry_date(slices, "obj-club") == _d(2026, 5, 7)
    hits = synthesize_hits(slices, shape=AssemblyShape.DURATION_STATE,
                           candidates=cands, question_date="2026-05-28")
    lines = [h["content"] for h in hits if h["kind"] == "duration-state"]
    assert lines == [
        "STATE ENTRY (Book Lovers Unite): 2026-05-07",
        "21 days from Book Lovers Unite state entry (2026-05-07) to the "
        "as-of date (2026-05-28)",
    ], lines
    # the anchor can never be dated AFTER the as-of: an entry outside the
    # question's window is NOT an entry, so the whole duration-state pair is
    # suppressed (the row dates are already as-of-windowed by the walker —
    # only the ObjectRegistered stamp can fall outside it)
    hits2 = synthesize_hits(slices, shape=AssemblyShape.DURATION_STATE,
                            candidates=cands, question_date="2026-05-01")
    assert [h["content"] for h in hits2
            if h["kind"] == "duration-state"] == []


def test_render_duration_state_honest_absence():
    """#2654 honest-absence: no usable entry -> NO anchor line (never a
    fabricated date); no as-of date -> the anchor line alone (the span is
    never computed against an invented 'today')."""
    g = {"obj-x": {"state": {"object_id": "obj-x", "name": "mystery",
                              "status": "live", "superseded_by": None,
                              "superseded_at": None},
                   "points": [{"object_id": "obj-x", "kind": "point",
                               "id": "p-u", "content": "undated",
                               "when": None,
                               "created_at": "1970-01-01T00:00:00Z",
                               "status": "live"}],
                   "events": []}}
    cands = [_cand("obj-x", "mystery", 0)]
    slices = collect_slices(_DictWalkerPort(g), cands,
                            shape=AssemblyShape.DURATION_STATE)
    assert _state_entry_date(slices, "obj-x") is None
    hits = synthesize_hits(slices, shape=AssemblyShape.DURATION_STATE,
                           candidates=cands, question_date="2026-09-10")
    assert [h["content"] for h in hits
            if h["kind"] == "duration-state"] == []
    # dated subject, no as-of supplied
    slices2, _ = _rfixture_current()
    hits2 = synthesize_hits(slices2, shape=AssemblyShape.DURATION_STATE,
                            candidates=[_cand("obj-couch", "couch", 0)])
    assert [h["content"] for h in hits2
            if h["kind"] == "duration-state"] == \
        ["STATE ENTRY (couch): 2026-08-10"]
    # the ingest-clock leak the as-of guard exists for: the subject has NO
    # dated row, so its only candidate date is the ObjectRegistered stamp —
    # and that stamp is the v2 lane's INGEST time, AFTER the historical
    # as-of. An entry outside the window is not an entry: no anchor line.
    g2 = {"obj-club": {
        "state": {"object_id": "obj-club", "name": "Book Lovers Unite",
                  "status": "live", "superseded_by": None,
                  "superseded_at": None,
                  "created_at": "2026-09-20T00:00:00Z"},  # the ingest clock
        "points": [{"object_id": "obj-club", "kind": "point",
                    "id": "p-u", "content": "joined the club",
                    "when": None,
                    "created_at": "1970-01-01T00:00:00Z",
                    "status": "live"}],
        "events": []}}
    cands2 = [_cand("obj-club", "Book Lovers Unite", 0)]
    slices3 = collect_slices(_DictWalkerPort(g2), cands2,
                             shape=AssemblyShape.DURATION_STATE)
    assert _state_entry_date(slices3, "obj-club") is not None  # stamp only
    hits3 = synthesize_hits(slices3, shape=AssemblyShape.DURATION_STATE,
                            candidates=cands2, question_date="2023-05-28")
    assert [h["content"] for h in hits3
            if h["kind"] == "duration-state"] == []


def test_render_same_name_collision_two_sections():
    """R12/C7: two same-named entities (distinct ids) render as SEPARATE
    state headers labeled with the object id — never merged."""
    dup = [{"object_id": "obj-a", "name": "bike", "status": "live",
            "superseded_by": None, "superseded_at": None},
           {"object_id": "obj-b", "name": "bike", "status": "live",
            "superseded_by": None, "superseded_at": None}]
    slices = AssemblySlices(state_rows=tuple(dup), timeline_rows=(),
                            evidence_rows=(),
                            admission={"rows_requested": 0,
                                       "rows_admitted": 0,
                                       "truncated": False})
    cands = [_cand("obj-a", "bike", 0), _cand("obj-b", "bike", 1)]
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=cands)
    labels = [h["content"] for h in hits]
    assert any("(bike #obj-a)" in c for c in labels), labels
    assert any("(bike #obj-b)" in c for c in labels), labels
    # live rows render the LIVE label (no fabricated date/successor)
    assert all("superseded by" not in c and "on " not in c
               for c in labels), labels


def test_render_live_state_header_label():
    """A LIVE state row renders 'STATE (name): live' — the reader sees the
    subject IS current (the 'as of now' semantics live in the label)."""
    slices = AssemblySlices(
        state_rows=({"object_id": "obj-d", "name": "dog bed",
                     "status": "live", "superseded_by": None,
                     "superseded_at": None},),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-d", "dog bed", 0)])
    assert hits[0]["content"] == "STATE (dog bed): live", hits


def test_render_successor_absent_name_only_annotation():
    """R12/C6(a): supersededBy names a successor that resolves to ZERO
    visible nodes (never created) → NAME-ONLY annotation (no fabricated
    date/evidence line, no fabricated content_snippet beyond the name)."""
    slices = AssemblySlices(
        state_rows=({"object_id": "obj-orphan", "name": "orphan-src",
                     "status": "superseded",
                     "superseded_by": "successor-never-created",
                     "superseded_at": "2026-09-01T00:00:00Z"},),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-orphan",
                                             "orphan-src", 0)])
    content = hits[0]["content"]
    assert "STATE (orphan-src): superseded" in content
    assert "successor-never-created" in content
    assert "no successor record found" in content, content


def test_render_long_successor_verified_keys_on_full_name_not_display():
    """#5370: a >200-char successor name is truncated FOR DISPLAY only.

    ``successors_verified`` is keyed on the stored FULL name (the ask-path
    probe's name set is the raw ``supersededBy`` values), so truncating
    BEFORE the membership test made a verified successor look unverified and
    the renderer emitted the NAME-ONLY "no successor record found"
    annotation for a successor that exists and is live. The lookup must run
    on the full name; only the rendered clause may truncate.
    """
    long_name = "gh-issue-title-" + ("y" * 240)
    assert len(long_name) > 200
    slices = AssemblySlices(
        state_rows=(({"object_id": "obj-long", "name": "long-src",
                      "status": "superseded",
                      "superseded_by": long_name,
                      "superseded_at": "2026-09-01T00:00:00Z"}),),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-long", "long-src", 0)],
                           successors_verified=frozenset({long_name}))
    content = hits[0]["content"]
    assert "no successor record found" not in content, content
    assert content == (
        f"STATE (long-src): superseded by {long_name[:200]}… "
        f"on 2026-09-01"), content
    # the structured field stays the DISPLAY-bounded name (the probe key is
    # full, but the reader/marker text is kept bounded by _MAX_SUCC_NAME).
    assert hits[0]["superseded_by"] == {
        "content_snippet": long_name[:200] + "…"}, hits[0]["superseded_by"]


def test_render_torn_row_empty_superseded_by():
    """R12/C6(b): status='superseded' with EMPTY supersededBy (hand-written
    /torn row) → 'successor unknown' annotation; the EMPTY value must not
    fabricate a link and must not flip retrieval_degraded at Task 6 (the
    fired path passes [] to the D8 gate)."""
    slices = AssemblySlices(
        state_rows=({"object_id": "obj-torn", "name": "torn-row",
                     "status": "superseded", "superseded_by": "",
                     "superseded_at": "2026-09-01T00:00:00Z"},),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-torn", "torn-row", 0)])
    content = hits[0]["content"]
    assert "STATE (torn-row): superseded" in content
    assert "successor unknown" in content, content
    sb = hits[0].get("superseded_by") or {}
    assert not (sb.get("content_snippet") or ""), \
        "no fabricated snippet on a torn row"


def test_render_terminal_point_retained_with_status():
    """TERMINAL POINT rows stay in the render (D8 carrier — legacy parity);
    their status rides the dict for assemble_context/W4, never dropped from
    a current-state render."""
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"].append(
        {"object_id": "obj-couch", "kind": "point", "id": "pRetr",
         "content": "an older note later retracted", "when": None,
         "created_at": "2026-08-11", "status": "retracted",
         "ep_alpha": None, "ep_beta": None})
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.CURRENT_STATE)
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)])
    p_hits = [h for h in hits if h.get("id") == "pRetr"]
    assert p_hits and p_hits[0].get("status") == "retracted"


def test_render_superseded_by_truncated_not_fabricated():
    """R12/C6: a >200-char supersededBy name is annotated truncated, never
    fabricated into a date/evidence line."""
    long_name = "z" * 250
    slices = AssemblySlices(
        state_rows=({"object_id": "obj-l", "name": "long-row",
                     "status": "superseded", "superseded_by": long_name,
                     "superseded_at": "2026-09-01T00:00:00Z"},),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-l", "long-row", 0)])
    content = hits[0]["content"]
    assert "z" * 200 in content and "z" * 250 not in content
    assert len(content) < 320


# ── Task-5 closing pins (reviewer cycles: matched controls, dedupe, ──────
#    near-midnight shared-helper math, honest-key passthrough, S1/S4) ────

def test_render_ordering_near_midnight_full_iso_shared_helper():
    """Ordering line math THROUGH the shared helper for full-ISO rows near
    midnight: couch when=2026-08-10T23:30:00Z vs dogbed created_at=
    2026-08-09T23:59:59Z -> dog bed came first on 2026-08-09 (UTC DATE
    truncation — NOT 08-10/08-10, which a local-zone naive date() would
    produce on western hosts). Expected value computed THROUGH _norm_date
    inside the test body."""
    from datetime import date as _d

    from tortoise.assembly import _norm_date
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"] = [
        {"object_id": "obj-couch", "kind": "point", "id": "pMid",
         "content": "couch event near midnight", "when": "2026-08-10T23:30:00Z",
         "created_at": "2026-08-10T23:30:00Z", "status": "live",
         "ep_alpha": None, "ep_beta": None}]
    port._g["obj-dogbed"]["points"] = [
        {"object_id": "obj-dogbed", "kind": "point", "id": "pD",
         "content": "dog bed event", "when": None,
         "created_at": "2026-08-09T23:59:59Z", "status": "live",
         "ep_alpha": None, "ep_beta": None}]
    cands = [_cand("obj-couch", "couch", 0),
             _cand("obj-dogbed", "dog bed", 1)]
    slices = collect_slices(port, cands, shape=AssemblyShape.ORDERING)
    hits = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                           candidates=cands)
    expected = ("dog bed came first on "
                f"{_norm_date('2026-08-09T23:59:59Z').isoformat()}")
    assert hits[0]["content"] == expected, hits[0]["content"]
    # rollover row is NOT truncated to the wrong day
    assert _norm_date("2026-08-10T23:30:00Z") == _d(2026, 8, 10)


def test_render_interval_expected_through_shared_helper():
    """Interval expected value computed THROUGH _norm_date (never an
    independent date-lib subtraction): 2026-09-01 - 2026-08-10T23:30:00Z."""
    from tortoise.assembly import _norm_date
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"] = [
        {"object_id": "obj-couch", "kind": "point", "id": "pA1",
         "content": "bought", "when": "2026-08-10T23:30:00Z",
         "created_at": "2026-08-10T23:30:00Z", "status": "live",
         "ep_alpha": None, "ep_beta": None},
        {"object_id": "obj-couch", "kind": "point", "id": "pA2",
         "content": "sold", "when": "2026-09-01", "created_at": "2026-09-01",
         "status": "live", "ep_alpha": None, "ep_beta": None}]
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.INTERVAL)
    hits = synthesize_hits(slices, shape=AssemblyShape.INTERVAL,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           halves=["buying the couch", "selling the couch"])
    days = (_norm_date("2026-09-01") - _norm_date("2026-08-10T23:30:00Z")).days
    assert days == 22  # UTC-truncated 2026-09-01 minus 2026-08-10
    assert hits[0]["content"] == (f"{days} days between buying the couch "
                                  f"and selling the couch"), hits[0]


def test_render_interval_single_dated_instance_no_line():
    """Degenerate window (ONE dated row instance) suppresses the interval
    line — never a fabricated '0 days' (sparse `when` is the design)."""
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"] = [
        {"object_id": "obj-couch", "kind": "point", "id": "pOnly",
         "content": "only dated event", "when": "2026-08-10",
         "created_at": "2026-08-10", "status": "live",
         "ep_alpha": None, "ep_beta": None},
        {"object_id": "obj-couch", "kind": "point", "id": "pUnd",
         "content": "undated companion", "when": None,
         "created_at": "1970-01-01T00:00:00Z", "status": "live",
         "ep_alpha": None, "ep_beta": None}]
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.INTERVAL)
    hits = synthesize_hits(slices, shape=AssemblyShape.INTERVAL,
                           candidates=[_cand("obj-couch", "couch", 0)],
                           halves=["buying the couch", "selling the couch"])
    assert not hits or hits[0].get("kind") != "interval", hits[:1]
    # section rows still render (undated last)
    assert [h["id"] for h in hits if h.get("id")][-1] == "pUnd"


def test_render_order_shuffle_matched_control():
    """Matched control: identical slices rendered under swapped candidate
    order produce the SAME order-INDEPENDENT tie phrase (zero delta), and
    re-running yields byte-identical content."""
    port = _fixture_walker_port()
    cands_ab = [_cand("obj-couch", "couch", 0),
                _cand("obj-dogbed", "dog bed", 1)]
    cands_ba = [_cand("obj-dogbed", "dog bed", 0),
                _cand("obj-couch", "couch", 1)]
    slices = collect_slices(port, cands_ab, shape=AssemblyShape.ORDERING)
    ab = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                         candidates=cands_ab)
    ab2 = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                          candidates=cands_ab)
    ba = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                         candidates=cands_ba)
    # couch + dog bed same earliest (08-10): the TIE phrase renders and is
    # CANDIDATE-ORDER-INDEPENDENT — a matched-control shuffle yields ZERO
    # delta (never a word-order-dependent winner)
    assert ab[0]["content"] == \
        "couch and dog bed both appeared on 2026-08-10"
    assert ab2[0]["content"] == ab[0]["content"]
    assert ba[0]["content"] == ab[0]["content"]


def test_render_superseded_state_matched_delta():
    """+superseded matched control: identical state minus the supersession
    clause renders the LIVE label; adding superseded_by flips ONLY the
    header clause + dict shape (no line-count drift)."""
    def _hits(superseded: bool):
        sr = {"object_id": "obj-c", "name": "couch",
              "status": "superseded" if superseded else "live",
              "superseded_by": "sofa" if superseded else None,
              "superseded_at": "2026-09-01T00:00:00Z" if superseded
              else None}
        slices = AssemblySlices(state_rows=(sr,), timeline_rows=(),
                                evidence_rows=(),
                                admission={"rows_requested": 0,
                                           "rows_admitted": 0,
                                           "truncated": False})
        return synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                               candidates=[_cand("obj-c", "couch", 0)],
                               successors_verified={"sofa"})
    live = _hits(False)
    sup = _hits(True)
    assert live[0]["content"] == "STATE (couch): live"
    assert sup[0]["content"] == ("STATE (couch): superseded by sofa on "
                               "2026-09-01")
    assert isinstance(sup[0]["superseded_by"], dict)
    assert len(live) == len(sup) == 1


def test_render_same_subject_duplicate_id_deduped():
    """Same-subject duplicate id (a re-read row) dedupes to ONE line."""
    port = _fixture_walker_port()
    dup = {"object_id": "obj-couch", "kind": "point", "id": "pDup",
           "content": "duplicated point", "when": "2026-08-11",
           "created_at": "2026-08-11", "status": "live",
           "ep_alpha": None, "ep_beta": None}
    port._g["obj-couch"]["points"].append(dup)
    port._g["obj-couch"]["points"].append(dict(dup))
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.CURRENT_STATE)
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)])
    n = sum(1 for h in hits if h.get("id") == "pDup")
    assert n == 1, n


def test_render_cross_subject_dedupe_per_section_not_global():
    """A point aboutObject-anchored to BOTH compare subjects renders once
    PER subject section (2 total — NOT globally deduped to 1): expected
    line count pinned. obj-couch section: pShared + pA-couch-bought;
    obj-dogbed section: pShared + pA-dogbed-chewed."""
    port = _fixture_walker_port()
    shared = {"object_id": None, "kind": "point", "id": "pShared",
              "content": "shared anchor point", "when": "2026-08-11",
              "created_at": "2026-08-11", "status": "live",
              "ep_alpha": None, "ep_beta": None}
    for oid in ("obj-couch", "obj-dogbed"):
        row = dict(shared)
        row["object_id"] = oid
        port._g[oid]["points"].append(row)
    cands = [_cand("obj-couch", "couch", 0),
             _cand("obj-dogbed", "dog bed", 1)]
    slices = collect_slices(port, cands, shape=AssemblyShape.ORDERING)
    hits = synthesize_hits(slices, shape=AssemblyShape.ORDERING,
                           candidates=cands)
    n_shared = sum(1 for h in hits if h.get("id") == "pShared")
    assert n_shared == 2, f"once per section, not global: {n_shared}"
    # 1 ordering line + couch section (pA-couch-bought, pShared,
    # pX-undated) + dogbed section (pA-dogbed-chewed, pShared) = 6 hits
    assert len(hits) == 6, [h.get("id") or h.get("content") for h in hits]
    # the dog-bed section's pShared proves per-section not global
    dogbed_ids = [h.get("id") for h in hits
                  if h.get("object_id") == "obj-dogbed"]
    assert dogbed_ids == ["pA-dogbed-chewed", "pShared"], dogbed_ids


def test_render_honest_key_passthrough_session_speaker():
    """Real rows pass honest session_date/speaker/validity keys through
    unchanged (the Task-6 decorate + byte-parity seam: _render_block reads
    lme_session_index/session_date/speaker/valid_from)."""
    port = _fixture_walker_port()
    port._g["obj-couch"]["points"] = [
        {"object_id": "obj-couch", "kind": "point", "id": "pSpk",
         "content": "told a friend about the couch", "when": None,
         "created_at": "2026-08-10", "status": "live",
         "valid_from": "2026-08-10", "ep_alpha": None, "ep_beta": None,
         "speaker": "user", "session_date": "2026-08-10",
         "lme_session_index": 3}]
    slices = collect_slices(port, [_cand("obj-couch", "couch", 0)],
                            shape=AssemblyShape.CURRENT_STATE)
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-couch", "couch", 0)])
    p_hits = [h for h in hits if h.get("id") == "pSpk"]
    assert p_hits and p_hits[0]["speaker"] == "user"
    assert p_hits[0]["session_date"] == "2026-08-10"
    assert p_hits[0]["valid_from"] == "2026-08-10"
    assert p_hits[0]["lme_session_index"] == 3


def test_render_recall_excluded_successor_name_only():
    """S1: a successor that EXISTS but is recall-excluded (the fired path's
    probe returns it excluded -> successors_verified empty) renders the
    NAME-ONLY annotation — the renderer cannot distinguish 'never created'
    from 'excluded' and must not fabricate either way."""
    slices = AssemblySlices(
        state_rows=({"object_id": "obj-c", "name": "couch",
                     "status": "superseded",
                     "superseded_by": "sofa",
                     "superseded_at": "2026-09-01T00:00:00Z"},),
        timeline_rows=(), evidence_rows=(),
        admission={"rows_requested": 0, "rows_admitted": 0,
                   "truncated": False})
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=[_cand("obj-c", "couch", 0)],
                           successors_verified=frozenset())
    content = hits[0]["content"]
    assert "STATE (couch): superseded by sofa" in content
    assert "no successor record found" in content, content
    assert " on 2026-09-01" not in content


def test_render_headers_follow_candidate_order_not_state_rows():
    """P2-1: CURRENT_STATE headers follow the CANDIDATE subject sequence,
    never the raw state_rows order (docker state query has no ORDER BY)."""
    sr_beta = {"object_id": "obj-beta", "name": "beta", "status": "live",
               "superseded_by": None, "superseded_at": None}
    sr_alpha = {"object_id": "obj-alpha", "name": "alpha", "status": "live",
                "superseded_by": None, "superseded_at": None}
    slices = AssemblySlices(state_rows=(sr_beta, sr_alpha),
                            timeline_rows=(), evidence_rows=(),
                            admission={"rows_requested": 0,
                                       "rows_admitted": 0,
                                       "truncated": False})
    cands = [_cand("obj-alpha", "alpha", 0), _cand("obj-beta", "beta", 1)]
    hits = synthesize_hits(slices, shape=AssemblyShape.CURRENT_STATE,
                           candidates=cands)
    assert [h["content"] for h in hits] == ["STATE (alpha): live",
                                            "STATE (beta): live"]


# ══════════════════════════════════════════════════════════════════════════
# #2655 — the date-scoped render: ONE synthesized header naming the RESOLVED
# day(s), then the day's rows (no resolved subject to head a section).
# ══════════════════════════════════════════════════════════════════════════

def _date_slices(rows):
    dated = []
    for r in rows:
        r = dict(r)
        r["date"] = _py_date.fromisoformat(r.get("when")
                                           or r.get("created_at"))
        r["tier"] = "when" if r.get("when") else "created"
        dated.append(r)
    dated.sort(key=lambda r: r["date"])
    return AssemblySlices(state_rows=(), timeline_rows=tuple(dated),
                          evidence_rows=tuple(dated),
                          admission={"rows_requested": 40,
                                     "rows_admitted": len(dated),
                                     "truncated": False})


def _anchor(label, *days):
    from tortoise.calendar_anchors import CalendarAnchor, CalendarAnchorKind
    return CalendarAnchor(label=label, kind=CalendarAnchorKind.FIXED_DATE,
                          days=tuple(days), surface=label)


def test_render_date_section_header_and_rows():
    slices = _date_slices([
        {"kind": "point", "id": "p2", "content": "second",
         "created_at": "2023-02-14"},
        {"kind": "event", "id": "ev1", "content": "an event",
         "when": "2023-02-14"},
        {"kind": "point", "id": "p1", "content": "first",
         "created_at": "2023-02-14"},
    ])
    hits = synthesize_hits(slices, shape=AssemblyShape.RELATIVE_DATE,
                           date_anchor=_anchor("valentine's day",
                                               _py_date(2023, 2, 14)))
    # header first, then rows in (date, kind, id) order — kind sorts before id
    # so the single event row leads the points
    assert hits[0] == {"content": "DATE (valentine's day): 2023-02-14",
                       "kind": "date"}
    assert [h.get("id") for h in hits[1:]] == ["ev1", "p1", "p2"]
    # the pure-walker derivation keys never leak into rendered hits
    for h in hits[1:]:
        assert "date" not in h and "tier" not in h


def test_render_date_section_names_the_two_day_weekend():
    slices = _date_slices([
        {"kind": "point", "id": "sat", "content": "sat",
         "created_at": "2023-05-20"},
        {"kind": "point", "id": "sun", "content": "sun",
         "created_at": "2023-05-21"},
    ])
    hits = synthesize_hits(
        slices, shape=AssemblyShape.RELATIVE_DATE,
        date_anchor=_anchor("the past weekend", _py_date(2023, 5, 20),
                            _py_date(2023, 5, 21)))
    assert hits[0]["content"] == (
        "DATE (the past weekend): 2023-05-20, 2023-05-21")
    assert [h.get("id") for h in hits[1:]] == ["sat", "sun"]


def test_render_date_section_is_order_shuffle_invariant():
    rows = [
        {"kind": "point", "id": f"p{i}", "content": f"n{i}",
         "created_at": "2023-02-14"}
        for i in range(5)
    ]
    a = synthesize_hits(_date_slices(rows),
                        shape=AssemblyShape.RELATIVE_DATE,
                        date_anchor=_anchor("valentine's day",
                                            _py_date(2023, 2, 14)))
    b = synthesize_hits(_date_slices(list(reversed(rows))),
                        shape=AssemblyShape.RELATIVE_DATE,
                        date_anchor=_anchor("valentine's day",
                                            _py_date(2023, 2, 14)))
    assert a == b


def test_render_date_section_empty_slices_render_nothing():
    """An empty day renders NOTHING — a bare header would replace a working
    legacy pool with a dateline (the caller's P1-1 fall-through gate)."""
    empty = AssemblySlices(admission={"rows_requested": 0, "rows_admitted": 0,
                                      "truncated": False})
    assert synthesize_hits(empty, shape=AssemblyShape.RELATIVE_DATE,
                           date_anchor=_anchor("valentine's day",
                                               _py_date(2023, 2, 14))) == []
    assert synthesize_hits(_date_slices([
        {"kind": "point", "id": "p", "created_at": "2023-02-14"}]),
        shape=AssemblyShape.RELATIVE_DATE, date_anchor=None) == []


# ══════════════════════════════════════════════════════════════════════════
# #2655 — the fired DECISION, hermetically. `_assemble_connected` is driven
# with the walker seam stubbed (no DB, no model): the day lookup, the
# empty-day fall-through (P1-1), and the missing-question_date abstention are
# all decided in pure code, so they are pinned here as well as in the
# docker-lane SDK tests.
# ══════════════════════════════════════════════════════════════════════════

class _FakeAskSDK:
    """The MINIMUM of the SDK seam `_assemble_connected` touches on the day
    path: the decoration call (id-keyed, fail-open) and the projection read
    behind the D8 marker block (also fail-open — it raises here on purpose, so
    the branch under test is proven to survive an unavailable projection)."""

    def annotate_ask_hits(self, hits):
        return hits

    def _get_proj(self):
        raise RuntimeError("no graph in this hermetic test")


def _drive_connected(monkeypatch, port, question, *, question_date):
    import tortoise.assembly as amod
    monkeypatch.setattr(amod, "docker_walker_port", lambda sdk: port)
    from tortoise.assembly import _assemble_connected
    return _assemble_connected(_FakeAskSDK(), question,
                               question_date=question_date)


def test_connected_fires_on_the_resolved_day_hermetically(monkeypatch):
    port = _DayStubPort([
        _day_row("p-sold", created_at="2026-09-01",
                 content="sold the old couch"),
        _day_row("p-bought", created_at="2026-08-10",
                 content="bought the grey couch"),
    ])
    block = _drive_connected(
        monkeypatch, port,
        "Who did I meet with during the lunch last Tuesday?",
        question_date="2026-09-07")
    assert block.fired is True
    assert block.shape == "relative-date-lookup"
    assert block.subjects == ()
    rendered = "\n".join(str(h.get("content") or "")
                        for h in block.post_cap_lines)
    assert "DATE (last tuesday): 2026-09-01" in rendered
    assert "sold the old couch" in rendered
    # the as-of equality: the 08-10 row belongs to another day
    assert "bought the grey couch" not in rendered
    assert block.admission["rows_admitted"] == 1


def test_connected_refuses_when_the_named_day_is_empty(monkeypatch):
    """P1-1 on the day path: an anchor that resolves to a day with NO rows
    must NOT fire — a bare dateline would replace a working legacy pool."""
    port = _DayStubPort([
        _day_row("p-other", created_at="2026-09-01", content="unrelated"),
    ])
    block = _drive_connected(
        monkeypatch, port,
        "What was the airline that I flied with on Valentine's day?",
        question_date="2026-09-10")   # 2026-02-14 has no rows
    assert block.fired is False
    assert block.shape is None
    assert block.post_cap_lines == []


def test_connected_refuses_without_a_question_date(monkeypatch):
    """The resolver never reads a clock: no ``question_date`` -> no anchor ->
    no fire, even though the day rows exist."""
    port = _DayStubPort([
        _day_row("p-sold", created_at="2026-09-01", content="sold the couch"),
    ])
    block = _drive_connected(
        monkeypatch, port,
        "Who did I meet with during the lunch last Tuesday?",
        question_date=None)
    assert block.fired is False
    assert block.shape is None


def test_connected_refuses_when_the_day_port_is_never_consulted(monkeypatch):
    """A non-anchor question must not reach the day walk at all — the
    resolver/dedup path is untouched for every other shape."""
    port = _DayStubPort([_day_row("p", created_at="2026-09-01")])
    block = _drive_connected(monkeypatch, port,
                             "What did I do two weeks ago?",
                             question_date="2026-09-10")
    assert block.fired is False
    assert port.calls == []


def _drive_subject_path(monkeypatch, resolver_port, walker_port, question, *,
                        question_date="2026-09-10"):
    """Drive `_assemble_connected`'s SUBJECT path with both graph seams
    stubbed — a DB-free regression pin for the #2655 refactor (the tail moved
    into the shared `_decorate_real_rows`/`_assemble_under_caps` helpers the
    #2652 window path uses); the subject pipeline must be unchanged."""
    import tortoise.assembly as amod
    monkeypatch.setattr(amod, "docker_resolver_port", lambda sdk: resolver_port)
    monkeypatch.setattr(amod, "docker_walker_port", lambda sdk: walker_port)
    from tortoise.assembly import _assemble_connected
    return _assemble_connected(_FakeAskSDK(), question,
                               question_date=question_date)


def test_connected_subject_path_unchanged_by_the_refactor(monkeypatch):
    """CURRENT_STATE through the real orchestration: resolve -> walk ->
    render -> the shared `_decorate_real_rows`/`_assemble_under_caps` tail
    the #2655 refactor extracted. The state header and the dated spine
    survive the tail extraction."""
    walker = _fixture_walker_port()
    resolver = _DictObjectPort([{"id": "obj-couch", "name": "couch"}])
    block = _drive_subject_path(
        monkeypatch, resolver, walker,
        "what is the current status of the couch?")
    assert block.fired is True
    assert block.shape == "current-state"
    assert [s["object_id"] for s in block.subjects] == ["obj-couch"]
    contents = [str(h.get("content") or "") for h in block.post_cap_lines]
    assert any(c.startswith("STATE (couch):") for c in contents), contents
    assert any("bought the grey couch" in c for c in contents), contents
    # the pure-walker derivation keys never reach the assembled hits
    assert all("tier" not in h and "date" not in h
               for h in block.post_cap_lines)


def test_connected_subject_path_two_subjects_ordering(monkeypatch):
    """ORDERING through the same orchestration: BOTH subjects must resolve
    (the R1 gate) and both halves reach the assembly."""
    walker = _fixture_walker_port()
    resolver = _DictObjectPort([
        {"id": "obj-couch", "name": "couch"},
        {"id": "obj-dogbed", "name": "dog bed"},
    ])
    block = _drive_subject_path(
        monkeypatch, resolver, walker,
        "which came first - the couch or the dog bed?")
    assert block.fired is True
    assert block.shape == "ordering"
    assert {s["object_id"] for s in block.subjects} == {"obj-couch",
                                                        "obj-dogbed"}
    contents = [str(h.get("content") or "") for h in block.post_cap_lines]
    assert any("came first on" in c or "both appeared on" in c
               for c in contents), contents


def test_connected_subject_path_one_half_unresolved_still_falls_through(
        monkeypatch):
    """The R1 gate survives the refactor: one unresolved half -> fire NOTHING
    (never a silently narrowed single-subject ordering)."""
    walker = _fixture_walker_port()
    resolver = _DictObjectPort([{"id": "obj-couch", "name": "couch"}])
    block = _drive_subject_path(
        monkeypatch, resolver, walker,
        "which came first - the couch or the hoverboard?")
    assert block.fired is False
    assert block.shape is None
    assert block.post_cap_lines == []


# ══════════════════════════════════════════════════════════════════════════
# #2652 — window walk + AGO_RELATIVE render (stub port; the docker-lane
# integration goldens live in tests/test_assembly_sdk.py)
# ══════════════════════════════════════════════════════════════════════════

from tortoise.assembly import (  # noqa: E402
    collect_window_slices,
)


class _DictWindowPort:
    """In-memory window port: rows is the raw ``window_rows`` reply."""

    def __init__(self, rows, *, raise_on_fetch=False):
        self.rows = rows
        self.calls = []
        self.raise_on_fetch = raise_on_fetch

    def window_rows(self, lo, hi, cap):
        self.calls.append((lo, hi, cap))
        if self.raise_on_fetch:
            raise RuntimeError("boom")
        return list(self.rows)


def _win_rows():
    return [
        {"kind": "point", "id": "p-in-when", "content": "in window by when",
         "when": "2026-08-10", "status": "live"},
        # junk `when` + REAL createdAt: the R2 ladder must place this row by
        # its real date, NOT drop it into the undated tier
        {"kind": "point", "id": "p-junk-when", "content": "junk when",
         "when": "garbage", "created_at": "2026-08-11", "status": "live"},
        {"kind": "point", "id": "p-outside", "content": "too late",
         "when": "2026-09-01", "status": "live"},
        {"kind": "point", "id": "p-undated", "content": "no date at all",
         "when": None, "created_at": "1970-01-01T00:00:00Z",
         "status": "live"},
        {"kind": "event", "id": "ev-1", "content": "an event",
         "started_at": "2026-08-10", "status": "live"},
    ]


def test_window_slices_tier_filter_and_undated_exclusion():
    """The window is a DATE-ARITHMETIC selector: rows are placed by the R2
    ladder's normalized date (a junk `when` with a real createdAt is dated,
    never dropped as undated), rows outside the window are excluded, and
    UNDATED rows are excluded too (an undated line cannot satisfy an
    elapsed-time window)."""
    anchor = resolve_relative_anchor("what did I do a month ago?", "2026-09-10")
    assert anchor.window_lo.isoformat() == "2026-07-26"
    assert anchor.window_hi.isoformat() == "2026-08-25"
    port = _DictWindowPort(_win_rows())
    slices = collect_window_slices(port, anchor)
    got = [r["id"] for r in slices.timeline_rows]
    # chronological, ties broken deterministically by id ('ev-1' < 'p-in-when')
    assert got == ["ev-1", "p-in-when", "p-junk-when"]
    assert [r["tier"] for r in slices.timeline_rows] == [
        "started", "when", "created"]
    assert [r["date"].isoformat() for r in slices.timeline_rows] == [
        "2026-08-10", "2026-08-10", "2026-08-11"]
    assert slices.admission["rows_admitted"] == 3
    # ONE batched fetch, bounded
    assert len(port.calls) == 1
    assert slices.state_rows == ()


def test_window_slices_recency_is_newest_first():
    """'most recently' answers about the LATEST entries, so its spine is
    ordered newest-first (the budget must hold the answer locus first)."""
    anchor = resolve_relative_anchor("Which streaming service did I start using most recently?",
                  "2026-09-10")
    assert anchor.newest_first is True
    slices = collect_window_slices(_DictWindowPort(_win_rows()), anchor)
    assert [r["date"].isoformat() for r in slices.timeline_rows] == [
        "2026-09-01", "2026-08-11", "2026-08-10", "2026-08-10"]
    assert next(r["id"] for r in slices.timeline_rows) == "p-outside"


def test_window_slices_count_form_is_newest_first_too():
    """The COUNT form ('how many weeks ago …') has the same answer locus:
    the elapsed time to the MOST RECENT occurrence. Its window is the widest
    (the ~400-day envelope) while the port fetches newest-first under its
    cap — so rendering oldest-first would hand the downstream budget the
    rows the fetch just deprioritised, and drop the answer."""
    anchor = resolve_relative_anchor(
        "How many weeks ago did I meet up with my aunt?", "2026-09-10")
    assert anchor.kind == "ago-count" and anchor.newest_first is True
    slices = collect_window_slices(_DictWindowPort(_win_rows()), anchor)
    assert [r["date"].isoformat() for r in slices.timeline_rows] == [
        "2026-09-01", "2026-08-11", "2026-08-10", "2026-08-10"]


def test_window_slices_port_raise_degrades_to_empty():
    """A raising port degrades to empty slices (never a raise) — the caller's
    empty-block guard then falls back to legacy."""
    anchor = resolve_relative_anchor("what did I do a month ago?", "2026-09-10")
    slices = collect_window_slices(_DictWindowPort([], raise_on_fetch=True),
                                   anchor)
    assert slices.timeline_rows == ()
    assert slices.admission["rows_admitted"] == 0
    # a port with no window leg at all is the same degrade
    class _NoLeg:
        pass
    assert collect_window_slices(_NoLeg(), anchor).timeline_rows == ()


def test_window_slices_truncation_is_per_leg_not_a_row_count():
    """`window_rows` issues TWO LIMIT-ed statements — points and events,
    each `cap` — so truncation is a PER-LEG verdict. `len(rows) >= cap`
    reported `truncated=True` for a window where NEITHER leg saturated
    (3 points + 1 event at cap 4 is 4 rows), and `rows_requested = cap`
    understated the walk (it asks for up to `2 * cap`)."""
    anchor = resolve_relative_anchor("what did I do a month ago?", "2026-09-10")

    def _rows(n, kind):
        dt = "when" if kind == "point" else "started_at"
        return [{"kind": kind, "id": f"{kind}{i}", "content": "c",
                 dt: "2026-08-10", "status": "live"} for i in range(n)]

    both_short = collect_window_slices(
        _DictWindowPort(_rows(3, "point") + _rows(1, "event")), anchor,
        per_window_cap=4)
    assert len(both_short.timeline_rows) == 4
    assert both_short.admission["rows_requested"] == 8
    assert both_short.admission["truncated"] is False
    # ... a saturated leg IS truncation, at the SAME admitted row count
    pts = collect_window_slices(_DictWindowPort(_rows(4, "point")), anchor,
                                per_window_cap=4)
    assert pts.admission["truncated"] is True
    evs = collect_window_slices(_DictWindowPort(_rows(4, "event")), anchor,
                                per_window_cap=4)
    assert evs.admission["truncated"] is True


def test_ago_render_window_header_and_elapsed_lines():
    """The fired AGO block: ONE window header + a deterministic elapsed line
    immediately before each dated row (`<date> — N days before <qd>`), so
    the reader gets the offset arithmetic the deterministic tier owns.
    Never a fabricated date: the header states the RANGE."""
    anchor = resolve_relative_anchor("what did I do a month ago?", "2026-09-10")
    slices = collect_window_slices(_DictWindowPort(_win_rows()), anchor)
    hits = synthesize_hits(slices, shape=AssemblyShape.AGO_RELATIVE,
                           question_date="2026-09-10", anchor=anchor)
    contents = [h["content"] for h in hits]
    assert contents[0] == ("WINDOW (2026-07-26 -> 2026-08-25): "
                           "1 month before 2026-09-10")
    # per-row offset lines + the untouched real rows (no `date`/`tier`
    # derivation keys leak into the render input)
    assert "2026-08-10 — 31 days before 2026-09-10" in contents
    assert "2026-08-11 — 30 days before 2026-09-10" in contents
    assert [h["kind"] for h in hits[:3]] == ["time-window", "ago-offset",
                                             "event"]
    for h in hits:
        assert "date" not in h and "tier" not in h


def test_ago_render_empty_window_returns_nothing():
    """A window with no dated row renders NOTHING (the caller's empty-block
    guard then falls back to legacy) — never a content-less window header
    replacing a working pool (#2165 P1-1)."""
    anchor = resolve_relative_anchor("what did I do a month ago?", "2026-09-10")
    empty = AssemblySlices(timeline_rows=(), evidence_rows=(),
                           admission={})
    assert synthesize_hits(empty, shape=AssemblyShape.AGO_RELATIVE,
                           question_date="2026-09-10", anchor=anchor) == []
    # ...and without an anchor the shape renders nothing either
    slices = collect_window_slices(_DictWindowPort(_win_rows()), anchor)
    assert synthesize_hits(slices, shape=AssemblyShape.AGO_RELATIVE,
                           question_date="2026-09-10", anchor=None) == []


def test_ago_render_week_and_recency_wording():
    wk = resolve_relative_anchor("what did I do 2 weeks ago?", "2026-09-10")
    assert wk.window_lo.isoformat() == "2026-08-24"
    assert wk.window_hi.isoformat() == "2026-08-30"
    rec = resolve_relative_anchor("Which streaming service did I start using most recently?",
               "2026-09-10")
    slices = collect_window_slices(_DictWindowPort(_win_rows()), rec)
    hits = synthesize_hits(slices, shape=AssemblyShape.AGO_RELATIVE,
                           question_date="2026-09-10", anchor=rec)
    assert hits[0]["content"] == (
        "WINDOW (2025-08-06 -> 2026-09-10): most recent activity before "
        "2026-09-10")


def _ago_window_fixture(sdk, monkeypatch):
    """#2652 live-lane window substrate — deliberately MINIMAL (three dated
    points, no Objects/events/distractors): the assertion under test is DATE
    selection, and a heavy fixture only adds unrelated failure modes
    (embedder encodes, index builds) to it.

    The embedder is PINNED OFF (the #2165 sdk module's autouse fixture does
    the same): without it every ``create_point`` runs a real
    sentence-transformers BERT encode — minutes-per-call on a loaded host and
    irrelevant to a DATE-window assertion.

    Geometry with question_date 2026-09-10: pw-a (2026-08-10) is EXACTLY 'a
    month ago', pw-b (2026-09-01) is '9 days ago', pw-c (2026-07-01) is the
    empty-window (2026-08-24..2026-08-30) substrate.
    """
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))
    for pid, sess, when in (("pw-a", "sess-w-a", "2026-08-10"),
                            ("pw-b", "sess-w-b", "2026-09-01"),
                            ("pw-c", "sess-w-c", "2026-07-01")):
        sdk.create_point("statement", f"windowed marker {pid}", id=pid,
                         session_id=sess, is_episodic=True, status="draft",
                         when=when, createdAt=when)


@_docker_only
def test_ago_relative_window_walk_live_month(_docker_sdk, monkeypatch):
    """#2652 live lane: question_date MINUS a calendar month → the as-of
    window over stored dates. The window is a HARD selector at BOTH ends —
    the too-late (2026-09-01) and too-early (2026-07-01) rows are absent —
    and no Object subject is involved (the selector is the TIME)."""
    from tortoise.assembly import _assemble_connected
    _ago_window_fixture(_docker_sdk, monkeypatch)
    block = _assemble_connected(_docker_sdk, "what did I do a month ago?",
                                question_date="2026-09-10")
    assert block.fired is True and block.shape == "ago-relative"
    joined = "\n".join(str(h.get("content")) for h in block.post_cap_lines)
    assert "WINDOW (2026-07-26 -> 2026-08-25): 1 month before 2026-09-10" \
        in joined
    assert "windowed marker pw-a" in joined
    assert "windowed marker pw-b" not in joined
    assert "windowed marker pw-c" not in joined
    assert block.subjects == ()
    assert block.admission["rows_admitted"] == 1


@_docker_only
def test_ago_relative_window_walk_live_day(_docker_sdk, monkeypatch):
    """#2652 live lane: a ONE-DAY window ('9 days ago') selects exactly the
    row dated 2026-09-01 — no neighbours, no fabrication."""
    from tortoise.assembly import _assemble_connected
    _ago_window_fixture(_docker_sdk, monkeypatch)
    day = _assemble_connected(_docker_sdk, "what did I do 9 days ago?",
                              question_date="2026-09-10")
    assert day.fired is True
    day_joined = "\n".join(str(h.get("content")) for h in day.post_cap_lines)
    assert "WINDOW (2026-09-01 -> 2026-09-01): 9 days before 2026-09-10" \
        in day_joined
    assert "windowed marker pw-b" in day_joined
    assert "windowed marker pw-a" not in day_joined
    assert "windowed marker pw-c" not in day_joined


@_docker_only
def test_ago_relative_window_walk_live_empty_and_unresolvable(
        _docker_sdk, monkeypatch):
    """#2652 live lane: a window with NO dated row falls back to legacy
    (fired=False + empty block — never an empty pool replacement, #2165
    P1-1); an absent/unparseable question_date never fires and never raises
    (the caller falls through to the legacy lane)."""
    from tortoise.assembly import _assemble_connected
    _ago_window_fixture(_docker_sdk, monkeypatch)
    empty = _assemble_connected(_docker_sdk, "what did I do 2 weeks ago?",
                               question_date="2026-09-10")
    assert empty.fired is False and empty.post_cap_lines == []
    assert _assemble_connected(
        _docker_sdk, "what did I do a month ago?",
        question_date="not-a-date").fired is False
    assert _assemble_connected(
        _docker_sdk, "what did I do a month ago?",
        question_date=None).fired is False


def test_ordering_list_regex_is_linear_and_still_reads_the_colon_form():
    """#2653's colon-list guard is anchored on "…: A, B, and C".

    It must stay that way in BOTH directions, because the two failure modes
    pull against each other and the regex originally failed the first one.

    LINEARITY. The tail was ``:\\s*[^?]+?\\??\\s*$``, where ``\\s*`` and
    ``[^?]+?`` both match whitespace: every offset inside a whitespace run is a
    distinct split the engine re-tries before the ``$`` fails. Measured on the
    PR head, ``_RE_ORDERING_LIST.match("which order: " + " "*1985 + "?x")`` took
    25.3 s and ``classify_question`` on the same string took 21.5 s — and
    ``tortoise.ask_lane._ask_validate`` ACCEPTS that string (2000 chars
    = ``MAX_ASK_QUESTION_CHARS``), so a crafted question parked the ask lane for
    twenty seconds with no timeout to catch it. `run_ask_lane` has no timeout:
    a hang is not an exception its envelope can see.

    The tail is now ``:\\s*[^?\\s][^?]*\\??\\s*$`` — ``\\s*`` and the option span
    start on DISJOINT classes, so there is nothing to re-split: measured
    0.0000 s at len 2000. The bound below is deliberately loose (4 orders of
    magnitude above the fixed time, ~7x below the broken one) so it cannot be
    a verdict about machine load — see #5049 for why that distinction matters.

    FALSE NEGATIVES. The strictness is not free: the old form also matched a
    colon followed by whitespace ALONE (``"which order: "`` -- the space was
    consumed as the "option list"). Rejecting that is the intent, and the
    control below pins it. Over all 133 questions of ``_assembly_census.json``
    old and new agree on every row (0 mismatches), so the tightening is
    invisible to real input.
    """
    import time

    from tortoise.assembly import _RE_ORDERING_LIST

    # Positive control: the measured corpus form still fires.
    assert _RE_ORDERING_LIST.match(
        "Which three events happened in the order from first to last: A, B, and C")
    assert _RE_ORDERING_LIST.match(
        "What is the order of the three events: 'A', 'B', and 'C'")
    assert _RE_ORDERING_LIST.match("which order did I place: the bike, the car, and the train?")

    # Negative control: no option list after the colon is not a list question.
    assert not _RE_ORDERING_LIST.match("which order:")
    assert not _RE_ORDERING_LIST.match("which order: ")
    assert not _RE_ORDERING_LIST.match("which order: ?")

    # Linearity: the adversarial shape must not be quadratic.
    worst = "which order: " + " " * 1985 + "?x"
    assert len(worst) <= 2000, "the probe must stay inside MAX_ASK_QUESTION_CHARS"
    started = time.monotonic()
    _RE_ORDERING_LIST.match(worst)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, (
        "the colon-list guard backtracked on whitespace again: "
        f"{elapsed:.2f}s on a {len(worst)}-char question that _ask_validate "
        "accepts. Keep the option span's first class disjoint from \\s*.")
