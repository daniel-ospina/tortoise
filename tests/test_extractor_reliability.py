"""Reliability tests for the v2 extractor LLM seam (M3, issue #1524).

Covers the S15 pipeline-state surfaces: ``_complete`` retries transient
classes (429/5xx/network/timeout) with exponential backoff and raises on the
final failure; fatal 4xx (401/402/403 + other 4xx) raise immediately with
ZERO retries; unknown classes are transient-safe; bounded ``max_tokens`` per
stage (S1 1500 / S2,S4 8000, TORTOISE_EXTRACTOR_MAX_TOKENS override);
truncation detection (last_finish_reason == "length"); and the per-session
error census / stats['llm'] roll-up of ``extract_session_v2`` (D3).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import extractor_v2 as v2  # noqa: E402, I001, RUF100


class _HTTPError(Exception):
    """Duck-typed requests.HTTPError (no hard requests import needed)."""

    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.response = type("R", (), {"status_code": status_code})()


class _Flaky:
    """Adapter that raises ``exc`` for the first ``fails`` calls, then
    returns ``ok``. Accepts the per-call ``max_tokens`` kwarg (M3 GATE-2)."""

    def __init__(self, exc, fails: int = 1, ok: str = "ok"):
        self.exc = exc
        self.fails = fails
        self.ok = ok
        self.calls = 0
        self.last_finish_reason = None

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return self.ok


class _Recorder:
    """Adapter that records the ``max_tokens`` kwarg per call."""

    def __init__(self):
        self.captured: list[int | None] = []
        self.last_finish_reason = None

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        self.captured.append(max_tokens)
        return "{}"  # parses as JSON for run_s2/run_s4


def _model(fn) -> _Recorder:
    """Wrap a plain callable as an adapter object (the v2 contract is
    ``model.complete(system=, user=)`` — a bare function is not a model)."""
    class _M:
        last_finish_reason = None

        def complete(self, *, system: str, user: str,
                     max_tokens: int | None = None) -> str:
            return fn(system=system, user=user, max_tokens=max_tokens)

    return _M()


# ── Task 1: retry/backoff + classification core ───────────────────────────

def test_complete_retries_transient_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    model = _Flaky(_HTTPError(429), fails=2, ok="ok")
    stats: dict = {}
    out = v2._complete(model, "sys", "user", retries=2, stats=stats)
    assert out == "ok"
    assert model.calls == 3  # 1 initial + 2 retries
    assert stats["attempts"] == 3
    assert stats["retries"] == 2
    assert stats["last_class"] is None
    assert stats["truncated"] is False


@pytest.mark.parametrize("exc", [
    _HTTPError(500), _HTTPError(502), _HTTPError(503),
    ConnectionError("connection reset"),
    TimeoutError("deadline"),
])
def test_complete_retries_5xx_network_timeout(monkeypatch, exc):
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    model = _Flaky(exc, fails=1, ok="ok")
    out = v2._complete(model, "s", "u", retries=2)
    assert out == "ok"
    assert model.calls == 2


@pytest.mark.parametrize("status", [401, 402, 403, 400, 422])
def test_complete_no_retry_on_fatal_4xx(status):
    """Fatal 4xx (auth/billing/forbidden/config-shape) → immediate raise,
    exactly ONE adapter call, no backoff sleep (fatal raises before the
    sleep — no time.sleep monkeypatch needed)."""
    model = _Flaky(_HTTPError(status), fails=99)
    with pytest.raises(_HTTPError):
        v2._complete(model, "s", "u", retries=2)
    assert model.calls == 1


def test_complete_unknown_class_retries(monkeypatch):
    """Unknown class = transient-safe (mirrors the harness blanket-retry
    precedent); the census records it as transient_unknown."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    model = _Flaky(RuntimeError("weird"), fails=1, ok="ok")
    assert v2._complete(model, "s", "u", retries=1) == "ok"
    assert model.calls == 2

    model2 = _Flaky(RuntimeError("weird"), fails=99)
    with pytest.raises(RuntimeError):
        v2._complete(model2, "s", "u", retries=1)
    assert model2.calls == 2  # exhausted — final exception propagates


def test_classify_error_matrix():
    """The D3 census vocabulary — stable keys the report + triage read."""
    assert v2._classify_error(_HTTPError(401)) == "fatal_401_auth"
    assert v2._classify_error(_HTTPError(402)) == "fatal_402_billing"
    assert v2._classify_error(_HTTPError(403)) == "fatal_403_forbidden"
    assert v2._classify_error(_HTTPError(400)) == "fatal_4xx"
    assert v2._classify_error(_HTTPError(422)) == "fatal_4xx"
    assert v2._classify_error(_HTTPError(429)) == "transient_429_rate_limit"
    assert v2._classify_error(_HTTPError(500)) == "transient_5xx"
    assert v2._classify_error(_HTTPError(503)) == "transient_5xx"
    assert v2._classify_error(TimeoutError()) == "transient_timeout"
    assert v2._classify_error(ConnectionError()) == "transient_network"
    assert v2._classify_error(RuntimeError()) == "transient_unknown"


def test_complete_stats_recorded_on_failure(monkeypatch):
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    model = _Flaky(_HTTPError(429), fails=99)
    stats: dict = {}
    with pytest.raises(_HTTPError):
        v2._complete(model, "s", "u", retries=1, stats=stats)
    assert stats["attempts"] == 2
    assert stats["retries"] == 1
    assert stats["last_class"] == "transient_429_rate_limit"


