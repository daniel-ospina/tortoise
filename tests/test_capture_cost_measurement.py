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
import math
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
    (_resp, _finish, _ptoks, _ctoks,
     cost_usd, cost_provider, cost_model) = v2._call_once(
        model, "s", "u", deadline_s=5, max_tokens=None, stats=stats)

    assert cost_provider == "fallback-test-lane"   # the SERVING lane
    assert cost_model == "deepseek-v4-flash"
    assert cost_usd == pytest.approx(0.001, abs=1e-9)
    assert model.provider == "primary-test-lane"             # configured primary
    assert model.last_route == "fallback-test-lane"
    # the cost driver rides the RETURN TUPLE, never the shared stats dict
    # (the deadline-abort counter in that dict is lock-guarded precisely
    # because it may be shared — a pop-based hand-off would race).
    assert "cost_provider" not in stats


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


# ── per-session aggregation + usage-less calls (review P1/P2) ────────────

def test_usage_less_calls_do_not_enter_the_distribution():
    """A lane whose calls returned NO usage block AND no charge is not a
    measurement. ``_measured_calls`` must count only calls that produced
    something priceable, otherwise this row enters the percentile
    population as a $0 and halves p50 (the review's P1).
    """
    usage_less = {"properties": {
        "session_id": "no-usage", "calls": 1, "cost_usd": 0.0,
        "calls_without_cost": 1, "calls_without_usage": 1,
        "deadline_aborts": 0,
        "by_stage": {"s2": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "usage_present": False,
            "calls_without_usage": 1, "calls_without_cost": 1}}}}}}
    real = _row("real", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})

    dist = costing.cost_per_session_distribution([usage_less, real])
    assert dist["n"] == 1
    assert dist["p50"] == pytest.approx(0.004, abs=1e-9)   # NOT halved
    assert dist["excluded_unmeasured"] == 1
    assert dist["calls_without_usage"] == 1


def test_rows_for_the_same_session_aggregate_into_one_sample():
    """A failed capture that is retried (#2335 WI-2b) writes a SECOND row
    for the SAME session — the retry's spend only. Percentiling rows would
    split one session's true cost across two samples and understate it for
    exactly the long/flaky sessions the p95 read is about (review P2).
    """
    def stage(cost):
        return {"s1": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
            "cost_usd": cost, "usage_present": True,
            "calls_without_usage": 0}}}}

    rows = [
        _row("sess-retried", 0.002, stage(0.002)),   # first attempt
        _row("sess-retried", 0.003, stage(0.003)),   # retry, same session
        _row("sess-other", 0.010, stage(0.010)),
    ]
    dist = costing.cost_per_session_distribution(rows)

    assert dist["n_rows"] == 3
    assert dist["n"] == 2                       # two SESSIONS, not three rows
    assert dist["total_usd"] == pytest.approx(0.015, abs=1e-9)
    assert dist["heaviest"][0]["session_id"] == "sess-other"
    retried = next(h for h in dist["heaviest"]
                   if h["session_id"] == "sess-retried")
    assert retried["cost_usd"] == pytest.approx(0.005, abs=1e-9)
    assert retried["rows"] == 2


def test_calls_without_usage_rides_the_emitted_row():
    """The no-usage disclosure must survive the PII allowlist and reach the
    session-level roll-up, not survive only inside ``by_stage``."""
    from tortoise import hosted_api as ha

    stats: dict = {}
    v2._accumulate_call_cost(stats, prompt_tokens=0, completion_tokens=0,
                             cost_usd=None, provider=_PROVIDER, model=_MODEL)
    llm = {"calls": 0, "retries": 0, "truncated": 0, "deadline_aborts": 0}
    v2._rollup_llm(llm, stats, "s2")
    assert llm["calls_without_usage"] == 1
    assert llm["calls_without_cost"] == 1

    props = ha._capture_cost_props("sess", {"stats": {"llm": llm}})
    assert props is not None
    assert props["calls_without_usage"] == 1
    assert "calls_without_usage" in ha._ALLOWED_ANALYTICS_PROPS


def test_resolve_entities_stage_cost_is_measured():
    """The D3 entity-resolution LLM fallback makes a REAL provider call. It
    used to be measured nowhere — silent unmeasured spend for every session
    that hit it (review P2)."""
    search = {"entities": [{"id": "obj1", "name": "Joseph",
                            "kind": "core:person"}],
              "points": [], "events": []}

    class CostReportingResolver:
        provider = _PROVIDER
        id = _MODEL
        last_finish_reason = "stop"

        def complete(self, *, system, user, max_tokens=None):
            self.last_prompt_tokens = 100
            self.last_completion_tokens = 10
            self.last_cost_usd = 0.001
            return json.dumps({"resolutions": [
                {"name": "Joe", "resolves_to": "Joseph"}]})

    stats: dict = {}
    res = v2.resolve_entities([{"name": "Joe", "kind": "core:person"}],
                              search, model=CostReportingResolver(),
                              stats=stats)
    assert res["records"][0]["mode"] == "llm"
    assert stats["cost"]["calls"] == 1                 # the call IS measured
    assert stats["cost"]["cost_usd"] == pytest.approx(0.001, abs=1e-9)

    llm = {"calls": 0, "retries": 0, "truncated": 0, "deadline_aborts": 0,
           "by_stage": {}}
    v2._rollup_llm(llm, stats, "resolve")
    lane = llm["by_stage"]["resolve"][_PROVIDER][_MODEL]
    assert lane["calls"] == 1
    assert lane["cost_usd"] == pytest.approx(0.001, abs=1e-9)


def test_resolve_entities_stats_are_optional():
    """Callers that pass no stats dict (the existing tests, phase-1 paths)
    must keep working unchanged."""
    search = {"entities": [{"id": "obj1", "name": "Joseph",
                            "kind": "core:person"}],
              "points": [], "events": []}
    res = v2.resolve_entities([{"name": "Joseph", "kind": "core:person"}],
                              search, model=None)
    assert res["map"]["Joseph"]["id"] == "obj1"


