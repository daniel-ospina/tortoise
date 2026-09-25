"""Session extraction semantics — LLM-default (#822, supersedes #312/#722).

LLM extraction is the DEFAULT (and only) capture extraction — the regex loop
was removed as a product path and the TORTOISE_SESSION_EXTRACTION mode knob is
gone. Semantics under test:
  - provider key configured (or TORTOISE_SESSION_LLM_MOCK=1 test seam)
    → capture runs the M2 LLM extractor, response extraction_mode == "llm"
  - no provider key → the capture STORES its turns and skips ONLY the LLM
    extraction, reporting extraction_mode == "no-provider" (#3892 owner
    ruling, byte-parity with sdk.capture_session)
  - provider availability reflects the keys the code actually consumes
    (ANTHROPIC_API_KEY excluded — #722)
"""
import os
import tempfile

import pytest

from tests._http_fixtures import patched_tortoise_sdk


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
    """The hosted provider check and the SDK extractor builder must agree — a
    drift would attempt an extraction no extractor can serve (regression
    guard for #822)."""
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
    actually build an extractor in sdk — and every key the check reports must
    be in the reported set. A drift fails the check wrongly (hosted
    available=True but sdk extractor=None → the capture ATTEMPTS an
    extraction nothing can serve: a partial write, then a 500/503)."""
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
                f"{k} must be reported available by the hosted provider check"
            assert _build_session_llm_extractor() is not None, \
                f"{k} is reported available by the hosted check but sdk builds NO extractor"
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
    fails the availability check OPEN (hosted says available, sdk builds
    None — a capture then attempts an extraction nothing can serve)."""
    from tortoise import hosted_api
    from tortoise.ingest import _PROVIDERS
    from tortoise.sdk import _SESSION_LLM_PROVIDER_PRIORITY

    for provider, (_url, key_env) in _PROVIDERS.items():
        assert provider in _SESSION_LLM_PROVIDER_PRIORITY, (
            f"provider {provider!r} registered in ingest._PROVIDERS but missing "
            f"from sdk._SESSION_LLM_PROVIDER_PRIORITY — the hosted provider "
            f"check would fail open for {key_env}"
        )
        assert key_env in hosted_api._LLM_PROVIDER_KEYS, (
            f"{key_env} (ingest provider {provider!r}) missing from "
            f"hosted_api._LLM_PROVIDER_KEYS — the gate would not see it"
        )


def test_analyze_keys_subset_of_session_keys():
    """#1197: every analyze._LLM_PROVIDERS key must be usable by the SESSION
    extractor — an analyze-only key (in analyze but not in ingest._PROVIDERS)
    would open the hosted provider check while sdk._build_session_llm_extractor
    builds None → mid-capture failure. A naive subset-vs-union check is
    tautological (_llm_provider_keys() unions analyze in by construction);
    the real invariant is: every analyze key must be an INGEST provider key."""
    from tortoise.ingest import _PROVIDERS  # noqa: I001
    from tortoise.analyze import _LLM_PROVIDERS

    ingest_keys = {key_env for _url, key_env in _PROVIDERS.values() if key_env}
    extra = set(_LLM_PROVIDERS) - ingest_keys
    assert not extra, (
        f"analyze-only key(s) {sorted(extra)} are not ingest provider keys — "
        f"they would open the hosted provider check while "
        f"sdk._build_session_llm_extractor cannot consume them; add them to "
        f"ingest._PROVIDERS or exclude them from the session key union"
    )


# ── capture_session honors the fail-closed / LLM-default contract ──────────


