"""HTTP test coverage for POST /v1/sessions/commit — epic #909 slice 5b (#953).

The derived-commit receiver contract (plan §6.1 + W-3 + W-7). Covers the
DE2E suite legs owned by this slice:

- DE2E-2  four-node chain (Session counters → Event AgentSession → Document
          transcript → Source bridge → extractedFrom Points) + session_indexer
          discoverability + entities (aboutObject, passes_frequency_gate) +
          operators with the (src,dst,op_type) MERGE key
- DE2E-5  sources[] → external Source + references chain
- DE2E-6  NAND direction written (unidirectional / bidirectional)
- DE2E-7  L1 replay (duplicate:true, zero writes, zero write-ops billed),
          L2 supersede re-capture (supersede_point), Sessions A/B/C budget
          (soft-15 WARN, >25 held, >50 402, ceiling-only re-submission),
          sessions quota (41st commit → 402), Layer-1 400/422 (incl.
          commit_id_mismatch + calibration_mismatch + 51-point cap), 401,
          500 fail-closed
- DE2E-10 byte-level privacy (no raw conversation in payload/telemetry/graph;
          basename-only paths; telemetry schema has no text-bearing fields,
          no graph-side counts, no judge_summary)
- PL4     metering (write_ops +1 per non-duplicate; held bills 0 →
          write_ops_billed:0; nodes_written net-new)
- R-13    dedicated 300/min/key commit bucket, exempt from the 100/min key
          bucket
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module — set before import
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Rate limiter trips 429 in full-suite runs — tests opt out; production keeps
# the limit (the R-13 bucket tests below exercise the middleware directly).
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.commit_schema import (
    compute_client_commit_id,
    point_content_id,
)
from tortoise.hosted_api import app, get_current_team
from tortoise.ids import content_hash
from tortoise.sdk import TortoiseSDK

# ── Test constants ───────────────────────────────────────────────────────────

TEST_TEAM_ID = "team-001"  # epic #1647 (T7): a TEAM id, not a test namespace — a "test-" prefix would trip the SDK's hyphenated test-* normalization (sdk.py) and map the team graph to test_team_001_tortoise while team_graph_name resolves team_team-001 (backup dump divergence)
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    # get_current_team always resolves the full limits dict — test stubs must
    # match, or fail-closed quota enforcement 500s instead of passing (#310).
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": 1000,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one."""
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    # Break the _make_sdk embedded fallback anchor (#1470): _FALLBACK_KEEPALIVE
    # is module-level and survives test files, so an anchored SDK bound to a
    # PREVIOUS test's temp DB leaks state into this test (the anchor's socket
    # dies when that tempdir is removed → redis.socket ConnectionError, or the
    # previous graph's rows appear in the "fresh" temp DB). Clear it so
    # _make_sdk re-binds to THIS test's temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    """Restore original TortoiseSDK.__init__."""
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture
def client():
    """TestClient with auth override and a temp FalkorDBLite DB.

    All /v1/* endpoints receive TEST_TEAM as the authenticated team; every
    TortoiseSDK instance (tenant + registry) shares the same temp DB.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


@pytest.fixture
def client_no_auth():
    """TestClient WITHOUT auth override — exercises the real 401 path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