def test_cost_by_stage_priced_flag_is_and_merged_across_rows():
    """``priced`` is a per-lane property (it depends on ``usage_present``), so
    one unpriced row for a lane must leave the aggregate lane UNPRICED —
    latching the first row's flag would print a priced-looking lane whose
    tokens partly were not (review P2)."""
    # BOTH orderings: a first-row latch would keep priced=True when the
    # PRICED row is seen first, so the priced-first case is the
    # discriminator; the unpriced-first case catches last-wins.
    priced_lane = {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.001, "usage_present": True,
        "calls_without_usage": 0}}}
    unpriced_lane = {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 0, "completion_tokens": 0,
        "cost_usd": 0.0, "usage_present": False,
        "calls_without_usage": 1}}}

    for order, first, second in (
            ("unpriced-first", unpriced_lane, priced_lane),
            ("priced-first", priced_lane, unpriced_lane)):
        out = costing.cost_by_stage([
            {"properties": {"session_id": "a", "by_stage": {"s2": first}}},
            {"properties": {"session_id": "b", "by_stage": {"s2": second}}},
        ])
        lane = out["by_stage"]["s2"]["models"][_PROVIDER][_MODEL]
        assert lane["priced"] is False, order
        assert lane["map_priced_usd"] > 0.0, order  # priced tokens still count
        assert out["by_stage"]["s2"]["unpriced_calls"] == 1, order

    # all-priced lanes stay priced
    out = costing.cost_by_stage([
        {"properties": {"session_id": "a", "by_stage": {"s2": priced_lane}}},
        {"properties": {"session_id": "b", "by_stage": {"s2": priced_lane}}},
    ])
    assert out["by_stage"]["s2"]["models"][_PROVIDER][_MODEL]["priced"] is True


def test_all_unpriced_sessions_do_not_read_as_mixed():
    """A session whose lanes were ALL unpriced used neither the provider's
    charge nor the map — it must not be counted as both, which reported
    ``source: mixed`` for a dataset where nothing was priced at all (and
    contradicted the per-session ``source``).
    """
    unmapped = {"properties": {"session_id": "mystery", "calls": 1,
                              "cost_usd": 0.0, "calls_without_cost": 1,
                              "calls_without_usage": 0, "deadline_aborts": 0,
                              "by_stage": {"s1": {"acme": {"mystery-1": {
                                  "calls": 1, "prompt_tokens": 100,
                                  "completion_tokens": 10, "cost_usd": 0.0,
                                  "usage_present": True,
                                  "calls_without_usage": 0}}}}}}
    dist = costing.cost_per_session_distribution([unmapped])

    assert dist["n"] == 1
    assert dist["unpriced_sessions"] == 1
    assert dist["source"] == "provider"      # not "mixed" — nothing priced
    assert dist["heaviest"][0]["source"] == "unpriced"
    assert dist["provider_reported_usd"] == 0.0
    assert dist["map_priced_usd"] == 0.0


def test_thresholds_file_metric_now_has_a_producer():
    """Guard against the original defect: A18 was asserted but nothing
    computed it. The computation now exists and is importable."""
    assert callable(costing.cost_per_session_distribution)
    assert costing.PRICING_MAP_VERSION        # versioned, repricable at read time


# ── no-extraction is a well-defined result, never a silent $0 ─────────────

def test_no_extraction_never_counts_as_a_zero_cost_success():
    """A session that produced NO measurement must not enter the cost
    distribution as a $0 session — that would drag p50 toward zero for
    exactly the reason the launch gate exists.

    Three shapes, all distinct and all disclosed:
      1. no row at all (replay / M2 / error) — ``_capture_cost_props`` is None;
      2. a row with no attempted call (empty capture);
      3. a row whose calls were attempted but never metered (e.g. every
         generation deadline-killed — billed upstream, no tokens here).
    """
    from tortoise import hosted_api as ha

    # 1. no extraction telemetry -> no row, not a zero-cost row
    assert ha._capture_cost_props("sess-none", {"stats": {}}) is None
    assert ha._capture_cost_props("sess-none", {}) is None

    # 2. a real row, but nothing was ever attempted
    zero = {"properties": {"session_id": "empty", "calls": 0,
                            "cost_usd": 0.0, "calls_without_cost": 0,
                            "deadline_aborts": 0, "by_stage": {}}}
    # 3. attempted, nothing metered — and the deadline kill is disclosed
    unmetered = {"properties": {"session_id": "killed", "calls": 2,
                                "cost_usd": 0.0, "calls_without_cost": 0,
                                "deadline_aborts": 2, "by_stage": {}}}
    measured = _row("real", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})

    dist = costing.cost_per_session_distribution([zero, unmetered, measured])

    assert dist["n_rows"] == 3          # every row accounted for
    assert dist["n"] == 1               # only the metered session is priced
    assert dist["p50"] == pytest.approx(0.004, abs=1e-9)   # not dragged to $0
    assert dist["excluded_no_calls"] == 1
    assert dist["excluded_unmeasured"] == 1
    assert dist["deadline_aborts"] == 2  # billed-but-unpriceable, disclosed
    assert [h["session_id"] for h in dist["heaviest"]] == ["real"]


def test_deadline_aborts_ride_the_emitted_row():
    """The disclosure has to survive the PII allowlist, or the report can
    never see it (the #3359 allowlist-strips-the-measurement failure mode).
    """
    from tortoise import hosted_api as ha

    meta = {"stats": {"llm": {
        "calls": 2, "retries": 0, "prompt_tokens": 0,
        "completion_tokens": 0, "cost_usd": 0.0,
        "calls_without_cost": 0, "deadline_aborts": 2, "by_stage": {},
    }}}
    props = ha._capture_cost_props("sess-killed", meta)
    assert props is not None
    assert props["deadline_aborts"] == 2
    assert "deadline_aborts" in ha._ALLOWED_ANALYTICS_PROPS

    dist = costing.cost_per_session_distribution([{"properties": props}])
    assert dist["deadline_aborts"] == 2
    assert dist["n"] == 0                # nothing measurable -> not priced
    assert dist["excluded_unmeasured"] == 1


# ── per-stage breakdown (the cheap-point-model evidence) ─────────────────

