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

import copy
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
    # ⛔ #2570 review round 3, finding 2: above the guard maximum the
    # dimension cannot be RAISED, but it must never be LOWERED either — a
    # bare ``min`` published a downgrade as a "raised window".
    assert raised["top_k"] == 10_000
    assert raised["context_item_cap"] == 10_000
    assert raised["max_context_tokens"] == 20_000
    with pytest.raises(ValueError):
        wr.raised_window(top_k=5, context_item_cap=5, max_context_tokens=5,
                         factor=0)


def test_raised_window_never_downgrades_a_dimension():
    """The raise is monotonic non-decreasing in every dimension (#2570 R3
    finding 2). 400 is the guard maximum: at it the raise is a no-op, above
    it the value is preserved, and the module's own census must never call a
    smaller window a raise."""
    for base in (1, 40, 399, 400, 500, 10_000):
        raised = wr.raised_window(top_k=base, context_item_cap=base,
                                  max_context_tokens=8000)
        assert raised["top_k"] >= base, (base, raised)
        assert raised["context_item_cap"] >= base, (base, raised)
        assert raised["max_context_tokens"] > 8000
    # at the guard maximum itself the raise is a no-op, not a shrink
    at_max = wr.raised_window(top_k=wr.MAX_RAISED_TOP_K,
                              context_item_cap=wr.MAX_RAISED_CONTEXT_ITEM_CAP,
                              max_context_tokens=8000)
    assert at_max["top_k"] == wr.MAX_RAISED_TOP_K
    assert at_max["context_item_cap"] == wr.MAX_RAISED_CONTEXT_ITEM_CAP
    # …and the token budget carries the re-run's difference on its own
    assert at_max["max_context_tokens"] == 16000


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
    assert wr.marginal_bound(env, raised_max_context_tokens=16000) == (
        16000
        + wr.DEFAULT_RECONCILIATION_PROMPT_HEADROOM
        + wr.DEFAULT_RECONCILIATION_COMPLETION_HEADROOM
        + 22)


def test_marginal_bound_is_denominated_on_the_raised_window():
    """⛔ #2570 review finding 3: the bound must be derived from the RAISED
    WINDOW, never from what the one-shot reader happened to spend.

    An under-filled one-shot context (400 prompt + 50 completion — a short
    preference question) does NOT cap the raised pass: the re-ask may legally
    consume the whole raised budget. A bound of ``2 x observed`` (≈900) would
    admit a pass that then spends ≈16 000, ~17x the number the cap was checked
    against.
    """
    cheap = _envelope(reader=(400, 50), judge=(20, 2))
    expensive = _envelope(reader=(8000, 300), judge=(20, 2))
    assert wr.marginal_bound(cheap, raised_max_context_tokens=16000) == \
        wr.marginal_bound(expensive, raised_max_context_tokens=16000)
    # …and the number is on the scale of the raised window, not of the spend.
    assert wr.marginal_bound(cheap,
                             raised_max_context_tokens=16000) >= 16000


def test_marginal_bound_requires_the_raised_window():
    """No caller may quietly fall back to the one-shot shape."""
    with pytest.raises(TypeError):
        wr.marginal_bound(_envelope())  # type: ignore[call-arg]


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
    raised = wr.raised_window(top_k=20, context_item_cap=40,
                              max_context_tokens=8000)
    assert wr.marginal_bound(
        one_shot,
        raised_max_context_tokens=raised["max_context_tokens"]) <= \
        wr.DEFAULT_RECONCILIATION_TOKEN_CAP


