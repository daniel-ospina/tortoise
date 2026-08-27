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


def _fresh_sdk(tmp_path):
    """Embedded FalkorDBLite SDK for the ingest roll-up integration test
    (no docker dependency — the harness's S3 is monkeypatched non-degraded)."""
    from tortoise.sdk import TortoiseSDK
    return TortoiseSDK(str(tmp_path / "lme.db"))


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
    S2/S4 outputs are not used. #1746 (D2): with finish_reason None/"stop"
    the class stays ``parse_error`` — truncation is NOT assumed."""
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


def test_extract_census_truncated_parse_error(monkeypatch):
    """#1746 (D2): S2/S4 final parse failure with a TRUNCATED first attempt
    (finish_reason == "length") → census ``truncated_parse_error``, NOT the
    plain ``parse_error`` class; the stage's truncated flag reaches the
    session llm roll-up (the D7 warning-only readout's source)."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    class _TruncGarbage:
        last_finish_reason = None

        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            self.last_finish_reason = "length"
            return "this is not JSON at all"

    out = v2.extract_session_v2(_TruncGarbage(), _conv())
    assert out["error_census"]["truncated_parse_error"] == 2  # S2 + S4
    assert "parse_error" not in out["error_census"]
    assert out["stats"]["llm"]["truncated"] == 2


def test_census_equality_mixed_errors(monkeypatch):
    """D1 invariant (#1746, criterion 2): every ``errors.append`` pairs
    exactly one census class — ``len(errors) == sum(error_census.values())``
    holds even on a mixed-error session, and the four previously-uncensused
    deterministic classes (s1_chunk_summary / empty_embed_list /
    entity_resolution_failed / s5_failed) are all present."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    # ── scenario 1: S1 chunk failure (fatal, one attempt) + S2 parse
    # failure + S4 truncation → complete_list empty → the empty-embed-list
    # class. 51 turns → 2 chunks: chunk 1 fails, chunk 2 succeeds so the
    # story is non-empty and S2/S4 actually run. ──
    big_conv = [{"role": "user", "content": f"turn {i}"}
                for i in range(51)]

    class _S1Boom:
        last_finish_reason = None

        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            if self.calls == 1:  # chunk 1's S1 — fatal 4xx, no retry
                raise _HTTPError(400)
            if "STORY SUMMARIZER" in system:
                return "A narrative for chunk 2."
            return "not json"

    out1 = v2.extract_session_v2(_S1Boom(), big_conv)
    assert len(out1["errors"]) == sum(out1["error_census"].values())
    assert out1["error_census"]["s1_chunk_summary"] == 1
    assert out1["error_census"]["empty_embed_list"] == 1
    assert out1["error_census"]["parse_error"] == 2
    assert any("S1 chunks failed" in e for e in out1["errors"])

    # ── scenario 2: S2 + S4 succeed → entity resolution + S5 both raise
    # (the previously-uncensused resolution/S5 classes). ──
    good = ('{"entities": [{"name": "gym", "kind": "core:plan", '
            '"lifecycle": "created", "supersedes": null, "note": null}], '
            '"events": [], "operators": [], '
            '"points": [{"content": "gym at 6pm", '
            '"pointKind": "statement"}]}')

    class _S2S4Good:
        last_finish_reason = "stop"

        def complete(self, *, system, user, max_tokens=None):
            if "STORY SUMMARIZER" in system:
                return "A narrative."
            return good

    monkeypatch.setattr(v2, "search_graph", lambda *a, **k: {
        "mode": "real", "degraded": False, "reason": None,
        "entities": [{"id": "e1", "name": "gym", "kind": "core:plan"}],
        "points": [], "events": [], "queries_run": 1})

    def _boom_resolve(*a, **k):
        raise RuntimeError("resolution boom")

    def _boom_embed(*a, **k):
        raise RuntimeError("embed boom")

    monkeypatch.setattr(v2, "resolve_entities", _boom_resolve)
    monkeypatch.setattr(v2, "execute_embed", _boom_embed)
    out2 = v2.extract_session_v2(_S2S4Good(), _conv())
    assert len(out2["errors"]) == sum(out2["error_census"].values())
    assert out2["error_census"]["entity_resolution_failed"] == 1
    assert out2["error_census"]["s5_failed"] == 1
    assert any("entity resolution failed" in e for e in out2["errors"])
    assert any("S5 failed" in e for e in out2["errors"])

    # the four deterministic classes all present across the two sessions
    all_classes = set(out1["error_census"]) | set(out2["error_census"])
    for cls in ("s1_chunk_summary", "empty_embed_list",
                "entity_resolution_failed", "s5_failed"):
        assert cls in all_classes


def test_llm_truncated_warning_only_not_error(monkeypatch):
    """#1746 (D7): a truncated S2/S4 output that still parses cleanly (the
    canonical tail-cut tolerance) is NOT an error class — errors stay empty
    — but the truncation IS recorded in ``llm_stats["truncated"]``: the
    warning-only readout (criterion 3's structural guard: no UNRECORDED
    truncation with valid=true)."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    class _TruncatedClean:
        last_finish_reason = None

        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            self.last_finish_reason = "length"
            return '{"points": [], "entities": [], "events": [], ' \
                   '"operators": []}'

    out = v2.extract_session_v2(_TruncatedClean(), _conv())
    assert out["errors"] == []
    assert out["error_census"] == {}
    assert out["stats"]["llm"]["truncated"] == 2  # S2 + S4 (per-STAGE flag)


