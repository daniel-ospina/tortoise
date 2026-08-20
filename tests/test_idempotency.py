"""Unit tests for tortoise.idempotency — IngestKey, document_key, stream_key, IngestResult.

Runnable without pytest:  .venv/bin/python tests/test_idempotency.py
(also works under pytest if installed).
"""
from __future__ import annotations

import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.idempotency import (  # noqa: E402, I001, RUF100
    IngestKey,
    IngestResult,
    document_key,
    stream_key,
)


# ── helpers ────────────────────────────────────────────────────────────────

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ── IngestKey ──────────────────────────────────────────────────────────────

def test_ingestkey_creation_and_fields():
    k = IngestKey("document", "abc123")
    assert k.kind == "document"
    assert k.value == "abc123"
    print("PASS test_ingestkey_creation_and_fields")


def test_ingestkey_as_dict():
    k = IngestKey("stream", "chunk-5")
    d = k.as_dict()
    assert d == {"kind": "stream", "value": "chunk-5"}
    assert isinstance(d, dict)
    print("PASS test_ingestkey_as_dict")


def test_ingestkey_is_frozen():
    k = IngestKey("document", "abc")
    try:
        k.kind = "stream"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("IngestKey should be frozen")
    try:
        k.value = "xyz"  # type: ignore[misc]
    except Exception:
        pass
    else:
        raise AssertionError("IngestKey should be frozen")
    # fields unchanged
    assert k.kind == "document"
    assert k.value == "abc"
    print("PASS test_ingestkey_is_frozen")


def test_ingestkey_equality():
    a = IngestKey("document", "abc")
    b = IngestKey("document", "abc")
    assert a == b
    assert not (a != b)  # noqa: SIM202
    # different kind
    c = IngestKey("stream", "abc")
    assert a != c
    # different value
    d = IngestKey("document", "xyz")
    assert a != d
    print("PASS test_ingestkey_equality")


# ── document_key ───────────────────────────────────────────────────────────

def test_document_key_kind():
    k = document_key("hello")
    assert k.kind == "document"
    print("PASS test_document_key_kind")


def test_document_key_value_is_sha256_hex():
    k = document_key("hello")
    expected = _sha256("hello")
    assert k.value == expected, f"{k.value!r} != {expected!r}"
    # must be 64-char hex
    assert len(k.value) == 64
    assert all(c in "0123456789abcdef" for c in k.value)
    print("PASS test_document_key_value_is_sha256_hex")


def test_document_key_same_text_same_key():
    k1 = document_key("hello world")
    k2 = document_key("hello world")
    assert k1.value == k2.value
    assert k1 == k2
    print("PASS test_document_key_same_text_same_key")


def test_document_key_different_text_different_key():
    k1 = document_key("hello")
    k2 = document_key("world")
    assert k1.value != k2.value
    assert k1 != k2
    print("PASS test_document_key_different_text_different_key")


def test_document_key_empty_string():
    k = document_key("")
    expected = _sha256("")
    assert k.value == expected
    assert k.kind == "document"
    print("PASS test_document_key_empty_string")


# ── stream_key ─────────────────────────────────────────────────────────────

def test_stream_key_kind():
    k = stream_key("s1", 0, 100)
    assert k.kind == "stream"
    print("PASS test_stream_key_kind")


def test_stream_key_value_format():
    k = stream_key("mystream", 42, 99)
    assert k.value == "mystream:42-99"
    print("PASS test_stream_key_value_format")


def test_stream_key_different_params_different_key():
    k1 = stream_key("a", 0, 10)
    k2 = stream_key("a", 0, 20)
    assert k1 != k2
    k3 = stream_key("b", 0, 10)
    assert k1 != k3
    print("PASS test_stream_key_different_params_different_key")


# ── IngestResult ───────────────────────────────────────────────────────────

def test_ingestresult_defaults():
    r = IngestResult(run_id=None, skip=False)
    assert r.run_id is None
    assert r.skip is False
    assert r.reason == ""
    print("PASS test_ingestresult_defaults")


def test_ingestresult_reason_defaults_to_empty():
    r = IngestResult(run_id="abc", skip=True)
    assert r.reason == ""
    print("PASS test_ingestresult_reason_defaults_to_empty")


def test_ingestresult_explicit_reason():
    r = IngestResult(run_id="abc", skip=True, reason="duplicate key")
    assert r.reason == "duplicate key"
    print("PASS test_ingestresult_explicit_reason")


def test_ingestresult_all_fields_accessible():
    r = IngestResult(run_id="r42", skip=False, reason="")
    assert r.run_id == "r42"
    assert r.skip is False
    assert r.reason == ""
    print("PASS test_ingestresult_all_fields_accessible")


def test_ingestresult_non_skip():
    r = IngestResult(run_id="run-1", skip=False, reason="fresh ingest")
    assert not r.skip
    assert r.run_id is not None
    print("PASS test_ingestresult_non_skip")


def test_ingestresult_is_mutable():
    r = IngestResult(run_id="a", skip=True, reason="dup")
    r.skip = False
    r.reason = ""
    assert not r.skip
    assert r.reason == ""
    print("PASS test_ingestresult_is_mutable")


# ── runner ─────────────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall idempotency tests passed")


if __name__ == "__main__":
    _run_all()
