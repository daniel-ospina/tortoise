"""Tests for backup — backup/restore round-trip."""
from __future__ import annotations  # noqa: I001

import json
import logging
import os
import tempfile
import traceback
from pathlib import Path

import pytest
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


#: The function under test. ``_bgsave`` is the ONLY place ``tortoise/backup.py``
#: constructs a FalkorDB client (``backup.py:903``), so "was ``_bgsave`` on the
#: stack" is the exact question "did the code under test build this client".
_SUT_FUNC = "_bgsave"


def _install_fake_falkordb(monkeypatch, instances, exc=None):
    """Replace ``falkordb.FalkorDB`` with a recorder so no socket is opened."""

    class _FakeFalkorDB:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connection = _RecordingConnection(exc)
            # WHAT separates "a client the code under test built" from "a
            # client some live worker from an earlier file in this shard
            # built": the recorder is PROCESS-wide (the fake replaces
            # ``falkordb.FalkorDB`` for EVERY constructor in the process), so
            # membership in ``instances`` says nothing about who asked. The
            # CONSTRUCTION STACK does, and it exists only here, so it is
            # recorded here — that is what lets ``_snapshot_client`` restore
            # the count pin #2974 shipped with without reviving the flake.
            frames = traceback.extract_stack()
            self.stack_files = tuple(
                f.filename.replace("\\", "/") for f in frames)
            self.stack_funcs = tuple(f.name for f in frames)
            self.constructed_by_the_code_under_test = _SUT_FUNC in self.stack_funcs
            instances.append(self)

    monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)
    return _FakeFalkorDB


def _snapshot_client(instances):
    """The ONE constructed client that was actually asked to ``BGSAVE``.

    ``instances`` is a RECORDER, not a scope: the fake replaces
    ``falkordb.FalkorDB`` for every constructor in the process, so machinery
    that outlives the test that started it (a live indexer/capture worker from
    an earlier file in the same shard) can build a client INSIDE this test's
    window and land in the list first. Selecting by INDEX therefore asserted
    about the wrong client and reddened the lane order-dependently — measured
    in CI shard ``test (h)`` (run 42046356881, job 127334541115, head
    ``f24717b33``): ``manifest["bgsave"] == "ok"`` while
    ``instances[0].connection.commands == []`` — a client was constructed
    first that this test never asked to snapshot (its captured setup log shows
    the previous file's ``test_auth_…`` projection still live).

    Selecting by the RECORDED COMMAND is what these tests mean: the snapshot
    was issued, exactly once, against the client under test. A foreign client
    is not this test's business in EITHER direction — neither its endpoint nor
    its COMMANDS (see below) — because it is not the code under test.

    ⛔ THE COUNT PIN IS NOT DROPPED, IT MOVED (#11038 review finding 1). The
    assertion this replaced — ``len(instances) == 1`` — answered "how many
    clients exist in this process", which is not the question: it reddened on
    a worker that had nothing to do with the snapshot, AND deleting it was a
    new FALSE PASS in the one pin guarding #2974. A second client built
    against the embedded ``localhost:16379`` defaults — the exact endpoints
    the fix exists to stop the snapshot path from dialing — became invisible,
    because nothing asserted another constructor's ``host``/``port`` at all.
    So the count is asserted over the RIGHT population: the clients whose
    CONSTRUCTION STACK names ``_bgsave``, i.e. the ones the code under test
    actually built.

    A foreign client is required to do NOTHING here — neither its ENDPOINT nor
    its COMMANDS are assertable, and asserting either re-creates the very
    order-dependent flake this helper exists to remove (#11038 review finding
    1, P3, at ``871fc03fc``). The recorder is process-wide, so a live
    ``session_indexer`` worker legitimately dials whatever URI IT was handed
    (``session_indexer.py:236-238`` parses it per call), which in CI is the
    docker lane's own ``localhost`` — and the first thing that worker does
    after connecting is ISSUE A COMMAND on that client. A `cmds == []` sweep
    over every non-subject client therefore reds
    `test_bgsave_dials_the_configured_uri_not_localhost` (and the two other
    callers) with a message that names the endpoint pin while the endpoint was
    never wrong: the #10010-review-P2 false-failure class, in the direction
    this helper was written to eliminate. It also bought no coverage for
    #2974: the endpoint of the client that MATTERS is pinned exactly, by
    kwargs, in each test that calls this, and "the snapshot went to a client
    the code under test built" is asserted by ``subjects[0] is
    snapshotted[0]`` above. That sweep was the only assertion in this helper
    that could fail for a reason outside the code under test.
    """
    recorded = [(i, list(i.connection.commands)) for i in instances]
    snapshotted = [i for i, cmds in recorded if cmds == [("BGSAVE",)]]
    assert len(snapshotted) == 1, (
        "exactly one constructed client must be asked to BGSAVE; recorded "
        f"{[(i.kwargs.get('host'), i.kwargs.get('port'), cmds) for i, cmds in recorded]}")

    subjects = [i for i in instances if i.constructed_by_the_code_under_test]
    assert len(subjects) == 1, (
        "exactly one client must be constructed by the code under test "
        f"(`_bgsave`); it built {len(subjects)}: "
        f"{[(i.kwargs.get('host'), i.kwargs.get('port')) for i in subjects]} — a "
        "second one is a #2974 regression (a fallback to the embedded "
        "``localhost:16379`` defaults that nothing else would notice)")
    assert subjects[0] is snapshotted[0], (
        "the client `_bgsave` constructed is not the one that received the "
        "BGSAVE — the snapshot went to a handle the code under test did not "
        "build, so its endpoint is unpinned")
    return snapshotted[0]


