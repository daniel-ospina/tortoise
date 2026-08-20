"""Task 1 tests — enums + exceptions + exit-code mapping (contract surface)."""
from __future__ import annotations

import pytest  # noqa: F401

from battery.enums import EpOutcome, ExitCode, ModelCallOutcome, Tier
from battery.exceptions import (
    ConfigError,
    EmptyCorpus,
    GoldVerificationError,
    InconclusiveRun,
    IsolationBreach,
    JudgeGateBlocked,
    ScoreUnavailable,
)


class TestTier:
    def test_membership(self):
        assert {t.value for t in Tier} == {"probe", "stream", "differential"}

    def test_flag_mapping(self):
        assert Tier.from_flag(1) is Tier.PROBE
        assert Tier.from_flag(2) is Tier.STREAM
        assert Tier.from_flag(3) is Tier.DIFFERENTIAL


class TestExitCode:
    def test_values(self):
        assert ExitCode.OK == 0
        assert ExitCode.OPERATIONAL == 1
        assert ExitCode.GATE_BLOCKED == 2
        assert ExitCode.INCONCLUSIVE == 3
        assert ExitCode.ARM_FAILED == 4
        assert ExitCode.EMPTY_CORPUS == 5


class TestEpOutcome:
    def test_values(self):
        assert {o.value for o in EpOutcome} == {"converged", "non_converged", "undec"}


class TestModelCallOutcome:
    def test_values(self):
        assert {o.value for o in ModelCallOutcome} == {
            "ok", "rate_limited", "timeout", "fallback_cached", "failed"}


class TestExceptionHierarchy:
    def test_empty_corpus_not_config_error(self):
        """Exit 5 must never be masked into exit 1 (scope DD7)."""
        assert not issubclass(EmptyCorpus, ConfigError)
        assert issubclass(GoldVerificationError, ConfigError)

    def test_contract_exceptions_exist(self):
        for cls in (JudgeGateBlocked, InconclusiveRun, IsolationBreach,
                    ScoreUnavailable, EmptyCorpus):
            assert issubclass(cls, Exception)
