"""The RECORD write path — the production caller the record never had (#9078).

WHY THIS MODULE EXISTS. `tortoise/record_projection.py` implements Interface A
(journal → Postgres, replayable and idempotent) and is proven against a real
Postgres in the PGlite harness — and until this module it had **zero production
callers**, so "Postgres is the system of record" (§2.0) was a claim about a test
fixture. The capstone measured exactly that (`#7950`, S2 **NOT-GREEN**):
`record_projection`, `read_record_view`, `rebuild_from_record` and `reconcile`
are imported by tests and by nothing else.

THE DOOR SET — the three live journal append sites that call this module, and
the ONLY places a record enters Postgres today. A fourth door that appends a
journal record MUST join them (`python tools/record_wiring_check.py` counts the
`record_writer.project(...)` call sites, so an unwired door is visible):

* ``TortoiseSDK._emit_event`` (``tortoise/sdk.py``) — every SDK write, and the
  capture lane;
* ``EventAPI._emit`` (``tortoise/api.py``) — the ingest/docs-miner/indexer/MCP
  write seam;
* ``ConversationMiner._emit_event`` (``tortoise/mining.py``) — the miner's own
  envelope, which NESTS its node under ``event`` (the one nested carrier
  ``record_projection`` accepts).

WHERE IT SITS — and the order is the whole design (§17.1, §2.0):

    journal append   ← the COMMIT POINT (`TortoiseSDK._emit_event`)
    record write     ← THIS module
    cache fold       ← `proj.apply`, inside `_live_write_scope`

The journal is the log of what happened; the Postgres record is the truth STATE
it describes; the FalkorDB graph is derived from both. So the record is written
*after* the append and *before* the fold. Nothing here writes the cache, and
nothing here writes the journal.

⛔ THE ENGINE IS NOT RESTATED. Every statement this module emits comes from
`record_projection.mutations_for` — the same function a rebuild-from-journal
replay uses. A hand-written INSERT here would be a second declaration of the
mapping, and the second declaration is the one that goes stale: the record's
totality/idempotence pins cannot see a caller that bypasses the engine.

⚠️ ORDERING GIVES US A FAILURE MODE, AND IT IS NAMED RATHER THAN HIDDEN: the
append has already COMMITTED when this module runs, so a projection that fails
cannot un-commit the write. It is therefore fail-soft — the capture succeeds,
the journal keeps the record, and the record LAGS. `replay` heals it from the
journal. What must never happen is the lag being silent, so every failure
increments `monitoring.record_record_write_failure(reason)` (served by
`monitoring.metrics()` as `record_write_failures`) and logs the record type and
the reason. Refusing the capture instead would fail a write that persisted —
the exact posture `_emit_event` already takes for the journal append itself
(#4240), one door further down.

CONFIGURATION — three names, and arming needs a tenant:

* ``TORTOISE_RECORD_DSN`` — a Postgres DSN (``postgresql://…``). Unset means the
  record is NOT armed: zero statements, zero connections, byte-identical
  behaviour. That is the default, and a deployment opts in.
* the TENANT — ``(org_id, graph_id)``, resolved per write, never guessed. The
  record's tables are keyed by it with a foreign key to ``public.graphs``, so a
  guess is a cross-tenant write: a DSN with no tenant REFUSES and counts
  ``no_tenant``. Two sources, in order:
    1. :func:`use_record_tenant` — a ContextVar, which is how a multi-tenant
       process (one SDK per tenant graph, one process) attributes each write;
    2. ``TORTOISE_RECORD_ORG_ID`` / ``TORTOISE_RECORD_GRAPH_ID`` — the
       single-tenant deployment's static attribution.
  Both are required as a PAIR from either source; a half-named tenant refuses.

⚠️ WHY NOT `TORTOISE_AUDIT_DSN`'s SHAPE ONLY. The audit DSN precedent
(`tortoise/audit_events.py`) arms a process-wide sink from env, which is right
for a single-tenant deployment and WRONG here: a hosted process serves many
tenant graphs, so an env-only tenant would file every tenant's records under
one organisation. The ContextVar is the seam that makes the hosted lane correct;
the env pair is the single-tenant convenience on the same resolution path.

WHAT IS DEFERRED, AND IT IS NAMED: ``public.journal_event`` — the record of what
the record consumed. Its ``seq`` is the journal ORDINAL per ``(org, graph)``
(the schema calls it *"the idempotency key: applying a position twice is a
no-op"*), and a full replay reproduces it with ``enumerate(records, start=1)``.
The live path has no ordinal to hand: no journal exposes one (`EventLog.append`
writes bytes and returns None; the only count is a full read), so writing this
row from here would FABRICATE a position that a later replay would then either
collide with or duplicate. That is a design question with its own issue
(:data:`POSITION_ROW_DEFERRED_ON`), and it is bookkeeping ABOUT the ontology
layers rather than one of them: §2.0's acceptance ("a user's Points, operators,
edges, Objects, Subjects and the event stream exist in Postgres") is satisfied
by the layer writes this module performs.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any

from . import monitoring, record_projection
from .record_projection import (
    RecordProjectionError,
    UnknownJournalRecordType,
    mutations_for,
    refused_props_names,
    table_columns,
)

_logger = logging.getLogger(__name__)

#: The Postgres DSN that arms the record. Unset → the record is not written.
DSN_ENV = "TORTOISE_RECORD_DSN"
#: Static tenant attribution for a SINGLE-TENANT deployment (see `use_record_tenant`).
ORG_ENV = "TORTOISE_RECORD_ORG_ID"
GRAPH_ENV = "TORTOISE_RECORD_GRAPH_ID"

#: The issue that owns the deferred `public.journal_event` position row
#: (the journal ORDINAL problem, stated in the module docstring). Named here so
#: a reader of the code — not only of the tracker — sees the residual.
POSITION_ROW_DEFERRED_ON = "#9190"

#: The issue that owns the MISSING PRODUCTION CALLER for the tenant ContextVar.
#: Named here because the gap is a cross-tenant hazard, not a rough edge: env is
#: process-global, so a multi-tenant process that arms the record with the env
#: pair files every tenant's rows under one organisation (see the module
#: docstring's "WHY NOT `TORTOISE_AUDIT_DSN`'s SHAPE ONLY").
TENANT_SEAM_DEFERRED_ON = "#9192"

#: Which statement prefixes are layer writes. The position row is the only
#: statement `replay_plan` emits that is NOT a layer write, and this module
#: never emits it (see :data:`POSITION_ROW_DEFERRED_ON`).
_POSITION_ROW_PREFIX = record_projection._JOURNAL_INSERT_PREFIX


# ── the tenant seam ─────────────────────────────────────────────────────────

_TENANT: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "tortoise_record_tenant", default=None)


@contextlib.contextmanager
def use_record_tenant(*, org_id: str, graph_id: str) -> Iterator[None]:
    """Attribute every record written inside this block to `(org_id, graph_id)`.

    The multi-tenant seam: a process serving several tenant graphs sets this per
    request (or per SDK call) rather than relying on process-wide env, which
    would file every tenant's record under one organisation. Nested blocks
    restore the outer value.

    ⚠️ NO PRODUCTION CALLER YET (`TENANT_SEAM_DEFERRED_ON`): the hosted lane is
    still unwired, so today only the env pair arms a process that serves several
    tenants — and that is the unsafe path this seam exists to replace.

    ⛔ AND THE SEAM MUST COVER THE THREAD, NOT JUST THE CALLER: a ContextVar does
    not follow a thread, so a worker pool executing the write (the hosted lane
    submits captures to `_CAPTURE_EXECUTOR`) resolves the ENV pair instead while
    the submitting thread's scope is set — measured in review. So the wrapping
    lane must set the scope INSIDE the worker (or pass it explicitly), never
    around a `submit()`. Until that caller exists, the exposure is real: a
    multi-tenant process armed from env writes every tenant's rows under one
    organisation.
    """
    if not org_id or not graph_id:
        raise ValueError("a record tenant needs BOTH org_id and graph_id")
    token = _TENANT.set((str(org_id), str(graph_id)))
    try:
        yield
    finally:
        _TENANT.reset(token)


def resolve_tenant() -> tuple[str, str] | None:
    """The tenant for THIS write: the ContextVar first, then the env pair.

    Returns ``None`` when neither source names a complete pair — the caller
    refuses (and counts ``no_tenant``) rather than guessing. A half-named pair
    is treated as absent on purpose: `(org, None)` is not a tenant.
    """
    scoped = _TENANT.get()
    if scoped is not None:
        return scoped
    org = (os.environ.get(ORG_ENV) or "").strip()
    graph = (os.environ.get(GRAPH_ENV) or "").strip()
    if org and graph:
        return org, graph
    return None


# ── the connection ──────────────────────────────────────────────────────────


def connect(dsn: str) -> Any:
    """Open one Postgres connection for the record.

    A module-level seam so a test (or a lane with its own pool) can substitute a
    connection without a real Postgres — the Python suite is deliberately
    DB-free (`tests/test_record_projection.py` states why) and the DB-level half
    of Interface A lives in the PGlite harness.

    ``psycopg2`` is an OPTIONAL dependency, like `tortoise/audit_events.py`'s
    audit sink: importing it lazily means an unarmed deployment never needs it.
    An import failure raises — :func:`project` turns that into a counted
    ``connect`` failure, never an exception on the customer's write.
    """
    import psycopg2

    # BOUNDED HANDSHAKE. libpq waits indefinitely by default, and this call sits
    # on a capture worker thread inside `_LOCK`: an unreachable store would pin
    # the worker until the outage ends, and four pinned workers take the hosted
    # capture lane down with a perfectly healthy journal (#3060's class).
    conn = psycopg2.connect(dsn, connect_timeout=CONNECT_TIMEOUT_S)
    # ⛔ THE HANDSHAKE IS ONLY HALF THE WAIT. A reachable store can still leave a
    # statement blocked indefinitely, and `project` holds the ONLY connection
    # (and therefore `_LOCK`) across the statement loop — so one wedged query
    # parks every writer, not just its own. The bound is set SERVER-side, per
    # session: it survives a black-holed socket (where a client-side timer on a
    # blocking call would not) and it is issued as a statement rather than as a
    # connect `options=` kwarg, because psycopg2 merges kwargs into the conninfo
    # and would silently OVERRIDE an `options` the caller's DSN already carries.
    with conn.cursor() as cursor:
        cursor.execute(f"SET statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
    conn.commit()
    return conn


class _State:
    """Process-wide writer state: the connection, and the refusal tallies."""

    def __init__(self) -> None:
        self.conn: Any | None = None
        self.refusals: dict[str, int] = {}
        self.columns: dict[str, frozenset[str]] | None = None
        self.refused: dict[str, frozenset[str]] | None = None
        #: Monotonic instant before which NO connect is attempted (the negative
        #: cache that stops a per-record handshake storm during an outage).
        self.next_connect_at: float = 0.0
        self.backoff_warned: bool = False

    def drop_connection(self) -> None:
        """Drop the socket only — the tallies are the PROCESS's history.

        Called from the failure path, so it must not erase evidence: an earlier
        review caught `reset()` zeroing `refusals` on the first store error,
        which loses exactly the counter an operator reads during an outage.
        """
        if self.conn is not None:
            with contextlib.suppress(Exception):
                self.conn.close()
        self.conn = None
        # NOT `next_connect_at` — a dropped connection is not a refusing store;
        # the backoff window belongs to a FAILED CONNECT (see `_connection`).
        self.backoff_warned = False

    def reset(self) -> None:
        self.drop_connection()
        self.refusals = {}
        self.columns = None
        self.refused = None
        self.next_connect_at = 0.0


_STATE = _State()
#: ONE connection, so exactly one transaction at a time. psycopg2's transaction
#: is connection-scoped: two threads on this socket interleave their statements
#: and then the FIRST `commit()` publishes the other's half-applied set, while
#: the other's `rollback()` discards it — and both callers return True. That is
#: a false success on the invariant this module exists to protect, and the
#: hosted lane really is concurrent (`hosted_api._CAPTURE_EXECUTOR`, 4 workers
#: by default). Serializing is the cheap correct answer; a connection pool that
#: removes the serialization is a throughput follow-up, not a correctness fix.
_LOCK = threading.Lock()
#: Guards the tallies only. A SEPARATE lock because `_refuse` runs on paths that
#: do not hold `_LOCK` (a tenant refusal returns before the transaction), and
#: `_LOCK` is not reentrant — taking it inside `_refuse` would deadlock the
#: callers that already hold it. The increments are read-modify-write on a dict,
#: so without this a burst undercounts exactly when an operator needs the number.
_TALLY_LOCK = threading.Lock()

#: Upper bound on the Postgres handshake (seconds). Long enough for a cold
#: TLS+auth round trip, short enough that a black-holed host cannot pin a
#: capture worker.
CONNECT_TIMEOUT_S = 5
#: Upper bound on ONE statement of a record transaction (milliseconds). A record
#: set is a handful of indexed upserts; anything approaching this is a store
#: problem, and the failure must return as a counted refusal rather than hold the
#: single connection (and therefore every other writer) indefinitely.
STATEMENT_TIMEOUT_MS = 5_000
#: How long a failed connect suppresses further attempts. Without it, every
#: record in every capture retries the handshake, so a store outage becomes a
#: per-event timeout instead of one.
RECONNECT_BACKOFF_S = 30.0


def reset() -> None:
    """Drop the connection and the tallies — the test/lane boundary.

    Also the operator's repair verb after a DSN change: the next armed write
    reconnects and re-reads the migrations from disk. The failure path uses
    ``_State.drop_connection`` instead — a failure must not erase the tally.
    """
    with _LOCK:
        _STATE.reset()


def armed() -> bool:
    """Is the record armed at all? A DSN, and nothing else, decides that."""
    return bool((os.environ.get(DSN_ENV) or "").strip())


def refusal_count(reason: str) -> int:
    """How many writes this process refused for *reason* (tests, diagnostics)."""
    with _TALLY_LOCK:
        return _STATE.refusals.get(reason, 0)


def _refuse(reason: str, record: Mapping[str, Any], detail: str = "",
            *, quiet: bool = False) -> None:
    """Count + log a refusal. Never raises: the append has already committed."""
    with _TALLY_LOCK:
        _STATE.refusals[reason] = _STATE.refusals.get(reason, 0) + 1
    with contextlib.suppress(Exception):
        monitoring.record_record_write_failure(reason)
    if quiet:
        return
    if reason == "unprojectable":
        # ⚠️ The healing claim is FALSE for this reason and saying it anyway
        # would send an operator to a replay that fails identically:
        # `replay_plan` calls this same `mutations_for`. A record the engine
        # cannot derive is a DEFECT in the record's producer mapping, and the
        # only repair is to declare the mutation.
        consequence = ("a replay will FAIL IDENTICALLY — this is a missing "
                       "mutation/producer shape, not lag")
    else:
        consequence = "the journal holds it, so a replay heals the record"
    _logger.error(
        "the record did not accept journal record %r (%s): %s — %s (#9078)",
        record.get("type"), reason, detail or "no detail", consequence,
    )


def _connection(dsn: str) -> Any:
    """The live connection, opened on first use and reopened after a failure.

    Carries the NEGATIVE CACHE: after a failed handshake, no further attempt is
    made for :data:`RECONNECT_BACKOFF_S`. Without it a store outage costs one
    full connect timeout PER RECORD — a ten-record capture would spend fifty
    seconds of a capture worker on handshakes that cannot succeed.
    """
    if _STATE.conn is not None:
        return _STATE.conn
    now = time.monotonic()
    if now < _STATE.next_connect_at:
        raise RecordStoreUnavailable(
            f"the record store refused its last handshake; next attempt in "
            f"{_STATE.next_connect_at - now:.0f}s")
    try:
        _STATE.conn = connect(dsn)
    except Exception:
        _STATE.next_connect_at = now + RECONNECT_BACKOFF_S
        raise
    _STATE.next_connect_at = 0.0
    _STATE.backoff_warned = False
    return _STATE.conn


class RecordStoreUnavailable(RuntimeError):
    """The record store is inside its post-failure backoff window."""


def _columns() -> tuple[dict[str, frozenset[str]], dict[str, frozenset[str]]]:
    """The schema's tables/columns and its refused property names — read ONCE.

    Both are parsed from the migration files on disk (`record_projection`), so
    they cost a file read rather than a round trip, but a per-record parse on
    the capture path would be pure waste: they cannot change while the process
    runs, and :func:`reset` is how a lane picks up a new migration.
    """
    if _STATE.columns is None or _STATE.refused is None:
        _STATE.columns = table_columns()
        _STATE.refused = refused_props_names()
    return _STATE.columns, _STATE.refused


def project(record: Mapping[str, Any]) -> bool:
    """Project ONE journal record into Postgres. ``True`` when it landed.

    Called by the write path after the journal append RETURNED SUCCESS — the
    caller owns that gate (``appended is True``), because this function cannot
    see whether the append happened.

    ONE TRANSACTION per record, and it is deliberately the record's own: the
    journal append is a separate, already-committed transaction, and §3 forbids
    a dual write between them, not a sequential one. `mutations_for` materializes
    the whole statement set BEFORE the first executes, so an unprojectable
    record fails with the transaction untouched rather than leaving a
    half-written record.

    FAIL-SOFT, ALWAYS. Every exit is ``False`` + a counted reason; none raises.
    A record that lags the journal is recoverable (replay from the journal); a
    capture that fails after committing to the journal is not.
    """
    if not armed():
        return False
    tenant = resolve_tenant()
    if tenant is None:
        _refuse("no_tenant", record,
                f"{DSN_ENV} is set but neither use_record_tenant() nor "
                f"{ORG_ENV}/{GRAPH_ENV} names the organisation and graph")
        return False
    org_id, graph_id = tenant

    try:
        columns, refused = _columns()
        statements = mutations_for(record, org_id=org_id, graph_id=graph_id,
                                   columns=columns, refused=refused)
    except (UnknownJournalRecordType, RecordProjectionError) as exc:
        # A record type with no declared mutation is a FAILURE, not a skip
        # (`#5285`'s rule). It is counted here and reds the totality suite in
        # CI; failing the capture would lose a write that already committed.
        _refuse("unprojectable", record, str(exc))
        return False
    if not statements:
        # A declared `NoRecordEffect` — a real answer, not a refusal.
        return True
    statements = [s for s in statements if not s.sql.startswith(_POSITION_ROW_PREFIX)]

    dsn = (os.environ.get(DSN_ENV) or "").strip()
    with _LOCK:
        try:
            conn = _connection(dsn)
        except Exception as exc:
            # One log line per backoff window, or a store outage floods the log
            # with one ERROR per record (the counter still increments per record,
            # because the number of records that missed the record is the truth).
            backoff = isinstance(exc, RecordStoreUnavailable)
            _refuse("connect", record, repr(exc), quiet=backoff and _STATE.backoff_warned)
            if backoff:
                _STATE.backoff_warned = True
            return False

        cursor = None
        try:
            cursor = conn.cursor()
            for statement in statements:
                cursor.execute(statement.sql, statement.params)
            conn.commit()
        except Exception as exc:
            with contextlib.suppress(Exception):
                conn.rollback()
            # A connection that failed mid-statement is not reusable: the next
            # write would inherit an aborted transaction. Drop it and reconnect
            # lazily — but keep the tallies (that is what `drop_connection`
            # is for; `reset` would erase the history an operator reads).
            _STATE.drop_connection()
            _refuse("execute", record, repr(exc))
            return False
        finally:
            if cursor is not None:
                with contextlib.suppress(Exception):
                    cursor.close()
    return True


def failures() -> dict[str, int]:
    """The counted refusal/failure reasons for this process (diagnostics)."""
    with _TALLY_LOCK:
        return dict(_STATE.refusals)


def note_no_journal(record_type: object) -> None:
    """An ARMED record met a write door with no journal configured (#9078).

    Not a refusal at the record's door — there was no record to project, because
    nothing was journalled. It is a CONFIGURATION gap, and the failure it
    produces is silent by nature: the journal (the record's source) does not
    exist, so the record can never gain the write while `metrics()` reads 0.
    Counted per occurrence (every missed write is real), warned once per process
    per type so a hot loop cannot flood the log.
    """
    if not armed():
        return
    key = str(record_type)
    with _TALLY_LOCK:
        _STATE.refusals["no_journal"] = _STATE.refusals.get("no_journal", 0) + 1
    with contextlib.suppress(Exception):
        monitoring.record_record_write_failure("no_journal")
    if key in _NO_JOURNAL_WARNED:
        return
    _NO_JOURNAL_WARNED.add(key)
    _logger.warning(
        "the record is armed (%s) but this SDK has NO journal: %s writes are "
        "journalled nowhere, so they can never reach the record (#9078)",
        DSN_ENV, key,
    )


#: Record types already warned about in `note_no_journal` (once per process).
_NO_JOURNAL_WARNED: set[str] = set()
