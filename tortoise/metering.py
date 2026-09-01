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

#: #1987 Task 6: per-team increment serialization — the MERGE+coalesce
#: increment is atomic per statement on server-mode FalkorDB, but embedded
#: FalkorDBLite connections race the read-modify-write (the pre-existing
#: write-op path has the same shape); the ask meter closes it in-process
#: with a per-team lock (cross-process races remain possible and are
#: documented best-effort, mirroring the per-process budget bucket).
import threading as _threading  # noqa: E402
from weakref import WeakValueDictionary as _WeakValueDictionary  # noqa: E402

#: Per-team increment-serialization lock registry — BOUNDED by construction:
#: a WeakValueDictionary keeps each lock alive only while some thread holds
#: it (a released lock with no holder is GC'd), so a fresh team id never leaks
#: a permanent registry entry (the ask build-lock finding's mirror).
_ask_meter_locks: _WeakValueDictionary = _WeakValueDictionary()
_ask_meter_locks_guard = _threading.Lock()


def _ask_meter_lock(team_id: str) -> _threading.Lock:
    with _ask_meter_locks_guard:
        lock = _ask_meter_locks.get(team_id)
        if lock is None:
            lock = _threading.Lock()
            _ask_meter_locks[team_id] = lock
        return lock

# ── Period helpers ───────────────────────────────────────────────────────────

def _current_period() -> str:
    """Calendar month as ``"YYYY-MM"`` in UTC."""
    now = datetime.now(timezone.utc)  # noqa: UP017
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
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
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

#: Family prefixes that meter at the STRONG rates (the model_adapters
#: ``_SPEC_FAMILY_PROVIDERS`` openrouter-only families — keep in sync).
_ASK_STRONG_FAMILIES = frozenset({"qwen", "upstage", "anthropic"})


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
    if family in _ASK_STRONG_FAMILIES:
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
    "selfhost" is NEVER the exemption key — a hosted team with the raw id
    "selfhost" is legal and MUST record usage, P1-4)."""
    from tortoise.transport import _selfhost_transport
    return _selfhost_transport.get()


def record_ask_usage(team_id: str | None, tier: str | None = None, *,
                     calls: int = 1, tokens_in: int = 0, tokens_out: int = 0,
                     cost_usd: float = 0.0,
                     _selfhost_transport: bool = False) -> dict | None:
    """Record a per-query ask usage increment (#1987 Task 6).

    Best-effort, non-fatal (metering failures never block the answer).
    Extends the ``:MeteringRecord`` (registry) with additive fields
    ``ask_calls``/``ask_tokens_in``/``ask_tokens_out``/``ask_cost_usd`` via
    the MERGE+coalesce pattern (mirrors ``record_write_ops``); Supabase mode
    routes through the ``metering_increment_ask`` seam.

    Exemptions (no record, zero writes): ``not team_id`` (stdio/None) OR the
    selfhost-transport ContextVar (``_selfhost_transport`` — set True ONLY
    by the selfhost HTTP MCP transport; the SELFHOST_TEAM_ID value is never
    the exemption key). ``tier`` stays None for the ask lane (tier-based ask
    budgets are OUT of v1).

    Returns a dict summary or None (exempt/no-op/failure).
    """
    if not team_id or _selfhost_transport or _selfhost_transport_active():
        return None
    period = _current_period()
    now_iso = datetime.now(timezone.utc).isoformat()  # noqa: UP017
    with _ask_meter_lock(team_id):
        return _record_ask_usage_locked(team_id, period, now_iso,
                                        calls=calls, tokens_in=tokens_in,
                                        tokens_out=tokens_out,
                                        cost_usd=cost_usd)


def _record_ask_usage_locked(team_id: str, period: str, now_iso: str, *,
                             calls: int, tokens_in: int, tokens_out: int,
                             cost_usd: float) -> dict | None:
    """The serialized increment body (under the per-team lock — embedded
    concurrency-safe)."""
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_increment_ask,
            )
            metering_increment_ask(get_control_plane(), team_id, period,
                                   calls=calls, tokens_in=tokens_in,
                                   tokens_out=tokens_out, cost_usd=cost_usd)
            return {"period": period, "ask_calls": calls,
                    "ask_tokens_in": tokens_in,
                    "ask_tokens_out": tokens_out,
                    "ask_cost_usd": cost_usd}
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        reg.query(
            "MERGE (m:MeteringRecord {team_id: $tid, period: $period}) "
            "SET m.ask_calls = coalesce(m.ask_calls, 0) + $calls, "
            "    m.ask_tokens_in = coalesce(m.ask_tokens_in, 0) + $tin, "
            "    m.ask_tokens_out = coalesce(m.ask_tokens_out, 0) + $tout, "
            "    m.ask_cost_usd = coalesce(m.ask_cost_usd, 0) + $cost, "
            "    m.updated_at = $now",
            params={"tid": team_id, "period": period, "calls": calls,
                    "tin": tokens_in, "tout": tokens_out, "cost": cost_usd,
                    "now": now_iso},
        )
        return {"period": period, "ask_calls": calls,
                "ask_tokens_in": tokens_in, "ask_tokens_out": tokens_out,
                "ask_cost_usd": cost_usd}
    except Exception as e:
        _logger.warning(
            "ask metering increment failed (non-fatal): team=%s period=%s "
            "error=%s", team_id, period, e,
        )
        return None


def get_ask_usage(team_id: str) -> dict:
    """Ask usage for *team_id* in the current billing period (#1987 Task 6).

    Returns ``{ask_calls, ask_tokens_in, ask_tokens_out, ask_cost_usd}`` for
    the team's current period — ZEROS for a team with no ask records yet (a
    successful read returning NO row is not an error; the MERGE only creates
    the record on the first write — P2-14). Read failures degrade to the
    zero-usage view (never 500).
    """
    period = _current_period()
    zeros = {"ask_calls": 0, "ask_tokens_in": 0, "ask_tokens_out": 0,
             "ask_cost_usd": 0.0}
    if not team_id:
        return {**zeros, "period": period}
    try:
        if _supabase_mode():
            from tortoise.supabase_control import (  # noqa: I001
                get_control_plane, metering_get_usage,
            )
            row = metering_get_usage(get_control_plane(), team_id, period)
            return {**zeros, **{k: row.get(k, 0) for k in zeros},
                    "period": period}
        sdk = _reg_sdk()
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (m:MeteringRecord {team_id: $tid, period: $period}) "
            "RETURN m.ask_calls, m.ask_tokens_in, m.ask_tokens_out, "
            "m.ask_cost_usd",
            params={"tid": team_id, "period": period},
        ).result_set
        if not rows:
            return {**zeros, "period": period}
        return {
            "ask_calls": int(rows[0][0] or 0),
            "ask_tokens_in": int(rows[0][1] or 0),
            "ask_tokens_out": int(rows[0][2] or 0),
            "ask_cost_usd": float(rows[0][3] or 0.0),
            "period": period,
        }
    except Exception as e:
        _logger.warning(
            "ask usage query failed (degrading to zero view): team=%s "
            "period=%s error=%s", team_id, period, e,
        )
        return {**zeros, "period": period}


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
        from tortoise.supabase_control import (  # noqa: I001
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
