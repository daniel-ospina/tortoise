"""#780 — EP draft filter + draft operator nodes (DE2E-4).

Shared live-only predicate `_live_only(clause, include_draft=False)` applied at
ALL FOUR factor-extraction call sites:

1. TortoiseEP._affected_claims / _affected_factors (ep.py) — via run()
2. extract_svbp_factors (projection/__init__.py — graph-wide SVBP path)
3. _bfs_select_operators (analyze.py)
4. _select_subgraph (sdk.py)

DE2E-4 (epic insight-mining-p2p4 §7): a DELIBERATE LEAK fixture — a pre-existing
LIVE operator from a live claim to one draft Point. With include_draft=False the
live claim's posterior is INVARIANT; with include_draft=True the same graph
CHANGES the posterior — proving the filter (not the wiring) is causal.
"""
from __future__ import annotations

import pytest

from tortoise.ep import TortoiseEP
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def set_evidence(sdk: TortoiseSDK, pid: str, alpha: float, beta: float) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, n.baseline_set=true",
        params={"id": pid, "al": alpha, "be": beta},
    )


def posterior_mean(sdk: TortoiseSDK, pid: str) -> float:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
        params={"id": pid},
    ).result_set
    a, b = float(rows[0][0]), float(rows[0][1])
    return a / (a + b)


def make_leak_graph(sdk: TortoiseSDK):
    """s (live, strong) IMPL l (live, neutral) via live operator O1;
    live operator O2 (leak) wired l IMPL d where d is DRAFT."""
    s = sdk.create_point("statement", "strong source", status="live")
    l = sdk.create_point("statement", "live claim", status="live")
    d = sdk.create_point("statement", "draft leak target")  # status: draft
    set_evidence(sdk, s["id"], 8.0, 1.0)
    set_evidence(sdk, l["id"], 1.0, 1.0)
    set_evidence(sdk, d["id"], 12.0, 1.0)  # strong draft — would pull l if leaked
    o1 = sdk.create_operator("IMPL", s["id"], [l["id"]])
    o2 = sdk.create_operator("IMPL", l["id"], [d["id"]])  # leak: live op -> draft
    return {"s": s["id"], "l": l["id"], "d": d["id"], "o1": o1["id"], "o2": o2["id"]}