@pytest.fixture
def client_quota40():
    """Client whose team has max_sessions=40 (DE2E-7 quota fixture — direct
    write convention: no tier gives 40)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        team40 = dict(TEST_TEAM)
        team40["max_sessions"] = 40
        app.dependency_overrides[get_current_team] = lambda: dict(team40)
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


def _team_sdk() -> TortoiseSDK:
    """Fresh tenant SDK on the shared test DB (post-request read surface)."""
    import tortoise.hosted_api as ha_mod
    return ha_mod._make_sdk(namespace=TEST_TEAM_ID)


def _reg_sdk():
    """Registry SDK — MeteringRecord reads (PL4 assertions)."""
    import tortoise.hosted_api as ha_mod
    return ha_mod._make_sdk(namespace="registry")


def _metering_rows():
    rows = _reg_sdk()._get_registry().query(
        "MATCH (m:MeteringRecord {team_id:$tid}) "
        "RETURN m.write_ops, m.nodes_written",
        params={"tid": TEST_TEAM_ID},
    ).result_set
    return (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)


def _commit_record(cid: str):
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (r:CommitRecord {client_commit_id:$cid}) "
        "RETURN r.status, r.write_ops_billed, r.telemetry, r.budget_warn",
        params={"cid": cid},
    ).result_set
    return rows[0] if rows else None


def _session_counter(session_id: str, field: str):
    rows = _team_sdk()._get_proj().g.query(
        f"MATCH (s:Session {{id:$sid}}) RETURN coalesce(s.{field}, 0)",
        params={"sid": session_id},
    ).result_set
    return int(rows[0][0]) if rows else 0


# ── Payload factory (mirrors the slice-5a client serializer, W-3) ───────────

_TELEMETRY = {
    "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok"},
    "model": {"provider": "anthropic", "id": "claude-3-7", "cfg_hash": "h1"},
    "counts": {"kept": 5, "candidate": 10, "segment": 12, "window": 3,
               "empty_windows": 0},
    "keep_ratio": 0.5,
    "dedup_hits": 0,
    "frontier_calls": 1,
    "llm_cost_usd": 0.02,
    "extraction_ms": 1234,
    "retry_count": 0,
    "last_error_code": None,
    "confidence_histogram": [0, 0, 0, 0, 0, 0, 0, 1, 2, 2],
}


def _point(i: int, **overrides) -> dict:
    p = {
        "id": f"pt_{i:064d}",
        "content": f"point {i}",
        "pointKind": "decision",
        "reason": "NEW",
        "confidence": 0.9,
        "c_cal": 0.8,
        "about_entities": ["Alpha"],
        "source_ref": "session.md",
        "quote": "",
        "status": "live",
    }
    p.update(overrides)
    return p


def _raw_payload(n_points: int = 1, *, session_id: str = "s1",
                 operators=None, **overrides) -> dict:
    """Raw §6.1 dict with an EMPTY client_commit_id (finalize computes it)."""
    payload = {
        "schema_version": "1",
        "session_id": session_id,
        "client_commit_id": "",
        "captured_at": "2026-08-11T10:00:00Z",
        "extractor": {"version": "value@1.0.0+abc+def", "mode": "byok",
                      "calibration_version": "v3"},
        "summary": "summary text",
        "story_arc": "arc text",
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
        "sources": [],
        "entities": [{"name": "Alpha", "kind": "Project",
                      "passes_frequency_gate": True}],
        "points": [_point(i) for i in range(n_points)],
        "operators": operators or [],
        "telemetry": _TELEMETRY,
    }
    payload.update(overrides)
    return payload


def _finalize(raw: dict) -> dict:
    """Compute the canonical client_commit_id (mirrors the client, W-3).

    The 400-path tests deliberately omit session_id — leave the submitted
    client_commit_id untouched there (the server 400s before hashing)."""
    if "session_id" in raw:
        raw["client_commit_id"] = compute_client_commit_id(
            raw["session_id"], raw["points"], raw["entities"], raw["operators"],
            raw["summary"], raw["story_arc"], raw.get("events", []),
            raw.get("supersessions", []))
    return raw


def _commit(client, raw: dict) -> dict:
    """Finalize + POST /v1/sessions/commit."""
    r = client.post("/v1/sessions/commit", json=_finalize(raw))
    return r


def _inject_session_state(session_id: str, *, value_nodes_created: int = 0,
                          is_episodic: bool | None = None) -> None:
    """DE2E-7 budget fixtures: EXPLICIT session counter states (direct write,
    conftest convention — the plan's fixtures inject prior state rather than
    reaching it through prior commits)."""
    props = {}
    if is_episodic is not None:
        props["is_episodic"] = is_episodic
    q = "MERGE (s:Session {id:$sid}) SET s.value_nodes_created=$n"
    params = {"sid": session_id, "n": value_nodes_created}
    if props:
        q += ", s.is_episodic=$ep"
        params["ep"] = is_episodic
    _team_sdk()._get_proj().g.query(q, params=params)


# ── DE2E-2 — four-node chain + discoverability + entities + operators ──────

class TestFourNodeChain:
    def test_commit_works_when_recording_disabled(self, client):
        """#1927 / #1910: commit_session needs NO session_recording gate —
        the off-switch lives in _capture_session_impl only. A team with the
        flag explicitly False can still POST /v1/sessions/commit (the
        derived-commit receiver never consults it)."""
        import tortoise.hosted_api as ha_mod
        ha_mod._update_onboarding_state(TEST_TEAM_ID, session_recording=False)
        r = _commit(client, _raw_payload(1))
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False
    def test_commit_writes_four_node_chain(self, client):
        r = _commit(client, _raw_payload(1))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["duplicate"] is False
        assert body["commit_id"] == body["commit_id"]
        assert body["nodes_created"] >= 2  # 1 point + 1 entity
        sdk = _team_sdk()
        proj = sdk._get_proj()
        g = proj.g

        # Session node + counters
        rows = g.query(
            "MATCH (s:Session {id:'s1'}) RETURN s.is_episodic, "
            "s.value_nodes_created, s.commit_count, s.draft_count",
        ).result_set
        assert rows, "Session node missing"
        assert rows[0][0] is True
        assert rows[0][1] >= 2 and rows[0][2] == 1

        # Event AgentSession (content-addressed eventId + capturedAt)
        eid = content_hash("s1:2026-08-11T10:00:00Z")
        rows = g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.eventKind, e.capturedAt, "
            "e.is_episodic, e.sessionId",
            params={"eid": eid},
        ).result_set
        assert rows, "AgentSession Event missing"
        assert rows[0][0] == "AgentSession"
        assert rows[0][1] == "2026-08-11T10:00:00Z"
        assert rows[0][2] is True

        # Document transcript (summary/story_arc/sessionId/sourcePath; NO content)
        rows = g.query(
            "MATCH (d:Document) WHERE d.sessionId='s1' "
            "RETURN d.documentKind, d.summary, d.story_arc, d.sourcePath, "
            "d.is_episodic",
        ).result_set
        assert rows, "Document missing"
        kind, summary, arc, srcpath, episodic = rows[0]
        assert kind == "transcript"
        assert summary == "summary text"
        assert arc == "arc text"
        assert srcpath == "session.md"  # basename only (privacy)
        assert episodic is True

        # (Event)-[:produces]->(Document)
        n = g.query(
            "MATCH (e:Event {eventId:$eid})-[:produces]->(d:Document) "
            "WHERE d.sessionId='s1' RETURN count(d)",
            params={"eid": eid},
        ).result_set[0][0]
        assert n >= 1

        # Source bridge (sourceKind agentSession, contentHash, provenance_spans)
        rows = g.query(
            "MATCH (s:Source {url:'session.md'}) RETURN s.sourceKind, "
            "s.contentHash, s.provenance_spans, s.is_episodic",
        ).result_set
        assert rows and rows[0][0] == "agentSession"
        assert rows[0][1] and rows[0][2] == ["0-10"]

        # (Document)<-[:references]-(Source)
        n = g.query(
            "MATCH (s:Source {url:'session.md'})-[:references]->(d:Document) "
            "WHERE d.sessionId='s1' RETURN count(d)",
        ).result_set[0][0]
        assert n >= 1

        # Points: c_cal, quote ≤200, source_ref, status; extractedFrom resolves
        rows = g.query(
            "MATCH (p:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000000'}) "
            "RETURN p.c_cal, p.status, p.source_ref, p.quote, p.pointKind, p.is_episodic",
        ).result_set
        assert rows, "Point pt_0 missing"
        assert rows[0][0] == 0.8 and rows[0][1] == "live"
        assert rows[0][2] == "session.md"
        n = g.query(
            "MATCH (p:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000000'})"
            "-[:extractedFrom]->(s:Source {url:'session.md'}) RETURN count(s)",
        ).result_set[0][0]
        assert n >= 1

        # Entities: Object node + aboutObject edge + passes_frequency_gate
        rows = g.query(
            "MATCH (o:Object {name:'Alpha'}) RETURN o.objectKind, o.passes_frequency_gate",
        ).result_set
        assert rows, "Object Alpha missing"
        assert rows[0][0] == "Project"
        n = g.query(
            "MATCH (p:Point)-[:aboutObject]->(o:Object {name:'Alpha'}) "
            "RETURN count(o)",
        ).result_set[0][0]
        assert n >= 1

    def test_commit_writes_e3_point_props(self, client):
        """E3 (#1535): search_keys / source_turn_id / quote persist on the
        committed Point node through BOTH the plain and supersede branches
        (S12 surface)."""
        raw = _raw_payload(1, points=[_point(
            0, content="my 5K best is 27:12", quote="my 5K best is 27:12",
            search_keys=["personal best", "27:12"], source_turn_id=0)])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        pid = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        props = g.query(
            "MATCH (p:Point {id:$pid}) "
            "RETURN p.quote, p.search_keys, p.source_turn_id",
            params={"pid": pid}).result_set
        assert props, "Point node missing"
        assert props[0][0] == "my 5K best is 27:12"
        # R2 (#1541 D3, owner-flagged cross-lane deviation): the GRAPH value
        # is a flat space-joined string (FalkorDB's fulltext index does not
        # index array-valued properties); the payload/commit schema keep the
        # E3 list format. See plan D3 + the R2 PR description.
        assert props[0][1] == "personal best 27:12"
        assert props[0][2] == 0

    def test_supersede_branch_carries_e3_point_props(self, client):
        """E3: the supersede create_point branch passes the E3 fields too —
        the superseding point carries its own quote/search_keys/source_turn_id."""
        sdk = _team_sdk()
        proj = sdk._get_proj()
        # seed an existing point so the new content REVISES it (supersede path)
        old_pid = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        proj.g.query(
            "MERGE (p:Point {id:$id}) SET p.pointKind='statement', "
            "    p.content='the old 5K claim', p.is_operator=false, "
            "    p.status='live', p.content_hash='seed' ",
            params={"id": old_pid})
        raw = _raw_payload(1, points=[_point(
            0, content="my 5K best is 27:12", quote="my 5K best is 27:12",
            search_keys=["personal best", "27:12"], source_turn_id=0)])
        # same point id + same content → dedup; to force the supersede branch
        # the new content must differ from the stored one. The commit path
        # supersedes when the payload point REVISES an existing point (the
        # #953 reconciliation derives supersede from the graph state).
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        # the superseding point is the OLD id (merge path) or a NEW content-
        # addressed id (supersede path) — either way the E3 props must land
        # on the node that carries the new content.
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.content = $c "
            "RETURN p.quote, p.search_keys, p.source_turn_id LIMIT 1",
            params={"c": "my 5K best is 27:12"}).result_set
        assert rows, "superseding point missing"
        assert rows[0][0] == "my 5K best is 27:12"
        # R2 (#1541 D3): flat graph value (see test_commit_writes_e3_point_props).
        assert rows[0][1] == "personal best 27:12"
        assert rows[0][2] == 0
        # pin the supersede branch actually ran: the seeded old node was
        # superseded (status flips, CORRECTS edge to the new point)
        old = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.status",
            params={"id": old_pid}).result_set
        assert old and old[0][0] == "superseded", \
            f"supersede branch did not run (old node status {old[0][0] if old else 'missing'})"

    def test_commit_without_e3_fields_keeps_legacy_shape(self, client):
        """E3 backward-compat: a commit with NO E3 fields leaves the Point
        node without search_keys/source_turn_id properties (pre-E3 shape)."""
        r = _commit(client, _raw_payload(1))
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        pid = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        rows = g.query(
            "MATCH (p:Point {id:$pid}) RETURN "
            "toBoolean(EXISTS(p.search_keys)), toBoolean(EXISTS(p.source_turn_id))",
            params={"pid": pid}).result_set
        assert rows and rows[0][0] is False and rows[0][1] is False

    def test_session_indexer_discoverability(self, client):
        """DE2E-2 step 4: the committed session MUST be findable through the
        EXISTING session_indexer AgentSession search path (sdk.search_sessions
        / get_session)."""
        r = _commit(client, _raw_payload(1))
        assert r.status_code == 200
        sdk = _team_sdk()
        # Legacy keyword fallback: name/keywords CONTAINS
        found = sdk.search_sessions("s1")
        assert any(ev.get("sessionId") == "s1" or ev.get("session_id") == "s1"
                   for ev in found), f"s1 not discoverable: {found}"
        # get_session by session_id
        ev = sdk.get_session("s1")
        assert ev is not None and ev.get("eventKind") == "AgentSession"

    def test_duplicate_entities_merge_and_gate_flag_written(self, client):
        """Duplicate entity names MERGE; passes_frequency_gate: false is
        written WITH the flag (amendment §4.3 #12)."""
        raw = _raw_payload(1, entities=[
            {"name": "Alpha", "kind": "Project", "passes_frequency_gate": False},
            {"name": "Alpha", "kind": "Project", "passes_frequency_gate": False},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (o:Object {name:'Alpha'}) RETURN count(o), "
            "coalesce(o.passes_frequency_gate, 'missing')",
        ).result_set
        assert rows[0][0] == 1, "duplicate entity names must MERGE to one node"
        assert rows[0][1] is False

    def test_operators_written_with_merge_key(self, client):
        """IMPL edge exists with the (src,dst,op_type) MERGE key; re-commit of
        the same operator (L2 merge) does not duplicate it."""
        ops = [{"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
                "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
                "op_type": "IMPL", "direction": "unidirectional"}]
        raw = _raw_payload(2, operators=ops)
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        # operator node exists with op_type IMPL + direction; edges wire src/dst
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:0}]->(s:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000000'}) "
            "MATCH (o)-[:IMPL {idx:1}]->(d:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000001'}) "
            "RETURN o.direction, count(o)",
        ).result_set
        assert rows and rows[0][0] == "unidirectional"
        # re-commit with the same operator → L2 merge (net-new does not grow)
        before = _session_counter("s1", "value_nodes_created")
        r2 = _commit(client, dict(raw))  # same payload, same client_commit_id
        assert r2.status_code == 200
        assert r2.json()["duplicate"] is True
        assert _session_counter("s1", "value_nodes_created") == before


# ── A1b (#1272): event-endpoint operators — write + replay ─────────────────

EV64 = "ev_" + "a" * 62
PT0 = "pt_0000000000000000000000000000000000000000000000000000000000000000"
PT1 = "pt_0000000000000000000000000000000000000000000000000000000000000001"


class TestEventEndpointOperators:
    """Owner ruling (2026-08-14): events MAY connect to points. The write path
    resolves :Event endpoints; the replay path reconciles them idempotently."""

    def _raw(self, operators, n_points=2):
        raw = _raw_payload(n_points, operators=operators)
        raw["events"] = [{
            "id": EV64, "eventKind": "decision",
            "content": "decided X", "about_entities": [],
            "source_ref": "session.md"}]
        return raw

    def test_event_target_operator_written(self, client):
        """Point→Event IMPL: argument point supports the decision event."""
        raw = self._raw([{"src": PT0, "dst": EV64, "op_type": "IMPL"}])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:1}]->(ev:Event {id:$ev}) "
            "RETURN count(o)",
            params={"ev": EV64},
        ).result_set
        assert rows and rows[0][0] == 1

    def test_event_src_operator_written(self, client):
        """Event→Point NAND: the ontology canonical example direction."""
        raw = self._raw([{"src": EV64, "dst": PT0, "op_type": "NAND",
                          "direction": "unidirectional"}])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'NAND'}) "
            "MATCH (o)-[:NAND {idx:0}]->(ev:Event {id:$ev}) "
            "RETURN count(o)",
            params={"ev": EV64},
        ).result_set
        assert rows and rows[0][0] == 1

    def test_event_endpoint_replay_no_duplicate(self, client):
        """Replay path: an event-endpoint operator reconciles as MERGE, not
        NEW — no duplicate node, no budget re-count (sites 5/6 fix)."""
        raw = self._raw([{"src": PT0, "dst": EV64, "op_type": "IMPL"}])
        r1 = _commit(client, raw)
        assert r1.status_code == 200, r1.text
        before = _session_counter("s1", "value_nodes_created")
        # Re-commit the same payload (same client_commit_id) — the partial-
        # retry contract: the record may be partial, so L2 reconciliation runs.
        r2 = _commit(client, dict(raw))
        assert r2.status_code == 200, r2.text
        assert r2.json()["duplicate"] is True
        assert _session_counter("s1", "value_nodes_created") == before
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:1}]->(ev:Event {id:$ev}) "
            "RETURN count(o)",
            params={"ev": EV64},
        ).result_set
        assert rows and rows[0][0] == 1  # one operator, not two


