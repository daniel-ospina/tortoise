"""Tests for tortoise.ids — ULID, content_hash, and now_iso.

Runnable without pytest:  .venv/bin/python tests/test_ids.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.ids import content_hash, now_iso, ulid  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────

_CROCKFORD = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


# ── ulid ─────────────────────────────────────────────────────────────

def test_ulid_returns_26_chars():
    u = ulid()
    assert len(u) == 26, f"expected 26 chars, got {len(u)}"
    print("PASS test_ulid_returns_26_chars")


def test_ulid_only_crockford_chars():
    u = ulid()
    assert set(u).issubset(_CROCKFORD), f"illegal chars: {set(u) - _CROCKFORD}"
    print("PASS test_ulid_only_crockford_chars")


def test_ulid_unique_on_consecutive_calls():
    a = ulid()
    b = ulid()
    assert a != b, "two consecutive ulid() calls must differ"
    print("PASS test_ulid_unique_on_consecutive_calls")


def test_ulid_first_char_is_timestamp_digit():
    """First char of a ULID is the top 5 bits of the millisecond timestamp, so
    it should always be a digit (0-9) for the forseeable future."""
    u = ulid()
    assert u[0] in "0123456789", f"first char '{u[0]}' is not a digit"
    print("PASS test_ulid_first_char_is_timestamp_digit")


# ── content_hash ─────────────────────────────────────────────────────

def test_content_hash_deterministic():
    h1 = content_hash("hello")
    h2 = content_hash("hello")
    assert h1 == h2, "same input must produce same hash"
    print("PASS test_content_hash_deterministic")


def test_content_hash_different_inputs():
    h1 = content_hash("hello")
    h2 = content_hash("world")
    assert h1 != h2, "different inputs must produce different hashes"
    print("PASS test_content_hash_different_inputs")


def test_content_hash_empty_string():
    h = content_hash("")
    assert len(h) == 64, "sha256 hex digest must be 64 hex chars"
    assert all(c in "0123456789abcdef" for c in h), "must be hex"
    print("PASS test_content_hash_empty_string")


def test_content_hash_handles_unicode():
    h = content_hash("cafe\u0301")  # café with combining accent
    assert len(h) == 64
    # deterministic with same unicode
    assert h == content_hash("cafe\u0301")
    print("PASS test_content_hash_handles_unicode")


# ── now_iso ──────────────────────────────────────────────────────────

def test_now_iso_returns_string():
    ts = now_iso()
    assert isinstance(ts, str), f"expected str, got {type(ts).__name__}"
    print("PASS test_now_iso_returns_string")


def test_now_iso_ends_with_utc_offset():
    ts = now_iso()
    assert ts.endswith("+00:00"), f"expected +00:00 suffix, got '{ts[-6:]}'"
    print("PASS test_now_iso_ends_with_utc_offset")


def test_now_iso_contains_t_separator():
    ts = now_iso()
    assert "T" in ts, f"expected 'T' separator, got '{ts}'"
    print("PASS test_now_iso_contains_t_separator")


def test_now_iso_parseable():
    ts = now_iso()
    dt = datetime.fromisoformat(ts)
    assert isinstance(dt, datetime), "must be parseable by datetime.fromisoformat"
    print("PASS test_now_iso_parseable")


# ── runner ───────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall ids tests passed")


if __name__ == "__main__":
    _run_all()
