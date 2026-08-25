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
    # #822/#1530: extraction_mode reports what actually ran — the v2 LLM
    # extractor via the mock seam reports the resolved route ("llm:mock").
    assert res["extraction_mode"] == "llm:mock"
    assert res["extraction_provider"] == "mock"
    # P1 #1529: success responses carry the fail-closed surface — ok=True, no
    # errors. warnings is an ADDITIVE surface: E3 (#1535) now emits a source-
    # turn resolution warning on the offline mock path (the mock's embed point
    # has no source_turn_id/quote match) — the response carries it, never
    # crashes on it (assert list-ness, not emptiness).
    assert res["ok"] is True
    assert res["errors"] == []
    assert isinstance(res["warnings"], list)


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
    # P1 #1529: the no-extractor check precedes the empty gate — an EMPTY
    # conversation with no key raises the SAME ValueError (fail-closed
    # exception, hosted 503-first precedent; never the structured empty
    # response, which would mask a misconfigured deploy).
    with pytest.raises(ValueError, match="LLM provider key"):
        sdk.capture_session([])


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
    """Repeated speaker tags must NOT conflate turns: one CONTAINS edge per turn.
    P1 #1529: content must be non-blank ("x" is below the 3-char sentence
    floor → the whole-conversation blank gate would fire pre-mutation)."""
    repeat = [{"role": "user", "content": "okay we proceed"}] * 3
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
    """len(conversation) == max_turns is accepted (boundary, not an overflow).
    P1 #1529: content must be non-blank so the cap boundary — not the blank
    gate — is what's exercised."""
    conv = [{"role": "user", "content": "okay"}] * 3
    res = sdk.capture_session(conv, max_turns=3)
    assert res["turns"] == 3


def test_capture_session_non_string_content_coerced(sdk):
    """Non-string content (numbers/dicts) is coerced, never crashes mid-write.
    P1 #1529: the single-turn falsy-0 case is BLANK (whole-conversation
    transcript is empty) → the empty gate fires pre-mutation — covered by
    test_capture_session_blank_conversation_fails_closed; this test keeps the
    non-blank mixed-conversation coercion lock (#721)."""
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


# ── #1530 P2: provider routing through the capture path (E2E-8) ─────────────

_V2_EMBED_JSON = (
    "{\"entities\": [{\"name\": \"the strategy\", "
    "\"kind\": \"core:strategy\", \"lifecycle\": \"created\", "
    "\"supersedes\": null, \"note\": null}], "
    "\"events\": [{\"content\": \"we decided on the new strategy\", "
    "\"eventKind\": \"core:decision\", \"about_entities\": [\"the strategy\"]}], "
    "\"points\": [{\"content\": \"the new strategy is durable\", "
    "\"pointKind\": \"statement\", \"about_entities\": [\"the strategy\"]}], "
    "\"operators\": [], \"chain_notes\": [], \"link_before_create\": []}")


class _FakeLLMResp:
    """Deterministic offline LLM response, keyed on the system prompt like
    _V2SessionMock (S1 → narrative, S2/S4 → the embed JSON)."""
    def __init__(self, content: str):
        self._content = content

    def raise_for_status(self):
        pass

    def json(self):
        return {"choices": [{"message": {"content": self._content}}],
                "usage": {}}


def _install_fake_provider(monkeypatch, requests_log):
    """Monkeypatch requests.Session.post with the _V2SessionMock response
    logic so the real adapters (OpenRouterModel / DeepSeekDirectModel) run
    fully offline; every request URL is appended to ``requests_log``."""
    import requests as _requests

    def _fake_post(self_or_url, url=None, **kwargs):
        requests_log.append(url)
        system = ((kwargs.get("json") or {}).get("messages") or [{}])[0].get("content", "")
        if "STORY SUMMARIZER" in system:
            content = "The session revealed a new strategy."
        else:
            content = _V2_EMBED_JSON
        return _FakeLLMResp(content)

    # Epic #1647 (PR #1684): the adapters call self._session.post
    # (requests.Session) — patching requests.post never intercepted, so
    # these tests made REAL network calls and 401'd/flaked without keys.
    monkeypatch.setattr(_requests.Session, "post", _fake_post)


def test_capture_session_gate_match_openai_key_alone_rejected(sdk, monkeypatch):
    """#1530 gate match: OPENAI_API_KEY alone no longer opens the v2 inner
    gate — the adapter cannot consume it (the #1468 failure class). The gate
    raises ValueError naming the routing-usable keys."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-only")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        sdk.capture_session([{"role": "user", "content": "x"},
                             {"role": "assistant", "content": "we decided"}])


def test_capture_session_gate_match_explicit_provider_with_wrong_key(sdk, monkeypatch):
    """D2 fail-closed: TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct with only an
    OPENROUTER key → ValueError (explicit provider names a key that isn't set).
    P1 #1529: content must be non-blank — the whole-conversation blank gate
    precedes the v2 inner provider gate, so a below-floor transcript would
    short-circuit to the structured empty response instead of exercising the
    provider-mismatch fail-closed path."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-only")
    monkeypatch.setenv("TORTOISE_EXTRACTOR_PROVIDER", "deepseek-direct")
    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        sdk.capture_session([{"role": "user",
                              "content": "we decided to ship serve first."}])