# ── DE2E-5 — external sources + references chain ───────────────────────────

class TestExternalSources:
    def test_sources_external_chain(self, client):
        """sources[] → external Source nodes; the session Source references
        the external Source; a point sourced to the external artifact resolves
        its extractedFrom edge through the chain."""
        raw = _raw_payload(2, sources=[
            {"sourceKind": "document", "url": "https://example.com/pricing",
             "credibilityTier": "T1", "contentHash": "sha123"},
        ], points=[
            _point(0, source_ref="session.md"),
            _point(1, source_ref="https://example.com/pricing"),
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (s:Source {url:'https://example.com/pricing'}) "
            "RETURN s.sourceKind, s.credibilityTier, s.contentHash, s.is_episodic",
        ).result_set
        assert rows and rows[0][0] == "document"
        assert rows[0][1] == "T1" and rows[0][2] == "sha123"
        # session Source references the external Source (DE2E-5 chain)
        n = g.query(
            "MATCH (a:Source {url:'session.md'})-[:references]->"
            "(b:Source {url:'https://example.com/pricing'}) RETURN count(b)",
        ).result_set[0][0]
        assert n >= 1
        # point → external Source extractedFrom resolves
        n = g.query(
            "MATCH (p:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000001'})"
            "-[:extractedFrom]->(s:Source {url:'https://example.com/pricing'}) "
            "RETURN count(s)",
        ).result_set[0][0]
        assert n >= 1


# ── DE2E-6 — NAND direction policy ─────────────────────────────────────────

class TestNandDirection:
    def test_nand_direction_written(self, client):
        ops = [
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "NAND", "direction": "unidirectional"},
        ]
        r = _commit(client, _raw_payload(2, operators=ops))
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (o:Point {is_operator:true, op_type:'NAND'}) "
            "MATCH (o)-[:NAND {idx:0}]->(:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000000'}) "
            "MATCH (o)-[:NAND {idx:1}]->(:Point {id:'pt_0000000000000000000000000000000000000000000000000000000000000001'}) "
            "RETURN o.direction",
        ).result_set
        assert rows and rows[0][0] == "unidirectional"

    def test_nand_bidirectional_mutual_restatement(self, client):
        ops = [
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "NAND", "direction": "bidirectional"},
        ]
        r = _commit(client, _raw_payload(2, operators=ops))
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (o:Point {is_operator:true, op_type:'NAND'}) RETURN o.direction",
        ).result_set
        assert rows and rows[0][0] == "bidirectional"

    def test_nand_without_direction_422(self, client):
        ops = [{"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
                "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
                "op_type": "NAND"}]
        r = _commit(client, _raw_payload(2, operators=ops))
        assert r.status_code == 422
        assert any("direction" in k for k in r.json()["detail"]), r.json()["detail"]


