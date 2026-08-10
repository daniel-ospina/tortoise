"""Coverage for `tortoise init --api-key` validation branches (#707).

Validates that the CLI classifies validation responses correctly:
- 200 JSON → validated → config saved
- 401/403 → key rejected → hard fail, config NOT saved
- 404 → misconfigured URL → hard fail with distinct message, config NOT saved
- 5xx/429/408 → transient → retry once, then warn + save
- network errors / http.client failures → warn + save (offline)
- 200 with non-JSON body → unvalidated → hard fail, config NOT saved
- trailing-slash TORTOISE_API_URL → normalized before request
"""
from __future__ import annotations

import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.error import HTTPError, URLError
from http.client import IncompleteRead

from tortoise.__main__ import main


def _http_error(code: int, msg: str = "err", body: bytes = b"") -> HTTPError:
    return HTTPError("https://api.premiselabs.co/v1/team", code, msg, {}, io.BytesIO(body))


def _ok_response(body: bytes = b'{"team": "premise"}') -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class TestInitApiKeyValidation:
    def _run(self, monkeypatch, tmp_path, urlopen_mock):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co")
        return main(["init", "--api-key", "tt_testkey"])

    def _config(self, tmp_path):
        cfg = tmp_path / ".tortoise"
        return cfg.read_text() if cfg.exists() else None

    # ── valid key (200) → saved ────────────────────────────────
    def test_valid_key_200_saves(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        cfg = self._config(tmp_path)
        assert cfg is not None
        assert '"api_key": "tt_testkey"' in cfg
        assert "API key validated" in capsys.readouterr().out

    # ── 401 / 403 → hard fail, no save ─────────────────────────
    def test_401_hard_fails_no_save(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401, "Unauthorized", b'{"error":"bad key"}')) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 1
        assert self._config(tmp_path) is None
        err = capsys.readouterr().err
        assert "API rejected the key (401)" in err
        assert "Config NOT saved" in err

    def test_403_hard_fails_no_save(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(403, "Forbidden")) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 1
        assert self._config(tmp_path) is None
        assert "API rejected the key (403)" in capsys.readouterr().err

    # ── transient 5xx/429/408 → retry once → warn + save ───────
    def test_500_transient_retries_then_saves(self, monkeypatch, tmp_path, capsys):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(500, "Internal Server Error"), _http_error(500, "Internal Server Error")],
        ) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        assert self._config(tmp_path) is not None
        assert urlopen.call_count == 2  # retried once before giving up
        out = capsys.readouterr().out
        assert "transient error" in out
        assert "Saving config anyway" in out

    def test_500_then_200_retry_succeeds(self, monkeypatch, tmp_path, capsys):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(500, "Internal Server Error"), _ok_response()],
        ) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        assert self._config(tmp_path) is not None
        assert urlopen.call_count == 2
        assert "API key validated" in capsys.readouterr().out

    def test_429_transient_warns_saves(self, monkeypatch, tmp_path, capsys):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(429, "Too Many Requests"), _http_error(429, "Too Many Requests")],
        ) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        assert self._config(tmp_path) is not None
        assert "Saving config anyway" in capsys.readouterr().out

    # ── 404 → misconfigured URL → hard fail, distinct message ─
    def test_404_misconfigured_url_hard_fails(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 1
        assert self._config(tmp_path) is None
        err = capsys.readouterr().err
        assert "API URL appears misconfigured" in err
        assert "Config NOT saved" in err
        assert "API rejected the key" not in err  # distinct from key rejection

    # ── network error → warn + save ────────────────────────────
    def test_network_error_warns_saves(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=URLError("connection refused")) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        assert self._config(tmp_path) is not None
        out = capsys.readouterr().out
        assert "Could not reach" in out
        assert "Saving config anyway" in out

    # ── http.client failure (IncompleteRead) → warn + save ─────
    def test_http_client_exception_warns_saves(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=IncompleteRead(b"partial")) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 0
        assert self._config(tmp_path) is not None
        out = capsys.readouterr().out
        assert "Could not reach" in out
        assert "Saving config anyway" in out

    # ── 200 with non-JSON body → unvalidated → no save ─────────
    def test_non_json_200_not_saved(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b"<html>captive portal</html>")) as urlopen:
            rc = self._run(monkeypatch, tmp_path, urlopen)
        assert rc == 1
        assert self._config(tmp_path) is None
        err = capsys.readouterr().err
        assert "non-JSON response" in err
        assert "Config NOT saved" in err

    # ── trailing-slash base URL → normalized before request ────
    def test_trailing_slash_normalized(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co/")
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()) as urlopen:
            rc = main(["init", "--api-key", "tt_testkey"])
        assert rc == 0
        # Request went to a single-slash path, not //v1/team.
        req_url = urlopen.call_args.args[0].full_url
        assert req_url == "https://api.premiselabs.co/v1/team"
        # Saved config stores the normalized URL.
        cfg = self._config(tmp_path)
        assert cfg is not None
        assert '"api_url": "https://api.premiselabs.co"' in cfg