def test_recovered_retry_records_truncation_flag(monkeypatch):
    """D7 (#1746, review-fix): the stage-level ``truncated`` flag ORs over
    ALL calls of the stage — a first attempt that parse-fails (stop) and a
    RECOVERED retry that is truncated (length) must still record the
    truncation (``_complete`` overwrites the flag per attempt; losing it
    would produce an UNRECORDED truncation with valid=true)."""
    monkeypatch.setattr(v2.time, "sleep", lambda _: None)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)

    class _RetryTruncated:
        last_finish_reason = None

        def __init__(self):
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            if "STORY SUMMARIZER" in system:
                self.last_finish_reason = "stop"
                return "A narrative."
            if self.calls % 2 == 1:  # retry attempt: valid JSON, truncated
                self.last_finish_reason = "length"
                return ('{"points": [], "entities": [], "events": [], '
                        '"operators": []}')
            self.last_finish_reason = "stop"
            return "not json"   # first attempt: garbage, stop

    out = v2.extract_session_v2(_RetryTruncated(), _conv())
    assert out["errors"] == []
    assert out["error_census"] == {}
    # S2 + S4 both recovered via a TRUNCATED retry → the truncation survives
    # (llm calls/retries are documented not-load-bearing — the parse-retry
    # lands in the nested stats["llm"]["retries"] counter, distinct from the
    # session roll-up; D2 #1746).
    assert out["stats"]["llm"]["truncated"] == 2


