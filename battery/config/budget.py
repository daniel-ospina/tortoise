"""Budget loader (config/budget.yaml) — the cost guard (scope DD12).

The FULL-corpus estimate gates: the runner refuses to start when estimated
cost exceeds budget OR --max-episodes > budget.max_episodes (budget wins).
Over budget → operational error (exit 1), before any episode runs.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from battery.exceptions import ConfigError

DEFAULT_MAX_EPISODES = 1000
DEFAULT_MAX_COST_USD = 50.0


@dataclass(frozen=True)
class BudgetConfig:
    max_episodes: int = DEFAULT_MAX_EPISODES
    max_estimated_cost_usd: float = DEFAULT_MAX_COST_USD

    def over_budget(self, *, n_episodes: int, estimated_cost_usd: float,
                    requested_max_episodes: int | None = None) -> str | None:
        """Return a human-readable refusal reason, or None when in budget.

        ``requested_max_episodes`` is the CLI --max-episodes flag; the
        budget's max_episodes takes precedence (budget wins).
        """
        cap = min(requested_max_episodes, self.max_episodes) \
            if requested_max_episodes is not None else self.max_episodes
        if n_episodes > cap:
            return (f"episodes ({n_episodes}) exceed the budget cap "
                    f"({cap}; budget.max_episodes={self.max_episodes})")
        if estimated_cost_usd > self.max_estimated_cost_usd:
            return (f"estimated cost ${estimated_cost_usd:.4f} exceeds budget "
                    f"${self.max_estimated_cost_usd:.4f}")
        return None


def load_budget(path: str | Path) -> BudgetConfig:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"budget file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    try:
        max_episodes = int(raw.get("max_episodes", DEFAULT_MAX_EPISODES))
        max_cost = float(raw.get("max_estimated_cost_usd", DEFAULT_MAX_COST_USD))
    except (TypeError, ValueError) as e:
        raise ConfigError(f"budget {p}: invalid numeric field: {e}") from e
    if max_episodes < 1 or max_cost < 0:
        raise ConfigError(f"budget {p}: caps must be positive")
    return BudgetConfig(max_episodes=max_episodes, max_estimated_cost_usd=max_cost)


def estimate_cost(arm: "ArmConfig", n_episodes: int) -> float:  # noqa: F821, UP037
    """Scope DD12 formula: n_episodes × expected_tokens × price_per_1k."""
    return arm.estimated_cost_usd(n_episodes)
