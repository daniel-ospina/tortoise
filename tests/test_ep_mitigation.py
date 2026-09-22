"""#2315 — mitigation strength is a live graded dampener in deterministic EP.

Product decision (2026-09-07, on #2315): a mitigation Point on an operator
``(op)-[:mitigated_by]->(m)`` reduces the OPERATOR's effective EP weight by
``w_eff = w * (1 - strength)`` (single source of the convention:
tortoise/weights.py module docstring) — a graded dampener, NOT a refutation.

Previously the strength value was stored but never read: compute_operator_weight
derived w without it, so mitigating an operator had zero belief effect (the
ep_e2e_patterns "GAP" scenario). These tests pin the WIRED behavior on the
deterministic EP path (live FalkorDB, isolated namespace per test — mirrors
tests/test_ep_directional.py): strong mitigation measurably lowers downstream
confidence vs unmitigated; weak lowers less than strong (monotone); a
mitigated NAND stays a contradiction (dampened, never refuted); and the
schema rule (mitigated_by only from an is_operator:true Point) is enforced by
the single writer gate.

Numeric regime (measured on this suite's EP config: damping=0.5, n_quad=12,
max_iter=50, tol=1e-3): an IMPL from a T0 source onto an unevidenced claim
settles the claim ~0.65 unmitigated, ~0.589 at strength 0.50 (Δ≈0.064),
~0.642 at 0.10 (Δ≈0.011); a T0 NAND on a Beta(4,1) target pushes it to
~0.518, and to only ~0.675 at strength 0.50.
"""

import os  # noqa: I001
import pytest
from tortoise.sdk import TortoiseSDK

_DB_URI = "docker://:falkordb@localhost:6379/tortoise_test_ep_mitigation"
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    from tortoise.sdk import TortoiseSDK as _ProbeSDK

    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available"
)


@pytest.fixture(autouse=True)
def _set_docker_uri(monkeypatch):
    """Point SDK constructions at the isolated docker test graph per-test."""
    monkeypatch.setenv("TORTOISE_DB_URI", _DB_URI)


