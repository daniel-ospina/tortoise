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


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall EventLog tests passed")


if __name__ == "__main__":
    _run_all()
