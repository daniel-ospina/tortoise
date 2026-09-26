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
directions. For the two `commit_ops` refusal sites the never-guess BEHAVIOUR
pre-existed; at all three (`commit_ops` ×2 and `assembly`, where the base
UNIONED both carriers) the tests additionally pin this PR's delta — the
**recorded non-folded entry** (asserted through ``caplog``) — so a revert of
the recording is red, not green. `session_link.py`'s fuzzy suffix probe (the
form `_FUZZY_NAME` covers) is pinned both ways as well. A source-level sweep then
asserts no *new* name-keyed identity read lands outside the documented
allowlist. The sweep covers the literal keyed form (`{name:$x}`), the OPERATOR
form (`.name = $x` / `.name IN $x`), the f-string-label form
(`f"...{label} {{name:$x}}"`) and the function-wrapped comparison
(`toLower(s.name) = $x`).

The ONE allowlisted site this PR does not fix — discovered by the widened
f-string sweep — is `tortoise/topic_summarization.py`'s Object/Subject
re-resolution by name; it is unlisted in the plan's §B/§B.1 tables and is filed
as **#5510** (the owning issue; remove the allowlist entry when it lands).
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
from tortoise.session_link import _resolve_targets

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
        sdk._create_entity("Subject", "alice",
                           {"name": "alice-work", "subjectKind": "analyst",
                            "status": "live"}, "SubjectAdded")
        sdk._create_entity("Subject", "bob",
                           {"name": "alice", "subjectKind": "reviewer",
                            "status": "live"}, "SubjectAdded")
        assert resolve_entity_id(sdk._get_proj().g, "Subject", "alice") == "alice"

    def test_null_status_is_live_and_outdated_flag_is_dead(self, sdk_factory):
        # #3590 D2's `null_status_with_outdated_flag` vector: a legacy node
        # with NO status is LIVE, and `outdated=true` is a second dead marker
        # even when the status itself is live (the literal-predicate trap).
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-legacy', name:'legacy'})")
        assert resolve_entity_id(proj.g, "Subject", "legacy") == "sub-legacy"
        proj.g.query("CREATE (s:Subject {id:'sub-stale', name:'stale', "
                     "status:'live', outdated:true})")
        assert resolve_entity_id(proj.g, "Subject", "stale") is None

    def test_idless_live_carrier_counts_toward_ambiguity(self, sdk_factory):
        # An id-less legacy carrier is a LIVE carrier: it must not be filtered
        # out of the ambiguity count and silently stepped over.
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {name:'dup', status:'live'})")
        proj.g.query("CREATE (s:Subject {id:'sub-real', name:'dup', "
                     "status:'live'})")
        with pytest.raises(AmbiguousEntityName):
            resolve_entity_id(proj.g, "Subject", "dup")

    def test_two_idless_live_carriers_refuse_not_none(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {name:'dup', status:'live'})")
        proj.g.query("CREATE (s:Subject {name:'dup', status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(proj.g, "Subject", "dup")
        # neither carrier has a routable id — the refusal still fires, and
        # the message reports 2 carriers, not 0.
        assert ei.value.candidate_ids == ()
        assert ei.value.carrier_count == 2

    def test_lone_idless_live_carrier_is_not_routable(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {name:'anon', status:'live'})")
        assert resolve_entity_id(proj.g, "Subject", "anon") is None

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


# ── provenance (the wrapped-comparison site the sweep now covers) ─────────


class TestProvenance:
    def test_single_live_name_resolves_case_insensitively(self, sdk_factory):
        # the documented legacy contract (test_provenance_case_insensitive_match)
        # must survive the route-then-refuse conversion.
        sdk = sdk_factory()
        sdk.create_subject("El Dato Team", subjectKind="team")
        p = sdk.create_point("statement", "B claim", authoredBy="el dato team")
        assert sdk.provenance(p["id"])["subject"]["name"] == "El Dato Team"

    def test_two_live_same_name_refuses(self, sdk_factory):
        sdk = sdk_factory()
        _create_subject_rows(sdk, "dup", ["sub-a", "sub-b"])
        p = sdk.create_point("statement", "a claim", authoredBy="dup")
        with pytest.raises(AmbiguousEntityName):
            sdk.provenance(p["id"])

    def test_sole_terminal_holder_does_not_resolve(self, sdk_factory):
        # the old probe had no live filter, so a superseded holder satisfied it.
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'ghost', "
                     "status:'superseded'})")
        p = sdk.create_point("statement", "a claim", authoredBy="ghost")
        assert sdk.provenance(p["id"])["subject"] is None

    def test_authored_by_id_resolves_by_id(self, sdk_factory):
        # `file_human_approval` rebinds `authoredBy` to the resolved ID, so the
        # read must accept an id, not only a name.
        sdk = sdk_factory()
        subj = sdk.create_subject("someone", subjectKind="person")
        p = sdk.create_point("statement", "a claim", authoredBy=subj["id"])
        assert sdk.provenance(p["id"])["subject"]["name"] == "someone"


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
        # ... and the `uses` edge lands on the resolved artifact node
        uses = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid})-[:uses]->(a) RETURN a.id",
            params={"eid": result["event_id"]}).result_set
        assert uses and uses[0][0] == obj["id"]

    def test_display_string_keeps_the_caller_reference(self, sdk_factory):
        # the display strings keep the ORIGINAL coordinate, not the resolved
        # id — a regression here would rewrite user-visible text (#3633).
        sdk = sdk_factory()
        sdk.create_subject("approver-r", "analyst")
        sdk.create_object("artifact-r", "thing")
        result = sdk.file_human_approval(
            approver_id="approver-r", artifact_id="artifact-r",
            point_ids=[self._claim(sdk)["id"]])
        assert sdk.get_point(result["decision_point_id"])["content"] == (
            "Approved: artifact-r")

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
        assert any("non-folded entry" in r.message for r in caplog.records)

    def test_seed_maps_ambiguity_to_subject_collision(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme", "org_id": "t1"},
                      {"id": "sub-2", "name": "Acme", "org_id": "t1"}]})
        with pytest.raises(SubjectCollision) as ei:
            seed_onboarding_anchors(handle, org_name="Acme", org_id="t1",
                                    include_person=False)
        # the ambiguity is DISCRIMINATED from an identity mismatch, so a
        # caller's message is not built from a false premise (an id-less
        # legacy carrier on the mismatch path also has existing_id None).
        assert ei.value.ambiguous is True and ei.value.existing_id is None

    def test_real_graph_one_live_one_terminal_resolves(self, sdk_factory):
        # the fake handle ignores the Cypher WHERE clause, so pin the live
        # filter against a real graph. The TERMINAL holder is created FIRST so
        # the old unfiltered `LIMIT 1` (which returns by insertion order)
        # would have returned it — this test fails on the base revision.
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'Acme', "
                     "status:'superseded'})")
        proj.g.query("CREATE (s:Subject {id:'sub-live', name:'Acme', "
                     "status:'live'})")
        assert find_subject_by_name(sdk._get_proj(), "Acme")["id"] == "sub-live"

    def test_real_graph_terminal_only_holder_is_none(self, sdk_factory):
        # a lone terminal holder never resolves a name (the base `LIMIT 1`
        # returned it — this is the failure-on-base pin for the live filter).
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (s:Subject {id:'sub-dead', name:'Ghost', "
                     "status:'superseded'})")
        assert find_subject_by_name(sdk._get_proj(), "Ghost") is None


# ── the list unions: commit_ops + assembly ────────────────────────────────


class TestCommitOpsSupersessionProbe:
    def test_two_live_same_name_ref_never_folds_either_carrier(
            self, sdk_factory, caplog):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        proj.g.query("CREATE (o:Object {id:'obj-succ', name:'succ', "
                     "objectKind:'thing', status:'live'})")
        warns: list[str] = []
        with caplog.at_level("WARNING"):
            applied = apply_supersessions(
                proj, sdk,
                [{"superseded": "dup", "supersedes_by": "succ"}],
                session_id="s3633", warn=warns.append)
        assert applied == 0, warns
        # THIS PR's delta at this site is the recorded non-folded entry — the
        # never-guess itself pre-existed, so without this assertion the test
        # would pass on the base revision too.
        assert any("non-folded entry" in r.message and "'dup'" in r.message
                   for r in caplog.records)
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN ['obj-a', 'obj-b'] "
            "RETURN o.id, o.status, o.supersededBy").result_set
        assert len(rows) == 2
        assert all(r[1] == "live" and r[2] is None for r in rows)

    def test_two_idless_same_name_carriers_never_fold(
            self, sdk_factory, caplog):
        # two DISTINCT id-less carriers share every visible column: a dedupe
        # keyed on the columns collapses them into one row and folds both.
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for _ in range(2):
            proj.g.query("CREATE (o:Object {name:'dup', objectKind:'thing', "
                         "status:'live'})")
        proj.g.query("CREATE (o:Object {id:'obj-succ', name:'succ', "
                     "objectKind:'thing', status:'live'})")
        warns: list[str] = []
        with caplog.at_level("WARNING"):
            applied = apply_supersessions(
                proj, sdk,
                [{"superseded": "dup", "supersedes_by": "succ"}],
                session_id="s3633-idless", warn=warns.append)
        assert applied == 0, warns
        # the non-folded entry reports 2 carriers even though NEITHER has a
        # routable id (the carrier_count fix).
        assert any("non-folded entry" in r.message
                   and "2 carrier" in r.message for r in caplog.records)
        rows = proj.g.query(
            "MATCH (o:Object {name:'dup'}) "
            "RETURN o.status, o.supersededBy").result_set
        assert len(rows) == 2
        assert all(r[0] == "live" and r[1] is None for r in rows)


