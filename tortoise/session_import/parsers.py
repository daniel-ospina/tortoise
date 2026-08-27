"""#1727 Slice 2 (Task 15) — per-harness session-store parsers (T2 backfill).

Each parser reads a harness's session file and returns the canonical
conversation shape POSTed to ``/v1/sessions``:

    [{"role": "user" | "assistant", "content": "<text>"}, ...]

Parsers are PURE functions over the file — no network, no writes — so a
re-parse of the same file is byte-identical (idempotency is a property of
the parser; the CLI's content-hash idempotency key + 2xx-only local receipt
are the idempotency/durability layer on top).

Supported stores:
  - ``codex``: Codex CLI session JSONL (``~/.codex/sessions/*.jsonl``).
    Records are ``{"type": "response_item", "payload": {"type": "message",
    "role": ..., "content": [{"type": "input_text"/"output_text", "text":
    ...}]}}``; legacy shapes (``user_message`` / ``assistant_message`` with a
    string ``content``) are tolerated. Tool calls / results are skipped.
  - ``pi``: REUSES the codex parser (named reuse, plan P2 Task 15) — pi
    session JSONL is a tree-structured JSONL like codex's, so the same
    record-shape walk applies.
  - ``claude-desktop``: Claude project JSONL (``~/.claude/projects/*/
    *.jsonl`` — the same store Claude Desktop and Claude Code share).
    Records are ``{"message": {"role": ..., "content": <str | parts>}}`` —
    the same shape session-end.sh converts, ported to a shared parser.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

_logger = logging.getLogger("tortoise.session_import")

# Roles we keep. Anything else (system, tool, function, model-rollout,
# …) is context noise for the capture surface — skipped, never coerced.
_KEEP_ROLES = {"user", "assistant"}


def _text_from_parts(parts) -> str:
    """Flatten an LLM content array (codex input_text/output_text, Claude
    text blocks) into one string. Non-text parts are skipped."""
    if isinstance(parts, str):
        return parts
    if not isinstance(parts, list):
        return ""
    out = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = (part.get("text") or part.get("input_text")
                or part.get("output_text"))
        if text:
            out.append(str(text).strip())
    return " ".join(t for t in out if t).strip()


def _walk_codex_records(path: Path, *, role_key: str) -> list[dict]:
    """Shared JSONL walk for codex-shaped stores.

    ``role_key`` selects where the message role lives (codex: the payload's
    ``type == "message"`` record; claude-desktop: the ``message`` sub-object).
    Tolerant of malformed lines (skipped, logged at debug) — a single broken
    line must not fail the whole backfill.
    """
    turns: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"cannot read session file {path}: {e}") from e
    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            _logger.debug("skip unparsable line %d in %s", lineno, path)
            continue
        role, content = _role_content(rec, role_key)
        if role not in _KEEP_ROLES:
            continue
        text = _text_from_parts(content)
        if not text:
            continue
        turns.append({"role": role, "content": text})
    return turns


def _role_content(rec: dict, role_key: str) -> tuple[str | None, object]:
    """Extract (role, content) from one record.

    codex (role_key="payload"): ``{"type": "response_item", "payload":
    {"type": "message", "role": ..., "content": ...}}``; legacy
    ``{"type": "user_message", "content": ...}`` / ``{"type":
    "assistant_message", ...}`` carry the role in the record type.

    claude-desktop (role_key="message"): ``{"message": {"role": ...,
    "content": ...}}``.
    """
    if role_key == "message":
        # Claude store: the record-level `type` is authoritative when
        # present — only user/assistant records become turns (summary,
        # system, tool-result records are noise). Type ABSENT ⇒ trust the
        # message role (tolerant of minimal hand-written fixtures).
        rtype = rec.get("type") or ""
        if rtype and rtype not in ("user", "assistant"):
            return None, None
        msg = rec.get("message") or {}
        return msg.get("role"), msg.get("content")
    # codex path
    rtype = rec.get("type") or ""
    if rtype == "response_item":
        payload = rec.get("payload") or {}
        if payload.get("type") != "message":
            return None, None
        return payload.get("role"), payload.get("content")
    if rtype in ("user_message", "assistant_message"):
        role = rtype.split("_")[0]
        payload = rec.get("payload")
        content = rec.get("content")
        if isinstance(payload, dict) and "content" in payload:
            content = payload["content"]
        return role, content
    return None, None


def parse_codex(path: str | Path) -> list[dict]:
    """Parse a Codex CLI session JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="payload")


# NAMED REUSE of the codex parser (plan P2 Task 15): pi session JSONL is a
# tree-structured JSONL like codex's — the same parser, ALIASED (not a
# divergent copy), so idempotency and shape tolerance are inherited. If the
# pi store ever diverges, split a dedicated pi.py parser here (the CLI
# dispatch in PARSERS is the single seam).
parse_pi = parse_codex


def parse_claude_desktop(path: str | Path) -> list[dict]:
    """Parse a Claude (Desktop/Code) project JSONL into conversation turns."""
    return _walk_codex_records(Path(path), role_key="message")


PARSERS: dict[str, Callable[[str | Path], list[dict]]] = {
    "codex": parse_codex,
    "claude-desktop": parse_claude_desktop,
    "pi": parse_pi,
}


def parse_transcript(path: str | Path, harness: str) -> list[dict]:
    """Dispatch a session file to its harness parser.

    Raises ValueError for an unknown harness (the CLI surfaces it as an
    honest parse failure — never a silent no-op).
    """
    parser = PARSERS.get(harness)
    if parser is None:
        raise ValueError(
            f"no parser for harness {harness!r} "
            f"(supported: {sorted(PARSERS)})")
    return parser(path)
