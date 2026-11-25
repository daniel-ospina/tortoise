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


def test_sources_list_pages_over_a_TOTAL_order_so_a_walk_cannot_drop_a_row(
        tmp_path):
    """#14593 review P2: this route sliced the CALLEE's NON-TOTAL order.

    ``_list_sources`` ends ``ORDER BY points DESC`` and the route re-issues that
    query once per page, so equal-``points`` rows (the ORDINARY shape — one
    extracted Point per provenance Source) may be returned in any order, and not
    necessarily the SAME order on the next request. A walk could then drop one
    row and repeat another while the marker positively said ``truncated: false``
    — the silent-drop class ``#5346`` names, closed on the two layer lists and
    missing here.

    ⛔ The falsifier is the ORDER, not just the set: the URLs are created in
    reverse-alphabetical order and all tie on ``points``, so a pass requires the
    documented total order ``(-points, url)`` rather than whatever the engine's
    tie order happens to be. Then a real offset walk is checked to visit every
    row exactly once, which is the property a caller actually relies on.
    """
    urls = ("https://example.test/zeta", "https://example.test/alpha",
            "https://example.test/mike")
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            for u in urls:  # created in this order, every row on `points = 1`
                sdk._create_point("statement", "extracted", extractedFrom=u)
        finally:
            sdk.close()
        whole = client.get("/v1/sources", params={"limit": 50}).json()
        assert [r["url"] for r in whole["sources"]] == sorted(urls), whole

        walk: list[str] = []
        offset = 0
        while True:
            page = client.get(
                "/v1/sources", params={"limit": 1, "offset": offset}).json()
            walk += [r["url"] for r in page["sources"]]
            if not page["truncated"]:
                break
            offset += 1
            assert offset <= len(urls), ("the walk did not terminate", walk)
        assert walk == sorted(urls), walk
        assert len(set(walk)) == len(walk), walk


def test_sources_list_imposes_a_TOTAL_order_the_callee_does_not_guarantee(
        tmp_path, monkeypatch):
    """⚠️ The callee orders by `points DESC` ONLY, and `points` ties are the
    COMMON shape (a provenance Source with one extracted Point is the ordinary
    row) — so the order `/v1/sources` pages over is NOT total (#5346), and the
    route re-reads the whole population on EVERY page.

    A non-total order therefore lets an offset walk repeat one row and drop
    another while the marker still reports the enumeration complete — the exact
    silent-loss shape `entity_reads.list_layer` closes on the sibling route with
    `ORDER BY n.{order_prop}, id(n)`.

    ⛔ The callee is REPLACED here rather than seeded through the graph on
    purpose, and the reason is MEASURED: the tie order the engine returns is not
    stable across fixtures. With the sort line removed, the graph-seeded test
    above observes `mike, …, alpha` for creation order `zeta, alpha, mike` (so it
    reds there), while a five-Source probe of the same route on this tree came
    back in key order and would have passed. That is what "not total" means — the
    tier order is the engine's to choose — so a graph fixture's discriminating
    power is a property of the engine's current group order, not of the contract.
    This fixture does not depend on it: the SAME population is handed back with
    the tie order flipping between reads, which is the hazard exactly as a route
    that re-issues the read per page would meet it, on any engine.

    Pre-fix this test is RED — the walk returns `[mm, aa, aa]` (a duplicate) and
    never yields `zz` (a dropped row) — which is what makes it a pin.
    """
    from tortoise.sdk import TortoiseSDK

    rows = [
        {"url": "https://example.test/mm", "sourceKind": "web", "points": 2},
        {"url": "https://example.test/zz", "sourceKind": "web", "points": 1},
        {"url": "https://example.test/aa", "sourceKind": "web", "points": 1},
    ]
    calls = {"n": 0}

    def _callee(self, kind=None):
        calls["n"] += 1
        # Same population, tie order flipped on every read: `points DESC`
        # permits EITHER, and a per-page re-read sees both.
        ties = [rows[1], rows[2]] if calls["n"] % 2 else [rows[2], rows[1]]
        return [dict(rows[0]), *[dict(r) for r in ties]]

    with _graph(str(tmp_path / "g.db")) as _ha, _rest_client() as client:
        monkeypatch.setattr(TortoiseSDK, "list_knowledge", _callee)
        body = client.get("/v1/sources", params={"limit": 50}).json()
        assert [r["url"] for r in body["sources"]] == [
            "https://example.test/mm",
            "https://example.test/aa",
            "https://example.test/zz",
        ], body

        walked: list[str] = []
        expected_truncated = {0: True, 1: True, 2: False, 3: False}
        for offset, truncated in expected_truncated.items():
            page = client.get("/v1/sources",
                              params={"limit": 1, "offset": offset}).json()
            assert page["truncated"] is truncated, (offset, page)
            walked.extend(r["url"] for r in page["sources"])
        # Every row exactly once, in the documented order — no drop, no repeat.
        assert walked == ["https://example.test/mm",
                          "https://example.test/aa",
                          "https://example.test/zz"], walked


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
        assert client.get("/v1/subjects").status_code == 403
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


# ── the `kind` filter is GRADED: an unknown kind is refused, not answered ──

def test_layer_lists_refuse_a_kind_no_vocabulary_declares(tmp_path):
    """#14593 review P1: an unknown `kind` answered a page marked COMPLETE.

    Pre-fix, `?kind=org` reached `entity_reads.list_layer`, matched nothing and
    returned `200 {"objects": [], "count": 0, "total_available": 0,
    "truncated": false}` — byte-identical to "this graph names no Objects of that
    kind", and POSITIVELY marked complete by the offset-aware marker this PR
    added. That is the fail-open class the PR fixes one file over (#8447), on a
    brand-new read surface, and the sibling route already refuses the input
    (`list_points` -> 400 via `known_kinds()`), so the two list surfaces must not
    grade the same bad input differently.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        client.post("/v1/subjects", json={"name": "Ops", "subjectKind": "team"})
        for route in ("/v1/objects", "/v1/subjects"):
            r = client.get(route, params={"kind": "org"})
            assert r.status_code == 400, (route, r.text)
            detail = r.json()["detail"]
            # the `list_points` shape: the refusal NAMES the vocabulary it
            # refused against, so a caller can correct a typo from the response.
            assert detail.startswith("kind must be one of "), (route, detail)


def test_layer_lists_refuse_a_kind_declared_on_another_layer(tmp_path):
    """A layer mix-up is refused too — `organization` is §5's SUBJECT kind."""
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        r = client.get("/v1/objects", params={"kind": "organization"})
        assert r.status_code == 400, r.text
        assert "kind must be one of " in r.json()["detail"], r.json()


def test_layer_lists_still_read_an_off_vocabulary_kind_the_graph_carries(tmp_path):
    """The GRAPH wins over the vocabulary — a refusal must not become a false block.

    Both spellings asserted here are UNDECLARED (the first two asserts measure
    that, rather than trusting it): the write doors on `<layer>Kind` are open, so
    a stored off-vocabulary kind exists, and refusing a filter over a row the
    caller can otherwise page to would be worse than the silence being fixed.
    This is also why a declared-vocabulary-ONLY validator was rejected here — it
    would refuse this file's own existing rows (`objectKind="organisation"`,
    `subjectKind="person"`).
    """
    from tortoise.kind_layers import declared_layers

    assert declared_layers("organisation") == frozenset()
    assert declared_layers("person") == frozenset()
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "organisation"})
        client.post("/v1/objects", json={"name": "GROW", "objectKind": "goal"})
        r = client.get("/v1/objects", params={"kind": "organisation"})
        assert r.status_code == 200, r.text
        assert [o["name"] for o in r.json()["objects"]] == ["ACME"], r.json()
        client.post("/v1/subjects", json={"name": "Daniel", "subjectKind": "person"})
        s = client.get("/v1/subjects", params={"kind": "person"})
        assert s.status_code == 200, s.text
        assert [x["name"] for x in s.json()["subjects"]] == ["Daniel"], s.json()


