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
THE FIVE DECISIONS #9187 ASKED THE SCOPE STAGE TO RESOLVE — AND THE SIXTH
#9192 ADDED (the tenant's authority, below)
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
failure raises. On the LIVE door, where a failure may not raise (decision 3,
OVERRIDES below), the refusal is a RETURNED value instead — `REFUSED` plus
`refusal` — so "nothing was written" is never a value the caller cannot tell
from "off by policy" (#8447).

A configured-but-MALFORMED DSN is not "unarmed" — it raises. Silently reading a
typo as "not configured" is the failure mode this whole seam exists to prevent,
wearing a passing test. (The LIVE door catches that raise, because it cannot
raise at all — it REPORTS it as `REFUSED` / `malformed_dsn`; what it must never
do is read as "not configured".) The error carries an 8-hex fingerprint of the value, not
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

**OVERRIDES:** one door deliberately departs from this decision — the LIVE door
(`write_live_record`). It runs after the caller's journal append has already
committed (§17.1 makes the append the commit point), so raising there would
hand a producer an exception for a write that PERSISTED — the one SDK caller
(`TortoiseSDK._write_record_projection`) already treats a record fault as
"never a reason to fail the caller's write". It counts
``write_failed``/``circuit_open`` and warns instead, which keeps the
``degraded`` vs ``unconfigured`` distinction on the counter surface rather than
the exception surface. `write_records`/`RecordWriter.write` — the path a caller
with an open transaction uses — still FAILS exactly as this decision says.

⛔ It also RETURNS the refusal (`RecordWriteOutcome.REFUSED` + `refusal`,
#8447). Counting alone was not a report: the counter is read by `/health` and
`monitoring.metrics()`, not by the producer that made the write, so a refused
record write came back as the SAME ``None`` an UNARMED deployment returns and
nothing on the caller's side could tell them apart. This adds no exception and
no new response shape — the failure still does not raise here, it reports.

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

**6. WHO may name the tenant — the scope in a multi-tenant process, the env
pair only on a DECLARED single-tenant one (#9192).** The tenant is a
per-request fact, so the per-request carrier (`use_record_tenant`) always wins.
The env pair is process-global and therefore only correct where the process
serves one graph; an operator states that with
:data:`RECORD_SINGLE_TENANT_ENV`. Undeclared ⇒ **multi-tenant** ⇒ the pair is
IGNORED and the armed write is refused (`no_tenant`, counted + warned), because
the alternative is not "a less precise tenant", it is tenant A's rows under
tenant B's organisation with no error at any layer — the row is well-formed and
the FK only checks the graph exists. An env fallback with no declaration in
front of it is reachable from the live door on any deployment that sets the
pair, which is exactly the hazard the declaration removes.

``OVERRIDES:`` the industry default (and #9078's scope decision 3's own
inference) of treating the mere PRESENCE of a process-global pair as proof that
the deployment is single-tenant — :data:`RECORD_SINGLE_TENANT_ENV` must be set,
because single-tenancy is a deployment fact that has to be STATED, and an
inferred one is exactly what a multi-tenant process would satisfy silently
(#9192 ask 2).

This does not remove #9078's scope decision 3 — a single-tenant deployment
still arms by env — but it DOES change the operational contract for an
UNDECLARED one: it armed before and refuses now, until it declares. That change
fails closed (counted + warned, nothing misfiled), and the amended decision is
relayed to #9078, documented in ``.env.example`` and in
``monitoring.record_record_write_failure``.

Decision 6 governs WHERE a per-request tenant comes from, not WHICH MECHANISM
carries it (#9188). The ambient carrier is :func:`use_record_tenant`, and it is
the right one wherever a lane can hold a `with` around the work. The hosted
REST lane cannot, and **the reason is confinement, not propagation**: a
contextvar set on the loop IS inherited by `asyncio.to_thread` and by
`_graph_offload` (both copy the context — measured), so ambient state *would*
reach the worker. But that lane resolves the tenant inside `_data_sdk`, which
for the capture path is itself submitted to `_graph_offload` — a scope entered
there is discarded with the copy — and to install it loop-side instead it would
have to be SET WITHOUT A `with`, because the REST auth seam is a plain FastAPI
dependency and none exits one today (a `yield`-dependency could — nothing does). Unreleased ambient state in a request
context is inherited by every task spawned from it (`_enqueue_dream`, the
indexing jobs) and by every other write door, and a stale one is tenant A's
rows under tenant B — the exact hazard decision 6 exists to remove. So that
lane carries the tenant BY VALUE instead (`write_live_record(..., tenant=)`),
resolved once from the same credential dict by :func:`record_tenant_for` and
bound to the ONE SDK that performs the write. Both carriers outrank the env
pair and both are this request's own answer; the decision's invariant (never a
process-global guess, never a cross-request leak) is what they share, and
neither may be replaced by one.

**THE TRANSACTION.** The seam never calls `commit()` and never calls
`rollback()` — **not even at RELEASE**: when the seam opened the connection it
hands back a `_PooledConnection` whose `close()` returns the handle to the pool,
and any unfinished transaction on it is rolled back at the next BORROW
(`_reset_parked_transaction`), never on the release path — a release rollback is
a server round trip taken after the caller's work is done, which is why it moved.
A `conn=` the CALLER injected is never wrapped, so
on that path both are still never called (pinned by a test whose fake
connection counts both and asserts they are zero). When the caller injects
`conn=`, the boundary is unambiguously theirs. When the seam opens the connection itself it RETURNS it on the outcome
(`RecordWrite.connection`) so the caller can commit or close it — a seam that
opened its own connection and committed it would commit the projection ALONE,
which is the dual write §3 forbids.

**THE FAILURE PATH DISPOSES OF WHAT THE SEAM OPENED.** A connection the seam
opened and could not use is disposed of before `RecordWriteError` is raised: the
caller never receives that handle, so it has nothing to roll back and no way to
close it, and the raising traceback would otherwise keep the handle (its aborted
server-side transaction and backend slot) alive for as long as the exception is
held. ⛔ Since #9558 "disposed of" means RELEASED: the connection
`_default_connect` hands out is a `_PooledConnection` whose `close()` returns
the handle to the idle set, and whose next borrower rolls back a transaction the
failed write left aborted (`_reset_parked_transaction`) — so an aborted
projection cannot be inherited by the next record and no backend slot is lost.
An INJECTED connection is never touched — it is the caller's, and its
transaction stays open and unrolled-back on failure.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import logging
import os
import select
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import derived_embedding as _derived_embedding
from . import postgres_bytes, record_projection, status_vocabulary
from .env_truthy import env_flag

_logger = logging.getLogger(__name__)

__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "LIVE_WRITE_BREAKER_SECONDS",
    "LIVE_WRITE_REFUSALS",
    "NON_DURABLE_JOURNAL_REFUSAL",
    "POSITION_ORDINAL_SOURCE",
    "POSITION_UNPROVEN_WARN_SECONDS",
    "RECORD_ALLOCATE_ENV",
    "RECORD_DSN_ENV",
    "RECORD_GRAPH_ENV",
    "RECORD_ORG_ENV",
    "RECORD_POOL_MAX_CONNECTIONS",
    "RECORD_SINGLE_TENANT_ENV",
    "RecordConnectError",
    "RecordWrite",
    "RecordWriteError",
    "RecordWriteOutcome",
    "RecordWriter",
    "armed_but_uncut",
    "armed_but_uncut_reasons",
    "clear_uncut_graph",
    "current_record_tenant",
    "dsn_fingerprint",
    "is_usable_tenant",
    "live_write_refusal",
    "note_uncut_graph",
    "provision_graph_record",
    "record_dsn",
    "record_holds_turns",
    "record_live_allocate",
    "record_owns_position",
    "record_tenant",
    "record_tenant_effective",
    "record_tenant_for",
    "reset_uncut_graphs",
    "single_tenant_declared",
    "use_record_tenant",
    "write_live_record",
    "write_records",
]

#: The record's position row (`public.journal_event` — layer 1, exposed as
#: `public.record_event_stream`) is written BY THE LIVE PATH, from the ordinal
#: `EventLog.append` returns (#9190, landed on main in `b3b4c2fb1`). The `seq` is
#: the journal's own fact: the replay numbers records ``enumerate(records,
#: start=1)`` over the same read, so a live write and a full replay give one
#: record one number by construction. What this constant names is the ISSUE that
#: supplied that ordinal — it used to name a deferral ("the live chokepoint has
#: no ordinal"), and that premise is closed; leaving the note here is what keeps
#: the next reader from re-deriving the deferral from an old comment.
POSITION_ORDINAL_SOURCE = "#9190"

#: The arming key. Present ⇒ armed; unset/blank ⇒ ``NOT_ARMED``. See the module
#: docstring, decision 2 — and note it is a DSN, not a boolean, so a second
#: "enabled" flag would be a second thing to get out of step.
RECORD_DSN_ENV = "TORTOISE_RECORD_DSN"

#: The SINGLE-TENANT arming pair (#9078 scope decision 3). A deployment that
#: serves exactly one graph may name it here instead of wrapping every write in
#: :func:`use_record_tenant`. Both must be set or neither counts: a half pair is
#: a misconfiguration and is refused like an unnamed tenant, never completed by
#: guesswork.
#:
#: ⚠️ A MULTI-TENANT process must NOT arm this way — env is process-global, so
#: every tenant's rows would be filed under one graph (#9192). Such a process
#: sets :func:`use_record_tenant` per request, and that always wins.
RECORD_ORG_ENV = "TORTOISE_RECORD_ORG_ID"
RECORD_GRAPH_ENV = "TORTOISE_RECORD_GRAPH_ID"

#: The DECLARATION that this deployment serves exactly ONE record tenant, which
#: is what makes the env pair above a valid answer (#9192). Default ABSENT ⇒
#: **multi-tenant**, the fail-closed default: the pair is then IGNORED and an
#: armed write with no request scope is refused (`no_tenant`) rather than filed
#: under a process-global tenant that every other tenant in the process would
#: share. Environments differ in whether they can name their tenant at all, so
#: the declaration is what an operator states — never something we infer from
#: which module happens to be imported, or from the control plane's mode (a
#: registry-plane self-host process can serve several orgs too).
#:
#: Read with :func:`env_flag` (typo ⇒ the default, never a flip): a typo'd
#: ``TORTOISE_RECORD_SINGLE_TENANT=yes-please`` leaves the process MULTI-tenant,
#: which refuses rows rather than misfiling them.
RECORD_SINGLE_TENANT_ENV = "TORTOISE_RECORD_SINGLE_TENANT"

#: Arms the ORDINAL ALLOCATOR on the live door (#12894 / `#9190`'s ruling).
#:
#: OFF by default, and OFF is today's behaviour byte for byte: the door writes the
#: position `EventLog.append` reported (the ENUMERATED arm). ON, the door asks the
#: record's counter for the number (`allocate=True`), and cross-checks it against
#: the append's own position — a mismatch REFUSES the write and rolls the
#: allocation back (see `write_live_record`).
#:
#: ⛔ ON A CUT-OVER GRAPH IT ALSO TAKES THE JSONL OFF THE WRITE PATH (#14002).
#: While both allocators existed they had to AGREE, and under concurrent writers
#: they cannot (the journal is ordered by append, the counter by allocation), so
#: the door refused a steady trickle of writes with `position_divergence` and the
#: product read `degraded`. Armed + an `active` cursor row therefore means the
#: record is the ONLY allocator: `tortoise.live_write.write` asks
#: :func:`record_owns_position` BEFORE it appends and skips the file append
#: entirely, and the counted number comes from the counter alone. The JSONL is a
#: FROZEN PREFIX from that point — an OPTIONAL copy, not an export: nothing writes
#: it yet (no drainer ships; `event_id`/`initiated_by`/`projection_version` have
#: no column, `§A7.8` item 6). What is true either way is that it is no longer a
#: POSITION SOURCE, so nothing numbers off it and the truncation class of failure
#: is gone.
#:
#: ⛔ IT IS SAFE TO SET ONLY ON A CUT-OVER GRAPH (`record_cursor` row in mode
#: `active`, written by `tools/record_allocator_cli.py`). ON with no cursor row is
#: a refusal per record (`cursor_not_active`), not a silent fallback: allocating
#: for a graph the seed never reached is the one thing the cutover sequence
#: forbids, and a flag that quietly degraded to enumeration would hide exactly the
#: mis-armed deployment an operator needs to see.
#:
#: Read with :func:`tortoise.env_truthy.env_flag`, so a TYPO leaves it OFF (the
#: default), never flipped — a half-typed opt-in must not renumber a live write.
RECORD_ALLOCATE_ENV = "TORTOISE_RECORD_ALLOCATE"

#: The bound on OPENING the record's connection, in seconds. libpq's default is
#: ``0`` — wait forever — and the live door opens one connection per journal
#: record ON THE CALLER'S THREAD, after the journal append has committed. So an
#: armed-but-unreachable record (a dead host, or a firewall dropping SYNs) would
#: stall every capture for as long as the kernel takes, which is the difference
#: between "the record is degraded" and "the capture path is hung". A DSN that
#: names its own ``connect_timeout`` WINS: the operator's explicit bound is not
#: silently replaced by this one.
CONNECT_TIMEOUT_SECONDS = 10

#: How long a POOLED handle may sit **idle in transaction** before the SERVER ends
#: it, in seconds (#9558 review cycle 4, P1). ``0`` — the server default — is
#: unbounded, and pooling turns that into a lock leak: the READ door parks a
#: handle INTRANS **on purpose** (``open_record`` issues its transaction-local
#: ``set_config`` and never commits), so before this bound a parked handle held
#: an ``AccessShareLock`` on the record tables until the SAME DSN was borrowed
#: again *in that process* — which may be never.
#:
#: Measured on PostgreSQL 16: a session parked exactly that way reports
#: ``state = idle in transaction`` with relation ``AccessShareLock``s, and a
#: concurrent ``ALTER TABLE`` on the same table fails with a lock timeout (it
#: does not block plain DML). Record migrations are actively edited (#9881 added
#: an ``ALTER TABLE public.point``), so a worker that served one read and then
#: went quiet could hang the next migration. Nothing else bounds it:
#: ``idle_in_transaction_session_timeout`` was set **nowhere** in the tree, and
#: ``open_record``'s ``statement_timeout`` is transaction-local and bounds
#: STATEMENTS, not idling.
#:
#: 60 s — not seconds, not infinity. It rides the ``options`` STARTUP packet, so
#: it costs **no round trip** and therefore does not undo the change it escorts.
#: The WRITE door parks IDLE, so this bound never fires on the path the pool
#: exists for; it only reclaims a READ-door handle, and a read borrow issues its
#: statements back to back. A handle the server ends is handled by the path that
#: already exists — the borrow-time reset raises, the handle is discarded, the
#: caller gets a fresh connect (see ``_take_idle_connection``) — so expiring one
#: costs a reconnect, never a surfaced error. A DSN that sets its own
#: ``idle_in_transaction_session_timeout`` WINS, for the same reason
#: How long the CREATION HOOK may spend connecting to the record, in seconds.
#: ⛔ SHORTER THAN THE LIVE DOOR'S :data:`CONNECT_TIMEOUT_SECONDS` (10), on
#: purpose: the hook runs on a graph-creation REQUEST, and in `hosted_api` that
#: request calls a SYNC function from an `async` endpoint, so the wait blocks the
#: worker's event loop (including a co-located `/health`). A record outage at
#: creation must cost the user a bounded pause, not the live path's full budget —
#: the graph is created either way and `/health` publishes the gap.
PROVISION_CONNECT_TIMEOUT_SECONDS = 3

#: :data:`CONNECT_TIMEOUT_SECONDS` defers to a ``connect_timeout`` the operator
#: named.
IDLE_IN_TRANSACTION_TIMEOUT_SECONDS = 60

#: How long the LIVE door stops trying after a connect to the record FAILED, in
#: seconds. :data:`CONNECT_TIMEOUT_SECONDS` bounds ONE connect; it does not bound
#: a CAPTURE, and the door is called once per journal record on the caller's
#: thread — so a capture emitting N records paid N × the timeout while
#: ``write_live_record`` (fail-soft by design) went on reporting success. Under
#: exactly the drop/blackhole case the timeout exists for, that is the wedged
#: capture the bound was supposed to prevent: 25 records × 10 s ≈ 4 minutes
#: inside one ``capture_session`` call.
#:
#: So a connect failure TRIPS this breaker: every further live write for the next
#: ``LIVE_WRITE_BREAKER_SECONDS`` is refused locally and counted
#: ``circuit_open`` — no socket, no timeout, no stall — and the first SUCCESSFUL
#: write closes it again. Connection-refused fails instantly and is unaffected in
#: cost; this bounds the shape that does not. It is deliberately process-wide
#: (one record, one deployment) and deliberately time-based rather than
#: attempt-based: a probe after the window re-tests the record once, so a healed
#: record recovers on its own with no restart.
LIVE_WRITE_BREAKER_SECONDS = 60.0

#: When the live breaker closes again, on ``time.monotonic()`` (0.0 = closed).
#: Module-level on purpose: the cost it bounds is per PROCESS, not per call.
_breaker_open_until = 0.0

#: How often the "position unproven" WARNING may repeat, in seconds. One line
#: per unproven append would be a warning per record on a large journal whose
#: `EventLog.append` is in a REPEATABLE unproven window (#9252: the
#: ``_FLOCK_UNHEALTHY`` / ``_POSITION_CACHE_DEAD`` states), which teaches an
#: operator to ignore the line. ``0.0`` disables the throttle (every unproven
#: append warns) — the seam the tests use.
POSITION_UNPROVEN_WARN_SECONDS = 300.0

#: When the position-unproven warning last fired, on ``time.monotonic()``;
#: ``-inf`` so the FIRST one always fires.
_unproven_position_warned_at = float("-inf")

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


class RecordConnectError(RecordWriteError):
    """The record is ARMED but its connection could not be OPENED.

    A :class:`RecordWriteError` in every respect (one catchable class for a
    caller), and a separate class for one reason: only THIS failure means the
    record is unreachable RIGHT NOW rather than refusing this one record, so it
    is what trips the live door's breaker (:data:`LIVE_WRITE_BREAKER_SECONDS`).
    A totality refusal or a statement-level Postgres error must NOT trip it —
    the connection was fine, and hiding real record errors behind a 60 s silence
    would be worse than the cost the breaker saves.
    """


class RecordWriteOutcome(StrEnum):
    """What the seam did.

    ``APPLIED``    the plan was materialized and every statement executed on
                   the caller's connection. The seam has NOT committed.
    ``NOT_ARMED``  no record DSN is configured, so nothing was written — zero
                   statements, zero connection attempts, no exception. This is
                   *off by policy*, and it is the write-side spelling of the
                   recorded term :data:`status_vocabulary.STATUS_UNCONFIGURED`;
                   read that term off :attr:`RecordWrite.status` rather than
                   comparing this value to a status word.
    ``REFUSED``    the record IS armed and this call did not write (a refused
                   tenant, an open breaker, a declined scratch journal, a
                   malformed DSN, a failed statement). Added for the LIVE door
                   (#8447), which may not raise — see this module's decision 3
                   OVERRIDES and §17.1 of `docs/architecture/STORAGE-ARCHITECTURE.md`:
                   the caller's append IS the commit point — and used to return
                   ``None`` — the value that means NOT_ARMED — so a caller could
                   not tell "off by policy" from "refused"; read why off
                   :attr:`RecordWrite.refusal`.
    """

    APPLIED = "applied"
    NOT_ARMED = "not-armed"
    REFUSED = "refused"


@dataclass(frozen=True)
class RecordWrite:
    """What a call to :meth:`RecordWriter.write` consumed. A report, never a
    substitute for the failure — a failed write raises instead of returning.

    ⛔ THE LIVE DOOR REPORTS ITS REFUSALS IN THIS TYPE (#8447).
    :func:`write_live_record` runs after the caller's journal append is the
    commit point (decision 3 OVERRIDES above; §17.1 of
    `docs/architecture/STORAGE-ARCHITECTURE.md`), so it may not raise — but it
    may not return ``None``
    either, because that is this module's spelling for *not armed: nothing was
    attempted, off by policy*. An ARMED write that did not happen comes back as
    ``outcome=REFUSED``, ``applied is False``, and ``refusal`` naming which
    counter term it was, so ``None`` means exactly one thing on that door."""

    outcome: RecordWriteOutcome
    #: Journal records handed to the seam (0 for a not-armed call).
    events: int = 0
    #: Record mutations executed. Excludes the journal-position writes, matching
    #: ``record_projection.ReplayReport.statements``.
    statements: int = 0
    #: Journal position of ``records[0]`` on an APPLIED call.
    first_seq: int = 1
    #: Journal position of the LAST record consumed (``first_seq - 1`` when
    #: nothing was consumed — an empty batch, a not-armed call, or the live
    #: door's write with no PROVEN ordinal: `write_live_record` reports that
    #: spelling rather than claiming a position it did not write), so a caller
    #: can advance its offset without re-deriving. Never a rewind: it is always
    #: derived from the caller's own ``first_seq`` — including on a ``REFUSED``
    #: return, where ``first_seq`` is the caller's ``seq`` when it supplied one
    #: (:func:`_refused`), so ``last_seq + 1`` re-offers the refused position
    #: instead of rewinding to the start of the journal.
    last_seq: int = 0
    #: The connection the seam OPENED, or ``None`` when the caller injected one
    #: (theirs to close) or the call was not armed. When set, the caller owns the
    #: transaction: this seam never commits, and never rolls back — not even at
    #: RELEASE, where the pooled handle's ``close()`` parks the connection as it
    #: stands; the pool resets a leftover transaction on the next BORROW.
    connection: Any | None = field(default=None, repr=False)
    #: #12762 — the STORED bytes this write created, measured from the rows
    #: Postgres actually holds (``postgres_bytes.measure_written_rows``), and the
    #: number of rows they cover. Carried rather than recorded HERE on purpose:
    #: this seam does not own the transaction, so it cannot know whether the
    #: write COMMITTED — and a ledger increment for a rolled-back write would
    #: inflate the org's figure with no repair path (the counter refuses
    #: negatives). :func:`write_live_record` records these after ITS commit;
    #: a caller that injected ``conn=`` owns that decision itself.
    written_bytes: int = 0
    written_rows: int = 0
    #: WHY an ARMED call did not write — one of the tokens
    #: ``monitoring.metrics()["record_write_failures"]`` / ``["record_non_journal_skips"]``
    #: is counted under (``malformed_dsn``, ``no_durable_journal``, ``no_tenant``,
    #: ``no_tenant_carried``, ``circuit_open``, ``write_failed``,
    #: ``journal_collision``), else ``None``.
    #: The SAME vocabulary as the counters on purpose (#8447): a caller that
    #: reports the refusal and an operator reading the metric are naming one
    #: thing, and a second word list is a second thing to drift.
    #:
    #: ⛔ ``journal_collision`` IS THE ONE POST-WRITE CONFLICT TOKEN (#9318, #12904).
    #: Every other value is decided before a socket is opened
    #: (`_live_write_verdict`) or by a failed statement; this one is a VERDICT on
    #: a conflict: the position row holds a record this write cannot index —
    #: a different `event_type`, a different capture time, an identity the
    #: surviving row CONTRADICTS, or (with no `ts` for the capture time to be
    #: compared) a payload whose derivation moved — which
    #: `record_projection.JournalPositionCollision` detected by comparing the
    #: surviving row's content AND its `recorded_at`. It is not in
    #: ``LIVE_WRITE_REFUSALS`` for that reason — that tuple is what the PRE-write
    #: verdict can return — and a consumer switching on this field must accept it.
    #: ⛔ A same-capture re-derivation IS this token when its identities moved:
    #: DRIFT is the same type, same capture and payloads whose identities do NOT
    #: contradict (`agrees`/`one_sided`/`none`) — that repairs itself, reports
    #: `applied` and counts nothing (#12904 P0-1) — while a re-keyed identity at
    #: one instant is `disagrees` and refuses. ⛔ AND A
    #: DIFFERENT record captured at the same instant IS this token when the two
    #: payloads name CONTRADICTING identities — an equal instant is a coordinate,
    #: not identity, so the drift arm is GATED on `_identity_evidence` (#9318);
    #: the residual it cannot see is the pair that shares an instant AND whose
    #: identities do NOT contradict — `agrees`, `one_sided` (one side names no
    #: identity) or `none` — which keeps the silent repair path.
    #: Nothing is lost (the
    #: journal holds the record); what is refused is indexing it under a position
    #: whose row this write does not CONTINUE (a different `event_type`, a
    #: different capture time, an identity the surviving row contradicts, or — for
    #: a write with no `ts` — a payload whose derivation moved),
    #: which the record cannot fix while its key carries
    #: no journal identity (#12894).
    #:
    #: ⛔ ``no_tenant_carried`` IS A SEPARATE TOKEN ON PURPOSE (#10021 review P3).
    #: The live door splits the nil tenant from a MALFORMED CARRIED pair — one is
    #: "nobody said whose turns these are", the other is "the caller said, and
    #: the answer was unusable" — and both count on the ``no_tenant`` COUNTER.
    #: So the closed list above is the refusal vocabulary, and the counter is a
    #: DIFFERENT set OF THE SAME SIZE — seven labels, not the same seven. The
    #: door folds `no_tenant_carried` onto `no_tenant` and counts
    #: `no_durable_journal` on the skip counter, while the counter separately
    #: carries the SDK-only `no_journal` and `seam_unimportable`; deriving one
    #: set from the other is wrong in BOTH directions. Listing only the
    #: counter words here is what made a consumer's closed list miss a value the
    #: door actually returns (:func:`write_live_record`'s ``LIVE_WRITE_REFUSALS``
    #: declares them together, and that set is authoritative).
    refusal: str | None = None

    @property
    def applied(self) -> bool:
        """Did the record actually get written on this call?

        The one check a caller needs. ``False`` means *nothing was written* —
        for ``NOT_ARMED`` it is not an error (an unarmed deployment is a
        supported state); a ``REFUSED`` call was armed and did not write, and
        :attr:`refusal` says WHY, but the SEVEN refusal tokens are not one
        severity: ``malformed_dsn`` / ``no_tenant`` / ``no_tenant_carried`` /
        ``circuit_open`` / ``write_failed`` / ``journal_collision`` are
        FAILURES, all six counted on ``record_write_failures`` (BOTH
        ``no_tenant`` arms count — the nil tenant and the malformed CARRIED
        pair, which is why the token list is one longer than the counter set),
        while ``no_durable_journal`` is a
        healthy-armed DECLINE counted on ``record_non_journal_skips`` — that lane
        has no journal by design, so nothing a replay could restore is missing
        (#9231 review P2, and the vocabulary note in
        :func:`monitoring.record_record_write_failure`). A consumer must
        branch on :attr:`refusal`, not on ``applied is False``, before calling a
        decline an error. Either way a caller that needs the record to exist
        must not read ``False`` as success.
        """
        return self.outcome is RecordWriteOutcome.APPLIED

    @property
    def status(self) -> str:
        """The recorded status term for this outcome
        (``available``/``degraded``/``unconfigured``).

        Derives through :func:`status_vocabulary.classify` — the ONE mapping of
        observable facts onto the recorded vocabulary — so the write side cannot
        drift from the read side's terms (#9187 decision 2/3).
        """
        if self.outcome is RecordWriteOutcome.NOT_ARMED:
            return status_vocabulary.classify(configured=False, reached=False)
        if self.outcome is RecordWriteOutcome.REFUSED:
            # ARMED and nothing written. Only `circuit_open` is LITERALLY
            # "configured, not reached" ⇒ `degraded`. The other six are near
            # misses, and NOTHING here has its own term: `write_failed` covers an
            # unreachable record AND a record that WAS reached and refused the
            # projection; `journal_collision` (#9318) is a record that WAS reached
            # and refused to file itself under a journal position it cannot index
            # (a different `event_type`, a different capture time, an identity the
            # surviving row contradicts, or — for a write with no `ts` to compare
            # — a payload whose derivation moved); `malformed_dsn` is a SET-UP defect (a value IS set and
            # is wrong, so not `unconfigured`, which is defined as no value at
            # all); `no_tenant` is a deployment/caller gap; `no_tenant_carried` is
            # the same gap one arm over (a CARRIED tenant pair that was unusable);
            # and `no_durable_journal` (the healthy M2 capture lane) never asks
            # the store at all. So `degraded` — which `status_vocabulary` defines
            # as "off by OUTAGE" — is the NEAREST of the four RECORDED terms for
            # all seven, not a measured outage for the other six. There is
            # deliberately no fifth RECORDED term ("do not add a fifth term… a
            # condition the four do not cover is a contract change") — the count
            # above is the REFUSAL set, which is one longer than the recorded
            # one — so `.status` alone cannot tell those apart: a consumer MUST
            # read :attr:`refusal`
            # first (see :attr:`applied`, and the write-path paragraph in
            # `status_vocabulary`'s module docstring).
            # Never `available` either way — a refusal that reads as the healthy
            # term is exactly the #8447 shape (`APPLIED` is the only `available`).
            return status_vocabulary.classify(configured=True, reached=False)
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


def _refused(refusal: str, seq: int | None) -> RecordWrite:
    """The LIVE door's refusal report for an ARMED call that did not write.

    ONE constructor for all seven refusal arms (#8447), for two reasons:

    * the ARM decides the outcome, so no arm can drift back to ``return None``
      — the value this module reserves for NOT ARMED — by accident;
    * the WATERMARK carries the caller's own position. A bare
      ``RecordWrite(outcome=REFUSED)`` takes the dataclass defaults
      ``first_seq=1, last_seq=0``, which is a *rewind* for any caller that
      supplied ``seq`` and applies the documented ``last_seq + 1`` rule: it
      would re-offer journal position 1 instead of the refused position.
      Reporting ``first_seq=seq, last_seq=seq - 1`` is this class's own "nothing
      consumed" spelling (``first_seq - 1``) — truthful (nothing beyond
      ``seq - 1`` was consumed by this call) and never a rewind, so a consumer
      that retries the refused position does the right thing.
    """
    first = seq if seq is not None else 1
    return RecordWrite(outcome=RecordWriteOutcome.REFUSED, refusal=refusal,
                       first_seq=first, last_seq=first - 1)


def dsn_fingerprint(dsn: str) -> str:
    """The 8-hex fingerprint of a DSN value — the ONLY form of it that may be published.

    A DSN is secret material (#2903/#2796), so a surface that must let an
    operator tell WHICH value a process holds (a stale secret, a value that
    never reached the machine) publishes this instead of the value: 32 bits of
    sha256 over a high-entropy string is a comparison handle, not a disclosure.

    ONE definition **inside the record seam**, called by :func:`record_dsn`'s
    refusal messages and by the arming report on the hosted ``/health`` (#9548).
    Two copies of this convention would drift, and the copy that drifts is the
    one that prints a DSN.

    Not the tree's only fingerprint, and this docstring does not claim to be:
    ``audit_events``, ``hosted_backup`` and ``backup_config`` each have their own
    for their own variable, and all three hash ``raw.strip()`` while this one is
    handed an already-stripped value (`record_dsn()`'s ``.strip()``). Folding them
    together is a separate change with its own blast radius (#9997), so the honest
    statement is the scoped one.
    """
    return hashlib.sha256(dsn.encode()).hexdigest()[:8]


def record_dsn(
    explicit: str | None = None, *, surface: str | None = None
) -> str | None:
    """The armed DSN, or ``None`` when the record is not armed.

    ``explicit`` wins over the environment (an injected value makes the DB-free
    pin possible). Unset/blank ⇒ ``None``. A value that is not a Postgres DSN —
    or one no connection can reach — raises :class:`RecordWriteError` carrying
    only a fingerprint of the value: the same secret-hygiene rule
    ``tortoise/audit_events.py`` applies to ``TORTOISE_AUDIT_DSN``
    (#2903/#2796). The reachability half is the HOST rule at the bottom; it
    exists so the value's shape and the seam's verdict cannot disagree — see
    there.

    ``surface`` names the arm the value came from when it is NOT
    ``TORTOISE_RECORD_DSN`` itself (#9801): the record's backup leg has its own
    variable, and telling an operator to fix ``TORTOISE_RECORD_DSN`` for a value
    they set on ``TORTOISE_RECORD_BACKUP_DSN`` is the same "say which one"
    misattribution the role refusal exists to end. Only the NAME travels — the
    value is still reduced to its fingerprint.
    """
    raw = explicit if explicit is not None else os.environ.get(RECORD_DSN_ENV)
    dsn = "" if raw is None else str(raw).strip()
    if not dsn:
        return None
    if not dsn.startswith(_DSN_SCHEMES):
        got = dsn_fingerprint(dsn)
        raise RecordWriteError(
            f"{surface or RECORD_DSN_ENV} must start with postgresql:// or "
            f"postgres:// (got <{got}>...) — a configured-but-unusable record "
            f"DSN is a misconfiguration, not an unarmed record"
        )
    # ── HOST rule: a right-scheme value can still be one no connection can use.
    # `libpq` does NOT fail a hostless URI — it fills the host from `PGHOST` or a
    # unix socket, i.e. it silently connects somewhere nobody named. For THIS
    # variable the codebase already refused that shape twice, in the legs that
    # dump and delete: `record_backup.libpq_env` ("the record DSN names no host
    # — refusing to let libpq fall back to a local socket, which would back up
    # the wrong database") and `record_backup.reset_record_tables` ("libpq would
    # fall back to PGHOST or a local socket, which can be the record"). The
    # WRITE path was the leg that still accepted it — so a clipped `gh secret
    # set` paste (`postgresql://user:pw@`) counted a failure per capture while
    # `/health` reported `armed: true`, which is the same false green #9548
    # exists to end, one predicate in from the scheme check.
    #
    # Both facts are decidable from the STRING alone (no connect, no env
    # fallback), so the refusal belongs HERE, in the seam that already owns
    # "usable"; a re-check in a reader would be a second definition of it, which
    # is the drift `dsn_fingerprint()` was extracted to prevent.
    # A whitespace-bearing value is the mangled-paste shape: a real URI must
    # percent-encode a space, so no driver could have been meant by it.
    problem: str | None = None
    if any(ch.isspace() for ch in dsn):
        problem = "contains whitespace (a URI must percent-encode it)"
    else:
        try:
            host = urlsplit(dsn).hostname
        except ValueError as exc:
            problem = f"is not a readable URI ({exc})"
        else:
            if not host:
                problem = "names no host"
    if problem is not None:
        got = dsn_fingerprint(dsn)
        raise RecordWriteError(
            f"{surface or RECORD_DSN_ENV} {problem} (got <{got}>...) — a "
            f"configured-but-unusable record DSN is a misconfiguration, not an "
            f"unarmed record. Name the server explicitly: without a host, "
            f"libpq falls back to PGHOST or a local socket"
        )
    return dsn


#: The TENANT a live write is filed under, per call, resolved from the caller's
#: own scope (`use_record_tenant`) — see :func:`record_tenant`. #9192's hazard is
#: a PROCESS-GLOBAL tenant: a hosted process serves many tenant graphs, so a
#: module-level or environment-supplied org/graph files tenant A's rows under
#: tenant B with no error (the row is well-formed and the FK only checks that
#: the graph exists). So the carrier a request sets is a ContextVar, and the
#: env pair is reachable only on a deployment that DECLARED itself single-tenant
#: (:func:`single_tenant_declared`). A scope ALWAYS wins over env. Neither set ⇒
#: an armed write is REFUSED and counted (`no_tenant`), never guessed. Ask
#: :func:`record_tenant` — not :func:`current_record_tenant` — to learn whether
#: an armed write will be refused.
_RECORD_TENANT: contextvars.ContextVar[tuple[str, str] | None] = (
    contextvars.ContextVar("tortoise_record_tenant", default=None)
)


@contextlib.contextmanager
def use_record_tenant(org_id: str, graph_id: str):
    """File every record write in this scope under ``(org_id, graph_id)``.

    The one AMBIENT way a live write learns its tenant. A hosted MCP request
    wraps its write path in this; a single-tenant process wraps its writes (or
    its whole serving loop). It is a ContextVar, so two concurrent requests in
    one process cannot see each other's tenant, and it nests.

    ⚠️ Not every lane can hold a `with` around its work: the hosted REST lane
    resolves the tenant inside ``hosted_api._data_sdk``, which the capture path
    submits to ``_graph_offload`` — a scope entered there dies with the context
    copy — and installing it loop-side instead would mean setting it WITH NO EXIT —
    today's REST auth seam is a plain dependency, not a `yield`-dependency, so it
    holds no `with` to hang one on — where every detached task and every other
    write door in that context inherits it. That lane
    carries the tenant BY VALUE on the SDK it opens (:func:`record_tenant_for`,
    ``write_live_record(..., tenant=)``) instead — one resolution, delivered
    where it cannot escape (#9188). Propagation is not the reason: a contextvar
    set on the loop IS copied into ``asyncio.to_thread`` / ``_graph_offload``.
    """
    if not (isinstance(org_id, str) and org_id
            and isinstance(graph_id, str) and graph_id):
        raise ValueError(
            "use_record_tenant needs a non-empty org_id and graph_id — an "
            "unnamed tenant is how a row lands under the wrong graph "
            f"(got {org_id!r}, {graph_id!r})"
        )
    token = _RECORD_TENANT.set((org_id, graph_id))
    try:
        yield (org_id, graph_id)
    finally:
        _RECORD_TENANT.reset(token)


def current_record_tenant() -> tuple[str, str] | None:
    """The tenant this SCOPE set, or ``None``. Scope only — never env.

    ⛔ ``None`` here does NOT mean an armed write is refused: this is the
    request scope alone, and :func:`record_tenant` falls back to the
    single-tenant ``RECORD_ORG_ENV``/``RECORD_GRAPH_ENV`` pair, so a process
    armed by env is refused nothing while this returns ``None``. Use
    :func:`record_tenant` to ask "what tenant would an armed write be filed
    under, and would it be refused?" — that ``None``, and only that one, is the
    refusal. This function answers the narrower "did the caller say whose data
    this is?", which is what a scope-aware caller wants (see
    :func:`use_record_tenant`).
    """
    return _RECORD_TENANT.get()


def record_tenant_for(org: Mapping[str, Any] | None, *,
                     control_plane_ids: bool) -> tuple[str, str] | None:
    """The record's tenant for ONE request, read off its resolved credential.

    THE ONE DECLARATION of "which ``(org_id, graph_id)`` is a record tenant",
    shared by every lane that resolves a credential per request: the MCP lane
    (:func:`tortoise.mcp_auth._record_tenant_scope`, which installs it as a
    :func:`use_record_tenant` scope) and the hosted REST lane
    (:func:`tortoise.hosted_api._data_sdk`, which binds it to the SDK it opens —
    see decision 6 for why that lane carries it by value: CONFINEMENT, not
    propagation). Two lanes, one answer: a divergence here files one request's
    rows under another graph.

    ``org`` is the dict an auth seam resolved: ``org_id``, plus the
    credential's ``graph_id`` (``None`` for an org-wide credential), plus — for
    an org-wide credential only, and only since #10025 — ``default_graph_id``,
    stamped by that credential's own resolver (an API key, a hosted SESSION/JWT
    and an OAuth ``oat_`` token all answer since #10046).

    ``None`` is returned — never a guess — for every shape this seam must NOT
    name:

    * ``control_plane_ids`` is False. It is the caller's statement that the
      resolution did not come from the control plane (``is_supabase_enabled()``
      — the predicate that chose the resolver). The record's layer tables FK
      ``(org_id, graph_id) -> public.graphs (org_id, id)``, so only a
      control-plane graph id can be filed under: the SELF-HOST registry lane's
      ``graph_id`` is the Falkor ``:Graph`` node id (``g_<hex>``, minted in
      ``sdk._graph_create``) and registry graphs are never inserted into
      ``public.graphs``. Installing one would displace a correctly declared
      single-tenant pair with a value the FK cannot satisfy, turning a
      recording deployment into ``write_failed`` on every write with a cause
      nobody could attribute.
    * no usable ``org_id``, or a ``graph_id`` that IS present and is not a
      usable id (wrong type, empty). A credential that named a graph is
      answered by that graph or not at all — it must never fall through to the
      default tenant, which would widen a graph-bound credential into an
      org-wide one.
    * an org-wide credential whose default graph the control plane could not
      name: no row, no ``default_graph_id`` on the dict, a field of the wrong
      type, or the literal ``"default"`` sentinel (see below).

    HOW AN ORG-WIDE CREDENTIAL IS NAMED (#10025). Its tenant is the org's
    **default graph**, and it is read from ``default_graph_id`` — the id of the
    org's active ``kind='default'`` ``public.graphs`` row, which the control
    plane resolved and put on the credential. Never derived from the name,
    never a constant, and never resolved here: an unresolvable default leaves
    the write REFUSED and counted (``no_tenant``) — the fail-closed direction —
    rather than filing the row under a tenant nobody chose.

    ⚠️ Two different things are called ``default_graph_id`` in this codebase,
    and only ONE of them is an id. The credential field READ HERE is a real
    ``public.graphs.id`` (``g_<16hex>``). The org-list payload's field of the
    same name (``hosted_api``'s graphs listing) carries the ``"default"``
    SENTINEL from :func:`tortoise.supabase_control.graph_metadata`, which is
    not an id and cannot satisfy the record's FK — so the sentinel is refused
    here explicitly. Do not hand an org-list row to this function.

    ⛔ This is not a fallback chain and must not grow into one: it reads the
    credential the caller already resolved, and nothing else. The env pair is a
    different question with its own gate (:func:`single_tenant_declared`).
    """
    if not control_plane_ids or not isinstance(org, Mapping):
        return None
    org_id = org.get("org_id")
    if not (isinstance(org_id, str) and org_id):
        return None
    graph_id = org.get("graph_id")
    if graph_id is not None:
        # A PRESENT graph_id answers or refuses — it never falls through to the
        # org-wide default below (that would widen a graph-bound credential).
        return (org_id, graph_id) if (isinstance(graph_id, str) and graph_id) else None
    # The org-wide case (#10025): the default graph's id, READ from the
    # control-plane row the resolver put on this dict — never derived from its
    # name and never a constant, so a rename cannot move a write to another
    # tenant, and an org whose default row does not exist keeps refusing.
    default_graph_id = org.get("default_graph_id")
    if isinstance(default_graph_id, str) and default_graph_id:
        # The `"default"` sentinel is NOT an id (see the docstring): a
        # `public.graphs.id` is `g_<16hex>`, so filing under the sentinel would
        # produce a row the record's FK rejects. Refuse rather than fail later.
        if default_graph_id == "default":
            return None
        return (org_id, default_graph_id)
    return None


def single_tenant_declared() -> bool:
    """True when this deployment declared it serves exactly ONE record tenant.

    The env pair (:data:`RECORD_ORG_ENV`/:data:`RECORD_GRAPH_ENV`) is
    PROCESS-GLOBAL, so it can only be correct where the process serves one
    graph. This is the operator's statement of exactly that (#9192), and the
    default is the safe direction: **absent ⇒ multi-tenant ⇒ the pair is
    ignored**. A deployment that sets the pair without the declaration gets
    refused rows and a warning naming this variable — never a cross-tenant
    write, which is silent (the row is well-formed and the FK only checks that
    the graph exists).
    """
    return env_flag(RECORD_SINGLE_TENANT_ENV, default=False)


def record_tenant() -> tuple[str, str] | None:
    """The tenant a LIVE write is filed under: the scope's, else the env pair.

    Precedence is the point. A request-scoped tenant
    (:func:`use_record_tenant`) is the only correct answer in a process that
    serves more than one graph, so it always wins. The
    :data:`RECORD_ORG_ENV`/:data:`RECORD_GRAPH_ENV` pair is the SINGLE-TENANT
    deployment's arming surface (#9078 scope decision 3) — without it, arming
    the record would require a code change at every call site, which is how a
    capability ends up with no production caller again. A HALF-configured pair
    is not a tenant: it is refused, not completed.

    ⛔ The pair is consulted ONLY when the deployment declared single tenancy
    (:func:`single_tenant_declared`, #9192). Env is process-global, so on a
    process serving several tenants it is not a weaker answer than the scope —
    it is a WRONG one, and it fails silently: tenant A's rows land under tenant
    B's organisation, well-formed, FK satisfied. So an undeclared process gets
    ``None`` here (⇒ the write is REFUSED and counted ``no_tenant``), which is
    the one failure mode a live door may have (#9192).
    """
    scoped = _RECORD_TENANT.get()
    if scoped is not None:
        return scoped
    if not single_tenant_declared():
        return None
    org_id = (os.environ.get(RECORD_ORG_ENV) or "").strip()
    graph_id = (os.environ.get(RECORD_GRAPH_ENV) or "").strip()
    if org_id and graph_id:
        return (org_id, graph_id)
    return None


def record_tenant_effective(
        carried: tuple[str, str] | None = None) -> tuple[str, str] | None:
    """THE tenant a live write is filed under: **carried > scope > env pair**.

    ONE resolution, so the two places that must agree cannot drift apart: the
    seam that FILES the write (:func:`write_live_record`) and the producer that
    decides WHETHER the record can hold its data at all
    (``sdk._write_capture_turns``'s capability read, #10067).

    They asked two different questions once, and it was a silent data-shape
    defect: the producer asked :func:`record_tenant` — scope and env pair ONLY —
    while the seam applied the CARRIED tenant first. On the hosted REST lane,
    which carries its tenant BY VALUE on the SDK it opens and enters no ambient
    scope by design (:func:`tortoise.hosted_api._data_sdk`), that meant the
    producer answered "no tenant" (⇒ keep the turns in the GRAPH) for a write
    the record was accepting the whole time — the record-only tier reverted on
    the one lane built for multi-tenancy, with nothing failing and no counter
    moving (#10067 review P1).

    ``carried`` is that caller-resolved ``(org_id, graph_id)`` — the answer of
    the SAME request that chose the graph this write landed in. ``None`` (the
    default) is the historical behaviour, byte-identical: nothing is carried and
    the scope/env answer stands, so every #9192 fail-closed rule is untouched.

    ⚠️ VALIDITY IS NOT THIS FUNCTION'S JOB. A HALF or non-string pair is still
    refused at the seam as it always was (``no_tenant``, #9188 review F4), with
    its own specific warning. A caller that needs "would the seam ACCEPT this"
    asks :func:`is_usable_tenant` as well — precedence and validity are two
    questions, and merging them would weaken the seam's refusal.
    """
    return carried if carried is not None else record_tenant()


def is_usable_tenant(tenant: object) -> bool:
    """Is *tenant* a ``(org_id, graph_id)`` pair of NON-EMPTY strings?

    The seam's own admissibility test, extracted so the capability read that
    decides WHETHER to hand the record a write cannot believe a shape the seam
    will refuse (a half pair, a list, an empty id): believing it would skip the
    graph write for a write the record declines, i.e. the transcript in neither
    store — the #10067 defect itself.
    """
    return (isinstance(tenant, tuple) and len(tenant) == 2
            and all(isinstance(part, str) and part for part in tenant))


def _refused_tenant_env_note() -> str:
    """Why the env pair did not arm this write — and what to do instead.

    Only for the refusal WARNING. Two operator situations produce a `no_tenant`
    refusal and each needs its own sentence:

    * the pair IS set and the process has not declared itself single-tenant —
      the operator needs the name of the missing declaration, not a generic "no
      tenant in scope" that reads as if their configuration vanished;
    * nothing is set at all (no scope, no pair) — the common case on the MCP
      lane, where a credential that names no graph installs no scope. Telling
      that operator to "wrap the write in ``use_record_tenant``" is advice they
      cannot act on (the lane already does it per request); the actionable
      sentence is the one naming a graph-bound credential (#9192 review A5).
    """
    org_id = (os.environ.get(RECORD_ORG_ENV) or "").strip()
    graph_id = (os.environ.get(RECORD_GRAPH_ENV) or "").strip()
    if org_id and graph_id:
        return (
            f" {RECORD_ORG_ENV}/{RECORD_GRAPH_ENV} ARE set, but this process "
            f"has not declared itself single-tenant, so the process-global "
            f"tenant was REFUSED instead of filing every tenant's rows under "
            f"one graph (#9192): set {RECORD_SINGLE_TENANT_ENV}=1 ONLY in a "
            f"deployment that serves exactly one graph, and wrap a "
            f"multi-tenant write in record_writer.use_record_tenant(...)."
        )
    if org_id or graph_id:
        return (
            f" {RECORD_ORG_ENV}/{RECORD_GRAPH_ENV} is a HALF pair (one set, "
            f"one empty) — that is a misconfiguration, not a tenant, and it is "
            f"never completed by guesswork."
        )
    return (
        " No tenant was in scope and no env pair is set: on the MCP and hosted "
        "REST lanes the tenant is installed from a GRAPH-bound key per "
        "request, so this means the credential named no graph (use a "
        "graph-bound `tk_` key); a single-tenant deployment instead sets "
        f"{RECORD_ORG_ENV}/{RECORD_GRAPH_ENV} AND "
        f"{RECORD_SINGLE_TENANT_ENV}=1."
    )


def _default_connect(dsn: str, *,
                     connect_timeout: int | None = None) -> Any:
    """Open the record's connection. The ONE place a driver choice lives.

    Lazily imported so importing this module needs no driver (the DB-free pin,
    and ``psycopg2`` is an optional extra: ``.[postgres]``). ``autocommit`` is
    deliberately NOT touched: the transaction boundary belongs to the caller,
    and turning autocommit on here would commit each statement of a projection
    that §3 requires to be atomic with the journal append.

    ⛔ IT MAY RETURN A REUSED CONNECTION (#9558 / R10). The live door calls the
    seam once per journal record, and each call used to pay a full TCP connect
    and auth handshake — the BEFORE tree measured **18.9–20.1 ms** per record
    (`per_record_ms` means across the four committed runs in
    `docs/evidence/9558-record-write-cost/runs/`) with `connect()` once per
    record. Pooling lives HERE, behind the one function whose job is "give me
    a connection", rather than at a call site, for one reason: it changes the
    cost of a connection and NOTHING about who owns one. `RecordWriter.write`
    still neither commits nor rolls back (not even at RELEASE: the pooled
    handle's ``close()`` parks the connection as it stands, and the pool resets a
    leftover transaction on the next BORROW), `RecordWrite.connection` is still
    the handle its caller commits, and §2.0's unarmed path still opens nothing.

    The handle returned is a :class:`_PooledConnection` whose ``close()``
    RETURNS it to the pool instead of closing it — that is the whole contract
    change, and it is invisible to every caller that closes the handle it was
    given, which is what this module's two disposal sites already do.

    A connection is reused only when it is provably still attached to a live
    server (see :func:`_connection_is_reusable`), and a burst larger than the
    idle set opens new connections rather than queueing: a write is never
    delayed or refused because the pool is busy.
    """
    reused = _take_idle_connection(dsn)
    if reused is not None:
        return _pooled_connection(reused, dsn)
    try:
        import psycopg2
    except ImportError as exc:  # pragma: no cover - exercised by install lanes
        raise RecordWriteError(
            "the record is armed but no Postgres driver is installed — install "
            "the 'postgres' extra (pip install -e '.[postgres]')"
        ) from exc
    # The operator's bound is a QUERY KEY, not a substring: `dsn` is the whole
    # URI, and a password such as `postgresql://svc:connect_timeout@host/db`
    # (needs no percent-encoding, passes `_DSN_SCHEMES`) would otherwise take
    # the "already bounded" branch and restore libpq's wait-forever default on
    # the per-record connect this constant exists to cap (#9231 review P2).
    # ⛔ `urlsplit().query` — never the whole `dsn` — is the surface EVERY one of
    # these questions is asked against: the parked-handle bound below repeated
    # the substring mistake for one cycle, and a password that merely ENDS in
    # `idle_in_transaction_session_timeout` silently removed the bound (#9558
    # review cycle 5).
    query = parse_qs(urlsplit(dsn).query)
    connect_kwargs: dict[str, Any] = {}
    if "connect_timeout" in query:
        # The operator bounded it already; do not replace their number.
        pass
    else:
        connect_kwargs["connect_timeout"] = (connect_timeout
                                             or CONNECT_TIMEOUT_SECONDS)
    # ⛔ EVERY connect carries the parked-handle bound (#9558 review cycle 4,
    # P1), because the handle it opens can be PARKED later and the bound has to
    # be on the session, not on the call that happened to create it. `options`
    # is a STARTUP parameter, so this adds no round trip and cannot be lost on
    # reuse. An operator's own value wins, same rule as `connect_timeout`.
    # ⛔ And `options` is ONE STARTUP field: psycopg2's keyword REPLACES a value
    # the DSN already carries (`extensions.make_dsn` — verified, not assumed), so
    # passing only our `-c` would silently drop the operator's own startup
    # options (`-c statement_timeout=…`, `-c search_path=…`, `application_name`)
    # on every connect. Theirs is PRESERVED and the bound APPENDED.
    operator_options = " ".join(query.get("options", []))
    already_bounded = (
        "idle_in_transaction_session_timeout" in query
        or "idle_in_transaction_session_timeout" in operator_options
    )
    if not already_bounded:
        connect_kwargs["options"] = (
            f"{operator_options} -c idle_in_transaction_session_timeout="
            f"{IDLE_IN_TRANSACTION_TIMEOUT_SECONDS * 1000}"
        ).strip()
    raw = psycopg2.connect(dsn, **connect_kwargs)
    return _pooled_connection(raw, dsn)


#: How many IDLE connections the pool keeps per DSN (#9558 / R10).
#:
#: Two, because the record write path is one record per call and its natural
#: concurrency is "a few capture threads", not a pool of workers: the second slot
#: covers one thread committing while another acquires, and anything beyond it is
#: a burst the process should not pay to keep alive. ⛔ This is an UPPER bound on
#: IDLENESS, never on connections — a cache miss opens a fresh connection and a
#: write is never delayed or refused for want of a pooled one, so a burst is
#: slower (it connects) instead of failing.
RECORD_POOL_MAX_CONNECTIONS = 2

#: How many DSN BUCKETS may hold idle handles at once. `RECORD_POOL_MAX_CONNECTIONS`
#: is per bucket, so without this a process that re-arms the DSN (a rotating
#: credential, a per-tenant DSN — `record_dsn()` re-reads the environment on
#: every call) would leave up to two live sockets behind for EVERY value it ever
#: saw. Past this many buckets the least-recently-created one is drained and its
#: handles closed, one bucket at a time. ⛔ The schema cache does the OPPOSITE on
#: overflow — it drops WHOLESALE (`_SCHEMA_CACHE.clear()`) — because its entries
#: are cheap to rebuild and its live set is tiny; do not model one on the other.
#: Both bounds exist for the same reason: the process is long-lived.
RECORD_POOL_MAX_IDLE_KEYS = 4

#: Idle connections, keyed by the SHA-256 of their DSN (never the DSN itself: the
#: pool is not a new place for a credential to sit in memory).
_IDLE_CONNECTIONS: dict[str, list[Any]] = {}
_IDLE_CONNECTIONS_LOCK = threading.Lock()


def _pool_key(dsn: str) -> str:
    """The pool bucket for `dsn` — a full digest, so no two DSNs can collide."""
    return hashlib.sha256(dsn.encode("utf-8")).hexdigest()


def _transaction_statuses() -> tuple[int, int]:
    """`(IDLE, UNKNOWN)` — psycopg2's transaction-status constants, lazily.

    ONE function for both constants for the same reason this module has one
    driver hook: two lazy imports of the same module are two places to lose the
    laziness, and a test that must model a connection without the driver
    installed has one name to patch instead of two.

    Imported inside the caller rather than at module scope, per this module's
    standing rule: importing the seam must not require a driver.
    """
    from psycopg2 import extensions

    return (
        extensions.TRANSACTION_STATUS_IDLE,
        extensions.TRANSACTION_STATUS_UNKNOWN,
    )


#: The ONE statement a borrowed handle is asked when its socket has input
#: pending. Not a health check on every borrow — see
#: :func:`_verify_pending_input` for why it is paid only in that window.
PENDING_INPUT_PROBE_SQL = "SELECT 1"


def _socket_has_pending_input(conn: Any) -> bool:
    """Is there unread input on this handle's socket? **Locally, no round trip.**

    The question `closed` and `transaction_status` cannot answer: both are
    CLIENT-side caches, and psycopg2 only refreshes them when a statement touches
    the socket. Measured on PG16 + psycopg2 2.9.13: a handle parked while its
    backend was alive still reports `closed=0, transaction_status=IDLE` after
    `pg_terminate_backend`, so the pool lent it out, the door's FIRST statement
    raised ``OperationalError: server closed the connection unexpectedly``, and
    one record was lost and counted ``write_failed`` (#12822's production
    counter, ~0.6 % of writes on the hosted deployment).

    A parked IDLE handle has nothing outstanding — psycopg2 consumes the whole
    reply, notices included, before the caller sees a result — so unread input
    means the peer spoke unasked, and only two things can have done that: it
    ENDED the connection, or it SENT something (a `NOTIFY` to a session that
    `LISTEN`s leaves a perfectly healthy handle readable). This function reports
    only the readiness, because a local read cannot tell those two apart:
    PostgreSQL's own shutdown sends a FATAL ErrorResponse before the FIN, so the
    first byte pending on a dead connection is ``'E'``, not EOF (measured) —
    sniffing it would be a second, silent copy of the wire format. The caller
    asks the SERVER when this says yes (:func:`_verify_pending_input`).

    Anything this cannot ask — a fake handle with no `fileno`, a driver that is
    not socket-backed, an fd the poller refuses — is answered ``False`` ("nothing
    is known to be pending").

    ⛔ THE TWO DIRECTIONS ARE NOT SYMMETRIC, and this function's own caller makes
    the difference load-bearing: ``True`` is what ADDS the verification
    (``if _socket_has_pending_input(...) and not _verify_pending_input(...)``),
    while ``False`` SKIPS it. ``False`` can never invent a discard — the caller
    reads it as "nothing is known" — but skipping is exactly the fail-open this
    module must not have quietly, and on a long-lived API process the case that
    produces it is the normal one rather than an edge one: fds climb past 1023,
    and ``select.select`` REFUSES an fd at or above ``FD_SETSIZE`` (measured:
    ``select.select([1060], ...)`` raises ``ValueError: filedescriptor out of
    range in select()``, whose ``except`` would have turned every borrow into
    "nothing pending" — the corpse lent unchanged and #12822 open again, with no
    counter and no log to show for it). Hence :func:`select.poll`, which has no
    fd ceiling, is registered for ``POLLIN`` only, returns the same readiness and
    consumes no byte. Where ``poll`` does not exist (Windows) this answers
    ``False``: the pre-fix behaviour, never a wrong record.

    Measured cost, 20 000 calls on an idle fd: **~1–2 µs/call** (1.7 µs on the
    host this was developed against, 0.9 µs on a reviewer's), and it sends
    nothing. A single-digit-microsecond claim, not a portable constant.
    """
    try:
        fileno = conn.fileno()
    except Exception:
        return False
    if not isinstance(fileno, int) or fileno < 0:
        return False
    try:
        poller = select.poll()
        poller.register(fileno, select.POLLIN)
    except Exception:
        return False
    try:
        return bool(poller.poll(0))
    except Exception:
        return False


def _verify_pending_input(conn: Any) -> bool:
    """Ask the server, ONCE, whether a handle with pending input is still usable.

    ⛔ THE ROUND TRIP IS PAID ONLY IN THIS WINDOW, and that placement is the whole
    design. A parked handle normally has a quiet socket, so the common borrow path
    pays single-digit microseconds and nothing else; this runs only when the
    readiness read found
    input, which happens once per peer death and once per notification. That is
    what makes it affordable to be CERTAIN: the alternative the module rejected
    ("a health query per acquire … a round trip on every record") is exactly the
    cost this avoids.

    What it does on a HEALTHY handle is answer the pending input and leave the
    handle as it was parked: the probe statement consumes whatever the peer sent,
    and the `rollback()` clears the transaction the probe opened, so a NOTIFY that
    a `LISTEN`ing session is entitled to stays delivered (measured: the
    notification is still in `conn.notifies`) and the next borrower's transaction
    starts clean. A statement that fails — the connection is gone, however the
    driver words it — returns ``False`` and the caller DISCARDS the handle: the
    next record gets a fresh connect instead of inheriting a corpse.

    ⚠️ THE PAIR COSTS **TWO** ROUND TRIPS, and the honest price is stated rather
    than hidden: the probe's own statement, then the `rollback()` that clears the
    transaction it opened (measured on two hosts, shipped driver: **0.43–1.3 ms**
    for the pair, against single-digit µs for a `rollback()` on an already-IDLE
    handle). It is paid ONCE PER EVENT — a death or a notification — never per
    record, which is what makes certainty affordable here; the common borrow
    (quiet socket) pays no round trip at all. ⛔ The rollback is NOT dropped to
    make this read as one round trip: `_reset_parked_transaction` would clear the
    same transaction a line later in today's only caller, and depending on the
    caller's next line is how a helper becomes a landmine for the next one.
    """
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(PENDING_INPUT_PROBE_SQL)
            cursor.fetchone()
        finally:
            with contextlib.suppress(Exception):
                cursor.close()
        conn.rollback()
        return True
    except Exception:
        return False


def _connection_is_reusable(conn: Any) -> bool:
    """Is this idle handle still attached to a live server connection?

    Two questions, both cheap and both LOCAL: `closed` (psycopg2 noticed) and
    `transaction_status == UNKNOWN` (psycopg2 has not, but the protocol says the
    link is gone). Neither touches the network — see :func:`_park_connection` for
    why the pool does not poke the server on every acquire.

    ⛔ IT IS DELIBERATELY NOT THE WHOLE ANSWER ON THE BORROW PATH. Both fields are
    client-side caches that psycopg2 refreshes only when a statement touches the
    socket, so a handle parked IDLE whose peer died passes this and is a CORPSE
    (#12822). :func:`_take_idle_connection` therefore follows it with
    :func:`_socket_has_pending_input` + :func:`_verify_pending_input`, which ask
    the socket and then — only if there is anything to ask about — the server.
    The split matters at RELEASE (`_park_connection`), where a round trip is
    forbidden: there the two local fields are all that may be paid.

    A handle that cannot even report its status — or a process where the driver
    cannot be imported — is NOT reusable, which fails towards the old
    close-per-record behaviour rather than towards reusing something unverified.
    """
    if conn is None or conn.closed:
        return False
    try:
        _idle, unknown = _transaction_statuses()
        return conn.info.transaction_status != unknown
    except Exception:
        return False


def _reset_parked_transaction(conn: Any) -> None:
    """Clear a transaction an earlier borrower left open — at REUSE, not release.

    ⛔ THIS ROUND TRIP BELONGS ON THE BORROW PATH, AND THAT PLACEMENT IS THE
    CONTRACT (#9558 review cycle 3, P1). `_park_connection` used to roll back on
    RELEASE. Every read-door borrow ends INTRANS (``open_record`` issues a
    statement and never commits), so that put a server round trip on the release
    of every read — after the rows were already fetched, and therefore before the
    answer was returned. libpq has no per-statement timeout, so a backend that
    wedged in that window blocked the release forever and DISCARDED a read that
    had already succeeded, which is exactly what `open_record`'s own "a served
    read must not be able to hang a request on a wedged database" promises it
    cannot do. At BORROW the caller is about to talk to the server anyway, so a
    hang there is the pre-existing failure mode of the door rather than a new one.

    It doubles as the pool's LIVENESS PROBE for the handles that need it: a
    handle parked INTRANS whose server died while it sat idle passes
    `_connection_is_reusable` (both `closed` and `transaction_status` are STALE
    client-side caches until something touches the socket), and the rollback is
    the first thing to touch it — so a dead handle is discarded here, before the
    door opens, instead of surfacing as a spurious `RecordUnavailable` to a
    caller that a fresh connect would have served (#9558 review cycle 3, P1).
    """
    idle_status, _unknown = _transaction_statuses()
    if conn.info.transaction_status != idle_status:
        conn.rollback()


def _take_idle_connection(dsn: str) -> Any | None:
    """An idle connection for `dsn`, or None when there is nothing to reuse.

    A handle the server no longer backs — or one whose parked transaction cannot
    be reset — is DISCARDED here rather than returned, so one record pays for a
    server restart and the next acquire connects fresh.

    ⛔ THE IDLE CASE IS THE ONE THAT LOST RECORDS (#12822). `closed` and
    `transaction_status` cannot see a peer that hung up while the handle was
    parked, so the borrow also reads the SOCKET (:func:`_socket_has_pending_input`,
    ~1-2 µs, one `poll(0)` syscall, no round trip) and, only when something is
    pending, asks the SERVER (:func:`_verify_pending_input`: one `SELECT 1` and
    its rollback, i.e. two round trips, measured 0.43–1.3 ms). Quiet socket ⇒ NO
    round trip; pending input ⇒ that verification, which is either a death found
    or a notification delivered to a handle that then stays in service. Paying
    it on EVERY borrow instead would be the "health query per acquire" this
    module rejected, and skipping it is what let a corpse through.

    Every blocking step (the verification, the reset, and the discard's Terminate
    packet) happens OUTSIDE the lock, one candidate at a time: `close()` is a
    socket write that can block on a full send buffer, and holding the pool lock
    across it would stall every other borrower and every release for that DSN
    behind one dead handle.
    """
    key = _pool_key(dsn)
    while True:
        with _IDLE_CONNECTIONS_LOCK:
            idle = _IDLE_CONNECTIONS.get(key)
            candidate = idle.pop() if idle else None
        if candidate is None:
            return None
        if _connection_is_reusable(candidate):
            # A quiet socket is the normal case and costs nothing beyond the
            # readiness read; pending input means the peer spoke, and only the
            # server can say whether it spoke to end us or to notify us.
            if _socket_has_pending_input(candidate) and not \
                    _verify_pending_input(candidate):
                with contextlib.suppress(Exception):
                    candidate.close()
                continue
            try:
                _reset_parked_transaction(candidate)
            except Exception:
                pass
            else:
                return candidate
        with contextlib.suppress(Exception):
            candidate.close()


def _pooled_connection(raw: Any, dsn: str) -> _PooledConnection:
    """Wrap `raw` so its `close()` RETURNS it to the pool instead of closing it."""
    return _PooledConnection(raw, _pool_key(dsn))


class _PooledConnection:
    """A connection borrowed from the pool; ``close()`` gives it back (#9558).

    WHY A WRAPPER AND NOT A CALL-SITE CHANGE. The WRITE half's two dispose sites
    — :meth:`RecordWriter.write`'s failure path and :func:`write_live_record`'s
    ``finally`` — both already do the right thing (close what you opened, exactly
    once, on every path), and so does every READ caller of
    :func:`_default_connect`: that hook is not the writer's alone
    (`record_serve.open_record` uses it too, and its own disposal sites close the
    handle the same way). Making each of them pool-aware instead would have meant
    a second release mechanism beside the one every caller already uses, and a
    caller that closed the handle itself would
    have leaked a pool slot with no error. Here there is exactly one contract:
    **closing the handle returns it.** A double close is a no-op.

    WHAT IT IS NOT. It is not a connection: only the DB-API surface this tree
    uses is forwarded explicitly (``cursor``/``commit``/``rollback``/``close``/
    ``closed``), and everything else — ``info``, ``autocommit``, … — goes through
    ``__getattr__``/``__setattr__`` to the real handle, so a caller that reads or
    sets session state still reaches the server. ``isinstance`` against
    ``psycopg2.extensions.connection`` is the one thing it does NOT satisfy, and
    nothing in this tree does that.

    ⛔ THE FENCE HAS A THIRD DOOR, and it is the one no flag on THIS object can
    close: a cursor obtained BEFORE the release. ``cursor()`` returns the
    driver's own cursor, which holds no reference to ``_returned``, so fencing
    ``cursor``/``commit``/``rollback`` and both attribute doors left it fully
    usable on a handle the pool may already have lent to another thread. Before
    this wrapper existed it was ``connection.close()`` that invalidated such a
    cursor — psycopg2 raises ``InterfaceError`` from any cursor operation once
    its connection is closed (<https://www.psycopg.org/docs/cursor.html>) — and
    parking a handle no longer closes it. So :meth:`close` closes every cursor
    it issued before parking, which restores exactly the pre-change loud error
    (#9558 review at `b8371c2350`, P2, reproduced).

    ⚠️ The transaction is the caller's, exactly as before: ``commit()`` forwards.
    What the pool adds is a reset of a handle that is not IDLE, charged to the
    next BORROW rather than to release — so an aborted projection still cannot be
    inherited by the next record, but the round trip happens where the caller is
    about to issue statements anyway (#9558 review cycle 4, P1). ⛔ It is NOT a
    rollback on release: that put a server round trip on the release of every
    read borrow, after the rows were fetched and before the answer was returned.
    """

    def __init__(self, raw: Any, key: str) -> None:
        object.__setattr__(self, "_raw", raw)
        object.__setattr__(self, "_key", key)
        object.__setattr__(self, "_returned", False)
        # Cursors this borrow ISSUED and has not been told are closed. Bounded
        # by one borrow's worth: the wrapper is built per borrow, never reused.
        object.__setattr__(self, "_issued_cursors", [])

    # — the DB-API surface the record path uses —
    def _live(self) -> Any:
        """The raw handle, REFUSING use once returned.

        `close()` must be a fence, not merely a release: before this change a
        statement on a closed connection raised the driver's own
        ``InterfaceError``. Now the handle is still open and may be borrowed by
        ANOTHER thread, so a late ``commit()`` on a returned wrapper would commit
        that thread's transaction and a late ``cursor()`` would issue statements
        on a connection this caller no longer owns — silent cross-talk replacing
        a loud error. The driver's own exception type is raised (lazily imported:
        it is optional at module scope, exactly like `_default_connect`'s).
        """
        if self._returned:
            try:
                from psycopg2 import InterfaceError  # type: ignore[import-not-found]
            except Exception:  # pragma: no cover - driver absent
                raise RuntimeError(
                    "connection already returned to the pool") from None
            raise InterfaceError("connection already returned to the pool")
        return self._raw

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        cur = self._live().cursor(*args, **kwargs)
        object.__getattribute__(self, "_issued_cursors").append(cur)
        return cur

    def commit(self) -> None:
        self._live().commit()

    def rollback(self) -> None:
        self._live().rollback()

    def close(self) -> None:
        """Return the handle to the pool. Idempotent, and never raises.

        It first invalidates every cursor this borrow issued — see the class
        docstring's "THIRD DOOR" note. A cursor already closed by its caller is
        closed again here, which psycopg2 makes a no-op (``cursor.close()`` does
        not raise for an already-closed cursor), so the common
        ``with conn.cursor() as cur:`` shape costs nothing.
        """
        if self._returned:
            return
        object.__setattr__(self, "_returned", True)
        self._close_issued_cursors()
        _park_connection(self._key, self._raw)

    def _close_issued_cursors(self) -> None:
        """Invalidate the cursors this borrow handed out. **Never raises.**

        The wrapper's whole contract with its callers is "close() returns the
        handle and is safe on every path", so a driver failure while closing a
        cursor (a dead server, a half-closed socket) must not escape and mask the
        write failure the caller is already reporting. A cursor with no
        ``close`` at all — the shape some fakes have — is simply forgotten.
        """
        cursors = object.__getattribute__(self, "_issued_cursors")
        while cursors:
            cur = cursors.pop()
            close = getattr(cur, "close", None)
            if close is None:
                continue
            with contextlib.suppress(Exception):
                close()

    @property
    def closed(self) -> Any:
        """Logically closed once returned, exactly like a closed connection."""
        return True if self._returned else self._raw.closed

    # — everything else is the real handle's —
    def __getattr__(self, name: str) -> Any:
        # ⛔ METHODS ARE THE SECOND DOOR TO THE RAW HANDLE, so they take the same
        # fence as the three DB-API verbs. A psycopg2 session mutator is reached
        # as a METHOD, not an attribute write: `handle.set_session(autocommit=True)`
        # goes through here, and before this fence it reached the parked handle
        # AFTER `_park_connection` had normalised it — re-poisoning the SAME
        # connection for the next borrower, which is the state the normalisation
        # exists to prevent (`__setattr__` closed the attribute-write door only;
        # #9558 review, P2, reproduced). `set_isolation_level`, `reset` and
        # `set_client_encoding` are the same shape.
        #
        # Attribute READS stay available: reading state cannot corrupt another
        # borrower, and a debugger or `repr` must still work.
        attr = getattr(object.__getattribute__(self, "_raw"), name)
        if object.__getattribute__(self, "_returned") and callable(attr):
            self._live()   # raises the driver's InterfaceError
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        # `autocommit` and friends must reach the server, not vanish onto the
        # wrapper. Only this class's own state is underscore-prefixed.
        #
        # ⛔ FENCED ON RELEASE, exactly like the three DB-API verbs above. Without
        # this the fence is 3/5 complete: `handle.close()` parks and normalises
        # the handle, and a later `handle.autocommit = True` would still reach the
        # raw — re-poisoning the SAME handle for the next borrower *after*
        # `_park_connection` had cleaned it, which is precisely the state the
        # normalisation exists to prevent (#9558 review cycle 2, P2, reproduced).
        # Attribute READS stay available (see `__getattr__`): reading state cannot
        # corrupt another borrower, and a debugger or `repr` must still work.
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._live()
        setattr(object.__getattribute__(self, "_raw"), name, value)

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        state = "returned" if self._returned else "borrowed"
        return f"<PooledConnection {state} {self._raw!r}>"


def _park_connection(key: str, conn: Any) -> None:
    """Return `conn` to the pool, or close it. **Never raises** — every path.

    A release failure must not mask a write failure that is already being
    reported by the caller (the same reason the previous ``close()`` was wrapped
    in `contextlib.suppress`).

    TWO THINGS IT DOES ON PURPOSE:

    * it DISCARDS a handle the server no longer backs (`closed`, or
      `TRANSACTION_STATUS_UNKNOWN`), so a restart cannot poison the pool;
    * it does **NOT** roll back an unfinished or aborted transaction. A failed
      projection leaves the transaction aborted, and handing that to the next
      record would fail it for the previous record's reason — so the reset is
      mandatory, but it happens at REUSE (`_reset_parked_transaction`), not here.
      ⛔ That placement is deliberate and is pinned by
      ``tests/test_9558_record_write_cost.py::test_a_parked_transaction_is_reset_at_reuse_not_at_release``:
      a release rollback is a server round trip on EVERY read borrow, taken after
      the rows are fetched and therefore before the answer is returned, so a
      backend that wedges in that window throws away a completed read (P1,
      #9558 review cycle 3).

    ⚠️ WHAT IT COSTS: nothing on the wire, on either door. It reads two LOCAL
    fields, normalises a client-side flag, and appends to a list. The single
    round trip the pool sometimes needs — resetting a transaction an earlier
    borrower left open — is charged to the next BORROW, where the caller is about
    to issue statements anyway. The whole mechanism replaces the connect+auth the
    old close-per-record path paid (**12.5–13.9 ms**, the connection half of the
    BEFORE record's 18.9–20.1 ms — not the whole record, per
    `docs/evidence/9558-record-write-cost/`).

    ⚠️ WHAT A PARKED HANDLE STILL HOLDS — the price of not resetting here, and
    it is not zero. The READ door parks INTRANS on purpose, and an INTRANS handle
    holds an ``AccessShareLock`` on the tables the read touched until its
    transaction ends (measured on PostgreSQL 16: such a session blocks
    ``ALTER TABLE``/``DROP``/``VACUUM FULL``/``CLUSTER``/``TRUNCATE`` on those
    tables, though not plain DML). The resolution is SERVER-side and needs no
    round trip: every connection this module opens carries
    :data:`IDLE_IN_TRANSACTION_TIMEOUT_SECONDS` in its STARTUP ``options``, so a
    parked transaction cannot outlive that bound. A handle the server ends this
    way is handled by the ordinary path — the borrow-time reset raises, the
    handle is discarded, the caller gets a fresh connect — so the bound costs an
    occasional reconnect, never a surfaced error. (An INERROR handle is the
    cheaper case: an aborted transaction releases its locks at the error, so the
    WRITE door's dirty park holds no lock at all.)

    ⚠️ It also NORMALISES `autocommit` back to False. The wrapper forwards session
    state on purpose (`__setattr__`), so a caller may legitimately set it — but a
    handle parked in autocommit mode is poisonous in two ways nobody would
    attribute to the pool: `_apply_tenant_scope`'s transaction-local `set_config`
    sets NOTHING on an autocommit connection, so every later record fails with a
    tenant-GUC error that names the DSN; and `open_record`'s transaction-local
    `statement_timeout` stops applying, so reads lose their bound silently.

    ⚠️ It does NOT poke the server, and this is why it cannot promise a dead
    handle is always caught here. A health ``SELECT 1`` per park would cost a
    round trip on every record. Both fields it reads are LOCAL and therefore
    STALE once the peer has gone: a handle parked while the server was alive can
    have died since, and psycopg2 reports it as usable until something touches the
    socket. What that costs depends on the DOOR, and the two are not the same —
    an earlier version of this docstring asserted one answer for both and was
    wrong (#9558 review cycle 3, P1):

    * the WRITE door's happy path parks IDLE, so nothing here touches its socket
      and the reset below is a no-op for it. That door used to find the death at
      USE — one lost record per dead handle, counted ``write_failed`` and healed
      by replay, with the breaker correctly not tripped because the record is
      reachable. ⛔ THE POOL NO LONGER ACCEPTS THAT COST: measured in production
      at 8 lost writes / 1292 attempts (~0.6 %) for one hosted deployment, the
      `write_failed` counter is what flipped `record.degraded` to true. The
      BORROW path now reads the socket and, when anything is pending, asks the
      server once (:func:`_take_idle_connection`), so the dead handle is
      discarded before the door opens and the record is WRITTEN on a fresh
      connect rather than lost and replayed (#12822);
    * the READ door parks INTRANS, so ``_reset_parked_transaction`` touches the
      socket BEFORE the door opens and discards the handle — the fresh connect is
      then paid by the pool, not by the caller as a ``RecordUnavailable``.
    """
    try:
        if conn is None or conn.closed:
            return
        if not _connection_is_reusable(conn):
            with contextlib.suppress(Exception):
                conn.close()
            return
        if getattr(conn, "autocommit", False):
            # Set through the wrapper by a caller; the next borrower must not
            # inherit it (see the docstring: a transaction-local `set_config`
            # sets nothing in autocommit mode).
            conn.autocommit = False
        evicted: list[Any] = []
        with _IDLE_CONNECTIONS_LOCK:
            idle = _IDLE_CONNECTIONS.get(key)
            if idle is None:
                # A NEW bucket: make room by draining the oldest ones, so a
                # re-armed DSN cannot leave two sockets per value alive forever.
                over = len(_IDLE_CONNECTIONS) - RECORD_POOL_MAX_IDLE_KEYS + 1
                for other in list(_IDLE_CONNECTIONS)[:max(0, over)]:
                    evicted.extend(_IDLE_CONNECTIONS.pop(other))
                idle = _IDLE_CONNECTIONS.setdefault(key, [])
            if len(idle) < RECORD_POOL_MAX_CONNECTIONS:
                idle.append(conn)
                conn = None
        for old in evicted:
            with contextlib.suppress(Exception):
                old.close()
        if conn is not None:
            conn.close()
    except Exception:
        with contextlib.suppress(Exception):
            conn.close()


def _release_pooled_connections() -> None:
    """Close every idle connection. Never raises.

    The TEST suite's isolation seam today: an idle handle outlives the record it
    wrote, exactly like any pool's, so a test that must not inherit one says so
    here rather than assuming garbage collection will. ⚠️ It is NOT registered
    as a shutdown hook — nothing in production calls it, and the sockets are
    reclaimed by process exit, which is what a pool holding at most
    `RECORD_POOL_MAX_IDLE_KEYS` x `RECORD_POOL_MAX_CONNECTIONS` handles can
    afford. Called from the `test_9558_record_write_cost` fixture (and named in
    that file as the seam it is) rather than claimed as a lifecycle verb this
    module does not have.
    """
    with _IDLE_CONNECTIONS_LOCK:
        idle_lists = list(_IDLE_CONNECTIONS.values())
        _IDLE_CONNECTIONS.clear()
    for idle in idle_lists:
        for conn in idle:
            with contextlib.suppress(Exception):
                conn.close()


#: One record type's embedding text, as the parts the text is composed from.
#: The ENTRY is a CALLABLE rather than a list of field names, and that is a
#: correctness requirement rather than a style choice: every composition here has
#: its own shape (`DocumentCreated` calls a function whose third argument is a
#: LIST, `EventRecorded` joins three parts with the falsy ones dropped), so a
#: field-name list would DESCRIBE the datum without being able to produce it — a
#: seam that joined such a list would stringify a document's `topics` and keep an
#: event's empty ones (`#13412`). The callable IS the plug point, and it returns
#: the COMPOSITION and nothing else: the arms that REFUSE to derive (a carried
#: vector, a session `:Source`, a source record that states no text — see
#: `derived_embedding_columns` below) are the CALLER's, so a consumer of this table
#: must apply them too, or it will embed a session source's topic lexicon.
_EmbeddingTextParts = Callable[[Mapping[str, Any]], Sequence[tuple[str, Any]]]

def _session_source_skip(payload: Mapping[str, Any]) -> str:
    """Why a SESSION `:Source` derives no vector here, or `""` if it is not one.

    §9.4: *"one vector attached to the `:Source` node, whose embedded text is the
    S1 narrative read from `public.session_narrative` — ⛔ never from
    `:Source.summary`"*. The narrative is a RECORD column (`#9555`), not a
    journal key, so this fold cannot compose it: a derivation from the journal
    line would encode the node's topic LEXICON (`#10000` puts it in
    `_searchText`) — the wrong text in a column the narrative arm must own.

    ⛔ THE CLASSIFICATION IS NOT RE-IMPLEMENTED. `cache_projection.
    is_episodic_source_node` is the repo's ONE declaration of a session
    `:Source`'s identity (two arms, never a flag: a `session:` url prefix, or
    `sourceKind == 'agentSession'` on a non-URL) and it already reads GRAPH
    property names, which is exactly what a journal payload carries. A second
    predicate here is how the two would drift.

    Fail-CLOSED on an unreadable predicate (`"the predicate is unreadable"` ⇒
    no vector) rather than fail-open: this function's answer decides WHICH TEXT
    a column encodes, and a wrong text is invisible where a missing vector is
    counted (`#13417`).

    ⚠️ NO WRITER COMPUTES THE NARRATIVE VECTOR TODAY, so this arm does not take
    a vector away from anyone: the encode calls on the capture path are the
    Point/turn ones, and nothing reads `public.session_narrative` to embed it.
    The population therefore has no vector on either side, and §14.3 V2's answer
    for the `:Source` label does not require inventing one out of the wrong text
    — `#13418`'s indicator 1 allows a documented skip in exactly this shape.
    """
    # ⛔ THE IDENTITY IS RESOLVED THE WAY BOTH LAYERS RESOLVE IT, before the
    # predicate sees it. `is_episodic_source_node` reads `props["url"]` (its own
    # docstring: it is the CACHE side's vocabulary), while the record resolves a
    # `:Source` key as `url OR id` (`record_projection._key_value`: "Absence is the
    # one shape that is a real producer — `add_document` emits `id` and no `url` at
    # all") and the fold does the same (`projection/entities.py:7173`, `raw_key =
    # url or sid`). Handing the payload over VERBATIM meant an `id`-keyed
    # `session:…` source was classified as a plain one and got the topic lexicon
    # embedded — the exact text §9.4 forbids, on the row `document_kind IS NULL`
    # routes to the `source` dense leg. Latent today (both in-tree emitters pass
    # `url=`), and free to close here.
    resolved = payload
    if payload.get("url") is None and payload.get("id") is not None:
        resolved = {**payload, "url": payload.get("id")}
    try:
        from .cache_projection import is_episodic_source_node

        episodic = is_episodic_source_node(resolved)
    except Exception:  # noqa: BLE001, RUF100 — unclassifiable ⇒ derive nothing
        return ("the episodic-source predicate is unreadable, so the session "
                "narrative rule (STORAGE §9.4) cannot be ruled out")
    if not episodic:
        return ""
    return ("a session :Source embeds the S1 narrative read from "
            "public.session_narrative (STORAGE §9.4), which the journal line "
            "this fold reads does not carry")


#: ⛔ MOVED (#13447). The per-label text rule now lives in the ONE seam both
#: callers read — :data:`tortoise.derived_embedding.TEXT_PARTS_BY_RECORD_TYPE` —
#: because the live write and the rebuild cannot be allowed to disagree about the
#: text they encode (a second copy here is the §12.2a "second rule set" defect).
#: The name is kept as an ALIAS, not a copy, because the record's own docs and the
#: parity test (`tests/test_13220_record_label_embeddings.py`) cite it: same dict,
#: one owner, and `tests/test_13447_derived_embedding_seam.py` pins the `is`
#: identity so a second dict cannot quietly reappear.
#:
#: What came WITH the entries is `#13412`/`#13418`, and it survives the move: the
#: document text is the GRAPH's `_build_search_text(title, summary, topics)`, CALLED
#: rather than re-implemented, and a plain `:Source` derives from `_searchText` —
#: the text its record ROW holds. Those rules now live on the seam's own helpers so
#: one table serves the live write, the fold and the rebuild.
_RECORD_EMBEDDING_TEXT: dict[str, _EmbeddingTextParts] = (
    _derived_embedding.TEXT_PARTS_BY_RECORD_TYPE)


@dataclass(frozen=True)
class _DerivedEmbedding:
    """One record's derived column values, and WHY it has none when it has none.

    The second field is not decoration: a derivation that silently produced
    nothing is indistinguishable from "this label carries no vector", and the
    two have different causes (`the embedder is not installed`, `the text is
    blank`, `the graph suppresses this write`). It rides the DEBUG log —
    `RecordWriter.write` logs it per record — and nothing downstream branches on
    it. (An earlier draft of this sentence said "rides the debug log" while
    nothing logged it at all; the log call is what makes the sentence true.)
    """

    columns: Mapping[str, Any] = field(default_factory=dict)
    skipped: str = ""


def derived_embedding_columns(
    record: Mapping[str, Any],
) -> _DerivedEmbedding:
    """The embedding the RECORD computes for one journal line (#13220).

    The journal never carried a vector for `object`/`subject` (the graph fold
    derives it and writes it straight onto the node, #7845) and carries none for
    `event`/`document`/`source` unless a caller supplied one, so
    `public.event.embedding` and `public.source.embedding` sat EMPTY under a
    built HNSW index. This is the writer that fills them, and it fills them in
    the RECORD's own row — never in `public.journal_event.payload`, because that
    row is the record's copy of the journal line and a value the record invented
    would make a record-sourced rebuild and a JSONL rebuild disagree.

    ⛔ THE THREE OUTCOMES, and each is the graph fold's own choice mirrored rather
    than invented:

    * a CARRIED vector is honoured where the graph honours one — `_upsert_event`
      consumes `inner["embedding"]` when present, so an event that already has a
      vector keeps it and none is computed; a `:Source`'s `embedding` is a
      declared caller passthrough (`_SOURCE_EXTRA_PROPS`), so the same arm
      applies to `SourceCreated`;
    * a carried vector is IGNORED where the graph ignores one —
      `_upsert_object`/`_upsert_subject` call `encode_for_row(name)`
      unconditionally, so the record derives from `name`; the split REFUSES a
      carried `embedding` (`DERIVED_COLUMNS`) and `_declared_mutations` BINDS the
      derived value into its column after the split (`record_projection.py:6031-6032`:
      "Bound AFTER the split and not merged into `props`") — the derived value is
      what the row holds, and it gets there by binding, never by overriding the
      snapshot;
    * nothing is derived where the graph suppresses the write — an `EventRecorded`
      whose `eventKind` is `meeting` (cycle-16: no subject/object ⇒ a junk vector
      and an unbounded network call on every meeting write/repair/replay), a
      `DocumentCreated` carrying `suppress_embedding` (epic #900 T3), and a
      SESSION `:Source` (whose vector text is the S1 narrative in
      `public.session_narrative`, §9.4 — see `_session_source_skip`).

    ⛔ NO SECOND EMBEDDING RULE. The seam is `embeddings.encode_for_row` — the SAME
    `#5008`-gated submission step every graph write calls — and the width passed is
    `None`.

    ⛔ THE WIDTH SENTENCE IS STATED FROM THE CODE, NOT FROM INTENT, because an
    earlier draft of it was BACKWARDS. `None` does NOT avoid dropping a vector the
    graph holds: on an UNARMED store the graph passes
    `FalkorProjection.required_embedding_dim` (`projection/entities.py`'s
    `_upsert_object`/`_upsert_subject`) and `_degrade_to_width` DROPS a row of the
    wrong width there, while `_degrade_to_width(…, None)` drops nothing — so on
    such a store, with a non-384 model, the record KEEPS a vector the graph
    dropped. That divergence is real, bounded, and the right answer here:

    * on an ARMED store — the target state — the graph's own answer is ALREADY
      `None` (`required_embedding_dim` answers `None` once the in-graph index is
      retired, #11642), so `None` is the SAME answer the graph gives;
    * the record has no projection and must not invent a store width (the §12.2a
      "second rule set" defect);
    * the width's real home is the CONVERTER — `public.record_embedding_vector`
      NULLs anything that is not a 384-long all-numeric array, so a wider vector is
      STORED in `embedding` and NULLed out of the GENERATED `embedding_vec`, i.e.
      present-but-unindexed rather than silently absent.

    Byte-identical populations on an UNARMED store would need
    `required_embedding_dim` threaded through the write seam; that is a follow-up,
    not something this docstring may claim.

    ⛔ COST, DISCLOSED — and this paragraph is the POST-FIX state (#13447), because
    the pre-fix claim that stood here is now false and would send the next lane to
    re-do landed work. Before #13447 this derivation encoded the SAME text the graph
    fold then encoded again (`projection/entities.py::_upsert_object`/
    `_upsert_subject` call `encode_for_row(name)` for the same row,
    `_upsert_document`/`_upsert_event` the same for theirs) and `encode_for_row`
    memoises nothing — so on an ARMED record every live `ObjectRegistered`/
    `SubjectAdded`/`EventRecorded`/`DocumentCreated` write paid TWO model calls for
    one text.

    That sharing now exists and it is ONE seam, not a follow-up: this column encodes
    through `derived_embedding.encode` (see the call below), the folds encode through
    the same seam, and the four live doors (`EventAPI._emit`,
    `ConversationMiner._emit_event`, `TortoiseSDK._apply_and_journal`,
    `TortoiseSDK._create_entity`) hold `derived_embedding.write_scope()` across the
    record write AND its fold. The memo is keyed on the TEXT, so the fold's second
    encode of the same text is served from it at ZERO model calls — one paid encode
    per live write.

    ⛔ AND THE DOCUMENT IS NO LONGER THE EXCEPTION. `#13412` (landed after this seam
    was cut) moved the record's document text onto the GRAPH's
    `_build_search_text(title, summary, topics)`; the two legs now compose the same
    string, so the text-keyed memo dedupes `DocumentCreated` exactly like the other
    labels. A plain `:Source` pays ONE call for a different reason — the graph
    encodes nothing for it (`_upsert_source`), so there is no second half to share.

    The residual that remains is a live door that does NOT carry `write_scope`: it
    pays the pre-#13447 second call. That last one is the fail-open direction on COST
    only — never a wrong vector. No cost figure is quoted here — the record write path's own per-record cost lives in
    `docs/evidence/9558-record-write-cost/` and an encode dominates it.

    ⛔ AND WHAT THIS DOES **NOT** REACH: a `record_projection.replay()` that is
    passed no ``derived`` — `tools/record_runner.py`'s fold is the live example —
    derives nothing, so every row it CREATES lands `embedding` NULL. ⚠️ NOT
    "every row it folds", which an earlier cut said: `embedding` is absent from
    the node UPSERT's column list AND from `ON CONFLICT … DO UPDATE SET` (both
    name only the BOUND columns — the SET list is built from `col_values`,
    `record_projection.py:3446`), so a row that already HAS a vector keeps it
    through a no-derived re-fold (measured: `payload_SURVIVED=t vec_SURVIVED=t`).
    The scope matters — it is the difference between "the backfill mints blank
    rows" and "the backfill wipes the population". The long form, with the
    measurement, is in this PR's migration header; the gap is not repaired here.

    Anything that cannot be derived returns NO columns: an uninstalled embedder
    (`compute_embeddings` answers `None`, #4280), an encode that raised, blank
    text (the graph skips it for the same reason — an empty string earned a junk
    vector in the HNSW index, #331), and a non-string part in the composed text.

    ⛔ ON THAT LAST ARM, corrected because an earlier cut of this docstring
    asserted a parity that does not exist: the graph does NOT swallow a
    non-string part. Its `" ".join(...)` sits OUTSIDE the `try` that guards the
    encode (`projection/entities.py` — the document path's join, then
    `try: encode_for_row(…) except Exception: pass`; the event path is the same
    shape), so a non-string part makes the FOLD RAISE, not silently skip. The
    record's refusal therefore does not mirror the graph here — it is the
    quieter of the two, and this module does not claim otherwise.

    It is still the right arm: raising out of the record's derivation would make
    a malformed journal line poison a fold the graph's own writer already aborts,
    and "no vector" is a state the record can hold (the column is nullable by
    construction). The divergence is bounded and stated rather than papered over
    — the same treatment the width arm above gets.

    ⛔ AND THE FALSY TEST IS THE GRAPH'S OWN (`filter(None, …)`), NOT
    `is None or == ""` — a DIFFERENT question from the non-string arm above, and
    the one an earlier cut got wrong. A falsy part of ANY type — `None`, `""`,
    `0`, `False`, `[]` — never reaches the graph's join: `filter(None, …)` drops
    it and the rest of the text still composes, so the node earns a vector.
    Testing identity instead refused it and left the record's row NULL where the
    node carried one — the #5004 divergence class this table exists to prevent.
    A TRUTHY non-string is the arm above: the graph raises, the record refuses.
    """
    record_type = record.get("type")
    if (not isinstance(record_type, str)
            or record_type not in _RECORD_EMBEDDING_TEXT):
        return _DerivedEmbedding(skipped="no embedding rule for this record type")
    # A producer may NEST its node under its own envelope key: `ConversationMiner`
    # emits `{type: "EventRecorded", event: {...}}` and the fold reads
    # `event.get("event", event)`. The derivation must read the same carrier, or a
    # mined event derives from an EMPTY text while its node gets a vector.
    inner = record.get("event") if record_type == "EventRecorded" else None
    payload: Mapping[str, Any] = inner if isinstance(inner, Mapping) else record

    if record_type == "EventRecorded":
        if payload.get("embedding") is not None:
            return _DerivedEmbedding(skipped="the record carries a vector")
        if payload.get("eventKind") == "meeting":
            return _DerivedEmbedding(
                skipped="a meeting event is suppressed by the graph fold")
    elif record_type == "DocumentCreated":
        if payload.get("suppress_embedding") or record.get("suppress_embedding"):
            return _DerivedEmbedding(skipped="suppress_embedding is set")
    elif record_type in ("SourceCreated", "SourceVersioned"):
        # BOTH source writers share the arms below — `SourceVersioned` is the
        # transition that moves the row's text, and the skip reasons apply to it
        # unchanged (a version line stating no `_searchText` binds no column, so
        # its old text and old vector stay a matched pair rather than drifting).
        #
        # The three `:Source` arms that derive NOTHING, each stated on the table
        # above: a caller-carried vector (the node's own, via the `embedding`
        # passthrough), a session `:Source` (§9.4's narrative rule), and a record
        # that states no text at all (the `title`-only door — a TEXT-CARRIER gap,
        # `#13474`, not a vector rule). Checked HERE rather than inside the rule so
        # the refusal NAMES its cause: "the composed text is blank" would be true
        # and useless to whoever reads the debug line.
        if payload.get("embedding") is not None:
            return _DerivedEmbedding(skipped="the record carries a vector")
        session_skip = _session_source_skip(payload)
        if session_skip:
            return _DerivedEmbedding(skipped=session_skip)
        if not payload.get("_searchText"):
            return _DerivedEmbedding(
                skipped="the record states no _searchText, so the row holds no "
                        "text for a vector to describe (the producer must carry "
                        "the composed text — #13474)")

    # ⛔ THE TEXT RULE IS THE SEAM'S, NOT THIS FUNCTION'S (#13447). One copy of the
    # per-label composition serves the record's column, the graph fold and the
    # rebuild, so the three cannot drift — see
    # `derived_embedding.TEXT_PARTS_BY_RECORD_TYPE`. The falsy/non-string/raising
    # arms and their reasons come back on `ComposedText` and are handed to the
    # caller VERBATIM (`_DerivedEmbedding(skipped=composed.skipped)`) — there is no
    # re-tagging layer and no separate record-side vocabulary: the string the DEBUG
    # log carries is the seam's own wording, so it must be read from
    # `compose()` when it changes (`#13447` review cycle 11, P3 — this comment
    # claimed the opposite, and no such translation exists).
    composed = _derived_embedding.compose(record_type, payload)
    if not composed.text:
        return _DerivedEmbedding(skipped=composed.skipped)
    try:
        from .embeddings import BOUNDED_SCOPE_RECORD_DERIVATION
    except Exception:  # noqa: BLE001, RUF100 — an unimportable seam derives nothing
        return _DerivedEmbedding(skipped="the embedding seam is unimportable")
    try:
        # ⛔ `bounded=True` IS LOAD-BEARING HERE (`#13265` review cycle-2 P1). This
        # call runs on the CALLER's thread, inside `write_live_record`, BEFORE any
        # cache fold (`write_live_record`'s docstring says so verbatim) — so an
        # unbounded wait for a cold embedder stalls the user's request, not a
        # background fold. `get()` blocks up to `_LOAD_TIMEOUT_S` (300 s since
        # #13674; 90 s when this was written) and, in a
        # warm window, on the load another thread is running; the graph's own
        # folds already opt in (`projection/entities.py`: `_upsert_object`,
        # `_upsert_subject`, the document and Event arms) precisely so a live
        # create does not pay it (#13224/#13229, measured 504 at 18.4 s).
        #
        # The bound is SAFE for this column on exactly the reasoning that makes
        # the derived folds the only opt-in: the vector is a DERIVED copy of the
        # journal payload, so a live decline is repairable (`replay(derived=…)`
        # recomputes it) and nothing here is record-TRUTH. `_in_replay_scope()`
        # suppresses the bound inside a replay WINDOW, so `derived =
        # replay(journal)` stays timing-free on both sides — no timing state is
        # introduced here that a non-deriving caller could observe.
        #
        # `None` for the width is deliberate and separate: passing
        # `required_embedding_dim` would make the seam DROP a vector of another
        # declared width while the pair's `GENERATED` `embedding_vec` simply stays
        # NULL (see the test that pins it).
        #
        # ⛔ #13417: `bounded_scope="record_derivation"` — this is the RECORD
        # leg, not a graph fold, and the two are repaired DIFFERENTLY. A decline
        # here leaves `public.{object,subject,event}.embedding` NULL, which
        # `graph-scripts/backfill_embeddings.py` cannot repair (it reads graph
        # nodes and would report zero residual over this population); the repair
        # is a re-derivation/replay. The seam cannot infer that, so the caller
        # states it, and `monitoring.DERIVED_EMBED_DECLINE_COUNT` keeps the two
        # legs apart in one label.
        #
        # ⛔ ONE ENCODE PER WRITE (#13447): this call and the graph fold's own call
        # for the SAME text run inside one `derived_embedding.write_scope()` (the
        # live doors open it), so the second one costs no model call. That is a
        # change in HOW MANY TIMES the text is encoded, never in whether a decline
        # may drop a vector — the `bounded` flag is exactly what it was.
        vec = _derived_embedding.encode(
            composed.text, None, bounded=True,
            bounded_scope=BOUNDED_SCOPE_RECORD_DERIVATION,
        )
    except Exception:  # noqa: BLE001, RUF100 — every failure is "this row has no vector"
        return _DerivedEmbedding(skipped="the encode raised")
    if not vec:
        return _DerivedEmbedding(skipped="the embedder produced no vector")
    return _DerivedEmbedding(columns={"embedding": list(vec)})


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
        position: bool = True,
        allocate: bool = False,
        carried_positions: bool = False,
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
        :param position: write the per-record ``journal_event`` position row
            (default ``True``). The LIVE path writes it too, keyed on the
            ordinal the journal's own append reported (:data:`POSITION_ORDINAL_SOURCE`,
            #9190) — never on a number this call derives. A caller that HAS no
            ordinal passes ``False``; a guessed ``seq`` still collides with a
            later replay's numbering in silence, and the four ontology layers
            are written either way.
        :param allocate: **the record issues the positions** (#12894). The
            plan's layer mutations run first and ONE statement then increments
            `public.record_cursor` and inserts the rows carrying the numbers it
            issued, so this call's `first_seq`/`last_seq` are the ALLOCATED
            positions — not the caller's watermark — and the byte meter reads
            the rows it actually wrote. `first_seq` must be left at 1 (the plan
            refuses a caller watermark that would be silently dropped) and the
            graph's cursor must exist in mode `active`
            (:mod:`tortoise.record_allocator`); a `seeded`/`halted`/absent
            cursor raises `CursorNotActive` rather than inventing a number.
        :param carried_positions: **which shape this batch is** (#13043 review
            P1-3), passed straight through to
            :func:`record_projection.replay`. ``False`` (the default, and what
            every production caller wants) means a **journal-sourced** batch:
            `first_seq` enumerates, and a top-level ``seq`` property on a record
            is IGNORED rather than trusted — that property is residue from the
            pre-reservation SDK or a `:Session` copy, and reading it made one
            legacy line refuse its own record's live write AND the only repair
            for it. ``True`` means a **record-sourced** batch (rows read from
            `public.journal_event`, whose ``seq`` is a column): the carried
            number is READ and a disagreement with the enumeration is a refusal.
            ⛔ Do not set this to describe what the records happen to CONTAIN;
            it describes where they CAME FROM.

        :raises RecordWriteError: the record was ARMED and the write did not
            happen (malformed DSN, unreachable record, totality refusal, or a
            Postgres error). With an injected ``conn=`` the caller's transaction
            is still open, uncommitted and NOT rolled back — rolling back is the
            caller's act, because only the caller knows what else is in that
            transaction. When the seam opened the connection itself it CLOSES it
            before raising, because the caller never receives that handle: there
            is nothing it could roll back. ⚠️ ``close()`` on a pooled handle PARKS
            it rather than aborting it — a handle parked with an unfinished
            transaction is reset by the next BORROW, and the lock that leaves
            behind is bounded server-side by
            :data:`IDLE_IN_TRANSACTION_TIMEOUT_SECONDS`.

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

        if allocate and not position:
            # Refused BEFORE a connection: an allocated number exists to be
            # CARRIED by a position row, so the two flags cannot both be set.
            raise RecordWriteError(
                "allocate=True and position=False are contradictory: the "
                "allocated number is carried by the journal_event row")

        opened: Any | None = None
        target = conn
        if target is None:
            opener = self.connect or _default_connect
            try:
                target = opener(dsn)
            except RecordWriteError:
                raise
            except Exception as exc:
                # Armed-but-unreachable MUST NOT read as unarmed (decision 3),
                # and it is its OWN class so the live door can bound the cost of
                # a dead record (`LIVE_WRITE_BREAKER_SECONDS`).
                raise RecordConnectError(
                    f"the record is armed but its connection could not be "
                    f"opened ({type(exc).__name__})"
                ) from exc
            opened = target

        try:
            # #13220 — the values the RECORD computes and the JOURNAL does not
            # carry (`object`/`subject` fingerprints, an event's composed vector, a
            # document's `_build_search_text` vector, a plain `:Source`'s `_searchText`).
            # Computed HERE, at the write seam,
            # and not in the plan builder: the derivation needs the embedder, and
            # `record_projection`'s statement set is pinned DB-FREE and
            # deterministically (`tests/test_11075_record_declared_type_contract.py`
            # closes over every declared column, so a column bound only where a
            # model happened to load would turn those pins environment-dependent).
            #
            # Threaded into the plan as a per-record-index map and NOT stamped into
            # the records, because `_journal_row` writes this same record into
            # `public.journal_event.payload` — the record's copy of the JOURNAL
            # line. A derived value in it would make a record-sourced rebuild carry
            # a vector a JSONL rebuild has to derive, i.e. two rebuild routes that
            # can disagree after an embedder rotation.
            derived: dict[int, Mapping[str, Any]] = {}
            for index, record in enumerate(records):
                won = derived_embedding_columns(record)
                if won.columns:
                    derived[index] = won.columns
                elif won.skipped:
                    # The `skipped` field's ONE consumer — without this line it was
                    # dead and its own docstring was a false claim (review, P2):
                    # "no vector" and "this label carries no vector" are different
                    # facts, and only the reason tells them apart.
                    _logger.debug(
                        "record embedding not derived (%s): %s",
                        won.skipped, record.get("type"))
            report = record_projection.replay(
                records, target, org_id=org_id, graph_id=graph_id,
                start_seq=first_seq, position=position, allocate=allocate,
                carried_positions=carried_positions,
                derived=derived or None,
            )
        except Exception as exc:
            # Totality (an undeclared/refused record type raises before the
            # first statement) and execution failures share one catchable class
            # and one required caller action: roll back, do not claim durability.
            #
            # A connection the SEAM opened is disposed of here: the caller never
            # receives it, so nobody else can close it, and the traceback below
            # would otherwise keep `target` (its aborted transaction, its backend
            # slot) alive as long as the exception is held. ⛔ ``close()`` on a
            # pooled handle does NOT abort the transaction: it PARKS the handle
            # as it stands, and the reset is charged to the next BORROW (that
            # placement is deliberate — a release rollback is a round trip on
            # every read borrow; see `_park_connection`). What bounds the parked
            # transaction, and with it the AccessShareLock an INTRANS handle would
            # otherwise hold indefinitely, is
            # IDLE_IN_TRANSACTION_TIMEOUT_SECONDS on the server side.
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
                # ⛔ `str(exc)` IS PART OF THE REPORT (#9548). Logging only
                # `type(exc).__name__` discarded the one fact a failure had:
                # 302 live rows were lost and every one of them was named
                # `InvalidTextRepresentation` — no cause text, no column, no
                # statement. The cause is chained (`from exc`) AND spelled out
                # here, because the caller of this seam (`write_live_record`)
                # logs `str()` of this exception and nothing else.
                #
                # ⚠️ THE TRADE-OFF, named rather than absorbed — and stated for the
                # code that ACTUALLY runs (#10726 review P2: an earlier version of
                # this note rejected "the primary message only" as the alternative,
                # which is what `_bounded_cause` already ships). A raw psycopg2
                # message interpolates its parameters client-side, so the
                # `LINE`/`DETAIL` it carries can quote a PREFIX of a row's values
                # (the #9548 case quotes `Token "err4032" is invalid`). That half is
                # WITHHELD here, not accepted: a driver exception is recognised by
                # its shape (`diag`/`pgcode`) and only its TERSE protocol message is
                # kept (`record_projection._primary_message`, which refuses
                # `DETAIL`/`CONTEXT`/`W`). Nor is the column half lost by that
                # choice: `replay()` raises `RecordStatementError` carrying
                # `_failure_line(_statement_attribution(...), _driver_fault(exc))`,
                # so the statement, its table and its columns ride the projection's
                # OWN attribution rather than the driver's row dump. The residual
                # is narrow and named: a driver that exposes NEITHER `diag` nor
                # `pgcode` is not recognised as one, and its `str(exc)` is logged —
                # length-bounded by `_bounded_cause`, but not value-withheld. That
                # residual is accepted, because a lost row nobody can attribute is
                # the worse failure (§17's own rule — the seam must never drop a row
                # in silence) and the row is the tenant's own data going to that
                # tenant's own log.
                #
                # ⛔ BOUNDED since #10648: `_bounded_cause` keeps that cause to
                # the projection's OWN attribution budget rather than logging an
                # unbounded driver message (one number, one place — see its
                # docstring), so whatever it carries stays one bounded log line.
                f"({type(exc).__name__}: {_bounded_cause(exc)})"
            ) from exc

        # ⛔ THE ALLOCATED NUMBER IS THE WATERMARK (#12894). When the record
        # issues the positions, `first_seq`/`last_seq` MUST come from what the
        # counter handed back — deriving them from the caller's `first_seq` is
        # how an allocated number reached neither `RecordWrite.last_seq` nor the
        # byte meter: the meter then matched ZERO rows and charged 0, silently,
        # under a contract that says it never fails a write. Measured by
        # `tests/test_12894_record_cursor_live.py::
        # test_the_allocated_number_reaches_the_watermark_and_the_byte_meter`.
        watermark_first = report.first_seq if allocate else first_seq
        watermark_last = (report.last_seq if allocate
                          else first_seq + len(records) - 1)

        # #12762: MEASURE what this write actually stored, for the org that
        # wrote it. Deliberately AFTER the projection succeeded: the reading is
        # of rows that now exist. ⛔ MEASURED, NOT RECORDED: this seam does not
        # own the transaction, so it cannot know whether the write commits — the
        # ledger increment happens at the door that commits
        # (``write_live_record``) or, for an injected connection, at the caller.
        written = _measure_written_bytes(
            target,
            org_id=org_id,
            graph_id=graph_id,
            first_seq=watermark_first,
            n_records=len(records),
            position=position,
        )

        return RecordWrite(
            outcome=RecordWriteOutcome.APPLIED,
            events=report.events,
            statements=report.statements,
            first_seq=watermark_first,
            last_seq=watermark_last,
            connection=opened,
            written_bytes=written.row_bytes if written is not None else 0,
            written_rows=written.rows if written is not None else 0,
        )


def _measure_written_bytes(
    conn: Any,
    *,
    org_id: str,
    graph_id: str,
    first_seq: int,
    n_records: int,
    position: bool,
) -> postgres_bytes.WrittenRows | None:
    """Measure the STORED bytes of the rows this write just created (#12762).

    The meter is :func:`tortoise.postgres_bytes.measure_written_rows` — one
    indexed read of ``public.journal_event`` for the positions this segment
    wrote, in the caller's own transaction, summing ``pg_column_size(row)``
    (the compressed stored size where compression applies; measured to follow
    the TOAST pointer, so a moved-out payload counts at its full stored size).
    The SQL additionally restricts to rows THIS transaction inserted, so a
    suppressed ``ON CONFLICT DO NOTHING`` — a lost position collision, which
    the live path can hit between two durable journals (#9307 residual) —
    contributes **0** and the org is never charged twice for the same bytes.

    ⛔ ``position=False`` IS SKIPPED, and that is a correctness gate, not an
    optimisation: a replay re-materialises rows that were ALREADY metered when
    they first landed, so metering it again would double the org's figure on
    every rebuild.

    ⛔ TOTAL — every failure is logged at DEBUG and returns ``None``. A meter
    must never fail, or change the outcome of, a record write. ⚠️ THAT SENTENCE
    HOLDS FOR THE METER'S OWN FAILURES — AND ONLY FOR THOSE — because of the
    SAVEPOINT in :func:`tortoise.postgres_bytes.measure_written_rows`, not
    because the exception is caught: this call runs inside the caller's
    transaction, and a caught exception there still leaves it ABORTED, so the
    caller's `commit()` would roll the whole write back (#12781 — ten
    empty-record failures on `main` at `9e934efad5`).

    ⛔ AND IT DOES NOT HOLD WHEN THE SAVEPOINT BOOKKEEPING ITSELF FAILS. If
    ``SAVEPOINT``/``RELEASE`` cannot be issued (``25P02`` on an already-aborted
    transaction, ``25P01`` outside a block, a dead connection) then the
    savepoint is precisely what is missing, and a caller that commits into that
    transaction anyway still rolls its rows back. That arm is DISCLOSED at
    WARNING, not repaired — the same residual the meter's own
    ``measure_written_rows`` docstring names. Every one of those triggers means
    the write was already doomed before the meter ran, so no new loss is
    introduced.

    ⛔ IT DOES NOT WRITE THE LEDGER. The measurement is reported on
    :attr:`RecordWrite.written_bytes`; the increment happens where the COMMIT
    is known (see :func:`_record_written_bytes`), because a ledger row for a
    rolled-back write is an over-count with no repair path.
    """
    if not position or n_records <= 0:
        return None
    try:
        return postgres_bytes.measure_written_rows(
            conn,
            org_id=org_id,
            graph_id=graph_id,
            seqs=range(first_seq, first_seq + n_records),
        )
    except Exception:  # never fail a write for a meter
        _logger.debug(
            "postgres byte measurement skipped for org=%s graph=%s "
            "first_seq=%s (non-fatal)", org_id, graph_id, first_seq,
            exc_info=True)
        return None


def _record_written_bytes(org_id: str, written: RecordWrite) -> None:
    """ACCUMULATE a COMMITTED write's stored bytes on the meter's ledger (#12762).

    Call this ONLY after the write's transaction has committed. On the live door
    that is :func:`write_live_record`; a caller that injected ``conn=`` into
    :meth:`RecordWriter.write` owns the transaction and therefore owns this call
    (``RecordWrite.written_bytes`` carries the figure for exactly that).

    ⛔ TOTAL: a ledger failure is logged and dropped, never propagated — the
    caller has already persisted its record and must not be failed by a meter.
    """
    if written.written_bytes <= 0 or written.written_rows <= 0:
        # Either nothing was stored or the measurement failed (already logged).
        # Neither is a zero-byte record, so neither is recorded as one.
        return
    try:
        from . import metering  # local: keep the write path's imports thin

        metering.record_postgres_bytes_written(
            org_id, n_bytes=written.written_bytes,
            n_records=written.written_rows)
    except Exception:  # never fail a write for a meter
        _logger.debug(
            "postgres byte metering skipped for org=%s bytes=%s (non-fatal)",
            org_id, written.written_bytes, exc_info=True)


def write_records(
    records: Sequence[Mapping[str, Any]],
    *,
    org_id: str,
    graph_id: str,
    first_seq: int = 1,
    conn: Any | None = None,
    position: bool = True,
    allocate: bool = False,
    writer: RecordWriter | None = None,
    carried_positions: bool = False,
) -> RecordWrite:
    """One-call form of :meth:`RecordWriter.write` — the chokepoint's entry point.

    Same contract, same outcomes, same failures. Constructing a
    :class:`RecordWriter` once and reusing it is equivalent — the DSN is
    resolved per call either way, so a late arming is seen by both spellings —
    and this function exists so a caller with no seam object to hold does not
    have to invent one.

    ``allocate`` is passed through unchanged: #12894's allocator is a property
    of the WRITE, not of this convenience wrapper. ``carried_positions`` is
    passed through for the same reason — it describes the batch's ORIGIN
    (journal or record), not this call (#13043 review P1-3).
    """
    return (writer or RecordWriter()).write(
        records, org_id=org_id, graph_id=graph_id, first_seq=first_seq,
        conn=conn, position=position, allocate=allocate,
        carried_positions=carried_positions,
    )


def _breaker_is_open(now: float | None = None) -> bool:
    """Has a recent connect failure silenced the live door for now?"""
    return (time.monotonic() if now is None else now) < _breaker_open_until


def _trip_live_breaker(now: float | None = None) -> None:
    """A connect to the record failed: stop paying for it once per record."""
    global _breaker_open_until
    _breaker_open_until = (
        (time.monotonic() if now is None else now) + LIVE_WRITE_BREAKER_SECONDS
    )


def _close_live_breaker() -> None:
    """The record answered: re-open the door (and reset the test seam)."""
    global _breaker_open_until
    _breaker_open_until = 0.0


#: The ONE refusal token that means *the journal which took the write is a
#: scratch buffer, not a journal* — so replay cannot heal the record from it.
#: Named once (#10389 review P3) because TWO parties branch on it: the seam that
#: returns it (:func:`_live_write_verdict`) and a REPORTER deciding whether a
#: ``True`` from the emit actually means "a durable journal holds this". A
#: hardcoded copy in the reporter had to drift from this vocabulary silently —
#: and the direction it would drift is the one that matters: a renamed token
#: would route a non-durable journal into the *durable* branch, turning the
#: "stored NOWHERE" ERROR into a WARNING. The tuple below is built from this
#: name, so a rename lands on every consumer at once.
NON_DURABLE_JOURNAL_REFUSAL = "no_durable_journal"


def _bounded_cause(exc: BaseException,
                   limit: int = record_projection.ATTRIBUTION_LINE_LIMIT) -> str:
    """The CAUSE's own message, bounded — the half a class name never carried.

    `RecordWriter.write` used to name only ``type(exc).__name__``, so every
    wrap of a driver error read ``(InvalidTextRepresentation)`` and nothing
    else. `record_projection.replay` now raises a
    :class:`record_projection.RecordStatementError` whose message already names
    the fault, the statement, its table and columns; this keeps that whole
    message attached to the failure a caller logs, while staying short enough for
    one log line.

    ⛔ THE LIMIT IS THE PROJECTION'S OWN BUDGET, not an independent number
    (#10648 review P2). This function keeps the HEAD of a message, so a limit
    below what `_failure_line` composed silently amputates the TAIL — which is
    how a wide statement's failure line lost the driver's fault clause entirely
    (measured: a 488-char attribution plus its driver, wrapped to 400 chars in
    which `invalid input syntax for type json` was absent). One number, in one
    place: `_failure_line` composes to it, so a line the projection composed
    reaches a log whole.

    ⛔ AND A DRIVER'S TEXT IS NARROWED, NOT CARRIED (#10648 review P2). A raw
    driver exception reaches this function by a second route — `replay()` composes
    its own line for a STATEMENT failure, but `scope_connection()` runs before the
    first statement, so its driver error propagates here untouched. On a driver
    error ``str(exc)`` is the protocol's fields CONCATENATED, and the optional
    `DETAIL`/`CONTEXT` fields quote the refused row (`Failing row contains (…)`)
    or the fragment (`JSON data, line 1: …`). The same withhold therefore has to
    hold at this seam, and it is applied by the SHAPE of the exception rather than
    by its type: an exception carrying `diag`/`pgcode` is one of the driver's, and
    only its primary message is kept (`record_projection._primary_message`). This
    module's own errors are untouched — they are its own text, not a driver's.
    """
    if getattr(exc, "diag", None) is not None or getattr(exc, "pgcode", None):
        return _bounded_text(record_projection._primary_message(exc), limit)
    return _bounded_text(str(exc), limit)


#: The payload fields a record's identity can live in, most specific first.
#: ``eventId`` is the fold's MERGE key for a Point/Event row, so it wins over a
#: generic ``id``; a Session keys on ``session_id``.
_RECORD_IDENTITY_KEYS: tuple[str, ...] = (
    "eventId", "node_key", "session_id", "event_id", "id", "name",
)

#: Identity fields whose VALUE is the user's own text rather than an id.
#:
#: ``name`` is last in :data:`_RECORD_IDENTITY_KEYS` on purpose: it is the only
#: identifying field a ``source``/``object`` snapshot may hold when it has no id,
#: so it still IDENTIFIES the row for an operator. But a source's ``name`` is its
#: document title — the same datum `record_projection._CONTENT_COLUMNS` withholds
#: as ``title`` — so its value is withheld exactly as that attribution withholds
#: it, and only its presence and length are logged (#10648 review P3).
#:
#: ⛔ THE TWO HALVES OF ONE LOG LINE MUST AGREE (#10648 review P2). Withholding it
#: HERE while `record_projection._CONTENT_COLUMNS` printed the same string as
#: ``name=Q3 pricing memo`` two clauses later was a withhold true of each half and
#: false of the line. `name` (with ``canonical_name``/``subject``/``object``) is
#: therefore in that list too, and
#: `tests/test_record_searchkeys_10620.py::test_the_identity_withhold_list_is_
#: a_subset_of_the_attribution_list` pins :data:`_IDENTITY_CONTENT_KEYS` as a
#: SUBSET of it — a new content-bearing identity key here goes red there until the
#: attribution half withholds it too.
_IDENTITY_CONTENT_KEYS: frozenset[str] = frozenset({"name"})

#: How much of ONE identity value the log line carries. An ``eventId`` is bounded
#: for the same reason every other logged value is: nothing that reaches this log
#: may be sized by the caller.
_IDENTITY_VALUE_LIMIT = 80


def _record_identity(record: Mapping[str, Any], limit: int = 3) -> str:
    """Name the entity/point id a failed record carried, for the log line.

    Never for a row, and never inventing a value: it reports the FIRST identity
    field the record actually holds, in each of its snapshot sub-dicts (a
    ``PointAdded`` keeps its payload under ``point``). Absence is stated as
    absence rather than left blank, because "the record had no id field" and
    "this code could not find it" are different facts and only one of them is
    worth chasing.

    ⛔ A CONTENT-BEARING identity field is NAMED and WITHHELD
    (:data:`_IDENTITY_CONTENT_KEYS`), and every value is bounded — this line is
    the same log line whose attribution withholds
    ``title``/``topics``/``summary``, so printing a source's ``name`` verbatim
    here would put a document title in the operator's log through the column
    next door (#10648 review P3).
    """
    found: list[str] = []
    for field_name, value in record.items():
        if not isinstance(value, Mapping):
            continue
        for key in _RECORD_IDENTITY_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                found.append(f"{field_name}.{key}="
                             f"{_identity_value(key, candidate)}")
                break
    for key in _RECORD_IDENTITY_KEYS:
        candidate = record.get(key)
        if isinstance(candidate, str) and candidate:
            found.append(f"{key}={_identity_value(key, candidate)}")
            break
    return " ".join(found[:limit]) if found else "(record carries no identity field)"


def _identity_value(key: str, value: str) -> str:
    """One identity field's LOGGED form: withheld if content, bounded either way."""
    if key in _IDENTITY_CONTENT_KEYS:
        return f"<{len(value)} chars withheld>"
    return _bounded_text(value, _IDENTITY_VALUE_LIMIT)


def _bounded_text(text: str, limit: int) -> str:
    """`text`, newline-escaped and truncated — the string form of the bound."""
    flat = text.replace("\n", "\\n")
    return flat if len(flat) <= limit else f"{flat[:limit]}…({len(text)} chars)"


def _count_write_failure(reason: str) -> None:
    """Count one lost record write. **Never raises** (#5148's rule, one frame on).

    The caller's journal append is already committed, so nothing here may hand
    the caller an exception for a write that persisted — not the import of
    ``monitoring`` (a failing import hook) and not the metrics call itself. The
    bare-``except`` swallow is deliberate and narrow: the RETURN VALUE of a
    counter is nothing a caller can use.
    """
    with contextlib.suppress(Exception):
        from . import monitoring
        monitoring.record_record_write_failure(reason)


def _count_non_journal_skip(reason: str = NON_DURABLE_JOURNAL_REFUSAL) -> None:
    """Count one DECLINED non-journal write. **Never raises** (#5148's rule).

    A sibling of :func:`_count_write_failure` with the same swallow and a
    different channel: ``monitoring.record_non_journal_skips``, not
    ``record_write_failures`` (#9231 review P2). Same rule, because the caller's
    journal-less append has committed just the same.
    """
    with contextlib.suppress(Exception):
        from . import monitoring
        monitoring.record_non_journal_skip(reason)


def _count_position_unproven() -> None:
    """Count one armed write whose journal append proved no position (#9307 F1).

    Same swallow and same reason as its siblings: the caller's append is already
    committed, so a metrics failure may never escape this door.
    """
    with contextlib.suppress(Exception):
        from . import monitoring
        monitoring.record_position_unproven()


def _count_write_applied() -> None:
    """Count one record write this process COMMITTED. **Never raises.**

    The POSITIVE channel beside the three failure/decline counters siblings
    above (#9548): without it an armed-and-writing deployment and one that has
    never been reached both read ``record_write_failures: {}`` on ``/health``,
    which is exactly how this P0 was closed on an empty object.

    Called AFTER ``connection.commit()`` returns, never at
    ``RecordWriteOutcome.APPLIED`` — APPLIED means the statements ran, so it
    would report a write that a later rollback discards. Same swallow as its
    siblings, for the same reason: the caller's write already persisted.
    """
    with contextlib.suppress(Exception):
        from . import monitoring
        monitoring.record_write_applied()


def _note_unproven_position(record: Mapping[str, Any]) -> None:
    """Count AND (at most once per window) WARN that layer 1 skipped a record.

    The skipped record is not lost — it is in the journal, and `replay` writes
    its `journal_event` row — but a silent skip is exactly the gap #9078 exists
    to close, so it is named on the metrics surface and in the log rather than
    left to be noticed by its absence.
    """
    global _unproven_position_warned_at
    _count_position_unproven()
    now = time.monotonic()
    if now - _unproven_position_warned_at < POSITION_UNPROVEN_WARN_SECONDS:
        return
    _unproven_position_warned_at = now
    _logger.warning(
        "no layer-1 position row for this record (position_unproven) — the "
        "journal append SUCCEEDED but could not prove the record's position "
        "(see EventLog.append's None states), so layer 1 is one record behind "
        "the journal until a replay heals it. Counted on "
        "monitoring.metrics()[\"record_position_unproven\"]; this line repeats "
        "at most once per %.0fs: %s", POSITION_UNPROVEN_WARN_SECONDS,
        record.get("type"),
    )


#: The reasons :func:`live_write_refusal` / :func:`_live_write_verdict` can
#: return. Named in ONE place because a caller branches on them: "unarmed" is
#: not a failure (nothing was attempted, §2.0 byte-identical), every other
#: token is a DECLINE this write cannot survive. "no_tenant_carried" is
#: separate from "no_tenant" only so the WARNING can tell an operator whose
#: carried pair was malformed from one who carried none — both are counted on
#: the same `no_tenant` counter, so the metric did not gain a term.
LIVE_WRITE_REFUSALS: tuple[str, ...] = (
    "unarmed", "malformed_dsn", NON_DURABLE_JOURNAL_REFUSAL, "no_tenant",
    "no_tenant_carried", "circuit_open",
)


def record_live_allocate() -> bool:
    """Does this deployment's live door allocate the ordinal (#12894)?

    Read at CALL TIME from :data:`RECORD_ALLOCATE_ENV`, never cached at import: a
    test (and a lane) flips it per write, and a cached read would make the switch
    depend on which process imported this module first.

    Default ``False``, including for a value :func:`env_flag` cannot parse — the
    fail-safe direction for a switch whose ON arm refuses writes on a graph that
    has not been seeded.
    """
    return env_flag(RECORD_ALLOCATE_ENV, False)


#: How often the "the cutover could not be MEASURED, so the append stays"
#: WARNING may repeat, in seconds. Same posture and same reason as
#: :data:`POSITION_UNPROVEN_WARN_SECONDS`: the condition repeats on every write
#: while it lasts, so an unthrottled line would be the whole log.
OWNS_POSITION_WARN_SECONDS = 60.0
_owns_position_warned_at = 0.0


def _warn_owns_position_unmeasured(exc: BaseException) -> None:
    """Say once per window why the cutover read fell back to the APPEND (#14002).

    The fallback is the SAFE direction (see :func:`record_owns_position`), so it
    is not a failure — but it is not silent either: an operator reading
    ``position_divergence`` refusals back after a cutover needs to know whether
    the door refused to skip the append or failed to ask, and those have
    different remedies (a flag/cursor problem vs a database that cannot be
    reached).
    """
    global _owns_position_warned_at
    now = time.monotonic()
    if now - _owns_position_warned_at < OWNS_POSITION_WARN_SECONDS:
        return
    _owns_position_warned_at = now
    _logger.warning(
        "the record's cutover could not be MEASURED (%s: %s), so this write "
        "appends to the JSONL and the door allocates against the journal's "
        "position — the two-allocator state that produces "
        "`position_divergence` under concurrent writers (#14002). Nothing is "
        "lost: the line is the durable copy and the guard still refuses a "
        "disagreement rather than renumbering. Check `%s` on THIS process and "
        "the graph's cursor with `tools/record_allocator_cli.py status`. This "
        "line repeats at most once per %.0fs",
        type(exc).__name__, _bounded_cause(exc), RECORD_ALLOCATE_ENV,
        OWNS_POSITION_WARN_SECONDS,
    )


def record_owns_position(*, tenant: tuple[str, str] | None = None,
                         durable: bool = True) -> bool:
    """Does the RECORD own this write's ordinal — i.e. must the caller NOT
    append to the journal? (#14002, `#12894`'s target architecture)

    THE DEFECT THIS CLOSES. While BOTH allocators exist they must agree, and
    they CANNOT under concurrency: the journal is ordered by **append** (the SDK
    site appends, then calls the door, holding no lock across the two) while the
    counter is ordered by **allocation**, so two in-flight writes can be numbered
    in the opposite order to their lines. The one that allocated first refuses,
    the other applies, and the counter is left exactly ONE BEHIND the journal —
    and on a cut-over graph that gap never closes, because an enumerated replay is
    itself refused there (`_refuse_an_enumerated_replay`), so EVERY later armed
    write refuses until an operator re-seeds. With 20+ agents writing
    continuously that is not an edge case; it is steady state, and the record
    reads `ok: false` while some writes land and some are refused.

    THE FIX IS NOT IN THE GUARD — the guard is right, and it is the only thing
    that kept the record honest while both allocators existed. The fix is to
    remove the SECOND allocator: while the record owns the ordinal, the write path
    must not append to the JSONL at all. Then there is one number space, writing
    the file becomes OPTIONAL (nothing on the write path depends on it) and — on a
    cut-over graph — the file is a FROZEN PREFIX rather than an export: the append
    stops when the BOX's `TORTOISE_RECORD_ALLOCATE` is rolled with the row
    `active`, NOT at the per-graph `arm` (the arm→roll window still appends), and
    no exporter exists (`event_id`/`initiated_by`/
    `projection_version` have no column, so a line rendered from the record would
    be a different journal; §A7.8 item 6 and §A7.5.6's drainer are unbuilt).
    ⛔ Do not describe it as "an export" until they are. Concurrent writers then
    serialise on a real database transaction, and the truncation class of failure
    disappears because nothing numbers off the file.

    THE CONDITION IS TWO FACTS, BOTH REQUIRED:

    * :func:`record_live_allocate` — the deployment is armed. OFF is today's
      behaviour byte for byte, checked FIRST so an unarmed deployment pays no
      socket and no read (this is the switch's whole cost story: one path, two
      arms).
    * the graph's `public.record_cursor` row is in mode ``active``. The ROW says
      the graph was cut over and the MODE says positions may be issued from it —
      and both are needed, in the same direction `ALLOCATION_SQL` needs them: a
      `seeded`/`halted`/absent row means the door's write will be REFUSED
      (`cursor_not_active`), and on THAT arm the journal line is the only durable
      copy of the write. So the append stays exactly where the allocation would
      refuse, and the line is not lost.

    ⛔ IT FAILS OPEN TO THE APPEND, and the direction is deliberate. Every way this
    question can go unmeasured — no DSN, a read error, the connect breaker open —
    answers ``False``, which leaves the pre-#14002 path in place: append, then let
    the divergence guard refuse a disagreement. The alternative (assume the record
    owns it) would skip the append for a graph that may not be cut over, and a
    record write that then fails has NO line behind it — the write is gone from
    both stores. A refusal is recoverable; a skipped append behind a refused write
    is not. So the unmeasured arm is named on a rate-limited WARNING
    (:func:`_warn_owns_position_unmeasured`) and not absorbed.

    ⚠️ WHICH ARMS OF THE FALLBACK ARE LOUD, STATED SO THE SENTENCE ABOVE IS NOT
    READ WIDER THAN IT IS. `_warn_owns_position_unmeasured` is reached from the
    `except` around the connect/read — the arms where the question was ASKED and
    failed. EVERY arm :func:`_live_write_verdict` can refuse BEFORE a socket is
    SILENT here (``unarmed``, ``malformed_dsn``, ``no_durable_journal``,
    ``no_tenant``, ``no_tenant_carried``, ``circuit_open``), and they are not one
    fact: on ``unarmed``/``malformed_dsn`` nothing on the write path can allocate
    either, so no two-allocator state exists and there is nothing to warn about;
    the tenant and durability tokens are refused by the door for the same reason;
    ``circuit_open`` DOES fall back to two allocators, and it is already loud on
    its own channel — the door's ``circuit_open`` refusal WARNING, which says a
    write was declined.

    ⛔ AND IT IS NOT CACHED, on purpose. A cached POSITIVE lets a graph that has
    since been halted (or whose counter row was deleted) keep skipping the append
    for the length of the TTL — during which every write is refused AND unline'd.
    A cached NEGATIVE delays the cutover by the same window. The read is one
    scoped ``SELECT`` on the pooled handle the record write is about to use
    anyway, and it replaces the file append this call exists to remove.
    ⚠️ NO LATENCY MEASUREMENT IS CLAIMED FOR THAT TRADE: what was measured is the
    correctness property (120 concurrent writes on 12 threads, positions 1..120,
    zero refusals, no file growth) — the acceptance window in
    `tests/test_14002_record_only_journal.py` — and NOT a p50/p99 against the
    append it replaces. Both designs concentrate a tenant-graph's writes on one
    lock (a file lock, or the counter's row lock), so "it serialises better" is
    not an argument this change makes.

    Callers: `tortoise.live_write.write` asks this BEFORE it appends, and forwards
    the answer to :func:`write_live_record` as ``journal_skipped=`` so the door can
    tell "no line, the counter is the only allocator" from the #9307 "the append
    could not prove a position" state. The two must never be one value: the first
    is healthy on a cut-over graph, the second is a refusal.
    """
    if not record_live_allocate():
        return False
    refusal, resolved, _detail = _live_write_verdict(
        durable=durable, tenant=tenant)
    if refusal is not None or resolved is None:
        # Nothing to measure: the write is refused before a socket anyway (or the
        # breaker is open), so the append must stay — the line is the durable copy
        # of a write the record is about to decline.
        return False
    connection: Any | None = None
    active = record_projection.MODE_ACTIVE
    try:
        from . import record_allocator as ra

        dsn = record_dsn()
        if dsn is None:
            return False
        connection = _default_connect(dsn)
        row = ra.read_cursor(connection, org_id=resolved[0],
                             graph_id=resolved[1])
    except Exception as exc:  # noqa: BLE001, RUF100
        _warn_owns_position_unmeasured(exc)
        return False
    finally:
        if connection is not None:
            # END THE READ'S TRANSACTION, then release the handle. `read_cursor`
            # scopes the connection, so the SELECT opened one; committing here is
            # what keeps a parked handle from sitting INTRANS with an
            # AccessShareLock between writes (#9558's lock-leak rule), and the
            # close is a RELEASE to the pool (`_PooledConnection`), not a kernel
            # goodbye.
            with contextlib.suppress(Exception):
                connection.commit()
            with contextlib.suppress(Exception):
                connection.close()
    # ⛔ #14582: THIS READ IS ALSO THE OBSERVATION. It is the only per-write read
    # of the graph's cursor row on an armed deployment, so a graph with no usable
    # row is VISIBLE here without one extra statement — and it is exactly the
    # population `/health` could not see (`record.ok: true` over a graph whose
    # record leg was off). No tenant identifiers are published; see
    # :func:`armed_but_uncut`.
    usable = row is not None and row[1] == active
    if usable:
        # ⛔ THE SAME READ THAT OBSERVES THE GAP OBSERVES ITS REPAIR. Without
        # this the registry is a LATCH: a graph an operator healed would keep
        # `/health` non-`ok` for the life of the process (see
        # :func:`clear_uncut_graph`).
        clear_uncut_graph(resolved[0], resolved[1])
    else:
        reason = "no_cursor_row" if row is None else f"mode_{row[1]}"
        note_uncut_graph(resolved[0], resolved[1], reason=reason)
        _warn_uncut_graph(resolved[0], resolved[1], reason=reason)
    return usable


# ── #14582: the creation hook, and the gap it can leave ────────────────────
#
# TWO FACTS, ONE PLACE. A graph created on an armed deployment must come out of
# creation with a `record_cursor` row in mode `active`, or its record leg
# accumulates nothing while the fleet reads healthy (`write_live_record` is
# fail-soft by contract, so the refusal is a counter and a WARNING, never an
# exception a user sees). The hook closes the first fact; this registry makes the
# SECOND fact — a graph the hook did not reach — impossible to hide.

#: How many DISTINCT tenants the registry remembers by name. The set is PROCESS
#: state fed from the write path, so it must be bounded: a per-graph label is
#: unbounded cardinality (the same rule `cache_leg.cache_write_targets` states for
#: the same reason). Past the cap the observation is still COUNTED
#: (`armed_but_uncut_untracked_observations`, and `armed_but_halted_untracked_observations`
#: for the halted population), so nothing is lost — only the distinctness is.
UNCUT_GRAPH_CAP = 32

_UNCUT_GRAPHS: dict[str, str] = {}
#: Observations that could NOT be tracked because the map was full. ⛔ IT IS NOT A
#: LIFETIME COUNTER: it is retracted to 0 as soon as a :func:`clear_uncut_graph`
#: makes room, because from that moment any graph still uncut is tracked again on
#: its next observation. Without the retraction this counter is the same LATCH
#: :func:`clear_uncut_graph` exists to prevent — a deployment the operator FULLY
#: healed still sums to > 0 and reads `ok: false` for the life of the process
#: (#14587 review P1).
_UNCUT_UNTRACKED = 0
#: The same count for the halted population, which has its own bounded map. Past
#: the cap a halted graph would otherwise stop being PUBLISHED with nothing
#: saying how many were dropped (#14587 review P2), contradicting
#: `armed_but_halted`'s own "still published, on its own key" contract.
_UNCUT_HALTED_UNTRACKED = 0
#: LIFETIME observation count — a DIAGNOSTIC, never folded into `ok` (the same
#: division `record_write_failures` (lifetime) vs `record_refusing` (window) uses).
_UNCUT_OBSERVATIONS = 0
#: Graphs whose row exists but is `halted` — SEEN, reported, and deliberately NOT
#: folded into `armed_but_uncut`. ⛔ The two are different populations and the
#: difference decides whether a graph is losing anything: `halted` is the state an
#: OPERATOR chose (`record_allocator.raise_named`'s remedy for a duplicate
#: position), and in it `record_owns_position` returns False, so
#: `live_write.write` KEEPS the JSONL append — the graph loses nothing. A graph
#: with NO row is one the hook never reached, and that is the gap this registry
#: exists for. Folding them together would report an operator's sanctioned action
#: as a deployment-wide defect that never clears (the halt is deliberate and
#: persists), i.e. a false red on `/health` for the life of the process.
_UNCUT_HALTED: dict[str, str] = {}
_UNCUT_GUARD = threading.Lock()


def note_uncut_graph(org_id: str, graph_id: str, *, reason: str) -> None:
    """Record that `(org_id, graph_id)` was OBSERVED with no usable cursor row.

    ⛔ NEVER RAISES, and that is a contract rather than politeness: this is called
    from the live write path (`record_owns_position`) and from the creation hook,
    and a monitoring counter that can fail a write or a graph creation is worse
    than no counter. The whole body is guarded for that reason.

    `reason` is one of `no_cursor_row` / `mode_<mode>` (observed on the write
    path) or a provisioning outcome (`provision_failed`,
    `provision_refused:<reason>`, `malformed_dsn`). A tenant already tracked has
    its reason REPLACED — the newest observation is the current one.

    ⛔ `mode_halted` DOES NOT LAND HERE. See :data:`_UNCUT_HALTED`: a halted graph
    keeps its JSONL append and is an operator's decision, so it is published on
    its own key instead of being reported as an uncut graph.
    """
    global _UNCUT_OBSERVATIONS, _UNCUT_UNTRACKED, _UNCUT_HALTED_UNTRACKED
    try:
        key = f"{org_id}\u0000{graph_id}"
        if reason == f"mode_{record_projection.MODE_HALTED}":
            with _UNCUT_GUARD:
                _UNCUT_HALTED[key] = reason
                if len(_UNCUT_HALTED) > UNCUT_GRAPH_CAP:
                    _UNCUT_HALTED.pop(next(iter(_UNCUT_HALTED)))
                    # ⛔ COUNT THE DROP. The uncut arm below has always done this;
                    # a bound that evicts silently is a bound that hides its own
                    # overflow, and the contract on `armed_but_halted` is that the
                    # population is still published on its own key.
                    _UNCUT_HALTED_UNTRACKED += 1
            return
        with _UNCUT_GUARD:
            _UNCUT_OBSERVATIONS += 1
            if key in _UNCUT_GRAPHS or len(_UNCUT_GRAPHS) < UNCUT_GRAPH_CAP:
                _UNCUT_GRAPHS[key] = reason
            else:
                _UNCUT_UNTRACKED += 1
    except Exception:  # noqa: BLE001, RUF100 - observability must not fail a write
        pass


def clear_uncut_graph(org_id: str, graph_id: str) -> None:
    """Forget `(org_id, graph_id)` — it was OBSERVED cut over (or provisioned).

    ⛔ WITHOUT THIS THE REGISTRY IS A LATCH, AND A LATCH IS NOT A HEALTH SIGNAL.
    Every entry is fed from an observation, so the SAME place that observes the
    gap observes its repair: `record_owns_position` reads the row on every armed
    write, so a graph an operator healed stops appearing uncut on its next write,
    and :func:`provision_graph_record` clears it on success. A field that could
    only ever be added to would pin `/health` non-`ok` for the life of the
    process over a graph that was fixed an hour ago — the failure mode this
    module's health contract names for cumulative counters, and it would turn the
    compensation for best-effort provisioning into a permanent false red.

    ⛔ IT RETRACTS THE OVERFLOW COUNTS TOO, and that is not housekeeping — it is
    the other half of the same rule (#14587 review P1). `_UNCUT_UNTRACKED` counts
    observations the FULL map could not track; once a heal makes room, any graph
    that is still uncut is tracked again on its next observation, so the backlog
    no longer describes a distinctness loss and keeping it would pin `ok: false`
    over a deployment where every graph was healed. `_UNCUT_OBSERVATIONS` is NOT
    retracted: it is published as a LIFETIME diagnostic and is never folded into
    `ok` (the `record_write_failures` / `record_refusing` division).
    """
    global _UNCUT_UNTRACKED, _UNCUT_HALTED_UNTRACKED
    try:
        key = f"{org_id}\u0000{graph_id}"
        with _UNCUT_GUARD:
            if _UNCUT_GRAPHS.pop(key, None) is not None \
                    and len(_UNCUT_GRAPHS) < UNCUT_GRAPH_CAP:
                _UNCUT_UNTRACKED = 0
            if _UNCUT_HALTED.pop(key, None) is not None \
                    and len(_UNCUT_HALTED) < UNCUT_GRAPH_CAP:
                _UNCUT_HALTED_UNTRACKED = 0
    except Exception:  # noqa: BLE001, RUF100 - observability must not fail a write
        pass


def armed_but_uncut() -> dict[str, int]:
    """The uncut-graph observation, for `/health` (#14582).

    ⛔ IT CARRIES NO TENANT IDENTIFIERS. `/health` is unauthenticated and a graph
    id is a customer's own name for a tenant (`_change_feed_health` states the
    same rule for the same reason: counts per stage, never names).

    ⛔ IT IS NOT THE DEPLOYMENT'S POPULATION — it is what THIS PROCESS has
    observed, on the write path or at creation. The org-wide population is a
    QUERY and `/health` collects no I/O (#2850: an unreachable record must never
    delay or fail a liveness probe), so the authoritative list is
    `tools/record_allocator_cli.py sweep|backfill`.

    `armed_but_uncut` is exact up to :data:`UNCUT_GRAPH_CAP` distinct graphs;
    `armed_but_uncut_untracked_observations` counts the OBSERVATIONS past that cap
    — ⛔ observations, not graphs, which is what its name says: it increments once
    per write to an untracked graph, so reading it as a graph count would size an
    incident wrongly. The two together can never read as "none" while there are
    some, and the overflow half is RETRACTED when a heal restores room, so the
    pair is state a repair can clear rather than a latch (#14587 review P1).

    `armed_but_uncut_observations` is the LIFETIME total — a diagnostic on its own
    key, ⛔ NEVER folded into `ok`: a cumulative counter must not pin a deployment
    degraded for the life of the process over a graph that was fixed an hour ago.

    `armed_but_halted` is a SEPARATE population and is NOT part of
    `armed_but_uncut`: a `halted` row is an operator's decision and that graph
    keeps its JSONL append (`record_owns_position` → False), so it loses nothing.
    `armed_but_halted_untracked_observations` is its overflow count, so a
    population past the cap is still COUNTED even where it cannot be named.
    """
    with _UNCUT_GUARD:
        return {
            "armed_but_uncut": len(_UNCUT_GRAPHS),
            "armed_but_uncut_untracked_observations": _UNCUT_UNTRACKED,
            "armed_but_uncut_observations": _UNCUT_OBSERVATIONS,
            "armed_but_halted": len(_UNCUT_HALTED),
            "armed_but_halted_untracked_observations": _UNCUT_HALTED_UNTRACKED,
        }


def armed_but_uncut_reasons() -> dict[str, int]:
    """`{reason: count}` BEHIND :func:`armed_but_uncut`, for `/health`.

    ⛔ DERIVED FROM THE TRACKED MAP, NOT ACCUMULATED (#14587 review P2). A dict
    incremented per observation can never be retracted: after every graph was
    healed it still published `{"no_cursor_row": 33}` beside a count of 0, so the
    two keys of the same payload contradicted each other with no repair path for
    this one. Deriving it makes the retraction automatic and gives the invariant
    an operator can check: `sum(reasons.values()) == armed_but_uncut`.

    The reason breakdown is therefore DISTINCT GRAPHS per reason; the lifetime
    observation total is `armed_but_uncut_observations`.
    """
    with _UNCUT_GUARD:
        reasons: dict[str, int] = {}
        for reason in _UNCUT_GRAPHS.values():
            reasons[reason] = reasons.get(reason, 0) + 1
        return reasons


def reset_uncut_graphs() -> None:
    """Zero the registry. For tests, and for a process that re-arms."""
    global _UNCUT_OBSERVATIONS, _UNCUT_UNTRACKED, _UNCUT_HALTED_UNTRACKED
    with _UNCUT_GUARD:
        _UNCUT_GRAPHS.clear()
        _UNCUT_HALTED.clear()
        _UNCUT_OBSERVATIONS = 0
        _UNCUT_UNTRACKED = 0
        _UNCUT_HALTED_UNTRACKED = 0


#: How often the "this graph has no usable cursor" WARNING may repeat, in
#: seconds. Same posture and same reason as the two constants above it: the
#: condition repeats on EVERY write to that graph while it lasts, so an
#: unthrottled line would be the whole log.
UNCUT_GRAPH_WARN_SECONDS = 60.0
_uncut_warned_at: dict[str, float] = {}


def _warn_uncut_graph(org_id: str, graph_id: str, *, reason: str) -> None:
    """Say, rate-limited per TENANT, that this graph's record leg is off.

    The remedy is named, because the two populations have different ones: a graph
    created before the hook landed (or whose hook failed) needs
    `record_allocator_cli.py` `seed`/`backfill`; a graph an operator halted needs
    its halt respected. Saying "check the cursor" without the verb is how an
    operator re-runs the wrong thing.
    """
    key = f"{org_id}\u0000{graph_id}"
    now = time.monotonic()
    with _UNCUT_GUARD:
        last = _uncut_warned_at.get(key)
        if last is not None and (now - last) < UNCUT_GRAPH_WARN_SECONDS:
            return
        # Bounded like the registry above: a per-tenant timestamp map is process
        # state fed from the write path.
        if len(_uncut_warned_at) >= UNCUT_GRAPH_CAP:
            _uncut_warned_at.clear()
        _uncut_warned_at[key] = now
    _logger.warning(
        "the record's cursor for %s/%s is not usable (%s), so every record this "
        "graph writes is REFUSED and its journal lines are the only durable "
        "copy — `/health` reports this under `record.armed_but_uncut`. Remedy: "
        "`tools/record_allocator_cli.py status --org-id %s --graph-id %s`, then "
        "`backfill` (or `seed` + `arm` for this one graph) if it should be cut "
        "over; a `halted` graph stays halted BY DESIGN until an operator "
        "reopens it. This line repeats at most once per %.0fs per graph",
        org_id, graph_id, reason, org_id, graph_id, UNCUT_GRAPH_WARN_SECONDS,
    )


def provision_graph_record(*, org_id: str, graph_id: str,
                           enumerated_floor: int | None = None
                           ) -> dict[str, Any]:
    """The CREATION HOOK the doors call (#14582): give a new graph its counter row.

    Called from every graph-creation door the census found
    (`hosted_api._provision_graph`, `supabase_control.ensure_default_graph_row`,
    `sdk._graph_create`'s registry arm).

    THREE PROPERTIES, each of which a tidy-looking variant would lose:

    1. ⛔ **UNARMED IS A NO-OP, NOT A FAILURE.** Without `TORTOISE_RECORD_DSN`
       there is no record:
       nothing is uncut, nothing is counted, and nothing is logged. This is the
       embedded/selfhost lane and most of CI.
    2. ⛔ **IT NEVER RAISES.** A graph creation must not fail because the record
       is briefly unreachable. `write_live_record` is fail-soft by contract and
       the DSN may be absent entirely, so making an OPTIONAL store a hard
       dependency of provisioning would trade a silent record gap for a 500 on
       the user's create — and the plan's `§A7.8` item 8, which says "fail-hard",
       presupposed the ONE-TRANSACTION RPC that made a hard failure atomic; this
       carrier cannot be atomic (the record app role has no grant on
       `public.graphs`, so the cursor row is written by a different role on a
       different connection), so a hard failure here would leave an orphan row
       behind every refusal AND 500 the create. The failure is therefore made
       LOUD and VISIBLE instead: counted and named on `/health` as
       `armed_but_uncut`, and healed by `record_allocator_cli.py backfill`.
    3. ⛔ **IT COMMITS, ONCE, AND ONLY ON SUCCESS.** The seed and the arm are one
       transaction (`record_allocator.provision_graph_record` does not commit),
       so a graph is never left in the state that allocates nothing.

    The floor is DERIVED unless the caller passes one: `record_allocator`
    reads `JOURNAL_COUNT_SQL` for this graph in the same tenant-scoped
    transaction, which is the seed's own quantity ("how many positions already
    exist"). ⛔ A door must NOT have to know it, and a hard-coded `0` would be
    WRONG for a graph that is older than the call site assuming it is new — the
    default row's credential self-heal reaches a graph that may predate this hook,
    and its journal holds exactly the positions a floor of `0` claims cannot
    exist. Either way the number is not trusted: `SEED_SQL` refuses
    (`SeedRefused(reason="divergence")`) when the journal's `MAX(seq)` exceeds the
    enumeration, so a real disagreement is REFUSED rather than silently seeded.
    """
    from . import record_allocator as ra

    try:
        dsn = record_dsn()
    except RecordWriteError as exc:
        # SET and REFUSED by the seam — a typo'd DSN. Nothing can be provisioned
        # and every record write on this deployment is refused the same way.
        note_uncut_graph(org_id, graph_id, reason="malformed_dsn")
        _logger.warning(
            "the graph's record cursor could NOT be provisioned for %s/%s: "
            "%s is set but the seam refused it (%s). `/health` reports this "
            "under `record.error` and `record.armed_but_uncut`",
            org_id, graph_id, RECORD_DSN_ENV, exc)
        return {"provisioned": False, "reason": "malformed_dsn"}
    if dsn is None:
        return {"provisioned": False, "reason": "unarmed"}

    connection: Any | None = None
    try:
        connection = _default_connect(
            dsn, connect_timeout=PROVISION_CONNECT_TIMEOUT_SECONDS)
        outcome = ra.provision_graph_record(
            connection, org_id=org_id, graph_id=graph_id,
            enumerated_floor=enumerated_floor)
    except ra.SeedRefused as exc:
        # REFUSED, NEVER SEEDED (#12894's divergence rule). The graph keeps
        # working on whatever allocator it had; what it loses is the record leg,
        # which is exactly why it is published.
        note_uncut_graph(org_id, graph_id,
                         reason=f"provision_refused:{exc.reason}")
        _warn_uncut_graph(org_id, graph_id,
                          reason=f"provision_refused:{exc.reason}")
        _logger.warning(
            "the graph's record cursor was REFUSED for %s/%s (%s): %s",
            org_id, graph_id, exc.reason, _bounded_cause(exc))
        return {"provisioned": False, "reason": f"refused:{exc.reason}"}
    except Exception as exc:
        note_uncut_graph(org_id, graph_id, reason="provision_failed")
        _logger.warning(
            "the graph's record cursor could NOT be provisioned for %s/%s "
            "(%s: %s) — the graph was created and every record it writes is "
            "refused until it is seeded (`record_allocator_cli.py backfill`). "
            "`/health` reports this under `record.armed_but_uncut`",
            org_id, graph_id, type(exc).__name__, _bounded_cause(exc))
        return {"provisioned": False, "reason": "provision_failed"}
    else:
        try:
            connection.commit()
        except Exception as exc:
            note_uncut_graph(org_id, graph_id, reason="provision_failed")
            _logger.warning(
                "the graph's record cursor could not be COMMITTED for %s/%s "
                "(%s: %s) — the graph exists and is uncut; remedy: "
                "`record_allocator_cli.py backfill --org-id <org>`",
                org_id, graph_id, type(exc).__name__, _bounded_cause(exc))
            return {"provisioned": False, "reason": "commit_failed"}
        # The graph IS cut over now, so any earlier observation of it is stale.
        clear_uncut_graph(org_id, graph_id)
        return {
            "provisioned": True,
            "graph_id": graph_id,
            "org_id": org_id,
            "seq": outcome.seq,
            "mode": outcome.mode,
            "already_cut_over": outcome.already_cut_over,
            "seeded": outcome.seeded,
            "reason": None,
        }
    finally:
        if connection is not None:
            # A failed statement leaves the transaction ABORTED; rolling it back
            # explicitly before the RELEASE keeps the pooled handle reusable
            # (the pool resets a leftover transaction on the next borrow, and
            # `close()` is a RELEASE to the pool, not a kernel goodbye).
            with contextlib.suppress(Exception):
                connection.rollback()
            with contextlib.suppress(Exception):
                connection.close()


def _live_write_verdict(
    *, durable: bool = True, tenant: tuple[str, str] | None = None,
    consult_breaker: bool = True,
) -> tuple[str | None, tuple[str, str] | None, Any]:
    """``(why an armed live write is refused before any socket, its tenant, detail)``.

    THE ONE DECLARATION of :func:`write_live_record`'s preconditions (#10000
    review P0). It is a separate function because the door is no longer the only
    caller: a producer that must decide WHICH STORE holds a write before making
    it (``sdk._write_capture_turns``) has to ask the same question the door will
    ask, and a second copy of these rules is how the decision and the write come
    to disagree — which is exactly how a turn lands in neither store (§17.1: a
    decision made before the write must be the write's own predicate, and
    anything the predicate cannot name is decided AFTER the write).

    ``None`` means the write will be ATTEMPTED. Everything the seam can decide
    without a socket is decided here — the arming surface, the journal's
    durability, the tenant, and the connect breaker. Reachability is
    deliberately NOT here: it can only be learned by connecting, which is the
    write.

    ``detail`` is what the WARNING needs and nothing else: the refusal's own
    exception for ``malformed_dsn``, the offending carrier for
    ``no_tenant_carried``, ``None`` otherwise. It is returned rather than
    re-derived by the reporter, because a second derivation is a second chance
    to report a value the verdict never saw.

    ⛔ ``consult_breaker=False`` IS FOR READERS (#10000 review P1). Every other
    arm above names a fact about the DEPLOYMENT or the TENANT, and therefore
    about where a past capture's turns went. ``circuit_open`` names a fact about
    the last 60 s of THIS process — a WRITE-side cost bound
    (:data:`LIVE_WRITE_BREAKER_SECONDS`: one connect timeout per journal record
    on the capture thread), which says nothing about where a transcript already
    lives. A reader that inherits it decides "the record holds nothing" without
    asking, from a signal that cannot answer — the deployment was armed, the
    record holds the turns, and one failed capture connect blanked the
    transcript for 60 s with no log line and no counter. So the read doors
    (``hosted_api._record_tenant_for_read``, ``sdk._record_turn_answer``) pass
    ``False`` and ASK the record: a read pays at most one bounded connect for
    one request, and a record that then fails is reported by the read's own
    fail-soft arm instead of being pre-judged here. Nothing on the write path
    may pass it — the breaker is exactly the bound the write needs.
    """
    try:
        armed = record_dsn() is not None
    except RecordWriteError as exc:
        return "malformed_dsn", None, exc
    if not armed:
        return "unarmed", None, None
    if not durable:
        return NON_DURABLE_JOURNAL_REFUSAL, None, None
    # ``record_tenant_effective`` IS the #10067 resolution and this is the call
    # that keeps it load-bearing: **carried > scope > env pair**, in ONE place.
    # Spelling it as `tenant if tenant is not None else record_tenant()` here (what
    # main wrote) is the same VALUE but a second copy of the precedence rule — and
    # a second copy is what #10067 exists to end (#10067 review P1: the producer
    # and the seam disagreed and the transcript went to neither store).
    resolved = record_tenant_effective(tenant)
    if resolved is not None and not is_usable_tenant(resolved):
        # The seam's ONLY injection point must not be its weakest carrier: an
        # empty/half pair would be unpacked straight into `write_records` and
        # file the row under a tenant nobody named (#9188 review F4).
        # ⛔ THE TEST IS THE SHARED DECLARATION, not a second copy of it: the
        # capability read that decides WHETHER to hand the record a write asks
        # the SAME function (`record_holds_turns` -> `live_write_refusal`), so
        # there is one validity rule rather than two spellings of one (#10067
        # review P3).
        return "no_tenant_carried", None, resolved
    if resolved is None:
        return "no_tenant", None, None
    if consult_breaker and _breaker_is_open():
        # A recent connect FAILED. Refuse locally: the journal already holds the
        # record, a replay heals it, and paying the connect timeout once per
        # record is what turns a degraded record into a wedged capture. A READER
        # opts out (see `consult_breaker` above): it has one request, not N
        # records, and this refusal is not evidence about a stored transcript.
        return "circuit_open", None, None
    return None, resolved, None


def live_write_refusal(*, durable: bool = True,
                       tenant: tuple[str, str] | None = None,
                       consult_breaker: bool = True) -> str | None:
    """Why an armed live write would be REFUSED (``None`` ⇒ it will be tried).

    The public half of :func:`_live_write_verdict`, for a caller that needs the
    answer BEFORE the write — ``sdk._write_capture_turns`` asks it to decide
    whether the record can hold this capture's turns, and
    ``hosted_api._record_tenant_for_read`` asks it to decide whether it may serve
    the transcript from the record. Both use THIS function, so the read side and
    the write side cannot disagree about which store holds the turns (#10000
    review P0-2/P0-3).

    ⛔ ``None`` is not a promise: it says no *precondition* refuses the write.
    A connect can still fail, the breaker can trip mid-capture, and the fold can
    still refuse the record — so a caller deciding a store from this answer
    ALONE is deciding before the write. Read the write's own outcome
    (:func:`write_live_record`'s return) for the rest, and keep an alternative
    store for what it reports it did not do.

    ``consult_breaker=False`` is a READER's statement: a write breaker is not
    evidence about where a stored transcript is (see
    :func:`_live_write_verdict`).
    """
    return _live_write_verdict(durable=durable, tenant=tenant,
                               consult_breaker=consult_breaker)[0]


def record_holds_turns(*, journal_configured: bool, durable: bool = True,
                       tenant: tuple[str, str] | None = None,
                       consult_breaker: bool = True) -> bool:
    """Can the RECORD hold a capture's TURNS for this tenant, right now?

    ⛔ ONE declaration, THREE callers — the capture writer that DECIDES where a
    capture's turns go (`sdk._write_capture_turns`), the hosted detail read, and
    the SDK's own `get_session_detail`. They ask it so that the store a write
    chooses and the store a read consults cannot disagree, which is the failure
    the #10000 review measured from both ends (a read that went to the record
    while the write had gone to the graph, and a write that went nowhere while
    the capture reported success).

    Two facts, both capability, neither a policy flag (#10000's ruling: §9.1/D1
    puts the transcript in Postgres, so nothing here may be a toggle that sends
    it back):

    * ``journal_configured`` — a capture's turns reach Postgres through the
      RECORD WRITES the journal carries (`_write_capture_turns` emits them when
      the SDK has a journal): ONE `TurnsRecorded` carrier per capture on the
      record-only lane (#10067), and one node-creating `PointAdded` per turn only
      where the GRAPH already holds the turns. Either way the journal is the
      vehicle, so without one the record cannot be where the turns are, whatever
      its DSN says. ``durable`` is the same fact one step in: a scratch buffer is
      not a journal and the seam declines it.
    * :func:`live_write_refusal` — the write door's own preconditions (armed
      DSN, a tenant, a closed connect breaker). A reason it names is a write this
      record will refuse, so its turns stay in the graph.

    ⛔ THE BREAKER HALF IS A WRITE FACT AND A READER MUST NOT INHERIT IT. The
    capture writer asks this with the default (``consult_breaker=True``); a READ
    door passes ``consult_breaker=False``, because "a connect failed in the last
    60 s" does not move a transcript that is already stored — and reading it as
    "the record holds nothing" blanked healthy transcripts, silently, for the
    length of the breaker (#10000 review P1).

    ⛔ ``True`` is a PREFLIGHT, not a promise: a connect can still fail. The
    writer therefore re-reads what each write actually reported; a reader has
    the stronger evidence instead (`record_read.session_turns` says whether the
    record ANSWERED), so a ``True`` here plus a silent record still falls back.
    """
    if not journal_configured:
        return False
    return live_write_refusal(durable=durable, tenant=tenant,
                              consult_breaker=consult_breaker) is None


def _report_live_write_refusal(reason: str, record_type: Any,
                              detail: Any = None) -> None:
    """Count and log ONE pre-write refusal — the door's whole observable half.

    Extracted from :func:`write_live_record` so :func:`_live_write_verdict` can
    decide WITHOUT counting: a caller that only asked the question did not
    attempt a write, so it must not move the metrics. The door in turn must
    report exactly what it reported before this refactor — same counters, same
    sentences, same counters' names.
    """
    if reason == "malformed_dsn":
        _count_write_failure("malformed_dsn")
        _logger.warning("the record write did not happen (malformed_dsn): %s", detail)
        return
    if reason == NON_DURABLE_JOURNAL_REFUSAL:
        # A DECLINE, not a loss: this lane has no journal by design, so no row is
        # missing that a replay could restore (#9231 review P2). Counted on the
        # skip counter and logged at DEBUG — a warning per extracted record on
        # the healthy M2 capture path is the same noise the counter was.
        _count_non_journal_skip(NON_DURABLE_JOURNAL_REFUSAL)
        _logger.debug(
            "the record write was declined (no_durable_journal) — the record "
            "is armed but this log is a scratch buffer, not a journal, so no "
            "replay could reproduce the row: %s", record_type,
        )
        return
    if reason == "no_tenant_carried":
        _count_write_failure("no_tenant")
        _logger.warning(
            "the record write did not happen (no_tenant) — a CARRIED tenant "
            "was not a (org_id, graph_id) pair of non-empty strings: %r",
            detail,
        )
        return
    if reason == "no_tenant":
        _count_write_failure("no_tenant")
        _logger.warning(
            "the record write did not happen (no_tenant) — the record is armed "
            "but no tenant could be named for this write.%s",
            _refused_tenant_env_note(),
        )
        return
    if reason == "circuit_open":
        _count_write_failure("circuit_open")
        _logger.warning(
            "the record write did not happen (circuit_open) — a connect to the "
            "record failed within the last %.0fs, so this write is refused "
            "without a socket: %s", LIVE_WRITE_BREAKER_SECONDS, record_type,
        )
        return
    # Unreachable by construction: the verdict returns only tokens from
    # LIVE_WRITE_REFUSALS. Assert rather than ignore — a reason nobody counted
    # would make a refusal SILENT, the one failure mode this door may not have.
    raise AssertionError(
        f"unreported live-write refusal {reason!r} (known: "
        f"{', '.join(LIVE_WRITE_REFUSALS)})")


#: What to DO about each allocator refusal, keyed by the metric the allocator
#: counted. The door cannot raise (its caller's append has already committed), so
#: the remedy has to be in the log line or the operator is left with a token that
#: says "something in the record failed".
_ALLOCATOR_REMEDY = {
    "cursor_not_active": (
        # ⛔ THE `halted` HALF USED TO DEAD-END (re-review, P2). It read "run the
        # seed and then `arm`, … a `halted` one means … `reopen` is the resume" —
        # but `seed` does NOT change `mode` (`SEED_SQL` sets `seq`/`updated_at`
        # only), so a `seed` on a `halted` counter leaves it `halted` and `arm`
        # (`AND mode='seeded'`) refuses. `reopen` is not a parenthetical aside,
        # it is a REQUIRED STEP, and the chain must name it in order.
        # ⛔ THE NO-ROW AND `seeded` BRANCHES CARRY NO TRANSITION ARROW ON
        # PURPOSE (re-review, P2): they are single-step, and an arrow there
        # interleaved a `seeded → active` ahead of the halted chain's
        # `halted → seeded`, which makes the message's arrows read out of order
        # by any prefix-order check and invites the reader to run them that way.
        # ⛔ THE `seeded` BRANCH MUST NOT SAY "never armed" (re-review round 3,
        # P2): `REOPEN_SQL` moves `halted → seeded` and KEEPS `seq`, so a
        # reopened cursor WAS armed and its numbers WERE consumed — the very
        # state this PR's live test constructs as `(4, 'seeded')`. The sibling
        # refusal in `record_projection` forbids this wording verbatim ("⛔ NOT
        # 'never armed'"); two operator-facing messages giving opposite advice
        # for one state is the class being removed here.
        # ⛔ AND IT MUST KEEP THE RE-SEED. `arm` sets only `mode`, so a `seeded`
        # counter that is BEHIND the table (a restore, or a write made before
        # arming) is armed into the colliding state — the next allocation then
        # issues an ordinal the table already holds, i.e. this very
        # `duplicate_position`. The seed is monotone `GREATEST`, so naming it
        # costs nothing when the counter is already right.
        "the counter has no row for this graph, or its mode is not 'active'. "
        "With no row, run the seed and then `arm` — that arms it. A `halted` "
        "cursor was stopped by a defect, and `seed` does NOT change the mode, so "
        "it takes `reopen` (`halted → seeded`), re-seed, then `arm` "
        "(`seeded → active`). A `seeded` cursor is not armed: re-seed it "
        "(monotone `GREATEST`, so it is free when the counter is already right, "
        "and it is required when a restore or a pre-arm write left the counter "
        "behind) and then `arm`"),
    "duplicate_position": (
        "the counter issued a position the record already holds — the counter is "
        "BEHIND the table (a restore that bulk-loaded `journal_event` without the "
        "counter). Do NOT re-arm it WITHOUT re-seeding first — that is how the "
        "same ordinal is issued twice; but `arm` is also the ONLY way back to an "
        "allocating counter, so a remedy that forbids it outright closes the last "
        "door. `halt` it FIRST (`active → halted`) — `seed` and `reopen` both "
        "REFUSE from `active`, so neither is the first step — then re-seed "
        "(monotone `GREATEST`), `reopen` (`halted → seeded`) and `arm` "
        "(`seeded → active`)"),
}


def _allocator_refusal_cause(exc: BaseException | None,
                             depth: int = 4) -> str | None:
    """Which ALLOCATOR refusal is buried in this failure, or ``None`` (#12894).

    Looked up by TYPE through the `from exc` chain, for the same reason
    :func:`_journal_collision_cause` is: a message match would fire on a record
    whose payload happens to contain the words. The return value is the metric
    name the allocator counted, which is also the key into
    :data:`_ALLOCATOR_REMEDY` — one vocabulary, pinned by
    ``tests/test_12894_record_cursor.py::test_the_door_speaks_the_counters_vocabulary``:
    a refusal cannot enter `kinds` below without naming a token
    `record_allocator.metrics()` really keeps and `_ALLOCATOR_REMEDY` really
    answers. The lookup at the call site stays a defensive ``.get`` with a
    fallback rather than a strict index, because it runs in the door's failure
    path where a ``KeyError`` would replace the failure being reported — so the
    invariant is a test, not a raise (fresh-context review, P3: the token WAS
    `position_collision` while the counter was `duplicate_position`).
    """
    from . import record_allocator as ra

    kinds = {ra.CursorNotActive: "cursor_not_active",
             ra.PositionCollision: "duplicate_position"}
    seen = 0
    while exc is not None and seen <= depth:
        for kind, metric in kinds.items():
            if isinstance(exc, kind):
                return metric
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return None


def _journal_collision_cause(
        exc: BaseException | None,
        depth: int = 4,
) -> record_projection.JournalPositionCollision | None:
    """The COLLISION buried in a wrapped failure, or ``None`` (#9318).

    `RecordWriter.write` wraps every projection failure in `RecordWriteError`
    (``from exc``), so the live door — which catches that wrapper — cannot tell
    a collision from a lost write by the exception it holds. It has to look
    through the chain, and it must do so by TYPE rather than by the message: a
    string match would also fire on a record whose payload happens to contain
    the words, and a guard that mis-classifies is worse than one that does not
    classify at all.

    ``__cause__`` first, ``__context__`` second (an implicit chain is still a
    chain), and the walk is BOUNDED with a visited set: a self-referential or
    pathologically deep chain cannot make this loop, which matters because this
    runs inside the door's failure path where an exception would replace the
    failure being reported.
    """
    seen: set[int] = set()
    while exc is not None and depth > 0 and id(exc) not in seen:
        if isinstance(exc, record_projection.JournalPositionCollision):
            return exc
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
        depth -= 1
    return None


def write_live_record(record: Mapping[str, Any], *,
                      durable: bool = True,
                      seq: int | None = None,
                      tenant: tuple[str, str] | None = None,
                      allocate: bool = False,
                      journal_skipped: bool = False,
                      ) -> RecordWrite | None:
    """The LIVE door: one journal record that has ALREADY been appended.

    Called at each of the three places the journal is appended on a live write
    — `TortoiseSDK._emit_event`, `EventAPI._emit`, `ConversationMiner.
    _emit_event` — so every producer's record reaches the record, not just the
    SDK's. §17.1: the append is the commit point, so this runs after it and
    before any cache fold.

    ``durable`` says whether the log the record was appended to is a JOURNAL.
    The record is ``replay(journal)`` — its whole repair story is "rebuild the
    record from the log" — so a record appended to a SCRATCH buffer must not
    become a row: nothing could ever reproduce it and no replay could correct
    it. The M2 session-capture extraction lane appends through
    `EventAPI._emit` to `_InMemoryEventLog` (no file I/O, by design), so a
    capture on that lane would otherwise file rows the journal never carried.
    ``False`` ⇒ nothing is written and the DECLINE is counted on
    ``monitoring.metrics()["record_non_journal_skips"]`` — NOT on
    ``record_write_failures``, because a lane that has no journal by design lost
    nothing a replay could restore (#9231 review P2: counting it as a failure
    made that counter permanently non-zero on the healthy capture path, since
    every ``capture_session`` emits through the M2 lane). Read AFTER the arming
    check, so an UNARMED deployment stays byte-identical (no counter, no log
    line) on this lane too.

    Returns ``None`` ONLY when the record is not armed (no DSN): nothing was
    attempted, no connection, no plan — the unarmed deployment is
    byte-identical to the pre-record one (§2.0's sequence). Otherwise it
    returns a :class:`RecordWrite` — ``APPLIED``, or ``REFUSED`` when an ARMED
    call did not write, with ``refusal`` naming which counter term it was — and
    a FAILURE never raises: it is counted
    (``monitoring.metrics()["record_write_failures"]``) and logged, because the
    journal already holds the record and a question about the derived record
    must not fail a write that persisted. ``replay`` heals the gap — ⛔ EXCEPT ON A
    CUT-OVER GRAPH, and the condition is the ``record_cursor`` ROW, not the write
    arm: ``_refuse_an_enumerated_replay`` refuses an enumerated replay by the ROW
    (its docstring is explicit: "the row — not the mode — is the fact"), so an
    enumerated replay heals nothing on any graph that has one, WHATEVER
    ``allocate`` was on the write that failed. There the repair is an allocating
    replay (``replay(allocate=True)`` — ⛔ a CODE CALL, not a CLI verb: nothing
    outside `tests/` invokes it, so an operator cannot run it from this tree's
    tools; #13043 review P2-9) or the allocator's operator path (see the
    ``position_unverifiable`` arm below). That
    contract is enforced, not just intended: every counter call goes through
    :func:`_count_write_failure`, which suppresses its own failures.

    ⛔ THE REFUSAL IS RETURNED, NOT ONLY COUNTED (#8447). Every arm below that
    refuses an ARMED write used to ``return None`` — the same value the unarmed
    path returns — so the producer holding it could not tell "off by policy"
    from "refused, nothing stored", and the only signal was a counter the
    producer does not read. `None` now means exactly one thing on this door.

    ⚠️ RETURNED, AND CONSUMED BY ONE DOOR (#10021). The capture writer is the
    door that cannot proceed without the answer: `sdk._emit_event` hands this
    return to `_write_capture_turns` through ``record_sink``, and that writer
    branches on ``applied``/``statements`` to decide whether the turn is in the
    record at all or must be written to the graph instead (§9.1/D1's
    "the store follows the capability"). The other two production call sites —
    ``api.EventAPI._emit`` and ``mining._emit_event`` — still DISCARD the value,
    and no door branches on ``refusal`` itself: for them the runtime signal for a
    refused ARMED write remains the counter and the WARNING. Consuming
    ``refusal`` where the store decision is not at stake is #8447's work (an
    explicit signal instead of a counter), not this change's.

    The RETURNED WATERMARK describes journal POSITIONS, so a write with no
    proven ordinal consumed none: it reports ``last_seq == first_seq - 1``, this
    class's own "nothing consumed" spelling (see :attr:`RecordWrite.last_seq`),
    rather than claiming position 1 was written. ``applied`` still answers the
    other question truthfully — the four ontology layers WERE written.

    The transaction is THIS write's own (the journal append was a file append,
    so there is nothing to span): the seam opens the connection, this commits
    it, and it is disposed of on every path — and since #9558 the dispose is a
    RELEASE back to the pool's idle set rather than a close, because
    `_default_connect` may hand out a `_PooledConnection`.

    Cost, measured at #9188's head: an armed record cost ~one connection +
    commit and ~4 ms of plan materialization (the fold re-read the record
    migrations to build its column map for every record — `table_columns()` was
    deliberately uncached, so a migration edit could never be read stale).
    ⛔ BOTH HALVES ARE NOW PAID ONCE RATHER THAN PER RECORD (#9558 / R10), and
    the measurement is in `docs/evidence/9558-record-write-cost/`:

    * the CONNECTION is REUSED across records (`_default_connect` returns an idle
      handle when it has one — the door's own contract is untouched: it still
      commits and disposes of the handle exactly once), and
    * the COLUMN MAP is served from the process's RESIDENT schema
      (`record_projection.table_columns`) — resolved once per process, so an
      unchanged schema costs ZERO filesystem operations rather than a stat pass
      (#4625). ⛔ A migration EDIT is therefore seen by the next RESOLUTION, not
      by the next record: the refresh verb is
      `record_projection._release_schema_cache()`, and the deployed image bakes
      these files in, so a redeploy is the production path. A caller that names
      an EXPLICIT migrations directory keeps the per-file fingerprint and its
      edit-detection (#9558).

    Measured through THIS door against a real Postgres 16 carrying the record
    migrations, 60 records after 5 warmup, ONE instrument run four times against
    each tree: **49.7–52.9 → 255.7–261.6 records/s**, per-record mean
    **18.9–20.1 → 3.82–3.91 ms**, `connect()` per record **1.00 → 0.00**, and
    the SERVER logged **66 → 2**
    authorized connections for the same 65 writes in every run (a count no
    client-side change could move). What remains is one transaction boundary per
    record, which is
    DELIBERATE: one record, one transaction, no batching, because a batch seam's
    ordering across three independent doors is a throughput question with no
    measurement behind it yet — and the connection is now reused, so batching is
    no longer the cheapest available win. The UNHEALTHY case is bounded too, in
    two places that answer different questions: opening the connection is capped
    by :data:`CONNECT_TIMEOUT_SECONDS` (~10 s), and a connect that FAILS trips
    :data:`LIVE_WRITE_BREAKER_SECONDS` so the REST of the capture is refused
    locally (counted ``circuit_open``) instead of paying that timeout once per
    record — N records used to cost N × 10 s inside one ``capture_session`` call,
    which is a wedged capture, not a degraded one.

    ⚠️ RESIDUAL, stated rather than implied: the death is proven at BORROW, which
    closes the window where a handle sat dead while PARKED — and only that one.
    TWO windows open AFTER the borrow remain, and neither is silent — both still
    count ONE ``write_failed`` record (healed by replay, unless the graph is cut
    over): a death that lands between the borrow and the first statement, and a
    peer that VANISHES without closing (a partition, a blackholed route): a
    socket that never learns of the death has no pending input to verify. ⛔ THE
    PROBE DECIDES NEITHER: it has already run when the first of them begins, and
    closing that one needs a RETRY the door does not do (the failure is counted
    and refused), while no check that PRECEDES the write can close the second.
    The handle is DISCARDED the moment a death IS seen (see
    :func:`_take_idle_connection`), so the cost is one record per DEATH rather
    than one per following record.

    ⛔ THE PARAGRAPH THIS REPLACES ACCEPTED ONE LOST RECORD PER DEAD HANDLE, and
    its own reasoning was right about the option it considered: *"a health query
    per acquire would remove it entirely at the price of a round trip on every
    record, which is not worth it"*. The option it did NOT consider is the one
    taken here — read the LOCAL SOCKET first (readiness probe, measured
    **~1–2 µs**, no round trip) and ask the server ONCE only when that read finds
    something pending, which is where the round trip belongs: it is then a
    question about a specific handle, not a tax on every record. The
    measurement that made this worth doing: the hosted deployment lost 8 record
    writes out of 1292 attempts (~0.6 %) to this exact path, monotonically, and
    the ``write_failed`` counter is what flipped `record.degraded`; a pooler's
    idle timeout, a failover or an operator restart is enough to produce it,
    and on a CUT-OVER graph the enumerated replay that is supposed to heal it is
    REFUSED (#12822). The lane measurement: 400 writes with the parked backends
    terminated from outside, **18 lost before this change and 0 after** (one
    command, both runs). The PIN is narrower and says so: `tests/test_9078…::
    test_a_parked_handle_whose_backend_died_costs_a_reconnect_not_a_record`
    writes TWICE around a real `pg_terminate_backend` and asserts the second one
    is APPLIED with both rows present and the counter unmoved — it proves the
    mechanism, and it is not the source of the 400-write figures above.

    ``seq`` is the record's OWN journal position — what `EventLog.append`
    returned (#9190). It is a fact of the journal, never an allocator: the replay
    numbers records ``enumerate(records, start=1)`` over the same read, so a live
    write and a full replay give one record one number by construction, and the
    position row lands in layer 1 (`public.journal_event`, and the
    `public.record_event_stream` view over it) on the live path.

    ``None`` names ONE population, and it is NOT the in-memory capture lane
    (#9307 review F1 — this paragraph used to say it was, which is false): that
    lane is declined EARLIER, at ``durable=False``, and counted
    ``no_durable_journal``, so it never reaches the position decision. What
    arrives here is a DURABLE journal whose ``append`` SUCCEEDED but could not
    PROVE the record's position — the ``None`` states listed by
    :meth:`tortoise.log.EventLog.append` (a tail it could not size, a
    cross-process lock it could not take, a journal it could not count, a peer
    writing unserialised inside its window, an inode swapped under the path).
    Two of those are documented as REPEATABLE windows on a large journal
    (#9252), so this is not a one-record curiosity. Such a record writes no
    position row — nothing there could be numbered, and inventing one is the
    #9190 defect — and the row arrives on a later replay, which re-numbers the
    SAME journal. The skip is therefore NAMED rather than silent: counted on
    ``monitoring.metrics()["record_position_unproven"]``, WARNed once per
    :data:`POSITION_UNPROVEN_WARN_SECONDS`, and the returned watermark does not
    claim the position it did not consume (see RETURN below).

    ⚠️ RESIDUAL — the one shape the paragraph above does not cover: a tenant with
    TWO durable journals. "One record one number by
    construction" holds per JOURNAL FILE, while `journal_event`'s key is per
    `(org_id, graph_id, seq)`: two durable journals under one tenant each number
    their records from 1, so the second journal's rows collide with the first's
    at `ON CONFLICT (org_id, graph_id, seq) DO NOTHING` (and the existing row
    keeps the OTHER journal's `event_type`/`payload` while the layer row
    underneath belongs to a different record). That was reachable before the
    live path existed only replay-vs-replay; the live path now participates,
    which is why it is stated here rather than left to be rediscovered (#9307
    review P2 — tracked on #9318).

    ⛔ SINCE #9318 IT IS NO LONGER SILENT, and still not REPAIRED. The fold's
    statement loop asks the cursor's affected-row count, and when the position
    row was absorbed it asks the database whether the surviving row is a record
    this write CONTINUES (`record_projection._refuse_a_journal_collision`, in
    Postgres's own jsonb equality and its own `timestamptz` comparison of
    `recorded_at`), and for a same-type/same-capture pair it then reads the two
    payloads it got back for contradicting identities (Python, `#9318`).
    Same content — and the same capture WHEN this write carries a `ts`; a
    `ts`-less write compares no instant at all ⇒ a genuine replay ⇒ silent no-op,
    unchanged;
    same `event_type` and same `recorded_at` with only the payload different ⇒
    READ AS the same record RE-DERIVED ⇒ also silent (#12904 P0-1) — a reading
    on two equal coordinates, not proof of one record — **unless the two payloads
    name CONTRADICTING identities**, in which case the identities this record
    would write are not the ones the row holds (a second record there, or this
    tree re-keyed it) and it is refused (#9318: the identity gate
    on the drift arm);
    anything else ⇒
    `JournalPositionCollision`, reported HERE as the
    ``journal_collision`` refusal — the same counter, the same WARNING, the same
    ``RecordWrite`` shape every other refusal uses — with a message naming the
    position and both payloads by digest.

    The REPAIR is journal IDENTITY in the key (a column identifying the
    journal, or a per-tenant ordinal allocator), not a number this door can
    derive — do NOT paper it over by declining position rows on the live path,
    which is the #9190 defect this door exists to close. That decision is
    `#12894`'s; when it lands, a collision cannot occur, every conflict is a
    replay, and the detector must be DELETED with that key change — leaving it
    in place converts legitimate old-journal replays into refusals (#12904
    P1-4).

    ``tenant`` is the ``(org_id, graph_id)`` the CALLER already resolved for
    this write — carried explicitly by a lane that cannot hold the ambient scope
    for the whole write (see :func:`use_record_tenant` and decision 6; the
    reason is confinement, not propagation). Precedence is
    **carried > scope > env pair**, and the order is a statement about
    specificity: a carried tenant was resolved by the same request that chose
    the graph this write went to (the hosted REST lane's ``_data_sdk``), so it
    is that request's answer travelling with its own SDK object rather than
    ambient state a worker could inherit from a DIFFERENT request. The scope
    still wins over the env pair, and the env pair is still refused unless the
    deployment declared single tenancy — none of #9192's fail-closed rules move.
    ``None`` (the default) is the historical behaviour, byte-identical: nothing
    is carried, the scope/env answer stands.

    ⚠️ The carrier is authoritative at THIS DOOR ONLY, and a HALF or non-string
    pair is refused exactly like an unnamed tenant, never unpacked
    (#9188 review F4): this is the seam's only injection point, so it must not
    be the one carrier weaker than the two feeding it. A lane whose records can
    also reach :meth:`tortoise.api.EventAPI._emit` or
    :meth:`tortoise.mining.ConversationMiner._emit_event` must give THOSE doors
    the same answer — they pass no carrier, so they read the scope/env pair, and
    a lane that mixed the two could split one request's rows across two tenants
    (#9346).

    ``allocate`` hands the POSITION to the record (#12894): the plan allocates
    from `public.record_cursor` and the resulting number is what this call
    reports as `first_seq`/`last_seq` and what the byte meter reads.

    ``journal_skipped`` says the CALLER did not append at all, because the record
    owns the ordinal (#14002). It is set by `tortoise.live_write.write` from
    :func:`record_owns_position` and it exists because the two ``seq is None``
    states must never collapse into one: on the ``#9307`` arm the append SUCCEEDED
    and could not prove a position (a refusal below), while on THIS arm there is no
    line at all — the counter is the only allocator, so there is nothing to
    corroborate the number against and nothing to diverge from. Concretely it
    changes three things and nothing else:

    1. it implies ``allocate`` (a skipped append can only be numbered by the
       counter), so ``allocate or record_live_allocate() or journal_skipped``;
    2. ``seq is None`` is then HEALTHY rather than ``position_unverifiable``;
    3. the cutover invariant is not checked, because there is no second reading to
       check — one number space is the whole point of the change, not a gap in it.

    ⛔ IT IS NOT A WAY TO SKIP THE JOURNAL ON A GRAPH THE RECORD DOES NOT OWN. A
    ``journal_skipped`` write on a graph with no ``active`` cursor row is refused
    by the allocator itself (``cursor_not_active``, with `arm`/`seed` named as the
    remedy) — the fail-closed direction. Only `record_owns_position`, which reads
    the flag AND the row, may set it.

    ⛔ ON THAT ARM ``seq`` IS **NOT** IGNORED — it is the OTHER SIDE of the
    cutover invariant. The record's counter issues the number and the journal's
    append reported one, and while BOTH allocators exist they must AGREE; a
    disagreement is refused and the allocation rolled back (the
    ``position_divergence`` arm below). ``seq=None`` is also a REFUSAL on this
    arm (``position_unverifiable`` — the counter would issue a number nothing
    corroborates), not the enumerated arm's row-less named skip; and a
    `seeded`/`halted`/absent counter is the third arm, refused before any of
    this. An earlier cut of this docstring said ``seq`` was ignored and that
    both arms did not apply; a fresh-context review measured the door and found
    the claim false (#12894 review cycle 4, P1), the same defect class this
    file's history already records once.

    ⛔ AND SINCE #14002 THE AGREEMENT ARM IS A WINDOW, NOT A PERMANENT STATE. On
    a CUT-OVER graph the chokepoint stops appending altogether, so there IS no
    second reading and nothing to diverge from: `journal_skipped=True` with
    ``seq=None`` allocates and applies, which is the shape the guard cannot
    produce a refusal for. The guard is NOT weakened — it still refuses every
    out-of-order ordinal a caller hands it, which is what pins the invariant for
    direct callers and for every pre-cutover graph; what changed is that the
    production path no longer manufactures the disagreement it refuses.

    The graph's cursor must be in mode ``active``. ⛔ A `seeded` /
    `halted` / absent cursor, or a duplicate position, comes back as
    ``outcome=REFUSED`` ``refusal="write_failed"`` and is counted — this door
    DOES NOT RAISE, by its own contract (the journal append has already
    committed, so a record failure must not fail a write that persisted). That
    is a REFUSAL of a mis-armed deployment rather than a lost row to be healed
    by replay. ⛔ THE DIFFERENCE IS **NOT** VISIBLE IN ``refusal``: all of these
    carry ``refusal="write_failed"``, the same token as a genuine record-write
    failure, because the vocabulary is closed and a new token would be a
    surface change. What distinguishes them is the WARNING the door emits
    (``_allocator_refusal_cause`` names the counter's state and the remedy —
    ``arm``/``reopen`` — instead of "a replay heals the record", which cannot
    apply) plus `record_allocator.metrics()`. An earlier cut of this docstring
    claimed ``refusal`` carried it; a fresh-context review measured the door and
    found the claim false (#12894 review cycle 3, P2).

    ``OVERRIDES:`` the module's decision 3 (`Armed but unreachable ⇒ the write
    FAILS (RecordWriteError)`) does not apply to this door, and that is a
    deliberate departure rather than an oversight. Decision 3 governs
    :meth:`RecordWriter.write`, whose caller's write has NOT yet committed; this
    door runs AFTER the caller's append is the commit point (§17.1), so raising
    here would hand a producer an exception for a write that persisted — the
    #5148 rule. The distinction decision 3 exists to keep (`degraded` vs
    `unconfigured`) is kept by the counter and the warning instead of by the
    exception, on the ``monitoring.record_write_failures`` surface. #9187 owns
    decision 3; the reversal is recorded there.
    """
    refusal, tenant, detail = _live_write_verdict(
        durable=durable, tenant=tenant)
    if refusal == "unarmed":
        # §2.0: the unarmed deployment is byte-identical — no counter, no log.
        return None
    if refusal is not None:
        _report_live_write_refusal(refusal, record.get("type"), detail)
        return _refused(refusal, seq)
    assert tenant is not None  # the verdict only clears with a resolved pair
    # ⛔ THE SWITCH (#12894). Read AFTER the refusals above, so an unarmed or
    # refused write is byte-identical whatever the flag says.
    #
    # ⚠️ AND IT **CAN** OVERRIDE A CALLER'S `allocate=False` (#13043 review P2-6).
    # The sentence here used to say the flag "can only turn the arm ON, never
    # override a caller's decision", which is false of the line below it: `or`
    # short-circuits on TRUTHY, so `allocate=False` falls through to the flag and
    # an ON deployment allocates whatever the caller passed. That is deliberate —
    # the flag describes the DEPLOYMENT (one graph cut over ⇒ every writer on it
    # allocates) and a per-call veto would let one caller write an enumerated row
    # onto a counter-issued graph, which is the split the cutover invariant exists
    # to prevent. A caller that must stay enumerated halts the GRAPH, it does not
    # pass `False` here.
    allocate = allocate or record_live_allocate() or journal_skipped
    org_id, graph_id = tenant
    connection: Any | None = None
    if seq is None and not allocate:
        # A DURABLE journal whose append could not prove the record's position
        # (see `seq` above): layer 1 gets no row for this record, which is a
        # NAMED skip and never a silent one (#9307 review F1).
        _note_unproven_position(record)
    if seq is None and allocate and not journal_skipped:
        # ⛔ THE ALLOCATED ARM NEEDS THE JOURNAL'S NUMBER TO CHECK AGAINST, so
        # "the append succeeded but could not prove the position" is a REFUSAL
        # here rather than a row-less write: the counter would issue a number
        # nothing corroborates, and the one invariant this arm exists to keep —
        # the line and its row carry the same number — would be unverifiable for
        # this record. Fail CLOSED, and say what ACTUALLY repairs it: on the
        # UNARMED arm a replay of the journal heals a row-less record, but on
        # THIS arm the counter is the authority and an enumerated replay is
        # REFUSED once the graph is cut over (`_refuse_an_enumerated_replay`) —
        # so "a replay heals it" is false here. ⛔ And the cost is not one record
        # either: the journal consumed a position the counter did not, so the
        # same one-behind gap the divergence arm produces is armed here too, and
        # every later armed write refuses for the same reason. The remedy is the
        # allocator's operator path, named in the warning below.
        #
        # ⛔ AND THIS ARM IS NOW THE ONLY ONE THAT READING CAN REACH ON A
        # CUT-OVER GRAPH, because the healthy path no longer appends at all
        # (#14002): `journal_skipped` is what separates the two `seq is None`
        # states, and a caller that dropped it turns every write on a cut-over
        # graph into this refusal — a permanent, counted, WARNed one. That is
        # why the chokepoint forwards it and why
        # `tools/record_wiring_check.py::live_call_sites_forward_the_journal_skip`
        # holds every door to it.
        _count_write_failure("write_failed")
        _logger.warning(
            "the record write was REFUSED (position_unverifiable) — the journal "
            "holds the record but the append could not PROVE its position, so "
            "the ordinal allocator (`%s`) has nothing to check the number it "
            "would issue against. An ENUMERATED replay does NOT heal this (it is "
            "refused on a cut-over graph), and "
            "the journal consumed a position the counter did not — so EVERY "
            "later armed write refuses for the same reason until the gap is "
            "closed (an ARMED replay of the missing record closes it one record "
            "at a time, once the caller commits — but that is a CODE call "
            "(`record_projection`'s `replay(..., allocate=True)`); there is no "
            "CLI verb for it in this tree). For the whole gap the remedy "
            "is the allocator's operator path: `halt` -> "
            "`seed --journal` -> `reopen` -> `arm`; record=%s %s",
            RECORD_ALLOCATE_ENV, record.get("type"), _record_identity(record),
        )
        return _refused("write_failed", seq)
    try:
        # #9190's ordinal, straight from the append. `position=False` only when
        # a durable journal could not PROVE the record's position (see `seq`): a
        # row needs a number, and inventing one collides with a later replay's
        # numbering. Counted and warned above, so the skip is observable.
        # ⛔ With `allocate=True` the number comes from the RECORD's counter
        # instead (#12894), so `seq` must NOT be forwarded as `first_seq`,
        # because `replay_plan` REFUSES a caller watermark on that arm
        # (`allocate=True derives the position from the record's counter, so
        # start_seq must be left at 1`). MEASURED
        # (review cycle 1, P1): forwarding it made a cut-over door refuse EVERY
        # write — all three production sites pass a real `seq` from `log.append`
        # — while the wiring gate blessed the body. So the arm passes the one
        # value the plan accepts and reports the ALLOCATED block back.
        write = write_records([record], org_id=org_id, graph_id=graph_id,
                              position=True if allocate else seq is not None,
                              first_seq=(1 if allocate
                                         else (seq if seq is not None else 1)),
                              allocate=allocate)
        _close_live_breaker()
        connection = write.connection
        # ⛔ THE CUTOVER INVARIANT (#12894): while BOTH allocators exist they must
        # AGREE, and a disagreement is a REFUSAL — never a silent renumbering.
        #
        # ⛔ THIS ARM IS UNREACHABLE WHEN THE APPEND WAS SKIPPED (#14002) — not
        # because the check was relaxed, but because `seq` is then `None` and
        # there is no second reading in existence. The guard below stays exactly
        # as it is for every ordinal a caller DOES hold (a direct caller, a
        # pre-cutover graph, a `backfill_copy_turns` batch): removing the second
        # allocator is the fix, weakening the comparison is not.
        #
        # On the allocated arm the record's counter issues the number and the
        # journal's append reported one; if they differ, writing the row anyway
        # puts the journal LINE at N and the position ROW at M for one record,
        # and a replay of the JSONL (which enumerates 1..N) can never reproduce
        # the row again — the two coordinates `#9318` exists to keep honest would
        # be split by this door. That is reachable on FOUR states that are all
        # real: a RESTORED or TRUNCATED journal (it reports a smaller position),
        # a SEALED one (`TORTOISE_JOURNAL_SEGMENTS=1` restarts the file-local
        # index at 1), a counter left behind by a refused write, and CONCURRENT
        # WRITERS — the journal is ordered by APPEND (the `sdk` site appends,
        # then calls this door, holding no lock across the two) while the counter
        # is ordered by ALLOCATION, so two in-flight writes can be numbered in
        # the opposite order to their lines: the one that allocated first
        # refuses (`allocated != its line`), the other applies, and the counter
        # is left exactly one behind the journal.
        #
        # ⛔ THE COST IS NOT "one refused record", AND THE TWO DIRECTIONS ARE
        # NOT SYMMETRIC — say it here rather than let an operator discover it, and
        # never describe them with one sentence:
        #
        #   * the journal AHEAD of the counter — the refused-write and
        #     CONCURRENT-WRITER states above. Every refusal arm appends its line
        #     and issues no counter number, so an ENUMERATED replay cannot close
        #     the gap: it writes rows and never the counter, and on a cut-over
        #     graph it is refused outright (`_refuse_an_enumerated_replay`). So
        #     every LATER armed write refuses for the same reason. ⚠️ An ARMED
        #     replay of the missing record DOES close it — `replay(allocate=True)`
        #     runs `ALLOCATION_SQL`, which ISSUES the position. `replay()` itself
        #     never commits (it is forbidden one — the caller does), so the gap is
        #     closed by the CALLER'S commit, and a rolled-back armed replay closes
        #     nothing. That is per-record
        #     surgery, though, so the general remedy for a whole gap is the
        #     operator chain: `halt` → `seed --journal <path>` → `reopen` → `arm`
        #     (the seed is allowed here, because the record's rows are BELOW the
        #     journal).
        #   * the journal BEHIND the counter — the RESTORED / TRUNCATED states
        #     above. WORSE, not better: each refused append still advances the
        #     journal by one, so the guard stops refusing after `counter - floor`
        #     records — and each of those records LOST its row with no operator
        #     action at all. It does not wedge, it eats. `seed --journal` REFUSES
        #     here (`SEED_SQL` needs `row_max <= enumerated_floor`) and a re-seed
        #     cannot lift the counter DOWN (`GREATEST(cursor_row.seq,
        #     EXCLUDED.seq)` is monotone), so `--enumerated-floor` only lets the
        #     SEED RUN — it is not a remedy. The remedy is to RESTORE the journal
        #     so its enumeration reaches the counter again.
        #   * SEALED (`TORTOISE_JOURNAL_SEGMENTS=1`) is STRICTLY WORSE, and it is
        #     the reason the seal is a gated operator transition. After a seal
        #     `EventLog.append` reports the FILE-LOCAL index (it restarts at 1)
        #     while the counter is per-GRAPH over the whole layout, so the guard
        #     refuses for `local` 1..`counter` — every one of those appends already
        #     committed its journal line and writes NO row, so it EATS `counter`
        #     records, more than the behind case's `counter - floor` — and then
        #     ACCEPTS at `local == counter + 1`, writing a row whose number is NOT
        #     the journal's position: a silent, permanent split of the two
        #     coordinates this guard exists to keep. NO counter value fixes that
        #     comparison, because the two sides are different COORDINATES, so the
        #     gate is procedural: ⛔ DO NOT ARM ACROSS A SEAL — halt the graph
        #     before sealing it. Segment-aware numbering is #13050, not a re-seed.
        #
        # That is the price of keeping one invariant — the line and its row carry
        # the same number — instead of guessing which side is right.
        #
        # ⛔ THE ROLLBACK IS THE OTHER HALF. Detecting the mismatch AFTER the
        # insert but BEFORE the commit is what makes this free: the allocation
        # (the counter increment) rolls back with the row, so the position is
        # neither BURNED (no gap) nor REUSED (nobody saw it). A check placed
        # after the commit would leave a row the record cannot number, and one
        # placed before the write could not exist — the counter's number is only
        # known once the statement has run.
        if allocate and seq is not None and write.applied:
            allocated_first = write.first_seq
            if allocated_first != seq:
                if connection is not None:
                    # ⛔ LOG THE SUPPRESSED EXCEPTION (#13043 review P2-7). The
                    # WARNING below asserts, as a FACT, that the allocation was
                    # rolled back — and a `suppress(Exception)` around the
                    # rollback could make that sentence false with nothing
                    # anywhere recording it. The rollback is the half that keeps
                    # the position from being BURNED, so a failure here is the
                    # one thing this branch must not swallow silently.
                    #
                    # ⚠️ UNASSERTED, and named so a later reader does not take
                    # it for coverage (#12894 review cycle 2 mutation table):
                    # this arm is reachable only when `rollback()` ITSELF raises,
                    # which no fake in the suite produces — the DB legs drive a
                    # real connection that rolls back cleanly. Added rather than
                    # dropped because the cost of the log line is nil and the
                    # alternative (a silent `suppress`) makes the WARNING below
                    # assert something nobody checked. A test for it needs a
                    # connection double that raises on `rollback`; that is a
                    # fake the seam does not otherwise accept, so it is recorded
                    # here instead of invented.
                    try:
                        connection.rollback()
                    except Exception as rollback_exc:  # noqa: BLE001, RUF100
                        _logger.warning(
                            "the divergence guard could NOT roll the allocation "
                            "back (%s: %s) — the counter may be one ahead of "
                            "the rows. `record_allocator_cli.py status` reads "
                            "both sides; a counter that is ahead is the state "
                            "that EATS rows, so halt the graph before "
                            "continuing",
                            type(rollback_exc).__name__,
                            _bounded_cause(rollback_exc),
                        )
                _count_write_failure("write_failed")
                _logger.warning(
                    "the record write was REFUSED (position_divergence) — the "
                    "journal holds the record at position %s and the ORDINAL "
                    "ALLOCATOR issued %s, so the two allocators disagree about "
                    "this record's number. Writing it would put the journal line "
                    "and its position row at different numbers, which no replay "
                    "of the journal could reproduce; the allocation was rolled "
                    "BACK so nothing was burned or renumbered. Compare the two "
                    "readings with `tools/record_allocator_cli.py status "
                    "--journal <path>`, whose position_divergence field is THIS "
                    "door's condition (LIKELY CAUSES DIFFER IN REMEDY, so read "
                    "which side is ahead): if the JOURNAL is ahead — a refused "
                    "write consumed a position the counter did not — the gap "
                    "never closes and EVERY later armed write refuses until you "
                    "re-seed: `halt`, `seed --journal <path>`, `reopen`, `arm`. "
                    "If the JOURNAL is behind — a restored or truncated "
                    "journal — it does NOT wedge: the guard stops refusing "
                    "after `counter - floor` records, and each of them "
                    "SILENTLY LOSES ITS ROW; `seed --journal` refuses there and "
                    "a re-seed cannot lower the counter, so RESTORE THE "
                    "JOURNAL (`--enumerated-floor` only lets the seed run, it is "
                    "not a remedy). A SEALED journal is a THIRD case and is "
                    "STRICTLY WORSE than either: `status --journal` REFUSES a "
                    "segmented layout by design (it will not half-count one), and "
                    "measuring the layout total with "
                    "`journal_segments.read_all_segments` and passing it as "
                    "`--enumerated-floor N` corrects the SEED, not the "
                    "comparison, because the append's number stays FILE-LOCAL "
                    "while the counter is per-graph. So it EATS `counter` records' "
                    "rows and then ACCEPTS at `local == counter + 1` with a "
                    "MIS-numbered row. NO counter value fixes that — the gate is "
                    "procedural: do not arm across a seal, halt the graph first "
                    "(#13050 owns segment-aware numbering). "
                    "record=%s %s",
                    seq, allocated_first, record.get("type"),
                    _record_identity(record),
                )
                return _refused("write_failed", seq)
        if connection is not None:
            connection.commit()
            # AFTER the commit, and only when the seam's OWN connection
            # committed: this is the record's positive channel (#9548). Every
            # other record counter is a failure/decline channel, so without
            # this one `record_write_failures: {}` cannot tell a writing
            # deployment from one that was never reached.
            _count_write_applied()
            # #12762 — and the same ordering argument applies to the byte
            # meter, with a worse failure mode: the ledger increment lives on
            # another store (the control plane / the registry), so charging it
            # BEFORE this commit would leave a rolled-back write's bytes on the
            # org's figure forever (the counter refuses negatives, and no
            # repair path exists). AFTER the commit, a failed ledger write is
            # merely a missing increment — which `postgres_bytes.reconcile`
            # reports as `unmetered_bytes`, the safe direction.
            _record_written_bytes(org_id, write)
        if seq is None and not allocate:
            # No position was consumed, so the watermark must not report one
            # as if it had been (a caller advancing `last_seq + 1` must neither
            # rewind nor skip): the seam was handed the default `first_seq=1`
            # for a row-less write, and `first_seq - 1` is this class's own
            # spelling for "nothing consumed".
            # ⛔ NOT with `allocate=True`: there the counter issued the number
            # and `write.last_seq` IS it — overwriting it here would discard
            # the one fact the allocation exists to produce.
            return replace(write, last_seq=write.first_seq - 1)
        return write
    except Exception as exc:
        collision = _journal_collision_cause(exc)
        if collision is not None:
            # ⛔ A COLLISION, NOT A LOST WRITE (#9318). The row at this position
            # cannot be indexed by this write — it holds a different
            # `event_type`, a write captured at a different instant, identities
            # this write's payload CONTRADICTS (a second record, or this record
            # re-keyed since), or, for a `ts`-less write, a payload whose
            # derivation moved — so writing the layer rows under it would index
            # them by another record's event — refused rather than
            # shipped. Nothing is lost: the journal holds the record
            # and the message names both sides. Now made LOUD on the SAME
            # refused-write channel every other arm uses (one counter, one
            # WARNING, one `RecordWrite(refusal=…)`) with its own reason token,
            # so an operator can tell it from `write_failed`. It does NOT trip
            # the breaker: the connection was fine.
            _count_write_failure("journal_collision")
            _logger.warning(
                "the record write was REFUSED (journal_collision) — the "
                "journal holds the record but the record's position row "
                "cannot be indexed by this write: it holds a DIFFERENT "
                "record type, a write captured at a DIFFERENT instant, "
                "identities this write's payload CONTRADICTS, or — for a "
                "write with no `ts` to compare — a payload whose derivation "
                "moved. Any of those is a second journal, a restore, a "
                "rotation, a rebuild numbering below the high-water mark, or "
                "a derivation that re-keyed its identity, re-timed it, or "
                "moved its payload; a "
                "replay cannot heal it while the key carries no journal "
                "identity (#12894): record=%s %s: %s",
                record.get("type"), _record_identity(record), collision,
            )
            return _refused("journal_collision", seq)
        allocator_state = _allocator_refusal_cause(exc)
        if allocator_state is not None:
            # ⛔ A MIS-ARMED COUNTER IS NOT A LOST WRITE, AND ITS REMEDY IS NOT
            # "replay". Both `CursorNotActive` and `PositionCollision` arrive
            # here on the allocated path; the generic arm below would tell the
            # operator that "a replay heals the record", which is false twice
            # over — a replay neither arms a cursor nor gets past #9318's
            # collision refusal. Named and counted the same way (one counter, the
            # closed `write_failed` token), but with the READING that applies.
            _count_write_failure("write_failed")
            _logger.warning(
                "the record write was REFUSED (%s) — the journal holds the "
                "record; the ORDINAL ALLOCATOR refused to issue a position. %s: "
                "record=%s %s: %s",
                allocator_state, _ALLOCATOR_REMEDY.get(
                    allocator_state, "check record_allocator.metrics()"),
                record.get("type"), _record_identity(record), exc,
            )
            return _refused("write_failed", seq)
        if isinstance(exc, RecordConnectError):
            # Unreachable RIGHT NOW (not a refusal of this one record): bound the
            # cost of the rest of the capture. A totality or statement failure
            # deliberately does NOT trip it — the connection was fine.
            _trip_live_breaker()
        _count_write_failure("write_failed")
        # ⛔ THE REMEDY MUST MATCH THE ARM (#12894 review). On the ALLOCATED arm
        # (`TORTOISE_RECORD_ALLOCATE=1`, which this change makes reachable in
        # production) a plain enumerated replay is REFUSED by
        # `record_projection._refuse_an_enumerated_replay` — the cursor ROW, not
        # the mode, is the fact. So the unqualified "a replay heals the record"
        # is false exactly where it now gets read most; the sibling allocator and
        # `position_unverifiable` arms were fixed for this, and this arm is the
        # one that catches every remaining failure on that path.
        _logger.warning(
            "the record write did not happen (write_failed) — the journal has "
            "the record; %s: record=%s %s: %s",
            # ⛔ THE DISCRIMINATOR IS THE CURSOR ROW, NOT THE ARM. `allocate`
            # says which write arm ran; `_refuse_an_enumerated_replay` refuses by
            # the ROW, and the two are INDEPENDENT — a graph cut over and then
            # left with `TORTOISE_RECORD_ALLOCATE` unset (the partial rollout
            # `.env.example` documents)
            # refuses on the enumerated arm and landed here, where the old
            # `if allocate` branch told the operator "a replay heals the "
            # "record" about a graph whose every replay is refused. The claim is
            # now true in BOTH states instead of wrong in one: it names the row
            # as the condition, so an operator on either arm can tell which case
            # they are in. `record_allocator_cli.py status` reports the row.
            "a replay heals the record UNLESS this graph has a `record_cursor` "
            "row (cut over), in which case an enumerated replay is refused — "
            "and the remedy is the one the refusal below names for the row's "
            "MODE, not a chain that assumes `active`: `replay(allocate=True)` "
            "needs mode='active' (and is a CODE call — no CLI verb ships), "
            "`halt` is scoped to 'active', `reopen` to "
            "'halted'. Run `record_allocator_cli.py status` for the mode"
            if not allocate else
            "repair it with an ALLOCATING replay (`replay(allocate=True)`, a "
            "CODE call — no CLI verb ships), not "
            "a plain one, which a cut-over graph refuses",
            record.get("type"), _record_identity(record), exc,
        )
        return _refused("write_failed", seq)
    finally:
        if connection is not None:
            # Close AFTER the commit so an armed deployment does not leak one
            # backend slot per journal record; a close failure must not mask a
            # write failure that is already being reported above.
            #
            # ⛔ A CLOSE IS A RELEASE (#9558 / R10). `_default_connect` hands out
            # a `_PooledConnection`, whose `close()` returns the handle to the
            # idle set instead of to the kernel — so this line is unchanged, and
            # what it MEANS (exactly one dispose per opened handle, on every
            # path) is unchanged too. That is why the pool lives in the hook and
            # not here: no call site had to learn about it.
            with contextlib.suppress(Exception):
                connection.close()
