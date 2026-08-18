"""Parity leg (issue #1414, plan §5 parity/ + §7 E2E-4.1).

Runs released benchmarks per arm with PINNED dataset versions (refuse on
mismatch — no silent upgrade), and applies the methodology-unchanged check
(judge rubric id hash + reader prompt hash vs the #1144 baseline record
persisted by tools/longmem_eval/report.py — the #1414 additive extension).

Also ships the bespoke SUPERSESSION-VS-STALE probe: released ForgetEval-class
benchmarks test deletion/drift, not Tortoise's supersede semantics — this
probe answers from the graph's CURRENT state with provenance and asserts the
stale answer is never returned.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

#: Pinned dataset versions (locked at implementation — the runner refuses
#: to run on mismatch; plan E2E-4.1).
PINNED_VERSIONS: dict[str, str] = {
    "longmemeval": "longmemeval-2025.3",
    "locomo": "locomo-v1",
    "memoryarena": "memoryarena-hf-rev-2026.02",
    "memoryagentbench": "memoryagentbench-2025.4",
}

class VersionMismatchError(Exception):
    """Pinned dataset version mismatch — refuse to run (no silent upgrade)."""


class BaselineMissingError(Exception):
    """#1144 baseline record (reader-prompt-hash / judge-rubric-id-hash)
    missing — run the baseline ingestion first (tools/longmem_eval)."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ParityRun:
    """One benchmark × one arm parity result."""

    benchmark: str
    arm: str
    version: str
    accuracy: float | None
    methodology_matched: bool
    samples: int


def check_pinned_version(benchmark: str, version: str) -> None:
    """Refuse to run on an unpinned/mismatched dataset version (E2E-4.1)."""
    pinned = PINNED_VERSIONS.get(benchmark)
    if pinned is None:
        raise VersionMismatchError(f"benchmark {benchmark!r} not pinned")
    if version != pinned:
        raise VersionMismatchError(
            f"{benchmark} version {version!r} != pinned {pinned!r} — "
            f"refusing to run (no silent upgrade)")


def methodology_hashes(reader_prompt: str, judge_rubric_id: str) -> tuple[str, str]:
    """The two hashes the unchanged-check compares (issue #1414)."""
    return _sha256(reader_prompt), _sha256(judge_rubric_id)


def run_parity(benchmark: str, version: str, arm: str,
               reader_prompt: str, judge_rubric_id: str,
               baseline: dict[str, str] | None,
               *,
               accuracy: float | None = None,
               samples: int = 0) -> ParityRun:
    """Execute one parity cell. Raises on version/baseline mismatch."""
    check_pinned_version(benchmark, version)
    if baseline is None:
        raise BaselineMissingError(
            f"baseline record missing for {benchmark} — the #1144 baseline "
            f"ingestion must persist reader_prompt_hash + "
            f"judge_rubric_id_hash first (report.py additive extension)")
    rp, jr = methodology_hashes(reader_prompt, judge_rubric_id)
    matched = (baseline.get("reader_prompt_hash") == rp
               and baseline.get("judge_rubric_id_hash") == jr)
    return ParityRun(benchmark=benchmark, arm=arm, version=version,
                     accuracy=accuracy, methodology_matched=matched,
                     samples=samples)


# ── Bespoke supersession-vs-stale probe ────────────────────────────────
@dataclass(frozen=True)
class StalenessProbe:
    """A superseded claim + its current superseding state."""

    scenario_id: str
    old_claim: str
    current_claim: str


def staleness_probes() -> list[StalenessProbe]:
    """Domain-neutral supersession pairs (the probe is bespoke: released
    benchmarks test deletion/drift, not Tortoise's supersede semantics)."""
    return [
        StalenessProbe("s-1",
                       "The API endpoint is /v1/deprecated",
                       "The API endpoint is /v1/current"),
        StalenessProbe("s-2",
                       "The default region is us-east-1",
                       "The default region is eu-west-2"),
        StalenessProbe("s-3",
                       "The pricing tier is Pro",
                       "The pricing tier is Enterprise"),
    ]


def score_staleness(probe: StalenessProbe,
                    retrieved_answer: str) -> bool:
    """A staleness probe passes when the retrieved answer reflects the
    CURRENT state (superseding claim), never the stale superseded answer.

    Matching uses DISCRIMINATIVE tokens only: the old and current claims
    share boilerplate ("the api endpoint is") — a stale answer must be
    caught on the tokens unique to the old claim, and a current answer
    recognized by the tokens unique to the current claim. Ambiguous
    answers (no distinctive match) FAIL closed.
    """
    norm = lambda t: set(t.lower().split())
    old_tokens = norm(probe.old_claim)
    cur_tokens = norm(probe.current_claim)
    stale_unique = old_tokens - cur_tokens
    current_unique = cur_tokens - old_tokens
    answer = norm(retrieved_answer)
    if stale_unique and answer & stale_unique:
        return False  # answered from the superseded state
    if current_unique and answer & current_unique:
        return True
    return False  # ambiguous — fail closed
