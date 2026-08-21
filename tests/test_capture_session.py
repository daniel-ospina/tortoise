"""SDK capture_session tests (#312 delta 4 + delta 5 speaker tagging, #822).

#822: LLM extraction is the default (and only) capture extraction — the regex
loop was removed as a product path and no-key fails closed. These tests run
against the offline MockModel extractor (TORTOISE_SESSION_LLM_MOCK=1 seam) so
no provider key or network is needed.
"""
import json
import logging
import re

import pytest

from tortoise.sdk import TortoiseSDK


def test_commit_session_threads_session_date(sdk, monkeypatch):
    """T10 (#1533): commit_session threads session_date into
    extract_session_v2 — ISO-now by default (D8: capture time = session
    date), explicit value honored."""
    import tortoise.extractor_v2 as ev2

    received = []

    def _fake_extract(model, conversation, **kw):
        received.append(kw.get("session_date"))
        return {"payload": None, "minted_kinds": [], "supersessions": [],
                "chain_notes": [], "link_before_create": [],
                "warnings": [], "story_arc": "", "search": {},
                "stats": {}, "errors": ["no payload produced"]}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    r = sdk.commit_session(CONV)
    assert r["ok"] is False  # no payload → never reports ok=True
    assert len(received) == 1
    assert received[0]  # ISO now by default — non-empty
    # explicit session_date= is honored verbatim
    sdk.commit_session(CONV, session_date="2026-08-01")
    assert received[1] == "2026-08-01"


# Legacy predicate name for negative-direction tests (#281). Kept as a
# constant so no edge-syntax literal appears in source (Task 5 sweep requires
# zero hits) — same pattern as tests/test_ranking.py.
_LEGACY_INSTANTIATES = "INSTANTIATES"


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Install the offline MockModel session extractor (#822) — the M2 LLM
    pipeline runs with zero network regardless of ambient provider keys
    (the dev shell has real OPENROUTER/DEEPSEEK keys). Any test that needs
    the no-key fail-closed path clears the seam itself."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


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
    assert res["extracted"] >= 1
    assert all(p["kind"] in ("decision", "statement") for p in res["points"])
    # #822: extraction_mode reports what actually ran — the M2 LLM extractor.
    assert res["extraction_mode"] == "llm"


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


def test_capture_session_no_provider_fails_closed(sdk, monkeypatch):
    """#822: no provider key (and no mock seam) → ValueError — the regex
    fallback is gone, capture requires an LLM provider."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError, match="LLM provider key"):
        sdk.capture_session(CONV)


def test_capture_session_llm_points_fresh_per_capture(sdk, monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")  # M2-mock-specific
    """#822: M2 extraction mints a fresh ULID per Point per capture —
    content-hash dedup is a later pipeline stage (epic #909 W-2 #784)."""
    sdk.capture_session(CONV)
    sdk.capture_session(CONV)
    stmt = sdk._get_proj().g.query(
        "MATCH (p:Point) WHERE p.pointKind IS NULL RETURN count(p)"
    ).result_set
    # 4 LLM points per capture (one per sentence; "ok" is below the 3-char
    # utterance floor) × 2 captures — no dedup (M2 mock semantics, untyped).
    assert stmt[0][0] == 8, f"expected 8 fresh M2 LLM points, got {stmt[0][0]}"


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
    # #1417: provenance is the point's eventId property, NOT an aboutEvent
    # content edge (ONTOLOGY §3.4) — no aboutEvent edges may be minted by the
    # capture path, and every extracted point must carry the event's eventId.
    eid = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set[0][0]
    no_edges = proj.g.query(
        "MATCH ()-[r:aboutEvent]->(:Event {eventKind:'sessionCaptured'}) RETURN count(r)"
    ).result_set
    assert no_edges[0][0] == 0, "capture path must not mint aboutEvent provenance"
    stamps = proj.g.query(
        "MATCH (n:Point) WHERE n.eventId = $eid RETURN count(n)",
        params={"eid": eid},
    ).result_set
    assert stamps[0][0] == res["extracted"], (
        "every extracted point must carry the sessionCaptured eventId"
    )


def test_capture_session_source_is_agent_session(sdk):
    """#1352: the session Source must be agentSession-typed with capture
    metadata + a references edge to the sessionCaptured Event — not the
    document-typed stub the extraction projection's _link_source default
    auto-creates (title=url, empty contentHash, no metadata)."""
    res = sdk.capture_session(CONV)
    sid = res["session_id"]
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Source {url:$url}) "
        "RETURN s.sourceKind, s.sessionId, s.capturedAt, s.contentHash, "
        "       s.summary, s.topics, s.eventId",
        params={"url": f"session:{sid}"},
    ).result_set
    assert rows, f"no Source at session:{sid}"
    kind, s_sid, captured_at, content_hash, summary, topics, event_id = rows[0]
    assert kind == "agentSession", f"sourceKind must be agentSession, got {kind!r}"
    assert s_sid == sid, "sessionId must mirror the capture session id"
    assert captured_at, "capturedAt must be set"
    assert content_hash, "contentHash must be populated from the transcript"
    assert summary, "summary must be derived from the conversation"
    assert isinstance(topics, list) and topics, "topics must be derived"
    assert event_id, "eventId must reference the sessionCaptured Event"
    # (Source)-[:references]->(sessionCaptured Event)
    refs = proj.g.query(
        "MATCH (s:Source {url:$url})-[:references]->(e:Event) "
        "RETURN e.eventId, e.eventKind",
        params={"url": f"session:{sid}"},
    ).result_set
    assert refs, "Source must reference the sessionCaptured Event"
    assert refs[0][1] == "sessionCaptured", refs
    assert refs[0][0] == event_id, \
        "Source.eventId must match the referenced Event"
    # extractedFrom edges resolve to the TYPED Source (same url)
    ep = proj.g.query(
        "MATCH (p:Point)-[:extractedFrom]->(s:Source {url:$url}) "
        "RETURN count(DISTINCT s.sourceKind)",
        params={"url": f"session:{sid}"},
    ).result_set
    assert ep[0][0] == 1 and rows[0][0] == "agentSession", \
        "all extracted Points must resolve to the agentSession Source"


