"""#2567 (C3-1 #2519) — the evidence-completeness retrieval loop (docker lane).

The multi-session partial-evidence lever (docs/scoping/2026-09-08-2519-
coverage-loop.md §3 mechanism (a)): a one-shot top-k cannot enumerate ALL
sessions that carry an aggregation question's subject — k<N stays 0 on the
official all-or-nothing ``recall_all@5`` binary. The loop (product rules in
tortoise/coverage_loop.py, composed by tools/longmem_eval/retrieve.py::
retrieve_for_question) runs:

  CENSUS → CHECK → EXPAND → MERGE

  * census: the rule-based facet census resolves the query's own entity
    anchors through the Object-name spine (#2518); each anchor's span = the
    distinct sessions carrying its ``aboutObject``-linked points,
  * check: the deduped pool is facet-incomplete when a census facet is
    seeded in the guard window yet its span is not fully covered (partial
    evidence — open-ended questions never fire),
  * expand: ONE targeted second sparse pass for the missing facet (hard
    ≤1-extra-pass bound; the A4/C2 reserved-slot OR contract),
  * merge: additive union in second-pass relevance order + the session-
    diverse window discipline (a same-session flood never crowds the
    guard window).

Hermetic contract proven here (default OFF; no LLM, no network):

  * (a) end-to-end: plain top-k misses a same-subject point in another
        session; the loop's targeted second pass surfaces it inside the
        top-k (session_recall@5 0.5 → 1.0),
  * (b) OFF == byte-identical (default no-kwarg == explicit False; ranked
        ids and pool identical; loop markers all-zero),
  * (c) the eval env gate is fail-safe OFF (unset/garbage → OFF; only
        1/true/yes/on arms; explicit flags beat the env),
  * (d) the hard ≤1-extra-pass bound (two missing facets → exactly ONE
        expansion pass),
  * (e) the never-fire rules: open-ended questions (no countable facet)
        and complete single-session spans never fire; an out-of-window
        date-qualified span never fires,
  * (f) fail-open: any loop failure keeps the ORIGINAL pool byte-identical.

Runs against a live FalkorDB (docker lane) on a DEDICATED per-test graph
(fresh indexes, zero cross-test contamination). Requires the docker lane's
FTS backend — skips when unavailable (embedded has no fulltext index).
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

# import the eval ingest first so its register_kind("event") / register_kind(
# "session-transcript") apply (turn points in the eval's graphs are Point
# nodes with pointKind "event" — the fixture mirrors that shape).
import tools.longmem_eval.ingest  # noqa: F401
from tortoise.sdk import TortoiseSDK

# ── Live-FalkorDB availability (the FTS backend the loop needs) ────────────
# NB: tier-2 CI sets TORTOISE_DB_URI="" (present-but-empty) — the plain
# .get(var, default) probe would return "" and die on the "_probe" suffix.
# The `or` fallback treats "" as unset so the probe reaches the provisioned
# falkordb service on BOTH lanes (matches test_search_engine_gaps/indexes).
_URI = (os.environ.get("TORTOISE_DB_URI")
        or "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = f"{_URI}_probe"
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")

#: The question tokens (after the shared stopword drop): the sparse OR leg
#: matches these against content ∪ search_keys.
QUESTION = "how much did the road bike repairs cost me in total"
ENTITY_ANCHOR = "road bike repairs"
SEED_ID = "p2567sessA0"     # session A turn point — matches the question
JOIN_ID = "p2567sessB1"     # session B turn point — search_keys-only join
SEED_SESSION = "sessA2567"
JOIN_SESSION = "sessB2567"
#: The cross-session point's E3 keys: zero overlap with the question's
#: tokens (plain FTS cannot match it) — its OWN alias vocabulary joins the
#: second pass via the shared aboutObject anchor (the C2 miss class).
JOIN_KEYS = "extra charge for wheels saturday service fee"
DISTRACTOR_TOPIC = "cooking pasta recipes with tomato basil sauce"


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server — fresh indexes and
    an empty graph make the differential hermetic (no leftovers from other
    tests in the shared matrix can seed anchors or pollute the pool)."""
    return f"{_URI}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): create_point stops embedding
    (``compute_embedding`` → None) and the query path sees no embedder
    (``EmbeddingModel.get`` → None), so the vector strategy never runs and
    the differential isolates the sparse loop mechanics. No model load, no
    network — deterministic on any machine with the docker lane."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


