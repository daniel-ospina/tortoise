"""Tier limits loader — canonical source is product/pricing.json (decision 1d).

The epic's pricing structure (product/pricing.json) is the single source of
truth for tier limits: max_graphs_per_team, max_users_per_team, max_api_keys,
included_write_ops_per_month, max_graph_nodes, and overage eligibility. This
module loads it at import time (cached) and exposes lookup helpers so
hosted_api.py and sdk.py never hardcode limits.

NOT a tier field: max_orgs — multi-org is a user-level capability (per-org
billing; org creation is rate-limited for abuse, not tier-capped).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

_PRICING_PATH = os.environ.get(
    "TORTOISE_PRICING_PATH",
    str(Path(__file__).resolve().parent.parent / "product" / "pricing.json"),
)

_cache: dict | None = None

# Keys every tier must DECLARE. ⚠️ This does NOT mean `tier_limits` EXPOSES all
# of them: it returns a SUBSET. The two storage allowances are declared-only on
# purpose (see `storage_limits` — exposing them would arm a BLOCKED dimension in
# the allowance gate and answer HTTP 500 on every gated write). A tier missing
# any of these is a config drift bug and must fail loudly (KeyError), not
# silently degrade to None/unlimited (#750.8).
_REQUIRED_LIMIT_KEYS = (
    "max_graphs_per_team",
    "max_users_per_team",
    "max_api_keys",
    "included_write_ops_per_month",
    "max_graph_nodes",
    # #11758: the two purchasable storage dimensions. REQUIRED TO BE DECLARED — a
    # tier that publishes no storage allowance must fail LOUDLY here rather than
    # read as "unlimited storage, free", which is the false-PASS this tuple
    # exists to stop. `null` is an ALLOWED value (it is the recorded state for
    # the free/anon record allowance); a MISSING key is not.
    "max_postgres_storage_bytes",
    "max_graph_storage_mb",
)


#: The dimension names ``billing.storage.dimensions`` is keyed by. They are the
#: DECLARED registry names from ``tortoise.cost_dimensions.DIMENSIONS`` — not a
#: second vocabulary. The customer-facing words live beside them in ``label``.
#: Declared here as the artifact's contract; ``tests/test_pricing_storage_
#: dimensions.py`` pins them to ``allowance_gate.ALLOWANCE_KEYS`` (the keys) and
#: to ``cost_dimensions.by_name`` (the names, units), so the artifact, the loader,
#: the gate and the arithmetic cannot drift into four spellings.
#: ⚠ The pin is a TEST rather than a runtime import for IMPORT-WEIGHT, not because
#: a cycle makes it impossible — ``quota`` imports this module only inside
#: function bodies, so ``pricing -> allowance_gate`` would in fact import cleanly.
#: It is not taken because ``allowance_gate`` pulls in ``quota``; ``pricing`` is
#: imported by tools, graph scripts and the `pricing-artifact` CI parity check,
#: which must stay dependency-light. If that cost ever stops mattering, making
#: the import real is the better fix and this note should be deleted.
STORAGE_DIMENSIONS = ("postgres_storage", "graph_storage")

#: Decimal, and it is pinned ON PURPOSE. The two meters publish DIFFERENT units
#: (``postgres_storage`` in bytes, ``graph_storage`` in decimal MB), so a silent
#: MiB/decimal mix is the measured error class
#: ``docs/architecture/STORAGE-ARCHITECTURE.md`` records. One definition, one
#: conversion, no per-caller factor. The artifact restates it NUMERICALLY in
#: ``billing.storage.bytes_per_gb`` and a test asserts the two are EQUAL — not
#: that some prose mentions a number, which a contradictory sentence would pass.
BYTES_PER_GB = 1_000_000_000


def _load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    with open(_PRICING_PATH) as f:
        _cache = json.load(f)
    return _cache


def reload() -> None:
    """Drop the cache (tests / config changes)."""
    global _cache
    _cache = None


def tier_limits(tier: str) -> dict:
    """Return the limits dict for a tier (empty dict if unknown).

    Unknown tiers default to the Free limits so a legacy/missing tier never
    grants more than the baseline. #750.8: a loaded tier that is MISSING a
    required key raises KeyError at lookup time instead of silently returning
    None (= unlimited) on config drift.
    """
    data = _load()
    tiers = data.get("tiers", {})
    t = tiers.get(tier) or tiers.get("free", {})
    missing = [k for k in _REQUIRED_LIMIT_KEYS if k not in t]
    if missing:
        raise KeyError(
            f"pricing.json tier {tier!r} missing required limit keys: {missing}"
        )
    return {
        "max_graphs_per_team": t.get("max_graphs_per_team"),  # None = unlimited
        "max_users_per_team": t.get("max_users_per_team"),
        "max_api_keys": t.get("max_api_keys"),
        "included_write_ops_per_month": t.get("included_write_ops_per_month"),
        "max_graph_nodes": t.get("max_graph_nodes"),
        "overage": bool(t.get("overage", False)),
    }


def storage_limits(tier: str) -> dict:
    """Return a tier's two storage allowances, keyed by the GATE's own keys (#11758).

    ⛔ **DELIBERATELY NOT part of :func:`tier_limits`.** That dict is handed
    around as "the org's limits" and reaches ``quota.enforce_org_limit``; the
    allowance gate arms on *presence* — ``allowance_gate.enforce_allowances``
    does ``if dim.limit_key not in limits: continue``
    (``allowance_gate.py``:823). Both storage dimensions are currently
    **BLOCKED** there (``postgres_storage`` has no per-org byte meter — a
    per-org split would be an ALLOCATION, not a measurement; ``graph_storage``'s
    own contract forbids capping on the ledger figure because the HNSW index is
    under-reported 8.5x-21.7x). Putting either key into the dict every caller
    treats as "the limits" is therefore one ``{**tier_limits(t), ...}`` away
    from arming a blocked dimension and answering HTTP 500 on every gated
    write. ``quota.py`` states the intended state: *"INERT BY DEFAULT: no
    production resolver emits an ``ALLOWANCE_KEYS`` value"*. This accessor keeps
    that true while letting the artifact carry the values a future resolver
    will emit.

    Returns ``{<allowance_key>: value | None}`` for every STORAGE dimension —
    ``None`` meaning UNCONFIGURED (the gate's own reading of it), never 0-by-0.
    Unknown tiers fall back to free, exactly like :func:`tier_limits`.

    ⛔ FAIL-CLOSED ON A MISSING KEY, exactly like :func:`tier_limits`. This is
    the accessor the money arithmetic reads, so a tier that omits a storage key
    must raise here rather than read as UNCONFIGURED: unconfigured means
    UNLIMITED downstream (``metering.storage_overage_cost_usd`` then reports
    ``allowance_configured: False`` and a $0.00 overage), so a silently dropped
    key would bill $0 for unbounded hot capacity while every check stayed green.
    An explicit ``null`` still passes — that is the RECORDED free/anon state —
    only an ABSENT key raises.
    """
    data = _load()
    tiers = data.get("tiers", {})
    t = tiers.get(tier) or tiers.get("free", {})
    dims = storage_dimensions()
    missing = [dim["allowance_key"] for dim in dims.values()
               if dim["allowance_key"] not in t]
    if missing:
        raise KeyError(
            f"pricing.json tier {tier!r} missing required storage allowance "
            f"key(s): {missing} — an absent allowance reads as UNCONFIGURED "
            f"(= unlimited) in the overage arithmetic")
    return {dim["allowance_key"]: t[dim["allowance_key"]]
            for dim in dims.values()}


def storage_dimensions() -> dict:
    """The two purchasable storage dimensions, keyed by DECLARED name (#11758).

    Each entry carries the per-GB-month rate, the cost basis it is priced above,
    the allowance key that names its per-tier included amount, and the scope
    (``account`` — unlimited graphs, nothing per graph). Raises ``KeyError`` when
    the artifact is missing a declared dimension, so the artifact cannot quietly
    shrink the offer to one dimension.
    """
    data = _load()
    dims = data.get("billing", {}).get("storage", {}).get("dimensions", {})
    missing = [name for name in STORAGE_DIMENSIONS if name not in dims]
    if missing:
        raise KeyError(
            f"pricing.json billing.storage.dimensions missing declared "
            f"storage dimension(s): {missing}")
    return {name: dims[name] for name in STORAGE_DIMENSIONS}


def storage_overage_rate(dimension: str) -> float:
    """The sale rate for *dimension*, in USD per GB-month (decimal GB).

    Read from the artifact, never hardcoded — the whole point of moving the
    price of record into ``product/pricing.json``. Raises ``KeyError`` for an
    undeclared dimension rather than returning a default rate: a default rate
    would price a dimension nobody decided.
    """
    dims = storage_dimensions()
    if dimension not in dims:
        raise KeyError(
            f"{dimension!r} is not a declared storage dimension "
            f"(declared: {sorted(dims)})")
    return float(dims[dimension]["overage_price_usd_per_gb_month"])


def tier_price(tier: str) -> int:
    data = _load()
    tiers = data.get("tiers", {})
    t = tiers.get(tier) or tiers.get("free", {})
    return int(t.get("price_usd_monthly", 0))


def overage_price_per_10k() -> float:
    data = _load()
    return float(data.get("billing", {}).get("overage_price_per_10k", 5.0))


def overage_tiers() -> list[str]:
    data = _load()
    return list(data.get("billing", {}).get("overage_tiers", ["pro", "team"]))


def has_overage(tier: str) -> bool:
    return tier in overage_tiers()


def all_tiers() -> list[str]:
    data = _load()
    return list(data.get("tiers", {}).keys())


def allowance_reachability(tier: str, nodes_per_op: float) -> dict:
    """Can a tier actually DELIVER the write-op allowance it publishes? (#4495)

    The price book publishes two independent numbers per tier and the dashboard
    shows both in one list as entitlements
    (``website/apps/dashboard/src/pricing.js`` → ``"<cap>-node graph"`` and
    ``"<allowance> write ops/mo"``):

    - ``included_write_ops_per_month`` — a monthly **flow**, purchasable as
      overage on the paid tiers; and
    - ``max_graph_nodes`` — a permanent **stock**, enforced as a WRITE REFUSAL
      (``hosted_api.py``'s capture gate: ``count + est > max_points``).

    They were set with no designed relationship (``cap / allowance`` is 1.0–3.0
    across the published tiers), so the stock can refuse the writes the flow
    was sold for. The allowance is only deliverable when the cap can absorb the
    nodes those ops produce:

        cap >= allowance * nodes_per_op

    ``nodes_per_op`` (``r``) is the NET-NEW NON-EPISODIC NODE YIELD of one
    billed write op — the conversion between the two axes. It is not a
    constant, and which write path produced it decides the value:

    - a **single-node write** (``POST /v1/points``, ``create_object``,
      ``create_subject``, a one-point commit) bills one op and stores one node,
      so ``r == 1`` — deliverable at **every** published tier, because every
      ``cap / allowance`` is >= 1.0. A ratio of exactly 1.0 (free, anon) is the
      point of ZERO SLACK: the last one-node write exactly fills the graph;
    - a **capture / batch commit** stores a whole extracted set per op. Two
      live refusals recorded on #4495 estimated ``+549`` and ``+48`` nodes, so
      ``r`` is ~48–549 — one to two orders of magnitude above every published
      ratio (1.0–3.0).

    So the defect is NOT "the allowance is unreachable in any usage". Deliverability
    is a STEP FUNCTION of ``r``: fine up to ``r = cap / allowance``, then gone. The
    published ratios put that step at 1–3 nodes per write, and the primary
    documented usage (capturing conversations and extracting knowledge) sits at
    48–549 — a 16x–549x overshoot, so no tier can absorb more than a few per cent
    of its published monthly flow, and because the cap is permanent stock the
    unspendable share only grows.

    ``r = 0`` means a write that adds NO NET-NEW node (dedup re-commit,
    supersede-only delta, replay, hold commit): it consumes no stock, so it
    cannot bind the cap and 0 is REACHABLE, not an error. ⛔ But the LEDGER
    cannot certify a true zero: it also writes ``nodes_written = 0`` for write
    paths that simply never record a yield, so a measured ``0`` must be read as
    UNATTRIBUTED (``metering.get_write_op_yield`` reports that distinction) —
    passing a measured 0 in here as a true zero would manufacture a REACHABLE
    verdict from work that was never measured.

    Nothing here prices, caps, throttles or changes a value: it is the
    arithmetic, in one place, so the ratio is not re-derived by hand (three
    prior hand-derived tables on #4495 disagreed about their denominator).

    Returns ``{tier, allowance, cap, nodes_per_op, cap_per_allowance,
    reachable_ops, unreachable_share, required_cap, cap_shortfall, reachable}``.
    ``reachable_ops`` is derived FROM the verdict (never computed independently)
    so the fields cannot contradict the bottom line at the exact boundary, and
    it is capped at ``allowance``: the flow, not the stock, is the ceiling once
    the cap is large enough.
    """
    limits = tier_limits(tier)
    allowance = int(limits.get("included_write_ops_per_month") or 0)
    cap_raw = limits.get("max_graph_nodes")
    if cap_raw is None:
        # ``tier_limits`` documents None as "unlimited" for the OBJECT-COUNT
        # keys, and this ratio cannot be computed from an unlimited cap: there
        # is no number to divide the allowance by. ⛔ Do NOT restate that as
        # "a null cap is unrankable" — since #8946 an unlimited cap RANKS above
        # every finite one (``billing._tier_cap``/``_subscription_rank``); what
        # fails here is the ARITHMETIC, not the rank. Coercing it to 0 would
        # report the maximum possible shortfall as a fabricated finding, and
        # the capture gate would TypeError on the comparison anyway.
        raise ValueError(
            f"tier {tier!r} publishes no numeric node cap (max_graph_nodes is "
            "null = unlimited) — the cap/allowance ratio needs a finite cap")
    cap = int(cap_raw)
    r = float(nodes_per_op)
    if r < 0 or r != r or r == float("inf"):
        raise ValueError(
            f"nodes_per_op must be finite and >= 0, got {nodes_per_op!r}")
    if allowance <= 0:
        raise ValueError(f"tier {tier!r} publishes no write-op allowance")

    required_cap = allowance * r
    # A cap is an INTEGER node count, so the requirement is whole nodes: a
    # shortfall of 0.25 nodes is still the next node. Quantising here is what
    # stops the rendered table contradicting its own bottom line — before it,
    # r=1.00004 printed "0.00% unusable" and "cap needs to be 0 nodes larger"
    # beside "VERDICT: UNREACHABLE". The tolerance (not a bare ceil) absorbs
    # binary-float error at an exact boundary: 29/7*7 is 29.000000000000004,
    # and a naive ceil would call that 30.
    tolerance = 1e-9 * max(1.0, required_cap)
    required_cap_nodes = math.ceil(required_cap - tolerance)
    reachable = cap >= required_cap_nodes
    reachable_ops = allowance if reachable else min(allowance, cap / r)
    return {
        "tier": tier,
        "allowance": allowance,
        "cap": cap,
        "nodes_per_op": r,
        "cap_per_allowance": cap / allowance,
        "reachable_ops": reachable_ops,
        "unreachable_share": max(0.0, 1.0 - reachable_ops / allowance),
        "required_cap": float(required_cap_nodes),
        "cap_shortfall": 0.0 if reachable else float(required_cap_nodes - cap),
        "reachable": reachable,
    }


def hourly_backups_enabled(tier: str | None) -> bool:
    """True when pricing.json features.hourly_backups is truthy for *tier*.

    Derives the backup-tiers allowlist from the canonical pricing source
    so the gate can never drift from product/pricing.json again (#656).
    #2317: the flag is named for the ACTUAL delivered cadence (hourly driver
    sweep → RPO ≤1h typical / ≤2h worst) — the pre-launch registry-era
    ``daily_backups`` name understated it.
    Unknown tiers default to False (no backups entitlement).
    """
    if not tier:
        return False
    data = _load()
    t = data.get("tiers", {}).get(tier, {})
    # Strict boolean check: pricing.json uses the string "planned" to mark
    # features that are not yet live. bool("planned") is True, which would
    # wrongly enable the feature — only a real JSON `true` unlocks it.
    return t.get("features", {}).get("hourly_backups", False) is True
