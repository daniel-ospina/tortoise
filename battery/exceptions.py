"""Battery domain exceptions (contract surface for the CLI exit-code
mapping; the CLI imports and catches these classes).

Note (#3327, 2026-09-12): #1410/#1413 do NOT all raise the classes here.
InconclusiveRun in particular has ZERO raise sites -- the matched-recall
pre-pass returns a result object and expresses INCONCLUSIVE as the
persisted outcome value plus exit code 3, not as an exception. Treat this
module as the exit-code CONTRACT, not as an inventory of live producers.

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
    """Matched-recall regime INCONCLUSIVE (exit 3).

    **Currently unused — reserved. There are ZERO ``raise`` sites.** The
    pre-pass (#1413 indicator 1) returns a *result object*
    (``{f1_by_arm, trigger_fired, subset_pct}``) rather than raising, so
    INCONCLUSIVE is expressed as the persisted outcome value plus CLI
    exit code 3 — not as an exception. ``battery/cli.py`` still *catches*
    this class to map it to exit 3 (the contract mapping), but since
    nothing raises it that branch is currently dead. Reserved for a
    future caller that genuinely needs the hard-fail form; do not cite
    this class as a producer that exists. Decision: #3327 (2026-09-12).
    """


class ScoreUnavailable(BatteryError):
    """A scorer could not produce a value (episode excluded + counted; NOT an
    exit code — #1410 wires the raise)."""


class IsolationBreach(BatteryError):
    """Cross-arm memory contamination detected (exit 4; trigger wired by
    #1408/E2E-3.6 — the mapping is contract-only in this slice)."""
