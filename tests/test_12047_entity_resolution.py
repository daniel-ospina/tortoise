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
    find_live_holder,
    follow_resolved_from,
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
                kind: str, *, ts: str = "2026-01-01", **extra) -> None:
    """A raw create — the only way to reach a *pre-existing* duplicate.

    Since #3590 the SDK door resolves a name before minting, so no public writer
    produces a twin by accident. The hosted graphs' damage (the issue's measured
    Subjects) is exactly this shape: nodes written before any resolution existed.
    """
    props = {"id": node_id, "name": name, kind_prop: kind,
             "status": "live", "createdAt": ts, **extra}
    assigns = ", ".join(f"{k}: ${k}" for k in props)
    sdk._get_proj().g.query(
        f"CREATE (n:{label} {{{assigns}}})",
        params=props,
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

    def test_a_legacy_row_with_no_stored_key_is_EXACT_not_a_fuzzy_proposal(self, sdk_factory):
        """FAILS on the pre-fix code: it returned ``fuzzy`` + requires_review.

        A pre-#12047 row carries ``name`` only — no ``canonical_name``. The
        deterministic rung is *fold equality*, not *stored-key equality*, so a
        fold-equal match on such a row is an EXACT resolution; reporting it as
        ``fuzzy`` told the caller a deterministic identity needed review, while
        the queue reported the same relationship as ``exact``.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-legacy", "The Owner",
                    "subjectKind", "naturalPerson")
        got = resolve_entity_name(sdk._get_proj().g, "Subject", "owner")
        assert got["resolution"] == "exact", got
        assert got["entity_id"] == "sub-legacy"
        assert got["requires_review"] is False

    def test_two_legacy_rows_that_fold_alike_are_AMBIGUOUS_not_a_pick(self, sdk_factory):
        """FAILS on the pre-fix code: it returned ONE id (the best-scored tie).

        This is the measured damage class — twins written before any resolution
        existed, with no ``canonical_name`` to look up — so ``find_live_holder``
        sees nothing and the fuzzy branch used to pick a winner. Answering "this
        is the entity" for a pair of live twins is the fail-open shape
        route-then-refuse (#3590 R8) forbids.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "The Owner",
                    "subjectKind", "naturalPerson")
        _create_raw(sdk, "Subject", "sub-b", "owner",
                    "subjectKind", "naturalPerson")
        got = resolve_entity_name(sdk._get_proj().g, "Subject", "the OWNER")
        assert got["resolution"] == "ambiguous", got
        assert got["entity_id"] is None, (
            "an ambiguous fold must not hand back a single entity")
        assert sorted(got["candidates"]) == ["sub-a", "sub-b"]
        assert got["requires_review"] is True


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
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == "sub-3")
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


class TestMergeIsDurable:
    def test_a_merge_survives_a_full_wipe_and_replay(self, tmp_path):
        """`derived == replay(journal)` — the claim the ledger rests on.

        A merge that lived only on the node would be the #7951 class: correct
        until the next rebuild, then silently gone (the alias lost, the
        resolution pointer lost, the variant free to mint a twin again). Both
        writes therefore go through ``update_entity``, whose ``EntityMutated``
        record carries the applied map as ``state``.

        The pair is created through the JOURNALED door (``create_subject``), not
        the raw helper: a raw ``CREATE`` writes no record, so replay would have
        no node to fold the mutation onto and the rebuild would (correctly) fail
        the non-folded assertion — that is the test fixture's limitation, not
        this change's.
        """
        from tortoise.sdk import TortoiseSDK

        events = tmp_path / "events"
        events.mkdir()
        sdk = TortoiseSDK(str(tmp_path / "t.db"),
                          event_log_path=str(events / "events.jsonl"),
                          namespace=f"test_suite_{os.urandom(4).hex()}")
        try:
            sdk.create_subject("fresh-context code reviewer", "role")
            sdk.create_subject("the fresh-context reviewer role", "role")
            row = sdk.list_dedup_candidates(candidate_type="entity")[0]
            sdk.approve_merge(row["candidate_id"], action="merge")
            g = sdk._get_proj().g
            q = ("MATCH (s:Subject) RETURN s.id, s.aliases, s.resolved_from, "
                 "s.canonical_name ORDER BY s.id")
            before = [list(r) for r in g.query(q).result_set]
            assert any(r[1] for r in before), "the merge wrote no alias — nothing to replay"

            sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
            after = [list(r) for r in g.query(q).result_set]
            assert after == before, (
                "the merge did not replay: aliases/resolved_from are live-only state")
        finally:
            sdk.close()