def run_ep(sdk: TortoiseSDK, seeds: list[str], include_draft: bool = False
           ) -> tuple[int, bool]:
    """Run EP the canonical way: graph baselines become run evidence (the
    posterior computation consumes run_evidence, not raw graph priors)."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in rows} if rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    return ep.run(seeds, max_hops=2, include_draft=include_draft)


# ── DE2E-4: leak fixture proves the filter is causal ──────────────

def test_de2e4_leak_filter_causal(sdk, tmp_path):
    """Live posterior invariant under include_draft=False, changed under True."""
    ids = make_leak_graph(sdk)

    # Default run (include_draft=False): leak operator O2 is degenerate after
    # draft-stripping (only l remains live) — l's posterior must equal the
    # no-leak control run.
    run_ep(sdk, [ids["o1"], ids["o2"]])
    leak_default = posterior_mean(sdk, ids["l"])

    # Control: fresh graph WITHOUT the leak operator (separate DB).
    ctrl = TortoiseSDK(db_path=str(tmp_path / "ctrl.db"))
    c = ctrl.create_point("statement", "strong source", status="live")
    lc = ctrl.create_point("statement", "live claim", status="live")
    set_evidence(ctrl, c["id"], 8.0, 1.0)
    set_evidence(ctrl, lc["id"], 1.0, 1.0)
    o1c = ctrl.create_operator("IMPL", c["id"], [lc["id"]])
    run_ep(ctrl, [o1c["id"]])
    ctrl_mean = posterior_mean(ctrl, lc["id"])

    assert leak_default == pytest.approx(ctrl_mean, abs=1e-6), (
        "leak operator must NOT change the live posterior with include_draft=False"
    )

    # Escape hatch: include_draft=True re-includes the draft — posterior changes.
    run_ep(sdk, [ids["o1"], ids["o2"]], include_draft=True)
    leak_inclusive = posterior_mean(sdk, ids["l"])
    assert leak_inclusive != pytest.approx(leak_default, abs=1e-4), (
        "include_draft=True must re-wire the leak and change the posterior"
    )


def test_de2e4_run_default_excludes_draft_points(sdk):
    ids = make_leak_graph(sdk)
    ep = sdk._get_ep()
    affected = ep._affected_claims([ids["o2"]], include_draft=False)
    assert ids["d"] not in affected, "draft target must be excluded from affected"
    assert ids["l"] in affected
    factors = ep._affected_factors(affected, include_draft=False)
    op_ids = {f[0] for f in factors}
    assert ids["o2"] not in op_ids, (
        "leak operator must be degenerate (single live input) and skipped"
    )
    assert ids["o1"] in op_ids


def test_de2e4_include_draft_reenables_identically(sdk, tmp_path):
    """run(include_draft=True) on a draft-inclusive graph must equal the
    default run on an IDENTICAL all-live graph — the escape hatch re-includes
    drafts identically (same factor set, same posterior)."""
    ids = make_leak_graph(sdk)
    run_ep(sdk, [ids["o1"], ids["o2"]], include_draft=True)
    inclusive = posterior_mean(sdk, ids["l"])

    # Identical topology, all nodes live from creation (separate DB).
    ctrl = TortoiseSDK(db_path=str(tmp_path / "ctrl.db"))
    s2 = ctrl.create_point("statement", "strong source", status="live")
    l2 = ctrl.create_point("statement", "live claim", status="live")
    d2 = ctrl.create_point("statement", "draft leak target", status="live")
    set_evidence(ctrl, s2["id"], 8.0, 1.0)
    set_evidence(ctrl, l2["id"], 1.0, 1.0)
    set_evidence(ctrl, d2["id"], 12.0, 1.0)
    o1b = ctrl.create_operator("IMPL", s2["id"], [l2["id"]])
    o2b = ctrl.create_operator("IMPL", l2["id"], [d2["id"]])
    run_ep(ctrl, [o1b["id"], o2b["id"]])
    promoted = posterior_mean(ctrl, l2["id"])
    assert inclusive == pytest.approx(promoted, abs=1e-6), (
        "include_draft=True must reproduce the all-live run exactly"
    )


# ── Call site 1: TortoiseEP._affected_claims / _affected_factors ──

def test_affected_claims_excludes_draft_operators_and_targets(sdk):
    s = sdk.create_point("statement", "s", status="live")
    live_t = sdk.create_point("statement", "live target", status="live")
    draft_t = sdk.create_point("statement", "draft target")
    live_op = sdk.create_operator("IMPL", s["id"], [live_t["id"]])
    draft_op = sdk.create_operator("IMPL", s["id"], [draft_t["id"]],
                                   promote_source=False)
    ep = sdk._get_ep()
    affected = ep._affected_claims([live_op["id"], draft_op["id"]],
                                   include_draft=False)
    assert live_t["id"] in affected
    assert draft_t["id"] not in affected, "draft target must be excluded"
    factors = ep._affected_factors(affected, include_draft=False)
    op_ids = {f[0] for f in factors}
    assert live_op["id"] in op_ids
    assert draft_op["id"] not in op_ids, "draft operator must be excluded"

    # Escape hatch: both included.
    affected2 = ep._affected_claims([live_op["id"], draft_op["id"]],
                                    include_draft=True)
    assert draft_t["id"] in affected2
    assert draft_op["id"] in {f[0] for f in
                              ep._affected_factors(affected2, include_draft=True)}


def test_affected_factors_strips_draft_input_ids(sdk):
    """A LIVE operator with one draft input keeps its live inputs; if all
    inputs are draft it becomes degenerate and is skipped."""
    live_a = sdk.create_point("statement", "a", status="live")
    live_b = sdk.create_point("statement", "b", status="live")
    draft_c = sdk.create_point("statement", "c")
    op = sdk.create_operator("IMPL", live_a["id"], [live_b["id"], draft_c["id"]])
    ep = sdk._get_ep()
    affected = ep._affected_claims([op["id"]], include_draft=False)
    factors = ep._affected_factors(affected, include_draft=False)
    (f,) = [f for f in factors if f[0] == op["id"]]
    assert draft_c["id"] not in f[2], "draft id must be stripped from input_ids"
    assert set(f[2]) == {live_a["id"], live_b["id"]}

    # All-draft operator (created with promote_source=False): excluded.
    d1 = sdk.create_point("statement", "d1")
    d2 = sdk.create_point("statement", "d2")
    op2 = sdk.create_operator("IMPL", d1["id"], [d2["id"]], promote_source=False)
    affected2 = ep._affected_claims([op2["id"]], include_draft=False)
    assert affected2 == set() or all(
        f[0] != op2["id"] for f in ep._affected_factors(affected2, include_draft=False)
    )


# ── Call site 2: extract_svbp_factors (SVBP path) ─────────────────

def test_extract_svbp_factors_excludes_drafts(sdk):
    proj = sdk._get_proj()
    s = sdk.create_point("statement", "s", status="live")
    a = sdk.create_point("statement", "a", status="live")
    b = sdk.create_point("statement", "b", status="live")
    d = sdk.create_point("statement", "d")
    sdk.create_operator("IMPL", s["id"], [a["id"], b["id"]])
    draft_op = sdk.create_operator("IMPL", s["id"], [d["id"], a["id"]],
                                   promote_source=False)

    factors, _ = proj.extract_svbp_factors()  # default: exclude drafts
    op_ids = {f[0] for f in factors}
    assert draft_op["id"] not in op_ids, "draft operator must not feed SVBP"
    # The live operator's factor must not contain the draft input.
    for f in factors:
        if f[1] == "IMPL":
            assert d["id"] not in f[2]

    factors2, _ = proj.extract_svbp_factors(include_draft=True)
    assert draft_op["id"] in {f[0] for f in factors2}


# ── Call sites 3+4: _bfs_select_operators / _select_subgraph ──────

def test_bfs_select_operators_excludes_drafts(sdk):
    from tortoise.analyze import _bfs_select_operators
    proj = sdk._get_proj()
    s = sdk.create_point("statement", "s", status="live")
    a = sdk.create_point("statement", "a", status="live")
    b = sdk.create_point("statement", "b", status="live")
    d = sdk.create_point("statement", "d")
    live_op = sdk.create_operator("IMPL", s["id"], [a["id"], b["id"]])
    draft_op = sdk.create_operator("IMPL", s["id"], [d["id"], b["id"]],
                                   promote_source=False)

    ops = _bfs_select_operators(proj, [s["id"]], include_draft=False)
    assert live_op["id"] in ops
    assert draft_op["id"] not in ops, "draft operator must be excluded"

    # Draft anchor: excluded entirely (no expansion from a draft).
    ops2 = _bfs_select_operators(proj, [d["id"]], include_draft=False)
    assert draft_op["id"] not in ops2

    # Escape hatch.
    ops3 = _bfs_select_operators(proj, [s["id"]], include_draft=True)
    assert draft_op["id"] in ops3


def test_select_subgraph_excludes_drafts(sdk):
    s = sdk.create_point("statement", "s", status="live")
    a = sdk.create_point("statement", "a", status="live")
    d = sdk.create_point("statement", "d")
    live_op = sdk.create_operator("IMPL", s["id"], [a["id"]])
    draft_op = sdk.create_operator("IMPL", s["id"], [d["id"]], promote_source=False)

    ops = sdk._select_subgraph([s["id"]], include_draft=False)
    assert live_op["id"] in ops
    assert draft_op["id"] not in ops
    ops2 = sdk._select_subgraph([s["id"]], include_draft=True)
    assert draft_op["id"] in ops2


# ── create_operator(promote_source=False) — draft operator nodes ──

def test_create_operator_promote_source_false_writes_draft_operator(sdk):
    a = sdk.create_point("statement", "A")
    b = sdk.create_point("statement", "B")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]], promote_source=False)
    assert op.get("status") == "draft", (
        "extraction operator node must carry status:'draft'"
    )
    assert sdk.get_point(a["id"]).get("status") == "draft", (
        "promote_source=False must NOT auto-promote the source (#131 bypass)"
    )
    assert sdk.get_point(b["id"]).get("status") == "draft"


def test_create_operator_default_promotes_source(sdk):
    """Back-compat: promote_source=True (default) preserves #131 promotion."""
    a = sdk.create_point("statement", "A")
    b = sdk.create_point("statement", "B")
    op = sdk.create_operator("IMPL", a["id"], [b["id"]])
    assert op.get("status") is None, "legacy: operator node has no status property"
    assert sdk.get_point(a["id"]).get("status") == "live"


