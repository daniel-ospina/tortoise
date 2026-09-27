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

import io
import json
import ssl
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError, URLError

import pytest

from tortoise.capture_spool import PostOutcome as PostOutcome
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
    # #3615: capture is gated on EXPLICIT consent — a credential is not consent.
    # This test exercises the real import path, so opt in.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
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
    # #3615: capture is gated on EXPLICIT consent — a credential is not consent.
    # This test exercises the real import path, so opt in.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
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
    server left it retry-eligible (capture_ok=False, lane "disabled"; "none"
    is the keyless sibling).

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


def test_sessions_import_deferred_remedy_names_both_levers(tmp_path, monkeypatch, capsys):
    """#4258 + #3892: when the receipt is BOTH keyless AND extraction-disabled,
    the deferred remedy must name both levers. Naming only the missing key
    would send the user to configure a provider that still would not extract
    while the team's `capture_extract` setting is OFF — the same wrong-lever
    defect the review flagged (a remedy that cannot fix the state it names).
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.sdk import _CAPTURE_EXTRACTION_DISABLED_WARNING

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            # the no-provider branch wins the MODE, but the server still adds
            # the extraction-disabled warning additively (#4258).
            return json.dumps({
                "session_id": "s-both",
                "extraction_mode": "no-provider",
                "warnings": ["no LLM provider key",
                             _CAPTURE_EXTRACTION_DISABLED_WARNING],
            }).encode()

    args = SimpleNamespace(file=str(_pi_turns_file(tmp_path, 3)),
                           harness="pi", session_id=None)
    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        assert _cmd_sessions_import(args) == 0
    assert list((tmp_path / "receipts").glob("*.json")) == [], (
        "a deferred import writes no receipt")
    err = capsys.readouterr().err.lower()
    assert "no llm provider key" in err and "turned off" in err, err
    assert "key is configured and extraction is turned back on" in err, (
        "the remedy must name BOTH levers, not just the missing key: " + err)


def test_sessions_import_defers_on_upgrade_refused_replay(tmp_path, monkeypatch, capsys):
    """#4258/#4188: on the non-convergent M2 lane the server REPLAYS a FAILED
    store-only prior — mode ``replayed`` + an upgrade-refused warning — which is
    STILL "no extraction ever ran". The CLI must treat that as DEFERRED (no
    local receipt), or every later re-import skips the POST on a stale receipt
    and the session can never gain memory points, making the warning's own
    remedy unreachable. Covers BOTH the keyless (pre-existing) and the
    setting-disabled (#4258) warning shapes.
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.sdk import (
        _CAPTURE_EXTRACTION_DISABLED_UPGRADE_REFUSED_WARNING,
        _CAPTURE_KEYLESS_UPGRADE_REFUSED_WARNING,
    )

    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    def _run(warning):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({
                    "session_id": "s-replay",
                    "extraction_mode": "replayed",
                    "warnings": [
                        "session already captured (same session_id) — no "
                        "new extraction",
                        warning,
                    ],
                }).encode()

        args = SimpleNamespace(file=str(_pi_turns_file(tmp_path, 3)),
                               harness="pi", session_id=None)
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: _Resp()):
            assert _cmd_sessions_import(args) == 0
        capsys.readouterr()

    # the setting-disabled variant (new in #4258) …
    _run(_CAPTURE_EXTRACTION_DISABLED_UPGRADE_REFUSED_WARNING)
    assert list((tmp_path / "receipts").glob("*.json")) == [], (
        "a replayed store-only receipt must not write a local receipt")
    # … and the keyless variant (the pre-existing shape of the same hole).
    _run(_CAPTURE_KEYLESS_UPGRADE_REFUSED_WARNING)
    assert list((tmp_path / "receipts").glob("*.json")) == []


# ── #4714: a RETRYABLE import refusal must land in the DURABLE SPOOL ────────
#
# The server's capture guard REFUSES rather than enqueues, and it advertises
# the retry (`Retry-After`) — so a 504 (wait budget exceeded) / 429 (capture
# capacity saturated) is not a rejection of the CONTENT, it is a deferral. The
# POST reached the server; there is no server-side copy to fall back on, so the
# parsed turns must survive in the same durable spool the claude/pi legs write
# BEFORE their POST. Without this the session is silently lost — the measured
# defect for codex (504) and cursor (429).


def _http_error(code: int, body: str) -> HTTPError:
    """An HTTPError carrying a REAL body through a file object (``e.fp``).

    ``_cmd_sessions_import`` reads ``e.read()`` for the detail it prints and
    records, so a body-less error would exercise a branch the server never
    takes.
    """
    return HTTPError(
        "https://api.tortoise.test/v1/sessions", code, "refused",
        hdrs=None, fp=io.BytesIO(body.encode("utf-8")),
    )


def _import_env(tmp_path, monkeypatch):
    """Hermetic import environment: no network, no HOME, a tmp spool+receipts."""
    from tortoise.capture_spool import spool_dir

    spool = tmp_path / "spool"
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    # #3615: `sessions import` TRANSMITS and is consent-gated. This helper is
    # the shared env for the import-path tests, none of which pin the refusal
    # (that lives in tests/test_capture_consent.py), so they all opt in.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(spool))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # POSITIVE CONTROL: a broken override would silently target the real spool.
    assert spool_dir() == spool
    return spool