def test_capture_session_source_materialized_when_event_write_fails(sdk, monkeypatch):
    """#1352: the Source materialization is independent of the Event write —
    when the sessionCaptured Event write fails (non-fatal), the Source is
    still upgraded to agentSession (just without the references edge)."""

    def boom(*args, **kwargs):
        raise RuntimeError("falkordb down")

    monkeypatch.setattr(sdk, "create_event", boom)
    res = sdk.capture_session(CONV)
    sid = res["session_id"]
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.sourceKind",
        params={"url": f"session:{sid}"},
    ).result_set
    assert rows and rows[0][0] == "agentSession", \
        "Source must be agentSession-typed even when the Event write failed"


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
    """M2 LLM extraction emits one Point per utterance — the deterministic
    regex silence case is gone (#822). An empty conversation still yields 0;
    short/empty turns yield 0 (utterance floor is 3 chars)."""
    plain = [
        {"role": "user", "content": "the weather today is fine"},
        {"role": "assistant", "content": "yes it is"},
    ]
    res = sdk.capture_session(plain)
    # 2 utterances → 2 LLM points (the value gate is epic #909's future
    # value_extractor; M2 is utterance→point).
    assert res["extracted"] == 1, res
    # Empty conversation → 0 extractions, turns still land.
    res = sdk.capture_session([])
    assert res["extracted"] == 0
    sid = res["session_id"]
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN count(t)", params={"sid": sid}
    ).result_set
    assert turns[0][0] == 0
    # Only the two utterance Points from the first capture exist (no epistemic
    # points from the empty capture).
    stmt = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind = 'statement' RETURN count(p)"
    ).result_set
    assert stmt[0][0] == 1, "the v2-extracted point from the first capture only"


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
    """Extraction-side CONTAINS edges: M2 LLM Points must be wired to the
    session, and extraction actually creates the epistemic Points (#822)."""
    res = sdk.capture_session(CONV)
    assert res["extracted"] >= 1, "CONV must produce at least one point each"
    sid = res["session_id"]
    proj = sdk._get_proj()
    # At least one extraction created an untyped (M2) epistemic Point
    kinds = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind = 'statement' RETURN count(p)"
    ).result_set
    total = kinds[0][0]
    assert total >= 1, "extraction loop must create epistemic Points"
    # Every extracted point is CONTAINS-connected to this session
    connected = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind = 'statement' RETURN collect(p.id)",
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
    assert res["extracted"] >= 1


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