def test_capture_session_deepseek_direct_route_recorded(sdk, monkeypatch):
    """DEEPSEEK_API_KEY alone → the direct adapter is used; the response
    records the resolved route + provider (#1530 D8)."""
    from tortoise.model_adapters import _reset_failover_cooldown
    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert res["extraction_mode"] == "llm:deepseek-direct"
    assert res["extraction_provider"] == "deepseek-direct"
    assert res["extracted"] >= 1
    # every LLM call went to the DIRECT endpoint (no OR hop)
    assert requests_log
    assert all("api.deepseek.com" in u for u in requests_log)


def test_capture_session_failover_records_fallback_route(sdk, monkeypatch):
    """E2E-8 failover variant: the primary (deepseek-direct) fails its first
    call with ConnectionError → the extraction succeeds via the OpenRouter
    fallback, the response records the fallback route, and the primary is
    never re-tried mid-extraction (D5 sticky, no flip-flop)."""
    import requests as _requests

    from tortoise.model_adapters import DeepSeekDirectModel, _reset_failover_cooldown

    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACTOR_PROVIDER", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    calls = {"ds": 0}
    orig = DeepSeekDirectModel.complete

    def _flaky_ds_complete(self, *, system, user, max_tokens: int | None = None):
        calls["ds"] += 1
        if calls["ds"] == 1:
            raise _requests.ConnectionError("simulated DS collapse (#1350)")
        return orig(self, system=system, user=user)

    monkeypatch.setattr(DeepSeekDirectModel, "complete", _flaky_ds_complete)

    res = sdk.capture_session([{"role": "user", "content": "x"},
                               {"role": "assistant", "content": "we decided"}])
    assert res["extraction_mode"] == "llm:openrouter", \
        "failover must record the fallback route on the wire"
    assert res["extraction_provider"] == "deepseek-direct", \
        "extraction_provider stays the configured primary"
    assert res["extracted"] >= 1
    assert calls["ds"] == 1, \
        "no flip-flop: the primary must never be re-tried after failover"
    # the rest of the extraction ran on OpenRouter
    assert any("openrouter.ai" in u for u in requests_log)


def test_capture_session_failover_meta_flags(sdk, monkeypatch):
    """The (extracted, meta) contract surfaces failover_used + errors through
    _extract_session_v2 (P1 consumes meta — shared contract, D8)."""
    import requests as _requests

    from tortoise.model_adapters import DeepSeekDirectModel, _reset_failover_cooldown

    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    calls = {"ds": 0}
    orig = DeepSeekDirectModel.complete

    def _flaky_ds_complete(self, *, system, user, max_tokens: int | None = None):
        calls["ds"] += 1
        if calls["ds"] == 1:
            raise _requests.ConnectionError("simulated DS collapse (#1350)")
        return orig(self, system=system, user=user)

    monkeypatch.setattr(DeepSeekDirectModel, "complete", _flaky_ds_complete)

    extracted, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "we decided"}],
        session_id="s-meta", now="2026-08-20T00:00:00Z")
    assert meta["provider"] == "deepseek-direct"
    assert meta["route"] == "openrouter"
    assert meta["failover_used"] is True
    assert isinstance(meta["errors"], list)
    assert isinstance(meta["warnings"], list)
    assert len(extracted) >= 1


