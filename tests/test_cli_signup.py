"""CLI `tortoise signup` tests (#1081, #1708 reuse-before-mint)."""
from __future__ import annotations

import io
import json
import os
import time
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


def _raise_http_error(code, body, headers=None):
    """A mint_handler for _recovery_flow that RAISES the HTTPError (the
    urllib error branch in _cmd_signup) instead of returning a response."""
    def _handler(req, timeout=None):
        raise _http_error(code, json.dumps(body), headers)
    return _handler


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


# ── #1709: signup-token persistence + recovery UX ─────────────────────────
# The mint response now carries an additive signup_token (the keyless-
# recovery credential); re-signup re-presents a stored token (recovery on
# the SAME team); a 422 invalid_signup_token warns + requires confirmation
# before clearing the token and minting fresh; a 403 suspended team fails
# closed; `tortoise recover` is the config-loss path.


def _ok_json(body):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
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

    def test_mint_write_failure_echoes_recovery_token(self, monkeypatch, tmp_path, capsys):
        """#1750: the orphan path (mint OK, save fails) must echo the recovery
        token like the key — previously it was silently destroyed, and the
        'fix perms and re-run' guidance minted a SECOND team, permanently
        orphaning the first (whose token was never shown)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        orphan_token = "st_" + "de" * 32
        body = {"key": "tt_orphan_000000000000000000000000000000000000000000",
                "team_id": "team-orphan", "team_name": "agent-orphan",
                "graph_name": "team_team-orphan", "identity": "anon-orphan",
                "tier": "free", "signup_token": orphan_token}
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint(body)), \
             mock.patch("os.replace", side_effect=OSError("read-only fs")):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        err = capsys.readouterr().err
        assert "could NOT be saved" in err
        assert orphan_token in err  # the recovery token is echoed, not destroyed
        assert "Recovery token" in err
        # guidance warns against a blind re-run (it would mint a NEW team and
        # the first team's key+token above are the only access to it)
        assert "do NOT re-run blindly" in err
        assert "NEW team" in err

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
        the next successful write must sweep them (only tmps older than the 1h
        age guard — a fresh tmp is a CONCURRENT writer's in-flight file)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        stale = d / "credentials.json.tmp-deadbeef"
        stale.write_text("partial key material")
        stale.chmod(0o600)
        old = time.time() - 7200  # older than the 1h sweep threshold
        os.utime(stale, (old, old))
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert not stale.exists(), "stale tmp must be swept on next successful write"

    def test_fresh_tmp_not_swept(self, monkeypatch, tmp_path):
        """#1708 fixer (P2): the sweep is age-guarded — a concurrent writer's
        FRESH tmp must survive (sweeping it deletes their in-flight file →
        FileNotFoundError on os.replace → spurious 'minted but could NOT be
        saved' orphan)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        fresh = d / "credentials.json.tmp-concurrent"
        fresh.write_text("in-flight write")
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            main._cmd_signup(mock.Mock())
        assert fresh.exists(), "fresh (concurrent) tmp must survive the sweep"
        assert (tmp_path / ".tortoise" / "credentials.json").is_file()

    def test_mint_200_missing_key_reports_orphan(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P2): mint POST returns 200 with valid JSON but NO key
        field (edge/proxy) — data['key'] must not KeyError-traceback outside the
        try; the fail-soft orphan contract applies (the server may have minted)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps({"team_id": "team-x"}).encode()
        resp.__enter__.return_value = resp
        with mock.patch("urllib.request.urlopen", return_value=resp):
            rc = main._cmd_signup(mock.Mock())
        assert rc == 1
        err = capsys.readouterr().err
        assert "may have been minted" in err.lower()

    def test_device_id_stored_non_dict_ignored(self, monkeypatch, tmp_path):
        """#1708 fixer (P2): a non-dict global store ([1,2,3]) must not
        AttributeError on .get('device_id') on the --force path (the resolver
        fail-closes non-dict configs on the reuse path; --force skips it)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text("[1, 2, 3]")
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        cfg = json.loads((d / "credentials.json").read_text())
        assert cfg["device_id"].startswith("anon-")

    def test_device_id_store_invalid_utf8_force_no_traceback(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P2): invalid UTF-8 in the global store is a ValueError
        (UnicodeDecodeError) — the --force device_id read must treat it as
        absent, not traceback."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_bytes(b"\xff\xfe\x00{not json")
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()):
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        assert "Traceback" not in capsys.readouterr().err

    def test_trailing_slash_api_url_normalized(self, monkeypatch, tmp_path):
        """#1708 fixer (P2): TORTOISE_API_URL with a trailing slash must not
        make the mint POST hit //v1/agent/signup (404). The base is normalized
        once at the top; the mint URL derives from it."""
        monkeypatch.setenv("TORTOISE_API_URL", "https://api.premiselabs.co/")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint()) as urlopen:
            rc = main._cmd_signup(mock.Mock())
        assert rc == 0
        urls = [c.args[0].full_url for c in urlopen.call_args_list]
        assert any(u.endswith("/v1/agent/signup") for u in urls)
        assert all("//v1/" not in u for u in urls)

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

    def test_env_401_valid_global_reused_no_second_mint(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P1): env key 401s but the GLOBAL store holds a valid key —
        the env shadow defeats remint idempotency (every re-run re-validates the
        dead env key and mints ANOTHER team). Must reuse the global key, warn
        about the shadow, and mint ZERO new keys."""
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_dead_env")
        monkeypatch.setenv("HOME", str(tmp_path))
        self._global_cfg(tmp_path, api_key="tt_valid_global")  # the store the mint would write
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        self._valid_team()]  # the global key validates
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO second mint
        # the global store must be untouched (still the valid key)
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["api_key"] == "tt_valid_global"
        cap = capsys.readouterr()
        assert "unset" in (cap.out + cap.err).lower()

    def test_cwd_401_valid_global_reused_no_second_mint(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P1): a legacy cwd/.tortoise key 401s but the global
        store is valid — reuse the global key instead of minting a duplicate
        team (the cwd config would shadow the global store on every re-run)."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        (tmp_path / "proj").mkdir()
        monkeypatch.chdir(tmp_path / "proj")
        (tmp_path / "proj" / ".tortoise").write_text(json.dumps(
            {"api_key": "tt_dead_cwd", "api_url": "https://api.premiselabs.co"}))
        self._global_cfg(tmp_path, api_key="tt_valid_global")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        self._valid_team()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO second mint
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["api_key"] == "tt_valid_global"
        cap = capsys.readouterr()
        assert "remove" in (cap.out + cap.err).lower()

    def test_env_401_remint_warns_shadow_without_force(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P1): env key 401s and there is NO valid global key →
        mint — but the env shadow persists at read time, so the D3 warning must
        fire EVEN WITHOUT --force (previously gated on --force only → silent
        exit 0 → every re-run re-401s and mints another team)."""
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_dead_env")
        monkeypatch.setenv("HOME", str(tmp_path))
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        assert any(call.args[0].full_url.endswith("/v1/agent/signup")
                   for call in urlopen.call_args_list)  # mint happened
        err = capsys.readouterr().err
        assert "TORTOISE_API_KEY" in err and "shadow" in err.lower()

    def test_env_401_global_unvalidatable_fail_closed(self, monkeypatch, tmp_path, capsys):
        """#1708 fixer (P1): env key 401s, the global key CANNOT be validated
        (network down) — fail closed rather than mint blind (the global key may
        be valid; minting would duplicate the team once the env shadow lifts)."""
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_dead_env")
        monkeypatch.setenv("HOME", str(tmp_path))
        self._global_cfg(tmp_path, api_key="tt_maybe_valid")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        URLError("connection refused")]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)  # NO mint on unvalidatable
        assert "--force" in capsys.readouterr().err

    def test_env_401_global_corrupt_store_fail_closed(self, monkeypatch, tmp_path, capsys):
        """#1708 second-model gate (P2): env key 401s and the global store is
        corrupt (invalid UTF-8) — _global_key_status must fail CLOSED (D6
        contract: never mint over an unreadable store) with the fix-or-delete
        hint; the corrupt store's team is never orphaned, and the 2/24h
        signup budget is never burned."""
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_dead_env")
        monkeypatch.setenv("HOME", str(tmp_path))
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_bytes(b"\xff\xfe\x00{not json")
        side_effects = [HTTPError("https://api.premiselabs.co/v1/team", 401, "u", {},
                                  io.BytesIO(b'{"detail":"unauthorized"}')),
                        _ok_mint()]
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1  # fail-closed — no mint
        assert not any(call.args[0].full_url.endswith("/v1/agent/signup")
                       for call in urlopen.call_args_list)
        assert "Traceback" not in capsys.readouterr().err
        # the corrupt store was NOT replaced — fail-closed keeps it intact
        stored = (tmp_path / ".tortoise" / "credentials.json").read_bytes()
        assert stored == b"\xff\xfe\x00{not json"

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

def _mint_body(team_id="team-cli-1709", key="tt_cli_1709_key_0000000000000000000000000000"):
    return {"key": key, "team_id": team_id, "team_name": "agent-cli-1709",
            "graph_name": f"team_{team_id}", "identity": "anon-cli-1709",
            "tier": "free", "signup_token": "st_" + "ab" * 32}


def _capture_urlopen(requests):
    def _inner(req, timeout=None):
        requests.append(req)
        return _ok_json(_mint_body())
    return _inner


def _recovery_flow(requests, mint_handler, *, reuse_code=401):
    """#1709 token-path urlopen mock: the stored-key reuse GET /v1/team
    (validated first under force=False) fails with reuse_code (401 = invalid
    → re-mint against the same host); mint POSTs go to mint_handler (which
    may raise HTTPError for the 422/403 branches). Records every request
    (GET reuse + POST mints) so tests can assert request shape."""
    def _inner(req, timeout=None):
        requests.append(req)
        if req.get_method() == "GET":
            raise _http_error(reuse_code, json.dumps({"detail": "invalid key"}))
        return mint_handler(req, timeout)
    return _inner


class TestSignupTokenPersistence:
    def test_mint_persists_signup_token(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen", _capture_urlopen([])):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["signup_token"].startswith("st_")
        assert cfg["api_key"].startswith("tt_")
        out = capsys.readouterr().out
        assert "RECOVERY TOKEN — save this" in out
        assert "st_" in out

    def test_resignup_represents_stored_token(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        # a previous mint wrote the token into the #1708 global store
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        requests = []
        with mock.patch("urllib.request.urlopen",
                        _recovery_flow(requests,
                                       lambda req, timeout=None: _ok_json(_mint_body()))):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        # [0] = reuse GET (401), [1] = the mint POST that re-presents the token
        assert requests[1].get_method() == "POST"
        body = json.loads(requests[1].data)
        assert body["signup_token"] == "st_" + "cd" * 32  # re-presented

    def test_recovery_response_keeps_stored_token(self, monkeypatch, tmp_path, capsys):
        """A token-present re-signup that RECOVERS (no signup_token in the
        response) must keep the stored token — rotation is rejected server-
        side, so without persistence recovery would be one-shot-only."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        stored = "st_" + "cd" * 32
        # recovery response: NO signup_token field (server does not re-issue)
        requests = []
        with mock.patch("urllib.request.urlopen",
                        _recovery_flow(requests,
                                       lambda req, timeout=None: _ok_json({
                                           "key": "tt_recovered_0000000000000000000000000000000000000000",
                                           "team_id": "team-1", "team_name": "agent-1",
                                           "graph_name": "team_team-1", "tier": "free"}))):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["signup_token"] == stored  # kept
        assert cfg["api_key"].startswith("tt_recovered")
        # #1751: the branch keys off whether a token was PRESENTED, not whether
        # the response carried one — this recovery still says 'Key recovered'
        cap = capsys.readouterr()
        assert "Key recovered on existing team" in cap.out
        assert "Free team created" not in cap.out

    def test_fresh_mint_missing_signup_token_warns(self, monkeypatch, tmp_path, capsys):
        """#1751: a fresh mint whose response lacks signup_token (server
        version skew / a field-stripping proxy) must NOT print the false
        'Key recovered on existing team' — say 'Free team created' and warn
        the recovery backdoor was not issued (`tortoise recover` won't work)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        body = {"key": "tt_notok_000000000000000000000000000000000000000000",
                "team_id": "team-notok", "team_name": "agent-notok",
                "graph_name": "team_team-notok", "identity": "anon-notok",
                "tier": "free"}  # NO signup_token field
        with mock.patch("urllib.request.urlopen", return_value=_ok_mint(body)):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        cap = capsys.readouterr()
        assert "Free team created" in cap.out
        assert "Key recovered" not in cap.out  # the false message must not appear
        assert "Recovery backdoor NOT created" in cap.err  # #1751 warning
        assert "signup token" in cap.err.lower()
        assert "tortoise recover" in cap.err  # user is told recovery won't work
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert "signup_token" not in cfg  # nothing silently persisted

    def test_recovery_response_injected_token_ignored(self, monkeypatch, tmp_path):
        """#1709 fixer P2.5: on the RECOVERY branch (a token was presented)
        a proxy-injected signup_token in the response must NOT overwrite the
        real stored credential — the response token is only authoritative on
        the fresh-mint branch (distinguished by request shape, not response
        content)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        stored = "st_" + "cd" * 32
        # recovery response carries a DIFFERENT signup_token (proxy-injected
        # or a server bug) — must be ignored on the recovery branch.
        requests = []
        with mock.patch("urllib.request.urlopen",
                        _recovery_flow(requests,
                                       lambda req, timeout=None: _ok_json({
                                           "key": "tt_recovered_0000000000000000000000000000000000000000",
                                           "team_id": "team-1", "team_name": "agent-1",
                                           "graph_name": "team_team-1", "tier": "free",
                                           "signup_token": "st_" + "ff" * 32}))):
            rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["signup_token"] == stored  # real credential kept

    def test_force_mints_fresh_ignoring_stored_token(self, monkeypatch, tmp_path):
        """#1709 fixer P2.4: --force is the documented escape hatch — a
        FRESH mint, never a token recovery. A stored token must not be
        re-presented under --force (a suspended team + dead token could
        never be escaped otherwise)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        requests = []
        with mock.patch("urllib.request.urlopen", _capture_urlopen(requests)):
            rc = main._cmd_signup(mock.Mock(force=True))
        assert rc == 0
        # --force skips the reuse GET entirely: requests[0] IS the mint POST
        assert requests and requests[0].get_method() == "POST"
        body = json.loads(requests[0].data)
        assert "signup_token" not in body
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        # the fresh mint's token replaced the old one
        assert cfg["signup_token"] == "st_" + "ab" * 32


class TestSignup422OrphanGuard:
    """A revoked/truncated stored token must NOT silently orphan the original
    team: warn first, require confirmation before clearing + minting fresh."""

    def test_422_non_interactive_fails_closed(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        with mock.patch("sys.stdin.isatty", return_value=False):  # noqa: SIM117
            with mock.patch("urllib.request.urlopen",
                            _recovery_flow([],
                                           _raise_http_error(422, {
                                               "detail": {"error_code": "invalid_signup_token"}}))):
                rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "recovery token is invalid" in err
        assert "Non-interactive — aborting" in err

    def test_422_confirm_yes_clears_token_and_remints(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        requests = []
        calls = [
            _http_error(422, json.dumps({"detail": {"error_code": "invalid_signup_token"}})),
            _ok_json(_mint_body(team_id="team-fresh-1709")),
        ]
        with mock.patch("sys.stdin.isatty", return_value=True):  # noqa: SIM117
            with mock.patch("builtins.input", return_value="YES"):
                def _side_effect(req, timeout=None):
                    requests.append(req)
                    if req.get_method() == "GET":
                        # reuse validation of the STORED key fails → re-mint
                        raise _http_error(401, json.dumps({"detail": "invalid key"}))
                    c = calls.pop(0)
                    if isinstance(c, Exception):
                        raise c
                    return c
                with mock.patch("urllib.request.urlopen", side_effect=_side_effect):
                    rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 0
        # [0] reuse GET (401), [1] mint-with-token (422), [2] mint-without
        # token — the retry minted WITHOUT the token (cleared)
        assert len(requests) == 3
        assert requests[1].get_method() == "POST"
        body = json.loads(requests[2].data)
        assert "signup_token" not in body
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["team_id"] == "team-fresh-1709"

    def test_422_confirm_no_aborts(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        with mock.patch("sys.stdin.isatty", return_value=True):  # noqa: SIM117
            with mock.patch("builtins.input", return_value="no"):
                with mock.patch("urllib.request.urlopen",
                                _recovery_flow([],
                                               _raise_http_error(422, {
                                                   "detail": {"error_code": "invalid_signup_token"}}))):
                    rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        assert "Aborted" in capsys.readouterr().err


class TestSignup403Suspended:
    def test_suspended_team_fails_closed(self, monkeypatch, tmp_path, capsys):
        """A suspended team must NOT be pushed toward orphaning — no fresh
        mint, no orphan prompt; fail closed with the suspension message."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_old", "api_url": "https://api.premiselabs.co",
            "team_id": "team-1", "team_name": "agent-1",
            "signup_token": "st_" + "cd" * 32}))
        with mock.patch("sys.stdin.isatty", return_value=True):  # noqa: SIM117
            with mock.patch("urllib.request.urlopen",
                            _recovery_flow([],
                                           _raise_http_error(403, {
                                               "detail": {"code": "SUSPENDED",
                                                           "message": "suspended due to unusual activity",
                                                           "appeal_url": "https://x/appeal"}}))):
                rc = main._cmd_signup(mock.Mock(force=False))
        assert rc == 1
        err = capsys.readouterr().err
        assert "suspended" in err
        assert "appeal" in err


