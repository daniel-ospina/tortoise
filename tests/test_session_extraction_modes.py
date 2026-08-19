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


def test_provider_key_parity_all_keys(monkeypatch):
    """#1197: EVERY key hosted_api._llm_provider_available() reports must
    actually build an extractor in sdk — and every key that opens the gate
    must be in the reported set. A drift fails the 503 gate open/closed
    wrongly (hosted available=True but sdk extractor=None → partial-write
    500 instead of a clean fail-closed 503)."""
    from tortoise import hosted_api
    from tortoise.sdk import _build_session_llm_extractor

    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MODEL", raising=False)
    for k in hosted_api._LLM_PROVIDER_KEYS:
        monkeypatch.delenv(k, raising=False)
    assert not hosted_api._llm_provider_available()
    for k in hosted_api._LLM_PROVIDER_KEYS:
        monkeypatch.setenv(k, "sk-test-1197")
        try:
            assert hosted_api._llm_provider_available(), \
                f"{k} must open the hosted 503 gate"
            assert _build_session_llm_extractor() is not None, \
                f"{k} opens the hosted gate but sdk builds NO extractor"
        finally:
            monkeypatch.delenv(k)


def test_openrouter_model_shape_warning_helper():
    """PR #1220 review P2 c65: _session_llm_model_shape_warning flags ONLY
    openrouter models lacking <family>/<model>; all other providers and
    well-formed openrouter routes never warn (the 404-at-capture case)."""
    from tortoise.sdk import _session_llm_model_shape_warning

    # openrouter + bare model → warn (the 404-at-capture shape)
    w = _session_llm_model_shape_warning("openrouter:deepseek-chat", "openrouter")
    assert w and "<family>/<model>" in w and "404" in w
    # openrouter + well-formed family/model → no warn
    assert _session_llm_model_shape_warning("openrouter:deepseek/deepseek-chat", "openrouter") is None
    # openrouter default (unset spec) → deepseek/deepseek-chat → no warn
    assert _session_llm_model_shape_warning("", "openrouter") is None
    # non-openrouter providers use bare model ids — never warn
    assert _session_llm_model_shape_warning("deepseek:deepseek-chat", "deepseek") is None
    assert _session_llm_model_shape_warning("openai:gpt-4o-mini", "openai") is None
    assert _session_llm_model_shape_warning("gemini:gemini-2.0-flash", "gemini") is None


def test_sdk_priority_covers_all_registry_providers():
    """#1197 drift guard: every provider registered in ingest._PROVIDERS must
    be (a) in sdk._SESSION_LLM_PROVIDER_PRIORITY and (b) carry a key in
    hosted_api._LLM_PROVIDER_KEYS. Adding a provider without updating both
    fails the 503 gate OPEN (hosted says available, sdk builds None)."""
    from tortoise import hosted_api
    from tortoise.ingest import _PROVIDERS
    from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY

    for provider, (_url, key_env) in _PROVIDERS.items():
        assert provider in _SESSION_LLM_PROVIDER_PRIORITY, (
            f"provider {provider!r} registered in ingest._PROVIDERS but missing "
            f"from sdk._SESSION_LLM_PROVIDER_PRIORITY — the 503 gate would fail "
            f"open for {key_env}"
        )
        assert key_env in hosted_api._LLM_PROVIDER_KEYS, (
            f"{key_env} (ingest provider {provider!r}) missing from "
            f"hosted_api._LLM_PROVIDER_KEYS — the gate would not see it"
        )


def test_analyze_keys_subset_of_session_keys():
    """#1197: every analyze._LLM_PROVIDERS key must be usable by the SESSION
    extractor — an analyze-only key (in analyze but not in ingest._PROVIDERS)
    would open the hosted 503 gate while sdk._build_session_llm_extractor
    builds None → mid-capture failure. A naive subset-vs-union check is
    tautological (_llm_provider_keys() unions analyze in by construction);
    the real invariant is: every analyze key must be an INGEST provider key."""
    from tortoise.ingest import _PROVIDERS
    from tortoise.analyze import _LLM_PROVIDERS

    ingest_keys = {key_env for _url, key_env in _PROVIDERS.values() if key_env}
    extra = set(_LLM_PROVIDERS) - ingest_keys
    assert not extra, (
        f"analyze-only key(s) {sorted(extra)} are not ingest provider keys — "
        f"they would open the hosted 503 gate while "
        f"sdk._build_session_llm_extractor cannot consume them; add them to "
        f"ingest._PROVIDERS or exclude them from the session key union"
    )


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
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
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
    assert body["extracted"] >= 1, body  # v2 mock emits one point
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
