"""DB-agnostic logic tests for benchmarks.bench_core (issue #316).

No graph, no DB, no heavy deps — pure measurement-core semantics: percentile
math, failure taxonomy, warmup protocol, arm-runner breaker hygiene, and the
pre-registered verdict rules (do NOT edit targets here — they live in
bench_core.PRE_REGISTERED_TARGETS_MS / E2E bands).
"""
from __future__ import annotations

import pytest

from benchmarks.bench_core import (
    CAP_MS,
    E2E_TARGET_MS,
    ELEVATED_TIMEOUT_MS,
    PRE_REGISTERED_TARGETS_MS,
    ArmResult,
    WarmupProtocol,
    classify_sample,
    e2e_verdict,
    headroom_ms,
    percentile,
    run_arm,
    run_arm_pair,
    strategy_verdict,
    summarize,
)

pytestmark = pytest.mark.bench


# ── Percentiles ─────────────────────────────────────────────────────────────

def test_percentile_nearest_rank():
    values = list(range(1, 101))  # 1..100
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 95
    assert percentile(values, 99) == 99
    assert percentile(values, 100) == 100


def test_percentile_empty_returns_zero():
    assert percentile([], 95) == 0.0


# ── summarize / right-censoring ─────────────────────────────────────────────

def test_summarize_excludes_capped_and_degraded_from_percentiles():
    """Capped/degraded samples are right-censored — never in the latency
    distribution (scoping: 'short-circuits bucketed never in p95')."""
    samples = [
        {"elapsed_ms": 10, "status": "healthy", "results": 5},
        {"elapsed_ms": 20, "status": "healthy", "results": 5},
        {"elapsed_ms": 30, "status": "healthy", "results": 5},
        {"elapsed_ms": 40, "status": "healthy", "results": 5},
        {"elapsed_ms": 50, "status": "healthy", "results": 5},
        {"elapsed_ms": 500, "status": "capped", "results": 0},        # cap-truncated
        {"elapsed_ms": 510, "status": "capped-tail", "results": 3},   # late return
        {"elapsed_ms": 1, "status": "degraded", "results": 0},        # no-match
    ]
    stats = summarize(samples)
    assert stats.count == 8
    assert stats.healthy == 5
    assert stats.capped == 1
    assert stats.capped_tail == 1
    assert stats.degraded == 1
    assert stats.p50_ms == 30
    assert stats.p95_ms == 50  # not polluted by the 500ms capped sample
    assert stats.p99_ms == 50
    assert stats.max_ms == 50


# ── Failure taxonomy ────────────────────────────────────────────────────────

def test_classify_sample_taxonomy():
    assert classify_sample(10.0, 5, None, 500) == "healthy"
    assert classify_sample(10.0, 0, None, 500) == "degraded"
    assert classify_sample(600.0, 0, None, 500) == "capped"          # late empty
    assert classify_sample(600.0, 3, None, 500) == "capped-tail"     # late rows
    assert classify_sample(50.0, 0, "timeout after 500ms", 500) == "capped"
    assert classify_sample(50.0, 0, "auth failed: 401", 500) == "invalidating"


# ── Warmup protocol ─────────────────────────────────────────────────────────

def test_warmup_protocol_runs_and_discards():
    """Warmup iterations run before measured samples and are discarded — the
    measured distribution must come from the steady-state phase only."""
    warmup = WarmupProtocol(iters=5, max_iters=20, cv_target=0.10, window=5)
    calls = []

    def steady(_=0):
        calls.append(1)
        return 10.0  # perfectly steady → CV 0 < 0.10 → stops at `iters`

    iters_run = warmup.warmup(steady)
    assert iters_run == warmup.iters  # steady state reached at the floor
    assert len(calls) == warmup.iters


def test_warmup_protocol_max_iters_cap():
    """A wildly oscillating warmup never exceeds max_iters (no infinite loop)."""
    warmup = WarmupProtocol(iters=3, max_iters=10, cv_target=0.10, window=3)
    noisy = [100.0, 1.0, 50.0, 200.0, 0.5, 150.0, 3.0, 90.0, 2.0, 300.0]
    i = {"n": 0}

    def oscillating():
        v = noisy[i["n"] % len(noisy)]
        i["n"] += 1
        return v

    iters_run = warmup.warmup(oscillating)
    assert iters_run == warmup.max_iters  # cap engaged (CV never settles)


# ── arm_runner hygiene ──────────────────────────────────────────────────────

class _FakeBreaker:
    """Tracks reset calls; can be forced open on demand (status probe)."""

    def __init__(self):
        self.reset_calls = 0
        self.open = False

    def reset(self):
        self.reset_calls += 1
        self.open = False  # reset closes the breaker (mirrors real semantics)

    def is_open(self):
        return self.open


