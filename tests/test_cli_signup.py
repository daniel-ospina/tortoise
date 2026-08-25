"""CLI `tortoise signup` tests (#1081)."""
import json
from unittest import mock
from urllib.error import HTTPError

import pytest

import tortoise.__main__ as main


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials, and
    never resolve a stray ./.tortoise file in the pytest CWD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def _http_error(code, body, headers=None):
    import io
    from email.message import Message
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return HTTPError("https://api.premiselabs.co/v1/agent/signup", code,
                     "err", msg, io.BytesIO(body.encode()))


class TestSignup429:
    def test_429_prints_retry_and_support(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(
                            429, json.dumps({"detail": {"error_code": "over_signup_ip_rate_limit"}}),
                            {"Retry-After": "86399"})):  # computed remaining (P2-FIX-5)
            rc = main._cmd_signup(mock.Mock())
        err = capsys.readouterr().err
        assert rc == 1
        assert "86399" in err or "24h" in err or "later" in err
        assert "support@premiselabs.co" in err
        assert "Signup rate limit" in err

    def test_non_429_error_unchanged(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(
                            500, json.dumps({"detail": "boom"}))):
            rc = main._cmd_signup(mock.Mock())
        err = capsys.readouterr().err
        assert rc == 1
        assert "Signup failed (500)" in err
        assert "boom" in err


def _ok_mint(body=None):
    import json as _j
    resp = mock.MagicMock()
    resp.read.return_value = _j.dumps(body or {
        "key": "tt_mint_000000000000000000000000000000000000000000",
        "team_id": "team-mint-1", "team_name": "agent-mint", "graph_name": "team_team-mint-1",
        "identity": "anon-mint", "tier": "free"}).encode()
    resp.__enter__.return_value = resp
    return resp


class TestGlobalWrite:
    def test_signup_from_home_writes_global_no_crash(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)  # cwd IS home (the bug)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 0
        cfg_path = tmp_path / ".tortoise" / "credentials.json"
        assert cfg_path.is_file(), f"expected {cfg_path} (was IsADirectoryError before #1708)"
        assert (cfg_path.stat().st_mode & 0o777) == 0o600
        assert (tmp_path / ".tortoise").stat().st_mode & 0o077 == 0  # dir 0700
        cfg = json.loads(cfg_path.read_text())
        assert cfg["api_key"].startswith("tt_")
        assert cfg["device_id"].startswith("anon-")

    def test_signup_from_home_with_pre_existing_data_home(self, monkeypatch, tmp_path):
        """The exact incident mechanism: ~/.tortoise ALREADY exists as the data
        home (tortoise.db + audit logs) and cwd == HOME — the old write to
        cwd/.tortoise raised IsADirectoryError AFTER minting."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        data_home = tmp_path / ".tortoise"
        data_home.mkdir(parents=True, exist_ok=True)
        (data_home / "tortoise.db").write_bytes(b"\x00" * 16)  # data home marker
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 0
        assert (data_home / "credentials.json").is_file()  # written INSIDE the dir
        assert (data_home / "tortoise.db").is_file()  # data home untouched

    def test_no_cwd_config_written(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        sub = tmp_path / "sub"
        sub.mkdir()
        monkeypatch.chdir(sub)  # cwd != HOME
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert not (sub / ".tortoise").exists(), "signup must not write cwd/.tortoise"
        assert (tmp_path / ".tortoise" / "credentials.json").is_file()

    def test_mint_write_failure_echoes_key_exits_1(self, monkeypatch, tmp_path, capsys):
        """Orphan class: mint succeeds, save fails → key echoed, exit 1, never exit 0."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()), \
             mock.patch("os.replace", side_effect=OSError("read-only fs")):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        err = capsys.readouterr().err
        assert "could NOT be saved" in err
        assert "tt_mint_" in err  # the minted key is echoed so it isn't lost

    def test_mint_mkdir_failure_exits_1(self, monkeypatch, tmp_path, capsys):
        """The mkdir/chmod legs of the write-failure handler (not just os.replace)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()), \
             mock.patch("pathlib.Path.mkdir", side_effect=OSError("EACCES")):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        assert "could NOT be saved" in capsys.readouterr().err

    def test_successful_write_cleans_stale_tmp(self, monkeypatch, tmp_path):
        """A crashed writer leaves credentials.json.tmp-* behind (key material) —
        the next successful write must sweep them."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        stale = d / "credentials.json.tmp-deadbeef"
        stale.write_text("partial key material")
        stale.chmod(0o600)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert not stale.exists(), "stale tmp must be swept on next successful write"

    def test_device_id_stable_across_mints(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        first = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())["device_id"]
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock(force=True))  # --force re-mints, same device
        second = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())["device_id"]
        assert first == second
