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

The single-coordinate sites and the SDK read paths get both directions — an
**(a)** single-holder resolves test and a **(b)** two-live-same-name refuses
test. `commit_ops` and `assembly` are pinned on the refusal side (their split
probes share one never-guess discipline), with `commit_ops` additionally
pinning that two DISTINCT id-less same-name carriers are not collapsed by the
arm dedupe. A source-level sweep then asserts no *new* name-keyed identity read
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

    def test_id_less_holder_keeps_the_name_ambiguous(self, sdk_factory):
        """#3633 review P1: an id-less live carrier is still a CARRIER. A name
        held by one id-less and one id-bearing live Subject is TWO holders —
        refuse, never resolve onto the addressable one and discard the other."""
        sdk = sdk_factory()
        subj = sdk.create_subject("half", "analyst")
        sdk._get_proj().g.query(
            "CREATE (s:Subject {name:'half', subjectKind:'analyst', "
            "status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_entity_id(sdk._get_proj().g, "Subject", "half")
        assert subj["id"] in ei.value.candidate_ids
        assert "<no-id>" in ei.value.candidate_ids

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

    def test_document_target_id_less_holder_keeps_ambiguity(self, sdk_factory):
        """#3633 review P1: the name arm pre-filtered id-less candidates, so one
        id-less plus one id-bearing live Object of the same name resolved onto
        the addressable one instead of refusing."""
        sdk = sdk_factory()
        obj = sdk.create_object("dupart", "thing")
        sdk._get_proj().g.query(
            "CREATE (o:Object {name:'dupart', objectKind:'thing', "
            "status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_document_target_id(sdk._get_proj().g, "dupart")
        assert obj["id"] in ei.value.candidate_ids
        assert "<no-id>" in ei.value.candidate_ids

    def test_document_target_duplicate_url_refuses(self, sdk_factory):
        """#3633 review P2: two Sources sharing one url (one id-less) is a
        duplicate-identity claim — refuse, never return the addressable one."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (n:Source {id:'src-1', name:'doc-a', "
            "documentKind:'transcript', url:'https://x.test/d', "
            "status:'live'})")
        proj.g.query(
            "CREATE (n:Source {name:'doc-b', documentKind:'transcript', "
            "url:'https://x.test/d', status:'live'})")
        with pytest.raises(AmbiguousEntityName) as ei:
            resolve_document_target_id(proj.g, "https://x.test/d")
        assert "src-1" in ei.value.candidate_ids
        assert "<no-id>" in ei.value.candidate_ids


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
        assert obj["id"]  # artifact existed

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

    def test_url_only_document_source_refuses_loud(self, sdk_factory):
        """#3633 review P2: an id-less document Source is NOT addressable as a
        ``create_edge`` target (that resolves by id | eventId — no url branch),
        so the resolver must NOT substitute its url. Substituting validated a
        node the caller could not then link to and silently dropped the
        ``uses`` edge; the refusal is now loud."""
        sdk = sdk_factory()
        sdk.create_subject("approver-u", "analyst")
        sdk._get_proj().g.query(
            "CREATE (n:Source {name:'url-doc', documentKind:'transcript', "
            "url:'https://example.test/doc', status:'live'})")
        with pytest.raises(ValueError, match="does not exist"):
            sdk.file_human_approval(
                approver_id="approver-u",
                artifact_id="https://example.test/doc",
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

    def test_two_live_same_name_refuses(self):
        handle = _FakeSeedHandle(
            {"Acme": [{"id": "sub-1", "name": "Acme"},
                      {"id": "sub-2", "name": "Acme"}]})
        with pytest.raises(AmbiguousEntityName):
            find_subject_by_name(handle, "Acme")

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

    def test_two_id_less_same_name_ref_never_folds_either_carrier(
            self, sdk_factory):
        """#3633 review P1: the split probes deduped by the projected
        ``(id, name)`` pair, so two DISTINCT id-less same-name Objects both
        projecting ``(None, 'dup')`` collapsed to ONE row — hiding the >1-name
        never-guess and folding BOTH carriers by name. The dedupe must key on
        node identity."""
        sdk = sdk_factory()
        proj = sdk._get_proj()
        # raw CREATE with no id: the legacy id-less carrier shape.
        for _ in range(2):
            proj.g.query("CREATE (o:Object {name:'idless', "
                         "objectKind:'thing', status:'live'})")
        proj.g.query("CREATE (o:Object {id:'succ-il', name:'succ-il', "
                     "objectKind:'thing', status:'live'})")
        warns: list[str] = []
        applied = apply_supersessions(
            proj, sdk,
            [{"superseded": "idless", "supersedes_by": "succ-il"}],
            session_id="s3633-idless", warn=warns.append)
        assert applied == 0, warns
        assert any("matches 2 Objects by name" in w for w in warns), warns
        rows = proj.g.query(
            "MATCH (o:Object {name:'idless'}) "
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

    def test_two_live_same_name_ref_is_refused(self, sdk_factory):
        sdk = sdk_factory()
        _create_object_rows(sdk, "dup", ["obj-a", "obj-b"])
        assert docker_resolver_port(sdk).exact_objects(["dup"]) == []

    def test_two_objects_claiming_one_id_is_refused(self, sdk_factory):
        """#3633 review P2: the id arm was a value-keyed dict, so two live
        Objects claiming ONE id silently kept the last. Duplicate-id is the
        same corruption ``resolve_entity_id`` refuses — mirror the refusal."""
        sdk = sdk_factory()
        _create_object_rows(sdk, "share-a", ["shared-id"])
        _create_object_rows(sdk, "share-b", ["shared-id"])
        assert docker_resolver_port(sdk).exact_objects(["shared-id"]) == []


# ── source-level sweep ────────────────────────────────────────────────────
#
# The issue's blind spot is the OPERATOR form (`.name = $x` / `.name IN $x`)
# and the f-string label: neither appears in a `grep "OR .*\.name = \$"` sweep,
# and the `get_reputation` fallback had no `OR` at all. This sweep covers the
# operator form, the `{name:$x}` keyed form, and the `{label}`-interpolated
# f-string form.
#
# It is NOT exhaustive for the whole class, and a clean run here is not a claim
# that no name-keyed identity read exists. A WRAPPED comparison
# (`toLower(x.name) = toLower($n)`), a differently named f-string label
# (`{entity_label}`), and a name-keyed map build in Python all fall outside
# these patterns. Sites of those shapes are deliberately NOT pulled in here —
# they need a disposition this issue does not own — and are recorded on #3633
# (follow-up comment) instead of being silently excused. Widen a pattern only
# together with an owner for the hits it surfaces.
#
# Every surviving hit is enumerated with the disposition that owns it. The
# allowlist is LIVE: a site that disappears fails the test (stale entry), and a
# site that appears fails it (undispositioned coordinate).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TORTOISE_DIR = os.path.join(REPO_ROOT, "tortoise")

_OPERATOR = re.compile(r"\.name\s*(?:=|IN)\s*\$")
_KEYED_READ = re.compile(r":(?:Object|Subject)\s*\{\{?\s*name\s*:")
_FSTRING_LABEL = re.compile(r"\{label\}\s*\{\{?\s*name")

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
        # `ID(o)` is projected so the arm dedupe keys on NODE identity — two
        # distinct id-less same-name Objects must stay two rows.
        '"MATCH (o:Object) WHERE o.name IN $names RETURN ID(o), o.id, o.name",',
        '"MATCH (o:Object) WHERE o.name = $ref "',
    },
    "tortoise/assembly.py": {
        # THIS PR's route-then-refuse NAME arm inside exact_objects.
        '"WHERE o.name IN $names "',
        # §B — `_probe_visible_successors` successor-name probe, S3/id-probe
        # owned. (The `_state_header_hit` name in an earlier version of this
        # comment was wrong: that function contains no Cypher at all.)
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
        # #1370 — a DIAGNOSTIC existence probe: it returns ONLY a count and
        # selects the "successor is a :Subject, entity supersession is
        # Object-only" warning. It resolves no id, folds nothing and picks no
        # carrier, so it is not an identity read (#3633 hazard absent);
        # #1370-owned.
        '"MATCH (s:Subject {name:$sb}) RETURN count(s)",',
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
        'f"MATCH (n:{label} {{name:$name}}) "',
    },
    "tortoise/sdk.py": {
        # §B P1-A "Delete in S1" — `_create_entity` canonical-id re-fetch.
        'f"MATCH (n:{label} {{name: $name}}) RETURN n.id",',
    },
    "tortoise/subject_binding.py": {
        # #1370 — ALREADY route-then-refuse: `LIMIT 2` + `len(rows) != 1 ->
        # (None, None)` resolves exactly one holder or refuses, so it never
        # unions two carriers and never picks one (the #3633 hazard absent).
        # #1370-owned and name-keyed by that module's owner-locked contract.
        # NOT identical to `entity_identity.resolve_entity_id`: it applies no
        # `_terminal_excluded` liveness filter, so a name whose ONLY holder is
        # terminal still resolves. That divergence is a #1370 / live-holder
        # question, not a #3633 union/guess, and is reported on #1370 rather
        # than dispositioned here.
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