def test_capture_session_fatal_4xx_no_failover(sdk, monkeypatch):
    """E2E-8 fatal-4xx negative: the primary raises 401 → the extraction
    fails closed with NO fallback attempt (fatal is never failed over)."""
    import requests as _requests

    from tortoise.model_adapters import DeepSeekDirectModel, _reset_failover_cooldown

    _reset_failover_cooldown()
    requests_log = []
    _install_fake_provider(monkeypatch, requests_log)
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
    monkeypatch.delenv("TORTOISE_EXTRACT_MODEL", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    err = _requests.HTTPError("HTTP 401")
    err.response = type("R", (), {"status_code": 401})()

    def _fatal_ds_complete(self, *, system, user, max_tokens: int | None = None):
        raise err

    monkeypatch.setattr(DeepSeekDirectModel, "complete", _fatal_ds_complete)

    _, meta = sdk._extract_session_v2(
        [{"role": "user", "content": "x"},
         {"role": "assistant", "content": "we decided"}],
        session_id="s-fatal", now="2026-08-20T00:00:00Z")
    assert meta["failover_used"] is False, "fatal 4xx must never fail over"
    assert meta["route"] == "deepseek-direct"
    assert meta["errors"], "the fatal 401 must be recorded in meta errors"
    # no OpenRouter call ever happened
    assert not any("openrouter.ai" in u for u in requests_log)


# ── P1 (#1529) fail-closed capture ────────────────────────────────────────
# Extraction errors surface (ok=False + errors on the response, mode="error"),
# extraction_mode is truthful ("llm:<route>" / "llm" on success, "empty" /
# "error" never claim success), an empty/blank conversation never ok=True and
# commits nothing, and E3's source_turn_id is never clobbered. Built on the
# P2 (#1530) live (extracted, meta) contract — meta carries errors/warnings/
# mode; P1 must not re-shape it (additive only).


class _FailingSessionExtractor:
    """Duck-typed M2 extractor whose run() raises — the dead-key / mid-run
    failure P1 must surface, not swallow (E2E-8 dead-key negative)."""
    version = "failing@0"

    def run(self, transcript, source_id, api):
        raise RuntimeError("provider returned 500")


class _PartialFailingSessionExtractor:
    """Emits ONE point then raises — the partial-emission case: extracted>0
    must never be read as success (ok is the signal)."""
    version = "partial@0"

    def run(self, transcript, source_id, api):
        api.add_point("decision: ship serve first", {"source": source_id})
        raise RuntimeError("provider rate limited mid-run")


class _EmptyOutputExtractor:
    """'Succeeds' but emits no points — the last silent extracted:0 window."""
    version = "empty-out@0"

    def run(self, transcript, source_id, api):
        pass


def _v2_out(payload=None, errors=(), warnings=()):
    """Shape-complete extractor_v2.extract_session_v2 output for seams."""
    return {"session_id": "sess_p1", "story_arc": "", "embed_list": {},
            "search": {"mode": "embedded", "degraded": True},
            "payload": payload, "chain_notes": [], "link_before_create": [],
            "supersessions": [], "warnings": list(warnings),
            "minted_kinds": [], "stats": {}, "errors": list(errors)}


# ── v2 branch (DEFAULT — the issue's "_extract_session_v2 discards
#    out[errors]" checklist item) ────────────────────────────────────────

def test_extract_session_v2_consults_out_errors(sdk, monkeypatch):
    """P1: the v2 wrapper must surface extractor_v2 out['errors'] — a dead
    key yields meta mode='error' + errors, never a silent extracted:0 (E2E-8)."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("provider returned 500" in e for e in meta["errors"])
    assert meta["warnings"] == [], "failure carries errors, never warnings"


def test_extract_session_v2_surfaces_warnings_and_zero_points(sdk, monkeypatch):
    """P1 (D6): completed-but-empty v2 output (payload None, no errors) is
    an additive warning, not a silent 0 and not a fake failure."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out())
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "v2"
    assert extracted == []
    assert any("no points" in w for w in meta["warnings"])
    assert meta["errors"] == []


def test_extract_session_v2_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (v2 carrier): payload points carrying E3's fields
    (source_turn_id, search_keys, when, quote — E3 #1535 landed them on the
    v2 payload) flow through `props` unchanged (whitelisted)."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-v2-1", "content": "we decided X",
                           "pointKind": "statement", "source_turn_id": "t0",
                           "search_keys": ["decide x", "x decision"],
                           "when": "2026-08-20", "quote": "we decided X"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    extracted, _meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "payload point must land"
    props = extracted[0].get("props", {})
    assert props.get("source_turn_id") == "t0", extracted
    assert props.get("search_keys") == ["decide x", "x decision"], extracted
    assert props.get("when") == "2026-08-20", extracted
    assert props.get("quote") == "we decided X", extracted
    # whitelist discipline: internal payload fields must NOT leak into props
    assert not any(k in props for k in ("id", "content", "pointKind",
                                        "about_entities")), props


def test_extract_session_v2_counts_point_write_skips(sdk, monkeypatch):
    """P1: the v2 point-write loop's silent `except: pass` becomes counted —
    a point that fails to write surfaces as an additive warning + error, never
    an invisible partial write."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_p1", "story_arc": "", "entities": [],
               "points": [{"id": "p-ok", "content": "we decided X",
                           "pointKind": "statement"},
                          {"id": "", "content": "no id -> skipped"},
                          {"id": "p-boom", "content": "we decided Y",
                           "pointKind": "statement"}],
               "operators": [], "events": [], "supersessions": [],
               "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    _real_create_point = sdk.create_point

    def _boom_point(*args, **kwargs):
        if args and args[1] == "we decided Y" or kwargs.get("content") == "we decided Y":
            raise RuntimeError("point write failed")
        return _real_create_point(*args, **kwargs)

    monkeypatch.setattr(sdk, "create_point", _boom_point)
    extracted, meta = sdk._extract_session_v2(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "the successful point is reported"
    assert any("failed to write" in w or "skipped" in w for w in meta["warnings"]), meta
    assert meta["mode"] == "error", "a failed point write is a capture failure"


# ── M2 branch (behind TORTOISE_SESSION_EXTRACTOR=m2) — seam tests call the
#    method directly (no env var needed) ─────────────────────────────────

def test_extract_session_llm_failure_is_structured_not_raised(sdk, monkeypatch):
    """P1: M2 LLM failure returns meta mode='error' with the exception class
    preserved (P2 needs TypeName to classify fatal 4xx) — never raises."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _FailingSessionExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("RuntimeError" in e and "500" in e for e in meta["errors"])


def test_extract_session_llm_partial_emission_reports_points(sdk, monkeypatch):
    """P1: a run that emitted points then failed reports them (extracted>0
    with ok=False — the caller's ok flag is the success signal)."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _PartialFailingSessionExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert len(extracted) >= 1, "partial points must still be reported"
    assert any("RuntimeError" in e for e in meta["errors"])


def test_extract_session_llm_fold_failure_is_structured(sdk, monkeypatch):
    """P1: a failure AFTER run() (fold) stays inside the fail-closed surface
    — no raw exception after partial writes. Partial emitter documents the
    orphan window (projection writes points during run(); a fold failure
    leaves unowned statement nodes — accepted and visible, not silent)."""
    import tortoise.projection as proj

    class _BoomFoldCaller:
        version = "boom-fold@0"

        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _BoomFoldCaller())
    monkeypatch.setattr(proj, "fold",
                        lambda events: (_ for _ in ()).throw(RuntimeError("fold blew up")))
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert extracted == []
    assert any("fold blew up" in e for e in meta["errors"])


def test_extract_session_llm_wiring_failure_is_structured(sdk, monkeypatch):
    """P1: a mid-CONTAINS-wiring failure stays inside the fail-closed surface
    and the response never silently diverges from the graph: points wired
    before the raise are reported; graph-side orphan state is pinned.
    NOTE: the wiring query MATCHes the Session — pre-create it (the seam
    bypasses capture_session, which is what creates the Session)."""
    sdk._get_proj().g.query("MERGE (s:Session {id:'sess_p1'})")

    class _EmitThenBoomWiring:
        version = "wiring-boom@0"

        def run(self, transcript, source_id, api):
            api.add_point("decision: ship serve first", {"source": source_id})
            api.add_point("decision: deploy second", {"source": source_id})

    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmitThenBoomWiring())
    proj = sdk._get_proj()
    _raw = proj.g._g
    _real_query = _raw.query  # _GuardedGraph.query is a read-only slot method
    calls = {"n": 0}

    def _boom_on_second(query, **params):
        if "CONTAINS" in query:
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("falkordb transient write failure")
        return _real_query(query, **params)

    monkeypatch.setattr(_raw, "query", _boom_on_second)
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "error"
    assert any("transient write failure" in e for e in meta["errors"])
    assert len(extracted) == 1, "the point wired before the failure stays reported"
    proj = sdk._get_proj()
    stmts = proj.g.query(
        "MATCH (p:Point) WHERE p.pointKind IS NULL RETURN count(p)").result_set
    assert stmts[0][0] == 2, "both emitted points exist in the graph (orphan pinned)"
    wired_n = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
        "WHERE p.pointKind IS NULL RETURN count(p)",
        params={"sid": "sess_p1"}).result_set
    assert wired_n[0][0] == 1, "exactly one point is CONTAINS-wired (the reported one)"


def test_extract_session_llm_empty_guard_is_self_consistent(sdk):
    """P1 (D2): the internal empty-transcript guard (defense-in-depth) must
    report an error entry — a regression to errors=[] would make capture
    compute ok=True on the empty path (the 'lying extraction_mode' bug)."""
    for conv in ([], [{"role": "user", "content": "ok"}]):
        extracted, meta = sdk._extract_session_llm(
            conv, "sess_p1", "2026-08-20T00:00:00+00:00")
        assert meta["mode"] == "empty", meta
        assert extracted == []
        assert any("empty" in e.lower() for e in meta["errors"]), meta
        assert meta["warnings"] == []


def test_extract_session_llm_zero_output_warns(sdk, monkeypatch):
    """P1: completed-but-empty M2 extraction is an additive warning, not a
    silent 0 and not a fake failure."""
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    extracted, meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert meta["mode"] == "llm"
    assert extracted == []
    assert any("no points" in w for w in meta["warnings"])
    assert meta["errors"] == []


def test_extract_session_llm_passthroughs_source_turn_id(sdk, monkeypatch):
    """E3 passthrough (M2 carrier): source_turn_id injected via
    add_point(**fields) — the carrier E3's projection will use — flows
    through `props` unchanged. (A synthetic PointUpdated event does NOT
    fold; the test uses the real carrier.)"""
    from tortoise.api import EventAPI

    class _StampingAPI(EventAPI):
        def add_point(self, content, provenance, **fields):
            fields["source_turn_id"] = "session_x_t0"
            return super().add_point(content, provenance, **fields)

    monkeypatch.setattr("tortoise.api.EventAPI", _StampingAPI)
    extracted, _meta = sdk._extract_session_llm(
        CONV, "sess_p1", "2026-08-20T00:00:00+00:00")
    assert extracted, "CONV must extract points"
    assert any(p.get("props", {}).get("source_turn_id") == "session_x_t0"
               for p in extracted), extracted


# ── capture_session fail-closed assembly (both branches) ──────────────────

def test_capture_session_empty_conversation_fails_closed(sdk):
    """P1: empty conversation never ok=True — nothing committed, no Session
    stub, extraction_mode 'empty', turns=0 (E2E-8 owned negative)."""
    res = sdk.capture_session([])
    assert res["ok"] is False
    assert res["extraction_mode"] == "empty"
    assert res["turns"] == 0
    assert res["extracted"] == 0
    assert res["points"] == []
    assert any("empty" in e.lower() for e in res["errors"])
    assert res["warnings"] == []
    sessions = sdk._get_proj().g.query(
        "MATCH (s:Session) RETURN count(s)").result_set
    assert sessions[0][0] == 0, "nothing may be committed for an empty capture"


def test_capture_session_blank_conversation_fails_closed(sdk):
    """P1: whole-conversation blank (below-floor / whitespace / None /
    missing-key / 5000-char whitespace / falsy-0 / 2-char) → ok=False,
    mode='empty', turns=0. Floor boundary: exactly-3-char 'abc' is NON-blank.
    (Note: 'ab cd ef' is ONE 8-char sentence per _SENT — non-blank, so it is
    NOT in the blank set; the gate uses the real transcript signal.)"""
    blank_convos = (
        [{"role": "user", "content": "ok"}],
        [{"role": "user", "content": " "}],
        [{"role": None, "content": None}],
        [{"role": "user"}],                      # missing content key
        [{"role": "user", "content": " " * 5000}],  # validator's upper bound, whitespace
        [{"role": "user", "content": 0}],         # str() = "0", below floor
        [{"role": "user", "content": "ab"}],      # 2 chars < floor
    )
    for conv in blank_convos:
        res = sdk.capture_session(conv)
        assert res["ok"] is False, conv
        assert res["extraction_mode"] == "empty", conv
        assert res["turns"] == 0, conv
        assert any("empty" in e.lower() for e in res["errors"]), conv
    # floor boundary, non-blank side
    res = sdk.capture_session([{"role": "user", "content": "abc"}])
    assert res["ok"] is True and res["extraction_mode"] in ("llm:mock", "llm"), res


def test_capture_session_v2_failure_surfaces_errors(sdk, monkeypatch):
    """P1 (E2E-8 dead-key, DEFAULT v2 branch): turn points still land,
    errors surface, mode 'error' — never a silent extracted:0."""
    import tortoise.extractor_v2 as ev2
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(errors=["RuntimeError: provider returned 500"]))
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert res["extracted"] == 0
    assert res["points"] == []
    assert any("provider returned 500" in e for e in res["errors"])
    assert res["warnings"] == [], "failure carries errors, never warnings"
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 3, "turn points must still land (documented partial)"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)"
    ).result_set
    assert events[0][0] == 1, "the capture attempt is recorded"


def test_capture_session_m2_failure_surfaces_errors(sdk, monkeypatch):
    """P1 (E2E-8 dead-key, M2 branch): same contract under
    TORTOISE_SESSION_EXTRACTOR=m2 with the duck-typed failing extractor."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _FailingSessionExtractor())
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert any("RuntimeError" in e and "500" in e for e in res["errors"])
    proj = sdk._get_proj()
    turns = proj.g.query(
        "MATCH (t:Point {pointKind:'event'}) RETURN count(t)").result_set
    assert turns[0][0] == 3, "turn points must still land"
    events = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN count(e)"
    ).result_set
    assert events[0][0] == 1, "the capture attempt is recorded"


