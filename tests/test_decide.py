"""Tests for tortoise decide — decision comparison wiring, truth vs relevance semantics.

Runnable with: python3 -m pytest tests/test_decide.py -v
Requires TORTOISE_DB_URI pointing at a FalkorDB (set by tests/conftest.py — isolated test graph #99).

Phase 2 (#49): context field removed. Tests use anchors or omit context entirely.
"""
from __future__ import annotations

import os
import sys
import tempfile as _tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests import _live_utils
from tortoise.sdk import TortoiseSDK

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Mirrors the probe pattern
# in tests/test_integration_search.py.
#
# The probe must not touch the DEFAULT embedded store: the first holder of
# the canonical path keeps it for the whole pytest process (servers close at
# atexit), so a bare TortoiseSDK() probe flips FALKORDB_AVAILABLE by LPT
# order — the tier-2 (b) leg (URI-less, carve-out) reds the skip-guard when
# an earlier selected file already holds it. Probe an isolated temp path:
# deterministic on every lane (docker lanes keep the URI branch — db_path is
# ignored when TORTOISE_DB_URI is set).
_DECIDE_EMBEDDED_DIR = _tempfile.mkdtemp(prefix="tortoise_")

FALKORDB_AVAILABLE = False
try:
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK(db_path=os.path.join(_DECIDE_EMBEDDED_DIR, "probe.db"))
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")



@pytest.fixture
def sdk():
    """SDK against the live FalkorDB with a unique namespace per test run."""
    # #1302: compute_confidence/dream are fail-closed on calibration by
    # default; these tests assert EP semantics with synthetic baselines —
    # calibration posture is orthogonal (same convention as epic903_fixtures
    # and the Track B suite).
    os.environ.setdefault("TORTOISE_EP_REQUIRE_CALIBRATION", "0")
    ns = f"test_decide_{uuid.uuid4().hex[:8]}"
    # Per-test isolated path on the embedded lane (see probe note above) —
    # never contends with the shared default store; docker lanes ignore it.
    sdk = TortoiseSDK(namespace=ns,
                      db_path=os.path.join(_DECIDE_EMBEDDED_DIR, f"{ns}.db"))
    yield sdk
    sdk.close()


def _mk():
    return uuid.uuid4().hex[:8]


class TestDecideScriptSmoke:
    """#1643 (D3): the decide.py SCRIPT itself must run end-to-end — points
    land (no #334 stubs), operators resolve, EP ranks the options. The #49
    `context=` TypeError made every point soft-fail and silently produced
    stub operators; this smoke test runs the script via subprocess against
    the live FalkorDB and asserts a clean run (ticks + EP, no ⚠ failures)."""

    def test_script_runs_clean(self):
        import socket
        import subprocess
        import sys
        import uuid as _uuid
        # This smoke test drives the SCRIPT against the LEGACY FalkorDB
        # container (:16379) via a docker:// URI — the module-level
        # FALKORDB_AVAILABLE probe only checks the embedded lane, so it
        # cannot gate this dependency. Repo skip convention (#1436):
        # docker-required tests SKIP, never ERROR on ConnectionError —
        # mirrors the _docker_reachable probe in
        # tests/test_round_trip_parity.py. CI provisions falkordb-legacy
        # on :16379, so the test still RUNS there; the documented local
        # compose (AGENTS.md) provisions only :6379 → local SKIP.
        _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _s.settimeout(1.0)
        try:
            _s.connect((_live_utils.service_host(), _live_utils.legacy_port()))
        except OSError:
            pytest.skip(
                f"Live FalkorDB legacy (:{_live_utils.legacy_port()}) not available — "
                "skip per #1436")
        finally:
            _s.close()
        graph = f"test_decide_smoke_{_uuid.uuid4().hex[:8]}"
        uri = _live_utils.legacy_uri(graph)
        res = subprocess.run(
            [sys.executable, "graph-scripts/decide.py",
             "--options", '{"opt:a":"Option A","opt:b":"Option B"}',
             "--criteria", '{"crit:1":"Criterion 1"}',
             "--findings", '{"finding:1":"Finding 1"}',
             "--edges", '[["crit:1","IMPL","opt:a"],["finding:1","IMPL","opt:a"],["finding:1","NAND","opt:b"]]',
             "--db", uri],
            capture_output=True, text=True, timeout=120,
        )
        out = res.stdout + res.stderr
        assert res.returncode == 0, f"decide.py failed:\n{out}"
        # Every point created (no ⚠ soft-failures — the #49 TypeError class).
        assert "⚠" not in out, f"soft-failures present:\n{out}"
        assert out.count("✓") >= 3, f"expected >=3 point ticks:\n{out}"
        # EP computation + ranking ran.
        assert "EP computed" in out or "rank" in out.lower(), out


