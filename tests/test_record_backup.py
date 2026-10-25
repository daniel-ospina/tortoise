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

* the artifact covers **every** record table (a subset is the #3895 failure:
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
import ipaddress
import json
import re
import socket
import subprocess
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


# ── the resolver is a fixture, not the host (#9601 review cycle 9, P3) ──────

#: The REAL probe, captured before the autouse fixture replaces it. The
#: address-identity arms opt back into it with `real_address_math`.
_REAL_DSN_ADDRESSES = rb._dsn_addresses


def _canned_addresses(host: str) -> frozenset[str]:
    """Deterministic stand-in for `record_backup._dsn_addresses`.

    Every drill test here compares `db.projectref.supabase.co` against
    `db.scratchref.supabase.co`, which `refuse_same_database` decides by ADDRESS —
    and the real probe is a `socket.getaddrinfo` round trip, twice per drill plus
    once per reset. That made this file non-hermetic in both directions: on a
    wildcard/hijacking resolver (or a corporate search domain) both names answer
    with ONE address and a HEALTHY drill is refused as "the SAME database", and on
    a host with no DNS the guard silently takes the lexical fallback. Neither is a
    property of the code under test, and this file's docstring claims it "runs in
    the ordinary lanes".

    One token per name, with the module's own loopback/wildcard collapse kept, so
    the guard's semantics (not its resolver) is what the drill arms exercise. The
    arms whose SUBJECT is address identity request `real_address_math` instead.
    """
    name = host.strip("[]").strip().lower()
    try:
        parsed = ipaddress.ip_address(name)
    except ValueError:
        return frozenset({f"name:{name}"})
    if parsed.is_loopback or parsed.is_unspecified:
        return frozenset({"loopback"})
    mapped = getattr(parsed, "ipv4_mapped", None)
    return frozenset({str(mapped or parsed)})


@pytest.fixture(autouse=True)
def _hermetic_address_resolution(monkeypatch):
    """No arm in this file may depend on the host's resolver."""
    monkeypatch.setattr(rb, "_dsn_addresses", _canned_addresses)


#: The answers the canned RESOLVER gives the address-identity arms.
_RESOLVED = {
    "localhost": ("127.0.0.1", "::1"),
    "127.0.0.1": ("127.0.0.1",),
    "::1": ("::1",),
    "0.0.0.0": ("0.0.0.0",),
    "::": ("::",),
    "::ffff:127.0.0.1": ("::ffff:127.0.0.1",),
    "::ffff:0.0.0.0": ("::ffff:0.0.0.0",),
    "::ffff:0:0": ("::ffff:0:0",),
}


@pytest.fixture
def real_address_math(monkeypatch):
    """The REAL `_dsn_addresses`, over a canned resolver instead of the network.

    ``monkeypatch`` does not count as a hermetic fixture, and neither does a real
    resolver: these arms keep the normalisation code they exist to test while the
    ANSWERS come from `_RESOLVED`. An autouse fixture is set up first, so this one
    wins for the arms that request it.
    """
    monkeypatch.setattr(rb, "_dsn_addresses", _REAL_DSN_ADDRESSES)

    def getaddrinfo(host, port, *args, **kwargs):
        key = str(host).strip("[]").lower()
        try:
            answers = _RESOLVED[key]
        except KeyError:
            raise socket.gaierror(
                socket.EAI_NONAME, "name or service not known") from None
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (a, 0))
            for a in answers
        ]

    monkeypatch.setattr(rb.socket, "getaddrinfo", getaddrinfo)


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
        elif "rolbypassrls" in sql:
            # The ROLE POSTURE read (#9801). The default connection bypasses RLS,
            # which is what R6 names the backup role to be — so every pre-#9801
            # arm in this file keeps measuring what it was written to measure.
            # `FakeConn.role` / `FakeConn.bypassing` move it.
            self._value = (self._conn.role, self._conn.bypassing)
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
        # A tuple for the count/config reads, and the posture pair for
        # `ROLE_POSTURE_SQL` — the real driver returns a row either way, and a
        # cursor that returned None there would read as "the role is unknown".
        if isinstance(self._value, tuple):
            return self._value
        return (self._value,)

    def close(self) -> None:
        pass


class FakeConn:
    def __init__(
        self,
        dsn: str,
        counts: dict[str, int],
        *,
        role: str = "postgres",
        bypassing=True,
    ) -> None:
        self.dsn = dsn
        self.counts = counts
        # The RLS posture `ROLE_POSTURE_SQL` reports for this connection (#9801).
        # Default: a bypassing backup role, which is what R6 requires of R9's
        # leg — so an arm that does not care about the role is unaffected.
        self.role = role
        self.bypassing = bypassing
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


#: Row counts per table for the fake record.
#: ⛔ DERIVED from the one declaration, never restated. It WAS a literal dict of
#: nine tables, and main then added `public.session_narrative` (#9555/R7): the
#: fixture became a second copy of `THE_RECORD_TABLES` and 40 arms went red on
#: `KeyError: 'session_narrative'` — the exact "the table set is restated" defect
#: `record_backup.py` exists not to have (its set comes from
#: `record_projection.THE_RECORD_TABLES`). A table this dict does not name gets
#: `_DEFAULT_COUNT`, so a new record table is covered by CONSTRUCTION and only a
#: count this file's assertions turn on is spelled out.
_COUNT_VALUES = {
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

_DEFAULT_COUNT = 6

SOURCE_COUNTS = {
    table: _COUNT_VALUES.get(table, _DEFAULT_COUNT)
    for table in THE_RECORD_TABLES
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
    # ⛔ `len(THE_RECORD_TABLES)`, not a literal: the numeral 9 was a second copy of
    # the declaration and main's #9555/R7 table made it wrong. The ABSOLUTE set is
    # pinned against the MIGRATIONS by `test_the_dumps_table_set_is_the_schema_s_
    # table_set`; this line only proves the dump took the whole declaration rather
    # than a prefix of it.
    assert len(dumped) == len(THE_RECORD_TABLES)
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


def test_percent_encoded_credentials_are_decoded_at_every_userinfo_site():
    """#3039: ONE userinfo reader — and the one it names must DECODE.

    Every site in this module that reads the DSN's userinfo (the libpq
    environment, the error sentence, the manifest's non-secret coordinates) goes
    through `tortoise.config.parse_uri_userinfo`; a raw
    `urlsplit(...).username` read here is refused by
    `tests/test_from_uri_userinfo.py::test_no_raw_urlparse_userinfo_read_in_guarded_dirs`.
    This test pins the CONTRACT that makes that substitution safe, because the
    guard cannot: `urlsplit` does NOT percent-decode, while libpq/psycopg DO, so
    handing the raw form to the client authenticates with a literal `%40` and
    fails with a misleading "invalid username-password pair". `env` is exactly
    what libpq reads, so a revert to the raw fields (or to `raw_uri_userinfo`)
    reds this.
    """
    dsn = "postgresql://rec%40ord:p%40ss%3Aword@db.projectref.supabase.co:5432/postgres"
    env = rb.libpq_env(dsn)
    assert env["PGUSER"] == "rec@ord"
    assert env["PGPASSWORD"] == "p@ss:word"
    # The manifest is plaintext and world-readable — decoded, never re-encoded.
    assert rb._source_of(dsn)["user"] == "rec@ord"
    # An error sentence names the user; the encoded form must not leak into it.
    assert "rec@ord@" in rb._coordinates_for_message(dsn)
    assert "rec%40ord" not in rb._coordinates_for_message(dsn)


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


def test_a_short_write_cannot_truncate_the_plaintext_dump(tmp_path, monkeypatch):
    """#9601 review cycle 3 — `os.write` may return SHORT, and one bare call drops the rest.

    `write(2)` to a regular file caps near 2 GiB on Linux, so the old single
    `os.write(fd, data)` left a TRUNCATED dump behind without raising: the
    artifact was published over a ciphertext cut from half a dump, and only
    `pg_restore` noticed (a red drill on an artifact that was never whole).

    The double caps every call at 7 bytes, which is the same shape as a kernel
    short write at a scale a test can run: pre-fix exactly 7 of the 200 bytes
    land and this fails.
    """
    real_write = rb.os.write

    def _short(fd, data):
        return real_write(fd, bytes(data[:7]))

    monkeypatch.setattr(rb.os, "write", _short)
    payload = bytes(range(200)) * 3
    target = tmp_path / "record.dump"
    rb._write_secret_file(target, payload)
    assert target.read_bytes() == payload


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


def test_the_drill_refuses_a_scratch_target_reached_by_another_NAME(real_address_math):
    """#9601 review cycle 3 (P1): coordinate equality was still LEXICAL on the host.

    `localhost` and `127.0.0.1` are ONE server with two names, so a scratch DSN
    spelled with the other name passed the "resolves to the SAME database"
    guard — and with `--reset-scratch` the drill then TRUNCATEed the record's
    every record table, restored the artifact over it, and printed `RESTORED`. The
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
            # 1 = the role-posture read (#9801), 2 = the vacuity count, 3 = the
            # `cut` count taken immediately BEFORE `pg_dump` runs (which is also
            # the manifest's `row_counts`), 4 = the post-dump re-count. Rows land
            # between 3 and 4 — i.e. INSIDE the dump, which is the case this arm
            # exists for: an insertion during the dump must not read as an
            # impossible artifact.
            if reads["source"] >= 4:
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
    concurrent row AFTER the dump (read 4), which is the one placement that never
    exercises that guard; this one places it AT the `cut` read (read 3), so the
    count that becomes the artifact's own claim sees it.
    """
    db = FakeDB(SOURCE_COUNTS)
    reads = {"source": 0}
    real_connect = db.connect
    # The row is committed BEFORE the read it precedes, so the count taken as the
    # artifact is cut (read #3 — #1 is the role-posture read added by #9801, #2 is
    # the vacuity count) already sees it — and so does the dump, and so does the
    # post-dump re-count (read #4).
    artifact_counts = {**SOURCE_COUNTS, "journal_event": 515}

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            if reads["source"] >= 3:
                db.source = dict(artifact_counts)
        return real_connect(dsn)

    def restore():
        db.scratch.update(artifact_counts)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=restore), connect=connect, key=KEY,
    )
    assert reads["source"] >= 3, "the concurrent row must land before the manifest count"
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 515, "restored": 515,
    }


