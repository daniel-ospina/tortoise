"""Tests for GitHub OAuth onboarding endpoints (#499).

Covers: connect (auth URL + state), callback (exchange + encrypted storage),
status (connected/not), auth requirements, state validation.
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("TORTOISE_ENCRYPTION_KEY", "I2n-E3K857hF9ENLgrOZ8YBPkEB4tu4jyrb1aJMUtnI=")
os.environ.setdefault("GITHUB_CLIENT_ID", "test-client-id")
os.environ.setdefault("GITHUB_CLIENT_SECRET", "test-client-secret")

import pytest
from fastapi.testclient import TestClient

from tortoise.hosted_api import app
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "github.db")
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # hosted_api._make_sdk (onboarding SDK builder) calls
        # TortoiseSDK(db_path=..., ...) with db_path as a KEYWORD — pop it
        # from **kw so the forwarding doesn't produce a duplicate-kwarg
        # TypeError (#647 sweep catch).
        kw_db = kw.pop("db_path", None)
        resolved = kw_db if kw_db is not None else (db_path if db_path_arg is None else db_path_arg)
        orig_init(self, db_path=resolved, namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE
    _FALLBACK_KEEPALIVE.clear()
    from tortoise.hosted_api import get_current_team
    app.dependency_overrides[get_current_team] = lambda: {
        "team_id": "test-team-1", "tier": "free", "key_id": "k1",
        "max_users": 1, "max_graphs": 1, "max_teams": 1,
    }
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
    TortoiseSDK.__init__ = orig_init


@pytest.fixture
def unauth_client(tmp_path):
    db_path = str(tmp_path / "github_unauth.db")
    orig_init = TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kw):
        # hosted_api._make_sdk (onboarding SDK builder) calls
        # TortoiseSDK(db_path=..., ...) with db_path as a KEYWORD — pop it
        # from **kw so the forwarding doesn't produce a duplicate-kwarg
        # TypeError (#647 sweep catch).
        kw_db = kw.pop("db_path", None)
        resolved = kw_db if kw_db is not None else (db_path if db_path_arg is None else db_path_arg)
        orig_init(self, db_path=resolved, namespace=namespace, **kw)

    TortoiseSDK.__init__ = _patched
    with TestClient(app) as tc:
        yield tc
    TortoiseSDK.__init__ = orig_init


class TestGitHubConnect:
    def test_connect_returns_auth_url(self, client):
        r = client.post("/v1/onboarding/github/connect", json={"org": "acme"})
        assert r.status_code == 200
        body = r.json()
        assert "auth_url" in body and "state" in body
        assert "github.com/login/oauth/authorize" in body["auth_url"]
        assert "client_id=test-client-id" in body["auth_url"]

    def test_connect_requires_auth(self, unauth_client):
        r = unauth_client.post("/v1/onboarding/github/connect", json={})
        assert r.status_code == 401


class TestGitHubCallback:
    def test_callback_rejects_bad_state(self, client):
        r = client.get("/v1/onboarding/github/callback?code=x&state=bad")
        assert r.status_code == 404

    def test_callback_missing_code(self, client):
        # Get a real state first
        r = client.post("/v1/onboarding/github/connect", json={})
        state = r.json()["state"]
        r = client.get(f"/v1/onboarding/github/callback?state={state}")
        assert r.status_code == 404  # no code

    def test_callback_handles_denial(self, client):
        # follow_redirects=False: TestClient follows the 302 to the external
        # welcome URL which isn't served locally → would show 404 instead of
        # the redirect we're testing.
        r = client.get("/v1/onboarding/github/callback?error=access_denied",
                       follow_redirects=False)
        assert r.status_code == 302
        assert "github=denied" in r.headers["location"]

    def test_callback_success_stores_login_as_org(self, client, monkeypatch):
        """#1845 (review P2-5): the SUCCESS path derives the real GitHub
        login from the token (GET /user → current_login) and stores THAT as
        github_org — never the internal team_id. Regression: the old flow
        stored team_id (a hex UUID) and every org-scoped lookup 404'd."""
        import tortoise.hosted_api as ha
        from tortoise.indexer.github_indexer import GitHubIndexer

        # Force Supabase mode so the callback's store path is the seam we
        # can intercept (established pattern — test_abuse_integration).
        fake_cp = object()
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: fake_cp)

        # Mint a valid CSRF state
        r = client.post("/v1/onboarding/github/connect", json={})
        state = r.json()["state"]

        stored: dict = {}
        async def _fake_exchange(code):
            return "access-token-123"

        monkeypatch.setattr(ha, "_exchange_github_token", _fake_exchange)
        monkeypatch.setattr(ha, "_update_onboarding_state", lambda *a, **k: None)
        monkeypatch.setattr(ha, "_start_index_job", lambda tid, kind="github": ("j1", True))

        async def fake_login(self):
            return "acme-user"

        monkeypatch.setattr(GitHubIndexer, "current_login", fake_login)
        monkeypatch.setattr(
            "tortoise.supabase_control.store_github_credentials",
            lambda cp, tid, *, token_enc, org: stored.update(
                {"tid": tid, "org": org}))
        async def _noop_run(*a, **k):
            return None

        monkeypatch.setattr(ha, "_run_indexing", _noop_run)

        r = client.get(f"/v1/onboarding/github/callback?code=code&state={state}",
                       follow_redirects=False)
        assert r.status_code == 302
        assert stored.get("org") == "acme-user", \
            "the callback must store the token login, not the team_id"

    def test_callback_login_failure_falls_back_to_state_org(
            self, client, monkeypatch):
        """#1845 (review P2-5): when the /user login call fails, an explicit
        body.org from the connect state survives (best-effort), never a 500."""
        import tortoise.hosted_api as ha
        from tortoise.indexer.github_indexer import GitHubIndexer

        fake_cp = object()
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: fake_cp)

        r = client.post("/v1/onboarding/github/connect", json={"org": "acme"})
        state = r.json()["state"]

        stored: dict = {}
        async def _fake_exchange(code):
            return "access-token-123"

        monkeypatch.setattr(ha, "_exchange_github_token", _fake_exchange)
        monkeypatch.setattr(ha, "_update_onboarding_state", lambda *a, **k: None)
        monkeypatch.setattr(ha, "_start_index_job", lambda tid, kind="github": ("j1", True))

        async def fake_login(self):
            raise RuntimeError("github down")

        monkeypatch.setattr(GitHubIndexer, "current_login", fake_login)
        monkeypatch.setattr(
            "tortoise.supabase_control.store_github_credentials",
            lambda cp, tid, *, token_enc, org: stored.update(
                {"tid": tid, "org": org}))
        async def _noop_run(*a, **k):
            return None

        monkeypatch.setattr(ha, "_run_indexing", _noop_run)

        r = client.get(f"/v1/onboarding/github/callback?code=code&state={state}",
                       follow_redirects=False)
        assert r.status_code == 302
        assert stored.get("org") == "acme", \
            "an explicit body.org must survive a /user failure"