def test_a_raised_window_that_cannot_fit_the_cap_is_refused():
    """The other half of finding 3: because the bound is now on the raised
    window's scale, a base budget large enough to push the raised window past
    the cap is REFUSED BEFORE any spend instead of overshooting post-hoc.

    Default cap 24 000, defaults for the two headrooms, a 400-token judge
    pass: a base ``max_context_tokens`` of 12 000 raises to 24 000, and
    24 000 + headrooms + judge cannot fit — so admission is False. At the
    default 8 000 it is True (the sizing pin above).
    """
    one_shot = _envelope(reader=(8000, 300), judge=(400, 10), ingest=(0, 0))
    big = wr.raised_window(top_k=20, context_item_cap=40,
                           max_context_tokens=12000)
    assert big["max_context_tokens"] == 24000
    bound = wr.marginal_bound(
        one_shot, raised_max_context_tokens=big["max_context_tokens"])
    assert bound > wr.DEFAULT_RECONCILIATION_TOKEN_CAP
    assert wr.admit_reconciliation(
        bound=bound, cap=wr.DEFAULT_RECONCILIATION_TOKEN_CAP) is False


def test_spend_is_unknown_reads_the_usage_present_flags():
    """⛔ #11830 review P1: a bucket whose provider returned NO usage block
    makes ``stage_tokens`` a LOWER BOUND. ``usage.py`` already keeps
    ``usage_present`` conservatively False for exactly this, so the arm must
    ask rather than price the unknown at 0."""
    known = _envelope()
    assert wr.spend_is_unknown(known, "reader", "judge") is False
    # An unmeasured call in one bucket poisons the whole stage.
    unknown = _envelope()
    unknown["by_stage"]["judge"]["openrouter"]["m"]["usage_present"] = False
    assert wr.spend_is_unknown(unknown, "judge") is True
    assert wr.spend_is_unknown(unknown, "reader") is False
    # The counter form is the same signal (``calls_without_usage`` > 0).
    counted = _envelope()
    counted["by_stage"]["reader"]["openrouter"]["m"][
        "calls_without_usage"] = 2
    assert wr.spend_is_unknown(counted, "reader") is True
    # Absent/malformed envelopes know nothing is unknown.
    assert wr.spend_is_unknown(None, "judge") is False
    assert wr.spend_is_unknown({"by_stage": "nonsense"}, "judge") is False


def test_a_sum_over_an_unknown_bucket_understates_the_spend():
    """The lower-bound property the guard above exists for: the same
    envelope reads the same token count whether or not it was measured."""
    known = _envelope()
    unknown = _envelope()
    unknown["by_stage"]["reader"]["openrouter"]["m"]["usage_present"] = False
    assert wr.stage_tokens(known, "reader") == wr.stage_tokens(unknown,
                                                               "reader")


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
    def __init__(self, answer: str = "42", extra_tokens: int = 0,
                 usage_present: bool = True):
        self._answer = answer
        self._extra = extra_tokens
        self._usage_present = usage_present
        self.calls: list[dict] = []

    def answer(self, *, context_hits, question, question_date,
               question_type):
        self.calls.append({"context_hits": context_hits, "question": question,
                           "question_date": question_date,
                           "question_type": question_type})
        lme_usage.get_collector().record(
            stage="reader", provider="openrouter", model_id="m",
            usage=({"prompt_tokens": self._extra} if self._extra else {}),
            usage_present=self._usage_present)
        return self._answer


