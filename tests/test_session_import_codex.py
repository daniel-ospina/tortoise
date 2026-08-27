"""#1727 Slice 2 (Task 15) — T2 backfill: codex + pi session-store parsers.

``tortoise sessions import --harness codex|pi`` stages Codex CLI session
JSONL (and pi's tree-structured JSONL — which REUSES the codex parser,
named reuse per the plan) into conversation turns and POSTs to hosted
/v1/sessions.

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


def test_pi_reuses_codex_parser(codex_jsonl):
    """Plan P2 Task 15: pi REUSES the codex parser (named reuse) — pi
    session JSONL is a tree-structured JSONL like codex's. The dispatch
    table maps 'pi' → parse_pi → parse_codex (same object, same turns)."""
    assert parse_pi(codex_jsonl) == _EXPECTED_TURNS
    # named reuse — the SAME callable, not a divergent copy
    from tortoise.session_import import PARSERS
    assert PARSERS["pi"] is PARSERS["codex"]
    assert PARSERS["pi"] is parse_pi


def test_pi_parser_idempotent(codex_jsonl):
    """The pi path inherits the parser idempotency property."""
    assert parse_pi(codex_jsonl) == parse_pi(codex_jsonl)


def test_parse_transcript_dispatch(codex_jsonl):
    """parse_transcript dispatches on the harness name; unknown harnesses
    raise ValueError (the CLI surfaces an honest parse failure, never a
    silent no-op)."""
    assert parse_transcript(str(codex_jsonl), "codex") == _EXPECTED_TURNS
    assert parse_transcript(str(codex_jsonl), "pi") == _EXPECTED_TURNS
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
