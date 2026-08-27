"""Supabase-mode onboarding/GitHub/health flip tests (#764, plan Tasks 6-7).

Endpoint-level (TestClient over the real app, REAL get_current_team resolving
against the in-memory FakeControlPlane) coverage of the Task 6-7 seams:

- onboarding_state read-patch round-trips from teams (jsonb — no string
  wrapping), E2E-5.
- GitHub connect callback stores github_token_enc + github_org on the teams
  row via the service-role seam (the column is REVOKEd from
  anon/authenticated in migration 0006); status reads them back via the same
  seam, E2E-5.
- /health/security reports the ACTUAL lookup scheme per mode (Task 7).
- /health/ready = AND(Supabase control plane, FalkorDB data plane) — fake
  up → ready, fake down → 503, E2E-8.

Env-gated exactly like test_auth_flip: SUPABASE_URL + service key set →
Supabase mode; unset → registry (registry-mode assertions below).
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

from tortoise.hosted_api import app  # noqa: I001

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import FREE_TEAM, TOKEN, _key_row


def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def _patch_tortoise_sdk_init(db_path: str):
    """Make hosted_api's TortoiseSDK use a temp embedded DB so the
    /health/ready FalkorDB probe doesn't touch prod."""
    import tortoise.hosted_api as ha_mod
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig


@pytest.fixture
def supabase_client(monkeypatch):
    """TestClient over the real app in Supabase mode with one fake team.

    Auth is REAL (no dependency override): the seeded api_keys row resolves
    via resolve_api_key against the fake — E2E-5 exercises the full auth →
    onboarding path.
    """
    fake = FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM, email="owner@example.com",
                       onboarding_state={}, github_token_enc=None,
                       github_org=None)],
    })
    fake.seed("api_keys", [_key_row()])
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "flip.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                tc.headers.update({"Authorization": f"Bearer {TOKEN}"})
                yield tc, fake
        finally:
            import tortoise.hosted_api as ha_mod
            ha_mod.TortoiseSDK.__init__ = _orig
            app.dependency_overrides.clear()


def _registry_client():
    """TestClient in registry mode (no Supabase creds)."""
    import tortoise.hosted_api as ha_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "reg.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            ha_mod.TortoiseSDK.__init__ = _orig
            app.dependency_overrides.clear()


# ── Onboarding state (E2E-5: read-patch from teams) ─────────────────────────

class TestOnboardingStateFlip:
    def test_read_returns_defaults_for_empty_state(self, supabase_client):
        tc, fake = supabase_client  # noqa: RUF059
        r = tc.get("/v1/onboarding/state")
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"] == {
            "github_connected": False, "github_indexed": False,
            "demo_created": False, "session_recording": False,
            "team_created": False, "prompt_pasted": False,
            "onboarding_complete": False,
            # #1725 (Slice 0): registered in _ONBOARDING_DEFAULT_STATE.
            "github_index_cursor": None,
            "github_legacy_backfill_done": False,
        }

    def test_patch_lands_on_teams_jsonb_and_reads_back(self, supabase_client):
        tc, fake = supabase_client
        r = tc.patch("/v1/onboarding/state", json={"demo_created": True,
                                                   "prompt_pasted": True})
        assert r.status_code == 200, r.text
        body = r.json()["onboarding"]
        assert body["demo_created"] is True and body["prompt_pasted"] is True
        # stored as a real JSON object on the teams row (jsonb — no string)
        stored = fake.tables["teams"][0]["onboarding_state"]
        assert isinstance(stored, dict)
        assert stored["demo_created"] is True
        # read-back through the endpoint reflects the patch
        r = tc.get("/v1/onboarding/state")
        assert r.json()["onboarding"]["demo_created"] is True

    def test_patch_ignores_unknown_keys(self, supabase_client):
        tc, _ = supabase_client
        r = tc.patch("/v1/onboarding/state", json={"not_a_field": 1})
        assert r.status_code == 200, r.text
        assert "not_a_field" not in r.json()["onboarding"]

    def test_email_read_patch_via_onboarding_endpoint(self, supabase_client):
        """E2E-5: email read-patch from teams via the onboarding endpoints
        (#764 review P2 — the email seam is wired, not dead code)."""
        tc, fake = supabase_client
        # read: fixture seeds owner@example.com on the teams row
        r = tc.get("/v1/onboarding/state")
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "owner@example.com"
        # patch: email lands on the teams row via the seam
        r = tc.patch("/v1/onboarding/state", json={"email": "owner@premise-labs.dev"})
        assert r.status_code == 200, r.text
        assert r.json()["email"] == "owner@premise-labs.dev"
        assert fake.tables["teams"][0]["email"] == "owner@premise-labs.dev"
        # read-back reflects it
        r = tc.get("/v1/onboarding/state")
        assert r.json()["email"] == "owner@premise-labs.dev"

    def test_session_recording_toggle_lands_on_teams(self, supabase_client):
        tc, fake = supabase_client
        r = tc.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"]["session_recording"] is True
        assert fake.tables["teams"][0]["onboarding_state"]["session_recording"] is True


# ── GitHub connect (E2E-5: token_enc + org via the seam) ────────────────────

