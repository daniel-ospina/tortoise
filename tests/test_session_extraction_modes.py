"""Session extraction-mode semantics (#312 delta 1 foundation).

TORTOISE_SESSION_EXTRACTION = auto|required|regex:
  auto (default): regex path when no LLM provider key — capture always works
  required: fail-closed 503 when no LLM provider configured
  regex: always deterministic regex
Unknown values fall back to auto.
"""
import os

import pytest


def _mode():
    from tortoise import hosted_api
    return hosted_api._session_extraction_mode()


def _provider_available():
    from tortoise import hosted_api
    return hosted_api._llm_provider_available()


def test_default_mode_is_auto(monkeypatch):
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTION", raising=False)
    assert _mode() == "auto"


def test_explicit_modes(monkeypatch):
    for m in ("auto", "required", "regex"):
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTION", m)
        assert _mode() == m


def test_unknown_mode_falls_back_to_auto(monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTION", "banana")
    assert _mode() == "auto"


def test_provider_availability(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not _provider_available()
    # #722: ANTHROPIC_API_KEY is NOT a tortoise provider key — its presence
    # from unrelated host tooling must not fail the `required` gate open.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert not _provider_available()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    assert _provider_available()


# ── capture_session honors the mode ─────────────────────────────────────────

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app
    return TestClient(app)


def test_required_mode_without_provider_503(monkeypatch, client):
    """`required` without any provider key fails closed with 503.

    Auth is overridden so the handler body actually runs — the extraction-mode
    gate sits AFTER auth, so without a valid team the request would 401 before
    ever reaching the 503 (previously the test was vacuous: the `in (401, 503)`
    assertion could only ever observe the auth 401).
    """
    from tortoise.hosted_api import app, get_current_team

    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "test-team-722", "key_id": "test-key-722", "tier": "free",
    }
    try:
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTION", "required")
        for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
                  "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        r = client.post("/v1/sessions", json={"conversation": []})
        assert r.status_code == 503, r.status_code
        assert "required" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()
