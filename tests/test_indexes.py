"""Issue #327 — schema-level index tests + plan-shape + behavioral parity.

Verifies: entity-key RANGE indexes are created (CALL db.indexes()), index
creation is idempotent, labeled lookups plan as Node By Index Scan (unlabeled
as All Node Scan — the P0 this issue fixes), and the _resolve_entity /
entity-CRUD / edge / navigation / org-query rewrites preserve behavior.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile
import uuid
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.projection import FalkorProjection

EXPECTED_RANGE_EMBEDDED = {
    # is_operator is intentionally absent on EVERY backend (#522 embedded,
    # #3154 docker/server). Embedded: falkordblite degrades the bool type
    # table across close/reopen, so the indexed `= false` form silently
    # returns 0 after restart. Docker/server: GRAPH.COPY drops the `false`
    # postings of a copied boolean RANGE index, so a copy whose index set
    # carries is_operator in its SDK-created position reads 0 for `= false`
    # — and the copy destination cannot rebuild it (a fresh CREATE on a copy
    # is corrupt too). The full label scan is correct on both. See
    # _ensure_indexes.
    "Point": ["id", "pointKind", "content_hash"],
    "Document": ["id", "documentKind"],
    "Subject": ["id", "name"],
    "Object": ["id", "name"],
    "Event": ["eventId"],
    "Source": ["id", "url"],
    # #2600: actor-filtered session reads (list_sessions?actor_user_id) must
    # be an index seek on both lanes — plain string single-prop RANGE index
    # (embedded-safe, mirrors the entity string indexes above).
    "Session": ["actor_user_id"],
}

# ── Epic #1647 T8 (D5/D6): docker sibling expectations ────────────────────
# D5 (research-brief §2.1): the RANGE sets are IDENTICAL on both engines —
# point_props (id, pointKind, content_hash) is not mode-split (verified
# _ensure_indexes L1214-1224). #3154: is_operator is deliberately ABSENT from
# the D5 range set on BOTH lanes — NO engine gets a boolean index (#522
# embedded, #3154 docker/server GRAPH.COPY).
EXPECTED_RANGE_DOCKER = {k: list(v) for k, v in EXPECTED_RANGE_EMBEDDED.items()}

# D6: the docker freshness index is the PLAIN lastDreamedAt index. #3154
# retired the (is_operator, lastDreamedAt) composite: GRAPH.COPY can copy a
# boolean RANGE index without its `false` postings on docker/server (the #522
# hazard, verified there too), and `= false` must not depend on it.
EXPECTED_POINT_STALENESS_DOCKER = ("lastDreamedAt",)


@pytest.fixture
def proj():
    tmpdir = tempfile.mkdtemp(prefix="tt_idx_")
    p = FalkorProjection(f"{tmpdir}/t.db",
                         allow_nonstandard_path=True)
    yield p
    p.close()
    # #4096: reclaim this fixture's temp tree on teardown.
    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def sdk():
    from tortoise.sdk import TortoiseSDK
    tmpdir = tempfile.mkdtemp(prefix="tt_sdkidx_")
    s = TortoiseSDK(f"{tmpdir}/t.db")
    yield s
    s.close()
    # #4096: reclaim this fixture's temp tree on teardown.
    shutil.rmtree(tmpdir, ignore_errors=True)


def _range_indexes(proj):
    """Return {label: {field: [types]}} from CALL db.indexes()."""
    rows = proj.g.query("CALL db.indexes()").result_set
    out = {}
    for row in rows:
        label, fields, types = row[0], row[1], row[2]
        # Defensive: 4.x returns a dict keyed by field, but 3.x servers
        # return a flat per-field list (one row per field) — normalize both
        # to {field: [types]}, pairing fields with types positionally.
        types = (dict(types) if isinstance(types, dict)
                 else ({f: [t] for f, t in zip(fields, types)}  # noqa: B905
                       if isinstance(types, (list, tuple))
                       else {f: [types] for f in fields}))
        # Merge per label (3.x emits one row per field) so the result is
        # label-complete regardless of row order.
        out.setdefault(label, {}).update({f: types.get(f, []) for f in fields})
    return out


# ── Non-embedded (docker/server) gate (#522) ─────────────────────────────
# Mirrors tests/test_search_engine_gaps.py: probe candidate URIs once at
# import; the docker-gated test below skips when no non-embedded FalkorDB is
# reachable (CI runs embedded-only via FalkorDBLite).
FALKORDB_AVAILABLE = False
_WORKING_URI: str | None = None


def _probe_falkordb(candidates: list[str | None]) -> tuple[bool, str | None]:
    """Probe candidate URIs for a live non-embedded FalkorDB."""
    _env_uri = os.environ.get("TORTOISE_DB_URI")
    for _uri in candidates:
        if not _uri:
            continue
        _proj = None
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection.from_uri(_uri)
            _proj.g.query("RETURN 1")
            return True, _uri
        except Exception:
            if _uri == _env_uri and _uri:
                break  # env-specified DB unreachable — don't fall through (#196)
            continue
        finally:
            if _proj is not None:
                try:  # noqa: SIM105
                    _proj.close()
                except Exception:
                    pass
    return False, None


_uri_candidates = [
    os.environ.get("TORTOISE_DB_URI"),
    "docker://:falkordb@localhost:6379/tortoise_test_idx522",
    "docker://:@localhost:16379/tortoise_test_idx522",
]
FALKORDB_AVAILABLE, _WORKING_URI = _probe_falkordb(_uri_candidates)


# ── Task 1: index existence + idempotency ────────────────────────────────

def test_entity_key_indexes_exist(proj):
    idx = _range_indexes(proj)
    for label, fields in EXPECTED_RANGE_EMBEDDED.items():
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
    pid = [p["id"] for p in sdk.query(kind="statement")  # noqa: RUF015
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
    # every CANONICAL entity node is resolvable by one of id/eventId/url.
    # Internal event-store nodes (:GraphEvent/:GraphEventMeta — emitted by
    # create_point's PointAdded via event_store, #432) and the graph-wide
    # :EpMeta EP-epoch bookkeeping node (#1163) are intentionally OUT of
    # scope for entity resolution. (_get_entity resolves only the 6
    # canonical labels; GraphEvent keys on event_id, not id/eventId/url —
    # #647 sweep catch).
    rows = proj.g.query(
        "MATCH (n) RETURN n.id, labels(n)[0], n.url, n.eventId").result_set
    assert rows, "no nodes created"
    INTERNAL = {"GraphEvent", "GraphEventMeta", "EpMeta", "Meta"}
    # "Meta" (epic #1541): the FTS migration markers (:Meta {key:
    # 'point_fts_v2'|'event_fts_v2'}) are internal bookkeeping written by
    # _ensure_indexes on fresh DBs — same class as EpMeta, never a user
    # entity. (#647 sweep catch — the test predates the marker.)
    for r in rows:
        label = r[1]
        if label in INTERNAL:
            continue  # internal event-store records, not user entities
        ident = r[0] or r[2] or r[3]
        # Robustness (#647 review P2): a non-canonical node WITHOUT any
        # identifier must fail as a clean labeled assertion, not call
        # get_entity(None) (unbound param-binding error).
        assert ident is not None, f"node {label} has no id/url/eventId and is not in INTERNAL"
        assert sdk.get_entity(ident), f"get_entity({ident}) returned empty for {label}"


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
    pid = [p["id"] for p in sdk.query(kind="statement")  # noqa: RUF015
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
    pid = [p["id"] for p in sdk.query(kind="statement")  # noqa: RUF015
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
    pid = [p["id"] for p in sdk.query(kind="statement")  # noqa: RUF015
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
    # Epic #1647 P4 (Task 10): query by the projection's ACTUAL graph name —
    # the literal "tortoise" matched only the embedded default; under a
    # docker session the redirect derives per-path names
    # (test_<stem>_<hash12>), so a hardcoded name would look in the wrong
    # graph (KeyError 'id'). p.graph_name is the truth on both lanes.
    prof = entityProfile(p.db, p.graph_name, "p1", hops=1)
    assert prof["entity"]["id"] == "p1"
    assert any(n.get("id") == "p2" for n in prof["connected"]["points"])
    trav = tortoise_traverse(p.db, p.graph_name, "p1", max_hops=1)
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


# ── Task 8: is_operator index regression (#522 embedded, #3154 docker) ──
# falkordblite/redislite degrades the persisted bool type table across
# close/reopen: indexed `= false` lookups silently return 0 after restart,
# while label scans coerce correctly (TRUE lookups survive, FALSE do not).
# #3154 extended the policy to docker/server: GRAPH.COPY can copy a boolean
# RANGE index without its `false` postings (when the source's index set
# carries is_operator in its SDK-created position). is_operator is therefore
# never indexed on ANY engine — every backend drops any stale persisted copy
# on open — see _ensure_indexes.

@pytest.mark.embedded_only
# Epic #1647 P4 (Task 10): the test drives the embedded `db_path` fixture
# (session 1 seeds a pre-#522 stale index through the embedded store), so it
# stays embedded_only. The purged code path itself is no longer
# embedded-exclusive — #3154 extended the purge to docker/server (covered by
# the D7 conformance test test_d7_boolean_index_purge). Marked with the
# D-2=A mechanism: visible skip on docker sessions, runs embedded in
# URI-less runs (the marker is the documented embedded-only surface).
def test_embedded_reopen_false_equality_correct():
    """After close/reopen, non-operator lookups must not silently empty.

    Session 1 seeds the PRE-FIX stale state (a pre-#522 build persisted the
    is_operator RANGE index), so session 2 exercises the actual migration
    path: _ensure_indexes must DROP the stale embedded index on open and the
    `= false` sweep must return the full non-operator set.
    """
    from tortoise.sdk import TortoiseSDK
    db_path = f"{tempfile.mkdtemp(prefix='tt_boolidx_')}/t.db"
    sdk = None
    sdk2 = None
    try:
        sdk = TortoiseSDK(db_path)
        a = sdk.create_point("statement", "bool-a")
        b = sdk.create_point("statement", "bool-b")
        sdk.create_operator("IMPL", a["id"], [b["id"]], label="op1")
        # Simulate the pre-fix state: a prior embedded build persisted the
        # is_operator RANGE index. It must be dropped by _ensure_indexes on
        # the next open, or `= false` silently returns 0 after reopen.
        proj1 = sdk._get_proj()
        proj1.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator)")
        sdk.close()
        sdk = None

        sdk2 = TortoiseSDK(db_path)
        proj = sdk2._get_proj()
        g = proj.g
        # (a) The stale index was dropped on open — the healing mechanism ran.
        idx = _range_indexes(proj)
        assert "RANGE" not in idx.get("Point", {}).get("is_operator", []), \
            f"embedded must drop stale is_operator index on open: {idx}"
        # (b) The #522 load-bearing form returns the full non-operator set.
        assert g.query("MATCH (n:Point) WHERE n.is_operator = false "
                       "RETURN count(n)").result_set[0][0] == 2
        # (c) The IS NULL disjunction is also unaffected.
        assert g.query("MATCH (n:Point) WHERE (n.is_operator IS NULL "
                       "OR n.is_operator = false) "
                       "RETURN count(n)").result_set[0][0] == 2
        # (d) Operator lookups unaffected.
        assert g.query("MATCH (n:Point {is_operator:true}) "
                       "RETURN count(n)").result_set[0][0] == 1
        assert g.query("MATCH (n:Point) WHERE n.is_operator = true "
                       "RETURN count(n)").result_set[0][0] == 1
    finally:
        if sdk is not None:
            sdk.close()
        if sdk2 is not None:
            sdk2.close()


def _current_uri() -> str:
    """Resolve the non-embedded URI at CALL time (mirrors test_search_engine_gaps).

    Prefers a live TORTOISE_DB_URI so a dev machine with a running server
    exercises it; falls back to the module-probe _WORKING_URI.
    """
    return os.environ.get("TORTOISE_DB_URI") or (
        _WORKING_URI or "docker://:falkordb@localhost:6379/tortoise_test_idx522")


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason="FalkorDB not available")
def test_non_embedded_is_operator_not_indexed():
    """#3154: non-embedded FalkorDB must NOT index the boolean property."""
    from tortoise.projection import FalkorProjection
    uri = _current_uri()
    # Round-2 guard: the URI was probed at import time and may resolve to a
    # LIVE non-test graph (dev machine with a real DB). The DETACH DELETE
    # below would trip _assert_test_graph and hard-fail the suite — skip
    # instead (same "no test server" semantics as FALKORDB_AVAILABLE).
    if not urlparse(uri).path.lstrip("/").startswith(("test_", "tortoise_test")):
        pytest.skip(f"resolved URI {uri!r} is not a test graph "
                    "(graph name must start with 'test_'/'tortoise_test_')")
    # Epic #1647 (T7): per-test graph — the env/job URI path is shared and
    # this fixture bulk-DETACHes its graph on every test.
    proj = FalkorProjection.from_uri(
        uri, graph_name=f"test_indexes_range_{os.urandom(4).hex()}")
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj._ensure_indexes()
        # #3154: no boolean index on docker/server — `= false` rides the
        # correct label scan (such a copied boolean index reads 0).
        idx = _range_indexes(proj)
        assert "RANGE" not in idx.get("Point", {}).get("is_operator", []), \
            f"non-embedded must NOT index the boolean property (#3154): {idx}"
        proj.g.query("CREATE (a:Point {id:'pa', content:'a', "
                     "pointKind:'statement', is_operator:false})")
        proj.g.query("CREATE (b:Point {id:'pb', content:'b', "
                     "pointKind:'statement', is_operator:false})")
        proj.g.query("CREATE (c:Point {id:'pc', content:'c', "
                     "pointKind:'statement', is_operator:true})")
        # The #522/#3154 load-bearing form returns the full non-operator set.
        assert proj.g.query("MATCH (n:Point) WHERE n.is_operator = false "
                            "RETURN count(n)").result_set[0][0] == 2
    finally:
        proj.close()