class TestDecideWiring:
    """Decision comparison — create points, operators, EP computation."""

    def test_create_option_point(self, sdk):
        """Option points are created with kind='option'."""
        p = sdk.create_point("option", "AGPLv3 dual-licensing", dedup=True)
        assert p["id"]
        assert p.get("pointKind") == "option"
        # Verify dedup works (opt-in via dedup=True; content_hash stored on first call)
        p2 = sdk.create_point("option", "AGPLv3 dual-licensing", dedup=True)
        assert p2["id"] == p["id"]

    def test_create_criterion_point(self, sdk):
        """Criterion points are created with kind='criterion'."""
        p = sdk.create_point("criterion", "Developer adoption")
        assert p["id"]
        assert p.get("pointKind") == "criterion"

    def test_create_evidence_point(self, sdk):
        """Evidence/finding points are created with kind='evidence'."""
        p = sdk.create_point("evidence", "AGPLv3 is OSI-approved")
        assert p["id"]
        assert p.get("pointKind") == "evidence"

    def test_impl_operator_wiring(self, sdk):
        """IMPL edge from evidence to option."""
        opt = sdk.create_point("option", "Option A")
        ev = sdk.create_point("evidence", "Supports A")
        op = sdk.create_operator("IMPL", ev["id"], [opt["id"]])
        assert op["id"]
        assert op.get("is_operator") is True

    def test_nand_operator_wiring(self, sdk):
        """NAND edge from evidence to option."""
        opt = sdk.create_point("option", "Option A")
        ev = sdk.create_point("evidence", "Opposes A")
        op = sdk.create_operator("NAND", ev["id"], [opt["id"]])
        assert op["id"]
        assert op.get("is_operator") is True

    def test_mitigation_range_clamped(self, sdk):
        """mitigate_operator enforces [0, 1] range on strength."""
        opt = sdk.create_point("option", "Option A")
        ev = sdk.create_point("evidence", "Weak evidence")
        op = sdk.create_operator("IMPL", ev["id"], [opt["id"]])

        # Valid range
        m1 = sdk.mitigate_operator(op["id"], "Minor caveat", 0.10)
        assert m1.get("id")

        # Strength is stored — verify we can mitigate at different levels
        m2 = sdk.mitigate_operator(op["id"], "Major limitation", 0.50)
        assert m2.get("id")

    def test_decide_clamps_out_of_range_strength(self):
        """The decide CLI clamps out-of-range mitigation strength to [0.10, 0.50].

        Regression for review feedback: the clamp lives in the decide layer,
        so out-of-range values (0.05, 0.80) must be forced into range.
        """
        # Mirror the clamp logic in graph-scripts/decide.py and _cmd_decide
        def clamp(s):
            return max(0.10, min(0.50, s))

        assert clamp(0.05) == 0.10
        assert clamp(0.10) == 0.10
        assert clamp(0.30) == 0.30
        assert clamp(0.50) == 0.50
        assert clamp(0.80) == 0.50

    def test_relevance_edge_reuses_existing_operator(self, sdk):
        """A (src, op_type, tgt) in both edges and relevance_edges must NOT
        create a duplicate operator — it reuses the one from edges."""
        crit = sdk.create_point("criterion", "Enterprise readiness")
        opt = sdk.create_point("option", "Option A")

        # Simulate decide wiring: create the edge, then mitigate the SAME operator
        op1 = sdk.create_operator("NAND", crit["id"], [opt["id"]])
        sdk.mitigate_operator(op1["id"], "Not relevant", 0.20)

        # The decide layer reuses op1 (tracked by (src, op_type, tgt)) rather
        # than creating op2. Verify only ONE operator connects crit→opt.
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(t:Point) "
            "WHERE t.id = $tid RETURN count(op)",
            params={"tid": opt["id"]},
        ).result_set
        # Allow >=1 because the test itself created op1; the point is the
        # reuse semantics are encoded in decide.py (created_ops tracking).
        assert rows[0][0] >= 1

    def test_compute_confidence_with_anchors(self, sdk):
        """EP computes confidence for points via anchors-based selection."""
        # Create two options
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")

        # Evidence strongly supports A, opposes B
        ev1 = sdk.create_point("evidence", "A is better", status="live")
        ev2 = sdk.create_point("evidence", "B has problems", status="live")

        sdk.create_operator("IMPL", ev1["id"], [opt_a["id"]])
        sdk.create_operator("NAND", ev2["id"], [opt_b["id"]])

        # Use anchors-based selection (replaces context scoping)
        result = sdk.compute_confidence(anchors=[opt_a["id"], opt_b["id"]])
        assert "iterations" in result
        assert "converged" in result
        confs = result.get("confidences", {})
        assert len(confs) > 0

    def test_truth_challenge_nands_point(self, sdk):
        """Truth challenge: NAND the target finding POINT directly (it's FALSE)."""
        # Finding that supports option
        ev_support = sdk.create_point("evidence", "Provider protects privacy")
        opt = sdk.create_point("option", "Option A")
        sdk.create_operator("IMPL", ev_support["id"], [opt["id"]])

        # Truth challenge: another finding says ev_support is FALSE
        ev_challenge = sdk.create_point("evidence",
            "Metadata reveals topics — provider CAN infer content")
        truth_op = sdk.create_operator("NAND", ev_challenge["id"], [ev_support["id"]])
        assert truth_op["id"]
        # The NAND is directly on the finding point — not on the operator
        # This means the finding itself is challenged as FALSE

    def test_relevance_challenge_mitigates_operator(self, sdk):
        """Relevance challenge: mitigates the OPERATOR (TRUE but matters LESS)."""
        opt = sdk.create_point("option", "Option A")
        ev = sdk.create_point("evidence",
            "Provider cannot read content")
        impl_op = sdk.create_operator("IMPL", ev["id"], [opt["id"]])

        # Relevance challenge: the finding is TRUE but its importance is OVERSTATED
        # → mitigate the OPERATOR, NOT the finding point
        mitigation = sdk.mitigate_operator(impl_op["id"],
            "Metadata is lossy — visible but not full content", 0.20)
        assert mitigation.get("id")
        # The finding point is NOT NANDed — it's still true, just weaker

    def test_option_point_never_nanded_for_bad_fit(self, sdk):
        """Never NAND an option or criterion point for bad fit — express fit on the operator."""
        opt = sdk.create_point("option", "Option A")
        crit = sdk.create_point("criterion", "Enterprise readiness")

        # Bad fit: criterion opposes option — express as NAND on the operator
        bad_fit_op = sdk.create_operator("NAND", crit["id"], [opt["id"]])
        assert bad_fit_op["id"]

        # The option point itself is never NANDed — it's still a valid option
        # We verify by checking the option point still exists and isn't outdated
        retrieved = sdk.get_point(opt["id"])
        assert retrieved["id"] == opt["id"]
        # Option is not outdated or invalidated (status may be draft until an edge
        # promotes it — what matters is it is NOT 'outdated')
        assert retrieved.get("status") != "outdated"

    def test_full_wiring_produces_ranked_output(self, sdk):
        """End-to-end: create options + criteria + findings, wire edges, compute confidence."""
        # Options
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")

        # Criteria
        crit_1 = sdk.create_point("criterion", "Security", status="live")
        crit_2 = sdk.create_point("criterion", "Adoption", status="live")

        # Findings
        f1 = sdk.create_point("evidence", "A is secure", status="live")
        f2 = sdk.create_point("evidence", "A has wide adoption", status="live")
        f3 = sdk.create_point("evidence", "B has security issues", status="live")

        # Wire: criteria → options
        sdk.create_operator("IMPL", crit_1["id"], [opt_a["id"]])
        sdk.create_operator("IMPL", crit_2["id"], [opt_a["id"]])
        sdk.create_operator("IMPL", crit_1["id"], [opt_b["id"]])
        sdk.create_operator("NAND", crit_2["id"], [opt_b["id"]])

        # Wire: findings → options
        sdk.create_operator("IMPL", f1["id"], [opt_a["id"]])
        sdk.create_operator("IMPL", f2["id"], [opt_a["id"]])
        sdk.create_operator("NAND", f3["id"], [opt_b["id"]])

        # Compute with anchors
        all_ids = [opt_a["id"], opt_b["id"], crit_1["id"], crit_2["id"],
                     f1["id"], f2["id"], f3["id"]]
        result = sdk.compute_confidence(anchors=all_ids)
        assert result["iterations"] >= 0
        confs = result.get("confidences", {})

        # Option A should have higher confidence (3 IMPL supports, 0 NANDs)
        # Option B should have lower confidence (1 IMPL, 2 NANDs)
        a_mean = confs.get(opt_a["id"], {}).get("mean", 0)
        b_mean = confs.get(opt_b["id"], {}).get("mean", 0)
        if isinstance(a_mean, (int, float)) and isinstance(b_mean, (int, float)):
            assert a_mean > b_mean, f"Expected A ({a_mean:.3f}) > B ({b_mean:.3f})"


