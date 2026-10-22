"""#9557 (R9) — the record's backup exists, and the artifact is PROVEN restorable.

`docs/durability-posture.md` §3 / §14.1 **O1** rules *durability = the store's
backup*. Promoting Postgres to the record moved that obligation onto Postgres,
and until `tortoise/record_backup.py` **no module of ours held it**: every
archiving module in the tree copies the *graph*. The defect class this file
guards is therefore not "the code does not work" but **"the machinery looks
present, for the wrong store"** — a backup that produces an artifact nobody can
restore, over a table set that is not the record's, through a connection the
vendor routes elsewhere.

Each test below is a REFUSAL or a MEASURED OUTCOME that the posture depends on,
and every one of them is a falsifier away from being vacuous:

* the artifact covers **all nine** record tables (a subset is the #3895 failure:
  an artifact unrestorable *by construction* while every check reads green);
* the **credential never reaches argv** (argv is world-readable via `ps`, and an
  error that quotes argv quotes the record's password into the logs);
* a **pooler DSN is refused** before any bytes move (a transaction pooler
  discards session state between statements, so a dump through it can be
  silently short — the worst possible backup);
* a **failed or empty dump uploads nothing** (an empty artifact suppresses the
  operator's own backup while reporting health);
* a **tampered artifact**, and an artifact whose **table set differs**, are
  refused before `pg_restore` is invoked;
* a restore into a **populated** target is refused (a restore into a live record
  is not a drill);
* a **drill over an empty record** is refused (every count matches when both
  sides are zero — a vacuous green);
* a **row-count mismatch NAMES the table** (`#9567` D8's own falsifier: delete
  one table's rows from the artifact and the drill must red *with the table
  named*);
* the **drill end to end** takes the artifact through upload → download →
  decrypt → restore and reports per-table counts (the "1 executed restore drill
  with a measured row count" target).

DB-free on purpose: `pg_dump`/`pg_restore` are driven through an injected runner
and the counts through an injected connector, so this runs in the ordinary lanes
and cannot be skipped for want of a database. What it proves is the *writer,
the refusals and the comparison*; the real drill against a live project is an
operational step (`docs/infra-runbook.md`), and this file does not claim to be
it.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise import record_backup as rb
from tortoise.hosted_backup import MemoryStorage, decrypt_backup
from tortoise.record_projection import THE_RECORD_TABLES

KEY = bytes(range(32))
DSN = "postgresql://record_user:s3cr3t-pw@db.projectref.supabase.co:5432/postgres"
SCRATCH_DSN = "postgresql://record_user:s3cr3t-pw@db.scratchref.supabase.co:5432/postgres"


# ── doubles ────────────────────────────────────────────────────────────────


class FakeProc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


class FakeRunner:
    """Stands in for `subprocess.run` at the pg_dump / pg_restore boundary.

    ``on_restore`` fires INSTEAD of writing a database, which is how the drill
    test makes the scratch side start empty and come back populated: the fake
    connector reads that state, so the counts in the report are produced by the
    same code path a live drill would use.
    """

    def __init__(
        self,
        *,
        dump_bytes: bytes = b"PGDUMP-CUSTOM-FORMAT",
        dump_returncode: int = 0,
        restore_returncode: int = 0,
        on_restore=None,
        write_no_file: bool = False,
    ) -> None:
        self.dump_bytes = dump_bytes
        self.dump_returncode = dump_returncode
        self.restore_returncode = restore_returncode
        self.on_restore = on_restore
        self.write_no_file = write_no_file
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[0].endswith("pg_dump"):
            if not self.write_no_file and self.dump_returncode == 0:
                Path(argv[argv.index("--file") + 1]).write_bytes(self.dump_bytes)
            return FakeProc(self.dump_returncode, "pg_dump: error: connection refused")
        if self.on_restore is not None and self.restore_returncode == 0:
            self.on_restore()
        return FakeProc(self.restore_returncode, "pg_restore: error: could not execute")

    def argv_for(self, tool: str) -> list[str]:
        for argv, _ in self.calls:
            if argv[0] == tool:
                return argv
        raise AssertionError(f"{tool} was never invoked: {[c[0][0] for c in self.calls]}")


class FakeCursor:
    def __init__(self, conn) -> None:
        self._conn = conn
        self._value = None

    def execute(self, sql, params=None):
        if "count(*)" in sql:
            table = sql.split('"')[1]
            self._value = self._conn.counts[table]
        elif sql.upper().startswith("TRUNCATE"):
            # The scratch reset: every record table in ONE statement, exactly as
            # the module emits it — so a reset that silently emptied one table
            # (or every table) is visible in the assertion, not assumed.
            for table in self._conn.counts:
                self._conn.counts[table] = 0
            self._value = None
        else:  # set_config
            self._value = None
        return None

    def fetchone(self):
        return (self._value,)

    def close(self) -> None:
        pass


class FakeConn:
    def __init__(self, dsn: str, counts: dict[str, int]) -> None:
        self.dsn = dsn
        self.counts = counts
        self.closed = False
        self.rolled_back = False
        self.committed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


class FakeDB:
    """Two per-DSN count maps; `connect()` hands out a connection per DSN."""

    def __init__(self, source: dict[str, int], scratch: dict[str, int] | None = None) -> None:
        self.source = dict(source)
        self.scratch = dict(scratch or dict.fromkeys(THE_RECORD_TABLES, 0))
        self.connections: list[FakeConn] = []

    def connect(self, dsn: str) -> FakeConn:
        counts = self.source if dsn == DSN else self.scratch
        conn = FakeConn(dsn, counts)
        self.connections.append(conn)
        return conn

    def populate_scratch_from_source(self) -> None:
        self.scratch.update(self.source)


SOURCE_COUNTS = {
    "node_identity": 41,
    "journal_event": 512,
    "source": 7,
    "event": 0,
    "session": 3,
    "object": 19,
    "subject": 11,
    "point": 24,
    "relation": 63,
}


def _dump_manifest(db: FakeDB | None = None, *, runner=None, storage=None):
    db = db or FakeDB(SOURCE_COUNTS)
    storage = storage or MemoryStorage()
    runner = runner or FakeRunner()
    manifest = rb.create_record_backup(
        DSN, storage, run=runner, connect=db.connect, key=KEY
    )
    return manifest, storage, runner, db


# ── the artifact covers the record, and only the record ────────────────────


def test_the_dump_covers_every_record_table():
    """A subset artifact is unrestorable *by construction* — #3895's own defect.

    The table set is not a caller parameter, so this cannot regress by a caller
    passing six of the nine; it regresses only if the module stops reading
    `THE_RECORD_TABLES`, and then this fails.
    """
    runner = FakeRunner()
    db = FakeDB(SOURCE_COUNTS)
    rb.create_record_backup(DSN, MemoryStorage(), run=runner, connect=db.connect, key=KEY)
    argv = runner.argv_for("pg_dump")
    dumped = sorted(a.split(".", 1)[1] for a in argv if a.startswith("--table=public."))
    assert dumped == sorted(THE_RECORD_TABLES)
    assert len(dumped) == 9
    # Portable into a DIFFERENT project: ownership and role grants do not exist
    # there, and pg_restore would fail on them halfway through.
    assert "--no-owner" in argv
    assert "--no-privileges" in argv
    assert "--format=custom" in argv
    # DATA-only: the schema's one home is the migrations (a schema-carrying
    # artifact either fights a migrated target — measured, 2026-10-22: pg_restore
    # refused `relation "public.node_identity" already exists` — or silently
    # installs a superseded schema).
    assert "--data-only" in argv


def test_the_password_never_reaches_argv_and_the_dsn_is_not_positional():
    """argv is public (`ps`, crash reports) and gets quoted into error text."""
    runner = FakeRunner()
    db = FakeDB(SOURCE_COUNTS)
    rb.create_record_backup(DSN, MemoryStorage(), run=runner, connect=db.connect, key=KEY)
    argv, kwargs = runner.calls[0]
    assert not any("s3cr3t-pw" in part for part in argv)
    assert not any(part.startswith("postgres") for part in argv)
    # The coordinates it DOES need are in the environment libpq reads.
    assert kwargs["env"]["PGPASSWORD"] == "s3cr3t-pw"
    assert kwargs["env"]["PGHOST"] == "db.projectref.supabase.co"
    assert kwargs["env"]["PGDATABASE"] == "postgres"


def test_the_ambient_libpq_environment_never_reaches_the_dump(monkeypatch):
    """#9601 review cycle 2 (P2): the allow-list is PINNED, not just described.

    `_subprocess_env` was `{**os.environ, **libpq_env(dsn)}` until cycle 1, so
    every libpq variable the DSN did not name was inherited from whatever shell
    started the backup. `PGDATABASE` alone is enough to dump a **different
    database than the manifest describes** — and the manifest is the map from the
    artifact back to the database it must be compared with, so the drill would
    verify one database's artifact against another's counts. `PGSERVICE` and
    `PGPASSFILE` reach a different server with different credentials; a stale
    `PGOPTIONS` silently changes the session.

    `test_the_password_never_reaches_argv_...` cannot catch a revert to the old
    shape: it only asserts the DSN's own coordinates are PRESENT, which is true
    either way. This one sets every dangerous variable to a value the DSN does
    NOT carry and asserts the env the DUMP passed is the DSN's — so restoring
    `{**os.environ, ...}` reds it.
    """
    for ambient in (
        "PGDATABASE", "PGUSER", "PGPASSWORD", "PGSERVICE", "PGOPTIONS",
        "PGSSLMODE", "PGPASSFILE", "PGHOST", "PGPORT",
    ):
        monkeypatch.setenv(ambient, "bogus-from-the-shell")
    monkeypatch.setenv("PATH", "/usr/bin")
    runner = FakeRunner()
    db = FakeDB(SOURCE_COUNTS)
    rb.create_record_backup(DSN, MemoryStorage(), run=runner, connect=db.connect, key=KEY)
    _argv, kwargs = runner.calls[0]
    env = kwargs["env"]
    # The DSN's coordinates win, and an inherited value cannot stand in for one
    # the DSN does not name.
    assert env["PGHOST"] == "db.projectref.supabase.co"
    assert env["PGPORT"] == "5432"
    assert env["PGUSER"] == "record_user"
    assert env["PGPASSWORD"] == "s3cr3t-pw"
    assert env["PGDATABASE"] == "postgres"
    for unnameable in ("PGSERVICE", "PGOPTIONS", "PGSSLMODE", "PGPASSFILE"):
        assert unnameable not in env, (
            f"{unnameable} was inherited from the ambient environment — a dump "
            "must reach the database its DSN names, not the one a shell variable names")
    # The minimum a client needs to RUN is still passed through.
    assert env["PATH"] == "/usr/bin"


def test_the_manifest_records_the_table_set_the_ciphertext_hash_and_no_credentials():
    manifest, storage, _runner, _db = _dump_manifest()
    assert manifest["format"] == rb.RECORD_DUMP_FORMAT
    assert sorted(manifest["tables"]) == sorted(THE_RECORD_TABLES)
    assert manifest["row_total"] == sum(SOURCE_COUNTS.values())
    assert manifest["row_counts"]["point"] == 24
    dumped = json.dumps(manifest)
    assert "s3cr3t-pw" not in dumped
    assert manifest["source"]["host"] == "db.projectref.supabase.co"
    # The stored object is the encrypted blob; its sha256 is what a restore
    # re-checks, and it is the CIPHERTEXT's (a plaintext hash cannot verify a
    # downloaded object).
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    blob = storage.download(key)
    assert decrypt_backup(blob, key=KEY) == b"PGDUMP-CUSTOM-FORMAT"
    assert manifest["sha256"] != manifest["plaintext_sha256"]
    table_keys = sorted(k for k in storage.list("") if k.endswith(rb.MANIFEST_OBJECT))
    assert table_keys == [f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.MANIFEST_OBJECT}"]


# ── the refusals: a backup that cannot be trusted must not be published ────


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres",
        "postgresql://u:p@aws-0-us-east-1.pooler.supabase.co:6543/postgres",
        "postgresql://u:p@db.ref.supabase.co:6543/postgres",
    ],
)
def test_a_pooler_dsn_is_refused_before_anything_is_dumped(dsn):
    runner = FakeRunner()
    with pytest.raises(rb.RecordBackupUnavailable, match=r"DIRECT|POOLER|transaction-mode"):
        rb.dump_record(dsn, "/tmp/should-not-exist.dump", run=runner)
    assert runner.calls == []


def test_the_pooler_refusal_is_a_configuration_fault_not_a_retryable_failure():
    """The headline refusal of this PR must not report itself as "retry".

    `require_direct_dsn`'s message is the fix (a second, direct DSN) — a
    scheduler that reads the refusal as `1` retries a permanent config fault
    forever, so the CLASS is the contract here, not just the sentence.
    """
    pooler = "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    with pytest.raises(rb.RecordBackupUnavailable):
        rb.require_direct_dsn(pooler)
    # Still a RecordBackupError (a caller that only wants "the backup did not
    # happen" keeps working), but NOT a refusal — the two are different codes.
    assert issubclass(rb.RecordBackupUnavailable, rb.RecordBackupError)
    assert not issubclass(rb.RecordBackupUnavailable, rb.RecordRestoreRefused)
    # A direct DSN is still accepted unchanged.
    assert rb.require_direct_dsn(DSN) == DSN


def test_a_missing_psycopg2_is_a_configuration_fault(monkeypatch):
    """`psycopg2` absent is an install fault (exit 2), not a backup failure."""
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    with pytest.raises(rb.RecordBackupUnavailable, match="psycopg2 is not installed"):
        rb._default_connector(DSN)


def test_a_failed_dump_raises_and_publishes_nothing():
    runner = FakeRunner(dump_returncode=1)
    storage = MemoryStorage()
    with pytest.raises(rb.RecordBackupError, match="pg_dump exited 1"):
        rb.create_record_backup(
            DSN, storage, run=runner, connect=FakeDB(SOURCE_COUNTS).connect, key=KEY
        )
    assert storage.list("") == []


def test_an_empty_dump_is_refused_rather_than_published():
    """An empty artifact is worse than no backup: it looks like one."""
    runner = FakeRunner(dump_bytes=b"")
    storage = MemoryStorage()
    with pytest.raises(rb.RecordBackupError, match="EMPTY"):
        rb.create_record_backup(
            DSN, storage, run=runner, connect=FakeDB(SOURCE_COUNTS).connect, key=KEY
        )
    assert storage.list("") == []


def test_a_missing_backup_key_is_a_configuration_fault_not_a_crash():
    """No key must be exit 2 (unconfigured), never a traceback read as "retry".

    Two things are pinned: the exception CLASS the verb maps to `2` (a plain
    `RecordBackupError` is `1` = "retry", which a keyless box retries forever),
    and that it fires BEFORE the dump — a key fault discovered after `pg_dump`
    writes the system of record to a temp file leaks a plaintext dump per attempt.
    """
    db = FakeDB(SOURCE_COUNTS)
    storage = MemoryStorage()
    runner = FakeRunner()
    with pytest.raises(rb.RecordBackupUnavailable, match="backup key is not configured"):
        rb.create_record_backup(
            DSN, storage, run=runner, connect=db.connect, key=None
        )
    assert runner.calls == []  # nothing was dumped
    assert db.connections == []  # nor counted
    assert storage.list("") == []


def test_a_dump_that_writes_no_file_is_refused():
    runner = FakeRunner(write_no_file=True)
    with pytest.raises(rb.RecordBackupError, match="wrote no artifact"):
        rb.create_record_backup(
            DSN, MemoryStorage(), run=runner, connect=FakeDB(SOURCE_COUNTS).connect, key=KEY
        )


def test_a_failed_cleanup_of_the_plaintext_dump_is_LOGGED_and_does_not_fail_the_backup(
    monkeypatch, caplog
):
    """`ignore_errors=True` made a failed removal silent — in the one path about plaintext.

    The dump in that directory is the system of record in the clear, so a removal
    that did not happen must leave evidence. It must still not FAIL the run: by
    then the artifact is uploaded and durable, and exiting 1 would say "the
    record is NOT backed up" — false.
    """
    import shutil as _shutil

    def _boom(path, *args, **kwargs):
        raise PermissionError(f"cannot remove {path}")

    # `context()` so the patch is gone before this test's tmpdir teardown (which
    # removes the same tree the backup just failed to clean).
    storage = MemoryStorage()
    with monkeypatch.context() as patch:
        patch.setattr(_shutil, "rmtree", _boom)
        with caplog.at_level("WARNING", logger=rb.LOG_NAME):
            manifest = rb.create_record_backup(
                DSN, storage, run=FakeRunner(), connect=FakeDB(SOURCE_COUNTS).connect, key=KEY
            )
    # The backup is published: a cleanup fault is not a backup fault.
    assert storage.list(rb.RECORD_BACKUP_PREFIX) != []
    assert manifest["row_total"] == sum(SOURCE_COUNTS.values())
    # ...and the failure is not silent.
    assert any("plaintext" in rec.message for rec in caplog.records)


# ── restore: verified before a byte lands, refused on a live target ────────


def _restore(manifest, storage, *, runner, db, dsn=SCRATCH_DSN):
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    return rb.restore_record_backup(
        storage, key, dsn, run=runner, connect=db.connect, key=KEY
    )


def test_restore_refuses_a_tampered_artifact_before_pg_restore_runs():
    manifest, storage, _r, _db = _dump_manifest()
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    blob = bytearray(storage.download(key))
    blob[-1] ^= 0xFF
    storage.upload(key, bytes(blob))
    runner = FakeRunner()
    with pytest.raises(rb.RecordRestoreRefused, match="sha256"):
        _restore(manifest, storage, runner=runner, db=FakeDB(SOURCE_COUNTS))
    assert runner.calls == []


def test_restore_refuses_an_artifact_encrypted_under_a_different_key():
    """A wrong key is a REFUSAL, not a transport error a scheduler retries."""
    manifest, storage, _r, _db = _dump_manifest()
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    runner = FakeRunner()
    with pytest.raises(rb.RecordRestoreRefused, match="could not be decrypted"):
        rb.restore_record_backup(
            storage, key, SCRATCH_DSN, run=runner,
            connect=FakeDB(SOURCE_COUNTS).connect, key=bytes(32),
        )
    assert runner.calls == []


def test_restore_refuses_an_artifact_whose_table_set_is_not_the_record():
    """A partial artifact must not restore into a green-looking half-record."""
    manifest, storage, _r, _db = _dump_manifest()
    manifest_key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.MANIFEST_OBJECT}"
    patched = dict(manifest)
    patched["tables"] = [t for t in THE_RECORD_TABLES if t != "relation"]
    patched["sha256"] = manifest["sha256"]  # integrity still intact; SHAPE is wrong
    storage.upload(manifest_key, json.dumps(patched).encode())
    runner = FakeRunner()
    with pytest.raises(rb.RecordRestoreRefused, match="table set"):
        _restore(manifest, storage, runner=runner, db=FakeDB(SOURCE_COUNTS))
    assert runner.calls == []


def test_the_manifest_pins_every_record_table_column_set():
    """The SCHEMA pin: an artifact written against an older schema must refuse.

    The artifact is data-only, so a drifted one would either fail mid-COPY or
    land its values in the wrong columns. The pin is read from the migrations
    (`table_columns()`), not restated, so it cannot agree with itself while
    disagreeing with the schema a target actually has.
    """
    manifest, _storage, _runner, _db = _dump_manifest()
    from tortoise.record_projection import table_columns

    parsed = table_columns()
    assert sorted(manifest["columns"]) == sorted(THE_RECORD_TABLES)
    for table, cols in manifest["columns"].items():
        assert cols == sorted(parsed[table])
        assert cols, f"{table} pinned an EMPTY column set — that pin proves nothing"


def test_restore_refuses_an_artifact_written_against_a_different_schema():
    manifest, storage, _r, _db = _dump_manifest()
    manifest_key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.MANIFEST_OBJECT}"
    patched = dict(manifest)
    columns = {t: list(c) for t, c in manifest["columns"].items()}
    columns["point"] = [c for c in columns["point"] if c != "content"]
    patched["columns"] = columns
    patched["sha256"] = manifest["sha256"]  # integrity intact; the SCHEMA drift is the defect
    storage.upload(manifest_key, json.dumps(patched).encode())
    runner = FakeRunner()
    with pytest.raises(rb.RecordRestoreRefused, match="DIFFERENT record schema"):
        _restore(manifest, storage, runner=runner, db=FakeDB(SOURCE_COUNTS))
    assert runner.calls == []


def test_a_target_without_the_record_schema_is_a_named_refusal():
    """The artifact is DATA-only: a target with no migrations must say so."""

    class _NoSchema(FakeDB):
        def connect(self, dsn):
            conn = super().connect(dsn)
            cur_factory = conn.cursor

            def _cursor():
                cur = cur_factory()
                original = cur.execute

                def _execute(sql, params=None):
                    if "count(*)" in sql:
                        raise RuntimeError('relation "public.point" does not exist')
                    return original(sql, params)

                cur.execute = _execute
                return cur

            conn.cursor = _cursor
            return conn

    with pytest.raises(rb.RecordBackupError, match="schema applied on this target"):
        rb.record_row_counts(DSN, connect=_NoSchema(SOURCE_COUNTS).connect)


def test_restore_into_a_populated_record_is_refused():
    """A restore into a live record is not a drill — refuse while harmless."""
    manifest, storage, _r, _db = _dump_manifest()
    populated = dict(SOURCE_COUNTS)
    runner = FakeRunner()
    with pytest.raises(rb.RecordRestoreRefused, match="NOT empty"):
        _restore(manifest, storage, runner=runner, db=FakeDB(SOURCE_COUNTS, populated))
    assert runner.calls == []


def test_a_failed_pg_restore_is_reported_as_a_failure_not_a_verdict():
    manifest, storage, _r, _db = _dump_manifest()
    runner = FakeRunner(restore_returncode=1)
    with pytest.raises(rb.RecordBackupError, match="did NOT restore"):
        _restore(manifest, storage, runner=runner, db=FakeDB(SOURCE_COUNTS))


# ── the comparison, and the falsifier #9567 D8 names ───────────────────────


def test_a_row_count_mismatch_names_the_table():
    """D8's falsifier: drop one table's rows → the drill reds WITH THE TABLE NAMED."""
    restored = dict(SOURCE_COUNTS)
    restored["relation"] = 0
    with pytest.raises(rb.RecordRestoreVerifyError) as excinfo:
        rb.verify_restore(SOURCE_COUNTS, restored)
    assert "relation" in str(excinfo.value)
    assert [m["table"] for m in excinfo.value.mismatches] == ["relation"]
    assert excinfo.value.mismatches[0]["restored"] == 0


def test_the_comparison_is_per_table_never_a_total():
    """A total can reconcile while a whole table is gone and another doubled.

    BOTH tables are named — the doubled one is a divergence too, not a rounding
    difference that offset the loss. Reading only the sum would have reported
    this restore as clean.
    """
    restored = dict(SOURCE_COUNTS)
    restored["relation"] = 0
    restored["journal_event"] = SOURCE_COUNTS["journal_event"] + SOURCE_COUNTS["relation"]
    report = rb.compare_row_counts(SOURCE_COUNTS, restored)
    assert sum(v["restored"] for v in report["tables"].values()) == sum(SOURCE_COUNTS.values())
    assert report["equal"] is False
    assert [m["table"] for m in report["mismatches"]] == ["journal_event", "relation"]


# ── the drill ──────────────────────────────────────────────────────────────


def test_the_drill_restores_the_artifact_and_reports_a_measured_row_count():
    db = FakeDB(SOURCE_COUNTS)
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    storage = MemoryStorage()
    report = rb.run_restore_drill(
        DSN, storage, SCRATCH_DSN, run=runner, connect=db.connect, key=KEY
    )
    assert report["verdict"] == "RESTORED"
    assert report["row_total"] == sum(SOURCE_COUNTS.values())
    assert report["tables"]["point"] == {"source": 24, "source_after": 24, "restored": 24}
    assert report["tables"]["relation"] == {"source": 63, "source_after": 63, "restored": 63}
    # The artifact is a persisted object, not an in-memory handoff: the drill
    # reads it back out of storage the way an operator's restore would.
    assert storage.list(f"{rb.RECORD_BACKUP_PREFIX}{report['backup_id']}/") != []
    # The target ended populated and was refused nothing — it started empty.
    assert db.scratch == SOURCE_COUNTS


def test_the_drill_refuses_a_scratch_target_that_is_the_record_itself():
    db = FakeDB(SOURCE_COUNTS)
    with pytest.raises(rb.RecordRestoreRefused,
                       match="resolves to the SAME database"):
        rb.run_restore_drill(
            DSN, MemoryStorage(), DSN, run=FakeRunner(), connect=db.connect, key=KEY
        )


@pytest.mark.parametrize("spelling", [
    # Every one of these is the SAME database as DSN, spelled differently.
    DSN.replace("postgresql://", "postgres://"),
    DSN.replace(":5432/", "/"),
    DSN + "?sslmode=require",
    DSN + "/",
])
def test_the_drill_refuses_an_EQUIVALENT_scratch_spelling_and_never_resets(
        spelling):
    """#9601 review cycle 1 (P0): the identity test is COORDINATES, not a string.

    `dsn == scratch_dsn` missed every equivalent spelling of one database, and
    the reset ran BEFORE the dump — so a drill pointed at the live record through
    any of them TRUNCATEd it, dumped the empty result, and verified zeros against
    zeros with exit 0. This pins both halves: the refusal fires, and NO reset and
    NO dump reached the source (the fake records nothing it was not asked for).
    """
    db = FakeDB(SOURCE_COUNTS)
    with pytest.raises(rb.RecordRestoreRefused,
                       match="resolves to the SAME database"):
        rb.run_restore_drill(
            DSN, MemoryStorage(), spelling, run=FakeRunner(),
            connect=db.connect, key=KEY, reset_scratch=True,
        )
    assert db.connections == [], (
        "the drill connected after refusing its scratch target as the source — "
        "a reset or a dump would have followed")
    assert db.scratch == dict.fromkeys(THE_RECORD_TABLES, 0), (
        "the drill touched its scratch target after refusing it as the source")


def test_the_drill_refuses_a_scratch_target_reached_by_another_NAME():
    """#9601 review cycle 3 (P1): coordinate equality was still LEXICAL on the host.

    `localhost` and `127.0.0.1` are ONE server with two names, so a scratch DSN
    spelled with the other name passed the "resolves to the SAME database"
    guard — and with `--reset-scratch` the drill then TRUNCATEed the record's
    nine tables, restored the artifact over it, and printed `RESTORED`. The
    guard compares the ADDRESSES both names resolve to now, so an alias cannot
    slip past it. FakeDB records every connection, and the assertion below is
    what proves the refusal happened BEFORE anything touched the source.
    """
    loopback = "postgresql://record_user:s3cr3t-pw@localhost:5432/postgres"
    alias = "postgresql://record_user:s3cr3t-pw@127.0.0.1:5432/postgres"
    db = FakeDB(SOURCE_COUNTS)
    with pytest.raises(rb.RecordRestoreRefused,
                       match="resolves to the SAME database"):
        rb.run_restore_drill(
            loopback, MemoryStorage(), alias, run=FakeRunner(),
            connect=db.connect, key=KEY, reset_scratch=True,
        )
    assert db.connections == [], (
        "the drill connected after refusing an aliased scratch target — a reset "
        "or a dump would have followed")


def test_the_drill_refuses_an_empty_record_because_the_pass_would_be_vacuous():
    """An empty record is a REFUSAL (no verdict) — never a data-loss mismatch.

    Exit 3 means "the drill ran and the restored record disagrees with its
    source", and under `--json` that path prints `{"verdict": "MISMATCH",
    "mismatches": []}` — a loss verdict naming no loss, on a run that never
    touched `pg_restore`. Pointing the drill at an empty record (wrong database,
    a fresh environment) is a misconfiguration, so it must be the refusal class
    the verb maps to "could not run".
    """
    empty = dict.fromkeys(THE_RECORD_TABLES, 0)
    db = FakeDB(empty)
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    with pytest.raises(rb.RecordRestoreRefused, match="EMPTY") as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN, run=runner, connect=db.connect, key=KEY
        )
    assert not isinstance(excinfo.value, rb.RecordRestoreVerifyError)
    # and it refused BEFORE taking a backup of nothing
    assert runner.calls == []


def test_the_drill_tolerates_rows_written_after_the_dump_and_names_the_window():
    """The record is LIVE: a row committed after the dump is not a lost row.

    The artifact is cut from a snapshot taken after the manifest's count, so
    `journal_event` legitimately holds 512 in the artifact while the source had
    reached 515 by the end of the dump. Comparing the pre-dump count exactly —
    the pre-fix behaviour — reds this healthy backup, and the documented daily
    schedule runs exactly this against the live record.
    """
    artifact_counts = dict(SOURCE_COUNTS)
    db = FakeDB(SOURCE_COUNTS)
    reads = {"source": 0}
    real_connect = db.connect

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            # 1 = the vacuity count, 2 = the manifest count (the artifact's own
            # numbers), 3 = the post-dump probe. Rows land between 2 and 3.
            if reads["source"] >= 3:
                db.source = {**db.source, "journal_event": 515}
        return real_connect(dsn)

    def restore():
        # The artifact carries the rows that existed AT DUMP TIME, not the three
        # that landed afterwards.
        db.scratch.update(artifact_counts)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=restore), connect=connect, key=KEY,
    )
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 515, "restored": 512,
    }
    # Every other table did not move, and reports so.
    assert report["tables"]["point"] == {"source": 24, "source_after": 24, "restored": 24}


def test_the_drill_does_not_red_when_a_row_lands_between_the_two_source_reads():
    """#9601 review cycle 2 (P1): the manifest's count is read AFTER `before`.

    `run_restore_drill` reads the source at `before`, and `create_record_backup`
    reads it again for the manifest's `row_counts` — a LATER read. On a live
    append-only record (a row lands on every capture turn) the second read is
    legitimately the LARGER number, so comparing `claimed` against `before` red a
    perfectly healthy backup: exit 3, `"verdict": "MISMATCH"` with no
    `mismatches`, on the exact schedule `docs/infra-runbook.md` documents, on a
    run whose restore had not started. The sibling test above places the
    concurrent row at the THIRD read, which is the one placement that never
    exercises that guard; this one places it at the second.
    """
    db = FakeDB(SOURCE_COUNTS)
    reads = {"source": 0}
    real_connect = db.connect
    # The row is committed BEFORE the read it precedes, so the manifest's own
    # count (read #2, inside `create_record_backup`) already sees it — and so does
    # the dump, and so does the post-dump ceiling (read #3).
    artifact_counts = {**SOURCE_COUNTS, "journal_event": 515}

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            if reads["source"] >= 2:
                db.source = dict(artifact_counts)
        return real_connect(dsn)

    def restore():
        db.scratch.update(artifact_counts)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=restore), connect=connect, key=KEY,
    )
    assert reads["source"] >= 2, "the concurrent row must land before the manifest count"
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 515, "restored": 515,
    }


def test_an_artifact_claiming_rows_the_source_never_held_is_a_REFUSAL(monkeypatch):
    """The cross-check still bites — as a REFUSAL, never as a mismatch verdict.

    The check is one-sided on purpose (see the test above): an artifact may not
    claim more rows than the source held once the dump finished, because it
    cannot carry rows that never existed. That is a disagreement between the
    artifact and the record, so NOTHING is restored and no verdict is produced:
    exit 2, not exit 3 — a `RecordRestoreVerifyError` here would print
    `{"verdict": "MISMATCH", "mismatches": []}`, a loss verdict naming no loss.
    """
    db = FakeDB(SOURCE_COUNTS)
    real = rb.create_record_backup

    def overclaiming(*args, **kwargs):
        manifest = real(*args, **kwargs)
        manifest["row_counts"] = {**manifest["row_counts"], "journal_event": 10_000}
        return manifest

    monkeypatch.setattr(rb, "create_record_backup", overclaiming)
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    with pytest.raises(rb.RecordRestoreRefused, match="never held") as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN, run=runner, connect=db.connect, key=KEY,
        )
    assert not isinstance(excinfo.value, rb.RecordRestoreVerifyError)
    # No restore ran, and the scratch target was not touched.
    assert [argv[0] for argv, _kw in runner.calls] == ["pg_dump"]
    assert db.scratch == dict.fromkeys(THE_RECORD_TABLES, 0)


def test_a_NET_DELETION_during_the_dump_is_not_an_impossible_artifact():
    """#9601 review cycle 3 (P1): the record is NOT append-only.

    `record_projection` emits `DELETE` for `delete`/`merge` journal ops and the
    erasure RPC removes all nine tables, so a NET DELETION inside the dump window
    leaves the post-dump re-count BELOW the artifact's own count on a healthy
    backup. A ceiling of `after` alone read that as an impossible artifact and
    refused with exit 2 — a false red on the daily drill, and one that also
    stopped retention, because the prune runs only on exit 0. The ceiling is the
    larger of the two source reads, so a legitimate deletion cannot red it.
    """
    db = FakeDB(SOURCE_COUNTS)
    real_connect = db.connect
    reads = {"source": 0}
    # The artifact carries 512 journal rows; by the time the ceiling is read the
    # record has aged 12 of them out.
    after_counts = {**SOURCE_COUNTS, "journal_event": 500}

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            if reads["source"] >= 3:
                db.source = dict(after_counts)
        return real_connect(dsn)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=lambda: db.scratch.update(SOURCE_COUNTS)),
        connect=connect, key=KEY,
    )
    assert reads["source"] >= 3, "the deletion must land before the ceiling read"
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 500, "restored": 512,
    }


def test_a_lost_table_is_STILL_red_on_a_record_that_moved():
    """The tolerance is a window, not a licence: loss is still named."""
    db = FakeDB(SOURCE_COUNTS)
    reads = {"n": 0}
    real_connect = db.connect

    def connect(dsn):
        if dsn == DSN:
            reads["n"] += 1
            if reads["n"] >= 3:
                db.source = {**db.source, "journal_event": 515}
        return real_connect(dsn)

    def lose_one_table():
        db.scratch.update(dict.fromkeys(SOURCE_COUNTS, 0))

    with pytest.raises(rb.RecordRestoreVerifyError) as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN,
            run=FakeRunner(on_restore=lose_one_table), connect=connect, key=KEY,
        )
    assert next(m["table"] for m in excinfo.value.mismatches) == "node_identity"
    assert "source 41" in str(excinfo.value)


def test_a_restore_carrying_rows_the_source_never_had_is_red():
    """Above the ceiling is a divergence too — duplication is not growth."""
    with pytest.raises(rb.RecordRestoreVerifyError) as excinfo:
        rb.verify_restore(
            SOURCE_COUNTS,
            {**SOURCE_COUNTS, "point": SOURCE_COUNTS["point"] + 5},
            source_after={**SOURCE_COUNTS, "point": SOURCE_COUNTS["point"] + 1},
        )
    assert [m["table"] for m in excinfo.value.mismatches] == ["point"]
    assert "source 24..25" in str(excinfo.value)


def test_a_scheduled_drill_is_repeatable_only_with_reset_scratch():
    """A successful drill leaves the scratch target POPULATED — run 2 would refuse.

    The documented cron line reuses one scratch DSN, so without a reset the
    drill succeeds exactly once and then reports "the target record is NOT
    empty" forever: a job that can never succeed again bounds no loss window,
    and at the exit-code level it is indistinguishable from a real refusal.
    """
    db = FakeDB(SOURCE_COUNTS)

    def drill(**kwargs):
        return rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN,
            run=FakeRunner(on_restore=db.populate_scratch_from_source),
            connect=db.connect, key=KEY, **kwargs,
        )

    assert drill()["verdict"] == "RESTORED"
    assert any(db.scratch.values())

    with pytest.raises(rb.RecordRestoreRefused, match="NOT empty"):
        drill()

    report = drill(reset_scratch=True)
    assert report["verdict"] == "RESTORED"
    assert report["scratch_reset"] is True
    assert db.scratch == SOURCE_COUNTS


def test_the_scratch_reset_truncates_the_record_tables_and_nothing_else():
    """The reset is one TRUNCATE of the record's own tables, with no CASCADE."""
    seen: list[str] = []

    class RecordingCursor(FakeCursor):
        def execute(self, sql, params=None):
            if sql.upper().startswith("TRUNCATE"):
                seen.append(sql)
            return super().execute(sql, params)

    class RecordingConn(FakeConn):
        def cursor(self):
            return RecordingCursor(self)

    scratch = dict.fromkeys(THE_RECORD_TABLES, 0) | {"journal_event": 9}

    def connect(dsn):
        return RecordingConn(dsn, scratch)

    rb.reset_record_tables(SCRATCH_DSN, connect=connect)
    assert len(seen) == 1
    assert "CASCADE" not in seen[0].upper()
    for table in THE_RECORD_TABLES:
        assert f'public."{table}"' in seen[0]
    assert not any(scratch.values())
    # The tenant rows the record's FKs reference are NOT the record's tables —
    # the statement never names them, so a reset cannot take the control plane
    # with it.
    assert "public.graphs" not in seen[0] and "public.organizations" not in seen[0]


def test_the_drill_reds_when_the_restored_record_is_short():
    """The shape of a real loss: the restore runs, the rows do not all arrive."""
    db = FakeDB(SOURCE_COUNTS)

    def short_restore():
        db.scratch.update({t: n for t, n in SOURCE_COUNTS.items() if t != "journal_event"})
        db.scratch["journal_event"] = 0

    runner = FakeRunner(on_restore=short_restore)
    with pytest.raises(rb.RecordRestoreVerifyError) as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN, run=runner, connect=db.connect, key=KEY
        )
    assert "journal_event" in str(excinfo.value)


# ── retention: prune never guesses ─────────────────────────────────────────


def test_prune_keeps_the_newest_and_refuses_to_keep_nothing():
    storage = MemoryStorage()
    for _ in range(3):
        _dump_manifest(storage=storage)
    keys = [k for k in storage.list(rb.RECORD_BACKUP_PREFIX) if k.endswith(rb.DUMP_OBJECT)]
    assert len(keys) == 3
    deleted = rb.prune_record_backups(storage, keep=2)
    assert len(deleted) == 2
    assert len([k for k in storage.list(rb.RECORD_BACKUP_PREFIX) if k.endswith(rb.DUMP_OBJECT)]) == 2
    with pytest.raises(rb.RecordBackupError, match="keep must be >= 1"):
        rb.prune_record_backups(storage, keep=0)


def test_prune_never_deletes_an_object_whose_manifest_it_cannot_read():
    """The naive delete-everything mutant must be REFUSED, in a call that deletes.

    Two readable artifacts and ``keep=1`` on purpose: with one artifact the
    deletion loop never runs at all, so the stray surviving proves nothing about
    the guard (a prune that deleted every unattributable key would pass it).
    Here a real deletion happens in the same call, so the stray's survival is the
    guard's doing.
    """
    storage = MemoryStorage()
    _dump_manifest(storage=storage)
    _dump_manifest(storage=storage)
    readable = [k for k in storage.list(rb.RECORD_BACKUP_PREFIX) if k.endswith(rb.DUMP_OBJECT)]
    assert len(readable) == 2
    stray = f"{rb.RECORD_BACKUP_PREFIX}20990101T000000000Z_deadbeef/{rb.DUMP_OBJECT}"
    storage.upload(stray, b"not attributable to any readable manifest")
    assert len(rb.list_record_backups(storage)) == 2
    deleted = rb.prune_record_backups(storage, keep=1)
    # A real prune happened — the older artifact's two objects went.
    assert len(deleted) == 2
    assert len([k for k in storage.list(rb.RECORD_BACKUP_PREFIX) if k.endswith(rb.DUMP_OBJECT)]) == 2
    # ...and the unattributable object survived it: a prune that guesses deletes
    # the only copy of something it did not understand.
    assert storage.download(stray).startswith(b"not attributable")


def test_prune_deletes_by_the_KEY_the_manifest_was_read_at_not_its_own_id():
    """#9601 review cycle 2 (P2): the deletion target comes from the PATH, not the file.

    `base` used to be built from `manifest["backup_id"]` — the id the file
    DECLARES about itself. A hand-edited manifest, a duplicated id from a
    half-failed upload, or an id copied from another artifact then redirects the
    deletion: `keep=1` deletes the objects of the artifact it just decided to
    KEEP, and the retention policy becomes the data loss it exists to bound
    (#2414). The sibling tests only ever exercise manifests whose `backup_id`
    agrees with its path, so a revert to the old shape keeps them green.

    Here the doomed artifact's manifest declares the SURVIVOR's id: the prune
    must delete `<id-a>/*` (where it was read) and must leave `<id-b>/*` alone.
    """
    storage = MemoryStorage()
    older = f"{rb.RECORD_BACKUP_PREFIX}20260101T000000000Z_aaaaaaaa"
    newer = f"{rb.RECORD_BACKUP_PREFIX}20260102T000000000Z_bbbbbbbb"

    def put(base: str, *, backup_id: str, created_at: str) -> None:
        manifest = {
            "backup_id": backup_id,
            "format": rb.RECORD_DUMP_FORMAT,
            "created_at": created_at,
            "tables": list(THE_RECORD_TABLES),
            "row_counts": dict(SOURCE_COUNTS),
            "sha256": "0" * 64,
        }
        storage.upload(f"{base}/{rb.DUMP_OBJECT}", b"ciphertext")
        storage.upload(f"{base}/{rb.MANIFEST_OBJECT}", json.dumps(manifest).encode())

    # The OLDER artifact's manifest lies about its id: it names the newer one.
    put(older, backup_id="20260102T000000000Z_bbbbbbbb", created_at="2026-01-01T00:00:00+00:00")
    put(newer, backup_id="20260102T000000000Z_bbbbbbbb", created_at="2026-01-02T00:00:00+00:00")
    assert [r["_manifest_key"] for r in rb.list_record_backups(storage)] == [
        f"{older}/{rb.MANIFEST_OBJECT}", f"{newer}/{rb.MANIFEST_OBJECT}",
    ]

    deleted = rb.prune_record_backups(storage, keep=1)
    assert deleted == [f"{older}/{rb.DUMP_OBJECT}", f"{older}/{rb.MANIFEST_OBJECT}"]
    # The artifact the policy kept is INTACT — the deleted-object count above is
    # what a delete-by-declared-id prune would have taken from the survivor.
    assert storage.download(f"{newer}/{rb.DUMP_OBJECT}") == b"ciphertext"
    assert json.loads(storage.download(f"{newer}/{rb.MANIFEST_OBJECT}"))["backup_id"] == (
        "20260102T000000000Z_bbbbbbbb")


def test_list_record_backups_skips_unreadable_manifests():
    storage = MemoryStorage()
    storage.upload(f"{rb.RECORD_BACKUP_PREFIX}x/{rb.DUMP_OBJECT}", b"orphan")
    _manifest, _s, _r, _d = _dump_manifest(storage=storage)
    rows = rb.list_record_backups(storage)
    assert len(rows) == 1
    assert rows[0]["format"] == rb.RECORD_DUMP_FORMAT


# ── the verb: it exists, and it refuses to report a green it did not measure ──


def _cli(monkeypatch, argv, storage=None, fail_storage=None, db=None, *,
         patch_connector=True, backup_key=KEY):
    from tortoise import __main__ as cli

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    import tortoise.hosted_backup as hb

    if fail_storage is not None:
        class _Boom:
            def __init__(self):
                raise RuntimeError(fail_storage)

        monkeypatch.setattr(hb, "R2Storage", _Boom)
    else:
        store = storage or MemoryStorage()
        monkeypatch.setattr(hb, "R2Storage", lambda: store)
    # The driver, not the verb, is what a DB-free lane cannot supply: psycopg2
    # lives in an optional extra, and the row counts are the verb's whole point.
    # Substituting the connector is stated here so a green run is not read as
    # "a live database was counted" — the live drill is the runbook step.
    if patch_connector:
        monkeypatch.setattr(rb, "_default_connector", (db or FakeDB(SOURCE_COUNTS)).connect)
    # ...and the pg_dump/pg_restore boundary, because this lane has no
    # PostgreSQL client binaries. Same disclosure as above: this pins the verb's
    # WIRING and its exit-code contract, not a live dump.
    monkeypatch.setattr(rb, "_default_run", FakeRunner())
    if backup_key is None:
        monkeypatch.delenv("TORTOISE_BACKUP_KEY", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_BACKUP_KEY", base64.b64encode(backup_key).decode())
    return cli.main(argv)


def test_the_verb_exits_2_when_the_record_is_not_armed(monkeypatch):
    # `main()` RETURNS the verdict (it does not raise) — assert the code a
    # scheduler would actually branch on.
    assert _cli(monkeypatch, ["record-backup"]) == 2


def test_the_verb_exits_2_when_the_bucket_is_unconfigured(monkeypatch):
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        fail_storage="R2 not configured",
    ) == 2


def test_the_verb_exits_2_on_a_pooler_dsn_and_dumps_nothing(monkeypatch, capsys):
    """The PR's headline refusal, read the way a scheduler reads it.

    Pre-fix this was `1` ("the backup failed, retry") — a permanent config fault
    retried forever — while the help text, the verb's docstring and the runbook
    all promised `2`.
    """
    storage = MemoryStorage()
    pooler = "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    assert _cli(monkeypatch, ["record-backup", "--record-dsn", pooler], storage=storage) == 2
    assert storage.list("") == []
    assert "pooler" in capsys.readouterr().err.lower()


def test_the_verb_exits_2_when_the_backup_key_is_unconfigured(monkeypatch, capsys):
    storage = MemoryStorage()
    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", DSN],
        storage=storage, backup_key=None,
    ) == 2
    # Nothing was published, and no plaintext dump was written.
    assert storage.list("") == []
    assert "backup key is not configured" in capsys.readouterr().err


def test_the_verb_exits_2_when_psycopg2_is_absent(monkeypatch, capsys):
    """An install fault is `2` (unconfigured), not `1` (retry)."""
    monkeypatch.setitem(sys.modules, "psycopg2", None)
    storage = MemoryStorage()
    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", DSN],
        storage=storage, patch_connector=False,
    ) == 2
    assert storage.list("") == []
    assert "psycopg2 is not installed" in capsys.readouterr().err


def test_the_verb_refuses_keep_0_as_exit_2_before_backing_anything_up(monkeypatch, capsys):
    """`--keep 0` is a usage refusal, not a traceback after a published upload."""
    storage = MemoryStorage()
    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", DSN, "--keep", "0"],
        storage=storage,
    ) == 2
    assert storage.list("") == []
    assert "--keep must be >= 1" in capsys.readouterr().err


def test_a_prune_failure_does_not_report_a_published_backup_as_failed(monkeypatch, capsys):
    """Exit 1 says "the record is NOT backed up" — false once the artifact is durable."""

    class UnlistableStorage(MemoryStorage):
        def list(self, prefix="", *args, **kwargs):
            raise RuntimeError("R2 listing timed out")

    storage = UnlistableStorage()
    assert _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], storage=storage) == 0
    # Both objects landed: the backup is durable, so the run is not a failure.
    assert any(k.endswith(rb.MANIFEST_OBJECT) for k in storage._objects)
    assert any(k.endswith(rb.DUMP_OBJECT) for k in storage._objects)
    err = capsys.readouterr().err
    assert "retention prune failed" in err
    assert "published" in err


def test_the_verb_exits_2_on_an_empty_record_and_prints_no_mismatch_verdict(monkeypatch, capsys):
    """No verdict means no `MISMATCH`: nothing ran, nothing disagrees."""
    empty = dict.fromkeys(THE_RECORD_TABLES, 0)
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN, "--json"],
        db=FakeDB(empty),
    ) == 2
    out = capsys.readouterr().out
    assert "MISMATCH" not in out


def test_the_verb_refuses_reset_scratch_without_a_scratch_dsn(monkeypatch, capsys):
    """A silent no-op would make an operator believe the scratch was emptied."""
    storage = MemoryStorage()
    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", DSN, "--reset-scratch"],
        storage=storage,
    ) == 2
    assert storage.list("") == []
    assert "--reset-scratch only applies to drill mode" in capsys.readouterr().err


def test_the_verb_drill_is_repeatable_with_reset_scratch(monkeypatch, capsys):
    """The documented schedule, run twice: refused without the flag, green with it."""
    db = FakeDB(SOURCE_COUNTS)

    class DrillingRunner(FakeRunner):
        def __call__(self, argv, **kwargs):
            if argv[0].endswith("pg_restore"):
                db.populate_scratch_from_source()
            return super().__call__(argv, **kwargs)

    from tortoise import __main__ as cli

    def run(argv):
        from tortoise import hosted_backup as hb
        store = MemoryStorage()
        monkeypatch.setattr(hb, "R2Storage", lambda: store)
        monkeypatch.setattr(rb, "_default_connector", db.connect)
        monkeypatch.setattr(rb, "_default_run", DrillingRunner())
        monkeypatch.setenv("TORTOISE_BACKUP_KEY", base64.b64encode(KEY).decode())
        return cli.main(argv)

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    base = ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN]
    assert run(base) == 0
    assert run(base) == 2  # the scratch target is now populated → refused
    assert run([*base, "--reset-scratch"]) == 0


def test_the_verb_backup_mode_does_not_claim_the_artifact_restores(monkeypatch, capsys):
    """A backup without a drill is a CLAIM — the verb must say so, not imply green."""
    storage = MemoryStorage()
    rc = _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], storage=storage)
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOT PROVEN RESTORABLE" in out
    assert storage.list(rb.RECORD_BACKUP_PREFIX) != []


def test_the_dumps_table_set_is_the_schema_s_table_set():
    """The artifact must cover what the MIGRATIONS create, not a parallel list.

    `record_tables()` delegates to `THE_RECORD_TABLES`, and `table_columns()`
    parses the SQL. If the two ever disagree, a table that exists in the record
    is silently absent from every artifact — the #3895 shape (a dump that is
    unrestorable by construction while its own checks read green).
    """
    from tortoise import record_projection as rp

    parsed = set(rp.table_columns())
    declared = set(rb.record_tables())
    assert declared == set(THE_RECORD_TABLES)
    assert declared <= parsed
    assert len(declared) == 9


# ── the dump's own verdict (#9601 review cycle 1) ──────────────────────────


def test_a_dsn_without_a_database_is_refused_and_nothing_is_dumped(tmp_path):
    """P1: the dump leg must not let libpq default the database.

    `libpq_env` refused a missing HOST for this reason and
    `restore_record_backup` refused a missing DATABASE — but the dump, the
    artifact everyone will trust, backed up whatever `PGDATABASE` (else the user
    name) happened to be. A backup of the wrong database is worse than none.
    """
    runner = FakeRunner()
    with pytest.raises(rb.RecordBackupUnavailable, match="names no database"):
        rb.dump_record(
            "postgresql://record_user:s3cr3t-pw@db.projectref.supabase.co:5432",
            tmp_path / "out.dump",
            run=runner,
        )
    assert runner.calls == [], "pg_dump ran for a DSN that names no database"


def test_a_runner_with_no_exit_status_is_not_a_successful_backup(tmp_path):
    """P2: a missing `returncode` used to default to 0 — 'the dump succeeded'."""
    class _NoVerdict:
        stderr = ""

    def _run(argv, **kwargs):
        Path(argv[argv.index("--file") + 1]).write_bytes(b"x")
        return _NoVerdict()

    with pytest.raises(rb.RecordBackupError, match="no exit status"):
        rb.dump_record(DSN, tmp_path / "o.dump", run=_run)


def test_a_missing_client_binary_is_UNAVAILABLE_not_a_failed_backup(tmp_path):
    """P2: an install fault is exit 2 ('could not run'), never exit 1 ('retry')."""
    def _run(argv, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", argv[0])

    with pytest.raises(rb.RecordBackupUnavailable, match="client binaries"):
        rb.dump_record(DSN, tmp_path / "o.dump", run=_run)
