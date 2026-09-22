"""Report usage/cost block tests (#2185 Task 6 — report.py / run.py).

Pins the conditional report contract (A7 / Am 21):

- the outcome projection emits ``llm_usage`` ONLY when the raw outcome
  carries it (conditional rerank_pass pattern — never a null key);
- ``report["usage"]`` appears iff ≥1 outcome has usage OR overhead rows
  exist — EXACTLY ONE new top-level key when emitting, NONE when not (the
  258-pin 16-key set is asserted unchanged for usage-free reports);
- block shape: ``{per_question: {qid: ...}, totals, overhead (when rows),
  cost, coverage, priced, pricing: {map_version, git_sha, verified_on}}``;
- coverage marker when 0 < n_with_usage < n_evidence_bearing;
- unpriced lane → ``priced: false`` in the block (loud, never silent);
- pricing does NOT mutate outcome ``llm_usage`` (deep-copy equality).
"""
from __future__ import annotations

import copy

import pytest

from tools.longmem_eval import usage as lme_usage
from tools.longmem_eval.run import outcomes_to_report


def _trusted_audit() -> dict:
    from tools.longmem_eval.dataset_audit import audit_dataset
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


def _env(*, reader_tokens=(1000, 200), judge_tokens=(500, 50),
         qid="q-usage-1", provider="openrouter",
         model="deepseek/deepseek-v4-flash"):
    """A realistic per-question envelope via the collector (mirrors the run
    drain path — rows bucket under the qid key)."""
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    lme_usage.set_question_key(qid)
    c.record(stage="reader", provider="openrouter", model_id=model,
             usage={"prompt_tokens": reader_tokens[0],
                    "completion_tokens": reader_tokens[1]},
             usage_present=True)
    c.record(stage="judge", provider="openai", model_id="gpt-4o-2024-08-06",
             usage={"prompt_tokens": judge_tokens[0],
                    "completion_tokens": judge_tokens[1]},
             usage_present=True)
    env = c.drain_question(qid)
    lme_usage.clear_question_key()
    lme_usage.reset_collector()
    assert env is not None
    return env


def _outcome(qid: str, *, label: bool = True, evidence_written: int = 2,
             llm_usage: dict | None = None) -> dict:
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": label,
        "hypothesis": "hypothesis",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "chunk_evidence_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "n_ingest_errors": 0,
        "ingest_error_text": None,
        "llm_calls": 3, "llm_retries": 0, "llm_truncated": 0,
        "recovery": {"sanitize": 0, "repair": 0},
        "context_tokens": 120, "context_point_count": 3,
        "retrieval_latency_ms": 11.0, "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0, "total_ms": 66.0,
        "valid": True, "error_classes": {},
        "leg_mix": {"tfidf": 3},
        "leg_mix@k": {"5": {"tfidf": 3}, "10": {"tfidf": 3},
                       "20": {"tfidf": 3}},
        "pool_size": 10,
        "evidence_written": evidence_written,
        "evidence_retrieved@k": {"5": 1, "10": 2, "20": 2},
        "ingest_latency_ms": 12.5,
        **({"llm_usage": llm_usage} if llm_usage is not None else {}),
    }


def _report(outcomes, *, usage_overhead=None):
    return outcomes_to_report(
        outcomes,
        reader_model="golden-reader",
        judge_model="golden-judge",
        ks=(5, 10, 20),
        top_k=20,
        split="s",
        r1_knobs={"chunk_turns": 2, "context_token_cap": 8000,
                  "max_chunks_per_session": 2},
        dataset_semantics_audit=_trusted_audit(),
        integrity_threshold=0.0,
        python_version="3.12.0",
        workers=1,
        dataset_fingerprint="deadbeefcafe1234",
        usage_overhead=usage_overhead,
    )


def _base_keys():
    return {
        "benchmark", "dataset", "split", "n_questions", "n_excluded",
        "accuracy", "retrieval", "latency_ms", "methodology", "failures",
        "n_failed", "outcomes", "integrity", "extraction_health",
        "leg_mix", "pool_size",
        "evidence",
    }


# ── conditional emission (A7 / Am 21) ──────────────────────────────────────

def test_usage_free_report_has_no_new_keys():
    report = _report([_outcome("q-plain")])
    assert set(report) == _base_keys()  # the 258-pin 16-key set unchanged
    for row in report["outcomes"]:
        assert "llm_usage" not in row


