"""#2906 — the spend meter prices the provider's own charge, not a constant.

A single constant for the pinned model is wrong by construction: OpenRouter
serves ``deepseek/deepseek-v4-flash`` from ~11 upstreams spread over ~0.068–0.14
per 1M input, and nothing pins which one serves a call. The provider already
returns the authoritative figure in the response we parse (``usage.cost``);
the meter must prefer it, fall back to the declared basis only when it is
absent, and record which of the two it used.

The whole change turns on one distinction: ``None`` (the route reported no
charge → estimate) vs ``0.0`` (the route reported a free call → authoritative).
Every branch here uses ``is None``; truthiness would silently re-price a real
free call from a basis that is known to be wrong in both directions.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.config.prices import cost_usd
from battery.parity.mabench import CrItem
from battery.parity.mabench_run import run_cr_lane
from battery.runner.model_calls import (
    RealModelCaller,
    UsageRecordingCaller,
    aggregate_cost_basis,
)

#: A provider charge that is deliberately NOT what the token basis produces
#: for the same tokens (0.0001008), so "metered from the provider" and
#: "metered from the basis" can never be confused by a passing test.
PROVIDER_COST = 0.0009


class _ScriptedCaller:
    """Caller exposing the model_adapters usage contract with one provider
    cost per call (``None`` = the route reported none)."""

    model_id = "deepseek/deepseek-v4-flash"
    temperature = 0.0

    def __init__(self, costs, *, prompt_tokens: int = 1000,
                 completion_tokens: int = 100):
        self._costs = list(costs)
        self.last_cost_usd: float | None = None
        self.last_prompt_tokens = prompt_tokens
        self.last_completion_tokens = completion_tokens

    def call(self, *, prompt: str) -> str:
        self.last_cost_usd = self._costs.pop(0) if self._costs else None
        return "Answer: unknown"


class TestMeterPrefersProviderCost:
    def test_provider_cost_is_used_when_present(self):
        rec = UsageRecordingCaller(_ScriptedCaller([PROVIDER_COST]))
        rec.call(prompt="q")
        row = rec.rows[0]
        basis_cost = cost_usd(1000, 100)
        assert basis_cost != pytest.approx(PROVIDER_COST), (
            "the fixture must distinguish the provider charge from the basis")
        assert row.cost_usd == pytest.approx(PROVIDER_COST)
        assert row.basis == "provider_reported"
        assert rec.totals()["cost_basis"] == "provider_reported"
        assert rec.spent_usd == pytest.approx(PROVIDER_COST)

    def test_absent_provider_cost_falls_back_and_is_labelled(self):
        rec = UsageRecordingCaller(_ScriptedCaller([None]))
        rec.call(prompt="q")
        row = rec.rows[0]
        assert row.cost_usd == pytest.approx(cost_usd(1000, 100))
        assert row.basis == "estimated"
        assert rec.totals()["cost_basis"] == "estimated"

    def test_mixed_bases_produce_mixed_totals(self):
        rec = UsageRecordingCaller(_ScriptedCaller([PROVIDER_COST, None]))
        rec.call(prompt="one")
        rec.call(prompt="two")
        assert [r.basis for r in rec.rows] == ["provider_reported", "estimated"]
        assert rec.totals()["cost_basis"] == "mixed"
        assert rec.spent_usd == pytest.approx(
            PROVIDER_COST + cost_usd(1000, 100))
        assert abs(rec.totals()["cost_usd"] - rec.spent_usd) < 1e-6


class TestZeroIsAuthoritative:
    def test_zero_provider_cost_is_not_treated_as_absent(self):
        """A genuinely free call reports 0.0. Truthiness would read it as
        missing and re-price it from the (wrong-in-both-directions) basis."""
        rec = UsageRecordingCaller(_ScriptedCaller([0.0]))
        rec.call(prompt="q")
        row = rec.rows[0]
        assert row.cost_usd == 0.0
        assert row.basis == "provider_reported"
        assert rec.totals()["cost_basis"] == "provider_reported"
        # the basis would have charged something non-zero
        assert cost_usd(1000, 100) > 0.0

    def test_real_caller_mirrors_none_and_zero_distinctly(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-fake")
        caller = RealModelCaller()
        caller._real.last_cost_usd = 0.0
        assert caller.last_cost_usd == 0.0  # not None
        caller._real.last_cost_usd = None
        assert caller.last_cost_usd is None
        caller._real.last_cost_usd = 0.0009
        assert caller.last_cost_usd == pytest.approx(0.0009)


class TestNoNegativeCost:
    def test_more_cached_than_prompt_cannot_go_negative(self):
        """A cache-hit report can exceed the prompt count; the fallback
        estimator (the only place a negative charge could arise) clamps, and
        no metered row is ever negative."""
        assert cost_usd(10, 0, cached_tokens=999) >= 0.0
        caller = _ScriptedCaller([None], prompt_tokens=10, completion_tokens=0)
        rec = UsageRecordingCaller(
            caller,
            cost_fn=lambda pt, ct: cost_usd(pt, ct, cached_tokens=999))
        rec.call(prompt="q")
        assert rec.rows[0].cost_usd >= 0.0
        assert rec.rows[0].basis == "estimated"


class TestAdapterSurfacesUsageCost:
    def _model_with_response(self, monkeypatch, payload):
        from tortoise import model_adapters
        model = model_adapters.OpenRouterModel("deepseek/deepseek-v4-flash")

        class _Resp:
            def raise_for_status(self):
                return None

            def json(self):
                return payload

        monkeypatch.setattr(model._session, "post",
                            lambda *a, **k: _Resp())
        return model

    def test_usage_cost_is_surfaced(self, monkeypatch):
        model = self._model_with_response(monkeypatch, {
            "usage": {"prompt_tokens": 6371, "completion_tokens": 244,
                      "cost": 0.00089655},
            "choices": [{"message": {"content": "hi"},
                         "finish_reason": "stop"}],
        })
        assert model.complete(system="s", user="u") == "hi"
        assert model.last_cost_usd == pytest.approx(0.00089655)

    def test_missing_usage_cost_surfaces_none(self, monkeypatch):
        model = self._model_with_response(monkeypatch, {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "choices": [{"message": {"content": "hi"},
                         "finish_reason": "stop"}],
        })
        model.complete(system="s", user="u")
        assert model.last_cost_usd is None

    def test_cost_does_not_carry_over_to_a_later_call_without_it(
            self, monkeypatch):
        """A response without ``usage.cost`` must clear the field, not leave
        the previous call's charge in place."""
        from tortoise import model_adapters
        model = model_adapters.OpenRouterModel("deepseek/deepseek-v4-flash")
        payloads = [
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5,
                       "cost": PROVIDER_COST},
             "choices": [{"message": {"content": "a"},
                          "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 5},
             "choices": [{"message": {"content": "b"},
                          "finish_reason": "stop"}]},
        ]

        class _Resp:
            def __init__(self, payload):
                self._payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        seq = iter(payloads)
        monkeypatch.setattr(model._session, "post",
                            lambda *a, **k: _Resp(next(seq)))
        model.complete(system="s", user="u")
        assert model.last_cost_usd == pytest.approx(PROVIDER_COST)
        model.complete(system="s", user="u")
        assert model.last_cost_usd is None


