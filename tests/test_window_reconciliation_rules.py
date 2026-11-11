"""#2570 (C3-4 / #2519 P3) — stratified window reconciliation: hermetic rules.

Pure-function + wiring tests for the P3 labeled-variant arm
(``tools/longmem_eval/window_reconciliation.py`` + the ``run.py``
wiring). No graph, no LLM, no network, no env mutation outside
``monkeypatch`` — runs on any lane (embedded or docker).

The arm's honesty contract is the thing these tests exist to pin:

  * the failing class is detected from ONE-SHOT outcome fields only, and
    never fires on an ``_abs`` question, an ungraded question, or a type
    outside the class;
  * the raised window scales the resolved base knobs and nothing else;
  * the per-question cap is a real admission gate on the extra
    generation, and its metering excludes the ingest burn (it is a
    RE-RUN budget, not a whole-question budget);
  * the re-run's results live under ``variant_*`` keys — the official
    ``recall_all@k`` (derived from the one-shot ``session_recall@k``) is
    BIT-IDENTICAL whether or not the variant keys are present.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.longmem_eval import run as runner
from tools.longmem_eval import usage as lme_usage
from tools.longmem_eval import w7_publish
from tools.longmem_eval import window_reconciliation as wr


# ── the failing-class predicate (one-shot outcome fields only) ──────────

def _reason(**over):
    base = dict(question_id="q_msr_1", question_type="multi-session",
                label=False, hypothesis="the answer is 3", reader_surface=None)
    base.update(over)
    return wr.failing_class_reason(**base)


def test_abstention_on_non_abs_msr_fires_reason_a():
    """The reader's own abstention vocabulary (clause-scoped, the product
    authority) fires the abstention reason on a non-_abs MSR question."""
    assert _reason(hypothesis="I don't know the answer to that.",
                   reader_surface={"5": 1.0}) == wr.REASON_ABSTENTION


def test_abstention_on_preference_fires():
    assert _reason(question_type="single-session-preference",
                   hypothesis="The asked information is absent from the "
                              "context.") == wr.REASON_ABSTENTION


def test_abs_question_never_fires_even_when_abstained():
    """An ``_abs`` question MUST abstain — re-running it on a wider window
    would be measuring the opposite of the intent."""
    assert _reason(question_id="q_msr_1_abs",
                   hypothesis="I don't know.") is None


def test_other_question_types_never_fire():
    for qt in ("single-session-user", "single-session-assistant",
               "temporal-reasoning", "knowledge-update", ""):
        assert _reason(question_type=qt) is None


def test_ungraded_question_never_fires():
    """Retrieval-only / reader-failure outcomes carry label=None — there is
    no outcome field to decide from, so the arm never spends."""
    assert _reason(label=None, hypothesis=None) is None


def test_judge_miss_with_low_reader_surface_fires_reason_b():
    assert _reason(label=False, reader_surface={"5": 0.5}) == \
        wr.REASON_JUDGE_MISS_LOW_SURFACE


def test_judge_miss_with_full_surface_does_not_fire():
    """All evidence-bearing content was already in the window — the window
    is not the suspected cause (this is the arm's own falsification guard)."""
    assert _reason(label=False, reader_surface={"5": 1.0}) is None


def test_judge_miss_with_absent_surface_is_unknown_not_lt_one():
    """Vacuity (no evidence-bearing content, so no measurable surface) is
    UNKNOWN — never coerced into ``< 1.0`` (fail-safe: no spend on an
    unproven cause)."""
    assert _reason(label=False, reader_surface={"5": None}) is None
    assert _reason(label=False, reader_surface={}) is None
    assert _reason(label=False, reader_surface=None) is None


def test_correct_answer_with_low_surface_never_fires():
    assert _reason(label=True, reader_surface={"5": 0.0}) is None


def test_reader_surface_value_is_k_independent_with_fallback():
    """The metric is k-independent by construction; a missing record for the
    requested k falls back to any recorded k, and a malformed one is None."""
    assert wr.reader_surface_value({"5": 0.25}, 5) == 0.25
    assert wr.reader_surface_value({"20": 0.25}, 5) == 0.25
    assert wr.reader_surface_value({"5": True}, 5) is None
    assert wr.reader_surface_value({"5": "0.5"}, 5) is None
    assert wr.reader_surface_value({}, 5) is None


# ── the raised window: scales the resolved base knobs ───────────────────

def test_raised_window_is_the_factor_on_all_three_dimensions():
    raised = wr.raised_window(top_k=20, context_item_cap=40,
                              max_context_tokens=8000)
    assert raised == {"top_k": 40, "context_item_cap": 80,
                      "max_context_tokens": 16000}


def test_raised_window_clamps_and_rejects_a_bad_factor():
    raised = wr.raised_window(top_k=10_000, context_item_cap=10_000,
                              max_context_tokens=10_000)
    assert raised["top_k"] == wr.MAX_RAISED_TOP_K
    assert raised["context_item_cap"] == wr.MAX_RAISED_CONTEXT_ITEM_CAP
    assert raised["max_context_tokens"] == 20_000
    with pytest.raises(ValueError):
        wr.raised_window(top_k=5, context_item_cap=5, max_context_tokens=5,
                         factor=0)


def test_raise_factor_default_is_pinned_to_two():
    assert wr.DEFAULT_WINDOW_RAISE_FACTOR == 2


# ── the per-question budget: metering + admission ───────────────────────

def _envelope(reader=(100, 10), judge=(20, 2), ingest=(9999, 9999)):
    env: dict = {"by_stage": {}}
    if reader:
        env["by_stage"]["reader"] = {"openrouter": {"m": {
            "prompt_tokens": reader[0], "completion_tokens": reader[1]}}}
    if judge:
        env["by_stage"]["judge"] = {"openrouter": {"m": {
            "prompt_tokens": judge[0], "completion_tokens": judge[1]}}}
    if ingest:
        env["by_stage"]["extractor"] = {"openrouter": {"m": {
            "prompt_tokens": ingest[0], "completion_tokens": ingest[1]}}}
    return env


def test_stage_tokens_sums_only_named_stages_and_never_raises():
    env = _envelope()
    assert wr.stage_tokens(env, "reader") == 110
    assert wr.stage_tokens(env, "reader", "judge") == 132
    assert wr.stage_tokens(None, "reader") == 0
    assert wr.stage_tokens({"by_stage": "nonsense"}, "reader") == 0
    assert wr.stage_tokens({"by_stage": {"reader": {"p": {"m": "x"}}}},
                           "reader") == 0


def test_marginal_bound_excludes_the_ingest_burn():
    """The cap bounds the RE-RUN, not the whole question: the extractor's
    burn must not make the arm un-admittable (it would otherwise never fire
    on a real run)."""
    env = _envelope(ingest=(500_000, 500_000))
    assert wr.marginal_bound(env) == 2 * 110 + 22


def test_envelope_delta_is_the_reader_plus_judge_growth_never_negative():
    before = _envelope(reader=(100, 10), judge=(20, 2))
    after = _envelope(reader=(150, 20), judge=(25, 3))
    assert wr.envelope_delta(before, after) == 66
    assert wr.envelope_delta(after, before) == 0


def test_admission_is_inclusive_at_the_cap_and_refuses_above():
    assert wr.admit_reconciliation(bound=24000, cap=24000) is True
    assert wr.admit_reconciliation(bound=24001, cap=24000) is False


def test_default_token_cap_fits_a_raised_full_window_re_ask():
    """Sanity pin on the default's sizing rationale: a raised (2×) re-ask of
    a full 8000-token reader context plus a judge pass fits the cap."""
    one_shot = _envelope(reader=(8000, 300), judge=(400, 10), ingest=(0, 0))
    assert wr.marginal_bound(one_shot) <= \
        wr.DEFAULT_RECONCILIATION_TOKEN_CAP


# ── the collector snapshot the arm prices itself from ───────────────────

def test_peek_question_is_non_destructive_and_drain_still_works():
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key("q_peek")
    try:
        collector.record(stage="reader", provider="openrouter",
                         model_id="m", usage={"prompt_tokens": 7},
                         usage_present=True)
        first = collector.peek_question("q_peek")
        second = collector.peek_question("q_peek")
        assert first == second
        assert first["total"]["prompt_tokens"] == 7
        # the cumulative envelope the outcome carries is still complete
        drained = collector.drain_question("q_peek")
        assert drained["total"]["prompt_tokens"] == 7
        assert collector.peek_question("q_peek") is None
    finally:
        lme_usage.clear_question_key()
        collector.reset()


def test_peek_question_missing_qid_is_none():
    collector = lme_usage.get_collector()
    collector.reset()
    try:
        assert collector.peek_question("nothing_here") is None
    finally:
        collector.reset()


# ── the re-run wiring (fakes; no graph, no LLM) ─────────────────────────

class _FakeReader:
    def __init__(self, answer: str = "42", extra_tokens: int = 0):
        self._answer = answer
        self._extra = extra_tokens
        self.calls: list[dict] = []

    def answer(self, *, context_hits, question, question_date,
               question_type):
        self.calls.append({"context_hits": context_hits, "question": question,
                           "question_date": question_date,
                           "question_type": question_type})
        if self._extra:
            lme_usage.get_collector().record(
                stage="reader", provider="openrouter", model_id="m",
                usage={"prompt_tokens": self._extra}, usage_present=True)
        return self._answer


class _FakeJudge:
    def __init__(self, verdict: bool = True):
        self._verdict = verdict
        self.calls: list[dict] = []

    def judge(self, *, question_type, question, answer, hypothesis,
              abstention):
        self.calls.append({"hypothesis": hypothesis,
                           "abstention": abstention})
        if self._verdict:
            lme_usage.get_collector().record(
                stage="judge", provider="openrouter", model_id="m",
                usage={"prompt_tokens": 5}, usage_present=True)
        return self._verdict


QUESTION = {
    "question_id": "q_msr_1",
    "question_type": "multi-session",
    "question": "how many bikes did I buy?",
    "question_date": "2026-01-01",
    "answer": "three",
}


def _one_shot_ret(surface: float | None, recalled: bool = False) -> dict:
    return {
        "context_points": [{"id": "p1", "content": "bike", "session_id": "s1"}],
        "reader_surface@k": {"5": surface},
        "session_recall@k": {"5": 1.0 if recalled else 0.5},
    }


def _recon(monkeypatch, *, ret, label, hypothesis, reader=None, judge=None,
           cap=24000, raised=None, retrieval_calls=None, qid="q_msr_1"):
    calls = retrieval_calls if retrieval_calls is not None else []

    def _fake_retrieve(sdk, question, *, ks, top_k, **kwargs):
        calls.append({"ks": ks, "top_k": top_k, **kwargs})
        return _one_shot_ret(1.0, recalled=True)

    monkeypatch.setattr(runner, "retrieve_for_question", _fake_retrieve)
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key(qid)
    try:
        stats = runner._reconcile_failing_question(
            sdk=object(), reader=reader or _FakeReader(),
            judge=judge or _FakeJudge(), question=QUESTION, qid=qid,
            ret=ret, label=label, hypothesis=hypothesis, ks=(5,),
            retrieval_kwargs={"retriever": "hybrid",
                              "evidence_boost": False,
                              "coverage_loop": False},
            raised=raised or {"top_k": 40, "context_item_cap": 80,
                              "max_context_tokens": 16000},
            cap=cap, max_retries=0)
    finally:
        lme_usage.clear_question_key()
        collector.reset()
    return stats


def test_wiring_no_fire_outcome_is_untouched_and_no_retrieval_runs(
        monkeypatch):
    """A question outside the failing class (correct answer, full surface)
    returns a marker that is explicitly not-fired and costs NOTHING."""
    calls: list = []
    stats = _recon(monkeypatch, ret=_one_shot_ret(1.0), label=True,
                   hypothesis="three", retrieval_calls=calls)
    assert stats["fired"] is False
    assert stats["admitted"] is False
    assert stats["variant_label"] is None
    assert calls == []


def test_wiring_admitted_pass_uses_the_raised_window_and_labels_the_variant(
        monkeypatch):
    """The re-run passes the RAISED top_k + item/token dimensions and the
    SAME arm config, re-asks reader + judge, and records the variant under
    variant_* keys."""
    calls: list = []
    reader = _FakeReader(answer="three")
    judge = _FakeJudge(verdict=True)
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.4), label=False,
                   hypothesis="two", reader=reader, judge=judge,
                   retrieval_calls=calls)
    assert stats["fired"] is True
    assert stats["reason"] == wr.REASON_JUDGE_MISS_LOW_SURFACE
    assert stats["admitted"] is True
    assert stats["variant_label"] is True
    assert stats["variant_hypothesis"] == "three"
    assert stats["reader_surface_after"] == 1.0
    # the raise is the ONE variable: same arm config, raised window
    assert len(calls) == 1
    assert calls[0]["top_k"] == 40
    assert calls[0]["context_item_cap"] == 80
    assert calls[0]["max_context_tokens"] == 16000
    assert calls[0]["retriever"] == "hybrid"
    assert calls[0]["coverage_loop"] is False
    assert reader.calls and reader.calls[0]["context_hits"]
    assert judge.calls and judge.calls[0]["abstention"] is False


