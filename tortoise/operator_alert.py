"""Operator alerts for failures we absorb (#3981) — the incident channel.

The #3981 ruling (2026-09-18, "PROCEED AND ALERT") requires three things: the
request proceeds, the increment is recorded as unmeterable, and an OPERATOR
ALERT FIRES. This module is the third leg: it submits an absorbed failure to the repo's
existing dual-channel sink (:class:`tortoise.alert_store.AlertStore` — GitHub
issue + Telegram, create-once dedup per (kind, subject)) through a bounded
thread pool (`_POOL`), so the filing (network) work runs off the caller's thread.

Design:
- The reporters run on the request path (some inline on the event loop) and a
  broken anchor drops an increment on EVERY write, so the filing is
  repeat-suppressed per (kind, org) and dispatched to a small bounded pool.
- The store is resolved on the CALLER's thread and passed into the worker.
  Resolution is env-only (no network). Resolving it inside the worker would run
  after the caller's monkeypatch was undone — bypassing the suite-wide alert
  isolation and able to file a real issue.
- :func:`alert_store` resolves through ``tortoise.alert_channel``, which does
  NOT import ``tortoise.hosted_api``: this module fires from the MCP stdio path,
  where importing the hosted app builds the whole FastAPI tree (~1.7 s,
  ``tortoise/mcp_server.py:31-35``) for a bookkeeping alert.
- The dispatcher mirrors ``mcp_server._emit_mcp_tool_call_telemetry``
  (``tortoise/mcp_server.py:177``; join seam ``_flush_mcp_telemetry``, ``:242``) — off-loop, tracked
  handles, a join seam — with a bounded pool for an alert: an unbounded thread per dropped increment
  is the storm this exists to avoid.

Nothing here changes request semantics: the callers already absorb the failure
and serve the request.
"""

from __future__ import annotations

import atexit
import collections
import concurrent.futures
import contextlib
import logging
import sys
import threading
import time
from typing import Any

_logger = logging.getLogger("tortoise.operator_alert")

#: The incident KIND for a dropped metering increment (#3981). Declared HERE,
#: not in ``tortoise.metering``, because the import-guard fallbacks that most
#: need it exist precisely because importing ``tortoise.metering`` FAILED — a
#: constant living there is unreachable exactly where it is used, and the
#: guarded import would swallow the ``NameError`` into a silent drop. The kind
#: string IS the R2 dedup key (``ops/alerts/{kind}/{org}.json``), so there is
#: one definition and the runbook row is pinned to it by
#: ``tests/test_operator_alert.py::test_kind_constants_match_the_runbook``.
UNMETERED_INCREMENT_KIND = "UNMETERED_INCREMENT"

#: The incident KIND for a Stripe billing notification the #3498 offload seam
#: REFUSED (#4456). The event is already CLAIMED when the notify is submitted
#: (the ``WebhookEvent`` marker commits BEFORE it), so a Stripe retry sees
#: ``is_first=False`` and the notification is **lost permanently** — the org
#: is never told its plan changed. The seam's public ``refused``
#: discriminator distinguishes this real drop from a plain bound miss (where
#: the worker still completes the send), and this is the escalation path.
BILLING_NOTIFY_REFUSED_KIND = "BILLING_NOTIFY_REFUSED"

#: Repeat-suppression windows (seconds). A recorded incident holds the long
#: window; an attempt that recorded NOTHING re-arms on the short one, so a
#: transient channel outage delays the first alert by at most a minute. The
#: condition is persistent and AlertStore already dedups the INCIDENT, so this
#: suppresses ATTEMPTS — it is not a persistence gate.
_ALERT_WINDOW_S = 900.0
_RETRY_WINDOW_S = 60.0
#: A worker that outlives this is assumed wedged. The reservation it holds is
#: released at the sites listed on ``_RESERVED``.
_INFLIGHT_STALE_S = 120.0
#: Shed-warning rate limit. The shed path runs on the CALLER's path (some reporters
#: are inline on the event loop), and the bound is saturated exactly during a
#: sweep-scale outage — so one WARNING per dropped increment would make the alert
#: mechanism amplify the storm it exists to report. One line per interval is enough
#: to see it happening; the suppressed count is deliberately not carried, because the
#: log budget must not grow with the outage either.
_SHED_LOG_INTERVAL_S = 60.0
#: ``None`` = "never logged". NOT ``0.0``: ``time.monotonic()``'s reference point is
#: explicitly undefined, so a clock reporting < 60 s (a fresh boot, a per-process
#: monotonic clock) would make ``now - 0.0 < interval`` true and suppress EVERY shed
#: warning until the clock passed the interval — a fail-open in the one signal this
#: module exists to surface, and a deterministic failure of the tests that assert it.
_LAST_SHED_LOG: float | None = None
#: Global dispatch bound: a sweep-scale outage drops for many orgs at once.
_MAX_INFLIGHT = 32
#: Map bounds: keys outside the windows are pruned; a hard cap evicts oldest.
_MAX_KEYS = 1024
_PRUNE_ABOVE = 256

