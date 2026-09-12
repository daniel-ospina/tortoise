"""#3139 — dream() must never report success while computing zero belief state.

The bug: ``GRAPH.COPY`` silently drops the ``false`` entries of a
single-property boolean index (filed separately as #3154). The EP/dream
selector used the bare ``X.is_operator = false`` spelling, which the broken
index serves — so on a restored/imported graph the selection returned zero
rows while the graph held 1000+ claims and 100 IMPL/NAND edges:

    dream(mode="full") -> {batches: 0, total_affected: 0,
                           converged_all: True, scanned_count: 0,
                           budget_used: 0,   coverage: 0.0}
    dream()            -> {iterations: 0, converged: True,
                           affected_claims: [...], budget_used: 0, ...}

Both shapes are indistinguishable from "nothing to do", and zero belief state
(``ep_alpha`` / ``posterior_alpha`` / ``confidence``) was written.

This module pins both halves of the fix:

* the dream/EP path selects with the index-independent predicate form
  (``X.is_operator IS NULL OR X.is_operator = false``), so a graph whose
  ``= false`` predicate is index-broken still propagates; and
* a pass that STILL selects nothing over a factored graph fails loudly with
  ``DreamNoOpError`` (the fail-closed backstop) before it can sweep its own
  backlog, while a genuinely factor-free graph no-ops with a recorded reason.

Hermetic embedded harness (tests/test_dream.py pattern). The GRAPH.COPY index
corruption is modelled at the projection boundary by suppressing the bare
``= false`` spelling — the exact spelling the broken index serves and the
spelling the fix no longer uses. The index-independent form is unaffected,
matching the verified behaviour of a real GRAPH.COPY'd FalkorDB graph.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_3139_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()


def _claim(sdk: TortoiseSDK, content: str) -> str:
    # #992/#943: EP tests model live claims (create_point defaults to draft).
    return sdk.create_point("statement", content, dedup=False,
                            status="live")["id"]


def _factored_graph(sdk: TortoiseSDK) -> tuple[str, str, str]:
    """Two derived-live IMPL operators over three live claims."""
    a = _claim(sdk, "conclusion A")
    b = _claim(sdk, "premise B")
    c = _claim(sdk, "premise C")
    sdk.create_operator("IMPL", b, [a])
    sdk.create_operator("IMPL", c, [a])
    return a, b, c


class _EmptyResult:
    def __init__(self):
        self.result_set: list = []


class _BrokenBooleanIndex:
    """Model a GRAPH.COPY'd graph (#3154): the boolean range index on
    ``Point.is_operator`` has dropped every ``false`` entry, so the bare
    ``X.is_operator = false`` predicate returns zero rows. The
    index-independent ``X.is_operator IS NULL OR X.is_operator = false`` form
    is not index-served and keeps working (verified against a real
    GRAPH.COPY'd FalkorDB graph)."""

    def __init__(self, real):
        self._real = real

    def query(self, cypher, *args, **kwargs):
        if ("is_operator = false" in cypher
                and "is_operator IS NULL" not in cypher):
            return _EmptyResult()
        return self._real.query(cypher, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _corrupt_boolean_index(monkeypatch, sdk):
    """Install the GRAPH.COPY corruption model on BOTH the projection and the
    EP handle: ``TortoiseEP.__init__`` captures ``self.g = projection.g`` and
    may have been constructed before this point (a write path calls
    ``_get_ep()``), so wrapping only ``proj.g`` would leave the ep.py
    factor-discovery queries running against the healthy index."""
    proj = sdk._get_proj()
    ep = sdk._get_ep()
    wrapper = _BrokenBooleanIndex(proj.g)
    monkeypatch.setattr(proj, "g", wrapper)
    monkeypatch.setattr(ep, "g", wrapper)


class TestCorruptBooleanIndexStillPropagates:
    """The regression: on a graph whose ``= false`` predicate is index-broken,
    dream() must select and propagate — not return a zero-work success."""

    def test_full_mode_writes_belief_state(self, sdk, monkeypatch):
        _factored_graph(sdk)
        proj = sdk._get_proj()
        _corrupt_boolean_index(monkeypatch, sdk)
        # The modelled corruption is live and asserted, not assumed.
        assert proj.g.query(
            "MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)"
        ).result_set == []
        assert proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN count(n)"
        ).result_set[0][0] == 3

        result = sdk.dream(mode="full", require_calibration=False)

        assert result["converged_all"] is True
        assert result["total_affected"] > 0, (
            "a populated factored graph must propagate, not scan zero anchors"
        )
        assert result["budget_used"] > 0, (
            "zero operators selected over a factored graph is the silent no-op"
        )
        n_alpha = proj.g.query(
            "MATCH (n:Point) WHERE n.ep_alpha IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        n_conf = proj.g.query(
            "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        assert n_alpha > 0 and n_conf > 0, "EP must persist belief state"

    def test_local_mode_writes_belief_state(self, sdk, monkeypatch):
        _factored_graph(sdk)
        assert sdk._dirty_roots, "writes mark dirty roots"
        proj = sdk._get_proj()
        _corrupt_boolean_index(monkeypatch, sdk)

        result = sdk.dream(mode="local", require_calibration=False)

        assert result["converged"] is True
        assert result["budget_used"] > 0
        n_conf = proj.g.query(
            "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        assert n_conf > 0, "local EP must persist belief state"

    def test_direct_edge_only_graph_propagates(self, sdk, monkeypatch):
        """Operator-less direct edges exercise the ep.py factor-discovery
        predicates (plain-point seed, per-hop expansion, Batch-3 direct-edge
        factors) — the EP handle must see the corruption model too. The
        ``iterations >= 1`` assertion is what pins Batch 3: without the direct
        factor, ``_affected_factors`` returns empty and ``run`` early-returns
        at 0 iterations (confidence would still be written from
        ``_last_affected``, so a confidence-only assertion would not catch it)."""
        a = _claim(sdk, "direct A")
        b = _claim(sdk, "direct B")
        sdk.create_direct_edge("IMPL", a, b)  # bidirectional by default
        proj = sdk._get_proj()
        _corrupt_boolean_index(monkeypatch, sdk)

        result = sdk.dream(mode="local", require_calibration=False)

        assert result["converged"] is True
        assert result["iterations"] >= 1, (
            "the direct-edge factor must participate — 0 iterations means "
            "Batch 3 dropped it")
        n_conf = proj.g.query(
            "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        assert n_conf == 2, "direct-edge EP must persist belief state"

    def test_source_only_window_pins_forward_direct_edge(self, sdk, monkeypatch):
        """Forward direct-edge traversal: a window holding only the SOURCE of
        a direct IMPL edge must select the factor (pins the forward selector
        predicate's index-independent spelling under corruption)."""
        a = _claim(sdk, "source A")
        b = _claim(sdk, "target B")
        sdk.create_direct_edge("IMPL", a, b)  # bidirectional
        _corrupt_boolean_index(monkeypatch, sdk)
        sdk._dirty_roots.clear()
        sdk._dirty_roots.add(a)  # source only
        result = sdk.dream(mode="local", require_calibration=False)
        assert result["iterations"] >= 1
        # Pins the `_window_closure` direct-edge predicate: with the closure
        # expanding to B, coverage is 2/2 = 1.0; without it the denominator
        # stays {A} while EP writes both → coverage 2.0 (contract violation).
        assert 0.0 <= result["coverage"] <= 1.0

    def test_bidirectional_target_only_window_pins_backward_direct_edge(
            self, sdk, monkeypatch):
        """Backward direct-edge traversal: a window holding only the TARGET of
        a BIDIRECTIONAL direct IMPL edge must still select the factor (pins
        the backward selector predicate under corruption; the unidirectional
        case is the no-op test above)."""
        a = _claim(sdk, "source A")
        b = _claim(sdk, "target B")
        sdk.create_direct_edge("IMPL", a, b)  # bidirectional
        _corrupt_boolean_index(monkeypatch, sdk)
        sdk._dirty_roots.clear()
        sdk._dirty_roots.add(b)  # target only
        result = sdk.dream(mode="local", require_calibration=False)
        assert result["iterations"] >= 1
        assert 0.0 <= result["coverage"] <= 1.0

    def test_stale_first_propagates_under_corruption(self, sdk, monkeypatch):
        """Stale-first mode must propagate too — exercises dream_window and
        the stale-first guard call/signal aggregation under the corruption
        model (a starved selector here would raise DreamNoOpError)."""
        _factored_graph(sdk)
        _corrupt_boolean_index(monkeypatch, sdk)
        result = sdk.dream(mode="stale-first", require_calibration=False)
        assert result["converged"] is True
        assert result["budget_used"] > 0

    def test_full_mode_clears_the_backlog_it_resolved(self, sdk, monkeypatch):
        """The resolved backlog must clear, so the C7 health surface does not
        report a false zero-output alarm after real work (#3139)."""
        _factored_graph(sdk)
        proj = sdk._get_proj()
        _corrupt_boolean_index(monkeypatch, sdk)

        sdk.dream(mode="full", require_calibration=False)

        assert proj.g.query(
            "MATCH (n:Point) WHERE n.ep_dirty = true RETURN count(n)"
        ).result_set[0][0] == 0
        health = sdk.dream_health_check()
        assert health["stale_backlog"] == 0
        assert health["last_pass_output"] > 0
        assert health["alarm_verdict"] is False


class TestSilentNoOpFailsLoudly:
    """The fail-closed backstop: a pass that selects nothing over a factored
    graph raises instead of reporting convergence."""

    @staticmethod
    def _starve_selector(monkeypatch):
        from tortoise import analyze
        monkeypatch.setattr(analyze, "_bfs_select_operators",
                            lambda *a, **k: (set(), set()))

    def test_local_starvation_raises_and_keeps_backlog(self, sdk, monkeypatch):
        from tortoise.exceptions import DreamNoOpError

        _factored_graph(sdk)
        self._starve_selector(monkeypatch)
        with pytest.raises(DreamNoOpError) as excinfo:
            sdk.dream(mode="local", require_calibration=False)
        assert excinfo.value.mode == "local"
        assert excinfo.value.eligible_factors > 0
        assert sdk._dirty_roots, (
            "the loud failure must fire before the dirty-root sweep — a failed "
            "pass that erased its own backlog would strand the graph forever"
        )

    def test_full_starvation_raises(self, sdk, monkeypatch):
        from tortoise.exceptions import DreamNoOpError

        _factored_graph(sdk)
        self._starve_selector(monkeypatch)
        with pytest.raises(DreamNoOpError) as excinfo:
            sdk.dream(mode="full", require_calibration=False)
        assert excinfo.value.mode == "full"
        assert excinfo.value.eligible_factors > 0

    def test_starved_selector_single_endpoint_window_raises(
            self, sdk, monkeypatch):
        """The guard's probe must catch an operator whose OTHER live endpoint
        is outside the pass's window — the selector finds an operator from a
        SINGLE frontier endpoint. A window-scoped derived-liveness count would
        miss it and let a starved selector silently succeed."""
        from tortoise.exceptions import DreamNoOpError

        a = _claim(sdk, "target A")
        b = _claim(sdk, "source B")
        sdk.create_operator("IMPL", b, [a])
        # Window = {A} only (B's dirty root removed); hydrate is a no-op
        # because the in-memory set is non-empty.
        sdk._dirty_roots.clear()
        sdk._dirty_roots.add(a)
        self._starve_selector(monkeypatch)
        with pytest.raises(DreamNoOpError) as excinfo:
            sdk.dream(mode="local", require_calibration=False)
        assert excinfo.value.eligible_factors > 0


class TestLegitimateNoOpIsDistinguishable:
    """The other half of the contract: a provably empty case returns normally
    and reports WHY it did nothing (no exception)."""

    def test_empty_graph_full_records_reason(self, sdk):
        result = sdk.dream(mode="full", require_calibration=False)
        assert result["converged_all"] is True
        assert result["total_affected"] == 0
        assert sdk.dream_health_check()["no_op_reason"] == "no_ep_factors"

    def test_operatorless_claims_noop(self, sdk):
        _claim(sdk, "isolated one")
        _claim(sdk, "isolated two")
        result = sdk.dream(mode="full", require_calibration=False)
        assert result["converged_all"] is True
        assert result["total_affected"] == 0
        assert sdk.dream_health_check()["no_op_reason"] == "no_ep_factors"

    def test_local_without_dirty_roots_records_reason(self, sdk):
        result = sdk.dream(mode="local", require_calibration=False)
        assert result["converged"] is True
        assert result["iterations"] == 0
        assert sdk.dream_health_check()["no_op_reason"] == "no_dirty_roots"

    def test_draft_operator_is_not_an_eligible_factor(self, sdk):
        """An EP-inert operator (draft/terminal, #780/#2422) with two LIVE
        endpoints must not count as an eligible factor — the selector applies
        the operator-side live filter, and the guard's probe must mirror it,
        else a legitimate no-op false-fires DreamNoOpError."""
        a = _claim(sdk, "conclusion A")
        b = _claim(sdk, "premise B")
        # promote_source=False → the operator is created draft while the
        # already-live endpoints stay live.
        sdk.create_operator("IMPL", b, [a], promote_source=False)
        result = sdk.dream(mode="full", require_calibration=False)
        assert result["converged_all"] is True
        assert result["total_affected"] == 0
        assert sdk.dream_health_check()["no_op_reason"] == "no_ep_factors"

    def test_unidirectional_direct_edge_target_is_not_a_factor(self, sdk):
        """A pass over only the TARGET of a unidirectional direct IMPL edge
        legitimately selects nothing — the selector never back-propagates
        into a unidirectional edge — so the guard must not false-fire, and the
        lazy read path (get_confidence → dream) must not raise either."""
        src = _claim(sdk, "source claim")
        tgt = _claim(sdk, "target claim")
        sdk.create_direct_edge("IMPL", src, tgt, direction="unidirectional")
        # Converge once, then re-dirty ONLY the target (the direction-sensitive
        # case the direction-blind probe got wrong).
        sdk.dream(mode="local", require_calibration=False)
        sdk._mark_dirty([tgt])
        result = sdk.dream(mode="local", require_calibration=False)
        assert result["converged"] is True
        assert sdk.dream_health_check()["no_op_reason"] == "no_ep_factors"
        # The read path lazily dreams the dirty root — it must not raise.
        sdk.get_confidence(tgt, require_calibration=False)
