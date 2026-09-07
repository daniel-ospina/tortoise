"""Shared config resolver — env → cwd → global (#1708 D1/D5/D6)."""
from __future__ import annotations

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


def test_non_dict_json_raises_config_error(monkeypatch, tmp_path):
    """#1708 fixer (P2): JSON that parses but isn't an object ([1,2,3]) must
    be _ConfigError, not an AttributeError from config.get(...)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_text("[1, 2, 3]")
    try:
        main._resolve_config_path()
        raise AssertionError("expected _ConfigError")
    except main._ConfigError:
        pass


def test_invalid_utf8_raises_config_error(monkeypatch, tmp_path):
    """#1708 fixer (P2): UnicodeDecodeError (invalid UTF-8) is a ValueError
    subclass — must be wrapped in _ConfigError, not a raw traceback."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".tortoise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".tortoise" / "credentials.json").write_bytes(b"\xff\xfe\x00{not json")
    try:
        main._resolve_config_path()
        raise AssertionError("expected _ConfigError")
    except main._ConfigError:
        pass


# ── #2369 trust boundary: co-source + global-only transmit identity ───────
# (file-sourced keys never take their destination from env; transmitting
# surfaces resolve the user-global config only.)


def _write_global_cfg(tmp_path, api_key="tt_global", api_url="https://api.premiselabs.co"):
    d = tmp_path / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "credentials.json").write_text(json.dumps(
        {"api_key": api_key, "api_url": api_url}))


def test_file_key_url_never_from_env_without_file_api_url(monkeypatch, tmp_path):
    """#2369 D1.1 (a): a FILE-sourced key whose file lacks api_url resolves
    to the built-in default — a poisoned TORTOISE_API_URL must NOT become
    the destination of the stored key (the pre-#2369 split-brain)."""
    monkeypatch.setenv("TORTOISE_API_URL", "http://attacker.example:9999")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    d = tmp_path / ".tortoise"
    d.mkdir(parents=True, exist_ok=True)
    (d / "credentials.json").write_text(json.dumps({"api_key": "tt_global"}))
    p, _cfg, key, url = main._resolve_config_path()
    assert key == "tt_global"
    assert url == "https://api.premiselabs.co"  # built-in default, never env
    assert p == d / "credentials.json"


def test_file_key_keeps_its_own_api_url_over_env(monkeypatch, tmp_path):
    """#2369 D1.1: a file that DOES carry api_url keeps it — the env URL
    override never applies to file-sourced keys."""
    monkeypatch.setenv("TORTOISE_API_URL", "http://attacker.example:9999")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _write_global_cfg(tmp_path, api_key="tt_global",
                  api_url="https://selfhosted.example.com")
    _, _cfg, key, url = main._resolve_config_path()
    assert key == "tt_global"
    assert url == "https://selfhosted.example.com"


def test_env_key_env_url_pair_honored_even_with_files(monkeypatch, tmp_path):
    """#2369 D1.1 (b): the allowed case — when the KEY came from env, the
    env TORTOISE_API_URL override applies (one env source chain), and it
    still wins over any stored file identity."""
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
    monkeypatch.setenv("TORTOISE_API_URL", "https://env-host.example.com")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    _write_global_cfg(tmp_path)
    p, _cfg, key, url = main._resolve_config_path()
    assert p is None
    assert key == "tt_env"
    assert url == "https://env-host.example.com"


def test_global_only_skips_cwd_tortoise(monkeypatch, tmp_path):
    """#2369 D1.2 (c): global_only=True (the transmitting surfaces) SKIPS a
    repo/cwd .tortoise — a repo-supplied identity can never authorize
    prompt transmission; the user-global config is the only file source."""
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / ".tortoise").write_text(json.dumps({
        "api_key": "tt_repo", "api_url": "http://attacker.example:9999"}))
    _write_global_cfg(tmp_path, api_key="tt_global")
    # Non-transmitting surfaces keep the #1708 D1 cwd-first precedence.
    _, _cfg, key, _ = main._resolve_config_path()
    assert key == "tt_repo"
    # Transmitting surfaces resolve the user-global config only.
    p, _cfg, key, url = main._resolve_config_path(global_only=True)
    assert key == "tt_global"
    assert p == tmp_path / ".tortoise" / "credentials.json"
    assert url == "https://api.premiselabs.co"


def test_global_only_no_global_returns_none_even_with_repo_config(monkeypatch, tmp_path):
    """#2369 D1.2: with ONLY a repo .tortoise present, the transmitting
    resolution is empty — the reflex must degrade to local/no-send, never
    transmit with the repo-supplied identity."""
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    (proj / ".tortoise").write_text(json.dumps({
        "api_key": "tt_repo", "api_url": "http://attacker.example:9999"}))
    p, _cfg, key, url = main._resolve_config_path(global_only=True)
    assert (p, key, url) == (None, None, None)
    # The env key still never leaks into the transmit path (D1b) either.
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_env")
    p, _cfg, key, url = main._resolve_config_path(include_env=False, global_only=True)
    assert (p, key, url) == (None, None, None)
