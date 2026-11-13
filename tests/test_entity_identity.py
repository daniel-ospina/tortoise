"""#3633 — name-keyed Object/Subject identity reads route-then-refuse.

An Object/Subject coordinate given as a **name** may be held by two live
entities at once (#3590 D2, explicit-id writers). A read that resolved the name
as an id either unioned both carriers (a fold) or took an arbitrary ``LIMIT 1``
(silent pick). This file pins the fix at every site issue #3633 names:

* ``TortoiseSDK.compute_reputation`` / ``get_owned_entities`` /
  ``get_org_structure`` / ``file_human_approval`` (approver + artifact);
* ``onboarding.seed.find_subject_by_name`` (and the seed's ``SubjectCollision``
  mapping);
* the ``id IN $ids OR name IN $names`` list unions in ``commit_ops`` and
  ``assembly``;
* the resolver itself (``tortoise/entity_identity.py``).

Each fixed site has a **(a)** single-live-holder resolves test and a **(b)**
two-live-same-name refuses test, so the disposition is pinned in both
directions. A source-level sweep then asserts no *new* name-keyed identity read
lands outside the documented allowlist (the unlisted reads are enumerated for
the follow-up issue; see the PR body).
"""
from __future__ import annotations

import ast
import collections
import io
import os
import re
import sys
import tokenize
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.assembly import docker_resolver_port
from tortoise.commit_ops import apply_supersessions
from tortoise.entity_identity import (
    ADDRESSING_SOURCE,
    AmbiguousEntityName,
    UnaddressableEntityName,
    clear_non_folded_entries,
    non_folded_entries,
    resolve_document_target_id,
    resolve_entity_id,
)
from tortoise.onboarding.seed import (
    SubjectCollision,
    find_subject_by_name,
    seed_onboarding_anchors,
)

# ── helpers ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_non_folded_record():
    """The non-folded record is process-level (interim until Slice 0); reset it
    so an assertion on it cannot see a previous test's entry."""
    clear_non_folded_entries()
    yield
    clear_non_folded_entries()


def _create_subject_rows(sdk, name, ids):
    """Raw-CREATE one live Subject per id under ``name``.

    The SDK's own ``create_entity`` door does not MERGE on ``{name}`` — since
    #3590 the write is keyed on the record's own ``id`` and the door RESOLVES
    the name first, adopting the single live holder — so no public writer
    produces two carriers of one name by accident. Two live same-name Subjects
    are the D2 shape an EXPLICIT-ID writer produces; a raw write is how a test
    reaches it directly.
    """
    proj = sdk._get_proj()
    for sid in ids:
        proj.g.query(
            "CREATE (s:Subject {id:$id, name:$name, subjectKind:'analyst', "
            "status:'live'})",
            params={"id": sid, "name": name})


def _create_object_rows(sdk, name, ids):
    proj = sdk._get_proj()
    for oid in ids:
        proj.g.query(
            "CREATE (o:Object {id:$id, name:$name, objectKind:'thing', "
            "status:'live'})",
            params={"id": oid, "name": name})


def _wire_impl(sdk, subject_name, content):
    """EventRecorded + IMPL edge for ``subject_name`` (the #152 test shape)."""
    proj = sdk._get_proj()
    eid = f"ev-{content}"
    proj.apply({"type": "EventRecorded", "id": eid, "eventId": eid,
                "eventKind": "analysis", "subject": subject_name})
    point = sdk.create_point("observation", content)
    proj.g.query(
        "MATCH (e:Event {eventId:$eid}), (p:Point {id:$pid}) "
        "CREATE (e)-[:IMPL]->(p)",
        params={"eid": eid, "pid": point["id"]})


# ── the resolver ──────────────────────────────────────────────────────────


