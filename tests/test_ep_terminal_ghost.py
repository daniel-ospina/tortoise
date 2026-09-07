"""#2422 — terminal claims must not vote in EP (P6.3 ghost-must-not-vote).

#2490 — terminal posterior freeze → vacuity decay: a terminalized claim's
posterior pins at its pre-terminal value unless the terminalizing WRITER
decays it. This file also pins that every terminalizing write (retract /
supersede / invalidate / assess_source / capture-lane retraction / rebuild
folds) decays the claim to vacuity (confidence 0.5, posterior (1,1)), that
the vacuous read is stable across dreams, and that every contested reader
(annotate_ep_batch, GraphRanker/StateRanker/GapsRanker, get_contested_claims,
_review_prune, why, analyze) excludes terminal claims.

Eval-spec P6.3: a retracted / invalidated / superseded claim's ghost must not
change any live posterior — re-running EP after terminalization must equal the
graph where the claim was never connected. Root cause fixed here:
``_live_only`` excluded only ``draft``; terminal statuses (retracted /
superseded / archived) and the legacy ``outdated=true`` flag (written by
``invalidate_point`` without touching status) kept their IMPL/NAND edges and
kept voting. ``retract_point`` also never scheduled a recompute (no
``_mark_dirty``).

The P0 reproduction (E2E 2026-09-06): chain A(baseline 10,1) →op→ B → C gives
C = 0.5503; after ``retract_point(A)`` C stayed 0.5503; deleting the op→A edge
gave 0.5000 — the #689-clean reference. These tests pin that retraction /
invalidation / supersession move C to the deletion reference.
"""
from __future__ import annotations

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def posterior_mean(sdk: TortoiseSDK, pid: str) -> float:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
        params={"id": pid},
    ).result_set
    a, b = float(rows[0][0]), float(rows[0][1])
    return a / (a + b)


def build_chain(sdk: TortoiseSDK) -> dict[str, str]:
    """A (baseline 10,1) --IMPL(op)--> B --IMPL(op)--> C. C inherits A's
    strength through the chain — the ghost-voting canary."""
    a = sdk.create_point("statement", "strong source", status="live")["id"]
    b = sdk.create_point("statement", "middle claim", status="live")["id"]
    c = sdk.create_point("statement", "leaf claim", status="live")["id"]
    sdk.set_point_baseline(a, 10.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)
    sdk.set_point_baseline(c, 1.0, 1.0)
    op1 = sdk.create_operator("IMPL", a, [b])["id"]
    op2 = sdk.create_operator("IMPL", b, [c])["id"]
    return {"a": a, "b": b, "c": c, "op1": op1, "op2": op2}


def run_ep(sdk: TortoiseSDK, seeds: list[str]) -> None:
    """Cold EP run with the graph-persisted baselines as evidence (the
    canonical compute surface)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in rows} if rows else {}
    ep = sdk._get_ep()
    ep.run(seeds, max_hops=2, evidence=evidence)


# ── P6.3: retraction isolates the ghost ─────────────────────────────

def test_retracted_claim_does_not_vote(sdk, tmp_path):
    """After retract_point(A), C must move to the deletion reference — the
    dead claim's outgoing operator message is zeroed."""
    ids = build_chain(sdk)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    live_mean = posterior_mean(sdk, ids["c"])
    assert live_mean > 0.51, f"chain must carry A's strength, got {live_mean}"

    # Control: fresh graph WITHOUT A's operator edge (the #689-clean ref).
    ctrl = TortoiseSDK(db_path=str(tmp_path / "ctrl.db"))
    ctrl_ids = build_chain(ctrl)
    # remove the op1 edge A->B: delete operator edges off op1
    ctrl._get_proj().g.query(
        "MATCH (o:Point {id:$id})-[r]->() DELETE r",
        params={"id": ctrl_ids["op1"]},
    )
    run_ep(ctrl, [ctrl_ids["op1"], ctrl_ids["op2"]])
    deletion_ref = posterior_mean(ctrl, ctrl_ids["c"])

    sdk.retract_point(ids["a"])
    # Retraction must have marked the neighborhood dirty (recompute scheduled).
    dirty = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.ep_dirty = true RETURN n.id",
        params={"ids": [ids["a"], ids["b"], ids["c"]]},
    ).result_set
    assert dirty, "retract_point must mark the neighborhood ep_dirty (#2422)"
    run_ep(sdk, [ids["op1"], ids["op2"]])
    retracted_mean = posterior_mean(sdk, ids["c"])
    # P6.3 contract: |Δ| < 0.005 vs deletion. The ~2e-5 residue is damping
    # convergence noise (loopy BP never reaches exact 0.5), not a ghost vote
    # — the live run sits at 0.5503, a 0.05 drop.
    assert retracted_mean == pytest.approx(deletion_ref, abs=0.005), (
        "retracted claim's ghost must not vote: "
        f"got C={retracted_mean}, deletion reference={deletion_ref}"
    )
    assert retracted_mean < live_mean - 0.04, (
        "retraction must measurably drop C: "
        f"live={live_mean}, retracted={retracted_mean}"
    )