@pytest.mark.skipif(not FALKORDB_AVAILABLE,
                    reason="FalkorDB not available")
def test_docker_lane_index_shape():
    """Epic #1647 T8 (D5/D6): docker sibling of test_entity_key_indexes_exist.

    The D5 range sets are IDENTICAL to embedded (EXPECTED_RANGE_DOCKER);
    #3154: docker now also gets the PLAIN lastDreamedAt staleness index — the
    (is_operator, lastDreamedAt) composite is retired on both engines (a
    boolean RANGE index can be copied without its `false` postings by
    GRAPH.COPY, the same #522 hazard verified on docker/server). is_operator
    is never in the D5 range set.
    """
    from urllib.parse import urlparse
    uri = _current_uri()
    # Round-2 guard (same as test_non_embedded_is_operator_not_indexed): the
    # probe may resolve to a LIVE non-test graph — skip rather than DETACH a
    # real DB (graph name must start with test_/tortoise_test_).
    if not urlparse(uri).path.lstrip("/").startswith(("test_", "tortoise_test")):
        pytest.skip(f"resolved URI {uri!r} is not a test graph "
                    "(graph name must start with 'test_'/'tortoise_test_')")
    proj = FalkorProjection.from_uri(
        uri, graph_name=f"test_indexes_docker_{os.urandom(4).hex()}")
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj._ensure_indexes()
        idx = _range_indexes(proj)
        for label, fields in EXPECTED_RANGE_DOCKER.items():
            assert label in idx, f"no indexes for {label}: {idx}"
            for f in fields:
                assert "RANGE" in idx[label].get(f, []), \
                    f"{label}.{f} index missing: {idx[label]}"
        rows = proj.g.query("CALL db.indexes()").result_set
        point_rows = [r for r in rows if r[0] == "Point"]
        assert point_rows, f"no Point indexes on docker: {rows}"
        fields = " ".join(str(r[1]) for r in point_rows)
        # #3154: the plain lastDreamedAt staleness index is present; the
        # boolean property is NOT indexed (GRAPH.COPY can drop the `false`
        # postings of a copied boolean index).
        assert all(p in fields for p in EXPECTED_POINT_STALENESS_DOCKER), (
            f"docker must keep the {EXPECTED_POINT_STALENESS_DOCKER} "
            f"staleness index, got {fields}")
        assert "is_operator" not in fields, (
            f"docker must not index the boolean property (#3154): {fields}")
    finally:
        with contextlib.suppress(Exception):  # tidy (epic #1647 review P2):
            # this from_uri mint is not journaled in URI-unset sessions —
            # drop it explicitly
            proj.db.select_graph(proj.graph_name).delete()
        proj.close()


