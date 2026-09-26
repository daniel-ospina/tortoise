"""Per-org write-op metering for overage billing (#681).

Design
~~~~~~
Metering records live as ``:MeteringRecord`` nodes in the FalkorDB **registry**
namespace (not org-specific graphs), keyed by ``(org_id, period_start)``, where
the row expresses a half-open window ``[period_start, period_end)`` (#3825).
The window is the **subscription's own billing period** (D10, adopted), or —
for an org with no subscription — **the calendar month in UTC** (D13,
adopted). ``period`` survives as a DERIVED ``"YYYY-MM"`` aggregation label; it
is never the row key and never the meter window (D10 permits a month as an
aggregation sub-period, never as the invoice window).

Why the registry graph?
- All billing state (Org nodes, subscription fields, WebhookEvent) already
  lives in the registry — metering is billing infrastructure, not user data.
- Lift-and-shift to Supabase (#669) is straightforward: one node label →
  one table.
- No cross-namespace queries needed — a single ``MERGE … SET …`` writes the
  record, and the dashboard/usage endpoint reads from the same namespace.

Period rollover is **lazy** (no cron): when a write arrives in a new window,
the period key changes → a new ``:MeteringRecord`` is MERGEd and incremented.
Previous-period records are frozen (no further increments). A Stripe renewal
advances the subscription anchor, so the next write opens a NEW row and leaves
the prior one byte-identical — history is never rewritten. This avoids a
scheduler dependency while keeping the write path simple.

Increment semantics
~~~~~~~~~~~~~~~~~~~
Each successful **write API call** = 1 write op. We count calls, not nodes
created — a ``create_point`` that internally creates 1 node and a
``capture_session`` that creates thousands both count as 1 write op. This
matches the pricing page's "$5 per additional 10k write ops" — a write op
is one API call, not one graph element.

Storage
~~~~~~~
::

    (:MeteringRecord {
        org_id:       "org_abc123",
        period_start: "2026-08-03T00:00:00+00:00",  -- row KEY: window [start,end)
        period_end:   "2026-09-03T00:00:00+00:00",  -- EXCLUSIVE upper bound
        period:       "2026-08",                    -- derived label, NOT the key
        write_ops:    42,
        nodes_written: 12,
        updated_at:   "2026-08-09T14:31:00.123Z"
    })

``nodes_written`` is the value-first commit cost driver (epic #909 §4.4/
W-4/PL4): +net-new non-episodic nodes per commit call (0 on hold commits;
supersede-only deltas exempt — R-14). It prevents the 25x per-node arbitrage
vs ``create_point`` while the billed unit stays ``write_ops`` (a commit call
is billed exactly once — PL4).

Atomic increment via FalkorDB Cypher::

    MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart})
    SET m.period = $label, m.period_end = $pend,
        m.write_ops = coalesce(m.write_ops, 0) + $n,
        m.updated_at = $now

Threshold events
~~~~~~~~~~~~~~~~
When an org crosses 80% or 100% of its ``included_write_ops_per_month``
(from ``product/pricing.json``), a structured log event is emitted at
WARNING (80%) or ERROR (100%). This feeds into the existing alerting
pipeline (Resend + Telegram — see #310 billing notifications).

Thresholds are checked **post-increment** on every write for orgs on
overage-eligible tiers. WHICH tiers those are is deliberately not restated
here: it is read from ``product/pricing.json::billing.overage_tiers`` via
``tortoise.pricing.has_overage()``, so an enumeration in this docstring
could only go stale (#4815 taught solo, previously a "hard-cap" tier, is
metered).

Usage exposure
~~~~~~~~~~~~~~
``get_current_usage(org_id)`` returns ``{write_ops_used, write_ops_limit,
period, period_start, period_end, overage_eligible}`` for the org's current
metering window (``period`` stays the derived ``"YYYY-MM"`` label so the
``/v1/team`` contract is unchanged). Wired into ``GET /v1/team`` (see
``OrgInfoResponse`` extension).

MCP writes
~~~~~~~~~~
MCP write tools call ``_safe`` which runs the write inside a try/except.
Metering is recorded **after** a successful write (no exception) and only
for quota-gated tools (the ``_QUOTA_GATED`` set). Stdio/selfhost mode
(with no org context) skips metering entirely.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

_logger = logging.getLogger(__name__)

#: #1987 Task 6: per-org increment serialization — the MERGE+coalesce
#: increment is atomic per statement on server-mode FalkorDB, but embedded
#: FalkorDBLite connections race the read-modify-write (the pre-existing
#: write-op path has the same shape); the ask meter closes it in-process
#: with a per-org lock (cross-process races remain possible and are
#: documented best-effort, mirroring the per-process budget bucket).
import threading as _threading  # noqa: E402
from weakref import WeakValueDictionary as _WeakValueDictionary  # noqa: E402

# #3665: the cohort spend reader drives a spend CEILING, so its failures must
# carry the quota classification (#686) rather than surfacing as an
# unclassified 500. ``tortoise.quota`` is stdlib-only at module level (safe to
# import eagerly — same reason the REST adapter imports it at the top).
from tortoise.quota import QuotaCheckError  # noqa: E402

#: Per-org increment-serialization lock registry — BOUNDED by construction:
#: a WeakValueDictionary keeps each lock alive only while some thread holds
#: it (a released lock with no holder is GC'd), so a fresh org id never leaks
#: a permanent registry entry (the ask build-lock finding's mirror).
_ask_meter_locks: _WeakValueDictionary = _WeakValueDictionary()
_ask_meter_locks_guard = _threading.Lock()


def _ask_meter_lock(org_id: str) -> _threading.Lock:
    with _ask_meter_locks_guard:
        lock = _ask_meter_locks.get(org_id)
        if lock is None:
            lock = _threading.Lock()
            _ask_meter_locks[org_id] = lock
        return lock

# ── Period helpers (#3825 / D10 + D13) ──────────────────────────────────────
#
# The meter's window is the SUBSCRIPTION'S OWN BILLING PERIOD, anchored to
# ``organizations.subscription_id`` — not a calendar month, not a rolling 30
# days. That is the convergent standard Stripe's own metering product
# implements: usage is totalled over the billing period so the meter
# reconciles exactly with the invoice line.
#
# D13 closes the gap D10 left open: ``subscription_id`` is nullable
# (``0006_teams.sql:32``), so every free/anon org — precisely the beta cohort
# the spend cap targets — has no anchor at all. The adopted fallback for that
# case is the CALENDAR MONTH IN UTC.
#
# Every window is HALF-OPEN ``[start, end)``: an instant exactly at ``end``
# belongs to the NEXT period, so a renewal boundary can neither double-count
# nor drop the boundary instant.


@dataclass(frozen=True)
class MeteringPeriod:
    """One metering window — half-open ``[start, end)``, tz-aware UTC."""

    start: datetime
    end: datetime

    @property
    def start_iso(self) -> str:
        """The ledger row KEY (``metering_records.period_start``)."""
        return self.start.isoformat()

    @property
    def end_iso(self) -> str:
        """The EXCLUSIVE upper bound (``metering_records.period_end``)."""
        return self.end.isoformat()

    @property
    def label(self) -> str:
        """``"YYYY-MM"`` derived from ``start`` — an AGGREGATION sub-period.

        D10 permits a month as an aggregation label, never as the invoice
        window. It is a PURE FUNCTION of ``start``, so it can never drift from
        the key it labels; the ledger's identity is ``(org_id, period_start)``.
        """
        return f"{self.start.year}-{self.start.month:02d}"


def _calendar_month_period(now: datetime | None = None) -> MeteringPeriod:
    """The calendar month in UTC containing *now* — the D13 fallback window.

    Anchored to 00:00:00 UTC on the 1st. GitHub Copilot ("00:00:00 UTC on the
    first day of each calendar month"), LangSmith and PymtHouse document
    exactly this split: a contract period when a subscription exists, the
    calendar month in UTC otherwise.

    Used ONLY for an org that carries no subscription — never as a FAILURE
    fallback. An unresolvable paid anchor RAISES (see ``_current_period``):
    metering a paying org on a month bucket would put its spend on a row the
    cap's window read never looks at, which is the "configured but not
    enforced" false PASS this lane exists to prevent.
    """
    now = now if now is not None else datetime.now(timezone.utc)  # noqa: UP017
    first = now.astimezone(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return MeteringPeriod(start=first, end=nxt)


def _anchor_instant(value: object) -> datetime | None:
    """Parse a stored period instant, or ``None`` when it is not unambiguous.

    Three storage shapes must all work. The control plane returns a
    ``timestamptz`` as an ISO-8601 string via PostgREST; the REGISTRY lane
    stores whatever the Stripe webhook wrote — a Unix EPOCH INT
    (``hosted_api`` sets ``updates["current_period_end"] = data[...]``
    verbatim, and ``billing.mirror_subscription`` does the same); and a
    hand-built dict may carry a real ``datetime``.

    A NAIVE (offset-less) value is REJECTED, not assumed UTC: the whole ledger
    is UTC, so an ambiguous instant must never be silently coerced. A ``bool``
    is rejected too (it is an ``int`` subclass, and ``True`` is not 1970).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _metering_anchor(org_id: str) -> dict:
    """The org's billing anchor: ``subscription_id`` + period start/end.

    Supabase mode reads the ``organizations`` row; otherwise the registry twin,
    matching a bare ``:Organization`` (production/URI lane) or ``:Team`` (the
    embedded lane's label) — the same id resolution ``cohort_cost.
    cohort_org_ids`` uses.

    An org with NO matching row returns ``{}``, which resolves to the D13
    calendar-month window (an unknown org has no subscription). A READ FAILURE
    raises instead — the caller must not spell "I could not find out" the same
    way as "this org has no subscription".
    """
    if _supabase_mode():
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, org_metering_anchor,
        )
        return dict(org_metering_anchor(get_control_plane(), org_id) or {})
    rows = _reg_sdk()._get_registry().query(
        "MATCH (o) WHERE (o:Organization OR o:Team) AND o.id = $id "
        "RETURN o.subscription_id, o.current_period_start, "
        "       o.current_period_end",
        params={"id": org_id},
    ).result_set
    if not rows:
        return {}
    row = rows[0]
    return {"subscription_id": row[0],
            "current_period_start": row[1],
            "current_period_end": row[2]}


