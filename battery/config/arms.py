"""Arms loader (config/arms.yaml) — per-arm config + cost constants.

Each arm entry: {arm_id, adapter (battery.arms.<name>), config {},
price_per_1k_usd, expected_tokens_per_episode, model_pin, temperature} —
the per-arm constants the budget formula uses (scope DD12/DD16) plus the
protocol-hash inputs (#2284 Task 6: model_pin + temperature feed the parity
protocol hash; #2292 re-locks the measured pins). #1408 adds adapters; the
schema is locked here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from battery.exceptions import ConfigError

DEFAULT_PRICE_PER_1K = 0.0
DEFAULT_TOKENS_PER_EPISODE = 500
#: Checked-in FLASH-CLASS placeholders (#2284 Task 6): arms.yaml carries
#: model_pin/temperature for the parity protocol hash; sibling #2292
#: re-locks the measured pins over these placeholders before real-run
#: parity. Defaults keep legacy arms.yaml files (and tmp fixtures without
#: the keys) loading identically — additive-only schema extension.
DEFAULT_MODEL_PIN = "flash-class-placeholder"
DEFAULT_TEMPERATURE = 0.0


@dataclass(frozen=True)
class ArmConfig:
    arm_id: str
    adapter: str
    config: dict[str, Any] = field(default_factory=dict)
    price_per_1k_usd: float = DEFAULT_PRICE_PER_1K
    expected_tokens_per_episode: int = DEFAULT_TOKENS_PER_EPISODE
    #: Protocol-hash inputs (#2284 Task 6; parity protocol_hash) — the ONLY
    #: population path is load_arms parsing arms.yaml (no other writer).
    model_pin: str = DEFAULT_MODEL_PIN
    temperature: float = DEFAULT_TEMPERATURE

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
            model_pin=str(e.get("model_pin", DEFAULT_MODEL_PIN)),
            temperature=float(e.get("temperature", DEFAULT_TEMPERATURE)),
        )
    return out


def _registry_key_for_pin(pin: str) -> str | None:
    """Map a full model slug (arms.yaml ``model_pin``) to the
    model_adapters.MODELS registry key. The registry's key->id table
    covers extractor keys; judge/reader-only keys pass through id==key."""
    from tortoise import model_adapters
    id_to_key: dict[str, str] = {}
    table = getattr(model_adapters, "_REGISTRY_KEY_TO_ID", None) or {}
    for k, v in table.items():
        id_to_key.setdefault(str(v), k)
    for k in model_adapters.MODELS:
        id_to_key.setdefault(k, k)
    return id_to_key.get(pin)


def resolve_pinned_model(pin: str):
    """Resolve a pinned model slug to the model_adapters registry factory
    instance (decision (a): same model across arms, temp 0, UNCAPPED).
    Refuses the placeholder sentinel and unknown slugs (ConfigError) — the
    real-run pre-flight never lets an unpinned/unresolvable model run."""
    if not pin or pin == DEFAULT_MODEL_PIN:
        raise ConfigError(
            f"model pin {pin!r} is the flash-class placeholder sentinel — "
            f"arms.yaml must carry a measured concrete pin before any "
            f"real run (sibling #2292 re-lock)")
    key = _registry_key_for_pin(pin)
    if key is None:
        raise ConfigError(
            f"unknown model pin {pin!r} — no model_adapters.MODELS entry "
            f"resolves it (known slugs: "
            f"deepseek/deepseek-v4-flash, qwen/qwen3.8-max, ...)")
    from tortoise import model_adapters
    try:
        return model_adapters.MODELS[key]()
    except KeyError as e:  # pragma: no cover — registry drift guard
        raise ConfigError(
            f"model pin {pin!r}: registry key {key!r} missing from MODELS"
        ) from e


def token_table_hash(arms: dict[str, ArmConfig]) -> str:
    """Canonical serialization of the per-arm token rows + their measured
    source — the reviewable-change hash for cost constants (#2292 Task 6,
    coordination n1): a re-lock of any arm's expected_tokens_per_episode
    drifts the hash, so a silent budget re-lock is impossible. The hash
    joins the artifact provenance + ``calibrate --print`` surface."""
    import hashlib
    lines = []
    for arm_id in sorted(arms):
        ac = arms[arm_id]
        lines.append(f"{arm_id}|{ac.expected_tokens_per_episode}")
    src = ";".join(lines)
    return hashlib.sha256(src.encode("utf-8")).hexdigest()[:16]