def test_cost_by_stage_reports_per_stage_and_per_model_breakdown():
    """The acceptance criterion asks for a per-stage cost split so the
    cheap-point-model mitigation can be shown to work (or not). Stages and
    their serving models must both be visible."""
    cheap = {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 1_000_000, "completion_tokens": 0,
        "cost_usd": 0.0, "usage_present": True, "calls_without_usage": 0}}}}
    dear = {"s4": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 0, "completion_tokens": 1_000_000,
        "cost_usd": 0.0, "usage_present": True, "calls_without_usage": 0}}}}
    out = costing.cost_by_stage([
        {"properties": {"session_id": "a", "by_stage": cheap}},
        {"properties": {"session_id": "b", "by_stage": dear}},
    ])
    by_stage = out["by_stage"]
    assert set(by_stage) == {"s1", "s4"}
    # 1M input tokens @ $0.14/1M vs 1M output tokens @ $0.28/1M
    assert by_stage["s1"]["map_priced_usd"] == pytest.approx(0.14, abs=1e-6)
    assert by_stage["s4"]["map_priced_usd"] == pytest.approx(0.28, abs=1e-6)
    assert by_stage["s1"]["models"][_PROVIDER][_MODEL]["calls"] == 1
    assert by_stage["s1"]["unpriced_calls"] == 0
    assert out["map_version"] == costing.PRICING_MAP_VERSION


def test_cost_by_stage_flags_an_unmapped_model():
    """An unmapped model is LOUD, never a silent $0."""
    by_stage = {"s2": {"openrouter": {"some/unlisted-model": {
        "calls": 3, "prompt_tokens": 500, "completion_tokens": 50,
        "cost_usd": 0.0, "usage_present": True, "calls_without_usage": 0}}}}
    out = costing.cost_by_stage([{"properties": {"by_stage": by_stage}}])
    bucket = out["by_stage"]["s2"]
    assert bucket["unpriced_calls"] == 3
    assert bucket["map_priced_usd"] == 0.0
    lane = bucket["models"]["openrouter"]["some/unlisted-model"]
    assert lane["priced"] is False


# ── the queryable path (the CLI the launch gate reads) ───────────────────

def test_report_cli_prints_p50_and_p95_from_a_capture_cost_jsonl(
        tmp_path, capsys):
    """Deliverable #3: a QUERYABLE path to p50/p95 $/session. Runs the real
    ``tools/capture_cost_report.py`` entry point over a real
    ``capture_cost`` JSONL (the shape the hosted writer emits) and asserts
    the launch-gate number is actually printed.
    """
    from tools import capture_cost_report as report

    def emit(session_id, cost):
        return {
            "org_id": "org-1", "event_name": "capture_cost",
            "created_at": "2026-09-16T12:00:00+00:00",
            "properties": {
                "session_id": session_id, "calls": 1, "retries": 0,
                "prompt_tokens": 100, "completion_tokens": 10,
                "cost_usd": cost, "calls_without_cost": 0,
                "deadline_aborts": 0,
                "by_stage": {"s1": {_PROVIDER: {_MODEL: {
                    "calls": 1, "prompt_tokens": 100,
                    "completion_tokens": 10, "cost_usd": cost,
                    "usage_present": True, "calls_without_usage": 0}}}},
            },
        }

    rows = [emit("s1", 0.001), emit("s2", 0.002), emit("s3", 0.003)]
    rows.append({"event_name": "some_other_event",
                 "properties": {"session_id": "ignored"}})
    rows.append({"org_id": "org-1", "event_name": "capture_cost",
                 "created_at": "2026-09-16T12:05:00+00:00",
                 "properties": {"session_id": "empty", "calls": 0,
                                "cost_usd": 0.0, "calls_without_cost": 0,
                                "deadline_aborts": 0, "by_stage": {}}})
    fixture = tmp_path / "capture_cost.jsonl"
    fixture.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out_json = tmp_path / "report.json"

    rc = report.main(["--jsonl", str(fixture), "--top", "2",
                      "--out", str(out_json)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "p50 $/session" in text
    assert "p95 $/session" in text
    assert "$0.002000" in text          # p50 of 0.001/0.002/0.003
    assert "excluded, no calls at all" in text
    assert "s1:" in text                # the per-stage split is printed
    assert _MODEL in text                # model ids are named

    payload = json.loads(out_json.read_text())
    assert payload["distribution"]["n"] == 3
    assert payload["distribution"]["excluded_no_calls"] == 1
    assert payload["distribution"]["p50"] == pytest.approx(0.002, abs=1e-9)
    assert payload["distribution"]["map_version"] == costing.PRICING_MAP_VERSION
    assert payload["model_ids"] == [_MODEL]


def test_report_cli_is_loud_when_no_session_is_measurable(tmp_path, capsys):
    """Rows present but NOTHING measurable (all deadline-killed / usage-less)
    must not print a $0.000000 launch-gate number, and the machine-readable
    payload must say ``measurable: false`` — otherwise a mechanical gate
    reads p50=0.0 as a pass against A18's alert: 0.15.
    """
    from tools import capture_cost_report as report

    killed = {"org_id": "org-1", "event_name": "capture_cost",
              "created_at": "2026-09-16T12:00:00+00:00",
              "properties": {"session_id": "killed", "calls": 3,
                             "cost_usd": 0.0, "calls_without_cost": 0,
                             "calls_without_usage": 0, "deadline_aborts": 3,
                             "by_stage": {}}}
    fixture = tmp_path / "killed.jsonl"
    fixture.write_text(json.dumps(killed) + "\n", encoding="utf-8")
    out_json = tmp_path / "killed.json"

    rc = report.main(["--jsonl", str(fixture), "--out", str(out_json)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "NO PRICED capture_cost SESSIONS" in text
    assert "p50 $/session" not in text          # no $0 fake number
    assert "$0.000000" not in text

    payload = json.loads(out_json.read_text())
    assert payload["measurable"] is False
    assert payload["distribution"]["n"] == 0
    assert payload["distribution"]["excluded_unmeasured"] == 1
    assert payload["distribution"]["deadline_aborts"] == 3


def test_report_cli_marks_a_measurable_window(tmp_path, capsys):
    """The positive control for the flag above."""
    from tools import capture_cost_report as report

    row = _row("s1", 0.002, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.002, "usage_present": True,
        "calls_without_usage": 0}}}})
    fixture = tmp_path / "ok.jsonl"
    fixture.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_json = tmp_path / "ok.json"

    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    capsys.readouterr()
    assert json.loads(out_json.read_text())["measurable"] is True