def _question(question: str = QUESTION, *,
              answer_sessions: list[str] | None = None,
              haystack_dates: list[str] | None = None) -> dict:
    """The retrieve_for_question question shape (the fixture's session ids
    are plain strings — retrieve matches the stored ``session_id`` props).
    ``haystack_session_ids`` + ``haystack_dates`` are aligned so the loop's
    date-range facet qualification has a session→date map."""
    return {
        "question_id": "q2567test",
        "question": question,
        "question_type": "multi-session-reasoning",
        "answer_session_ids": (answer_sessions
                               if answer_sessions is not None
                               else [SEED_SESSION, JOIN_SESSION]),
        "haystack_session_ids": [SEED_SESSION, JOIN_SESSION],
        "haystack_dates": (haystack_dates
                           if haystack_dates is not None
                           else ["2026-09-01", "2026-09-05"]),
        "haystack_sessions": [],
        "answer": "",
    }


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph with the two-session fixture:

    * Object anchor ``road bike repairs`` (the entity spine node),
    * session A turn point (kind "event" — the eval's turn shape) matching
      the question's own tokens,
    * session B turn point about the SAME anchor whose content shares NO
      query token and whose E3 ``search_keys`` are its only join surface —
      absent from the one-shot pool (turn points enter the eval pool only
      via fts/vector; only kind-"statement" extraction points are
      structural-scanned),
    * 12 statement distractors with zero token overlap with the question or
      the keys (they rank only via the structural kind scan).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point(
            "event",
            "the road bike repairs cost 120 dollars at the shop",
            id=SEED_ID, session_id=SEED_SESSION,
            search_keys="road bike repairs bill paid 120 dollars",
            status="draft")
        sdk.create_point(
            "event",
            "on saturday morning the weather was sunny so i went for a "
            "long walk instead",
            id=JOIN_ID, session_id=JOIN_SESSION,
            search_keys=JOIN_KEYS, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement",
                f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2567dist{i}", session_id=f"dist2567{i}",
                status="draft")
        proj.g.query(
            "MATCH (p:Point), (o:Object {name:$name}) "
            "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
            params={"name": ENTITY_ANCHOR,
                    "ids": [SEED_ID, JOIN_ID]})
        yield sdk
    finally:
        sdk.close()


# ── (a) the end-to-end contract ───────────────────────────────────────────

def test_loop_surfaces_missing_session_evidence_inside_topk(seeded_sdk):
    """Plain top-k misses the same-subject point from session B (its
    content shares no query token and it is not structural-scanned); the
    coverage loop's targeted second pass surfaces it INSIDE the top-k, and
    the official session_recall@5 moves 0.5 → 1.0."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    off_ids = [h["id"] for h in off["hits"]]
    assert SEED_ID in off_ids[:5]
    assert JOIN_ID not in off_ids[:5], (
        "plain top-k must MISS the session B point (the partial-evidence "
        "miss class #2513 targets)")
    assert JOIN_ID not in off_ids, (
        "session B's point must be absent from the ONE-SHOT pool entirely")
    assert off["session_recall@k"]["5"] == 0.5
    # off-arm markers are zeros (reconstructable 2×2 with #2518)
    assert off["coverage_loop"] is False
    assert off["coverage_loop_stats"]["loop_iterations"] == 0
    assert off["coverage_loop_stats"]["loop_fired_facet"] is None
    assert off["coverage_loop_stats"]["loop_merged_added"] == 0

    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    on_ids = [h["id"] for h in on["hits"]]
    assert JOIN_ID in on_ids, (
        "the loop's targeted second pass must surface the session B point")
    assert JOIN_ID in on_ids[:5], (
        "the surfaced point must land INSIDE the top-k reader window")
    assert SEED_ID in on_ids[:5], "the pass-1 seed is never displaced"
    assert on["session_recall@k"]["5"] == 1.0
    stats = on["coverage_loop_stats"]
    assert on["coverage_loop"] is True
    assert stats["loop_iterations"] == 1
    assert stats["loop_fired_facet"] == f"entity:{ENTITY_ANCHOR}"
    assert stats["loop_merged_added"] >= 1


# ── (b) default OFF: byte-identical no-op ─────────────────────────────────

def test_loop_disabled_is_default_byte_identical(seeded_sdk):
    """Default (no kwarg) == explicit ``coverage_loop=False`` == no flag:
    identical ranked ids in the same order — the arm changes NOTHING
    without opt-in (the #1745 fail-safe default decision), and the loop
    stats stay all-zero."""
    import inspect

    from tools.longmem_eval.retrieve import retrieve_for_question
    sig = inspect.signature(retrieve_for_question)
    assert sig.parameters["coverage_loop"].default is None
    q = _question()
    default = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                    pool_size=60)
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60, coverage_loop=False)
    assert [h["id"] for h in default["hits"]] == [h["id"] for h in off["hits"]]
    assert default["coverage_loop"] is False
    assert default["coverage_loop_stats"] == off["coverage_loop_stats"]
    assert default["coverage_loop_stats"]["loop_iterations"] == 0
    assert JOIN_ID not in [h["id"] for h in default["hits"]], (
        "default behavior must not run the loop")


def test_eval_env_gate_failsafe_off(seeded_sdk, monkeypatch):
    """The eval A/B gate (``retrieve_for_question`` / env
    ``TORTOISE_LME_COVERAGE_LOOP``) is fail-safe OFF: unset and garbage
    resolve False, only explicit truthy (1/true/yes/on) arms the loop — a
    typo never flips the knob. The outcome records the resolved arm so the
    2×2 A/B with #2518 is reconstructable."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["coverage_loop"] is False
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "garbage")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10,
        pool_size=60)["coverage_loop"] is False
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "1")
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60)
    assert on["coverage_loop"] is True
    assert JOIN_ID in [h["id"] for h in on["hits"][:5]]
    # explicit flag beats the env in both directions
    monkeypatch.setenv("TORTOISE_LME_COVERAGE_LOOP", "1")
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        coverage_loop=False)["coverage_loop"] is False
    monkeypatch.delenv("TORTOISE_LME_COVERAGE_LOOP", raising=False)
    assert retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        coverage_loop=True)["coverage_loop"] is True