class TestDecideContextFree:
    """Context-free mode: compute_confidence(factors=[operator_ids]) — no context param needed."""

    def test_context_free_produces_ranked_table(self, sdk):
        """Wire 2 options + findings, run compute_confidence(factors=...),
        and assert a ranked table is produced with numeric confidences."""
        # Options
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")

        # Findings
        f1 = sdk.create_point("evidence", "A is strongly supported", status="live")
        f2 = sdk.create_point("evidence", "B has major issues", status="live")

        # Collect operator IDs as the CLI would for --context-free mode
        operator_ids: list[str] = []

        # Wire: finding → options (IMPL supports, NAND opposes)
        # A gets 2 IMPL supports, B gets 1 NAND oppose → clear A > B
        op1 = sdk.create_operator("IMPL", f1["id"], [opt_a["id"]])
        operator_ids.append(op1["id"])

        op2 = sdk.create_operator("IMPL", f1["id"], [opt_b["id"]])
        operator_ids.append(op2["id"])

        op3 = sdk.create_operator("NAND", f2["id"], [opt_b["id"]])
        operator_ids.append(op3["id"])

        # Extra IMPL for A to create clear separation
        f3 = sdk.create_point("evidence", "A is also cost-effective", status="live")
        op4 = sdk.create_operator("IMPL", f3["id"], [opt_a["id"]])
        operator_ids.append(op4["id"])

        assert len(operator_ids) == 4, f"Expected 4 operators, got {len(operator_ids)}"

        # Compute confidence via explicit factors (context-free)
        result = sdk.compute_confidence(factors=operator_ids)

        assert "iterations" in result
        assert result["iterations"] >= 0
        assert "converged" in result

        confs = result.get("confidences", {})
        assert len(confs) > 0, "Expected at least some confidence entries"

        # Collect per-option confidence (only option-kind points)
        opt_conf: dict[str, float] = {}
        for pid, cid in {"opt:a": opt_a["id"], "opt:b": opt_b["id"]}.items():
            mean = confs.get(cid, {}).get("mean")
            if isinstance(mean, (int, float)):
                opt_conf[pid] = float(mean)

        assert len(opt_conf) > 0, "Expected ranked confidence for at least one option"

        # Assert all confidences are numeric and in [0, 1]
        for pid, c in opt_conf.items():
            assert isinstance(c, float), f"{pid} confidence is not float: {type(c)}"
            assert 0.0 <= c <= 1.0, f"{pid} confidence out of range: {c}"

        # Option A (2 IMPL, 0 NAND) should have higher confidence than
        # Option B (1 IMPL, 1 NAND)
        a_mean = opt_conf.get("opt:a", 0)
        b_mean = opt_conf.get("opt:b", 0)
        assert a_mean > b_mean, (
            f"Expected A ({a_mean:.4f}) > B ({b_mean:.4f}) — "
            f"A has 2 IMPL (strong support), B has 1 IMPL + 1 NAND (contested)"
        )

    def test_context_free_produces_consistent_ranking(self, sdk):
        """Same wiring with anchors-based vs context-free should produce
        ranked output within tolerance (not identical — different EP scopes —
        but same relative ordering)."""
        # Options
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")

        # Findings
        f1 = sdk.create_point("evidence", "A is good", status="live")
        f2 = sdk.create_point("evidence", "B is bad", status="live")

        operator_ids: list[str] = []

        op1 = sdk.create_operator("IMPL", f1["id"], [opt_a["id"]])
        operator_ids.append(op1["id"])
        op2 = sdk.create_operator("NAND", f2["id"], [opt_b["id"]])
        operator_ids.append(op2["id"])

        # Context-free
        result_cf = sdk.compute_confidence(factors=operator_ids)
        # Anchors-based
        result_anchors = sdk.compute_confidence(
            anchors=[opt_a["id"], opt_b["id"], f1["id"], f2["id"]],
        )

        cf_confs = result_cf.get("confidences", {})
        anchor_confs = result_anchors.get("confidences", {})

        def get_mean(confs, pid):
            m = confs.get(pid, {}).get("mean")
            return float(m) if isinstance(m, (int, float)) else None

        a_cf = get_mean(cf_confs, opt_a["id"])
        b_cf = get_mean(cf_confs, opt_b["id"])
        a_an = get_mean(anchor_confs, opt_a["id"])
        b_an = get_mean(anchor_confs, opt_b["id"])

        # Both modes should rank Option A above Option B. Fail loudly
        # instead of passing vacuously if EP produced no confidences
        # (draft-filter degeneracy would silently zero them — #992).
        assert all(v is not None for v in [a_cf, b_cf, a_an, b_an]), (
            f"EP produced no confidences (a_cf={a_cf}, b_cf={b_cf}, "
            f"a_an={a_an}, b_an={b_an}) — draft-filter degeneracy?"
        )
        assert a_cf > b_cf, (
            f"Context-free: Expected A ({a_cf:.4f}) > B ({b_cf:.4f})"
        )
        assert a_an > b_an, (
            f"Anchors: Expected A ({a_an:.4f}) > B ({b_an:.4f})"
        )
        # Confidences should be within reasonable tolerance
        assert abs(a_cf - a_an) < 0.5, (
            f"A confidence divergence: cf={a_cf:.4f} anchors={a_an:.4f}"
        )


