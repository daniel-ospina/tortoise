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

from .env_truthy import env_flag  # #4097: the declared truthy contract
from .retention import RESTORE_WINDOW_DAYS  # #4179 single window authority

_AES_KEY_SIZE = 32

# Secrets that the deploy workflow can sync from GH → Fly (the "syncable" set).
# REGISTRY_STREAM_KEY is deliberately EXCLUDED — it must be set out-of-band
# by the operator on Fly (fly secrets set) and never present in GitHub.
# Same for the #2319 lock/mirror surface (CF_API_TOKEN + R2_MIRROR_*):
# optional-when-sweep-enabled, operator-set out-of-band on Fly.
_SYNCABLE_REQUIRED = (
    "TORTOISE_BACKUP_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
)

# ── #2319 bucket-lock window contract ────────────────────────────────────────
# R2 bucket locks (Cloudflare rule API — NOT S3 Object Lock; see
# docs/ops/registry-backup-dr.md §#2319) are prefix-scoped Age retentions that
# block delete/overwrite within the window. BACKUP_LOCK_DAYS must stay
# strictly below the restore window (tortoise/retention.py RESTORE_WINDOW_DAYS
# = 7 days — the #4179 authority; named in docs/retention-and-deletion.md) so a
# purged graph's artifacts (>= grace days old when the purge erases them) are
# never inside the lock window — the erasure promise stays honest. Default 3
# days protects the recovery-critical hourly window.
LOCK_DAYS_MIN = 1
LOCK_DAYS_MAX = RESTORE_WINDOW_DAYS - 1  # keep strictly below the purge window
DEFAULT_LOCK_DAYS = 3