def test_layer_lists_admit_a_declared_kind_this_graph_does_not_carry(tmp_path):
    """An honest "you know nothing" is a COMPLETE EMPTY page, never a 400.

    The second half is the trap this fix had to avoid: `known_kinds("subjectKind")`
    answers the EMPTY set by design (`domain_loader._BUCKET_ATTRS["subjectKind"]
    is None` — no `subjectKinds` list exists in the manifest schema), so a
    bucket-read validator would 400 every subject filter, including the §5
    vocabulary this asserts is admitted.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "organisation"})
        r = client.get("/v1/objects", params={"kind": "goal"})
        assert r.status_code == 200, r.text
        assert r.json()["objects"] == [], r.json()
        assert r.json()["truncated"] is False, r.json()
        t = client.get("/v1/subjects", params={"kind": "core:team"})
        assert t.status_code == 200, t.text
        assert t.json()["subjects"] == [], t.json()
        assert t.json()["truncated"] is False, t.json()


def test_layer_lists_refuse_an_invented_NAMESPACE_but_read_a_carried_one(tmp_path):
    """⛔ review P2: a namespaced `kind` was admitted by stripping the namespace.

    The first validator asked `kind_layers.declared_layers(kind)`, which
    deliberately strips ANY namespace (`domain_loader.kind_is_known` splits on
    `:`). Measured: `declared_layers('zzz:goal') == ['object', 'point']`, so
    `?kind=zzz:goal` answered the empty-and-complete page the validator exists to
    remove, while `?kind=zzz:team` was refused on the Subject route — the two
    layers grading one input differently. Admission now takes the layer's own
    EXACT spellings (bare, or the repo's `core:` form) plus whatever the graph
    CARRIES, so a namespace nobody declares is refused …

    …and a namespaced kind the graph really holds is still readable (the
    fail-closed fix must not become a false block): both directions in one test.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        for route, kind in (("/v1/objects", "zzz:goal"),
                            ("/v1/subjects", "zzz:team")):
            r = client.get(route, params={"kind": kind})
            assert r.status_code == 400, (route, kind, r.text)
            assert "kind must be one of " in r.json()["detail"], r.json()
        # A row the write door ACCEPTED under that spelling makes it readable.
        client.post("/v1/objects", json={"name": "NS", "objectKind": "zzz:goal"})
        ok = client.get("/v1/objects", params={"kind": "zzz:goal"})
        assert ok.status_code == 200, ok.text
        assert [o["name"] for o in ok.json()["objects"]] == ["NS"], ok.json()


def test_layer_lists_treat_the_two_spellings_of_one_kind_as_one_kind(tmp_path):
    """⛔ review P2/P3: `team` and `core:team` are ONE kind on both sides.

    §5's subject vocabulary is NAMESPACED (`subject_kind_names()` = `core:team`,
    `core:role`, …), so a validator that only compared against the vocabulary's
    own spellings refused `?kind=team` — the spelling `POST /v1/subjects` itself
    writes — with a 400. And once both spellings are admitted, an EQUALITY filter
    answers `200 count: 0, truncated: false` for whichever spelling the rows do
    not happen to use, which is the misleading-complete page this whole
    validation exists to remove.

    ⛔ An ARBITRARY namespace is a different vocabulary item and stays exact:
    `zzz:goal` must not pull in the bare `goal` rows.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        # ⛔ BEFORE any write: the BARE spelling must be admitted on an EMPTY
        # graph. This is the discriminating assertion — with a row present the
        # `carried` arm admits the spelling on its own, so the rest of this test
        # passes on the UNFIXED validator (measured). Only the empty graph
        # exercises the vocabulary arm the bare-form rule lives in.
        for k in ("team", "role"):
            r = client.get("/v1/subjects", params={"kind": k})
            # `.get`: on the UNFIXED validator the body is a 400 with no
            # `count`, and subscripting it raised KeyError instead of reporting
            # the state this assertion exists to catch.
            assert (r.status_code, r.json().get("count")) == (200, 0), (k, r.text)
        client.post("/v1/subjects", json={"name": "Ops", "subjectKind": "team"})
        client.post("/v1/subjects", json={"name": "QA", "subjectKind": "core:team"})
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        client.post("/v1/objects", json={"name": "NS", "objectKind": "zzz:goal"})

        # The bare spelling is admitted (not a 400) and finds BOTH rows.
        for kind in ("team", "core:team"):
            r = client.get("/v1/subjects", params={"kind": kind})
            assert r.status_code == 200, (kind, r.text)
            assert sorted(s["name"] for s in r.json()["subjects"]) == ["Ops", "QA"], \
                (kind, r.json())
        # ⛔ The CARRIED arm answers the equivalence too: a graph holding the
        # BARE off-vocabulary spelling must admit its `core:` form (the filter
        # would answer correctly for either, so refusing one is a pure over-block).
        client.post("/v1/objects", json={"name": "BARE", "objectKind": "organisation"})
        for kind in ("organisation", "core:organisation"):
            r = client.get("/v1/objects", params={"kind": kind})
            assert r.status_code == 200, (kind, r.text)
            assert [o["name"] for o in r.json()["objects"]] == ["BARE"], (kind, r.json())
        # …and the equivalence is `core:`-only: an arbitrary namespace is exact.
        for kind, expected in (("goal", ["ACME"]), ("core:goal", ["ACME"]),
                               ("zzz:goal", ["NS"])):
            r = client.get("/v1/objects", params={"kind": kind})
            assert r.status_code == 200, (kind, r.text)
            assert [o["name"] for o in r.json()["objects"]] == expected, (kind, r.json())


def test_layer_lists_admit_the_nil_kind_and_never_name_a_value_they_refuse(tmp_path):
    """⛔ review P2: `other` was REFUSED while the refusal listed it as allowed.

    `declared_layers('other')` is `frozenset()` BY DESIGN (`NIL_KINDS`), so the
    declared arm could never admit it and only a carried row could — yet the 400's
    `allowed` list is built from the layer vocabulary, which names `other`. A
    refusal that names a value it rejects is a false block, and `other` is the
    fold's ON CREATE default on EVERY entity layer, so it is always answerable.
    """
    from tortoise.kind_layers import NIL_KINDS

    assert NIL_KINDS  # the arm exists to be exercised, not assumed
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        for route in ("/v1/objects", "/v1/subjects"):
            for kind in sorted(NIL_KINDS):
                r = client.get(route, params={"kind": kind})
                assert r.status_code == 200, (route, kind, r.text)
                assert r.json()["truncated"] is False, r.json()
        # …and a refused kind's `allowed` list never names the value it refused.
        bad = client.get("/v1/objects", params={"kind": "org"})
        allowed = bad.json()["detail"].removeprefix("kind must be one of ")
        for kind in sorted(NIL_KINDS):
            assert repr(kind) in allowed or f'"{kind}"' in allowed, (kind, allowed)



def test_layer_list_treats_a_blank_kind_as_no_filter(tmp_path):
    """#9572's rule at the READ door: blank is not a kind.

    Pre-fix a whitespace `kind` was truthy, so it filtered on
    `objectKind = "   "` and answered an empty COMPLETE page for a filter the
    caller never meant to apply — the same silence as the typo above.
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "goal"})
        r = client.get("/v1/objects", params={"kind": "   "})
        assert r.status_code == 200, r.text
        assert r.json()["count"] == 1, r.json()


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


