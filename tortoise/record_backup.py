"""The record's backup: an independent logical dump we hold, and a drill that proves it restores.

#9557 (R9 of the hosted-prerequisites record, `08-hosted-prerequisites.md` §B).

## Why this module exists

The durability ruling is already recorded — §3 and §14.1's **O1**: *durability =
the store's backup; rebuildability = the journal*. Promoting Postgres to the
record therefore **moved the durability obligation onto Postgres**, and until
this module **no module of ours held it**: every archiving module in the tree
(`backup.py`, `hosted_backup.py`, `backup_sweep.py`, `selfhost_backup.py`) copies
the **graph**. The machinery *looked* present — it was present, for the wrong
store. That is the whole reason this is a module and not a runbook paragraph.

## Which posture, and what was NOT adopted

Two postures were on the table, and the difference is not price:

* **A. the vendor's managed backup / PITR alone.** A PITR restore is **in
  place** — the vendor *"download[s] the latest available physical backup **to
  the project** and partially restore[s] the database"*, and *"The project is
  inaccessible during this process"*. So it produces **no artifact** we can
  restore into a scratch record, and *"When you delete a project, we
  permanently remove all associated data, including any backups stored in S3"* —
  the copy that protects the record lives inside the thing it protects.
* **B. an independent logical dump we hold off-box.** `pg_dump` of the record's
  own tables, encrypted, uploaded beside the graph archives, restorable into any
  scratch Postgres — which is the only shape that can be **drilled**, and a
  restore that has never been run is not a restore.

**B is implemented here. A is NOT adopted** — it is a cost-bearing owner
decision (the vendor's PITR is a paid add-on with a compute prerequisite and is
excluded from the Spend Cap), so it is left as a recorded option rather than
silently turned on: `docs/durability-posture.md` carries the posture, the
vendor's published prices and the two facts that argue against A alone. B is not
a substitute for a better RPO; it is the half that yields an artifact, and a
deployment may hold both.

Quotes read from the vendor's pages on **2026-10-22** (recorded in full on
#9557). One correction carried from there rather than tidied away: the
"not available for direct download" sentence sits in a subsection about
disabling PITR, and the daily-backup docs imply downloadable daily backups exist
for legacy logical-backup projects — so this module's argument rests on the two
facts that are unambiguous (in-place restore, backups die with the project), not
on a general "nothing is downloadable" claim.

## Why `pg_dump` and not a vendor API

`pg_dump`/`pg_restore` are the vendor's own documented escape hatch for exactly
this (a logical dump is what their CLI wraps), they need no new privileged
surface, and the artifact is portable to any Postgres — so the drill does not
have to trust the vendor's restore path in order to check the vendor's data.
⚠️ The vendor routes dump/restore through the **direct** connection, not the
pooler: a transaction-mode pooler loses session state between transactions, and
a dump/restore *is* a session. :func:`require_direct_dsn` refuses a pooler DSN,
because the failure mode of ignoring it is a dump that looks fine and is not.

## Why the artifact is DATA-only, and what a restore target must therefore carry

Measured, not assumed — this module's first live drill (a real `pg_dump` →
`pg_restore` against `postgres:17`, 2026-10-22) failed three ways worth naming:

1. **A schema-carrying artifact cannot restore into a migrated target.**
   `pg_restore` refuses on `relation "node_identity" already exists`, and
   the schema's ONE home is already `supabase/migrations` (parsed by
   `record_projection.table_columns()`, shipped in a wheel). Carrying a second
   copy in the artifact would let a restore silently install a superseded
   schema — so the artifact is `--data-only` and the **target must have the
   migrations applied**.
2. **The record's foreign keys point OUTSIDE the record.** `node_identity`,
   `journal_event`, `session` and `relation` reference
   `public.graphs(org_id, id)` and `public.organizations(id)` — the control
   plane. A data restore into a target without those rows fails on the FK,
   which is the CORRECT direction (a record dangling from missing tenancy is not
   a record) and is left fail-closed rather than worked around with
   `session_replication_role = replica`.
3. **A drifted artifact must refuse rather than half-restore.** The manifest
   pins the **column set** of every record table, so an artifact written against
   an older schema is refused with the table and columns named instead of
   failing mid-COPY.

So the two preconditions of a restore are stated once here and repeated in the
runbook: the target carries the record's schema (the migrations) and the tenancy
rows its FKs reference. A restore that cannot meet them fails loudly — which is
the point, and precisely what the drill is for.

## What this module refuses, and why each refusal is the point

* **A pooler DSN** (:func:`require_direct_dsn`) — see above.
* **A dump that produced nothing** (:func:`dump_record`) — an empty artifact
  uploaded is worse than a failed backup: it suppresses the operator's own copy
  while reporting health.
* **A partially-populated artifact** — the table set is not a parameter. Every
  artifact covers :data:`tortoise.record_projection.THE_RECORD_TABLES` in full,
  so "restores the record" cannot quietly become "restores six of the nine
  tables" (`#3895` is this tree's own precedent: a dump that filtered nodes but
  not edges was unrestorable *by construction*).
* **Restoring into a populated record** (:func:`restore_record_backup`) — a
  restore into a live record is not a drill, and `pg_restore` would fail halfway
  through on a duplicate key instead of refusing up front.
* **A drill over an empty record** (:func:`run_restore_drill`) — every count
  matches when both sides are zero, so an empty-record drill is a vacuous pass.
* **A mismatched table count after restore** — and it names the tables, because
  `#9567`'s D8 falsifier is exactly "delete one table from the artifact → the
  drill must red **with the table named**".
* **A restore target without the record's schema, or without the tenancy rows
  the record's FKs reference** — the artifact is data-only and the FKs point at
  the control plane, so both fail loudly instead of half-landing (see the
  measured section above).

## What this is NOT

It is not the journal (a JSONL replay is **rebuildability**, never durability —
the ruling above), it is not `rebuild_from_record` (R8 / #9556 owns restoring the
**graph** from a record), and it does not decide the RPO: the dump cadence is the
caller's schedule, stated in `docs/infra-runbook.md`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import stat
import subprocess
import tempfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Callable  # noqa: UP035
from urllib.parse import parse_qs, unquote, urlsplit

from .hosted_backup import decrypt_backup, encrypt_backup
from .record_projection import THE_RECORD_TABLES

__all__ = [
    "DUMP_OBJECT",
    "MANIFEST_OBJECT",
    "RECORD_BACKUP_KEEP",
    "RECORD_BACKUP_PREFIX",
    "RECORD_DUMP_FORMAT",
    "RecordBackupError",
    "RecordBackupUnavailable",
    "RecordRestoreRefused",
    "RecordRestoreVerifyError",
    "compare_row_counts",
    "create_record_backup",
    "dump_record",
    "libpq_env",
    "list_record_backups",
    "prune_record_backups",
    "record_columns",
    "record_row_counts",
    "record_tables",
    "require_backup_key",
    "require_direct_dsn",
    "reset_record_tables",
    "restore_record_backup",
    "run_restore_drill",
]

LOG_NAME = "tortoise.record_backup"
log = logging.getLogger(LOG_NAME)

#: Where a record artifact lives in the shared bucket. Deliberately NOT under
#: ``backups/``: the graph sweeps walk that prefix (`list_drill_candidates`,
#: `prune_backups`) and a record artifact there would be listed as a graph
#: archive whose org/graph segments it does not have.
RECORD_BACKUP_PREFIX = "record-backups/"

#: The artifact format. A reader that does not recognise the value refuses the
#: artifact rather than restoring something it cannot describe.
RECORD_DUMP_FORMAT = "tortoise-record-logical-dump-v1"

DUMP_OBJECT = "record.dump.enc"
MANIFEST_OBJECT = "manifest.json"

#: How many artifacts an unattended run keeps. A record artifact holds the same
#: data as a graph archive and the graph sweep keeps a 24 h + 7 d + 4 w triple;
#: this is a flat count because a record dump is one object, not a pool.
RECORD_BACKUP_KEEP = 14


def record_tables() -> tuple[str, ...]:
    """The record tables an artifact must cover, in declaration order.

    Read from their ONE declaration: a second list here would be the copy that
    goes stale (the reason `record_projection.table_columns` parses the SQL
    rather than restating it).
    """
    return tuple(THE_RECORD_TABLES)


def record_columns() -> dict[str, list[str]]:
    """``{table: sorted columns}`` from the MIGRATIONS, for the manifest pin.

    Read through ``record_projection.table_columns()`` — the parser over
    `supabase/migrations` — rather than restated, so the pin cannot agree with
    itself while disagreeing with the schema a restore target actually has.
    """
    from .record_projection import table_columns

    parsed = table_columns()
    return {t: sorted(parsed[t]) for t in record_tables()}


_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

#: A Supabase pooler host is ``…pooler.supabase.com``; the transaction-mode port
#: is 6543. Both are refused for a dump/restore session (see the module
#: docstring): the vendor routes backup/restore to the DIRECT connection.
_POOLER_HOST_MARKERS = ("pooler.supabase.com", "pooler.supabase.co")
_TRANSACTION_POOLER_PORT = 6543

#: psycopg2 session bounds, shared with `record_reconcile`'s reader posture — a
#: backup run must not hang on a wedged database, and an unbounded `count(*)`
#: over a large table is exactly how it would.
RECORD_CONNECT_TIMEOUT_S = 5
RECORD_STATEMENT_TIMEOUT_MS = 30000

Runner = Callable[..., Any]
Connector = Callable[[str], Any]


class RecordBackupError(RuntimeError):
    """The backup could not be produced, transported or described.

    Distinct from the verification refusals below so a caller can tell "the
    backup did not happen" (retry/schedule) from "the backup happened and is
    WRONG" (an incident).
    """


class RecordBackupUnavailable(RecordBackupError):
    """The run could not be performed at all — this box or this invocation is
    misconfigured. **No verdict was produced, and retrying cannot fix it.**

    Distinct from its parent on purpose: a plain :class:`RecordBackupError` means
    "the backup was attempted and did not happen — retry", while the faults that
    raise this one (a pooler DSN, `psycopg2` absent, `TORTOISE_BACKUP_KEY`
    missing or malformed, a DSN with no host) are PERMANENT until somebody edits
    a config. A scheduler that reads both as the same code retries a config fault
    forever, minting a fresh artifact each time — so the verb maps this class to
    its "could not run" code rather than to "the backup failed".
    """


class RecordRestoreRefused(RecordBackupError):
    """A restore was attempted that this module will not perform.

    A populated target, a foreign/unknown artifact format, an artifact whose
    table set is not the record's, a tampered payload, and a **vacuous drill**
    (an empty record) — each is a refusal, not a partial restore, and none of
    them produced a verdict. Subclasses :class:`RecordBackupError` so a caller
    that only wants "the drill did not run" can catch one name.
    """


class RecordRestoreVerifyError(RecordBackupError):
    """The restore RAN and the numbers disagree.

    Only a real disagreement belongs here: a caller maps this class to an
    incident ("the artifact is NOT proven restorable"), so a case where nothing
    ran — a vacuous drill over an empty record, an artifact that failed its
    integrity or shape pin — must be a refusal instead, or the operator is paged
    for a mismatch that names no mismatch.

    Carries ``mismatches``: ``[{table, source, restored}]`` for the tables that
    did not match, so the report NAMES what was lost instead of saying "the
    drill failed".
    """

    def __init__(self, message: str, *, mismatches: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.mismatches = list(mismatches or [])


# ── connection-string handling ──────────────────────────────────────────────


def _split_dsn(dsn: str):
    if not isinstance(dsn, str) or not dsn.strip():
        raise RecordBackupUnavailable("no record DSN — an unarmed backup is not a backup")
    parts = urlsplit(dsn.strip())
    if parts.scheme not in ("postgres", "postgresql"):
        raise RecordBackupUnavailable(
            "the record DSN must be a postgresql:// (or postgres://) URI — got "
            f"scheme {parts.scheme!r}. A backup of the wrong store is worse than "
            "no backup."
        )
    return parts


def require_direct_dsn(dsn: str) -> str:
    """Refuse a POOLER DSN for a dump/restore session; return the DSN unchanged.

    The vendor's own guidance routes backup/restore to the direct connection,
    and the reason is mechanical: a transaction-mode pooler discards session
    state between transactions, so a session-scoped `SET` (and anything else a
    dump/restore relies on) is not there when the next statement runs. A dump
    taken through one can *look* fine and be short, which is the failure this
    refusal exists to prevent rather than diagnose later.

    Cheap to satisfy (a second, direct connection string) and impossible to
    notice afterwards — hence a refusal with the fix in the message.
    """
    parts = _split_dsn(dsn)
    host = (parts.hostname or "").lower()
    for marker in _POOLER_HOST_MARKERS:
        if host.endswith(marker) or f".{marker}" in host:
            raise RecordBackupUnavailable(
                f"the record DSN host {host!r} is a POOLER. A dump/restore "
                "session must use the DIRECT connection string — the pooler "
                "loses session state between transactions, so an artifact taken "
                "through it can be silently short. Re-run with the direct DSN "
                "(db.<project-ref>.supabase.co:5432)."
            )
    try:
        port = parts.port
    except ValueError as e:  # a malformed port is a malformed DSN
        raise RecordBackupUnavailable(f"the record DSN has an unreadable port: {e}") from e
    if port == _TRANSACTION_POOLER_PORT:
        raise RecordBackupUnavailable(
            f"the record DSN uses port {_TRANSACTION_POOLER_PORT} (the "
            "transaction-mode pooler). A dump/restore session must use the "
            "DIRECT connection — see require_direct_dsn's docstring."
        )
    return dsn


def libpq_env(dsn: str) -> dict[str, str]:
    """The libpq environment for ``dsn``, so the credential never reaches argv.

    Every ``pg_dump``/``pg_restore`` call in this module is driven through these
    variables rather than a positional DSN. Two reasons, both load-bearing:

    * **argv is public.** A process's arguments are readable by every user on
      the box and land in `ps` output, shell history and crash reports; the
      password does not belong there.
    * **argv is logged.** An error message that quotes the failing argv would
      quote the record's credentials into the deployment's log stream.

    So the DSN is parsed once, and only non-secret coordinates (host, port, user,
    database, ssl mode) ever appear in an argument or a report.
    """
    parts = _split_dsn(dsn)
    host = parts.hostname
    if not host:
        raise RecordBackupUnavailable(
            "the record DSN names no host — refusing to let libpq fall back to a "
            "local socket, which would back up the wrong database"
        )
    env: dict[str, str] = {"PGHOST": host}
    try:
        if parts.port:
            env["PGPORT"] = str(parts.port)
    except ValueError as e:
        raise RecordBackupUnavailable(f"the record DSN has an unreadable port: {e}") from e
    if parts.username:
        env["PGUSER"] = unquote(parts.username)
    if parts.password:
        env["PGPASSWORD"] = unquote(parts.password)
    database = unquote(parts.path.lstrip("/"))
    if database:
        env["PGDATABASE"] = database
    query = parse_qs(parts.query)
    # Only the parameters libpq reads from the environment are forwarded; an
    # unknown parameter is DROPPED rather than guessed at (a wrong sslmode is a
    # different connection, and 'we are not sure' must not be the answer).
    for key, env_key in (
        ("sslmode", "PGSSLMODE"),
        ("connect_timeout", "PGCONNECT_TIMEOUT"),
        ("sslrootcert", "PGSSLROOTCERT"),
        ("options", "PGOPTIONS"),
    ):
        values = query.get(key)
        if values:
            env[env_key] = values[0]
    env.setdefault("PGCONNECT_TIMEOUT", str(RECORD_CONNECT_TIMEOUT_S))
    return env


def _subprocess_env(dsn: str) -> dict[str, str]:
    return {**os.environ, **libpq_env(dsn)}


def _require_table(name: str) -> str:
    """Validate a table name before it is interpolated into SQL/argv.

    The names come from :data:`THE_RECORD_TABLES`, so this cannot fire today —
    it exists so that a future edit that makes the list dynamic cannot turn a
    backup into an injection point.
    """
    if not isinstance(name, str) or not _TABLE_RE.match(name) or name not in THE_RECORD_TABLES:
        raise RecordBackupError(
            f"{name!r} is not one of the record's declared tables "
            f"({', '.join(THE_RECORD_TABLES)}) — refusing to dump or count an "
            "undeclared table"
        )
    return name


# ── preconditions ──────────────────────────────────────────────────────────


def require_backup_key() -> bytes:
    """Validate ``TORTOISE_BACKUP_KEY`` WITHOUT encrypting anything.

    Called before the dump, not after it: the key is a precondition of the run
    (the artifact cannot be written without it), and reaching it only after a
    whole record has been written to disk means a keyless box dumps the system
    of record to a temp file, fails, and reports a *retryable* failure — every
    attempt minting another plaintext dump to clean up.
    """
    from .hosted_backup import _get_backup_key

    try:
        return _get_backup_key()
    except RuntimeError as e:
        # `TORTOISE_BACKUP_KEY` absent/malformed is a CONFIGURATION fault, not a
        # backup failure. A scheduler reads the code, so this must not surface as
        # "the backup failed, retry" — it would retry a permanent fault forever.
        raise RecordBackupUnavailable(f"the backup key is not configured — {e}") from e


# ── row counts (the measurement the drill is decided by) ────────────────────


def _default_connector(dsn: str):
    try:
        import psycopg2
    except ImportError as e:
        raise RecordBackupUnavailable(
            "psycopg2 is not installed — the record cannot be counted, so a "
            "restore cannot be verified. Install the `postgres` extra."
        ) from e
    return psycopg2.connect(dsn, connect_timeout=RECORD_CONNECT_TIMEOUT_S)


def record_row_counts(
    dsn: str,
    *,
    connect: Connector | None = None,
    tables: tuple[str, ...] | None = None,
    timeout_ms: int = RECORD_STATEMENT_TIMEOUT_MS,
) -> dict[str, int]:
    """``{table: row count}`` for the record, read in ONE session.

    The count is the drill's unit of evidence because it is the only per-table
    number that is cheap to take on both sides of a restore and impossible to
    fake by agreeing with itself: `#9567` D8 compares these against the live
    baseline, and a table missing from the artifact shows up as a count that
    fell to zero rather than as a general "restore failed".

    ``connect`` exists so the drill can be exercised without a database; the
    default opens psycopg2 with the same connect/statement bounds the record's
    other readers use.
    """
    names = tuple(_require_table(t) for t in (tables or record_tables()))
    connector = connect or _default_connector
    conn = connector(dsn)
    try:
        cur = conn.cursor()
        try:
            # `set_config` rather than `SET`: a placeholder is only legal in a
            # function call, and a `SET` built by string formatting is how an
            # integer becomes an injection surface.
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (str(int(timeout_ms)),))
            counts: dict[str, int] = {}
            for table in names:
                try:
                    cur.execute(f'SELECT count(*) FROM public."{table}"')
                    row = cur.fetchone()
                except Exception as e:
                    raise RecordBackupError(
                        f"could not count public.{table} ({e}) — is the record's "
                        "schema applied on this target? The artifact is DATA-only; "
                        "the schema's one home is supabase/migrations, so a restore "
                        "target must carry it before any data can land."
                    ) from e
                counts[table] = int(row[0])
        finally:
            cur.close()
    finally:
        # Read-only session: roll back rather than commit, so a backup run
        # cannot leave an open transaction holding locks on a live record.
        try:
            conn.rollback()
        finally:
            conn.close()
    return counts


# ── dump ────────────────────────────────────────────────────────────────────


def _default_run(argv, **kwargs):  # pragma: no cover - thin subprocess seam
    return subprocess.run(argv, **kwargs)


def dump_record(
    dsn: str,
    out_path: str | os.PathLike[str],
    *,
    run: Runner | None = None,
    pg_dump: str = "pg_dump",
) -> dict[str, Any]:
    """`pg_dump --data-only` the record's tables to ``out_path``; return its facts.

    The table set is NOT a parameter: see the module docstring's refusal list.
    ``--data-only`` is the schema decision stated in the docstring: the schema's
    one home is `supabase/migrations`, and an artifact that also carried it could
    install a superseded one (and, measured 2026-10-22, cannot restore into a
    target that already has the migrations — `pg_restore` refuses on
    `relation … already exists`).

    ``--no-owner --no-privileges`` is what makes the data restorable into a
    *different* project, where `postgres`' ownership and the vendor's managed
    role grants do not exist.

    Failures are loud and produce no artifact: a non-zero exit is a refusal
    carrying the tool's stderr (which never contains the DSN — see
    :func:`libpq_env`), and a zero-byte or missing output is also a refusal,
    because an empty dump is an artifact that claims to be a backup.
    """
    require_direct_dsn(dsn)
    runner = run or _default_run
    out = Path(out_path)
    argv = [
        pg_dump,
        "--data-only",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file",
        str(out),
        *[f"--table=public.{t}" for t in record_tables()],
    ]
    # The DATABASE comes from PGDATABASE in the env, not from argv: see
    # `libpq_env`. `pg_dump` with no positional database uses it.
    proc = runner(argv, env=_subprocess_env(dsn), capture_output=True, text=True)
    returncode = getattr(proc, "returncode", 0)
    if returncode != 0:
        stderr = (getattr(proc, "stderr", "") or "").strip()
        raise RecordBackupError(
            f"pg_dump exited {returncode} — the record was NOT backed up. "
            f"{stderr[-800:] if stderr else 'no stderr'}"
        )
    if not out.exists():
        raise RecordBackupError(
            f"pg_dump exited 0 but wrote no artifact at {out} — an absent dump is "
            "not a backup"
        )
    payload = out.read_bytes()
    if not payload:
        raise RecordBackupError(
            f"pg_dump wrote an EMPTY artifact at {out} — refusing to publish a "
            "backup that would restore an empty record"
        )
    return {
        "format": RECORD_DUMP_FORMAT,
        "tables": list(record_tables()),
        "columns": record_columns(),
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "dumped_at": datetime.now(UTC).isoformat(),
    }


# ── create: dump → encrypt → upload ────────────────────────────────────────


def _backup_id(now: datetime | None = None) -> str:
    ts = now or datetime.now(timezone.utc)  # noqa: UP017
    # Millisecond + random suffix: two runs inside one millisecond must not
    # collide on one object key (a silent overwrite is a lost backup).
    return f"{ts.strftime('%Y%m%dT%H%M%S')}{ts.microsecond // 1000:03d}Z_{secrets.token_hex(4)}"


def _source_of(dsn: str) -> dict[str, str]:
    """The NON-SECRET coordinates of the source, for the manifest.

    A manifest is plaintext and world-readable to anyone with bucket access, so
    it records where the artifact came from without recording how to get in.
    """
    parts = _split_dsn(dsn)
    out = {"host": (parts.hostname or "").lower()}
    try:
        if parts.port:
            out["port"] = str(parts.port)
    except ValueError:
        pass
    database = unquote(parts.path.lstrip("/"))
    if database:
        out["database"] = database
    if parts.username:
        out["user"] = unquote(parts.username)
    return out


def create_record_backup(
    dsn: str,
    storage,
    *,
    run: Runner | None = None,
    connect: Connector | None = None,
    workdir: str | os.PathLike[str] | None = None,
    key: bytes | None = None,
    pg_dump: str = "pg_dump",
) -> dict[str, Any]:
    """Dump, encrypt and upload the record; return the plaintext manifest.

    The manifest carries the **ciphertext's** sha256 (what a restore re-checks),
    the row counts measured at dump time (so an operator can tell a thin
    artifact from a thin record), the table set, and the non-secret source
    coordinates. It carries no credentials and no row contents.

    ``storage`` is the same duck-typed protocol the graph archives use
    (`upload`/`download`/`list`/`delete`), so a deployment already carrying
    `R2Storage` credentials for the graph sweep needs nothing new.
    """
    require_direct_dsn(dsn)
    # BEFORE the dump: see `require_backup_key`. Only when no key was injected —
    # a caller that passed `key=` has already satisfied the precondition.
    if key is None:
        require_backup_key()
    counts = record_row_counts(dsn, connect=connect)
    backup_id = _backup_id()
    tmp = tempfile.mkdtemp(prefix="record-backup-", dir=str(workdir) if workdir else None)
    try:
        dump_path = Path(tmp) / "record.dump"
        facts = dump_record(dsn, dump_path, run=run, pg_dump=pg_dump)
        payload = dump_path.read_bytes()
        try:
            blob = encrypt_backup(payload, key=key)
        except RuntimeError as e:
            # Reached only when a `key=` was injected and is unusable, or when
            # the environment changed under us: the up-front
            # `require_backup_key()` is the normal path for an unconfigured box.
            raise RecordBackupUnavailable(f"the backup key is not configured — {e}") from e
        manifest: dict[str, Any] = {
            "backup_id": backup_id,
            "format": RECORD_DUMP_FORMAT,
            "created_at": facts["dumped_at"],
            "tables": facts["tables"],
            "columns": facts["columns"],
            "row_counts": counts,
            "row_total": sum(counts.values()),
            "source": _source_of(dsn),
            "plaintext_sha256": facts["sha256"],
            "sha256": hashlib.sha256(blob).hexdigest(),
            "bytes": len(blob),
        }
        base = f"{RECORD_BACKUP_PREFIX}{backup_id}"
        storage.upload(f"{base}/{DUMP_OBJECT}", blob, "application/octet-stream")
        storage.upload(
            f"{base}/{MANIFEST_OBJECT}",
            json.dumps(manifest, indent=2).encode("utf-8"),
            "application/json",
        )
    finally:
        # The decrypted dump is the record in plaintext — remove it in every
        # path, including the failure paths above.
        _rmtree(tmp)
    log.info("record backup %s published (%d rows, %d table(s))",
             backup_id, manifest["row_total"], len(manifest["tables"]))
    return manifest


def _rmtree(path: str | os.PathLike[str]) -> None:
    """Remove the plaintext dump directory, LOUDLY if it could not be removed.

    `ignore_errors=True` made a failed removal silent: no exception, no log, and
    a return that reads exactly like "the plaintext is gone". That is the one
    path this module says must never be silent — the dump in that directory is
    the system of record in the clear — so a failure is logged as a warning
    instead. Cleanup must never FAIL the backup (the artifact is already
    published and durable by then), which is why it is a warning and not a
    raise.
    """
    import shutil

    try:
        shutil.rmtree(str(path))
    except FileNotFoundError:
        return
    except OSError:
        log.warning(
            "could not remove the plaintext record dump directory %s — it may still "
            "hold the record in the clear; remove it by hand",
            path,
            exc_info=True,
        )


def _write_secret_file(path: Path, data: bytes) -> None:
    """Write bytes readable only by this process's owner.

    A decrypted record dump on a shared host is the whole system of record in
    the clear; 0600 before the bytes land, not after.
    """
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
        raise RecordBackupError(f"{path} is readable beyond its owner — refusing to write a plaintext record dump there")


# ── list / prune ────────────────────────────────────────────────────────────


def list_record_backups(storage) -> list[dict[str, Any]]:
    """Every readable record artifact, oldest first.

    A manifest that cannot be read or parsed is **skipped** — never guessed at.
    The caller prunes on this list, and "we could not tell what this object is"
    must not become "delete it".
    """
    rows: list[dict[str, Any]] = []
    for key in storage.list(RECORD_BACKUP_PREFIX):
        if not key.endswith(f"/{MANIFEST_OBJECT}"):
            continue
        try:
            parsed = json.loads(storage.download(key))
        except Exception:
            continue
        if not isinstance(parsed, dict) or not parsed.get("backup_id"):
            continue
        parsed["_manifest_key"] = key
        rows.append(parsed)
    rows.sort(key=lambda row: str(row.get("created_at") or ""))
    return rows


def prune_record_backups(storage, *, keep: int = RECORD_BACKUP_KEEP) -> list[str]:
    """Delete all but the newest ``keep`` artifacts; return the deleted keys.

    Only objects under an artifact whose manifest this module could READ and
    whose ``format`` it recognises are deleted. An unattributable object is left
    alone: a prune that guesses is a prune that deletes the only copy of
    something it did not understand.

    ``keep < 1`` is refused rather than clamped: "keep the newest zero" would
    delete every artifact, which is a data-loss instruction, not a policy.
    """
    if keep < 1:
        raise RecordBackupError(f"keep must be >= 1, got {keep} — a prune that keeps nothing is data loss")
    artifacts = [a for a in list_record_backups(storage) if a.get("format") == RECORD_DUMP_FORMAT]
    doomed = artifacts[:-keep] if len(artifacts) > keep else []
    deleted: list[str] = []
    for manifest in doomed:
        base = f"{RECORD_BACKUP_PREFIX}{manifest['backup_id']}"
        for obj in (DUMP_OBJECT, MANIFEST_OBJECT):
            key = f"{base}/{obj}"
            try:
                storage.delete(key)
            except Exception:  # a prune failure must not lose the survivor set
                log.warning("record-backup prune could not delete %s", key, exc_info=True)
                continue
            deleted.append(key)
    return deleted


# ── restore + verify ────────────────────────────────────────────────────────


def _read_manifest(storage, backup_key: str) -> dict[str, Any]:
    manifest_key = backup_key.replace(f"/{DUMP_OBJECT}", f"/{MANIFEST_OBJECT}")
    try:
        parsed = json.loads(storage.download(manifest_key))
    except Exception as e:
        raise RecordRestoreRefused(
            f"the artifact's manifest is unreadable ({manifest_key}) — refusing to "
            "restore with verification disabled"
        ) from e
    if not isinstance(parsed, dict):
        raise RecordRestoreRefused("the artifact's manifest is not an object — refusing")
    return parsed


def _verify_manifest(manifest: dict[str, Any], blob: bytes) -> None:
    if manifest.get("format") != RECORD_DUMP_FORMAT:
        raise RecordRestoreRefused(
            f"artifact format {manifest.get('format')!r} is not {RECORD_DUMP_FORMAT!r} — "
            "a reader must not restore a shape it cannot describe"
        )
    if not manifest.get("sha256"):
        raise RecordRestoreRefused(
            "the artifact's manifest carries no sha256 — refusing to restore with "
            "integrity disabled"
        )
    if hashlib.sha256(blob).hexdigest() != manifest["sha256"]:
        raise RecordRestoreRefused(
            "artifact integrity check failed (sha256 mismatch) — the object is "
            "truncated or was modified after it was written"
        )
    declared = manifest.get("tables")
    if sorted(declared or []) != sorted(record_tables()):
        raise RecordRestoreRefused(
            "the artifact's table set is not the record's: "
            f"artifact={sorted(declared or [])} record={sorted(record_tables())} — "
            "restoring a partial record would look like a successful restore"
        )
    # The SCHEMA pin. The artifact carries data only, so an artifact written
    # against an older schema would otherwise fail mid-COPY — or, on a reordered
    # column, land values in the wrong columns. Refusing up front names the
    # drift instead of reporting "the restore failed".
    declared_columns = manifest.get("columns")
    if not isinstance(declared_columns, dict):
        raise RecordRestoreRefused(
            "the artifact's manifest carries no column pin — refusing to restore "
            "into a schema it cannot be proven to match"
        )
    drifted = {
        table: {"artifact": sorted(declared_columns.get(table) or []), "schema": cols}
        for table, cols in record_columns().items()
        if sorted(declared_columns.get(table) or []) != cols
    }
    if drifted:
        raise RecordRestoreRefused(
            "the artifact was written against a DIFFERENT record schema "
            f"({', '.join(sorted(drifted))}) — its columns do not match the "
            "migrations this reader parses. Restoring it would fail mid-COPY or "
            "land values in the wrong columns; re-take the backup against the "
            "current schema."
        )


def restore_record_backup(
    storage,
    backup_key: str,
    target_dsn: str,
    *,
    run: Runner | None = None,
    connect: Connector | None = None,
    workdir: str | os.PathLike[str] | None = None,
    key: bytes | None = None,
    pg_restore: str = "pg_restore",
) -> dict[str, Any]:
    """Download → verify → refuse a populated target → restore; return the outcome.

    The ORDER is the guarantee. The integrity and table-set checks run before
    any bytes are handed to `pg_restore`, and the target is read for row counts
    before the restore starts, so a mistaken invocation against the live record
    is refused while it is still harmless. `--single-transaction` then makes the
    restore itself atomic: a failure halfway leaves the target as it was, which
    is the only exit a drill can be trusted to observe.
    """
    require_direct_dsn(target_dsn)
    if not backup_key.endswith(f"/{DUMP_OBJECT}"):
        raise RecordRestoreRefused(
            f"backup_key must reference a {DUMP_OBJECT} object, got {backup_key!r}"
        )
    try:
        blob = storage.download(backup_key)
    except KeyError as e:
        raise RecordRestoreRefused(f"artifact not found: {backup_key}") from e
    except Exception as e:
        # A transport failure is NOT a refusal (nothing was measured, and a retry
        # may well succeed) — but it must arrive as this module's error, not as a
        # raw traceback out of `main()`, which reads as "the backup failed".
        raise RecordBackupError(
            f"the artifact could not be downloaded ({backup_key}): {e} — nothing "
            "was restored"
        ) from e
    manifest = _read_manifest(storage, backup_key)
    _verify_manifest(manifest, blob)
    try:
        payload = decrypt_backup(blob, key=key)
    except ValueError as e:
        # A wrong key and a forged payload both surface as ValueError from
        # AES-GCM. Neither is a transport failure, so neither may be reported
        # as "retry later" — a caller must see a REFUSAL.
        raise RecordRestoreRefused(
            f"the artifact could not be decrypted ({e}) — the key is wrong or the "
            "payload was tampered with; nothing was restored"
        ) from e

    before = record_row_counts(target_dsn, connect=connect)
    populated = {t: n for t, n in before.items() if n}
    if populated:
        raise RecordRestoreRefused(
            "the target record is NOT empty "
            f"({', '.join(f'{t}={n}' for t, n in sorted(populated.items()))}) — a "
            "restore into a populated record is not a drill, and it would either "
            "fail on a duplicate key or overwrite live data. Point the drill at a "
            "scratch database."
        )

    runner = run or _default_run
    tmp = tempfile.mkdtemp(prefix="record-restore-", dir=str(workdir) if workdir else None)
    try:
        dump_path = Path(tmp) / "record.dump"
        _write_secret_file(dump_path, payload)
        env = _subprocess_env(target_dsn)
        database = env.get("PGDATABASE", "")
        if not database:
            raise RecordBackupUnavailable(
                "the target DSN names no database — pg_restore would have nothing "
                "to restore into"
            )
        argv = [
            pg_restore,
            "--data-only",
            "--no-owner",
            "--no-privileges",
            "--single-transaction",
            "--exit-on-error",
            "--dbname",
            database,
            str(dump_path),
        ]
        proc = runner(argv, env=env, capture_output=True, text=True)
        returncode = getattr(proc, "returncode", 0)
        if returncode != 0:
            stderr = (getattr(proc, "stderr", "") or "").strip()
            raise RecordBackupError(
                f"pg_restore exited {returncode} — the artifact did NOT restore. "
                f"{stderr[-800:] if stderr else 'no stderr'}"
            )
    finally:
        _rmtree(tmp)

    return {
        "backup_id": manifest.get("backup_id"),
        "target": _source_of(target_dsn),
        "restored": record_row_counts(target_dsn, connect=connect),
        "manifest": manifest,
    }


def compare_row_counts(
    source: dict[str, int],
    restored: dict[str, int],
    *,
    tables: tuple[str, ...] | None = None,
    source_after: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compare two per-table count maps; return a report with named mismatches.

    Equality is required per table, never in TOTAL: a restore that lost a whole
    table while duplicating rows elsewhere can reconcile the sum, and the sum is
    the number an operator would quote.

    ``source_after`` turns the exact comparison into a WINDOW, and it exists
    because the record is a live system: the artifact is cut from a snapshot
    taken after the count, so rows committed in between are legitimately in the
    artifact and not in the count. Comparing exactly against a pre-dump count
    therefore reds a perfectly healthy backup on any record with continuous
    writes — and a drill that reds every day on a healthy backup trains the
    operator to ignore the one signal the design exists to produce.

    The window is ``[min(source, source_after), max(source, source_after)]`` per
    table, and it keeps the falsifier sharp in both directions: below the floor
    is LOSS (rows that existed before the dump did not come back), and above the
    ceiling is a restore carrying rows the source never had (duplication or a
    corrupt artifact). Only the ambiguity in the middle — the count-to-snapshot
    gap — is tolerated, and the report NAMES the moved table with both bounds.
    """
    names = tuple(tables or record_tables())
    rows: dict[str, dict[str, int]] = {}
    mismatches: list[dict[str, Any]] = []
    for table in names:
        want = int(source.get(table, -1))
        got = int(restored.get(table, -1))
        later = int(source_after.get(table, want)) if source_after is not None else want
        floor, ceiling = min(want, later), max(want, later)
        rows[table] = {"source": want, "source_after": later, "restored": got}
        if not (floor <= got <= ceiling):
            mismatches.append(
                {"table": table, "source": want, "source_after": later, "restored": got}
            )
    return {
        "equal": not mismatches,
        "tables": rows,
        "mismatches": mismatches,
        "row_total": sum(v["source"] for v in rows.values()),
    }


