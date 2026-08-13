"""GAP-15 #7003: Conversation mining pipeline tests.

ConversationMiner → extractor → ≥3 EventRecorded events per session.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.api import EventAPI          # noqa: E402
from tortoise.log import EventLog          # noqa: E402
from tortoise.mining import ConversationMiner   # noqa: E402
from tortoise.mining import (              # noqa: E402
    EpSafeCommit,
    quarantine_batch,
    list_quarantined,
    batch_status,
    BATCH_STATUS_ACTIVE,
    BATCH_STATUS_COMMITTED,
    BATCH_STATUS_QUARANTINED,
)
from tortoise.sdk import TortoiseSDK       # noqa: E402


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api():
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test"), log


# ── Gate: ≥3 events per session ──────────────────────────────────

def test_mine_sample_transcript():
    """Mining sample_transcript.txt produces ≥3 EventRecorded events."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Connor: We should raise the burn rate slowly.\n"
        "Connor: Because if we jump it too fast, early buyers get wrecked and they leave.\n"
        "Spencer: But a slow raise lets a manipulator accumulate a cheap position before anyone notices.\n"
        "Spencer: So the schedule has to be unpredictable, not just slow.\n"
        "Connor: That's not relevant if the position is washable anyway.\n"
        "Connor: A washable position means the ledger can be reset, therefore accumulation gives no lasting edge.\n"
        "Spencer: However, washing has a detectable cost, since every reset shows up in settlement flow.\n"
        "Connor: Given that settlement flow is public, honest actors can price the wash in.\n"
    )

    result = miner.mine(transcript, "test_session", api)

    assert result["events"] >= 3, (
        f"Gate failed: {result['events']} events < 3 minimum"
    )
    assert result["points"] > 0, "Expected at least 1 Point"
    assert result["operators"] > 0, "Expected at least 1 Operator"

    # Verify EventRecorded events in log
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]
    assert len(recorded) >= 3, f"Expected ≥3 EventRecorded in log, got {len(recorded)}"

    # Verify at least one meeting event
    kinds = [e["event"]["eventKind"] for e in recorded]
    assert "meeting" in kinds, f"Expected 'meeting' in event kinds: {kinds}"

    print(f"PASS test_mine_sample_transcript "
          f"({result['events']} events, {result['points']} points, "
          f"{result['operators']} operators)")


def test_mine_events_have_required_fields():
    """All EventRecorded events have required fields: eventId, eventKind, subject, object."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Alice: We decided to use FalkorDB for the memory backend.\n"
        "Bob: I disagree because Postgres would be simpler.\n"
        "Alice: But Postgres graph queries are slow for our use case.\n"
    )

    result = miner.mine(transcript, "test_fields", api)
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]

    required = ["eventId", "eventKind", "subject", "object", "startedAt", "participants"]
    for ev in recorded:
        inner = ev["event"]
        for field in required:
            assert field in inner, f"Missing field '{field}' in event {inner.get('eventId')}"
        assert len(inner["eventId"]) > 0, "eventId must not be empty"
        assert len(inner["eventKind"]) > 0, "eventKind must not be empty"

    print(f"PASS test_mine_events_have_required_fields ({len(recorded)} events)")


def test_mine_derives_decisions():
    """Decision language in transcript produces 'decision' events."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Alice: We decided to adopt the new pricing model.\n"
        "Alice: I agree with Bob's assessment.\n"
        "Bob: We should commit to the Q3 timeline.\n"
    )

    result = miner.mine(transcript, "test_decisions", api)
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]
    kinds = [e["event"]["eventKind"] for e in recorded]

    assert "decision" in kinds, f"Expected 'decision' event, got: {kinds}"
    print(f"PASS test_mine_derives_decisions ({len(recorded)} events, kinds: {kinds})")