TIER_MAP = {"T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1)}
# Effect thresholds — margins over the suite EPSILON 0.02 convention where
# the measured deltas are 2-7x larger (see module docstring).
STRONG_DELTA_MIN = 0.03  # strong (0.50) vs unmitigated (measured ~0.064)
SPREAD_MIN = 0.04  # conf(0.10) - conf(0.50) monotone span (~0.053)
NAND_WEAKEN_MIN = 0.05  # unmitigated-NAND drop vs 0.50-mitigated (~0.157)


def fresh_sdk(graph_name=None):
    import uuid

    ns = graph_name or f"test_ep_mit_{uuid.uuid4().hex[:8]}"
    return TortoiseSDK(db_path=None, namespace=ns)


def make_point(sdk, content, kind="statement", tier=None):
    p = sdk.create_point(kind, content, status="live")
    if tier is not None:
        sdk.set_point_baseline(p["id"], *tier)
    return p


def make_operator(sdk, source_id, target_id, op_type="IMPL"):
    return sdk.create_operator(op_type, source_id, [target_id])


def run_ep(sdk):
    """Run EP to convergence over every operator; return {id: confidence}."""
    from tortoise.ep import TortoiseEP

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


def get_conf(result, point_id):
    return result.get(point_id, 0.5)


class TestMitigationDampensImply:
    """(a)+(b): mitigation is a live, monotone dampener of IMPL weight."""

    def build_imply_chain(self, sdk):
        """T0 source S -[IMPL]-> claim C (unevidenced downstream claim)."""
        src = make_point(sdk, "T0 source supports C", tier=TIER_MAP["T0"])
        claim = make_point(sdk, "Downstream claim C")
        op = make_operator(sdk, src["id"], claim["id"], "IMPL")
        return op["id"], claim["id"]

    def test_strong_mitigation_lowers_downstream_confidence(self):
        """(a) strength 0.50 vs unmitigated: direction + delta threshold."""
        sdk = fresh_sdk()
        op_id, claim_id = self.build_imply_chain(sdk)

        conf_unmit = get_conf(run_ep(sdk), claim_id)
        sdk.mitigate_operator(op_id, "Major counter-evidence", 0.50)
        conf_strong = get_conf(run_ep(sdk), claim_id)

        assert conf_strong < conf_unmit, (
            f"strong mitigation must lower downstream confidence: "
            f"unmitigated={conf_unmit:.4f}, strong={conf_strong:.4f}"
        )
        assert conf_unmit - conf_strong >= STRONG_DELTA_MIN, (
            f"strong mitigation delta below threshold: "
            f"{conf_unmit - conf_strong:.4f} < {STRONG_DELTA_MIN}"
        )

    def test_weak_mitigation_lowers_less_than_strong_monotone(self):
        """(b) strength is monotone: 0.10 < 0.30 < 0.50 dampening."""
        sdk = fresh_sdk()
        op_id, claim_id = self.build_imply_chain(sdk)

        conf_unmit = get_conf(run_ep(sdk), claim_id)
        confs = {}
        for strength in (0.10, 0.30, 0.50):
            # Idempotent update: same mitigation point, new strength.
            sdk.mitigate_operator(op_id, f"mitigate at {strength}", strength)
            confs[strength] = get_conf(run_ep(sdk), claim_id)

        # Monotone: higher strength -> LOWER downstream confidence.
        assert confs[0.10] > confs[0.30] > confs[0.50], f"monotonicity violated: {confs}"
        assert confs[0.10] - confs[0.50] >= SPREAD_MIN, (
            f"weak-vs-strong spread below threshold: {confs[0.10] - confs[0.50]:.4f} < {SPREAD_MIN}"
        )
        # Both mitigations dampen below the unmitigated baseline...
        assert confs[0.10] < conf_unmit
        assert confs[0.50] < conf_unmit
        # ...and weak lowers LESS than strong (direction of the delta).
        d_weak = conf_unmit - confs[0.10]
        d_strong = conf_unmit - confs[0.50]
        assert d_weak < d_strong, (
            f"weak delta ({d_weak:.4f}) must be below strong delta ({d_strong:.4f})"
        )
        assert d_strong >= STRONG_DELTA_MIN

    def test_unrelated_nand_pair_unaffected_by_imply_mitigation(self):
        """(c) dampening is operator-scoped — other factors keep their w."""
        sdk = fresh_sdk()
        op_id, _claim_id = self.build_imply_chain(sdk)
        # Independent NAND component in the same graph.
        attacker = make_point(sdk, "NAND attacker", tier=TIER_MAP["T0"])
        target = make_point(sdk, "NAND target", tier=TIER_MAP["T2"])
        nand_op = make_operator(sdk, attacker["id"], target["id"], "NAND")

        conf_target_before = get_conf(run_ep(sdk), target["id"])
        sdk.mitigate_operator(op_id, "dampen the IMPL only", 0.50)
        conf_target_after = get_conf(run_ep(sdk), target["id"])
        # Same graph except the IMPL operator's strength — NAND pair settles
        # to the same fixed point (damping convergence, tol 1e-3).
        assert abs(conf_target_after - conf_target_before) < 0.02, (
            f"NAND target moved without its operator changing: "
            f"{conf_target_before:.4f} -> {conf_target_after:.4f}"
        )
        assert nand_op["id"]


class TestMitigatedNandDampensNotRefutes:
    """(c) NAND semantics preserved: a mitigated NAND weakens but still attacks."""

    def test_mitigated_nand_still_contradicts(self):
        sdk = fresh_sdk()
        attacker = make_point(sdk, "T0 NAND attacker", tier=TIER_MAP["T0"])
        target = make_point(sdk, "Contradicted claim", tier=(4, 1))
        nand_op = make_operator(sdk, attacker["id"], target["id"], "NAND")

        conf_unmit = get_conf(run_ep(sdk), target["id"])
        sdk.mitigate_operator(nand_op["id"], "Attack overstated", 0.50)
        conf_mit = get_conf(run_ep(sdk), target["id"])

        # Dampened: the 0.50 mitigation weakens the attack (target higher).
        assert conf_mit > conf_unmit, (
            f"mitigated NAND should attack more weakly: unmit={conf_unmit:.4f} mit={conf_mit:.4f}"
        )
        assert conf_mit - conf_unmit >= NAND_WEAKEN_MIN, (
            f"NAND dampening below threshold: {conf_mit - conf_unmit:.4f} < {NAND_WEAKEN_MIN}"
        )
        # NOT refuted: the target still sits well below its Beta(4,1) prior
        # mean (0.8) — the contradiction still lands, only softer.
        assert conf_mit < 0.72, (
            f"mitigated NAND must still contradict: target {conf_mit:.4f} "
            f"should be below prior mean 0.8"
        )


class TestMitigatedBySchemaRule:
    """(d) mitigated_by can ONLY originate from an is_operator:true Point."""

    def test_mitigate_operator_rejects_plain_point(self):
        sdk = fresh_sdk()
        plain = make_point(sdk, "A plain claim — not an operator")
        with pytest.raises(ValueError, match="not an operator"):
            sdk.mitigate_operator(plain["id"], "must fail", 0.30)
        # No (op)-[:mitigated_by]->(m) edge may exist after the rejection.
        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (:Point)-[:mitigated_by]->(:Point) RETURN count(*)").result_set
        assert rows[0][0] == 0

    def test_mitigate_operator_rejects_missing_point(self):
        sdk = fresh_sdk()
        with pytest.raises(ValueError, match="not found"):
            sdk.mitigate_operator("does-not-exist", "nope", 0.30)

    def test_generic_create_edge_cannot_write_mitigated_by(self):
        """mitigated_by is not in the create_edge allowlist (§3.9)."""
        sdk = fresh_sdk()
        a = make_point(sdk, "A")
        b = make_point(sdk, "B")
        with pytest.raises(ValueError, match="Unknown predicate"):
            sdk.create_edge("mitigated_by", a["id"], b["id"])
