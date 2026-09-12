"""A7/#2976 — product reranker seam: tri-state gate, budget guard, one impl.

Hermetic (no DB, no model, no network): the cross-encoder is never loaded —
the deterministic in-repo ``FakeScorer`` is injected through the
``tortoise.rerank.get_scorer`` seam. Pins the four #2976 acceptance
contracts:

  1. **default-off is byte-identical** — the off path returns the untouched
     pool and never even loads a scorer;
  2. **flag-on reorders deterministically** — the same input scores the same
     way every run (FakeScorer + greedy MMR);
  3. **the context/token budget guard refuses and DECLARES** — a reranked set
     over the same caps ``assemble_context`` enforces degrades to the
     unreranked order with a reason, never a silent truncation;
  4. **one implementation** — the product and the eval lane expose the SAME
     scoring function objects (the #2976 de-fork; a re-fork reds here).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import rerank  # noqa: E402, RUF100

#: 3 hits in a deliberately WRONG retrieval order: x0 is irrelevant, x1 has
#: partial query overlap, x2 is the best match. A working reranker promotes
#: x2 to rank 0. MMR then keeps x1 (partial) over x0.
HITS: list[dict] = [
    {"id": "x0", "content": "weather forecast rain", "session_id": "s1"},
    {"id": "x1", "content": "gym session at 5pm", "session_id": "s2"},
    {"id": "x2", "content": "gym gym gym", "session_id": "s3"},
]
QUERY = "when is the gym session"


@pytest.fixture(autouse=True)
def _clean_rerank_state(monkeypatch):
    """Pin the ask-lane knobs to their documented defaults and clear the
    module-level scorer caches around every test (a leaked default model
    failure must not change a later expectation)."""
    for name in ("TORTOISE_ASK_RERANK", "TORTOISE_ASK_RERANK_MODEL",
                 "TORTOISE_ASK_RERANK_CAP", "TORTOISE_ASK_RERANK_LAMBDA"):
        monkeypatch.delenv(name, raising=False)
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()
    yield
    rerank._scorer_cache.clear()
    rerank._fail_cache.clear()


def _inject_fake(monkeypatch):
    monkeypatch.setattr(rerank, "get_scorer",
                        lambda model=None: (rerank.FakeScorer(), ""))


# ── 1. tri-state gate + default-off is byte-identical ──────────────────────

def test_flag_is_tri_state_and_fails_safe_off():
    assert rerank.rerank_enabled(True) is True
    assert rerank.rerank_enabled(False) is False
    assert rerank.rerank_enabled(None) is False          # env unset → OFF
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        with pytest.MonkeyPatch.context() as m:
            m.setenv("TORTOISE_ASK_RERANK", truthy)
            assert rerank.rerank_enabled(None) is True
    for falsy in ("", "0", "no", "garbage", "2"):
        with pytest.MonkeyPatch.context() as m:
            m.setenv("TORTOISE_ASK_RERANK", falsy)
            assert rerank.rerank_enabled(None) is False


def test_off_path_is_untouched_and_never_loads_a_scorer(monkeypatch):
    def _must_not_load(*a, **k):  # pragma: no cover — fires only on regression
        raise AssertionError("scorer loaded on the rerank-OFF path")

    monkeypatch.setattr(rerank, "get_scorer", _must_not_load)
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=False)
    assert out == HITS                      # byte-identical order/content
    assert stats == {"applied": False, "degrade_reason": "disabled"}


# ── 2. flag-on reorders deterministically ──────────────────────────────────

def test_on_reorders_deterministically(monkeypatch):
    _inject_fake(monkeypatch)
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True)
    assert [h["id"] for h in out] == ["x2", "x1"]
    assert stats["applied"] is True
    assert stats["selected_count"] == 2
    assert stats["moved"] == 1
    # rerun → identical selection/order (deterministic, no model randomness)
    out2, stats2 = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True)
    assert [h["id"] for h in out2] == [h["id"] for h in out]
    assert stats2 == stats


def test_score_failure_degrades_to_untouched(monkeypatch):
    class _Boom:
        def score(self, query, contents):
            raise RuntimeError("scorer exploded")

    monkeypatch.setattr(rerank, "get_scorer",
                        lambda model=None: (_Boom(), ""))
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True)
    assert out == HITS
    assert stats["applied"] is False
    assert "scorer exploded" in stats["degrade_reason"]


# ── 3. context/token budget guard ──────────────────────────────────────────

def test_token_budget_guard_refuses_and_declares(monkeypatch):
    _inject_fake(monkeypatch)
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True,
        max_context_tokens=1)
    assert out == HITS                       # unreranked order, not truncated
    assert stats["applied"] is False
    assert stats["degrade_reason"].startswith(
        "reranked-set-exceeds-context-budget")
    assert stats["budget_tokens"] > 1
    assert stats["max_context_tokens"] == 1


def test_byte_budget_guard_refuses_and_declares(monkeypatch):
    _inject_fake(monkeypatch)
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True,
        max_context_bytes=1)
    assert out == HITS
    assert stats["applied"] is False
    assert "bytes" in stats["degrade_reason"]
    assert stats["budget_bytes"] > 1


def test_budget_guard_passes_when_the_reranked_set_fits(monkeypatch):
    _inject_fake(monkeypatch)
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True,
        max_context_tokens=100_000, max_context_bytes=10_000_000)
    assert [h["id"] for h in out] == ["x2", "x1"]
    assert stats["applied"] is True


def test_guard_reason_is_empty_when_the_set_fits():
    reasons, tokens, nbytes = rerank.context_budget_overrun(
        HITS, max_context_tokens=100_000, max_context_bytes=10_000_000)
    assert reasons == [] and tokens > 0 and nbytes > 0


def test_guard_estimate_agrees_with_the_assembly_accounting():
    """The guard reads the SAME estimator ``assemble_context`` enforces, so
    the two can never disagree about what fits."""
    from tortoise.retrieval import estimate_tokens, render_context
    text = render_context(HITS, question_date="2026-01-01")
    assert rerank.context_budget_overrun(
        HITS, question_date="2026-01-01")[1] == estimate_tokens(text)
    assert len(text.encode("utf-8")) == rerank.context_budget_overrun(
        HITS, question_date="2026-01-01")[2]


# ── 4. optional-dep inertness + one implementation ─────────────────────────

def test_missing_cross_encoder_is_inert_not_broken(monkeypatch):
    def _no_sentence_transformers(*a, **k):
        raise ImportError("No module named 'sentence_transformers'")

    monkeypatch.setattr(rerank, "CrossEncoderScorer",
                        _no_sentence_transformers)
    scorer, reason = rerank.get_scorer("ci-model-not-installed")
    assert scorer is None and "sentence_transformers" in reason
    out, stats = rerank.ask_lane_rerank(
        QUERY, list(HITS), proj=None, top_k=2, enabled=True)
    assert out == HITS                       # degrades, never raises
    assert stats["applied"] is False
    assert stats["degrade_reason"]


def test_scoring_logic_has_one_implementation():
    """#2976 de-fork guard: the eval lane re-exports the PRODUCT's scoring
    objects — a re-forked copy would fail this identity check."""
    from tools.longmem_eval import rerank as eval_rerank
    for name in ("CrossEncoderScorer", "FakeScorer", "mmr_select",
                 "rerank_hits", "load_scorer", "_pair_sim",
                 "_fetch_embeddings", "_env_int", "_env_float",
                 "RERANK_MODEL_DEFAULT", "RERANK_TRUNCATE_CHARS"):
        assert getattr(eval_rerank, name) is getattr(rerank, name), name


def test_byte_guard_charges_the_assembly_framing():
    """The byte guard must be at least as strict as ``assemble_context``,
    which also charges the per-block separator bytes — otherwise a set that
    'fits' the guard can still lose its lowest-ranked hit at assembly (the
    silent truncation the guard exists to prevent)."""
    _, _, nbytes = rerank.context_budget_overrun(HITS)
    reasons, _, _ = rerank.context_budget_overrun(
        HITS, max_context_bytes=nbytes + 1)      # +2 framing no longer fits
    assert reasons and "bytes" in reasons[0]
    ok, _, _ = rerank.context_budget_overrun(
        HITS, max_context_bytes=nbytes + 2)      # exactly the framing slack
    assert ok == []


def test_budget_guard_never_raises_on_a_malformed_hit(monkeypatch):
    """The A7 never-raise contract holds even when the guard renders a hit
    the scorer path never touched (e.g. a non-dict ``superseded_by``)."""
    _inject_fake(monkeypatch)
    hits = [{"id": "a", "content": "gym gym", "session_id": "s",
             "superseded_by": ["not-a-dict"]}]
    out, stats = rerank.ask_lane_rerank(
        QUERY, hits, proj=None, top_k=1, enabled=True,
        max_context_tokens=8000)
    assert out == hits
    assert stats["applied"] is False
    assert stats["degrade_reason"].startswith(
        "reranked-set-context-check-failed")