# ── (c) the hard iteration bound ──────────────────────────────────────────

def test_loop_hard_one_extra_pass_bound(seeded_sdk, monkeypatch):
    """The ≤1-extra-pass hard bound (§7 iteration-cost creep guard): even
    with TWO census facets left incomplete by the one-shot pool, the loop
    runs exactly ONE targeted second pass (the bound is structural, and the
    per-outcome iteration census records it)."""
    import tortoise.coverage_loop as _cl
    from tools.longmem_eval.retrieve import retrieve_for_question

    calls: list[list] = []
    real_pass = _cl.loop_expansion_pass

    def _counting_pass(proj, query, facets, **kw):
        calls.append([f.key for f in facets])
        return real_pass(proj, query, facets, **kw)

    monkeypatch.setattr(_cl, "loop_expansion_pass", _counting_pass)
    # a second anchor with its own two-session span (seed in session C via
    # a content-matching point, a keys-only join in session D) so the census
    # resolves TWO seeded-but-incomplete facets.
    proj = seeded_sdk._get_proj()
    seeded_sdk.create_entity("object", "kitchen renovation",
                             objectKind="core:other", is_episodic=True)
    seeded_sdk.create_point(
        "event",
        "the kitchen renovation cost 2500 dollars at the end",
        id="p2567sessC0", session_id="sessC2567",
        search_keys="kitchen renovation total paid 2500 dollars",
        status="draft")
    seeded_sdk.create_point(
        "event",
        "the oven light stays on after closing the door",
        id="p2567sessD1", session_id="sessD2567",
        search_keys="tile backsplash labour charge friday invoice",
        status="draft")
    proj.g.query(
        "MATCH (p:Point), (o:Object {name:$name}) "
        "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
        params={"name": "kitchen renovation",
                "ids": ["p2567sessC0", "p2567sessD1"]})
    q2 = _question(
        "how much did the road bike repairs and kitchen renovation cost "
        "in total",
        answer_sessions=[SEED_SESSION, JOIN_SESSION, "sessC2567",
                         "sessD2567"])
    on = retrieve_for_question(seeded_sdk, q2, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert calls, "the loop must fire (two seeded-but-incomplete facets)"
    assert len(calls) == 1, (
        "two missing facets must still run exactly ONE expansion pass "
        "(the hard ≤1-extra-pass bound)")
    assert on["coverage_loop_stats"]["loop_iterations"] == 1
    # the single pass carried BOTH facets' vocabulary (union, not a per-
    # facet iteration) and the missing sessions surfaced inside the top-k.
    assert "p2567sessD1" in [h["id"] for h in on["hits"][:5]]
    assert on["session_recall@k"]["5"] == 1.0


# ── (d) the never-fire rules ──────────────────────────────────────────────

def test_open_ended_question_never_fires(seeded_sdk):
    """Open-ended questions (no countable entity facet — the query's tokens
    resolve no Object-spine anchor) NEVER fire: the census is empty, so ON
    == OFF byte-identical and the markers stay zero."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question("summarize what happened in the whole conversation",
                  answer_sessions=[SEED_SESSION])
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in off["hits"]] == [h["id"] for h in on["hits"]]
    stats = on["coverage_loop_stats"]
    assert on["coverage_loop"] is True
    assert stats["loop_iterations"] == 0
    assert stats["loop_fired_facet"] is None
    assert stats["loop_merged_added"] == 0


def test_complete_single_session_span_never_fires(seeded_sdk):
    """A facet whose census span is FULLY covered by the pool's top window
    never fires — the single-session-user regression shape (the loop must
    not touch questions whose evidence is already complete)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    # query that resolves the SAME anchor but whose only matching evidence
    # (session A's seed) fully covers the census span restricted to the
    # sessions the fixture's date map knows… here the span has TWO sessions
    # but session B's point cannot match ANY plain query — instead fire the
    # rule with the SPAN single-session shape: an anchor linked to ONE
    # session only.
    proj = seeded_sdk._get_proj()
    seeded_sdk.create_entity("object", "garden herb patch",
                             objectKind="core:other", is_episodic=True)
    seeded_sdk.create_point(
        "event",
        "we grew basil and rosemary in the garden herb patch",
        id="p2567solo0", session_id="soloS2567",
        search_keys="garden herb patch basil rosemary", status="draft")
    proj.g.query(
        "MATCH (p:Point), (o:Object {name:$name}) "
        "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
        params={"name": "garden herb patch", "ids": ["p2567solo0"]})
    q = _question("what did we grow in the garden herb patch",
                  answer_sessions=["soloS2567"])
    off = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                pool_size=60)
    assert "p2567solo0" in [h["id"] for h in off["hits"][:5]]
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in off["hits"]] == [h["id"] for h in on["hits"]], (
        "a fully-covered single-session span must keep the loop a no-op")
    stats = on["coverage_loop_stats"]
    assert stats["loop_iterations"] == 0
    assert stats["loop_fired_facet"] is None


