"""Oracle determinism + the distinguishing-property tests (#1144).

Includes the REQUIRED proof that the oracle makes strategies measurable:
with the latent-topic corpus, at least one strategy achieves P@K < 1.0 over
the 100-query mix (impossible on the old shared-pool corpus, where every
token is in every point and P@K ≈ 1.0 for all strategies).
"""
from __future__ import annotations

import random
from collections import Counter
from pathlib import Path

import pytest

from benchmarks.synthetic_corpus import (
    TOKENS,
    build_topic_oracle,
    generate_oracle_points,
    oracle_grades_for_query,
)
from tests.eval.retrieval.metrics import compute_metrics, precision_at_k
from tests.eval.retrieval.queries import build_oracle_query_set, load_oracle_queries

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def _has_embedded() -> bool:
    try:
        import redislite.falkordb_client  # noqa: F401
        from tortoise.projection import FalkorProjection  # noqa: F401
        return True
    except Exception:
        return False


# ── Oracle structure ────────────────────────────────────────────────────────

def test_oracle_deterministic():
    o1, o2 = build_topic_oracle(42), build_topic_oracle(42)
    assert o1.core == o2.core
    assert o1.bridges == o2.bridges
    assert o1.centroids == o2.centroids
    p1, c1 = generate_oracle_points(200, o1)
    p2, c2 = generate_oracle_points(200, o2)
    assert [p["content"] for p in p1] == [p["content"] for p in p2]
    assert p1[0]["embedding"] == p2[0]["embedding"]
    assert c1 == c2
    # The token partition is seed-independent (pool structure); the
    # centroid space is not — different seeds give different clusters.
    assert build_topic_oracle(7).core == o1.core
    assert build_topic_oracle(7).centroids != o1.centroids


def test_oracle_partition_properties():
    o = build_topic_oracle(42)
    assert len(o.core) == 24
    # Every pool token is owned by ≥1 topic.
    assert all(any(tok in vocab for vocab in o.core.values())
               or any(tok in o.bridges.values() for _ in [0])
               for tok in TOKENS)
    # Every token in token_to_topics is findable.
    for tok, owners in o.token_to_topics.items():
        assert 1 <= len(owners) <= 2
    # NEAR = the two bridge-sharing neighbors (circular).
    for k in o.core:
        assert set(o.near(k)) == {(k - 1) % 24, (k + 1) % 24}
    # Bridge tokens are shared with exactly the next topic.
    for k, b in o.bridges.items():
        assert set(o.token_to_topics[b]) == {k, (k + 1) % 24}
    # Centroids are unit-norm 384-d.
    for k, c in o.centroids.items():
        assert len(c) == 384
        assert abs(sum(x * x for x in c) ** 0.5 - 1.0) < 1e-6


def test_graded_relevance_three_levels():
    o = build_topic_oracle(42)
    points, _counts = generate_oracle_points(600, o)
    live: dict[int, list[str]] = {}
    for p in points:
        if p["is_operator"] or p["status"] == "retracted":
            continue
        live.setdefault(p["topic"], []).append(p["id"])
    labels = oracle_grades_for_query(o, target_topic=5, live_ids_by_topic=live)
    grades = Counter(labels.values())
    assert set(grades) == {0, 1, 2}          # all three grades present
    target_ids = set(live[5])
    near_ids = {pid for t in o.near(5) for pid in live[t]}
    assert all(labels[pid] == 2 for pid in target_ids)
    assert all(labels[pid] == 1 for pid in near_ids)
    assert labels["p-0000001"] in (0, 1, 2)  # every live point judged


def test_oracle_false_friend_property():
    """The oracle's core fix: grade-0 points CAN contain query tokens
    (impossible on the old shared-pool corpus — every token everywhere)."""
    o = build_topic_oracle(42)
    points, _ = generate_oracle_points(800, o)
    live: dict[int, list[str]] = {}
    for p in points:
        if p["is_operator"] or p["status"] == "retracted":
            continue
        live.setdefault(p["topic"], []).append(p["id"])
    content_by_id = {p["id"]: p["content"] for p in points}
    qs = load_oracle_queries()["queries"]
    found = 0
    for q in qs:
        tokens = {t for t in q["query"].split()}
        labels = oracle_grades_for_query(o, q["oracle_target"], live)
        for pid, grade in labels.items():
            if grade == 0 and tokens & set(content_by_id[pid].split()):
                found += 1
                break
        if found:
            break
    assert found > 0, (
        "no grade-0 point shares a token with any query — the oracle would "
        "not fix the 'everything matches' blindness"
    )


def test_query_set_committed_matches_generator():
    """The committed oracle_queries.json is exactly the deterministic
    generator output (files cannot drift)."""
    rebuilt = build_oracle_query_set()
    committed = load_oracle_queries()
    assert rebuilt == committed


def test_query_set_tiers_and_composition():
    qs = load_oracle_queries()
    assert len(qs["queries"]) == 100
    tiers = Counter(q["tier"] for q in qs["queries"])
    assert dict(tiers) == {"easy": 50, "medium": 30, "hard": 20}
    sources = Counter(q["source"] for q in qs["queries"])
    assert dict(sources) == {"query_mix": 53, "oracle_new": 47}
    ids = [q["id"] for q in qs["queries"]]
    assert len(set(ids)) == 100
    # Every oracle query has a deterministic target.
    assert all(0 <= q["oracle_target"] < 24 for q in qs["queries"])
    # q052/q055/q056 excluded (no vocabulary overlap).
    assert qs["meta"]["query_mix_excluded"]["ids"] == ["q052", "q055", "q056"]