# ── #4465: the schema-completeness guard on the bootstrap sweep ───────────
#
# `_ensure_indexes` is idempotent but pays ~26 graph round-trips of DDL that
# is already satisfied on every construction after the first. Measured on the
# hosted capture path: one 2-turn capture constructs ~12 projections (7 of
# them on the event-loop thread), and 180+ of the capture's ~217 on-loop
# queries were that repeated, already-satisfied DDL. The guard answers the
# same question from the engine's own index catalogue — for Point that is two
# bounded reads: the catalogue, plus the array-valued `search_keys`
# precondition. These tests pin each property the guard's correctness rests
# on:
# it is TRUE only when the schema really is complete, it is FALSE for every
# shape that still needs the sweep, and it is keyed on the GRAPH (not on
# process memory, which goes stale the moment a graph is re-created).

#: Statements a fully-indexed graph makes redundant — their presence in a
#: repeat `_ensure_indexes()` call means the sweep ran again. The vector-index
#: API probe is deliberately NOT here: resolving whether the engine registers
#: `db.idx.vector.createNodeIndex` or only the Cypher-native form is 1–2
#: round trips that `CALL db.indexes()` cannot answer, and it is not repeated
#: *schema* work.
_REDUNDANT_ON_INDEXED_GRAPH = (
    "CREATE INDEX FOR",
    "DROP INDEX ON",
)