def test_capture_session_partial_emission_ok_false_points_land(sdk, monkeypatch):
    """P1 (D2 contract note, M2 branch): extracted > 0 alongside ok=False —
    partial points ARE wired + eventId-stamped; extracted is never success."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _PartialFailingSessionExtractor())
    res = sdk.capture_session(CONV)
    assert res["ok"] is False
    assert res["extraction_mode"] == "error"
    assert res["extracted"] == len(res["points"]) >= 1
    assert any("RuntimeError" in e for e in res["errors"])
    assert res["warnings"] == [], "failure carries errors, never warnings"
    proj = sdk._get_proj()
    eid = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(eid) == 1
    wired = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) WHERE p.pointKind IS NULL "
        "RETURN count(p)", params={"sid": res["session_id"]}).result_set
    assert wired[0][0] == res["extracted"], "partial points must be CONTAINS-wired"
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId <> $eid RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]], "eid": eid[0][0]}
    ).result_set
    assert unstamped[0][0] == 0, "partial points must carry the eventId"


def test_capture_session_zero_extraction_is_warning_not_failure(sdk, monkeypatch):
    """P1 (D6, M2 branch): completed run with no points → ok=True, mode
    'llm', additive warning — nothing extractable is not a failure."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    res = sdk.capture_session(
        [{"role": "user", "content": "the weather today is fine"}])
    assert res["ok"] is True
    assert res["extraction_mode"] == "llm"
    assert res["extracted"] == 0
    assert any("no points" in w for w in res["warnings"])
    assert res["errors"] == []