def test_the_belief_routes_provenance_chain_also_honours_the_source_rule(
        tmp_path):
    """#14593 review P1 (data disclosure, #3998): the SAME rule, a FOURTH door.

    ``/belief`` attaches ``sdk._provenance(id)`` verbatim, and that helper builds
    its ``delegation`` arm with ``properties(n)`` — the WHOLE node map — for
    whatever the author ``:Subject`` is connected to. A ``references`` edge to a
    ``:Source`` therefore handed the Source's raw ``text`` back inside an
    ordinary ``graphs:read`` response, on a route that never asked for a
    document body: the exact disclosure ``_layer_payload`` (by-id routes) and
    ``_endpoint_preview`` (relations) refuse, arriving by the one arm nobody
    filtered. Measured pre-fix through public doors only — the ``:Source`` and
    the edge were both created by the SDK — `provenance.delegation[0].props`
    carried ``"text": "SECRET-..."``.

    ⛔ The assertion is on the WHOLE response, not on one key: the rule is "a
    Source's raw payload never rides the route", so a future re-shape of the
    delegation arm must not re-open it by moving the payload somewhere else.
    """
    url = "https://example.test/belief-delegation-14593"
    secret = "RAW DOCUMENT BODY THAT MUST NOT RIDE THIS ROUTE"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            subj = sdk._create_subject("Reviewer", "person")
            # `_create_source`, not the `extractedFrom` auto-mint: the auto-minted
            # node is keyed by `url` with no `id`, so `create_edge` cannot resolve
            # it and silently answers `created: false` (measured while writing this
            # row — the fixture, not the route, would have been the failure).
            src = sdk._create_source(url, "web")
            assert src["id"] == url, src
            # Below the door on purpose: `_create_source` refuses a caller-supplied
            # `text`, and the route must not hand back what the write door would
            # not accept (same construction as the `/v1/sources` sibling above).
            sdk._get_proj().g._query(
                "MATCH (s:Source {url:$u}) SET s.text = $t",
                params={"u": url, "t": secret}, read_only=False)
            edge = sdk._create_edge("references", subj["id"], url)
            assert edge["created"] is True, (
                "the fixture could not write the edge it asserts on: "
                f"{edge}")
            point = sdk._create_point(
                "statement", "a claim with a documented author",
                authoredBy=subj["id"])
        finally:
            sdk.close()

        out = client.get(f"/v1/entities/{point['id']}/belief")
        assert out.status_code == 200, out.text
        body = out.json()
        # The fixture really did reach the delegation arm — otherwise this row
        # would pass while grading nothing (the non-vacuity half of the P1).
        delegation = body["evidence"]["provenance"]["delegation"]
        assert [d["node_type"] for d in delegation] == ["Source"], body
        assert delegation[0]["via"] == "references", body
        assert delegation[0]["props"].get("url") == url, body
        assert "text" not in delegation[0]["props"], body
        assert secret not in repr(body), body


def test_the_delegation_filter_grades_the_label_SET_not_just_the_first_label(
        tmp_path):
    """#14593 review P1: a hybrid neighbour is judged by ALL its labels.

    ``node_type`` is the neighbour's FIRST label. So ``(:Object:Source)``
    presented as ``node_type == "Object"`` — an allowed label — and the filter,
    keying on that single string, published the node's whole ``properties`` map
    with the ``:Source`` half included. MEASURED on this tree, through public
    doors only: a hybrid node carrying ``text`` returned it inside
    ``GET /v1/entities/{id}/belief``.

    ⛔ The hybrid is not a hand-written fixture shape: ``mcp_server.py``'s #4649
    blast-radius fix exists for ``(:Object:Source)``, #12914 and
    ``tool_registry.py`` (citing tortoise#13114) name ``(:Object:Source)`` /
    ``(:Subject:Source)`` / ``(:Object:Event)`` as shapes the supersede path
    meets, and ``relabel_legacy_documents`` leaves ``:Document:Source`` behind on
    an interrupted run. This arm creates the exact hybrid and asserts the payload
    nowhere reaches the response.
    """
    secret = "HYBRID OBJECT-SOURCE BODY THAT MUST NOT RIDE THIS ROUTE"
    hybrid_id = "hybrid-object-source-14593"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            subj = sdk._create_subject("Reviewer", "person")
            # `:Object` FIRST on purpose: that is what makes `labels(n)[0]` an
            # allowed label and reproduces the disclosure instead of pretending
            # the order does not matter.
            sdk._get_proj().g._query(
                "CREATE (n:Object:Source {id:$i, name:'HYBRID', url:$u, text:$t})",
                params={"i": hybrid_id, "u": "https://example.test/hybrid-14593",
                        "t": secret}, read_only=False)
            edge = sdk._create_edge("references", subj["id"], hybrid_id)
            assert edge["created"] is True, f"the fixture could not write the edge: {edge}"
            point = sdk._create_point(
                "statement", "a claim with a documented author",
                authoredBy=subj["id"])
        finally:
            sdk.close()

        out = client.get(f"/v1/entities/{point['id']}/belief")
        assert out.status_code == 200, out.text
        body = out.json()
        delegation = body["evidence"]["provenance"]["delegation"]
        # The fixture reached the arm, so this row grades something.
        assert [d["node_type"] for d in delegation] == ["Object"], body
        # …and the `:Source` half is still there to be seen, which is the point.
        assert set(delegation[0]["labels"]) == {"Object", "Source"}, delegation
        assert "text" not in delegation[0]["props"], delegation[0]["props"]
        assert secret not in repr(body), "a hybrid node's payload rode the route"


def test_the_delegation_filter_denies_by_default_not_by_one_spelling():
    """#14593 review P1 (the fix's OWN hole): the filter keyed on ONE spelling.

    ``_provenance`` fills ``node_type`` with ``labels(n)[0]``, and the engine does
    not guarantee the ORDER of ``labels()`` (Cypher manual, list functions:
    *"the order of returned labels is not guaranteed"*). Measured on this tree the
    two legacy shapes allocate in OPPOSITE directions: ``(:Document:Source)``
    returns ``['Source', 'Document']`` while ``(:Object:Source)`` returns
    ``['Object', 'Source']``. An equality test against ``"Source"`` therefore
    filters one and skips the other — the same node class, no code difference — so
    the guard was only as strong as the POSITION of the label it named.

    ⛔ The unit under test is the DECISION, so this pins it in both directions for
    the labels the fixture actually carries: the out-of-set ``Document`` and the
    never-heard-of ``Gadget`` are filtered, and the publishable ``Object``'s map is
    returned untouched — otherwise "deny by default" would quietly become "filter
    everything" and strip a legitimate neighbour's properties. The leaking
    ALLOW-LISTED spelling cannot be reproduced here (it needs a real allocation
    order, which a hand-built dict does not have); it is pinned end-to-end by
    ``test_the_delegation_filter_grades_the_label_SET_not_just_the_first_label``
    above.
    """
    from tortoise.hosted_api import _filter_delegation_payloads

    secret = "RAW DOC BODY THAT MUST NOT RIDE THIS ROUTE"
    url = "https://example.test/legacy-document-14593"
    provenance = {"delegation": [
        # the legacy multi-label shape: `labels(n)[0]` is `Document`, not `Source`
        {"via": "references", "node_type": "Document",
         "props": {"url": url, "id": url, "text": secret}},
        # a label the route has never heard of: deny-by-default must cover it
        {"via": "references", "node_type": "Gadget", "props": {"text": secret}},
        # ⛔ and the ABSENT arm: no `node_type` at all. `m6`, which denies every
        # KNOWN non-publishable spelling and lets a missing one through, passes
        # this test without this entry (measured) — so "deny by default" would
        # be pinned only for misspellings, never for absence.
        {"via": "references", "props": {"url": url, "text": secret}},
        # a publishable neighbour is returned AS STORED
        {"via": "dependsOn", "node_type": "Object",
         "props": {"name": "BETA", "objectKind": "goal"}},
    ]}
    out = _filter_delegation_payloads(provenance)
    assert [d.get("node_type") for d in out["delegation"]] == [
        "Document", "Gadget", None, "Object"], out
    assert out["delegation"][0]["props"] == {"url": url, "id": url}, out
    assert "text" not in out["delegation"][1]["props"], out
    assert out["delegation"][2]["props"] == {"url": url}, out
    assert out["delegation"][3]["props"] == {
        "name": "BETA", "objectKind": "goal"}, out
    assert secret not in repr(out), out
    # …and the input is not mutated in place: the route publishes what this
    # returns, so a caller holding the callee's dict must not lose the payload.
    assert provenance["delegation"][0]["props"]["text"] == secret


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
            "id": b["id"], "label": "Object", "name": "BETA",
            "nameTruncated": False}, rels

        # The SAME edge, read from the other end, is the other DIRECTION — the
        # fact `navigation.entityProfile` cannot report (its per-hop query UNIONs
        # both orientations and keeps only the edge type).
        back = client.get(f"/v1/entities/{b['id']}/relations").json()["relations"]
        assert [r["direction"] for r in back] == ["in"], back
        assert back[0]["other"]["id"] == a["id"], back