class TestSignupRecoverHint:
    """#1756: a config that parses with a signup_token but NO api_key is the
    exact state `tortoise recover` exists for — signup must point there, not
    brand the config 'corrupt — delete it' (destructive: deleting destroys
    the recovery token) or suggest --force (mints a NEW team, orphaning the
    old one). No mint may happen on this shape: exit 1 without network."""

    def test_token_only_global_config_prints_recover_hint_no_mint(
            self, monkeypatch, tmp_path, capsys):
        """Global store with a signup_token but no api_key: the resolver's
        api_key invariant trips _ConfigError — signup must print the recover
        hint naming the file and exit 1 WITHOUT minting (urlopen never
        called)."""
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "signup_token": "st_" + "ef" * 32}))  # no api_key
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        err = capsys.readouterr().err
        assert rc == 1
        assert "tortoise recover" in err
        assert str(gdir / "credentials.json") in err
        assert "Do NOT delete this file" in err
        assert "do NOT use --force" in err
        assert "corrupt or unreadable" not in err
        assert "fix or delete" not in err
        urlopen.assert_not_called()  # no mint, no reuse GET — exit before network

    def test_token_only_cwd_config_prints_recover_hint(self, monkeypatch, tmp_path, capsys):
        """Legacy cwd/.tortoise token-only shape — the candidate scan (cwd
        first, then global) must find it and point at recover."""
        (tmp_path / ".tortoise").write_text(json.dumps({
            "api_url": "http://localhost:8010",
            "signup_token": "st_" + "ef" * 32}))  # no api_key
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        err = capsys.readouterr().err
        assert rc == 1
        assert "tortoise recover" in err
        assert str(tmp_path / ".tortoise") in err
        urlopen.assert_not_called()

    def test_corrupt_config_keeps_corrupt_message(self, monkeypatch, tmp_path, capsys):
        """Genuinely corrupt config (invalid JSON / invalid UTF-8) must keep
        the existing corrupt-config guidance — the recover hint is only for
        the token-only shape, not for unparseable files."""
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_bytes(b"\xff\xfe\x00{not json")
        with mock.patch("urllib.request.urlopen") as urlopen:
            rc = main._cmd_signup(mock.Mock(force=False))
        err = capsys.readouterr().err
        assert rc == 1
        assert "corrupt or unreadable" in err
        assert "tortoise recover" not in err
        urlopen.assert_not_called()


