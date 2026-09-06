"""#2422 — terminal claims must not vote in EP (P6.3 ghost-must-not-vote).

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
