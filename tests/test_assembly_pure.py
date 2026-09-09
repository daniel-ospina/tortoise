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


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 4 — typed walker + slice builder (R2/R3-8/R12, R17 P3-1/P3-3/
# P3-6). RED→GREEN: collect_slices / AssemblySlices / WalkerPort /
# docker_walker_port are new symbols in tortoise/assembly.py.
# ══════════════════════════════════════════════════════════════════════════

from tortoise.assembly import (  # noqa: E402
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