def test_relations_bounds_and_marks_a_long_neighbour_body(tmp_path):
    """The `other.name` preview is BOUNDED, and the cut is MARKED (#14593 P2).

    MEASURED before the fix, on a real ASGI app: one `related` edge between two
    Points whose `content` was 4000 characters returned `other.name` at 4000
    characters with `truncated: false` — a `:Point` carries no `name`, so the
    blanket `coalesce(name, content, url)` landed on the body. With `limit` at
    `le=1000` that is a per-request size of `limit × largest neighbour body` on a
    brand-new public GET, and the function's own docstring gives fan-out
    amplification as its reason for not returning whole nodes.

    ⛔ The assertion is on the LENGTH AND THE FLAG, not on a prefix: a shortened
    value a caller cannot detect is the "absence read as an answer" class this PR
    removes elsewhere, so bounding without marking would trade one defect for
    another. The suite could not see any of it before — its only relation fixture
    used Object neighbours, whose `name` is short.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        hub = client.post("/v1/points", json={
            "content": "the anchor", "kind": "statement"}).json()
        # DISTINCTIVE, not one repeated character: with "x" * 4000 the value
        # assertion below is satisfied by ANY 200 "x"s, so a mutant returning a
        # different slice stayed green (measured).
        long_body = "EDGE-OF-THE-BODY" + "y" * 4000
        far = client.post("/v1/points", json={
            "content": long_body, "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            assert sdk._create_edge("related", hub["id"], far["id"])
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{hub['id']}/relations")
        assert r.status_code == 200, r.text
        rels = r.json()["relations"]
        assert len(rels) == 1, rels
        other = rels[0]["other"]
        assert other["id"] == far["id"], other
        # A Point is answered by its content, and the preview is capped.
        assert len(other["name"]) == 200, len(other["name"])
        assert other["name"] == long_body[:200], other["name"][:40]
        assert other["nameTruncated"] is True, other
        # The page itself was NOT truncated — the two are different facts, and a
        # route that conflated them would hide the cut behind the page flag.
        assert r.json()["truncated"] is False, r.json()["truncated"]


def test_relations_never_puts_a_Sources_payload_in_the_name_field(tmp_path):
    """A `:Source` neighbour is answered by its `url`, never by its `content`.

    The blanket `coalesce(m.name, m.content, m.url)` reached an UNDECLARED
    `content` on a `:Source` neighbour — the `#3998` class (a property no
    declaration covers, surfacing through a read path that never asked for it),
    on a route that has no business returning a source's stored text. This arm
    writes that property below the write door and asserts the payload appears
    NOWHERE in the response body (not just in `name`: a regression that moved it
    to another key would otherwise pass).
    """
    payload = "SK-SOURCE-PAYLOAD-DO-NOT-SERVE"
    url = "https://example.test/doc"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        hub = client.post("/v1/points", json={
            "content": "the anchor", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            src = sdk._create_source(url, "web")
            assert src["id"] == url, src
            # ⛔ `content`, deliberately — NOT `text`. This asserts the property
            # the blanket coalesce actually READ, and the distinction was
            # measured: `text` is filtered by `filter_source_props` and the old
            # query never looked at it, so a `text`-only fixture passes on the
            # BUGGY code (it did, on the first attempt at this arm — a vacuous
            # test is a false PASS, which is the class this PR is about).
            # `content` is neither declared on `:Source` nor read through the
            # filter, so it is a node property an out-of-band writer can leave
            # behind and the coalesce would hand straight back.
            sdk._get_proj().g._query(
                "MATCH (s:Source {url:$u}) SET s.content = $t, s.text = $t",
                params={"u": url, "t": payload}, read_only=False)
            assert sdk._create_edge("related", hub["id"], url)["created"] is True
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{hub['id']}/relations")
        assert r.status_code == 200, r.text
        assert payload not in r.text, "a Source's stored text rode the response"
        other = r.json()["relations"][0]["other"]
        assert other["label"] == "Source", other
        assert other["name"] == url, other


def test_relations_never_publishes_a_legacy_Documents_stored_body(tmp_path):
    """The `ELSE` arm must not reach `content` — a legacy `:Document` lives there.

    Review P2 (second round): the per-layer CASE closed the `:Source` arm but its
    `ELSE` kept the old blanket ``coalesce(m.name, m.content, m.url)``, so a bare
    ``(:Document {id})`` — pre-D10, or left behind by an interrupted
    ``relabel_legacy_documents`` — still answered ``other.name`` with its stored
    body. ``projection.entities`` names ``content`` as the retired document
    payload and ``migrate_kinds`` records that ``_upsert_document`` genuinely
    stored it, so this is the same disclosure class one label over — and the
    route's own docstring claimed the rule was "everything else by its name".

    ⛔ The fallback is now ``coalesce(m.name, m.url, m.id)``: a name if the node
    has one, else its identity. Nothing legitimate is lost — `:Event` keeps its
    narrative in `session_narrative`, never on the node.
    """
    secret = "PRE-D10 DOCUMENT BODY THAT MUST NOT RIDE THIS ROUTE"
    legacy_id = "doc-legacy-14593"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        hub = client.post("/v1/points", json={
            "content": "the anchor", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            # Below the door: no product writer produces this shape today, which is
            # exactly why a restored pre-D10 dump is the realistic source. The EDGE
            # is written here too — `_create_edge` resolves ids through the identity
            # table, which does not know a bare `:Document`, so a fixture that went
            # through it would fail on the fixture rather than on the route
            # (measured: `{'created': False}`).
            sdk._get_proj().g._query(
                "CREATE (d:Document {id:$i}) SET d.content = $t "
                "WITH d MATCH (a:Point {id:$a}) CREATE (a)-[:related]->(d)",
                params={"i": legacy_id, "t": secret, "a": hub["id"]},
                read_only=False)
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{hub['id']}/relations")
        assert r.status_code == 200, r.text
        assert secret not in r.text, "a legacy Document's body rode the response"
        other = r.json()["relations"][0]["other"]
        assert other["label"] == "Document", other
        # It falls back to the node's own IDENTITY, so the row is still usable.
        assert other["name"] == legacy_id, other


def test_relations_bounds_the_ROOT_the_same_way_it_bounds_a_neighbour(tmp_path):
    """The root's `entity.name` is capped and flagged like a neighbour's (P3).

    The code said "the root's own name, on the same coalesce as a neighbour's"
    while the neighbour side had gained a cap and a flag the root did not have —
    so a client gating display on `nameTruncated` read the root's missing flag as
    "bounded" and got an unbounded value (measured: 4000 characters). What the two
    sides now SHARE is the cap and the flag; the coalesce is deliberately not
    shared (a neighbour is selected per layer, the root reads one known map), and
    an earlier revision of this docstring overstated that as "answer identically"
    — the same claim-wider-than-code defect one level down. The full body stays
    reachable at the entity's own route.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        body_4000 = "ROOT-BODY-MARKER-" + ("y" * 4000)
        root = client.post("/v1/points", json={
            "content": body_4000, "kind": "statement"}).json()
        far = client.post("/v1/points", json={
            "content": "a neighbour", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            assert sdk._create_edge("related", root["id"], far["id"])
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{root['id']}/relations")
        assert r.status_code == 200, r.text
        entity = r.json()["entity"]
        assert len(entity["name"]) == 200, len(entity["name"])
        # ⛔ The VALUE, not just the length: on an all-identical body a mutant
        # that returns a different 200-character slice — or a "nicer"
        # ``[:199] + "…"`` — stays green on a length assertion alone (review P3,
        # the same gap the neighbour arm was tightened for one commit over).
        from tortoise.entity_reads import _ENDPOINT_NAME_MAX
        assert entity["name"] == body_4000[:_ENDPOINT_NAME_MAX], entity["name"][:40]
        assert entity["nameTruncated"] is True, entity
        # The neighbour side carries the SAME flag, which is the shared half.
        assert r.json()["relations"][0]["other"]["nameTruncated"] is False
        # ⛔ AND THE ROOT'S FALSE ARM: without this, `_root_preview` forced to
        # `nameTruncated: True` unconditionally stays green (measured) — every
        # root would claim truncation and no row would notice.
        short = client.get(f"/v1/entities/{far['id']}/relations")
        assert short.status_code == 200, short.text
        assert short.json()["entity"]["nameTruncated"] is False, short.json()["entity"]


def test_relations_identifies_an_Event_neighbour_by_its_eventId(tmp_path):
    """⛔ review P3: the `m.eventId` identity fallback had NO pin.

    An `:Event` neighbour carries no `id` — its identity key is `eventId` — so the
    endpoint-name expression falls back through it. Measured: reverting the ELSE
    arm to `coalesce(m.name, m.url, m.id)` left the whole suite green, i.e. an
    Event neighbour would answer `name: None` with nothing noticing. The body
    (`content`) must not substitute for the identity either — that is the #3998
    direction the fallback order itself encodes.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        root = client.post("/v1/objects", json={
            "name": "HOST", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) "
                "CREATE (e:Event {eventId:$eid, content:$body}) "
                "CREATE (n)-[:related]->(e)",
                params={"id": root["id"], "eid": "evt-14592-identity",
                        "body": "EVENT-CONTENT-BODY-MUST-NOT-BE-THE-NAME"},
                read_only=False)
        finally:
            sdk.close()
        r = client.get(f"/v1/entities/{root['id']}/relations")
        assert r.status_code == 200, r.text
        other = r.json()["relations"][0]["other"]
        assert other["label"] == "Event", other
        assert other["id"] == "evt-14592-identity", other
        assert other["name"] == "evt-14592-identity", other
        assert "EVENT-CONTENT-BODY-MUST-NOT-BE-THE-NAME" not in r.text, r.text


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
            # ⛔ #8447's OTHER half, pinned here: a `limit+1` probe is a LOWER
            # BOUND, never a total. Deleting `exact=False` at the call site (so
            # the probe's `candidates` is published as `total_available`) left
            # this whole file green and reported `total_available: 2` for a
            # 4-edge population — so the marker's own rule needs its own row.
            if body["truncated"]:
                assert "total_available" not in body, body
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
        # ⛔ `ep_state` rides every belief answer (#11702): a baseline-stamped
        # prior is `baseline`, never silently reported as a measurement.
        assert body["ep_state"] == "baseline", body
        assert body["lastBeliefWrite"]["seq"] == seq, body
        # The named reason is for a null write ONLY: when the store DOES hold the
        # row, there is nothing unavailable to explain.
        assert body["lastBeliefWriteUnavailable"] is None, body
        assert body["lastBeliefWrite"]["committed"] == {
            "confidence": 0.8, "posteriorAlpha": 8.0, "posteriorBeta": 2.0,
        }, body
        # The window is PUBLISHED, not hidden: a null last write means "not in
        # this window", never "it never changed".
        assert body["lastBeliefWriteScan"]["eventType"] == "ConfidenceChanged"
        # ⛔ The window is its SIZE, and says so — a key named `eventsScanned`
        # reported 1000 on a graph holding one row (#14593 review).
        assert body["lastBeliefWriteScan"]["windowRows"] >= 1
        assert "eventsScanned" not in body["lastBeliefWriteScan"], body
        # …and it is the CONSTANT, not a count: `>= 1` cannot tell a count from a
        # limit, so pin the identity. Both arms assert it because this one can
        # SKIP under load with INCONCLUSIVE [#5049], and a skip is not a pass.
        from tortoise.event_store import LATEST_FOR_NODE_SCAN
        assert body["lastBeliefWriteScan"]["windowRows"] == LATEST_FOR_NODE_SCAN, body
        # Provenance is recorded for Points, and said so.
        assert body["evidence"]["provenanceRecordedFor"] == "Point"
        assert body["evidence"]["provenance"]["point"]["id"] == p["id"], body


def test_belief_does_not_let_a_null_last_write_read_as_never_moved(tmp_path, monkeypatch):
    """#14593 review P1: a null ``lastBeliefWrite`` must not speak for the store.

    MEASURED: the GRAPH event store receives the types its emitter ADMITS —
    ``sdk._GRAPH_EVENT_TYPES``, plus ``PointRevised`` when the change feed is on
    (every product ``append_event`` call site is ``sdk._emit_event``'s gated
    arm) — and ``ConfidenceChanged`` is in neither arm, so ``latest_for_node(..., "ConfidenceChanged", …)`` cannot return a
    row on a real graph, and the field was null while the docstring promised
    "when it last moved… from the store's own record rather than inferred".
    A client reading that null concludes the belief never moved, which is false:
    the change is durable in the event JOURNAL. The route now publishes a named
    reason beside the null — the same shape it already uses for
    ``NO_PERSISTED_BELIEF`` — so absence reads as "this store cannot record it",
    not as a measurement.

    ⛔ This test is RED before the fix (the key does not exist, so the subscript
    raises), and it asserts the emitter's OWN gate rather than a hardcoded
    list — the day ``ConfidenceChanged`` joins that set, the reason must vanish
    on its own, and the sibling test above already pins the populated case.
    """
    from tortoise.sdk import _GRAPH_EVENT_TYPES

    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            pid = sdk._create_point("statement", "a measured claim")["id"]
            sdk._set_point_baseline(pid, 8.0, 2.0)
        finally:
            sdk.close()
        r = client.get(f"/v1/entities/{pid}/belief")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lastBeliefWrite"] is None, body
        if "ConfidenceChanged" in _GRAPH_EVENT_TYPES:  # pragma: no cover
            # The emitter started recording it: the query is now answerable and
            # there must be no reason to publish.
            assert body["lastBeliefWriteUnavailable"] is None, body
            return
        reason = body["lastBeliefWriteUnavailable"]
        assert reason["error_code"] == (
            "BELIEF_CHANGES_NOT_IN_GRAPH_EVENT_STORE"), body
        # It NAMES what the store does record, so the answer is diagnosable, and
        # it points at where the change really is instead of asserting absence.
        assert "ConfidenceChanged" not in reason["recordedEventTypes"], body
        assert reason["recordedEventTypes"], body
        # ⛔ The enumeration must MIRROR the emitter's gate, not one arm of it:
        # `sdk._emit_event` also stores `PointRevised` when the #2353 change feed
        # is on, and a client rules types OUT from this list. Both directions are
        # graded, because `sorted(_GRAPH_EVENT_TYPES)` satisfies the two
        # assertions above (measured: the pre-fix return passes them) — which is
        # exactly how the under-publishing answer shipped.
        from tortoise.hosted_api import _graph_event_types

        monkeypatch.setenv("TORTOISE_CHANGE_FEED", "1")
        assert "PointRevised" in _graph_event_types()
        monkeypatch.delenv("TORTOISE_CHANGE_FEED", raising=False)
        assert "PointRevised" not in _graph_event_types()
        assert "journal" in reason["message"], body
        # The scan block is published here too, and its window is a LIMIT, never
        # a count (review P2) — pinned in THIS arm because under load the
        # populated-case sibling can SKIP with INCONCLUSIVE [#5049], and a
        # skip is not a pass: the contract must be observed somewhere that runs.
        from tortoise.event_store import LATEST_FOR_NODE_SCAN
        assert body["lastBeliefWriteScan"]["windowRows"] == LATEST_FOR_NODE_SCAN, body
        assert "eventsScanned" not in body["lastBeliefWriteScan"], body
        assert "windowKind" not in body["lastBeliefWriteScan"], body


def _plant_malformed_event(sdk, seq: int) -> None:
    """Write a `ConfidenceChanged` row whose `payload` is NOT JSON.

    `append_event` serialises its payload (`json.dumps`), so the one shape that
    makes `latest_for_node`'s parse arm matter cannot be produced through it; a
    store written by an older/other producer can carry it, which is the row the
    docstring's "SKIPPED, not fatal" promise is about.
    """
    proj = sdk._get_proj()
    proj.g._query(
        "CREATE (e:GraphEvent {seq:$seq, ts:'2026-01-01T00:00:00+00:00', "
        "type:'ConfidenceChanged', event_id:$eid, payload:$payload})",
        params={"seq": int(seq), "eid": f"ev-14592-malformed-{seq}",
                "payload": "not json"},
        read_only=False,
    )


def test_belief_matches_the_parsed_id_and_skips_a_malformed_payload(tmp_path):
    """`latest_for_node`'s TWO hardening promises, each pinned by its own row.

    1. The id is matched on the PARSED payload, never by substring: a NEWER row
       whose id merely CONTAINS this node's id (`<id>-shadow`) must not be
       reported as this node's transition.
    2. A row whose payload will not parse is SKIPPED, not fatal: one bad write
       must not hide every later one (nor 500 the read).

    Both rows are planted NEWER than the real one, so each promise is the only
    thing standing between the assertion and the wrong answer — a substring
    match selects the shadow row, and an unguarded `json.loads` raises on the
    malformed one.
    """
    from tortoise import event_store

    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a measured claim with shadowing neighbours",
            "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            sdk._set_point_baseline(p["id"], 8.0, 2.0)
            seq = _plant_confidence_event(
                sdk, p["id"], confidence=0.8, alpha=8.0, beta=2.0)
            proj = sdk._get_proj()
            # Promise 1: the id IS A PREFIX of this newer row's id.
            shadow_seq = event_store.next_seq(proj)
            assert event_store.append_event(
                proj, shadow_seq, "ConfidenceChanged",
                {"id": f"{p['id']}-shadow", "confidence": 0.2,
                 "posterior_alpha": 2.0, "posterior_beta": 8.0},
                f"ev-14592-shadow-{shadow_seq}") is True
            # Promise 2: an unparseable payload, newer still.
            _plant_malformed_event(sdk, event_store.next_seq(proj))
        finally:
            sdk.close()

        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["lastBeliefWrite"]["seq"] == seq, body
        assert body["lastBeliefWrite"]["committed"] == {
            "confidence": 0.8, "posteriorAlpha": 8.0, "posteriorBeta": 2.0,
        }, body
        # The shadow row would have published the OTHER node's transition.
        assert body["current"]["alpha"] == 8.0, body
        assert body["current"]["beta"] == 2.0, body


def test_belief_refuses_an_uncalibrated_graph_rather_than_substituting_a_number(tmp_path):
    """A topology-only figure reported as EP is the silent-uncalibrated class
    #7478 targets, so the read REFUSES (409) and names the remedy.

    ⚠️ The fixture has to give the node a PERSISTED belief WITHOUT a prior: the
    gate now asks the node first, so a node carrying no belief at all answers
    `current: null` (200) and never reaches EP — there is nothing to refuse. The
    case that still refuses is exactly the one the gate exists for: a node whose
    belief the graph cannot attribute to any prior. `posterior_alpha/beta` alone
    (no `baseline_set`) is that shape — measured: `check_confidence` raises
    `CalibrationError: 1 uncalibrated evidence points`.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "an unmeasured claim", "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            # A raw property WRITE has no SDK door (nothing sets a posterior
            # without a prior), so it states its arm: `read_only=False`.
            sdk._get_proj().g._query(
                "MATCH (n:Point {id:$id}) "
                "SET n.posterior_alpha = 8.0, n.posterior_beta = 2.0",
                params={"id": p["id"]}, read_only=False)
        finally:
            sdk.close()
        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "EP_UNCALIBRATED", detail
        assert detail["remedy"], detail


def test_belief_does_not_run_ep_for_a_node_with_no_belief(tmp_path):
    """Asking EP for a node with no belief would WRITE (the lazy-consistency
    dream) to produce a number the handler then discards.

    Pinned on the OBSERVABLE consequence rather than on call order: an
    uncalibrated graph answers `200 current: null` for such a node, which is
    reachable only if `check_confidence` was never asked (it raises
    `CalibrationError` on that same graph — see the test above).
    """
    with _graph(str(tmp_path / "g.db")), _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "no belief at all", "kind": "statement"}).json()
        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 200, r.text
        assert r.json()["current"] is None, r.text