def test_wiring_abstention_fires_reason_a(monkeypatch):
    stats = _recon(monkeypatch, ret=_one_shot_ret(1.0), label=False,
                   hypothesis="I don't know, the information is absent.")
    assert stats["fired"] is True
    assert stats["reason"] == wr.REASON_ABSTENTION


def test_wiring_cap_skips_the_pass_when_the_bound_does_not_fit(monkeypatch):
    """The cap is a real gate: a question whose own one-shot reader burn puts
    the conservative bound over the cap is NOT re-run."""
    calls: list = []
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key("q_msr_1")
    try:
        collector.record(stage="reader", provider="openrouter", model_id="m",
                         usage={"prompt_tokens": 20000}, usage_present=True)
        monkeypatch.setattr(
            runner, "retrieve_for_question",
            lambda *a, **k: calls.append(k) or _one_shot_ret(1.0))
        stats = runner._reconcile_failing_question(
            sdk=object(), reader=_FakeReader(), judge=_FakeJudge(),
            question=QUESTION, qid="q_msr_1", ret=_one_shot_ret(0.2),
            label=False, hypothesis="two", ks=(5,),
            retrieval_kwargs={"retriever": "hybrid"},
            raised={"top_k": 40, "context_item_cap": 80,
                    "max_context_tokens": 16000},
            cap=1000, max_retries=0)
    finally:
        lme_usage.clear_question_key()
        collector.reset()
    assert stats["fired"] is True
    assert stats["admitted"] is False
    assert stats["skip_reason"] == "token_cap"
    assert stats["variant_label"] is None
    assert calls == []