class _AdapterStub:
    """Minimal serving adapter for the routing wrappers: reports one provider
    charge per call, like the real adapters now do."""

    def __init__(self, provider: str, *, cost: float | None):
        self.provider = provider
        self.id = "deepseek/deepseek-v4-flash"
        self.last_prompt_tokens = 10
        self.last_completion_tokens = 5
        self.last_cost_usd = cost

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        return "ok"


class TestRoutingForwardsProviderCost:
    """The routing wrappers forward the usage contract, so the provider
    charge must survive them (else every routed call silently estimates)."""

    def test_routing_model_forwards_last_cost_usd(self):
        from tortoise.model_adapters import RoutingModel
        primary = _AdapterStub("routing-primary", cost=PROVIDER_COST)
        model = RoutingModel(primary, fallback=None, cooldown_s=0)
        assert model.last_cost_usd is None
        model.complete(system="s", user="u")
        assert model.last_cost_usd == pytest.approx(PROVIDER_COST)

    def test_rotating_model_forwards_last_cost_usd(self):
        from tortoise.model_adapters import RotatingModel
        provider = _AdapterStub("rotating-a", cost=PROVIDER_COST)
        pool = RotatingModel([provider], cooldown_s=0)
        assert pool.last_cost_usd is None
        pool.complete(system="s", user="u")
        assert pool.last_cost_usd == pytest.approx(PROVIDER_COST)