def test_belief_never_puts_a_sources_body_in_entity_name(tmp_path):
    """#14593 review P1 (data disclosure, #3998): the FIFTH door, and the cheap one.

    `get_entity_belief` read `resolved[0]["properties"]` RAW — `_layer_payload`,
    which every by-id route applies, was skipped — and then coalesced
    `name -> content -> url`. `content` is NOT in `_SOURCE_NODE_PROP_NAMES`, so
    `filter_source_props` drops it everywhere else; here it was published as
    `entity.name`. Measured: the body answered as the name on `/belief` while
    `/v1/sources/{id}` withheld the same value.

    ⛔ Reachability is not contrived: the session-capture producer writes
    `:Source {url: "session:<id>"}`, a SLASH-FREE identity, so this route's single
    `{entity_id}` segment addresses it (a normal url-keyed Source cannot be
    reached here — its `/` breaks the segment, which the module documents).
    """
    url = "session:rev-14593-capture"
    secret = "SESSION CAPTURE BODY THAT MUST NOT BE A NAME"
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        sdk = _sdk(ha)
        try:
            assert sdk._create_source(url, "web")["id"] == url
            sdk._get_proj().g._query(
                "MATCH (s:Source {url:$u}) SET s.content = $c",
                params={"u": url, "c": secret}, read_only=False)
        finally:
            sdk.close()

        out = client.get(f"/v1/entities/{url}/belief")
        assert out.status_code == 200, out.text
        body = out.json()
        assert body["entity"]["label"] == "Source", body
        assert body["entity"]["name"] == url, body
        assert secret not in out.text, out.text
        # The sibling door withheld it before this fix and must keep doing so.
        assert secret not in client.get(f"/v1/sources/{url}").text


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
        # …and the state vocabulary says the same thing in #11702's own word.
        assert body["ep_state"] == "absent", body


