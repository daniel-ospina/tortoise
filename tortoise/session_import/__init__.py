"""#1727 Slice 2 (Task 15) — T2 historical-session backfill parsers.

``tortoise sessions import --harness codex|claude-desktop|pi`` stages a
harness's session store into conversation turns, POSTs to hosted
``/v1/sessions`` with a deterministic idempotency key, and writes a LOCAL
receipt only on 2xx (Task 15 acceptance: 403/402/503 ⇒ fail, no receipt,
honest error; Codex + Desktop parsers idempotent on re-import).

pi REUSES the codex parser (named reuse, pinned by the plan): pi session
JSONL is a tree-structured JSONL like codex's.
"""
from __future__ import annotations

from .parsers import (
    PARSERS,
    parse_claude_desktop,
    parse_codex,
    parse_pi,
    parse_transcript,
)

__all__ = [
    "PARSERS",
    "parse_claude_desktop",
    "parse_codex",
    "parse_pi",
    "parse_transcript",
]
