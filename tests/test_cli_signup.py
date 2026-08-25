"""CLI `tortoise signup` tests (#1081, #1708 reuse-before-mint)."""
import io
import json
from unittest import mock
from urllib.error import HTTPError, URLError

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


# ── #1708 reuse-before-mint (D2/D3) ──────────────────────────────────────────
# All reuse-path calls pass mock.Mock(force=False) — a bare mock.Mock() has
# truthy attribute access, so getattr(args, "force", False) would be truthy
# and the reuse gate would always be skipped (vacuous tests).


class TestReuse:
    def _valid_team(self):  # GET /v1/team 200
        import json as _j
        resp = mock.MagicMock()
        resp.read.return_value = _j.dumps(
            {"team_id": "team-g", "tier": "free"}).encode()
        resp.__enter__.return_value = resp
        return resp

    def _global_cfg(self, tmp_path, **extra):
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        d.chmod(0o700)
        cfg = {"api_key": "tt_valid", "api_url": "https://api.premiselabs.co",
               "team_id": "team-g", **extra}
        (d / "credentials.json").write_text(json.dumps(cfg))

    def test_reuse_global_config_skips_mint(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert "already" in out.lower() or "reus" in out.lower()
        # 0 new keys: the mint POST was NEVER issued
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_from_other_cwd(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        (tmp_path / "elsewhere").mkdir()
        monkeypatch.chdir(tmp_path / "elsewhere")
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_env_key(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_envkey")
        monkeypatch.setenv("HOME", str(tmp_path))
        with mock.patch("urllib.request.urlopen", return_value=self._valid_team()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_invalid_key_remints(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "unauthorized", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0  # re-minted after 401
        # mirror the skip-mint tests: a regression that treats 401 as "reuse"
        # (rc 0, no mint) must fail here
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_forbidden_not_suspended_remints(self, monkeypatch, tmp_path):
        """Non-SUSPENDED 403 = key rejected → re-mint (D2); only SUSPENDED
        fail-closes. Both 403 branches must be pinned."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_rejected")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 403, "forbidden", {},
                                  io.BytesIO(b'{"detail":"not allowed"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_invalid_key_remints_from_cwd_config(self, monkeypatch, tmp_path):
        """Legacy upgrade path: a pre-#1708 cwd/.tortoise (no device_id) whose
        key 401s → re-mint MUST persist the device_id to the GLOBAL file so
        client identity stays anchored (future #1709 dedupe)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        (tmp_path / "proj").mkdir()
        monkeypatch.chdir(tmp_path / "proj")
        (tmp_path / "proj" / ".tortoise").write_text(json.dumps(
            {"api_key": "tt_old", "api_url": "https://api.premiselabs.co", "team_id": "team-old"}))
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        global_cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert global_cfg["device_id"].startswith("anon-")
        mint_body = urlopen.call_args_list[-1].args[0].data.decode()
        assert json.loads(mint_body)["identity"] == global_cfg["device_id"]

    def test_reuse_validation_timeout_fail_closed(self, monkeypatch, tmp_path, capsys):
        """socket.timeout / TimeoutError from resp.read() after headers arrive is
        NOT a URLError — the except tuple must include it (flaky-proxy stall)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=TimeoutError("timed out")) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_remint_post_timeout_reports_orphan(self, monkeypatch, tmp_path, capsys):
        """401 → re-mint POST hangs (timeout): the server may have minted —
        the message must not say 'Cannot reach API' (that misleads into retry)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        TimeoutError("timed out")]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "may have been minted" in err.lower() or "not blindly" in err.lower()

    def test_mint_200_garbage_reports_orphan(self, monkeypatch, tmp_path, capsys):
        """Mint POST returns 200 with an HTML body (proxy) — the server DID
        mint; 'Cannot reach API' would mislead the user into double-firing."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        bad = mock.MagicMock()
        bad.read.return_value = b"<html>Sign in</html>"
        bad.__enter__.return_value = bad
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        bad]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "may have been minted" in err.lower()
        assert "cannot reach api" not in err.lower()

    def test_reuse_env_url_vs_stored_url(self, monkeypatch, tmp_path):
        """TORTOISE_API_URL env AND a 401-ing stored config URL: D2 pins the
        mint to the CONFIG URL; the message must surface which host is used."""
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_url="https://old-host.example.com", api_key="tt_stale")
        side_effects = [HTTPError("https://old-host.example.com/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.startswith("https://old-host.example.com/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_concurrent_signup_writers_no_corruption(self, monkeypatch, tmp_path):
        """Two racing writers: unique tmp + os.replace must leave a parseable
        credentials.json with one complete config and no torn inode (D4)."""
        import threading
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        barrier = threading.Barrier(2)
        results = []

        def _mint(*_args, **_kwargs):
            # NB: urlopen(req, timeout=15) passes a kwarg — the side_effect
            # must accept it (plan-verbatim `_self/_args` signature would
            # TypeError; intent unchanged).
            barrier.wait()  # both writers hit the write block concurrently
            return _ok_mint()

        def _run():
            with mock.patch("urllib.request.urlopen", side_effect=_mint):
                results.append(main._cmd_signup(mock.Mock()))

        t1 = threading.Thread(target=_run)
        t2 = threading.Thread(target=_run)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["api_key"].startswith("tt_mint_")
        assert len(list((tmp_path / ".tortoise").glob("credentials.json.tmp-*"))) <= 1

    def test_reuse_validation_network_fail_closed(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=URLError("connection refused")) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err  # escape hint
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO mint on unreachable

    def test_reuse_validation_429_fail_closed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(429, json.dumps({"detail": "limited"}))) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_validation_5xx_fail_closed(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(500, json.dumps({"detail": "boom"}))) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_validation_200_garbage_fail_closed(self, monkeypatch, tmp_path, capsys):
        """Captive-portal/proxy 200-with-HTML hits the JSONDecodeError leg."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        resp = mock.MagicMock()
        resp.read.return_value = b"<html>Sign in</html>"
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "--force" in capsys.readouterr().err
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)

    def test_reuse_suspended_403_no_remint(self, monkeypatch, tmp_path, capsys):
        """#308: SUSPENDED 403 must fail closed — never mint over a suspension."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_susp")
        body = json.dumps({"detail": {"code": "SUSPENDED",
                                       "message": "Team suspended",
                                       "appeal_url": "https://support"}})
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(403, body)) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO mint on suspension
        cap = capsys.readouterr()  # readouterr DRAINS the buffer — capture ONCE
        assert "suspended" in (cap.out + cap.err).lower()

    def test_reuse_remints_against_stored_api_url(self, monkeypatch, tmp_path):
        """Re-mint must target the validated config's base URL, not env/default."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        self._global_cfg(tmp_path, api_url="https://staging.example.com", api_key="tt_stage")
        side_effects = [HTTPError("https://staging.example.com/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.startswith("https://staging.example.com/v1/agent/signup")
                   for call in urlopen.call_args_list)

    def test_reuse_invalid_then_rate_limited(self, monkeypatch, tmp_path, capsys):
        """Revoked key + exhausted 2/24h budget: message must mention BOTH."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path, api_key="tt_revoked")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{}')),
                        _http_error(429, json.dumps({"detail": {"error_code": "over_signup_ip_rate_limit"}}),
                                    {"Retry-After": "3600"})]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "rate limit" in err.lower()
        assert "invalid" in err.lower()  # the stored key is ALSO dead — say both

    def test_force_mints_despite_existing(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        self._global_cfg(tmp_path)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)  # mint ran (no validation call)

    def test_force_warns_env_shadow(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_bad_env")
        monkeypatch.setenv("HOME", str(tmp_path))
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        err = capsys.readouterr().err
        assert "TORTOISE_API_KEY" in err and "shadow" in err.lower()
