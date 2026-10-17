"""#5509 — the name-keyed identity reads that were still live after #3633.

A name is a MUTABLE natural key. Under #3590 D2 two live ``:Object``/
``:Subject`` carriers may hold one name at the same time (same-name
coexistence is legal and is produced by explicit-id writers — and, measured in
this file, by a rename through the public ``update_entity``), so a read that
resolves a name as if it were an identity has a choice the caller never
expressed. The historic answers were both wrong:

* **union** — ``MATCH (o:Object) WHERE o.name = $topic`` returns every carrier,
  so two identities' Points/edges are folded into one value;
* **silent pick** — ``LIMIT 1`` / ``result_set[0]`` hands back a
  backend-order-dependent carrier.

This file pins the #5509 slice. ``test_5509_belief_timeline_*`` is the rename
reproduction the issue asks for: the SAME ``belief_timeline("Acme")`` call
answers with one company's decision before the rename and with both companies'
decisions after it.

Each behaviour test names, in its docstring, the pre-fix answer that makes it
fail on ``origin/main``. The two ``*_does_not_resolve_identity_by_name`` tests
are the structural half: the canonical-id re-fetch in ``_create_entity`` and
``mining._reify_entities`` was a name→id read whose row the write had already
determined (``ON MATCH SET o.id=coalesce($id, o.id)``), so they assert the read
is GONE rather than that its result is wrong.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.entity_identity import (
    AmbiguousEntityName,
    clear_non_folded_entries,
    non_folded_entries,
    resolve_entity_id,
)
from tortoise.ids import now_iso
from tortoise.mining import ConversationMiner
from tortoise.projection.edges import resolve_structural_target
from tortoise.subject_binding import bind_point_subjects


@pytest.fixture(autouse=True)
def _clean_non_folded_record():
    """The non-folded record is process-level (interim until Slice 0)."""
    clear_non_folded_entries()
    yield
    clear_non_folded_entries()


class _RecordingGraph:
    """A graph handle that records every statement, then delegates.

    Wrapped around ``proj.g`` so the assertions below see the SDK's OWN reads
    (including the projection's) — a name-keyed identity read has to be absent
    from the traffic, not merely harmless.
    """

    def __init__(self, inner):
        self._inner = inner
        self.queries: list[str] = []

    def query(self, cypher, params=None, timeout=None):
        self.queries.append(" ".join(cypher.split()))
        return self._inner.query(cypher, params=params, timeout=timeout)

    def __getattr__(self, name):
        return getattr(self._inner, name)


#: The deleted re-fetches: `MATCH (n:{label} {name:$name}) RETURN n.id` (the
#: alias varies by site: `n` in `_create_entity`, `o` in mining). Matched
#: loosely so the pre-fix `{name: $name}` / `{name:$name}` spellings both hit.
_NAME_ID_REFETCH = re.compile(
    r"MATCH \(\w+:(?:Object|Subject) \{name: ?\$name\}\) RETURN \w+\.id")



def _about_decision(sdk, content: str, object_id: str) -> str:
    """A decision Point aboutObject-linked to ``object_id`` (id-keyed write)."""
    pid = sdk.create_point("decision", content)["id"]
    sdk._get_proj().create_about_edge(pid, object_id, "aboutObject")
    return pid


class TestBeliefTimelineIdentityRead:
    def test_rename_appends_the_other_carrier_on_main(self, sdk_factory):
        """THE REPRODUCTION. Pre-fix, the second `belief_timeline("Acme")`
        returns BOTH companies' decisions — a rename made the name ambiguous
        and the name-keyed read unioned the two carriers' Points."""
        sdk = sdk_factory()
        acme = sdk.create_object("Acme", "company")
        beta = sdk.create_object("Beta", "company")
        _about_decision(sdk, "Acme should ship in Q1", acme["id"])
        _about_decision(sdk, "Beta should ship in Q3", beta["id"])

        before = sdk.belief_timeline("Acme")
        assert [t["content"] for t in before] == ["Acme should ship in Q1"], (
            f"one live carrier must resolve to its own chain: {before}")

        # The rename that produces the D2 shape through a PUBLIC door: "Beta"
        # is a different identity, and its name is now the same natural key.
        sdk.update_entity(beta["id"], name="Acme")

        try:
            after = sdk.belief_timeline("Acme")
        except AmbiguousEntityName as exc:
            after = None
            assert set(exc.candidate_ids) == {acme["id"], beta["id"]}
        assert after is None, (
            "the name-keyed read UNIONED two identities' belief chains after "
            f"the rename: {[t['content'] for t in after]}")
        assert [e.shape for e in non_folded_entries()] == ["ambiguous-name"]

    def test_terminal_only_topic_is_refused_and_recorded(self, sdk_factory):
        """Pre-fix, the name-keyed read has no status predicate, so a name held
        only by a SUPERSEDED Object still returned that dead identity's chain.
        D2's ``single_terminal_holder_reference`` refuses it."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        obj = sdk.create_object("Acme", "company")
        _about_decision(sdk, "Acme should ship in Q1", obj["id"])
        proj.g.query("MATCH (o:Object {id:$id}) SET o.status='superseded'",
                     params={"id": obj["id"]})

        assert sdk.belief_timeline("Acme") == []
        assert [e.shape for e in non_folded_entries()] == ["no-live-holder"]

    def test_one_live_carrier_still_resolves(self, sdk_factory):
        """Parity: the routed read keeps the single-live-holder answer (both
        sides pass — pinned so the route cannot be "fixed" by always refusing).
        """
        sdk = sdk_factory()
        obj = sdk.create_object("Acme", "company")
        _about_decision(sdk, "Acme should ship in Q1", obj["id"])
        _about_decision(sdk, "Acme should ship in Q2", obj["id"])
        timeline = sdk.belief_timeline("Acme")
        assert [t["content"] for t in timeline] == [
            "Acme should ship in Q1", "Acme should ship in Q2"]

    def test_canonical_name_coordinate_still_resolves(self, sdk_factory):
        """Parity for the widened name arm: ``canonical_name`` was a legal
        coordinate before this change and must stay one — but through the
        resolver's holder count, not a second union arm."""
        sdk = sdk_factory()
        obj = sdk.create_object("Acme Corporation", "company",
                                canonical_name="acme-corp")
        _about_decision(sdk, "Acme should ship in Q1", obj["id"])
        assert [t["content"] for t in sdk.belief_timeline("acme-corp")] == [
            "Acme should ship in Q1"]

    def test_an_id_coordinate_resolves(self, sdk_factory):
        """The resolver's id arm: an id names exactly one node, so it is the
        one coordinate that never needs a holder count."""
        sdk = sdk_factory()
        obj = sdk.create_object("Acme", "company")
        _about_decision(sdk, "Acme should ship in Q1", obj["id"])
        assert [t["content"] for t in sdk.belief_timeline(obj["id"])] == [
            "Acme should ship in Q1"]


class TestSubjectBinderTargetIdentityRead:
    def test_terminal_only_holder_is_not_bound(self, sdk_factory):
        """Pre-fix, ``_resolve_target``'s ``LIMIT 2`` probe had no status
        predicate, so a name held by exactly ONE **superseded** Subject
        resolved to it and the fresh claim got an ``aboutSubject`` edge to a
        dead identity (D2: never hand a caller a terminal id)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        sid = sdk.create_entity("subject", "the team",
                                subjectKind="core:team")["node"]["id"]
        proj.g.query("MATCH (n:Subject {id:$id}) SET n.status='superseded'",
                     params={"id": sid})
        pid = sdk.create_point("statement", "the team shipped")["id"]

        out = bind_point_subjects(proj, sdk, point_id=pid, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.99}]})

        assert [(o["outcome"], o["reason"]) for o in out] == [
            ("unbound", "unresolved")]
        assert proj.g.query(
            "MATCH (:Point {id:$p})-[:aboutSubject]->(s) RETURN s.id",
            params={"p": pid}).result_set == []
        assert [e.shape for e in non_folded_entries()] == ["no-live-holder"]

    def test_two_live_same_name_subjects_are_not_bound(self, sdk_factory):
        """Parity + record: two live carriers were already refused pre-fix
        (``len(rows) != 1``), but the refusal existed nowhere — this pins that
        it is now RECORDED (the pre-fix run fails on the empty entry list)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for sid in ("sub-a", "sub-b"):
            proj.g.query(
                "CREATE (s:Subject {id:$id, name:'the team', "
                "subjectKind:'core:team', status:'live'})",
                params={"id": sid})
        pid = sdk.create_point("statement", "the team shipped")["id"]
        out = bind_point_subjects(proj, sdk, point_id=pid, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.99}]})
        assert out[0]["outcome"] == "unbound"
        assert [e.shape for e in non_folded_entries()] == ["ambiguous-name"]

    def test_one_live_holder_binds_with_its_stored_kind(self, sdk_factory):
        """Parity: the resolved id's KIND still comes back (a second, ID-KEYED
        property read) and the F10 kind gate still fires on a mismatch."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        sdk.create_entity("subject", "the team", subjectKind="core:team")
        pid = sdk.create_point("statement", "the team shipped")["id"]
        ok = bind_point_subjects(proj, sdk, point_id=pid, slots={"subject": [
            {"name": "the team", "kind": "core:team", "confidence": 0.99}]})
        assert (ok[0]["outcome"], ok[0]["reason"]) == ("bound", "linked")

        pid2 = sdk.create_point("statement", "the team shipped again")["id"]
        bad = bind_point_subjects(proj, sdk, point_id=pid2, slots={"subject": [
            {"name": "the team", "kind": "core:role", "confidence": 0.99}]})
        assert (bad[0]["outcome"], bad[0]["reason"]) == (
            "unbound", "kind_mismatch")


class TestStructuralTargetIdentityRead:
    def test_subject_two_carriers_are_refused(self, sdk_factory):
        """Pre-fix, `resolve_structural_target` read `LIMIT 1`, so a replay
        descriptor for a name held by two Subjects attached to an arbitrary
        carrier (backend order) — a rebuild could target a different node than
        the live graph did."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for sid in ("s-a", "s-b"):
            proj.g.query(
                "CREATE (s:Subject {id:$id, name:'dup', "
                "subjectKind:'analyst', status:'live'})",
                params={"id": sid})
        assert resolve_structural_target(
            proj.g, "Subject", "dup", "aboutSubject") is None
        assert [e.shape for e in non_folded_entries()] == ["ambiguous-name"]

    def test_object_two_carriers_are_refused(self, sdk_factory):
        """The same silent pick on the f-string label arm (Object/Event/Point,
        which the literal `:Object {name:` sweep cannot see)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for oid in ("obj-a", "obj-b"):
            proj.g.query(
                "CREATE (o:Object {id:$id, name:'dup', "
                "objectKind:'thing', status:'live'})",
                params={"id": oid})
        assert resolve_structural_target(
            proj.g, "Object", "dup", "aboutObject") is None
        assert [e.shape for e in non_folded_entries()] == ["ambiguous-name"]

    def test_one_holder_still_resolves(self, sdk_factory):
        """Parity: one holder keeps resolving (an id that names one node)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        sdk.create_entity("subject", "solo", subjectKind="analyst")
        row = resolve_structural_target(
            proj.g, "Subject", "solo", "aboutSubject")
        assert row is not None and row["logical"]


