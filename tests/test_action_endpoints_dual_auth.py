"""#1852: seed/index ACTION endpoints accept the session JWT (dual-auth).

The #1833 read set was converted to ``get_current_team_session_ungated``;
these 8 action/write endpoints were missed — a dashboard session JWT
(eyJ...) hit ``get_current_team``'s tt_-only gate → 401 "Invalid API key
format" for OAuth users (wizard "Seed my graph" + MemorySources re-index +
job-status polls).

Each test here asserts BOTH lanes on every converted endpoint:
- session JWT (eyJ) → the handler runs past the dependency (200, or the
  endpoint's own post-auth response — never 401);
- tt_ API key → unchanged behavior.

Harness mirrors test_dashboard_login's TestDashboardLoginGate: Supabase-mode
FakeControlPlane, team minted via /v1/agent/signup, owner membership seeded
for the session user, ``sa.verify_session_jwt`` patched (same seam as #1082
tests). SDK construction is redirected to a per-test temp embedded DB
(mirrors test_session_key_http) so the create endpoints write hermetically.
"""
from __future__ import annotations

import os
import sys
import time
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient
from types import SimpleNamespace

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tortoise.hosted_api import app

from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://actiondual.test.supabase.co"


# ── Fixtures ────────────────────────────────────────────────────────────────


def _patch_tortoise_sdk_init(db_path: str):
    """Redirect every TortoiseSDK construction to one temp embedded DB."""
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    # #1470: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests; clear so this test's SDK re-binds
    # to THIS test's temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig


