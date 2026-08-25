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
    from tortoise.ingest import _PROVIDERS  # noqa: I001
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
    from fastapi.testclient import TestClient  # noqa: I001
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


def test_default_llm_with_provider_key_422_on_empty(monkeypatch, client):
    """P1 #1529: an EMPTY conversation is now rejected with 422 before any
    write (the old "graceful" 200 + extracted:0 is the E2E-8 owned negative
    — an empty conversation is never ok=True). The mock-seam + NON-empty
    conversation still yields 200 with a truthful extraction_mode (the seam
    makes the full 200 path run; the effective-mode field pins the
    honest-reporting behavior: extraction_mode says "llm:mock" because the
    mock route is what actually ran (#822/#1530)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-722")
    r = client.post("/v1/sessions", json={"conversation": []})
    assert r.status_code == 422, r.status_code
    assert "extractable content" in r.json()["detail"]
    # non-empty + mock seam → 200 + truthful mode (the empty gate must not
    # reject a real conversation)
    r2 = client.post("/v1/sessions", json={
        "conversation": [{"role": "user", "content": "we decided to ship it"}]})
    assert r2.status_code == 200, r2.status_code
    body = r2.json()
    assert body["extraction_mode"] == "llm:mock"
    assert body["extraction_provider"] == "mock"
    # E3 (#1535) emits a source-turn resolution warning on the offline mock
    # path — warnings is an additive list; errors must be empty on success.
    assert body["errors"] == []
    assert isinstance(body["warnings"], list)


@pytest.mark.embedded_only  # Epic #1647 (PR #1684): TORTOISE_SESSION_LLM_MOCK is an embedded-lane seam — on docker the v2 extractor's S3 stage runs (mode='real') and the mock cannot search, yielding 0 points; file-order flaky (bidirectional pollution). Docker-lane mock extraction = separate divergence.
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
    assert body["extraction_mode"] == "llm:mock"
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


# ── #1530 P2: gate match + route recording on the hosted path ───────────────

def test_openai_only_key_v2_503_not_500(monkeypatch, client):
    """#1530 gate match on the hosted path: an openai-only deployment passes
    the broad outer gate (_llm_provider_available) but the v2 extractor's
    adapter cannot consume OPENAI_API_KEY — the inner gate's ValueError
    converts to a clean fail-closed 503, NEVER an uncaught 500 (the #1468
    divergence lesson). P1 #1529: the request must be NON-empty — the
    whole-conversation blank gate (422) precedes the inner v2 gate, so an
    empty body would 422 instead of exercising the provider-mismatch path."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-only")
    r = client.post("/v1/sessions", json={"conversation": [
        {"role": "user", "content": "we decided to ship it"}]})
    assert r.status_code == 503, r.status_code
    detail = r.json()["detail"]
    assert "DEEPSEEK_API_KEY" in detail and "OPENROUTER_API_KEY" in detail


@pytest.mark.embedded_only  # Epic #1647 (PR #1684): mock-seam extraction (S3-stage lane divergence) — see test_default_llm_extracts_points
def test_hosted_capture_records_deepseek_direct_route(monkeypatch, client):
    """The hosted capture response records the resolved v2 route + provider
    (parity with the SDK path by construction, #1530 D8)."""
    import requests as _requests

    class _FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content":
                "{\"entities\": [{\"name\": \"the strategy\", "
                "\"kind\": \"core:strategy\", \"lifecycle\": \"created\", "
                "\"supersedes\": null, \"note\": null}], "
                "\"events\": [{\"content\": \"we decided on the new strategy\", "
                "\"eventKind\": \"core:decision\", "
                "\"about_entities\": [\"the strategy\"]}], "
                "\"points\": [{\"content\": \"the new strategy is durable\", "
                "\"pointKind\": \"statement\", "
                "\"about_entities\": [\"the strategy\"]}], "
                "\"operators\": [], \"chain_notes\": [], "
                "\"link_before_create\": []}"}}],
                "usage": {}}

    def _fake_post(self_or_url, url=None, **kwargs):
        return _FakeResp()

    # Epic #1647 (PR #1684): adapters call self._session.post — patch the
    # Session seam (the requests.post patch never intercepted → real network)
    monkeypatch.setattr(_requests.Session, "post", _fake_post)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    r = client.post("/v1/sessions", json={"conversation": [
        {"role": "user", "content": "we decided on the new strategy"},
    ]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["extraction_mode"] == "llm:deepseek-direct"
    assert body["extraction_provider"] == "deepseek-direct"
    assert body["extracted"] >= 1


# ── P1 (#1529): the CLI consumer (status-only) must not report success on a
#    failed capture — the hosted API surfaces extraction failures as 200 +
#    additive body errors (extraction_mode "error"/"empty"), so the body is
#    the only failure signal a status-only consumer sees.


def test_cmd_session_capture_mode_error_exits_1(tmp_path, monkeypatch):
    """P1: a 200 body with extraction_mode 'error' + errors → exit 1 + stderr —
    never 'Captured session: …' with extracted: 0."""
    import json

    from tortoise.__main__ import _cmd_session_capture, _parse_transcript

    f = tmp_path / "transcript.txt"
    f.write_text("User: we decided to ship it\nAssistant: agreed\n")
    assert _parse_transcript(f.read_text()), "transcript must parse to turns"

    payload = {"session_id": "s-err", "extraction_mode": "error",
               "errors": ["RuntimeError: provider returned 500"],
               "extracted": 0, "warnings": []}

    class _FakeResp:
        def read(self):
            return json.dumps(payload).encode()

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _FakeCtx())
    args = type("A", (), {"file": str(f)})()
    assert _cmd_session_capture(args, "api-key", "http://api") == 1


def test_cmd_session_capture_mode_empty_exits_1(tmp_path, monkeypatch):
    """P1: a 200 body with extraction_mode 'empty' → exit 1 (an empty
    conversation is a failure on the hosted surface, 422 in-band)."""
    import json

    from tortoise.__main__ import _cmd_session_capture, _parse_transcript

    f = tmp_path / "transcript.txt"
    f.write_text("User: we decided to ship it\nAssistant: agreed\n")
    assert _parse_transcript(f.read_text())

    payload = {"session_id": "s-empty", "extraction_mode": "empty",
               "errors": ["no extractable content — empty or blank conversation"],
               "extracted": 0, "warnings": []}

    class _FakeResp:
        def read(self):
            return json.dumps(payload).encode()

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _FakeCtx())
    args = type("A", (), {"file": str(f)})()
    assert _cmd_session_capture(args, "api-key", "http://api") == 1


def test_cmd_session_capture_success_still_returns_0(tmp_path, monkeypatch, capsys):
    """P1 regression: the happy path stays green — a successful capture body
    (truthful mode, no errors) still prints 'Captured session:' and returns 0."""
    import json

    from tortoise.__main__ import _cmd_session_capture, _parse_transcript

    f = tmp_path / "transcript.txt"
    f.write_text("User: we decided to ship it\nAssistant: agreed\n")
    assert _parse_transcript(f.read_text())

    payload = {"session_id": "s-ok", "extraction_mode": "llm:mock",
               "extraction_provider": "mock", "extracted": 1,
               "points": [], "errors": [], "warnings": []}

    class _FakeResp:
        def read(self):
            return json.dumps(payload).encode()

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: _FakeCtx())
    args = type("A", (), {"file": str(f)})()
    assert _cmd_session_capture(args, "api-key", "http://api") == 0
    out = capsys.readouterr().out
    assert "Captured session: s-ok" in out
