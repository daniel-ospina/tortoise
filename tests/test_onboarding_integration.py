"""Onboarding E2E integration tests (#502) — verify the full flow works.

Tests the hosted onboarding journey end-to-end against the built endpoints
(#498/#499/#500/#501): register → key → onboarding state → demo graph →
session recording → completion. Uses embedded FalkorDBLite.

These are the integration-gate tests for epic #235. They run against the
real hosted_api app with mocked external services (GitHub, Supabase).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app
from tortoise.sdk import TortoiseSDK  # noqa: F401


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with env-based embedded DB + auth override.

    Sets TORTOISE_DB_PATH so _make_sdk (which reads env) uses the temp DB
    — the hosted_api endpoints resolve the registry through _make_sdk.
    """
    db_path = str(tmp_path / "e2e.db")
    monkeypatch.setenv("TORTOISE_DB_PATH", db_path)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    # Seed a team in the registry graph the endpoints will read.
    # team_create generates a ULID id — capture it for the auth override.
    from tortoise.hosted_api import _make_sdk
    sdk = _make_sdk(namespace="registry")
    try:
        team = sdk.team_create("e2e-team")
    except Exception:
        # Already exists — look it up
        rows = sdk._get_registry().query(
            "MATCH (t:Team {name: $name}) RETURN t.id",
            params={"name": "e2e-team"}).result_set
        team = {"id": rows[0][0]}
    real_team_id = team["id"]
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": real_team_id, "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1,
    }
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()


class TestOnboardingJourney:
    """The full yes/no onboarding journey (E2E-1 through E2E-7)."""

    def test_e2e_register_to_key(self, client):
        """E2E-1: Self-service register returns a usable API key."""
        r = client.post("/v1/register", json={"email": "dev@example.com",
                                              "password": "password123"})
        assert r.status_code == 200
        body = r.json()
        assert "api_key" in body and body["api_key"].startswith("tt_")
        assert "team_id" in body

    def test_e2e_register_idempotent(self, client):
        """Registering twice returns already_registered (409) without re-key."""
        r1 = client.post("/v1/register", json={"email": "a@b.com", "password": "password123"})
        assert r1.status_code == 200
        assert "api_key" in r1.json()
        key1 = r1.json()["api_key"]  # noqa: F841
        r2 = client.post("/v1/register", json={"email": "a@b.com", "password": "password123"})
        # 409 with already_registered message, no new key leaked
        assert r2.status_code == 409
        assert r2.json().get("detail", {}).get("message") == "already_registered"
        assert "api_key" not in str(r2.json().get("detail"))

    def test_e2e_onboarding_state_defaults(self, client):
        """E2E-7: State auto-initializes with defaults (Q6 verification)."""
        r = client.get("/v1/onboarding/state")
        assert r.status_code == 200
        onboarding = r.json()["onboarding"]
        assert onboarding["demo_created"] is False
        assert onboarding["session_recording"] is False
        assert onboarding["github_connected"] is False

    def test_e2e_demo_graph_creates_content(self, client):
        """E2E-5: Demo graph creates Points + auto-updates state."""
        r = client.post("/v1/demo")
        assert r.status_code == 200
        assert r.json()["status"] in ("seeded", "already_seeded")
        # State auto-updated
        state = client.get("/v1/onboarding/state").json()["onboarding"]
        assert state["demo_created"] is True

    def test_e2e_demo_idempotent(self, client):
        """E2E-5: Re-running demo doesn't crash (sentinel)."""
        client.post("/v1/demo")
        r2 = client.post("/v1/demo")
        assert r2.status_code == 200

    def test_e2e_session_recording_toggle(self, client):
        """E2E-6: Session recording toggle updates state."""
        r = client.post("/v1/onboarding/session-recording", json={"enabled": True})
        assert r.status_code == 200
        assert r.json()["onboarding"]["session_recording"] is True
        # Toggle off
        r2 = client.post("/v1/onboarding/session-recording", json={"enabled": False})
        assert r2.json()["onboarding"]["session_recording"] is False

    def test_e2e_patch_state_merge(self, client):
        """E2E-7: PATCH merges fields without clobbering others."""
        r = client.patch("/v1/onboarding/state", json={"prompt_pasted": True})
        assert r.status_code == 200
        onboarding = r.json()["onboarding"]
        assert onboarding["prompt_pasted"] is True
        assert "github_connected" in onboarding  # other fields preserved

    def test_e2e_onboarding_complete_flag(self, client):
        """E2E-7: Setting onboarding_complete works (verification done)."""
        r = client.patch("/v1/onboarding/state", json={"onboarding_complete": True})
        assert r.status_code == 200
        assert r.json()["onboarding"]["onboarding_complete"] is True

    def test_e2e_github_not_connected_by_default(self, client):
        """E2E-3: GitHub status shows not-connected until OAuth."""
        r = client.get("/v1/onboarding/github/status")
        assert r.status_code == 200
        assert r.json()["connected"] is False

    def test_e2e_github_connect_returns_auth_url(self, client):
        """E2E-3: GitHub connect returns an authorize URL (mock client id)."""
        os.environ["GITHUB_CLIENT_ID"] = "e2e-client"
        r = client.post("/v1/onboarding/github/connect", json={"org": "acme"})
        assert r.status_code == 200
        assert "github.com/login/oauth/authorize" in r.json()["auth_url"]


