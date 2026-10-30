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
import contextvars
import hashlib
import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import record_projection, status_vocabulary
from .env_truthy import env_flag

_logger = logging.getLogger(__name__)

__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "LIVE_WRITE_BREAKER_SECONDS",
    "LIVE_WRITE_REFUSALS",
    "POSITION_ORDINAL_SOURCE",
    "POSITION_UNPROVEN_WARN_SECONDS",
    "RECORD_DSN_ENV",
    "RECORD_GRAPH_ENV",
    "RECORD_ORG_ENV",
    "RECORD_SINGLE_TENANT_ENV",
    "RecordConnectError",
    "RecordWrite",
    "RecordWriteError",
    "RecordWriteOutcome",
    "RecordWriter",
    "current_record_tenant",
    "dsn_fingerprint",
    "is_usable_tenant",
    "live_write_refusal",
    "record_dsn",
    "record_holds_turns",
    "record_tenant",
    "record_tenant_effective",
    "record_tenant_for",
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

#: The bound on OPENING the record's connection, in seconds. libpq's default is
#: ``0`` — wait forever — and the live door opens one connection per journal
#: record ON THE CALLER'S THREAD, after the journal append has committed. So an
#: armed-but-unreachable record (a dead host, or a firewall dropping SYNs) would
#: stall every capture for as long as the kernel takes, which is the difference
#: between "the record is degraded" and "the capture path is hung". A DSN that
#: names its own ``connect_timeout`` WINS: the operator's explicit bound is not
#: silently replaced by this one.
CONNECT_TIMEOUT_SECONDS = 10

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
    #: transaction: this seam never commits and never rolls back.
    connection: Any | None = field(default=None, repr=False)
    #: WHY an ARMED call did not write — one of the tokens
    #: ``monitoring.metrics()["record_write_failures"]`` / ``["record_non_journal_skips"]``
    #: is counted under (``malformed_dsn``, ``no_durable_journal``, ``no_tenant``,
    #: ``no_tenant_carried``, ``circuit_open``, ``write_failed``), else ``None``.
    #: The SAME vocabulary as the counters on purpose (#8447): a caller that
    #: reports the refusal and an operator reading the metric are naming one
    #: thing, and a second word list is a second thing to drift.
    #:
    #: ⛔ ``no_tenant_carried`` IS A SIXTH TOKEN ON PURPOSE (#10021 review P3).
    #: The live door splits the nil tenant from a MALFORMED CARRIED pair — one is
    #: "nobody said whose turns these are", the other is "the caller said, and
    #: the answer was unusable" — and both count on the ``no_tenant`` COUNTER.
    #: So the closed list above is the refusal vocabulary and the counter set is
    #: one word coarser: a consumer branching on this field must accept six
    #: values, while an operator reading the metric sees five. Listing only the
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
        :attr:`refusal` says WHY, but the SIX refusal tokens are not one
        severity: ``malformed_dsn`` / ``no_tenant`` / ``no_tenant_carried`` /
        ``circuit_open`` / ``write_failed`` are FAILURES, all five counted on
        ``record_write_failures`` (BOTH ``no_tenant`` arms count — the nil tenant
        and the malformed CARRIED pair, which is why the token list is one longer
        than the counter set), while ``no_durable_journal`` is a
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
            # "configured, not reached" ⇒ `degraded`. The other five are near
            # misses, and NOTHING here has its own term: `write_failed` covers an
            # unreachable record AND a record that WAS reached and refused the
            # projection; `malformed_dsn` is a SET-UP defect (a value IS set and
            # is wrong, so not `unconfigured`, which is defined as no value at
            # all); `no_tenant` is a deployment/caller gap; `no_tenant_carried` is
            # the same gap one arm over (a CARRIED tenant pair that was unusable);
            # and `no_durable_journal` (the healthy M2 capture lane) never asks
            # the store at all. So `degraded` — which `status_vocabulary` defines
            # as "off by OUTAGE" — is the NEAREST of the four RECORDED terms for
            # all six, not a measured outage for the other five. There is
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

    ONE constructor for all six refusal arms (#8447), for two reasons:

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
    # The operator's bound is a QUERY KEY, not a substring: `dsn` is the whole
    # URI, and a password such as `postgresql://svc:connect_timeout@host/db`
    # (needs no percent-encoding, passes `_DSN_SCHEMES`) would otherwise take
    # the "already bounded" branch and restore libpq's wait-forever default on
    # the per-record connect this constant exists to cap (#9231 review P2).
    if "connect_timeout" in parse_qs(urlsplit(dsn).query):
        # The operator bounded it already; do not replace their number.
        return psycopg2.connect(dsn)
    return psycopg2.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS)


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
                # Armed-but-unreachable MUST NOT read as unarmed (decision 3),
                # and it is its OWN class so the live door can bound the cost of
                # a dead record (`LIVE_WRITE_BREAKER_SECONDS`).
                raise RecordConnectError(
                    f"the record is armed but its connection could not be "
                    f"opened ({type(exc).__name__})"
                ) from exc
            opened = target

        try:
            report = record_projection.replay(
                records, target, org_id=org_id, graph_id=graph_id,
                start_seq=first_seq, position=position,
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
    position: bool = True,
    writer: RecordWriter | None = None,
) -> RecordWrite:
    """One-call form of :meth:`RecordWriter.write` — the chokepoint's entry point.

    Same contract, same outcomes, same failures. Constructing a
    :class:`RecordWriter` once and reusing it is equivalent — the DSN is
    resolved per call either way, so a late arming is seen by both spellings —
    and this function exists so a caller with no seam object to hold does not
    have to invent one.
    """
    return (writer or RecordWriter()).write(
        records, org_id=org_id, graph_id=graph_id, first_seq=first_seq,
        conn=conn, position=position,
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


def _count_non_journal_skip(reason: str = "no_durable_journal") -> None:
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
    "unarmed", "malformed_dsn", "no_durable_journal", "no_tenant",
    "no_tenant_carried", "circuit_open",
)


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
        return "no_durable_journal", None, None
    # ``record_tenant_effective`` IS HEAD's #10067 resolution and this is the call
    # that keeps it load-bearing: **carried > scope > env pair**, in ONE place.
    # Spelling it as `tenant if tenant is not None else record_tenant()` here (what
    # main wrote) is the same VALUE but a second copy of the precedence rule — and
    # a second copy is what #10067 exists to end (#10067 review P1: the producer
    # and the seam disagreed and the transcript went to neither store).
    resolved = record_tenant_effective(tenant)
    if resolved is not None and not (
            isinstance(resolved, tuple) and len(resolved) == 2
            and all(isinstance(part, str) and part for part in resolved)):
        # The seam's ONLY injection point must not be its weakest carrier: an
        # empty/half pair would be unpacked straight into `write_records` and
        # file the row under a tenant nobody named (#9188 review F4).
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

    * ``journal_configured`` — the turns reach the record ONLY through the
      per-turn `PointAdded` records (`_write_capture_turns` emits them when the
      SDK has a journal), so without one the record cannot be where they are,
      whatever its DSN says. ``durable`` is the same fact one step in: a scratch
      buffer is not a journal and the seam declines it.
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
    if reason == "no_durable_journal":
        # A DECLINE, not a loss: this lane has no journal by design, so no row is
        # missing that a replay could restore (#9231 review P2). Counted on the
        # skip counter and logged at DEBUG — a warning per extracted record on
        # the healthy M2 capture path is the same noise the counter was.
        _count_non_journal_skip("no_durable_journal")
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


