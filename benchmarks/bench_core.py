"""bench_core — pure measurement core for the #316 latency benchmark.

DB-agnostic and unit-testable without a live graph: percentile summaries,
warmup-then-measure protocol, per-arm runner with circuit-breaker hygiene,
the failure taxonomy, and the pre-registered verdict rules.

Pre-registered numbers (issue #316 scoping, v5.1 — do NOT edit without a
scoping revision):

    Per-strategy targets (isolation p95, censored column):
        FTS      <  50 ms
        vector   < 100 ms
        hybrid   < 200 ms   (≈ max(strategy) — strategies run in PARALLEL
                             under the 500ms collective cap)
        TF-IDF   < 500 ms   (degraded fallback)

    E2E-8 target: mix-weighted p95 ≤ 300 ms (censored / client wall-clock
    column). Verdict bands:
        ≤ 300 ms         → "achieved"
        300–500 ms       → "cap-dominated" (collective 500ms cap dominates;
                            budget eaten by the cap, not strategy work)
        > 500 ms (+ ε)   → "tail" (join-tail / post-cap work)

    #317 headroom = 300 − full-E2E-uncensored-p95 (elevated column) — bounds
    the cross-encoder reranker budget.

Failure taxonomy (4 classes, per scoping):
    healthy       — completed within cap with results
    degraded      — completed within cap, empty results (no-match query or
                    strategy short-circuit; "degraded-fast" verdict guard)
    capped        — right-censored by the driver/collective cap (timeout
                    exception, or elapsed ≥ cap with no results)
    capped-tail   — returned results but only after the cap elapsed
    invalidating  — environmental failure (auth/DB down) → aborts the arm
    breaker-open  — first-class failure: circuit breaker tripped mid-arm
"""
from __future__ import annotations

import math
import statistics
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Literal

# ── Pre-registered numbers (scoping §Plan / issue body — do NOT drift) ──────

PRE_REGISTERED_TARGETS_MS: dict[str, float] = {
    "fts": 50.0,
    "vector": 100.0,
    "hybrid": 200.0,
    "tfidf": 500.0,
}

E2E_TARGET_MS: float = 300.0          # E2E-8: mix-weighted p95 ≤ 300ms
CAP_MS: int = 500                     # degradation_chain collective cap (driver timeout must be int)
ELEVATED_TIMEOUT_MS: int = 5000       # elevated-cap column (#317 uncensored)
E2E_TAIL_EPS_MS: float = 0.0          # band boundary is exactly > CAP_MS

DEFAULT_WARMUP_ITERS = 5
DEFAULT_WARMUP_MAX_ITERS = 20
WARMUP_CV_TARGET = 0.10               # warm-up steady-state CV < 10%
DEFAULT_SAMPLES = 50                  # scoping per-tier: 100 / 50 / 25

# Failure classes (scoping: capped / invalidating / healthy / capped-tail,
# plus degraded + breaker-open which the benchmark surfaces explicitly).
FailureClass = Literal[
    "healthy", "degraded", "capped", "capped-tail", "invalidating", "breaker-open",
]

E2E_Verdict = Literal["achieved", "cap-dominated", "tail"]


# ── Stats ────────────────────────────────────────────────────────────────────

def percentile(sorted_values: list[float], q: float) -> float:
    """Nearest-rank percentile over an ASCENDING list. q in (0, 100]."""
    if not sorted_values:
        return 0.0
    rank = math.ceil(q / 100.0 * len(sorted_values))
    return sorted_values[min(rank, len(sorted_values)) - 1]


@dataclass
class LatencyStats:
    count: int = 0
    mean_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    # Failure-class counters (healthy samples excluded from the p50/p95/p99
    # percentile columns — a capped sample's elapsed is right-censored and
    # must never sit in the latency distribution).
    healthy: int = 0
    degraded: int = 0
    capped: int = 0
    capped_tail: int = 0
    invalidating: int = 0
    breaker_open: int = 0

    @property
    def failure_count(self) -> int:
        return self.degraded + self.capped + self.capped_tail + self.invalidating + self.breaker_open


def summarize(samples: list[dict]) -> LatencyStats:
    """Summarize raw samples [{elapsed_ms, status, results}] → LatencyStats.

    Only `healthy` samples contribute to the latency distribution; capped
    samples are right-censored by definition and must never pollute p95
    (scoping: "short-circuits bucketed never in p95").
    """
    healthy_elapsed = sorted(
        s["elapsed_ms"] for s in samples if s["status"] == "healthy"
    )
    stats = LatencyStats(count=len(samples))
    if healthy_elapsed:
        stats.mean_ms = statistics.fmean(healthy_elapsed)
        stats.min_ms = healthy_elapsed[0]
        stats.max_ms = healthy_elapsed[-1]
        stats.p50_ms = percentile(healthy_elapsed, 50)
        stats.p95_ms = percentile(healthy_elapsed, 95)
        stats.p99_ms = percentile(healthy_elapsed, 99)
    for s in samples:
        status = s["status"]
        setattr(stats, status.replace("-", "_"), getattr(stats, status.replace("-", "_")) + 1)
    return stats