_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="operator-alert")

_LOCK = threading.Lock()
#: Least-recently-attempted first: eviction must not reset the throttle of the
#: persistently-failing keys a sweep-scale outage produces while keeping cold keys.
_ATTEMPT: collections.OrderedDict[tuple[str, str], tuple[float, float]] = collections.OrderedDict()
_INFLIGHT: dict[tuple[str, str], float] = {}                # key -> attempt token
#: ADMITTED-but-unsettled dispatches. Taken under _LOCK at admission and released at
#: these sites: `_run`'s `finally` once a started dispatch settles, `_forget` when a
#: future was cancelled before `_run` started (the done-callback `alert_operator`
#: registers), and `alert_operator`'s two pre-pool failure paths (no store, rejected
#: submit). `_reap_locked` does not decrement it. The gate is `_RESERVED >=
#: _MAX_INFLIGHT`.
_RESERVED = 0
#: future -> submitted ts. Used by `join_operator_alerts` and by _reap_locked for
#: dead-handle housekeeping — NOT by the shed gate (that reads _RESERVED).
_HANDLES: dict[Any, float] = {}
#: The full-map sweep is amortised housekeeping, not the gate: the admission decision
#: is O(1) (`_due_locked` + `_RESERVED`), so the O(n) scan runs once per _SWEEP_EVERY
#: admissions (and whenever _ATTEMPT exceeds _PRUNE_ABOVE) instead of on every
#: hot-path write. It prunes `_ATTEMPT` and ages dead handles out of `_HANDLES`; it
#: does not touch `_INFLIGHT`.
_SWEEP_EVERY = 64
_SINCE_SWEEP = 0


def _shutdown_pool() -> None:
    """Drop queued alerts at exit — LOUDLY. cancel_futures discards work with no
    log and no incident, which is the silent-drop class this module exists to
    remove; at least leaving a WARNING makes the loss visible in the shutdown tail
    (the transports are 15s-bounded, so the pending set is normally tiny)."""
    with _LOCK:
        # Not a drop/running split — cancel_futures only cancels the QUEUED ones;
        # the running ones are abandoned to interpreter exit. Say that, not "dropped".
        pending = sum(1 for f in _HANDLES if not f.done())
    if pending:
        _logger.warning(
            "operator alerts unsettled at shutdown (pending=%d; queued cancelled, "
            "running abandoned)", pending)
    _POOL.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_pool)


def alert_store():
    """The shared UNGATED alert-channel builder, or ``None``.

    Gated on the alert credentials only, never on ``BACKUP_SWEEP_ENABLED``
    (#3981, #3820 D5a). Tests patch THIS name to inject a fake.

    PREFERS the hosted leg when ``tortoise.hosted_api`` is ALREADY imported:
    that leg runs the identical policy and constructor (both live in
    ``tortoise.alert_channel``) but injects the hosted module's own
    ``_backup_storage`` — the process-wide R2 singleton (#3968). The light
    default (:func:`alert_channel.light_storage`) builds a FRESH ``R2Storage``,
    and therefore a fresh boto3 client, per call: correct for the
    once-per-window alert this module dispatches, wrong for a caller that asks
    per request (``cohort_cost._alert_store`` runs on every cap-firing capture).
    In a hosted process both callers build through
    ``hosted_api._incident_alert_store``, and patching THIS function redirects
    both.

    It does not import ``tortoise.hosted_api``: this runs on the MCP stdio path
    for a dropped increment, and importing the hosted app builds the whole
    FastAPI tree (~1.7 s, ``tortoise/mcp_server.py:31-35``). When another importer
    has already loaded it, the hosted branch above answers; otherwise the light
    leg answers and ``tortoise.hosted_api`` stays out of ``sys.modules``
    (pinned by ``tests/test_operator_alert.py::test_the_light_leg_never_imports_the_hosted_app``).
    """
    ha = sys.modules.get("tortoise.hosted_api")
    if ha is not None:
        return ha._incident_alert_store()
    from tortoise import alert_channel

    return alert_channel.incident_alert_store()


