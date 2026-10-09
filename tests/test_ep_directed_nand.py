"""Directed NAND operator tests (#753 — P0 fix: NAND attack semantics).

NAND direction semantics (#753; canonical default revised by #7813):
  - DEFAULT (direction ABSENT) is PER op_type, not one global constant
    (CYCLE-25, ontology v3.6 §5.2.7): IMPL → "bidirectional", NAND →
    "unidirectional" (the extraction default — a new claim attacks the
    existing one). Before #7813 the parameter defaulted to the STRING
    "bidirectional", which shadowed that canonicalization entirely, so every
    omitted-direction NAND was silently MUTUAL. See sdk._canonical_direction.
  - A caller may explicitly declare either direction. direction="bidirectional"
    makes a NAND mutual ("A and B can't both be true", logically symmetric);
    direction="unidirectional" is a DIRECTED attack — the attacker's truth
    penalizes the target, and the back-message guard in ep.py ensures the
    attacker receives NO factor message (Dung-style attack).
  - N-ary directed NAND decomposes as source→each-target (no arbitrary
    target↔target directed attacks).
The symmetric phi_nand potential was measured to behave as an "agreement
coupling" in some configurations (a strong attacker could RAISE a target);
directed attacks opt out of that coupling.
"""
import pytest  # noqa: I001

from tortoise.sdk import TortoiseSDK
from tortoise.ep import TortoiseEP


def make_point(sdk, content, kind="statement"):
    return sdk.create_point(kind, content, status="live")  # #780 default excludes drafts


def make_operator(sdk, source_id, target_id, op_type="IMPL", direction=None):
    kwargs = {}
    if direction is not None:
        kwargs["direction"] = direction
    return sdk.create_operator(op_type, source_id, [target_id], **kwargs)


def set_evidence(sdk, pid, alpha, beta):
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, n.baseline_set=true",
        params={"id": pid, "al": alpha, "be": beta},
    )


def run_ep(sdk):
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    op_ids = [r[0] for r in rows] if rows else []
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3, evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def test_operator_direction_defaults_are_per_op_type(sdk):
    """#7813 REGRESSION — the per-op_type default must not be shadowed.

    The defect: ``create_operator``'s ``direction`` parameter defaulted to the
    STRING "bidirectional". Because a string default is never None, the
    ``if direction is None: direction = self._canonical_direction(...)`` branch
    was UNREACHABLE on the default path, so every caller that omitted
    ``direction`` got a MUTUAL NAND even though ontology v3.6 §5.2.7 says
    NAND → "unidirectional". A bidirectional NAND damages BOTH endpoints (a
    unidirectional one spares the attacker), so the bug silently inverted the
    meaning of "X attacks Y" and made the best-measured option rank last on the
    storage-architecture decision.

    This asserts all three legs of the contract in one place so the shadowing
    cannot silently return: the op_type default for NAND, the op_type default
    for IMPL, and that an explicit value still overrides.
    """
    a = make_point(sdk, "a")
    b = make_point(sdk, "b")
    c = make_point(sdk, "c")

    # Leg 1: NAND with direction ABSENT -> unidirectional (was bidirectional).
    nand = sdk.create_operator("NAND", a["id"], [b["id"]])
    # Leg 2: IMPL with direction ABSENT -> bidirectional (unchanged).
    impl = sdk.create_operator("IMPL", a["id"], [b["id"]])
    # Leg 3: an explicit direction always overrides the op_type default.
    nand_mutual = sdk.create_operator(
        "NAND", a["id"], [c["id"]], direction="bidirectional")

    proj = sdk._get_proj()
    def _direction(op):
        return proj.g.query(
            "MATCH (o:Point {id:$id}) RETURN o.direction", params={"id": op["id"]}
        ).result_set[0][0]

    assert _direction(nand) == "unidirectional", (
        "NAND with direction absent must canonicalize to unidirectional "
        "(ontology v3.6 §5.2.7) — a non-None parameter default shadows "
        "create_operator's _canonical_direction call (#7813)"
    )
    assert _direction(impl) == "bidirectional", (
        "IMPL with direction absent must stay bidirectional (#7813)"
    )
    assert _direction(nand_mutual) == "bidirectional", (
        "an explicit direction must override the op_type default (#7813)"
    )


