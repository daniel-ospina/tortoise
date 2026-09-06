"""Parity leg (issue #1414, plan §5 parity/ + §7 E2E-4.1).

Runs released benchmarks per arm with PINNED dataset versions (refuse on
mismatch — no silent upgrade), and applies the methodology-unchanged check
vs the #1144 baseline record (persisted by tools/longmem_eval/report.py —
the #1414 additive extension). Since #2284 Task 6 the check compares THREE
hashes: judge rubric id hash + reader prompt hash + PROTOCOL hash over
{seed, model_pin, temperature, event_schema (SCHEMA_VERSION), tool_surface}
— a decide-loop/protocol change (schema bump, model pin change, temp/seed
change, tool-surface change) trips the unchanged-check instead of being
invisible to parity (#1414 hole closed).

#1144 BASELINE-RECORD REQUIREMENT: a baseline record MUST carry all three
hashes for the protocol leg to be verifiable. Old 2-tuple records (reader
prompt + rubric only) keep matching on the 2-tuple compare (back-compat)
but the run is marked ``protocol_unknown`` — consumers (the parity CLI)
must surface that state and persist it in the parity record so the #1144
re-record (baseline ingestion with protocol_hash) is forced rather than
leaving protocol drift invisible.

Also ships the bespoke SUPERSESSION-VS-STALE probe: released ForgetEval-class
benchmarks test deletion/drift, not Tortoise's supersede semantics — this
probe answers from the graph's CURRENT state with provenance and asserts the
stale answer is never returned.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field  # noqa: F401
from pathlib import Path  # noqa: F401

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
    #: 16-hex = 64-bit collision domain (reader-prompt + rubric hashes —
    #: #1414 back-compat). Collision policy: a truncated-hash collision on a
    #: methodology element is indistinguishable from an unchanged methodology
    #: (the unchanged-check only TRIPS parity — the #1144 baseline re-record
    #: is the recovery path), and the elements are compared INDEPENDENTLY
    #: (no concatenation), so a collision on one element never hides drift
    #: on another.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _sha256_full(text: str) -> str:
    #: FULL sha256 (64-hex) for the PROTOCOL leg (round-4 P2 #2284 Task 6
    #: parity): the protocol element is NEW (no #1414 back-compat domain),
    #: so full-length is safe and strictly more collision-resistant than
    #: the 16-hex methodology hashes.
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: Tool-surface ids the parity protocol exercises — the schema-v1.1
#: tool_event verb surface (battery/runner/emit.py _SUBTYPE_OK) the
#: decide loop records on the product store. Pinned HERE as the parity
#: single source: a decide-loop tool-surface change (add/rename a verb)
#: must update this tuple AND trip the protocol hash (that is the point).
TOOL_SURFACE_IDS: tuple[str, ...] = (
    "create_point", "create_operator", "file_nand", "register_conflict",
    "mitigate", "supersede",
)


def protocol_hash(*, seed: int, model: dict[str, str | float],
                  event_schema: str,
                  tool_surface: tuple[str, ...]) -> str:
    """FULL-sha256 protocol hash (round-4 P2 — the round-3 protocol leg
    reused the 16-hex ``_sha256``; the protocol element is new, so
    full-length is safe and back-compat-free) over the decide-loop protocol
    inputs: seed, model pin (``model["model_id"]``) + temperature, the
    event-log schema version (artifacts.SCHEMA_VERSION) and the tool-surface
    ids (sorted — order-insensitive, membership-sensitive). Every input is
    normalized to a canonical line so equivalent floats hash identically
    (0 == 0.0)."""
    lines = [
        f"seed:{seed}",
        f"model:{model.get('model_id', '')}",
        f"temperature:{float(model.get('temperature', 0.0))}",
        f"event_schema:{event_schema}",
    ]
    lines += [f"tool_surface:{t}" for t in sorted(tool_surface)]
    return _sha256_full("\n".join(lines))


@dataclass(frozen=True)
class ParityRun:
    """One benchmark × one arm parity result.

    ``protocol_hash``: the protocol hash the CURRENT run derived (backfilled
    onto the record even when the baseline could not verify it — drift is
    never invisible). ``protocol_unknown``: True when the baseline record
    has no protocol_hash (old 2-tuple record) or the caller supplied no
    protocol — the protocol leg was NOT verifiable, so consumers must warn
    and persist the state (the #1144 re-record is forced).
    """

    benchmark: str
    arm: str
    version: str
    accuracy: float | None
    methodology_matched: bool
    samples: int
    protocol_hash: str | None = None
    protocol_unknown: bool = False


def check_pinned_version(benchmark: str, version: str) -> None:
    """Refuse to run on an unpinned/mismatched dataset version (E2E-4.1)."""
    pinned = PINNED_VERSIONS.get(benchmark)
    if pinned is None:
        raise VersionMismatchError(f"benchmark {benchmark!r} not pinned")
    if version != pinned:
        raise VersionMismatchError(
            f"{benchmark} version {version!r} != pinned {pinned!r} — "
            f"refusing to run (no silent upgrade)")


def methodology_hashes(reader_prompt: str, judge_rubric_id: str, *,
                       protocol: str | None = None
                       ) -> tuple[str, str, str | None]:
    """The hashes the unchanged-check compares (#1414 + #2284 Task 6).

    Returns a 3-tuple in FIXED element order — (reader_prompt_hash,
    judge_rubric_id_hash, protocol_hash) — compared independently by
    run_parity. ``protocol`` is the precomputed protocol_hash(...) string
    (derived by the CLI from the pinned arm's model_pin/temperature +
    SCHEMA_VERSION + tool-surface ids); None when the caller has no
    protocol leg (2-tuple compare + protocol-unknown, see run_parity).
    """
    return _sha256(reader_prompt), _sha256(judge_rubric_id), protocol


def run_parity(benchmark: str, version: str, arm: str,
               reader_prompt: str, judge_rubric_id: str,
               baseline: dict[str, str] | None,
               *,
               accuracy: float | None = None,
               samples: int = 0,
               protocol: str | None = None) -> ParityRun:
    """Execute one parity cell. Raises on version/baseline mismatch.

    Compares all THREE methodology hashes when the baseline record carries
    protocol_hash AND a current ``protocol`` is supplied. Back-compat: a
    baseline WITHOUT protocol_hash (old 2-tuple record) still matches on the
    reader-prompt + rubric compare, but the run is marked ``protocol_unknown``
    (compare-2 + warn — the caller surfaces the warn) so protocol drift can
    never pass silently. A protocol change on a 3-tuple baseline trips
    ``methodology_matched=False`` even when the reader prompt and rubric are
    unchanged (the #1414 invisibility hole closed).
    """
    check_pinned_version(benchmark, version)
    if baseline is None:
        raise BaselineMissingError(
            f"baseline record missing for {benchmark} — the #1144 baseline "
            f"ingestion must persist reader_prompt_hash + "
            f"judge_rubric_id_hash + protocol_hash first (report.py "
            f"additive extension)")
    rp, jr, _ = methodology_hashes(reader_prompt, judge_rubric_id)
    base_matched = (baseline.get("reader_prompt_hash") == rp
                    and baseline.get("judge_rubric_id_hash") == jr)
    bl_proto = baseline.get("protocol_hash")
    can_verify_protocol = protocol is not None and bl_proto is not None
    protocol_matched = can_verify_protocol and protocol == bl_proto
    matched = base_matched and (protocol_matched or not can_verify_protocol)
    return ParityRun(benchmark=benchmark, arm=arm, version=version,
                     accuracy=accuracy, methodology_matched=matched,
                     samples=samples, protocol_hash=protocol,
                     protocol_unknown=not can_verify_protocol)


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
    import re
    norm = lambda t: set(re.findall(r"[a-z0-9/_-]+", t.lower()))  # noqa: E731
    old_tokens = norm(probe.old_claim)
    cur_tokens = norm(probe.current_claim)
    stale_unique = old_tokens - cur_tokens
    current_unique = cur_tokens - old_tokens
    answer = norm(retrieved_answer)
    if stale_unique and answer & stale_unique:
        return False  # answered from the superseded state
    if current_unique and answer & current_unique:  # noqa: SIM103
        return True
    return False  # ambiguous — fail closed