def _restore_tortoise_sdk_init(orig):
    ha_mod.TortoiseSDK.__init__ = orig


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Supabase-mode FakeControlPlane + temp embedded DB (mirrors
    test_dashboard_login._env + test_session_key_http's SDK patch)."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-action-dual-test")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    db_path = str(tmp_path / "action-dual.db")
    _orig = _patch_tortoise_sdk_init(db_path)
    ha_mod._INDEX_JOBS.clear()
    try:
        with TestClient(app) as client:
            yield client, fake
    finally:
        ha_mod._INDEX_JOBS.clear()
        _restore_tortoise_sdk_init(_orig)


def _provision_anon(client):
    """Mint an anonymous team via /v1/agent/signup (Supabase mode)."""
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["team_id"]


def _patch_session_user(monkeypatch, user_id: str):
    """Patch the JWT verifier so 'Bearer eyJ.sess' resolves to user_id
    (same seam as test_dashboard_login._patch_session_user)."""
    async def _fake(request):
        return {"user_id": user_id, "email": "owner@example.com",
                "sub": user_id}
    import tortoise.session_auth as sa
    monkeypatch.setattr(sa, "verify_session_jwt", _fake)


def _seed_owner_membership(fake, team_id: str, user_id: str):
    """Give the session user an owner membership so _session_user_team's
    membership resolution + ?team_id= ownership check pass."""
    fake.tables.setdefault("team_memberships", []).append({
        "id": str(uuid.uuid4()), "team_id": team_id, "user_id": user_id,
        "role": "owner", "status": "active", "created_at": "2026-01-01T00:00:00Z",
        "identity": None, "lookup_hash": None,
    })


def _seed_github_creds(fake, team_id: str):
    """Seed github_token_enc/github_org on the teams row so the index POSTs
    pass the connect check. The blob is deliberately NOT fernet — the
    background job fails fast at decrypt (no network), exactly the
    established pattern in test_index_docs_api."""
    for t in fake.tables.get("teams", []):
        if t.get("id") == team_id:
            t["github_token_enc"] = "garbage-not-fernet"
            t["github_org"] = "acme"
            return
    raise AssertionError(f"team row for {team_id} not found in fake plane")


@pytest.fixture
def session_user(env, monkeypatch):
    """Provision a team, patch the session JWT verifier, seed the owner
    membership + GitHub creds. Yields a SimpleNamespace holding the client,
    the fake plane, the tt_ key and the team_id."""
    client, fake = env
    key, team_id = _provision_anon(client)
    user_id = str(uuid.uuid4())
    _patch_session_user(monkeypatch, user_id)
    _seed_owner_membership(fake, team_id, user_id)
    _seed_github_creds(fake, team_id)
    return SimpleNamespace(client=client, fake=fake, key=key, team_id=team_id)


def _drain_job(client, job_id: str, timeout_s: float = 3.0):
    """Best-effort poll until the background job reaches a terminal state
    (decrypt-fail is immediate; the entry stays pollable regardless)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/v1/index/github/{job_id}")
        if r.status_code == 200 and r.json().get("status") in ("completed", "failed"):
            return r.json()
        time.sleep(0.02)
    return None


# ── Seed/write endpoints ────────────────────────────────────────────────────


class TestCreateEndpointsDualAuth:
    def test_create_object_session_jwt(self, session_user):
        c = session_user.client
        r = c.post("/v1/objects", headers={"Authorization": "Bearer eyJ.sess"},
                   json={"name": "WizardProject", "objectKind": "project"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "WizardProject"

    def test_create_object_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/objects", headers={"Authorization": f"Bearer {session_user.key}"},
                   json={"name": "KeyProject", "objectKind": "project"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "KeyProject"

    def test_create_subject_session_jwt(self, session_user):
        c = session_user.client
        r = c.post("/v1/subjects", headers={"Authorization": "Bearer eyJ.sess"},
                   json={"name": "Alice", "subjectKind": "person"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Alice"

    def test_create_subject_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/subjects", headers={"Authorization": f"Bearer {session_user.key}"},
                   json={"name": "Bob", "subjectKind": "person"})
        assert r.status_code == 200, r.text
        assert r.json()["name"] == "Bob"

    def test_create_point_session_jwt(self, session_user):
        """The quota + abuse + metering bookkeeping must all work with the
        session dict (no key_id dependency — _check_team_limit/_record_write_op/
        _abuse_record_points read via .get, so a session dict never 500s)."""
        c = session_user.client
        r = c.post("/v1/points", headers={"Authorization": "Bearer eyJ.sess"},
                   json={"content": "session-seeded point", "kind": "statement"})
        assert r.status_code == 200, r.text
        assert r.json()["content"] == "session-seeded point"

    def test_create_point_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/points", headers={"Authorization": f"Bearer {session_user.key}"},
                   json={"content": "key-seeded point", "kind": "statement"})
        assert r.status_code == 200, r.text
        assert r.json()["content"] == "key-seeded point"

    def test_create_point_session_jwt_quota_enforced(self, session_user):
        """The points gate still runs on the session lane: cap the team at 0
        points → session-authed write 402s (fail-closed), proving the quota
        path reads the session dict's max_points, not a key field."""
        for t in session_user.fake.tables.get("teams", []):
            if t.get("id") == session_user.team_id:
                t["graph_size_cap"] = 0
        r = session_user.client.post("/v1/points", headers={"Authorization": "Bearer eyJ.sess"},
                                     json={"content": "over-cap point", "kind": "statement"})
        assert r.status_code == 402, r.text


# ── GitHub index endpoints ──────────────────────────────────────────────────


class TestGitHubIndexDualAuth:
    def test_index_github_session_jwt(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/github", headers={"Authorization": "Bearer eyJ.sess"},
                   json={"org": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"
        _drain_job(c, r.json()["job_id"])

    def test_index_github_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/github", headers={"Authorization": f"Bearer {session_user.key}"},
                   json={"org": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"

    def test_index_github_repoll_session_jwt(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/github/re-poll",
                   headers={"Authorization": "Bearer eyJ.sess"}, json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"
        _drain_job(c, r.json()["job_id"])

    def test_index_github_repoll_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/github/re-poll",
                   headers={"Authorization": f"Bearer {session_user.key}"}, json={})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"

    def test_index_github_job_status_session_jwt(self, session_user):
        """Job created under KEY auth → polled under a SESSION JWT (the
        MemorySources re-index flow polls with the same session credential)."""
        c = session_user.client
        created = c.post("/v1/index/github",
                         headers={"Authorization": f"Bearer {session_user.key}"},
                         json={"org": "acme"})
        job_id = created.json()["job_id"]
        r = c.get(f"/v1/index/github/{job_id}",
                  headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 200, r.text
        assert r.json()["job_id"] == job_id

    def test_index_github_job_status_tt_key(self, session_user):
        c = session_user.client
        created = c.post("/v1/index/github",
                         headers={"Authorization": f"Bearer {session_user.key}"},
                         json={"org": "acme"})
        job_id = created.json()["job_id"]
        r = c.get(f"/v1/index/github/{job_id}",
                  headers={"Authorization": f"Bearer {session_user.key}"})
        assert r.status_code == 200, r.text
        assert r.json()["job_id"] == job_id

    def test_index_github_job_status_cross_tenant_404(self, session_user):
        """Cross-tenant isolation unchanged on the session lane: a job owned
        by another team still 404s (job.get('team_id') != team dict's id)."""
        c = session_user.client
        # second team owns the job
        key2, team2 = _provision_anon(c)
        _seed_github_creds(session_user.fake, team2)
        created = c.post("/v1/index/github",
                         headers={"Authorization": f"Bearer {key2}"},
                         json={"org": "acme"})
        job_id = created.json()["job_id"]
        r = c.get(f"/v1/index/github/{job_id}",
                  headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 404, r.text


# ── Docs index endpoints ────────────────────────────────────────────────────


class TestDocsIndexDualAuth:
    def test_index_docs_session_jwt(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/docs", headers={"Authorization": "Bearer eyJ.sess"},
                   json={"org": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"

    def test_index_docs_tt_key(self, session_user):
        c = session_user.client
        r = c.post("/v1/index/docs", headers={"Authorization": f"Bearer {session_user.key}"},
                   json={"org": "acme"})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "started"

    def test_index_docs_job_status_session_jwt(self, session_user):
        c = session_user.client
        created = c.post("/v1/index/docs",
                         headers={"Authorization": f"Bearer {session_user.key}"},
                         json={"org": "acme"})
        job_id = created.json()["job_id"]
        r = c.get(f"/v1/index/docs/{job_id}",
                  headers={"Authorization": "Bearer eyJ.sess"})
        assert r.status_code == 200, r.text
        assert r.json()["job_id"] == job_id

    def test_index_docs_job_status_tt_key(self, session_user):
        c = session_user.client
        created = c.post("/v1/index/docs",
                         headers={"Authorization": f"Bearer {session_user.key}"},
                         json={"org": "acme"})
        job_id = created.json()["job_id"]
        r = c.get(f"/v1/index/docs/{job_id}",
                  headers={"Authorization": f"Bearer {session_user.key}"})
        assert r.status_code == 200, r.text
        assert r.json()["job_id"] == job_id
