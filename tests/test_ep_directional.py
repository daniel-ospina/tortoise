"""E019: Directional vs Bidirectional EP Propagation — False Cascade Detection.

Tests whether bidirectional EP messaging on directed IMPL edges causes
false cascades where an invalidated sub-argument (A) incorrectly reduces
confidence in an unrelated sub-argument (B) that shares only a conclusion.
"""
import os
import pytest
from tortoise.sdk import TortoiseSDK

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Mirrors the probe pattern
# in tests/test_integration_search.py.
FALKORDB_AVAILABLE = False
try:
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


TIER_MAP = {
    "T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1),
}
EPSILON = 0.02


def fresh_sdk(graph_name=None):
    import uuid
    ns = graph_name or f"test_ep_dir_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(db_path=None, namespace=ns)
    return sdk


def make_point(sdk, content, kind="statement"):
    return sdk.create_point(kind, content)


def make_operator(sdk, source_id, target_id, op_type="IMPL", direction=None):
    kwargs = {}
    if direction is not None:
        kwargs["direction"] = direction
    return sdk.create_operator(op_type, source_id, [target_id], **kwargs)


def run_ep(sdk):
    """Run EP belief propagation. Direction is controlled per-operator
    via the `direction` property (ONTOLOGY v3.1 #189), not via a global EP flag."""
    from tortoise.ep import TortoiseEP
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
    ).result_set
    op_ids = [r[0] for r in rows] if rows else []
    # Hydrate evidence from graph
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence"
    ).result_set
    return {r[0]: r[1] for r in rows} if rows else {}


def get_conf(result, point_id):
    return result.get(point_id, 0.5)