def test_directed_attack_lowers_target(sdk):
    """A confident attacker lowers the target vs an identical control target."""
    a = make_point(sdk, "attacker")
    b1 = make_point(sdk, "target attacked")
    b2 = make_point(sdk, "target control")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 12.0, 1.0)   # strong T0-class attacker
    set_evidence(sdk, b1["id"], 5.0, 1.0)   # moderate targets
    set_evidence(sdk, b2["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")   # activate subgraph
    make_operator(sdk, s["id"], b1["id"], "IMPL")  # matched twin support (review P2)
    make_operator(sdk, s["id"], b2["id"], "IMPL")  # matched twin support
    make_operator(sdk, a["id"], b1["id"], "NAND", direction="unidirectional")  # directed attack on b1 only

    res = run_ep(sdk)
    attacked, control = res[b1["id"]], res[b2["id"]]
    assert attacked < control - 0.02, (
        f"directed attack must lower target: attacked={attacked:.3f} control={control:.3f}"
    )


def test_no_direct_back_pressure_on_attacker(sdk):
    """Directed attack must not send a DIRECT factor message back to the
    attacker (no back-pressure through the NAND factor).

    Note: indirect coupling through shared support (the #86 bidirectional-IMPL
    path) can still move the attacker in other topologies; this test asserts
    the direct factor-level immunity, not global invariance."""
    a = make_point(sdk, "attacker")
    b = make_point(sdk, "target")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 10.0, 1.0)
    set_evidence(sdk, b["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")

    res_before = run_ep(sdk)
    ca_before = res_before[a["id"]]

    make_operator(sdk, a["id"], b["id"], "NAND", direction="unidirectional")  # directed attack
    res_after = run_ep(sdk)
    ca_after = res_after[a["id"]]

    assert abs(ca_after - ca_before) < 0.01, (
        f"attacker must be immune to its own directed attack: "
        f"{ca_before:.3f} -> {ca_after:.3f}"
    )


def test_bidirectional_nand_is_mutual(sdk):
    """Explicit bidirectional NAND keeps mutual-contradiction semantics: the
    attacker receives BACK-PRESSURE (moves), unlike directed NAND where the
    attacker is immune. Distinguishing property between the two modes
    (measured with balanced evidence: directed attacker stays put,
    mutual attacker drops)."""
    a = make_point(sdk, "a")
    b = make_point(sdk, "b")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 5.0, 1.0)  # balanced — no dominant evidence
    set_evidence(sdk, b["id"], 5.0, 1.0)
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")

    res_before = run_ep(sdk)
    ca_b = res_before[a["id"]]

    make_operator(sdk, a["id"], b["id"], "NAND", direction="bidirectional")
    res_after = run_ep(sdk)
    ca_a = res_after[a["id"]]

    # Mutual mode: the source receives back-pressure (moves). Directed mode
    # (default) leaves the attacker untouched — proven by
    # test_no_back_pressure_on_attacker. NOTE: mutual coupling is weak in this
    # engine (measured +0.0024 — the documented "contradictions invisible"
    # weakness from the eval spec, #753); the threshold proves directionality
    # exists without overclaiming mutual strength.
    assert abs(ca_a - ca_b) > 0.001, (
        f"bidirectional NAND must couple back onto the source: "
        f"attacker {ca_b:.3f} -> {ca_a:.3f}"
    )


def test_reinstatement(sdk):
    """Dung reinstatement: C attacks B, B attacks A → A recovers vs no reinstatement.

    A(T2) is supported by strong evidence; B attacks A; C attacks B. With
    reinstatement, C's attack on B weakens B's attack on A, so A's confidence
    recovers relative to the no-C case.
    """
    a = make_point(sdk, "A (claimed)")
    b = make_point(sdk, "B (attacks A)")
    c = make_point(sdk, "C (attacks B)")
    s = make_point(sdk, "support")
    set_evidence(sdk, a["id"], 8.0, 1.0)
    set_evidence(sdk, b["id"], 6.0, 1.0)
    set_evidence(sdk, c["id"], 10.0, 1.0)   # C is a strong attacker
    set_evidence(sdk, s["id"], 8.0, 1.0)
    make_operator(sdk, s["id"], a["id"], "IMPL")
    make_operator(sdk, s["id"], b["id"], "IMPL")
    make_operator(sdk, s["id"], c["id"], "IMPL")

    # Case 1: A attacked by B only (directed attacks)
    make_operator(sdk, b["id"], a["id"], "NAND", direction="unidirectional")
    res1 = run_ep(sdk)
    a_without_reinst = res1[a["id"]]

    # Case 2: add C attacking B (reinstatement chain)
    make_operator(sdk, c["id"], b["id"], "NAND", direction="unidirectional")
    res2 = run_ep(sdk)
    a_with_reinst = res2[a["id"]]

    # Reinstatement: A should be at least as strong with C attacking B
    assert a_with_reinst >= a_without_reinst - 0.01, (
        f"reinstatement failed: A without C={a_without_reinst:.3f}, "
        f"A with C attacking B={a_with_reinst:.3f}"
    )


