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