class TestAssemblyExactObjects:
    def test_id_arm_and_name_arm_resolve_apart(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "nx", ["obj-x"])
        _create_object_rows(sdk, "same", ["obj-y"])
        got = docker_resolver_port(sdk).exact_objects(["obj-x", "same"])
        assert {r["id"] for r in got} == {"obj-x", "obj-y"}

    def test_two_live_same_name_ref_is_refused(self, sdk_factory, caplog):
        sdk = sdk_factory()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        with caplog.at_level("WARNING"):
            got = docker_resolver_port(sdk).exact_objects(["dup"])
        assert got == []
        # the refusal records the non-folded entry (this site's delta)
        assert any("non-folded entry" in r.message
                   and "2 carrier" in r.message for r in caplog.records)

    def test_two_nodes_claiming_one_id_is_refused(self, sdk_factory):
        # raw corruption, never a resolvable read (parity with the resolver)
        sdk = sdk_factory()
        proj = sdk._get_proj()
        for name in ("one", "two"):
            proj.g.query(
                "CREATE (o:Object {id:'obj-clash', name:$name, "
                "objectKind:'thing', status:'live'})",
                params={"name": name})
        assert docker_resolver_port(sdk).exact_objects(["obj-clash"]) == []


# ── session_link's fuzzy suffix probe ─────────────────────────────────────