class TestCanonicalIdIsNotReReadByName:
    def test_create_entity_issues_no_name_keyed_id_read(self, sdk_factory):
        """Pre-fix, `_create_entity` re-fetched the canonical id with
        ``MATCH (n:{label} {name: $name}) RETURN n.id`` and took
        ``result_set[0][0]`` — an arbitrary name-keyed row. The id is already
        determined by the write (``ON MATCH SET o.id=coalesce($id, o.id)``), so
        the read must not exist at all."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        recorder = _RecordingGraph(proj.g)
        proj.g = recorder
        try:
            obj = sdk.create_object("Acme Widget", "company")
            subj = sdk.create_entity("subject", "Acme Team",
                                     subjectKind="core:team")["node"]
            # The deterministic canonical ids the write path supplied.
            assert obj["id"].startswith("obj-")
            assert subj["id"].startswith("sub-")
            stored = proj.g.query(
                "MATCH (n) WHERE n.id IN $ids RETURN n.id",
                params={"ids": [obj["id"], subj["id"]]}).result_set
            assert {r[0] for r in stored} == {obj["id"], subj["id"]}
        finally:
            proj.g = recorder._inner
        leftovers = [q for q in recorder.queries if _NAME_ID_REFETCH.search(q)]
        assert leftovers == [], (
            "the create path still resolves its identity by NAME: "
            f"{leftovers}")

    def test_reify_entities_issues_no_name_keyed_id_read(self, sdk_factory):
        """The same removed read on ``mining._reify_entities`` (it fed the
        mention's ``aboutObject`` wiring, so an arbitrary same-name row there
        wires a claim onto a node this call did not write)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()

        class _Api:
            """The two EventAPI members the reify phase uses."""

            def __init__(self, projection):
                self.projection = projection

            def add_object(self, name, kind, *, id=None, **props):
                projection = self.projection
                projection.apply({
                    "type": "ObjectRegistered", "id": id, "name": name,
                    "objectKind": kind, "createdAt": now_iso(),
                    "canonical_name": props.get("canonical_name"),
                    "title": props.get("title")})
                return id

        recorder = _RecordingGraph(proj.g)
        proj.g = recorder
        try:
            wired = ConversationMiner(None)._reify_entities(
                "A transcript about Acme Corp.",
                "session:s-5509", _Api(proj),
                [{"name": "Acme Corp", "objectKind": "company"}], [], [])
            assert wired == 1
            rows = proj.g.query(
                "MATCH (o:Object {name:'Acme Corp'}) RETURN o.id",
            ).result_set
        finally:
            proj.g = recorder._inner
        canonical = ConversationMiner._object_id("Acme Corp")
        assert [r[0] for r in rows] == [canonical]
        leftovers = [q for q in recorder.queries if _NAME_ID_REFETCH.search(q)]
        assert leftovers == [], (
            "reification still resolves its identity by NAME: "
            f"{leftovers}")


