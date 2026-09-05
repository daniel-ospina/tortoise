"""#1999 (W3) shared seed-core unit tests — lane-agnostic.

The seed core (tortoise/onboarding/seed.py) must be importable WITHOUT
hosted_api (mirror of the state.py hygiene rule — the same module is the
reusable half of surface 15 that W12's self-hosted path consumes), own the
ontology vocabulary + normalization + collision semantics, and perform the
two-Subject seed against a duck-typed SDK surface (create_subject /
create_edge / query) so every lane (hosted REST, MCP, self-hosted) shares
identical ontology behavior.

These tests run in BOTH lanes (no graph required — in-memory fake SDK).
"""
from __future__ import annotations

import pytest

from tortoise.onboarding.seed import (
    ORG_ANCHOR_KIND,
    PERSON_ANCHOR_KIND,
    SubjectCollision,
    derive_display_name_from_email,
    find_subject_by_name,
    is_own_subject,
    normalize_person_kind,
    seed_onboarding_anchors,
)

# ── in-memory fake SDK (MERGE-by-name semantics mirror) ───────

class _FakeResult:
    def __init__(self, rows):
        self.result_set = rows


class _FakeSDK:
    """Duck-typed SDK: subjects store + edges list, MERGE-on-name ON MATCH
    kind/refs write (projection semantics), deterministic name-derived id."""

    def __init__(self):
        self.subjects: dict[str, dict] = {}
        self.edges: list[tuple] = []

    def query(self, cypher, **params):
        name = params.get("name")
        rows = [[dict(self.subjects[name])]] if name in self.subjects else []
        return _FakeResult(rows)

    def _id_for(self, label, name):
        import hashlib
        digest = hashlib.sha256(f"{label}:{name}".encode()).hexdigest()[:26]
        return f"{label[:3].lower()}-{digest}"

    def create_subject(self, name, subjectKind="other", **props):
        node = self.subjects.get(name)
        if node is None:
            node = {"id": self._id_for("Subject", name), "name": name,
                    "subjectKind": subjectKind, "status": "live"}
            node.update(props)
            self.subjects[name] = node
            return dict(node)
        # ON MATCH: canonical id wins; subjectKind=coalesce; props SET +=
        node["id"] = node.get("id") or self._id_for("Subject", name)
        if subjectKind:
            node["subjectKind"] = subjectKind
        for k, v in props.items():
            node[k] = v
        return dict(node)

    def create_edge(self, relation, from_id, to_id):
        edge = (relation, from_id, to_id)
        created = edge not in self.edges
        if created:
            self.edges.append(edge)
        return {"edge": {"relation": relation, "from": from_id, "to": to_id},
                "created": created, "nudges": []}


def _make_sdk(subjects=None):
    sdk = _FakeSDK()
    for name, props in (subjects or {}).items():
        sdk.subjects[name] = props
    return sdk


# ── module hygiene ────────────────────────────────────────────

class TestModuleHygiene:
    def test_no_hosted_api_import(self):
        import ast
        import inspect
        import sys
        before = set(sys.modules)
        import tortoise.onboarding.seed as m
        newly = set(sys.modules) - before
        assert "tortoise.hosted_api" not in newly
        src = inspect.getsource(m)
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module and \
                    "hosted_api" in node.module:
                raise AssertionError(
                    f"seed.py imports hosted_api via {node.module!r}")
            if isinstance(node, ast.Import):
                for a in node.names:
                    if "hosted_api" in a.name:
                        raise AssertionError(
                            f"seed.py imports hosted_api via {a.name!r}")

    def test_anchor_kinds_are_subject_subclasses(self):
        # ontology §4.2/§5: organization + naturalPerson are Subject core
        # subclasses — never Object/Statement kinds (B1 regression guard).
        assert ORG_ANCHOR_KIND == "organization"
        assert PERSON_ANCHOR_KIND == "naturalPerson"


# ── kind normalization ────────────────────────────────────────

class TestNormalizePersonKind:
    def test_legacy_person_normalized(self):
        assert normalize_person_kind("person") == "naturalPerson"

    def test_already_canonical_unchanged(self):
        assert normalize_person_kind("naturalPerson") == "naturalPerson"

    def test_other_kinds_unchanged(self):
        assert normalize_person_kind("organization") == "organization"
        assert normalize_person_kind("team") == "team"
        assert normalize_person_kind("") == ""
        assert normalize_person_kind(None) is None


