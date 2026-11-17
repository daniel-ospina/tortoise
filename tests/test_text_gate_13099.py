"""#13099 — invariants the text-gate measurement must not lose.

These pin the parts of `tools/text_gate_13099.py` that would silently make the
published number worthless if they drifted, and they need no database:

* **the corpus is deterministic**, so the figures are reproducible at all;
* **the committed queries are reused verbatim** — a re-typed or re-ordered query
  set would break comparability to the recorded figures, which is the single
  most likely way this measurement produces a wrong answer;
* **the committed grade function is untouched**, so no label is invented;
* **the question class really is degenerate**, which the README offers as
  non-evidence — if that ever stops being true the README's caveat is wrong and
  this test says so;
* **the decision rule keys on the CI's DIRECTION.** This is the one that matters
  most: a significant GAIN is not a reason to keep the in-graph index, and a
  verdict function that treated "not within noise" as "disqualify" would reverse
  the decision the issue exists to test.

Run without a graph (the repo's suite needs its DB seam, so use the lane form)::

    uv run python tools/test_lane.py run -- tests/test_text_gate_13099.py -q
"""
from __future__ import annotations

import hashlib

from benchmarks.synthetic_corpus import build_topic_oracle, generate_oracle_points
from tools.text_gate_13099 import (
    N_POINTS,
    QUERY_CLASSES,
    SEED,
    build_corpus,
    build_query_classes,
    labels_for,
    load_committed_queries,
    oov_evidence,
    verdict,
)


def _corpus_digest() -> str:
    _, points, live = build_corpus()
    h = hashlib.sha256()
    for p in points:
        h.update(f"{p['id']}\t{p['content']}\t{p['status']}\t{p['topic']}\n".encode())
    for topic in sorted(live):
        h.update(f"{topic}:{','.join(live[topic])}\n".encode())
    return h.hexdigest()


def test_corpus_is_deterministic():
    """Two builds of the committed corpus are identical, byte for byte.

    Without this the artifact is not reproducible and no comparison to the
    recorded figures means anything.
    """
    assert _corpus_digest() == _corpus_digest()


def test_corpus_matches_the_committed_shape():
    oracle, points, _ = build_corpus()
    assert len(points) == N_POINTS
    assert oracle.n == 24
    assert all(p["id"].startswith("p-") for p in points)
    # topics are round-robin from the seed — every topic is populated
    assert len({p["topic"] for p in points}) == oracle.n


def test_short_keyword_class_reuses_the_committed_queries_verbatim():
    """The anchor class must be the committed set, not a re-typed copy."""
    _, points, _ = build_corpus()
    committed = load_committed_queries()
    classes = build_query_classes(build_topic_oracle(seed=SEED), points, committed)

    got = classes["short_keyword"]
    assert len(got) == len(committed) == 100
    for c, q in zip(got, committed, strict=True):
        assert c["text"] == q["query"]
        assert c["target"] == q["oracle_target"]
        assert c["qid"] == q["id"]


def test_all_classes_reuse_the_committed_grade_function():
    """Grade 2 is exactly the topic's live point set — no new labels anywhere."""
    oracle, _points, live = build_corpus()
    for topic in (0, 7, 23):
        labels = labels_for(oracle, live, topic)
        grade2 = {pid for pid, g in labels.items() if g == 2}
        assert grade2 == set(live[topic])
        assert all(g in (0, 1, 2) for g in labels.values())


def test_query_classes_are_populated():
    _, points, _ = build_corpus()
    classes = build_query_classes(
        build_topic_oracle(seed=SEED), points, load_committed_queries())
    assert set(classes) == set(QUERY_CLASSES)
    assert len(classes["short_keyword"]) == 100
    assert len(classes["natural_language_question"]) == 100
    assert len(classes["entity_name"]) == 96  # four documents x 24 topics


def test_question_class_is_measurably_degenerate():
    """The README offers this class as NON-evidence; that claim is measured.

    If the scaffold tokens ever became in-vocabulary, the class would start
    carrying independent signal and the README would need to promote it rather
    than warn about it. Failing here is the signal to revisit the README.
    """
    _, points, _ = build_corpus()
    classes = build_query_classes(
        build_topic_oracle(seed=SEED), points, load_committed_queries())
    ev = oov_evidence(points, classes)
    assert ev["added_scaffold_tokens"] > 500
    assert ev["oov_fraction"] > 0.90


# ── the decision rule — the invariant that protects the decision itself ─────

def _results(arm_status: dict[str, dict[str, tuple[float, float, float]]]) -> dict:
    """Build a minimal `results` shape: {arm: {class: (mean, lower, upper)}}."""
    out: dict = {}
    for cls in ("short_keyword", "entity_name"):
        arms = {
            "in_graph_fulltext": {"metrics": {}, "n_queries": 1},
        }
        for arm, per_cls in arm_status.items():
            mean, lo, hi = per_cls.get(cls, (0.0, 0.0, 0.0))
            arms[arm] = {
                "n_queries": 1,
                "metrics": {},
                "delta_vs_baseline": {
                    "ndcg@10": {"mean": mean, "lower": lo, "upper": hi, "n": 100},
                },
            }
        out[cls] = {"n_queries": 1, "arms": arms}
    return out


def test_a_significant_gain_is_not_a_disqualification():
    """+5 pts with a CI above 0 is BETTER — it must never be read as a loss.

    This is the defect the verdict function exists to avoid: treating "outside
    the noise band" as "disqualify" would reverse the decision the issue tests.
    """
    v = verdict(_results({"pg_ts_rank": {
        "short_keyword": (5.0, 1.0, 9.0),
        "entity_name": (5.0, 1.0, 9.0),
    }}))
    assert v["headline_ndcg@10"]["pg_ts_rank"]["short_keyword"]["status"] == "better"
    assert "pg_ts_rank" in v["qualifying_candidates"]


def test_a_significant_loss_disqualifies():
    v = verdict(_results({"pg_bad": {
        "short_keyword": (-9.0, -14.0, -4.0),
        "entity_name": (0.0, -2.0, 2.0),
    }}))
    assert v["headline_ndcg@10"]["pg_bad"]["short_keyword"]["status"] == "worse"
    assert "pg_bad" not in v["qualifying_candidates"]


def test_a_noise_band_result_qualifies():
    """The middle case: a CI spanning zero is neither a gain nor a loss."""
    v = verdict(_results({"pg_ts_rank": {
        "short_keyword": (2.3, -0.9, 5.5),
        "entity_name": (0.4, -1.0, 1.9),
    }}))
    assert v["headline_ndcg@10"]["pg_ts_rank"]["short_keyword"]["status"] == "within_noise"
    assert "pg_ts_rank" in v["qualifying_candidates"]


def test_the_raw_query_diagnostic_is_never_a_candidate():
    """`in_graph_fulltext_raw` exists to decompose builder-vs-scorer.

    It drops the OR-union the product builds, so it loses by construction. A
    verdict that offered it as an adoptable candidate would be nonsense.
    """
    v = verdict(_results({"in_graph_fulltext_raw": {
        "short_keyword": (40.0, 30.0, 50.0),
        "entity_name": (40.0, 30.0, 50.0),
    }}))
    assert "in_graph_fulltext_raw" not in v["qualifying_candidates"]
    assert "in_graph_fulltext_raw" in v["diagnostic_arms_not_candidates"]


def test_points_are_generated_before_any_graph_is_touched():
    """The corpus must be buildable without a database, or it is not reusable."""
    points, counts = generate_oracle_points(
        N_POINTS, build_topic_oracle(seed=SEED), seed=SEED)
    assert len(points) == N_POINTS and counts
