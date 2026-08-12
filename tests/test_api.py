"""API coverage tests — remaining gaps in api.py.

Runs standalone:  .venv/bin/python tests/test_api.py
or via pytest if installed.
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.api import EventAPI, provenance          # noqa: E402
from tortoise.idempotency import document_key          # noqa: E402
from tortoise.log import EventLog                       # noqa: E402
from tortoise.projection import fold                    # noqa: E402


# ── helpers (same pattern as test_m1.py) ──────────────────────────────

def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _api(projection=None):
    log = EventLog(_tmp("events.jsonl"))
    return EventAPI(log, initiated_by="extractor", agent_id="test",
                    projection=projection), log


def _build(api, source="doc.txt"):
    prov = provenance(source, [0, 10], "quote", extracted_by="test@0")
    a = api.add_point("we should raise B slowly", prov)
    b = api.add_point("fast raises wreck early buyers", prov)
    op = api.add_operator("IMPL", [b, a], prov)
    return a, b, op


# ── coverage gap: lines 76-78 (retract superseded points) ────────────

def test_reprocess_retracts_superseded_points():
    """begin_ingest with new version emits PointRetracted for old-run points."""
    api, log = _api()
    text = "the quick brown fox"
    prov = provenance("doc.txt", [0, 19], text, extracted_by="test@0")

    # Run 1 (v1)
    api.begin_ingest("doc.txt", "v1", document_key(text))
    p1 = api.add_point("old extraction", prov)

    # Run 2 (v2) — should retract p1
    r2 = api.begin_ingest("doc.txt", "v2", document_key(text))
    assert not r2.skip

    events = log.read_all()
    retracted_ids = [
        e["id"] for e in events if e["type"] == "PointRetracted"
    ]
    assert p1 in retracted_ids, (
        f"retracted ids {retracted_ids} should contain {p1}"
    )

    # Verify fold: old point is a tombstone (status='retracted'), not hard-deleted (#689)
    points = fold(events)
    assert p1 in points, "superseded point tombstone must survive fold (#689)"
    assert points[p1].get("status") == "retracted"

    print("PASS test_reprocess_retracts_superseded_points")


def test_reprocess_retracts_only_old_run_points():
    """Superseded retraction targets only the old run, not fresh points."""
    api, log = _api()
    text = "abc"
    prov = provenance("doc.txt", [0, 3], text, extracted_by="test@0")

    api.begin_ingest("doc.txt", "v1", document_key(text))
    old_a = api.add_point("old A", prov)
    old_b = api.add_point("old B", prov)

    # Reprocess: old points retracted, new points in fresh run survive
    api.begin_ingest("doc.txt", "v2", document_key(text))
    new_c = api.add_point("new C", prov)

    events = log.read_all()
    retracted = {e["id"] for e in events if e["type"] == "PointRetracted"}
    assert old_a in retracted and old_b in retracted, \
        "both old-run points must be retracted"
    assert new_c not in retracted, \
        "point from new run must NOT be among retracted"

    points = fold(events)
    assert new_c in points
    # #689: old points are tombstones, not hard-deleted
    assert old_a in points and old_b in points
    assert points[old_a].get("status") == "retracted"
    assert points[old_b].get("status") == "retracted"

    print("PASS test_reprocess_retracts_only_old_run_points")


# ── coverage gap: lines 115-116 (auto-compute grounding) ─────────────

class _MockGroundingProjection:
    """Projection stub with compute_grounding — for testing the auto-trigger."""

    def __init__(self):
        self.calls: list[dict] = []
        self.points: dict[str, dict] = {}

    def apply(self, event: dict) -> None:
        from tortoise.projection import _apply_one
        _apply_one(self.points, event)

    def compute_grounding(self):
        self.calls.append({"called": True})

    def rebuild(self, log):
        self.points = fold(log.read_all())


def test_resolution_event_triggers_grounding():
    """add_point with pointKind='resolution-event' calls compute_grounding.

    #49: context is deprecated — grounding keys off pointKind now.
    """
    proj = _MockGroundingProjection()
    api, _log = _api(projection=proj)
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")

    api.add_point("foo", prov, pointKind="resolution-event")
    assert len(proj.calls) == 1, "compute_grounding must fire once"

    print("PASS test_resolution_event_triggers_grounding")


def test_resolution_event_legacy_context_no_grounding():
    """Legacy context='resolution-event' (no pointKind) must NOT fire grounding.

    #49 deprecation: the trigger is pointKind, not the context param.
    """
    proj = _MockGroundingProjection()
    api, _log = _api(projection=proj)
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")

    api.add_point("foo", prov)
    assert len(proj.calls) == 0, "legacy context must not trigger grounding"

    print("PASS test_resolution_event_legacy_context_no_grounding")


def test_normal_context_does_not_trigger_grounding():
    """add_point with normal context must NOT call compute_grounding."""
    proj = _MockGroundingProjection()
    api, _log = _api(projection=proj)
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")

    api.add_point("bar", prov)
    assert len(proj.calls) == 0, "normal context must not trigger grounding"

    print("PASS test_normal_context_does_not_trigger_grounding")


def test_resolution_event_no_projection_safe():
    """resolution-event with no projection attached must not crash."""
    api, log = _api(projection=None)
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    pid = api.add_point("z", prov, pointKind="resolution-event")
    assert pid and isinstance(pid, str)
    print("PASS test_resolution_event_no_projection_safe")


# ── add_operator edge cases ──────────────────────────────────────────

def test_add_operator_normalizes_dict_inputs():
    """Inputs as dicts with 'id' key → normalized to ULID strings."""
    api, log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    pid = api.add_operator(
        "NAND",
        [{"id": "aaa", "x": 1}, {"id": "bbb", "y": 2}],
        prov,
    )
    points = fold(log.read_all())
    assert points[pid]["operator"]["inputs"] == ["aaa", "bbb"]
    print("PASS test_add_operator_normalizes_dict_inputs")


class _FakeNode:
    """Object with .id attribute — simulates a Point-like object."""
    def __init__(self, id_: str):
        self.id = id_


def test_add_operator_normalizes_object_inputs():
    """Inputs as objects with .id → normalized to ULID strings."""
    api, log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    pid = api.add_operator(
        "IMPL",
        [_FakeNode("n1"), _FakeNode("n2")],
        prov,
    )
    points = fold(log.read_all())
    assert points[pid]["operator"]["inputs"] == ["n1", "n2"]
    print("PASS test_add_operator_normalizes_object_inputs")


def test_add_operator_invalid_type():
    """Invalid op_type raises ValueError (#331: explicit validation, not a
    bare assert that vanishes under python -O)."""
    api, _log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    try:
        api.add_operator("XOR", ["a"], prov)
        assert False, "should have raised"
    except ValueError as e:
        assert "XOR" in str(e)
    print("PASS test_add_operator_invalid_type")


def test_add_operator_fallback_inputs():
    """Non-dict, non-.id inputs pass through as-is (raw strings)."""
    api, log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    pid = api.add_operator("IMPL", ["raw_str_1", "raw_str_2"], prov)
    points = fold(log.read_all())
    assert points[pid]["operator"]["inputs"] == ["raw_str_1", "raw_str_2"]
    print("PASS test_add_operator_fallback_inputs")


# ── _emit corrects passthrough ───────────────────────────────────────

def test_emit_sets_corrects():
    """_emit passes corrects to the event dict."""
    api, log = _api()
    api.current_run = "run-1"
    ev = api._emit("TestEvent", corrects="ev-abc", key="val")
    assert ev["corrects"] == "ev-abc"
    assert ev["type"] == "TestEvent"
    assert ev["initiated_by"] == "extractor"
    assert ev["agent_id"] == "test"
    assert ev["key"] == "val"
    assert "event_id" in ev and "ts" in ev
    # Verify in log
    stored = [e for e in log.read_all() if e["event_id"] == ev["event_id"]]
    assert len(stored) == 1 and stored[0]["corrects"] == "ev-abc"
    print("PASS test_emit_sets_corrects")


def test_emit_no_corrects():
    """_emit with no corrects → corrects is None in the event."""
    api, log = _api()
    ev = api._emit("NoCorrectsEvent", payload=42)
    assert ev["corrects"] is None
    stored = [e for e in log.read_all() if e["event_id"] == ev["event_id"]]
    assert stored[0]["corrects"] is None
    print("PASS test_emit_no_corrects")


# ── begin_ingest force ────────────────────────────────────────────────

def test_begin_ingest_force():
    """force=True skips the idempotency cache."""
    api, log = _api()
    text = "force me"
    prov = provenance("doc.txt", [0, 3], text, extracted_by="test@0")

    r1 = api.begin_ingest("doc.txt", "v1", document_key(text))
    assert not r1.skip
    old = api.add_point("old", prov)

    # Same key + version, but force → must reprocess
    r2 = api.begin_ingest("doc.txt", "v1", document_key(text), force=True)
    assert not r2.skip, "force=True must skip the idempotency gate"
    assert r2.run_id != r1.run_id, "force=True must produce a new run_id"

    points = fold(log.read_all())
    # #689: tombstone — old point exists with status='retracted'
    assert old in points, "forced reprocess must leave tombstone (#689)"
    assert points[old].get("status") == "retracted"
    print("PASS test_begin_ingest_force")


def test_begin_ingest_force_supersedes():
    """force=True with no prior points doesn't crash (superseded=[])."""
    api, _log = _api()
    text = "clean slate"
    r1 = api.begin_ingest("doc.txt", "v1", document_key(text), force=True)
    assert not r1.skip
    # No points added in v1 at all → superseded empty → retraction loop is no-op
    r2 = api.begin_ingest("doc.txt", "v1", document_key(text), force=True)
    assert not r2.skip
    print("PASS test_begin_ingest_force_supersedes")


