"""ct≡bct surface-twin diff predicate (#2284 T3/T4 — SHARED home).

The corpus-v1.1 benign-control contract: a ``bct-*`` scenario is a benign
false-positive surface twin of its ``ct-*`` planted counterpart — the policy
surface (system prompt + turns + question, i.e. exactly what
``render_reader_prompt`` renders) is byte-equivalent EXCEPT the benign
question slot and the ¬A (injection) turn.

``surface_diff(ct_id, bct_id)`` returns the set of POLICY-SURFACE slot ids
that differ between the two scenarios (slot ids: ``"system"``,
``"turn_<i>"`` for each 0-based turn index, ``"question"``). A twin pair is
valid iff ``surface_diff(...) <= NEG_A_DELTA_SLOTS`` — never raw
render_hash equality (the question slot and the ¬A turn are SUPPOSED to
differ; everything else must be identical).

Tasks 3 AND 4 import from here — this is the SINGLE home of the predicate.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import schema
from .corpus_loader import normalize

#: The ONLY policy-surface slots a ct-* scenario and its bct-* benign twin
#: may differ on: the benign question slot + the ¬A (injection) turn slot.
#: Contradiction k is pinned to CONTRADICTION_K (= 5) in this corpus version,
#: so the ¬A turn is always 0-based index k-1 (= 4) → slot id "turn_4".
NEG_A_DELTA_SLOTS: frozenset[str] = frozenset(
    {"question", f"turn_{schema.CONTRADICTION_K - 1}"}
)

_AUTHORING_SOURCE = Path(__file__).resolve().parent / "corpus.yaml"


@lru_cache(maxsize=1)
def _authoring_by_id() -> dict[str, dict[str, Any]]:
    """Authoring-source (corpus.yaml) scenarios indexed by id — the default
    ``surface_diff`` source: both Task 3 (tests) and Task 4 (no-leak lock)
    compare the yaml-authored policy surface. Cached: the yaml file is large
    and the predicate is called once per twin pair."""
    from .validate import load_yaml_dupreject
    data = load_yaml_dupreject(_AUTHORING_SOURCE)
    return {str(sc.get("id")): sc for sc in data.get("scenarios", [])}


def _slot_texts(scenario: dict[str, Any]) -> dict[str, str]:
    """Policy-surface slots of one single-session scenario dict. The sealed
    corpus.json scenario shape == the authored yaml shape (nested ``prompt``
    {system, turns, question} — gold is stripped/redacted only, prompt slots
    untouched)."""
    prompt = scenario.get("prompt") or {}
    out: dict[str, str] = {"system": str(prompt.get("system", ""))}
    for i, turn in enumerate(prompt.get("turns") or []):
        out[f"turn_{i}"] = str(turn.get("content", ""))
    out["question"] = str(prompt.get("question", ""))
    return out


def surface_diff(
    ct_id: str,
    bct_id: str,
    *,
    scenarios: Mapping[str, dict[str, Any]] | None = None,
) -> frozenset[str]:
    """Slot-id set of policy-surface differences between a ct-* scenario and
    its bct-* twin.

    ``scenarios``: an id→scenario-dict mapping (sealed corpus.json scenarios
    or authored yaml dicts). Defaults to the authoring corpus.yaml (cached).

    A slot id is present when its normalized text differs (missing slots
    compare as empty — an extra turn on either side surfaces as a turn slot
    delta). Raises ``ValueError`` when either id is absent from the source.
    """
    by_id: Mapping[str, dict[str, Any]] = (
        _authoring_by_id() if scenarios is None else scenarios)
    for sid in (ct_id, bct_id):
        if sid not in by_id:
            raise ValueError(
                f"surface_diff: no scenario {sid!r} in the source corpus")
    a = _slot_texts(by_id[ct_id])
    b = _slot_texts(by_id[bct_id])
    return frozenset(
        k for k in set(a) | set(b)
        if normalize(a.get(k, "")) != normalize(b.get(k, "")))