def test_belief_distinguishes_a_declared_prior_from_a_measurement(tmp_path):
    """⛔ P2 (review cycle 4): a DECLARED PRIOR must not answer as a measurement.

    The handler used to decide "is there a belief?" from its own key tuple, and
    `ep_alpha`/`ep_beta`/`baseline_set` are exactly what `set_point_baseline`
    stamps — so a prior with no evidence consumed published as `current` with
    `currentUnavailable: null`, and a declared **Beta(1,1)** reproduced
    byte-for-byte the `{mean: 0.5, alpha: 1.0, beta: 1.0, effective_n: 2.0}`
    payload the absent arm above removes as fabricated. Nothing in the response
    told the two apart.

    #11702 is the recorded rule — a *measured* 0.5 and an *absent/declared* 0.5
    "must never be" the same answer — and #3415 is the second: a prior-only
    baseline's variance is the WIDTH OF THE DECLARED PRIOR, not a measured
    contention. So the route publishes `ep_state` beside `current`, and this
    test pins BOTH arms against each other on the same graph.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        declared = client.post("/v1/points", json={
            "content": "a prior with no evidence",
            "kind": "statement"}).json()
        measured = client.post("/v1/points", json={
            "content": "a claim EP actually measured",
            "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            # Declared Beta(1,1) — the shape whose payload is indistinguishable
            # from the fabricated one if the state is not published.
            sdk._set_point_baseline(declared["id"], 1.0, 1.0)
            sdk._set_point_baseline(measured["id"], 8.0, 2.0)
            # A posterior written beside the prior is what EP's own
            # `_flush_cache` commits, so `posterior_alpha` present is the
            # measured arm (#3276's predicate) — no EP pass needed to pin it.
            sdk._get_proj().g._query(
                "MATCH (n:Point {id:$id}) SET n.posterior_alpha = 9.0, "
                "n.posterior_beta = 1.0, n.confidence = 0.9",
                params={"id": measured["id"]}, read_only=False)
        finally:
            sdk.close()

        d = client.get(f"/v1/entities/{declared['id']}/belief").json()
        m = client.get(f"/v1/entities/{measured['id']}/belief").json()

        # ⛔ The declared prior is REPORTED (it is a real stored belief) …
        assert d["ep_state"] == "baseline", d
        assert d["current"]["alpha"] == 1.0 and d["current"]["beta"] == 1.0, d
        # …and the measured claim is distinguishable from it by the same word.
        assert m["ep_state"] == "measured", m
        assert m["current"]["mean"] == pytest.approx(0.9), m
        # The two facts a client needs are in the payload, not implied:
        assert d["ep_state"] != m["ep_state"], (d, m)


def test_belief_does_not_report_a_terminal_claims_decayed_posterior_as_measured(tmp_path):
    """⛔ P2 (review cycle 5): a terminalising write's decayed Beta(1,1) is a
    STORED VALUE, not a measurement — #2490's rule, applied through the one fold.

    `_retract_point` runs `live.decay_clause`, which stamps the claim with
    `VACUITY_BELIEF` (`confidence=0.5, posterior_alpha=1.0, posterior_beta=1.0`).
    Those columns satisfy `ep_measured_cypher`, so a route that published
    `ep_state` WITHOUT the terminal fold answered `measured` — with
    `{mean: 0.5, alpha: 1.0, beta: 1.0, effective_n: 2.0}`, byte-identical to the
    fabricated payload the absent arm removes, under the one word that claims an
    EP pass produced it. Measured before the fold: the stored row read
    `('retracted', 0.5, 1.0, 1.0, True)` and the route said `measured`.

    The fold is `search_engine.ep_state_resolve` — the ONE place #2490's gate is
    applied (`why.py`, the rankers and `annotate_ep_batch` all call it), fed by
    `live.is_terminal_status`, which is also what those callers use.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a claim that will be retracted",
            "kind": "statement"}).json()
        sdk = _sdk(ha)
        try:
            sdk._set_point_baseline(p["id"], 8.0, 2.0)
            before = client.get(f"/v1/entities/{p['id']}/belief").json()
            assert before["ep_state"] == "baseline", before
            sdk._retract_point(p["id"])
        finally:
            sdk.close()

        body = client.get(f"/v1/entities/{p['id']}/belief").json()
        # The POSTERIOR case: the claim is ended, its stored belief is the
        # decayed vacuity — `measured` would name an EP pass that never made it.
        assert body["ep_state"] == "baseline", body
        # The numbers themselves are still the stored ones (not invented, not
        # nulled): the state is what stops them reading as a measurement.
        assert body["current"]["alpha"] == 1.0, body
        assert body["current"]["beta"] == 1.0, body


