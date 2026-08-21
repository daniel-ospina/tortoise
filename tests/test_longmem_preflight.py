"""M2 pre-flight gate tests (#1523, epic #1509): billing probe + 4xx fail-fast.

Runs fully offline — stubbed transports (the existing test_longmem_runner.py
pattern), no real keys, embedded FalkorDBLite for the run-loop tests. The
error-class taxonomy is consumed from P2's production module
(``tortoise.model_adapters`` — the M2 plan's provisional-copy hedge is
obsolete; D1: one taxonomy, no divergence).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.judge import MockJudge  # noqa: E402, RUF100
from tools.longmem_eval.preflight import (  # noqa: E402, RUF100
    PROBE_SYSTEM,
    PROBE_USER,
    FatalProviderError,
    PreflightError,
    check_judge_key,
    run_preflight,
)
from tools.longmem_eval.reader import MockReader  # noqa: E402, RUF100
from tools.longmem_eval.run import (
    _print_summary,
    run_evaluation,
    run_main,
)
from tortoise.model_adapters import (  # noqa: E402, RUF100
    FATAL_STATUS_CODES,
    LlmErrorClass,
    classify_llm_error,
    is_fatal,
    is_transient,
)

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _http_err(status: int) -> requests.exceptions.HTTPError:
    """requests-style HTTPError carrying ``response.status_code`` (the shape
    ``tortoise.model_adapters._http_status`` reads)."""
    exc = requests.exceptions.HTTPError(f"HTTP {status}")
    exc.response = type("_R", (), {"status_code": status})()
    return exc


def _set_all_keys(monkeypatch) -> None:
    """All four provider keys set (dummy) so build_reader/build_judge pass."""
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.setenv(k, "sk-test")


# ── Stub transports (recording / ok / fatal) ───────────────────────────────

class _RecordingExtractor:
    id = "deepseek/deepseek-v4-flash"
    provider = "deepseek-direct"
    key_env = "DEEPSEEK_API_KEY"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return "probe digest ok"


class _RecordingReader:
    model_id = "deepseek/deepseek-v4-flash"
    model_spec = "openrouter:deepseek/deepseek-v4-flash"
    provider = "openrouter"
    key_env = "OPENROUTER_API_KEY"

    def __init__(self):
        self.calls: list[str] = []

    def ping(self, probe: str) -> str:
        self.calls.append(probe)
        return "probe answer ok"


class _RecordingJudge:
    model_id = "gpt-4o-2024-08-06"
    model_spec = "openai:gpt-4o-2024-08-06"
    provider = "openai"
    key_env = "OPENAI_API_KEY"

    def __init__(self):
        self.calls: list[str] = []

    def ping(self, probe: str) -> str:
        self.calls.append(probe)
        return "yes"


class _OkExtractor(_RecordingExtractor):
    pass


# ── Pre-flight gate: one realistic completion per model, before the loop ──

def test_preflight_pings_each_model_once(monkeypatch):
    """run_preflight pings the extractor once (S1-shaped), the reader once
    and the judge once; every check reports status ok with the model's
    resolved identity (E2E-2 / S1/2/3 integration)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    reader, judge = _RecordingReader(), _RecordingJudge()
    extractor = _RecordingExtractor()
    result = run_preflight(reader=reader, judge=judge,
                           extractor_model=extractor)
    assert result["status"] == "ok"
    assert [c["what"] for c in result["checks"]] == [
        "judge-key", "extractor-billing-probe", "reader", "judge"]
    assert all(c["status"] == "ok" for c in result["checks"])
    # extractor: exactly ONE S1-shaped completion through its provider
    assert len(extractor.calls) == 1
    system, user = extractor.calls[0]
    assert system == PROBE_SYSTEM
    assert user == PROBE_USER
    # reader/judge: exactly ONE ping each with the probe user text
    assert reader.calls == [PROBE_USER]
    assert judge.calls == [PROBE_USER]
    by_what = {c["what"]: c for c in result["checks"]}
    assert by_what["extractor-billing-probe"]["provider"] == "deepseek-direct"
    assert by_what["extractor-billing-probe"]["model_id"] == "deepseek/deepseek-v4-flash"
    assert by_what["extractor-billing-probe"]["latency_ms"] >= 0
    assert by_what["reader"]["model_id"] == "deepseek/deepseek-v4-flash"


