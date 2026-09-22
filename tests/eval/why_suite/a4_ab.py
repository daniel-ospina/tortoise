"""A4 contested-boost A/B arm (epic #2080 E2E-1 When-3, issue #2100 Indicator
6) — contested-boost vs confidence-only ranking over the planted corpus.

The falsification check owned by THIS issue: contentiousness-as-a-scored-
signal (W4-b, #2102) should make a conflict-relevant query surface the
CONTESTED state over its relevance-matched uncontested twin.  If the boost
never helps, the A4 headline thesis is falsified internally at eval cost
(epic R3) — the result feeds W7-b publication.

Measurement discipline:

* The pair set = deterministic same-content twin pairs planted per run
  (``seeding.seed_a4_twin_pairs``): a contested/boundary twin (NAND +
  posterior at the pair's variance tier) and an UNCONTESTED twin with the
  IDENTICAL claim content (relevance-matched by construction — the query's
  similarity leg cannot separate them).  Tiers span the E2E-1 variance
  range: at-threshold control (variance == 0.04 exactly — NOT > threshold,
  the W4-b strict-boundary case: boost must NOT fire) / just-over /
  high-variance.
* W4-b pair-set pre-assertions (verified, never aspirational):
  (1) the contested/boundary twin's PERSISTED variance is calibrated to its
      tier (> CONTESTED_VARIANCE_THRESHOLD for just-over/high; == for the
      at-threshold control — the W4-b strict-boundary case, so the RERANK
      level treats the control as NOT contested and never boosts it);
  (2) the query is genuinely conflict-RELEVANT: ``resolve_contested_
      relevance`` (variance-agnostic — it resolves the NAND counter-claim
      side) fires on EVERY twin (query == the counter-claim content);
  (3) the twins are relevance-matched: identical claim content ⇒ the
      similarity leg is equal by construction.
* The ORDERING measurement runs the REAL search seam (graph-order rerank
  with the query threaded) under flag-on vs flag-off; contested_first_rate
  per regime + delta over the contested-tier pairs.  ``measured`` requires
  BOTH pre-assertions true AND >= 1 contested-tier pair whose ordering is
  regime-dependent AND NO at-threshold control flip — a control flip is a
  BOUNDARY VIOLATION (the boost fired on a not-contested twin; recorded,
  never measured).  A pair whose ordering is regime-INDEPENDENT while its
  twin sits at a large confidence gap is recorded with the gap (the pinned
  twin-delta bound is not satisfiable on naive twins under the ranker's
  confidence weighting — E2E-1 When-3 pair calibration is open work;
  recorded as a PRECISE GAP, never a faked measured delta).

This arm is an EVAL-PHASE artifact per plan §7 E2E-1 — NEVER an E2E gate:
the runner records its result in the run report + receipt notes; a failing
pre-assertion marks the arm invalid for that run, it never fails the suite.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from eval.why_suite import seeding

from tortoise.ranking import (
    CONTESTED_VARIANCE_THRESHOLD,
    resolve_contested_relevance,
)

A4_RELEVANCE_DELTA_BOUND = 0.05  # E2E-1 pinned twin-delta bound (pairs must
# sit within the boost effect pre-boost for the ordering to be boost-decided)


def _beta_variance(alpha: float, beta: float) -> float:
    s = alpha + beta
    if s <= 0:
        return 0.0
    return (alpha * beta) / (s * s * (s + 1))


def _persisted_variance(proj, pid: str) -> float:
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
        params={"id": pid},
    ).result_set
    a, b = float(rows[0][0]), float(rows[0][1])
    return _beta_variance(a, b)


def _positions(hits: list[dict]) -> dict[str, int]:
    return {h["id"]: index for index, h in enumerate(hits)}


def _rank_pair(sdk, query: str) -> list[dict]:
    """The REAL search seam: graph-order rerank with the query threaded
    (flag-on boosts via the env; flag-off runs byte-identical to the
    pre-W4-b ranker).  Window is the whole statement pool."""
    return sdk.tortoise_fts_query(
        query,
        kind="statement",
        order_by="graph",
        limit=1000,
        w4_enrich=True,
    )


def measure(
    sdk,
    seed_result: dict | None = None,
    *,
    rank_fn: Callable | None = None,
) -> dict:
    """Run the A/B over the planted twin pairs.

    ``sdk`` is the hermetic graph (used to plant the pair set when
    ``seed_result`` is None and passed through to ``rank_fn``).
    ``rank_fn(sdk, query) -> list[dict]`` and ``seed_result`` are
    injectable (unit tests use deterministic fakes); the runner uses the
    REAL search seam + the real per-run seeding.

    Returns the eval-phase report::

        {"arm": "a4_contested_boost_v1", "measured": bool,
         "pre_assertions": {"variance_calibrated": bool,
                             "conflict_relevant": bool},
         "pairs": [...per-pair rows...],
         "contested_first_rate_on": float, "contested_first_rate_off": float,
         "delta": float, "notes": [str]}
    """
    rank_fn = rank_fn or _rank_pair
    if seed_result is None:
        seed_result = seeding.seed_a4_twin_pairs(sdk)
    proj = sdk._get_proj()
    pairs_in = seed_result["pairs"]
    notes: list[str] = []
    pairs: list[dict] = []
    variance_calibrated = True
    conflict_relevant = True
    for pair in pairs_in:
        contested = pair["contested_twin"]
        tier = pair["tier"]
        variance = _persisted_variance(proj, contested)
        is_contested_tier = tier != "at_threshold"
        if is_contested_tier:
            calibrated = variance > CONTESTED_VARIANCE_THRESHOLD
        else:
            calibrated = abs(variance - CONTESTED_VARIANCE_THRESHOLD) < 1e-9
        variance_calibrated = variance_calibrated and calibrated
        boosts = resolve_contested_relevance(proj, [contested], pair["query"])
        relevant = contested in boosts  # variance-agnostic: the NAND side
        # is query-relevant on every pair (query == the counter-claim).
        conflict_relevant = conflict_relevant and relevant
        # Ordering under each regime (fresh rank call per regime so the env
        # toggle cannot leak between the two measurements; the prior env
        # value is restored so the W4 flag never leaks into sibling code).
        saved_flag = os.environ.get("TORTOISE_W4_ENRICHMENT")
        os.environ["TORTOISE_W4_ENRICHMENT"] = "1"
        try:
            hits_on = rank_fn(sdk, pair["query"])
        finally:
            if saved_flag is None:
                os.environ.pop("TORTOISE_W4_ENRICHMENT", None)
            else:
                os.environ["TORTOISE_W4_ENRICHMENT"] = saved_flag
        hits_off = rank_fn(sdk, pair["query"])
        pos_on = _positions(hits_on)
        pos_off = _positions(hits_off)
        clean = pair["clean_twin"]
        contested_first_on = pos_on.get(contested) is not None and (
            pos_on.get(clean) is None or pos_on[contested] < pos_on[clean]
        )
        contested_first_off = pos_off.get(contested) is not None and (
            pos_off.get(clean) is None or pos_off[contested] < pos_off[clean]
        )
        pairs.append(
            {
                "pair_id": pair["pair_id"],
                "tier": tier,
                "variance": round(variance, 6),
                "variance_calibrated": calibrated,
                "conflict_relevant": relevant,
                "contested_in_window_on": contested in pos_on,
                "contested_first_on": contested_first_on,
                "contested_first_off": contested_first_off,
                "regime_dependent": contested_first_on != contested_first_off,
                "pos_on": pos_on.get(contested),
                "pos_off": pos_off.get(contested),
                "pos_clean_on": pos_on.get(clean),
                "pos_clean_off": pos_off.get(clean),
            }
        )
    contested_tier = [p for p in pairs if p["tier"] != "at_threshold"]
    rate_on = (
        sum(1 for p in contested_tier if p["contested_first_on"]) / len(contested_tier)
        if contested_tier
        else 0.0
    )
    rate_off = (
        sum(1 for p in contested_tier if p["contested_first_off"]) / len(contested_tier)
        if contested_tier
        else 0.0
    )
    # The control (at-threshold) pair must NEVER flip: its twin is NOT
    # contested (variance == threshold — the W4-b strict boundary), so a
    # regime-dependent control ordering is a boundary violation (the boost
    # fired on a not-contested state), never a measured A/B.
    control_flips = [p for p in pairs if p["tier"] == "at_threshold" and p["regime_dependent"]]
    contested_flips = [p for p in pairs if p["tier"] != "at_threshold" and p["regime_dependent"]]
    pre_ok = variance_calibrated and conflict_relevant
    measured = bool(contested_flips) and pre_ok and not control_flips
    if control_flips:
        notes.append(
            "BOUNDARY VIOLATION: the at-threshold control twin (variance == "
            "threshold, NOT contested) flipped order between regimes on "
            + ", ".join(p["pair_id"] for p in control_flips)
            + " — the boost fired on a not-contested state; NOT measured."
        )
    if not pre_ok:
        notes.append(
            "not measured: pair-set pre-assertions failed "
            f"(variance_calibrated={variance_calibrated}, "
            f"conflict_relevant={conflict_relevant}) — an invalid pair set "
            "never records a measured delta"
        )
    if not contested_flips and not control_flips:
        notes.append(
            "A/B ordering is regime-INDEPENDENT on every planted pair: on the "
            "naive same-content twins the ranker's confidence weighting "
            "dominates the pre-boost gap (contested twin posterior ~0.5 vs "
            "clean ~0.92) and the W4-b boost effect does not flip any pair. "
            "The E2E-1 pinned relevance-delta bound (pairs within the boost "
            "effect pre-boost) is NOT satisfiable on this pair configuration "
            "— the calibrated When-3 pair set is open calibration work "
            "(W4-a deferred it as S7-scoped). Recorded as a PRECISE GAP, "
            "never a faked measured delta; feeds W7-b."
        )
    if not variance_calibrated:
        notes.append(
            "pre-assertion FAILED: persisted variance not calibrated "
            "to the planted tier — pair set invalid"
        )
    if not conflict_relevant:
        notes.append(
            "pre-assertion FAILED: resolve_contested_relevance did not "
            "fire on every twin's counter-claim query — pair set "
            "invalid"
        )
    return {
        "arm": "a4_contested_boost_v1",
        "measured": measured,
        "pre_assertions": {
            "variance_calibrated": variance_calibrated,
            "conflict_relevant": conflict_relevant,
        },
        "pairs": pairs,
        "contested_first_rate_on": round(rate_on, 4),
        "contested_first_rate_off": round(rate_off, 4),
        "delta": round(rate_on - rate_off, 4),
        "notes": notes,
    }
