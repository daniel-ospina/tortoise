"""Managed secret store for backup encryption keys (#2318).

Why: backup-encryption key management was static env secrets with a manual
rotation runbook (docs/ops/registry-backup-dr.md). This module is the managed
secret-store seam + rotation machinery:

- Two key roles share the same AES-256-GCM crypto:
    backup          → ``TORTOISE_BACKUP_KEY`` (user-facing backups; GH-syncable
                      Fly secret; also the selfhost export key)
    registry_stream → ``REGISTRY_STREAM_KEY`` (sweep archives, #661 — Fly-only
                      out-of-band secret, NEVER a GitHub secret)
- Rotation keeps a DUAL-KEY overlap window: a new active key is minted while
  the previous active key is RETAINED (``*_PREVIOUS`` env var or an older
  version in the file store) so archives encrypted under the old key stay
  decryptable in-app during the window; the retained key is purged after the
  overlap (second rotation overwrites it / ``purge_retained``).
- Provider seam (``open_key_store``):
    env  (default — back-compat: Fly env secrets; rotation = operator applies
          the env plan via ``fly secrets set``/GH secret updates)
    file (versioned 0600 JSON store — selfhost + tests + the migration path;
          a stepping stone whose layout maps 1:1 onto a cloud KMS)
  A cloud KMS adapter (AWS Secrets Manager / GCP / Vault / Cloudflare) is the
  documented extension point — NO provider is implemented here because no
  cloud credentials exist in this runtime; ``open_key_store("kms")`` fails
  closed with instructions (see docs/ops/registry-backup-dr.md §Secret store).

No key material is ever written to logs: surfaces expose 8-hex fingerprints
only (same prefix convention as the export envelope header).
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import secrets
import tempfile
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Protocol

_AES_KEY_SIZE = 32
_FP_HEX = 8

STORE_FORMAT = "tortoise-backup-keys"
STORE_VERSION = 1
DEFAULT_STORE_PATH = "~/.tortoise/backup-keys.json"


class KeyStoreError(RuntimeError):
    """A secret-store key is missing, malformed, or the provider is unknown.

    Fail-loud by design (the #265/#596 encryption posture): a misconfigured
    retention key must never silently degrade to "cannot decrypt" — a backup
    that cannot be decrypted is data loss waiting to happen."""


@dataclass(frozen=True)
class KeyRole:
    """One encryption-surface role: its env names + human description."""

    name: str
    active_env: str
    previous_env: str
    description: str


ROLES: dict[str, KeyRole] = {
    "backup": KeyRole(
        "backup",
        "TORTOISE_BACKUP_KEY",
        "TORTOISE_BACKUP_KEY_PREVIOUS",
        "user-facing backups (also the selfhost export/import key)",
    ),
    "registry_stream": KeyRole(
        "registry_stream",
        "REGISTRY_STREAM_KEY",
        "REGISTRY_STREAM_KEY_PREVIOUS",
        "sweep backup archives (#661 — Fly-only, never a GitHub secret)",
    ),
}

PROVIDER_ENV = "BACKUP_KEY_STORE"
PROVIDER_PATH_ENV = "BACKUP_KEY_STORE_PATH"


def role(name: str) -> KeyRole:
    """Resolve a role name, raising KeyStoreError for unknown roles."""
    try:
        return ROLES[name]
    except KeyError:
        raise KeyStoreError(
            f"unknown backup-key role {name!r} — expected one of: " + ", ".join(sorted(ROLES))
        ) from None


# ── key material helpers ─────────────────────────────────────────────────────


def generate_key() -> bytes:
    """Fresh 32-byte AES-256 key (never a default, never derived)."""
    return secrets.token_bytes(_AES_KEY_SIZE)


def encode_key(key: bytes) -> str:
    """base64 wire form (the env/file-store representation)."""
    return base64.b64encode(key).decode()


def decode_key(raw: str, *, env_name: str | None = None) -> bytes:
    """Strict base64 decode of a 32-byte key.

    ``env_name`` is included in errors so a misconfiguration names the exact
    env var / store field to fix. Tolerates a trailing newline (shell-quoted
    secrets commonly carry one).
    """
    label = env_name or "backup key"
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception as e:
        raise KeyStoreError(f"{label} must be base64-encoded (got {raw[:8]!r}...): {e}") from e
    if len(key) != _AES_KEY_SIZE:
        raise KeyStoreError(f"{label} must decode to {_AES_KEY_SIZE} bytes (got {len(key)})")
    return key


def key_fingerprint(key: bytes) -> str:
    """8-hex sha256 prefix — the ONLY key identity that may reach logs."""
    return sha256(key).hexdigest()[:_FP_HEX]


def _dedup(keys: list[bytes]) -> tuple[bytes, ...]:
    """Fingerprint-dedup, preserving order (active first)."""
    seen: set[str] = set()
    out: list[bytes] = []
    for k in keys:
        fp = key_fingerprint(k)
        if fp not in seen:
            seen.add(fp)
            out.append(k)
    return tuple(out)


# ── the seam ─────────────────────────────────────────────────────────────────


class KeyStore(Protocol):
    """A managed store for one or both backup key roles.

    ``active(role)`` — the current ENCRYPT key (may be None before first
    rotation). ``candidates(role)`` — decrypt keys, active first, including
    retained previous versions during the rotation overlap window.
    """

    def active(self, name: str) -> bytes | None: ...
    def candidates(self, name: str) -> tuple[bytes, ...]: ...


class EnvKeyStore:
    """Environment-backed store (the Fly deploy + back-compat default).

    ``active`` = ``{ACTIVE_ENV}``; retained candidates = ``{PREVIOUS_ENV}``.
    Read-only at runtime (process env is fixed at boot); rotation is planned
    with ``plan_env_rotation`` and applied out-of-band by the operator
    (``fly secrets set`` / GitHub secret / .env). Invalid key material fails
    loud with the env var named.
    """

    def __init__(self, env: Mapping[str, str] | None = None):
        self._env = os.environ if env is None else env

    def _raw(self, role_name: str, env_name: str) -> str:
        return str(self._env.get(env_name, "")).strip()

    def active(self, name: str) -> bytes | None:
        r = role(name)
        raw = self._raw(name, r.active_env)
        if not raw:
            return None
        return decode_key(raw, env_name=r.active_env)

    def candidates(self, name: str) -> tuple[bytes, ...]:
        r = role(name)
        keys: list[bytes] = []
        raw = self._raw(name, r.active_env)
        if raw:
            keys.append(decode_key(raw, env_name=r.active_env))
        raw_prev = self._raw(name, r.previous_env)
        if raw_prev:
            keys.append(decode_key(raw_prev, env_name=r.previous_env))
        return _dedup(keys)


# ── env rotation plan (pure — operator applies the assignments) ─────────────


@dataclass(frozen=True)
class EnvRotationPlan:
    """A computed rotation for an env-backed store (Fly/GH secrets).

    ``assignments`` maps env var → base64 value; a None value means the var
    must be UNSET. Applying the plan yields the dual-key overlap state:
    ``{ACTIVE}`` = the new key (encrypt), ``{PREVIOUS}`` = the old active key
    (retained for decrypt). The operator applies it out-of-band (the app
    cannot mutate its own process env) and purges the retained key after the
    overlap window with a follow-up ``fly secrets unset {PREVIOUS}``.
    """

    role: KeyRole
    new_key: bytes
    old_active: bytes | None
    assignments: dict[str, str | None]

    @property
    def new_fingerprint(self) -> str:
        return key_fingerprint(self.new_key)

    @property
    def old_active_fingerprint(self) -> str | None:
        return key_fingerprint(self.old_active) if self.old_active else None


def plan_env_rotation(
    role_name: str,
    env: Mapping[str, str] | None = None,
    *,
    new_key: bytes | None = None,
) -> EnvRotationPlan:
    """Compute an env-backed rotation for ``role_name``.

    Mint a fresh key (or accept ``new_key`` for deterministic tests/replay),
    promote the current active key to the retained slot, and return the exact
    env assignments to apply. The previous retained key (if any) is dropped by
    the promotion — the overlap window is bounded to (new, old-active); an
    operator who needs a longer window simply delays applying the plan.
    """
    r = role(role_name)
    store = EnvKeyStore(env)
    old_active = store.active(role_name)
    new = new_key if new_key is not None else generate_key()
    if len(new) != _AES_KEY_SIZE:
        raise KeyStoreError(f"new key must decode to {_AES_KEY_SIZE} bytes (got {len(new)})")
    assignments: dict[str, str | None] = {r.active_env: encode_key(new)}
    if old_active is not None:
        assignments[r.previous_env] = encode_key(old_active)
    return EnvRotationPlan(role=r, new_key=new, old_active=old_active, assignments=assignments)


# ── file-backed versioned store ─────────────────────────────────────────────


@dataclass(frozen=True)
class RotationResult:
    """Outcome of a FileKeyStore rotation/purge."""

    role: KeyRole
    new_key: bytes
    old_active: bytes | None
    dropped_fingerprints: tuple[str, ...]

    @property
    def new_fingerprint(self) -> str:
        return key_fingerprint(self.new_key)


class FileKeyStore:
    """Versioned, file-backed secret store (selfhost + tests + migration path).

    Layout (``{path}``, chmod 0600, atomic tempfile+rename writes):
        {"store": "tortoise-backup-keys", "version": 1,
         "roles": {role: [{"key": b64, "fingerprint": fp,
                           "created_at": iso}, …]}}
    Versions are newest-first; the newest is the ACTIVE (encrypt) key and
    every retained version is a decrypt candidate.

    ``keep_versions`` bounds the rotation overlap window (default 2 = active +
    one retained previous key). A rotation mints a new active version and
    drops the oldest version past the bound; ``purge_retained`` drops every
    retained version (post-overlap cleanup). A threading lock serializes
    read-modify-write in-process; atomic replace keeps concurrent readers from
    observing a torn file. Single-writer is the documented contract across
    processes (same as the R2 last-writer-wins state files).
    """

    def __init__(self, path: str | Path, *, keep_versions: int = 2):
        self._path = Path(path).expanduser()
        self._keep = max(1, int(keep_versions))
        self._lock = threading.RLock()

    # ── read ────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, object]:
        if not self._path.exists():
            return {"store": STORE_FORMAT, "version": STORE_VERSION, "roles": {}}
        try:
            doc = json.loads(self._path.read_text())
        except (OSError, ValueError) as e:
            raise KeyStoreError(f"backup-keys store {self._path} unreadable/corrupt: {e}") from e
        if not isinstance(doc, dict) or doc.get("store") != STORE_FORMAT:
            raise KeyStoreError(f"{self._path} is not a tortoise backup-keys store")
        return doc

    def _versions(self, doc: dict[str, object], name: str) -> list[dict[str, str]]:
        roles = doc.get("roles")
        if not isinstance(roles, dict):
            return []
        raw = roles.get(name)
        if not isinstance(raw, list):
            return []
        out: list[dict[str, str]] = []
        for entry in raw:
            if isinstance(entry, dict) and isinstance(entry.get("key"), str):
                out.append(entry)  # type: ignore[arg-type]
        return out

    def _decode_version(self, entry: dict[str, str], name: str) -> bytes:
        r = role(name)
        return decode_key(entry["key"], env_name=f"{r.active_env} (store {self._path})")

    def active(self, name: str) -> bytes | None:
        with self._lock:
            versions = self._versions(self._load(), name)
            if not versions:
                return None
            return self._decode_version(versions[0], name)

    def candidates(self, name: str) -> tuple[bytes, ...]:
        with self._lock:
            versions = self._versions(self._load(), name)
            return _dedup([self._decode_version(v, name) for v in versions])

    # ── write ───────────────────────────────────────────────────────────────

    def _save(self, doc: dict[str, object]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".backup-keys-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp)  # only exists when the try block raised pre-replace

    def _store_versions(
        self, doc: dict[str, object], name: str, versions: list[dict[str, str]]
    ) -> None:
        roles = doc.setdefault("roles", {})
        if not isinstance(roles, dict):
            roles = {}
            doc["roles"] = roles
        roles[name] = versions

    def rotate(self, name: str, *, new_key: bytes | None = None) -> RotationResult:
        """Mint a new active key for ``name``, retaining the previous active
        key as a decrypt candidate and dropping versions past ``keep_versions``.

        First rotation on an empty store seeds the role (old_active None)."""
        r = role(name)
        new = new_key if new_key is not None else generate_key()
        if len(new) != _AES_KEY_SIZE:
            raise KeyStoreError(f"new key must decode to {_AES_KEY_SIZE} bytes (got {len(new)})")
        with self._lock:
            doc = self._load()
            versions = self._versions(doc, name)
            old_active = self._decode_version(versions[0], name) if versions else None
            now = datetime.now(UTC).isoformat()
            versions.insert(
                0,
                {
                    "key": encode_key(new),
                    "fingerprint": key_fingerprint(new),
                    "created_at": now,
                },
            )
            dropped: list[str] = []
            while len(versions) > self._keep:
                tail = versions.pop()
                fp = tail.get("fingerprint")
                if fp:
                    dropped.append(fp)
            self._store_versions(doc, name, versions)
            self._save(doc)
        return RotationResult(
            role=r, new_key=new, old_active=old_active, dropped_fingerprints=tuple(dropped)
        )

    def purge_retained(self, name: str) -> tuple[str, ...]:
        """Drop every retained (non-active) version — post-overlap cleanup."""
        with self._lock:
            doc = self._load()
            versions = self._versions(doc, name)
            if not versions:
                return ()
            dropped = [str(v["fingerprint"]) for v in versions[1:] if v.get("fingerprint")]
            self._store_versions(doc, name, versions[:1])
            self._save(doc)
            return tuple(dropped)


# ── factory ─────────────────────────────────────────────────────────────────


def open_key_store(
    provider: str | None = None,
    *,
    path: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> KeyStore:
    """Open the configured secret-store provider.

    Selection (in precedence order): the ``provider`` argument, else the
    ``BACKUP_KEY_STORE`` env var (default ``env`` — the pre-#2318 behavior,
    zero config change for existing deploys). ``file`` requires a path: the
    ``BACKUP_KEY_STORE_PATH`` env var or ``path`` (default
    ``~/.tortoise/backup-keys.json``).

    Cloud KMS providers are the documented extension seam — the runtime has no
    cloud credentials, so selecting one fails closed with instructions rather
    than silently degrading to a weaker store.
    """
    src = env if env is not None else os.environ
    selected = (provider or "").strip() or str(src.get(PROVIDER_ENV, "")).strip() or "env"
    selected = selected.lower()
    if selected == "env":
        return EnvKeyStore(env)
    if selected == "file":
        p = path or str(src.get(PROVIDER_PATH_ENV, "")).strip() or DEFAULT_STORE_PATH
        return FileKeyStore(p)
    raise KeyStoreError(
        f"BACKUP_KEY_STORE={selected!r} is not a supported provider — expected "
        "'env' or 'file'. A cloud KMS provider (e.g. BACKUP_KEY_STORE=kms) is "
        "the documented extension seam (docs/ops/registry-backup-dr.md "
        "§Secret store) but is NOT implemented in this runtime: no KMS "
        "credentials are configured here. Implement the KeyStore adapter "
        "against the cloud SDK and select it by name — until then the store "
        "fails closed rather than degrading to an unmanaged key."
    )
