"""#4889 — the Subject layer's read surfaces must fail LOUD, never silently empty.

The read-only disposition census (which predicate is known-accepted /
producer-tracked / defect) lives on issue #4889. This file pins the CODE half
of the issue's acceptance criteria — the surfaces that can return an empty
value that is indistinguishable from "there is nothing here":

* ``search_engine.subject_binding_available`` — the exact, fail-OPEN probe
  behind ``SearchResult.subject_unavailable``.
* ``SearchResult.to_dict`` — the additive marker, emitted ONLY when set.
* ``TortoiseSDK.get_org_structure`` — the additive ``unavailable`` map for the
  producer-less ``holdsRole`` leg. ``memberOf`` is deliberately NOT marked: it
  has a real producer (``onboarding/seed.py::seed_onboarding_anchors``), so an
  empty ``members`` list is a finding, not a gap.

The load-bearing property is the FAIL-OPEN direction: a probe error must
withhold the marker, because a broken probe inventing "this surface is
unavailable" is a false claim about the artifact.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from tortoise.search_engine import (
    SUBJECT_BINDING_UNAVAILABLE,
    SearchResult,
    subject_binding_available,
)
from tortoise.sdk import (
    HOLDS_ROLE_UNAVAILABLE,
    TortoiseSDK,
    _holds_role_available,
)


class _ResultSet:
    def __init__(self, rows):
        self.result_set = rows


class _FakeGraph:
    """Minimal ``graph.query`` stand-in: returns rows or raises."""

    def __init__(self, rows=None, exc=None):
        self._rows = rows
        self._exc = exc
        self.calls: list[str] = []
        self.kwargs: list[dict] = []

    def query(self, cypher, **kwargs):
        self.calls.append(cypher)
        self.kwargs.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return _ResultSet(self._rows)


@pytest.fixture
def sdk():
    """Fresh SDK on an isolated embedded graph (mirrors tests/test_about_edges.py)."""
    db_path = f"{tempfile.mkdtemp(prefix='tt_4889_')}/test.db"
    s = TortoiseSDK(db_path)
    s.test_guard = lambda: None  # bypass production guard for test graph
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


# ── subject_binding_available: exact, and fail-open ──────────────────────

class TestSubjectBindingAvailable:
    def test_zero_edges_is_unavailable(self):
        # Two UNION ALL legs, each an integer row (0 when it matches nothing).
        g = _FakeGraph(rows=[[0], [0]])
        assert subject_binding_available(g) is False

    def test_any_edge_is_available(self):
        g = _FakeGraph(rows=[[3], [0]])
        assert subject_binding_available(g) is True

    def test_probe_sums_both_legs(self):
        """The Point and Event legs are separate rows; only their SUM
        decides."""
        assert subject_binding_available(_FakeGraph(rows=[[0], [1]])) is True
        assert subject_binding_available(_FakeGraph(rows=[[2], [3]])) is True
        assert subject_binding_available(_FakeGraph(rows=[[0], [0]])) is False

    def test_probe_is_bounded(self):
        """An unbounded count on the search path is the defect this probe
        must not reintroduce — it carries a timeout."""
        g = _FakeGraph(rows=[[0], [0]])
        subject_binding_available(g)
        assert g.kwargs[0].get("timeout") is not None

    def test_probe_error_fails_open(self):
        """A broken probe must NOT invent an unavailability claim."""
        g = _FakeGraph(exc=RuntimeError("graph down"))
        assert subject_binding_available(g) is True

    def test_empty_result_set_fails_open(self):
        g = _FakeGraph(rows=[])
        assert subject_binding_available(g) is True

    @pytest.mark.parametrize("rows", [[[]], [[None]], [["x"]]])
    def test_malformed_probe_result_fails_open(self, rows):
        """``RETURN count(r)`` yields integer rows; anything else is an
        anomalous probe and must not be read as "no edges"."""
        assert subject_binding_available(_FakeGraph(rows=rows)) is True

    def test_probe_is_one_query(self):
        g = _FakeGraph(rows=[[0], [0]])
        subject_binding_available(g)
        assert len(g.calls) == 1

    def test_probe_is_scoped_to_the_sources_the_field_reads(self):
        """The advertised ``subject`` resolves Point/Event → Subject only. An
        Object-sourced ``aboutSubject`` edge (the GitHub connector) must not
        make the probe report "available", or the marker would be withheld on
        a graph where every Point hit is still silently subject-less."""
        from tortoise.search_engine import _SUBJECT_SOURCE_SCOPED_PROBE
        assert ":Point" in _SUBJECT_SOURCE_SCOPED_PROBE
        assert ":Event" in _SUBJECT_SOURCE_SCOPED_PROBE
        assert "(:Subject)" in _SUBJECT_SOURCE_SCOPED_PROBE
        g = _FakeGraph(rows=[[0], [0]])
        subject_binding_available(g)
        assert g.calls[0] == _SUBJECT_SOURCE_SCOPED_PROBE


# ── SearchResult.to_dict: additive marker ────────────────────────────────

class TestSubjectUnavailableMarker:
    def test_absent_when_unset(self):
        r = SearchResult(id="p1", content="c", point_kind="statement")
        d = r.to_dict()
        assert "subject_unavailable" not in d
        assert "subject" not in d

    def test_emitted_when_set(self):
        r = SearchResult(id="p1", content="c", point_kind="statement")
        r.subject_unavailable = SUBJECT_BINDING_UNAVAILABLE
        d = r.to_dict()
        assert d["subject_unavailable"] == SUBJECT_BINDING_UNAVAILABLE
        # The marker never fabricates a subject.
        assert "subject" not in d

    def test_reason_names_the_tracked_producers(self):
        # Actionable by construction: the message must point somewhere.
        for token in ("#4934", "#4938", "#1370", "#1509", "aboutSubject"):
            assert token in SUBJECT_BINDING_UNAVAILABLE


# ── search path: the marker actually reaches the wire ────────────────────

class TestSearchPathFailsLoud:
    def test_search_marks_subject_unavailable_on_a_producerless_graph(self, sdk):
        sdk.create_point("statement", "the census lane measured this graph")
        hits = sdk.tortoise_fts_query("census lane", entity_type="point", limit=5)
        assert hits, "FTS should find the point just written"
        assert all("subject" not in h for h in hits)
        assert all(h.get("subject_unavailable") == SUBJECT_BINDING_UNAVAILABLE
                   for h in hits)

    def test_search_self_clears_once_a_producer_edge_exists(self, sdk):
        sdk.create_point("statement", "self-clear probe point")
        sdk.create_subject("B7-cost-research", "team")
        proj = sdk._get_proj()
        sid = proj.g.query(
            "MATCH (s:Subject {name:'B7-cost-research'}) RETURN s.id"
        ).result_set[0][0]
        pid = proj.g.query(
            "MATCH (p:Point {content:'self-clear probe point'}) RETURN p.id"
        ).result_set[0][0]
        proj.g.query(
            "MATCH (p:Point {id:$p}), (s:Subject {id:$s}) "
            "MERGE (p)-[:aboutSubject]->(s)",
            params={"p": pid, "s": sid})
        hits = sdk.tortoise_fts_query("self-clear probe", entity_type="point",
                                      limit=5)
        assert hits
        for h in hits:
            assert "subject_unavailable" not in h, (
                "the marker must self-clear the moment a producer edge exists")
        # The subject itself now resolves.
        assert hits[0]["subject"]["name"] == "B7-cost-research"

    def test_object_sourced_aboutSubject_does_not_clear_the_marker(self, sdk):
        """The advertised ``subject`` resolves Point/Event → Subject only. An
        Object-sourced ``aboutSubject`` edge (the GitHub connector's shape,
        ``(o:Object)-[:aboutSubject]->(s:Subject)``) can never populate the
        Point field, so it must not silence a marker that is still true for
        every Point hit — that is why the probe is label-scoped."""
        sdk.create_point("statement", "object-sourced probe point")
        sdk.create_subject("W3A lane", "team")
        sdk.create_object("an indexed repository", "repository")
        proj = sdk._get_proj()
        sid = proj.g.query(
            "MATCH (s:Subject {name:'W3A lane'}) RETURN s.id"
        ).result_set[0][0]
        oid = proj.g.query(
            "MATCH (o:Object {name:'an indexed repository'}) RETURN o.id"
        ).result_set[0][0]
        proj.g.query(
            "MATCH (o:Object {id:$o}), (s:Subject {id:$s}) "
            "MERGE (o)-[:aboutSubject]->(s)",
            params={"o": oid, "s": sid})
        hits = sdk.tortoise_fts_query("object-sourced probe",
                                      entity_type="point", limit=5)
        assert hits
        assert all("subject" not in h for h in hits)
        assert all(h.get("subject_unavailable") == SUBJECT_BINDING_UNAVAILABLE
                   for h in hits), (
            "an Object-sourced aboutSubject edge cannot populate the Point "
            "field, so it must not suppress the marker")

    def test_degraded_fallback_carries_the_marker(self, sdk, monkeypatch):
        """The TF-IDF fallback advertises the same promoted-state fields as
        the primary path, so it owes the same fail-loud contract."""
        import tortoise.search_engine as se
        sdk.create_point("statement", "fallback marker probe point")
        monkeypatch.setattr(se, "degradation_chain", lambda *a, **k: {})
        hits = sdk.tortoise_fts_query("fallback marker probe",
                                      entity_type="point", limit=5)
        assert hits, "the degraded fallback should still serve the point"
        assert all(h.get("subject_unavailable") == SUBJECT_BINDING_UNAVAILABLE
                   for h in hits)

    def test_degraded_fallback_does_not_mark_when_a_producer_exists(
            self, sdk, monkeypatch):
        import tortoise.search_engine as se
        sdk.create_point("statement", "fallback producer probe point")
        sdk.create_subject("W3A lane", "team")
        proj = sdk._get_proj()
        sid = proj.g.query(
            "MATCH (s:Subject {name:'W3A lane'}) RETURN s.id"
        ).result_set[0][0]
        pid = proj.g.query(
            "MATCH (p:Point {content:'fallback producer probe point'}) "
            "RETURN p.id").result_set[0][0]
        proj.g.query(
            "MATCH (p:Point {id:$p}), (s:Subject {id:$s}) "
            "MERGE (p)-[:aboutSubject]->(s)",
            params={"p": pid, "s": sid})
        monkeypatch.setattr(se, "degradation_chain", lambda *a, **k: {})
        hits = sdk.tortoise_fts_query("fallback producer probe",
                                      entity_type="point", limit=5)
        assert hits
        for h in hits:
            assert "subject_unavailable" not in h, (
                "a producer edge must suppress the marker on the fallback path")
            assert h.get("subject", {}).get("name") == "W3A lane"


# ── get_org_structure: roles marked, members NOT marked ──────────────────

class TestGetOrgStructure:
    def test_roles_marked_unavailable_on_a_producerless_graph(self, sdk):
        sdk.create_subject("root-org", "organization")
        org = sdk.get_org_structure("root-org")
        assert org["members"] == []
        assert org["roles"] == []
        assert org["unavailable"] == {"roles": HOLDS_ROLE_UNAVAILABLE}

    def test_members_are_not_marked_because_memberOf_has_a_producer(self, sdk):
        """memberOf has a real producer — an empty list is a finding."""
        sdk.create_subject("root-org", "organization")
        sdk.create_subject("member-1", "role")
        proj = sdk._get_proj()
        root = proj.g.query(
            "MATCH (s:Subject {name:'root-org'}) RETURN s.id"
        ).result_set[0][0]
        m1 = proj.g.query(
            "MATCH (s:Subject {name:'member-1'}) RETURN s.id"
        ).result_set[0][0]
        proj.g.query(
            "MATCH (p:Subject {id:$m}), (s:Subject {id:$r}) "
            "MERGE (p)-[:memberOf]->(s)",
            params={"m": m1, "r": root})
        org = sdk.get_org_structure(root)
        assert any(m["id"] == m1 for m in org["members"])
        # roles stays marked; members is never in the map.
        assert "members" not in org["unavailable"]

    def test_non_subject_holdsRole_edge_does_not_suppress_the_marker(self, sdk):
        """The read traverses Subject→Subject only; an Object→Subject
        ``holdsRole`` edge (reachable via ``create_edge``) cannot populate
        ``roles``, so it must not silence the marker."""
        sdk.create_subject("root-org", "organization")
        sdk.create_object("a stray object", "repository")
        proj = sdk._get_proj()
        root = proj.g.query(
            "MATCH (s:Subject {name:'root-org'}) RETURN s.id"
        ).result_set[0][0]
        oid = proj.g.query(
            "MATCH (o:Object {name:'a stray object'}) RETURN o.id"
        ).result_set[0][0]
        proj.g.query(
            "MATCH (o:Object {id:$o}), (s:Subject {id:$s}) "
            "MERGE (o)-[:holdsRole]->(s)",
            params={"o": oid, "s": root})
        org = sdk.get_org_structure("root-org")
        assert org["roles"] == []
        assert org["unavailable"] == {"roles": HOLDS_ROLE_UNAVAILABLE}, (
            "a non-Subject holdsRole edge cannot populate `roles`, so the "
            "marker must stay")

    def test_roles_marker_self_clears_on_a_holdsRole_edge(self, sdk):
        sdk.create_subject("root-org", "organization")
        sdk.create_subject("boss", "role")
        proj = sdk._get_proj()
        root = proj.g.query(
            "MATCH (s:Subject {name:'root-org'}) RETURN s.id"
        ).result_set[0][0]
        boss = proj.g.query(
            "MATCH (s:Subject {name:'boss'}) RETURN s.id"
        ).result_set[0][0]
        proj.g.query(
            "MATCH (p:Subject {id:$p}), (r:Subject {id:$r}) "
            "MERGE (p)-[:holdsRole]->(r)",
            params={"p": root, "r": boss})
        org = sdk.get_org_structure(root)
        assert "unavailable" not in org, (
            "the marker must self-clear the moment a holdsRole edge exists")
        assert any(r["id"] == boss for r in org["roles"])


class TestHoldsRoleProbe:
    def test_zero_edges_is_unavailable(self):
        assert _holds_role_available(_FakeGraph(rows=[[0]])) is False

    def test_any_edge_is_available(self):
        assert _holds_role_available(_FakeGraph(rows=[[1]])) is True

    def test_probe_is_scoped_to_the_read_shape(self):
        """The read is ``(:Subject)-[:holdsRole]->(:Subject)``; a non-Subject
        ``holdsRole`` edge must not suppress the marker."""
        from tortoise.sdk import _HOLDS_ROLE_SCOPED_PROBE
        assert ":Subject" in _HOLDS_ROLE_SCOPED_PROBE
        assert "holdsRole" in _HOLDS_ROLE_SCOPED_PROBE
        g = _FakeGraph(rows=[[0]])
        _holds_role_available(g)
        assert g.calls[0] == _HOLDS_ROLE_SCOPED_PROBE

    def test_probe_is_bounded(self):
        g = _FakeGraph(rows=[[0]])
        _holds_role_available(g)
        assert g.kwargs[0].get("timeout") is not None

    def test_probe_error_fails_open(self):
        assert _holds_role_available(_FakeGraph(exc=RuntimeError("down"))) is True

    def test_empty_result_set_fails_open(self):
        assert _holds_role_available(_FakeGraph(rows=[])) is True

    @pytest.mark.parametrize("rows", [[[]], [[None]], [["x"]]])
    def test_malformed_probe_result_fails_open(self, rows):
        assert _holds_role_available(_FakeGraph(rows=rows)) is True
