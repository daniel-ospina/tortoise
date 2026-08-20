"""Constant-by-name contract enums (plan §6: contracts referenced by enum
name, never literal strings). All child issues import from here.

- Tier: scenario tier mapping (CLI --tier 1|2|3 → probe|stream|differential)
- ExitCode: the plan §6 CLI contract (0/1/2/3/4/5)
- EpOutcome: honest EP outcome per scenario (epic plan §4; E2E-1.3)
- ModelCallOutcome: per-call outcome enum (epic plan §4; E2E-1.5)
"""
from __future__ import annotations

import enum


class Tier(enum.Enum):
    """Scenario tier. CLI flag maps 1|2|3 via ``from_flag``."""

    PROBE = "probe"
    STREAM = "stream"
    DIFFERENTIAL = "differential"

    @classmethod
    def from_flag(cls, value: int | str) -> "Tier":  # noqa: UP037
        return {1: cls.PROBE, 2: cls.STREAM, 3: cls.DIFFERENTIAL}[int(value)]


class ExitCode(enum.IntEnum):
    """CLI exit-code contract (epic plan §6).

    Exit 1 is a deliberate extension of the plan's 0/2/3/4/5 list, justified
    by in-repo precedent (judge_harness/kappa: 1 = operational error) for
    usage/config/stub errors.
    """

    OK = 0
    OPERATIONAL = 1
    GATE_BLOCKED = 2
    INCONCLUSIVE = 3
    ARM_FAILED = 4
    EMPTY_CORPUS = 5


class EpOutcome(enum.Enum):
    """Honest EP outcome recorded per scenario (plan §4; E2E-1.3).

    In this slice ep_outcome is ``converged`` on every code path (mock arm
    exposes no EP surface); the non_converged/undec computation is wired by
    #1409 (E2E-1.3 emission test).
    """

    CONVERGED = "converged"
    NON_CONVERGED = "non_converged"
    UNDEC = "undec"


class ModelCallOutcome(enum.Enum):
    """Per-model-call outcome (plan §4; S3 flag; E2E-1.5).

    ``rate_limited``/``timeout`` are TERMINAL outcomes (post-retry).
    ``fallback_cached`` = a deterministic cached response was served;
    ``failed`` = the call failed with no cached response. Both are excluded
    from metric aggregates and reported as counts — never silent.
    """

    OK = "ok"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    FALLBACK_CACHED = "fallback_cached"
    FAILED = "failed"