class TestMisledUserReAsk:
    """#1728 Slice 3 (Task 17, Journey 2): the misled-user re-ask. Legacy
    ``session_recording=True`` teams (set before the capture mechanism was
    honest) are re-asked exactly once per visit until RESOLVED — the gate is
    ``session_recording=True && !capture_revised && !capture_ask_shown``;
    ``capture_ask_shown`` is set on ANSWER only (dismissal never consumes the
    ask); decline clears consent + sets capture_revised but never clears
    probes/receipts."""

    def _set_legacy_state(self, client, **extra):
        from tortoise.hosted_api import _make_sdk, _update_onboarding_state
        sdk = _make_sdk(namespace="registry")
        # the fixture seeds the team via team_create (ULID id) — resolve the
        # real id by name (state writes are MATCH...SET on the team node)
        rows = sdk._get_registry().query(
            "MATCH (t:Team {name: $name}) RETURN t.id",
            params={"name": "e2e-team"}).result_set
        team_id = rows[0][0]
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.onboarding_state = $st",
            params={"id": team_id, "st": "{}"},
        )
        _update_onboarding_state(team_id, session_recording=True, **extra)

    def _gate(self, st: dict) -> bool:
        return bool(st["session_recording"] and not st["capture_revised"]
                    and not st["capture_ask_shown"])

    def test_misled_user_reask_once(self, client):
        """Journey 2: a misled returning user (session_recording=True,
        nothing resolved) sees the re-ask gate on their first visit; answering
        (capture_ask_shown set) resolves it; a second visit never re-fires."""
        self._set_legacy_state(client)
        # visit 1 — the gate is live (the dashboard re-ask pane renders)
        r = client.get("/v1/onboarding/state")
        st = r.json()["onboarding"]
        assert self._gate(st) is True
        # the user ANSWERS (the re-ask answer PATCH — ANSWER only sets
        # capture_ask_shown; dismissal never does)
        r2 = client.patch("/v1/onboarding/state", json={
            "session_recording": True, "capture_revised": True,
            "capture_ask_shown": True,
        })
        assert r2.status_code == 200, r2.text
        # visit 2 — resolved: no re-ask (capture_revised + ask_shown both set)
        r3 = client.get("/v1/onboarding/state")
        assert self._gate(r3.json()["onboarding"]) is False

    def test_reask_dismissal_does_not_consume(self, client):
        """T2-P2f: dismissal NEVER consumes the ask — a dismissed (not
        answered) user sees the gate again on the next visit (capture_ask_shown
        stays False)."""
        self._set_legacy_state(client)
        # dismissal = the client does NOT patch capture_ask_shown; the pane is
        # merely closed client-side for this visit. A fresh visit re-reads the
        # same gate inputs.
        r = client.get("/v1/onboarding/state")
        assert self._gate(r.json()["onboarding"]) is True
        r2 = client.get("/v1/onboarding/state")
        assert self._gate(r2.json()["onboarding"]) is True, \
            "dismissal must not consume the exactly-once ask"

    def test_reask_decline_then_reenable(self, client):
        """Decline (NO) clears consent + sets capture_revised + ask_shown;
        re-enabling later re-sets consent — the wizard's sessions toggle-on
        PATCH writes consent + capture_revised together (single consent
        source), and the re-ask stays resolved."""
        self._set_legacy_state(client)
        # decline
        r = client.patch("/v1/onboarding/state", json={
            "session_recording": False, "capture_revised": True,
            "capture_ask_shown": True,
        })
        assert r.status_code == 200, r.text
        st = r.json()["onboarding"]
        assert st["session_recording"] is False
        assert self._gate(st) is False
        # later opt-in via the wizard toggle-on PATCH (same keys as Q3 yes)
        r2 = client.patch("/v1/onboarding/state", json={
            "session_recording": True, "capture_revised": True})
        assert r2.status_code == 200, r2.text
        st2 = r2.json()["onboarding"]
        assert st2["session_recording"] is True
        assert self._gate(st2) is False, \
            "re-enable must not re-trigger the re-ask (already resolved)"
