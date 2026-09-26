"""#4889 — the Subject layer's read surfaces must not dress an unanswerable read
as an empty one.

Measured 2026-09-23 on the hosted dogfood graph: **0** ``aboutSubject`` edges
against 27,310 ``aboutObject``, and **1** ``:Subject`` node. The cause is a
label-dispatch defect in the capture entity spine (#4934) plus a second, independent
gap on the document path (its only Subject producer is behind an opt-in flag,
#4938) — neither is a data accident, so until a producer exists every consumer of
``search_engine``'s ``subject`` decoration and of ``get_org_structure``'s
``roles`` leg gets a null/empty that is indistinguishable from a real answer.

These tests pin the fail-loud contract:

- a graph with ZERO ``aboutSubject`` edges makes the decorating surface emit
  ``subject_unavailable``, naming the missing producer and the tracking issues;
- a graph that holds the relation does NOT mark (an empty ``subject`` there
  really does mean "this claim has no subject"), so the marker self-clears the
  moment a producer lands;
- the probe FAILS OPEN on a query error — a broken probe must never invent an
  unavailability claim;
- ``get_org_structure`` marks only the producer-less leg (``roles`` →
  ``holdsRole``); ``members`` is deliberately unmarked because ``memberOf``
  HAS a producer (the onboarding anchor seed);
- ``SearchResult.to_dict`` emits ``subject`` XOR ``subject_unavailable``, and
  neither on a healthy graph (byte-compatible with the pre-#4889 shape).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import (
    _ORG_STRUCTURE_UNPRODUCED_LEGS,
    TortoiseSDK,
    _decorate_fallback_hits,
    _relation_populated,
)
from tortoise.search_engine import (
    SUBJECT_BINDING_UNAVAILABLE,
    SearchResult,
    fetch_point_epistemic_state,
    subject_binding_available,
    subject_binding_unavailable_reason,
)

TEST_GRAPH = "tortoise_test_4889_subject_read_surfaces"


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "subject4889.db"), namespace=TEST_GRAPH)
    yield s
    try:
        s.test_guard()
        s._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    s.close()


def _graph(sdk):
    return sdk._get_proj().g


class _BoomGraph:
    """A graph handle whose every query raises (probe fail-open coverage)."""

    def query(self, *args, **kwargs):
        raise RuntimeError("graph down")


# ── the structural probe ───────────────────────────────────────────────────


def test_probe_false_without_producer_true_once_one_edge_exists(sdk):
    assert subject_binding_available(_graph(sdk)) is False
    pid = sdk.create_point("statement", "a claim about alice")["id"]
    sid = sdk.create_subject("alice", subjectKind="naturalPerson")["id"]
    sdk.create_edge("aboutSubject", pid, sid)
    assert subject_binding_available(_graph(sdk)) is True


def test_probe_fails_open_on_query_error():
    """A broken probe must never manufacture an unavailability claim."""
    assert subject_binding_available(_BoomGraph()) is True


# ── fetch_point_epistemic_state ────────────────────────────────────────────


def test_fetcher_issues_no_extra_query_and_stays_marker_free(sdk):
    """#4889: the availability decision lives at the SURFACE, never inside the
    fetcher — the why-block assembly pins this fetcher's query budget at 6."""
    pid = sdk.create_point("statement", "a claim with no subject")["id"]
    calls = []
    real_query = _graph(sdk).query

    def _counting(self, cypher, params=None, timeout=None):
        calls.append(cypher)
        return real_query(cypher, params=params, timeout=timeout)

    from tortoise.projection import _GuardedGraph
    original = _GuardedGraph.query
    _GuardedGraph.query = _counting
    try:
        st = fetch_point_epistemic_state(_graph(sdk), [pid])[pid]
    finally:
        _GuardedGraph.query = original
    assert st["subject"] is None
    assert "subject_unavailable" not in st
    assert len(calls) == 1, f"the fetcher must stay single-query: {calls}"


