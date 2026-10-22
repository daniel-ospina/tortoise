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

    def cursor(self):
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True

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
    with pytest.raises(rb.RecordBackupError, match=r"DIRECT|POOLER|transaction-mode"):
        rb.dump_record(dsn, "/tmp/should-not-exist.dump", run=runner)
    assert runner.calls == []


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
    """No key must be exit 2 (unconfigured), never a traceback read as "retry"."""
    db = FakeDB(SOURCE_COUNTS)
    storage = MemoryStorage()
    with pytest.raises(rb.RecordBackupError, match="backup key is not configured"):
        rb.create_record_backup(
            DSN, storage, run=FakeRunner(), connect=db.connect, key=None
        )
    assert storage.list("") == []


def test_a_dump_that_writes_no_file_is_refused():
    runner = FakeRunner(write_no_file=True)
    with pytest.raises(rb.RecordBackupError, match="wrote no artifact"):
        rb.create_record_backup(
            DSN, MemoryStorage(), run=runner, connect=FakeDB(SOURCE_COUNTS).connect, key=KEY
        )


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
    assert report["tables"]["point"] == {"source": 24, "restored": 24}
    assert report["tables"]["relation"] == {"source": 63, "restored": 63}
    # The artifact is a persisted object, not an in-memory handoff: the drill
    # reads it back out of storage the way an operator's restore would.
    assert storage.list(f"{rb.RECORD_BACKUP_PREFIX}{report['backup_id']}/") != []
    # The target ended populated and was refused nothing — it started empty.
    assert db.scratch == SOURCE_COUNTS


def test_the_drill_refuses_a_scratch_target_that_is_the_record_itself():
    db = FakeDB(SOURCE_COUNTS)
    with pytest.raises(rb.RecordRestoreRefused, match="scratch DSN is the source DSN"):
        rb.run_restore_drill(
            DSN, MemoryStorage(), DSN, run=FakeRunner(), connect=db.connect, key=KEY
        )


def test_the_drill_refuses_an_empty_record_because_the_pass_would_be_vacuous():
    empty = dict.fromkeys(THE_RECORD_TABLES, 0)
    db = FakeDB(empty)
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    with pytest.raises(rb.RecordRestoreVerifyError, match="EMPTY"):
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN, run=runner, connect=db.connect, key=KEY
        )
    # and it refused BEFORE taking a backup of nothing
    assert runner.calls == []


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
    storage = MemoryStorage()
    _dump_manifest(storage=storage)
    stray = f"{rb.RECORD_BACKUP_PREFIX}20990101T000000000Z_deadbeef/{rb.DUMP_OBJECT}"
    storage.upload(stray, b"not attributable to any readable manifest")
    assert rb.list_record_backups(storage) == [] or all(
        row.get("format") == rb.RECORD_DUMP_FORMAT for row in rb.list_record_backups(storage)
    )
    rb.prune_record_backups(storage, keep=1)
    # Unattributable objects survive: a prune that guesses deletes the only copy
    # of something it did not understand.
    assert storage.download(stray).startswith(b"not attributable")


def test_list_record_backups_skips_unreadable_manifests():
    storage = MemoryStorage()
    storage.upload(f"{rb.RECORD_BACKUP_PREFIX}x/{rb.DUMP_OBJECT}", b"orphan")
    _manifest, _s, _r, _d = _dump_manifest(storage=storage)
    rows = rb.list_record_backups(storage)
    assert len(rows) == 1
    assert rows[0]["format"] == rb.RECORD_DUMP_FORMAT


# ── the verb: it exists, and it refuses to report a green it did not measure ──


def _cli(monkeypatch, argv, storage=None, fail_storage=None, db=None):
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
    monkeypatch.setattr(rb, "_default_connector", (db or FakeDB(SOURCE_COUNTS)).connect)
    # ...and the pg_dump/pg_restore boundary, because this lane has no
    # PostgreSQL client binaries. Same disclosure as above: this pins the verb's
    # WIRING and its exit-code contract, not a live dump.
    monkeypatch.setattr(rb, "_default_run", FakeRunner())
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", base64.b64encode(KEY).decode())
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