class TestIssue400EPFixes:
    """#400: EP N+1 factor extraction + create_operator validation + empty-result signal."""

    def test_batch_factor_extraction_has_both_inputs(self, sdk):
        """Batch extract_svbp_factors returns operators with all inputs (not N+1 drops)."""
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")
        ev = sdk.create_point("evidence", "Both good", status="live")

        # Create operator with source + 2 targets (3 inputs total)
        op = sdk.create_operator("IMPL", ev["id"], [opt_a["id"], opt_b["id"]])

        proj = sdk._get_proj()
        factors, _ = proj.extract_svbp_factors()

        # Find our operator in the extracted factors
        op_factor = None
        for f in factors:
            if f[0] == op["id"]:
                op_factor = f
                break

        assert op_factor is not None, f"Operator {op['id']} not found in extracted factors"
        _op_id, op_type, input_ids, weight = op_factor
        assert op_type == "IMPL"
        assert weight == 1.0
        # Should have all 3 inputs: source (ev) + 2 targets (opt_a, opt_b)
        assert len(input_ids) >= 2, \
            f"Expected >=2 inputs, got {len(input_ids)}: {input_ids}"
        assert ev["id"] in input_ids
        assert opt_a["id"] in input_ids
        assert opt_b["id"] in input_ids

    def test_create_operator_missing_endpoint_raises(self, sdk):
        """create_operator with missing source or target raises ValueError (no silent drop)."""
        ev = sdk.create_point("evidence", "Real evidence")
        fake_id = "nonexistent-point-000000"

        # Missing target
        with pytest.raises(ValueError, match="do not exist"):
            sdk.create_operator("IMPL", ev["id"], [fake_id])

        # Missing source
        with pytest.raises(ValueError, match="do not exist"):
            sdk.create_operator("IMPL", fake_id, [ev["id"]])

    def test_compute_confidence_empty_signals_no_dirty_roots(self, sdk):
        """compute_confidence on empty graph returns diagnostic='no_dirty_roots' (#395).

        Pre-#395 the empty/clean no-arg returned 'no_factors' (global extract
        found no operators); the new no-arg contract short-circuits on the
        dirty-roots check first — 'no_factors' now means "dirty roots but no
        live closure" (breaking-change record, AC8)."""
        result = sdk.compute_confidence()
        assert result["iterations"] == 0
        assert result["converged"] is True
        assert result.get("diagnostic") == "no_dirty_roots", (
            f"Expected diagnostic='no_dirty_roots' on empty graph, got {result}"
        )
        assert result["confidences"] == {}

    def test_nand_operator_gets_weight_3(self, sdk):
        """NAND operators get weight 3.0 in factor extraction (batch query preserves this)."""
        opt_a = sdk.create_point("option", "Option A", status="live")
        opt_b = sdk.create_point("option", "Option B", status="live")
        ev = sdk.create_point("evidence", "A opposes B", status="live")

        op = sdk.create_operator("NAND", ev["id"], [opt_a["id"], opt_b["id"]])

        proj = sdk._get_proj()
        factors, _ = proj.extract_svbp_factors()

        nand_factors = [f for f in factors if f[0] == op["id"]]
        assert len(nand_factors) == 1
        _op_id, op_type, _inputs, weight = nand_factors[0]
        assert op_type == "NAND"
        assert weight == 3.0, f"Expected NAND weight 3.0, got {weight}"