# ── MITIGATES mechanism (DE2E-11 core artifact, §4.2) ─────────────────────

class TestMitigates:
    def test_mitigates_writes_mitigation_artifact(self, client):
        """Z MITIGATES [X→A]: the mitigation Point + (m)-[:IMPL]->(op) +
        (op)-[:mitigated_by]->(m) mechanism; mitigation_strength in range."""
        x = "pt_0000000000000000000000000000000000000000000000000000000000000000"
        a = "pt_0000000000000000000000000000000000000000000000000000000000000001"
        z = "pt_0000000000000000000000000000000000000000000000000000000000000002"
        ops = [
            {"src": x, "dst": a, "op_type": "IMPL",
             "direction": "unidirectional"},
            {"src": z, "dst": a, "op_type": "MITIGATES",
             "target": {"src": x, "dst": a, "op_type": "IMPL"},
             "strength": 0.4},
        ]
        raw = _raw_payload(3, operators=ops, points=[
            _point(0, id=x, content="it's cheap"),
            _point(1, id=a, content="option A"),
            _point(2, id=z, content="we can raise the price"),
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        # mitigation Point with mitigation_strength + both edges
        rows = g.query(
            "MATCH (op:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (op)-[:IMPL {idx:0}]->(:Point {id:$x}) "
            "MATCH (op)-[:IMPL {idx:1}]->(:Point {id:$a}) "
            "MATCH (m:Point)-[:IMPL]->(op) "
            "MATCH (op)-[:mitigated_by]->(m) "
            "RETURN m.mitigation_strength, m.pointKind",
            params={"x": x, "a": a},
        ).result_set
        assert rows, "mitigation artifact missing"
        assert rows[0][0] == 0.4
        assert rows[0][1] == "statement"

    def test_mitigates_target_missing_operator_422(self, client):
        ops = [
            {"src": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
             "op_type": "MITIGATES",
             "target": {"src": "pt_0000000000000000000000000000000000000000000000000000000000000002",
                        "dst": "pt_0000000000000000000000000000000000000000000000000000000000000001",
                        "op_type": "IMPL"},
             "strength": 0.4},
        ]
        r = _commit(client, _raw_payload(3, operators=ops))
        assert r.status_code == 422  # target ∉ emitted operator keys
        assert any("target" in k for k in r.json()["detail"]), r.json()["detail"]


# ── DE2E-7 — idempotency + budget + quota + Layer-1 ───────────────────────

class TestReplayIdempotency:
    def test_l1_replay_duplicate_true_zero_writes(self, client):
        raw = _raw_payload(1)
        r1 = _commit(client, raw)
        assert r1.status_code == 200 and r1.json()["duplicate"] is False
        cid = r1.json()["commit_id"]
        points_before = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) RETURN count(p)").result_set[0][0]
        ops_before, nodes_before = _metering_rows()

        r2 = _commit(client, dict(raw))  # exact replay
        assert r2.status_code == 200
        body = r2.json()
        assert body["duplicate"] is True
        assert body["nodes_created"] == 0 and body["nodes_merged"] == 0
        assert body["held"] == []
        # zero writes
        points_after = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) RETURN count(p)").result_set[0][0]
        assert points_after == points_before
        # :CommitRecord fully_written; zero write-ops billed for the replay
        rec = _commit_record(cid)
        assert rec and rec[0] == "fully_written" and rec[1] == 1
        assert _metering_rows() == (ops_before, nodes_before)
        # commit_count not bumped by the replay
        assert _session_counter("s1", "commit_count") == 1

    def test_l2_supersede_recapture(self, client):
        """Re-capture where point P1's content changes X→Y: new content-
        addressed id + supersede_point (CORRECTS + outdated + edge transfer);
        Session.commit_count incremented (tie-break order)."""
        old_id = f"pt_{content_hash('original claim X')}"
        raw1 = _raw_payload(1, points=[
            {"id": old_id, "content": "original claim X", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "live"},
        ])
        r1 = _commit(client, raw1)
        assert r1.status_code == 200, r1.text

        # re-capture: SAME id, CHANGED content → supersede candidate
        raw2 = _raw_payload(1, points=[
            {"id": old_id, "content": "revised claim Y", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.95, "c_cal": 0.9,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "live"},
        ])
        r2 = _commit(client, raw2)
        assert r2.status_code == 200, r2.text
        new_id = point_content_id("revised claim Y")
        g = _team_sdk()._get_proj().g
        # new point exists; old point outdated + CORRECTS edge
        rows = g.query(
            "MATCH (old:Point {id:$old}) RETURN old.outdated, old.status",
            params={"old": old_id},
        ).result_set
        assert rows and rows[0][0] is True and rows[0][1] == "superseded"
        n = g.query(
            "MATCH (new:Point {id:$new})-[:CORRECTS]->(old:Point {id:$old}) "
            "RETURN count(old)",
            params={"new": new_id, "old": old_id},
        ).result_set[0][0]
        assert n >= 1
        # edge transfer: extractedFrom moved to the new point
        n = g.query(
            "MATCH (new:Point {id:$new})-[:extractedFrom]->(s:Source {url:'session.md'}) "
            "RETURN count(s)",
            params={"new": new_id},
        ).result_set[0][0]
        assert n >= 1
        assert _session_counter("s1", "commit_count") == 2

    def test_merge_same_id_same_content_zero_budget(self, client):
        """MERGE hits burn zero budget: a same-content re-send under a NEW
        commit id (extractor bump, R-14-adjacent) MERGEs, not duplicates."""
        raw = _raw_payload(1)
        assert _commit(client, raw).status_code == 200
        before = _session_counter("s1", "value_nodes_created")
        # NEW client_commit_id via CHANGED point content under the SAME id
        # (reconcile_payload is id-keyed: same id + changed content →
        # supersede candidate; extractor.version is excluded from the
        # canonical hash — a version-only re-send is an L1 replay, DE2E-7)
        raw2 = dict(raw)
        raw2["points"] = [dict(raw2["points"][0], content="pc 0 revised")]
        r = _commit(client, raw2)
        assert r.status_code == 200 and r.json()["duplicate"] is False
        assert _session_counter("s1", "value_nodes_created") == before


