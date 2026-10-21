"""The measurement POSTURE of a LongMemEval run — #8486.

A LongMemEval number is only readable when it says WHICH configuration
produced it. #8486's comment thread pins two facts that must ride every
surface a number appears on:

* the **ingest mode** (``deterministic`` = raw-content graph, no extraction;
  ``v2`` = the production extractor, real Layer-1 payload), and
* **whether the belief layer was activated** (``ep_activation.activate_beliefs``
  — promotion of the draft extraction points/operators + a real EP pass).

The second one matters because ``ingest_v2`` writes every extraction point
``status="draft"`` and mints its operators with ``promote_source=False``,
while ``tortoise/ep.py`` filters the factor graph with ``_live_only(...)``
(``include_draft`` defaults to ``False``) — so an un-activated eval graph is
EP-INERT and every claim reads the neutral ``0.50`` of a Beta(1,1) prior that
was NEVER measured (#2598). A ``v2`` run without activation therefore has the
structure but no belief: its number must never be described as measuring
Tortoise's reasoning (``docs/benchmarks/longmemeval-500q/run-report.md`` §3).

This module is the ONE place that derives the posture, so the runner (which
records it), the report methodology (which carries it) and the published
receipt (which REFUSES a completed run without it) can never disagree.

Hermetic by construction: stdlib only, no DB, no network — the callers own the
mechanics (which outcomes exist, whether the arm was requested).
"""
from __future__ import annotations

from typing import Any

#: The belief layer WAS activated for the run (every question's EP pass ran).
BELIEF_LAYER_ACTIVATED = "activated"
#: Activation was requested but did not complete cleanly for every question —
#: the run mixes a measured belief layer with an EP-inert one, so it may not
#: be described as a belief-activated measurement.
BELIEF_LAYER_PARTIAL = "partial"
#: Activation was NOT requested: the belief layer contributes nothing (for
#: ``deterministic`` graphs no operators exist at all; for ``v2`` graphs the
#: draft points are EP-inert). The report must say so next to the number.
BELIEF_LAYER_INERT = "inert"
#: No activation decision is recorded (a pre-#8486 report, or a caller that
#: never resolved the arm). NOT a publishable posture — the receipt gate
#: refuses it (#8486).
BELIEF_LAYER_UNKNOWN = "unknown"

BELIEF_LAYER_VALUES: frozenset[str] = frozenset({
    BELIEF_LAYER_ACTIVATED, BELIEF_LAYER_PARTIAL, BELIEF_LAYER_INERT,
    BELIEF_LAYER_UNKNOWN,
})

#: Manifest keys summed over the run by :func:`summarize_belief_activation`.
_SUMMED_KEYS = (
    "points_promoted",
    "operators_promoted",
    "points_with_ep",
    "points_unmeasured",
)