def test_ingest_v2_llm_and_recovery_rollup(tmp_path, monkeypatch):
    """#1746 (D7): the LIVE ``ingest_haystack_v2`` rolls each session's
    extractor ``stats["llm"]`` (calls/retries/truncated) and
    ``stats["recovery"]`` into its per-question stats — a recovering-then-
    failing session mix sums across sessions; the session-level exception
    path contributes one call."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    class _Model:
        last_finish_reason = None

        def __init__(self, responses):
            self._responses = responses
            self.calls = 0

        def complete(self, *, system, user, max_tokens=None):
            self.calls += 1
            return self._responses(self.calls, system)

    def _resp(call: int, system: str) -> str:
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        return ('{"points": [], "entities": [], "events": [], '
                '"operators": []}')

    monkeypatch.setattr(ev2, "search_graph", lambda *a, **k: {
        "mode": "real", "degraded": False, "reason": None,
        "entities": [], "points": [], "events": [], "queries_run": 0})

    # Session 1's extraction raises a SESSION-LEVEL exception (the
    # orchestrator normally swallows stage failures, so the only way to
    # exercise the ingest catch path is at the extract boundary itself).
    # ingest_v2's function-local import picks the patched name up at call
    # time, so patching ev2.extract_session_v2 is sufficient.
    real_extract = ev2.extract_session_v2
    extract_calls = {"n": 0}

    def _flaky_extract(*a, **k):
        extract_calls["n"] += 1
        if extract_calls["n"] == 2:
            raise RuntimeError("session-1 extraction boom")
        return real_extract(*a, **k)

    monkeypatch.setattr(ev2, "extract_session_v2", _flaky_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "q_llm_roll",
            "haystack_session_ids": ["s0", "s1"],
            "haystack_dates": ["2026-06-02", "2026-06-16"],
            "haystack_sessions": [
                [{"role": "user", "content": "t0"}],
                [{"role": "user", "content": "t1"}],
            ],
        }
        stats = ingest_haystack_v2(sdk, question, model=_Model(_resp))
        # session 0: S1(1) + S2(1) + S4(1) = 3 extractor calls rolled from
        # out["stats"]["llm"]; session 1: the session-level exception path
        # contributes exactly 1 call / 0 truncated (D7).
        assert stats["llm"] == {"calls": 4, "retries": 0, "truncated": 0}
        assert stats["recovery"] == {}
        assert len(stats["errors"]) == 1
        assert stats["error_census"] == {"transient_unknown": 1}
    finally:
        sdk.close()


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


# ── #1787 Task 1: live-API probes — max_tokens=16000 acceptance ─────────────
# Both probes carry @pytest.mark.live (registered in pyproject.toml markers) so
# the deterministic regression never re-executes them (-m "not live"); the
# guard test below pins the marker.

def _probe_filler(repeat: int = 6000):
    """#1787 Task 1 — build + CALIBRATE the echo filler. ``"word `` is
    plausibly ONE BPE token (~5-7 chars), so a fixed char count is
    UNCALIBRATED. MEASURED (plan cycle 4): repeat=4700 → 32,915 chars → 9,406
    tokens (cl100k) — only ~14.8% above 8192, INSIDE the ±15% cl100k error
    band. The floor: calibrate to land at ~12K cl100k tokens (repeat=6000 →
    ~12K) so the worst-case real echo (0.85 × 12K ≈ 10.2K under the ±15%
    cl100k-vs-served-tokenizer family-drift band) clears 8192 with ~24%
    margin. Fallback when NO tokenizer is installed: the filler alone at a
    PESSIMISTIC 2 chars/token (~36K chars ≈ 18K tokens) still clears 8192 —
    the calibration never hard-fails and the live `tokens > 8192` assert
    carries the verdict."""
    import json
    filler = '"word ' * repeat            # ~42K chars at repeat=6000
    payload = json.dumps({"items": [filler]})   # well-formed JSON (P1-3)
    # calibration — count tokens of the assembled prompt BEFORE the live call
    tokenizer_name = None
    try:
        import tiktoken  # PRIMARY: no HF-hub MODEL download; BPE ranks fetched once on first use, cached
        tok = tiktoken.get_encoding("cl100k_base")  # approx, ±15%
        tokenizer_name = f"tiktoken/{tok.name}"
    except Exception:  # noqa: BLE001 — optional precision paths
        try:
            from transformers import AutoTokenizer  # OPTIONAL precision check
            tok = AutoTokenizer.from_pretrained("deepseek-ai/DeepSeek-V3")  # REQUIRES NETWORK (HF hub)
            tokenizer_name = "transformers/DeepSeek-V3"
        except Exception:  # noqa: BLE001
            tok = None  # no tokenizer — pessimistic char-bound fallback
    if tok is not None:
        n = len(tok.encode(payload))
        # assert the echo budget's LOWER bound against the ±15% calibration
        # error band — 0.85 × 10500 ≈ 8,925 > 8,192, so the worst-case real
        # echo always clears the proof threshold.
        assert n >= 10500, \
            f"filler uncalibrated: {repeat} repeats → {n} tokens (need ≥ 10,500 — " \
            f"worst-case real echo ≥ 8,925 > 8,192 at the ±15% band)"
    else:
        # no tokenizer — size so a pessimistic ~2 chars/token bound clears
        # 8192 and let the live tokens>8192 assert carry the verdict.
        chars = len(payload)
        n = chars // 2
        assert n >= 8192, f"filler too small even at 2 chars/token: {chars} chars → ~{n} tokens"
        tokenizer_name = "pessimistic-2-chars-per-token (NO tokenizer installed)"
    return payload, n, tokenizer_name


@pytest.mark.live
def test_probe_max_tokens_above_8k():
    """#1787 Task 1 — the V4 ceiling is 384K; the legacy alias must accept
    max_tokens=16000 without a 400 or a server-side clamp to 8192. The probe
    forces a LONG generation (calibrated JSON echo, ~12K tokens) and asserts
    the adapter recorded >8192 completion tokens; finish_reason is read per
    P1-1 — clamp-vs-exhaust is distinguishable by completion_tokens alone:
    clamp-at-8192 → length at ~8K; exhaust-at-16K → length at ~16K. The echo
    is ALSO fidelity-checked (P1-B): a well-formed-but-short echo
    (elided/shortened) must not be misread as a clamp signal. Runs through
    _complete's deadline machinery (deadline_s=800 = 0.05 × 16000, the scaled
    default's math) so a stalled chunked response is bounded and classifiable.
    #1787 Task 2 Step 6 owns the deadline_s=800 → None edit once the scaled
    sentinel lands (inherits the same 800s)."""
    import os
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("no DEEPSEEK_API_KEY")
    from tortoise import extractor_v2 as v2
    from tortoise.model_adapters import DeepSeekDirectModel
    m = DeepSeekDirectModel("deepseek-chat", max_tokens=None, temperature=0.0)
    payload, est_tokens, tokenizer_name = _probe_filler()
    print(f"probe calibrator: {tokenizer_name} — {est_tokens} tokens "
          f"(floor ≥ 10,500; worst-case real echo ≥ 8,925 > 8,192)")
    import json
    json.loads(payload)  # P1-3: well-formed-JSON guarantee before the call
    resp = v2._complete(
        m, system="Emit the exact JSON object from the user message, unchanged.",
        user=payload, max_tokens=16000, deadline_s=800)
    assert isinstance(resp, str) and resp
    tokens = m.last_completion_tokens or 0
    if m.last_finish_reason == "length":
        # length: either clamp (tokens ≈ 8192 — FAIL) or exhaust (tokens ≈
        # 16000 — PASS; the echo is mid-string-truncated and UNPARSEABLE by
        # construction — that is the PASS signal, not a failure).
        assert tokens > 8192, \
            f"server clamped output at {tokens} tokens (finish=length)"
        print(f"probe: finish=length with {tokens} tokens — exhaust, "
              f"NOT a clamp (echo mid-string-truncated by design); PASS")
    else:
        # finish=stop: the echo is COMPLETE — run the fidelity checks. The
        # clamp assert is gated on finish=length; stop + tokens ≤ 8192 falls
        # through to the fidelity check and is classified as an
        # authoring/sizing signal, NOT a clamp.
        assert tokens > 8192, \
            f"echo too short: {tokens} tokens (finish=stop — the served " \
            f"tokenizer is ≥1.6× sparser than cl100k or the model elided " \
            f"the echo; re-run with repeat adjustment — NOT a clamp signal)"
        json.loads(resp)                   # round-trip parse — payload is JSON
        assert len(resp) >= 0.9 * len(payload), \
            f"echo fidelity: response {len(resp)} chars < 0.9 × payload " \
            f"{len(payload)} chars (model elided/shortened the echo — " \
            f"finish={m.last_finish_reason!r}; re-run with repeat adjustment " \
            f"/ investigate the served model's output preference — NOT a " \
            f"clamp)"


@pytest.mark.live
def test_probe_v4_flash_non_thinking():
    """#1787 Task 1 Step 3 — probe deepseek-v4-flash + non-thinking (the
    migration-safe variant). Expected per the adapter docstring (pilot
    #1549): api.deepseek.com's deepseek-v4-flash reasons by default and
    collapses to empty output (all tokens spent reasoning, finish=length,
    ZERO content) — that is exactly why the direct lane wires
    `deepseek-chat`. This probe documents the collapse as a baseline; a
    400/unknown-model on this wire id is itself DOCUMENTED (xfail), not a
    hard failure. Routed through _complete's deadline machinery
    (deadline_s=800) like the alias probe."""
    import os
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.skip("no DEEPSEEK_API_KEY")
    from tortoise.model_adapters import DeepSeekDirectModel
    from tortoise import extractor_v2 as v2
    m = DeepSeekDirectModel("deepseek-v4-flash", max_tokens=None, temperature=0.0)
    try:
        resp = v2._complete(m, system="Reply ok.",
                            user="JSON: {\"ok\": true}", max_tokens=16000,
                            deadline_s=800)
    except Exception as e:  # noqa: BLE001 — P2-G: 400/unknown-model is documented, not fatal
        status = getattr(e, 'response', None) and getattr(e.response, 'status_code', None)
        if status == 400 or 'model' in str(e).lower() and 'unknown' in str(e).lower():
            pytest.xfail("direct API does not serve deepseek-v4-flash (400 "
                         "unknown-model on this wire id) — recorded on #1787 "
                         "(P2-G); re-enable after the companion adapter "
                         "migration (#1790) or when the direct API serves it")
        raise  # genuine unexpected error — hard FAIL
    if resp == "":
        # documented collapse — assert the SIGNATURE (finish=length: all
        # tokens spent reasoning, zero content), not just emptiness.
        assert m.last_finish_reason == "length", (
            f"v4-flash returned empty WITHOUT finish=length "
            f"({m.last_finish_reason!r}) — not the documented collapse; "
            f"update after adapter migration")
        pytest.xfail("v4-flash reasons by default (pilot #1549) — pending "
                     "companion adapter-migration (thinking toggle)")
    # non-empty path — assert the response is real JSON (the user prompt
    # demanded a JSON value), not any truthy string.
    import json
    assert isinstance(resp, str) and resp
    json.loads(resp)  # must parse — a non-JSON/partial blob FAILS the probe


def test_live_probe_markers_pinned():
    """#1787 Task 1 guard — both live probes MUST carry @pytest.mark.live
    (registered in pyproject.toml markers) so the deterministic regression
    (-m "not live") never re-executes live API calls. A dropped marker on
    either probe fails here."""
    import inspect
    from tests.test_extractor_reliability import (  # noqa: F401 — module access
        test_probe_max_tokens_above_8k as alias_probe)
    from tests.test_extractor_reliability import (
        test_probe_v4_flash_non_thinking as v4_probe)
    for name, fn in (("test_probe_max_tokens_above_8k", alias_probe),
                     ("test_probe_v4_flash_non_thinking", v4_probe)):
        marker = getattr(fn, "pytestmark", None)
        assert marker and any(getattr(m, "name", None) == "live" for m in marker), \
            f"{name} must carry @pytest.mark.live"
        # sanity: the test body calls the live API (not a stub)
        src = inspect.getsource(fn)
        assert "DEEPSEEK_API_KEY" in src and "DeepSeekDirectModel" in src