def test_report_cli_is_loud_when_nothing_could_be_priced(tmp_path, capsys):
    """A window with counted sessions whose lanes are ALL unpriced (an
    unmapped model on a lane that reports no charge) has ``n > 0`` but no
    priceable number. It must NOT print $0.000000 and must report
    ``measurable: false`` — otherwise a mechanical gate reads p50=0.0 as a
    pass against A18's alert: 0.15.
    """
    from tools import capture_cost_report as report

    unpriced = {"org_id": "org-1", "event_name": "capture_cost",
                "created_at": "2026-09-16T12:00:00+00:00",
                "properties": {
                    "session_id": "mystery", "calls": 1,
                    "cost_usd": 0.0, "calls_without_cost": 1,
                    "calls_without_usage": 0, "deadline_aborts": 0,
                    "by_stage": {"s1": {"deepseek-direct": {
                        "no-such-model-0731": {
                            "calls": 1, "prompt_tokens": 100,
                            "completion_tokens": 10, "cost_usd": 0.0,
                            "usage_present": True,
                            "calls_without_usage": 0}}}}}}
    fixture = tmp_path / "unpriced.jsonl"
    fixture.write_text(json.dumps(unpriced) + "\n", encoding="utf-8")
    out_json = tmp_path / "unpriced.json"

    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "NO PRICED capture_cost SESSIONS" in text
    assert "p50 $/session" not in text
    assert "$0.000000" not in text

    payload = json.loads(out_json.read_text())
    assert payload["measurable"] is False
    assert payload["distribution"]["n"] == 1        # counted
    assert payload["distribution"]["priced_sessions"] == 0
    assert payload["distribution"]["unpriced_sessions"] == 1


# ── poison-tolerant report assembly (foreign JSONL input) ────────────────

def test_poisoned_values_degrade_instead_of_crashing():
    """A tampered/foreign row must degrade the report, not crash it — the
    module's ``_price_lane._tok`` contract. ``float('inf')`` and non-dict
    buckets previously raised OverflowError/AttributeError."""
    poisoned = {"properties": {
        "session_id": "poison", "calls": float("inf"),
        "cost_usd": "not-a-number", "calls_without_cost": float("nan"),
        "deadline_aborts": True,
        "by_stage": {"s1": {"openrouter": {"some/model": {
            "calls": float("inf"), "prompt_tokens": float("inf"),
            "completion_tokens": float("nan"),
            "cost_usd": "junk", "usage_present": True}}}}}}
    not_a_dict = {"properties": {"session_id": "junk",
                                 "by_stage": {"s2": {"p": {"m": "junk"}}}}}

    # neither call may raise
    dist = costing.cost_per_session_distribution([poisoned, not_a_dict])
    stages = costing.cost_by_stage([poisoned, not_a_dict])

    assert dist["n_rows"] == 2
    assert isinstance(dist["p95"], float)
    assert dist["calls_without_cost"] == 0        # nan/bool degrade to 0
    assert dist["deadline_aborts"] == 0           # bool degrades to 0
    assert stages["by_stage"]["s1"]["models"]["openrouter"]["some/model"][
        "calls"] == 0


def test_report_cli_survives_a_poisoned_row(tmp_path, capsys):
    """The CLI must exit 0 on a poisoned-but-valid-JSON row, not traceback."""
    from tools import capture_cost_report as report

    poisoned = {"event_name": "capture_cost", "properties": {
        "session_id": "poison", "calls": 2,
        "cost_usd": 0.002, "calls_without_cost": 0, "deadline_aborts": 0,
        "by_stage": {"s1": {_PROVIDER: {_MODEL: {
            "calls": 2, "prompt_tokens": float("inf"),
            "completion_tokens": float("nan"), "cost_usd": 0.002,
            "usage_present": True, "calls_without_usage": 0}}}}}}
    fixture = tmp_path / "poison.jsonl"
    # json.dumps writes Infinity/NaN (valid in Python's default dialect)
    fixture.write_text(json.dumps(poisoned) + "\n", encoding="utf-8")

    assert report.main(["--jsonl", str(fixture)]) == 0
    assert "p50 $/session" in capsys.readouterr().out


def test_poisoned_containers_degrade_instead_of_crashing():
    """The container levels above the leaf bucket can be poisoned too (a
    foreign export with ``by_stage`` as a string/list, or a stage whose
    value is not a dict). Each must degrade, not traceback the report.
    """
    shapes = [
        {"session_id": "a", "by_stage": "junk", "calls": 1},
        {"session_id": "b", "by_stage": [1, 2], "calls": 1},
        {"session_id": "c", "by_stage": 7, "calls": 1},
        {"session_id": "d", "by_stage": {"s1": "junk"}, "calls": 1},
        {"session_id": "e", "by_stage": {"s1": [1]}, "calls": 1},
        {"session_id": "f", "by_stage": {"s1": {"p": "junk"}}, "calls": 1},
    ]
    rows = [{"properties": p} for p in shapes]

    dist = costing.cost_per_session_distribution(rows)
    stages = costing.cost_by_stage(rows)
    assert dist["n_rows"] == 6
    assert isinstance(dist["p95"], float)
    assert stages["by_stage"] == {}          # nothing usable was found

    from tools import capture_cost_report as report
    assert report._model_ids(rows) == []      # no crash enumerating models


def test_non_finite_provider_cost_never_reaches_the_percentile():
    """``cost_usd: Infinity/NaN`` must not become the launch-gate number —
    ``p50: inf`` is worse than a disclosed 0, and it serialises to invalid
    strict JSON in the machine-readable report."""
    for bad in (float("inf"), float("-inf"), float("nan")):
        dist = costing.cost_per_session_distribution(
            [{"properties": {"session_id": "x", "calls": 1,
                              "cost_usd": bad, "calls_without_cost": 0,
                              "deadline_aborts": 0, "by_stage": {}}}])
        assert math.isfinite(dist["p50"])
        assert math.isfinite(dist["p95"])
        assert math.isfinite(dist["total_usd"])


    # huge arbitrary-precision ints are valid JSON and raise OverflowError
    # inside math.isfinite — the magnitude guard must come first
    big = 10 ** 400
    big_lane = {"calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
                "cost_usd": big, "usage_present": True,
                "calls_without_usage": 0}

    def _row_with(cost_usd, by_stage):
        return {"properties": {"session_id": "big", "calls": 1,
                                "cost_usd": cost_usd,
                                "calls_without_cost": 0,
                                "deadline_aborts": 0, "by_stage": by_stage}}
    dist_big = costing.cost_per_session_distribution([
        _row_with(big, {}),                                   # poisoned total
        _row_with(0.0, {"s1": {_PROVIDER: {_MODEL: big_lane}}}),   # + lane
    ])
    assert math.isfinite(dist_big["p50"])
    assert math.isfinite(dist_big["total_usd"])

    stages = costing.cost_by_stage(
        [_row_with(0.0, {"s1": {_PROVIDER: {_MODEL: big_lane}}})])
    lane = stages["by_stage"]["s1"]["models"][_PROVIDER][_MODEL]
    assert lane["provider_reported_usd"] == 0.0     # the poison degraded
    assert lane["map_priced_usd"] > 0.0             # map pricing still works