def _current_period(org_id: str) -> MeteringPeriod:
    """The metering window for *org_id* (#3825 / D10 + D13).

    Subscription present → the subscription's own billing period. No
    subscription → the calendar month in UTC (D13).

    Deliberately NOT tolerant of a half-known anchor. An org that carries a
    ``subscription_id`` but whose period cannot be resolved (missing,
    unparseable, naive, or inverted) RAISES
    :class:`~tortoise.quota.QuotaCheckError` rather than falling back to the
    calendar month. A read failure raises too.

    WHAT THAT RAISE IS (#3981). It is a **SIGNAL, not enforcement**: every
    production caller absorbs it, alerts the operator and serves the request.
    On the **write** path (the three ``record_*`` writers, via
    ``_require_period``) the increment is dropped and reported
    (``report_unmetered_increment``). On the **read** path
    (``cohort_cost.enforce_cohort_cost_cap``) the pre-spend cap CANNOT be
    enforced for that org, so it serves too and reports via
    ``cohort_cost.report_unenforceable_cap`` — refusing a paying org over our
    own bookkeeping fault is exactly what the #3981 ruling forbids. A raise
    nobody re-raises refuses nothing; the ONLY user-facing refusal on this
    lane is the gate's 402 for a window that RESOLVES and a cohort at/over
    the cap.

    ``org_id`` is REQUIRED — the previous zero-argument form was structurally
    incapable of resolving a per-subscription anchor, which is how the whole
    ledger ended up month-keyed.
    """
    try:
        anchor = _metering_anchor(org_id)
    except Exception as e:
        raise QuotaCheckError(
            f"metering window unresolvable for org {org_id!r}: the billing "
            f"anchor read failed ({e}) — refusing to fall back to a calendar "
            f"month (no calendar-month fallback)"
        ) from e
    sub_id = anchor.get("subscription_id")
    if sub_id is None or not str(sub_id).strip():
        return _calendar_month_period()  # D13 — no subscription to anchor to
    start = _anchor_instant(anchor.get("current_period_start"))
    end = _anchor_instant(anchor.get("current_period_end"))
    if start is None or end is None or start >= end:
        raise QuotaCheckError(
            f"metering window unresolvable for org {org_id!r}: it carries "
            f"subscription {sub_id!r} but its billing period is not a usable "
            f"half-open interval (start={anchor.get('current_period_start')!r}, "
            f"end={anchor.get('current_period_end')!r}) — refusing to meter a "
            f"subscription org on a calendar month (no calendar-month fallback)"
        )
    return MeteringPeriod(start=start, end=end)


