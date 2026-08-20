#!/usr/bin/env python3
"""build_window_transcript — emit an utterance-tagged transcript from the pi
session-events capture (epic #909 window #2 gate, issue #946).

Reads the session-events JSONL (one JSON record per session:
{session_id, conversation[], metadata}) and emits the harness transcript
format ('<index>: <role>: <text>' — one EDU per line, per the 1-turn-1-EDU
segmentation rule) for the named session.

Newline/whitespace sanitization is MANDATORY: the session-events capture
contains embedded newlines in turn content (the #960 user turn has 19) and
the harness parse_transcript hard-errors on multi-line EDUs. Each turn's
content is collapsed to a single line before emission.

Usage:
    python tools/build_window_transcript.py --session 019ff63b-9eef-7f89-a572-308590e3b0e0 \
        --events ~/.tortoise/session-events/2026-08-12.jsonl \
        --out tests/eval/w2-960/transcript.txt
"""
from __future__ import annotations

import argparse
import json
import re
import sys  # noqa: F401
from pathlib import Path

_WS = re.compile(r"\s+")


def load_session(path: Path, session_id: str) -> dict:
    """Return the session record matching session_id (one record per session).

    Hard-fails on duplicate session_ids so a re-captured/resumed session can never
    silently produce a transcript from the wrong conversation (review P2, PR #1259).
    """
    matches = []
    with path.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("session_id") == session_id:
                matches.append((lineno, record))
    if not matches:
        raise SystemExit(f"session {session_id} not found in {path}")
    if len(matches) > 1:
        lines = ", ".join(str(ln) for ln, _ in matches)
        raise SystemExit(
            f"session {session_id} found {len(matches)} times in {path} "
            f"(lines {lines}) — ambiguous; refusing to pick the first"
        )
    return matches[0][1]


def sanitize(text: str) -> str:
    """Collapse all whitespace runs (incl. newlines) to single spaces, strip."""
    return _WS.sub(" ", text).strip()


def build(record: dict) -> list[str]:
    """One EDU per conversation turn, '<index>: <role>: <text>'."""
    conversation = record.get("conversation", [])
    if not isinstance(conversation, list) or not conversation:
        raise SystemExit("session record has no non-empty 'conversation' array")
    lines: list[str] = []
    for i, turn in enumerate(conversation):
        role = turn.get("role")
        content = turn.get("content", "")
        if role not in ("user", "assistant"):
            raise SystemExit(f"turn {i}: unexpected role {role!r}")
        if not isinstance(content, str):
            raise SystemExit(f"turn {i}: content is {type(content).__name__}, not str")
        text = sanitize(content)
        if not text:
            raise SystemExit(f"turn {i}: empty content after sanitization")
        _check_secrets(text, i)
        lines.append(f"{i}: {role}: {text}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_window_transcript",
        description="Emit an utterance-tagged window transcript from a pi "
        "session-events capture (harness format '<index>: <role>: <text>').",
    )
    parser.add_argument("--session", required=True, help="session_id to extract")
    parser.add_argument("--events", required=True, help="session-events JSONL path "
                        "(e.g. ~/.tortoise/session-events/2026-08-12.jsonl)")
    parser.add_argument("--out", required=True, help="transcript output path")
    args = parser.parse_args(argv)

    record = load_session(Path(args.events).expanduser(), args.session)
    lines = build(record)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(lines)} EDUs -> {out}")
    return 0


# Secret patterns — transcripts derive from the full agent perceptual stream and may be
# committed to a public repo; this is a BEST-EFFORT guard, not a guarantee — redaction is
# the operator's responsibility (SEC review, PR #1259). Ordered (regex, replacement-or-None):
# None means hard-fail with a line reference. Coverage: OpenAI/Anthropic/Stripe/GitHub
# (classic + fine-grained)/AWS/bearer/private-key; JWT, Google, Slack shapes are out of scope.
_SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{32,}"), None),           # OpenAI/Anthropic-style keys (>=32 tail; >=16 would FP on skill ids like sk-workflow-standard)
    (re.compile(r"sk_live_[A-Za-z0-9]{16,}"), None),        # Stripe secret key
    (re.compile(r"rk_live_[A-Za-z0-9]{16,}"), None),        # Stripe restricted key
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), None),            # GitHub classic PAT
    (re.compile(r"github_pat_[A-Za-z0-9_]{30,}"), None),    # GitHub fine-grained PAT
    (re.compile(r"gh[osu]_[A-Za-z0-9]{20,}"), None),        # GitHub org/user/SSH tokens
    (re.compile(r"AKIA[0-9A-Z]{16}"), None),                # AWS access key id
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"), None),  # bearer tokens
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), None),
]


def _check_secrets(text: str, edu_index: int) -> None:
    """Hard-fail on secret patterns so a credential never lands in a committed transcript."""
    for pat, _replacement in _SECRET_PATTERNS:
        m = pat.search(text)
        if m:
            raise SystemExit(
                f"turn {edu_index}: possible secret pattern {m.group(0)[:16]!r}... "
                f"({pat.pattern}) — redact before building the transcript"
            )


if __name__ == "__main__":
    raise SystemExit(main())
