"""Coverage for `tortoise team keys {list,create,revoke}` (#304).

GET /v1/team/keys → list (table or --json)
POST /v1/team/keys → create (key shown once)
DELETE /v1/team/keys/{id} → revoke (confirm unless --force/--json)

Error paths: no config, 401/403 (key rejected), 402 (limit), 429 (rate
limit), 404 (not found), cross-team 403 — all clean stderr messages, exit 1.
"""
from __future__ import annotations

import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.error import HTTPError, URLError

import pytest

from tortoise.__main__ import main


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials, and
    never resolve a stray ./.tortoise file in the pytest CWD (a repo-root
    .tortoise would 401→re-mint and break the reuse tests)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

CONFIG = (
    '{"api_key": "tt_testkey", "api_url": "https://api.premiselabs.co", "team_id": "team123"}\n'
)

LIST_BODY = json.dumps({
    "keys": [
        {"id": "k1", "key_prefix": "tt_abc", "created_at": "2026-08-01T12:00:00",
         "last_used_at": "2026-08-11T09:00:00", "revoked_at": None, "name": "CI"},
        {"id": "k2", "key_prefix": "tt_def", "created_at": "2026-08-02T08:00:00",
         "last_used_at": None, "revoked_at": "2026-08-03T14:00:00", "name": None},
    ]
}).encode()

CREATE_BODY = json.dumps({
    "id": "kid1", "key": "tt_fullkey", "key_prefix": "tt_full",
    "created_at": "2026-08-11T10:00:00Z", "name": "staging",
}).encode()


def _http_error(code: int, msg: str = "err", body: bytes = b"") -> HTTPError:
    return HTTPError("https://api.premiselabs.co/v1/team/keys", code, msg, {}, io.BytesIO(body))