def test_complete_deadline_aborts_attempt():
    """Each attempt has its own deadline (D1) — a wedged call raises
    TimeoutError (transient) and is retried; with retries=0 it propagates."""
    class _Slow:
        def complete(self, *, system, user, max_tokens=None):
            time.sleep(1.0)
            return "late"

    with pytest.raises(TimeoutError):
        v2._complete(_Slow(), "s", "u", deadline_s=0.05, retries=0)


# ── Task 2: bounded max_tokens + truncation detection ─────────────────────

def test_complete_passes_stage_cap(monkeypatch):
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)
    m = _Recorder()
    v2.run_s1(m, "transcript")
    v2.run_s2(m, "story")
    v2.run_s4(m, "story", {}, {})
    assert m.captured == [1500, 8000, 8000]


def test_complete_env_override(monkeypatch):
    monkeypatch.setenv("TORTOISE_EXTRACTOR_MAX_TOKENS", "12000")
    m = _Recorder()
    v2.run_s1(m, "transcript")
    v2.run_s2(m, "story")
    v2.run_s4(m, "story", {}, {})
    assert m.captured == [12000, 12000, 12000]


def test_complete_truncation_detected():
    class _Trunc(_Recorder):
        def __init__(self):
            super().__init__()
            self.last_finish_reason = "length"

    stats: dict = {}
    v2._complete(_Trunc(), "s", "u", max_tokens=1500, stats=stats)
    assert stats["truncated"] is True

    stats2: dict = {}
    v2._complete(_Recorder(), "s", "u", max_tokens=1500, stats=stats2)
    assert stats2["truncated"] is False


def test_complete_zero_is_uncapped():
    """max_tokens=0 = documented uncapped escape hatch — no kwarg passed."""
    m = _Recorder()
    v2._complete(m, "s", "u", max_tokens=0)
    assert m.captured == [None]


def test_unbounded_adapter_warns_and_records(monkeypatch):
    """GATE-2 degraded path: an adapter without the kwarg or a writable
    max_tokens attr → loud warning + stats['unbounded_adapter'] (fail-open
    with visibility, never a silent no-op)."""
    monkeypatch.delenv("TORTOISE_EXTRACTOR_MAX_TOKENS", raising=False)

    class _Legacy:
        def complete(self, *, system: str, user: str) -> str:
            return "x"

    stats: dict = {}
    with pytest.warns(UserWarning, match="UNBOUNDED"):
        v2._complete(_Legacy(), "s", "u", max_tokens=1500, stats=stats)
    assert stats["unbounded_adapter"] is True


# ── Task 3: extract_session_v2 census / stats plumbing ────────────────────

def _conv() -> list[dict]:
    return [{"role": "user", "content": "we decided X"},
            {"role": "assistant", "content": "the plan is durable"}]


def test_extract_census_aggregates_stage_failures(monkeypatch):
    """S2+S4 both exhaust retries on 429 → census counts each stage failure;
    the pipeline completes (errors recorded, never raised)."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    def model_complete(*, system, user, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            return "We believed X. The session revealed Y."
        raise _HTTPError(429)

    out = v2.extract_session_v2(_model(model_complete), _conv())
    assert out["error_census"]["transient_429_rate_limit"] == 2  # S2 + S4
    assert any("S2 failed" in e for e in out["errors"])
    assert any("S4 failed" in e for e in out["errors"])
    llm = out["stats"]["llm"]
    assert llm["calls"] >= 7    # S1(1) + S2(3) + S4(3)
    assert llm["retries"] >= 4  # S2 2 + S4 2 exhausted retries
    assert "payload" in out


def test_extract_census_parse_error(monkeypatch):
    """Unparseable S2/S4 JSON → census parse_error (not a transient class);
    S2/S4 outputs are not used."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    def model_complete(*, system, user, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        return "this is not JSON at all"

    out = v2.extract_session_v2(_model(model_complete), _conv())
    assert out["error_census"]["parse_error"] == 2  # S2 + S4
    assert any("S2 failed" in e for e in out["errors"])
    assert out["embed_list"] == {}


def test_extract_stats_llm_rollup(monkeypatch):
    """A recovered transient (S1 first attempt 429 → retried) is NOT a census
    entry but IS rolled into stats['llm']['retries']."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    calls = {"n": 0}

    def model_complete(*, system, user, max_tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _HTTPError(429)  # S1 first attempt
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        return '{"points": [], "entities": [], "events": [], "operators": []}'

    out = v2.extract_session_v2(_model(model_complete), _conv())
    llm = out["stats"]["llm"]
    assert llm["calls"] == 4   # S1(2) + S2(1) + S4(1)
    assert llm["retries"] == 1  # only S1's recovered retry
    assert llm["truncated"] == 0
    assert out["error_census"] == {}  # fully recovered → no census entries


def test_telemetry_retry_count_wired(monkeypatch):
    """The payload telemetry's hardcoded retry_count is wired to the real
    per-session retry count (was always 0)."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    calls = {"n": 0}

    def model_complete(*, system, user, max_tokens=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _HTTPError(429)
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        return '{"points": [], "entities": [], "events": [], "operators": []}'

    out = v2.extract_session_v2(_model(model_complete), _conv())
    assert out["payload"]["telemetry"]["retry_count"] == 1


def test_extract_census_empty_on_clean_run(monkeypatch):
    """A fully healthy session → empty census + zero llm retries/truncations."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    def model_complete(*, system, user, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        return '{"points": [], "entities": [], "events": [], "operators": []}'

    out = v2.extract_session_v2(_model(model_complete), _conv())
    assert out["error_census"] == {}
    assert out["stats"]["llm"]["retries"] == 0
    assert out["stats"]["llm"]["truncated"] == 0
    assert out["stats"]["llm"]["calls"] == 3  # S1 + S2 + S4
