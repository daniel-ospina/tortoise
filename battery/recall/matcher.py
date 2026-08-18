"""Matched-recall pre-pass (issue #1413, plan §2 W2 + §5 recall/).

Runs BEFORE the reasoning battery: top-K factual retrieval F1 (K=5) per arm
over a small self-contained factual probe subset (independent of the #1407
reasoning corpus). Symmetric trigger: if ANY arm falls ≥0.10 F1 short of
the corpus-best factual retrieval (A4 included — graph retrieval may lose
to RAG on factual F1), the comparison reruns on a recall-matched balanced
subset; if that subset is <50% of the probes, the differential verdict is
INCONCLUSIVE (a result object, not an exception — the pre-committed branch).

Result is immutable per run (the matched-recall outcome is recorded in
profile.json and never re-interpreted post-hoc).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

#: Factual top-K used for the recall match (plan §2 W2).
TOP_K = 5
#: Any arm within this F1 delta of the corpus-best is "matched".
F1_TOLERANCE = 0.10
#: Balanced-subset floor — below this fraction of probes the verdict is
#: INCONCLUSIVE (matching is not meaningful).
SUBSET_FLOOR = 0.50


@dataclass(frozen=True)
class FactualProbe:
    """One factual question with its gold answer (self-contained)."""

    id: str
    question: str
    gold: str


@dataclass(frozen=True)
class RecallResult:
    """Immutable matched-recall outcome for one run."""

    f1_by_arm: dict[str, float]
    trigger_fired: bool
    subset_pct: float
    outcome: str  # "matched" | "inconclusive"

    def __post_init__(self) -> None:
        assert self.outcome in ("matched", "inconclusive")


class Retriever(Protocol):
    """An arm's factual retrieval surface (recall matcher only needs the
    top-K retrieval — not the full ArmAdapter protocol)."""

    def retrieve_factual(self, question: str, k: int = TOP_K) -> list[str]: ...


def default_probes() -> list[FactualProbe]:
    """Self-contained factual probe subset (small, domain-neutral)."""
    return [
        FactualProbe("f1", "What is the capital of France?", "Paris"),
        FactualProbe("f2", "Which planet is known as the Red Planet?", "Mars"),
        FactualProbe("f3", "What is 7 times 8?", "56"),
        FactualProbe("f4", "Who wrote Hamlet?", "Shakespeare"),
        FactualProbe("f5", "What is the largest ocean?", "Pacific"),
        FactualProbe("f6", "What gas do plants absorb?", "carbon dioxide"),
        FactualProbe("f7", "How many continents are there?", "seven"),
        FactualProbe("f8", "What is the boiling point of water in Celsius?", "100"),
    ]


def _f1_at_k(retrieved: Sequence[str], gold: str, k: int = TOP_K) -> float:
    """F1 at K: does the gold appear in the top-K retrieved texts?
    Token-overlap precision/recall over the top-K (deterministic)."""
    top = [r.lower() for r in retrieved[:k]]
    gold_tokens = set(gold.lower().split())
    if not gold_tokens:
        return 0.0
    hit = sum(1 for t in top if any(g in t for g in gold_tokens))
    recall = hit / len(gold_tokens) if gold_tokens else 0.0
    precision = hit / max(len(top), 1)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def match_recall(probes: Sequence[FactualProbe],
                 retrievers: dict[str, Retriever],
                 *, top_k: int = TOP_K,
                 tolerance: float = F1_TOLERANCE,
                 subset_floor: float = SUBSET_FLOOR) -> RecallResult:
    """Compute the matched-recall outcome for one run.

    Symmetric trigger: ANY arm ≥ tolerance below the corpus-best factual
    F1 triggers a balanced-subset rerun (arms whose F1 is within tolerance
    of best keep all probes; the divergent arm is measured on the subset
    where arms agree). If the retained subset is < floor of the probes, the
    outcome is INCONCLUSIVE.
    """
    f1: dict[str, float] = {}
    for aid, retriever in retrievers.items():
        hits = sum(
            1 for p in probes
            if _f1_at_k(retriever.retrieve_factual(p.question, top_k),
                        p.gold, top_k) > 0.0)
        f1[aid] = hits / len(probes) if probes else 0.0

    best = max(f1.values())
    trigger_fired = any(best - v > tolerance for v in f1.values())

    if not trigger_fired:
        return RecallResult(f1_by_arm=f1, trigger_fired=False,
                            subset_pct=1.0, outcome="matched")

    # Balanced subset: keep probes where the divergent arm's retrieval
    # overlaps the best arm's (arms "agree" on the question).
    divergent = [aid for aid, v in f1.items() if best - v > tolerance]
    best_arm = max(f1, key=f1.get)
    kept: list[FactualProbe] = []
    for p in probes:
        best_hits = _f1_at_k(retrievers[best_arm].retrieve_factual(
            p.question, top_k), p.gold, top_k)
        div_hits = any(
            _f1_at_k(retrievers[a].retrieve_factual(p.question, top_k),
                     p.gold, top_k) > 0.0 for a in divergent)
        if best_hits > 0.0 or div_hits:
            kept.append(p)
    subset_pct = len(kept) / len(probes) if probes else 0.0
    if subset_pct < subset_floor:
        return RecallResult(f1_by_arm=f1, trigger_fired=True,
                            subset_pct=subset_pct, outcome="inconclusive")

    # Rerun F1 on the balanced subset (all arms).
    f1_sub: dict[str, float] = {}
    for aid, retriever in retrievers.items():
        hits = sum(
            1 for p in kept
            if _f1_at_k(retriever.retrieve_factual(p.question, top_k),
                        p.gold, top_k) > 0.0)
        f1_sub[aid] = hits / len(kept) if kept else 0.0
    return RecallResult(f1_by_arm=f1_sub, trigger_fired=True,
                        subset_pct=subset_pct, outcome="matched")