def _is_repeated_schema_work(cypher: str) -> bool:
    """A statement that may only run on a graph that still needs BUILDING.

    DDL, the marker WRITE, and the fixup's node-level ``SET`` (which the
    earlier form missed — a repeat sweep that flattened every Point again
    would otherwise have counted as clean). The fixup's precondition READS are
    deliberately NOT in this set (#5444): the fast path must read the fixup's
    state to know whether it is owed, and forbidding that read is what made
    the marker-gate attempt unusable. ``_is_fast_path_read`` bounds them
    instead — reads are allowed, writes and DDL are not, so a future "probe by
    rebuilding" still cannot hide here.
    """
    return (cypher.startswith(_REDUNDANT_ON_INDEXED_GRAPH)
            or "fulltext.createNodeIndex" in cypher
            or "fulltext.drop" in cypher
            or cypher.startswith("MERGE (m:Meta")
            or "SET n.search_keys" in cypher)


#: The ONLY extra statement the fast path may issue (#5444): the cap-immune
#: fixup precondition. It is the only arm left — the ``point_fts_v2`` marker is
#: deliberately NOT consulted, because "marker present" does not mean "fixup
#: done": `sdk.update_entity` (the `surface.update_entity` MCP tool) writes raw
#: `SET n += $p` props without flattening. Pinned to the EXACT literal rather
#: than by prefix (#5312 review, P2): a prefix match classifies ANY reworded
#: statement as a permitted "read" — including a destructive one such as
#: ``MATCH (n:Point) WHERE n.search_keys IS NOT NULL DETACH DELETE n``.
_FAST_PATH_READS = (
    "MATCH (n:Point) WHERE n.search_keys IS NOT NULL "
    "AND typeof(n.search_keys) = 'List' RETURN 1 LIMIT 1",
)

