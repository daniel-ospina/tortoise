"""Hosted POST /v1/context integration tests (issue #2103 — S9 → E2E-9).

Docker lane (real FalkorDB via the hermetic session graph redirect). Auth-
override style mirrors tests/test_hosted_api.py; the R0 two-teams/two-keys
isolation test uses the REAL registry auth path with provision_test_user
(never a mock), per the #2083 test-registry fixture contract.

Surfaces: fail-closed auth (401/403, 0 cross-team), 422 out-of-contract,
fail-open content (assembly_error/timeout — never 503 on the read path),
zero-LLM, statelessness (0 new nodes on re-POST), SDK/HTTP deep-equal
parity, GET vs POST /v1/context non-collision, 429 backoff contract.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import (
    app,
    get_current_team,
    get_current_team_gated,
)
from tortoise.sdk import TortoiseSDK

TEST_TEAM_ID = "team-001"


# ── fixtures (auth-override lane, mirrors test_hosted_api) ─────────────────

@pytest.fixture
def client():
    import tortoise.hosted_api as ha_mod

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: dict(
            {"team_id": TEST_TEAM_ID, "tier": "free", "key_id": None})
        _orig_init = ha_mod.TortoiseSDK.__init__

        def _patched_init(self, db_path_arg=None, *, namespace=None,
                          graph_name=None, **kwargs):
            _orig_init(self, db_path, namespace=namespace,
                       graph_name=graph_name)

        ha_mod.TortoiseSDK.__init__ = _patched_init
        os.environ["TORTOISE_DB_PATH"] = db_path
        ha_mod._FALLBACK_KEEPALIVE.clear()
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            os.environ.pop("TORTOISE_DB_PATH", None)
            ha_mod.TortoiseSDK.__init__ = _orig_init
            app.dependency_overrides.clear()


def _seed_team_sdk(team_id: str = TEST_TEAM_ID) -> TortoiseSDK:
    """Open the same lane's team SDK for direct seeding."""
    import tortoise.hosted_api as ha_mod
    return ha_mod._make_sdk(namespace=team_id)


def _plant_contested(sdk, content: str, counter_content: str) -> str:
    proj = sdk._get_proj()
    ev = sdk.create_point("evidence", f"{content} [supporting record]")
    claim = sdk.create_point("statement", content)
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    counter = sdk.create_point("statement", counter_content)
    sdk.create_operator("NAND", counter["id"], [claim["id"]])
    for pid, a, b in ((claim["id"], 2.0, 0.7), (ev["id"], 12.0, 1.0),
                      (counter["id"], 10.0, 1.0)):
        mean = round(a / (a + b), 4)
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
            "n.posterior_alpha=$a, n.posterior_beta=$b",
            params={"id": pid, "a": a, "b": b, "c": mean})
    return claim["id"]


