"""#2185 Task 7 — end-to-end usage sweep (run_evaluation integration).

Covers the FULL wiring that the unit layers cannot: the per-question task
body's question-key → sink fire → outcome drain → checkpoint persistence →
end-of-run overhead drain → report usage block, through run_evaluation with
sink-equivalent stubbed reader/judge models (the transport seam itself is
unit-tested in test_longmem_usage_seam.py; here the models emit the same
payload a bound usage_sink would).

Cases:
* all-questions-succeed under workers=2 — per-question envelopes land on
  the outcomes AND the projected report rows; block totals = Σ envelopes;
  no overhead section (nothing fell off a question);
* a terminally-failed question — reader+judge spend drains to overhead, the
  failure entry persists its usage replica (kill-9-safe), and the report's
  overhead section shows exactly that spend while per_question holds only
  the completed questions;
* A4 kill-9 read-back: a crafted checkpoint whose failure entry carries a
  usage replica (payload missing / partial) folds shortfall-only into the
  collector overhead on load — the resumed report would show the spend.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.longmem_eval import usage as lme_usage
from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"

# One sink-equivalent "call" worth of usage per stage (the reader/judge
# models below emit this exact payload per invocation, as a bound usage_sink
# would after a transport response).
READER_TOKENS = {"prompt_tokens": 500, "completion_tokens": 100}
JUDGE_TOKENS = {"prompt_tokens": 100, "completion_tokens": 20}


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _reset():
    lme_usage.reset_collector()
    lme_usage.get_collector().reset()


def _require_embedder():
    """The deterministic mock pipeline still runs write-time embeddings —
    skip where the embedder is unavailable (CI without the HF cache)."""
    from tortoise.embeddings import EmbeddingModel
    m = EmbeddingModel.get(load_timeout=120)
    if m is None:
        pytest.skip("sentence-transformers / all-MiniLM-L6-v2 cache "
                    "not available — usage E2E skipped")


class SinkReader(MockReader):
    """MockReader whose answer path also fires one reader-lane usage row
    (the payload a bound usage_sink emits from a real transport response)."""

    def answer(self, **kw) -> str:
        lme_usage.get_collector().record(
            stage="reader", provider="openrouter",
            model_id="deepseek/deepseek-v4-flash",
            usage=dict(READER_TOKENS), usage_present=True)
        return super().answer(**kw)


class SinkJudge(MockJudge):
    """MockJudge whose judge path fires one judge-lane usage row (the
    payload a bound usage_sink emits from a real transport response)."""

    def judge(self, *, question_type: str, question: str, answer: str,
              hypothesis: str, abstention: bool) -> bool:
        lme_usage.get_collector().record(
            stage="judge", provider="openai", model_id="gpt-4o-2024-08-06",
            usage=dict(JUDGE_TOKENS), usage_present=True)
        return super().judge(
            question_type=question_type, question=question, answer=answer,
            hypothesis=hypothesis, abstention=abstention)


def _run(instances, *, reader, judge, tmp_path, checkpoint=None,
         workers=1, max_retries=2):
    return runner.run_evaluation(
        instances, reader=reader, judge=judge, ks=(5, 10, 20), top_k=20,
        split="s", work_dir=str(tmp_path), checkpoint=checkpoint,
        workers=workers, max_retries=max_retries)


# ── success path (workers=2) ────────────────────────────────────────────────

def test_e2e_all_success_usage_on_outcomes_and_report(tmp_path):
    """Every question's reader+judge spend lands on its outcome envelope,
    the projected report rows carry it, block totals == Σ envelopes, and NO
    overhead section exists (nothing was left off a question). Runs with the
    default worker pool (the embedded per-question SDK path is single-
    worker in this env — the threaded no-leak attribution lives at the
    collector unit layer, test_threaded_fires_single_drain_no_lost_rows)."""
    _require_embedder()
    instances = _mini()
    assert len(instances) == 5
    _reset()
    outcomes, report = _run(
        instances, reader=SinkReader(), judge=SinkJudge(),
        tmp_path=tmp_path)
    assert len(outcomes) == 5
    assert report["n_failed"] == 0

    # every outcome carries its own envelope with the expected lane sums
    for o in outcomes:
        env = o.get("llm_usage")
        assert isinstance(env, dict), o["question_id"]
        total = env["total"]
        assert total["prompt_tokens"] == 500 + 100
        assert total["completion_tokens"] == 100 + 20
        assert total["calls"] == 2
        by_stage = env["by_stage"]
        assert by_stage["reader"]["openrouter"]["deepseek/deepseek-v4-flash"][
            "prompt_tokens"] == 500
        assert by_stage["judge"]["openai"]["gpt-4o-2024-08-06"][
            "prompt_tokens"] == 100

    # report usage block: per-question for all 5, totals == Σ envelopes
    block = report["usage"]
    assert set(block["per_question"]) == {o["question_id"] for o in outcomes}
    assert block["totals"]["prompt_tokens"] == 5 * 600
    assert block["totals"]["completion_tokens"] == 5 * 120
    assert block["totals"]["calls"] == 10
    assert block["cost"]["usd"] > 0
    assert block["priced"] is True
    assert "overhead" not in block
    assert report["methodology"]["usage_pricing"]["map_version"]
    assert block["pricing"]["git_sha"]

    # projected Layer-1 rows carry the envelope (conditional projection)
    for row in report["outcomes"]:
        assert row["question_id"] in block["per_question"]
        assert row["llm_usage"]["total"]["prompt_tokens"] == 600

    # usage-free control: the SAME run with plain mocks emits no usage keys
    _reset()
    outcomes2, report2 = _run(
        instances, reader=MockReader(), judge=MockJudge(),
        tmp_path=tmp_path)
    assert len(outcomes2) == 5
    assert "usage" not in report2
    assert "usage_pricing" not in report2["methodology"]
    for o in report2["outcomes"]:
        assert "llm_usage" not in o


# ── failed-question path ─────────────────────────────────────────────────────


def _fail_qid(_instances):
    """A judge that records its lane spend (the transport response arrived)
    then raises a transient transport error — every judge call on the
    single-instance slice fails after metering (exactly the sequence the
    seam fires on a transport-successful-then-classified-failure attempt)."""

    class BoomJudge(SinkJudge):
        def judge(self, *, question_type, question, answer, hypothesis,
                  abstention) -> bool:
            lme_usage.get_collector().record(
                stage="judge", provider="openai",
                model_id="gpt-4o-2024-08-06",
                usage=dict(JUDGE_TOKENS), usage_present=True)
            import requests
            raise requests.exceptions.Timeout("judge transient")

    return BoomJudge()


def test_e2e_failed_question_spend_reports_as_overhead(tmp_path):
    """A terminally-failed question's reader+judge spend drains to overhead:
    the failure entry persists its usage replica (kill-9-safe), and the
    report's overhead section shows exactly that spend while per_question
    holds only the completed questions."""
    _require_embedder()
    instances = _mini()[:1]
    _reset()
    cp = tmp_path / "cp.json"
    boom = _fail_qid(instances)
    outcomes, report = _run(
        instances, reader=SinkReader(), judge=boom, tmp_path=tmp_path,
        checkpoint=str(cp), max_retries=0)
    assert outcomes == []
    assert report["n_failed"] == 1

    block = report["usage"]
    # failed qid's spend = 1 reader call + 1 judge call (max_retries=0)
    assert block["overhead"]["prompt_tokens"] == 500 + 100
    assert block["overhead"]["completion_tokens"] == 100 + 20
    assert block["overhead"]["calls"] == 2
    assert set(block["per_question"]) == set()  # no completed outcome
    assert {l["stage"] for l in block["overhead"]["lanes"]} == {
        "reader", "judge"}

    # kill-9 safety: the checkpoint failure entry carries the usage replica
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["failures"]) == 1
    rep = saved["failures"][0].get("usage")
    assert isinstance(rep, dict)
    assert rep["total"]["prompt_tokens"] == 600


# ── A4 kill-9 read-back (load-time fold) ────────────────────────────────────

def test_load_fold_full_and_shortfall(tmp_path):
    """A crafted checkpoint whose failure entry carries a usage replica folds
    into the collector overhead at load (fold_usage=True): FULL fold when the
    payload is absent (kill -9 between the failure upsert and the trailing
    save), SHORTFALL-ONLY when the payload holds a partial amount (never the
    overlap — idempotent on repeated resume)."""
    by_stage = {
        "reader": {"openrouter": {"deepseek/deepseek-v4-flash": {
            "prompt_tokens": 500, "completion_tokens": 100,
            "calls": 1, "usage_present": True}}}}

    def _cp(replica, payload=None):
        data = {
            "format": runner.CHECKPOINT_FORMAT,
            "run_key": "embedded__hybrid__default__default",
            "surface": "embedded", "retriever": "hybrid",
            "model": "default", "prompt": "default",
            "fingerprint": "stale-not-checked-without-fp",
            "outcomes": [],
            "failures": [{
                "question_id": "q-fail", "question_type": "single-session-user",
                "error": "judge:TimeoutError('x')",
                "error_class": "judge:retries_exhausted", "retryable": True,
                "attempts": 1, "failed_at_utc": "2026-09-03T00:00:00+00:00",
                "in_progress": None,
                "usage": {"by_stage": by_stage, "total": {
                    "prompt_tokens": 500, "completion_tokens": 100,
                    "calls": 1}},
            }],
            "updated_at_utc": "2026-09-03T00:00:00+00:00",
        }
        if payload is not None:
            data["usage_overhead"] = payload
        p = tmp_path / "cp.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    # full fold: payload absent → the whole replica lands in overhead
    _reset()
    runner._load_checkpoint(str(_cp(None)), fold_usage=True)
    oh = lme_usage.get_collector().drain_overhead()
    assert oh["total"]["prompt_tokens"] == 500
    assert oh["total"]["completion_tokens"] == 100
    _reset()

    # shortfall-only fold: payload holds 300 prompt / 0 completion for the
    # same lane → the fold adds exactly the missing 200/100 (never 800)
    _reset()
    partial = {"q-fail": {"reader": {"openrouter": {
        "deepseek/deepseek-v4-flash": {
            "prompt_tokens": 300, "completion_tokens": 0,
            "calls": 1, "usage_present": True}}}}}
    runner._load_checkpoint(str(_cp(None, payload=partial)), fold_usage=True)
    oh = lme_usage.get_collector().drain_overhead()
    assert oh["total"]["prompt_tokens"] == 500  # 300 payload + 200 shortfall
    assert oh["total"]["completion_tokens"] == 100
    _reset()