def write_live_record(record: Mapping[str, Any], *,
                      durable: bool = True,
                      seq: int | None = None,
                      tenant: tuple[str, str] | None = None,
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
    must not fail a write that persisted. ``replay`` heals the gap. That
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
    it, and it is closed on every path.

    Cost, measured at #9188's head: an armed record costs ~one connection +
    commit and ~4 ms of plan materialization (the fold re-reads the record
    migrations to build its column map — `table_columns()` is deliberately
    uncached, so a migration edit can never be read stale). That is a
    DELIBERATE residual: one record, one transaction, no batching, because a
    batch seam's ordering across three independent doors is a throughput
    question with no measurement behind it yet. The UNHEALTHY case is bounded
    too, in two places that answer different questions: opening the connection
    is capped by :data:`CONNECT_TIMEOUT_SECONDS` (~10 s), and a connect that
    FAILS trips :data:`LIVE_WRITE_BREAKER_SECONDS` so the REST of the capture
    is refused locally (counted ``circuit_open``) instead of paying that
    timeout once per record — N records used to cost N × 10 s inside one
    ``capture_session`` call, which is a wedged capture, not a degraded one.

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
    their records from 1, so the second journal's rows are absorbed by
    `ON CONFLICT (org_id, graph_id, seq) DO NOTHING` and the existing row keeps
    the OTHER journal's `event_type`/`payload` while the layer row underneath
    belongs to a different record. That was reachable before this change only
    replay-vs-replay; the live path now participates, so it is stated here
    rather than left to be rediscovered (#9307 review P2 — tracked on #9318).
    The fix is journal IDENTITY in the key (a column identifying the
    journal, or a per-tenant ordinal allocator), not a number this door can
    derive — do NOT paper it over by declining position rows on the live path,
    which is the #9190 defect this door exists to close.

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
    org_id, graph_id = tenant
    connection: Any | None = None
    if seq is None:
        # A DURABLE journal whose append could not prove the record's position
        # (see `seq` above): layer 1 gets no row for this record, which is a
        # NAMED skip and never a silent one (#9307 review F1).
        _note_unproven_position(record)
    try:
        # #9190's ordinal, straight from the append. `position=False` only when
        # a durable journal could not PROVE the record's position (see `seq`): a
        # row needs a number, and inventing one collides with a later replay's
        # numbering. Counted and warned above, so the skip is observable.
        write = write_records([record], org_id=org_id, graph_id=graph_id,
                              position=seq is not None,
                              first_seq=seq if seq is not None else 1)
        _close_live_breaker()
        connection = write.connection
        if connection is not None:
            connection.commit()
        if seq is None:
            # No position was consumed, so the watermark must not report one
            # as if it had been (a caller advancing `last_seq + 1` must neither
            # rewind nor skip): the seam was handed the default `first_seq=1`
            # for a row-less write, and `first_seq - 1` is this class's own
            # spelling for "nothing consumed".
            return replace(write, last_seq=write.first_seq - 1)
        return write
    except Exception as exc:
        if isinstance(exc, RecordConnectError):
            # Unreachable RIGHT NOW (not a refusal of this one record): bound the
            # cost of the rest of the capture. A totality or statement failure
            # deliberately does NOT trip it — the connection was fine.
            _trip_live_breaker()
        _count_write_failure("write_failed")
        _logger.warning(
            "the record write did not happen (write_failed) — the journal has "
            "the record and a replay heals the record: %s", exc,
        )
        return _refused("write_failed", seq)
    finally:
        if connection is not None:
            # Close AFTER the commit so an armed deployment does not leak one
            # backend slot per journal record; a close failure must not mask a
            # write failure that is already being reported above.
            with contextlib.suppress(Exception):
                connection.close()
