"""#2002 (W6, epic #1976): member-authz for the Settings view/delete leg —
the DE2E-11 authz assert, exercised on BOTH auth lanes.

DE2E-11 (self-use org) asserts "member authz enforced" on the W6 surface:
- GET /v1/sessions/{id} + DELETE /v1/sessions/{id} are dual-auth (#1828):
  a session JWT (the Settings dashboard's lane) OR a tt_ key (agents).
- A session user must be an ACTIVE member of the ?team_id= team — otherwise
  403 (the #1148 membership gate, _session_user_team) — and the resolution
  happens BEFORE any handler touches the graph (no existence oracle, no
  partial delete).
- Key auth is team-scoped by resolution: another team's key sees 404, never
  the session (no cross-team existence leak).
- POST /v1/sessions (the capture HOOK) intentionally stays tt_-key-only —
  the hook is agent-side; the session lane is the dashboard's view/delete.

Harness mirrors test_action_endpoints_dual_auth (Supabase-mode
FakeControlPlane + patched_tortoise_sdk temp embedded DB) so the suite is
lane-agnostic — the capture writes hermetically to the per-test temp graph
and onboarding jsonb state goes to the fake plane's teams rows.
"""

from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest
from fastapi.testclient import TestClient

import tortoise.supabase_control as sc
from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import FakeControlPlane
from tortoise.hosted_api import app

_SUPABASE_URL = "https://w6authz.test.supabase.co"
CONV = [{"role": "user", "content": "We decided to ship disclosure first."},
        {"role": "assistant", "content": "Agreed."}]


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline MockModel extractor — capture runs with zero network."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Supabase-mode FakeControlPlane + temp embedded DB (mirrors
    test_action_endpoints_dual_auth._env)."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-w6-authz-test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    db_path = str(tmp_path / "w6-authz.db")
    with patched_tortoise_sdk(db_path), TestClient(app) as client:
        yield client, fake


def _provision_anon(client) -> tuple[str, str]:
    """Mint an anonymous team via /v1/agent/signup (Supabase mode) — returns
    (tt_ key, team_id)."""
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["team_id"]


def _patch_session_user(monkeypatch, user_id: str):
    """Patch the JWT verifier so 'Bearer eyJ.sess' resolves to user_id."""
    async def _fake(request):
        return {"user_id": user_id, "email": "user@example.com", "sub": user_id}
    import tortoise.session_auth as sa
    monkeypatch.setattr(sa, "verify_session_jwt", _fake)


def _seed_membership(fake, team_id: str, user_id: str, role: str = "owner"):
    fake.tables.setdefault("team_memberships", []).append({
        "id": str(uuid.uuid4()), "team_id": team_id, "user_id": user_id,
        "role": role, "status": "active", "created_at": "2026-01-01T00:00:00Z",
        "identity": None, "lookup_hash": None,
    })


def _team_state(fake, team_id: str) -> dict:
    """Onboarding jsonb (operational keys — the capture receipt lives here,
    migration 0006 teams.onboarding_state)."""
    from tortoise.supabase_control import (
        get_control_plane,
        team_onboarding_state,
    )
    return team_onboarding_state(get_control_plane(), team_id) or {}


def _capture_key_lane(client, key: str, team_id: str, sid: str):
    r = client.post("/v1/sessions",
                    headers={"Authorization": f"Bearer {key}"},
                    json={"conversation": CONV, "session_id": sid})
    assert r.status_code == 200, r.text
    return r.json()


_SESS = "Bearer eyJ.sess"


