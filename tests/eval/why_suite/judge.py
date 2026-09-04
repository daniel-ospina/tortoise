"""Pinned why-layer judge — ``judge_why_suite_v1`` (issue #2100, epic #2080).

The zero-dependency first increment of the why-suite (issue Indicator 2):
the pinned judge prompt is a STATIC FILE (``judge_why_suite_v1.txt``) whose
sha256 is folded into the protocol pin.  Nothing in this module depends on
W4-a's assembly — E2E-1's grading arm consumes the pin as a FILE ARTIFACT,
so the dependency graph stays acyclic (W3-b → judge prompt; W4-a E2E-1 →
the pin file, never the other way).

The judge protocol:

* The four why-questions (conflict-surfacing / dig-deeper navigation /
  support-chain sufficiency / trade-off sufficiency) are graded by the
  DETERMINISTIC graders in ``grading.py`` — the pinned rubric implemented
  mechanically (that is what makes rates deterministic across runs).
* ``judge_pin`` (recorded in every published baseline + receipt) =
  ``judge_why_suite_v1:<sha256>`` where the hash covers the ENTIRE protocol
  SURFACE: the static prompt file + the mechanical rubric code
  (``grading.py``) + the metrics/verdict semantics (``schema.py`` — the
  denominators, floors, and verdict comparisons a grader edit would
  silently change).  A prompt/grader/metrics/gold change is a PROTOCOL
  change: the grading pre-step asserts the on-disk digest still equals the
  pinned hash and the committed baseline's ``judge_pin`` matches the run's
  pin — a drift fails the run as INCONCLUSIVE (judge_pin_mismatch), never a
  silent compare under a different protocol.  (Corpus + gold are separately
  pinned by the ``fixtures_hash``; runner.py is harness glue and unpinned —
  deliberate: execution scaffolding cannot change grading semantics.)

Hermetic: pure file reading + hashing — no DB, no network, no LLM.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The static prompt file is the canonical protocol artifact (module-relative
# — never a hardcoded absolute path).
JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "judge_why_suite_v1.txt"
# The rubric that implements the prompt is CODE — a grader edit would change
# grading semantics under an unchanged prompt, so the code is folded into the
# same protocol digest (review P1, #2100).
_GRADER_PATH = Path(__file__).resolve().parent / "grading.py"
# aggregate_metrics (denominators/rates) + the floors/bars + compare_run
# verdict comparisons live in schema.py — same silent-drift hole, same fold.
_SCHEMA_PATH = Path(__file__).resolve().parent / "schema.py"

# Protocol name (shared with E2E-1's grading arm — the single source of
# truth for the pinned judge version).
JUDGE_PROTOCOL = "judge_why_suite_v1"


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def judge_prompt_text() -> str:
    """The full pinned prompt text (the on-disk artifact)."""
    return JUDGE_PROMPT_PATH.read_text(encoding="utf-8")


def prompt_sha256() -> str:
    """sha256 of the pinned prompt FILE (kept for drift diagnostics)."""
    return _sha256_hex(JUDGE_PROMPT_PATH.read_bytes())


def _protocol_bytes() -> bytes:
    """The bytes the protocol digest covers: prompt + rubric code + metrics
    semantics (null-separated — a same-byte boundary can never alias)."""
    return b"\x00".join(
        [JUDGE_PROMPT_PATH.read_bytes(), _GRADER_PATH.read_bytes(), _SCHEMA_PATH.read_bytes()]
    )


def protocol_sha256() -> str:
    """sha256 over the ENTIRE protocol surface (prompt + grading.py +
    schema.py) — a whitespace edit to any artifact is a protocol change."""
    return _sha256_hex(_protocol_bytes())


def judge_pin() -> str:
    """The run/baseline pin:
    ``judge_why_suite_v1:<sha256-over-(prompt+grading+schema)>``."""
    return f"{JUDGE_PROTOCOL}:{protocol_sha256()}"


def assert_prompt_pinned() -> str:
    """Grading pre-step: assert the on-disk protocol surface is PINNED.

    The committed pin constant below is the protocol anchor — if the prompt
    file OR the rubric/metrics code (grading.py / schema.py) was edited
    without re-pinning, this raises and every run stops (fail-closed: a
    drifted judge must never grade silently).  Returns the current pin on
    success.

    NOTE: when the protocol is deliberately revised, bump the protocol name
    to ``judge_why_suite_v2`` (never silently re-pin v1) and re-bless with
    ``--bless-protocol``.
    """
    expected = protocol_sha256()
    if expected != PINNED_PROTOCOL_SHA256:
        raise AssertionError(
            f"judge protocol drifted: on-disk digest {expected[:16]}… != "
            f"pinned {PINNED_PROTOCOL_SHA256[:16]}… (prompt/grading.py/"
            "schema.py edit?) — a judge-protocol change requires a NEW "
            "protocol name + --bless-protocol, never a silent edit of "
            "judge_why_suite_v1"
        )
    return judge_pin()


# ── The pinned digest (protocol anchor — do not edit without re-pinning) ───
# Regenerate with: uv run python -c \
#   "import sys; sys.path.insert(0,'tests'); from eval.why_suite import judge; print(judge.protocol_sha256())"
PINNED_PROTOCOL_SHA256 = "e71d9ecac022f7cd64ed41c17c07b6f102c7704b66b90e395544e166f8409299"
