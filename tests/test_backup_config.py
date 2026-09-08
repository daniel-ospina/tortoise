"""Tests for tortoise/backup_config.py — the fail-closed env contract."""

from __future__ import annotations

import base64
import os

import pytest

from tortoise.backup_config import ConfigError, load_config


def _good_env() -> dict[str, str]:
    key = base64.b64encode(b"k" * 32).decode()
    stream_key = base64.b64encode(b"s" * 32).decode()
    return {
        "BACKUP_SWEEP_ENABLED": "true",
        "TORTOISE_BACKUP_KEY": key,
        "REGISTRY_STREAM_KEY": stream_key,
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


def test_dead_skip_fresh_knob_removed(monkeypatch):
    """#2317: BACKUP_SKIP_FRESH_MIN (parsed but never consumed) is REMOVED —
    the env var must not silently re-enter the config contract (the original
    registry-era skip window is superseded by the in-flight 202 guard +
    per-team locks + retention prune)."""
    env = _good_env()
    env["BACKUP_SKIP_FRESH_MIN"] = "1"
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.enabled is True
    assert not hasattr(cfg, "skip_fresh_min"), \
        "skip_fresh_min dead knob must stay removed from BackupConfig"


def test_env_dict_injection_does_not_leak(monkeypatch):
    """load_config(env=...) must not mutate the real process environment."""
    before = dict(os.environ)
    cfg = load_config(_good_env())
    assert cfg.enabled is True
    assert dict(os.environ) == before
    assert "BACKUP_SWEEP_ENABLED" not in os.environ


# ── #2319 bucket-lock + geo-mirror config contract ──────────────────────────


def test_lock_defaults_off_three_days(monkeypatch):
    """Lock knobs default to disabled with the documented 3-day window."""
    monkeypatch.setattr(os, "environ", _good_env())
    cfg = load_config()
    assert cfg.lock_enabled is False
    assert cfg.lock_days == 3
    assert cfg.cf_api_token == ""
    assert cfg.mirror_enabled is False
    assert cfg.mirror_endpoint == ""


def test_lock_enabled_parses_days_and_token(monkeypatch):
    env = _good_env()
    env.update({"BACKUP_LOCK_ENABLED": "true", "BACKUP_LOCK_DAYS": "5",
                "CF_API_TOKEN": "cf-token"})
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.lock_enabled is True
    assert cfg.lock_days == 5
    assert cfg.cf_api_token == "cf-token"


def test_lock_days_rejects_out_of_bounds(monkeypatch):
    """#2319: the lock window must stay below the #2304 trash grace (7d) so
    purge erasure of a deleted graph's artifacts is never blocked — 0, 7 and
    negative windows are ConfigErrors."""
    for bad in ("0", "7", "-1", "31"):
        env = _good_env()
        env.update({"BACKUP_LOCK_ENABLED": "true", "BACKUP_LOCK_DAYS": bad})
        monkeypatch.setattr(os, "environ", env)
        with pytest.raises(ConfigError, match="BACKUP_LOCK_DAYS"):
            load_config()


def test_lock_days_ignored_when_lock_disabled(monkeypatch):
    """An out-of-range BACKUP_LOCK_DAYS with the lock DISABLED is inert (the
    value only binds once the operator flips BACKUP_LOCK_ENABLED on)."""
    env = _good_env()
    env["BACKUP_LOCK_DAYS"] = "99"
    monkeypatch.setattr(os, "environ", env)
    assert load_config().lock_days == 99  # parsed, but not enforced


def test_mirror_enabled_requires_all_creds(monkeypatch):
    """BACKUP_MIRROR_ENABLED=true without the four R2_MIRROR_* creds is a
    ConfigError (fail-closed — a silently-dead mirror is a false durability
    promise, the #101 class)."""
    env = _good_env()
    env["BACKUP_MIRROR_ENABLED"] = "true"
    env["R2_MIRROR_ACCOUNT_ID"] = "mirror-acct"
    env["R2_MIRROR_ACCESS_KEY_ID"] = "mak"
    env["R2_MIRROR_SECRET_ACCESS_KEY"] = "msk"
    monkeypatch.setattr(os, "environ", env)  # R2_MIRROR_BUCKET missing
    with pytest.raises(ConfigError, match="R2_MIRROR_BUCKET"):
        load_config()


def test_mirror_parses_creds_and_derives_endpoint(monkeypatch):
    env = _good_env()
    env.update({"BACKUP_MIRROR_ENABLED": "true",
                "R2_MIRROR_ACCOUNT_ID": "mirror-acct",
                "R2_MIRROR_ACCESS_KEY_ID": "mak",
                "R2_MIRROR_SECRET_ACCESS_KEY": "msk",
                "R2_MIRROR_BUCKET": "tortoise-backups-mirror"})
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.mirror_enabled is True
    assert cfg.mirror_endpoint == "https://mirror-acct.r2.cloudflarestorage.com"
    assert cfg.mirror_bucket == "tortoise-backups-mirror"


def test_mirror_endpoint_override_wins(monkeypatch):
    env = _good_env()
    env.update({"BACKUP_MIRROR_ENABLED": "true",
                "R2_MIRROR_ACCOUNT_ID": "mirror-acct",
                "R2_MIRROR_ACCESS_KEY_ID": "mak",
                "R2_MIRROR_SECRET_ACCESS_KEY": "msk",
                "R2_MIRROR_BUCKET": "mirror",
                "R2_MIRROR_ENDPOINT": "https://s3.example-region.example"})
    monkeypatch.setattr(os, "environ", env)
    assert load_config().mirror_endpoint == "https://s3.example-region.example"


def test_lock_and_mirror_ignored_when_sweep_disabled(monkeypatch):
    """The disabled branch keeps lock/mirror at inert defaults — a deploy
    without the sweep never validates or reads the #2319 knobs."""
    env = dict(os.environ)
    env.pop("BACKUP_SWEEP_ENABLED", None)
    env.update({"BACKUP_LOCK_ENABLED": "true", "BACKUP_MIRROR_ENABLED": "true"})
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.lock_enabled is False
    assert cfg.mirror_enabled is False