class TestSettingsViewDeleteSessionLane:
    def test_owner_session_view_and_delete_roundtrip(self, env, monkeypatch):
        """DE2E-11 happy path on the SESSION lane (the Settings dashboard's
        lane): the member owner captures via key, then views + deletes via
        the session JWT — the transcript 404s after, and the per-harness
        receipt is cleaned by recompute."""
        client, fake = env
        key, team_id = _provision_anon(client)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_membership(fake, team_id, user_id)
        sid = f"authz-s1-{uuid.uuid4().hex[:6]}"
        cap = _capture_key_lane(client, key, team_id, sid)
        assert cap["first_capture"] is True
        q = f"?team_id={team_id}"
        # view (session lane)
        det = client.get(f"/v1/sessions/{sid}{q}", headers={"Authorization": _SESS})
        assert det.status_code == 200, det.text
        assert det.json()["id"] == sid
        assert len(det.json()["turn_points"]) == cap["turns"]
        # delete (session lane) → transcript gone + receipt cleaned
        d = client.delete(f"/v1/sessions/{sid}{q}", headers={"Authorization": _SESS})
        assert d.status_code == 200, d.text
        assert d.json()["deleted"] is True
        assert client.get(f"/v1/sessions/{sid}{q}",
                          headers={"Authorization": _SESS}).status_code == 404
        st = _team_state(fake, team_id)
        assert not st.get("session_capture_receipt"), \
            "receipt must be cleaned when the bucket empties"

    def test_non_member_session_is_403_before_any_data_op(self, env, monkeypatch):
        """A session user with NO membership gets 403 on both GET detail and
        DELETE — authz resolution precedes any handler/graph access (no
        existence oracle for a non-member, no partial delete)."""
        client, _fake = env
        _key, team_id = _provision_anon(client)
        stranger = str(uuid.uuid4())
        _patch_session_user(monkeypatch, stranger)  # no membership rows
        q = f"?team_id={team_id}"
        g = client.get(f"/v1/sessions/whatever-1{q}", headers={"Authorization": _SESS})
        assert g.status_code == 403, g.text
        assert "membership" in g.json()["detail"]
        d = client.delete(f"/v1/sessions/whatever-1{q}",
                          headers={"Authorization": _SESS})
        assert d.status_code == 403, d.text
        assert "membership" in d.json()["detail"]

    def test_member_of_other_team_cannot_target_this_team(self, env, monkeypatch):
        """?team_id= targeting a team the session user is NOT a member of →
        403 "No membership in team" (the #1148 membership check closes the
        cross-team id-guessing hole on the W6 surface)."""
        client, fake = env
        _key_a, team_a = _provision_anon(client)
        _key_b, team_b = _provision_anon(client)
        user_b = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_b)
        _seed_membership(fake, team_b, user_b)  # member of B only
        q = f"?team_id={team_a}"
        g = client.get(f"/v1/sessions/whatever-2{q}", headers={"Authorization": _SESS})
        assert g.status_code == 403, g.text
        assert "No membership in team" in g.json()["detail"]
        d = client.delete(f"/v1/sessions/whatever-2{q}",
                          headers={"Authorization": _SESS})
        assert d.status_code == 403, d.text

    def test_unauthenticated_is_401(self, env):
        client, _fake = env
        assert client.get("/v1/sessions/whatever-3?team_id=t").status_code == 401
        assert client.delete("/v1/sessions/whatever-3?team_id=t").status_code == 401


class TestSettingsViewDeleteKeyLane:
    def test_key_lane_roundtrip_unchanged(self, env):
        """tt_ keys (the agent hook lane) keep working on the W6 surface:
        view + delete + receipt cleanup without a session."""
        client, _fake = env
        key, team_id = _provision_anon(client)
        sid = f"authz-k1-{uuid.uuid4().hex[:6]}"
        cap = _capture_key_lane(client, key, team_id, sid)
        assert cap["first_capture"] is True
        det = client.get(f"/v1/sessions/{sid}?team_id={team_id}",
                         headers={"Authorization": f"Bearer {key}"})
        assert det.status_code == 200, det.text
        d = client.delete(f"/v1/sessions/{sid}?team_id={team_id}",
                          headers={"Authorization": f"Bearer {key}"})
        assert d.status_code == 200, d.text
        assert client.get(f"/v1/sessions/{sid}?team_id={team_id}",
                          headers={"Authorization": f"Bearer {key}"}
                          ).status_code == 404

    def test_cross_team_key_sees_404_not_the_session(self, env):
        """Key auth is team-scoped by resolution: team B's key GET/DELETE of
        team A's session resolves on B's own tenant graph → 404. No
        existence leak across teams."""
        client, _fake = env
        key_a, team_a = _provision_anon(client)
        key_b, team_b = _provision_anon(client)
        sid = f"authz-x1-{uuid.uuid4().hex[:6]}"
        _capture_key_lane(client, key_a, team_a, sid)
        hb = {"Authorization": f"Bearer {key_b}"}
        assert client.get(f"/v1/sessions/{sid}?team_id={team_b}",
                          headers=hb).status_code == 404
        assert client.delete(f"/v1/sessions/{sid}?team_id={team_b}",
                             headers=hb).status_code == 404
        # and the owner's session is untouched by the foreign attempt
        det = client.get(f"/v1/sessions/{sid}?team_id={team_a}",
                         headers={"Authorization": f"Bearer {key_a}"})
        assert det.status_code == 200, det.text

    def test_capture_hook_stays_key_only(self, env, monkeypatch):
        """POST /v1/sessions (the capture HOOK) is agent-side — a session
        JWT is NOT an acceptable capture credential (the #1927 gate + quota
        work belong to the key lane)."""
        client, fake = env
        _key, team_id = _provision_anon(client)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _seed_membership(fake, team_id, user_id)
        r = client.post(f"/v1/sessions?team_id={team_id}",
                        headers={"Authorization": _SESS},
                        json={"conversation": CONV, "session_id": "hook-1"})
        assert r.status_code == 401, r.text