def test_an_inflated_manifest_claim_is_neither_a_REFUSAL_nor_a_bound(monkeypatch):
    """The manifest's `row_counts` is NOT a bound on the verdict (#9601 review, P2).

    A `want > ceiling` check used to sit here, documented as refusing an artifact
    that claims more rows than the source ever held. It could never fire: the
    manifest's `row_counts` IS the drill's own `cut`, so a ceiling built from
    `max(before, cut, after)` contains the claim by construction. The check is
    gone; what must remain true is the property it was meant to protect — an
    inflated claim must neither refuse a healthy run nor rescue a SHORT one. The
    restore comparison is the bound, and it reds with the table named.
    """
    db = FakeDB(SOURCE_COUNTS)
    real = rb.create_record_backup

    def overclaiming(*args, **kwargs):
        manifest = real(*args, **kwargs)
        manifest["row_counts"] = {**manifest["row_counts"], "journal_event": 10_000}
        return manifest

    def short_restore():
        # The artifact restores 12 journal rows short of its source.
        db.scratch.update(SOURCE_COUNTS | {"journal_event": 500})

    monkeypatch.setattr(rb, "create_record_backup", overclaiming)
    runner = FakeRunner(on_restore=short_restore)
    with pytest.raises(rb.RecordRestoreVerifyError) as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN, run=runner, connect=db.connect, key=KEY,
        )
    assert "journal_event" in str(excinfo.value)
    assert excinfo.value.mismatches


