"""Tests for backup — backup/restore round-trip."""
from __future__ import annotations  # noqa: I001

import json
import logging
import os
import tempfile
from pathlib import Path

import pytest  # noqa: F401
from tortoise.backup import _bgsave, backup, restore


def test_backup_creates_timestamped_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fake events.jsonl
        events_path = os.path.join(tmpdir, "events.jsonl")
        with open(events_path, "w") as f:
            f.write(json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "hello"}}) + "\n")

        db_path = os.path.join(tmpdir, "tortoise.db")
        Path(db_path).write_text("fake db")

        target = backup(db_path=db_path, events_path=events_path,
                        target_dir=os.path.join(tmpdir, "backups", "manual"))

        assert target.exists()
        assert (target / "events.jsonl").exists()
        assert (target / "tortoise.db").exists()
        manifest = target / "manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["db"] == "tortoise.db"
        assert data["events"] == "events.jsonl"
        assert "backed_up_at" in data


def test_restore_replays_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create backup
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "test"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        result = restore(str(src), db_path=dst_db, events_path=dst_events)
        assert result["status"] == "ok"
        assert result["events"] == 1
        assert os.path.exists(dst_events)


def test_restore_missing_dir():
    result = restore("/nonexistent/backup", db_path="/tmp/unused.db")
    assert result["status"].startswith("error")


def test_restore_rdb_first_when_snapshot_present(monkeypatch):
    """into_falkor restores via RDB snapshot when present (#114)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "event-only"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')
        # A non-empty RDB stub — restore must use it, not replay JSONL
        (src / "tortoise.db").write_text("rdb-snapshot-data")

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        # Fake a projection whose snapshot has data (count > 0) so the
        # RDB-first path returns without replaying JSONL.
        class _FakeProj:
            def __init__(self, db_path):
                class _G:
                    def query(self, cypher, params=None):
                        class _R:
                            result_set = [[5]]  # noqa: RUF012
                        return _R()
                self.g = _G()
            def close(self):
                pass

        import tortoise.projection as proj_mod
        monkeypatch.setattr(proj_mod, "FalkorProjection", _FakeProj)

        result = restore(str(src), db_path=dst_db, events_path=dst_events, into_falkor=True)
        assert result["status"] == "ok"
        assert result.get("restored_via") == "rdb"
        assert os.path.exists(dst_db)


def test_restore_jsonl_fallback_when_rdb_empty(monkeypatch):
    """into_falkor falls back to JSONL replay when the RDB snapshot is empty (#114)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "event-only"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')
        (src / "tortoise.db").write_text("rdb-snapshot-data")

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        class _EmptyProj:
            def __init__(self, db_path):
                class _G:
                    def query(self, cypher, params=None):
                        class _R:
                            result_set = [[0]]  # noqa: RUF012
                        return _R()
                self.g = _G()
            def apply(self, ev):
                pass
            def close(self):
                pass

        import tortoise.projection as proj_mod
        monkeypatch.setattr(proj_mod, "FalkorProjection", _EmptyProj)

        result = restore(str(src), db_path=dst_db, events_path=dst_events, into_falkor=True)
        assert result["status"] == "ok"
        # Empty RDB → fell through to JSONL replay → no restored_via=rdb
        assert result.get("restored_via") is None


# ── #331: legacy-manifest embedded.db fallback (NameError regression) ──

def test_restore_legacy_manifest_embedded_db_fallback(tmp_path):
    """#331 regression: restore with a legacy manifest (no 'db' key) must
    fall back to embedded.db WITHOUT crashing.

    Pre-fix this path raised NameError inside restore() (logger was used
    before it was defined); the fallback must copy embedded.db to the target.
    """
    src = tmp_path / "backup"
    src.mkdir()
    (src / "events.jsonl").write_text(
        '{"type": "PointAdded", "point": {"id": "p1", "content": "x"}}\n'
    )
    # Legacy manifest: no 'db' key → restore must fall back to embedded.db
    (src / "manifest.json").write_text(
        json.dumps({"backed_up_at": "20260101T000000Z"})
    )
    (src / "embedded.db").write_bytes(b"stub-embedded-db")

    dst = tmp_path / "dst"
    dst.mkdir()
    result = restore(str(src), db_path=str(dst / "tortoise.db"),
                     events_path=str(dst / "events.jsonl"))
    assert result["status"] == "ok"
    assert (dst / "tortoise.db").exists(), \
        "embedded.db fallback must be copied to the target db path"
    assert (dst / "tortoise.db").read_bytes() == b"stub-embedded-db"


# ── #2974: BGSAVE must dial the CONFIGURED DB, and fail loudly ────────────
#
# Regression: ``_bgsave()`` dialed FALKORDB_HOST/FALKORDB_PORT with the
# embedded defaults (localhost:16379), so in hosted production — which sets
# only FALKORDB_CLOUD_URI → TORTOISE_DB_URI — the BGSAVE never reached the
# real instance and the swallowed ``except Exception: pass`` hid it.
# The endpoint now comes from the same canonical resolver the product uses
# (tortoise.projection.resolve_db_endpoint, shared with from_uri).


class _RecordingConnection:
    """Stand-in for falkordb's ``.connection`` (a redis client)."""

    def __init__(self, exc: Exception | None = None):
        self.commands: list[tuple] = []
        self._exc = exc

    def execute_command(self, *args):
        self.commands.append(args)
        if self._exc is not None:
            raise self._exc
        return "Background saving started"


def _install_fake_falkordb(monkeypatch, instances, exc=None):
    """Replace ``falkordb.FalkorDB`` with a recorder so no socket is opened."""

    class _FakeFalkorDB:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connection = _RecordingConnection(exc)
            instances.append(self)

    monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)
    return _FakeFalkorDB