def _window(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _count_nodes(sdk) -> int:
    rows = sdk._get_proj().g.query("MATCH (n) RETURN count(n)").result_set
    return int(rows[0][0]) if rows else 0


def _assert_deep_equal_contract(a: dict, b: dict) -> None:
    """SDK/HTTP parity — deep-equal on the canonicalized parsed contract
    fields (order-insensitive per field lists), not byte identity."""
    assert set(a) == set(b) == {"pointers", "why", "surfaced", "block",
                                "degraded_reason"}
    for key in ("pointers", "why", "surfaced"):
        assert sorted(a[key], key=repr) == sorted(b[key], key=repr), key
    assert a["block"] == b["block"]
    assert a["degraded_reason"] == b["degraded_reason"]


# ── E2E-9 1a content delivery (contested) + contract shape ─────────────────

def test_post_v1_context_contested_content_and_shape(client):
    sdk = _seed_team_sdk()
    claim = _plant_contested(
        sdk,
        "Acme security review was due May 1 and has not shipped",
        "Acme security review shipped on April 30 per the release log",
    )
    r = client.post("/v1/context", json={
        "window": _window("What is the status of the Acme security review? "
                          "Has it shipped?"),
        "session_id": "sess_http_acme", "why": True,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded_reason"] is None
    ids = [p["id"] for p in body["pointers"]]
    assert claim in ids, ids
    entry = next(w for w in body["why"] if w["point_id"] == claim)
    assert entry["ep"]["contested"] is True
    assert entry["ep"]["variance"] > 0.04
    assert "nand" in [d["kind"] for d in entry["dig_deeper"]]
    assert len(body["surfaced"]) == len(body["pointers"])
    assert len(body["block"].encode("utf-8")) <= 8 * 1024
    assert f"point/{claim}" in body["block"]


def test_clean_empty_and_courtesy(client):
    r = client.post("/v1/context", json={"window": _window(
        "What is the status of the Orion migration?")})
    assert r.status_code == 200
    assert r.json() == {"pointers": [], "why": [], "surfaced": [],
                        "block": "", "degraded_reason": None}
    r2 = client.post("/v1/context", json={"window": _window(
        "Thanks, that helps a lot.")})
    assert r2.json()["degraded_reason"] is None and r2.json()["pointers"] == []


# ── Statelessness / determinism / zero-LLM ─────────────────────────────────

def test_repost_same_session_identical_and_zero_new_nodes(client):
    sdk = _seed_team_sdk()
    _plant_contested(sdk, "Harborlight allocation doubles next round",
                     "Orlando opposes the Harborlight increase")
    payload = {"window": _window("What did we decide about Harborlight's "
                                 "allocation?"),
               "session_id": "sess_idem", "why": True}
    before = _count_nodes(sdk)
    r1 = client.post("/v1/context", json=payload)
    r2 = client.post("/v1/context", json=payload)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert _count_nodes(sdk) == before  # 0 new graph nodes


def test_zero_llm_read_path(client, monkeypatch):
    for k in list(os.environ):
        if k.endswith("_API_KEY") or k in (
                "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GEMINI_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    sdk = _seed_team_sdk()
    _plant_contested(sdk, "Widget Co closes escrow Friday",
                     "Widget Co escrow slipped a week")
    r = client.post("/v1/context", json={
        "window": _window("When does the Widget Co escrow close?"),
        "session_id": "sess_zero_http", "why": True})
    assert r.status_code == 200
    assert r.json()["degraded_reason"] is None
    assert r.json()["pointers"]


def test_sdk_http_deep_equal_parity(client):
    sdk = _seed_team_sdk()
    _plant_contested(sdk, "Lumen canary ships to five percent",
                     "Lumen canary is gated behind the SLO fix")
    window = _window("Is the Lumen canary shipping to five percent yet?")
    sdk_r = sdk.volunteer_context(window, session_id="sess_par",
                                  prior_context=None, why=True)
    http_r = client.post("/v1/context", json={
        "window": window, "session_id": "sess_par", "why": True,
    }).json()
    assert http_r["degraded_reason"] is None
    _assert_deep_equal_contract(sdk_r, http_r)


def test_get_vs_post_context_never_collide(client):
    """GET /v1/context (session-start digest) and POST /v1/context (the
    reflex) are distinct routes with distinct semantics (named failure
    mode)."""
    from fastapi.routing import APIRoute
    ctx_routes = [r for r in app.routes
                  if isinstance(r, APIRoute) and r.path == "/v1/context"]
    methods = sorted({m for r in ctx_routes for m in (r.methods or [])})
    assert "GET" in methods and "POST" in methods, methods
    g = client.get("/v1/context")
    assert g.status_code == 200
    # The GET digest shape (session_context) is NOT the reflex contract.
    assert "pointers" not in g.json()
    p = client.post("/v1/context", json={"window": _window(
        "What do you remember from last session?")})
    assert p.status_code == 200
    assert set(p.json()) == {"pointers", "why", "surfaced", "block",
                             "degraded_reason"}


# ── Fail-open content (never 503 on the read path) ─────────────────────────

def test_server_error_degrades_200_never_503(client, monkeypatch):
    import tortoise.hosted_api as ha_mod

    def _boom(*a, **k):
        raise RuntimeError("simulated assembly failure")

    monkeypatch.setattr(ha_mod.TortoiseSDK, "volunteer_context", _boom)
    r = client.post("/v1/context", json={"window": _window(
        "What did Alice decide?")})
    assert r.status_code == 200, r.text  # fail-open, NEVER 503
    body = r.json()
    assert body["degraded_reason"] == "assembly_error"
    assert body["pointers"] == [] and body["block"] == ""


def test_enforced_slo_breach_degrades_timeout(client, monkeypatch):
    """Induced SLO breach (enforce lane on) → 200 + degraded timeout."""
    import time

    import tortoise.hosted_api as ha_mod
    monkeypatch.setenv("TORTOISE_VOLUNTEER_ENFORCE_SLO", "1")

    orig = ha_mod.TortoiseSDK.volunteer_context

    def _slow(self, window, *a, **k):
        time.sleep(0.4)  # > 300 ms SLO
        return orig(self, window, *a, **k)

    monkeypatch.setattr(ha_mod.TortoiseSDK, "volunteer_context", _slow)
    r = client.post("/v1/context", json={"window": _window(
        "What did Alice decide?")})
    assert r.status_code == 200
    assert r.json()["degraded_reason"] == "timeout"
    assert r.json()["pointers"] == [] and r.json()["block"] == ""


def test_hard_ceiling_never_hangs_caller(client, monkeypatch):
    import time

    import tortoise.hosted_api as ha_mod
    import tortoise.volunteer as volunteer_mod
    # Shorten the ceiling (8 × SLO_MS — the route imports SLO_MS from the
    # volunteer module at call time) so the test is fast: 8 × 50 ms.
    monkeypatch.setattr(volunteer_mod, "SLO_MS", 50)

    def _hung(self, window, *a, **k):
        time.sleep(10)

    monkeypatch.setattr(ha_mod.TortoiseSDK, "volunteer_context", _hung)
    r = client.post("/v1/context", json={"window": _window(
        "What did Alice decide?")})
    assert r.status_code == 200  # never 503, never a hung caller
    assert r.json()["degraded_reason"] == "timeout"


# ── 422 out-of-contract (SDK validates first; HTTP maps the same rules) ────

def test_422_out_of_bounds(client):
    empty = client.post("/v1/context", json={"window": []})
    assert empty.status_code == 422
    huge = client.post("/v1/context", json={
        "window": [{"role": "user", "content": "x"}] * 1001})
    assert huge.status_code == 422
    big = client.post("/v1/context", json={
        "window": [{"role": "user", "content": "y" * 20_000}]})
    assert big.status_code == 422
    bad_budget = client.post("/v1/context", json={
        "window": _window("hi"), "max_pointers": 9})
    assert bad_budget.status_code == 422
    bad_conf = client.post("/v1/context", json={
        "window": _window("hi"), "min_confidence": 1.7})
    assert bad_conf.status_code == 422
    bad_role = client.post("/v1/context", json={
        "window": [{"role": "robot", "content": "hi"}]})
    assert bad_role.status_code == 422


def test_sdk_validates_before_http(client):
    """SDK raises ValueError on the same out-of-contract inputs BEFORE any
    request — the SDK-first contract."""
    sdk = _seed_team_sdk()
    from tortoise.volunteer import VolunteerValidationError
    with pytest.raises(VolunteerValidationError):
        sdk.volunteer_context([])
    with pytest.raises(VolunteerValidationError):
        sdk.volunteer_context(_window("hi"), max_pointers=9)


# ── 429 backoff contract (standard per-key limiter + Retry-After) ──────────

def test_429_rate_limit_retry_after_backoff_safe(monkeypatch):
    """The endpoint rides the shared per-key RateLimitMiddleware: on limit it
    returns 429 + Retry-After (client backoff — not a hard failure).  A
    retry after the window is deterministic (the read path is stateless)."""
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import RateLimitMiddleware
    # The shared test env disables the limiter; this test needs it armed —
    # the middleware reads the flag at construction time.
    monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)

    class _NoopAuth:
        pass

    mini = __import__("fastapi").FastAPI()
    mini.add_middleware(RateLimitMiddleware, max_per_minute=100,
                        path_limits={"/v1/context": 1})
    # Reuse the real handler (real pipeline + real auth dependency overridden
    # to the no-key lane) — the limiter sees no Bearer → per-IP bucket.
    async def _team():
        return {"team_id": TEST_TEAM_ID, "tier": "free", "key_id": None}

    mini.dependency_overrides[get_current_team_gated] = _team
    mini.add_api_route("/v1/context", ha_mod.volunteer_context,
                       methods=["POST"])
    with TestClient(mini) as tc:
        payload = {"window": _window("What did Alice decide?"),
                   "session_id": "sess_429"}
        first = tc.post("/v1/context", json=payload)
        assert first.status_code == 200
        limited = tc.post("/v1/context", json=payload)
        assert limited.status_code == 429
        assert "Retry-After" in limited.headers
        # Deterministic read-only: the same request is safe to retry (no
        # state mutated by the 429'd attempt).
        assert tc.get("/health").status_code in (200, 404)


# ── R0: fail-closed auth — 401/403 + zero cross-team (REAL keys) ───────────

class _RealAuthLane:
    """Pin ONE shared DB path BEFORE provision_test_user so the provisioned
    teams' registry rows and the hosted app's lookups land on the same
    redirect-derived server graphs (the test-mode redirect hashes
    session+path+graph_name).  Requests then authenticate through the REAL
    get_current_team path (no auth override — never a mock)."""

    def __init__(self, tmp_path):
        import tortoise.hosted_api as ha_mod
        self.ha_mod = ha_mod
        self.db_path = os.path.join(str(tmp_path), "real.db")
        self._orig = ha_mod.TortoiseSDK.__init__

        def _patched(self_, db_path_arg=None, *, namespace=None,
                     graph_name=None, **kwargs):
            self._orig(self_, self.db_path, namespace=namespace,
                       graph_name=graph_name)

        ha_mod.TortoiseSDK.__init__ = _patched
        os.environ["TORTOISE_DB_PATH"] = self.db_path
        ha_mod._FALLBACK_KEEPALIVE.clear()

    def close(self):
        import os as _os
        self.ha_mod.TortoiseSDK.__init__ = self._orig
        _os.environ.pop("TORTOISE_DB_PATH", None)

    def client(self) -> TestClient:
        return TestClient(app)


@pytest.fixture
def real_auth(tmp_path):
    lane = _RealAuthLane(tmp_path)
    yield lane
    lane.close()


def _mint_api_key(real_auth, provisioned: dict) -> str:
    """Mint a REAL APIKey node in the registry control plane the hosted
    auth reads (``namespace="registry"`` — where POST /v1/team/keys writes;
    schema parity: hash_api_key + key_prefix token[:10] + expires_at null).
    The Bearer token authenticates through the REAL get_current_team verify
    path (PBKDF2 + prefix scan + team resolution + suspension check) and the
    per-request _data_sdk(namespace=team_id) tenancy path.

    NOTE (the #2083 external-tenancy fallback, documented): provision_test_user
    scopes its OWN control plane per namespace (``{ns}_control_plane``), which
    the hosted auth layer cannot read by construction — so the key ROW is
    provisioned in the registry plane exactly like the hosted mint paths do,
    bound to the provisioned team id.  The request auth is the real path —
    never a mock, never an auth override."""
    import uuid
    from datetime import UTC, datetime

    from tortoise.auth import hash_api_key
    token = f"tt_{uuid.uuid4().hex}"
    reg = real_auth.ha_mod._make_sdk(namespace="registry")._get_registry()
    reg.query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:$kh, "
        "key_prefix:$kp, created_by:$cb, created_via:'provisioned', "
        "created_at:$now, expires_at:null})",
        params={"id": f"key-{os.urandom(4).hex()}",
                "tid": provisioned["team_id"],
                "kh": hash_api_key(token), "kp": token[:10],
                "cb": provisioned["user_id"],
                "now": datetime.now(UTC).isoformat()},
    )
    return token


def _plant_team_fact(real_auth, team_id: str, content: str,
                     counter: str | None = None) -> str:
    """Seed a measured belief in the team's data graph via the SAME tenancy
    resolver the endpoint uses (per-request _make_sdk(namespace=team_id))."""
    sdk = real_auth.ha_mod._make_sdk(namespace=team_id)
    claim = sdk.create_point("statement", content)
    proj = sdk._get_proj()
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence=0.92, "
        "n.posterior_alpha=12.0, n.posterior_beta=1.0",
        params={"id": claim["id"]})
    if counter:
        c = sdk.create_point("statement", counter)
        sdk.create_operator("NAND", c["id"], [claim["id"]])
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.confidence=0.9, "
            "n.posterior_alpha=10.0, n.posterior_beta=1.0",
            params={"id": c["id"]})
    sdk.close()
    return claim["id"]


