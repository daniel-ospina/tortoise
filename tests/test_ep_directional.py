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
        """3 shared conclusions: bidirectional cascade is larger."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(
            sdk, num_shared=3, direction="bidirectional")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4", direction="bidirectional")
        sdk.close()
        # 3 shared should drop MORE than 1 shared (~0.05-0.10)
        assert r["c2_drop"] > 0.04, \
            f"Dense C2 drop too small: {r['c2_drop']:.4f}"
        assert r["b_drop"] > 0.02, \
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
        """C2 with 1 T4 anchor: partial cascade protection."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        self.add_anchors(sdk, c2_id, 1, "T4")
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        # 1 T4 anchor should reduce but not eliminate cascade
        assert r["c2_drop"] < 0.08, \
            f"Low-anchor drop too large: {r['c2_drop']:.4f}"
        assert r["c2_drop"] > 0.01, \
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
        """C1 loses A's support regardless of mode."""
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
        assert r_bi["c1_drop"] > 0.05, "C1 should drop in bidirectional"
        assert r_dir["c1_drop"] > 0.05, "C1 should drop in directed"

    # ── B feedback measurement ─────────────────────────────

    def test_b_feedback_bidirectional(self):
        """B receives feedback from C1 in bidirectional mode."""
        sdk = fresh_sdk()
        a_id, b_id, c2_id, shared_ids = self.build_shared_conclusion_graph(sdk)
        r = self.measure_drop(sdk, a_id, b_id, c2_id, shared_ids,
                              "T4")
        sdk.close()
        assert r["b_drop"] > 0.01, \
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