def test_report_cli_survives_a_huge_integer_cost(tmp_path, capsys):
    """A 400-digit integer ``cost_usd`` is valid JSON and must not traceback
    the CLI (``math.isfinite`` raises OverflowError on such ints)."""
    from tools import capture_cost_report as report

    row = {"event_name": "capture_cost", "properties": {
        "session_id": "big", "calls": 1, "cost_usd": 10 ** 400,
        "calls_without_cost": 0, "deadline_aborts": 0,
        "by_stage": {"s1": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
            "cost_usd": 10 ** 400, "usage_present": True,
            "calls_without_usage": 0}}}}}}
    fixture = tmp_path / "big.jsonl"
    fixture.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert report.main(["--jsonl", str(fixture)]) == 0
    assert "p50 $/session" in capsys.readouterr().out


def test_window_tolerates_mixed_timestamp_types():
    """An export with epoch ints or structured stamps must not kill the
    report on ``min()``/``max()`` type comparison."""
    from tools import capture_cost_report as report

    assert report._window([]) == ("unknown", "unknown")
    assert report._window([{"created_at": 1}, {"created_at": "2026-01-01"}]) == (
        "1", "2026-01-01")
    assert report._window([{"created_at": {"x": 1}}])[0] != "unknown"


def test_report_cli_warns_when_the_window_is_only_partially_priced(
        tmp_path, capsys):
    """A mixed window still prints the number, but must flag that some of
    it is a lower bound — otherwise a mechanical gate reads p50 as fully
    measured."""
    from tools import capture_cost_report as report

    priced = _row("priced", 0.5, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.5, "usage_present": True,
        "calls_without_usage": 0}}}})
    unpriced = {"properties": {
        "session_id": "unpriced", "calls": 1, "cost_usd": 0.0,
        "calls_without_cost": 1, "calls_without_usage": 0,
        "deadline_aborts": 0,
        "by_stage": {"s1": {"deepseek-direct": {"no-such-model-0731": {
            "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
            "cost_usd": 0.0, "usage_present": True,
            "calls_without_usage": 0}}}}}}
    fixture = tmp_path / "mixed.jsonl"
    fixture.write_text(
        "\n".join(json.dumps(r) for r in (priced, unpriced)) + "\n",
        encoding="utf-8")
    out_json = tmp_path / "mixed.json"

    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "p50 $/session" in text
    assert "PARTIALLY PRICED" in text
    assert "sessions counted : n=2" in text
    assert "of which priced  : 1" in text

    payload = json.loads(out_json.read_text())
    assert payload["measurable"] is True
    assert payload["fully_priced"] is False
    assert payload["priced_sessions"] == 1


def test_unmetered_attempts_are_disclosed_and_block_fully_priced():
    """Attempts that produced no meterable response (a provider-side read
    timeout retried by `_complete`, which is NOT the extractor's own
    deadline kill) may still be billed upstream. They must be disclosed and
    must stop ``fully_priced`` from reading true — otherwise p50/p95
    understate billed spend with no aggregate disclosure.
    """
    stage = {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}}
    # 3 attempts, only 1 of which produced a metered call -> 2 unmetered
    row = _row("flaky", 0.004, stage)
    row["properties"]["calls"] = 3

    dist = costing.cost_per_session_distribution([row])
    assert dist["n"] == 1
    assert dist["unmetered_attempts"] == 2
    assert dist["fully_priced"] is False
    assert dist["heaviest"][0]["unmetered_attempts"] == 2

    # the clean control stays fully priced
    clean = costing.cost_per_session_distribution([_row("clean", 0.004, stage)])
    assert clean["unmetered_attempts"] == 0
    assert clean["fully_priced"] is True


def test_report_cli_warns_on_unmetered_attempts(tmp_path, capsys):
    """The CLI must say so out loud, not just carry the counter."""
    from tools import capture_cost_report as report

    stage = {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}}
    row = _row("flaky", 0.004, stage)
    row["event_name"] = "capture_cost"
    row["properties"]["calls"] = 3
    fixture = tmp_path / "flaky.jsonl"
    fixture.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_json = tmp_path / "flaky.json"

    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "produced no meterable response" in text
    assert "attempts with no meterable reply: 2" in text
    payload = json.loads(out_json.read_text())
    assert payload["fully_priced"] is False
    assert payload["distribution"]["unmetered_attempts"] == 2