def _require_period(org_id: str, what: str) -> MeteringPeriod:
    """Window resolution for the write paths — a raised SIGNAL (#3825/#3981).

    This was ``_resolve_period_or_none``: it DROPPED the increment and returned
    None, on the long-standing best-effort contract (#681/#923). #3825 replaced
    the silent drop with a raise, on the reasoning that a dropped increment
    leaves the ledger SHORT, so the cohort SUM undercounts and the cap fires
    LATE — spending real money past the cap. That reasoning is sound; what
    shipped with it did not hold end-to-end.

    WHAT THIS IS, AND WHAT IT IS NOT (#3981): this is a **SIGNAL**. It is NOT
    enforcement. An unresolvable window RAISES
    :class:`~tortoise.quota.QuotaCheckError`, but every production caller of
    the three ``record_*`` writers wraps the call in a broad ``except`` and
    absorbs the raise: the request is served and the increment is dropped,
    exactly as before #3825. A raise nobody re-raises refuses nothing. The
    user-facing refusal, where one exists, lives at the **pre-spend admission
    gate** (``cohort_cost.enforce_cohort_cost_cap``, reached from the hosted
    capture path BEFORE any spend). That gate refuses (402) only when the
    window RESOLVES and the cohort is at/over the cap; an unresolvable window
    it absorbs and reports (``cohort_cost.report_unenforceable_cap``), per the
    #3981 ruling. Nothing here adds or removes either behaviour.

    So the callers report the drop to the OPERATOR — never silently, and never
    to the user: a bookkeeping fault of ours must never hand a user a 500
    (``report_unmetered_increment``, one lane per swallow site). What was
    recoverable about the old behaviour was the *silence*, which is why the
    request is still served and the drop is still announced.

    Scope: ONLY window resolution. A failure of the increment RPC itself stays
    non-fatal inside each writer — logged at WARNING and dropped, not retried
    at any call site. That is a separate residual; representing it is #3824.
    """
    try:
        return _current_period(org_id)
    except Exception as e:
        _logger.error(
            "%s: the org's metering window is unresolvable (no calendar-month "
            "fallback) — the increment will be DROPPED and the caller reports "
            "it to the operator. This raise is a SIGNAL, not a refusal "
            "(#3981): team=%s error=%s",
            what, org_id, e,
        )
        raise


def report_unmetered_increment(lane: str, org_id: str | None,
                               error: BaseException) -> None:
    """Operator alert: an increment the ledger will NOT contain (#3981).

    Call this from the ``except`` handler that absorbs a failed ``record_*``
    writer. Under the recorded #3825/#3981 ruling the request is SERVED — a
    bookkeeping fault of ours must never become a user-facing refusal — and the
    increment is therefore gone. That is the defect the silent
    ``except Exception: pass`` left behind: a ledger that reads short with no
    trace anywhere. This is the trace.

    ``lane`` names the swallow site (one stable token per site) so an alert is
    attributable to the handler that dropped the increment rather than to
    "metering failed somewhere". The six lanes are ``write_op``,
    ``object_write_op``, ``subject_write_op``, ``capture_ledger``,
    ``mcp_write_op`` and ``ask_ledger``.

    Never raises: the alert itself must not become a new failure path (a signal
    that can raise is a refusal by another name). The incident — a GitHub issue
    plus Telegram, deduped per (kind, org) — is the durable operator signal; the
    ERROR record below is the local one. The increment still reads short on the
    ledger: this makes the drop VISIBLE, it does not repair it (leg 2 of the
    ruling is tracked separately, see the plan doc / #3981).
    """
    with contextlib.suppress(Exception):  # the alert must never raise
        _logger.error(
            "UNMETERED INCREMENT (#3981): lane=%s team=%s error=%s: %s — the "
            "request was served and the ledger will read short for this "
            "window; the pre-spend admission gate is the only enforcement "
            "point on this lane",
            lane, org_id or "<none>", type(error).__name__, error,
            exc_info=error,
        )
        from tortoise.operator_alert import alert_unmetered_increment

        alert_unmetered_increment(lane, org_id, error)


def _display_period_label() -> str:
    """A calendar-month label for DISPLAY-ONLY degrade paths.

    ``get_current_usage``/``get_ask_usage`` never 500 (#923) — they degrade to
    a zero view. When the WINDOW itself is unresolvable there is no honest
    label for a row that was never read, so these paths render the current
    calendar month as a placeholder and log loudly.

    This NEVER supplies a ledger key: the key is only ever ``_current_period``'s
    ``start_iso``, and a bare month label can no longer address a row at all.
    """
    return _calendar_month_period().label