#: Cypher DESTRUCTIVE verbs. A repeat sweep must issue none: the fixup — the
#: only writer — belongs to the first sweep. Deliberately NOT the broad
#: ``CREATE``: the two vector-API attempts are expected here and one of them is
#: a ``CREATE VECTOR INDEX``. This replaces a `" SET "`/`" MERGE "` blacklist
#: applied to the reads only, which a reworded destructive statement could
#: evade (#5312 review, P2).
_DESTRUCTIVE_VERBS = (" DETACH DELETE ", " DELETE ", " REMOVE ", " SET ",
                      " MERGE ")


def _is_fast_path_read(cypher: str) -> bool:
    return " ".join(cypher.split()) in _FAST_PATH_READS


def _is_destructive(cypher: str) -> bool:
    padded = " " + " ".join(cypher.split()).upper() + " "
    return any(v in padded for v in _DESTRUCTIVE_VERBS)


@pytest.fixture
def graph_factory():
    """Build ISOLATED graphs on whichever backend this lane uses.

    The guard is behavioural — it reads the graph's own index catalogue — so
    the tests must own the graph they assert on: the shared session graph is
    already bootstrapped by earlier tests, which would make "the sweep did
    not rerun" vacuous. A ``test_``-prefixed ``graph_name`` is honored
    verbatim on embedded AND under the #1647 test redirect, so one recipe
    serves both lanes.
    """
    tmpdir = tempfile.mkdtemp(prefix="tt_4465_")
    db_path = f"{tmpdir}/t.db"
    made: list = []

    def _make():
        proj = FalkorProjection(
            db_path,
            graph_name=f"test_4465_guard_{uuid.uuid4().hex[:8]}",
            allow_nonstandard_path=True,
        )
        made.append(proj)
        return proj

    try:
        yield _make
    finally:
        for proj in made:
            with contextlib.suppress(Exception):
                proj.db.select_graph(proj.graph_name).delete()
            proj.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_schema_probe_is_true_only_on_a_complete_schema(graph_factory):
    """A bootstrapped graph reads current; a graph missing ONE required
    index does not — and the next sweep restores it."""
    proj = graph_factory()
    assert proj._schema_is_current() is True, (
        "a freshly bootstrapped graph must read as current")

    proj.g.query("DROP INDEX ON :Point(lastDreamedAt)")
    assert proj._schema_is_current() is False, (
        "a graph missing a required RANGE index must NOT read as current — "
        "the guard would skip the very DDL that recreates it (#4465)")

    proj._ensure_indexes()
    assert proj._schema_is_current() is True


