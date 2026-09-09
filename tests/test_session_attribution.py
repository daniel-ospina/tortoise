"""Tests for tortoise/session_attribution.py — machine_id derivation and field
sanitisation, mirroring the agent-infra capture-attribution.ts contract.

The derive-only contract:
- machine_id = sha256 hex of ``{hostname}\\0{username}``
- Never-throw: gethostname()/getuser() failures → deterministic placeholders
- Memoized per process
- Sanitise: strip control chars, trim, cap max_length; empty → None
"""

from __future__ import annotations

import hashlib
import re
import socket

import pytest

from tortoise.session_attribution import (
    _CTRL_CHAR_RE,
    derive_machine_id,
    sanitize_attribution_field,
)

# ═══════════════════════════════════════════════════════════════════════════
# sanitize_attribution_field
# ═══════════════════════════════════════════════════════════════════════════


class TestSanitizeAttributionField:
    """Mirrors ``sanitizeAttribution`` in agent-infra capture-attribution.ts."""

    def test_none_returns_none(self) -> None:
        assert sanitize_attribution_field(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert sanitize_attribution_field("") is None

    def test_whitespace_only_returns_none(self) -> None:
        assert sanitize_attribution_field("   ") is None
        assert sanitize_attribution_field("\t\n ") is None

    def test_strips_whitespace(self) -> None:
        assert sanitize_attribution_field("  hello  ") == "hello"

    def test_removes_control_characters(self) -> None:
        # Null byte, newline, carriage return, tab, etc.
        assert sanitize_attribution_field("hello\x00world") == "helloworld"
        assert sanitize_attribution_field("line1\nline2") == "line1line2"
        assert sanitize_attribution_field("col1\tcol2") == "col1col2"
        assert sanitize_attribution_field("abc\x1f") == "abc"

    def test_truncates_to_max_length(self) -> None:
        value = "a" * 300
        assert len(sanitize_attribution_field(value, max_length=256)) == 256
        assert sanitize_attribution_field(value, max_length=256) == "a" * 256

    def test_within_max_length_not_truncated(self) -> None:
        value = "hello-world"
        result = sanitize_attribution_field(value, max_length=256)
        assert result == value
        assert len(result) == len(value)

    def test_model_max_length_128(self) -> None:
        value = "claude-sonnet-4-20250514-variant-with-extra-suffix-12345678"
        result = sanitize_attribution_field(value, max_length=128)
        assert len(result) <= 128
        assert result == value[:128]

    def test_preserves_printable_unicode(self) -> None:
        value = "valid-unicode-ñ-é-福"
        assert sanitize_attribution_field(value) == value

    def test_all_control_chars_returns_none(self) -> None:
        assert sanitize_attribution_field("\x00\x01\x02") is None

    def test_control_char_regex(self) -> None:
        """Verify the regex matches bytes 0x00-0x1f but not printable ASCII."""
        for c in range(0x20):
            assert _CTRL_CHAR_RE.match(chr(c)), f"char 0x{c:02x} not matched"
        for c in range(0x20, 0x7F):
            assert not _CTRL_CHAR_RE.match(chr(c)), f"char 0x{c:02x} matched"


# ═══════════════════════════════════════════════════════════════════════════
# derive_machine_id
# ═══════════════════════════════════════════════════════════════════════════


class TestDeriveMachineId:
    """Derive-only sha256 of hostname\\0username — never-throw, memoised."""

    def test_returns_sha256_hex(self) -> None:
        mid = derive_machine_id()
        assert mid is not None
        # SHA-256 hex is exactly 64 characters
        assert len(mid) == 64
        # Must be valid hex
        assert re.fullmatch(r"[0-9a-f]{64}", mid)

    def test_deterministic_per_host(self) -> None:
        """Multiple calls return the same value (memoised)."""
        assert derive_machine_id() == derive_machine_id()

    def test_derivation_matches_expected(self) -> None:
        """Verify the derivation logic produces the right hash."""
        hostname = socket.gethostname()
        username = __import__("getpass").getuser()
        expected = hashlib.sha256(
            f"{hostname}\0{username}".encode("utf-8", errors="replace")
        ).hexdigest()
        assert derive_machine_id() == expected

    def test_sanitized_machine_id_64_hex(self) -> None:
        """The raw derive_machine_id output should pass sanitization unchanged."""
        mid = derive_machine_id()
        assert mid is not None
        sanitized = sanitize_attribution_field(mid, max_length=256)
        assert sanitized == mid
        assert len(sanitized) == 64

    @pytest.mark.parametrize(
        "model_input,expected",
        [
            ("claude-sonnet-4-20250514", "claude-sonnet-4-20250514"),
            (None, None),
            ("", None),
            ("  leading-trailing  ", "leading-trailing"),
            ("model\x00with\x01nulls", "modelwithnulls"),
        ],
    )
    def test_model_sanitized_correctly(
        self, model_input: str | None, expected: str | None
    ) -> None:
        result = sanitize_attribution_field(model_input, max_length=128)
        assert result == expected


# ═══════════════════════════════════════════════════════════════════════════
# Integration: SessionRequest already validates the fields — verify the helper
# produces values that pass SessionRequest validation.
# ═══════════════════════════════════════════════════════════════════════════


class TestSessionRequestValidation:
    """Verify SessionRequest accepts values from our helpers."""

    def test_machine_id_passes_session_request(self) -> None:
        from tortoise.hosted_api import SessionRequest

        mid = derive_machine_id()
        assert mid is not None
        sanitized = sanitize_attribution_field(mid, max_length=256)
        # Should construct without validation error
        sr = SessionRequest(
            conversation=[{"role": "user", "content": "hello"}],
            machine_id=sanitized,
        )
        assert sr.machine_id == sanitized

    def test_empty_machine_id_skipped(self) -> None:
        from tortoise.hosted_api import SessionRequest

        sr = SessionRequest(
            conversation=[{"role": "user", "content": "hello"}],
            machine_id=None,
        )
        assert sr.machine_id is None

    def test_printable_model_passes_validation(self) -> None:
        from tortoise.hosted_api import SessionRequest

        sr = SessionRequest(
            conversation=[{"role": "user", "content": "hello"}],
            model="claude-sonnet-4-20250514",
        )
        assert sr.model == "claude-sonnet-4-20250514"

    def test_control_char_rejected_by_session_request(self) -> None:
        """SessionRequest's own validator rejects control chars — our
        sanitize function must strip them so our callers never 422."""
        from tortoise.hosted_api import SessionRequest

        # Sanitized value should pass
        sanitized = sanitize_attribution_field("hello\x00world", max_length=256)
        assert sanitized == "helloworld"
        sr = SessionRequest(
            conversation=[{"role": "user", "content": "hello"}],
            machine_id=sanitized,
        )
        assert sr.machine_id == "helloworld"

    def test_model_too_long_sanitized(self) -> None:
        from tortoise.hosted_api import SessionRequest

        long_model = "m" * 200
        sanitized = sanitize_attribution_field(long_model, max_length=128)
        assert len(sanitized) == 128
        # SessionRequest ctor validates — 128 is within its 128 max_length
        sr = SessionRequest(
            conversation=[{"role": "user", "content": "hello"}],
            model=sanitized,
        )
        assert sr.model == sanitized
        assert len(sr.model) == 128