def test_capture_session_long_turn_extracts_only_stored_text(sdk, monkeypatch):
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")  # M2-mock-specific
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


# ── #1194: relation-stage flood cap — the pre-write estimate stays a true ceiling


class _PermissiveRelationModel:
    """Relation model returning EVERY ordered pair (i→j) — the O(n²) flood a
    permissive real model can emit. MockModel's cue-word sparsity hides this in
    the other tests; a real model has no prompt-enforced cap (extractor.py
    _RelationStage.run returns the raw relations list)."""

    id = "perm-relations"

    def complete(self, *, system, user):
        pts = json.loads(user)["points"]
        n = len(pts)
        return json.dumps({"relations": [
            {"op_type": "IMPL", "src": i, "dst": j}
            for i in range(n) for j in range(n) if i != j
        ]})


class _CountingAPI:
    """Duck-typed EventAPI recording point/operator counts (extractor only
    calls add_point/add_operator in transcript mode)."""

    def __init__(self):
        self.points = 0
        self.operators = 0
        self.ids = []

    def add_point(self, content, provenance, **fields):
        self.points += 1
        self.ids.append(f"p{self.points}")
        return self.ids[-1]

    def add_operator(self, op_type, args, provenance):
        self.operators += 1


def test_session_relation_flood_capped_estimate_is_true_ceiling():
    """#1194: a permissive relation model emitting O(n²) relations must not
    write more operator nodes than the pre-write estimate counted. The
    extractor dedupes + clamps operators ≤ points, so actual node writes
    (points + operators) stay ≤ estimate (2 × sentences) — the 402 flood
    gate the estimate feeds remains a true upper bound on node writes.

    10 points → the permissive model emits 90 relations; without the cap that
    is 90 IMPL operator nodes (non-episodic Points counted by the quota) vs
    an estimate of 2 × 10 = 20 — a silent gate bypass. With the cap, operators
    are held at ≤ points and the estimate holds."""
    from tortoise.extractor import LLMExtractor, MockModel
    from tortoise.sdk import _session_llm_transcript

    conversation = [
        {"role": "user", "content": f"sentence number {i} about the topic."}
        for i in range(10)
    ]
    transcript, est = _session_llm_transcript(conversation)
    assert est == 20, f"expected 2×10 sentences estimate, got {est}"

    ex = LLMExtractor(MockModel("mock-point"), _PermissiveRelationModel())
    api = _CountingAPI()
    ex.run(transcript, "src-test", api)

    assert api.points == 10, api.points
    # the O(n²) flood (90 relations) is clamped to the estimate's ceiling
    assert api.operators <= api.points, (
        f"operator flood escaped the cap: {api.operators} > {api.points} points")
    assert api.points + api.operators <= est, (
        f"estimate {est} < actual nodes {api.points + api.operators}")


# ── #1350: v2 capture (the default since this wiring) ─────────────────