def test_report_cli_live_pull_failure_exits_2(tmp_path, capsys, monkeypatch):
    """A Supabase transport/status failure on the tool's PRIMARY documented
    path (``--days``) must honour the documented exit-2 contract, not
    traceback."""
    from tools import capture_cost_report as report

    monkeypatch.setenv("SUPABASE_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "not-a-real-key")
    rc = report.main(["--days", "1"])
    assert rc == 2
    assert "live pull failed" in capsys.readouterr().err


def test_absent_cost_usd_is_disclosed_not_a_silent_zero():
    """A row whose ``properties`` OMITS ``cost_usd`` (reachable via the
    tool's documented foreign-input paths — ``--json``/``--jsonl``) must
    not be read as "the provider reported $0": that would emit a silent
    $0.00 and ``fully_priced: true`` while discarding the map's own price
    for the very same tokens (review cycle 9).
    """
    # No ``cost_usd`` key at all, but priceable tokens under ``by_stage``.
    row = {"properties": {"session_id": "nokey", "calls": 1,
                          "calls_without_cost": 0, "calls_without_usage": 0,
                          "deadline_aborts": 0,
                          "by_stage": {"s1": {_PROVIDER: {_MODEL: {
                              "calls": 1, "prompt_tokens": 1000,
                              "completion_tokens": 100,
                              "usage_present": True,
                              "calls_without_usage": 0}}}}}}

    dist = costing.cost_per_session_distribution([row])
    assert dist["n"] == 1
    # Priced from the MAP, because the provider total was never supplied.
    assert dist["total_usd"] > 0.0
    assert dist["source"] == "map"
    assert dist["fully_priced"] is True


def test_null_cost_usd_with_no_metered_call_is_disclosed_not_a_silent_zero():
    """``cost_usd: None`` with no metered call at all: no measurement exists,
    so the session is EXCLUDED from the distribution and disclosed — it must
    never be reported as a confident $0 (review cycle 9)."""
    row = {"properties": {"session_id": "nullcost", "calls": 2,
                          "cost_usd": None,
                          "calls_without_cost": 0, "calls_without_usage": 0,
                          "deadline_aborts": 0, "by_stage": {}}}

    dist = costing.cost_per_session_distribution([row])
    assert dist["n"] == 0                      # no measurement -> no sample
    assert dist["excluded_unmeasured"] == 1
    assert dist["unmetered_attempts"] == 2      # still disclosed
    assert dist["priced_sessions"] == 0
    assert dist["fully_priced"] is False        # not a silent zero-success


def test_absent_cost_usd_with_unpriceable_tokens_is_flagged_unpriced():
    """A row with meterable calls but an UNPRICEABLE model and NO provider
    ``cost_usd`` must land in the distribution as a DISCLOSED unpriced
    lower bound — not counted as a priced $0 (review cycle 9)."""
    row = {"properties": {"session_id": "unpriced", "calls": 1,
                          "calls_without_cost": 0, "calls_without_usage": 0,
                          "deadline_aborts": 0,
                          "by_stage": {"s1": {_PROVIDER: {"unknown-model": {
                              "calls": 1, "prompt_tokens": 1000,
                              "completion_tokens": 100,
                              "usage_present": True,
                              "calls_without_usage": 0}}}}}}

    dist = costing.cost_per_session_distribution([row])
    assert dist["n"] == 1
    assert dist["unpriced_sessions"] == 1
    assert dist["priced_sessions"] == 0
    assert dist["fully_priced"] is False


def test_cost_by_stage_marks_the_pair_overlapping():
    """The stage/lane `by_stage` envelope carries the SAME non-additive
    pair as the distribution, so it must carry the same marker — a consumer
    summing `provider_reported_usd + map_priced_usd` per stage would
    otherwise manufacture phantom spend (review cycle 10)."""
    row = _row("stagey", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})

    stages = costing.cost_by_stage([row])
    assert stages["overlap"] is True
    bucket = stages["by_stage"]["s1"]
    assert bucket["overlap"] is True
    # ... and on the LEAF lane entry, which the --out JSON emits verbatim.
    lane = bucket["models"][_PROVIDER][_MODEL]
    assert lane["overlap"] is True
    assert lane["provider_reported_usd"] > 0.0
    assert lane["map_priced_usd"] > 0.0


def test_provider_and_map_totals_are_marked_overlapping():
    """``provider_reported_usd``/``map_priced_usd`` overlap and must be
    machine-detectably non-additive (review cycle 9)."""
    row = _row("both", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})
    dist = costing.cost_per_session_distribution([row])
    assert dist["overlap"] is True
    # The documented invariant: the pair is NOT a partition of total_usd.
    assert dist["provider_reported_usd"] > 0.0
    assert dist["map_priced_usd"] > 0.0
    assert dist["total_usd"] < (dist["provider_reported_usd"]
                                + dist["map_priced_usd"])


def test_all_unmetered_session_still_discloses_its_attempts():
    """A session whose EVERY attempt produced no meterable response is
    excluded from the distribution (no measurement exists) — but its
    attempts may still be billed upstream, so they must STILL be disclosed
    and must stop ``fully_priced`` from reading true (review cycle 8).
    """
    clean = _row("clean", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})
    dead = {"properties": {"session_id": "dead", "calls": 3,
                           "cost_usd": 0.0, "calls_without_cost": 0,
                           "calls_without_usage": 0, "deadline_aborts": 0,
                           "by_stage": {}}}

    dist = costing.cost_per_session_distribution([clean, dead])
    assert dist["n"] == 1                     # only the clean one is priced
    assert dist["excluded_unmeasured"] == 1
    assert dist["unmetered_attempts"] == 3    # still disclosed
    assert dist["fully_priced"] is False      # the window is NOT fully priced


def test_report_cli_live_pull_invalid_url_exits_2(capsys, monkeypatch):
    """A malformed SUPABASE_URL raises ``httpx.InvalidURL``, which is NOT an
    ``httpx.HTTPError`` — it must still honour the documented exit-2
    contract (review cycle 8)."""
    from tools import capture_cost_report as report

    monkeypatch.setenv("SUPABASE_URL", "http://localhost:notaport")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "not-a-real-key")
    assert report.main(["--days", "1"]) == 2
    assert "live pull failed" in capsys.readouterr().err