def alert_unmetered_increment(lane: str, org_id: str | None,
                              error: BaseException) -> None:
    """Dispatch the ``UNMETERED_INCREMENT`` incident. Never raises.

    The entry point for a dropped increment: the reporter in ``tortoise.metering``
    and the import-guard fallbacks (``hosted_api``, ``mcp_server``, ``ask_lane``)
    call THIS, so the kind string and the detail vocabulary are written once.
    Importable without ``tortoise.metering`` — the fallbacks run when that import
    failed.
    """
    with contextlib.suppress(Exception):  # the alert must never raise
        alert_operator(UNMETERED_INCREMENT_KIND, org_id,
                       {"lane": lane, "error_type": type(error).__name__})


def alert_billing_notify_refused(org_id: str | None,
                                 event_type: str | None = None) -> None:
    """Dispatch the ``BILLING_NOTIFY_REFUSED`` incident. Never raises.

    Fired by the Stripe webhook when the #3498 offload seam REFUSED the
    billing notification (#4456): the event is already claimed, so this real
    drop is unrecoverable and must not be silent. Detail is a fixed,
    message-free vocabulary (the incident body is durable) — ``op`` names the
    seam site, ``event_type`` the Stripe event type, ``org_id`` the affected
    org; no free text.

    The incident SUBJECT is PLATFORM-SCOPED (``""``) by the #4456 plan: ONE
    Resend account serves every team, so a per-org key would file N issues for
    ONE outage (the same reason ``notify.py`` passes ``""`` for a failed
    billing send). Dedup is ``(kind, subject)``; the affected org still
    travels in the detail. The shared telemetry pool makes such a refusal
    CROSS-TENANT — one saturation refuses a notify per billing webhook for
    every tenant — so per-org keying would amplify the outage it reports.
    """
    with contextlib.suppress(Exception):  # the alert must never raise
        alert_operator(BILLING_NOTIFY_REFUSED_KIND, "",
                       {"op": "billing_notify", "event_type": event_type,
                        "org_id": org_id or "?"})


def file_operator_incident(store, kind: str, org_id: str | None, detail: dict) -> bool:
    """Open (or re-use) the incident; True iff one is ON RECORD. Never raises.

    ``open_incident_state`` — NOT ``open_incident``: that bool is True only for
    a FILED call, so a DEDUP hit (an incident IS on record) would read as "not
    on record" and re-arm the short window. ``store`` is resolved by the caller.

    The on-record rule (``outcome in {FILED, DEDUP}``, i.e. ``is not SUPPRESSED``)
    is ALSO implemented by ``hosted_api._analytics_open_incident``, and
    ``tests/test_operator_alert.py::test_on_record_predicate_parity`` pins the two
    equal over every :class:`OpenOutcome` member. Extracting one shared helper is
    deferred (filed as a follow-up).
    """
    if store is None:
        return False
    try:
        from tortoise.alert_store import OpenOutcome
        return store.open_incident_state(kind, org_id or "", dict(detail)) in (
            OpenOutcome.FILED, OpenOutcome.DEDUP)
    except Exception:
        _logger.warning("operator incident filing failed (kind=%s)", kind,
                        exc_info=True)
        return False


def _reap_locked(now: float) -> None:
    """Drop dead handles past the stale bound — map housekeeping, not the gate.

    Does not touch ``_RESERVED``.
    """
    for f, ts in list(_HANDLES.items()):
        if now - ts > _INFLIGHT_STALE_S:
            _HANDLES.pop(f, None)


