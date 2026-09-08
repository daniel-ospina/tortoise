"""#2521 (C5 #2513) — aggregative-intent detection + facet coverage (graph).

The graph-backed half of the #2521 tests: the coverage check resolves the
query's entity anchors through the Object-name spine (the SAME #2518
surface) and enumerates the anchor's linked Points' facets from a PLANTED
multi-facet fixture (tests (b) complete/partial/none + the never-flag-
open-ended rule, (c) OFF-by-default byte-identical, and (d) the eval arm
marker). The pure math half lives in tests/test_aggregative_intent.py —
this file proves the identical logic against a live graph.

Fixture story (the #2513 measured miss class): the question "how much did
the road bike repairs cost me in total" spans THREE sessions (a new repair
cost in each) all linked to the SAME Object anchor. One-shot top-k starves
session C (its content shares no query token — only the aboutObject join
knows it is a same-subject facet). The coverage verdict must report
k-of-N = 2-of-3, missing session C — the exact signal the C3-3
coverage-signal routing (#2519) will consume.

Hermetic: no model, no network — the dense leg is pinned out (the
differential is exactly the sparse retrieval + the anchor census). Runs
against a live FalkorDB (docker lane) on a DEDICATED per-test graph.
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.sdk import TortoiseSDK

# ── Live-FalkorDB availability (the FTS backend the anchor spine needs) ───
_URI = os.environ.get(
    "TORTOISE_DB_URI",
    "docker://:falkordb@localhost:6379/tortoise_test_matrix").rstrip("/")
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

#: The #2518-style aggregation question — spans three sessions (A, B, C)
#: about the SAME Object anchor. A and B's content shares the question's
#: tokens (they rank in plain top-k); C's content shares NONE (only the
#: aboutObject join makes it a known same-subject facet — the starved
#: session the one-shot top-k leaves out).
QUESTION = "how much did the road bike repairs cost me in total"
ENTITY_ANCHOR = "road bike repairs"
#: session A — content matches the question's own tokens
A_ID, A_SESSION = "p2521sessA", "agg2521A"
#: session B — content matches the question's own tokens
B_ID, B_SESSION = "p2521sessB", "agg2521B"
#: session C — the SAME-subject facet plain retrieval starves
C_ID, C_SESSION = "p2521sessC", "agg2521C"
DISTRACTOR_TOPIC = "cooking pasta recipes with tomato basil sauce"


def _fresh_uri() -> str:
    """A dedicated per-test graph on the docker server — fresh indexes and
    an empty graph make the differential hermetic (no leftovers from other
    tests in the shared matrix can seed anchors or pollute the pool)."""
    return f"{_URI}_{uuid.uuid4().hex[:10]}"


@pytest.fixture(autouse=True)
def _no_embedder(monkeypatch):
    """Pin the dense leg OUT (hermetic): create_point stops embedding and
    the query path sees no embedder, so the vector strategy never runs and
    the differential isolates the sparse leg + the anchor census."""
    import tortoise.embeddings as _emb
    monkeypatch.setattr(_emb, "compute_embedding",
                        staticmethod(lambda content: None))
    monkeypatch.setattr(_emb.EmbeddingModel, "get",
                        staticmethod(lambda: None))


@pytest.fixture
def seeded_sdk(monkeypatch):
    """A fresh dedicated graph with the three-session fixture:

    * Object anchor ``road bike repairs`` (the entity spine node),
    * one linked Point per session (A: content matches the query; B:
      content matches; C: content shares NO query token — known to the
      anchor census only, the #2513 starved-facet shape),
    * 12 distractors with zero token overlap (they cannot rank).
    """
    monkeypatch.setenv("TORTOISE_DB_URI", _fresh_uri())
    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        sdk.create_entity("object", ENTITY_ANCHOR, objectKind="core:other",
                          is_episodic=True)
        sdk.create_point(
            "statement",
            "the road bike repairs cost 120 dollars at the bike shop "
            "last month and included new brake pads",
            id=A_ID, session_id=A_SESSION, status="draft")
        sdk.create_point(
            "statement",
            "the road bike repairs cost another 80 dollars for new "
            "wheels and a chain",
            id=B_ID, session_id=B_SESSION, status="draft")
        sdk.create_point(
            "statement",
            "on saturday morning the weather was sunny so i went for a "
            "long walk along the river instead",
            id=C_ID, session_id=C_SESSION, status="draft")
        for i in range(12):
            sdk.create_point(
                "statement",
                f"{DISTRACTOR_TOPIC} number {i}",
                id=f"p2521dist{i}", session_id=f"dist2521{i}",
                status="draft")
        proj.g.query(
            "MATCH (p:Point), (o:Object {name:$name}) "
            "WHERE p.id IN $ids MERGE (p)-[:aboutObject]->(o)",
            params={"name": ENTITY_ANCHOR,
                    "ids": [A_ID, B_ID, C_ID]})
        yield sdk
    finally:
        sdk.close()


def _question() -> dict:
    return {
        "question_id": "q2521facet",
        "question": QUESTION,
        "question_type": "multi-session",
        "answer_session_ids": [A_SESSION, B_SESSION, C_SESSION],
        "haystack_dates": ["2026-09-01", "2026-09-05", "2026-09-09"],
        "haystack_sessions": [],
        "answer": "",
    }


# ── (b) facet coverage on the planted multi-facet fixture ─────────────────

def test_partial_coverage_reports_missing_starved_session(seeded_sdk):
    """The #2513 partial-evidence miss class, measured e2e: the eval's
    ranked top-k window (``pool[:top_k]``) surfaces the two FTS-matching
    sessions (A+B — they are the only docs with query-token overlap, so
    they deterministically hold the top-2 ranks) and starves C (only the
    aboutObject census knows it is a same-subject facet). The coverage
    check reports k-of-N = 2/3, signal partial, missing agg2521C — the
    exact signal the C3-3 routing will expand on."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    ret = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=2, pool_size=60,
        aggregative_flag=True)
    window_sessions = {h["session_id"] for h in ret["hits"][:2]}
    assert window_sessions == {A_SESSION, B_SESSION}, (
        "the top-2 window must hold the two FTS-matching sessions (the "
        "partial-evidence window shape — C is the starved facet)")
    verdict = ret["aggregative_verdict"]
    assert verdict is not None
    intent = verdict["detected_intent"]
    assert intent["is_aggregative"] is True
    assert intent["quantifier"] == "how_much"
    assert intent["scope"] == "entity-scoped"
    assert verdict["signal"] == "partial"
    assert verdict["n_facets"] == 3
    assert verdict["retrieved_facets"] == 2
    assert verdict["facet_coverage"] == pytest.approx(2 / 3)
    assert C_SESSION in verdict["missing_facets"]
    assert A_SESSION not in verdict["missing_facets"]
    assert ENTITY_ANCHOR in verdict["anchors"]


