"""GAP-15 #7003: Conversation mining pipeline tests.

ConversationMiner → extractor → ≥3 EventRecorded events per session.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI          # noqa: E402
from tortoise.log import EventLog          # noqa: E402
from tortoise.mining import ConversationMiner   # noqa: E402


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


if __name__ == "__main__":
    test_mine_sample_transcript()
    test_mine_events_have_required_fields()
    test_mine_derives_decisions()
    test_mine_derives_friction()
    test_mine_sparse_transcript()
    test_mine_preserves_point_content()