@pytest.fixture()
def client(monkeypatch):
    """TestClient with auth override + temp FalkorDBLite DB.

    Auth is overridden so the handler body actually runs — the extraction
    gate sits AFTER auth, so without a valid team the request would 401 before
    ever reaching the provider check (previously the test was vacuous: the
    `in (401, 503)` assertion could only ever observe the auth 401). The SDK patch lets the 200
    path (quota check, graph writes) run against an embedded temp DB.
    TORTOISE_SESSION_LLM_MOCK=1 installs the offline MockModel extractor so
    the LLM path runs with zero network.
    """
    from fastapi.testclient import TestClient  # noqa: I001
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app, get_current_org

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_org] = lambda: {
            # C5 #2114 (#2260): legacy tt_ class — scope-less key_id dicts 403
            # the data-plane gates otherwise (mirrors #2241 migration).
            "org_id": "test-team-722", "key_id": "test-key-722",
            "legacy_full_access": True, "tier": "free",
            # #4010: the sessions gate is fail-closed on a MISSING key now
            # (the lenient 1000 fallback is deleted) — the override must
            # carry the resolved value: unlimited → explicit None.
            "max_sessions": None,
        }
        monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch/_restore_tortoise_sdk_init copies).
        with patched_tortoise_sdk(db_path):
            # #1927: session_recording defaults ON (no consent gate) — the
            # explicit opt-in below keeps the capture-gate tests (422/200)
            # deterministic regardless of default drift. Provision the Team
            # node first (the state writer is MATCH...SET).
            ha_mod._make_sdk(namespace="registry")._get_registry().query(
                "CREATE (t:Team {id:$id, onboarding_state:$st})",
                params={"id": "test-team-722", "st": "{}"},
            )
            ha_mod._update_onboarding_state(
                "test-team-722", session_recording=True)
            with TestClient(app) as tc:
                yield tc


def test_no_provider_stores_and_surfaces_not_extracted(monkeypatch, client):
    """#3892 (owner ruling 2026-09-18): with NO provider key the hosted
    capture still STORES the session and its turns, skips ONLY the LLM
    extraction into memory points, and reports the visible "stored, not yet
    extracted" state — never a refusal, never a silent zero.

    This test is the mutation guard for that ruling: reinstating the old
    503-first refusal (or dropping the store) makes it RED.
    """
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    marker = "keylessstoreproofzzq"
    conv = [{"role": "user",
             "content": f"we decided the {marker} capture must store its turns"}]
    r = client.post("/v1/sessions", json={"conversation": conv})
    assert r.status_code == 200, r.text[:400]
    body = r.json()
    sid = body["session_id"]

    # (1) visible, truthful receipt — what was saved, what is missing, how to
    # enable the rest. Never silent, never an opaque failure.
    assert body["extraction_mode"] == "no-provider", body
    assert body["extracted"] == 0, body
    assert body["turns"] == 1, body
    assert body["errors"] == [], body
    warnings = " ".join(body["warnings"])
    assert "no LLM provider key" in warnings
    assert "STORED" in warnings and "searchable" in warnings

    # (2) it LANDED — the Session + its turn Point exist and are wired.
    import tortoise.hosted_api as ha_mod
    sdk = ha_mod._make_sdk(namespace="test-team-722")
    proj = sdk._get_proj()
    wired = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) RETURN count(t)",
        params={"sid": sid}).result_set
    assert wired[0][0] == 1, "the keyless capture must store its turn Point"

    # (3) distinguishable from a fully-processed capture — never a false
    # "fully saved to memory".
    st = proj.g.query(
        "MATCH (s:Session {id:$sid}) "
        "RETURN s.capture_ok, s.capture_extractor",
        params={"sid": sid}).result_set[0]
    assert st[0] is False and st[1] == "none", st

    # (4) retrievable through the EXISTING read path, with no key present.
    s = client.get("/v1/search", params={"q": marker})
    assert s.status_code == 200, s.text[:300]
    assert any(marker in (h.get("content") or "")
               for h in s.json()["results"]), s.json()


