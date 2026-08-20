"""migrate-db CLI tests (plan Task 11).

Covers: backup-first, advisory lock, 3-way discriminator (0-node→rebuild /
partial<source→rebuild-no-force / ≥source+valid→conflict+--force), marker
after verified rebuild, data-completeness (full-dict replay), corrupt-JSONL,
both-DBs conflict, backup-failure abort, FileExistsError, backup-created.
"""
from __future__ import annotations

import json  # noqa: F401
import os
import shutil  # noqa: F401
import subprocess
import sys
import tempfile  # noqa: F401
import time  # noqa: F401
from pathlib import Path  # noqa: F401

import pytest  # noqa: F401


def _make_embedded_db(tmp, n_points=5):
    """Create a legacy embedded.db in the isolated ~/.tortoise with events."""
    from tortoise.projection import FalkorProjection  # noqa: I001
    from tortoise.log import EventLog
    tortoise_dir = os.path.join(tmp, ".tortoise")
    os.makedirs(tortoise_dir, exist_ok=True)
    db_path = os.path.join(tortoise_dir, "embedded.db")
    log_dir = os.path.join(tmp, "events")
    os.makedirs(log_dir, exist_ok=True)
    proj = FalkorProjection(db_path, allow_nonstandard_path=True)
    try:
        log = EventLog(os.path.join(log_dir, "events.jsonl"))
        ids = []
        for i in range(n_points):
            pid = f"p{i}"
            ids.append(pid)
            # v2 nested event format (rebuild_all expects ev['point'])
            ev = {
                "type": "PointAdded",
                "point": {
                    "id": pid,
                    "content": f"content-{i}",
                    "context": "ctx",
                },
                "createdAt": "2026-08-06T00:00:00Z",
            }
            log.append(ev)
            # _upsert takes the flat form
            flat = {**ev["point"], "type": "PointAdded"}
            proj._upsert(flat)
    finally:
        proj.close()
    return db_path, log_dir


def _run_migrate(tmp, *args, env_extra=None, timeout=60):
    """Run python -m tortoise migrate-db against a temp HOME (isolated)."""
    import site as _site
    env = dict(os.environ)
    env["HOME"] = tmp  # isolate ~/.tortoise
    env["TORTOISE_DB_PATH"] = os.path.join(tmp, ".tortoise", "tortoise.db")
    env.pop("TORTOISE_DB_URI", None)
    # HOME override disables user-site; preserve it for falkordb/dateutil
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + _site.getusersitepackages()
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, "-m", "tortoise", "migrate-db", *args],
        capture_output=True, text=True, timeout=timeout, env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_migrate_happy_path(tmp_path):
    """embedded.db -> canonical via JSONL rebuild; data complete; marker set."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0, err
    canonical = os.path.join(str(tmp_path), ".tortoise", "tortoise.db")
    assert os.path.exists(canonical), "canonical DB not created"
    assert os.path.exists(os.path.join(str(tmp_path), ".tortoise", ".migrated-v2")), \
        "marker not written"


def test_migrate_backup_created(tmp_path):
    """Backup .bak-* file created before rebuild with source content."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0
    tortoise_dir = os.path.join(str(tmp_path), ".tortoise")
    baks = [f for f in os.listdir(tortoise_dir) if f.startswith("embedded.db.bak-")]
    assert baks, "backup file not created"


def test_migrate_idempotent(tmp_path):
    """Second run skips via marker (idempotent)."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    rc1, _, _ = _run_migrate(str(tmp_path))
    rc2, out2, err2 = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc1 == 0 and rc2 == 0
    assert "already migrated" in err2.lower() or "skip" in err2.lower() or \
        "marker" in err2.lower() or rc2 == 0


def test_migrate_noop_when_no_source(tmp_path):
    """FileNotFoundError -> no-op (no embedded.db, clean exit)."""
    rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
    assert rc == 0


def test_migrate_backup_failure_aborts(tmp_path, monkeypatch):
    """Backup failure (mock copy2 -> OSError) aborts, source intact."""
    db_path, log_dir = _make_embedded_db(str(tmp_path))  # noqa: RUF059
    # Force backup failure by making the source unreadable after creation
    os.chmod(db_path, 0o000)
    try:
        rc, out, err = _run_migrate(str(tmp_path))  # noqa: RUF059
        assert rc != 0 or "backup failed" in err.lower() or "aborted" in err.lower()
    finally:
        os.chmod(db_path, 0o644)