class TestResolverNameProperties:
    """The resolver's ``name_props`` arm — ONE holder count over every name
    spelling, with the live predicate bound to the WHOLE disjunction."""

    def test_a_terminal_canonical_name_holder_does_not_count_as_live(
            self, sdk_factory):
        """Pins the parentheses in ``_name_predicate``: unparenthesised, the
        status predicate would bind to the second arm only, the superseded
        holder would count as live, and the name would refuse."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query(
            "CREATE (o:Object {id:'obj-live', name:'Acme', "
            "objectKind:'company', status:'live'})")
        g.query(
            "CREATE (o:Object {id:'obj-dead', name:'Other', "
            "canonical_name:'Acme', objectKind:'company', status:'superseded'})")
        assert resolve_entity_id(
            g, "Object", "Acme",
            name_props=("name", "canonical_name")) == "obj-live"
        # The default arm sees only `name`, so the canonical_name carrier is
        # not a candidate at all.
        assert resolve_entity_id(g, "Object", "Acme") == "obj-live"

    def test_two_live_spellings_refuse(self, sdk_factory):
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query(
            "CREATE (o:Object {id:'obj-a', name:'Acme', "
            "objectKind:'company', status:'live'})")
        g.query(
            "CREATE (o:Object {id:'obj-b', name:'Other', "
            "canonical_name:'Acme', objectKind:'company', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(g, "Object", "Acme",
                              name_props=("name", "canonical_name"))

    def test_an_unknown_name_property_is_refused(self, sdk_factory):
        """``name_props`` is interpolated into the statement structure — an
        unknown one must fail loudly, exactly like an unknown label."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        with pytest.raises(RuntimeError):
            resolve_entity_id(g, "Object", "Acme", name_props=("nam3",))
