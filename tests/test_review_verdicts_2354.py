"""#2354 — uncertainty-verdict layer (epic #2349 E-review).

E2E (the issue's own target): a below-threshold correction conflict → queued →
each of close / keep / reject → asserted graph state + a provenance verdict
record on BOTH points. Plus the two postures the epic fixes as policy:

* auto-accept reads graph EP state (never a model self-report) and only
  above the configurable threshold — the canonical measurement state is
  recorded, and an unmeasured claim is warned about;
* the sweep never auto-resolves a queued decision, and the drainer applies only
  the verdicts it is handed.

`tests/test_de2e6_temporal.py` is the sibling suite for the candidate-minting
path this layer rides (`_temporal_wire`); this file owns the verdict contract.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from tortoise.extractor import entity_stage_fixture
from tortoise.review_verdicts import (
    DECISION_DECIDED,
    DECISION_QUEUED,
    DEFAULT_THRESHOLD,
    VERDICT_CLOSE,
    VERDICT_KEEP,
    VERDICT_REJECT,
    VERDICT_VALUES,
    VerdictLayer,
    auto_accept_enabled,
    resolve_threshold,
)
from tortoise.sdk import TortoiseSDK

T1 = "2026-07-01T10:00:00Z"
T2 = "2026-07-05T10:00:00Z"
T3 = "2026-07-10T10:00:00Z"

D1_TX = "Alice: We decided to use port 16379 for FalkorDB.\n"
D2_TX = "Alice: We decided to revert to port 16380 because the port 16379 decision was wrong.\n"
D3_TX = "Alice: We decided to replace the port 16379 decision with port 16390.\n"


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"),
                       event_log_path=str(tmp_path / "events.jsonl"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """The layer's knobs must not leak in from the runner's environment."""
    monkeypatch.delenv("TORTOISE_VERDICT_AUTO_ACCEPT", raising=False)
    monkeypatch.delenv("TORTOISE_VERDICT_THRESHOLD", raising=False)


def _mine(sdk, transcript, source_id, session_date, tmp):
    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import mine_conversation
    base = tmp or tempfile.mkdtemp()
    api = EventAPI(EventLog(os.path.join(base, "e.jsonl")),
                   initiated_by="extractor", agent_id="t",
                   projection=sdk._get_proj())
    return mine_conversation(
        transcript, source_id, api,
        entity_stage=entity_stage_fixture(),
        content_dedup=False,  # isolate the temporal pass (dedup is #784's)
        session_date=session_date,
        sdk=sdk)


def _decision_points(sdk):
    return sdk._get_proj().g.query(
        "MATCH (n:Point {pointKind:'decision'}) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "RETURN n.id, n.content, n.status ORDER BY n.id"
    ).result_set


def _prior_id(sdk):
    return next(p[0] for p in _decision_points(sdk)
                if p[1].startswith("We decided to use port"))


def _make_candidate(sdk, tmp, *, tx=D2_TX, session=T2):
    """Mine a LIVE prior, then a conflicting decision → one temporal candidate.

    Returns ``(candidate_id, prior_id, batch_id)``. The mining post-pass has
    already run the #2354 sweep, so the candidate is in the decision state.
    """
    _mine(sdk, D1_TX, "rv-d1", T1, str(tmp))
    prior = _prior_id(sdk)
    sdk.promote_point(prior)
    _mine(sdk, tx, "rv-d2", session, str(tmp))
    cands = sdk.list_dedup_candidates(candidate_type="temporal")
    assert cands, "expected a temporal correction candidate"
    cid = cands[0]["id"]
    props = sdk.get_point(cid)
    return cid, prior, props.get("batch_id")


def _props(sdk, point_id):
    return sdk.get_point(point_id)


def _seed_candidate(sdk, cid, prior_id):
    """A minimal candidate + live prior pair, for the determinism tests."""
    sdk._get_proj().g.query(
        "CREATE (t:Point {id:$p, status:'live', pointKind:'decision', "
        "content:'prior belief'})",
        params={"p": prior_id})
    sdk._get_proj().g.query(
        "CREATE (n:Point {id:$c, status:'draft', pointKind:'decision', "
        "content:'conflicting belief', temporal_candidate:true, "
        "temporal_target_id:$p, temporal_target_ids:[$p]})",
        params={"c": cid, "p": prior_id})


def _nand_between(sdk, a_id, b_id):
    rows = sdk._get_proj().g.query(
        "MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$a}), "
        "(op)-[:NAND]->(:Point {id:$b}) RETURN count(op)",
        params={"a": a_id, "b": b_id},
    ).result_set
    return rows[0][0]


# ── threshold resolution ────────────────────────────────────────────
class TestThresholdConfig:
    def test_default_and_explicit(self):
        assert resolve_threshold() == DEFAULT_THRESHOLD
        assert resolve_threshold(0.9) == 0.9

    def test_env_configures(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_VERDICT_THRESHOLD", "0.82")
        assert resolve_threshold() == 0.82

    def test_env_garbage_falls_back_never_raises(self, monkeypatch):
        for bad in ("not-a-number", "0", "-1", "1.5"):
            monkeypatch.setenv("TORTOISE_VERDICT_THRESHOLD", bad)
            assert resolve_threshold() == DEFAULT_THRESHOLD

    def test_explicit_out_of_range_raises(self):
        for bad in (0.0, -0.1, 1.5):
            with pytest.raises(ValueError):
                resolve_threshold(bad)

    def test_auto_accept_defaults_off_and_env_opts_in(self, monkeypatch):
        assert auto_accept_enabled() is False
        assert auto_accept_enabled(True) is True
        monkeypatch.setenv("TORTOISE_VERDICT_AUTO_ACCEPT", "yes")
        assert auto_accept_enabled() is True
        monkeypatch.setenv("TORTOISE_VERDICT_AUTO_ACCEPT", "0")
        assert auto_accept_enabled() is False


# ── indicator 1: below-threshold → named decision state, not limbo ──
class TestQueuedDecisionState:
    def test_below_threshold_candidate_is_queued(self, sdk, tmp_path):
        cid, prior, batch_id = _make_candidate(sdk, tmp_path)
        props = _props(sdk, cid)
        assert props["reviewDecision"] == DECISION_QUEUED, props
        assert props["reviewThreshold"] == DEFAULT_THRESHOLD
        # The EP belief is below the default bar, so it is queued rather than
        # auto-accepted; the canonical measurement state is recorded.
        assert props["reviewConfidence"] < DEFAULT_THRESHOLD, props
        assert props["reviewMeasurement"] in ("measured", "baseline", "unmeasured")
        assert props["status"] == "draft"
        # It is a decision to make, not a resolved one: still in the queue.
        cands = sdk.list_dedup_candidates(candidate_type="temporal")
        assert [c["id"] for c in cands] == [cid]

        layer = VerdictLayer(sdk)
        pending = layer.pending(candidate_type="temporal")
        assert [p["id"] for p in pending] == [cid]
        assert pending[0]["verdicts"] == list(VERDICT_VALUES)
        assert pending[0]["targets"] == [prior]
        assert batch_id, "the candidate must be bound to its mining batch"

    def test_sweep_is_idempotent_and_deterministic(self, sdk, tmp_path):
        cid, _prior, batch_id = _make_candidate(sdk, tmp_path)
        layer = VerdictLayer(sdk)
        first = layer.sweep(batch_id=batch_id)
        assert first["queued"] == 1 and first["auto_accepted"] == 0
        second = layer.sweep(batch_id=batch_id)
        # A queued (not decided) candidate is re-stamped, never re-resolved.
        assert second["queued"] == 1 and second["auto_accepted"] == 0
        assert _props(sdk, cid)["reviewDecision"] == DECISION_QUEUED


# ── indicator 4: threshold reads the calibrated EP posterior ────────
class TestEpPosteriorThreshold:
    def test_auto_accept_when_ep_posterior_clears_threshold(
            self, sdk, tmp_path):
        cid, prior, _batch = _make_candidate(sdk, tmp_path)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) "
            "SET n.posterior_alpha = 9.0, n.posterior_beta = 1.0",
            params={"id": cid})
        layer = VerdictLayer(sdk, auto_accept=True)
        post = layer.ep_posterior(cid)
        assert post["mean"] == pytest.approx(0.9)
        assert post["measurement"] == "measured"
        rep = layer.sweep(candidate_ids=[cid])
        assert rep["auto_accepted"] == 1 and rep["queued"] == 0, rep
        props = _props(sdk, cid)
        assert props["reviewDecision"] == DECISION_DECIDED
        assert props["reviewVerdict"] == VERDICT_KEEP
        assert props["reviewVerdictSource"] == "auto"
        assert props["status"] == "live"
        # the deferred wire landed, and the prior carries the provenance
        assert _nand_between(sdk, cid, prior) >= 1
        assert _props(sdk, prior)["reviewVerdict"] == VERDICT_KEEP
        assert _props(sdk, prior)["reviewVerdictCandidate"] == cid

    def test_above_measurement_but_below_configured_threshold_queues(
            self, sdk, tmp_path):
        cid, _prior, _batch = _make_candidate(sdk, tmp_path)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) "
            "SET n.posterior_alpha = 9.0, n.posterior_beta = 1.0",
            params={"id": cid})
        layer = VerdictLayer(sdk, auto_accept=True, threshold=0.95)
        rep = layer.sweep(candidate_ids=[cid])
        assert rep["queued"] == 1 and rep["auto_accepted"] == 0, rep
        assert _props(sdk, cid)["reviewDecision"] == DECISION_QUEUED

    def test_measurement_state_and_configurable_threshold(self, sdk):
        # A directly seeded candidate carries NO EP state at all — the
        # canonical vocabulary reads it `unmeasured` (neutral Beta(1,1), 0.5).
        _seed_candidate(sdk, "rv-a", "rv-prior")
        layer = VerdictLayer(sdk, auto_accept=True, threshold=0.4)
        post = layer.ep_posterior("rv-a")
        assert post["measurement"] == "unmeasured"
        assert post["mean"] == pytest.approx(0.5)
        layer.mark_queued("rv-a", posterior=post)
        assert _props(sdk, "rv-a")["reviewMeasurement"] == "unmeasured"
        # The threshold is the operator's bar: at 0.4 the same belief clears
        # it and auto-accepts (the configurable-threshold contract).
        rep = layer.sweep(candidate_ids=["rv-a"])
        assert rep["auto_accepted"] == 1, rep
        assert _props(sdk, "rv-a")["reviewVerdict"] == VERDICT_KEEP


# ── indicator 2: each verdict maps to existing mechanics + provenance ─
class TestVerdictMapping:
    def test_keep_promotes_and_records_provenance_on_both_points(
            self, sdk, tmp_path):
        cid, prior, _batch = _make_candidate(sdk, tmp_path)
        rec = VerdictLayer(sdk).decide(cid, VERDICT_KEEP, reason="operator kept",
                                       decided_by="lane-2354")
        assert rec["provenance_targets"] == [prior]
        props = _props(sdk, cid)
        assert props["status"] == "live"
        assert props["temporal_reviewed"] == "merge"
        assert props["reviewDecision"] == DECISION_DECIDED
        assert props["reviewVerdict"] == VERDICT_KEEP
        assert props["reviewVerdictBy"] == "lane-2354"
        assert props["reviewVerdictReason"] == "operator kept"
        assert props["reviewVerdictTargets"] == [prior]
        tprops = _props(sdk, prior)
        assert tprops["reviewVerdict"] == VERDICT_KEEP
        assert tprops["reviewVerdictCandidate"] == cid
        assert tprops["reviewVerdictBy"] == "lane-2354"
        assert _nand_between(sdk, cid, prior) >= 1

    def test_reject_drops_the_candidate_and_is_journaled(self, sdk, tmp_path):
        cid, prior, _batch = _make_candidate(sdk, tmp_path)
        VerdictLayer(sdk).decide(cid, VERDICT_REJECT, decided_by="lane-2354")
        props = _props(sdk, cid)
        assert props["status"] == "draft"          # never promoted
        assert props["temporal_reviewed"] == "reject"
        assert props["reviewVerdict"] == VERDICT_REJECT
        assert _nand_between(sdk, cid, prior) == 0
        assert _props(sdk, prior)["reviewVerdict"] == VERDICT_REJECT
        log = sdk._get_event_log().read_all()
        assert any(e.get("type") == "DedupeRejected" for e in log), log

    def test_reject_retracts_an_already_live_candidate(self, sdk, tmp_path):
        cid, _prior, _batch = _make_candidate(sdk, tmp_path)
        sdk.promote_point(cid)  # live, but not yet decided by the layer
        VerdictLayer(sdk).decide(cid, VERDICT_REJECT)
        assert _props(sdk, cid)["status"] == "retracted"

    def test_close_marks_decided_no_action(self, sdk, tmp_path):
        cid, prior, _batch = _make_candidate(sdk, tmp_path)
        VerdictLayer(sdk).decide(cid, VERDICT_CLOSE, reason="restatement")
        props = _props(sdk, cid)
        assert props["status"] == "draft"          # no promote
        assert props["temporal_reviewed"] == "close"
        assert props["reviewDecision"] == DECISION_DECIDED
        assert props["reviewVerdict"] == VERDICT_CLOSE
        assert _nand_between(sdk, cid, prior) == 0  # no wire
        assert _props(sdk, prior)["reviewVerdict"] == VERDICT_CLOSE
        # decided-no-action removes it from the review queue
        assert sdk.list_dedup_candidates(candidate_type="temporal") == []
        assert VerdictLayer(sdk).pending(candidate_type="temporal") == []

    def test_out_of_vocabulary_verdict_and_double_decide(self, sdk, tmp_path):
        cid, _prior, _batch = _make_candidate(sdk, tmp_path)
        layer = VerdictLayer(sdk)
        with pytest.raises(ValueError, match="verdict must be one of"):
            layer.decide(cid, "ship")
        layer.decide(cid, VERDICT_CLOSE)
        # same verdict → idempotent; different verdict → refused
        again = layer.decide(cid, VERDICT_CLOSE)
        assert again["already_decided"] is True
        with pytest.raises(ValueError, match="already decided"):
            layer.decide(cid, VERDICT_KEEP)

    def test_keep_refused_while_batch_quarantined(self, sdk, tmp_path):
        cid, _prior, batch_id = _make_candidate(sdk, tmp_path)
        sdk.quarantine_batch(batch_id, reason="W-3 test quarantine")
        layer = VerdictLayer(sdk)
        with pytest.raises(ValueError, match="quarantined"):
            layer.decide(cid, VERDICT_KEEP)
        props = _props(sdk, cid)
        # The refusal is total — the candidate is neither promoted nor
        # marked reviewed, so it stays a queued decision.
        assert props["status"] == "draft"
        assert props.get("temporal_reviewed") is None
        assert props["reviewDecision"] == DECISION_QUEUED
        rep = layer.sweep(batch_id=batch_id)
        assert rep["auto_accepted"] == 0 and rep["batch_quarantined"]

    def test_keep_refused_on_terminal_candidate_without_side_effects(
            self, sdk):
        """#2354 review P1: `keep` on a terminal candidate half-applied.

        The pre-fix order (approve_merge → promote_point → raise) stamped
        ``temporal_reviewed='merge'`` and journaled ``DedupeRecorded`` before
        discovering ``not_draft``, so the candidate vanished from BOTH review
        surfaces with no decision recorded — the exact limbo #2354 removes.
        The refusal must now be side-effect-free.
        """
        _seed_candidate(sdk, "rv-term", "rv-prior")
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:'rv-term'}) SET n.status = 'retracted'")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-term")
        with pytest.raises(ValueError, match="not_draft"):
            layer.decide("rv-term", VERDICT_KEEP, decided_by="probe")
        props = _props(sdk, "rv-term")
        assert props["status"] == "retracted"        # never promoted
        assert props.get("temporal_reviewed") is None  # no phantom review
        assert props["reviewDecision"] == DECISION_QUEUED  # still a decision
        assert props.get("reviewVerdict") is None
        # The candidate is still a queued decision an operator can reach, and
        # re-issuing the refusal is side-effect-free.
        assert [p["id"] for p in layer.pending(
            candidate_type="temporal")] == ["rv-term"]
        log = sdk._get_event_log().read_all()
        assert not any(e.get("type") == "DedupeRecorded" for e in log), log

    def test_keep_refused_on_operator_candidate_without_side_effects(self, sdk):
        """The other ``promote_point`` block reason, same no-side-effect rule."""
        _seed_candidate(sdk, "rv-op", "rv-prior")
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:'rv-op'}) SET n.is_operator = true")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-op")
        with pytest.raises(ValueError, match="is_operator"):
            layer.decide("rv-op", VERDICT_KEEP)
        props = _props(sdk, "rv-op")
        assert props.get("temporal_reviewed") is None
        assert props["reviewDecision"] == DECISION_QUEUED
        log = sdk._get_event_log().read_all()
        assert not any(e.get("type") == "DedupeRecorded" for e in log), log

    def test_keep_rolls_back_the_review_write_on_a_lost_promotion_race(
            self, sdk, monkeypatch):
        """A quarantine landing between the read and the CAS must not strand it.

        The pre-check cannot see a race, so the post-promotion refusal still
        exists — but it now restores the review state ``approve_merge`` wrote,
        instead of leaving a reviewed-but-unpromoted candidate behind.
        """
        _seed_candidate(sdk, "rv-race", "rv-prior")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-race")
        monkeypatch.setattr(
            sdk, "promote_point",
            lambda pid: {"id": pid, "promoted": False, "blocked": True,
                         "reason": "batch_quarantined"})
        with pytest.raises(ValueError, match="could not be promoted"):
            layer.decide("rv-race", VERDICT_KEEP)
        props = _props(sdk, "rv-race")
        assert props.get("temporal_reviewed") is None
        assert not props.get("reviewed")
        assert props["reviewDecision"] == DECISION_QUEUED
        assert props.get("reviewVerdict") is None


# ── indicator 3: deterministic drainer, never auto-resolves ─────────
class TestSweepFailureSurfaced:
    def test_mining_surfaces_sweep_failure(self, sdk, tmp_path, monkeypatch):
        """#2354 review P2: a failed sweep must not read as "queued nothing".

        Pre-fix, ``mine()`` swallowed the exception and returned
        ``verdict_sweep: None`` only, so a caller could not distinguish a
        sweep that never ran from one that queued zero candidates — the
        pre-#2354 limbo with a log line. The failure is now surfaced under
        its own key, like the W-2 dedup block's ``dedup_error``.
        """
        import tortoise.review_verdicts as rv

        def _boom(self, **kwargs):
            raise RuntimeError("graph exploded")

        monkeypatch.setattr(rv.VerdictLayer, "sweep", _boom)
        res = _mine(sdk, D1_TX, "rv-sweep-error", T1, str(tmp_path))
        assert res["verdict_sweep"] is None
        assert res["verdict_sweep_error"]
        assert "graph exploded" in res["verdict_sweep_error"]

    def test_mining_reports_no_sweep_error_on_success(self, sdk, tmp_path):
        res = _mine(sdk, D1_TX, "rv-sweep-ok", T1, str(tmp_path))
        assert res["verdict_sweep"] is not None
        assert res["verdict_sweep_error"] is None


class TestDeterministicDrainer:
    def test_empty_drain_resolves_nothing(self, sdk):
        _seed_candidate(sdk, "rv-a", "rv-prior")
        _seed_candidate(sdk, "rv-b", "rv-prior")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-a")
        layer.mark_queued("rv-b")
        rep = layer.drain({})
        assert rep["applied"] == 0 and rep["errors"] == []
        assert rep["remaining_queued"] == 2
        assert {p["id"] for p in layer.pending()} == {"rv-a", "rv-b"}

    def test_drain_applies_in_ascending_id_order(self, sdk):
        _seed_candidate(sdk, "rv-a", "rv-prior")
        _seed_candidate(sdk, "rv-b", "rv-prior")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-a")
        layer.mark_queued("rv-b")
        # handed out of order on purpose: the result order must be sorted
        rep = layer.drain({"rv-b": VERDICT_CLOSE, "rv-a": VERDICT_REJECT},
                          decided_by="lane-2354")
        assert [r["candidate_id"] for r in rep["results"]] == ["rv-a", "rv-b"]
        assert rep["applied"] == 2 and rep["errors"] == []
        assert rep["remaining_queued"] == 0
        assert _props(sdk, "rv-a")["reviewVerdict"] == VERDICT_REJECT
        assert _props(sdk, "rv-b")["reviewVerdict"] == VERDICT_CLOSE
        # the shared prior carries BOTH provenance records, newest wins
        tprops = _props(sdk, "rv-prior")
        assert tprops["reviewVerdict"] in (VERDICT_REJECT, VERDICT_CLOSE)
        assert tprops["reviewVerdictCandidate"] in ("rv-a", "rv-b")

    def test_drain_reports_errors_without_aborting(self, sdk):
        _seed_candidate(sdk, "rv-a", "rv-prior")
        layer = VerdictLayer(sdk)
        layer.mark_queued("rv-a")
        rep = layer.drain({"rv-a": "ship", "missing": VERDICT_CLOSE})
        assert rep["applied"] == 0
        assert {e["candidate_id"] for e in rep["errors"]} == {"rv-a", "missing"}
        assert rep["remaining_queued"] == 1
