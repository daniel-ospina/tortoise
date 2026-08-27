"""#1727 Slice 2 (Task 15) — T2 backfill: claude-desktop parser.

``tortoise sessions import --harness claude-desktop`` stages Claude project
JSONL (``~/.claude/projects/*/*.jsonl`` — the store Claude Desktop and
Claude Code share) into conversation turns. The record shape is
``{"message": {"role": ..., "content": <str | parts>}}`` — the same shape
session-end.sh converts, ported to the shared parser.

Assertions pin: idempotency (re-parse identical), text-block flattening,
tool/other-role noise skipping, and dispatch aliasing (CLI 'desktop' →
canonical 'claude-desktop' harness is the CLI's mapping; the parser itself
keys on the canonical name).
"""
from __future__ import annotations

import json

import pytest

from tortoise.session_import import parse_claude_desktop

_DESKTOP_LINES = [
    {"type": "user",
     "message": {"role": "user",
                 "content": [{"type": "text", "text": "Hello graph"},
                             {"type": "text", "text": " again"}]}},
    {"type": "assistant",
     "message": {"role": "assistant",
                 "content": "plain string assistant reply"}},
    {"type": "user",
     "message": {"role": "user", "content": ""}},
    {"type": "assistant",
     "message": {"role": "assistant",
                 "content": [{"type": "tool_use", "name": "bash",
                              "input": {"cmd": "ls"}}]}},
    {"type": "user",
     "message": {"role": "system", "content": "system noise"}},
    {"type": "summary", "message": {"role": "assistant", "content": "sum"}},
]

_EXPECTED = [
    {"role": "user", "content": "Hello graph again"},
    {"role": "assistant", "content": "plain string assistant reply"},
]


@pytest.fixture()
def desktop_jsonl(tmp_path):
    p = tmp_path / "session-claude.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _DESKTOP_LINES) + "\n",
                 encoding="utf-8")
    return p


def test_claude_desktop_parser_shapes(desktop_jsonl):
    """text-block parts flatten; string content passes through; tool_use /
    system / summary records never become turns."""
    turns = parse_claude_desktop(desktop_jsonl)
    assert turns == _EXPECTED, turns


def test_claude_desktop_parser_idempotent(desktop_jsonl):
    """Task 15 acceptance: re-parse yields identical turns (idempotent on
    re-import — the CLI content-hash key rides on this)."""
    assert parse_claude_desktop(desktop_jsonl) == parse_claude_desktop(desktop_jsonl)


def test_claude_desktop_parser_empty_file(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("", encoding="utf-8")
    assert parse_claude_desktop(p) == []


def test_claude_desktop_parser_broken_lines_skipped(tmp_path):
    p = tmp_path / "broken.jsonl"
    p.write_text("garbage\n" + json.dumps({"message": {"role": "user",
                                                       "content": "ok"}}),
                 encoding="utf-8")
    assert parse_claude_desktop(p) == [{"role": "user", "content": "ok"}]