def _as_int(value: Any) -> int:
    """Non-negative int from a manifest cell; junk counts as 0."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def summarize_belief_activation(
    outcomes: list[dict[str, Any]],
    *,
    requested: bool,
) -> dict[str, Any]:
    """Aggregate the per-outcome activation manifests into ONE run block.

    Each outcome may carry ``belief_activation`` — the manifest returned by
    ``ep_activation.activate_beliefs`` (``points_promoted``,
    ``operators_promoted``, ``ep_ran``, ``ep_engine``, ``points_with_ep``,
    ``points_unmeasured``) or the runner's fail-open shape
    ``{"ep_ran": False, "error": "..."}``.

    Deterministic, order-independent, and pure: ``requested`` comes from the
    caller's resolved arm, never inferred from the outcomes (a requested arm
    that produced no manifest is a FAILED activation, not an absent arm).

    ``questions`` is EVERY outcome the number is computed over — NOT the
    number of manifests. A RESUMED outcome (a checkpoint written before the
    arm existed, or under a different arm) carries no manifest, so counting
    only manifests would make a run whose resumed majority is EP-inert read as
    ``activated`` on the strength of the handful of questions that re-ran. The
    checkpoint fingerprint refuses a cross-arm resume (``_build_fingerprint``
    carries ``activate_beliefs``); this denominator is the INDEPENDENT second
    guard, and it is what makes a blended run report ``partial``.

    ``questions_with_ep`` is the POSITIVE-measurement cell: a manifest counts
    only when the pass ran AND its own post-pass census found at least one
    non-operator point carrying persisted EP state (``points_with_ep > 0``).
    ``ep_ran`` alone is not evidence of measurement — ``sdk.dream(...)``
    returns NORMALLY with zero belief writes when the window holds no EP
    factors (``no_op_reason = "no_ep_factors"``) and on the #1163 stale-run
    stand-down, so a run whose every question's EP pass measured nothing
    would otherwise read ``activated`` (fresh-context review, P1).
    """
    rows = [o for o in (outcomes or ()) if isinstance(o, dict)]
    manifests = [o["belief_activation"] for o in rows
                 if isinstance(o.get("belief_activation"), dict)]
    summary: dict[str, Any] = {
        "requested": bool(requested),
        # every question the number covers (resumed outcomes included)
        "questions": len(rows),
        # the subset that actually reported an activation manifest
        "measured": len(manifests),
        "ep_ran": sum(1 for m in manifests if m.get("ep_ran") is True),
        # #8486: the positive-measurement evidence for this question (the
        # pass ran AND its census found persisted posteriors) — see the
        # docstring; ``belief_layer_state`` cannot report ``activated``
        # without one per question.
        "questions_with_ep": sum(
            1 for m in manifests
            if m.get("ep_ran") is True
            and _as_int(m.get("points_with_ep")) > 0),
        "failed": sum(1 for m in manifests if m.get("error")),
        "engine": next((m.get("ep_engine") for m in manifests
                        if m.get("ep_engine")), None),
        "errors": [str(m.get("error")) for m in manifests if m.get("error")][:5],
    }
    for key in _SUMMED_KEYS:
        summary[key] = sum(_as_int(m.get(key, 0)) for m in manifests)
    return summary


def belief_layer_state(*, requested: bool,
                       activation: dict[str, Any] | None) -> str:
    """The belief-layer state the posture reports.

    ``activated`` requires a POSITIVE measurement for every question — a
    request alone is never consent (an arm that raised, or a run with no
    questions, must not read as activated), and neither is a partial one: a
    question with no manifest (a resumed outcome from before the arm) is an
    EP-INERT question in the same number, so the run is ``partial``.

    "Measured" means MEASURED, not "returned without raising": ``ep_ran`` is
    set by a bare ``try`` around ``sdk.dream(...)``, which returns normally
    with zero belief-state writes when the window holds no EP factors
    (``no_op_reason = "no_ep_factors"`` — a v2 question whose extraction
    minted claims but no IMPL/NAND operators, common on single-session
    questions) and on the #1163 stale-run stand-down. Both shapes are already
    reported by the manifest this function reads (``points_with_ep == 0``,
    ``points_unmeasured == every non-operator point``), so a question without
    a positive census is UNMEASURED → ``partial`` (fresh-context review, P1:
    the arm could otherwise publish ``beliefs_activated: true`` for a graph in
    which not one claim carries a posterior).

    ``points_unmeasured > 0`` alone deliberately does NOT force ``partial``:
    a v2 graph holds non-operator points that belong to NO factor graph (its
    turn + raw-chunk points), so they can never be stamped, and requiring the
    whole census would make ``activated`` unreachable for every real run. A
    claim with no posterior still reads ``unmeasured`` through
    ``ep_activation.read_confidence`` (#2598) — the per-point truth does not
    depend on this summary's policy.
    """
    if not requested:
        return BELIEF_LAYER_INERT
    if not isinstance(activation, dict):
        return BELIEF_LAYER_UNKNOWN
    questions = _as_int(activation.get("questions", 0))
    if questions == 0:
        # Armed, but not one question produced a manifest: unproven, never
        # "activated" on the strength of the request alone.
        return BELIEF_LAYER_PARTIAL
    # ``measured`` is absent only in an older summary shape; treat it as full
    # coverage there so a legacy dict is judged by its own ep_ran/failed cells.
    measured = _as_int(activation.get("measured", questions))
    if measured < questions:
        # Some questions never reported: the number mixes a measured belief
        # layer with an EP-inert one — never ACTIVATED.
        return BELIEF_LAYER_PARTIAL
    if (_as_int(activation.get("failed", 0)) == 0
            and _as_int(activation.get("ep_ran", 0)) == questions
            # a question the EP pass measured NOTHING in is unmeasured —
            # ``ep_ran`` alone is "dream() did not raise", never consent.
            # An absent cell is 0 (fail-closed): no positive evidence, no
            # ``activated`` claim.
            and _as_int(activation.get("questions_with_ep", 0)) == questions):
        return BELIEF_LAYER_ACTIVATED
    return BELIEF_LAYER_PARTIAL


def build(*, ingest_mode: str,
          activation: dict[str, Any] | None = None) -> dict[str, Any]:
    """The run's measurement-posture block.

    ``beliefs_activated`` is the boolean the receipt gate requires. ``None`` —
    no decision ever reached this run (``activation is None``: a report
    produced before #8486 wired the arm) — is deliberately NOT ``False``: the
    gate must refuse an unrecorded posture rather than claim one nobody took.

    It is ``True`` ONLY when the belief layer was measured for EVERY question
    (``activated``). ``inert`` and ``partial`` are both ``False``: the number
    then covers questions whose claims read an unmeasured 0.50, so describing
    the run as belief-activated would over-claim. ``belief_layer`` carries the
    distinction between them for the reader.
    """
    if activation is None:
        return {
            "ingest_mode": str(ingest_mode or ""),
            "beliefs_activated": None,
            "belief_layer": BELIEF_LAYER_UNKNOWN,
            "activation": None,
        }
    requested = bool(activation.get("requested"))
    layer = belief_layer_state(requested=requested, activation=activation)
    beliefs_activated: bool | None = (
        None if layer == BELIEF_LAYER_UNKNOWN
        else layer == BELIEF_LAYER_ACTIVATED)
    return {
        "ingest_mode": str(ingest_mode or ""),
        "beliefs_activated": beliefs_activated,
        "belief_layer": layer,
        "activation": activation,
    }


def of_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Read the posture block a report carries (``methodology`` section)."""
    if not isinstance(report, dict):
        return None
    methodology = report.get("methodology") or {}
    if not isinstance(methodology, dict):
        return None
    posture = methodology.get("measurement_posture")
    return posture if isinstance(posture, dict) else None