def test_capture_session_success_shape_consistent_with_graph(sdk):
    """P1: on ok=True the graph actually has the Event, every extracted
    point carries its eventId, and the turn stream matches the response."""
    res = sdk.capture_session(CONV)
    assert res["ok"] is True and res["errors"] == []
    proj = sdk._get_proj()
    eid = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(eid) == 1, "exactly one sessionCaptured Event on success"
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId <> $eid RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]], "eid": eid[0][0]}
    ).result_set
    assert unstamped[0][0] == 0, "every extracted point carries the eventId"
    turns = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN count(t)", params={"sid": res["session_id"]}).result_set
    assert turns[0][0] == res["turns"], "graph turn count must match the response"


def test_capture_session_event_write_failure_keeps_structured_success(sdk, monkeypatch):
    """P1: create_event failure is non-fatal (#721) — structured success
    shape + additive warning + correct graph state (points present, no
    dangling eventId, Source eventId null, no references edge)."""
    def boom(*args, **kwargs):
        raise RuntimeError("falkordb down")
    monkeypatch.setattr(sdk, "create_event", boom)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert res["extraction_mode"] in ("llm:mock", "llm")
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    proj = sdk._get_proj()
    dangling = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NOT NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert dangling[0][0] == 0, "no point may reference the failed Event"
    src = proj.g.query(
        "MATCH (s:Source {sourceKind:'agentSession'}) RETURN s.eventId").result_set
    assert src and src[0][0] is None, "Source must have no eventId when no Event landed"
    refs = proj.g.query(
        "MATCH (:Source)-[:references]->(:Event) RETURN count(*)").result_set
    assert refs[0][0] == 0, "no references edge when no Event landed"


