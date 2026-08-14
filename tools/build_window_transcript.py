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
import sys
from pathlib import Path

_WS = re.compile(r"\s+")


def load_session(path: Path, session_id: str) -> dict:
    """Return the session record matching session_id (one record per session)."""
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("session_id") == session_id:
                return record
    raise SystemExit(f"session {session_id} not found in {path}")


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


if __name__ == "__main__":
    raise SystemExit(main())
