"""#3665 (lane B7) — the cohort cost cap: a spend ceiling that stops work.

WHAT THIS IS
------------
A **pre-spend admission gate** for the hosted capture path. When the measured
LLM spend of the caller's COHORT has reached the configured ceiling, a capture
is refused **before the capture's own writes** — 402 on REST, ``ERR_QUOTA``
(``-32006``) on MCP, plus an :class:`~tortoise.alert_store.AlertStore`
incident so the firing is observable.

The refusal is not literally zero-write, and this docstring does not claim it:
returning 402 records the per-harness ``session_capture_last_error_*``
onboarding key (the hosted wrapper's non-2xx bookkeeping, the same as every
other refusal), and the observability sink files an incident. What is true —
and is what matters — is that **no capture data is written**: no Session MERGE,
no turn Points, no ``capture_ok``, no receipt, so the transcript is untouched
and retryable.

WHY AT ADMISSION, AND WHY THAT IS SAFE
--------------------------------------
The extraction is inline and synchronous in the request (``hosted_api``
``_capture_session_impl`` awaits it), and a fail-closed 402 admission block
already sits before the turn-write loop and both extraction calls. Refusing
there means:

* no Session MERGE, no turn Points, no ``capture_ok``, no receipt;
* the transcript is untouched on the user's machine and is retryable
  verbatim — a re-POST of a session that never landed is a normal first-time
  extraction, because only a session WITH ``capture_ok`` is a replay (a
  ``retry_failed_capture`` session does exist, but its re-POST re-runs
  extraction by design, #2335 WI-2b);
* identical behaviour on REST and MCP, because both share the one impl.

The rejected alternatives are rejected for data-loss reasons, not taste: a
stop *after* the turn loop leaves ``capture_ok`` NULL, and the next
same-``session_id`` POST is then served as a no-op replay (200, 0 extracted) —
permanent, silent un-extraction. That hazard is documented on
``hosted_api._reserve_capture_slot`` and is why capacity is also rejected at
admission.

WHAT A COHORT IS
----------------
There is no cohort column anywhere, and the only FK-enforced key to spend is
``organizations.id``. So a cohort is a **set of org ids bucketed by
``organizations.created_at``** — the signup-wave definition, and the same
idiom the existing signup rate limiter uses
(``supabase_control.membership_count_since``).

WHAT IT READS — AND WHAT IT HONESTLY DOES NOT BOUND
---------------------------------------------------
The ledger is ``metering_records`` (PK ``(org_id, period_start)``): the ask
lane's ``ask_cost_usd`` (#1987) plus the capture lane's ``capture_cost_usd``
(measured by #3359, put on the ledger by migration 20260917000001, re-keyed to
the period window by 20260918000001). The aggregate is one row per org per
metered window, not one per capture — which is why this read can run on every
admission without a cache (a cache would weaken the bound by its TTL; #3665
trade-off 2, decided explicitly).

**The window is the REQUESTING org's metering window** (#3825 / D10): the
subscription's own billing period, or — for an org with no subscription — the
calendar month in UTC (D13). A cohort is a set of orgs whose subscriptions may
carry DIFFERENT anchors, so "the cohort's spend this window" is only
well-defined per ledger row; the reader therefore applies an OVERLAP test and
an overlapping row is counted in full. Over-reading can only fire the ceiling
EARLIER; the alternative (rows that START inside the window) under-reads every
org whose period began earlier, and on a spend ceiling an under-read is
fail-OPEN.

**This bounds REQUESTS, not dollars.** A pre-spend check reads spend *already
recorded*; captures already in flight can still spend after they passed the
gate. The overshoot is bounded by ``_CAPTURE_MAX_IN_FLIGHT`` (default 8) x one
extraction's worst case. Closing it needs a reservation (reserve an estimated
cost at admission, reconcile on completion) — the ledger and its atomic
increment RPC are the substrate for that. Stated here so it is never read as
a hard dollar bound.

**An unmeasured capture prices as $0.00.** ``capture_cost_usd`` is the
provider's *reported* charge (the #3359 measurement). When the provider
reports none — the analytics row's ``calls_without_cost`` disclosure — or a
generation is deadline-killed before it can be priced (``deadline_aborts``),
the recorded cost is 0.0, so the ceiling UNDER-reads by exactly those calls
and can fire later than a perfect measurement would. The disclosure counters
are on the analytics row, not on this ledger; carrying them onto the ledger
(and surfacing unpriced calls in the trip detail) is the follow-up that would
close it. Stated here so a cohort whose measurement failed is never read as a
cheap cohort.

**It is not armed by default.** ``TORTOISE_COHORT_COST_CAP_USD`` unset means
the gate is a no-op; the machinery ships enabled-off so arming is an explicit
ops decision. ARM ORDER MATTERS — and BOTH levers are required: apply migration
20260917000001 (the ``capture_cost_usd`` column and its increment RPC) BEFORE
setting the cap, and set ``TORTOISE_COHORT_COST_SINCE`` WITH it. The cap alone
is a fail-closed configuration error (an unscoped cap would apply to every
org; a malformed ``since`` would select an empty cohort and disarm the cap),
so setting one without the other 500s the admission block rather than arming
something that bounds nothing.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime

from tortoise.quota import QuotaCheckError, QuotaExceededError

_logger = logging.getLogger("tortoise.cohort_cost")

# Ops levers. Both are read per request (a cap can be armed or lifted without
# a redeploy of the gate logic).
CAP_ENV = "TORTOISE_COHORT_COST_CAP_USD"
SINCE_ENV = "TORTOISE_COHORT_COST_SINCE"

# The AlertStore incident kind — deduped by AlertStore on (kind, subject), so
# a cohort that keeps tripping opens ONE incident, not one per capture.
INCIDENT_KIND = "COHORT_COST_CAP"

# The SEPARATE kind for a cap that could not be evaluated at all (#3981).
# Deliberately NOT a superstring of ``INCIDENT_KIND``: the R2-unreachable
# adoption path resolves an existing incident by GitHub search on the org
# suffix, so a name that contains the other would let the two be confused for
# one another. Pinned to its runbook row by
# ``tests/test_operator_alert.py::test_kind_constants_match_the_runbook``.
UNENFORCEABLE_INCIDENT_KIND = "COHORT_CAP_UNENFORCEABLE"

# Bounds on the cohort we are willing to PRICE. Both control-plane reads are
# server-side aggregates that return a SINGLE row — ``cohort_org_ids_since``
# (``array_agg``) and ``metering_cohort_spend`` (``sum``) — so PostgREST's
# ``db-max-rows`` response cap cannot silently truncate either one. That was
# not true of the first revision of this lane, which read both as filtered row
# lists and defended them with a row-count guard that a short read passes
# (it returns FEWER rows, and the (org_id, period) PK makes an over-return
# impossible — a dead check). The bound is now a plain policy limit on how
# large a cohort the cap will gate, and an oversize cohort is refused
# (QuotaCheckError → 500), never partially priced. ~10-50 orgs is the beta
# population this cap is written for.
_MAX_COHORT_ORGS = 500

# #4614: the machine-readable CATEGORY of a cohort-spend refusal. Distinct from
# `quota.QUOTA_REFUSAL_CODE` on purpose — the cap is a SPEND ceiling, not a
# plan node cap, and a client that branches on `detail.code` must not send the
# user to buy a bigger plan that cannot lift it.
COHORT_COST_REFUSAL_CODE = "cohort_cost_cap"


class CohortCostCapExceeded(QuotaExceededError):
    """The cohort's measured spend is at/over the cap (#3665).

    A ``QuotaExceededError`` subclass on purpose: the existing fail-closed 402
    contract (``hosted_api._check_org_limit``, MCP's ``ERR_QUOTA`` mapping)
    then covers the cohort cap with **no new mechanism** — which is the whole
    point of reusing ``quota.enforce_org_limit``'s error pair. ``detail``
    carries the cohort-scoped incident payload for the AlertStore sink.

    #4614: it overrides the refusal CATEGORY. A spent cohort budget and a
    hit plan cap are both 402s, and a caller that branches on
    ``detail.code`` must be able to tell them apart — reading a spend cap as
    "you are out of plan allowance" would send the user to buy a bigger plan
    that does not lift it.
    """

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message, code=COHORT_COST_REFUSAL_CODE)
        self.incident_detail = dict(detail or {})


@dataclass(frozen=True)
class CohortCostCap:
    """A resolved cap: ``cap_usd`` over the cohort created on/after ``since``."""
    cap_usd: float
    since: str


def resolve_cohort_cost_cap(env: dict | None = None) -> CohortCostCap | None:
    """Resolve the cap from the environment. ``None`` → the gate is off.

    FAIL-CLOSED on a malformed cap: **only an ABSENT key disables the gate.** A
    present-but-unusable value — unparseable text, ``0``, a negative, a
    whitespace-only string, or a NON-FINITE float (``nan``/``inf``/``1e999``) —
    raises :class:`QuotaCheckError` (→ 500 at the REST adapter) rather than
    being silently treated as "off". A value someone believes is arming the
    ceiling (or emergency-stopping it) while actually disarming it would be
    exactly the "configured but not enforced" false PASS this lane exists to
    prevent; a loud 500 is visible and fixable. To turn the cap off, REMOVE the
    variable.

    ``float()`` accepts ``nan``/``inf`` and neither is ``<= 0``, so a bare
    sign check would let either through and the comparison ``spent >= cap``
    would be permanently False — the ceiling silently gone. ``math.isfinite``
    is the guard (hosted_api.py's env-int readers record the same trap for
    ``TORTOISE_HEALTH_PROBE_INTERVAL``).
    """
    env = os.environ if env is None else env
    if CAP_ENV not in env:
        return None  # absent → off (the default posture)
    raw = (env.get(CAP_ENV) or "").strip()
    try:
        cap_usd = float(raw)
    except ValueError:
        raise QuotaCheckError(
            f"{CAP_ENV} is not a number ({raw!r}) — the cohort cost cap "
            f"cannot be evaluated, refusing rather than silently disarming "
            f"(remove the variable to disable the cap)"
        ) from None
    if not math.isfinite(cap_usd) or cap_usd <= 0:
        raise QuotaCheckError(
            f"{CAP_ENV} is {raw!r} — a non-positive or non-finite cap cannot "
            f"bound anything, refusing rather than silently disarming the "
            f"ceiling (remove the variable to disable the cap)"
        )
    since = (env.get(SINCE_ENV) or "").strip()
    if not since:
        raise QuotaCheckError(
            f"{CAP_ENV} is set but {SINCE_ENV} (the cohort start, ISO-8601) "
            f"is not — an unscoped cap would apply to every org"
        )
    return CohortCostCap(cap_usd=cap_usd, since=_normalise_since(since))


def _normalise_since(raw: str) -> str:
    """Validate and canonicalise the cohort start as ISO-8601 UTC.

    FAIL-CLOSED on anything that is not an unambiguous instant. The registry
    lane compares ``o.created_at > $since`` as STRINGS, so a value that is not
    ISO-8601 orders lexicographically and can match no org at all: the cohort
    resolves empty, every org then reads as "outside the cohort", and the cap
    is silently disarmed with no error and no incident. (The Supabase lane
    compares ``timestamptz`` server-side — ``cohort_org_ids_since`` — but the
    two lanes must not disagree on the same misconfiguration.) Canonicalising
    also makes ``Z`` vs ``+00:00`` and fractional precision compare correctly.
    """
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise QuotaCheckError(
            f"{SINCE_ENV} is not ISO-8601 ({raw!r}) — a cohort start that "
            f"does not parse would select an empty cohort and silently "
            f"disarm the cap; refusing (fail-closed)"
        ) from None
    if parsed.tzinfo is None:
        raise QuotaCheckError(
            f"{SINCE_ENV} has no UTC offset ({raw!r}) — the cohort start is "
            f"ambiguous; refusing (fail-closed)"
        )
    return parsed.astimezone(UTC).isoformat()


# ``current_period()`` USED TO LIVE HERE — a SECOND, independent calendar-month
# implementation, duplicating ``metering._current_period``. It was the THIRD
# month producer in the codebase (after ``metering``'s own resolver and #3780's
# capture lane), and #3825/D14 requires it to carry the window migration rather
# than survive it. It is now DELETED, not re-pointed: two producers of the
# ledger's row key can disagree, and when they do the cap sums a window nobody
# wrote to and reads the cohort as FREE — the false PASS this lane exists to
# prevent. The ONE producer is ``metering._current_period(org_id)``, which
# resolves the subscription anchor (D10) and falls back to the calendar month
# in UTC only when there is no subscription at all (D13).
#
# ``next_period_start_iso()`` was deleted with it: the reset instant is the
# resolved window's OWN ``end`` — a month boundary is not where a subscription
# period ends.


def cohort_org_ids(since: str) -> list[str]:
    """The cohort: org ids with ``created_at > since``.

    Supabase mode reads the ``organizations`` table (the source of truth
    post-#669); otherwise the registry twin, matching a bare ``:Organization``
    (production/URI lane) or ``:Team`` (the embedded lane's label).

    BOUNDED: at most ``_MAX_COHORT_ORGS`` orgs are priced. A larger cohort
    raises (fail-closed) rather than gating on a partial org set — dropped
    orgs would read as outside the cohort and disarm the cap for exactly
    those orgs.

    FAIL-CLOSED: a resolution failure raises ``QuotaCheckError`` — an
    unresolvable cohort must not read as "this org is outside the cohort",
    which would silently disarm the cap.
    """
    from tortoise.supabase_control import (
        cohort_org_ids_since,
        get_control_plane,
        is_supabase_enabled,
    )
    try:
        if is_supabase_enabled():
            ids = cohort_org_ids_since(
                get_control_plane(), since, _MAX_COHORT_ORGS)
        else:
            from tortoise.metering import _reg_sdk
            rows = _reg_sdk()._get_registry().query(
                "MATCH (o) WHERE (o:Organization OR o:Team) "
                "AND o.created_at > $since RETURN o.id",
                params={"since": since},
            ).result_set
            ids = [str(r[0]) for r in rows if r[0]]
    except QuotaCheckError:
        raise
    except Exception as e:
        raise QuotaCheckError(
            f"cohort resolution failed (created_at > {since}): {e}"
        ) from e
    if len(ids) > _MAX_COHORT_ORGS:
        raise QuotaCheckError(
            f"cohort is larger than the {_MAX_COHORT_ORGS}-org bound this cap "
            f"will gate ({len(ids)} orgs) — refusing to evaluate the ceiling "
            f"over a partial cohort (fail-closed)"
        )
    return ids


def _alert_store():
    """The AlertStore, or ``None`` when no alert channel is configured.

    Delegates through ``operator_alert.alert_store`` — the shared
    ALERT-only, sweep-independent seam (#3981; #3820 D5a class). The
    pre-existing gating on ``_backup_config_safe()`` left the money-capping
    alert invisible by default, because that helper is ``None`` whenever
    ``BACKUP_SWEEP_ENABLED`` is off. Indirection kept callable so tests can
    substitute a real AlertStore over fake transport.
    """
    from tortoise.operator_alert import alert_store

    return alert_store()


def file_cohort_cost_incident(org_id: str, detail: dict | None = None) -> bool:
    """Open (or re-use) the incident for a cap firing. True if this call filed.

    SYNCHRONOUS and network-bound (GitHub issue + Telegram) — callers on the
    event loop MUST dispatch it through ``asyncio.to_thread``. Best-effort by
    contract: observability must never turn a refusal into a 500, and the
    refusal is already the protection.
    """
    detail = dict(detail or {})
    try:
        store = _alert_store()
    except Exception:
        _logger.exception("cohort_cost: alert store unavailable")
        return False
    if store is None:
        # No alert plane (dev/embedded). The structured refusal log is the
        # only sink available — never silently nothing.
        _logger.warning("cohort_cost_cap trip (no alert store): org=%s %s",
                        org_id, detail)
        return False
    try:
        return bool(store.open_incident(INCIDENT_KIND, org_id, detail))
    except Exception:
        _logger.exception("cohort_cost: incident filing failed org=%s", org_id)
        return False


def report_unenforceable_cap(org_id: str, error: BaseException) -> None:
    """Operator alert: the cohort cap CANNOT be enforced for this org (#3981).

    Mirrors ``metering.report_unmetered_increment``: the failure is absorbed by
    US and announced to the OPERATOR — never silently, and never to the user.
    The pre-spend cap's window is the REQUESTING org's metering window, and an
    org whose subscription anchor is unusable (half-known, unparseable, naive,
    inverted, or an unreadable anchor) has no measurable window — so the cap is
    not evaluated for it and the request is SERVED. That is the accepted trade
    under the owner's #3981 ruling: **a late cap beats refusing a paying org.**

    A calendar-month substitute is NOT used here: it would read a cohort whose
    windows are subscription-anchored as FREE — the false PASS this lane exists
    to prevent (see ``metering._current_period``, D10/D13). Every org whose
    window DOES resolve is still gated exactly as before.

    Never raises: the alert itself must not become a new failure path (a signal
    that can raise is a refusal by another name). The incident — a GitHub issue
    plus Telegram, deduped per (kind, org) — is the durable operator signal; the
    ERROR record below is the local one. The kind is
    :data:`UNENFORCEABLE_INCIDENT_KIND`, distinct from :data:`INCIDENT_KIND` (a
    cap that FIRED), so an unenforceable cap can never read as a cap firing in
    the incident channel.
    """
    with contextlib.suppress(Exception):  # the alert must never raise
        _logger.error(
            "UNENFORCEABLE COHORT COST CAP (#3981): team=%s error=%s: %s — "
            "the org's metering window is unresolvable, so the pre-spend cap "
            "was NOT evaluated and the request is served; the cohort's spend "
            "for this window cannot be measured (no calendar-month fallback). "
            "A late cap beats refusing a paying org; this alert is the trade.",
            org_id, type(error).__name__, error, exc_info=error,
        )
        from tortoise.operator_alert import alert_operator

        alert_operator(UNENFORCEABLE_INCIDENT_KIND, org_id,
                       {"error_type": type(error).__name__})


def enforce_cohort_cost_cap(org: dict | None, *,
                            cap: CohortCostCap | None = None,
                            env: dict | None = None) -> None:
    """Refuse when *org*'s cohort is at/over the measured-spend cap (#3665).

    Args:
        org: the resolved org dict (REST) or the hand-built capture dict (MCP)
            — only ``org_id`` is read.
        cap: a pre-resolved cap (callers that already resolved one / tests).
        env: environment override for the resolution (tests).

    Raises:
        CohortCostCapExceeded: the cohort's measured spend >= the cap (a
            ``QuotaExceededError`` subclass — the existing 402 contract).
        QuotaCheckError: the COHORT could not be resolved (fail-closed → 500;
            never a silent pass). This is NOT window resolution: an
            unresolvable metering window is ABSORBED here and reported to the
            operator (``report_unenforceable_cap``), so the request is served
            (#3981, owner ruling — see the note at the call site).

    No-ops when: no ``org_id`` (stdio/operator), the cap is unset, the org was
    created before the cohort start (the cap is cohort-SCOPED — a pre-beta org
    is never caught by it), or the selfhost transport is serving.
    """
    org_id = (org or {}).get("org_id")
    if not org_id:
        return  # stdio/operator — no org context
    from tortoise.metering import _selfhost_transport_active
    if _selfhost_transport_active():
        return  # selfhost transport is unbudgeted (#1987's precedent)
    resolved = cap if cap is not None else resolve_cohort_cost_cap(env)
    if resolved is None:
        return  # unset → gate off (the default posture)

    ids = cohort_org_ids(resolved.since)
    if str(org_id) not in ids:
        return  # not in the cohort — the cap never reaches outside it

    from tortoise.metering import _current_period, get_cohort_spend_usd
    # The window is the REQUESTING org's metering window (D10: the
    # subscription's own billing period; D13: the calendar month in UTC when it
    # has no subscription). ``_current_period`` RAISES QuotaCheckError for a
    # subscription org whose anchor is unusable.
    #
    # ABSORBED, NOT REFUSED (#3981, owner ruling 2026-09-18). Before #3825 this
    # gate's window was PURE CALENDAR ARITHMETIC (a deleted ``current_period()``)
    # and could not raise; #3825 made it call the resolver, which CAN. Letting it
    # escape as a 500 would be a NEW unconditional user-facing refusal on the
    # capture path, BEFORE any spend — exactly what the ruling forbids. So an
    # unresolvable window is absorbed here and REPORTED TO THE OPERATOR: the
    # request proceeds, and the cap is simply NOT EVALUATED for this org, whose
    # cohort spend cannot be measured for a window nobody can resolve. Do NOT
    # substitute a calendar month — for a subscription-anchored cohort that
    # reads the cohort as FREE, the false PASS this lane exists to prevent.
    # ACCEPTED TRADE (the ruling's own words): a LATE cap beats refusing a
    # PAYING org — and the alert is what keeps that from being silent. The money
    # guard is UNCHANGED for every org whose window DOES resolve.
    try:
        period = _current_period(org_id)
    except QuotaCheckError as e:
        report_unenforceable_cap(str(org_id), e)
        return
    spent = get_cohort_spend_usd(ids, period)
    if spent >= resolved.cap_usd:
        # The client-visible message carries NO cohort-wide figure
        # (code-review cycle 1, P2): ``spent`` is the SUM across every org in
        # the cohort, so publishing it on the 402 would disclose the
        # aggregate LLM COGS of the OTHER tenants — and, since a tenant can
        # observe its own run-rate, their spend by subtraction. The tenant is
        # told what it needs (the ceiling is reached, nothing was written,
        # when to retry); the figures go to the internal sinks only — the
        # AlertStore incident and the server-side log.
        raise CohortCostCapExceeded(
            f"Cohort LLM spend cap reached for this billing period. This "
            f"request started no extraction and wrote no capture data — "
            f"re-POST the same session after {period.end_iso} "
            f"and it will extract normally. Contact us if "
            f"you need the cap raised.",
            detail={
                "cohort_since": resolved.since,
                "cohort_size": len(ids),
                "cap_usd": resolved.cap_usd,
                "spent_usd": round(spent, 6),
                "period": period.label,
                "period_start": period.start_iso,
                "period_end": period.end_iso,
                "org_id": str(org_id),
            },
        )