def test_belief_folds_the_POSITION_axis_into_the_terminal_state(tmp_path):
    """⛔ review P2: the terminal fold passed the STATUS axis only.

    `live.is_terminal_status` is total over three axes (status, the legacy
    `outdated` flag, and the position axis `isClosed`/`pipelineSlot`, #13034), and
    its own docstring says a caller that fetched those properties must pass them.
    `_resolve_entity` returns `properties(n)`, so they ARE in hand here. Measured
    before this arm: an Object with `isClosed=true` (and, separately,
    `pipelineSlot='terminal'`) and a stored posterior answered
    `ep_state: "measured"` — an ended claim reported as a measurement, the same
    fail-open one axis over from the retracted-Point case above.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        closed = client.post("/v1/objects", json={
            "name": "CLOSED", "objectKind": "goal"}).json()
        staged = client.post("/v1/objects", json={
            "name": "STAGED", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) SET n.posterior_alpha = 7.0, "
                "n.posterior_beta = 3.0",
                params={"id": closed["id"]}, read_only=False)
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) SET n.posterior_alpha = 7.0, "
                "n.posterior_beta = 3.0",
                params={"id": staged["id"]}, read_only=False)
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) SET n.isClosed = true",
                params={"id": closed["id"]}, read_only=False)
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) SET n.pipelineSlot = 'terminal'",
                params={"id": staged["id"]}, read_only=False)
        finally:
            sdk.close()
        for entity in (closed, staged):
            body = client.get(f"/v1/entities/{entity['id']}/belief").json()
            assert body["ep_state"] == "baseline", (entity["name"], body)


def test_the_belief_route_documents_both_readings_of_baseline():
    """⛔ P3 (review): `ep_state: "baseline"` has TWO readings on this route.

    `baseline` is not only a DECLARED PRIOR (a `set_point_baseline` stamp whose
    variance is the prior's own width, #3415): `ep_state_resolve`'s #2490 fold
    also downgrades a TERMINAL claim's DECAYED posterior to `baseline` — exactly
    what `test_belief_does_not_report_a_terminal_claims_decayed_posterior_as_measured`
    above proves (a retracted Point answers `baseline` carrying
    `alpha=beta=1.0`), and `current` then carries the decay, not a prior. The
    route-level comment is the only place a
    client of THIS field is told what the word means, and it stated only the
    prior reading — so a client applying it takes a decayed vacuity for a
    declared prior, the error the surrounding prose exists to prevent.
    `docs/product/mcp-sdk-surface.md` already states both readings; this pin
    keeps the route's copy on the same contract instead of letting a future edit
    drop the second arm again.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "tortoise" / "hosted_api.py").read_text(encoding="utf-8")
    marker = '"ep_state": belief_state,'
    at = source.index(marker)
    prose = source[source.rindex("# ⛔ `measured`", 0, at):at]
    assert "DECLARED" in prose and "PRIOR" in prose, prose
    assert "TERMINAL" in prose and "DECAYED" in prose, (
        "the `ep_state` comment states only the declared-prior reading of "
        "`baseline` — the #2490 terminal fold publishes a DECAYED posterior "
        "under the same word (see "
        "test_belief_does_not_report_a_terminal_claims_decayed_posterior_as_"
        "measured), so the route must say both")