def test_mine_derives_friction():
    """Conflict language + NAND operators produce 'friction' events."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Alice: We should use React.\n"
        "Bob: However, Vue is simpler and contradicts React's complexity claims.\n"
        "Alice: But React has the larger ecosystem.\n"
    )

    result = miner.mine(transcript, "test_friction", api)
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]
    kinds = [e["event"]["eventKind"] for e in recorded]

    # Friction must come from NAND operators and/or conflict-language points.
    # Regression for #325: the friction-language fallback used to be a no-op
    # `pass` — conflict-language friction was never emitted.
    assert "friction" in kinds, f"Expected 'friction' event, got: {kinds}"
    print(f"PASS test_mine_derives_friction ({len(recorded)} events, kinds: {kinds})")



def test_mine_friction_from_conflict_language_without_nand():
    """Regression #325: conflict-language in a point that is NOT covered by a
    NAND operator must still produce a friction event. The old fallback loop
    was a literal `pass` — conflict-language friction was never emitted."""
    miner = ConversationMiner()
    api, log = _api()

    # A single disagreement-cue point, no NAND operators (MockExtractor only
    # emits NAND when two points are in tension — a lone claim has none).
    transcript = "Alice: This approach does not agree with our findings.\n"

    result = miner.mine(transcript, "test_friction_lang", api)
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]
    kinds = [e["event"]["eventKind"] for e in recorded]

    assert "friction" in kinds, f"Expected conflict-language friction, got: {kinds}"


def test_mine_sparse_transcript():
    """Transcript with minimal content still produces at least a meeting event."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Alice: We should think about the problem more carefully.\n"
    )

    result = miner.mine(transcript, "test_sparse", api)
    events = log.read_all()
    recorded = [e for e in events if e["type"] == "EventRecorded"]

    assert len(recorded) >= 1, f"Expected at least meeting event, got {len(recorded)}"
    kinds = [e["event"]["eventKind"] for e in recorded]
    assert "meeting" in kinds, f"Expected meeting event, got {kinds}"
    print(f"PASS test_mine_sparse_transcript ({len(recorded)} events, kinds: {kinds})")

def test_mine_preserves_point_content():
    """Extracted Points are findable in the log with correct provenance."""
    miner = ConversationMiner()
    api, log = _api()

    transcript = (
        "Alice: FalkorDB is the right choice for graph storage.\n"
        "Bob: I agree because it uses the Redis protocol which is battle-tested.\n"
    )

    result = miner.mine(transcript, "test_prov", api)
    events = log.read_all()
    points = [e for e in events if e["type"] == "PointAdded"]

    assert len(points) >= 2, f"Expected >=2 Points, got {len(points)}"
    contents = [p["point"]["content"] for p in points]
    assert any("FalkorDB" in c for c in contents), f"No Point mentions FalkorDB: {contents}"
    print(f"PASS test_mine_preserves_point_content ({len(points)} points)")


# ── Phase-4 EP-safe batch commit (#785) ──────────────────────────────
# W-3 gate + batch-level quarantine state machine. Uses FalkorDBLite via
# TortoiseSDK (same embedded projection the pipeline writes through).


def _set_status(sdk, pid, status):
    """Direct status write — test seam for pre-#780 paths.

    On main, create_operator auto-promotes the SOURCE Point to live (#131) and
    writes no status on the operator node; the event path defaults operator
    nodes to live. #780's `create_operator(promote_source=False)` is not merged,
    so fixtures that need draft operator nodes / draft endpoints write status
    directly. Remove the seam when #780 lands.
    """
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$st",
        params={"id": pid, "st": status},
    )


@pytest.fixture
def mining_sdk():
    sdk = TortoiseSDK(os.path.join(
        tempfile.mkdtemp(prefix="tortoise_mining_test_"), "test.db"))
    yield sdk
    sdk.close()