# ── Pricing integration ─────────────────────────────────────────────────────

def _ops_allowance(tier: str) -> int:
    """Return included_write_ops_per_month for a tier (pricing.json)."""
    from tortoise.pricing import tier_limits
    lim = tier_limits(tier)
    return int(lim.get("included_write_ops_per_month", 0))


def _overage_eligible(tier: str) -> bool:
    """True if this tier is billed for overage."""
    from tortoise.pricing import has_overage
    return has_overage(tier)


# ── Registry SDK helper ─────────────────────────────────────────────────────

def _supabase_mode() -> bool:
    """True when metering should use the Supabase control plane (post-#669
    flip: the registry is deleted — MeteringRecord nodes there would
    recreate it on every /v1/team call)."""
    from tortoise.supabase_control import is_supabase_enabled
    return is_supabase_enabled()


def _reg_sdk():
    """Build a TortoiseSDK pointing at the registry namespace.

    Same precedence as ``quota._make_sdk``: URI mode when TORTOISE_DB_URI is
    set; else embedded via TORTOISE_DB_PATH with tempfile fallback.
    SUPABASE MODE (post-#669 flip): metering uses the metering_records table
    (0014) via the seam — never the registry (which is deleted).
    """
    from tortoise.sdk import TortoiseSDK
    if os.environ.get("TORTOISE_DB_URI"):
        return TortoiseSDK(namespace="registry")
    db_path = os.environ.get("TORTOISE_DB_PATH", "/data/tortoise.db")
    try:
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    except OSError:
        db_path = os.path.join(tempfile.gettempdir(), "tortoise.db")
    return TortoiseSDK(db_path=db_path, namespace="registry")


# ── Core increment ──────────────────────────────────────────────────────────

def record_write_ops(org_id: str, tier: str | None = None, n: int = 1,
                     nodes_written: int = 0) -> dict | None:
    """Increment the write-op counter for *org_id* in the current billing period.

    Args:
        org_id: Org identifier (required).
        tier: Team tier — used to determine overage eligibility and allowance
            for threshold events. If None, threshold checks are skipped (e.g.
            when called from a context where tier isn't readily available).
        n: Number of write ops to record (default 1).
        nodes_written: Net-new non-episodic nodes written by this call (the
            value-first commit cost driver, epic #909 §4.4/W-4/PL4 — 0 on
            hold commits; supersede-only deltas exempt, R-14). Stored on the
            MeteringRecord as ``nodes_written``.

    Returns:
        ``{write_ops, nodes_written, period, ops_allowance, overage_eligible}``
        for threshold checking, or None if the increment RPC is unreachable
        (non-fatal — the window is known, and the drop is logged at WARNING:
        it is NOT retried at any call site, so that increment is not
        recovered. Representing an unmeterable increment is #3824).

    Raises:
        QuotaCheckError: the org's metering window is unresolvable. This is a
            SIGNAL, not enforcement (#3981): this writer's callers absorb the
            raise, alert the operator and serve the request, so it refuses no
            user request. Enforcement -- where a refusal exists -- lives at the
            pre-spend admission gate. Only window resolution raises; a failure
            of the increment RPC itself is still logged and swallowed (the
            window is known, but the increment is dropped — see Returns).
    """
    if not org_id:
        return None
    period = _require_period(org_id, "write-op metering increment")
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_increment,
            )
            write_ops = metering_increment(
                get_control_plane(), org_id, period.start_iso, period.end_iso,
                n, nodes_written=nodes_written)
            result = {
                "write_ops": write_ops,
                "nodes_written": nodes_written,
                "period": period.label,
                "period_start": period.start_iso,
                "period_end": period.end_iso,
                "ops_allowance": _ops_allowance(tier) if tier else 0,
                "overage_eligible": _overage_eligible(tier) if tier else False,
            }
            _check_thresholds(org_id, tier, result, n)
            return result
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "SET m.period = $label, m.period_end = $pend, "
            "    m.write_ops = coalesce(m.write_ops, 0) + $n, "
            "    m.nodes_written = coalesce(m.nodes_written, 0) + $nw, "
            "    m.updated_at = $now",
            params={"tid": org_id, "pstart": period.start_iso,
                    "pend": period.end_iso, "label": period.label, "n": n,
                    "nw": nodes_written, "now": now_iso},
        )
        rows = reg.query(
            "MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "RETURN m.write_ops, m.nodes_written",
            params={"tid": org_id, "pstart": period.start_iso},
        ).result_set
        write_ops = int(rows[0][0]) if rows else n
        nodes_written_total = int(rows[0][1]) if rows else nodes_written
    except Exception as e:
        _logger.warning(
            "metering increment failed (non-fatal): team=%s period=%s error=%s",
            org_id, period.label, e,
        )
        return None

    result = {
        "write_ops": write_ops,
        "nodes_written": nodes_written_total,
        "period": period.label,
        "period_start": period.start_iso,
        "period_end": period.end_iso,
        "ops_allowance": _ops_allowance(tier) if tier else 0,
        "overage_eligible": _overage_eligible(tier) if tier else False,
    }

    # Threshold events
    _check_thresholds(org_id, tier, result, n)

    return result


# ── Threshold events ────────────────────────────────────────────────────────

_THRESHOLD_PCT = [80, 100]

# Track which thresholds have already been crossed to avoid duplicate events
# per period. In-memory only — resets on process restart (acceptable for v1;
# a duplicate alert on restart is better than missing an alert entirely).
_thresholds_fired: set[tuple[str, str, int]] = set()


