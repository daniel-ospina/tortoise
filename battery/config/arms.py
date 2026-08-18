"""Arms loader (config/arms.yaml) — per-arm config + cost constants.

Each arm entry: {arm_id, adapter (battery.arms.<name>), config {},
price_per_1k_usd, expected_tokens_per_episode} — the per-arm constants the
budget formula uses (scope DD12/DD16). #1408 adds adapters; the schema is
locked here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from battery.exceptions import ConfigError

DEFAULT_PRICE_PER_1K = 0.0
DEFAULT_TOKENS_PER_EPISODE = 500


@dataclass(frozen=True)
class ArmConfig:
    arm_id: str
    adapter: str
    config: dict[str, Any] = field(default_factory=dict)
    price_per_1k_usd: float = DEFAULT_PRICE_PER_1K
    expected_tokens_per_episode: int = DEFAULT_TOKENS_PER_EPISODE

    def estimated_cost_usd(self, n_episodes: int) -> float:
        """Per-arm episode cost estimate (scope DD12 formula)."""
        return (n_episodes * self.expected_tokens_per_episode
                * self.price_per_1k_usd / 1000.0)


def load_arms(path: str | Path) -> dict[str, ArmConfig]:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"arms file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries = raw.get("arms") or []
    if not isinstance(entries, list):
        raise ConfigError(f"arms {p}: 'arms' must be a list")
    out: dict[str, ArmConfig] = {}
    for e in entries:
        try:
            arm_id = str(e["arm_id"])
            adapter = str(e["adapter"])
        except (KeyError, TypeError) as ex:
            raise ConfigError(f"arms entry missing arm_id/adapter: {e}") from ex
        if arm_id in out:
            raise ConfigError(f"duplicate arm_id {arm_id!r}")
        out[arm_id] = ArmConfig(
            arm_id=arm_id,
            adapter=adapter,
            config=dict(e.get("config") or {}),
            price_per_1k_usd=float(e.get("price_per_1k_usd", DEFAULT_PRICE_PER_1K)),
            expected_tokens_per_episode=int(
                e.get("expected_tokens_per_episode", DEFAULT_TOKENS_PER_EPISODE)),
        )
    return out
