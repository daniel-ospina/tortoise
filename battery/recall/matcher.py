"""Matched-recall pre-pass (issue #1413, plan §2 W2 + §5 recall/).

Runs BEFORE the reasoning battery: top-K factual retrieval F1 (K=5) per arm
over a small self-contained factual probe subset (independent of the #1407
reasoning corpus). Symmetric trigger: if ANY arm falls ≥0.10 F1 short of
the corpus-best factual retrieval (A4 included — graph retrieval may lose
to RAG on factual F1), the comparison reruns on a recall-matched balanced
subset; if that subset is <50% of the probes, the differential verdict is
INCONCLUSIVE (a result object, not an exception — the pre-committed branch).

Amendment (2026-09-12, #3327): the trigger's population is the
retrieval-capable comparators {a1, a2, a2b, a3, a4} — a0 is EXCLUDED and
retained as the trigger's positive control. The "ANY arm" wording above is
preserved and read by purpose. Controlling decision:
docs/research/2026-09-12-matched-recall-a0-control.md; contract rows
§3.2.1/§7 of docs/benchmarks/comparison-systems.md. The population is the
``TRIGGER_POPULATION`` constant below; the pre-pass is WIRED into the run
path (#3327) by ``battery/recall/prepass.py`` (per-arm retriever) and
``battery/runner/run.py`` (probes from the scenario corpus -> recall.json ->
profile.json.matched_recall).

Result is immutable per run (the matched-recall outcome is recorded in
profile.json and never re-interpreted post-hoc).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence  # noqa: UP035

#: Factual top-K used for the recall match (plan §2 W2).
TOP_K = 5
#: Any arm within this F1 delta of the corpus-best is "matched".
#: Amendment (2026-09-12, #3327): read over the trigger population
#: {a1, a2, a2b, a3, a4} — a0 is EXCLUDED (see module docstring).
#: The population is the ``TRIGGER_POPULATION`` constant defined below.
F1_TOLERANCE = 0.10
#: Balanced-subset floor — below this fraction of probes the verdict is
#: INCONCLUSIVE (matching is not meaningful).
SUBSET_FLOOR = 0.50
#: The arms the symmetric trigger is defined over (#3327 decision,
#: 2026-09-12): the RETRIEVAL-CAPABLE comparators. ``a0`` is EXCLUDED and
#: retained as the trigger's positive control plus a published profile row
#: annotated *recall-confounded*. Basis: a0's recall is 0.0 by construction
#: (``battery/arms/a0_plain.py:26-27``), so an a0-inclusive trigger fires on
#: every run and makes every differential run INCONCLUSIVE — which would
#: leave a pre-committed four-verdict contract able to express exactly one
#: verdict. Decision: docs/research/2026-09-12-matched-recall-a0-control.md;
#: contract rows §3.2.1/§7 of docs/benchmarks/comparison-systems.md.
TRIGGER_POPULATION: tuple[str, ...] = ("a1", "a2", "a2b", "a3", "a4")
#: Controls retained in the output block (present-and-flagged, never
#: silently dropped) but taken OUT of the trigger computation.
EXCLUDED_CONTROLS: tuple[str, ...] = ("a0",)
#: The exclusion reason carried next to a0's row (checksum of the decision).
EXCLUDED_CONTROL_REASON = (
    "recall-confounded: no-memory control, retrieval recall 0.0 by "
    "construction (a0_plain.py:26-27); retained as the trigger's positive "
    "control and as a published profile row, EXCLUDED from "
    "trigger_population (#3327 decision, 2026-09-12)")


@dataclass(frozen=True)
class FactualProbe:
    """One factual question with its gold answer and its corpus scenario.

    The probe is keyed by its STABLE ``id`` everywhere — the capture map,
    the ``Retriever`` protocol and the ``AgentContext`` — NEVER by
    ``question`` text. The shipped ``battery/config/corpus.yaml`` holds four
    groups of scenarios (8 of 140) whose ``question`` text is IDENTICAL but
    whose gold differs (e.g. ``d-001``/``wv-001``); a question-keyed index
    silently collapses each group onto one scenario and scores the other
    member's gold against the wrong scenario's context — a fabricated
    retrieval miss inside the measurement itself (#3327 review).

    ``scenario`` is the harness-side corpus handle, carried ON the probe so
    the retriever builds the arm's ``AgentContext`` from the probe's OWN
    scenario instead of re-deriving one from question text. ``gold`` stays
    harness-side: the matcher reads it, and it is never placed in the
    ``AgentContext`` handed to an arm (sealed-gold boundary, scope DD2).
    """

    id: str
    question: str
    gold: str
    #: Corpus scenario handle (harness-side). ``None`` only for probes built
    #: without a corpus (hand-written fixtures); the retriever REFUSES such
    #: a probe rather than fabricating a retrieval. Excluded from equality
    #: and hashing (``compare=False``): the handle is opaque and may be
    #: unhashable (``Scenario`` carries dict fields), while probe identity
    #: is the ``id``.
    scenario: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class RecallResult:
    """Immutable matched-recall outcome for one run."""

    f1_by_arm: Mapping[str, float]
    trigger_fired: bool
    subset_pct: float
    outcome: str  # "matched" | "inconclusive"

    def __post_init__(self) -> None:
        if self.outcome not in ("matched", "inconclusive"):
            raise ValueError(f"invalid outcome: {self.outcome!r}")
        # Freeze the per-arm F1 map (immutable per run — never
        # re-interpreted post-hoc).
        object.__setattr__(self, "f1_by_arm",
                           __import__("types").MappingProxyType(
                               dict(self.f1_by_arm)))


class Retriever(Protocol):
    """An arm's factual retrieval surface (recall matcher only needs the
    top-K retrieval — not the full ArmAdapter protocol).

    Keyed by the probe's STABLE ``id`` — never by ``question`` text, which
    is not unique across the shipped corpus (see ``FactualProbe``).
    """

    def retrieve_factual(self, probe_id: str, k: int = TOP_K) -> list[str]: ...


def default_probes() -> list[FactualProbe]:
    """HERMETIC TEST FIXTURE ONLY — NOT the production probe source.

    Generic world facts ("capital of France") that no arm's scenario-corpus
    memory contains. #3327 decision (.3): the pre-registration measures
    probes "on the scenario corpus" (01-align.md:56), so using these in the
    run path would mean the trigger can never legitimately fire. The
    production source is ``scenario_probes`` (the run's own corpus). Kept
    for matcher unit tests + the a0 positive-control fixture.
    """
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


def scenario_probes(scenarios: Sequence[Any], *,
                    limit: int | None = None) -> list[FactualProbe]:
    """Build the factual probe subset FROM THE SCENARIO CORPUS (#3327.3).

    01-align.md:56 measures probes "on the scenario corpus" — the probes
    must land on content an arm's memory actually holds, or the trigger can
    never legitimately fire. Probe question = the scenario's authored
    ``question``; gold = the scenario's sealed gold text, read HERE on the
    harness side only (the arm receives the question, never the gold — the
    sealed-gold boundary is preserved). The SOURCING scenario is carried on
    the probe (harness-side handle) so downstream retrieval keys by the
    probe's ``id`` and builds the context from the probe's OWN scenario, not
    from question text — the corpus has duplicate questions with different
    gold (#3327 review). Scenarios lacking a question or a gold answer are
    SKIPPED (counted by the caller, never fabricated into a probe). Order is
    corpus order and deterministic; ``limit`` caps the subset for cost.
    """
    probes: list[FactualProbe] = []
    for sc in scenarios:
        sid = str(getattr(sc, "id", "") or "")
        question = str(getattr(sc, "question", "") or "").strip()
        if not sid or not question:
            continue
        golds_fn = getattr(sc, "golds", None)
        golds = golds_fn() if callable(golds_fn) else ()
        gold = " ".join(str(g).strip() for g in golds if str(g).strip())
        if not gold:
            continue
        probes.append(FactualProbe(id=sid, question=question, gold=gold,
                                   scenario=sc))
        if limit is not None and len(probes) >= limit:
            break
    return probes


def _f1_at_k(retrieved: Sequence[str], gold: str, k: int = TOP_K) -> float:
    """F1 at K: does the gold appear in the top-K retrieved texts?
    Token-overlap precision/recall over the top-K (deterministic);
    recall counts DISTINCT matched gold tokens and caps at 1.0 (a single
    retrieved text can only match each gold token once)."""
    top = [r.lower() for r in retrieved[:k]]
    gold_tokens = set(gold.lower().split())
    if not gold_tokens:
        return 0.0
    matched = set()
    for t in top:
        for g in gold_tokens:
            if g in t:
                matched.add(g)
    recall = min(len(matched) / len(gold_tokens), 1.0)
    precision = len(matched) / max(len(top), 1)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def match_recall(probes: Sequence[FactualProbe],
                 retrievers: dict[str, Retriever],
                 *, top_k: int = TOP_K,
                 tolerance: float = F1_TOLERANCE,
                 subset_floor: float = SUBSET_FLOOR,
                 population: Sequence[str] = TRIGGER_POPULATION,
                 ) -> RecallResult:
    """Compute the matched-recall outcome for one run.

    Symmetric trigger: ANY arm ≥ tolerance below the corpus-best factual
    F1 triggers a balanced-subset rerun (arms whose F1 is within tolerance
    of best keep all probes; the divergent arm is measured on the subset
    where arms agree). If the retained subset is < floor of the probes, the
    outcome is INCONCLUSIVE.

    Amendment (2026-09-12, #3327): "ANY arm" reads over the trigger
    population {a1, a2, a2b, a3, a4} — a0 is EXCLUDED and retained as the
    trigger's positive control (decision:
    docs/research/2026-09-12-matched-recall-a0-control.md; §3.2.1/§7 of
    docs/benchmarks/comparison-systems.md). Every retriever passed in is
    still MEASURED (its F1 stays in ``f1_by_arm``, so a0's row is published
    and flagged rather than dropped) but only the trigger population
    participates in the divergence computation. A control-only probe set
    raises ``ValueError`` — a vacuous "matched" over no compared arm would
    be a false pass. Outcome is a RESULT OBJECT, never an exception; the
    INCONCLUSIVE consequence is the persisted ``outcome`` value plus the
    run's exit code (#1413 indicator 1).
    """
    f1: dict[str, float] = {}
    if not probes:
        raise ValueError(
            "matched-recall probe set is empty — no probe was sourced from "
            "the scenario corpus, so no recall can be matched (#3327.3); "
            "the caller must omit the block rather than publish a vacuous "
            "outcome")
    for aid, retriever in retrievers.items():
        hits = sum(
            1 for p in probes
            if _f1_at_k(retriever.retrieve_factual(p.id, top_k),
                        p.gold, top_k) > 0.0)
        f1[aid] = hits / len(probes) if probes else 0.0

    population_now = tuple(a for a in retrievers if a in population)
    if not population_now:
        raise ValueError(
            f"matched-recall trigger population is empty — none of "
            f"{sorted(retrievers)} is in {tuple(population)}; a control-only "
            f"probe set can not express the relative trigger (#3327)")
    best = max(f1[a] for a in population_now)
    trigger_fired = any(best - f1[a] > tolerance for a in population_now)

    if not trigger_fired:
        return RecallResult(f1_by_arm=f1, trigger_fired=False,
                            subset_pct=1.0, outcome="matched")

    divergent = [a for a in population_now if best - f1[a] > tolerance]
    best_arm = max(population_now, key=lambda a: f1[a])
    kept = [
        p for p in probes
        if _f1_at_k(retrievers[best_arm].retrieve_factual(
            p.id, top_k), p.gold, top_k) > 0.0
        and all(_f1_at_k(retrievers[a].retrieve_factual(p.id, top_k),
                         p.gold, top_k) > 0.0 for a in divergent)
    ]

    # Balanced subset: keep probes where the best arm AND every divergent
    # arm both retrieve the gold (arms "agree" on the question). If the
    # arms answer DISJOINT questions, the intersection collapses below the
    # floor → INCONCLUSIVE (the match is not meaningful).
    subset_pct = len(kept) / len(probes) if probes else 0.0
    if subset_pct < subset_floor:
        return RecallResult(f1_by_arm=f1, trigger_fired=True,
                            subset_pct=subset_pct, outcome="inconclusive")

    # Rerun F1 on the balanced subset (all arms).
    f1_sub: dict[str, float] = {}
    for aid, retriever in retrievers.items():
        hits = sum(
            1 for p in kept
            if _f1_at_k(retriever.retrieve_factual(p.id, top_k),
                        p.gold, top_k) > 0.0)
        f1_sub[aid] = hits / len(kept) if kept else 0.0
    return RecallResult(f1_by_arm=f1_sub, trigger_fired=True,
                        subset_pct=subset_pct, outcome="matched")