def test_schema_probe_rejects_the_boolean_is_operator_index(graph_factory):
    """#3154: a graph carrying the poisoned boolean index is NOT current.

    The guard's `is_operator` arm is what keeps the #3154 purge reachable on
    a graph that is otherwise fully indexed — without it the fast path would
    adopt the corrupt index forever (#3154 is a silent-wrong-answer defect,
    not a slow one).
    """
    proj = graph_factory()
    proj.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator)")
    assert proj._schema_is_current() is False, (
        "a boolean is_operator index must fail the probe (#3154)")

    proj._ensure_indexes()
    assert proj._schema_is_current() is True
    rows = proj.g.query("CALL db.indexes()").result_set
    assert not any("is_operator" in str(row[1]) for row in rows), (
        f"the sweep must purge the boolean index (#3154): {rows}")


def test_schema_probe_is_per_graph_not_per_process(graph_factory):
    """The guard is read from the GRAPH, so one graph's drift cannot make
    another read current.

    Mutation check (must stay true): replacing the probe with a process-wide
    "already bootstrapped" set keyed on anything coarser than the graph
    (the endpoint, the org, a module global) makes the last assertion fail —
    graph B's dropped index would be invisible to graph A.
    """
    a = graph_factory()
    b = graph_factory()
    assert a._schema_is_current() is True
    assert b._schema_is_current() is True

    b.g.query("DROP INDEX ON :Point(lastDreamedAt)")
    assert b._schema_is_current() is False
    assert a._schema_is_current() is True, (
        "graph A must NOT inherit graph B's drift — a guard keyed on "
        "anything but the graph is a cross-tenant correctness bug (#4465)")


def test_schema_probe_notices_a_recreated_graph(graph_factory):
    """A graph dropped and re-created in-process is NOT current.

    This is the case a process-wide flag gets wrong: the NAME is unchanged,
    so the flag still reads "indexed" while the new graph carries no indexes
    — and `required_embedding_dim` would then advertise a vector index that
    does not exist. Re-reading the catalogue cannot go stale.

    Recreating the graph is exactly what an org-graph delete, a
    ``GRAPH.COPY`` restore or an embedded recovery leaves behind.
    """
    proj = graph_factory()
    assert proj._schema_is_current() is True
    proj.db.select_graph(proj.graph_name).delete()
    assert proj._schema_is_current() is False, (
        "the re-created graph has no indexes — a process-wide cache would "
        "still report the OLD graph as bootstrapped (#4465)")