def _ok_response(body: bytes) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class TestTeamKeysList:
    def _cfg(self, tmp_path):
        (tmp_path / ".tortoise").write_text(CONFIG)

    def test_list_keys_json(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(LIST_BODY)) as urlopen:
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["team_id"] == "team123"  # from .tortoise (API returns keys only)
        assert [k["id"] for k in out["keys"]] == ["k1", "k2"]
        assert out["keys"][1]["revoked_at"]  # revoked key surfaced
        # 20260825000001: labels ride the list payload (nullable)
        assert out["keys"][0]["name"] == "CI"
        assert out["keys"][1]["name"] is None
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/team/keys"
        assert req.headers["Authorization"] == "Bearer tt_testkey"

    def test_list_keys_human_table(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(LIST_BODY)):
            rc = main(["team", "keys", "list"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "API keys for team team123:" in out
        assert "k1" in out and "tt_abc" in out
        assert "active" in out and "revoked" in out
        assert "never" in out  # last_used_at null → never
        # 20260825000001: label column — named key shows it, unnamed shows ''
        assert "CI" in out

    def test_list_keys_no_config(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["team", "keys", "list"])
        assert rc == 1
        assert "Run 'tortoise init --api-key <key>' first" in capsys.readouterr().err

    def test_list_keys_unauthorized(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
            rc = main(["team", "keys", "list"])
        assert rc == 1
        assert "API key rejected — re-run tortoise init --api-key" in capsys.readouterr().err

    def test_list_keys_network_error(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            rc = main(["team", "keys", "list"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Cannot reach API at https://api.premiselabs.co" in err

    # ── non-object 2xx body ([]/null) → clean error, no traceback (#875 P2) ──
    def test_list_keys_non_object_response(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b"[]")):
            rc = main(["team", "keys", "list"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "expected a JSON object, got list" in captured.err
        assert "Traceback" not in captured.err

    def test_list_keys_null_response_json(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b"null")):
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out == {"status": "error", "error": "invalid_response",
                       "message": "Invalid response from API: expected a JSON object, got NoneType."}
        assert "expected a JSON object" in captured.err

    # ── --json error contract: JSON on stdout, human text on stderr (#875 P2) ──
    def test_list_keys_json_error_unauthorized(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "key_rejected"
        assert out["http_code"] == 401
        assert "API key rejected" in out["message"]
        assert "API key rejected — re-run tortoise init --api-key" in captured.err

    def test_list_keys_json_error_network(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "network"
        assert "Cannot reach API" in out["message"]
        assert "Cannot reach API at https://api.premiselabs.co" in captured.err

    def test_list_keys_json_error_no_config(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        rc = main(["team", "keys", "list", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "no_config"
        assert "tortoise init --api-key" in out["message"]
        assert "Run 'tortoise init --api-key <key>' first" in captured.err


class TestTeamKeysCreate:
    def _cfg(self, tmp_path):
        (tmp_path / ".tortoise").write_text(CONFIG)

    def test_create_key_json(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(CREATE_BODY)) as urlopen:
            rc = main(["team", "keys", "create", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["key"] == "tt_fullkey"
        assert out["key_prefix"] == "tt_full"
        assert out["id"] == "kid1"
        assert out["created_at"] == "2026-08-11T10:00:00Z"
        assert out["team_id"] == "team123"
        # 20260825000001: the response label is surfaced in JSON output
        assert out["name"] == "staging"
        req = urlopen.call_args.args[0]
        assert req.get_method() == "POST"
        assert req.full_url == "https://api.premiselabs.co/v1/team/keys"
        assert json.loads(req.data) == {}  # no --name → empty body

    def test_create_key_with_name_sends_label(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(CREATE_BODY)) as urlopen:
            rc = main(["team", "keys", "create", "--name", "staging"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Name:        staging" in out
        req = urlopen.call_args.args[0]
        assert json.loads(req.data) == {"name": "staging"}
        # urllib normalizes header case (Content-Type → Content-type)
        assert req.headers["Content-type"] == "application/json"

    def test_create_key_name_clamped_to_64_chars(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(CREATE_BODY)) as urlopen:
            rc = main(["team", "keys", "create", "--name", "x" * 200])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert len(json.loads(req.data)["name"]) == 64

    def test_create_key_human(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(CREATE_BODY)):
            rc = main(["team", "keys", "create"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Created new API key: tt_fullkey" in out
        assert "Store this key — it won't be shown again." in out
        assert "full access to your team's graph" in out

    def test_create_key_at_limit(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(402, "Limit")):
            rc = main(["team", "keys", "create"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "API key limit reached (max 3 for free tier)" in err
        assert "Revoke an existing key first" in err

    def test_create_key_rate_limited(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(429, "Rate")):
            rc = main(["team", "keys", "create"])
        assert rc == 1
        assert "Too many keys created recently — try again in 60s." in capsys.readouterr().err

    def test_create_key_unauthorized(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
            rc = main(["team", "keys", "create"])
        assert rc == 1
        assert "API key rejected" in capsys.readouterr().err

    # ── non-object 2xx body → clean error, no traceback (#875 P2) ──
    def test_create_key_non_object_response(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b"[]")):
            rc = main(["team", "keys", "create"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "expected a JSON object, got list" in captured.err
        assert "Traceback" not in captured.err

    # ── --json error contract: JSON on stdout, human text on stderr (#875 P2) ──
    def test_create_key_json_error_limit(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(402, "Limit")):
            rc = main(["team", "keys", "create", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "limit_reached"
        assert out["http_code"] == 402
        assert "API key limit reached" in out["message"]
        assert "API key limit reached (max 3 for free tier)" in captured.err

    def test_create_key_json_error_rate_limited(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(429, "Rate")):
            rc = main(["team", "keys", "create", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "rate_limited"
        assert out["http_code"] == 429
        assert "Too many keys created recently" in captured.err


class TestTeamKeysRevoke:
    def _cfg(self, tmp_path):
        (tmp_path / ".tortoise").write_text(CONFIG)

    def test_revoke_key_confirm_yes(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("builtins.input", return_value="y") as inp, \
             mock.patch("urllib.request.urlopen", return_value=_ok_response(
                 b'{"revoked": true, "key_id": "kid1", "revoked_at": "2026-08-11T10:05:00Z"}')) as urlopen:
            rc = main(["team", "keys", "revoke", "kid1"])
        assert rc == 0
        assert inp.called
        assert "✅ API key kid1 revoked." in capsys.readouterr().out
        req = urlopen.call_args.args[0]
        assert req.get_method() == "DELETE"
        assert req.full_url == "https://api.premiselabs.co/v1/team/keys/kid1"

    def test_revoke_key_confirm_no(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("builtins.input", return_value="n"), \
             mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["team", "keys", "revoke", "kid1"])
        assert rc == 1
        assert not urlopen.called  # declined → no API call
        assert "Aborted." in capsys.readouterr().err

    def test_revoke_key_force(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("builtins.input") as inp, \
             mock.patch("urllib.request.urlopen", return_value=_ok_response(
                 b'{"revoked": true, "key_id": "kid1"}')) as urlopen:
            rc = main(["team", "keys", "revoke", "kid1", "--force"])
        assert rc == 0
        assert not inp.called  # --force skips the prompt
        assert urlopen.called

    def test_revoke_key_json_skips_prompt(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("builtins.input") as inp, \
             mock.patch("urllib.request.urlopen", return_value=_ok_response(
                 b'{"revoked": true, "key_id": "kid1", "revoked_at": "2026-08-11T10:05:00Z"}')) as urlopen:  # noqa: F841
            rc = main(["team", "keys", "revoke", "kid1", "--json"])
        assert rc == 0
        assert not inp.called
        out = json.loads(capsys.readouterr().out)
        assert out == {"revoked": True, "key_id": "kid1", "revoked_at": "2026-08-11T10:05:00Z"}

    def test_revoke_key_not_found(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            rc = main(["team", "keys", "revoke", "ghost", "--force"])
        assert rc == 1
        assert "API key not found" in capsys.readouterr().err

    def test_revoke_key_cross_team(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(403, "Forbidden")):
            rc = main(["team", "keys", "revoke", "otherkid", "--force"])
        assert rc == 1
        assert "Cannot revoke — this key belongs to a different team" in capsys.readouterr().err

    def test_revoke_key_already_revoked(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(
                b'{"revoked": true, "already": true, "key_id": "kid1"}')):
            rc = main(["team", "keys", "revoke", "kid1", "--force"])
        assert rc == 0  # idempotent
        assert "was already revoked (idempotent)" in capsys.readouterr().out

    def test_revoke_key_unauthorized(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized")):
            rc = main(["team", "keys", "revoke", "kid1", "--force"])
        assert rc == 1
        assert "API key rejected — re-run tortoise init --api-key" in capsys.readouterr().err

    # ── non-object 2xx body → clean error, no traceback (#875 P2) ──
    def test_revoke_key_non_object_response(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b"null")):
            rc = main(["team", "keys", "revoke", "kid1", "--force"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "expected a JSON object, got NoneType" in captured.err
        assert "Traceback" not in captured.err

    # ── --json error contract: JSON on stdout, human text on stderr (#875 P2) ──
    def test_revoke_key_json_error_not_found(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            rc = main(["team", "keys", "revoke", "ghost", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "not_found"
        assert out["http_code"] == 404
        assert "API key not found" in captured.err

    def test_revoke_key_json_error_cross_team(self, monkeypatch, tmp_path, capsys):
        self._cfg(tmp_path)
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(403, "Forbidden")):
            rc = main(["team", "keys", "revoke", "otherkid", "--json"])
        assert rc == 1
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["status"] == "error"
        assert out["error"] == "cross_team"
        assert out["http_code"] == 403
        assert "Cannot revoke — this key belongs to a different team" in captured.err
