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
    """The risk-2 leg: an agent-filed true NAND moves the target's EP
    posterior mean by >= the [cal] ep-variance row on the next retrieve —
    the EP channel is LIVE (an inert engine retires nothing)."""
    result = run_ep_liveness(str(tmp_path / "live"))
    assert liveness_ok(result), (
        f"EP mechanism-liveness FAILED on the product path: {result} — "
        f"per plan Task 8 Step 1 this mandates a PRODUCT issue (never "
        f"an opt-out)")
    assert result["ep_outcome"] in ("converged", "contested"), result
    assert result["affected_count"] > 0, result


def test_ep_read_channel_never_arm_unavailable(tmp_path):
    """The read surface itself is healthy (never ArmUnavailable) — a dead
    read channel would fake the delta by raising instead of measuring."""
    try:
        run_ep_liveness(str(tmp_path / "live2"))
    except ArmUnavailable as e:  # pragma: no cover — failure path
        pytest.fail(f"ArmUnavailable on the product read channel: {e}")
