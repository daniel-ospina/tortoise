"""Session extraction semantics — LLM-default (#822, supersedes #312/#722).

LLM extraction is the DEFAULT (and only) capture extraction — the regex loop
was removed as a product path and the TORTOISE_SESSION_EXTRACTION mode knob is
gone. Semantics under test:
  - provider key configured (or TORTOISE_SESSION_LLM_MOCK=1 test seam)
    → capture runs the M2 LLM extractor, response extraction_mode == "llm"
  - no provider key → fail-closed 503
  - provider availability reflects the keys the code actually consumes
    (ANTHROPIC_API_KEY excluded — #722)
"""
import os
import tempfile

import pytest


def _provider_available():
    from tortoise import hosted_api
    return hosted_api._llm_provider_available()


def test_provider_availability(monkeypatch):
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert not _provider_available()
    # #722: ANTHROPIC_API_KEY is NOT a tortoise provider key — its presence
    # from unrelated host tooling must not fail the fail-closed gate open.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert not _provider_available()
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    assert _provider_available()


def test_provider_availability_mock_seam(monkeypatch):
    """TORTOISE_SESSION_LLM_MOCK=1 counts as configured (test seam)."""
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    assert _provider_available()


def test_sdk_and_hosted_availability_agree(monkeypatch):
    """The hosted 503 gate and the SDK extractor builder must agree — a drift
    would fail the gate open/closed wrongly (regression guard for #822)."""
    from tortoise.sdk import _build_session_llm_extractor

    def _extractor_present():
        return _build_session_llm_extractor() is not None

    # both false (no key, no seam)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert _provider_available() is False
    assert _extractor_present() is False
    # both true (real key)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert _provider_available() is True
    assert _extractor_present() is True
    # both true (mock seam)
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    assert _provider_available() is True
    assert _extractor_present() is True


# ── TORTOISE_SESSION_LLM_MODEL spec parsing (#1194) ────────────────────────


def _build_with_model(monkeypatch, spec):
    """Build the real extractor with DEEPSEEK_API_KEY set and the given
    TORTOISE_SESSION_LLM_MODEL spec (mock seam off). Other provider keys are
    cleared so deepseek is the configured provider (openrouter has priority
    and the dev shell exports real OPENROUTER/DEEPSEEK keys)."""
    from tortoise.sdk import _build_session_llm_extractor

    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MODEL", spec)
    return _build_session_llm_extractor()


def test_session_llm_model_bare_spec_accepted(monkeypatch):
    """#1194: a bare <model> (no 'provider:' prefix) is accepted and resolved
    against the configured provider key. Before the fix partition(':') made
    the bare form unreachable — p=<model>, m="" → misleading "names provider
    'deepseek-chat' but 'deepseek' is configured" error, or "bad model spec"
    when the bare name happened to equal the provider name — so a bare
    TORTOISE_SESSION_LLM_MODEL could NEVER be accepted despite the error
    message promising it, surfacing as an uncaught 500 on every capture."""
    ex = _build_with_model(monkeypatch, "deepseek-chat")
    assert ex is not None
    assert ex.points.model.id == "deepseek-chat"
    assert ex.relations.model.id == "deepseek-chat"


def test_session_llm_model_bare_spec_equal_to_provider_accepted(monkeypatch):
    """#1194: the other unreachable branch — a bare spec equal to the provider
    name ("deepseek") previously tripped `if not m` → "bad model spec". Now
    it is a valid bare model id."""
    ex = _build_with_model(monkeypatch, "deepseek")
    assert ex is not None
    assert ex.points.model.id == "deepseek"


def test_session_llm_model_prefixed_spec_requires_matching_provider(monkeypatch):
    """#1194: provider:model with the configured provider is accepted; a
    mismatched provider prefix still raises a clear config error (not a 500),
    and a trailing-colon spec with no model is still rejected."""
    ex = _build_with_model(monkeypatch, "deepseek:deepseek-reasoner")
    assert ex.points.model.id == "deepseek-reasoner"

    with pytest.raises(ValueError, match="names provider 'openai'"):
        _build_with_model(monkeypatch, "openai:gpt-4o-mini")

    with pytest.raises(ValueError, match="bad model spec"):
        _build_with_model(monkeypatch, "deepseek:")


# ── capture_session honors the fail-closed / LLM-default contract ──────────


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
def client(monkeypatch):
    """TestClient with auth override + temp FalkorDBLite DB.

    Auth is overridden so the handler body actually runs — the extraction
    gate sits AFTER auth, so without a valid team the request would 401 before
    ever reaching the 503 (previously the test was vacuous: the `in (401, 503)`
    assertion could only ever observe the auth 401). The SDK patch lets the 200
    path (quota check, graph writes) run against an embedded temp DB.
    TORTOISE_SESSION_LLM_MOCK=1 installs the offline MockModel extractor so
    the LLM path runs with zero network.
    """
    from fastapi.testclient import TestClient
    from tortoise.hosted_api import app, get_current_team

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: {
            "team_id": "test-team-722", "key_id": "test-key-722", "tier": "free",
        }
        _orig_init = _patch_tortoise_sdk_init(db_path)
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


def test_no_provider_503(monkeypatch, client):
    """No provider key (and no mock seam) fails closed with 503 — the regex
    fallback is gone, capture is disabled until a provider is configured."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 503, r.status_code
    assert "LLM provider key" in r.json()["detail"]


def test_default_llm_with_provider_key_200(monkeypatch, client):
    """Default (no TORTOISE_SESSION_EXTRACTION knob — it is gone) WITH a
    provider key runs the LLM extractor and reports extraction_mode \"llm\".

    #722 review P2 inverse: the availability gate's inverse was untested. The
    mock seam makes the full 200 path run; the effective-mode field pins the
    honest-reporting behavior: extraction_mode says \"llm\" because that is
    what actually ran (#822 — no more hardcoded \"regex\" stopgap).
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-722")
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert body["extraction_mode"] == "llm"


def test_default_llm_extracts_points(monkeypatch, client):
    """LLM default actually extracts: the M2 MockModel turns each sentence of
    a dense conversation into a Point (decision/claim regexes are gone)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-722")
    r = client.post("/v1/sessions", json={
        "conversation": [
            {"role": "user",
             "content": "I think the auth dead-end is the top issue. "
                        "We decided to ship serve --http first."},
            {"role": "assistant",
             "content": "Agreed. Evidence suggests the website config is "
                        "the root cause."},
        ],
    })
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert body["extraction_mode"] == "llm"
    assert body["extracted"] >= 2, body
    assert all(p["kind"] == "statement" for p in body["points"])
    # Extracted Points are wired to the session (CONTAINS) — same contract as
    # the removed regex loop.
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace="test-team-722")
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.is_episodic IS NULL OR p.is_episodic = false RETURN count(p)",
        params={"sid": body["session_id"]},
    ).result_set
    assert rows[0][0] == body["extracted"], \
        "every extracted LLM Point must be CONTAINS-connected to the session"