# ── merge_points ─────────────────────────────────────────────────────

def test_merge_points_emits_and_removes():
    """merge_points emits PointsMerged and removes merge_ids from fold."""
    api, log = _api()
    a, b, op = _build(api)
    ev = api.merge_points(keep_id=a, merge_ids=[b], corrects=op)
    assert ev and isinstance(ev, str)

    # Verify PointsMerged event in the log
    merged_events = [e for e in log.read_all() if e["type"] == "PointsMerged"]
    assert len(merged_events) == 1
    me = merged_events[0]
    assert me["keep_id"] == a
    assert me["merge_ids"] == [b]
    assert me["corrects"] == op

    # Verify fold: b removed, a remains
    points = fold(log.read_all())
    assert a in points
    assert b not in points, "merged-away point must be absent from fold"
    print("PASS test_merge_points_emits_and_removes")


def test_merge_points_no_corrects():
    """merge_points with corrects=None works fine."""
    api, log = _api()
    a, b, _op = _build(api)
    ev = api.merge_points(keep_id=a, merge_ids=[b])
    merged = [e for e in log.read_all() if e["type"] == "PointsMerged"]
    assert merged[0]["corrects"] is None
    assert b not in fold(log.read_all())
    print("PASS test_merge_points_no_corrects")


def test_projection_matches_log_after_every_mutation():
    """#12: projection state == fold(log) after every API mutation."""
    from tortoise.projection import InMemoryProjection
    proj = InMemoryProjection()
    api, log = _api(projection=proj)

    def _check():
        assert proj.points == fold(log.read_all()), \
            f"projection drifted from log after {log.read_all()[-1]['type']}"

    prov = provenance("doc.txt", [0, 10], "quote", extracted_by="test@0")

    # add_point
    a = api.add_point("statement A", prov)
    _check()

    b = api.add_point("statement B", prov)
    _check()

    # add_operator
    op = api.add_operator("IMPL", [b, a], prov)
    _check()

    # revise_point
    api.revise_point(a, new_content="revised A", corrects="fix-1")
    _check()

    # retract_point
    api.retract_point(b, corrects="fix-2")
    _check()

    # merge_points
    c = api.add_point("statement C", prov)
    api.merge_points(keep_id=op, merge_ids=[c])
    _check()

    print("PASS test_projection_matches_log_after_every_mutation")


