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
        sdk._get_registry().query("CREATE (t:Team {id:$id})", params={"id": _ORG})
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
def _rest_client():
    from tests._verdict import verdict_client
    from tortoise.hosted_api import app, get_current_org

    app.dependency_overrides[get_current_org] = _team
    try:
        with verdict_client(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def _sdk(ha):
    """A handle on the SAME graph the routes resolve, for seeding shapes no
    public REST door writes (an edge, a lifecycle status, an EP baseline)."""
    return ha._make_sdk(namespace=_ORG)


# ── GET /v1/objects ────────────────────────────────────────────────────────

def test_objects_list_reads_back_what_the_write_door_created(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
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
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        client.post("/v1/objects", json={"name": "ACME", "objectKind": "organisation"})
        client.post("/v1/objects", json={"name": "GROW", "objectKind": "goal"})
        r = client.get("/v1/objects", params={"kind": "goal"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert [o["name"] for o in body["objects"]] == ["GROW"], body


def test_objects_list_pages_over_a_total_order_and_says_it_truncated(tmp_path):
    """A FULL page must not be byte-identical to a COMPLETE one (#8447): the
    marker is what separates them, and `offset` is how a caller continues."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        for name in ("A", "B", "C"):
            client.post("/v1/objects", json={"name": name, "objectKind": "goal"})
        seen: list[str] = []
        for offset in (0, 1, 2):
            r = client.get("/v1/objects", params={"limit": 1, "offset": offset})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["truncated"] is True, body
            # The total is EXACT (a real count under the same WHERE), not a
            # lower bound wearing a total's clothes.
            assert body["total_available"] == 3, body
            seen.extend(o["name"] for o in body["objects"])
        # Total-order paging: every row exactly once, none dropped or repeated.
        assert sorted(seen) == ["A", "B", "C"], seen


def test_objects_list_excludes_ended_rows_and_the_id_still_reads(tmp_path):
    """The `/v1/points` tombstone contract, one layer over: ended rows leave the
    listing surface and stay retrievable BY ID."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        node = client.post(
            "/v1/objects", json={"name": "OLD", "objectKind": "goal"}).json()
        sdk = _sdk(ha)
        try:
            sdk._get_proj().g.query(
                "MATCH (o:Object {id:$id}) SET o.status = 'deprecated'",
                params={"id": node["id"]})
            listed = client.get("/v1/objects").json()
            assert [o["name"] for o in listed["objects"]] == [], listed
            assert client.get(f"/v1/objects/{node['id']}").status_code == 200
        finally:
            sdk.close()


def test_object_by_id_is_scoped_to_its_own_layer(tmp_path):
    """An id that names a Point is a REAL entity this LAYER-scoped route does not
    serve — 404 here, 200 at the route that owns its label."""
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "a claim", "kind": "statement"}).json()
        r = client.get(f"/v1/objects/{p['id']}")
        assert r.status_code == 404, r.text
        assert client.get(f"/v1/points/{p['id']}").status_code == 200
        assert client.get("/v1/objects/does-not-exist").status_code == 404


# ── GET /v1/subjects ───────────────────────────────────────────────────────

def test_subjects_list_and_by_id_mirror_the_objects_route(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
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
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$u}) SET s.text = $t",
                params={"u": url, "t": "RAW BODY THAT MUST NOT LEAK"})
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
            assert sdk._get_proj().create_edge(a["id"], b["id"], "dependsOn")
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
            sdk._get_proj().create_edge(a["id"], b["id"], "related")
        finally:
            sdk.close()
        body = client.get(f"/v1/entities/{a['id']}/relations").json()
        by_pred = {r["predicate"]: r for r in body["relations"]}
        assert by_pred["causes"]["declaredBy"] == ["dev"], by_pred
        assert by_pred["causes"]["mechanism"] == "IMPL", by_pred
        assert by_pred["related"]["declaredBy"] == [], by_pred
        assert body["declarationsReadable"] is True, body


def test_relations_is_layer_agnostic_and_404s_on_an_unknown_entity(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        a = client.post("/v1/objects", json={"name": "ALPHA", "objectKind": "goal"}).json()
        s = client.post("/v1/subjects", json={"name": "Daniel", "subjectKind": "person"}).json()
        sdk = _sdk(ha)
        try:
            sdk._get_proj().create_edge(s["id"], a["id"], "holdsRole")
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
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        p = client.post("/v1/points", json={
            "content": "an unmeasured claim", "kind": "statement"}).json()
        r = client.get(f"/v1/entities/{p['id']}/belief")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["error_code"] == "EP_UNCALIBRATED", detail
        assert detail["remedy"], detail


def test_belief_404s_on_an_unknown_entity(tmp_path):
    with _graph(str(tmp_path / "g.db")) as ha, _rest_client() as client:
        assert client.get("/v1/entities/nope/belief").status_code == 404