def test_usage_report_gains_exactly_one_top_level_key():
    env = _env()
    report = _report([_outcome("q-usage-1", llm_usage=env)])
    assert set(report) == _base_keys() | {"usage"}


def test_overhead_rows_alone_trigger_emission():
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    c.record(stage="preflight", provider="openrouter",
             model_id="deepseek/deepseek-v4-flash",
             usage={"prompt_tokens": 100, "completion_tokens": 0},
             usage_present=True)
    oh = c.drain_overhead()
    lme_usage.reset_collector()
    report = _report([_outcome("q-plain")], usage_overhead=oh)
    assert set(report) == _base_keys() | {"usage"}


# ── block content ───────────────────────────────────────────────────────────

def test_block_per_question_totals_and_cost():
    env = _env(reader_tokens=(1_000_000, 0), judge_tokens=(0, 100_000))
    report = _report([_outcome("q-usage-1", llm_usage=env)])
    block = report["usage"]
    # reader 1.0M × $0.14/1M + judge 0.1M × $10/1M = 0.14 + 1.00
    assert block["per_question"]["q-usage-1"]["prompt_tokens"] == 1_000_000
    assert block["per_question"]["q-usage-1"]["completion_tokens"] == 100_000
    assert block["per_question"]["q-usage-1"]["cost_usd"] == pytest.approx(1.14)
    assert block["totals"]["prompt_tokens"] == 1_000_000
    assert block["totals"]["completion_tokens"] == 100_000
    assert block["totals"]["calls"] == 2
    assert block["cost"]["usd"] == pytest.approx(1.14)
    assert block["priced"] is True
    assert block["pricing"]["map_version"]
    assert block["pricing"]["git_sha"]
    assert block["pricing"]["verified_on"]
    assert "overhead" not in block  # no overhead rows → no overhead key


def test_block_overhead_section_separate_from_per_question():
    env = _env(reader_tokens=(1000, 200))
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    c.record(stage="preflight", provider="openrouter",
             model_id="deepseek/deepseek-v4-flash",
             usage={"prompt_tokens": 500, "completion_tokens": 100},
             usage_present=True)
    oh = c.drain_overhead()
    lme_usage.reset_collector()
    report = _report([_outcome("q-usage-1", llm_usage=env)],
                     usage_overhead=oh)
    block = report["usage"]
    # per-question totals EXCLUDE overhead; overhead has its own section
    assert block["per_question"]["q-usage-1"]["prompt_tokens"] == 1500
    assert block["totals"]["prompt_tokens"] == 1500 + 500
    assert block["overhead"]["prompt_tokens"] == 500
    assert block["overhead"]["cost_usd"] == pytest.approx(round(
        (500 * 0.14 + 100 * 0.28) / 1e6, 6))
    # overhead lanes are visible (the preflight lane row)
    assert block["overhead"]["lanes"][0]["stage"] == "preflight"


def test_block_overhead_covers_breaker_and_failed_spend():
    """Breaker-open + failed-question spend lands in the overhead section
    (never the evidence-bearing per-question numerator) — live-moved into
    the collector and drained as overhead before the report builds."""
    env = _env(qid="q-evidence")
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    lme_usage.set_question_key("q-breaker")
    c.record(stage="ingest", provider="openrouter",
             model_id="deepseek/deepseek-v4-flash",
             usage={"prompt_tokens": 2000, "completion_tokens": 500},
             usage_present=True)
    lme_usage.clear_question_key()
    c.record(stage="preflight", provider="openrouter",
             model_id="deepseek/deepseek-v4-flash",
             usage={"prompt_tokens": 100, "completion_tokens": 0},
             usage_present=True)
    c.drain_to_overhead("q-breaker")  # breaker/failure move
    oh = c.drain_overhead()
    lme_usage.reset_collector()
    report = _report([_outcome("q-evidence", llm_usage=env)],
                     usage_overhead=oh)
    block = report["usage"]
    assert set(block["per_question"]) == {"q-evidence"}
    assert block["overhead"]["prompt_tokens"] == 2100
    assert block["totals"]["prompt_tokens"] == 2100 + 1500  # ev: 1000+500