def test_repeat_sweep_runs_no_already_satisfied_ddl(graph_factory):
    """#4465: re-running `_ensure_indexes` on an indexed graph is ONE probe.

    This is the defect in isolation: the sweep is idempotent, so a second
    call is a no-op semantically — but it used to be 26 graph round-trips of
    it, on the event loop, ~7 times per hosted capture.

    Mutation check (must stay true): removing the `_schema_is_current()`
    guard from `_ensure_indexes` puts the full ~26-statement sweep back in
    `seen` and fails here.
    """
    proj = graph_factory()
    seen: list[str] = []
    graph_cls = type(proj.g)
    orig_query = graph_cls.query

    def _record(self, cypher, *args, **kwargs):
        seen.append(" ".join(cypher.split()))
        return orig_query(self, cypher, *args, **kwargs)

    graph_cls.query = _record
    try:
        proj._ensure_indexes()
    finally:
        graph_cls.query = orig_query

    repeated = [c for c in seen if _is_repeated_schema_work(c)]
    assert repeated == [], (
        "a second `_ensure_indexes()` on an already-indexed graph re-ran "
        f"{len(repeated)} schema statement(s): {repeated[:6]} — the "
        "bootstrap is repeated per construction again (#4465)")

    # #5444: the fast path also READS the fixup's precondition (the
    # array-valued `search_keys`), which is what lets the probe tell an owed
    # fixup from a done one. Bound it: exactly one such read, and never a
    # write, so the check above cannot be evaded by probing via rebuilding.
    # (The `point_fts_v2` marker is NOT read — see `_schema_is_current`.)
    reads = [c for c in seen if _is_fast_path_read(c)]
    assert len(reads) <= 1, (
        f"the fast path issued {len(reads)} precondition read(s): {reads} — "
        "bounded to the single cap-immune array read (#5444)")

    # No DESTRUCTIVE statement may run: the fixup belongs to the first sweep.
    # `_is_destructive` matches the five verbs the fixup itself uses, so this
    # pins "no data write", NOT "no write at all" — the vector-API handle
    # probe below legitimately issues `CREATE VECTOR INDEX`/`createNodeIndex`
    # and is budgeted for separately. This replaces a `" SET "`/`" MERGE "`
    # blacklist applied to the reads only, which a reworded statement could
    # evade; the fast-path list above is pinned to exact literals, so a
    # reworded probe is not counted as a permitted read in the first place.
    writes = [c for c in seen if _is_destructive(c)]
    assert writes == [], (
        f"a second `_ensure_indexes()` issued destructive statement(s): "
        f"{writes} — the fixup must run only on a graph that still owes it "
        "(#5444)")

    assert seen.count("CALL db.indexes()") == 1, (
        f"the guard must cost exactly one catalogue probe, got {seen}")
    # One probe + at most the two vector-API attempts (procedure, then the
    # Cypher-native fallback) — anything more is schema work coming back.
    # The #5444 precondition reads are excluded: they are bounded separately
    # above, and the point of this count is that the SWEEP is gone.
    non_fast = [c for c in seen if not _is_fast_path_read(c)]
    assert len(non_fast) <= 3, (
        f"the repeat sweep is no longer a probe: {non_fast}")


def test_probe_detects_an_owed_search_keys_fixup(graph_factory):
    """#5444: 'two-field index present, marker absent, array ``search_keys``'
    is NOT current — that graph still owes the one-time data fixup.

    The fixup flattens array-valued ``search_keys`` because FalkorDB's
    fulltext index does not index array properties; leaving it owed makes
    those Points permanently invisible to ``queryNodes`` — silent
    unfindability, not a slow path.

    The probe tests the fixup's REAL precondition (an array-valued
    ``search_keys``), never the ``point_fts_v2`` marker: more than one writer
    controls that marker, so "marker present" is not "fixup done".

    Mutation check (must stay true): removing the
    ``_array_valued_search_keys_exist()`` arm from ``_schema_is_current``
    fails assertion (a) below — with the arm gone the early-return condition
    is ``required <= present and fts_required``, which is True for a
    fully-indexed graph, so the probe would report a healthy graph as
    not-current and re-bootstrap it on every construction.

    Assertion (a) pins the state that ISOLATES that arm: the marker absent
    and NO array-valued ``search_keys`` anywhere. That state must read as
    CURRENT, so (a) is both the arm's mutation guard and the
    fast-path-preservation test.
    """
    proj = graph_factory()
    g = proj.g
    g.query("MATCH (n:Point) DETACH DELETE n")
    g.query("MATCH (m:Meta) WHERE m.key='point_fts_v2' DELETE m")

    # (a) Marker ABSENT, no array-valued search_keys → still CURRENT. This is
    # the arm's isolator: only the array check can keep this fast.
    assert proj._schema_is_current() is True, (
        "a marker-less graph that owes no fixup must still read as current — "
        "otherwise every construction re-runs the sweep (the CI regression "
        "that made the marker-only gate unusable, #5444)")

    # (b) The issue's repro state: marker absent AND an array present.
    g.query(
        "CREATE (n:Point {id:'legacy-5444', pointKind:'core:fact', "
        "content:'legacy row', content_hash:'h5444', "
        "search_keys:['fastest 5k','running pb']})")
    assert proj._schema_is_current() is False, (
        "a graph whose search_keys is still an array owes the one-time "
        "fixup and must not read as current (#5444)")

    # (c) The marker must NOT be able to certify an owed fixup. This is the
    # state that falsified the design's invariant: `sdk.update_entity` (the
    # `surface.update_entity` MCP tool) writes raw `SET n += $p` props without
    # flattening, so a graph whose marker is already set can still hold an
    # array. Consulting the marker in the probe (tried, then reverted) made
    # this state permanently invisible — the #5444 defect, relocated.
    g.query("MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true")
    assert proj._schema_is_current() is False, (
        "the point_fts_v2 marker must not be able to hide an owed fixup: a "
        "supported write path stores array search_keys without flattening "
        "(#5312 review, P1)")

    proj._ensure_indexes()

    sk = g.query(
        "MATCH (n:Point {id:'legacy-5444'}) RETURN n.search_keys"
    ).result_set[0][0]
    assert not isinstance(sk, (list, tuple)), (
        f"the sweep must flatten search_keys to a space-joined string, "
        f"got {sk!r}")
    assert "fastest 5k" in str(sk)

    assert g.query(
        "MATCH (m:Meta {key:'point_fts_v2'}) RETURN 1"
    ).result_set, "the sweep must re-mint the marker once the fixup has run"

    try:
        hits = g.query(
            "CALL db.idx.fulltext.queryNodes('Point','fastest 5k') RETURN 1"
        ).result_set
    except Exception:
        hits = None  # engine without the fulltext query procedure
    if hits is not None:
        assert hits, (
            "the repaired Point must be findable by FTS — silence here IS "
            "the #5444 symptom")


