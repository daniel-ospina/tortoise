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
    return sdk.create_point("statement", content, status="live")  # #780 default excludes drafts


# ── set_prior / get_confidence ────────────────────────────────────


class TestEvidence:
    def test_set_returns_data(self, sdk):
        result = sdk.set_point_baseline("claim-1", 5.0, 2.0)
        # #398/#2199: return includes baseline provenance (source defaults to
        # the #2199 token family's author-set value, renamed from 'explicit').
        assert result == {"claim_id": "claim-1", "alpha": 5.0, "beta": 2.0,
                          "source": "set-by-author"}

    def test_get_confidence_default(self, sdk):
        p = _make_claim(sdk, "some claim")
        # #1157: live-uncalibrated claim trips the fail-closed gate; this
        # asserts the read surface's return contract, not the gate — opt out.
        c = sdk.get_confidence(p["id"], require_calibration=False)
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
        # #344: neutral Beta(1,1) baselines keep the fail-closed EP gate active
        # (uncalibrated points previously carried the same prior silently).
        sdk.set_point_baseline(a["id"], 1, 1)
        sdk.set_point_baseline(b["id"], 1, 1)
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
        for cid in (a["id"], b["id"], c["id"]):
            sdk.set_point_baseline(cid, 1, 1)  # #344: neutral calibrated baselines
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
        sdk.set_point_baseline(b["id"], 1, 1)  # #344: neutral baseline (gate active)
        result = sdk.compute_confidence()
        assert result["confidences"][a["id"]]["mean"] > 0.8

    def test_with_explicit_factors(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        sdk.set_point_baseline(a["id"], 1, 1)  # #344: neutral calibrated baselines
        sdk.set_point_baseline(b["id"], 1, 1)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])

        # Pass factors with the real operator ID
        result = sdk.compute_confidence(factors=[(op["id"], "IMPL", [a["id"], b["id"]], 1.0)])
        assert result["iterations"] > 0


# ── propagate_shock ──────────────────────────────────────────────────




# ── #330: per-run evidence + cache lifecycle ─────────────────────────────


class TestEvidenceLeak:
    """#330: run()-level evidence must be call-scoped — never mutate the
    instance's _evidence dict, never re-apply stale run evidence later."""

    def test_run_evidence_does_not_leak_to_next_run(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        ep = sdk._get_ep()
        ep.run([op["id"]], evidence={b["id"]: (10.0, 1.0)})

        # Reset b's graph params to uniform, then run again WITHOUT evidence.
        # A leak would re-write the (10.0, 1.0) prior onto the graph.
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=1.0, n.ep_beta=1.0",
            params={"id": b["id"]},
        )
        ep.run([op["id"]])
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.ep_alpha, n.ep_beta",
            params={"id": b["id"]},
        ).result_set[0]
        # The EP run recomputes b's posterior from its prior, so a leaked
        # prior would land at the evidence fixed point (~10.5) while a clean
        # run without evidence sits near uniform (< 5). Assert the split, not
        # an exact sentinel.
        assert row[0] < 5.0, (
            f"run-level evidence leaked into the next run: ep_alpha={row[0]:.4f} "
            f"(evidence fixed point ~10.5)"
        )
        assert b["id"] not in ep._evidence, "run-level evidence mutated the instance _evidence dict"

    def test_run_evidence_overrides_constructor_in_posterior(self, sdk):
        from tortoise.ep import TortoiseEP
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        proj = sdk._get_proj()
        ep = TortoiseEP(proj, evidence={b["id"]: (3.0, 1.0)})
        # Run-level evidence must override the constructor evidence for this
        # run — including in the real posterior computation (with operators),
        # not just the zero-operator graph pre-write.
        ep.run([op["id"]], evidence={b["id"]: (1.0, 3.0)})
        conf = ep.compute_confidence(b["id"])
        assert conf["mean"] < 0.5, (
            f"run evidence (1,3) should override constructor (3,1); "
            f"got mean {conf['mean']}"
        )
        # Constructor dict must be untouched by run()
        assert ep._evidence[b["id"]] == (3.0, 1.0)

    def test_constructor_evidence_applies_every_run(self, sdk):
        from tortoise.ep import TortoiseEP
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        proj = sdk._get_proj()
        ep = TortoiseEP(proj, evidence={b["id"]: (10.0, 1.0)})
        # Constructor evidence is persistent: applied on EVERY run, even with
        # no run-level evidence. The run recomputes b's posterior FROM that
        # prior (strong evidence => high mean), deterministically each run.
        ep.run([op["id"]])
        first = ep.compute_confidence(b["id"])
        assert first["mean"] > 0.8, (
            f"constructor evidence (10,1) should drive b's posterior high; "
            f"got mean {first['mean']}"
        )
        ep.run([op["id"]])  # second run, no run-level evidence
        second = ep.compute_confidence(b["id"])
        assert second["mean"] > 0.8
        assert abs(first["alpha"] - second["alpha"]) < 1e-3 and abs(first["beta"] - second["beta"]) < 1e-3, (
            f"constructor evidence not re-applied deterministically: "
            f"run1=({first['alpha']:.4f},{first['beta']:.4f}) run2=({second['alpha']:.4f},{second['beta']:.4f})"
        )
        # Run-level evidence for a DIFFERENT claim must not disturb constructor
        # evidence for b.
        c = _make_claim(sdk, "C")
        ep.run([op["id"]], evidence={c["id"]: (2.0, 8.0)})
        third = ep.compute_confidence(b["id"])
        assert abs(third["alpha"] - second["alpha"]) < 1e-3 and abs(third["beta"] - second["beta"]) < 1e-3, (
            f"unrelated run evidence disturbed b's constructor-evidence posterior"  # noqa: F541
        )