def _check_thresholds(
    org_id: str,
    tier: str | None,
    result: dict,
    n: int = 1,
) -> None:
    """Emit log events if write_ops crossed an 80% or 100% threshold.

    Only fires for overage-eligible tiers — the set is
    ``product/pricing.json::billing.overage_tiers``, read through
    ``tortoise.pricing.has_overage()``; it is not restated here. Each
    threshold fires at most once per (org_id, period, pct) per process
    lifetime.
    """
    if not tier or not result.get("overage_eligible"):
        return
    allowance = result.get("ops_allowance", 0)
    if allowance <= 0:
        return
    write_ops = result.get("write_ops", 0)
    period = result.get("period", "")

    for pct in _THRESHOLD_PCT:
        threshold = int(allowance * pct / 100)
        # Crossed the threshold THIS increment (was below, now at or above).
        # `previous` accounts for the batch size n — a single jump over the
        # threshold (e.g. 0 → 105 with n=105) still fires the event.
        previous = write_ops - max(n, 1)
        if previous < threshold <= write_ops:
            # Deduped per WINDOW, not per month label (#3825): two different
            # billing periods of one org can share a "YYYY-MM" label (e.g.
            # Sep-03→Oct-03 and Sep-15→Oct-15), so keying on the label would
            # suppress a legitimate alert for the second period.
            key = (org_id, result.get("period_start") or period, pct)
            if key in _thresholds_fired:
                continue
            _thresholds_fired.add(key)
            level = logging.WARNING if pct == 80 else logging.ERROR
            _logger.log(
                level,
                "write-op threshold %d%% reached: team=%s period=%s count=%d/%d",
                pct, org_id, period, write_ops, allowance,
            )


def _reset_thresholds_for_tests() -> None:
    """Clear the in-memory threshold tracker (test helper only)."""
    _thresholds_fired.clear()


# ── Ask metering (#1987 Task 6) ──────────────────────────────────────────

#: Ask-lane metering rates: verified deepseek-direct published rates
#: $0.14/M input, $0.28/M output × a single documented ×1.5 safety factor
#: (covers the OpenRouter fallback-lane markup — the resolved lane may route
#: some traffic there). deepseek-direct is the CHEAPEST lane, so the meter
#: over-covers. Worst case ~9.2k in + 500 out ≈ $0.0014-0.0023/query, ~5-7×
#: under the $0.01 target.
ASK_METER_RATES = {"prompt_per_1m": 0.21, "completion_per_1m": 0.42}

# #2069: STRONG-lane rates — qwen3.8-max via OpenRouter at verified $2.00/M
# in, $6.00/M out × the same documented ×1.5 over-cover convention (so the
# meter never under-counts on the strong lane; real worst ~$0.021, METERED
# worst ~$0.032 — the $0.01/query structural target is broken for the strong
# lane, recorded as an owner decision, see docs/runbook/1987-ask-abstention-
# check.md §#2069).
ASK_METER_RATES_STRONG = {"prompt_per_1m": 3.00, "completion_per_1m": 9.00}

#: Family prefixes that meter at the STRONG rates — DERIVED from the
#: model_adapters routing map (the openrouter-only families), so adding a
#: family to the router automatically meters it strong (no drift possible).
#: Lazy import keeps this module import-order-independent (model_adapters
#: never imports metering).
def _strong_families() -> frozenset:
    from .model_adapters import _SPEC_FAMILY_PROVIDERS

    return frozenset(
        fam for fam, provs in _SPEC_FAMILY_PROVIDERS.items()
        if provs == {"openrouter"}
    )


def select_ask_meter_rates(model_id: str | None) -> dict:
    """Pick the ask-lane metering rates by the SERVING wire id's family
    (#2069): a family-prefixed strong-family spec (``qwen/qwen3.8-max`` —
    the ``_LockedReader.model`` wire id) → ``ASK_METER_RATES_STRONG``;
    everything else (bare ids, ``deepseek/*`` — incl. a deepseek spec
    forced to openrouter via ``TORTOISE_ASK_PROVIDER``) stays on the
    default deepseek envelope (the ×1.5 over-cover documents OpenRouter
    markup, metering.py rates docstring).
    """
    family = (model_id or "").split("/", 1)[0]
    if family in _strong_families():
        return ASK_METER_RATES_STRONG
    return ASK_METER_RATES


def estimate_ask_cost_usd(tokens_in: int, tokens_out: int,
                          rates: dict | None = None) -> float:
    """Estimate the per-query LLM cost at the ask-lane over-covered rates
    (#1987 Task 6) — the producer of the response's ``cost_estimate_usd``
    field (honestly named an ESTIMATE, never an exact bill).

    ``rates`` defaults to ``ASK_METER_RATES`` (``{"prompt_per_1m": …,
    "completion_per_1m": …}``); the consumed input quantity is
    ``input_tokens = estimate_tokens_ask(system_prompt_for(qtype)) +
    estimate_tokens_ask(rendered_context)`` (Task 5).
    """
    r = rates if rates is not None else ASK_METER_RATES
    return (tokens_in / 1_000_000 * r["prompt_per_1m"]
            + tokens_out / 1_000_000 * r["completion_per_1m"])


def _selfhost_transport_active() -> bool:
    """True while a selfhost HTTP MCP transport is serving the request — the
    transport-keyed exemption channel (tortoise/transport.py; the value
    "selfhost" is NEVER the exemption key — a hosted org with the raw id
    "selfhost" is legal and MUST record usage, P1-4)."""
    from tortoise.transport import _selfhost_transport
    return _selfhost_transport.get()