def test_preflight_mock_mode_skips():
    """--mock → the gate is a no-op: skipped dict, zero transport calls."""
    result = run_preflight(reader=MockReader(), judge=MockJudge(), mock=True)
    assert result["status"] == "skipped"
    assert result["mock"] is True
    assert result["checks"] == []


def test_run_main_records_preflight_block(tmp_path, capsys):
    """The pre-flight result rides report['preflight'] (additive kwarg) and
    the summary prints the gate line."""
    _, report = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        preflight={"status": "ok", "checks": []})
    assert report["preflight"]["status"] == "ok"
    _print_summary(report)
    out = capsys.readouterr().out
    assert "pre-flight gate:           ok" in out


def test_run_main_mock_records_skipped_preflight(tmp_path):
    """--mock through run_main records the skipped gate block in the report."""
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                       "--mock", "--output", str(out)])
    assert report["preflight"]["status"] == "skipped"
    assert report["preflight"]["mock"] is True


def test_skip_preflight_flag_bypasses_gate(monkeypatch, tmp_path):
    """--skip-preflight bypasses the gate (debugging only) and records the
    skipped block — run_preflight must not be called."""
    _set_all_keys(monkeypatch)
    import tools.longmem_eval.run as run_mod

    seen: dict = {}

    def _no_preflight(**kw):  # pragma: no cover — must never be called
        seen["preflight_called"] = True
        raise AssertionError("gate must not run under --skip-preflight")

    def _stub_eval(instances, **kw):
        seen["preflight_arg"] = kw.get("preflight")
        return [], {"n_questions": 0, "split": "s", "accuracy": {},
                    "retrieval": {}, "latency_ms": {}, "methodology": {},
                    "failures": [], "n_failed": 0, "outcomes": [],
                    "preflight": kw.get("preflight")}

    monkeypatch.setattr(run_mod, "run_preflight", _no_preflight)
    monkeypatch.setattr(run_mod, "run_evaluation", _stub_eval)
    monkeypatch.setattr(run_mod, "_print_summary", lambda r: None)
    run_mod.run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                      "--skip-preflight", "--output", str(tmp_path / "r.json")])
    assert "preflight_called" not in seen
    assert seen["preflight_arg"]["status"] == "skipped"
    assert seen["preflight_arg"]["reason"] == "skip-preflight"


# ── Billing probe: S1-sized, 402 distinct from per-request cap (D3) ───────

def test_billing_probe_is_s1_shaped():
    """Token budgets asserted: PROBE_SYSTEM ≤ 2000 / PROBE_USER ≤ 500 tokens
    (whitespace estimator + markup allowance) — the probe mirrors S1_TMPL's
    digest instruction at ~1/10 scale (see tortoise/extractor_v2.py)."""
    assert len(PROBE_SYSTEM.split()) <= 2000
    assert len(PROBE_USER.split()) <= 500
    assert "summariz" in PROBE_SYSTEM.lower()  # digest-shaped instruction
    assert "user:" in PROBE_USER and "assistant:" in PROBE_USER