def test_keyless_session_is_extraction_upgradable_with_a_key(monkeypatch, client):
    """#3892 / #4007: the keyless session records lane "none" + capture_ok
    False, and the hosted retry gate accepts "none" — so a LATER capture of
    the SAME session_id WITH a key RE-EXTRACTS on explicit request, instead of
    silently replaying. Never automatic: only the re-capture triggers it."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    conv = [{"role": "user", "content": "we decided to ship the upgrade path"}]
    r1 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-keyless-upgrade"})
    assert r1.status_code == 200, r1.text[:400]
    assert r1.json()["extraction_mode"] == "no-provider", r1.json()

    # A key appears (explicit configuration) — the SAME session is re-captured
    # on request. Extraction must RUN (the #2335 TRUE retry), not replay.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    r2 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-keyless-upgrade"})
    assert r2.status_code == 200, r2.text[:400]
    assert r2.json()["extraction_mode"] not in ("no-provider", "replayed"), \
        r2.json()


def test_hosted_m2_lane_discloses_the_refused_keyless_retry(monkeypatch, client):
    """#4188 / #4007 parity with the SDK's
    `test_m2_lane_refuses_the_keyless_retry_and_says_so`: a keyless session
    re-captured WITH a key while the deployment is on the non-convergent M2
    lane REPLAYS (the retry stays refused), and the receipt says WHY — never
    the false "already captured" of a fully-extracted session."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    conv = [{"role": "user", "content": "we decided to ship the upgrade path"}]
    r1 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-hosted-m2-keyless"})
    assert r1.json()["extraction_mode"] == "no-provider", r1.json()

    # A key appears, but this deployment selects the M2 lane: the re-attempt
    # is refused (M2 is non-convergent) — and DISCLOSED.
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    r2 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-hosted-m2-keyless"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["extraction_mode"] == "replayed", body
    warnings = " ".join(body["warnings"])
    assert "stored WITHOUT a provider key" in warnings, warnings
    assert "no extraction has ever run" in warnings, warnings
    assert "TORTOISE_SESSION_EXTRACTOR=m2" in warnings, warnings