class TestParityCrLaneCarriesBasis:
    ITEMS = (
        CrItem(qa_pair_id="q1", config="c", question="Who chairs Fatah?",
               accepted=("Mahmoud Abbas",)),
    )

    def test_cell_detail_has_cost_basis_key(self):
        caller = _ScriptedCaller([PROVIDER_COST])
        cell, run = run_cr_lane(self.ITEMS, caller, lane="mock", config="c",
                                context="facts")
        assert "cost_basis" in cell.detail
        assert cell.detail["cost_basis"] == "provider_reported"
        assert run.cost_basis == "provider_reported"

    def test_fallback_lane_detail_says_estimated(self):
        caller = _ScriptedCaller([None])
        cell, _ = run_cr_lane(self.ITEMS, caller, lane="mock", config="c",
                              context="facts")
        assert cell.detail["cost_basis"] == "estimated"


class TestAggregateBasis:
    def test_empty_is_estimated(self):
        assert aggregate_cost_basis([]) == "estimated"

    def test_unknown_labels_do_not_claim_provider_reported(self):
        assert aggregate_cost_basis([None, "estimated"]) == "estimated"

    def test_all_provider_reported(self):
        assert aggregate_cost_basis(
            ["provider_reported", "provider_reported"]) == "provider_reported"

    def test_both_bases_is_mixed(self):
        assert aggregate_cost_basis(
            ["provider_reported", "estimated"]) == "mixed"

    def test_an_already_mixed_label_stays_mixed(self):
        # episode-level labels are themselves aggregates; folding a "mixed"
        # episode with a provider-reported one must not collapse to
        # provider_reported.
        assert aggregate_cost_basis(
            ["mixed", "provider_reported"]) == "mixed"
        assert aggregate_cost_basis(["mixed"]) == "mixed"


class TestRunSummaryLabelsBasis:
    """Requirement 3: a persisted real spend figure names its derivation."""

    def test_real_arm_block_carries_cost_basis(self):
        from battery.runner.run import _arm_summary_block
        block = _arm_summary_block(
            "a0", arm_present=True, run_mode="real", spend_usd=0.012,
            cost_basis="provider_reported")
        assert block["cost_basis"] == "provider_reported"
        assert block["real_spend_usd"] == pytest.approx(0.012)

    def test_real_arm_without_a_basis_is_estimated_not_null(self):
        from battery.runner.run import _arm_summary_block
        # an arm that failed at init still declares a real 0.0 figure
        block = _arm_summary_block("a0", arm_present=False, run_mode="real")
        assert block["real_spend_usd"] == 0.0
        assert block["cost_basis"] == "estimated"

    def test_mock_arm_has_no_basis(self):
        from battery.runner.run import _arm_summary_block
        block = _arm_summary_block("a0", arm_present=True, run_mode="mock")
        assert block["real_spend_usd"] is None
        assert block["cost_basis"] is None

    def test_arm_basis_folds_episode_labels(self):
        from battery.runner.run import _arm_cost_basis

        class _Ep:
            def __init__(self, basis):
                self.ep_surface = {"usage": {"cost_basis": basis}}

        assert _arm_cost_basis([]) == "estimated"
        assert _arm_cost_basis([_Ep("provider_reported")]) == \
            "provider_reported"
        assert _arm_cost_basis(
            [_Ep("provider_reported"), _Ep("estimated")]) == "mixed"
        assert _arm_cost_basis([_Ep(None)]) == "estimated"


