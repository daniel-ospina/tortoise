"""Per-team write-op metering for overage billing (#681).

Design
~~~~~~
Metering records live as ``:MeteringRecord`` nodes in the FalkorDB **registry**
namespace (not team-specific graphs), keyed by ``(team_id, billing_period)``
where ``billing_period`` is a calendar-month string ``"YYYY-MM"``.

Why the registry graph?
- All billing state (Team nodes, subscription fields, WebhookEvent) already
  lives in the registry — metering is billing infrastructure, not user data.
- Lift-and-shift to Supabase (#669) is straightforward: one node label →
  one table.
- No cross-namespace queries needed — a single ``MERGE … SET …`` writes the
  record, and the dashboard/usage endpoint reads from the same namespace.

Period rollover is **lazy** (no cron): when a write arrives in a new month,
the period key changes → a new ``:MeteringRecord`` is MERGEd and incremented.
Previous-period records are frozen (no further increments). This avoids a
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
        team_id:   "team_abc123",
        period:    "2026-08",
        write_ops: 42,
        nodes_written: 12,
        updated_at: "2026-08-09T14:31:00.123Z"
    })

``nodes_written`` is the value-first commit cost driver (epic #909 §4.4/
W-4/PL4): +net-new non-episodic nodes per commit call (0 on hold commits;
supersede-only deltas exempt — R-14). It prevents the 25x per-node arbitrage
vs ``create_point`` while the billed unit stays ``write_ops`` (a commit call
is billed exactly once — PL4).

Atomic increment via FalkorDB Cypher::

    MERGE (m:MeteringRecord {team_id: $tid, period: $period})
    SET m.write_ops = coalesce(m.write_ops, 0) + $n,
        m.updated_at = $now

Threshold events
~~~~~~~~~~~~~~~~
When a team crosses 80% or 100% of its ``included_write_ops_per_month``
(from ``product/pricing.json``), a structured log event is emitted at
WARNING (80%) or ERROR (100%). This feeds into the existing alerting
pipeline (Resend + Telegram — see #310 billing notifications).

Thresholds are checked **post-increment** on every write for teams on
overage-eligible tiers (pro, team). Free and Solo tiers have no overage
and never trigger threshold events — they simply hit the hard quota limit.

Usage exposure
~~~~~~~~~~~~~~
``get_current_usage(team_id)`` returns ``{write_ops_used, write_ops_limit,
period, overage_eligible}`` for the current billing period. Wired into
``GET /v1/team`` (see ``TeamInfoResponse`` extension).

MCP writes
~~~~~~~~~~
MCP write tools call ``_safe`` which runs the write inside a try/except.
Metering is recorded **after** a successful write (no exception) and only
for quota-gated tools (the ``_QUOTA_GATED`` set). Stdio/selfhost mode
(with no team context) skips metering entirely.
"""
from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone

_logger = logging.getLogger(__name__)

# ── Period helpers ───────────────────────────────────────────────────────────

def _current_period() -> str:
    """Calendar month as ``"YYYY-MM"`` in UTC."""
    now = datetime.now(timezone.utc)
    return f"{now.year}-{now.month:02d}"


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

def record_write_ops(team_id: str, tier: str | None = None, n: int = 1,
                     nodes_written: int = 0) -> dict | None:
    """Increment the write-op counter for *team_id* in the current billing period.

    Args:
        team_id: Team identifier (required).
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
        for threshold checking, or None if the registry is unreachable
        (non-fatal — metering failures never block the write).

    Raises:
        Nothing — metering is best-effort. Failures are logged and swallowed.
    """
    if not team_id:
        return None
    period = _current_period()
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (
                get_control_plane, metering_increment,
            )
            write_ops = metering_increment(
                get_control_plane(), team_id, period, n,
                nodes_written=nodes_written)
            result = {
                "write_ops": write_ops,
                "nodes_written": nodes_written,
                "period": period,
                "ops_allowance": _ops_allowance(tier) if tier else 0,
                "overage_eligible": _overage_eligible(tier) if tier else False,
            }
            _check_thresholds(team_id, tier, result, n)
            return result
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {team_id: $tid, period: $period}) "
            "SET m.write_ops = coalesce(m.write_ops, 0) + $n, "
            "    m.nodes_written = coalesce(m.nodes_written, 0) + $nw, "
            "    m.updated_at = $now",
            params={"tid": team_id, "period": period, "n": n,
                    "nw": nodes_written, "now": now_iso},
        )
        rows = reg.query(
            "MATCH (m:MeteringRecord {team_id: $tid, period: $period}) "
            "RETURN m.write_ops, m.nodes_written",
            params={"tid": team_id, "period": period},
        ).result_set
        write_ops = int(rows[0][0]) if rows else n
        nodes_written_total = int(rows[0][1]) if rows else nodes_written
    except Exception as e:
        _logger.warning(
            "metering increment failed (non-fatal): team=%s period=%s error=%s",
            team_id, period, e,
        )
        return None

    result = {
        "write_ops": write_ops,
        "nodes_written": nodes_written_total,
        "period": period,
        "ops_allowance": _ops_allowance(tier) if tier else 0,
        "overage_eligible": _overage_eligible(tier) if tier else False,
    }

    # Threshold events
    _check_thresholds(team_id, tier, result, n)

    return result