class TestGitHubStatus:
    def test_status_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/github/status")
        assert r.status_code == 401

    def test_status_not_connected(self, client):
        r = client.get("/v1/onboarding/github/status")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["org"] is None


class TestGitHubRepos:
    """#1845: GET /v1/onboarding/github/repos — the source-scope selector's
    repo-list read path (server-side token, SHORT names)."""

    def test_repos_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/github/repos")
        assert r.status_code == 401

    def test_repos_not_connected(self, client):
        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["org"] is None
        assert body["repos"] == []

    def test_repos_lists_short_names(self, client, monkeypatch):
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        # Stub the encrypted-credential read (no store/network in tests).
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda team_id: (encrypt_token("fake-token"), "acme"))
        from tortoise.indexer.github_indexer import GitHubIndexer

        async def fake_resolve(self, org):
            return [f"{org}/repo1", f"{org}/repo2", "solo-repo"]

        monkeypatch.setattr(GitHubIndexer, "resolve_repos", fake_resolve)

        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["org"] == "acme"
        # full_names are stripped to SHORT names (the /v1/index/* endpoints
        # already re-add the org/ prefix from the stored org).
        assert body["repos"] == ["repo1", "repo2", "solo-repo"]

    def test_repos_heals_legacy_team_id_org(self, client, monkeypatch):
        """#1845 (regression): a team whose stored github_org is the internal
        team_id UUID (the pre-#1845 connect bug) self-heals to the token's
        real login — the selector's org is real, and the org is PATCHed back
        so the fix is permanent without a reconnect."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        team_id = "test-team-1"
        # stored org = the team_id itself (the bug)
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda tid: (encrypt_token("fake-token"), tid))
        from tortoise.indexer.github_indexer import GitHubIndexer
        patched = []

        async def fake_login(self):
            return "acme-user"

        async def fake_resolve(self, org):
            return [f"{org}/repo1"]

        monkeypatch.setattr(GitHubIndexer, "current_login", fake_login)
        monkeypatch.setattr(GitHubIndexer, "resolve_repos", fake_resolve)
        monkeypatch.setattr(ha, "_store_github_org",
                            lambda tid, enc, org: patched.append((tid, org)))

        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["org"] == "acme-user"
        assert body["repos"] == ["repo1"]
        assert patched == [(team_id, "acme-user")], \
            "the healed org must be persisted back"

    def test_repos_resolve_fallback_to_user_repos(self, client, monkeypatch):
        """#1845: when BOTH orgs/ and users/ repo lookups 404 (unknown org),
        resolve_repos falls back to the token's OWN repos (/user/repos) —
        the selector lists what the token can actually see instead of
        rendering an empty list."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda team_id: (encrypt_token("fake-token"), "ghost-org"))
        from tortoise.indexer.github_indexer import GitHubIndexer

        async def fake_resolve(self, org):
            # legacy behavior raised on 404; the fallback path is tested
            # at the indexer layer (test_github_index_lifecycle) — here we
            # assert the endpoint tolerates an empty resolve (never 500).
            return []

        monkeypatch.setattr(GitHubIndexer, "resolve_repos", fake_resolve)
        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        assert r.json()["repos"] == []