def test_run_arm_resets_breakers_between_arms():
    """Breaker hygiene: reset before each arm — an OPEN breaker from a prior
    arm must never bleed into the next arm's measurement."""
    breaker = _FakeBreaker()
    breaker.open = True  # stale open from a previous arm

    def fn():
        return 5.0, 3, None

    result = run_arm(
        "fts", fn, samples=3, timeout_ms=CAP_MS,
        breaker_names=["fts"], reset_breakers=breaker.reset,
        breaker_is_open=lambda _: breaker.is_open(),
    )
    assert breaker.reset_calls >= 1  # reset ran before the arm
    assert result.stats.healthy == 3  # measured real work, not short-circuits
    assert result.breaker_tripped is False


def test_run_arm_breaker_open_is_first_class_failure():
    """If the breaker opens MID-arm, remaining samples stop and the failure is
    bucketed as breaker-open — never as healthy low latency."""
    breaker = _FakeBreaker()
    breaker.open = False
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] >= 3:
            breaker.open = True  # breaker trips on the 3rd measured sample
        return 5.0, 3, None

    result = run_arm(
        "vector", fn, samples=10, timeout_ms=CAP_MS,
        warmup=WarmupProtocol(iters=0, max_iters=0),  # no warmup calls
        breaker_names=["vector"], reset_breakers=breaker.reset,
        breaker_is_open=lambda _: breaker.is_open(),
    )
    assert result.breaker_tripped is True
    assert result.stats.breaker_open == 1
    # Samples after the trip are NOT measured (arm stops) — the trip itself
    # is bucketed as breaker-open, never as healthy latency.
    assert result.stats.healthy == 2
    assert result.stats.count == 3


def test_run_arm_degraded_fast_guard():
    """Majority empty results → degraded-fast: the measured latency is NOT
    representative (strategy short-circuited), so the per-target verdict is
    not meaningful."""
    result = run_arm(
        "fts", lambda: (2.0, 0, None), samples=10, timeout_ms=CAP_MS,
    )
    assert result.degraded_fast is True
    assert result.stats.degraded == 10


def test_run_arm_invalidating_aborts():
    """Environmental failure (auth/DB down) invalidates the run — the column
    cannot be compared against pre-registered targets."""
    result = run_arm(
        "fts", lambda: (1.0, 0, "authentication failed: 401"),
        samples=10, timeout_ms=CAP_MS,
    )
    assert result.invalidated is True
    assert "authentication failed" in (result.invalidated_reason or "")
    assert result.stats.count == 1  # aborted after first invalidating sample


def test_run_arm_two_column_protocol():
    """Censored column uses the default 500ms cap; elevated column uses
    elevated_timeout_ms — unpaired sample sets."""
    def fn(timeout_ms):
        return 10.0, 3, None

    pair = run_arm_pair("fts", fn, samples=5)
    assert pair["censored"].timeout_ms == CAP_MS
    assert pair["elevated"].timeout_ms == ELEVATED_TIMEOUT_MS
    assert pair["censored"].stats.count == 5
    assert pair["elevated"].stats.count == 5


# ── Pre-registered verdict rules ────────────────────────────────────────────

def test_strategy_verdict_strict_less_than():
    assert strategy_verdict(49.9, 50.0) == "PASS"
    assert strategy_verdict(50.0, 50.0) == "FLAG"   # target is '<', not '<='
    assert strategy_verdict(500.0, 500.0) == "FLAG"


def test_pre_registered_targets_match_scoping():
    """The fixed targets from the scoping doc — a drift here is a scoping
    violation, not a code fix."""
    assert PRE_REGISTERED_TARGETS_MS == {
        "fts": 50.0, "vector": 100.0, "hybrid": 200.0, "tfidf": 500.0,
    }
    assert E2E_TARGET_MS == 300.0
    assert CAP_MS == 500.0
    assert ELEVATED_TIMEOUT_MS == 5000.0


def test_e2e_verdict_bands():
    assert e2e_verdict(300.0) == "achieved"          # ≤300
    assert e2e_verdict(250.0) == "achieved"
    assert e2e_verdict(300.1) == "cap-dominated"     # 300–500
    assert e2e_verdict(499.9) == "cap-dominated"
    assert e2e_verdict(501.0) == "tail"              # >500+ε
    assert e2e_verdict(800.0) == "tail"


def test_headroom_317():
    assert headroom_ms(200.0) == 100.0               # 300 − 200
    assert headroom_ms(350.0) == -50.0               # negative → no headroom


# ── ArmResult shape ─────────────────────────────────────────────────────────

def test_arm_result_to_dict_has_failure_classes():
    result = ArmResult(name="x", timeout_ms=CAP_MS, samples_requested=3,
                       stats=summarize([
                           {"elapsed_ms": 1, "status": "healthy", "results": 1},
                           {"elapsed_ms": 2, "status": "capped", "results": 0},
                       ]), warmup_iters=2)
    d = result.to_dict()
    assert d["stats"]["healthy"] == 1
    assert d["stats"]["capped"] == 1
    assert d["stats"]["percentile_basis"].startswith("healthy-samples-only")
