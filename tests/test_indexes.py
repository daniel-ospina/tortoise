"""Issue #327 — schema-level index tests + plan-shape + behavioral parity.

Verifies: entity-key RANGE indexes are created (CALL db.indexes()), index
creation is idempotent, labeled lookups plan as Node By Index Scan (unlabeled
as All Node Scan — the P0 this issue fixes), and the _resolve_entity /
entity-CRUD / edge / navigation / org-query rewrites preserve behavior.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.projection import FalkorProjection

EXPECTED_RANGE = {
    "Point": ["id", "pointKind", "content_hash", "is_operator"],
    "Document": ["id", "documentKind"],
    "Subject": ["id", "name"],
    "Object": ["id", "name"],
    "Event": ["eventId"],
    "Source": ["id", "url"],
}


@pytest.fixture
def proj():
    p = FalkorProjection(f"{tempfile.mkdtemp(prefix='tt_idx_')}/t.db",
                         allow_nonstandard_path=True)
    yield p
    p.close()


@pytest.fixture
def sdk():
    from tortoise.sdk import TortoiseSDK
    s = TortoiseSDK(f"{tempfile.mkdtemp(prefix='tt_sdkidx_')}/t.db")
    yield s
    s.close()


def _range_indexes(proj):
    """Return {label: {field: [types]}} from CALL db.indexes()."""
    rows = proj.g.query("CALL db.indexes()").result_set
    out = {}
    for row in rows:
        label, fields, types = row[0], row[1], row[2]
        types = dict(types)
        out[label] = {f: types.get(f, []) for f in fields}
    return out


# ── Task 1: index existence + idempotency ────────────────────────────────

def test_entity_key_indexes_exist(proj):
    idx = _range_indexes(proj)
    for label, fields in EXPECTED_RANGE.items():
        assert label in idx, f"no indexes for {label}: {idx}"
        for f in fields:
            assert "RANGE" in idx[label].get(f, []), \
                f"{label}.{f} index missing: {idx[label]}"


def test_indexes_idempotent_reinit():
    db_path = f"{tempfile.mkdtemp(prefix='tt_idxre_')}/t.db"
    p1 = FalkorProjection(db_path, allow_nonstandard_path=True)
    p1.close()
    # Second init on the same graph must not raise ("already indexed" fast path)
    p2 = FalkorProjection(db_path, allow_nonstandard_path=True)
    idx = _range_indexes(p2)
    assert "Point" in idx and "Subject" in idx and "Source" in idx
    p2.close()


# ── Task 2: _resolve_entity union helper ─────────────────────────────────

def test_resolve_entity_branches(proj):
    g = proj.g
    g.query("CREATE (p:Point {id:'pt1', content:'c', pointKind:'statement', is_operator:false})")
    g.query("CREATE (s:Subject {id:'sj1', name:'alice', subjectKind:'person'})")
    g.query("CREATE (o:Object {id:'ob1', name:'widget', objectKind:'tool'})")
    g.query("CREATE (e:Event {eventId:'ev1', eventKind:'meeting'})")
    g.query("CREATE (src:Source {url:'http://a', id:'http://a', sourceKind:'document'})")
    g.query("CREATE (stub:Source {url:'http://stub'})")  # url-only, no id

    by_id = {r["label"]: r for r in proj._resolve_entity("pt1")}
    assert by_id["Point"]["key"] == "id"
    assert by_id["Point"]["properties"]["pointKind"] == "statement"

    by_event = proj._resolve_entity("ev1", by_id=False, by_eventId=True)
    assert [r["label"] for r in by_event] == ["Event"]
    assert by_event[0]["key"] == "eventId"

    srcs = proj._resolve_entity("http://a")  # Source id==url -> dedup to ONE
    assert [r["label"] for r in srcs] == ["Source"] and len(srcs) == 1

    stub = proj._resolve_entity("http://stub", by_url=True)
    assert len(stub) == 1 and stub[0]["label"] == "Source" and stub[0]["key"] == "url"

    assert len(proj._resolve_entity("http://a", by_id=False, by_eventId=False,
                                    by_url=True)) == 1  # url-only match
    assert not proj._resolve_entity("http://missing", by_id=True, by_eventId=True,
                                    by_url=True)


# ── Task 3: sdk entity CRUD parity ───────────────────────────────────────

def test_get_entity_parity_all_types(sdk):
    """Each canonical entity type resolves via its key (issue #327)."""
    sdk.create_point("statement", "hello world")
    sdk.create_subject("parity-subj", "role")
    sdk.create_event("parity-ev", "meeting")
    proj = sdk._get_proj()
    proj.g.query("CREATE (src:Source {url:'http://parity', id:'http://parity'})")
    # Point by id
    pid = [p["id"] for p in sdk.query(kind="statement")
           if p["content"] == "hello world"][0]
    ent = sdk.get_entity(pid)
    assert ent and ent.get("pointKind") == "statement"
    # Subject by id
    sid = proj.g.query("MATCH (s:Subject {name:'parity-subj'}) RETURN s.id").result_set[0][0]
    assert sdk.get_entity(sid).get("name") == "parity-subj"
    # Event by eventId (covers id==eventId invariant)
    eid = proj.g.query("MATCH (e:Event {eventKind:'meeting'}) RETURN e.eventId").result_set[0][0]
    assert sdk.get_entity(eid).get("eventKind") == "meeting"
    # Source by url
    assert sdk.get_entity("http://parity").get("url") == "http://parity"
    # every node in the graph is resolvable by one of id/eventId/url
    rows = proj.g.query(
        "MATCH (n) RETURN n.id, labels(n)[0], n.url, n.eventId").result_set
    assert rows, "no nodes created"
    for r in rows:
        ident = r[0] or r[2] or r[3]
        assert sdk.get_entity(ident), f"get_entity({ident}) returned empty"


def test_get_entity_stub_source_by_url(sdk):
    proj = sdk._get_proj()
    proj.g.query("CREATE (p:Point {id:'pt1', content:'x', pointKind:'statement', is_operator:false})")
    proj.g.query("MERGE (s:Source {url:'http://stub'}) ON CREATE SET s.sourceKind='doc'")
    proj.g.query("MATCH (p:Point {id:'pt1'}), (s:Source {url:'http://stub'}) "
                 "MERGE (p)-[:extractedFrom]->(s)")
    ent = sdk.get_entity("http://stub")
    assert ent and ent.get("url") == "http://stub"


def test_update_delete_entity_parity(sdk):
    sdk.create_point("statement", "upd del test")
    pid = [p["id"] for p in sdk.query(kind="statement")
           if p["content"] == "upd del test"][0]
    e = sdk.update_entity(pid, note="hi")
    assert e.get("note") == "hi"
    assert sdk.delete_entity(pid) is True
    assert sdk.get_entity(pid) == {}


def test_get_entity_session_excluded(sdk):
    proj = sdk._get_proj()
    proj.g.query("CREATE (s:Session {id:'sess-1'})")
    assert sdk.get_entity("sess-1") == {}  # documented exclusion (issue #327)


# ── Task 4: edges parity ─────────────────────────────────────────────────

def test_create_edge_hetero(sdk):
    sdk.create_point("statement", "edge test")
    sdk.create_subject("carol", "role")
    proj = sdk._get_proj()
    pid = [p["id"] for p in sdk.query(kind="statement")
           if p["content"] == "edge test"][0]
    sid = proj.g.query("MATCH (s:Subject {name:'carol'}) RETURN s.id").result_set[0][0]
    assert proj.create_edge(pid, sid, "authoredBy") is True
    n = proj.g.query(
        "MATCH (p:Point {id:$pid})-[:authoredBy]->(s:Subject {id:$sid}) RETURN count(*)",
        params={"pid": pid, "sid": sid}).result_set[0][0]
    assert n == 1


def test_create_edge_url_stub_target_returns_false(sdk):
    proj = sdk._get_proj()
    proj.g.query("CREATE (p:Point {id:'pt1', content:'x', pointKind:'statement', is_operator:false})")
    proj.g.query("CREATE (src:Source {url:'http://only-url'})")  # no id
    ok = proj.create_edge("pt1", "http://only-url", "references")
    assert ok is False  # target OR-set is id|eventId — url-only stub not matched
    n = proj.g.query(
        "MATCH (:Point {id:'pt1'})-[:references]->(:Source {url:'http://only-url'}) "
        "RETURN count(*)").result_set[0][0]
    assert n == 0


def test_create_about_edge_parity(sdk):
    sdk.create_point("statement", "about alice")
    sdk.create_subject("alice", "person")
    proj = sdk._get_proj()
    pid = [p["id"] for p in sdk.query(kind="statement")
           if p["content"] == "about alice"][0]
    sid = proj.g.query("MATCH (s:Subject {name:'alice'}) RETURN s.id").result_set[0][0]
    assert proj.create_about_edge(pid, sid, "aboutSubject") is True


def test_create_owned_by_cycle_still_raises(sdk):
    sdk.create_subject("orgA", "organization")
    sdk.create_subject("orgB", "organization")
    proj = sdk._get_proj()
    a = proj.g.query("MATCH (s:Subject {name:'orgA'}) RETURN s.id").result_set[0][0]
    b = proj.g.query("MATCH (s:Subject {name:'orgB'}) RETURN s.id").result_set[0][0]
    proj.create_owned_by(a, b)
    with pytest.raises(ValueError):
        proj.create_owned_by(b, a)


# ── Task 5: navigation parity + plan-shape ───────────────────────────────

def test_navigation_parity_real_graph():
    from tortoise.navigation import entityProfile, tortoise_traverse
    p = FalkorProjection(f"{tempfile.mkdtemp(prefix='tt_navidx_')}/t.db",
                         allow_nonstandard_path=True)
    g = p.g
    g.query("CREATE (a:Point {id:'p1', content:'c1', pointKind:'statement', is_operator:false})")
    g.query("CREATE (b:Point {id:'p2', content:'c2', pointKind:'statement', is_operator:false})")
    g.query("MATCH (a:Point {id:'p1'}), (b:Point {id:'p2'}) CREATE (a)-[:IMPL]->(b)")
    prof = entityProfile(p.db, "tortoise", "p1", hops=1)
    assert prof["entity"]["id"] == "p1"
    assert any(n.get("id") == "p2" for n in prof["connected"]["points"])
    trav = tortoise_traverse(p.db, "tortoise", "p1", max_hops=1)
    assert trav["entity"]["id"] == "p1" and len(trav["nodes"]) >= 1
    p.close()


def test_navigation_bfs_uses_index_scan():
    p = FalkorProjection(f"{tempfile.mkdtemp(prefix='tt_navidx2_')}/t.db",
                         allow_nonstandard_path=True)
    g = p.g
    g.query("CREATE (a:Point {id:'p1', content:'c', pointKind:'statement', is_operator:false})")
    plan_out = str(g.explain("MATCH (n:Point)-[r]->(m) WHERE n.id = 'p1' RETURN m"))
    assert "Node By Index Scan" in plan_out
    plan_in = str(g.explain("MATCH (n:Point)<-[r]-(m) WHERE n.id = 'p1' RETURN m"))
    assert "Node By Index Scan" in plan_in and "All Node Scan" not in plan_in
    plan_trap = str(g.explain("MATCH (n)<-[r]-(m:Point) WHERE n.id = 'p1' RETURN m"))
    assert "All Node Scan" in plan_trap  # regression trap: neighbor-labeled
    p.close()


# ── Task 6: org queries parity ───────────────────────────────────────────

def test_org_queries_parity(sdk):
    sdk.create_subject("root-org", "organization")
    sdk.create_subject("member-1", "role")
    proj = sdk._get_proj()
    root = proj.g.query("MATCH (s:Subject {name:'root-org'}) RETURN s.id").result_set[0][0]
    m1 = proj.g.query("MATCH (s:Subject {name:'member-1'}) RETURN s.id").result_set[0][0]
    proj.g.query("MATCH (p:Subject {id:$m}), (s:Subject {id:$r}) MERGE (p)-[:memberOf]->(s)",
                 params={"m": m1, "r": root})
    org = sdk.get_org_structure(root)
    assert any(m["id"] == m1 for m in org["members"])
    org2 = sdk.get_org_structure("root-org")
    assert org2["members"] == org["members"]
    proj.create_owned_by(m1, root)  # (entity, subject): m1 owned by root
    owned = sdk.get_owned_entities(root)
    assert any(e["id"] == m1 for e in owned)


# ── Task 7: plan-shape battery ───────────────────────────────────────────

def test_unlabeled_lookup_is_all_node_scan(proj):
    g = proj.g
    g.query("CREATE (a:Point {id:'p1', content:'x', pointKind:'statement', is_operator:false})")
    plan = str(g.explain("MATCH (n) WHERE n.id = 'p1' RETURN n"))
    assert "All Node Scan" in plan  # documents why the issue is P0


def test_labeled_lookups_are_index_scans(proj):
    g = proj.g
    g.query("CREATE (s:Subject {id:'s1', name:'x', subjectKind:'team'})")
    for q in (
        "MATCH (n:Subject {id:'s1'}) RETURN n",
        "MATCH (n:Subject) WHERE n.name = 'x' RETURN n",
        "MATCH (n:Object) WHERE n.id = 's1' RETURN n",
        "MATCH (n:Event) WHERE n.eventId = 's1' RETURN n",
        "MATCH (n:Source) WHERE n.url = 's1' RETURN n",
        "MATCH (n:Subject) WHERE n.id = 's1' OR n.name = 'x' RETURN n",
    ):
        plan = str(g.explain(q))
        assert "Node By Index Scan" in plan, f"not index-backed: {q} -> {plan}"


def test_resolve_entity_queries_use_index_scans(proj):
    """The _resolve_entity union branches must each be index-backed."""
    g = proj.g
    g.query("CREATE (p:Point {id:'pt1', content:'x', pointKind:'statement', is_operator:false})")
    branches = proj._RESOLVE_BRANCHES
    for label, prop in branches:
        plan = str(g.explain(f"MATCH (n:{label}) WHERE n.{prop} = 'pt1' RETURN n"))
        assert "Node By Index Scan" in plan, f"{label}.{prop} not index-backed: {plan}"