class TestJudgeCostIsNotRepriced:
    """#2906 — the same bug class in the judge client: a provider-reported cost
    of exactly 0.0 is a VALUE (a free/zero-priced call), not an absence. The
    previous `usage.get("cost", 0.0) or fallback(...)` silently re-priced it.

    ``_real_call`` is the metering seam against the reserve HARD STOP, so this
    is a spend-path test, not a cosmetic one.
    """

    @staticmethod
    def _call_with(monkeypatch, usage, *, fallback_cost):
        import io
        import json as _json

        import battery.judge.client as client

        calls = {"fallback": 0}

        def _spy(model, pt, ct):
            calls["fallback"] += 1
            return fallback_cost

        monkeypatch.setattr(client, "_openrouter_cost", _spy)
        body = _json.dumps({
            "choices": [{"message": {"content": '{"verdict": "ok"}'}}],
            "usage": usage,
            "model": "deepseek/deepseek-v4-flash",
        }).encode()

        class _Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        monkeypatch.setattr("urllib.request.urlopen",
                            lambda *a, **k: _Resp(body))
        jc = client.JudgeClient(model_id="deepseek/deepseek-v4-flash")
        out = jc._real_call("prompt", 0.0)
        return out, calls

    def test_zero_reported_cost_is_honoured(self, monkeypatch):
        out, calls = self._call_with(
            monkeypatch,
            {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.0},
            fallback_cost=99.0)
        assert out["cost_usd"] == 0.0
        assert calls["fallback"] == 0, "a reported 0.0 must not be re-priced"

    def test_absent_cost_still_falls_back(self, monkeypatch):
        out, calls = self._call_with(
            monkeypatch, {"prompt_tokens": 10, "completion_tokens": 20},
            fallback_cost=0.5)
        assert out["cost_usd"] == 0.5
        assert calls["fallback"] == 1

    def test_reported_nonzero_cost_beats_fallback(self, monkeypatch):
        out, calls = self._call_with(
            monkeypatch,
            {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.123},
            fallback_cost=99.0)
        assert out["cost_usd"] == 0.123
        assert calls["fallback"] == 0


class TestPersistedSpendAlwaysCarriesItsBasis:
    """#2906 (review #2915 P2) — two spend figures that were persisted without
    a provenance label. The invariant is: a persisted number never implies it
    was provider-priced when it was not."""

    def test_judge_meter_report_carries_basis(self):
        from battery.judge.evidence import _SpendMeter, meter_report

        def _call(cost, basis):
            from battery.judge.client import JudgeCall
            return JudgeCall(rubric_id="r", item_id="i", verdict="ok",
                             confidence=1.0, cost_usd=cost, cost_basis=basis)

        m = _SpendMeter()
        # a genuinely free provider-priced call is still provider_reported
        m.record(_call(0.0, "provider_reported"))
        m.record(_call(0.5, "estimated"))
        rep = meter_report(m)
        assert rep["cost_basis"] == "mixed"

        m2 = _SpendMeter()
        m2.record(_call(0.1, "provider_reported"))
        assert meter_report(m2)["cost_basis"] == "provider_reported"

        # zero priced calls must NOT claim provenance
        assert meter_report(_SpendMeter())["cost_basis"] == "estimated"

    def test_judge_call_defaults_to_estimated_not_provider(self):
        from battery.judge.client import JudgeCall
        jc = JudgeCall(rubric_id="r", item_id="i", verdict="ok", confidence=1.0)
        assert jc.cost_basis == "estimated", "provenance is never assumed"

    def test_probe_spend_basis_rule(self):
        """The manifest's label comes from this rule, so test the rule
        directly rather than through a full probe run (deterministic)."""
        from battery.probes.probe_runner import _spend_cost_basis

        # nothing priced => never claims provenance
        assert _spend_cost_basis(0, 0) == "estimated"
        assert _spend_cost_basis(3, 0) == "provider_reported"
        assert _spend_cost_basis(0, 3) == "estimated"
        assert _spend_cost_basis(2, 1) == "mixed"

    def test_probe_manifest_uses_the_rule(self):
        """The manifest must consume the rule, not re-derive it inline."""
        import inspect

        from battery.probes import probe_runner

        src = inspect.getsource(probe_runner.run_probe)
        assert "_spend_cost_basis(" in src
        assert '"cost_basis"' in src
