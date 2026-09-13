"""Matched-recall pre-pass wiring (#3327) — the per-arm factual retriever
and the persisted ``matched_recall`` block.

``battery/recall/matcher.py`` holds the pure contract (probes, F1, symmetric
trigger, INCONCLUSIVE result object). This module adapts a real
``ArmAdapter`` onto the matcher's ``Retriever`` protocol, captures each
arm's factual retrieval for the run's scenario-corpus probes, and assembles
the block that is written to ``recall.json`` and threaded to
``profile.json.matched_recall`` (the §3.2.1 contract fields).

Design constraints from the #3327 decision
(``docs/research/2026-09-12-matched-recall-a0-control.md``):

* Trigger population = the retrieval-capable comparators
  ``{a1, a2, a2b, a3, a4}`` (``TRIGGER_POPULATION``). ``a0`` is EXCLUDED
  from the trigger, RETAINED as its positive control and as a published row
  annotated *recall-confounded* — never silently dropped.
* An arm that cannot retrieve (``ArmUnavailable``) is NEVER coerced into an
  empty result: an empty F1 would read as "divergent" and fire the trigger
  by fabrication. The exception propagates and the run records the arm as
  unavailable.
* The pre-pass returns a RESULT OBJECT; INCONCLUSIVE is the persisted
  ``outcome`` plus exit code 3, never an exception (#1413 indicator 1).
"""
from __future__ import annotations

import os
from typing import Any, Mapping, Sequence  # noqa: UP035

from battery.recall.matcher import (
    EXCLUDED_CONTROL_REASON,
    EXCLUDED_CONTROLS,
    F1_TOLERANCE,
    SUBSET_FLOOR,
    TOP_K,
    TRIGGER_POPULATION,
    FactualProbe,
    RecallResult,
    match_recall,
)


def question_scenario_map(
        probes: Sequence[FactualProbe],
        scenarios: Sequence[Any]) -> dict[str, Any]:
    """Map each probe's question text to the corpus scenario that sourced
    it (probe.id == scenario.id). The arm needs an ``AgentContext`` — the
    scenario handle — not just the question string."""
    by_id = {str(getattr(sc, "id", "")): sc for sc in scenarios}
    out: dict[str, Any] = {}
    for p in probes:
        sc = by_id.get(p.id)
        if sc is not None:
            out.setdefault(p.question, sc)
    return out


class ArmFactualRetriever:
    """Adapt one ``ArmAdapter`` onto the matcher's ``Retriever`` protocol.

    ``retrieve_factual(question, k)`` builds the minimal agent context for
    the probe's scenario and reads the arm's memory through the SAME
    ``ArmAdapter.retrieve`` surface the battery uses, returning the top-k
    memory contents. ``ArmUnavailable`` is deliberately NOT caught — a
    vendor/key failure must surface as an unavailable arm, never as a
    fabricated empty retrieval (distinct from a genuine empty read).

    Real runs additionally verify the adapter's own credential surface
    (``required_env_keys``, #2633) before reading: a real run without the
    vendor key would otherwise silently exercise the arm's seeded
    in-process mock store and publish a mock factual F1 under a real label.
    The run-path pre-flight already refuses such an arm before the attempt
    dir; this is the second, fail-closed layer at the measurement site.
    Hermetic/mock lanes pass ``require_vendor_credentials=False`` so the
    documented mock contract stays usable.
    """

    def __init__(self, arm: Any, scenario_for_question: Mapping[str, Any],
                 *, require_vendor_credentials: bool = False,
                 episode_seed: int = 0):
        self._arm = arm
        self._scenario_for_question = dict(scenario_for_question)
        self._require_vendor_credentials = require_vendor_credentials
        self._episode_seed = episode_seed

    def _assert_credentials(self) -> None:
        if not self._require_vendor_credentials:
            return
        # Lazy: importing ``battery.arms`` at module load would re-enter
        # ``battery.runner`` (arms/__init__ -> a4 -> runner.setup -> runner
        # -> run -> recall.prepass) and deadlock the partial module. The
        # retriever is a leaf utility; it resolves the arms surface on use.
        from battery.arms.base import ArmUnavailable
        required = tuple(getattr(self._arm, "required_env_keys", ()) or ())
        missing = [k for k in required if not os.environ.get(k)]
        if missing:
            raise ArmUnavailable(
                f"recall pre-pass: arm {getattr(self._arm, 'arm_id', '?')!r} "
                f"vendor key(s) {', '.join(missing)} absent from the "
                f"environment — refusing to measure the seeded in-process "
                f"mock store as a real factual F1 (#2633)")

    def retrieve_factual(self, question: str, k: int = TOP_K) -> list[str]:
        from battery.arms.base import AgentContext, ArmUnavailable
        self._assert_credentials()
        scenario = self._scenario_for_question.get(question)
        if scenario is None:
            raise ArmUnavailable(
                f"recall pre-pass: no corpus scenario for probe question "
                f"{question!r} — refusing rather than fabricating a "
                f"retrieval")
        context = AgentContext(
            scenario=scenario, episode_seed=self._episode_seed,
            prior_memories=(), user_message=question)
        memories = self._arm.retrieve(context)
        return [str(getattr(m, "content", "")) for m in memories][:k]