def _release_locked() -> None:
    """Release one admission reservation (caller holds ``_LOCK``); floors at 0."""
    global _RESERVED
    if _RESERVED > 0:
        _RESERVED -= 1


def _prune_locked(now: float) -> None:
    """Amortised map housekeeping — not the admission decision (see _RESERVED).

    Prunes ``_ATTEMPT`` and reaps dead handles. Does not touch ``_INFLIGHT`` or
    ``_RESERVED``.
    """
    if len(_ATTEMPT) > _PRUNE_ABOVE:
        for k, (ts, window) in list(_ATTEMPT.items()):
            if now - ts >= window:
                _ATTEMPT.pop(k, None)
        while len(_ATTEMPT) > _MAX_KEYS:
            _ATTEMPT.popitem(last=False)          # least-recently-attempted
    _reap_locked(now)


def _log_shed(now: float, kind: str) -> None:
    """Rate-limited shed warning — at most one line per ``_SHED_LOG_INTERVAL_S``.

    The shed path runs on the CALLER's path, and the bound saturates exactly during a
    sweep-scale outage, so one WARNING per dropped increment would make the alert
    mechanism amplify the storm it exists to report. The suppressed count is
    deliberately not carried: the log budget must not grow with the outage either.
    """
    global _LAST_SHED_LOG
    with _LOCK:
        if (_LAST_SHED_LOG is not None
                and now - _LAST_SHED_LOG < _SHED_LOG_INTERVAL_S):
            return
        _LAST_SHED_LOG = now
    _logger.warning("operator alert shed — dispatch queue full (kind=%s)", kind)


def _due_locked(key: tuple[str, str], now: float) -> bool:
    """Is this key due? Checks the ``_ATTEMPT`` window first; the stale-latch pop
    sits on the path that returns ``True``.
    """
    last = _ATTEMPT.get(key)
    if last is not None and now - last[0] < last[1]:
        return False                      # throttled — leave the latch alone
    started = _INFLIGHT.get(key)
    if started is not None:
        if now - started <= _INFLIGHT_STALE_S:
            return False
        _INFLIGHT.pop(key, None)          # wedged worker self-heals; an admit follows
    return True


def _run(store, key, kind, org_id, detail, token) -> None:
    try:
        # Ownership check BEFORE the store write: a superseded attempt does not
        # spend a network call filing an incident its successor may already be
        # filing, and does not reach a store that may be tearing down. A kind paused
        # in ``ops/suppression.json`` returns ``SUPPRESSED`` with no line, by design
        # (``AlertStore.open_incident_state``). The check is repeated below because a
        # newer attempt can start while this one is in flight.
        with _LOCK:
            if _INFLIGHT.get(key) != token:
                return
        try:
            on_record = file_operator_incident(store, kind, org_id, detail)
        except Exception:
            on_record = False
        with _LOCK:
            if _INFLIGHT.get(key) != token:
                return                    # a newer attempt owns the state
            _INFLIGHT.pop(key, None)
            _ATTEMPT[key] = (time.monotonic(),
                             _ALERT_WINDOW_S if on_record else _RETRY_WINDOW_S)
            _ATTEMPT.move_to_end(key)
    finally:
        # Release the admission reservation HERE, not in the done-callback:
        # Future.set_result notifies waiters BEFORE invoking done callbacks, so a
        # callback release races the joining thread and makes `join → _RESERVED == 0`
        # flaky. `finally` runs before the future is marked done.
        with _LOCK:
            _release_locked()


def _forget(fut) -> None:
    """Drop one dispatch's handle; release its reservation if the future was
    cancelled before ``_run`` started.

    A started future releases its reservation in ``_run``'s ``finally``. The
    cancellation is logged here: a queued alert aged out of ``_HANDLES`` is not
    visible to ``_shutdown_pool``'s pending-handle count. The line states what is
    observed (cancelled before running); the cause — pool shutdown, a test's
    cancelling pool, ``cancel_futures`` — is not available here.
    """
    with _LOCK:
        _HANDLES.pop(fut, None)
        if fut.cancelled():
            _release_locked()
            _logger.warning("operator alert cancelled before it ran")