class _Resp(io.BytesIO):
    """A minimal context-manager response for the patched transport."""

    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _post_refused_get_serves(session_id: str, turns: list[dict], *,
                            extracted: int = 2, body: str =
                            '{"detail":"The server\'s wait budget was exceeded"}',
                            get_404: bool = False, post_exc=None):
    """A transport where the POST is refused and the confirming READ answers
    with the session the abandoned handler went on to write.

    The rows are the shape the server actually RETURNS: `role` as its own field
    and `content` with the `[role] ` prefix STRIPPED (`get_session_detail`).
    Built from the original turn dicts, never by echoing the writer's
    `"[role] text"` string — a fake that echoes the writer hides a
    format-mismatched comparison, which is how this confirmation was inert on
    its first attempt while these tests stayed green.
    """

    def _open(req, timeout=None):
        if getattr(req, "get_method", lambda: "GET")() == "POST":
            if post_exc is not None:
                raise post_exc
            raise _http_error(504, body)
        if get_404:
            raise _http_error(404, '{"detail":"Session not found"}')
        detail = {
            "id": session_id, "created_at": "2026-09-23T08:21:06Z",
            "turns": len(turns), "extracted": extracted,
            "turn_points": [{"id": f"{session_id}_t{i}",
                             "role": turns[i]["role"],
                             "content": turns[i]["content"]}
                            for i in range(len(turns))],
            "source": {"url": f"session:{session_id}"},
        }
        return _Resp(json.dumps(detail).encode("utf-8"))
    return _open


def test_a_post_commit_504_writes_the_receipt_and_is_not_spooled(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """#4675: the 504 arrived AFTER the commit, so the honest verdict is
    'imported', not 'failed'.

    The transport bound ABANDONS its handler rather than cancelling it, so the
    POST's work completes server-side while the client is told it was refused.
    Reporting that as a failure parks the turns in the spool AND tells the user
    a capture that already landed will retry — and with no drain wired for
    codex it would never be filed.

    MUTATION THAT REDS THIS: drop the `_confirm_already_captured()` call from
    the HTTPError branch (or the receipt write inside it) — rc is 1 and no
    receipt is written.
    """
    from tortoise.__main__ import _cmd_sessions_import

    spool = _import_env(tmp_path, monkeypatch)
    n = len(_EXPECTED_TURNS)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _post_refused_get_serves("sid-committed", _EXPECTED_TURNS))

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-committed"))

    assert rc == 0, capsys.readouterr().err
    receipts = list((tmp_path / "receipts").glob("*.json"))
    assert [p.name for p in receipts] == ["sid-committed.json"], receipts
    record = json.loads(receipts[0].read_text())
    assert record["harness"] == "codex"
    assert record["turns"] == n
    assert record["confirmed_after_refusal"] is True
    assert "committed it" in capsys.readouterr().err
    # Nothing is parked: the turns are already on the server.
    from tortoise.capture_spool import read_spool_meta
    assert read_spool_meta(spool, "sid-committed") is None


def test_a_post_commit_504_without_extraction_still_spools(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """#4188 is not regressed. A keyless capture stores the turns and SKIPS
    extraction; writing the local 'imported' receipt for it would make every
    later explicit re-import a no-op, so the session could never gain memory
    points once a key appears. Durable turns with no extraction therefore still
    defer to the spool.

    MUTATION THAT REDS THIS: accept UNEXTRACTED in `_confirm_already_captured`
    — a receipt appears and the session can never be re-extracted.
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _post_refused_get_serves("sid-keyless", _EXPECTED_TURNS, extracted=0))

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-keyless"))

    assert rc == 1
    assert not list((tmp_path / "receipts").glob("*.json")), (
        "a capture whose extraction never ran must stay re-importable")
    assert read_spool_meta(spool, "sid-keyless") is not None, (
        "the turns must survive for the next drain")


def test_a_504_whose_session_never_appears_still_spools(
        tmp_path, monkeypatch, codex_jsonl):
    """A genuinely pre-commit refusal keeps today's behaviour exactly: an
    honest failure and a durable spool entry. The confirmation must not turn
    'I could not tell' into 'it committed'.

    MUTATION THAT REDS THIS: treat a 404 (or any inconclusive read) as filed.
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _post_refused_get_serves("sid-never", _EXPECTED_TURNS, get_404=True))

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-never"))

    assert rc == 1
    assert not list((tmp_path / "receipts").glob("*.json"))
    assert read_spool_meta(spool, "sid-never") is not None


