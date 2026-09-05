"""W3-b why-suite deterministic seeding (epic #2080, issue #2100).

Plants the shared E2E-1/E2E-7 planted-conflict corpus onto a hermetic graph
and returns the per-topic ROLE MAP (topic key → planted point ids per role)
that the runner's dig-deeper grading resolves gold target_roles against.

The plant structure mirrors W4-a's E2E-1 seeding EXACTLY (the jointly-pinned
source: tests/test_w4_why_enrichment.py::_seed_e2e1_corpus + its _plant_*
helpers, issue #2101) — topic keys + point content strings + posterior
calibration are byte-identical, so the assembled why-block grades the SAME
artifact W4-a's E2E-1 measures.  The one deliberate deviation: a clean
topic's SECOND record is planted at posterior (11, 1) instead of (12, 1) so
the two clean supports never tie on EP weight — the assembly's
deterministic supports-pointer target (highest weight) is then the FIRST
record on every run (a tied pair would tie-break on runtime ULIDs and make
dig-deeper navigation grading nondeterministic; composition + content +
graded-point denominators are unaffected — the seeding drift test asserts
equality with W4-a's real seeding over composition + planted content).

The corpus manifest (fixture side) is the source of the topic lists + role
templates; a drift between this planting and the jointly-pinned manifest
fails the runner's preflight (a planted topic missing a manifest role, or a
role resolving to the wrong point, is a LOUD run error — never a silent
skip).

Hermetic: no network/LLM.  The caller opens the throwaway graph.
"""

from __future__ import annotations

from eval.why_suite import corpus


def _set_posterior(proj, pid: str, alpha: float, beta: float) -> None:
    """Persist EP posterior params the way compute_confidence does
    (n.confidence = posterior mean; posterior_alpha/beta drive variance /
    contested) — mirrors tests/test_w4_why_enrichment.py::_set_posterior."""
    s = alpha + beta
    mean = round(alpha / s, 4) if s > 0 else 0.5
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta, "c": mean},
    )


def _mean_of(proj, pid: str) -> float:
    """The PERSISTED posterior mean of one point (graph truth)."""
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence",
        params={"id": pid},
    ).result_set
    if not rows or rows[0][0] is None:
        raise ValueError(f"posterior mean missing for {pid} — plant is incomplete")
    return float(rows[0][0])


def _assert_means_differ(proj, option_a: str, option_b: str, topic: str) -> None:
    """Tie-freedom: the planted EP-favored alternative must be UNIQUE —
    equal posterior means would make "the EP-favored option" an id-tiebreak
    artifact (review P3, #2100).  Reads persisted confidence (the runner
    grades the same graph truth) and raises on a tie."""
    ma, mb = _mean_of(proj, option_a), _mean_of(proj, option_b)
    if ma == mb:
        raise ValueError(
            f"decision topic {topic!r} plants EQUAL option posterior means "
            f"({ma}) — the EP-favored alternative is not unique; adjust the "
            "plant (e.g. 4.0/1.0 vs 5.0/2.0) so the tradeoff-sufficiency "
            "grade is meaningful"
        )


def _create_point(sdk, kind: str, content: str) -> str:
    res = sdk.create_point(kind, content)
    return res["id"]


def _imply(sdk, source_id: str, target_id: str) -> None:
    sdk.create_operator("IMPL", source_id, [target_id])


def _nand(sdk, source_id: str, target_id: str) -> None:
    sdk.create_operator("NAND", source_id, [target_id])


# ── Per-family planters (mirror the W4-a _plant_* helpers) ─────────────────


def _plant_conflicted_roles(sdk, proj, topic: str, *, alpha: float, beta: float) -> dict[str, str]:
    """One conflicted claim: 1 IMPL support + 1 NAND counterargument +
    balanced persisted posterior (variance > threshold → contested).
    Returns {claim, support, counter} ids."""
    support = _create_point(sdk, "evidence", f"{topic} supporting record alpha")
    claim = _create_point(sdk, "statement", f"{topic} belief statement")
    _imply(sdk, support, claim)
    counter = _create_point(sdk, "statement", f"{topic} counterargument gamma")
    _nand(sdk, counter, claim)
    _set_posterior(proj, claim, alpha, beta)
    _set_posterior(proj, support, 12.0, 1.0)
    _set_posterior(proj, counter, 6.0, 1.0)
    return {"claim": claim, "support": support, "counter": counter}