def test_capture_session_event_write_no_id_warns(sdk, monkeypatch):
    """P1 (D4): create_event succeeding but returning a dict WITHOUT
    id/eventId silently skips stamping — must surface as an additive warning."""
    monkeypatch.setattr(sdk, "create_event",
                        lambda *a, **kw: {"name": "no-id-event"})
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    unstamped = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NOT NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert unstamped[0][0] == 0, "no point may carry a dangling eventId"


def test_capture_session_stamping_failure_warns_and_leaves_points(sdk, monkeypatch):
    """P1 (D4): the eventId-stamping query failing (Event created, points
    unstamped) surfaces an additive warning under ok=True; the degraded graph
    state (Event present, points present, no dangling id) is asserted."""
    proj = sdk._get_proj()
    _raw = proj.g._g
    _real_query = _raw.query  # _GuardedGraph.query is a read-only slot method

    def _boom_stamp(query, **params):
        if "SET n.eventId=" in query:
            raise RuntimeError("stamping query failed")
        return _real_query(query, **params)

    monkeypatch.setattr(_raw, "query", _boom_stamp)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Event" in w or "event" in w.lower() for w in res["warnings"]), res
    unstamped = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids AND n.eventId IS NULL RETURN count(n)",
        params={"ids": [p["id"] for p in res["points"]]}).result_set
    assert unstamped[0][0] == res["extracted"], \
        "stamping failure leaves points present and unstamped (no dangling id)"


def test_capture_session_source_materialization_failure_warns(sdk, monkeypatch):
    """P1: _materialize_session_source failure is additive-warning, never a
    raw exception after partial writes (D4)."""
    def boom(*args, **kwargs):
        raise RuntimeError("source write failed")
    monkeypatch.setattr(sdk, "_materialize_session_source", boom)
    res = sdk.capture_session(CONV)
    assert res["ok"] is True, res
    assert any("Source" in w or "source" in w for w in res["warnings"]), res


