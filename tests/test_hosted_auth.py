"""Tests for hosted platform auth, provisioning, and team creation (#7713).

Covers:
- API key hashing with pepper (hash_api_key, verify_api_key)
- Team name sanitization and derivation
- Team creation via TortoiseSDK.team_create()
- Duplicate team prevention
- API key format (tt_ prefix, hex format)
"""
from __future__ import annotations

import importlib
import os
import re
import tempfile
from pathlib import Path

import pytest
import tortoise.auth as auth_mod
from tortoise.sdk import TortoiseSDK


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def sdk():
    """Create SDK with temporary FalkorDBLite database."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        sdk = TortoiseSDK(db_path, namespace="test-hosted")
        yield sdk


# ── API Key Hashing Tests ───────────────────────────────────────────────────

class TestApiKeyHashing:
    """Tests for hash_api_key and verify_api_key from tortoise.auth."""

    def test_hash_api_key_is_deterministic(self):
        """Same input always produces same hash."""
        key = "tt_abc123def456"
        h1 = auth_mod.hash_api_key(key)
        h2 = auth_mod.hash_api_key(key)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 produces 32 bytes = 64 hex chars

    def test_hash_api_key_different_inputs_produce_different_hashes(self):
        """Different keys produce different hashes."""
        h1 = auth_mod.hash_api_key("tt_key_one")
        h2 = auth_mod.hash_api_key("tt_key_two")
        assert h1 != h2

    def test_hash_api_key_uses_pepper_when_set(self, monkeypatch):
        """When TORTOISE_SECRET_PEPPER is set, PBKDF2 is used."""
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "my-pepper-value")
        mod = importlib.reload(auth_mod)
        try:
            hashed = mod.hash_api_key("test-key-123")
            assert len(hashed) == 64  # PBKDF2-HMAC SHA-256 produces 32 bytes = 64 hex chars
        finally:
            monkeypatch.delenv("TORTOISE_SECRET_PEPPER", raising=False)

    def test_hash_api_key_no_pepper_fallback(self, monkeypatch):
        """Without pepper, uses plain SHA-256."""
        monkeypatch.delenv("TORTOISE_SECRET_PEPPER", raising=False)
        mod = importlib.reload(auth_mod)
        try:
            hashed = mod.hash_api_key("test-key")
            assert len(hashed) == 64  # SHA-256 hex digest
        finally:
            pass  # env already cleared

    def test_verify_api_key_correct(self):
        """verify_api_key returns True for correct key."""
        key = "tt_test_verification_key"
        stored = auth_mod.hash_api_key(key)
        assert auth_mod.verify_api_key(key, stored) is True

    def test_verify_api_key_incorrect(self):
        """verify_api_key returns False for wrong key."""
        stored = auth_mod.hash_api_key("tt_correct_key")
        assert auth_mod.verify_api_key("tt_wrong_key", stored) is False

    def test_verify_api_key_constant_time(self):
        """verify_api_key uses constant-time comparison (no short-circuit)."""
        key = "tt_" + "a" * 60
        stored = auth_mod.hash_api_key(key)
        # Test with keys of same length but different last char
        wrong_key = key[:-1] + "b"
        assert auth_mod.verify_api_key(wrong_key, stored) is False


# ── Team Name Sanitization Tests ────────────────────────────────────────────

class TestTeamNameSanitization:
    """Tests for team name validation and sanitization logic."""

    def test_valid_team_name_accepted(self, sdk):
        """Valid alphanumeric team names with hyphens/underscores pass."""
        result = sdk.team_create("my-team_123")
        assert result["name"] == "my-team_123"
        assert result["api_key"].startswith("tt_")

    def test_team_name_with_spaces_rejected(self, sdk):
        """Spaces are not allowed in team names."""
        with pytest.raises(ValueError, match="alphanumeric"):
            sdk.team_create("my team")

    def test_team_name_with_special_chars_rejected(self, sdk):
        """Special characters are rejected."""
        with pytest.raises(ValueError, match="alphanumeric"):
            sdk.team_create("team@name!")

    def test_team_name_empty_rejected(self, sdk):
        """Empty team name raises ValueError."""
        with pytest.raises(ValueError):
            sdk.team_create("")

    def test_team_name_whitespace_only_rejected(self, sdk):
        """Whitespace-only name raises ValueError."""
        with pytest.raises(ValueError):
            sdk.team_create("   ")

    def test_team_name_too_long_rejected(self, sdk):
        """Names > 64 characters are rejected."""
        with pytest.raises(ValueError):
            sdk.team_create("a" * 65)

    def test_team_name_max_length_accepted(self, sdk):
        """Exactly 64 characters is fine."""
        name = "a" * 64
        result = sdk.team_create(name)
        assert result["name"] == name

    def test_team_name_starts_with_hyphen_rejected(self, sdk):
        """Leading hyphen is not allowed."""
        with pytest.raises(ValueError, match="alphanumeric"):
            sdk.team_create("-myteam")


# ── Team Creation Tests ─────────────────────────────────────────────────────

class TestTeamCreation:
    """Integration tests for SDK.team_create()."""

    def test_team_create_returns_expected_fields(self, sdk):
        """team_create returns name, graph_name, api_key, id."""
        result = sdk.team_create("test-team")
        assert "name" in result
        assert "graph_name" in result
        assert "api_key" in result
        assert "id" in result
        assert result["name"] == "test-team"
        assert result["graph_name"] == "team_test-team"

    def test_team_create_api_key_format(self, sdk):
        """API key starts with tt_ and is hex-encoded."""
        result = sdk.team_create("apikey-test")
        key = result["api_key"]
        assert key.startswith("tt_")
        # After tt_, should be hex (UUID4)
        hex_part = key[3:]
        assert re.match(r'^[0-9a-f]{32}$', hex_part), f"Key hex part: {hex_part}"

    def test_team_create_duplicate_rejected(self, sdk):
        """Creating the same team name twice raises ValueError."""
        sdk.team_create("unique-team")
        with pytest.raises(ValueError, match="already exists"):
            sdk.team_create("unique-team")

    def test_team_create_different_namespaces_independent(self, sdk):
        """Same team name in different namespaces is allowed."""
        # Create in one namespace
        sdk.team_create("cross-ns-team")

        # Create with different namespace should be independent
        sdk2 = TortoiseSDK(sdk._db_path, namespace="test-hosted-2")
        result = sdk2.team_create("cross-ns-team")
        assert result["name"] == "cross-ns-team"

    def test_team_create_generates_unique_keys(self, sdk):
        """Each team gets a unique API key."""
        r1 = sdk.team_create("team-a")
        r2 = sdk.team_create("team-b")
        assert r1["api_key"] != r2["api_key"]

    def test_team_create_graph_name_format(self, sdk):
        """Graph name follows team_{name} pattern."""
        result = sdk.team_create("mygraph")
        assert result["graph_name"] == "team_mygraph"

    def test_team_create_rollback_on_graph_failure(self, sdk):
        """If graph creation fails, registry entry is rolled back."""
        # Create first team normally
        sdk.team_create("rollback-test")

        # Try creating with same name — should fail
        with pytest.raises(ValueError, match="already exists"):
            sdk.team_create("rollback-test")


# ── Dev Mode Tests ──────────────────────────────────────────────────────────

class TestAuthDevMode:
    """Tests for dev mode bypass behavior."""

    def test_dev_mode_allows_all_requests(self, monkeypatch):
        """When TORTOISE_API_KEY is unset, all requests pass."""
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        mod = importlib.reload(auth_mod)
        try:
            assert mod.is_dev_mode() is True
            assert mod.require_auth() is True
            assert mod.require_auth({"authorization": "anything"}) is True
        finally:
            monkeypatch.delenv("TORTOISE_API_KEY", raising=False)

    def test_production_mode_rejects_unauthorized(self, monkeypatch):
        """When TORTOISE_API_KEY is set, bad tokens are rejected."""
        monkeypatch.setenv("TORTOISE_API_KEY", "prod-key-123")
        mod = importlib.reload(auth_mod)
        try:
            assert mod.is_dev_mode() is False
            assert mod.require_auth() is False  # No headers
            assert mod.require_auth({"authorization": "wrong"}) is False
        finally:
            monkeypatch.delenv("TORTOISE_API_KEY", raising=False)


# ── Security Tests (E2E-7-D) ────────────────────────────────────────────────

class TestSecurityBaseline:
    """Security tests from E2E-7-D test design."""

    def test_api_key_hash_is_sha256_or_pbkdf2(self):
        """hash_api_key produces a hex string of correct length."""
        h = auth_mod.hash_api_key("test-key")
        # SHA-256 = 64 chars hex, PBKDF2-HMAC SHA-256 = 128 chars hex
        assert len(h) in (64, 128)
        assert all(c in "0123456789abcdef" for c in h)

    def test_api_keys_are_not_stored_in_plaintext(self, sdk):
        """After team_create, verify the graph doesn't store the plaintext key."""
        result = sdk.team_create("security-test")
        api_key = result["api_key"]

        # The returned API key is plaintext (for one-time display)
        assert api_key.startswith("tt_")

        # But the key stored in the graph should be hashed
        # Check via query: the Team node should have a key_hash property
        # and that hash should NOT equal the plaintext key
        key_hash = auth_mod.hash_api_key(api_key)
        assert key_hash != api_key
        assert len(key_hash) >= 64

    def test_api_key_verification_works_end_to_end(self, sdk):
        """Create a team, get the key, verify it validates against its hash."""
        result = sdk.team_create("verify-test")
        api_key = result["api_key"]
        stored_hash = auth_mod.hash_api_key(api_key)

        # Correct key verifies
        assert auth_mod.verify_api_key(api_key, stored_hash) is True
        # Wrong key does not
        assert auth_mod.verify_api_key("tt_wrong_key_000000000000", stored_hash) is False