def test_surface_reason_when_the_graph_has_no_producer(sdk):
    pid = sdk.create_point("statement", "a claim with no subject")["id"]
    state = fetch_point_epistemic_state(_graph(sdk), [pid])
    reason = subject_binding_unavailable_reason(_graph(sdk), state)
    assert reason == SUBJECT_BINDING_UNAVAILABLE
    # the marker must be actionable: it names the tracking issues.
    for ref in ("#1370", "#1509", "#4934", "#4938"):
        assert ref in reason


def test_surface_reason_is_empty_when_this_point_has_no_subject_but_the_relation_exists(sdk):
    """`subject: None` on a graph WITH a producer means "no subject" — the
    marker must not over-fire."""
    bare = sdk.create_point("statement", "unrelated claim")["id"]
    other = sdk.create_point("statement", "a different claim")["id"]
    other_subject = sdk.create_subject("bob")["id"]
    sdk.create_edge("aboutSubject", other, other_subject)
    state = fetch_point_epistemic_state(_graph(sdk), [bare])
    assert subject_binding_unavailable_reason(_graph(sdk), state) == ""


def test_surface_reason_is_empty_for_an_empty_batch(sdk):
    assert subject_binding_unavailable_reason(_graph(sdk), {}) == ""


def test_state_resolves_the_points_own_subject(sdk):
    pid = sdk.create_point("statement", "a claim about carol")["id"]
    sid = sdk.create_subject("carol", subjectKind="naturalPerson")["id"]
    sdk.create_edge("aboutSubject", pid, sid)
    state = fetch_point_epistemic_state(_graph(sdk), [pid])
    assert state[pid]["subject"]["name"] == "carol"
    assert state[pid]["subject"]["kind"] == "naturalPerson"
    assert subject_binding_unavailable_reason(_graph(sdk), state) == ""


def test_state_resolves_the_event_fallback_through_the_eventid_property(sdk):
    """#1417: the ≤1-hop fallback resolves via the point's `eventId` PROPERTY,
    never via an `aboutEvent` edge."""
    pid = sdk.create_point("statement", "a claim made in a meeting")["id"]
    _graph(sdk).query(
        "MATCH (p:Point {id:$id}) SET p.eventId = 'ev-4889-fallback'",
        params={"id": pid})
    sdk.create_subject("dave", subjectKind="naturalPerson")
    _graph(sdk).query(
        "CREATE (e:Event {eventId:'ev-4889-fallback'})")
    _graph(sdk).query(
        "MATCH (e:Event {eventId:'ev-4889-fallback'}), (s:Subject {name:'dave'}) "
        "CREATE (e)-[:aboutSubject]->(s)")
    state = fetch_point_epistemic_state(_graph(sdk), [pid])
    assert state[pid]["subject"]["name"] == "dave"
    assert subject_binding_unavailable_reason(_graph(sdk), state) == ""


def test_empty_batch_returns_empty_without_probing(sdk):
    assert fetch_point_epistemic_state(_graph(sdk), []) == {}


# ── SearchResult.to_dict ───────────────────────────────────────────────────


def _bare_result(**kwargs):
    return SearchResult(id="p1", content="c", point_kind="statement",
                        **kwargs).to_dict()


def test_to_dict_emits_no_subject_key_on_a_healthy_result():
    d = _bare_result()
    assert "subject" not in d
    assert "subject_unavailable" not in d


def test_to_dict_emits_the_unavailable_marker_instead_of_a_silent_null():
    d = _bare_result(subject_unavailable=SUBJECT_BINDING_UNAVAILABLE)
    assert "subject" not in d
    assert d["subject_unavailable"] == SUBJECT_BINDING_UNAVAILABLE


def test_to_dict_emits_the_subject_when_resolved():
    d = _bare_result(subject={"id": "s1", "name": "alice", "kind": "naturalPerson"})
    assert d["subject"]["name"] == "alice"
    assert "subject_unavailable" not in d


def test_to_dict_never_emits_both():
    d = _bare_result(
        subject={"id": "s1", "name": "alice", "kind": "naturalPerson"},
        subject_unavailable=SUBJECT_BINDING_UNAVAILABLE,
    )
    assert "subject" in d and "subject_unavailable" not in d