def test_invalidated_claim_does_not_vote(sdk, tmp_path):
    """invalidate_point writes the outdated=true flag WITHOUT changing status —
    the terminal-exclusion must cover the flag (a live-status + outdated=true
    point must not vote)."""
    ids = build_chain(sdk)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    live_mean = posterior_mean(sdk, ids["c"])

    ctrl = TortoiseSDK(db_path=str(tmp_path / "ctrl.db"))
    ctrl_ids = build_chain(ctrl)
    ctrl._get_proj().g.query(
        "MATCH (o:Point {id:$id})-[r]->() DELETE r",
        params={"id": ctrl_ids["op1"]},
    )
    run_ep(ctrl, [ctrl_ids["op1"], ctrl_ids["op2"]])
    deletion_ref = posterior_mean(ctrl, ctrl_ids["c"])

    # successor point for invalidate (CORRECTS edge target must exist)
    succ = sdk.create_point("statement", "corrected source", status="live")["id"]
    sdk.invalidate_point(ids["a"], succ)
    assert sdk.get_point(ids["a"])["outdated"] is True, (
        "invalidate_point sets outdated=true flag"
    )
    run_ep(sdk, [ids["op1"], ids["op2"]])
    invalidated_mean = posterior_mean(sdk, ids["c"])
    # P6.3 contract tolerance (see retract test — damping residue only)
    assert invalidated_mean == pytest.approx(deletion_ref, abs=0.005), (
        "invalidated (outdated=true) claim's ghost must not vote: "
        f"got C={invalidated_mean}, deletion reference={deletion_ref}"
    )
    assert invalidated_mean < live_mean - 0.04, (
        "invalidation must measurably drop C: "
        f"live={live_mean}, invalidated={invalidated_mean}"
    )
    assert live_mean != pytest.approx(deletion_ref, abs=1e-4), "sanity: chain carries strength"


def test_superseded_claim_does_not_vote(sdk, tmp_path):
    """A superseded point (status='superseded') must not vote even if any
    incident edge survived transfer."""
    ids = build_chain(sdk)
    run_ep(sdk, [ids["op1"], ids["op2"]])

    # Supersede A into a fresh successor — the old point goes terminal.
    succ = sdk.create_point("statement", "successor source", status="live")["id"]
    sdk.supersede_point(ids["a"], succ)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    # A is terminal: its factor must not feed the run. The successor is fresh
    # (no baseline) so C's strength must drop below the live baseline level —
    # C keeps only B's neutral prior through op2.
    c_mean = posterior_mean(sdk, ids["c"])
    b_mean = posterior_mean(sdk, ids["b"])
    assert c_mean <= b_mean + 0.005, (
        "superseded A must not keep pushing C above B's neutral level: "
        f"C={c_mean}, B={b_mean}"
    )


# ── Terminal seed: a dead claim seeds nothing ────────────────────────

def test_terminal_seed_runs_nothing(sdk):
    """A retracted claim used as a plain-point seed must contribute nothing
    (mirrors the draft-seed contract #780, extended to terminal #2422)."""
    a = sdk.create_point("statement", "a", status="live")["id"]
    b = sdk.create_point("statement", "b", status="live")["id"]
    sdk.create_operator("IMPL", a, [b])
    sdk.retract_point(a)
    ep = sdk._get_ep()
    affected = ep._affected_claims([a], include_draft=False)
    assert a not in affected, "terminal seed must not run itself"
    iters, converged = ep.run([a])
    assert (iters, converged) == (0, True), (
        "EP seeded only with a terminal claim must early-return"
    )


def test_terminal_claim_not_in_affected_factors(sdk):
    """A retracted claim never appears in the factor input set (its operator
    edge is not a voting factor)."""
    a = sdk.create_point("statement", "a", status="live")["id"]
    b = sdk.create_point("statement", "b", status="live")["id"]
    sdk.set_point_baseline(a, 5.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)
    op = sdk.create_operator("IMPL", a, [b])["id"]
    sdk.retract_point(a)
    ep = sdk._get_ep()
    affected = ep._affected_claims([b], include_draft=False)
    factors = ep._affected_factors(affected, include_draft=False)
    for f in factors:
        if f[0] == op:
            assert a not in f[2], (
                "retracted claim must be stripped from the operator's input_ids"
            )


