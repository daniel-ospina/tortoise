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

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.assembly import docker_resolver_port
from tortoise.commit_ops import apply_supersessions
from tortoise.entity_identity import (
    AmbiguousEntityName,
    resolve_document_target_id,
    resolve_entity_id,
)
from tortoise.onboarding.seed import (
    SubjectCollision,
    find_subject_by_name,
    seed_onboarding_anchors,
)

# ── helpers ───────────────────────────────────────────────────────────────


def _create_subject_rows(sdk, name, ids):
    """Raw-CREATE one live Subject per id under ``name``.

    The SDK's own write path MERGEs on ``{name}`` on main, so it can only ever
    produce ONE carrier. Two live same-name Subjects are the D2 shape an
    explicit-id writer produces; a raw write is how a test reaches it.
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

    def test_id_of_a_terminal_holder_still_resolves(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'ghost', "
                     "status:'superseded'})")
        assert resolve_entity_id(proj.g, "Subject", "sub-dead") == "sub-dead"

    def test_unknown_name_and_unknown_id_are_none(self, sdk_factory):
        g = sdk_factory()._get_proj().g
        assert resolve_entity_id(g, "Subject", "nobody") is None
        assert resolve_entity_id(g, "Subject", None) is None

    def test_non_identity_label_is_refused(self, sdk_factory):
        g = sdk_factory()._get_proj().g
        with pytest.raises(RuntimeError):
            resolve_entity_id(g, "Point", "p1")

    def test_document_target_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "artifact-dup", ["obj-a", "obj-b"])
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(sdk._get_proj().g, "artifact-dup")

    def test_document_target_two_nodes_claiming_one_id_refuse(self, sdk_factory):
        """An id claimed by two nodes is corruption — never a silent pick."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-dup', name:'n1', status:'live'})")
        g.query("CREATE (o:Object {id:'obj-dup', name:'n2', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(g, "obj-dup")

    def test_document_target_two_nodes_claiming_one_url_refuse(self, sdk_factory):
        """A url claimed by two candidates is corruption — never a silent pick."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Source {id:'src-a', url:'https://x.example/1', "
                "documentKind:'file', status:'live'})")
        g.query("CREATE (s:Source {id:'src-b', url:'https://x.example/1', "
                "documentKind:'file', status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(g, "https://x.example/1")

    def test_two_nodes_claiming_one_id_refuse(self, sdk_factory):
        """The id arm's never-guess guard: refuse, do not return the first."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dup', name:'n1', status:'live'})")
        g.query("CREATE (s:Subject {id:'sub-dup', name:'n2', status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(g, "Subject", "sub-dup")
        assert set(ei.value.candidate_ids) == {"sub-dup"}

    def test_idless_live_holder_counts_toward_ambiguity(self, sdk_factory):
        """A live holder with NO id is still a holder — and must refuse.

        This base really mints id-less `:Object`/`:Subject` nodes (a bare
        `MERGE (o:Object {name:$name})` — the aboutObject wiring in
        `hosted_api`). Counting only id-bearing rows would let the resolver
        SILENTLY PICK the id-bearing carrier of a two-live-holder name, which
        is the guess #3633 exists to refuse.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-live', name:'dup', status:'live'})")
        g.query("CREATE (s:Subject {name:'dup'})")  # id-less, status NULL -> LIVE
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(g, "Subject", "dup")
        assert ei.value.holder_count == 2
        # only the id-bearing carrier can be named as a target
        assert ei.value.candidate_ids == ("sub-live",)

    def test_lone_idless_holder_does_not_resolve_a_name(self, sdk_factory):
        """One live holder with no id: nothing to route to -> None, no raise."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {name:'orphan'})")
        assert resolve_entity_id(g, "Subject", "orphan") is None

    def test_document_target_idless_holder_counts_toward_ambiguity(
            self, sdk_factory):
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-live', name:'art', status:'live'})")
        g.query("CREATE (o:Object {name:'art'})")  # id-less, status NULL -> LIVE
        with pytest.raises(AmbiguousEntityName):
            resolve_document_target_id(g, "art")


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

    def test_org_structure_single_live_name_resolves(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        root = sdk.create_subject("root-org", "organization")
        member = sdk.create_subject("member-1", "role")
        proj.g.query(
            "MATCH (p:Subject {id:$m}), (s:Subject {id:$r}) "
            "MERGE (p)-[:memberOf]->(s)",
            params={"m": member["id"], "r": root["id"]})
        proj.g.query(
            "MATCH (p:Subject {id:$r}), (x:Subject {id:$m}) "
            "MERGE (p)-[:holdsRole]->(x)",
            params={"m": member["id"], "r": root["id"]})
        org = sdk.get_org_structure("root-org")
        assert any(m["id"] == member["id"] for m in org["members"])
        assert any(r["id"] == member["id"] for r in org["roles"])

    def test_org_structure_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "branch-dup", ["sub-a", "sub-b"])
        with pytest.raises(AmbiguousEntityName):
            sdk.get_org_structure("branch-dup")