def record_ask_usage(org_id: str | None, tier: str | None = None, *,
                     calls: int = 1, tokens_in: int = 0, tokens_out: int = 0,
                     cost_usd: float = 0.0,
                     _selfhost_transport: bool = False) -> dict | None:
    """Record a per-query ask usage increment (#1987 Task 6).

    Window resolution RAISES on an unresolvable anchor (#3825) rather than
    keying the row to a calendar month. That raise is a SIGNAL, not a refusal
    (#3981): the caller (``sdk.ask``) absorbs it, serves the answer and reports
    the dropped increment to the operator. A failure of the increment RPC
    itself stays non-fatal — logged at WARNING and dropped, not retried at any
    call site (representing that increment is #3824).
    Extends the ``:MeteringRecord`` (registry) with additive fields
    ``ask_calls``/``ask_tokens_in``/``ask_tokens_out``/``ask_cost_usd`` via
    the MERGE+coalesce pattern (mirrors ``record_write_ops``); Supabase mode
    routes through the ``metering_increment_ask`` seam.

    Exemptions (no record, zero writes): ``not org_id`` (stdio/None) OR the
    selfhost-transport ContextVar (``_selfhost_transport`` — set True ONLY
    by the selfhost HTTP MCP transport; the SELFHOST_ORG_ID value is never
    the exemption key). ``tier`` stays None for the ask lane (tier-based ask
    budgets are OUT of v1).

    Returns a dict summary or None (exempt/no-op/failure).
    """
    if not org_id or _selfhost_transport or _selfhost_transport_active():
        return None
    period = _require_period(org_id, "ask metering increment")
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    with _ask_meter_lock(org_id):
        return _record_ask_usage_locked(org_id, period, now_iso,
                                        calls=calls, tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                        cost_usd=cost_usd)


def _record_ask_usage_locked(org_id: str, period: MeteringPeriod,
                             now_iso: str, *,
                             calls: int, tokens_in: int, tokens_out: int,
                             cost_usd: float) -> dict | None:
    """The serialized increment body (under the per-org lock — embedded
    concurrency-safe). """
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_increment_ask,
            )
            metering_increment_ask(get_control_plane(), org_id,
                                   period.start_iso, period.end_iso,
                                   calls=calls, tokens_in=tokens_in,
                                   tokens_out=tokens_out, cost_usd=cost_usd)
            return {"period": period.label,
                    "period_start": period.start_iso,
                    "period_end": period.end_iso,
                    "ask_calls": calls,
                    "ask_tokens_in": tokens_in,
                    "ask_tokens_out": tokens_out,
                    "ask_cost_usd": cost_usd}
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "SET m.period = $label, m.period_end = $pend, "
            "    m.ask_calls = coalesce(m.ask_calls, 0) + $calls, "
            "    m.ask_tokens_in = coalesce(m.ask_tokens_in, 0) + $tin, "
            "    m.ask_tokens_out = coalesce(m.ask_tokens_out, 0) + $tout, "
            "    m.ask_cost_usd = coalesce(m.ask_cost_usd, 0) + $cost, "
            "    m.updated_at = $now",
            params={"tid": org_id, "pstart": period.start_iso,
                    "pend": period.end_iso, "label": period.label,
                    "calls": calls, "tin": tokens_in, "tout": tokens_out,
                    "cost": cost_usd, "now": now_iso},
        )
        return {"period": period.label,
                "period_start": period.start_iso,
                "period_end": period.end_iso,
                "ask_calls": calls,
                "ask_tokens_in": tokens_in, "ask_tokens_out": tokens_out,
                "ask_cost_usd": cost_usd}
    except Exception as e:
        _logger.warning(
            "ask metering increment failed (non-fatal): team=%s period=%s "
            "error=%s", org_id, period.label, e,
        )
        return None


def get_ask_usage(org_id: str) -> dict:
    """Ask usage for *org_id* in the current billing period (#1987 Task 6).

    Returns ``{ask_calls, ask_tokens_in, ask_tokens_out, ask_cost_usd}`` for
    the org's current period — ZEROS for an org with no ask records yet (a
    successful read returning NO row is not an error; the MERGE only creates
    the record on the first write — P2-14). Read failures degrade to the
    zero-usage view (never 500).
    """
    zeros = {"ask_calls": 0, "ask_tokens_in": 0, "ask_tokens_out": 0,
             "ask_cost_usd": 0.0}

    def _zero_view(label: str,
                   period: MeteringPeriod | None = None) -> dict:
        return {**zeros, "period": label,
                "period_start": period.start_iso if period else None,
                "period_end": period.end_iso if period else None}

    if not org_id:
        return _zero_view(_display_period_label())
    try:
        period = _current_period(org_id)
    except Exception as e:
        # #923: this read never raises. An unresolvable WINDOW has no row to
        # read, so the degradation is the zero view — never a month key.
        _logger.warning(
            "ask usage query failed (degrading to zero view): team=%s "
            "error=%s", org_id, e,
        )
        return _zero_view(_display_period_label())
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_get_usage,
            )
            row = metering_get_usage(get_control_plane(), org_id,
                                     period.start_iso)
            return {**zeros, **{k: row.get(k, 0) for k in zeros},
                    "period": period.label,
                    "period_start": period.start_iso,
                    "period_end": period.end_iso}
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "RETURN m.ask_calls, m.ask_tokens_in, m.ask_tokens_out, "
            "m.ask_cost_usd",
            params={"tid": org_id, "pstart": period.start_iso},
        ).result_set
        if not rows:
            return _zero_view(period.label, period)
        return {
            "ask_calls": int(rows[0][0] or 0),
            "ask_tokens_in": int(rows[0][1] or 0),
            "ask_tokens_out": int(rows[0][2] or 0),
            "ask_cost_usd": float(rows[0][3] or 0.0),
            "period": period.label,
            "period_start": period.start_iso,
            "period_end": period.end_iso,
        }
    except Exception as e:
        _logger.warning(
            "ask usage query failed (degrading to zero view): team=%s "
            "period=%s error=%s", org_id, period.label, e,
        )
        return _zero_view(period.label, period)