# ── runner ────────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall API coverage tests passed")


if __name__ == "__main__":
    _run_all()


# ------------------------------------------------------------------ #125 capture API


def test_add_document_capture_fields():
    """#125: add_document persists topics/summary/session_id/event_id in the event."""
    api, log = _api()
    api.add_document("doc-1", "Conv", document_kind="transcript",
                     topics=["licensing", "AGPL"], summary="Compared",
                     session_id="s1", event_id="evt-1", about_entities=["agent-pi"])
    events = log.read_all()
    created = [e for e in events if e["type"] == "DocumentCreated"]
    assert len(created) == 1
    assert created[0]["topics"] == ["licensing", "AGPL"]
    assert created[0]["summary"] == "Compared"
    assert created[0]["session_id"] == "s1"
    assert created[0]["event_id"] == "evt-1"
    assert created[0]["about_entities"] == ["agent-pi"]


def test_add_document_partial_update_preserves_source_path():
    """#167 (P0 fix): partial update through add_document WITHOUT source_path
    must NOT wipe an existing sourcePath. Regression: "" default was non-null
    in Cypher and coalesce("", d.sourcePath) wiped it."""
    api, log = _api()
    api.add_document("doc-sp2", "With Source", document_kind="transcript",
                     source_path="/tmp/test.md")
    # Partial update via API — source_path omitted entirely
    api.add_document("doc-sp2", "With Source", document_kind="transcript",
                     summary="Updated only")
    events = [e for e in log.read_all() if e["type"] == "DocumentCreated"]
    assert len(events) == 2
    assert events[0]["source_path"] == "/tmp/test.md"
    # Second event must carry source_path=None (not "") so projection coalesce
    # preserves the value
    assert events[1]["source_path"] is None, f"got {events[1]['source_path']!r}"