class TestReviewFixes:
    """Regression pins for the first review round (all were measured defects)."""

    def test_the_queue_row_and_the_merge_apply_the_SAME_survivor(self, sdk_factory):
        """F2: `entity_pair_for` was a second scanner with different rules.

        The queue elects the OLDER node as survivor and reports one row per
        duplicate; the merge re-derived the pair with max-similarity/lowest-id
        and no reviewed filter — so a verdict on (C into A) could fold C into a
        newer node the reviewer never saw.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-1", "alpha beta gamma", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-2", "alpha beta gammas", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-3", "alpha beta gammaa", "subjectKind", "other")
        g = sdk._get_proj().g
        rows = {r["candidate_id"]: r for r in sdk.list_dedup_candidates(candidate_type="entity")}
        for cid, row in rows.items():
            if row.get("ambiguous"):
                continue
            pair = entity_pair_for(g, cid)
            assert pair is not None, f"{cid} is in the queue but has no pair"
            assert pair["survivor_id"] == row["existing_id"], (
                f"the queue showed {cid}→{row['existing_id']} but the merge would "
                f"apply {cid}→{pair['survivor_id']}")

    def test_a_merged_carrier_is_never_elected_as_a_survivor(self, sdk_factory):
        """F3: a retired carrier received the alias, so the chain grew per re-run."""
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "alpha gamma", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-b", "alpha gammas", "subjectKind", "other")
        row = sdk.list_dedup_candidates(candidate_type="entity")[0]
        sdk.approve_merge(row["candidate_id"], action="merge")
        survivor = row["existing_id"]
        # A fresh exact twin of the MERGED-AWAY name appears.
        _create_raw(sdk, "Subject", "sub-d", row["name"], "subjectKind", "other")
        rows = sdk.list_dedup_candidates(candidate_type="entity")
        for r in rows:
            assert r["existing_id"] != row["candidate_id"], (
                "the merge target is a retired carrier (resolved_from set)")
        # And the alias landed on the real survivor, once.
        aliases = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:$id}) RETURN s.aliases",
            params={"id": survivor}).result_set[0][0]
        assert normalize_entity_name(row["name"]) in aliases

    def test_the_preflight_probe_and_the_write_agree_on_the_folded_key(self, sdk_factory):
        """F1: a probe that passes where the write raises leaves a half write."""
        from tortoise.entity_identity import AmbiguousEntityName, refuse_ambiguous_create
        sdk = sdk_factory()
        # The two carriers are written with the folded key the SDK writes, so the
        # collision is the #12047 shape (same folded name, different spellings).
        for nid, raw in (("sub-1", "the owner"), ("sub-2", "an owner")):
            sdk._get_proj().g.query(
                "CREATE (n:Subject {id:$id, name:$raw, canonical_name:$k, "
                "subjectKind:'naturalPerson', status:'live'})",
                params={"id": nid, "raw": raw,
                        "k": normalize_entity_name(raw)})
        g = sdk._get_proj().g
        with pytest.raises(AmbiguousEntityName):
            refuse_ambiguous_create(g, "Subject", "owner")
        with pytest.raises(AmbiguousEntityName):
            sdk.create_subject("owner", "naturalPerson")

    def test_a_refused_role_holder_leaves_nothing_behind(self, sdk_factory):
        """F5: the refusal ran AFTER the node and its journal line were written."""
        sdk = sdk_factory()
        sdk.create_subject("Daniel", "naturalPerson")
        before = _count(sdk, "Subject")
        with pytest.raises(ValueError, match="matches no live"):
            sdk.create_subject("owner", "role", roleHolder="nobody at all")
        assert _count(sdk, "Subject") == before, (
            "a refused create wrote a Subject — the caller was told it failed")
        with pytest.raises(ValueError, match="subjectKind='role'"):
            sdk.create_subject("an org", "organization", roleHolder="Daniel")
        assert _count(sdk, "Subject") == before

    def test_a_merged_variant_resolves_by_the_alias_rung_not_as_ambiguous(
            self, sdk_factory):
        """F8: recording "these are one entity" turned the read into "there are two"."""
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "alpha gamma", "subjectKind", "other")
        _create_raw(sdk, "Subject", "sub-b", "alpha gammas", "subjectKind", "other")
        row = sdk.list_dedup_candidates(candidate_type="entity")[0]
        sdk.approve_merge(row["candidate_id"], action="merge")
        got = resolve_entity_name(sdk._get_proj().g, "Subject", row["name"])
        assert got["resolution"] in ("exact", "alias"), got
        assert got["entity_id"] == row["existing_id"]
        assert got["requires_review"] is False

    def test_a_kind_mismatch_is_reported_on_the_verdict(self, sdk_factory):
        """F13: the queue refuses to call it auto-mergeable; the verdict must say so."""
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-1", "alpha gamma", "subjectKind", "role")
        _create_raw(sdk, "Subject", "sub-2", "alpha gammas", "subjectKind", "naturalPerson")
        row = sdk.list_dedup_candidates(candidate_type="entity")[0]
        assert row["kind_mismatch"] is True
        verdict = sdk.approve_merge(row["candidate_id"], action="merge")
        assert verdict["kind_mismatch"] is True

    def test_an_empty_label_set_refuses_rather_than_reporting_empty(self):
        """F10: the primitive must not answer [] without reading the graph."""
        class _Untouched:
            def query(self, *_a, **_kw):
                raise AssertionError("the scan must not answer without reading")
        with pytest.raises(ValueError, match="at least one label"):
            entity_candidates(_Untouched(), labels=())


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
        sdk.create_subject("the operator", "role", roleHolder=person["id"])
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


# ── the second review round (fresh-context reviewer, measured defects) ────


class TestSecondReviewRound:
    """Pins for the defects the SECOND review round measured.

    Every test here fails on the head that preceded it. They are grouped (rather
    than folded into the classes above) so the round that produced them stays
    legible in the history: a fix that is later reverted has to argue with the
    measurement, not with a prose claim.
    """

    def test_the_merge_recomputes_with_the_QUEUE_s_rungs(self, sdk_factory):
        """P0: the queue ranks by the semantic rung, the merge recomputed without it.

        `entity_pair_for` passed `include_semantic=False` while
        `list_dedup_candidates` used the default `True`. On a graph where the
        vectors rank a DIFFERENT survivor than the string rungs, the reviewer
        approved (C into B) and the verdict folded C into A — a merge applied to a
        pair the reviewer never saw, which is the failure the parity pin above
        (`test_the_queue_row_and_the_merge_apply_the_SAME_survivor`) exists to
        prevent and could not see, because it injects no encoder.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "qqq alpha", "subjectKind", "other",
                    ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "qqq zzzzzzzz", "subjectKind", "other",
                    ts="2026-01-02")
        _create_raw(sdk, "Subject", "sub-c", "qqq alpho", "subjectKind", "other",
                    ts="2026-01-03")

        # Only the vectors put sub-b next to sub-c; the strings put sub-a there.
        vectors = {"qqq alpha": [0.0, 1.0], "qqq alpho": [0.2, 0.98],
                   "qqq zzzzzzzz": [0.2, 0.98]}

        def _enc(texts):
            return ([vectors[t] for t in texts], True)

        g = sdk._get_proj().g
        queue = {r["candidate_id"]: r for r in
                 entity_candidates(g, encode_fn=_enc)}
        assert queue["sub-c"]["method"] == "semantic", queue["sub-c"]
        assert queue["sub-c"]["existing_id"] == "sub-b"
        pair = entity_pair_for(g, "sub-c", encode_fn=_enc)
        assert pair is not None
        assert pair["survivor_id"] == queue["sub-c"]["existing_id"], (
            "the merge recomputed a DIFFERENT survivor than the queue showed")

    def test_a_rejected_exact_pair_does_not_hide_the_next_candidate(self, sdk_factory):
        """P1: `settled` was populated before the reviewed-pair filter.

        A candidate rejected against one exact twin matched NO other survivor for
        the rest of the scan, so a genuine duplicate silently vanished from the
        queue — contradicting `_pair_reviewed`'s \"only the reviewed combination is
        suppressed\" and under-reporting exactly what this module exists to report.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "aaa one", "subjectKind", "other",
                    ts="2026-01-01")
        _create_raw(sdk, "Subject", "sub-b", "aaa wun", "subjectKind", "other",
                    ts="2026-01-02")
        _create_raw(sdk, "Subject", "sub-c", "aaa one", "subjectKind", "other",
                    ts="2026-01-03", dedup_reviewed="reject",
                    dedup_target_id="sub-a")
        rows = sdk.list_dedup_candidates(candidate_type="entity")
        got = {(r["candidate_id"], r["existing_id"]) for r in rows}
        assert ("sub-c", "sub-b") in got, (
            f"the real sub-b/sub-c duplicate is missing from the queue: {got}")
        assert ("sub-c", "sub-a") not in got, (
            "the reviewed combination must stay suppressed")

    def test_a_merge_does_not_rewrite_the_candidate_s_caller_key(self, sdk_factory):
        """P2: the survivor's `canonical_name` was fill-only; the candidate's was not.

        A caller-supplied spelling is an addressable key
        (`resolve_entity_id(name_props=("name","canonical_name"))` answers for it),
        so overwriting it with the fold deletes a key — and falsifies
        `find_live_holder`'s stated premise that the candidate keeps its own key.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Object", "obj-a", "Big Boss Inc", "objectKind", "organization",
                    ts="2026-01-01")
        _create_raw(sdk, "Object", "obj-c", "Big Boss Inc.", "objectKind", "organization",
                    ts="2026-01-02", canonical_name="Big Boss Inc")
        row = next(r for r in sdk.list_dedup_candidates(candidate_type="entity")
                   if r["candidate_id"] == "obj-c")
        assert row["existing_id"] == "obj-a"
        sdk.approve_merge("obj-c", action="merge")
        kept = sdk._get_proj().g.query(
            "MATCH (o:Object {id:'obj-c'}) RETURN o.canonical_name, o.resolved_from"
        ).result_set[0]
        assert kept[0] == "Big Boss Inc", (
            "the merge rewrote the caller's canonical_name with the fold")
        assert kept[1] == "obj-a"

    def test_a_string_aliases_property_is_not_split_into_characters(self, sdk_factory):
        """P2: `aliases` is writable as a bare string; iterating it lost the alias."""
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "alpha gamma", "subjectKind", "other",
                    ts="2026-01-01", aliases="legacy alias")
        _create_raw(sdk, "Subject", "sub-b", "alpha gammas", "subjectKind", "other",
                    ts="2026-01-02")
        sdk.approve_merge("sub-b", action="merge")
        aliases = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:'sub-a'}) RETURN s.aliases").result_set[0][0]
        assert "legacy alias" in aliases, (
            f"the stored alias was destroyed by per-character iteration: {aliases}")
        assert "alpha gammas" in aliases
        assert not any(len(a) == 1 for a in aliases if a != " ")

    def test_the_semantic_math_degrades_instead_of_escaping(self, sdk_factory):
        """P2: the matrix math sat OUTSIDE the degrade guard.

        A length-matching but non-2-D encoder result passed the row check and then
        raised `numpy.AxisError` — a `ValueError` subclass, so `entity_candidates`
        re-raised it verbatim and the whole queue refused where the docstring
        promises a logged degrade to the string rungs.
        """
        sdk = sdk_factory()
        _create_raw(sdk, "Subject", "sub-a", "fresh-context code reviewer",
                    "subjectKind", "role")
        _create_raw(sdk, "Subject", "sub-b", "the fresh-context reviewer role",
                    "subjectKind", "role")

        def _flat(_texts):
            return ([1.0, 1.0], True)  # right LENGTH, wrong rank

        rows = entity_candidates(sdk._get_proj().g, encode_fn=_flat)
        assert rows and rows[0]["method"] == "fuzzy", (
            "a broken similarity matrix must degrade to the string rungs")

    def test_an_unknown_label_refuses_before_reaching_cypher(self):
        """P2: the label reaches the Cypher label position unvalidated."""
        class _Untouched:
            def query(self, *_a, **_kw):
                raise AssertionError("the label must be validated before the query")

        for call in (lambda: find_live_holder(_Untouched(), "Injected", "x"),
                     lambda: follow_resolved_from(_Untouched(), "Injected", "x")):
            with pytest.raises(ValueError, match="covers"):
                call()

    def test_a_rename_refreshes_the_folded_key(self, sdk_factory):
        """P1: a rename left the stale fold behind, so the OLD spelling resolved
        onto the RENAMED entity — a wrong-entity write, with the pre-flight probe
        agreeing because it reads the same key."""
        sdk = sdk_factory()
        obj = sdk.create_object("owner", "concept")
        sdk.update_entity(obj["id"], name="Alice")
        g = sdk._get_proj().g
        folded = g.query("MATCH (o:Object {id:$id}) RETURN o.canonical_name",
                         params={"id": obj["id"]}).result_set[0][0]
        assert folded == normalize_entity_name("Alice"), (
            "the rename left the stale folded key on the node")
        # Creating the OLD spelling must mint a NEW entity, not write onto Alice.
        again = sdk.create_object("owner", "concept")
        assert again["id"] != obj["id"], (
            "a write of the pre-rename spelling resolved onto the renamed entity")

    def test_a_padded_role_kind_is_accepted_exactly_as_the_door_accepts_it(
            self, sdk_factory):
        """P2: the `roleHolder` gate compared the RAW kind while the door normalised it.

        The padded kind must arrive as a PROP, not through `create_subject`'s
        positional argument — the alias normalises that one itself
        (`subjectKind = named_kind(subjectKind)`), so only the prop path reaches
        the gate raw.
        """
        sdk = sdk_factory()
        sdk.create_subject("Daniel", "naturalPerson")
        role = sdk.create_subject("owner", roleHolder="Daniel",
                                  subjectKind=" role ")
        assert role["id"]
        assert sdk._get_proj().g.query(
            "MATCH (:Subject)-[r:holdsRole]->(:Subject) RETURN count(r)"
        ).result_set[0][0] == 1

    def test_both_role_holder_spellings_are_popped_off_the_node(self, sdk_factory):
        """P2: supplying both spellings left `role_holder` on the node."""
        sdk = sdk_factory()
        sdk.create_subject("Daniel", "naturalPerson")
        role = sdk.create_subject("owner", "role", roleHolder="Daniel",
                                  role_holder="Daniel")
        props = sdk._get_proj().g.query(
            "MATCH (s:Subject {id:$id}) RETURN properties(s)",
            params={"id": role["id"]}).result_set[0][0]
        assert "roleHolder" not in props and "role_holder" not in props, props

    def test_every_entity_writer_stores_the_FOLDED_canonical_name(self, sdk_factory):
        """P0/P1: a raw name in `canonical_name` is a second key space in one property.

        `EventAPI.add_object` only `setdefault`s a caller's value, so every writer
        is responsible for folding. Two did not (the docs miner, literally; the
        issue-Object and Subject-stub doors, by not writing the key at all).
        """
        from tortoise.docs_miner import normalize_entity_name as _fold_used_by_docs
        assert _fold_used_by_docs is normalize_entity_name, (
            "the docs miner must fold through the ONE resolver fold")

        sdk = sdk_factory()
        sdk._get_proj().g.query(
            "MERGE (s:Subject {name:$n}) "
            "ON CREATE SET s.id=$n, s.subjectKind='other', s.canonical_name=$f",
            params={"n": "The Owner", "f": normalize_entity_name("The Owner")})
        # The funnel fills the fold for a door that passes none.
        sdk._create_entity("Object", "obj-funnel",
                           {"name": "The Operator", "objectKind": "role",
                            "createdAt": "2026-01-01"}, "ObjectRegistered")
        rows = sdk._get_proj().g.query(
            "MATCH (o:Object {id:'obj-funnel'}) RETURN o.canonical_name"
        ).result_set[0]
        assert rows[0] == normalize_entity_name("The Operator")
