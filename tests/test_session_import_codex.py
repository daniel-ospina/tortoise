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
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

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
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.setenv("TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "receipts"))
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(spool))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # POSITIVE CONTROL: a broken override would silently target the real spool.
    assert spool_dir() == spool
    return spool


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


def test_remove_spool_entry_unlinks_and_reports(tmp_path):
    """`remove_spool_entry` is what lets `session verify` take its synthetic
    probe back out of the spool. It must remove BOTH files (meta + turn log)
    and report whether anything was there, so a no-op is distinguishable from
    a removal."""
    from tortoise.capture_spool import (
        Snapshot,
        read_spool_meta,
        remove_spool_entry,
        write_spool_entry,
    )

    root = tmp_path / "spool"
    write_spool_entry(root, Snapshot(
        session_id="probe-1", turns=list(_EXPECTED_TURNS), source="probe",
        machine_id="m", model=None, harness="codex"))
    assert read_spool_meta(root, "probe-1") is not None

    assert remove_spool_entry(root, "probe-1") is True
    assert read_spool_meta(root, "probe-1") is None
    # Nothing left for a drain to find: the log must be gone, not just the meta.
    assert not list(root.rglob("*probe-1*")), list(root.rglob("*"))
    # A second call reports honestly that there was nothing to remove.
    assert remove_spool_entry(root, "probe-1") is False
