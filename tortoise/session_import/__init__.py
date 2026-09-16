"""#1727 Slice 2 (Task 15) — T2 historical-session backfill parsers.

``tortoise sessions import --harness codex|claude-desktop|pi`` stages a
harness's session store into conversation turns, POSTs to hosted
``/v1/sessions`` with a deterministic idempotency key, and writes a LOCAL
receipt only on 2xx (Task 15 acceptance: 403/402/503 ⇒ fail, no receipt,
honest error; Codex + Desktop parsers idempotent on re-import).

Each harness has its own record shape (#3667): pi's
``{"type": "message", "message": {"role": ..., "content": [...]}}`` is NOT
codex's ``payload`` shape — the codex-parser alias it replaced returned 0
turns for every real Pi session.
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