def test_a_NET_DELETION_during_the_dump_is_not_an_impossible_artifact():
    """#9601 review cycle 3 (P1): the record is NOT append-only.

    `record_projection` emits `DELETE` for `delete`/`merge` journal ops and the
    erasure RPC removes every record table, so a NET DELETION inside the dump window
    leaves the post-dump re-count BELOW the artifact's own count on a healthy
    backup. A ceiling of `after` alone read that as an impossible artifact and
    refused with exit 2 — a false red on the daily drill, and one that also
    stopped retention, because the prune runs only on exit 0. The ceiling is the
    largest of the source reads the drill takes itself, so a legitimate deletion
    cannot red it (and `max(before, after)` alone was still not enough — see the
    transient-peak test below, cycle 4).
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
            # 1 = the role-posture read (#9801), 2 = the vacuity count, 3 = the
            # `cut` count, 4 = the post-dump re-count.
            if reads["source"] >= 4:
                db.source = dict(after_counts)
        return real_connect(dsn)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=lambda: db.scratch.update(SOURCE_COUNTS)),
        connect=connect, key=KEY,
    )
    assert reads["source"] >= 4, "the deletion must land before the ceiling read"
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 500, "restored": 512,
    }


def test_the_drill_does_not_red_when_rows_land_before_the_CUT_and_age_out_by_the_ceiling():
    """#9601 review cycle 4 (P1): a transient peak neither endpoint can see.

    The drill reads the source three times — before the dump, immediately BEFORE
    the artifact is CUT, and once the dump has finished — and a live record makes all three
    numbers different: rows land after `before` (so the artifact's snapshot holds
    515 journal rows) and are aged out before the post-dump re-count (500). An
    endpoint ceiling — `max(before, after)` = 512 — therefore refused the healthy
    artifact with exit 2 and the sentence *"the source never held more than
    512"*, which this same run had already DISPROVED by measuring 515. The
    ceiling is the largest of the three reads the drill takes itself, so an
    honest artifact's claim is accepted; the sibling test above pins that the
    check still bites above every one of them.
    """
    db = FakeDB(SOURCE_COUNTS)
    reads = {"source": 0}
    real_connect = db.connect
    peak = {**SOURCE_COUNTS, "journal_event": 515}
    aged_out = {**SOURCE_COUNTS, "journal_event": 500}

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            # 1 = the ROLE POSTURE read (#9801), 2 = the vacuity count, 3 = the
            # `cut` count taken immediately before `pg_dump` runs, 4 = the
            # post-dump re-count.
            if reads["source"] >= 3:
                db.source = dict(peak)
            if reads["source"] >= 4:
                db.source = dict(aged_out)
        return real_connect(dsn)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=lambda: db.scratch.update(peak)),
        connect=connect, key=KEY,
    )
    assert reads["source"] >= 4, "the deletion must land before the post-dump re-count"
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 500, "restored": 515,
    }


def test_an_artifact_with_no_row_counts_claim_uses_the_SAME_ceiling(monkeypatch):
    """The second symptom of the same bound: an older artifact carries no claim.

    With no `row_counts` the cross-check is skipped, so the VERIFY window is the
    only comparison left — and building it from `(before, after)` alone re-ran
    the identical false red as exit 3: *"journal_event (source 500..512 →
    restored 515). The artifact is NOT proven restorable"* — a data-loss incident
    naming no loss, on a run that lost nothing. Both comparisons now take the one
    upper bound the drill measured (`cut`), so they cannot disagree.
    """
    db = FakeDB(SOURCE_COUNTS)
    reads = {"source": 0}
    real_connect = db.connect
    peak = {**SOURCE_COUNTS, "journal_event": 515}
    aged_out = {**SOURCE_COUNTS, "journal_event": 500}

    real = rb.create_record_backup

    def unclaimed(*args, **kwargs):
        manifest = real(*args, **kwargs)
        manifest.pop("row_counts", None)  # an older, hand-restored manifest
        return manifest

    monkeypatch.setattr(rb, "create_record_backup", unclaimed)

    def connect(dsn):
        if dsn == DSN:
            reads["source"] += 1
            # 1 = the role-posture read (#9801), then the three counts.
            if reads["source"] >= 3:
                db.source = dict(peak)
            if reads["source"] >= 4:
                db.source = dict(aged_out)
        return real_connect(dsn)

    report = rb.run_restore_drill(
        DSN, MemoryStorage(), SCRATCH_DSN,
        run=FakeRunner(on_restore=lambda: db.scratch.update(peak)),
        connect=connect, key=KEY,
    )
    assert report["verdict"] == "RESTORED"
    assert report["tables"]["journal_event"] == {
        "source": 512, "source_after": 500, "restored": 515,
    }


def test_a_pooler_SCRATCH_dsn_is_a_configuration_fault_and_mints_nothing():
    """#9601 review cycle 4 (P2): the scratch side is validated BEFORE the dump.

    `restore_record_backup` refuses a pooler target — but it runs AFTER the
    upload, and the drill's post-publish wrap re-classified that
    `RecordBackupUnavailable` as `RecordRestoreFailed` = exit 1 = "retry", while
    the runbook's exit table promises `2` for a pooler DSN. So every retry dumped,
    encrypted, uploaded and pruned again, forever, on a permanent config fault.
    """
    storage = MemoryStorage()
    runner = FakeRunner()
    db = FakeDB(SOURCE_COUNTS)
    pooler = "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    with pytest.raises(rb.RecordBackupUnavailable) as excinfo:
        rb.run_restore_drill(
            DSN, storage, pooler, run=runner, connect=db.connect, key=KEY,
        )
    # The permanent-config class, NOT the post-publish "retry" class.
    assert not isinstance(excinfo.value, rb.RecordRestoreFailed)
    message = str(excinfo.value).lower()
    assert "pooler" in message and "scratch" in message
    # Nothing was dumped and nothing was minted — the refusal costs one count.
    assert runner.calls == []
    assert storage.list("") == []


def test_a_lost_table_is_STILL_red_on_a_record_that_moved():
    """The tolerance is a window, not a licence: loss is still named."""
    db = FakeDB(SOURCE_COUNTS)
    reads = {"n": 0}
    real_connect = db.connect

    def connect(dsn):
        if dsn == DSN:
            reads["n"] += 1
            # 1 = the role-posture read (#9801), 2 = the vacuity count, 3 = the
            # `cut` count, 4 = the post-dump re-count. The row lands INSIDE the
            # dump (between 3 and 4), so the artifact may legitimately carry it.
            if reads["n"] >= 4:
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

    rb.reset_record_tables(SCRATCH_DSN, source_dsn=DSN, connect=connect)
    assert len(seen) == 1
    assert "CASCADE" not in seen[0].upper()
    for table in THE_RECORD_TABLES:
        assert f'public."{table}"' in seen[0]
    assert not any(scratch.values())
    # The tenant rows the record's FKs reference are NOT the record's tables —
    # the statement never names them, so a reset cannot take the control plane
    # with it.
    assert "public.graphs" not in seen[0] and "public.organizations" not in seen[0]


def test_a_REFUSED_drill_does_not_truncate_the_scratch_evidence(monkeypatch):
    """#9601 review cycle 4 (P3) and cycle 5 (P2): the reset must not precede a refusal.

    The reset is opt-in precisely because an automatic truncate would erase the
    leftover state of a RED drill — the evidence an operator needs. It used to run
    BETWEEN the post-dump re-count and the artifact cross-check; it then moved
    after the cross-check, but `restore_record_backup` still re-downloaded and
    re-verified the artifact AFTER the truncate — so an artifact that corrupted in
    the bucket (exactly what the drill exists to detect) truncated the scratch on
    its way to the refusal. The verified artifact is now loaded BEFORE the reset,
    so nothing that can refuse runs after it.
    """

    class CorruptOnRead(MemoryStorage):
        """A bucket-side corruption: the bytes no longer match the sha256 pin."""

        def download(self, key, *a, **k):
            blob = super().download(key, *a, **k)
            if key.endswith(rb.DUMP_OBJECT):
                return blob[:-1] + bytes([blob[-1] ^ 0xFF])
            return blob

    db = FakeDB(
        SOURCE_COUNTS,
        scratch=dict.fromkeys(THE_RECORD_TABLES, 0) | {"journal_event": 9},
    )
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    with pytest.raises(rb.RecordRestoreRefused, match=r"sha256|tampered|verify"):
        rb.run_restore_drill(
            DSN, CorruptOnRead(), SCRATCH_DSN,
            run=runner, connect=db.connect, key=KEY, reset_scratch=True,
        )
    # The previous run's rows are still there — the refusal preserved the evidence.
    assert db.scratch["journal_event"] == 9
    # And nothing was restored: pg_dump ran, pg_restore never did.
    assert [argv[0] for argv, _kw in runner.calls] == ["pg_dump"]


def test_an_artifact_encrypted_under_a_retained_previous_key_still_restores(monkeypatch):
    """#9601 review (P1): a key rotation must not strand a retained artifact.

    The restore leg called the bare `decrypt_backup`, which resolves the ACTIVE
    key only. After a rotation (the documented `TORTOISE_BACKUP_KEY_PREVIOUS`
    overlap, #2318) every retained record artifact became unreadable — and was
    reported as TAMPERING, i.e. a security incident on a healthy artifact, at the
    exact moment a restore was needed. The graph restore path has always tried the
    candidate chain; the record path now shares that ONE definition.
    """
    import tortoise.hosted_backup as hb

    old_key = bytes(range(32, 64))
    storage = MemoryStorage()
    db = FakeDB(SOURCE_COUNTS)
    manifest = rb.create_record_backup(
        DSN, storage, run=FakeRunner(), connect=db.connect, key=old_key
    )
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    # The box has rotated: KEY is active, `old_key` is retained as the previous one.
    monkeypatch.setattr(hb, "_decrypt_candidate_keys", lambda explicit=None: (KEY, old_key))
    verified, payload = rb.load_verified_artifact(storage, key, key=KEY)
    assert verified["backup_id"] == manifest["backup_id"]
    assert payload == b"PGDUMP-CUSTOM-FORMAT"


def test_an_artifact_under_a_key_this_box_no_longer_holds_is_a_REFUSAL(monkeypatch):
    """The other half: once no candidate authenticates, the refusal says WHY.

    Without this, a mutant that always falls back to some stored key would pass
    the rotation test above while restoring an artifact it should not have.
    """
    import tortoise.hosted_backup as hb

    old_key = bytes(range(32, 64))
    storage = MemoryStorage()
    db = FakeDB(SOURCE_COUNTS)
    manifest = rb.create_record_backup(
        DSN, storage, run=FakeRunner(), connect=db.connect, key=old_key
    )
    key = f"{rb.RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{rb.DUMP_OBJECT}"
    monkeypatch.setattr(hb, "_decrypt_candidate_keys", lambda explicit=None: (KEY,))
    with pytest.raises(rb.RecordRestoreRefused, match="no configured backup key"):
        rb.load_verified_artifact(storage, key, key=KEY)


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


def test_the_reset_REFUSES_the_record_it_is_supposed_to_protect():
    """#9601 review cycle 5 (P1): the reset OWNS the identity guard.

    The refusal used to be a closure inside `run_restore_drill`, so the exported
    operation that TRUNCATEs accepted whatever DSN it was handed — safe by CALLER,
    not by construction. Every spelling of the record below reaches the same
    database, and NONE of them may connect: the guard decides before the first
    cursor exists, which is the property that makes a pre-connection check worth
    having.
    """
    attempts: list[str] = []

    def connect(dsn):
        attempts.append(dsn)
        raise AssertionError("the reset connected before deciding the target was safe")

    same_spellings = (
        DSN,
        DSN.replace("postgresql://", "postgres://"),
        DSN + "/",
        DSN.replace(":5432", ""),
    )
    for target in same_spellings:
        with pytest.raises(rb.RecordRestoreRefused, match="SAME database"):
            rb.reset_record_tables(target, source_dsn=DSN, connect=connect)
    assert attempts == [], (
        "the reset connected to a target it had not yet proven different from the "
        f"record: {attempts}"
    )


def test_the_reset_REFUSES_a_Dsn_that_names_no_database():
    """#9601 review cycle 5 (P1): libpq fills a missing `dbname`, so it cannot be compared.

    `postgresql://user:pw@host:5432` (and the same with a trailing `/`) parses to an
    EMPTY database name, and libpq then uses `PGDATABASE`, else the USER name — so a
    "different" DSN can reach the record, and `--reset-scratch` TRUNCATEs it. The
    dump leg already refused this shape; the leg that deletes did not.
    """

    def connect(dsn):
        raise AssertionError("connected to a DSN that names no database")

    unnamed = (
        "postgresql://record_user:s3cr3t-pw@db.projectref.supabase.co:5432",
        "postgresql://record_user:s3cr3t-pw@db.projectref.supabase.co:5432/",
    )
    for target in unnamed:
        with pytest.raises(rb.RecordRestoreRefused, match="names no database"):
            rb.reset_record_tables(target, source_dsn=DSN, connect=connect)


def test_the_reset_refuses_the_shapes_libpq_RESOLVES_somewhere_else():
    """#9601 review cycle 6 (P1): the guard must read the DSN the way LIBPQ does.

    Two measured bypasses of the cycle-5 guard, both of which reached the TRUNCATE:

    * a QUERY coordinate — libpq lets `?dbname=`, `?host=`, `?hostaddr=`, `?port=` and
      `?service=` override the URI it was given, so `…/scratchdb?dbname=postgres`
      compared as `scratchdb` while the connection reached `postgres` (the record).
    * a MISSING host — `postgresql:///postgres` compared as host `''`, which can
      never intersect a real record host, while libpq fell back to `PGHOST` / a local
      socket. The dump leg already refused this shape (`libpq_env`: "the record DSN
      names no host"), so the leg that DELETES was the odd one out.

    Both are refused rather than folded in: an unread coordinate is an uncomparable
    one, and refusing too much is the safe direction for this guard.
    """

    def connect(dsn):
        raise AssertionError("connected to a target the guard could not reason about")

    def _refused(target: str, needle: str) -> None:
        with pytest.raises(rb.RecordRestoreRefused, match=needle):
            rb.reset_record_tables(target, source_dsn=DSN, connect=connect)

    for key in ("dbname", "host", "hostaddr", "port", "service"):
        _refused(
            f"postgresql://record_user:pw@db.scratchref.supabase.co:5432/scratchdb?{key}=x",
            "query string",
        )
    _refused("postgresql:///postgres", "names no host")
    _refused("postgresql:///scratch?host=db.projectref.supabase.co", "query string")
    # ⛔ A `#` hides a whole query from `urlsplit` while libpq reads it as data
    # (cycle 7, P0 — measured end-to-end: libpq sent `database=postgres`).
    with pytest.raises(rb.RecordBackupUnavailable, match="contains `#`"):
        rb.reset_record_tables(
            f"{DSN[:-len('postgres')]}scratchdb#?dbname=postgres",
            source_dsn=DSN,
            connect=connect,
        )
    # ⛔ A MULTI-HOST list is one unreadable name to `urlsplit` and N servers to libpq
    # (cycle 7, P1 — measured: it dialed the second entry, the record).
    _refused(
        "postgresql://record_user:pw@db.invalid,db.projectref.supabase.co:5432/scratch",
        "MORE THAN ONE host",
    )
    # The inert keys still work: they cannot move the connection, and the dump leg
    # forwards exactly these. `application_name` is the DENY-BY-EFFECT case (#9601
    # review cycle 8, P3): the previous allow-list refused it with a message claiming
    # libpq honours it, which was a false block on the normal way an operator marks a
    # drill's sessions in the server log.
    for inert in ("sslmode=require", "application_name=tortoise-drill", "keepalives=1"):
        rb.reset_record_tables(
            f"postgresql://record_user:pw@db.scratchref.supabase.co:5432/scratch?{inert}",
            source_dsn=DSN,
            connect=lambda dsn: FakeConn(dsn, dict.fromkeys(THE_RECORD_TABLES, 0)),
        )


def test_the_reset_refuses_WILDCARD_addresses_and_the_other_loopback_family(
    real_address_math):
    """#9601 review cycle 8 (P1/P2): 'any' is not a server, and `::1` is `127.0.0.1`.

    Both measured end-to-end on a real socket before this fix:

    * a scratch DSN spelled `0.0.0.0` (the value every container bind and every
      `listen_addresses` line contains) PASSED the guard and the kernel routed it to
      loopback, where `reset_record_tables` truncated the record; same for `[::]`,
      `[::ffff:0.0.0.0]` and `[0:0:0:0:0:ffff:0:0]`.
    * `[::1]` and `127.0.0.1` produced disjoint address sets for ONE dual-stack
      server, so the guard passed a target that dialed the record.

    A wildcard is REFUSED (no drill target legitimately names "any"), and loopback
    is ONE token in either family, so the mapped/wildcard spellings also collapse
    onto it rather than sitting beside it.
    """

    def never(dsn):
        raise AssertionError("connected to a target the guard could not reason about")

    loopback_record = "postgresql://u:p@127.0.0.1:5432/postgres"
    for wildcard in (
        "postgresql://u:p@0.0.0.0:5432/postgres",
        "postgresql://u:p@[::]:5432/postgres",
    ):
        with pytest.raises(rb.RecordRestoreRefused, match="WILDCARD"):
            rb.reset_record_tables(wildcard, source_dsn=loopback_record, connect=never)
    for same in (
        "postgresql://u:p@[::1]:5432/postgres",
        "postgresql://u:p@[::ffff:127.0.0.1]:5432/postgres",
        "postgresql://u:p@[::ffff:0.0.0.0]:5432/postgres",
        "postgresql://u:p@0.0.0.0:5432/postgres",
    ):
        # Each is the SAME place as the loopback record: refused, never connected.
        with pytest.raises(rb.RecordRestoreRefused):
            rb.reset_record_tables(same, source_dsn=loopback_record, connect=never)
    assert rb._dsn_addresses("::1") == rb._dsn_addresses("127.0.0.1") != frozenset()


def test_a_failed_dump_delete_does_NOT_orphan_its_manifest():
    """#9601 review cycle 5 (P3): the manifest is the index — it goes last.

    A `continue` after a failed delete moved on to the MANIFEST, so a locked dump
    object lost its manifest: the artifact then vanished from `list_record_backups`
    (which enumerates manifests) while its ciphertext stayed in the bucket forever,
    unreachable and unprunable, one per failed delete.
    """

    class LockedDumpStorage(MemoryStorage):
        def __init__(self, locked: str) -> None:
            super().__init__()
            self.locked = locked

        def delete(self, key: str) -> None:
            if key.endswith(rb.DUMP_OBJECT) and key.startswith(self.locked):
                raise RuntimeError("object is locked")
            super().delete(key)

    storage = MemoryStorage()
    oldest, _, _, _ = _dump_manifest(storage=storage)
    _dump_manifest(storage=storage)
    locked_base = f"{rb.RECORD_BACKUP_PREFIX}{oldest['backup_id']}"
    locked = LockedDumpStorage(locked_base)
    locked._objects = storage._objects  # same bucket, one object refuses deletion

    rb.prune_record_backups(locked, keep=1)

    assert f"{locked_base}/{rb.MANIFEST_OBJECT}" in locked._objects, (
        "the prune deleted the manifest of an artifact whose dump it could not "
        "delete — the remaining ciphertext can no longer be listed or pruned"
    )
    assert f"{locked_base}/{rb.DUMP_OBJECT}" in locked._objects


def test_the_documented_retention_depth_is_pinned():
    """#9601 review cycle 5 (P3): `14` is stated in two docs and defended by nothing.

    Every prune arm passes an explicit `keep`, so the CONSTANT could be changed to
    any number with the whole suite green. A retention depth is a durability
    promise (how far back a record can be restored), so it is asserted against the
    figure the docs publish, in both places a default is applied.
    """
    import inspect

    assert rb.RECORD_BACKUP_KEEP == 14
    assert (
        inspect.signature(rb.prune_record_backups).parameters["keep"].default
        == rb.RECORD_BACKUP_KEEP
    ), "the prune's own default drifted from the documented depth"


def test_a_BLANK_scratch_dsn_is_a_usage_error_not_backup_only(monkeypatch):
    """#9601 review cycle 5 (P2) and cycle 6 (P3): `--scratch-dsn "$UNSET"` must not read as "published".

    An empty value used to fall into backup mode — exit 0, `published`, and no drill —
    on a schedule whose exit table defines `0` in drill mode as "the restore verified".
    An ABSENT flag still means backup-only. A SUPPLIED flag that names nothing is an
    error, and so is a SET env var that names nothing (cycle 6: the env surface is the
    one the runbook prefers, and it collapsed the same two states).
    """
    storage = MemoryStorage()
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", ""],
        storage=storage,
    ) == 2
    assert storage.list(rb.RECORD_BACKUP_PREFIX) == [], (
        "a blank --scratch-dsn still published an artifact"
    )
    # ...and the absent-flag path is unchanged: backup-only, exit 0, one artifact.
    assert _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], storage=storage) == 0
    assert len(storage.list(rb.RECORD_BACKUP_PREFIX)) > 0
    # The ENV surface refuses a blank value too (#9601 review cycle 6, P3): it is the
    # surface the runbook prefers, and it used to collapse "set to blank" into
    # backup-only mode — the same silent no-drill the flag branch refuses.
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=MemoryStorage(),
        scratch_env="   ",
    ) == 2


def test_a_failed_upload_is_a_documented_failure_class_not_a_traceback(monkeypatch, capsys):
    """#9601 review cycle 5 (P2): `R2Storage.upload` raises a bare `RuntimeError`.

    None of the verb's handlers caught it, so a bucket fault escaped `main()` as a
    traceback instead of the exit-`1` "could not perform" the runbook documents.
    """

    class FailingUpload(MemoryStorage):
        def upload(self, key: str, data: bytes, content_type: str | None = None) -> None:
            raise RuntimeError("R2 upload failed for " + key)

    code = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=FailingUpload(),
    )
    assert code == 1, "an upload failure must arrive as the exit-1 class, not a traceback"
    err = capsys.readouterr().err
    assert "NOT backed up" in err and "Traceback" not in err


def test_the_prune_keeps_the_documented_depth_by_default(monkeypatch):
    """#9601 review cycle 5 (P3): the verb's default reaches the documented `14`.

    `test_the_documented_retention_depth_is_pinned` pins the constant and the
    function default; this pins the WIRING — the verb must forward the constant when
    `--keep` is absent, or the documented depth is a number nobody applies.
    """
    seen: list[int] = []

    def spy(storage, *, keep=rb.RECORD_BACKUP_KEEP):
        seen.append(keep)
        return []

    monkeypatch.setattr(rb, "prune_record_backups", spy)
    assert _cli(monkeypatch, ["record-backup", "--record-dsn", DSN]) == 0
    assert seen == [14], f"the verb pruned with {seen}, not the documented depth"


def test_addresses_normalise_an_ipv4_mapped_ipv6_spelling(real_address_math):
    """#9601 review cycle 7 (P1): `[::ffff:127.0.0.1]` IS `127.0.0.1`.

    Measured before this fix: the two spellings produced disjoint address sets, so
    `same_server` stayed False, the guard passed, and libpq dialed the record.
    """
    v4 = rb._dsn_addresses("127.0.0.1")
    v6_mapped = rb._dsn_addresses("::ffff:127.0.0.1")
    assert v4, "the fixture address did not resolve — the probe would be vacuous"
    assert v6_mapped == v4, (
        f"an IPv4-mapped IPv6 spelling resolved to {sorted(v6_mapped)} while the plain "
        f"IPv4 spelling resolved to {sorted(v4)} — the guard would compare them as "
        "different servers"
    )


def test_the_guard_REFUSES_ambient_libpq_coordinates(monkeypatch):
    """#9601 review cycle 7 (P2): `PGHOSTADDR` outranks the URI's host.

    Measured: a scratch DSN naming `127.0.0.2` reached `127.0.0.1` (the record)
    through an ambient `PGHOSTADDR`, and an absent port let an ambient `PGPORT`
    choose the port. `hostaddr` beats `host`, and a service file can set anything, so
    while those variables are set the DSN the guard compared is not the DSN libpq
    would use. (The connector no longer reads ambient values either — it is built
    from `libpq_env` with an explicit port — so this refusal is defence in depth.)
    """
    for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE"):
        monkeypatch.setenv(name, "127.0.0.1" if name == "PGHOSTADDR" else "svc")
        with pytest.raises(rb.RecordRestoreRefused, match="OUTRANK the DSN"):
            rb.reset_record_tables(
                SCRATCH_DSN,
                source_dsn=DSN,
                connect=lambda dsn: pytest.fail("connected with ambient coordinates set"),
            )
        monkeypatch.delenv(name)


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


def test_prune_does_not_evict_an_artifact_whose_age_it_cannot_read():
    """#9601 review cycle 3 — an undatable manifest used to be evicted FIRST.

    `list_record_backups` sorted on `str(row.get("created_at") or "")`, so a
    manifest with no `created_at` sorted as `""` — ahead of every real
    timestamp — and `prune_record_backups` deleted the one artifact whose age
    this code could not determine, on the strength of a field it failed to read.
    Deleting is irreversible and keeping is not, so an undatable artifact must
    sort LAST: the prune deletes what it can prove is old.

    Two dated artifacts and one undatable, `keep=2`: pre-fix the undated object
    goes and both dated ones survive, so the assertion below is a real
    discriminator rather than a count that happens to match.
    """
    storage = MemoryStorage()
    older = f"{rb.RECORD_BACKUP_PREFIX}20260101T000000000Z_aaaaaaaa"
    newer = f"{rb.RECORD_BACKUP_PREFIX}20260102T000000000Z_bbbbbbbb"
    undated = f"{rb.RECORD_BACKUP_PREFIX}20260103T000000000Z_cccccccc"

    def put(base: str, manifest: dict) -> None:
        storage.upload(f"{base}/{rb.DUMP_OBJECT}", b"ciphertext")
        storage.upload(f"{base}/{rb.MANIFEST_OBJECT}", json.dumps(manifest).encode())

    def manifest(backup_id: str, **extra) -> dict:
        return {
            "backup_id": backup_id,
            "format": rb.RECORD_DUMP_FORMAT,
            "tables": list(THE_RECORD_TABLES),
            "row_counts": dict(SOURCE_COUNTS),
            "sha256": "0" * 64,
            **extra,
        }

    put(older, manifest("20260101T000000000Z_aaaaaaaa", created_at="2026-01-01T00:00:00+00:00"))
    put(newer, manifest("20260102T000000000Z_bbbbbbbb", created_at="2026-01-02T00:00:00+00:00"))
    # The newest artifact carries NO `created_at` — an older writer, or a
    # hand-restored manifest. Nothing here proves it is old, so nothing may
    # delete it.
    put(undated, manifest("20260103T000000000Z_cccccccc"))

    deleted = rb.prune_record_backups(storage, keep=2)
    assert deleted == [f"{older}/{rb.DUMP_OBJECT}", f"{older}/{rb.MANIFEST_OBJECT}"]
    assert storage.download(f"{undated}/{rb.DUMP_OBJECT}") == b"ciphertext"
    assert storage.download(f"{newer}/{rb.DUMP_OBJECT}") == b"ciphertext"
    # An UNPARSEABLE stamp is the same fact as a missing one: the age was never
    # read. `"not-a-date"` is not `""`, so it only survives if the key PARSES
    # rather than comparing the raw strings.
    storage2 = MemoryStorage()
    for base, created in (
        (older, "2026-01-01T00:00:00+00:00"),
        (newer, "2026-01-02T00:00:00+00:00"),
        (undated, "not-a-date"),
    ):
        storage2.upload(f"{base}/{rb.DUMP_OBJECT}", b"ciphertext")
        storage2.upload(
            f"{base}/{rb.MANIFEST_OBJECT}",
            json.dumps(manifest(base.rsplit("/", 1)[-1], created_at=created)).encode(),
        )
    assert rb.prune_record_backups(storage2, keep=2) == [
        f"{older}/{rb.DUMP_OBJECT}", f"{older}/{rb.MANIFEST_OBJECT}",
    ]
    assert storage2.download(f"{undated}/{rb.DUMP_OBJECT}") == b"ciphertext"


def test_list_record_backups_skips_unreadable_manifests():
    storage = MemoryStorage()
    storage.upload(f"{rb.RECORD_BACKUP_PREFIX}x/{rb.DUMP_OBJECT}", b"orphan")
    _manifest, _s, _r, _d = _dump_manifest(storage=storage)
    rows = rb.list_record_backups(storage)
    assert len(rows) == 1
    assert rows[0]["format"] == rb.RECORD_DUMP_FORMAT


# ── the verb: it exists, and it refuses to report a green it did not measure ──


def _cli(monkeypatch, argv, storage=None, fail_storage=None, db=None, *, runner=None,
         patch_connector=True, backup_key=KEY, scratch_env=None, backup_env=None):
    from tortoise import __main__ as cli

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    # #9801 gives the backup its OWN DSN surface. Clear it by default, exactly
    # like the scratch one below, so an operator's shell env cannot decide which
    # role a test backs up as; a test that pins the env route passes `backup_env=`.
    if backup_env is None:
        monkeypatch.delenv(rb.RECORD_BACKUP_DSN_ENV, raising=False)
    else:
        monkeypatch.setenv(rb.RECORD_BACKUP_DSN_ENV, backup_env)
    # The scratch DSN has an environment surface too (#9601 review, P2). Clear it
    # by default so an operator's shell env cannot turn every backup-mode test
    # into a drill; a test that pins the env route passes `scratch_env=`.
    if scratch_env is None:
        monkeypatch.delenv("TORTOISE_RECORD_SCRATCH_DSN", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_RECORD_SCRATCH_DSN", scratch_env)
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
    monkeypatch.setattr(rb, "_default_run", runner if runner is not None else FakeRunner())
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


def test_the_verb_exits_2_on_a_pooler_SCRATCH_dsn_and_mints_nothing(monkeypatch, capsys):
    """#9601 review cycle 4 (P2), read the way a scheduler reads it.

    The drill's scratch DSN is a pre-dump precondition, so a pooler there is the
    same permanent config fault as a pooler source: exit `2`, no artifact minted.
    Pre-fix this exited `1` ("retry") — after publishing an artifact — so a
    scheduler re-dumped and re-uploaded on every attempt.
    """
    storage = MemoryStorage()
    pooler = "postgresql://u:p@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", pooler],
        storage=storage,
    ) == 2
    assert storage.list("") == []
    err = capsys.readouterr().err.lower()
    assert "pooler" in err and "scratch" in err


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


def test_a_drill_exit_1_does_not_claim_the_record_is_unbacked(monkeypatch, capsys):
    """#9601 review cycle 3 (P2): exit `1` in DRILL mode is NOT "no backup".

    The drill publishes the artifact and only THEN restores, so a failing
    `pg_restore` exits `1` while a complete, checksummed artifact sits in
    `record-backups/`. The old flat message — "the record is NOT backed up —
    retry" — reports a durability outage that does not exist, and tells the
    operator to retry a permanent scratch-target fault as if it were transient.
    """
    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN],
        storage=storage,
        runner=FakeRunner(restore_returncode=1),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "the record was NOT backed up" not in err
    assert "the record IS backed up" in err
    assert "NOT proven" in err
    # The claim is supported by the object store, not asserted from the code path:
    # the artifact really is durable, and the message names it.
    assert any(k.endswith(rb.MANIFEST_OBJECT) for k in storage._objects)
    assert any(k.endswith(rb.DUMP_OBJECT) for k in storage._objects)
    published = next(
        json.loads(storage.download(k))["backup_id"]
        for k in storage._objects
        if k.endswith(rb.MANIFEST_OBJECT)
    )
    assert published in err


def test_a_drill_exit_2_after_the_upload_does_not_claim_nothing_was_backed_up(
        monkeypatch, capsys):
    """#9601 review cycle 10 (P2): exit `2` in DRILL mode is not always "no backup".

    `run_restore_drill` publishes the artifact and only THEN resets and restores,
    so a PERMANENT scratch-configuration fault discovered there — the one the
    runbook calls the likeliest, a stale/wrong scratch credential — exits `2`
    while a complete, checksummed artifact is already durable in
    `record-backups/`. The old flat sentence, "nothing was backed up", is the
    false durability outage this verb exists to remove; the exit-1 fix did not
    cover it because this fault keeps its own class.

    The double raises only on the SCRATCH leg, so the source counts, the dump and
    the upload all succeed first: the artifact's presence is a fact of the run, not
    an assumption. Pre-fix the sentence says "nothing was backed up".
    """

    class ScratchLoginRefused(FakeDB):
        def connect(self, dsn):
            if dsn == SCRATCH_DSN:
                raise _DriverError(
                    'password authentication failed for user "scratch"', "28P01"
                )
            return super().connect(dsn)

    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN,
         "--reset-scratch"],
        storage=storage,
        db=ScratchLoginRefused(SOURCE_COUNTS),
    )
    assert rc == 2  # still the *config fault* code: nothing is retried blindly
    err = capsys.readouterr().err
    assert "nothing was backed up" not in err
    assert "IS backed up" in err
    # The claim is supported by the object store, not asserted from the code path:
    # the artifact really is durable, and the message names it.
    assert storage.list(rb.RECORD_BACKUP_PREFIX) != []
    published = next(
        json.loads(storage.download(k))["backup_id"]
        for k in storage._objects
        if k.endswith(rb.MANIFEST_OBJECT)
    )
    assert published in err


def test_a_permanent_fault_BEFORE_the_upload_still_says_nothing_was_backed_up(
        monkeypatch, capsys):
    """The other half of the same contract: the published wording must not go global.

    Without this, a mutant that always names a published artifact would pass the
    drill arm above while telling an operator whose FIRST count failed that a
    backup exists — the same false claim, pointed the other way. Here the source
    leg is what cannot connect, so nothing was dumped and nothing may be claimed.
    """
    storage = MemoryStorage()
    monkeypatch.setattr(
        rb, "_default_connector", _raising("password authentication failed", "28P01")
    )
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=storage,
        patch_connector=False,
    )
    assert rc == 2
    err = capsys.readouterr().err
    assert "nothing was backed up" in err
    assert "IS backed up" not in err
    assert storage.list("") == []


def test_a_backup_mode_exit_1_still_says_the_record_is_not_backed_up(monkeypatch, capsys):
    """The other half of the same contract: the mode-aware wording must not go global.

    Without this, a mutant that always prints "published" would pass the drill
    test while telling a scheduler that a FAILED dump left the record backed up.
    """
    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=storage,
        runner=FakeRunner(dump_returncode=1),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "the record was NOT backed up" in err
    assert "IS backed up" not in err
    assert storage.list("") == []


def test_the_documented_exit_1_contract_is_CLASS_aware_in_the_runbook_and_the_verb():
    """The exit-code contract lives in two artifacts a scheduler's author reads.

    Both used to say the meaning of exit `1` depends on the MODE — "in drill mode
    the artifact is published before the restore is attempted, so the record IS
    backed up". That is false for a drill-mode DUMP failure: nothing was published,
    and the scheduler read the sentence reserved for a post-publish restore
    failure — a durability outage triaged as "backed up" (#9601 review, P2). The
    discriminator is the failure CLASS, and these assertions hold both artifacts
    to it.
    """
    from tortoise import __main__ as cli

    runbook = Path(__file__).resolve().parent.parent / "docs" / "infra-runbook.md"
    rows = [line for line in runbook.read_text().splitlines() if line.startswith("| `1` |")]
    assert len(rows) == 1, f"expected exactly one exit-1 row, found {len(rows)}"
    row = rows[0]
    assert "failure class" in row.lower()
    assert "RecordRestoreFailed" in row
    assert "nothing was published" in row.lower()

    doc = cli._cmd_record_backup.__doc__ or ""
    assert "FAILURE CLASS" in doc
    assert "RecordRestoreFailed" in doc
    assert "NOTHING was published" in doc
    assert "check preconditions 4-5" in doc


def test_the_ceiling_claims_only_what_the_three_source_reads_CAN_see():
    """#9601 review cycle 9 (P3) + cycle 11 (P2): `cut` is read BEFORE `pg_dump`.

    The bound is `max(before, cut, after)` and every one of those reads is taken
    OUTSIDE the dump: the artifact's snapshot instant falls between the `cut` read
    and the `after` read, so a row inserted in that gap and aged out before `after`
    is in the artifact and in no read — it lands above the ceiling and reds a
    healthy artifact with exit `3`. The module and the runbook called `cut` "a
    count taken as the artifact is cut" and the bound "a window whose endpoints
    cannot see a transient peak", which reads as if it WERE the snapshot count. A
    future lane restoring the bound from that prose is the failure this pins; the
    residual is #9795, and both artifacts must name it rather than imply it away.

    ⛔ THE PIN IS THE CLAIM, NOT ONE PHRASING (#9601 review cycle 11, P2). Cycle
    9's arm asserted the PRESENT tense ("...as the artifact is cut") only, and
    the same false statement survived in the PAST tense at three sites —
    including `verify_restore`'s own docstring, i.e. the API doc for the very
    argument it mis-describes. The assertion below forbids the claim in either
    tense, anywhere in the module, and requires the TRUE statement inside
    `verify_restore`'s docstring specifically, so the guard follows the property
    down the file instead of proving one wording.
    """
    root = Path(__file__).resolve().parent.parent
    runbook = " ".join((root / "docs" / "infra-runbook.md").read_text().split())
    source = " ".join((root / "tortoise" / "record_backup.py").read_text().split())

    # The false claim, in BOTH tenses and in both artifacts. Pre-fix the module
    # carried "as the artifact was cut" three times, so this reds on it.
    false_claim = re.compile(r"as the artifact (?:is|was) cut")
    assert not false_claim.search(runbook), (
        "the runbook still calls `cut` the count taken as the artifact is/was cut — "
        "it is taken BEFORE pg_dump runs, and the snapshot instant is inside the dump")
    assert not false_claim.search(source), (
        "the module still calls `cut` the count taken as the artifact is/was cut — "
        f"found: {false_claim.findall(source)} (even as a negated quotation, the "
        "phrase reads as the definition to a lane skimming for `source_high`)")
    assert "cannot see a transient peak" not in source, (
        "the comment still promises the bound sees a transient peak — it sees only "
        "peaks a SAMPLE observed, never one inside the dump window")

    # The true one, named with the residual.
    assert "before `pg_dump` starts" in runbook
    assert "largest read the drill takes OUTSIDE the dump" in runbook
    assert "no arithmetic over these three reads is" in runbook
    assert "#9795" in runbook, "the residual must be named, not implied away"
    assert "before ``pg_dump`` runs" in source
    assert "#9795" in source

    # ⛔ AND IN THE DOC THE ARGUMENT IS DOCUMENTED BY (cycle 11, P2): the drill's
    # call site, `create_record_backup`'s `counts` parameter and `verify_restore`'s
    # `source_high` each stated the false claim. A pin that skims the whole file
    # cannot tell a corrected site from an uncorrected one, so the API doc is
    # asserted directly.
    api_doc = " ".join((rb.verify_restore.__doc__ or "").split())
    assert "immediately BEFORE ``pg_dump`` runs" in api_doc, (
        "verify_restore's docstring must say WHEN `source_high` is read — it is the "
        "API doc the next lane restoring the bound will read")
    assert "#9795" in api_doc, (
        "the residual must be named where `source_high` is defined, not only in "
        "the module docstring a caller may never open")
    writer_doc = " ".join((rb.create_record_backup.__doc__ or "").split())
    assert "immediately BEFORE ``pg_dump`` runs" in writer_doc, (
        "create_record_backup's `counts` docstring must say when the drill reads it")


def test_the_pre_restore_guarantee_names_the_ARTIFACT_and_admits_the_target_residual():
    """#9601 review cycle 11 (P3): the absolute claim was not true.

    The module said the verified artifact's load "leaves NO refusing step between
    the reset and `pg_restore`", and the runbook said every step that can REFUSE
    runs before the truncate. After the `TRUNCATE` commits, `restore_record_backup`
    still (a) re-counts the target and (b) writes the payload file before invoking
    `pg_restore`, and either can fail — destroying exactly the leftover evidence
    of the last RED drill the ordering exists to preserve. The claim was absolute
    and load-bearing (cycles 2 and 5 built on it), so it is NARROWED to what is
    true — no step that can refuse on the ARTIFACT — with the target-side residual
    named. Pre-fix, both absolute phrasings were present, so this reds on it.
    """
    root = Path(__file__).resolve().parent.parent
    runbook = " ".join((root / "docs" / "infra-runbook.md").read_text().split())
    # Comment markers are flattened too: the claim these assertions are about
    # lives in a `#` comment, and the raw whitespace-normalised form keeps the
    # `#` the line started with inside the sentence.
    source = " ".join(
        re.sub(r"(?m)^[ \t]*#+[ \t]?", "", (root / "tortoise" / "record_backup.py").read_text()).split()
    )
    assert "NO refusing step between" not in source, (
        "the module still claims NO refusing step at all between the reset and "
        "pg_restore — the target re-count and the payload write both run there")
    assert "every step that can REFUSE runs" not in runbook, (
        "the runbook still promises every refusing step runs before the truncate")
    assert "and immediately before `pg_restore`" not in runbook, (
        "the runbook still says the reset is immediately before pg_restore")

    # The true, narrower one — and the residual, named with its two real steps.
    assert "NO step that can refuse on the ARTIFACT" in source
    assert "residual is the TARGET side" in source
    assert "re-counts the target" in source
    assert "writes the payload file" in source
    assert "refuse on the **artifact**" in runbook
    assert "The **target** side is not covered by that guarantee" in runbook

    # And the ordering the guarantee IS about still holds in the code: the
    # artifact is verified before the truncate, and the truncate before the
    # restore.
    body = (root / "tortoise" / "record_backup.py").read_text()
    drill = body.split("def run_restore_drill", 1)[1]
    assert drill.index("load_verified_artifact(") < drill.index("reset_record_tables(") < drill.index(
        "restore_record_backup("
    ), "load_verified_artifact must run before reset_record_tables must run before restore"


def test_a_drill_mode_dump_failure_does_not_claim_the_record_is_backed_up(monkeypatch, capsys):
    """The failure the mode-based wording got wrong (#9601 review, P2).

    A drill-mode `pg_dump` failure happens BEFORE anything is published, so the
    run must say the record is NOT backed up — not "published, restore unproven".
    """
    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN],
        storage=storage,
        runner=FakeRunner(dump_returncode=1),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "the record was NOT backed up" in err
    assert "IS backed up" not in err
    assert storage.list("") == []


def test_the_scratch_dsn_can_come_from_the_environment(monkeypatch, capsys):
    """argv is world-readable (`ps`), so the drill has an env route (#9601 review).

    The runbook's cron line used to pass `--scratch-dsn "$VAR"`, putting the
    record's password on the command line — the one thing the module promises it
    never does.
    """
    storage = MemoryStorage()
    db = FakeDB(SOURCE_COUNTS)
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=storage,
        db=db,
        runner=FakeRunner(on_restore=db.populate_scratch_from_source),
        scratch_env=SCRATCH_DSN,
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "RESTORED" in out
    # The drill really ran (a restore was performed), so the env route reached the
    # same code the flag path uses — not a no-op that merely exited 0.
    assert db.scratch["journal_event"] == SOURCE_COUNTS["journal_event"]


def test_the_verb_backup_mode_does_not_claim_the_artifact_restores(monkeypatch, capsys):
    """A backup without a drill is a CLAIM — the verb must say so, not imply green."""
    storage = MemoryStorage()
    rc = _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], storage=storage)
    assert rc == 0
    out = capsys.readouterr().out
    assert "NOT PROVEN RESTORABLE" in out
    assert storage.list(rb.RECORD_BACKUP_PREFIX) != []


def test_the_verb_exits_3_when_the_drill_disagrees_with_its_source(monkeypatch, capsys):
    """Exit `3` is the ONLY code that says "restore disproved".

    #9601 review (P1): every other exit code had a verb-level test; this one had
    only a NEGATIVE assertion (`"MISMATCH" not in out`, in the empty-record test).
    Mutating `return 3` -> `return 0` left the whole suite green, so a regression
    that turns a RED drill into exit `0` would tell an operator and a scheduler
    that the record is backed up AND the artifact proven restorable, when the
    drill proved it does NOT restore. That is the exact false PASS this feature
    exists to prevent, and nothing pinned it.
    """
    storage = MemoryStorage()
    db = FakeDB(SOURCE_COUNTS)
    # The restore "runs" and populates nothing: the restored copy disagrees with
    # a populated source, which is a MISMATCH verdict — not the empty-SOURCE
    # refusal (the source has rows; the restore produced none).
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN, "--json"],
        storage=storage,
        db=db,
        runner=FakeRunner(on_restore=lambda: None),
    )
    assert rc == 3, "a red drill must not exit 0 — 0 is 'backed up and proven'"
    captured = capsys.readouterr()
    payload = json.loads(captured.out[captured.out.index("{"):])
    assert payload["verdict"] == "MISMATCH"
    assert payload["mismatches"], "a MISMATCH must name the tables that disagree"
    assert "RESTORE DRILL RED" in captured.err
    # The contract exit 3 shares with its sibling `RecordRestoreFailed` path: a
    # disagreeing drill is a RESTORE verdict, never a durability outage. The
    # reviewed suggestion also expected the published artifact id in stderr;
    # verified against the source and it is NOT in that message, so asserting it
    # would have pinned a requirement the code does not make (a finding is a
    # claim — verified, not applied).
    assert "NOT proven restorable" in captured.err
    assert "record was NOT backed up" not in captured.err


def test_the_verb_passes_keep_to_the_prune(monkeypatch):
    """`--keep` is the operator's retention policy; it must reach `prune`.

    #9601 review (P2): the prune tests call `prune_record_backups` DIRECTLY with
    their own `keep`, so nothing asserted the flag is plumbed from the verb.
    Mutating `keep=keep` -> `keep=1` left the suite green — and then an operator's
    `--keep 30` would silently delete every artifact past the first.
    """
    seen = {}

    def _prune(storage, keep):
        seen["keep"] = keep
        return []

    monkeypatch.setattr(rb, "prune_record_backups", _prune)

    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", DSN, "--keep", "7"],
        storage=MemoryStorage(),
    ) == 0
    assert seen == {"keep": 7}


def test_the_restore_argv_is_data_only_and_single_transaction(monkeypatch):
    """The restore's atomicity is a documented guarantee — pin the argv.

    `restore_record_backup`'s docstring says `--single-transaction` makes the
    restore atomic, "the only exit a drill can be trusted to observe". The DUMP
    side's argv is asserted; the RESTORE side's was not, and dropping
    `--single-transaction` (or `--data-only`) left the suite green — a mid-restore
    failure would then leave a half-restored scratch, so the documented "retry"
    meets exit 2 ("the target record is NOT empty") instead.
    """
    db = FakeDB(SOURCE_COUNTS)
    runner = FakeRunner(on_restore=db.populate_scratch_from_source)
    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN],
        storage=MemoryStorage(),
        db=db,
        runner=runner,
    ) == 0
    argv = runner.argv_for("pg_restore")
    for flag in ("--data-only", "--single-transaction", "--exit-on-error",
                 "--no-owner", "--no-privileges"):
        assert flag in argv, f"the restore argv lost {flag}: {argv}"


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
    assert len(declared) >= 9, (
        "the record has at least the nine tables this guard was written against; "
        "fewer means `table_columns()` stopped parsing, not that the record shrank "
        "(the ABSOLUTE set is the equality above, against the migrations)"
    )


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


# ── a driver failure is a CLASS, never a traceback (#9601 review cycle 9) ───


class _DriverError(RuntimeError):
    """`psycopg2.OperationalError` without the optional dependency.

    Same shape the module classifies on: a message that may quote the connection
    string, and `pgcode` (None unless libpq gave us a SQLSTATE).
    """

    def __init__(self, message: str, pgcode: str | None = None) -> None:
        super().__init__(message)
        self.pgcode = pgcode


def _raising(message: str, pgcode: str | None = None, seen: list[str] | None = None):
    def connect(dsn):
        if seen is not None:
            seen.append(dsn)
        raise _DriverError(message, pgcode)

    return connect


def test_a_driver_failure_of_the_COUNT_leg_is_a_documented_class_not_a_traceback():
    """P2: `connector(dsn)` stood outside every `try`, so `OperationalError` escaped.

    The first *connection* either mode opens is the role-posture read (#9801) —
    from `create_record_backup` in backup-only mode, from `run_restore_drill` in
    drill mode. The first *count* is the drill's emptiness check (`before`), or,
    in backup-only mode, `create_record_backup`'s own manifest count; there is no
    drill at all in backup-only mode. `_default_connector`'s last statement is a
    bare `psycopg2.connect(...)`, so a refused connection / bad password / absent
    database used to leave `main()` as an interpreter traceback: exit 1 ("try
    again") on faults only a config edit clears, and no sentence from the class
    table the verb documents. Pre-fix this raises `_DriverError`.
    """
    with pytest.raises(rb.RecordBackupError) as excinfo:
        rb.record_row_counts(DSN, connect=_raising("connection ... failed: refused"))
    # A refused connection is RETRYABLE — not the permanent-fault class.
    assert not isinstance(excinfo.value, rb.RecordBackupUnavailable), (
        "a connection refused was classified as a permanent config fault")
    message = str(excinfo.value)
    assert "_DriverError" in message, "the driver's own type is what names the failure"
    # The driver message can quote the connection string, and this text reaches the
    # log stream — so it carries the TYPE and non-secret coordinates, never str(e).
    assert "s3cr3t-pw" not in message
    assert "record_user@db.projectref.supabase.co:5432/postgres" in message


def test_a_driver_failure_of_the_RESET_leg_is_a_documented_class_not_a_traceback():
    """The same hole on the leg that TRUNCATEs — `--reset-scratch` is unattended."""
    seen: list[str] = []
    with pytest.raises(rb.RecordBackupError) as excinfo:
        rb.reset_record_tables(
            SCRATCH_DSN, source_dsn=DSN, connect=_raising("login failed", seen=seen)
        )
    assert seen == [SCRATCH_DSN], "the connector must have been attempted exactly once"
    assert "scratch target" in str(excinfo.value), (
        "the sentence must name WHICH side could not be reached")
    assert "s3cr3t-pw" not in str(excinfo.value)


def test_a_PERMANENT_driver_fault_is_exit_2_so_a_scheduler_stops_retrying():
    """P2, the consequence that matters: exit 2 is "could not run", not "retry".

    `password authentication failed` (28P01) and `database does not exist` (3D000)
    are raised on EVERY attempt, and each attempt dumps, encrypts and uploads first.
    Uncaught they exited 1 = "attempted and did not happen — try again", forever.
    """
    with pytest.raises(rb.RecordBackupUnavailable) as excinfo:
        rb.record_row_counts(DSN, connect=_raising("password authentication failed", "28P01"))
    assert "PERMANENT" in str(excinfo.value)
    assert "28P01" in str(excinfo.value)

    with pytest.raises(rb.RecordBackupUnavailable) as missing_db:
        rb.record_row_counts(DSN, connect=_raising('database "record" does not exist', "3D000"))
    assert "3D000" in str(missing_db.value)


@pytest.mark.parametrize("pgcode,expected", [(None, 1), ("28P01", 2)])
def test_the_verb_exits_the_CLASS_code_when_the_ROLE_leg_cannot_connect(
        monkeypatch, capsys, pgcode, expected):
    """The end-to-end half: the code a scheduler actually branches on.

    Since #9801 the gate is the FIRST connection either path opens, so the
    `_default_connector` failure this arm injects happens on the ROLE-POSTURE
    read — the mapping is the same (`require_bypassing_role` routes through the
    same `_connect` → `_connection_failure` pair as the count leg). Naming the
    leg is not enough to prove it, because an arm that only asserts the exit code
    is satisfied by a run that never opened the gate and failed on the count
    instead, so the arm also spies both legs: the gate must be REACHED and the
    count must NOT be. The count leg's own end-to-end proof is
    `test_the_verb_exits_the_CLASS_code_when_the_COUNT_leg_cannot_connect` below.

    Pre-fix this is not an exit code at all — the exception leaves `main()`.
    """
    reached: list[str] = []
    real_gate, real_count = rb.require_bypassing_role, rb.record_row_counts

    def gate(*a, **kw):
        reached.append("gate")
        return real_gate(*a, **kw)

    def count(*a, **kw):
        reached.append("count")
        return real_count(*a, **kw)

    monkeypatch.setattr(rb, "require_bypassing_role", gate)
    monkeypatch.setattr(rb, "record_row_counts", count)
    monkeypatch.setattr(rb, "_default_connector", _raising("could not connect", pgcode))
    rc = _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], patch_connector=False)
    captured = capsys.readouterr()
    assert reached == ["gate"], (
        f"the failure must land on the role gate BEFORE any count — reached {reached}")
    assert rc == expected, f"stdout={captured.out!r} stderr={captured.err!r}"
    assert "Traceback" not in captured.err
    assert "could not be reached" in captured.err
    assert "s3cr3t-pw" not in captured.err


@pytest.mark.parametrize("pgcode,expected", [(None, 1), ("28P01", 2)])
def test_the_verb_exits_the_CLASS_code_when_the_COUNT_leg_cannot_connect(
        monkeypatch, capsys, pgcode, expected):
    """The count leg's OWN end-to-end exit mapping, past the #9801 role gate.

    The arm above now fails on the posture read, because the gate became the
    first connection; without this sibling the verb-level proof for "a driver
    failure on the COUNT leg is exit 1 (retryable) / 2 (permanent)" would be
    asserting that mapping about the gate instead of the leg it names. So the
    connector answers the posture read and raises on the SECOND connect, and the
    arm pins that the posture statement really ran first — otherwise a run that
    never opened the gate would satisfy the exit code by accident.
    """
    log = _StatementLog()
    base = _logged_connect(FakeDB(SOURCE_COUNTS), log)
    attempts: list[str] = []

    def connector(dsn):
        attempts.append(dsn)
        if attempts[1:]:  # the gate already ran; the COUNT leg is what fails
            raise _DriverError("could not connect", pgcode)
        return base(dsn)

    monkeypatch.setattr(rb, "_default_connector", connector)
    rc = _cli(monkeypatch, ["record-backup", "--record-dsn", DSN], patch_connector=False)
    captured = capsys.readouterr()
    assert attempts == [DSN, DSN], (
        "the run must reach the posture read and then the count leg")
    assert any("rolbypassrls" in sql for sql in log.sql), (
        f"the role gate never executed, so this arm is not measuring the count leg: "
        f"{log.sql}")
    assert rc == expected, f"stdout={captured.out!r} stderr={captured.err!r}"
    assert "Traceback" not in captured.err
    assert "could not be reached" in captured.err
    assert "s3cr3t-pw" not in captured.err


# ── the subprocess and reset bounds (#9601 review cycle 8, P2) ─────────────


def test_the_dump_asks_pg_dump_not_to_wait_forever_for_a_table_lock():
    """`pg_dump`'s per-table ACCESS SHARE queues behind a pending ACCESS EXCLUSIVE.

    Without `--lock-wait-timeout` the client waits indefinitely even though the
    run has a wall-clock bound; the flag makes the dump fail into the retry class
    while the lock holder is still the problem, and the sentence says so.
    """
    runner = FakeRunner()
    db = FakeDB(SOURCE_COUNTS)
    rb.create_record_backup(DSN, MemoryStorage(), run=runner, connect=db.connect, key=KEY)
    argv = runner.argv_for("pg_dump")
    assert f"--lock-wait-timeout={rb.RECORD_LOCK_WAIT_TIMEOUT_S}s" in argv


def test_a_blocked_dump_is_BOUNDED_and_exits_into_the_retry_class(monkeypatch, capsys):
    """An unattended run must FAIL, not stall: no artifact, no exit code = no alert.

    Pre-fix `_default_run` was a bare `subprocess.run(argv, **kwargs)`, so a dump
    blocked behind a long transaction (or a wedged database) waited forever — the
    module's own claim that "a backup run must not hang on a wedged database" was
    false on its most likely path. A timeout is the RETRY class and nothing was
    published, so the sentence must not claim otherwise.
    """

    class BlockedRunner(FakeRunner):
        def __call__(self, argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            if argv[0].endswith("pg_dump"):
                raise subprocess.TimeoutExpired(argv, rb.RECORD_SUBPROCESS_TIMEOUT_S)
            return FakeProc(0, "")

    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN],
        storage=storage,
        runner=BlockedRunner(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "did not finish within" in err
    assert "NOT backed up" in err
    assert "Traceback" not in err
    assert storage.list("") == [], "a timed-out dump must upload nothing"


def test_a_blocked_restore_is_BOUNDED_and_reports_the_artifact_as_durable(monkeypatch, capsys):
    """The same bound on `pg_restore`, and the durability sentence still holds.

    The drill publishes BEFORE it restores, so a restore that times out must exit
    into the post-publish wording with the artifact's id — not into "nothing was
    backed up", and not into a traceback.
    """

    class RestoreBlocked(FakeRunner):
        def __call__(self, argv, **kwargs):
            self.calls.append((list(argv), kwargs))
            if argv[0].endswith("pg_dump"):
                Path(argv[argv.index("--file") + 1]).write_bytes(self.dump_bytes)
                return FakeProc(0, "")
            raise subprocess.TimeoutExpired(argv, rb.RECORD_SUBPROCESS_TIMEOUT_S)

    storage = MemoryStorage()
    rc = _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN],
        storage=storage,
        runner=RestoreBlocked(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "did not finish within" in err
    assert "IS backed up" in err
    assert "nothing was backed up" not in err
    published = next(
        json.loads(storage.download(k))["backup_id"]
        for k in storage._objects
        if k.endswith(rb.MANIFEST_OBJECT)
    )
    assert published in err


def test_the_scratch_reset_bounds_its_TRUNCATE_lock_wait():
    """`TRUNCATE` needs ACCESS EXCLUSIVE — bounded, a blocked reset is a REFUSAL.

    The reset ran under the 5 s CONNECT bound only, so an open transaction on the
    scratch target held it forever with the job producing no exit code. Both
    session bounds must be set BEFORE the truncate, on the same session.
    """
    seen: list[tuple[str, object]] = []

    class RecordingCursor(FakeCursor):
        def execute(self, sql, params=None):
            seen.append((sql, params))
            return super().execute(sql, params)

    class RecordingConn(FakeConn):
        def cursor(self):
            return RecordingCursor(self)

    scratch = dict.fromkeys(THE_RECORD_TABLES, 0)
    rb.reset_record_tables(
        SCRATCH_DSN, source_dsn=DSN, connect=lambda dsn: RecordingConn(dsn, scratch)
    )
    lock_at = [i for i, (sql, _p) in enumerate(seen) if "lock_timeout" in sql]
    statement_at = [i for i, (sql, _p) in enumerate(seen) if "statement_timeout" in sql]
    truncate_at = [i for i, (sql, _p) in enumerate(seen) if sql.upper().startswith("TRUNCATE")]
    assert len(lock_at) == 1 and len(statement_at) == 1 and len(truncate_at) == 1
    assert lock_at[0] < truncate_at[0], "lock_timeout must be set on the session BEFORE the TRUNCATE"
    assert statement_at[0] < truncate_at[0]
    assert seen[lock_at[0]][1] == (str(int(rb.RECORD_RESET_TIMEOUT_MS)),)


def test_a_scratch_reset_blocked_past_the_bound_is_a_REFUSAL_not_a_traceback():
    """The bound's failure is a documented class: the leftover evidence stands.

    A lock timeout on the truncate means the reset did NOT happen, so the target
    still holds the previous drill's rows and the run says "could not empty the
    scratch record" — a refusal (exit 2), never a traceback out of `main()`.
    """

    class BlockedCursor(FakeCursor):
        def execute(self, sql, params=None):
            if sql.upper().startswith("TRUNCATE"):
                raise _DriverError(
                    "canceling statement due to lock timeout", "55P03"
                )
            return super().execute(sql, params)

    class BlockedConn(FakeConn):
        def cursor(self):
            return BlockedCursor(self)

    with pytest.raises(rb.RecordRestoreRefused) as excinfo:
        rb.reset_record_tables(
            SCRATCH_DSN,
            source_dsn=DSN,
            connect=lambda dsn: BlockedConn(dsn, dict.fromkeys(THE_RECORD_TABLES, 0)),
        )
    assert "could not empty the scratch record" in str(excinfo.value)
    assert not isinstance(excinfo.value, rb.RecordBackupUnavailable)


def test_a_scratch_side_count_failure_does_not_blame_the_record():
    """#9601 review cycle 11: the sentence must name the side that failed.

    The drill counts the SOURCE and the SCRATCH target through the same
    `record_row_counts`, and the restore leg never passed a `role` — so a
    scratch-side driver failure was reported as "the record could not be
    reached", i.e. a system-of-record outage that does not exist, on the exact
    path (`--reset-scratch`, stale scratch credential) the runbook calls the
    likeliest. Pre-fix this says "the record could not be reached".
    """

    class ScratchCountRefused(FakeDB):
        def connect(self, dsn):
            if dsn == SCRATCH_DSN and self.scratch_reads_seen():
                raise _DriverError(
                    'password authentication failed for user "scratch"', "28P01"
                )
            return super().connect(dsn)

        def scratch_reads_seen(self) -> bool:
            # Only the RESTORE leg's count: the artifact must be minted first, so
            # the failure is post-publish and the class/exit codes are the ones
            # the drill documents.
            return any(c.dsn == SCRATCH_DSN for c in self.connections)

    with pytest.raises(rb.RecordBackupError) as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN,
            run=FakeRunner(), connect=ScratchCountRefused(SOURCE_COUNTS).connect, key=KEY,
        )
    message = str(excinfo.value)
    assert "scratch target" in message, (
        "the failure happened on the SCRATCH side — the sentence must say so")
    assert "the record could not be reached" not in message, (
        "a scratch-side outage must not be reported as a record outage")


# ── #9801: the backup must run as a role that BYPASSES RLS ──────────────────
#
# R6 arms the record as `tortoise_record_app` — `NOBYPASSRLS`, not the table
# owner — and names the database-level backup as one of exactly three purposes a
# bypassing role may serve. Nothing enforced that on this module, so the verb
# connected as the APP role: every per-table count returned ZERO (`app.current_org_id`
# is never set, and R6 chose silent reads), the drill refused with "the record is
# EMPTY" about a fully populated record, and in backup-only mode `pg_dump`'s
# `row_security = off` default is an ERROR under a NOBYPASSRLS role. Both paths
# published nothing while the module read as done.


class _RoledDB(FakeDB):
    """A fake record whose connection reports an arbitrary RLS posture."""

    def __init__(self, *args, role="postgres", bypassing=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.role = role
        self.bypassing = bypassing

    def connect(self, dsn):
        conn = super().connect(dsn)
        conn.role, conn.bypassing = self.role, self.bypassing
        return conn


class _StatementLog:
    """Every statement a connection issued, so ORDER can be asserted."""

    def __init__(self):
        self.sql: list[str] = []


def _logged_connect(db, log: _StatementLog):
    real = db.connect

    def connect(dsn):
        conn = real(dsn)
        real_cursor = conn.cursor

        def cursor():
            cur = real_cursor()
            execute = cur.execute

            def _execute(sql, params=None):
                log.sql.append(sql)
                return execute(sql, params)

            cur.execute = _execute
            return cur

        conn.cursor = cursor
        return conn

    return connect


def test_require_bypassing_role_names_the_role_it_accepted():
    """The happy path returns the ROLE, so a caller can say who did the dump."""
    db = _RoledDB(SOURCE_COUNTS, role="postgres", bypassing=True)
    assert rb.require_bypassing_role(DSN, connect=db.connect) == "postgres"


def test_the_drill_refuses_a_non_bypassing_role_and_NAMES_it_before_any_count():
    """#9801's headline: the sentence names the ROLE, never the data.

    Pre-fix the same run raised `"the record is EMPTY"` — sending an operator to
    inspect a populated record while the fault was the connecting role — and it
    did so AFTER reading every table's count, i.e. after acting on numbers the
    policies had already emptied.
    """
    log = _StatementLog()
    db = _RoledDB(SOURCE_COUNTS, role="tortoise_record_app", bypassing=False)

    with pytest.raises(rb.RecordBackupUnavailable) as excinfo:
        rb.run_restore_drill(
            DSN, MemoryStorage(), SCRATCH_DSN,
            run=FakeRunner(), connect=_logged_connect(db, log), key=KEY,
        )

    message = str(excinfo.value)
    assert "tortoise_record_app" in message, "the refusal must NAME the role"
    assert rb.RECORD_BACKUP_DSN_ENV in message, "…and the surface that fixes it"
    assert "EMPTY" not in message, (
        "a role fault must not be reported as an empty record — that misdiagnosis "
        "is the defect")
    # THE ORDER: the role was decided BEFORE the first `count(*)`, so no evidence
    # was taken from a connection the policies were silently emptying.
    assert not any("count(*)" in sql for sql in log.sql), (
        f"the drill counted before checking the role: {log.sql}")
    assert any("rolbypassrls" in sql for sql in log.sql), log.sql


def test_the_backup_refuses_a_non_bypassing_role_before_dumping(monkeypatch):
    """Backup-only mode: no artifact at all, and the role named (#9801)."""
    runner = FakeRunner()
    storage = MemoryStorage()
    db = _RoledDB(SOURCE_COUNTS, role="tortoise_record_app", bypassing=False)

    with pytest.raises(rb.RecordBackupUnavailable, match="tortoise_record_app"):
        rb.create_record_backup(DSN, storage, run=runner, connect=db.connect, key=KEY)

    assert runner.calls == [], "nothing may be dumped under a role that cannot read the rows"
    assert storage.list("") == []


def test_the_verb_exits_2_on_a_non_bypassing_role_and_names_the_variable(monkeypatch, capsys):
    """The scheduler's view: a permanent config fault is `2`, not `1` (retry)."""
    storage = MemoryStorage()
    db = _RoledDB(SOURCE_COUNTS, role="tortoise_record_app", bypassing=False)

    assert _cli(
        monkeypatch,
        ["record-backup", "--record-dsn", DSN, "--scratch-dsn", SCRATCH_DSN],
        storage=storage, db=db,
    ) == 2

    err = capsys.readouterr().err
    assert "tortoise_record_app" in err
    assert rb.RECORD_BACKUP_DSN_ENV in err
    assert "the record is EMPTY" not in err
    assert storage.list("") == []


def test_the_backup_dsn_env_surface_wins_over_the_record_dsn(monkeypatch):
    """Two surfaces, two roles: the backup must not inherit the app DSN (#9801)."""
    seen: dict[str, str] = {}

    def capture(dsn, storage, **kwargs):
        seen["dsn"] = dsn
        raise rb.RecordBackupUnavailable("captured")

    monkeypatch.setattr(rb, "create_record_backup", capture)
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://app:pw@db.test/app_role_db")
    backup_dsn = "postgresql://postgres:pw@db.test/record?sslmode=require"

    assert _cli(
        monkeypatch, ["record-backup"], backup_env=backup_dsn,
    ) == 2  # exit 2 from the captured refusal; the DSN is what this pins
    assert seen["dsn"] == backup_dsn, (
        "the backup must run on its OWN DSN surface, not the record's app-role one")


def test_a_blank_backup_dsn_env_is_refused_not_fallen_back_to_the_app_role(monkeypatch, capsys):
    """A cron line whose env is gone must NOT silently back up as the app role.

    Falling through to `TORTOISE_RECORD_DSN` here is precisely the #9801 defect:
    an empty record, a published-nothing run, and a green-looking exit.
    """
    storage = MemoryStorage()
    monkeypatch.setenv("TORTOISE_RECORD_DSN", DSN)

    assert _cli(
        monkeypatch, ["record-backup"], storage=storage, backup_env="",
    ) == 2

    err = capsys.readouterr().err
    assert rb.RECORD_BACKUP_DSN_ENV in err
    assert "set but names nothing" in err
    assert storage.list("") == []


def test_the_role_gate_reads_a_mapping_shaped_row_and_a_NamedTuple_one():
    """Both cursor shapes, because the gate is the FIRST statement either path runs.

    `record_projection` advertises `dict`-shaped rows (a `RealDict` cursor) and a
    plain positional one; reading the posture by position alone raises `KeyError:
    0` on the mapping shape before anything else executes — the same defect the
    tenant scope paid for in #9600 review cycle 4.
    """
    class _MappingCursor(FakeCursor):
        def fetchone(self):
            return {"role": "postgres", "bypassing": True}

    class _NamedTupleCursor(FakeCursor):
        def fetchone(self):
            from collections import namedtuple
            Row = namedtuple("Row", "role bypassing")
            return Row("postgres", True)

    for cursor_cls in (_MappingCursor, _NamedTupleCursor):
        class _Conn(FakeConn):
            def cursor(self, _cls=cursor_cls):
                return _cls(self)

        assert rb.require_bypassing_role(
            DSN, connect=lambda dsc, _c=_Conn: _c(dsc, SOURCE_COUNTS)
        ) == "postgres"


def test_a_role_whose_posture_cannot_be_READ_is_refused_not_assumed():
    """An unverifiable role is not a verified one — the fail-closed direction.

    Two shapes: no `pg_roles` row at all (`fetchone()` returns None), and a row
    whose `bypassing` leg came back NULL (an expression Postgres could not
    evaluate). Neither may be read as "bypasses".
    """
    class _NoRowCursor(FakeCursor):
        def fetchone(self):
            return None

    class _NullPostureCursor(FakeCursor):
        def fetchone(self):
            return ("postgres", None)

    for cursor_cls, expected in (
        (_NoRowCursor, "could not be read"),
        (_NullPostureCursor, "could not be evaluated"),
    ):
        class _Conn(FakeConn):
            def cursor(self, _cls=cursor_cls):
                return _cls(self)

        with pytest.raises(rb.RecordBackupUnavailable, match=expected):
            rb.require_bypassing_role(
                DSN, connect=lambda dsc, _c=_Conn: _c(dsc, SOURCE_COUNTS)
            )


def test_the_posture_statement_carries_the_SAME_predicates_as_the_scope():
    """One definition, two shapes — and the pin that keeps them one.

    `ROLE_POSTURE_SQL` is what decides whether a dump may run at all, and nothing
    else in the Python tree executes it: a dropped predicate here would fail
    closed at runtime with no test reddening — a SEMANTICALLY valid drop yields
    `bypassing = false` → `RecordBackupUnavailable` → exit `2`, and only a SQL
    SYNTAX break reaches exit `1` through `_connection_failure`. The four legs are
    therefore pinned against `TENANT_SCOPE_SQL` — the statement the runtime SQL
    suite DOES execute — so drift reddens here instead.
    """
    from tortoise import record_projection as rp

    for leg in (
        "r.rolsuper OR r.rolbypassrls",
        "pg_has_role(r.oid, g.oid, 'USAGE')",
        "c.relowner = r.oid",
        "pg_has_role(r.oid, c.relowner, 'USAGE')",
    ):
        assert leg in rp.ROLE_POSTURE_SQL, f"the backup gate lost {leg!r}"
        assert leg in rp.TENANT_SCOPE_SQL, (
            f"{leg!r} is gone from the scope statement — the two no longer answer "
            "the same question")
    assert "AS role" in rp.ROLE_POSTURE_SQL, (
        "the role column has no alias — a RealDict cursor cannot name the role in "
        "the refusal, which is the whole point of #9801")
    assert "AS bypassing" in rp.ROLE_POSTURE_SQL
    assert "current_setting" not in rp.ROLE_POSTURE_SQL, (
        "the posture statement must not depend on the tenant GUC: the backup leg "
        "is deliberately unscoped (it must see every tenant)")


def test_a_malformed_backup_dsn_names_the_variable_it_was_read_from(monkeypatch, capsys):
    """The value came from the BACKUP surface — say so, and say ONLY that (#9801).

    `record_writer.record_dsn` names its own env var in its message, and sending
    an operator to `TORTOISE_RECORD_DSN` for a value they set on
    `TORTOISE_RECORD_BACKUP_DSN` is the same "say which one" failure the role
    refusal exists to end. Naming the right surface in an APPENDED clause was not
    enough — the sentence still opened by blaming `TORTOISE_RECORD_DSN`, and the
    appended clause ran on from a message with no closing period.
    """
    assert _cli(
        monkeypatch, ["record-backup"],
        backup_env="mysql://user:pw@db.example/record",
    ) == 2

    err = capsys.readouterr().err
    assert rb.RECORD_BACKUP_DSN_ENV in err, (
        "the message must attribute the malformed value to the surface it came from")
    assert "TORTOISE_RECORD_DSN" not in err, (
        "the app-role variable must not be named at all: the value was never read "
        f"from it, and naming it first sends the operator to the wrong arm — {err!r}")
    assert "record The value" not in err, (
        f"the sentence runs on from an unterminated message — {err!r}")
    assert "pw@db.example" not in err, "the value itself is never echoed"


def test_a_malformed_flag_dsn_is_attributed_to_the_FLAG(monkeypatch, capsys):
    """The flag wins over both env surfaces, so the sentence must name the flag.

    `--record-dsn` takes precedence over `TORTOISE_RECORD_BACKUP_DSN` and
    `TORTOISE_RECORD_DSN`; attributing its value to either variable is the same
    misattribution one surface up.
    """
    assert _cli(
        monkeypatch, ["record-backup", "--record-dsn", "mysql://u:pw@db.example/record"],
    ) == 2

    err = capsys.readouterr().err
    assert "--record-dsn" in err, err
    assert "TORTOISE_RECORD_DSN" not in err, (
        f"the value came from argv, not from an env surface — {err!r}")
    assert "pw@db.example" not in err