def test_wiring_cap_exceeded_is_disclosed(monkeypatch):
    """An admitted pass whose ACTUAL marginal spend exceeded the cap is
    recorded, flagged — never hidden (the census is honest about overshoot)."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two", reader=_FakeReader(extra_tokens=5000),
                   cap=100)
    assert stats["admitted"] is True
    assert stats["marginal_tokens"] == 5005
    assert stats["cap_exceeded"] is True


# ── the labeled-variant block (never the official number) ───────────────

def _variant_outcome(qid: str, one_shot: bool, variant: bool | None = None,
                     fired: bool = True, admitted: bool = True,
                     surface_before: float = 0.5,
                     surface_after: float | None = 1.0,
                     recall_before: float = 0.5,
                     recall_after: float = 0.5,
                     cap_exceeded: bool = False,
                     marginal: int = 100) -> dict:
    out: dict = {
        "question_id": qid,
        "question_type": "multi-session",
        "label": one_shot,
        "session_recall@k": {"5": recall_before},
    }
    stats = {"fired": fired, "admitted": admitted, "cap": 24000,
             "reader_surface_before": surface_before,
             "reader_surface_after": surface_after,
             "session_recall_before": recall_before,
             "session_recall_after": recall_after,
             "cap_exceeded": cap_exceeded,
             "marginal_tokens": marginal}
    if variant is not None:
        stats["variant_label"] = variant
    out["window_reconciliation"] = True
    out["window_reconciliation_stats"] = stats
    return out


def test_variant_rows_pair_the_subset_and_count_the_flips():
    outcomes = [
        _variant_outcome("q1", one_shot=False, variant=True),   # recovered
        _variant_outcome("q2", one_shot=True, variant=True),    # kept
        _variant_outcome("q3", one_shot=True, variant=False),   # lost
        _variant_outcome("q4", one_shot=False, variant=False),  # kept miss
    ]
    rows = wr.reconciliation_variant_rows(outcomes, k=5)
    assert rows["n_fired"] == 4 and rows["n_admitted"] == 4
    assert rows["n_graded"] == 4
    assert rows["one_shot_accuracy"] == 0.5
    assert rows["value"] == 0.5
    assert rows["recovered"] == 1 and rows["lost"] == 1
    assert rows["net_flips"] == 0


def test_variant_rows_count_the_cap_census_and_surface_delta():
    outcomes = [
        _variant_outcome("q1", one_shot=False, variant=True),
        _variant_outcome("q2", one_shot=False, admitted=False,
                         variant=None, surface_after=None),
        _variant_outcome("q3", one_shot=False, variant=True,
                         cap_exceeded=True, marginal=99_000),
    ]
    rows = wr.reconciliation_variant_rows(outcomes, k=5)
    assert rows["n_fired"] == 3
    assert rows["n_admitted"] == 2
    assert rows["n_skipped_token_cap"] == 1
    assert rows["cap_exceeded_n"] == 1
    assert rows["token_cap"] == 24000
    assert rows["marginal_tokens_total"] == 100 + 99_000  # admitted only
    assert rows["reader_surface@5"]["before"] == pytest.approx(0.5)
    assert rows["reader_surface@5"]["after"] == pytest.approx(1.0)
    assert rows["reader_surface@5"]["n"] == 2


def test_variant_rows_pool_drift_is_surfaced():
    outcomes = [
        _variant_outcome("q1", one_shot=False, variant=True,
                         recall_before=0.5, recall_after=0.5),
        _variant_outcome("q2", one_shot=False, variant=True,
                         recall_before=0.5, recall_after=1.0),
    ]
    rows = wr.reconciliation_variant_rows(outcomes, k=5)
    assert rows["pool_drift_n"] == 1


def test_variant_rows_empty_when_the_arm_never_fired():
    rows = wr.reconciliation_variant_rows([{"question_id": "q1",
                                            "label": True}], k=5)
    assert rows["n_fired"] == 0 and rows["n_admitted"] == 0
    assert rows["value"] is None
    assert "LABELED VARIANT" in rows["label"]


def test_official_recall_all_is_bit_identical_with_and_without_the_variant():
    """THE honesty contract: the official row derives from the one-shot
    ``session_recall@k`` alone. Adding the arm's keys must not move it."""
    plain = [
        {"question_id": "q1", "session_recall@k": {"5": 1.0}, "label": False},
        {"question_id": "q2", "session_recall@k": {"5": 0.5}, "label": False},
    ]
    ar = [dict(o) for o in plain]
    ar[1].update(_variant_outcome("q2", one_shot=False, variant=True,
                                  recall_before=0.5, recall_after=1.0))
    plain_rows = w7_publish.variant_rows(plain, k=5)
    arm_rows = w7_publish.variant_rows(ar, k=5)
    assert (plain_rows["official_recall_all@5"]["value"]
            == arm_rows["official_recall_all@5"]["value"] == 0.5)
    assert (plain_rows["official_recall_all@5"]["n"]
            == arm_rows["official_recall_all@5"]["n"] == 2)
    assert plain_rows["window_reconciliation"]["n_fired"] == 0
    assert arm_rows["window_reconciliation"]["n_fired"] == 1