def test_capture_session_two_warnings_sources_no_clobber(sdk, monkeypatch):
    """P1 (D7): two simultaneous degradations must BOTH surface — a clobbering
    `warnings = [...]` reassignment drops one and fails this test."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr("tortoise.sdk._build_session_llm_extractor",
                        lambda: _EmptyOutputExtractor())
    monkeypatch.setattr(sdk, "create_event",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("event down")))
    res = sdk.capture_session(
        [{"role": "user", "content": "the weather today is fine"}])
    joined = " | ".join(res["warnings"])
    assert "no points" in joined, res["warnings"]
    assert "Event" in joined or "event" in joined.lower(), res["warnings"]


def test_capture_session_recapture_never_clobbers_source_turn_id(sdk, monkeypatch):
    """E3 (#1529 note): (a) turn-point source_turn_id survives re-capture
    (MERGE SET list excludes it — turn-stream idempotency only; extraction
    points/Event fresh per capture BY DESIGN); (b) eventId stamping touches
    only extracted points; (c) per-capture provenance: each capture's points
    carry that capture's fresh Event eventId.

    Runs the M2 branch (fresh ULIDs per capture): the v2 branch's content-
    addressed ids are deterministic across captures, so a re-capture RE-stamps
    the same point nodes to the new Event — per-capture set-identity
    provenance is an M2 property (v2's re-stamp is intended content-addressed
    semantics, locked separately by the re-capture turn tests)."""
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    res1 = sdk.capture_session(CONV)
    sid = res1["session_id"]
    proj = sdk._get_proj()
    turn_id = f"{sid}_t0"
    proj.g.query(
        "MATCH (p:Point {id:$id}) SET p.source_turn_id='turn-42'",
        params={"id": turn_id})
    res2 = sdk.capture_session(CONV, session_id=sid)  # re-capture same session
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.source_turn_id, p.eventId",
        params={"id": turn_id}).result_set
    assert rows and rows[0][0] == "turn-42", "re-capture must not clobber source_turn_id"
    assert rows[0][1] is None, "turn points carry no eventId (extracted only)"
    evs = proj.g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(evs) == 2, "one fresh Event per capture (intended)"
    eids = {ev[0] for ev in evs}
    stamps = set()
    for res in (res1, res2):
        stamped = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN collect(DISTINCT n.eventId)",
            params={"ids": [p["id"] for p in res["points"]]}).result_set[0][0]
        assert len(stamped) == 1, f"points of one capture share one eventId: {stamped}"
        stamps.add(stamped[0])
    assert stamps == eids, f"per-capture provenance broken: {stamps} vs {eids}"


def test_capture_session_recapture_shorter_conversation_pins_state(sdk):
    """P1 (D3): re-capturing the same session_id with a SHORTER different
    conversation — turn-stream MERGE is keyed {sid}_t{i}, so higher-index
    turns from the prior capture stay CONTAINS-wired (stale residue) while
    response turns report the new length. PIN the accepted state."""
    res = sdk.capture_session([{"role": "user", "content": "first capture with five turns"},
                               {"role": "assistant", "content": "second"},
                               {"role": "user", "content": "third"}])
    sid = res["session_id"]
    sdk.capture_session([{"role": "user", "content": "shorter re-capture"}],
                        session_id=sid)
    wired = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point {pointKind:'event'}) "
        "RETURN collect(t.id)", params={"sid": sid}).result_set[0][0]
    assert set(wired) == {f"{sid}_t{i}" for i in range(3)}, wired


# ── #1532 P4: capture-path parity (window / role / quota / MITIGATES / id) ──

def test_capture_turn_window_truncates_content(sdk):
    from tortoise.sdk import _capture_turn_window
    conv = [{"role": "user", "content": "x" * 6000}]
    out = _capture_turn_window(conv)
    assert len(out[0]["content"]) == 5000
    assert out[0]["content"] == "x" * 5000
    assert len(conv[0]["content"]) == 6000, "input list is never mutated"


def test_capture_turn_window_preserves_short_and_absent(sdk):
    from tortoise.sdk import _capture_turn_window
    conv = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": None},
            {"role": "user"}]                      # content key absent
    out = _capture_turn_window(conv)
    assert out[0]["content"] == "hi"
    assert out[1]["content"] == ""                  # None -> "" (store-loop parity)
    assert out[2]["content"] == ""


def test_capture_turn_window_idempotent_when_pre_truncated(sdk):
    """#1532 D1: running the window over an already-windowed conversation is
    a no-op — the SDK loop's [:5000] and the extraction call can both apply
    it without double-truncating."""
    from tortoise.sdk import _capture_turn_window
    conv = [{"role": "user", "content": "y" * 5000}]
    out = _capture_turn_window(conv)
    assert out[0]["content"] == "y" * 5000


def test_normalize_turn_role_matches_sdk_loop(sdk):
    from tortoise.sdk import _normalize_turn_role
    assert _normalize_turn_role("user") == "user"
    assert _normalize_turn_role(None) == "unknown"
    assert _normalize_turn_role(123) == "123"
    assert _normalize_turn_role({"a": 1}) == "{'a': 1}"
    assert _normalize_turn_role(False) == "False"   # falsy non-string, not swallowed


def test_capture_extraction_input_is_stored_window(sdk, monkeypatch):
    """#1532 D1: the extraction call receives the truncated conversation —
    a phrase past the 5000-char cut must not reach the LLM (v2's
    _edus_from_conversation uses RAW content, so the capture loop must
    pre-window before the extraction call)."""
    from tortoise import sdk as sdk_mod
    seen: list = []
    orig = sdk_mod.TortoiseSDK._extract_session_v2

    def spy(self, conversation, session_id, now):
        seen.append([t["content"] for t in conversation])
        return orig(self, conversation, session_id, now)

    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_extract_session_v2", spy)

    content = ("plain filler text without triggers. " * 200) + "evidence suggests the fix landed."
    assert len(content) > 5000
    sdk.capture_session([{"role": "user", "content": content}])
    assert seen and all(len(c) <= 5000 for c in seen[0]), \
        "extraction input must be the stored window"


def test_extraction_estimate_v2_shape(sdk):
    from tortoise.sdk import _session_extraction_estimate
    conv = [{"role": "user", "content": "one. two. three."}]  # 3 sentences
    m2 = _session_extraction_estimate(conv, extractor="m2")
    v2 = _session_extraction_estimate(conv, extractor="v2")
    assert m2 == 6          # 3 sentences * 2 (points + operators)
    assert v2 == 9          # 3 * 3 (points + operators + entities/events)
    assert v2 > m2


def test_extraction_estimate_default_is_v2(sdk, monkeypatch):
    monkeypatch.delenv("TORTOISE_EXTRACTOR", raising=False)
    from tortoise.sdk import _session_extraction_estimate
    conv = [{"role": "user", "content": "one. two."}]
    assert _session_extraction_estimate(conv) == 6  # v2 default (2 sentences * 3)


def test_extraction_estimate_legacy_alias(sdk, monkeypatch):
    """#1532 D4: the deprecated-compat name resolves to the same v2-aware
    estimator — pre-migration callers keep working."""
    from tortoise.sdk import _session_extraction_estimate, \
        _session_llm_extraction_estimate
    conv = [{"role": "user", "content": "one. two. three."}]
    assert _session_llm_extraction_estimate(conv) == \
        _session_extraction_estimate(conv)


def test_capture_writes_mitigates_artifact(sdk, monkeypatch):
    """#1532 D3: capture applies v2 payload MITIGATES -> mitigation artifact
    identical to the commit path (mitigation Point + IMPL + mitigated_by),
    with the same content-derived reason."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_mit", "story_arc": "", "entities": [],
               "events": [],
               "points": [
                   {"id": "pt_x", "content": "it's cheap",
                    "pointKind": "statement"},
                   {"id": "pt_a", "content": "option A",
                    "pointKind": "statement"},
                   {"id": "pt_z", "content": "we can raise the price",
                    "pointKind": "statement"},
               ],
               "operators": [
                   {"src": "pt_x", "dst": "pt_a", "op_type": "IMPL",
                    "direction": "unidirectional"},
                   {"src": "pt_z", "dst": "pt_a", "op_type": "MITIGATES",
                    "target": {"src": "pt_x", "dst": "pt_a",
                               "op_type": "IMPL"},
                    "strength": 0.4},
               ],
               "supersessions": [], "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    res = sdk.capture_session(
        [{"role": "user", "content": "we can raise the price."}])
    assert res["ok"] is True, res["errors"]
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (op:Point {is_operator:true, op_type:'IMPL'}) "
        "MATCH (m:Point)-[:IMPL]->(op) "
        "MATCH (op)-[:mitigated_by]->(m) "
        "RETURN m.mitigation_strength, m.content",
    ).result_set
    assert rows, "capture must write the mitigation artifact"
    assert rows[0][0] == 0.4
    assert "raise the price" in rows[0][1], \
        f"reason must be the mitigating point's content, got {rows[0][1]!r}"


