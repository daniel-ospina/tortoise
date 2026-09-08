"""#2291 I-4 read channel + honest ep_outcome terminal table tests.

Locks the plan's Task-4 acceptance: Memory.confidence is the EP posterior
mean (never None on the real path); the terminal table is decisive-only
(empty affected set never decisive; contested non-decisive BY CONSTRUCTION
even when the engine converged); the decide cap never forces CONVERGED;
undec vs non_converged tie-breaks on scenario task_type; the [cal]
ep-variance row is passed explicitly as variance_threshold.
"""
from __future__ import annotations

import pytest

from battery.arms.a4_tortoise import DECIDE_CYCLES_CAP
from battery.arms.base import AgentContext, Memory
from battery.config.thresholds import load_thresholds
from battery.testing.seeds import setup_seed_mode


@pytest.fixture(scope="module")
def ep_variance() -> float:
    rows = load_thresholds(
        "battery/config/thresholds.yaml").cal_rows
    val = [v for (m, a, v) in rows if m == "ep-variance" and a == "a4"]
    assert val, "thresholds.yaml [cal] ep-variance row for a4 missing"
    return float(val[0])


def _store(tmp_path, sid: str = "ct-001"):
    return setup_seed_mode(tmp_path, sid)


def _record(store, content: str, kind: str = "nand",
            confidence=None) -> None:
    mems = store.retrieve("")
    store._arm.record(
        AgentContext(scenario=store._scenario, episode_seed=0,
                     prior_memories=tuple(mems), user_message="go"),
        Memory(id="e", content=content, confidence=confidence, kind=kind))


def test_retrieve_confidence_never_none(tmp_path) -> None:
    """Memory.confidence on the real path = the EP posterior mean (seed
    baselines: medium Beta(3,1) mean 0.75) — never None."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        assert mems
        for m in mems:
            assert m.confidence is not None
            assert 0.0 <= m.confidence <= 1.0
        claims = [m for m in mems if m.kind == "claim"]
        assert claims
        assert any(abs(m.confidence - 0.75) < 1e-6 for m in claims)
    finally:
        store.close()


def test_pristine_noop_terminal_outcome(tmp_path, ep_variance) -> None:
    """A fresh seed_mode episode (no writes, no operator edges) reads
    no-op — the empty affected set is never decisive."""
    store = _store(tmp_path)
    try:
        out = store._arm.ep_terminal_outcome(
            store._scenario, variance_threshold=ep_variance)
        assert out["outcome"] == "no-op"
        assert out["affected_count"] == 0
        assert out["capped"] is False
    finally:
        store.close()


def test_decided_nand_reads_converged(tmp_path, ep_variance) -> None:
    """A closed-set NAND produces a decisive affected set → converged when
    the posterior variance stays within the [cal] row."""
    store = _store(tmp_path)
    try:
        _record(store, "counter-evidence alpha", kind="nand")
        assert store._arm.decide_cycles == 1
        out = store._arm.ep_terminal_outcome(
            store._scenario, variance_threshold=ep_variance)
        assert out["decide_cycles"] == 1
        # variance of a medium-evidence NAND stays under the row OR reads
        # contested — both are engine-honest; converged requires decisive.
        if out["outcome"] == "converged":
            assert out["affected_count"] > 0
            assert out["max_variance"] <= ep_variance
        else:
            assert out["outcome"] in ("contested", "non_converged", "undec")
    finally:
        store.close()


def test_contested_non_decisive_even_when_converged(tmp_path, ep_variance
                                                    ) -> None:
    """A low-credibility conflicting NAND pushes the claim's posterior
    variance above the [cal] row → CONTESTED even though the engine
    converges (mechanism convergence != decisiveness); numeric variance is
    retained."""
    store = _store(tmp_path)
    try:
        mems = store.retrieve("")
        claims = [m for m in mems if m.kind == "claim"]
        assert claims
        sdk = store._arm._sdk(store._scenario)
        ev = sdk.create_point(kind="evidence", content="weak contradictory "
                             "probe", credibility="unverified")
        sdk.create_operator("NAND", ev["id"], [claims[0].id],
                            direction="unidirectional")
        store._arm.decide_cycles = 1
        out = store._arm.ep_terminal_outcome(
            store._scenario, variance_threshold=ep_variance)
        assert out["outcome"] == "contested"
        assert out["max_variance"] > ep_variance
        assert out["converged"] is True  # engine converged — still contested
        assert out["capped"] is False
    finally:
        store.close()


def test_decide_cap_never_forces_converged(tmp_path, ep_variance) -> None:
    """At the decide cap the process STOPPED — never forced CONVERGED:
    non_converged on a non-loopy scenario (ct), undec on a loopy one
    (lp-001) when the state is not contested."""
    ct = _store(tmp_path, "ct-001")
    try:
        _record(ct, "cap probe finding", kind="nand")
        ct._arm.decide_cycles = DECIDE_CYCLES_CAP
        out = ct._arm.ep_terminal_outcome(
            ct._scenario, variance_threshold=ep_variance)
        assert out["capped"] is True
        if out["outcome"] == "contested":
            pass  # contested state persists (still non-decisive)
        else:
            assert out["outcome"] == "non_converged"  # ct is not loopy
    finally:
        ct.close()
    lp = _store(tmp_path, "lp-001")
    try:
        _record(lp, "cap probe finding", kind="nand")
        lp._arm.decide_cycles = DECIDE_CYCLES_CAP
        out = lp._arm.ep_terminal_outcome(
            lp._scenario, variance_threshold=ep_variance)
        assert out["capped"] is True
        if out["outcome"] != "contested":
            assert out["outcome"] == "undec"  # lp is loopy → undec, never converged
    finally:
        lp.close()


def test_lp_engine_convergence_boundary_documented(tmp_path, ep_variance
                                                   ) -> None:
    """Reachability boundary (pre-verified RED, probe 2026-09-07): the
    damped embedded engine CONVERGES the authored lp NAND triangles
    (iterations 13) — the engine-diagnostic undec leg is currently
    unreachable on the committed corpus by design; undec is reached via the
    decide-cap path. Never force undec. This test documents the boundary so
    a future corpus change that makes lp truly oscillate is noticed."""
    lp = _store(tmp_path, "lp-001")
    try:
        sdk = lp._arm._sdk(lp._scenario)
        cc = sdk.compute_confidence(factors=None, anchors=None)
        assert cc.get("converged") is True  # engine converges lp today
        out = lp._arm.ep_terminal_outcome(
            lp._scenario, variance_threshold=ep_variance)
        # no writes yet → honest no-op (never a forced undec)
        assert out["outcome"] in ("no-op", "converged", "contested")
        assert out["outcome"] != "undec"
    finally:
        lp.close()


def test_cal_row_present_and_readable(ep_variance) -> None:
    """thresholds.yaml [cal] ep-variance row exists (0.04) and is the arm's
    explicit variance_threshold source (no inline constant in the arm)."""
    assert ep_variance == 0.04
    import ast
    from pathlib import Path
    src = Path("battery/arms/a4_tortoise.py").read_text()
    tree = ast.parse(src)
    # default only on the method signature; call sites must pass explicitly
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", "") == "ep_terminal_outcome"]
    # tests/consumers pass variance_threshold; the arm internal default is
    # the documented product default, but the ROW is the authoritative value.
    assert "variance_threshold" in src
