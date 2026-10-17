"""#5109 — a credential store that drifted open must say so, on the read side.

THE GAP THIS PINS
=================
`tortoise` pins its own credential store CLOSED on every write: the tmp file is
`os.open(..., 0o600)`, the rename is atomic, and the directory is
`os.chmod(home_dir, 0o700)` (#1708, `__main__.py::_cmd_signup` /
`_cmd_recover`). **Nothing verified that on READ.** So a store whose mode had
drifted — restored from a backup, re-created by hand, `cp`'d without `-p` — was
opened and its value used at whatever mode it carried, silently.

That is the half of this issue's class the repo can still answer for itself.
#5109's exposure lives in artifacts this repo does not write (pi session and
task-session transcripts, `agent-infra#1572` / `#1651`), and the copies that
already exist are only made moot by rotating the key (an owner action, ordered
per #4356). What is left here is the property this repo *asserts in prose and
never checked*: the store it reads is the store it wrote.

WHAT IS ASSERTED, AND WHAT IS DELIBERATELY NOT
----------------------------------------------
* WARN, never refuse. `0644` behind a `0700` parent is not reachable by another
  user — #5109's own 2026-10-09 correction, where "world-readable" was
  withdrawn after the parent chain was checked — so a hazard hint is the right
  shape and a hard refusal would over-block a setup that is not exposed.
* The warning must NOT quote the credential. A warning about a leak that prints
  the leaked value writes it into the transcript the issue is about.
* stderr only. `--json` callers own stdout; a warning there corrupts the
  payload (the same rule `_write_mcp_config_file(status_to_stderr=...)` exists
  for).
* Once per path per process, so a long-lived resolver loop cannot bury it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tortoise.__main__ import (
    _CREDENTIAL_MODE_WARNED,
    _read_stored_signup_token_with_source,
    _resolve_config_path,
    _warn_if_credential_store_is_readable,
)

# `uuid4().hex` is 32 lowercase hex, so every minted key is `tt_[0-9a-f]{32}`.
# A SENTINEL, so this test can never itself leak a real credential.
KEY = "tt_" + "0123456789abcdef" * 2
TOKEN = "st_" + "fedcba9876543210" * 4
URL = "https://api.premiselabs.co"


@pytest.fixture(autouse=True)
def _isolated_home_and_env(tmp_path, monkeypatch):
    """No ambient credential may reach these tests (#3721's class, one level up).

    The real `~/.tortoise/credentials.json` on a developer machine — and a real
    `TORTOISE_API_KEY` in its shell — would otherwise decide the outcome, so the
    suite would assert the MACHINE rather than the resolver, and a warn-once
    cache poisoned by another test would silence the line under test.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    _CREDENTIAL_MODE_WARNED.clear()
    yield
    _CREDENTIAL_MODE_WARNED.clear()


def _store(path: Path, payload: dict, mode: int) -> Path:
    """Write a credential store at an EXPLICIT mode (umask must not decide)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    os.chmod(path, mode)
    return path


def _warnings(capsys) -> str:
    return capsys.readouterr().err


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o606])
def test_a_store_readable_by_group_or_other_warns(mode, tmp_path, capsys) -> None:
    """ANY group/other bit is the finding — not just the 0644 the issue named."""
    path = _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, mode)

    _resolve_config_path()

    err = _warnings(capsys)
    assert err.strip(), f"mode {mode:04o} produced no warning"
    assert str(path) in err
    assert f"{mode:04o}" in err
    assert "chmod 600" in err


@pytest.mark.parametrize("mode", [0o600, 0o400])
def test_a_correctly_closed_store_is_silent(mode, tmp_path, capsys) -> None:
    """The shape this repo writes (0600) must not produce noise."""
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, mode)

    _path, _config, api_key, api_url = _resolve_config_path()

    assert (api_key, api_url) == (KEY, URL)
    assert _warnings(capsys) == ""


def test_the_warning_never_quotes_the_credential(tmp_path, capsys) -> None:
    """A leak warning that prints the value IS the leak (#5109's whole point)."""
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)

    _resolve_config_path()

    err = _warnings(capsys)
    assert "chmod 600" in err  # the warning really fired
    assert KEY not in err
    assert KEY[3:] not in err  # neither the body alone


def test_warning_does_not_refuse_the_key(tmp_path, capsys) -> None:
    """WARN, never refuse: `0644` behind a `0700` parent is not exposed.

    #5109's 2026-10-09 correction withdrew the "world-readable" claim after the
    parent chain was checked. A store that still resolves must still resolve —
    a hint that breaks authentication is a worse failure than the hazard.
    """
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)

    path, config, api_key, api_url = _resolve_config_path()

    assert _warnings(capsys).strip()
    assert path == tmp_path / ".tortoise"
    assert config["api_key"] == KEY
    assert api_key == KEY
    assert api_url == URL


