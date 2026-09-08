"""Backup sweep configuration — single validated source for the env contract.

Fail-closed by default: ``BACKUP_SWEEP_ENABLED`` defaults to ``false``, so any
deploy that lacks the required secrets boots and serves normally with backups
disabled (preserving the #643 warn-only degrade contract — a boot crash on
missing backup secrets is the #545 blast radius). When enabled, missing
required syncable secrets fail fast at boot with a clear error.

Telegram secrets are required-when-enabled: a silently-dead human alert
channel is exactly the #101 silent-no-op class this feature exists to close.
"""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

_AES_KEY_SIZE = 32

# Secrets that the deploy workflow can sync from GH → Fly (the "syncable" set).
# REGISTRY_STREAM_KEY is deliberately EXCLUDED — it must be set out-of-band
# by the operator on Fly (fly secrets set) and never present in GitHub.
_SYNCABLE_REQUIRED = (
    "TORTOISE_BACKUP_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)

_DEFAULT_GH_REPO = "daniel-ospina/tortoise"


class ConfigError(RuntimeError):
    """Configuration is invalid — the app must not start backups with it."""


@dataclass(frozen=True)
class BackupConfig:
    """Parsed + validated backup sweep configuration."""

    enabled: bool
    backup_key: bytes
    registry_stream_key: bytes  # Fly-only, never in GH — #661
    r2_account_id: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_bucket: str
    telegram_bot_token: str
    telegram_chat_id: str
    github_issues_pat: str
    alert_assignee: str
    gh_repo: str
    # #2318: decrypt key chains — active-first candidates per role (the
    # active key plus retained previous keys during a rotation overlap
    # window). Encrypt callers use the single active key fields above
    # (unchanged); restore/decrypt callers iterate a chain so archives
    # encrypted under a rotated-out key stay decryptable in-app.
    backup_key_chain: tuple[bytes, ...] = ()
    registry_stream_key_chain: tuple[bytes, ...] = ()
    # Tuning (defaults are the reviewed production values).
    stale_threshold_min: int = 90
    watcher_poll_seconds: int = 600
    driver_down_threshold_min: int = 240
    watcher_grace_min: int = 120
    size_guard_max_nodes: int = 100_000
    skip_fresh_min: int = 45
    retention_hourly: int = 24
    retention_daily: int = 7
    retention_weekly: int = 4
    simulate_enabled: bool = False
    team_sweep_enabled: bool = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer (got {raw!r})") from e


def _require(name: str) -> str:
    """Fetch a required env var, raising a ConfigError with guidance if absent."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{name} not set — required when backup sweep is enabled")
    return value


def _parse_backup_key(raw: str, name: str = "TORTOISE_BACKUP_KEY") -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception as e:
        raise ConfigError(f"{name} must be base64 (got {raw[:8]!r}...): {e}") from e
    if len(key) != _AES_KEY_SIZE:
        raise ConfigError(f"{name} must decode to {_AES_KEY_SIZE} bytes (got {len(key)})")
    return key


def _optional_key_env(name: str) -> bytes | None:
    """Parse an OPTIONAL key env var (the *_PREVIOUS retained keys, #2318).

    Absent/empty → None (no retained key). Present-but-invalid fails loud — a
    malformed retention key would silently break old-archive decryption."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return _parse_backup_key(raw, name=name)


def _key_chain(active: bytes, *retained: bytes | None) -> tuple[bytes, ...]:
    """Active-first, fingerprint-deduped decrypt chain (#2318)."""
    seen: set[str] = set()
    out: list[bytes] = []
    for key in (active, *retained):
        if key is None:
            continue
        fp = hashlib.sha256(key).hexdigest()
        if fp not in seen:
            seen.add(fp)
            out.append(key)
    return tuple(out)


def load_config(env: dict[str, str] | None = None) -> BackupConfig:
    """Load + validate backup config from ``env`` (default: ``os.environ``).

    Fail-closed: ``BACKUP_SWEEP_ENABLED`` unset/not-true ⇒ ``enabled=False``
    and the required-secret checks are skipped (the app boots and serves).
    """
    if env is not None:
        prev = os.environ
        try:
            os.environ = {**os.environ, **{k: v for k, v in env.items() if v is not None}}  # noqa: B003
            return _load_from_env()
        finally:
            os.environ = prev  # noqa: B003
    return _load_from_env()


def _load_from_env() -> BackupConfig:
    enabled = _env_bool("BACKUP_SWEEP_ENABLED", default=False)
    team_sweep_enabled = _env_bool("BACKUP_TEAM_SWEEP_ENABLED", default=False)
    if not enabled:
        # Fail-closed default: build a disabled config with empty keys; the
        # app must not call into the sweep machinery when disabled.
        return BackupConfig(
            enabled=False,
            backup_key=b"",
            registry_stream_key=b"",
            r2_account_id="",
            r2_access_key_id="",
            r2_secret_access_key="",
            r2_bucket="",
            telegram_bot_token="",
            telegram_chat_id="",
            github_issues_pat="",
            alert_assignee="",
            gh_repo=_DEFAULT_GH_REPO,
            team_sweep_enabled=team_sweep_enabled,
        )

    # Enabled — required syncable secrets fail fast at boot. In file-store
    # mode (#2318, BACKUP_KEY_STORE=file) the KEY envs are NOT required — the
    # key file is authoritative — but every other syncable secret still is.
    store_provider = os.environ.get("BACKUP_KEY_STORE", "").strip().lower() or "env"
    if store_provider not in ("env", "file"):
        raise ConfigError(
            f"BACKUP_KEY_STORE={store_provider!r} is not a supported provider — "
            "expected 'env' or 'file'. A cloud KMS provider (e.g. "
            "BACKUP_KEY_STORE=kms) is the documented extension seam "
            "(docs/ops/registry-backup-dr.md §Secret store) but is NOT "
            "implemented in this runtime — no KMS credentials are configured."
        )
    required_envs = tuple(
        n for n in _SYNCABLE_REQUIRED if store_provider == "env" or n != "TORTOISE_BACKUP_KEY"
    )
    missing = [n for n in required_envs if not os.environ.get(n, "").strip()]
    if missing:
        raise ConfigError(
            "Backup sweep enabled but required secrets missing: "
            + ", ".join(missing)
            + " (deploy-hosted.yml sets BACKUP_SWEEP_ENABLED=true only when all are present)"
        )

    if store_provider == "env":
        backup_key = _parse_backup_key(_require("TORTOISE_BACKUP_KEY"))
        # #2318: the *_PREVIOUS retained key is OPTIONAL — it exists only
        # during a rotation overlap window (old archives stay decryptable).
        backup_key_chain = _key_chain(backup_key, _optional_key_env("TORTOISE_BACKUP_KEY_PREVIOUS"))

        # Registry stream key — Fly-only, never synced from GH (#661).
        # Fail-closed: the sweep must not encrypt registry archives with a
        # GH-visible key. The operator sets this out-of-band on Fly.
        registry_stream_raw = os.environ.get("REGISTRY_STREAM_KEY", "").strip()
        if not registry_stream_raw:
            raise ConfigError(
                "REGISTRY_STREAM_KEY not set — required when backup sweep is enabled. "
                "Set it out-of-band on Fly (fly secrets set REGISTRY_STREAM_KEY=...). "
                "It must never be a GitHub secret."
            )
        registry_stream_key = _parse_backup_key(registry_stream_raw)
        registry_stream_key_chain = _key_chain(
            registry_stream_key,
            _optional_key_env("REGISTRY_STREAM_KEY_PREVIOUS"),
        )
    else:  # file store (#2318) — the versioned key file is authoritative.
        try:
            from .secret_store import KeyStoreError, open_key_store

            store = open_key_store(
                "file",
                path=os.environ.get("BACKUP_KEY_STORE_PATH", "").strip() or None,
            )
            backup_key_chain = store.candidates("backup")
            if not backup_key_chain:
                raise ConfigError(
                    "TORTOISE_BACKUP_KEY missing from the backup-keys store — "
                    "seed it with the rotation tool (tools/rotate-backup-keys.py "
                    "--role backup) or switch back to BACKUP_KEY_STORE=env."
                )
            registry_stream_key_chain = store.candidates("registry_stream")
            if not registry_stream_key_chain:
                raise ConfigError(
                    "REGISTRY_STREAM_KEY missing from the backup-keys store — "
                    "seed it with the rotation tool (tools/rotate-backup-keys.py "
                    "--role registry_stream) or switch back to BACKUP_KEY_STORE=env. "
                    "It must never be a GitHub secret (#661)."
                )
        except ConfigError:
            raise
        except KeyStoreError as e:
            raise ConfigError(f"backup-keys store error: {e}") from e
        backup_key = backup_key_chain[0]
        registry_stream_key = registry_stream_key_chain[0]

    # Telegram — required-when-enabled (a silently-dead human channel is #101).
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not telegram_token or not telegram_chat_id:
        raise ConfigError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required when backup sweep "
            "is enabled — a dead human alert channel is a silent no-op (#101)"
        )

    alert_assignee = os.environ.get("BACKUP_ALERT_ASSIGNEE", "").strip()
    if not alert_assignee:
        raise ConfigError("BACKUP_ALERT_ASSIGNEE required when backup sweep is enabled")

    github_issues_pat = os.environ.get("DR_ISSUES_PAT", "").strip()
    if not github_issues_pat:
        raise ConfigError("DR_ISSUES_PAT required when backup sweep is enabled")

    gh_repo = os.environ.get("GH_REPO", "").strip() or _DEFAULT_GH_REPO

    return BackupConfig(
        enabled=True,
        backup_key=backup_key,
        registry_stream_key=registry_stream_key,
        backup_key_chain=backup_key_chain,
        registry_stream_key_chain=registry_stream_key_chain,
        r2_account_id=_require("R2_ACCOUNT_ID"),
        r2_access_key_id=_require("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_require("R2_SECRET_ACCESS_KEY"),
        r2_bucket=_require("R2_BUCKET"),
        telegram_bot_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        github_issues_pat=github_issues_pat,
        alert_assignee=alert_assignee,
        gh_repo=gh_repo,
        stale_threshold_min=_env_int("BACKUP_STALE_THRESHOLD_MIN", 90),
        watcher_poll_seconds=_env_int("BACKUP_WATCHER_POLL_SECONDS", 600),
        driver_down_threshold_min=_env_int("BACKUP_DRIVER_DOWN_THRESHOLD_MIN", 240),
        watcher_grace_min=_env_int("BACKUP_WATCHER_GRACE_MIN", 120),
        size_guard_max_nodes=_env_int("BACKUP_SIZE_GUARD_MAX_NODES", 100_000),
        skip_fresh_min=_env_int("BACKUP_SKIP_FRESH_MIN", 45),
        retention_hourly=_env_int("BACKUP_RETENTION_HOURLY", 24),
        retention_daily=_env_int("BACKUP_RETENTION_DAILY", 7),
        retention_weekly=_env_int("BACKUP_RETENTION_WEEKLY", 4),
        simulate_enabled=_env_bool("BACKUP_SIMULATE_ENABLED", default=False),
        team_sweep_enabled=_env_bool("BACKUP_TEAM_SWEEP_ENABLED", default=False),
    )
