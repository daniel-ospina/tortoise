"""Track C (#3011) — EP activation tests.

Hermetic: no DB, no network. ``read_confidence`` is pure; ``activate_beliefs``
is driven through a stub SDK that records the Cypher it receives and the EP
call it is asked to make.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import ep_activation as epa  # noqa: E402, RUF100

# ── read_confidence ────────────────────────────────────────────────────────

def test_read_confidence_uses_posterior():
    p = {"posterior_alpha": 3.0, "posterior_beta": 1.0}
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_absent_is_none_not_half():
    """#2598: an unmeasured point must NEVER render a fabricated 0.5."""
    assert epa.read_confidence({}) is None
    assert epa.read_confidence({}) != 0.5
    # explicit-None values are also absent (the property dict shape a graph
    # read returns when the props were never written)
    assert epa.read_confidence(
        {"posterior_alpha": None, "posterior_beta": None,
         "ep_alpha": None, "ep_beta": None}) is None


def test_read_confidence_prefers_posterior_over_ep():
    p = {"posterior_alpha": 3.0, "posterior_beta": 1.0,
         "ep_alpha": 9.0, "ep_beta": 1.0}
    # posterior 3/4 wins over prior mean 9/10
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_prefers_falsy_posterior_too():
    """Preference is by PRESENCE, not truthiness — a 0.0 posterior must not
    silently fall back to the ep_* prior (which would overstate belief)."""
    p = {"posterior_alpha": 0.0, "posterior_beta": 4.0,
         "ep_alpha": 9.0, "ep_beta": 1.0}
    assert epa.read_confidence(p) == pytest.approx(0.0)


def test_read_confidence_ep_only():
    """Only ep_* present (pre-EP or baseline'd shape) is a valid read."""
    p = {"ep_alpha": 1.0, "ep_beta": 3.0}
    assert epa.read_confidence(p) == pytest.approx(0.25)


def test_read_confidence_per_field_fallback():
    """A missing posterior_beta falls back to ep_beta, keeping the measured
    posterior_alpha — the per-field coalesce the canonical read uses."""
    p = {"posterior_alpha": 3.0, "ep_beta": 1.0, "ep_alpha": 99.0}
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_partial_state_is_none():
    """EP persists alpha+beta together; a lone parameter is malformed state,
    not a measurement — returning alpha/(alpha+0)=1.0 would fabricate."""
    assert epa.read_confidence({"posterior_alpha": 3.0}) is None
    assert epa.read_confidence({"ep_beta": 2.0}) is None
    assert epa.read_confidence({"posterior_alpha": 3.0,
                                "posterior_beta": None,
                                "ep_beta": None}) is None


def test_read_confidence_degenerate_and_garbage():
    assert epa.read_confidence({"posterior_alpha": 0.0,
                                "posterior_beta": 0.0}) is None
    assert epa.read_confidence({"posterior_alpha": "n/a",
                                "posterior_beta": 1.0}) is None
    assert epa.read_confidence(None) is None


# ── activate_beliefs (stub SDK) ────────────────────────────────────────────

class _Result:
    def __init__(self, result_set):
        self.result_set = result_set


class _FakeGraph:
    """Records every Cypher and answers by matching the (normalized) query."""

    def __init__(self, draft_ids, op_map, total, with_ep):
        self.draft_ids = list(draft_ids)
        self.op_map = dict(op_map)
        self.total = total
        self.with_ep = with_ep
        self.queries: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        c = " ".join(cypher.split())
        if "SET n.status = 'live'" in c:
            return _Result([])
        if "SET o.status = 'live'" in c:
            return _Result([])
        if "RETURN DISTINCT o.id" in c:
            out = []
            for cid in params.get("ids", []):
                for oid in self.op_map.get(cid, []):
                    out.append([oid])
            return _Result(out)
        if "count(n)" in c and "posterior_alpha IS NOT NULL" in c:
            return _Result([[self.with_ep]])
        if "count(n)" in c:
            return _Result([[self.total]])
        if "RETURN n.id" in c and "is_operator IS NULL" in c:
            return _Result([[pid] for pid in self.draft_ids])
        return _Result([])

    def matching(self, needle):
        return [(c, p) for c, p in self.queries if needle in c]


class _FakeProj:
    def __init__(self, graph):
        self.g = graph


class _FakeSDK:
    def __init__(self, graph, namespace="eval-ns"):
        self._namespace = namespace
        self._proj = _FakeProj(graph)
        self.events: list[tuple[str, dict]] = []
        self.dream_calls: list[dict] = []
        self.dirty_calls: list[list[str]] = []

    def _get_proj(self):
        return self._proj

    def get_point(self, pid):
        return {"id": pid, "status": "live"}

    def _emit_event(self, type_, **kwargs):
        self.events.append((type_, kwargs))

    def _mark_dirty(self, point_ids):
        self.dirty_calls.append(list(point_ids))

    def dream(self, **kwargs):
        self.dream_calls.append(kwargs)
        return {"mode": "full"}


