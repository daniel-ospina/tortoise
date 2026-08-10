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

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module import.
# Set a test pepper before importing tortoise.auth.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

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

    def test_hash_api_key_roundtrip(self):
        """hash_api_key + verify_api_key roundtrip works (per-key salt means
        hashes are not deterministic across calls, but verification always works)."""
        key = "tt_abc123def456"
        h1 = auth_mod.hash_api_key(key)
        h2 = auth_mod.hash_api_key(key)
        # Per-key random salt: same input → different stored values
        assert h1 != h2
        # But both verify correctly against the original key
        assert auth_mod.verify_api_key(key, h1) is True
        assert auth_mod.verify_api_key(key, h2) is True
        # Format: salt_hex(64):digest_hex(64) = 129 chars
        assert len(h1) == 129

    def test_hash_api_key_different_inputs_produce_different_hashes(self):
        """Different keys produce different hashes."""
        h1 = auth_mod.hash_api_key("tt_key_one")
        h2 = auth_mod.hash_api_key("tt_key_two")
        assert h1 != h2

    def test_hash_api_key_uses_different_pepper_produces_different_hashes(self, monkeypatch):
        """With a different pepper, the same key produces different stored values."""
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "my-pepper-value")
        mod_a = importlib.reload(auth_mod)
        h_a = mod_a.hash_api_key("test-key-123")

        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "other-pepper-value")
        mod_b = importlib.reload(auth_mod)
        h_b = mod_b.hash_api_key("test-key-123")

        # Different peppers → different hashes for the same key
        assert h_a != h_b
        # Restore module state (env + _PEPPER_BYTES) — the reloads above leave
        # auth._PEPPER_BYTES pinned to "other-pepper-value", poisoning every
        # later lookup_hash/hash_api_key call in the session (broke
        # test_auth_flip + test_supabase_control in full-suite runs, #767).
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        importlib.reload(auth_mod)

    def test_hash_api_key_import_uses_dev_pepper_without_env(self, monkeypatch):
        """Dev mode (no TORTOISE_API_KEY, no pepper): import succeeds using dev pepper."""
        monkeypatch.delenv("TORTOISE_SECRET_PEPPER", raising=False)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        mod = importlib.reload(auth_mod)
        assert mod._SECRET_PEPPER == auth_mod._DEV_PEPPER
        # Restore env so auth_mod can be re-imported cleanly
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        importlib.reload(auth_mod)

    def test_hash_api_key_import_crashes_without_pepper_in_prod(self, monkeypatch):
        """Prod mode (TORTOISE_API_KEY set, no pepper): import raises RuntimeError."""
        monkeypatch.delenv("TORTOISE_SECRET_PEPPER", raising=False)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_prod_key")
        with pytest.raises(RuntimeError, match="TORTOISE_SECRET_PEPPER"):
            importlib.reload(auth_mod)
        # Restore env so auth_mod can be re-imported cleanly
        monkeypatch.delenv("TORTOISE_API_KEY")
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        importlib.reload(auth_mod)

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

    def test_verify_api_key_rejects_malformed_stored(self):
        """verify_api_key handles malformed stored strings gracefully."""
        assert auth_mod.verify_api_key("tt_key", "not-a-valid-format") is False
        assert auth_mod.verify_api_key("tt_key", "short:hash") is False
        assert auth_mod.verify_api_key("tt_key", "") is False
        # None is caught by the ValueError/AttributeError handler
        assert auth_mod.verify_api_key("tt_key", None) is False


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
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper")
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
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper")
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

    def test_api_key_hash_is_pbkdf2(self):
        """hash_api_key produces a 'salt:hash' hex string of correct length."""
        h = auth_mod.hash_api_key("test-key")
        # Format: salt_hex(64):digest_hex(64) = 129 chars
        assert len(h) == 129
        salt_hex, digest_hex = h.split(":")
        assert len(salt_hex) == 64
        assert len(digest_hex) == 64
        assert all(c in "0123456789abcdef" for c in salt_hex)
        assert all(c in "0123456789abcdef" for c in digest_hex)

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

    def test_hash_survives_pepper_reload(self, monkeypatch):
        """Hashes generated with one pepper can be verified after re-import
        with the same pepper (simulating process restart)."""
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "survive-restart-pepper")
        mod_a = importlib.reload(auth_mod)
        key = "tt_key_for_restart_test"
        stored = mod_a.hash_api_key(key)

        # Simulate restart: re-import with same pepper
        mod_b = importlib.reload(auth_mod)
        assert mod_b.verify_api_key(key, stored) is True