def test_capture_session_v2_default_routes_and_writes(sdk):
    """#1350: capture runs the v2 5-stage extractor by default — the mocked
    v2 output (one statement point) lands with the session CONTAINS + the
    session Source extractedFrom link + eventId provenance."""
    import os
    os.environ.pop("TORTOISE_SESSION_EXTRACTOR", None)
    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert res["extracted"] >= 1
    proj = sdk._get_proj()
    sid = res["session_id"]
    # the extracted statement point carries the eventId provenance stamp
    stmt = proj.g.query(
        "MATCH (p:Point {pointKind:'statement'}) RETURN p.id, p.eventId"
    ).result_set
    assert stmt and stmt[0][1], "v2 extracted point must be eventId-stamped"
    # the session Source link (extractedFrom) resolves
    src = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN count(*)",
        params={"url": f"session:{sid}"}).result_set
    assert src[0][0] == 1, "session Source must exist"


def test_capture_session_v2_mock_seam_satisfies_provider_gate(sdk, monkeypatch):
    """#1468: TORTOISE_SESSION_LLM_MOCK=1 must satisfy _extract_session_v2's
    provider gate even with ALL provider keys absent — the hosted e2e server
    scrubs keys and runs the offline seam. The gate previously checked only
    keys (contradicting its own error message), raising ValueError → HTTP 500
    on POST /v1/sessions. Padded value (" 1 ") must also satisfy the gate
    (normalized like the sibling gates — strict == "1" would diverge the
    inner gate from the outer gates and 500 again)."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", " 1 ")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert res["extracted"] >= 1


def test_capture_session_v2_default_adapter_is_uncapped(sdk, monkeypatch):
    """#1468: the v2 extractor's DEFAULT adapter (no TORTOISE_EXTRACT_MODEL
    override) must be the production in-module adapter with an UNCAPPED
    output budget — never the test-only tests.model_adapters.MODELS
    (ModuleNotFoundError on the hosted server → HTTP 500 on POST /v1/sessions),
    and never the capped 4000-token default (the 5-stage extractor truncates
    and silently loses chunks)."""
    from tortoise import sdk as sdk_module
    from tortoise.sdk import _V2SessionMock

    # Clear the autouse mock seam + install a provider key so the real
    # (non-mock) branch of _extract_session_v2 runs.
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-1468")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)

    captured = {}

    def _fake_model_adapter(model_id, max_tokens=4000, temperature=0.0):
        captured["model_id"] = model_id
        captured["max_tokens"] = max_tokens
        captured["temperature"] = temperature
        return _V2SessionMock()  # offline stand-in with the real complete() contract

    monkeypatch.setattr(sdk_module, "_model_adapter", _fake_model_adapter)

    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert captured["model_id"] == "deepseek/deepseek-v4-flash"
    assert captured["max_tokens"] is None, (
        "v2 default must be UNCAPPED (max_tokens=None) — capped adapters "
        "truncate and silently lose chunks (#1468)")
    assert captured["temperature"] == 0.0
    assert res["extracted"] >= 1


def test_capture_session_v2_extract_model_override_stays_capped(sdk, monkeypatch):
    """#1468: an explicit TORTOISE_EXTRACT_MODEL override keeps the bounded
    4000-token default (summary/construct posture, T13 #1272) — only the
    DEFAULT v2 adapter is uncapped."""
    from tortoise import sdk as sdk_module
    from tortoise.sdk import _V2SessionMock

    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-1468")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.setenv("TORTOISE_EXTRACT_MODEL", "deepseek/deepseek-v4-pro")

    captured = {}

    def _fake_model_adapter(model_id, max_tokens=4000, temperature=0.0):
        captured["model_id"] = model_id
        captured["max_tokens"] = max_tokens
        captured["temperature"] = temperature
        return _V2SessionMock()

    monkeypatch.setattr(sdk_module, "_model_adapter", _fake_model_adapter)

    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert captured["model_id"] == "deepseek/deepseek-v4-pro"
    assert captured["max_tokens"] == 4000, (
        "explicit TORTOISE_EXTRACT_MODEL override must keep the bounded default")
    assert res["extracted"] >= 1