def test_an_unreachable_api_that_still_committed_writes_the_receipt(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """The URLError branch gets the SAME confirmation. A read timeout or a
    dropped connection after the server received the POST is exactly the
    post-commit shape, and today it parks the turns and reports a failure.

    MUTATION THAT REDS THIS: drop `_confirm_already_captured(None, ...)` from
    the URLError branch.
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _post_refused_get_serves("sid-offline", _EXPECTED_TURNS,
                                 post_exc=URLError("Connection refused")))

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-offline"))

    assert rc == 0, capsys.readouterr().err
    assert [p.name for p in (tmp_path / "receipts").glob("*.json")] == \
        ["sid-offline.json"]
    assert read_spool_meta(spool, "sid-offline") is None


def test_a_402_whose_session_landed_writes_no_receipt_but_keeps_the_spool(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """402 is `retry` to the CLASSIFIER (#4614: a quota refusal is transient —
    its `est` is computed from the INCOMING capture, so the identical capture
    succeeds once a node is freed), but it is in the recorded no-receipt set,
    so the confirmation must exclude it by NAME.

    #4675's body, verbatim: *"a LOCAL receipt is written on a **2xx**
    (403/402/503 ⇒ exit 1, honest error, NO receipt) … that rule is correct and
    must not be weakened"* — and the Task-15 acceptance line in
    `tortoise/__main__.py`.

    **The session here IS durable** — the read serves exactly the posted rows —
    and that is what makes the test DISCRIMINATE. With the set derived from the
    classifier, the confirmation runs, matches, and mints a receipt for a
    refusal (rc 0): the recorded rule, weakened. An unreachable fake cannot
    test this, because the confirmation swallows its own errors (fail-open by
    contract), so "the read must not happen" is not assertable by making the
    read raise — an earlier version of this test did exactly that and stayed
    green under the mutation.

    MUTATION THAT REDS THIS: drop 402 from `_NO_RECEIPT_REFUSAL_STATUSES` (or
    derive the set from `classify_failure`) — rc becomes 0 and a receipt lands.
    """
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _post_refused_get_serves(
            "sid-quota", _EXPECTED_TURNS,
            post_exc=_http_error(402,
                                 '{"detail":"Team points limit reached"}')))

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-quota"))

    assert rc == 1, capsys.readouterr().err
    assert "HTTP 402" in capsys.readouterr().err
    assert not list((tmp_path / "receipts").glob("*.json")), \
        "the recorded 2xx-only rule: a 402 must not mint a receipt"
    assert read_spool_meta(spool, "sid-quota") is not None, \
        "honouring the 402 rule must not cost the session — #4614 keeps it"


def test_a_503_whose_session_landed_writes_no_receipt_but_keeps_the_spool(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """503 is `retry` to the CLASSIFIER (`status >= 500`) but it is in the
    recorded no-receipt set, so the confirmation must exclude it by NAME.

    #4675's body, verbatim: *"a LOCAL receipt is written on a **2xx**
    (403/402/503 ⇒ exit 1, honest error, NO receipt) … that rule is correct and
    must not be weakened"* — and the Task-15 acceptance line in
    `tortoise/__main__.py`. A 503 whose commit landed must still exit 1 with no
    receipt; what it must NOT do is lose the session, so the entry is spooled
    and recoverable (§3.3).

    MUTATION THAT REDS THIS: drop `status == 503` from the gate in
    `_confirm_already_captured` — a durable 503 mints a receipt and exits 0.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)

    class _Detail(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, 503, "unavailable", None,
                            io.BytesIO(b'{"detail":"Service Unavailable"}'))
        sid = req.full_url.rsplit("/", 1)[-1]
        detail = {"id": sid, "turns": len(_EXPECTED_TURNS), "extracted": 2,
                  "turn_points": [
                      {"id": f"{sid}_t{i}", "role": t["role"],
                       "content": t["content"]}
                      for i, t in enumerate(_EXPECTED_TURNS)]}
        return _Detail(json.dumps(detail).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-503"))

    assert rc == 1, capsys.readouterr().err
    assert "HTTP 503" in capsys.readouterr().err
    assert not list((tmp_path / "receipts").glob("*.json")), \
        "the recorded 2xx-only rule: a 503 mint no receipt"
    assert read_spool_meta(spool, "sid-503") is not None, \
        "honouring the 503 rule must not cost the session — it stays spooled"


@pytest.mark.parametrize("code,body", [
    (429, '{"detail":"capture capacity saturated — too many captures in '
          'flight; retry shortly"}'),
    (504, '{"detail":"The server\'s wait budget for this request was '
          'exceeded"}'),
])
def test_retryable_import_failure_is_spooled_for_the_next_drain(
        tmp_path, monkeypatch, codex_jsonl, code, body):
    """A retryable HTTP refusal leaves the turns DURABLE, and the existing
    drain files them later.

    Mutation: drop the spool write from the HTTPError branch — the meta
    assertion REDs (the session is lost)."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import PostOutcome, flush_spool, read_spool_meta, read_spool_turns

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-retry")

    def _raise(req, timeout=None):
        raise _http_error(code, body)

    with mock.patch("urllib.request.urlopen", _raise):
        rc = _cmd_sessions_import(args)

    # Still an HONEST failure: exit 1, no receipt — the spool is ADDITIONAL.
    assert rc == 1
    assert not list((tmp_path / "receipts").glob("*.json")), (
        "a failed POST must not write a receipt")

    meta = read_spool_meta(spool, "sid-retry")
    assert meta is not None, (
        "a retryable refusal left NO durable copy of the turns")
    assert meta["harness"] == "codex"
    assert meta["turns_count"] == len(_EXPECTED_TURNS)
    assert read_spool_turns(spool, "sid-retry") == _EXPECTED_TURNS

    # The EXISTING drain files it — the deferral is not a dead end, and what it
    # posts is exactly what the refused import tried to post.
    filed: list[dict] = []

    def _post(payload):
        filed.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    summary = flush_spool(spool, _post)
    assert summary.filed == 1, summary
    assert not summary.lost
    assert len(filed) == 1
    assert filed[0]["conversation"] == _EXPECTED_TURNS
    assert filed[0]["harness"] == "codex"
    assert filed[0]["session_id"] == "sid-retry"


@pytest.mark.parametrize("code", [400, 403])
def test_permanent_import_failure_is_not_spooled(
        tmp_path, monkeypatch, codex_jsonl, code, capsys):
    """A PERMANENT refusal keeps today's behaviour exactly: honest error, no
    receipt, and NOT parked on the spool (a malformed payload never becomes
    valid by waiting).

    Mutation: spool unconditionally — the spool assertions RED; mutation: drop
    the error line — the stderr assertion REDs."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import list_spool_metas, read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-perm")

    def _raise(req, timeout=None):
        raise _http_error(code, '{"detail":"malformed payload"}')

    with mock.patch("urllib.request.urlopen", _raise):
        rc = _cmd_sessions_import(args)

    assert rc == 1
    assert not list((tmp_path / "receipts").glob("*.json"))
    err = capsys.readouterr().err
    assert f"import failed (HTTP {code})" in err, err
    assert read_spool_meta(spool, "sid-perm") is None
    metas, _ = list_spool_metas(spool)
    assert metas == []


