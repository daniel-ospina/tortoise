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

import contextlib
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
    # #3820 (cycle-2 P1): the canonical name too, or on a box carrying
    # production secrets `_service_key()` finds this one, `url` is empty, and
    # the write is classified `fallback`/`supabase_env_incomplete` — the
    # "no Supabase" premise above would be false.
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics.jsonl"))
    ha._track_analytics_event("team-1", "capture_cost", props)
    rec = json.loads((tmp_path / "analytics.jsonl").read_text()
                     .strip().splitlines()[-1])
    assert rec["event_name"] == "capture_cost"
    assert rec["properties"]["cost_usd"] == pytest.approx(0.003, abs=1e-9)
    assert rec["properties"]["by_stage"]["s1"][_PROVIDER][_MODEL][
        "prompt_tokens"] == 100


def test_capture_cost_props_none_only_for_a_genuinely_call_free_capture():
    """#3824: ``None`` must mean exactly ONE thing — zero provider calls (F1).
    A capture that reached the provider but lost its roll-up (F2) must still
    produce a row, or its billed spend and a clean $0 stay the same shape.

    REDs on: restoring the unconditional ``return None`` for an empty
    ``stats`` — the collapse #3824 exists to break.
    """
    from tortoise import hosted_api as ha

    # F1 — no calls at all (the empty-transcript gate): no row.
    assert ha._capture_cost_props("sess-1", {"stats": {}}) is None
    assert ha._capture_cost_props("sess-1", {}) is None

    # F2 — calls made, no surviving roll-up: a row carrying the disclosure.
    props = ha._capture_cost_props("sess-m2", {"stats": {"unattributed": 3}})
    assert props is not None
    assert props["unattributed"] == 3
    assert props["calls"] == 0          # nothing was meterable
    assert props["by_stage"] == {}

    # Junk evidence must not fabricate a row inside a best-effort emit —
    # including a fraction, a non-finite value, and an absurd magnitude. A
    # call count is a whole number: anything else is malformed and is treated
    # as absent rather than truncated into a row.
    for junk in (0, -1, None, "3", True, 2.5e-1, float("nan"),
                 float("inf"), 2.5, 10**400):
        assert ha._capture_cost_props(
            "sess-x", {"stats": {"unattributed": junk}}) is None, junk
    # ... while a real count beside a real roll-up rides the same row.
    both = ha._capture_cost_props("sess-both", {"stats": {
        "llm": {"calls": 1, "cost_usd": 0.5, "by_stage": {}},
        "unattributed": 2}})
    assert both is not None
    assert both["calls"] == 1 and both["unattributed"] == 2


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
      1. no row at all (the empty gate) — the emitter
         returns ``None``;
      2. a row with no attempted call (empty capture);
      3. a row whose calls were attempted but never metered (e.g. every
         generation deadline-killed — billed upstream, no tokens here).

    #3824 adds a fourth — a capture that reached the provider with no
    surviving roll-up — and it is NOT shape 1: it writes a row carrying
    ``unattributed`` and is exercised by
    ``test_m2_capture_makes_calls_so_it_is_counted_not_absent``.
    """
    from tortoise import hosted_api as ha

    # 1. no calls at all -> no row, not a zero-cost row
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
            "legacy_full_access": True, "max_points": 100000,
            "max_sessions": None}

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
                          "calls_without_tokens": 0,
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
            "calls_without_tokens": 0,
            "deadline_aborts": 0,
            # #3824: the call-evidence disclosure. Zero here because this
            # stub's calls DO reach a roll-up — the deep-equal below then
            # pins the whole payload, so a dropped or renamed key fails even
            # though the rest is right.
            "unattributed": 0,
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
    # #3820 (cycle-2 P1): `SUPABASE_SERVICE_KEY` is the LEGACY name; the
    # canonical `SUPABASE_SERVICE_ROLE_KEY` must go too, or an ambient
    # production secret makes this "no Supabase" fixture build a real
    # degradation (url absent + role key present → `supabase_env_incomplete`).
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
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
    assert resp.json()["stats"]["llm"]["calls"] == len(model.calls)  # telemetry present

    cost_rows = [r for r in _b7_rows(tmp_path)
                 if r.get("event_name") == "capture_cost"]
    assert len(cost_rows) == 1, cost_rows
    assert cost_rows[0]["org_id"] == _B7_TEAM["org_id"]
    props = cost_rows[0]["properties"]
    # the emitted stage keys are pinned as a SET before the deep-equal, so a
    # remap that also perturbed the payload's shape cannot pass by accident
    assert set(props["by_stage"]) == {"s1", "s2", "s4"}
    assert props == model.expected_props("sess-b7-attrib")


def test_counter_proxy_forwards_public_writes_but_keeps_its_own_count():
    """#3824: the counter proxy must be transparent in BOTH directions.
    Public attribute WRITES have to reach the wrapped model — the #2185 usage
    seam is attached by assignment (``model.usage_sink = sink``), so a
    read-only proxy would silently drop it — while ``count`` stays
    wrapper-local, so counting cannot leak onto the model or be clobbered by
    it. REDs on: removing ``__setattr__`` (the write is swallowed) or letting
    ``count`` forward (the model grows a phantom ``count``).
    """
    from tortoise.sdk import _SessionLLMCallCounter

    class _Model:
        usage_sink = None
        provider = "p"
        id = "m"

        def complete(self, **_kw):
            return "ok"

    model = _Model()
    proxy = _SessionLLMCallCounter(model)
    proxy.usage_sink = lambda *_a, **_k: None   # public write -> model
    assert model.usage_sink is not None
    assert proxy.provider == "p" and proxy.id == "m"   # reads delegate
    assert proxy.count == 0 and not hasattr(model, "count")
    proxy.complete()
    proxy.complete()
    assert proxy.count == 2 and not hasattr(model, "count")


class _UsageReportingSessionModel:
    """An M2-stage model that fires the REAL #2185 usage seam exactly like
    ``models.OpenAICompatModel`` does (``_emit_usage_sink`` with the
    response-local usage block), over deterministic offline content.

    ``send_usage=False`` models a provider response with NO usage block at
    all — the shape that must be disclosed with zeros, never priced with
    invented tokens.
    """

    provider = _PROVIDER

    def __init__(self, model_id, *, prompt_tokens=100, completion_tokens=10,
                 cost_usd=0.001, ledger=None, send_usage=True):
        from tortoise.extractor import MockModel

        self.id = model_id
        self.usage_sink = None
        self._pt = prompt_tokens
        self._ct = completion_tokens
        self._cost = cost_usd
        self._ledger = [] if ledger is None else ledger
        self._send_usage = send_usage
        self._inner = MockModel(model_id)

    def complete(self, *, system, user):
        from tortoise.models import _emit_usage_sink

        out = self._inner.complete(system=system, user=user)
        usage = None
        if self._send_usage:
            usage = {"prompt_tokens": self._pt,
                     "completion_tokens": self._ct}
            if self._cost is not None:
                usage["cost"] = self._cost
        self._ledger.append(usage)
        _emit_usage_sink(self, usage)
        return out


def _m2_meta(tmp_path, monkeypatch, *, send_usage=True, cost_usd=0.001):
    """Drive the REAL ``_extract_session_llm`` on the M2 lane with stub
    models that fire the REAL usage seam (no network, no provider).

    Returns ``(ledger, extracted, meta)``; ``ledger`` records what each
    served call reported (``None`` = no usage block).
    """
    from tortoise import sdk as sdk_mod
    from tortoise.sdk import TortoiseSDK, _session_llm_extractor

    ledger: list = []
    point = _UsageReportingSessionModel(
        "point-model", prompt_tokens=100, completion_tokens=10,
        cost_usd=cost_usd, ledger=ledger, send_usage=send_usage)
    relation = _UsageReportingSessionModel(
        "rel-model", prompt_tokens=200, completion_tokens=20,
        cost_usd=cost_usd, ledger=ledger, send_usage=send_usage)
    extractor = _session_llm_extractor(point, relation)
    monkeypatch.setattr(sdk_mod, "_build_session_llm_extractor",
                        lambda: extractor)
    sdk_obj = TortoiseSDK(db_path=str(tmp_path / "m2-cost.db"))
    extracted, meta = sdk_obj._extract_session_llm(
        _conv(), "sess-m2-cost", "2026-09-27T00:00:00+00:00")
    return ledger, extracted, meta


def test_m2_usage_sink_prices_the_spend_the_lane_incurred(tmp_path, monkeypatch):
    """#3747: the M2 lane makes real billed calls, and before this fix it
    dropped their usage — so the #3359 row could only DISCLOSE the calls
    (#3824 ``unattributed``), never price them. With the #2185 sink bound on
    the lane's models, the tokens/charge reach ``meta["stats"]["llm"]`` and
    the emitted row carries them.

    REDs on: removing the sink attachment (``stats`` collapses back to the
    unpriced disclosure) and on any dropped/zeroed measured field.
    """
    from tortoise import hosted_api as ha

    ledger, extracted, meta = _m2_meta(tmp_path, monkeypatch)
    assert extracted, "the M2 lane must really have run"
    assert len(ledger) == 2, "point + relation stage, one call each"
    assert all(u is not None for u in ledger), "the stub reported usage"

    expected_cost = round(sum(u.get("cost") or 0.0 for u in ledger), 6)
    llm = meta["stats"]["llm"]
    assert llm["calls"] == len(ledger)
    assert llm["prompt_tokens"] == sum(u["prompt_tokens"] for u in ledger)
    assert llm["completion_tokens"] == sum(
        u["completion_tokens"] for u in ledger)
    assert llm["cost_usd"] == pytest.approx(expected_cost, abs=1e-9)
    # a fully-metered capture attributes every call — no residual disclosure
    assert "unattributed" not in meta["stats"]

    props = ha._capture_cost_props("sess-m2-cost", meta)
    assert props is not None
    assert props["calls"] == len(ledger)
    assert props["prompt_tokens"] == 300
    assert props["completion_tokens"] == 30
    assert props["cost_usd"] == pytest.approx(expected_cost, abs=1e-9)
    assert props["calls_without_usage"] == 0
    assert props["unattributed"] == 0
    assert set(props["by_stage"]) == {"m2"}


def test_m2_a_raising_usage_sink_must_not_erase_the_call():
    """#5822 review P2 — the ORDER of the count inside the sink is load-bearing.

    ``_emit_usage_sink`` swallows an accumulator raise by design ("a metering
    observer must NEVER flip a call outcome"), so a MALFORMED provider payload
    raises INSIDE ``_accumulate_call_cost`` — ``{"prompt_tokens": "abc"}``
    trips ``int("abc")``, and a JSON ``1e309`` parses to ``inf`` and trips
    ``int(inf)``. The point is that such a payload is well-formed JSON from a
    provider, not a hand-built object.

    If the sink bumped ``attempts`` BEFORE accumulating, the caller would see
    ``llm_calls == calls_made``, compute ``unattributed = max(0, calls_made -
    calls) == 0``, leave the roll-up empty, and have ``_capture_cost_props``
    return ``None`` — ERASING every call from the report. That is strictly
    worse than the #3824 ``unattributed`` disclosure the lane had before this
    sink existed: the fix must never launder the blind spot into silence.
    Counting AFTER keeps the residual honest.

    REDs on: counting before accumulating (the pre-review order).
    """
    from tortoise.sdk import _session_llm_usage_sink

    bad_payloads = (
        {"prompt_tokens": "abc"},          # int("abc") -> ValueError
        {"prompt_tokens": float("inf")},   # JSON 1e309 -> int(inf) -> OverflowError
        {"completion_tokens": "def"},
        {"completion_tokens": float("inf")},
    )
    for bad in bad_payloads:
        stats: dict = {}
        sink = _session_llm_usage_sink(stats)
        with contextlib.suppress(Exception):
            # the emitter suppresses it; we only care about the resulting state
            sink(provider="openai", model_id="m",
                 usage=bad, usage_present=True)
        assert stats.get("attempts", 0) == 0, (
            "a raise inside the accumulator must leave the count untouched, so "
            "the caller's residual still discloses the call — got "
            f"{stats!r} for {bad!r}")


def test_accumulate_call_cost_is_atomic_on_a_bad_charge():
    """#5822 review P3 — a payload the PROVIDER controls must never half-land.

    ``cost_usd`` is the last value ``_accumulate_call_cost`` coerces, and it
    used to be coerced AFTER the token counters were bumped. A well-formed-JSON
    usage block with a string ``cost`` (``"abc"``) or an integer with 400
    digits (``float()`` OverflowError) therefore left the failing call's TOKENS
    in ``stats['cost']`` while ``by_route`` was never created. The M2 sink
    counts AFTER this function returns, so the same call was disclosed as
    ``unattributed`` AND priced into the row's top-level ``prompt_tokens`` —
    which then contradicted the row's own priced ``by_stage`` breakdown.

    Atomicity is the invariant: a call that cannot be parsed contributes
    NOTHING, so the residual can disclose it cleanly.

    REDs on: coercing ``cost_usd`` after the token mutations (the pre-review
    order) — ``stats['cost']['prompt_tokens']`` reads 200 instead of 0.
    """
    from tortoise.extractor_v2 import _accumulate_call_cost

    for label, make_bad_cost in (("non-numeric", lambda: "abc"),
                                 ("overflowing", lambda: float(10 ** 400))):
        stats: dict = {}
        with contextlib.suppress(Exception):
            # NB: the bad value must be produced INSIDE the guard — building the
            # tuple eagerly would raise while constructing it, not in the call.
            _accumulate_call_cost(
                stats, prompt_tokens=200, completion_tokens=20,
                cost_usd=make_bad_cost(), provider="openrouter",
                model="point-model")
        cost = stats.get("cost", {})
        assert cost.get("prompt_tokens", 0) == 0, (
            "a call whose charge cannot be parsed must contribute no tokens — "
            f"got {cost!r} for a {label} cost")
        assert cost.get("completion_tokens", 0) == 0, cost
        assert cost.get("calls", 0) == 0, cost
        assert "by_route" not in cost, (
            f"a partial accumulation must not create a route bucket: {cost!r}")
        assert stats.get("attempts", 0) == 0, stats


def test_m2_bad_charge_does_not_contradict_the_row_it_is_disclosed_on():
    """The end-to-end form of the atomicity invariant, on the lane that owns
    the defect: after one GOOD call and one whose ``cost`` cannot be parsed,
    the emitted row's top-level token count must agree with its priced
    ``by_stage`` breakdown — the failing call's tokens must appear NOWHERE in
    the priced totals, only in the ``unattributed`` count.
    """
    from tortoise.sdk import _session_llm_usage_sink

    stats: dict = {}
    sink = _session_llm_usage_sink(stats)
    # call 1: priced normally
    sink(provider="openrouter", model_id="point-model",
         usage={"prompt_tokens": 100, "completion_tokens": 10,
                "cost": 0.001}, usage_present=True)
    # call 2: valid tokens, unparseable charge -> must land NOWHERE
    with contextlib.suppress(Exception):
        sink(provider="openrouter", model_id="point-model",
             usage={"prompt_tokens": 200, "completion_tokens": 20,
                    "cost": "abc"}, usage_present=True)

    cost = stats["cost"]
    assert cost["prompt_tokens"] == 100, cost
    assert cost["completion_tokens"] == 10, cost
    assert cost["calls"] == 1, cost
    assert cost["cost_usd"] == 0.001, cost
    # the priced breakdown must agree with the totals (the P3 contradiction)
    assert cost["by_route"]["openrouter"]["point-model"]["prompt_tokens"] == 100
    # and the residual the caller derives is exactly the unpriced call
    assert max(0, 2 - stats.get("attempts", 0)) == 1


def test_accumulate_call_cost_rejects_a_non_finite_charge():
    """#5822 review P3 — ``float()`` does NOT raise on ``inf``/``nan``, so the
    non-finite charge is the one provider-controlled ``cost`` that slips past
    both earlier guards and lands on the row.

    Consequences, both reproduced: ``_track_analytics_event`` encodes with
    httpx's ``allow_nan=False``, so one non-finite value raises ``ValueError``
    and the capture_cost row is written ONLY to the local JSONL fallback — it
    never reaches ``analytics_events``, the table
    ``cost_per_session_distribution`` scans. And because ``round(nan + x, 6)``
    stays ``nan``, a single ``nan`` SWALLOWS every later valid charge.

    An unusable charge must be treated exactly like an absent one: disclosed
    via ``calls_without_cost``, never a non-finite row — while the TOKENS are
    still kept so the row stays repricable from the pricing map.

    REDs on: accepting the parsed value unconditionally (the pre-review order).
    """
    from tortoise.extractor_v2 import _accumulate_call_cost

    for label, bad in (("json 1e400 -> inf", float("inf")),
                       ("-inf", float("-inf")),
                       ("nan", float("nan")),
                       ("string nan", "nan")):
        stats: dict = {}
        _accumulate_call_cost(
            stats, prompt_tokens=100, completion_tokens=10, cost_usd=bad,
            provider="openrouter", model="point-model")
        cost = stats["cost"]
        assert math.isfinite(cost.get("cost_usd", 0.0)), (
            f"a non-finite charge must never land on the row ({label}): {cost!r}")
        assert cost.get("cost_usd", 0.0) == 0.0, cost
        assert cost["calls_without_cost"] == 1, (
            f"an unusable charge is disclosed as without-cost ({label}): {cost!r}")
        assert cost["prompt_tokens"] == 100, cost   # tokens survive for repricing
        assert cost["completion_tokens"] == 10, cost

    # and the sharper half: a nan must not swallow a LATER valid charge
    stats = {}
    _accumulate_call_cost(stats, prompt_tokens=100, completion_tokens=10,
                          cost_usd="nan", provider="p", model="m")
    _accumulate_call_cost(stats, prompt_tokens=10, completion_tokens=1,
                          cost_usd=0.001, provider="p", model="m")
    cost = stats["cost"]
    assert cost["cost_usd"] == 0.001, (
        "a poisoned session total must not swallow the next valid charge: "
        f"{cost!r}")
    assert math.isfinite(cost["cost_usd"]), cost
    assert cost["calls_without_cost"] == 1, cost


def test_accumulate_call_cost_bounds_the_accumulated_total():
    """#5822 cycle-4 P3 — guarding the OPERAND cannot bound the RESULT.

    Both charges here are FINITE, so the ``math.isfinite(cost_val)`` guard
    passes them; only their SUM overflows. ``1e308 + 1e308 == inf``, and the
    consequences are exactly the ones the non-finite-input fix addressed: the
    emitted row raises ``ValueError`` under httpx's ``allow_nan=False`` and is
    dropped from ``analytics_events`` (surviving only in the JSONL fallback),
    and ``inf + x == inf`` swallows every later valid charge in the session.

    The overflowed charge is disclosed instead of written.

    REDs on: summing into ``acc``/``lane`` without re-checking finiteness.
    """
    from tortoise.extractor_v2 import _accumulate_call_cost

    stats: dict = {}
    for _ in range(2):
        _accumulate_call_cost(
            stats, prompt_tokens=100, completion_tokens=10, cost_usd=1e308,
            provider="openrouter", model="point-model")

    cost = stats["cost"]
    assert math.isfinite(cost["cost_usd"]), (
        f"the accumulated total must never be inf: {cost!r}")
    assert cost["cost_usd"] == 1e308, cost      # the first, representable charge
    assert cost["calls"] == 2, cost             # both calls still counted
    assert cost["calls_without_cost"] == 1, (
        f"the overflowed charge is disclosed, not written: {cost!r}")

    lane = cost["by_route"]["openrouter"]["point-model"]
    assert math.isfinite(lane["cost_usd"]), lane
    assert lane["calls_without_cost"] == 1, lane

    # and the row must remain JSON-encodable exactly as the analytics sink does
    assert math.isfinite(json.loads(json.dumps(cost))["cost_usd"]), cost


def test_rollup_does_not_reintroduce_a_non_finite_total():
    """#5822 cycle-5 P3 — the AGGREGATION seam undoes a per-stage guard.

    ``_accumulate_call_cost`` now bounds its own running total, but
    ``_rollup_llm`` is called ONCE PER STAGE into the same ``llm_stats``, and it
    re-summed with a plain ``round(a + b, 6)``. Two stages whose totals are each
    finite (``1e308``) therefore overflow at the roll-up, and the emitted row
    carries ``inf`` again: httpx encodes with ``allow_nan=False``, so the row is
    dropped from ``analytics_events``, and ``inf + x == inf`` swallows every
    later charge.

    Reachable on the DEFAULT v2 lane (a provider reporting ``usage.cost``), not
    only the opt-in M2 lane.

    REDs on: summing the cross-stage total without re-checking finiteness.
    """
    from tortoise.extractor_v2 import _rollup_llm

    llm: dict = {"calls": 0, "retries": 0, "truncated": 0,
                 "deadline_aborts": 0}
    for stage in ("s1", "s2"):
        _rollup_llm(
            llm,
            {"cost": {"calls": 1, "prompt_tokens": 100,
                      "completion_tokens": 10, "cost_usd": 1e308}},
            stage=stage)

    assert math.isfinite(llm["cost_usd"]), (
        f"the rolled-up total must never be inf: {llm!r}")
    assert llm["cost_usd"] == 1e308, llm     # the first representable total
    assert llm["calls_without_cost"] == 1, (
        f"the unrepresentable aggregate is disclosed: {llm!r}")
    # the row must survive the exact encoding the analytics sink performs
    assert math.isfinite(json.loads(json.dumps(llm))["cost_usd"]), llm


# ── #5854: the token fields are validated, never shaped ─────────────────────

def _one_token_call(usage: dict):
    """Drive ONE provider usage block through the REAL M2 lane:
    ``_session_llm_usage_sink`` -> ``_rollup_llm`` -> ``_capture_cost_props``
    -> ``cost_per_session_distribution``. Returns
    ``(llm, props, distribution)`` — the emitted row, not a mock of it."""
    from tortoise import hosted_api as ha
    from tortoise.extractor_v2 import _rollup_llm
    from tortoise.sdk import _session_llm_usage_sink

    stats: dict = {}
    _session_llm_usage_sink(stats)(
        provider=_PROVIDER, model_id=_MODEL, usage=usage, usage_present=True)
    llm: dict = {"calls": 0, "retries": 0, "truncated": 0,
                 "deadline_aborts": 0, "by_stage": {}}
    _rollup_llm(llm, stats, "m2")
    props = ha._capture_cost_props("sess-5854", {"stats": {"llm": llm}})
    assert props is not None
    dist = costing.cost_per_session_distribution([{"properties": props}])
    return llm, props, dist


def test_token_normaliser_positive_control_records_a_well_formed_usage():
    """CONTROL — the guard must not narrow the VALID path. A well-formed
    usage block still records its tokens and charge and discloses nothing.
    Without this, a guard that rejected everything would look like a fix."""
    _llm, props, dist = _one_token_call(
        {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.001})

    assert props["prompt_tokens"] == 100
    assert props["completion_tokens"] == 10
    assert props["cost_usd"] == pytest.approx(0.001, abs=1e-9)
    assert props["calls_without_tokens"] == 0
    assert dist["total_usd"] == pytest.approx(0.001, abs=1e-9)
    assert dist["fully_priced"] is True


def test_token_normaliser_rejects_a_bool_instead_of_fabricating_one():
    """#5854 row 1: ``{"prompt_tokens": true}`` used to emit
    ``prompt_tokens: 1`` — a token FABRICATED from a boolean (``True`` IS
    ``1`` in Python). It must be rejected and DISCLOSED, while the valid
    sibling token and the charge survive untouched.

    REDs on: the pre-fix ``int(prompt_tokens or 0)`` (emits 1, no counter)."""
    _llm, props, _dist = _one_token_call(
        {"prompt_tokens": True, "completion_tokens": 10, "cost": 0.001})

    assert props["prompt_tokens"] == 0          # not the fabricated 1
    assert props["completion_tokens"] == 10     # the valid sibling is kept
    assert props["cost_usd"] == pytest.approx(0.001, abs=1e-9)
    assert props["calls_without_tokens"] == 1


def test_token_normaliser_rejects_a_float_rather_than_truncating():
    """#5854 row 2 and the issue's OPEN DECISION: a JSON float is REJECTED +
    DISCLOSED, not floored. Flooring is the same silent-shaping defect class
    the issue is about, so it cannot be the fix.

    REDs on: the pre-fix ``int(100.9)`` (emits 100/10, no counter)."""
    _llm, props, _dist = _one_token_call(
        {"prompt_tokens": 100.9, "completion_tokens": 10.9, "cost": 0.001})

    assert props["prompt_tokens"] == 0          # not the truncated 100
    assert props["completion_tokens"] == 0      # not the truncated 10
    assert props["calls_without_tokens"] == 1


def test_token_normaliser_rejects_negatives_and_cannot_price_a_negative():
    """#5854 row 3, the sharpest: a negative token count used to reach the
    dollar total through ``_price_lane._tok`` and return a NEGATIVE session
    cost that the row still declared ``fully_priced``. Rejected at the
    accumulator, and the reader refuses it from already-stored data too.

    REDs on: the pre-fix ``int(-1000)`` (total -0.000168, fully_priced
    True)."""
    _llm, props, dist = _one_token_call(
        {"prompt_tokens": -1000, "completion_tokens": -100})

    assert props["prompt_tokens"] == 0
    assert props["completion_tokens"] == 0
    assert props["cost_usd"] == 0.0
    assert props["calls_without_tokens"] == 1
    # no usable measurement -> EXCLUDED, never a negative and never fully priced
    assert dist["total_usd"] >= 0.0
    assert dist["n"] == 0
    assert dist["excluded_unmeasured"] == 1
    assert dist["fully_priced"] is False


def test_token_normaliser_rejects_an_absurd_magnitude():
    """A magnitude no real generation can reach is rejected, mirroring the
    reader's own ``abs > 1e300`` bound — otherwise it prices a session at
    astronomically more than was ever spent.

    REDs on: the pre-fix ``int(10 ** 400)`` (a shaped, enormous token)."""
    _llm, props, _dist = _one_token_call({"prompt_tokens": 10 ** 400})

    assert props["prompt_tokens"] == 0
    assert props["calls_without_tokens"] == 1


def test_token_normaliser_keeps_the_non_finite_raise():
    """The #5822 atomic contract is preserved for a value ``int()`` cannot
    represent at all: ``inf``/``nan`` (and a non-numeric string) still RAISE
    out of the accumulator, so the caller's residual discloses the call
    (``test_m2_a_raising_usage_sink_must_not_erase_the_call``) rather than a
    counter turning it into a $0-token row. The SHAPING values — bool,
    fractional, negative, absurd — are the ones #5854 counters."""
    from tortoise.extractor_v2 import _accumulate_call_cost

    for bad in (float("inf"), float("-inf"), float("nan"), "abc"):
        stats: dict = {}
        with pytest.raises((ValueError, OverflowError, TypeError)):
            _accumulate_call_cost(
                stats, prompt_tokens=bad, completion_tokens=1,
                cost_usd=0.001, provider="p", model="m")
        assert "prompt_tokens" not in stats.get("cost", {}), stats


def test_token_normaliser_unit_rejects_the_shapeable_and_keeps_a_valid_zero():
    """The normaliser's contract, directly. A valid ``0`` is NOT a rejection
    (so the counter cannot fire on an honest zero), and a number-LIKE
    non-number (a numeric string, a ``Decimal``) is rejected rather than
    parsed — ``int()`` would shape a count out of it."""
    from decimal import Decimal

    from tortoise.extractor_v2 import _normalise_token_count

    # rejected: the silently-shaping class
    assert _normalise_token_count(True) is None
    assert _normalise_token_count(False) is None
    assert _normalise_token_count(100.9) is None
    assert _normalise_token_count(-1000) is None
    assert _normalise_token_count(10 ** 400) is None
    assert _normalise_token_count("100") is None
    assert _normalise_token_count(Decimal("100.9")) is None

    # absent is None, but an honest zero is a VALID count
    assert _normalise_token_count(None) is None
    assert _normalise_token_count(0) == 0
    assert _normalise_token_count(0.0) == 0
    assert _normalise_token_count(100) == 100
    assert _normalise_token_count(100.0) == 100


def test_token_rejection_rides_the_emitted_row_and_the_allowlist():
    """The disclosure must SURVIVE the PII filter — an unregistered key is
    stripped at the writer, the documented #3359 silent-loss mode — and it
    must roll to the session level beside the sibling counters."""
    from tortoise import hosted_api as ha

    _llm, props, _dist = _one_token_call(
        {"prompt_tokens": True, "completion_tokens": 10})

    assert props["calls_without_tokens"] == 1
    assert set(props) <= ha._ALLOWED_ANALYTICS_PROPS
    assert "calls_without_tokens" in ha._ALLOWED_ANALYTICS_PROPS


def test_reader_refuses_a_stored_negative_token_instead_of_a_silent_zero():
    """#5854 reader path. Rows written BEFORE this fix are already in
    ``analytics_events``, so the accumulator fix alone cannot reach them.
    ``_price_lane`` must reject the negative AND mark the lane UNPRICED — a
    silent $0 would defeat the same premise the negative did."""
    stored = {"properties": {
        "session_id": "sess-stored-neg", "calls": 1,
        "prompt_tokens": -1000, "completion_tokens": -100,
        "cost_usd": 0.0, "calls_without_cost": 1,
        "calls_without_usage": 0, "calls_without_tokens": 0,
        "deadline_aborts": 0, "unattributed": 0,
        "by_stage": {"m2": {_PROVIDER: {_MODEL: {
            "calls": 1, "prompt_tokens": -1000,
            "completion_tokens": -100, "cost_usd": 0.0,
            "calls_without_cost": 1, "calls_without_usage": 0,
            "calls_without_tokens": 0, "usage_present": True}}}}}}

    dist = costing.cost_per_session_distribution([stored])
    assert dist["total_usd"] >= 0.0            # never sign-flipped
    assert dist["p50"] >= 0.0
    assert dist["unpriced_sessions"] == 1      # DISCLOSED, not a silent $0
    assert dist["fully_priced"] is False


def test_v2_lane_cannot_shape_a_bool_token_either():
    """The accumulator fix alone is not enough on the DEFAULT v2 lane:
    ``_call_once`` used to ``int()`` the counts BEFORE the accumulator saw
    them, so a bool was already a fabricated ``1``. This drives the REAL
    ``extract_session_v2`` path with a model reporting a bool.

    REDs on: restoring ``int(getattr(model, "last_prompt_tokens", None) or
    0)`` in ``_call_once`` (3 fabricated tokens, no counter)."""
    from tortoise import hosted_api as ha

    model = CostReportingModel(prompt_tokens=True, completion_tokens=10)
    out = v2.extract_session_v2(model, _conv())
    llm = out["stats"]["llm"]

    assert llm["calls"] == 3                    # three real calls happened
    assert llm["prompt_tokens"] == 0            # not 3 fabricated tokens
    assert llm["completion_tokens"] == 30
    assert llm["calls_without_tokens"] == 3

    props = ha._capture_cost_props("sess-v2-5854", out)
    assert props is not None
    assert props["prompt_tokens"] == 0
    assert props["calls_without_tokens"] == 3


def test_m2_missing_usage_block_is_disclosed_never_fabricated(
        tmp_path, monkeypatch):
    """A provider response with NO usage block must not be turned into a
    measurement: the lane reports ZERO tokens/charge and DISCLOSES the calls
    (``calls_without_usage``), so the row is excluded from the priced
    distribution rather than reading as a fabricated $0 sample.

    REDs on: a sink that invents tokens/charge when ``usage`` is ``None``
    (the fabricated block would both raise ``prompt_tokens`` and drop
    ``calls_without_usage`` to 0, putting the row INTO the distribution).
    """
    from tortoise import hosted_api as ha

    ledger, extracted, meta = _m2_meta(tmp_path, monkeypatch, send_usage=False)
    assert extracted and len(ledger) == 2
    assert all(u is None for u in ledger)

    llm = meta["stats"]["llm"]
    assert llm["calls"] == 2            # the calls really happened
    assert llm["prompt_tokens"] == 0    # and no token was invented
    assert llm["completion_tokens"] == 0
    assert llm["cost_usd"] == 0.0
    assert llm["calls_without_usage"] == 2
    assert llm["calls_without_cost"] == 2
    assert all(bucket["usage_present"] is False
               for providers in llm["by_stage"].values()
               for models_ in providers.values()
               for bucket in models_.values())

    props = ha._capture_cost_props("sess-m2-cost", meta)
    assert props is not None
    assert props["prompt_tokens"] == 0 and props["cost_usd"] == 0.0

    dist = costing.cost_per_session_distribution([{"properties": props}])
    assert dist["n"] == 0                       # NOT priced as a $0 session
    assert dist["excluded_unmeasured"] == 1      # disclosed, never measured
    assert dist["calls_without_usage"] == 2


def test_m2_failed_extraction_still_reports_the_spend_it_incurred(
        tmp_path, monkeypatch):
    """A ``run()`` that raises AFTER a successful billed call must still
    report that call's usage: the spend is real whether or not the extraction
    succeeded. The roll-up is read after the fail-closed try/except, so the
    accumulator survives a provider 500 — the same reason the #3824 call
    counter is read there."""
    from tortoise import hosted_api as ha
    from tortoise import sdk as sdk_mod
    from tortoise.sdk import TortoiseSDK, _session_llm_extractor

    ledger: list = []
    extractor = _session_llm_extractor(
        _UsageReportingSessionModel("point-model", ledger=ledger),
        _UsageReportingSessionModel("rel-model", ledger=ledger))

    class _CallThenBoom:
        version = extractor.version
        _call_counters = extractor._call_counters
        _cost_stats = extractor._cost_stats

        def run(self, transcript, source_id, api):
            extractor.points.model.complete(
                system="extract_points json",
                user=json.dumps({"utterances": {}}))
            raise RuntimeError("provider 500 after the first billed call")

    monkeypatch.setattr(sdk_mod, "_build_session_llm_extractor",
                        lambda: _CallThenBoom())
    sdk_obj = TortoiseSDK(db_path=str(tmp_path / "m2-boom.db"))
    extracted, meta = sdk_obj._extract_session_llm(
        _conv(), "sess-m2-boom", "2026-09-27T00:00:00+00:00")

    assert extracted == []
    assert meta["mode"] == "error"
    assert any("RuntimeError" in e for e in meta["errors"])
    llm = meta["stats"]["llm"]
    assert llm["calls"] == 1                 # the one call that landed
    assert llm["prompt_tokens"] == 100
    props = ha._capture_cost_props("sess-m2-boom", meta)
    assert props is not None and props["calls"] == 1
    assert props["prompt_tokens"] == 100


def test_m2_real_extractor_stamps_the_configured_provider(monkeypatch):
    """The REAL M2 model build must carry its provider id, or the emitted
    row's ``(provider, model)`` lane is ``unknown`` and a cost-SILENT
    provider (deepseek-direct reports no ``usage.cost``) can never be
    repriced from the versioned map — the #3359 report path's whole point.

    Builds models only (no call, no network): ``OpenAICompatModel`` carries
    no provider of its own, so this is the only place it is known.
    """
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MODEL", raising=False)
    for env_name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-no-call")

    from tortoise.sdk import _build_session_llm_extractor

    extractor = _build_session_llm_extractor()
    assert extractor is not None
    assert extractor.points.model.provider == "openrouter"
    assert extractor.relations.model.provider == "openrouter"


def test_m2_capture_makes_calls_so_it_is_counted_not_absent(
        tmp_path, monkeypatch, _b7_capture_client):
    """#3824 — THE EMISSION ACCEPTANCE. An M2 capture issues real provider
    calls; under this fixture's offline mock seam (``TORTOISE_SESSION_LLM_MOCK``)
    the model exposes NO #2185 usage seam, so the lane produces no PRICED
    ``llm`` roll-up — only the #3824 call-evidence disclosure. Driving the
    REAL REST handler, the row handed to the REAL analytics writer must EXIST
    and deep-equal the payload the capture implies, with ``unattributed >= 1``
    — never be absent. Absence is what made billed spend and a clean $0 the
    same shape, and #3780's cohort-cap denominator was set from that
    undercount.

    NOTE (updated by #3747): the absence of ``llm`` HERE is a property of the
    mock's model, not of the lane — the real-provider M2 path now DOES price
    its spend (``test_m2_usage_sink_prices_the_spend_the_lane_incurred``). The
    F2 invariant this test protects is unchanged: an unaccounted-for call is
    disclosed on a row, never erased.

    REDs on: restoring ``return None`` for a ``stats`` with no ``llm``
    roll-up (the collapse), or dropping the producer's call evidence at the
    model boundary — the row disappears either way.
    """
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")

    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-unattributed"})
    assert resp.status_code == 200, resp.text
    # Pin the premise: the M2 lane really extracted (extracted > 0) and its
    # mock model produced no PRICED roll-up — this is F2, not F1.
    assert resp.json()["extracted"] > 0
    assert "llm" not in (resp.json()["stats"] or {})

    rows = [r for r in _b7_rows(tmp_path)
            if r.get("event_name") == "capture_cost"]
    assert len(rows) == 1, rows
    props = rows[0]["properties"]
    assert props["unattributed"] >= 1
    # Whole-payload deep-equal: a zeroed or dropped measured field, or a
    # missing/renamed key, fails even though ``unattributed`` is right. The
    # count itself is lane chunking's property (any >= 1 is the contract).
    assert props == {
        "session_id": "sess-b7-unattributed",
        "calls": 0, "retries": 0,
        "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        "calls_without_cost": 0, "calls_without_usage": 0,
        "calls_without_tokens": 0,
        "deadline_aborts": 0, "by_stage": {},
        "unattributed": props["unattributed"],
    }


def test_hosted_replay_emits_no_cost_row_even_though_m2_does(
        tmp_path, monkeypatch, _b7_capture_client):
    """A REPLAY (the same ``session_id`` POSTed twice) must emit no second
    ``capture_cost`` row: nothing ran, so any row would be a phantom $0.

    REDs on: a phantom replay row — i.e. BOTH guards that stop one removed
    together, the emitter's F1 ``None`` AND the call-site
    ``if not session_existed or retry_failed_capture:``. VERIFIED: dropping
    either one ALONE leaves this GREEN, so this is deliberately an
    END-TO-END assertion of the observable contract rather than a
    single-mutation pin (the F1 gate has its own in
    ``test_capture_cost_props_none_only_for_a_genuinely_call_free_capture``).
    The scope's stated T2 mutation — "drop the ``if not llm`` gate ...
    fabricating a measured $0 for a replay" — does NOT hold on its own: the
    replay never reaches the emitter, because the emit call site sits behind
    the replay guard. The two-guard conjunction is
    what actually REDs this.

    #3745 authored the absence pin this replaces, and its own docstring
    disclaimed endorsing the M2 blind spot ("NOT endorsed here"). The M2
    half of that blind spot is now closed by #3824 (the M2 lane's calls ARE
    counted); what remains — a ZERO-CALL replay emitting nothing — is the
    contract asserted here, not deleted.
    """
    from tortoise import hosted_api as ha

    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")

    def _capture():
        return _b7_capture_client.post("/v1/sessions", json={
            "conversation": _conv(), "harness": "pi",
            "session_id": "sess-b7-replay"})

    def _cost_rows():
        return [r for r in _b7_rows(tmp_path)
                if r.get("event_name") == "capture_cost"]

    first = _capture()
    assert first.status_code == 200, first.text
    # The M2 capture itself DID emit (that is #3824's other half) — so the
    # absence asserted below is the REPLAY guard, not a dead writer.
    assert len(_cost_rows()) == 1, _cost_rows()

    # Positive control for the writer: a sentinel through the REAL writer
    # lands where _b7_rows reads, so a missing row is the handler's guard.
    ha._track_analytics_event(_B7_TEAM["org_id"], "b7_sentinel", {})
    assert any(r.get("event_name") == "b7_sentinel" for r in _b7_rows(tmp_path))

    second = _capture()
    assert second.status_code == 200, second.text
    assert second.json()["extraction_mode"] == "replayed"
    # The replay's OWN telemetry is empty and it added no row.
    assert second.json()["stats"] == {}
    assert len(_cost_rows()) == 1, _cost_rows()


def test_emit_call_site_writes_no_cost_row_when_props_is_none(
        tmp_path, monkeypatch, _b7_capture_client):
    """The emit call site's ``if _cost_props is not None:`` guard is what
    actually decides whether a ``capture_cost`` row is written, and #3824's
    rewrite of the old absence pin left it uncovered on its NEGATIVE branch:
    an F1 capture (``_capture_cost_props`` -> ``None``) must write no row, or
    a zero-cost phantom row is fabricated for a capture with no measurement.

    REDs on: dropping the ``is not None`` guard (``if True:``), which writes a
    row with ``properties == {}`` — the silent-measurement failure #3359 /
    #3745 exist to prevent, and a real path on merged main (#3892: a keyless
    capture stores its turns and reaches this guard).

    The M2 lane is driven with its #3824 call counters ABSENT, so the capture
    genuinely extracts and genuinely emits ``stats == {}`` — an F1 shape
    driven through the producer seam, not a monkeypatched emitter.
    """
    from tortoise import hosted_api as ha
    from tortoise import sdk as sdk_mod
    from tortoise.extractor import LLMExtractor, MockModel

    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    # The pre-#3824 M2 shape: an extractor that makes calls but carries no
    # call evidence, so the emitter sees an empty ``stats`` and returns None.
    monkeypatch.setattr(
        sdk_mod, "_build_session_llm_extractor",
        lambda: LLMExtractor(MockModel("mock-point"),
                             MockModel("mock-relation")))

    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-f1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["extracted"] > 0        # the lane really ran
    assert resp.json()["stats"] == {}          # ... with no roll-up: F1

    # Positive control: the writer is live, so an absent row is the GUARD and
    # not a dead writer.
    ha._track_analytics_event(_B7_TEAM["org_id"], "b7_f1_sentinel", {})
    assert any(r.get("event_name") == "b7_f1_sentinel"
               for r in _b7_rows(tmp_path))
    assert [r for r in _b7_rows(tmp_path)
            if r.get("event_name") == "capture_cost"] == []


def test_unattributed_survives_the_pii_allowlist(tmp_path, monkeypatch):
    """The #3824 disclosure has to survive the PII allowlist, or the report
    can never see it — the documented #3359 allowlist-strips-the-measurement
    failure mode, which leaves the ROW present and the FACT absent.

    REDs on: removing ``unattributed`` from ``_ALLOWED_ANALYTICS_PROPS``.
    """
    from tortoise import hosted_api as ha

    props = ha._capture_cost_props("sess-m2", {"stats": {"unattributed": 2}})
    assert props is not None
    assert props["unattributed"] == 2
    assert "unattributed" in ha._ALLOWED_ANALYTICS_PROPS

    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setattr(ha, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics.jsonl"))
    ha._track_analytics_event("team-1", "capture_cost", props)
    rec = json.loads((tmp_path / "analytics.jsonl").read_text()
                     .strip().splitlines()[-1])
    assert rec["properties"]["unattributed"] == 2


def test_unattributed_calls_enter_the_denominator_and_are_named():
    """#3824 — THE POINT IS THE DENOMINATOR. A capture that reached the
    provider with no surviving roll-up used to contribute NOTHING to the
    distribution: no row, so no ``n_rows``, no ``n``, no ``excluded_*``, and
    — because ``unmetered`` is computed WITHIN a row — no
    ``unmetered_attempts``. The cohort cap read off that figure under-
    refuses in exactly the case it exists to catch.

    REDs on: leaving ``costing.cost_per_session_distribution`` untouched —
    the disclosed calls land in no counter, and the F2 session is mislabelled
    ``excluded_no_calls`` (the undercount, reproduced).
    """
    from tools.longmem_eval import costing

    # F2: two calls made, nothing meterable, no roll-up survived.
    unrolled = {"properties": {"session_id": "m2", "calls": 0,
                              "cost_usd": 0.0, "calls_without_cost": 0,
                              "calls_without_usage": 0, "deadline_aborts": 0,
                              "unattributed": 2, "by_stage": {}}}
    measured = _row("real", 0.004, {"s1": {_PROVIDER: {_MODEL: {
        "calls": 1, "prompt_tokens": 100, "completion_tokens": 10,
        "cost_usd": 0.004, "usage_present": True,
        "calls_without_usage": 0}}}})

    dist = costing.cost_per_session_distribution([unrolled, measured])

    assert dist["n_rows"] == 2             # the row is COUNTED, not invisible
    assert dist["n"] == 1                  # ... but it has no measured spend
    assert dist["unattributed_calls"] == 2
    assert dist["unattributed_captures"] == 1
    assert dist["unmetered_attempts"] == 2    # disclosed as attempts
    assert dist["excluded_unmeasured"] == 1   # NOT "no calls at all"
    assert dist["excluded_no_calls"] == 0
    assert dist["fully_priced"] is False
    assert dist["p50"] == pytest.approx(0.004, abs=1e-9)   # not dragged to 0

    # The control: a window with nothing unrolled reads clean.
    clean = costing.cost_per_session_distribution([measured])
    assert clean["unattributed_calls"] == 0
    assert clean["unattributed_captures"] == 0
    assert clean["unmetered_attempts"] == 0


def test_report_cli_discloses_unattributed_calls(tmp_path, capsys):
    """A disclosure that exists only in the distribution dict is invisible to
    the operator reading the launch-gate number — the report text and the
    ``--out`` JSON contract must name it (#3824).
    """
    from tools import capture_cost_report as report

    fixture = tmp_path / "unattributed.jsonl"
    fixture.write_text(json.dumps({
        "org_id": "org-1", "event_name": "capture_cost",
        "created_at": "2026-09-16T12:00:00+00:00",
        "properties": {"session_id": "m2", "calls": 0, "cost_usd": 0.0,
                       "calls_without_cost": 0, "calls_without_usage": 0,
                       "deadline_aborts": 0, "unattributed": 2,
                       "by_stage": {}}}) + "\n", encoding="utf-8")
    out_json = tmp_path / "unattributed.json"
    assert report.main(["--jsonl", str(fixture), "--out", str(out_json)]) == 0
    text = capsys.readouterr().out
    assert "calls with no surviving roll-up" in text
    assert "captures behind those calls" in text

    payload = json.loads(out_json.read_text())
    assert payload["measurable"] is False
    assert payload["distribution"]["unattributed_calls"] == 2
    assert payload["distribution"]["unattributed_captures"] == 1
    assert payload["distribution"]["excluded_no_calls"] == 0
    assert payload["distribution"]["excluded_unmeasured"] == 1


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
    """The emit must run OFF the event loop, on the dedicated ``telemetry``
    pool: ``_track_analytics_event`` POSTs synchronously and the API runs a
    single uvicorn worker, so an inline call stalls every concurrent request
    for the duration of the Supabase round-trip (the #2988/#3498 class).
    Asserted BEHAVIOURALLY — the loop thread and the writer thread must differ,
    and the writer thread must be the shared off-loop entry point's telemetry
    pool (#4468), NOT the loop's SHARED default executor that the abuse hooks
    and the selfhost readiness probe compete on — because inlining the call
    leaves every other emission test green (with Supabase unset the write is a
    fast local append).

    The loop thread is sampled INDEPENDENTLY of the props build (via the
    awaited ``_async_audit`` seam), so a future refactor that moves the whole
    emission — props build AND write — into one worker closure still passes:
    the guarded property is "the write is off the loop", not "the write is on
    a different thread from the props build"."""
    import threading

    from tortoise import hosted_api as ha
    from tortoise import monitoring
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
        seen["emit_thread_name"] = threading.current_thread().name

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
        "handed off via the shared off-loop entry point (#4015 / #4468)")
    assert seen["emit_thread_name"].startswith(
        monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME), (
        f"the capture-lane analytics emit ran on {seen['emit_thread_name']!r} "
        f"— it must use the dedicated telemetry pool "
        f"({monitoring.CONTROL_PLANE_TELEMETRY_WORKER_NAME!r}), never the "
        "loop's shared default executor the abuse hooks compete on (#4468)")


def test_capture_lane_analytics_emit_rides_the_telemetry_pool(monkeypatch):
    """#4468: the capture lane's analytics emit is routed through the shared
    off-loop entry point (``_emit_analytics_off_loop`` → ``_cp_offload`` on the
    dedicated ``telemetry`` pool), not ``asyncio.to_thread`` on the loop's
    SHARED default executor that the abuse hooks also use.

    REDs on unpatched main: the emit is
    ``asyncio.to_thread(_track_analytics_event, …)``, which never reaches
    ``_cp_offload``, so the recorded route is empty and the assertion fails.
    """
    import asyncio

    from tortoise import hosted_api as ha

    seen: dict = {}
    emitted: list = []

    async def _record_offload(fn, *, op, best_effort=False, **kwargs):
        # Record the seam the entry point delegates to instead of running the
        # blocking POST; the callable is exercised separately below.
        seen.setdefault("offloads", []).append((op, best_effort))
        seen["fn"] = fn
        return None

    monkeypatch.setattr(ha, "_cp_offload", _record_offload)
    monkeypatch.setattr(
        ha, "_capture_cost_props",
        lambda session_id, meta: {"session_id": session_id, "cost_usd": 0.001})
    # The ledger write (the other, unchanged ``asyncio.to_thread`` call) needs
    # no real registry here; it must not short-circuit before the emit.
    monkeypatch.setattr("tortoise.metering.record_capture_usage",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        ha, "_track_analytics_event",
        lambda org_id, event_name, properties=None:
            emitted.append((org_id, event_name, properties)))

    asyncio.run(ha._emit_capture_ledger("org-4468", "sess-4468", {}))

    assert seen.get("offloads") == [("analytics_event", True)], (
        "the capture-lane analytics emit did not ride the shared off-loop "
        f"entry point on the telemetry pool (#4468): {seen.get('offloads')!r}")
    # The callable the seam was handed IS the capture_cost emit.
    seen["fn"]()
    assert emitted == [("org-4468", "capture_cost",
                        {"session_id": "sess-4468", "cost_usd": 0.001})], (
        f"the routed emit did not produce the capture_cost row: {emitted!r}")


def test_capture_lane_swallows_the_strict_mode_registration_raise(
        monkeypatch, caplog):
    """#4468: the capture lane deliberately KEEPS its own ``except Exception``
    around the emit, so the #3821 strict-mode ``UnregisteredTelemetryKey``
    that ``_emit_analytics_off_loop`` lets escape is caught HERE — a committed
    capture is never failed by bookkeeping. ``_track_onboarding_event``
    depends on that same raise, so both the escape and this swallow are
    contracts; weakening either is the defect.

    The assertion is bound to the RAISE actually reaching the handler (via
    the logged ``exc_info``), so the test cannot pass vacuously by the guard
    never firing.
    """
    import asyncio
    import logging as _logging

    from tortoise import hosted_api as ha

    monkeypatch.setenv(ha._TELEMETRY_STRICT_ENV, "1")
    monkeypatch.setattr(
        ha, "_capture_cost_props",
        lambda session_id, meta: {"cost_usd": 0.001,
                                  "unregistered_probe_key": 1})
    monkeypatch.setattr("tortoise.metering.record_capture_usage",
                        lambda *a, **k: None)

    with caplog.at_level(_logging.ERROR, logger="tortoise.api"):
        # Must NOT raise, though the strict-mode guard fires inside the emit.
        asyncio.run(ha._emit_capture_ledger("org-4468", "sess-4468", {}))

    assert any(
        record.exc_info
        and isinstance(record.exc_info[1], ha.UnregisteredTelemetryKey)
        for record in caplog.records), (
        "the strict-mode raise never reached the capture lane's handler — "
        "either the test is vacuous or the escape was weakened (#4468)")



def test_hosted_capture_emits_that_session_s_graph_op_row(
        tmp_path, monkeypatch, _b7_capture_client):
    """#3561/#3359 emission-boundary acceptance: the REAL REST capture handler
    emits a ``capture_graph_ops`` row through the REAL analytics writer, with a
    NON-ZERO op count and a phase split that reconciles.

    REDs on: dropping the ``@counts_capture_ops_async`` decorator, deleting the
    emit at the end of ``_capture_session_impl``, or a guard that never fires —
    each of which leaves the meter silently emitting nothing in production
    (the exact loss this measurement exists to prevent)."""
    resp = _b7_capture_client.post("/v1/sessions", json={
        "conversation": _conv(), "harness": "pi",
        "session_id": "sess-b7-graphops"})
    assert resp.status_code == 200, resp.text

    rows = [r for r in _b7_rows(tmp_path)
            if r.get("event_name") == "capture_graph_ops"]
    assert len(rows) == 1, rows
    assert rows[0]["org_id"] == _B7_TEAM["org_id"]
    props = rows[0]["properties"]
    assert props["session_id"] == "sess-b7-graphops"
    assert props["graph_ops_total"] > 0, "capture emitted no graph ops"
    by_phase = props["graph_ops_by_phase"]
    assert set(by_phase) == {"session_store", "extraction", "commit", "belief"}
    assert by_phase["session_store"]["total"] > 0
    # no op is double-counted across phases
    assert sum(p["total"] for p in by_phase.values()) == props["graph_ops_total"]