class TestCmdRecover:
    """tortoise recover --token st_... → POST /v1/agent/recover → config
    rewritten; the token is PERSISTED back (recovery must not be one-shot)."""

    def test_recover_happy_path_writes_config(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        token = "st_" + "ef" * 32
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: _ok_json({
                            "key": "tt_rec_000000000000000000000000000000000000000000",
                            "team_id": "team-9", "team_name": "agent-9",
                            "graph_name": "team_team-9", "tier": "free"})):
            rc = main._cmd_recover(mock.Mock(token=token))
        assert rc == 0
        cfg = json.loads((tmp_path / ".tortoise" / "credentials.json").read_text())
        assert cfg["api_key"].startswith("tt_rec")
        assert cfg["team_id"] == "team-9"
        assert cfg["signup_token"] == token  # ⛔ persisted
        out = capsys.readouterr().out
        assert "Key recovered on team agent-9" in out
        assert "data intact" in out

    def test_recover_no_token_fails(self, capsys):
        rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 1
        assert "No recovery token" in capsys.readouterr().err

    def test_recover_422_invalid_token(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(422, json.dumps({
                            "detail": {"error_code": "invalid_signup_token"}}))):
            rc = main._cmd_recover(mock.Mock(token="st_" + "aa" * 32))
        assert rc == 1
        assert "invalid signup token" in capsys.readouterr().err

    def test_recover_malformed_200_no_traceback(self, monkeypatch, tmp_path, capsys):
        """#1709 fixer P2.2: a 200 with valid JSON but no key/team_id
        (proxy garbage) must warn + exit 1 — never a KeyError traceback on
        the unguarded derefs."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
        with mock.patch("urllib.request.urlopen",
                        lambda req, timeout=None: _ok_json({"status": "ok"})):
            rc = main._cmd_recover(mock.Mock(token="st_" + "aa" * 32))
        assert rc == 1
        err = capsys.readouterr().err
        assert "malformed" in err

    # ── #1752: recover token source must MATCH the auth key source ────────
    def test_recover_stored_token_same_config_no_warning(
            self, monkeypatch, tmp_path, capsys):
        """#1752: recover with NO --token reads the stored token from the
        SAME config that holds the key — used silently, no divergence
        warning (mirrors revoke's (b) contract)."""
        token = "st_" + "dd" * 32
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text(json.dumps({
            "api_key": "tt_team_d", "api_url": "https://api.premiselabs.co",
            "team_id": "team-d", "signup_token": token}))
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_mint({
                            "key": "tt_rec_dd0000000000000000000000000000000000000000",
                            "team_id": "team-d", "team_name": "agent-d",
                            "graph_name": "team_team-d", "tier": "free"})) as urlopen:
            rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert json.loads(req.data) == {"signup_token": token}  # stored token
        err = capsys.readouterr().err
        assert "recovery token comes from" not in err
        assert "note:" not in err  # no divergence warning at all

    def test_recover_env_key_divergent_token_warns(self, monkeypatch,
                                                   tmp_path, capsys):
        """#1752 mirror of revoke (a): env key (team A, no token) + stored
        token for a DIFFERENT team (team B) — the recover attempt uses the
        stored token and prints the divergence warning naming the shadow
        source instead of silently mixing teams."""
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_env_team_a")
        token_b = "st_" + "bb" * 32
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text(json.dumps({
            "api_key": "tt_team_b", "api_url": "https://api.premiselabs.co",
            "team_id": "team-b", "signup_token": token_b}))
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_mint({
                            "key": "tt_rec_bb0000000000000000000000000000000000000000",
                            "team_id": "team-b", "team_name": "agent-b",
                            "graph_name": "team_team-b", "tier": "free"})) as urlopen:
            rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert json.loads(req.data) == {"signup_token": token_b}
        err = capsys.readouterr().err
        assert "recovery token comes from" in err
        assert "TORTOISE_API_KEY (env)" in err  # the KEY source named
        assert str(d / "credentials.json") in err  # the TOKEN source named

    def test_recover_cwd_global_divergence_warns(self, monkeypatch,
                                                 tmp_path, capsys):
        """#1752 mirror of revoke (c): key in a cwd/.tortoise config WITHOUT
        a token + the token in the global store — the fallback warns naming
        BOTH file sources."""
        proj = tmp_path / "proj"
        proj.mkdir()
        monkeypatch.chdir(proj)
        (proj / ".tortoise").write_text(json.dumps({  # legacy cwd FILE shape
            "api_key": "tt_cwd_team_a",
            "api_url": "https://api.premiselabs.co"}))  # no signup_token
        token_b = "st_" + "ee" * 32
        d = tmp_path / ".tortoise"
        d.mkdir(parents=True, exist_ok=True)
        (d / "credentials.json").write_text(json.dumps({
            "api_key": "tt_global_team_b",
            "api_url": "https://api.premiselabs.co",
            "signup_token": token_b}))
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_mint({
                            "key": "tt_rec_ee0000000000000000000000000000000000000000",
                            "team_id": "team-b", "team_name": "agent-b",
                            "graph_name": "team_team-b", "tier": "free"})) as urlopen:
            rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert json.loads(req.data) == {"signup_token": token_b}
        err = capsys.readouterr().err
        assert "recovery token comes from" in err
        assert str(proj / ".tortoise") in err
        assert str(d / "credentials.json") in err
        assert "Traceback" not in err

    def test_recover_uses_config_api_url(self, monkeypatch, tmp_path):
        """#1749: the recover POST URL must come from the stored config's
        api_url (resolver chain), NOT the env-only default host — with a
        config api_url on a non-default host, recovery used to POST to prod
        and 422'd "invalid signup token" with misleading guidance."""
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_key": "tt_key", "api_url": "http://localhost:8010",
            "team_id": "team-9", "signup_token": "st_" + "ef" * 32}))
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = lambda req, timeout=None: _ok_json({
                "key": "tt_rec_000000000000000000000000000000000000000000",
                "team_id": "team-9", "team_name": "agent-9"})
            rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url.startswith("http://localhost:8010/v1/agent/recover")

    def test_recover_token_only_config_uses_api_url(self, monkeypatch, tmp_path):
        """#1749 nuance: a config that parses but has NO api_key trips the
        resolver's _ConfigError invariant — recover authenticates with the
        signup token (no key needed), so it must still honor the stored
        api_url instead of failing on the recover surface."""
        monkeypatch.delenv("TORTOISE_API_URL", raising=False)
        gdir = tmp_path / ".tortoise"
        gdir.mkdir(parents=True, exist_ok=True)
        (gdir / "credentials.json").write_text(json.dumps({
            "api_url": "http://localhost:8010",
            "signup_token": "st_" + "ef" * 32}))  # no api_key
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = lambda req, timeout=None: _ok_json({
                "key": "tt_rec_000000000000000000000000000000000000000000",
                "team_id": "team-9", "team_name": "agent-9"})
            rc = main._cmd_recover(mock.Mock(token=None))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url.startswith("http://localhost:8010/v1/agent/recover")

    def test_recover_env_fallback_when_no_config(self, monkeypatch, tmp_path):
        """#1749: with no config anywhere the resolver returns None —
        TORTOISE_API_URL must still be honored (env fallback, pre-fix
        behavior preserved)."""
        monkeypatch.setenv("TORTOISE_API_URL", "http://env-host:9999")
        with mock.patch("urllib.request.urlopen") as urlopen:
            urlopen.side_effect = lambda req, timeout=None: _ok_json({
                "key": "tt_rec_000000000000000000000000000000000000000000",
                "team_id": "team-9", "team_name": "agent-9"})
            rc = main._cmd_recover(mock.Mock(token="st_" + "ef" * 32))
        assert rc == 0
        req = urlopen.call_args.args[0]
        assert req.full_url.startswith("http://env-host:9999/v1/agent/recover")