def verify_restore(
    source: dict[str, int],
    restored: dict[str, int],
    *,
    source_after: dict[str, int] | None = None,
) -> dict[str, Any]:
    """`compare_row_counts`, but a mismatch RAISES with the tables named.

    ``source_after`` is the post-dump re-count: pass it whenever the source can
    be written to (see :func:`compare_row_counts`), so the drill measures what
    the artifact could legitimately contain rather than what a count taken
    before the dump saw.
    """
    report = compare_row_counts(source, restored, source_after=source_after)
    if not report["equal"]:
        def _named(m: dict[str, Any]) -> str:
            if m["source"] == m["source_after"]:
                return f"{m['table']} (source {m['source']} → restored {m['restored']})"
            return (
                f"{m['table']} (source {min(m['source'], m['source_after'])}"
                f"..{max(m['source'], m['source_after'])} → restored {m['restored']})"
            )

        named = ", ".join(_named(m) for m in report["mismatches"])
        raise RecordRestoreVerifyError(
            f"the restored record does not match its source: {named}. The "
            "artifact is NOT proven restorable.",
            mismatches=report["mismatches"],
        )
    return report


def reset_record_tables(dsn: str, *, connect: Connector | None = None) -> None:
    """EMPTY the record's tables on ``dsn`` — the scratch reset that makes a drill repeatable.

    A successful drill leaves the scratch target populated, and every restore
    refuses a populated target — so without a reset the documented schedule
    succeeds exactly ONCE and then reports "the target record is NOT empty"
    forever, which at the exit-code level is indistinguishable from a real
    refusal. That is worse than a red: the drill is the only thing bounding the
    record's loss window, and a job that can never succeed again bounds nothing.

    This is deliberately NOT called by :func:`restore_record_backup` and is opt-in
    (`--reset-scratch`) in the verb: a truncate that ran automatically would
    destroy the evidence of the last RED drill, and the operator would have no
    reason to suspect it.

    ALL the record tables go in ONE ``TRUNCATE`` statement, which is what makes
    it legal while their foreign keys reference each other. No ``CASCADE``: a
    cascade would reach into whatever else references the record, and a reset
    that empties more than the record is exactly the accident this refuses to
    have. If anything outside the record references it, the statement fails
    loudly instead.
    """
    require_direct_dsn(dsn)
    sql = "TRUNCATE " + ", ".join(f'public."{_require_table(t)}"' for t in record_tables())
    connector = connect or _default_connector
    conn = connector(dsn)
    try:
        cur = conn.cursor()
        try:
            cur.execute(sql)
        except Exception as e:
            raise RecordRestoreRefused(
                f"could not empty the scratch record ({e}) — refusing to restore "
                "into a target this run could not reset. --reset-scratch only "
                "truncates the record's own tables on the SCRATCH DSN."
            ) from e
        finally:
            cur.close()
        conn.commit()
    finally:
        conn.close()