class _FakeJudge:
    def __init__(self, verdict: bool = True, usage_present: bool = True):
        self._verdict = verdict
        self._usage_present = usage_present
        self.calls: list[dict] = []

    def judge(self, *, question_type, question, answer, hypothesis,
              abstention):
        self.calls.append({"hypothesis": hypothesis,
                           "abstention": abstention})
        if self._verdict:
            lme_usage.get_collector().record(
                stage="judge", provider="openrouter", model_id="m",
                usage={"prompt_tokens": 5},
                usage_present=self._usage_present)
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
    """The cap is a real gate: when the BOUND exceeds the cap the pass is NOT
    re-run.

    ⛔ #2570 review round 3, finding 3: this fixture records its 20 000 tokens
    under ``stage="judge"`` — the ONLY stage ``marginal_bound`` reads for its
    judge term. The previous version recorded them under ``stage="reader"``,
    which the bound deliberately ignores (finding 3 of round 1), so the
    docstring's causal claim was not what the fixture exercised: the bound
    was 19 000 from the window + headrooms alone and the assertion held for a
    reason the prose named wrongly. Pinning the judge term is the point.
    """
    calls: list = []
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key("q_msr_1")
    try:
        collector.record(stage="judge", provider="openrouter", model_id="m",
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
    # the judge term IS in the bound: 16000 + 1000 + 2000 + 20000
    assert stats["marginal_bound"] == 39000
    assert stats["skip_reason"] == "token_cap"
    assert stats["variant_label"] is None
    assert calls == []


def test_wiring_cap_exceeded_is_disclosed(monkeypatch):
    """An admitted pass whose ACTUAL marginal spend exceeded the cap is
    recorded, flagged — never hidden (the census is honest about overshoot).

    With the corrected bound the cap can only be overshot when the bound was
    itself too small, so admission here is granted on cap=20 000 (> the
    19 000 bound) and the pass then spends ~40 000 — the exact shape
    ``bound_exceeded`` exists to publish."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two", reader=_FakeReader(extra_tokens=40000),
                   cap=20000)
    assert stats["admitted"] is True
    assert stats["marginal_tokens"] == 40005
    assert stats["cap_exceeded"] is True
    assert stats["bound_exceeded"] is True


def test_wiring_bound_derives_from_the_raised_window(monkeypatch):
    """⛔ #2570 review finding 3, at the wiring: the bound the arm records is
    on the RAISED WINDOW's scale — a cheap one-shot reader does not shrink it.

    ``raised.max_context_tokens`` (16 000 here) + the two declared headrooms +
    the one-shot judge tokens. The judge contributes 0 in this harness (the
    one-shot judge call is not part of the unit under test), so the expected
    value is the window + headrooms exactly."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two")
    assert stats["admitted"] is True
    assert stats["marginal_bound"] == (
        16000
        + wr.DEFAULT_RECONCILIATION_PROMPT_HEADROOM
        + wr.DEFAULT_RECONCILIATION_COMPLETION_HEADROOM)
    assert stats["bound_exceeded"] is False


def test_wiring_bound_exceeded_is_disclosed(monkeypatch):
    """A pass that spends past the declared bound is a census row, not
    silence — that row is what would falsify the bound on a live run."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two",
                   reader=_FakeReader(extra_tokens=40000), cap=1_000_000)
    assert stats["admitted"] is True
    assert stats["bound_exceeded"] is True
    assert stats["cap_exceeded"] is False


def test_wiring_unmetered_spend_is_not_certified_as_within_budget(
        monkeypatch):
    """⛔ #11830 review P1: an admitted pass whose reader/judge returned no
    usage block spends an UNMEASURED amount. Publishing
    ``cap_exceeded=False`` for it certifies 'within budget' from a lower
    bound — so both flags go NULL (not measured) and the row is flagged."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two",
                   reader=_FakeReader(usage_present=False),
                   judge=_FakeJudge(usage_present=False), cap=1_000_000)
    assert stats["admitted"] is True
    assert stats["marginal_unmetered"] is True
    # ⛔ None, not False: False is the claim "we measured it and it fit".
    assert stats["cap_exceeded"] is None
    assert stats["bound_exceeded"] is None


def test_wiring_a_measured_pass_is_certified_normally(monkeypatch):
    """The negative control for the row above — a measured pass still gets a
    real boolean verdict, so the guard cannot quietly disarm the census."""
    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two", cap=1_000_000)
    assert stats["marginal_unmetered"] is False
    assert stats["cap_exceeded"] is False
    assert stats["bound_exceeded"] is False