def test_relations_keeps_an_inbound_neighbour_that_carries_no_identity(tmp_path):
    """⛔ P3 (review cycle 4): the self-loop guard must not drop identity-less
    neighbours.

    The reverse branch excluded the self-loop with a COALESCED comparison
    (`coalesce(m.id, m.eventId, m.url) <> coalesce(n.id, n.eventId, n.url)`).
    For a neighbour carrying none of those properties both sides are NULL and
    `NULL <> NULL` is NULL, so the WHERE dropped the row — every INBOUND edge
    from an identity-less node silently vanished, which is the “read as
    isolated” defect this read exists to avoid (`#6976`). `id(m) <> id(n)` is
    total, so the self-loop is still excluded exactly once and no neighbour is
    lost for lacking a name.
    """
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={
            "name": "GAMMA", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            # No public door mints a node without an identity property, which is
            # why the shape is written directly (and stated, not hidden).
            sdk._get_proj().g._query(
                "MATCH (n:Object {id:$id}) "
                "CREATE (anon:Object {name:'NO-IDENTITY'})-[:addresses]->(n)",
                params={"id": a["id"]}, read_only=False)
            # A self-loop in the SAME graph proves the guard still fires once.
            assert sdk._create_edge("related", a["id"], a["id"])
        finally:
            sdk.close()
        body = client.get(f"/v1/entities/{a['id']}/relations").json()
        inbound = [r for r in body["relations"] if r["direction"] == "in"]
        assert len(inbound) == 1, body
        assert inbound[0]["predicate"] == "addresses", inbound
        # Honest about what it could not name: the id is null, not invented.
        assert inbound[0]["other"]["id"] is None, inbound
        assert inbound[0]["other"]["name"] == "NO-IDENTITY", inbound
        # The self-loop is still reported exactly once, as outgoing.
        loops = [r for r in body["relations"]
                 if r["predicate"] == "related"]
        assert [r["direction"] for r in loops] == ["out"], loops


# ── the two readers that carry a three-valued / family-specific answer ──────

def test_declared_relation_packs_reports_an_unreadable_row_set_as_none(monkeypatch):
    """`None` must mean "could not be asked", never "nothing is declared".

    `entity_relations` renders `declarationsReadable: false` + `declaredBy: null`
    off this, so a `{}` here would publish "no installed pack is named" as a
    positive finding about a vocabulary the registry could not answer for. There
    is no route-level way to reach it (a working registry always answers), so the
    reader is pinned directly — the alternative was an untested claim.

    ⚠️ `monkeypatch`, never a hand-rolled save/restore: `domain_loader` calls
    `_get_registry` as a bare module global, so an over-clever restore that drops
    the attribute breaks EVERY later test in the session (measured: nine
    `NameError: name '_get_registry' is not defined` in `tests/test_event_store.py`
    from exactly that).
    """
    import tortoise.domain_loader as dl
    from tortoise import pack_registry

    class _Reg:
        def __init__(self, rows):
            self._rows = rows

        def list_relations(self):
            return self._rows

    def _answer(rows):
        monkeypatch.setattr(dl, "_get_registry", lambda: rows_factory(rows))

    def rows_factory(rows):
        return rows if rows is None else _Reg(rows)

    # (a) rows returned, NOT ONE readable -> None (the fail-closed direction)
    _answer([1, None, {"pack": "venture"}, {"predicate": "   "}])
    assert pack_registry.declared_relation_packs() is None
    # (b) one usable row among garbage -> that row's attribution survives
    _answer(["junk", {"pack": "venture", "predicate": "causes"}])
    assert pack_registry.declared_relation_packs() == {"causes": ("venture",)}
    # (c) an EMPTY row set is a real answer: no pack declares anything.
    _answer([])
    assert pack_registry.declared_relation_packs() == {}
    # (d) declared but no pack named -> the predicate keeps a key with (),
    #     so "declared, unattributed" is distinguishable from "absent".
    _answer([{"predicate": "causes"}])
    assert pack_registry.declared_relation_packs() == {"causes": ()}
    # (e) no registry at all -> None
    _answer(None)
    assert pack_registry.declared_relation_packs() is None


def test_layer_lists_call_the_layers_own_terminal_vocabulary(monkeypatch):
    """`list_layer` must ASK for the layer's own terminal family, per layer.

    `live._terminal_excluded`'s default is the Point family ANDed with the legacy
    `outdated` flag, and its docstring says the entity read surfaces "must pass
    `commit_ops.OBJECT_TERMINAL_STATUSES` … and `include_outdated_flag=False`".
    The two families are identical SETS today — which is exactly why a revert is
    invisible in behaviour, and why the seam has to be graded where it is USED.

    ⛔ This test grades the CALL SITE, not the helper. The first version of it
    called `_layer_terminal_predicate` directly, so replacing `list_layer`'s
    `conditions = [_layer_terminal_predicate(label)]` with the pre-fix
    `conditions = [_terminal_excluded("n.status")]` left the whole suite green
    (measured). The spy below reds on exactly that mutant.
    """
    from tortoise import entity_reads
    from tortoise.commit_ops import (
        OBJECT_TERMINAL_STATUSES,
        SUBJECT_TERMINAL_STATUSES,
    )
    from tortoise.live import _terminal_excluded

    # Shape first: the helper answers each layer's own family, without the
    # Point-only `outdated` conjunct.
    obj = entity_reads._layer_terminal_predicate("Object")
    subj = entity_reads._layer_terminal_predicate("Subject")
    assert obj != _terminal_excluded("n.status"), obj
    assert "n.outdated" not in obj and "n.outdated" not in subj
    assert obj == _terminal_excluded(
        "n.status", OBJECT_TERMINAL_STATUSES, include_outdated_flag=False)
    assert subj == _terminal_excluded(
        "n.status", SUBJECT_TERMINAL_STATUSES, include_outdated_flag=False)

    # …and the CALL SITE goes through it, for the label it was handed.
    calls: list[str] = []
    real = entity_reads._layer_terminal_predicate

    def _spy(label):
        calls.append(label)
        return real(label)

    monkeypatch.setattr(entity_reads, "_layer_terminal_predicate", _spy)

    class _Graph:
        def __init__(self):
            self.queries: list[str] = []

        def ro_query(self, cypher, params=None):
            self.queries.append(cypher)
            # The COUNT answers one row; the page answers none (the assertions are
            # about the WHERE, so the rows are irrelevant and must be dict-shaped
            # or the `dict(r[0])` projection would be what fails).
            result = [[0]] if "count(n)" in cypher else []

            class _R:
                def __init__(self):
                    self.result_set = result

            return _R()

    class _Proj:
        def __init__(self):
            self.g = _Graph()

    proj = _Proj()
    for label in ("Object", "Subject"):
        entity_reads.list_layer(proj, label, limit=5)
    assert calls == ["Object", "Subject"], calls
    # The predicate it produced is the one that reached the WHERE — not the
    # Point default.
    for query in proj.g.queries:
        assert "n.outdated" not in query, query


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_layer_list_pages_over_a_TOTAL_order_at_the_call_site(label):
    """#5346: the offset walk is only safe if the PAGE statement orders totally.

    ⛔ MEASURED GAP (review at ``f775e1a990ef``): deleting ``, id(n)`` from the page
    statement left the ENTIRE file green — 40 passed. Two reasons, both measured:
    the same-name fixture asserts NAMES (two ``SAME`` rows are one string, so a
    repeated row and a dropped row look identical), and this engine happened to
    break equal-name ties the same way on every per-page re-query, so even an
    id-based assertion on that fixture would not have reddened.

    So the order is graded where it is WRITTEN — on the emitted statement — the
    way `test_layer_lists_call_the_layers_own_terminal_vocabulary` grades the
    terminal predicate. `id(n)` rather than `n.id` is the point: entity ids are
    not unique across labels (the module states it), so only the node id makes
    ``(name, id(n))`` total.
    """
    from tortoise import entity_reads

    class _Graph:
        def __init__(self):
            self.queries: list[str] = []

        def ro_query(self, cypher, params=None):
            self.queries.append(cypher)
            result = [[0]] if "count(n)" in cypher else []

            class _R:
                def __init__(self):
                    self.result_set = result

            return _R()

    class _Proj:
        def __init__(self):
            self.g = _Graph()

    proj = _Proj()
    entity_reads.list_layer(proj, label, limit=5, offset=10)

    pages = [q for q in proj.g.queries if "SKIP $skip" in q]
    assert pages, proj.g.queries
    order_prop = entity_reads.LAYER_ORDER_PROP[label]
    for query in pages:
        assert f"ORDER BY n.{order_prop}, id(n) SKIP $skip LIMIT $limit" in query, query
        # and NOT the non-total form, which is the mutant this row exists for
        assert f"ORDER BY n.{order_prop} SKIP" not in query, query
