"""Tests for tortoise/backup_config.py — the fail-closed env contract."""

from __future__ import annotations

import base64
import os

import pytest

from tortoise.backup_config import ConfigError, load_config


def _good_env() -> dict[str, str]:
    key = base64.b64encode(b"k" * 32).decode()
    return {
        "BACKUP_SWEEP_ENABLED": "true",
        "TORTOISE_BACKUP_KEY": key,
        "R2_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "ak",
        "R2_SECRET_ACCESS_KEY": "sk",
        "R2_BUCKET": "tortoise-backups",
        "TELEGRAM_BOT_TOKEN": "123:token",
        "TELEGRAM_CHAT_ID": "551595722",
        "DR_ISSUES_PAT": "ghp_fake",
        "BACKUP_ALERT_ASSIGNEE": "daniel-ospina",
    }


def test_fail_closed_default_disabled(monkeypatch):
    """BACKUP_SWEEP_ENABLED unset ⇒ disabled config, no secret validation."""
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.backup_key == b""
    assert cfg.gh_repo == "daniel-ospina/tortoise"


def test_fail_closed_unrecognized_value(monkeypatch):
    """An unrecognized (non-true) value must NOT enable the sweep."""
    monkeypatch.setenv("BACKUP_SWEEP_ENABLED", "banana")
    cfg = load_config()
    assert cfg.enabled is False


def test_enabled_requires_syncable_secrets(monkeypatch):
    """Enabled + missing R2 secret ⇒ ConfigError (fail fast at boot)."""
    env = _good_env()
    del env["R2_BUCKET"]
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="R2_BUCKET"):
        load_config()


def test_enabled_requires_valid_backup_key(monkeypatch):
    env = _good_env()
    env["TORTOISE_BACKUP_KEY"] = "not-base64!!"
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="base64"):
        load_config()


def test_enabled_requires_32_byte_key(monkeypatch):
    env = _good_env()
    env["TORTOISE_BACKUP_KEY"] = base64.b64encode(b"short").decode()
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="32 bytes"):
        load_config()


def test_enabled_requires_telegram(monkeypatch):
    """Missing Telegram secrets ⇒ ConfigError — a dead human channel is #101."""
    env = _good_env()
    del env["TELEGRAM_CHAT_ID"]
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="TELEGRAM"):
        load_config()


def test_enabled_requires_assignee(monkeypatch):
    env = _good_env()
    del env["BACKUP_ALERT_ASSIGNEE"]
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="BACKUP_ALERT_ASSIGNEE"):
        load_config()


def test_gh_repo_default_and_override(monkeypatch):
    env = _good_env()
    monkeypatch.setattr(os, "environ", env)
    assert load_config().gh_repo == "daniel-ospina/tortoise"
    env["GH_REPO"] = "org/custom"
    monkeypatch.setattr(os, "environ", env)
    assert load_config().gh_repo == "org/custom"


def test_enabled_loads_full_config(monkeypatch):
    env = _good_env()
    env.update(
        {
            "BACKUP_STALE_THRESHOLD_MIN": "45",
            "BACKUP_WATCHER_POLL_SECONDS": "300",
            "BACKUP_SIMULATE_ENABLED": "true",
            "GH_REPO": "daniel-ospina/tortoise",
        }
    )
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.enabled is True
    assert cfg.stale_threshold_min == 45
    assert cfg.watcher_poll_seconds == 300
    assert cfg.simulate_enabled is True
    assert cfg.retention_hourly == 24
    assert cfg.size_guard_max_nodes == 100_000


def test_team_sweep_enabled_flag_default_false(monkeypatch):
    """BACKUP_TEAM_SWEEP_ENABLED defaults to false."""
    env = _good_env()
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.team_sweep_enabled is False


def test_team_sweep_enabled_flag_true(monkeypatch):
    """BACKUP_TEAM_SWEEP_ENABLED=true is parsed correctly."""
    env = _good_env()
    env["BACKUP_TEAM_SWEEP_ENABLED"] = "true"
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.team_sweep_enabled is True


def test_team_sweep_enabled_even_when_sweep_disabled(monkeypatch):
    """BACKUP_TEAM_SWEEP_ENABLED is parsed independently of BACKUP_SWEEP_ENABLED."""
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.setenv("BACKUP_TEAM_SWEEP_ENABLED", "true")
    cfg = load_config()
    assert cfg.enabled is False  # main sweep disabled
    assert cfg.team_sweep_enabled is True  # team-sweep flag still read


def test_env_dict_injection_does_not_leak(monkeypatch):
    """load_config(env=...) must not mutate the real process environment."""
    key = base64.b64encode(b"k" * 32).decode()
    before = dict(os.environ)
    cfg = load_config({**_good_env(), "TORTOISE_BACKUP_KEY": key})
    assert cfg.enabled is True
    assert dict(os.environ) == before
    assert "BACKUP_SWEEP_ENABLED" not in os.environ
