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
* Measured-negative rows that fire: 2/78 = precision 0.9744 (floor ≥ 0.95,
  R16). Both false fires are CENSUS-LABEL NOISE with an in-shape twin filed
  under a positive class (pin adjudicated below): (a) 370a8ff4
  "How many weeks had passed since I recovered from the flu when I went on
  my 10th jog" — two-anchor interval-since morphology, twin interval rows
  fire; (b) gpt4_70e84552_abs "Which task did I complete first, fixing the
  fence or purchasing three cows from Peter?" — the _abs twin of a row
  filed ordering/compare whose identical morphology fires.

Both adjudications keep the shape router (NOT the census labels) as the
arbiter — mirrored in the module docstring.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.assembly import (
    AssemblyShape,
    ResolveResult,
    classify_question,
    extract_subject_terms,
    resolve_subjects,
)

_CENSUS = json.loads(
    (Path(__file__).resolve().parent / "_assembly_census.json").read_text())

# the measured-negative classes (the census's non-fireable sub-classes)
_NEG_CLASSES = {
    "ago-relative", "frequency/count", "duration-state",
    "relative-date-lookup", "nary-ordering", "other",
    "pattern/recurring", "offset-comparison", "recency",
    "recency/current-state",
}
# the two adjudicated label-noise rows that MAY fire (pinned — do not grow
# this list without a documented census divergence)
_ADJUDICATED_FIRE = {
    "370a8ff4",               # interval-since twin (filed frequency/count)
    "gpt4_70e84552_abs",      # ordering twin (filed nary-ordering)
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


# ── measured precision floor on the census negative set (R15/R16) ─────────

def test_census_negative_set_precision_floor():
    """Precision on the measured negative set ≥ 0.95: at most 2 of the 78
    non-fireable rows may fire, and those 2 are pinned to the adjudicated
    label-noise rows (a NEW false fire fails — the floor is monotonic)."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] in _NEG_CLASSES]
    assert len(rows) == 78
    false_fires = [
        r for r in rows
        if classify_question(r["question"]) is not None]
    assert {r["qid"] for r in false_fires} <= _ADJUDICATED_FIRE, \
        f"unexpected false fires: {[(r['qid'], r['question'][:70]) for r in false_fires]}"
    assert len(false_fires) <= 2, \
        f"precision floor breached: {len(false_fires)}/78 fired"
    # the two adjudicated rows fire as their shapes (they are in-shape)
    for r in false_fires:
        assert classify_question(r["question"]) is not None


def test_census_in_scope_recall_and_shape_rejections():
    """In-scope census rows: the 50 shape-typical rows fire; the 5 pinned
    duration/count mis-files do NOT (shape-based rejection is the point)."""
    rows = [r for r in _CENSUS["rows"] if r["cls"] not in _NEG_CLASSES]
    assert len(rows) == 55
    fired = [r for r in rows if classify_question(r["question"]) is not None]
    no_fire = [r for r in rows if classify_question(r["question"]) is None]
    assert len(fired) == 50, \
        f"in-scope fires drifted: {len(fired)} (expected 50)"
    assert len(no_fire) == 5
    by_text = {r["question"]: r for r in no_fire}
    assert set(by_text) == _IN_SCOPE_NO_FIRE, \
        f"in-scope rejection set drifted: {sorted(set(by_text) ^ _IN_SCOPE_NO_FIRE)}"


def test_ago_relative_never_fires_even_with_state_morphology():
    """R13 reject-on-relative-offset: 'ago'/'last <time>' kills the shape
    even when the phrasing carries state morphology."""
    for q in [
        "what is the current status of the couch two weeks ago?",
        "What was the status of the couch two weeks ago?",
        "How many days had passed since I bought the couch two weeks ago "
        "when I sold the dog bed?",
        "How many weeks ago did I meet up with my aunt and receive the "
        "crystal chandelier?",
        "How many months ago did I start my current job at Google?",
        "Which came first, buying the couch two months ago or selling the "
        "dog bed last Saturday?",
        "I received a piece of jewelry last Saturday from whom?",
        "What was the airline that I flied with on Valentine's day?",
    ]:
        assert classify_question(q) is None, q
        assert extract_subject_terms(q) == [], q


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


def test_duration_frequency_never_fire():
    """Duration-state / frequency-count / offset-count morphology never
    fires — 'how long', 'how many X did I spend/take', single-anchor 'since',
    'how many X before Y'."""
    for q in [
        "How long have I been working before I started my current job at "
        "NovaTech?",
        "How long had I been a member of 'Book Lovers Unite' when I "
        "attended the meetup?",
        "How long did I use my new binoculars before I saw the American "
        "goldfinches returning to the area?",
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


def test_nary_and_recency_never_fire():
    """N-ary lists (3+ options), recency ('most recently'), and the
    'how many days passed between the two Xs' compound single-subject form
    never fire."""
    for q in [
        "Which three events happened in order: the day I helped my friend, "
        "the trip, and the party?",
        "What is the order of the six museums I visited from earliest to "
        "latest?",
        "Which mode of transport did I use most recently, a bus or a "
        "train?",
        "Which streaming service did I start using most recently?",
        "How many days passed between the two deep-subject milestones?",
        "When did I buy the couch?",  # date-lookup w/o shape support (v1)
        # 3+ option lists (review r2 P1 — every delimiter family)
        "Which did I get first, the couch or the chair or the table?",
        "Which did I get first, the couch, the dog bed or the sofa?",
        "Which came first: my graduation, my wedding or my first child?",
        "Which came first; my graduation; my wedding or my first child?",
    ]:
        assert classify_question(q) is None, q


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
def test_resolver_docker_fts_paraphrase(_docker_sdk):
    """R7 docker-FTS paraphrase (RESOLUTION-ONLY — the fired-path assembly
    half is Task 6's): a subject term with NO exact-name head but a stored
    Object whose NAME token-matches via FTS ('the grey comfy couch from
    the store' → couch) resolves source='fts' confidence='med'."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the grey comfy couch from the store"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert not res.unresolved
    assert len(res.candidates) == 1
    c = res.candidates[0]
    assert c.name == "couch"
    assert c.source == "fts"
    assert c.confidence == "med"


@_docker_only
def test_resolver_docker_unresolved_keeps_legacy(_docker_sdk):
    """A term matching NO Object stays unresolved on the live lane — the R1
    fired=False signal (legacy fallback), never an empty/errored fire."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port,
                           ["the couch", "the teleporting exercise bike"],
                           shape=AssemblyShape.ORDERING)
    assert res.both_halves_ok(AssemblyShape.ORDERING) is False
    assert len(res.unresolved) == 1


@_docker_only
def test_resolver_docker_alias_leg(_docker_sdk):
    """R7 leg 3 live: the Object NAME does not token-match the term but an
    anchored Point search_keys does ('the ikea purchase' → couch — couch's
    bought-point search_keys 'couch ikea 800 dollars'). Exact + FTS both
    miss (Object names couch/dog bed/sofa share no ikea token)."""
    _ag.build_base_graph(_docker_sdk)
    from tortoise.assembly import docker_resolver_port
    port = docker_resolver_port(_docker_sdk)
    res = resolve_subjects(port, ["the ikea purchase"],
                           shape=AssemblyShape.CURRENT_STATE)
    assert not res.unresolved
    assert len(res.candidates) == 1
    c = res.candidates[0]
    assert c.name == "couch"
    assert c.source == "alias"
    assert c.confidence == "low"
