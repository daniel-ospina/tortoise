"""Reader-safe scenario corpus loader + sealed gold store (issue #1407).

The READER path (agent-under-test prompt assembly) must never be able to
retrieve gold answers — the S5 seal. This module enforces that:

* ``load_corpus()`` loads the committed reader-facing ``corpus.json`` (gold
  free — every scenario is checked with ``assert_no_gold``) and is fail-closed
  (``CorpusMissingError`` / ``CorpusCorruptError``).
* ``render_reader_prompt()`` is the ONLY sanctioned agent-visible renderer —
  it renders the arm-compatible prompt pack (and session content for
  multi-session packs) and NEVER renders scorer metadata (``attack_type``,
  ``hostile``, ``gold_sha256``, ``matched_control_for``, ``variant_of``,
  ``graph_script``). Adapters must never stringify scenario dicts.
* ``GoldStore`` is the scorer-path accessor, fail-closed on missing/corrupt
  stores and unknown ids. Its ``digest()`` (sha256 of the canonical store
  dict) is the ``golds_sha256`` manifest value — ``verify_seal`` cross-checks
  them.

Digest basis (pinned): ``canonical_json`` (sorted keys, compact separators,
UTF-8) over DICTS — never over file bytes (the indented files on disk are
display format only).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

GOLD_KEY = "gold"
GOLD_HASH_KEY = "gold_sha256"

_ROLE_VALUES = ("user", "assistant")


class GoldLeakError(Exception):
    """Gold content found in reader-visible data."""


class SealMissingError(Exception):
    """Sealed gold store missing or corrupt — rebuild with the builder."""


class SealMismatchError(Exception):
    """Gold-store digest does not match the corpus manifest's golds_sha256."""


class StoreEntryMissingError(Exception):
    """No gold entry for the requested scenario id in the sealed store."""


class CorpusMissingError(Exception):
    """Committed corpus.json missing — rebuild with the builder."""


class CorpusCorruptError(Exception):
    """Committed corpus.json corrupt."""


def canonical_json(obj: Any) -> bytes:
    """Canonical serialization — the ONLY bytes ever hashed (determinism).

    Sorted keys, compact separators, UTF-8, no trailing whitespace: two builds
    of the same corpus produce identical bytes regardless of dict insertion
    order or Python hash randomization (no sets anywhere).
    """
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def normalize(s: str) -> str:
    """Casefold + whitespace collapse — the matching contract for every
    content binding (claim placement, first-appearance, gold no-substring)."""
    return " ".join(s.casefold().split())


def contains_phrase(text: str, phrase: str) -> bool:
    """Word-boundary substring match of the FULL normalized phrase.

    ``"sky"`` must not match ``"skydiving"``; boundaries are non-alphanumeric
    characters. Matches the in-repo precedent (longmem_eval MockJudge) with
    normalization.
    """
    t = normalize(text)
    p = normalize(phrase)
    if not p:
        return False
    start = 0
    while True:
        idx = t.find(p, start)
        if idx == -1:
            return False
        before = t[idx - 1] if idx > 0 else ""
        after = t[idx + len(p)] if idx + len(p) < len(t) else ""
        if not (before.isalnum() or after.isalnum()):
            return True
        start = idx + 1