def test_billing_probe_402_is_fatal(monkeypatch):
    """Extractor billing probe 402 → PreflightError naming the extractor
    check (a mid-run 402 after a successful probe is provably BALANCE)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _FatalExtractor(_OkExtractor):
        def complete(self, **kw):
            raise _http_err(402)

    with pytest.raises(PreflightError) as ei:
        run_preflight(reader=_RecordingReader(), judge=_RecordingJudge(),
                      extractor_model=_FatalExtractor())
    assert len(ei.value.failures) == 1
    assert ei.value.failures[0]["status"] == "fatal"
    msg = str(ei.value)
    assert "extractor-billing-probe" in msg
    assert "402" in msg


# ── 401/402/403 fatal-class abort (pre-flight) ────────────────────────────

@pytest.mark.parametrize("status", [401, 402, 403])
def test_preflight_fatal_401_402_403_abort(monkeypatch, status):
    """Each fatal status → PreflightError listing the failing check + status
    (E2E-2: the gate aborts with a clear message; no question executes)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _FatalExtractor(_OkExtractor):
        def complete(self, **kw):
            raise _http_err(status)

    with pytest.raises(PreflightError) as ei:
        run_preflight(reader=_RecordingReader(), judge=_RecordingJudge(),
                      extractor_model=_FatalExtractor())
    msg = str(ei.value)
    assert str(status) in msg
    assert ei.value.failures[0]["status"] == "fatal"


def test_run_main_preflight_failure_exits_nonzero(monkeypatch, tmp_path):
    """A failed gate → run_main exits non-zero and NO question executes."""
    _set_all_keys(monkeypatch)
    import tools.longmem_eval.run as run_mod

    def _fatal_gate(**kw):
        raise PreflightError([{
            "what": "extractor-billing-probe", "status": "fatal",
            "detail": "HTTPError: 402 Payment Required"}])

    executed = {"n": 0}

    def _must_not_run(*a, **k):  # pragma: no cover — gate must fail first
        executed["n"] += 1

    monkeypatch.setattr(run_mod, "run_preflight", _fatal_gate)
    monkeypatch.setattr(run_mod, "run_evaluation", _must_not_run)
    with pytest.raises(SystemExit) as ei:
        run_mod.run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                          "--output", str(tmp_path / "r.json")])
    assert ei.value.code == 1
    assert executed["n"] == 0  # nothing in the 500-Q loop started


# ── Judge key presence (explicit, config-only) ────────────────────────────

def test_preflight_judge_key_absent_explicit_message(monkeypatch):
    """OPENAI_API_KEY unset with the default judge spec → PreflightError whose
    message names OPENAI_API_KEY verbatim (E2E-2 negative)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(PreflightError) as ei:
        run_preflight(reader=_RecordingReader(), judge=_RecordingJudge(),
                      extractor_model=_OkExtractor())
    msg = str(ei.value)
    assert "OPENAI_API_KEY" in msg
    assert "judge key missing" in msg
    assert any(c["what"] == "judge-key" for c in ei.value.failures)


def test_preflight_judge_key_present_ok(monkeypatch):
    """Key set → the judge-key check passes (config-only, no network)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    result = run_preflight(reader=_RecordingReader(), judge=_RecordingJudge(),
                           extractor_model=_OkExtractor())
    judge_check = result["checks"][0]
    assert judge_check["what"] == "judge-key"
    assert judge_check["status"] == "ok"
    assert judge_check["key_env"] == "OPENAI_API_KEY"


