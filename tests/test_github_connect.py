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

from tests._http_fixtures import patched_tortoise_sdk
from tortoise.hosted_api import app


@pytest.fixture
def client(tmp_path):
    db_path = str(tmp_path / "github.db")
    # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
    # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
    # __init__ → deterministic anchor close → clear overrides at exit.
    # Supersedes the inline kw_db-first _patched: audited — no test here
    # constructs an SDK directly, and the only caller (hosted_api._make_sdk)
    # passes the fixture path once the pin is set (docker-lane URI mode
    # passes no path at all), so the old caller-path forwarding was a latent
    # shared-/data/tortoise.db binding on the embedded lane, not a real
    # pass-through. The helper's force-drop + pin is the #1497/#2090 fix.
    with patched_tortoise_sdk(db_path):
        from tortoise.hosted_api import get_current_org
        app.dependency_overrides[get_current_org] = lambda: {
            "org_id": "test-team-1", "tier": "free", "key_id": "k1",
            "max_users": 1, "max_graphs": 1, "max_teams": 1,
        }
        with TestClient(app) as tc:
            yield tc


@pytest.fixture
def unauth_client(tmp_path):
    db_path = str(tmp_path / "github_unauth.db")
    # #2127 wave 2: shared helper (see the client fixture audit note — the
    # 401-path tests never construct SDKs, but the fixture still pins +
    # closes deterministically for uniform discipline).
    with patched_tortoise_sdk(db_path), TestClient(app) as tc:
        yield tc


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
        github_org — never the internal org_id. Regression: the old flow
        stored org_id (a hex UUID) and every org-scoped lookup 404'd."""
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
            "the callback must store the token login, not the org_id"

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
        # #1893 (code-review P1): a CLEAN disconnect carries NO resolve_error
        # flag — the flag is a positive-only resolve-failure signal.
        assert "resolve_error" not in body

    def test_repos_lists_short_names(self, client, monkeypatch):
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        # Stub the encrypted-credential read (no store/network in tests).
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
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

    def test_repos_decrypt_failure_flagged(self, client, monkeypatch):
        """#1893 (code-review P1): a stored token that fails to decrypt returns
        connected:false + resolve_error:true — a resolve-class failure, never
        evidence of an empty org (pruning the persisted scope on it would
        clobber the selection)."""
        import tortoise.hosted_api as ha
        # Corrupt stored credentials so decrypt_token raises ValueError.
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: ("not-a-valid-encrypted-token", "acme"))

        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is False
        assert body["repos"] == []
        assert body.get("resolve_error") is True

    def test_repos_resolve_failure_flagged(self, client, monkeypatch):
        """#1893 (code-review P1): a server-side resolve failure returns 200
        with an EMPTY list + resolve_error:true (never a 500) — the dashboard
        must not treat it as a genuinely-empty org (pruning the persisted
        scope on it would clobber the stored selection)."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
        from tortoise.indexer.github_indexer import GitHubIndexer

        async def fake_resolve_fail(self, org):
            raise RuntimeError("github down")

        monkeypatch.setattr(GitHubIndexer, "resolve_repos", fake_resolve_fail)

        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["connected"] is True
        assert body["org"] == "acme"
        assert body["repos"] == []
        assert body.get("resolve_error") is True

    def test_repos_success_no_resolve_error(self, client, monkeypatch):
        """#1893 (code-review P1): a successful resolve carries NO
        resolve_error flag (absent, not false) — the dashboard's flag check
        stays a positive-only signal."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
        from tortoise.indexer.github_indexer import GitHubIndexer

        async def fake_resolve(self, org):
            return [f"{org}/repo1"]

        monkeypatch.setattr(GitHubIndexer, "resolve_repos", fake_resolve)

        r = client.get("/v1/onboarding/github/repos")
        assert r.status_code == 200
        body = r.json()
        assert body["repos"] == ["repo1"]
        assert "resolve_error" not in body

    def test_repos_heals_legacy_org_id_org(self, client, monkeypatch):
        """#1845 (regression): a team whose stored github_org is the internal
        org_id UUID (the pre-#1845 connect bug) self-heals to the token's
        real login — the selector's org is real, and the org is PATCHed back
        so the fix is permanent without a reconnect."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        org_id = "test-team-1"
        # stored org = the org_id itself (the bug)
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
        assert patched == [(org_id, "acme-user")], \
            "the healed org must be persisted back"

    def test_repos_resolve_fallback_to_user_repos(self, client, monkeypatch):
        """#1845: when BOTH orgs/ and users/ repo lookups 404 (unknown org),
        resolve_repos falls back to the token's OWN repos (/user/repos) —
        the selector lists what the token can actually see instead of
        rendering an empty list."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("fake-token"), "ghost-org"))
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
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
        for bad in ("../victimorg/x", "a/b?q=1", "repo name"):
            r = client.get(f"/v1/onboarding/github/branches?repo={bad}")
            assert r.status_code == 400, f"repo={bad!r} should 400"

    def test_branches_lists_names(self, client, monkeypatch):
        """Branch names come back SHORT (server prepends org/ before the
        GitHub call)."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
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
                            lambda org_id: (encrypt_token("fake-token"), "acme"))
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


# ── #4946: the real GitHub disconnect (token revocation) ─────────────────
#
# The pre-#1924 "disconnect" only wrote github_connected: false while the
# stored OAuth token stayed live. These pin the real path: GitHub revocation
# first, then a LOCAL clear + the flag write, with the revocation outcome
# reported rather than silently downgraded to clear-only.

def _provision_team(org_id: str = "test-team-1", *,
                    token_enc: str | None = None,
                    org: str | None = None,
                    state: str = "{}") -> None:
    """Create the registry Team node the state/credential writers MATCH on.

    The writers are MATCH…SET (a silent no-op without the node), so the
    readbacks below are real persistence assertions, not in-memory echoes.
    ``state`` seeds the stored jsonb — pass ``{"github_connected": true}`` so
    the flag assertions after a disconnect pin the WRITE, not the default
    (``_ONBOARDING_DEFAULT_STATE`` already has ``github_connected: False``).
    """
    from tortoise.hosted_api import _make_sdk
    props: dict = {"id": org_id, "onboarding_state": state}
    if token_enc is not None:
        props["github_token_enc"] = token_enc
    if org is not None:
        props["github_org"] = org
    map_items = ", ".join(f"{k}: ${k}" for k in props)
    _make_sdk(namespace="registry")._get_registry().query(
        f"CREATE (t:Team {{{map_items}}})", params=props)


def _stored_credentials(org_id: str = "test-team-1"):
    from tortoise.hosted_api import _github_credentials
    return _github_credentials(org_id)


class TestGitHubDisconnect:
    def test_disconnect_requires_auth(self, unauth_client):
        r = unauth_client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 401

    def test_disconnect_not_connected_is_idempotent(self, client, monkeypatch):
        """No stored token: nothing to revoke, no GitHub call, but the flag
        is still written false and the endpoint reports the no-op honestly."""
        import tortoise.hosted_api as ha
        # Stored state says connected=True while no credential exists — the
        # endpoint must still write the flag false (never rely on the default).
        _provision_team(state='{"github_connected": true}')
        called = []

        async def _spy(token):
            called.append(token)
            raise AssertionError("must not call GitHub with no stored token")

        monkeypatch.setattr(ha, "_revoke_github_token", _spy)
        r = client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["connected"] is False
        assert body["revoked"] is True
        assert body["revoke_reason"] == "not_connected"
        assert called == []
        assert client.get("/v1/onboarding/state").json()["onboarding"][
            "github_connected"] is False

    def test_disconnect_revokes_then_clears_and_flips_flag(
            self, client, monkeypatch):
        """Success path: GitHub revokes the token, the local ciphertext is
        removed AND github_connected is written false by this endpoint."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        # Seed connected=True (plus non-default source intents) so the
        # post-disconnect assertions pin the endpoint's WRITE rather than the
        # default state, which is already github_connected: False.
        _provision_team(token_enc=encrypt_token("gho_live_token"),
                        org="acme",
                        state='{"github_connected": true, '
                              '"issues_enabled": false, '
                              '"docs_enabled": false}')
        seen = {}

        async def _fake_revoke(token):
            seen["token"] = token
            return True, "revoked"

        monkeypatch.setattr(ha, "_revoke_github_token", _fake_revoke)
        r = client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 200, r.text
        assert seen["token"] == "gho_live_token", "the decrypted token is revoked"
        body = r.json()
        assert body == {"connected": False, "revoked": True,
                        "revoke_reason": "revoked"}, body
        # local credential gone
        assert _stored_credentials() == (None, None)
        # the endpoint (not a source toggle) wrote the connection flag
        st = client.get("/v1/onboarding/state").json()["onboarding"]
        assert st["github_connected"] is False
        # the per-source ENABLE intent is untouched (#1924 separation): the
        # seeded False values must survive, not be reset to the True default
        assert st["issues_enabled"] is False
        assert st["docs_enabled"] is False

    def test_revocation_failure_still_clears_and_reports(self, client, monkeypatch):
        """A failed revocation never leaves the token stored: the local
        ciphertext is cleared, the flag flips, and the failure is named."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        _provision_team(token_enc=encrypt_token("gho_live_token"),
                        org="acme", state='{"github_connected": true}')

        async def _net_fail(token):
            return False, "network"

        monkeypatch.setattr(ha, "_revoke_github_token", _net_fail)
        r = client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revoked"] is False
        assert body["revoke_reason"] == "network"
        assert body["connected"] is False
        assert _stored_credentials() == (None, None), \
            "a failed revocation must still clear the stored token locally"
        assert client.get("/v1/onboarding/state").json()["onboarding"][
            "github_connected"] is False

    def test_undecryptable_token_cleared_but_not_claimed_revoked(
            self, client, monkeypatch):
        """A stored-but-undecryptable ciphertext leaves us nothing to send
        GitHub — the plaintext could still be live, so the clear happens but
        the outcome is NOT reported as a confirmed revocation."""
        import tortoise.hosted_api as ha
        _provision_team(token_enc="not-a-valid-ciphertext", org="acme",
                        state='{"github_connected": true}')

        async def _spy(token):
            raise AssertionError("no plaintext → no GitHub call")

        monkeypatch.setattr(ha, "_revoke_github_token", _spy)
        r = client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revoked"] is False
        assert body["revoke_reason"] == "undecryptable"
        assert _stored_credentials() == (None, None)
        assert client.get("/v1/onboarding/state").json()["onboarding"][
            "github_connected"] is False

    def test_disconnect_uses_supabase_clear_seam(self, client, monkeypatch):
        """Hosted (Supabase) mode: the local half goes through the
        service-role seam that PATCHes ``organizations`` — the only writer of
        the column-REVOKEd github_token_enc."""
        import tortoise.hosted_api as ha
        from tortoise.crypto import encrypt_token
        fake_cp = object()
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: fake_cp)
        monkeypatch.setattr(ha, "_github_credentials",
                            lambda org_id: (encrypt_token("gho_tok"), "acme"))
        monkeypatch.setattr(ha, "_update_onboarding_state",
                            lambda *a, **k: None)
        cleared = []
        monkeypatch.setattr(
            "tortoise.supabase_control.clear_github_credentials",
            lambda cp, org_id: cleared.append((cp, org_id)))

        async def _revoke(token):
            return True, "revoked"

        monkeypatch.setattr(ha, "_revoke_github_token", _revoke)
        r = client.post("/v1/onboarding/github/disconnect")
        assert r.status_code == 200, r.text
        assert cleared == [(fake_cp, "test-team-1")], \
            "the clear must run through the Supabase service-role seam"
        assert r.json()["revoked"] is True

    def test_disconnect_rejects_graph_bound_key(self, client):
        """#2300 parity: a per-graph key must not tear down the ORG's GitHub
        connection."""
        from tortoise.hosted_api import (
            app,
            get_current_org_session_ungated,
        )
        app.dependency_overrides[get_current_org_session_ungated] = lambda: {
            "org_id": "test-team-1", "graph_id": "g-1",
            "legacy_full_access": True,
        }
        try:
            r = client.post("/v1/onboarding/github/disconnect")
        finally:
            app.dependency_overrides.pop(get_current_org_session_ungated, None)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"


class TestRevokeGitHubToken:
    """The revocation helper's GitHub contract (#4946) — the endpoint tests
    above stub it, so its own outcomes are pinned here."""

    @staticmethod
    def _run(coro):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    @staticmethod
    def _stub_httpx(monkeypatch, *, status_code=None, exc=None):
        import httpx
        seen: dict = {}

        class _Resp:
            def __init__(self, code):
                self.status_code = code

        class _Client:
            def __init__(self, **kw):
                seen["client_kwargs"] = kw

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def delete(self, url, **kw):
                seen["url"] = url
                seen["kwargs"] = kw
                if exc is not None:
                    raise exc
                return _Resp(status_code)

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        return seen

    def test_204_is_revoked(self, monkeypatch):
        import tortoise.hosted_api as ha
        seen = self._stub_httpx(monkeypatch, status_code=204)
        assert self._run(ha._revoke_github_token("tok")) == (True, "revoked")
        assert seen["url"].endswith("/applications/test-client-id/token")
        assert seen["kwargs"]["json"] == {"access_token": "tok"}
        assert seen["kwargs"]["auth"] == ("test-client-id", "test-client-secret")

    def test_404_is_not_a_confirmed_revocation(self, monkeypatch):
        """GitHub documents only 204/422 for DELETE; a 404 (unknown app or
        token resource) leaves liveness unconfirmed, so it must NOT be
        reported as revoked — a false "gone" claim is the cosmetic bug."""
        import tortoise.hosted_api as ha
        self._stub_httpx(monkeypatch, status_code=404)
        assert self._run(ha._revoke_github_token("tok")) == (False, "http_404")

    def test_422_is_not_a_confirmed_revocation(self, monkeypatch):
        """422 is GitHub's documented "validation failed" outcome for the
        DELETE — reported, never upgraded to a confirmed revocation."""
        import tortoise.hosted_api as ha
        self._stub_httpx(monkeypatch, status_code=422)
        assert self._run(ha._revoke_github_token("tok")) == (False, "http_422")

    def test_unexpected_status_is_reported(self, monkeypatch):
        import tortoise.hosted_api as ha
        self._stub_httpx(monkeypatch, status_code=500)
        assert self._run(ha._revoke_github_token("tok")) == (False, "http_500")

    def test_transport_failure_is_network(self, monkeypatch):
        import tortoise.hosted_api as ha
        self._stub_httpx(monkeypatch, exc=RuntimeError("boom"))
        assert self._run(ha._revoke_github_token("tok")) == (False, "network")

    def test_unconfigured_app_reports_not_configured(self, monkeypatch):
        import tortoise.hosted_api as ha
        monkeypatch.delenv("GITHUB_CLIENT_ID", raising=False)
        assert self._run(ha._revoke_github_token("tok")) == \
            (False, "not_configured")

