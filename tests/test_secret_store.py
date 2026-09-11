"""Tests for tortoise/secret_store.py — managed backup-key secret store (#2318).

Covers the store seam (env + file providers), dual-key retention/rotation
(active key for encrypt, retained previous key for decrypt during the overlap
window, then purge), provider selection, and the backup_config wiring
(key chains surface in BackupConfig).

#2318 scope: no cloud-KMS provider is implemented (no credentials in this
environment) — the provider seam + factory fail-closed + the DR runbook
document the KMS extension point.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import pytest

from tortoise import secret_store as ss
from tortoise.backup_config import ConfigError, load_config
from tortoise.hosted_backup import (
    _decrypt_candidate_keys,
    decrypt_backup,
    encrypt_backup,
)


def _env(
    active: str | None = None, previous: str | None = None, name: str = "TORTOISE_BACKUP_KEY"
) -> dict[str, str]:
    out: dict[str, str] = {}
    if active is not None:
        out[name] = active
    prev = name + "_PREVIOUS" if name.endswith("_KEY") else name
    if previous is not None:
        out[prev] = previous
    return out


def _b64(data: bytes = b"k" * 32) -> str:
    return base64.b64encode(data).decode()


def _key_bytes(byte: int = 7) -> bytes:
    return bytes([byte]) * 32


# ── encode/decode + fingerprints ────────────────────────────────────────────


def test_generate_key_returns_32_bytes_unique():
    a = ss.generate_key()
    b = ss.generate_key()
    assert len(a) == 32 and len(b) == 32
    assert a != b


def test_encode_decode_roundtrip():
    key = ss.generate_key()
    assert ss.decode_key(ss.encode_key(key)) == key


def test_decode_rejects_non_base64():
    with pytest.raises(ss.KeyStoreError, match="base64") as exc:
        ss.decode_key("not-base64!!", env_name="TORTOISE_BACKUP_KEY_PREVIOUS")
    # #2796 review (test-review): assert on the 8-char prefix the pre-fix code
    # emitted (asserting the full value would pass vacuously) and pin the
    # fingerprint so a constant placeholder also fails.
    assert "not-base" not in str(exc.value)
    assert hashlib.sha256(b"not-base64!!").hexdigest()[:8] in str(exc.value)


def test_decode_rejects_wrong_length():
    raw = base64.b64encode(b"short").decode()
    with pytest.raises(ss.KeyStoreError, match="32 bytes"):
        ss.decode_key(raw)


def test_decode_tolerates_trailing_newline():
    raw = ss.encode_key(_key_bytes()) + "\n"
    assert ss.decode_key(raw) == _key_bytes()


def test_fingerprint_is_8_hex_stable_and_distinct():
    a, b = _key_bytes(1), _key_bytes(2)
    assert ss.key_fingerprint(a) == ss.key_fingerprint(a)
    assert len(ss.key_fingerprint(a)) == 8
    assert ss.key_fingerprint(a) != ss.key_fingerprint(b)


def test_roles_cover_both_encryption_surfaces():
    assert set(ss.ROLES) == {"backup", "registry_stream"}
    assert ss.ROLES["backup"].active_env == "TORTOISE_BACKUP_KEY"
    assert ss.ROLES["backup"].previous_env == "TORTOISE_BACKUP_KEY_PREVIOUS"
    assert ss.ROLES["registry_stream"].active_env == "REGISTRY_STREAM_KEY"
    assert ss.ROLES["registry_stream"].previous_env == "REGISTRY_STREAM_KEY_PREVIOUS"


# ── EnvKeyStore ─────────────────────────────────────────────────────────────


def test_env_store_active_none_when_unset(monkeypatch):
    monkeypatch.delenv("TORTOISE_BACKUP_KEY", raising=False)
    assert ss.EnvKeyStore().active("backup") is None


def test_env_store_candidates_active_only():
    store = ss.EnvKeyStore(_env(active=_b64(_key_bytes(1))))
    assert store.candidates("backup") == (_key_bytes(1),)


def test_env_store_candidates_include_previous_newest_first():
    store = ss.EnvKeyStore(_env(active=_b64(_key_bytes(2)), previous=_b64(_key_bytes(1))))
    assert store.candidates("backup") == (_key_bytes(2), _key_bytes(1))


def test_env_store_candidates_dedup_active_equals_previous():
    key = _b64(_key_bytes(3))
    store = ss.EnvKeyStore(_env(active=key, previous=key))
    assert store.candidates("backup") == (_key_bytes(3),)


def test_env_store_invalid_previous_fails_loud():
    store = ss.EnvKeyStore(_env(active=_b64(), previous="junk!!"))
    with pytest.raises(ss.KeyStoreError, match="TORTOISE_BACKUP_KEY_PREVIOUS"):
        store.candidates("backup")


def test_env_store_stream_role_uses_stream_vars():
    env = _env(active=_b64(_key_bytes(5)), name="REGISTRY_STREAM_KEY")
    store = ss.EnvKeyStore(env)
    assert store.active("registry_stream") == _key_bytes(5)


# ── env rotation plan ───────────────────────────────────────────────────────


def test_env_rotation_bootstrap_generates_first_key():
    plan = ss.plan_env_rotation("backup", env=_env(), new_key=_key_bytes(9))
    assert plan.old_active is None
    assert plan.assignments["TORTOISE_BACKUP_KEY"] == ss.encode_key(_key_bytes(9))
    assert "TORTOISE_BACKUP_KEY_PREVIOUS" not in plan.assignments


def test_env_rotation_moves_active_to_previous():
    env = _env(active=_b64(_key_bytes(1)))
    plan = ss.plan_env_rotation("backup", env=env, new_key=_key_bytes(2))
    assert plan.old_active == _key_bytes(1)
    assert plan.assignments["TORTOISE_BACKUP_KEY"] == ss.encode_key(_key_bytes(2))
    assert plan.assignments["TORTOISE_BACKUP_KEY_PREVIOUS"] == ss.encode_key(_key_bytes(1))


def test_env_rotation_drops_old_previous_in_assignments():
    """A second rotation replaces the previous slot (the old previous key is
    overwritten by the assignment — the operator applies the full plan)."""
    env = _env(active=_b64(_key_bytes(2)), previous=_b64(_key_bytes(1)))
    plan = ss.plan_env_rotation("backup", env=env, new_key=_key_bytes(3))
    assert plan.assignments["TORTOISE_BACKUP_KEY_PREVIOUS"] == ss.encode_key(_key_bytes(2))
    # Applying the plan yields a consistent dual-key env.
    applied = dict(env)
    applied.update({k: v for k, v in plan.assignments.items() if v is not None})
    assert ss.EnvKeyStore(applied).candidates("backup") == (
        _key_bytes(3),
        _key_bytes(2),
    )


def test_env_rotation_stream_role_and_random_key():
    env = _env(active=_b64(_key_bytes(1)), name="REGISTRY_STREAM_KEY")
    plan = ss.plan_env_rotation("registry_stream", env=env)
    assert len(plan.new_key) == 32
    assert plan.old_active == _key_bytes(1)
    assert plan.assignments["REGISTRY_STREAM_KEY_PREVIOUS"] == ss.encode_key(_key_bytes(1))


def test_env_rotation_unknown_role_rejected():
    with pytest.raises(ss.KeyStoreError, match="backup"):
        ss.plan_env_rotation("nope", env=_env())


# ── FileKeyStore ────────────────────────────────────────────────────────────


def test_file_store_empty_active_none(tmp_path):
    store = ss.FileKeyStore(tmp_path / "keys.json")
    assert store.active("backup") is None
    assert store.candidates("backup") == ()


def test_file_store_rotate_seeds_first_version(tmp_path):
    store = ss.FileKeyStore(tmp_path / "keys.json")
    res = store.rotate("backup", new_key=_key_bytes(1))
    assert res.old_active is None
    assert res.new_key == _key_bytes(1)
    assert store.active("backup") == _key_bytes(1)
    assert store.candidates("backup") == (_key_bytes(1),)


def test_file_store_second_rotation_retains_old_key(tmp_path):
    """The dual-key overlap window: after rotation the OLD key is retained as
    a decrypt candidate; encrypt uses the new active key."""
    store = ss.FileKeyStore(tmp_path / "keys.json")
    store.rotate("backup", new_key=_key_bytes(1))
    res = store.rotate("backup", new_key=_key_bytes(2))
    assert res.old_active == _key_bytes(1)
    assert store.active("backup") == _key_bytes(2)
    assert store.candidates("backup") == (_key_bytes(2), _key_bytes(1))
    assert store.candidates("backup")[0] == _key_bytes(2)  # newest first


def test_file_store_third_rotation_purges_oldest(tmp_path):
    """keep_versions=2: a third rotation drops the oldest key (the window is
    bounded — old keys past the overlap are purged on the next rotation)."""
    store = ss.FileKeyStore(tmp_path / "keys.json")
    store.rotate("backup", new_key=_key_bytes(1))
    store.rotate("backup", new_key=_key_bytes(2))
    res = store.rotate("backup", new_key=_key_bytes(3))
    assert res.dropped_fingerprints == (ss.key_fingerprint(_key_bytes(1)),)
    assert store.candidates("backup") == (_key_bytes(3), _key_bytes(2))


def test_file_store_purge_retained_keeps_only_active(tmp_path):
    store = ss.FileKeyStore(tmp_path / "keys.json")
    store.rotate("backup", new_key=_key_bytes(1))
    store.rotate("backup", new_key=_key_bytes(2))
    dropped = store.purge_retained("backup")
    assert dropped == (ss.key_fingerprint(_key_bytes(1)),)
    assert store.candidates("backup") == (_key_bytes(2),)
    assert store.active("backup") == _key_bytes(2)


def test_file_store_roles_are_independent(tmp_path):
    store = ss.FileKeyStore(tmp_path / "keys.json")
    store.rotate("backup", new_key=_key_bytes(1))
    store.rotate("registry_stream", new_key=_key_bytes(9))
    assert store.active("backup") == _key_bytes(1)
    assert store.active("registry_stream") == _key_bytes(9)
    assert store.candidates("registry_stream") == (_key_bytes(9),)


def test_file_store_persists_across_reopen(tmp_path):
    path = tmp_path / "keys.json"
    ss.FileKeyStore(path).rotate("backup", new_key=_key_bytes(1))
    ss.FileKeyStore(path).rotate("backup", new_key=_key_bytes(2))
    reopened = ss.FileKeyStore(path)
    assert reopened.candidates("backup") == (_key_bytes(2), _key_bytes(1))


def test_file_store_writes_0600_and_utf8_json(tmp_path):
    path = tmp_path / "keys.json"
    ss.FileKeyStore(path).rotate("backup", new_key=_key_bytes(1))
    assert (path.stat().st_mode & 0o777) == 0o600
    doc = json.loads(path.read_text())
    assert doc["store"] == "tortoise-backup-keys"
    ver = doc["roles"]["backup"][0]
    assert ver["key"] == ss.encode_key(_key_bytes(1))
    assert ver["fingerprint"] == ss.key_fingerprint(_key_bytes(1))
    assert "created_at" in ver


def test_file_store_corrupt_file_fails_loud(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text("{not json")
    store = ss.FileKeyStore(path)
    with pytest.raises(ss.KeyStoreError, match="unreadable/corrupt"):
        store.candidates("backup")


def test_file_store_invalid_stored_key_fails_loud(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps(
            {
                "store": "tortoise-backup-keys",
                "version": 1,
                "roles": {"backup": [{"key": "not-base64!!"}]},
            }
        )
    )
    with pytest.raises(ss.KeyStoreError, match="base64"):
        ss.FileKeyStore(path).candidates("backup")


# ── provider factory ────────────────────────────────────────────────────────


def test_open_key_store_defaults_to_env():
    assert isinstance(ss.open_key_store(), ss.EnvKeyStore)


def test_open_key_store_env_provider(monkeypatch):
    monkeypatch.setenv("BACKUP_KEY_STORE", "env")
    assert isinstance(ss.open_key_store(), ss.EnvKeyStore)


def test_open_key_store_file_provider(tmp_path):
    path = str(tmp_path / "keys.json")
    store = ss.open_key_store("file", path=path)
    assert isinstance(store, ss.FileKeyStore)
    store.rotate("backup", new_key=_key_bytes(1))
    assert ss.open_key_store("file", path=path).active("backup") == _key_bytes(1)


def test_open_key_store_file_path_from_env(tmp_path, monkeypatch):
    path = tmp_path / "keys.json"
    monkeypatch.setenv("BACKUP_KEY_STORE", "file")
    monkeypatch.setenv("BACKUP_KEY_STORE_PATH", str(path))
    store = ss.open_key_store()
    assert isinstance(store, ss.FileKeyStore)
    assert store._path == path


def test_open_key_store_unknown_provider_fails_closed():
    with pytest.raises(ss.KeyStoreError, match="BACKUP_KEY_STORE"):
        ss.open_key_store("vault")


def test_open_key_store_kms_not_implemented_fails_closed():
    """No cloud-KMS credentials exist in this environment — the factory must
    fail closed and point at the documented extension seam."""
    with pytest.raises(ss.KeyStoreError, match=r"KMS|kms"):
        ss.open_key_store("kms")


# ── backup_config wiring ────────────────────────────────────────────────────


def _good_env() -> dict[str, str]:
    return {
        "BACKUP_SWEEP_ENABLED": "true",
        "TORTOISE_BACKUP_KEY": _b64(_key_bytes(1)),
        "TORTOISE_BACKUP_KEY_PREVIOUS": _b64(_key_bytes(0)),
        "REGISTRY_STREAM_KEY": _b64(_key_bytes(2)),
        "REGISTRY_STREAM_KEY_PREVIOUS": _b64(_key_bytes(3)),
        "R2_ACCOUNT_ID": "acct",
        "R2_ACCESS_KEY_ID": "ak",
        "R2_SECRET_ACCESS_KEY": "sk",
        "R2_BUCKET": "tortoise-backups",
        "TELEGRAM_BOT_TOKEN": "123:token",
        "TELEGRAM_CHAT_ID": "551595722",
        "DR_ISSUES_PAT": "ghp_fake",
        "BACKUP_ALERT_ASSIGNEE": "daniel-ospina",
    }


def test_config_env_mode_surfaces_dual_key_chains(monkeypatch):
    monkeypatch.setattr(os, "environ", _good_env())
    cfg = load_config()
    assert cfg.backup_key == _key_bytes(1)  # active (back-compat field)
    assert cfg.backup_key_chain == (_key_bytes(1), _key_bytes(0))
    assert cfg.registry_stream_key == _key_bytes(2)
    assert cfg.registry_stream_key_chain == (_key_bytes(2), _key_bytes(3))


def test_config_env_mode_chains_empty_without_previous(monkeypatch):
    env = _good_env()
    del env["TORTOISE_BACKUP_KEY_PREVIOUS"]
    del env["REGISTRY_STREAM_KEY_PREVIOUS"]
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.backup_key_chain == (_key_bytes(1),)
    assert cfg.registry_stream_key_chain == (_key_bytes(2),)


def test_config_env_mode_invalid_previous_fails_closed(monkeypatch):
    env = _good_env()
    env["TORTOISE_BACKUP_KEY_PREVIOUS"] = "junk!!"
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="TORTOISE_BACKUP_KEY_PREVIOUS"):
        load_config()


def test_config_disabled_has_empty_chains(monkeypatch):
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    monkeypatch.delenv("TORTOISE_BACKUP_KEY", raising=False)
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.backup_key_chain == ()
    assert cfg.registry_stream_key_chain == ()


def test_config_file_store_mode_resolves_roles(tmp_path, monkeypatch):
    env = _good_env()
    for k in (
        "TORTOISE_BACKUP_KEY",
        "TORTOISE_BACKUP_KEY_PREVIOUS",
        "REGISTRY_STREAM_KEY",
        "REGISTRY_STREAM_KEY_PREVIOUS",
    ):
        del env[k]
    env["BACKUP_KEY_STORE"] = "file"
    env["BACKUP_KEY_STORE_PATH"] = str(tmp_path / "keys.json")
    store = ss.FileKeyStore(tmp_path / "keys.json")
    store.rotate("backup", new_key=_key_bytes(1))
    store.rotate("registry_stream", new_key=_key_bytes(2))
    monkeypatch.setattr(os, "environ", env)
    cfg = load_config()
    assert cfg.backup_key == _key_bytes(1)
    assert cfg.backup_key_chain == (_key_bytes(1),)
    assert cfg.registry_stream_key == _key_bytes(2)


def test_config_file_store_missing_role_fails_closed(tmp_path, monkeypatch):
    env = _good_env()
    for k in (
        "TORTOISE_BACKUP_KEY",
        "TORTOISE_BACKUP_KEY_PREVIOUS",
        "REGISTRY_STREAM_KEY",
        "REGISTRY_STREAM_KEY_PREVIOUS",
    ):
        del env[k]
    env["BACKUP_KEY_STORE"] = "file"
    env["BACKUP_KEY_STORE_PATH"] = str(tmp_path / "keys.json")
    ss.FileKeyStore(tmp_path / "keys.json").rotate("backup", new_key=_key_bytes(1))
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="REGISTRY_STREAM_KEY"):
        load_config()


def test_config_unknown_key_store_provider_fails_closed(monkeypatch):
    env = _good_env()
    env["BACKUP_KEY_STORE"] = "aws-secrets-manager"
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError, match="BACKUP_KEY_STORE"):
        load_config()


def test_config_file_store_bad_path_fails_closed(tmp_path, monkeypatch):
    env = _good_env()
    for k in (
        "TORTOISE_BACKUP_KEY",
        "TORTOISE_BACKUP_KEY_PREVIOUS",
        "REGISTRY_STREAM_KEY",
        "REGISTRY_STREAM_KEY_PREVIOUS",
    ):
        del env[k]
    env["BACKUP_KEY_STORE"] = "file"
    env["BACKUP_KEY_STORE_PATH"] = str(tmp_path / "does-not-exist" / "keys.json")
    monkeypatch.setattr(os, "environ", env)
    with pytest.raises(ConfigError):
        load_config()


# ── hosted_backup decrypt candidates (dual-key restore) ─────────────────────


def test_decrypt_candidate_keys_explicit_first_then_chains(monkeypatch):
    env = _good_env()
    env["BACKUP_KEY_STORE"] = "env"
    monkeypatch.setattr(os, "environ", env)
    explicit = _key_bytes(9)
    keys = _decrypt_candidate_keys(explicit)
    assert keys[0] == explicit
    # Chains carry both roles' active + retained keys, deduped by fingerprint.
    assert keys[1] == _key_bytes(1)
    assert _key_bytes(0) in keys
    assert _key_bytes(2) in keys
    assert _key_bytes(3) in keys
    assert len(keys) == len(set(keys))


def test_decrypt_candidate_keys_env_fallback_when_sweep_disabled(monkeypatch):
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", _b64(_key_bytes(1)))
    monkeypatch.setenv("TORTOISE_BACKUP_KEY_PREVIOUS", _b64(_key_bytes(0)))
    monkeypatch.delenv("BACKUP_SWEEP_ENABLED", raising=False)
    keys = _decrypt_candidate_keys()
    assert keys == (_key_bytes(1), _key_bytes(0))


def test_retained_key_decrypts_old_archive_after_rotation(monkeypatch):
    """Old key (now retained as *_PREVIOUS) decrypts an archive encrypted
    before the rotation — the in-app dual-key window end to end."""
    old_key, new_key = _key_bytes(0), _key_bytes(1)
    blob = encrypt_backup(b"pre-rotation secret", key=old_key)
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", _b64(new_key))
    monkeypatch.setenv("TORTOISE_BACKUP_KEY_PREVIOUS", _b64(old_key))
    # Active (new) key fails alone…
    with pytest.raises(ValueError, match=r"tampered|wrong key"):
        decrypt_backup(blob, key=new_key)
    # …but the candidate chain (new → retained old) succeeds.
    plaintext = None
    for candidate in _decrypt_candidate_keys():
        try:
            plaintext = decrypt_backup(blob, key=candidate)
            break
        except ValueError:
            continue
    assert plaintext == b"pre-rotation secret"


def test_retained_stream_key_decrypts_old_sweep_archive(monkeypatch):
    """#661 cross-role seam preserved under rotation: an OLD sweep archive
    (retained REGISTRY_STREAM_KEY_PREVIOUS) decrypts through the backup-key
    role's candidate chain (the drill path passes cfg.backup_key)."""
    old_stream = _key_bytes(3)  # = REGISTRY_STREAM_KEY_PREVIOUS in _good_env
    blob = encrypt_backup(b"sweep-old", key=old_stream)
    env = _good_env()
    monkeypatch.setattr(os, "environ", env)
    keys = _decrypt_candidate_keys(explicit=_key_bytes(1))  # backup active
    plaintext = None
    for candidate in keys:
        try:
            plaintext = decrypt_backup(blob, key=candidate)
            break
        except ValueError:
            continue
    assert plaintext == b"sweep-old"
    assert ss.key_fingerprint(old_stream) in {ss.key_fingerprint(k) for k in keys}
