"""#12047 — entity resolution is real, and the queue never reports a false clean.

The defect this file pins
-------------------------
``TortoiseSDK.list_dedup_candidates(candidate_type='entity')`` returned a
hard-coded ``[]`` (#783's stub), so the live MCP tool
``tortoise_list_dedup_candidates`` answered **"are there duplicate entities?"**
with *"none"* when the truth was *"cannot answer"*. #12047 measured the damage on
the hosted graphs and reopened #783, which had been closed COMPLETED on an
acceptance criterion naming ``tortoise/entity_resolver.py`` — a file that has
never existed in git history.

What is pinned here
-------------------
* **the stub is gone**: a graph holding two near-duplicate Subjects RETURNS a
  pair. This is the test that FAILS on the pre-fix tree (``return []``).
* **the honest empty**: a scan that cannot run RAISES, and never degrades to an
  empty list a caller could read as "no duplicates".
* **the deterministic rung resolves onto the existing entity**: two writes that
  differ only by a leading article / case / punctuation produce ONE node, not a
  twin — and the survivor keeps the caller's surface form.
* **the ladder**: exact → fuzzy → semantic, with the semantic rung's proposals
  never auto-mergeable.
* **review parity with Points**: ``approve_merge`` merge/reject work on
  entities, the merge is NON-DESTRUCTIVE (no node deleted, the alias is recorded
  so a later write of the variant resolves), and an ambiguous pair REFUSES.
* **``holdsRole`` production**: a role Subject declared with ``roleHolder`` is
  linked to its person — the edge that had ZERO occurrences in both hosted
  graphs — and an unresolvable holder refuses rather than guessing.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.entity_resolver import (
    EntityScanUnavailable,
    apply_entity_merge,
    entity_candidates,
    entity_pair_for,
    normalize_entity_name,
    resolve_entity_name,
    similarity,
)

# ── helpers ───────────────────────────────────────────────────────────────


def _count(sdk, label: str, **where) -> int:
    clause = "".join(f" AND n.{k} = ${k}" for k in where)
    rows = sdk._get_proj().g.query(
        f"MATCH (n:{label}) WHERE 1=1{clause} RETURN count(n)",
        params=where or None,
    ).result_set
    return int(rows[0][0]) if rows else 0


def _create_raw(sdk, label: str, node_id: str, name: str, kind_prop: str,
                kind: str) -> None:
    """A raw create — the only way to reach a *pre-existing* duplicate.

    Since #3590 the SDK door resolves a name before minting, so no public writer
    produces a twin by accident. The hosted graphs' damage (the issue's measured
    Subjects) is exactly this shape: nodes written before any resolution existed.
    """
    sdk._get_proj().g.query(
        f"CREATE (n:{label} {{id:$id, name:$name, {kind_prop}:$kind, "
        "status:'live', createdAt:$ts})",
        params={"id": node_id, "name": name, "kind": kind, "ts": "2026-01-01"},
    )


# ── the fold (pure) ───────────────────────────────────────────────────────


class TestNormalizeEntityName:
    @pytest.mark.parametrize("a,b", [
        ("the owner", "owner"),
        ("  The   Owner ", "owner"),
        ("the fresh-context code reviewer", "fresh-context code reviewer"),
        ("daniel-ospina/agent-infra", "daniel ospina agent infra"),
        ("Port 16379", "port 16379"),
    ])
    def test_spellings_that_carry_no_identity_fold_together(self, a, b):
        assert normalize_entity_name(a) == normalize_entity_name(b)

    def test_a_leading_article_alone_still_yields_a_usable_key(self):
        # A key of "" would match every entity in the graph.
        assert normalize_entity_name("the") != ""
        assert normalize_entity_name("...") != ""

    def test_genuinely_different_names_do_not_fold_together(self):
        assert normalize_entity_name("the founder") != normalize_entity_name("the operator")


# ── the stub (the test that fails on the pre-fix tree) ────────────────────


class TestEntityQueueIsNoLongerAStub:
    def test_two_near_duplicates_are_returned_where_the_stub_returned_empty(self, sdk_factory):
        """FAILS on the pre-fix tree: ``candidate_type='entity'`` was ``[]``.

        The two names are the issue's measured damage class (one role under three
        spellings); their difflib ratio is ~0.815, inside the 0.60–0.92 band.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-1", "fresh-context code reviewer",
                    "subjectKind", "role")
        _create_raw(sdk, "Subject", "sub-2", "the fresh-context reviewer role",
                    "subjectKind", "role")

        rows = sdk.list_dedup_candidates(candidate_type="entity")
        assert rows, "the entity queue returned nothing for a known near-duplicate pair"
        row = rows[0]
        assert row["candidate_type"] == "entity"
        assert {row["candidate_id"], row["existing_id"]} == {"sub-1", "sub-2"}
        assert row["method"] == "fuzzy"
        assert 0.60 <= row["similarity"] < 0.92
        # Both spellings are roles, so the kinds agree — no mismatch flag.
        assert row["kind_mismatch"] is False

    def test_the_queue_carries_the_shape_the_review_surface_reads(self, sdk_factory):
        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-1", "a16z Speedrun", "objectKind", "organization")
        _create_raw(sdk, "Object", "obj-2", "a16z Speedrun 2026 application",
                    "objectKind", "organization")
        row = sdk.list_dedup_candidates(candidate_type="entity")[0]
        for key in ("id", "candidate_id", "existing_id", "target_id", "name",
                    "existing_name", "label", "method", "similarity",
                    "candidate_type", "status"):
            assert key in row, f"missing review-queue field {key!r}"
        assert row["label"] == "Object"
        assert row["existing_name"] in {"a16z Speedrun", "a16z Speedrun 2026 application"}

    def test_a_scan_that_cannot_run_raises_instead_of_going_empty(self, monkeypatch):
        """The honest empty: 'could not compare' must never read as 'clean'."""
        import tortoise.entity_resolver as er

        class _Broken:
            def query(self, *_a, **_kw):
                raise RuntimeError("backend down")

        with pytest.raises(EntityScanUnavailable) as ei:
            entity_candidates(_Broken())
        assert "refusing to report an empty queue" in str(ei.value)

    def test_the_sdk_boundary_propagates_the_failure_rather_than_returning_empty(
            self, sdk_factory, monkeypatch):
        import tortoise.entity_resolver as er

        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-1", "alpha one", "subjectKind", "other")

        def _boom(*_a, **_kw):
            raise EntityScanUnavailable("scan unavailable")

        monkeypatch.setattr(er, "entity_candidates", _boom)
        with pytest.raises(EntityScanUnavailable):
            sdk.list_dedup_candidates(candidate_type="entity")

    def test_unknown_candidate_type_still_refuses(self, sdk_factory):
        sdk = sdk_factory()
        with pytest.raises(ValueError):
            sdk.list_dedup_candidates(candidate_type="nonsense")