class TestInitApiKeyEnhancements:
    """#304: init --api-key output enhancements — --json / --harness / --write-mcp-config."""

    def _run(self, monkeypatch, tmp_path, argv):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co")
        return main(argv)

    def _config(self, tmp_path):
        cfg = tmp_path / ".tortoise"
        return cfg.read_text() if cfg.exists() else None

    # ── --json success shape (agent consumption) ──────────────
    def test_json_output_valid_key(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response(b'{"team_id": "team123"}')):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)  # stdout is pure JSON
        assert out["status"] == "connected"
        assert out["team_id"] == "team123"
        assert out["api_url"] == "https://api.premiselabs.co"
        assert out["mcp"]["endpoint"] == "https://api.premiselabs.co/mcp"
        assert out["mcp"]["auth_header"] == "Bearer tt_testkey"
        assert set(out["mcp"]["configs"]) == {"claude", "codex", "cursor", "pi"}
        claude = out["mcp"]["configs"]["claude"]
        assert claude["file"] == ".mcp.json"
        assert claude["config"]["mcpServers"]["tortoise"]["type"] == "streamable-http"
        assert claude["config"]["mcpServers"]["tortoise"]["headers"]["Authorization"] == "Bearer tt_testkey"
        # cursor shape has no `type` field; codex is a command
        assert "type" not in out["mcp"]["configs"]["cursor"]["config"]["mcpServers"]["tortoise"]
        assert out["mcp"]["configs"]["codex"]["command"].startswith("codex mcp add tortoise")
        assert out["onboarding_prompt_url"] == "https://premiselabs.co/onboarding-prompt.md"
        assert out["config_path"].endswith(".tortoise")
        assert out["next_steps"]
        # config still saved with 600 perms
        cfg_path = tmp_path / ".tortoise"
        assert '"api_key": "tt_testkey"' in cfg_path.read_text()
        assert (cfg_path.stat().st_mode & 0o777) == 0o600

    # ── --json error shapes (all 5 paths → parseable JSON) ────
    def test_json_output_rejected_key(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(403, "Forbidden", b'{"error":"bad key"}')):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_bad", "--json"])
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out == {"status": "error", "error": "key_rejected", "http_code": 403,
                       "message": "API rejected the key (403): {\"error\":\"bad key\"}"}
        assert self._config(tmp_path) is None  # hard fail → NOT saved

    def test_json_output_offline(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=URLError("connection refused")):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--json"])
        assert rc == 0  # warn + save (existing behavior)
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["error"] == "offline"
        assert "Could not reach" in out["message"]
        assert out["config_saved"] is True
        assert self._config(tmp_path) is not None  # saved anyway

    def test_json_output_transient(self, monkeypatch, tmp_path, capsys):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=[_http_error(500, "Internal Server Error"), _http_error(500, "Internal Server Error")],
        ):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--json"])
        assert rc == 0
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["error"] == "transient"
        assert out["http_code"] == 500
        assert out["config_saved"] is True
        assert self._config(tmp_path) is not None

    def test_json_output_bad_url(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(404, "Not Found")):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--json"])
        assert rc == 1
        out = json.loads(capsys.readouterr().out)
        assert out["status"] == "error"
        assert out["error"] == "bad_url"
        assert self._config(tmp_path) is None

    # ── --harness filter ──────────────────────────────────────
    def test_harness_filter(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--harness", "claude"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Claude Code" in out
        assert '"type": "streamable-http"' in out
        assert "codex mcp add" not in out  # only the claude config
        assert "Cursor" not in out
        assert "[2] Codex" not in out
        assert "onboarding-prompt.md" in out

    # ── --write-mcp-config ────────────────────────────────────
    def test_write_mcp_config_creates_file(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path,
                           ["init", "--api-key", "tt_testkey", "--harness", "claude", "--write-mcp-config"])
        assert rc == 0
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        tortoise = mcp["mcpServers"]["tortoise"]
        assert tortoise["type"] == "streamable-http"
        assert tortoise["url"] == "https://api.premiselabs.co/mcp"
        assert tortoise["headers"]["Authorization"] == "Bearer tt_testkey"
        assert "Wrote MCP config" in capsys.readouterr().out

    def test_write_mcp_config_merges_existing(self, monkeypatch, tmp_path, capsys):
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"other": {"command": "x"}}, "extra": 1}))
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path,
                           ["init", "--api-key", "tt_testkey", "--harness", "pi", "--write-mcp-config"])
        assert rc == 0
        mcp = json.loads((tmp_path / ".mcp.json").read_text())
        assert mcp["extra"] == 1  # other top-level keys preserved
        assert "other" in mcp["mcpServers"]  # other servers preserved
        assert mcp["mcpServers"]["tortoise"]["type"] == "streamable-http"

    def test_write_mcp_config_no_harness_errors(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_testkey", "--write-mcp-config"])
        assert rc == 1
        assert "--harness required" in capsys.readouterr().err
        assert not (tmp_path / ".mcp.json").exists()

    def test_write_mcp_config_existing_tortoise_entry_warns(self, monkeypatch, tmp_path, capsys):
        # Pre-seed .tortoise with a DIFFERENT key so both runs validate
        # (otherwise the 2nd run short-circuits on already-connected).
        (tmp_path / ".tortoise").write_text(
            json.dumps({"api_key": "tt_cfg", "api_url": "https://api.premiselabs.co"}))
        (tmp_path / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"tortoise": {"url": "old"}}}))
        # without --force: refused, file untouched
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path,
                           ["init", "--api-key", "tt_testkey", "--harness", "claude", "--write-mcp-config"])
        assert rc == 1
        assert "already configured" in capsys.readouterr().err
        assert '"url": "old"' in (tmp_path / ".mcp.json").read_text()
        # with --force: overwritten (different key so the 2nd run isn't
        # short-circuited by already-connected)
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path,
                           ["init", "--api-key", "tt_testkey2", "--harness", "claude", "--write-mcp-config", "--force"])
        assert rc == 0
        assert '"url": "old"' not in (tmp_path / ".mcp.json").read_text()

    def test_write_mcp_config_codex_prints_command(self, monkeypatch, tmp_path, capsys):
        with mock.patch("urllib.request.urlopen", return_value=_ok_response()):
            rc = self._run(monkeypatch, tmp_path,
                           ["init", "--api-key", "tt_testkey", "--harness", "codex", "--write-mcp-config"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "codex mcp add tortoise --url https://api.premiselabs.co/mcp --bearer-token-env-var TORTOISE_API_KEY" in out
        assert not (tmp_path / ".mcp.json").exists()  # codex is command-based

    # ── already-connected (idempotent, no API call) ───────────
    def test_already_connected_same_key(self, monkeypatch, tmp_path, capsys):
        (tmp_path / ".tortoise").write_text(
            json.dumps({"api_key": "tt_same", "api_url": "https://api.premiselabs.co"}))
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = self._run(monkeypatch, tmp_path, ["init", "--api-key", "tt_same"])
        assert rc == 0
        assert not urlopen.called  # no API call
        assert "Already connected" in capsys.readouterr().out