def run_restore_drill(
    dsn: str,
    storage,
    scratch_dsn: str,
    *,
    run: Runner | None = None,
    connect: Connector | None = None,
    workdir: str | os.PathLike[str] | None = None,
    key: bytes | None = None,
    pg_dump: str = "pg_dump",
    pg_restore: str = "pg_restore",
    reset_scratch: bool = False,
) -> dict[str, Any]:
    """Back up the record, restore the artifact into scratch, and prove it matched.

    This is the drill the durability posture requires — *"a restore that has
    never been run is not a restore"* — and it takes the artifact through the
    full path an operator would: dump, encrypt, upload, download, decrypt,
    restore, count. It never trusts the writer's own claim about what it wrote:
    the numbers come from the two databases, not from the manifest.

    The comparison is against the counts the ARTIFACT was cut with (the manifest's
    `row_counts`, taken immediately before the dump) and is bounded above by a
    re-count taken after the dump: the record is live, so rows committed in that
    gap are in the artifact and not in the count, and comparing exactly would red
    a healthy backup every day (see :func:`compare_row_counts`).

    Refusals, in the order they matter:

    * an **empty source** — every count would match against an empty scratch
      database, so the drill would report success while proving nothing. This is
      a REFUSAL (no verdict), never a mismatch: nothing ran;
    * a scratch DSN **equal to the source** — the restore (and a
      ``reset_scratch``) would destroy the record it is supposed to protect;
    * a non-empty scratch target (refused inside
      :func:`restore_record_backup`) — unless ``reset_scratch`` emptied it first.
    """
    if dsn == scratch_dsn:
        raise RecordRestoreRefused(
            "the drill's scratch DSN is the source DSN — a drill must not restore "
            "over the record it is protecting"
        )
    before = record_row_counts(dsn, connect=connect)
    if not any(before.values()):
        # A refusal, NOT `RecordRestoreVerifyError`: exit 3 means "the drill ran
        # and the record disagrees with its source", and paging an operator for a
        # data-integrity incident when the truth is "you pointed this at an empty
        # record" is the false incident this class split exists to prevent.
        raise RecordRestoreRefused(
            "the record is EMPTY — every count would match an empty scratch "
            "database, so this drill would prove nothing. Measure against a record "
            "that holds data."
        )
    if reset_scratch:
        reset_record_tables(scratch_dsn, connect=connect)
    manifest = create_record_backup(
        dsn, storage, run=run, connect=connect, workdir=workdir, key=key, pg_dump=pg_dump
    )
    # The artifact's own counts, taken immediately before `pg_dump` — a strictly
    # narrower window than the vacuity count above, and the numbers the artifact
    # actually claims to contain.
    source = {t: int(n) for t, n in (manifest.get("row_counts") or {}).items()}
    if not source:
        raise RecordBackupError(
            "the manifest carries no row_counts — a drill with nothing to compare "
            "against is not a verification"
        )
    after = record_row_counts(dsn, connect=connect)
    backup_key = f"{RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{DUMP_OBJECT}"
    restored = restore_record_backup(
        storage,
        backup_key,
        scratch_dsn,
        run=run,
        connect=connect,
        workdir=workdir,
        key=key,
        pg_restore=pg_restore,
    )
    report = verify_restore(source, restored["restored"], source_after=after)
    return {
        "verdict": "RESTORED",
        "backup_id": manifest["backup_id"],
        "backup_key": backup_key,
        "created_at": manifest.get("created_at"),
        "drilled_at": datetime.now(UTC).isoformat(),
        "tables": report["tables"],
        "row_total": report["row_total"],
        "source": manifest.get("source"),
        "target": restored.get("target"),
        "scratch_reset": bool(reset_scratch),
    }