def test_unreachable_api_is_spooled_too(tmp_path, monkeypatch, codex_jsonl):
    """A NETWORK failure (no HTTP status at all) is the most common transient
    and `classify_failure(None)` is "retry" — so it must spool as well.

    Before this the `URLError` branch only wrote a breadcrumb, so an offline
    machine lost every session it imported: the same silent loss as the 504,
    on the failure most likely to happen.

    Mutation: drop the `_spool_if_retryable(None, ...)` call from the URLError
    branch — the meta assertion REDs."""
    from urllib.error import URLError

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import (
        PostOutcome,
        flush_spool,
        read_spool_meta,
        read_spool_turns,
    )

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-net")

    def _raise(req, timeout=None):
        raise URLError("Connection refused")

    with mock.patch("urllib.request.urlopen", _raise):
        rc = _cmd_sessions_import(args)

    assert rc == 1
    assert not list((tmp_path / "receipts").glob("*.json"))
    meta = read_spool_meta(spool, "sid-net")
    assert meta is not None, "an unreachable API lost the session"
    assert read_spool_turns(spool, "sid-net") == _EXPECTED_TURNS

    filed: list[dict] = []

    def _post(payload):
        filed.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    assert flush_spool(spool, _post).filed == 1


def test_spooled_entry_keeps_source_and_is_a_superset_of_the_post(
        tmp_path, monkeypatch, codex_jsonl):
    """The drained payload must carry what the refused POST carried — and the
    `machine_id` the spool adds is an addition, not a substitution.

    Pins `source` (unpinned before) and records the one field `_flush_one`
    synthesises that the import POST never sent."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import PostOutcome, flush_spool

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-src")

    with mock.patch("urllib.request.urlopen",
                    lambda req, timeout=None: (_ for _ in ()).throw(
                        _http_error(429, '{"detail":"saturated"}'))):
        assert _cmd_sessions_import(args) == 1

    filed: list[dict] = []

    def _post(payload):
        filed.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    assert flush_spool(spool, _post).filed == 1
    assert filed[0]["source"] == codex_jsonl.stem
    # `_flush_one` synthesises machine attribution unconditionally; the import
    # POST never sent it. So the payload SUPERSETS the refused POST...
    assert "machine_id" in filed[0], filed[0].keys()
    # ...but it is not a pure superset: an absent model is OMITTED, not sent as
    # null (`if meta.get("model"): payload["model"] = ...`).
    assert "model" not in filed[0], filed[0].keys()


def test_a_spool_write_failure_cannot_mask_the_honest_error(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """FAIL-OPEN: if the spool itself raises, the command must still exit 1 with
    the real error on stderr — no traceback escaping into the hook, and above
    all no "Spooled session" line that would claim a durability it does not
    have."""
    from tortoise.__main__ import _cmd_sessions_import

    _import_env(tmp_path, monkeypatch)   # hermetic env; the value is unused
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-boom")

    with mock.patch("urllib.request.urlopen",
                    lambda req, timeout=None: (_ for _ in ()).throw(
                        _http_error(504, '{"detail":"wait budget exceeded"}'))), \
            mock.patch("tortoise.capture_spool.write_spool_entry",
                       side_effect=RuntimeError("disk on fire")):
        rc = _cmd_sessions_import(args)

    assert rc == 1
    err = capsys.readouterr().err
    assert "import failed (HTTP 504)" in err, err
    assert "disk on fire" in err, err
    assert "Traceback" not in err, err
    assert "Spooled session" not in err, (
        "a failed spool write must not claim the session is durable")


def test_an_already_filed_entry_does_not_promise_a_filing(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """TRUTHFUL OUTPUT: a drain that already filed this exact content SKIPS the
    entry, so the message must not promise a filing it will not perform."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import PostOutcome, flush_spool, spool_dir

    _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-dup")
    boom = lambda req, timeout=None: (_ for _ in ()).throw(  # noqa: E731
        _http_error(504, '{"detail":"wait budget exceeded"}'))

    def _post(payload):
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    with mock.patch("urllib.request.urlopen", boom):
        assert _cmd_sessions_import(args) == 1
        ctx = capsys.readouterr().err
        assert "tortoise session drain" in ctx, ctx

    # File it, then let the SAME session fail again: the copy is now a no-op.
    assert flush_spool(spool_dir(), _post).filed == 1
    with mock.patch("urllib.request.urlopen", boom):
        assert _cmd_sessions_import(args) == 1
        assert "already filed" in capsys.readouterr().err


