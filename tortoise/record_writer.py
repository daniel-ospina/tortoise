"""The record-write seam — the one production boundary into the Postgres record.

Epic **#7879** (the Postgres record), scope item 2 — *"The write path. One path:
the truth first, then the projection."* Issue **#9187** (parent **#9078**).

WHY THIS MODULE EXISTS. `tortoise/record_projection.py` is complete and proven:
a declarative mutation algebra, one registry entry per record type, a total plan
and an idempotent executor. **Nothing in production called its write half.**
Measured at `ecc7bc8ca` (the `f96a18da1` measurement in #9187 re-run):

    rg -n "record_projection" -g '!tests/**' -g '!docs/**' tortoise/ supabase/
      cache_projection.py:86,411,442   the RECORD→CACHE reader (Interface B)
      projection/entities.py:1512      a comment
      change_feed.py:245               a comment
      consistency.py:926,1107,2693     the JOURNAL's applied-seq state, not the record
      supabase/tests/pglite/validate.mjs:656   the TEST harness

⇒ the only importer of the *write* half is the PGlite test harness. This module
is the missing boundary: it takes the journal records a caller has just
appended, resolves the record's connection, and drives
`record_projection.replay(...)` on **the caller's transaction**. Wiring a real
caller then becomes a one-line act (#9188) instead of a cross-system change.

WHAT THIS MODULE IS NOT. It is not a second fold. `record_projection` is the ONE
declaration of journal→record; the seam *calls* it and adds nothing to the
mutation set. It is not a driver either: `psycopg2` is imported lazily inside
`_default_connect`, only when the seam is armed and no connection was injected,
so importing this module costs no driver and the DB-free tests stay DB-free.

──────────────────────────────────────────────────────────────────────────────
THE FIVE DECISIONS #9187 ASKED THE SCOPE STAGE TO RESOLVE — AND THE ANSWER
──────────────────────────────────────────────────────────────────────────────

**1. Which connection, and whose DSN.** The record's OWN DSN —
`TORTOISE_RECORD_DSN` — never the control-plane client (`tortoise/
supabase_control.py`, `cp`). Three reasons, any one of which decides it: `cp`
speaks PostgREST over HTTP and `replay()` needs a DB-API cursor with `%s`
parameters; `cp` serves *auth* (orgs, api_keys, memberships) and the record is a
durable append-only layer of the *data* plane — §11.3 of
`docs/architecture/STORAGE-ARCHITECTURE.md` already names the two things both
called "connections"; and the record carries its OWN append-only migration
history (`supabase/migrations/20261012*_record_*.sql` plus the forward record
migrations), so it is a different destination even when it happens to share a
server. A caller that already owns a connection injects it (`conn=`), which is
how a chokepoint can put the journal append and this projection on ONE
transaction.

**2. The arming surface — `TORTOISE_RECORD_DSN`, default OFF.** Presence-based,
because for a URI "present or absent" *is* the question (`tortoise/env_truthy.py`
V5), and because the sibling optional-Postgres seam on this tree already arms
that way (`TORTOISE_AUDIT_DSN`, `tortoise/audit_events.py`). Unset or blank ⇒
**`NOT_ARMED`**: zero statements, no connection attempt, no exception, and the
plan is never even materialized (a deployment that is not writing the record
must not pay the fold, and must not fail a capture over a record type it is not
writing). Default off is not a preference: §2.0 of the storage architecture says
the cache stays fully populated while the sequence lands, so capture must be
byte-identical for every deployment that has not armed the record.

The outcome is a *named return value*, never a log line — `RecordWriteOutcome`,
with `.status` giving the recorded term from `tortoise/status_vocabulary.py`
(`unconfigured` = "no store / endpoint / key is configured — off by policy").
ONE vocabulary: the seam consumes the recorded terms through that module's own
`classify(...)`, it does not mint a fifth one. A caller distinguishes
not-armed from success with `write.applied`, and from a *failure* because a
failure raises.

A configured-but-MALFORMED DSN is not "unarmed" — it raises. Silently reading a
typo as "not configured" is the failure mode this whole seam exists to prevent,
wearing a passing test. The error carries an 8-hex fingerprint of the value, not
the value (#2903/#2796: a malformed DSN is still secret material).

**3. Armed but unreachable ⇒ the write FAILS (`RecordWriteError`).** Not a
counted backlog. §3/§17.1 make the record the truth, so a capture that reports
success while the truth is unreachable claims a durability that did not happen;
and swallowing the failure would make "configured but down" (`degraded`)
indistinguishable from "never configured" (`unconfigured`) on the write side —
the exact collapse `status_vocabulary` exists to keep apart, which is why
`status` derives from `classify(configured=…, reached=…)` and not from a
hand-written word. A counted backlog is an *outbox*: a durable queue with its
own ordering, retry and watermark semantics. That is a design with its own state
machine; if #9188 wants one it decides one there. A seam cannot half-own it,
and half-owning it is how a record silently loses rows.

**4. Per-record vs batch ⇒ ONE batch per call.** Forced by the position, not
preferred. `replay_plan` numbers records by their index in the batch, so one
replay per record would number every record `seq=1` and the
`(org_id, graph_id, seq)` idempotency key would make every record after the
first `ON CONFLICT DO NOTHING` — the position log would then claim consumption
the record never had. A batch is one statement set, one connection, one
transaction boundary.

**5. The position watermark ⇒ the journal's own offset, owned by the CALLER.**
`first_seq` (default `1`) is the journal position of `records[0]`, passed
through to `record_projection.replay(..., start_seq=first_seq)` (an additive,
default-preserving parameter — see that function's "THE POSITION WATERMARK").
The seam does not mint or cache a counter: an in-process counter is wrong the
moment a second process writes, and a per-record source of truth already
exists — the append-only journal. A re-run after a crash re-derives the same
offset from the same durable facts, so nothing renumbers.

**THE TRANSACTION.** The seam never calls `commit()` and never calls
`rollback()` (pinned by a test whose fake connection counts both and asserts
they are zero). When the caller injects `conn=`, the boundary is unambiguously
theirs. When the seam opens the connection itself it RETURNS it on the outcome
(`RecordWrite.connection`) so the caller can commit or close it — a seam that
opened its own connection and committed it would commit the projection ALONE,
which is the dual write §3 forbids.

**THE FAILURE PATH DISPOSES OF WHAT THE SEAM OPENED.** A connection the seam
opened and could not use is CLOSED before `RecordWriteError` is raised: the
caller never receives that handle, so it has nothing to roll back and no way to
close it, and the raising traceback would otherwise keep the handle (its aborted
server-side transaction and backend slot) alive for as long as the exception is
held. An INJECTED connection is never touched — it is the caller's, and its
transaction stays open and unrolled-back on failure.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from . import record_projection, status_vocabulary

__all__ = [
    "RECORD_DSN_ENV",
    "RecordWrite",
    "RecordWriteError",
    "RecordWriteOutcome",
    "RecordWriter",
    "record_dsn",
    "write_records",
]

#: The arming key. Present ⇒ armed; unset/blank ⇒ ``NOT_ARMED``. See the module
#: docstring, decision 2 — and note it is a DSN, not a boolean, so a second
#: "enabled" flag would be a second thing to get out of step.
RECORD_DSN_ENV = "TORTOISE_RECORD_DSN"

#: The DSN schemes a DB-API Postgres driver accepts. A value outside this set is
#: a *misconfiguration*, never an unarmed state.
_DSN_SCHEMES: tuple[str, ...] = ("postgresql://", "postgres://")


class RecordWriteError(Exception):
    """The record write was ARMED and did not happen.

    One catchable class for every armed-path failure — a refusal raised by the
    projection before the first statement (totality), a Postgres error during
    execution, a connection that could not be opened, or a malformed DSN — so a
    caller has exactly one thing to catch in order to do the one right thing
    next: dispose of the transaction, and never claim durability.

    When the seam OPENED the connection and the write failed, that connection
    has already been closed by the seam (a close aborts its transaction), so
    there is nothing left for the caller to dispose of. An INJECTED connection
    is left open, uncommitted and not rolled back — rolling it back is the
    caller's act, since only the caller knows what else is in it.

    ``__cause__`` carries the underlying error unchanged. In particular a
    totality failure keeps its own type:
    ``except RecordWriteError as exc: isinstance(exc.__cause__,
    record_projection.UnknownJournalRecordType)``. The seam wraps rather than
    re-raises because the cause is what a human reads.
    """


class RecordWriteOutcome(StrEnum):
    """What the seam did — two values, because a failure does not return.

    ``APPLIED``    the plan was materialized and every statement executed on
                   the caller's connection. The seam has NOT committed.
    ``NOT_ARMED``  no record DSN is configured, so nothing was written — zero
                   statements, zero connection attempts, no exception. This is
                   *off by policy*, and it is the write-side spelling of the
                   recorded term :data:`status_vocabulary.STATUS_UNCONFIGURED`;
                   read that term off :attr:`RecordWrite.status` rather than
                   comparing this value to a status word.
    """

    APPLIED = "applied"
    NOT_ARMED = "not-armed"


@dataclass(frozen=True)
class RecordWrite:
    """What a call to :meth:`RecordWriter.write` consumed. A report, never a
    substitute for the failure — a failed write raises instead of returning."""

    outcome: RecordWriteOutcome
    #: Journal records handed to the seam (0 for a not-armed call).
    events: int = 0
    #: Record mutations executed. Excludes the journal-position writes, matching
    #: ``record_projection.ReplayReport.statements``.
    statements: int = 0
    #: Journal position of ``records[0]`` on an APPLIED call.
    first_seq: int = 1
    #: Journal position of the LAST record consumed (``first_seq - 1`` when
    #: nothing was consumed — an empty batch, or a not-armed call), so a caller
    #: can advance its offset without re-deriving. Never a rewind: it is always
    #: derived from the caller's own ``first_seq``.
    last_seq: int = 0
    #: The connection the seam OPENED, or ``None`` when the caller injected one
    #: (theirs to close) or the call was not armed. When set, the caller owns the
    #: transaction: this seam never commits and never rolls back.
    connection: Any | None = field(default=None, repr=False)

    @property
    def applied(self) -> bool:
        """Did the record actually get written on this call?

        The one check a caller needs. ``False`` means *nothing was written* —
        it is not an error (an unarmed deployment is a supported state), but a
        caller that needs the record to exist must not read it as success.
        """
        return self.outcome is RecordWriteOutcome.APPLIED

    @property
    def status(self) -> str:
        """The recorded status term for this outcome (``available``/``unconfigured``).

        Derives through :func:`status_vocabulary.classify` — the ONE mapping of
        observable facts onto the recorded vocabulary — so the write side cannot
        drift from the read side's terms (#9187 decision 2/3).
        """
        if self.outcome is RecordWriteOutcome.NOT_ARMED:
            return status_vocabulary.classify(configured=False, reached=False)
        # APPLIED ⇒ `available` BY CONSTRUCTION, not from an observation: the
        # classifier's rule is `hits is None or hits > 0` → available, so no
        # statement count this call could supply would move the term. Passing
        # `hits=` here would be a dead argument claiming a distinction the
        # classifier does not make.
        #
        # An APPLIED EMPTY batch therefore reports `available` too, and that is
        # deliberate: nothing was REQUIRED of the store (no connection, no
        # statement — `test_an_empty_batch_is_applied_without_opening_a_
        # connection` pins the absence), so this is not a claim that it was
        # probed. The alternative — `empty` — would assert a read returned no
        # rows, which is equally untrue.
        return status_vocabulary.classify(configured=True, reached=True)


def record_dsn(explicit: str | None = None) -> str | None:
    """The armed DSN, or ``None`` when the record is not armed.

    ``explicit`` wins over the environment (an injected value makes the DB-free
    pin possible). Unset/blank ⇒ ``None``. A value that is not a Postgres DSN
    raises :class:`RecordWriteError` carrying only a fingerprint of the value —
    the same secret-hygiene rule ``tortoise/audit_events.py`` applies to
    ``TORTOISE_AUDIT_DSN`` (#2903/#2796).
    """
    raw = explicit if explicit is not None else os.environ.get(RECORD_DSN_ENV)
    dsn = "" if raw is None else str(raw).strip()
    if not dsn:
        return None
    if not dsn.startswith(_DSN_SCHEMES):
        got = hashlib.sha256(dsn.encode()).hexdigest()[:8]
        raise RecordWriteError(
            f"{RECORD_DSN_ENV} must start with postgresql:// or postgres:// "
            f"(got <{got}>...) — a configured-but-unusable record DSN is a "
            f"misconfiguration, not an unarmed record"
        )
    return dsn


def _default_connect(dsn: str) -> Any:
    """Open the record's connection. The ONE place a driver choice lives.

    Lazily imported so importing this module needs no driver (the DB-free pin,
    and ``psycopg2`` is an optional extra: ``.[postgres]``). ``autocommit`` is
    deliberately NOT touched: the transaction boundary belongs to the caller,
    and turning autocommit on here would commit each statement of a projection
    that §3 requires to be atomic with the journal append.
    """
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - exercised by install lanes
        raise RecordWriteError(
            "the record is armed but no Postgres driver is installed — install "
            "the 'postgres' extra (pip install -e '.[postgres]')"
        ) from exc
    return psycopg2.connect(dsn)


@dataclass
class RecordWriter:
    """The record-write seam. Cheap to construct; holds no connection.

    ``dsn``      the armed DSN. ``None`` (the default) resolves
                 :data:`RECORD_DSN_ENV` on every call, so a process started
                 before the record was armed picks it up rather than staying
                 silently unwritten for its lifetime.
    ``connect``  the driver hook — ``callable(dsn) -> connection``. Injected by
                 every DB-free test; defaults to :func:`_default_connect`.
    """

    dsn: str | None = None
    connect: Callable[[str], Any] | None = None

    @property
    def armed(self) -> bool:
        """Is a record DSN configured? Raises on a malformed one (never false)."""
        return record_dsn(self.dsn) is not None

    def write(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        org_id: str,
        graph_id: str,
        first_seq: int = 1,
        conn: Any | None = None,
    ) -> RecordWrite:
        """Apply `records` to the record, on a transaction the CALLER owns.

        :param records: the journal records the chokepoint has just appended —
            ONE contiguous segment, in journal order. Empty is legal and cheap.
        :param first_seq: the journal position of ``records[0]``. The caller's
            fact (see the module docstring, decision 5). A position below 1 is
            refused by the projection (`RecordProjectionError` as the cause) —
            on the ARMED path only: an unarmed call materializes no plan, so it
            accepts what it was given and reports it back unchanged.
        :param conn: an already-open connection to write on. Inject it to span
            the journal append and this projection in ONE transaction; the seam
            then neither opens nor closes nor commits it.

        :raises RecordWriteError: the record was ARMED and the write did not
            happen (malformed DSN, unreachable record, totality refusal, or a
            Postgres error). With an injected ``conn=`` the caller's transaction
            is still open, uncommitted and NOT rolled back — rolling back is the
            caller's act, because only the caller knows what else is in that
            transaction. When the seam opened the connection itself it CLOSES it
            before raising (closing aborts the transaction), because the caller
            never receives that handle: there is nothing it could roll back.

        NOT ARMED ⇒ returns ``RecordWrite(NOT_ARMED)`` and touches nothing: no
        connection, no statements, no fold materialization, no exception. It is
        never a silent no-op, because the return value names it.
        """
        dsn = record_dsn(self.dsn)          # raises on a malformed value
        if dsn is None:
            # `last_seq` is `first_seq - 1` here too, not the dataclass default:
            # a caller advancing `next_offset = result.last_seq + 1` must not
            # rewind its watermark to 1 because nothing was written.
            return RecordWrite(outcome=RecordWriteOutcome.NOT_ARMED,
                               first_seq=first_seq, last_seq=first_seq - 1)

        if not records:
            # Nothing to write: do not open a connection to say so. (An armed
            # caller with an empty segment has nothing dropped — nothing existed.)
            return RecordWrite(outcome=RecordWriteOutcome.APPLIED,
                               first_seq=first_seq, last_seq=first_seq - 1)

        opened: Any | None = None
        target = conn
        if target is None:
            opener = self.connect or _default_connect
            try:
                target = opener(dsn)
            except RecordWriteError:
                raise
            except Exception as exc:
                # Armed-but-unreachable MUST NOT read as unarmed (decision 3).
                raise RecordWriteError(
                    f"the record is armed but its connection could not be "
                    f"opened ({type(exc).__name__})"
                ) from exc
            opened = target

        try:
            report = record_projection.replay(
                records, target, org_id=org_id, graph_id=graph_id,
                start_seq=first_seq,
            )
        except Exception as exc:
            # Totality (an undeclared/refused record type raises before the
            # first statement) and execution failures share one catchable class
            # and one required caller action: roll back, do not claim durability.
            #
            # A connection the SEAM opened is disposed of here: the caller never
            # receives it, so nobody else can close it, and the traceback below
            # would otherwise keep `target` (its aborted transaction, its backend
            # slot) alive as long as the exception is held. Closing aborts the
            # transaction, which is the only outcome left once it is invisible.
            # An INJECTED connection is never touched — it is the caller's.
            if opened is not None:
                # A close failure must not mask the cause — the write failure is
                # what the caller has to report, and a close that raised would
                # replace it with a less informative error.
                with contextlib.suppress(Exception):
                    opened.close()
            raise RecordWriteError(
                f"the record write failed after {len(records)} journal "
                f"record(s) were materialized from position {first_seq} "
                f"({type(exc).__name__})"
            ) from exc

        return RecordWrite(
            outcome=RecordWriteOutcome.APPLIED,
            events=report.events,
            statements=report.statements,
            first_seq=first_seq,
            last_seq=first_seq + len(records) - 1,
            connection=opened,
        )


def write_records(
    records: Sequence[Mapping[str, Any]],
    *,
    org_id: str,
    graph_id: str,
    first_seq: int = 1,
    conn: Any | None = None,
    writer: RecordWriter | None = None,
) -> RecordWrite:
    """One-call form of :meth:`RecordWriter.write` — the chokepoint's entry point.

    Same contract, same outcomes, same failures. Constructing a
    :class:`RecordWriter` once and reusing it is equivalent and avoids
    re-resolving the DSN per call; this function exists so a caller with no
    seam object to hold does not have to invent one.
    """
    return (writer or RecordWriter()).write(
        records, org_id=org_id, graph_id=graph_id, first_seq=first_seq, conn=conn,
    )
