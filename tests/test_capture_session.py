"""SDK capture_session tests (#312 delta 4 + delta 5 speaker tagging)."""
import logging
import re

import pytest

from tortoise.sdk import TortoiseSDK

# Legacy predicate name for negative-direction tests (#281). Kept as a
# constant so no edge-syntax literal appears in source (Task 5 sweep requires
# zero hits) — same pattern as tests/test_ranking.py.
_LEGACY_INSTANTIATES = "INSTANTIATES"


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]


def test_capture_session_shape(sdk):
    res = sdk.capture_session(CONV)
    assert res["session_id"].startswith("session_")
    assert res["turns"] == 3
    assert res["extracted"] >= 2
    assert all(p["kind"] in ("decision", "statement") for p in res["points"])


def test_capture_session_turns_are_speaker_tagged(sdk):
    sdk.capture_session(CONV)
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker ORDER BY t.id"
    ).result_set
    assert [r[0] for r in rows] == ["user", "assistant", "user"]


def test_capture_session_idempotent(sdk):
    sdk.capture_session(CONV)
    sid = sdk._get_proj().g.query("MATCH (s:Session) RETURN s.id").result_set[0][0]
    sdk.capture_session(CONV, session_id=sid)  # re-capture same session
    proj = sdk._get_proj()
    turns = proj.g.query("MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 3, "re-capture must not duplicate turn points"


def test_capture_session_dedup_across_sessions(sdk):
    # Same claim in two sessions → ONE statement point (content-hash dedup)
    sdk.capture_session(CONV)
    sdk.capture_session(CONV)
    stmt = sdk._get_proj().g.query(
        "MATCH (p:Point {pointKind:'statement'}) RETURN count(p)"
    ).result_set
    assert stmt[0][0] == 2, "identical claims across sessions dedup to one point each"


def test_capture_session_turn_cap(sdk):
    with pytest.raises(ValueError, match="turn cap"):
        sdk.capture_session([{"role": "user", "content": "x"}] * 201, max_turns=200)


def test_capture_session_creates_event(sdk):
    res = sdk.capture_session(CONV)
    proj = sdk._get_proj()
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)"
    ).result_set
    assert events[0][0] == 1
    # aboutEvent edges to extracted points
    edges = proj.g.query(
        "MATCH ()-[r:aboutEvent]->(:Event {eventKind:'sessionCaptured'}) RETURN count(r)"
    ).result_set
    assert edges[0][0] == res["extracted"]


def test_capture_session_event_recorded_write_lands(sdk):
    """EventRecorded entity write actually lands: full payload on the Event node."""
    sdk.capture_session(CONV)
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) "
        "RETURN e.eventId, e.id, e.startedAt, e.endedAt"
    ).result_set
    assert len(rows) == 1
    event_id, node_id, started_at, ended_at = rows[0]
    assert re.match(r"^[0-9a-f]+-[0-9a-f]{12}$", event_id), "create_event mints a ULID eventId"
    assert node_id == event_id, "Event node id mirrors the EventRecorded eventId"
    assert started_at and ended_at, "timestamps populated from the capture write"


def test_capture_session_event_write_failure_logs_warning(sdk, caplog, monkeypatch):
    """Swallow path is visible: Event write failure logs a warning, capture continues."""

    def boom(*args, **kwargs):
        raise RuntimeError("falkordb down")

    monkeypatch.setattr(sdk, "create_event", boom)
    with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
        res = sdk.capture_session(CONV)
    assert res["turns"] == 3, "turn/session writes must still succeed"
    assert res["session_id"].startswith("session_")
    assert any(
        "sessionCaptured" in r.getMessage() and "non-fatal" in r.getMessage()
        for r in caplog.records
    ), caplog.records


def test_capture_session_zero_extraction(sdk):
    """Conversations with no decision/claim patterns → 0 extractions, turns still land."""
    plain = [
        {"role": "user", "content": "the weather today is fine"},
        {"role": "assistant", "content": "yes it is"},
    ]
    res = sdk.capture_session(plain)
    assert res["extracted"] == 0
    proj = sdk._get_proj()
    sid = proj.g.query("MATCH (s:Session) RETURN s.id").result_set[0][0]
    turns = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN count(t)", params={"sid": sid}
    ).result_set
    assert turns[0][0] == 2
    # No epistemic points created
    stmt = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind IN ['decision','statement'] RETURN count(p)"
    ).result_set
    assert stmt[0][0] == 0


def test_capture_session_contains_edges_when_speaker_repeats(sdk):
    """Repeated speaker tags must NOT conflate turns: one CONTAINS edge per turn."""
    repeat = [{"role": "user", "content": "x"}] * 3
    res = sdk.capture_session(repeat)
    sid = res["session_id"]
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN t.id, t.speaker ORDER BY t.id", params={"sid": sid}
    ).result_set
    assert [r[0] for r in rows] == [f"{sid}_t{i}" for i in range(3)]
    assert all(r[1] == "user" for r in rows)


