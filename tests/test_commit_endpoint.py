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
          sessions count = :Session nodes with the **41st commit LANDING**
          (the old "41st → 402" leg was the flat 1000 cap, reopened and
          superseded by #4010), Layer-1 400/422 (incl.
          commit_id_mismatch + calibration_mismatch + 51-point cap), 401,
          500 fail-closed
- DE2E-10 byte-level privacy (no raw conversation in payload/telemetry/graph;
          basename-only paths; telemetry schema has no text-bearing fields,
          no graph-side counts, no judge_summary)
- PL4     metering (write_ops +1 per non-duplicate; held bills 0 →
          write_ops_billed:0; nodes_written net-new)
- #4704   the PL3 remainder: a FAILED write's retry re-enters the write
          phase (L1 does not replay a `partial` record), and must neither
          re-apply the Session counters nor re-count the payload it already
          declared
- R-13    dedicated 300/min/key commit bucket, exempt from the 100/min key
          bucket
"""
from __future__ import annotations

import json
import logging
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# #67: TORTOISE_SECRET_PEPPER is mandatory for auth module — set before import
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Rate limiter trips 429 in full-suite runs — tests opt out; production keeps
# the limit (the R-13 bucket tests below exercise the middleware directly).
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import require_answered, verdict_client
from tortoise.commit_schema import (
    compute_client_commit_id,
    point_content_id,
)
from tortoise.file_indexer import hash_text
from tortoise.hosted_api import app, get_current_org
from tortoise.ids import content_hash
from tortoise.sdk import TortoiseSDK

# ── Test constants ───────────────────────────────────────────────────────────

TEST_ORG_ID = "team-001"  # epic #1647 (T7): a TEAM id, not a test namespace — a "test-" prefix would trip the SDK's hyphenated test-* normalization (sdk.py) and map the team graph to test_team_001_tortoise while org_graph_name resolves team_team-001 (backup dump divergence)
TEST_TEAM = {
    "org_id": TEST_ORG_ID,
    "key_id": "test-key-001",
    # C5 #2114 (#2260): legacy tt_ class — scope-less key_id dicts 403 the
    # data-plane gates otherwise (mirrors the #2241 migration pattern).
    "legacy_full_access": True,
    "tier": "free",
    # get_current_org always resolves the full limits dict — test stubs must
    # match, or fail-closed quota enforcement 500s instead of passing (#310).
    "max_users": 1,
    "max_graphs": 1,
    "max_points": 10000,
    "max_api_keys": 2,
    "max_sessions": None,
}


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    """TestClient with auth override and a temp FalkorDBLite DB.

    All /v1/* endpoints receive TEST_TEAM as the authenticated team; every
    TortoiseSDK instance (tenant + registry) shares the same temp DB.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_org] = lambda: dict(TEST_TEAM)
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch/_restore_tortoise_sdk_init copies).
        with patched_tortoise_sdk(db_path), verdict_client(app) as tc:
            yield tc


@pytest.fixture
def client_no_auth():
    """TestClient WITHOUT auth override — exercises the real 401 path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        with patched_tortoise_sdk(db_path), verdict_client(app) as tc:
            yield tc


def _team_sdk() -> TortoiseSDK:
    """Fresh tenant SDK on the shared test DB (post-request read surface)."""
    import tortoise.hosted_api as ha_mod
    return ha_mod._make_sdk(namespace=TEST_ORG_ID)


def _reg_sdk():
    """Registry SDK — MeteringRecord reads (PL4 assertions)."""
    import tortoise.hosted_api as ha_mod
    return ha_mod._make_sdk(namespace="registry")


def _metering_rows():
    rows = _reg_sdk()._get_registry().query(
        "MATCH (m:MeteringRecord {org_id:$tid}) "
        "RETURN m.write_ops, m.nodes_written",
        params={"tid": TEST_ORG_ID},
    ).result_set
    return (int(rows[0][0]), int(rows[0][1])) if rows else (0, 0)


def _commit_record(cid: str):
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (r:CommitRecord {client_commit_id:$cid}) "
        "RETURN r.status, r.write_ops_billed, r.telemetry, r.budget_warn",
        params={"cid": cid},
    ).result_set
    return rows[0] if rows else None


def _commit_record_declared(cid: str):
    """``value_nodes_declared`` on the :CommitRecord (#4704), or None.

    Kept out of ``_commit_record`` on purpose: that helper's 4-tuple is
    unpacked by existing tests.
    """
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (r:CommitRecord {client_commit_id:$cid}) "
        "RETURN r.value_nodes_declared",
        params={"cid": cid},
    ).result_set
    return rows[0][0] if rows else None


def _session_counter(session_id: str, field: str):
    rows = _team_sdk()._get_proj().g.query(
        f"MATCH (s:Session {{id:$sid}}) RETURN coalesce(s.{field}, 0)",
        params={"sid": session_id},
    ).result_set
    return int(rows[0][0]) if rows else 0


def _session_source_rows():
    """Every agentSession Source in the tenant graph as (url, contentHash).

    Queried by ``sourceKind`` rather than by a hard-coded url so a test can
    observe the identity the write path actually minted (#4005) — the
    pre-fix basename url and the post-fix canonical ``session:<id>`` are both
    visible here.
    """
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (s:Source {sourceKind:'agentSession'}) "
        "RETURN s.url, s.contentHash",
    ).result_set
    return [(r[0], r[1]) for r in rows]


def _session_source_meta(url: str):
    """(contentHash, version, sourcePath) of one Source, or None."""
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (s:Source {url:$url}) "
        "RETURN s.contentHash, s.version, s.sourcePath",
        params={"url": url},
    ).result_set
    return rows[0] if rows else None


def _all_source_urls():
    rows = _team_sdk()._get_proj().g.query(
        "MATCH (s:Source) RETURN s.url",
    ).result_set
    return sorted(r[0] for r in rows)


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
        "provenance_refs": [{"path": "session.md", "spans": ["0-10"],
                             "contentHash": hash_text("raw session transcript")}],
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
    """Finalize + POST /v1/sessions/commit.

    #5049: the route is not exempt from the transport wait bound, so a request
    that outlives it is answered with the legible 504 refusal rather than an
    answer. `require_answered` turns that non-answer into an INCONCLUSIVE skip
    naming the bound; every other response — including the 4xx refusals this
    file's callers assert on — is returned unchanged.
    """
    r = client.post("/v1/sessions/commit", json=_finalize(raw))
    return require_answered(r, what="POST /v1/sessions/commit answered")


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


# ── #2193 Test6bEntitySupersessionGuards fixtures — the entity supersession
# lane driven through POST /v1/sessions/commit (seeding + fold commits) ─────

def _seed_objects(client, session_id: str, names: list[str]) -> None:
    """Prior-commit seeding of LIVE Objects by name — the superseded side
    must pre-exist the supersession commit (the commit under test only
    carries the supersession records). The names ride payload.entities and
    `create_entity` mints an OPAQUE id for each — since #3590 it is NOT
    derived from the name: `ids.entity_name_id` is gone, `ids.ulid()` is the
    mint, and the `obj-<sha26(name)>` spelling survives only in
    `legacy_ids._derived_legacy_handle` (an offline legacy-key normaliser,
    never an entity create path) — so nothing here may assert on the id's
    spelling. One net-new point keeps the payload non-empty. Must 200 — a
    failed seed would invalidate the guard under test."""
    raw = _raw_payload(1, session_id=session_id, points=[
        {"id": f"pt_{content_hash('seed:' + session_id)}",
         "content": f"seed point {session_id}",
         "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
         "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
         "quote": "", "status": "live"},
    ], entities=[
        {"name": n, "kind": "Project", "passes_frequency_gate": True}
        for n in names
    ])
    r = _commit(client, raw)
    assert r.status_code == 200, r.text


def _supersede_commit(client, session_id: str, ref: str, sby: str, *,
                      evidence: str, entities: list[str] | None = None):
    """A commit whose ONLY supersession work is one entity record (ref →
    sby). The successor rides payload.entities when given (the step-6 entity
    write lands it on the graph before the §6b fold) — pass [] when the
    successor must stay OUT of the payload (dangling or already-terminal
    successors)."""
    raw = _raw_payload(1, session_id=session_id, points=[
        {"id": f"pt_{content_hash('supersede:' + session_id)}",
         "content": f"supersede point {session_id}",
         "pointKind": "decision", "reason": "NEW", "confidence": 0.9,
         "c_cal": 0.8, "about_entities": [], "source_ref": "session.md",
         "quote": "", "status": "live"},
    ], entities=[
        {"name": n, "kind": "Project", "passes_frequency_gate": True}
        for n in (entities or [])
    ], supersessions=[
        {"superseded": ref, "supersedes_by": sby, "evidence": evidence},
    ])
    return _commit(client, raw)


def _supersede_chain_commit(client, session_id: str, chain: list[tuple[str, str]],
                            *, evidence: str):
    """#2249: a commit whose ONLY supersession work is a same-payload CHAIN
    (reverse-emission shape — the payload asserts B→C before A→B). Rides a
    two-record supersessions list; the test seeds successors first via
    _seed_objects (mirroring the guard (a)–(h) style)."""
    raw = _raw_payload(1, session_id=session_id, supersessions=[
        {"superseded": ref, "supersedes_by": sby, "evidence": evidence}
        for ref, sby in chain
    ])
    return _commit(client, raw)

def _object_row(name: str):
    """Graph read of every Object carrier named *name* — (status,
    supersededBy, id) per row. Returns ALL carriers (the duplicate-name
    guard asserts on both)."""
    return _team_sdk()._get_proj().g.query(
        "MATCH (o:Object {name:$n}) RETURN o.status, o.supersededBy, o.id",
        params={"n": name}).result_set


# ── DE2E-2 — four-node chain + discoverability + entities + operators ──────

class TestFourNodeChain:
    def test_commit_works_when_recording_disabled(self, client):
        """#1927 / #1910: commit_session needs NO session_recording gate —
        the off-switch lives in _capture_session_impl only. A team with the
        flag explicitly False can still POST /v1/sessions/commit (the
        derived-commit receiver never consults it)."""
        import tortoise.hosted_api as ha_mod
        ha_mod._update_onboarding_state(TEST_ORG_ID, session_recording=False)
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

        # Document transcript — D10: a :Source keyed url=doc_<hash>
        # (summary/sessionId/sourcePath; NO content/doc_status).
        # #5082: the document's TYPE axis (`sourceKind='document'`) sits beside
        # the `documentKind` genre — pre-fix this writer left it NULL while
        # §4.6 marks it REQUIRED on every `:Source`.
        # #10000: `story_arc` is ASSERTED ABSENT, not present. §9.1/D1 places the
        # S1 narrative in Postgres ("not in the graph, not a `Document` node, not
        # a `Point`"), and this writer used to SET it on the node — prose in the
        # RAM-resident layer that `STORAGE-ARCHITECTURE.md` §2.1 says can never
        # unload, with no production reader. The narrative's home is
        # `public.session_narrative` (`tests/test_9555_narrative_carrier.py`);
        # this pin is what reds if it comes back onto a node.
        rows = g.query(
            "MATCH (s:Source) WHERE s.sessionId='s1' "
            "AND s.documentKind IS NOT NULL "
            "RETURN s.documentKind, s.summary, s.story_arc, s.sourcePath, "
            "s.is_episodic, s.sourceKind",
        ).result_set
        assert rows, "Document Source missing"
        kind, summary, arc, srcpath, episodic, skind = rows[0]
        assert kind == "transcript"
        assert summary == "summary text"
        assert arc is None, (
            "the S1 narrative must NOT be a property of the transcript :Source "
            "(§9.1/D1, #10000) — it is stored in public.session_narrative"
        )
        assert srcpath == "session.md"  # basename only (privacy)
        assert episodic is True
        assert skind == "document", (
            "the /v1/sessions transcript :Source must carry "
            "sourceKind='document' (#5082) — a document is a :Source (§4.4) "
            "and §4.6 marks sourceKind REQUIRED on every :Source"
        )

        # The AgentSession Event carries the same rule (#10000): identity +
        # metadata (`summary`), never the narrative prose.
        rows = g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.story_arc, e.summary",
            params={"eid": eid},
        ).result_set
        assert rows[0][0] is None, (
            "the S1 narrative must NOT be a property of the AgentSession :Event "
            "(§9.1/D1, #10000)"
        )
        assert rows[0][1] == "summary text"

        # (Event)-[:produces]->(document Source)
        n = g.query(
            "MATCH (e:Event {eventId:$eid})-[:produces]->(d:Source) "
            "WHERE d.sessionId='s1' RETURN count(d)",
            params={"eid": eid},
        ).result_set[0][0]
        assert n >= 1

        # Source bridge (sourceKind agentSession, contentHash, provenance_spans)
        rows = g.query(
            "MATCH (s:Source {url:'session:s1'}) RETURN s.sourceKind, "
            "s.contentHash, s.provenance_spans, s.is_episodic",
        ).result_set
        assert rows and rows[0][0] == "agentSession"
        assert rows[0][1] and rows[0][2] == ["0-10"]

        # (document Source)<-[:references]-(session Source)
        n = g.query(
            "MATCH (s:Source {url:'session:s1'})-[:references]->(d:Source) "
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
            "-[:extractedFrom]->(s:Source {url:'session:s1'}) RETURN count(s)",
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

    def test_entity_commit_does_not_reset_a_stored_discriminator(self, client):
        """#9572 (review round 4, P1): the PRODUCTION capture door.

        `TortoiseSDK.commit_session` POSTs the v2 payload here, and
        `extractor_v2` repairs every unclassifiable entity to `core:other`. This
        door wrote that repair as the entity's `objectKind`, so a bare re-commit
        of a session that merely MENTIONED an Object reset its stored kind.

        Pinned at the ROUTE on purpose: a helper-level pin is exactly how this
        twin survived four review rounds.
        """
        sdk = _team_sdk()
        sdk.create_object("ProbeCommitZ", "capability")
        r = _commit(client, _raw_payload(1, entities=[
            {"name": "Alpha", "kind": "Project",
             "passes_frequency_gate": True},
            {"name": "ProbeCommitZ", "kind": "core:other",
             "passes_frequency_gate": True},
        ]))
        assert r.status_code == 200, r.text
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (o:Object {name:'ProbeCommitZ'}) RETURN o.objectKind",
        ).result_set
        assert rows and rows[0][0] == "capability", (
            f"the commit door wrote the classifier's repair as the kind: {rows}")

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
            "MATCH (a:Source {url:'session:s1'})-[:references]->"
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

    def test_anchorless_recommit_preserves_stored_external_anchor(self, client):
        """#4146: an anchorless re-commit of an external ``sources[]`` url must
        NOT wipe its stored ``contentHash`` or bump ``version``.

        This is the exact defect #4005 fixed for the SESSION Source (see
        ``TestSessionSourceIndexIdentity``), still live in the external loop
        immediately beside it: ``contentHash=src.contentHash or ""`` turned the
        back-compat NULL into ``""``, so ``_upsert_source``'s ON MATCH took the
        OVERWRITE branch — the preserve branch fires only WHEN ``$hash IS
        NULL``, and ``s.contentHash <> $hash`` is TRUE for any stored non-empty
        hash. Red before the fix: the anchor is wiped and the version bumped.
        """
        url = "https://example.com/pricing"
        anchor = hash_text("pricing v1")
        raw = _raw_payload(1, summary="first capture", sources=[
            {"sourceKind": "document", "url": url,
             "credibilityTier": "T1", "contentHash": anchor},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        meta = _session_source_meta(url)
        assert meta is not None, "the external Source was never created"
        assert meta[0] == anchor, meta
        version = meta[1]

        # back-compat client: same url, no contentHash at all, different
        # summary so this is a real write (not an L1 replay)
        raw = _raw_payload(1, summary="second capture", sources=[
            {"sourceKind": "document", "url": url, "credibilityTier": "T1"},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        assert r.json().get("duplicate") is not True, (
            "the second commit was deduped as an L1 replay — not a real write"
        )
        meta = _session_source_meta(url)
        assert meta[0] == anchor, (
            f"an anchorless re-commit wiped the stored external contentHash: {meta[0]!r}"
        )
        assert meta[1] == version, (
            f"an anchorless re-commit bumped the version: {meta[1]} != {version}"
        )

    def test_empty_string_anchor_is_treated_as_absent(self, client):
        """#4146: an EMPTY-STRING anchor is an absent anchor, not a hash.

        The field admits ``""`` (``contentHash: str | None`` with no
        ``min_length``), so a client can send ``contentHash: ""`` and mean
        "I have no anchor". A bare pass-through would let that wipe the stored
        hash exactly like the bug; this pins the ``or None`` normalization.
        """
        url = "https://example.com/empty-anchor"
        anchor = hash_text("v1")
        raw = _raw_payload(1, summary="first capture", sources=[
            {"sourceKind": "document", "url": url, "contentHash": anchor},
        ])
        assert _commit(client, raw).status_code == 200
        version = _session_source_meta(url)[1]

        raw = _raw_payload(1, summary="second capture", sources=[
            {"sourceKind": "document", "url": url, "contentHash": ""},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        assert r.json().get("duplicate") is not True, (
            "the second commit was deduped as an L1 replay — not a real write"
        )
        meta = _session_source_meta(url)
        assert meta[0] == anchor, (
            f"an EMPTY-STRING anchor wiped the stored contentHash: {meta[0]!r}"
        )
        assert meta[1] == version, (
            f"an EMPTY-STRING anchor bumped the version: {meta[1]} != {version}"
        )

    def test_anchored_recommit_still_updates_external_anchor(self, client):
        """#4146: a re-commit carrying a NEW anchor must still update the stored
        hash and bump the version.

        The fix makes an ABSENT anchor preserve; it must never make a PRESENT
        one inert. Measured: mutating the fix to an unconditional
        ``contentHash=None`` turns this test, both preservation tests and
        ``test_sources_external_chain`` red.
        """
        url = "https://example.com/pricing"
        a1, a2 = hash_text("pricing v1"), hash_text("pricing v2")
        raw = _raw_payload(1, summary="first capture", sources=[
            {"sourceKind": "document", "url": url, "contentHash": a1},
        ])
        assert _commit(client, raw).status_code == 200
        assert _session_source_meta(url)[0] == a1
        v1 = _session_source_meta(url)[1]

        raw = _raw_payload(1, summary="second capture", sources=[
            {"sourceKind": "document", "url": url, "contentHash": a2},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        meta = _session_source_meta(url)
        assert meta[0] == a2, (
            f"a NEW anchor did not update the stored hash: {meta[0]!r}"
        )
        assert meta[1] == v1 + 1, (
            f"a NEW anchor did not bump the version: {meta[1]} != {v1 + 1}"
        )


# ── #4005 — the hosted session Source is a real index entry ───────────────

class TestSessionSourceIndexIdentity:
    """#4005 — identity + integrity of the hosted session ``:Source``.

    Pre-fix the commit path minted it with ``url = os.path.basename(ref.path)``
    and ``contentHash = content_hash(url)``: two raw files sharing a basename
    on two machines COLLIDED on the single ``MERGE (s:Source {url:$url})``
    key, and the stored hash could not detect that the raw had changed,
    existed, or was absent. The identity is now the canonical
    ``session:<session_id>`` (ONTOLOGY §4.6) — the SAME url the capture path
    materializes and ``delete_session`` deletes — with the W-7 basename as a
    property. Each behaviour below was RED before the fix.
    """

    def test_distinct_sessions_sharing_a_basename_get_distinct_urls(self, client):
        """(i) two sessions whose raw file shares the basename ``session.md``
        get DISTINCT Source urls (pre-fix both collapsed onto ``session.md``).

        This is the two-machines case as far as identity can distinguish it:
        the collision domain is the session id. Two machines that resolve the
        SAME session id still share one Source — the limitation is pinned by
        the next test."""
        for sid in ("machine-a", "machine-b"):
            raw = _raw_payload(1, session_id=sid)
            raw["provenance_refs"] = [{"path": "session.md", "spans": []}]
            r = _commit(client, raw)
            assert r.status_code == 200, r.text
        urls = sorted(u for u, _ in _session_source_rows())
        assert urls == ["session:machine-a", "session:machine-b"], urls

    def test_same_session_id_is_one_identity_pinned_limitation(self, client):
        """The limitation, pinned explicitly rather than implied-fixed: the
        identity is the SESSION, not the basename. Two raws that resolve the
        SAME session id (e.g. two machines both falling back to
        ``derive_session_id`` -> ``file_<stem>``) address ONE Source."""
        for summary in ("first raw", "different raw"):
            raw = _raw_payload(1, summary=summary)
            raw["provenance_refs"] = [{"path": "session.md", "spans": []}]
            r = _commit(client, raw)
            assert r.status_code == 200, r.text
        assert [u for u, _ in _session_source_rows()] == ["session:s1"]

    def test_content_hash_is_of_raw_not_of_url(self, client):
        """(ii) ``contentHash`` is the RAW's anchor, never ``hash(url)``.

        (a) with no client anchor the server stores NO anchor — it must not
        fabricate ``content_hash(url)``; (b) a supplied non-empty raw anchor
        is stored verbatim and differs from ``content_hash(url)``.
        """
        raw = _raw_payload(1)
        # legacy/back-compat client: basename-only ref, no raw anchor
        raw["provenance_refs"] = [{"path": "session.md", "spans": ["0-10"]}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rows = _session_source_rows()
        assert len(rows) == 1, rows
        url, stored = rows[0]
        assert url == "session:s1", rows
        assert not stored, stored
        assert stored != content_hash(url), (
            "contentHash is a hash of the Source url, not of the raw"
        )

        raw_text = "the raw session transcript: non-empty bytes"
        raw = _raw_payload(1, summary="second capture")
        raw["provenance_refs"] = [{"path": "session.md", "spans": [],
                                   "contentHash": hash_text(raw_text)}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        rows = _session_source_rows()
        assert len(rows) == 1, rows
        url, stored = rows[0]
        assert stored == hash_text(raw_text)
        assert stored != content_hash(url)

    def test_anchor_stable_for_same_raw_changes_for_new_raw(self, client):
        """(iii) the anchor is stable while the raw is unchanged and changes
        when the raw changes, on the SAME Source url.

        Every re-commit carries a DIFFERENT ``summary``, so it is a real write,
        not an L1 replay — the pre-fix test used byte-identical payloads that
        the server short-circuits with zero writes (a vacuous assertion,
        #4005 review)."""

        def _commit_raw(summary: str, raw_text: str):
            raw = _raw_payload(1, summary=summary)
            raw["provenance_refs"] = [{"path": "session.md", "spans": [],
                                       "contentHash": hash_text(raw_text)}]
            resp = _commit(client, raw)
            assert resp.status_code == 200, resp.text
            assert resp.json().get("duplicate") is not True
            rows = _session_source_rows()
            assert len(rows) == 1, rows
            return rows[0]

        url_a, stored_a = _commit_raw("summary A1", "raw A: first capture")
        assert url_a == "session:s1"
        assert stored_a == hash_text("raw A: first capture")
        v_a = _session_source_meta(url_a)[1]
        # SAME raw, re-committed → SAME url, SAME anchor, NO version bump
        url_a2, stored_a2 = _commit_raw("summary A2", "raw A: first capture")
        assert url_a2 == url_a and stored_a2 == stored_a
        assert _session_source_meta(url_a)[1] == v_a
        # the raw changed → SAME url, DIFFERENT anchor, version bump (the
        # contract create_source relies on)
        url_b, stored_b = _commit_raw("summary B", "raw B: the raw changed")
        assert url_b == url_a, (url_a, url_b)
        assert stored_b == hash_text("raw B: the raw changed")
        assert stored_b != stored_a
        assert _session_source_meta(url_a)[1] == v_a + 1

    def test_anchorless_recommit_preserves_stored_anchor(self, client):
        """#4005 review P1: an anchorless re-commit must NOT wipe the stored
        anchor or bump version.

        Pre-fix ``contentHash=ref.contentHash or ""`` turned the back-compat
        NULL into ``""``, so ``_upsert_source``'s conditional write took the
        OVERWRITE branch (the preserve branch fires only WHEN ``$hash IS
        NULL``) — the anchor was wiped and the version bumped."""
        anchor = hash_text("raw v1")
        raw = _raw_payload(1, summary="first capture")
        raw["provenance_refs"] = [{"path": "session.md", "spans": [],
                                   "contentHash": anchor}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        url, stored = _session_source_rows()[0]
        assert url == "session:s1" and stored == anchor
        version = _session_source_meta(url)[1]

        # back-compat client: no contentHash at all, different summary so this
        # is a real write (not an L1 replay)
        raw = _raw_payload(1, summary="second capture")
        raw["provenance_refs"] = [{"path": "session.md", "spans": []}]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        assert r.json().get("duplicate") is not True
        url2, stored2 = _session_source_rows()[0]
        assert url2 == url
        assert stored2 == anchor, (
            "an anchorless re-commit wiped the stored contentHash"
        )
        assert _session_source_meta(url2)[1] == version, (
            "an anchorless re-commit bumped the version"
        )

    def test_capture_commit_delete_agree_on_one_session_source(self, client):
        """#4005 review P1: capture, commit and delete all address the SAME
        ``:Source``.

        Pre-fix the capture path materialized ``session:s1`` while the commit
        path minted a basename node (``session.md``) — a SECOND agentSession
        Source that ``delete_session`` never deleted (orphan on delete). With
        one canonical identity the whole lifecycle is ONE node."""
        from tortoise.hosted_api import app, get_current_org_session_ungated
        app.dependency_overrides[get_current_org_session_ungated] = \
            lambda: dict(TEST_TEAM)
        try:
            # 1) capture path — the hosted capture endpoint calls this helper
            sdk = _team_sdk()
            sdk._materialize_session_source(
                "s1", None, "2026-08-11T10:00:00+00:00",
                [{"role": "user", "content": "the raw transcript"}])
            assert [u for u, _ in _session_source_rows()] == ["session:s1"]

            # 2) commit path — SAME identity, no second Source
            raw = _raw_payload(1)
            raw["provenance_refs"] = [{"path": "session.md", "spans": [],
                                       "contentHash": hash_text("raw v1")}]
            r = _commit(client, raw)
            assert r.status_code == 200, r.text
            rows = _session_source_rows()
            assert [u for u, _ in rows] == ["session:s1"], rows

            # 3) delete path — the canonical identity is what it removes
            rd = client.delete("/v1/sessions/s1")
            assert rd.status_code == 200, rd.text
            assert _session_source_rows() == []
        finally:
            app.dependency_overrides.pop(get_current_org_session_ungated, None)

    @pytest.mark.parametrize("path", [
        "session.md",
        "/Users/alice/notes/session.md",
        "notes\\session.md",
        "a/b/session.md",
    ])
    def test_layer1_accepted_source_ref_resolves_to_the_session_source(
            self, client, path):
        """Review P2: Layer-1 and the writer derive the basename through the
        ONE shared primitive, so any path Layer-1 accepts produces an
        ``extractedFrom`` that resolves to the session Source — never a
        bare-basename Source minted by the fallback."""
        raw = _raw_payload(1)
        raw["provenance_refs"] = [{"path": path, "spans": []}]
        raw["points"][0]["source_ref"] = "session.md"
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        n = g.query(
            "MATCH (p:Point {id:$pid})-[:extractedFrom]->(s:Source {url:'session:s1'}) "
            "RETURN count(s)",
            params={"pid": raw["points"][0]["id"]},
        ).result_set[0][0]
        assert n >= 1
        # D10: the commit mints a distinct document :Source (url=doc_<hash>)
        # alongside the canonical session Source. The assertion's intent is
        # that any Layer-1-accepted path still re-points extractedFrom at the
        # session Source — NO bare-basename Source (the fallback's
        # `session.md`) is ever minted.
        doc_url = f"doc_{content_hash('s1:2026-08-11T10:00:00Z')}"
        assert _all_source_urls() == sorted(["session:s1", doc_url]), \
            _all_source_urls()

    @pytest.mark.parametrize("path", [".", "..", "/", "a/.", "a/.."])
    def test_basename_less_provenance_path_422s_before_any_write(
            self, client, path):
        """Review P1/P2: a basename-less path used to pass Layer-1 and then
        either raise mid-write (a 500 after the Session/Document/Event landed)
        or mint a bare-basename Source. It must 422 BEFORE any write."""
        raw = _raw_payload(1)
        raw["provenance_refs"] = [{"path": path, "spans": []}]
        r = _commit(client, raw)
        assert r.status_code == 422, r.text
        assert _all_source_urls() == [], _all_source_urls()

    def test_blank_session_id_422s_before_any_write(self, client):
        """Review P1: ``session_id='   '`` passed ``min_length=1`` and then
        raised inside the write AFTER the Session counters, Document and Event
        were written — a redacted 500 with a non-converging retry."""
        raw = _raw_payload(1)
        raw["session_id"] = "   "
        r = _commit(client, raw)
        assert r.status_code == 422, r.text
        assert _all_source_urls() == [], _all_source_urls()

    def test_derivation_is_the_canonical_session_identity(self):
        """Derivation: the identity is the canonical ``session:<id>`` — the
        SAME url the capture path materializes and ``delete_session`` deletes —
        and it can NEVER collide with ``derive_source_url``'s ``corpus://``
        authority (review P1 #3)."""
        from tortoise.file_indexer import (
            derive_session_source_url,
            derive_source_content_hash,
            derive_source_url,
            provenance_basename,
        )

        assert derive_session_source_url("s1") == "session:s1"
        # the session id is the collision domain ...
        assert derive_session_source_url("m1") != derive_session_source_url("m2")
        # ... and a session id can never alias a corpus name: even an id equal
        # to the corpus name yields `session:`, never `corpus://<name>/...`
        assert derive_session_source_url("notes") != \
            derive_source_url("/root/notes/session.md", "/root/notes",
                              corpus_name="notes")
        # a blank session id is a hard error (Layer-1 422s first)
        for bad in ("", "   ", None):
            with pytest.raises(ValueError):
                derive_session_source_url(bad)
        # the ONE shared basename primitive (W-7 + Windows separators)
        assert provenance_basename("/Users/alice/notes/session.md") == "session.md"
        assert provenance_basename("notes\\session.md") == "session.md"
        assert provenance_basename("a/b/session.md") == "session.md"
        for basename_less in ("", "/", ".", "..", "a/.", "a/.."):
            assert provenance_basename(basename_less) == "", basename_less
        # the anchor is a hash of the raw's CRLF-normalized text, never the url
        assert derive_source_content_hash("") == ""
        assert derive_source_content_hash(None) == ""
        assert derive_source_content_hash("raw") == hash_text("raw")
        assert derive_source_content_hash("a\r\nb") == \
            derive_source_content_hash("a\nb")


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
        # #4937 INVARIANT GUARD (the regression pin for the refusal itself is
        # tests/test_sdk.py::test_mitigates_is_not_an_operator_kind): the
        # payload spelling MITIGATES never materializes a peer operator kind —
        # it attaches to the IMPL bridge above (mitigated_by), it is NOT a
        # generic operator of kind MITIGATES. Holds on main too, so it guards
        # the invariant rather than the #4937 diff.
        peer = g.query(
            "MATCH (o:Point {is_operator:true}) WHERE o.op_type = 'MITIGATES' "
            "RETURN count(o)").result_set
        assert peer[0][0] == 0

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


# ── #4970 — the resolved-id map on the hosted commit lane ─────────────────

class TestRekeyedPointResolvedIds:
    """#4970 — the hosted commit lane reproduced the #4716 two-id-space hole.

    A payload point whose CONTENT already exists in the graph under a
    DIFFERENT id re-keys inside ``create_point(..., dedup=True)`` (whose
    return value the §5 loop IGNORED), while every downstream reference kept
    the payload's ``pt_<sha>`` id. ``_load_commit_graph_state`` only loads
    ``p.id IN <payload ids>``, so the prior node is invisible to
    ``reconcile_payload`` and the point is marked ``action == "new"``; the
    ``pt_<sha>`` node is then never created.

    Six consumers read that payload id and every one dropped or mis-wrote
    its edge. This is the residual the #4716 fix left behind — its Part 1
    remap WAS scoped to the v2 CAPTURE commit; #4970 closed it by wiring the
    same shared helper on the hosted lane:

      1. §5's Session ``CONTAINS`` MERGE (the adjacent defect),
      2. §5's ``supersede_point(prior, pid)`` — the successor id was never
         minted, so the lifecycle guard raises and the commit fails closed,
      3. §6's ``aboutObject`` MERGE (no match ⇒ no edge, no error),
      4. §7's operator endpoints — ``create_operator`` raises and
         ``apply_payload_operators`` swallows it as
         ``operator write skipped (inputs missing?)``,
      5. §7's MITIGATES reason, resolved from the SAME ref after the remap
         (a graph id) while ``payload.points`` is keyed by payload id —
         hence the map-aware reverse resolver,
      6. §6b's ``supersedes_by`` successor ref (a ``pt_`` id BY
         construction).

    The fix is the general shape #4936/PR #5478 used: the writer RETURNS the
    id it actually used and downstream stamping keys on the RESOLVED graph
    id. Here §5 builds ``payload id -> resolved graph id`` and threads it
    through the SAME shared ``commit_ops.remap_*`` helpers the capture path
    uses (#4716) — no rival mechanism.

    Before/after (this fixture, pre-seeded legacy node holding "point 0"):

        before: IMPL edges [] · CONTAINS {pt_1...} · aboutObject {pt_1...}
                · warning "operator write skipped (inputs missing?)"
        after:  IMPL edges [(legacy, pt_1...)] · CONTAINS {legacy, pt_1...}
                · aboutObject {legacy, pt_1...} · no warning
    """

    def test_rekey_keeps_operator_contains_and_about_object(self, client, caplog):
        # The graph pre-exists holding the payload point's CONTENT under a
        # legacy (auto-minted ULID) id — the re-key source.
        legacy_id = _team_sdk().create_point("decision", "point 0")["id"]
        assert legacy_id and not legacy_id.startswith("pt_"), legacy_id

        p0 = f"pt_{'0' * 64}"   # payload id — re-keys onto legacy_id
        p1 = f"pt_{'1' * 64}"   # genuinely new
        raw = _raw_payload(2, points=[
            _point(0, id=p0, content="point 0", about_entities=["Alpha"]),
            _point(1, id=p1, content="point 1", about_entities=["Alpha"]),
        ], operators=[
            {"src": p0, "dst": p1, "op_type": "IMPL",
             "direction": "unidirectional"},
        ])
        with caplog.at_level(logging.WARNING, logger="tortoise.commit_ops"):
            r = _commit(client, raw)
        assert r.status_code == 200, r.text
        # (3) the operator edge was NOT silently dropped
        assert "operator write skipped" not in caplog.text, caplog.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:0}]->(s) "
            "MATCH (o)-[:IMPL {idx:1}]->(d) "
            "RETURN s.id, d.id",
        ).result_set
        assert [tuple(r) for r in rows] == [(legacy_id, p1)], rows
        # (1) Session CONTAINS reaches BOTH points, the re-keyed one included
        contained = {row[0] for row in g.query(
            "MATCH (:Session {id:'s1'})-[:CONTAINS]->(p:Point) RETURN p.id",
        ).result_set}
        assert {legacy_id, p1} <= contained, contained
        # (2) §6 aboutObject keys on the resolved id too
        about = {row[0] for row in g.query(
            "MATCH (p:Point)-[:aboutObject]->(:Object {name:'Alpha'}) "
            "RETURN p.id",
        ).result_set}
        assert {legacy_id, p1} <= about, about
        # the payload id names NO node — no phantom was minted (the re-key is
        # real; the fix must not create a second point)
        assert g.query("MATCH (p:Point {id:$id}) RETURN count(p)",
                       params={"id": p0}).result_set[0][0] == 0

    def test_rekey_keeps_the_supersession_successor_ref(self, client):
        """§6b — ``supersedes_by`` is a payload ``pt_<sha>`` id BY
        construction, so it shares the two-id-space hole: the successor point
        re-keyed onto a pre-existing node under another id and
        ``sdk.supersede(prior, '<payload id>')`` targeted a node that does not
        exist — it RAISED and ``apply_supersessions`` swallowed it as
        ``point supersede '<prior>' → '<payload id>' failed`` (not the
        ``… ref '<payload id>' not found`` skip, which fires only when the
        already-graph-id ``superseded`` side is absent) — so the CORRECTS fold
        was lost. The §6b call now remaps
        through the same map the operators use (capture-path parity), while
        ``superseded`` — already a real graph id, and the record's lane
        discriminator — is untouched."""
        old_id = f"pt_{content_hash('gym at 6pm')}"
        assert _commit(client, _raw_payload(1, points=[
            {"id": old_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])).status_code == 200

        # the successor CONTENT pre-exists under a legacy id → the new
        # ``pt_<sha>`` payload id re-keys onto it in §5
        new_id = point_content_id("gym at 5pm")
        legacy_id = _team_sdk().create_point("decision", "gym at 5pm")["id"]
        assert not legacy_id.startswith("pt_"), legacy_id

        r = _commit(client, _raw_payload(1, session_id="s2", points=[
            {"id": new_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ], supersessions=[
            {"superseded": old_id, "supersedes_by": new_id,
             "evidence": "fact-value contradiction (later session value "
                         "change)"},
        ]))
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        n = g.query(
            "MATCH (new:Point {id:$new})-[:CORRECTS]->(old:Point {id:$old}) "
            "RETURN count(old)",
            params={"new": legacy_id, "old": old_id}).result_set[0][0]
        assert n == 1, f"CORRECTS fold lost on the re-keyed successor ({n})"

    def test_rekey_resolves_the_mitigation_reason_content(self, client):
        """§7's MITIGATES reason is resolved from the SAME ref the remap
        rewrites: after ``remap_operator_endpoint_refs`` that ref is a GRAPH
        id, while ``payload.points`` is keyed by PAYLOAD id — so a re-keyed
        dampener's reason degraded to its bare graph id (the #4716 review P1
        on the capture path, reproduced here). §7 hands the helper a
        map-aware resolver (the reverse map) so the payload content still
        resolves."""
        legacy_id = _team_sdk().create_point("decision", "point 0")["id"]
        assert not legacy_id.startswith("pt_"), legacy_id
        p0 = f"pt_{'0' * 64}"   # payload id — re-keys onto legacy_id
        p1 = f"pt_{'1' * 64}"   # genuinely new
        raw = _raw_payload(2, points=[
            _point(0, id=p0, content="point 0"),
            _point(1, id=p1, content="point 1"),
        ], operators=[
            {"src": p0, "dst": p1, "op_type": "IMPL",
             "direction": "unidirectional"},
            # the dampener IS the re-keyed point
            {"src": p0, "dst": p1, "op_type": "MITIGATES",
             "target": {"src": p0, "dst": p1, "op_type": "IMPL"},
             "strength": 0.4},
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (op:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (op)-[:IMPL {idx:0}]->(:Point {id:$legacy}) "
            "MATCH (op)-[:IMPL {idx:1}]->(:Point {id:$p1}) "
            "MATCH (op)-[:mitigated_by]->(m:Point) "
            "RETURN m.content",
            params={"legacy": legacy_id, "p1": p1},
        ).result_set
        assert rows, "mitigation artifact missing on the re-keyed dampener"
        content = rows[0][0]
        # the reason is the PAYLOAD point's content (wrapped by
        # mitigate_operator's display prefix) — never the bare resolved id
        assert "point 0" in content, content
        assert legacy_id not in content, content

    def test_rekey_supersede_uses_the_resolved_successor_id(self, client):
        """§5's supersede branch passed the never-minted ``supersede_id`` to
        ``supersede_point`` when ``create_point`` re-keyed the successor onto
        a pre-existing node — the lifecycle guard then raised and the whole
        commit failed closed. The branch now supersedes the PRIOR with the
        RESOLVED successor id."""
        old_id = point_content_id("gym at 6pm")
        assert _commit(client, _raw_payload(1, points=[
            {"id": old_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])).status_code == 200
        # the successor CONTENT pre-exists under a legacy id → the recomputed
        # ``supersede_id`` re-keys onto it in §5
        legacy_id = _team_sdk().create_point("decision", "gym at 5pm")["id"]
        assert not legacy_id.startswith("pt_"), legacy_id
        r = _commit(client, _raw_payload(1, session_id="s2", points=[
            {"id": old_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ]))
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        n = g.query(
            "MATCH (new:Point {id:$new})-[:CORRECTS]->(old:Point {id:$old}) "
            "RETURN count(old)",
            params={"new": legacy_id, "old": old_id}).result_set[0][0]
        assert n == 1, f"CORRECTS fold lost on the re-keyed successor ({n})"
        # the recomputed successor id names NO node — no phantom was minted
        assert g.query("MATCH (p:Point {id:$id}) RETURN count(p)",
                       params={"id": point_content_id("gym at 5pm")}
                       ).result_set[0][0] == 0

    def test_supersede_operator_ref_follows_the_successor(self, client):
        """On the ``supersede`` reconcile action the payload point id IS the
        PRIOR node's graph id, and §5 keys it to the RESOLVED successor — so a
        payload operator ref naming that point lands on the SUCCESSOR. That is
        deliberate and consistent with ``supersede_point``'s own edge transfer
        (an operator edge on a superseded point belongs to its successor);
        pinned here so the semantics are not incidental. This holds on the
        ordinary (non-re-keyed) supersede path too, which is why it needs its
        own test."""
        prior_id = point_content_id("gym at 6pm")
        assert _commit(client, _raw_payload(1, points=[
            {"id": prior_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])).status_code == 200
        successor_id = point_content_id("gym at 5pm")
        p1 = f"pt_{'1' * 64}"
        r = _commit(client, _raw_payload(2, session_id="s2", points=[
            {"id": prior_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
            _point(1, id=p1, content="point 1"),
        ], operators=[
            {"src": prior_id, "dst": p1, "op_type": "IMPL",
             "direction": "unidirectional"},
        ]))
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (o:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (o)-[:IMPL {idx:0}]->(s) "
            "MATCH (o)-[:IMPL {idx:1}]->(d) "
            "RETURN s.id, d.id",
        ).result_set
        assert [tuple(r) for r in rows] == [(successor_id, p1)], rows
        # the prior is terminal and carries no operator endpoint
        assert g.query("MATCH (p:Point {id:$id}) RETURN p.status",
                       params={"id": prior_id}).result_set[0][0] == "superseded"

    def test_supersede_mitigation_reason_is_the_payload_content(self, client):
        """§5 keys the map by PAYLOAD point id ONLY (one id space), so the
        reverse-map winner is a real ``payload.points`` entry and the MITIGATES
        reason resolves to the payload CONTENT rather than degrading to the
        bare graph id (#4716 review P1). On the supersede path §5's map value
        for ``pr.point.id`` IS the resolved successor, and the reverse lookup
        returns the payload id — never ``supersede_id``, which has no
        ``payload.points`` entry."""
        prior_id = point_content_id("gym at 6pm")
        assert _commit(client, _raw_payload(1, points=[
            {"id": prior_id, "content": "gym at 6pm", "pointKind": "decision",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ])).status_code == 200
        successor_id = point_content_id("gym at 5pm")
        p1 = f"pt_{'1' * 64}"
        r = _commit(client, _raw_payload(2, session_id="s2", points=[
            {"id": prior_id, "content": "gym at 5pm", "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
            _point(1, id=p1, content="point 1"),
        ], operators=[
            {"src": prior_id, "dst": p1, "op_type": "IMPL",
             "direction": "unidirectional"},
            {"src": prior_id, "dst": p1, "op_type": "MITIGATES",
             "target": {"src": prior_id, "dst": p1, "op_type": "IMPL"},
             "strength": 0.4},
        ]))
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        rows = g.query(
            "MATCH (op:Point {is_operator:true, op_type:'IMPL'}) "
            "MATCH (op)-[:IMPL {idx:0}]->(:Point {id:$succ}) "
            "MATCH (op)-[:IMPL {idx:1}]->(:Point {id:$p1}) "
            "MATCH (op)-[:mitigated_by]->(m:Point) "
            "RETURN m.content",
            params={"succ": successor_id, "p1": p1},
        ).result_set
        assert rows, "mitigation artifact missing on the superseded dampener"
        content = rows[0][0]
        assert "gym at 5pm" in content, content
        # never the bare graph id (neither the prior nor the successor)
        assert successor_id not in content and prior_id not in content, content

    def test_payload_id_and_supersede_id_do_not_share_one_key_space(
            self, client):
        """#4970 review P2 — the §5 map is keyed by PAYLOAD point id ONLY.

        ``point_content_id`` hashes CONTENT ONLY, while dedup matches
        content+kind (#784), so two records in ONE payload can collide across
        id spaces: a ``new`` point whose payload id is
        ``pt_<hash("shared text")>`` and a ``supersede`` record whose NEW
        content is that same text share the latter's server-recomputed
        ``supersede_id``. Keying the map by ``supersede_id`` as well let the
        second record OVERWRITE the first record's payload-id entry (last
        writer wins), silently re-pointing the first record's aboutObject
        edge at the SECOND record's successor. Here the first point dedup-hits
        a pre-existing legacy node while the second's successor is minted at
        the shared content-addressed id, so the two resolutions DIFFER and the
        mis-route is observable rather than self-cancelling."""
        shared = "shared text"
        shared_id = point_content_id(shared)
        # Pre-existing OBSERVATION holding the shared content under a legacy
        # id: record A's write dedup-hits it, so A resolves to the legacy node
        # (never to ``shared_id``).
        legacy_observation = _team_sdk().create_point(
            "observation", shared)["id"]
        assert not legacy_observation.startswith("pt_"), legacy_observation
        # Pre-existing DECISION to supersede, seeded at its own
        # content-addressed id: the payload point below reuses that id with
        # changed content, so reconcile takes the supersede path.
        prior_old_id = point_content_id("old claim")
        prior_decision = _team_sdk().create_point(
            "decision", "old claim", id=prior_old_id)["id"]
        assert prior_decision == prior_old_id

        r = _commit(client, _raw_payload(
            2, session_id="s1",
            entities=[
                {"name": "Alpha", "kind": "Project",
                 "passes_frequency_gate": True},
                {"name": "Beta", "kind": "Project",
                 "passes_frequency_gate": True},
            ],
            points=[
            {"id": shared_id, "content": shared, "pointKind": "observation",
             "reason": "NEW", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Alpha"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
            {"id": prior_old_id, "content": shared, "pointKind": "decision",
             "reason": "REVISES", "confidence": 0.9, "c_cal": 0.8,
             "about_entities": ["Beta"], "source_ref": "session.md",
             "quote": "", "status": "draft"},
        ]))
        assert r.status_code == 200, r.text

        g = _team_sdk()._get_proj().g
        # A's aboutObject edge belongs on the legacy observation it RESOLVED
        # to — the pre-fix map had ``shared_id`` overwritten by B and would
        # route it onto B's successor node.
        rows = g.query(
            "MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object) "
            "RETURN collect(o.name)",
            params={"pid": legacy_observation},
        ).result_set
        assert rows and sorted(rows[0][0]) == ["Alpha"], rows
        # B's successor — minted at the shared content-addressed id — carries
        # B's own edge, and NOT A's.
        rows = g.query(
            "MATCH (p:Point {id:$pid})-[:aboutObject]->(o:Object) "
            "RETURN collect(o.name)",
            params={"pid": shared_id},
        ).result_set
        assert rows and sorted(rows[0][0]) == ["Beta"], rows


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
            "MATCH (new:Point {id:$new})-[:extractedFrom]->(s:Source {url:'session:s1'}) "
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


class TestFailedWriteRetry4704:
    """#4704 — the SEQUENTIAL twin of the #3718 double-apply bug.

    Both requests carry the SAME ``client_commit_id`` but they do NOT overlap:
    the first FAILS mid-write and returns the fail-closed 500 whose contract
    is "retry with the same client_commit_id" (its Session counters have
    already landed — they are step 1 of the write phase); the second arrives
    after the winner released, sees a ``partial`` record, which L1 does not
    replay, and re-runs the write phase. Unguarded that phase re-applied
    ``value_nodes_created += net_new`` and ``commit_count +1`` for ONE logical
    payload, and `value_nodes_created` is the budget numerator
    (`adjudicate_budget`'s ``prior_created``) — so the session's spend was
    inflated for every LATER commit. The retry's own adjudication was
    inflated too: ``reconcile`` sees only the REMAINDER, and adding it to a
    prior that already contained the whole payload is what the WARN assertion
    below pins.

    Mutation proof: drop the ``:CommitRecord`` guard from step 1 of
    ``_execute_commit_writes`` and the counters read 24 / 2 (not 15 / 1);
    keep the guard but adjudicate the retry on ``reconcile.net_new`` and the
    response's ``warn`` is True where the clean attempt warned nothing.
    """

    _SESSION = "s-4704"
    _PRIOR = 6
    # _raw_payload(8) = 8 new points + the new ("Alpha", "Project") entity
    _NET_NEW = 9

    def _fail_the_first_point_write(self, monkeypatch) -> dict:
        """Make the FIRST payload point write raise — i.e. fail the phase
        AFTER step 1 (the counters) landed, which is the whole window this
        bug needs. Every later call delegates to the real writer."""
        real = TortoiseSDK.create_point
        calls = {"n": 0}

        def _boom_once(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated transient graph error")
            return real(self, *args, **kwargs)

        monkeypatch.setattr(TortoiseSDK, "create_point", _boom_once)
        return calls

    def test_failed_write_retry_applies_the_counters_once(self, client,
                                                          monkeypatch):
        _inject_session_state(self._SESSION, value_nodes_created=self._PRIOR)
        raw = _finalize(_raw_payload(8, session_id=self._SESSION))
        calls = self._fail_the_first_point_write(monkeypatch)

        first = require_answered(
            client.post("/v1/sessions/commit", json=raw),
            what="POST /v1/sessions/commit answered")
        assert first.status_code == 500, first.text
        assert calls["n"] == 1
        # the counters landed BEFORE the failure; the record stays `partial`
        assert _session_counter(self._SESSION, "value_nodes_created") == (
            self._PRIOR + self._NET_NEW)
        assert _session_counter(self._SESSION, "commit_count") == 1
        rec = _commit_record(raw["client_commit_id"])
        assert rec is not None and rec[0] == "partial"
        assert _commit_record_declared(raw["client_commit_id"]) == self._NET_NEW

        second = require_answered(
            client.post("/v1/sessions/commit", json=raw),
            what="POST /v1/sessions/commit answered")
        assert second.status_code == 200, (
            f"the retry must complete the payload: got {second.status_code} "
            f"{second.text}")
        body = second.json()
        assert body["duplicate"] is False and body["held"] == []
        assert body["nodes_created"] == self._NET_NEW
        # the retry's own adjudication is the clean attempt's (prior 6 + 9 =
        # 15): no WARN. Re-counting the remainder (15 + 9 = 24) warns here.
        assert body["warn"] is False, body

        # ONE logical payload ⇒ the counters moved ONCE
        assert _session_counter(self._SESSION, "value_nodes_created") == (
            self._PRIOR + self._NET_NEW)
        assert _session_counter(self._SESSION, "commit_count") == 1

        # ...and the retry really did write the remainder
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point {session_id:$sid}) RETURN count(p)",
            params={"sid": self._SESSION}).result_set
        assert rows[0][0] == 8

        # one logical payload ⇒ one write-op, its net-new metered once
        assert _metering_rows() == (1, self._NET_NEW)
        rec = _commit_record(raw["client_commit_id"])
        assert rec is not None and rec[0] == "fully_written" and rec[1] == 1
        assert rec[3] is False  # budget_warn — the clean verdict, not the retry's

        # the fix must not disturb L1: a further replay is still a duplicate
        third = require_answered(
            client.post("/v1/sessions/commit", json=raw),
            what="POST /v1/sessions/commit answered")
        assert third.status_code == 200 and third.json()["duplicate"] is True
        assert _session_counter(self._SESSION, "value_nodes_created") == (
            self._PRIOR + self._NET_NEW)
        assert _session_counter(self._SESSION, "commit_count") == 1
        assert _metering_rows() == (1, self._NET_NEW)


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


# ── #5363 — the #4021 inverted-window refusal on the hosted COMMIT path ─────
#
# The refusal is a DETERMINISTIC payload error (the same payload re-raises), so
# the fail-closed 500's "retry with the same client_commit_id" advice is wrong
# for it — and because `_execute_commit_writes` created the successor BEFORE
# calling `supersede_point`, the refusal also left a live orphan successor that
# no retry could fix. These tests pin both halves: the 422 mapping (A) and the
# per-action check that keeps the refusal from MINTING the successor it would
# orphan (B) — scoped to that successor, not to the whole request: the chain
# writes and any earlier point of the commit have already landed by then.

_PREDECESSOR_ID = "pt_0000000000000000000000000000000000000000000000000000000000000000"
_SUPERSEDED_CONTENT = "the old 5K claim"
_REVISING_CONTENT = "my 5K best is 27:12"
_PREDECESSOR_VALID_FROM = "2026-08-01T00:00:00+00:00"
_REVISING_WHEN = "2026-06-01T00:00:00+00:00"


def _seed_inverted_window_predecessor() -> str:
    """Seed the §5 supersede PREDECESSOR — the payload point id, live, non-
    operator, carrying a stored window START the revising payload's `when`
    precedes. A direct seed (conftest convention, cf. the E3 supersede test)
    rather than a prior commit, so the stored `validFrom` is exact."""
    _team_sdk()._get_proj().g.query(
        "MERGE (p:Point {id:$id}) SET p.pointKind='statement', "
        "    p.content=$c, p.is_operator=false, p.status='live', "
        "    p.content_hash='seed', p.validFrom=$vf",
        params={"id": _PREDECESSOR_ID, "c": _SUPERSEDED_CONTENT,
                "vf": _PREDECESSOR_VALID_FROM})
    return _PREDECESSOR_ID


def _inverted_window_commit(client):
    """Commit the same payload point id with DIFFERENT content and a `when`
    that precedes the seeded predecessor's `validFrom` — the §5 reconcile
    action is `supersede`, the one `_execute_commit_writes` takes."""
    return _commit(client, _raw_payload(1, points=[_point(
        0, content=_REVISING_CONTENT, when=_REVISING_WHEN)]))


class Test5363InvertedSupersedeWindow:
    """#5363 — the #4021 refusal mapped to an actionable 422 and raised before
    the point's successor is minted (so the refusal leaves no orphan successor,
    the partial write a retry could never complete)."""

    def test_inverted_window_422s_before_the_successor_is_minted(self, client):
        """(B) + (A) together, on the real write path: the commit 422s naming
        the conflict, the successor is NEVER minted, and the predecessor is
        left untouched. Without the fix this is a 500 over an orphan successor.

        Fails without the fix: the status is 500 (not 422) AND the successor
        node exists (the `succ == []` assertion). The final forward-window leg
        is the over-fix guard — it passes before and after, and exists so a
        pre-check that refuses EVERY supersede reds this test rather than
        shipping."""
        old_pid = _seed_inverted_window_predecessor()
        r = _inverted_window_commit(client)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "supersede_window_inverted"
        # actionable: the conflicting ids AND the two window bounds it refuses
        assert detail["superseded"] == old_pid
        assert detail["supersedes_by"] == point_content_id(_REVISING_CONTENT)
        assert detail["successor_validFrom"] == _REVISING_WHEN
        assert detail["predecessor_validFrom"] == _PREDECESSOR_VALID_FROM
        assert "inverted window" in detail["errors"][0]
        # the retry-advising 500 detail must NOT ride this response (a
        # same-payload retry re-raises identically)
        assert "retry" not in json.dumps(detail).lower()

        g = _team_sdk()._get_proj().g
        # NO partial write: the successor was never created (the orphan)
        succ = g.query(
            "MATCH (p:Point) WHERE p.content = $c RETURN p.id",
            params={"c": _REVISING_CONTENT}).result_set
        assert succ == [], f"orphan successor minted before the refusal: {succ}"
        # NO partial write: the predecessor was not superseded or stamped
        old = g.query(
            "MATCH (p:Point {id:$id}) RETURN p.status, p.validTo",
            params={"id": old_pid}).result_set
        assert old and old[0][0] == "live", f"predecessor mutated: {old}"
        assert old[0][1] is None, f"predecessor validTo stamped: {old}"

        # Over-fix guard: a `when` ON/AFTER the predecessor's `validFrom` is a
        # legal window and must still commit (the check refuses the inversion,
        # not supersession).
        fwd = _commit(client, _raw_payload(1, points=[_point(
            0, content=_REVISING_CONTENT, when="2026-09-01T00:00:00+00:00")]))
        assert fwd.status_code == 200, fwd.text
        after = g.query("MATCH (p:Point {id:$id}) RETURN p.status",
                        params={"id": old_pid}).result_set
        assert after and after[0][0] == "superseded", \
            "the forward window did not take the supersede branch"

    def test_refusal_escaping_the_precheck_is_still_a_422(self, client,
                                                          monkeypatch):
        """(A) alone: the boundary of last resort. With the pre-write check
        disabled the #4021 refusal still reaches `supersede_point` after the
        successor is minted (a race/uncatalogued path) — it must map to the
        same 422, never the retry-advising 500.

        Fails without the fix: the status is 500, not 422."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_prevalidate_supersede_window",
                            lambda sdk, pr, *, now: None)
        _seed_inverted_window_predecessor()
        r = _inverted_window_commit(client)
        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "supersede_window_inverted"
        assert detail["superseded"] == _PREDECESSOR_ID
        assert "retry" not in json.dumps(detail).lower()

    def test_same_commit_dedup_sibling_is_never_a_false_422(self, client):
        """#7000 review P2 — a VALID commit must NOT be refused because the
        pre-check resolved the successor at T0 and could not see the
        same-commit sibling `create_point` dedups onto.

        Deterministic repro (no timing race): the predecessor's window starts
        in the FUTURE (`validFrom = now + 30d`); payload point A is `new` with
        content C and `when = now + 90d`; payload point B carries the
        predecessor id with the SAME content C and NO `when` (→ reconcile
        action `supersede`).

        On the write path A is minted first, then B re-keys onto A (same
        content+kind) and `supersede_point` reads A's stored `validFrom`
        (`now + 90d` ≥ the predecessor's `now + 30d`) → the window is legal.
        A T0 pre-check cannot see A, so it measured the successor's fallback
        start as the hosted `now` (< `now + 30d`) and refused a commit the
        writer accepts.

        Fails before the fix: `assert 422 == 200` (the refusal body names the
        hosted `now`, not A's window start)."""
        from datetime import UTC, datetime, timedelta
        _now = datetime.now(UTC)
        pred_vf = (_now + timedelta(days=30)).isoformat()
        sibling_when = (_now + timedelta(days=90)).isoformat()
        content = "the 5K claim, restated once"
        old_pid = _PREDECESSOR_ID
        _team_sdk()._get_proj().g.query(
            "MERGE (p:Point {id:$id}) SET p.pointKind='statement', "
            "    p.content=$c, p.is_operator=false, p.status='live', "
            "    p.content_hash='seed-dedup', p.validFrom=$vf",
            params={"id": old_pid, "c": _SUPERSEDED_CONTENT, "vf": pred_vf})

        raw = _raw_payload(0, points=[
            # A: NEW, content C, a future window start that LEGALLY follows the
            # predecessor's. Deliberately id 7 — NOT the predecessor id.
            _point(7, content=content, when=sibling_when),
            # B: the predecessor id + the SAME content → supersede, whose
            # successor (content C) already exists by the time B writes.
            _point(8, id=old_pid, content=content),
        ])
        r = _commit(client, raw)
        assert r.status_code == 200, r.text

        g = _team_sdk()._get_proj().g
        # the predecessor IS superseded, and its window ended at A's start
        old = g.query(
            "MATCH (p:Point {id:$id}) RETURN p.status, p.validTo",
            params={"id": old_pid}).result_set
        assert old and old[0][0] == "superseded", f"not superseded: {old}"
        assert old[0][1] == sibling_when, \
            f"validTo {old[0][1]!r} != successor window start {sibling_when!r}"
        # exactly ONE node carries the shared content — B re-keyed onto A
        # (a second node would mean the dedup path never folded the sibling)
        dup = g.query(
            "MATCH (p:Point) WHERE p.content = $c RETURN p.id",
            params={"c": content}).result_set
        assert len(dup) == 1, f"expected one deduped successor, got {dup}"


class Test6bEntitySupersessionGuards:
    """#2193 — the hosted §6b entity-supersession GUARD SET pinned through
    POST /v1/sessions/commit (assert graph outcomes + the ObjectSuperseded
    GraphEvent journal — NEVER response["warnings"], which is Layer-1 only
    and carries no §6b/helper warn signal):

    (a) happy-path anchor — a live target folds onto a live successor;
    (b) keep-first — a terminal target's supersededBy never blind-overwrites;
    (c) dedup — same-successor re-record with DIFFERENT evidence is NOT an L1
        replay (evidence rides the supersession canonical, so the ccid
        changes) and must journal exactly ONE ObjectSuperseded event;
    (d) never-guess — duplicate-name carriers (incl. a raw id-less legacy
        write) fold NEITHER;
    (e) self-alias (canonical id → own name) stays live;
    (f) dangling successor → target stays live;
    (g) the GraphEvent payload is the id-style shape incl. session_id;
    (h) existing-terminal successor → fold skipped, target stays live.

    Pre-#2193 the §6b inline consumer was BLIND: (b)-(h) fail against it
    (RED at base — the Task-2.4 commit evidence) and flip green only after
    the shared apply_supersessions migration (Task 4).
    """

    def test_happy_path_entity_fold(self, client):
        """(a) anchor — seed a live target; the fold commit supersedes it
        onto a live successor (rides payload.entities): target → superseded
        with supersededBy = the successor name; successor stays live."""
        _seed_objects(client, "g6a-seed", ["a-old"])
        r = _supersede_commit(client, "g6a-fold", "a-old", "a-new",
                              evidence="entity lifecycle supersedes",
                              entities=["a-new"])
        assert r.status_code == 200, r.text
        old = _object_row("a-old")
        assert old, old
        assert old[0][0] == "superseded", old
        assert old[0][1] == "a-new", old
        new = _object_row("a-new")
        assert new and new[0][0] == "live", new

    def test_divergent_successor_keep_first(self, client):
        """(b) keep-first — after A→B folded, a record A→C must NOT clobber:
        supersededBy STAYS B (the fold never blind-overwrites a terminal
        target); C (written by the second commit's step 6) stays live."""
        _seed_objects(client, "g6b-seed", ["b-old"])
        r1 = _supersede_commit(client, "g6b-c1", "b-old", "b-succ-1",
                               evidence="first successor wins",
                               entities=["b-succ-1"])
        assert r1.status_code == 200, r1.text
        r2 = _supersede_commit(client, "g6b-c2", "b-old", "b-succ-2",
                               evidence="divergent successor",
                               entities=["b-succ-2"])
        assert r2.status_code == 200, r2.text
        old = _object_row("b-old")
        assert old and old[0][0] == "superseded", old
        assert old[0][1] == "b-succ-1", \
            "terminal target must keep its FIRST successor (keep-first)"
        succ2 = _object_row("b-succ-2")
        assert succ2 and succ2[0][0] == "live", succ2

    def test_same_successor_rededup_single_journal(self, client):
        """(c) dedup — A→B twice with DIFFERENT evidence is NOT an L1 replay
        (evidence is part of the supersession canonical → distinct ccids), so
        the second fold commit really re-runs the consumer: it must dedup
        (same-successor idempotent no-op) and journal exactly ONE
        ObjectSuperseded GraphEvent."""
        _seed_objects(client, "g6c-seed", ["c-old"])
        r1 = _supersede_commit(client, "g6c-c1", "c-old", "c-new",
                               evidence="session-1 evidence",
                               entities=["c-new"])
        assert r1.status_code == 200, r1.text
        r2 = _supersede_commit(client, "g6c-c2", "c-old", "c-new",
                               evidence="session-2 evidence (re-derived)",
                               entities=["c-new"])
        assert r2.status_code == 200, r2.text
        assert r2.json()["duplicate"] is False, \
            "different evidence must NOT replay at L1 (the ccid differs)"
        g = _team_sdk()._get_proj().g
        n = g.query(
            "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) RETURN count(e)",
        ).result_set[0][0]
        assert n == 1, f"exactly ONE fold journal expected, got {n}"
        old = _object_row("c-old")
        assert old and old[0][0] == "superseded" and old[0][1] == "c-new", \
            old

    def test_duplicate_name_never_guess(self, client):
        """(d) never-guess — TWO carriers share the ref name: the canonical
        obj-<sha> carrier from a prior commit PLUS a raw id-less legacy write
        (CREATE {name, status:'live'} — create_entity/commits would MERGE by
        name and collapse the dup, making this vacuous). A name ref is
        ambiguous → NEITHER carrier folds (both stay live)."""
        _seed_objects(client, "g6d-seed", ["d-old", "d-new"])
        _team_sdk()._get_proj().g.query(
            "CREATE (o:Object {name:$n, status:'live'})",
            params={"n": "d-old"})
        assert len(_object_row("d-old")) == 2, \
            "canonical + raw carriers must both be present"
        r = _supersede_commit(client, "g6d-fold", "d-old", "d-new",
                              evidence="ambiguous name ref",
                              entities=[])
        assert r.status_code == 200, r.text
        rows = _object_row("d-old")
        assert len(rows) == 2, rows
        assert [row[0] for row in rows] == ["live", "live"], \
            f"duplicate-name ref must fold NEITHER carrier: {rows!r}"

    def test_self_supersession_id_name_alias_stays_live(self, client):
        """(e) self-alias — superseded = the canonical id, supersedes_by =
        the SAME Object's own name: every successor candidate IS the target
        (id/name alias) → skip; the Object stays live (a self-fold would
        erase it from recall's default view)."""
        _seed_objects(client, "g6e-seed", ["e-old"])
        oid = _object_row("e-old")[0][2]
        # ⛔ #3590: the create mints an OPAQUE id (`ids.ulid()`), so nothing here
        # may assert the retired `obj-<sha26(name)>` spelling — `_seed_objects`'
        # own docstring states the same rule. What the guard needs is that the
        # reference IS the alias (an id addressing the Object whose NAME is the
        # successor string); asserting the two differ keeps the fixture from
        # degenerating into the same-string case, where every successor
        # candidate trivially equals the target for a reason that has nothing
        # to do with the id/name alias arm under test.
        assert oid and isinstance(oid, str), oid
        assert oid != "e-old", oid
        r = _supersede_commit(client, "g6e-fold", oid, "e-old",
                              evidence="self-reference via id/name alias",
                              entities=[])
        assert r.status_code == 200, r.text
        old = _object_row("e-old")
        assert old and old[0][0] == "live", \
            f"self-supersession must NOT fold the target: {old!r}"

    def test_dangling_successor_skipped(self, client):
        """(f) dangling successor — the claimed successor was never written
        (not in the payload, not in the graph): folding would leave NO
        visible successor for recall → the target stays live (skip)."""
        _seed_objects(client, "g6f-seed", ["f-old"])
        r = _supersede_commit(client, "g6f-fold", "f-old", "f-missing",
                              evidence="dangling successor",
                              entities=[])
        assert r.status_code == 200, r.text
        old = _object_row("f-old")
        assert old and old[0][0] == "live", \
            f"dangling successor must NOT fold the target: {old!r}"

    def test_entity_fold_journals_session_id_in_graph_event(self, client):
        """(g) C2 journaling — the ObjectSuperseded GraphEvent payload is the
        id-style shape {id, name, supersedes_by, session_id, evidence} (full
        provenance); session_id == the committing session. Pre-#2193 §6b
        emitted a positional payload WITHOUT session_id (the phase-2 C2 gap)
        — this fails at base on the KEYSET (4 keys, no session_id), not a
        KeyError."""
        _seed_objects(client, "g6g-seed", ["g-old"])
        sess = "g6g-commit"
        r = _supersede_commit(client, sess, "g-old", "g-new",
                              evidence="fold evidence string",
                              entities=["g-new"])
        assert r.status_code == 200, r.text
        g = _team_sdk()._get_proj().g
        events = g.query(
            "MATCH (e:GraphEvent {type:'ObjectSuperseded'}) "
            "RETURN e.payload ORDER BY e.seq",
        ).result_set
        assert len(events) == 1, events
        payload = json.loads(events[0][0])
        assert set(payload) == {"id", "name", "supersedes_by",
                                "session_id", "evidence"}, \
            f"id-style payload keyset expected (5 keys incl. session_id), " \
            f"got {sorted(payload)}"
        assert payload["session_id"] == sess, payload
        assert payload["name"] == "g-old", payload
        assert payload["supersedes_by"] == "g-new", payload
        assert payload["evidence"] == "fold evidence string", payload
        oid = _object_row("g-old")[0][2]
        assert payload["id"] == oid, payload

    def test_existing_terminal_successor_skips_fold(self, client):
        """(h) visible-successor gate — after B→C folded (B terminal), a
        record A→B must NOT fold A: the only successor candidate under that
        name is terminal (recall-excluded) → no visible successor exists →
        A STAYS LIVE (B stays terminal, C stays live)."""
        _seed_objects(client, "g6h-seed", ["h-a", "h-b", "h-c"])
        r1 = _supersede_commit(client, "g6h-c1", "h-b", "h-c",
                               evidence="fold b onto c",
                               entities=["h-c"])
        assert r1.status_code == 200, r1.text
        r2 = _supersede_commit(client, "g6h-c2", "h-a", "h-b",
                               evidence="record a onto terminal b",
                               entities=[])
        assert r2.status_code == 200, r2.text
        a = _object_row("h-a")
        assert a and a[0][0] == "live", \
            f"fold onto a terminal successor must be SKIPPED: {a!r}"
        b = _object_row("h-b")
        assert b and b[0][0] == "superseded" and b[0][1] == "h-c", b
        c = _object_row("h-c")
        assert c and c[0][0] == "live", c



class Test6bSameCommitChain:
    """(i) #2249 hosted e2e — same-commit CHAINS driven through POST
    /v1/sessions/commit (reverse-emission payloads)."""
    def test_reverse_chain_folds_to_literal_end_state(self, client):
        """(i) #2249 hosted e2e — a same-commit chain emitted REVERSE
        ([B→C, A→B]) must converge to the payload-literal end state: A
        supersededBy h-b, B supersededBy h-c, C live. Pre-fix A stayed live
        (its fold gate-skipped a successor the same payload terminalized)."""
        _seed_objects(client, "g6i-seed", ["h-a", "h-b", "h-c"])
        r = _supersede_chain_commit(
            client, "g6i-c1", [("h-b", "h-c"), ("h-a", "h-b")],
            evidence="reverse chain")
        assert r.status_code == 200, r.text
        a = _object_row("h-a")
        assert a and a[0][0] == "superseded" and a[0][1] == "h-b", a
        b = _object_row("h-b")
        assert b and b[0][0] == "superseded" and b[0][1] == "h-c", b
        c = _object_row("h-c")
        assert c and (c[0][0] or "live") == "live" and c[0][1] is None, c


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

    def test_sessions_count_and_41st_commit_lands(self, client):
        """#947 P0 (preserved): `_count_resource('sessions')` returns the
        Session-node count, NOT the all-nodes count. #4010: the 41st commit
        LANDS — sessions have no cap (the old fixture's direct
        `max_sessions=40` write is no longer honoured as a cap)."""
        from tortoise.quota import count_org_usage
        sdk = _team_sdk()
        for i in range(40):
            raw = _raw_payload(1, session_id=f"qs{i}")
            r = _commit(client, raw)
            assert r.status_code == 200, f"commit {i} failed: {r.text}"
        assert count_org_usage(TEST_ORG_ID, "sessions", sdk=sdk) == 40
        # 41st commit is STORED (was a 402 before #4010).
        r = _commit(client, _raw_payload(1, session_id="qs40"))
        assert r.status_code == 200, r.text
        assert count_org_usage(TEST_ORG_ID, "sessions", sdk=sdk) == 41

    def test_empty_commit_ok_zero_budget(self, client):
        """An empty derived commit is valid: 200, zero budget burn,
        commit_count +1 (DE2E-8)."""
        r = _commit(client, _raw_payload(0, points=[], entities=[],
                                         provenance_refs=[]))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["nodes_created"] == 0 and body["held"] == []
        assert _session_counter("s1", "commit_count") == 1


class TestCommitPointsGate:
    """#4051 — the commit lane's pre-write org `max_points` gate.

    Until #4010 the flat `max_sessions` bounded this lane's TOTAL node growth;
    sessions are now unlimited for every tier, and `_count_resource("points")`
    excludes the chain this lane mints (:Session, :Event, transcript :Source).
    A free-tier key could therefore loop POST /v1/sessions/commit with a fresh
    session_id — even `points: []` — and mint nodes no quota counted.
    """

    @staticmethod
    def _set_max_points(limit: int) -> None:
        app.dependency_overrides[get_current_org] = lambda: {
            **TEST_TEAM, "max_points": limit}

    @staticmethod
    def _set_limits(**overrides) -> None:
        app.dependency_overrides[get_current_org] = lambda: {
            **TEST_TEAM, **overrides}

    @staticmethod
    def _seed_points(n: int) -> None:
        for i in range(n):
            _team_sdk()._get_proj().g.query(
                "MERGE (p:Point {id:$pid}) "
                "SET p.content='seed', p.is_episodic=false",
                params={"pid": f"pt_seed_{i}"},
            )

    @staticmethod
    def _point_count() -> int:
        rows = _team_sdk()._get_proj().g.query(
            "MATCH (p:Point) RETURN count(p)").result_set
        return int(rows[0][0])

    @staticmethod
    def _chain_counts(session_id: str) -> tuple[int, int, int]:
        """(:Session, :Event, transcript :Source) minted for a session.

        Counts by `sessionId` for Event/Source — the transcript :Source MERGE
        is keyed by `url`, but stamps `s.sessionId` (D10: a document is a
        :Source; the :Document label is retired).
        """
        g = _team_sdk()._get_proj().g
        sess = g.query("MATCH (s:Session {id:$sid}) RETURN count(s)",
                       params={"sid": session_id}).result_set[0][0]
        ev = g.query("MATCH (e:Event {sessionId:$sid}) RETURN count(e)",
                     params={"sid": session_id}).result_set[0][0]
        src = g.query("MATCH (s:Source {sessionId:$sid}) RETURN count(s)",
                      params={"sid": session_id}).result_set[0][0]
        return int(sess), int(ev), int(src)

    def test_session_cap_is_not_the_org_limit_gate(self, client):
        """#4051: the SESSION cap must not be consulted as a stand-in for the
        org limit — a `max_sessions` an org could never satisfy must not refuse.

        This is the removal's pin. Before it, the lane called
        `_check_org_limit(org, "sessions")` ahead of the points gate: with
        `max_sessions: 0` (a value that WOULD refuse) and existing Sessions, the
        commit 402'd on a limit that #4010 made unenforceable — a guard reading
        a number that cannot bind. Mutation REDs this: re-add that call.
        """
        self._set_limits(max_points=10000, max_sessions=0)
        for i in range(3):
            _team_sdk()._get_proj().g.query(
                "MERGE (s:Session {id:$sid}) SET s.is_episodic=true",
                params={"sid": f"sProxyCap{i}"},
            )
        r = _commit(client, _raw_payload(0, session_id="sProxyCap", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 200, (
            "a session cap refused a commit — the removed proxy is back: "
            f"{r.text}")
        assert self._chain_counts("sProxyCap") == (1, 1, 1)

    def test_unreadable_max_points_refuses_and_mints_nothing(self, client):
        """#4051: the org limit is read from its OWN source, and a value that
        cannot be READ is refused — a miss is never a pass.

        Scope, stated precisely: this drives the GATE's input through a
        dependency-overridden limits dict. That override is a TEST SEAM — it
        exercises the gate's refusal contract independently of what any one
        production lane happens to put in the dict, which is the property that
        keeps the contract from depending on a lane's resolver.
        WHICH lanes arrive raw and which die earlier is stated ONCE, in
        `quota.enforce_org_limit`'s SCOPE note — not restated here (copies
        of that inventory drifted apart inside this change; a copy claiming the
        `/v1/index/docs` documents gate arrives raw survived three review rounds,
        and it is false: that lane is handled by the DERIVED branch, and
        `resolve_org_limits` coerces `int(mp)` before any gate). It pins the
        gate's refusal contract — name the field, write nothing — and NOT an
        end-to-end corrupt-column path on this lane: the commit lane's own
        dependency (`get_current_org_gated`) coerces `max_points` with `int(mp)`
        first, so a corrupt STORED value here fails earlier, in auth, as an
        unnamed 500. That resolver gap is tracked as #10136.

        Before the gate validated its input the same dict escaped as a bare
        `TypeError` — refused by accident, naming nothing, and outside the
        `QuotaCheckError` contract every caller handles. Mutation REDs this:
        delete the `int(limit)` validation in `quota.enforce_org_limit`.
        """
        self._set_limits(max_points="unreadable")
        r = _commit(client, _raw_payload(0, session_id="sUnreadable", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 500, (
            f"an unreadable org limit was not refused: {r.status_code} {r.text}")
        detail = str(r.json()["detail"])
        assert "max_points" in detail and "unreadable" in detail, (
            f"the refusal must NAME the unreadable surface, got: {detail}")
        assert self._chain_counts("sUnreadable") == (0, 0, 0)

    def test_missing_max_points_refuses_and_mints_nothing(self, client):
        """#4051: the presence contract RIDES on `max_points` now, so a limits
        dict that OMITS the key must still refuse — and name it.

        Removing `_check_org_limit(org, "sessions")` removed this lane's OTHER
        presence check: it 500'd a limits dict with no `max_sessions` key
        (#310 GAP-B). The removal is only honest while the surviving check is
        pinned HERE — otherwise a later edit that made the points gate skip a
        MISSING `max_points` ("a miss is never a pass") would leave this lane
        with no fail-closed org gate at all, and every other test on the lane
        would stay green (they all supply the key). Mutation REDs this: make
        the missing-key branch in `quota.enforce_org_limit` `return` instead of
        raise — the commit then 200s and mints the chain.

        ⚠️ IT DOES NOT DISCRIMINATE THIS CHANGE, and says so rather than letting
        a green run imply it: for a limits dict with no `max_points` at all, the
        PRE-change tree also 500s naming `max_points` (the old
        `_check_org_limit(org, "sessions")` ran first and `enforce_org_limit`'s
        missing-key raise fires on whichever limit is asked for). Measured, not
        assumed: revert both source files to `origin/main` and this test still
        PASSES while
        `test_session_cap_is_not_the_org_limit_gate` and
        `test_unreadable_max_points_refuses_and_mints_nothing` both RED. It is a
        guard-rail against a FUTURE edit, not evidence for this one.
        """
        limits = dict(TEST_TEAM)
        del limits["max_points"]
        app.dependency_overrides[get_current_org] = lambda: limits
        r = _commit(client, _raw_payload(0, session_id="sMissingMaxPoints",
                                         points=[], entities=[],
                                         provenance_refs=[]))
        assert r.status_code == 500, (
            "a limits dict with no max_points key passed the gate: "
            f"{r.status_code} {r.text}")
        detail = str(r.json()["detail"])
        assert "max_points" in detail, (
            f"the refusal must NAME the missing field, got: {detail}")
        assert self._chain_counts("sMissingMaxPoints") == (0, 0, 0), (
            "the refused commit minted nodes")

    def test_at_max_points_refused_and_mints_nothing(self, client):
        """#4051: with the org AT max_points, a `points: []` commit — the
        cheapest possible loop iteration — 402s and mints no
        Session/Event/transcript-Source/Point node."""
        self._set_max_points(1)
        self._seed_points(1)  # count == limit → the boundary is inclusive
        before = self._point_count()
        r = _commit(client, _raw_payload(0, session_id="sGateAt", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 402, r.text
        detail = r.json()["detail"]
        # Same structured refusal the capture/points lanes raise (#4614).
        assert detail["code"] == "quota_exceeded"
        assert detail["resource"] == "points"
        assert detail["limit"] == 1
        assert detail["used"] == 1
        assert self._chain_counts("sGateAt") == (0, 0, 0), (
            "a refused commit must mint no Session/Event/Source — NOT 'nothing': "
            "a :CommitRecord with status='partial' IS merged before the gate "
            "(`store.acquire(..., status='partial')`, hosted_api.py:17865, which "
            "runs ahead of the gate at :17968); replay-safe, and invisible to "
            "_chain_counts by design")
        assert self._point_count() == before  # no partial Point either

    def test_over_max_points_refused_and_mints_nothing(self, client):
        """#4051: one over the limit (count > max_points) refuses too."""
        self._set_max_points(1)
        self._seed_points(3)
        r = _commit(client, _raw_payload(0, session_id="sGateOver", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 402, r.text
        assert r.json()["detail"]["resource"] == "points"
        assert self._chain_counts("sGateOver") == (0, 0, 0)

    def test_count_error_fails_closed(self, client, monkeypatch):
        """#4051: a COUNTING FAILURE must never be a silent pass.

        `_check_org_limit` documents "counting errors raise HTTP 500
        (QuotaCheckError) — never a silent pass", and that is the property the
        new gate site must inherit.

        ⛔ IT MUST DISCRIMINATE THE POINTS SITE FROM ANY OTHER `_check_org_limit`
        SITE in the handler. The patch below raises for `resource == "points"`
        only, so every other resource-gate call passes and the 500 can come from
        the points site alone. (When this test was written the lane still called
        `_check_org_limit(org, "sessions")` first — a patch that failed EVERY
        resource made the sessions gate raise the 500 and the request never
        reached the points site, so that first cut pinned nothing and stayed
        GREEN under mutation. #4051 has since removed the sessions call; the
        resource filter is what keeps this test measuring the site it names.)
        """
        import tortoise.quota as quota_mod
        from tortoise.quota import QuotaCheckError

        # Patch the seam the gate CALLS, not the gate itself: `_check_org_limit`
        # imports `enforce_org_limit` locally and catches QuotaCheckError around
        # it to raise HTTP 500. Patching `_check_org_limit` instead would bypass
        # that handler and the exception would escape the TestClient (which
        # re-raises server exceptions) — proving nothing.
        def _boom(org, resource, *, slot_credit=0, **kwargs):
            if resource != "points":
                return  # every other resource-gate site must PASS — see the docstring
            raise QuotaCheckError("count unavailable")

        monkeypatch.setattr(quota_mod, "enforce_org_limit", _boom)
        r = _commit(client, _raw_payload(1, session_id="sGateErr", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 500, r.text

    def test_one_under_max_points_still_commits(self, client):
        """#4051 boundary: at max_points - 1 the same payload lands — a normal
        commit is untouched by the gate (regression)."""
        self._set_max_points(1)
        # count == 0 == limit - 1 → admitted
        r = _commit(client, _raw_payload(0, session_id="sGateUnder", points=[],
                                         entities=[], provenance_refs=[]))
        assert r.status_code == 200, r.text
        assert r.json()["nodes_created"] == 0
        sess, ev, src = self._chain_counts("sGateUnder")
        assert (sess, ev, src) == (1, 1, 1)

    def test_replayed_commit_not_re_gated(self, client):
        """#4051 replay interaction: a fully_written re-POST returns 200
        duplicate even when the org is AT max_points. The gate sits after
        every replay return, so a replay (zero writes) is never 402'd —
        #1727's lesson.

        The first commit is an empty payload so the test does NOT load the
        embedding model (which can exceed the 10s transport bound in a
        targeted run). The cap is then reached by a direct Point seed.
        """
        self._set_max_points(1)
        raw = _raw_payload(0, session_id="sGateReplay", points=[],
                           entities=[], provenance_refs=[])
        r1 = _commit(client, raw)
        assert r1.status_code == 200, r1.text
        # push the org AT max_points AFTER the first commit landed
        self._seed_points(1)
        r2 = _commit(client, dict(raw))
        assert r2.status_code == 200, r2.text
        assert r2.json()["duplicate"] is True
        # the replay minted nothing beyond the first commit's chain
        assert self._chain_counts("sGateReplay") == (1, 1, 1)


class TestCommitOrgIngestBound:
    """#4051 §3.1 — the per-ORG ingest bound on the commit lane.

    What the classes above close, and what they do not: the payload's
    per-request SHAPE is bounded (MAX_EVENTS/MAX_SOURCES/MAX_PROVENANCE_REFS,
    #9425) and the lane carries the same pre-write `max_points` gate every
    other write endpoint has (#5637). Neither bounds the TOTAL: a
    `points: []` commit mints :Session + transcript :Source + AgentSession
    :Event, `_count_resource("points")` matches none of them, so the count
    never moves and that gate can never trip. MEASURED on the pre-fix code:
    a free-tier key looping /v1/sessions/commit with a fresh `session_id`
    mints unbudgeted nodes forever.

    This class pins the part of #4051 that is NOT decision-blocked — its own
    §3.1 (the triage comment on the issue names it "adoptable, not done"): a
    per-ORG, per-minute ingest allowance denominated in the STORED BYTES a
    request will add — the unit the owner ruled for storage (#4495/#5331,
    MB/GB not nodes) — refused with 429 + Retry-After. It is a THROTTLE
    (bounds the rate), not a cap (bounds the total) — the total bound is §4
    and turns on `max_graph_nodes` vs the recorded "storage is free" position
    (#4495/#5331), an owner ruling this lane does not take.

    Why the charge is not `nodes_written`: the PL4 metering figure
    (`plan.reconcile.net_new`) is points+entities+operators only, so a
    payload whose `events[]`/`sources[]` are large while its points all
    reconcile as merges meters 0 while still minting :Event/:Source nodes —
    the budget would be bypassable by the very hole it bounds.
    """

    @staticmethod
    def _allow(monkeypatch, charge_bytes: int) -> None:
        """Set the per-minute BYTE allowance and start from an empty window.

        The window is PROCESS state keyed by org_id (every test in this file
        authenticates as `team-001`), so it is reset rather than assumed — see
        `tests/conftest.py::_fresh_commit_ingest_budget`.
        """
        import tortoise.quota as quota_mod
        monkeypatch.setattr(
            quota_mod, "MAX_COMMIT_INGEST_BYTES_PER_MIN_PER_ORG", charge_bytes)
        quota_mod._reset_commit_ingest_budget_for_tests()

    @staticmethod
    def _charge(**overrides) -> int:
        """The bytes one payload is charged — the unit the budget compares.

        Read from the SAME function the endpoint calls, so a test cannot pin a
        charge the product does not compute.
        """
        raw = _raw_payload(0, points=[], entities=[], provenance_refs=[])
        raw.update(overrides)
        return TestCommitOrgIngestBound._charge_of(raw)

    @staticmethod
    def _charge_of(raw: dict) -> int:
        """The charge of an arbitrary raw payload dict (as the endpoint sees it)."""
        from tortoise.commit_schema import (
            CommitPayload,
            commit_ingest_charge_bytes,
        )
        payload = dict(raw)
        payload["client_commit_id"] = "0" * 64
        return commit_ingest_charge_bytes(CommitPayload(**payload))

    @staticmethod
    def _empty_commit(client, session_id: str):
        """The cheapest loop iteration: `points: []` — the `:CommitRecord`
        anchor, the `:Session`, the transcript `:Source` and the AgentSession
        `:Event`, and no Points."""
        return _commit(client, _raw_payload(
            0, session_id=session_id, points=[], entities=[],
            provenance_refs=[]))

    def test_points_empty_loop_is_refused_once_the_minute_is_spent(
            self, client, monkeypatch):
        """THE HOLE, pinned: the cheapest loop iteration is charged 4 × 1024 B
        (the :CommitRecord anchor plus the :Session/:Source/:Event chain, none
        of which the points count sees), so an allowance of two such commits
        admits exactly two — the third must 429 and mint no chain.

        Pre-fix this loop has NO org-level bound at all: every iteration 200s
        while the org's points count stays 0 (asserted below), which is the
        `points: []` bypass #4051 names. MEASURED pre-fix, this exact test
        failed with `the third empty commit was admitted (200)`.
        """
        unit = self._charge()
        self._allow(monkeypatch, 2 * unit)
        for i in (0, 1):
            r = self._empty_commit(client, f"sIngest{i}")
            assert r.status_code == 200, r.text
        # The chain nodes a commit mints that no quota counts: proof the thing
        # we are now metering is the thing that was invisible before.
        assert TestCommitPointsGate._chain_counts("sIngest0") == (1, 1, 1)
        assert TestCommitPointsGate._point_count() == 0

        r = self._empty_commit(client, "sIngest2")
        assert r.status_code == 429, (
            f"the third empty commit was admitted ({r.status_code}) — the "
            "per-org ingest budget did not bind")
        assert int(r.headers["Retry-After"]) >= 1
        detail = r.json()["detail"]
        assert detail["code"] == "ingest_rate_limited"
        # The refusal names its SCOPE and the numbers it compared (#4614's
        # contract: a refusal is a structured, distinguishable state).
        assert detail["scope"] == "org"
        assert detail["limit"] == 2 * unit
        assert detail["used"] == 2 * unit
        assert detail["unit"] == "bytes/60s"
        # ... and no chain node was minted for the refused request.
        assert TestCommitPointsGate._chain_counts("sIngest2") == (0, 0, 0)

    def test_the_refusal_does_not_change_the_retry_adjudication(
            self, client, monkeypatch):
        """A refused attempt leaves NO record of its own, so the retry is a
        FIRST adjudication (#4051).

        `store.acquire(...)` merges the `:CommitRecord` before any gate, and
        `plan_commit`/`is_first_adjudication` read ANY existing record as a
        previous attempt — so a leftover `partial` record would drop the
        first-adjudication hard-25 band. MEASURED before the fix: a 26-net-new
        payload holds 26 on a clean graph, but after a 429 left the record the
        retry WROTE all 26 — the client never got the `held[]` adjudication it
        is supposed to act on, and the documented backoff-and-retry became a
        way to write what the ceiling had deferred.
        """
        unit = self._charge()
        self._allow(monkeypatch, unit)
        assert self._empty_commit(client, "sIngestRecA").status_code == 200

        raw = _raw_payload(
            26, session_id="sIngestRecB",
            points=[_point(i, about_entities=[]) for i in range(26)])
        r = _commit(client, raw)
        assert r.status_code == 429, r.text
        cid = _finalize(dict(raw))["client_commit_id"]
        # The attempt's OWN record is gone: nothing was written, so the retry
        # must plan as a first attempt (and the record is not a leftover node
        # either — "pre-write" now genuinely mints nothing for this attempt).
        assert _commit_record(cid) is None, (
            "the refused attempt left its :CommitRecord behind — the retry "
            "would be re-adjudicated without the first-adjudication band")
        assert TestCommitPointsGate._chain_counts("sIngestRecB") == (0, 0, 0)

        # Retry the SAME payload after the documented backoff (the window is
        # reset, which is what the 60 s wait achieves).
        self._allow(monkeypatch, unit)
        retry = _commit(client, raw)
        assert retry.status_code == 200, retry.text
        body = retry.json()
        assert len(body["held"]) == 26, (
            "the retry wrote what the first adjudication should have HELD — "
            f"held={len(body['held'])}")
        assert body["nodes_created"] == 0
        assert TestCommitPointsGate._point_count() == 0

    def test_held_commit_is_charged(self, client, monkeypatch):
        """A HELD commit must be charged too — it WRITES.

        The `held` outcome (>25 net-new on a first adjudication) MERGEs the
        `:Session` node with its `value_nodes_held` counter, and for a fresh
        `session_id` that MERGE CREATES the node while the points count stays
        at 0. A gate placed below that return would leave the cheapest
        remaining loop (a 26-point payload, fresh session each iteration, no
        Points written, no count moved) completely uncharged — the exact class
        #4051 is about. Review cycle 1 caught the gate sitting below it.
        """
        # The allowance is exactly ONE held-band commit's charge: the two empty
        # commits spend 2 × unit, so the held commit must be refused — and its
        # charge stays at/below the allowance, so the over-sized-single-request
        # rule cannot be what admits it (an allowance BELOW the request's own
        # charge would exercise that other rule instead).
        held = _raw_payload(
            26, session_id="sIngestHeld",
            points=[_point(i, about_entities=[]) for i in range(26)])
        self._allow(monkeypatch, self._charge_of(held))
        assert self._empty_commit(client, "sIngestHeldA").status_code == 200
        assert self._empty_commit(client, "sIngestHeldB").status_code == 200
        r = _commit(client, held)
        assert r.status_code == 429, (
            f"a held-band commit was admitted uncharged ({r.status_code}) — "
            "the gate sits below the held return")
        sess, ev, src = TestCommitPointsGate._chain_counts("sIngestHeld")
        assert (sess, ev, src) == (0, 0, 0), (
            "the held :Session MERGE landed despite the refusal")

    def test_refused_charge_does_not_poison_the_window(self, client, monkeypatch):
        """A refused charge is NOT appended to the window.

        A client parked at the boundary retries; if each refusal were
        appended, the window would never empty and the org would be throttled
        for as long as it keeps retrying. Pinned directly (review cycle 1: the
        mutation `append the refused charge` left the whole class green).
        """
        unit = self._charge()
        self._allow(monkeypatch, 2 * unit)
        assert self._empty_commit(client, "sIngestPzA").status_code == 200
        assert self._empty_commit(client, "sIngestPzB").status_code == 200
        for i in range(3):
            assert self._empty_commit(
                client, f"sIngestPzRefused{i}").status_code == 429
        from tortoise.quota import commit_ingest_admit
        # used is still exactly what the two ADMITTED commits spent: the three
        # refusals added nothing (a zero charge is always admissible).
        assert commit_ingest_admit(TEST_ORG_ID, 0).used == 2 * unit

    def test_oversized_single_request_is_admitted_once(self, client, monkeypatch):
        """A request larger than the whole allowance is admitted once.

        Refusing it with a Retry-After would tell a client to retry a payload
        that can never fit — an unbreakable loop that appears only when an
        operator sets `TORTOISE_COMMIT_INGEST_BYTES_PER_MIN` below the route's
        8 MiB body cap. Admitting it spends the window, so the burst is bounded
        by ONE request rather than by "never".
        """
        self._allow(monkeypatch, 100)  # smaller than any commit's charge
        r = self._empty_commit(client, "sIngestOversize1")
        assert r.status_code == 200, r.text
        r2 = self._empty_commit(client, "sIngestOversize2")
        assert r2.status_code == 429, (
            "an oversize request did not spend the window — the burst is not "
            "bounded")


    def test_bound_is_per_org_not_per_key(self, client, monkeypatch):
        """The bound must key on the ORG.

        Today's only ingest rate on this lane is 300 req/min per KEY
        (`RateLimitMiddleware.PATH_LIMITS`), and a team plan allows 20 keys —
        so a per-key bound multiplies with the number of keys the tenant can
        mint. One org spending its minute must not spend another's.

        The middle leg is the DISCRIMINATING one: `key_id` varies while
        `org_id` is held fixed, so the mutation `key = org_id` → `key = key_id`
        in `commit_ingest_admit` fails here (verified by applying it). The
        second-org leg alone cannot discriminate — a new key AND a new org
        gets a fresh window under either keying.
        """
        self._allow(monkeypatch, self._charge())
        assert self._empty_commit(client, "sIngestOrgA1").status_code == 200
        assert self._empty_commit(client, "sIngestOrgA2").status_code == 429

        # A SECOND KEY of the SAME ORG must not buy the org more room.
        app.dependency_overrides[get_current_org] = lambda: {
            **TEST_TEAM, "key_id": "test-key-002"}
        assert self._empty_commit(client, "sIngestOrgA2b").status_code == 429, (
            "a second API key of the SAME org was handed a fresh window — "
            "the budget is keyed on the key, not on the org")

        # A SECOND org has its own window — its first commit still lands.
        app.dependency_overrides[get_current_org] = lambda: {
            **TEST_TEAM, "org_id": "team-002", "key_id": "test-key-002"}
        r = self._empty_commit(client, "sIngestOrgB1")
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False
        # ...and org A's window is still spent: the two windows are separate
        # keys, so B's commit did not buy A any more room.
        app.dependency_overrides[get_current_org] = lambda: dict(TEST_TEAM)
        assert self._empty_commit(client, "sIngestOrgA3").status_code == 429

    def test_sticky_overflow_blocks_a_second_budget(self, client, monkeypatch):
        """An org that spent the SHARED overflow must not graduate to a fresh
        owned bucket when a slot frees — `rate_buckets.py`'s #3124 rule.

        The store-overflow regime IS a flood signature, so a fail-open there
        bites exactly where the bound is needed: without per-key attribution an
        org's overflow charges are invisible to routing, and the first freed
        slot hands it a SECOND full allowance inside the same window (up to 2x
        the limit).
        """
        import tortoise.quota as quota_mod
        unit = self._charge()
        self._allow(monkeypatch, 2 * unit)
        # One slot for the whole store, so TEST_ORG_ID cannot own one and is
        # charged to the shared overflow instead.
        monkeypatch.setattr(quota_mod, "_COMMIT_INGEST_MAX_ORGS", 1)
        assert quota_mod.commit_ingest_admit("org-owner", 1).allowed is True
        # ANOTHER org spends half the overflow first, so this org's own `used`
        # and the shared bucket's total are DIFFERENT numbers — otherwise every
        # assertion below is satisfied by both the shared and the per-org
        # reading of the refusal.
        assert quota_mod.commit_ingest_admit("org-other", unit).allowed is True
        r1 = self._empty_commit(client, "sIngestOvA")
        assert r1.status_code == 200, (
            "the overflow-routed commit was refused — the store is not full")
        r2 = self._empty_commit(client, "sIngestOvB")
        assert r2.status_code == 429
        # ...and the refusal names THIS org's spend, not the shared bucket's.
        assert r2.json()["detail"]["used"] == unit
        assert r2.json()["detail"]["scope"] == "process", (
            "an overflow refusal must say it is about the instance's shared "
            "allowance, not the caller's org budget")
        # A slot frees (what `_commit_ingest_reclaim` does to an aged-out
        # bucket) while this org's OWN overflow charges are still in-window.
        quota_mod._commit_ingest_budget.pop("org-owner")
        r3 = self._empty_commit(client, "sIngestOvC")
        assert r3.status_code == 429, (
            "a slot freed and the overflow-routed org got a SECOND full "
            "allowance inside the same window — the overflow is not sticky "
            "per org")
        assert r3.json()["detail"]["used"] == unit

    def test_overflow_refusal_reports_this_orgs_own_spend(self, monkeypatch):
        """A refusal from the SHARED overflow must name what THIS org spent.

        The overflow bucket holds several orgs' charges; reporting its total as
        the caller's `used` tells a tenant it spent bytes it never sent (the
        second half of `rate_buckets.py`'s #3124 attribution rule).
        """
        import tortoise.quota as quota_mod
        self._allow(monkeypatch, 1000)
        monkeypatch.setattr(quota_mod, "_COMMIT_INGEST_MAX_ORGS", 1)
        assert quota_mod.commit_ingest_admit("org-owner", 1).allowed is True
        assert quota_mod.commit_ingest_admit("org-y", 600).allowed is True
        assert quota_mod.commit_ingest_admit("org-z", 300).allowed is True
        adm = quota_mod.commit_ingest_admit("org-z", 300)
        assert adm.allowed is False
        assert adm.used == 300, (
            f"the refusal reports {adm.used} bytes but this org spent 300 — "
            "the rest belongs to another tenant sharing the overflow bucket")
        assert adm.scope == "process"

    def test_a_fresh_org_takes_a_free_slot_while_the_overflow_is_warm(
            self, monkeypatch):
        """The stickiness is PER ORG, never store-wide.

        A store-wide rule would deny EVERY fresh tenant once the overflow is
        spent — a new-customer denial reachable with a free owned slot. Guards
        the other direction of `test_sticky_overflow_blocks_a_second_budget`.

        Both discriminators are needed (measured against the mutation
        `sticky = overflow is not None and len(overflow) > 0`): the overflow is
        spent to its LIMIT (a merely warm overflow still admits the fresh org,
        just into the shared bucket), and the assertion reads WHICH bucket it
        was charged to (allowed/used are identical under both routings).
        """
        import tortoise.quota as quota_mod
        self._allow(monkeypatch, 1000)
        monkeypatch.setattr(quota_mod, "_COMMIT_INGEST_MAX_ORGS", 1)
        assert quota_mod.commit_ingest_admit("org-owner", 1).allowed is True
        assert quota_mod.commit_ingest_admit("org-y", 1000).allowed is True
        quota_mod._commit_ingest_budget.pop("org-owner")  # a slot frees
        adm = quota_mod.commit_ingest_admit("org-fresh", 500)
        assert adm.allowed is True, (
            "a fresh org with no overflow charges of its own was refused the "
            "free slot — the overflow rule is store-wide, not per org")
        assert adm.used == 500
        assert (quota_mod._commit_ingest_budget.get("org-fresh") or None), (
            "the fresh org was charged to the SHARED overflow instead of "
            "taking the free owned slot — the stickiness leaked to store-wide")
        assert adm.scope == "org"

    def test_replay_does_not_consume_the_budget(self, client, monkeypatch):
        """A fully_written re-POST writes nothing, so it must not be charged
        — #1727's lesson applied to the new bound: quota never gates a
        duplicate."""
        self._allow(monkeypatch, self._charge())
        raw = _raw_payload(0, session_id="sIngestReplay", points=[],
                           entities=[], provenance_refs=[])
        r1 = _commit(client, raw)
        assert r1.status_code == 200, r1.text
        # The minute is now fully spent: a NEW commit is refused...
        assert self._empty_commit(client, "sIngestReplay2").status_code == 429
        # ...while the replay of the committed payload still answers 200.
        r2 = _commit(client, dict(raw))
        assert r2.status_code == 200, r2.text
        assert r2.json()["duplicate"] is True
        assert TestCommitPointsGate._chain_counts("sIngestReplay") == (1, 1, 1)

    def test_default_allowance_admits_a_normal_commit(self, client,
                                                      monkeypatch):
        """The gate must be invisible to ordinary traffic: at the SHIPPED
        default allowance the empty commit — the zero-budget payload
        `test_empty_commit_ok_zero_budget` pins — still 200s.

        Review cycle 3: the previous form asserted the RESOLVED constant, while
        `quota.py` reads ``TORTOISE_COMMIT_INGEST_BYTES_PER_MIN`` at import and
        `docs/INGEST_CONTRACT.md` §9 advertises that knob — so any shell or CI
        job that exported the documented variable failed a test unrelated to
        its change. The DOCUMENTED default is still pinned, now on the named
        constant (`COMMIT_INGEST_DEFAULT_BYTES_PER_MIN`) that the knob defaults
        to, and the endpoint leg opts onto that same figure so an ambient
        override cannot red — or mask — the behaviour it pins.
        """
        import tortoise.quota as quota_mod
        # Pin the SHIPPED figure, not a floor: the docs promise 10 MiB
        # (`docs/INGEST_CONTRACT.md` §9, `docs/capacity.md` §3), so a silent
        # retune of the default must fail here rather than only in a doc drift
        # report. (Review cycle 2: `>= 1_000_000` would still pass at 1 MiB.)
        assert quota_mod.COMMIT_INGEST_DEFAULT_BYTES_PER_MIN == 10 * 1024 * 1024
        monkeypatch.setattr(
            quota_mod, "MAX_COMMIT_INGEST_BYTES_PER_MIN_PER_ORG",
            quota_mod.COMMIT_INGEST_DEFAULT_BYTES_PER_MIN)
        r = self._empty_commit(client, "sIngestDefault")
        assert r.status_code == 200, r.text
        assert r.json()["nodes_created"] == 0
        assert TestCommitPointsGate._chain_counts("sIngestDefault") == (1, 1, 1)

    def test_used_is_the_declared_node_writes_not_the_metered_net_new(
            self, client, monkeypatch):
        """The charge is the nodes the write phase MINTS, not PL4's
        `net_new` (points+entities+operators): an `events[]`-only commit
        meters 0 net-new nodes while minting one :Event per entry, so a
        net-new-keyed budget would let the bypass through.

        The allowance admits ONE empty commit and nothing more, and the
        events-only commit is charged more than an empty one — so it is
        refused while a net-new-keyed budget would see 0 and admit it.
        """
        events = [{"id": f"ev_{i:064d}", "eventKind": "decision",
                   "content": f"event {i}", "confidence": 0.8,
                   "source_ref": "session.md", "about_entities": []}
                  for i in range(2)]
        unit = self._charge()
        events_charge = self._charge(events=events)
        assert events_charge > unit, (
            "an events-only payload is not charged for the :Event nodes it "
            "mints — the budget would be bypassable by the hole it bounds")
        # The allowance is exactly the events payload's own charge: if the
        # charge were keyed on PL4's `net_new` (0 for a merge-only payload, and
        # 0 here because no Points are declared) the events commit would spend
        # nothing and the FOLLOWING empty commit would be admitted.
        self._allow(monkeypatch, events_charge)
        r = _commit(client, _raw_payload(
            0, session_id="sIngestEvents", points=[], entities=[],
            provenance_refs=[], events=events))
        assert r.status_code == 200, r.text
        assert self._empty_commit(client, "sIngestEventsAfter").status_code == 429, (
            "the events-only commit did not spend the window — the budget is "
            "not charging the :Event nodes the write phase mints")
        assert TestCommitPointsGate._chain_counts("sIngestEvents") == (1, 1, 1)

    def test_uncapped_text_fields_are_charged_as_payload_bytes(self):
        """The payload term: `summary`/`story_arc` carry no Layer-1 field cap,
        so a few-node commit could otherwise add megabytes for a few KB of node
        charge. The charge must follow the bytes the client sent.

        (Unit test, no HTTP: the charge function is what the endpoint calls.)
        """
        big = "x" * 40_000
        assert self._charge(summary=big) >= len(big)
        assert self._charge(summary=big) > self._charge()

    def test_no_org_context_is_unbounded(self):
        """stdio/operator (no org) mirrors `_check_org_limit`'s
        `if not org_id: return` — there is no tenant to throttle."""
        from tortoise.quota import commit_ingest_admit
        assert commit_ingest_admit(None, 10 ** 9).allowed is True
        assert commit_ingest_admit("", 10 ** 9).allowed is True

    def test_retry_after_is_the_wait_until_this_charge_fits(self, monkeypatch):
        """`Retry-After` must be the wait after which THIS request fits, not
        `window - age(oldest entry)`.

        Charges expire oldest-first and they are uneven, so the oldest entry's
        expiry can leave a request still too big: 10 B at t+0 and 900 B at t+2
        against a 1,000 B allowance leave the window at 900 B when the first
        expires, and a 500 B request needs the SECOND to expire at t+62.
        Promising `window - age(oldest)` makes a client that honours the header
        exactly be refused again (review cycle 3, measured `retry=2.00` at
        t+60 for a request that needed t+62).
        """
        import tortoise.quota as quota_mod

        class _Clock:
            now = 1000.0

            @classmethod
            def monotonic(cls):
                return cls.now

        monkeypatch.setattr(quota_mod, "time", _Clock)
        monkeypatch.setattr(
            quota_mod, "MAX_COMMIT_INGEST_BYTES_PER_MIN_PER_ORG", 1000)
        quota_mod._reset_commit_ingest_budget_for_tests()
        assert quota_mod.commit_ingest_admit("orgAdj", 10).allowed is True
        _Clock.now += 2.0
        assert quota_mod.commit_ingest_admit("orgAdj", 900).allowed is True
        _Clock.now += 1.0
        refused = quota_mod.commit_ingest_admit("orgAdj", 500)
        assert refused.allowed is False
        assert refused.used == 910
        # The 900 B charge was taken at t+2, so the request fits only at t+62.
        assert refused.retry_after == pytest.approx(59.0), (
            f"retry_after {refused.retry_after} promises a wait after which "
            "the request still does not fit (the 900 B charge expires at "
            "t+62, not t+60)")
        # ... and it is admitted exactly then.
        _Clock.now = 1062.0
        assert quota_mod.commit_ingest_admit("orgAdj", 500).allowed is True

    def test_the_charge_is_never_cheaper_than_the_write(self, client,
                                                       monkeypatch):
        """The declared node count is an UPPER bound on the nodes the write
        phase ACTUALLY mints — measured against the graph, not asserted from
        the formula that produced it.

        Review cycle 2 found the count under-charging: every created Point and
        Operator also leaves a `:GraphEvent` (`sdk._GRAPH_EVENT_TYPES`), so a
        points-bearing payload minted ~2x the nodes the count declared. That is
        the fail-OPEN direction for a throttle — a charge cheaper than the
        write — so the bound is pinned against the graph itself. The allowance
        is set wide on purpose: this test is about the SHAPE, not the limit.

        ⛔ Review cycle 3 found this leg measuring a commit that minted NOTHING
        in the lane it claimed to pin: it re-sent the SAME point ids and
        content, so both points reconciled as `merge` (`MATCH … SET
        p.updatedAt`, zero nodes) and the 5-node cushion hid the entire points
        term — the cycle-2 regression this test exists for passed it. The
        measured commit therefore now carries NEW ids, NEW content and a NEW
        entity name, and the `minted >= …` assertion below pins that the nodes
        under test were really minted.
        """
        from tortoise.commit_schema import (
            CommitPayload,
            declared_commit_write_count,
        )
        self._allow(monkeypatch, 64 * 1024 * 1024)

        def declared_of(raw: dict) -> int:
            return declared_commit_write_count(
                CommitPayload(**{**raw, "client_commit_id": "0" * 64}))

        def node_total() -> int:
            return int(_team_sdk()._get_proj().g.query(
                "MATCH (n) RETURN count(n)").result_set[0][0])

        def measured(raw: dict) -> tuple[int, int]:
            """(declared, minted) for ONE commit, measured off the graph."""
            before = node_total()
            r = _commit(client, raw)
            assert r.status_code == 200, r.text
            return declared_of(raw), node_total() - before

        # Measured on a STEADY-STATE commit: the very first write to a graph
        # also creates the event store's one-time metadata singletons
        # (`:GraphEventMeta`, `:EpMeta`, `:Meta`), which are per-GRAPH
        # constants rather than per-request growth — the loop #4051 describes
        # repeats this shape, not that one. So commit once to absorb them.
        assert _commit(client, _raw_payload(
            2, session_id="sIngestBound")).status_code == 200

        # ── (a) NEW points + a NEW entity: the shape cycle 3's false PASS
        # was supposed to measure. Different ids, different content, and an
        # entity name (`Beta`) the graph does not hold yet, so the points and
        # their `PointAdded` events are really minted. ──
        declared_new, minted_new = measured(_raw_payload(
            2, session_id="sIngestBoundNew2",
            points=[_point(2, about_entities=["Beta"]),
                    _point(3, about_entities=["Beta"])],
            entities=[{"name": "Beta", "kind": "Project",
                       "passes_frequency_gate": True}],
        ))
        assert minted_new >= 10, (
            f"the measured commit minted {minted_new} nodes — it must mint the "
            "2 Points, their 2 `PointAdded` events, the new `:Object`, the "
            "4-node chain and the provenance `:Source`; a lower delta means "
            "the points reconciled as merges and this leg pins nothing")
        assert declared_new >= minted_new, (
            f"the charge declared {declared_new} nodes but the commit minted "
            f"{minted_new} — the throttle would be cheaper than the write, "
            "which is the fail-open direction (#4051)")

        # ── (b) SUPERSEDE: the same ids with CHANGED content. The write phase
        # mints the successor Point + its `PointAdded` AND the predecessor's
        # `PointSuperseded` (3 nodes/entry), where the pre-cycle-3 flat rate
        # charged 2. Measured absolutely: the entity `Alpha` and the two
        # superseded points already exist, so this leg has no cushion to hide
        # a one-node deficit behind. ──
        declared_sup, minted_sup = measured(_raw_payload(
            2, session_id="sIngestBoundSup2",
            points=[_point(2, content="revised 2"),
                    _point(3, content="revised 3")],
        ))
        assert minted_sup > 0
        assert declared_sup >= minted_sup, (
            f"a SUPERSEDING point is charged {declared_sup} nodes but the "
            f"commit minted {minted_sup} — charging `2` per `points[]` entry "
            "misses the predecessor's `PointSuperseded` event (the fail-open "
            "direction, #4051)")

    def test_a_mitigates_operator_is_charged_for_its_second_event(
            self, client, monkeypatch):
        """A `MITIGATES` bridge-attack mints THREE nodes, not two.

        `sdk.mitigate_operator` CREATEs the mitigation `:Point` and then emits
        BOTH `PointAdded` and `OperatorAdded` for it — two durability records,
        where an `IMPL`/`NAND` entry (`sdk.create_operator`) leaves one. The
        mitigation Point is in no other lane (it is not a `points[]` entry), so
        an under-charged operator lane is a straight fail-open.

        The discrimination is MARGINAL on purpose: the two payloads are
        identical except for the extra `MITIGATES` record, so the difference
        isolates that one entry. An absolute `declared >= minted` leg cannot
        see a one-node deficit here — the flat 3-per-point rate deliberately
        carries a ≥2-node cushion over a `new` point — which is exactly the
        false-PASS shape review cycle 3 called out. Measured: the mitigation
        adds 3 nodes; charged pre-fix: 2 -> red; charged now: 3 -> green.
        """
        from tortoise.commit_schema import (
            CommitPayload,
            declared_commit_write_count,
        )
        self._allow(monkeypatch, 64 * 1024 * 1024)

        def declared_of(raw: dict) -> int:
            return declared_commit_write_count(
                CommitPayload(**{**raw, "client_commit_id": "0" * 64}))

        def node_total() -> int:
            return int(_team_sdk()._get_proj().g.query(
                "MATCH (n) RETURN count(n)").result_set[0][0])

        def measured(raw: dict) -> tuple[int, int]:
            before = node_total()
            r = _commit(client, raw)
            assert r.status_code == 200, r.text
            return declared_of(raw), node_total() - before

        # Absorb the per-graph singletons first (`_GraphEventMeta` etc.).
        assert _commit(client, _raw_payload(
            1, session_id="sIngestMitBound")).status_code == 200

        impl_only = _raw_payload(3, session_id="sIngestMitA", points=[
            _point(20, content="it is cheap"),
            _point(21, content="option A"),
            _point(22, content="we can raise the price"),
        ], operators=[
            {"src": _point(20)["id"], "dst": _point(21)["id"],
             "op_type": "IMPL", "direction": "unidirectional"},
        ])
        with_mitigates = _raw_payload(3, session_id="sIngestMitB", points=[
            _point(30, content="it is expensive"),
            _point(31, content="option B"),
            _point(32, content="we can cut the price"),
        ], operators=[
            {"src": _point(30)["id"], "dst": _point(31)["id"],
             "op_type": "IMPL", "direction": "unidirectional"},
            {"src": _point(32)["id"], "dst": _point(31)["id"],
             "op_type": "MITIGATES",
             "target": {"src": _point(30)["id"], "dst": _point(31)["id"],
                        "op_type": "IMPL"},
             "strength": 0.4},
        ])

        declared_a, minted_a = measured(impl_only)
        declared_b, minted_b = measured(with_mitigates)
        # The bound must hold on BOTH shapes...
        assert declared_a >= minted_a, (declared_a, minted_a)
        assert declared_b >= minted_b, (
            f"the mitigation-bearing commit declared {declared_b} nodes but "
            f"minted {minted_b} — the throttle would be cheaper than the "
            "write (#4051)")
        # ...and the MITIGATES entry itself must carry its own 3 nodes.
        extra_minted = minted_b - minted_a
        extra_declared = declared_b - declared_a
        assert extra_minted == 3, (
            f"the MITIGATES record minted {extra_minted} nodes, not 3 (the "
            "mitigation `:Point` + its `PointAdded` + its `OperatorAdded`) — "
            "this leg is measuring something other than the mitigation lane")
        assert extra_declared >= extra_minted, (
            f"a MITIGATES entry is charged {extra_declared} nodes but writes "
            f"{extra_minted} — the operator lane under-charges the "
            "bridge-attack, the fail-open direction (#4051)")


class TestDeclaredCommitWriteCount:
    """The budget's unit — every node `_execute_commit_writes` mints."""

    @staticmethod
    def _payload(**overrides):
        from tortoise.commit_schema import CommitPayload
        raw = _raw_payload(0, points=[], entities=[], provenance_refs=[])
        raw.update(overrides)
        raw["client_commit_id"] = "0" * 64
        return CommitPayload(**raw)

    def test_empty_payload_declares_the_anchor_and_chain(self):
        """4 = `:CommitRecord` (merged before every gate) + `:Session` +
        transcript `:Source` + AgentSession `:Event`."""
        from tortoise.commit_schema import declared_commit_write_count
        assert declared_commit_write_count(self._payload()) == 4

    def test_provenance_refs_add_the_session_source(self):
        """`_execute_commit_writes` step 4 materializes the canonical
        `session:<id>` `:Source` only when `provenance_refs` is non-empty — an
        ordinary commit has them, so omitting it would under-charge the common
        case (review cycle 1)."""
        from tortoise.commit_schema import declared_commit_write_count
        p = self._payload(provenance_refs=[
            {"path": "session.md", "spans": ["0-10"]}],)
        assert declared_commit_write_count(p) == 5

    def test_each_collection_entry_is_charged_once(self):
        """Flat lanes cost one node per entry; `points[]` costs THREE (the
        upper bound over the `new`=2 / `supersede`=3 / `merge`=0 reconcile
        actions), `operators[]` TWO for `IMPL`/`NAND`, and `supersessions[]`
        TWO. See `_COMMIT_POINT_ENTRY_NODES` and friends in
        `tortoise/commit_schema.py`."""
        from tortoise.commit_schema import declared_commit_write_count
        p = self._payload(
            points=[_point(0), _point(1)],
            entities=[{"name": "Alpha", "kind": "Project",
                       "passes_frequency_gate": True}],
            events=[{"id": f"ev_{i:064d}", "eventKind": "decision",
                     "content": f"e{i}", "confidence": 0.8,
                     "source_ref": "session.md", "about_entities": []}
                    for i in range(3)],
            sources=[{"url": "https://example.test/a",
                      "sourceKind": "web"}],
        )
        # 4 fixed + (2 points × 3) + 1 entity + 3 events + 1 source
        assert declared_commit_write_count(p) == 4 + 6 + 1 + 3 + 1

    def test_a_mitigates_entry_is_charged_three_and_an_impl_two(self):
        """The operator lane's rate follows the WIRE `op_type`: a `MITIGATES`
        bridge-attack costs 3 nodes (`mitigate_operator` emits `PointAdded`
        AND `OperatorAdded` for its mitigation Point), an `IMPL`/`NAND` 2
        (`create_operator` emits `OperatorAdded` only)."""
        from tortoise.commit_schema import declared_commit_write_count
        base = self._payload(points=[_point(0), _point(1), _point(2)])
        impl = self._payload(
            points=[_point(0), _point(1), _point(2)],
            operators=[{"src": _point(0)["id"], "dst": _point(1)["id"],
                        "op_type": "IMPL"}],
        )
        mitigates = self._payload(
            points=[_point(0), _point(1), _point(2)],
            operators=[
                {"src": _point(0)["id"], "dst": _point(1)["id"],
                 "op_type": "IMPL"},
                {"src": _point(2)["id"], "dst": _point(1)["id"],
                 "op_type": "MITIGATES",
                 "target": {"src": _point(0)["id"], "dst": _point(1)["id"],
                            "op_type": "IMPL"},
                 "strength": 0.4},
            ],
        )
        assert (declared_commit_write_count(impl)
                - declared_commit_write_count(base)) == 2
        assert (declared_commit_write_count(mitigates)
                - declared_commit_write_count(impl)) == 3

    def test_the_node_floor_is_the_pricing_cost_basis(self):
        """The floor is the repo's own per-node figure, not a new number.

        `product/pricing.json` `billing.cost_basis.bytes_per_node` is the ONE
        source of that figure; if an operator retunes the cost basis, this rate
        must not keep charging the old value silently.
        """
        import json
        from pathlib import Path

        from tortoise.commit_schema import COMMIT_INGEST_BYTES_PER_NODE
        pricing_json = Path(__file__).resolve().parent.parent / "product" / "pricing.json"
        basis = json.loads(pricing_json.read_text())["billing"]["cost_basis"]
        assert basis["bytes_per_node"] == COMMIT_INGEST_BYTES_PER_NODE

    def test_the_charge_is_the_node_floor_for_a_small_payload(self):
        """A small payload is charged `nodes × 1024` — the floor, by exact
        value, so deleting the floor from `commit_ingest_charge_bytes` fails
        here directly (review cycle 1: it previously failed only by accident,
        through a few bytes of `session_id` length drift)."""
        from tortoise.commit_schema import (
            COMMIT_INGEST_BYTES_PER_NODE,
            commit_ingest_charge_bytes,
        )
        assert commit_ingest_charge_bytes(self._payload()) == 4 * COMMIT_INGEST_BYTES_PER_NODE

    def test_a_fat_payload_is_charged_by_its_bytes(self):
        """The other arm: an uncapped `summary` is charged what it actually
        sends, not the node floor."""
        from tortoise.commit_schema import commit_ingest_charge_bytes
        big = "x" * 40_000
        assert commit_ingest_charge_bytes(self._payload(summary=big)) >= len(big)


class TestLayer1:
    def test_missing_required_field_400(self, client):
        raw = _raw_payload(1)
        del raw["session_id"]
        r = require_answered(
            client.post("/v1/sessions/commit", json=_finalize(raw)),
            what="POST /v1/sessions/commit answered")
        assert r.status_code == 400
        assert "session_id" in r.json()["detail"]

    def test_missing_client_commit_id_400(self, client):
        raw = _raw_payload(1)
        del raw["client_commit_id"]
        r = require_answered(
            client.post("/v1/sessions/commit", json=raw),
            what="POST /v1/sessions/commit answered")
        assert r.status_code == 400

    def test_commit_id_mismatch_422(self, client):
        raw = _finalize(_raw_payload(1))
        raw["client_commit_id"] = "0" * 64  # wrong hash
        r = require_answered(
            client.post("/v1/sessions/commit", json=raw),
            what="POST /v1/sessions/commit answered")
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
        r = require_answered(
            client_no_auth.post(
                "/v1/sessions/commit", json=_finalize(_raw_payload(1)),
                headers={"Authorization": "Bearer tt_badkey0000000000000000"}),
            what="POST /v1/sessions/commit answered")
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
        # basename-only: the document Source's sourcePath is the basename,
        # and the Source identity is the canonical session-scoped permalink
        # (no absolute path).
        rows = g.query(
            "MATCH (s:Source) WHERE s.sessionId='s1' "
            "AND s.documentKind IS NOT NULL RETURN s.sourcePath",
        ).result_set
        assert rows and rows[0][0] == "session.md"
        rows = g.query(
            "MATCH (s:Source {url:'session:s1'}) RETURN count(s)",
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
        detail (auth-gated: the get_current_org override fires 401-free
        first). The 413 must NOT be remapped into the 400 catch-all."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_COMMIT_SESSION_MAX_BYTES", 8192)
        r = require_answered(
            client.post(
                "/v1/sessions/commit", content=_oversized_chunked(),
                headers={"content-type": "application/json"}),
            what="POST /v1/sessions/commit answered")
        assert r.status_code == 413
        assert r.json()["detail"] == ha_mod._COMMIT_SESSION_413_DETAIL

    def test_commit_malformed_400_preserved(self, client):
        """Malformed JSON → 400 'Request body must be a JSON object'
        (unchanged — empty body lands here too, via json.loads(b''))."""
        r = require_answered(
            client.post(
                "/v1/sessions/commit", content=b"{",
                headers={"content-type": "application/json"}),
            what="POST /v1/sessions/commit answered")
        assert r.status_code == 400
        assert r.json()["detail"] == "Request body must be a JSON object"

    def test_commit_empty_400_preserved(self, client):
        """Empty body → 400 (unchanged — Starlette's request.json() raises on
        empty; json.loads(b'') raises the same class into the same catch)."""
        r = require_answered(
            client.post(
                "/v1/sessions/commit", content=b"",
                headers={"content-type": "application/json"}),
            what="POST /v1/sessions/commit answered")
        assert r.status_code == 400
        assert r.json()["detail"] == "Request body must be a JSON object"


def test_commit_seam_is_inconclusive_on_a_transport_bound_refusal():
    """#5049: `_commit`'s route is not exempt from the transport wait bound.

    Deterministic (no load, no server): the seam's own request is answered by
    the product's refusal. Removing `require_answered` from `_commit` makes this
    go RED with `DID NOT RAISE pytest.skip.Exception` — the pre-fix `_commit`
    returned the refusal, and each caller's bare `assert 504 == 200` (or `500`)
    was the FAIL this conversion deletes.
    """

    class _BoundRefusalClient:
        # #8579 P2-1: the fake must carry the product's REST ENVELOPE, not just
        # the message text — the detector is envelope-gated now.
        status_code = 504

        def __init__(self) -> None:
            import json

            from tortoise import mcp_auth

            self._message = mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE
            self.text = json.dumps({"detail": self._message})

        def post(self, *_args, **_kwargs):
            return self

        def json(self):
            return {"detail": self._message}

    with pytest.raises(pytest.skip.Exception) as excinfo:
        _commit(
            _BoundRefusalClient(),
            _raw_payload(0, session_id="sRefusal", points=[], entities=[],
                         provenance_refs=[]))
    assert "INCONCLUSIVE [#5049]" in str(excinfo.value)


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8802: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``POST /v1/sessions/commit`` is
    the route every read in this file funnels to and it is not exempt from the
    bound. The plain-client control is the non-vacuity leg — it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if
    the route is ever exempted (the middleware stops refusing, so the
    control's 504 assertion fails).

    TWO clients in this module stay plain, both declared in
    `test_verdict_contract.py::_CONVERTED_CLIENT_SEAMS`: the R-13 rate-limit
    test's ``TestClient(mini)``, whose ``mini`` app carries only
    ``RateLimitMiddleware`` (no ``WaitBoundMiddleware``, so no read bound from it
    can be a transport refusal), and this pin's own control.
    """
    from fastapi.testclient import TestClient

    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("POST", "/v1/sessions/commit")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(app) as raw:
        assert raw.post("/v1/sessions/commit", json={}).status_code == 504
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.post("/v1/sessions/commit", json={})
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "POST /v1/sessions/commit answered" in message


# ── #5475 — the SDK kind gate on the hosted COMMIT path ────────────────────
#
# #5202 keeps `entities[].kind` OUT of Layer-1 (an off-vocabulary entity kind
# passes validation), and this door persists it through `sdk.create_entity` —
# which is now the gated funnel. So the refusal reaches the commit door, and it
# is DETERMINISTIC (the same payload refuses identically on every retry): the
# fail-closed 500's "retry with the same client_commit_id" advice is wrong for
# it, and it would discard the Option-A message the writer acts on. Same
# reasoning, and the same 422 mapping, as the #5363 inverted-window refusal
# above.

class Test5475CommitDoorKindRefusal:

    def test_unapproved_entity_kind_is_an_actionable_422(self, client):
        """A commit naming an unapproved pack on an entity is a 422, not a 500.

        FAIL-ON (pre-fix): the entity write is accepted, the pack joins the
        approval set, and the commit reports success. With the gate but WITHOUT
        the pre-write placement measured here, the 422 arrives after the point,
        the sources and the Session have landed and leaves the :CommitRecord
        ``partial`` — which reds the ``nothing landed`` leg (and reads as a
        pre-write validation error, which it is not).
        REACHABLE: ``entities[].kind`` is not Layer-1 gated (#5202), so the
        payload really does reach the write phase; the graph really is
        ``dev``-only (seeded, then read back before the commit), and the
        payload carries a Point too, so "nothing landed" is not vacuous.
        """
        from tortoise.pack_state import graph_installed_namespaces
        sdk = _team_sdk()
        sdk._get_proj().g.query(
            "MERGE (p:PackInstall {namespace:'dev'}) SET p.version='0.0.0', "
            "    p.status='active', p.source='starter'")
        assert graph_installed_namespaces(sdk) == frozenset({"dev"})

        r = _commit(client, _raw_payload(
            n_points=1,
            # the point is what makes "nothing landed" non-vacuous: it must NOT
            # be written before the refusal. Its about_entities is emptied so
            # Layer-1 (which runs first) has nothing to object to.
            points=[_point(0, about_entities=[])],
            entities=[{"name": "CampaignAlpha", "kind": "marketing:campaign",
                       "passes_frequency_gate": True}]))

        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "KIND_NAMESPACE_NOT_INSTALLED", r.text
        assert detail["namespace"] == "marketing"
        assert detail["kind"] == "marketing:campaign"
        assert detail["property"] == "objectKind"
        assert detail["installed_namespaces"] == ["dev"]
        # Option A: what / why / what to do, delivered rather than swallowed.
        assert "marketing" in detail["message"]
        assert "balloon" in detail["message"]
        assert "PUT /v1/graphs/{graph_id}/packs" in detail["message"]
        assert "retry" not in json.dumps(detail).lower()
        # the approval set did not move
        assert graph_installed_namespaces(sdk) == frozenset({"dev"})
        # and NOTHING of the commit landed: the refusal is a pre-write payload
        # rejection like every other 422 on this route, not a half-commit.
        g = sdk._get_proj().g
        for label in ("Point", "Object", "Source", "Session", "CommitRecord"):
            n = g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
            assert n == 0, f"the refused commit left {n} :{label} node(s)"

    def test_approved_entity_kind_commit_still_succeeds(self, client):
        """The control: a commit whose entity kind IS approved keeps writing.

        FAIL-ON: a gate that refuses every namespaced entity kind on a graph
        with pack records (the extractor's pack-kinded entities are legitimate).
        REACHABLE: ``dev`` is installed and ``dev:epic`` is a real dev kind.
        """
        sdk = _team_sdk()
        sdk._get_proj().g.query(
            "MERGE (p:PackInstall {namespace:'dev'}) SET p.version='0.0.0', "
            "    p.status='active', p.source='starter'")
        r = _commit(client, _raw_payload(
            n_points=0,
            entities=[{"name": "EpicAlpha", "kind": "dev:epic",
                       "passes_frequency_gate": True}]))
        assert r.status_code == 200, r.text


# ── #9876 — the create-side AMBIGUITY refusal on the hosted COMMIT path ──────
#
# `entity_identity.resolve_or_mint` REFUSES a name that two or more LIVE
# entities hold (route-then-refuse, R8). `_execute_commit_writes` reaches it at
# step 6 — after steps 2/2b/3/3b/4/5 have journaled and written — so before the
# fix the refusal escaped as the fail-closed 500 whose detail says "the commit
# is replay-safe (retry with the same client_commit_id)". That advice is wrong
# twice over for this shape: the refusal is DETERMINISTIC (a same-payload retry
# re-raises identically), and it lands on top of a half-written commit — the
# #2422/#7925 partial-mutation class the SDK bundle door already closes with a
# bundle-wide ambiguity loop (`sdk._validate_bundle`).
#
# These pins cover both halves, in the same shape #5363 uses for the inverted
# supersede window: (A) the 422 mapping on the boundary, and (B) the PRE-WRITE
# probe that makes the refusal write NOTHING at all.

_AMBIGUOUS_NAME = "Alpha"


def _add_idless_live_carrier(name: str) -> None:
    """A second LIVE carrier of *name* that carries NO id — the legacy / raw
    / foreign ``MERGE (n:Label {name:…})`` row. It is the shape that makes the
    count and the report disagree if either filters on truthy ids (#8018), so
    the refusal's evidence is asserted against it."""
    _team_sdk()._get_proj().g.query(
        # ⛔ CREATE, not MERGE: a MERGE on the name pattern would MATCH the
        # seeded id-carrying carrier and stamp it instead of opening the second
        # live row this shape needs.
        "CREATE (o:Object {name:$n, status:'live'})", params={"n": name})


def _node_counts() -> dict[str, int]:
    g = _team_sdk()._get_proj().g
    labels = ("Point", "Object", "Source", "Session", "CommitRecord", "Event")
    return {label: g.query(f"MATCH (n:{label}) RETURN count(n)").result_set[0][0]
            for label in labels}


class Test9876AmbiguousEntityNameOnCommit:
    """#9876 — a commit naming an entity two LIVE carriers hold is refused
    BEFORE the first write, as an actionable 422."""

    def test_a_commit_naming_a_doubly_held_entity_422s_and_writes_nothing(
            self, client):
        """(B) the pre-write probe: clean 422, NOTHING written — not even a
        :CommitRecord — and the conflicting carriers are named.

        Fails without the fix: the status is 500 (the retry-advising
        fail-closed detail) and the commit is half-written (Events/Points/
        Sources/Session/CommitRecord counts all move)."""
        _seed_objects(client, "g9876-a", [_AMBIGUOUS_NAME])
        _add_idless_live_carrier(_AMBIGUOUS_NAME)
        before = _node_counts()
        raw = _raw_payload(1, session_id="g9876-a2")
        _finalize(raw)
        r = client.post("/v1/sessions/commit", json=raw)

        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "AMBIGUOUS_ENTITY_NAME", r.text
        assert detail["label"] == "Object"
        assert detail["name"] == _AMBIGUOUS_NAME
        # the evidence: BOTH live carriers, the id-less one NAMED rather than
        # filtered away (the count that refused is live ROWS)
        assert len(detail["candidate_ids"]) == 2, detail
        assert "<id-less>" in detail["candidate_ids"], detail
        assert "rename or retire" in detail["message"], detail
        # a deterministic refusal must never ship the retry advice
        assert "retry" not in json.dumps(detail).lower()

        # ⛔ NOTHING of the commit landed — including the :CommitRecord, which
        # is what makes a corrected payload a genuine FIRST attempt instead of
        # a re-adjudication without the first-adjudication hard-25 band.
        assert _node_counts() == before, (
            f"the refused commit wrote: {_node_counts()} vs {before}")
        # and it neither adopted nor minted a third carrier of the name
        rows = _object_row(_AMBIGUOUS_NAME)
        assert len(rows) == 2, rows

    def test_a_refusal_escaping_the_precheck_is_still_a_422(self, client,
                                                            monkeypatch):
        """(A) the boundary of last resort — with the pre-write probe disabled
        the refusal still reaches `sdk.create_entity` at step 6 (a concurrent
        create between probe and write, or a leg the probe does not enumerate),
        and it must map to the same 422, never the retry-advising 500.

        Fails without the fix: the status is 500, not 422."""
        import tortoise.hosted_api as ha_mod
        monkeypatch.setattr(ha_mod, "_refuse_ambiguous_commit_entities",
                            lambda sdk, reconcile: None)
        _seed_objects(client, "g9876-b", [_AMBIGUOUS_NAME])
        _add_idless_live_carrier(_AMBIGUOUS_NAME)
        r = _commit(client, _raw_payload(1, session_id="g9876-b2"))

        assert r.status_code == 422, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "AMBIGUOUS_ENTITY_NAME", r.text
        assert detail["name"] == _AMBIGUOUS_NAME
        assert len(detail["candidate_ids"]) == 2, detail
        assert "retry" not in json.dumps(detail).lower()

    def test_a_terminal_carrier_beside_a_live_one_is_not_a_refusal(
            self, client):
        """Over-fix guard: a TERMINAL carrier of the same name does not block
        the create (R5 — another incarnation), so the commit must still land.

        FAIL-ON: a probe that counts ROWS instead of LIVE rows (`MATCH
        (o:Object {name:$n})` with no terminal exclusion) 422s a legal commit
        the writer accepts."""
        _seed_objects(client, "g9876-c", [_AMBIGUOUS_NAME])
        _team_sdk()._get_proj().g.query(
            "CREATE (o:Object {name:$n, status:'superseded', "
            "               id:'obj-buried-alpha'})",
            params={"n": _AMBIGUOUS_NAME})
        r = _commit(client, _raw_payload(1, session_id="g9876-c2"))
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is False, r.text

    def test_an_idempotent_repost_is_never_refused(self, client):
        """Over-fix guard on the PLACEMENT: the probe sits AFTER the L1 replay
        return, so re-POSTing an already-written payload still answers
        `duplicate:true` even once the name it names has become doubly held.

        FAIL-ON: probe placed above the replay return → the same re-POST 422s
        and a client's documented idempotent retry fails on a commit that
        already succeeded."""
        first = _raw_payload(1, session_id="g9876-d")
        assert _commit(client, first).status_code == 200
        assert _commit_record(first["client_commit_id"])[0] == "fully_written"
        # the name the committed payload created is now doubly held
        _add_idless_live_carrier(_AMBIGUOUS_NAME)

        again = dict(first)  # identical payload ⇒ identical client_commit_id
        r = client.post("/v1/sessions/commit", json=again)
        assert r.status_code == 200, r.text
        assert r.json()["duplicate"] is True, r.text


# ── #9876 review — the LIVE pass must classify an about-edge MENTION the way
# the replay pass does ───────────────────────────────────────────────────────

_ABOUT_NAME = "Ghost"


class Test9876AboutMentionDisposition:
    """A commit's ``events[].about_entities`` name is ATTRIBUTION, so its miss
    takes the EXEMPT ``unfolded-mention`` shape on BOTH passes.

    ``hosted_api`` step 6a resolves each deferred about name with
    ``resolve_fold_name``. The replay pass resolves the same reference with
    ``mention=True`` (``projection/entities.py:4896`` status-fold target,
    ``:5275`` ``EventRecorded.object``), and D1's ``unfolded-mention`` row
    enumerates *"an about-edge name"* as an exempt ATTRIBUTION use — so a
    non-exempt record here is one reference with two dispositions: exactly the
    live/replay parity break R8 forbids (*one journal → one disposition on
    every surface*), and a report claiming a fold refused something for a write
    where nothing was folded.
    """

    def test_a_mention_that_races_an_ambiguous_carrier_records_the_exempt_shape(
            self, client, monkeypatch):
        """The reachable live miss: a second carrier appears BETWEEN the
        pre-write probe and step 6a — the race the probe/422 boundary admits by
        design (see ``test_a_refusal_escaping_the_precheck_is_still_a_422``).

        FAIL-ON (pre-fix): step 6a omits ``mention=True``, so this records
        ``ambiguous-entity-name`` — a NON-exempt refusal — while replay of the
        same event records the exempt shape."""
        from tortoise.projection import nonfolded
        from tortoise.projection.nonfolded import (
            SHAPE_AMBIGUOUS_ENTITY_NAME,
            SHAPE_UNFOLDED_MENTION,
            SHAPE_UNREGISTERED_REFERENCE,
        )

        recorded: list = []
        real_record = nonfolded.record_non_folded

        def _spy(shape, **fields):
            entry = real_record(shape, **fields)
            recorded.append(entry)
            return entry

        # `resolve_fold_name` imports the symbol at CALL time, so patching the
        # module attribute is what a live commit actually sees.
        monkeypatch.setattr(nonfolded, "record_non_folded", _spy)

        real_create = TortoiseSDK.create_entity

        def _create_then_split(self, kind, name, **kw):
            """Step 6 registers the entity; the SECOND live carrier of the same
            name lands right after it, before step 6a resolves."""
            out = real_create(self, kind, name, **kw)
            if name == _ABOUT_NAME:
                self._get_proj().g.query(
                    "CREATE (o:Object {name:$n, status:'live'})",
                    params={"n": name})
            return out

        monkeypatch.setattr(TortoiseSDK, "create_entity", _create_then_split)

        raw = _raw_payload(1, session_id="g9876-e")
        raw["entities"] = [
            {"name": _AMBIGUOUS_NAME, "kind": "Project",
             "passes_frequency_gate": True},
            {"name": _ABOUT_NAME, "kind": "core:other",
             "passes_frequency_gate": True},
        ]
        raw["events"] = [{
            "id": f"ev_{content_hash('a mention of a name two carriers hold')[:62]}",
            "eventKind": "decision",
            "content": "a mention of a name two carriers hold",
            "confidence": 0.5,
            "about_entities": [_ABOUT_NAME],
            "source_ref": "session.md",
        }]
        r = _commit(client, raw)
        assert r.status_code == 200, r.text

        mentions = [e for e in recorded
                    if f"name={_ABOUT_NAME!r}" in (e.detail or "")]
        assert mentions, (
            "the live about-edge miss recorded nothing — the probe below cannot "
            "distinguish the two dispositions if step 6a never ran")
        shapes = {e.shape for e in mentions}
        assert SHAPE_UNFOLDED_MENTION in shapes, (
            "the live pass recorded the about-edge mention NON-exemptly "
            f"({sorted(shapes)}); replay records {SHAPE_UNFOLDED_MENTION} for "
            "the same reference — one reference, two dispositions")
        assert SHAPE_AMBIGUOUS_ENTITY_NAME not in shapes, (
            "an about-edge MENTION was recorded as an identity refusal — a "
            "write that folded nothing is reported as a refused fold")
        assert SHAPE_UNREGISTERED_REFERENCE not in shapes, (
            "an about-edge MENTION was recorded as an identity refusal")


def test_the_tenant_route_cannot_put_the_narrative_back_on_a_source():
    """#10000: the S1 narrative is not a node property, and NOT caller-writable.

    The commit writer's raw `SET s.story_arc` is gone, but `story_arc` stays
    DECLARED on `:Source` (legacy nodes read back; ONTOLOGY §4.4 lists it), and
    `_update_entity` uses that declaration as its write allowlist. Without the
    server-managed entry, `update_entity(<url>, story_arc=…)` — the generic
    tenant route, reachable from the MCP tool `tortoise_update_entity` — would
    persist §9.1's prose onto a node AND into the `EntityMutated` record, where a
    rebuild restores it: the `__runId` hole (#5196 round 3) one property over.

    (1) FAILS if the key is only DECLARED: the write is accepted and survives.
    (2) REACHABLE: the refusal is checked through the real tenant surface, and
        the node is read afterwards to prove nothing landed.
    """
    sdk = _team_sdk()
    url = f"session:narr-route-{content_hash('narrative-route-probe')[:12]}"
    sdk.create_source(url, "agentSession", contentHash="h1")

    with pytest.raises(ValueError, match="server-managed"):
        sdk.update_entity(url, story_arc="NARRATIVE-ZEBRA prose must not land")

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.story_arc", params={"u": url}
    ).result_set
    assert rows, "the probe Source is missing — the refusal proved nothing"
    assert rows[0][0] is None, (
        "the S1 narrative reached a :Source node through the tenant route — "
        "§9.1/D1 places it in public.session_narrative only (#10000)"
    )