def test_the_warning_is_stderr_only_so_stdout_stays_a_pure_payload(tmp_path, capsys) -> None:
    """`--json` callers own stdout — a warning there corrupts the payload."""
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)

    _resolve_config_path()

    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip()


def test_the_warning_fires_once_per_path_per_process(tmp_path, capsys) -> None:
    """A resolver loop must not bury the line it is trying to show."""
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)

    for _ in range(5):
        assert _resolve_config_path()[2] == KEY

    err = _warnings(capsys)
    assert err.count("chmod 600") == 1, err

    # …and the cache is what silenced it, not the check having gone quiet.
    _CREDENTIAL_MODE_WARNED.clear()
    assert _resolve_config_path()[2] == KEY
    assert _warnings(capsys).count("chmod 600") == 1


def test_two_store_paths_warn_independently(tmp_path, capsys, monkeypatch) -> None:
    """Keying the cache by path (not by "have we warned at all") matters here.

    The cwd legacy candidate and the global store are DIFFERENT files; a cache
    that remembered "we already warned" would hide the second one.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    legacy = _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)
    global_store = _store(
        home / ".tortoise" / "credentials.json",
        {"api_key": KEY, "api_url": URL},
        0o644,
    )

    _resolve_config_path()  # cwd candidate wins and warns
    legacy.unlink()
    _resolve_config_path()  # now the global candidate warns

    err = _warnings(capsys)
    assert str(legacy) in err
    assert str(global_store) in err


def test_an_env_sourced_key_is_not_a_store_and_never_warns(tmp_path, capsys, monkeypatch) -> None:
    """There is no file to be permissive when the key comes from the shell."""
    _store(tmp_path / ".tortoise", {"api_key": KEY, "api_url": URL}, 0o644)
    monkeypatch.setenv("TORTOISE_API_KEY", KEY)

    path, _config, api_key, _api_url = _resolve_config_path()

    assert path is None  # env candidate
    assert api_key == KEY
    assert _warnings(capsys) == ""


def test_the_recovery_token_reader_warns_too(tmp_path, capsys) -> None:
    """`st_` is a full credential (#1709) — same store, same finding.

    A reader that validates its neighbour and not itself is how a store
    `chmod`ed open stays unnoticed on the recovery path.
    """
    path = _store(
        tmp_path / ".tortoise",
        {"api_key": KEY, "api_url": URL, "signup_token": TOKEN},
        0o644,
    )

    token, source = _read_stored_signup_token_with_source()

    assert (token, source) == (TOKEN, path)
    err = _warnings(capsys)
    assert str(path) in err and "chmod 600" in err
    assert TOKEN not in err


def test_the_check_itself_never_raises_on_an_unstattable_path(capsys) -> None:
    """Credential resolution already succeeded — this check cannot fail it."""
    _warn_if_credential_store_is_readable(Path("/nonexistent/tortoise/credentials.json"))
    assert capsys.readouterr().err == ""


def test_a_symlink_to_a_closed_store_reports_the_mode_the_read_uses(tmp_path, capsys) -> None:
    """`stat()` (not `lstat()`) is deliberate: the resolved target is what is read."""
    real = _store(tmp_path / "real-credentials.json", {"api_key": KEY, "api_url": URL}, 0o600)
    link = tmp_path / ".tortoise"
    link.symlink_to(real)

    assert _resolve_config_path()[2] == KEY
    assert _warnings(capsys) == ""