def test_wiring_never_mutates_the_one_shot_result_or_its_label(
        monkeypatch):
    """★ THE honesty contract, pinned THROUGH THE WIRING rather than at the
    renderer (#11830 review P2): the official number derives from the
    one-shot ``ret``/``label``/``hypothesis``, so the arm must leave every
    one of them byte-identical. The variant values live only in
    ``variant_*``"""
    ret = _one_shot_ret(0.2, recalled=False)
    ret_before = copy.deepcopy(ret)
    hypothesis = "one"
    stats = _recon(monkeypatch, ret=ret, label=False, hypothesis=hypothesis,
                   cap=1_000_000)
    assert stats["admitted"] is True
    # The one-shot retrieval result is untouched (a raised pass has its own).
    assert ret == ret_before
    # The one-shot label/hypothesis are not the ones the arm produced.
    assert stats["variant_label"] is True
    assert stats["variant_hypothesis"] == "42"


# ── containment: the optional pass may never decide the question's fate ──

def test_wiring_a_failing_variant_pass_is_recorded_not_raised(monkeypatch):
    """⛔ #2570 review finding 2.

    The pass runs inside the per-question ``try`` whose handler DROPS the
    outcome on an exception, and the one-shot outcome is built only after it
    returns — so an uncaught error here would discard an already-measured
    question from the official population, mislabel ``_stage``, and (under
    ``revalidate``) abort the run. It must return a marker instead.
    """
    def _boom(*a, **k):
        raise RuntimeError("provider wobble")

    monkeypatch.setattr(runner, "retrieve_for_question", _boom)
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key("q_msr_1")
    try:
        stats = runner._reconcile_failing_question(
            sdk=object(), reader=_FakeReader(), judge=_FakeJudge(),
            question=QUESTION, qid="q_msr_1", ret=_one_shot_ret(0.2),
            label=False, hypothesis="two", ks=(5,),
            retrieval_kwargs={"retriever": "hybrid"},
            raised={"top_k": 40, "context_item_cap": 80,
                    "max_context_tokens": 16000},
            cap=24000, max_retries=0)
    finally:
        lme_usage.clear_question_key()
        collector.reset()
    assert stats["fired"] is True      # it WAS in the failing class
    assert stats["admitted"] is True   # …and it was attempted
    assert stats["skip_reason"] == "error"
    assert "RuntimeError: provider wobble" in stats["error"]
    assert stats["variant_label"] is None  # no fabricated verdict


def test_wiring_a_failing_reader_in_the_pass_still_returns(monkeypatch):
    """The reader/judge legs are contained too, not only retrieval."""
    class _ExplodingReader(_FakeReader):
        def answer(self, **kwargs):
            raise ValueError("reader exploded")

    stats = _recon(monkeypatch, ret=_one_shot_ret(0.2), label=False,
                   hypothesis="two", reader=_ExplodingReader())
    assert stats["skip_reason"] == "error"
    assert "ValueError: reader exploded" in stats["error"]
    assert stats["variant_label"] is None


def test_wiring_a_fatal_provider_error_still_aborts(monkeypatch):
    """Containment must NOT swallow the run-level abort classes: a dead key
    would otherwise be absorbed by a measurement-only variant while the run
    kept producing garbage (M2 / #1523)."""
    class _Fatal(Exception):
        pass

    monkeypatch.setattr(runner, "is_fatal", lambda e: True)

    def _boom(*a, **k):
        raise _Fatal("billing cap hit")

    monkeypatch.setattr(runner, "retrieve_for_question", _boom)
    collector = lme_usage.get_collector()
    collector.reset()
    lme_usage.set_question_key("q_msr_1")
    try:
        with pytest.raises(_Fatal):
            runner._reconcile_failing_question(
                sdk=object(), reader=_FakeReader(), judge=_FakeJudge(),
                question=QUESTION, qid="q_msr_1", ret=_one_shot_ret(0.2),
                label=False, hypothesis="two", ks=(5,),
                retrieval_kwargs={"retriever": "hybrid"},
                raised={"top_k": 40, "context_item_cap": 80,
                        "max_context_tokens": 16000},
                cap=24000, max_retries=0)
    finally:
        lme_usage.clear_question_key()
        collector.reset()