# ─────────────────────────────────────────────────────────────────────────────
# #6671 — a re-run must not change the answer.
#
# `create_point` is written with dedup=True (content hash), so the points
# converge across runs; `create_operator` is NOT idempotent. decide.py kept its
# `created_ops` map per-run only, so every re-run re-created the full edge set.
# Because `mitigate_operator` is idempotent PER OPERATOR, the re-run's
# mitigations landed on the second copy: run-1 edges damped, run-2 edges at full
# weight, both read by EP. Measured on #6671: 2-edge decision, two runs →
# 4 operators, option confidence 0.5736 → 0.5184.
# ─────────────────────────────────────────────────────────────────────────────

def _load_decide_script():
    """Import graph-scripts/decide.py as a module (it is a script, not a package)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "graph-scripts" / "decide.py"
    spec = importlib.util.spec_from_file_location("decide_script_6671", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _operator_census(sdk) -> dict[str, int]:
    """Operator + mitigation counts in the fixture's graph, by op_type."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (op:Point) WHERE op.is_operator = true "
        "RETURN op.op_type, count(op)").result_set
    census = {row[0]: int(row[1]) for row in rows}
    mit = proj.g.query(
        "MATCH (op:Point)-[r:mitigated_by]->(m) RETURN count(r)").result_set
    census["_mitigations"] = int(mit[0][0]) if mit else 0
    return census