class TestSessionLinkSuffixProbe:
    """`_resolve_targets`'s `o.name ENDS WITH $suffix` probe (the form
    `_FUZZY_NAME` covers): exactly-one LIVE match links; a lone TERMINAL
    holder must not (D2's `single_terminal_holder_reference`), and two live
    same-suffix carriers are the existing honest no-match."""

    def _ref(self):
        return [{"org": "", "repo": "", "num": "42", "form": "bare_num"}]

    def test_lone_live_suffix_holder_links(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (o:Object {id:'obj-live', name:'acme/repo#42', "
                     "objectKind:'pm:issue', status:'live'})")
        assert _resolve_targets(proj, self._ref()) == ["obj-live"]

    def test_lone_terminal_suffix_holder_does_not_link(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (o:Object {id:'obj-dead', name:'acme/repo#42', "
                     "objectKind:'pm:issue', status:'superseded'})")
        assert _resolve_targets(proj, self._ref()) == []

    def test_two_live_same_suffix_holders_do_not_link(self, sdk_factory):
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query("CREATE (o:Object {id:'obj-a', name:'acme/repo#42', "
                     "objectKind:'pm:issue', status:'live'})")
        proj.g.query("CREATE (o:Object {id:'obj-b', name:'other/repo#42', "
                     "objectKind:'pm:issue', status:'live'})")
        assert _resolve_targets(proj, self._ref()) == []


# ── source-level sweep ────────────────────────────────────────────────────
#
# The issue's blind spot is the OPERATOR form (`.name = $x` / `.name IN $x`)
# and the f-string label: neither appears in a `grep "OR .*\.name = \$"` sweep,
# and the `get_reputation` fallback had no `OR` at all. This sweep covers the
# operator form, the `{name:$x}` keyed form, the f-string label form, the
# FUNCTION-WRAPPED comparison (`toLower(s.name) = $x`), and the fuzzy/negative
# operators (`.name ENDS WITH $x` / `CONTAINS` / `<>`) — the wrapped and fuzzy
# forms being how `provenance`'s and `session_link`'s arbitrary/terminal reads
# stayed invisible to the first three.
#
# Every surviving hit is enumerated with the disposition that owns it. The
# allowlist is LIVE: a site that disappears fails the test (stale entry), and a
# site that appears fails it (undispositioned coordinate).
#
# WHAT THIS SWEEP DOES NOT SEE (stated so it is not over-trusted): a
# backtick-quoted property (`` o.`name` = $x ``); a function-wrapped comparison
# with no space after the operator (`toLower(o.name)=toLower($x)`); a label/key
# split across concatenated literals or lines (`_FSTRING_LABEL` needs `MATCH`
# on the same line, `_KEYED_READ` needs label and key on the same line). The
# fuzzy/negative operators ARE covered by `_FUZZY_NAME`, which needs no `MATCH`
# on the line, so a split query (`session_link.py`) is seen.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORTOISE_DIR = os.path.join(REPO_ROOT, "tortoise")

_OPERATOR = re.compile(r"\.name\s*(?:=|IN)\s*\$")
# A keyed MATCH map carrying a `name` key — ANY position in the map, not just
# first: `{name:$x}` and `{id:$cid, name:$x}` are the same identity coordinate,
# and requiring `name` first left the compound form invisible.
_KEYED_READ = re.compile(r":(?:Object|Subject)\s*\{\{?[^{}]*\bname\s*:")
# Any interpolated label (`{label}`, `{lbl}`, `{kind}`) immediately followed by
# a name key. Pinning the variable NAME is how the `_create_entity` re-fetch
# hid for eight review cycles (#3590 P1-A); do not narrow it back. The `:` is
# precisely what excludes a key whose spelling merely STARTS with `name`
# (`{{namespace: …}}`, `{{namefoo: …}}`) — dropping it re-opens that false
# positive, and `{{` alone does NOT exclude it (`name` is a prefix of
# `namespace`).
_FSTRING_LABEL = re.compile(r"\{[^}]*\}\s*\{\{?\s*name\s*:")
# A name comparison WRAPPED in a function call — `toLower(s.name) = $n`,
# `toLower(e.name) CONTAINS $q`. The `\$`-anchored operator sweep above is
# blind to these (the RHS is an expression, not a bare `$param`), which is how
# `TortoiseSDK.provenance`'s arbitrary-carrier read stayed invisible.
_WRAPPED_NAME = re.compile(
    r"\.name\s*\)+\s*(?:=|<>|!=|\bIN\b|\bCONTAINS\b|\bSTARTS\s+WITH\b)\s")
# The FUZZY / NEGATIVE operators with a bare `$param` right-hand side, wrapped
# or not: `.name ENDS WITH $suffix`, `.name CONTAINS $q`, `.name <> $x`. The
# `$` requirement keeps Python attribute comparisons (`Path(t).name != x`) out.
# No `MATCH` requirement: a split query (`session_link.py`) puts this clause on
# its own line.
_FUZZY_NAME = re.compile(
    r"\.name\s*\)*\s*(?:<>|!=|\bENDS\s+WITH\b|\bSTARTS\s+WITH\b|"
    r"\bCONTAINS\b)\s*\$")

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
        # COMPOUND keyed maps (`{id:$cid, name:$name}`) — the registration
        # existence probes behind `_journal_*_registration`: "is this (id, name)
        # pair already registered?", NOT a name-only identity read. They are
        # safe precisely because the id conjunct is present.
        '"MATCH (o:Object {id:$cid, name:$name}) RETURN o.id",',
        '"MATCH (s:Subject {id:$cid, name:$name}) RETURN s.id",',
    },
    "tortoise/mining.py": {
        # §B resolver caller (S1 key-parity / S2 mint-flip).
        'r = proj.g.query("MATCH (o:Object {name:$name}) RETURN o.id",',
    },
    "tortoise/commit_ops.py": {
        # §B "Route" — successor-name probe, S3/id-probe owned.
        '"MATCH (o:Object {name:$sb}) RETURN o.id, o.name, o.status",',
    },
    "tortoise/hosted_api.py": {
        # §A hosted session-capture paired anchor (S1).
        '"MATCH (p:Point {id:$pid}), (o:Object {name:$name}) "',
    },
    "tortoise/onboarding/seed.py": {
        # THIS PR's routed read — live-filtered, single-holder-or-refuse.
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
        'cypher = (f"MATCH (n:{label} {{name:$name}}) "',
    },
    "tortoise/sdk.py": {
        # §B P1-A "Delete in S1" — `_create_entity` canonical-id re-fetch.
        'f"MATCH (n:{label} {{name: $name}}) RETURN n.id",',
    },
    "tortoise/projection/edges.py": {
        # §B "Route" — resolve_structural_target / about-edge anchors.
        'q = (f"MATCH (x:{label} {{name:$key}}) "',
        'f"MATCH (e:{label} {{name:$name}}) RETURN e.name LIMIT 1",',
        'f"MATCH (n:{n[\'label\']} {{{n[\'key\']}:$sid}}), '
        '(e:{label} {{name:$name}}) "',
    },
    "tortoise/topic_summarization.py": {
        # UNLISTED in the plan's §B/§B.1 tables — filed as #5510 (the issue's
        # own rule: an unlisted coordinate is filed, not left silent). Stage 1a
        # narrows a topic-name match to Object/Subject and re-resolves by name,
        # so it can union both same-name carriers' Points; #5510 routes it
        # through resolve_entity_id and pins the refusal.
        'f"MATCH (p:Point)-[:{edge_type}]->(e:{entity_label} {{name: $name}}) "',
    },
}

_ALLOWED_WRAPPED = {
    "tortoise/entity_identity.py": {
        # THIS PR's resolver, case-insensitive NAME arm (`provenance`'s
        # legacy contract) — live-filtered, single-carrier-or-refuse.
        'cypher = (f"MATCH (n:{label}) WHERE toLower(n.name) = toLower($name) "',
    },
    "tortoise/sdk.py": {
        # NOT an identity read: the AgentSession keyword-search clause (a
        # CONTAINS search over a LIST of sessions, no single-identity claim).
        'clauses.append("(toLower(e.name) CONTAINS $q OR any(kw IN '
        'e.keywords WHERE toLower(kw) CONTAINS $q))")',
    },
}


_ALLOWED_FUZZY = {
    # the same AgentSession keyword-search clause as the `_ALLOWED_WRAPPED`
    # entry — NOT an identity read (a CONTAINS search over a LIST of sessions).
    "tortoise/sdk.py": _ALLOWED_WRAPPED["tortoise/sdk.py"],
    "tortoise/session_link.py": {
        # FIXED in this PR: the query also carries
        # `AND {_terminal_excluded('o.status')}` on the preceding line, so a
        # lone TERMINAL pm:issue holder no longer satisfies the suffix match
        # (D2's `single_terminal_holder_reference` vector). The exactly-one
        # rule above it already refuses zero/multiple.
        '"AND o.name ENDS WITH $suffix RETURN o.id",',
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


def test_no_undispositioned_wrapped_name_identity_read():
    _assert_allowlisted(_scan(_WRAPPED_NAME, skip_set=True), _ALLOWED_WRAPPED)


def test_no_undispositioned_fuzzy_name_identity_read():
    _assert_allowlisted(_scan(_FUZZY_NAME, skip_set=True), _ALLOWED_FUZZY)
