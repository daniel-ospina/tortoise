"""#4675 — confirming a capture that was REFUSED but had already committed.

``POST /v1/sessions`` can write the Session and its turns and still answer with
a retryable refusal: the transport wait bound ABANDONS its handler rather than
cancelling it, so the work completes after the 504 is sent. The client's
terminality rule therefore cannot be "the status was retryable ⇒ nothing
committed" — it must ask the server, and it must ask the right question.

These tests pin the QUESTION (the deterministic turn-id set, not the Session
row) and the ANSWER for every inconclusive read (defer, never file, never
discard).
"""
from __future__ import annotations

import pytest

from tortoise.session_confirm import (
    FILED,
    TURNS_MISSING,
    UNEXTRACTED,
    UNKNOWN,
    confirm_capture,
    expected_turn_ids,
    session_extracted,
    turn_point_id,
    turn_point_ids,
)


def _detail(session_id: str, turns: int, extracted: int = 2,
            ids: list[str] | None = None) -> dict:
    return {
        "id": session_id,
        "turns": turns,
        "extracted": extracted,
        "turn_points": [{"id": i} for i in (ids or (
            [turn_point_id(session_id, i) for i in range(turns)]))],
    }


def _reader(responses):
    """A reader yielding the given responses in order; entries may be an
    exception CLASS to raise, ``None`` for a 404, or a detail dict."""
    seq = list(responses)
    calls: list[dict] = []

    def read(api_url, api_key, session_id, *, timeout=None):
        calls.append({"api_url": api_url, "session_id": session_id,
                      "timeout": timeout})
        item = seq.pop(0) if seq else None
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("refused")
        return item

    read.calls = calls
    return read


def test_the_posted_turn_set_and_extraction_is_filed():
    reader = _reader([_detail("s1", 3, extracted=4)])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == FILED


def test_durable_turns_without_extraction_is_unextracted():
    """Extraction runs AFTER the turn write, so the confirmation can prove the
    turns while the extraction the bound abandoned is still running. The SPOOL
    may terminalise on that; the import RECEIPT may not (#4188)."""
    reader = _reader([_detail("s1", 3, extracted=0)])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == UNEXTRACTED


def test_more_turns_than_posted_is_not_filed():
    """A concurrent capture that grew the transcript is not evidence that THIS
    payload committed. A superset must never read as filed."""
    reader = _reader([_detail("s1", 5)])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == TURNS_MISSING


def test_a_partial_turn_set_is_not_filed():
    reader = _reader([_detail("s1", 1)])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == TURNS_MISSING


def test_404_through_the_whole_window_is_unknown():
    """The abandoned handler may not have MERGEd yet — unknown, never
    'not committed', so the caller defers."""
    reader = _reader([None, None, None])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == UNKNOWN
    assert len(reader.calls) == 3


def test_a_refused_read_is_unknown():
    reader = _reader([TimeoutError, TimeoutError, TimeoutError])
    assert confirm_capture(reader, "u", "k", "s1", 3,
                           sleep=lambda _s: None) == UNKNOWN


def test_a_404_that_appears_is_filed_within_the_window():
    """The bounded re-read exists for exactly this: the write is racing us."""
    reader = _reader([None, _detail("s1", 2)])
    assert confirm_capture(reader, "u", "k", "s1", 2,
                           sleep=lambda _s: None) == FILED
    assert len(reader.calls) == 2


def test_no_turns_posted_is_never_confirmed():
    """A payload we never sent cannot be confirmed — the reader must not even
    be consulted, or an empty conversation would file against a coincidental
    id-space match."""
    reader = _reader([_detail("s1", 0)])
    assert confirm_capture(reader, "u", "k", "s1", 0,
                           sleep=lambda _s: None) == UNKNOWN
    assert reader.calls == []


def test_a_missing_session_id_is_never_confirmed():
    reader = _reader([_detail("", 1)])
    assert confirm_capture(reader, "u", "k", "", 1,
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
    reader = _reader([bad, bad, bad])
    assert confirm_capture(reader, "u", "k", "s1", 1,
                           sleep=lambda _s: None) == UNKNOWN


def test_the_sleep_runs_between_attempts_only():
    slept: list[float] = []
    reader = _reader([None, None, None])
    confirm_capture(reader, "u", "k", "s1", 1, attempts=3, delay_s=0.25,
                    sleep=slept.append)
    assert slept == [0.25, 0.25], slept


def test_the_read_timeout_is_the_short_one_not_the_post_timeout():
    reader = _reader([_detail("s1", 1)])
    confirm_capture(reader, "u", "k", "s1", 1, read_timeout_s=7.5,
                    sleep=lambda _s: None)
    assert reader.calls[0]["timeout"] == 7.5


def test_turn_point_ids_are_the_servers_idempotency_ids():
    """The contract the confirmation rests on: the hosted writer MERGEs
    ``{session_id}_t{i}`` for the whole window BEFORE extraction."""
    assert turn_point_id("abc", 0) == "abc_t0"
    assert turn_point_id("abc", 12) == "abc_t12"
    assert expected_turn_ids("abc", 3) == {"abc_t0", "abc_t1", "abc_t2"}
    assert expected_turn_ids("abc", 0) == set()
    assert turn_point_ids(_detail("abc", 2)) == {"abc_t0", "abc_t1"}


def test_session_extracted_is_total_over_odd_values():
    assert session_extracted({"extracted": 3}) == 3
    assert session_extracted({"extracted": -4}) == 0
    assert session_extracted({"extracted": True}) == 0
    assert session_extracted({"extracted": "3"}) == 0
    assert session_extracted({"extracted": None}) == 0
    assert session_extracted(None) == 0
