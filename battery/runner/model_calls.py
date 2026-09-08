"""Model-call layer — outcome recording + retry table + fallback cache.

S3 surface: every model call records its outcome ∈ ModelCallOutcome
{ok, rate_limited, timeout, fallback_cached, failed} — never silent (the
epic's critical bug-pattern flag). Retry table (scope DD8):
  rate_limited  → ≤2 backoff retries, then TERMINAL rate_limited
  timeout       → ≤1 retry, then TERMINAL timeout
  failed        → no retry (terminal)
  fallback_cached → no retry (a deterministic cached response was served)

``sleep`` is injectable so CI tests never sleep.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field  # noqa: F401
from typing import Callable, Protocol  # noqa: UP035

from battery.enums import ModelCallOutcome
from battery.exceptions import ConfigError

#: Pinned deepseek-v4-flash (OpenRouter) per-1M-token rates — the real-
#: money meter's price source (matches probe_runner._PROBE_RATES_PER_1M_USD
#: + the judge reserve table's deepseek row: ONE price basis across the
#: battery's spend meters). #2284 Task 8 Step 3 (usage-capture slice).
_REAL_RATES_PER_1M_USD: tuple[float, float] = (0.27, 1.10)


def _usage_cost_usd(pt: int, ct: int) -> float:
    p_in, p_out = _REAL_RATES_PER_1M_USD
    return (float(pt) * p_in + float(ct) * p_out) / 1_000_000.0


class RealModelCaller:
    """The pinned real model caller (decision (a): deepseek/deepseek-v4-
    flash, temp 0, UNCAPPED output) — the in-repo caller-bridge seam pulled
    forward from Task 9 step 2 into Task 8 part 1 (without it tokens are
    unmeasurable). Exposes the model_adapters usage contract: after each
    call the inner adapter's ``last_prompt_tokens`` /
    ``last_completion_tokens`` hold the per-call usage. Fail-closed when
    OPENROUTER_API_KEY is absent — a real caller is never a silent mock.

    ``pin`` (a full model slug, default None): when given, the caller is
    built from arms.yaml's ``resolve_pinned_model`` — the SAME resolution
    the run's pre-flight validates — so the model that executes is never
    pinned by coincidence (review #2604 P1: the hardcoded registry key
    only matched the pin today). None => the decision-(a) registry default.
    """

    def __init__(self, pin: str | None = None,
                 key_env: str = "OPENROUTER_API_KEY"):
        import os
        if not os.environ.get(key_env):
            raise ConfigError(
                f"RealModelCaller refuses to start: {key_env} absent "
                f"(fail-closed — real model calls are spend-gated, never "
                f"a silent mock)")
        if pin:
            from battery.config.arms import resolve_pinned_model
            self._real = resolve_pinned_model(pin)
        else:
            from tortoise import model_adapters
            try:
                self._real = model_adapters.MODELS["deepseek-flash"]()
            except KeyError as e:  # pragma: no cover — registry drift guard
                raise ConfigError(
                    "model_adapters.MODELS['deepseek-flash'] missing") from e

    @property
    def model_id(self) -> str:
        return getattr(self._real, "id", "deepseek/deepseek-v4-flash")

    @property
    def temperature(self) -> float:
        return getattr(self._real, "temperature", 0.0)

    @property
    def last_prompt_tokens(self) -> int:
        return int(getattr(self._real, "last_prompt_tokens", 0) or 0)

    @property
    def last_completion_tokens(self) -> int:
        return int(getattr(self._real, "last_completion_tokens", 0) or 0)

    def call(self, *, prompt: str) -> str:
        return self._real.complete(system="", user=prompt)


@dataclass
class _UsageRow:
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float


class UsageRecordingCaller:
    """Minimal usage-capture slice (#2284 Task 8 Step 3): wraps any
    ModelCaller exposing the usage contract and records per-call token +
    dollar rows (the meter the mid-run budget cap + arms.yaml re-lock read
    from). ``cost_fn`` is injectable for hermetic tests; the default prices
    at the real pinned-model rates.
    """

    def __init__(self, caller: ModelCaller,
                 cost_fn: Callable[[int, int], float] = _usage_cost_usd):
        self._caller = caller
        self._cost_fn = cost_fn
        self.rows: list[_UsageRow] = []

    @property
    def model_id(self) -> str:
        return getattr(self._caller, "model_id", "?")

    @property
    def temperature(self) -> float:
        return getattr(self._caller, "temperature", 0.0)

    def call(self, *, prompt: str) -> str:
        text = self._caller.call(prompt=prompt)
        pt = int(getattr(self._caller, "last_prompt_tokens", 0) or 0)
        ct = int(getattr(self._caller, "last_completion_tokens", 0) or 0)
        self.rows.append(_UsageRow(pt, ct, self._cost_fn(pt, ct)))
        return text

    @property
    def spent_usd(self) -> float:
        return sum(r.cost_usd for r in self.rows)

    def totals(self) -> dict:
        return {
            "calls": len(self.rows),
            "prompt_tokens": sum(r.prompt_tokens for r in self.rows),
            "completion_tokens": sum(r.completion_tokens for r in self.rows),
            "cost_usd": round(self.spent_usd, 6),
        }


class ModelCaller(Protocol):
    """The agent/arm model under test (real provider wiring lands with
    #1408/#1409; the mock + this wrapper are the contract)."""

    model_id: str
    temperature: float

    def call(self, *, prompt: str) -> str: ...


class OutcomeRecordingCaller:
    """Wraps a ModelCaller, recording per-call outcomes + serving the
    deterministic fallback cache on failure.

    ``cache`` is a callable ``str -> str`` producing a deterministic cached
    response (or raising KeyError when no cached response exists → the call
    records ``failed``).
    """

    def __init__(self, caller: ModelCaller, *,
                 cache: Callable[[str], str] | None = None,
                 max_rate_limited_retries: int = 2,
                 max_timeout_retries: int = 1,
                 backoff_fn: Callable[[int], float] | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._caller = caller
        self._cache = cache
        self._max_rl = max_rate_limited_retries
        self._max_to = max_timeout_retries
        self._backoff = backoff_fn or (lambda attempt: float(attempt))
        self._sleep = sleep
        self.outcomes: list[ModelCallOutcome] = []

    @property
    def model_id(self) -> str:
        return self._caller.model_id

    @property
    def temperature(self) -> float:
        return getattr(self._caller, "temperature", 0.0)

    def call(self, *, prompt: str) -> str:
        """Run one model call with the retry table; record the terminal
        outcome. Returns the (possibly cached) response text."""
        rl_remaining = self._max_rl
        to_remaining = self._max_to
        while True:
            try:
                text = self._caller.call(prompt=prompt)
                self.outcomes.append(ModelCallOutcome.OK)
                return text
            except RateLimited as e:
                if rl_remaining > 0:
                    rl_remaining -= 1
                    self._sleep(self._backoff(self._max_rl - rl_remaining))
                    continue
                return self._fail(prompt, ModelCallOutcome.RATE_LIMITED, e)
            except CallTimeout as e:
                if to_remaining > 0:
                    to_remaining -= 1
                    self._sleep(self._backoff(self._max_to - to_remaining))
                    continue
                return self._fail(prompt, ModelCallOutcome.TIMEOUT, e)
        raise AssertionError("unreachable")  # pragma: no cover
    
    def _fail(self, prompt: str, outcome: ModelCallOutcome, err: Exception) -> str:
        if self._cache is not None:
            try:
                text = self._cache(prompt)
                self.outcomes.append(ModelCallOutcome.FALLBACK_CACHED)
                return text
            except KeyError:
                pass
        self.outcomes.append(outcome)
        raise ModelCallFailed(f"{outcome.value}: {err}") from err


class RateLimited(Exception):
    """Transient 429-style error (retried per the retry table)."""


class CallTimeout(Exception):
    """Transient timeout (retried once per the retry table)."""


class ModelCallFailed(Exception):
    """Terminal call failure — recorded, never silent."""


def outcome_counts(outcomes: list[ModelCallOutcome]) -> dict[str, int]:
    """{outcome.value: count} over the recorded outcomes (all enum values)."""
    return {o.value: outcomes.count(o) for o in ModelCallOutcome}