class TestResolveEntityId:
    def test_single_live_name_resolves_to_its_id(self, sdk_factory):
        sdk = sdk_factory()
        subj = sdk.create_subject("solo", "analyst")
        g = sdk._get_proj().g
        assert resolve_entity_id(g, "Subject", "solo") == subj["id"]
        assert resolve_entity_id(g, "Subject", subj["id"]) == subj["id"]

    def test_two_live_same_name_refuses_and_records(self, sdk_factory, caplog):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "dup", ["sub-a", "sub-b"])
        g = sdk._get_proj().g
        with caplog.at_level("WARNING"), pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(g, "Subject", "dup")
        assert set(ei.value.candidate_ids) == {"sub-a", "sub-b"}
        assert ei.value.label == "Subject" and ei.value.name == "dup"
        assert any("non-folded entry" in r.message for r in caplog.records)

    def test_case_insensitive_name_resolution(self, sdk_factory):
        """Opt-in: the case-insensitive arm is OFF by default and ON only where
        asked (`provenance`'s historical contract)."""
        sdk = sdk_factory()
        subj = sdk.create_subject("El Dato Team", "team")
        g = sdk._get_proj().g
        assert resolve_entity_id(g, "Subject", "el dato team") is None
        assert resolve_entity_id(
            g, "Subject", "el dato team",
            case_insensitive_name=True) == subj["id"]

    def test_name_resolving_to_a_doubly_claimed_id_refuses(self, sdk_factory):
        """A name resolves to id X, but two nodes claim X — returning X would
        hand every downstream `id = $sid` leg a coordinate that FOLDS both."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'X', name:'n1', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'X', name:'n2', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(proj.g, "Subject", "n1")

    def test_addressed_name_resolving_to_a_doubly_claimed_id_refuses(
            self, sdk_factory):
        """The duplicate-id claimant count must run even when `addressing` is
        set — the endpoint-ident dedup COLLAPSES same-ident claimants, so
        `addressing` must not bypass it (two Subjects claiming X)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'X', name:'n1', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'X', name:'n2', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(proj.g, "Subject", "n1",
                              addressing=ADDRESSING_SOURCE)
        assert [e.shape for e in non_folded_entries()] == ["duplicate-id"]

    def test_document_target_same_id_sources_refuse(self, sdk_factory):
        """Two document Sources claiming one id are invisible to an `Object`
        claimant count — the document resolver must count over
        `_DOCUMENT_PREDICATE` (its real candidate space)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (n:Source {id:'X', url:'https://a/doc', "
                     "documentKind:'report', status:'live'})")
        proj.g.query("CREATE (n:Source {id:'X', url:'https://b/doc', "
                     "documentKind:'report', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(proj.g, "https://a/doc")

    def test_id_less_and_id_carrying_live_holders_refuse(self, sdk_factory):
        """A holder with no `id` is still a live holder (finding: filtering the
        None out silently picked the id-carrying one)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {name:'mix', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'sub-with-id', name:'mix', "
                     "status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(proj.g, "Subject", "mix")
        assert "<id-less>" in ei.value.candidate_ids
        assert "sub-with-id" in ei.value.candidate_ids

    def test_single_id_less_holder_refuses_loudly(self, sdk_factory, caplog):
        """Unambiguous but unaddressable: never return a vacuous value that
        looks like "no data"."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {name:'only-idless', status:'live'})")
        with caplog.at_level("WARNING"), pytest.raises(
                UnaddressableEntityName):
            resolve_entity_id(proj.g, "Subject", "only-idless")
        assert any("non-folded entry" in r.message for r in caplog.records)
        assert [e.shape for e in non_folded_entries()] == [
            "unaddressable-name"]

    def test_exact_id_wins_over_a_same_string_name(self, sdk_factory):
        # Subject A id='alice'; Subject B name='alice' — the #152 precedence.
        sdk = sdk_factory()
        _create_object_rows(sdk, "unused", [])
        sdk._create_entity("Subject", "alice",
                           {"name": "alice-work", "subjectKind": "analyst",
                            "status": "live"}, "SubjectAdded")
        sdk._create_entity("Subject", "bob",
                           {"name": "alice", "subjectKind": "reviewer",
                            "status": "live"}, "SubjectAdded")
        assert resolve_entity_id(sdk._get_proj().g, "Subject", "alice") == "alice"

    def test_terminal_holder_does_not_resolve_a_name(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'ghost', "
                     "status:'superseded'})")
        assert resolve_entity_id(proj.g, "Subject", "ghost") is None
        # D2 single_terminal_holder_reference: refused AND recorded, so a
        # terminal-only name is distinguishable from an absent one.
        assert [(e.shape, list(e.candidate_ids))
                for e in non_folded_entries()] == [
            ("no-live-holder", ["sub-dead"])]

    def test_one_live_one_terminal_holder_resolves_to_live(self, sdk_factory):
        """D2 `one_live_one_terminal_holder`: the terminal holder does NOT
        make the name ambiguous — it resolves to the live one, and nothing is
        refused."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-live', name:'mixed', "
                     "status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'mixed', "
                     "status:'superseded'})")
        assert resolve_entity_id(proj.g, "Subject", "mixed") == "sub-live"
        assert non_folded_entries() == ()

    def test_id_of_a_terminal_holder_still_resolves(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'ghost', "
                     "status:'superseded'})")
        assert resolve_entity_id(proj.g, "Subject", "sub-dead") == "sub-dead"
        # An exact-id hit is unambiguous: nothing is refused or recorded.
        assert non_folded_entries() == ()

    def test_unknown_name_and_unknown_id_are_none(self, sdk_factory):
        g = sdk_factory()._get_proj().g
        assert resolve_entity_id(g, "Subject", "nobody") is None
        assert resolve_entity_id(g, "Subject", None) is None
        # A genuinely ABSENT name is not a refusal — no entry recorded.
        assert non_folded_entries() == ()

    def test_non_identity_label_is_refused(self, sdk_factory):
        g = sdk_factory()._get_proj().g
        with pytest.raises(RuntimeError):
            resolve_entity_id(g, "Point", "p1")

    def test_document_target_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "artifact-dup", ["obj-a", "obj-b"])
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(sdk._get_proj().g, "artifact-dup")

    def test_document_target_url_only_source_refuses(self, sdk_factory):
        """A document Source with a url but NO id is unaddressable — the write
        path cannot wire it (create_edge's target set is id|eventId), so
        returning the url would file an approval with a silently missing edge."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (n:Source {url:'https://x/doc', "
                     "documentKind:'report', status:'live'})")
        with pytest.raises(UnaddressableEntityName):
            resolve_document_target_id(proj.g, "https://x/doc")

    def test_document_target_terminal_only_name_refused_and_recorded(
            self, sdk_factory):
        """Same D2 refusal on the document name arm: a terminal-only document
        name resolves to no id AND records a non-folded entry."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-dead', name:'dead-doc', "
                 "status:'archived'})")
        assert resolve_document_target_id(g, "dead-doc") is None
        assert [e.shape for e in non_folded_entries()] == ["no-live-holder"]

    def test_document_target_absent_name_records_nothing(self, sdk_factory):
        """A genuinely ABSENT document name is not a refusal — no entry."""
        g = sdk_factory()._get_proj().g
        assert resolve_document_target_id(g, "no-such-doc") is None
        assert non_folded_entries() == ()

    def test_document_target_one_live_one_terminal_resolves_to_live(
            self, sdk_factory):
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-live', name:'mixed-doc', "
                 "status:'live'})")
        g.query("CREATE (o:Object {id:'obj-dead', name:'mixed-doc', "
                 "status:'archived'})")
        assert resolve_document_target_id(g, "mixed-doc") == "obj-live"
        assert non_folded_entries() == ()

    def test_document_target_doubly_claimed_id_refuses_via_name_arm(
            self, sdk_factory):
        """The name arm must verify the id it resolves is uniquely claimed in
        the DOCUMENT space — two nodes claiming id X would be folded by the
        caller's id-keyed `create_edge`."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for _ in range(2):
            proj.g.query(
                "CREATE (o:Object {id:'X', name:'doc', "
                "objectKind:'thing', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(proj.g, "doc")

    def test_document_target_doubly_claimed_id_refuses_via_target_space(
            self, sdk_factory):
        """`create_edge`'s TARGET OR-set is id|eventId, deduped by the node's own
        identity. An Event keyed ``eventId='X'`` but carrying a DIFFERENT ``id``
        resolves as a SECOND target alongside an Object ``id='X'`` — the
        resolved artifact id would be addressed by two endpoints."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (o:Object {id:'X', name:'doc', "
                     "objectKind:'thing', status:'live'})")
        proj.g.query("CREATE (e:Event {id:'Y', eventId:'X', "
                     "eventKind:'analysis', eventStatus:'completed'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(proj.g, "doc")

    def test_provenance_resolves_id_typed_authored_by(self, sdk_factory):
        """`file_human_approval` now writes the resolved Subject ID into
        `authoredBy`; `provenance` must still find the author."""
        sdk = sdk_factory()
        subj = sdk.create_subject("pi-agent", "agent")
        p = sdk.create_point("statement", "A claim", authoredBy=subj["id"])
        out = sdk.provenance(p["id"])
        assert out["subject"] is not None
        assert out["subject"]["name"] == "pi-agent"

    def test_provenance_keeps_case_insensitive_name_match(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("El Dato Team", "team")
        p = sdk.create_point("statement", "B claim",
                             authoredBy="el dato team")
        out = sdk.provenance(p["id"])
        assert out["subject"] is not None
        assert out["subject"]["name"] == "El Dato Team"

    def test_provenance_two_live_same_name_refuses(self, sdk_factory):
        """Acceptance (b) for this site: two live same-name Subjects do NOT
        fold — `provenance` refuses instead of picking one authoredBy carrier."""
        sdk = sdk_factory()
        _create_subject_rows(sdk, "dup", ["sub-a", "sub-b"])
        p = sdk.create_point("statement", "C claim", authoredBy="DUP")
        with pytest.raises(AmbiguousEntityName):
            sdk.provenance(p["id"])

    def test_provenance_terminal_author_refused_and_recorded(self, sdk_factory):
        """D2: a terminal-only author never resolves to a terminal id. The
        historical read returns no subject, but the refusal is RECORDED — so
        "the author is terminal" is not silently indistinguishable from
        "no author" (#3633 §B.1 refuse + record)."""
        sdk = sdk_factory()
        sdk._get_proj().g.query(
            "CREATE (s:Subject {id:'sub-dead', name:'ghost-author', "
            "status:'retracted'})")
        p = sdk.create_point("statement", "D claim",
                             authoredBy="ghost-author")
        out = sdk.provenance(p["id"])
        assert out["subject"] is None
        assert [e.shape for e in non_folded_entries()] == ["no-live-holder"]


# ── compute_reputation ────────────────────────────────────────────────────


class TestComputeReputation:
    def test_single_live_name_resolves(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("frank", "analyst")
        _wire_impl(sdk, "frank", "frank's claim")
        rep = sdk.compute_reputation("frank")
        assert rep["total_events"] == 1
        assert rep["outcomes"][0]["content"] == "frank's claim"

    def test_two_live_same_name_do_not_fold(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "dup", ["sub-a", "sub-b"])
        with pytest.raises(AmbiguousEntityName):
            sdk.compute_reputation("dup")


# ── get_owned_entities / get_org_structure ────────────────────────────────


class TestGovernanceReads:
    def test_owned_entities_single_live_name_resolves(self, sdk_factory):
        sdk = sdk_factory()
        owner = sdk.create_subject("owner-x", "organization")
        point = sdk.create_point("observation", "an owned thing")
        sdk._get_proj().create_owned_by(point["id"], owner["id"])
        owned = sdk.get_owned_entities("owner-x")
        assert any(e.get("id") == point["id"] for e in owned)

    def test_owned_entities_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "owner-dup", ["sub-a", "sub-b"])
        with pytest.raises(AmbiguousEntityName):
            sdk.get_owned_entities("owner-dup")


# ── file_human_approval ───────────────────────────────────────────────────


class TestFileHumanApproval:
    def _claim(self, sdk):
        return sdk.create_point("statement", "a live claim", status="live")

    def test_single_live_name_resolves(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("approver-x", "analyst")
        obj = sdk.create_object("artifact-x", "thing")
        result = sdk.file_human_approval(
            approver_id="approver-x", artifact_id="artifact-x",
            point_ids=[self._claim(sdk)["id"]])
        assert result["event_id"]
        # the resolved ID is what got wired, never the name
        r = sdk._get_proj().g.query(
            "MATCH (s:Subject {name:'approver-x'})-[:performs]->(e:Event) "
            "RETURN count(e)").result_set
        assert r[0][0] == 1
        # the RESOLVED id is what the `uses` edge targets — pre-fix the NAME
        # was passed to create_edge, whose target OR-set is id|eventId only, so
        # the edge was silently not wired (the defect this path fixes).
        uses = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e})-[:uses]->(o:Object {name:'artifact-x'}) "
            "RETURN o.id", params={"e": result["event_id"]}).result_set
        assert uses and uses[0][0] == obj["id"]

    def test_single_live_document_id_resolves(self, sdk_factory):
        # a document Source is keyed by url (id == url); its display value is
        # `title`, so an exact id is the only address.
        sdk = sdk_factory()
        sdk.create_subject("approver-d", "analyst")
        doc = sdk.create_document("artifact-doc", "artifact")
        result = sdk.file_human_approval(
            approver_id="approver-d", artifact_id=doc["id"],
            point_ids=[self._claim(sdk)["id"]])
        assert result["event_id"]
        # the document Source (id == url) is the `uses` target by its id
        uses = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$e})-[:uses]->(n) RETURN n.id",
            params={"e": result["event_id"]}).result_set
        assert uses and uses[0][0] == doc["id"]

    def test_approver_id_also_a_source_url_refuses(self, sdk_factory):
        """`create_edge`'s SOURCE OR-set includes `Source.url`, so an approver
        id that is also a Source's url would MERGE a SECOND `performs` edge —
        the resolver must refuse over the source space, not just `n.id`."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'alice', name:'Alice', "
                     "subjectKind:'person', status:'live'})")
        proj.g.query("CREATE (n:Source {id:'src-1', url:'alice', "
                     "status:'live'})")
        obj = sdk.create_object("artifact-w", "thing")
        with pytest.raises(AmbiguousEntityName):
            sdk.file_human_approval(
                approver_id="alice", artifact_id=obj["id"],
                point_ids=[self._claim(sdk)["id"]])
        assert [e.shape for e in non_folded_entries()] == ["endpoint-collision"]

    def test_approver_id_doubly_claimed_refuses(self, sdk_factory):
        """The addressed approver name path must still count same-label
        claimants: two Subjects claiming the id would fold the performs edge."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'X', name:'n1', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'X', name:'n2', status:'live'})")
        obj = sdk.create_object("artifact-q", "thing")
        with pytest.raises(AmbiguousEntityName):
            sdk.file_human_approval(
                approver_id="n1", artifact_id=obj["id"],
                point_ids=[self._claim(sdk)["id"]])

    def test_two_live_same_name_approver_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "approver-dup", ["sub-a", "sub-b"])
        doc = sdk.create_document("artifact-y", "artifact")
        with pytest.raises(AmbiguousEntityName):
            sdk.file_human_approval(
                approver_id="approver-dup", artifact_id=doc["id"],
                point_ids=[self._claim(sdk)["id"]])

    def test_two_live_same_name_artifact_refuses(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("approver-z", "analyst")
        _create_object_rows(sdk, "artifact-dup", ["obj-a", "obj-b"])
        with pytest.raises(AmbiguousEntityName):
            sdk.file_human_approval(
                approver_id="approver-z", artifact_id="artifact-dup",
                point_ids=[self._claim(sdk)["id"]])


# ── onboarding seed ───────────────────────────────────────────────────────


class _Rows:
    def __init__(self, rows):
        self.result_set = rows


class _FakeSeedHandle:
    """Duck-typed seed handle: ``name`` -> the rows the graph would return.

    #12120: the seed asks the FOLDED UNION (``name`` · ``canonical_name`` · the
    folded spelling) through ``entity_identity._name_key_predicate``, so the
    spelling arrives as ``n0``/``n1``… beside any ``name`` param. The fake
    answers whichever spelling it actually holds rows for, and ``[]`` otherwise.

    ⛔ It also ASSERTS the query is the union read (#12047 round-4 finding 4).
    The earlier version of this docstring claimed that "``[]`` otherwise" proved
    the probe had not fallen back to a raw read — it did not: the fake answered
    ``merged["name"]`` too, so a hand-rolled ``MATCH (s:Subject {name: $name})``
    was answered identically and every pin below passed under that revert
    (measured). The assertion is the guard the comment claimed to be.
    """

    def __init__(self, by_name):
        self._by_name = by_name

    def query(self, cypher, **params):
        # A raw keyed read must fail LOUDLY here: the union's first arm is always
        # `$n0`, so its absence means this probe is not the writer's key space.
        assert "$n0" in cypher, f"not the folded union read: {cypher}"
        merged = dict(params.get("params") or {})
        merged.update({k: v for k, v in params.items() if k != "params"})
        candidates = [merged[f"n{i}"] for i in range(len(merged))
                      if f"n{i}" in merged]
        for cand in candidates:
            if cand in self._by_name:
                return _Rows([[dict(p)] for p in self._by_name[cand]])
        return _Rows([])


class TestFindSubjectByName:
    def test_single_live_match_resolves(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme", "status": "live"}]})
        assert find_subject_by_name(handle, "Acme")["id"] == "sub-1"

    def test_two_live_same_name_refuses(self, caplog):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme"},
                      {"id": "sub-2", "name": "Acme"}]})
        with caplog.at_level("WARNING"), pytest.raises(AmbiguousEntityName):
            find_subject_by_name(handle, "Acme")
        # the refusal also records the non-folded entry (the module contract)
        assert any("non-folded entry" in r.message for r in caplog.records)

    def test_terminal_holder_is_counted_not_skipped(self, sdk_factory):
        """A retracted anchor must NOT read as free: the seed's writer MERGEs
        on `{name}`, so filtering terminal holders out of the read would let a
        DIFFERENT org adopt (resurrect + re-key) the retracted node."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                "subjectKind:'organization', status:'retracted'})")
        props = find_subject_by_name(g, "Acme")
        assert props["id"] == "sub-dead"

    def test_terminal_and_live_holder_refuse(self, sdk_factory):
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                "subjectKind:'organization', status:'retracted'})")
        g.query("CREATE (s:Subject {id:'sub-live', name:'Acme', "
                "subjectKind:'organization', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            find_subject_by_name(g, "Acme")

    def test_id_less_holder_stays_visible_in_the_evidence(self, caplog):
        """A bare `if p.get('id')` filter would drop the id-less carrier and
        make a 2-carrier refusal read as a 1-carrier one."""
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme"},
                      {"name": "Acme"}]})
        with caplog.at_level("WARNING"), pytest.raises(
                AmbiguousEntityName) as exc:
            find_subject_by_name(handle, "Acme")
        assert "<id-less>" in exc.value.candidate_ids

    def test_seed_maps_ambiguity_to_subject_collision(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme", "org_id": "t1"},
                      {"id": "sub-2", "name": "Acme", "org_id": "t1"}]})
        with pytest.raises(SubjectCollision):
            seed_onboarding_anchors(handle, org_name="Acme", org_id="t1",
                                    include_person=False)


# ── the list unions: commit_ops + assembly ────────────────────────────────


class TestCommitOpsSupersessionProbe:
    def test_two_live_same_name_ref_never_folds_either_carrier(self, sdk_factory):
        """The per-ref probe in `apply_supersessions` never unions a same-name
        pair (it already had the >1-never-guess discipline pre-fix; this pins
        it against the split id/name arms)."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        proj.g.query("CREATE (o:Object {id:'obj-succ', name:'succ', "
                     "objectKind:'thing', status:'live'})")
        warns: list[str] = []
        applied = apply_supersessions(
            proj, sdk,
            [{"superseded": "dup", "supersedes_by": "succ"}],
            session_id="s3633", warn=warns.append)
        assert applied == 0, warns
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN ['obj-a', 'obj-b'] "
            "RETURN o.id, o.status, o.supersededBy").result_set
        assert len(rows) == 2
        assert all(r[1] == "live" and r[2] is None for r in rows)
        # the FOLD's refusal is a structured record, not merely a warning
        assert [e.shape for e in non_folded_entries()] == ["ambiguous-name"]

    def test_fold_order_prepass_never_guesses_an_ambiguous_ref(
            self, sdk_factory):
        """Drive `_supersession_fold_order` itself with >=2 entity records (the
        `len(entity) < 2` early-return means the single-record tests above never
        execute its split id/name probes).

        FALSIFIABLE: records are S(idx 0) then R(idx 1). R's successor name is
        'dupname', which TWO id-carrying Objects hold. If the pre-pass wrongly
        picked one carrier, it would create an R->S edge and hoist R ahead of S
        (order [1, 0]); correct never-guess leaves payload order [0, 1].
        """
        from tortoise.commit_ops import _supersession_fold_order

        sdk = sdk_factory()
        proj = sdk._get_proj()
        for oid in ("b1", "b2"):
            proj.g.query(
                "CREATE (o:Object {id:$id, name:'dupname', "
                "objectKind:'thing', status:'live'})", params={"id": oid})
        proj.g.query("CREATE (o:Object {id:'r', name:'r', "
                     "objectKind:'thing', status:'live'})")
        records = [
            {"superseded": "dupname", "supersedes_by": "x"},   # S (idx 0)
            {"superseded": "r", "supersedes_by": "dupname"},    # R (idx 1)
        ]
        assert _supersession_fold_order(proj, records) == [0, 1]

    def test_fold_order_prepass_dedupes_id_and_name_match(
            self, sdk_factory):
        """A node whose id EQUALS its name is ONE carrier, not two: if the id
        and name arms were not deduped by node identity, `by_id[ref]` would hold
        two rows and the ref would be skipped (no target -> no edge -> order
        [0, 1]). The dedup resolves it, so the edge exists -> order [1, 0]."""
        from tortoise.commit_ops import _supersession_fold_order

        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (o:Object {id:'selfsame', name:'selfsame', "
                     "objectKind:'thing', status:'live'})")
        proj.g.query("CREATE (o:Object {id:'r', name:'r', "
                     "objectKind:'thing', status:'live'})")
        records = [
            {"superseded": "selfsame", "supersedes_by": "x"},     # S (idx 0)
            {"superseded": "r", "supersedes_by": "selfsame"},     # R (idx 1)
        ]
        assert _supersession_fold_order(proj, records) == [1, 0]


class TestThe12120NameKeySpaceHelper:
    """`name_key_space_same_entity` — the ONE clause every node-returning reader
    composes, so its contract is pinned here rather than only through its callers.

    #12120: the helper is `_name_key_predicate`'s node-returning twin (the readers
    that must attach to EVERY carrier, or reach an id-less stub, cannot use the
    id-resolving `resolve_entity_id_same_entity`). Its two obligations: the
    non-blank clause is the WRITER's union (both name props, `aliases`, and both
    the raw and the folded spelling), and a blank name is a ZERO-ROW answer the
    caller honours — never a props-only clause that would match a `''` name.
    """

    def test_a_blank_name_is_a_zero_row_answer(self):
        from tortoise.entity_identity import name_key_space_same_entity

        for blank in ("", "   "):
            assert name_key_space_same_entity("o", blank) == ("", {}), repr(blank)

    def test_the_clause_is_the_writers_union_over_both_spellings(self):
        from tortoise.entity_identity import name_key_space_same_entity

        clause, params = name_key_space_same_entity("o", "The Widgets")
        assert clause.startswith("(") and clause.endswith(")"), clause
        assert "o.name" in clause and "o.canonical_name" in clause, clause
        assert "IN o.aliases" in clause, clause
        # Both spellings travel as params: the raw one AS WRITTEN (so an exact
        # carrier is still matched) and the folded one (so the writer's key is).
        assert "The Widgets" in params.values(), params
        # `normalize_entity_name` folds case and drops a leading article; the
        # plural is NOT stemmed, and that is the writer's key too.
        assert "widgets" in params.values(), params

    def test_a_case_variant_and_an_alias_carrier_are_both_matched(self, sdk_factory):
        from tortoise.entity_identity import name_key_space_same_entity

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            g.query(
                "CREATE (a:Object {id:'space-a', name:'Widget', "
                "objectKind:'thing', status:'live', canonical_name:'widget'}) "
                "CREATE (b:Object {id:'space-b', name:'Gadget', "
                "objectKind:'thing', status:'live', canonical_name:'gadget', "
                "aliases:['widget']})")
            clause, params = name_key_space_same_entity("o", "the WIDGET")
            ids = {r[0] for r in g.query(
                f"MATCH (o:Object) WHERE {clause} RETURN o.id",
                params=params).result_set}
            assert ids == {"space-a", "space-b"}, ids
        finally:
            sdk.close()


class TestNonFoldedRecord:
    """The refusal must be a STRUCTURED, assertable record — not merely a log
    line (decision record R8: "a refused fold is an assertion failure, not a log
    line"). The durable per-projection journal is Slice 0's; until it lands this
    process-level entry is the record."""

    def test_ambiguous_name_records_a_structured_entry(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "dup", ["sub-a", "sub-b"])
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(sdk._get_proj().g, "Subject", "dup")
        entries = non_folded_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry.label == "Subject"
        assert entry.name == "dup"
        assert entry.candidate_ids == ("sub-a", "sub-b")
        assert entry.shape == "ambiguous-name"

    def test_batch_refusal_records_without_raising(self, sdk_factory):
        """`assembly.exact_objects` must keep running (drop the ref) but still
        RECORD the refusal — a log-only signal leaves the run green."""
        sdk = sdk_factory()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        assert docker_resolver_port(sdk).exact_objects(["dup"]) == []
        entries = non_folded_entries()
        assert [e.shape for e in entries] == ["ambiguous-name"]
        assert entries[0].label == "Object" and entries[0].name == "dup"

    def test_duplicate_id_records_a_duplicate_id_shape(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'X', name:'n1', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'X', name:'n2', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(proj.g, "Subject", "n1")
        assert [e.shape for e in non_folded_entries()] == ["duplicate-id"]


class TestAssemblyExactObjects:
    def test_id_arm_and_name_arm_resolve_apart(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "nx", ["obj-x"])
        _create_object_rows(sdk, "same", ["obj-y"])
        got = docker_resolver_port(sdk).exact_objects(["obj-x", "same"])
        assert {r["id"] for r in got} == {"obj-x", "obj-y"}

    def test_two_live_same_name_ref_is_refused(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        assert docker_resolver_port(sdk).exact_objects(["dup"]) == []

    def test_duplicate_id_carriers_refuse_not_collapse(self, sdk_factory):
        """Two nodes claiming one id is corruption — pre-fix the id dict
        silently kept the last, so the ref resolved; now it is refused."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for _ in range(2):
            proj.g.query(
                "CREATE (o:Object {id:'same-id', name:'n', "
                "objectKind:'thing', status:'live'})")
        assert docker_resolver_port(sdk).exact_objects(["same-id"]) == []
        assert [e.shape for e in non_folded_entries()] == ["duplicate-id"]


# ── source-level sweep ────────────────────────────────────────────────────
#
# The issue's blind spot is the OPERATOR form (`.name = $x` / `.name IN $x`)
# and the f-string label: neither appears in a `grep "OR .*\.name = \$"` sweep,
# and the `get_reputation` fallback had no `OR` at all. This sweep covers the
# operator form, the `{name:$x}` keyed form, and the f-string label form.
#
# `_OPERATOR` is deliberately widened past a bare `= $`: it also matches a
# wrapped comparison (`toLower(s.name) = toLower($n)`) and the `CONTAINS` /
# `STARTS WITH` / `ENDS WITH` substring forms, because the narrow pattern MISSED
# the `provenance` read (a review finding) — the defect class is "a name is
# compared", not "a name is compared to a bare `$`".
#
# Every surviving hit is enumerated with the disposition that owns it. The
# allowlist is LIVE: a site that disappears fails the test (stale entry), and a
# site that appears fails it (undispositioned coordinate).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORTOISE_DIR = os.path.join(REPO_ROOT, "tortoise")

# `_OPERATOR` matches a name comparison in EITHER operand order (`o.name = $x`
# and `$x = o.name`) and over bracket / backtick property syntax, with the `$`
# bound to a small window so a `.name` projection and an unrelated `$param`
# later in the same statement do not fabricate a hit. The narrow original
# (`\.name\s*=\s*\$`) MISSED the wrapped `toLower(s.name) = toLower($n)`
# `provenance` read (a review finding): the defect class is "a name is
# compared", not "a name is compared to a bare `$`".
_OPERATOR = re.compile(
    r"(?:[A-Za-z_]\w*\.name\b|\w+\['name'\]|\w+\[\"name\"\]|\w+`name`)"
    r"\s*\)?\s*(?:=|IN|CONTAINS|STARTS WITH|ENDS WITH)\s*[^\n]{0,40}\$"
    r"|\$\w+\s*\)?\s*(?:=|IN|CONTAINS|STARTS WITH|ENDS WITH)\s*[^\n]{0,40}"
    r"(?:[A-Za-z_]\w*\.name\b|\w+\['name'\]|\w+\[\"name\"\]|\w+`name`)")
_KEYED_READ = re.compile(r":(?:Object|Subject)\s*\{\{?\s*name\s*:")
_FSTRING_LABEL = re.compile(
    r"\{[A-Za-z_][\w.\[\]'\"()]*\}\s*\{\{?\s*name\s*[:}]")

# Each entry is ``normalized STATEMENT -> occurrence COUNT``. The count is
# load-bearing: two sites carrying byte-identical query text (e.g.
# `projection/entities.py`'s two `...+ live,` statements) would otherwise
# collapse into one entry, and a stale entry for a deleted duplicate would look
# live.
#
# Snippets are WHOLE LOGICAL STATEMENTS (see `_scan`), not physical lines: a
# wrapped Cypher comparison (``"... o.name = "`` / ``"$value ..."``) is one
# statement and must be dispositioned as one.
#
# #5509 CENSUS AND CLASSIFICATION — every surviving name-keyed site, and WHY it
# keeps the name. "Name-keyed" is not by itself a defect: a name is the right
# coordinate for a search, for "does any entity hold this name", and for the
# write that MINTS the identity. It is the wrong coordinate exactly when the
# site must answer *which ONE entity* — that is what the resolver owns.
#
#   A. ROUTED / DELETED by #5509 (gone from the lists below): `belief_timeline`
#      (was a name union over both carriers), `subject_binding._resolve_target`
#      (resolved a TERMINAL holder, D2), `resolve_structural_target`'s replay
#      targets (was `LIMIT 1`, an arbitrary carrier), `_create_entity`'s and
#      `mining`'s canonical-id re-fetch (was `result_set[0][0]`).
#   B. CANDIDATE ENUMERATION that already refuses (commit_ops' successor/ref
#      probes, `assembly.exact_objects`' name arm, `onboarding.seed.
#      find_subject_by_name`): they must see EVERY carrier to answer "is this
#      name ambiguous?", then refuse (`>1 -> never-guess` + a non-folded entry).
#      Routing them through the resolver would turn "skip this one record,
#      recorded" into "raise", a semantics change the issues defer.
#   C. EXISTENCE / PRESENCE probe (sdk.ingest's `existed` dedup counter,
#      `assembly._probe_visible_successors`): the question is "does any live
#      entity hold this name", never "which one". No identity is selected.
#   D. SEARCH / DISPLAY (sdk's `e.name CONTAINS $q` query clause,
#      `session_link`'s exactly-one suffix probe): the name IS the input.
#   E. WRITE-PATH identity MINTING, migration-gated (projection/entities.py's
#      `MERGE (n:{label} {name:$name})` family + `_persist_extra_props` +
#      lifecycle folds; projection/edges.py's legacy `_create_about_edges` /
#      `_try_about_edge` anchors). The producer sends a NAME and the fold keys on
#      it; two carriers are attached to (over-attribution, no identity pick)
#      rather than one being guessed. Converting these to id keys needs the
#      name->id migration (plan S1/S3) — NOT this sweep's fix, and not
#      something a read route can fake.
#      ⛔ TWO members LEFT this class in #12120 without becoming a read route:
#      `hosted_api`'s session-capture anchor and `sdk`'s `about_entities` loop
#      now take their SPELLING space from the writer's folded union
#      (`name_key_space_same_entity`) while keeping the attach-to-every-carrier
#      shape this note protects (so the "would refuse where capture writes"
#      objection above never applies). They are still name-keyed reads — they are
#      merely no longer keyed on a SINGLE raw name property — so their
#      `_ALLOWED_KEYED`/`_ALLOWED_OPERATOR` entries are DELETED with this change
#      (the deletion is the load-bearing part: the census then enforces the
#      writer's space at these sites), not widened.
_ALLOWED_OPERATOR = {
    'tortoise/assembly.py': {
        # B: route-then-refuse NAME arm (a candidate set, refusal per ref).
        'name_rows = proj.g.query( "MATCH (o:Object) " "WHERE o.name IN $names " f"{status_filter}" "RETURN o.id, o.name", params={"names": names}).result_set': 1,
        # C: presence probe — "ANY visible Object of that name" verifies a
        # successor annotation; it never selects one identity.
        'rows = proj.g.query( "MATCH (o:Object) WHERE o.name IN $names " "RETURN o.name AS nm, o.status", params={"names": sorted(names)}).result_set': 1,
        # #4061 R1 "DISTINGUISHING probe" — owned by S3 (identity/resolver).
        # This is the ONE read that MUST keep the name-or-id union: the
        # resolver cannot tell "no exact match" from "the exact match is
        # excluded", and only a name/id search over the EXCLUSION set
        # answers the latter. Routing it through the #3633 resolver would
        # delete the very distinction it exists to draw.
        'rows = proj.g.query( "MATCH (o:Object) " "WHERE (o.name IN $names OR o.id IN $names) " "AND o.status IN $statuses " "RETURN o.id, o.name", params={"names": names, "statuses": sorted( _RESOLVER_UNRESOLVABLE_OBJECT_STATUSES)}).result_set': 1,
    },
    'tortoise/commit_ops.py': {
        # B: candidate enumeration — every one of these probes returns ALL
        # carriers and the fold refuses on >1 (`never-guess` + a non-folded
        # entry). This is the shape that must keep the name key.
        # §B "Route" — successor-name candidate probe, S3/id-probe owned.
        'cand_rows = proj.g.query( "MATCH (o:Object) WHERE o.name IN $names RETURN o.name, o.id", params={"names": sb_names}).result_set': 1,
        '_name_rows = proj.g.query( "MATCH (o:Object) WHERE o.name IN $names RETURN ID(o), o.id, o.name", params={"names": refs_sorted}).result_set': 1,
        # #12120 review rounds 3 + 8: the family defer asks whether the SUBJECT
        # family holds the ref by its EXACT spelling (so a fold-only Subject does
        # not outvote a fold-only Object on a tie). An exactness TEST, not a
        # lookup that selects an identity: the fold itself still goes through the
        # writer's key space, and the row it returns is only used as a boolean.
        # Round 8 folded the two spellings into ONE read. ⛔ Round 29: the
        # collapse-report guard does NOT call this helper — it asks
        # `_subject_family_holds_ref`, deliberately, because an exactness test
        # cannot see a by-id or by-fold Subject holder — that helper's docstring
        # records WHY, and the guard's own note records the measurement. So this is ONE
        # caller, ONE read, ONE census entry — not "two questions that ask it".
        # Round 24 FLATTENS the read to ids (it returned the `[id]` ROWS its own
        # docstring, name and `-> list` annotation deny), so the census entry
        # follows the statement — the coordinate is unchanged, only the row
        # reshaping is: still ONE entry for this one read.
        'return [r[0] for r in proj.g.query( f"MATCH (s:Subject) WHERE {space} AND s.name = $ref RETURN s.id", params={**params, "ref": ref}).result_set if r]': 1,
        # ⛔ TWO ENTRIES REMOVED by #12120 supersession-probe site — the ref-side probes of the
        # supersession fold (`o.name = $ref` / the #1423 Subject twin
        # `s.name = $ref`). Both were candidate-ENUMERATION reads over a key space
        # NARROWER than the write they describe, which is the same defect class as
        # #12120 itself: a fold-only ref was skipped with the wrong reason
        # ("ref is not a Subject" / "not found") and its successor stayed live and
        # search-visible. They now take their arms from
        # `entity_identity.name_key_space_same_entity` — the ONE definition of who
        # holds a name — so there is no narrower coordinate left to excuse. The
        # #1423 disposition itself is UNCHANGED: the Subject twin is still reached
        # only when the id form matched nothing, it still returns every carrier,
        # and the caller still refuses outright on >1 (`len(by_name) > 1` -> warn +
        # `_skip`, "never-guess"), so it cannot fold two same-name Subjects into one.
        # (`cand_rows` above stays: the fold-ORDER pre-pass is a pure ordering
        # heuristic with no refusal semantics — it fails soft to payload order.)
    },
    'tortoise/entity_identity.py': {
        # THE sanctioned name->id reads (the resolver module). The predicate is
        # now BUILT (`_name_predicate`), so the raw comparison lives inside
        # that builder and this sweep sees only the builder's arms — which is
        # the point of routing: one copy, one place.
        # (#5509: the single-literal entry that used to live here is deleted,
        # not replaced — the statement is no longer a name comparison.)
    },
    'tortoise/sdk.py': {
        # D: SEARCH surface — the FTS/query clause. A name is the query.
        'clauses.append("(toLower(e.name) CONTAINS $q OR any(kw IN e.keywords WHERE toLower(kw) CONTAINS $q))")': 1,
        # C: `update_point`'s TAGGED landing witness (#7405), the existence
        # probe for the EDGE half of a write. `:Tag` identity IS its name —
        # the reconciler MERGEs `(t:Tag {name:$tag})` in the same routine
        # (`projection/edges.py::_sync_tags`), so a name here cannot name two
        # carriers the way an Object/Subject name can, and there is no other
        # coordinate to key on. It returns an aggregate COUNT, never a row, so
        # it can never select one identity; the caller compares the count to
        # the requested set and raises on a short landing. Name-keying is
        # deliberate and narrow: `Tag` is a value node, not an identity family.
        'rows = proj.g.query( "MATCH (p:Point {id:$pid})-[:TAGGED]->(t:Tag) " "WHERE t.name IN $names RETURN count(DISTINCT t.name)", params={"pid": id, "names": _sync_wanted}, ).result_set': 1,
        # #12120 review round 3: the capture twin of the narrowing below — see
        # the near_dup_merge/hosted_api entries. Exactness only; the lookup beside
        # it is the writer's folded union.
        '_space = f"({_space}) AND o.name = $exact"': 1,
    },
    # #12120 review round 3: ONE new operator-form read, in each of the three
    # `aboutObject` attachment sites — an exactness NARROWING, never an identity
    # pick. When an `about_entities`/union spelling only FOLD-equals a declared
    # subject-kind name, only a carrier whose own `name` IS that spelling may be
    # attributed: a fold-only carrier is the un-gated `#1370`/`#4934` edge this
    # skip exists to prevent, while F6's legitimately-cased Object keeps its edge
    # (`test_case_only_mismatch_still_gets_its_about_object_edge`). The lookup
    # beside it is still the writer's folded union, so the entry is the exactness
    # constraint and nothing wider.
    'tortoise/near_dup_merge.py': {
        '_space = f"({_space}) AND o.name = $exact"': 1,
    },
    'tortoise/hosted_api.py': {
        '_space = f"({_space}) AND o.name = $exact"': 1,
        # #12120 review round 6: the EVENT channel's twin of the same narrowing.
        # Its reference is resolved in §6a AFTER the writes land, so the
        # exactness constraint cannot ride on the probe's WHERE clause; the
        # deferred resolution asks for the exact spelling instead (a fold-only
        # carrier would be the un-gated `#4934` edge, while F6's exact carrier
        # keeps its edge). Same disposition as the line above — the fold is the
        # writer's, and this only refuses a fold-only carrier.
        # #12120 review (round 7): the arm also carries the resolver's LIVE
        # predicate (`live._terminal_excluded`, the Cypher deny-list behind the
        # Python mirror `resolve_fold_name` reads), because narrowing the spelling
        # must not silently widen the rest of the arm's contract: without it a
        # fold-only reference with ONE exact carrier that is already
        # superseded/archived/retracted would receive a NEW `aboutObject` edge no
        # recall surface shows. Still an exactness + liveness TEST, never an
        # identity pick — the caller attaches to EVERY surviving exact carrier.
        '_exact_rows = proj.g.query( "MATCH (o:Object) WHERE o.name = $n AND " f"{_terminal_excluded(\'o.status\')} RETURN o.id", params={"n": _name}).result_set': 1,
    },
    'tortoise/session_link.py': {
        # D: the suffix probe already enforces EXACTLY-ONE (`if len(rows) == 1`),
        # so zero/multiple matches is a no-op — it never picks a carrier.
        'rows = proj.g.query( "MATCH (o:Object) WHERE o.objectKind=\'pm:issue\' " "AND o.name ENDS WITH $suffix RETURN o.id", params={"suffix": suffix}, ).result_set': 1,
    },
}

_ALLOWED_KEYED = {
    # ⛔ NO ENTRY for `tortoise/commit_ops.py` (removed by the supersession-probe site).
    # Its successor and ref probes used to be raw `{name:$sb}` reads — a keyed
    # READ enumerating a NARROWER key space than the supersession fold resolves on
    # (`name` · `canonical_name` · `aliases`, raw and folded), so a fold-equivalent
    # successor was reported "dangling" while the superseded entity stayed live.
    # They take their arms from `entity_identity.name_key_space_same_entity` now,
    # so there is no narrower coordinate left to excuse; the never-guess/visible-
    # successor gates are unchanged.
    # ⛔ NO ENTRY for `tortoise/hosted_api.py` (removed by the about-anchor site). The
    # hosted `about_entities` anchor attached on the RAW name only, so a
    # fold-equivalent spelling matched nothing and the `aboutObject` edge was
    # silently dropped. It still attaches to EVERY carrier (no identity is picked,
    # so the recorded objection — a resolver call would refuse where capture
    # writes — does not apply); the spelling key space is the writer's.
    'tortoise/onboarding/state.py': {
        # ⛔ NO ENTRY NEEDED (removed by #12120 finding 3). This used to be a
        # raw `{name: $name}` probe — a keyed READ that enumerated a NARROWER key
        # space than the write it guards, so a fold-colliding anchor name was
        # classified "safe to create" and the write landed on the OTHER org's
        # node (rewriting `org_id`). It now takes its arms from
        # `entity_identity._name_key_predicate`, the one definition of the write's
        # key space, so there is no narrower coordinate left to excuse.
    },
    'tortoise/onboarding/seed.py': {
        # ⛔ NO ENTRY NEEDED (removed by #12120 finding 3). `find_subject_by_name`
        # used to be a raw `{name: $name}` enumeration — a keyed READ over a
        # narrower key space than `create_subject` merges on, which is exactly
        # the class this sweep exists to find. It now takes its arms from
        # `entity_identity._name_key_predicate` (the union: `name` ·
        # `canonical_name` · `aliases`, raw and folded), so there is no narrower
        # coordinate left to disposition. The refusal semantics are unchanged and
        # still pinned: single holder -> its props, 2+ -> AmbiguousEntityName,
        # NO status filter.
    },
    'tortoise/projection/edges.py': {
        # #5509 A: `resolve_structural_target`'s name-keyed targets read with
        # `LIMIT 2` and refuse on >1 (`_refuse_ambiguous_target`), so the
        # remaining read is the holder probe itself, not a silent pick.
        # #12120 finding 1: this is now the RESOLVE-ONLY arm and the refusal arm —
        # when `mint` is requested, `_mint_subject_stub` answers with the node it
        # ADDRESSED (over the folded union) and this read is not reached. It still
        # refuses a spelling that its own key holds twice.
        'rows = g.query( "MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 2", params={"name": key}).result_set': 1,
        # #12120 finding 1: the READ-BACK of the stub `_mint_subject_stub` has just
        # MERGE'd — it cannot name two carriers, because the mint is guarded by a
        # folded-union holder probe that returns BEFORE this write when any live
        # holder exists. It returns the identity of the node the call wrote (the
        # caller wires by that internal id), and a 2-row answer here is REFUSED
        # rather than picked. The minted stub is the last name-keyed WRITE in the
        # tree, migration-gated (owner: S3 identity/resolver).
        'rows = g.query( "MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 2", params={"name": name}).result_set': 1,
        # E: the cascade's Subject-stub FALLBACK anchor (#8398 P1-2). Reached
        # only when no label matched by name — including no `:Subject`, raw OR
        # folded (`_resolve_subject_fold_target` runs first) — so the mint above
        # it creates the node this read then addresses. A write key,
        # migration-gated (owner: S3 identity/resolver). Its predecessor, the
        # rel-ignoring `MERGE (n)-[:aboutSubject]->(s)` by name, is REMOVED with
        # `_try_about_edge`'s inline write, not left stale below.
        #
        # ⛔ The `LIMIT 1` read that used to follow the mint at BOTH call sites is
        # GONE (#12120 finding 1): `_mint_subject_stub` now returns the node it
        # addressed, so no caller re-derives the target from the MENTION'S spelling
        # (which a fold-only holder does not carry) and live no longer picks one of
        # N carriers.
    },
    'tortoise/projection/entities.py': {
        # §B "Route" — lifecycle folds / participants fallback / edge anchors;
        # S3 deletes the supersession name fallback. Counts pin the duplicated
        # statements so a half-removal cannot pass.
        # #4520: the `REMOVE s.embedding` / `REMOVE o.embedding` entries were
        # DELETED, not replaced — both Subject/Object sites now call
        # `tortoise/vector_write.py::clear_embedding`, whose MATCH pattern is
        # built at runtime and passed as an argument, so this #3633 sweep cannot
        # see it. The read is dispositioned in that module's seam.
        # #2729: the pack-declared fold dispatcher. `_fold_object_status`
        # MATCHes the Event's target by NAME (the prop the event carries) and
        # guards on the declaring kind, case-folded + form-widened
        # (`toLower(o.objectKind) IN $kinds` covers `venture:tranche` and bare
        # `tranche`). It is a lifecycle fold, not an identity resolve — it
        # writes `Object.status` for a kind whose lifecycle the pack declared,
        # and the terminal guard (below) is carried into the same predicate.
        # Owned by S3 with the connector folds it generalizes; the pre-#2729
        # literal built-in statements it replaced are REMOVED, not left stale.
        #
        # #2729 review P1: the kind comparison goes through `toStringOrNull`
        # (NOT `toLower` on the raw property) because a payload-supplied
        # non-string `objectKind` made `toLower` raise `Type mismatch` and
        # abort the whole event write — a failure mode the pre-#2729 built-in
        # fold never had (it never read `objectKind`). Same sites, same S3
        # ownership; only the coercion is new.
        # The companion MISS probe (distinguishes "the terminal guard refused"
        # from "no Object of the declaring kind exists", which is reported as
        # `status_fold_unapplied`). Same name-keyed MATCH, same disposition.
        # The built-in connector compatibility half — parametrized by #2729
        # (was the two literal `SET o.status='in_progress'`/`'completed'`
        # statements this PR replaced). Same name-keyed coordinate, same S3
        # ownership; the `$declared` guard is what makes a declared-lifecycle
        # kind own its state. The `IS NULL` disjuncts keep the pre-#2729
        # tolerance for an absent OR non-string `objectKind` (#2729 review P1).
        # Refreshed for #5593: `main` rewrote both lifecycle predicates to
        # read the shared OBJECT vocabulary (`NOT (o.status IN $excluded)` +
        # `_terminal_object_statuses()` -> `commit_ops.OBJECT_TERMINAL_STATUSES`)
        # instead of restating the literal `'superseded'`.
        # The entries are updated to the new source text, NOT dropped: the
        # sites are still name-keyed and still owned by S3.
        # NOTE: this is the OBJECT vocabulary; it is deliberately NOT the set
        # `live` uses for Points (`TERMINAL_EXCLUDED_STATUSES` also carries
        # `outdated`), so it does not make this predicate drift-free against
        # the resolver. Do not restate it as "the same constant".
        # ⛔ RETIRED (#3590 round-5 review F2): the `participatesIn` fallback no
        # longer MERGEs by name. `_event_plain_merge` now wires it on the SAME
        # resolved performer id as the `performs` arm, so there is no name-keyed
        # site left here to disposition — and leaving the entry would make the
        # sweep clear a pattern that no longer exists.
    },
    'tortoise/near_dup_merge.py': {
        # ⛔ NO ENTRY (removed by the merge-attachment site). The merge attachment was a raw
        # `{name:$n}` lookup — narrower than the write it mirrors, so a claim naming
        # a fold-equivalent spelling of a registered Object was reported
        # `unresolved` instead of linked. It takes its arms from
        # `entity_identity.name_key_space_same_entity` now. The `unresolved`
        # MEANING is preserved and restated in the function's docstring: no Object
        # holds that name in the key space the WRITE resolves on. The attachment
        # still lands on EVERY carrier (id-less stub included), which is why the
        # recorded objection to routing it through a resolver — a merge would
        # REFUSE where capture WRITES — does not apply.
    },
    'tortoise/sdk.py': {
        # ⛔ NO ENTRY for the `about_entities` claim loop (removed by #12120's
        # about-anchor site) — nor for its id-less fallback. Its local twin is
        # `hosted_api`'s anchor, converted with it: both attach to EVERY carrier
        # over the writer's folded spelling space, so the recorded
        # "migration-gated" objection (routing through a resolver would refuse
        # where capture writes) is preserved rather than overridden.
        # C: ingest's existence probe is NO LONGER here — it now asks the
        # dispositioned resolver (`entity_identity.all_name_holder_ids`) for the
        # label the WRITE lands on, via `sdk._existing_entity_ids` (#9964).
        # Probing the ITEM's label here measured a different node than the
        # write touched once the kind could re-label the write, so the entry
        # that used to record the two raw reads is gone with them.
    },
}

_ALLOWED_FSTRING = {
    'tortoise/projection/edges.py': {
        # #5509 A: the non-stubbable labels' (Object/Event/Point) replay target
        # — `LIMIT 2` + `_refuse_ambiguous_target` on >1.
        'q = (f"MATCH (x:{label} {{name:$key}}) " f"RETURN ID(x), x.id LIMIT 2")': 1,
        # E: the name cascade's PROBE (`_resolve_about_edge_target`) — live
        # auto-detect still asks "which label holds this name", one attempt per
        # label, and the answer is now JOURNALED as the pinned rel/label (#8398
        # P1-2) instead of being re-derived at replay. Write key,
        # migration-gated (owner: S3). Replaces the retired
        # `RETURN e.name LIMIT 1` probe + the two by-name MERGEs it fed: the
        # write is now by the RESOLVED internal id (`WHERE ID(t)=$tid`).
        # `LIMIT` dropped entirely since the second #8398 review (P2-3): the
        # probe must see EVERY same-name carrier — `LIMIT 1` pinned an arbitrary
        # backend row order into an append-only journal, and `LIMIT 2` would
        # still truncate at the third carrier. The coordinate text is updated,
        # NOT the disposition — the read is still name-keyed, still one probe per
        # cascade label, and still owned by S3.
        'rows = self.g.query( f"MATCH (e:{label} {{name:$name}}) " "RETURN ID(e), coalesce(e.id, e.eventId, e.name)", params={"name": target_name}, ).result_set': 1,
    },
    # `tortoise/sdk.py` no longer needs an entry here (#12047 review round 9):
    # `_entity_name_holders`' raw-name f-string probe is GONE rather than
    # excused — it now takes its holder set from the writer's own predicate
    # (`entity_identity._live_holders_same_entity`), so the guard and the write
    # cannot disagree about which entity a bundle-local ref will land on.
}


def _logical_units(path: str) -> list[str]:
    """Normalized LOGICAL STATEMENTS of a Python file (whitespace collapsed).

    A physical-line sweep is evadable: a Cypher string wrapped across lines
    (``"... o.name = "`` / ``"$value ..."``) puts the name and its ``$`` on
    different lines, and implicit string concatenation splits one query
    arbitrarily. ``tokenize`` marks logical line ends (``NEWLINE``, as opposed
    to ``NL`` inside brackets), so each statement is joined before matching.

    Docstring-only statements (a leading, possibly prefixed, triple-quoted
    literal) are dropped: prose that *quotes* a retired pattern is
    documentation, not a coordinate. A docstring that SHARES a logical line
    with real code is NOT dropped — its code sibling must still be scanned.
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    lines = src.splitlines()
    skip_types = {tokenize.NEWLINE, tokenize.NL, tokenize.COMMENT,
                  tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING,
                  tokenize.ENDMARKER}
    spans: list[tuple[int, int]] = []
    start = None
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.NEWLINE:
            if start is not None:
                spans.append((start, tok.end[0]))
                start = None
        elif tok.type not in skip_types and start is None:
            start = tok.start[0]
    if start is not None:
        spans.append((start, len(lines)))
    units = [" ".join(" ".join(lines[a - 1:b]).split()) for a, b in spans]
    return [u for u in units if not _is_docstring_only(u)]


def _is_docstring_only(unit: str) -> bool:
    """True when a logical statement is ONLY a triple-quoted string literal.

    AST-based, not a prefix regex: a prefixed docstring (``r`` + triple quotes)
    is recognized, and a docstring sharing a logical line with code is NOT
    dropped — the code sibling must still be scanned.
    """
    if '"""' not in unit and "'''" not in unit:
        return False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            tree = ast.parse(unit)
        except SyntaxError:
            return False
    return (len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str))


# A clause keyword terminates a write clause: `SET a=$x, b=$y` is a write, but
# `SET a=$x WHERE ...` means the `b=$y` was a read.
_CLAUSE = re.compile(
    r"\b(?:MATCH|WHERE|RETURN|WITH|MERGE|CREATE|SET|ON|UNWIND|DELETE|DETACH)\b")


def _in_set_clause(prefix: str) -> bool:
    idx = prefix.rfind("SET ")
    return idx >= 0 and not _CLAUSE.search(prefix[idx + 4:])


def _scan(pattern, *, skip_set=False, skip_merge=False, require=None):
    """{relpath: [normalized matching STATIC statements]} over ``tortoise/**.py``.

    Matches are tested against a whole logical statement (see
    ``_logical_units``), so a wrapped/composed query is dispositioned as one
    unit. ``skip_set`` / ``skip_merge`` drop WRITE occurrences only — the
    occurrence's own clause is inspected, so a statement that READS a name and
    then MERGEs/SETs something else is still reported (this sweep is about
    READS; the matching writes are S1's blast radius). ``require`` keeps a
    pattern to statements that carry a Cypher ``MATCH`` (case-insensitively —
    Cypher keywords are case-insensitive, so a lower-case ``match`` must not
    slip through).

    KNOWN LIMITS (a sweep is a net, not a proof): a keyed pattern whose label or
    key is a NON-constant expression (``"MATCH (o:" + lbl + " {name:$n})"``,
    ``f"MATCH (n:{lbl} {{{key}:$v}})"``) or a comparison split across two
    concatenated constants cannot be reconstructed without a dataflow pass, so
    those forms are NOT matched. They are covered by the allowlist discipline +
    review, not by this regex.
    """
    hits: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(TORTOISE_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, REPO_ROOT)
            for unit in _logical_units(path):
                if require and not re.search(rf"(?i)\b{re.escape(require)}\b",
                                            unit):
                    continue
                for m in pattern.finditer(unit):
                    prefix = unit[:m.start()]
                    if skip_set and _in_set_clause(prefix):
                        continue
                    if skip_merge and re.search(r"MERGE\s*\([^)]*$", prefix):
                        continue
                    hits.setdefault(rel, []).append(unit)
                    break
    return hits


def _assert_allowlisted(hits, allowed):
    def norm(s):
        return re.sub(r"\s+", " ", s).strip()


    for rel, lines in hits.items():
        assert rel in allowed, (
            f"#3633: a name-keyed Object/Subject identity read reappeared in "
            f"{rel}: {lines} — disposition it (route-then-refuse) or add it to "
            f"the allowlist with its owner")
        expected = {norm(s): n for s, n in allowed[rel].items()}
        counts = collections.Counter(norm(x) for x in lines)
        for line, got in counts.items():
            assert line in expected, (
                f"#3633: {rel} carries a name-keyed coordinate not in the "
                f"allowlist: {line!r} (allowed: {sorted(expected)})")
            assert got == expected[line], (
                f"#3633: {rel} has {got} occurrences of {line!r}, allowlist "
                f"expects {expected[line]} — a site was added or removed; "
                f"update the count instead of leaving it stale")
    # The allowlist may not silently outlive the sites it excuses — including
    # one of two byte-identical duplicates (hence the COUNT, not a set).
    for rel, snippets in allowed.items():
        counts = collections.Counter(norm(x) for x in hits.get(rel, []))
        for snippet, n in snippets.items():
            assert counts.get(norm(snippet), 0) == n, (
                f"#3633: allowlist entry {rel}: {snippet!r} expected {n} "
                f"occurrence(s), found {counts.get(norm(snippet), 0)} — the "
                f"site it excused is gone; remove the entry")


def test_no_undispositioned_operator_form_identity_read():
    _assert_allowlisted(_scan(_OPERATOR, skip_set=True), _ALLOWED_OPERATOR)


def test_no_undispositioned_keyed_identity_read():
    _assert_allowlisted(
        _scan(_KEYED_READ, skip_merge=True, require="MATCH"),
        _ALLOWED_KEYED)


def test_no_undispositioned_fstring_label_identity_read():
    _assert_allowlisted(
        _scan(_FSTRING_LABEL, require="MATCH"), _ALLOWED_FSTRING)


def test_sweep_catches_evasive_query_forms():
    """Self-test of the GUARD: a line-based sweep misses a reversed comparison,
    bracket property syntax, a string wrapped across lines, and a lower-case
    Cypher keyword. The logical-unit scan must catch all four — and must NOT
    fabricate a hit from a docstring that merely quotes a retired pattern."""
    import tempfile

    src = (
        'A = "MATCH (o:Object) WHERE $name = o.name RETURN o.id"\n'
        'B = "MATCH (o:Object) WHERE o[\'name\'] = $name RETURN o.id"\n'
        'C = ("MATCH (o:Object) WHERE o.name = "\n'
        '     "$value RETURN o.id")\n'
        'D = "match (o:Object {name:$n}) return o.id"\n'
        'r"""docs: the old probe was o.name = $n (retired)"""\n'
    )
    fd, path = tempfile.mkstemp(dir=TORTOISE_DIR, suffix=".py", text=True)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(src)
        rel = os.path.relpath(path, REPO_ROOT)
        assert len(_scan(_OPERATOR, skip_set=True).get(rel, [])) == 3, \
            "the sweep missed an evasive name-keyed comparison"
        assert len(_scan(_KEYED_READ, skip_merge=True, require="MATCH")
                   .get(rel, [])) == 1, \
            "`require` is case-sensitive — a lower-case `match` slipped through"
        assert _scan(_FSTRING_LABEL, require="MATCH").get(rel, []) == [], \
            "a docstring that quotes a retired pattern must not fabricate a hit"
    finally:
        os.remove(path)


class TestThe12120BlankNameBlindSpot:
    """A whitespace-only name IS mintable, and equality cannot see it (#9599).

    #12120 review round 14 introduced this pin on a docstring claim that a blank
    name is not mintable; round 15 falsified the claim AND its replacement. What is
    actually true, measured on this backend:

    * ``create_object("   ")`` mints a node named three spaces, readable BY ID;
    * an indexed equality filter on that value matches NOTHING — the #9599
      behaviour, identical for a padded non-blank name — so ``main``'s raw
      ``{name:$name}`` anchor wrote no edge for it either (measured: 0), and
      ``name_key_space_same_entity``'s ``("", {})`` answer is
      engine-contingent rather than a fold decision;
    * every name-keyed read in the fold agrees today only because of that filter.

    So the pin holds the two facts the helper's contract now states, and the
    write-door question (should such a name be mintable at all) is #12242.
    """

    def test_a_blank_named_holder_exists_and_equality_cannot_see_it(
            self, sdk_factory):
        from tortoise.entity_identity import (
            _live_holders_same_entity,
            name_key_space_same_entity,
        )

        sdk = sdk_factory()
        try:
            g = sdk._get_proj().g
            made = sdk.create_object("   ", "document")
            assert made and made.get("name") == "   ", made
            # read back BY ID: the point of the test is that equality cannot.
            row = g.query("MATCH (o:Object {id:$id}) RETURN o.name, size(o.name)",
                          params={"id": made["id"]}).result_set
            assert row and row[0][0] == "   " and int(row[0][1]) == 3, row

            assert g.query("MATCH (o:Object) WHERE o.name = $n RETURN o.id",
                           params={"n": "   "}).result_set == [], (
                "#9599 no longer hides a whitespace-bearing name from equality: "
                "`name_key_space_same_entity`'s zero-row answer is no longer "
                "agreement with the rest of the fold — re-derive that contract")

            assert name_key_space_same_entity("o", "   ") == ("", {})
            holders, _props = _live_holders_same_entity(g, "Object", "   ")
            assert holders == [], holders
        finally:
            sdk.close()
