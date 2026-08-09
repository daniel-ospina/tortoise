"""Session extraction-mode semantics (#312 delta 1 foundation).

TORTOISE_SESSION_EXTRACTION = auto|required|regex:
  auto (default): regex path when no LLM provider key — capture always works
  required: fail-closed 503 when no LLM provider configured
  regex: always deterministic regex
Unknown values fall back to auto.
"""
import os
import tempfile

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


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one.

    Same pattern as tests/test_hosted_api.py — the full 200 path needs a real
    (embedded) DB for quota checks and graph writes.
    """
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    """Restore original TortoiseSDK.__init__."""
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture()
def client():
    """TestClient with auth override + temp FalkorDBLite DB.

    Auth is overridden so the handler body actually runs — the extraction-mode
    gate sits AFTER auth, so without a valid team the request would 401 before
    ever reaching the 503 (previously the test was vacuous: the `in (401, 503)`
    assertion could only ever observe the auth 401). The SDK patch lets the 200
    path (quota check, graph writes) run against an embedded temp DB.
    """
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app, get_current_team

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "test-team-722", "key_id": "test-key-722", "tier": "free",
        }
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


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


def test_required_mode_with_provider_key_200(monkeypatch, client):
    """Inverse gate: `required` WITH a provider key must NOT 503.

    #722 review P2: the availability check's inverse was untested — a
    regression where the gate fails open/closed wrongly would 503 production
    while current tests stay green. Full 200 path runs (regex extraction is the
    implemented baseline; LLM branching is pending #312 delta 2), and the
    effective-mode field pins the honest-reporting behavior: the response
    says "regex" because that is what actually ran.
    """
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTION", "required")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-722")
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 200, r.status_code
    body = r.json()
    # #722: effective method is what ran — the regex path (not 503, not a lie
    # about LLM extraction).
    assert body["extraction_mode"] == "regex"


def test_regex_mode_reports_effective_method(monkeypatch, client):
    """explicit `regex` mode returns 200 and reports the effective method."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTION", "regex")
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 200, r.status_code
    assert r.json()["extraction_mode"] == "regex"