def test_ep_clean_after_rebuild_retained_status(sdk, tmp_path):
    """Sanity: retraction schedules a recompute that converges on the
    retracted-claim graph (the recompute itself must not crash when the only
    seed is a terminal claim's neighborhood)."""
    ids = build_chain(sdk)
    sdk.retract_point(ids["a"])
    # The dirty-marked neighborhood must be dreamable without error and the
    # terminal seed excluded from the run set.
    result = sdk.dream(dirty_only=True)
    assert result is not None


# ── include_draft escape hatch must NOT resurrect terminal ghosts ─────

def test_include_draft_hatch_does_not_resurrect_terminal_ghost(sdk, tmp_path):
    """VGATE P1 (#2422): terminal exclusion is UNCONDITIONAL — the
    include_draft=True escape hatch re-includes drafts only, NEVER a
    retracted claim. Pre-fix, run(include_draft=True) re-admitted the
    terminal input into the degenerate operator's factor inputs and C
    returned to 0.5503 — the exact ghost."""
    ids = build_chain(sdk)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    live_mean = posterior_mean(sdk, ids["c"])

    sdk.retract_point(ids["a"])
    ep = sdk._get_ep()
    ep.run([ids["op1"], ids["op2"]], max_hops=2, include_draft=True)
    c_mean = posterior_mean(sdk, ids["c"])
    assert c_mean <= 0.505, (
        "include_draft=True must not resurrect the retracted ghost: "
        f"C={c_mean} (live was {live_mean})"
    )


# ── #2422 review-fix regressions ─────────────────────────────────────

def test_supersede_into_draft_successor_no_ghost(sdk, tmp_path):
    """Review P1 (#2422): superseding into a DRAFT successor is legal, but it
    makes the operator degenerate (draft excluded) — its stale sibling message
    (op1→B) kept voting pre-fix (C stayed 0.5503). invalidate_factor_messages
    on supersede must kill it."""
    ids = build_chain(sdk)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    live_mean = posterior_mean(sdk, ids["c"])
    assert live_mean > 0.51

    # Supersede A into a draft (never-promoted) successor.
    succ = sdk.create_point("statement", "draft successor")["id"]  # draft
    assert sdk.get_point(succ)["status"] == "draft"
    sdk.supersede_point(ids["a"], succ)
    run_ep(sdk, [ids["op1"], ids["op2"]])
    c_mean = posterior_mean(sdk, ids["c"])
    assert c_mean <= 0.505, (
        "supersede into a draft successor must not leave the ghost voting: "
        f"C={c_mean} (live was {live_mean})"
    )


def test_deprecated_status_claim_excluded_from_factors(sdk, tmp_path):
    """Review P2 (#2422): 'deprecated' is written by legacy/assessment paths
    and excluded from every read surface (search_engine/recall_state) — EP
    must not let it vote either. The Python mirror in _affected_factors must
    derive from the SAME TERMINAL_EXCLUDED_STATUSES set as the Cypher
    predicate (pre-fix the mirror treated any out-of-vocabulary status as
    terminal while the Cypher predicate admitted deprecated → the claim
    voted through Cypher paths but was stripped in Python — a within-run
    inconsistency)."""
    ids = build_chain(sdk)

    # Set A's status to deprecated directly (no SDK write path for it).
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status = 'deprecated'",
        params={"id": ids["a"]},
    )
    assert sdk.get_point(ids["a"])["status"] == "deprecated"
    ep = sdk._get_ep()
    affected = ep._affected_claims([ids["b"]], include_draft=False)
    factors = ep._affected_factors(affected, include_draft=False)
    for f in factors:
        if f[0] == ids["op1"]:
            assert ids["a"] not in f[2], (
                "deprecated input must be stripped from the operator's "
                "input_ids (Python mirror == Cypher vocabulary)"
            )
    # The Cypher-side _live_only predicate must exclude deprecated too.
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) WHERE "
        "n.status IS NULL OR n.status <> 'draft' AND "
        "(n.status IS NULL OR (n.status <> 'retracted' AND n.status <> 'superseded' "
        " AND n.status <> 'outdated' AND n.status <> 'archived' "
        " AND n.status <> 'deprecated')) AND coalesce(n.outdated,false)=false "
        "RETURN n.id",
        params={"id": ids["a"]},
    ).result_set
    assert not rows, (
        "_live_only Cypher predicate must exclude a deprecated-status claim"
    )


