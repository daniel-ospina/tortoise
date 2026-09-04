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
  ``judge_why_suite_v1:<sha256>`` where the hash covers the ENTIRE prompt
  file.  A prompt/grader/gold change is a PROTOCOL change: the grading
  pre-step asserts the on-disk prompt hash still equals the pinned hash and
  the committed baseline's ``judge_pin`` matches the run's pin — a drift
  fails the run as INCONCLUSIVE (judge_pin_mismatch), never a silent compare
  under a different protocol.

Hermetic: pure file reading + hashing — no DB, no network, no LLM.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# The static prompt file is the canonical protocol artifact (module-relative
# — never a hardcoded absolute path).
JUDGE_PROMPT_PATH = Path(__file__).resolve().parent / "judge_why_suite_v1.txt"

# Protocol name (shared with E2E-1's grading arm — the single source of
# truth for the pinned judge version).
JUDGE_PROTOCOL = "judge_why_suite_v1"


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def judge_prompt_text() -> str:
    """The full pinned prompt text (the on-disk artifact)."""
    return JUDGE_PROMPT_PATH.read_text(encoding="utf-8")


def prompt_sha256() -> str:
    """sha256 of the pinned prompt FILE (every byte — a whitespace edit is a
    protocol change)."""
    return _sha256_hex(JUDGE_PROMPT_PATH.read_bytes())


def judge_pin() -> str:
    """The run/baseline pin: ``judge_why_suite_v1:<sha256-of-prompt-file>``."""
    return f"{JUDGE_PROTOCOL}:{prompt_sha256()}"


def assert_prompt_pinned() -> str:
    """Grading pre-step: assert the on-disk prompt is the PINNED artifact.

    The committed pin constant below is the protocol anchor — if the prompt
    file was edited without re-pinning, this raises and every run stops
    (fail-closed: a drifted judge must never grade silently).  Returns the
    current pin on success.

    NOTE: when the prompt is deliberately revised, bump the protocol name to
    ``judge_why_suite_v2`` (never silently re-pin v1) and re-bless with
    ``--bless-protocol``.
    """
    # The committed anchor (this module's own file hash — assert_self_pin
    # guarantees it matches the on-disk prompt; the baseline assertion is
    # runner-side via schema.compare_run's judge_pin guard).
    expected = _sha256_hex(JUDGE_PROMPT_PATH.read_bytes())
    if expected != PINNED_PROMPT_SHA256:
        raise AssertionError(
            f"judge prompt drifted: on-disk sha256 {expected[:16]}… != "
            f"pinned {PINNED_PROMPT_SHA256[:16]}… — a judge-protocol change "
            "requires a NEW protocol name + --bless-protocol, never a silent "
            "edit of judge_why_suite_v1"
        )
    return judge_pin()


# ── The pinned hash (protocol anchor — do not edit without re-pinning) ─────
# Regenerate with: uv run python -c \
#   "import hashlib,pathlib;print(hashlib.sha256(pathlib.Path('tests/eval/why_suite/judge_why_suite_v1.txt').read_bytes()).hexdigest())"
PINNED_PROMPT_SHA256 = "3c7c124b109942aa2d58b01794e95ebad42ceff83a42a2a3ae4f6a4217d5b511"
