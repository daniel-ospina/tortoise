"""Tier limits loader — canonical source is product/pricing.json (decision 1d).

The epic's pricing structure (product/pricing.json) is the single source of
truth for tier limits: max_graphs_per_team, max_users_per_team, max_api_keys,
included_write_ops_per_month, max_graph_nodes, and overage eligibility. This
module loads it at import time (cached) and exposes lookup helpers so
hosted_api.py and sdk.py never hardcode limits.

NOT a tier field: max_teams — multi-team is a user-level capability (per-team
billing; team creation is rate-limited for abuse, not tier-capped).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_PRICING_PATH = os.environ.get(
    "TORTOISE_PRICING_PATH",
    str(Path(__file__).resolve().parent.parent / "product" / "pricing.json"),
)

_cache: dict | None = None


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
    grants more than the baseline.
    """
    data = _load()
    tiers = data.get("tiers", {})
    t = tiers.get(tier) or tiers.get("free", {})
    return {
        "max_graphs_per_team": t.get("max_graphs_per_team"),  # None = unlimited
        "max_users_per_team": t.get("max_users_per_team"),
        "max_api_keys": t.get("max_api_keys"),
        "included_write_ops_per_month": t.get("included_write_ops_per_month"),
        "max_graph_nodes": t.get("max_graph_nodes"),
        "overage": bool(t.get("overage", False)),
    }


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