# ── the labeled-variant block (never the official number) ───────────────

def _variant_outcome(qid: str, one_shot: bool, variant: bool | None = None,
                     fired: bool = True, admitted: bool = True,
                     surface_before: float = 0.5,
                     surface_after: float | None = 1.0,
                     recall_before: float = 0.5,
                     recall_after: float = 0.5,
                     cap_exceeded: bool = False,
                     marginal: int = 100,
                     skip_reason: str | None = None) -> dict:
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
    if skip_reason is not None:
        stats["skip_reason"] = skip_reason
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
                         variant=None, surface_after=None,
                         skip_reason="token_cap"),
        _variant_outcome("q3", one_shot=False, variant=True,
                         cap_exceeded=True, marginal=99_000),
    ]
    rows = wr.reconciliation_variant_rows(outcomes, k=5)
    assert rows["n_fired"] == 3
    assert rows["n_admitted"] == 2
    assert rows["n_skipped_token_cap"] == 1
    assert rows["cap_exceeded_n"] == 1
    assert rows["n_errored"] == 0
    assert rows["token_cap"] == 24000
    assert rows["marginal_tokens_total"] == 100 + 99_000  # admitted only
    assert rows["reader_surface@5"]["before"] == pytest.approx(0.5)
    assert rows["reader_surface@5"]["after"] == pytest.approx(1.0)
    assert rows["reader_surface@5"]["n"] == 2


def test_variant_rows_counts_an_errored_pass_and_a_bound_overshoot():
    """A failed optional pass and a bound overshoot are both CENSUS rows —
    the containment counter and the falsifier of the bound, never silence."""
    errored = _variant_outcome("q1", one_shot=False, variant=None)
    errored["window_reconciliation_stats"]["error"] = "RuntimeError: x"
    over = _variant_outcome("q2", one_shot=False, variant=True)
    over["window_reconciliation_stats"]["bound_exceeded"] = True
    rows = wr.reconciliation_variant_rows([errored, over], k=5)
    assert rows["n_fired"] == 2
    assert rows["n_errored"] == 1
    assert rows["n_graded"] == 1     # the errored pass contributes no verdict
    assert rows["bound_exceeded_n"] == 1


def test_variant_rows_count_an_unmetered_pass_separately():
    """⛔ #11830 review P1: an unmetered pass is NOT a cheap pass. Its lower
    bound must not enter ``marginal_tokens_total``, and the census reports it
    rather than letting it read as spend that fit the cap."""
    measured = _variant_outcome("q1", one_shot=False, variant=True)
    measured["window_reconciliation_stats"]["marginal_tokens"] = 900
    unmetered = _variant_outcome("q2", one_shot=False, variant=True)
    unmetered["window_reconciliation_stats"]["marginal_tokens"] = 0
    unmetered["window_reconciliation_stats"]["marginal_unmetered"] = True
    unmetered["window_reconciliation_stats"]["cap_exceeded"] = None
    rows = wr.reconciliation_variant_rows([measured, unmetered], k=5)
    assert rows["n_unmetered"] == 1
    # The total is the MEASURED spend only — never the unknown priced at 0.
    assert rows["marginal_tokens_total"] == 900
    assert rows["cap_exceeded_n"] == 0


