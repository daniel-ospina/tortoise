"""#4675 — confirming a capture that was REFUSED but had already committed.

``POST /v1/sessions`` can write the Session and its turns and still answer with
a retryable refusal: the transport wait bound ABANDONS its handler rather than
cancelling it, so the work completes after the 504 is sent. The client's
terminality rule therefore cannot be "the status was retryable ⇒ nothing
committed" — it must ask the server, and it must ask the right question.

These tests pin the QUESTION (the posted rows, by id AND by stored text — not
the Session row, and not the id set alone) and the ANSWER for every
inconclusive read (defer, never file, never discard).
"""
from __future__ import annotations

import pytest

from tortoise.session_confirm import (
    FILED,
    TURNS_MISSING,
    UNEXTRACTED,
    UNKNOWN,
    confirm_capture,
    expected_turns,
    session_extracted,
    turn_point_id,
    turn_points,
)
from tortoise.session_verify import _ApiError

TURNS = [{"role": "user", "content": "ship it"},
         {"role": "assistant", "content": "on it"},
         {"role": "user", "content": "unrelated"}]

SESSION = "s1"


def _stored(index: int) -> str:
    from tortoise.sdk import _capture_turn_texts
    return _capture_turn_texts([dict(t) for t in TURNS])[index]


def _detail(turns: list[dict] = TURNS, extracted: int = 2, *,
            session_id: str = SESSION, contents: list[str] | None = None) -> dict:
    from tortoise.sdk import _capture_turn_texts
    texts = (contents if contents is not None
             else _capture_turn_texts([dict(t) for t in turns]))
    rows = [{"id": turn_point_id(session_id, i), "content": texts[i]}
            for i in range(len(texts))]
    return {"id": session_id, "turns": len(texts), "extracted": extracted,
            "turn_points": rows}


def _reader(responses):
    """A reader yielding the given responses in order; an entry may be an
    exception INSTANCE to raise, ``None`` for a 404, or a detail dict."""
    seq = list(responses)
    calls: list[dict] = []

    def read(api_url, api_key, session_id, *, timeout=None):
        calls.append({"api_url": api_url, "session_id": session_id,
                      "timeout": timeout})
        item = seq.pop(0) if seq else None
        if isinstance(item, BaseException):
            raise item
        return item

    read.calls = calls
    return read


