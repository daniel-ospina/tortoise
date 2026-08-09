"""EventLog tests — append-only JSONL store.

Runnable without pytest:  .venv/bin/python tests/test_log.py
(also works under pytest if installed).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.log import EventLog  # noqa: E402


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def test_init_string_path():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    assert isinstance(log.path, Path)
    assert str(log.path) == p
    print("PASS test_init_string_path")


def test_init_path_object():
    p = Path(_tmp("events.jsonl"))
    log = EventLog(p)
    assert isinstance(log.path, Path)
    assert log.path == p
    print("PASS test_init_path_object")


def test_append_writes_valid_jsonl():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"type": "PointAdded", "content": "hello"})
    with open(p, "r", encoding="utf-8") as f:
        line = f.readline().strip()
    parsed = json.loads(line)
    assert parsed == {"type": "PointAdded", "content": "hello"}
    print("PASS test_append_writes_valid_jsonl")


def test_multiple_appends_multiple_lines():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    for i in range(3):
        log.append({"n": i})
    with open(p, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    assert len(lines) == 3
    assert [json.loads(l)["n"] for l in lines] == [0, 1, 2]
    print("PASS test_multiple_appends_multiple_lines")


def test_read_all_nonexistent_file():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    assert not os.path.exists(p)
    assert log.read_all() == []
    print("PASS test_read_all_nonexistent_file")


def test_read_all_empty_file():
    p = _tmp("events.jsonl")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).touch()
    log = EventLog(p)
    assert log.read_all() == []
    print("PASS test_read_all_empty_file")


def test_read_all_returns_dicts():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"x": 1})
    log.append({"x": 2})
    result = log.read_all()
    assert result == [{"x": 1}, {"x": 2}]
    print("PASS test_read_all_returns_dicts")


def test_append_none_writes_null():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append(None)  # None → json null — a valid JSON scalar, not an error
    result = log.read_all()
    assert result == [None]
    print("PASS test_append_none_writes_null")


def test_append_non_serializable_raises():
    log = EventLog(_tmp("events.jsonl"))
    try:
        log.append({"bad": {1, 2, 3}})  # set is not JSON-serializable
        assert False, "should have raised"
    except TypeError:
        pass
    print("PASS test_append_non_serializable_raises")


def test_append_unicode():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"msg": "こんにちは — café 🎉"})
    result = log.read_all()
    assert result == [{"msg": "こんにちは — café 🎉"}]
    print("PASS test_append_unicode")


def test_directory_auto_creation():
    base = tempfile.mkdtemp(prefix="tortoise_")
    p = os.path.join(base, "deeply", "nested", "dirs", "events.jsonl")
    log = EventLog(p)
    assert not os.path.exists(os.path.dirname(p))
    log.append({"created": True})
    assert os.path.exists(p)
    result = log.read_all()
    assert result == [{"created": True}]
    print("PASS test_directory_auto_creation")


# ── read_after / cursor tests (#688) ────────────────────────────────


def test_read_after_none_on_empty():
    log = EventLog(_tmp("events.jsonl"))
    assert log.read_after() == []
    assert log.read_after(None) == []
    print("PASS test_read_after_none_on_empty")


def test_read_after_none_returns_all():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    log.append({"n": 3})
    assert log.read_after() == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert log.read_after(None) == [{"n": 1}, {"n": 2}, {"n": 3}]
    print("PASS test_read_after_none_returns_all")


def test_cursor_at_end_empty_log():
    log = EventLog(_tmp("events.jsonl"))
    token = log.cursor_at_end()
    assert isinstance(token, str) and len(token) > 0
    # Reading after empty-log cursor returns nothing (no events yet)
    assert log.read_after(token) == []
    print("PASS test_cursor_at_end_empty_log")


def test_cursor_at_end_no_new_events():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    # No new events appended after cursor snapshot
    assert log.read_after(token) == []
    print("PASS test_cursor_at_end_no_new_events")


def test_read_after_returns_only_new():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    token = log.cursor_at_end()  # snapshot after 2 events
    log.append({"n": 3})
    log.append({"n": 4})
    assert log.read_after(token) == [{"n": 3}, {"n": 4}]
    print("PASS test_read_after_returns_only_new")


def test_read_after_returns_empty_when_no_new():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    # Multiple polls with no appends should all return empty
    assert log.read_after(token) == []
    assert log.read_after(token) == []
    print("PASS test_read_after_returns_empty_when_no_new")


def test_invalid_cursor_raises():
    log = EventLog(_tmp("events.jsonl"))
    for bad in ("not-base64!", "", "   ", "eyJ2IjoyfQ=="):  # v=2 unsupported
        try:
            log.read_after(bad)
            assert False, f"should have raised for {bad!r}"
        except ValueError:
            pass
    print("PASS test_invalid_cursor_raises")


def test_cursor_beyond_end_returns_empty():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    # Encode a cursor pointing to index 99 (way beyond the single event)
    token = log._encode_cursor(99)
    assert log.read_after(token) == []
    print("PASS test_cursor_beyond_end_returns_empty")


def test_polling_e2e_pattern():
    """Simulate the polling pattern: append, snapshot, append, poll, repeat."""
    log = EventLog(_tmp("events.jsonl"))

    # Initial sync
    log.append({"type": "A", "n": 0})
    events = log.read_after()  # None → all
    assert len(events) == 1
    cursor = log.cursor_at_end()
    assert log.read_after(cursor) == []  # no new yet

    # First poll cycle
    log.append({"type": "B", "n": 1})
    log.append({"type": "C", "n": 2})
    new_events = log.read_after(cursor)
    assert new_events == [{"type": "B", "n": 1}, {"type": "C", "n": 2}]
    cursor = log.cursor_at_end()

    # Second poll cycle — no new events
    assert log.read_after(cursor) == []

    # Third poll cycle — one more
    log.append({"type": "D", "n": 3})
    assert log.read_after(cursor) == [{"type": "D", "n": 3}]
    print("PASS test_polling_e2e_pattern")


def test_cursor_is_stable():
    """A cursor obtained earlier still works after more appends."""
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token_after_1 = log.cursor_at_end()
    log.append({"n": 2})
    log.append({"n": 3})
    # Using the old cursor still returns exactly events 2,3
    assert log.read_after(token_after_1) == [{"n": 2}, {"n": 3}]
    print("PASS test_cursor_is_stable")


def test_read_after_with_unicode_events():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"msg": "こんにちは"})
    token = log.cursor_at_end()
    log.append({"msg": "— café 🎉"})
    assert log.read_after(token) == [{"msg": "— café 🎉"}]
    print("PASS test_read_after_with_unicode_events")


def test_read_after_single_event_batch():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    log.append({"n": 2})
    assert log.read_after(token) == [{"n": 2}]
    print("PASS test_read_after_single_event_batch")


def test_read_after_idempotent_cursor():
    """Same cursor returns same (deterministic) results across calls."""
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    token = log.cursor_at_end()
    log.append({"n": 3})
    result1 = log.read_after(token)
    result2 = log.read_after(token)
    assert result1 == result2 == [{"n": 3}]
    print("PASS test_read_after_idempotent_cursor")


def test_cursor_token_opaque():
    """Cursor tokens should be stable across encode/decode round-trips."""
    for idx in (-1, 0, 1, 42, 999):
        token = EventLog._encode_cursor(idx)
        assert isinstance(token, str)
        # Must not contain the raw index visibly
        assert str(idx) not in token
        assert EventLog._decode_cursor(token) == idx
    print("PASS test_cursor_token_opaque")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall EventLog tests passed")


if __name__ == "__main__":
    _run_all()