# Mirror-store creds (second-region/second-account copy, env-guarded). When
# R2_MIRROR_ENDPOINT is unset the endpoint derives from R2_MIRROR_ACCOUNT_ID
# (https://{id}.r2.cloudflarestorage.com) — mirror of the primary R2_* shape.
_MIRROR_REQUIRED = (
    "R2_MIRROR_ACCOUNT_ID",
    "R2_MIRROR_ACCESS_KEY_ID",
    "R2_MIRROR_SECRET_ACCESS_KEY",
    "R2_MIRROR_BUCKET",
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
    retention_hourly: int = 24
    retention_daily: int = 7
    retention_weekly: int = 4
    simulate_enabled: bool = False
    org_sweep_enabled: bool = False
    # #2319: immutability + geo-mirror surface (defaults are the documented
    # production values; drift-bounds enforced in _load_from_env).
    lock_enabled: bool = False
    lock_days: int = DEFAULT_LOCK_DAYS
    cf_api_token: str = ""  # R2-scoped Cloudflare API token (lock verification)
    mirror_enabled: bool = False
    mirror_endpoint: str = ""
    mirror_access_key_id: str = ""
    mirror_secret_access_key: str = ""
    mirror_bucket: str = ""


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
        # #2796 review (R2/R4): a malformed key is still secret material — the
        # raw prefix must never reach a log or a published incident body. Report
        # a non-reversible 8-hex identity instead (secret_store.key_fingerprint
        # contract: fingerprints are the only key identity that may be logged).
        got = hashlib.sha256(raw.strip().encode()).hexdigest()[:8]
        raise ConfigError(f"{name} must be base64 (got <{got}>...): {e}") from e
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


def load_alert_config(env: dict[str, str] | None = None) -> BackupConfig | None:
    """The ALERT-channel config, NOT gated on ``BACKUP_SWEEP_ENABLED`` (#3820 D5a).

    ``load_config`` fails closed on the sweep switch: disabled, it returns a
    config carrying EMPTY alert credentials, so every factory built on it
    (``hosted_api._alert_store_from``) disappears exactly when a NON-backup
    path needs to alert. The analytics sink incident (#3820) must be fileable
    on a deployment whose backups are off — the sweep switch decides whether
    backups RUN, never whether a degraded sink is VISIBLE — so its credentials
    are read here ungated. Same env contract (names, precedence, repo default)
    as the sweep's, so the two channels cannot drift apart.

    Returns ``None`` when there is no issue filer (``DR_ISSUES_PAT`` unset):
    with no PAT there is no channel at all, and the caller keeps the counter +
    WARNING log. That NARROWS the documented D6 residual to the CHANNEL's own
    construction — it is never "the sweep is off". ``TORTOISE_BACKUP_KEY`` and
    ``REGISTRY_STREAM_KEY`` are deliberately NOT required: this config files a
    GitHub issue and archives nothing, so their absence cannot silence the
    channel. **R2 is different and IS effectively required.** The config
    carries the R2 fields so the built ``AlertStore`` can use the object store
    for per-(kind, subject) dedup, and that build goes
    ``_analytics_alert_store`` -> ``alert_channel.incident_alert_store`` ->
    ``alert_channel.alert_store_from`` -> the leg's object store (the hosted leg
    injects ``hosted_api._backup_storage``; the stdio leg builds from env) ->
    ``R2Storage()``, whose ``__init__`` RAISES unless ``R2_ACCOUNT_ID`` /
    ``R2_ACCESS_KEY_ID`` / ``R2_SECRET_ACCESS_KEY`` / ``R2_BUCKET`` are all set
    (``TORTOISE_BACKUP_STORAGE=memory`` is the selfhost/test seam). The caller
    swallows that raise and turns the channel into ``None`` -- so a MISSING or
    TYPOED R2 secret DOES silence the channel. The honest D6 residual is
    therefore "no PAT **or** an unusable object store", not "no PAT" alone.

    ``env`` is read as the WHOLE environment mapping (default ``os.environ``);
    unlike ``load_config`` it is not merged over the process env, so a test can
    supply exactly the alert surface it means to pin.
    """
    e = os.environ if env is None else env

    def _get(name: str) -> str:
        return (e.get(name) or "").strip()

    github_issues_pat = _get("DR_ISSUES_PAT")
    if not github_issues_pat:
        return None
    return BackupConfig(
        # `enabled` is the SWEEP switch and stays False: this config carries the
        # alert channel only (`_alert_store_from` reads the alert fields).
        enabled=False,
        # Sweep-only key material is absent by design (see the docstring).
        backup_key=b"",
        registry_stream_key=b"",
        r2_account_id=_get("R2_ACCOUNT_ID"),
        r2_access_key_id=_get("R2_ACCESS_KEY_ID"),
        r2_secret_access_key=_get("R2_SECRET_ACCESS_KEY"),
        r2_bucket=_get("R2_BUCKET"),
        telegram_bot_token=_get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get("TELEGRAM_CHAT_ID"),
        github_issues_pat=github_issues_pat,
        alert_assignee=_get("BACKUP_ALERT_ASSIGNEE"),
        gh_repo=_get("GH_REPO") or _DEFAULT_GH_REPO,
    )


def _load_from_env() -> BackupConfig:
    enabled = env_flag("BACKUP_SWEEP_ENABLED", False)
    org_sweep_enabled = env_flag("BACKUP_TEAM_SWEEP_ENABLED", False)
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
            org_sweep_enabled=org_sweep_enabled,
        )

    # ── #2319 immutability contract (validated when the sweep is enabled). ──
    lock_enabled = env_flag("BACKUP_LOCK_ENABLED", False)
    lock_days = _env_int("BACKUP_LOCK_DAYS", DEFAULT_LOCK_DAYS)
    if lock_enabled and not (LOCK_DAYS_MIN <= lock_days <= LOCK_DAYS_MAX):
        raise ConfigError(
            f"BACKUP_LOCK_DAYS must be {LOCK_DAYS_MIN}..{LOCK_DAYS_MAX} "
            f"(got {lock_days}) — the lock window must stay below the "
            "#2304 trash grace (7d) so purge erasure of a deleted graph's "
            "backup artifacts is never blocked"
        )
    # CF_API_TOKEN is optional: without it the live lock-config verification
    # (/v1/internal/backups/verify-lock) reports unverifiable and the runbook
    # (console/wrangler steps) is the verification path (#2319).
    cf_api_token = os.environ.get("CF_API_TOKEN", "").strip()

    # ── #2319 geo-mirror (second-store copy) — env-guarded, fail-closed. ──
    mirror_enabled = env_flag("BACKUP_MIRROR_ENABLED", False)
    mirror_account_id = os.environ.get("R2_MIRROR_ACCOUNT_ID", "").strip()
    mirror_access_key_id = os.environ.get("R2_MIRROR_ACCESS_KEY_ID", "").strip()
    mirror_secret_access_key = os.environ.get("R2_MIRROR_SECRET_ACCESS_KEY", "").strip()
    mirror_bucket = os.environ.get("R2_MIRROR_BUCKET", "").strip()
    if mirror_enabled:
        missing = [n for n, v in (
            ("R2_MIRROR_ACCOUNT_ID", mirror_account_id),
            ("R2_MIRROR_ACCESS_KEY_ID", mirror_access_key_id),
            ("R2_MIRROR_SECRET_ACCESS_KEY", mirror_secret_access_key),
            ("R2_MIRROR_BUCKET", mirror_bucket),
        ) if not v]
        if missing:
            raise ConfigError(
                "BACKUP_MIRROR_ENABLED=true but mirror store creds missing: "
                + ", ".join(missing)
                + " (set them out-of-band on Fly; they never sync from GH)"
            )
    mirror_endpoint = (
        os.environ.get("R2_MIRROR_ENDPOINT", "").strip()
        or (f"https://{mirror_account_id}.r2.cloudflarestorage.com" if mirror_account_id else "")
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
        retention_hourly=_env_int("BACKUP_RETENTION_HOURLY", 24),
        retention_daily=_env_int("BACKUP_RETENTION_DAILY", 7),
        retention_weekly=_env_int("BACKUP_RETENTION_WEEKLY", 4),
        simulate_enabled=env_flag("BACKUP_SIMULATE_ENABLED", False),
        org_sweep_enabled=env_flag("BACKUP_TEAM_SWEEP_ENABLED", False),
        lock_enabled=lock_enabled,
        lock_days=lock_days,
        cf_api_token=cf_api_token,
        mirror_enabled=mirror_enabled,
        mirror_endpoint=mirror_endpoint,
        mirror_access_key_id=mirror_access_key_id,
        mirror_secret_access_key=mirror_secret_access_key,
        mirror_bucket=mirror_bucket,
    )
