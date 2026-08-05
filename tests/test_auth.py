"""Tests for auth — API key validation, dev mode bypass."""
from __future__ import annotations

import importlib
import os

import pytest

# TORTOISE_SECRET_PEPPER is mandatory since #67 — set before import
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import tortoise.auth as auth_mod


def test_dev_mode_when_no_key(monkeypatch):
    """Dev mode active when TORTOISE_API_KEY is not set."""
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper")
    # Re-import to pick up the env change
    mod = importlib.reload(auth_mod)
    try:
        assert mod.is_dev_mode() is True
        assert mod.require_auth() is True
        assert mod.require_auth({}) is True  # any headers ok in dev
    finally:
        # Restore
        pass


def test_require_auth_rejects_when_key_set(monkeypatch):
    """require_auth rejects missing/wrong tokens when TORTOISE_API_KEY is set."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tk_secret123")
    monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper")
    mod = importlib.reload(auth_mod)
    try:
        assert mod.is_dev_mode() is False
        # No headers → reject
        assert mod.require_auth() is False
        # Wrong token → reject
        assert mod.require_auth({"authorization": "Bearer wrong"}) is False
        # Missing Bearer prefix → reject
        assert mod.require_auth({"authorization": "tk_secret123"}) is False
    finally:
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)


def test_require_auth_accepts_correct_token(monkeypatch):
    """Correct Bearer token passes."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tk_secret123")
    monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper")
    mod = importlib.reload(auth_mod)
    try:
        assert mod.require_auth({"authorization": "Bearer tk_secret123"}) is True
    finally:
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
