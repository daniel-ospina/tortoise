"""Corpus loader (config/corpus.yaml) — scenario schema + sealed-gold boundary.

Schema: id, tier (probe|stream|differential), family, task_type, attack_type,
split (train|waves|held_out), prompt_pack (list of {role, content} turns),
gold_ref (path + sha256 into the gold store), k (injection-turn), optional
contradiction_pairs (list of {claim_a, claim_b, injection_turn}) and
evidence_scripts (list).

Validation is TYPE-ONLY (field types/enums); tier-mandatory enforcement is
#1407-owned. Golds are verified by sha256 at load and fail closed (exit-1
class); gold text is readable ONLY via Scenario.golds() — never handed to
the episode/agent context (sealed-gold boundary, scope DD2).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

from battery.enums import Tier
from battery.exceptions import ConfigError, EmptyCorpus, GoldVerificationError

_TIERS = {t.value for t in Tier}
_SPLITS = {"train", "waves", "held_out"}
_ATTACK_TYPES = {"", "poisoned", "sybil", "echo_chamber", "flapping", "anchoring"}


@dataclass(frozen=True)
class GoldRef:
    """Reference into the sealed gold store (never inline gold text).

    ``path`` is resolved to an absolute file path at load time so
    ``Scenario.golds()`` can read the sealed text on demand.
    """

    path: str
    sha256: str
    abs_path: str = ""

    def read_text(self) -> str:
        return Path(self.abs_path or self.path).read_text(encoding="utf-8")


@dataclass(frozen=True)
class ContradictionPair:
    """Planted contradiction (R1/L4). k = injection turn, pinned per pair."""

    claim_a: str
    claim_b: str
    injection_turn: int


@dataclass(frozen=True)
class Scenario:
    """One scenario (plan §4 entity model, in YAML form)."""

    id: str
    tier: Tier
    family: str
    task_type: str
    attack_type: str
    split: str
    prompt_pack: tuple[dict[str, str], ...]
    gold_ref: GoldRef | None
    k: int | None
    contradiction_pairs: tuple[ContradictionPair, ...] = ()
    evidence_scripts: tuple[str, ...] = ()

    #: Sealed gold text — the ONLY scoring-side access surface. The episode
    #: context handed to arms/agents never contains gold text.
    def golds(self) -> tuple[str, ...]:
        if self.gold_ref is None:
            return ()
        return (self.gold_ref.read_text(),)

    def to_episode_context(self) -> dict[str, Any]:
        """Reader/arm-visible projection — gold text is NEVER included."""
        return {
            "id": self.id,
            "tier": self.tier.value,
            "family": self.family,
            "task_type": self.task_type,
            "attack_type": self.attack_type,
            "prompt_pack": list(self.prompt_pack),
            "k": self.k,
            "contradiction_pairs": [
                {"claim_a": p.claim_a, "claim_b": p.claim_b,
                 "injection_turn": p.injection_turn}
                for p in self.contradiction_pairs
            ],
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_scenario(raw: dict[str, Any], gold_base: Path) -> Scenario:
    """Validate (type-only) + coerce one raw scenario dict."""
    try:
        sid = str(raw["id"])
        tier = Tier(raw["tier"])
        family = str(raw["family"])
        task_type = str(raw.get("task_type", ""))
        attack_type = str(raw.get("attack_type", ""))
        split = str(raw.get("split", "train"))
        if split not in _SPLITS:
            raise ConfigError(f"scenario {sid}: invalid split {split!r}")
        pp = raw.get("prompt_pack", [])
        if not isinstance(pp, list):
            raise ConfigError(f"scenario {sid}: prompt_pack must be a list")
        prompt_pack = tuple(
            {"role": str(t.get("role", "user")), "content": str(t.get("content", ""))}
            for t in pp
        )
        k = raw.get("k")
        if k is not None and not isinstance(k, int):
            raise ConfigError(f"scenario {sid}: k must be an int")
        # Gold ref: relative path resolved under the gold store root.
        gold_ref = None
        gr = raw.get("gold_ref")
        if gr is not None:
            if not isinstance(gr, dict) or "path" not in gr or "sha256" not in gr:
                raise ConfigError(f"scenario {sid}: gold_ref must be {{path, sha256}}")
            gold_path = (gold_base / gr["path"]).resolve()
            expected = str(gr["sha256"]).lower()
            if not gold_path.is_file():
                raise GoldVerificationError(
                    f"scenario {sid}: gold file missing: {gold_path}")
            actual = hashlib.sha256(gold_path.read_bytes()).hexdigest()
            if actual != expected:
                raise GoldVerificationError(
                    f"scenario {sid}: gold sha256 mismatch for {gold_path} "
                    f"(expected {expected}, got {actual})")
            gold_ref = GoldRef(path=gr["path"], sha256=expected,
                               abs_path=str(gold_path))
        pairs = tuple(
            ContradictionPair(
                claim_a=str(p["claim_a"]), claim_b=str(p["claim_b"]),
                injection_turn=int(p.get("injection_turn", k or 0)))
            for p in raw.get("contradiction_pairs", [])
        )
        scripts = tuple(str(s) for s in raw.get("evidence_scripts", []))
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError) as e:
        raise ConfigError(f"scenario schema violation: {e}") from e
    return Scenario(
        id=sid, tier=tier, family=family, task_type=task_type,
        attack_type=attack_type, split=split, prompt_pack=prompt_pack,
        gold_ref=gold_ref, k=k, contradiction_pairs=pairs,
        evidence_scripts=scripts,
    )


def load_corpus(path: str | Path, *, gold_base: str | Path | None = None,
                ) -> list[Scenario]:
    """Load + validate the corpus. Raises EmptyCorpus on zero scenarios and
    GoldVerificationError on gold mismatch/missing (exit-1 class at dispatch).
    """
    from battery.exceptions import ConfigError  # noqa: PLC0415 (import guard)
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"corpus file not found: {p}")
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    scenarios_raw = raw.get("scenarios") or []
    if not isinstance(scenarios_raw, list):
        raise ConfigError(f"corpus {p}: 'scenarios' must be a list")
    base = Path(gold_base) if gold_base is not None else p.parent.parent / "golds"
    scenarios = [_coerce_scenario(s, base) for s in scenarios_raw]
    if not scenarios:
        raise EmptyCorpus(f"corpus {p} has 0 scenarios — refusing to start")
    return scenarios


def scenarios_by_tier(scenarios: Iterable[Scenario], tier: Tier | None) -> list[Scenario]:
    """Filter scenarios by tier (None = all), preserving file order."""
    if tier is None:
        return list(scenarios)
    return [s for s in scenarios if s.tier is tier]