def test_variant_rows_do_not_call_an_errored_pass_a_cap_skip():
    """⛔ #11830 review P2: an errored pass is not the budget gate refusing.
    ``skip_reason`` already distinguishes them; the census must too."""
    errored = _variant_outcome("q1", one_shot=False, variant=None)
    errored["window_reconciliation_stats"]["error"] = "RuntimeError: x"
    errored["window_reconciliation_stats"]["skip_reason"] = "error"
    capped = _variant_outcome("q2", one_shot=False, variant=None)
    capped["window_reconciliation_stats"]["admitted"] = False
    capped["window_reconciliation_stats"]["skip_reason"] = "token_cap"
    rows = wr.reconciliation_variant_rows([errored, capped], k=5)
    assert rows["n_skipped_token_cap"] == 1
    assert rows["n_errored"] == 1


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
    assert "window_reconciliation_max_context_tokens" not in off
    on = _fingerprint(window_reconciliation=True,
                      window_reconciliation_token_cap=24000,
                      window_reconciliation_max_context_tokens=8000)
    assert on["window_reconciliation"] is True
    assert on["window_reconciliation_token_cap"] == 24000
    assert on["window_reconciliation_max_context_tokens"] == 8000


def test_fingerprint_refuses_an_arm_flip_or_a_cap_change():
    """An arm-ON checkpoint cannot be resumed with the arm OFF, and two cap
    volumes can never blend into one artifact."""
    on = _fingerprint(window_reconciliation=True,
                      window_reconciliation_token_cap=24000,
                      window_reconciliation_max_context_tokens=8000)
    assert runner._fingerprint_diffs(on, _fingerprint())
    assert runner._fingerprint_diffs(
        on, _fingerprint(window_reconciliation=True,
                         window_reconciliation_token_cap=12000,
                         window_reconciliation_max_context_tokens=8000))
    assert not runner._fingerprint_diffs(on, on)


def test_fingerprint_refuses_a_window_change_at_the_same_arm_and_cap():
    """⛔ #2570 review round 3, finding 1 — R6 was DECLARED but not covered.

    The arm's methodology comment promises an arm-ON checkpoint "can never be
    resumed under a different cap/window — the fingerprint gate". ``top_k``
    and ``context_item_cap`` were gated; the third window dimension
    (``max_context_tokens``, which the raise scales) was not, so a checkpoint
    written at 8000 could be resumed at 12000 with an identical fingerprint —
    and the resumed segment would blend two windows into one published
    variant block, or (past the cap) take ``token_cap`` on every question
    while the artifact claimed a single window.

    Same arm, same cap, DIFFERENT window ⇒ the resume must be refused.
    """
    written = _fingerprint(window_reconciliation=True,
                           window_reconciliation_token_cap=24000,
                           window_reconciliation_max_context_tokens=8000)
    resumed_bigger = _fingerprint(window_reconciliation=True,
                                 window_reconciliation_token_cap=24000,
                                 window_reconciliation_max_context_tokens=12000)
    resumed_smaller = _fingerprint(window_reconciliation=True,
                                  window_reconciliation_token_cap=24000,
                                  window_reconciliation_max_context_tokens=4000)
    assert runner._fingerprint_diffs(written, resumed_bigger)
    assert runner._fingerprint_diffs(written, resumed_smaller)


def test_variant_rows_surface_pair_excludes_an_admitted_pass_without_after():
    """⛔ #2570 review round 2, finding 1 — ONE PAIRED POPULATION.

    An ADMITTED pass can carry ``reader_surface_before`` with no
    ``reader_surface_after`` (its second reader errored). Filtering the two
    means independently averaged ``before`` over a LARGER population than
    ``after`` and then published the difference — a delta between two
    different question sets. Both means and ``n`` must come from the paired
    subset only.
    """
    errored = _variant_outcome("q1", one_shot=False, variant=None,
                               surface_before=0.2, surface_after=None)
    errored["window_reconciliation_stats"]["error"] = "RuntimeError: wobble"
    paired = _variant_outcome("q2", one_shot=False, variant=True,
                              surface_before=0.5, surface_after=1.0)
    rows = wr.reconciliation_variant_rows([errored, paired], k=5)
    assert rows["n_admitted"] == 2
    assert rows["n_errored"] == 1
    surface = rows["reader_surface@5"]
    # the errored pass contributes to NEITHER mean
    assert surface["before"] == pytest.approx(0.5)
    assert surface["after"] == pytest.approx(1.0)
    assert surface["n"] == 1