# ── file_human_approval ───────────────────────────────────────────────────


class TestFileHumanApproval:
    def _claim(self, sdk):
        return sdk.create_point("statement", "a live claim", status="live")

    def test_single_live_name_resolves(self, sdk_factory):
        sdk = sdk_factory()
        subj = sdk.create_subject("approver-x", "analyst")
        obj = sdk.create_object("artifact-x", "thing")
        result = sdk.file_human_approval(
            approver_id="approver-x", artifact_id="artifact-x",
            point_ids=[self._claim(sdk)["id"]])
        assert result["event_id"]
        g = sdk._get_proj().g
        # The REBINDING is the point: every downstream write must carry the
        # RESOLVED ID, never the name the caller passed. Asserting a traversal
        # from `{name:'approver-x'}` would pass either way (name and id reach
        # the same node in the single-holder case) — assert the stored value.
        rows = g.query("MATCH (p:Point {id:$pid}) RETURN p.authoredBy",
                       params={"pid": result["decision_point_id"]}).result_set
        assert rows[0][0] == subj["id"]
        # ...and the ARTIFACT rebinding too: the `uses` edge must point at the
        # resolved Object id, not at the name the caller passed.
        rows = g.query(
            "MATCH (e:Event {eventId:$eid})-[:uses]->(o:Object) RETURN o.id",
            params={"eid": result["event_id"]}).result_set
        assert [r[0] for r in rows] == [obj["id"]], rows
        r = g.query(
            "MATCH (s:Subject {id:$sid})-[:performs]->(e:Event) "
            "RETURN count(e)", params={"sid": subj["id"]}).result_set
        assert r[0][0] == 1
        assert obj["id"]  # artifact existed

    def test_single_live_object_url_resolves(self, sdk_factory):
        """An `:Object` carries a `url` too — `_connect_issue_objects` writes
        `MERGE (o:Object {id:$oid}) SET … o.url=$url`. The URL arm must probe
        the same candidate space as the id/name arms, not `:Source` alone."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-issue', name:'issue 1', "
                "objectKind:'issue', status:'live', url:'https://x.example/1'})")
        assert resolve_document_target_id(
            g, "https://x.example/1") == "obj-issue"

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
    """Duck-typed seed handle: ``name`` -> the rows the graph would return."""

    def __init__(self, by_name):
        self._by_name = by_name

    def query(self, cypher, **params):
        name = params.get("name") or (params.get("params") or {}).get("name")
        return _Rows([[dict(p)] for p in self._by_name.get(name, [])])


class _RealGraphHandle:
    """The production seed-handle shape (``.query(cypher, **params)``) over a
    REAL graph, so the seeded Cypher — including the live-holder filter — is
    actually executed (``_FakeSeedHandle`` returns rows by name and cannot see
    it)."""

    def __init__(self, g):
        self._g = g

    def query(self, cypher, **params):
        return self._g.query(cypher, params=params)


class _RealSeedHandle:
    """The PRODUCTION seed-handle shape (`hosted_api._OrgSeedSurface`) over a
    REAL graph: `query` executes the seed's Cypher, and `create_subject` /
    `create_edge` go through the SDK, so a refusal is proven by the graph being
    untouched rather than by a fake."""

    def __init__(self, sdk):
        self._sdk = sdk
        self._g = sdk._get_proj().g

    def query(self, cypher, **params):
        return self._g.query(cypher, params=params)

    def create_subject(self, name, subjectKind="other", **props):
        return self._sdk.create_subject(name, subjectKind=subjectKind, **props)

    def create_edge(self, relation, from_id, to_id):
        return self._sdk.create_edge(relation, from_id, to_id)


class TestFindSubjectByName:
    def test_single_live_match_resolves(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme", "status": "live"}]})
        assert find_subject_by_name(handle, "Acme")["id"] == "sub-1"

    def test_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "Acme", ["sub-1", "sub-2"])
        with pytest.raises(AmbiguousEntityName):
            find_subject_by_name(_RealGraphHandle(sdk._get_proj().g), "Acme")

    def test_terminal_holder_does_not_block_a_live_match(self, sdk_factory):
        """The seeded Cypher's live-holder filter, against a real graph.

        One live + one terminal same-name Subject resolves to the LIVE one;
        only two live holders may refuse.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                "status:'superseded'})")
        g.query("CREATE (s:Subject {id:'sub-live', name:'Acme', status:'live'})")
        props = find_subject_by_name(_RealGraphHandle(g), "Acme")
        assert props is not None and props["id"] == "sub-live"

    def test_only_terminal_holders_yield_none(self, sdk_factory):
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'a', name:'Gone', status:'superseded'})")
        g.query("CREATE (s:Subject {id:'b', name:'Gone', status:'retracted'})")
        assert find_subject_by_name(_RealGraphHandle(g), "Gone") is None

    def test_seed_maps_ambiguity_to_subject_collision(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme", "org_id": "t1"},
                      {"id": "sub-2", "name": "Acme", "org_id": "t1"}]})
        with pytest.raises(SubjectCollision) as ei:
            seed_onboarding_anchors(handle, org_name="Acme", org_id="t1",
                                    include_person=False)
        # the ambiguity path carries its own caller-facing question, so a client
        # does not report it as an ownership mismatch (there is no single
        # existing identity to be "not this org/user")
        assert ei.value.existing_id is None
        assert ei.value.question and "more than one live" in ei.value.question

    def test_terminal_holder_not_ours_is_still_refused(self, sdk_factory):
        """Pre-#3633 classified a terminal same-name Subject and refused when
        it was NOT ours. The live-only resolver reports the name as free, so
        without this the seed would create straight over a dead node."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                "status:'superseded', subjectKind:'organization'})")
        with pytest.raises(SubjectCollision) as ei:
            seed_onboarding_anchors(_RealSeedHandle(sdk), org_name="Acme",
                                    org_id="t1", include_person=False)
        assert ei.value.existing_id == "sub-dead"
        # zero writes: the terminal node is untouched
        rows = g.query("MATCH (s:Subject {name:'Acme'}) "
                       "RETURN s.id, s.status").result_set
        assert rows == [["sub-dead", "superseded"]], rows

    def test_terminal_holder_that_is_ours_is_reused(self, sdk_factory):
        """OURS is idempotently reused — the pre-#3633 contract, preserved.

        The anchor carries this org's ref, so the seed reuses it (and the
        name-keyed create on this base revives it) rather than refusing or
        minting a SECOND carrier for the same name.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                "status:'superseded', subjectKind:'organization', org_id:'t1'})")
        report = seed_onboarding_anchors(_RealSeedHandle(sdk), org_name="Acme",
                                         org_id="t1", include_person=False)
        assert report["org_created"] is False
        rows = g.query("MATCH (s:Subject {name:'Acme'}) "
                       "RETURN count(s)").result_set
        assert rows[0][0] == 1, rows

    def test_two_terminal_holders_never_pick_one(self, sdk_factory):
        """Several terminal holders: the old ``LIMIT 1`` picked arbitrarily."""
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (s:Subject {id:'sub-a', name:'Acme', "
                "status:'superseded'})")
        g.query("CREATE (s:Subject {id:'sub-b', name:'Acme', "
                "status:'retracted'})")
        with pytest.raises(SubjectCollision) as ei:
            seed_onboarding_anchors(_RealSeedHandle(sdk), org_name="Acme",
                                    org_id="t1", include_person=False)
        assert ei.value.existing_id is None
        assert ei.value.question and "terminal" in ei.value.question

    def test_no_holder_at_all_is_still_creatable(self, sdk_factory):
        """The refusal above must not block a genuinely free name."""
        sdk = sdk_factory()
        report = seed_onboarding_anchors(_RealSeedHandle(sdk), org_name="Fresh",
                                         org_id="t1", include_person=False)
        assert report["org_created"] is True


# ── the list unions: commit_ops + assembly ────────────────────────────────


class TestCommitOpsSupersessionProbe:
    def test_two_live_same_name_ref_never_folds_either_carrier(self, sdk_factory):
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

    def test_two_nodes_claiming_one_id_still_never_guess(self, sdk_factory):
        """The split arms must not hide a duplicate-id corruption.

        The two probes are deduped CROSS-arm (one node matched by both arms is
        one node), but a row repeated WITHIN one arm is two nodes claiming one
        id — the guard below must still see both (a plain dict-dedupe collapsed
        them into one row and folded it silently).
        """
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for _ in range(2):
            proj.g.query("CREATE (o:Object {id:'obj-dup', name:'dupname', "
                         "objectKind:'thing', status:'live'})")
        proj.g.query("CREATE (o:Object {id:'obj-succ', name:'succ', "
                     "objectKind:'thing', status:'live'})")
        warns: list[str] = []
        applied = apply_supersessions(
            proj, sdk,
            [{"superseded": "obj-dup", "supersedes_by": "succ"}],
            session_id="s3633", warn=warns.append)
        assert applied == 0
        assert any("never-guess" in w for w in warns), warns


class TestAssemblyExactObjects:
    def test_id_arm_and_name_arm_resolve_apart(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "nx", ["obj-x"])
        _create_object_rows(sdk, "same", ["obj-y"])
        got = docker_resolver_port(sdk).exact_objects(["obj-x", "same"])
        assert {r["id"] for r in got} == {"obj-x", "obj-y"}

    def test_id_arm_wins_over_a_name_arm_match(self, sdk_factory):
        """The arms must be resolved APART, not unioned for one coordinate.

        One ref whose string is Object A's `id` AND Object B's `name`: the old
        single `o.id IN $names OR o.name IN $names` query returned BOTH (a
        union of two identities in one coordinate). Route-then-refuse resolves
        the id arm outright and returns exactly one.
        """
        sdk = sdk_factory()
        _create_object_rows(sdk, "shared", ["obj-id-arm"])
        _create_object_rows(sdk, "obj-id-arm", ["obj-name-arm"])
        got = docker_resolver_port(sdk).exact_objects(["obj-id-arm"])
        assert [r["id"] for r in got] == ["obj-id-arm"], got

    def test_two_live_same_name_ref_is_refused(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        assert docker_resolver_port(sdk).exact_objects(["dup"]) == []

    def test_two_nodes_claiming_one_id_are_refused(self, sdk_factory):
        """An id claimed by two nodes is corruption, never a last-wins pick.

        A plain `{r[0]: ...}` dict collapsed the two rows (the pre-change list
        comprehension returned both); the ref is now refused like any other
        never-guess case.
        """
        sdk = sdk_factory()
        g = sdk._get_proj().g
        g.query("CREATE (o:Object {id:'obj-dup', name:'n1', "
                "objectKind:'thing', status:'live'})")
        g.query("CREATE (o:Object {id:'obj-dup', name:'n2', "
                "objectKind:'thing', status:'live'})")
        assert docker_resolver_port(sdk).exact_objects(["obj-dup"]) == []


# ── source-level sweep ────────────────────────────────────────────────────
#
# The issue's blind spot is the OPERATOR form (`.name = $x` / `.name IN $x`)
# and the f-string label: neither appears in a `grep "OR .*\.name = \$"` sweep,
# and the `get_reputation` fallback had no `OR` at all. This sweep covers the
# operator form, the `{name:$x}` keyed form, and the f-string label form.
#
# Every surviving hit is enumerated with the disposition that owns it. The
# allowlist is LIVE: a site that disappears fails the test (stale entry), and a
# site that appears fails it (undispositioned coordinate).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORTOISE_DIR = os.path.join(REPO_ROOT, "tortoise")

_OPERATOR = re.compile(r"\.name\s*(?:=|IN)\s*\$")
_KEYED_READ = re.compile(r":(?:Object|Subject)\s*\{\{?\s*name\s*:")
# #5510: the label may be ANY interpolated name (`{label}`, `{entity_label}`,
# `{n['label']}` …), not just the literal `{label}` — a literal-only pattern is
# blind to exactly the sites the widened sweep was introduced to see. The
# trailing `\s*:` is load-bearing: without it, `{{namespace: $ns}}` matches on
# the `name` prefix of `namespace` (a false positive in pack_manifest_store).
_FSTRING_LABEL = re.compile(r"\{[^}]*\}\s*\{\{?\s*name\s*:")

_ALLOWED_OPERATOR = {
    # THE sanctioned name->id read (this PR's resolver).
    "tortoise/entity_identity.py": {
        'f"MATCH (n) WHERE {_DOCUMENT_PREDICATE} AND n.name = $value "',
    },
    "tortoise/sdk.py": {
        # Object TOPIC match (topic/canonical_name), not an identity read.
        '"AND (o.name = $topic OR o.canonical_name = $topic) "',
    },
    "tortoise/commit_ops.py": {
        # §B "Route" — successor-name candidate probe, S3 makes it an id probe.
        '"MATCH (o:Object) WHERE o.name IN $names RETURN o.name, o.id",',
        # THIS PR's route-then-refuse NAME arm (id arm probed separately).
        '"MATCH (o:Object) WHERE o.name IN $names RETURN o.id, o.name",',
        '"MATCH (o:Object) WHERE o.name = $ref "',
    },
    "tortoise/assembly.py": {
        # THIS PR's route-then-refuse NAME arm inside exact_objects.
        '"WHERE o.name IN $names "',
        # §B — `_state_header_hit` successor-name probe, S3/id-probe owned.
        '"MATCH (o:Object) WHERE o.name IN $names "',
    },
}

_ALLOWED_KEYED = {
    "tortoise/sdk.py": {
        # aboutObject linking fallback + ingest existence probes (§B "Route").
        '"MATCH (o:Object {name:$n}) "',
        '"(o:Object {name:$n}) "',
        '"MATCH (n:Subject {name:$name}) RETURN n.id",',
        '"MATCH (n:Object {name:$name}) RETURN n.id",',
    },
    "tortoise/mining.py": {
        # §B resolver caller (S1 key-parity / S2 mint-flip).
        'r = proj.g.query("MATCH (o:Object {name:$name}) RETURN o.id",',
    },
    "tortoise/commit_ops.py": {
        # §B "Route" — successor-name probe, S3/id-probe owned.
        '"MATCH (o:Object {name:$sb}) RETURN o.id, o.name, o.status",',
        # #1370 diagnostic: a `count(s)` existence probe used ONLY to word the
        # "successor is a :Subject" warning accurately (the generic dangling
        # message would misdiagnose a correctly-typed node). It resolves no
        # identity — the count is never used as an id. Owner #5509 (an upstream
        # site, not introduced by #3633).
        '"MATCH (s:Subject {name:$sb}) RETURN count(s)",',
    },
    "tortoise/hosted_api.py": {
        # §A hosted session-capture paired anchor (S1).
        '"MATCH (p:Point {id:$pid}), (o:Object {name:$name}) "',
    },
    "tortoise/onboarding/seed.py": {
        # THIS PR's two routed reads — the LIVE one (find_subject_by_name,
        # live-filtered, single-holder-or-refuse) and the TERMINAL one
        # (terminal_subject_props, the pre-#3633 collision classification that
        # the live-only read would otherwise hide). Both normalise identically.
        '"MATCH (s:Subject {name: $name}) "',
    },
    "tortoise/projection/edges.py": {
        # §B "Route" — resolve_structural_target / about-edge anchors.
        '"MATCH (s:Subject {name:$name}) RETURN ID(s), s.id LIMIT 1",',
        'f"MATCH (n:{n[\'label\']} {{{n[\'key\']}:$pid}}), '
        '(s:Subject {{name:$name}}) "',
    },
    "tortoise/projection/entities.py": {
        # §B "Route" — lifecycle folds / participants fallback / edge anchors;
        # S3 deletes the supersession name fallback.
        '"MATCH (s:Subject {name:$name}) REMOVE s.embedding",',
        '"MATCH (n:Subject {name: $name})", {"name": name},',
        '"MATCH (o:Object {name:$name}) REMOVE o.embedding",',
        '"MATCH (n:Object {name: $name})", {"name": name},',
        '"MATCH (o:Object {name:$name}) " + live,',
        '"MATCH (s:Subject {name:$name}), (e:Event {eventId:$eid}) "',
        '"MATCH (o:Object {name:$name}), (e:Event {eventId:$eid}) "',
        '"MATCH (o:Object {name:$n}) "',
        '"MATCH (s:Subject {name: $name}), (e:Event {eventId: $eid}) "',
    },
}

_ALLOWED_FSTRING = {
    "tortoise/entity_identity.py": {
        # THE sanctioned name->id read (this PR's resolver).
        'f"MATCH (n:{label} {{name:$name}}) "',
    },
    "tortoise/sdk.py": {
        # §B P1-A "Delete in S1" — `_create_entity` canonical-id re-fetch.
        'f"MATCH (n:{label} {{name: $name}}) RETURN n.id",',
    },
    "tortoise/topic_summarization.py": {
        # #5510 (filed from this work): the f-string label is `entity_label`,
        # not `label` — a name-keyed Object/Subject seed read that unions both
        # same-name carriers' Points. §B `Route`; owner #5510 owns the fix.
        'f"MATCH (p:Point)-[:{edge_type}]->(e:{entity_label} {{name: $name}}) "',
    },
    "tortoise/subject_binding.py": {
        # Upstream #1370's OWN name->id resolver. It refuses AMBIGUITY —
        # `RETURN n.id, n.<kind> ... LIMIT 2` then `len(rows) != 1: return
        # None, None`, so two same-name carriers never resolve — but it applies
        # NO live/terminal filter (it is not routed through the D2 predicate),
        # so a name held by exactly ONE TERMINAL carrier still resolves to that
        # terminal node. That residual is REAL and is NOT fixed here: #1370
        # landed after this work's base and owns the binder's own contract
        # (single-query kind-property read, fail-closed refusal). Owner #5509.
        'f"MATCH (n:{label} {{name:$name}}) "',
    },
    "tortoise/projection/edges.py": {
        # §B "Route" — resolve_structural_target / about-edge anchors.
        'q = (f"MATCH (x:{label} {{name:$key}}) "',
        'f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",',
        'f"MATCH (n:{n[\'label\']} {{{n[\'key\']}:$sid}}), '
        '(e:{label} {{name:$name}}) "',
    },
}


def _scan(pattern, *, require_match=False, skip_set=False,
          skip_merge=False):
    """{relpath: [normalized matching source lines]} over ``tortoise/**.py``.

    Comment-only lines are skipped: a comment that quotes a retired pattern is
    documentation, not a coordinate. ``skip_set``/``skip_merge`` drop write
    lines (``SET x.name =`` / ``MERGE (o:Object {name:``) — this sweep is about
    READS; the matching writes are S1's blast radius.
    """
    hits: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(TORTOISE_DIR):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, REPO_ROOT)
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    if not pattern.search(line):
                        continue
                    if require_match and "MATCH" not in line:
                        continue
                    if skip_set and "SET " in line:
                        continue
                    if skip_merge and "MERGE" in line:
                        continue
                    hits.setdefault(rel, []).append(" ".join(line.split()))
    return hits


def _assert_allowlisted(hits, allowed):
    def norm(s):
        return re.sub(r"\s+", " ", s).strip()

    for rel, lines in hits.items():
        assert rel in allowed, (
            f"#3633: a name-keyed Object/Subject identity read reappeared in "
            f"{rel}: {lines} — disposition it (route-then-refuse) or add it to "
            f"the allowlist with its owner")
        expected = {norm(s) for s in allowed[rel]}
        for line in lines:
            assert norm(line) in expected, (
                f"#3633: {rel} carries a name-keyed coordinate not in the "
                f"allowlist: {line!r} (allowed: {sorted(expected)})")
    # The allowlist may not silently outlive the sites it excuses.
    for rel, snippets in allowed.items():
        live = {norm(x) for x in hits.get(rel, [])}
        for snippet in snippets:
            assert norm(snippet) in live, (
                f"#3633: allowlist entry {rel}: {snippet!r} matches nothing — "
                f"the site it excused is gone; remove the entry")


def test_no_undispositioned_operator_form_identity_read():
    _assert_allowlisted(_scan(_OPERATOR, skip_set=True), _ALLOWED_OPERATOR)


def test_no_undispositioned_keyed_identity_read():
    _assert_allowlisted(
        _scan(_KEYED_READ, skip_merge=True), _ALLOWED_KEYED)


def test_no_undispositioned_fstring_label_identity_read():
    _assert_allowlisted(
        _scan(_FSTRING_LABEL, require_match=True), _ALLOWED_FSTRING)