def stats_to_dict(stats: LatencyStats) -> dict:
    d = asdict(stats)
    d["percentile_basis"] = (
        "healthy-samples-only (capped/degraded right-censored, excluded)"
    )
    return d


# ── Warmup protocol ─────────────────────────────────────────────────────────

@dataclass
class WarmupProtocol:
    """Warmup-then-measure: run warmup iterations (discarded) until steady
    state (CV < WARMUP_CV_TARGET on the trailing window) or max iterations.
    Scoping: "warm-up CV<10% + max-iterations cap". DB-agnostic — the callable
    may hit a live graph; the protocol only observes elapsed times.
    """

    iters: int = DEFAULT_WARMUP_ITERS
    max_iters: int = DEFAULT_WARMUP_MAX_ITERS
    cv_target: float = WARMUP_CV_TARGET
    window: int = 5

    def warmup(self, fn: Callable[[], float | None]) -> int:
        """Run discarded warmup iterations; return how many were run.

        fn() must return elapsed_ms (None falls back to wall-clock of the
        call). Stop early once the trailing window's coefficient of variation
        drops below cv_target (steady state).
        """
        elapsed: list[float] = []
        iters_run = 0
        for _ in range(self.max_iters):
            t0 = time.perf_counter()
            value = fn()
            sample = value if value is not None else (time.perf_counter() - t0) * 1000.0
            elapsed.append(float(sample))
            iters_run += 1
            if iters_run >= self.iters:
                window = elapsed[-self.window:]
                if len(window) >= 3 and _cv(window) < self.cv_target:
                    break
        return iters_run

    def to_dict(self) -> dict:
        return {
            "iters": self.iters,
            "max_iters": self.max_iters,
            "cv_target": self.cv_target,
            "window": self.window,
        }


def _cv(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = statistics.fmean(values)
    if mean <= 0:
        return 0.0
    return statistics.pstdev(values) / mean


# ── Arm runner ──────────────────────────────────────────────────────────────

@dataclass
class ArmConfig:
    name: str
    samples: int = DEFAULT_SAMPLES
    timeout_ms: float = CAP_MS
    warmup: WarmupProtocol = field(default_factory=WarmupProtocol)


class ArmInvalidated(Exception):
    """Environmental failure (auth/DB down) — the run is not comparable."""


def classify_sample(
    elapsed_ms: float, results_count: int, error: str | None, timeout_ms: float
) -> str:
    """Map one raw call outcome onto the failure taxonomy."""
    if error is not None:
        lowered = error.lower()
        if "timeout" in lowered or "timed out" in lowered or "deadline" in lowered:
            return "capped"
        return "invalidating"
    if elapsed_ms >= timeout_ms:
        # Returned rows past the cap → the cap truncated the tail, not the work.
        return "capped-tail" if results_count > 0 else "capped"
    return "healthy" if results_count > 0 else "degraded"


@dataclass
class ArmResult:
    name: str
    timeout_ms: float
    samples_requested: int
    stats: LatencyStats
    warmup_iters: int
    breaker_tripped: bool = False
    invalidated: bool = False
    invalidated_reason: str | None = None
    # degraded-fast guard: majority of samples empty → latency is NOT
    # representative of the strategy (it short-circuited), so the per-target
    # pass/flag verdict is not meaningful.
    degraded_fast: bool = False

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "timeout_ms": self.timeout_ms,
            "samples_requested": self.samples_requested,
            "stats": stats_to_dict(self.stats),
            "warmup_iters": self.warmup_iters,
            "breaker_tripped": self.breaker_tripped,
            "invalidated": self.invalidated,
            "invalidated_reason": self.invalidated_reason,
            "degraded_fast": self.degraded_fast,
        }


