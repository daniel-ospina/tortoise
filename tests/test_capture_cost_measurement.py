"""#3359 — per-session provider cost measurement (calibration data only).

The measurement rides the REAL code path, so these tests assert on what the
production functions actually produce rather than on a mock of them:

    _call_once  (in-thread capture of last_cost_usd + tokens + serving route)
      -> _complete  (per-call cost accumulator on the stage's stats dict)
        -> _rollup_llm  (per-stage tokens / cost / by_stage roll-up)
          -> capture_cost row  (hosted capture, allowlist-filtered)
            -> cost_per_session  (report-time, versioned pricing map)

Nothing here touches the billing path: the customer-visible unit stays
``write_ops`` and no cap / kill-switch is introduced (the owner's ruling is
"measure, do not cap").
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tests.test_extractor_reliability import _conv  # noqa: E402
from tools.longmem_eval import costing  # noqa: E402
from tortoise import extractor_v2 as v2  # noqa: E402

_PROVIDER = "openrouter"
_MODEL = "deepseek/deepseek-v4-flash"


class CostReportingModel:
    """A real extractor stub that reports provider usage on every call.

    Sets the same adapter attributes the production adapters set
    (``tortoise/model_adapters.py:166-170``) so ``_call_once``'s in-thread
    capture is exercised, not bypassed.
    """

    provider = _PROVIDER
    id = _MODEL
    last_finish_reason = "stop"

    def __init__(self, prompt_tokens: int = 100, completion_tokens: int = 10,
                 cost_usd: float | None = 0.001):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cost_usd = cost_usd
        self.calls: list[str] = []

    def complete(self, *, system: str, user: str, max_tokens=None):
        self.last_prompt_tokens = self.prompt_tokens
        self.last_completion_tokens = self.completion_tokens
        self.last_cost_usd = self.cost_usd
        self.calls.append(system[:40])
        if "STORY SUMMARIZER" in system:
            return "A narrative."
        if "GAP REVIEWER" in system:
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "s4 point", '
                    '"pointKind": "statement"}]}')
        # S2 (GRAPH MAPPER)
        return ('{"entities": [], "events": [], "operators": [], '
                '"points": [{"content": "s2 point", '
                '"pointKind": "statement"}]}')


# ── the real end-to-end extraction path ────────────────────────────────────

def test_extract_session_v2_rolls_provider_cost_and_tokens_per_stage():
    """THE acceptance test: a full ``extract_session_v2`` run with a model
    that reports its provider charge must surface that charge, the tokens,
    and the serving route split by stage on ``stats["llm"]``.

    Before the fix this fails with ``KeyError: 'cost_usd'`` — the tokens were
    computed per call then discarded and ``last_cost_usd`` was never read."""
    model = CostReportingModel()
    out = v2.extract_session_v2(model, _conv())
    llm = out["stats"]["llm"]

    # one S1 call, one S2 call, one S4 call — each reporting $0.001
    assert llm["calls"] == 3
    assert llm["cost_usd"] == pytest.approx(0.003, abs=1e-9)
    assert llm["prompt_tokens"] == 300
    assert llm["completion_tokens"] == 30
    # every call reported a cost — nothing to disclose
    assert llm["calls_without_cost"] == 0

    assert set(llm["by_stage"]) == {"s1", "s2", "s4"}
    s1 = llm["by_stage"]["s1"][_PROVIDER][_MODEL]
    assert s1["calls"] == 1
    assert s1["prompt_tokens"] == 100
    assert s1["completion_tokens"] == 10
    assert s1["cost_usd"] == pytest.approx(0.001, abs=1e-9)
    # the pricing-envelope contract the report-time repricer consumes
    assert s1["usage_present"] is True
    assert s1["calls_without_usage"] == 0


def test_extract_session_v2_discloses_calls_without_provider_cost():
    """A provider that reports NO charge (``last_cost_usd is None`` — the
    deepseek-direct route today) must be disclosed via
    ``calls_without_cost``, never silently priced at $0."""
    model = CostReportingModel(cost_usd=None)
    out = v2.extract_session_v2(model, _conv())
    llm = out["stats"]["llm"]

    assert llm["cost_usd"] == 0.0            # nothing was reported
    assert llm["calls_without_cost"] == 3    # and that is said out loud
    assert llm["prompt_tokens"] == 300       # tokens still measured (repriceable)
    # usage IS present (tokens arrived) — only the CHARGE is missing, so the
    # lane stays priceable from the versioned map at report time.
    lane = llm["by_stage"]["s1"][_PROVIDER][_MODEL]
    assert lane["usage_present"] is True
    assert lane["cost_usd"] == 0.0
    assert lane["calls_without_cost"] == 1


def test_rollup_llm_merges_multiple_chunks_into_one_stage_bucket():
    """The S1 stage runs once per chunk: two chunks must merge into ONE
    per-stage bucket (not overwrite), and the session totals must sum."""
    first: dict = {}
    second: dict = {}
    v2._accumulate_call_cost(first, prompt_tokens=100, completion_tokens=10,
                             cost_usd=0.001, provider=_PROVIDER, model=_MODEL)
    v2._accumulate_call_cost(second, prompt_tokens=200, completion_tokens=20,
                             cost_usd=0.002, provider=_PROVIDER, model=_MODEL)
    llm: dict = {"calls": 0, "retries": 0, "truncated": 0,
                 "deadline_aborts": 0}  # the caller-owned roll-up dict
    v2._rollup_llm(llm, first, "s1")
    v2._rollup_llm(llm, second, "s1")

    assert llm["prompt_tokens"] == 300
    assert llm["completion_tokens"] == 30
    assert llm["cost_usd"] == pytest.approx(0.003, abs=1e-9)
    s1 = llm["by_stage"]["s1"][_PROVIDER][_MODEL]
    assert s1["calls"] == 2
    assert s1["prompt_tokens"] == 300
    assert s1["cost_usd"] == pytest.approx(0.003, abs=1e-9)


def test_escalation_accumulates_both_calls_not_just_the_last():
    """A length-truncated S1 call escalates into a SECOND provider call
    (#2134). Both are billed, so the measurement must sum them — a per-call
    snapshot would report only the escalated call and silently drop the base
    call's spend, systematically under-counting exactly the long sessions
    the p95 read is about."""
    class Escalating(CostReportingModel):
        def complete(self, *, system: str, user: str, max_tokens=None):
            self._n = getattr(self, "_n", 0) + 1
            self.last_prompt_tokens = self.prompt_tokens
            self.last_completion_tokens = self.completion_tokens
            self.last_cost_usd = self.cost_usd
            if self._n == 1:
                self.last_finish_reason = "length"
                return "A truncated narrative."
            self.last_finish_reason = "stop"
            return "A complete narrative."

    stats: dict = {}
    v2.run_s1(Escalating(), "CONVERSATION", stats=stats)

    # the base call (length-truncated) AND the escalated call were billed
    assert stats["cost"]["calls"] == 2
    assert stats["cost"]["prompt_tokens"] == 200
    assert stats["cost"]["completion_tokens"] == 20
    assert stats["cost"]["cost_usd"] == pytest.approx(0.002, abs=1e-9)

    llm: dict = {"calls": 0, "retries": 0, "truncated": 0,
                 "deadline_aborts": 0}
    v2._rollup_llm(llm, stats, "s1")
    assert llm["prompt_tokens"] == 200
    assert llm["cost_usd"] == pytest.approx(0.002, abs=1e-9)
    assert llm["by_stage"]["s1"][_PROVIDER][_MODEL]["calls"] == 2


def test_call_once_attributes_cost_to_the_serving_route_on_failover():
    """A ``RoutingModel`` keeps ``provider`` = the configured primary and
    flips ``last_route`` when it fails over. The measurement must charge the
    lane that actually served the call — reading ``provider`` would bill the
    fallback's charge to the primary's rate (and misprice it at report
    time, or drop it as unpriced)."""
    from tortoise.model_adapters import RoutingModel

    class Adapter:
        def __init__(self, provider, model_id):
            self.provider = provider
            self.id = model_id
            self.last_finish_reason = "stop"
            self.last_prompt_tokens = 100
            self.last_completion_tokens = 10
            self.last_cost_usd = 0.001

        def complete(self, *, system: str, user: str, max_tokens=None):
            return "ok"

    class Boom(Adapter):
        def complete(self, *, system: str, user: str, max_tokens=None):
            raise RuntimeError("transient upstream")  # no .response → failover

    primary = Boom("primary-test-lane", _MODEL)
    fallback = Adapter("fallback-test-lane", "deepseek-v4-flash")
    model = RoutingModel(primary, fallback)

    stats: dict = {}
    v2._call_once(model, "s", "u", deadline_s=5, max_tokens=None, stats=stats)

    assert stats["cost_provider"] == "fallback-test-lane"   # the SERVING lane
    assert stats["cost_model"] == "deepseek-v4-flash"
    assert model.provider == "primary-test-lane"             # configured primary
    assert model.last_route == "fallback-test-lane"


def test_rollup_honors_disclosed_calls_without_provider_cost():
    """A stage whose provider reported NO charge must disclose every such
    call, never silently price it at $0 — including the kind_classifier's
    adjudication batches (which merge their accumulator into the session
    roll-up). The disclosure must survive the merge, and a second batch on a
    DIFFERENT route must keep its own lane."""
    batch1: dict = {}
    v2._accumulate_call_cost(batch1, prompt_tokens=100, completion_tokens=10,
                             cost_usd=None, provider="deepseek",
                             model="deepseek-v4-flash")
    v2._accumulate_call_cost(batch1, prompt_tokens=50, completion_tokens=5,
                             cost_usd=None, provider="deepseek",
                             model="deepseek-v4-flash")
    batch2: dict = {}
    v2._accumulate_call_cost(batch2, prompt_tokens=25, completion_tokens=2,
                             cost_usd=0.004, provider=_PROVIDER, model=_MODEL)

    usage: dict = {}                       # the kind_classifier's `usage`
    v2._merge_cost_accumulator(usage, batch1)
    v2._merge_cost_accumulator(usage, batch2)

    llm: dict = {"calls": 0, "retries": 0, "truncated": 0,
                 "deadline_aborts": 0}
    v2._rollup_llm(llm, usage, "classify")
    assert llm["calls_without_cost"] == 2          # both uncharged calls said
    assert llm["prompt_tokens"] == 175
    assert llm["cost_usd"] == pytest.approx(0.004, abs=1e-9)
    assert set(llm["by_stage"]["classify"]) == {"deepseek", _PROVIDER}
    silent = llm["by_stage"]["classify"]["deepseek"]["deepseek-v4-flash"]
    assert silent["cost_usd"] == 0.0
    assert silent["calls_without_cost"] == 2
    assert silent["usage_present"] is True         # tokens present → priceable


# ── the hosted row (allowlist + writer) ────────────────────────────────────

def test_capture_cost_props_are_allowlisted_and_emitted(tmp_path, monkeypatch):
    """The hosted lane builds a PII-filtered ``capture_cost`` row and the
    real writer emits it. Before the fix the allowlist strips every new key
    (``properties`` comes out empty) — a silent measurement loss."""
    from tortoise import hosted_api as ha

    meta = {"stats": {"llm": {
        "calls": 3, "retries": 0, "truncated": 0,
        "prompt_tokens": 300, "completion_tokens": 30,
        "cost_usd": 0.003, "calls_without_cost": 0,
        "by_stage": {
            "s1": {_PROVIDER: {_MODEL: {
                "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                "cost_usd": 0.001, "usage_present": True,
                "calls_without_usage": 0, "calls_without_cost": 0}}},
            "s2": {_PROVIDER: {_MODEL: {
                "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                "cost_usd": 0.001, "usage_present": True,
                "calls_without_usage": 0, "calls_without_cost": 0}}},
            "s4": {_PROVIDER: {_MODEL: {
                "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                "cost_usd": 0.001, "usage_present": True,
                "calls_without_usage": 0, "calls_without_cost": 0}}},
        },
    }}}

    props = ha._capture_cost_props("sess-1", meta)
    assert props is not None
    assert props["session_id"] == "sess-1"
    assert props["cost_usd"] == pytest.approx(0.003, abs=1e-9)
    assert props["prompt_tokens"] == 300
    # the allowlist must not strip a single measured field
    assert set(props) <= ha._ALLOWED_ANALYTICS_PROPS

    # emit through the REAL writer (Supabase unset → JSONL fallback)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics.jsonl"))
    ha._track_analytics_event("team-1", "capture_cost", props)
    rec = json.loads((tmp_path / "analytics.jsonl").read_text()
                     .strip().splitlines()[-1])
    assert rec["event_name"] == "capture_cost"
    assert rec["properties"]["cost_usd"] == pytest.approx(0.003, abs=1e-9)
    assert rec["properties"]["by_stage"]["s1"][_PROVIDER][_MODEL][
        "prompt_tokens"] == 100


def test_capture_cost_props_none_without_llm_telemetry():
    """A replayed / M2 capture has no extractor telemetry — no row, no
    phantom measurement."""
    from tortoise import hosted_api as ha
    assert ha._capture_cost_props("sess-1", {"stats": {}}) is None
    assert ha._capture_cost_props("sess-1", {}) is None


# ── report-time: the threshold is now computable from the real row ─────────

_THRESHOLDS = _REPO_ROOT / "tests" / "extraction_eval" / "thresholds.yaml"


def _row(session_id: str, cost_usd: float, by_stage: dict) -> dict:
    return {"team_id": "team-1", "event_name": "capture_cost",
            "properties": {"session_id": session_id, "calls": 1,
                           "prompt_tokens": 100, "completion_tokens": 10,
                           "cost_usd": cost_usd, "calls_without_cost": 0,
                           "by_stage": by_stage}}


def test_cost_per_session_computable_from_capture_cost_rows():
    """``cost_per_session`` (thresholds.yaml A18) now has a producer: the
    capture_cost row distribution. Asserts the metric is derived from the
    real row shape and is comparable to the declared threshold."""
    rows = [
        _row("s1", 0.001, {"s1": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
            "cost_usd": 0.001, "usage_present": True,
            "calls_without_usage": 0}}}}),
        _row("s2", 0.002, {"s1": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
            "cost_usd": 0.002, "usage_present": True,
            "calls_without_usage": 0}}}}),
    ]
    dist = costing.cost_per_session_distribution(rows)
    assert dist["n"] == 2
    assert dist["p50"] == pytest.approx(0.0015, abs=1e-9)
    assert dist["p95"] == pytest.approx(0.00195, abs=1e-9)  # linear interp
    assert dist["max"] == pytest.approx(0.002, abs=1e-9)
    assert dist["unpriced_sessions"] == 0

    standards = yaml.safe_load(_THRESHOLDS.read_text())["standards"]
    assert "cost_per_session" in standards          # A18 — the row exists
    assert dist["p50"] <= standards["cost_per_session"]["alert"]


def test_cost_per_session_reprices_from_map_when_provider_is_silent():
    """A row whose calls reported no cost is repriced at report time from the
    versioned map over raw tokens — the million input tokens below are
    $0.14 at the openrouter v4-flash rate, never silently $0."""
    by_stage = {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0,
        "cost_usd": 0.0, "usage_present": True, "calls_without_usage": 0}}}}
    row = {"properties": {"session_id": "silent", "calls": 1,
                          "prompt_tokens": 1_000_000, "completion_tokens": 0,
                          "cost_usd": 0.0, "calls_without_cost": 1,
                          "by_stage": by_stage}}
    dist = costing.cost_per_session_distribution([row])
    assert dist["p50"] == pytest.approx(0.14, abs=1e-6)
    assert dist["unpriced_sessions"] == 0           # the map DID price it
    assert dist["source"] == "map"                  # and it is disclosed


def test_measurement_is_emitted_and_repriced_end_to_end():
    """The whole chain on the real code path, with no mock at any seam:
    ``extract_session_v2`` (provider reports NO charge — deepseek-direct
    today) → ``_rollup_llm`` → ``_capture_cost_props`` → report-time
    ``cost_per_session_distribution`` repricing from raw tokens via the
    versioned map. This is the acceptance evidence: a per-session COST that
    actually exists, not an estimate in a draft doc."""
    from tortoise import hosted_api as ha

    out = v2.extract_session_v2(CostReportingModel(cost_usd=None), _conv())
    props = ha._capture_cost_props("sess-e2e", {"stats": out["stats"]})
    assert props is not None
    assert props["calls_without_cost"] == 3

    dist = costing.cost_per_session_distribution([{"properties": props}])
    assert dist["n"] == 1
    assert dist["unpriced_sessions"] == 0
    assert dist["source"] == "map"

    rate = costing.PRICING_MAP[_PROVIDER][_MODEL]
    per_lane = round(
        (100 * rate["prompt_per_1m"] + 10 * rate["completion_per_1m"]) / 1e6,
        6)
    assert dist["p50"] == pytest.approx(round(3 * per_lane, 6), abs=1e-9)


def test_thresholds_file_metric_now_has_a_producer():
    """Guard against the original defect: A18 was asserted but nothing
    computed it. The computation now exists and is importable."""
    assert callable(costing.cost_per_session_distribution)
    assert costing.PRICING_MAP_VERSION        # versioned, repricable at read time