# ── arm resolution + fingerprint (the checkpoint contract) ──────────────

def test_cap_resolver_is_inert_while_the_arm_is_off(monkeypatch):
    monkeypatch.setenv("TORTOISE_LME_WINDOW_RECONCILIATION_CAP", "12345")
    assert runner._resolve_window_reconciliation_cap(False) is None
    assert runner._resolve_window_reconciliation_cap(
        False, explicit=999) is None
    assert runner._resolve_window_reconciliation_cap(True) == 12345


def test_cap_resolver_clamps_garbage_to_the_default(monkeypatch):
    monkeypatch.delenv("TORTOISE_LME_WINDOW_RECONCILIATION_CAP",
                       raising=False)
    assert runner._resolve_window_reconciliation_cap(True) == \
        wr.DEFAULT_RECONCILIATION_TOKEN_CAP
    # a silent zero-token cap would admit nothing and look like a dead arm
    assert runner._resolve_window_reconciliation_cap(True, explicit=0) == \
        wr.DEFAULT_RECONCILIATION_TOKEN_CAP
    assert runner._resolve_window_reconciliation_cap(True, explicit=500) \
        == 500


def _fingerprint(**over):
    kwargs = dict(
        reader_model="mock-reader", judge_model="mock-judge", ks=(5,),
        top_k=5, split="s", ingest_mode="v2", extractor_model=None,
        max_retries=0, dataset_fingerprint="unknown",
        rerank_config=runner._resolve_rerank(
            rerank=None, rerank_model=None, rerank_pool=None,
            per_session_cap=None, mmr_lambda=None, max_k=5)["config"])
    kwargs.update(over)
    return runner._build_fingerprint(**kwargs)


def test_fingerprint_always_carries_the_arm_and_caps_only_when_armed():
    off = _fingerprint()
    assert off["window_reconciliation"] is False
    assert "window_reconciliation_token_cap" not in off
    on = _fingerprint(window_reconciliation=True,
                      window_reconciliation_token_cap=24000)
    assert on["window_reconciliation"] is True
    assert on["window_reconciliation_token_cap"] == 24000


def test_fingerprint_refuses_an_arm_flip_or_a_cap_change():
    """An arm-ON checkpoint cannot be resumed with the arm OFF, and two cap
    volumes can never blend into one artifact."""
    on = _fingerprint(window_reconciliation=True,
                      window_reconciliation_token_cap=24000)
    assert runner._fingerprint_diffs(on, _fingerprint())
    assert runner._fingerprint_diffs(
        on, _fingerprint(window_reconciliation=True,
                         window_reconciliation_token_cap=12000))
    assert not runner._fingerprint_diffs(on, on)