def alert_operator(kind: str, org_id: str | None, detail: dict | None = None):
    """Fire-and-forget operator alert; returns the handle or None. Never raises."""
    global _SINCE_SWEEP, _RESERVED
    key = (kind, org_id or "")
    now = time.monotonic()
    shed = False
    with _LOCK:
        _SINCE_SWEEP += 1
        if _SINCE_SWEEP >= _SWEEP_EVERY:
            _SINCE_SWEEP = 0
            _prune_locked(now)
        # The admission bound is decided here, before `_due_locked` may pop
        # `_INFLIGHT[key]`.
        if _RESERVED >= _MAX_INFLIGHT:
            # A shed is not an attempt: leave `_ATTEMPT` untouched, or a later
            # `_due_locked` would read the window this call never consumed.
            shed = True                          # bounded: shed, keep throttled
        elif not _due_locked(key, now):
            return None
        else:
            _ATTEMPT[key] = (now, _RETRY_WINDOW_S)   # provisional; _run re-arms
            _ATTEMPT.move_to_end(key)
            token = now
            _RESERVED += 1
            _INFLIGHT[key] = token
    if shed:
        _log_shed(now, kind)
        return None
    try:
        store = alert_store()                    # CALLER thread — deterministic
    except Exception:
        store = None
    if store is None:
        with _LOCK:
            # Token-guarded: `alert_store()` ran outside the lock, so a same-key
            # dispatch may have replaced the latch in the meantime. Release our own
            # reservation.
            if _INFLIGHT.get(key) == token:
                _INFLIGHT.pop(key, None)
            _release_locked()
        _logger.warning("operator alert not filed — no alert channel (kind=%s)", kind)
        return None
    try:
        fut = _POOL.submit(_run, store, key, kind, org_id, dict(detail or {}), token)
    except Exception:  # pool shutting down — never drop the alert silently
        with _LOCK:
            if _INFLIGHT.get(key) == token:  # token-guarded
                _INFLIGHT.pop(key, None)
            _release_locked()
        _logger.warning("operator alert dispatch failed (kind=%s)", kind,
                        exc_info=True)
        return None
    # Register the settle callback before recording the handle, and record the handle
    # only while the future is unsettled (both under _LOCK).
    fut.add_done_callback(_forget)
    with _LOCK:
        if not fut.done():
            _HANDLES[fut] = time.monotonic()
    return fut


def join_operator_alerts(timeout: float = 5.0) -> int:
    """Wait up to *timeout* for in-flight dispatches; returns the UNSETTLED count.

    ``0`` means every tracked dispatch settled within the timeout. A handle aged past
    ``_INFLIGHT_STALE_S`` is dropped from ``_HANDLES``, so a wedged worker can be
    untracked here while still holding its pool thread and its reservation. A
    timed-out handle stays tracked. Never raises.
    """
    with _LOCK:
        handles = list(_HANDLES)
    deadline = time.monotonic() + timeout
    unsettled = 0
    for h in handles:
        try:
            h.result(max(0.0, deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            unsettled += 1
            continue
        except Exception:
            pass
        with _LOCK:
            _HANDLES.pop(h, None)
    return unsettled


def reset_operator_alert_state_for_tests() -> None:
    """Clear ALL process-local state (test seam) — throttle, latch, bound, sweep.

    ``_RESERVED`` and ``_HANDLES`` are reset WITH the maps: a reservation left by
    a test that wedged a worker would silently shrink the budget every later test
    sees, and a stale handle would be joined (or counted) by the next test.
    ``_SINCE_SWEEP`` is reset so a test that pins the amortised sweep is not
    dependent on how many admissions earlier tests happened to make.
    """
    global _RESERVED, _SINCE_SWEEP, _LAST_SHED_LOG
    with _LOCK:
        _ATTEMPT.clear()
        _INFLIGHT.clear()
        _HANDLES.clear()
        _RESERVED = 0
        _SINCE_SWEEP = 0
        # Also the shed-log rate limit, or the first test to shed would silence the
        # warning for every later test inside _SHED_LOG_INTERVAL_S.
        _LAST_SHED_LOG = None
