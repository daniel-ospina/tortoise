"""#1727 Slice 2 (Task 15) — T2 historical-session backfill parsers.

``tortoise sessions import --harness codex|claude-desktop|cursor|pi`` stages a
harness's session store into conversation turns, POSTs to hosted
``/v1/sessions`` with a deterministic idempotency key, and writes a LOCAL
receipt only on 2xx (Task 15 acceptance: 403 ⇒ fail, no receipt, honest error;
a retryable refusal — 402/408/425/429/5xx, including 503 — is SPOOLED for a
later drain by ``_spool_if_retryable``, #4714 — the import still exits non-zero
and writes no receipt; Codex + Desktop parsers idempotent on re-import).

Each harness has its own record shape (#3667): pi's
``{"type": "message", "message": {"role": ..., "content": [...]}}`` is NOT
codex's ``payload`` shape — the codex-parser alias it replaced returned 0
turns for every real Pi session.
"""
from __future__ import annotations

from .parsers import (
    MAX_TURNS,
    PARSERS,
    parse_claude_desktop,
    parse_codex,
    parse_cursor,
    parse_pi,
    parse_transcript,
    window_turns,
)

__all__ = [
    "MAX_TURNS",
    "PARSERS",
    "parse_claude_desktop",
    "parse_codex",
    "parse_cursor",
    "parse_pi",
    "parse_transcript",
    "window_turns",
]
