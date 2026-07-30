"""Tests for EP belief propagation SDK methods — #6908.

Runnable with: .venv/bin/python -m pytest tests/test_sdk_ep.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ep_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_claim(sdk: TortoiseSDK, content: str):
    return sdk.create_point("statement", content)


# ── set_prior / get_confidence ────────────────────────────────────


class TestEvidence:
    def test_set_returns_data(self, sdk):
        result = sdk.set_point_baseline("claim-1", 5.0, 2.0)
        assert result == {"claim_id": "claim-1", "alpha": 5.0, "beta": 2.0}

    def test_get_confidence_default(self, sdk):
        p = _make_claim(sdk, "some claim")
        c = sdk.get_confidence(p["id"])
        assert "mean" in c
        assert "variance" in c
        assert c["alpha"] == 1.0 and c["beta"] == 1.0  # default uniform


# ── compute_confidence ───────────────────────────────────────────────────────────


class TestRunEP:
    def test_empty_graph_returns_zero(self, sdk):
        result = sdk.compute_confidence()
        assert result["iterations"] == 0
        assert result["converged"] is True
        assert result["confidences"] == {}

    def test_converges_on_simple_chain(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        sdk.create_operator("IMPL", a["id"], [b["id"]])

        result = sdk.compute_confidence()
        assert result["iterations"] > 0
        assert result["converged"] is True
        assert a["id"] in result["confidences"]
        assert b["id"] in result["confidences"]

    def test_confidences_in_01(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        c = _make_claim(sdk, "C")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.create_operator("NAND", b["id"], [c["id"]])

        result = sdk.compute_confidence()
        for cid, conf in result["confidences"].items():
            assert 0 <= conf["mean"] <= 1, f"mean {conf['mean']} out of [0,1] for {cid}"

    def test_evidence_affects_confidence(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        sdk.create_operator("IMPL", a["id"], [b["id"]])

        # Strong evidence on A → B should inherit higher confidence
        sdk.set_point_baseline(a["id"], 10.0, 1.0)
        result = sdk.compute_confidence()
        assert result["confidences"][a["id"]]["mean"] > 0.8

    def test_with_explicit_factors(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])

        # Pass factors with the real operator ID
        result = sdk.compute_confidence(factors=[(op["id"], "IMPL", [a["id"], b["id"]], 1.0)])
        assert result["iterations"] > 0


# ── propagate_shock ──────────────────────────────────────────────────


