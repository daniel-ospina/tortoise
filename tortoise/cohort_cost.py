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
The ledger is ``metering_records`` (PK ``(org_id, period)``): the ask lane's
``ask_cost_usd`` (#1987) plus the capture lane's ``capture_cost_usd``
(measured by #3359, put on the ledger by migration 20260917000001). The
aggregate is one row per org per month, not one per capture — which is why
this read can run on every admission without a cache (a cache would weaken
the bound by its TTL; #3665 trade-off 2, decided explicitly).

**This bounds REQUESTS, not dollars.** A pre-spend check reads spend *already
recorded*; captures already in flight can still spend after they passed the
gate. The overshoot is bounded by ``_CAPTURE_MAX_IN_FLIGHT`` (default 8) x one
extraction's worst case. Closing it needs a reservation (reserve an estimated
cost at admission, reconcile on completion) — the ledger and its atomic
increment RPC are the substrate for that. Stated here so it is never read as
a hard dollar bound.

**It is not armed by default.** ``TORTOISE_COHORT_COST_CAP_USD`` unset means
the gate is a no-op; the machinery ships enabled-off so arming is an explicit
ops decision. ARM ORDER MATTERS: apply migration 20260917000001 (the
``capture_cost_usd`` column and its increment RPC) BEFORE setting the cap —
an armed gate reading a column that does not exist fails CLOSED (500), which
is the intended direction of failure but still an outage.
"""
from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from tortoise.quota import QuotaCheckError, QuotaExceededError

_logger = logging.getLogger("tortoise.cohort_cost")

# Ops levers. Both are read per request (a cap can be armed or lifted without
# a redeploy of the gate logic).
CAP_ENV = "TORTOISE_COHORT_COST_CAP_USD"
SINCE_ENV = "TORTOISE_COHORT_COST_SINCE"

# The AlertStore incident kind — deduped by AlertStore on (kind, subject), so
# a cohort that keeps tripping opens ONE incident, not one per capture.
INCIDENT_KIND = "COHORT_COST_CAP"

# Bounds on the cohort we are willing to PRICE, chosen so both control-plane
# reads (``organizations`` and ``metering_records``) stay under PostgREST's
# ``db-max-rows`` response cap. A silently truncated read would UNDERSTATE
# spend — a fail-open on a spend ceiling — so an oversize cohort is refused
# (QuotaCheckError → 500), never partially summed. ~10-50 orgs is the beta
# population this cap is written for.
#
# ASSUMPTION THIS RESTS ON: the deployed PostgREST ``db-max-rows`` must be
# GREATER than this bound (the platform default is 1000; this is 500). The
# guards below compare against the COHORT SIZE, not against the row count a
# short read would return, so a project configured with ``db-max-rows`` below
# the cohort size would get a silently short read that satisfies both guards.
# Re-check this bound if ``db-max-rows`` is ever lowered.
_MAX_COHORT_ORGS = 500


class CohortCostCapExceeded(QuotaExceededError):
    """The cohort's measured spend is at/over the cap (#3665).

    A ``QuotaExceededError`` subclass on purpose: the existing fail-closed 402
    contract (``hosted_api._check_org_limit``, MCP's ``ERR_QUOTA`` mapping)
    then covers the cohort cap with **no new mechanism** — which is the whole
    point of reusing ``quota.enforce_org_limit``'s error pair. ``detail``
    carries the cohort-scoped incident payload for the AlertStore sink.
    """

    def __init__(self, message: str, *, detail: dict | None = None) -> None:
        super().__init__(message)
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
    return CohortCostCap(cap_usd=cap_usd, since=since)


def current_period() -> str:
    """The billing period as ``"YYYY-MM"`` (UTC) — the ledger's row key."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    return f"{now.year}-{now.month:02d}"


def next_period_start_iso() -> str:
    """First instant of the next period — the cap's reset instant, surfaced in
    the refusal message so the refusal is actionable."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return nxt.isoformat()


def cohort_org_ids(since: str) -> list[str]:
    """The cohort: org ids with ``created_at > since``.

    Supabase mode reads the ``organizations`` table (the source of truth
    post-#669); otherwise the registry twin, matching a bare ``:Organization``
    (production/URI lane) or ``:Team`` (the embedded lane's label).

    BOUNDED: at most ``_MAX_COHORT_ORGS`` orgs are priced. A larger cohort
    raises (fail-closed) rather than silently reading a truncated org set —
    truncation here would both understate spend AND drop orgs out of the
    membership test, disarming the cap for exactly those orgs. (This bound is
    only a truncation guard while the project's ``db-max-rows`` exceeds it —
    see ``_MAX_COHORT_ORGS``.)

    FAIL-CLOSED: a resolution failure raises ``QuotaCheckError`` — an
    unresolvable cohort must not read as "this org is outside the cohort",
    which would silently disarm the cap.
    """
    from tortoise.supabase_control import get_control_plane, is_supabase_enabled
    try:
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "organizations", select=["id"],
                filters=[("created_at", "gt", since)],
                limit=_MAX_COHORT_ORGS + 1,
            )
            ids = [str(r["id"]) for r in rows if r.get("id")]
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
            f"can price from one bounded ledger read ({len(ids)} orgs) — "
            f"refusing to gate on a possibly-truncated aggregate (fail-closed)"
        )
    return ids


def _alert_store():
    """The AlertStore, or ``None`` when the backup/alert plane is unconfigured
    (a local dev box, the embedded test lane). Indirection kept callable so
    tests can substitute a REAL AlertStore over fake transport."""
    from tortoise import hosted_api as _ha
    cfg = _ha._backup_config_safe()
    if cfg is None:
        return None
    return _ha._alert_store_from(cfg)


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
        QuotaCheckError: the cap or the cohort could not be evaluated
            (fail-closed → 500; never a silent pass).

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

    from tortoise.metering import get_cohort_spend_usd
    period = current_period()
    spent = get_cohort_spend_usd(ids, period)
    if spent >= resolved.cap_usd:
        raise CohortCostCapExceeded(
            f"Cohort LLM spend cap reached for this billing period: "
            f"${spent:.4f} spent across {len(ids)} organization(s) in the "
            f"cohort against a ${resolved.cap_usd:.2f} ceiling. This request "
            f"started no extraction and wrote no capture data — re-POST the "
            f"same session after {next_period_start_iso()} (period {period}) "
            f"and it will extract normally. Contact us if you need the cap "
            f"raised.",
            detail={
                "cohort_since": resolved.since,
                "cohort_size": len(ids),
                "cap_usd": resolved.cap_usd,
                "spent_usd": round(spent, 6),
                "period": period,
                "org_id": str(org_id),
            },
        )