def test_check_judge_key_resolves_spec_provider(monkeypatch):
    """A custom judge spec resolves its own key env var, not the default."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class _OrJudge(_RecordingJudge):
        model_spec = "openrouter:deepseek/deepseek-v4-flash"

    assert check_judge_key(_OrJudge())["status"] == "ok"
    assert check_judge_key(_OrJudge())["key_env"] == "OPENROUTER_API_KEY"
    # the default spec still demands OPENAI_API_KEY
    with pytest.raises(PreflightError):
        run_preflight(reader=_RecordingReader(), judge=_RecordingJudge(),
                      extractor_model=_OkExtractor())


# ── Mid-run fail-fast (D4): fatal aborts, transient retries/records ───────

def test_midrun_fatal_aborts_run(tmp_path):
    """A fatal-class error on question 2 → FatalProviderError propagates out
    of run_evaluation (run ABORTED), never recorded in ``failures``."""
    class _FatalOnSecond(MockReader):
        def __init__(self):
            self.n = 0

        def answer(self, **kw):
            self.n += 1
            if self.n == 2:
                raise _http_err(402)
            return super().answer(**kw)

    with pytest.raises(FatalProviderError) as ei:
        run_evaluation(_mini()[:2], reader=_FatalOnSecond(), judge=MockJudge(),
                       ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                       max_retries=2)
    msg = str(ei.value)
    assert "402" in msg
    assert "mini_msr_002" in msg  # the qid of the aborting question
    assert ei.value.where == "run-loop"


def test_midrun_transient_exhausted_still_records_failure(tmp_path):
    """Transient-exhausted (503) → recorded in ``failures`` and the run
    CONTINUES — the existing per-question isolation semantics (the
    test_single_question_failure_does_not_abort_run guard)."""
    class _TransientAlways(MockReader):
        def answer(self, **kw):
            raise _http_err(503)

    outcomes, report = run_evaluation(
        _mini()[:2], reader=_TransientAlways(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=0)
    assert outcomes == []
    assert report["n_failed"] == 2
    assert len(report["failures"]) == 2


def test_judge_transient_5xx_retried_then_ok(tmp_path, monkeypatch):
    """Verify-gate fix (judge reliability): a judge 503 is retried by
    _call_with_backoff and the question completes — never silently dropped.
    Backoff is shrunk surgically (base/cap, NOT the global time module —
    redislite's server-start wait loop depends on real time.sleep)."""
    import tools.longmem_eval.run as run_mod
    _real = run_mod._call_with_backoff

    def _fast(fn, *, what, retries, **kw):
        return _real(fn, what=what, retries=retries, base=0.01, cap=0.05)

    monkeypatch.setattr(run_mod, "_call_with_backoff", _fast)

    class _FlakyJudge(MockJudge):
        def __init__(self):
            self.n = 0

        def judge(self, **kw):
            self.n += 1
            if self.n <= 2:
                raise _http_err(503)
            return super().judge(**kw)

    outcomes, report = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=_FlakyJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=2)
    assert len(outcomes) == 2  # retried then OK — outcome present
    assert report["n_failed"] == 0
    assert report["failures"] == []


def test_judge_fatal_402_aborts(tmp_path):
    """A judge 402 mid-run → run aborts (never silently dropped, never
    retried — fatal re-raises before the backoff path)."""
    class _FatalJudge(MockJudge):
        def judge(self, **kw):
            raise _http_err(402)

    with pytest.raises(FatalProviderError) as ei:
        run_evaluation(_mini()[:2], reader=MockReader(), judge=_FatalJudge(),
                       ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                       max_retries=2)
    assert "402" in str(ei.value)


# ── Taxonomy contract (D1): pinned against the P2 production module ───────

def test_error_classify_mapping():
    """Fatal = {401,402,403}; transient = {429, 5xx}; other classes are not
    misclassified. Runs against tortoise.model_adapters (P2 #1530) — the
    single source (the provisional-copy hedge is obsolete)."""
    assert frozenset({401, 402, 403}) == FATAL_STATUS_CODES
    for status in (401, 402, 403):
        exc = _http_err(status)
        assert classify_llm_error(exc) == LlmErrorClass.FATAL
        assert is_fatal(exc) and not is_transient(exc)
    for status in (429, 500, 502, 503, 504):
        exc = _http_err(status)
        assert classify_llm_error(exc) == LlmErrorClass.TRANSIENT
        assert is_transient(exc) and not is_fatal(exc)
    # non-HTTP transport errors are transient-safe; unknown → not fatal
    import urllib.error
    conn = urllib.error.URLError("connection reset")
    assert classify_llm_error(conn) == LlmErrorClass.TRANSIENT
    assert is_transient(conn) and not is_fatal(conn)
    assert classify_llm_error(RuntimeError("boom")) == LlmErrorClass.UNKNOWN
    assert not is_fatal(RuntimeError("boom"))