class TestGithubConnectFlip:
    def test_callback_stores_token_enc_and_org_on_teams(self, supabase_client,
                                                        monkeypatch):
        """Connect → callback: the OAuth exchange result lands as an ENCRYPTED
        token + org on the teams row (service-role seam), and onboarding state
        marks github_connected. The raw token never appears on the row."""
        tc, fake = supabase_client

        async def _fake_exchange(code: str) -> str:
            return "gho_raw_access_token_123"

        import tortoise.hosted_api as ha
        monkeypatch.setattr(ha, "_exchange_github_token", _fake_exchange)

        r = tc.post("/v1/onboarding/github/connect", json={})
        assert r.status_code == 200, r.text
        state = r.json()["state"]

        r = tc.get(f"/v1/onboarding/github/callback?code=test-code&state={state}",
                   follow_redirects=False)
        assert r.status_code == 302, r.text
        assert "github=connected" in r.headers["location"]

        row = fake.tables["teams"][0]
        # encrypted blob stored — never the raw token
        assert row["github_token_enc"] is not None
        assert row["github_token_enc"] != "gho_raw_access_token_123"
        assert row["github_org"] == "team-free-001"  # default org = team_id
        # onboarding state marked connected through the same seam
        assert row["onboarding_state"]["github_connected"] is True

    def test_status_reads_via_seam_when_connected(self, supabase_client,
                                                  monkeypatch):
        """github_status decrypts + reports org/repos from the teams row —
        no registry access (E2E-5 'github status reads work')."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        tc, fake = supabase_client
        fake.tables["teams"][0].update({
            "github_token_enc": encrypt_token("gho_token_for_status"),
            "github_org": "acme",
        })
        monkeypatch.setattr(ha, "_github_repos_count", lambda token: 7)
        r = tc.get("/v1/onboarding/github/status")
        assert r.status_code == 200, r.text
        assert r.json() == {"connected": True, "org": "acme", "repos_count": 7}

    def test_status_not_connected_when_no_token(self, supabase_client):
        tc, _ = supabase_client
        r = tc.get("/v1/onboarding/github/status")
        assert r.status_code == 200, r.text
        assert r.json() == {"connected": False, "org": None, "repos_count": None}

    def test_index_requires_connected(self, supabase_client):
        tc, _ = supabase_client
        r = tc.post("/v1/index/github", json={"org": "acme"})
        assert r.status_code == 400  # not connected — seam read found no token

    def test_callback_fails_closed_on_control_plane_outage(
            self, supabase_client, monkeypatch):
        """Control plane down during the callback → 500 (RuntimeError
        propagates) — never a success redirect, never a registry fallback."""
        import tortoise.hosted_api as ha
        import tortoise.supabase_control as sc

        async def _fake_exchange(code: str) -> str:
            return "gho_raw_access_token_123"

        monkeypatch.setattr(ha, "_exchange_github_token", _fake_exchange)
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        # raise_server_exceptions=False: the RuntimeError the seam raises is a
        # real 500 in production — assert it as an HTTP response.
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "flip500.db")
            _orig = _patch_tortoise_sdk_init(db_path)
            try:
                with TestClient(app, raise_server_exceptions=False) as tc:
                    # Seed the CSRF state directly (connect itself needs auth,
                    # which also fails closed under the error plane — the
                    # callback is the public leg under test).
                    import time as _time
                    ha._GITHUB_STATES["test-state-1"] = {
                        "team_id": "team-free-001", "org": "team-free-001",
                        "created_at": _time.time(),
                    }
                    r = tc.get("/v1/onboarding/github/callback?code=test-code&state=test-state-1",
                               follow_redirects=False)
                    assert r.status_code == 500
            finally:
                ha.TortoiseSDK.__init__ = _orig
                app.dependency_overrides.clear()


# ── Health endpoints (Task 7 / E2E-8) ───────────────────────────────────────

class TestHealthSecurity:
    def test_registry_mode_reports_salted_pbkdf2(self, monkeypatch):
        """Backward-compatible keys unchanged; scheme additive."""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        for tc in _registry_client():
            r = tc.get("/health/security")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["hashing"] == "pbkdf2_hmac_sha256"
            assert "pepper_configured" in body
            assert body["scheme"] == "salted_pbkdf2_hmac_sha256"
            assert "lookup" in body

    def test_supabase_mode_reports_lookup_hash(self, supabase_client):
        tc, _ = supabase_client
        r = tc.get("/health/security")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["scheme"] == "lookup_hash_sha256"
        assert "sha256(pepper + key)" in body["lookup"]
        # backward-compatible keys preserved in both modes
        assert body["hashing"] == "pbkdf2_hmac_sha256"
        assert "pepper_configured" in body
        assert isinstance(body["api_auth_enforced"], bool)


class TestHealthReady:
    def test_supabase_mode_ready_when_both_planes_up(self, supabase_client):
        tc, _ = supabase_client
        r = tc.get("/health/ready")
        assert r.status_code == 200, r.text
        assert r.json() == {"status": "ok", "db": "connected",
                            "control_plane": "connected"}

    def test_supabase_mode_not_ready_when_data_plane_down(
            self, monkeypatch, supabase_client):
        """AND gate, first leg: FalkorDB down + control plane up → 503
        "Database unreachable" (never 200, even though the control plane is
        fine)."""
        import tortoise.hosted_api as ha

        def _boom_sdk(**kwargs):
            raise RuntimeError("FalkorDB unreachable (simulated)")

        monkeypatch.setattr(ha, "_make_sdk", _boom_sdk)
        tc, _ = supabase_client
        r = tc.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["detail"] == "Database unreachable"

    def test_supabase_mode_not_ready_when_control_plane_down(
            self, monkeypatch, supabase_client):
        """Fail-closed: control-plane outage → 503 ready=false (never 200)."""
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _ = supabase_client
        r = tc.get("/health/ready")
        assert r.status_code == 503
        assert r.json()["detail"] == "Control plane unreachable"

    def test_registry_mode_unchanged(self, monkeypatch):
        """Registry mode probes FalkorDB only (today's behavior) — no
        control_plane key, no Supabase requirement."""
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        for tc in _registry_client():
            r = tc.get("/health/ready")
            assert r.status_code == 200, r.text
            assert r.json() == {"status": "ok", "db": "connected"}
