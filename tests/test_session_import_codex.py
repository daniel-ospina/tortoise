"""#1727 Slice 2 (Task 15) — T2 backfill: codex + pi session-store parsers.

``tortoise sessions import --harness codex|pi`` stages Codex CLI session
JSONL and Pi session JSONL (each in its OWN record shape — #3667 removed the
codex-parser alias that returned 0 turns for real Pi sessions) into
conversation turns and POSTs to hosted /v1/sessions.

Assertions here pin: parser idempotency (re-parse of the same file yields
byte-identical turns), record-shape tolerance (response_item / legacy
message types), content-part flattening (input_text/output_text), and
non-message noise skipping (tool calls, system prompts never become turns).
"""
from __future__ import annotations

import json

import pytest

from tortoise.session_import import parse_codex, parse_pi, parse_transcript

# A minimal codex-shaped session file exercising every record shape the
# parser must handle: response_item messages with part-array content,
# legacy user/assistant_message records with string content, and noise
# records (tool calls, system) that must be skipped.
_CODEX_LINES = [
    {"type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text",
                              "text": "Let's ship the memory slice."}]}},
    {"type": "response_item",
     "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text",
                              "text": "Agreed — the consent gate is the P0."},
                             {"type": "output_text",
                              "text": "Receipts are 2xx-only."}]}},
    {"type": "response_item",
     "payload": {"type": "function_call", "name": "bash",
                 "arguments": "ls"}},
    {"type": "response_item",
     "payload": {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": ""}]}},
    {"type": "user_message", "content": "legacy user turn"},
    {"type": "assistant_message",
     "payload": {"content": "legacy assistant turn"}},
    {"type": "system", "payload": {"type": "message", "role": "system",
                                   "content": "you are helpful"}},
    {"type": "event_msg", "payload": {"type": "item_updated",
                                      "item_id": "x"}},
]

_EXPECTED_TURNS = [
    {"role": "user", "content": "Let's ship the memory slice."},
    {"role": "assistant",
     "content": "Agreed — the consent gate is the P0. "
                "Receipts are 2xx-only."},
    {"role": "user", "content": "legacy user turn"},
    {"role": "assistant", "content": "legacy assistant turn"},
]


@pytest.fixture()
def codex_jsonl(tmp_path):
    p = tmp_path / "session-codex.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _CODEX_LINES) + "\n",
                 encoding="utf-8")
    return p


# A REAL Pi session file shape (#3667): the record type is the literal
# "message" and the role/content live in the `message` sub-object. Session
# header, model_change and thinking_level_change entries are noise; thinking /
# toolCall content parts are skipped by the text flattener.
_PI_LINES = [
    {"type": "session", "version": 3, "id": "h", "cwd": "/repo",
     "timestamp": "2026-01-01T00:00:00Z"},
    {"type": "model_change", "id": "m1", "parentId": None,
     "provider": "deepseek", "modelId": "deepseek-v4-flash",
     "timestamp": "2026-01-01T00:00:01Z"},
    {"type": "message", "id": "m2", "parentId": "m1",
     "timestamp": "2026-01-01T00:00:02Z",
     "message": {"role": "user", "content": [
         {"type": "text", "text": "Ship the capture seam."}]}},
    {"type": "message", "id": "m3", "parentId": "m2",
     "timestamp": "2026-01-01T00:00:03Z",
     "message": {"role": "assistant", "content": [
         {"type": "thinking", "thinking": "internal — never a turn"},
         {"type": "text", "text": "On it."},
         {"type": "toolCall", "id": "t1", "name": "bash",
          "arguments": {"command": "ls"}}]}},
    {"type": "message", "id": "m4", "parentId": "m3",
     "timestamp": "2026-01-01T00:00:04Z",
     "message": {"role": "toolResult",
                 "content": [{"type": "text", "text": "noise"}]}},
    {"type": "message", "id": "m5", "parentId": "m4",
     "timestamp": "2026-01-01T00:00:05Z",
     "message": {"role": "user", "content": "plain-string content"}},
    {"type": "message", "id": "m6", "parentId": "m5",
     "timestamp": "2026-01-01T00:00:06Z",
     "message": {"role": "assistant", "content": []}},
]

_PI_EXPECTED = [
    {"role": "user", "content": "Ship the capture seam."},
    {"role": "assistant", "content": "On it."},
    {"role": "user", "content": "plain-string content"},
]


@pytest.fixture()
def pi_jsonl(tmp_path):
    p = tmp_path / "2026-01-01T00-00-00-000Z_abc.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in _PI_LINES) + "\n",
                 encoding="utf-8")
    return p


def test_codex_parser_shapes(codex_jsonl):
    """response_item + legacy message shapes flatten to turns; noise
    (function_call, empty content, system, item_updated) is skipped."""
    turns = parse_codex(codex_jsonl)
    assert turns == _EXPECTED_TURNS, turns


def test_codex_parser_idempotent(codex_jsonl):
    """Task 15 acceptance: the parser is a pure function of the file —
    re-parse yields byte-identical turns (the CLI's content-hash idempotency
    key rides on this)."""
    assert parse_codex(codex_jsonl) == parse_codex(codex_jsonl)


def test_pi_parser_reads_real_pi_session_shape(pi_jsonl):
    """#3667: Pi's OWN record shape (type == "message" → message.role /
    message.content). The codex alias this replaced matched codex's `payload`
    shape and returned 0 turns on every real Pi session."""
    assert parse_pi(pi_jsonl) == _PI_EXPECTED


def test_pi_and_codex_are_distinct_parsers():
    """#3667: the named reuse was the bug — each harness shape has its own
    branch, and the dispatch table points at it."""
    from tortoise.session_import import PARSERS
    assert PARSERS["pi"] is parse_pi
    assert PARSERS["pi"] is not PARSERS["codex"]


def test_pi_parser_idempotent(pi_jsonl):
    """The pi path keeps the parser idempotency property."""
    assert parse_pi(pi_jsonl) == parse_pi(pi_jsonl)


def test_pi_parser_rejects_codex_shape(codex_jsonl):
    """The shapes are genuinely different — a codex-shaped file is NOT a Pi
    session, so the pi parser yields nothing for it (the inverse of the bug:
    the alias treated one shape as the other)."""
    assert parse_pi(codex_jsonl) == []


def test_parse_transcript_dispatch(codex_jsonl, pi_jsonl):
    """parse_transcript dispatches on the harness name; unknown harnesses
    raise ValueError (the CLI surfaces an honest parse failure, never a
    silent no-op)."""
    assert parse_transcript(str(codex_jsonl), "codex") == _EXPECTED_TURNS
    assert parse_transcript(str(pi_jsonl), "pi") == _PI_EXPECTED
    with pytest.raises(ValueError, match="no parser"):
        parse_transcript(str(codex_jsonl), "cursor")


def test_codex_parser_broken_lines_skipped(tmp_path):
    """A malformed line must not fail the whole backfill — skipped and
    logged, valid lines still parse (T1-P16-style tolerance)."""
    p = tmp_path / "broken.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps({"type": "user_message", "content": "still fine"})
        + "\n",
        encoding="utf-8")
    turns = parse_codex(p)
    assert turns == [{"role": "user", "content": "still fine"}]


def test_codex_parser_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="cannot read"):
        parse_codex(tmp_path / "nope.jsonl")