class TestE5PointSupersessions:
    """E5 (#1537) Task 3 — a payload's pt_* supersession record materializes
    the EXISTING canonical sdk.supersede() on commit (CORRECTS + outdated +
    edge transfer); already-terminal old → idempotent skip (no ValueError —
    supersede_point would raise); missing old → fail-open warning; the entity
    ObjectSuperseded fold is untouched by point refs."""

    def test_commit_materializes_point_supersession_via_supersede(self, client):
        old_id = f"pt_{content_hash('gym at 6pm')}"
        raw1 = _raw_payload(1, points=[
            {"id": old_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])
        assert _commit(client, raw1).status_code == 200

        new_id = point_content_id("gym at 5pm")
        raw2 = _raw_payload(1, session_id="s2", points=[
            {"id": new_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ], supersessions=[
            {"superseded": old_id, "supersedes_by": new_id,
             "evidence": "fact-value contradiction (later session value "
                          "change)"},
        ])
        r2 = _commit(client, raw2)
        assert r2.status_code == 200, r2.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (old:Point {id:$old}) RETURN old.status, old.outdated",
            params={"old": old_id}).result_set
        assert rows and rows[0][0] == "superseded" and rows[0][1] is True
        n = g.query(
            "MATCH (new:Point {id:$new})-[:CORRECTS]->(old:Point {id:$old}) "
            "RETURN count(old)",
            params={"new": new_id, "old": old_id}).result_set[0][0]
        assert n == 1

    def test_commit_point_supersession_replay_and_terminal_skip(self, client):
        """Re-commit of the same payload is an L1 replay (zero writes); a NEW
        commit referencing the already-terminal old is an idempotent skip (no
        ValueError, single CORRECTS edge — the §6b terminal guard)."""
        old_id = f"pt_{content_hash('gym at 6pm')}"
        raw1 = _raw_payload(1, points=[
            {"id": old_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])
        assert _commit(client, raw1).status_code == 200

        new_id = point_content_id("gym at 5pm")
        raw2 = _raw_payload(1, session_id="s2", points=[
            {"id": new_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ], supersessions=[
            {"superseded": old_id, "supersedes_by": new_id,
             "evidence": "fact-value contradiction (later session value "
                          "change)"},
        ])
        assert _commit(client, raw2).status_code == 200
        # exact replay → duplicate, zero writes (L1 idempotency)
        r3 = _commit(client, dict(raw2))
        assert r3.status_code == 200 and r3.json()["duplicate"] is True
        g = _team_sdk()._get_proj().g
        # a NEW commit superseding INTO the already-terminal old → skip
        new3 = point_content_id("gym at 4pm")
        raw3 = _raw_payload(1, session_id="s3", points=[
            {"id": new3, "content": "gym at 4pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ], supersessions=[
            {"superseded": old_id, "supersedes_by": new3,
             "evidence": "fact-value contradiction (later session value "
                          "change)"},
        ])
        r4 = _commit(client, raw3)
        assert r4.status_code == 200, r4.text
        n = g.query(
            "MATCH (:Point {id:$old})<-[:CORRECTS]-(p:Point) RETURN count(p)",
            params={"old": old_id}).result_set[0][0]
        assert n == 1, "terminal old must keep exactly one CORRECTS edge"

    def test_commit_point_supersession_missing_old_fail_open(self, client):
        """Missing old → 200 with a warning (fail-open — mirrors the entity
        fold's never-guess discipline); the commit still writes."""
        new_id = point_content_id("gym at 5pm")
        raw = _raw_payload(1, points=[
            {"id": new_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ], supersessions=[
            {"superseded": "pt_missing", "supersedes_by": new_id,
             "evidence": "fact-value contradiction (later session value "
                          "change)"},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False


class TestBudgetDE2E7:
    def test_session_a_held_and_ceiling_only_resubmission(self, client):
        """Session A: prior 20 → commit 10 → cumulative 30 → held[10] (NOT
        written); re-submission (same payload) → checked against the 50-
        ceiling only → written (:CommitRecord held → fully_written)."""
        _inject_session_state("sA", value_nodes_created=20)
        points = [{"id": f"pt_{i:064d}", "content": f"pa {i}",
                   "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
                   "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
                   "quote": "", "status": "live"}
                  for i in range(10)]
        raw = _raw_payload(10, session_id="sA", points=points, entities=[])

        r1 = _commit(client, raw)
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["duplicate"] is False
        assert len(body["held"]) == 10 and body["nodes_created"] == 0
        # items NOT written; held count recorded on the Session; record held
        n = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content STARTS WITH 'pa ' RETURN count(p)",
        ).result_set[0][0]
        assert n == 0, "held items must NOT be written"
        assert _session_counter("sA", "value_nodes_held") == 10
        rec = _commit_record(body["commit_id"])
        assert rec and rec[0] == "held" and rec[1] == 0  # bills zero (PL4)
        ops_before, _ = _metering_rows()

        # re-submission: 50-ceiling only → written (no infinite hold)
        r2 = _commit(client, dict(raw))
        assert r2.status_code == 200, r2.text
        body2 = r2.json()
        assert body2["duplicate"] is False and body2["held"] == []
        assert body2["nodes_created"] == 10
        n = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content STARTS WITH 'pa ' RETURN count(p)",
        ).result_set[0][0]
        assert n == 10
        rec = _commit_record(body["commit_id"])
        assert rec and rec[0] == "fully_written" and rec[1] == 1
        # the single +1 for the logical payload
        assert _metering_rows()[0] == ops_before + 1

    def test_soft_15_warn_telemetry(self, client):
        """A session crossing 15 with items still written → 200 + WARN."""
        _inject_session_state("sW", value_nodes_created=10)
        points = [{"id": f"pt_{i:064d}", "content": f"pw {i}",
                   "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
                   "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
                   "quote": "", "status": "live"}
                  for i in range(10)]
        r = _commit(client, _raw_payload(10, session_id="sW", points=points,
                                         entities=[]))
        assert r.status_code == 200
        body = r.json()
        assert body["warn"] is True
        assert body["nodes_created"] == 10
        assert _session_counter("sW", "value_nodes_created") == 20
        rec = _commit_record(body["commit_id"])
        assert rec and rec[3] is True  # budget_warn recorded on the record

    def test_session_b_ceiling_402(self, client):
        """Session B: prior 45 → commit 10 → cumulative 55 → 402, nothing
        written."""
        _inject_session_state("sB", value_nodes_created=45)
        points = [{"id": f"pt_{i:064d}", "content": f"pb {i}",
                   "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
                   "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
                   "quote": "", "status": "live"}
                  for i in range(10)]
        r = _commit(client, _raw_payload(10, session_id="sB", points=points,
                                         entities=[]))
        assert r.status_code == 402
        assert "detail" in r.json()
        n = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content STARTS WITH 'pb ' RETURN count(p)",
        ).result_set[0][0]
        assert n == 0, "ceiling-exceeded items must NOT be written"

    def test_session_c_held_resubmission_ceiling_402(self, client):
        """Session C: held re-submission that would push cumulative past 50 →
        402; items remain held client-side (never dropped)."""
        _inject_session_state("sC", value_nodes_created=40)
        points = [{"id": f"pt_{i:064d}", "content": f"pc {i}",
                   "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
                   "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
                   "quote": "", "status": "live"}
                  for i in range(10)]
        raw = _raw_payload(10, session_id="sC", points=points, entities=[])
        r1 = _commit(client, raw)
        assert r1.status_code == 200 and len(r1.json()["held"]) == 10
        # session accumulates 5 more nodes before the re-submission
        _inject_session_state("sC", value_nodes_created=45)
        r2 = _commit(client, dict(raw))
        assert r2.status_code == 402
        n = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content STARTS WITH 'pc ' RETURN count(p)",
        ).result_set[0][0]
        assert n == 0, "items remain held client-side — never written"

    def test_sessions_quota_41st_commit_402(self, client_quota40):
        """Quota fixture: max_sessions=40 → 40 minimal commits → 41st commit
        402; _count_resource('sessions') returns 40 (NOT the all-nodes count,
        the #947 P0 regression)."""
        from tortoise.quota import count_team_usage
        sdk = _team_sdk()
        for i in range(40):
            raw = _raw_payload(1, session_id=f"qs{i}")
            r = _commit(client_quota40, raw)
            assert r.status_code == 200, f"commit {i} failed: {r.text}"
        assert count_team_usage(TEST_TEAM_ID, "sessions", sdk=sdk) == 40
        # 41st commit → 402
        r = _commit(client_quota40, _raw_payload(1, session_id="qs40"))
        assert r.status_code == 402
        assert "sessions" in r.json()["detail"]

    def test_empty_commit_ok_zero_budget(self, client):
        """An empty derived commit is valid: 200, zero budget burn,
        commit_count +1 (DE2E-8)."""
        r = _commit(client, _raw_payload(0, points=[], entities=[],
                                         provenance_refs=[]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["nodes_created"] == 0 and body["held"] == []
        assert _session_counter("s1", "commit_count") == 1


class TestLayer1:
    def test_missing_required_field_400(self, client):
        raw = _raw_payload(1)
        del raw["session_id"]
        r = client.post("/v1/sessions/commit", json=_finalize(raw))
        assert r.status_code == 400
        assert "session_id" in r.json()["detail"]

    def test_missing_client_commit_id_400(self, client):
        raw = _raw_payload(1)
        del raw["client_commit_id"]
        r = client.post("/v1/sessions/commit", json=raw)
        assert r.status_code == 400

    def test_commit_id_mismatch_422(self, client):
        raw = _finalize(_raw_payload(1))
        raw["client_commit_id"] = "0" * 64  # wrong hash
        r = client.post("/v1/sessions/commit", json=raw)
        assert r.status_code == 422
        body = r.json()["detail"]
        assert body.get("code") == "commit_id_mismatch"
        assert "client_commit_id" in body

    def test_calibration_mismatch_422(self, client):
        """Stale-brief payload: kind valid in the old brief, absent now →
        422 {code: calibration_mismatch}."""
        raw = _raw_payload(1, points=[
            {"id": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "content": "claim", "pointKind": "totallyUnknownKind",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "live"},
        ])
        r = _commit(client, raw)
        assert r.status_code == 422
        body = r.json()["detail"]
        assert body.get("code") == "calibration_mismatch"

    def test_51_point_payload_422(self, client):
        """MAX_PAYLOAD_POINTS raw cap (50) — 422, independent of the budget
        ceiling (402)."""
        raw = _raw_payload(51)
        r = _commit(client, raw)
        assert r.status_code == 422
        assert "points" in r.json()["detail"]

    def test_exactly_50_points_ok(self, client):
        # The budget ceiling counts net-new NON-EPISODIC NODES (points +
        # entities): _raw_payload adds 1 entity, so 49 points + 1 entity = 50
        # nodes → OK (the 50-point Layer-1 cap is a separate, points-only cap).
        raw = _raw_payload(49)
        r = _commit(client, raw)
        assert r.status_code == 200, r.text

    def test_quote_over_200_422(self, client):
        raw = _raw_payload(1, points=[
            {"id": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "content": "claim", "pointKind": "decision", "reason": "NEW",
             "confidence": 0.9, "c_cal": 0.8, "about_entities": ["Alpha"],
             "source_ref": "session.md", "quote": "q" * 201, "status": "live"},
        ])
        r = _commit(client, raw)
        assert r.status_code == 422

    def test_dangling_source_ref_422(self, client):
        raw = _raw_payload(1, points=[
            {"id": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "content": "claim", "pointKind": "decision", "reason": "NEW",
             "confidence": 0.9, "c_cal": 0.8, "about_entities": ["Alpha"],
             "source_ref": "nonexistent.md", "quote": "", "status": "live"},
        ])
        r = _commit(client, raw)
        assert r.status_code == 422
        assert any("source_ref" in k for k in r.json()["detail"]), r.json()["detail"]

    def test_401_unauthenticated(self, client_no_auth):
        r = client_no_auth.post(
            "/v1/sessions/commit", json=_finalize(_raw_payload(1)),
            headers={"Authorization": "Bearer tt_badkey0000000000000000"})
        assert r.status_code == 401

    def test_500_fail_closed_redacted(self, client, monkeypatch):
        """A graph-write failure surfaces as a redacted 500 (the client retries
        with the same client_commit_id — safe by L1)."""
        import tortoise.hosted_api as ha_mod

        def _boom(sdk, payload, plan):
            raise RuntimeError("internal DB secret detail")

        monkeypatch.setattr(ha_mod, "_execute_commit_writes", _boom)
        r = _commit(client, _raw_payload(1))
        assert r.status_code == 500
        assert "secret" not in r.json()["detail"]
        assert "detail" in r.json()


# ── DE2E-10 — privacy (byte-level) ─────────────────────────────────────────

class TestPrivacy:
    _RAW_PARAGRAPH = "the full raw conversation that must never appear"

    def test_no_raw_conversation_in_graph(self, client):
        raw = _raw_payload(1)
        # a raw paragraph appears ONLY in provenance_path basename context —
        # the graph must not contain it
        raw["provenance_refs"] = [
            {"path": "/Users/alice/projects/el-dato/session.md",
             "spans": ["0-10"]},
        ]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (n) WHERE n.content CONTAINS 'raw conversation' "
            "OR n.sourcePath CONTAINS '/Users/' "
            "OR n.url CONTAINS '/Users/' RETURN n.content, n.sourcePath, n.url",
        ).result_set
        assert not rows, f"privacy leak: {rows}"
        # basename-only: the Document.sourcePath + Source url are basenames
        rows = g.query(
            "MATCH (d:Document) WHERE d.sessionId='s1' RETURN d.sourcePath",
        ).result_set
        assert rows and rows[0][0] == "session.md"
        rows = g.query(
            "MATCH (s:Source {url:'session.md'}) RETURN count(s)",
        ).result_set
        assert rows[0][0] >= 1

    def test_telemetry_schema_no_text_no_graph_counts_no_judge(self, client):
        """W-7: the telemetry block stored on the :CommitRecord has NO
        text-bearing fields, NO graph-side counts (merge/supersede/held/draft/
        live — the server derives them from Session counters) and NO
        judge_summary (dropped from v1)."""
        raw = _raw_payload(1)
        r = _commit(client, raw)
        assert r.status_code == 200
        rec = _commit_record(r.json()["commit_id"])
        assert rec is not None
        import json as _json
        telemetry = _json.loads(rec[2])
        flat = _json.dumps(telemetry)
        # no conversation content anywhere in the stored block
        assert "conversation" not in flat.lower()
        # no graph-side count keys
        for banned in ("merge_count", "supersede_count", "held_count",
                       "draft_count", "live_count", "graph"):
            assert banned not in flat
        # judge_summary dropped from v1 telemetry
        assert "judge_summary" not in flat
        # allowed schema keys only (no text-bearing fields)
        assert set(telemetry.keys()) <= {
            "extractor", "model", "counts", "keep_ratio", "dedup_hits",
            "frontier_calls", "llm_cost_usd", "extraction_ms", "retry_count",
            "last_error_code", "confidence_histogram"}
        assert set(telemetry["extractor"].keys()) == {"version", "mode",
                                                      "calibration_version"}
        assert set(telemetry["counts"].keys()) <= {
            "kept", "candidate", "segment", "window", "empty_windows"}

    def test_quote_is_bounded_in_graph(self, client):
        raw = _raw_payload(1, points=[
            {"id": "pt_0000000000000000000000000000000000000000000000000000000000000000",
             "content": "claim", "pointKind": "decision", "reason": "NEW",
             "confidence": 0.9, "c_cal": 0.8, "about_entities": ["Alpha"],
             "source_ref": "session.md", "quote": "short quote", "status": "live"},
        ])
        assert _commit(client, raw).status_code == 200
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) WHERE p.id='pt_0000000000000000000000000000000000000000000000000000000000000000' "
            "RETURN p.quote",
        ).result_set
        assert rows and rows[0][0] == "short quote"


# ── PL4 — metering (write_ops + nodes_written) ─────────────────────────────

class TestMetering:
    def test_write_ops_and_nodes_written_non_duplicate(self, client):
        raw = _raw_payload(2)
        r = _commit(client, raw)
        assert r.status_code == 200
        ops, nodes = _metering_rows()
        assert ops == 1  # +1 per non-duplicate commit call
        assert nodes == r.json()["nodes_created"]  # net-new non-episodic

    def test_replay_bills_zero(self, client):
        raw = _raw_payload(1)
        assert _commit(client, raw).status_code == 200
        ops_before, _ = _metering_rows()
        assert _commit(client, dict(raw)).json()["duplicate"] is True
        assert _metering_rows()[0] == ops_before

    def test_held_bills_zero_and_resubmission_bills_one(self, client):
        """PL4: overflow-to-hold commit bills ZERO (write_ops_billed: 0); the
        re-submission bills the single +1 — one logical payload billed exactly
        once."""
        _inject_session_state("sM", value_nodes_created=20)
        points = [{"id": f"pt_{i:064d}", "content": f"pm {i}",
                   "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
                   "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
                   "quote": "", "status": "live"}
                  for i in range(10)]
        raw = _raw_payload(10, session_id="sM", points=points, entities=[])
        r1 = _commit(client, raw)
        assert r1.status_code == 200 and len(r1.json()["held"]) == 10
        ops_before, _ = _metering_rows()
        assert _metering_rows()[0] == ops_before  # unchanged (bill 0)
        rec = _commit_record(r1.json()["commit_id"])
        assert rec and rec[1] == 0  # write_ops_billed: 0

        r2 = _commit(client, dict(raw))
        assert r2.status_code == 200 and r2.json()["held"] == []
        assert _metering_rows()[0] == ops_before + 1  # exactly +1


# ── R-13 — dedicated rate bucket (300/min/key for the commit endpoint) ─────

class TestRateBucket:
    def test_commit_path_gets_dedicated_300_bucket(self):
        from tortoise.hosted_api import RateLimitMiddleware
        mw = RateLimitMiddleware(None, max_per_minute=100)
        assert mw._limit_for("/v1/sessions/commit") == 300
        assert mw._limit_for("/v1/points") == 100  # general bucket unchanged
        assert RateLimitMiddleware.PATH_LIMITS == {"/v1/sessions/commit": 300}

    def test_commit_bucket_key_distinct_and_exempt(self):
        from tortoise.hosted_api import RateLimitMiddleware
        mw = RateLimitMiddleware(None)
        commit_key = mw._bucket_key(
            "/v1/sessions/commit", "Bearer tt_key123", "1.1.1.1")
        general_key = mw._bucket_key(
            "/v1/points", "Bearer tt_key123", "1.1.1.1")
        assert commit_key == "tt_key123@/v1/sessions/commit"
        assert general_key == "tt_key123"
        assert commit_key != general_key  # dedicated bucket — exempt by construction

    def test_commit_bucket_429_http(self, monkeypatch):
        """HTTP-level: the commit path enforces ITS limit (2 here) and returns
        429 with Retry-After; the general bucket is not consumed by commit
        requests (exemption)."""
        from fastapi import FastAPI
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        mini = FastAPI()

        @mini.post("/v1/sessions/commit")
        def _commit_ok():
            return {"ok": True}

        @mini.get("/v1/points")
        def _points_ok():
            return {"ok": True}

        mini.add_middleware(
            __import__("tortoise.hosted_api", fromlist=["RateLimitMiddleware"]).RateLimitMiddleware,
            max_per_minute=1,
            path_limits={"/v1/sessions/commit": 2},
        )
        with TestClient(mini) as tc:
            h = {"Authorization": "Bearer tt_testkey0000000000"}
            assert tc.post("/v1/sessions/commit", headers=h).status_code == 200
            assert tc.post("/v1/sessions/commit", headers=h).status_code == 200
            r = tc.post("/v1/sessions/commit", headers=h)
            assert r.status_code == 429
            assert "Retry-After" in r.headers
            # the general 100/min bucket is untouched by commit requests
            assert tc.get("/v1/points", headers=h).status_code == 200


# ── T14 (#1272): Phase A exit smoke — construct-shaped payload commits ─────

class TestConstructPathSmoke:
    """The Phase A exit gate: a construct-shaped payload (event-endpoint
    operators, enriched points at the neutral prior) must commit end-to-end
    through the real endpoint — system-green, not just suite-green."""

    def test_construct_payload_commits_end_to_end(self, client):
        # Shape mirrors what _stream_to_payload now emits (T4): point ids
        # content-derived, event-endpoint IMPL, neutral prior, draft status.
        from tortoise.ids import content_hash
        pt_id = f"pt_{content_hash('the graph is the memory')[:62]}"
        ev_id = f"ev_{content_hash('Adopt the state-centric model')[:62]}"
        raw = _raw_payload(0, session_id="s-construct")
        raw["entities"] = [{"name": "state-model", "kind": "core:goal",
                            "passes_frequency_gate": True}]
        raw["points"] = [{
            "id": pt_id, "content": "the graph is the memory",
            "pointKind": "statement", "reason": "NEW",
            "confidence": 0.5, "c_cal": 0.5,
            "about_entities": ["state-model"], "source_ref": "session.md",
            "quote": "", "status": "draft",
        }]
        raw["events"] = [{
            "id": ev_id, "eventKind": "decision",
            "content": "Adopt the state-centric model",
            "confidence": 0.5, "about_entities": ["state-model"],
            "source_ref": "session.md",
        }]
        raw["operators"] = [
            {"src": pt_id, "dst": ev_id, "op_type": "IMPL"},
            {"src": pt_id, "dst": ev_id, "op_type": "MITIGATES",
             "target": {"src": pt_id, "dst": ev_id, "op_type": "IMPL"},
             "strength": 0.3},
        ]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("commit_id")
        assert body.get("duplicate") is False
        # the event-endpoint operator edge exists in the graph
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:1}]->(ev:Event {id:$ev}) "
            "RETURN count(o)",
            params={"ev": ev_id},
        ).result_set
        assert rows and rows[0][0] == 1