def test_the_spooled_message_matches_what_a_drain_would_actually_do(
        tmp_path, monkeypatch, codex_jsonl, capsys):
    """TRUTHFUL OUTPUT, bounded BOTH ways. The window is CARRIED across a
    rewrite now, so a drain inside a live window will NOT file the entry — the
    message must name that state instead of promising a filing. But a window
    beyond now + RETRY_MAX is one `_flush_one` treats as CORRUPT and files
    immediately, so claiming a drain cannot help there would be the opposite
    lie. The message's bound must be the drain's bound.

    Mutations: (1) drop the live-window branch -> phase 2 REDs (the message
    promises a filing the drain will refuse). (2) drop the upper bound ->
    phase 3 REDs (the message refuses a filing the drain would perform)."""
    import json as _json
    import time as _time

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import (
        RETRY_MAX_SECONDS,
        PostOutcome,
        _meta_path,
        flush_spool,
        read_spool_meta,
    )

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-window")
    boom = lambda req, timeout=None: (_ for _ in ()).throw(  # noqa: E731
        _http_error(504, '{"detail":"time budget exceeded"}'))

    # Phase 1: a FRESH entry has no window yet, so the drain really will file it.
    with mock.patch("urllib.request.urlopen", boom):
        assert _cmd_sessions_import(args) == 1
        assert "tortoise session drain" in capsys.readouterr().err

    def _doctor(window_ms):
        meta = read_spool_meta(spool, "sid-window")
        meta["next_attempt_at_ms"] = window_ms
        _meta_path(spool, "sid-window").write_text(
            _json.dumps(meta), encoding="utf-8")

    # Phase 2: a LIVE window. Doctor it AFTER the spool write (which sanitises a
    # corrupt window), and stub the write so the doctored value survives to the
    # read the message is built from.
    _doctor(_time.time() * 1000.0 + 30_000)
    with mock.patch("urllib.request.urlopen", boom), \
            mock.patch("tortoise.capture_spool.write_spool_entry",
                       lambda *a, **k: {"written": True, "bytes": 1,
                                        "discards": []}):
        assert _cmd_sessions_import(args) == 1
        err = capsys.readouterr().err
    assert "backoff window" in err, err
    assert "tortoise session drain" not in err, (
        "a drain inside the window will NOT file this, so promising one is "
        "false: " + err)

    # Phase 3: an absurd window is CORRUPT, and the drain files it immediately —
    # so the message must go back to naming the command.
    _doctor(_time.time() * 1000.0 + RETRY_MAX_SECONDS * 1000 * 10)
    with mock.patch("urllib.request.urlopen", boom), \
            mock.patch("tortoise.capture_spool.write_spool_entry",
                       lambda *a, **k: {"written": True, "bytes": 1,
                                        "discards": []}):
        assert _cmd_sessions_import(args) == 1
        err = capsys.readouterr().err
    assert "backoff window" not in err, (
        "a corrupt window is filed immediately, so claiming a drain cannot "
        "help is false: " + err)
    assert "tortoise session drain" in err, err

    # ...and prove the claim rather than asserting it.
    filed = flush_spool(spool, lambda payload: PostOutcome(
        ok=True, status=200, body={"session_id": payload["session_id"]}))
    assert filed.filed == 1, "the message promised a filing the drain refused"


def test_an_oversized_turn_is_clamped_before_spooling(tmp_path, monkeypatch):
    """The spool stores the CLAMPED turn, matching `session capture`.

    `SPOOL_MAX_ENTRY_BYTES` is sized on the clamped maximum (500 turns x 5000
    chars), so the import path must clamp identically or it stores a different
    session than the capture leg would. This case pins the CLAMP ITSELF — that
    the stored content is truncated — not the (arithmetically impossible)
    overflow: at 5000 chars x 500 turns the worst-case escaping leaves ~1.7 MB
    of headroom under the 16 MiB bound.

    Mutation: spool the unclamped turns — the length assertion REDs."""
    import json as _json

    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_turns

    spool = _import_env(tmp_path, monkeypatch)
    long_turn = "x" * 20_000
    # The REAL codex rollout shape (see _CODEX_LINES): a `response_item`
    # wrapper with the message inside `payload`. A bare message record parses
    # to ZERO turns, which would make this test assert nothing.
    transcript = tmp_path / "big.jsonl"
    transcript.write_text("\n".join(_json.dumps(rec) for rec in [
        {"type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": long_turn}]}},
        {"type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text",
                                  "text": "short reply"}]}},
    ]) + "\n", encoding="utf-8")

    def _raise(req, timeout=None):
        raise _http_error(429, '{"detail":"saturated"}')

    with mock.patch("urllib.request.urlopen", _raise):
        assert _cmd_sessions_import(SimpleNamespace(
            file=str(transcript), harness="codex",
            session_id="sid-big")) == 1

    turns = read_spool_turns(spool, "sid-big")
    assert turns, "the oversized session was discarded instead of spooled"
    assert len(turns[0]["content"]) == 5000, (
        f"unclamped turn of {len(turns[0]['content'])} chars reached the spool")


def test_remove_spool_entry_removes_and_is_spooled_reports_truthfully(tmp_path):
    """`remove_spool_entry` is intentionally void: "was it removed?" and "is it
    gone?" are different questions, and conflating them in a bool is how a
    caller comes to report a removal that failed. The pairing with `is_spooled`
    is what makes the answer checkable."""
    from tortoise.capture_spool import (
        Snapshot,
        is_spooled,
        read_spool_meta,
        remove_spool_entry,
        write_spool_entry,
    )

    root = tmp_path / "spool"
    assert is_spooled(root, "verify-1") is False
    write_spool_entry(root, Snapshot(
        session_id="verify-1", turns=list(_EXPECTED_TURNS), source="probe",
        machine_id="m", model=None, harness="codex"))
    assert is_spooled(root, "verify-1") is True

    remove_spool_entry(root, "verify-1")
    assert is_spooled(root, "verify-1") is False
    assert read_spool_meta(root, "verify-1") is None
    # Nothing left for a drain to find: the LOG must be gone, not just the meta.
    assert not list(root.rglob("*verify-1*")), list(root.rglob("*"))

    # Removing again is a harmless no-op, and still reports gone.
    remove_spool_entry(root, "verify-1")
    assert is_spooled(root, "verify-1") is False