# ── the deterministic rung: a near-duplicate resolves onto the existing entity


class TestDeterministicResolutionReusesTheEntity:
    def test_two_spellings_that_carry_no_identity_produce_one_node(self, sdk_factory):
        """The repro: today's tree mints a twin; after the fix it reuses the node."""
        sdk = sdk_factory()
        first = sdk.create_subject("fresh-context code reviewer", "role")
        second = sdk.create_subject("the fresh-context code reviewer", "role")
        assert first["id"] == second["id"]
        assert _count(sdk, "Subject") == 1
        # The caller's surface form is preserved on the surviving node — the fold
        # is a lookup key, never a rewrite of what was written (#3590).
        names = {r[0] for r in sdk._get_proj().g.query(
            "MATCH (s:Subject) RETURN s.name").result_set}
        assert names == {"fresh-context code reviewer"}

    def test_punctuation_and_case_also_resolve(self, sdk_factory):
        sdk = sdk_factory()
        a = sdk.create_object("daniel-ospina/agent-infra", "repository")
        b = sdk.create_object("Daniel Ospina Agent Infra", "repository")
        assert a["id"] == b["id"]
        assert _count(sdk, "Object") == 1

    def test_the_folded_spelling_written_second_still_resolves(self, sdk_factory):
        """The measured damage pair, in the direction that has no fold delta.

        Write "the owner" first (name carries the article), then "owner" — whose
        own spelling IS already folded. A guard on ``folded != name`` made the
        folded lookup inert exactly here, minting the twin the issue is about.
        """
        sdk = sdk_factory()
        first = sdk.create_subject("the owner", "naturalPerson")
        second = sdk.create_subject("owner", "naturalPerson")
        assert second["id"] == first["id"]
        assert _count(sdk, "Subject") == 1

    def test_a_genuinely_different_name_still_mints(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("the founder", "naturalPerson")
        sdk.create_subject("the operator", "naturalPerson")
        assert _count(sdk, "Subject") == 2

    def test_resolve_entity_name_reports_the_rung_it_took(self, sdk_factory):
        sdk = sdk_factory()
        node = sdk.create_subject("owner", "naturalPerson")
        g = sdk._get_proj().g
        exact = resolve_entity_name(g, "Subject", "the OWNER")
        assert exact["resolution"] == "exact" and exact["entity_id"] == node["id"]
        assert exact["requires_review"] is False

        _create_raw(sdk, "Subject", "sub-role-1", "fresh-context code reviewer",
                    "subjectKind", "role")
        fuzzy = resolve_entity_name(g, "Subject", "the fresh-context reviewer role")
        assert fuzzy["resolution"] == "fuzzy"
        assert fuzzy["requires_review"] is True, (
            "a similarity score alone must never resolve without review")

        new = resolve_entity_name(g, "Subject", "a wholly unrelated entity")
        assert new["resolution"] == "new" and new["entity_id"] is None


# ── the semantic rung ─────────────────────────────────────────────────────


class TestSemanticTier:
    def test_a_pair_only_the_vectors_see_is_proposed_and_never_auto_merged(self, sdk_factory):
        """Names that share a block but not a string, with an injected encoder.

        The encoder is injected (the #783 plan's deterministic-fixture rule): a
        test never asserts against live model output.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "abc one", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-b", "abc zzzzz", "subjectKind", "other")
        g = sdk._get_proj().g
        assert similarity(normalize_entity_name("abc one"),
                          normalize_entity_name("abc zzzzz")) < 0.60, (
            "this test needs a pair the FUZZY rung misses")

        def _identical_vectors(texts):
            return ([[1.0, 0.0, 0.0] for _ in texts], True)

        rows = entity_candidates(g, encode_fn=_identical_vectors)
        assert rows, "the semantic rung proposed nothing"
        assert rows[0]["method"] == "semantic"
        assert rows[0]["semantic_similarity"] == pytest.approx(1.0)
        assert rows[0]["auto_mergeable"] is False, (
            "a semantic proposal must never be auto-mergeable (#2730: a raw "
            "cosine is not calibrated against this graph's names)")

    def test_a_failing_encoder_degrades_to_the_string_rungs_without_raising(self, sdk_factory):
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "fresh-context code reviewer",
                    "subjectKind", "role")
        _create_raw(sdk, "Subject", "sub-b", "the fresh-context reviewer role",
                    "subjectKind", "role")

        def _boom(_texts):
            raise RuntimeError("model unavailable")

        rows = entity_candidates(sdk._get_proj().g, encode_fn=_boom)
        assert rows and rows[0]["method"] == "fuzzy"


# ── review parity with Points: merge / reject ─────────────────────────────


class TestApproveMergeOnEntities:
    def _pair(self, sdk):
        _create_raw(sdk, "Subject", "sub-old", "fresh-context code reviewer",
                    "subjectKind", "role")
        _create_raw(sdk, "Subject", "sub-new", "the fresh-context reviewer role",
                    "subjectKind", "role")
        return sdk.list_dedup_candidates(candidate_type="entity")[0]

    def test_merge_records_the_alias_and_deletes_nothing(self, sdk_factory):
        sdk = sdk_factory()
        row = self._pair(sdk)
        result = sdk.approve_merge(row["candidate_id"], action="merge")
        assert result["candidate_type"] == "entity"
        assert result["wired"] is True
        assert result["survived_id"] == row["existing_id"]

        # NOTHING was deleted, and the survivor recorded the alias.
        assert _count(sdk, "Subject") == 2
        g = sdk._get_proj().g
        survivor = g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.canonical_name, s.aliases",
            params={"id": row["existing_id"]}).result_set[0]
        assert survivor[0] == normalize_entity_name(
            g.query("MATCH (s:Subject {id:$id}) RETURN s.name",
                    params={"id": row["existing_id"]}).result_set[0][0])
        assert normalize_entity_name(row["name"]) in survivor[1]

        # The candidate left the queue and carries its provenance.
        merged = g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.resolved_from, s.dedup_reviewed",
            params={"id": row["candidate_id"]}).result_set[0]
        assert merged == [row["existing_id"], "merge"]
        assert [r["candidate_id"] for r in
                sdk.list_dedup_candidates(candidate_type="entity")
                ] == []

        # …and a LATER write of the variant resolves instead of minting a twin.
        again = sdk.create_subject("the fresh-context reviewer role", "role")
        assert again["id"] == row["existing_id"]
        assert _count(sdk, "Subject") == 2

    def test_reject_keeps_the_candidate_separate_and_out_of_the_queue(self, sdk_factory):
        sdk = sdk_factory()
        row = self._pair(sdk)
        result = sdk.approve_merge(row["candidate_id"], action="reject")
        assert result["action"] == "reject" and result["wired"] is False
        g = sdk._get_proj().g
        props = g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.dedup_reviewed, s.resolved_from",
            params={"id": row["candidate_id"]}).result_set[0]
        assert props[0] == "reject" and props[1] is None
        # The survivor gained no alias: a rejection writes nothing onto it.
        assert g.query(
            "MATCH (s:Subject {id:$id}) RETURN coalesce(s.aliases, [])",
            params={"id": row["existing_id"]}).result_set[0][0] == []
        assert [r["candidate_id"] for r in
                sdk.list_dedup_candidates(candidate_type="entity")] == []

    def test_a_repeat_of_the_same_verdict_is_idempotent(self, sdk_factory):
        sdk = sdk_factory()
        row = self._pair(sdk)
        sdk.approve_merge(row["candidate_id"], action="reject")
        again = sdk.approve_merge(row["candidate_id"], action="reject")
        assert again["already_reviewed"] is True

    def test_an_ambiguous_pair_refuses_instead_of_picking_a_survivor(self, sdk_factory):
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-1", "aaa bbb1", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-2", "aaa bbb2", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-3", "aaa bbb3", "subjectKind", "other")
        g = sdk._get_proj().g
        # sub-3's two rivals are equidistant — the resolver must refuse.
        with pytest.raises(ValueError, match="refusing"):
            entity_pair_for(g, "sub-3")
        row = [r for r in sdk.list_dedup_candidates(candidate_type="entity")
               if r["candidate_id"] == "sub-3"][0]
        assert row["ambiguous"] is True and row["tied_with"]

    def test_a_repeat_merge_is_idempotent_and_a_verdict_cannot_be_flipped(
            self, sdk_factory):
        sdk = sdk_factory()
        row = self._pair(sdk)
        first = sdk.approve_merge(row["candidate_id"], action="merge")
        again = sdk.approve_merge(row["candidate_id"], action="merge")
        assert first["wired"] is True
        assert again["already_reviewed"] is True
        # …and "merge" then "reject" is a verdict FLIP, not a second opinion:
        # the survivor's alias was written by the merge, so the two would
        # disagree about the same candidate.
        with pytest.raises(ValueError, match="already reviewed"):
            sdk.approve_merge(row["candidate_id"], action="reject")

    def test_apply_entity_merge_refuses_a_self_merge(self, sdk_factory):
        sdk = sdk_factory()
        node = sdk.create_subject("self merge", "other")
        with pytest.raises(ValueError):
            apply_entity_merge(sdk, node["id"], node["id"],
                               method="exact", similarity=1.0)


# ── holdsRole production (person → role) ──────────────────────────────────


class TestHoldsRoleProduction:
    def test_a_role_declared_with_a_holder_is_linked(self, sdk_factory):
        """The edge both hosted graphs had ZERO of (#12047)."""
        sdk = sdk_factory()
        person = sdk.create_subject("Daniel", "naturalPerson")
        role = sdk.create_subject("owner", "role", roleHolder="Daniel")
        rows = sdk._get_proj().g.query(
            "MATCH (p:Subject)-[:holdsRole]->(r:Subject) "
            "RETURN p.id, r.id").result_set
        assert [list(r) for r in rows] == [[person["id"], role["id"]]]

    def test_the_holder_may_be_addressed_by_id(self, sdk_factory):
        sdk = sdk_factory()
        person = sdk.create_subject("Operator", "naturalPerson")
        role = sdk.create_subject("the operator", "role", roleHolder=person["id"])
        assert sdk._get_proj().g.query(
            "MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)"
        ).result_set[0][0] == 1

    def test_an_unresolvable_holder_refuses_rather_than_guessing(self, sdk_factory):
        sdk = sdk_factory()
        with pytest.raises(ValueError, match="matches no live"):
            sdk.create_subject("owner", "role", roleHolder="nobody at all")
        assert sdk._get_proj().g.query(
            "MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)"
        ).result_set[0][0] == 0

    def test_role_holder_on_a_non_role_refuses(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("Daniel", "naturalPerson")
        with pytest.raises(ValueError, match="subjectKind='role'"):
            sdk.create_subject("an organisation", "organization", roleHolder="Daniel")

    def test_the_holder_prop_never_lands_on_the_node(self, sdk_factory):
        sdk = sdk_factory()
        sdk.create_subject("Daniel", "naturalPerson")
        role = sdk.create_subject("owner", "role", roleHolder="Daniel")
        props = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:$id}) RETURN properties(s)",
            params={"id": role["id"]}).result_set[0][0]
        assert "roleHolder" not in props and "role_holder" not in props