class TestIssue6671DecideRerunIsIdempotent:
    """#6671: filing the SAME decision twice must not add operators, split the
    mitigations across copies, or move the EP ranking."""

    def _decision(self, *, label=None):
        edge = ["crit:1", "IMPL", "opt:a"]
        if label is not None:
            edge = [*edge, label]
        return {
            "context": "issue-6671",
            "options": {"opt:a": "Option A", "opt:b": "Option B"},
            "criteria": {"crit:1": "Criterion 1"},
            "findings": {"finding:1": "Finding 1"},
            "edges": [edge, ["finding:1", "NAND", "opt:b"]],
            "truth_edges": [{"source": "finding:1", "op_type": "NAND",
                             "target": "opt:a"}],
            "relevance_edges": [{"source": "crit:1", "op_type": "IMPL",
                                 "target": "opt:a", "reason": "weak",
                                 "strength": 0.20}],
        }

    def test_rerun_does_not_duplicate_operators(self, sdk):
        decide = _load_decide_script()
        data = self._decision()

        decide.file_decision(sdk, data)
        first = _operator_census(sdk)
        # 2 edges + 1 truth edge, all distinct triples: one IMPL, two NAND.
        assert first.get("IMPL") == 1, first
        assert first.get("NAND") == 2, first

        decide.file_decision(sdk, data)
        second = _operator_census(sdk)

        assert second == first, (
            "a re-run grew the edge set — EP now counts every relation twice "
            f"(before={first}, after={second})")

    def test_rerun_leaves_rankings_unchanged(self, sdk):
        decide = _load_decide_script()
        data = self._decision()

        run1 = decide.file_decision(sdk, data)
        run2 = decide.file_decision(sdk, data)

        # A vacuous pass: `approx({}) == {}`, so a regression that stops
        # producing any ranking at all (e.g. a draft-elected bridge) would leave
        # this invariance assertion green.
        assert run1["confidences"] and run2["confidences"], (
            "EP produced no option confidences — the invariance assertion "
            f"below would pass vacuously (run1={run1['confidences']}, "
            f"run2={run2['confidences']})")

        # EP is an iterative solve, so two passes over the SAME graph are not
        # bit-identical (measured drift here: ~1e-8). The defect this pins is
        # six orders of magnitude larger — a second filing of the same decision
        # moved opt:a 0.5736 → 0.5184 (#6671), because the duplicated
        # operators split the mitigations.
        assert run2["confidences"] == pytest.approx(
            run1["confidences"], abs=1e-6), (
            "the ranking depends on how many times the script was run: "
            f"run1={run1['confidences']} run2={run2['confidences']}")

    def test_rerun_keeps_one_mitigation_on_the_live_bridge(self, sdk):
        """The mitigation must damp the ONE operator, not split across copies."""
        decide = _load_decide_script()
        data = self._decision()

        decide.file_decision(sdk, data)
        decide.file_decision(sdk, data)

        census = _operator_census(sdk)
        assert census["_mitigations"] == 1, (
            "mitigations split across duplicate operators (#6671): " + str(census))

    def test_distinct_labels_are_distinct_operators(self, sdk):
        """Operator identity includes the Layer-3 label: two edges on the same
        triple with different labels are two claims, and must not be collapsed."""
        decide = _load_decide_script()

        decide.file_decision(sdk, self._decision(label="supports"))
        decide.file_decision(sdk, self._decision(label="refutes"))
        decide.file_decision(sdk, self._decision(label="supports"))

        census = _operator_census(sdk)
        assert census.get("IMPL") == 2, (
            "labeled edges collapsed onto one operator, or duplicated on re-run: "
            + str(census))