def test_retract_does_not_strand_terminal_dirty_root(sdk, tmp_path):
    """Review P2 (#2422): a terminal point can never enter an EP affected
    set, so marking it ep_dirty strands the flag forever (pins auto-dream to
    'local', accumulates dirty flags). _mark_dirty must exclude terminal
    points from the persisted dirty set."""
    ids = build_chain(sdk)
    sdk.retract_point(ids["a"])
    dirty = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true AND n.id = $id RETURN count(n)",
        params={"id": ids["a"]},
    ).result_set
    assert int(dirty[0][0]) == 0, (
        "retracted claim must not be ep_dirty (terminal roots are never "
        "swept — #2422 review)"
    )
    # Its LIVE reverse-BFS neighbor B must still be dirty (recompute needed).
    b_dirty = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true AND n.id = $id RETURN count(n)",
        params={"id": ids["b"]},
    ).result_set
    assert int(b_dirty[0][0]) == 1, (
        "the retracted claim's live neighbor must remain dirty for recompute"
    )
    # Hydration must not resurrect the terminal root.
    sdk._dirty_roots = set()
    sdk._hydrate_dirty_roots()
    assert ids["a"] not in sdk._dirty_roots


# ── SVBP / analyze extraction families (review P2 test-gap) ─────────

def test_svbp_factors_exclude_retracted_input(sdk, tmp_path):
    """Review P2: extract_svbp_factors (projection/__init__.py — the graph-
    wide SVBP path) independently had the retracted/outdated leak pre-fix.
    Assert it now excludes a retracted claim's operator."""
    ids = build_chain(sdk)
    sdk.retract_point(ids["a"])
    proj = sdk._get_proj()
    factors = proj.extract_svbp_factors()
    for f in factors:
        # factor tuple shape: (op_id, rel, [input...], weight, ...)
        input_ids = f[2] if len(f) > 2 else []
        assert ids["a"] not in input_ids, (
            "extract_svbp_factors must exclude a retracted input (#2422)"
        )


def test_stale_first_claims_exclude_terminal(sdk, tmp_path):
    """Review P2: analyze._stale_first_claims previously returned terminal
    claims (its stale-first dirty window feeds dreaming). Assert a retracted
    claim is not returned."""
    ids = build_chain(sdk)
    sdk.retract_point(ids["a"])
    from tortoise.analyze import _stale_first_claims
    stale = _stale_first_claims(sdk._get_proj())
    assert ids["a"] not in stale, (
        "a retracted claim must never enter the stale-first dream window "
        "(it can never be swept — #2422)"
    )
    assert ids["b"] in stale, (
        "the retracted claim's LIVE neighbor stays in the stale window"
    )


# ── assess_source outdated-flag ghost (second-model P2 coverage) ─────

def test_assess_source_superseded_assessment_no_strand(sdk, tmp_path):
    """Second-model P2: assess_source marks older assessments outdated=true
    (the flag class). The superseded assessment must be dropped from the
    dirty set (terminal can never be swept) and its neighborhood recomputed.
    Mirrors test_retract_does_not_strand_terminal_dirty_root for the
    assess_source write surface."""
    url = "https://example.com/src-ghost"
    first = sdk.assess_source(url, "agent-a", 0.9, "first assessment")
    old_id = first["assessment_point_id"]
    # Wire the assessment into an EP neighborhood (assessment → claim).
    claim = sdk.create_point("statement", "assessed claim", status="live")["id"]
    sdk.create_operator("IMPL", old_id, [claim])
    sdk._dirty_roots.clear()

    # Second assessment from the same assessor supersedes the first.
    second = sdk.assess_source(url, "agent-a", 0.1, "revised assessment")
    assert second["assessment_point_id"] != old_id
    assert sdk.get_point(old_id)["outdated"] is True

    # The superseded (outdated=true) assessment must not strand in the
    # dirty set (terminal never swept); its neighborhood IS recomputed.
    assert old_id not in sdk._dirty_roots, (
        "superseded assessment must not strand in _dirty_roots (#2422)"
    )
    claim_dirty = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.ep_dirty = true AND n.id = $id RETURN count(n)",
        params={"id": claim},
    ).result_set
    assert int(claim_dirty[0][0]) == 1, (
        "the superseded assessment's operator neighbor must be dirty for recompute"
    )


# ═══════════════════════════════════════════════════════════════════════════
# #2490 — terminal posterior vacuity decay (decay at EVERY terminalizing
# write + rebuild fold; every contested reader excludes terminals)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def sup_2490(tmp_path):
    """(db, events, sdk) with the JSONL journal wired (rebuild replay)."""
    import os
    db = os.path.join(str(tmp_path), "p2490.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _rebuild_2490(sdk, events_dir) -> None:
    sdk._get_proj().rebuild_all(str(events_dir))


def ep_store(sdk: TortoiseSDK, pid: str) -> dict:
    """Stored (not coalesced) EP/lifecycle columns of a Point."""
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.confidence, n.posterior_alpha, "
        "       n.posterior_beta, n.ep_alpha, n.ep_beta, n.status, n.outdated",
        params={"id": pid},
    ).result_set
    if not rows:
        raise AssertionError(f"no point {pid}")
    r = rows[0]
    return {"confidence": r[0], "posterior_alpha": r[1], "posterior_beta": r[2],
            "ep_alpha": r[3], "ep_beta": r[4], "status": r[5],
            "outdated": bool(r[6])}


