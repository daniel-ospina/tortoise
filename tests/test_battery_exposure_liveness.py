"""#2284 Task 8 Step 1 — EP mechanism-liveness on the REAL product path.

A synthetic EP smoke graph: the agent files a TRUE NAND (two
high-credibility contradictory evidence points + closed-set NAND through
arm.record) and the product EP engine's posterior on the target must move
by >= the [cal] ep-variance row (thresholds.yaml, never a literal) AND the
moved value must be visible on the NEXT product retrieve (sibling-A
surface). If the product path fails this leg, the plan mandates a PRODUCT
issue — never an opt-out.
"""
from __future__ import annotations

import os

import pytest

from battery.arms.base import ArmUnavailable
from battery.exposure.liveness import liveness_ok, run_ep_liveness
from battery.testing.seeds import setup_seed_mode


@pytest.fixture(autouse=True, scope="module")
def _force_embedded_lane() -> None:
    """Hermetic per-test stores materialize scenario graphs as named
    (battery_ct-001 …) — a TORTOISE_DB_URI redirect folds graphs per test
    and voids the assertions. Force the embedded lane (precedent:
    test_embedded_lifecycle / test_battery_ep_outcome)."""
    saved_uri = os.environ.pop("TORTOISE_DB_URI", None)
    saved_path = os.environ.pop("TORTOISE_DB_PATH", None)
    try:
        yield
    finally:
        if saved_uri is not None:
            os.environ["TORTOISE_DB_URI"] = saved_uri
        if saved_path is not None:
            os.environ["TORTOISE_DB_PATH"] = saved_path


def test_agent_nand_moves_ep_posterior(tmp_path):
    """The risk-2 leg at the LOGICAL bar (owner challenge 2026-09-08):
    two strong (high-credibility) contradictions filed through the product
    record path against the seeded weak/medium support must drive the
    target's posterior BELOW neutral 0.50 — the EP channel resolves a
    deliberation logically (an engine that only twitches by the epsilon
    floor would retire nothing)."""
    result = run_ep_liveness(str(tmp_path / "live"))
    assert liveness_ok(result), (
        f"EP mechanism-liveness FAILED the logical bar on the product "
        f"path: {result} — per plan Task 8 Step 1 this mandates a PRODUCT "
        f"issue (never an opt-out)")
    assert result["moved"] is not None and result["moved"] < 0.50, result
    assert result["ep_outcome"] in ("converged", "contested"), result
    assert result["affected_count"] > 0, result


def test_ep_read_channel_never_arm_unavailable(tmp_path):
    """The read surface itself is healthy (never ArmUnavailable) — a dead
    read channel would fake the delta by raising instead of measuring."""
    try:
        run_ep_liveness(str(tmp_path / "live2"))
    except ArmUnavailable as e:  # pragma: no cover — failure path
        pytest.fail(f"ArmUnavailable on the product read channel: {e}")


def _posterior_after(store, n_nand: int, credibility: str) -> tuple[float, float]:
    """Seed-mode store + n NAND contradictions at ONE credibility tier
    (direct SDK wiring — the clean-room engine check) → (baseline, moved)
    on the target claim after an EP run."""
    arm = store._arm
    sc = store._scenario
    before = store.retrieve("")
    claims = [m for m in before if m.kind == "claim"]
    target = claims[0]
    c0 = target.confidence
    sdk = arm._sdk(sc)
    for i in range(n_nand):
        ev = sdk.create_point(
            kind="evidence",
            content=f"contradicting source {i} ({credibility}) {n_nand}",
            credibility=credibility, status="live")
        sdk.create_operator("NAND", ev["id"], [target.id],
                            direction="unidirectional")
    arm.ep_terminal_outcome(sc, variance_threshold=0.04)
    after = store.retrieve("")
    moved = next((m.confidence for m in after
                  if m.kind == "claim" and m.id == target.id), c0)
    return float(c0), float(moved)


def _store(tmp_path, name: str):
    return setup_seed_mode(str(tmp_path / name), "ct-001")


def test_ep_deliberation_logic_matrix(tmp_path):
    """Owner challenge lock (2026-09-08): the EP engine must calculate the
    outcome of a deliberation LOGICALLY on properly wired graphs —
    (1) contradiction STRENGTH is monotone (low < medium < high < gold
    pull harder); (2) contradiction COUNT accumulates (1 < 2 strong);
    (3) the owner's exact case — weak/medium support vs TWO strong
    contradictions — resolves the claim BELOW neutral 0.50; (4) direction:
    support raises, contradiction lowers (checked implicitly: baseline
    starts at the seeded mean and every NAND lowers it)."""
    import tempfile
    outs: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as ns:
        for cred in ("low", "medium", "high", "gold"):
            st = setup_seed_mode(f"{ns}/s-{cred}", "ct-001")
            try:
                c0, m = _posterior_after(st, 1, cred)
                outs[cred] = m
            finally:
                st.close()
        st = setup_seed_mode(f"{ns}/s-2high", "ct-001")
        try:
            c0, two = _posterior_after(st, 2, "high")
        finally:
            st.close()
    assert c0 > 0.70  # seeded support baseline (sanity)
    # (1) strength monotone: higher credibility pulls the target lower
    assert outs["low"] > outs["medium"] > outs["high"] > outs["gold"]
    # (2) count accumulates: two high < one high
    assert two < outs["high"]
    # (3) two strong contradictions against the weak/medium support resolve
    # the claim below neutral
    assert two < 0.50