def test_the_restored_count_pin_reds_on_a_second_client_from_the_code_under_test(
        monkeypatch):
    """The count pin has TEETH: a SECOND `_bgsave`-built client must red.

    Non-vacuity for the #11038 review finding 1 fix. The pin is only worth
    having if the shape it guards actually fails: `backup.py` building a
    second client against the embedded ``localhost:16379`` defaults while the
    first one does the snapshot. That is precisely what a #2974 fallback looks
    like, and it is invisible to the recorded-command selector alone because
    the stray client is never asked to BGSAVE.
    """
    instances: list = []
    fake = _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://cloud.example.com:6379/tortoise")
    assert _bgsave() == "ok"
    stray = fake(host="localhost", port=16379)
    assert stray.constructed_by_the_code_under_test is False, (
        "guard: a construction from THIS test's own frame is not a subject — "
        "attribution is by the construction stack, not by list membership")
    # …now make it the regression shape: a second client `_bgsave` built.
    stray.constructed_by_the_code_under_test = True

    with pytest.raises(AssertionError,
                       match="exactly one client must be constructed by"):
        _snapshot_client(instances)


def test_a_foreign_client_issuing_commands_still_leaves_the_pin_green(
        monkeypatch):
    """Non-vacuity for #11038 review finding 1 (P3): a worker must not red it.

    The recorder is process-wide, so a live worker from an earlier file in the
    shard can construct a client INSIDE this test's window and immediately
    issue a command on it. Under the pre-fix `cmds == []` sweep that reddened
    the #2974 endpoint pin with a message naming the endpoint, i.e. a
    false-failure produced outside the code under test. The pin must read the
    client `_bgsave` built and nothing else.
    """
    instances: list = []
    fake = _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://cloud.example.com:6379/tortoise")
    assert _bgsave() == "ok"

    # The foreign worker: its own URI, its own client, the first command any
    # client issues after connecting.
    worker = fake(host="localhost", port=6379)
    worker.connection.execute_command("PING")
    assert worker.connection.commands == [("PING",)]

    client = _snapshot_client(instances)
    assert client.kwargs["host"] == "cloud.example.com", (
        "the endpoint pin must read the client `_bgsave` built, never a "
        "foreign client's")
    assert client.connection.commands == [("BGSAVE",)]


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
    client = _snapshot_client(instances)
    # #3065: the socket pair comes from the product's shared resolver
    # (``projection._bounded_server_socket_kwargs``), not from literals — the
    # 5s/10s pair that used to be pinned here was the pre-#2850 hardcoded
    # values, so ``TORTOISE_DB_SOCKET_TIMEOUT`` / ``..._CONNECT_TIMEOUT`` never
    # reached the snapshot path. The connect bound is therefore the product's
    # 2s (the value #2850 chose to beat a 5s health-check budget), and
    # ``test_bounded_raw_clients_3065.py`` pins the knob override end to end.
    # #2969: the same resolver also pins the RETRY policy, so the configured
    # read bound is not multiplied by redis-py's default (3 retries -> ~4x) on
    # the one client that inherited it. ZERO is the policy: this client builds
    # ``falkordb.FalkorDB``, whose own ``retry=None`` default already meant one
    # attempt, and a single retry would double the snapshot's wait. Popped so
    # the socket pair is pinned as an EXACT dict while the retry policy is
    # asserted on its own (`_retries == 0` below): ``Retry`` and ``NoBackoff``
    # DO compare by value on the pinned 7.4.1 (verified: `Retry(NoBackoff(), 0)
    # == Retry(NoBackoff(), 0)` is True), but ``Retry.__eq__`` also spans the
    # ``supported_errors`` set, so folding it into the dict would couple this
    # pin to a vendor default that has nothing to do with #2969.
    kwargs = dict(client.kwargs)
    retry = kwargs.pop("retry")
    assert kwargs == {
        "host": "falkordb-cloud.example.com",
        "port": 6380,
        "username": None,
        "password": "s3cret",
        "ssl": True,
        "socket_connect_timeout": 2.0,
        "socket_timeout": 10.0,
    }
    assert retry._retries == 0, (
        "the snapshot client must not multiply the configured bound — one "
        "retry would hold a wedged BGSAVE for 2 x the bound instead of 1 x "
        "(#2969)")
    assert retry._backoff.__class__.__name__ == "NoBackoff", (
        "the pin is the `Retry(NoBackoff(), 0)` idiom, not zero retries "
        "with some other backoff (#2969)")
    assert client.connection.commands == [("BGSAVE",)]


def test_bgsave_explicit_uri_argument_wins_over_env(monkeypatch):
    """The CLI ``--db`` URI (when it is a URI) is the snapshot target."""
    instances: list = []
    _install_fake_falkordb(monkeypatch, instances)
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "redis://:envpw@env-host.example.com:7000/tortoise")

    status = _bgsave("docker://:pw@cli-host:6379/tortoise")

    assert status == "ok"
    client = _snapshot_client(instances)
    assert client.kwargs["host"] == "cli-host"
    assert client.kwargs["port"] == 6379
    assert client.kwargs["password"] == "pw"


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
    client = _snapshot_client(instances)
    assert client.connection.commands == [("BGSAVE",)], (
        "the manifest's 'ok' must be EVIDENCE: exactly one BGSAVE reached a "
        "constructed client — never merely assumed")


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