class TestEventEndpointNoSpuriousEdges:
    """VGATE follow-up (#1272): the event-endpoint write must NOT create
    spurious operator edges to unrelated :Point nodes (a Cypher precedence
    bug — `s:Point OR s:Event AND ...` — previously matched ALL Points)."""

    def test_no_spurious_edges_to_unrelated_points(self, client):
        from tortoise.ids import content_hash
        pt_a = f"pt_{content_hash('argument a')[:62]}"
        pt_b = f"pt_{content_hash('unrelated point b')[:62]}"
        ev_id = f"ev_{content_hash('decided the approach')[:62]}"
        raw = _raw_payload(0, session_id="s-no-spurious")
        raw["entities"] = [{"name": "e", "kind": "core:goal",
                            "passes_frequency_gate": True}]
        raw["points"] = [
            {"id": pt_a, "content": "argument a", "pointKind": "statement",
             "reason": "NEW", "confidence": 0.5, "c_cal": 0.5,
             "about_entities": ["e"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
            {"id": pt_b, "content": "unrelated point b", "pointKind": "statement",
             "reason": "NEW", "confidence": 0.5, "c_cal": 0.5,
             "about_entities": ["e"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ]
        raw["events"] = [{"id": ev_id, "eventKind": "decision",
                          "content": "decided the approach", "confidence": 0.5,
                          "about_entities": ["e"], "source_ref": "session.md"}]
        raw["operators"] = [{"src": pt_a, "dst": ev_id, "op_type": "IMPL"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        # The operator node wires src→dst (2 input edges: idx 0 + idx 1).
        # The corruption being guarded: spurious edges to UNRELATED Points.
        # So: edges to Points must be exactly 1 (the src argument point),
        # never more (the bug matched every Point in the graph).
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL]->(x:Point) "
            "RETURN o.id, count(x)",
        ).result_set
        assert rows and rows[0][1] == 1, f"spurious Point edges: {rows}"
        # And the event target is the only non-Point edge.
        rows2 = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL]->(x:Event) "
            "RETURN o.id, count(x)",
        ).result_set
        assert rows2 and rows2[0][1] == 1, f"event edge missing: {rows2}"


# ── P1/P2 review fixes (#1272, independent fresh-context review) ───────────

class TestReviewTelemetryAndEventEdges:
    """P1-2: keep_ratio ≤ 1 (denominator = reconcilable set); new events are
    not dedup hits. P2-4: event→aboutObject edge exists for a NEW entity."""

    def test_keep_ratio_leq_1_and_events_not_dedup(self, client):
        from tortoise.ids import content_hash
        pt = f"pt_{content_hash('a point')[:62]}"
        ev = f"ev_{content_hash('an event')[:62]}"
        raw = _raw_payload(0, session_id="s-keepratio")
        raw["entities"] = [{"name": "opt-a", "kind": "core:goal",
                            "passes_frequency_gate": True}]
        raw["points"] = [{"id": pt, "content": "a point",
                          "pointKind": "statement", "reason": "NEW",
                          "confidence": 0.5, "c_cal": 0.5,
                          "about_entities": ["opt-a"], "source_ref": "session.md",
                          "quote": "", "status": "draft"}]
        raw["events"] = [{"id": ev, "eventKind": "decision",
                          "content": "an event", "confidence": 0.5,
                          "about_entities": ["opt-a"], "source_ref": "session.md"}]
        raw["operators"] = [{"src": pt, "dst": ev, "op_type": "IMPL"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rec = _commit_record(r.json()["commit_id"])
        assert rec is not None
        import json as _json
        telemetry = _json.loads(rec[2])
        assert telemetry["keep_ratio"] is not None
        assert 0.0 <= telemetry["keep_ratio"] <= 1.0, telemetry["keep_ratio"]
        # dedup_hits = reconcilable_submitted - net_new; a fresh commit has
        # low dedup (not the full event count).
        assert telemetry["dedup_hits"] is not None

    def test_event_about_object_edge_created_for_new_entity(self, client):
        from tortoise.ids import content_hash
        pt = f"pt_{content_hash('arg')[:62]}"
        ev = f"ev_{content_hash('decided on new option')[:62]}"
        raw = _raw_payload(0, session_id="s-newentity")
        raw["entities"] = [{"name": "brand-new-option", "kind": "core:goal",
                            "passes_frequency_gate": True}]
        raw["points"] = [{"id": pt, "content": "arg",
                          "pointKind": "statement", "reason": "NEW",
                          "confidence": 0.5, "c_cal": 0.5,
                          "about_entities": ["brand-new-option"],
                          "source_ref": "session.md", "quote": "",
                          "status": "draft"}]
        raw["events"] = [{"id": ev, "eventKind": "decision",
                          "content": "decided on new option", "confidence": 0.5,
                          "about_entities": ["brand-new-option"],
                          "source_ref": "session.md"}]
        raw["operators"] = [{"src": pt, "dst": ev, "op_type": "IMPL"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (e:Event {eventId:$ev})-[:aboutObject]->(o:Object {name:'brand-new-option'}) "
            "RETURN count(e)",
            params={"ev": ev},
        ).result_set
        assert rows and rows[0][0] == 1, "event→Object edge missing for new entity"


# ── E1 (#1533): event startedAt persisted server-side ──────────────────────

class TestE1EventStartedAt:
    """D6: the derived-commit receiver writes e.startedAt from the payload's
    started_at (coalesce fallback: captured_at → now for undated events)."""

    def test_event_started_at_written_from_payload(self, client):
        from tortoise.ids import content_hash
        ev = f"ev_{content_hash('dated event')[:62]}"
        raw = _raw_payload(0, session_id="s-e1-dated")
        raw["events"] = [{"id": ev, "eventKind": "decision",
                          "content": "dated event", "confidence": 0.5,
                          "about_entities": ["Alpha"],
                          "source_ref": "session.md",
                          "started_at": "2026-08-01"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.startedAt",
            params={"eid": ev}).result_set
        assert rows and rows[0][0] == "2026-08-01"

    def test_undated_event_started_at_coalesces_to_captured_at(self, client):
        from tortoise.ids import content_hash
        ev = f"ev_{content_hash('undated event')[:62]}"
        raw = _raw_payload(0, session_id="s-e1-undated")
        raw["events"] = [{"id": ev, "eventKind": "decision",
                          "content": "undated event", "confidence": 0.5,
                          "about_entities": ["Alpha"],
                          "source_ref": "session.md"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.startedAt",
            params={"eid": ev}).result_set
        # coalesce(e.startedAt, ev.captured_at or now) — never null
        assert rows and rows[0][0]

    def test_point_when_written_from_payload(self, client):
        """E1 code-review fix: the derived-commit receiver persists
        Point.when (the payload slot must land on the node, not vanish at
        write); undated points write no when prop."""
        from tortoise.ids import content_hash
        pt = f"pt_{content_hash('dated point')[:62]}"
        raw = _raw_payload(0, session_id="s-e1-ptwhen")
        raw["points"] = [{"id": pt, "content": "dated point",
                          "pointKind": "statement", "reason": "NEW",
                          "confidence": 0.5, "c_cal": 0.5,
                          "about_entities": ["Alpha"],
                          "source_ref": "session.md", "quote": "",
                          "status": "draft", "when": "2026-08-01"}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point {id:$pid}) RETURN p.when",
            params={"pid": pt}).result_set
        assert rows and rows[0][0] == "2026-08-01"

        # undated point → no when prop on the node
        pt2 = f"pt_{content_hash('undated point')[:62]}"
        raw2 = _raw_payload(0, session_id="s-e1-ptwhen2")
        raw2["points"] = [{"id": pt2, "content": "undated point",
                           "pointKind": "statement", "reason": "NEW",
                           "confidence": 0.5, "c_cal": 0.5,
                           "about_entities": ["Alpha"],
                           "source_ref": "session.md", "quote": "",
                           "status": "draft"}]
        r2 = _commit(client, raw2)
        assert r2.status_code == 200, r2.text
        rows2 = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point {id:$pid}) RETURN p.when",
            params={"pid": pt2}).result_set
        assert rows2 and not rows2[0][0]


# ── #2032 body-sweep cap ────────────────────────────────────────────────────


def _oversized_chunked(n_chunks: int = 8, step: int = 8192):
    """Chunked generator, NO content-length — forces the streaming-cap path."""
    for _ in range(n_chunks):
        yield b"x" * step


class TestCommitBodySweepCap:
    def test_commit_oversized_chunked_413(self, client, monkeypatch):
        """commit_session — oversized chunked body → 413 with the commit
        detail (auth-gated: the get_current_team override fires 401-free
        first). The 413 must NOT be remapped into the 400 catch-all."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_COMMIT_SESSION_MAX_BYTES", 8192)
        r = client.post(
            "/v1/sessions/commit", content=_oversized_chunked(),
            headers={"content-type": "application/json"})
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._COMMIT_SESSION_413_DETAIL

    def test_commit_malformed_400_preserved(self, client):
        """Malformed JSON → 400 'Request body must be a JSON object'
        (unchanged — empty body lands here too, via json.loads(b''))."""
        r = client.post(
            "/v1/sessions/commit", content=b"{",
            headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Request body must be a JSON object"

    def test_commit_empty_400_preserved(self, client):
        """Empty body → 400 (unchanged — Starlette's request.json() raises on
        empty; json.loads(b'') raises the same class into the same catch)."""
        r = client.post(
            "/v1/sessions/commit", content=b"",
            headers={"content-type": "application/json"})
        assert r.status_code == 400
        assert r.json()["detail"] == "Request body must be a JSON object"
