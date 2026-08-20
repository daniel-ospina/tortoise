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