def test_search_surface_emits_the_marker_end_to_end(sdk):
    """The field is advertised by `TortoiseSDK.search` — assert on the wire
    shape a consumer actually receives, not just on the helper."""
    pid = sdk.create_point("statement", "a uniquely-phrased claim about zulu")["id"]
    hits = sdk.tortoise_fts_query("zulu uniquely-phrased")
    hit = next(h for h in hits if h.get("id") == pid)
    assert "subject" not in hit
    assert hit["subject_unavailable"] == SUBJECT_BINDING_UNAVAILABLE


def test_search_surface_is_silent_once_a_subject_exists(sdk):
    """The marker self-clears: with the relation populated, the hit carries
    the resolved subject and NO unavailability key."""
    pid = sdk.create_point("statement", "a uniquely-phrased claim about yankee")["id"]
    sid = sdk.create_subject("grace", subjectKind="naturalPerson")["id"]
    sdk.create_edge("aboutSubject", pid, sid)
    hits = sdk.tortoise_fts_query("yankee uniquely-phrased")
    hit = next(h for h in hits if h.get("id") == pid)
    assert hit["subject"]["name"] == "grace"
    assert "subject_unavailable" not in hit


def test_fallback_decoration_propagates_the_marker(sdk):
    """The embedded-fallback tier never resolved a subject — say so rather
    than return the hit undecorated and silent."""
    pid = sdk.create_point("statement", "claim")["id"]
    out = _decorate_fallback_hits([{"id": pid}], _graph(sdk))
    assert out[0]["subject_unavailable"] == SUBJECT_BINDING_UNAVAILABLE


# ── get_org_structure ──────────────────────────────────────────────────────


def test_unproduced_leg_map_declares_only_producer_less_legs():
    assert set(_ORG_STRUCTURE_UNPRODUCED_LEGS) == {"roles"}
    for rel, reason in _ORG_STRUCTURE_UNPRODUCED_LEGS.values():
        assert rel == "holdsRole"
        assert isinstance(reason, str) and reason


def test_org_structure_marks_roles_and_never_members(sdk):
    """`roles` has no automatic producer anywhere (only an explicit
    create_edge('holdsRole', …)); `members` does (the onboarding anchor seed),
    so an empty members list must stay unmarked."""
    org = sdk.create_subject("acme", subjectKind="organization")["id"]
    out = sdk.get_org_structure(org)
    assert out["members"] == []
    assert out["roles"] == []
    assert "members" not in out["unavailable"]
    assert "holdsRole" in out["unavailable"]["roles"]
    assert "#4889" in out["unavailable"]["roles"]


def test_org_structure_is_silent_once_roles_exist(sdk):
    """The marker self-clears: with the relation populated, an empty leg is a
    truthful "this Subject holds no role"."""
    person = sdk.create_subject("erin", subjectKind="naturalPerson")["id"]
    role = sdk.create_subject("reviewer", subjectKind="role")["id"]
    sdk.create_edge("holdsRole", person, role)
    out = sdk.get_org_structure(person)
    assert [r["name"] for r in out["roles"]] == ["reviewer"]
    assert "unavailable" not in out


def test_org_structure_resolves_members_when_filed(sdk):
    org = sdk.create_subject("globex", subjectKind="organization")["id"]
    person = sdk.create_subject("frank", subjectKind="naturalPerson")["id"]
    sdk.create_edge("memberOf", person, org)
    out = sdk.get_org_structure(org)
    assert [m["name"] for m in out["members"]] == ["frank"]


def test_relation_populated_refuses_an_undeclared_relation(sdk):
    """The relation is interpolated into the query STRUCTURE — fail closed on
    anything outside the declared constant set."""
    with pytest.raises(RuntimeError):
        _relation_populated(sdk._get_proj(), "memberOf")
    with pytest.raises(RuntimeError):
        _relation_populated(sdk._get_proj(), "holdsRole`] RETURN 1 //")