# ── Capture lane: measured per-session extraction cost (#3665 / #3359) ──────
#
# #3359 made the hosted capture path MEASURE per-session LLM cost (provider-
# authoritative ``last_cost_usd`` rolled up by ``extractor_v2._rollup_llm``)
# and emit it as an ``analytics_events`` row. That is a measurement, not a
# ledger — nothing keyed by org+period, so a spend CEILING cannot read it
# without scanning every capture row in the period. These two functions put
# the SAME measurement on the durable per-period ``:MeteringRecord`` (columns
# from migration 20260917000001) so that a pre-spend gate can answer "what has
# this cohort spent this period?" in one row-per-org read. No parallel system:
# it is exactly the ``record_ask_usage`` shape.


def record_capture_usage(org_id: str | None, *, calls: int = 1,
                         cost_usd: float = 0.0,
                         _selfhost_transport: bool = False) -> dict | None:
    """Record the MEASURED cost of one capture extraction attempt.

    Window resolution RAISES on an unresolvable anchor (#3825) rather than
    keying the row to a calendar month. That raise is a SIGNAL, not a refusal
    (#3981): the caller absorbs it, the capture is served and the dropped
    increment is reported to the operator. A failure of the increment RPC
    itself stays non-fatal — logged at WARNING and dropped, not retried at any
    call site (representing that increment is #3824).
    Exemptions mirror ``record_ask_usage``: ``not org_id`` (stdio/None) or
    the selfhost-transport ContextVar (``_selfhost_transport``).

    Records a row even when ``cost_usd == 0.0`` — ``capture_calls`` is the
    denominator that keeps a genuinely-free capture distinguishable from a
    capture whose cost was never measured (#3359's silent-zero lesson).

    A NON-FINITE ``cost_usd`` is dropped to 0.0 (with a warning) before it can
    reach the ledger. This is the spend ceiling's own substrate: a ``nan`` on
    the row would poison the cohort ``SUM`` and make ``spent >= cap``
    permanently False — a silently disarmed ceiling (#3665 review, the
    ``math.isfinite`` trap ``cohort_cost.resolve_cohort_cost_cap`` guards too).
    """
    if not org_id or _selfhost_transport or _selfhost_transport_active():
        return None
    if not math.isfinite(cost_usd):
        _logger.warning(
            "capture metering dropped a non-finite cost (team=%s cost=%r) — "
            "recording 0.0 for the call rather than poisoning the cohort SUM",
            org_id, cost_usd,
        )
        cost_usd = 0.0
    period = _require_period(org_id, "capture metering increment")
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    with _ask_meter_lock(org_id):
        return _record_capture_usage_locked(org_id, period, now_iso,
                                            calls=calls, cost_usd=cost_usd)


def _record_capture_usage_locked(org_id: str, period: MeteringPeriod,
                                 now_iso: str, *,
                                 calls: int, cost_usd: float) -> dict | None:
    """The serialized capture increment body."""
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_increment_capture_cost,
            )
            metering_increment_capture_cost(get_control_plane(), org_id,
                                            period.start_iso, period.end_iso,
                                            calls=calls,
                                            cost_usd=cost_usd)
            return {"period": period.label,
                    "period_start": period.start_iso,
                    "period_end": period.end_iso,
                    "capture_calls": calls,
                    "capture_cost_usd": cost_usd}
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "SET m.period = $label, m.period_end = $pend, "
            "    m.capture_calls = coalesce(m.capture_calls, 0) + $calls, "
            "    m.capture_cost_usd = coalesce(m.capture_cost_usd, 0) + $cost, "
            "    m.updated_at = $now",
            params={"tid": org_id, "pstart": period.start_iso,
                    "pend": period.end_iso, "label": period.label,
                    "calls": calls, "cost": cost_usd, "now": now_iso},
        )
        return {"period": period.label,
                "period_start": period.start_iso,
                "period_end": period.end_iso,
                "capture_calls": calls,
                "capture_cost_usd": cost_usd}
    except Exception as e:
        _logger.warning(
            "capture metering increment failed (non-fatal): team=%s "
            "period=%s error=%s", org_id, period.label, e,
        )
        return None


def get_cohort_spend_usd(org_ids: list[str],
                         period: MeteringPeriod) -> float:
    """Measured LLM spend for a COHORT over ONE metering window (#3665/#3825).

    The cohort is a set of org ids (``cohort_cost.cohort_org_ids`` — there is
    no cohort column; the only FK-enforced key to spend is
    ``organizations.id``). Spend is the durable ledger's cost columns for
    those orgs, over the supplied window: ``SUM(ask_cost_usd +
    capture_cost_usd)``.

    ``period`` is REQUIRED (#3825). It used to default to a calendar month,
    which made "the cohort's spend this period" depend on a bucket no caller
    had chosen — the exact ambiguity the ledger could not express. The caller
    (``cohort_cost.enforce_cohort_cost_cap``) resolves it from the org being
    gated, so the window is the SUBSCRIPTION'S billing period (D10) — never a
    month.

    The window is applied as an OVERLAP test (``period_start < end AND
    period_end > start``), so a cohort row straddling an edge is counted. The
    alternative — rows whose ``period_start`` falls inside the window —
    UNDER-reads every org whose period began earlier, and on a spend CEILING
    an under-read is fail-OPEN. An over-read can only fire the cap EARLIER.

    Both cost columns are MEASURED metres, never billing estimates: the ask
    lane's serving-lane rates and the capture lane's provider-authoritative
    charge. A cohort with no ledger row reads as ``0.0`` (the MERGE only
    creates the row on the first write).

    FAIL-CLOSED, deliberately unlike ``get_ask_usage``: this READER drives a
    spend ceiling, so a query failure RAISES rather than degrading to a zero
    view. Degrading here would read as "the cohort has spent nothing" — a
    fail-open the cap could never recover from (#686's discipline; the
    caller maps the raise to a 500, never a silent pass).
    """
    ids = [str(i) for i in (org_ids or []) if i]
    if not ids:
        return 0.0
    if _supabase_mode():
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, metering_cohort_spend,
        )
        try:
            return metering_cohort_spend(get_control_plane(), ids,
                                         period.start_iso, period.end_iso)
        except QuotaCheckError:
            raise
        except Exception as e:
            # #686's fail-closed quota contract: a counting failure is a
            # QuotaCheckError (→ 500 carrying the quota classification),
            # never an unclassified 500 and never a silent pass. The
            # control-plane read raises RuntimeError; classifying it here is
            # what makes the gate's own ``except QuotaCheckError`` clause (and
            # MCP's ERR_QUOTA_SERVER) actually fire. code-review cycle 1, P2.
            raise QuotaCheckError(
                f"cohort spend read failed for {len(ids)} org(s), window "
                f"[{period.start_iso}, {period.end_iso}): {e}"
            ) from e
    sdk = _reg_sdk()
    rows = sdk._get_registry().query(
        "MATCH (m:MeteringRecord) "
        "WHERE m.org_id IN $ids "
        "  AND m.period_start < $pend AND m.period_end > $pstart "
        "RETURN sum(coalesce(m.ask_cost_usd, 0.0) "
        "         + coalesce(m.capture_cost_usd, 0.0))",
        params={"ids": ids, "pstart": period.start_iso,
                "pend": period.end_iso},
    ).result_set
    return float(rows[0][0] or 0.0) if rows else 0.0