def test_projection_emits_llm_usage_conditionally():
    env = _env()
    report = _report([_outcome("q-usage-1", llm_usage=env),
                      _outcome("q-plain")])
    by_qid = {o["question_id"]: o for o in report["outcomes"]}
    assert by_qid["q-usage-1"]["llm_usage"] == env
    assert "llm_usage" not in by_qid["q-plain"]


# ── coverage / data-point cost ──────────────────────────────────────────────

def test_coverage_marker_when_mixed():
    env = _env()
    report = _report([_outcome("q-with", llm_usage=env),
                      _outcome("q-without", evidence_written=3)])
    block = report["usage"]
    assert block["coverage"] == {
        "evidence_bearing": 2, "with_usage": 1, "partial": True}
    # per-point cost computed over the covered (with-usage) subset only
    dp = block["cost"]["data_point_usd"]
    assert dp is not None and dp > 0
    # Σ over covered subset (evidence_written=2) — dp = cost/2
    assert dp == pytest.approx(block["per_question"]["q-with"]["cost_usd"] / 2)


def test_coverage_full_no_marker():
    env = _env()
    report = _report([_outcome("q-a", llm_usage=env),
                      _outcome("q-b", llm_usage=_env(
                          reader_tokens=(100, 20), qid="q-b"))])
    block = report["usage"]
    assert block["coverage"]["with_usage"] == 2
    assert block["coverage"].get("partial") is None


# ── unpriced lanes + non-mutation ───────────────────────────────────────────

def test_unpriced_lane_flips_block_priced_false():
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    lme_usage.set_question_key("q-weird")
    c.record(stage="reader", provider="openrouter",
             model_id="totally/unknown-model-x",
             usage={"prompt_tokens": 1000, "completion_tokens": 100},
             usage_present=True)
    env = c.drain_question("q-weird")
    lme_usage.clear_question_key()
    lme_usage.reset_collector()
    report = _report([_outcome("q-weird", llm_usage=env)])
    block = report["usage"]
    assert block["priced"] is False
    assert block["per_question"]["q-weird"]["priced"] is False


def test_pricing_does_not_mutate_outcome_llm_usage():
    env = _env()
    outcomes = [_outcome("q-usage-1", llm_usage=env)]
    before = copy.deepcopy(outcomes)
    _report(outcomes)
    assert outcomes[0]["llm_usage"] == before[0]["llm_usage"]


# ── round-2 code-review regressions (#2250) ─────────────────────────────────

def test_per_question_estimated_is_stable_boolean():
    """Guidance review P3: per-question ``estimated`` must be a stable bool
    (the envelope breakdown's ``estimated`` is a LIST of estimated lanes),
    matching the README schema."""
    lme_usage.reset_collector()
    c = lme_usage.get_collector()
    lme_usage.set_question_key("q-est")
    # deepseek-chat on openrouter is an estimated alias lane
    c.record(stage="reader", provider="openrouter",
             model_id="deepseek/deepseek-chat",
             usage={"prompt_tokens": 1000, "completion_tokens": 100},
             usage_present=True)
    env = c.drain_question("q-est")
    lme_usage.clear_question_key()
    lme_usage.reset_collector()
    block = _report([_outcome("q-est", llm_usage=env)])["usage"]
    est = block["per_question"]["q-est"]["estimated"]
    assert isinstance(est, bool) and est is True


def test_llm_calls_never_below_usage_rows():
    """Guidance review P4 (plan Task-7 acceptance): the per-question usage
    rows (metered responses) can never EXCEED the attempt counter
    llm_calls — a drift check pinning the two counters' semantics."""
    env = _env(reader_tokens=(1000, 200), judge_tokens=(500, 50))
    # 2 lanes fire 2 sink rows; the run reports 3 attempts
    usage_rows = sum(
        bucket.get("calls", 0)
        for stage in (env.get("by_stage") or {}).values()
        for prov in stage.values()
        for bucket in prov.values())
    assert usage_rows == 2
    assert usage_rows <= _outcome("q", llm_usage=env)["llm_calls"]
    # a ROWLESS outcome (mock run) carries no llm_usage and 0 calls
    outcome = _outcome("q-mock")
    assert "llm_usage" not in outcome
    assert outcome["llm_calls"] == 3  # attempt counter stays (pre-#2185)