class TestCacheFreshness:
    """#330: EP caches are a per-run working set — public reads must never
    serve stale in-memory values after a run (including early returns)."""

    def test_early_return_clears_stale_cache(self, sdk):
        a = _make_claim(sdk, "A")
        b = _make_claim(sdk, "B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        ep = sdk._get_ep()
        ep.run([op["id"]], evidence={b["id"]: (10.0, 1.0)})

        # External write changes the graph AFTER the run. Under the #852
        # posterior-first contract, consumers read
        # coalesce(posterior_*, ep_*, 1.0) — a param change must clear the
        # stale posterior too (mirrors the evidence pre-write in run() and
        # set_point_baseline's baseline-change clearing).
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=4.0, n.ep_beta=4.0, "
            "n.posterior_alpha=null, n.posterior_beta=null",
            params={"id": b["id"]},
        )
        # A run that early-returns (no affected claims) must NOT serve the
        # stale cache from the previous run.
        ep.run(["does-not-exist-op"])
        conf = ep.compute_confidence(b["id"])
        assert conf["alpha"] == 4.0 and conf["beta"] == 4.0, (
            f"stale cache served: got ({conf['alpha']}, {conf['beta']}), "
            f"expected fresh graph (4.0, 4.0)"
        )


# ── #330: zero-division guards ────────────────────────────────────────────


class TestZeroTotalGuard:
    """#330: a/(a+b) must not raise ZeroDivisionError when stored params are
    (0,0) (reachable via set_point_baseline(id, 0, 0) or raw graph writes)."""

    def test_compute_confidence_zero_total_does_not_crash(self, sdk):
        # Isolated claim with NO operators (dream() early-returns; the guard
        # fires on the raw stored (0,0)).
        p = _make_claim(sdk, "degenerate-claim")
        sdk.set_point_baseline(p["id"], 0.0, 0.0)
        c = sdk.get_confidence(p["id"])   # was ZeroDivisionError
        assert c["mean"] == 0.5           # uniform fallback
        assert abs(c["variance"] - 1/12) < 1e-9  # Beta(1,1) uniform variance
        assert c["effective_n"] == 0
        assert c["alpha"] == 0.0 and c["beta"] == 0.0

    def test_is_strong_zero_total_no_crash(self, sdk):
        from tortoise.ep import TortoiseEP
        p = _make_claim(sdk, "strong-check")
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=0.0, n.ep_beta=0.0",
            params={"id": p["id"]},
        )
        ep = TortoiseEP(proj)
        assert ep._is_strong(p["id"]) is False   # was ZeroDivisionError

    def test_flush_cache_zero_total_no_crash(self, sdk):
        from tortoise.ep import TortoiseEP
        p = _make_claim(sdk, "flush-check")
        proj = sdk._get_proj()
        ep = TortoiseEP(proj)
        ep._node_cache = {p["id"]: (0.0, 0.0)}
        ep._flush_cache()   # was ZeroDivisionError in the mean calc
        row = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.confidence",
            params={"id": p["id"]},
        ).result_set[0]
        assert row[0] == 0.5

    def test_zero_total_operator_chain_end_to_end(self, sdk):
        # #330: a (0,0)-params claim CONNECTED to an operator exercises every
        # guarded division site through the user-visible path (dream + run +
        # compute_confidence) without crashing.
        a = _make_claim(sdk, "A-zero")
        b = _make_claim(sdk, "B-zero")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        proj = sdk._get_proj()
        for cid in (a["id"], b["id"]):
            # #344: baseline first (keeps the gate active), then the (0,0)
            # params the test exercises below.
            sdk.set_point_baseline(cid, 1, 1)
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.ep_alpha=0.0, n.ep_beta=0.0",
                params={"id": cid},
            )
        result = sdk.compute_confidence()
        assert result["iterations"] >= 0
        for cid, conf in result["confidences"].items():  # noqa: B007
            assert 0 <= conf["mean"] <= 1, f"mean {conf['mean']} out of range"