def plant_highvar_terminal(sdk: TortoiseSDK, pid: str) -> None:
    """Force the STORED posterior of a claim to a near-balanced Beta(2,2)
    (variance 0.05 > CONTESTED_VARIANCE_THRESHOLD 0.04) BEFORE terminalizing.
    Discriminator for the reader-gate tests: pre-fix un-gated readers rank
    this terminal CONTESTED by stored variance alone — the exclusion assertion
    fails on old code even when decay+gate are both removed."""
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "SET n.confidence = 0.5, n.posterior_alpha = 2.0, "
        "    n.posterior_beta = 2.0",
        params={"id": pid})


def build_measured(sdk: TortoiseSDK) -> dict[str, str]:
    """Single measured claim A (baseline 10,1) wired to a neutral claim B via
    an IMPL operator — A gets a real EP posterior flush (~0.909 mean)."""
    a = sdk.create_point("statement", "measured source claim", status="live")["id"]
    b = sdk.create_point("statement", "neutral follow-on claim", status="live")["id"]
    sdk.set_point_baseline(a, 10.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)
    op = sdk.create_operator("IMPL", a, [b])["id"]
    run_ep(sdk, [op])
    return {"a": a, "b": b, "op": op}


def assert_vacuous(sdk: TortoiseSDK, pid: str, terminal_status: str,
                   outdated: bool) -> None:
    """#2490 core read: a terminalized claim reads confidence 0.5 + posterior
    (1,1) — the vacuous Beta(1,1) — with ep_alpha/ep_beta prior history kept."""
    st = ep_store(sdk, pid)
    if terminal_status:
        assert st["status"] == terminal_status, st
    assert st["outdated"] is outdated, st
    assert st["posterior_alpha"] == 1.0 and st["posterior_beta"] == 1.0, st
    assert st["confidence"] == 0.5, st
    assert posterior_mean(sdk, pid) == pytest.approx(0.5), st
    # Prior history is retained — the SOLE recovery vector (#2490).
    assert st["ep_alpha"] == 10.0 and st["ep_beta"] == 1.0, st


def test_retract_decays_terminal_posterior(sdk):
    """retract_point decays the claim atomically with the status write and the
    vacuous read is stable across a subsequent EP run (the terminal claim can
    never re-enter EP to repin a posterior)."""
    ids = build_measured(sdk)
    a = ids["a"]
    assert posterior_mean(sdk, a) > 0.6, "A must be measured pre-retraction"
    sdk.retract_point(a)
    assert_vacuous(sdk, a, terminal_status="retracted", outdated=False)
    # Post-dream stability: another EP run (terminal excluded as a factor)
    # must leave the decayed read untouched.
    run_ep(sdk, [ids["op"]])
    assert_vacuous(sdk, a, terminal_status="retracted", outdated=False)


def test_supersede_decays_terminal_posterior(sdk):
    """supersede_point decays the superseded old claim (status + outdated)."""
    ids = build_measured(sdk)
    a = ids["a"]
    assert posterior_mean(sdk, a) > 0.6
    succ = sdk.create_point("statement", "successor claim", status="live")["id"]
    sdk.supersede_point(a, succ)
    assert_vacuous(sdk, a, terminal_status="superseded", outdated=True)
    run_ep(sdk, [ids["op"]])
    assert_vacuous(sdk, a, terminal_status="superseded", outdated=True)


def test_invalidate_decays_terminal_posterior(sdk):
    """invalidate_point writes the legacy outdated=true FLAG without touching
    status — the decay must ride that flag write (flag-only terminal class)."""
    ids = build_measured(sdk)
    a = ids["a"]
    assert posterior_mean(sdk, a) > 0.6
    succ = sdk.create_point("statement", "correcting claim", status="live")["id"]
    sdk.invalidate_point(a, succ)
    assert_vacuous(sdk, a, terminal_status="live", outdated=True)
    run_ep(sdk, [ids["op"]])
    assert_vacuous(sdk, a, terminal_status="live", outdated=True)