def test_a_verify_probe_is_never_filed_by_a_DRAIN_but_its_own_capture_files(
        tmp_path):
    """THE STRUCTURAL GUARD — and the line it must not cross.

    An automatic drain must never file a `session verify` probe: the codex/
    cursor seams write from a DETACHED worker that can spool one after verify's
    cleanup has run, and the next drain would POST synthetic content into the
    tenant graph.

    But `session verify` fires the real seam, and the seam runs
    `session capture`, which files THROUGH THE SAME CODE — so refusing there
    stops the probe landing at all, `captured` reads FAIL for every harness, and
    verify can never prove the chain it exists to prove. That was a real
    regression (caught in CI). The guard applies to the unfiltered drain only,
    never to a filing that names its session.

    Just as important: the match must be NARROW. Session ids also come from
    `_local_session_id` (`<transcript-stem>-<digest>`), so a real transcript
    named `verify-my-notes.jsonl` derives `verify-my-notes-0e9ebe1a9262`.
    Under a prefix test that REAL capture was both refused and deleted.

    Mutations that must RED this: (a) drop the guard — the drain files the
    probe; (b) widen it to a prefix match — the real session is refused;
    (c) apply it to a targeted filing too — "captured" becomes unfillable."""
    from tortoise.capture_spool import (
        PostOutcome,
        Snapshot,
        flush_spool,
        is_probe_session_id,
        is_spooled,
        read_spool_meta,
        write_spool_entry,
    )

    # The exact shape `session_verify._probe_id` emits.
    assert is_probe_session_id("verify-codex-20260922T184500Z-a1b2c3") is True
    assert is_probe_session_id("verify-cursor-20260101T000000Z-ffffff") is True
    # ...and things that merely LOOK like it must not be treated as probes.
    assert is_probe_session_id("verify-my-notes-0e9ebe1a9262") is False, (
        "a real session id derived from a verify-*.jsonl filename")
    assert is_probe_session_id("verify-abc") is False
    assert is_probe_session_id("verify") is False
    assert is_probe_session_id("imp_deadbeef") is False
    assert is_probe_session_id(
        "verify-codex-20260922T184500Z-a1b2c3-extra") is False, "not a suffix"

    root = tmp_path / "spool"
    probe_id = "verify-codex-20260922T184500Z-a1b2c3"
    write_spool_entry(root, Snapshot(
        session_id=probe_id, turns=list(_EXPECTED_TURNS), source="probe",
        machine_id="m", model=None, harness="codex"))
    # A REAL session alongside it must still be filed, so the guard is not a
    # blanket-off that would pass this test while breaking capture.
    write_spool_entry(root, Snapshot(
        session_id="imp-real", turns=list(_EXPECTED_TURNS), source="real",
        machine_id="m", model=None, harness="codex"))
    # ...including the look-alike, which is real user data.
    write_spool_entry(root, Snapshot(
        session_id="verify-my-notes-0e9ebe1a9262", turns=list(_EXPECTED_TURNS),
        source="notes", machine_id="m", model=None, harness="codex"))

    posted: list[dict] = []

    def _post(payload):
        posted.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    flush_spool(root, _post)
    drain_ids = [p["session_id"] for p in posted]
    assert sorted(drain_ids) == ["imp-real", "verify-my-notes-0e9ebe1a9262"], (
        drain_ids)
    # The probe is NOT filed by a drain — but it is also NOT destroyed: the
    # refusal HOLDS the entry, so a false positive costs nothing. verify's own
    # cleanup removes what it created.
    assert probe_id not in drain_ids, "a drain filed a verify probe"
    assert is_spooled(root, probe_id), "the refusal must not destroy the entry"

    # ...but a TARGETED filing — which is exactly what `session capture` does,
    # and therefore what verify's own seam does — MUST go through. Refusing here
    # stopped the probe landing at all, so `captured` read FAIL for every
    # harness and verify could never prove the chain it exists to prove. That
    # was a real regression, caught in CI and not by review.
    posted.clear()
    flush_spool(root, _post, only_session_id=probe_id)
    assert [p["session_id"] for p in posted] == [probe_id], (
        "verify's own capture could not file its probe — captured reads FAIL")
    # A filed entry stays on the spool with `filed_key` stamped (that is the
    # design — the file is the record); what matters is that it POSTED, and that
    # the next drain will SKIP it rather than re-file it.
    meta = read_spool_meta(root, probe_id) or {}
    assert meta.get("filed_key"), "the probe was filed without stamping filed_key"
    posted.clear()
    flush_spool(root, _post)
    assert probe_id not in [p["session_id"] for p in posted], (
        "the already-filed probe was re-posted by a drain")


@pytest.mark.parametrize("exc", [
    TimeoutError("timed out reading the response"),
    ConnectionResetError("connection reset by peer"),
    json.JSONDecodeError("Expecting value", "<html>proxy</html>", 0),
])
def test_a_response_phase_failure_is_spooled_too(
        tmp_path, monkeypatch, codex_jsonl, exc):
    """The body read and its parse happen UNDER the `with`, and none of these
    is a URLError: a read timeout is a bare TimeoutError (an OSError, not a
    URLError), a truncated body is ConnectionResetError, and a proxy's HTML
    error page is a JSONDecodeError. Before this they escaped UNHANDLED — no
    spool and no breadcrumb — which is the same silent-loss class this path
    exists to close, and a capacity-gated server that accepts the connection
    then stalls is exactly that shape.

    Mutation: narrow the handler tuple so this clause is dead — this REDs."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import flush_spool, read_spool_meta, read_spool_turns

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-resp")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise exc

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        rc = _cmd_sessions_import(args)

    # An HONEST failure: exit 1, no receipt — the spool is additional.
    assert rc == 1, f"{type(exc).__name__} escaped instead of exiting 1"
    assert not list((tmp_path / "receipts").glob("*.json"))
    # ...and the turns are durable, with a breadcrumb so the harness owner sees it.
    assert read_spool_meta(spool, "sid-resp") is not None, (
        f"{type(exc).__name__} lost the session")
    assert read_spool_turns(spool, "sid-resp") == _EXPECTED_TURNS
    crumb = tmp_path / "capture-errors" / "codex.json"
    assert crumb.exists(), "a response-phase failure wrote no breadcrumb"

    filed: list[dict] = []

    def _post(payload):
        filed.append(payload)
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    assert flush_spool(spool, _post).filed == 1


def test_a_drained_session_clears_the_stale_failure_breadcrumb(
        tmp_path, monkeypatch, codex_jsonl):
    """A drain-recovered session must not leave the machine-local "this harness
    lost its last capture" breadcrumb standing.

    `sessions import` clears it only on a 2xx — a path a spooled-then-drained
    session never takes — so after this PR's recovery flow the breadcrumb still
    claimed a loss that had already been repaired, and `session verify` read a
    resolved failure as current.

    Mutation: drop the `_clear_breadcrumb_for` call — this REDs."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import PostOutcome, flush_spool, spool_dir

    _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-recover")

    with mock.patch("urllib.request.urlopen",
                    lambda req, timeout=None: (_ for _ in ()).throw(
                        _http_error(504, '{"detail":"wait budget exceeded"}'))):
        assert _cmd_sessions_import(args) == 1

    crumb = tmp_path / "capture-errors" / "codex.json"
    assert crumb.exists(), "the failure must be recorded in the first place"

    def _post(payload):
        return PostOutcome(ok=True, status=200,
                           body={"session_id": payload["session_id"]})

    assert flush_spool(spool_dir(), _post).filed == 1
    assert not crumb.exists(), (
        "a filed session still reports its harness as having lost a capture")


