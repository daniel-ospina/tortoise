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
from typing import Any, Iterable  # noqa: UP035

import yaml

from battery.enums import Tier
from battery.exceptions import ConfigError, EmptyCorpus, GoldVerificationError

_TIERS = {t.value for t in Tier}
_SPLITS = {"train", "waves", "held_out", "wave-1", "wave-2", "wave-3"}
# wave-N aliases: schema.py SPLITS uses explicit wave-1..3; "waves" kept for
# backward compat with the #1406 smoke corpus.
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
    """One scenario (plan §4 entity model, in YAML form).

    Supports both the #1406 smoke schema (prompt_pack + gold_ref) and the
    #1407 production schema (prompt {system,turns,question} + inline gold) —
    the loader normalizes both to this record; the reader path never sees
    gold text (to_episode_context).

    #2284 T2 loader fidelity: authored semantic content that was previously
    DROPPED at conversion is now preserved TYPED — ``question`` (stored
    separately from prompt_pack, never reconstructed by position),
    ``session_scripts`` (per-session {session, turns, question} tuples),
    ``evidence_tiers`` (authored {tier, claim, valence} triples), ``drift``
    (real shape {decision, offsets}), ``retraction`` (ret-* carries its ONLY
    k inside it — no top-level k), ``graph_script`` (a DICT {nodes,
    nand_edges, contested_pair} — never a str). ``_coerce_scenario`` raises
    ``ConfigError`` when any of these is present-but-empty (per-field
    survival lock — no silent drop).
    """

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
    #: Inline gold (production schema) — scorer-side accessor only.
    inline_gold: str = ""

    #: Top-level prompt.question (production schema). Stored SEPARATELY from
    #: prompt_pack — the flattened question turn is REMOVED from prompt_pack
    #: (prompt_pack = turns only) so the reader projection never
    #: double-renders it.
    question: str = ""
    #: Typed preservation of authored semantic content (#2284 T2).
    session_scripts: tuple[dict[str, Any], ...] = ()
    evidence_tiers: tuple[dict[str, Any], ...] = ()
    drift: dict[str, Any] = field(default_factory=dict)
    graph_script: dict[str, Any] = field(default_factory=dict)
    retraction: dict[str, Any] = field(default_factory=dict)
    #: Structured (non-str) gold expected value preserved from the authored
    #: YAML — list/dict golds (R4's real defeat conditions) must NOT be
    #: str-coerced away: the derive pass reads the typed list into the log
    #: (the str repr would make a probe iterate characters). Scorer-side
    #: accessor only — never rendered agent-side, never in to_render_dict.
    structured_gold: Any = None

    #: Sealed gold text — the ONLY scoring-side access surface. The episode
    #: context handed to arms/agents never contains gold text.
    def golds(self) -> tuple[str, ...]:
        if self.gold_ref is not None:
            return (self.gold_ref.read_text(),)
        if self.inline_gold:
            return (self.inline_gold,)
        return ()

    def to_render_dict(self) -> dict[str, Any]:
        """Corpus.json-shaped projection for render_reader_prompt — the ONLY
        agent-visible renderer (no second renderer; #2284 T2).

        ``prompt`` is re-nested ``{system, turns, question, session_scripts}``
        exactly as corpus_loader consumes it, and the authored semantic fields
        ride along TOP-LEVEL as authored (``evidence_tiers``, ``drift``,
        ``retraction``, ``graph_script``). Scorer metadata can never appear:
        Scenario holds typed fields only (no gold/hostile/gold_sha256).
        """
        system = "\n".join(
            str(t["content"]) for t in self.prompt_pack
            if t.get("role") == "system")
        turns = [dict(t) for t in self.prompt_pack if t.get("role") != "system"]
        prompt: dict[str, Any] = {
            "system": system,
            "turns": turns,
            "question": self.question,
        }
        if self.session_scripts:
            prompt["session_scripts"] = [dict(s) for s in self.session_scripts]
        out: dict[str, Any] = {
            "id": self.id,
            "tier": self.tier.value,
            "family": self.family,
            "task_type": self.task_type,
            "attack_type": self.attack_type,
            "split": self.split,
            "k": self.k,
            "prompt": prompt,
        }
        if self.evidence_tiers:
            out["evidence_tiers"] = [dict(e) for e in self.evidence_tiers]
        if self.drift:
            out["drift"] = dict(self.drift)
        if self.retraction:
            out["retraction"] = dict(self.retraction)
        if self.graph_script:
            out["graph_script"] = dict(self.graph_script)
        return out

    def to_episode_context(self) -> dict[str, Any]:
        """Reader/arm-visible projection — the SINGLE render rule (#2284 T2):
        ``render = render_reader_prompt(to_render_dict())`` — the run-path
        Scenario surface is exactly what corpus_loader renders for the
        dict-typed reader path. Gold text is NEVER included; ``retrieved``
        Memories are filled by the arm at run time — never scenario content.
        """
        from battery.config.corpus_loader import render_reader_prompt
        return {
            "id": self.id,
            "tier": self.tier.value,
            "family": self.family,
            "task_type": self.task_type,
            "attack_type": self.attack_type,
            "split": self.split,
            "render": render_reader_prompt(self.to_render_dict()),
            "retrieved": [],
        }


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_dict_list(value: Any, sid: str, label: str) -> tuple[dict[str, Any], ...]:
    """Non-empty list of dicts -> tuple of copied dicts (#2284 T2 fidelity).

    ``None`` (absent) -> (). A PRESENT-but-empty/invalid value raises
    ``ConfigError`` — the per-field survival lock: content that was authored
    must never silently coerce to an empty tuple (the old drop point).
    """
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise ConfigError(
            f"scenario {sid}: {label} present but empty/invalid — refusing a "
            "silent drop (loader-fidelity lock)")
    out: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ConfigError(f"scenario {sid}: {label} entries must be dicts")
        out.append(dict(item))
    return tuple(out)