def run_arm(
    name: str,
    fn: Callable[[], tuple[float, int, str | None]],
    *,
    samples: int = DEFAULT_SAMPLES,
    timeout_ms: float = CAP_MS,
    warmup: WarmupProtocol | None = None,
    breaker_names: list[str] | None = None,
    reset_breakers: Callable[[], None] | None = None,
    breaker_is_open: Callable[[str], bool] | None = None,
    degraded_fast_threshold: float = 0.5,
) -> ArmResult:
    """Run one measurement arm with warmup + breaker hygiene.

    fn() → (elapsed_ms, results_count, error) for ONE query invocation.
    breaker_names: strategy breaker names to snapshot before/after and reset
    between arms (hygiene — an OPEN breaker short-circuits and would otherwise
    look like instant "healthy" degraded latency).
    """
    warmup = warmup or WarmupProtocol()
    samples_list: list[dict] = []

    # Pre-snapshot: breaker state must be CLOSED before an arm measures work.
    if reset_breakers is not None:
        reset_breakers()
    breaker_tripped = False

    # Warmup (discarded). Uses the fn's REPORTED elapsed — a warmup error is
    # not a measurement (and breakers are not consulted during warmup; the
    # measured phase classifies any breaker-open as a first-class failure).
    def _reported_or_zero():
        try:
            return fn()[0]
        except Exception:
            return 0.0

    warmup_iters = warmup.warmup(_reported_or_zero)

    # Measured phase.
    for _ in range(samples):
        elapsed_ms, results_count, error = _time_fn(fn)
        status = classify_sample(elapsed_ms, results_count, error, timeout_ms)
        samples_list.append({"elapsed_ms": elapsed_ms, "status": status, "results": results_count})
        if status == "invalidating":
            return ArmResult(
                name=name, timeout_ms=timeout_ms, samples_requested=samples,
                stats=summarize(samples_list), warmup_iters=warmup_iters,
                invalidated=True, invalidated_reason=error,
            )
        if breaker_is_open is not None and breaker_names:
            if any(breaker_is_open(b) for b in breaker_names):
                breaker_tripped = True
                # First-class failure: short-circuit sampled from an OPEN
                # breaker is fast-but-meaningless — keep the sample classified
                # as breaker-open for the failure-count columns.
                samples_list[-1]["status"] = "breaker-open"
                break

    stats = summarize(samples_list)
    healthy_frac = (
        stats.healthy / stats.count if stats.count else 0.0
    )
    return ArmResult(
        name=name, timeout_ms=timeout_ms, samples_requested=samples,
        stats=stats, warmup_iters=warmup_iters, breaker_tripped=breaker_tripped,
        degraded_fast=healthy_frac < (1.0 - degraded_fast_threshold),
    )


def _time_fn(fn) -> tuple[float, int, str | None]:
    t0 = time.perf_counter()
    try:
        elapsed, count, error = fn()
        return elapsed, count, error
    except Exception as e:  # noqa: BLE001 — classify at the arm boundary
        return (time.perf_counter() - t0) * 1000.0, 0, str(e)


# ── Two-column protocol (censored vs elevated-cap, unpaired) ────────────────

def run_arm_pair(
    name: str,
    fn: Callable[[float], tuple[float, int, str | None]],
    *,
    samples: int = DEFAULT_SAMPLES,
    censored_timeout_ms: float = CAP_MS,
    elevated_timeout_ms: float = ELEVATED_TIMEOUT_MS,
    **arm_kwargs,
) -> dict:
    """Run the same arm twice: censored (default cap) + elevated (uncapped
    true-completion) columns. Unpaired — separate sample sets per column.
    fn(timeout_ms) → (elapsed_ms, results_count, error)."""
    censored = run_arm(
        f"{name}:censored", lambda: fn(censored_timeout_ms),
        samples=samples, timeout_ms=censored_timeout_ms, **arm_kwargs,
    )
    elevated = run_arm(
        f"{name}:elevated", lambda: fn(elevated_timeout_ms),
        samples=samples, timeout_ms=elevated_timeout_ms, **arm_kwargs,
    )
    return {"censored": censored, "elevated": elevated}


# ── Pre-registered verdict rules ────────────────────────────────────────────

def strategy_verdict(p95_ms: float, target_ms: float) -> str:
    """PASS when p95 < target (strictly below, matching the '<' pre-registration)."""
    return "PASS" if p95_ms < target_ms else "FLAG"


def e2e_verdict(mix_weighted_p95_ms: float) -> E2E_Verdict:
    """E2E-8 verdict bands (scoping: ≤300 achieved / 300-500 cap-dominated /
    >500+ε tail)."""
    if mix_weighted_p95_ms <= E2E_TARGET_MS:
        return "achieved"
    if mix_weighted_p95_ms <= CAP_MS + E2E_TAIL_EPS_MS:
        return "cap-dominated"
    return "tail"


def headroom_ms(elevated_p95_ms: float) -> float:
    """#317 cross-encoder budget: 300 − full-E2E-uncensored-p95."""
    return E2E_TARGET_MS - elevated_p95_ms