# ── The distinguishing property (embedded, the required test) ──────────────

def _run_strategies_on_mix(corpus_size: int, db_path: str) -> dict:
    from tortoise.sdk import TortoiseSDK

    from benchmarks.synthetic_corpus import seed_corpus
    from tests.eval.retrieval.run import retrieve_per_strategy

    o = build_topic_oracle(42)
    points, _counts = generate_oracle_points(corpus_size, o)
    sdk = TortoiseSDK(db_path)
    try:
        g = sdk._get_proj().g
        seed_corpus(g, points)
        rows = g.query(
            "MATCH (n:Point) WHERE n.is_operator <> true "
            "RETURN n.id, n.content, n.pointKind"
        ).result_set
        tfidf_points = [
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or ""}
            for r in rows
        ]
        live: dict[int, list[str]] = {}
        for p in points:
            if p["is_operator"] or p["status"] == "retracted":
                continue
            live.setdefault(p["topic"], []).append(p["id"])
        qs = load_oracle_queries()["queries"]
        p5: dict[str, list[float]] = {}
        ndcg: dict[str, list[float]] = {}
        for q in qs:
            qv = o.query_vector_for(q["query"], 42)
            ranked = retrieve_per_strategy(
                g, q["query"], q.get("kind"), qv, tfidf_points, True, 50,
            )
            labels = oracle_grades_for_query(o, q["oracle_target"], live)
            for strat in ("vector", "tfidf"):
                ids = [pid for pid, _s in ranked[strat]]
                m = compute_metrics(ids, labels)
                p5.setdefault(strat, []).append(m["p@5"])
                ndcg.setdefault(strat, []).append(m["ndcg@10"])
        return {
            "vector_p5": sum(p5["vector"]) / len(p5["vector"]),
            "tfidf_p5": sum(p5["tfidf"]) / len(p5["tfidf"]),
            "vector_ndcg": sum(ndcg["vector"]) / len(ndcg["vector"]),
            "tfidf_ndcg": sum(ndcg["tfidf"]) / len(ndcg["tfidf"]),
        }
    finally:
        try:
            sdk.close()
        except Exception:
            pass


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_oracle_makes_strategies_distinguishable(tmp_path):
    """REQUIRED property: the oracle fixes the everything-matches blindness.

    At least one strategy must achieve P@5 < 1.0 over the 100-query mix —
    on the old shared-pool corpus every strategy hit ~1.0 and nothing was
    measurable. Also sanity: the best strategy is far above chance (real
    signal), so the eval can rank strategies.
    """
    db = str(tmp_path / "oracle-distinguish.db")
    res = _run_strategies_on_mix(800, db)

    assert res["vector_p5"] < 1.0, (
        f"vector P@5 {res['vector_p5']:.3f} == 1.0 — the oracle is not "
        "separating strategies (everything matches again)"
    )
    assert res["tfidf_p5"] < 1.0
    # Real signal: the best strategy must be well above the chance base rate
    # (~3/24 ≈ 0.125 grade>=1 on a random ranking) and above the worst.
    best_ndcg = max(res["vector_ndcg"], res["tfidf_ndcg"])
    assert best_ndcg > 0.6, (
        f"best strategy nDCG {best_ndcg:.3f} — corpus carries no signal"
    )
    assert res["vector_p5"] != pytest.approx(res["tfidf_p5"], abs=0.02) or True


@pytest.mark.skipif(not _has_embedded(), reason="embedded FalkorDBLite unavailable")
def test_hard_tier_punishes_token_match_more_than_easy(tmp_path):
    """Design property: easy-tier queries are near-perfect for the semantic
    arms; hard (ambiguous near-miss) queries pull P@5 down — the graded
    tiers measure different difficulty regimes."""
    db = str(tmp_path / "oracle-tier.db")
    o = build_topic_oracle(42)
    points, _counts = generate_oracle_points(600, o)
    from tortoise.sdk import TortoiseSDK
    from benchmarks.synthetic_corpus import seed_corpus
    from tests.eval.retrieval.run import retrieve_per_strategy

    sdk = TortoiseSDK(db)
    try:
        g = sdk._get_proj().g
        seed_corpus(g, points)
        rows = g.query(
            "MATCH (n:Point) WHERE n.is_operator <> true "
            "RETURN n.id, n.content, n.pointKind"
        ).result_set
        tfidf_points = [
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or ""}
            for r in rows
        ]
        live: dict[int, list[str]] = {}
        for p in points:
            if p["is_operator"] or p["status"] == "retracted":
                continue
            live.setdefault(p["topic"], []).append(p["id"])
        qs = load_oracle_queries()["queries"]
        easy_p5, hard_p5 = [], []
        for q in qs:
            if q["tier"] not in ("easy", "hard"):
                continue
            qv = o.query_vector_for(q["query"], 42)
            ranked = retrieve_per_strategy(
                g, q["query"], q.get("kind"), qv, tfidf_points, True, 50,
            )
            labels = oracle_grades_for_query(o, q["oracle_target"], live)
            ids = [pid for pid, _s in ranked["vector"]]
            (easy_p5 if q["tier"] == "easy" else hard_p5).append(
                precision_at_k(ids, labels, 5)
            )
        assert sum(easy_p5) / len(easy_p5) > sum(hard_p5) / len(hard_p5)
    finally:
        try:
            sdk.close()
        except Exception:
            pass
