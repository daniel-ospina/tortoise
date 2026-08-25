"""Shared config resolver — env → cwd → global (#1708 D1/D5/D6)."""
import json

import pytest

import tortoise.__main__ as main

GLOBAL = json.dumps({"api_key": "tt_global", "api_url": "https://api.premiselabs.co",
                     "team_id": "team-g", "device_id": "anon-g"})


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials, and
    never resolve a stray ./.tortoise file in the pytest CWD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


def _write_global(home):  # simulate signup output
    d = home / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "credentials.json"
    f.write_text(GLOBAL)
    f.chmod(0o600)


def test_env_wins_over_files(monkeypatch, tmp_path):
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise" / "credentials.json").parent.mkdir()
    (tmp_path / ".tortoise" / "credentials.json").write_text(GLOBAL)
    (tmp_path / ".tortoise").chmod(0o700)
    p, _cfg, key, _url = main._resolve_config_path()
    assert key == "tt_env" and p is None


def test_cwd_wins_over_global(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")
    (tmp_path / "proj" / ".tortoise").write_text(
        json.dumps({"api_key": "tt_cwd", "api_url": "https://api.premiselabs.co"}))
    _write_global(tmp_path)
    _, _cfg, key, _ = main._resolve_config_path()
    assert key == "tt_cwd"


def test_global_when_no_cwd(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_global(tmp_path)
    monkeypatch.chdir(tmp_path / "..")
    p, _cfg, key, _ = main._resolve_config_path()
    assert key == "tt_global" and p == tmp_path / ".tortoise" / "credentials.json"


def test_dot_tortoise_dir_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_global(tmp_path)
    (tmp_path / "repos" / "p").mkdir(parents=True)
    (tmp_path / "repos" / "p" / ".tortoise").mkdir()  # a DIRECTORY, not a file
    monkeypatch.chdir(tmp_path / "repos" / "p")
    _, _cfg, key, _ = main._resolve_config_path()
    assert key == "tt_global"  # dir is skipped, global wins


def test_no_config_anywhere(monkeypatch, tmp_path):
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main._resolve_config_path() == (None, None, None, None)


def test_empty_env_key_treated_as_unset(monkeypatch, tmp_path):
    for bad in ("", "   ", "\t"):
        monkeypatch.setenv("TORTOISE_API_KEY", bad)
        monkeypatch.setenv("HOME", str(tmp_path))
        _write_global(tmp_path)
        _, _cfg, key, _ = main._resolve_config_path()
        assert key == "tt_global", f"{bad!r} must be skipped (strip), not win"


def test_empty_file_api_key_skipped(monkeypatch, tmp_path):
    """A file candidate whose api_key is empty/whitespace is "no config here"
    — the next candidate wins (mirrors the env empty-key branch)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj").mkdir()
    monkeypatch.chdir(tmp_path / "proj")
    (tmp_path / "proj" / ".tortoise").write_text(json.dumps(
        {"api_key": "   ", "api_url": "https://api.premiselabs.co"}))
    _write_global(tmp_path)
    _, _cfg, key, _ = main._resolve_config_path()
    assert key == "tt_global"  # empty cwd api_key skipped, global wins


def test_corrupt_global_raises_config_error(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_text("{not json")
    try:
        main._resolve_config_path()
        raise AssertionError("expected _ConfigError")
    except main._ConfigError as e:
        assert "credentials.json" in str(e)


def test_unreadable_global_raises_config_error(monkeypatch, tmp_path):
    """mode 000 passes is_file() but read_text raises PermissionError (an
    OSError) — the resolver must wrap it in _ConfigError, not traceback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    f = tmp_path / ".tortoise" / "credentials.json"
    f.write_text(GLOBAL)
    f.chmod(0o000)
    try:
        main._resolve_config_path()
        raise AssertionError("expected _ConfigError")
    except main._ConfigError:
        pass


def test_non_string_api_key_raises_config_error(monkeypatch, tmp_path):
    """{"api_key": 123} is undefined behavior — pin it as _ConfigError (never
    a request with 'Bearer 123')."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_text(json.dumps({"api_key": 123}))
    try:
        main._resolve_config_path()
        raise AssertionError("expected _ConfigError")
    except main._ConfigError:
        pass