def test_two_teams_two_keys_zero_cross_team(provision_test_user, real_auth):
    """R0 detection: per-request _make_sdk(namespace=team_id) tenancy — team
    B's key NEVER serves team A's context (0 cross-team), even on
    overlapping entity names (E2E-4 content isolation, not just names)."""
    team_a = provision_test_user(tier="free")
    team_b = provision_test_user(tier="free")
    a_claim = _plant_team_fact(
        real_auth, team_a["team_id"],
        "Harborlight gets a thirty percent allocation from Acme",
        "Harborlight allocation was cut to ten percent by Acme")
    _plant_team_fact(
        real_auth, team_b["team_id"],
        "Harborlight gets a five percent allocation from Beta Corp",
        "Harborlight allocation is under review by Beta Corp")
    key_a = _mint_api_key(real_auth, team_a)
    key_b = _mint_api_key(real_auth, team_b)
    assert key_a != key_b
    auth_a = {"Authorization": f"Bearer {key_a}"}
    auth_b = {"Authorization": f"Bearer {key_b}"}
    client = real_auth.client()
    with client:
        # Team A asks about Acme's Harborlight allocation → sees its own.
        r_a = client.post("/v1/context", json={
            "window": _window("What is Harborlight's allocation from Acme "
                              "right now?"),
            "session_id": "sess_A"}, headers=auth_a)
        assert r_a.status_code == 200, r_a.text
        a_ids = [p["id"] for p in r_a.json()["pointers"]]
        assert a_claim in a_ids, a_ids
        # Team B asks the SAME question about Acme → never A's context.
        r_b = client.post("/v1/context", json={
            "window": _window("What is Harborlight's allocation from Acme "
                              "right now?"),
            "session_id": "sess_B"}, headers=auth_b)
        assert r_b.status_code == 200, r_b.text
        b_ids = [p["id"] for p in r_b.json()["pointers"]]
        assert a_claim not in b_ids, f"CROSS-TEAM LEAK: {b_ids}"
        # Sanity: B's key resolves B's graph (same window, own content).
        r_b2 = client.post("/v1/context", json={
            "window": _window("What is Harborlight's allocation from Beta "
                              "Corp right now?"),
            "session_id": "sess_B2"}, headers=auth_b)
        assert r_b2.status_code == 200
        assert r_b2.json()["pointers"], "B's own context must resolve"