def test_date_qualified_out_of_window_span_never_fires(seeded_sdk):
    """The date-range facet rule: when the query bounds the window and the
    census date map places a span session OUTSIDE it, that session is not
    part of the countable span — a span fully inside the window never
    fires."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    # session A is dated 2026-09-01 (in the [09-01, 09-03] interval); the
    # aboutObject-linked session B is dated 2026-09-05 (OUT of the window).
    q = _question(
        "between 2026-09-01 and 2026-09-03 how much did the road bike "
        "repairs cost me",
        answer_sessions=[SEED_SESSION])
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True,
                               )
    assert on["coverage_loop"] is True
    assert on["coverage_loop_stats"]["loop_iterations"] == 0, (
        "an out-of-window date-qualified span must not fire the loop")
    assert on["coverage_loop_stats"]["loop_fired_facet"] is None


# ── (e) fail-open: any failure keeps the ORIGINAL pool ────────────────────

def test_loop_fail_open_keeps_original_result(seeded_sdk, monkeypatch):
    """Any loop failure (census graph fault, expansion fault) keeps the
    ORIGINAL result byte-identical — the loop can never turn a working lane
    into a broken one (the A4/C2 fail-open posture)."""
    import tortoise.coverage_loop as _cl
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    baseline = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                                     pool_size=60, coverage_loop=False)

    def _boom(*a, **k):
        raise RuntimeError("injected census failure")

    monkeypatch.setattr(_cl, "facet_census", _boom)
    on = retrieve_for_question(seeded_sdk, q, ks=(5,), top_k=10,
                               pool_size=60, coverage_loop=True)
    assert [h["id"] for h in baseline["hits"]] == [h["id"] for h in on["hits"]]
    assert [h["id"] for h in baseline["hits"][:5]] == \
        [h["id"] for h in on["hits"][:5]]
    assert on["coverage_loop"] is True
    assert on["coverage_loop_stats"]["loop_iterations"] == 0
    assert on["coverage_loop_stats"]["loop_fired_facet"] is None
    assert on["coverage_loop_stats"]["loop_merged_added"] == 0