def _confirm(reader, turns=TURNS, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return confirm_capture(reader, "u", "k", SESSION, turns, **kw)


def test_the_posted_rows_and_extraction_is_filed():
    assert _confirm(_reader([_detail()])) == FILED


def test_durable_turns_without_extraction_is_unextracted():
    """Extraction runs AFTER the turn write, so the confirmation can prove the
    turns while the extraction the bound abandoned is still running. The SPOOL
    may terminalise on that; the import RECEIPT may not (#4188)."""
    assert _confirm(_reader([_detail(extracted=0)])) == UNEXTRACTED


def test_the_same_ids_with_DIFFERENT_TEXT_are_not_filed():
    """The P0 this guards: the ids are POSITIONAL, so a session id reused for
    different content with the same number of turns — a compaction, a branch, a
    MAX_SESSION_TURNS window shift — matches on ids while the new text is
    nowhere on the server. Filing on that loses the only copy.

    MUTATION THAT REDS THIS: compare `set(turn_points)` to `set(expected)`
    instead of the whole `{id: text}` mapping.
    """
    other = ["[user] a completely different turn", "[assistant] and another",
             "[user] and a third"]
    assert _confirm(_reader([_detail(contents=other)])) == TURNS_MISSING


def test_one_changed_turn_is_not_filed():
    """Content equality is per row, not per session."""
    swapped = [_stored(0), _stored(1), "[user] edited"]
    assert _confirm(_reader([_detail(contents=swapped)])) == TURNS_MISSING


def test_more_turns_than_posted_is_not_filed():
    grown = [*TURNS, {"role": "assistant", "content": "fourth"}]
    reader = _reader([_detail(grown)])
    assert _confirm(reader, turns=TURNS) == TURNS_MISSING
    # The grown set is a SUPERSET — accepting it would mean a concurrent
    # capture's rows satisfy our POST.
    assert len(reader.calls) == 1


def test_a_partial_turn_set_is_not_filed():
    assert _confirm(_reader([_detail(turns=TURNS[:1])])) == TURNS_MISSING


@pytest.mark.parametrize("response", [
    None,                                       # 404: not MERGEd yet
    _ApiError("HTTP 504", status=504),          # a refused READ
    _ApiError("HTTP 429", status=429),
    TimeoutError("read timed out"),             # transport
])
def test_every_inconclusive_read_defers(response):
    """UNKNOWN never files. The abandoned handler may simply not have written
    yet, and a read we could not complete is not evidence either way."""
    reader = _reader([response, response])
    assert _confirm(reader) == UNKNOWN
    assert len(reader.calls) == 2


def test_a_404_that_appears_is_filed_within_the_window():
    """The bounded re-read exists for exactly this: the write is racing us."""
    reader = _reader([None, _detail()])
    assert _confirm(reader) == FILED
    assert len(reader.calls) == 2


def test_a_transient_read_failure_then_success_is_filed():
    reader = _reader([TimeoutError("read timed out"), _detail()])
    assert _confirm(reader) == FILED


def test_no_turns_posted_is_never_confirmed():
    reader = _reader([_detail(turns=[])])
    assert _confirm(reader, turns=[]) == UNKNOWN
    assert reader.calls == []


def test_a_missing_session_id_is_never_confirmed():
    reader = _reader([_detail()])
    assert confirm_capture(reader, "u", "k", "", TURNS,
                           sleep=lambda _s: None) == UNKNOWN
    assert reader.calls == []


@pytest.mark.parametrize("bad", [
    {"turn_points": None},
    {"turn_points": "not-a-list"},
    {"turn_points": [{"id": 7}, {}, None, "x"]},
    {},
    "not-a-dict",
    None,
])
def test_a_malformed_detail_is_unknown_not_filed(bad):
    reader = _reader([bad, bad])
    assert _confirm(reader) == UNKNOWN


def test_a_row_whose_content_is_unreadable_cannot_match():
    """A row we cannot read the text of must never satisfy the comparison."""
    rows = [{"id": turn_point_id(SESSION, i), "content": None}
            for i in range(len(TURNS))]
    assert turn_points({"turn_points": rows})[turn_point_id(SESSION, 0)] != _stored(0)
    assert _confirm(_reader([{"turn_points": rows}])) == TURNS_MISSING


def test_the_sleep_runs_between_attempts_only():
    slept: list[float] = []
    reader = _reader([None, None])
    _confirm(reader, attempts=2, delay_s=0.25, sleep=slept.append)
    assert slept == [0.25], slept


def test_the_read_timeout_is_the_short_one_not_the_post_timeout():
    reader = _reader([_detail()])
    _confirm(reader, read_timeout_s=7.5)
    assert reader.calls[0]["timeout"] == 7.5


def test_the_default_budget_fits_inside_a_hook_that_already_waited_on_the_post():
    """The confirmation runs AFTER a POST that may have spent its own 30s, and
    the Claude SessionEnd hook that runs `session capture` synchronously is
    cancelled at 60s. Keep the worst case small."""
    from tortoise import session_confirm as sc
    worst = (sc.DEFAULT_ATTEMPTS * sc.DEFAULT_READ_TIMEOUT_S
             + (sc.DEFAULT_ATTEMPTS - 1) * sc.DEFAULT_DELAY_S)
    assert worst <= 15.0, worst


# ── the format contract this rests on ────────────────────────────────────


def test_the_turn_id_matches_the_servers_own_writer_format():
    """Drift guard. `{session_id}_t{i}` is the writer's per-row idempotency key;
    if it changes server-side, every confirmation would silently defer."""
    src = (__import__("pathlib").Path(__file__).resolve().parents[1]
           / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    assert 'f"{session_id}_t{i}"' in src, (
        "sdk._write_capture_turns no longer builds its ids that way — "
        "session_confirm.turn_point_id must follow it")
    assert turn_point_id("abc", 0) == "abc_t0"
    assert turn_point_id("abc", 12) == "abc_t12"


def test_the_stored_text_matches_the_servers_own_writer_definition():
    """The content comparison must describe the string the server STORES, so it
    is derived from the writer's shared helper rather than mirrored."""
    from tortoise.sdk import _capture_turn_texts
    odd = [{"role": "user", "content": "x"}, {"role": None, "content": None},
           {"role": 7, "content": 42}, {"content": "no role"},
           {"role": "assistant", "content": "y" * 6000}]
    assert expected_turns("abc", odd) == {
        f"abc_t{i}": text
        for i, text in enumerate(_capture_turn_texts([dict(t) for t in odd]))}


def test_session_extracted_is_total_over_odd_values():
    assert session_extracted({"extracted": 3}) == 3
    assert session_extracted({"extracted": -4}) == 0
    assert session_extracted({"extracted": True}) == 0
    assert session_extracted({"extracted": "3"}) == 0
    assert session_extracted({"extracted": None}) == 0
    assert session_extracted(None) == 0