# ── email-prefix display-name derivation ─────────────────────

class TestDeriveDisplayName:
    def test_dotted_local_part_title_cased(self):
        assert derive_display_name_from_email(
            "alex.johnson@example.com") == "Alex Johnson"

    def test_separators(self):
        assert derive_display_name_from_email("alex_johnson@x.com") == "Alex Johnson"
        assert derive_display_name_from_email("alex-johnson@x.com") == "Alex Johnson"

    def test_plus_tag_dropped(self):
        # gmail-style +tag: subaddress of the base — the tag is NOT a name word
        assert derive_display_name_from_email("alex+work@x.com") == "Alex"

    def test_single_token(self):
        assert derive_display_name_from_email("john@x.com") == "John"

    def test_numeric_local_part_kept(self):
        assert derive_display_name_from_email("123@x.com") == "123"

    def test_no_at_sign_none(self):
        assert derive_display_name_from_email("not-an-email") is None

    def test_empty_and_missing(self):
        assert derive_display_name_from_email("") is None
        assert derive_display_name_from_email("@x.com") is None
        assert derive_display_name_from_email(None) is None


# ── identity / collision classification ──────────────────────

class TestIsOwnSubject:
    def test_org_match_by_org_id(self):
        assert is_own_subject({"org_id": "t1"}, org_id="t1") is True

    def test_org_mismatch(self):
        assert is_own_subject({"org_id": "t2"}, org_id="t1") is False

    def test_org_ref_absent_never_claimed(self):
        # no org_id prop on the existing subject → identity unprovable →
        # NOT ours (never claim a ref-less same-name node)
        assert is_own_subject({"name": "Acme"}, org_id="t1") is False

    def test_person_match_by_user_id(self):
        assert is_own_subject({"user_id": "u1"}, user_id="u1") is True

    def test_person_user_id_mismatch_wins_over_email(self):
        # different user_id → different identity even with same email
        assert is_own_subject({"user_id": "u2", "email": "a@x.com"},
                              user_id="u1", email="a@x.com") is False

    def test_person_email_match_when_existing_user_ref_absent(self):
        assert is_own_subject({"email": "a@x.com"},
                              email="a@x.com") is True

    def test_person_email_mismatch(self):
        assert is_own_subject({"email": "b@x.com"}, email="a@x.com") is False

    def test_person_no_refs_never_claimed(self):
        assert is_own_subject({"name": "Alex"}, user_id=None, email=None) is False

    def test_org_anchor_not_person(self):
        # org_id ref does not make a subject "ours" for the person anchor
        assert is_own_subject({"org_id": "t1"}, user_id="u1") is False


class TestFindSubjectByName:
    def test_found(self):
        sdk = _make_sdk({"Acme": {"id": "sub-1", "name": "Acme",
                                  "subjectKind": "organization"}})
        props = find_subject_by_name(sdk, "Acme")
        assert props["id"] == "sub-1"

    def test_not_found(self):
        assert find_subject_by_name(_make_sdk(), "Acme") is None


# ── two-Subject seed ──────────────────────────────────────────