class A0StubRetriever:
    """a0 no-memory control stub — retrieval recall 0.0 by construction
    (``battery/arms/a0_plain.py:26-27``).

    Used by the run path to publish a0's row (present-and-flagged) and by
    the positive-control self-test. a0 is EXCLUDED from the trigger
    population: its constant 0.0 must never drive ``trigger_fired``.
    """

    def retrieve_factual(self, question: str, k: int = TOP_K) -> list[str]:
        return []


class CapturedRetriever:
    """Replay one arm's already-captured probe retrieval.

    The balanced-subset rerun inside ``match_recall`` queries each arm
    again; replaying the capture keeps that deterministic and avoids
    repeated live vendor/graph reads (and any chance the subset rerun sees
    a different memory state)."""

    def __init__(self, by_question: Mapping[str, Sequence[str]]):
        self._by_question = {q: list(r) for q, r in by_question.items()}

    def retrieve_factual(self, question: str, k: int = TOP_K) -> list[str]:
        return list(self._by_question.get(question, ()))[:k]


def capture_factual_recall(arm: Any, probes: Sequence[FactualProbe],
                           scenarios: Sequence[Any], *,
                           run_mode: str = "mock",
                           top_k: int = TOP_K) -> dict[str, list[str]]:
    """Retrieve every probe from one arm; return ``{question: [contents]}``.

    Propagates ``ArmUnavailable`` — the caller records the arm as
    unavailable rather than substituting an empty (fabricated) retrieval.
    """
    retriever = ArmFactualRetriever(
        arm, question_scenario_map(probes, scenarios),
        require_vendor_credentials=(run_mode == "real"))
    return {p.question: retriever.retrieve_factual(p.question, top_k)
            for p in probes}


def build_matched_recall_block(
        probes: Sequence[FactualProbe],
        captures: Mapping[str, Mapping[str, Sequence[str]]],
        *, include_a0: bool = False,
        unavailable_arms: Mapping[str, str] | None = None,
        top_k: int = TOP_K,
        tolerance: float = F1_TOLERANCE,
        subset_floor: float = SUBSET_FLOOR,
        ) -> dict[str, Any] | None:
    """Assemble the ``matched_recall`` block persisted to ``recall.json``
    and threaded to ``profile.json.matched_recall``.

    Returns ``None`` when the pre-pass could not express the trigger (no
    corpus-sourced probes, or no trigger-population arm measured) — the
    block is omitted, never published vacuous.

    The block carries the four §3.2.1 contract fields
    (``f1_by_arm``/``trigger_fired``/``subset_pct``/``outcome``) plus the
    explicit excluded-control visibility: ``trigger_population`` and
    ``excluded_controls`` (a0 present-and-flagged *recall-confounded*, so a
    reader can see it was excluded by decision, not dropped by accident).
    """
    if not probes:
        return None
    retrievers: dict[str, Any] = {}
    if include_a0:
        retrievers["a0"] = A0StubRetriever()
    for arm_id, by_question in captures.items():
        retrievers[arm_id] = CapturedRetriever(by_question)
    if not any(a in TRIGGER_POPULATION for a in retrievers):
        return None

    result: RecallResult = match_recall(
        probes, retrievers, top_k=top_k, tolerance=tolerance,
        subset_floor=subset_floor)
    return {
        # ── the four §3.2.1 contract fields ─────────────────────────────
        "f1_by_arm": dict(result.f1_by_arm),
        "trigger_fired": bool(result.trigger_fired),
        "subset_pct": float(result.subset_pct),
        "outcome": result.outcome,
        # ── excluded-control visibility (#3327.4 — present-and-flagged) ──
        "trigger_population": list(TRIGGER_POPULATION),
        "excluded_controls": {
            arm: EXCLUDED_CONTROL_REASON for arm in EXCLUDED_CONTROLS
            if arm in retrievers},
        # ── audit receipt: provenance + availability + contract constants ─
        "probe_source": "scenario-corpus",
        "probe_ids": [p.id for p in probes],
        "probe_count": len(probes),
        "unavailable_arms": dict(unavailable_arms or {}),
        "top_k": top_k,
        "tolerance": tolerance,
        "subset_floor": subset_floor,
    }
