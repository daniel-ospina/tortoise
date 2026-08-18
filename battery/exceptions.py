"""Battery domain exceptions (contract surface for the CLI exit-code
mapping; #1410/#1413 raise/import the shipped classes).

EmptyCorpus is deliberately NOT a ConfigError subclass and the CLI
dispatcher catches it BEFORE ConfigError so exit 5 is never masked into
exit 1 (E2E-1.4).
"""
from __future__ import annotations


class BatteryError(Exception):
    """Base class for battery domain errors."""


class EmptyCorpus(BatteryError):
    """Corpus has zero scenarios — refuse to start (exit 5, E2E-1.4)."""


class ConfigError(BatteryError):
    """Invalid/missing config (exit 1, operational)."""


class GoldVerificationError(ConfigError):
    """A gold_ref file is missing or its sha256 does not match (exit 1)."""


class JudgeGateBlocked(BatteryError):
    """Judge-validation gate blocks scoring (exit 2; trigger wired by #1410)."""


class InconclusiveRun(BatteryError):
    """Matched-recall regime INCONCLUSIVE (exit 3; trigger wired by #1413)."""


class ScoreUnavailable(BatteryError):
    """A scorer could not produce a value (episode excluded + counted; NOT an
    exit code — #1410 wires the raise)."""


class IsolationBreach(BatteryError):
    """Cross-arm memory contamination detected (exit 4; trigger wired by
    #1408/E2E-3.6 — the mapping is contract-only in this slice)."""
