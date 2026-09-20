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
from types import SimpleNamespace
from unittest import mock

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
    # ``cursor`` is a real harness now (#3819) — this uses one with no parser.
    with pytest.raises(ValueError, match="no parser"):
        parse_transcript(str(codex_jsonl), "vim")


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


# ── #3575 P1-A: the backfill leg must apply the SAME window the live Pi capture
# extension applies, and it must be the bound the HANDLER enforces
# (`MAX_SESSION_TURNS = 500`, tortoise/quota.py) — NOT the Pydantic
# `SessionRequest.conversation` max_length=1000. The Pydantic boundary only
# decides whether the model accepts the body; the handler then raises HTTP 400
# above 500, so a 501–1000-turn payload passes `SessionRequest(...)` and still
# writes NO receipt. Measured on the real local Pi corpus (2026-09, 374 files
# with >=1 turn): 45 files (12.0%) exceed 500 turns; 21 (5.6%) exceed 1000
# (max 2555).


def _pi_turns_file(tmp_path, n: int):
    lines = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        lines.append(json.dumps({
            "type": "message",
            "message": {"role": role, "content": f"turn {i}"},
        }))
    p = tmp_path / f"pi-{n}-turns.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_sessions_import_windows_to_last_max_session_turns(tmp_path, monkeypatch, capsys):
    """A >MAX_SESSION_TURNS session POSTs the LAST MAX_SESSION_TURNS turns —
    the payload clears the HANDLER cap, not merely the Pydantic boundary, and
    the truncation is reported."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.hosted_api import SessionRequest
    from tortoise.quota import MAX_SESSION_TURNS
    from tortoise.session_import import MAX_TURNS

    # The client bound MUST mirror the bound the handler enforces
    # (tortoise/quota.py::MAX_SESSION_TURNS), never the Pydantic max_length.
    assert MAX_TURNS == MAX_SESSION_TURNS, (
        "the window must mirror the HANDLER cap (tortoise/quota.py), not "
        "SessionRequest.conversation's max_length"
    )

    p = _pi_turns_file(tmp_path, 1005)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-1"}'

    def _fake_urlopen(req, timeout=None):
        captured["conversation"] = json.loads(req.data.decode())["conversation"]
        return _Resp()

    args = SimpleNamespace(file=str(p), harness="pi", session_id=None)
    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        rc = _cmd_sessions_import(args)

    assert rc == 0
    conv = captured["conversation"]
    assert len(conv) == MAX_TURNS
    assert len(conv) <= MAX_SESSION_TURNS, (
        f"window kept {len(conv)} > handler cap {MAX_SESSION_TURNS} — the "
        "real route 400s and no receipt is written"
    )
    # the LAST turns win — recent context is what memory wants
    assert conv[0]["content"] == "turn 505"
    assert conv[-1]["content"] == "turn 1004"
    # clears the Pydantic boundary — necessary but NOT sufficient (the handler
    # cap above is the one that actually refuses; the real-route 2xx is pinned
    # by tests/test_hosted_api.py::test_backfill_window_lands_below_the_handler_cap)
    SessionRequest(conversation=conv)
    # ...and the receipt is written for what was actually sent
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text())
    assert receipt["turns"] == MAX_TURNS
    # never a silent drop
    err = capsys.readouterr().err.lower()
    assert "truncat" in err and "505 older turns dropped" in err


def test_sessions_import_window_is_a_noop_at_or_below_the_limit(tmp_path, monkeypatch, capsys):
    """A session of exactly MAX_TURNS is sent whole and reports no truncation."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.session_import import MAX_TURNS

    p = _pi_turns_file(tmp_path, MAX_TURNS)
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    captured: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"session_id": "s-2"}'

    def _fake_urlopen(req, timeout=None):
        captured["conversation"] = json.loads(req.data.decode())["conversation"]
        return _Resp()

    args = SimpleNamespace(file=str(p), harness="pi", session_id=None)
    with mock.patch("urllib.request.urlopen", _fake_urlopen):
        rc = _cmd_sessions_import(args)

    assert rc == 0
    assert len(captured["conversation"]) == MAX_TURNS
    assert captured["conversation"][0]["content"] == "turn 0"
    assert "truncat" not in capsys.readouterr().err.lower()


def test_sessions_import_defers_on_extraction_disabled(tmp_path, monkeypatch, capsys):
    """#4258: a store-only 2xx from the team's `capture_extract` OFF setting must
    NOT write a local "imported" receipt — the SAME DEFERRED contract #4188
    established for the keyless mode. A receipt would make every later
    re-import skip the POST (`receipt.exists()` → 0), so the session could never
    gain memory points after extraction is turned back on — even though the
    server left it retry-eligible (capture_ok=False, lane "none").

    The control (a keyed `llm:*` 2xx) DOES write the receipt, so the assertion
    discriminates rather than merely observing an empty dir.
    """
    from tortoise.__main__ import _cmd_sessions_import

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    def _run(path, mode):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({
                    "session_id": "s-" + mode,
                    "extraction_mode": mode,
                    "warnings": [],
                }).encode()

        args = SimpleNamespace(file=str(path), harness="pi", session_id=None)
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
            return _cmd_sessions_import(args)

    # store-only (extraction turned OFF): deferred → NO local receipt
    assert _run(_pi_turns_file(tmp_path, 3), "extraction-disabled") == 0
    assert list((tmp_path / "receipts").glob("*.json")) == [], (
        "a deferred (store-only) import must not write a receipt — it would "
        "make every later re-import a no-op")
    err = capsys.readouterr().err.lower()
    assert "deferred" in err and "turned off" in err

    # control: a keyed extraction 2xx DOES write the receipt
    assert _run(_pi_turns_file(tmp_path, 2), "llm:mock") == 0
    assert len(list((tmp_path / "receipts").glob("*.json"))) == 1, (
        "the keyed control must write a receipt — otherwise the empty dir "
        "above proves nothing")
