"""#14592 Phase A — REST read parity for Objects, Subjects, Sources, relations, belief.

**The defect this file pins.** The product could CREATE four ontology layers
(Point · Object · Subject · Source) and READ back one. Measured on the live
`/openapi.json` before this change: `/v1/objects` and `/v1/subjects` were
POST-only, `/v1/sources` did not exist, and there was no route for relations or
confidence — while every one of those reads already existed behind the MCP door.
An app could hand us knowledge and could not ask *what do you know about? what is
connected to X? how sure are you?*

So each test below asserts a READ against state written through the PUBLIC write
door (or, where no door exists for the shape, through the SDK/repo the route
delegates to), because the class of bug being closed is "the write succeeded and
nothing can read it back" — a test that seeded the graph directly would pass on
the broken tree too if the route were absent … which it was, so the route's own
existence is asserted implicitly by every 200.

⛔ **Nothing here asserts a new MCP tool or a new public SDK method.** Phase A is
REST parity plus reuse: the delegation table is `tortoise/entity_reads.py`'s
module docstring, and the two reads that had no reusable function are free
functions there.

Runnable with the docker lane (a test graph — never `live-v5`):

    uv run python tools/test_lane.py run -- tests/test_14592_read_parity.py -v
"""
from __future__ import annotations

import contextlib
import os
from urllib.parse import quote

import pytest

# #67: mandatory before `tortoise.auth` import in a hosted-API test process.
os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

_ORG = "team-14592"


def _team() -> dict:
    """The org dict `get_current_org` resolves — full limits (a stub missing
    them makes the quota gate 500 instead of passing, #310)."""
    return {
        "org_id": _ORG, "key_id": "test-key-14592", "tier": "free",
        "graph_id": None, "scopes": [], "legacy_full_access": True,
        "delegation_depth": None, "created_by_key_id": None,
        "max_users": 1, "max_graphs": 1, "max_points": 100000,
        "max_api_keys": 2, "max_sessions": None,
    }


def _seed_registry(ha) -> None:
    """The MCP/quota lanes read the quota from the REGISTRY graph, so an org
    that exists nowhere is refused before any write."""
    sdk = ha._make_sdk(namespace="registry")
    try:
        # ⛔ `_query(..., read_only=False)`, not `.query(...)`: no SDK door creates a
        # registry Team under a CALLER-CHOSEN id (`_org_create` mints its own), so
        # this is a raw write, and the vendor's unified verb states which arm it is
        # (`query`/`ro_query` both delegate to it). `.query` on an untyped raw
        # handle is the ordinary-word receiver the privatisation ratchet counts as
        # residue, so a new one is new debt in `surface-guard` — `read_only=False`
        # says "write" out loud instead.
        sdk._get_registry()._query(
            "CREATE (t:Team {id:$id})", params={"id": _ORG}, read_only=False)
    finally:
        sdk.close()


@contextlib.contextmanager
def _graph(db_path):
    from tests._http_fixtures import patched_tortoise_sdk

    with patched_tortoise_sdk(db_path):
        import tortoise.hosted_api as ha

        _seed_registry(ha)
        yield ha


