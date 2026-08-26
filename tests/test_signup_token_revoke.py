"""#1715 — user-facing signup-token revocation.

The #1709 recovery token (agent_signup_tokens.revoked_at) gains a
user-facing kill switch: POST /v1/agent/token/revoke {signup_token} lets a
claimed user (dashboard session) or CLI user (key auth) revoke their OWN
team's token by plaintext — the token can no longer recover keys
(token-present signup/recover → uniform 422 invalid_signup_token).

Covered here (the #1709 suite owns the mint/recover flows — untouched):
· revoke sets revoked_at in BOTH lanes (Supabase via FakeControlPlane,
  registry via real FalkorDB — the docker lane);
· revoked token → recover/signup → uniform 422 invalid_signup_token
  (identical body to malformed/unknown — no existence oracle);
· team-scoped: unknown token → 404, another team's token → 403, and the
  RPC/registry write is scoped so a caller can never kill a foreign token;
· idempotent: already-revoked → {"revoked": true, "already": true};
· auth-gated: unauthenticated → 401 (no existence signal);
· audit: agent_signup_token_revoke with resource_type='signup_token';
· CLI: `tortoise token-revoke` (stored token or --token, #1708 resolver).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from unittest import mock
from urllib.error import HTTPError, URLError

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.auth import lookup_hash as _lookup_hash
from tortoise.hosted_api import app


def _http_error(code, body):
    import io
    from email.message import Message
    msg = Message()
    return HTTPError("https://api.premiselabs.co/v1/agent/token/revoke", code,
                     "err", msg, io.BytesIO(body.encode()))


def _ok_json(body):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


def _mint(client, **extra):
    r = client.post("/v1/agent/signup", json=extra)
    assert r.status_code == 200, r.text
    return r.json()


def _st_token() -> str:
    """A VALID-format st_ token (64 hex) that does not exist anywhere —
    distinct from test_agent_signup_idempotency's 32-hex _st_token, which is
    deliberately MALFORMED there (uniform-422 unknown-token probes)."""
    return "st_" + (uuid.uuid4().hex + uuid.uuid4().hex)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestSupabaseLane:
    """Revoke against the FakeControlPlane (zero network, emulates the
    service_role RPC 20260826000001)."""

    @pytest.fixture(autouse=True)
    def _supabase_env(self, monkeypatch):
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://agent1715.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-agent-1715")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        self.fake = fake

    def _token_rows(self, token: str) -> list[dict]:
        th = _lookup_hash(token)
        return [t for t in self.fake.tables.get("agent_signup_tokens", [])
                if t["token_hash"] == th]

    def test_revoke_sets_revoked_at_then_uniform_422(self, client):
        """Journey: user revokes the leaked token → token-present signup AND
        recover return the uniform 422 (no recovery backdoor)."""
        data = _mint(client)
        token, team_id = data["signup_token"], data["team_id"]
        r = client.post("/v1/agent/token/revoke", json={"signup_token": token},
                        headers={"Authorization": f"Bearer {data['key']}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"revoked": True, "already": False, "team_id": team_id}
        # the row flipped in the control plane
        rows = self._token_rows(token)
        assert len(rows) == 1 and rows[0]["revoked_at"] is not None
        # revoked token can no longer recover keys — uniform 422 both surfaces
        for path in ("/v1/agent/signup", "/v1/agent/recover"):
            r2 = client.post(path, json={"signup_token": token})
            assert r2.status_code == 422, f"{path}: {r2.text}"
            assert r2.json()["detail"]["error_code"] == "invalid_signup_token"

    def test_revoke_requires_auth_401(self, client):
        """No Authorization header → 401, token untouched (no existence
        signal to unauthenticated callers — uniform-422 contract)."""
        data = _mint(client)
        r = client.post("/v1/agent/token/revoke",
                        json={"signup_token": data["signup_token"]})
        assert r.status_code == 401, r.text
        assert self._token_rows(data["signup_token"])[0]["revoked_at"] is None

    def test_revoke_malformed_token_uniform_422(self, client):
        """Malformed/missing signup_token → the SAME uniform 422 body as the
        recover surface (no new format oracle)."""
        data = _mint(client)
        headers = {"Authorization": f"Bearer {data['key']}"}
        for payload in ({"signup_token": "st_short"},
                        {"signup_token": 123},
                        {},
                        {"signup_token": "st_" + "Z" * 64}):
            r = client.post("/v1/agent/token/revoke", json=payload,
                            headers=headers)
            assert r.status_code == 422, f"{payload}: {r.text}"
            assert r.json()["detail"] == {"error_code": "invalid_signup_token",
                                          "message": "Invalid signup token."}

    def test_revoke_uppercase_hex_normalized(self, client):
        """#1709 normalization parity: a real token copy-pasted with UPPERCASE
        hex must revoke (never a silent uniform-422 on the panic surface) —
        mirrors _agent_recover_flow's lower() before the format gate."""
        data = _mint(client)
        headers = {"Authorization": f"Bearer {data['key']}"}
        upper = "st_" + data["signup_token"][3:].upper()  # same token, uppercase
        assert upper != data["signup_token"]
        r = client.post("/v1/agent/token/revoke", json={"signup_token": upper},
                        headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["revoked"] is True and body["already"] is False
        # the (lowercased) token is now revoked → recovery is dead
        r2 = client.post("/v1/agent/recover", json={"signup_token": upper},
                         headers=headers)
        assert r2.status_code == 422, r2.text

    def test_revoke_unknown_token_404(self, client):
        data = _mint(client)
        r = client.post("/v1/agent/token/revoke",
                        json={"signup_token": _st_token()},
                        headers={"Authorization": f"Bearer {data['key']}"})
        assert r.status_code == 404, r.text

    def test_revoke_other_teams_token_403_and_not_revoked(self, client):
        """Team A authenticated as A cannot revoke team B's token — and B's
        token stays LIVE (the RPC WHERE is team-scoped)."""
        a, b = _mint(client), _mint(client)
        r = client.post("/v1/agent/token/revoke",
                        json={"signup_token": b["signup_token"]},
                        headers={"Authorization": f"Bearer {a['key']}"})
        assert r.status_code == 403, r.text
        assert self._token_rows(b["signup_token"])[0]["revoked_at"] is None
        # B can still recover — the attempted cross-team kill changed nothing
        r2 = client.post("/v1/agent/recover",
                         json={"signup_token": b["signup_token"]})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"] == b["team_id"]

    def test_revoke_already_revoked_idempotent(self, client):
        data = _mint(client)
        headers = {"Authorization": f"Bearer {data['key']}"}
        first = client.post("/v1/agent/token/revoke",
                            json={"signup_token": data["signup_token"]},
                            headers=headers)
        assert first.status_code == 200 and first.json()["already"] is False
        second = client.post("/v1/agent/token/revoke",
                             json={"signup_token": data["signup_token"]},
                             headers=headers)
        assert second.status_code == 200, second.text
        assert second.json() == {"revoked": True, "already": True,
                                 "team_id": data["team_id"]}

    def test_revoke_writes_via_rpc_and_audits(self, client, monkeypatch):
        """Supabase lane: the write goes through the revoke_signup_token RPC
        (never a registry SDK — writer inventory) and records an audit event
        with resource_type='signup_token'."""
        captured: list[dict] = []

        async def _capture_audit(request, team_id, operation, **kw):
            captured.append({"team_id": team_id, "operation": operation, **kw})

        monkeypatch.setattr(ha_mod, "_async_audit", _capture_audit)
        data = _mint(client)
        r = client.post("/v1/agent/token/revoke",
                        json={"signup_token": data["signup_token"]},
                        headers={"Authorization": f"Bearer {data['key']}"})
        assert r.status_code == 200, r.text
        assert any(c[0] == "revoke_signup_token" for c in self.fake.rpc_calls)
        # the RPC body carries the team-scope guard (hash + team) — the wire
        # contract of the service_role RPC, not just its name
        assert ("revoke_signup_token",
                {"p_token_hash": _lookup_hash(data["signup_token"]),
                 "p_team_id": data["team_id"]}) in self.fake.rpc_calls
        revoke_evts = [e for e in captured
                       if e["operation"] == "agent_signup_token_revoke"]
        assert len(revoke_evts) == 1, captured
        ev = revoke_evts[0]
        assert ev["resource_type"] == "signup_token"
        assert ev["team_id"] == data["team_id"]
        assert ev["resource_id"] == data["team_id"]


class TestRegistryLane:
    """Revoke against the REAL FalkorDB registry (docker lane). The mint +
    revoke ride the same endpoint; the revoked token then 422s. The lane is
    PINNED (TORTOISE_CONTROL_PLANE=registry + creds deleted) so a shell with
    Supabase creds exported can never silently divert these tests."""

    @pytest.fixture(autouse=True)
    def _registry_env(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")

    def test_registry_revoke_then_recover_422(self, client, monkeypatch):
        data = _mint(client)
        token, team_id = data["signup_token"], data["team_id"]
        captured: list[dict] = []

        async def _capture_audit(request, team_id, operation, **kw):
            captured.append({"team_id": team_id, "operation": operation, **kw})

        monkeypatch.setattr(ha_mod, "_async_audit", _capture_audit)
        r = client.post("/v1/agent/token/revoke", json={"signup_token": token},
                        headers={"Authorization": f"Bearer {data['key']}"})
        assert r.status_code == 200, r.text
        assert r.json() == {"revoked": True, "already": False, "team_id": team_id}
        # the registry lane records the same audit event as the Supabase lane
        revoke_evts = [e for e in captured
                       if e["operation"] == "agent_signup_token_revoke"]
        assert len(revoke_evts) == 1, captured
        assert revoke_evts[0]["resource_type"] == "signup_token"
        assert revoke_evts[0]["resource_id"] == team_id
        # the node flipped
        sdk = ha_mod._make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (n:SignupToken {team_id:$tid}) RETURN n.revoked_at",
            params={"tid": team_id},
        ).result_set
        assert rows and rows[0][0] is not None
        # revoked token can no longer recover keys — uniform 422
        r2 = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r2.status_code == 422, r2.text
        assert r2.json()["detail"]["error_code"] == "invalid_signup_token"
        r3 = client.post("/v1/agent/signup", json={"signup_token": token})
        assert r3.status_code == 422, r3.text
        assert r3.json()["detail"]["error_code"] == "invalid_signup_token"

    def test_registry_revoke_team_scoped(self, client):
        a, b = _mint(client), _mint(client)
        # A cannot kill B's token (403) and B's token still recovers
        r = client.post("/v1/agent/token/revoke",
                        json={"signup_token": b["signup_token"]},
                        headers={"Authorization": f"Bearer {a['key']}"})
        assert r.status_code == 403, r.text
        r2 = client.post("/v1/agent/recover",
                         json={"signup_token": b["signup_token"]})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"] == b["team_id"]
        # unknown token → 404
        r3 = client.post("/v1/agent/token/revoke",
                         json={"signup_token": _st_token()},
                         headers={"Authorization": f"Bearer {a['key']}"})
        assert r3.status_code == 404, r3.text
        # already-revoked → idempotent already
        r4 = client.post("/v1/agent/token/revoke",
                         json={"signup_token": a["signup_token"]},
                         headers={"Authorization": f"Bearer {a['key']}"})
        assert r4.status_code == 200 and r4.json()["already"] is False
        r5 = client.post("/v1/agent/token/revoke",
                         json={"signup_token": a["signup_token"]},
                         headers={"Authorization": f"Bearer {a['key']}"})
        assert r5.status_code == 200 and r5.json()["already"] is True


class TestCmdTokenRevoke:
    """tortoise token-revoke — CLI UX (stored token or --token, #1708
    config resolver for the auth key)."""

    def _stored_cfg(self, tmp_path, **extra):
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        cfg = {"api_key": "tt_revoker", "api_url": "https://api.premiselabs.co",
               "team_id": "team-9", **extra}
        (d / "credentials.json").write_text(json.dumps(cfg))

    def test_token_revoke_happy_path(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        token = "st_" + "ef" * 32
        self._stored_cfg(tmp_path, signup_token=token)
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_json(
                            {"revoked": True, "already": False,
                             "team_id": "team-9"})) as urlopen:
            rc = main._cmd_token_revoke(mock.Mock(token=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "revoked" in out.lower()
        assert token[:14] in out
        req = urlopen.call_args.args[0]
        assert req.full_url.endswith("/v1/agent/token/revoke")
        assert req.method == "POST"
        assert req.headers.get("Authorization") == "Bearer tt_revoker"
        assert req.headers.get("Content-type") == "application/json"
        assert json.loads(req.data) == {"signup_token": token}

    def test_token_revoke_explicit_token_arg(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)  # no signup_token in the store
        token = "st_" + "aa" * 32
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_json(
                            {"revoked": True, "already": False,
                             "team_id": "team-9"})) as urlopen:
            rc = main._cmd_token_revoke(mock.Mock(token=token))
        assert rc == 0
        assert json.loads(urlopen.call_args.args[0].data) == {
            "signup_token": token}

    def test_token_revoke_no_token_fails(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)  # key present, no signup_token
        rc = main._cmd_token_revoke(mock.Mock(token=None))
        assert rc == 1
        assert "No recovery token found" in capsys.readouterr().err

    def test_token_revoke_no_api_key_fails(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        assert "No stored API key" in capsys.readouterr().err

    def test_token_revoke_422_invalid_token(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(422, json.dumps({
                            "detail": {"error_code": "invalid_signup_token"}}))):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        assert "invalid signup token" in capsys.readouterr().err

    def test_token_revoke_404_not_found(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(404, json.dumps(
                            {"detail": "Signup token not found"}))):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_token_revoke_401_rejected_key(self, monkeypatch, tmp_path, capsys):
        """Stale key after rotation: 401 → clear message, rc 1 (the stored
        key can no longer authenticate the revoke request)."""
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(401, json.dumps(
                            {"detail": "unauthorized"}))):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        assert "rejected" in capsys.readouterr().err

    def test_token_revoke_malformed_200_guard(self, monkeypatch, tmp_path, capsys):
        """#1715 fixer guard: a 200 with non-dict JSON (proxy garbage) must
        warn + exit 1 — never a KeyError traceback on data.get()."""
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_json(["ok"])):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        err = capsys.readouterr().err
        assert "malformed" in err
        assert "Traceback" not in err

    def test_token_revoke_legacy_cwd_file_config(self, monkeypatch, tmp_path, capsys):
        """Legacy shape: a plain FILE at cwd/.tortoise (pre-#1708) carries
        both the api_key and the signup_token — the resolvers must honor it
        (cwd beats ~/.tortoise/credentials.json precedence)."""
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        (tmp_path / ".tortoise").write_text(json.dumps({
            "api_key": "tt_legacy", "api_url": "https://api.premiselabs.co",
            "signup_token": "st_" + "cd" * 32}))
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_json({"revoked": True, "already": False,
                                               "team_id": "team-9"})) as urlopen:
            rc = main._cmd_token_revoke(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers.get("Authorization") == "Bearer tt_legacy"
        assert json.loads(req.data) == {"signup_token": "st_" + "cd" * 32}

    def test_token_revoke_already_revoked(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: _ok_json(
                            {"revoked": True, "already": True,
                             "team_id": "team-9"})):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already revoked" in out

    def test_token_revoke_network_error(self, monkeypatch, tmp_path, capsys):
        import tortoise.__main__ as main
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._stored_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=URLError("boom")):
            rc = main._cmd_token_revoke(mock.Mock(token="st_" + "ab" * 32))
        assert rc == 1
        assert "Cannot reach API" in capsys.readouterr().err