def test_create_operator_draft_status_survives_event_payload(sdk, tmp_path):
    """The OperatorAdded event must carry the draft status so JSONL replay
    (projection/entities.py coalesce default 'live') preserves it."""
    import json

    sdk2 = TortoiseSDK(db_path=str(tmp_path / "t.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))
    a = sdk2.create_point("statement", "A")
    b = sdk2.create_point("statement", "B")
    op = sdk2.create_operator("IMPL", a["id"], [b["id"]], promote_source=False)
    log = json.loads(sdk2._get_event_log().load())[-1] if hasattr(
        sdk2._get_event_log(), "load"
    ) else None
    if log is None:
        lines = open(str(tmp_path / "events.jsonl")).read().splitlines()
        log = json.loads(lines[-1]) if lines else None
    assert log is not None, "OperatorAdded event must be written to the JSONL log"
    assert log.get("type") == "OperatorAdded", f"last event is {log.get('type')}"
    assert log.get("point", {}).get("id") == op["id"]
    assert log["point"].get("status") == "draft", (
        "OperatorAdded event must carry status:'draft' for replay parity"
    )


# ── Review-fix regressions (#943 code review) ─────────────────────────

def test_draft_seed_runs_nothing(sdk):
    """A draft claim used as a plain-point seed must run nothing: affected
    set empty, run() early-returns (0, True)."""
    s = sdk.create_point("statement", "s", status="live")
    d = sdk.create_point("statement", "draft seed")
    op = sdk.create_operator("IMPL", s["id"], [d["id"]])
    ep = sdk._get_ep()
    affected = ep._affected_claims([d["id"]], include_draft=False)
    assert affected == set(), (
        f"draft seed must contribute nothing, got affected={affected}"
    )
    iters, converged = ep.run([d["id"]])
    assert (iters, converged) == (0, True)
    # include_draft=True restores the seed's reachability.
    affected2 = ep._affected_claims([d["id"]], include_draft=True)
    assert s["id"] in affected2 or op["id"] in affected2


def test_live_seed_does_not_cross_draft_operator_bridge(sdk):
    """Live claim s --live op O1--> live l1, plus s --DRAFT op D--> live l2
    with a live operator O3 on l2 (l2's own chain): seeding s must NOT reach
    l2 through D, so neither l2 nor O3's factor enters the affected set."""
    s = sdk.create_point("statement", "s", status="live")
    l1 = sdk.create_point("statement", "l1", status="live")
    l2 = sdk.create_point("statement", "l2", status="live")
    c3 = sdk.create_point("statement", "c3", status="live")
    o1 = sdk.create_operator("IMPL", s["id"], [l1["id"]])
    d = sdk.create_operator("IMPL", s["id"], [l2["id"]], promote_source=False)
    o3 = sdk.create_operator("IMPL", l2["id"], [c3["id"]])
    ep = sdk._get_ep()
    affected = ep._affected_claims([s["id"]], include_draft=False)
    assert l1["id"] in affected
    assert l2["id"] not in affected, (
        "live claim reached only through a DRAFT operator bridge must be excluded"
    )
    assert c3["id"] not in affected
    factors = ep._affected_factors(affected, include_draft=False)
    factor_ops = {f[0] for f in factors}
    assert o1["id"] in factor_ops
    assert d["id"] not in factor_ops
    assert o3["id"] not in factor_ops
    # include_draft=True crosses the bridge (legacy semantics).
    affected2 = ep._affected_claims([s["id"]], include_draft=True)
    assert l2["id"] in affected2


def test_directional_factor_skipped_when_draft_source_stripped(sdk):
    """Non-bidirectional operator whose idx-0 SOURCE is draft: skipping beats
    renumbering a live target into the source slot (direction inversion)."""
    draft_src = sdk.create_point("statement", "draft source")
    t1 = sdk.create_point("statement", "t1", status="live")
    t2 = sdk.create_point("statement", "t2", status="live")
    # promote_source=False keeps the source draft; the operator node starts
    # draft too — simulate the #785 promote flow making the OPERATOR live
    # while its source stays draft (reachable via update_point/manual write).
    op = sdk.create_operator("IMPL", draft_src["id"], [t1["id"], t2["id"]],
                             direction="unidirectional", promote_source=False)
    sdk._get_proj().g.query(
        "MATCH (o:Point {id:$id}) SET o.status = 'live'",
        params={"id": op["id"]},
    )
    ep = sdk._get_ep()
    # Seed the live targets so the operator is discovered via Batch 1.
    affected = {t1["id"], t2["id"]}
    factors = ep._affected_factors(affected, include_draft=False)
    assert all(f[0] != op["id"] for f in factors), (
        "draft-source unidirectional factor must be skipped, not renumbered"
    )
    # include_draft=True keeps it (legacy).
    factors2 = ep._affected_factors({draft_src["id"], t1["id"], t2["id"]},
                                    include_draft=True)
    assert any(f[0] == op["id"] for f in factors2)


def test_unary_operator_factor_kept_when_not_draft_caused(sdk):
    """A genuinely unary operator (created with empty target_ids) keeps its
    factor when the <2-input state is NOT draft-caused — matches pre-PR
    behavior (_update_factor no-ops it) and keeps include_draft parity."""
    a = sdk.create_point("statement", "a", status="live")
    op = sdk.create_operator("IMPL", a["id"], [])
    ep = sdk._get_ep()
    factors = ep._affected_factors({a["id"]}, include_draft=False)
    assert any(f[0] == op["id"] for f in factors), (
        "unary (non-draft-caused) factor must be kept for legacy parity"
    )


def test_compute_confidence_live_invariant_with_draft_subgraph(sdk, tmp_path):
    """Write-back surface: compute_confidence must leave live confidences
    unchanged when a draft subgraph is present. Two identical live graphs,
    one with the leak operator (live op wired to a draft Point): l's
    confidence must be identical — the leak must not change it."""
    def build(with_leak: bool, db: str) -> tuple[dict, dict]:
        s = TortoiseSDK(db_path=db)
        ids = make_leak_graph(s)
        if not with_leak:
            # remove the leak operator's edges (keep the draft point)
            s._get_proj().g.query(
                "MATCH (o:Point {id:$oid})-[r]->() DELETE r",
                params={"oid": ids["o2"]},
            )
        return s, ids

    leak_sdk, leak_ids = build(True, str(tmp_path / "leak.db"))
    ctrl_sdk, ctrl_ids = build(False, str(tmp_path / "ctrl.db"))
    leak_res = leak_sdk.compute_confidence(factors=[leak_ids["o1"], leak_ids["o2"]])
    ctrl_res = ctrl_sdk.compute_confidence(factors=[ctrl_ids["o1"]])
    l_leak = leak_res["confidences"][leak_ids["l"]]["mean"]
    l_ctrl = ctrl_res["confidences"][ctrl_ids["l"]]["mean"]
    assert l_leak == pytest.approx(l_ctrl, abs=1e-9), (
        "the draft-connected leak operator must not change the live posterior "
        "through compute_confidence"
    )