@contextlib.contextmanager
def _rest_client(org=None):
    """A client with the auth dependency overridden.

    ``org`` defaults to the full-access org. The SCOPE tests pass a narrower-key
    factory: ``_require_scope`` short-circuits on ``legacy_full_access`` or
    ``key_id is None``, so a scoped 403 is only reachable through this seam
    (`get_current_org_gated`'s own docstring names the override path as the
    supported test seam).
    """
    from tests._verdict import verdict_client
    from tortoise.hosted_api import app, get_current_org

    app.dependency_overrides[get_current_org] = org or _team
    try:
        with verdict_client(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _sdk(ha):
    """A handle on the SAME graph the routes resolve, for seeding shapes no
    public REST door writes (an edge, a lifecycle status, an EP baseline)."""
    return ha._make_sdk(namespace=_ORG)


def _scoped_key() -> dict:
    """A key holding a data-unrelated scope and NO ``graphs:read``.

    ``_require_scope`` allows ``legacy_full_access`` and session auth
    unconditionally, so a key that must be REFUSED has to be a scoped key that is
    neither.
    """
    return {**_team(), "legacy_full_access": False, "scopes": ["keys:manage"]}


# ── GET /v1/objects ────────────────────────────────────────────────────────

def test_objects_list_reads_back_what_the_write_door_created(tmp_path):
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        for name, kind in (("ACME", "organisation"), ("GROW", "goal")):
            r = client.post("/v1/objects", json={"name": name, "objectKind": kind})
            assert r.status_code == 200, r.text
        r = client.get("/v1/objects")
        assert r.status_code == 200, r.text
        body = r.json()
        names = sorted(o["name"] for o in body["objects"])
        assert names == ["ACME", "GROW"], body
        assert body["count"] == 2, body
        # A complete page is POSITIVELY marked complete, not left un-marked.
        assert body["truncated"] is False, body
        assert "total_available" not in body, body


def test_objects_list_filters_on_the_kind_the_write_door_writes(tmp_path):
    """`objectKind` is the write door's own property; a filter on any other name
    would answer an empty page for a graph full of the thing asked for."""
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "organisation"})
        client.post("/v1/objects", json={"name": "GROW", "objectKind": "goal"})
        r = client.get("/v1/objects", params={"kind": "goal"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [o["name"] for o in body["objects"]] == ["GROW"], body


def test_objects_list_pages_over_a_total_order_and_says_it_truncated(tmp_path):
    """Paging: the marker must be true ONLY while matches remain, over a TOTAL order.

    ⚠️ Three falsifiers, because the first version of this test could not red for
    any of them:

    * **the terminal page must not claim truncation** — computed from
      ``total > page length`` (pre-fix), offsets 0 and 1 said "more remain"
      correctly, but offset 2 (the last page) and offset 3 (past the end, empty)
      ALSO said ``truncated: true``. That is the inverse of #8447: a caller paging
      ``while truncated`` never terminates, and "more remain" becomes
      indistinguishable from "this was the end".
    * **the order must be TOTAL** — two of the four carriers share a NAME
      (``#3590 D2`` makes same-named carriers legal), so ``ORDER BY name`` alone
      is not a total order and each page's separate query returns a non-adjacent
      slice: rows get dropped and repeated while the exact count still reports
      the enumeration complete (``#5346``). With distinct names the mutant was
      indistinguishable, which is why the fixture is not distinct.
    * **the total is EXACT** — a real ``count()`` under the same WHERE, not a
      lower bound wearing a total's clothes.

    ⚠️ The same-name pair is planted BELOW the write door on purpose: the door is
    idempotent by name (measured — two ``POST /v1/objects`` with one name leave
    ONE row, ``count() == 1``), so a fixture built only from it cannot carry the
    pair this test exists to page over.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        for name in ("SAME", "B", "C"):
            client.post("/v1/objects", json={"name": name, "objectKind": "goal"})
        sdk = _sdk(ha)
        try:
            # A WRITE with no door, for the reason above. `_query(...,
            # read_only=False)` states the arm (`_seed_registry` explains why).
            sdk._get_proj().g._query(
                "CREATE (o:Object {id:$id, name:'SAME', objectKind:'goal'})",
                params={"id": "obj-dup-14592"}, read_only=False)
        finally:
            sdk.close()
        seen: list[str] = []
        # 4 rows, one per page: offsets 0-2 have rows behind them, 3 is the
        # terminal page, 4 is PAST the end and empty.
        expected_truncated = {0: True, 1: True, 2: True, 3: False, 4: False}
        for offset, truncated in expected_truncated.items():
            r = client.get("/v1/objects", params={"limit": 1, "offset": offset})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["truncated"] is truncated, (offset, body)
            if truncated:
                assert body["total_available"] == 4, (offset, body)
            else:
                # A complete answer must not advertise a total it did not cut to.
                assert "total_available" not in body, (offset, body)
            seen.extend(o["name"] for o in body["objects"])
        # Every row exactly once, none dropped or repeated — over the same-name
        # pair, which a per-page query on a non-total order cannot guarantee.
        assert sorted(seen) == ["B", "C", "SAME", "SAME"], seen


def test_sources_list_pages_with_the_same_offset_truth(tmp_path):
    """The Python-paged route asks the same question as the store-paged one.

    ``/v1/sources`` pages the whole-population answer in Python, so it carries its
    own ``offset`` into the marker; a fix applied to one route family and not the
    other would leave the same non-terminating walk here.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            for i in range(2):
                sdk._create_point("statement", "extracted",
                                  extractedFrom=f"https://example.test/src-{i}")
        finally:
            sdk.close()
        pages = {
            offset: client.get("/v1/sources",
                               params={"limit": 1, "offset": offset}).json()
            for offset in (0, 1, 2)
        }
        # 2 sources, one per page: offset 0 has one behind it, offset 1 is the
        # terminal page, offset 2 is past the end. Pre-fix all three said True.
        assert [p["truncated"] for p in pages.values()] == [True, False, False], pages
        assert pages[0]["total_available"] == 2, pages
        assert "total_available" not in pages[1], pages
        assert pages[2]["sources"] == [], pages


def test_objects_list_excludes_ended_rows_and_the_id_still_reads(tmp_path):
    """The `/v1/points` tombstone contract, one layer over: ended rows leave the
    listing surface and stay retrievable BY ID."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        node = client.post(
            "/v1/objects", json={"name": "OLD", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            # A WRITE with no door: nothing in the SDK ends an Object (the
            # lifecycle writers are Point-only) and `_delete_entity` would remove
            # the row the by-id arm below asserts is still reachable. The verb is
            # `_query(..., read_only=False)` for the reason `_seed_registry` states.
            sdk._get_proj().g._query(
                "MATCH (o:Object {id:$id}) SET o.status = 'deprecated'",
                params={"id": node["id"]}, read_only=False)
            listed = client.get("/v1/objects").json()
            assert [o["name"] for o in listed["objects"]] == [], listed
            assert client.get(f"/v1/objects/{node['id']}").status_code == 200
        finally:
            sdk.close()


def test_object_by_id_is_scoped_to_its_own_layer(tmp_path):
    """An id that names a Point is a REAL entity this LAYER-scoped route does not
    serve — 404 here, 200 at the route that owns its label.

    ⚠️ The POSITIVE CONTROL in the same test is the falsifier. On the pre-change
    tree there was no ``GET /v1/objects/{id}`` AT ALL, so "the route refuses the
    wrong label" and "the route does not exist" were INDISTINGUISHABLE from a 404
    alone — the review measured this test passing unchanged on `origin/main`.
    Asserting the SAME path answers 200 for a real Object id makes the test
    unable to pass without the route.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a claim", "kind": "statement"}).json()
        obj = client.post("/v1/objects", json={
            "name": "REAL", "objectKind": "goal"}).json()
        r = client.get(f"/v1/objects/{p['id']}")
        assert r.status_code == 404, r.text
        assert client.get(f"/v1/points/{p['id']}").status_code == 200
        assert client.get("/v1/objects/does-not-exist").status_code == 404
        # The positive control: this path DOES serve its own layer.
        assert client.get(f"/v1/objects/{obj['id']}").status_code == 200


def test_by_id_routes_enforce_the_same_scope_as_the_list_routes(tmp_path):
    """A narrower key must not fetch a payload it cannot even LIST.

    Review finding P0: the three by-id handlers delegated to a helper that had no
    `_require_scope`, so `GET /v1/objects` answered 403 while
    `GET /v1/objects/{id}` answered 200 for the SAME key — the contract
    `_require_scope`'s docstring states ("never post-filter: the check is the
    FIRST statement of a data handler"), violated in exactly the direction that
    leaks a payload.
    """
    with _graph(str(tmp_path / "g.db")) as _ha, _rest_client() as client:
        obj = client.post("/v1/objects", json={
            "name": "SCOPED", "objectKind": "goal"}).json()
        who = client.post("/v1/subjects", json={
            "name": "SCOPED-S", "subjectKind": "person"}).json()
    with _graph(str(tmp_path / "g.db")), _rest_client(_scoped_key) as client:
        assert client.get("/v1/objects").status_code == 403
        assert client.get(f"/v1/objects/{obj['id']}").status_code == 403
        assert client.get(f"/v1/subjects/{who['id']}").status_code == 403
        assert client.get("/v1/sources").status_code == 403
        assert client.get(f"/v1/sources/{quote('https://x.test/a', safe='')}"
                          ).status_code == 403
        assert client.get(f"/v1/entities/{obj['id']}/relations").status_code == 403
        assert client.get(f"/v1/entities/{obj['id']}/belief").status_code == 403


# ── GET /v1/subjects ───────────────────────────────────────────────────────

def test_subjects_list_and_by_id_mirror_the_objects_route(tmp_path):
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        s = client.post("/v1/subjects", json={
            "name": "Daniel", "subjectKind": "person"}).json()
        client.post("/v1/subjects", json={"name": "Ops", "subjectKind": "team"})
        body = client.get("/v1/subjects").json()
        assert sorted(x["name"] for x in body["subjects"]) == ["Daniel", "Ops"], body
        assert client.get("/v1/subjects", params={
            "kind": "person"}).json()["count"] == 1
        one = client.get(f"/v1/subjects/{s['id']}")
        assert one.status_code == 200, one.text
        assert one.json()["subjectKind"] == "person", one.text
        assert client.get("/v1/subjects/nope").status_code == 404


# ── GET /v1/sources ────────────────────────────────────────────────────────

def test_sources_list_is_the_mcp_doors_own_population(tmp_path):
    """The list delegates to `list_knowledge(kind='source')` — the SAME arm the
    `tortoise_list_knowledge` MCP tool serves, so the doors cannot disagree about
    what a provenance Source is."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            sdk._create_point("statement", "extracted", extractedFrom=(
                "https://example.test/doc-14592"))
            expected = sdk.list_knowledge(kind="source")
        finally:
            sdk.close()
        body = client.get("/v1/sources").json()
        assert body["sources"] == expected, body
        assert body["sources"][0]["url"] == "https://example.test/doc-14592"
        assert body["truncated"] is False


def test_source_by_id_resolves_a_url_keyed_source(tmp_path):
    """A provenance Source is keyed by `url` — it may carry NO `id` at all
    (#4649), so the url the LIST returns has to be an address."""
    url = "https://example.test/doc-14592-b"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            sdk._create_point("statement", "extracted", extractedFrom=url)
        finally:
            sdk.close()
        r = client.get(f"/v1/sources/{quote(url, safe='')}")
        assert r.status_code == 200, r.text
        assert r.json().get("url") == url, r.text
        assert client.get("/v1/sources/https%3A%2F%2Fnope.test%2Fx").status_code == 404


def test_source_payload_is_filtered_on_the_route(tmp_path):
    """#3998: the graph indexes a document's raw text — it is not the raw store.
    A route that handed it back would be a NEW disclosure path, so this pins the
    shared filter's presence rather than trusting it."""
    url = "https://example.test/filtered-14592"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            sdk._create_point("statement", "extracted", extractedFrom=url)
            # A WRITE with no door, and deliberately so: `_create_source` REFUSES a
            # caller-supplied `text` from `**props` (its blocklist names it), which
            # is exactly why this test plants one below the door — the route must
            # not hand back a payload the write door would not accept.
            sdk._get_proj().g._query(
                "MATCH (s:Source {url:$u}) SET s.text = $t",
                params={"u": url, "t": "RAW BODY THAT MUST NOT LEAK"},
                read_only=False)
        finally:
            sdk.close()
        body = client.get(f"/v1/sources/{quote(url, safe='')}").json()
        assert body.get("url") == url, body
        assert "text" not in body, body
        assert "RAW BODY THAT MUST NOT LEAK" not in repr(body), body


# ── GET /v1/entities/{id}/relations ────────────────────────────────────────

def test_relations_reports_direction_predicate_and_other_endpoint(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={"name": "ALPHA", "objectKind": "goal"}).json()
        b = client.post("/v1/objects", json={"name": "BETA", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            assert sdk._create_edge("dependsOn", a["id"], b["id"])
        finally:
            sdk.close()

        out = client.get(f"/v1/entities/{a['id']}/relations")
        assert out.status_code == 200, out.text
        rels = out.json()["relations"]
        assert len(rels) == 1, rels
        assert rels[0]["direction"] == "out", rels
        assert rels[0]["predicate"] == "dependsOn", rels
        assert rels[0]["other"] == {
            "id": b["id"], "label": "Object", "name": "BETA"}, rels

        # The SAME edge, read from the other end, is the other DIRECTION — the
        # fact `navigation.entityProfile` cannot report (its per-hop query UNIONs
        # both orientations and keeps only the edge type).
        back = client.get(f"/v1/entities/{b['id']}/relations").json()["relations"]
        assert [r["direction"] for r in back] == ["in"], back
        assert back[0]["other"]["id"] == a["id"], back


def test_relations_pages_and_offset_actually_advances(tmp_path):
    """`offset` must MOVE, and the page must be BOUNDED by the caller's limit.

    ⚠️ This is the regression test for a measured store behaviour, not a formality.
    With the window written flat after `UNION ALL` — ``… UNION ALL … ORDER BY …
    SKIP $skip LIMIT $limit`` — the deployed engine (FalkorDB graph module 42006)
    SILENTLY DROPS both clauses: `SKIP 0/1/2/3` each returned the SAME first rows,
    so `offset` was inert and the store returned the node's entire edge set on
    every request (`rows[:limit]` was the only bound). The single-branch form is
    unaffected, which is why the list routes were fine and this one was not. The
    fix is the `CALL { … }` wrapper in `entity_reads.entity_relations`.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        hub = client.post("/v1/objects", json={
            "name": "HUB", "objectKind": "goal"}).json()
        spokes = [client.post("/v1/objects", json={
            "name": f"S{i}", "objectKind": "goal"}).json() for i in range(4)]
        sdk = _sdk(ha)
        try:
            for sp in spokes:
                assert sdk._create_edge("related", hub["id"], sp["id"])
        finally:
            sdk.close()

        seen: list[str] = []
        offsets = (0, 1, 2, 3, 4)
        sizes = []
        for offset in offsets:
            body = client.get(f"/v1/entities/{hub['id']}/relations",
                              params={"limit": 1, "offset": offset}).json()
            sizes.append(body["count"])
            seen.extend(r["other"]["name"] for r in body["relations"])
        # The bound is the CALLER's: one row per request.
        assert sizes == [1, 1, 1, 1, 0], sizes
        # …and the window MOVED: five offsets walked all four spokes, no repeats.
        assert seen == ["S0", "S1", "S2", "S3"], seen
        # A page that holds everything says so.
        whole = client.get(f"/v1/entities/{hub['id']}/relations",
                           params={"limit": 10}).json()
        assert whole["count"] == 4 and whole["truncated"] is False, whole


def test_relations_reports_a_self_loop_once(tmp_path):
    """A self-edge matches BOTH orientations, so the naive UNION returns the SAME
    edge twice and `count` double-counts it (measured: `['out', 'in']`, both with
    `other.id == self`). `create_edge` has no self-edge guard, so this is
    reachable through a public writer."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={
            "name": "SELF", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            assert sdk._create_edge("related", a["id"], a["id"])
        finally:
            sdk.close()
        body = client.get(f"/v1/entities/{a['id']}/relations").json()
        assert body["count"] == 1, body
        assert [r["direction"] for r in body["relations"]] == ["out"], body


def test_relations_reports_which_pack_declared_the_predicate(tmp_path):
    """`declaredBy` is THREE-valued: a pack-declared verb names its packs, a
    structural one is `[]` — and a registry that could not be read is `null`,
    never an empty list claiming nothing declares it."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={"name": "ALPHA", "objectKind": "goal"}).json()
        b = client.post("/v1/objects", json={"name": "BETA", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            # A DOMAIN verb rides the operator path as the Layer-3 label
            # (ONTOLOGY §8 / #7852) — it is `causes`, declared by the dev pack.
            sdk.link_entities("IMPL", a["id"], b["id"], label="causes")
            sdk._create_edge("related", a["id"], b["id"])
        finally:
            sdk.close()
        body = client.get(f"/v1/entities/{a['id']}/relations").json()
        by_pred = {r["predicate"]: r for r in body["relations"]}
        assert by_pred["causes"]["declaredBy"] == ["dev"], by_pred
        assert by_pred["causes"]["mechanism"] == "IMPL", by_pred
        assert by_pred["related"]["declaredBy"] == [], by_pred
        assert body["declarationsReadable"] is True, body


def test_relations_reports_an_unreadable_registry_as_null_never_empty(
        tmp_path, monkeypatch):
    """The THIRD value is the point of `declaredBy`, and it needs its own test.

    Every other assertion in this file drives the two READABLE arms, so a mutant
    that collapses the third value into ``[]``
    (``list((declared or {}).get(...))``) leaves the whole suite green. That is a
    false claim, not a formatting preference: ``[]`` asserts *"no installed pack
    declares this predicate"* about a vocabulary the read could not see.

    ``declared_relation_packs`` returns ``None`` when the pack rows cannot be read
    (it returns ``{}`` only for a readable registry that declares nothing), so
    patching it to ``None`` drives exactly the state the route must not flatten.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={"name": "ALPHA", "objectKind": "goal"}).json()
        b = client.post("/v1/objects", json={"name": "BETA", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            sdk._create_edge("related", a["id"], b["id"])
        finally:
            sdk.close()

        # `entity_relations` imports the function INSIDE the call, so patching the
        # module attribute is what the read actually resolves.
        import tortoise.pack_registry as pack_registry

        monkeypatch.setattr(pack_registry, "declared_relation_packs", lambda: None)

        body = client.get(f"/v1/entities/{a['id']}/relations").json()
        assert body["relations"], body  # the row is still served
        assert body["declarationsReadable"] is False, body
        # `None`, never `[]`: an unasked registry is not a positive finding.
        assert [r["declaredBy"] for r in body["relations"]] == [None], body


def test_relations_is_layer_agnostic_and_404s_on_an_unknown_entity(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={"name": "ALPHA", "objectKind": "goal"}).json()
        s = client.post("/v1/subjects", json={"name": "Daniel", "subjectKind": "person"}).json()
        sdk = _sdk(ha)
        try:
            sdk._create_edge("holdsRole", s["id"], a["id"])
        finally:
            sdk.close()
        body = client.get(f"/v1/entities/{s['id']}/relations").json()
        assert body["entity"]["label"] == "Subject", body
        assert [r["other"]["label"] for r in body["relations"]] == ["Object"], body
        assert client.get("/v1/entities/nope/relations").status_code == 404


# ── GET /v1/entities/{id}/belief ───────────────────────────────────────────

def _plant_confidence_event(sdk, point_id: str, *, confidence: float,
                            alpha: float, beta: float) -> int:
    """Write a real `ConfidenceChanged` row through the store's own writer.

    The EP emitter only journals when an event log is configured
    (`emit=self._emit_event if self._event_log_path else None`), so a test graph
    has no producer to drive; this uses the store's own append, which is exactly
    the shape the emitter commits (`id`, `confidence`, `posterior_alpha`,
    `posterior_beta`).
    """
    from tortoise import event_store

    proj = sdk._get_proj()
    seq = event_store.next_seq(proj)
    ok = event_store.append_event(
        proj, seq, "ConfidenceChanged",
        {"id": point_id, "confidence": confidence,
         "posterior_alpha": alpha, "posterior_beta": beta},
        f"ev-14592-{point_id}-{seq}")
    assert ok is True, "the fixture could not write the event it asserts on"
    return seq


def test_belief_returns_the_current_posterior_and_the_last_write(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a measured claim", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            sdk._set_point_baseline(p["id"], 8.0, 2.0)
            seq = _plant_confidence_event(
                sdk, p["id"], confidence=0.8, alpha=8.0, beta=2.0)
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 200, r.text
        body = r.json()
        # EP α/β, not a substituted score: mean = α/(α+β) = 0.8.
        assert body["current"]["alpha"] == 8.0, body
        assert body["current"]["beta"] == 2.0, body
        assert body["current"]["mean"] == pytest.approx(0.8), body
        assert body["lastBeliefWrite"]["seq"] == seq, body
        assert body["lastBeliefWrite"]["committed"] == {
            "confidence": 0.8, "posteriorAlpha": 8.0, "posteriorBeta": 2.0,
        }, body
        # The window is PUBLISHED, not hidden: a null last write means "not in
        # this window", never "it never changed".
        assert body["lastBeliefWriteScan"]["eventType"] == "ConfidenceChanged"
        assert body["lastBeliefWriteScan"]["eventsScanned"] >= 1
        # Provenance is recorded for Points, and said so.
        assert body["evidence"]["provenanceRecordedFor"] == "Point"
        assert body["evidence"]["provenance"]["point"]["id"] == p["id"], body


def test_belief_refuses_an_uncalibrated_graph_rather_than_substituting_a_number(tmp_path):
    """A topology-only figure reported as EP is the silent-uncalibrated class
    #7478 targets, so the read REFUSES (409) and names the remedy."""
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "an unmeasured claim", "kind": "statement"}).json()
        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "EP_UNCALIBRATED", detail
        assert detail["remedy"], detail


def test_belief_404s_on_an_unknown_entity(tmp_path):
    """A 404 for an absent entity, with a POSITIVE CONTROL in the same test: a
    404 alone was satisfiable on the pre-change tree, where the route did not
    exist (review finding)."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a measured claim", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            sdk._set_point_baseline(p["id"], 8.0, 2.0)
        finally:
            sdk.close()
        assert client.get("/v1/entities/nope/belief").status_code == 404
        assert client.get(f"/v1/entities/{p['id']}/belief").status_code == 200


def test_belief_reports_a_node_with_no_persisted_belief_as_null(tmp_path):
    """A node EP has never consumed must NOT come back as a plausible Beta(1,1).

    Review finding P1: `ep._read_node` coalesces absent params to `(1.0, 1.0)`, so
    an Object (and a Subject) answered 200 with
    `{mean: 0.5, alpha: 1.0, beta: 1.0, effective_n: 2.0}` — a perfectly shaped
    posterior over NO evidence, which is the substituted-number class #7478
    targets one level below the graph-level calibration 409. The route now asks the
    NODE whether a belief exists, and says why when it does not.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        obj = client.post("/v1/objects", json={
            "name": "UNMEASURED", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            # Keep the GRAPH calibrated so this test isolates the per-node rule
            # from the graph-level 409 (a separate test pins that one).
            other = sdk._create_point("statement", "a measured claim")["id"]
            sdk._set_point_baseline(other, 8.0, 2.0)
        finally:
            sdk.close()
        r = client.get(f"/v1/entities/{obj['id']}/belief")
        body = r.json()
        assert r.status_code == 200, r.text
        assert body["current"] is None, body
        assert body["currentUnavailable"]["error_code"] == "NO_PERSISTED_BELIEF", body
        # It NAMES the labels that can carry one, so the answer is diagnosable.
        assert "Object" in body["currentUnavailable"]["beliefCarriedBy"], body
