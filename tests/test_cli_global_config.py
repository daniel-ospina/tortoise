"""#1708 Task 1 acceptance — command-level smoke tests.

1. Global-config smoke: commands resolve `~/.tortoise/credentials.json` when
   there is no cwd `.tortoise` (create-point / context / session / team all
   issue their request with the global key).
2. Env-only smoke: TORTOISE_API_KEY set + no files → hosted commands resolve
   the env key (the config=None crash guard: config.get(...) must never see
   None).
3. D1b regression: env alone must NOT flip `tortoise context` to hosted mode;
   a global config still does (include_env=False).
"""
from __future__ import annotations

import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.__main__ import main

GLOBAL_CFG = {
    "api_key": "tt_global", "api_url": "https://api.premiselabs.co",
    "team_id": "team-g", "team_name": "Global", "device_id": "anon-g",
}


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.chdir(tmp_path)


def _seed_global(tmp_path):
    d = tmp_path / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    d.chmod(0o700)
    f = d / "credentials.json"
    f.write_text(json.dumps(GLOBAL_CFG))
    f.chmod(0o600)


def _ok(body: dict) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


class TestGlobalConfigCommands:
    """Command-level acceptance: create-point/context/session/team work from a
    global config (no cwd .tortoise)."""

    def test_team_info_from_global(self, tmp_path, monkeypatch, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"team_id": "team-g", "tier": "free", "point_count": 0})) as urlopen:
            rc = main(["team", "info"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/team"
        assert req.headers["Authorization"] == "Bearer tt_global"
        assert "Team:       team-g" in capsys.readouterr().out

    def test_create_point_from_global(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "p1", "kind": "statement"})) as urlopen:
            rc = main(["create-point", "hello world", "--kind", "statement"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/points"
        assert req.headers["Authorization"] == "Bearer tt_global"

    def test_session_capture_from_global(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        transcript = tmp_path / "conv.txt"
        transcript.write_text("User: hello\nAssistant: hi there\n")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"session_id": "s1"})) as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://api.premiselabs.co/v1/sessions"
        assert req.headers["Authorization"] == "Bearer tt_global"

    def test_context_hosted_from_global(self, tmp_path, monkeypatch, capsys):
        _seed_global(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "remembered", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/context")
                   for call in urlopen.call_args_list)
        assert urlopen.call_args.args[0].headers["Authorization"] == "Bearer tt_global"
        assert "remembered" in capsys.readouterr().out


class TestConfigErrorCommandHandling:
    """D6 per-site _ConfigError handling: a corrupt/unreadable global config
    must never traceback — context falls back to local mode, create-point and
    session fail cleanly with rc 1 ("Invalid config at {path}")."""

    @staticmethod
    def _seed_corrupt_global(tmp_path):
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text("{not json")

    def test_context_corrupt_global_falls_back_to_local(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "local.db"))
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert not urlopen.called, "corrupt config must fall back to the local SDK path"
        cap = capsys.readouterr()  # readouterr DRAINS the buffer — capture ONCE
        assert "corrupt or unreadable" in cap.err
        assert "no prior sessions" in cap.out

    def test_create_point_corrupt_global_fails_closed(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["create-point", "hello world"])
        assert rc == 1
        assert not urlopen.called
        assert "Invalid config at" in capsys.readouterr().err

    def test_session_capture_corrupt_global_fails_closed(self, tmp_path, monkeypatch, capsys):
        self._seed_corrupt_global(tmp_path)
        transcript = tmp_path / "conv.txt"
        transcript.write_text("User: hello\nAssistant: hi\n")
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["session", "capture", "--file", str(transcript)])
        assert rc == 1
        assert not urlopen.called
        assert "Invalid config at" in capsys.readouterr().err


class TestEnvOnlyResolution:
    """TORTOISE_API_KEY set + no files → hosted commands resolve the env key
    (the config=None crash guard: config.get(...) must never see None)."""

    def test_team_keys_list_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"keys": [{"id": "k1", "key_prefix": "tt_env"}]})) as urlopen:
            rc = main(["team", "keys", "list"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_envkey"
        assert req.full_url == "https://api.premiselabs.co/v1/team/keys"

    def test_team_keys_list_json_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"keys": [{"id": "k1", "key_prefix": "tt_env"}]})) as urlopen:
            rc = main(["team", "keys", "list", "--json"])
        assert rc == 0
        assert urlopen.call_args.args[0].headers["Authorization"] == "Bearer tt_envkey"

    def test_team_keys_create_json_env_only(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"id": "kid1", "key": "tt_newkey", "key_prefix": "tt_new"})) as urlopen:
            rc = main(["team", "keys", "create", "--json"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_envkey"
        assert req.get_method() == "POST"


class TestContextD1bRegression:
    """D1b: env alone must NOT flip `tortoise context` to hosted mode; a
    global config still does (include_env=False)."""

    def test_context_env_only_stays_local(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "local.db"))
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main(["context"])
        assert rc == 0
        assert not urlopen.called, "env alone must keep context on the local SDK path"
        assert "no prior sessions" in capsys.readouterr().out

    def test_context_global_config_still_hosted_with_env_key_set(self, tmp_path, monkeypatch):
        _seed_global(tmp_path)
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        with mock.patch("urllib.request.urlopen", return_value=_ok(
                {"diary_entries": [], "recent_points": [
                    {"content": "hosted digest", "pointKind": "observation"}],
                 "recent_events": []})) as urlopen:
            rc = main(["context"])
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.headers["Authorization"] == "Bearer tt_global"