def test_add_document_partial_update_preserves_capture_fields():
    """#167 P2: summary/session_id/event_id defaults None so partial updates
    through add_document don't wipe existing values via coalesce("", d.x, '')."""
    api, log = _api()
    api.add_document("doc-sp3", "Full", document_kind="transcript",
                     summary="Original summary", session_id="s9", event_id="e9")
    # Partial update — none of the capture fields provided
    api.add_document("doc-sp3", "Full", document_kind="transcript",
                     doc_status="archived")
    events = [e for e in log.read_all() if e["type"] == "DocumentCreated"]
    assert len(events) == 2
    e0, e1 = events
    assert e0["summary"] == "Original summary" and e0["session_id"] == "s9"
    assert e1["summary"] is None, f"got {e1['summary']!r}"
    assert e1["session_id"] is None, f"got {e1['session_id']!r}"
    assert e1["event_id"] is None, f"got {e1['event_id']!r}"


def test_add_event_emits_eventrecorded():
    """#125: add_event emits EventRecorded with id=eid and full payload."""
    api, log = _api()
    eid = api.add_event("evt-1", "sessionCaptured", subject="agent-pi",
                        object_name="doc-1", object_type="Document",
                        uses=[{"name": "tortoise-capture", "kind": "skill"}],
                        about_entities=["agent-pi"])
    assert eid == "evt-1"
    events = log.read_all()
    evs = [e for e in events if e["type"] == "EventRecorded"]
    assert len(evs) == 1
    assert evs[0]["id"] == "evt-1"  # id, not eventId — matches _upsert_event lookup
    assert evs[0]["eventKind"] == "sessionCaptured"
    assert evs[0]["object"] == "doc-1"
    assert evs[0]["objectType"] == "Document"
    assert evs[0]["uses"] == [{"name": "tortoise-capture", "kind": "skill"}]


# ── #331: crash-vector regression tests ────────────────────────────────

def test_merge_points_none_inputs_graceful():
    """#331: merge_points(None) must not raise TypeError — treat as empty."""
    api, log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    a = api.add_point("point a", prov)

    eid = api.merge_points(a, None)
    assert eid
    events = [e for e in log.read_all() if e["type"] == "PointsMerged"]
    assert len(events) == 1
    assert events[0]["keep_id"] == a
    assert events[0]["merge_ids"] == []

    # empty list must behave identically
    eid2 = api.merge_points(a, [])
    assert eid2
    print("PASS test_merge_points_none_inputs_graceful")


def test_merge_points_single_string_id():
    """#331: a bare string merge id is treated as one id, not char-split."""
    api, log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    a = api.add_point("point a", prov)
    api.merge_points(a, "p-xyz")
    events = [e for e in log.read_all() if e["type"] == "PointsMerged"]
    assert events[0]["merge_ids"] == ["p-xyz"]
    print("PASS test_merge_points_single_string_id")


def test_add_operator_rejects_non_string_inputs():
    """#331: non-string operator inputs must raise TypeError, not crash
    in the label join."""
    api, _log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    for bad in ([1, 2], [None], [b"bytes"], [{"no_id": 1}], 42, None):
        try:
            api.add_operator("IMPL", bad, prov)
            assert False, f"should have raised TypeError for {bad!r}"
        except TypeError:
            pass
    print("PASS test_add_operator_rejects_non_string_inputs")


def test_add_operator_rejects_non_string_op_type():
    """#331: non-string op_type must raise ValueError (a bare assert vanishes
    under python -O — validate explicitly)."""
    api, _log = _api()
    prov = provenance("doc.txt", [0, 1], "x", extracted_by="test@0")
    for bad in (None, 123, b"NAND", ["NAND"]):
        try:
            api.add_operator(bad, ["a"], prov)
            assert False, f"should have raised ValueError for {bad!r}"
        except ValueError as e:
            assert "gate" in str(e)
    print("PASS test_add_operator_rejects_non_string_op_type")