# ── Threshold events ────────────────────────────────────────────────────────

_THRESHOLD_PCT = [80, 100]

# Track which thresholds have already been crossed to avoid duplicate events
# per period. In-memory only — resets on process restart (acceptable for v1;
# a duplicate alert on restart is better than missing an alert entirely).
_thresholds_fired: set[tuple[str, str, int]] = set()


def _check_thresholds(
    team_id: str,
    tier: str | None,
    result: dict,
    n: int = 1,
) -> None:
    """Emit log events if write_ops crossed an 80% or 100% threshold.

    Only fires for overage-eligible tiers (pro, team). Each threshold fires
    at most once per (team_id, period, pct) per process lifetime.
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
            key = (team_id, period, pct)
            if key in _thresholds_fired:
                continue
            _thresholds_fired.add(key)
            level = logging.WARNING if pct == 80 else logging.ERROR
            _logger.log(
                level,
                "write-op threshold %d%% reached: team=%s period=%s count=%d/%d",
                pct, team_id, period, write_ops, allowance,
            )


def _reset_thresholds_for_tests() -> None:
    """Clear the in-memory threshold tracker (test helper only)."""
    _thresholds_fired.clear()


# ── Usage query ─────────────────────────────────────────────────────────────

def get_current_usage(team_id: str) -> dict:
    """Return write-op usage for *team_id* in the current billing period.

    Returns:
        ``{write_ops_used: int, write_ops_limit: int, period: str,
           overage_eligible: bool, overage_cost_usd: float | None}``

    ``overage_cost_usd`` is the cost of ops BEYOND the included allowance
    (rounded up to the nearest 10k block, $5/block). None if under allowance
    or not eligible.
    """
    period = _current_period()
    ops_used = 0
    if _supabase_mode():
        from tortoise.supabase_control import (
            get_control_plane, metering_get, team_tier,
        )
        try:
            cp = get_control_plane()
            ops_used = metering_get(cp, team_id, period)
            tier = team_tier(cp, team_id) or "free"
            ops_limit = _ops_allowance(tier)
            eligible = _overage_eligible(tier)
            overage_cost = None
            if eligible and ops_used > ops_limit:
                from tortoise.pricing import overage_price_per_10k
                overage_units = (ops_used - ops_limit + 9999) // 10000
                overage_cost = overage_units * overage_price_per_10k()
            return {
                "write_ops_used": ops_used,
                "write_ops_limit": ops_limit,
                "period": period,
                "overage_eligible": eligible,
                "overage_cost_usd": overage_cost,
            }
        except Exception as e:
            # Metering is best-effort by contract — a control-plane blip must
            # never 500 the /v1/team hot path (#923). Degrade to the
            # free-tier zero-usage view, mirroring the registry path below.
            _logger.warning(
                "metering usage query failed: team=%s period=%s error=%s",
                team_id, period, e,
            )
            return {
                "write_ops_used": 0,
                "write_ops_limit": _ops_allowance("free"),
                "period": period,
                "overage_eligible": False,
                "overage_cost_usd": None,
            }
    try:
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (m:MeteringRecord {team_id: $tid, period: $period}) "
            "RETURN m.write_ops",
            params={"tid": team_id, "period": period},
        ).result_set
        if rows:
            ops_used = int(rows[0][0])
    except Exception as e:
        _logger.warning(
            "metering usage query failed: team=%s period=%s error=%s",
            team_id, period, e,
        )

    # Determine tier from the Team node for allowance
    try:
        sdk2 = _reg_sdk()
        reg2 = sdk2._get_registry()
        trows = reg2.query(
            "MATCH (t:Team {id: $tid}) RETURN t.tier",
            params={"tid": team_id},
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

    return {
        "write_ops_used": ops_used,
        "write_ops_limit": ops_limit,
        "period": period,
        "overage_eligible": eligible,
        "overage_cost_usd": overage_cost,
    }