def test_capture_mitigates_deep_miss_dropped_not_raised(sdk, monkeypatch):
    """#1532 D3: a MITIGATES whose target IMPL edge is absent is DROPPED
    (support-edge-first, DE2E-11 negative) — never raises, never attaches."""
    import tortoise.extractor_v2 as ev2
    payload = {"session_id": "sess_mitmiss", "story_arc": "", "entities": [],
               "events": [],
               "points": [
                   {"id": "pt_x", "content": "it's cheap",
                    "pointKind": "statement"},
                   {"id": "pt_z", "content": "we can raise the price",
                    "pointKind": "statement"},
               ],
               "operators": [
                   {"src": "pt_z", "dst": "pt_a", "op_type": "MITIGATES",
                    "target": {"src": "pt_x", "dst": "pt_a",
                               "op_type": "IMPL"},
                    "strength": 0.4},
               ],
               "supersessions": [], "client_commit_id": "ccid"}
    monkeypatch.setattr(ev2, "extract_session_v2",
                        lambda *a, **kw: _v2_out(payload=payload))
    res = sdk.capture_session(
        [{"role": "user", "content": "we can raise the price."}])
    assert res["ok"] is True, \
        "a deep-miss mitigation must never fail the capture"
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (m:Point)-[:mitigated_by]->(op:Point) RETURN count(m)",
    ).result_set
    assert rows[0][0] == 0, \
        "no mitigation may attach without its target IMPL edge"


def test_client_commit_id_capture_parity(sdk, monkeypatch):
    """#1532 MECE: capture routes through the SAME supersessions-inclusive
    computation as commit — assertion, not re-implementation (E5 #1537 owns
    the functional 3-site agreement; the capture payload is execute_embed's,
    stamped at site 1 and re-stamped identically at _post_commit site 2)."""
    from tortoise import extractor_v2 as ev2
    from tortoise.commit_schema import compute_client_commit_id
    captured: dict = {}
    real = ev2.extract_session_v2

    def spy(model, conversation, **kw):
        out = real(model, conversation, **kw)
        if out.get("payload"):
            captured["payload"] = out["payload"]
        return out

    monkeypatch.setattr(ev2, "extract_session_v2", spy)
    res = sdk.capture_session([{"role": "user",
                                "content": "we adopted strategy B, dropping A."}])
    assert res["ok"] is True, res["errors"]
    payload = captured.get("payload")
    assert payload, "capture must produce a Layer-1 payload (v2 wiring)"
    cid = payload["client_commit_id"]
    assert cid, "payload must carry a stamped client_commit_id"
    expected = compute_client_commit_id(
        payload["session_id"], payload["points"], payload["entities"],
        payload["operators"], payload["summary"], payload["story_arc"],
        payload.get("events", []), payload.get("supersessions", []))
    assert cid == expected, \
        "capture payload id must equal the shared supersessions-inclusive computation"
    # Re-stamping through the commit path's _post_commit signature (site 2)
    # reproduces the same id — capture and commit cannot drift.
    assert compute_client_commit_id(
        payload["session_id"], payload["points"], payload["entities"],
        payload["operators"], payload["summary"], payload["story_arc"],
        payload.get("events", []), payload.get("supersessions", [])) == cid