def test_complete_coverage_end_to_end_full_window(seeded_sdk):
    """Complete e2e: with a window that spans the whole pool (all 15
    points <= top_k), every known same-subject facet is retrieved — the
    verdict is complete (3/3, no missing facets)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    ret = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=60, pool_size=60,
        aggregative_flag=True)
    verdict = ret["aggregative_verdict"]
    assert verdict["signal"] == "complete"
    assert verdict["facet_coverage"] == 1.0
    assert verdict["n_facets"] == 3
    assert verdict["retrieved_facets"] == 3
    assert verdict["missing_facets"] == []


def test_complete_coverage_when_all_facets_retrieved(seeded_sdk):
    """Complete: when every known same-subject facet has a representative
    in the retrieval, the verdict is complete (k == N == 3, no missing
    facets). Exercised over the live anchor census with the full pool."""
    from tortoise.aggregate import aggregative_verdict
    verdict = aggregative_verdict(
        query=QUESTION, proj=seeded_sdk._get_proj(),
        retrieved_points=[
            {"id": A_ID, "session_id": A_SESSION},
            {"id": B_ID, "session_id": B_SESSION},
            {"id": C_ID, "session_id": C_SESSION},
        ])
    assert verdict["signal"] == "complete"
    assert verdict["facet_coverage"] == 1.0
    assert verdict["n_facets"] == 3
    assert verdict["retrieved_facets"] == 3
    assert verdict["missing_facets"] == []
    assert ENTITY_ANCHOR in verdict["anchors"]
    assert verdict["capped"] is False


def test_none_coverage_when_nothing_retrieved(seeded_sdk):
    """Zero-of-N is the honest worst case — partial with the full missing
    list (never 'complete', never a no-claim 'none' while an exhaustive N
    exists in the index)."""
    from tortoise.aggregate import aggregative_verdict
    verdict = aggregative_verdict(
        query=QUESTION, proj=seeded_sdk._get_proj(), retrieved_points=[])
    assert verdict["signal"] == "partial"
    assert verdict["facet_coverage"] == 0.0
    assert len(verdict["missing_facets"]) == 3


def test_never_flag_open_ended_even_with_resolvable_anchor(
        seeded_sdk, monkeypatch):
    """The never-flag-open-ended rule (index gate proof): a corpus-self-
    referential aggregation ("how many memories…") must NEVER run the
    anchor census — even though this graph holds a resolvable Object. The
    scope gate short-circuits before any graph IO (a boom census proves
    it)."""
    import tortoise.aggregate as aggmod

    def _boom(*_a, **_k):
        raise AssertionError(
            "open-ended aggregation must never reach the anchor census "
            "(the never-flag rule)")

    monkeypatch.setattr(aggmod, "collect_anchor_census", _boom)
    verdict = aggmod.aggregative_verdict(
        query="how many memories do i have in total",
        proj=seeded_sdk._get_proj(), retrieved_points=[])
    assert verdict["signal"] == "none"
    assert verdict["reason"] == "open_ended"
    assert verdict["detected_intent"]["scope"] == "open-ended"
    assert verdict["facet_coverage"] is None
    assert verdict["missing_facets"] == []


def test_no_anchor_and_no_linked_points_never_flag(seeded_sdk):
    """Un-resolvable subject → no coverage claim (the index holds no
    exhaustive enumeration for it). An anchor with zero linked points is
    equally no-claim (nothing to enumerate)."""
    from tortoise.aggregate import aggregative_verdict
    # a scoped aggregation whose subject exists NOWHERE in the Object index
    verdict = aggregative_verdict(
        query="how much did my sailing lessons in july cost in total",
        proj=seeded_sdk._get_proj(), retrieved_points=[])
    assert verdict["signal"] == "none"
    assert verdict["reason"] == "no_anchor"
    assert verdict["facet_coverage"] is None


# ── (c) OFF-by-default byte-identical + (d) the eval arm marker ────────────

def test_off_by_default_is_byte_identical(seeded_sdk, monkeypatch):
    """Default (no kwarg, no env) == explicit ``aggregative_flag=False`` —
    byte-identical outcomes with NO verdict key: the arm changes nothing
    without opt-in (the #1745 fail-safe default decision)."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    monkeypatch.delenv("TORTOISE_LME_AGGREGATIVE_FLAG", raising=False)
    default = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60)
    assert default["aggregative_flag"] is False
    assert "aggregative_verdict" not in default
    off = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        aggregative_flag=False)
    # byte-identical apart from the wall-clock latency field (measured per
    # call — the only nondeterministic key in the outcome shape)
    assert {k: v for k, v in off.items() if k != "retrieval_latency_ms"} \
        == {k: v for k, v in default.items()
            if k != "retrieval_latency_ms"}, \
        "explicit OFF must be byte-identical to default"