# ── Usage query ─────────────────────────────────────────────────────────────

def get_current_usage(org_id: str) -> dict:
    """Return write-op usage for *org_id* in its current metering window.

    Returns:
        ``{write_ops_used: int, write_ops_limit: int, period: str,
           period_start: str | None, period_end: str | None,
           overage_eligible: bool, overage_cost_usd: float | None}``

    ``period`` stays the DERIVED ``"YYYY-MM"`` label so the ``/v1/team``
    contract is unchanged (#3825); ``period_start``/``period_end`` expose the
    window the figure was actually read from.

    ``overage_cost_usd`` is the cost of ops BEYOND the included allowance
    (rounded up to the nearest 10k block, $5/block). None if under allowance
    or not eligible.

    DEGRADES, never raises (#923). An unresolvable WINDOW has no row to read,
    so it renders the zero view — with a placeholder label, never a month KEY.

    NOTE (#3825): this view follows the org's metering window, so for a
    subscription org it is its BILLING period, while ``pricing.json`` prices
    write-op overage per month (``included_write_ops_per_month``). Reconciling
    a non-monthly meter window against a monthly price is a separate open item,
    out of this change's scope.
    """
    try:
        period: MeteringPeriod | None = _current_period(org_id)
    except Exception as e:
        _logger.warning(
            "metering window unresolvable (usage view degrades to zeros): "
            "team=%s error=%s", org_id, e,
        )
        period = None

    def _view(ops_used: int, ops_limit: int, eligible: bool,
              overage_cost: float | None) -> dict:
        return {
            "write_ops_used": ops_used,
            "write_ops_limit": ops_limit,
            "period": period.label if period else _display_period_label(),
            "period_start": period.start_iso if period else None,
            "period_end": period.end_iso if period else None,
            "overage_eligible": eligible,
            "overage_cost_usd": overage_cost,
        }

    if period is None:
        return _view(0, _ops_allowance("free"), False, None)

    ops_used = 0
    if _supabase_mode():
        from tortoise.supabase_control import (  # noqa: I001
            get_control_plane, metering_get, org_tier,
        )
        try:
            cp = get_control_plane()
            ops_used = metering_get(cp, org_id, period.start_iso)
            tier = org_tier(cp, org_id) or "free"
            ops_limit = _ops_allowance(tier)
            eligible = _overage_eligible(tier)
            overage_cost = None
            if eligible and ops_used > ops_limit:
                from tortoise.pricing import overage_price_per_10k
                overage_units = (ops_used - ops_limit + 9999) // 10000
                overage_cost = overage_units * overage_price_per_10k()
            return _view(ops_used, ops_limit, eligible, overage_cost)
        except Exception as e:
            # Metering is best-effort by contract — a control-plane blip must
            # never 500 the /v1/team hot path (#923). Degrade to the
            # free-tier zero-usage view, mirroring the registry path below.
            _logger.warning(
                "metering usage query failed: team=%s period=%s error=%s",
                org_id, period.label, e,
            )
            return _view(0, _ops_allowance("free"), False, None)
    try:
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (m:MeteringRecord {org_id: $tid, period_start: $pstart}) "
            "RETURN m.write_ops",
            params={"tid": org_id, "pstart": period.start_iso},
        ).result_set
        if rows:
            ops_used = int(rows[0][0])
    except Exception as e:
        _logger.warning(
            "metering usage query failed: team=%s period=%s error=%s",
            org_id, period.label, e,
        )

    # Determine tier from the Org node for allowance
    try:
        sdk2 = _reg_sdk()
        reg2 = sdk2._get_registry()
        trows = reg2.query(
            "MATCH (t:Team {id: $tid}) RETURN t.tier",
            params={"tid": org_id},
        ).result_set
        tier = trows[0][0] if trows else "free"
    except Exception:
        tier = "free"

    ops_limit = _ops_allowance(tier)
    eligible = _overage_eligible(tier)

    overage_cost = None
    if eligible and ops_used > ops_limit:
        from tortoise.pricing import overage_price_per_10k
        overage_units = (ops_used - ops_limit + 9999) // 10000
        overage_cost = overage_units * overage_price_per_10k()

    return _view(ops_used, ops_limit, eligible, overage_cost)