def test_metering_counts_a_grown_keyless_recapture(monkeypatch, client):
    """#4188 (review cycle 3): the turn loop runs for EVERY request, so a
    keyless re-capture of a GROWN transcript writes NEW turn Points even
    though its branch extracts nothing. It must be metered — keying the meter
    on which branch ran (not on the actual write) was a billing/abuse blind
    spot."""
    import tortoise.hosted_api as ha_mod
    meter: list = []
    monkeypatch.setattr(
        ha_mod, "_record_write_op",
        lambda org, nodes_written=0: meter.append(
            (org["org_id"], nodes_written)))

    # 1) keyed capture (mock seam on) succeeds → a fresh write is metered.
    one = [{"role": "user", "content": "first turn"}]
    r1 = client.post("/v1/sessions", json={
        "conversation": one, "session_id": "s-meter-grown"})
    assert r1.status_code == 200, r1.text
    assert meter, "a fresh capture must be metered"

    # 2) keyless re-capture of the SAME session with a GROWN transcript: the
    #    prior SUCCEEDED (so the retry gate does NOT fire) but the turn loop
    #    writes a new turn Point — metered on the actual write.
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    meter.clear()
    grown = [*one, {"role": "assistant", "content": "second turn"}]
    r2 = client.post("/v1/sessions", json={
        "conversation": grown, "session_id": "s-meter-grown"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["extraction_mode"] == "no-provider", r2.json()
    assert meter, \
        "a grown keyless re-capture writes a new turn Point and must be metered"


def test_keyless_recapture_of_unchanged_transcript_meters_nothing(
        monkeypatch, client):
    """#4188 (review cycle 1, P2): a KEYLESS re-capture of an UNCHANGED
    transcript is an idempotent no-op — its turn ids are deterministic, so the
    loop MERGEs onto the same Points and writes ZERO nodes. `retry_failed_
    capture` is nevertheless True for it (the prior lane is "none"), so a
    meter keyed on which branch ran fires a phantom write-op on every re-POST
    (and charges the full transcript length on the abuse leg) — the #1827
    class, reopened for the keyless shape. The meter must stay SILENT."""
    import tortoise.hosted_api as ha_mod
    meter: list = []
    monkeypatch.setattr(
        ha_mod, "_record_write_op",
        lambda org, nodes_written=0: meter.append(
            (org["org_id"], nodes_written)))

    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    conv = [{"role": "user", "content": "an unchanged keyless transcript"}]
    r1 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-meter-noop"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["extraction_mode"] == "no-provider", r1.json()

    # Re-POST the IDENTICAL transcript while still keyless: the retry gate is
    # armed (prior lane "none") but the turn loop writes nothing.
    meter.clear()
    r2 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-meter-noop"})
    assert r2.status_code == 200, r2.text
    assert not meter, (
        "an unchanged keyless re-capture writes ZERO nodes — the meter must "
        f"not fire (phantom write-op): {meter}"
    )


def test_prior_turn_count_read_failure_does_not_500_a_recapture(
        monkeypatch, client):
    """#4188 (review cycle 3): the prior-stored-turn read feeds ONLY the
    write-op meter, and it runs AFTER the Session MERGE has committed. A
    transient graph error there must not 500 the capture, and must not abort
    the keyless→keyed upgrade; it falls back to 0, which makes the meter
    OVER-count (the documented conservative posture) rather than skip."""
    import tortoise.hosted_api as ha_mod
    from tortoise.projection import _GuardedGraph

    meter: list = []
    monkeypatch.setattr(
        ha_mod, "_record_write_op",
        lambda org, nodes_written=0: meter.append(
            (org["org_id"], nodes_written)))

    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    conv = [{"role": "user", "content": "prior-read failure probe"}]
    r1 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-priorread"})
    assert r1.status_code == 200, r1.text

    real_query = _GuardedGraph.query

    def flaky(self, cypher, *a, **kw):
        if "is_episodic = true RETURN count(t)" in cypher:
            raise RuntimeError("transient graph error (test)")
        return real_query(self, cypher, *a, **kw)

    monkeypatch.setattr(_GuardedGraph, "query", flaky)
    meter.clear()
    r2 = client.post("/v1/sessions", json={
        "conversation": conv, "session_id": "s-priorread"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["extraction_mode"] == "no-provider", r2.json()
    assert meter, (
        "the failed prior-count read must fall back to 0 and let the meter "
        "OVER-count — never 500 and never silently skip"
    )


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

    # #3615: capture requires explicit consent — the credential alone is not it.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    # #3963: the CLI now spools before it uploads — isolate the spool so this
    # test never writes into the developer machine's real capture spool.
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
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

    # #3615: without the opt-in the function returns 1 for a DIFFERENT reason —
    # this test must exercise the extraction-mode branch, so ask for capture.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    # #3963: spool isolation (see the mode-error test above).
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
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

    # #3615: capture requires explicit consent — the credential alone is not it.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    # #3963: spool isolation (see the mode-error test above).
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
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


def test_cmd_session_capture_replayed_is_not_reported_as_not_extracted(
        tmp_path, monkeypatch, capsys):
    """#4188 (review cycle 2): only the `no-provider` mode means "not
    extracted". A `replayed` receipt means the PRIOR capture SUCCEEDED — its
    memory points DO exist — so emitting the not-extracted line for it is a
    false statement about the session. The no-provider mode must still
    disclose it. (Mutation guard: the pre-fix `not mode.startswith("llm")`
    predicate printed "Extraction: replayed" and fails the first assert.)"""
    import json

    from tortoise.__main__ import _cmd_session_capture, _parse_transcript

    # #3682: capture is opt-in, so a test that drives the capture path must
    # consent explicitly (the credential alone no longer does).
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    # #3963: spool isolation (see the mode-error test above).
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))

    def _run(mode, tag):
        # A DISTINCT transcript per run. The session id is derived from the
        # transcript path and the spool is idempotent per session, so reusing
        # one file made the second run a dedup no-op ("Spooled session", on
        # stdout) that never reached the reporting path this test exists to
        # pin — the stderr assertion then passed for the wrong reason.
        f = tmp_path / f"transcript-{tag}.txt"
        f.write_text("User: we decided to ship it\nAssistant: agreed\n")
        assert _parse_transcript(f.read_text())
        payload = {"session_id": f"s-mode-{tag}", "extraction_mode": mode,
                   "extracted": 0, "points": [], "errors": [],
                   "warnings": []}

        class _FakeResp:
            def read(self):
                return json.dumps(payload).encode()

        class _FakeCtx:
            def __enter__(self):
                return _FakeResp()

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen",
                            lambda req, timeout: _FakeCtx())
        capsys.readouterr()  # drain
        args = type("A", (), {"file": str(f)})()
        assert _cmd_session_capture(args, "api-key", "http://api") == 0
        return capsys.readouterr().err

    # a REPLAY: the prior capture succeeded — never claim "not extracted".
    assert "Extraction:" not in _run("replayed", "a")
    # a KEYLESS store: this IS the not-extracted state — must be disclosed.
    assert "Extraction: no-provider" in _run("no-provider", "b")