def test_mcp_tool_honors_direction(sdk, tmp_path, monkeypatch):
    """#7813 — the MCP tool must NOT re-shadow the SDK's per-op_type default.

    ``tortoise_create_operator`` previously defaulted its ``direction`` to the
    STRING "bidirectional" and passed it through explicitly, so a non-None
    value reached ``create_operator`` and the CYCLE-25 canonicalization never
    ran. Fixing ``sdk.create_operator`` alone would therefore have left every
    agent-created NAND mutual: the MCP surface is the only path most agents
    use, so this leg is what makes the SDK fix observable in practice.
    """
    import os
    _prev_db = os.environ.get("TORTOISE_DB_PATH")
    # Epic #1647 (PR #1684 CI-fix): the tool SDK (_get_sdk) reads
    # TORTOISE_DB_URI on the docker lane → connects to the URI-default graph,
    # while the test's sdk fixture (path=) redirects to a DERIVED graph → the
    # tool cannot see the seeded points. This test's contract is MCP-tool +
    # fixture-SDK SHARING one store — pop the URI so both resolve through
    # TORTOISE_DB_PATH (embedded store) on BOTH lanes.
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    os.environ["TORTOISE_DB_PATH"] = str(tmp_path / "t.db")  # align tool SDK with fixture
    from tortoise.mcp_auth import _current_org_id, _transport_mode
    tok_mode = _transport_mode.set("stdio")
    tok_team = _current_org_id.set(None)
    try:
        from tortoise.mcp_server import tortoise_create_operator
        a = make_point(sdk, "a")
        b = make_point(sdk, "b")
        # route through the MCP tool handler with NO direction (default None)
        res_default = tortoise_create_operator("NAND", a["id"], [b["id"]])
        res_directed = tortoise_create_operator(
            "NAND", a["id"], [b["id"]], direction="unidirectional")
        proj = sdk._get_proj()
        d_def = proj.g.query(
            "MATCH (o:Point {id:$id}) RETURN o.direction",
            params={"id": res_default["id"]}).result_set[0][0]
        d_dir = proj.g.query(
            "MATCH (o:Point {id:$id}) RETURN o.direction",
            params={"id": res_directed["id"]}).result_set[0][0]
        assert d_def == "unidirectional", (
            "MCP tool with direction absent must canonicalize to unidirectional "
            "for NAND — a non-None tool default shadows the SDK's "
            "_canonical_direction call (#7813)"
        )
        assert d_dir == "unidirectional", "explicit directed must be honored"
    finally:
        _transport_mode.reset(tok_mode)
        _current_org_id.reset(tok_team)
        import tortoise.mcp_server as _mcp
        _mcp._sdk = None  # clear module-level SDK cache (bound to this test's DB)
        if _prev_db is None:
            os.environ.pop("TORTOISE_DB_PATH", None)
        else:
            os.environ["TORTOISE_DB_PATH"] = _prev_db


def test_directed_nary_nand_source_to_targets_only(sdk):
    """Directed N-ary NAND must emit source→each-target attacks ONLY — no
    arbitrary target↔target directed attacks (review P2 fix)."""
    src = make_point(sdk, "src")
    t1 = make_point(sdk, "t1")
    t2 = make_point(sdk, "t2")
    s = make_point(sdk, "support")
    for pid in (src["id"], t1["id"], t2["id"], s["id"]):
        set_evidence(sdk, pid, 5.0, 1.0)
    make_operator(sdk, s["id"], src["id"], "IMPL")
    make_operator(sdk, s["id"], t1["id"], "IMPL")
    make_operator(sdk, s["id"], t2["id"], "IMPL")
    # directed n-ary NAND: src attacks both targets
    sdk.create_operator("NAND", src["id"], [t1["id"], t2["id"]],
                        direction="unidirectional")

    from tortoise.ep import TortoiseEP
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    op_ids = [r[0] for r in rows] if rows else []
    ev = {r[0]: (r[1], r[2]) for r in (proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set=true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id,n.ep_alpha,n.ep_beta").result_set or [])}
    TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
               evidence=ev).run(op_ids, max_hops=2)

    # t1 must NOT receive an attack message from t2 (and vice versa) — only
    # from src. Check no message exists on a t1→t2 NAND edge.
    msgs = proj.g.query(
        "MATCH (:Point {id:$t1})-[r:NAND]->(:Point {id:$t2}) "
        "RETURN count(r)", params={"t1": t1["id"], "t2": t2["id"]}
    ).result_set
    assert msgs[0][0] == 0, "directed n-ary NAND must not create target↔target attacks"