def _plant_decision_roles(sdk, proj, topic: str) -> dict[str, str]:
    """One decision point (kind=decision, 2 option alternatives via
    operators, mitigation on each connecting operator, conflicted structure:
    IMPL support + NAND counter + variance > threshold)."""
    support = _create_point(sdk, "evidence", f"{topic} decision support record")
    decision = _create_point(sdk, "decision", f"{topic} decision point")
    option_a = _create_point(sdk, "option", f"{topic} alternative one")
    option_b = _create_point(sdk, "option", f"{topic} alternative two")
    counter = _create_point(sdk, "statement", f"{topic} decision counterargument")
    _imply(sdk, support, decision)
    op1 = sdk.create_operator("IMPL", decision, [option_a])
    op2 = sdk.create_operator("IMPL", decision, [option_b])
    _nand(sdk, counter, decision)
    sdk.mitigate_operator(op1["id"], "QA gate + staged rollout")
    sdk.mitigate_operator(op2["id"], "communicate the delay")
    _set_posterior(proj, decision, 2.0, 2.0)
    _set_posterior(proj, option_a, 4.0, 1.0)  # mean 0.8 — the EP favorite
    _set_posterior(proj, option_b, 5.0, 2.0)  # mean ~0.714
    _set_posterior(proj, support, 12.0, 1.0)
    _set_posterior(proj, counter, 6.0, 1.0)
    # TIE-FREEDOM plant-time assert (review P3, #2100): the EP-favored
    # alternative only MEANS something when the planted option posteriors
    # differ — an edit that makes them equal would silently flip "favored"
    # to the id tie-break.  Read the PERSISTED means back (the graph-truth
    # the runner grades under) and fail loud on a tie.
    _assert_means_differ(proj, option_a, option_b, topic)
    return {
        "decision": decision,
        "support": support,
        "option_a": option_a,
        "option_b": option_b,
        "counter": counter,
    }


def _plant_superseded_roles(sdk, proj, topic: str) -> dict[str, str]:
    """One superseded predecessor: conflicted structure (IMPL + NAND +
    variance > threshold) PLUS a CORRECTS edge from a successor and
    status='superseded' (outdated + validTo window end).  Mirrors W4-a's
    raw-state E2E-1 corpus plant — the predecessor KEEPS its conflict
    structure (the superseded denominator E2E-1 measures) while the
    assembly's supersession view resolves the successor via the incoming
    CORRECTS edge."""
    inner = _plant_conflicted_roles(sdk, proj, topic, alpha=2.0, beta=2.0)
    old = inner["claim"]
    successor = _create_point(sdk, "statement", f"{topic} successor belief")
    _set_posterior(proj, successor, 10.0, 1.0)
    proj.g.query(
        "MATCH (n:Point {id:$old}), (s:Point {id:$new}) "
        "CREATE (s)-[:CORRECTS]->(n) SET n.status='superseded', "
        "n.outdated=true, n.validTo='2026-06-01'",
        params={"old": old, "new": successor},
    )
    return {
        "old": old,
        "support": inner["support"],
        "counter": inner["counter"],
        "successor": successor,
    }


def _plant_clean_roles(sdk, proj, topic: str) -> dict[str, str]:
    """One clean claim: 2 IMPL supports + high-support posterior (NOT
    contested), no NANDs anywhere.  Second record posterior (11, 1) — see
    the module docstring (deterministic supports-pointer target)."""
    support_a = _create_point(sdk, "evidence", f"{topic} clean record one")
    support_b = _create_point(sdk, "evidence", f"{topic} clean record two")
    claim = _create_point(sdk, "statement", f"{topic} clean belief statement")
    _imply(sdk, support_a, claim)
    _imply(sdk, support_b, claim)
    _set_posterior(proj, claim, 12.0, 1.0)
    _set_posterior(proj, support_a, 12.0, 1.0)
    _set_posterior(proj, support_b, 11.0, 1.0)
    return {"claim": claim, "support_a": support_a, "support_b": support_b}


# Family → planter + the manifest role the planter output uses as the
# family's GRADED point id (claim / decision / old — the assembled point).
_FAMILY_PLANTERS = {
    "p9": ("claim", _plant_conflicted_roles, {"alpha": 1.5, "beta": 1.5}),
    "plain": ("claim", _plant_conflicted_roles, {"alpha": 2.0, "beta": 2.0}),
    "decision": ("decision", _plant_decision_roles, None),
    "superseded": ("old", _plant_superseded_roles, None),
    "clean": ("claim", _plant_clean_roles, None),
}


