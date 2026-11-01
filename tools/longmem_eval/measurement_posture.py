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

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.11+-only import would fail first and read as "this tool is broken" rather
# than "your interpreter is too old" (D9 shape, `tests/test_entry_point_python_
# guard.py` reds without it).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # A library module with no CLI: the refusal names the floor and the
    # interpreter it actually got, and deliberately offers NO invocation
    # (there is none to run — `tests/test_entry_point_python_guard.py`).
    raise SystemExit(
        f"tools/longmem_eval/measurement_posture.py requires Python >= 3.12 "
        f"(got {sys.version_info[0]}.{sys.version_info[1]})")

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
#: The CENSUS cells are deliberately NOT here — a census that was never read is
#: UNKNOWN, and summing it would publish a fabricated ``0`` (see
#: :data:`_UNKNOWN_PER_MANIFEST_KEYS` and the block after this loop).
_SUMMED_KEYS = (
    "points_promoted",
    "operators_promoted",
)

#: Summed cells a manifest may carry as UNKNOWN (``None``). They sum over the
#: MEASURED cells only and publish an ``<key>_unread`` count beside the sum, so
#: a partial figure cannot be mistaken for a complete measurement.
#:
#: The family is not only the GRAPH census (#10137 review 3, P2):
#: ``points_with_ep`` / ``points_unmeasured`` degrade to UNKNOWN the same way
#: when their read fails (``ep_activation._census``), and they are the cells
#: the per-question verdict is built from — a ``None`` there summed as ``0``
#: publishes "0 claims carry EP state" for a census that was never read.
#:
#: ONE rule for all four: an ABSENT cell is UNKNOWN exactly like a ``None`` one.
#: A fail-open manifest (``{"ep_ran": False, "error": ...}``) omits them, and
#: that census was never read — reading the omission as a measured ``0`` is the
#: same fabrication, one shape over.
_UNKNOWN_PER_MANIFEST_KEYS = (
    "operators",
    "factor_edges",
    "points_with_ep",
    "points_unmeasured",
)

#: #10137: the BELIEF-ELIGIBLE denominator and its substrate exclusion, summed
#: by the SAME rule as the four above but kept in their OWN tuple.
#:
#: Why separate: ``_UNKNOWN_PER_MANIFEST_KEYS`` is also the completeness set the
#: census probe reads per question (a manifest missing one of its cells is an
#: UNREAD question). Folding these in would retroactively mark every pre-#10137
#: checkpoint — whose own cells WERE measured — as unread, silently devaluing
#: published history to make room for a new cell. Adding a cell must never
#: change the disclosure state of a run that never had it.
_UNKNOWN_PER_MANIFEST_KEYS_10137 = (
    "points_substrate",
    "belief_eligible_total",
    "belief_eligible_with_ep",
    "belief_eligible_unmeasured",
)

# ── #10137: WHY a question has no measured contribution ──────────────────
# The state (``activated``/``partial``/``inert``/``unknown``) says HOW MUCH of
# the run is covered. These reasons say WHY the rest is not — enumerated, per
# question, never inferred from a green run. ONE of them is structural
# (nothing EP can do), which is exactly the distinction a fix needs:
# :data:`INERT_REASONS_STRUCTURAL` is the machine-readable form of that and
# yields exactly one reason.

#: The graph has no operator node AND no direct IMPL/NAND edge, so no factor
#: reaches a claim: EP-inert for ever, whatever the arm does (#10137). Both
#: censuses must be a MEASURED zero — an unread census is UNKNOWN and never
#: produces this verdict.
INERT_NO_OPERATORS = "no_operators"
#: The pass ran, the graph HAS operators, and none reached the window — the
#: SDK's own ``no_op_reason`` (#3139).
INERT_NO_EP_FACTORS = "no_ep_factors"
#: The pass was stood down because a concurrent run held the graph (#1163).
INERT_STALE_RUN_GUARD = "stale_run_guard"
#: Activation raised, or the runner recorded its fail-open shape.
INERT_ACTIVATION_FAILED = "activation_failed"
#: A RESUMED outcome with no activation manifest (a checkpoint written before
#: the arm existed, or under another arm) — an EP-inert question inside the
#: same number.
INERT_NO_MANIFEST = "no_manifest"
#: The pass ran but no POSITIVE measurement could be established: either no
#: claim carries persisted EP state (``points_with_ep == 0``) or the coverage
#: census could not be read at all (``points_with_ep`` is UNKNOWN). The two are
#: different facts and the manifest's raw cells say which.
INERT_UNMEASURED = "unmeasured"