def _coerce_dict(value: Any, sid: str, label: str) -> dict[str, Any]:
    """Non-empty dict -> copy (#2284 T2 fidelity — same present-but-empty
    lock as ``_coerce_dict_list``)."""
    if value is None:
        return {}
    if not isinstance(value, dict) or not value:
        raise ConfigError(
            f"scenario {sid}: {label} present but empty/invalid — refusing a "
            "silent drop (loader-fidelity lock)")
    return dict(value)


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
        pr: dict[str, Any] = {}
        pp = raw.get("prompt_pack", [])
        if not pp and "prompt" in raw:
            # #1407 production schema: prompt {system, turns, question}.
            pr = raw["prompt"]
            if not isinstance(pr, dict):
                raise ConfigError(f"scenario {sid}: prompt must be a dict")
            pp = []
            if pr.get("system"):
                pp.append({"role": "system", "content": str(pr["system"])})
            for t in pr.get("turns", []) or []:
                pp.append({"role": str(t.get("role", "user")),
                           "content": str(t.get("content", ""))})
            # #2284 T2 question de-dup: the flattened question turn is REMOVED
            # from prompt_pack (prompt_pack = turns only) and stored on
            # Scenario.question instead — prompt_pack consumers (setup.py /
            # arms seeding) see turns-only, so to_render_dict never
            # double-renders the question.
        elif not isinstance(pp, list):
            raise ConfigError(f"scenario {sid}: prompt_pack must be a list")
        prompt_pack = tuple(
            {"role": str(t.get("role", "user")), "content": str(t.get("content", ""))}
            for t in pp
        )
        # #2284 T2 — typed preservation of authored semantic content (each
        # present-but-empty coercion raises ConfigError — never a silent drop).
        question = str(pr.get("question") or "") if pr else ""
        if pr and "question" in pr and not question.strip():
            raise ConfigError(
                f"scenario {sid}: prompt.question present but empty — refusing a "
                "silent drop (loader-fidelity lock)")
        scripts_raw = pr.get("session_scripts") if pr else None
        if scripts_raw is None:
            scripts_raw = raw.get("session_scripts")
        session_scripts = _coerce_dict_list(scripts_raw, sid, "session_scripts")
        evidence_tiers = _coerce_dict_list(
            raw.get("evidence_tiers"), sid, "evidence_tiers")
        drift = _coerce_dict(raw.get("drift"), sid, "drift")
        graph_script = _coerce_dict(raw.get("graph_script"), sid, "graph_script")
        retraction = _coerce_dict(raw.get("retraction"), sid, "retraction")
        # Inline gold (production schema) — scorer-side only. The str
        # projection stays the text accessor (golds()); structured gold
        # (list/dict expected — R4 defeat conditions) is preserved typed
        # for the derive pass (#2284 review P1: str-coercing a list gold
        # made derive emit a repr string the probe would iterate char-wise).
        inline_gold = ""
        structured_gold = None
        gold_raw = raw.get("gold")
        if isinstance(gold_raw, dict) and gold_raw.get("expected"):
            expected_raw = gold_raw["expected"]
            inline_gold = str(expected_raw)
            if isinstance(expected_raw, (list, dict, tuple)):
                structured_gold = expected_raw
        elif gold_raw is not None:
            raise ConfigError(f"scenario {sid}: gold must be {{expected: ...}}")
        k = raw.get("k")
        if k is not None and not isinstance(k, int):
            raise ConfigError(f"scenario {sid}: k must be an int")
        # Gold ref: relative path resolved under the gold store root.
        gold_ref = None
        gr = raw.get("gold_ref")
        if gr is not None:
            if not isinstance(gr, dict) or "path" not in gr or "sha256" not in gr:
                raise ConfigError(f"scenario {sid}: gold_ref must be {{path, sha256}}")
            gold_base_resolved = gold_base.resolve()
            gold_path = (gold_base_resolved / gr["path"]).resolve()
            # Containment guard: gold paths must stay under the sealed store
            # (code-review P3 — no traversal out of the gold root).
            if not gold_path.is_relative_to(gold_base_resolved):
                raise GoldVerificationError(
                    f"scenario {sid}: gold path escapes the sealed store: {gold_path}")
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
        # #1407 production schema uses planted_contradictions [{claim,
        # counter_claim, k}] — normalized to the plan-§4 contradiction_pairs
        # [{claim_a, claim_b, injection_turn}] the runner/probes consume.
        pairs_raw = raw.get("contradiction_pairs")
        if not pairs_raw and raw.get("planted_contradictions"):
            pairs_raw = [
                {"claim_a": p["claim"], "claim_b": p["counter_claim"],
                 "injection_turn": p.get("k", k or 5)}
                for p in raw["planted_contradictions"]
            ]
        pairs = tuple(
            ContradictionPair(
                claim_a=str(p["claim_a"]), claim_b=str(p["claim_b"]),
                injection_turn=int(p.get("injection_turn", k or 5)))
            for p in pairs_raw or []
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
        evidence_scripts=scripts, inline_gold=inline_gold,
        structured_gold=structured_gold,
        question=question, session_scripts=session_scripts,
        evidence_tiers=evidence_tiers, drift=drift,
        graph_script=graph_script, retraction=retraction,
    )


def load_corpus(path: str | Path, *, gold_base: str | Path | None = None,
                ) -> list[Scenario]:
    """Load + validate the corpus. Raises EmptyCorpus on zero scenarios and
    GoldVerificationError on gold mismatch/missing (exit-1 class at dispatch).
    """
    from battery.exceptions import ConfigError  # noqa: PLC0415, RUF100
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