def _walk(node: Any, path: str, out: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if key == GOLD_KEY:
                out.append(child)
            _walk(value, child, out)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk(value, f"{path}[{i}]", out)


def assert_no_gold(obj: Any) -> list[str]:
    """Recursive gold scan — raises ``GoldLeakError`` if any ``gold`` key
    (EXACT key equality — ``gold_sha256`` never trips) exists at any depth.

    Returns the list of gold-bearing paths (empty when clean). This is the
    load-time reader guard.
    """
    paths: list[str] = []
    _walk(obj, "$", paths)
    if paths:
        raise GoldLeakError(
            "gold content found in reader-visible data at: " + ", ".join(paths))
    return paths


def _strip_gold(node: Any) -> None:
    """In-place recursive deletion of every ``key == GOLD_KEY`` at any depth.

    Used by the builder on the EMITTED scenarios; combined with
    ``assert_no_gold`` it proves gold-freeness of the reader artifact.
    """
    if isinstance(node, dict):
        for key in [k for k in node if k == GOLD_KEY]:
            del node[key]
        for value in node.values():
            _strip_gold(value)
    elif isinstance(node, list):
        for value in node:
            _strip_gold(value)


def _render_turns(turns: list[dict]) -> str:
    return "\n".join(f"{t['role']}: {t['content']}" for t in turns)


def _evidence_block(scenario: dict) -> str:
    """Authored ``evidence_tiers`` content rendered as an Evidence block
    (#2284 T2). Emission is EXACTLY the authored {tier, claim, valence}
    triples — never synthesized numbers, never gold. Empty when the pack
    carries no evidence_tiers."""
    tiers = scenario.get("evidence_tiers")
    if not tiers:
        return ""
    lines = ["Evidence:"]
    for item in tiers:
        tier = str(item.get("tier", ""))
        valence = str(item.get("valence", ""))
        claim = str(item.get("claim", ""))
        tag = f"{tier} ({valence})" if valence else tier
        lines.append(f"{tag}: {claim}")
    return "\n".join(lines)


def render_from_scenario(scenario, session: int | None = None) -> str:
    """Convenience: render a run-path Scenario through the ONE renderer —
    ``render_reader_prompt(scenario.to_render_dict(), session=session)``
    (#2284 T2 — the Scenario reader surface is the corpus.json-shaped dict,
    no second renderer)."""
    return render_reader_prompt(scenario.to_render_dict(), session=session)


def _session_scripts_of(scenario: dict) -> list[dict] | None:
    """Session scripts live at scenario level or nested under prompt — normalize."""
    scripts = scenario.get("session_scripts")
    if scripts is None:
        scripts = (scenario.get("prompt") or {}).get("session_scripts")
    return scripts or None


def render_reader_prompt(scenario: dict, session: int | None = None) -> str:
    """Render the ONLY agent-visible surface for a scenario.

    Fails closed: runs ``assert_no_gold`` first (a gold-bearing scenario —
    including a nested gold inside ``hostile``/turns — raises ``GoldLeakError``).

    Single-session packs: system + turns + question.
    Multi-session packs (``session_scripts``): ``session=None`` renders the
    accumulated full-history view (system + every session in order — a
    comparison/control surface, NOT the L4 delivery default); ``session=N``
    renders system + session N's turns + question only. Per-session delivery
    is owned by the harness (#1410).

    #2284 T2: packs carrying authored ``evidence_tiers`` get their Evidence
    block appended (authored tier/claim content verbatim — no synthesis); the
    question line is emitted only when the question is non-empty (never a bare
    ``question: `` line).

    Scorer metadata (``attack_type``, ``hostile``, ``gold_sha256``,
    ``matched_control_for``, ``variant_of``, ``graph_script``) is NEVER
    rendered.
    """
    assert_no_gold(scenario)
    system = scenario["prompt"]["system"]
    parts = [system]
    evidence = _evidence_block(scenario)
    scripts = _session_scripts_of(scenario)
    if scripts:
        ordered = sorted(scripts, key=lambda s: s["session"])
        if session is None:
            for s in ordered:
                parts.append(_render_turns(s["turns"]))
                question = str(s.get("question", ""))
                if question.strip():
                    parts.append("question: " + question)
            if evidence:
                parts.append(evidence)
        else:
            s = next((x for x in ordered if x["session"] == session), None)
            if s is None:
                raise KeyError(
                    f"scenario {scenario.get('id')!r} has no session {session!r} "
                    f"(sessions: {[x['session'] for x in ordered]})")
            parts.append(_render_turns(s["turns"]))
            question = str(s.get("question", ""))
            if question.strip():
                parts.append("question: " + question)
            if evidence:
                parts.append(evidence)
    else:
        parts.append(_render_turns(scenario["prompt"]["turns"]))
        question = str(scenario["prompt"].get("question", ""))
        if question.strip():
            parts.append("question: " + question)
        if evidence:
            parts.append(evidence)
    return "\n\n".join(parts)


class Corpus:
    """Reader-safe view of the corpus (scenarios + manifest)."""

    def __init__(self, scenarios: list[dict], manifest: dict):
        self.scenarios = scenarios
        self.manifest = manifest

    def by_id(self, scenario_id: str) -> dict:
        for sc in self.scenarios:
            if sc["id"] == scenario_id:
                return sc
        raise KeyError(f"no scenario {scenario_id!r} in corpus")

    def filter(
        self,
        *,
        tier: str | None = None,
        family: str | None = None,
        task_type: str | None = None,
        attack_type: str | None = None,
        split: str | None = None,
        seed: int | None = None,
    ) -> list[dict]:
        """AND-combination filter, deterministic (sorted by id).

        ``seed`` is a determinism key for a downstream-supplied sample — the
        corpus itself returns the FULL filtered set (same seed + same caller
        size → same subset). Selection by wave = ``split="wave-N"``.
        """
        out = [
            sc for sc in self.scenarios
            if (tier is None or sc["tier"] == tier)
            and (family is None or sc["family"] == family)
            and (task_type is None or sc["task_type"] == task_type)
            and (attack_type is None or sc.get("attack_type") == attack_type)
            and (split is None or sc["split"] == split)
        ]
        out.sort(key=lambda s: s["id"])
        return out


def load_corpus(path: str | Path | None = None) -> Corpus:
    """Load the committed reader-facing corpus.json (fail-closed).

    Default path is package-anchored (never CWD-anchored). Every scenario is
    checked gold-free; a reader path that would surface gold refuses loudly.
    """
    p = Path(path) if path else Path(__file__).resolve().parent / "corpus.json"
    if not p.is_file():
        raise CorpusMissingError(
            f"committed corpus.json not found at {p} — rebuild with: "
            "uv run python -m battery.config.build_corpus")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CorpusCorruptError(
            f"committed corpus.json at {p} is corrupt ({exc}) — rebuild with: "
            "uv run python -m battery.config.build_corpus") from exc
    if not isinstance(raw, dict) or "scenarios" not in raw or "manifest" not in raw:
        raise CorpusCorruptError(
            f"committed corpus.json at {p} has an invalid shape (expected "
            "{scenarios, manifest})")
    for sc in raw["scenarios"]:
        assert_no_gold(sc)  # reader path fails closed on any gold presence
    return Corpus(raw["scenarios"], raw["manifest"])


class GoldStore:
    """Scorer-path accessor for the sealed gold answers (fail-closed)."""

    def __init__(self, path: str | Path | None = None):
        # Package-anchored default — never CWD-anchored.
        self._path = (
            Path(path) if path
            else Path(__file__).resolve().parent / ".gold_store" / "golds.json"
        )
        self._store: dict | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict:
        if self._store is not None:
            return self._store
        if not self._path.is_file():
            raise SealMissingError(
                f"sealed gold store not found at {self._path} — run: "
                "uv run python -m battery.config.build_corpus")
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SealMissingError(
                f"sealed gold store at {self._path} is corrupt ({exc}) — "
                "rebuild with: uv run python -m battery.config.build_corpus"
            ) from exc
        if not isinstance(data, dict):
            raise SealMissingError(
                f"sealed gold store at {self._path} is not a JSON object")
        self._store = data
        return self._store

    def gold(self, scenario_id: str) -> dict:
        store = self._load()
        if scenario_id not in store:
            raise StoreEntryMissingError(
                f"no gold entry for scenario {scenario_id!r} in the sealed "
                "store — it may have been added after the last build; run: "
                "uv run python -m battery.config.build_corpus")
        return store[scenario_id]

    def digest(self) -> str:
        """sha256 of the canonical store DICT (sorted by construction via
        sort_keys) — identical basis to the manifest's golds_sha256."""
        return hashlib.sha256(canonical_json(self._load())).hexdigest()


def verify_seal(corpus: Corpus, store: GoldStore) -> None:
    """Cross-check the gold store against the corpus manifest's golds_sha256.

    Raises ``SealMismatchError`` (with both digests) on mismatch — the
    tamper-evidence half of the S5 seal.
    """
    expected = corpus.manifest.get("golds_sha256")
    if expected is None:
        raise SealMismatchError("corpus manifest has no golds_sha256 — stale build")
    actual = store.digest()
    if actual != expected:
        raise SealMismatchError(
            f"gold store digest {actual} != manifest golds_sha256 {expected} — "
            "the store is stale or tampered; run: uv run python -m "
            "battery.config.build_corpus")