#: Every reason :func:`classify_question` can return for an EP-inert question.
INERT_REASONS: frozenset[str] = frozenset({
    INERT_NO_OPERATORS, INERT_NO_EP_FACTORS, INERT_STALE_RUN_GUARD,
    INERT_ACTIVATION_FAILED, INERT_NO_MANIFEST, INERT_UNMEASURED,
})
#: The subset that is STRUCTURAL: no change to the arm, the promotion or the
#: pass can ever help, because there is no operator node AND no direct
#: IMPL/NAND edge to run on. Both censuses must be a MEASURED zero — an
#: unreachable census is UNKNOWN, and UNKNOWN never produces this verdict.
INERT_REASONS_STRUCTURAL: frozenset[str] = frozenset({INERT_NO_OPERATORS})


def _as_int(value: Any) -> int:
    """Non-negative int from a manifest cell; junk counts as 0."""
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0


def _optional_int(value: Any) -> int | None:
    """A manifest cell that can be genuinely UNKNOWN (``None``).

    #10137: ``operators == 0`` is a MEASUREMENT ("this graph mints no
    operators") and drives a structural verdict, so an ABSENT cell must not
    collapse into the same value — unknown stays unknown.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return None


def _failed(manifest: dict[str, Any]) -> bool:
    """The ONE spelling of ``_classify_question``'s ``activation_failed`` arm.

    Named and exported-in-module so the run-level cells (``ep_ran``,
    ``failed``) and the per-question classifier cannot drift apart again — that
    drift is what #10137 review 13 fixed for ``questions_with_ep`` and review 14
    for these two. A manifest FAILED if it carries a diagnosis (``error``) OR
    says the pass did not run: the two shapes a producer can emit are
    ``activate_beliefs``'s own fail-open return and
    ``run.py::_activate_beliefs_for_question``'s handler, and both must read as
    one.
    """
    return bool(manifest.get("error")) or manifest.get("ep_ran") is not True


def _failure_message(manifest: dict[str, Any]) -> str | None:
    """The ``errors`` cell for ONE manifest, or ``None`` when it did not fail.

    A manifest that failed WITHOUT naming a diagnosis still gets a line: the
    alternative is an empty ``errors`` list beside a non-zero ``failed`` count,
    which reads as "something broke and we are not saying what". The line states
    the SHAPE (no diagnosis was reported), it does not invent a cause.
    """
    if not _failed(manifest):
        return None
    error = manifest.get("error")
    if error:
        return str(error)
    return "activation failed: ep_ran is not true and no diagnosis was reported"


def _classify_question(
    manifest: dict[str, Any] | None,
) -> tuple[str | None, bool, int | None, int | None]:
    """``(inert_reason, measured, points_off_neutral, points_moved)`` for ONE.

    ``inert_reason`` is ``None`` for a question whose claims carry a measured
    posterior; otherwise it is one of :data:`INERT_REASONS`.

    Order matters, and it is causal rather than cosmetic: an activation that
    FAILED is named before its census (a partial graph would mislead), then
    the structural cause (no operator exists — nothing downstream can ever
    help), then the pass's own no-op reason, then the numeric census.

    Cells an older SDK/stub never reported are UNKNOWN, and unknown never
    produces a reason: ``operators`` absent must not read as ``no_operators``
    (a fabricated structural cause is worse than an unclassified one), so the
    classification falls through to the cells that ARE present.

    ⚠️ The two contribution cells are ``int | None`` and ``None`` is carried
    through, NOT collapsed to ``0`` (#10137 review 2, P0). A RESUMED outcome
    carrying a pre-#10137 manifest reaches here with ``points_with_ep > 0`` and
    no contribution cells — reachable without any exception, because the
    checkpoint fingerprint records only the ``activate_beliefs`` flag. Turning
    that ``None`` into ``0`` published ``neutral_questions`` — "the belief
    layer ran and moved nothing" — for a census that was never read.
    """
    if not isinstance(manifest, dict):
        return INERT_NO_MANIFEST, False, None, None
    if _failed(manifest):
        return INERT_ACTIVATION_FAILED, False, None, None
    with_ep = _optional_int(manifest.get("points_with_ep"))
    off_neutral = _optional_int(manifest.get("points_off_neutral"))
    moved = _optional_int(manifest.get("points_moved_off_neutral"))
    if with_ep is not None and with_ep > 0:
        # Measured — whatever the cause cell says. A posterior is a posterior;
        # the census is the fact (it also keeps an operator-LESS graph with
        # direct IMPL/NAND edges, #888 W5, from being called structural).
        return None, True, off_neutral, moved
    # An UNREADABLE coverage census is not "measured nothing": publish it as
    # the unclassified shape rather than letting a failed read decide the run's
    # posture (#10137 review 2, P1).
    if with_ep is None:
        return INERT_UNMEASURED, False, None, None
    operators = _optional_int(manifest.get("operators"))
    factor_edges = _optional_int(manifest.get("factor_edges"))
    if operators == 0 and factor_edges == 0:
        # Structural cause: no operator node AND no direct factor edge exists,
        # so no factor can reach a claim and nothing the arm does can ever
        # help. BOTH must be a MEASURED zero (#10137 review, P1): an IMPL/NAND
        # straight between two claims is a factor with no operator node (#888
        # W5), so `operators == 0` alone does not make the graph inert — and
        # either census being UNKNOWN must never fabricate a structural cause.
        return INERT_NO_OPERATORS, False, None, None
    reason = manifest.get("no_op_reason")
    if reason in (INERT_NO_EP_FACTORS, INERT_STALE_RUN_GUARD):
        return reason, False, None, None
    return INERT_UNMEASURED, False, None, None


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

    #10137 — coverage is not contribution, so the POPULATIONS are published
    next to the ratio, never left to be inferred from a green run:

    * ``inert_questions`` — the enumerated EP-inert population, per question
      (``question_id``, ``question_type``, ``reason`` ∈ :data:`INERT_REASONS`),
      and ``inert_by_reason`` for the counts. This is the input any fix needs;
      ``no_operators`` is STRUCTURAL (nothing EP can do will help), the rest
      are arm/pass causes.
    * ``neutral_questions`` — the measured-but-NEUTRAL population: AT LEAST
      ONE claim carries a persisted posterior and NONE of those reports a mean
      off the uniform ``Beta(1,1)`` ``0.50`` — i.e. the belief layer ran and
      moved nothing (an IMPL-only graph with no source prior is a fixed
      point). Coverage may be PARTIAL: this is a per-QUESTION verdict
      (``points_off_neutral == 0`` with ``points_with_ep > 0``), so a question
      whose 1 of 50 claims is covered belongs here exactly like a fully
      covered one — the enumerated entry carries the ``question_id`` and
      ``question_type`` (this population is a count of questions, and
      ``questions_neutral`` is its size); the per-question coverage figures
      stay on the manifest's ``points_with_ep`` / ``points_unmeasured`` cells.
      Coverage alone reports these as measured; they are the reason
      ``questions_off_neutral`` exists.
    * ``questions_off_neutral`` / ``points_off_neutral`` — the STATE
      population: questions (and claims) whose persisted posterior is not the
      neutral mean. It is NOT the contribution — a claim given a non-uniform
      baseline sits here with EP never having run — which is why
      ``points_moved_off_neutral`` narrows it to what THIS pass created.
    * ``points_moved_off_neutral`` — the subset of ``points_off_neutral`` that
      was NOT off-neutral before the pass: the claims this pass moved OFF the
      neutral mean. It is an ID-SET difference, so it detects movement FROM the
      neutral mean only — a claim that was already off-neutral and whose
      posterior the pass changed further (0.60 → 0.80) is deliberately not
      counted, and neither is one that moved back TO neutral. It answers "did
      the belief layer contribute", not "did any posterior change".
      ``operators`` / ``factor_edges`` — the graph-shape census, ``None``
      (UNKNOWN) when no manifest could read one.
    * ``by_question_type`` — the same populations split by the benchmark's own
      ``question_type``, so "single-session questions are the common case" is
      a measurement rather than an assertion. Its buckets RECONCILE with the
      run-level cells by construction (``questions_off_neutral``,
      ``points_off_neutral`` and ``points_moved_off_neutral`` are derived from
      them), and its per-type counter is ``questions_measured`` so it cannot
      be confused with the run-level ``measured`` (which counts MANIFESTS, a
      different population — #10137 review).

    Every added key is ADDITIVE and sorted deterministically: an older reader
    ignores them, and a re-run over the same outcomes produces the same block.
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
        # #10137 review 14 (P2): `ep_ran` and `failed` are DERIVED from
        # ``_classify_question``'s first arm, not re-spelled here. They were the
        # third pair of duplicate predicates in this block, and the drift was
        # the common failure shape rather than a corner: the producer
        # (``ep_activation.activate_beliefs``) used to return `ep_ran: False`
        # with NO `error` key when `dream` raised, so `failed` (keyed on
        # `error`) published 0 for a run where every pass raised — while the
        # classifier called every one of those questions
        # `activation_failed`. The producer now records the diagnosis too
        # (review 14), but the ONE-predicate rule is what makes the cells
        # agree whether or not a future producer remembers.
        #
        # The partition closes by construction: a manifest is either
        # `activation_failed` or it reported a real activation, so
        # `ep_ran + failed == measured`.
        "ep_ran": sum(1 for m in manifests if not _failed(m)),
        "failed": sum(1 for m in manifests if _failed(m)),
        "engine": next((m.get("ep_engine") for m in manifests
                        if m.get("ep_engine")), None),
        "errors": [e for e in (_failure_message(m) for m in manifests)
                   if e is not None][:5],
        # #8486: the positive-measurement evidence for this question (the
        # pass ran AND its census found persisted posteriors) — see the
        # docstring; ``belief_layer_state`` cannot report ``activated``
        # without one per question.
        #
        # #10137 review 13 (P2): the cell is DERIVED from
        # ``_classify_question``'s own ``measured`` verdict rather than
        # re-spelling the predicate here. The two spellings had drifted: this
        # one counted a manifest carrying BOTH ``ep_ran: True`` and ``error``
        # (the error arm is checked FIRST there, so that question was
        # ``activation_failed`` and the buckets counted it as 0), which broke
        # the `sum(bucket) == run-level` reconciliation this block exists to
        # guarantee — and inflated the coverage cell ``belief_layer_state``
        # builds ``activated`` on. ONE predicate, so it cannot drift again.
        "questions_with_ep": sum(
            1 for m in manifests if _classify_question(m)[1]),
    }
    for key in _SUMMED_KEYS:
        summary[key] = sum(_as_int(m.get(key, 0)) for m in manifests)
    # #10137: a census cell that is UNKNOWN per manifest must not sum as a 0 —
    # that is how a run publishes "this graph has no operators" without having
    # read a census — so the sum covers the MEASURED cells only and the unread
    # count is published beside it. A partial sum that does not say how partial
    # it is would be indistinguishable from a complete measurement (#10137
    # review 2, P1). A cell that is ABSENT is UNKNOWN for the same reason as a
    # ``None`` one — the fail-open manifest omits every census cell because
    # none was read — so the four keys share ONE rule.
    #
    # #10137 review 9 (P3): the denominator is the QUESTION population, not the
    # manifest-bearing subset. Iterating ``manifests`` made a question with NO
    # manifest (the shape this module itself classifies ``no_manifest`` — a
    # checkpoint written before the arm, or under another arm) neither KNOWN nor
    # UNREAD, so a 3-question run whose census was read for 1 of them published
    # ``operators: 7, operators_unread: 0``: a partial sum that claims to be
    # complete, which is the invariant the block above exists to keep. A missing
    # manifest is an unread census, full stop.
    for key in _UNKNOWN_PER_MANIFEST_KEYS:
        values = [
            _optional_int(o["belief_activation"].get(key))
            if isinstance(o.get("belief_activation"), dict) else None
            for o in rows]
        known = [v for v in values if v is not None]
        summary[key] = sum(known) if known else None
        summary[f"{key}_unread"] = len(values) - len(known)
    # #10137: the belief-eligible cells. The point-level pair above counts every
    # non-operator ``Point``, and an eval graph's population is dominated by the
    # ingest's episodic substrate (turn/chunk points, ``is_episodic=true``) that
    # no factor can reach — so ``points_with_ep / points`` is a FLOOR on belief
    # coverage, never belief coverage, and its dilution is unbounded (measured
    # 9.77% on the 24-question sample, ``docs/evidence/10137-corpus-ep-census/``).
    # These are the same census over the population that can actually carry a
    # belief; ``points_substrate`` publishes what was excluded (the turn and
    # transcript-chunk points the ingest writes — NOT ``is_episodic``, which the
    # ingest also stamps on the extracted claims themselves).
    for key in _UNKNOWN_PER_MANIFEST_KEYS_10137:
        values = [
            _optional_int(o["belief_activation"].get(key))
            if isinstance(o.get("belief_activation"), dict) else None
            for o in rows]
        known = [v for v in values if v is not None]
        summary[key] = sum(known) if known else None
        summary[f"{key}_unread"] = len(values) - len(known)

    # ── #10137: the belief-eligible coverage RATIO ────────────────────
    # The number this issue exists to publish: of the points that can actually
    # CARRY a belief (the non-substrate, non-operator population — turn and
    # transcript-chunk points excluded), how many carry one? UNKNOWN
    # when either side is — a ratio over a partial census is the fabricated
    # figure the ``*_unread`` companions exist to make impossible.
    _elig_total = summary.get("belief_eligible_total")
    _elig_with = summary.get("belief_eligible_with_ep")
    _elig_complete = (
        summary.get("belief_eligible_total_unread") == 0
        and summary.get("belief_eligible_with_ep_unread") == 0)
    summary["belief_eligible_ratio"] = (
        (_elig_with / _elig_total)
        if _elig_complete and isinstance(_elig_total, int)
        and isinstance(_elig_with, int) and _elig_total > 0 else None)

    # ── #10137: the POPULATIONS, enumerated per question ──────────────
    inert: list[dict[str, Any]] = []
    neutral: list[dict[str, Any]] = []
    by_reason: dict[str, int] = {}
    questions_off_neutral = 0
    by_type: dict[str, dict[str, Any]] = {}
    for outcome in rows:
        manifest = outcome.get("belief_activation")
        if not isinstance(manifest, dict):
            manifest = None
        reason, measured, off_neutral, moved = _classify_question(manifest)
        qid = outcome.get("question_id")
        qtype = str(outcome.get("question_type") or "")
        entry = {"question_id": qid, "question_type": qtype}
        # Per-type buckets count every outcome, so the split's total always
        # reconciles with ``questions`` (the ratio's own denominator).
        bucket = by_type.setdefault(qtype, {
            "questions": 0, "questions_measured": 0,
            "questions_with_ep": 0,
            "questions_off_neutral": 0, "questions_inert": 0,
            "questions_neutral": 0, "questions_contribution_unknown": 0,
            "operators": None, "operators_unread": 0,
            "factor_edges": None, "factor_edges_unread": 0,
            "points_off_neutral": 0, "points_off_neutral_unread": 0,
            "points_moved_off_neutral": 0,
            "points_moved_off_neutral_unread": 0,
        })
        bucket["questions"] += 1
        # #10137 review 10 (P3): the bucket's ``*_unread`` counters use the same
        # QUESTION denominator as the run-level cell. Guarding on ``manifest is
        # not None`` made a manifest-less question (``no_manifest`` — a
        # checkpoint written before the arm) neither counted nor unread INSIDE
        # its type, so a 3-question type whose census was read for 1 published
        # ``operators: 3, operators_unread: 0`` while the run-level cell said
        # ``operators_unread: 2``: the split stopped reconciling, and the type
        # figure claimed a completeness it did not have.
        for k in ("operators", "factor_edges"):
            v = (_optional_int(manifest.get(k))
                 if manifest is not None else None)
            if v is None:
                bucket[f"{k}_unread"] += 1
            else:
                bucket[k] = (bucket[k] or 0) + v
        if measured:
            bucket["questions_measured"] += 1
            bucket["questions_with_ep"] += 1
            # The contribution cells are UNKNOWN-preserving end to end: a
            # question whose census was never read is counted as unknown, NEVER
            # as "moved nothing" (#10137 review 2, P0).
            if off_neutral is None:
                bucket["questions_contribution_unknown"] += 1
                bucket["points_off_neutral_unread"] += 1
                bucket["points_moved_off_neutral_unread"] += 1
            else:
                bucket["points_off_neutral"] += off_neutral
                if moved is None:
                    bucket["points_moved_off_neutral_unread"] += 1
                else:
                    bucket["points_moved_off_neutral"] += moved
                if off_neutral > 0:
                    questions_off_neutral += 1
                    bucket["questions_off_neutral"] += 1
                else:
                    # covered, and every posterior IS the neutral mean — a
                    # MEASURED zero, which is the only thing allowed here
                    neutral.append(entry)
                    bucket["questions_neutral"] += 1
        else:
            # #10137 review (P2): the closed ``reason`` vocabulary is what a
            # lane routes a fix on, but the SDK's own diagnosis must not be
            # DISCARDED when it lands outside that vocabulary. Carry it
            # verbatim, but only where the closed vocabulary could not name it
            # (``unmeasured`` is literally "no diagnosis recorded") — so the
            # enumerated entry never contradicts its own reason.
            raw = manifest.get("no_op_reason") if manifest is not None else None
            entry_out = {**entry, "reason": reason}
            if reason == INERT_UNMEASURED and raw:
                entry_out["no_op_reason"] = raw
            inert.append(entry_out)
            by_reason[reason] = by_reason.get(reason, 0) + 1
            bucket["questions_inert"] += 1

    def _sorted(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(entries, key=lambda e: str(e.get("question_id")))

    summary["questions_inert"] = len(inert)
    summary["inert_questions"] = _sorted(inert)
    summary["inert_by_reason"] = dict(sorted(by_reason.items()))
    summary["questions_neutral"] = len(neutral)
    summary["neutral_questions"] = _sorted(neutral)
    summary["questions_off_neutral"] = questions_off_neutral
    # #10137 review 6 (P2): the None/0 decision is applied PER BUCKET too, not
    # only at the run level. A type whose every question was inert kept the
    # initialised ``0`` and published ``points_off_neutral: 0,
    # points_off_neutral_unread: 0`` — a MEASURED zero for a type nothing ever
    # measured — while the run-level cell correctly published ``None`` for the
    # same evidence. The split is the deliverable the issue asks for ("split by
    # question type"), so both views must be judged by one rule.
    for b in by_type.values():
        if b["questions_measured"] <= b["questions_contribution_unknown"]:
            b["points_off_neutral"] = None
        if b["questions_measured"] <= b["points_moved_off_neutral_unread"]:
            b["points_moved_off_neutral"] = None
    # #10137 review (P1): the CONTRIBUTION cells are derived from the SAME
    # classified population as the enumerated buckets, never summed over every
    # manifest. Summing all manifests published points from a question whose
    # belief layer FAILED while every enumerated view excluded it (measured:
    # summary 11 vs buckets 4), so two derivations of one number disagreed
    # inside one report. Deriving from the buckets makes the reconciliation a
    # structural property rather than a hope.
    #
    # #10137 review 3 (P1): the None/0 decision is made on the MEASURED
    # POPULATION, not on the summed cells. "No bucket has points or unread" is
    # also true of a run where EVERY question was inert — nothing reads the
    # contribution census there, so the empty buckets summed to a published
    # `points_off_neutral: 0`, i.e. exactly the fabricated "measured zero" the
    # issue exists to stop, printed by the very cell meant to expose it. A
    # bucket contributes a measurement when it classified a question with the
    # cell read.
    off_measured = any(
        b["questions_measured"] > b["questions_contribution_unknown"]
        for b in by_type.values())
    summary["points_off_neutral"] = (
        sum(b["points_off_neutral"] for b in by_type.values()
            if b["points_off_neutral"] is not None)
        if off_measured else None)
    summary["points_off_neutral_unread"] = sum(
        b["points_off_neutral_unread"] for b in by_type.values())
    moved_measured = any(
        b["questions_measured"] > b["points_moved_off_neutral_unread"]
        for b in by_type.values())
    summary["points_moved_off_neutral"] = (
        sum(b["points_moved_off_neutral"] for b in by_type.values()
            if b["points_moved_off_neutral"] is not None)
        if moved_measured else None)
    summary["points_moved_off_neutral_unread"] = sum(
        b["points_moved_off_neutral_unread"] for b in by_type.values())
    summary["questions_contribution_unknown"] = sum(
        b["questions_contribution_unknown"] for b in by_type.values())
    summary["by_question_type"] = dict(sorted(by_type.items()))
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

    ⚠️ #10137 RESIDUAL — this bar measures COVERAGE, not CONTRIBUTION, and it
    is deliberately NOT re-cut here: ``activated`` still means "every question
    carries a measured posterior", so a run that is fully covered and moved
    NOTHING (an IMPL-only graph over the unmeasured ``Beta(1,1)`` prior, whose
    fixed point is exactly ``0.50``) still reads ``activated``. That bar is
    #8486's recorded disclosure rule and tightening it is an OPEN OWNER
    DECISION, not a lane's call. What this module does instead is publish the
    contribution population BESIDE the state
    (``summarize_belief_activation`` → ``questions_off_neutral``,
    ``neutral_questions``, ``inert_questions``, ``by_question_type``), so a
    covered-but-inert run is visible on the same surface that reports
    ``activated``, and no reader has to infer it from a green run.
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