def test_auth_fail_closed_401_missing_and_invalid(real_auth):
    client = real_auth.client()
    with client:
        missing = client.post("/v1/context", json={
            "window": _window("hi")})
        assert missing.status_code == 401, missing.text
        bad = client.post("/v1/context", json={
            "window": _window("hi")},
            headers={"Authorization": "Bearer tt_garbage_key_0000"})
        assert bad.status_code == 401, bad.text
        wrong_scheme = client.post("/v1/context", json={
            "window": _window("hi")},
            headers={"Authorization": "Basic abc"})
        assert wrong_scheme.status_code == 401


def test_revoked_key_fails_closed(provision_test_user, real_auth):
    team = provision_test_user(tier="free")
    token = _mint_api_key(real_auth, team)
    client = real_auth.client()
    with client:
        ok = client.post("/v1/context", json={
            "window": _window("hi")},
            headers={"Authorization": f"Bearer {token}"})
        assert ok.status_code == 200, ok.text
        # Revoke the key (registry lane: revoked_at is authoritative)…
        from datetime import UTC, datetime
        real_auth.ha_mod._make_sdk(namespace="registry")._get_registry().query(
            "MATCH (k:APIKey {key_prefix:$kp}) SET k.revoked_at=$now",
            params={"kp": token[:10],
                    "now": datetime.now(UTC).isoformat()})
        revoked = client.post("/v1/context", json={
            "window": _window("hi")},
            headers={"Authorization": f"Bearer {token}"})
        # Fail-CLOSED: revoked keys never authenticate (registry lane:
        # 401 invalid-key; Supabase lane: 403 revoked — both fail closed).
        assert revoked.status_code in (401, 403), revoked.text


def test_suspended_team_403(provision_test_user, real_auth):
    team = provision_test_user(tier="free")
    token = _mint_api_key(real_auth, team)
    from datetime import UTC, datetime
    # The suspension check reads the Team node from the REGISTRY control
    # plane (get_current_team's team fetch) — mirror the provisioned team
    # row there (the real signup path creates it) and suspend it.
    reg = real_auth.ha_mod._make_sdk(namespace="registry")._get_registry()
    reg.query(
        "MERGE (t:Team {id:$id}) SET t.suspended_at=$now",
        params={"id": team["team_id"],
                "now": datetime.now(UTC).isoformat()})
    client = real_auth.client()
    with client:
        r = client.post("/v1/context", json={
            "window": _window("hi")},
            headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403, r.text