class TestQuarantineBatch:
    def test_quarantine_marks_batch_and_blocks_promotion(self, mining_sdk):
        sdk = mining_sdk
        p = sdk.create_point("decision", "claim", status="draft", batch_id="b1")
        res = quarantine_batch(sdk._get_proj(), "b1", reason="EP drift (W-3)")
        assert res == {"batch_id": "b1", "blocked": True,
                       "reason": "EP drift (W-3)", "status": "quarantined"}
        assert batch_status(sdk._get_proj(), "b1")["status"] == "quarantined"
        blocked = sdk.promote_point(p["id"])
        assert blocked["blocked"] is True
        assert blocked["reason"] == "batch_quarantined"
        assert blocked["batch_id"] == "b1"

    def test_list_quarantined_only_returns_quarantined(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p = sdk.create_point("decision", "a", status="draft", batch_id="qb1")
        ok = sdk.create_point("decision", "b", status="draft", batch_id="ok1")
        quarantine_batch(proj, "qb1", reason="drift")
        # #779 integration: supply an unchanged grounding snapshot.
        EpSafeCommit(proj, "ok1").run([ok["id"]],
                                      grounding_before=0.5,
                                      grounding_after=0.5)
        assert sdk.get_point(p["id"])["status"] == "draft"
        assert [b["batch_id"] for b in list_quarantined(proj)] == ["qb1"]

    def test_quarantine_is_idempotent_and_requires_reason(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        with pytest.raises(ValueError):
            quarantine_batch(proj, "b1", reason="")
        quarantine_batch(proj, "b1", reason="first")
        q = batch_status(proj, "b1")
        q1 = q["quarantinedAt"]
        quarantine_batch(proj, "b1", reason="second")
        q = batch_status(proj, "b1")
        assert q["reason"] == "second"
        assert q["quarantinedAt"] == q1  # original timestamp preserved

    def test_unknown_batch_has_no_state(self, mining_sdk):
        assert batch_status(mining_sdk._get_proj(), "nope") is None


class TestEpSafeCommit:
    def test_clean_draft_batch_commits(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "d1", status="draft", batch_id="c1")
        p2 = sdk.create_point("decision", "d2", status="draft", batch_id="c1")
        res = EpSafeCommit(proj, "c1", grounding_fn=lambda: 0.5).run(
            [p1["id"], p2["id"]], grounding_before=0.5, grounding_after=0.51)
        assert res["ok"] is True and res["committed"] is True
        assert res["quarantined"] is False
        assert res["checks"]["all_points_draft"] is True
        assert res["checks"]["no_auto_wire"] is True
        assert res["checks"]["grounding"]["status"] == "pass"
        assert batch_status(proj, "c1")["status"] == BATCH_STATUS_COMMITTED

    def test_live_point_in_batch_quarantines(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "x", status="draft", batch_id="q1")
        p2 = sdk.create_point("decision", "y", status="live", batch_id="q1")
        res = EpSafeCommit(proj, "q1").run([p1["id"], p2["id"]])
        assert res["ok"] is False and res["quarantined"] is True
        assert res["checks"]["non_draft_points"][0]["id"] == p2["id"]
        assert batch_status(proj, "q1")["status"] == BATCH_STATUS_QUARANTINED
        assert sdk.promote_point(p1["id"])["reason"] == "batch_quarantined"

    def test_missing_point_fails_closed(self, mining_sdk):
        sdk = mining_sdk
        p = sdk.create_point("decision", "a", status="draft", batch_id="m1")
        res = EpSafeCommit(sdk._get_proj(), "m1").run([p["id"], "no-such-point"])
        assert res["ok"] is False
        assert res["checks"]["non_draft_points"][0] == {
            "id": "no-such-point", "reason": "not_found"}

    def test_auto_wire_operator_node_live_quarantines(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        # draft-to-draft NAND with a LIVE operator node = event-path default
        # leak on main (projection coalesce -> 'live'); #780 fixes this.
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="w1")
        p2 = sdk.create_point("decision", "b", status="draft", batch_id="w1")
        op = sdk.create_operator("NAND", p1["id"], [p2["id"]])
        _set_status(sdk, p1["id"], "draft")
        _set_status(sdk, p2["id"], "draft")
        _set_status(sdk, op["id"], "live")  # event-path leak simulation
        res = EpSafeCommit(proj, "w1").run([p1["id"], p2["id"]])
        assert res["ok"] is False
        assert res["checks"]["auto_wired"][0]["reason"] == "operator_node_live"

    def test_auto_wire_live_endpoint_quarantines(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="w2")
        live = sdk.create_point("decision", "prior", status="live")
        op = sdk.create_operator("IMPL", p1["id"], [live["id"]])
        _set_status(sdk, p1["id"], "draft")
        _set_status(sdk, op["id"], "draft")
        res = EpSafeCommit(proj, "w2").run([p1["id"]])
        assert res["ok"] is False
        assert res["checks"]["auto_wired"][0]["reason"] == "endpoint_live"

    def test_draft_operator_draft_endpoints_is_clean(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="c2")
        p2 = sdk.create_point("decision", "b", status="draft", batch_id="c2")
        op = sdk.create_operator("NAND", p1["id"], [p2["id"]])
        _set_status(sdk, p1["id"], "draft")
        _set_status(sdk, p2["id"], "draft")
        _set_status(sdk, op["id"], "draft")
        # #779 landed: mean_grounding is live — supply an unchanged snapshot.
        res = EpSafeCommit(proj, "c2").run([p1["id"], p2["id"]],
                                           grounding_before=0.5,
                                           grounding_after=0.5)
        assert res["ok"] is True
        assert res["checks"]["no_auto_wire"] is True
        assert res["checks"]["grounding"]["status"] == "pass"

    def test_grounding_drift_over_2pct_quarantines(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="g1")
        p2 = sdk.create_point("decision", "b", status="draft", batch_id="g1")
        res = EpSafeCommit(proj, "g1", grounding_fn=lambda: 0.55).run(
            [p1["id"], p2["id"]], grounding_before=0.5, grounding_after=0.55)
        assert res["ok"] is False
        assert res["checks"]["grounding"]["status"] == "fail"
        assert res["checks"]["grounding"]["drift"] == pytest.approx(0.05)
        assert res["reason"].startswith("W-3 failed: grounding_drift")

    def test_grounding_gate_skip_branch_unit(self, mining_sdk):
        """#779 landed: the DEFAULT grounding_fn resolves mean_grounding, so
        the skip branch is reachable only via a directly-constructed
        EpSafeCommit with an explicitly-None fn (unit-level check)."""
        sdk = mining_sdk
        proj = sdk._get_proj()
        gate = EpSafeCommit(proj, "s1")
        gate._grounding_fn = None  # pre-#779 era seam, unit-level
        check = gate._grounding_check(None, None)
        assert check["status"] == "skipped"
        assert "#779" in check["note"]

    def test_grounding_gate_active_with_default_fn_requires_snapshot(self, mining_sdk):
        """#779 landed: the DEFAULT grounding_fn resolves mean_grounding —
        the gate is active and fails closed without a pre-snapshot."""
        sdk = mining_sdk
        proj = sdk._get_proj()
        p = sdk.create_point("decision", "a", status="draft", batch_id="s2")
        res = EpSafeCommit(proj, "s2").run([p["id"]])
        assert res["ok"] is False
        assert res["checks"]["grounding"]["status"] == "fail"
        assert "grounding_before" in res["checks"]["grounding"]["note"]
        # With an unchanged snapshot the batch commits.
        res2 = EpSafeCommit(proj, "s2").run([p["id"]],
                                            grounding_before=0.5,
                                            grounding_after=0.5)
        assert res2["ok"] is True

    def test_recovery_rerun_pass_unquarantines(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="r1")
        p2 = sdk.create_point("decision", "b", status="draft", batch_id="r1")
        # First run fails W-3 (grounding drift) -> quarantined
        bad = EpSafeCommit(proj, "r1", grounding_fn=lambda: 0.9).run(
            [p1["id"], p2["id"]], grounding_before=0.5, grounding_after=0.9)
        assert bad["quarantined"] is True
        assert sdk.promote_point(p1["id"])["reason"] == "batch_quarantined"
        # Fix the cause (drift resolved) + re-run -> un-quarantined
        good = EpSafeCommit(proj, "r1", grounding_fn=lambda: 0.51).run(
            [p1["id"], p2["id"]], grounding_before=0.5, grounding_after=0.51)
        assert good["ok"] is True and good["recovered"] is True
        assert batch_status(proj, "r1")["status"] == BATCH_STATUS_COMMITTED
        assert list_quarantined(proj) == []
        # Points remain draft until promotion; promotion now allowed
        assert sdk.get_point(p1["id"])["status"] == "draft"
        res = sdk.promote_point(p1["id"])
        assert res["promoted"] is True

    def test_batch_state_defaults_to_active(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        sdk.create_point("decision", "a", status="draft", batch_id="a1")
        p = sdk.create_point("decision", "b", status="draft", batch_id="a1")
        assert batch_status(proj, "a1") is None  # no state until W-3 runs
        assert sdk.promote_point(p["id"])["promoted"] is True  # unregistered batch is not blocked


if __name__ == "__main__":
    test_mine_sample_transcript()
    test_mine_events_have_required_fields()
    test_mine_derives_decisions()
    test_mine_derives_friction()
    test_mine_sparse_transcript()
    test_mine_preserves_point_content()


class TestEpSafeCommitReviewFixes:
    """#944 code-review regression tests."""

    def test_null_status_operator_node_is_live_leak(self, mining_sdk):
        """The REAL pre-#780 leak shape: operator node with NO status property
        (create_operator writes none; projection coalesce defaults to live).
        W-3 must flag operator_node_live — a raw 'live' SET never occurs."""
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="w2")
        p2 = sdk.create_point("decision", "b", status="draft", batch_id="w2")
        op = sdk.create_operator("NAND", p1["id"], [p2["id"]])
        _set_status(sdk, p1["id"], "draft")
        _set_status(sdk, p2["id"], "draft")
        # NOTE: no _set_status(op) — raw status stays NULL (the leak shape)
        assert sdk.get_point(op["id"]).get("status") is None
        res = EpSafeCommit(proj, "w2").run([p1["id"], p2["id"]])
        assert res["ok"] is False
        assert res["checks"]["auto_wired"][0]["reason"] == "operator_node_live"

    def test_empty_batch_fails_closed(self, mining_sdk):
        """An all-fail extraction produces zero Points — the gate must
        quarantine, not vacuously commit (plan J-1)."""
        sdk = mining_sdk
        proj = sdk._get_proj()
        res = EpSafeCommit(proj, "empty-batch").run([])
        assert res["ok"] is False
        assert res["quarantined"] is True
        assert "empty_batch" in res["reason"]

    def test_committed_at_written_and_quarantined_at_cleared_on_recovery(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        from tortoise.mining import batch_status, quarantine_batch
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="rec1")
        quarantine_batch(proj, "rec1", reason="EP drift (W-3)")
        q = batch_status(proj, "rec1")
        assert q["status"] == "quarantined"
        assert q["quarantinedAt"] is not None
        res = EpSafeCommit(proj, "rec1").run([p1["id"]],
                                           grounding_before=0.5,
                                           grounding_after=0.5)
        assert res["ok"] is True and res["recovered"] is True
        committed = batch_status(proj, "rec1")
        assert committed["status"] == "committed"
        assert committed["committedAt"] is not None, (
            "committedAt must be written on commit (review #944)"
        )
        assert committed["quarantinedAt"] is None, (
            "quarantinedAt is episode state — must clear on recovery"
        )

    def test_grounding_error_fails_closed(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="g1")

        def boom():
            raise RuntimeError("graph query failed")

        res = EpSafeCommit(proj, "g1", grounding_fn=boom).run(
            [p1["id"]], grounding_before=0.5)
        assert res["ok"] is False
        assert res["checks"]["grounding"]["status"] == "fail"

    def test_grounding_before_required_when_fn_available(self, mining_sdk):
        sdk = mining_sdk
        proj = sdk._get_proj()
        p1 = sdk.create_point("decision", "a", status="draft", batch_id="g2")

        def identity():
            return 0.5

        res = EpSafeCommit(proj, "g2", grounding_fn=identity).run([p1["id"]])
        assert res["ok"] is False
        assert res["checks"]["grounding"]["status"] == "fail"


class TestW3PipelineWiring:
    """#990 — EpSafeCommit enforcement in the extraction pipeline."""

    def test_mine_stamps_batch_id_and_commits(self, mining_sdk):
        """mine_conversation stamps batch_id on extraction Points and runs
        the W-3 gate — a clean batch is committed (additive result keys)."""
        import tortoise.mining as mining
        sdk = mining_sdk
        proj = sdk._get_proj()
        from tortoise.log import EventLog
        import tempfile, os
        log = EventLog(os.path.join(tempfile.mkdtemp(), "e.jsonl"))
        from tortoise.api import EventAPI
        api = EventAPI(log, initiated_by="extractor", agent_id="t",
                       projection=proj)
        result = mining.mine_conversation(
            "Alice: We decided to move the FalkorDB default port to 16379.\n"
            "Bob: I disagree because changing port 16379 breaks the redis config.\n"
            "Alice: But tortoise#123 tracks the migration work.\n",
            "session_s990a", api)
        assert result["batch_id"], "batch_id must be returned"
        assert result["points"] > 0, "fixture transcript must produce points"
        assert result["batch_status"] == "committed", result
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.batch_id = $bid RETURN count(n)",
            params={"bid": result["batch_id"]},
        ).result_set
        assert rows[0][0] == result["points"], (
            "every extraction Point must carry the batch_id"
        )

    def test_mine_without_projection_not_gated(self, mining_sdk):
        """Standalone log mode (no projection) skips the gate and says so."""
        import tortoise.mining as mining
        from tortoise.log import EventLog
        import tempfile, os
        log = EventLog(os.path.join(tempfile.mkdtemp(), "e.jsonl"))
        from tortoise.api import EventAPI
        api = EventAPI(log, initiated_by="extractor", agent_id="t")  # no projection
        result = mining.mine_conversation(
            "We decided to fix tortoise#123 today.", "session_s990b", api)
        assert result["batch_status"] == "not_gated"
        assert "projection" in result["batch_reason"]

    def test_batch_state_survives_rebuild(self, mining_sdk, tmp_path):
        """#990 durability: a quarantined batch stays quarantined after
        wipe+rebuild_all (:Batch marker snapshot)."""
        import tortoise.mining as mining
        sdk = mining_sdk
        proj = sdk._get_proj()
        p = sdk.create_point("decision", "a", status="draft", batch_id="dur1")
        mining.quarantine_batch(proj, "dur1", reason="EP drift (W-3)")
        assert mining.batch_status(proj, "dur1")["status"] == "quarantined"
        rebuilt = proj.rebuild_all(str(tmp_path))
        assert rebuilt["events"] >= 0
        bs = mining.batch_status(proj, "dur1")
        assert bs is not None and bs["status"] == "quarantined", (
            "quarantine lock must survive rebuild_all"
        )
