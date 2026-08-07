"""Tests for tortoise.crypto — Fernet token encryption (#499)."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

from tortoise.crypto import encrypt_token, decrypt_token


class TestCrypto:
    def test_roundtrip(self):
        enc = encrypt_token("gho_supersecret")
        assert enc != "gho_supersecret"
        assert decrypt_token(enc) == "gho_supersecret"

    def test_unicode_token(self):
        token = "gho_tokén-üñicødé-日本語"
        assert decrypt_token(encrypt_token(token)) == token

    def test_tamper_detection(self):
        enc = encrypt_token("secret")
        # Flip a character in the ciphertext
        tampered = ("A" if enc[0] != "A" else "B") + enc[1:]
        with pytest.raises(ValueError):
            decrypt_token(tampered)

    def test_empty_ciphertext_raises(self):
        with pytest.raises(ValueError):
            decrypt_token("")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_ENCRYPTION_KEY", raising=False)
        with pytest.raises(RuntimeError):
            encrypt_token("x")
