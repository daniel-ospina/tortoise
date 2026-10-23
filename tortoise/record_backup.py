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
surface, and the artifact is portable to any Postgres **that already carries the
record's schema and the tenancy rows its foreign keys reference** — so the drill
does not have to trust the vendor's restore path in order to check the vendor's
data. The precondition is not incidental: nothing in this tree backs up the
control plane (`organizations`, `graphs`), so this artifact is a recovery of the
record into a target that still has its tenancy, NOT a recovery from project
loss on its own (see `docs/durability-posture.md`).
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
* **A connection whose ROLE cannot bypass RLS** (:func:`require_bypassing_role`,
  #9801) — R6 arms the record as `tortoise_record_app` (`NOBYPASSRLS`), and under
  that role every per-table count returns ZERO (the `*_app_tenant` policies read
  an `app.current_org_id` this leg never sets, and the migration chose silent
  reads) while `pg_dump`'s `row_security = off` default errors outright. The two
  symptoms were an artifact that never existed and a drill that called a
  populated record EMPTY. The verb therefore takes its DSN from
  `TORTOISE_RECORD_BACKUP_DSN` when set, and refuses a non-bypassing role naming
  the role rather than the data.
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

import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import re
import secrets
import socket
import stat
import subprocess
import tempfile
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, Callable  # noqa: UP035
from urllib.parse import parse_qs, unquote, urlsplit

from .config import parse_uri_userinfo
from .hosted_backup import decrypt_backup_with_rotation, encrypt_backup
from .record_projection import ROLE_POSTURE_SQL, THE_RECORD_TABLES

__all__ = [
    "DUMP_OBJECT",
    "MANIFEST_OBJECT",
    "RECORD_BACKUP_DSN_ENV",
    "RECORD_BACKUP_KEEP",
    "RECORD_BACKUP_PREFIX",
    "RECORD_DUMP_FORMAT",
    "RecordBackupError",
    "RecordBackupUnavailable",
    "RecordRestoreFailed",
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
    "require_bypassing_role",
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

#: The DSN surface the BACKUP uses, separate from `TORTOISE_RECORD_DSN` (#9801).
#:
#: The record itself connects as `tortoise_record_app` (`NOBYPASSRLS`, R6), and
#: that is the right role for every read and write the product makes. A
#: `pg_dump`/`pg_restore` session is NOT one of those: R6 names the
#: database-level backup as one of three purposes a bypassing role may serve,
#: because a backup that cannot see every tenant backs nothing up. Two roles,
#: two surfaces — a `TORTOISE_RECORD_BACKUP_DSN` that is unset keeps the old
#: behaviour (fall back to the record's DSN) rather than making the verb
#: unreachable, and :func:`require_bypassing_role` turns the resulting
#: role mismatch into a refusal that NAMES the role instead of an empty record.
RECORD_BACKUP_DSN_ENV = "TORTOISE_RECORD_BACKUP_DSN"

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

#: Wall-clock bound on each `pg_dump` / `pg_restore` subprocess (#9601 review
#: cycle 8, P2). Both legs used to be a bare `subprocess.run` with no `timeout=`:
#: `pg_dump`'s per-table `ACCESS SHARE` request queues behind any pending
#: `ACCESS EXCLUSIVE`, so an unattended cron run could wait FOREVER — producing no
#: artifact, no exit code and no alert, which contradicts this module's own
#: promise that "a backup run must not hang on a wedged database" and is the one
#: failure mode an operator cannot see. An hour is pathological for this record
#: (seconds in the measured drill); a run that reaches it is reported as a
#: RETRY-class failure, not a traceback.
RECORD_SUBPROCESS_TIMEOUT_S = 3600

#: How long `pg_dump` may wait for a table lock before giving up, in a format
#: `SET statement_timeout` accepts. Short on purpose: a dump that cannot take its
#: `ACCESS SHARE` is blocked by a long transaction, and waiting is strictly worse
#: than failing into the retry class while the bound above is still generous.
RECORD_LOCK_WAIT_TIMEOUT_S = 30

#: Bound on the scratch TRUNCATE's own lock wait and statement, in ms. The reset
#: runs `TRUNCATE`, which needs `ACCESS EXCLUSIVE`: without this it queues behind
#: any open transaction on the scratch target and hangs the job exactly as the
#: dump leg could.
RECORD_RESET_TIMEOUT_MS = RECORD_STATEMENT_TIMEOUT_MS

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

    ⛔ THE CLASS IS NOT THE PUBLISH STATE (#9601 review cycle 10, P2). A drill
    publishes before it resets and restores, so a PERMANENT fault discovered there
    (the likeliest: a stale scratch credential) raises this class while a
    complete, checksummed artifact is already durable in ``record-backups/``. The
    class still decides the CODE (``2`` — do not retry blindly), but the code does
    not decide the SENTENCE: ``backup_id``/``backup_key`` are set ONLY when the
    fault landed after the upload, so a caller can say WHICH artifact is durable
    instead of claiming nothing was backed up. ``None`` means nothing was
    published, which is the only case where that claim is true.
    """

    def __init__(
        self,
        message: str,
        *,
        backup_id: str | None = None,
        backup_key: str | None = None,
    ):
        super().__init__(message)
        self.backup_id = backup_id
        self.backup_key = backup_key


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


class RecordRestoreFailed(RecordBackupError):
    """A DRILL run failed **after** the artifact was published (#9601 cycle 3, P2).

    The drill publishes before it restores, so a post-publish failure leaves a
    complete, checksummed artifact in ``record-backups/`` — and a call reporting
    it as a plain :class:`RecordBackupError` told a scheduler "the record is NOT
    backed up" (a durability outage that does not exist) and "retry" for the
    one drill fault that retrying can never clear (a scratch target missing the
    tenancy rows its foreign keys reference).

    Carries ``backup_id``/``backup_key`` so the caller can say WHICH artifact is
    durable instead of making a claim about the record's state it cannot support.
    """

    def __init__(self, message: str, *, backup_id: str, backup_key: str):
        super().__init__(message)
        self.backup_id = backup_id
        self.backup_key = backup_key


# ── connection-string handling ──────────────────────────────────────────────


def _split_dsn(dsn: str):
    if not isinstance(dsn, str) or not dsn.strip():
        raise RecordBackupUnavailable("no record DSN — an unarmed backup is not a backup")
    # ⛔ A `#` IS A FRAGMENT TO `urlsplit` AND ORDINARY DATA TO LIBPQ (#9601 review
    # cycle 7, P0 — measured end-to-end: `…/scratchdb#?dbname=postgres` read as
    # database `scratchdb` with an empty query HERE, while libpq sent
    # `database=postgres` on the wire, so the reset TRUNCATEd the record). The two
    # parsers can never be reconciled on this character, and every coordinate this
    # module reasons about is read through `urlsplit` — so it is REFUSED, at the one
    # function both the guard and `libpq_env` use. A `#` also truncates the netloc,
    # so a password containing one could only ever be silently mangled.
    if "#" in dsn:
        raise RecordBackupUnavailable(
            "the record DSN contains `#` — libpq treats it as data and every parser "
            "here treats it as a fragment terminator, so the coordinates this "
            "module would check are NOT the ones libpq would use. Percent-encode "
            "it (%23) or move the secret to the environment."
        )
    parts = urlsplit(dsn.strip())
    if parts.scheme not in ("postgres", "postgresql"):
        raise RecordBackupUnavailable(
            "the record DSN must be a postgresql:// (or postgres://) URI — got "
            f"scheme {parts.scheme!r}. A backup of the wrong store is worse than "
            "no backup."
        )
    return parts


def require_direct_dsn(dsn: str, *, label: str = "record") -> str:
    """Refuse a POOLER DSN for a dump/restore session; return the DSN unchanged.

    The vendor's own guidance routes backup/restore to the direct connection,
    and the reason is mechanical: a transaction-mode pooler discards session
    state between transactions, so a session-scoped `SET` (and anything else a
    dump/restore relies on) is not there when the next statement runs. A dump
    taken through one can *look* fine and be short, which is the failure this
    refusal exists to prevent rather than diagnose later.

    Cheap to satisfy (a second, direct connection string) and impossible to
    notice afterwards — hence a refusal with the fix in the message.

    ``label`` names WHICH DSN was refused (`record` or `scratch`), so a drill
    told that its **scratch** connection string is a pooler does not go and check
    the source's (`#9601` review cycle 4, P2: the scratch side is now validated
    before an artifact is minted, and the message has to say which side it means).
    """
    parts = _split_dsn(dsn)
    host = (parts.hostname or "").lower()
    for marker in _POOLER_HOST_MARKERS:
        if host.endswith(marker) or f".{marker}" in host:
            raise RecordBackupUnavailable(
                f"the {label} DSN host {host!r} is a POOLER. A dump/restore "
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
            f"the {label} DSN uses port {_TRANSACTION_POOLER_PORT} (the "
            "transaction-mode pooler). A dump/restore session must use the "
            "DIRECT connection — see require_direct_dsn's docstring."
        )
    return dsn


def require_bypassing_role(
    dsn: str,
    *,
    connect: Connector | None = None,
    label: str = "record",
) -> str:
    """Refuse a dump/restore DSN whose ROLE cannot see every tenant; return the role.

    ⛔ THE OPPOSITE PRECONDITION FROM THE TENANT SCOPE, AND THE SAME FOUR
    PREDICATES (#9801). R6
    (`docs/epics/2026-10-10-7879-postgres-record/09-record-tenancy-decision.md`)
    decides that the record connects as `tortoise_record_app` — `NOBYPASSRLS`,
    not the table owner — and names the database-level backup/restore (*"R9:
    `pg_dump`/`pg_restore`"*) as one of exactly three purposes a bypassing role
    may serve. So this leg must NOT use the app DSN, and nothing enforced that:
    the verb read `TORTOISE_RECORD_DSN`, connected as the app role, and

    * the drill's `count(*)` per table returned **0** under every `*_app_tenant`
      policy (`app.current_org_id` is never set on this connection, and the R6
      migration chose *silent reads* — a missing GUC returns NULL, so rows
      VANISH rather than raising). The drill then refused with *"the record is
      EMPTY"* — pointing the operator at the DATA when the fault was the role,
      and doing it on a healthy record that was in fact fully populated;
    * backup-only mode died inside `pg_dump`, whose `row_security = off` errors
      on the first RLS table under a `NOBYPASSRLS` role, so exit `1` ("retry")
      was returned for a PERMANENT configuration fact on every scheduled
      attempt.

    Both outcomes published **no artifact** while the module read as done —
    the exact "looks present, holds nothing" failure R9 exists to remove — so
    this is a refusal at the same layer as :func:`require_direct_dsn`, with the
    ROLE named in the sentence for the same reason (the fix is a config edit,
    and the message must say which one).

    `ROLE_POSTURE_SQL` is `record_projection`'s own posture statement, not a
    second copy of the predicates: the scope requires ``bypassing is False``
    and this requires ``bypassing is True``, and one definition is what keeps
    those two answers about the same question.

    ⚠️ One residual the shared predicate carries, named rather than papered
    over: ``bypassing`` is true for MEMBERSHIP in a bypassing role
    (`pg_has_role(..., 'USAGE')`), which is what the RLS *policies* honour — so a
    role merely granted `service_role` passes this gate. `pg_dump`'s
    `row_security = off` needs the exemption on the CONNECTING role (superuser,
    `BYPASSRLS`, or the table owner), so such a role would pass here and then
    fail inside `pg_dump` (exit 1, retry). It is unreachable from this schema
    (no migration grants a bypassing role to `tortoise_record_app`), and
    tightening it here would make the gate stricter than the boundary it reads,
    so it is recorded instead of guessed at.

    Returns the connecting role's name, so a caller can say which role did the
    dump instead of leaving it to be inferred from a DSN.
    """
    connector = connect or _default_connector
    conn = _connect(connector, dsn, role=label)
    try:
        try:
            cur = conn.cursor()
        except Exception as e:  # pragma: no cover - driver-specific, same class
            raise _connection_failure(dsn, e, role=label) from e
        try:
            cur.execute(ROLE_POSTURE_SQL, (list(record_tables()),))
            row = cur.fetchone()
        except Exception as e:
            raise _connection_failure(dsn, e, role=label) from e
        finally:
            with contextlib.suppress(Exception):
                cur.close()
    finally:
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception):
            conn.close()
    if not row:
        raise RecordBackupUnavailable(
            f"the {label} connection's role could not be read — `current_user` "
            "has no pg_roles row, so its RLS posture is unknown. A backup must "
            "see EVERY tenant (the RLS policies otherwise return zero rows "
            "silently), and an unverifiable role is not a verified one; "
            "refusing rather than publishing an artifact that may be empty "
            "(#9801)"
        )
    # Positional on a plain cursor, mapping-shaped on a `RealDict` one — both
    # columns are aliased (`role`, `bypassing`) so either shape is readable.
    if isinstance(row, dict) or hasattr(row, "keys"):
        role, bypassing = row["role"], row["bypassing"]
    else:
        role, bypassing = row[0], row[1]
    if bypassing is None:
        raise RecordBackupUnavailable(
            f"the {label} connection's role posture could not be evaluated "
            f"(role {role!r}) — refusing rather than assuming it bypasses RLS "
            "(#9801)"
        )
    if bypassing is not True:
        raise RecordBackupUnavailable(
            f"the {label} connection runs as role {role!r}, which does NOT "
            "bypass RLS. The record's tables are all RLS-enabled and every "
            "`*_app_tenant` policy reads `app.current_org_id`, which this "
            "connection never sets — so every per-table count returns ZERO and "
            "`pg_dump` (whose `row_security = off` default is an ERROR for a "
            "NOBYPASSRLS role) cannot read the record at all. The record is "
            "probably not empty; THIS CONNECTION cannot see it. R9's backup is "
            "one of the three purposes R6 (epic #7879) names as a legitimate "
            "RLS bypass, so it must NOT use the app-role DSN: arm "
            f"{RECORD_BACKUP_DSN_ENV} with a role that bypasses RLS (the table "
            "owner / `postgres`, or `service_role`), separate from "
            "TORTOISE_RECORD_DSN, using the DIRECT connection "
            "(docs/infra-runbook.md §3.5 — precondition 2 is this role rule; "
            "precondition 1 is the pooler rule)."
        )
    return str(role)


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
    # ⛔ The credential is read through `tortoise.config.parse_uri_userinfo`, the
    # ONE sanctioned userinfo reader (#3039) — never `urlsplit(...).username`.
    # That guard's `continue` is file-wide, so a raw read here would either red
    # `tests/test_from_uri_userinfo.py` or force an exemption that blinds the
    # guard to the very bug class it exists to catch in this file (#3067). It is
    # also not a behaviour change: `parse_uri_userinfo` is `unquote` over the same
    # fields, and `_split_dsn` has already refused a `#` that could make the two
    # parsers disagree.
    username, password = parse_uri_userinfo(dsn.strip())
    if username:
        env["PGUSER"] = username
    if password:
        env["PGPASSWORD"] = password
    database = unquote(parts.path.lstrip("/"))
    if not database:
        # #9601 review cycle 1 (P1): the DUMP leg was the one that let a DSN
        # without a database fall through to whatever libpq defaults to (the
        # ambient `PGDATABASE`, else the user name) — a backup that succeeds
        # and contains the WRONG database. `restore_record_backup` already
        # refused this; the artifact everyone will trust did not.
        raise RecordBackupUnavailable(
            "the record DSN names no database — refusing to let libpq fall back "
            "to a default (PGDATABASE, else the user name), which would back up "
            "the wrong database"
        )
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
    """The subprocess environment: libpq's coordinates, and nothing ambient.

    #9601 review cycle 1 (P1): this used to be ``{**os.environ, **libpq_env(dsn)}``,
    so any libpq variable the DSN did not name (`PGDATABASE`, `PGUSER`,
    `PGOPTIONS`, `PGSERVICE`) was inherited from whatever shell started the
    backup — a way for the dump to reach a different database than the manifest
    describes. It is an ALLOW-LIST now: the coordinates ``libpq_env`` derived
    from the DSN, plus the minimum a client needs to run (a `PATH` to find the
    binary, `HOME` for `~/.pgpass`).
    """
    env = libpq_env(dsn)
    for passthrough in ("PATH", "HOME", "LANG", "LC_ALL", "TZ"):
        value = os.environ.get(passthrough)
        if value:
            env[passthrough] = value
    return env


# ── the identity guard: what must never be TRUNCATEd ────────────────────────


#: The libpq URI query keys that can MOVE a connection. Everything else libpq
#: accepts there (`application_name`, `keepalives*`, `target_session_attrs`,
#: `channel_binding`, `sslcert`, …) cannot change which server or database a
#: connection reaches, so a DSN carrying one is still one the identity guard can
#: reason about. The rule is DENY-BY-EFFECT (#9601 review cycle 8, P3): the previous
#: allow-list refused `?application_name=…` — the normal way an operator marks a
#: drill's sessions in the server log — with a message claiming libpq honours it.
#: `service`/`servicefile` are here because a service file can set any coordinate.
_MOVING_QUERY_KEYS = frozenset(
    {"dbname", "host", "hostaddr", "port", "service", "servicefile"}
)


def _dsn_identity(dsn_str: str, *, label: str = "the record") -> dict[str, str]:
    """The coordinates a DSN names — REFUSING one that names no database.

    Coordinates, never the literal string: two spellings of one database are the
    same database (`postgres://` vs `postgresql://`, an implicit `:5432`, a
    trailing `/`, an added `?sslmode=require`), and this is the guard whose miss
    TRUNCATEs the record.

    ⛔ AN UNNAMED DATABASE IS NOT COMPARABLE (#9601 review cycle 5, P1).
    `postgresql://user:pw@host:5432` — and the same with a trailing `/` — parses to
    an empty database name, and libpq then fills `dbname` from `PGDATABASE`, else
    the USER name. Two DSNs that look different, and that a string comparison
    therefore called different, can reach ONE database; with `--reset-scratch` the
    drill TRUNCATEs through that connection. The dump leg already refused this
    shape (`libpq_env`: "the record DSN names no database — refusing to let libpq
    fall back to a default"); the leg that DELETES had no such check.

    ⛔ THIS GUARD MUST READ THE DSN THE WAY LIBPQ READS IT (#9601 review cycle 6,
    P1). Two axes the authority/path parse above cannot see, both MEASURED as
    guard passes that then TRUNCATE the record:

    * a QUERY coordinate — `…/scratchdb?dbname=postgres` parses here to database
      `scratchdb`, while libpq resolves `dbname=postgres` (the query overrides the
      path). `host`, `hostaddr`, `port` and `service` are the same trick.
    * a MISSING host — `postgresql:///postgres` parses here to host `''`, which can
      never intersect a real record host, while libpq falls back to `PGHOST` (or a
      local socket) and reaches the record.

    Both are REFUSED rather than folded in: `refusing too much is the safe
    direction for a guard whose miss TRUNCATEs the record`. The credentials belong
    in the environment surface this module already documents, not in a query string
    the guard and `libpq_env` would parse differently.
    """
    parts = _split_dsn(dsn_str)
    try:
        port = str(parts.port or 5432)
    except ValueError as e:
        raise RecordRestoreRefused(
            f"a {label} DSN has an unreadable port: {e}"
        ) from e
    moving = sorted(
        key
        for key in parse_qs(parts.query, keep_blank_values=True)
        if key.lower() in _MOVING_QUERY_KEYS
    )
    if moving:
        raise RecordRestoreRefused(
            f"a {label} DSN passes connection coordinates in its query string "
            f"({', '.join(moving)}) — libpq honours those, so the target this "
            "guard compared is NOT the target the connection would reach. Put "
            "the coordinate in the URI itself, and the credential in the "
            "environment surface (TORTOISE_RECORD_BACKUP_DSN for a dump's source, "
            "TORTOISE_RECORD_SCRATCH_DSN for a drill's target, TORTOISE_RECORD_DSN "
            "for the record itself)."
        )
    host = unquote(parts.hostname or "").lower()
    if not host:
        raise RecordRestoreRefused(
            f"the {label} DSN names no host — libpq would fall back to PGHOST or "
            "a local socket, which can be the record. Name the server explicitly."
        )
    if "," in host or "#" in host:
        # ⛔ A MULTI-HOST LIST IS AN UNREAD COORDINATE (#9601 review cycle 7, P1 —
        # measured: `host1,host2` compared as one name whose `getaddrinfo` fails, so
        # the address fallback was silently inert, while libpq tried each host in
        # turn and succeeded on the record). `urlsplit` keeps the list whole, so no
        # comparison here can see the individual hosts it would dial. The host is
        # UNQUOTED first, because `%2C` is a comma to libpq and a literal to
        # `urlsplit` (#9601 review cycle 8 residual).
        raise RecordRestoreRefused(
            f"the {label} DSN names MORE THAN ONE host, or hides one behind an "
            f"escape ({host!r}) — libpq tries each in turn, so the server this "
            "guard compared is not the server the connection would reach. Name "
            "one host."
        )
    # ⛔ A WILDCARD ADDRESS IS NOT A PLACE (#9601 review cycle 8, P1 — measured
    # end-to-end: a scratch DSN spelled `0.0.0.0` PASSED the guard and the kernel
    # routed it to loopback, where `reset_record_tables` truncated the record; same
    # for `[::]`, `[::ffff:0.0.0.0]` and `[0:0:0:0:0:ffff:0:0]`). `0.0.0.0` is not
    # exotic — it is what every container bind and every `listen_addresses` line
    # contains, so it is the value an operator copies by accident, which is exactly
    # the accident this guard exists for. No drill target legitimately names "any".
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None and literal.is_unspecified:
        raise RecordRestoreRefused(
            f"the {label} DSN names the WILDCARD address {host!r} — 'any' is not a "
            "server: the kernel routes it to a real interface (loopback on most "
            "hosts), so this target cannot be shown to be different from the "
            "record. Name the server's actual address."
        )
    # Strip BOTH slashes, not just the leading one: `…/postgres/` names the same
    # database as `…/postgres`, and refusing too much is the safe direction for a
    # guard whose miss TRUNCATEs the record.
    database = unquote(parts.path).strip("/")
    if not database:
        raise RecordRestoreRefused(
            f"the {label} DSN names no database — libpq would fill `dbname` from "
            "PGDATABASE, else the user name, so this target cannot be shown to be "
            "a DIFFERENT database from the record. Name it explicitly."
        )
    return {"host": host, "port": port, "database": database}


def _dsn_addresses(host: str) -> frozenset[str]:
    """The addresses a name actually reaches (#9601 review cycle 3, P1).

    ``_dsn_identity`` compares the host LEXICALLY, and coordinate equality is not
    server equality across names: `localhost` and `127.0.0.1`, an IP and its DNS
    name, a CNAME and its target are ONE server spelled two ways, so a scratch DSN
    using the other spelling passed this guard — and with `--reset-scratch` the
    drill then TRUNCATEs the record's own tables and reports `RESTORED`.

    Resolving both sides closes that WITHOUT a database round trip (a connection
    here would touch the scratch before the guard has decided anything is safe, and
    the drill's "nothing was connected" assertion is part of the contract). An
    unresolvable name yields the empty set and the guard falls back to the lexical
    comparison it has always made: that is fail-open on THIS axis only, and
    deliberately so — it cannot refuse a real drill whose scratch name does not
    resolve from the host running it, while every name that DOES resolve is now
    compared by address.
    """
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return frozenset()
    # ⛔ NORMALISE THE ADDRESS, or two spellings of one address never meet (#9601
    # review cycle 7, P1 — measured: `[::ffff:127.0.0.1]` vs `127.0.0.1` compared as
    # disjoint sets while libpq dialed the same server and the reset TRUNCATEd the
    # record). An IPv4-mapped IPv6 address IS the IPv4 address; compare it as one.
    addresses: set[str] = set()
    for info in infos:
        raw = str(info[4][0])
        try:
            parsed = ipaddress.ip_address(raw)
        except ValueError:
            addresses.add(raw)
            continue
        mapped = getattr(parsed, "ipv4_mapped", None)
        resolved = mapped or parsed
        if resolved.is_loopback or resolved.is_unspecified:
            # ⛔ ONE TOKEN FOR THIS MACHINE (#9601 review cycle 8, P2 — measured:
            # `[::1]` and `127.0.0.1` produced disjoint sets for ONE dual-stack
            # server, and `0.0.0.0`/`::` produced a third, so the guard passed a
            # target that dialed the record). A wildcard routes to a real interface
            # and loopback is loopback, in either family.
            addresses.add("loopback")
            continue
        addresses.add(str(resolved))
    return frozenset(addresses)


def refuse_same_database(
    source_dsn: str, target_dsn: str, *, target_label: str = "drill's scratch"
) -> None:
    """Refuse a target that resolves to the record itself.

    ⛔ THE ONE GUARD, AT MODULE LEVEL (#9601 review cycle 5, P1). It lived as a
    closure inside :func:`run_restore_drill`, so the operation that actually
    mutates — :func:`reset_record_tables` — carried no guard of its own: it emptied
    whatever it was handed, and the safety was a property of ONE caller. A guard a
    data-loss operation does not own is a guard the next caller does not get.
    """
    source_id = _dsn_identity(source_dsn, label="record")
    target_id = _dsn_identity(target_dsn, label=target_label)
    # ⛔ AND THE AMBIENT ENVIRONMENT IS REFUSED, not just parsed (#9601 review cycle
    # 7, P2 — measured: an ambient `PGHOSTADDR` outranked the URI's host and the
    # reset reached the record). `hostaddr` beats `host`, and a service file can set
    # any coordinate, so while they are set there is no honest answer this guard can
    # give: the DSN it compared is not the DSN libpq would use.
    ambient = sorted(
        name for name in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE") if os.environ.get(name)
    )
    if ambient:
        raise RecordRestoreRefused(
            "the environment sets libpq coordinates that OUTRANK the DSN "
            f"({', '.join(ambient)}) — the connection could reach a server this "
            "guard never compared. Unset them for the drill."
        )
    same_server = source_id["host"] == target_id["host"]
    if not same_server:
        same_server = bool(
            _dsn_addresses(source_id["host"]) & _dsn_addresses(target_id["host"])
        )
    if (
        same_server
        and source_id["port"] == target_id["port"]
        and source_id["database"] == target_id["database"]
    ):
        raise RecordRestoreRefused(
            f"the {target_label} DSN resolves to the SAME database as the "
            f"source ({source_id!r}) — a drill must not restore over, or "
            "reset, the record it is protecting"
        )


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
    """Connect to exactly the coordinates `libpq_env` resolved — nothing ambient.

    ⛔ THE RAW DSN WAS THE HOLE (#9601 review cycle 7, P2 — measured: a target with
    no port plus an ambient `PGPORT` reached the record's port, and an ambient
    `PGHOSTADDR` outranked the URI's host entirely). `psycopg2.connect(dsn)` hands
    the URI to libpq, which merges every libpq variable the URI omits from the
    process environment; the dump and restore legs already refuse that (they run
    with `_subprocess_env`'s allow-list). The count leg is the MEASUREMENT the whole
    verdict rests on, so it is built from the same resolution the guard compared:
    explicit keywords, a pinned port, and no fallback to the ambient environment.
    """
    try:
        import psycopg2
    except ImportError as e:
        raise RecordBackupUnavailable(
            "psycopg2 is not installed — the record cannot be counted, so a "
            "restore cannot be verified. Install the `postgres` extra."
        ) from e
    env = libpq_env(dsn)
    kwargs: dict[str, object] = {
        "host": env["PGHOST"],
        # EXPLICIT, not `parts.port or 5432`: an absent port must not let an ambient
        # `PGPORT` decide which server this counts.
        "port": env.get("PGPORT", "5432"),
        "dbname": env["PGDATABASE"],
        "connect_timeout": RECORD_CONNECT_TIMEOUT_S,
    }
    for libpq_key, kwarg in (
        ("PGUSER", "user"),
        ("PGPASSWORD", "password"),
        ("PGSSLMODE", "sslmode"),
        ("PGSSLROOTCERT", "sslrootcert"),
        ("PGOPTIONS", "options"),
    ):
        if libpq_key in env:
            kwargs[kwarg] = env[libpq_key]
    return psycopg2.connect(**kwargs)


#: SQLSTATEs that mean "this cannot work until somebody edits a config".
#: Authentication failures and a database that does not exist are the two faults a
#: scheduler must NOT retry forever: libpq raises them on every attempt, and each
#: attempt of this verb mints (and, on a keyless box, writes a plaintext) dump.
_PERMANENT_CONNECT_SQLSTATES = frozenset({
    "28000",  # invalid_authorization_specification
    "28P01",  # invalid_password
    "3D000",  # invalid_catalog_name — the database does not exist
})


def _connection_failure(
    dsn: str, exc: BaseException, *, role: str = "record"
) -> RecordBackupError:
    """Classify a DRIVER failure as one of this module's own classes.

    ⛔ A DRIVER FAILURE IS NOT A TRACEBACK (#9601 review cycle 9, P2). ``connector(dsn)``
    stood outside every ``try`` in :func:`record_row_counts` and
    :func:`reset_record_tables`, and :func:`_default_connector`'s last statement is a
    bare ``psycopg2.connect(**kwargs)`` — so `connection refused`,
    `password authentication failed` and `database "x" does not exist` left ``main()``
    as an interpreter traceback. Two things went wrong with that, and both are the class
    this verb spent four review cycles separating:

    * **A permanent fault became "retry forever".** An uncaught exception exits ``1``,
      which this verb's own table defines as "attempted and did not happen — try
      again", on faults only a config edit clears (each attempt dumping, encrypting and
      uploading first).
    * **No documented sentence.** The runbook's table and the verb's docstring promise
      one per class; a traceback is none of them.

    So the driver's own SQLSTATE decides (``pgcode``), and the message carries only the
    exception TYPE, that SQLSTATE, and the DSN's non-secret coordinates — never
    ``str(exc)``, because a driver error can quote the connection string and this text
    reaches the log stream (:func:`libpq_env`'s whole argument).
    """
    sqlstate = getattr(exc, "pgcode", None)
    name = type(exc).__name__ + (f" {sqlstate}" if sqlstate else "")
    where = _coordinates_for_message(dsn)
    if sqlstate in _PERMANENT_CONNECT_SQLSTATES:
        return RecordBackupUnavailable(
            f"the {role} could not be reached ({name}) on {where} — a PERMANENT "
            "fault (credentials, or a database that does not exist), so retrying "
            "cannot clear it. Nothing ran and no verdict was produced."
        )
    return RecordBackupError(
        f"the {role} could not be reached ({name}) on {where} — the session could "
        "not be opened or did not survive, so nothing was measured and no verdict "
        "was produced."
    )


def _coordinates_for_message(dsn: str) -> str:
    """``user@host:port/database`` for an error sentence — never the credential.

    Deliberately NOT :func:`libpq_env`: that one includes ``PGPASSWORD`` and refuses a
    hostless DSN (raising while we are already building an error message would replace
    the real failure with a parsing one).
    """
    try:
        parts = _split_dsn(dsn)
        host = parts.hostname or "<no host>"
        try:
            port = parts.port
        except ValueError:
            port = None
        database = unquote(parts.path.lstrip("/")) or "<no database>"
        user = parse_uri_userinfo(dsn.strip())[0] or "<no user>"
        return f"{user}@{host}:{port or 5432}/{database}"
    except Exception:  # pragma: no cover - a DSN that cannot be read at all
        return "the DSN the run was given"


def _connect(connector: Connector, dsn: str, *, role: str = "record"):
    """Open a session, naming a DRIVER failure in this module's classes.

    The one gate both the count and the reset legs go through, so "the driver failed"
    can never be a traceback on one path and a documented class on the other.
    """
    try:
        return connector(dsn)
    except RecordBackupError:
        raise
    except Exception as e:
        raise _connection_failure(dsn, e, role=role) from e


def record_row_counts(
    dsn: str,
    *,
    connect: Connector | None = None,
    tables: tuple[str, ...] | None = None,
    timeout_ms: int = RECORD_STATEMENT_TIMEOUT_MS,
    role: str = "record",
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

    ``role`` names WHICH side a driver failure happened on, and it is not
    cosmetic (#9601 review cycle 11): the drill counts the SOURCE and the SCRATCH
    target through this same function, and every failure sentence said "the
    record could not be reached" — telling an operator during a scratch-side
    outage that their system of record was unreachable. The restore leg passes
    ``role="scratch target"``.
    """
    names = tuple(_require_table(t) for t in (tables or record_tables()))
    connector = connect or _default_connector
    conn = _connect(connector, dsn, role=role)
    try:
        try:
            cur = conn.cursor()
        except Exception as e:
            # `cursor()` is a round trip on some drivers, so it belongs to the same
            # class as the connect it follows: a dead session must not be a traceback.
            raise _connection_failure(dsn, e, role=role) from e
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
        except RecordBackupError:
            raise
        except Exception as e:
            # A session that dies BETWEEN statements (the server restarted, the
            # connection was reaped) is the connector's failure class too — otherwise
            # only the CONNECT is documented and the next statement is a traceback.
            raise _connection_failure(dsn, e, role=role) from e
        finally:
            cur.close()
    finally:
        # Read-only session: roll back rather than commit, so a backup run
        # cannot leave an open transaction holding locks on a live record. A
        # teardown that fails on an already-dead session leaves nothing behind
        # (nothing was committed), so it must not replace a real result — or a
        # real error — with a traceback.
        with contextlib.suppress(Exception):
            conn.rollback()
        with contextlib.suppress(Exception):
            conn.close()
    return counts


# ── dump ────────────────────────────────────────────────────────────────────


def _default_run(argv, **kwargs):  # pragma: no cover - thin subprocess seam
    # ⛔ BOUNDED (#9601 review cycle 8, P2): an unbounded client call on an
    # unattended job is a silent stall, not a failure. A caller that needs a
    # different bound passes `timeout=` and wins.
    kwargs.setdefault("timeout", RECORD_SUBPROCESS_TIMEOUT_S)
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
        # ⛔ DO NOT WAIT FOREVER FOR A TABLE LOCK (#9601 review cycle 8, P2): the
        # per-table ACCESS SHARE request queues behind any pending ACCESS
        # EXCLUSIVE, and a cron job that blocks there produces no artifact, no
        # exit code and no alert. Failing into the retry class is visible.
        f"--lock-wait-timeout={RECORD_LOCK_WAIT_TIMEOUT_S}s",
        "--file",
        str(out),
        *[f"--table=public.{t}" for t in record_tables()],
    ]
    # The DATABASE comes from PGDATABASE in the env, not from argv: see
    # `libpq_env`. `pg_dump` with no positional database uses it.
    try:
        proc = runner(argv, env=_subprocess_env(dsn), capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        # The shipped runner's wall-clock bound (see
        # `RECORD_SUBPROCESS_TIMEOUT_S`). Nothing has been uploaded, so this is
        # the RETRY class — never a traceback out of an unattended job.
        raise RecordBackupError(
            f"{pg_dump} did not finish within {RECORD_SUBPROCESS_TIMEOUT_S}s and was "
            "killed — the record was NOT backed up. The dump was blocked (a long "
            f"transaction holding a table lock, or a wedged database); it waits at "
            f"most {RECORD_LOCK_WAIT_TIMEOUT_S}s for a table lock before failing"
        ) from e
    except OSError as e:
        # #9601 review cycle 1 (P2): a missing client binary is a PERMANENT
        # install fault, not "the backup failed, retry" — the same split
        # `_default_connector` makes for the driver.
        raise RecordBackupUnavailable(
            f"{pg_dump} could not be executed ({e}) — the PostgreSQL client "
            "binaries are not installed on this box"
        ) from e
    returncode = getattr(proc, "returncode", None)
    if returncode is None:
        # #9601 review cycle 1 (P2): defaulting a MISSING exit status to 0
        # published "success" for an unknown verdict. A backup with no verdict
        # is not a backup.
        raise RecordBackupError(
            "pg_dump returned no exit status — a backup with no verdict is not a "
            "backup"
        )
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
    user = parse_uri_userinfo(dsn.strip())[0]
    if user:
        out["user"] = user
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
    counts: dict[str, int] | None = None,
    role_verified: bool = False,
) -> dict[str, Any]:
    """Dump, encrypt and upload the record; return the plaintext manifest.

    The manifest carries the **ciphertext's** sha256 (what a restore re-checks),
    the row counts measured immediately BEFORE the dump (so an operator can tell a thin
    artifact from a thin record), the table set, and the non-secret source
    coordinates. It carries no credentials and no row contents.

    ``counts`` lets the caller supply the measurement the manifest records
    instead of this function taking its own — the DRILL passes the count it read
    immediately BEFORE ``pg_dump`` runs (#9601 review cycle 4, P1; the snapshot
    instant is INSIDE the dump that follows, so this number is an upper bound the
    drill observed and never a snapshot count — #9795). Without that, the
    manifest's number came from a read taken AFTER the drill's own pre-dump read
    and BEFORE its post-dump re-count, so a record that took rows in and aged
    them out inside the dump window produced a claim no endpoint of the drill's
    window could explain — a false refusal on a healthy artifact. Using ONE
    measurement for both the artifact's claim and the drill's ceiling removes the
    gap between them; the claim is still never used as a BOUND (see
    :func:`run_restore_drill`).

    ``storage`` is the same duck-typed protocol the graph archives use
    (`upload`/`download`/`list`/`delete`), so a deployment already carrying
    `R2Storage` credentials for the graph sweep needs nothing new.
    """
    require_direct_dsn(dsn)
    # BEFORE the dump: see `require_backup_key`. Only when no key was injected —
    # a caller that passed `key=` has already satisfied the precondition.
    if key is None:
        require_backup_key()
    # ⛔ THE ROLE, BEFORE THE COUNT AND THE DUMP (#9801). Deliberately AFTER the
    # key check so a keyless box still makes no connection at all (that is the
    # invariant the arm below pins), and BEFORE the count so backup-only mode
    # cannot publish an empty artifact under the record's app-role DSN: under
    # `NOBYPASSRLS`, `pg_dump`'s `row_security = off` default is an ERROR on the
    # first RLS table and every count reads zero.
    #
    # `role_verified` is the DRILL's path: it has already run the same check on
    # the same DSN (it had to, BEFORE its first count — see
    # :func:`run_restore_drill`), and a second round trip in the middle of its
    # read sequence would be a check whose only effect is cost. It is scoped to
    # that caller: it makes the DRILL's path safe, not a hypothetical direct
    # caller's — a function that took `role_verified=True` on trust would be the
    # unwatched door, so only the drill passes it and only for the DSN it just
    # checked.
    if not role_verified:
        require_bypassing_role(dsn, connect=connect)
    if counts is None:
        counts = record_row_counts(dsn, connect=connect)
    else:
        counts = {str(t): int(n) for t, n in counts.items()}
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
        # ⛔ THE TRANSPORT IS A DOCUMENTED FAILURE CLASS, NOT A TRACEBACK (#9601 review
        # cycle 5, P2). `R2Storage.upload` raises a bare `RuntimeError`, which none of
        # the verb's handlers catch, so a bucket fault escaped `main()` as a traceback
        # instead of the exit-1 "could not perform" the runbook documents. The dump
        # object goes first and the manifest second, so a failure between them leaves
        # a ciphertext no manifest attributes — which `list_record_backups` cannot see
        # and the prune therefore cannot remove. Naming the fault here is what lets the
        # operator and the scheduler read it as the transport class it is.
        try:
            storage.upload(f"{base}/{DUMP_OBJECT}", blob, "application/octet-stream")
            storage.upload(
                f"{base}/{MANIFEST_OBJECT}",
                json.dumps(manifest, indent=2).encode("utf-8"),
                "application/json",
            )
        except Exception as e:
            raise RecordBackupError(
                f"the artifact could not be uploaded to {base} ({e}) — the record "
                "was dumped and encrypted locally but is NOT backed up. Retry the "
                "run; any object already written under this key is incomplete."
            ) from e
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
        # Write until done, never one bare `os.write` (#9601 cycle 3, P2):
        # `write(2)` may return SHORT — Linux caps a single write to a regular
        # file near 2 GiB — and the old single call dropped the remainder
        # silently, leaving a TRUNCATED dump that only `pg_restore` would notice
        # (a red drill, exit 1, on an artifact that was never whole). A loop
        # truncates nothing; a zero/negative return is a hard error rather than
        # a quiet spin.
        view = memoryview(data)
        while len(view):
            written = os.write(fd, view)
            if written <= 0:
                raise RecordBackupError(
                    f"short write to {path.name}: wrote {len(data) - len(view)} of "
                    f"{len(data)} byte(s) and the write stopped returning progress "
                    "— refusing to leave a truncated record dump behind"
                )
            view = view[written:]
    finally:
        os.close(fd)
    if stat.S_IMODE(os.stat(path).st_mode) & 0o077:
        raise RecordBackupError(f"{path} is readable beyond its owner — refusing to write a plaintext record dump there")


# ── list / prune ────────────────────────────────────────────────────────────


def _manifest_age_key(row: dict[str, Any]) -> tuple[int, float]:
    """Sort key for retention: a determinable age first (oldest → newest), then unknown.

    ``(0, timestamp)`` for a manifest whose ``created_at`` parses, ``(1, 0.0)``
    otherwise — so every artifact this module cannot date sorts after every
    artifact it can, whatever ``keep`` the caller asks for.
    """
    raw = row.get("created_at")
    if not raw:
        return (1, 0.0)
    try:
        stamp = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return (1, 0.0)
    if stamp.tzinfo is None:
        # A naive stamp is UTC here, not an error: `created_at` is stamped by
        # this module as `datetime.now(UTC).isoformat()`, and an offset-less
        # legacy value is still an older-or-newer fact worth sorting on.
        stamp = stamp.replace(tzinfo=UTC)
    return (0, stamp.timestamp())


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
    # Oldest first, and an artifact whose age cannot be READ sorts LAST (#9601
    # cycle 3): the naive `str(row.get("created_at") or "")` gives a manifest
    # with no (or unparseable) `created_at` the empty string, which sorts
    # FIRST — so `prune_record_backups` would evict the one artifact whose age
    # this code cannot determine, on the strength of a field it never read. The
    # safe direction is to delete what we can prove is old; an undatable
    # artifact survives until someone looks at it.
    rows.sort(key=_manifest_age_key)
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
        # #9601 review cycle 1 (P2): the object base comes from the KEY the
        # manifest was READ AT, never from the `backup_id` field INSIDE the file.
        # A manifest whose id disagrees with its own path (a hand-edit, a
        # duplicated id, a half-failed upload) would otherwise delete another
        # artifact's objects — possibly the newest.
        manifest_key = manifest.get("_manifest_key")
        if not manifest_key or not str(manifest_key).endswith(f"/{MANIFEST_OBJECT}"):
            log.warning(
                "record-backup prune skipped an artifact with no resolvable key "
                "(backup_id=%r) — a prune that guesses is a prune that deletes "
                "something it did not understand", manifest.get("backup_id"))
            continue
        base = str(manifest_key).rsplit("/", 1)[0]
        # ⛔ THE MANIFEST IS DELETED LAST, AND ONLY IF THE DUMP WENT (#9601 review
        # cycle 5, P3). The loop used to `continue` after a failed delete, so a
        # locked dump object was followed by a SUCCESSFUL manifest delete: the
        # artifact vanished from `list_record_backups` (which enumerates manifests)
        # while its ciphertext stayed in the bucket — unreachable, unprunable, and
        # one copy per failed delete, permanently.
        for obj in (DUMP_OBJECT, MANIFEST_OBJECT):
            key = f"{base}/{obj}"
            try:
                storage.delete(key)
            except Exception:  # a prune failure must not lose the survivor set
                log.warning("record-backup prune could not delete %s", key, exc_info=True)
                break
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


def load_verified_artifact(
    storage,
    backup_key: str,
    *,
    key: bytes | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Download → verify integrity + schema pin → decrypt; return (manifest, payload).

    Split out of :func:`restore_record_backup` so the caller can verify the
    artifact BEFORE it destroys anything. `--reset-scratch` TRUNCATEs the
    drill's target, and a truncate performed on the way to a refusal destroys
    the leftover state of the last RED drill — exactly what
    :func:`reset_record_tables` says it must never destroy (#9601 review, P2).
    Download, manifest parse, sha256 check, table set, column pin and decryption
    all live here, so nothing that can refuse runs after the reset.
    """
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
        # ⛔ NOT the bare primitive: it resolves the ACTIVE key only, so a
        # `TORTOISE_BACKUP_KEY` rotation stranded every retained record artifact
        # and reported it as tampering (#9601 review, P1). The rotation chain is
        # the graph restore path's own, shared as ONE definition
        # (`decrypt_backup_with_rotation`); a record artifact is encrypted with
        # the SAME key, so it must be restorable by the same rule.
        payload = decrypt_backup_with_rotation(blob, key=key)
    except ValueError as e:
        # A wrong key and a forged payload both surface as ValueError from
        # AES-GCM. Neither is a transport failure, so neither may be reported
        # as "retry later" — a caller must see a REFUSAL. Reaching here means NO
        # candidate authenticated, which is a genuinely wrong key or tamper.
        raise RecordRestoreRefused(
            f"the artifact could not be decrypted ({e}) — no configured backup key "
            "authenticated it (the active key or a retained previous one), so the "
            "payload is tampered with or was written under a key this box does not "
            "hold; nothing was restored"
        ) from e
    return manifest, payload


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
    artifact: tuple[dict[str, Any], bytes] | None = None,
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
    # ⛔ THE TARGET IS NAMED BEFORE IT IS READ (#9601 review cycle 5, P2). The
    # `names no database` refusal below ran AFTER `record_row_counts`, and in the
    # `--reset-scratch` path the caller has already TRUNCATEd by then — so a DSN
    # libpq would resolve to a default was connected to before anything said no.
    # Same class and same words as the check below, moved to the front.
    if not unquote(_split_dsn(target_dsn).path).strip("/"):
        raise RecordBackupUnavailable(
            "the target DSN names no database — pg_restore would have nothing "
            "to restore into"
        )
    # `artifact` is the already-verified (manifest, payload) pair. A caller that
    # had to reset the target first passes it so NO refusal can happen after
    # that reset; the default path verifies here, before touching the target.
    if artifact is None:
        manifest, payload = load_verified_artifact(storage, backup_key, key=key)
    else:
        manifest, payload = artifact

    before = record_row_counts(
        target_dsn, connect=connect, role="scratch target"
    )
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
        try:
            proc = runner(argv, env=env, capture_output=True, text=True)
        except subprocess.TimeoutExpired as e:
            # ⛔ BOUNDED like the dump leg (#9601 review cycle 8, P2). This one is
            # post-publish when called from the drill, so `run_restore_drill` wraps
            # it as `RecordRestoreFailed` and the sentence keeps the durability
            # discrimination: the artifact IS durable, the restore was not proven.
            raise RecordBackupError(
                f"{pg_restore} did not finish within {RECORD_SUBPROCESS_TIMEOUT_S}s and "
                "was killed — the artifact did NOT restore. The target was blocked "
                "(a long transaction holding a lock, or a wedged database)"
            ) from e
        except OSError as e:
            # #9601 review cycle 1 (P2): same class split as the dump leg — an
            # absent `pg_restore` is an install fault, not a restore failure.
            raise RecordBackupUnavailable(
                f"{pg_restore} could not be executed ({e}) — the PostgreSQL "
                "client binaries are not installed on this box"
            ) from e
        returncode = getattr(proc, "returncode", None)
        if returncode is None:
            raise RecordBackupError(
                "pg_restore returned no exit status — a restore with no verdict "
                "is not a verified restore"
            )
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
        "restored": record_row_counts(
            target_dsn, connect=connect, role="scratch target"
        ),
        "manifest": manifest,
    }


def compare_row_counts(
    source: dict[str, int],
    restored: dict[str, int],
    *,
    tables: tuple[str, ...] | None = None,
    source_after: dict[str, int] | None = None,
    source_high: dict[str, int] | None = None,
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

    The floor is ``min(source, source_after)`` and the ceiling is
    ``max(source, source_after, source_high)`` per table. ``source_high`` is the
    extra UPPER bound that lets the window contain a peak a *sample* can see: the
    record can take rows in and age them out between two reads, so a healthy
    artifact may legitimately carry more rows than either endpoint shows (#9601
    review cycle 4, P1). It only ever RAISES the ceiling — never the floor, and
    never the baseline — so it cannot excuse a loss. The floor is unaffected by it.

    ⛔ ``source_high`` IS NOT THE SNAPSHOT COUNT, and no arithmetic over these
    reads can be (#9601 review cycle 9, P3). It is taken immediately before
    ``pg_dump`` runs, so the artifact's snapshot instant falls between it and
    ``source_after``: a row inserted in that gap and deleted before
    ``source_after`` is in the artifact, in none of the reads, and lands ABOVE the
    ceiling — a false exit 3 on a healthy artifact. See
    :func:`run_restore_drill` and #9795 for the exact-count fix and why it is not
    free.

    The window keeps the falsifier sharp in both directions: below the floor
    is LOSS (rows that existed before the dump did not come back), and above the
    ceiling is a restore carrying rows no source read ever observed (duplication,
    a corrupt artifact, or — the residual above — a transient peak inside the
    dump). The report NAMES the moved table with both bounds.
    """
    names = tuple(tables or record_tables())
    rows: dict[str, dict[str, int]] = {}
    mismatches: list[dict[str, Any]] = []
    for table in names:
        want = int(source.get(table, -1))
        got = int(restored.get(table, -1))
        later = int(source_after.get(table, want)) if source_after is not None else want
        high = int(source_high.get(table, later)) if source_high is not None else later
        floor, ceiling = min(want, later), max(want, later, high)
        rows[table] = {"source": want, "source_after": later, "restored": got}
        if not (floor <= got <= ceiling):
            mismatches.append(
                {
                    "table": table,
                    "source": want,
                    "source_after": later,
                    "source_high": high,
                    "restored": got,
                }
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
    source_high: dict[str, int] | None = None,
) -> dict[str, Any]:
    """`compare_row_counts`, but a mismatch RAISES with the tables named.

    ``source_after`` is the post-dump re-count: pass it whenever the source can
    be written to (see :func:`compare_row_counts`), so the drill measures what
    the artifact could legitimately contain rather than what a count taken
    before the dump saw.

    ``source_high`` is the drill's own reading taken immediately BEFORE
    ``pg_dump`` runs (``cut``), which RAISES the ceiling of the accepted window —
    it never lowers it. ⚠️ It is NOT a measurement of the artifact's snapshot:
    the snapshot instant lies INSIDE the dump that follows this read, so a row
    inserted between them and deleted before ``source_after`` is in the artifact
    and in no read — that residual is #9795, not a guarantee this bound makes.
    """
    report = compare_row_counts(
        source, restored, source_after=source_after, source_high=source_high
    )
    if not report["equal"]:
        def _named(m: dict[str, Any]) -> str:
            low = min(m["source"], m["source_after"])
            high = max(m["source"], m["source_after"], m.get("source_high", m["source_after"]))
            if low == high:
                return f"{m['table']} (source {low} → restored {m['restored']})"
            return (
                f"{m['table']} (source {low}..{high} → restored {m['restored']})"
            )

        named = ", ".join(_named(m) for m in report["mismatches"])
        raise RecordRestoreVerifyError(
            f"the restored record does not match its source: {named}. The "
            "artifact is NOT proven restorable.",
            mismatches=report["mismatches"],
        )
    return report


def reset_record_tables(
    dsn: str, *, source_dsn: str, connect: Connector | None = None
) -> None:
    """EMPTY the record's tables on ``dsn`` — the scratch reset that makes a drill repeatable.

    ``source_dsn`` is REQUIRED and is the record being protected, not a courtesy
    (#9601 review cycle 5, P1): the identity refusal used to live only in
    :func:`run_restore_drill`, so this exported operation was safe by CALLER and
    emptied anything it was handed. See :func:`refuse_same_database`.

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
    # ⛔ THE RESET OWNS THE GUARD NOW (#9601 review cycle 5, P1). Two holes closed
    # in one place: a target that RESOLVES to the record (by name or by address) is
    # refused, and so is a target DSN that names NO database — libpq fills `dbname`
    # from `PGDATABASE`, else the USER name, so a bare `postgresql://user:pw@host:5432`
    # can reach the record while looking like a different database. The dump leg
    # already refused that shape; the leg that DELETES did not.
    refuse_same_database(source_dsn, dsn, target_label="reset target")
    sql = "TRUNCATE " + ", ".join(f'public."{_require_table(t)}"' for t in record_tables())
    connector = connect or _default_connector
    # The same gate as the count leg, with the role named: a login failure on the
    # SCRATCH target used to reach the operator as a traceback, and `--reset-scratch`
    # is the mode an unattended job runs.
    conn = _connect(connector, dsn, role="scratch target")
    try:
        try:
            cur = conn.cursor()
        except Exception as e:
            # `cursor()` is a round trip on some drivers, so it belongs to the same
            # class as the connect it follows.
            raise _connection_failure(dsn, e, role="scratch target") from e
        try:
            # ⛔ THE TRUNCATE IS BOUNDED TOO (#9601 review cycle 8, P2). `TRUNCATE`
            # needs `ACCESS EXCLUSIVE`, and this leg used to run it under the 5 s
            # CONNECT bound only — an open transaction on the scratch target then
            # held the reset forever, with the job producing no exit code. Both
            # bounds are session settings, so a blocked reset fails as
            # `RecordRestoreRefused` (a refusal, not a traceback).
            cur.execute(
                "SELECT set_config('statement_timeout', %s, false)",
                (str(int(RECORD_RESET_TIMEOUT_MS)),),
            )
            cur.execute(
                "SELECT set_config('lock_timeout', %s, false)",
                (str(int(RECORD_RESET_TIMEOUT_MS)),),
            )
            cur.execute(sql)
        except RecordBackupError:
            raise
        except Exception as e:
            raise RecordRestoreRefused(
                f"could not empty the scratch record ({e}) — refusing to restore "
                "into a target this run could not reset. --reset-scratch only "
                "truncates the record's own tables on the SCRATCH DSN."
            ) from e
        finally:
            cur.close()
        try:
            conn.commit()
        except Exception as e:
            # The TRUNCATE did not land: a session that died mid-commit is the same
            # class as one that never opened, not a traceback out of an unattended job.
            raise _connection_failure(dsn, e, role="scratch target") from e
    finally:
        with contextlib.suppress(Exception):
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

    The comparison is against the counts read from the SOURCE DATABASE before the
    dump (``before``) and is bounded above by the larger of two further source
    reads: ``cut`` (taken immediately BEFORE ``pg_dump`` runs) and ``after``
    (taken once the dump has finished). The record is live — it takes rows in and
    ages them out — so a healthy artifact may legitimately carry a count that no
    single sample shows, and the bound must be the maximum the drill OBSERVED
    rather than one endpoint (#9601 review cycle 4, P1: a row that landed after
    ``before`` and was deleted before ``after`` made ``max(before, after)`` refuse
    a healthy artifact with a sentence this module had already disproved — it had
    measured the higher number itself). All three reads are the DRILL's own
    measurement of the source; the manifest's claim is never read as a bound here,
    and it is not a check either — it IS ``cut``, so any ceiling built from these
    reads contains it by construction (see :func:`compare_row_counts`).

    ⚠️ ``cut`` is read immediately BEFORE ``pg_dump`` starts, so it is NOT a
    measurement of the artifact's snapshot at all: the bound is
    the largest read taken OUTSIDE the dump — not a measurement of the
    dump's own snapshot (#9601 review cycle 9, P3). The artifact's snapshot
    instant lies between the ``cut`` read and the ``after`` read, so a row
    inserted in that gap and deleted before ``after`` is in the artifact and in
    none of the three reads: it reds with exit 3 on a healthy artifact. Closing
    that needs a count read from the SAME snapshot ``pg_dump`` reads
    (``pg_export_snapshot`` + ``pg_dump --snapshot``); it is tracked in #9795
    rather than claimed closed, so treat a single red run on a live record as a
    reading to verify before it is treated as an outage.

    Refusals, in the order they matter:

    * an **empty source** — every count would match against an empty scratch
      database, so the drill would report success while proving nothing. This is
      a REFUSAL (no verdict), never a mismatch: nothing ran;
    * a scratch DSN **equal to the source** — the restore (and a
      ``reset_scratch``) would destroy the record it is supposed to protect;
    * a scratch DSN that can never be restored into (a **pooler**) — refused
      BEFORE the artifact is minted (#9601 review cycle 4, P2). The restore leg
      checks the same precondition, but by then the dump, the encryption and the
      upload have all happened, and the post-publish wrap re-classified that
      PERMANENT config fault as "retry" — so a scheduler minted a fresh artifact
      on every attempt while the runbook's exit table promised `2`;
    * a non-empty scratch target (refused inside
      :func:`restore_record_backup`) — unless ``reset_scratch`` emptied it first.

    ⚠️ There is deliberately NO "the artifact claims more rows than the source
    held" refusal. The manifest's ``row_counts`` is ``cut`` — the drill's own
    measurement, handed to :func:`create_record_backup` — so comparing it against
    a ceiling of ``before``/``cut``/``after`` could never fire; listing it as a
    refusal promised a run that cannot happen (#9601 review, P2). The verdict's
    bound is the comparison of the RESTORED counts against the source reads.

    A failure AFTER the artifact is published raises :class:`RecordRestoreFailed`,
    not a bare :class:`RecordBackupError` (#9601 review cycle 3, P2): the drill
    publishes before it restores, so at that point the record IS backed up and
    only the restore is unproven. A caller told otherwise reports a durability
    outage that does not exist. A PERMANENT config fault in the same region keeps
    its :class:`RecordBackupUnavailable` class (exit `2`, so nothing is retried
    blindly) but is re-raised carrying the published ``backup_id`` — the class
    says how to react, the identity says what is already durable (#9601 review
    cycle 10, P2).

    ⛔ THE ARTIFACT IS CUT BEFORE ANY RESET, and the identity test is COORDINATES,
    not string equality (#9601 review cycle 1, P0). Both halves of the old shape
    were a data-loss path: ``dsn == scratch_dsn`` misses every equivalent spelling
    of one database (``postgres://`` vs ``postgresql://``, an implicit ``:5432``,
    an added ``?sslmode=require``, a trailing ``/``), and the reset ran BEFORE
    :func:`create_record_backup` — so a drill pointed at the live record through
    any of those spellings TRUNCATEd it, dumped the now-empty result, verified
    zeros against zeros, and printed ``RESTORED`` with exit 0.
    """
    # Coordinates, never the literal string: two spellings of one database are
    # ⛔ ONE GUARD, AT MODULE LEVEL, AND THE RESET OWNS IT TOO (#9601 review
    # cycle 5, P1). The two closures that used to live here are `_dsn_identity` /
    # `_dsn_addresses`, and the comparison is `refuse_same_database` — because
    # `reset_record_tables`, the operation that actually TRUNCATEs, could not reach
    # a closure: it emptied whatever DSN it was handed, and the safety was a
    # property of THIS caller.
    refuse_same_database(dsn, scratch_dsn)
    # ⛔ THE ROLE GATE COMES BEFORE THE FIRST COUNT (#9801), which is the whole
    # point of its position: the counts below are the drill's evidence, and under
    # the app-role DSN R6 mandates they return ZERO for every table — after which
    # the drill's own refusal says "the record is EMPTY" about a record that is
    # fully populated. One round trip, and the sentence names the role instead.
    require_bypassing_role(dsn, connect=connect)
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
    # ⛔ THE SCRATCH SIDE'S PRECONDITIONS ARE CHECKED HERE, BEFORE THE ARTIFACT
    # EXISTS (#9601 review cycle 4, P2). `restore_record_backup` and
    # `reset_record_tables` each re-check them, but they run AFTER the upload, and
    # the wrap below turns a `RecordBackupUnavailable` raised there into
    # `RecordRestoreFailed` = exit 1 = "retry". A pooler scratch DSN is a
    # PERMANENT config fault: retrying it dumps, encrypts, uploads and prunes
    # again, forever, on a schedule whose exit table promises 2. Nothing has been
    # read from the source beyond a count and nothing has been dumped, so
    # refusing here costs one count and mints nothing.
    require_direct_dsn(scratch_dsn, label="scratch")
    # ⛔ THE CUT MEASUREMENT — the artifact's own claim, taken by the DRILL. It is
    # read once and used for BOTH the manifest's `row_counts` and the upper bound
    # of the comparison, so the two cannot come from different instants: reading
    # it inside `create_record_backup` left a gap in which a row could land after
    # this count and before that one, which is the same false refusal one
    # millisecond narrower (#9601 review cycle 4, P1). It is read immediately
    # BEFORE `pg_dump` runs, which is why it cannot be called the artifact's
    # snapshot count: the snapshot instant is inside the dump that follows, and a
    # row inserted in between and aged out before the post-dump read is above
    # every read this drill takes (#9601 review cycle 9, P3 — see #9795). It is an
    # upper bound only — never a floor, never the baseline — so a manifest that
    # inflates its own numbers cannot widen acceptance (the value bounded here is
    # the one the drill measured, not the one the artifact declares).
    cut = record_row_counts(dsn, connect=connect)
    # ⛔ CUT THE ARTIFACT FIRST. Every statement below this line mutates the
    # SCRATCH target; nothing before it may depend on the scratch being empty.
    manifest = create_record_backup(
        dsn,
        storage,
        run=run,
        connect=connect,
        workdir=workdir,
        key=key,
        pg_dump=pg_dump,
        counts=cut,
        role_verified=True,
    )
    # The post-dump re-count (`after`) is read INSIDE the post-publish leg below;
    # see the comment on the first statement of that `try` for why its position is
    # part of the fix.
    backup_key = f"{RECORD_BACKUP_PREFIX}{manifest['backup_id']}/{DUMP_OBJECT}"
    # ⛔ FROM HERE THE ARTIFACT IS DURABLE, so a failure is no longer "the backup
    # did not happen" (#9601 review cycle 3, P2). Re-raising it as a plain
    # RecordBackupError made the verb print "the record is NOT backed up — retry"
    # while a complete, checksummed artifact sat in `record-backups/`, and it told
    # an operator to retry the one class here that retrying cannot clear (a
    # scratch target missing the tenancy rows its FKs reference). The refusals,
    # the verify verdict AND `RecordBackupUnavailable` keep their own classes: they
    # are NOT "attempted and did not happen", and the caller maps them to exit 2 /
    # exit 3 (#9601 review cycle 4, P2 — the permanent config fault was the one
    # class swallowed here, so a pooler scratch DSN told a scheduler to retry).
    try:
        # The POST-DUMP RE-COUNT. Reading it here — after the artifact is durable —
        # is the fix for #9601 review cycle 2 (P1): `claimed` (the manifest's own
        # counts) is measured just before `pg_dump` starts, i.e. AFTER `before`, so on a
        # live append-only record `claimed > before` is the ordinary concurrent-insert
        # signature — most of all for `journal_event`, which gets a row on every
        # capture turn. Comparing the later read against the earlier one therefore
        # red a HEALTHY backup on the documented daily schedule, on a run whose
        # restore had not started, and — because `RecordRestoreVerifyError` carries
        # no `mismatches` here — printed `{"verdict": "MISMATCH", "mismatches": []}`:
        # a loss verdict naming no loss. The check that survives is the restore
        # comparison itself (source reads vs RESTORED counts); the old "the
        # artifact may not claim more rows than the source held" bound was
        # tautological on this path and is gone — see the note below.
        after = record_row_counts(dsn, connect=connect)
        # The source numbers are the ones READ FROM THE SOURCE DATABASE (`before`),
        # never the artifact's own claim about itself (#9601 review cycle 1, P2):
        # `compare_row_counts` accepts a window `[min(source, after), max(...)]`, so a
        # manifest whose `row_counts` were zeroed widened acceptance to `[0, after]`
        # and a short artifact restored green — while this function's own docstring
        # promises it "never trusts the writer's own claim about what it wrote". The
        # manifest's claim is never an input to the verdict.
        source = {t: int(n) for t, n in before.items()}
        if not source:
            raise RecordBackupError(
                "the source held no rows when they were read — a drill with nothing "
                "to compare against is not a verification"
            )
        # ⛔ THERE IS NO "the artifact claims more rows than the source held"
        # CHECK, because it cannot fire — and documenting it as a refusal was
        # worse than not having it (#9601 review, P2). The manifest's
        # `row_counts` IS `cut`, the drill's own measurement, passed into
        # `create_record_backup(counts=cut)`; a ceiling built from
        # `max(before, cut, after)` therefore CONTAINS the claim by construction,
        # so `want > ceiling` was unsatisfiable and the branch was dead. What
        # bounds ACCEPTANCE is the restore comparison (`verify_restore(source,
        # restored, source_after=after, source_high=cut)`): its window is built
        # from the source's own reads, and the manifest's claim never widens it.
        # A manifest that inflates its numbers is caught by the restored counts
        # mismatching, which is the verdict that actually measures the data.
        #
        # ⛔ VERIFY THE ARTIFACT BEFORE THE RESET. `--reset-scratch` TRUNCATEs
        # the drill's target; download, manifest parse, sha256, the table set,
        # the column pin and decryption each can REFUSE, and a truncate performed on the way
        # to such a refusal destroys the leftover state of the last RED drill
        # that `reset_record_tables` says it must never destroy (#9601 review,
        # P2). Loading the verified artifact here leaves NO step that can refuse
        # on the ARTIFACT — integrity, manifest shape, the table set, the column
        # pin or decryption — between the reset and `pg_restore` (#9601 review
        # cycle 11, P3: the older "no refusing step at all" was too strong, see
        # the residual named below).
        #
        # ⚠️ The residual is the TARGET side, and it is named rather than
        # claimed away: `restore_record_backup` still re-counts the target and
        # writes the payload file before `pg_restore` runs, and either can fail
        # (a scratch session that dies between the truncate and this read, a
        # target written to in that window, a short payload write). Those
        # destroy the previous drill's leftover rows exactly as a post-reset
        # artifact refusal would. The window is one successful connect wide, and
        # closing it needs the emptiness verdict to move BEFORE the truncate —
        # not done here.
        manifest, payload = load_verified_artifact(storage, backup_key, key=key)
        if reset_scratch:
            reset_record_tables(scratch_dsn, source_dsn=dsn, connect=connect)
        restored = restore_record_backup(
            storage,
            backup_key,
            scratch_dsn,
            run=run,
            connect=connect,
            workdir=workdir,
            key=key,
            pg_restore=pg_restore,
            artifact=(manifest, payload),
        )
    except (RecordRestoreRefused, RecordRestoreVerifyError):
        # Refusals and verdicts keep their own classes untouched: a refusal is not
        # an artifact-state claim at all.
        raise
    except RecordBackupUnavailable as e:
        # `RecordBackupUnavailable` is a PERMANENT config fault and keeps its own
        # class (#9601 review cycle 4, P2): it is not "the drill ran and did not
        # finish", it is "this box cannot run it", and the verb maps it to exit 2.
        # Re-wrapping it as `RecordRestoreFailed` (exit 1) told a scheduler to retry
        # a pooler DSN forever — minting a fresh artifact on every attempt.
        #
        # ⛔ BUT IT MUST CARRY THE PUBLISHED ARTIFACT (#9601 review cycle 10, P2).
        # This arm is REACHED FROM THE POST-PUBLISH LEG (a stale scratch credential
        # on the reset, `pg_restore` absent, a scratch DSN that names no database),
        # so by construction `create_record_backup` has already returned and the
        # artifact is durable. Re-raised bare, the verb's exit-2 handler told the
        # operator "nothing was backed up" — the same false durability outage the
        # exit-1 fix above exists to remove, for a likelier fault. Same class, same
        # exit code; the artifact's identity is what the sentence needs.
        raise RecordBackupUnavailable(
            str(e), backup_id=manifest["backup_id"], backup_key=backup_key
        ) from e
    except RecordBackupError as e:
        raise RecordRestoreFailed(
            str(e), backup_id=manifest["backup_id"], backup_key=backup_key
        ) from e
    # ONE upper bound for the verify window (#9601 review cycle 4, P1): `cut`, the
    # drill's own reading taken immediately BEFORE `pg_dump` runs (never the
    # artifact's snapshot count — #9795), must be passed as ``source_high`` — without it a
    # healthy artifact whose rows landed after `before` and aged out before
    # `after` was verified against `[after, before]` alone and red as a data-loss
    # incident on a run that lost nothing.
    report = verify_restore(source, restored["restored"], source_after=after, source_high=cut)
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