def test_a_failure_reading_the_ERROR_body_is_still_spooled(
        tmp_path, monkeypatch, codex_jsonl):
    """P1: the error body is read INSIDE `except HTTPError`, and an exception
    raised in an `except` block is NOT caught by the later clauses of the same
    `try`. So a server that returns 504/429 and then stalls while sending the
    body escaped the command entirely — no spool, no receipt — which is the
    same silent loss this path exists to close, and exactly the shape a
    capacity-gated server produces.

    Mutation: drop the inner try/except (or `.decode()` without `replace`) —
    this REDs.

    The body is decoded with errors="replace" for the sibling case: a non-UTF-8
    error body must not raise UnicodeDecodeError out of the handler either."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import flush_spool, read_spool_meta, read_spool_turns

    spool = _import_env(tmp_path, monkeypatch)
    args = SimpleNamespace(file=str(codex_jsonl), harness="codex",
                           session_id="sid-errbody")

    class _Fp:
        def read(self):
            raise TimeoutError("stalled sending the error body")

    def _raise(req, timeout=None):
        raise HTTPError("https://api.tortoise.test/v1/sessions", 504,
                        "wait budget exceeded", hdrs=None, fp=_Fp())

    with mock.patch("urllib.request.urlopen", _raise):
        rc = _cmd_sessions_import(args)

    assert rc == 1, "the stalled error body escaped the command"
    assert not list((tmp_path / "receipts").glob("*.json"))
    assert read_spool_meta(spool, "sid-errbody") is not None, (
        "a stalled error body lost the session")
    assert read_spool_turns(spool, "sid-errbody") == _EXPECTED_TURNS
    # 504 is retryable, so the drain must still be able to file it.
    assert flush_spool(spool, lambda p: PostOutcome(
        ok=True, status=200, body={"session_id": p["session_id"]})).filed == 1


def test_a_non_utf8_error_body_is_handled_not_raised(
        tmp_path, monkeypatch, codex_jsonl):
    """A proxy's garbled body must not raise UnicodeDecodeError out of the
    handler — an undecodable refusal is still a retryable refusal."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)

    def _raise(req, timeout=None):
        raise HTTPError("https://api.tortoise.test/v1/sessions", 429,
                        "saturated", hdrs=None,
                        fp=io.BytesIO(b"\xff\xfe not utf-8 \x80"))

    with mock.patch("urllib.request.urlopen", _raise):
        rc = _cmd_sessions_import(SimpleNamespace(
            file=str(codex_jsonl), harness="codex", session_id="sid-badbody"))

    assert rc == 1
    assert read_spool_meta(spool, "sid-badbody") is not None


def test_the_breadcrumb_clear_is_kind_aware_and_identity_aware(
        tmp_path, monkeypatch, codex_jsonl):
    """`_clear_breadcrumb_for` must not destroy evidence about something else.

    The breadcrumb path is shared with the shipped hooks' `install-inert`
    record, which is the ONLY way `session verify` reaches INERT — so a blind
    unlink let a drain racing verify make an inert install read as PROVEN. And
    a failure recorded for a DIFFERENT session is still current, however
    recently it happened.

    The check is by IDENTITY, not timestamp: the spool's `updated_at` is frozen
    on the dedup path, so it cannot say when a session last failed — the most
    obvious retry (re-importing identical content) never cleared its own
    record under a timestamp comparison.

    Mutations that must RED this: (a) drop the `kind` check — case 1 fails;
    (b) drop the identity check — case 2 fails.
    """
    from tortoise.capture_spool import _clear_breadcrumb_for
    from tortoise.hook_install import KIND_CAPTURE_FAILURE, KIND_INSTALL_INERT

    _import_env(tmp_path, monkeypatch)
    crumb = tmp_path / "capture-errors" / "codex.json"
    crumb.parent.mkdir(parents=True, exist_ok=True)

    # (1) An INERT install record is NOT ours to clear.
    crumb.write_text(json.dumps({
        "harness": "codex", "kind": KIND_INSTALL_INERT,
        "detail": "install is inert",
    }), encoding="utf-8")
    _clear_breadcrumb_for("codex", "sid-mine")
    assert crumb.exists(), "an INERT install record was cleared — verify lies"

    # (2) Another session's failure is still current.
    crumb.write_text(json.dumps({
        "harness": "codex", "kind": KIND_CAPTURE_FAILURE,
        "detail": "a different session failed", "session_id": "sid-other",
    }), encoding="utf-8")
    _clear_breadcrumb_for("codex", "sid-mine")
    assert crumb.exists(), "another session's live failure was cleared"

    # (3) OUR record IS cleared...
    crumb.write_text(json.dumps({
        "harness": "codex", "kind": KIND_CAPTURE_FAILURE,
        "detail": "this session failed then recovered",
        "session_id": "sid-mine",
    }), encoding="utf-8")
    _clear_breadcrumb_for("codex", "sid-mine")
    assert not crumb.exists(), "a recovered session left a stale failure record"

    # (4) A record with NO session id is the shape the shipped codex/cursor
    # shell hooks write, and it is NOT a legacy one — so it holds no identity to
    # match and must survive. Clearing it would erase a still-current failure
    # for a different session, which is what this function promises not to do.
    crumb.write_text(json.dumps({
        "harness": "codex", "kind": KIND_CAPTURE_FAILURE,
        "detail": "written by the shipped hook, no session id",
    }), encoding="utf-8")
    _clear_breadcrumb_for("codex", "sid-mine")
    assert crumb.exists(), (
        "a record with no identity was cleared — it may describe another "
        "session that is still lost")