def test_report_cli_is_loud_on_an_empty_window(tmp_path, capsys):
    """An empty window must NEVER read as a passing measurement — it has to
    say the launch gate cannot be computed."""
    from tools import capture_cost_report as report

    fixture = tmp_path / "empty.jsonl"
    fixture.write_text("", encoding="utf-8")
    rc = report.main(["--jsonl", str(fixture)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "NO capture_cost ROWS IN THIS WINDOW" in text
    assert "NOT a pass" in text


def test_report_cli_rejects_unusable_input(tmp_path, capsys):
    """Bad input is exit 2 with a message on stderr — never a silent
    zero-report."""
    from tools import capture_cost_report as report

    bad = tmp_path / "bad.jsonl"
    bad.write_text("not json at all\n", encoding="utf-8")
    rc = report.main(["--jsonl", str(bad)])
    assert rc == 2
    assert "not valid JSON" in capsys.readouterr().err


# ── the hosted EMISSION call site, executed end-to-end (B7 audit repair) ──
#
# Everything above exercises the props builder and the analytics writer
# SEPARATELY. The call site that joins them — the hosted capture handler —
# was never executed, so a mutation there (attributing the row to another
# session, or emitting an empty row when no extraction ran) stayed green;
# and the by-stage assertions used byte-identical per-stage lanes (100/10/
# $0.001 for s1, s2 AND s4), so a wrong STAGE KEY in the emitted payload was
# invisible. These tests drive the REAL REST capture endpoint over an
# embedded DB and deep-equal the row the REAL writer receives.

_B7_TEAM = {"org_id": "team-b7-cost", "tier": "free", "key_id": "k-b7",
            "legacy_full_access": True, "max_points": 100000}

_B7_S2 = ('{"entities": [], "events": [], "operators": [], '
          '"points": [{"content": "s2 point", '
          '"pointKind": "statement"}]}')
_B7_S4 = ('{"entities": [], "events": [], "operators": [], '
          '"points": [{"content": "s4 point", '
          '"pointKind": "statement"}]}')


class DistinctStageCostModel:
    """Cost-reporting extractor stub whose s1/s2/s4 calls report DIFFERENT
    ``(tokens, charge)`` — the discriminator the byte-identical fixtures
    above lack. Every call is recorded, so the expected payload is derived
    from what the provider actually served, never from a hand-written
    constant."""

    provider = _PROVIDER
    id = _MODEL
    last_finish_reason = "stop"

    def __init__(self):
        self.calls: list[tuple[str, int, int, float]] = []

    def complete(self, *, system: str, user: str, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            stage, pt, ct, cost, out = "s1", 100, 10, 0.001, "A narrative."
        elif "GAP REVIEWER" in system:
            stage, pt, ct, cost, out = "s4", 300, 30, 0.003, _B7_S4
        else:
            stage, pt, ct, cost, out = "s2", 200, 20, 0.002, _B7_S2
        self.last_prompt_tokens = pt
        self.last_completion_tokens = ct
        self.last_cost_usd = cost
        self.calls.append((stage, pt, ct, cost))
        return out

    def expected_props(self, session_id: str) -> dict:
        """The exact ``capture_cost`` properties the stub's OWN calls imply."""
        by_stage: dict = {}
        for stage, pt, ct, cost in self.calls:
            bucket = (by_stage.setdefault(stage, {})
                      .setdefault(_PROVIDER, {}).setdefault(_MODEL, {
                          "calls": 0, "prompt_tokens": 0,
                          "completion_tokens": 0, "cost_usd": 0.0,
                          "calls_without_cost": 0, "calls_without_usage": 0,
                          "usage_present": True}))
            bucket["calls"] += 1
            bucket["prompt_tokens"] += pt
            bucket["completion_tokens"] += ct
            bucket["cost_usd"] = round(bucket["cost_usd"] + cost, 6)
        return {
            "session_id": session_id,
            "calls": len(self.calls),
            "retries": 0,
            "prompt_tokens": sum(c[1] for c in self.calls),
            "completion_tokens": sum(c[2] for c in self.calls),
            "cost_usd": round(sum(c[3] for c in self.calls), 6),
            "calls_without_cost": 0,
            "calls_without_usage": 0,
            "deadline_aborts": 0,
            "by_stage": by_stage,
        }


@pytest.fixture()
def _b7_capture_client(tmp_path, monkeypatch):
    """The REAL hosted capture app over an embedded DB, with the analytics
    writer pointed at a temp JSONL (no Supabase, no network)."""
    from fastapi.testclient import TestClient

    from tests._http_fixtures import patched_tortoise_sdk
    from tortoise import hosted_api as _ha
    from tortoise.hosted_api import app, get_current_org

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(_ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics.jsonl"))
    with patched_tortoise_sdk(str(tmp_path / "b7.db")):
        app.dependency_overrides[get_current_org] = lambda: dict(_B7_TEAM)
        with TestClient(app) as tc:
            yield tc


def _b7_rows(tmp_path: Path) -> list[dict]:
    path = tmp_path / "analytics.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def test_hosted_capture_emits_that_session_s_measured_cost_row(
        tmp_path, monkeypatch, _b7_capture_client):
    """THE emission-boundary acceptance test: the REAL REST capture handler
    emits a ``capture_cost`` row through the REAL analytics writer, and the
    row's ``properties`` deep-equals the payload the served calls imply —
    the requested ``session_id``, the summed charge/tokens, and each stage's
    OWN lane under its OWN key.

    REDs on: a wrong session id at the call site (attribution corruption), a
    dropped/zeroed cost field, and any stage-key remap in the emitted
    ``by_stage`` (the lanes below are distinct, so a swap cannot hide)."""
    from tortoise import sdk as sdk_mod

    model = DistinctStageCostModel()
    monkeypatch.setattr(sdk_mod, "_V2SessionMock", lambda: model)

    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-attrib"})
    assert resp.status_code == 200, resp.text
    # the extractor really ran all three stages we are measuring (the call
    # COUNT/order is extractor_v2's chunking property, not the emission
    # contract this test pins — expected_props tolerates any call sequence,
    # so only the stage SET is asserted here)
    assert set(c[0] for c in model.calls) == {"s1", "s2", "s4"}
    assert resp.json()["stats"]["llm"]["calls"] == 3   # telemetry present

    cost_rows = [r for r in _b7_rows(tmp_path)
                 if r.get("event_name") == "capture_cost"]
    assert len(cost_rows) == 1, cost_rows
    assert cost_rows[0]["org_id"] == _B7_TEAM["org_id"]
    props = cost_rows[0]["properties"]
    # the emitted stage keys are pinned as a SET before the deep-equal, so a
    # remap that also perturbed the payload's shape cannot pass by accident
    assert set(props["by_stage"]) == {"s1", "s2", "s4"}
    assert props == model.expected_props("sess-b7-attrib")


def test_hosted_capture_emits_no_cost_row_when_no_llm_rollup_exists(
        tmp_path, monkeypatch, _b7_capture_client):
    """A capture whose extractor produced NO LLM roll-up
    (``meta["stats"] == {}``) must emit NO ``capture_cost`` row at all —
    never a zero-cost row that a reader could mistake for a measured $0.
    REDs if the handler drops the ``is not None`` guard and writes a phantom
    row.

    The lane used here is M2 (``TORTOISE_SESSION_EXTRACTOR=m2``). Note the
    name says ROLL-UP, not "no extraction": the M2 lane DOES extract and
    DOES make real provider calls — it simply discards the usage block and
    hard-codes ``stats: {}`` (``sdk.py:3903-3910``), so it is the reachable
    no-telemetry shape. That blind spot is filed as #3747 and is NOT
    endorsed here; this test pins the handler's guard, nothing more."""
    from tortoise import hosted_api as ha

    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")

    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-none"})
    assert resp.status_code == 200, resp.text
    # Pin the premise: the capture really extracted (extracted > 0) and
    # really produced NO LLM roll-up (stats == {}).
    assert resp.json()["extracted"] > 0
    assert resp.json()["stats"] == {}

    # Positive control for the ABSENCE below: the analytics channel is live
    # in this test's environment (a sentinel written through the REAL writer
    # lands where _b7_rows reads), so the missing capture_cost row is the
    # handler's guard and not a dead writer.
    ha._track_analytics_event(_B7_TEAM["org_id"], "b7_sentinel", {})
    assert any(r.get("event_name") == "b7_sentinel"
               for r in _b7_rows(tmp_path))

    assert [r for r in _b7_rows(tmp_path)
            if r.get("event_name") == "capture_cost"] == []


def test_report_cli_aggregates_retries_per_session_before_percentiling(
        tmp_path, capsys):
    """The p50/p95 TOOL aggregates rows per ``session_id`` before
    percentiling: a retried capture's second row (#2335 WI-2b) must not
    split one session's spend across two samples. Proven at the CLI
    boundary with a fixture carrying a duplicate session id."""
    from tools import capture_cost_report as report

    def emit(session_id, cost):
        # A row shaped like a LIVE writer's output — a real per-stage lane
        # with the disclosure counters present — so the aggregation is
        # exercised over a shape the boundary actually produces (not an
        # all-unmetered miscellany).
        return {"org_id": "org-1", "event_name": "capture_cost",
                "created_at": "2026-09-16T12:00:00+00:00",
                "properties": {
                    "session_id": session_id, "calls": 1,
                    "retries": 0,
                    "calls_without_cost": 0, "calls_without_usage": 0,
                    "deadline_aborts": 0, "prompt_tokens": 100,
                    "completion_tokens": 10, "cost_usd": cost,
                    "by_stage": {"s1": {_PROVIDER: {_MODEL: {
                        "calls": 1, "prompt_tokens": 100,
                        "completion_tokens": 10, "cost_usd": cost,
                        "usage_present": True, "calls_without_cost": 0,
                        "calls_without_usage": 0}}}}}}

    rows = [emit("sess-retried", 0.002), emit("sess-retried", 0.003),
            emit("sess-other", 0.010)]
    fixture = tmp_path / "retries.jsonl"
    fixture.write_text("\n".join(json.dumps(r) for r in rows) + "\n",
                       encoding="utf-8")
    out_json = tmp_path / "retries.json"

    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    capsys.readouterr()
    dist = json.loads(out_json.read_text())["distribution"]

    assert dist["n_rows"] == 3        # three ROWS
    assert dist["n"] == 2             # but two SESSIONS
    assert dist["total_usd"] == pytest.approx(0.015, abs=1e-9)
    assert dist["max"] == pytest.approx(0.010, abs=1e-9)
    # the percentile itself: over SESSIONS [0.005, 0.010] p50 is 0.0075;
    # over ROWS [0.002, 0.003, 0.010] it would be 0.003.
    assert dist["p50"] == pytest.approx(0.0075, abs=1e-9)
    assert dist["fully_priced"] is True     # no unmetered/unpriced rows
    retried = {h["session_id"]: h for h in dist["heaviest"]}["sess-retried"]
    assert retried["cost_usd"] == pytest.approx(0.005, abs=1e-9)
    assert retried["rows"] == 2


def test_capture_cost_props_carries_the_retry_counter():
    """``retries`` is emitted, not hardcoded: a roll-up carrying retries must
    surface them on the row. Without this, replacing the props mapping's
    ``retries`` with a literal ``0`` is a spelling-preserving mutation that
    no emission test can see (the stub never triggers a retry)."""
    from tortoise import hosted_api as ha

    props = ha._capture_cost_props("sess-retries", {"stats": {"llm": {
        "calls": 2, "retries": 2, "prompt_tokens": 0,
        "completion_tokens": 0, "cost_usd": 0.0, "by_stage": {}}}})
    assert props is not None
    assert props["retries"] == 2


def test_emission_write_is_handed_off_the_event_loop(
        monkeypatch, _b7_capture_client):
    """The emit must run OFF the event loop (``asyncio.to_thread``):
    ``_track_analytics_event`` POSTs synchronously and the API runs a single
    uvicorn worker, so an inline call stalls every concurrent request for the
    duration of the Supabase round-trip (the #2988/#3498 class). Asserted
    BEHAVIOURALLY — the loop thread and the writer thread must differ —
    because inlining the call leaves every other emission test green (with
    Supabase unset the write is a fast local append).

    The loop thread is sampled INDEPENDENTLY of the props build (via the
    awaited ``_async_audit`` seam), so a future refactor that moves the whole
    emission — props build AND write — into one ``to_thread`` closure still
    passes: the guarded property is "the write is off the loop", not "the
    write is on a different thread from the props build"."""
    import threading

    from tortoise import hosted_api as ha
    from tortoise import sdk as sdk_mod

    monkeypatch.setattr(sdk_mod, "_V2SessionMock",
                        lambda: DistinctStageCostModel())
    real_props = ha._capture_cost_props
    seen: dict = {}

    async def _record_audit(*args, **kwargs):
        # awaited inline by the handler → this IS the event-loop thread
        seen["loop_thread"] = threading.get_ident()

    def _record_props(*args, **kwargs):
        seen["handler_thread"] = threading.get_ident()
        return real_props(*args, **kwargs)

    def _record_emit(*args, **kwargs):
        seen["emit_thread"] = threading.get_ident()

    monkeypatch.setattr(ha, "_async_audit", _record_audit)
    monkeypatch.setattr(ha, "_capture_cost_props", _record_props)
    monkeypatch.setattr(ha, "_track_analytics_event", _record_emit)

    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-offloop"})
    assert resp.status_code == 200, resp.text
    assert "loop_thread" in seen and "handler_thread" in seen, (
        "the handler never ran — the probe is not measuring anything")
    assert "emit_thread" in seen, "the emit never reached the writer"
    assert seen["emit_thread"] != seen["loop_thread"], (
        "the analytics write ran ON the event-loop thread — it must be "
        "handed off via asyncio.to_thread")