class TestSeedOnboardingAnchors:
    def test_files_exactly_two_subjects_member_of(self):
        sdk = _make_sdk()
        rep = seed_onboarding_anchors(
            sdk, org_name="Acme Labs", person_name="Alex Johnson",
            org_id="team-1", user_id="user-1", person_email="alex.johnson@x.com")
        assert rep["org_created"] is True
        assert rep["person_created"] is True
        org = rep["org_subject"]
        person = rep["user_subject"]
        assert org["subjectKind"] == "organization"
        assert person["subjectKind"] == "naturalPerson"
        assert org["org_id"] == "team-1"
        assert person["user_id"] == "user-1"
        assert person["email"] == "alex.johnson@x.com"
        # exactly 2 Subject nodes; memberOf person → org
        assert set(sdk.subjects) == {"Acme Labs", "Alex Johnson"}
        assert ("memberOf", person["id"], org["id"]) in sdk.edges
        assert rep["member_of"]["created"] is True
        # never Object/Statement (no such nodes exist in the fake store)
        assert all(s["subjectKind"] in ("organization", "naturalPerson")
                   for s in sdk.subjects.values())

    def test_person_anchor_uses_correct_kind_not_other(self):
        sdk = _make_sdk()
        rep = seed_onboarding_anchors(
            sdk, org_name="Acme", person_name="Alex", org_id="t1")
        assert rep["user_subject"]["subjectKind"] == "naturalPerson"

    def test_replay_idempotent_reuses_canonical(self):
        sdk = _make_sdk()
        rep1 = seed_onboarding_anchors(
            sdk, org_name="Acme", person_name="Alex", org_id="t1",
            user_id="u1", person_email="alex@x.com")
        rep2 = seed_onboarding_anchors(
            sdk, org_name="Acme", person_name="Alex", org_id="t1",
            user_id="u1", person_email="alex@x.com")
        assert rep2["org_created"] is False
        assert rep2["person_created"] is False
        assert rep2["org_subject"]["id"] == rep1["org_subject"]["id"]
        assert rep2["user_subject"]["id"] == rep1["user_subject"]["id"]
        assert len(sdk.subjects) == 2
        assert len([e for e in sdk.edges if e[0] == "memberOf"]) == 1

    def test_same_name_distinct_org_collision_never_silent_merge(self):
        # existing org Subject for a DIFFERENT org_id — the seed must raise,
        # never attach our org_id ref to the distinct identity.
        sdk = _make_sdk({"Acme": {"id": "sub-other", "name": "Acme",
                                  "subjectKind": "organization",
                                  "org_id": "team-other"}})
        with pytest.raises(SubjectCollision) as ei:
            seed_onboarding_anchors(
                sdk, org_name="Acme", person_name="Alex", org_id="team-1")
        assert ei.value.existing_id == "sub-other"
        # the distinct subject is untouched (no ref merge)
        assert sdk.subjects["Acme"]["org_id"] == "team-other"
        assert "Alex" not in sdk.subjects

    def test_same_name_distinct_person_collision(self):
        sdk = _make_sdk({"Alex": {"id": "sub-alex2", "name": "Alex",
                                  "subjectKind": "naturalPerson",
                                  "user_id": "user-2"}})
        with pytest.raises(SubjectCollision):
            seed_onboarding_anchors(
                sdk, org_name="Acme", person_name="Alex", org_id="t1",
                user_id="user-1", person_email="alex@x.com")
        assert sdk.subjects["Alex"]["user_id"] == "user-2"

    def test_ref_less_same_name_person_collision(self):
        # same-name person with NO refs → identity unprovable → collision
        # (never silently claim a legacy node)
        sdk = _make_sdk({"Alex": {"id": "sub-legacy", "name": "Alex",
                                  "subjectKind": "naturalPerson"}})
        with pytest.raises(SubjectCollision):
            seed_onboarding_anchors(
                sdk, org_name="Acme", person_name="Alex", org_id="t1",
                user_id="user-1", person_email="alex@x.com")

    def test_legacy_person_normalized_on_match(self):
        # existing 'person'-kind subject with OUR email + no user ref →
        # reused AND normalized to naturalPerson (DM-3: normalize, don't
        # validate-block)
        sdk = _make_sdk({"Alex": {"id": "sub-alex", "name": "Alex",
                                  "subjectKind": "person",
                                  "email": "alex@x.com"}})
        rep = seed_onboarding_anchors(
            sdk, org_name="Acme", person_name="Alex", org_id="t1",
            person_email="alex@x.com")
        assert rep["user_subject"]["id"] == "sub-alex"
        assert rep["user_subject"]["subjectKind"] == "naturalPerson"
        assert rep["person_kind_normalized"] is True
        assert sdk.subjects["Alex"]["subjectKind"] == "naturalPerson"

    def test_person_name_required_when_included(self):
        with pytest.raises(ValueError):
            seed_onboarding_anchors(
                _make_sdk(), org_name="Acme", person_name="   ",
                org_id="t1")

    def test_org_name_required(self):
        with pytest.raises(ValueError):
            seed_onboarding_anchors(
                _make_sdk(), org_name="", person_name="Alex", org_id="t1")