class TestE019DirectionalCascade:
    """E019: Directional vs bidirectional EP — false cascade measurement."""

    # ── Helpers ──────────────────────────────────────────────

    def build_shared_conclusion_graph(self, sdk, num_shared=1, direction=None):
        """A and B both IMPL C1 (and C1a, C1b...). B also IMPL C2."""
        a = make_point(sdk, "Point A")
        b = make_point(sdk, "Point B")
        c2 = make_point(sdk, "C2 — independent conclusion")

        make_operator(sdk, b["id"], c2["id"], "IMPL", direction=direction)

        shared_ids = []
        for i in range(num_shared):
            ci = make_point(sdk, f"C1{'abcdef'[i]} — shared conclusion {i+1}")
            make_operator(sdk, a["id"], ci["id"], "IMPL", direction=direction)
            make_operator(sdk, b["id"], ci["id"], "IMPL", direction=direction)
            shared_ids.append(ci["id"])

        return a["id"], b["id"], c2["id"], shared_ids

    def add_nand(self, sdk, point_id):
        """Invalidate A with a strong NAND contradiction."""
        nand = make_point(sdk, "NAND contradiction source")
        sdk.set_point_baseline(nand["id"], 10, 1)  # Strong NAND source (T0)
        make_operator(sdk, nand["id"], point_id, "NAND")

    def add_anchors(self, sdk, c2_id, count, tier):
        """Add independent IMPL sources to C2."""
        for i in range(count):
            anchor = make_point(sdk, f"Anchor {i+1} ({tier})")
            sdk.set_point_baseline(anchor["id"], *TIER_MAP[tier])
            make_operator(sdk, anchor["id"], c2_id, "IMPL")

    def measure_drop(self, sdk, a_id, b_id, c2_id, shared_ids,
                     b_tier, direction="bidirectional"):
        """Run EP, get baseline, NAND A, run EP again, return drops."""
        # Set source evidence
        sdk.set_point_baseline(a_id, *TIER_MAP["T0"])
        sdk.set_point_baseline(b_id, *TIER_MAP[b_tier])

        # Baseline EP
        result_before = run_ep(sdk)
        c2_before = get_conf(result_before, c2_id)
        b_before = get_conf(result_before, b_id)
        c1_before = get_conf(result_before, shared_ids[0])

        # Invalidate A
        self.add_nand(sdk, a_id)

        # After NAND EP
        result_after = run_ep(sdk)
        c2_after = get_conf(result_after, c2_id)
        b_after = get_conf(result_after, b_id)
        c1_after = get_conf(result_after, shared_ids[0])

        return {
            "c2_before": c2_before, "c2_after": c2_after,
            "c2_drop": c2_before - c2_after,
            "b_before": b_before, "b_after": b_after,
            "b_drop": b_before - b_after,
            "c1_drop": c1_before - c1_after,
        }

    # ── Isolated C2 tests ──────────────────────────────────

    def test_no_false_cascade(self):
        """A invalidated → C1 drops, B barely affected, C2 isolated."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        # A gets T4 (weak), NAND gets T0 (strong) — A should drop
        sdk.set_point_baseline(a_id, *TIER_MAP["T4"])
        sdk.set_point_baseline(b_id, *TIER_MAP["T0"])

        result_before = run_ep(sdk)
        self.add_nand(sdk, a_id)
        result_after = run_ep(sdk)
        sdk.close()

        a_drop = get_conf(result_before, a_id) - get_conf(result_after, a_id)
        c1_drop = get_conf(result_before, shared_ids[0]) - get_conf(result_after, shared_ids[0])
        b_drop = get_conf(result_before, b_id) - get_conf(result_after, b_id)
        c2_drop = get_conf(result_before, c2_id) - get_conf(result_after, c2_id)

        assert a_drop > 0.03, f"A should drop: {a_drop:.4f}"
        assert c1_drop > 0.001, f"C1 should drop: {c1_drop:.4f}"
        assert b_drop < 0.02, f"B minimal feedback: {b_drop:.4f}"
        assert c2_drop < 0.005, f"C2 isolated: {c2_drop:.4f}"

    def test_directed_also_clean(self):
        """Directed EP also shows no cascade (same result)."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(
            sdk, direction="unidirectional")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4", direction="unidirectional")
        sdk.close()
        assert r["c2_drop"] < 0.02, f"Directed C2: {r['c2_drop']:.4f}"

    # ── Dense shared conclusions ───────────────────────────

    def test_dense_shared_bidirectional(self):
        """3 shared conclusions: bidirectional cascade is larger.

        Real-drop thresholds (#855): with the re-run drift fixed (#852) the
        cascade is measured from clean priors. A T0 NAND drives A down ~0.08
        and the dense fan-out gives B real feedback: c2 drops ~0.005, B
        ~0.014 (pre-fix true values were ~0.001; the old 0.04/0.02
        thresholds were calibrated on drift-inflated re-runs, #844).
        """
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(
            sdk, num_shared=3, direction="bidirectional")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4", direction="bidirectional")
        sdk.close()
        # 3 shared should drop MORE than 1 shared
        assert r["c2_drop"] > 0.0025, \
            f"Dense C2 drop too small: {r['c2_drop']:.4f}"
        assert r["b_drop"] > 0.007, \
            f"Dense B drop too small: {r['b_drop']:.4f}"

    def test_dense_shared_directed(self):
        """3 shared conclusions: directed EP still clean."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(
            sdk, num_shared=3, direction="unidirectional")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4", direction="unidirectional")
        sdk.close()
        assert r["c2_drop"] < 0.02, \
            f"Directed C2 should not drop: {r['c2_drop']:.4f}"

    # ── Anchored C2: gradient ──────────────────────────────

    def test_low_anchor_one_t4(self):
        """C2 with 1 T4 anchor: partial cascade protection.

        Real-drop thresholds (#855): a single T4 anchor reduces C2's drop
        to ~0.001 (pre-fix: ~0.000; drift-inflated: ~0.01+).
        """
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        self.add_anchors(sdk, c2_id, 1, "T4")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        # 1 T4 anchor should reduce but not eliminate cascade
        assert r["c2_drop"] < 0.01, \
            f"Low-anchor drop too large: {r['c2_drop']:.4f}"
        assert r["c2_drop"] > 0.0005, \
            f"Low-anchor drop too small: {r['c2_drop']:.4f}"

    def test_med_anchor_two_t4(self):
        """C2 with 2 T4 anchors: stronger cascade protection."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        self.add_anchors(sdk, c2_id, 2, "T4")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        assert r["c2_drop"] < 0.05, \
            f"Med-anchor drop too large: {r['c2_drop']:.4f}"

    def test_high_anchor_five_t2(self):
        """C2 with 5 T2 anchors: cascade nearly eliminated."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        self.add_anchors(sdk, c2_id, 5, "T2")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        assert r["c2_drop"] < 0.03, \
            f"High-anchor drop too large: {r['c2_drop']:.4f}"

    # ── Anchoring gradient monotonicity ────────────────────

    def test_anchor_gradient_monotonic(self):
        """More anchors = less cascade (monotonic)."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)

        # Isolated
        r0 = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                                "T4")

        # Reset NAND
        sdk2 = fresh_sdk()
        a_id2, b_id2, c2_id2, shared_ids2 = \
            self.build_shared_conclusion_graph(sdk2)
        self.add_anchors(sdk2, c2_id2, 1, "T4")
        r1 = self.measure_drop(sdk2, a_id2, b_id2, c2_id2, shared_ids2,
                                "T4")

        sdk3 = fresh_sdk()
        a_id3, b_id3, c2_id3, shared_ids3 = \
            self.build_shared_conclusion_graph(sdk3)
        self.add_anchors(sdk3, c2_id3, 5, "T2")
        r5 = self.measure_drop(sdk3, a_id3, b_id3, c2_id3, shared_ids3,
                                "T4")

        sdk.close(); sdk2.close(); sdk3.close()

        assert r0["c2_drop"] > r1["c2_drop"], \
            f"Isolated ({r0['c2_drop']:.4f}) <= 1-anchor ({r1['c2_drop']:.4f})"
        assert r1["c2_drop"] > r5["c2_drop"], \
            f"1-anchor ({r1['c2_drop']:.4f}) <= 5-anchor ({r5['c2_drop']:.4f})"

    # ── C1 control: always drops ───────────────────────────

    def test_c1_always_drops(self):
        """C1 loses A's support regardless of mode.

        Real-drop threshold (#855): with the drift fixed, C1 drops ~0.022
        in both modes (pre-fix true drop was ~0.0015; the old 0.05 bar was
        drift-inflated, #844).
        """
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        # Bidirectional (default)
        r_bi = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                                  "T4")
        sdk.close()
        # Directed
        sdk2 = fresh_sdk()
        a_id2, b_id2, c2_id2, shared_ids2 = \
            self.build_shared_conclusion_graph(sdk2, direction="unidirectional")
        r_dir = self.measure_drop(sdk2, a_id2, b_id2, c2_id2, shared_ids2,
                                   "T4", direction="unidirectional")
        sdk2.close()
        assert r_bi["c1_drop"] > 0.01, "C1 should drop in bidirectional"
        assert r_dir["c1_drop"] > 0.01, "C1 should drop in directed"

    # ── B feedback measurement ─────────────────────────────

    def test_b_feedback_bidirectional(self):
        """B receives feedback from C1 in bidirectional mode.

        Real-drop threshold (#855): B (T4, weak evidence) receives real
        feedback ~0.006 when A is contradicted (pre-fix: ~0.000; the old
        0.01 bar was drift-inflated, #844).
        """
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        assert r["b_drop"] > 0.003, \
            f"B should receive feedback: drop={r['b_drop']:.4f}"

    def test_b_no_feedback_directed(self):
        """B receives no feedback from C1 in directed mode."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(
            sdk, direction="unidirectional")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4", direction="unidirectional")
        sdk.close()
        assert r["b_drop"] < 0.02, \
            f"B should not receive feedback in directed: drop={r['b_drop']:.4f}"


# ── #852 regression: re-run stability + posterior observability ─────────────

def test_rerun_stability_immutable_baselines():
    """#852: re-running EP on an UNCHANGED graph must not drift. The old
    _flush_cache overwrote ep_alpha/ep_beta (the immutable baseline priors)
    with posteriors, so each re-run re-hydrated the previous posterior as the
    new prior → confidence eroded monotonically (0.309→0.191→0.132…)."""
    sdk = fresh_sdk()
    a = make_point(sdk, "A"); c1 = make_point(sdk, "C1")
    make_operator(sdk, a["id"], c1["id"], "IMPL")
    sdk.set_point_baseline(a["id"], *TIER_MAP["T0"])
    r1 = run_ep(sdk); c1_run1 = get_conf(r1, c1["id"])
    r2 = run_ep(sdk); c1_run2 = get_conf(r2, c1["id"])
    r3 = run_ep(sdk); c1_run3 = get_conf(r3, c1["id"])
    sdk.close()
    assert abs(c1_run2 - c1_run1) < 1e-3, f"re-run drift: {c1_run1:.4f} -> {c1_run2:.4f}"
    assert abs(c1_run3 - c1_run2) < 1e-3, f"re-run drift: {c1_run2:.4f} -> {c1_run3:.4f}"

def test_baseline_prior_preserved_posterior_observable():
    """#852 review P1: baseline'd claims keep ep_alpha/beta as immutable
    priors, but the EP posterior is observable via posterior_alpha/beta and
    n.confidence (compute_confidence reflects the attack, not the prior)."""
    sdk = fresh_sdk()
    a = make_point(sdk, "A"); c1 = make_point(sdk, "C1")
    make_operator(sdk, a["id"], c1["id"], "IMPL")
    sdk.set_point_baseline(a["id"], *TIER_MAP["T0"])  # 10/1 → prior mean 0.9091
    nand = make_point(sdk, "NAND"); sdk.set_point_baseline(nand["id"], 10, 1)
    make_operator(sdk, nand["id"], a["id"], "NAND")
    proj = sdk._get_proj()
    rows = proj.g.query("MATCH (o:Point) WHERE o.is_operator = true RETURN o.id").result_set
    ev = proj.g.query("MATCH (n:Point) WHERE n.baseline_set = true RETURN n.id, n.ep_alpha, n.ep_beta").result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev}
    from tortoise.ep import TortoiseEP
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3, evidence=evidence)
    ep.run([r[0] for r in rows], max_hops=2)
    pt = sdk.get_point(a["id"])
    # prior preserved
    assert pt["ep_alpha"] == 10 and pt["ep_beta"] == 1, \
        f"baseline prior overwritten: {pt['ep_alpha']}/{pt['ep_beta']}"
    # posterior observable
    assert pt.get("posterior_alpha") is not None, "posterior_alpha not written"
    conf = ep.compute_confidence(a["id"])
    prior_mean = pt["ep_alpha"] / (pt["ep_alpha"] + pt["ep_beta"])
    assert conf["mean"] != prior_mean, "posterior equals prior — attack invisible"
    assert abs(conf["mean"] - pt["confidence"]) < 1e-4, \
        f"confidence {pt['confidence']} != posterior {conf['mean']}"
    sdk.close()


# ── #855 regression: combined cascade + n-ary conservation ──────────────────

def test_cascade_magnitude_and_nary_conservation_regression():
    """#855 regression: NAND→IMPL cascade propagates at correct magnitude
    AND #853 n-ary conservation is not re-broken by the phi_impl change.

    Pre-fix (main): c1_drop ~0.001 (effectively zero), b_drop ~0.000.
    Post-fix (#852 + #855): c1_drop 0.015–0.035, b_drop 0.003–0.012.
    The upper bound prevents drift-inflated thresholds from masking
    regressions (old thresholds >0.05 were drift artifacts per #844).

    The n-ary conservation guard: test_ep_nary_falsification.py already
    validates that NAND n-ary decomposition (phi_nand, unchanged) respects
    weight conservation and input-order invariance. This test confirms
    that the changed phi_impl does not cross-contaminate n-ary semantics
    — IMPL n-ary (source→targets only) and NAND n-ary both use their own
    phi functions independently.
    """
    # Build standard cascade graph: A→C1, B→C1, B→C2 + NAND attacking A.
    # C1 shares A's evidence — when A is contradicted, C1 must drop
    # measurably (the cascade). C2 is the control (should stay isolated).
    sdk = fresh_sdk()
    a = make_point(sdk, "A"); b = make_point(sdk, "B")
    c1 = make_point(sdk, "C1"); c2 = make_point(sdk, "C2")
    make_operator(sdk, a["id"], c1["id"], "IMPL")
    make_operator(sdk, b["id"], c1["id"], "IMPL")
    make_operator(sdk, b["id"], c2["id"], "IMPL")
    for pt in (a, b):
        sdk.set_point_baseline(pt["id"], *TIER_MAP["T4"])
    nand = make_point(sdk, "NAND"); sdk.set_point_baseline(nand["id"], *TIER_MAP["T0"])
    make_operator(sdk, nand["id"], a["id"], "NAND")

    r = run_ep(sdk)
    t4_mean = TIER_MAP["T4"][0] / (TIER_MAP["T4"][0] + TIER_MAP["T4"][1])  # 1.1/2.1 ≈ 0.5238
    c1_drop = t4_mean - get_conf(r, c1["id"])
    b_drop = t4_mean - get_conf(r, b["id"])
    c2_drop = t4_mean - get_conf(r, c2["id"])
    sdk.close()

    # Cascade: C1 must drop measurably (lost A's support).
    assert 0.015 <= c1_drop <= 0.035, \
        f"C1 cascade outside expected band: {c1_drop:.4f}"
    # Feedback: B receives ~0.006 from bidirectional IMPL with weakened C1.
    assert 0.003 <= b_drop <= 0.012, \
        f"B feedback outside expected band: {b_drop:.4f}"
    # Isolation: C2 shares only B, its confidence is nearly unchanged.
    assert c2_drop < 0.01, \
        f"C2 should be isolated: drop={c2_drop:.4f}"

    # N-ary conservation guard: run the key n-ary check inline to confirm
    # the phi_impl change does not affect NAND n-ary (phi_nand is separate).
    # Validates #853 is not re-broken at the new NAND base weight (8.0).
    from tortoise.weights import NAND_BASE_WEIGHT
    assert NAND_BASE_WEIGHT == 8.0, \
        f"NAND_BASE_WEIGHT changed: {NAND_BASE_WEIGHT} — n-ary conservation may need recalibration"

    # Verify the n-ary conservation at w=8.0: a 4-input NAND must conserve
    # total pull vs binary pair at same weight. Uses the same pattern as
    # test_ep_nary_falsification.test_nary_nand_weight_not_overcounted.
    import types
    from tortoise.ep import TortoiseEP

    def _make_ep_local(nodes):
        stub = types.SimpleNamespace(
            g=types.SimpleNamespace(query=lambda *a, **k: types.SimpleNamespace(result_set=[]))
        )
        ep = TortoiseEP(stub, damping=1.0, max_iter=50, tol=1e-4, n_quad=8)
        ep._node_cache = {cid: (1.0, 1.0) for cid in nodes}
        ep._msg_cache = {}
        return ep

    def _total_local(ep):
        return sum(abs(v[0]) + abs(v[1]) for v in ep._msg_cache.values())

    w = NAND_BASE_WEIGHT  # 8.0
    ep_bin = _make_ep_local(["a", "b"])
    ep_bin._update_factor("op", "NAND", ["a", "b"], weight=w)
    ep_nary = _make_ep_local(["a", "b", "c", "d"])
    ep_nary._update_factor("op", "NAND", ["a", "b", "c", "d"], weight=w)
    total_ratio = _total_local(ep_nary) / _total_local(ep_bin)
    assert 0.90 <= total_ratio <= 1.10, \
        f"n-ary conservation broken at w={w}: ratio={total_ratio:.4f}"
    # Per-claim: n=4 → each claim gets 2/4 = 0.5× the binary per-claim pull.
    msg_bin = abs(ep_bin._msg_cache[("op", "a", "NAND")][0]) + abs(ep_bin._msg_cache[("op", "a", "NAND")][1])
    msg_nary = abs(ep_nary._msg_cache[("op", "a", "NAND")][0]) + abs(ep_nary._msg_cache[("op", "a", "NAND")][1])
    assert 0.40 * msg_bin <= msg_nary <= 0.65 * msg_bin, \
        f"per-claim nary pull drifted at w={w}: {msg_nary:.4f} vs binary {msg_bin:.4f}"