def seed_why_corpus(sdk) -> dict:
    """Plant the full 40-point E2E-1 corpus onto ``sdk``'s graph.

    Returns the role map ``{topic_key: {role: point_id}}`` + the
    per-family graded-point lists, keyed for the runner::

        {"roles": {topic: {role: id}}, "graded": {family: [point ids]},
         "manifest_topics": {family: [topic keys]}}
    """
    manifest = corpus.load_manifest()
    proj = sdk._get_proj()
    roles: dict[str, dict[str, str]] = {}
    graded: dict[str, list[str]] = {}
    topics = manifest["topics"]
    for family in ("p9", "decision", "superseded", "plain", "clean"):
        graded_key, planter, params = _FAMILY_PLANTERS[family]
        graded_list: list[str] = []
        for topic in topics[family]:
            if params is not None:
                planted = planter(sdk, proj, topic, **params)
            else:
                planted = planter(sdk, proj, topic)
            roles[topic] = planted
            graded_list.append(planted[graded_key])
        graded[family] = graded_list
    return {"roles": roles, "graded": graded, "manifest_topics": topics}


def seed_topic(sdk, topic: str) -> dict[str, str]:
    """Plant ONE topic (used by tests) and return its role map."""
    manifest = corpus.load_manifest()
    topics = manifest["topics"]
    proj = sdk._get_proj()
    for family, keys in topics.items():
        if topic in keys:
            _, planter, params = _FAMILY_PLANTERS[family]
            if params is not None:
                return planter(sdk, proj, topic, **params)
            return planter(sdk, proj, topic)
    raise ValueError(f"topic {topic!r} is not in the jointly-pinned manifest")


# ── A4 twin-pair planting (contested-boost A/B, epic E2E-1 When-3) ────────
# Twin pairs: SAME claim content planted twice — a contested/boundary twin
# (NAND + balanced posterior at the pair's variance tier) and a clean twin
# (2 supports + high posterior, no NAND).  A query over the contested twin's
# counter-claim is conflict-relevant BY CONSTRUCTION (W4-b significant-token
# rule: the query shares every token with the counter-claim) while the
# twins' shared claim content keeps them relevance-matched.  Tiers span the
# variance range per E2E-1 (at-threshold control, just-over, high) — W4-b
# pair-set pre-assertions.
A4_TIER_POSTERIORS: list[tuple[str, float, float]] = [
    ("at_threshold", 2.625, 2.625),  # variance == 0.04 exactly → NOT > threshold (control)
    ("just_over", 2.0, 2.0),  # variance 0.05 > threshold
    ("high", 1.5, 1.5),  # variance ~0.0714 >> threshold
]
A4_PAIRS_PER_TIER = 2  # n = 6 pairs (E2E-1: 5-10 spanning the range)


def seed_a4_twin_pairs(sdk) -> dict:
    """Plant the A4 twin pairs (deterministic, per-run — NOT part of the
    committed 40-point corpus; the pair set is E2E-1's When-3 ranking set
    which W4-a deferred as S7-scoped).  Returns::

        {"pairs": [{"pair_id", "tier", "contested_twin", "clean_twin",
                    "counter", "query"}], "posterior": {pair_id: (a, b)}}
    """
    proj = sdk._get_proj()
    pairs: list[dict] = []
    index = 0
    for tier, alpha, beta in A4_TIER_POSTERIORS:
        for _ in range(A4_PAIRS_PER_TIER):
            topic = f"a4-twin-{index}"
            # Contested/boundary twin: conflicted structure at the tier
            # posterior (variance > threshold for just_over/high; exactly at
            # threshold for the at_threshold control → NOT contested).
            contested = _plant_conflicted_roles(sdk, proj, topic, alpha=alpha, beta=beta)
            # Clean twin: IDENTICAL claim content to the contested twin (the
            # relevance-matched pair), no NAND, high-support posterior.
            shared = f"{topic} belief statement"
            clean_twin = _create_point(sdk, "statement", shared)
            support = _create_point(sdk, "evidence", f"{topic} clean twin record")
            _imply(sdk, support, clean_twin)
            _set_posterior(proj, clean_twin, 12.0, 1.0)
            _set_posterior(proj, support, 12.0, 1.0)
            counter = contested["counter"]
            query = f"{topic} counterargument gamma"
            pairs.append(
                {
                    "pair_id": f"pair-{index}",
                    "tier": tier,
                    "contested_twin": contested["claim"],
                    "clean_twin": clean_twin,
                    "counter": counter,
                    "query": query,
                }
            )
            index += 1
    return {"pairs": pairs}


# Re-exported for the drift test: the W4-a planting composition this seeding
# mirrors (used by the runner's post-seed composition assertion).
def graded_point_ids(seed_result: dict, family: str) -> list[str]:
    return seed_result["graded"][family]