def test_env_gate_failsafe_off_and_tristate(seeded_sdk, monkeypatch):
    """The eval A/B gate is fail-safe OFF: unset and garbage resolve False,
    only explicit truthy (1/true/yes/on) arms the check — a typo never
    flips the knob. Explicit flags beat the env in both directions. Under
    the arm the outcome records the marker + the verdict."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    monkeypatch.delenv("TORTOISE_LME_AGGREGATIVE_FLAG", raising=False)
    assert retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10,
        pool_size=60)["aggregative_flag"] is False
    monkeypatch.setenv("TORTOISE_LME_AGGREGATIVE_FLAG", "garbage")
    assert retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10,
        pool_size=60)["aggregative_flag"] is False
    # env truthy arms the check → the per-outcome verdict rides the outcome
    monkeypatch.setenv("TORTOISE_LME_AGGREGATIVE_FLAG", "1")
    on = retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=2, pool_size=60)
    assert on["aggregative_flag"] is True
    assert on["aggregative_verdict"] is not None
    assert on["aggregative_verdict"]["detected_intent"]["is_aggregative"]
    assert on["aggregative_verdict"]["signal"] == "partial"
    # explicit flags beat the env in both directions
    monkeypatch.setenv("TORTOISE_LME_AGGREGATIVE_FLAG", "1")
    assert retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        aggregative_flag=False)["aggregative_flag"] is False
    monkeypatch.delenv("TORTOISE_LME_AGGREGATIVE_FLAG", raising=False)
    assert retrieve_for_question(
        seeded_sdk, _question(), ks=(5,), top_k=10, pool_size=60,
        aggregative_flag=True)["aggregative_flag"] is True


def test_arm_records_per_outcome_vocabulary(seeded_sdk):
    """The #2521 per-outcome arm contract: under the arm every question's
    outcome records {detected_intent, facet_coverage, missing_facets} (the
    vocabulary the C3-3 coverage-signal routing consumes) — a NON-
    aggregative question records the detected-intent None-shape and no
    coverage claim."""
    from tools.longmem_eval.retrieve import retrieve_for_question
    q = _question()
    q["question"] = "what did i eat for dinner on tuesday"
    ret = retrieve_for_question(
        seeded_sdk, q, ks=(5,), top_k=10, pool_size=60,
        aggregative_flag=True)
    verdict = ret["aggregative_verdict"]
    assert verdict["detected_intent"]["is_aggregative"] is False
    assert verdict["signal"] == "none"
    assert verdict["reason"] == "not_aggregative"
    assert verdict["facet_coverage"] is None