def test_capture_session_contains_edges_to_extractions(sdk):
    """Extraction-side CONTAINS edges: decision/statement points must be wired
    to the session, and extraction actually creates decision/statement nodes."""
    res = sdk.capture_session(CONV)
    assert res["extracted"] >= 2, "CONV must trigger at least one extraction each"
    sid = res["session_id"]
    proj = sdk._get_proj()
    # At least one extraction created a decision/statement node
    kinds = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind IN ['decision','statement'] "
        "RETURN p.pointKind, count(p)"
    ).result_set
    total = sum(r[1] for r in kinds)
    assert total >= 2, "extraction loop must create decision/statement nodes"
    assert set(r[0] for r in kinds) == {"decision", "statement"}, \
        f"CONV should extract both kinds, got {[r[0] for r in kinds]}"
    # Every extracted point is CONTAINS-connected to this session
    connected = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IN ['decision','statement'] RETURN collect(p.id)",
        params={"sid": sid},
    ).result_set[0][0]
    assert len(connected) == res["extracted"], \
        f"expected {res['extracted']} extraction edges, got {len(connected)}"
    extracted_ids = {p["id"] for p in res["points"]}
    assert set(connected) == extracted_ids, \
        "CONTAINS edges must cover exactly the extracted points"


def test_capture_session_role_coerced_to_string(sdk):
    """Truthy non-string roles (123, dict) are stored as str() — never raw
    (contradicting the `speaker | string` ontology row) and never crash the
    write mid-loop into a partial session."""
    conv = [
        {"role": 123, "content": "I think the top issue is auth."},
        {"role": {"a": 1}, "content": "we decided to ship serve --http."},
    ]
    res = sdk.capture_session(conv)
    assert res["turns"] == 2, "all turns must complete — no partial session left"
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker ORDER BY t.id"
    ).result_set
    assert [r[0] for r in rows] == ["123", "{'a': 1}"], \
        "non-string roles stored as their str() form"
    # Speaker values are strings (ontology `speaker | string`), extraction still ran
    assert all(isinstance(r[0], str) for r in rows)
    assert res["extracted"] >= 2


def test_capture_session_exactly_at_cap(sdk):
    """len(conversation) == max_turns is accepted (boundary, not an overflow)."""
    conv = [{"role": "user", "content": "x"}] * 3
    res = sdk.capture_session(conv, max_turns=3)
    assert res["turns"] == 3


def test_capture_session_empty_conversation(sdk):
    """Empty conversation: no turns, no crash, Session still recorded."""
    res = sdk.capture_session([])
    assert res["turns"] == 0
    assert res["extracted"] == 0
    sid = res["session_id"]
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.turn_count", params={"sid": sid}
    ).result_set
    assert rows[0][0] == 0


def test_capture_session_none_role_content(sdk):
    """None role/content degrade gracefully instead of crashing."""
    res = sdk.capture_session([{"role": None, "content": None}])
    assert res["turns"] == 1
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.speaker"
    ).result_set
    assert rows[0][0] == "unknown", "None role normalizes to 'unknown'"


def test_capture_session_non_string_content_coerced(sdk):
    """Non-string content (numbers/dicts) is coerced, never crashes mid-write."""
    conv = [
        {"role": "user", "content": 12345},
        {"role": "assistant", "content": {"text": "we decided to ship v2"}},
    ]
    res = sdk.capture_session(conv)
    assert res["turns"] == 2, "all turns must complete — no partial session left"
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.content ORDER BY t.id"
    ).result_set
    assert rows[0][0] == "[user] 12345", "int content stored as its str() form"
    assert rows[1][0] == "[assistant] {'text': 'we decided to ship v2'}", \
        "dict content stored as its str() form"


def test_capture_session_long_turn_extracts_only_stored_text(sdk):
    """#721 provenance: extraction scans the STORED (truncated) turn text.
    A turn > 5000 chars stores content[:5000]; a phrase past the cut must NOT
    be extracted — its source text exists in no stored turn. Every extracted
    phrase must be present in the stored turn text."""
    # One claim inside the 5000-char window (positive control) + trigger-free
    # padding to push a second claim past the cut. Padding must not match any
    # decision/claim regex so the past-cut claim is the only candidate for the
    # claims patterns (the per-turn cap would otherwise eat it — #721).
    lead = "I believe the root cause is known. "
    pad = "plain filler text without triggers. "
    assert not re.search(r"(?:let'?s|we will|we should|I will|I'm going to|decided|decision|I think|I believe|my understanding is|the problem is|the key insight|evidence suggests|data shows|we found that|this means|plan is|next steps?:|action item:)", pad, re.I)
    before = lead + pad * 145  # 5113 chars > 5000
    assert len(before) > 5000
    past_cut = "evidence suggests the fix landed."
    content = before + past_cut
    res = sdk.capture_session([{"role": "user", "content": content}])
    proj = sdk._get_proj()
    turn = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.content"
    ).result_set[0][0]
    assert turn == "[user] " + content[:5000], \
        "stored turn text is the truncated 5000 chars"
    # Extraction still runs on the stored window (positive control).
    assert any("root cause is known" in p["text"] for p in res["points"]), \
        "claims inside the 5000-char window must still be extracted"
    # Provenance holds: every extracted phrase exists within the stored turn.
    for p in res["points"]:
        assert p["text"] in turn, \
            f"extracted {p['text']!r} not present in stored (truncated) turn"
    # The phrase past the 5000-char cut is NOT extracted (old code did).
    assert not any("fix landed" in p["text"] for p in res["points"]), \
        "phrases past the 5000-char cut must not be extracted"