def test_assess_source_decays_superseded_assessment(sdk):
    """assess_source flags older same-(url, assessor) assessments outdated —
    the flagged assessment decays atomically (alias p)."""
    url = "https://example.com/src-2490-decay"
    first = sdk.assess_source(url, "agent-a", 0.9, "first assessment")
    old_id = first["assessment_point_id"]
    claim = sdk.create_point("statement", "assessed claim", status="live")["id"]
    op = sdk.create_operator("IMPL", old_id, [claim])["id"]
    sdk.set_point_baseline(old_id, 10.0, 1.0)
    run_ep(sdk, [op])
    assert posterior_mean(sdk, old_id) > 0.6, "assessment must be measured"
    # Second assessment from the same assessor supersedes (flags) the first.
    sdk.assess_source(url, "agent-a", 0.1, "revised assessment")
    assert_vacuous(sdk, old_id, terminal_status=None, outdated=True)
    run_ep(sdk, [op])
    assert_vacuous(sdk, old_id, terminal_status=None, outdated=True)


def test_capture_lane_retraction_decays(sdk, tmp_path):
    """Capture-lane (EventAPI re-ingest) retraction: the projection fold
    (_retract) is the replay surface — a PointRetracted emitted into the
    projection must decay the tombstoned claim."""
    import os

    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    ids = build_measured(sdk)
    a = ids["a"]
    assert posterior_mean(sdk, a) > 0.6
    log = EventLog(os.path.join(str(tmp_path), "cap_events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=sdk._get_proj())
    api.retract_point(a, corrects=None)
    assert_vacuous(sdk, a, terminal_status="retracted", outdated=False)


def test_superseded_rebuild_decay_parity(sup_2490):
    """A superseded-then-rebuilt claim reads 0.5 (fold decay), NOT the
    ep_alpha-coalesced 0.909 (10,1) a journal replay would otherwise
    resurrect — the PointSuperseded fold decays the re-stamped node."""
    _, events, sdk = sup_2490
    a = sdk.create_point(
        "statement", "measured old A", status="live",
        ep_alpha=10.0, ep_beta=1.0, baseline_set=True)["id"]
    succ = sdk.create_point("statement", "successor A'", status="live")["id"]
    sdk.supersede_point(a, succ)
    pre = ep_store(sdk, a)
    assert pre["posterior_alpha"] == 1.0 and pre["confidence"] == 0.5
    _rebuild_2490(sdk, events)
    post = ep_store(sdk, a)
    # Rebuild replay must reproduce the live decayed read via the fold decay:
    # the PointSuperseded fold writes posterior (1,1) + confidence 0.5 onto
    # the re-stamped node (a NO-fold rebuild leaves posterior/confidence
    # NULL — EP columns are not projection-managed props — so a decayed
    # terminal would otherwise read back as unmeasured neutral, or as the
    # coalesced ep_alpha prior on graphs that journal it).
    assert post["posterior_alpha"] == 1.0 and post["posterior_beta"] == 1.0, post
    assert post["confidence"] == 0.5, post
    assert post["status"] == "superseded" and post["outdated"] is True, post
    assert posterior_mean(sdk, a) == pytest.approx(0.5), post


def test_retracted_rebuild_decay_parity(sup_2490):
    """retract → rebuild → 0.5: the PointRetracted fold decay ships in the
    rebuild replay (retract fold-decay must not go untested)."""
    _, events, sdk = sup_2490
    a = sdk.create_point(
        "statement", "measured retract A", status="live",
        ep_alpha=10.0, ep_beta=1.0, baseline_set=True)["id"]
    sdk.retract_point(a)
    pre = ep_store(sdk, a)
    assert pre["posterior_alpha"] == 1.0 and pre["confidence"] == 0.5
    _rebuild_2490(sdk, events)
    post = ep_store(sdk, a)
    assert post["posterior_alpha"] == 1.0 and post["posterior_beta"] == 1.0, post
    assert post["confidence"] == 0.5, post
    assert post["status"] == "retracted", post
    assert posterior_mean(sdk, a) == pytest.approx(0.5), post


# ── Reader assertions: no contested computation lists a terminal claim ─────

def _terminal_chain(sdk: TortoiseSDK) -> dict[str, str]:
    """Measured A terminalized via supersede + a LIVE measured claim X (both
    ~0.909 measured) for include/exclude contrast on the same graph."""
    ids = build_measured(sdk)  # A strong → supersede below
    succ = sdk.create_point("statement", "successor claim", status="live")["id"]
    sdk.supersede_point(ids["a"], succ)
    x = sdk.create_point("statement", "live measured contrast", status="live")["id"]
    sdk.set_point_baseline(x, 10.0, 1.0)
    opx = sdk.create_operator("IMPL", x, [ids["b"]])["id"]
    run_ep(sdk, [ids["op"], opx])
    ids.update({"succ": succ, "x": x, "opx": opx})
    return ids


def test_annotate_ep_batch_gates_terminal(sdk):
    """annotate_ep_batch: a terminal (superseded + decayed) measured claim
    reads has_ep=False + contested=False + confidence_mean 0.5; the LIVE
    measured claim keeps has_ep=True."""
    from tortoise.search_engine import annotate_ep_batch
    ids = _terminal_chain(sdk)
    ann = annotate_ep_batch(sdk._get_proj().g, [ids["a"], ids["x"]])
    ta, xa = ann[ids["a"]], ann[ids["x"]]
    assert ta.has_ep is False and ta.contested is False, ta
    assert ta.confidence_mean == 0.5, ta
    assert xa.has_ep is True and xa.contested is False, xa
    assert xa.confidence_mean == pytest.approx(0.909, abs=0.01), xa


def test_rankers_gate_terminal(sdk):
    """GraphRanker/StateRanker/GapsRanker signal fetchers: a terminal claim
    is never contested and never has_ep; its live twin stays measured."""
    from tortoise.ranking import GapsRanker, GraphRanker, StateRanker
    ids = _terminal_chain(sdk)
    proj = sdk._get_proj()
    gs = GraphRanker(projection=proj)._fetch_point_signals([ids["a"], ids["x"]])
    assert gs[ids["a"]]["contested"] is False, gs
    assert gs[ids["x"]]["contested"] is False, gs
    assert gs[ids["x"]]["confidence"] > 0.5, gs
    ss = StateRanker(projection=proj)._fetch_point_signals([ids["a"], ids["x"]])
    assert ss[ids["a"]]["has_ep"] is False and ss[ids["a"]]["contested"] is False, ss
    assert ss[ids["x"]]["has_ep"] is True, ss
    gaps = GapsRanker(projection=proj)._fetch_confidence_signals([ids["a"], ids["x"]])
    assert gaps[ids["a"]]["has_ep"] is False and gaps[ids["a"]]["contested"] is False, gaps
    assert gaps[ids["x"]]["has_ep"] is True, gaps


def test_get_contested_claims_excludes_terminal_keeps_live_unmeasured(sdk):
    """get_contested_claims excludes terminal claims (decayed (1,1) variance
    must not list) but KEEPS the unmeasured LIVE claim — Beta(1,1) fallback
    variance 1/12 > 0.04 lists it (test_agent_ops_supersede:169 pin
    semantics — NO has_ep gate here). Discriminating: the terminal carries a
    pre-terminalization stored Beta(2,2) (variance 0.05 > 0.04) — un-gated
    old code ranks it contested by stored variance."""
    ids = build_measured(sdk)
    succ = sdk.create_point("statement", "successor claim", status="live")["id"]
    plant_highvar_terminal(sdk, ids["a"])
    sdk.supersede_point(ids["a"], succ)
    unmeasured = sdk.create_point("statement", "live unmeasured claim",
                                  status="live")["id"]
    ep = sdk._get_ep()
    by_id = {c["id"]: c for c in ep.get_contested_claims()}
    assert ids["a"] not in by_id, "superseded+decayed claim must not list"
    # The live unmeasured claim (no persisted α/β → coalesced (1,1)) MUST
    # list — an unmeasured LIVE claim is not excluded by the terminal
    # predicate and there is deliberately NO has_ep gate (:169 pin).
    assert unmeasured in by_id, by_id
    assert by_id[unmeasured]["variance"] > 0.04


def test_review_prune_variance_scan_excludes_terminal(sdk):
    """_review_prune's contested variance scan: a terminal (retracted +
    decayed) measured claim is flagged STALE (status) — never CONTESTED.
    The terminal carries a pre-terminalization stored Beta(2,2) — old
    un-gated code flags it contested by stored variance."""
    ids = build_measured(sdk)
    plant_highvar_terminal(sdk, ids["a"])
    sdk.retract_point(ids["a"])
    out = sdk.review_connections(mode="prune", scope=None, prune_limit=50)
    pruned = out["prune"]
    stale = [e for e in pruned if e["issue"] == "stale"]
    contested = [e for e in pruned if e["issue"] == "contested"]
    stale_endpoints = {e["detail"].get("stale_endpoint") for e in stale}
    assert ids["a"] in stale_endpoints, stale
    for e in contested:
        assert e["detail"].get("contested_endpoint") != ids["a"], contested


def test_review_prune_nand_challenged_excludes_flag_outdated(sdk):
    """_review_prune's NAND-challenged (contested) leg: a LEGACY-INVALIDATED
    claim (status stays 'live' + outdated=true — the flag class #2490
    decays) with an incoming NAND operator edge must be flagged STALE —
    never CONTESTED.  Pre-fix the status-only gate admitted it (status
    'live') and the terminal read contested AND stale — the carve rested on
    the false premise that status='live' implies live.  The LIVE counter
    claim (outdated null) stays legitimately contested."""
    x = sdk.create_point("statement", "X challenged claim", status="live")["id"]
    y = sdk.create_point("statement", "Y live counter claim", status="live")["id"]
    sdk.create_operator("NAND", x, [y])
    # Legacy invalidate: flag-only terminalization (status untouched).
    corr = sdk.create_point("statement", "X corrected replacement",
                            status="live")["id"]
    sdk.invalidate_point(x, corr)
    out = sdk.review_connections(mode="prune", scope=None, prune_limit=50)
    pruned = out["prune"]
    stale = [e for e in pruned if e["issue"] == "stale"]
    contested = [e for e in pruned if e["issue"] == "contested"]
    stale_endpoints = {e["detail"].get("stale_endpoint") for e in stale}
    assert x in stale_endpoints, stale
    contested_endpoints = {
        e["detail"].get("contested_endpoint") for e in contested
    }
    assert x not in contested_endpoints, contested
    # The live counter claim is still challenged (its own NAND) — contested.
    assert y in contested_endpoints, contested


def test_why_direct_read_terminal_not_contested(sdk):
    """why() direct id-lookup of a superseded claim (the supersession block
    must serve terminal ids): the ep sub-block reads has_ep=False +
    contested=False (projection-side override — NOT a WHERE drop)."""
    from tortoise.why import assemble_why_blocks
    ids = _terminal_chain(sdk)
    blocks = assemble_why_blocks(sdk._get_proj(), [ids["a"], ids["x"]])
    ep_a = blocks[ids["a"]]["ep"]
    assert ep_a["has_ep"] is False and ep_a["contested"] is False, ep_a
    assert ep_a["confidence_mean"] == 0.5, ep_a
    ep_x = blocks[ids["x"]]["ep"]
    assert ep_x["has_ep"] is True and ep_x["confidence_mean"] > 0.5, ep_x
    # Supersession context is still served for the terminal id.
    assert blocks[ids["a"]]["supersession"].get("status") == "superseded"


def test_analyze_most_uncertain_and_trends_exclude_terminal(sdk):
    """analyze most_uncertain/trends ORDER BY variance DESC must not list a
    decayed terminal (variance 1/12 > 0.04 would top the ranking)."""
    from tortoise.analyze import analyze
    phrase = "how the strategy over time?"  # exact entity classify() extracts
    a = sdk.create_point("statement", phrase + " measured claim TERM-A",
                         status="live")["id"]
    x = sdk.create_point("statement", phrase + " measured claim LIVE-X",
                         status="live")["id"]
    b = sdk.create_point("statement", "neutral follower", status="live")["id"]
    sdk.set_point_baseline(a, 10.0, 1.0)
    sdk.set_point_baseline(x, 10.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)
    op_a = sdk.create_operator("IMPL", a, [b])["id"]
    op_x = sdk.create_operator("IMPL", x, [b])["id"]
    run_ep(sdk, [op_a, op_x])
    sdk.retract_point(a)
    proj = sdk._get_proj()

    res = analyze("what are we most uncertain about?", proj)
    assert res["pattern"] == "most_uncertain", res["pattern"]
    raw_ids = {r[0] for r in res["raw"]}
    assert a not in raw_ids, f"decayed terminal listed as most uncertain: {res['raw']}"
    assert x in raw_ids, f"live measured claim missing from most uncertain: {res['raw']}"

    res2 = analyze("how has the strategy changed over time?", proj)
    assert res2["pattern"] == "trends", res2["pattern"]
    raw2 = {r[0] for r in res2["raw"]}
    assert a not in raw2, f"decayed terminal listed in trends: {res2['raw']}"
    assert x in raw2, f"live measured claim missing from trends: {res2['raw']}"


def test_w4_boost_never_fires_on_terminal(sdk, monkeypatch):
    """W4-b relevance-gated contested boost: a terminal claim is gated to
    contested=False upstream, so the boost resolver never sees it and
    w4_contested_boost never fires — even under the W4 flag + a query."""
    from tortoise.ranking import StateRanker
    ids = build_measured(sdk)
    plant_highvar_terminal(sdk, ids["a"])
    sdk.retract_point(ids["a"])
    monkeypatch.setenv("TORTOISE_W4_ENRICHMENT", "1")
    ranker = StateRanker(projection=sdk._get_proj())
    results = [{"id": ids["a"], "entity_type": "point",
                "confidence": 0.5, "similarity": 0.8}]
    ranked = ranker.rerank(results, entity_type="point",
                           query="measured source claim")
    rr = ranked[0]["recall_ranking"]
    assert rr["contested"] is False, rr
    assert "w4_contested_boost" not in rr, rr