class TestGitHubBranches:
    """#1845: GET /v1/onboarding/github/branches — the docs per-repo branch
    picker's read path (server-side token, SHORT repo name)."""

    def test_branches_requires_auth(self, unauth_client):
        r = unauth_client.get("/v1/onboarding/github/branches?repo=repo1")
        assert r.status_code == 401

    def test_branches_not_connected(self, client):
        r = client.get("/v1/onboarding/github/branches?repo=repo1")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["branches"] == []

    def test_branches_invalid_repo_400(self, client, monkeypatch):
        """#1845 (review P1 parity): a malicious repo short name must be
        rejected (400) before it reaches the GitHub URL path."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda team_id: (encrypt_token("fake-token"), "acme"))
        for bad in ("../victimorg/x", "a/b?q=1", "repo name"):
            r = client.get(f"/v1/onboarding/github/branches?repo={bad}")
            assert r.status_code == 400, f"repo={bad!r} should 400"

    def test_branches_lists_names(self, client, monkeypatch):
        """Branch names come back SHORT (server prepends org/ before the
        GitHub call)."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda team_id: (encrypt_token("fake-token"), "acme"))
        from tortoise.indexer.github_indexer import GitHubIndexer

        async def fake_list(self, repo):
            return ["main", "dev", "feature/x"]

        async def fake_default(self, repo):
            return "main"

        monkeypatch.setattr(GitHubIndexer, "list_branches", fake_list)
        monkeypatch.setattr(GitHubIndexer, "default_branch", fake_default)
        r = client.get("/v1/onboarding/github/branches?repo=repo1")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["repo"] == "repo1"
        assert body["branches"] == ["main", "dev", "feature/x"]
        assert body["default_branch"] == "main"

    def test_branches_failure_is_empty_not_500(self, client, monkeypatch):
        """A branch-list failure degrades to an EMPTY list (the picker
        still renders its default branch), never a 500."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda team_id: (encrypt_token("fake-token"), "acme"))
        from tortoise.indexer.github_indexer import GitHubFetchError, GitHubIndexer

        async def fake_list(self, repo):
            raise GitHubFetchError("boom")

        async def fake_default(self, repo):
            return "main"

        monkeypatch.setattr(GitHubIndexer, "list_branches", fake_list)
        monkeypatch.setattr(GitHubIndexer, "default_branch", fake_default)
        r = client.get("/v1/onboarding/github/branches?repo=repo1")
        assert r.status_code == 200
        assert r.json()["branches"] == []
        assert r.json()["default_branch"] == "main"