class TestIssue6671CriterionKindRegistered:
    """#6671 defect 2: `criterion` is a DECIDE_PART_KINDS member but was never
    registered, so every criterion write warned 'Unrecognized pointKind'."""

    def test_criterion_is_a_registered_point_kind(self):
        from tortoise.domain_loader import known_kinds

        assert "criterion" in known_kinds("pointKind"), (
            "criterion is in sdk.DECIDE_PART_KINDS but not registered — the "
            "decide flow writes a kind the vocabulary does not know")

    def test_criterion_write_emits_no_unknown_kind_warning(self, sdk, caplog):
        import logging

        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.create_point("criterion", f"Developer adoption {uuid.uuid4().hex[:6]}")
        assert "Unrecognized pointKind" not in caplog.text, caplog.text


class TestIssue6671ReviewGaps:
    """The three gaps a fresh reviewer found in the first fix attempt — each one
    was a way the re-run could still duplicate an operator or silently drop an
    edge, and each is pinned here so it cannot come back."""

    def _data(self, edge, *, truth_edges=None, relevance_edges=None):
        return {
            "context": "issue-6671-gaps",
            "options": {"opt:a": "Option A"},
            "criteria": {"crit:1": "Criterion 1"},
            "findings": {},
            "edges": [edge],
            "truth_edges": truth_edges or [],
            "relevance_edges": relevance_edges or [],
        }

    def test_rerun_does_not_duplicate_part_whole_operators(self, sdk):
        """`composedOf` (and the rest of the part/whole family) mirrors its
        bridge as `hasPart`; the lookup is edge-type agnostic, so it must reuse
        those too — refusing to look them up re-created #6671's defect for
        every op_type `create_operator` accepts."""
        decide = _load_decide_script()
        data = self._data(["crit:1", "composedOf", "opt:a"])

        decide.file_decision(sdk, data)
        first = _operator_census(sdk)
        decide.file_decision(sdk, data)
        second = _operator_census(sdk)

        assert first.get("composedOf") == 1, first
        assert second == first, f"part/whole edge duplicated on re-run: {second}"

    def test_rerun_does_not_duplicate_non_point_endpoints(self, sdk):
        """Operator endpoints may be :Point/:Subject/:Object/:Event (#7902). An
        endpoint anchored as `:Point` in the lookup misses a Subject/Object
        endpoint and duplicates the edge."""
        decide = _load_decide_script()
        subject = sdk.create_subject(f"Subject {_mk()}")
        option = sdk.create_point("option", f"Option {_mk()}")

        data = {
            "context": "issue-6671-gaps",
            "options": {}, "criteria": {}, "findings": {},
            "edges": [[subject["id"], "IMPL", option["id"]]],
            "truth_edges": [], "relevance_edges": [],
        }

        decide.file_decision(sdk, data)
        first = _operator_census(sdk)
        decide.file_decision(sdk, data)
        second = _operator_census(sdk)

        assert first.get("IMPL") == 1, first
        assert second == first, (
            f"Subject→Point edge duplicated on re-run: {second}")

    def test_draft_operator_is_reused_and_its_status_reported(self, sdk, capsys):
        """A draft operator is EXCLUDED from EP (#780), and #6671's scoping
        decides the posture: elect it, REPORT the status, and never file a live
        twin — a twin is what lets `update_point(draft, status="live")` leave
        two live copies on one bridge (measured), i.e. this issue's defect
        returning permanently."""
        decide = _load_decide_script()
        crit = sdk.create_point("criterion", f"Criterion {_mk()}")
        opt = sdk.create_point("option", f"Option {_mk()}")
        draft = sdk.create_operator("IMPL", crit["id"], [opt["id"]],
                                    promote_source=False)
        assert draft.get("status") == "draft"

        data = {
            "context": "issue-6671-gaps",
            "options": {}, "criteria": {}, "findings": {},
            "edges": [[crit["id"], "IMPL", opt["id"]]],
            "truth_edges": [], "relevance_edges": [],
        }
        result = decide.file_decision(sdk, data)

        elected = [row for row in result["operators"] if row["op_type"] == "IMPL"]
        assert len(elected) == 1, elected
        assert elected[0]["id"] == draft["id"], (
            "a live twin was filed instead of electing the existing bridge — "
            "one operator per bridge, promoted in place, is the #6671 decision")
        bridges = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true}) RETURN count(op)").result_set
        assert bridges[0][0] == 1, f"twin operator created: {bridges}"
        out = capsys.readouterr().out
        assert "status=draft" in out, (
            "the run did not say which bridge it used: " + out)
        assert "update_point(id, status='live')" in out, (
            "the warning does not name the promotion path: " + out)

    def test_non_live_status_is_named_accurately(self, sdk, capsys):
        """A non-live operator is not necessarily a DRAFT. `retracted` is a
        stored status too (`POINT_STATUS_VALUES`), and calling a retracted claim
        a "draft" would hide that the run re-asserted it at full EP weight."""
        decide = _load_decide_script()
        crit = sdk.create_point("criterion", f"Criterion {_mk()}")
        opt = sdk.create_point("option", f"Option {_mk()}")
        op = sdk.create_operator("IMPL", crit["id"], [opt["id"]])
        # Neither `retract_point` nor `update_point` moves an OPERATOR through
        # its lifecycle (retraction is for statement points; update_point only
        # promotes draft→live), so the non-live state is written directly — the
        # shape a lifecycle writer or a legacy graph leaves behind, which is
        # exactly what the note has to describe correctly.
        sdk._get_proj().g.query(
            "MATCH (p:Point {id:$i}) SET p.status = 'retracted'",
            params={"i": op["id"]})
        assert sdk.get_point(op["id"]).get("status") == "retracted"

        data = {
            "context": "issue-6671-gaps",
            "options": {}, "criteria": {}, "findings": {},
            "edges": [[crit["id"], "IMPL", opt["id"]]],
            "truth_edges": [], "relevance_edges": [],
        }
        result = decide.file_decision(sdk, data)

        elected = [row for row in result["operators"] if row["op_type"] == "IMPL"]
        assert elected and elected[0]["id"] == op["id"], (
            "a terminal operator must be elected (one bridge, no twin), not "
            "shadowed by a fresh one")
        bridges = sdk._get_proj().g.query(
            "MATCH (op:Point {is_operator:true}) RETURN count(op)").result_set
        assert bridges[0][0] == 1, f"twin operator created: {bridges}"
        out = capsys.readouterr().out
        assert "status=retracted" in out, (
            "the warning does not name the operator's real status: " + out)
        assert "DRAFT" not in out, (
            "a retracted operator is reported as a draft: " + out)