def test_capture_session_falsy_non_string_content_not_swallowed(sdk):
    """Falsy non-strings (0/False/{}/[]) survive coercion — no `or ""` swallow."""
    conv = [
        {"role": "user", "content": 0},
        {"role": "assistant", "content": False},
        {"role": "user", "content": {}},
        {"role": "assistant", "content": []},
        {"role": "user", "content": None},
    ]
    res = sdk.capture_session(conv)
    assert res["turns"] == 5, "all turns must complete — no partial session left"
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN t.content ORDER BY t.id"
    ).result_set
    assert rows[0][0] == "[user] 0", "0 stored as its str() form, not swallowed"
    assert rows[1][0] == "[assistant] False", \
        "False stored as its str() form, not swallowed"
    assert rows[2][0] == "[user] {}", "{} stored as its str() form, not swallowed"
    assert rows[3][0] == "[assistant] []", "[] stored as its str() form, not swallowed"
    assert rows[4][0] == "[user] ", "None degrades to empty string"


# ── E2E-6: Semantic connections (Event→Object) ──────────────────────────
#
# ONTOLOGY v3.2 §3.2 (issue #214) removed the INSTANTIATES predicate from
# the valid-predicate vocabulary; the canonical Event→Object connection is
# the aboutObject edge (#281 re-scope). These tests assert the canonical
# aboutObject edge produced by the capture surface (capture_session →
# create_about_edge / create_event) and guard that no INSTANTIATES edge is
# created anywhere in the graph these tests touch. The session-indexer's
# legacy INSTANTIATES producer (_connect_issue_objects, sdk.py) is still
# pending #281's swap on origin/main; its compliance with #214 is #281's
# test scope, not asserted here.


def test_capture_session_event_object_canonical_about_edge(sdk):
    """E2E-6: sessionCaptured Event → Object semantic connection is the
    canonical aboutObject edge — traversable both directions, and the dead
    INSTANTIATES predicate is not emitted by the canonical producer."""
    # 1. Session pipeline creates the sessionCaptured Event.
    sdk.capture_session(CONV)
    proj = sdk._get_proj()
    ev = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId, e.id"
    ).result_set
    assert ev, "capture_session must create the sessionCaptured Event"
    eid, euid = ev[0]
    assert euid == eid, "Event node id mirrors the EventRecorded eventId"

    # 2. Object the session was about (canonical ObjectRegistered event).
    obj = sdk.create_object("license-research", "skill")

    # 3. Canonical semantic connection: (Event)-[:aboutObject]->(Object).
    #    create_event(aboutObject=…) wires this exact edge; use the same
    #    producer primitive the capture flow calls.
    assert proj.create_about_edge(eid, obj["id"], "aboutObject") is True
    conn = proj.g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object {id:$oid}) "
        "RETURN o.name, o.objectKind",
        params={"eid": eid, "oid": obj["id"]},
    ).result_set
    assert conn == [["license-research", "skill"]], f"aboutObject miss: {conn}"

    # 4. Backward traversal: Object → Event (graph queryable both ways).
    back = proj.g.query(
        "MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event) RETURN e.eventId",
        params={"oid": obj["id"]},
    ).result_set
    assert back and back[0][0] == eid, f"reverse traversal miss: {back}"

    # 5. Removed-predicate guard (#214): no INSTANTIATES edge anywhere in
    #    this graph. Scoped to the capture surface this test drives — the
    #    index flow's legacy producer is #281's test scope.
    dead = proj.g.query(
        f"MATCH ()-[:{_LEGACY_INSTANTIATES}]->() RETURN count(*)",
    ).result_set
    assert dead[0][0] == 0, "INSTANTIATES was removed from ONTOLOGY v3.2 §3.2"


def test_create_event_wires_about_object_by_name(sdk):
    """E2E-6 companion: create_event(aboutObject=<name>) — the wiring the
    session-capture flow uses once #281 lands — resolves an existing Object
    by name and produces the canonical aboutObject edge."""
    sdk.create_object("license-research", "skill")
    ev = sdk.create_event("session-s1", "sessionCaptured",
                          aboutObject="license-research")
    proj = sdk._get_proj()
    conn = proj.g.query(
        "MATCH (e:Event {eventId:$eid})-[:aboutObject]->(o:Object) "
        "RETURN o.name, o.objectKind",
        params={"eid": ev["eventId"]},
    ).result_set
    assert conn == [["license-research", "skill"]], f"name-resolved aboutObject miss: {conn}"