def test_bgsave_dials_the_configured_uri_not_localhost(monkeypatch):
    """#2974: the endpoint is TORTOISE_DB_URI, never the embedded defaults."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    # The embedded defaults the old code used — they must be IGNORED.
    monkeypatch.setenv("FALKORDB_HOST", "localhost")
    monkeypatch.setenv("FALKORDB_PORT", "16379")
    monkeypatch.setenv(
        "TORTOISE_DB_URI",
        "rediss://:s3cret@falkordb-cloud.example.com:6380/tortoise")

    status = _bgsave()

    assert status == "ok"
    assert len(instances) == 1, "exactly one client must be constructed"
    assert instances[0].kwargs == {
        "host": "falkordb-cloud.example.com",
        "port": 6380,
        "username": None,
        "password": "s3cret",
        "ssl": True,
        "socket_connect_timeout": 5,
        "socket_timeout": 10,
    }
    assert instances[0].connection.commands == [("BGSAVE",)]


def test_bgsave_explicit_uri_argument_wins_over_env(monkeypatch):
    """The CLI ``--db`` URI (when it is a URI) is the snapshot target."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://:envpw@env-host.example.com:7000/tortoise")

    status = _bgsave("docker://:pw@cli-host:6379/tortoise")

    assert status == "ok"
    assert instances[0].kwargs["host"] == "cli-host"
    assert instances[0].kwargs["port"] == 6379
    assert instances[0].kwargs["password"] == "pw"


def test_bgsave_connection_failure_is_loud(monkeypatch, caplog):
    """#2974: a failed snapshot is visible — ERROR log + 'failed' status —
    never the old silent ``except Exception: pass``."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances,
                           exc=RuntimeError("connection refused"))
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://cloud.example.com:6379/tortoise")

    with caplog.at_level(logging.ERROR, logger="tortoise.backup"):
        status = _bgsave()

    assert status.startswith("failed:")
    assert "cloud.example.com:6379" in status
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "a BGSAVE connection failure must be logged at ERROR"


def test_bgsave_unsupported_scheme_is_loud(monkeypatch, caplog):
    """A configured-but-unresolvable URI is a misconfiguration, not a skip."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI", "bolt://neo4j.example.com:7687")

    with caplog.at_level(logging.ERROR, logger="tortoise.backup"):
        status = _bgsave()

    assert status.startswith("failed:")
    assert "bolt" in status
    assert instances == [], \
        "must not attempt a connection to an unresolvable endpoint"
    assert [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_bgsave_embedded_mode_skips_without_alarm(monkeypatch, caplog):
    """No configured server URI = embedded mode: skip at INFO, no error."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)

    with caplog.at_level(logging.INFO, logger="tortoise.backup"):
        status = _bgsave()

    assert status.startswith("skipped:")
    assert instances == []
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_backup_manifest_records_bgsave_failure(monkeypatch, tmp_path):
    """#2974: a broken snapshot is visible in the backup artifact itself."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances,
                           exc=RuntimeError("boom"))
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://cloud.example.com:6379/tortoise")

    (tmp_path / "events.jsonl").write_text('{"type": "PointAdded"}\n')
    (tmp_path / "tortoise.db").write_text("stub-db")

    target = backup(db_path=str(tmp_path / "tortoise.db"),
                    events_path=str(tmp_path / "events.jsonl"),
                    target_dir=str(tmp_path / "backups"))

    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["bgsave"].startswith("failed:"), \
        "the manifest must record that the snapshot was NOT taken"


def test_backup_manifest_records_bgsave_ok(monkeypatch, tmp_path):
    """The happy path is recorded too, so 'ok' is never merely assumed."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://cloud.example.com:6379/tortoise")

    (tmp_path / "events.jsonl").write_text('{"type": "PointAdded"}\n')
    (tmp_path / "tortoise.db").write_text("stub-db")

    target = backup(db_path=str(tmp_path / "tortoise.db"),
                    events_path=str(tmp_path / "events.jsonl"),
                    target_dir=str(tmp_path / "backups"))

    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["bgsave"] == "ok"
    assert instances[0].connection.commands == [("BGSAVE",)]


def test_resolve_db_endpoint_is_the_shared_canonical_resolver():
    """#2974: the backup endpoint and from_uri's endpoint derive from ONE
    resolver — they cannot drift."""
    from tortoise.projection import DbEndpoint, resolve_db_endpoint

    endpoint = resolve_db_endpoint(
        "rediss://user:pw@cloud.example.com:6380/team_acme")
    assert endpoint == DbEndpoint(
        host="cloud.example.com",
        port=6380,
        username="user",
        password="pw",
        graph_name="team_acme",
        ssl=True,
    )
    # graph_name override (multi-tenant isolation, #7886) wins over the path
    override = resolve_db_endpoint(
        "docker://:pw@localhost:6379/tortoise", graph_name="team_x")
    assert override.graph_name == "team_x"
    assert override.ssl is False