def test_legacy_single_field_point_index_is_upgraded_with_flat_data(graph_factory):
    """The legacy branch's gate needs the SCHEMA half too (#5444).

    That branch exists to drop→recreate ``Point(content)`` into
    ``Point(content, search_keys)``. Gating it only on "an array is owed" meant
    a legacy graph whose data is already FLAT never upgraded: the field stayed
    missing, the probe stayed not-current, every construction re-ran the whole
    sweep (#4465's churn, permanently) and ``search_keys`` was never indexed.

    Mutation check (must stay true): dropping
    ``or self._point_fts_search_keys_field_missing()`` from that gate leaves the
    index single-field and fails the ``search_keys in fields`` assertion.
    """
    proj = graph_factory()
    g = proj.g

    dropped = False
    for proc in ("db.idx.fulltext.drop", "db.idx.fulltext.dropIndex"):
        try:
            g.query(f"CALL {proc}('Point')")
            dropped = True
            break
        except Exception:
            continue
    if not dropped:
        pytest.skip("engine has no Point fulltext drop procedure (#5440)")

    g.query("MATCH (m:Meta) WHERE m.key='point_fts_v2' DELETE m")
    g.query("MATCH (n:Point) DETACH DELETE n")
    g.query(
        "CREATE (n:Point {id:'legacy-flat', pointKind:'core:fact', "
        "content:'legacy flat row', content_hash:'hlf', "
        "search_keys:'fastest 5k'})")
    g.query("CALL db.idx.fulltext.createNodeIndex('Point', 'content')")

    assert proj._schema_is_current() is False, (
        "a single-field Point FTS index is not the required schema")

    proj._ensure_indexes()

    fields = None
    for row in g.query("CALL db.indexes()").result_set:
        if row and row[0] == "Point":
            fields = set((row[2] or {}).keys())
    assert fields and "search_keys" in fields, (
        "the legacy single-field index must be upgraded to carry search_keys "
        f"even when no array is owed, got {fields} (#5444)")
    assert proj._schema_is_current() is True


def test_schema_probe_ignores_a_similarly_named_boolean_property(graph_factory):
    """`is_operator_flag` is NOT the forbidden `is_operator` index.

    A substring test for `is_operator` matches any property whose name
    contains it, so such a graph reads "not current" FOREVER and pays the
    whole ~26-statement sweep on every construction — the exact churn #4465
    removes. The sibling detector (`hosted_backup
    ._audit_copied_boolean_indexes`) matches exactly for this reason.

    Mutation check (must stay true): reverting to `"is_operator" in
    str(prop)` fails the second assertion.
    """
    proj = graph_factory()
    assert proj._schema_is_current() is True

    proj.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator_flag)")
    assert proj._schema_is_current() is True, (
        "a differently-named property must not disable the fast path — the "
        "substring arm made the guard permanently miss for such a graph")