def _make(draft_ids, op_map=None, total=None, with_ep=0, namespace="eval-ns"):
    graph = _FakeGraph(draft_ids, op_map or {},
                       total=len(draft_ids) if total is None else total,
                       with_ep=with_ep)
    return _FakeSDK(graph, namespace=namespace), graph


def test_activate_promotes_points_and_operators_then_runs_ep():
    sdk, graph = _make(["c1", "c2", "c3"],
                       op_map={"c1": ["op1"], "c2": ["op1"], "c3": ["op2"]},
                       with_ep=3)

    manifest = epa.activate_beliefs(sdk, namespace="eval-ns")

    # promotion query issued for the draft points + each draft operator
    assert len(graph.matching("SET n.status = 'live'")) == 1
    assert len(graph.matching("SET o.status = 'live'")) == 2
    # EP invoked through the whole-graph write surface
    assert len(sdk.dream_calls) == 1
    assert sdk.dream_calls[0]["mode"] == "full"
    assert sdk.dream_calls[0]["require_calibration"] is False
    # rebuild-durable events emitted for every promotion
    kinds = [e[0] for e in sdk.events]
    assert kinds.count("PointPromoted") == 3
    assert kinds.count("OperatorPromoted") == 2
    # dirty-marking mirrors the capture block
    assert sdk.dirty_calls == [["c1", "c2", "c3"]]
    assert manifest["points_promoted"] == 3
    assert manifest["operators_promoted"] == 2
    assert manifest["ep_ran"] is True
    assert manifest["ep_engine"] == "dream:full"
    assert manifest["points_with_ep"] == 3
    assert manifest["points_unmeasured"] == 0
    assert isinstance(manifest["duration_ms"], int)


def test_activate_batches_promotion_queries():
    """Thousands of eval points must not ride one Cypher statement."""
    sdk, graph = _make([f"c{i}" for i in range(5)], with_ep=5)
    epa.activate_beliefs(sdk, batch_size=2)
    # ceil(5/2) = 3 point-promotion batches
    assert len(graph.matching("SET n.status = 'live'")) == 3


def test_activate_operator_deduped_across_batches():
    sdk, graph = _make(["c1", "c2", "c3"],
                       op_map={"c1": ["op1"], "c2": ["op1"], "c3": ["op1"]})
    manifest = epa.activate_beliefs(sdk, batch_size=1)
    # op1 is incident to all three chunks but goes live exactly once
    assert len(graph.matching("SET o.status = 'live'")) == 1
    assert manifest["operators_promoted"] == 1


def test_activate_counts_unmeasured():
    sdk, _ = _make(["c1", "c2", "c3", "c4"], total=4, with_ep=1)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] == 1
    assert manifest["points_unmeasured"] == 3


def test_activate_no_drafts_still_runs_ep():
    """An already-promoted graph is re-activated idempotently — no promotion
    query is issued, but EP still runs (posteriors may be stale/absent)."""
    sdk, graph = _make([], total=10, with_ep=0)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_promoted"] == 0
    assert graph.matching("SET n.status = 'live'") == []
    assert len(sdk.dream_calls) == 1
    assert manifest["points_unmeasured"] == 10


def test_activate_ep_failure_is_fail_open_and_reported():
    sdk, _ = _make(["c1"])

    def boom(**kwargs):
        raise RuntimeError("EP exploded")

    sdk.dream = boom  # type: ignore[assignment]
    manifest = epa.activate_beliefs(sdk)
    assert manifest["ep_ran"] is False
    # promotion writes still committed — the manifest is the surface that
    # tells the caller the pass failed
    assert manifest["points_promoted"] == 1


def test_activate_namespace_mismatch_refuses():
    sdk, _ = _make(["c1"], namespace="graph-a")
    with pytest.raises(ValueError, match="namespace mismatch"):
        epa.activate_beliefs(sdk, namespace="graph-b")


def test_activate_namespace_none_skips_guard():
    sdk, _ = _make(["c1"], namespace="graph-a")
    manifest = epa.activate_beliefs(sdk, namespace=None)
    assert manifest["points_promoted"] == 1


def test_activate_rejects_bad_batch_size():
    sdk, _ = _make(["c1"])
    with pytest.raises(ValueError, match="batch_size"):
        epa.activate_beliefs(sdk, batch_size=0)