@pytest.mark.parametrize("body_bytes,label", [
    (b"<html>caf\xe9</html>", "a non-UTF-8 proxy page"),
    (b"\xff\xfe\x00<\x00h", "a UTF-16 body truncated mid-character"),
])
def test_a_non_utf8_success_body_does_not_escape(
        tmp_path, monkeypatch, codex_jsonl, body_bytes, label):
    """`json.loads(bytes)` raises UnicodeDecodeError for a non-UTF-8 body, which
    is a ValueError sibling of JSONDecodeError — NOT a JSONDecodeError. Catching
    only JSONDecodeError therefore let a latin-1 or truncated-multibyte response
    escape the command entirely: no spool, no receipt, an unhandled traceback.
    Enumerating exception types is how this kept losing sessions; the handler
    now takes the SUPERCLASSES.

    Mutation: narrow the tuple back to JSONDecodeError — this REDs."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return body_bytes

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        rc = _cmd_sessions_import(SimpleNamespace(
            file=str(codex_jsonl), harness="codex", session_id="sid-nonutf8"))

    assert rc == 1, f"{label} escaped the command"
    assert read_spool_meta(spool, "sid-nonutf8") is not None, (
        f"{label} lost the session")


@pytest.mark.parametrize("exc", [
    OSError(5, "Input/output error"),
    ssl.SSLError("handshake stall mid-read"),
])
def test_a_bare_oserror_reading_the_response_is_spooled(
        tmp_path, monkeypatch, codex_jsonl, exc):
    """`resp.read()` can raise OSError/ssl.SSLError, which are neither
    TimeoutError nor ConnectionError — enumerating the OSError family instead of
    naming the base let these escape too. Mutation: narrow the tuple — REDs."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import read_spool_meta

    spool = _import_env(tmp_path, monkeypatch)

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise exc

    with mock.patch("urllib.request.urlopen", lambda req, timeout=None: _Resp()):
        rc = _cmd_sessions_import(SimpleNamespace(
            file=str(codex_jsonl), harness="codex", session_id="sid-oserr"))

    assert rc == 1, f"{type(exc).__name__} escaped the command"
    assert read_spool_meta(spool, "sid-oserr") is not None


def test_the_probe_match_cannot_drift_from_the_producer():
    """THE load-bearing invariant of the probe guard, asserted against the
    PRODUCER rather than a copied literal.

    `capture_spool._PROBE_SESSION_ID_RE` and `session_verify._probe_id` describe
    the same format in two places with nothing enforcing agreement. Pinning
    hardcoded samples lets the producer drift and the guard silently stop
    matching every real probe — synthetic content reaching the tenant graph with
    a green test. Deriving the samples from `_probe_id` is what makes the drift
    visible.

    Mutation: change `_probe_id` to emit a different suffix width — this REDs.
    """
    from tortoise.capture_spool import is_probe_session_id
    from tortoise.session_verify import _probe_id

    for harness in ("claude", "codex", "cursor", "pi"):
        produced = _probe_id(harness)
        assert is_probe_session_id(produced) is True, (
            f"a REAL {harness} probe id does not match the drain's guard: "
            f"{produced!r} — the guard is dead and synthetic content can be filed")


def test_a_config_url_error_is_loud_and_not_spooled(tmp_path, monkeypatch,
                                                    codex_jsonl, capsys):
    """A malformed API URL is a CONFIG error, not a response failure. The
    response-phase clause takes ValueError now, so without a pre-check a
    scheme-less URL was reported as \"import failed reading the response\" and
    spooled — telling the user to fix a spool that is not broken.

    Mutation: drop the pre-check — the message and the spool assertion RED."""
    from tortoise.__main__ import _cmd_sessions_import
    from tortoise.capture_spool import is_spooled, spool_dir

    _import_env(tmp_path, monkeypatch)
    monkeypatch.setenv("TORTOISE_API_URL", "api.premiselabs.co")   # no scheme

    rc = _cmd_sessions_import(SimpleNamespace(
        file=str(codex_jsonl), harness="codex", session_id="sid-url"))

    assert rc == 1
    err = capsys.readouterr().err
    assert "Invalid API URL" in err, err
    assert "reading the response" not in err, (
        "a config error was reported as a response failure")
    assert not is_spooled(spool_dir(), "sid-url"), (
        "a malformed URL was spooled and will fail identically forever")
